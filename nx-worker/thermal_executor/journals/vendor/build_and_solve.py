from __future__ import print_function

import csv
import json
import math
import os
import re
import time
import traceback

import NXOpen
import NXOpen.CAE
import NXOpen.UF


FACE_TOLERANCE_MM = 1.0e-3


class StageObserver(object):
    enabled = False

    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


def read_json(path):
    with open(path, "r", encoding="utf-8-sig") as stream:
        return json.load(stream)


def write_json(path, value):
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(path, "w") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)


def safe_name(value):
    result = re.sub(r"[^A-Za-z0-9_]+", "_", str(value)).strip("_")
    return result[:120] or "UNNAMED"


def open_base_display(session, path):
    opened = session.Parts.OpenBaseDisplay(path)
    return opened[0] if isinstance(opened, tuple) else opened


def string_attribute(value, title):
    try:
        return value.GetStringUserAttribute(title, -1)
    except Exception:
        return None


def create_fem(session, master, fem_path, semantics):
    if os.path.exists(fem_path):
        os.remove(fem_path)
    file_new = session.Parts.FileNew()
    sync = None
    try:
        file_new.TemplateFileName = "Blank"
        file_new.TemplateType = NXOpen.FileNewTemplateType.Item
        file_new.Application = NXOpen.FileNewApplication.CaeFem
        file_new.Units = NXOpen.Part.Units.Millimeters
        file_new.NewFileName = fem_path
        file_new.MasterFileName = ""
        file_new.MakeDisplayedPart = False
        file_new.UseBlankTemplate = True
        fem = file_new.Commit()
        file_new.Destroy()
        file_new = None
        sync = fem.NewFemSynchronizeOptions()
        sync.SetAllSynchronizeFlags(False)
        active_ids = set(
            row["business_id"] for row in semantics["bodies"] if row.get("analysis_enabled", True)
        )
        selected_bodies = [
            body for body in master.Bodies if string_attribute(body, "AGENT_OBJECT_ID") in active_ids
        ]
        fem.FinalizeCreation(
            master,
            None,
            NXOpen.CAE.FemPart.UseBodiesOption.SelectedBodies,
            selected_bodies,
            sync,
            "NX SPACE SYSTEMS THERMAL",
            "Thermal",
            NXOpen.CAE.BaseFemPart.AxisymAbstractionType.ValueOf(0),
            [],
        )
        if fem.AssociatedCadPart is None or fem.BaseFEModel is None:
            raise RuntimeError("FEM direct FinalizeCreation did not associate CAD geometry")
        if list(fem.GetSolverAndAnalysisType()) != ["NX SPACE SYSTEMS THERMAL", "Thermal"]:
            raise RuntimeError("FEM solver/analysis type was not initialized")
        fem.Save(fem.SaveComponents.TrueValue, fem.CloseAfterSave.FalseValue)
        return fem
    finally:
        if file_new is not None:
            file_new.Destroy()
        if sync is not None:
            sync.Dispose()


def body_map(master):
    result = {}
    for body in master.Bodies:
        business_id = string_attribute(body, "AGENT_OBJECT_ID")
        if business_id:
            result[business_id] = body
    return result


def apply_step_geometry(semantics, binding):
    actual_by_id = dict((row["business_id"], row) for row in binding.get("bodies", []))
    expected_ids = set(row["business_id"] for row in semantics["bodies"])
    if set(actual_by_id) != expected_ids:
        raise RuntimeError("NX binding body IDs do not match geometry semantics")
    for row in semantics["bodies"]:
        actual = actual_by_id[row["business_id"]].get("bbox")
        if not isinstance(actual, list) or len(actual) != 6:
            raise RuntimeError("NX binding has no STEP-derived bbox for %s" % row["business_id"])
        row["bbox"] = {
            "min": [float(value) for value in actual[:3]],
            "max": [float(value) for value in actual[3:]],
        }
    actual_faces = dict((row["face_id"], row) for row in binding.get("faces", []))
    for row in semantics["faces"]:
        actual = actual_faces.get(row["face_id"])
        if not actual or not isinstance(actual.get("bbox"), dict):
            raise RuntimeError("NX binding has no STEP-derived face bounds for %s" % row["face_id"])
        row["bbox"] = actual["bbox"]
        row["plane_origin_mm"] = actual["plane_origin_mm"]


def polygon_body(uf, modeling_body):
    tag = uf.Sf.ModlBodyAskBody(modeling_body.Tag)
    tag = tag[-1] if isinstance(tag, tuple) else tag
    return NXOpen.TaggedObjectManager.GetTaggedObject(tag)


def face_bbox(uf, face_tag):
    return [float(value) for value in uf.Sf.FaceAskBoundingBox(face_tag)]


def expected_face_bbox(face_row):
    if "bbox" in face_row:
        return [float(value) for value in face_row["bbox"]["min"] + face_row["bbox"]["max"]]
    axis = int(face_row["axis"])
    other = [index for index in range(3) if index != axis]
    bounds = face_row["bbox_2d_mm"]
    minimum = [0.0, 0.0, 0.0]
    maximum = [0.0, 0.0, 0.0]
    minimum[axis] = float(face_row["plane_value_mm"])
    maximum[axis] = float(face_row["plane_value_mm"])
    minimum[other[0]] = float(bounds[0])
    maximum[other[0]] = float(bounds[1])
    minimum[other[1]] = float(bounds[2])
    maximum[other[1]] = float(bounds[3])
    return minimum + maximum


