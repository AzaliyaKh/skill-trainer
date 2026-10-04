import json
import io
import shutil
import subprocess
import yaml
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from pathlib import Path

from src.benchmark.aggregate import aggregate_all
from src.benchmark.benchmark import benchmark, evaluate_dataset, run_holdout
from src.benchmark.case import load_all_cases
from src.benchmark.error_analysis import analyze
from src.benchmark.optimizer import optimize, apply_optimization
from src.benchmark.provider import FakeProvider
from src.benchmark.regression import run_regression, compare, print_verdict
from src.benchmark.release import release
from src.benchmark.review import (approve_optimization_feedback, export_review,
                                  export_optimization_review, open_in_vscode_simple_browser)
from src.benchmark.storage import load_config, write_json, read_json, lifecycle_paths


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        # Isolate text fixtures from the working document benchmark dataset.
        self.dataset = self.root / 'dataset'
        for split, names in [('dev', ['case-003', 'case-004', 'case-005']),
                             ('holdout', ['case-102'])]:
            for case_name in names:
                shutil.copytree(Path('dataset/example') / split / case_name,
                                self.dataset / split / case_name)

        self.config = load_config('config/benchmark.yaml')
        self.config['dataset'] = {'path': str(self.dataset / 'dev'), 'holdout_path': str(self.dataset / 'holdout')}
        source = Path(self.config['skill']['path']).parent
        target = self.root / 'skill' / source.name
        shutil.copytree(source, target)
        self.config['skill']['path'] = str(target / 'SKILL.md')
        self.config['results_dir'] = str(self.root / 'dev')
        self.config['models'] = [{'provider': 'fake', 'model': 'fixture'}]
        self.config['judge']['provider'] = 'judge'
        for field in ('runs_dir', 'versions_dir', 'releases_dir'):
            self.config['lifecycle'][field] = str(self.root / field)
        self.config['lifecycle']['previous_version'] = 'v0'
        self.config['lifecycle']['candidate_version'] = 'v1'
        self.specification = ('# Назначение\nURL Shortener.\n# API\nPOST /shorten, GET /{code}, DELETE /{code}.\n'
                              '# Валидация и ошибки\nHTTP 400 и HTTP 404. Только http/https, код из 8 символов.\n'
                              '# Критерии приёмки\nEndpoint-ы выполняют заявленное поведение.')
        self.task_specification = ('# Назначение\nTask API.\n# API\nPOST /tasks, GET /tasks, '
                                   'PATCH /tasks/{id}, DELETE /tasks/{id}.\n# Валидация и ошибки\n'
                                   'Статусы todo, in_progress, done. HTTP 400 и HTTP 404.\n'
                                   '# Критерии приёмки\nОперации выполняют заявленное поведение.')
        self.file_specification = ('# Назначение\nFile API.\n# API\nPOST /files, GET /files/{id}, '
                                   'DELETE /files/{id}.\n# Валидация и ошибки\n5 МБ, PNG/JPEG, HTTP 400 и HTTP 404.\n'
                                   '# Критерии приёмки\nОперации выполняют заявленное поведение.')
        self.notification_specification = ('# Назначение\nNotification API.\n# API\nPOST /notifications, '
                                           'GET /notifications, PATCH /notifications/{id}/read.\n'
                                           '# Валидация и ошибки\n500 символов, HTTP 400 и HTTP 404.\n'
                                           '# Критерии приёмки\nОперации выполняют заявленное поведение.')

    def providers(self, split='dev', repeat=1):
        cases = load_all_cases(str(self.dataset / split))
        judge = [json.dumps({'criteria': [{'name': c.name, 'score': c.points, 'reasoning': 'Fixture score'}
                                         for c in case.rubric.criteria]}) for case in cases] * repeat
        outputs = ([self.specification, self.task_specification, self.file_specification]
                   if split == 'dev' else [self.notification_specification]) * repeat
        return {'fake': FakeProvider(outputs), 'judge': FakeProvider(judge)}

    def prepare_candidate(self):
        benchmark(self.config, providers=self.providers())
        root = Path(self.config['results_dir'])
        summary = aggregate_all(root, self.config['aggregation']['gates'])
        self.assertTrue(summary['passed'])
        write_json(root / 'summary.json', summary)
        write_json(root / 'error_analysis.json', analyze(summary, root, self.config['error_analysis']))
        original = Path(self.config['skill']['path']).read_text()
        proposal = self.root / 'proposal.json'
        write_json(proposal, {'skill_md': original + '\nBefore finalizing, verify that no proposed clarification is presented as a confirmed requirement.\n',
                              'changes': [{'problem': 'Ambiguous requirements', 'change': 'Separate proposals', 'reason': 'Avoid invented requirements'}]})
        self.config['optimization']['proposal_path'] = str(proposal)
        optimize(self.config)
        return lifecycle_paths(self.config)

    def test_full_offline_lifecycle_and_release_guards(self):
        paths = self.prepare_candidate()
        self.assertFalse(paths['new_skill'].exists())
        self.assertTrue(paths['proposed_skill'].exists())
        self.approve_optimization(paths)
        apply_optimization(self.config)
        evaluate_dataset(self.config, paths['new_skill'], Path(self.config['dataset']['path']),
                         paths['regression'] / paths['candidate'], 'dev', self.providers())
        regression = run_regression(self.config)
        self.assertTrue(regression['passed'])
        self.assertEqual(regression['total_score_delta'], 0)
        self.assertTrue(run_holdout(self.config, self.providers('holdout'))['passed'])
        feedback_path = export_review(self.config)
        review_html = (paths['review'] / 'index.html').read_text()
        self.assertIn(self.specification, review_html)
        self.assertIn('provider: fake', review_html)
        self.assertIn('Результаты моделей', review_html)
        self.assertIn('Fixture score', review_html)
        with self.assertRaisesRegex(ValueError, 'expert review'):
            release(self.config)
        feedback = read_json(feedback_path)
        feedback['status'] = 'complete'
        for row in feedback['reviews']:
            row.update(approved=True, feedback='Synthetic test approval')
        write_json(feedback_path, feedback)
        export_review(self.config)
        self.assertEqual(read_json(feedback_path), feedback)
        candidate = paths['new_skill'] / 'SKILL.md'
        original = candidate.read_text()
        candidate.write_text(original + '\nChanged after evaluation\n')
        with self.assertRaisesRegex(ValueError, 'exact candidate'):
            release(self.config)
        candidate.write_text(original)
        output = paths['holdout'] / 'raw/case-102/fixture/run-01/workspace/outputs/answer.md'
        original_output = output.read_text()
        output.write_text('[]')
        with self.assertRaisesRegex(ValueError, 'evidence has changed'):
            release(self.config)
        output.write_text(original_output)
        result = release(self.config)
        self.assertTrue(result['expert_review_passed'])
        self.assertTrue((paths['release'] / 'SKILL.md').exists())
        with self.assertRaises(FileExistsError):
            release(self.config)

    def test_review_shows_every_model_for_each_case(self):
        paths = self.prepare_candidate()
        self.approve_optimization(paths)
        apply_optimization(self.config)
        self.config['models'] = [
            {'provider': 'provider-a', 'model': 'vendor/model-a'},
            {'provider': 'provider-b', 'model': 'vendor/model-b'},
        ]
        case_count = len(load_all_cases(self.config['dataset']['path']))
        outputs = [self.specification, self.task_specification, self.file_specification] * 2
        judge_responses = []
        for _ in range(4):
            for case in load_all_cases(self.config['dataset']['path']):
                judge_responses.append(json.dumps({'criteria': [
                    {'name': criterion.name, 'score': criterion.points, 'reasoning': 'Compared result'}
                    for criterion in case.rubric.criteria]}))
        providers = {
            'provider-a': FakeProvider(outputs[:case_count]),
            'provider-b': FakeProvider(outputs[:case_count]),
            'judge': FakeProvider(judge_responses),
        }
        # Each version gets fresh generation providers while the judge keeps its queue.
        regression_root = paths['regression']
        providers['provider-a'] = FakeProvider(outputs[:case_count])
        providers['provider-b'] = FakeProvider(outputs[:case_count])
        candidate = evaluate_dataset(self.config, paths['new_skill'], Path(self.config['dataset']['path']),
                                     regression_root / paths['candidate'], 'dev', providers)
        previous = read_json(Path(self.config['results_dir']) / 'summary.json')
        result = compare(previous, candidate, self.config['regression'])
        result.update({'skill_name': paths['name'], 'previous_version': paths['previous'],
                       'candidate_version': paths['candidate'], 'previous_digest': '',
                       'candidate_digest': '', 'evidence_digest': ''})
        write_json(regression_root / 'regression.json', result)

        holdout_case = load_all_cases(self.config['dataset']['holdout_path'])[0]
        holdout_output = self.notification_specification
        holdout_judge = json.dumps({'criteria': [
            {'name': criterion.name, 'score': criterion.points, 'reasoning': 'Holdout result'}
            for criterion in holdout_case.rubric.criteria]})
        holdout_providers = {'provider-a': FakeProvider([holdout_output]),
                             'provider-b': FakeProvider([holdout_output]),
                             'judge': FakeProvider([holdout_judge, holdout_judge])}
        run_holdout(self.config, holdout_providers)
        export_review(self.config)
        review_html = (paths['review'] / 'index.html').read_text()
        self.assertIn('vendor/model-a', review_html)
        self.assertIn('vendor/model-b', review_html)
        self.assertIn('provider: provider-a', review_html)
        self.assertIn('provider: provider-b', review_html)
        self.assertEqual(review_html.count(self.specification), 2)
        markdown = (paths['review'] / 'result.md').read_text()
        for model in ('vendor/model-a', 'vendor/model-b'):
            self.assertIn(model, markdown)
        for metric in ('total_score', 'input_tokens', 'output_tokens', 'latency_seconds', 'cost_usd'):
            self.assertIn(metric, markdown)
        self.assertIn(self.specification, markdown)
        self.assertIn('Holdout result', markdown)
        self.assertIn('Версия Skill: v1', markdown)
        self.assertIn('Judge (config):', markdown)

    def test_holdout_uses_explicit_version_without_changing_config(self):
        paths = lifecycle_paths(self.config)
        selected = Path(self.config['lifecycle']['versions_dir']) / paths['name'] / 'v4'
        shutil.copytree(paths['source'], selected)
        (selected / 'SKILL.md').write_text((selected / 'SKILL.md').read_text() + '\nSelected version marker\n')
        providers = self.providers('holdout')
        result = run_holdout(self.config, providers, version='v4')
        self.assertEqual(result['version'], 'v4')
        self.assertEqual(self.config['lifecycle']['candidate_version'], 'v1')
        self.assertIn('Selected version marker', providers['fake'].calls[0]['system'])
        root = Path(self.config['lifecycle']['runs_dir']) / 'holdout' / paths['name'] / 'v4'
        self.assertTrue((root / 'holdout.json').is_file())
        with self.assertRaises(FileNotFoundError):
            run_holdout(self.config, version='v99')

    @patch('src.benchmark.review.subprocess.run')
    @patch('src.benchmark.review.shutil.which', return_value='/usr/bin/code')
    def test_review_page_opens_through_vscode(self, which, run):
        page = self.root / 'review/index.html'
        page.parent.mkdir(parents=True)
        page.write_text('<html></html>')
        self.assertTrue(open_in_vscode_simple_browser(page))
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ['/usr/bin/code', '--reuse-window', '--open-url'])
        self.assertEqual(command[3], page.resolve().as_uri())

    def test_optimizer_rejects_holdout_evidence(self):
        paths = self.prepare_candidate()
        self.config['lifecycle']['candidate_version'] = 'v2'
        root = Path(self.config['results_dir'])
        for name in ('summary.json', 'error_analysis.json'):
            data = read_json(root / name)
            data['provenance']['split'] = 'holdout'
            write_json(root / name, data)
        with self.assertRaisesRegex(ValueError, 'DEV evidence'):
            optimize(self.config)

    def test_missing_run_fails_gate(self):
        benchmark(self.config, providers=self.providers())
        root = Path(self.config['results_dir'])
        (root / 'case-004/fixture/run-01/evaluation.json').unlink()
        summary = aggregate_all(root, self.config['aggregation']['gates'])
        self.assertFalse(summary['passed'])
        self.assertEqual(len(summary['missing_runs']), 1)

    def test_regression_detects_criterion_drop_without_total_drop(self):
        import copy
        benchmark(self.config, providers=self.providers())
        previous = aggregate_all(Path(self.config['results_dir']), self.config['aggregation']['gates'])
        candidate = copy.deepcopy(previous)
        candidate['cases']['case-004']['fixture']['criteria']['requirements_fidelity']['mean'] -= 10
        result = compare(previous, candidate, self.config['regression'])
        self.assertEqual(result['total_score_delta'], 0)
        self.assertFalse(result['passed'])
        self.assertTrue(result['critical_regressions'])

    def test_failed_regression_prints_score_declines(self):
        import copy
        benchmark(self.config, providers=self.providers())
        summary = aggregate_all(Path(self.config['results_dir']), self.config['aggregation']['gates'])
        candidate = copy.deepcopy(summary)
        candidate['cases']['case-003']['fixture']['score']['mean'] -= 3
        paths = lifecycle_paths(self.config)
        shutil.copytree(paths['source'], paths['old_skill'])
        shutil.copytree(paths['source'], paths['new_skill'])
        write_json(paths['regression'] / 'v0/summary.json', summary)
        write_json(paths['regression'] / 'v1/summary.json', candidate)
        result = compare(summary, candidate, self.config['regression'])
        output = io.StringIO()
        with redirect_stdout(output):
            print_verdict(self.config, result)
        self.assertIn('кандидат не принят как улучшение', output.getvalue())
        self.assertIn('1. case-003 / Fixture:', output.getvalue())

    def test_make_next_version_updates_only_lifecycle_pair(self):
        paths = self.prepare_candidate()
        self.approve_optimization(paths)
        apply_optimization(self.config)
        write_json(paths['regression'] / 'regression.json', {
            'previous_version': 'v0', 'candidate_version': 'v1', 'passed': False,
        })
        config_path = self.root / 'next-version.yaml'
        config_path.write_text(yaml.safe_dump(self.config), encoding='utf-8')
        before = load_config(str(config_path))
        process = subprocess.run(['make', 'next-version', f'CONFIG={config_path}'],
                                 capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        after = load_config(str(config_path))
        self.assertEqual(after['lifecycle']['previous_version'], 'v1')
        self.assertEqual(after['lifecycle']['candidate_version'], 'v2')
        before['lifecycle'].update(previous_version='v1', candidate_version='v2')
        self.assertEqual(after, before)

    def approve_optimization(self, paths):
        feedback_path = paths['optimization'] / 'feedback.json'
        feedback = read_json(feedback_path)
        feedback['status'] = 'complete'
        for review in feedback['reviews']:
            review.update(approved=True, feedback='Approved fixture')
        write_json(feedback_path, feedback)
        return feedback

    def test_optimization_review_applies_to_working_skill(self):
        source = Path(self.config['skill']['path'])
        original = source.read_bytes()
        resource = source.parent / 'references' / 'guide.md'
        resource.parent.mkdir()
        resource.write_text('Preserve this resource')
        paths = self.prepare_candidate()
        self.assertEqual(source.read_bytes(), original)
        html = (paths['optimization'] / 'index.html').read_text()
        self.assertIn('Diff', html)
        self.assertIn('make optimize-apply', html)
        self.assertIn('Before finalizing', html)
        with self.assertRaisesRegex(ValueError, 'approval'):
            apply_optimization(self.config)
        feedback = self.approve_optimization(paths)
        export_optimization_review(self.config)
        self.assertEqual(read_json(paths['optimization'] / 'feedback.json'), feedback)
        report = apply_optimization(self.config)
        self.assertTrue(report['applied'])
        self.assertEqual(source.read_bytes(), (paths['new_skill'] / 'SKILL.md').read_bytes())
        self.assertEqual((paths['old_skill'] / 'SKILL.md').read_bytes(), original)
        self.assertEqual(resource.read_text(), 'Preserve this resource')
        self.assertEqual(apply_optimization(self.config), report)

    def test_make_approve_validates_and_preserves_feedback(self):
        paths = self.prepare_candidate()
        feedback_path = paths['optimization'] / 'feedback.json'
        before = read_json(feedback_path)
        before['reviews'][0]['feedback'] = 'Reviewed carefully'
        before['reviews'][0]['reviewer'] = 'fixture-reviewer'
        malformed = dict(before, reviews=[])
        write_json(feedback_path, malformed)
        with self.assertRaisesRegex(ValueError, 'every proposed change'):
            approve_optimization_feedback(self.config)
        write_json(feedback_path, before)

        config_path = self.root / 'approve-config.yaml'
        config_path.write_text(yaml.safe_dump(self.config))
        process = subprocess.run(['make', 'approve', f'CONFIG={config_path}'],
                                 capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        after = read_json(feedback_path)
        self.assertEqual(before['status'], 'pending')
        self.assertEqual(after['status'], 'complete')
        self.assertTrue(all(review['approved'] is True for review in after['reviews']))
        self.assertEqual(after['reviews'][0]['feedback'], 'Reviewed carefully')
        self.assertEqual(after['reviews'][0]['reviewer'], 'fixture-reviewer')
        self.assertTrue(apply_optimization(self.config)['applied'])

    def test_optimization_rejected_or_partial_approval_does_not_apply(self):
        paths = self.prepare_candidate()
        original = paths['source'].joinpath('SKILL.md').read_bytes()
        feedback_path = paths['optimization'] / 'feedback.json'
        feedback = self.approve_optimization(paths)
        feedback['reviews'][0]['approved'] = False
        write_json(feedback_path, feedback)
        with self.assertRaisesRegex(ValueError, 'approval'):
            apply_optimization(self.config)
        feedback['reviews'] = []
        write_json(feedback_path, feedback)
        with self.assertRaisesRegex(ValueError, 'approval'):
            apply_optimization(self.config)
        self.assertEqual(paths['source'].joinpath('SKILL.md').read_bytes(), original)

    def test_optimization_rejects_stale_working_copy_and_candidate(self):
        paths = self.prepare_candidate()
        self.approve_optimization(paths)
        source = paths['source'] / 'SKILL.md'
        source.write_text(source.read_text() + '\nLocal edit\n')
        with self.assertRaisesRegex(ValueError, 'Working Skill changed'):
            apply_optimization(self.config)
        candidate = paths['proposed_skill'] / 'SKILL.md'
        candidate.write_text(candidate.read_text() + '\nUnreviewed edit\n')
        with self.assertRaisesRegex(ValueError, 'Optimization files have changed'):
            apply_optimization(self.config)

    def cli_config(self, score=100, fail=False):
        outputs = []
        evaluations = []
        for case, output in zip(load_all_cases(self.config['dataset']['path']),
                                (self.specification, self.task_specification, self.file_specification)):
            outputs.append(output)
            evaluations.append(json.dumps({'criteria': [
                {'name': c.name, 'score': c.points * score // 100, 'reasoning': 'Offline fixture'}
                for c in case.rubric.criteria]}))
        self.config['judge']['provider'] = 'fake'
        self.config['providers']['fake'] = {'responses': [] if fail else outputs + evaluations}
        proposal = self.root / 'proposal.json'
        write_json(proposal, {'skill_md': Path(self.config['skill']['path']).read_text() + '\nVerify every finding against its source.\n',
                              'changes': [{'problem': 'Traceability', 'change': 'Verify findings', 'reason': 'DEV evidence'}]})
        self.config['optimization']['proposal_path'] = str(proposal)
        config_path = self.root / 'config.yaml'
        config_path.write_text(yaml.safe_dump(self.config))
        return config_path

    def test_make_runs_stage_dependencies_and_optimization(self):
        config_path = self.cli_config()
        for target in ('benchmark', 'aggregate', 'error-analysis', 'optimize'):
            process = subprocess.run(['make', target, 'VERSION=v0', f'CONFIG={config_path}'],
                                     capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        root = lifecycle_paths(self.config)['regression'] / 'v0'
        self.assertTrue(read_json(root / 'raw/benchmark.json')['complete'])
        self.assertTrue(read_json(root / 'summary.json')['passed'])
        self.assertTrue(read_json(root / 'error_analysis.json')['cases'])
        self.assertTrue((lifecycle_paths(self.config)['optimization'] / 'optimization.json').is_file())
        repeated = subprocess.run(['make', 'aggregate-dev', f'CONFIG={config_path}'], capture_output=True, text=True)
        self.assertEqual(repeated.returncode, 0, repeated.stdout + repeated.stderr)
        self.assertIn('Reusing completed DEV benchmark', repeated.stdout)

        for command in (['approve'], ['optimize-apply'], ['benchmark', 'VERSION=v1'],
                        ['aggregate', 'VERSION=v1'], ['regression', 'FROM=v0', 'TO=v1']):
            process = subprocess.run(['make', *command, f'CONFIG={config_path}'], capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        paths = lifecycle_paths(self.config)
        self.assertTrue((paths['new_skill'] / 'SKILL.md').is_file())
        self.assertTrue((paths['regression'] / 'v0/raw/benchmark.json').is_file())
        self.assertTrue((paths['regression'] / 'v1/raw/benchmark.json').is_file())
        self.assertTrue((paths['regression'] / 'v0-to-v1/regression.json').is_file())

    def test_make_stops_after_provider_failure_even_with_keep_going(self):
        config_path = self.cli_config(fail=True)
        process = subprocess.run(['make', 'benchmark', 'VERSION=v0', f'CONFIG={config_path}'],
                                 capture_output=True, text=True)
        self.assertNotEqual(process.returncode, 0)
        root = lifecycle_paths(self.config)['regression'] / 'v0/raw'
        self.assertFalse(read_json(root / 'benchmark.json')['complete'])
        self.assertFalse((root.parent / 'summary.json').exists())
        self.assertFalse((root.parent / 'error_analysis.json').exists())
        self.assertFalse(lifecycle_paths(self.config)['optimization'].exists())

    def test_low_scores_are_evidence_for_optimization_not_execution_errors(self):
        config_path = self.cli_config(score=10)
        for target in ('benchmark', 'aggregate', 'error-analysis', 'optimize'):
            process = subprocess.run(['make', target, 'VERSION=v0', f'CONFIG={config_path}'],
                                     capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        root = lifecycle_paths(self.config)['regression'] / 'v0'
        summary = read_json(root / 'summary.json')
        self.assertTrue(summary['complete'])
        self.assertFalse(summary['passed'])
        analysis = read_json(root / 'error_analysis.json')
        self.assertTrue(analysis['cases']['case-003']['fixture']['has_errors'])
        self.assertTrue((lifecycle_paths(self.config)['optimization'] / 'optimization.json').is_file())

    def test_reuse_rejects_stale_and_resumes_missing_evaluation(self):
        benchmark(self.config, providers=self.providers())
        self.config['output_language'] = 'en'
        with self.assertRaisesRegex(RuntimeError, 'does not match'):
            benchmark(self.config, reuse=True)
        self.config['output_language'] = 'ru'
        (Path(self.config['results_dir']) / 'case-003/fixture/run-01/evaluation.json').unlink()
        generation = FakeProvider([])
        judge = self.providers()['judge']
        result = benchmark(self.config, reuse=True, providers={'fake': generation, 'judge': judge})
        self.assertTrue(result['complete'])
        self.assertEqual(generation.calls, [])
        self.assertEqual(len(judge.calls), 1)

    def test_optimizer_missing_analysis_has_actionable_error(self):
        benchmark(self.config, providers=self.providers())
        root = Path(self.config['results_dir'])
        write_json(root / 'summary.json', aggregate_all(root, self.config['aggregation']['gates']))
        with self.assertRaisesRegex(RuntimeError, 'Missing error_analysis.json.*make optimize'):
            optimize(self.config)


if __name__ == '__main__':
    unittest.main()
