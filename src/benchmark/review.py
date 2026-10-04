"""Small static expert-review exporter; approval is always supplied by a human."""
import html
import json
import os
import re
import shutil
import subprocess
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
            f'<title>{html.escape(title)}</title>',
            """<style>
body{font:15px/1.5 system-ui,sans-serif;max-width:1500px;margin:auto;padding:24px;color:#222}
h1,h2,h3{line-height:1.2}.case{border-top:3px solid #555;margin-top:36px;padding-top:12px}
.context{display:grid;grid-template-columns:1fr 1fr;gap:16px}.runs{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:16px}
.panel,.run{border:1px solid #bbb;border-radius:8px;padding:14px;min-width:0}.run{border-top:5px solid #3973ac}
.meta{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px}.tag{background:#eee;border-radius:12px;padding:2px 8px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f6f6f6;padding:12px;border-radius:6px;max-height:36rem;overflow:auto}
details{margin-top:10px}iframe{width:100%;height:32rem;border:1px solid #ccc}img{max-width:100%;height:auto}
@media(max-width:850px){.context{grid-template-columns:1fr}.runs{grid-template-columns:1fr}}
</style>""",
            f'<h1>{html.escape(title)}</h1><p>{html.escape(instruction)}</p>']


def _safe_model_name(model):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", model.replace("/", "__"))


def _model_metadata(config, storage_name):
    for item in config.get("models", []):
        if _safe_model_name(item.get("model", "")) == storage_name:
            return {"configured_model": item.get("model"), "provider": item.get("provider")}
    return {"configured_model": storage_name, "provider": None}


def _read_optional_json(path):
    return read_json(path) if path.is_file() else None


def _artifact_html(path, root, label=None):
    """Render existing evidence inline while retaining a link to the source file."""
    path = Path(path)
    relative = os.path.relpath(path.resolve(), root.resolve())
    from urllib.parse import quote
    href = html.escape(quote(relative))
    title = html.escape(label or path.name)
    if not path.is_file():
        return f'<p>{title}: <em>missing</em></p>'
    extension = path.suffix.lower()
    if extension in {".txt", ".md", ".json", ".yaml", ".yml", ".toml", ".csv", ".xml", ".html", ".sql", ".py", ".js", ".ts", ".ini", ".cfg", ".sh"}:
        content = html.escape(path.read_text(encoding="utf-8"))
        return f'<h4><a href="{href}">{title}</a></h4><pre>{content}</pre>'
    if extension == ".svg":
        source = html.escape(path.read_text(encoding="utf-8"))
        return f'<h4><a href="{href}">{title}</a></h4><img src="{href}" alt="{title}"><details><summary>SVG source</summary><pre>{source}</pre></details>'
    if extension in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        return f'<h4><a href="{href}">{title}</a></h4><img src="{href}" alt="{title}">'
    if extension == ".pdf":
        return f'<h4><a href="{href}">{title}</a></h4><iframe src="{href}" title="{title}"></iframe>'
    return f'<p><a href="{href}">{title}</a> ({html.escape(extension or "file")})</p>'


def _files_html(paths, root):
    parts = []
    for value in paths:
        path = Path(value)
        if path.is_dir():
            files = [item for item in sorted(path.rglob("*")) if item.is_file()]
            parts.extend(_artifact_html(item, root, str(item.relative_to(path))) for item in files)
        else:
            parts.append(_artifact_html(path, root))
    return "".join(parts) or "<p><em>No files</em></p>"


def review_evidence(config):
    from .regression import regression_report_path
    paths = lifecycle_paths(config)
    regression = read_json(regression_report_path(config, paths["previous"], paths["candidate"]))
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
            case = cases[case_id]
            model_metadata = _model_metadata(config, model)
            rows.append({"split": split, **run, **model_metadata, "outputs": str(directory / "workspace/outputs"),
                         "prompt": case.prompt,
                         "inputs": [str(Path(case.file_path).parent / value) for value in case.inputs],
                         "usage": _read_optional_json(directory / "usage.json"),
                         "checks": str(directory / "checks.json"), "evaluation": str(directory / "evaluation.json"),
                         "reference": [str(Path(case.file_path).parent / value) for value in case.reference],
                         "previous_outputs": str(previous / "workspace/outputs") if split == "dev" else None})
    return fingerprint, rows


