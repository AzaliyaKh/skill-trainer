"""Run a dataset with incremental persistence and a fixed judge."""
import re
from pathlib import Path

from .case import load_all_cases
from .aggregate import aggregate_all
from .error_analysis import analyze
from .checks import validate_skill
from .provider import make_provider
from .runner import evaluate_saved_run, generate_and_check
from .storage import write_json, read_json, digest_tree, digest_json, identifier, lifecycle_paths, load_config


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
        "dataset_digest": digest_tree(dataset_dir, ignore_office_locks=True), "dataset_path": str(dataset_dir.resolve()),
        "settings_digest": digest_json({key: config.get(key) for key in
                                      ("models", "providers", "runs_per_case", "generation", "judge", "output_language", "aggregation")}),
        "expected_runs": [{"case_id": case.id, "model": name, "run_id": f"run-{run:02d}"}
                          for case in cases for name in model_names for run in range(1, runs_per_case + 1)],
    }
    if output_root.exists() and any(output_root.iterdir()):
        if not reuse:
            raise FileExistsError(f"Results already exist: {output_root}; choose a new results_dir")
        report_path = output_root / "benchmark.json"
        manifest_path = output_root / "manifest.json"
        if not manifest_path.is_file():
            raise RuntimeError(f"DEV benchmark has no manifest in {output_root}; choose a new results_dir")
        saved_manifest = read_json(manifest_path)
        if any(saved_manifest.get(key) != value for key, value in manifest.items()):
            raise RuntimeError("Existing DEV benchmark does not match current Skill, dataset or settings; choose a new results_dir")
        report = read_json(report_path) if report_path.is_file() else {
            **saved_manifest, "complete": False, "failures": [],
        }
        case_by_id = {case.id: case for case in cases}
        model_by_name = {_safe_model_name(item['model']): item for item in config['models']}
        resumable = []
        regenerate = []
        for item in manifest["expected_runs"]:
            directory = output_root / item["case_id"] / item["model"] / item["run_id"]
            required = ("usage.json", "checks.json", "evaluation.json", "status.json")
            complete = (not (directory / "error.json").exists()
                        and all((directory / name).is_file() for name in required)
                        and read_json(directory / "status.json").get("status") == "complete")
            if complete:
                continue
            error_path = directory / "error.json"
            error = read_json(error_path) if error_path.is_file() else {}
            status_path = directory / "status.json"
            status = read_json(status_path).get("status") if status_path.is_file() else None
            generation_ready = ((directory / "usage.json").is_file()
                                and (directory / "checks.json").is_file()
                                and (directory / "workspace/outputs").is_dir())
            if generation_ready and (status == "ready_for_evaluation" or error.get("stage") == "evaluation"
                                     or not (directory / "evaluation.json").is_file()):
                resumable.append((case_by_id[item["case_id"]], item["model"], item["run_id"], directory))
                continue
            if not directory.exists() or error.get("stage") in {"generation", "checks"} or not generation_ready:
                regenerate.append((case_by_id[item["case_id"]], model_by_name[item["model"]],
                                   int(item["run_id"].removeprefix("run-")), directory))
                continue
            raise RuntimeError(f"Existing DEV run cannot be resumed safely: {directory}; choose a new results_dir")
        if not resumable and not regenerate:
            print(f"Reusing completed DEV benchmark: {output_root}")
            return report
        failures = []
        if regenerate:
            print(f"Retrying generation for {len(regenerate)} failed run(s): {output_root}")
        for case, model_config, run_id, directory in regenerate:
            # Keep partial artifacts and diagnostics outside raw results so they
            # cannot be counted as additional benchmark runs.
            if directory.exists():
                archive_root = output_root.with_name(output_root.name + "-failed-attempts") / directory.relative_to(output_root)
                attempt = 1
                while (archive_root / f"attempt-{attempt:02d}").exists():
                    attempt += 1
                archive_root.mkdir(parents=True, exist_ok=True)
                directory.rename(archive_root / f"attempt-{attempt:02d}")
            model = model_config["model"]
            print(f"[{model}] {case.id} run {run_id}/{runs_per_case}")
            error = generate_and_check(config, model_config, case, skill_content, skill_dir,
                                       run_id, directory, provider)
            if error:
                failures.append({"case_id": case.id, "model": model, "run_id": run_id, **error})
            else:
                resumable.append((case, model, run_id, directory))
        print(f"Resuming semantic evaluation for {len(resumable)} saved run(s): {output_root}")
        for case, model, run_id, directory in resumable:
            print()
            print(f"[judge: {config['judge']['model']}] {model} / {case.id} {run_id}")
            error = evaluate_saved_run(config, case, directory, provider)
            if error:
                failures.append({"case_id": case.id, "model": model, "run_id": run_id, **error})
        result = {**manifest, "complete": not failures, "failures": failures}
        write_json(report_path, result)
        return result
    write_json(output_root / "manifest.json", manifest)
    failures = []
    ready = []
    print("\nPhase 1/2: generation and deterministic checks")
    for model_config in config["models"]:
        model = model_config["model"]
        for case in cases:
            for run_id in range(1, runs_per_case + 1):
                run_dir = output_root / case.id / _safe_model_name(model) / f"run-{run_id:02d}"
                print()
                print(f"[{model}] {case.id} run {run_id}/{runs_per_case}")
                error = generate_and_check(
                    config, model_config, case, skill_content, skill_dir, run_id, run_dir, provider,
                )
                if error:
                    failures.append({"case_id": case.id, "model": model, "run_id": run_id, **error})
                else:
                    ready.append((case, model, run_id, run_dir))
    print("\nPhase 2/2: semantic evaluation")
    for case, model, run_id, run_dir in ready:
        print()
        print(f"[judge: {config['judge']['model']}] {model} / {case.id} run {run_id}/{runs_per_case}")
        error = evaluate_saved_run(config, case, run_dir, provider)
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


