from __future__ import annotations

import argparse
import json
from pathlib import Path

from .domains import run_managed_stage
from .io import read_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", required=True, choices=("thermal", "structural", "fov"))
    parser.add_argument("--stage", required=True)
    parser.add_argument("--context", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = run_managed_stage(args.domain, args.stage, read_json(args.context))
        if not isinstance(result, dict):
            raise ValueError("Managed stage must return a dictionary")
        result.setdefault("ok", True)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0 if result.get("ok") else 1
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
