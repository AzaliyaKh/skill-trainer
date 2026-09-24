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
from src.benchmark.provider import OpenRouterProvider, CodexCLIProvider
from src.benchmark.evaluator import artifact_text
import subprocess
import sys
from src.benchmark.storage import clean

class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.case = load_case('dataset/example/dev/case-001/case.yaml')

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
        config['models'] = [{'provider': 'fake', 'model': 'fake'}]
        config['judge']['provider'] = 'judge'
        fake = FakeProvider(['Противоречия. Рекомендации.', '<svg xmlns="http://www.w3.org/2000/svg"/>'])
        judge = FakeProvider([RuntimeError('offline'), RuntimeError('offline')])
        result = benchmark(config, results_dir=self.root / 'raw', providers={'fake': fake, 'judge': judge})
        self.assertFalse(result['complete'])
        run = self.root / 'raw/case-001/fake/run-01'
        self.assertTrue((run / 'workspace/outputs/answer.md').exists())
        self.assertTrue((run / 'usage.json').exists())
        self.assertTrue((run / 'checks.json').exists())
        summary = aggregate_all(self.root / 'raw', config['aggregation']['gates'])
        self.assertFalse(summary['passed'])
        self.assertEqual(summary['cases']['case-001']['fake']['runs_count'], 1)
        self.assertIsNone(summary['cases']['case-001']['fake']['usage']['cost_usd_total'])

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
        self.assertEqual(len(load_all_cases('dataset/example/dev')), 2)
        self.assertEqual(len(load_all_cases('dataset/example/holdout')), 1)

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
        shutil.copytree('dataset/example/dev/case-001', root)
        data = yaml.safe_load((root / 'case.yaml').read_text())
        data['checks'] = [{'type': 'typo_check'}]
        (root / 'case.yaml').write_text(yaml.safe_dump(data))
        with self.assertRaisesRegex(ValueError, 'Unknown or invalid check'):
            load_case(str(root / 'case.yaml'))







class BoundaryTests(unittest.TestCase):
    def test_reference_and_symlink_input_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'case'
            shutil.copytree('dataset/example/dev/case-001', root)
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
    def test_codex_mounts_only_workspace_and_runtime(self, run, which):
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
            return Mock(returncode=0, stderr='', stdout=json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 5, 'output_tokens': 3}}))
        run.side_effect = execute
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / 'workspace'
            (workspace / 'inputs').mkdir(parents=True)
            response = CodexCLIProvider(auth_file=str(Path(temporary) / 'missing')).generate('fixture', '', '', 0, 100, str(workspace))
        self.assertEqual(response.input_tokens, 5)
        self.assertEqual(response.output, 'Готово')

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
        for name in ('src/benchmark', 'tests', 'skill', 'dataset', 'config', '.venv', 'runs', 'skill_versions', 'releases'):
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
        for name in ('src/benchmark', 'tests', 'skill', 'dataset', 'config'):
            self.assertTrue((self.root / name / 'keep.txt').exists())
        self.assertTrue((self.root / '.env').exists())
        for name in ('runs', 'skill_versions', 'releases', 'src/benchmark/__pycache__'):
            self.assertFalse((self.root / name).exists())
        self.make('clean')

    def test_distclean_removes_artifacts_and_environment(self):
        self.make('distclean')
        for name in ('runs', 'skill_versions', 'releases', '.venv'):
            self.assertFalse((self.root / name).exists())
        self.assertTrue((self.root / 'skill/keep.txt').exists())
        self.make('distclean')

    def test_invalid_cleanup_paths_fail_before_deletion(self):
        for target in ('.', '..', 'src', '.venv', 'dataset', '/'):
            with self.assertRaises(ValueError):
                clean(self.root, ['runs', target], '.venv')
            self.assertTrue((self.root / 'runs/keep.txt').exists())



if __name__ == '__main__':
    unittest.main()