def export_markdown_review(config, rows, root):
    """Export every model run, its metrics and readable artifact content."""
    from urllib.parse import quote
    from .evaluator import artifact_text
    russian = config.get("output_language", "ru") == "ru"
    paths = lifecycle_paths(config)
    lines = ["# " + ("Результаты моделей" if russian else "Model results"), "",
             f"Skill: {paths['name']}", "",
             ("Версия Skill: " if russian else "Skill version: ") + paths['candidate'], "",
             f"Judge (config): {config['judge']['model']} / {config['judge'].get('provider', 'openrouter')}", "",
             ("Неизвестные метрики обозначены —. Оценка judge отсутствует, если запуск ещё не оценён."
              if russian else "Unknown metrics are shown as —. Judge scores are absent for unevaluated runs."), ""]
    def cell(value):
        return str(value if value is not None else "—").replace("|", r"\|").replace("\n", "<br>")
    for row in rows:
        usage = row.get("usage") or {}
        evaluation = _read_optional_json(Path(row["evaluation"])) or {}
        checks = _read_optional_json(Path(row["checks"])) or {}
        model = usage.get("model") or row.get("configured_model") or row["model"]
        lines += [f"## {row['split']} / {row['case_id']} / {model} / {row['run_id']}", "",
                  "| " + ("Метрика | Значение" if russian else "Metric | Value") + " |", "| --- | --- |"]
        metrics = {"skill_version": paths["candidate"], "provider": row.get("provider"), "execution_mode": usage.get("execution_mode"),
                   "total_score": evaluation.get("total_score"), "max_score": evaluation.get("max_score"),
                   "checks_passed": checks.get("passed"), "checks_failed": checks.get("failed")}
        metrics.update({key: usage.get(key) for key in
                        ("input_tokens", "output_tokens", "latency_seconds", "cost_usd")})
        lines.extend(f"| {key} | {cell(value)} |" for key, value in metrics.items())
        lines += ["", "### " + ("Результаты judge по критериям" if russian else "Judge criterion scores"), "",
                  "| criterion | score | max_score | reasoning |", "| --- | --- | --- | --- |"]
        for criterion in evaluation.get("criteria", []):
            lines.append("| " + " | ".join(cell(criterion.get(key)) for key in
                                            ("name", "score", "max_score", "reasoning")) + " |")
        lines += ["", "### " + ("Проверки" if russian else "Checks"), "",
                  "| name | passed | severity | evidence |", "| --- | --- | --- | --- |"]
        for check in checks.get("checks", []):
            lines.append("| " + " | ".join(cell(check.get(key)) for key in
                                            ("name", "passed", "severity", "evidence")) + " |")
        lines += ["", "### " + ("Ответ модели" if russian else "Model answer"), ""]
        outputs = Path(row["outputs"])
        files = sorted(path for path in outputs.rglob("*") if path.is_file())
        if not files:
            lines += ["Результаты отсутствуют" if russian else "No outputs", ""]
        for path in files:
            href = quote(os.path.relpath(path.resolve(), root.resolve()))
            lines += [f"#### [{path.relative_to(outputs)}]({href})", ""]
            try:
                if path.suffix.lower() == ".docx":
                    import zipfile
                    import xml.etree.ElementTree as ET
                    with zipfile.ZipFile(path) as archive:
                        document = ET.fromstring(archive.read("word/document.xml"))
                    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                    content = "\n\n".join("".join(p.itertext()) for p in document.findall(".//w:p", ns))
                else:
                    content = artifact_text(path)
                fence = "`" * max(3, max((len(m.group()) + 1 for m in re.finditer(r"`+", content)), default=3))
                lines += [fence + "text", content, fence, ""]
            except (OSError, ValueError, KeyError, RuntimeError) as exc:
                lines += [str(exc), ""]
        error = _read_optional_json(outputs.parents[1] / "error.json")
        if error:
            lines += ["```json", json.dumps(error, ensure_ascii=False, indent=2), "```", ""]
    report = root / "result.md"
    root.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def export_review(config):
    paths = lifecycle_paths(config)
    fingerprint, rows = review_evidence(config)
    root = paths["review"]
    identities = [{k: row[k] for k in ("split", "case_id", "model", "run_id")} for row in rows]
    feedback = prepare_feedback(root, fingerprint, identities)
    write_json(root / "index.json", {"evidence_digest": fingerprint, "runs": rows})
    export_markdown_review(config, rows, root)
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
    grouped = {}
    for row in rows:
        grouped.setdefault((row["split"], row["case_id"]), []).append(row)
    labels = {"task": "Задача", "inputs": "Входные данные", "reference": "Эталон",
              "outputs": "Ответ модели", "previous": "Ответ предыдущей версии",
              "checks": "Проверки", "evaluation": "Оценка"} if russian else {
              "task": "Task", "inputs": "Inputs", "reference": "Reference",
              "outputs": "Model answer", "previous": "Previous version answer",
              "checks": "Checks", "evaluation": "Evaluation"}
    for (split, case_id), case_rows in grouped.items():
        first = case_rows[0]
        parts.append(f'<section class="case"><h2>{html.escape(split + "/" + case_id)}</h2>')
        parts.append('<div class="context"><div class="panel">')
        parts.append(f'<h3>{labels["task"]}</h3><pre>{html.escape(first["prompt"])}</pre>')
        parts.append(f'<h3>{labels["inputs"]}</h3>{_files_html(first["inputs"], root)}</div>')
        parts.append(f'<div class="panel"><h3>{labels["reference"]}</h3>{_files_html(first["reference"], root)}</div></div>')
        parts.append('<h3>' + ("Результаты моделей" if russian else "Model results") + '</h3><div class="runs">')
        for row in case_rows:
            usage = row.get("usage") or {}
            actual_model = usage.get("model") or row.get("configured_model") or row["model"]
            provider = row.get("provider") or "—"
            metadata = {key: value for key, value in usage.items() if key not in {"model", "created_files"} and value is not None}
            parts.append('<article class="run">')
            parts.append(f'<h3>{html.escape(str(actual_model))}</h3><div class="meta">'
                         f'<span class="tag">provider: {html.escape(str(provider))}</span>'
                         f'<span class="tag">run: {html.escape(row["run_id"])}</span></div>')
            if metadata:
                parts.append(f'<details><summary>Metadata</summary><pre>{html.escape(json.dumps(metadata, ensure_ascii=False, indent=2))}</pre></details>')
            parts.append(f'<h4>{labels["outputs"]}</h4>{_files_html([row["outputs"]], root)}')
            response = Path(row["outputs"]).parents[1] / "response.md"
            if response.is_file():
                parts.append(_artifact_html(response, root, "response.md"))
            if row.get("previous_outputs"):
                parts.append(f'<details><summary>{labels["previous"]}</summary>{_files_html([row["previous_outputs"]], root)}</details>')
            for key in ("checks", "evaluation"):
                parts.append(f'<details><summary>{labels[key]}</summary>{_files_html([row[key]], root)}</details>')
            parts.append('</article>')
        parts.append('</div></section>')
    parts.append('</html>')
    (root / "index.html").write_text("\n".join(parts), encoding="utf-8")
    return feedback