def match_polygon_face(uf, polygon_body_object, face_row):
    face_data = uf.Sf.BodyAskFaces(polygon_body_object.Tag)
    tags = list(face_data[1]) if isinstance(face_data, tuple) else list(face_data)
    expected = expected_face_bbox(face_row)
    candidates = []
    for tag in tags:
        actual = face_bbox(uf, tag)
        error = max(abs(actual[index] - expected[index]) for index in range(6))
        if error <= FACE_TOLERANCE_MM:
            candidates.append((error, int(tag)))
    candidates.sort()
    if not candidates:
        raise RuntimeError("no polygon face matched %s" % face_row["face_id"])
    if len(candidates) > 1 and abs(candidates[0][0] - candidates[1][0]) < 1.0e-10:
        raise RuntimeError("ambiguous polygon face match for %s" % face_row["face_id"])
    return NXOpen.TaggedObjectManager.GetTaggedObject(candidates[0][1]), candidates[0][0]


def create_materials(fem, material_rows):
    collection = fem.MaterialManager.PhysicalMaterials
    density_unit = fem.UnitCollection.FindObject("KilogramPerCubicMeter")
    conductivity_unit = fem.UnitCollection.FindObject("ThermalConductivity_Metric3")
    specific_heat_unit = fem.UnitCollection.FindObject("SpecificHeat_Metric2")
    result = {}
    for material_id, row in material_rows.items():
        builder = collection.CreatePhysicalMaterialBuilder(NXOpen.PhysicalMaterial.Type.Isotropic)
        try:
            builder.Name = material_id
            builder.Description = "Generated by nx_thermal_pipeline"
            table = builder.PropertyTable
            table.SetIntegerPropertyValue("DensityControl", 0)
            table.SetScalarWithDataPropertyValue("MassDensity", float(row.get("rho_kg_m3", row["density_kg_m3"])), density_unit)
            table.SetIntegerPropertyValue("ThermalConductivityControl", 0)
            table.SetScalarWithDataPropertyValue("ThermalConductivity", float(row.get("k_W_mK", row["conductivity_W_mK"])), conductivity_unit)
            table.SetIntegerPropertyValue("SpecificHeatControl", 0)
            table.SetScalarWithDataPropertyValue("SpecificHeat", float(row.get("cp_J_kgK", row["heat_capacity_J_kgK"])), specific_heat_unit)
            committed = builder.Commit()
        finally:
            builder.Destroy()
        material = committed
        if material is None or type(material).__name__ != "PhysicalMaterial":
            material = next(item for item in collection if item.Name == material_id)
        result[material_id] = material
    return result


def mesh_size(body_row, mesh_config):
    body_mesh = body_row.get("mesh")
    if isinstance(body_mesh, dict) and "requested_size_mm" in body_mesh:
        return float(body_mesh["requested_size_mm"])
    if body_row["object_type"] == "shell":
        return float(mesh_config["shell_size_mm"])
    if body_row["object_type"] == "wall":
        return float(mesh_config["wall_size_mm"])
    dims = [float(body_row["bbox"]["max"][index]) - float(body_row["bbox"]["min"][index]) for index in range(3)]
    value = min(dims) / 2.0
    if value < float(mesh_config["component_min_mm"]):
        return max(0.25, value)
    return min(float(mesh_config["component_max_mm"]), value)


def assign_material_to_mesh(fem, mesh, material, collector_name):
    builder = fem.BaseFEModel.MeshManager.CreateCollectorBuilder(mesh.MeshCollector, "Solid")
    options = fem.NewMaterialOptions()
    try:
        builder.CollectorName = collector_name
        options.Material = material
        options.MaterialInherited = False
        builder.PropertyTable.SetPhysicalMaterialPropertyValue("material", options)
        builder.Commit()
    finally:
        if hasattr(options, "FreeResource"):
            options.FreeResource()
        elif hasattr(options, "Dispose"):
            options.Dispose()
        builder.Destroy()


def create_tet_mesh(fem, cae_body, size_mm, retry_scales=(1.0, 1.5)):
    last_error = None
    for attempt, scale in enumerate(retry_scales):
        actual_size = size_mm * scale
        builder = fem.BaseFEModel.MeshManager.CreateMesh3dTetBuilder(NXOpen.CAE.Mesh3d.Null)
        try:
            element = builder.ElementType
            element.ElementDimension = NXOpen.CAE.ElementTypeBuilder.ElementType.FreeSolid
            element.ElementOrder = NXOpen.CAE.ElementTypeBuilder.ElementOrderType.Linear
            names = list(element.GetElementTypeNames())
            if "TET4" not in names:
                raise RuntimeError("TET4 is unavailable: %s" % names)
            element.ElementTypeName = "TET4"
            element.DestinationCollector.AutomaticMode = True
            builder.SelectionList.Add(cae_body)
            builder.PropertyTable.SetScalarWithDataPropertyValue(
                "quad mesh overall edge size",
                actual_size,
                fem.UnitCollection.FindObject("MilliMeter"),
            )
            committed = builder.CommitMesh()
            meshes = list(committed) if isinstance(committed, (list, tuple)) else [committed]
            if len(meshes) != 1:
                raise RuntimeError("tet builder returned %d meshes" % len(meshes))
            return meshes[0], actual_size, attempt, "TET4"
        except Exception:
            last_error = traceback.format_exc()
        finally:
            builder.Destroy()
    raise RuntimeError("tet mesh failed after retry:\n%s" % last_error)


