from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from .nx_runtime import run_nx_journal, windows_file_version


def _checks(nx_base: Path, domain: str) -> dict[str, Any]:
    run_journal = nx_base / "NXBIN" / "run_journal.exe"
    values: dict[str, Any] = {
        "nx_base": str(nx_base.resolve()),
        "run_journal": run_journal.is_file(),
        "run_journal_version": windows_file_version(run_journal),
        "step214ug": (nx_base / "STEP214UG" / "step214ug.cmd").is_file(),
        "nastran": (nx_base / "NXNASTRAN" / "bin" / "nastran.exe").is_file(),
        "thermal_fem_template": (nx_base / "SIMULATION" / "templates" / "FemSpaceSysThermalMetric.fem").is_file(),
        "thermal_sim_template": (nx_base / "SIMULATION" / "templates" / "SimSpaceSysThermalMetric.sim").is_file(),
    }
    required = ["run_journal"]
    if domain in {"thermal", "all"}:
        required.extend(("thermal_fem_template", "thermal_sim_template"))
    if domain in {"structural", "all"}:
        required.append("nastran")
    if domain in {"fov", "all"}:
        required.append("step214ug")
    values["required_files"] = sorted(set(required))
    values["files_ok"] = all(bool(values.get(name)) for name in values["required_files"])
    return values


def _probe(nx_base: Path, run_dir: Path) -> dict[str, Any]:
    return run_nx_journal(
        nx_base=nx_base,
        journal_name="probe_runtime",
        task={"nx_base": str(nx_base)},
        run_dir=run_dir,
        timeout=180,
    )


def doctor(nx_base: Path, domain: str, work_dir: Path | None = None) -> dict[str, Any]:
    checks = _checks(nx_base.resolve(), domain)
    if work_dir:
        work_root = work_dir.resolve()
        work_root.mkdir(parents=True, exist_ok=True)
        run_dir = Path(tempfile.mkdtemp(prefix="ugnxsim_doctor_", dir=work_root))
        (run_dir / "logs").mkdir(parents=True)
        (run_dir / "tmp").mkdir()
        temporary = None
    else:
        temporary = tempfile.TemporaryDirectory(prefix="ugnxsim_doctor_")
        run_dir = Path(temporary.name)
        (run_dir / "logs").mkdir()
        (run_dir / "tmp").mkdir()
    try:
        try:
            nx_probe = _probe(nx_base, run_dir)
        except Exception as exc:
            nx_probe = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        result = {
            "ok": bool(checks["files_ok"] and nx_probe.get("ok")),
            "domain": domain,
            "checks": checks,
            "nx_probe": nx_probe,
        }
        if work_dir:
            result["probe_dir"] = str(run_dir)
        return result
    finally:
        if temporary is not None:
            temporary.cleanup()
