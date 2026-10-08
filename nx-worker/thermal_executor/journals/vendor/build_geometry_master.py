from __future__ import print_function

import hashlib
import json
import os
import re
import traceback

import NXOpen
import NXOpen.Features
import NXOpen.GeometricUtilities
import NXOpen.UF


FACE_TOLERANCE_MM = 1.0e-3


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
    name = re.sub(r"[^A-Za-z0-9_]+", "_", str(value)).strip("_")
    return name[:120] or "UNNAMED"


def open_base_display(session, path):
    opened = session.Parts.OpenBaseDisplay(path)
    return opened[0] if isinstance(opened, tuple) else opened


def set_string_attribute(value, title, content):
    value.SetUserAttribute(title, -1, str(content), NXOpen.Update.Option.Now)


def string_attribute(value, title):
    try:
        return value.GetStringUserAttribute(title, -1)
    except Exception:
        return None


def unique_edge_points(face):
    points = []
    for edge in face.GetEdges():
        for point in edge.GetVertices():
            row = [float(point.X), float(point.Y), float(point.Z)]
            if not any(max(abs(row[index] - other[index]) for index in range(3)) < 1.0e-8 for other in points):
                points.append(row)
    return points


def face_signature(face):
    points = unique_edge_points(face)
    if not points:
        raise RuntimeError("face has no edge vertices")
    minimum = [min(point[index] for point in points) for index in range(3)]
    maximum = [max(point[index] for point in points) for index in range(3)]
    return minimum, maximum


def expected_face_bounds(face_row):
    if "bbox" in face_row:
        return [float(value) for value in face_row["bbox"]["min"]], [float(value) for value in face_row["bbox"]["max"]]
    axis = int(face_row["axis"])
    other = [index for index in range(3) if index != axis]
    bounds = face_row["bbox_2d_mm"]
    minimum = [None, None, None]
    maximum = [None, None, None]
    minimum[axis] = float(face_row["plane_value_mm"])
    maximum[axis] = float(face_row["plane_value_mm"])
    minimum[other[0]] = float(bounds[0])
    maximum[other[0]] = float(bounds[1])
    minimum[other[1]] = float(bounds[2])
    maximum[other[1]] = float(bounds[3])
    return minimum, maximum


def face_match_score(face, face_row):
    actual_min, actual_max = face_signature(face)
    expected_min, expected_max = expected_face_bounds(face_row)
    if "bbox" in face_row:
        error = max(
            max(abs(actual_min[index] - expected_min[index]) for index in range(3)),
            max(abs(actual_max[index] - expected_max[index]) for index in range(3)),
        )
        return error if error <= FACE_TOLERANCE_MM else None
    axis = int(face_row["axis"])
    if abs(actual_max[axis] - actual_min[axis]) > FACE_TOLERANCE_MM:
        return None
    plane_error = max(
        abs(actual_min[axis] - expected_min[axis]),
        abs(actual_max[axis] - expected_max[axis]),
    )
    if plane_error > FACE_TOLERANCE_MM:
        return None
    other = [index for index in range(3) if index != axis]
    bounds_error = 0.0
    for index in other:
        bounds_error += abs(actual_min[index] - expected_min[index])
        bounds_error += abs(actual_max[index] - expected_max[index])
    return plane_error * 1000.0 + bounds_error


def match_face(body, face_row):
    candidates = []
    for face in body.GetFaces():
        score = face_match_score(face, face_row)
        if score is not None:
            candidates.append((score, int(face.Tag), face))
    candidates.sort(key=lambda item: (item[0], item[1]))
    if not candidates:
        raise RuntimeError("no NX face matched semantic face %s" % face_row["face_id"])
    if len(candidates) > 1 and abs(candidates[0][0] - candidates[1][0]) < 1.0e-8:
        raise RuntimeError("ambiguous NX faces matched semantic face %s" % face_row["face_id"])
    return candidates[0][2], candidates[0][0]


