import math
import statistics
from pathlib import Path

from .storage import cli_config, read_json, write_json


def _stats(values: list[float], confidence_level=0.95) -> dict:
    if not 0 < confidence_level < 1:
        raise ValueError("confidence_level must be between 0 and 1")
    if not values:
        return {
            "confidence_level": confidence_level,
            "ci_low": None, "ci_high": None,
            "mean": None,
            "stddev": None,
            "min": None,
            "max": None,
            "ci95_low": None,
            "ci95_high": None,
        }

    mean = statistics.mean(values)

    if len(values) == 1:
        return {
            "confidence_level": confidence_level,
            "ci_low": None, "ci_high": None,
            "mean": mean,
            "stddev": 0.0,
            "min": values[0],
            "max": values[0],
            "ci95_low": None,
            "ci95_high": None,
        }

    stddev = statistics.stdev(values)

    standard_error = (
        stddev / math.sqrt(len(values))
    )

    margin = (
        1.96
        * standard_error
    )

    configured_margin = statistics.NormalDist().inv_cdf((1 + confidence_level) / 2) * standard_error
    return {
        "confidence_level": confidence_level,
        "ci_low": mean - configured_margin,
        "ci_high": mean + configured_margin,
        "mean": mean,
        "stddev": stddev,
        "min": min(values),
        "max": max(values),
        "ci95_low": mean - margin,
        "ci95_high": mean + margin,
    }


def aggregate_model_runs(
    model_dir: Path,
    confidence_level=0.95,
) -> dict:

    scores = []
    criteria_scores = {}

    checks_passed = 0
    checks_failed = 0
    critical_failures = 0

    input_tokens = []
    output_tokens = []
    costs = []
    latencies = []

    runs = []
    incomplete_runs = []

    for run_dir in sorted(
        model_dir.glob("run-*")
    ):

        evaluation_path = (
            run_dir / "evaluation.json"
        )

        checks_path = (
            run_dir / "checks.json"
        )

        usage_path = (
            run_dir / "usage.json"
        )

        complete = all(p.exists() for p in (evaluation_path, checks_path, usage_path)) and not (run_dir / "error.json").exists()
        if not complete:
            incomplete_runs.append(run_dir.name)
        evaluation = read_json(evaluation_path) if evaluation_path.exists() else None

        checks = read_json(checks_path) if checks_path.exists() else {}

        usage = read_json(usage_path) if usage_path.exists() else {}

        score_percent = None
        if complete:
            total_score = evaluation[
                "total_score"
            ]

            max_score = evaluation[
                "max_score"
            ]

            score_percent = (
                total_score
                / max_score
                * 100
            )

            scores.append(
                score_percent
            )

            for criterion in evaluation[
                "criteria"
            ]:

                name = criterion["name"]

                criterion_percent = (
                    criterion["score"]
                    / criterion["max_score"]
                    * 100
                )

                criteria_scores.setdefault(
                    name,
                    [],
                ).append(
                    criterion_percent
                )

        critical_failures += sum(not c["passed"] and c.get("severity") == "critical" for c in checks.get("checks", []))
        checks_passed += checks.get(
            "passed",
            0,
        )

        checks_failed += checks.get(
            "failed",
            0,
        )

        if usage.get(
            "input_tokens"
        ) is not None:
            input_tokens.append(
                usage["input_tokens"]
            )

        if usage.get(
            "output_tokens"
        ) is not None:
            output_tokens.append(
                usage["output_tokens"]
            )

        if usage.get(
            "cost_usd"
        ) is not None:
            costs.append(
                usage["cost_usd"]
            )

        if usage.get(
            "latency_seconds"
        ) is not None:
            latencies.append(
                usage["latency_seconds"]
            )

        runs.append(
            {
                "run": run_dir.name,
                "score": score_percent,
            }
        )

    total_checks = (
        checks_passed
        + checks_failed
    )

    if total_checks:
        checks_pass_rate = (
            checks_passed
            / total_checks
        )
    else:
        checks_pass_rate = None

    criteria_summary = {}

    for name, values in (
        criteria_scores.items()
    ):
        criteria_summary[name] = _stats(
            values, confidence_level
        )

    return {
        "runs_count": len(runs),
        "completed_runs_count": len(scores),
        "incomplete_runs": incomplete_runs,

        "score": _stats(scores, confidence_level),

        "criteria": criteria_summary,

        "checks": {
            "passed": checks_passed,
            "failed": checks_failed,
            "critical_failures": critical_failures,
            "pass_rate":
                checks_pass_rate,
        },

        "usage": {
            "input_tokens_total":
                sum(input_tokens) if input_tokens else None,

            "output_tokens_total":
                sum(output_tokens) if output_tokens else None,

            "cost_usd_total":
                sum(costs) if costs else None,

            "cost_usd_mean":
                statistics.mean(costs)
                if costs
                else None,

            "latency_seconds_mean":
                statistics.mean(latencies)
                if latencies
                else None,
        },

        "runs": runs,
    }