def open_in_vscode_simple_browser(page):
    """Ask the active VS Code instance to open the generated local review page."""
    executable = shutil.which("code")
    if not executable:
        print(f"VS Code CLI was not found; open {Path(page).resolve()} manually")
        return False
    command = [executable, "--reuse-window", "--open-url", Path(page).resolve().as_uri()]
    try:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"Could not open the review in VS Code Simple Browser: {exc}")
        print(f"Open {Path(page).resolve()} manually")
        return False
    return True


def validate_feedback(config):
    fingerprint, rows = review_evidence(config)
    identities = [{k: row[k] for k in ("split", "case_id", "model", "run_id")} for row in rows]
    return feedback_approved(lifecycle_paths(config)["review"] / "feedback.json", fingerprint, identities)


def optimization_evidence(config):
    paths = lifecycle_paths(config)
    report = read_json(paths["optimization"] / "optimization.json")
    candidate = paths["proposed_skill"] if paths["proposed_skill"].exists() else paths["new_skill"]
    if (report["candidate_digest"] != digest_tree(candidate) or
            report["source_digest"] != digest_tree(paths["old_skill"])):
        raise ValueError("Optimization files have changed; create a new proposal before review")
    identities = [{"change_id": f"change-{number:03d}"} for number, _ in enumerate(report["changes"], 1)]
    return digest_json(report), identities, report


