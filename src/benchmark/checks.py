"""Deterministic checks with an extensible handler registry."""
import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from .case import checked_path


HANDLERS = {}


def register_check(name):
    def register(handler):
        if name in HANDLERS:
            raise ValueError(f"Check already registered: {name}")
        HANDLERS[name] = handler
        return handler
    return register


def _path(spec, outputs):
    if outputs is None:
        raise ValueError("This check requires outputs_dir")
    return checked_path(outputs, spec["path"])


def _text(spec, answer, outputs):
    return _path(spec, outputs).read_text(encoding="utf-8") if "path" in spec else answer


@register_check("non_empty")
def non_empty(spec, answer, outputs):
    return bool(_text(spec, answer, outputs).strip()), "Output must not be empty"


@register_check("contains")
def contains(spec, answer, outputs):
    expected = spec["text"]
    return expected.casefold() in _text(spec, answer, outputs).casefold(), f"Required text: {expected}"


@register_check("file_exists")
def file_exists(spec, answer, outputs):
    path = _path(spec, outputs)
    return path.is_file(), f"Required file: {spec['path']}"


@register_check("json_valid")
def json_valid(spec, answer, outputs):
    json.loads(_text(spec, answer, outputs))
    return True, "Valid JSON"


@register_check("json_schema")
def json_schema(spec, answer, outputs):
    import jsonschema
    def local_refs(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ("$ref", "$dynamicRef") and not item.startswith("#"):
                    raise ValueError("JSON Schema must use local references only")
                local_refs(item)
        elif isinstance(value, list):
            for item in value:
                local_refs(item)
    local_refs(spec["schema"])
    jsonschema.validate(json.loads(_text(spec, answer, outputs)), spec["schema"])
    return True, "JSON matches schema"


@register_check("svg_valid")
def svg_valid(spec, answer, outputs):
    root = ET.fromstring(_text(spec, answer, outputs))
    return root.tag in ("svg", "{http://www.w3.org/2000/svg}svg"), "SVG root element"


@register_check("docx_valid")
def docx_valid(spec, answer, outputs):
    with zipfile.ZipFile(_path(spec, outputs)) as archive:
        ET.fromstring(archive.read("word/document.xml"))
        ET.fromstring(archive.read("[Content_Types].xml"))
        if archive.testzip():
            raise ValueError("Corrupt DOCX member")
    return True, "Readable DOCX package"


@register_check("pdf_valid")
def pdf_valid(spec, answer, outputs):
    from pypdf import PdfReader
    reader = PdfReader(_path(spec, outputs), strict=True)
    return len(reader.pages) > 0, "Readable non-empty PDF"


def normalize_checks(checks):
    if isinstance(checks, list):
        return checks
    unknown = set(checks) - {"required_sections", "items", "non_empty"}
    if unknown:
        raise ValueError(f"Unknown check configuration: {sorted(unknown)}")
    specs = []
    if checks.get("non_empty", True):
        specs.append({"type": "non_empty", "name": "non_empty_answer"})
    specs.extend({"type": "contains", "text": section, "name": f"required_section:{section}"}
                 for section in checks.get("required_sections", []))
    return specs + checks.get("items", [])


def run_checks(answer: str, checks, outputs_dir=None, expected_outputs=None) -> dict:
    outputs = Path(outputs_dir) if outputs_dir else None
    specs = list(normalize_checks(checks))
    specs += [{"type": "file_exists", "path": item["path"], "severity": "critical"}
              for item in expected_outputs or [] if item.get("required", True)]
    results = []
    for spec in specs:
        kind = spec["type"]
        name = spec.get("name", kind + (":" + spec["path"] if "path" in spec else ""))
        try:
            if kind not in HANDLERS:
                raise ValueError(f"Unknown check type: {kind}")
            passed, evidence = HANDLERS[kind](spec, answer, outputs)
        except Exception as exc:
            passed, evidence = False, f"{type(exc).__name__}: {exc}"
        results.append({"name": name, "passed": bool(passed), "category": spec.get("category", kind),
                        "severity": spec.get("severity", "major"), "evidence": evidence})
    passed = sum(item["passed"] for item in results)
    return {"passed": passed, "failed": len(results) - passed, "checks": results}


def validate_skill(skill_dir: Path, expected_name=None) -> dict:
    import shutil
    import tempfile
    import yaml
    from skills_ref import validate
    from .storage import digest_tree, identifier

    errors = []
    name = None
    digest = None
    try:
        content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        if not content.startswith("---\n"):
            raise ValueError("Missing YAML frontmatter")
        metadata = yaml.safe_load(content.split("---", 2)[1])
        name = identifier(metadata["name"])
        if expected_name and name != expected_name:
            raise ValueError(f"Skill name changed: expected {expected_name}, got {name}")
        digest = digest_tree(skill_dir)
        # Version folders are named v0/v1, while skills-ref expects the skill name.
        if expected_name:
            with tempfile.TemporaryDirectory() as temporary:
                destination = Path(temporary) / name
                shutil.copytree(skill_dir, destination)
                errors.extend(validate(destination))
        else:
            errors.extend(validate(skill_dir))
    except (OSError, ValueError, KeyError, TypeError, IndexError, yaml.YAMLError) as exc:
        errors.append(str(exc))
    return {"valid": not errors, "errors": errors, "warnings": [], "skill_name": name, "skill_digest": digest}


def main():
    import argparse
    from .storage import write_json
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill", default="skill/requirements-analysis")
    parser.add_argument("--name")
    parser.add_argument("--output")
    args = parser.parse_args()
    result = validate_skill(Path(args.skill), args.name)
    output = Path(args.output or f"runs/validation/{Path(args.skill).name}/validation.json")
    write_json(output, result)
    print(result)
    if not result["valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
