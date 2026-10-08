from __future__ import annotations

import csv
import math
import os
import tempfile
import xml.etree.ElementTree as ET
from collections import OrderedDict
from pathlib import Path
from typing import Any


# VTK XML UnstructuredGrid cell type ids.
ELEMENT_TYPES: dict[str, tuple[str, int, int]] = {
    "TET4": ("tetra", 4, 10),
    "CTETRA": ("tetra", 4, 10),
    "TET10": ("tetra10", 10, 24),
    "CTETRA10": ("tetra10", 10, 24),
    "HEX8": ("hexahedron", 8, 12),
    "CHEXA": ("hexahedron", 8, 12),
    "HEX20": ("hexahedron20", 20, 25),
    "CHEXA20": ("hexahedron20", 20, 25),
    "WEDGE6": ("wedge", 6, 13),
    "PENTA6": ("wedge", 6, 13),
    "CPENTA": ("wedge", 6, 13),
    "WEDGE15": ("wedge15", 15, 26),
    "PENTA15": ("wedge15", 15, 26),
    "PYRAMID5": ("pyramid", 5, 14),
    "CPYRAM": ("pyramid", 5, 14),
    "PYRAMID13": ("pyramid13", 13, 27),
    "TRI3": ("triangle", 3, 5),
    "CTRIA3": ("triangle", 3, 5),
    "TRI6": ("triangle6", 6, 22),
    "CTRIA6": ("triangle6", 6, 22),
    "QUAD4": ("quad", 4, 9),
    "CQUAD4": ("quad", 4, 9),
    "QUAD8": ("quad8", 8, 23),
    "CQUAD8": ("quad8", 8, 23),
}


def _finite(row: dict[str, str], field: str, label: str) -> float:
    try:
        value = float(row[field])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field} for {label}") from exc
    if not math.isfinite(value):
        raise ValueError(f"Non-finite {field} for {label}")
    return value


def _node_id(value: object, label: str) -> int:
    try:
        result = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid node id for {label}: {value!r}") from exc
    if result <= 0:
        raise ValueError(f"Node id must be positive for {label}: {result}")
    return result


def _read_nodes(path: Path) -> tuple[list[list[float]], list[float], list[str], dict[int, int]]:
    points: list[list[float]] = []
    temperatures: list[float] = []
    business_ids: list[str] = []
    point_index: dict[int, int] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row_index, row in enumerate(csv.DictReader(stream), start=2):
            label = f"node row {row_index}"
            node_id = _node_id(row.get("node_id"), label)
            if node_id in point_index:
                raise ValueError(f"Duplicate node id in {path}: {node_id}")
            point_index[node_id] = len(points)
            points.append([
                _finite(row, "x_mm", label),
                _finite(row, "y_mm", label),
                _finite(row, "z_mm", label),
            ])
            temperature = _finite(row, "temperature_K", label)
            if temperature <= 0.0:
                raise ValueError(f"Non-positive Kelvin temperature for {label}")
            temperatures.append(temperature)
            business_ids.append(str(row.get("business_id") or "__unassigned__"))
    if not points:
        raise ValueError(f"Thermal VTU requires at least one node: {path}")
    return points, temperatures, business_ids, point_index


def _element_type(row: dict[str, str], label: str) -> tuple[str, int, int]:
    raw = str(row.get("element_type") or "").strip().upper()
    if not raw:
        raw = "HEX8" if row.get("n8") else "TET4"
    try:
        return ELEMENT_TYPES[raw]
    except KeyError as exc:
        raise ValueError(f"Unsupported thermal element type {raw!r} for {label}") from exc