def approve_optimization_feedback(config):
    """Approve every reviewed optimization change after validating its evidence."""
    paths = lifecycle_paths(config)
    fingerprint, identities, _ = optimization_evidence(config)
    feedback_path = paths["optimization"] / "feedback.json"
    if not feedback_path.is_file():
        raise FileNotFoundError(f"Missing optimization feedback: {feedback_path}")
    feedback = read_json(feedback_path)
    if not isinstance(feedback, dict):
        raise ValueError("Optimization feedback must be a JSON object")
    if feedback.get("evidence_digest") != fingerprint:
        raise ValueError("Optimization feedback belongs to different evidence")
    if feedback.get("status") not in {"pending", "complete"}:
        raise ValueError("Optimization feedback status must be pending or complete")
    reviews = feedback.get("reviews")
    if not isinstance(reviews, list) or len(reviews) != len(identities):
        raise ValueError("Optimization feedback must contain every proposed change exactly once")
    remaining = list(identities)
    for review in reviews:
        if (not isinstance(review, dict) or not isinstance(review.get("feedback"), str)
                or review.get("approved") is not None and type(review.get("approved")) is not bool):
            raise ValueError("Each optimization review requires a valid approved value and feedback string")
        for identity in remaining:
            if all(review.get(key) == value for key, value in identity.items()):
                remaining.remove(identity)
                break
        else:
            raise ValueError("Optimization feedback contains an unknown or duplicate change")
        review["approved"] = True
    if remaining:
        raise ValueError("Optimization feedback is missing proposed changes")
    feedback["status"] = "complete"
    write_json(feedback_path, feedback)
    if not feedback_approved(feedback_path, fingerprint, identities):
        raise RuntimeError("Optimization feedback approval could not be verified")
    return {"approved": True, "feedback": str(feedback_path), "reviews": len(reviews)}


def export_optimization_review(config):
    import difflib
    paths = lifecycle_paths(config)
    fingerprint, identities, report = optimization_evidence(config)
    root = paths["optimization"]
    feedback = prepare_feedback(root, fingerprint, identities)
    before = (paths["old_skill"] / "SKILL.md").read_text(encoding="utf-8")
    candidate = paths["proposed_skill"] if paths["proposed_skill"].exists() else paths["new_skill"]
    after = (candidate / "SKILL.md").read_text(encoding="utf-8")
    diff = "".join(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                     fromfile="previous/SKILL.md", tofile="candidate/SKILL.md"))
    russian = config.get("output_language", "ru") == "ru"
    title = "Проверка улучшений Skill" if russian else "Skill optimization review"
    instruction = ("Проверьте все изменения, затем выполните make approve и make optimize-apply. Частичное одобрение не применяется."
                   if russian else "Inspect every change, then run make approve and make optimize-apply. Partial approval is not applied.")
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
    config = cli_config()
    feedback = export_review(config)
    report = lifecycle_paths(config)["review"] / "result.md"
    print(f"Markdown review: {report}")
    print(feedback)


if __name__ == "__main__":
    main()