def benchmark_version(config, version, providers=None, reuse=False):
    """Run DEV only for one immutable Skill version."""
    paths = lifecycle_paths(config)
    version = paths["candidate"] if version == "candidate" else paths["previous"] if version == "previous" else version
    version = identifier(version)
    skill = Path(config["lifecycle"]["versions_dir"]) / paths["name"] / version
    if not skill.is_dir() and version == paths["previous"]:
        skill = paths["source"]
    if not skill.is_dir():
        raise FileNotFoundError(f"Skill version does not exist: {skill}")
    root = paths["regression"] / version / "raw"
    return benchmark(config, skill_dir=skill, dataset_dir=Path(config["dataset"]["path"]),
                     results_dir=root, providers=providers, split="dev", reuse=reuse)


def run_holdout(config, providers=None, version=None):
    paths = lifecycle_paths(config)
    if version is not None:
        version = identifier(version)
        paths["candidate"] = version
        paths["new_skill"] = Path(config["lifecycle"]["versions_dir"]) / paths["name"] / version
        paths["holdout"] = Path(config["lifecycle"]["runs_dir"]) / "holdout" / paths["name"] / version
    if not paths["new_skill"].is_dir():
        raise FileNotFoundError(f"Skill version does not exist: {paths['new_skill']}")
    print(f"Holdout Skill: {paths['new_skill']} (version {paths['candidate']})")
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
    parser.add_argument("--version", "--skill-version", dest="version",
                        help="Run DEV or holdout for this Skill version")
    parser.add_argument("--reuse", action="store_true", help="Reuse matching DEV results and retry failed generation/evaluation")
    args = parser.parse_args()
    config = load_config(args.config)
    result = (run_holdout(config, version=args.version) if args.holdout else
              benchmark_version(config, args.version, reuse=args.reuse) if args.version else
              benchmark(config, reuse=args.reuse))
    if not result["passed" if args.holdout else "complete"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
