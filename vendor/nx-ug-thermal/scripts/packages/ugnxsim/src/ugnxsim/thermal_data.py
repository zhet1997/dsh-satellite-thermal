from __future__ import annotations

import csv
import math
import re
from pathlib import Path
from typing import Any

from .io import file_record, read_json, sha256_file, write_json


INPUT_SCHEMA = "nx-ug-thermal-inputs/1"
SEMANTICS_SCHEMA = "nx-ug-thermal-geometry-semantics/2"
SOURCE_SCHEMA = "nx-ug-thermal-source-manifest/1"
ASSUMPTIONS_SCHEMA = "nx-ug-thermal-assumptions/1"
STATUSES = {"provided", "derived", "estimated", "global_default"}
FACE_NAMES = ("x-", "x+", "y-", "y+", "z-", "z+")
DEFAULTS = {
    "power_W": 0.0,
    "conductivity_W_mK": 167.0,
    "emissivity": 0.8,
    "contact_resistance_m2K_W": 0.001,
    "external_heat_flux_W_m2": 0.0,
    "initial_temperature_K": 100.0,
    "space_temperature_K": 3.0,
    "view_factor": 1.0,
    "density_kg_m3": 2700.0,
    "heat_capacity_J_kgK": 896.0,
}


def _quantity(value: float, unit: str, status: str, source: str, method: str) -> dict[str, Any]:
    if status not in STATUSES:
        raise ValueError(f"Unsupported thermal input status: {status}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"Thermal input must be finite: {source}")
    return {
        "value": number,
        "unit": unit,
        "status": status,
        "source_locator": source,
        "method": method,
    }


def _default_quantity(name: str, unit: str) -> dict[str, Any]:
    return _quantity(DEFAULTS[name], unit, "global_default", f"config.defaults.{name}", "demo continuity default")


def _provided_quantity(value: Any, unit: str, source: str) -> dict[str, Any]:
    return _quantity(float(value), unit, "provided", source, "explicit source value")


def _global_quantity(
    overrides: dict[str, Any], names: tuple[str, ...], default: str, unit: str
) -> dict[str, Any]:
    global_values = overrides.get("global") if isinstance(overrides.get("global"), dict) else {}
    for name in names:
        if name in global_values:
            return _provided_quantity(global_values[name], unit, f"thermal-overrides.json#/global/{name}")
    return _default_quantity(default, unit)


def _safe_id(value: object) -> str:
    text = re.sub(r"[^A-Za-z0-9_]+", "_", str(value)).strip("_")
    if not text:
        raise ValueError(f"Cannot create an ASCII identifier from {value!r}")
    return text.upper()


def _bbox(values: object, label: str) -> dict[str, list[float]]:
    if not isinstance(values, list) or len(values) != 6:
        raise ValueError(f"{label} must contain six bounds")
    numbers = [float(value) for value in values]
    if not all(math.isfinite(value) for value in numbers):
        raise ValueError(f"{label} contains a non-finite value")
    return {"min": numbers[:3], "max": numbers[3:]}


def _face(owner_id: str, bbox: dict[str, list[float]], axis: int, side: int, role: str) -> dict[str, Any]:
    others = [index for index in range(3) if index != axis]
    suffix = "MIN" if side < 0 else "MAX"
    return {
        "face_id": f"FACE_{_safe_id(owner_id)}_{'XYZ'[axis]}{suffix}",
        "owner_id": owner_id,
        "axis": axis,
        "axis_name": "xyz"[axis],
        "side": "min" if side < 0 else "max",
        "normal_sign": side,
        "plane_value_mm": bbox["min" if side < 0 else "max"][axis],
        "bbox_2d_mm": [
            bbox["min"][others[0]], bbox["max"][others[0]],
            bbox["min"][others[1]], bbox["max"][others[1]],
        ],
        "aliases": [],
        "role": role,
    }


def _axis_side(value: object, fallback: tuple[int, int] = (2, -1)) -> tuple[int, int]:
    if isinstance(value, str):
        token = value.strip().upper().replace(" ", "")
        suffix_match = re.search(r"([XYZ])_?(MIN|MAX|NEG|POS)(?:$|_)", token)
        if suffix_match:
            axis = "XYZ".index(suffix_match.group(1))
            return axis, -1 if suffix_match.group(2) in {"MIN", "NEG"} else 1
        match = re.search(r"(NEG|POS|[-+<>])_?([XYZ])|([XYZ])([-+])", token)
        if match:
            if match.group(2):
                axis = "XYZ".index(match.group(2))
                sign_token = match.group(1)
                return axis, -1 if sign_token in {"NEG", "-", "<"} else 1
            axis = "XYZ".index(match.group(3))
            return axis, -1 if match.group(4) == "-" else 1
    if isinstance(value, list) and len(value) == 3:
        numbers = [float(item) for item in value]
        axis = max(range(3), key=lambda index: abs(numbers[index]))
        return axis, -1 if numbers[axis] < 0 else 1
    return fallback


def _vec(values: object, label: str) -> list[float]:
    if not isinstance(values, list) or len(values) != 3:
        raise ValueError(f"{label} must contain three numbers")
    result = [float(value) for value in values]
    if not all(math.isfinite(value) for value in result):
        raise ValueError(f"{label} contains a non-finite value")
    return result


def _matrix(values: object, label: str) -> list[list[float]]:
    if not isinstance(values, list) or len(values) != 3:
        raise ValueError(f"{label} must contain three rows")
    return [_vec(row, f"{label}[{index}]") for index, row in enumerate(values)]


def _transform(rotation: list[list[float]], point: list[float], translation: list[float]) -> list[float]:
    return [sum(rotation[row][column] * point[column] for column in range(3)) + translation[row] for row in range(3)]


def _rotate(rotation: list[list[float]], vector: list[float]) -> list[float]:
    return [sum(rotation[row][column] * vector[column] for column in range(3)) for row in range(3)]


def _dot(left: list[float], right: list[float]) -> float:
    return sum(left[index] * right[index] for index in range(3))


def _normal(vector: list[float]) -> list[float]:
    length = math.sqrt(_dot(vector, vector))
    if length <= 0.0:
        raise ValueError("zero-length face normal")
    return [value / length for value in vector]


def _bounds_from_vertices(vertices: list[list[float]]) -> dict[str, list[float]]:
    return {
        "min": [min(point[index] for point in vertices) for index in range(3)],
        "max": [max(point[index] for point in vertices) for index in range(3)],
    }


def _box_vertices(dimensions: list[float], rotation: list[list[float]], translation: list[float]) -> list[list[float]]:
    half = [value / 2.0 for value in dimensions]
    return [
        _transform(rotation, [sx * half[0], sy * half[1], sz * half[2]], translation)
        for sx in (-1.0, 1.0) for sy in (-1.0, 1.0) for sz in (-1.0, 1.0)
    ]


def _box_face_vertices(
    dimensions: list[float], rotation: list[list[float]], translation: list[float], token: str
) -> tuple[list[list[float]], list[float]]:
    axis, side = _axis_side(token)
    half = [value / 2.0 for value in dimensions]
    others = [index for index in range(3) if index != axis]
    vertices = []
    for first, second in ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)):
        local = [0.0, 0.0, 0.0]
        local[axis] = side * half[axis]
        local[others[0]] = first * half[others[0]]
        local[others[1]] = second * half[others[1]]
        vertices.append(_transform(rotation, local, translation))
    local_normal = [0.0, 0.0, 0.0]
    local_normal[axis] = float(side)
    return vertices, _normal(_rotate(rotation, local_normal))


