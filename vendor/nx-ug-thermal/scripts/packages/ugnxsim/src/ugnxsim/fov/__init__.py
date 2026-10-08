from __future__ import annotations

import os
import re
import subprocess
from math import radians, tan
from pathlib import Path
from typing import Any

from ..io import read_json, write_json
from ..nx_runtime import run_nx_journal
from ..process import run_process
from ..snapshots import fov as fov_snapshot


def _run_dir(context: dict[str, Any]) -> Path:
    return Path(context["run_dir"])


def _box(minimum: list[float], maximum: list[float], label: str):
    from build123d import Align, Box, Location

    size = [float(maximum[index]) - float(minimum[index]) for index in range(3)]
    shape = Box(*size, align=(Align.MIN, Align.MIN, Align.MIN)).moved(Location(tuple(minimum)))
    shape.label = label
    return shape


def _source_shapes(run_dir: Path):
    from build123d import Compound

    semantics = read_json(run_dir / "source_inputs" / "geometry_semantics.json")
    normalized = read_json(run_dir / "source_inputs" / "normalized_spec.json")
    envelope = normalized["envelope"]
    outer = _box(envelope["outer_bbox"]["min"], envelope["outer_bbox"]["max"], "outer_shell")
    inner = _box(envelope["inner_bbox"]["min"], envelope["inner_bbox"]["max"], "inner_void")
    shell = outer - inner
    shell.label = "outer_shell"
    shapes = [("outer_shell", shell)]
    for body in semantics["bodies"]:
        label = str(body["business_id"])
        if label == "outer_shell":
            continue
        shapes.append((label, _box(body["bbox"]["min"], body["bbox"]["max"], label)))
    compound = Compound(children=[shape for _, shape in shapes], label="satellite_source_geometry")
    return compound, shapes, semantics


def _make_fov(spec: dict[str, Any]):
    from build123d import Align, Cone, Plane, Vector

    length = float(spec["display_length_mm"])
    radius = length * tan(radians(float(spec["half_angle_deg"])))
    cone = Cone(0.0, radius, length, align=(Align.CENTER, Align.CENTER, Align.MIN))
    cone = Plane(origin=tuple(spec["origin_mm"]), z_dir=Vector(*spec["direction"])) * cone
    cone.label = str(spec["label"])
    return cone


def _boxes_overlap(first: Any, second: Any) -> bool:
    first_min, first_max = tuple(first.min), tuple(first.max)
    second_min, second_max = tuple(second.min), tuple(second.max)
    return not any(
        first_max[index] < second_min[index] or first_min[index] > second_max[index]
        for index in range(3)
    )


def _generate_and_intersect(run_dir: Path, config: dict[str, Any]) -> dict[str, Any]:
    from build123d import Compound, export_step

    source, source_shapes, _ = _source_shapes(run_dir)
    cones = [_make_fov(spec) for spec in config["fov_specs"]]
    source_step = run_dir / "results" / "source_geometry.step"
    fov_step = run_dir / "results" / "fov_components.step"
    export_step(source, source_step)
    export_step(Compound(children=cones, label="fov_components"), fov_step)

    tolerance = float(config.get("intersection_tolerance_mm3", 1.0e-3))
    intersections = []
    for spec, cone in zip(config["fov_specs"], cones):
        cone_box = cone.bounding_box()
        blockers = []
        total_volume = 0.0
        for label, source_shape in source_shapes:
            if not _boxes_overlap(source_shape.bounding_box(), cone_box):
                continue
            common = source_shape & cone
            volume = float(common.volume) if common is not None else 0.0
            if volume > tolerance:
                blockers.append({"source_label": label, "intersection_volume_mm3": volume})
                total_volume += volume
        intersections.append(
            {
                "label": spec["label"],
                "intersects": bool(blockers),
                "intersection_volume_mm3_sum": total_volume,
                "blocking_source_bodies": blockers,
            }
        )
    write_json(run_dir / "results" / "fov_intersections.json", {"results": intersections})
    return {
        "source_step": str(source_step),
        "fov_step": str(fov_step),
        "source_body_count": len(source_shapes),
        "fov_count": len(cones),
        "intersections": intersections,
    }


def _translation_summary(log_path: Path) -> dict[str, Any]:
    text = log_path.read_text(encoding="latin-1", errors="replace")

    def number(pattern: str) -> int | None:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        return int(match.group(1)) if match else None

    return {
        "log": str(log_path),
        "end_of_translation": "End of Translation" in text,
        "solids_input": number(r"Total number of solids input for this translation\s*:\s*(\d+)"),
        "body_validation_faults": number(r"Number of solids created with body validation fault\(s\)\s*:\s*(\d+)"),
        "sheet_bodies": number(r"Number of solids ending up as sheet bodies\s*:\s*(\d+)"),
    }


