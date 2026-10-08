from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath

from .schema import issue, origin
from .storage import DomainError

MAX_UPLOAD = int(os.environ.get("THERMAL_MAX_UPLOAD_MB", "200")) * 1024 * 1024
MAX_EXPANDED = 600 * 1024 * 1024
# Explicit values used by the canonical contract and the geometry handoff's
# equipment-envelope contract. Envelope coincidence is not source contact
# evidence; do not infer physical conductance from either representation.
SOURCE_EVIDENCE_CONTACT_POLICIES = {
    "source-evidence-required",
    "unverified-candidates-only; retained structure contacts require source evidence",
}


def available():
    return importlib.util.find_spec("cadquery") is not None and importlib.util.find_spec("trimesh") is not None


def safe_zip(path: Path, destination: Path):
    """Reject traversal, aliases, symlinks and zip bombs before writing anything."""
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise DomainError("无法读取 ZIP 文件。", "invalid_zip") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > 5000 or sum(i.file_size for i in infos) > MAX_EXPANDED:
            raise DomainError("ZIP 解压后过大。", "archive_too_large", 413)
        seen = set()
        for item in infos:
            name = item.filename.replace("\\", "/")
            parts = PurePosixPath(name).parts
            if name.startswith("/") or any(p in {"..", "."} or ":" in p for p in name.split("/")) or not parts:
                raise DomainError("ZIP 包含不安全路径。", "unsafe_archive")
            if stat.S_ISLNK(item.external_attr >> 16) or item.flag_bits & 1:
                raise DomainError("ZIP 不接受符号链接或加密成员。", "unsafe_archive")
            key = name.casefold().rstrip("/")
            if key in seen:
                raise DomainError("ZIP 包含重复路径。", "unsafe_archive")
            seen.add(key)
            if item.file_size > MAX_UPLOAD or (item.file_size > 1024 * 1024 and item.file_size / max(item.compress_size, 1) > 400):
                raise DomainError("ZIP 成员过大或压缩比异常。", "archive_too_large", 413)
        destination.mkdir(parents=True, exist_ok=True)
        for item in infos:
            target = destination.joinpath(*PurePosixPath(item.filename.replace("\\", "/")).parts)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)


def select_geometry(upload, directory):
    if upload.suffix.lower() in {".stp", ".step"}:
        return upload, None
    unpack = directory / "unpacked"
    safe_zip(upload, unpack)
    candidates = [f for f in unpack.rglob("*") if f.is_file() and f.suffix.lower() in {".step", ".stp"}]
    preferred = [f for f in candidates if f.name.lower() == "simplified.step"]
    if len(preferred) == 1:
        selected = preferred[0]
    elif len(candidates) == 1:
        selected = candidates[0]
    else:
        raise DomainError("几何包应包含唯一 STEP，或明确命名 simplified.step。", "ambiguous_geometry")
    properties = next(iter(unpack.rglob("properties.json")), None)
    metadata = json.loads(properties.read_text("utf-8-sig")) if properties and properties.stat().st_size < 10 * 1024 * 1024 else None
    return selected, metadata


def _component_records(metadata):
    if not metadata:
        return []
    if isinstance(metadata, list):
        return metadata
    for key in ("components", "parts", "bodies"):
        if isinstance(metadata.get(key), list):
            return metadata[key]
    return []


