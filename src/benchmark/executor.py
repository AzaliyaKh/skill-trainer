"""Execution contracts; adding a mode does not change benchmark orchestration."""
from pathlib import Path
from .case import Case

TEXT_EXTENSIONS = {
    ".txt",
    ".svg",
    ".md",
    ".py",

    ".c",
    ".h",
    ".cpp",
    ".hpp",

    ".java",

    ".js",
    ".ts",

    ".json",
    ".yaml",
    ".yml",
    ".toml",

    ".csv",

    ".xml",
    ".html",

    ".sql",

    ".sh",

    ".ini",
    ".cfg",
}


def _read_text_inputs(
    inputs_dir: Path,
) -> str:

    parts = []

    for path in sorted(
        inputs_dir.rglob("*")
    ):

        if not path.is_file():
            continue

        relative_path = (
            path.relative_to(
                inputs_dir
            )
        )

        if (
            path.suffix.lower()
            not in TEXT_EXTENSIONS
        ):
            raise RuntimeError(
                "Text execution cannot read "
                f"binary input: {relative_path}. "
                "Use execution.mode: agent "
                "for PDF, DOCX and other "
                "non-text files."
            )

        content = path.read_text(
            encoding="utf-8"
        )

        parts.append(
            f"""
INPUT FILE: {relative_path}

{content}
""".strip()
        )

    return "\n\n".join(parts)


def _build_text_prompt(
    case: Case,
    inputs_dir: Path,
) -> str:

    input_content = (
        _read_text_inputs(
            inputs_dir
        )
    )

    parts = [case.prompt.strip(), "\nReturn only the final artifact content, without Markdown fences unless the artifact is Markdown."]

    if input_content:
        parts.append(
            "\n\nINPUTS:\n\n"
            + input_content
        )

    return "".join(parts)


def _build_agent_prompt(
    case: Case,
) -> str:

    expected_outputs = []

    for output in (
        case.expected_outputs
    ):

        path = output.get("path")

        if path:
            expected_outputs.append(
                f"- outputs/{path}"
            )

    if expected_outputs:

        outputs_description = (
            "\n".join(
                expected_outputs
            )
        )

    else:

        outputs_description = (
            "- Save any generated files "
            "inside outputs/"
        )

    return f"""
TASK:

{case.prompt.strip()}

WORKSPACE:

The working directory contains:

inputs/
    Input files for the task.

outputs/
    Save final generated files here.

Do not modify the original input files.

EXPECTED OUTPUTS:

{outputs_description}

Skill resources are available under skill/.

Complete the task using the files and tools
available in the workspace.

Your final textual response should briefly
describe what was done.
""".strip()



class TextExecutor:
    def execute(self, provider, case, system, workspace, **settings):
        return provider.generate(system=system, prompt=_build_text_prompt(case, workspace / "inputs"), **settings)


class AgentExecutor:
    def execute(self, provider, case, system, workspace, **settings):
        if not getattr(provider, "supports_agent", False):
            raise ValueError("Agent execution requires an agent-capable provider")
        return provider.generate(system=system, prompt=_build_agent_prompt(case), workspace=str(workspace), **settings)


EXECUTORS = {"text": TextExecutor(), "agent": AgentExecutor()}


def register_executor(name, executor):
    if name in EXECUTORS:
        raise ValueError(f"Executor already registered: {name}")
    EXECUTORS[name] = executor