def create_hex_mesh(fem, cae_body, body_row, size_mm, uf):
    dims = [float(body_row["bbox"]["max"][index]) - float(body_row["bbox"]["min"][index]) for index in range(3)]
    axis = min(range(3), key=lambda index: dims[index])
    body_faces = uf.Sf.BodyAskFaces(cae_body.Tag)
    face_tags = list(body_faces[1]) if isinstance(body_faces, tuple) else list(body_faces)
    planar = []
    walls = []
    for face_tag in face_tags:
        bbox = face_bbox(uf, face_tag)
        face = NXOpen.TaggedObjectManager.GetTaggedObject(int(face_tag))
        if abs(bbox[axis + 3] - bbox[axis]) <= FACE_TOLERANCE_MM:
            planar.append((0.5 * (bbox[axis] + bbox[axis + 3]), face))
        else:
            walls.append(face)
    planar.sort(key=lambda value: value[0])
    if len(planar) != 2 or len(walls) != 4:
        raise RuntimeError("body is not a six-face sweep block on axis %d" % axis)

    builder = fem.BaseFEModel.MeshManager.CreateMesh3dHexBuilder(NXOpen.CAE.SweptMesh.Null)
    try:
        builder.CreationType = NXOpen.CAE.Mesh3dHexBuilder.Type.BetweenAutomatic
        element = builder.ElementType
        element.ElementDimension = NXOpen.CAE.ElementTypeBuilder.ElementType.SweepSolid
        element.ElementOrder = NXOpen.CAE.ElementTypeBuilder.ElementOrderType.Linear
        names = list(element.GetElementTypeNames())
        if "HEXA8" not in names:
            raise RuntimeError("HEXA8 is unavailable: %s" % names)
        element.ElementTypeName = "HEXA8"
        element.DestinationCollector.AutomaticMode = True
        builder.SelectionList.Add(cae_body)
        builder.SourceFaceList.Add(planar[0][1])
        builder.TargetFaceList.Add(planar[1][1])
        for wall in walls:
            builder.WallFaceList.Add(wall)
        unit = fem.UnitCollection.FindObject("MilliMeter")
        builder.PropertyTable.SetScalarWithDataPropertyValue("quad mesh overall edge size", float(size_mm), unit)
        builder.PropertyTable.SetScalarWithDataPropertyValue("source element size", float(size_mm), unit)
        builder.PropertyTable.SetBooleanPropertyValue("mapped mesh option bool", True)
        builder.PropertyTable.SetIntegerPropertyValue("quad only option", 1)
        builder.PropertyTable.SetBooleanPropertyValue("Number of Layers bool", True)
        layer_count = max(1, int(round(dims[axis] / float(size_mm))))
        builder.PropertyTable.SetIntegerPropertyValue("number of layers", layer_count)
        before = set(int(mesh.Tag) for mesh in fem.BaseFEModel.MeshManager.GetMeshes())
        builder.CommitMesh()
        meshes = [mesh for mesh in fem.BaseFEModel.MeshManager.GetMeshes() if int(mesh.Tag) not in before]
        if len(meshes) != 1:
            raise RuntimeError("hex builder created %d meshes" % len(meshes))
        return meshes[0], float(size_mm), 0, "HEX8"
    finally:
        builder.Destroy()


def create_one_mesh(fem, cae_body, body_row, size_mm, uf, mesh_config):
    if mesh_config.get("strategy") == "hybrid_hex" and body_row["object_type"] != "shell":
        try:
            return create_hex_mesh(fem, cae_body, body_row, size_mm, uf)
        except Exception:
            if mesh_config.get("structured_required"):
                raise
    body_mesh = body_row.get("mesh")
    retry_scales = tuple(body_mesh.get("retry_scales", (1.0, 1.5))) if isinstance(body_mesh, dict) else (1.0, 1.5)
    return create_tet_mesh(fem, cae_body, size_mm, retry_scales=retry_scales)


def semantic_element_faces(uf, mesh_row, face_row):
    located = uf.Sf.LocateElement(mesh_row["mesh_tag"])
    element_tags = list(located[1]) if isinstance(located, tuple) else list(located)
    if "normal" in face_row:
        normal = [float(value) for value in face_row["normal"]]
        origin = [float(value) for value in face_row["plane_origin_mm"]]
        bounds = expected_face_bbox(face_row)
    else:
        axis = int(face_row["axis"])
        normal = [0.0, 0.0, 0.0]
        normal[axis] = 1.0
        origin = [0.0, 0.0, 0.0]
        origin[axis] = float(face_row["plane_value_mm"])
        bounds = expected_face_bbox(face_row)
    face_count = 6 if mesh_row["element_type"] == "HEX8" else 4
    tolerance = 1.0e-3
    plane_tolerance = tolerance
    if int(mesh_row.get("source_box_count", 0)) > 1:
        plane_tolerance = max(tolerance, min(2.0, float(mesh_row["actual_size_mm"]) * 0.1))
    result = []
    for element_tag in element_tags:
        element = NXOpen.TaggedObjectManager.GetTaggedObject(int(element_tag))
        for face_index in range(face_count):
            nodes = list(element.GetCornerNodesOnFace(face_index))
            if not nodes:
                continue
            coordinates = []
            for node in nodes:
                node_data = uf.Sf.AskNode(node.Tag)
                coordinates.append(list(node_data[-1]))
            if not all(
                abs(sum((float(point[index]) - origin[index]) * normal[index] for index in range(3))) <= plane_tolerance
                for point in coordinates
            ):
                continue
            face_min = [min(float(point[index]) for point in coordinates) for index in range(3)]
            face_max = [max(float(point[index]) for point in coordinates) for index in range(3)]
            if not all(
                face_max[index] >= bounds[index] - tolerance
                and face_min[index] <= bounds[index + 3] + tolerance
                for index in range(3)
            ):
                continue
            result.append(element.GetElemFace(face_index))
    if not result:
        raise RuntimeError("semantic face %s resolved to zero explicit element faces" % face_row["face_id"])
    return result


