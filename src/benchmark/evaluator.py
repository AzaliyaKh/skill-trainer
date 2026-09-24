import json
from dataclasses import dataclass
from typing import List
from pathlib import Path

from .case import Case, Rubric

@dataclass
class CriterionScore:
    name: str
    score: int
    max_score: int
    reasoning: str


@dataclass
class EvaluationResult:
    criteria_scores: List[CriterionScore]
    total_score: int
    max_score: int


def _load_reference(case: Case) -> str:
    case_dir = Path(case.file_path).parent

    parts = []

    for relative_path in case.reference:
        file_path = case_dir / relative_path

        content = artifact_text(file_path)

        parts.append(
            f"REFERENCE FILE: {relative_path}\n\n{content}"
        )

    return "\n\n".join(parts)


def _build_system_prompt(rubric: Rubric) -> str:
    lines = [
        "You are an objective evaluator. Score the provided output strictly using the rubric below.",
        "Do not reward or penalise based on your own preferences. Only the rubric criteria matter.",
        "",
    ]

    if rubric.context:
        lines += ["CONTEXT:", rubric.context.strip(), ""]

    lines += [f"RUBRIC ({len(rubric.criteria)} criteria, {rubric.total} pts total)", ""]

    for criterion in rubric.criteria:
        lines.append(f"### {criterion.name} (max {criterion.points} pts)")
        for level in criterion.levels:
            lines.append(f"  {level.range[0]}-{level.range[1]} pts: {level.description}")
        lines.append("")

    names = [c.name for c in rubric.criteria]
    lines += [
        "---",
        "OUTPUT FORMAT",
        "",
        "Return raw JSON only.",
        'Schema: {"criteria": [{"name": "<criterion name>", "score": <integer>, "reasoning": "<one sentence>"}]}',
        "",
        "Requirements:",
        f"- Exactly {len(names)} entries, in this order: " + ", ".join(names),
        "- Each score must fit the criterion range.",
        "- Each name must match exactly.",
    ]
    return "\n".join(lines)


def _parse_response(raw: str, rubric: Rubric) -> EvaluationResult:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]

    data = json.loads(text.strip())
    criteria_map = {c.name: c for c in rubric.criteria}
    scores = []

    entries = data["criteria"]
    if not isinstance(entries, list) or [e.get("name") for e in entries] != list(criteria_map):
        raise ValueError("Judge must return each rubric criterion exactly once, in order")
    for entry in entries:
        ref = criteria_map[entry["name"]]
        score = entry["score"]
        if type(score) is not int or not 0 <= score <= ref.points:
            raise ValueError(f"Invalid score for {ref.name}: {score}")
        if not isinstance(entry.get("reasoning"), str) or not entry["reasoning"].strip():
            raise ValueError(f"Missing reasoning for {ref.name}")
        scores.append(CriterionScore(ref.name, score, ref.points, entry["reasoning"]))

    return EvaluationResult(
        criteria_scores=scores,
        total_score=sum(item.score for item in scores),
        max_score=rubric.total,
    )


def evaluate_output(
    provider,
    judge_model: str,
    judge_temperature: float,
    judge_max_tokens: int,
    case: Case,
    output: str,
    outputs_dir: str | None = None,
    output_language: str = "ru",
    max_artifact_chars: int = 200000,
) -> EvaluationResult:

    reference = _load_reference(case)
    if outputs_dir is not None:
        output = artifact_text(Path(outputs_dir))
    if len(output) + len(reference) > max_artifact_chars:
        raise ValueError("Artifact evidence exceeds configured max_artifact_chars; refusing silent truncation")

    prompt = f"""
TASK:

{case.prompt}

REQUESTED OUTPUT LANGUAGE: {case.output_language or output_language}

REFERENCE SOLUTION:

{reference}

OUTPUT TO EVALUATE:

{output}
""".strip()

    response = provider.generate(
        model=judge_model,
        temperature=judge_temperature,
        max_tokens=judge_max_tokens,
        system=_build_system_prompt(case.rubric),
        prompt=prompt,
    )

    return _parse_response(
        response.output,
        case.rubric,
    )


ARTIFACT_READERS = {}


def artifact_text(path: Path) -> str:
    """Extract actual evidence; unsupported formats fail instead of judging filenames."""
    import zipfile
    import xml.etree.ElementTree as ET
    from .executor import TEXT_EXTENSIONS
    if path.is_symlink():
        raise ValueError(f"Symlink artifact: {path}")
    if path.is_dir():
        return "\n\n".join(f"FILE: {p.relative_to(path)}\n{artifact_text(p)}"
                            for p in sorted(path.rglob("*")) if p.is_file() or p.is_symlink())
    extension = path.suffix.lower()
    if extension in ARTIFACT_READERS:
        return ARTIFACT_READERS[extension](path)
    if extension in TEXT_EXTENSIONS:
        return path.read_text(encoding="utf-8")
    if extension == ".pdf":
        from pypdf import PdfReader
        text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
        if not text.strip():
            raise ValueError("PDF has no extractable text; register an OCR/visual artifact reader")
        return text
    if extension in (".docx", ".xlsx"):
        with zipfile.ZipFile(path) as archive:
            names = (["word/document.xml"] if extension == ".docx" else
                     sorted(n for n in archive.namelist() if n.startswith("xl/") and n.endswith(".xml")))
            return "\n".join(ET.tostring(ET.fromstring(archive.read(name)), encoding="unicode") for name in names)
    raise ValueError(f"No semantic artifact reader for {extension}; register ARTIFACT_READERS handler")
