"""Small static expert-review exporter; approval is always supplied by a human."""
import html
import os
from pathlib import Path

from .case import load_all_cases
from .storage import cli_config, lifecycle_paths, read_json, write_json, digest_tree, digest_json


def prepare_feedback(root, fingerprint, identities):
    """Shared pending-feedback contract for optimization and expert review."""
    root.mkdir(parents=True, exist_ok=True)
    feedback = root / "feedback.json"
    if feedback.exists():
        if read_json(feedback).get("evidence_digest") != fingerprint:
            raise ValueError("Existing feedback belongs to different evidence")
    else:
        write_json(feedback, {"status": "pending", "evidence_digest": fingerprint,
                              "reviews": [dict(identity, approved=None, feedback="") for identity in identities]})
    return feedback


def feedback_approved(path, fingerprint, identities):
    if not path.is_file() or not identities:
        return False
    feedback = read_json(path)
    if feedback.get("status") != "complete" or feedback.get("evidence_digest") != fingerprint:
        return False
    reviews = feedback.get("reviews")
    if not isinstance(reviews, list) or len(reviews) != len(identities):
        return False
    remaining = list(identities)
    for review in reviews:
        if not isinstance(review, dict) or review.get("approved") is not True or not isinstance(review.get("feedback"), str):
            return False
        for identity in remaining:
            if all(review.get(key) == value for key, value in identity.items()):
                remaining.remove(identity)
                break
        else:
            return False
    return not remaining


def page_header(config, title, instruction):
    return [f'<!doctype html><html lang="{html.escape(config.get("output_language", "ru"))}"><meta charset="utf-8">',
            f'<title>{html.escape(title)}</title><h1>{html.escape(title)}</h1><p>{html.escape(instruction)}</p>']


def review_evidence(config):
    paths = lifecycle_paths(config)
    regression = read_json(paths["regression"] / "regression.json")
    holdout = read_json(paths["holdout"] / "holdout.json")
    fingerprint = digest_json({"candidate": digest_tree(paths["new_skill"]), "regression": regression, "holdout": holdout})
    rows = []
    for split, root, dataset in (
        ("dev", paths["regression"] / paths["candidate"] / "raw", config["dataset"]["path"]),
        ("holdout", paths["holdout"] / "raw", config["dataset"]["holdout_path"]),
    ):
        cases = {case.id: case for case in load_all_cases(dataset)}
        for run in read_json(root / "manifest.json")["expected_runs"]:
            case_id, model, run_id = run["case_id"], run["model"], run["run_id"]
            directory = root / case_id / model / run_id
            previous = paths["regression"] / paths["previous"] / "raw" / case_id / model / run_id
            rows.append({"split": split, **run, "outputs": str(directory / "workspace/outputs"),
                         "checks": str(directory / "checks.json"), "evaluation": str(directory / "evaluation.json"),
                         "reference": [str(Path(cases[case_id].file_path).parent / r) for r in cases[case_id].reference],
                         "previous_outputs": str(previous / "workspace/outputs") if split == "dev" else None})
    return fingerprint, rows


def export_review(config):
    paths = lifecycle_paths(config)
    fingerprint, rows = review_evidence(config)
    root = paths["review"]
    identities = [{k: row[k] for k in ("split", "case_id", "model", "run_id")} for row in rows]
    feedback = prepare_feedback(root, fingerprint, identities)
    write_json(root / "index.json", {"evidence_digest": fingerprint, "runs": rows})
    russian = config.get("output_language", "ru") == "ru"
    title = "Экспертная проверка" if russian else "Expert review"
    instruction = ("Откройте результаты и эталоны. В feedback.json заполните approved и feedback для каждого запуска, затем установите status: complete."
                   if russian else "Inspect outputs and references. Fill approved and feedback for every run in feedback.json, then set status to complete.")
    parts = page_header(config, title, instruction)
    def link(path):
        path = Path(path)
        relative = os.path.relpath(path.resolve(), root.resolve())
        from urllib.parse import quote
        return f'<a href="{html.escape(quote(relative))}">{html.escape(path.name)}</a>'
    parts.append("<p>" + ("Версии Skill: " if russian else "Skill versions: ") +
                 link(paths["old_skill"] / "SKILL.md") + " / " + link(paths["new_skill"] / "SKILL.md") + "</p>")
    labels = {"outputs": "Результаты", "previous_outputs": "Предыдущая версия", "checks": "Проверки", "evaluation": "Оценка", "reference": "Эталон"}
    for row in rows:
        parts.append(f'<h2>{html.escape(row["split"] + "/" + row["case_id"] + "/" + row["model"] + "/" + row["run_id"])}</h2><ul>')
        for key in ("outputs", "previous_outputs", "checks", "evaluation", "reference"):
            value = row[key]
            if not value:
                continue
            files = value if isinstance(value, list) else [value]
            links = []
            for file in files:
                path = Path(file)
                links.extend(link(p) for p in sorted(path.rglob("*")) if p.is_file()) if path.is_dir() else links.append(link(path))
            parts.append(f'<li>{labels[key] if russian else key}: {", ".join(links)}</li>')
        parts.append('</ul>')
    parts.append('</html>')
    (root / "index.html").write_text("\n".join(parts), encoding="utf-8")
    return feedback