def _planar_face(owner_id: str, face_id: str, vertices: list[list[float]], normal: list[float], role: str) -> dict[str, Any]:
    center = [sum(point[index] for point in vertices) / len(vertices) for index in range(3)]
    edge_a = [vertices[1][index] - vertices[0][index] for index in range(3)]
    edge_b = [vertices[-1][index] - vertices[0][index] for index in range(3)]
    cross = [
        edge_a[1] * edge_b[2] - edge_a[2] * edge_b[1],
        edge_a[2] * edge_b[0] - edge_a[0] * edge_b[2],
        edge_a[0] * edge_b[1] - edge_a[1] * edge_b[0],
    ]
    area = math.sqrt(_dot(cross, cross))
    return {
        "face_id": face_id,
        "owner_id": owner_id,
        "normal": _normal(normal),
        "plane_origin_mm": center,
        "bbox": _bounds_from_vertices(vertices),
        "area_mm2": area,
        "aliases": [],
        "role": role,
    }


def _identity() -> list[list[float]]:
    return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


def _face_token_id(token: str) -> str:
    if len(token) == 2 and token[0] in "<>" and token[1] in "XYZ":
        return token[1] + ("POS" if token[0] == ">" else "NEG")
    return _safe_id(token)


def _shell_geometry(shell: dict[str, Any]) -> tuple[dict[str, list[float]], float, dict[str, dict[str, dict[str, Any]]]]:
    rotation = _matrix(shell.get("rotation", _identity()), "shell.rotation")
    translation = _vec(shell.get("translation_mm", [0, 0, 0]), "shell.translation_mm")
    wall = float(shell.get("wall_mm") or 0.0)
    shell_type = str(shell.get("type") or "")
    local_surfaces: dict[str, tuple[list[float], list[list[float]]]] = {}
    local_inner_surfaces: dict[str, list[list[float]]] = {}
    if shell_type == "polygon_prism":
        sides = int(shell["sides"])
        radius = float(shell["radius_mm"])
        height = float(shell["height_mm"])
        if sides < 3 or radius <= wall or height <= 2.0 * wall:
            raise ValueError("invalid polygon_prism shell dimensions")
        offset = math.radians(float(shell.get("rotation_deg") or 0.0))
        ring = [[radius * math.cos(offset + 2.0 * math.pi * index / sides), radius * math.sin(offset + 2.0 * math.pi * index / sides), 0.0] for index in range(sides)]
        bottom = [[point[0], point[1], -height / 2.0] for point in reversed(ring)]
        top = [[point[0], point[1], height / 2.0] for point in ring]
        local_surfaces["<Z"] = ([0.0, 0.0, -1.0], bottom)
        local_surfaces[">Z"] = ([0.0, 0.0, 1.0], top)
        for index in range(sides):
            start, end = ring[index], ring[(index + 1) % sides]
            candidate = [end[1] - start[1], start[0] - end[0], 0.0]
            midpoint = [(start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0, 0.0]
            normal = _normal(candidate)
            if _dot(midpoint, normal) < 0.0:
                normal = [-value for value in normal]
            local_surfaces[f"S{index}"] = (normal, [
                [start[0], start[1], -height / 2.0], [end[0], end[1], -height / 2.0],
                [end[0], end[1], height / 2.0], [start[0], start[1], height / 2.0],
            ])
        outer_area = 0.5 * sides * radius * radius * math.sin(2.0 * math.pi / sides)
        inner_radius = (radius * math.cos(math.pi / sides) - wall) / math.cos(math.pi / sides)
        inner_height = height - 2.0 * wall
        inner_ring = [[inner_radius * math.cos(offset + 2.0 * math.pi * index / sides), inner_radius * math.sin(offset + 2.0 * math.pi * index / sides), 0.0] for index in range(sides)]
        local_inner_surfaces["<Z"] = [[point[0], point[1], -inner_height / 2.0] for point in reversed(inner_ring)]
        local_inner_surfaces[">Z"] = [[point[0], point[1], inner_height / 2.0] for point in inner_ring]
        for index in range(sides):
            start, end = inner_ring[index], inner_ring[(index + 1) % sides]
            local_inner_surfaces[f"S{index}"] = [
                [start[0], start[1], -inner_height / 2.0], [end[0], end[1], -inner_height / 2.0],
                [end[0], end[1], inner_height / 2.0], [start[0], start[1], inner_height / 2.0],
            ]
        inner_area = 0.5 * sides * inner_radius * inner_radius * math.sin(2.0 * math.pi / sides)
        volume = outer_area * height - inner_area * (height - 2.0 * wall)
    elif shell_type == "box":
        dimensions = _vec(shell.get("dimensions_mm"), "shell.dimensions_mm")
        if any(value <= 2.0 * wall for value in dimensions):
            raise ValueError("invalid box shell dimensions")
        for token in (">X", "<X", ">Y", "<Y", ">Z", "<Z"):
            vertices, normal = _box_face_vertices(dimensions, _identity(), [0.0, 0.0, 0.0], token)
            local_surfaces[token] = (normal, vertices)
            inner_vertices, unused_normal = _box_face_vertices(
                [value - 2.0 * wall for value in dimensions], _identity(), [0.0, 0.0, 0.0], token
            )
            local_inner_surfaces[token] = inner_vertices
        volume = math.prod(dimensions) - math.prod([value - 2.0 * wall for value in dimensions])
    else:
        raise ValueError(f"unsupported shell type: {shell_type}")

    surfaces: dict[str, dict[str, dict[str, Any]]] = {}
    all_outer: list[list[float]] = []
    for token, (local_normal, local_vertices) in local_surfaces.items():
        world_normal = _normal(_rotate(rotation, local_normal))
        outer = [_transform(rotation, point, translation) for point in local_vertices]
        inner = [_transform(rotation, point, translation) for point in local_inner_surfaces[token]]
        all_outer.extend(outer)
        surfaces[token] = {
            "external": _planar_face("envelope", f"FACE_ENVELOPE_{_face_token_id(token)}_OUTER", outer, world_normal, "radiation"),
            "internal": _planar_face("envelope", f"FACE_ENVELOPE_{_face_token_id(token)}_INNER", inner, world_normal, "shell_contact"),
        }
    return _bounds_from_vertices(all_outer), volume, surfaces


def _body_row(
    component: dict[str, Any], placement: dict[str, Any], assembly_step: Path, assembly_hash: str, power_W: float
) -> dict[str, Any]:
    component_id = str(component["id"])
    dimensions = _vec(component.get("dimensions_mm"), f"component {component_id} dimensions")
    rotation = _matrix(placement.get("rotation"), f"layout {component_id} rotation")
    translation = _vec(placement.get("translation_mm"), f"layout {component_id} translation_mm")
    volume = math.prod(dimensions)
    if volume <= 0.0:
        raise ValueError(f"Component {component_id} has non-positive simplified volume")
    source_boxes = placement.get("boxes")
    source_box_count = len(source_boxes) if isinstance(source_boxes, list) else 0
    return {
        "business_id": component_id,
        "component_id": component_id,
        "exchange_name": _safe_id(component_id),
        "object_type": "component",
        "step_file": "geometry/assembly.step",
        "source_step": str(assembly_step),
        "geometry_hash": assembly_hash,
        "bbox": _bounds_from_vertices(_box_vertices(dimensions, rotation, translation)),
        "placement": None,
        "dimensions_mm": dimensions,
        "rotation": rotation,
        "translation_mm": translation,
        "material_id": f"MAT_{_safe_id(component_id)}",
        "power_W": power_W,
        "volume_mm3": volume,
        "mass_kg": float(component.get("mass_kg") or 0.0),
        "source_box_count": source_box_count,
    }


def _multibox_mesh_policy(bbox: dict[str, list[float]]) -> dict[str, Any]:
    extents = [float(bbox["max"][index]) - float(bbox["min"][index]) for index in range(3)]
    if any(not math.isfinite(value) or value <= 0.0 for value in extents):
        raise ValueError("Multi-box component has invalid contract bounding-box extents")
    shortest_extent = min(extents)
    rounded_size = math.ceil((shortest_extent / 4.0) / 5.0) * 5.0
    requested_size = min(50.0, max(25.0, rounded_size))
    return {
        "requested_size_mm": requested_size,
        "retry_scales": [1.0, 1.5, 2.0, 3.0],
        "policy": "linux_multibox_bbox_v1",
        "shortest_bbox_extent_mm": shortest_extent,
    }


def _collision_exclusions(collision_check: dict[str, Any]) -> dict[str, list[int]]:
    pairs = collision_check.get("collision_pairs")
    if not isinstance(pairs, list):
        raise ValueError("collision_check.json collision_pairs must be an array")
    result: dict[str, list[int]] = {}
    for index, pair in enumerate(pairs):
        if not isinstance(pair, dict):
            raise ValueError(f"collision_check.json collision_pairs[{index}] must be an object")
        component_ids = pair.get("component_ids")
        if (
            not isinstance(component_ids, list)
            or len(component_ids) != 2
            or not all(isinstance(value, str) and value.strip() for value in component_ids)
        ):
            raise ValueError(
                f"collision_check.json collision_pairs[{index}].component_ids must contain two component IDs"
            )
        volume = pair.get("collision_volume_mm3")
        if isinstance(volume, bool) or not isinstance(volume, (int, float)) or not math.isfinite(float(volume)) or float(volume) <= 0.0:
            raise ValueError(
                f"collision_check.json collision_pairs[{index}].collision_volume_mm3 must be positive"
            )
        for component_id in component_ids:
            result.setdefault(component_id.strip(), []).append(index)
    return result


def _component_mount_face(component: dict[str, Any], placement: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    token = str(component.get("mount_face") or "<Z")
    vertices, normal = _box_face_vertices(
        body["dimensions_mm"], body["rotation"], body["translation_mm"], token
    )
    face = _planar_face(
        body["business_id"], f"FACE_{_safe_id(component['id'])}_MOUNT", vertices, normal, "component_mount"
    )
    face["aliases"] = [token]
    return face


def _resolve_source(workspace: Path, raw: object, label: str) -> Path:
    if isinstance(raw, Path):
        source = raw
    elif isinstance(raw, str) and raw.strip():
        source = Path(raw.strip())
    else:
        raise ValueError(f"{label} is missing")
    if not source.is_absolute():
        source = workspace / source
    source = source.resolve()
    try:
        source.relative_to(workspace)
    except ValueError as exc:
        raise ValueError(f"{label} escapes the workspace: {source}") from exc
    if not source.is_file() or source.stat().st_size <= 0:
        raise FileNotFoundError(f"{label} is missing or empty: {source}")
    return source


def _contract_face(
    owner_id: str, face_id: str, raw: dict[str, Any], normal: list[float], role: str
) -> dict[str, Any]:
    bbox = _bbox(
        [*raw.get("bbox", {}).get("min", []), *raw.get("bbox", {}).get("max", [])],
        f"geometry contract face {face_id} bbox",
    )
    origin = raw.get("plane_origin_mm")
    if origin is None:
        origin = [(bbox["min"][index] + bbox["max"][index]) / 2.0 for index in range(3)]
    area = float(raw.get("area_mm2") or 0.0)
    if not math.isfinite(area) or area <= 0.0:
        raise ValueError(f"geometry contract face {face_id} has non-positive area")
    face_normal = raw.get("normal", normal)
    return {
        "face_id": face_id,
        "owner_id": owner_id,
        "normal": _normal(_vec(face_normal, f"geometry contract face {face_id} normal")),
        "plane_origin_mm": _vec(origin, f"geometry contract face {face_id} plane_origin_mm"),
        "bbox": bbox,
        "area_mm2": area,
        "aliases": [],
        "role": role,
    }


def _geometry_contract(
    contract: dict[str, Any], components: list[dict[str, Any]], excluded_components: set[str]
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    if contract.get("schema_version") != "cad-builder-geometry-contract/1":
        raise ValueError("unsupported CAD Builder geometry contract schema")
    if contract.get("geometry_mode") != "simplified_envelope":
        raise ValueError("CAD Builder geometry contract must describe simplified_envelope geometry")
    expected_ids = ["envelope", *[str(row["id"]).strip() for row in components]]
    body_rows = contract.get("bodies")
    if not isinstance(body_rows, list) or [row.get("component_id") for row in body_rows] != expected_ids:
        raise ValueError("CAD Builder geometry contract body order does not match project.json")
    if [row.get("step_body_index") for row in body_rows] != list(range(len(expected_ids))):
        raise ValueError("CAD Builder geometry contract has invalid STEP body indexes")
    bodies = {str(row["component_id"]): row for row in body_rows}
    for component_id, row in bodies.items():
        _bbox([*row.get("bbox", {}).get("min", []), *row.get("bbox", {}).get("max", [])], f"body {component_id} bbox")
        volume = float(row.get("volume_mm3") or 0.0)
        if not math.isfinite(volume) or volume <= 0.0:
            raise ValueError(f"CAD Builder geometry contract body {component_id} has non-positive volume")

    surface_rows = contract.get("shell_surfaces")
    if not isinstance(surface_rows, list) or not surface_rows:
        raise ValueError("CAD Builder geometry contract contains no shell surfaces")
    surfaces: dict[str, dict[str, Any]] = {}
    for row in surface_rows:
        token = str(row.get("surface_id") or "")
        normal = _vec(row.get("normal"), f"shell surface {token} normal")
        if isinstance(row.get("external_face"), dict) and isinstance(row.get("internal_face"), dict):
            surfaces[token] = {
                "external": _contract_face(
                    "envelope", f"FACE_ENVELOPE_{_face_token_id(token)}_OUTER",
                    row["external_face"], normal, "radiation",
                ),
                "internal": _contract_face(
                    "envelope", f"FACE_ENVELOPE_{_face_token_id(token)}_INNER",
                    row["internal_face"], normal, "shell_contact",
                ),
            }
        else:
            surfaces[token] = {
                "external": _planar_face(
                    "envelope", f"FACE_ENVELOPE_{_face_token_id(token)}_OUTER",
                    [_vec(point, f"shell surface {token} external vertex") for point in row.get("external_vertices", [])],
                    normal, "radiation",
                ),
                "internal": _planar_face(
                    "envelope", f"FACE_ENVELOPE_{_face_token_id(token)}_INNER",
                    [_vec(point, f"shell surface {token} internal vertex") for point in row.get("internal_vertices", [])],
                    normal, "shell_contact",
                ),
            }

    region_rows = contract.get("mount_regions")
    if not isinstance(region_rows, list):
        raise ValueError("CAD Builder geometry contract contains no mount_regions array")
    regions = {str(row.get("component_id")): row for row in region_rows}
    if set(regions) != set(expected_ids[1:]):
        raise ValueError("CAD Builder geometry contract mount-region IDs do not match project.json")
    for component in components:
        component_id = str(component["id"]).strip()
        region = regions[component_id]
        if region.get("step_body_index") != bodies[component_id].get("step_body_index"):
            raise ValueError(f"CAD Builder geometry contract has inconsistent body index for {component_id}")
        if str(region.get("mount_face")) != str(component.get("mount_face") or "<Z"):
            raise ValueError(f"CAD Builder geometry contract mount face differs for {component_id}")
        shell_token = str(region.get("shell_mount_face") or "")
        if shell_token not in surfaces:
            raise ValueError(f"CAD Builder geometry contract has invalid shell surface for {component_id}")
        faces = region.get("faces")
        if not isinstance(faces, list) or (not faces and component_id not in excluded_components):
            raise ValueError(f"CAD Builder geometry contract has no mount faces for {component_id}")
    return bodies, surfaces, regions


def _validate_export_manifest(manifest: dict[str, Any], assembly: Path, contract: Path) -> None:
    if manifest.get("mode") != "envelope":
        raise ValueError("simplified CAD manifest is not an envelope export")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("simplified CAD manifest contains no artifacts")
    for name, path in (("step", assembly), ("geometry_contract", contract)):
        record = artifacts.get(name)
        if not isinstance(record, dict) or record.get("sha256") != sha256_file(path):
            raise ValueError(f"simplified CAD manifest hash mismatch for {name}")


def _node_step(workspace: Path, manifest_path: Path, node: dict[str, Any]) -> Path:
    files = node.get("files") if isinstance(node.get("files"), dict) else {}
    raw = files.get("step")
    if not isinstance(raw, str):
        raise ValueError(f"CAD node {node.get('id')} has no STEP file")
    artifact_root = manifest_path.parent.parent
    return _resolve_source(workspace, artifact_root / raw, f"CAD node {node.get('id')} STEP")


def _value_for_component(
    component: dict[str, Any], override: dict[str, Any], names: tuple[str, ...], default: str, unit: str, pointer: str
) -> dict[str, Any]:
    for name in names:
        if name in override:
            return _provided_quantity(override[name], unit, f"thermal-overrides.json#/components/{component['id']}/{name}")
    thermal = component.get("thermal") if isinstance(component.get("thermal"), dict) else {}
    for name in names:
        if name in component:
            return _provided_quantity(component[name], unit, f"project.json#/components/{pointer}/{name}")
        if name in thermal:
            return _provided_quantity(thermal[name], unit, f"project.json#/components/{pointer}/thermal/{name}")
    return _default_quantity(default, unit)


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def build_thermal_case(
    thermal_inputs: dict[str, Any], semantics: dict[str, Any]
) -> dict[str, Any]:
    """Build the NX-facing case from the persisted normalized Linux inputs."""
    if thermal_inputs.get("schema_version") != INPUT_SCHEMA:
        raise ValueError("thermal-inputs.json has an unsupported schema")
    if semantics.get("schema_version") != SEMANTICS_SCHEMA:
        raise ValueError("geometry-semantics.json has an unsupported schema")
    components = {
        str(row["id"]): row
        for row in thermal_inputs.get("components", [])
        if isinstance(row, dict) and "id" in row
    }
    materials: dict[str, dict[str, float]] = {}
    for body in semantics.get("bodies", []):
        component_id = str(body.get("component_id") or body["business_id"])
        conductivity = DEFAULTS["conductivity_W_mK"]
        if component_id in components:
            conductivity = float(components[component_id]["conductivity"]["value"])
        materials[str(body["material_id"])] = {
            "conductivity_W_mK": conductivity,
            "density_kg_m3": DEFAULTS["density_kg_m3"],
            "heat_capacity_J_kgK": DEFAULTS["heat_capacity_J_kgK"],
        }
    environment = thermal_inputs["environment"]
    environment_faces = list(environment["faces"])
    return {
        "schema_version": "nx-ug-thermal-case/1",
        "analysis_mode": "steady",
        "solver": {
            "name": "NX SPACE SYSTEMS THERMAL",
            "analysis": "Thermal",
            "solution_type": "Space Systems Thermal",
            "cg_tolerance": 1.0e-6,
            "cg_iteration_limit": 500,
            "cg_matrix_fill": 200,
            "steady_max_temperature_change_K": 0.01,
        },
        "materials": materials,
        "initial_temperature_K": float(thermal_inputs["analysis"]["initial_temperature"]["value"]),
        "radiation": {
            "emissivity": DEFAULTS["emissivity"],
            "environment_temperature_K": float(environment["space_temperature"]["value"]),
            "view_factor": float(environment["view_factor"]["value"]),
        },
        "external_heat_flux": {
            "enabled": any(abs(float(row["heat_flux"]["value"])) > 0.0 for row in environment_faces),
            "faces_W_m2": {
                str(row["face_id"]): float(row["heat_flux"]["value"])
                for row in environment_faces
            },
        },
        "total_power_W": sum(
            float(row["power"]["value"])
            for row in thermal_inputs.get("components", [])
            if row.get("analysis_enabled", True)
        ),
        "component_power_W": {
            str(row["id"]): float(row["power"]["value"])
            for row in thermal_inputs.get("components", [])
            if row.get("analysis_enabled", True)
        },
        "mesh": {
            "strategy": "tet4",
            "shell_size_mm": 20.0,
            "wall_size_mm": 16.0,
            "component_min_mm": 10.0,
            "component_max_mm": 25.0,
        },
    }


def prepare_thermal_inputs(
    workspace: Path,
    *,
    component_path: Path | None = None,
    layout_path: Path | None = None,
    assembly_step_path: Path | None = None,
    overrides_path: Path | None = None,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    component_path = _resolve_source(
        workspace, component_path or workspace / "00_inputs" / "project.json", "project.json"
    )
    layout_path = _resolve_source(
        workspace, layout_path or workspace / "01_cad" / "planner" / "layout.json", "layout.json"
    )
    assembly_step_path = _resolve_source(
        workspace,
        assembly_step_path or workspace / "01_cad" / "simplified" / "assembly.step",
        "simplified assembly STEP",
    )
    geometry_contract_path = _resolve_source(
        workspace, workspace / "01_cad" / "simplified" / "geometry-contract.json", "CAD Builder geometry contract"
    )
    simplified_manifest_path = _resolve_source(
        workspace, workspace / "01_cad" / "simplified" / "manifest.json", "simplified CAD manifest"
    )
    collision_check_path = _resolve_source(
        workspace, workspace / "01_cad" / "collision_check.json", "collision check"
    )
    mount_face_check_path = _resolve_source(
        workspace, workspace / "01_cad" / "mount_face_check.json", "mount-face check"
    )
    if overrides_path:
        overrides_path = _resolve_source(workspace, overrides_path, "thermal overrides")
    component_doc = read_json(component_path)
    layout_doc = read_json(layout_path)
    mount_face_check = read_json(mount_face_check_path)
    collision_check = read_json(collision_check_path)
    geometry_contract = read_json(geometry_contract_path)
    simplified_manifest = read_json(simplified_manifest_path)
    _validate_export_manifest(simplified_manifest, assembly_step_path, geometry_contract_path)
    overrides = read_json(overrides_path) if overrides_path else {}
    components = component_doc.get("components")
    if not isinstance(components, list) or not components:
        raise ValueError(f"project.json contains no components: {component_path}")
    placements = layout_doc.get("placements")
    if not isinstance(placements, dict) or not placements:
        raise ValueError(f"layout.json contains no placements: {layout_path}")
    if layout_doc.get("metadata", {}).get("coordinate_mode") != "world":
        raise ValueError("layout.json metadata.coordinate_mode must be world")
    component_overrides = overrides.get("components") if isinstance(overrides.get("components"), dict) else {}
    problem_ids = mount_face_check.get("problem_component_ids")
    if not isinstance(problem_ids, list) or not all(isinstance(value, str) for value in problem_ids):
        raise ValueError("mount_face_check.json problem_component_ids must be a string array")
    mount_problem_ids = set(problem_ids)
    collision_exclusions = _collision_exclusions(collision_check)
    collision_component_ids = set(collision_exclusions)
    excluded_components = mount_problem_ids | collision_component_ids
    assembly_hash = sha256_file(assembly_step_path)
    contract_bodies, shell_surfaces, mount_regions = _geometry_contract(
        geometry_contract, components, excluded_components
    )
    shell_contract = contract_bodies["envelope"]
    shell_bbox = _bbox([*shell_contract["bbox"]["min"], *shell_contract["bbox"]["max"]], "envelope contract bbox")
    shell_volume = float(shell_contract["volume_mm3"])
    bodies: list[dict[str, Any]] = [{
        "business_id": "envelope",
        "component_id": "envelope",
        "exchange_name": "ENVELOPE",
        "object_type": "shell",
        "analysis_enabled": True,
        "step_file": "geometry/assembly.step",
        "source_step": str(assembly_step_path),
        "geometry_hash": assembly_hash,
        "bbox": shell_bbox,
        "placement": None,
        "material_id": "MAT_ENVELOPE",
        "power_W": 0.0,
        "volume_mm3": shell_volume,
        "step_body_index": 0,
    }]
    radiation_faces = [row["external"] for row in shell_surfaces.values()]
    faces = list(radiation_faces)
    normalized_components: list[dict[str, Any]] = []
    contacts: list[dict[str, Any]] = []
    assumptions: list[dict[str, Any]] = []
    component_ids = {str(component.get("id")) for component in components if isinstance(component, dict)}
    unknown_problem_ids = sorted(mount_problem_ids - component_ids)
    if unknown_problem_ids:
        raise ValueError(f"mount_face_check.json references unknown components: {unknown_problem_ids}")
    unknown_collision_ids = sorted(collision_component_ids - component_ids)
    if unknown_collision_ids:
        raise ValueError(f"collision_check.json references unknown components: {unknown_collision_ids}")
    if component_ids != set(placements):
        missing = sorted(component_ids - set(placements))
        unknown = sorted(set(placements) - component_ids)
        raise ValueError(f"component/layout ID mismatch; missing placements={missing}, unknown placements={unknown}")
    for index, component in enumerate(components):
        if not isinstance(component, dict) or not str(component.get("id", "")).strip():
            raise ValueError(f"project.json entry {index} has no id")
        component_id = str(component["id"]).strip()
        placement = placements[component_id]
        if not isinstance(placement, dict):
            raise ValueError(f"layout placement for {component_id} must be an object")
        override = component_overrides.get(component_id, {})
        if not isinstance(override, dict):
            raise ValueError(f"Override for component {component_id} must be an object")
        power = _value_for_component(component, override, ("power_W", "power_w"), "power_W", "W", str(index))
        conductivity = _value_for_component(
            component, override, ("conductivity_W_mK",), "conductivity_W_mK", "W/(m*K)", str(index)
        )
        emissivity = _value_for_component(component, override, ("emissivity",), "emissivity", "1", str(index))
        body = _body_row(component, placement, assembly_step_path, assembly_hash, power["value"])
        body_contract = contract_bodies[component_id]
        body["bbox"] = _bbox([*body_contract["bbox"]["min"], *body_contract["bbox"]["max"]], f"body {component_id} contract bbox")
        body["volume_mm3"] = float(body_contract["volume_mm3"])
        body["step_body_index"] = int(body_contract["step_body_index"])
        body["analysis_enabled"] = component_id not in excluded_components
        if body["source_box_count"] > 1:
            body["mesh"] = _multibox_mesh_policy(body["bbox"])
        bodies.append(body)
        region = mount_regions[component_id]
        shell_token = str(region["shell_mount_face"])
        shell_side = "internal" if str(region.get("placement") or "internal") == "internal" else "external"
        target_face = shell_surfaces[shell_token][shell_side]
        if target_face["face_id"] not in {row["face_id"] for row in faces}:
            faces.append(target_face)
        base_contact_id = f"CONTACT_{_safe_id(component_id)}"
        contact_override = (
            overrides.get("contacts", {}).get(base_contact_id, {})
            if isinstance(overrides.get("contacts"), dict) else {}
        )
        if not isinstance(contact_override, dict):
            raise ValueError(f"Override for contact {base_contact_id} must be an object")
        if "resistance_m2K_W" in contact_override:
            resistance = _provided_quantity(
                contact_override["resistance_m2K_W"], "m2*K/W", f"thermal-overrides.json#/contacts/{base_contact_id}/resistance_m2K_W"
            )
        else:
            resistance = _default_quantity("contact_resistance_m2K_W", "m2*K/W")
        region_faces = region["faces"]
        for face_index, raw_face in enumerate(region_faces):
            suffix = "" if len(region_faces) == 1 else f"_{face_index + 1:03d}"
            face_id = f"FACE_{_safe_id(component_id)}_MOUNT{suffix}"
            contact_id = f"{base_contact_id}{suffix}"
            source_face = _contract_face(component_id, face_id, raw_face, region["normal"], "component_mount")
            source_face["aliases"] = [str(region["mount_face"])]
            faces.append(source_face)
            area = _quantity(
                float(source_face["area_mm2"]) * 1.0e-6, "m2", "derived",
                f"01_cad/simplified/geometry-contract.json#/mount_regions/{index}/faces/{face_index}/area_mm2",
                "CAD Builder fused STEP mount-face area",
            )
            contacts.append({
                "contact_id": contact_id, "component_a": component_id, "face_a": source_face["face_id"],
                "component_b": "envelope", "face_b": target_face["face_id"], "area": area,
                "thermal_resistance": resistance,
            })
        normalized = {
            "id": component_id,
            "name": str(component.get("name") or component_id),
            "geometry_ref": "geometry/assembly.step",
            "body_ids": [body["business_id"]],
            "analysis_enabled": component_id not in excluded_components,
            "power": power,
            "conductivity": conductivity,
            "emissivity": emissivity,
        }
        normalized_components.append(normalized)
        for field_name in ("power", "conductivity", "emissivity"):
            value = normalized[field_name]
            if value["status"] in {"estimated", "global_default"}:
                assumptions.append({"target": component_id, "field": field_name, **value})
        if resistance["status"] in {"estimated", "global_default"}:
            assumptions.append({"target": base_contact_id, "field": "thermal_resistance", **resistance})

    body_component = {row["business_id"]: row.get("component_id", row["business_id"]) for row in bodies}
    contacts = [row for row in contacts if row["component_a"] not in excluded_components]
    faces = [
        row for row in faces
        if row["role"] != "component_mount" or body_component[row["owner_id"]] not in excluded_components
    ]
    excluded_contact_ids = {f"CONTACT_{_safe_id(component_id)}" for component_id in excluded_components}
    assumptions = [row for row in assumptions if not any(str(row.get("target", "")).startswith(value) for value in excluded_contact_ids)]
    for component_id in sorted(mount_problem_ids):
        assumptions.append({
            "target": component_id,
            "field": "analysis_enabled",
            "value": False,
            "unit": "1",
            "status": "derived",
            "source_locator": "01_cad/mount_face_check.json#/problem_component_ids",
            "method": "excluded because the CAD mounting-face check reported a problem",
        })
    for component_id in sorted(collision_component_ids):
        for pair_index in collision_exclusions[component_id]:
            assumptions.append({
                "target": component_id,
                "field": "analysis_enabled",
                "value": False,
                "unit": "1",
                "status": "derived",
                "source_locator": f"01_cad/collision_check.json#/collision_pairs/{pair_index}/component_ids",
                "method": "excluded because the CAD collision check reported a collision",
            })

    environment_overrides = overrides.get("environment") if isinstance(overrides.get("environment"), dict) else {}
    environment_faces = []
    for face_name in FACE_NAMES:
        if face_name in environment_overrides:
            flux = _provided_quantity(
                environment_overrides[face_name], "W/m2", f"thermal-overrides.json#/environment/{face_name}"
            )
        else:
            flux = _default_quantity("external_heat_flux_W_m2", "W/m2")
            assumptions.append({"target": face_name, "field": "external_heat_flux", **flux})
        direction = [0.0, 0.0, 0.0]
        direction["xyz".index(face_name[0])] = -1.0 if face_name[1] == "-" else 1.0
        target_face = max(radiation_faces, key=lambda row: (_dot(row["normal"], direction), row["face_id"]))
        environment_faces.append({"boundary_id": face_name, "face_id": target_face["face_id"], "heat_flux": flux})

    initial_temperature = _global_quantity(
        overrides, ("initial_temperature_K",), "initial_temperature_K", "K"
    )
    space_temperature = _global_quantity(
        overrides, ("space_temperature_K",), "space_temperature_K", "K"
    )
    view_factor = _global_quantity(overrides, ("view_factor",), "view_factor", "1")
    for field_name, value in (
        ("initial_temperature", initial_temperature), ("space_temperature", space_temperature), ("view_factor", view_factor)
    ):
        assumptions.append({"target": "global", "field": field_name, **value})

    thermal_inputs = {
        "schema_version": INPUT_SCHEMA,
        "analysis": {"type": "steady_state", "initial_temperature": initial_temperature},
        "components": normalized_components,
        "contacts": contacts,
        "environment": {
            "space_temperature": space_temperature,
            "view_factor": view_factor,
            "faces": environment_faces,
        },
    }
    semantics_contacts = [{
        "contact_id": row["contact_id"],
        "source_face_id": row["face_a"],
        "target_face_id": row["face_b"],
        "resistance_m2K_W": row["thermal_resistance"]["value"],
        "area_mm2": row["area"]["value"] * 1.0e6,
        "kind": "component_mount",
    } for row in contacts if row["area"]["value"] > 0.0]
    semantics = {
        "schema_version": SEMANTICS_SCHEMA,
        "units": {"length": "mm", "power": "W", "contact_resistance": "m2*K/W"},
        "geometry_source": {
            "mode": "simplified_assembly",
            "step_file": "geometry/assembly.step",
            "source_step": str(assembly_step_path),
            "sha256": assembly_hash,
        },
        "expected_body_count": len(bodies),
        "tolerance_mm": 1.0e-3,
        "bodies": bodies,
        "faces": faces,
        "contacts": semantics_contacts,
        "radiation_face_ids": [face["face_id"] for face in radiation_faces],
    }
    build_thermal_case(thermal_inputs, semantics)

    inputs_dir = workspace / "02_sim" / "thermal" / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    write_json(inputs_dir / "thermal-inputs.json", thermal_inputs)
    write_json(inputs_dir / "geometry-semantics.json", semantics)
    write_json(inputs_dir / "assumptions.json", {"schema_version": ASSUMPTIONS_SCHEMA, "items": assumptions})
    source_paths = [component_path, layout_path, assembly_step_path, geometry_contract_path, simplified_manifest_path, collision_check_path, mount_face_check_path]
    if overrides_path:
        source_paths.append(overrides_path)
    source_manifest = {
        "schema_version": SOURCE_SCHEMA,
        "workspace": str(workspace),
        "files": [file_record(path, root=workspace) for path in source_paths],
    }
    write_json(inputs_dir / "source-manifest.json", source_manifest)

    component_rows = []
    for row in normalized_components:
        component_rows.append({
            "component_id": row["id"], "component_name": row["name"], "step_path": row["geometry_ref"],
            "power_W": row["power"]["value"], "power_status": row["power"]["status"],
            "power_source_locator": row["power"]["source_locator"],
            "conductivity_W_mK": row["conductivity"]["value"], "conductivity_status": row["conductivity"]["status"],
            "conductivity_source_locator": row["conductivity"]["source_locator"],
            "emissivity": row["emissivity"]["value"], "emissivity_status": row["emissivity"]["status"],
            "emissivity_source_locator": row["emissivity"]["source_locator"],
        })
    _write_csv(inputs_dir / "components.csv", component_rows, list(component_rows[0]))
    contact_rows = [{
        "contact_id": row["contact_id"], "component_a": row["component_a"], "face_a": row["face_a"],
        "component_b": row["component_b"], "face_b": row["face_b"], "area_m2": row["area"]["value"],
        "area_status": row["area"]["status"], "thermal_resistance_m2K_W": row["thermal_resistance"]["value"],
        "resistance_status": row["thermal_resistance"]["status"],
    } for row in contacts]
    _write_csv(inputs_dir / "contacts.csv", contact_rows, list(contact_rows[0]) if contact_rows else [
        "contact_id", "component_a", "face_a", "component_b", "face_b", "area_m2", "area_status",
        "thermal_resistance_m2K_W", "resistance_status",
    ])
    environment_rows = [{
        "boundary_id": row["boundary_id"], "face_id": row["face_id"],
        "heat_flux_W_m2": row["heat_flux"]["value"], "status": row["heat_flux"]["status"],
        "source_locator": row["heat_flux"]["source_locator"],
    } for row in environment_faces]
    _write_csv(inputs_dir / "environment.csv", environment_rows, list(environment_rows[0]))
    return {
        "ok": True,
        "inputs_dir": str(inputs_dir),
        "component_count": len(normalized_components),
        "body_count": len(bodies),
        "contact_count": len(semantics_contacts),
        "assumption_count": len(assumptions),
        "source_count": len(source_manifest["files"]),
        "geometry_mode": "simplified_assembly",
        "excluded_component_count": len(excluded_components),
        "collision_excluded_component_count": len(collision_component_ids),
    }