def create_groups_and_meshes(fem, master, semantics, thermal_case, uf, materials, observer):
    modeling_bodies = body_map(master)
    body_rows = dict(
        (row["business_id"], row) for row in semantics["bodies"] if row.get("analysis_enabled", True)
    )
    polygon_bodies = {}
    body_groups = {}
    face_groups = {}
    face_objects = {}
    mesh_rows = []
    all_body_objects = []

    for business_id, row in body_rows.items():
        if business_id not in modeling_bodies:
            raise RuntimeError("master PRT body is missing: %s" % business_id)
        cae_body = polygon_body(uf, modeling_bodies[business_id])
        polygon_bodies[business_id] = cae_body
        all_body_objects.append(cae_body)
        body_groups[business_id] = fem.CaeGroups.CreateGroup("BODY_" + safe_name(business_id), [cae_body])

    all_body_group = fem.CaeGroups.CreateGroup("ALL_BODIES", all_body_objects)
    face_match_errors = {}
    for face_row in semantics["faces"]:
        cae_face, error = match_polygon_face(uf, polygon_bodies[face_row["owner_id"]], face_row)
        face_objects[face_row["face_id"]] = cae_face
        face_groups[face_row["face_id"]] = fem.CaeGroups.CreateGroup("FACE_" + safe_name(face_row["face_id"]), [cae_face])
        face_match_errors[face_row["face_id"]] = error

    observer.geometry_ready(all_body_objects)
    mesh_total = len(body_rows)
    for mesh_index, (business_id, row) in enumerate(body_rows.items(), 1):
        requested_size = mesh_size(row, thermal_case["mesh"])
        try:
            mesh, actual_size, retry_count, element_type = create_one_mesh(
                fem, polygon_bodies[business_id], row, requested_size, uf, thermal_case["mesh"]
            )
        except Exception:
            raise RuntimeError("mesh failed for business body %s:\n%s" % (business_id, traceback.format_exc()))
        mesh.SetName("MESH_" + safe_name(business_id))
        assign_material_to_mesh(fem, mesh, materials[row["material_id"]], "MC_" + safe_name(business_id))
        located = uf.Sf.LocateElement(mesh.Tag)
        element_tags = list(located[1]) if isinstance(located, tuple) else list(located)
        node_tags = set()
        for element_tag in element_tags:
            located_nodes = uf.Sf.LocateNodesOnElement(element_tag)
            values = located_nodes[1] if isinstance(located_nodes, tuple) else located_nodes
            for node_tag in values:
                node_tags.add(int(node_tag))
        element_objects = [NXOpen.TaggedObjectManager.GetTaggedObject(int(tag)) for tag in element_tags]
        observer.mesh_created(business_id, mesh, element_objects, mesh_index, mesh_total)
        mesh_rows.append(
            {
                "business_id": business_id,
                "component_id": row.get("component_id", business_id),
                "mesh_tag": int(mesh.Tag),
                "requested_size_mm": requested_size,
                "actual_size_mm": actual_size,
                "retry_count": retry_count,
                "element_count": len(element_tags),
                "node_count": len(node_tags),
                "material_id": row["material_id"],
                "element_type": element_type,
                "source_box_count": int(row.get("source_box_count", 0)),
                "mesh_density_policy": row.get("mesh", {}).get("policy", "standard"),
            }
        )

    observer.meshes_complete(mesh_total)

    mesh_by_body = dict((row["business_id"], row) for row in mesh_rows)
    element_groups = {}
    component_elements = {}
    all_elements = []
    for business_id, mesh_row in mesh_by_body.items():
        located = uf.Sf.LocateElement(mesh_row["mesh_tag"])
        element_tags = list(located[1]) if isinstance(located, tuple) else list(located)
        elements = [NXOpen.TaggedObjectManager.GetTaggedObject(int(tag)) for tag in element_tags]
        element_groups[business_id] = fem.CaeGroups.CreateGroup("ELEMENTS_" + safe_name(business_id), elements)
        component_id = body_rows[business_id].get("component_id", business_id)
        component_elements.setdefault(component_id, []).extend(elements)
        all_elements.extend(elements)
    component_element_groups = dict(
        (component_id, fem.CaeGroups.CreateGroup("COMPONENT_ELEMENTS_" + safe_name(component_id), elements))
        for component_id, elements in component_elements.items()
    )
    all_element_group = fem.CaeGroups.CreateGroup("ALL_ELEMENTS", all_elements)
    element_face_groups = {}
    element_face_counts = {}
    for face_row in semantics["faces"]:
        element_faces = semantic_element_faces(uf, mesh_by_body[face_row["owner_id"]], face_row)
        group = fem.CaeGroups.CreateGroup("ELEM_FACE_" + safe_name(face_row["face_id"]), element_faces)
        element_face_groups[face_row["face_id"]] = group
        element_face_counts[face_row["face_id"]] = len(element_faces)

    fem.Save(fem.SaveComponents.TrueValue, fem.CloseAfterSave.FalseValue)
    return {
        "body_groups": body_groups,
        "face_groups": face_groups,
        "face_objects": face_objects,
        "element_face_groups": element_face_groups,
        "element_face_counts": element_face_counts,
        "element_groups": element_groups,
        "component_element_groups": component_element_groups,
        "all_element_group": all_element_group,
        "all_body_group": all_body_group,
        "all_body_objects": all_body_objects,
        "polygon_bodies": polygon_bodies,
        "mesh_rows": mesh_rows,
        "face_match_errors_mm": face_match_errors,
    }


