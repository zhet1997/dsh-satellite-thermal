from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_record(path: Path, *, root: Path | None = None) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": resolved.relative_to(root.resolve()).as_posix() if root else str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def copy_case_source(source: Path, target: Path) -> None:
    if target.exists():
        raise FileExistsError(f"Refusing to overwrite frozen case source: {target}")
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc", "runs"),
    )


def require_file(path: Path, label: str | None = None) -> Path:
    if not path.is_file():
        raise FileNotFoundError(f"{label or 'Required file'} is missing: {path}")
    return path


def require_ascii_path(path: Path) -> None:
    try:
        str(path.resolve()).encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError(f"NX 12 workflow requires an ASCII path: {path}") from exc
