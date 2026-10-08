from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any

import meshio
import numpy as np
from pyNastran.bdf.bdf import read_bdf
from pyNastran.op2.op2 import OP2
from scipy.spatial import cKDTree

from ..io import read_json, write_json


SOLID_TYPES = (
    ("CTETRA", "ctetra", "tetra"),
    ("CHEXA", "chexa", "hexahedron"),
)


def _solid_table(op2: OP2, result_kind: str, element_key: str, subcase_id: int):
    attribute = f"{element_key}_{result_kind}"
    containers = [
        getattr(op2, attribute, None),
        getattr(getattr(getattr(op2, "op2_results", None), result_kind, None), attribute, None),
    ]
    for container in containers:
        if container and subcase_id in container:
            return container[subcase_id]
    return None


def _solid_equivalent_by_element(table: Any, model: Any, element_type: str) -> dict[int, float]:
    headers = list(table.get_headers())
    equivalent_index = headers.index("von_mises") if "von_mises" in headers else len(headers) - 1
    result: dict[int, float] = {}
    for element_node, values in zip(table.element_node, table.data[0]):
        element_id = int(element_node[0])
        if element_id not in model.elements or model.elements[element_id].type != element_type:
            continue
        value = float(values[equivalent_index])
        if math.isfinite(value):
            result[element_id] = max(result.get(element_id, -math.inf), value)
    return result


def _all_solid_equivalent(op2: OP2, model: Any, result_kind: str, subcase_id: int) -> dict[int, float]:
    result: dict[int, float] = {}
    found: list[str] = []
    for element_type, element_key, _ in SOLID_TYPES:
        table = _solid_table(op2, result_kind, element_key, subcase_id)
        if table is None:
            continue
        found.append(element_type)
        result.update(_solid_equivalent_by_element(table, model, element_type))
    if not found:
        raise RuntimeError(f"OP2 has no CTETRA/CHEXA {result_kind} for subcase {subcase_id}")
    return result


def _table_for_case(container: dict[int, Any], subcase_id: int, name: str):
    if subcase_id not in container:
        raise RuntimeError(f"OP2 has no {name} for subcase {subcase_id}")
    return container[subcase_id]


def _read_op2(path: Path) -> OP2:
    op2 = OP2(debug=False, log=None)
    op2.read_op2(path, combine=True, build_dataframe=False)
    return op2