def create_sim(session, fem, sim_path):
    if os.path.exists(sim_path):
        os.remove(sim_path)
    file_new = session.Parts.FileNew()
    try:
        file_new.TemplateFileName = "SimSpaceSysThermalMetric.sim"
        file_new.TemplateType = NXOpen.FileNewTemplateType.Item
        file_new.Application = NXOpen.FileNewApplication.CaeSim
        file_new.Units = NXOpen.Part.Units.Millimeters
        file_new.NewFileName = sim_path
        file_new.MasterFileName = ""
        file_new.MakeDisplayedPart = True
        file_new.UseBlankTemplate = False
        sim = file_new.Commit()
    finally:
        try:
            file_new.Destroy()
        except Exception:
            pass
    if sim is None:
        sim = session.Parts.BaseWork
    sim.FinalizeCreation(fem, -1, [])
    sim.Save(sim.SaveComponents.TrueValue, sim.CloseAfterSave.FalseValue)
    return sim


def target_group(builder, index, filter_type, group):
    if builder.TargetSetManager.TargetSetCount <= index:
        builder.TargetSetManager.CreateCaeSet()
    builder.TargetSetManager.SetTargetSetGroup(index, filter_type, group)


def target_element_face_group(builder, index, group):
    if builder.TargetSetManager.TargetSetCount <= index:
        builder.TargetSetManager.CreateCaeSet()
    members = []
    for element_face in group.GetEntities():
        resolved = element_face.GetElementsAndFaceIds()
        if not isinstance(resolved, tuple) or len(resolved) != 2:
            raise RuntimeError("unexpected element-face resolution for %s" % group.GetName())
        elements, face_ids = resolved
        for element, face_id in zip(elements, face_ids):
            members.append(NXOpen.CAE.SetObject(element, NXOpen.CAE.CaeSetObjectSubType.ElementFace, int(face_id)))
    if not members:
        raise RuntimeError("element-face group %s has no explicit members" % group.GetName())
    builder.TargetSetManager.SetTargetSetMembers(index, members)
    return len(members)


def map_groups_to_sim(sim, fem_groups):
    by_name = dict((group.GetName(), group) for group in sim.CaeGroups)
    result = {
        "body_groups": {},
        "face_groups": {},
        "element_face_groups": {},
        "element_groups": {},
        "component_element_groups": {},
        "all_body_group": by_name[fem_groups["all_body_group"].GetName()],
        "all_element_group": by_name[fem_groups["all_element_group"].GetName()],
    }
    for business_id, group in fem_groups["body_groups"].items():
        result["body_groups"][business_id] = by_name[group.GetName()]
    for face_id, group in fem_groups["face_groups"].items():
        result["face_groups"][face_id] = by_name[group.GetName()]
    for face_id, group in fem_groups["element_face_groups"].items():
        result["element_face_groups"][face_id] = by_name[group.GetName()]
    for business_id, group in fem_groups["element_groups"].items():
        result["element_groups"][business_id] = by_name[group.GetName()]
    for component_id, group in fem_groups["component_element_groups"].items():
        result["component_element_groups"][component_id] = by_name[group.GetName()]
    return result


def commit_bc(builder, solution):
    try:
        bc = builder.CommitBc()
    finally:
        builder.Destroy()
    if bc is None:
        raise RuntimeError("SST boundary builder returned no object")
    solution.AddBc(bc)
    return bc


def contact_area_mm2(source, target):
    if "area_mm2" in source:
        return float(source["area_mm2"])
    a = source["bbox_2d_mm"]
    b = target["bbox_2d_mm"]
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[2], b[2]))


def property_names(table):
    return [table.GetPropertyNameByIndex(index) for index in range(table.GetPropertyCount())]


