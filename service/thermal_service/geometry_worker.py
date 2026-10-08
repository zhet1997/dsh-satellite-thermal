"""One CAD job per process. Importing this module never creates an HTTP app."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from .storage import DomainError, atomic_json


def execute(job):
    # Keep CadQuery/OpenCascade imports and all native calls in this process.
    from . import geometry

    directory = Path(job["directory"]).resolve()
    if job.get("example"):
        upload, names = geometry.create_example(directory)
    else:
        upload, names = Path(job["upload"]).resolve(), None
    generated = geometry.import_geometry(upload, directory, names)
    for key in ("stepPath", "previewPath"):
        generated[key] = str(Path(generated[key]).resolve())
    return generated


def main():
    job_path, output = map(Path, sys.argv[1:])
    try:
        generated = execute(json.loads(job_path.read_text(encoding="utf-8")))
        result = {"ok": True, "generated": generated}
    except Exception as exc:
        logging.exception("Geometry worker failed")
        result = {"ok": False, "error": {
            "detail": exc.detail if isinstance(exc, DomainError) else "几何导入失败，原文件已保留。请检查 STEP 或工程服务依赖。",
            "code": exc.code if isinstance(exc, DomainError) else "geometry_failed",
            "status": exc.status if isinstance(exc, DomainError) else 500}}
    atomic_json(output, result)


if __name__ == "__main__":
    main()
