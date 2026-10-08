from __future__ import annotations

import csv
import importlib.util
import json
import sys
import tempfile
import types
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from unittest.mock import patch

from ugnxsim.deployment import MCP_SERVER, load_remote_target
from ugnxsim.io import read_json, sha256_file
from ugnxsim.linux_control import RETURN_FILES, package_run, publish_run, pull_run, push_run, status_run
from ugnxsim.publish import publish_temperature_vtu
from ugnxsim.thermal_data import DEFAULTS, FACE_NAMES, build_thermal_case, prepare_thermal_inputs
from ugnxsim.thermal_vtu import export_temperature_vtu


SKILL_ROOT = Path(__file__).resolve().parents[4]
JOURNALS = SKILL_ROOT / "scripts" / "windows" / "journals"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def _coordinate(origin: list[float]) -> dict[str, object]:
    return {
        "originMm": origin,
        "xAxis": [1, 0, 0],
        "yAxis": [0, 1, 0],
        "zAxis": [0, 0, 1],
    }


def _build_workspace(root: Path) -> Path:
    workspace = root / "workspace"
    simplified = workspace / "01_cad" / "simplified"
    simplified.mkdir(parents=True)
    (simplified / "assembly.step").write_text("ISO-10303-21;\n/* assembly */\nEND-ISO-10303-21;\n", encoding="ascii")
    _write_json(
        workspace / "00_inputs" / "project.json",
        {
            "schema_version": 3,
            "shell": {
                "type": "box", "dimensions_mm": [100, 100, 100], "wall_mm": 2,
                "rotation": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "translation_mm": [50, 50, 50],
            },
            "components": [
                {
                    "id": "P001",
                    "name": "powered",
                    "node_id": "N0003",
                    "power_w": 5,
                    "mass_kg": 1.0,
                    "dimensions_mm": [10, 10, 10],
                    "mount_face": "<Z",
                    "shell_mount_face": "<Z",
                    "placement": "internal",
                },
                {
                    "id": "P002",
                    "name": "passive",
                    "node_id": "N0004",
                    "dimensions_mm": [10, 10, 10],
                    "mount_face": ">X",
                    "shell_mount_face": ">X",
                    "placement": "internal",
                },
            ]
        },
    )
    _write_json(
        workspace / "01_cad" / "planner" / "layout.json",
        {
            "schema_version": 3,
            "metadata": {"coordinate_mode": "world"},
            "placements": {
                "P001": {"rotation": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "translation_mm": [15, 15, 7]},
                "P002": {
                    "rotation": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "translation_mm": [93, 35, 35],
                },
            },
        },
    )
    _write_json(workspace / "01_cad" / "mount_face_check.json", {"problem_component_ids": []})
    _write_json(workspace / "01_cad" / "collision_check.json", {"collision_pairs": []})
    contract_path = simplified / "geometry-contract.json"
    surfaces = []
    for token, normal, outer, inner in (
        (">X", [1, 0, 0], [[100, 0, 0], [100, 100, 0], [100, 100, 100], [100, 0, 100]], [[98, 0, 0], [98, 100, 0], [98, 100, 100], [98, 0, 100]]),
        ("<X", [-1, 0, 0], [[0, 100, 0], [0, 0, 0], [0, 0, 100], [0, 100, 100]], [[2, 100, 0], [2, 0, 0], [2, 0, 100], [2, 100, 100]]),
        (">Y", [0, 1, 0], [[100, 100, 0], [0, 100, 0], [0, 100, 100], [100, 100, 100]], [[100, 98, 0], [0, 98, 0], [0, 98, 100], [100, 98, 100]]),
        ("<Y", [0, -1, 0], [[0, 0, 0], [100, 0, 0], [100, 0, 100], [0, 0, 100]], [[0, 2, 0], [100, 2, 0], [100, 2, 100], [0, 2, 100]]),
        (">Z", [0, 0, 1], [[0, 0, 100], [100, 0, 100], [100, 100, 100], [0, 100, 100]], [[0, 0, 98], [100, 0, 98], [100, 100, 98], [0, 100, 98]]),
        ("<Z", [0, 0, -1], [[0, 100, 0], [100, 100, 0], [100, 0, 0], [0, 0, 0]], [[0, 100, 2], [100, 100, 2], [100, 0, 2], [0, 0, 2]]),
    ):
        surfaces.append({"surface_id": token, "normal": normal, "external_vertices": outer, "internal_vertices": inner})
    _write_json(contract_path, {
        "schema_version": "cad-builder-geometry-contract/1", "geometry_mode": "simplified_envelope",
        "bodies": [
            {"component_id": "envelope", "step_body_index": 0, "bbox": {"min": [0, 0, 0], "max": [100, 100, 100]}, "volume_mm3": 115264},
            {"component_id": "P001", "step_body_index": 1, "bbox": {"min": [10, 10, 2], "max": [20, 20, 12]}, "volume_mm3": 1000},
            {"component_id": "P002", "step_body_index": 2, "bbox": {"min": [88, 30, 30], "max": [98, 40, 40]}, "volume_mm3": 1000},
        ],
        "shell_surfaces": surfaces,
        "mount_regions": [
            {"component_id": "P001", "step_body_index": 1, "mount_face": "<Z", "shell_mount_face": "<Z", "placement": "internal", "normal": [0, 0, -1], "faces": [{"bbox": {"min": [10, 10, 2], "max": [20, 20, 2]}, "plane_origin_mm": [15, 15, 2], "area_mm2": 100}], "total_area_mm2": 100},
            {"component_id": "P002", "step_body_index": 2, "mount_face": ">X", "shell_mount_face": ">X", "placement": "internal", "normal": [1, 0, 0], "faces": [{"bbox": {"min": [98, 30, 30], "max": [98, 40, 40]}, "plane_origin_mm": [98, 35, 35], "area_mm2": 100}], "total_area_mm2": 100},
        ],
    })
    step_path = simplified / "assembly.step"
    _write_json(simplified / "manifest.json", {
        "generator": "cad_native_export/1.1", "mode": "envelope",
        "artifacts": {
            "step": {"path": str(step_path), "bytes": step_path.stat().st_size, "sha256": sha256_file(step_path)},
            "geometry_contract": {"path": str(contract_path), "bytes": contract_path.stat().st_size, "sha256": sha256_file(contract_path)},
        },
    })
    return workspace


