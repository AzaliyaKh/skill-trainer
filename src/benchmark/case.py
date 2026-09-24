from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple

import yaml


@dataclass
class RubricLevel:
    range: Tuple[int, int]
    description: str


@dataclass
class RubricCriterion:
    name: str
    points: int
    levels: List[RubricLevel]


@dataclass
class Rubric:
    context: str
    criteria: List[RubricCriterion]

    @property
    def total(self) -> int:
        return sum(c.points for c in self.criteria)


@dataclass
class Case:
    id: str
    name: str
    category: str
    severity: str

    prompt: str

    inputs: List[str]
    reference: List[str]

    checks: dict
    rubric: Rubric

    file_path: str
    execution: dict = field(default_factory=lambda: {"mode": "text"})
    expected_outputs: list = field(default_factory=list)
    output_language: str | None = None


def load_case(file_path: str) -> Case:
    path = Path(file_path)
    if path.is_symlink():
        raise ValueError(f"Symlink case file: {path}")
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    validate_case_data(data, path)
    rubric_data = data["rubric"]
    criteria = []
    for c in rubric_data["criteria"]:
        levels = [
            RubricLevel(range=tuple(level["range"]), description=level["description"])
            for level in c.get("levels", [])
        ]
        criteria.append(
            RubricCriterion(name=c["name"], points=c["points"], levels=levels)
        )

    return Case(
        id=data["id"],
        name=data["name"],
        category=data.get("category", ""),
        severity=data.get("severity", ""),
        prompt=data["prompt"],
        inputs=data.get("inputs", []),
        reference=data.get("reference", []),
        checks=data.get("checks", {}),
        rubric=Rubric(
            context=rubric_data.get("context", ""),
            criteria=criteria,
        ),
        file_path=str(path.resolve()),
        execution=data.get("execution", {"mode": "text"}),
        expected_outputs=data.get("expected_outputs", []),
        output_language=data.get("output_language"),
    )


def load_all_cases(dataset_dir: str) -> List[Case]:
    cases = []
    for case_dir in sorted(Path(dataset_dir).iterdir()):
        if not case_dir.is_dir():
            continue
        case_file = case_dir / "case.yaml"
        if not case_file.exists():
            raise ValueError(f"Missing case.yaml: {case_dir}")
        cases.append(load_case(str(case_file)))
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate case IDs")
    if not cases:
        raise ValueError(f"No cases in {dataset_dir}")
    return cases


def safe_relative(value: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"Invalid relative path: {value!r}")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path == Path("."):
        raise ValueError(f"Unsafe relative path: {value}")
    return path


def checked_path(root: Path, value: str) -> Path:
    path = root / safe_relative(value)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Path escapes root: {value}")
    for parent in (path, *path.parents):
        if parent == root.parent:
            break
        if parent.is_symlink():
            raise ValueError(f"Symlinks are not allowed: {path}")
    return path


def validate_case_data(data: dict, path: Path) -> None:
    import re
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in {path}")
    for key in ("id", "name", "prompt"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"Missing or invalid {key}: {path}")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", data["id"]):
        raise ValueError("Unsafe case ID")
    execution = data.get("execution", {"mode": "text"})
    if not isinstance(execution, dict) or not isinstance(execution.get("mode"), str):
        raise ValueError("execution.mode must be a string")
    if "output_path" in execution:
        safe_relative(execution["output_path"])
    language = data.get("output_language")
    if language is not None and (not isinstance(language, str) or not language.strip()):
        raise ValueError("Invalid output_language")
    for group in ("inputs", "reference"):
        entries = data.get(group, [])
        if not isinstance(entries, list):
            raise ValueError(f"{group} must be a list")
        normalized = [safe_relative(value) for value in entries]
        for index, first in enumerate(normalized):
            for second in normalized[index + 1:]:
                if first == second or first in second.parents or second in first.parents:
                    raise ValueError(f"Overlapping {group} paths: {first}, {second}")
        for value in entries:
            relative = safe_relative(value)
            if relative.parts[0] != group:
                raise ValueError(f"{group} paths must be inside {group}/")
            source = checked_path(path.parent, value)
            if not source.exists():
                raise ValueError(f"Missing {group}: {source}")
            for child in source.rglob("*") if source.is_dir() else []:
                if child.is_symlink():
                    raise ValueError(f"Symlinks are not allowed: {child}")
    outputs = data.get("expected_outputs", [])
    if not isinstance(outputs, list):
        raise ValueError("expected_outputs must be a list")
    for output in outputs:
        safe_relative(output["path"])
        if not isinstance(output.get("required", True), bool):
            raise ValueError("Output required must be boolean")
    if len({o["path"] for o in outputs}) != len(outputs):
        raise ValueError("Duplicate expected output paths")
    checks = data.get("checks", {})
    if not isinstance(checks, (dict, list)):
        raise ValueError("checks must be a mapping or list")
    from .checks import normalize_checks, HANDLERS
    for spec in normalize_checks(checks):
        if not isinstance(spec, dict) or spec.get("type") not in HANDLERS:
            raise ValueError(f"Unknown or invalid check: {spec}")
        if "path" in spec:
            safe_relative(spec["path"])
        if spec["type"] == "contains" and not isinstance(spec.get("text"), str):
            raise ValueError("contains requires text")
        if spec["type"] in {"file_exists", "pdf_valid", "docx_valid"} and "path" not in spec:
            raise ValueError("File check requires path")
    criteria = data.get("rubric", {}).get("criteria", [])
    if not criteria:
        raise ValueError("Rubric requires criteria")
    names = []
    for criterion in criteria:
        name, points = criterion["name"], criterion["points"]
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Invalid criterion name")
        if type(points) is not int or points <= 0:
            raise ValueError("Criterion points must be a positive integer")
        names.append(name)
        for level in criterion.get("levels", []):
            bounds = level["range"]
            if len(bounds) != 2 or any(type(v) is not int for v in bounds) or not 0 <= bounds[0] <= bounds[1] <= points:
                raise ValueError("Invalid rubric level range")
    if len(set(names)) != len(names):
        raise ValueError("Duplicate rubric criteria")


def main():
    import argparse
    from .storage import write_json
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="dataset/example/dev")
    parser.add_argument("--output", default="runs/dataset_validation/dev.json")
    args = parser.parse_args()
    try:
        cases = load_all_cases(args.dataset)
        result = {"valid": True, "cases": [c.id for c in cases], "errors": []}
    except (ValueError, KeyError, TypeError, OSError, yaml.YAMLError) as exc:
        result = {"valid": False, "errors": [str(exc)]}
    write_json(Path(args.output), result)
    print(result)
    if not result["valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
