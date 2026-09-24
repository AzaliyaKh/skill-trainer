import json
import shutil
import subprocess
import yaml
import tempfile
import unittest
from pathlib import Path

from src.benchmark.aggregate import aggregate_all
from src.benchmark.benchmark import benchmark, run_holdout
from src.benchmark.case import load_all_cases
from src.benchmark.error_analysis import analyze
from src.benchmark.optimizer import optimize, apply_optimization
from src.benchmark.provider import FakeProvider
from src.benchmark.regression import run_regression, compare
from src.benchmark.release import release
from src.benchmark.review import export_review, export_optimization_review
from src.benchmark.storage import load_config, write_json, read_json, lifecycle_paths


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = load_config('config/benchmark.yaml')
        source = Path(self.config['skill']['path']).parent
        target = self.root / 'skill' / source.name
        shutil.copytree(source, target)
        self.config['skill']['path'] = str(target / 'SKILL.md')
        self.config['results_dir'] = str(self.root / 'dev')
        self.config['models'] = [{'provider': 'fake', 'model': 'fixture'}]
        self.config['judge']['provider'] = 'judge'
        for field in ('runs_dir', 'versions_dir', 'releases_dir'):
            self.config['lifecycle'][field] = str(self.root / field)
        self.report = 'Противоречия REQ-1 и REQ-2. Рекомендации: уточнить почту и время ответа.'
        self.svg = '<svg xmlns="http://www.w3.org/2000/svg"><text>Согласование</text></svg>'

    def providers(self, split='dev', repeat=1):
        cases = load_all_cases('dataset/example/' + split)
        judge = [json.dumps({'criteria': [{'name': c.name, 'score': c.points, 'reasoning': 'Fixture score'}
                                         for c in case.rubric.criteria]}) for case in cases] * repeat
        outputs = ([self.report, self.svg] if split == 'dev' else
                   [json.dumps([{'requirement': 'R-1/R-2', 'issue': 'Сроки несовместимы', 'question': 'Нужен архив?'}])]) * repeat
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
        self.approve_optimization(paths)
        apply_optimization(self.config)
        regression = run_regression(self.config, self.providers(repeat=2))
        self.assertTrue(regression['passed'])
        self.assertEqual(regression['total_score_delta'], 0)
        self.assertTrue(run_holdout(self.config, self.providers('holdout'))['passed'])
        feedback_path = export_review(self.config)
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
        output = paths['holdout'] / 'raw/case-101/fixture/run-01/workspace/outputs/issues.json'
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
        (root / 'case-002/fixture/run-01/evaluation.json').unlink()
        summary = aggregate_all(root, self.config['aggregation']['gates'])
        self.assertFalse(summary['passed'])
        self.assertEqual(len(summary['missing_runs']), 1)

    def test_regression_detects_criterion_drop_without_total_drop(self):
        import copy
        benchmark(self.config, providers=self.providers())
        previous = aggregate_all(Path(self.config['results_dir']), self.config['aggregation']['gates'])
        candidate = copy.deepcopy(previous)
        candidate['cases']['case-002']['fixture']['criteria']['source_fidelity']['mean'] -= 10
        result = compare(previous, candidate, self.config['regression'])
        self.assertEqual(result['total_score_delta'], 0)
        self.assertFalse(result['passed'])
        self.assertTrue(result['critical_regressions'])

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
        candidate = paths['new_skill'] / 'SKILL.md'
        candidate.write_text(candidate.read_text() + '\nUnreviewed edit\n')
        with self.assertRaisesRegex(ValueError, 'Optimization files have changed'):
            apply_optimization(self.config)

    def cli_config(self, score=100, fail=False):
        responses = []
        for case, output in zip(load_all_cases(self.config['dataset']['path']), (self.report, self.svg)):
            responses += [output, json.dumps({'criteria': [
                {'name': c.name, 'score': c.points * score // 100, 'reasoning': 'Offline fixture'}
                for c in case.rubric.criteria]})]
        self.config['judge']['provider'] = 'fake'
        self.config['providers']['fake'] = {'responses': [] if fail else responses}
        proposal = self.root / 'proposal.json'
        write_json(proposal, {'skill_md': Path(self.config['skill']['path']).read_text() + '\nVerify every finding against its source.\n',
                              'changes': [{'problem': 'Traceability', 'change': 'Verify findings', 'reason': 'DEV evidence'}]})
        self.config['optimization']['proposal_path'] = str(proposal)
        config_path = self.root / 'config.yaml'
        config_path.write_text(yaml.safe_dump(self.config))
        return config_path

    def test_make_runs_stage_dependencies_and_optimization(self):
        config_path = self.cli_config()
        process = subprocess.run(['make', '-j4', 'optimize', f'CONFIG={config_path}'], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        root = Path(self.config['results_dir'])
        self.assertTrue(read_json(root / 'benchmark.json')['complete'])
        self.assertTrue(read_json(root / 'summary.json')['passed'])
        self.assertTrue(read_json(root / 'error_analysis.json')['cases'])
        self.assertTrue((lifecycle_paths(self.config)['optimization'] / 'optimization.json').is_file())
        repeated = subprocess.run(['make', 'aggregate-dev', f'CONFIG={config_path}'], capture_output=True, text=True)
        self.assertEqual(repeated.returncode, 0, repeated.stdout + repeated.stderr)
        self.assertIn('Reusing completed DEV benchmark', repeated.stdout)

    def test_make_stops_after_provider_failure_even_with_keep_going(self):
        config_path = self.cli_config(fail=True)
        process = subprocess.run(['make', '-k', '-j4', 'aggregate-dev', 'error-analysis', 'optimize', f'CONFIG={config_path}'],
                                 capture_output=True, text=True)
        self.assertNotEqual(process.returncode, 0)
        root = Path(self.config['results_dir'])
        self.assertFalse(read_json(root / 'benchmark.json')['complete'])
        self.assertFalse((root / 'summary.json').exists())
        self.assertFalse((root / 'error_analysis.json').exists())
        self.assertFalse(lifecycle_paths(self.config)['optimization'].exists())

    def test_low_scores_are_evidence_for_optimization_not_execution_errors(self):
        config_path = self.cli_config(score=10)
        process = subprocess.run(['make', 'optimize', f'CONFIG={config_path}'], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        root = Path(self.config['results_dir'])
        summary = read_json(root / 'summary.json')
        self.assertTrue(summary['complete'])
        self.assertFalse(summary['passed'])
        analysis = read_json(root / 'error_analysis.json')
        self.assertTrue(analysis['cases']['case-001']['fixture']['has_errors'])
        self.assertTrue((lifecycle_paths(self.config)['optimization'] / 'optimization.json').is_file())

    def test_reuse_rejects_stale_or_missing_results(self):
        benchmark(self.config, providers=self.providers())
        self.config['output_language'] = 'en'
        with self.assertRaisesRegex(RuntimeError, 'does not match'):
            benchmark(self.config, reuse=True)
        self.config['output_language'] = 'ru'
        (Path(self.config['results_dir']) / 'case-001/fixture/run-01/evaluation.json').unlink()
        with self.assertRaisesRegex(RuntimeError, 'incomplete'):
            benchmark(self.config, reuse=True)

    def test_optimizer_missing_analysis_has_actionable_error(self):
        benchmark(self.config, providers=self.providers())
        root = Path(self.config['results_dir'])
        write_json(root / 'summary.json', aggregate_all(root, self.config['aggregation']['gates']))
        with self.assertRaisesRegex(RuntimeError, 'Missing error_analysis.json.*make optimize'):
            optimize(self.config)


if __name__ == '__main__':
    unittest.main()
