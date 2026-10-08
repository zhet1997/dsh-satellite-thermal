from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from .io import read_json, write_json
from .nx_runtime import run_nx_journal
from .publish import publish_temperature_vtu
from .snapshots import thermal as thermal_snapshot
from .thermal_vtu import export_temperature_vtu


def _paths(context: dict[str, Any]) -> tuple[Path, Path]:
    run_dir = Path(context["run_dir"])
    return run_dir, Path(context["nx_base"])


def solve(context: dict[str, Any]) -> dict[str, Any]:
    run_dir, nx_base = _paths(context)
    result = run_nx_journal(
        nx_base=nx_base,
        journal_name="thermal_solve_existing",
        task={
            "run_dir": str(run_dir),
            "sim_path": str(run_dir / "nx" / "thermal_case.sim"),
            "solution_name": context["case"].get("solution_name", "transient_thermal"),
            "solver_timeout_seconds": int(context["case"].get("timeouts", {}).get("solver", 7200)),
        },
        run_dir=run_dir,
        timeout=int(context["case"].get("timeouts", {}).get("solve", 7800)),
    )
    write_json(run_dir / "nx" / "solver_status.json", result)
    return result


def summarize(context: dict[str, Any]) -> dict[str, Any]:
    run_dir, nx_base = _paths(context)
    result = run_nx_journal(
        nx_base=nx_base,
        journal_name="thermal_extract_results",
        task={
            "run_dir": str(run_dir),
            "sim_path": str(run_dir / "nx" / "thermal_case.sim"),
            "raw_results_dir": str(run_dir / "nx" / "raw_results"),
            "solution_name": context["case"].get("solution_name", "transient_thermal"),
        },
        run_dir=run_dir,
        timeout=int(context["case"].get("timeouts", {}).get("summarize", 1800)),
    )
    summary = result["results"]
    vtu = export_temperature_vtu(
        run_dir / "nx" / "raw_results" / "nodes.csv",
        run_dir / "nx" / "raw_results" / "elements.csv",
        run_dir / "reports" / "native.vtu",
    )
    summary["vtu"] = vtu
    write_json(run_dir / "reports" / "result_summary.json", summary)
    return {"ok": True, "summary": summary, "vtu": vtu}


def validate(context: dict[str, Any]) -> dict[str, Any]:
    run_dir, _ = _paths(context)
    summary = read_json(run_dir / "reports" / "result_summary.json")
    solver = read_json(run_dir / "nx" / "solver_status.json")
    acceptance = context["case"].get("acceptance", {})
    temperatures = [
        float(summary.get("temperature_min_K", math.nan)),
        float(summary.get("temperature_max_K", math.nan)),
        float(summary.get("temperature_mean_K", math.nan)),
    ]
    checks = {
        "solver_completed": bool(solver.get("native_results", {}).get("end_of_run")),
        "native_bun_exists": Path(solver.get("native_results", {}).get("result_path", "")).is_file(),
        "minimum_result_times": int(summary.get("iteration_count", 0))
        >= int(acceptance.get("minimum_result_times", 1)),
        "finite_positive_temperatures": all(math.isfinite(value) and value > 0.0 for value in temperatures),
    }
    result = {"ok": all(checks.values()), "checks": checks, "temperatures_K": temperatures}
    write_json(run_dir / "reports" / "validation.json", result)
    if not result["ok"]:
        raise RuntimeError("thermal validation failed: " + ", ".join(name for name, ok in checks.items() if not ok))
    return result


def snapshot(context: dict[str, Any]) -> dict[str, Any]:
    run_dir, _ = _paths(context)
    output = thermal_snapshot(run_dir)
    return {"ok": True, "snapshot": str(output), "size_bytes": output.stat().st_size}


def publish(context: dict[str, Any]) -> dict[str, Any]:
    return publish_temperature_vtu(context)
