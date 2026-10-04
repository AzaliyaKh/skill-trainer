"""Fail-closed release packaging bound to the validated candidate and evidence."""
import shutil
import tempfile
from datetime import datetime, timezone

from .review import validate_feedback
from .storage import cli_config, lifecycle_paths, read_json, write_json, digest_tree, digest_json
from .checks import validate_skill
from .regression import compare
from .regression import regression_report_path


def release(config):
    paths = lifecycle_paths(config)
    validation = validate_skill(paths["new_skill"], paths["name"])
    if not validation["valid"]:
        raise ValueError(f"Candidate validation failed: {validation['errors']}")
    candidate_digest = validation["skill_digest"]
    regression = read_json(regression_report_path(config, paths["previous"], paths["candidate"]))
    holdout = read_json(paths["holdout"] / "holdout.json")
    if any(report.get("candidate_digest") != candidate_digest or report.get("passed") is not True
           for report in (regression, holdout)):
        raise ValueError("Release requires passing regression and holdout for this exact candidate")
    if (regression.get("candidate_version") != paths["candidate"] or holdout.get("version") != paths["candidate"]
            or any(report.get("skill_name") != paths["name"] for report in (regression, holdout))):
        raise ValueError("Evidence version/skill mismatch")
    previous_digest = digest_tree(paths["old_skill"])
    if regression.get("previous_digest") != previous_digest:
        raise ValueError("Previous Skill has changed since regression")
    regression_digest = digest_json({v: digest_tree(paths["regression"] / v) for v in (paths["previous"], paths["candidate"])})
    holdout_summary = read_json(paths["holdout"] / "summary.json")
    if (regression.get("evidence_digest") != regression_digest or
            holdout.get("evidence_digest") != digest_tree(paths["holdout"] / "raw") or
            holdout.get("summary_digest") != digest_json(holdout_summary)):
        raise ValueError("Evaluation evidence has changed; rerun validation stages")
    previous_summary = read_json(paths["regression"] / paths["previous"] / "summary.json")
    candidate_summary = read_json(paths["regression"] / paths["candidate"] / "summary.json")
    if not compare(previous_summary, candidate_summary, config["regression"])["passed"]:
        raise ValueError("Regression does not pass current policy")
    from .aggregate import aggregate_all
    current_holdout = aggregate_all(paths["holdout"] / "raw", config["aggregation"]["gates"])
    current_dev = aggregate_all(paths["regression"] / paths["candidate"] / "raw", config["aggregation"]["gates"])
    if not current_holdout["passed"] or not current_dev["passed"]:
        raise ValueError("Candidate fails current quality gates")
    review_required = config.get("release", {}).get("require_expert_review", True)
    expert_passed = validate_feedback(config) if review_required else None
    if review_required and not expert_passed:
        raise ValueError("Complete, matching, approved expert review is required")
    destination = paths["release"]
    if destination.exists():
        raise FileExistsError(f"Release already exists: {destination}")
    result = {"skill_name": paths["name"], "version": paths["candidate"],
              "released_at": datetime.now(timezone.utc).isoformat(), "candidate_digest": candidate_digest,
              "validation_passed": True, "regression_passed": True, "holdout_passed": True,
              "expert_review_passed": expert_passed, "expert_review_required": review_required,
              "regression_digest": digest_json(regression), "holdout_digest": digest_json(holdout)}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        from pathlib import Path
        package = Path(temporary) / paths["name"]
        shutil.copytree(paths["new_skill"], package)
        write_json(package / "release.json", result)
        package.rename(destination)
    return result


def main():
    print(release(cli_config()))


if __name__ == "__main__":
    main()