def create_solution_and_bcs(sim, groups, semantics, thermal_case, observer):
    simulation = sim.Simulation
    analysis_mode = "steady"
    solution_name = "steady_state_thermal"
    solution = simulation.CreateSolution(
        "NX SPACE SYSTEMS THERMAL",
        "Thermal",
        "Space Systems Thermal",
        solution_name,
    )
    if observer.enabled:
        simulation.ActiveSolution = solution
    solution.PropertyTable.SetBooleanPropertyValue("Foreground", False)
    solution.PropertyTable.SetBooleanPropertyValue("Run Solution in Parallel", False)
    solution.PropertyTable.SetBooleanPropertyValue("Temperatures", True)
    solution.PropertyTable.SetDoublePropertyValue(
        "Radiative Environment Temperature",
        float(thermal_case["radiation"]["environment_temperature_K"]) - 273.15,
    )
    solver_config = thermal_case.get("solver", {})
    solver_options = solution.SolverOptionsPropertyTable
    solver_options.SetDoublePropertyValue("Convergence Criterion", float(solver_config.get("cg_tolerance", 1.0e-6)))
    solver_options.SetIntegerPropertyValue("Conjugate Gradient Solver Iteration Limit", int(solver_config.get("cg_iteration_limit", 500)))
    solver_options.SetIntegerPropertyValue("Convergence Matrix Fill Value", int(solver_config.get("cg_matrix_fill", 200)))
    solver_options.SetDoublePropertyValue(
        "Steady State - Maximum Temperature Change",
        float(solver_config.get("steady_max_temperature_change_K", 0.01)),
    )
    created = []
    heat_count = 0
    heat_generation_unit = sim.UnitCollection.FindObject("HeatGeneration_Metric2")
    heat_flux_unit = sim.UnitCollection.FindObject("HeatFlux_Metric5")
    kelvin = sim.UnitCollection.FindObject("Kelvin")
    thermal_resistance = sim.UnitCollection.FindObject("ThermalResistance_Metric4")
    thermal_conductance = sim.UnitCollection.FindObject("ThermalConductance_Metric4")
    faces = dict((row["face_id"], row) for row in semantics["faces"])

    observer.begin_bc_phase("initial")
    initial = simulation.CreateBcBuilderForConstraintDescriptor("Initial Temperature", "INITIAL_TEMPERATURE")
    target_group(initial, 0, NXOpen.CAE.CaeSetGroupFilterType.SolidElement, groups["all_element_group"])
    # NX12 SST stores this unitless-looking property in degrees Celsius.
    initial.PropertyTable.SetDoublePropertyValue("Temperature", float(thermal_case["initial_temperature_K"]) - 273.15)
    initial_bc = commit_bc(initial, solution)
    created.append(initial_bc)
    observer.bc_created("initial", "INITIAL_TEMPERATURE", initial_bc, 1, 1)
    observer.bc_phase_completed("initial", sim)

    component_volumes = {}
    for body in semantics["bodies"]:
        component_id = body.get("component_id", body["business_id"])
        component_volumes[component_id] = component_volumes.get(component_id, 0.0) + float(body.get("volume_mm3") or 0.0)
    heat_components = [
        (component_id, float(power))
        for component_id, power in thermal_case.get("component_power_W", {}).items()
        if float(power) > 0.0
    ]
    observer.begin_bc_phase("heat")
    for heat_index, (component_id, power) in enumerate(heat_components, 1):
        volume_mm3 = float(component_volumes.get(component_id) or 0.0)
        if volume_mm3 <= 0.0:
            raise RuntimeError("heat-generating component has non-positive volume: %s" % component_id)
        volumetric_power = power / volume_mm3
        builder = simulation.CreateBcBuilderForLoadDescriptor("Heat Generation", "HEAT_" + safe_name(component_id))
        target_group(
            builder, 0, NXOpen.CAE.CaeSetGroupFilterType.SolidElement, groups["component_element_groups"][component_id]
        )
        builder.PropertyTable.SetScalarWithDataPropertyValue("Heat Generation", volumetric_power, heat_generation_unit)
        heat_bc = commit_bc(builder, solution)
        created.append(heat_bc)
        observer.bc_created("heat", component_id, heat_bc, heat_index, len(heat_components))
        heat_count += 1
    observer.bc_phase_completed("heat", sim)

    radiation = thermal_case["radiation"]
    radiation_faces = list(semantics["radiation_face_ids"])
    observer.begin_bc_phase("radiation")
    for radiation_index, face_id in enumerate(radiation_faces, 1):
        builder = simulation.CreateBcBuilderForConstraintDescriptor("Simple Environment Radiation", "RAD_" + safe_name(face_id))
        target_group(builder, 0, NXOpen.CAE.CaeSetGroupFilterType.GeomFace, groups["face_groups"][face_id])
        builder.PropertyTable.SetDoublePropertyValue("Emissivity Value", float(radiation["emissivity"]))
        builder.PropertyTable.SetDoublePropertyValue("View Factor", float(radiation["view_factor"]))
        builder.PropertyTable.SetIntegerPropertyValue("Temperature Type", 1)
        builder.PropertyTable.SetScalarWithDataPropertyValue("Temperature", float(radiation["environment_temperature_K"]), kelvin)
        radiation_bc = commit_bc(builder, solution)
        created.append(radiation_bc)
        observer.bc_created("radiation", face_id, radiation_bc, radiation_index, len(radiation_faces))
    observer.bc_phase_completed("radiation", sim)

    flux_rows = []
    external_flux = thermal_case.get("external_heat_flux", {})
    if external_flux.get("enabled"):
        external_flux_faces = sorted(external_flux.get("faces_W_m2", {}).items())
        observer.begin_bc_phase("flux")
        for flux_index, (face_id, absorbed_flux) in enumerate(external_flux_faces, 1):
            if float(absorbed_flux) == 0.0:
                continue
            if face_id not in groups["face_groups"]:
                raise RuntimeError("external heat flux references unknown face: %s" % face_id)
            builder = simulation.CreateBcBuilderForLoadDescriptor("Heat Flux", "FLUX_" + safe_name(face_id))
            target_group(builder, 0, NXOpen.CAE.CaeSetGroupFilterType.GeomFace, groups["face_groups"][face_id])
            builder.PropertyTable.SetScalarWithDataPropertyValue("Heat Flux", float(absorbed_flux), heat_flux_unit)
            flux_bc = commit_bc(builder, solution)
            created.append(flux_bc)
            observer.bc_created("flux", face_id, flux_bc, flux_index, len(external_flux_faces))
            flux_rows.append({"face_id": face_id, "absorbed_heat_flux_W_m2": float(absorbed_flux)})
        observer.bc_phase_completed("flux", sim)

    contact_rows = []
    contacts = list(semantics["contacts"])
    observer.begin_bc_phase("contact")
    for contact_index, contact in enumerate(contacts, 1):
        source = faces[contact["source_face_id"]]
        target = faces[contact["target_face_id"]]
        area_mm2 = float(contact.get("area_mm2") or contact_area_mm2(source, target))
        if area_mm2 <= 0.0:
            raise RuntimeError("contact has zero overlap area: %s" % contact["contact_id"])
        # NX12 SST Manual Pairing rejects valid solid-face meshes during setup
        # checking. Thermal Coupling accepts the same face regions and total
        # resistance model, so use it consistently for deterministic solves.
        descriptor = "Contact Thermal Coupling"
        builder = simulation.CreateBcBuilderForSimulationObjectDescriptor(descriptor, safe_name(contact["contact_id"]))
        source_member_count = target_element_face_group(
            builder, 0, groups["element_face_groups"][contact["source_face_id"]]
        )
        target_member_count = target_element_face_group(
            builder, 1, groups["element_face_groups"][contact["target_face_id"]]
        )
        area_resistance = contact.get("resistance_m2K_W")
        total_resistance = None
        total_conductance = None
        if contact["kind"] == "wall_shell":
            builder.PropertyTable.SetIntegerPropertyValue("Type", 0)
            total_conductance = 1.0e12
            builder.PropertyTable.SetScalarWithDataPropertyValue("Total Conductance", total_conductance, thermal_conductance)
            requested_model = "ideal_conductance"
        else:
            area_resistance = float(area_resistance)
            total_resistance = 0.0 if area_resistance == 0.0 else area_resistance / (area_mm2 * 1.0e-6)
            builder.PropertyTable.SetIntegerPropertyValue("Type", 1)
            builder.PropertyTable.SetScalarWithDataPropertyValue("Total Resistance", total_resistance, thermal_resistance)
            requested_model = "area_resistance"
        try:
            contact_bc = commit_bc(builder, solution)
            created.append(contact_bc)
        except Exception:
            raise RuntimeError("failed to commit contact %s:\n%s" % (contact["contact_id"], traceback.format_exc()))
        observer.bc_created("contact", contact["contact_id"], contact_bc, contact_index, len(contacts))
        contact_rows.append(
            {
                "contact_id": contact["contact_id"],
                "descriptor": descriptor,
                "kind": contact["kind"],
                "requested_model": requested_model,
                "area_mm2": area_mm2,
                "area_resistance_m2K_W": area_resistance,
                "total_resistance_K_W": total_resistance,
                "total_conductance_W_K": total_conductance,
                "path_length_mm": contact.get("path_length_mm"),
                "source_element_face_count": source_member_count,
                "target_element_face_count": target_member_count,
            }
        )
    observer.bc_phase_completed("contact", sim)

    sim.Save(sim.SaveComponents.TrueValue, sim.CloseAfterSave.FalseValue)
    observer.bcs_complete()
    return solution, {
        "analysis_mode": analysis_mode,
        "solution_name": solution_name,
        "bc_count": len(created),
        "heat_generation_count": heat_count,
        "radiation_count": len(semantics["radiation_face_ids"]),
        "contact_count": len(contact_rows),
        "external_heat_flux_count": len(flux_rows),
        "external_heat_fluxes": flux_rows,
        "contacts": contact_rows,
    }