def _translate_step(nx_base: Path, run_dir: Path, source: Path, target: Path, name: str) -> dict[str, Any]:
    translator = nx_base / "STEP214UG" / "step214ug.cmd"
    definition = nx_base / "STEP214UG" / "step214ug.def"
    if not translator.is_file() or not definition.is_file():
        raise FileNotFoundError(f"NX STEP214UG is incomplete under {nx_base}")
    target.parent.mkdir(parents=True, exist_ok=True)
    native_log = run_dir / "logs" / f"{name}_step214ug.log"
    command_text = subprocess.list2cmdline(
        [str(translator), str(source), "o", str(target), "l", str(native_log), "d", str(definition)]
    )
    env = os.environ.copy()
    env["UGII_BASE_DIR"] = str(nx_base)
    env["UGII_ROOT_DIR"] = str(nx_base / "NXBIN")
    env["STEP214UG_DIR"] = str(nx_base / "STEP214UG") + os.sep
    completed = run_process(
        [os.environ.get("ComSpec", "cmd.exe"), "/d", "/s", "/c", command_text],
        cwd=target.parent,
        log_path=run_dir / "logs" / f"{name}_translator_launcher.log",
        env=env,
        timeout=1800,
        encoding="gbk" if os.name == "nt" else "utf-8",
    )
    if completed.returncode != 0 or not target.is_file() or not native_log.is_file():
        raise RuntimeError(f"STEP214UG failed for {source}; see {native_log}")
    summary = _translation_summary(native_log)
    if not summary["end_of_translation"] or summary["body_validation_faults"] != 0:
        raise RuntimeError(f"STEP214UG output failed validation; see {native_log}")
    return {**summary, "target": str(target)}


def analyze(context: dict[str, Any]) -> dict[str, Any]:
    run_dir = _run_dir(context)
    nx_base = Path(context["nx_base"])
    config = read_json(run_dir / "results" / "fov_config.json")
    geometry = _generate_and_intersect(run_dir, config)
    source_top = run_dir / "nx" / "source" / "source_geometry_nx12.prt"
    fov_top = run_dir / "nx" / "fov" / "fov_components_nx12.prt"
    source_translation = _translate_step(nx_base, run_dir, Path(geometry["source_step"]), source_top, "source")
    fov_translation = _translate_step(nx_base, run_dir, Path(geometry["fov_step"]), fov_top, "fov")
    if fov_translation["solids_input"] != len(config["fov_specs"]):
        raise RuntimeError("NX FOV translation solid count does not match the configured FOV count")

    final_assembly = run_dir / "nx" / "source" / "fov_review_nx12.prt"
    journal_task = {
        "source_top": str(source_top),
        "final_assembly": str(final_assembly),
        "fov_parts_dir": str(fov_top.parent),
        "fov_specs": config["fov_specs"],
    }
    added = run_nx_journal(
        nx_base=nx_base,
        journal_name="fov_add_components",
        task=journal_task,
        run_dir=run_dir,
        timeout=1800,
    )
    styled = run_nx_journal(
        nx_base=nx_base,
        journal_name="fov_style_components",
        task={**journal_task, "translucency": int(config.get("translucency", 70))},
        run_dir=run_dir,
        timeout=1800,
    )
    nx_validation_path = run_dir / "results" / "nx_validation.json"
    nx_validation = run_nx_journal(
        nx_base=nx_base,
        journal_name="fov_validate_assembly",
        task={**journal_task, "validation_path": str(nx_validation_path)},
        run_dir=run_dir,
        timeout=1800,
    )
    result = {
        "ok": True,
        "geometry": geometry,
        "source_translation": source_translation,
        "fov_translation": fov_translation,
        "nx_add": added,
        "nx_style": styled,
        "nx_validation": nx_validation,
        "final_assembly": str(final_assembly),
    }
    write_json(run_dir / "nx" / "analyze_report.json", result)
    return result


def summarize(context: dict[str, Any]) -> dict[str, Any]:
    run_dir = _run_dir(context)
    analysis = read_json(run_dir / "nx" / "analyze_report.json")
    intersections = read_json(run_dir / "results" / "fov_intersections.json")["results"]
    summary = {
        "schema_version": "ugnxsim-fov-summary/1",
        "fov_count": len(intersections),
        "intersections": intersections,
        "nx_validation": analysis["nx_validation"],
        "source_body_count": analysis["geometry"]["source_body_count"],
        "artifacts": {
            "source_step": analysis["geometry"]["source_step"],
            "fov_step": analysis["geometry"]["fov_step"],
            "final_nx_assembly": analysis["final_assembly"],
        },
    }
    write_json(run_dir / "reports" / "result_summary.json", summary)
    return {"ok": True, "summary": summary}


def validate(context: dict[str, Any]) -> dict[str, Any]:
    run_dir = _run_dir(context)
    summary = read_json(run_dir / "reports" / "result_summary.json")
    analysis = read_json(run_dir / "nx" / "analyze_report.json")
    expected = context["case"].get("acceptance", {}).get("fov_intersections", {})
    actual = {row["label"]: bool(row["intersects"]) for row in summary["intersections"]}
    artifacts = summary["artifacts"]
    translation = analysis["fov_translation"]
    checks = {
        "configured_fov_count": summary["fov_count"] == len(context["case"].get("fov_specs", [])),
        "expected_intersections": bool(expected) and expected == actual,
        "nx_occurrences_match": bool(summary["nx_validation"].get("pass")),
        "source_step_exists": Path(artifacts["source_step"]).is_file(),
        "fov_step_exists": Path(artifacts["fov_step"]).is_file(),
        "nx_assembly_exists": Path(artifacts["final_nx_assembly"]).is_file(),
        "translation_completed": bool(translation.get("end_of_translation")),
        "translation_has_no_body_faults": translation.get("body_validation_faults") == 0,
    }
    result = {"ok": all(checks.values()), "checks": checks, "intersections": actual}
    write_json(run_dir / "reports" / "validation.json", result)
    if not result["ok"]:
        failed = ", ".join(name for name, passed in checks.items() if not passed)
        raise RuntimeError(f"FOV validation failed: {failed}")
    return result


def snapshot(context: dict[str, Any]) -> dict[str, Any]:
    output = fov_snapshot(_run_dir(context))
    return {"ok": True, "snapshot": str(output), "size_bytes": output.stat().st_size}
