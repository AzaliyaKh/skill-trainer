"""Explain existing evidence without another model call."""
from pathlib import Path
from .storage import cli_config, read_json, write_json


def analyze(summary, runs_dir, thresholds):
    result = {"cases": {}, "provenance": summary.get("provenance", {})}
    for case_id, models in summary["cases"].items():
        result["cases"][case_id] = {}
        for model, group in models.items():
            issues, failed_checks, reasoning, runs = [], {}, {}, []
            if not group["gate"]["passed"]:
                issues.append({"type": "failed_gates", "gates": group["gate"]["gates"]})
            for name, stats in group["criteria"].items():
                if stats["mean"] is not None and stats["mean"] < thresholds["weak_criterion"]:
                    issues.append({"type": "weak_criterion", "name": name, "mean": stats["mean"]})
            if (group["score"]["stddev"] or 0) > thresholds["high_stddev"]:
                issues.append({"type": "high_variance", "stddev": group["score"]["stddev"]})
            for run in group["runs"]:
                directory = Path(runs_dir) / case_id / model / run["run"]
                record = dict(run)
                if run["score"] is not None and run["score"] < thresholds["low_score"]:
                    issues.append({"type": "low_score", **run})
                if (directory / "error.json").exists():
                    record["error"] = read_json(directory / "error.json")
                    issues.append({"type": "run_error", **record})
                if (directory / "checks.json").exists():
                    for check in read_json(directory / "checks.json")["checks"]:
                        if not check["passed"]:
                            failed_checks.setdefault(check["name"], []).append({"run": run["run"], **check})
                if (directory / "evaluation.json").exists():
                    for criterion in read_json(directory / "evaluation.json")["criteria"]:
                        reasoning.setdefault(criterion["name"], []).append({"run": run["run"], **criterion})
                runs.append(record)
            for name, failures in failed_checks.items():
                issues.append({"type": "recurring_check" if len(failures) >= thresholds["recurring_count"] else "failed_check",
                               "name": name, "count": len(failures)})
            result["cases"][case_id][model] = {"has_errors": bool(issues), "issues": issues,
                                               "failed_checks": failed_checks, "judge_reasoning": reasoning, "runs": runs}
    result["missing_runs"] = summary.get("missing_runs", [])
    return result


def main():
    config = cli_config()
    root = Path(config["results_dir"])
    summary_path = root / "summary.json"
    if not summary_path.is_file():
        raise SystemExit("Missing DEV summary; run make error-analysis to execute its prerequisites")
    summary = read_json(summary_path)
    if summary.get("complete") is not True:
        raise SystemExit("DEV aggregation is incomplete; fix the failed benchmark before error analysis")
    result = analyze(summary, root, config["error_analysis"])
    write_json(root / "error_analysis.json", result)
    print(f"Error analysis written to {root / 'error_analysis.json'}")


if __name__ == "__main__":
    main()