def extract_mesh_rows(uf, mesh_rows):
    node_rows = {}
    element_rows = []
    element_id_seen = set()
    for mesh_row in mesh_rows:
        mesh_tag = mesh_row["mesh_tag"]
        located = uf.Sf.LocateElement(mesh_tag)
        element_tags = list(located[1]) if isinstance(located, tuple) else list(located)
        for element_tag in element_tags:
            element_info = uf.Sf.AskElement(element_tag)
            element_id = int(element_info[0])
            if element_id in element_id_seen:
                continue
            element_id_seen.add(element_id)
            located_nodes = uf.Sf.LocateNodesOnElement(element_tag)
            node_tags = list(located_nodes[1]) if isinstance(located_nodes, tuple) else list(located_nodes)
            labels = []
            for node_tag in node_tags:
                node_info = uf.Sf.AskNode(node_tag)
                node_id = int(node_info[0])
                coordinates = list(node_info[-1])
                labels.append(node_id)
                node_rows[node_id] = {
                    "node_id": node_id,
                    "x_mm": float(coordinates[0]),
                    "y_mm": float(coordinates[1]),
                    "z_mm": float(coordinates[2]),
                    "business_id": mesh_row.get("component_id", mesh_row["business_id"]),
                }
            expected_count = 8 if mesh_row.get("element_type") == "HEX8" else 4
            if len(labels) != expected_count:
                raise RuntimeError("%s element %d has %d nodes" % (mesh_row.get("element_type"), element_id, len(labels)))
            element_row = {
                "element_id": element_id,
                "element_type": mesh_row.get("element_type", "TET4"),
                "business_id": mesh_row.get("component_id", mesh_row["business_id"]),
            }
            for index, node_id in enumerate(labels):
                element_row["n%d" % (index + 1)] = node_id
            element_rows.append(element_row)
    return node_rows, element_rows