def _target_config(root: Path) -> Path:
    target = root / "config.json"
    _write_json(
        target,
        {
            "dsh": {
                "mcpServers": {
                    MCP_SERVER: {
                        "remoteTarget": {
                            "workspace_dir": r"D:\data\mcp-uploads",
                            "download_dir": r"D:\data\mcp-downloads",
                            "run_journal": r"D:\NX12\NXBIN\run_journal.exe",
                        }
                    }
                }
            },
        },
    )
    return target


class ThermalDataPreparationTest(unittest.TestCase):
    def test_defaults_priority_six_faces_and_assumptions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = _build_workspace(Path(temporary))
            result = prepare_thermal_inputs(workspace)
            inputs = workspace / "02_sim" / "thermal" / "inputs"
            self.assertEqual(
                {path.name for path in inputs.iterdir()},
                {
                    "thermal-inputs.json", "geometry-semantics.json", "source-manifest.json",
                    "assumptions.json", "components.csv", "contacts.csv", "environment.csv",
                },
            )
            thermal = read_json(inputs / "thermal-inputs.json")
            by_id = {row["id"]: row for row in thermal["components"]}

            self.assertTrue(result["ok"])
            self.assertEqual(by_id["P001"]["power"]["value"], 5.0)
            self.assertEqual(by_id["P001"]["power"]["status"], "provided")
            self.assertEqual(by_id["P002"]["power"]["value"], DEFAULTS["power_W"])
            self.assertEqual(by_id["P002"]["power"]["status"], "global_default")
            self.assertEqual(by_id["P002"]["conductivity"]["value"], 167.0)
            self.assertEqual(by_id["P002"]["emissivity"]["value"], 0.8)
            self.assertEqual(thermal["analysis"]["initial_temperature"]["value"], 100.0)
            self.assertEqual(thermal["environment"]["space_temperature"]["value"], 3.0)
            self.assertEqual(thermal["environment"]["view_factor"]["value"], 1.0)
            self.assertEqual([row["boundary_id"] for row in thermal["environment"]["faces"]], list(FACE_NAMES))
            self.assertTrue(all(row["heat_flux"]["value"] == 0.0 for row in thermal["environment"]["faces"]))
            self.assertEqual(thermal["contacts"][0]["thermal_resistance"]["value"], 0.001)
            self.assertEqual(thermal["contacts"][0]["area"]["status"], "derived")
            assumptions = read_json(inputs / "assumptions.json")["items"]
            self.assertTrue(any(row["target"] == "P002" and row["field"] == "power" for row in assumptions))
            self.assertTrue(all(row["status"] in {"estimated", "global_default"} for row in assumptions))

    def test_explicit_overrides_win_and_are_traceable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = _build_workspace(Path(temporary))
            overrides = workspace / "00_inputs" / "thermal-overrides.json"
            _write_json(
                overrides,
                {
                    "components": {"P001": {"power_W": 9, "conductivity_W_mK": 42, "emissivity": 0.55}},
                    "contacts": {"CONTACT_P001": {"resistance_m2K_W": 0.02}},
                    "environment": {"x+": 11},
                    "global": {"initial_temperature_K": 120, "space_temperature_K": 4, "view_factor": 0.9},
                },
            )
            prepare_thermal_inputs(workspace, overrides_path=overrides)
            thermal = read_json(workspace / "02_sim" / "thermal" / "inputs" / "thermal-inputs.json")
            p1 = next(row for row in thermal["components"] if row["id"] == "P001")
            self.assertEqual((p1["power"]["value"], p1["power"]["status"]), (9.0, "provided"))
            self.assertEqual(p1["conductivity"]["value"], 42.0)
            self.assertEqual(p1["emissivity"]["value"], 0.55)
            self.assertEqual(thermal["contacts"][0]["thermal_resistance"]["value"], 0.02)
            x_plus = next(row for row in thermal["environment"]["faces"] if row["boundary_id"] == "x+")
            self.assertEqual((x_plus["heat_flux"]["value"], x_plus["heat_flux"]["status"]), (11.0, "provided"))
            self.assertEqual(thermal["analysis"]["initial_temperature"]["value"], 120.0)
            self.assertEqual(thermal["environment"]["space_temperature"]["value"], 4.0)
            self.assertEqual(thermal["environment"]["view_factor"]["value"], 0.9)

    def test_geometry_semantics_and_source_manifest_are_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = _build_workspace(Path(temporary))
            prepare_thermal_inputs(workspace)
            inputs = workspace / "02_sim" / "thermal" / "inputs"
            semantics = read_json(inputs / "geometry-semantics.json")
            manifest = read_json(inputs / "source-manifest.json")
            by_id = {row["business_id"]: row for row in semantics["bodies"]}
            self.assertEqual(set(by_id), {"envelope", "P001", "P002"})
            self.assertEqual(
                [row["step_body_index"] for row in semantics["bodies"]],
                [0, 1, 2],
            )
            self.assertEqual(by_id["P001"]["bbox"]["min"], [10.0, 10.0, 2.0])
            self.assertIsNone(by_id["P001"]["placement"])
            p2_mounts = [
                row for row in semantics["contacts"]
                if row["kind"] == "component_mount" and row["contact_id"].startswith("CONTACT_P002")
            ]
            self.assertEqual(len(p2_mounts), 1)
            self.assertTrue(all(row["target_face_id"] == "FACE_ENVELOPE_XPOS_INNER" for row in p2_mounts))
            p2_power = sum(row["power_W"] for row in semantics["bodies"] if row.get("component_id") == "P002")
            self.assertEqual(p2_power, 0.0)
            for record in manifest["files"]:
                path = workspace / record["path"]
                self.assertEqual(record["sha256"], sha256_file(path))
                self.assertEqual(record["size_bytes"], path.stat().st_size)

    def test_explicit_source_outside_workspace_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = _build_workspace(root)
            outside = root / "outside.json"
            outside.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "escapes the workspace"):
                prepare_thermal_inputs(workspace, component_path=outside)

    def test_contact_area_is_derived_from_simplified_mount_face(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = _build_workspace(Path(temporary))
            prepare_thermal_inputs(workspace)
            inputs = workspace / "02_sim" / "thermal" / "inputs"
            thermal = read_json(inputs / "thermal-inputs.json")
            contact = next(row for row in thermal["contacts"] if row["component_a"] == "P001")
            self.assertEqual(contact["area"]["status"], "derived")
            self.assertAlmostEqual(contact["area"]["value"], 0.0001)

    def test_multiface_mount_region_creates_one_nx_contact_per_cad_face(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = _build_workspace(Path(temporary))
            contract_path = workspace / "01_cad" / "simplified" / "geometry-contract.json"
            contract = read_json(contract_path)
            contract["mount_regions"][0]["faces"] = [
                {"bbox": {"min": [10, 10, 2], "max": [14, 20, 2]}, "area_mm2": 40},
                {"bbox": {"min": [14, 10, 2], "max": [20, 20, 2]}, "area_mm2": 60},
            ]
            _write_json(contract_path, contract)
            manifest_path = contract_path.parent / "manifest.json"
            manifest = read_json(manifest_path)
            manifest["artifacts"]["geometry_contract"]["sha256"] = sha256_file(contract_path)
            manifest["artifacts"]["geometry_contract"]["bytes"] = contract_path.stat().st_size
            _write_json(manifest_path, manifest)
            prepare_thermal_inputs(workspace)
            thermal = read_json(workspace / "02_sim" / "thermal" / "inputs" / "thermal-inputs.json")
            contacts = [row for row in thermal["contacts"] if row["component_a"] == "P001"]
            self.assertEqual([row["contact_id"] for row in contacts], ["CONTACT_P001_001", "CONTACT_P001_002"])
            self.assertAlmostEqual(sum(row["area"]["value"] for row in contacts), 0.0001)

    def test_actual_contract_face_normal_overrides_region_normal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = _build_workspace(Path(temporary))
            contract_path = workspace / "01_cad" / "simplified" / "geometry-contract.json"
            contract = read_json(contract_path)
            contract["mount_regions"][0]["faces"][0]["normal"] = [0.0, 0.01, -0.99995]
            _write_json(contract_path, contract)
            manifest_path = contract_path.parent / "manifest.json"
            manifest = read_json(manifest_path)
            manifest["artifacts"]["geometry_contract"]["sha256"] = sha256_file(contract_path)
            manifest["artifacts"]["geometry_contract"]["bytes"] = contract_path.stat().st_size
            _write_json(manifest_path, manifest)

            prepare_thermal_inputs(workspace)
            semantics = read_json(
                workspace / "02_sim" / "thermal" / "inputs" / "geometry-semantics.json"
            )
            face = next(row for row in semantics["faces"] if row["face_id"] == "FACE_P001_MOUNT")
            self.assertAlmostEqual(face["normal"][1], 0.01, places=5)
            self.assertAlmostEqual(face["normal"][2], -0.99995, places=5)

    def test_layout_boxes_do_not_replace_the_merged_assembly_body(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = _build_workspace(Path(temporary))
            layout_path = workspace / "01_cad" / "planner" / "layout.json"
            layout = read_json(layout_path)
            layout["placements"]["P002"]["boxes"] = [
                {"dimensions_mm": [4, 10, 10], "rotation": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "translation_mm": [91, 35, 35]},
                {"dimensions_mm": [6, 10, 10], "rotation": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "translation_mm": [95, 35, 35]},
            ]
            _write_json(layout_path, layout)
            source_step = workspace / "01_cad" / "simplified" / "assembly.step"
            source_hash = sha256_file(source_step)

            result = prepare_thermal_inputs(workspace)
            semantics = read_json(workspace / "02_sim" / "thermal" / "inputs" / "geometry-semantics.json")
            thermal = read_json(workspace / "02_sim" / "thermal" / "inputs" / "thermal-inputs.json")
            p2 = [row for row in semantics["bodies"] if row.get("component_id") == "P002"]

            self.assertEqual(result["body_count"], 3)
            self.assertEqual(len(p2), 1)
            self.assertEqual(p2[0]["business_id"], "P002")
            self.assertEqual(p2[0]["source_box_count"], 2)
            self.assertEqual(p2[0]["mesh"]["requested_size_mm"], 25.0)
            self.assertEqual(p2[0]["mesh"]["retry_scales"], [1.0, 1.5, 2.0, 3.0])
            self.assertEqual(p2[0]["mesh"]["policy"], "linux_multibox_bbox_v1")
            self.assertEqual(next(row for row in thermal["components"] if row["id"] == "P002")["body_ids"], ["P002"])
            self.assertEqual(Path(semantics["geometry_source"]["source_step"]), source_step)
            self.assertEqual(semantics["geometry_source"]["sha256"], source_hash)
            self.assertEqual(sha256_file(source_step), source_hash)
            self.assertFalse((workspace / "02_sim" / "thermal" / "geometry" / "assembly.step").exists())

    def test_nx_model_binding_does_not_compare_json_and_step_bounds(self) -> None:
        journal = (JOURNALS / "build_geometry_master.py").read_text(encoding="utf-8")
        self.assertIn('"body_binding_method": "step_body_index"', journal)
        self.assertIn('"bbox_source": "NX imported STEP"', journal)
        self.assertNotIn("def bbox_error", journal)
        self.assertNotIn("bbox error", journal)
        solve_journal = (JOURNALS / "build_and_solve.py").read_text(encoding="utf-8")
        self.assertIn("def apply_step_geometry", solve_journal)
        self.assertIn('binding = read_json(task["nx_binding_path"])', solve_journal)
        self.assertIn('row["plane_origin_mm"] = actual["plane_origin_mm"]', solve_journal)

    def test_multibox_mesh_policy_is_frozen_on_linux_and_never_refines(self) -> None:
        journal = (JOURNALS / "build_and_solve.py").read_text(encoding="utf-8")
        self.assertIn('body_mesh = body_row.get("mesh")', journal)
        self.assertIn('return float(body_mesh["requested_size_mm"])', journal)
        self.assertIn('body_mesh.get("retry_scales", (1.0, 1.5))', journal)
        self.assertNotIn('mesh_config.get("multibox_component_size_mm"', journal)
        self.assertNotIn("retry_scales = (0.", journal)

        with tempfile.TemporaryDirectory() as temporary:
            workspace = _build_workspace(Path(temporary))
            prepare_thermal_inputs(workspace)
            inputs = workspace / "02_sim" / "thermal" / "inputs"
            thermal_case = build_thermal_case(
                read_json(inputs / "thermal-inputs.json"),
                read_json(inputs / "geometry-semantics.json"),
            )
            self.assertNotIn("multibox_component_size_mm", thermal_case["mesh"])

    def test_coarse_mesh_contact_faces_use_overlap_not_full_containment(self) -> None:
        journal = (JOURNALS / "build_and_solve.py").read_text(encoding="utf-8")
        self.assertIn("face_max[index] >= bounds[index] - tolerance", journal)
        self.assertIn("face_min[index] <= bounds[index + 3] + tolerance", journal)
        self.assertIn('min(2.0, float(mesh_row["actual_size_mm"]) * 0.1)', journal)
        self.assertNotIn("bounds[index] - tolerance <= float(point[index])", journal)

    def test_mounting_problem_component_is_excluded_from_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = _build_workspace(Path(temporary))
            _write_json(workspace / "01_cad" / "mount_face_check.json", {"problem_component_ids": ["P002"]})
            result = prepare_thermal_inputs(workspace)
            inputs = workspace / "02_sim" / "thermal" / "inputs"
            thermal = read_json(inputs / "thermal-inputs.json")
            semantics = read_json(inputs / "geometry-semantics.json")
            p2 = next(row for row in thermal["components"] if row["id"] == "P002")
            self.assertFalse(p2["analysis_enabled"])
            self.assertTrue(all(row["component_a"] != "P002" for row in thermal["contacts"]))
            self.assertTrue(all(not row["analysis_enabled"] for row in semantics["bodies"] if row.get("component_id") == "P002"))
            self.assertEqual(result["excluded_component_count"], 1)

    def test_colliding_components_are_excluded_from_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = _build_workspace(Path(temporary))
            _write_json(
                workspace / "01_cad" / "collision_check.json",
                {
                    "collision_pairs": [
                        {
                            "component_ids": ["P001", "P002"],
                            "collision_volume_mm3": 12.5,
                        }
                    ]
                },
            )
            result = prepare_thermal_inputs(workspace)
            inputs = workspace / "02_sim" / "thermal" / "inputs"
            thermal = read_json(inputs / "thermal-inputs.json")
            semantics = read_json(inputs / "geometry-semantics.json")
            assumptions = read_json(inputs / "assumptions.json")["items"]
            thermal_case = build_thermal_case(thermal, semantics)

            self.assertTrue(all(not row["analysis_enabled"] for row in thermal["components"]))
            self.assertTrue(all(not row["analysis_enabled"] for row in semantics["bodies"] if row["object_type"] == "component"))
            self.assertEqual(thermal["contacts"], [])
            self.assertEqual(thermal_case["component_power_W"], {})
            self.assertEqual(thermal_case["total_power_W"], 0.0)
            collision_reasons = [
                row for row in assumptions
                if row.get("source_locator", "").startswith("01_cad/collision_check.json#/")
            ]
            self.assertEqual({row["target"] for row in collision_reasons}, {"P001", "P002"})
            self.assertEqual(result["excluded_component_count"], 2)
            self.assertEqual(result["collision_excluded_component_count"], 2)


class PackageAndTransportTest(unittest.TestCase):
    def test_package_contains_frozen_inputs_geometry_and_windows_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = _build_workspace(Path(temporary))
            prepare_thermal_inputs(workspace)
            result = package_run(workspace)
            payload = Path(result["run_dir"]) / "payload.zip"
            with zipfile.ZipFile(payload) as archive:
                names = set(archive.namelist())
            self.assertIn("run-manifest.json", names)
            self.assertIn("inputs/thermal-inputs.json", names)
            self.assertIn("inputs/thermal-case.json", names)
            self.assertIn("journals/build_geometry_master.py", names)
            self.assertIn("journals/build_and_solve.py", names)
            self.assertIn("journals/extract_results.py", names)
            self.assertIn("Start-NxStage.ps1", names)
            self.assertIn("Invoke-NxStage.ps1", names)
            self.assertNotIn("setup-wqn-pc-2026.ps1", names)
            self.assertTrue(result["run_id"].isascii())
            manifest = read_json(Path(result["run_dir"]) / "run-manifest.json")
            self.assertEqual(manifest["workspace_dir"], r"D:\data\mcp-uploads")
            self.assertEqual(manifest["download_dir"], r"D:\data\mcp-downloads")
            self.assertEqual(manifest["run_journal"], r"E:\nx12\NXBIN\run_journal.exe")
            with zipfile.ZipFile(Path(result["run_dir"]) / "transfer.zip") as transfer:
                self.assertEqual(set(transfer.namelist()), {"payload.zip", "run-manifest.json"})

    def test_package_rejects_source_changed_after_prepare(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = _build_workspace(Path(temporary))
            prepare_thermal_inputs(workspace)
            source = workspace / "01_cad" / "simplified" / "assembly.step"
            source.write_text("changed", encoding="ascii")
            with self.assertRaisesRegex(RuntimeError, "changed after normalization"):
                package_run(workspace)

    def test_push_returns_mcp_upload_plan_and_verified_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = _build_workspace(root)
            target_path = _target_config(root)
            prepare_thermal_inputs(workspace)
            packaged = package_run(workspace, target_config=target_path)
            run_dir = Path(packaged["run_dir"])
            result = push_run(run_dir, target_config=target_path)
            self.assertEqual(result["mcp_server"], MCP_SERVER)
            self.assertEqual(result["tool"], "create_upload")
            self.assertEqual(result["arguments"]["sourcePath"], str(run_dir / "transfer.zip"))
            self.assertEqual(result["arguments"]["targetPath"], f"nx-ug-thermal/{packaged['run_id']}/transfer.zip")
            self.assertEqual(result["arguments"]["sha256"], sha256_file(run_dir / "transfer.zip"))
            self.assertIn("Get-FileHash", result["next"]["powershell_template"])
            self.assertIn("Start-NxStage.ps1", result["next"]["powershell_template"])

    def test_pull_returns_download_plan_then_verifies_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = _build_workspace(Path(temporary))
            prepare_thermal_inputs(workspace)
            packaged = package_run(workspace)
            run_dir = Path(packaged["run_dir"])
            plan = pull_run(run_dir)
            self.assertEqual(plan["tool"], "create_download")
            self.assertEqual(plan["download_to"], str(run_dir / "return-bundle.zip"))

            source_dir = run_dir / "fake-return"
            source_dir.mkdir()
            files = {}
            for name in RETURN_FILES:
                path = source_dir / name
                path.write_text(f"result:{name}\n", encoding="utf-8")
                files[name] = {"path": name, "size_bytes": path.stat().st_size, "sha256": sha256_file(path)}
            return_manifest = source_dir / "return-manifest.json"
            _write_json(return_manifest, {"files": list(files.values())})
            with zipfile.ZipFile(run_dir / "return-bundle.zip", "w") as archive:
                for name in RETURN_FILES:
                    archive.write(source_dir / name, name)
                archive.write(return_manifest, "return-manifest.json")

            result = pull_run(run_dir)
            self.assertTrue(result["ready"])
            self.assertEqual({path.name for path in (run_dir / "returned").iterdir()}, {*RETURN_FILES, "return-manifest.json"})

    def test_status_is_read_only_and_checks_recorded_pid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = _build_workspace(Path(temporary))
            prepare_thermal_inputs(workspace)
            packaged = package_run(workspace)
            result = status_run(Path(packaged["run_dir"]))
            self.assertEqual(result["mcp_server"], MCP_SERVER)
            self.assertIn("Get-Process", result["powershell"])
            self.assertNotIn("Start-Process", result["powershell"])

    def test_publish_copies_all_verified_return_files_and_updates_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = _build_workspace(Path(temporary))
            prepare_thermal_inputs(workspace)
            packaged = package_run(workspace)
            run_dir = Path(packaged["run_dir"])
            returned = run_dir / "returned"
            returned.mkdir(exist_ok=True)
            for name in RETURN_FILES:
                (returned / name).write_text(f"result:{name}\n", encoding="utf-8")

            result = publish_run(run_dir)

            output = workspace / "02_sim" / "simulation"
            self.assertEqual({path.name for path in output.iterdir()}, set(RETURN_FILES))
            self.assertEqual(result["output_dir"], str(output))
            self.assertEqual(read_json(run_dir / "state" / "linux.json")["status"], "published")


class DeploymentConfigTest(unittest.TestCase):
    def test_default_target_contains_mcp_workspace_and_nx_journal(self) -> None:
        target = load_remote_target()
        self.assertEqual(target["mcp_server"], MCP_SERVER)
        self.assertEqual(target["workspace_dir"], r"D:\data\mcp-uploads")
        self.assertEqual(target["download_dir"], r"D:\data\mcp-downloads")
        self.assertEqual(target["run_journal"], r"E:\nx12\NXBIN\run_journal.exe")
        self.assertEqual(
            set(target),
            {"mcp_server", "workspace_dir", "download_dir", "run_journal", "config_path"},
        )

    def test_missing_mcp_and_extra_fields_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target_path = _target_config(root)
            value = read_json(target_path)
            value["dsh"]["mcpServers"]["windows-powershell-other"] = value["dsh"]["mcpServers"].pop(MCP_SERVER)
            _write_json(target_path, value)
            with self.assertRaisesRegex(ValueError, "missing"):
                load_remote_target(target_path)
            value = read_json(_target_config(root))
            remote_target = value["dsh"]["mcpServers"][MCP_SERVER]["remoteTarget"]
            remote_target["legacy_connection"] = "unused"
            _write_json(target_path, value)
            with self.assertRaisesRegex(ValueError, "must contain only"):
                load_remote_target(target_path)

    def test_nested_transport_configuration_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target_path = _target_config(root)
            value = read_json(target_path)
            value["dsh"]["mcpServers"][MCP_SERVER]["remoteTarget"]["transport"] = {
                "host": "example.invalid"
            }
            _write_json(target_path, value)
            with self.assertRaisesRegex(ValueError, "must contain only"):
                load_remote_target(target_path)


class ExistingCasePublishTest(unittest.TestCase):
    def test_publish_returns_mcp_download_plan_without_ssh(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run-001"
            reports = run_dir / "reports"
            reports.mkdir(parents=True)
            (reports / "native.vtu").write_text("vtu-data", encoding="ascii")
            windows_workspace = root / "windows-workspace"
            windows_workspace.mkdir()
            mcp_workspace = root / "mcp-workspace"
            target_path = _target_config(root)
            context = {
                "run_dir": str(run_dir),
                "windows_workspace_dir": str(windows_workspace),
                "remote_workspace": "/active/workspace",
                "remote_target_config": str(target_path),
            }
            with patch(
                "ugnxsim.publish.load_remote_target",
                return_value={
                    "mcp_server": MCP_SERVER,
                    "workspace_dir": str(mcp_workspace),
                    "download_dir": str(mcp_workspace / "downloads"),
                },
            ):
                result = publish_temperature_vtu(context)
            download_root = mcp_workspace / "downloads"
            self.assertEqual(result["tool"], "create_download")
            self.assertEqual(result["download_to"], "/active/workspace/02_sim/simulation/native.vtu")
            self.assertEqual(result["arguments"]["sourcePath"], str(download_root / "run-001-native.vtu"))
            self.assertEqual(result["sha256"], sha256_file(download_root / "run-001-native.vtu"))


class ThermalVtuTest(unittest.TestCase):
    def test_writes_nonempty_vtu_temperature_array_and_component_stats(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nodes = root / "nodes.csv"
            elements = root / "elements.csv"
            with nodes.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=["node_id", "x_mm", "y_mm", "z_mm", "temperature_K", "business_id"])
                writer.writeheader()
                writer.writerows([
                    {"node_id": 1, "x_mm": 0, "y_mm": 0, "z_mm": 0, "temperature_K": 100, "business_id": "A"},
                    {"node_id": 2, "x_mm": 1, "y_mm": 0, "z_mm": 0, "temperature_K": 110, "business_id": "A"},
                    {"node_id": 3, "x_mm": 0, "y_mm": 1, "z_mm": 0, "temperature_K": 120, "business_id": "B"},
                    {"node_id": 4, "x_mm": 0, "y_mm": 0, "z_mm": 1, "temperature_K": 130, "business_id": "B"},
                ])
            elements.write_text("element_id,element_type,n1,n2,n3,n4\n10,TET4,1,2,3,4\n", encoding="utf-8")
            output = root / "native.vtu"
            result = export_temperature_vtu(nodes, elements, output)
            tree = ET.parse(output)
            piece = tree.find("./UnstructuredGrid/Piece")
            temperature = tree.find("./UnstructuredGrid/Piece/PointData/DataArray[@Name='T']")
            self.assertEqual(piece.attrib["NumberOfPoints"], "4")
            self.assertEqual(piece.attrib["NumberOfCells"], "1")
            self.assertEqual(temperature.attrib["Units"], "K")
            self.assertEqual([float(value) for value in temperature.text.split()], [100.0, 110.0, 120.0, 130.0])
            self.assertEqual(result["point_array"], "T")
            self.assertEqual(result["component_temperatures_K"]["A"]["mean_K"], 105.0)
            self.assertEqual(result["component_temperatures_K"]["B"]["max_K"], 130.0)

    def test_rejects_missing_connectivity_node(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nodes = root / "nodes.csv"
            elements = root / "elements.csv"
            nodes.write_text("node_id,x_mm,y_mm,z_mm,temperature_K,business_id\n1,0,0,0,100,A\n", encoding="utf-8")
            elements.write_text("element_id,element_type,n1,n2,n3,n4\n10,TET4,1,2,3,4\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "references missing nodes"):
                export_temperature_vtu(nodes, elements, root / "native.vtu")


class NxStaticContractTest(unittest.TestCase):
    def test_verified_nx12_creation_calls_and_steady_solution_are_preserved(self) -> None:
        solve = (JOURNALS / "build_and_solve.py").read_text(encoding="utf-8")
        self.assertIn('"NX SPACE SYSTEMS THERMAL",\n            "Thermal",', solve)
        self.assertIn("sim.FinalizeCreation(fem, -1, [])", solve)
        self.assertIn('"Space Systems Thermal",\n        solution_name,', solve)
        self.assertIn('solution_name = "steady_state_thermal"', solve)
        self.assertIn('descriptor = "Contact Thermal Coupling"', solve)
        self.assertIn("CreateBcBuilderForSimulationObjectDescriptor(descriptor,", solve)

    def test_solve_and_extract_use_separate_nx_journals_and_resume_is_read_only(self) -> None:
        worker = (SKILL_ROOT / "scripts" / "windows" / "Invoke-NxStage.ps1").read_text(encoding="utf-8")
        starter = (SKILL_ROOT / "scripts" / "windows" / "Start-NxStage.ps1").read_text(encoding="utf-8")
        self.assertIn('Invoke-NxJournal -JournalName "build_and_solve"', worker)
        self.assertIn('Invoke-NxJournal -JournalName "extract_results"', worker)
        self.assertIn("[IO.FileMode]::CreateNew", worker)
        self.assertIn("Get-Process -Id", starter)
        self.assertIn("Read-Utf8JsonWithRetry", starter)
        self.assertIn("catch [IO.IOException]", starter)
        self.assertLess(starter.index("if (Test-Path -LiteralPath $statePath"), starter.index("Start-Process"))

    def test_windows_runner_is_powershell_51_json_and_empty_array_compatible(self) -> None:
        worker = (SKILL_ROOT / "scripts" / "windows" / "Invoke-NxStage.ps1").read_text(encoding="utf-8")
        starter = (SKILL_ROOT / "scripts" / "windows" / "Start-NxStage.ps1").read_text(encoding="utf-8")
        for source in (worker, starter):
            self.assertNotIn("Get-Content -Raw", source)
        self.assertIn("[AllowEmptyCollection()][array]$Artifacts", worker)
        self.assertIn("New-Object Text.UTF8Encoding($false)", worker)
        self.assertIn("Move-Item -LiteralPath $temporary -Destination $Path -Force", worker)
        self.assertNotIn("[IO.File]::Replace", worker)

    def test_model_passes_frozen_manifest_path_and_preserves_journal_error(self) -> None:
        worker = (SKILL_ROOT / "scripts" / "windows" / "Invoke-NxStage.ps1").read_text(encoding="utf-8")
        geometry = (JOURNALS / "build_geometry_master.py").read_text(encoding="utf-8")
        self.assertIn('geometry_manifest_path = Join-Path $script:PackageRoot "cad\\geometry_manifest.json"', worker)
        self.assertIn('manifest_path = task["geometry_manifest_path"]', geometry)
        self.assertNotIn('os.path.join(task["run_dir"], "cad", "geometry_manifest.json")', geometry)
        self.assertIn("$process.Refresh()", worker)
        exit_check = "if ($null -ne $process.ExitCode -and $process.ExitCode -ne 0)"
        self.assertIn(exit_check, worker)
        self.assertLess(worker.index("if (-not $result.ok)"), worker.index(exit_check))

    def test_inline_windows_powershell_reads_json_as_utf8(self) -> None:
        source = (SKILL_ROOT / "scripts" / "packages" / "ugnxsim" / "src" / "ugnxsim" / "linux_control.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("[IO.File]::ReadAllText"), 2)
        self.assertNotIn("Get-Content -Raw -LiteralPath", source)

    def test_finalize_creates_one_mcp_download_bundle(self) -> None:
        worker = (SKILL_ROOT / "scripts" / "windows" / "Invoke-NxStage.ps1").read_text(encoding="utf-8")
        self.assertIn("[IO.Compression.ZipFile]::CreateFromDirectory", worker)
        self.assertIn('($script:RunId + "-return.zip")', worker)
        self.assertIn("$sourceManifest.download_dir", worker)
        self.assertIn("10737418240", worker)

    def test_windows_runtime_uses_frozen_configured_paths(self) -> None:
        worker = (SKILL_ROOT / "scripts" / "windows" / "Invoke-NxStage.ps1").read_text(encoding="utf-8")
        starter = (SKILL_ROOT / "scripts" / "windows" / "Start-NxStage.ps1").read_text(encoding="utf-8")
        geometry = (JOURNALS / "build_geometry_master.py").read_text(encoding="utf-8")
        self.assertIn("$script:RunManifest.run_journal", worker)
        self.assertIn("$manifest.workspace_dir", starter)
        self.assertIn('os.path.join(task["nx_base"], "STEP214UG", "step214ug.def")', geometry)
        old_nx_path = "D:" + r"\Install\NX12"
        old_work_path = "D:" + r"\UG_Test_Path"
        for source in (worker, starter, geometry):
            self.assertNotIn(old_nx_path, source)
            self.assertNotIn(old_work_path, source)

    def test_zero_power_energy_balance_is_valid(self) -> None:
        nxopen = types.ModuleType("NXOpen")
        cae = types.ModuleType("NXOpen.CAE")
        nxopen.CAE = cae
        module_path = JOURNALS / "extract_results.py"
        spec = importlib.util.spec_from_file_location("nx_extract_results_test", module_path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"NXOpen": nxopen, "NXOpen.CAE": cae}):
            spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            balance = module.build_energy_balance(Path(temporary), {"total_power_W": 0}, {})
        self.assertEqual(balance["status"], "zero_input")
        self.assertEqual(balance["input_power_W"], 0.0)
        self.assertIsNone(balance["relative_residual"])

    def test_steady_extraction_reads_only_final_iteration_in_batches(self) -> None:
        extract = (JOURNALS / "extract_results.py").read_text(encoding="utf-8")
        self.assertIn("for iteration in reversed(range(len(iteration_labels)))", extract)
        self.assertIn("RESULT_BATCH_SIZE = 8192", extract)
        self.assertIn("access.AskNodalResult(indices)", extract)
        self.assertNotIn("access.AskNodalResult(row[\"result_node_index\"])", extract)


if __name__ == "__main__":
    unittest.main()