def _read_cells(
    path: Path, point_index: dict[int, int]
) -> tuple[list[int], list[int], list[int], OrderedDict[str, int]]:
    connectivity: list[int] = []
    offsets: list[int] = []
    vtk_types: list[int] = []
    type_counts: OrderedDict[str, int] = OrderedDict()
    element_ids: set[int] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row_index, row in enumerate(csv.DictReader(stream), start=2):
            label = f"element row {row_index}"
            try:
                element_id = int(str(row.get("element_id")))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid element_id for {label}") from exc
            if element_id in element_ids:
                raise ValueError(f"Duplicate element id in {path}: {element_id}")
            element_ids.add(element_id)
            type_name, node_count, vtk_type = _element_type(row, f"element {element_id}")
            node_ids = [_node_id(row.get(f"n{index}"), f"element {element_id}") for index in range(1, node_count + 1)]
            if len(set(node_ids)) != node_count:
                raise ValueError(f"Element {element_id} contains duplicate node ids")
            missing = [node_id for node_id in node_ids if node_id not in point_index]
            if missing:
                raise ValueError(f"Element {element_id} references missing nodes: {missing}")
            connectivity.extend(point_index[node_id] for node_id in node_ids)
            offsets.append(len(connectivity))
            vtk_types.append(vtk_type)
            type_counts[type_name] = type_counts.get(type_name, 0) + 1
    if not element_ids:
        raise ValueError(f"Thermal VTU requires at least one element: {path}")
    return connectivity, offsets, vtk_types, type_counts


def _numbers(values: list[float] | list[int]) -> str:
    return " ".join(format(value, ".17g") if isinstance(value, float) else str(value) for value in values)


def _data_array(parent: ET.Element, *, data_type: str, name: str | None, values: str, **attributes: str) -> None:
    options = {"type": data_type, "format": "ascii", **attributes}
    if name is not None:
        options["Name"] = name
    ET.SubElement(parent, "DataArray", options).text = values


def export_temperature_vtu(nodes_path: Path, elements_path: Path, output_path: Path) -> dict[str, Any]:
    points, temperatures, business_ids, point_index = _read_nodes(nodes_path)
    connectivity, offsets, vtk_types, type_counts = _read_cells(elements_path, point_index)
    root = ET.Element(
        "VTKFile",
        {"type": "UnstructuredGrid", "version": "0.1", "byte_order": "LittleEndian"},
    )
    grid = ET.SubElement(root, "UnstructuredGrid")
    piece = ET.SubElement(
        grid,
        "Piece",
        {"NumberOfPoints": str(len(points)), "NumberOfCells": str(len(offsets))},
    )
    point_data = ET.SubElement(piece, "PointData", {"Scalars": "T"})
    _data_array(point_data, data_type="Float64", name="T", values=_numbers(temperatures), Units="K")
    ET.SubElement(piece, "CellData")
    point_element = ET.SubElement(piece, "Points")
    flat_points = [coordinate for point in points for coordinate in point]
    _data_array(
        point_element,
        data_type="Float64",
        name=None,
        values=_numbers(flat_points),
        NumberOfComponents="3",
        Units="mm",
    )
    cells = ET.SubElement(piece, "Cells")
    _data_array(cells, data_type="Int64", name="connectivity", values=_numbers(connectivity))
    _data_array(cells, data_type="Int64", name="offsets", values=_numbers(offsets))
    _data_array(cells, data_type="UInt8", name="types", values=_numbers(vtk_types))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=output_path.name + ".", suffix=".tmp", dir=output_path.parent)
    os.close(descriptor)
    try:
        ET.ElementTree(root).write(temporary, encoding="utf-8", xml_declaration=True)
        os.replace(temporary, output_path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    if output_path.stat().st_size <= 0:
        raise RuntimeError(f"Thermal VTU export did not create a non-empty file: {output_path}")
    grouped: dict[str, list[float]] = {}
    for business_id, temperature in zip(business_ids, temperatures):
        grouped.setdefault(business_id, []).append(temperature)
    component_temperatures = {
        business_id: {
            "node_count": len(values),
            "min_K": min(values),
            "max_K": max(values),
            "mean_K": sum(values) / len(values),
        }
        for business_id, values in sorted(grouped.items())
    }
    return {
        "path": str(output_path),
        "size_bytes": output_path.stat().st_size,
        "point_count": len(points),
        "cell_count": len(offsets),
        "cell_types": dict(type_counts),
        "point_array": "T",
        "temperature_unit": "K",
        "coordinate_unit": "mm",
        "temperature_min_K": min(temperatures),
        "temperature_max_K": max(temperatures),
        "temperature_mean_K": sum(temperatures) / len(temperatures),
        "component_temperatures_K": component_temperatures,
    }
