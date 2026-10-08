from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

from .io import read_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--context", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        spec = importlib.util.spec_from_file_location("ugnxsim_case_stage", args.script)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load stage source: {args.script}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        run = getattr(module, "run", None)
        if not callable(run):
            raise ValueError(f"{args.script} must define run(context)")
        result = run(read_json(args.context))
        if not isinstance(result, dict):
            raise ValueError(f"{args.script} run(context) must return a dictionary")
        result.setdefault("ok", True)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0 if result.get("ok") else 1
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