def body_bbox(uf_session, body):
    points = []
    for face in body.GetFaces():
        points.extend(unique_edge_points(face))
    if not points:
        raise RuntimeError("body has no face edge vertices: %s" % body.Name)
    minimum = [min(point[index] for point in points) for index in range(3)]
    maximum = [max(point[index] for point in points) for index in range(3)]
    return minimum + maximum


def create_master_part(session, output_part):
    if os.path.exists(output_part):
        os.remove(output_part)
    part = session.Parts.NewDisplay(output_part, NXOpen.Part.Units.Millimeters)
    session.Parts.SetWork(part)
    if session.Parts.Work is None or int(session.Parts.Work.Tag) != int(part.Tag):
        raise RuntimeError("failed to make geometry master the NX work part")
    part.Save(part.SaveComponents.FalseValue, part.CloseAfterSave.FalseValue)
    return part


def translate_step_to_part(session, body_row, output_part, settings_file):
    if os.path.exists(output_part):
        os.remove(output_part)
    importer = session.DexManager.CreateStep214Importer()
    try:
        importer.InputFile = body_row.get("nx_step_file") or body_row["step_file"]
        importer.OutputFile = output_part
        importer.SettingsFile = settings_file
        importer.ImportTo = importer.ImportToOption.NewPart
        importer.FlattenAssembly = True
        importer.Optimize = False
        importer.SimplifyGeometry = False
        importer.SewSurfaces = False
        importer.SmoothBSurfaces = False
        importer.FileOpenFlag = False
        importer.ProcessHoldFlag = True
        importer.Validate()
        importer.Commit()
    finally:
        importer.Destroy()
    if not os.path.exists(output_part):
        raise RuntimeError("STEP translator produced no NX part for %s" % body_row["business_id"])


def import_native_part(part, input_part):
    before = set(int(body.Tag) for body in part.Bodies)
    importer = part.ImportManager.CreatePartImporter()
    try:
        importer.FileName = input_part
        importer.Scale = 1.0
        importer.CreateNamedGroup = False
        importer.ImportViews = False
        importer.ImportCamObjects = False
        importer.LayerOption = NXOpen.PartImporter.LayerOptionType.Original
        importer.DestinationCoordinateSystemSpecification = NXOpen.PartImporter.DestinationCoordinateSystemSpecificationType.Work
        orientation = NXOpen.Matrix3x3()
        orientation.Xx = 1.0
        orientation.Xy = 0.0
        orientation.Xz = 0.0
        orientation.Yx = 0.0
        orientation.Yy = 1.0
        orientation.Yz = 0.0
        orientation.Zx = 0.0
        orientation.Zy = 0.0
        orientation.Zz = 1.0
        importer.DestinationCoordinateSystem = part.NXMatrices.Create(orientation)
        importer.DestinationPoint = NXOpen.Point3d(0.0, 0.0, 0.0)
        importer.Commit()
    finally:
        importer.Destroy()
    return [body for body in part.Bodies if int(body.Tag) not in before]


def import_bodies(session, part, body_row, imported_parts_dir, settings_file):
    if session.Parts.Work is None or int(session.Parts.Work.Tag) != int(part.Tag):
        session.Parts.SetWork(part)
    if not os.path.isdir(imported_parts_dir):
        os.makedirs(imported_parts_dir)
    intermediate_part = os.path.join(imported_parts_dir, safe_name(body_row["exchange_name"]) + ".prt")
    translate_step_to_part(session, body_row, intermediate_part, settings_file)
    created = import_native_part(part, intermediate_part)
    expected_count = 6 if body_row["business_id"] == "outer_shell" and body_row.get("nx_step_file") else 1
    if len(created) != expected_count:
        raise RuntimeError(
            "native part import for %s created %d bodies, expected %d; work=%s source_part=%s total_bodies=%d"
            % (
                body_row["business_id"],
                len(created),
                expected_count,
                getattr(session.Parts.Work, "FullPath", None),
                intermediate_part,
                len(list(part.Bodies)),
            )
        )
    return created


