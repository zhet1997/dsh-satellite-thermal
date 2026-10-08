from __future__ import annotations

import csv
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from pyNastran.bdf.bdf import BDF
from pyNastran.bdf.case_control_deck import CaseControlDeck
from scipy.spatial import ConvexHull, cKDTree

from ..io import read_json, write_json


STRUCTURE_IDS = ("outer_shell", "03_GB_ZY_BJ", "04_GB_FY_BJ")


def load_nodes(path: Path) -> dict[int, dict[str, Any]]:
    rows: dict[int, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            node_id = int(row["node_id"])
            rows[node_id] = {
                "xyz": np.array([float(row["x_mm"]), float(row["y_mm"]), float(row["z_mm"])], dtype=float),
                "business_id": row["business_id"],
            }
    return rows


def load_elements(path: Path, included: set[str]) -> dict[int, dict[str, Any]]:
    rows: dict[int, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["business_id"] not in included:
                continue
            element_id = int(row["element_id"])
            element_type = str(row.get("element_type") or ("HEX8" if row.get("n8") else "TET4")).upper()
            node_columns = (
                ("n1", "n2", "n3", "n4")
                if element_type == "TET4"
                else ("n1", "n2", "n3", "n4", "n5", "n6", "n7", "n8")
            )
            if element_type not in {"TET4", "HEX8"}:
                raise RuntimeError(f"Unsupported structural element type: {element_type}")
            if not all(row.get(name) for name in node_columns):
                raise RuntimeError(f"Incomplete {element_type} connectivity for element {element_id}")
            rows[element_id] = {
                "nodes": [int(row[name]) for name in node_columns],
                "business_id": row["business_id"],
                "element_type": element_type,
            }
    return rows


def face_nodes(
    nodes: dict[int, dict[str, Any]],
    owner_id: str,
    face: dict[str, Any],
    footprint: list[float] | None = None,
    minimum: int = 4,
) -> list[int]:
    axis = int(face["axis"])
    other = [index for index in range(3) if index != axis]
    plane = float(face["plane_value_mm"])
    bounds = footprint if footprint is not None else [float(value) for value in face["bbox_2d_mm"]]
    plane_candidates: list[tuple[int, np.ndarray]] = []
    selected: list[int] = []
    for node_id, row in nodes.items():
        if row["business_id"] != owner_id:
            continue
        xyz = row["xyz"]
        if abs(float(xyz[axis]) - plane) > 1.0e-3:
            continue
        plane_candidates.append((node_id, xyz))
        if bounds[0] - 1.0e-3 <= xyz[other[0]] <= bounds[1] + 1.0e-3 and bounds[2] - 1.0e-3 <= xyz[other[1]] <= bounds[3] + 1.0e-3:
            selected.append(node_id)
    if len(selected) >= minimum:
        return sorted(set(selected))
    if not plane_candidates:
        raise RuntimeError("no mesh nodes found on semantic face %s" % face["face_id"])
    center = np.array([(bounds[0] + bounds[1]) * 0.5, (bounds[2] + bounds[3]) * 0.5])
    ranked = sorted(
        plane_candidates,
        key=lambda item: float(np.linalg.norm(item[1][other] - center)),
    )
    return sorted(item[0] for item in ranked[: max(minimum, min(12, len(ranked)))])


def center_of_bbox(body: dict[str, Any]) -> np.ndarray:
    return (np.asarray(body["bbox"]["min"], dtype=float) + np.asarray(body["bbox"]["max"], dtype=float)) * 0.5


def build_bdf(run_dir: Path) -> dict[str, Any]:
    inputs_dir = run_dir / "source_inputs"
    semantics = read_json(inputs_dir / "geometry_semantics.json")
    case = read_json(inputs_dir / "structural_case.json")
    structure_ids = set(case.get("structure_body_ids", STRUCTURE_IDS))
    all_nodes = load_nodes(inputs_dir / "nodes_geometry.csv")
    elements = load_elements(inputs_dir / "elements.csv", structure_ids)
    used_node_ids = {node_id for element in elements.values() for node_id in element["nodes"]}
    nodes = {node_id: all_nodes[node_id] for node_id in used_node_ids}
    bodies = {row["business_id"]: row for row in semantics["bodies"]}
    faces = {row["face_id"]: row for row in semantics["faces"]}

    model = BDF(debug=False)
    model.sol = 101
    control_lines = [
        "TITLE = NX structural satellite acceleration cases",
        "ECHO = NONE",
        "DISPLACEMENT(PLOT) = ALL",
        "STRESS(PLOT,VONMISES) = ALL",
        "STRAIN(PLOT,VONMISES) = ALL",
        "SPCFORCES(PLOT) = ALL",
    ]
    for index, load_case in enumerate(case["load_cases"], start=1):
        control_lines.extend(
            (
                "SUBCASE %d" % index,
                "  LABEL = %s" % load_case.get("name", load_case["id"]),
                "  SPC = 1",
                "  LOAD = %d" % (100 + index),
            )
        )
    model.case_control_deck = CaseControlDeck(control_lines, log=model.log)
    for node_id in sorted(nodes):
        model.add_grid(node_id, nodes[node_id]["xyz"].tolist())

    material = case["materials"]["aluminum_6061"]
    model.add_mat1(
        1,
        float(material["youngs_modulus_MPa"]),
        None,
        float(material["poissons_ratio"]),
        rho=float(material["density_kg_m3"]) * 1.0e-12,
    )
    model.add_psolid(1, 1)
    for element_id in sorted(elements):
        row = elements[element_id]
        if row["element_type"] == "TET4":
            model.add_ctetra(element_id, 1, row["nodes"])
        else:
            model.add_chexa(element_id, 1, row["nodes"])

    mesh_volume_mm3 = 0.0
    for element in elements.values():
        xyz = np.array([nodes[node_id]["xyz"] for node_id in element["nodes"]], dtype=float)
        if element["element_type"] == "TET4":
            mesh_volume_mm3 += abs(float(np.linalg.det(np.vstack((xyz[1:] - xyz[0]))))) / 6.0
        else:
            mesh_volume_mm3 += float(ConvexHull(xyz).volume)
    mesh_structure_mass_kg = mesh_volume_mm3 * float(material["density_kg_m3"]) * 1.0e-9

    next_node = max(nodes) + 1
    next_element = max(elements) + 1
    component_rows = []
    component_node_ids: dict[str, int] = {}
    for contact in semantics["contacts"]:
        if contact["kind"] != "component_mount":
            continue
        component_id = faces[contact["source_face_id"]]["owner_id"]
        body = bodies[component_id]
        mass_kg = float(body.get("mass_kg") or 0.0)
        if mass_kg <= 0.0:
            continue
        source = faces[contact["source_face_id"]]
        target = faces[contact["target_face_id"]]
        mount_nodes = face_nodes(
            nodes,
            target["owner_id"],
            target,
            footprint=[float(value) for value in source["bbox_2d_mm"]],
        )
        center_node = next_node
        next_node += 1
        center = center_of_bbox(body)
        model.add_grid(center_node, center.tolist(), comment="component mass %s" % component_id)
        model.add_conm2(next_element, center_node, mass_kg / 1000.0, comment="mass tonne")
        mass_element = next_element
        next_element += 1
        model.add_rbe3(
            next_element,
            center_node,
            "123",
            [1.0],
            ["123"],
            [mount_nodes],
            comment="distributed mount %s" % component_id,
        )
        spider_element = next_element
        next_element += 1
        component_node_ids[component_id] = center_node
        component_rows.append(
            {
                "business_id": component_id,
                "mass_kg": mass_kg,
                "center_node_id": center_node,
                "mass_element_id": mass_element,
                "spider_element_id": spider_element,
                "mount_node_count": len(mount_nodes),
                "target_face_id": target["face_id"],
            }
        )

    tie_rows = []
    used_wall_dependents: set[int] = set()
    wall_ties = []
    for wall_id in case.get("wall_tie_body_ids", ("03_GB_ZY_BJ", "04_GB_FY_BJ")):
        safe_wall = "OBJ_" + wall_id
        for axis_name in ("XMIN", "XMAX", "ZMIN", "ZMAX"):
            wall_ties.append(
                (
                    "STRUCTURAL_TIE_%s_%s" % (wall_id, axis_name),
                    faces["WALL_%s_%s" % (safe_wall, axis_name)],
                    faces["SHELL_INNER_%s" % axis_name],
                )
            )
    for contact_id, source, target in wall_ties:
        source_nodes = face_nodes(nodes, source["owner_id"], source)
        source_nodes = [node_id for node_id in source_nodes if node_id not in used_wall_dependents]
        if not source_nodes:
            continue
        target_nodes = face_nodes(nodes, target["owner_id"], target)
        target_xyz = np.array([nodes[node_id]["xyz"] for node_id in target_nodes])
        tree = cKDTree(target_xyz)
        pairs: dict[int, list[int]] = defaultdict(list)
        distances = []
        for source_node in source_nodes:
            distance, index = tree.query(nodes[source_node]["xyz"])
            pairs[target_nodes[int(index)]].append(source_node)
            distances.append(float(distance))
        for independent, dependent in sorted(pairs.items()):
            model.add_rbe2(next_element, independent, "123", sorted(set(dependent)), comment=contact_id)
            next_element += 1
        used_wall_dependents.update(source_nodes)
        tie_rows.append(
            {
                "contact_id": contact_id,
                "source_node_count": len(source_nodes),
                "target_node_count": len(target_nodes),
                "rbe2_count": len(pairs),
                "max_pair_distance_mm": max(distances) if distances else None,
            }
        )

    interface = case["launch_interface"]
    interface_face = faces[interface["constraint_face_id"]]
    constrained_nodes = face_nodes(
        nodes,
        interface_face["owner_id"],
        interface_face,
        footprint=[float(value) for value in interface["footprint_xy_mm"]],
        minimum=12,
    )
    model.add_spc1(1, "123456", constrained_nodes, comment="P001 launch interface footprint")
    center_rotation_nodes = sorted(component_node_ids.values())
    if center_rotation_nodes:
        model.add_spc1(1, "456", center_rotation_nodes, comment="suppress unused CONM2 center rotations")
    gravity = float(case["gravity_mm_s2"])
    for index, load_case in enumerate(case["load_cases"], start=1):
        model.add_grav(100 + index, gravity, [float(value) for value in load_case["acceleration_g"]])
    model.add_param("POST", [-1])
    model.add_param("AUTOSPC", ["YES"])
    model.add_param("GRDPNT", [0])

    deck_path = run_dir / "nx" / "structural_case.bdf"
    model.write_bdf(deck_path, size=16, is_double=False, interspersed=False, enddata=True)
    node_business = {str(node_id): row["business_id"] for node_id, row in nodes.items()}
    node_business.update({str(node_id): "MASS_" + component_id for component_id, node_id in component_node_ids.items()})
    metadata = {
        "schema_version": "1.0",
        "deck_path": str(deck_path),
        "structure_node_count": len(nodes),
        "structure_element_count": len(elements),
        "element_type_counts": {
            element_type: sum(1 for row in elements.values() if row["element_type"] == element_type)
            for element_type in ("TET4", "HEX8")
        },
        "component_mass_count": len(component_rows),
        "component_mass_kg": sum(row["mass_kg"] for row in component_rows),
        "cad_structure_mass_kg": float(case["structure_mass_kg"]),
        "mesh_structure_mass_kg": mesh_structure_mass_kg,
        "modeled_total_mass_kg": mesh_structure_mass_kg + sum(row["mass_kg"] for row in component_rows),
        "constrained_node_ids": constrained_nodes,
        "components": component_rows,
        "ties": tie_rows,
        "node_business_ids": node_business,
        "element_business_ids": {str(element_id): row["business_id"] for element_id, row in elements.items()},
    }
    write_json(run_dir / "nx" / "deck_report.json", metadata)
    return {
        "ok": True,
        "deck_path": str(deck_path),
        "structure_node_count": len(nodes),
        "structure_element_count": len(elements),
        "component_mass_count": len(component_rows),
        "component_mass_kg": metadata["component_mass_kg"],
        "mesh_structure_mass_kg": mesh_structure_mass_kg,
        "modeled_total_mass_kg": metadata["modeled_total_mass_kg"],
        "constrained_node_count": len(constrained_nodes),
        "ties": tie_rows,
    }
