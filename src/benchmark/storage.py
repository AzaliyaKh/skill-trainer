"""Artifact storage, cleanup, configuration paths and content identities for pipeline stages."""
import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path



def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    temporary.replace(path)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_config(path: str) -> dict:
    import yaml
    config = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a mapping")
    return config


def identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value):
        raise ValueError(f"Unsafe identifier: {value!r}")
    return value


def digest_tree(root: Path, *, ignore_office_locks: bool = False) -> str:
    digest = hashlib.sha256()
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"Expected regular directory: {root}")
    for path in sorted(root.rglob("*")):
        if ignore_office_locks and path.name.startswith('.~lock.') and path.name.endswith('#'):
            continue
        if path.is_symlink():
            raise ValueError(f"Symlinks are not allowed: {path}")
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode())
            digest.update(b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def digest_json(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def cli_config():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/benchmark.yaml")
    return load_config(parser.parse_args().config)


def lifecycle_paths(config):
    from .checks import validate_skill
    source = Path(config["skill"]["path"]).parent
    version_root = Path(config["lifecycle"]["versions_dir"]).resolve()
    expected = source.parent.name if source.resolve().parent.parent == version_root else None
    validation = validate_skill(source, expected)
    if not validation["valid"]:
        raise ValueError(f"Invalid baseline skill: {validation['errors']}")
    name = validation["skill_name"]
    settings = config["lifecycle"]
    previous = identifier(settings["previous_version"])
    candidate = identifier(settings["candidate_version"])
    if previous == candidate:
        raise ValueError("Previous and candidate versions must differ")
    versions = Path(settings["versions_dir"]) / name
    runs = Path(settings["runs_dir"])
    return {"name": name, "source": source, "previous": previous, "candidate": candidate,
            "old_skill": versions / previous, "new_skill": versions / candidate,
            "optimization": runs / "optimization" / name / candidate,
            "proposed_skill": runs / "optimization" / name / candidate / "candidate",
            "regression": runs / "regression" / name,
            "holdout": runs / "holdout" / name / candidate,
            "review": runs / "review" / name / candidate,
            "release": Path(settings["releases_dir"]) / name / candidate}


def advance_lifecycle_config(config_path):
    """Advance vN -> vN+1 after the current candidate has regression evidence."""
    config_path = Path(config_path)
    config = load_config(str(config_path))
    paths = lifecycle_paths(config)
    match = re.fullmatch(r"v(\d+)", paths["candidate"])
    if not match:
        raise ValueError("candidate_version must use the vN format")
    if not paths["new_skill"].is_dir():
        raise FileNotFoundError(f"Current candidate version does not exist: {paths['new_skill']}")
    regression_path = paths["regression"] / "regression.json"
    pair_path = paths["regression"] / f"{paths['previous']}-to-{paths['candidate']}" / "regression.json"
    if not regression_path.is_file() and pair_path.is_file():
        regression_path = pair_path
    if not regression_path.is_file():
        raise FileNotFoundError("Regression report is required before advancing lifecycle versions")
    regression = read_json(regression_path)
    if (regression.get("previous_version") != paths["previous"] or
            regression.get("candidate_version") != paths["candidate"]):
        raise ValueError("Regression report does not match the configured version pair")
    next_version = f"v{int(match.group(1)) + 1}"
    if (Path(config["lifecycle"]["versions_dir"]) / paths["name"] / next_version).exists():
        raise FileExistsError(f"Next Skill version already exists: {next_version}")

    text = config_path.read_text(encoding="utf-8")
    lifecycle = re.search(r"(?ms)^lifecycle:\s*\n(?P<body>(?:^[ \t]+.*\n?)*)", text)
    if not lifecycle:
        raise ValueError("Missing lifecycle mapping in configuration")
    body = lifecycle.group("body")
    replacements = {
        "previous_version": paths["candidate"],
        "candidate_version": next_version,
    }
    for key, value in replacements.items():
        pattern = rf"(?m)^(?P<indent>[ \t]+){key}:\s*[^\n]*$"
        if len(re.findall(pattern, body)) != 1:
            raise ValueError(f"lifecycle.{key} must occur exactly once")
        body = re.sub(pattern, rf"\g<indent>{key}: {value}", body)
    updated = text[:lifecycle.start("body")] + body + text[lifecycle.end("body"):]
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=config_path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(updated)
    temporary.replace(config_path)
    verified = load_config(str(config_path))["lifecycle"]
    if verified["previous_version"] != paths["candidate"] or verified["candidate_version"] != next_version:
        raise RuntimeError("Lifecycle version update could not be verified")
    return {"previous_version": paths["candidate"], "candidate_version": next_version,
            "config": str(config_path)}


DEFAULT_ARTIFACTS = ('runs', 'runs_bad', 'skill/versions/requirements-analysis', 'build', 'dist',
                     '.pytest_cache', '.mypy_cache', '.ruff_cache', 'htmlcov', '.coverage')
PROTECTED = ('src', 'tests', 'dataset', 'skill', 'config', '.git', '.codex', '.agents')


def clean(root, artifacts, environment, distclean=False):
    root = Path(root).resolve()
    environment = root / environment
    targets = [root / path for path in artifacts]
    protected = [root / name for name in PROTECTED]
    generated_versions = root / 'skill/versions/requirements-analysis'
    for target in [*targets, environment]:
        absolute = target.absolute()
        # Validate before deleting anything. Do not follow links outside the project.
        if '..' in target.parts or absolute == root or not absolute.is_relative_to(root):
            raise ValueError(f'Unsafe cleanup path: {target}')
        allowed_versions = (target in targets and target != environment and
                            (absolute == generated_versions or absolute.is_relative_to(generated_versions)))
        if any((absolute == path or path.is_relative_to(absolute) or absolute.is_relative_to(path))
               and not (path == root / 'skill' and allowed_versions)
               for path in protected):
            raise ValueError(f'Cleanup overlaps source files: {target}')
        if any(parent.is_symlink() for parent in target.parents if parent != root):
            raise ValueError(f'Cleanup path has a symlink parent: {target}')
    for target in targets:
        if target == environment or environment.is_relative_to(target) or target.is_relative_to(environment):
            raise ValueError('Artifact paths must not overlap the environment')
    targets += list(root.glob('*.egg-info'))
    for directory in ('src', 'tests'):
        base = root / directory
        if base.is_dir() and not base.is_symlink():
            targets += list(base.rglob('__pycache__'))
            targets += list(base.rglob('*.pyc'))
    if distclean:
        targets.append(environment)
    for target in targets:
        if target.is_symlink() or target.is_file():
            target.unlink(missing_ok=True)
        elif target.exists():
            shutil.rmtree(target)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--venv', default='.venv')
    parser.add_argument('--distclean', action='store_true')
    parser.add_argument('--advance-lifecycle', action='store_true')
    parser.add_argument('--config', default='config/benchmark.yaml')
    parser.add_argument('--artifacts', nargs='*', default=list(DEFAULT_ARTIFACTS))
    args = parser.parse_args()
    if args.advance_lifecycle:
        print(advance_lifecycle_config(args.config))
    else:
        clean(Path.cwd(), args.artifacts, args.venv, args.distclean)


if __name__ == '__main__':
    main()