def unite_two_bodies(part, target, tool):
    builder = part.Features.CreateBooleanBuilderUsingCollector(NXOpen.Features.BooleanFeature.Null)
    try:
        builder.Operation = NXOpen.Features.Feature.BooleanType.Unite
        builder.Target = target
        builder.Tool = tool
        builder.RetainTarget = False
        builder.RetainTool = False
        feature = builder.CommitFeature()
        bodies = list(feature.GetBodies())
        if len(bodies) != 1:
            raise RuntimeError("NX boolean unite returned %d bodies" % len(bodies))
        return bodies[0]
    finally:
        builder.Destroy()


def unite_bodies(part, bodies):
    result = bodies[0]
    for tool in bodies[1:]:
        result = unite_two_bodies(part, result, tool)
    return result


def apply_assembly_placement(part, body, placement):
    if not placement:
        return body
    origin = placement.get("originMm")
    x_axis = placement.get("xAxis")
    y_axis = placement.get("yAxis")
    z_axis = placement.get("zAxis")
    if not all(isinstance(row, list) and len(row) == 3 for row in (origin, x_axis, y_axis, z_axis)):
        raise RuntimeError("invalid component coordinate system")
    matrix = NXOpen.Matrix3x3()
    matrix.Xx, matrix.Xy, matrix.Xz = [float(value) for value in x_axis]
    matrix.Yx, matrix.Yy, matrix.Yz = [float(value) for value in y_axis]
    matrix.Zx, matrix.Zy, matrix.Zz = [float(value) for value in z_axis]
    builder = part.BaseFeatures.CreateMoveObjectBuilder(None)
    try:
        builder.MoveObjectResult = NXOpen.Features.MoveObjectBuilder.MoveObjectResultOptions.MoveOriginal
        builder.Associative = False
        builder.MoveParents = False
        builder.ObjectToMoveObject.Add(body)
        motion = builder.TransformMotion
        motion.SetOption(NXOpen.GeometricUtilities.ModlMotion.Options.Dynamic)
        motion.SetMoveHandle(False)
        motion.SetManipulatorOrigin(NXOpen.Point3d(*[float(value) for value in origin]))
        motion.SetManipulatorMatrix(matrix)
        builder.Commit()
    finally:
        builder.Destroy()
    return body


def geometry_hash(body_row, manifest_by_id):
    value = body_row.get("geometry_hash")
    if value:
        return value
    manifest = manifest_by_id.get(body_row["business_id"], {})
    return manifest.get("sha256", "unavailable")


def collect_attributes(value):
    result = {}
    for title in ("AGENT_OBJECT_ID", "COMPONENT_ID", "OBJECT_TYPE", "MATERIAL_ID", "GEOMETRY_HASH", "FACE_REGION_ID", "OWNER_ID", "FACE_ROLE"):
        item = string_attribute(value, title)
        if item is not None:
            result[title] = item
    return result