def step_name_records(path):
    """Read STEP assembly names with occurrence placements, for unique matching.

    Names are supplementary metadata. Geometry and NX indices continue to use
    the untouched importStep solid/face order; no piece is merged or discarded.
    """
    import cadquery as cq
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDF import TDF_Label, TDF_LabelSequence
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopLoc import TopLoc_Location
    from OCP.XCAFDoc import XCAFDoc_DocumentTool
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    if "RetDone" not in str(reader.ReadFile(str(path))):
        return []
    doc = TDocStd_Document(TCollection_ExtendedString("thermal"))
    if not reader.Transfer(doc):
        return []
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    records = []

    def label_name(label):
        value = TDataStd_Name()
        if not label.FindAttribute(TDataStd_Name.GetID_s(), value):
            return ""
        name = value.Get().ToExtString()
        def decode(match):
            try:
                return bytes.fromhex(match.group(1)).decode("utf-16-be")
            except ValueError:
                return match.group(0)
        return re.sub(r"(?:_X2_|\\X2\\)([0-9a-fA-F]+)(?:_X0_|\\X0\\)", decode, name)

    def visit(label, location, depth=0):
        if depth > 100:
            return
        target = TDF_Label()
        if not shape_tool.GetReferredShape_s(label, target):
            target = label
        native = shape_tool.GetShape_s(label)
        if native.IsNull():
            return
        world = location.Multiplied(native.Location())
        part = cq.Shape.cast(native.Located(world))
        children = TDF_LabelSequence()
        if shape_tool.IsAssembly_s(target):
            shape_tool.GetComponents_s(target, children, False)
        if children.Length():
            for i in range(1, children.Length() + 1):
                visit(children.Value(i), world, depth + 1)
        else:
            name = label_name(label)
            if not name or name.startswith("=>[") or "assembly relationship" in name.lower():
                name = label_name(target)
            if name:
                subshapes = part.Solids() or part.Shells() or part.Faces()
                for index, body in enumerate(subshapes):
                    bb = body.BoundingBox()
                    records.append({"name": name if len(subshapes) == 1 else f"{name} · 实体 {index+1}",
                                    "boundsMm": [bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax], "volumeMm3": body.Volume()})
    roots = TDF_LabelSequence()
    shape_tool.GetFreeShapes(roots)
    for i in range(1, roots.Length() + 1):
        visit(roots.Value(i), TopLoc_Location())
    return records