def apply_gates(
    summary: dict,
    gates: dict,
) -> dict:

    unknown = set(gates) - {"min_score_mean", "min_checks_pass_rate"}
    if unknown:
        raise ValueError(f"Unknown gates: {sorted(unknown)}")
    results = [{"name": "no_critical_failures", "passed": not summary["checks"].get("critical_failures", 0)},
               {"name": "complete_runs", "passed": summary["runs_count"] > 0 and not summary.get("incomplete_runs"),
                "value": summary.get("completed_runs_count", summary["runs_count"]), "threshold": summary["runs_count"]}]

    score_mean = summary[
        "score"
    ]["mean"]

    min_score = gates.get(
        "min_score_mean"
    )

    if min_score is not None:
        results.append(
            {
                "name":
                    "min_score_mean",

                "passed":
                    score_mean is not None
                    and score_mean
                    >= min_score,

                "value":
                    score_mean,

                "threshold":
                    min_score,
            }
        )

    checks_pass_rate = summary[
        "checks"
    ]["pass_rate"]

    min_checks = gates.get(
        "min_checks_pass_rate"
    )

    if min_checks is not None:
        results.append(
            {
                "name":
                    "min_checks_pass_rate",

                "passed":
                    checks_pass_rate
                    is not None
                    and checks_pass_rate
                    >= min_checks,

                "value":
                    checks_pass_rate,

                "threshold":
                    min_checks,
            }
        )

    return {
        "passed": all(
            gate["passed"]
            for gate in results
        ),
        "gates": results,
    }


def aggregate_all(
    runs_dir: Path,
    gates: dict,
    confidence_level=0.95,
) -> dict:

    result = {
        "cases": {}
    }

    for case_dir in sorted(
        runs_dir.iterdir()
    ):

        if not case_dir.is_dir():
            continue

        case_result = {}

        for model_dir in sorted(
            case_dir.iterdir()
        ):

            if not model_dir.is_dir():
                continue

            summary = aggregate_model_runs(
                model_dir, confidence_level
            )

            gate_result = apply_gates(
                summary,
                gates,
            )

            case_result[
                model_dir.name
            ] = {
                **summary,
                "gate":
                    gate_result,
            }

        result["cases"][
            case_dir.name
        ] = case_result

    manifest_path = runs_dir / "manifest.json"
    if manifest_path.exists():
        manifest = read_json(manifest_path)
        result["provenance"] = manifest
        missing = []
        for item in manifest["expected_runs"]:
            directory = runs_dir / item["case_id"] / item["model"] / item["run_id"]
            if not all((directory / name).exists() for name in ("usage.json", "checks.json", "evaluation.json", "status.json")):
                missing.append(item)
        result["missing_runs"] = missing
    groups = [group for models in result["cases"].values() for group in models.values()]
    result["complete"] = bool(groups) and not result.get("missing_runs") and all(not group["incomplete_runs"] for group in groups)
    result["passed"] = result["complete"] and all(group["gate"]["passed"] for group in groups)
    result["applied_gates"] = gates
    return result


def main() -> None:
    config = cli_config()
    runs_dir = Path(config.get("results_dir", "runs/dev"))
    aggregation = config.get("aggregation", {})
    result = aggregate_all(runs_dir, aggregation.get("gates", {}), aggregation.get("confidence_level", 0.95))
    output_path = runs_dir / "summary.json"
    write_json(output_path, result)
    print(f"Aggregation written to {output_path}")
    if not result["complete"]:
        raise SystemExit("Aggregation incomplete: DEV has failed or missing runs; inspect error.json and rerun in a new results_dir")
    if not result["passed"]:
        print("Quality gates failed; results are complete and available for error analysis and optimization")


if __name__ == "__main__":
    main()
