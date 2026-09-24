import re
import shutil
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List

from .case import Case, checked_path
from .checks import run_checks
from .evaluator import evaluate_output
from .executor import EXECUTORS
from .storage import write_json, digest_tree


_FRONTMATTER_RE = re.compile(
    r"^---\s*\n.*?\n---\s*\n",
    re.DOTALL,
)


@dataclass
class RunResult:
    output: str

    model: str
    case_id: str
    run_id: int

    input_tokens: int | None
    output_tokens: int | None
    cost: float | None

    latency: float

    execution_mode: str

    workspace_dir: str
    outputs_dir: str

    created_files: List[str]


def strip_frontmatter(
    content: str,
) -> str:

    return _FRONTMATTER_RE.sub(
        "",
        content,
        count=1,
    ).lstrip()


def _prepare_workspace(
    case: Case,
    run_dir: Path,
) -> tuple[Path, Path, Path]:

    workspace_dir = (
        run_dir / "workspace"
    )

    inputs_dir = (
        workspace_dir / "inputs"
    )

    outputs_dir = (
        workspace_dir / "outputs"
    )

    if workspace_dir.exists():
        raise FileExistsError(f"Refusing to overwrite workspace: {workspace_dir}")

    inputs_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    outputs_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    case_dir = Path(
        case.file_path
    ).parent

    for relative_path in case.inputs:

        source = checked_path(case_dir, relative_path)

        if not source.exists():
            raise FileNotFoundError(
                f"Input not found: {source}"
            )

        destination = (
            inputs_dir
            / Path(relative_path).relative_to("inputs")
        )

        destination.parent.mkdir(parents=True, exist_ok=True)

        if source.is_dir():

            shutil.copytree(
                source,
                destination,
                dirs_exist_ok=True,
            )

        else:

            shutil.copy2(source, destination)

    return (
        workspace_dir,
        inputs_dir,
        outputs_dir,
    )


def _collect_created_files(
    outputs_dir: Path,
) -> List[str]:

    files = []

    for path in sorted(
        outputs_dir.rglob("*")
    ):

        if path.is_symlink():
            raise ValueError(f"Output symlink is not allowed: {path}")
        if path.is_file():

            files.append(
                str(
                    path.relative_to(
                        outputs_dir
                    )
                )
            )

    return files


def run_case(
    provider,
    model: str,
    temperature: float,
    max_tokens: int,

    case: Case,
    skill_content: str,

    run_id: int,
    run_dir: Path,
    output_language: str = "ru",
    skill_dir: Path | None = None,
) -> RunResult:

    execution_mode = (
        case.execution.get(
            "mode",
            "text",
        )
    )

    if execution_mode not in EXECUTORS:
        raise ValueError(f"Unsupported execution mode: {execution_mode}")

    (
        workspace_dir,
        inputs_dir,
        outputs_dir,
    ) = _prepare_workspace(
        case,
        run_dir,
    )

    system = strip_frontmatter(
        skill_content
    )

    language = case.output_language or output_language
    system += f"\n\nOUTPUT LANGUAGE: {language}\nUse this language for all user-facing text, tables, captions and diagram labels."
    if skill_dir is not None:
        digest_tree(skill_dir)  # Reject linked resources before copying.
        shutil.copytree(skill_dir, workspace_dir / "skill")
        if execution_mode == "text":
            from .executor import _read_text_inputs
            references = workspace_dir / "skill" / "references"
            if references.exists():
                system += "\nSKILL RESOURCES:\n" + _read_text_inputs(references)
    response = EXECUTORS[execution_mode].execute(
        provider, case, system, workspace_dir,
        model=model, temperature=temperature, max_tokens=max_tokens,
    )
    write_json(run_dir / "usage.json", {
        "model": response.model, "execution_mode": execution_mode,
        "input_tokens": response.input_tokens, "output_tokens": response.output_tokens,
        "cost_usd": response.cost, "latency_seconds": response.latency,
        "created_files": [], "output_language": language,
    })
    if getattr(response, "transcript", None) is not None:
        write_json(run_dir / "transcript.json", {"events": response.transcript})

    answer_path = (
        checked_path(outputs_dir, case.execution.get("output_path", "answer.md"))
    )

    answer_path.parent.mkdir(parents=True, exist_ok=True)
    if execution_mode == "text" or not answer_path.exists():
        answer_path.write_text(response.output, encoding="utf-8")
    else:
        (run_dir / "response.md").write_text(response.output, encoding="utf-8")

    created_files = (
        _collect_created_files(
            outputs_dir
        )
    )

    from .storage import read_json
    usage = read_json(run_dir / "usage.json")
    usage["created_files"] = created_files
    write_json(run_dir / "usage.json", usage)

    from .executor import TEXT_EXTENSIONS
    checked_output = answer_path.read_text(encoding="utf-8") if answer_path.suffix.lower() in TEXT_EXTENSIONS else response.output
    return RunResult(
        output=checked_output,

        model=response.model,
        case_id=case.id,
        run_id=run_id,

        input_tokens=
            response.input_tokens,

        output_tokens=
            response.output_tokens,

        cost=response.cost,

        latency=response.latency,

        execution_mode=
            execution_mode,

        workspace_dir=str(
            workspace_dir
        ),

        outputs_dir=str(
            outputs_dir
        ),

        created_files=
            created_files,
    )


def run_and_evaluate(config, model_config, case, skill_content, skill_dir, run_id, run_dir, get_provider):
    """Own one run: generation, persisted checks, judge, and recoverable errors."""
    run_dir.mkdir(parents=True)
    model = model_config["model"]
    stage = "generation"
    try:
        result = run_case(
            provider=get_provider(model_config["provider"]), model=model,
            **config["generation"], case=case, skill_content=skill_content,
            run_id=run_id, run_dir=run_dir, skill_dir=skill_dir,
            output_language=config.get("output_language", "ru"),
        )
        stage = "checks"
        checks = run_checks(result.output, case.checks, result.outputs_dir, case.expected_outputs)
        write_json(run_dir / "checks.json", checks)
        stage = "evaluation"
        judge = config["judge"]
        evaluation = evaluate_output(
            provider=get_provider(judge.get("provider", "openrouter")), judge_model=judge["model"],
            judge_temperature=judge["temperature"], judge_max_tokens=judge["max_tokens"],
            case=case, output=result.output, outputs_dir=result.outputs_dir,
            output_language=config.get("output_language", "ru"),
            max_artifact_chars=judge.get("max_artifact_chars", 200000),
        )
        write_json(run_dir / "evaluation.json", {
            "criteria": [asdict(item) for item in evaluation.criteria_scores],
            "total_score": evaluation.total_score, "max_score": evaluation.max_score,
        })
        write_json(run_dir / "status.json", {"status": "complete"})
    except Exception as exc:
        error = {"stage": stage, "error": f"{type(exc).__name__}: {exc}"}
        write_json(run_dir / "error.json", error)
        print(f"Failed {stage}: {exc}")
        return error
    return None
