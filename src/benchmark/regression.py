"""Compare already generated DEV results for two Skill versions."""
import shutil
import statistics
from pathlib import Path

from .storage import identifier, lifecycle_paths, load_config, read_json, write_json, digest_tree, digest_json


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


def regression_report_path(config, from_version, to_version):
    paths = lifecycle_paths(config)
    legacy = paths["regression"] / "regression.json"
    if legacy.is_file():
        report = read_json(legacy)
        if (report.get("previous_version"), report.get("candidate_version")) == (from_version, to_version):
            return legacy
    return paths["regression"] / f"{from_version}-to-{to_version}" / "regression.json"


def _materialize_legacy_results(config, paths, version):
    """Preserve and expose pre-versioned DEV evidence without running a benchmark."""
    destination = paths["regression"] / version
    if (destination / "summary.json").is_file() or version != paths["previous"]:
        return
    source = Path(config["results_dir"])
    if not (source / "summary.json").is_file() or not (source / "benchmark.json").is_file():
        return
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination / "raw", ignore=shutil.ignore_patterns("summary.json", "error_analysis.json"))
    shutil.copy2(source / "summary.json", destination / "summary.json")
    if (source / "error_analysis.json").is_file():
        shutil.copy2(source / "error_analysis.json", destination / "error_analysis.json")


def run_regression(config, from_version=None, to_version=None, providers=None):
    paths = lifecycle_paths(config)
    from_version = identifier(from_version or paths["previous"])
    to_version = identifier(to_version or paths["candidate"])
    if from_version == to_version:
        raise ValueError("Regression versions must differ")
    root = paths["regression"]
    report_path = regression_report_path(config, from_version, to_version)
    if report_path.is_file():
        return read_json(report_path)
    summaries = []
    for version in (from_version, to_version):
        _materialize_legacy_results(config, paths, version)
        summary_path = root / version / "summary.json"
        skill = Path(config["lifecycle"]["versions_dir"]) / paths["name"] / version
        if not summary_path.is_file():
            raise RuntimeError(f"Missing summary for {version}; run make aggregate VERSION={version}")
        if not skill.is_dir():
            raise FileNotFoundError(f"Skill version does not exist: {skill}")
        summary = read_json(summary_path)
        if (summary.get("complete") is not True or
                summary.get("provenance", {}).get("skill_digest") != digest_tree(skill)):
            raise ValueError(f"DEV evidence does not match immutable Skill {version}")
        summaries.append(summary)
    previous, candidate = summaries
    result = compare(previous, candidate, config["regression"])
    versions = Path(config["lifecycle"]["versions_dir"]) / paths["name"]
    result.update({"skill_name": paths["name"], "previous_version": from_version, "candidate_version": to_version,
                   "previous_digest": digest_tree(versions / from_version),
                   "candidate_digest": digest_tree(versions / to_version),
                   "evidence_digest": digest_json({v: digest_tree(root / v) for v in (from_version, to_version)})})
    write_json(report_path, result)
    return result


def print_verdict(config, result):
    if result["passed"]:
        print("Regression пройдена: кандидат принят как улучшение.")
        return
    print("Regression не пройдена: кандидат не принят как улучшение.")
    paths = lifecycle_paths(config)
    previous_version = result.get("previous_version", paths["previous"])
    candidate_version = result.get("candidate_version", paths["candidate"])
    previous = _groups(read_json(paths["regression"] / previous_version / "summary.json"))
    candidate = _groups(read_json(paths["regression"] / candidate_version / "summary.json"))
    declines = [row for row in result.get("comparisons", []) if row.get("score_delta") is not None
                and row["score_delta"] < 0]
    if declines:
        print("Снижение общей оценки:")
        for number, row in enumerate(declines, 1):
            key = (row["case_id"], row["model"])
            before = previous[key]["score"]["mean"]
            after = candidate[key]["score"]["mean"]
            label = row["model"].rsplit("-", 1)[-1].capitalize()
            print(f"{number}. {row['case_id']} / {label}: {before:g} → {after:g}.")
    other = len(result.get("critical_regressions", [])) - len(declines)
    if other > 0:
        print(f"Дополнительно заблокировано сравнений из-за критериев, checks или quality gates: {other}.")
    print(f"Полный отчёт: {regression_report_path(config, previous_version, candidate_version)}")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/benchmark.yaml")
    parser.add_argument("--from-version", required=True)
    parser.add_argument("--to-version", required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    result = run_regression(config, args.from_version, args.to_version)
    print_verdict(config, result)
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