def validate_part(part, semantics, uf_session):
    expected_bodies = {row["business_id"]: row for row in semantics["bodies"]}
    actual_by_id = {}
    for body in part.Bodies:
        business_id = string_attribute(body, "AGENT_OBJECT_ID")
        if business_id:
            actual_by_id[business_id] = body
    if len(actual_by_id) != len(expected_bodies):
        raise RuntimeError(
            "persistent body attribute count is %d, expected %d"
            % (len(actual_by_id), len(expected_bodies))
        )

    face_rows_by_owner = {}
    for row in semantics["faces"]:
        face_rows_by_owner.setdefault(row["owner_id"], []).append(row)

    body_bindings = []
    face_bindings = []
    for business_id, body_row in expected_bodies.items():
        if business_id not in actual_by_id:
            raise RuntimeError("missing persistent NX body %s" % business_id)
        body = actual_by_id[business_id]
        actual_bbox = body_bbox(uf_session, body)
        if string_attribute(body, "OBJECT_TYPE") != body_row["object_type"]:
            raise RuntimeError("body %s OBJECT_TYPE did not persist" % business_id)
        if string_attribute(body, "MATERIAL_ID") != body_row["material_id"]:
            raise RuntimeError("body %s MATERIAL_ID did not persist" % business_id)
        body_bindings.append(
            {
                "business_id": business_id,
                "name": body.Name,
                "tag": int(body.Tag),
                "bbox": actual_bbox,
                "bbox_source": "NX imported STEP",
                "attributes": collect_attributes(body),
            }
        )

        named_faces = {}
        for face in body.GetFaces():
            face_id = string_attribute(face, "FACE_REGION_ID")
            if face_id:
                named_faces[face_id] = face
        for face_row in face_rows_by_owner.get(business_id, []):
            face_id = face_row["face_id"]
            if face_id not in named_faces:
                raise RuntimeError("face attribute did not persist: %s" % face_id)
            face = named_faces[face_id]
            score = face_match_score(face, face_row)
            if score is None:
                raise RuntimeError("persistent face geometry no longer matches: %s" % face_id)
            actual_min, actual_max = face_signature(face)
            points = unique_edge_points(face)
            face_bindings.append(
                {
                    "face_id": face_id,
                    "owner_id": business_id,
                    "name": face.Name,
                    "tag": int(face.Tag),
                    "match_score": score,
                    "bbox": {"min": actual_min, "max": actual_max},
                    "plane_origin_mm": [
                        sum(point[index] for point in points) / len(points) for index in range(3)
                    ],
                    "bbox_source": "NX imported STEP",
                    "attributes": collect_attributes(face),
                }
            )

    if len(face_bindings) != len(semantics["faces"]):
        raise RuntimeError(
            "persistent semantic face count is %d, expected %d"
            % (len(face_bindings), len(semantics["faces"]))
        )
    return {
        "body_count": len(body_bindings),
        "face_count": len(face_bindings),
        "bbox_source": "NX imported STEP",
        "bodies": body_bindings,
        "faces": face_bindings,
    }


