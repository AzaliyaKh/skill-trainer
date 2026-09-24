"""Compare previous and candidate Skill versions on the same DEV dataset."""
import statistics
from pathlib import Path

from .benchmark import evaluate_dataset
from .storage import cli_config, lifecycle_paths, write_json, digest_tree, digest_json


def _groups(summary):
    return {(case, model): group for case, models in summary["cases"].items() for model, group in models.items()}


def compare(previous, candidate, policy):
    old, new = _groups(previous), _groups(candidate)
    compatible = bool(old) and old.keys() == new.keys()
    for key in ("dataset_digest", "settings_digest", "expected_runs"):
        compatible &= previous.get("provenance", {}).get(key) == candidate.get("provenance", {}).get(key)
    deltas, improved, degraded, critical = [], [], [], []
    def delta(a, b):
        return b - a if a is not None and b is not None else None
    for case_model in sorted(old.keys() | new.keys()):
        case, model = case_model
        if case_model not in old or case_model not in new:
            critical.append({"case_id": case, "model": model, "reason": "missing comparison"})
            continue
        a, b = old[case_model], new[case_model]
        score = delta(a["score"]["mean"], b["score"]["mean"])
        checks = delta(a["checks"]["pass_rate"], b["checks"]["pass_rate"])
        variance = delta((a["score"]["stddev"] or 0) ** 2, (b["score"]["stddev"] or 0) ** 2)
        criteria = {name: delta(a["criteria"].get(name, {}).get("mean"), b["criteria"].get(name, {}).get("mean"))
                    for name in a["criteria"].keys() | b["criteria"].keys()}
        record = {"case_id": case, "model": model, "score_delta": score, "criteria_delta": criteria,
                  "checks_delta": checks, "variance_delta": variance}
        deltas.append(record)
        if score is not None and score > 0:
            improved.append(record)
        if score is not None and (score < 0 or any(v is not None and v < 0 for v in criteria.values()) or (checks or 0) < 0):
            degraded.append(record)
        invalid = (score is None or score < -policy["max_score_drop"] or
                   any(v is None or v < -policy["max_criterion_drop"] for v in criteria.values()) or
                   checks is None or checks < -policy["max_checks_drop"] or
                   (b["score"]["stddev"] or 0) - (a["score"]["stddev"] or 0) > policy["max_stddev_increase"] or
                   not b["gate"]["passed"] or b["checks"].get("critical_failures", 0) > 0)
        if invalid:
            critical.append(record)
    values = [r["score_delta"] for r in deltas if r["score_delta"] is not None]
    return {"passed": compatible and candidate.get("passed", False) and not critical,
            "compatible": compatible, "total_score_delta": statistics.mean(values) if values else None,
            "comparisons": deltas, "improved_cases": improved, "degraded_cases": degraded,
            "critical_regressions": critical, "applied_gates": policy}


def run_regression(config, providers=None):
    paths = lifecycle_paths(config)
    root = paths["regression"]
    if (root / "regression.json").exists():
        raise FileExistsError("Regression report already exists; use a new version pair/results root")
    summaries = []
    for version, skill in ((paths["previous"], paths["old_skill"]), (paths["candidate"], paths["new_skill"])):
        summaries.append(evaluate_dataset(config, skill, Path(config["dataset"]["path"]), root / version, "dev", providers))
    result = compare(*summaries, config["regression"])
    result.update({"skill_name": paths["name"], "previous_version": paths["previous"], "candidate_version": paths["candidate"],
                   "previous_digest": digest_tree(paths["old_skill"]), "candidate_digest": digest_tree(paths["new_skill"]),
                   "evidence_digest": digest_json({v: digest_tree(root / v) for v in (paths["previous"], paths["candidate"])})})
    write_json(root / "regression.json", result)
    return result


def main():
    if not run_regression(cli_config())["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
