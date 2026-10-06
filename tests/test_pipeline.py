import json
import tempfile
import unittest
from pathlib import Path
from src.benchmark.case import load_all_cases, load_case
from src.benchmark.provider import FakeProvider
from src.benchmark.runner import run_case
from src.benchmark.checks import run_checks
from src.benchmark.evaluator import _parse_response
from src.benchmark.benchmark import benchmark
from src.benchmark.aggregate import aggregate_all
from src.benchmark.storage import load_config
import importlib.util
import shutil
from unittest.mock import patch, Mock
import requests
import yaml
from src.benchmark.provider import OpenRouterProvider, CodexCLIProvider, GenerationResult
from src.benchmark.evaluator import artifact_text
import subprocess
import sys
from src.benchmark.storage import clean

class PipelineTests(unittest.TestCase):
    def test_dataset_digest_ignores_only_office_locks(self):
        from src.benchmark.storage import digest_tree
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'requirements.md'
            source.write_text('original')
            baseline = digest_tree(root, ignore_office_locks=True)
            lock = root / '.~lock.reference.docx#'
            lock.write_text('temporary')
            self.assertEqual(baseline, digest_tree(root, ignore_office_locks=True))
            self.assertNotEqual(baseline, digest_tree(root))
            lock.unlink()
            self.assertEqual(baseline, digest_tree(root, ignore_office_locks=True))
            source.write_text('changed')
            self.assertNotEqual(baseline, digest_tree(root, ignore_office_locks=True))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        # Isolate text fixtures from the working document benchmark dataset.
        self.dataset = self.root / 'dataset'
        for split, names in [('dev', ['case-003', 'case-004', 'case-005']),
                             ('holdout', ['case-102'])]:
            for case_name in names:
                shutil.copytree(Path('dataset/example') / split / case_name,
                                self.dataset / split / case_name)

        self.case = load_case('dataset/example/dev/case-003/case.yaml')

    def test_language_and_reference_boundary(self):
        fake = FakeProvider(['Отчёт'])
        run_case(fake, 'fake', 0, 100, self.case, 'Instructions', 1, self.root / 'run')
        self.assertIn('OUTPUT LANGUAGE: ru', fake.calls[0]['system'])
        self.assertNotIn('REFERENCE', fake.calls[0]['prompt'])
        self.assertFalse((self.root / 'run/workspace/reference').exists())
        self.case.output_language = 'en'
        fake = FakeProvider(['Report'])
        run_case(fake, 'fake', 0, 100, self.case, 'Instructions', 1, self.root / 'english')
        self.assertIn('OUTPUT LANGUAGE: en', fake.calls[0]['system'])

    def test_agent_preserves_files(self):
        self.case.execution = {'mode': 'agent'}
        fake = FakeProvider(['Finished'], files={'answer.md': 'Actual document', 'table.json': '[1]'})
        result = run_case(fake, 'fake', 0, 100, self.case, '', 1, self.root / 'run')
        self.assertEqual((Path(result.outputs_dir) / 'answer.md').read_text(), 'Actual document')
        self.assertIn('table.json', result.created_files)
        self.assertEqual(result.output, 'Actual document')

    def test_judge_failure_preserves_generation_and_checks(self):
        config = load_config('config/benchmark.yaml')
        config['dataset']['path'] = str(self.dataset / 'dev')
        config['models'] = [{'provider': 'fake', 'model': 'fake'}]
        config['judge']['provider'] = 'judge'
        fake = FakeProvider([
            '# Критерии приёмки\nPOST /shorten GET /{code} DELETE /{code} 400 404',
            '# Критерии приёмки\nPOST /tasks GET /tasks PATCH /tasks/{id} DELETE /tasks/{id} 400 404',
            '# Критерии приёмки\nPOST /files GET /files/{id} DELETE /files/{id} 5 МБ 400 404',
        ])
        cases = load_all_cases(config['dataset']['path'])
        evaluations = [json.dumps({'criteria': [
            {'name': criterion.name, 'score': criterion.points, 'reasoning': 'Fixture'}
            for criterion in case.rubric.criteria]}) for case in cases]
        judge = FakeProvider([RuntimeError('offline'), *evaluations[1:]])
        result = benchmark(config, results_dir=self.root / 'raw', providers={'fake': fake, 'judge': judge})
        self.assertFalse(result['complete'])
        run = self.root / 'raw/case-003/fake/run-01'
        self.assertTrue((run / 'workspace/outputs/answer.md').exists())
        self.assertTrue((run / 'usage.json').exists())
        self.assertTrue((run / 'checks.json').exists())
        self.assertFalse((run / 'workspace/inputs').exists())
        self.assertFalse((run / 'workspace/skill').exists())
        summary = aggregate_all(self.root / 'raw', config['aggregation']['gates'])
        self.assertFalse(summary['passed'])
        self.assertEqual(summary['cases']['case-003']['fake']['runs_count'], 1)
        self.assertIsNone(summary['cases']['case-003']['fake']['usage']['cost_usd_total'])

        generation = FakeProvider([])
        resumed = benchmark(config, results_dir=self.root / 'raw', reuse=True,
                            providers={'fake': generation, 'judge': FakeProvider([evaluations[0]])})
        self.assertTrue(resumed['complete'])
        self.assertEqual(generation.calls, [])
        self.assertFalse((run / 'error.json').exists())
        self.assertEqual(json.loads((run / 'status.json').read_text())['status'], 'complete')

    def test_generation_failure_resumes_only_failed_run(self):
        config = load_config('config/benchmark.yaml')
        config['dataset']['path'] = str(self.dataset / 'dev')
        config['models'] = [{'provider': 'fake', 'model': 'fake'}]
        config['judge']['provider'] = 'judge'
        cases = load_all_cases(config['dataset']['path'])
        evaluations = [json.dumps({'criteria': [
            {'name': criterion.name, 'score': criterion.points, 'reasoning': 'Fixture'}
            for criterion in case.rubric.criteria]}) for case in cases]
        root = self.root / 'raw'
        result = benchmark(config, results_dir=root, providers={
            'fake': FakeProvider([RuntimeError('403 Forbidden'), *['Ответ'] * (len(cases) - 1)]),
            'judge': FakeProvider(evaluations[1:]),
        })
        self.assertFalse(result['complete'])
        run = root / cases[0].id / 'fake/run-01'
        (run / 'workspace/outputs/partial.txt').write_text('Partial attempt')
        completed = root / cases[1].id / 'fake/run-01'
        saved = {path.relative_to(completed): path.read_bytes()
                 for path in completed.rglob('*') if path.is_file()}

        failed = benchmark(config, results_dir=root, reuse=True, providers={
            'fake': FakeProvider([RuntimeError('still offline')]), 'judge': FakeProvider([]),
        })
        self.assertFalse(failed['complete'])
        self.assertEqual(len(failed['failures']), 1)
        archived = self.root / 'raw-failed-attempts' / cases[0].id / 'fake/run-01'
        self.assertTrue((archived / 'attempt-01/workspace/outputs/partial.txt').exists())

        generation = FakeProvider(['Восстановленный ответ'])
        judge = FakeProvider([evaluations[0]])
        resumed = benchmark(config, results_dir=root, reuse=True,
                            providers={'fake': generation, 'judge': judge})
        self.assertTrue(resumed['complete'])
        self.assertEqual(resumed['failures'], [])
        self.assertEqual(len(generation.calls), 1)
        self.assertEqual(len(judge.calls), 1)
        self.assertFalse((run / 'workspace/outputs/partial.txt').exists())
        self.assertFalse((run / 'error.json').exists())
        self.assertTrue((archived / 'attempt-02/error.json').exists())
        self.assertEqual(saved, {path.relative_to(completed): path.read_bytes()
                                 for path in completed.rglob('*') if path.is_file()})
        summary = aggregate_all(root, config['aggregation']['gates'])
        self.assertEqual(summary['cases'][cases[0].id]['fake']['runs_count'], 1)

    def test_interrupted_benchmark_resumes_without_benchmark_report(self):
        config = load_config('config/benchmark.yaml')
        config['dataset']['path'] = str(self.dataset / 'dev')
        config['models'] = [{'provider': 'fake', 'model': 'fake'}]
        config['judge']['provider'] = 'judge'
        root = self.root / 'interrupted-raw'
        class InterruptProvider:
            supports_agent = False

            def generate(self, **kwargs):
                raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            benchmark(config, results_dir=root, providers={
                'fake': InterruptProvider(), 'judge': FakeProvider([]),
            })
        self.assertTrue((root / 'manifest.json').is_file())
        self.assertFalse((root / 'benchmark.json').exists())

        cases = load_all_cases(config['dataset']['path'])
        answers = [
            '# Критерии приёмки\nPOST /shorten GET /{code} DELETE /{code} 400 404',
            '# Критерии приёмки\nPOST /tasks GET /tasks PATCH /tasks/{id} DELETE /tasks/{id} 400 404',
            '# Критерии приёмки\nPOST /files GET /files/{id} DELETE /files/{id} 5 МБ 400 404',
        ]
        evaluations = [json.dumps({'criteria': [
            {'name': criterion.name, 'score': criterion.points, 'reasoning': 'Fixture'}
            for criterion in case.rubric.criteria]}) for case in cases]
        resumed = benchmark(config, results_dir=root, reuse=True, providers={
            'fake': FakeProvider(answers), 'judge': FakeProvider(evaluations),
        })
        self.assertTrue(resumed['complete'])
        self.assertTrue((root / 'benchmark.json').is_file())
        self.assertTrue((self.root / 'interrupted-raw-failed-attempts/case-003/fake/run-01/attempt-01').is_dir())

    def test_invalid_judge_scores(self):
        entries = [{'name': c.name, 'score': c.points, 'reasoning': 'OK'} for c in self.case.rubric.criteria]
        for bad in (entries[:-1], entries + [entries[0]], [dict(entries[0], score=999)] + entries[1:]):
            with self.assertRaises(ValueError):
                _parse_response(json.dumps({'criteria': bad}), self.case.rubric)

    def test_file_checks_and_unknown_check(self):
        (self.root / 'data.json').write_text('{"ok": true}')
        checks = run_checks('', [{'type': 'json_valid', 'path': 'data.json'}, {'type': 'unknown'}], self.root)
        self.assertEqual((checks['passed'], checks['failed']), (1, 1))
        checks = run_checks('', [], self.root, [{'path': '../escape', 'required': True}])
        self.assertEqual(checks['failed'], 1)

    def test_dataset_loading(self):
        self.assertEqual(len(load_all_cases(str(self.dataset / 'dev'))), 3)
        self.assertEqual(len(load_all_cases(str(self.dataset / 'holdout'))), 1)

    def test_folder_inputs_preserve_nested_paths(self):
        self.case.inputs = ['inputs']
        fake = FakeProvider(['Report'])
        result = run_case(fake, 'fake', 0, 100, self.case, '', 1, self.root / 'folder')
        self.assertTrue((Path(result.workspace_dir) / 'inputs/requirements.md').exists())
        self.assertFalse((Path(result.workspace_dir) / 'reference').exists())

    def test_confidence_level_changes_interval(self):
        from src.benchmark.aggregate import _stats
        narrow = _stats([70, 80, 90], 0.8)
        wide = _stats([70, 80, 90], 0.99)
        self.assertLess(narrow['ci_high'], wide['ci_high'])
        self.assertEqual(narrow['ci95_high'], wide['ci95_high'])

    def test_unknown_check_is_rejected_before_execution(self):
        import shutil
        import yaml
        root = self.root / 'case'
        shutil.copytree('dataset/example/dev/case-003', root)
        data = yaml.safe_load((root / 'case.yaml').read_text())
        data['checks'] = [{'type': 'typo_check'}]
        (root / 'case.yaml').write_text(yaml.safe_dump(data))
        with self.assertRaisesRegex(ValueError, 'Unknown or invalid check'):
            load_case(str(root / 'case.yaml'))