def write_mesh_geometry(raw_results_dir, uf, mesh_rows):
    if not os.path.isdir(raw_results_dir):
        os.makedirs(raw_results_dir)
    node_rows, element_rows = extract_mesh_rows(uf, mesh_rows)
    nodes_path = os.path.join(raw_results_dir, "nodes_geometry.csv")
    with open(nodes_path, "w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["node_id", "x_mm", "y_mm", "z_mm", "business_id"])
        writer.writeheader()
        for node_id in sorted(node_rows):
            writer.writerow(node_rows[node_id])
    elements_path = os.path.join(raw_results_dir, "elements.csv")
    with open(elements_path, "w", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=["element_id", "element_type", "n1", "n2", "n3", "n4", "n5", "n6", "n7", "n8", "business_id"],
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in element_rows:
            writer.writerow(row)
    return {"node_count": len(node_rows), "element_count": len(element_rows)}


def wait_for_solver_files(sim_path, solution_name, timeout_seconds=3600):
    directory = os.path.dirname(sim_path)
    stem = os.path.splitext(os.path.basename(sim_path))[0]
    prefix = stem + "-" + solution_name
    log_path = os.path.join(directory, prefix + ".log")
    result_path = os.path.join(directory, prefix + ".bun")
    deadline = time.time() + float(timeout_seconds)
    while time.time() < deadline:
        log_complete = False
        if os.path.exists(log_path):
            with open(log_path, "r", errors="replace") as stream:
                log_text=stream.read()
                if 'User requested STOP' in log_text:
                    raise RuntimeError('Native SST stopped before temperature results; log reports User requested STOP: '+log_path)
                log_complete = "End of run" in log_text
        if log_complete and os.path.exists(result_path) and os.path.getsize(result_path) > 0:
            return {"log_path": log_path, "result_path": result_path}
        time.sleep(5.0)
    raise RuntimeError("timed out waiting for SST solver completion: %s" % prefix)


def remove_stale_solution_outputs(sim_path, solution_name):
    directory = os.path.dirname(sim_path)
    sim_stem = os.path.splitext(os.path.basename(sim_path))[0]
    prefix = (sim_stem + "-" + solution_name).lower()
    for name in os.listdir(directory):
        if name.lower().startswith(prefix):
            path = os.path.join(directory, name)
            if os.path.isfile(path):
                os.remove(path)


def execute():
    task = read_json(os.environ["NX_THERMAL_TASK"])
    result = {"ok": False}
    observer = StageObserver()
    try:
        session = NXOpen.Session.GetSession()
        uf = NXOpen.UF.UFSession.GetUFSession()
        semantics = read_json(task["semantics_path"])
        binding = read_json(task["nx_binding_path"])
        apply_step_geometry(semantics, binding)
        thermal_case = read_json(task["thermal_case_path"])
        master = open_base_display(session, task["master_part"])
        fem = create_fem(session, master, task["fem_path"], semantics)
        session.Parts.SetDisplay(fem, False, False)
        session.Parts.SetWork(fem)
        materials = create_materials(fem, thermal_case["materials"])
        groups = create_groups_and_meshes(fem, master, semantics, thermal_case, uf, materials, observer)
        mesh_report = {
            "ok": True,
            "body_count": len(groups["mesh_rows"]),
            "total_element_count": sum(row["element_count"] for row in groups["mesh_rows"]),
            "total_node_count_by_body": sum(row["node_count"] for row in groups["mesh_rows"]),
            "meshes": groups["mesh_rows"],
            "face_match_errors_mm": groups["face_match_errors_mm"],
            "element_face_counts": groups["element_face_counts"],
        }
        write_json(os.path.join(task["run_dir"], "nx", "mesh_report.json"), mesh_report)
        mesh_geometry = write_mesh_geometry(task["raw_results_dir"], uf, groups["mesh_rows"])

        sim = create_sim(session, fem, task["sim_path"])
        if observer.enabled:
            session.Parts.SetDisplay(sim, False, False)
        session.Parts.SetWork(sim)
        sim_groups = map_groups_to_sim(sim, groups)
        observer.simulation_ready(sim_groups)
        solution, physics_report = create_solution_and_bcs(sim, sim_groups, semantics, thermal_case, observer)
        remove_stale_solution_outputs(task["sim_path"], solution.Name)
        setup_status = solution.Solve(
            NXOpen.CAE.SimSolution.SolveOption.WriteSolverInputFile,
            NXOpen.CAE.SimSolution.SetupCheckOption.CompleteCheckAndOutputErrors,
        )
        observer.solve_started(setup_status, "actual")
        solve_status = solution.Solve(
            NXOpen.CAE.SimSolution.SolveOption.Solve,
            NXOpen.CAE.SimSolution.SetupCheckOption.CompleteCheckAndOutputErrors,
        )
        solver_files = wait_for_solver_files(
            task["sim_path"], solution.Name, int(task.get("solver_timeout_seconds", 7200))
        )
        observer.solve_completed(solve_status, "actual", solution, sim, solver_files.get("result_path"))
        sim.Save(sim.SaveComponents.TrueValue, sim.CloseAfterSave.FalseValue)
        solve_report = {
            "ok": True,
            "setup_status": str(setup_status),
            "solve_status": str(solve_status),
            "solver_completed": True,
            "solver_files": solver_files,
            "physics": physics_report,
            "mesh_geometry": mesh_geometry,
            "total_power_W": float(thermal_case["total_power_W"]),
        }
        write_json(os.path.join(task["run_dir"], "nx", "solve_report.json"), solve_report)
        result.update({"ok": True, "mesh_report": mesh_report, "solve_report": solve_report})
    except Exception:
        result["error"] = traceback.format_exc()
        write_json(os.path.join(task["run_dir"], "nx", "solve_report.json"), result)
    write_json(task["result_path"], result)


if __name__ == "__main__":
    execute()