def build(task, session, uf_session, semantics):
    output_part = task["output_part"]
    manifest_path = task["geometry_manifest_path"]
    manifest = read_json(manifest_path)
    manifest_by_id = dict((row["business_id"], row) for row in manifest["bodies"])
    part = create_master_part(session, output_part)

    aggregate = hashlib.sha256()
    faces_by_owner = {}
    for face_row in semantics["faces"]:
        faces_by_owner.setdefault(face_row["owner_id"], []).append(face_row)

    imported = []
    imported_parts_dir = os.path.join(task["run_dir"], "nx", "imported_parts")
    settings_file = os.path.join(task["nx_base"], "STEP214UG", "step214ug.def")
    geometry_source = semantics.get("geometry_source")
    if not isinstance(geometry_source, dict) or geometry_source.get("mode") != "simplified_assembly":
        raise RuntimeError("geometry semantics does not define a simplified assembly STEP")
    assembly_row = {
        "business_id": "assembly",
        "exchange_name": "ASSEMBLY",
        "step_file": os.path.join(task["run_dir"], "package", geometry_source["step_file"].replace("/", os.sep)),
    }
    if session.Parts.Work is None or int(session.Parts.Work.Tag) != int(part.Tag):
        session.Parts.SetWork(part)
    if not os.path.isdir(imported_parts_dir):
        os.makedirs(imported_parts_dir)
    intermediate_part = os.path.join(imported_parts_dir, "ASSEMBLY.prt")
    translate_step_to_part(session, assembly_row, intermediate_part, settings_file)
    created_bodies = import_native_part(part, intermediate_part)
    if len(created_bodies) != int(semantics["expected_body_count"]):
        raise RuntimeError(
            "simplified assembly import created %d bodies, expected %d"
            % (len(created_bodies), int(semantics["expected_body_count"]))
        )
    matched = {}
    for index, (body_row, body) in enumerate(zip(semantics["bodies"], created_bodies)):
        step_body_index = int(body_row.get("step_body_index", index))
        if step_body_index != index:
            raise RuntimeError(
                "STEP body sequence mismatch for %s: recorded %d, imported %d"
                % (body_row["business_id"], step_body_index, index)
            )
        matched[body_row["business_id"]] = (body, body_bbox(uf_session, body), index)

    for body_row in semantics["bodies"]:
        body, actual_bbox, step_body_index = matched[body_row["business_id"]]
        digest = geometry_hash(body_row, manifest_by_id)
        aggregate.update((body_row["business_id"] + ":" + digest + "\n").encode("utf-8"))
        body.SetName("BODY_" + safe_name(body_row["exchange_name"]))
        set_string_attribute(body, "AGENT_OBJECT_ID", body_row["business_id"])
        set_string_attribute(body, "COMPONENT_ID", body_row.get("component_id", body_row["business_id"]))
        set_string_attribute(body, "OBJECT_TYPE", body_row["object_type"])
        set_string_attribute(body, "MATERIAL_ID", body_row["material_id"])
        set_string_attribute(body, "GEOMETRY_HASH", digest)

        face_items = []
        used_tags = set()
        for face_row in faces_by_owner.get(body_row["business_id"], []):
            face, score = match_face(body, face_row)
            if int(face.Tag) in used_tags:
                raise RuntimeError("one NX face matched multiple semantic faces on %s" % body_row["business_id"])
            used_tags.add(int(face.Tag))
            face.SetName(safe_name(face_row["face_id"]))
            set_string_attribute(face, "FACE_REGION_ID", face_row["face_id"])
            set_string_attribute(face, "OWNER_ID", body_row["business_id"])
            set_string_attribute(face, "FACE_ROLE", face_row["role"])
            face_items.append({"face_id": face_row["face_id"], "tag": int(face.Tag), "match_score": score})
        imported.append(
            {
                "business_id": body_row["business_id"],
                "body_tag": int(body.Tag),
                "step_body_index": step_body_index,
                "bbox": actual_bbox,
                "bbox_source": "NX imported STEP",
                "semantic_faces": face_items,
            }
        )

    set_string_attribute(part, "AGENT_OBJECT_ID", "NX_THERMAL_GEOMETRY_MASTER")
    set_string_attribute(part, "OBJECT_TYPE", "geometry_master")
    set_string_attribute(part, "MATERIAL_ID", "mixed")
    set_string_attribute(part, "GEOMETRY_HASH", aggregate.hexdigest())
    part.Save(part.SaveComponents.FalseValue, part.CloseAfterSave.FalseValue)
    validation = validate_part(part, semantics, uf_session)
    binding = {
        "schema_version": "1.0",
        "part_path": output_part,
        "part_tag": int(part.Tag),
        "part_attributes": collect_attributes(part),
        "body_count": validation["body_count"],
        "face_count": validation["face_count"],
        "body_binding_method": "step_body_index",
        "bbox_source": validation["bbox_source"],
        "bodies": validation["bodies"],
        "faces": validation["faces"],
        "import_diagnostics": imported,
    }
    write_json(os.path.join(task["run_dir"], "nx", "nx_binding.json"), binding)
    return binding


def verify(task, session, uf_session, semantics):
    part = open_base_display(session, task["output_part"])
    validation = validate_part(part, semantics, uf_session)
    return {
        "part_path": task["output_part"],
        "body_count": validation["body_count"],
        "face_count": validation["face_count"],
        "body_binding_method": "persistent_attributes",
        "bbox_source": validation["bbox_source"],
        "part_attributes": collect_attributes(part),
    }


def execute():
    task_path = os.environ["NX_THERMAL_TASK"]
    task = read_json(task_path)
    result = {"ok": False, "mode": task.get("mode", "build")}
    try:
        session = NXOpen.Session.GetSession()
        uf_session = NXOpen.UF.UFSession.GetUFSession()
        semantics = read_json(task["semantics_path"])
        if task.get("mode") == "verify":
            details = verify(task, session, uf_session, semantics)
        else:
            details = build(task, session, uf_session, semantics)
        result.update({"ok": True, "details": details})
    except Exception:
        result["error"] = traceback.format_exc()
    write_json(task["result_path"], result)


if __name__ == "__main__":
    execute()