def validate_feedback(config):
    fingerprint, rows = review_evidence(config)
    identities = [{k: row[k] for k in ("split", "case_id", "model", "run_id")} for row in rows]
    return feedback_approved(lifecycle_paths(config)["review"] / "feedback.json", fingerprint, identities)


def optimization_evidence(config):
    paths = lifecycle_paths(config)
    report = read_json(paths["optimization"] / "optimization.json")
    if (report["candidate_digest"] != digest_tree(paths["new_skill"]) or
            report["source_digest"] != digest_tree(paths["old_skill"])):
        raise ValueError("Optimization files have changed; create a new proposal before review")
    identities = [{"change_id": f"change-{number:03d}"} for number, _ in enumerate(report["changes"], 1)]
    return digest_json(report), identities, report


def export_optimization_review(config):
    import difflib
    paths = lifecycle_paths(config)
    fingerprint, identities, report = optimization_evidence(config)
    root = paths["optimization"]
    feedback = prepare_feedback(root, fingerprint, identities)
    before = (paths["old_skill"] / "SKILL.md").read_text(encoding="utf-8")
    after = (paths["new_skill"] / "SKILL.md").read_text(encoding="utf-8")
    diff = "".join(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                     fromfile="previous/SKILL.md", tofile="candidate/SKILL.md"))
    russian = config.get("output_language", "ru") == "ru"
    title = "Проверка улучшений Skill" if russian else "Skill optimization review"
    instruction = ("Проверьте изменения. В feedback.json заполните approved и feedback для каждого изменения, затем установите status: complete. "
                   "После одобрения всего кандидата выполните make optimize-apply. Частичное одобрение не применяется."
                   if russian else "Inspect the proposal. Fill approved and feedback for every change in feedback.json, then set status to complete. "
                   "After approving the complete candidate, run make optimize-apply. Partial approval is not applied.")
    parts = page_header(config, title, instruction)
    parts.append('<p>' + ("Файл для применения: " if russian else "Apply target: ") + html.escape(report["apply_path"]) + '</p>')
    parts.append('<p><a href="feedback.json">feedback.json</a> · <a href="optimization.json">optimization.json</a></p>')
    for identity, change in zip(identities, report["changes"]):
        parts.append('<h2>' + identity["change_id"] + '</h2><dl>')
        for key, label in (("problem", "Проблема"), ("change", "Изменение"), ("reason", "Обоснование")):
            parts.append(f'<dt>{label if russian else key}</dt><dd>{html.escape(change[key])}</dd>')
        parts.append('</dl>')
    for title, content in (("Diff", diff), ("Исходный Skill" if russian else "Previous Skill", before),
                           ("Предложенный Skill" if russian else "Proposed Skill", after)):
        parts.append(f'<h2>{title}</h2><pre>{html.escape(content)}</pre>')
    parts.append('</html>')
    (root / "index.html").write_text("\n".join(parts), encoding="utf-8")
    write_json(root / "index.json", {"evidence_digest": fingerprint, "changes": identities,
                                     "apply_path": report["apply_path"], "diff": diff})
    return feedback


def main():
    print(export_review(cli_config()))


if __name__ == "__main__":
    main()
