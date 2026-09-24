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


def digest_tree(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"Expected regular directory: {root}")
    for path in sorted(root.rglob("*")):
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
            "regression": runs / "regression" / name,
            "holdout": runs / "holdout" / name / candidate,
            "review": runs / "review" / name / candidate,
            "release": Path(settings["releases_dir"]) / name / candidate}


DEFAULT_ARTIFACTS = ('runs', 'skill_versions', 'releases', 'build', 'dist',
                     '.pytest_cache', '.mypy_cache', '.ruff_cache', 'htmlcov', '.coverage')
PROTECTED = ('src', 'tests', 'dataset', 'skill', 'config', '.git', '.codex', '.agents')


def clean(root, artifacts, environment, distclean=False):
    root = Path(root).resolve()
    environment = root / environment
    targets = [root / path for path in artifacts]
    protected = [root / name for name in PROTECTED]
    for target in [*targets, environment]:
        absolute = target.absolute()
        # Validate before deleting anything. Do not follow links outside the project.
        if '..' in target.parts or absolute == root or not absolute.is_relative_to(root):
            raise ValueError(f'Unsafe cleanup path: {target}')
        if any(absolute == path or path.is_relative_to(absolute) or absolute.is_relative_to(path)
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
    parser.add_argument('--artifacts', nargs='*', default=list(DEFAULT_ARTIFACTS))
    args = parser.parse_args()
    clean(Path.cwd(), args.artifacts, args.venv, args.distclean)


if __name__ == '__main__':
    main()