class BoundaryTests(unittest.TestCase):
    def test_reference_and_symlink_input_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'case'
            shutil.copytree('dataset/example/dev/case-003', root)
            data = yaml.safe_load((root / 'case.yaml').read_text())
            data['inputs'] = ['reference/answer.md']
            (root / 'case.yaml').write_text(yaml.safe_dump(data))
            with self.assertRaises(ValueError):
                load_case(str(root / 'case.yaml'))
            data['inputs'] = ['inputs/link.md']
            (root / 'inputs/link.md').symlink_to(root / 'reference/answer.md')
            (root / 'case.yaml').write_text(yaml.safe_dump(data))
            with self.assertRaises(ValueError):
                load_case(str(root / 'case.yaml'))

    @patch('src.benchmark.provider.time.sleep')
    @patch('src.benchmark.provider.requests.post')
    def test_http_retry_and_null_usage(self, post, sleep):
        retry = Mock(status_code=503)
        retry.json.side_effect = ValueError('HTML response')
        success = Mock(status_code=200)
        success.json.return_value = {'choices': [{'message': {'content': 'OK'}}]}
        post.side_effect = [requests.Timeout('timeout'), retry, success]
        response = OpenRouterProvider('fake-key', max_retries=3).generate('fixture', '', '', 0, 100)
        self.assertEqual(post.call_count, 3)
        self.assertIsNone(response.cost)
        self.assertIsNone(response.input_tokens)

    @patch('src.benchmark.provider.requests.post')
    def test_http_non_json_error_diagnostic(self, post):
        post.return_value = Mock(status_code=403, text='Forbidden')
        post.return_value.json.side_effect = ValueError()
        with self.assertRaisesRegex(RuntimeError, '403.*Forbidden'):
            OpenRouterProvider('fake-key').generate('fixture', '', '', 0, 100)

    @patch('src.benchmark.provider.shutil.which', side_effect=lambda name: '/usr/bin/' + name)
    @patch('src.benchmark.provider.subprocess.run')
    def test_codex_passes_each_selected_model_to_cli(self, run, which):
        selected_models = []
        def execute(command, **kwargs):
            home_index = command.index('/home/agent')
            home = Path(command[home_index - 1])
            (home / 'response.txt').write_text('Готово')
            self.assertIn('--clearenv', command)
            self.assertNotIn(str(Path.cwd()), command)
            self.assertIn('--ro-bind', command)
            self.assertIn('--sandbox', command)
            self.assertIn('--json', command)
            self.assertNotIn('shell', kwargs)
            model_index = command.index('-m')
            selected_models.append(command[model_index + 1])
            return Mock(returncode=0, stderr='', stdout=json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 5, 'output_tokens': 3}}))
        run.side_effect = execute
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / 'workspace'
            (workspace / 'inputs').mkdir(parents=True)
            provider = CodexCLIProvider(auth_file=str(Path(temporary) / 'missing'))
            for model in ('gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna'):
                response = provider.generate(model, '', '', 0, 100, str(workspace))
                self.assertEqual(response.input_tokens, 5)
                self.assertEqual(response.output, 'Готово')
        self.assertEqual(selected_models, ['gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna'])

    def test_configured_codex_models_coexist_with_fixed_luna_judge(self):
        config = load_config('config/benchmark.yaml')
        self.assertEqual(config['models'], [
            {'name': 'gpt-5-6-sol', 'provider': 'codex', 'model': 'gpt-5.6-sol'},
            {'name': 'gpt-5-6-terra', 'provider': 'codex', 'model': 'gpt-5.6-terra'},
            {'name': 'gpt-5-6-luna', 'provider': 'codex', 'model': 'gpt-5.6-luna'},
        ])
        self.assertEqual(config['judge']['provider'], 'codex')
        self.assertEqual(config['judge']['model'], 'gpt-5.6-luna')
        self.assertEqual(config['optimization']['provider'], 'codex')
        self.assertEqual(config['optimization']['model'], 'gpt-5.6-sol')

        case = load_case('dataset/example/dev/case-003/case.yaml')
        answer = '# Критерии приёмки\nPOST /shorten GET /{code} DELETE /{code} 400 404'

        class RecordingCodex:
            def __init__(self):
                self.calls = []

            def generate(self, model, system, prompt, temperature, max_tokens, workspace=None):
                judge = 'objective evaluator' in system
                self.calls.append({'model': model, 'judge': judge})
                if judge:
                    output = json.dumps({'criteria': [
                        {'name': criterion.name, 'score': criterion.points, 'reasoning': 'Fixture'}
                        for criterion in case.rubric.criteria]})
                else:
                    output = answer
                return GenerationResult(output, model, None, None, None, 0.0)

        with tempfile.TemporaryDirectory() as temporary:
            dataset = Path(temporary) / 'dataset'
            shutil.copytree(Path(case.file_path).parent, dataset / 'case-003')
            provider = RecordingCodex()
            result = benchmark(config, dataset_dir=dataset, results_dir=Path(temporary) / 'runs',
                               providers={'codex': provider})
        self.assertTrue(result['complete'])
        generation_models = [call['model'] for call in provider.calls if not call['judge']]
        judge_models = [call['model'] for call in provider.calls if call['judge']]
        self.assertEqual(generation_models, ['gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna'])
        self.assertEqual(judge_models, ['gpt-5.6-luna'] * 3)
        self.assertEqual([call['judge'] for call in provider.calls],
                         [False, False, False, True, True, True])

    def test_docx_extraction_without_optional_dependencies(self):
        import zipfile
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with zipfile.ZipFile(root / 'report.docx', 'w') as archive:
                archive.writestr('[Content_Types].xml', '<Types/>')
                archive.writestr('word/document.xml', '<document><p>Требование</p></document>')
            result = run_checks('', [{'type': 'docx_valid', 'path': 'report.docx'}], root)
            self.assertEqual(result['failed'], 0)
            self.assertIn('Требование', artifact_text(root / 'report.docx'))
            content_checks = run_checks('Summary without document text', [
                {'type': 'contains', 'path': 'report.docx', 'text': 'Требование'},
                {'type': 'contains', 'path': 'report.docx', 'text': 'Missing requirement'},
            ], root)
            self.assertEqual(content_checks['passed'], 1)
            self.assertEqual(content_checks['failed'], 1)

    @unittest.skipUnless(importlib.util.find_spec("pypdf"), "pypdf is not installed")
    def test_document_checks_and_actual_content_extraction(self):
        from pypdf import PdfWriter
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            writer.write(root / 'report.pdf')
            result = run_checks('', [{'type': 'pdf_valid', 'path': 'report.pdf'}], root)
            self.assertEqual(result['failed'], 0)
            with self.assertRaisesRegex(ValueError, 'no extractable text'):
                artifact_text(root / 'report.pdf')
            (root / 'image.png').write_bytes(b'fake')
            with self.assertRaisesRegex(ValueError, 'No semantic artifact reader'):
                artifact_text(root / 'image.png')

    @unittest.skipUnless(importlib.util.find_spec("jsonschema"), "jsonschema is not installed")
    def test_json_schema(self):
        result = run_checks('{"count": "wrong"}', [{"type": "json_schema", "schema": {
            "type": "object", "properties": {"count": {"type": "integer"}}, "required": ["count"]}}])
        self.assertEqual(result["failed"], 1)
        result = run_checks('{"count": 2}', [{"type": "json_schema", "schema": {
            "type": "object", "properties": {"count": {"type": "integer"}}, "required": ["count"]}}])
        self.assertEqual(result["failed"], 0)





    @patch('src.benchmark.provider.time.sleep')
    @patch('src.benchmark.provider.requests.post')
    def test_rate_limit_retry_after_and_string_error_code(self, post, sleep):
        limited = Mock(status_code=200, headers={'Retry-After': '12'})
        limited.json.return_value = {'error': {'code': '429', 'message': 'rate limited'}}
        success = Mock(status_code=200)
        success.json.return_value = {'choices': [{'message': {'content': 'OK'}}]}
        post.side_effect = [limited, success]
        result = OpenRouterProvider('fake-key').generate('fixture', '', '', 0, 100)
        self.assertEqual(result.output, 'OK')
        sleep.assert_called_once_with(12)

    @patch('src.benchmark.provider.time.sleep')
    @patch('src.benchmark.provider.requests.post')
    def test_exhausted_rate_limit_remains_a_failure(self, post, sleep):
        limited = Mock(status_code=429, headers={})
        limited.json.return_value = {'error': {'code': 429, 'message': 'upstream rate limited'}}
        post.return_value = limited
        with self.assertRaisesRegex(RuntimeError, '429'):
            OpenRouterProvider('fake-key', max_retries=2).generate('fixture', '', '', 0, 100)
        self.assertEqual(post.call_count, 2)



class CleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = Path(__file__).resolve().parents[1]
        for name in ('src/benchmark', 'tests', 'skill', 'skill/requirements-analysis', 'skill/versions/requirements-analysis/v0', 'skill/versions/other/v0', 'dataset', 'config', '.venv', 'runs', 'runs_bad', 'skill_versions', 'releases'):
            path = self.root / name
            path.mkdir(parents=True, exist_ok=True)
            (path / 'keep.txt').write_text(name)
        shutil.copy2(self.project / 'src/benchmark/storage.py', self.root / 'src/benchmark/storage.py')
        (self.root / 'src/benchmark/__pycache__').mkdir()
        (self.root / 'src/benchmark/__pycache__/old.pyc').write_bytes(b'cache')
        (self.root / '.env').write_text('KEEP=1')

    def make(self, target):
        subprocess.run(['make', '-f', str(self.project / 'Makefile'), target, f'SYSTEM_PYTHON={sys.executable}'],
                       cwd=self.root, check=True, capture_output=True, text=True)

    def test_clean_keeps_environment_and_sources(self):
        self.make('clean')
        self.assertTrue((self.root / '.venv/keep.txt').exists())
        for name in ('src/benchmark', 'tests', 'skill', 'skill/requirements-analysis', 'skill/versions/other/v0', 'dataset', 'config'):
            self.assertTrue((self.root / name / 'keep.txt').exists())
        self.assertTrue((self.root / '.env').exists())
        for name in ('runs', 'runs_bad', 'skill/versions/requirements-analysis', 'src/benchmark/__pycache__'):
            self.assertFalse((self.root / name).exists())
        for name in ('skill_versions', 'releases'):
            self.assertTrue((self.root / name / 'keep.txt').exists())
        self.make('clean')

    def test_distclean_removes_artifacts_and_environment(self):
        self.make('distclean')
        for name in ('runs', 'runs_bad', 'skill/versions/requirements-analysis', '.venv'):
            self.assertFalse((self.root / name).exists())
        self.assertTrue((self.root / 'skill/keep.txt').exists())
        for name in ('skill_versions', 'releases'):
            self.assertTrue((self.root / name / 'keep.txt').exists())
        self.make('distclean')

    def test_invalid_cleanup_paths_fail_before_deletion(self):
        for target in ('.', '..', 'src', '.venv', 'dataset', 'skill', 'skill/versions', 'skill/requirements-analysis', 'skill/versions/other', '/'):
            with self.assertRaises(ValueError):
                clean(self.root, ['runs', target], '.venv')
            self.assertTrue((self.root / 'runs/keep.txt').exists())



if __name__ == '__main__':
    unittest.main()