def _write_node_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_element_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def export_results(run_dir: Path) -> dict[str, Any]:
    deck_report = read_json(run_dir / "nx" / "deck_report.json")
    case = read_json(run_dir / "source_inputs" / "structural_case.json")
    deck_path = run_dir / "nx" / "structural_case.bdf"
    op2_path = deck_path.with_suffix(".op2")
    if not op2_path.exists():
        raise FileNotFoundError(f"OP2 result is missing: {op2_path}")
    model = read_bdf(deck_path, xref=False, validate=False, debug=False)
    op2 = _read_op2(op2_path)
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    all_node_ids = sorted(model.nodes)
    point_index = {node_id: index for index, node_id in enumerate(all_node_ids)}
    points = np.array([model.nodes[node_id].xyz for node_id in all_node_ids], dtype=float)
    element_ids_by_type = {
        element_type: sorted(
            element_id for element_id, element in model.elements.items() if element.type == element_type
        )
        for element_type, _, _ in SOLID_TYPES
    }
    solid_ids = sorted(element_id for values in element_ids_by_type.values() for element_id in values)
    element_business = deck_report["element_business_ids"]
    node_business = deck_report["node_business_ids"]
    constrained_ids = [int(value) for value in deck_report["constrained_node_ids"]]
    constraint_tree = cKDTree(np.array([model.nodes[node_id].xyz for node_id in constrained_ids], dtype=float))
    exclude_distance = float(case["reporting"]["exclude_constraint_singularity_mm"])
    allowable = float(case["materials"]["aluminum_6061"]["allowable_stress_MPa"])
    total_mass_tonne = float(deck_report["modeled_total_mass_kg"]) / 1000.0

    summaries: dict[str, Any] = {}
    reaction_rows: dict[str, Any] = {}
    safety_rows: dict[str, Any] = {}
    for subcase_id, load_case in enumerate(case["load_cases"], start=1):
        displacement = _table_for_case(op2.displacements, subcase_id, "displacement")
        disp_by_node = {
            int(node_id): np.asarray(values[:3], dtype=float)
            for node_id, values in zip(displacement.node_gridtype[:, 0], displacement.data[0])
        }
        node_rows = []
        point_displacement = np.zeros((len(all_node_ids), 3), dtype=float)
        for node_id in all_node_ids:
            vector = disp_by_node.get(node_id, np.zeros(3))
            point_displacement[point_index[node_id]] = vector
            xyz = model.nodes[node_id].xyz
            node_rows.append(
                {
                    "node_id": node_id,
                    "business_id": node_business.get(str(node_id), "unknown"),
                    "x_mm": float(xyz[0]),
                    "y_mm": float(xyz[1]),
                    "z_mm": float(xyz[2]),
                    "ux_mm": float(vector[0]),
                    "uy_mm": float(vector[1]),
                    "uz_mm": float(vector[2]),
                    "displacement_magnitude_mm": float(np.linalg.norm(vector)),
                }
            )

        vm_by_element = _all_solid_equivalent(op2, model, "stress", subcase_id)
        strain_by_element = _all_solid_equivalent(op2, model, "strain", subcase_id)
        element_rows = []
        stress_by_id: dict[int, float] = {}
        strain_by_id: dict[int, float] = {}
        for element_id in solid_ids:
            node_ids = model.elements[element_id].node_ids
            centroid = np.mean(np.array([model.nodes[node_id].xyz for node_id in node_ids]), axis=0)
            vm = float(vm_by_element.get(element_id, math.nan))
            equivalent_strain = float(strain_by_element.get(element_id, math.nan))
            distance = float(constraint_tree.query(centroid)[0])
            stress_by_id[element_id] = vm
            strain_by_id[element_id] = equivalent_strain
            element_rows.append(
                {
                    "element_id": element_id,
                    "element_type": model.elements[element_id].type,
                    "business_id": element_business.get(str(element_id), "unknown"),
                    "centroid_x_mm": float(centroid[0]),
                    "centroid_y_mm": float(centroid[1]),
                    "centroid_z_mm": float(centroid[2]),
                    "distance_to_constraint_mm": distance,
                    "von_mises_MPa": vm,
                    "equivalent_strain": equivalent_strain,
                    "equivalent_strain_microstrain": equivalent_strain * 1.0e6,
                }
            )

        key = load_case["id"]
        prefix = key.lower()
        _write_node_csv(results_dir / f"{prefix}_displacement_nodes.csv", node_rows)
        _write_element_csv(results_dir / f"{prefix}_stress_elements.csv", element_rows)
        magnitudes = np.linalg.norm(point_displacement, axis=1)
        cell_blocks = []
        stress_blocks = []
        strain_blocks = []
        for element_type, _, meshio_type in SOLID_TYPES:
            element_ids = element_ids_by_type[element_type]
            if not element_ids:
                continue
            cell_blocks.append(
                (
                    meshio_type,
                    np.asarray(
                        [
                            [point_index[node_id] for node_id in model.elements[element_id].node_ids]
                            for element_id in element_ids
                        ],
                        dtype=int,
                    ),
                )
            )
            stress_blocks.append(np.asarray([stress_by_id[element_id] for element_id in element_ids], dtype=float))
            strain_blocks.append(np.asarray([strain_by_id[element_id] for element_id in element_ids], dtype=float))
        meshio.write(
            results_dir / f"{prefix}.vtu",
            meshio.Mesh(
                points=points,
                cells=cell_blocks,
                point_data={"displacement_mm": point_displacement, "displacement_magnitude_mm": magnitudes},
                cell_data={
                    "von_mises_MPa": stress_blocks,
                    "equivalent_strain": strain_blocks,
                    "equivalent_strain_microstrain": [values * 1.0e6 for values in strain_blocks],
                },
            ),
        )
        finite_element_rows = [row for row in element_rows if math.isfinite(float(row["von_mises_MPa"]))]
        raw_row = max(finite_element_rows, key=lambda row: float(row["von_mises_MPa"]))
        filtered_element_rows = [
            row for row in finite_element_rows if float(row["distance_to_constraint_mm"]) > exclude_distance
        ]
        filtered_row = max(
            filtered_element_rows or finite_element_rows,
            key=lambda row: float(row["von_mises_MPa"]),
        )
        raw_max = float(raw_row["von_mises_MPa"])
        filtered_max = float(filtered_row["von_mises_MPa"])
        strain_row = max(
            (row for row in element_rows if math.isfinite(float(row["equivalent_strain"]))),
            key=lambda row: float(row["equivalent_strain"]),
        )
        max_disp_index = int(np.nanargmax(magnitudes))
        max_disp_node_id = all_node_ids[max_disp_index]
        summaries[key] = {
            "name": load_case.get("name", load_case["id"]),
            "node_count": len(all_node_ids),
            "element_type_counts": {
                element_type: len(element_ids_by_type[element_type])
                for element_type, _, _ in SOLID_TYPES
                if element_ids_by_type[element_type]
            },
            "max_displacement_mm": float(magnitudes[max_disp_index]),
            "max_displacement_node_id": max_disp_node_id,
            "max_displacement_business_id": node_business.get(str(max_disp_node_id), "unknown"),
            "max_displacement_xyz_mm": points[max_disp_index].tolist(),
            "max_von_mises_raw_MPa": raw_max,
            "max_von_mises_raw_element_id": int(raw_row["element_id"]),
            "max_von_mises_raw_business_id": raw_row["business_id"],
            "max_von_mises_raw_xyz_mm": [
                float(raw_row["centroid_x_mm"]),
                float(raw_row["centroid_y_mm"]),
                float(raw_row["centroid_z_mm"]),
            ],
            "max_von_mises_filtered_MPa": filtered_max,
            "max_von_mises_filtered_element_id": int(filtered_row["element_id"]),
            "max_von_mises_filtered_business_id": filtered_row["business_id"],
            "max_von_mises_filtered_xyz_mm": [
                float(filtered_row["centroid_x_mm"]),
                float(filtered_row["centroid_y_mm"]),
                float(filtered_row["centroid_z_mm"]),
            ],
            "max_equivalent_strain": float(strain_row["equivalent_strain"]),
            "max_equivalent_strain_microstrain": float(strain_row["equivalent_strain_microstrain"]),
            "max_equivalent_strain_element_id": int(strain_row["element_id"]),
            "max_equivalent_strain_business_id": strain_row["business_id"],
            "max_equivalent_strain_xyz_mm": [
                float(strain_row["centroid_x_mm"]),
                float(strain_row["centroid_y_mm"]),
                float(strain_row["centroid_z_mm"]),
            ],
            "stress_filter_distance_mm": exclude_distance,
        }
        safety_rows[key] = {
            "allowable_stress_MPa": allowable,
            "raw_reserve_factor": allowable / raw_max if raw_max > 0.0 else None,
            "raw_margin_of_safety": allowable / raw_max - 1.0 if raw_max > 0.0 else None,
            "filtered_reserve_factor": allowable / filtered_max if filtered_max > 0.0 else None,
            "filtered_margin_of_safety": allowable / filtered_max - 1.0 if filtered_max > 0.0 else None,
        }

        spc = _table_for_case(op2.spc_forces, subcase_id, "SPC force")
        reaction = np.sum(np.asarray(spc.data[0, :, :3], dtype=float), axis=0)
        acceleration = np.asarray(load_case["acceleration_g"], dtype=float) * float(case["gravity_mm_s2"])
        expected = -total_mass_tonne * acceleration
        residual = reaction - expected
        denominator = max(float(np.linalg.norm(expected)), 1.0e-12)
        reaction_rows[key] = {
            "reaction_N": reaction.tolist(),
            "expected_reaction_N": expected.tolist(),
            "residual_N": residual.tolist(),
            "relative_vector_residual": float(np.linalg.norm(residual) / denominator),
        }

    summary = {
        "schema_version": "1.0",
        "modeled_total_mass_kg": float(deck_report["modeled_total_mass_kg"]),
        "element_type_counts": {
            element_type: len(element_ids_by_type[element_type])
            for element_type, _, _ in SOLID_TYPES
            if element_ids_by_type[element_type]
        },
        "subcases": summaries,
    }
    write_json(results_dir / "summary.json", summary)
    write_json(results_dir / "reaction_balance.json", reaction_rows)
    write_json(results_dir / "safety_report.json", safety_rows)
    return {"ok": True, **summary, "reaction_balance": reaction_rows, "safety": safety_rows}
