from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any


DOMAIN_STAGES = {
    "thermal": ("model", "mesh", "conditions", "solve", "summarize", "validate", "snapshot", "publish"),
    "structural": ("model", "mesh", "conditions", "solve", "summarize", "validate", "snapshot"),
    "fov": ("model", "analyze", "summarize", "validate", "snapshot"),
}

AGENT_STAGES = {
    "thermal": {"model", "mesh", "conditions"},
    "structural": {"model", "mesh", "conditions"},
    "fov": {"model"},
}


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load Python source: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_case(case_root: Path) -> dict[str, Any]:
    source = case_root / "analysis" / "case.py"
    if not source.is_file():
        raise FileNotFoundError(f"Case definition is missing: {source}")
    module = _load_module(source, f"ugnxsim_case_{case_root.name}")
    value = getattr(module, "CASE", None)
    if not isinstance(value, dict):
        raise ValueError(f"{source} must define a CASE dictionary")
    domain = str(value.get("domain", "")).lower()
    if domain not in DOMAIN_STAGES:
        raise ValueError(f"Unsupported case domain {domain!r}; expected one of {sorted(DOMAIN_STAGES)}")
    if not str(value.get("id", "")).strip():
        raise ValueError(f"{source} must define a non-empty CASE['id']")
    return value


def check_case(case_root: Path, source_run: Path) -> dict[str, Any]:
    brief = case_root / "analysis-brief.md"
    if not brief.is_file() or not brief.read_text(encoding="utf-8").strip():
        raise FileNotFoundError(f"A non-empty analysis brief is required before execution: {brief}")
    case = load_case(case_root)
    missing_stages = []
    for stage in sorted(AGENT_STAGES[case["domain"]]):
        if not (case_root / "analysis" / f"{stage}.py").is_file():
            missing_stages.append(stage)
    if missing_stages:
        raise FileNotFoundError(f"Case is missing Agent stage source: {', '.join(missing_stages)}")
    missing_inputs = []
    for relative in case.get("source_requirements", []):
        if not (source_run / relative).is_file():
            missing_inputs.append(relative)
    if missing_inputs:
        raise FileNotFoundError("Source run is incomplete: " + ", ".join(missing_inputs))
    return {
        "ok": True,
        "case_id": case["id"],
        "domain": case["domain"],
        "brief": str(brief.resolve()),
        "source_run": str(source_run.resolve()),
        "stages": list(DOMAIN_STAGES[case["domain"]]),
        "source_requirements": list(case.get("source_requirements", [])),
    }
