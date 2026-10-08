from __future__ import annotations

from typing import Any


def run_managed_stage(domain: str, stage: str, context: dict[str, Any]) -> dict[str, Any]:
    if domain == "thermal":
        from . import thermal

        table = {
            "solve": thermal.solve,
            "summarize": thermal.summarize,
            "validate": thermal.validate,
            "snapshot": thermal.snapshot,
            "publish": thermal.publish,
        }
    elif domain == "structural":
        from . import structural

        table = {
            "solve": structural.solve,
            "summarize": structural.summarize,
            "validate": structural.validate,
            "snapshot": structural.snapshot,
        }
    elif domain == "fov":
        from . import fov

        table = {
            "analyze": fov.analyze,
            "summarize": fov.summarize,
            "validate": fov.validate,
            "snapshot": fov.snapshot,
        }
    else:
        raise ValueError(f"Unsupported domain: {domain}")
    if stage not in table:
        raise ValueError(f"Stage {stage!r} is not CLI-managed for {domain}")
    return table[stage](context)
