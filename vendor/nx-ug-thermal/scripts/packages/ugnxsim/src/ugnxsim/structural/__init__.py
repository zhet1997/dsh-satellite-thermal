from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from ..io import read_json, write_json
from ..snapshots import structural as structural_snapshot
from .bdf_model import build_bdf
from .postprocess import export_results
from .solver import solve_nastran


def _run_dir(context: dict[str, Any]) -> Path:
    return Path(context["run_dir"])


def solve(context: dict[str, Any]) -> dict[str, Any]:
    run_dir = _run_dir(context)
    deck = build_bdf(run_dir)
    solver = solve_nastran(
        run_dir,
        Path(context["nx_base"]),
        timeout=int(context["case"].get("timeouts", {}).get("solver", 3600)),
    )
    return {"ok": True, "deck": deck, "solver": solver}


def summarize(context: dict[str, Any]) -> dict[str, Any]:
    run_dir = _run_dir(context)
    result = export_results(run_dir)
    summary = {
        "schema_version": "ugnxsim-structural-summary/1",
        "modeled_total_mass_kg": result["modeled_total_mass_kg"],
        "element_type_counts": result["element_type_counts"],
        "subcases": result["subcases"],
        "reaction_balance": result["reaction_balance"],
        "safety": result["safety"],
    }
    write_json(run_dir / "reports" / "result_summary.json", summary)
    return {"ok": True, "summary": summary}


def _finite_positive(value: Any, *, allow_zero: bool = False) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and (number >= 0.0 if allow_zero else number > 0.0)


def validate(context: dict[str, Any]) -> dict[str, Any]:
    run_dir = _run_dir(context)
    solver = read_json(run_dir / "nx" / "solve_report.json")
    summary = read_json(run_dir / "reports" / "result_summary.json")
    acceptance = context["case"].get("acceptance", {})
    expected_cases = [str(row["id"]) for row in context["case"].get("load_cases", [])]
    subcases = summary.get("subcases", {})
    reactions = summary.get("reaction_balance", {})
    maximum_residual = float(acceptance.get("maximum_reaction_relative_residual", 1.0e-5))

    finite_subcases = bool(expected_cases)
    for case_id in expected_cases:
        row = subcases.get(case_id, {})
        finite_subcases = finite_subcases and all(
            (
                _finite_positive(row.get("max_displacement_mm"), allow_zero=True),
                _finite_positive(row.get("max_von_mises_raw_MPa"), allow_zero=True),
                _finite_positive(row.get("max_von_mises_filtered_MPa"), allow_zero=True),
                _finite_positive(row.get("max_equivalent_strain"), allow_zero=True),
            )
        )

    reaction_ok = bool(expected_cases)
    for case_id in expected_cases:
        residual = reactions.get(case_id, {}).get("relative_vector_residual")
        reaction_ok = reaction_ok and _finite_positive(residual, allow_zero=True) and float(residual) <= maximum_residual

    f06 = Path(str(solver.get("f06", "")))
    op2 = Path(str(solver.get("op2", "")))
    checks = {
        "f06_exists": f06.is_file(),
        "op2_exists": op2.is_file(),
        "end_of_job": bool(solver.get("end_of_job")),
        "no_fatal_lines": not solver.get("fatal_lines"),
        "expected_subcases_present": bool(expected_cases) and set(expected_cases) == set(subcases),
        "finite_structural_quantities": finite_subcases,
        "reaction_balance_within_limit": reaction_ok,
    }
    result = {
        "ok": all(checks.values()),
        "checks": checks,
        "maximum_reaction_relative_residual": maximum_residual,
        "subcases": expected_cases,
        "native_results": {"f06": str(f06), "op2": str(op2)},
    }
    write_json(run_dir / "reports" / "validation.json", result)
    if not result["ok"]:
        failed = ", ".join(name for name, passed in checks.items() if not passed)
        raise RuntimeError(f"structural validation failed: {failed}")
    return result


def snapshot(context: dict[str, Any]) -> dict[str, Any]:
    output = structural_snapshot(_run_dir(context))
    return {"ok": True, "snapshot": str(output), "size_bytes": output.stat().st_size}
