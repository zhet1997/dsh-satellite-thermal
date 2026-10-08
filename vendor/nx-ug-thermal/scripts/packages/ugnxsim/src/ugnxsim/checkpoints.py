from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from .io import read_json, write_json


def checkpoint_path(run_dir: Path) -> Path:
    return run_dir / "reports" / "checkpoints.json"


def load_checkpoints(run_dir: Path) -> dict[str, Any]:
    path = checkpoint_path(run_dir)
    if not path.exists():
        return {"schema_version": "ugnxsim-checkpoints/1", "stages": []}
    return read_json(path)


def append_checkpoint(run_dir: Path, stage: str, result: dict[str, Any]) -> None:
    document = load_checkpoints(run_dir)
    rows = document.setdefault("stages", [])
    if any(row.get("stage") == stage for row in rows):
        raise RuntimeError(f"Stage {stage!r} already has a checkpoint; create a new run for repairs")
    rows.append(
        {
            "stage": stage,
            "ok": bool(result.get("ok")),
            "completed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "result": result,
        }
    )
    write_json(checkpoint_path(run_dir), document)


def require_predecessor(run_dir: Path, stages: tuple[str, ...], stage: str) -> None:
    index = stages.index(stage)
    if index == 0:
        return
    completed = {row.get("stage"): row for row in load_checkpoints(run_dir).get("stages", [])}
    predecessor = stages[index - 1]
    row = completed.get(predecessor)
    if not row or not row.get("ok"):
        raise RuntimeError(f"Stage {stage!r} requires successful checkpoint {predecessor!r}")