def import_geometry(upload, directory, example_names=None):
    if not available():
        raise DomainError("工程服务尚未安装几何依赖，请启动包含 CadQuery 的 Docker 服务。", "geometry_dependency_unavailable", 503)
    import cadquery as cq
    import numpy as np
    import trimesh

    selected, metadata = select_geometry(upload, directory)
    normalized = directory / "geometry.step"
    if selected.resolve() != normalized.resolve():
        shutil.copyfile(selected, normalized)
    sha = hashlib.sha256(normalized.read_bytes()).hexdigest()
    try:
        shape = cq.importers.importStep(str(normalized)).val()
    except Exception as exc:
        raise DomainError("STEP 读取失败；首版要求有效 STEP，未自动删减组件。", "step_read_failed") from exc
    solids = shape.Solids()
    # Include free sheets without also duplicating shells that bound a solid.
    solid_faces = {f.hashCode() for solid in solids for f in solid.Faces()}
    sheets = [s for s in shape.Shells() if not any(f.hashCode() in solid_faces for f in s.Faces())]
    sheet_faces = {f.hashCode() for s in sheets for f in s.Faces()}
    sheets.extend(f for f in shape.Faces() if f.hashCode() not in solid_faces | sheet_faces)
    pieces = [(s, "solid") for s in solids] + [(s, "shell") for s in sheets]
    if not pieces or len(pieces) > 5000:
        raise DomainError("STEP 中没有可用实体/曲面，或组件数量超出上限。", "unsupported_geometry")
    records = _component_records(metadata)
    if not records and not example_names:
        try:
            records = step_name_records(normalized)
        except Exception:
            records = []
    by_index = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        idx = record.get("stepIndex", record.get("step_index", record.get("bodyIndex")))
        if isinstance(idx, int) and 1 <= idx <= len(pieces) and idx not in by_index:
            by_index[idx] = record
    # Official geometry plugin v1 exports placed bounds/volume but no STEP
    # index. Bind only a unique measured geometric match, never array order.
    mapped_records = {id(rec) for rec in by_index.values()}
    for index, (part, _) in enumerate(pieces, 1):
        if index in by_index:
            continue
        bb = part.BoundingBox()
        actual = [bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax]
        matches = []
        for record in records:
            if not isinstance(record, dict) or id(record) in mapped_records:
                continue
            bounds = record.get("boundsMm")
            if isinstance(bounds, list) and len(bounds) == 6 and all(isinstance(v, (float, int)) for v in bounds):
                if max(abs(a-b) for a, b in zip(actual, bounds)) < 0.01:
                    volume = record.get("simplifiedVolumeMm3", record.get("volumeMm3"))
                    if volume is None or abs(part.Volume() - volume) <= max(.01, abs(volume) * 1e-5):
                        matches.append(record)
        if len(matches) == 1:
            by_index[index] = matches[0]
            mapped_records.add(id(matches[0]))
    scene = trimesh.Scene()
    components, problems = [], []
    triangles = 0
    palette = [(118, 160, 180, 255), (229, 169, 94, 255), (129, 174, 149, 255), (170, 147, 191, 255)]
    component_shapes = []
    for index, (part, kind) in enumerate(pieces, 1):
        cid = f"c-{sha[:12]}-{index}"
        bb = part.BoundingBox()
        center = part.Center()
        rec = by_index.get(index, {})
        name = str(rec.get("name", rec.get("label", example_names[index - 1] if example_names and index <= len(example_names) else f"组件 {index}")))
        component = {"id": cid, "name": name, "stepIndex": index, "kind": kind,
                     "center": [center.x, center.y, center.z], "size": [bb.xlen, bb.ylen, bb.zlen],
                     "boundsMm": [bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax],
                     "volumeMm3": part.Volume() if kind == "solid" else 0, "surfaceAreaMm2": part.Area(),
                     "faces": [], "provenance": {"geometry": origin("derived", "OpenCascade 读取原始 STEP", reason=f"STEP body #{index}"),
                                                    "material": origin(reason="源 STEP 未指定已核实材料，暂用铝合金参考值。")}}
        for key in ("material", "conductivity", "density", "specificHeat", "power", "emissivity", "absorptivity", "thickness"):
            if key in rec:
                component[key] = rec[key]
                component["provenance"][key] = origin("provided", "几何交付包 properties.json（明确序号或唯一实际几何匹配）")
        if rec.get("id"):
            component["sourceId"] = str(rec["id"])
        for face_index, face in enumerate(part.Faces(), 1):
            fid = f"{cid}:f{face_index}"
            ctr = face.Center()
            try:
                n = face.normalAt(ctr)
            except Exception:
                n = face.normalAt()
            fdata = {"id": fid, "index": face_index, "name": f"表面 {face_index}", "normal": [n.x, n.y, n.z],
                     "center": [ctr.x, ctr.y, ctr.z], "areaMm2": face.Area(), "geometryType": face.geomType()}
            face_bounds = face.BoundingBox()
            fdata["boundsMm"] = [face_bounds.xmin, face_bounds.ymin, face_bounds.zmin, face_bounds.xmax, face_bounds.ymax, face_bounds.zmax]
            if kind == "shell":
                fdata.update({"side": "top", "availableSides": ["top", "bottom"]})
            component["faces"].append(fdata)
            vertices, faces = face.tessellate(max(.2, max(bb.xlen, bb.ylen, bb.zlen) / 250), .2)
            if not faces:
                continue
            triangles += len(faces)
            if triangles > 2000000:
                raise DomainError("模型预览网格过大，请简化几何后导入。", "preview_too_large", 413)
            mesh = trimesh.Trimesh(vertices=np.array([[v.x, v.y, v.z] for v in vertices]), faces=np.array(faces), process=False)
            mesh.visual.face_colors = palette[(index - 1) % len(palette)]
            mesh.metadata.update({"componentId": cid, "faceId": fid, "stepIndex": index, "faceIndex": face_index, "source": "step-tessellation"})
            scene.add_geometry(mesh, node_name=fid, geom_name=fid, metadata={"componentId": cid, "faceId": fid})
        components.append(component)
        component_shapes.append(part)
    preview = directory / "preview.glb"
    preview.write_bytes(scene.export(file_type="glb"))
    # Exact touching planar faces establish candidate conductive paths. No
    # bounding-box-only guesses and no hidden brackets/screws are introduced.
    links = []
    policy = metadata.get("thermalGeometryPolicy", {}) if isinstance(metadata, dict) else {}
    contact_policy = policy.get("contactPolicy") if isinstance(policy, dict) else None
    source_evidence_required = isinstance(contact_policy, str) and contact_policy in SOURCE_EVIDENCE_CONTACT_POLICIES
    if source_evidence_required:
        problems.append(issue("设备包络交付包要求从原始几何或资料建立热连接；本次不把包络接触自动加入导热网络。", field="links"))
    def overlaps(a, b, tolerance=1e-5):
        return all(a[k] <= b[k + 3] + tolerance and b[k] <= a[k + 3] + tolerance for k in range(3))
    if not source_evidence_required and len(pieces) <= 150:
        planar_faces = [[(fi, face, components[i]["faces"][fi]) for fi, face in enumerate(part.Faces())
                         if face.geomType() == "PLANE"] for i, part in enumerate(component_shapes)]
        for i in range(len(component_shapes)):
            for j in range(i + 1, len(component_shapes)):
                try:
                    if not overlaps(components[i]["boundsMm"], components[j]["boundsMm"]):
                        continue
                    for fi, face, fa in planar_faces[i]:
                        for fj, counterpart, fb in planar_faces[j]:
                            if not overlaps(fa["boundsMm"], fb["boundsMm"]):
                                continue
                            if sum(a * b for a, b in zip(fa["normal"], fb["normal"])) > -.999:
                                continue
                            if abs(sum(n * (a - b) for n, a, b in zip(fa["normal"], fa["center"], fb["center"]))) > 1e-5:
                                continue
                            # Positive intersection area is the actual contact
                            # proof. Whole-body or face minimum-distance passes
                            # add no evidence and can stall on detailed B-reps.
                            shared = face.intersect(counterpart).Area()
                            if shared <= 1e-4:
                                continue
                            links.append({"id": f"l-{i}-{fi}-{j}-{fj}", "source": components[i]["id"], "target": components[j]["id"],
                                          "sourceFace": fa["id"], "targetFace": fb["id"], "kind": "contact", "value": .001,
                                          "unit": "m²·K/W", "contactAreaMm2": shared,
                                          "provenance": origin("estimated", "STEP 实际共面接触识别；接触热阻为工程估值", reason="接触面几何重合，未提供实测热阻。")})
                except Exception:
                    problems.append(issue("一对几何接触未能自动确认，请在拓扑中核查；未自动补建该路径。", components[i]["id"], "links"))
    elif not source_evidence_required:
        problems.append(issue("大装配未自动遍历所有接触对，可导入明确热连接或在拓扑中添加。", field="links"))
    if records and not by_index:
        problems.append(issue("几何包属性未提供明确 STEP 序号，已保留源文件；未按不可靠的列表顺序强行匹配。", field="documents"))
    return {"components": components, "links": links, "issues": problems, "stepPath": normalized, "previewPath": preview, "sha256": sha,
            "triangleCount": triangles, "identityMapping": "step-body-and-face-order-with-source-sha256", "units": "mm"}


def create_example(directory):
    if not available():
        raise DomainError("示例需要几何计算服务（CadQuery）。", "geometry_dependency_unavailable", 503)
    import cadquery as cq
    parts = [cq.Workplane("XY").box(280, 220, 8).translate((0, 0, -4)).val(),
             cq.Workplane("XY").box(80, 65, 38).translate((-60, 0, 19)).val(),
             cq.Workplane("XY").box(60, 75, 30).translate((65, 0, 15)).val()]
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "example.step"
    cq.exporters.export(cq.Compound.makeCompound(parts), str(path))
    return path, ["演示舱板", "演示载荷", "演示电源"]
