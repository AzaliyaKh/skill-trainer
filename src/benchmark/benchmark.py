"""Run a dataset with incremental persistence and a fixed judge."""
import re
from pathlib import Path

from .case import load_all_cases
from .aggregate import aggregate_all
from .error_analysis import analyze
from .checks import validate_skill
from .provider import make_provider
from .runner import run_and_evaluate
from .storage import write_json, read_json, digest_tree, digest_json, lifecycle_paths, load_config


def _safe_model_name(model: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", model.replace("/", "__"))
    if not name or name in (".", ".."):
        raise ValueError("Invalid model name")
    return name


def benchmark(config, *, skill_dir=None, dataset_dir=None, results_dir=None, providers=None, split="dev", reuse=False):
    skill_dir = Path(skill_dir or Path(config["skill"]["path"]).parent)
    dataset_dir = Path(dataset_dir or config["dataset"]["path"])
    output_root = Path(results_dir or config["results_dir"])
    from .executor import EXECUTORS
    import yaml
    metadata = yaml.safe_load((skill_dir / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1])
    validation = validate_skill(skill_dir, metadata["name"])
    if not validation["valid"]:
        raise ValueError(f"Invalid Skill: {validation['errors']}")
    cases = load_all_cases(str(dataset_dir))
    if any(c.execution["mode"] not in EXECUTORS for c in cases):
        raise ValueError("Dataset contains an unregistered execution mode")
    runs_per_case = config.get("runs_per_case", 1)
    if type(runs_per_case) is not int or runs_per_case < 1:
        raise ValueError("runs_per_case must be a positive integer")
    model_names = [_safe_model_name(m["model"]) for m in config["models"]]
    if not model_names or len(set(model_names)) != len(model_names):
        raise ValueError("Models must have distinct safe names")
    skill_content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    supplied = dict(providers or {})

    def provider(name):
        if name not in supplied:
            supplied[name] = make_provider(name, config.get("providers", {}).get(name))
        return supplied[name]

    manifest = {
        "split": split, "skill_digest": digest_tree(skill_dir),
        "dataset_digest": digest_tree(dataset_dir), "dataset_path": str(dataset_dir.resolve()),
        "settings_digest": digest_json({key: config.get(key) for key in
                                      ("models", "providers", "runs_per_case", "generation", "judge", "output_language", "aggregation")}),
        "expected_runs": [{"case_id": case.id, "model": name, "run_id": f"run-{run:02d}"}
                          for case in cases for name in model_names for run in range(1, runs_per_case + 1)],
    }
    if output_root.exists() and any(output_root.iterdir()):
        if not reuse:
            raise FileExistsError(f"Results already exist: {output_root}; choose a new results_dir")
        report_path = output_root / "benchmark.json"
        if not report_path.is_file():
            raise RuntimeError(f"DEV benchmark is unfinished in {output_root}; choose a new results_dir and rerun make dev-benchmark")
        report = read_json(report_path)
        if report.get("complete") is not True:
            failures = report.get("failures", [])
            diagnostic = failures[0].get("error", "unknown failure") if failures else "unknown failure"
            raise RuntimeError(f"DEV benchmark failed: {diagnostic}. Preserve {output_root}; choose a new results_dir to retry")
        if any(report.get(key) != value for key, value in manifest.items()):
            raise RuntimeError("Existing DEV benchmark does not match current Skill, dataset or settings; choose a new results_dir")
        for item in manifest["expected_runs"]:
            directory = output_root / item["case_id"] / item["model"] / item["run_id"]
            required = ("usage.json", "checks.json", "evaluation.json", "status.json")
            if ((directory / "error.json").exists() or not all((directory / name).is_file() for name in required)
                    or read_json(directory / "status.json").get("status") != "complete"):
                raise RuntimeError(f"Existing DEV run is incomplete: {directory}; choose a new results_dir")
        print(f"Reusing completed DEV benchmark: {output_root}")
        return report
    write_json(output_root / "manifest.json", manifest)
    failures = []
    for model_config in config["models"]:
        model = model_config["model"]
        for case in cases:
            for run_id in range(1, runs_per_case + 1):
                run_dir = output_root / case.id / _safe_model_name(model) / f"run-{run_id:02d}"
                print()
                print(f"[{model}] {case.id} run {run_id}/{runs_per_case}")
                error = run_and_evaluate(
                    config, model_config, case, skill_content, skill_dir, run_id, run_dir, provider,
                )
                if error:
                    failures.append({"case_id": case.id, "model": model, "run_id": run_id, **error})
    print()
    result = {**manifest, "complete": not failures, "failures": failures}
    write_json(output_root / "benchmark.json", result)
    return result


def evaluate_dataset(config, skill, dataset, root, split, providers=None):
    validation = validate_skill(skill, lifecycle_paths(config)["name"])
    if not validation["valid"]:
        raise ValueError(f"Invalid Skill: {validation['errors']}")
    benchmark(config, skill_dir=skill, dataset_dir=dataset, results_dir=root / "raw", providers=providers, split=split)
    summary = aggregate_all(root / "raw", config["aggregation"]["gates"], config["aggregation"].get("confidence_level", 0.95))
    write_json(root / "summary.json", summary)
    write_json(root / "error_analysis.json", analyze(summary, root / "raw", config["error_analysis"]))
    write_json(root / "validation.json", validation)
    return summary


def run_holdout(config, providers=None):
    paths = lifecycle_paths(config)
    dev, holdout = Path(config["dataset"]["path"]).resolve(), Path(config["dataset"]["holdout_path"]).resolve()
    if dev == holdout or dev.is_relative_to(holdout) or holdout.is_relative_to(dev):
        raise ValueError("Holdout and DEV must be disjoint directories")
    root = paths["holdout"]
    summary = evaluate_dataset(config, paths["new_skill"], holdout, root, "holdout", providers)
    groups = {(case, model): group for case, models in summary["cases"].items() for model, group in models.items()}
    failed = [{"case_id": case, "model": model} for (case, model), group in groups.items() if not group["gate"]["passed"]]
    critical = [{"case_id": case, "model": model, "count": group["checks"].get("critical_failures", 0)}
                for (case, model), group in groups.items() if group["checks"].get("critical_failures", 0)]
    result = {"passed": summary["passed"] and not critical, "skill_name": paths["name"], "version": paths["candidate"],
              "candidate_digest": digest_tree(paths["new_skill"]), "applied_gates": config["aggregation"]["gates"],
              "overall_scores": {case + "/" + model: group["score"] for (case, model), group in groups.items()},
              "failed_cases": failed, "critical_failures": critical,
              "evidence_digest": digest_tree(root / "raw"), "summary_digest": digest_json(summary)}
    write_json(root / "holdout.json", result)
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/benchmark.yaml")
    parser.add_argument("--holdout", action="store_true")
    parser.add_argument("--reuse", action="store_true", help="Reuse only complete, matching DEV results")
    args = parser.parse_args()
    config = load_config(args.config)
    result = run_holdout(config) if args.holdout else benchmark(config, reuse=args.reuse)
    if not result["passed" if args.holdout else "complete"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
