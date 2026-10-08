from __future__ import annotations

import copy
import math
import re

from .storage import DomainError, digest, now, uid

AXES = ["+X", "-X", "+Y", "-Y", "+Z", "-Z"]
DEFAULT_ORBIT = {"semiMajorAxisKm": 6978.137, "eccentricity": 0, "inclinationDeg": 97.79,
                 "raanDeg": 0, "argumentOfPerigeeDeg": 0, "trueAnomalyDeg": 0, "season": "summer"}
MATERIALS = {"aluminum": {"conductivity": 167., "density": 2700., "specificHeat": 900.},
             "copper": {"conductivity": 385., "density": 8960., "specificHeat": 385.},
             "steel": {"conductivity": 16., "density": 7850., "specificHeat": 500.},
             "titanium": {"conductivity": 7., "density": 4430., "specificHeat": 560.}}
MATERIAL_ALIASES = {"铝": "aluminum", "铝合金": "aluminum", "铝合金6061": "aluminum", "6061": "aluminum", "aluminium": "aluminum",
                    "铜": "copper", "不锈钢": "steel", "钢": "steel", "钛": "titanium", "钛合金": "titanium"}
FIELD_LABELS = {"conductivity": "导热率", "density": "密度", "specificHeat": "比热容", "emissivity": "红外发射率",
                "absorptivity": "太阳吸收率", "power": "功耗", "thickness": "板厚", "meshSizeMm": "网格尺寸",
                "spaceTemperatureK": "空间温度", "initialTemperatureK": "初始温度", "semiMajorAxisKm": "轨道半长轴",
                "eccentricity": "偏心率", "inclinationDeg": "轨道倾角", "raanDeg": "升交点赤经",
                "argumentOfPerigeeDeg": "近地点幅角", "trueAnomalyDeg": "初始真近点角"}


def origin(status="estimated", source="默认工程估值", original=None, reason=None):
    result = {"status": status, "source": source}
    if original is not None:
        result["originalValue"] = str(original) if isinstance(original, float) and not math.isfinite(original) else original
    if reason:
        result["reason"] = reason
    return result


def finite(value):
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def issue(message, cid=None, field=None, severity="warning", resolution=None):
    result = {"id": "i-" + digest([cid, field, message])[:20], "severity": severity, "message": message}
    if cid:
        result["componentId"] = cid
    if field:
        result["field"] = field
    if resolution:
        result["resolution"] = resolution
    return result


def number(obj, key, default, bounds, issues, cid=None, sources=None, units="", strict_min=False):
    raw = obj.get(key)
    value = finite(raw)
    valid = value is not None and bounds[0] <= value <= bounds[1] and (not strict_min or value > bounds[0])
    provenance = obj.setdefault("provenance", {})
    label = FIELD_LABELS.get(key, key)
    if not valid:
        value = float(default)
        message = f"{label}{'缺失' if raw is None else '超出合理范围'}，采用 {value:g} {units}。"
        provenance[key] = origin(original=raw, reason=message, source=sources or "工程估值（待复核）")
    else:
        current = provenance.get(key)
        if not isinstance(current, dict) or current.get("status") not in {"manual", "estimated", "derived", "provided"}:
            provenance[key] = origin("provided", "项目输入")
    obj[key] = value
    if provenance[key]["status"] == "estimated":
        issues.append(issue(provenance[key].get("reason", f"{label}使用估值 {value:g} {units}，可修改后重算。"), cid, key,
                            resolution="已填齐并允许继续；更正会创建新的运行。"))
    return value


def new_project(name):
    stamp = now()
    return {"id": uid("p-"), "name": str(name or "未命名热分析")[:160], "revision": 0,
            "createdAt": stamp, "updatedAt": stamp, "geometry": {"status": "empty", "source": "step"},
            "components": [], "links": [], "boundaries": [], "cases": [], "orbit": dict(DEFAULT_ORBIT),
            "settings": {"autoRun": True, "meshSizeMm": 10., "spaceTemperatureK": 3., "initialTemperatureK": 293.15},
            "documents": [], "issues": [], "runs": [], "_artifacts": {}}


def normalize(project):
    """Make a deterministic, complete steady-state input without hiding estimates.

    Numeric problems get explicitly sourced estimates. Object-reference problems
    raise validation errors: inventing a component or thermal path is not an
    authorized parameter estimate.
    """
    p = copy.deepcopy(project)
    issues = list(p.get("_importIssues", []))
    if len(p.get("components", [])) > 5000:
        raise DomainError("当前版本最多支持 5000 个组件。", "model_too_large", 413)
    ids = set()
    faces = {}
    for c in p["components"]:
        cid = c.get("id")
        if not isinstance(cid, str) or not cid or cid in ids:
            raise DomainError("组件 ID 必须存在且唯一。", "invalid_component_id")
        ids.add(cid)
        c["name"] = str(c.get("name", cid))[:160]
        if c.get("kind") not in {"solid", "shell"}:
            c["kind"] = "solid"
        raw_material = c.get("material")
        material = str(raw_material or "aluminum")
        c["material"] = MATERIAL_ALIASES.get(material.lower(), material.lower() if material.lower() in MATERIALS else material)
        defaults = MATERIALS.get(c["material"], MATERIALS["aluminum"])
        constant_source = f"{c['material']} 参考工程常数（并非牌号认证值）" if c["material"] in MATERIALS else f"未知材料 {c['material']}，暂按铝合金参考常数估算"
        c.setdefault("provenance", {})
        if not raw_material or "material" not in c["provenance"]:
            c["provenance"]["material"] = origin("provided" if raw_material else "estimated", "项目输入" if raw_material else "未指定材料，暂用铝合金", reason=None if raw_material else "材料缺失，暂按铝合金参考材料；会影响导热路径。")
        if c["provenance"]["material"].get("status") == "estimated":
            issues.append(issue(c["provenance"]["material"].get("reason", "材料使用工程估值。"), cid, "material"))
        for key, bounds, units in [("conductivity", (0, 10000), "W/(m·K)"), ("density", (0, 100000), "kg/m³"), ("specificHeat", (0, 100000), "J/(kg·K)")]:
            number(c, key, defaults[key], bounds, issues, cid, constant_source, units, True)
        number(c, "emissivity", .8, (0, 1), issues, cid, units="")
        number(c, "absorptivity", .2, (0, 1), issues, cid, units="")
        number(c, "power", 0, (0, 1e9), issues, cid, units="W")
        if c["kind"] == "shell":
            # A finite thickness must accompany every 2-D representation.
            number(c, "thickness", 2, (0, 1e4), issues, cid, units="mm", strict_min=True)
            c.setdefault("shell", {"normalConvention": "source-face", "sides": ["top", "bottom"], "reference": "geometry-surface"})
        else:
            c.pop("shell", None)
        for key in ["temperatureLimitMin", "temperatureLimitMax"]:
            if key in c:
                temp = finite(c[key])
                if temp is None or not 0 <= temp <= 1e5:
                    c.pop(key, None)
                    issues.append(issue(f"{key} 无效，温限检查暂不适用；计算仍继续。", cid, key))
                else:
                    c[key] = temp
        if c.get("temperatureLimitMin", 0) > c.get("temperatureLimitMax", float("inf")):
            c["temperatureLimitMin"], c["temperatureLimitMax"] = c["temperatureLimitMax"], c["temperatureLimitMin"]
            issues.append(issue("温限上下界颠倒，已互换。", cid, "temperatureLimitMin"))
        face_ids = set()
        for face in c.setdefault("faces", []):
            if not face.get("id") or face["id"] in face_ids:
                raise DomainError("表面 ID 必须存在且唯一。", "invalid_face_id")
            face_ids.add(face["id"])
            # Overrides apply to a real referenced face; omitted values inherit.
            for key in ("emissivity", "absorptivity"):
                if key in face:
                    number(face, key, c[key], (0, 1), issues, cid, units="")
        faces[cid] = face_ids

    def target(cid, fid=None):
        if cid not in ids:
            raise DomainError("热关系或边界引用了不存在的组件。", "unknown_component")
        if fid is not None and fid not in faces[cid]:
            raise DomainError("热关系或边界引用了不存在的表面。", "unknown_face")

    relation_ids = set()
    for link in p["links"]:
        link.setdefault("id", uid("l-"))
        if link["id"] in relation_ids:
            raise DomainError("热关系 ID 重复。", "duplicate_link")
        relation_ids.add(link["id"])
        target(link.get("source"), link.get("sourceFace"))
        target(link.get("target"), link.get("targetFace"))
        if link["source"] == link["target"] and link.get("sourceFace") == link.get("targetFace"):
            raise DomainError("热关系两端必须是不同对象。", "self_connection")
        kind = link.get("kind")
        if kind not in {"contact", "conductance", "radiation"}:
            raise DomainError("不支持的热关系类型。", "invalid_link_kind")
        units = {"contact": "m²·K/W", "conductance": "W/K", "radiation": "1"}
        default = {"contact": .001, "conductance": 1., "radiation": 1.}[kind]
        value = finite(link.get("value"))
        upper = 1 if kind == "radiation" else 1e12
        if value is None or not 0 <= value <= upper or (kind != "radiation" and value == 0):
            link["provenance"] = origin(original=link.get("value"), reason="热关系参数缺失或无效，已采用工程估值。")
            value = default
        link["value"], link["unit"] = value, units[kind]
        link.setdefault("provenance", origin("provided", "项目输入"))
        if link["provenance"].get("status") == "estimated":
            relationship_label = {"contact": "接触热阻", "conductance": "热导", "radiation": "辐射交换系数"}[kind]
            issues.append(issue(f"{relationship_label}采用估值 {value:g} {units[kind]}，影响传热路径强度。", link["source"], "links"))
        if kind == "radiation":
            link["viewFactorSource"] = "nx-computed"
            link["meaning"] = "configured exchange factor; not a computed view factor"
    boundary_ids = set()
    for boundary in p["boundaries"]:
        boundary.setdefault("id", uid("b-"))
        if boundary["id"] in boundary_ids:
            raise DomainError("边界 ID 重复。", "duplicate_boundary")
        boundary_ids.add(boundary["id"])
        if boundary.get("kind") not in {"space", "heatflux", "temperature"}:
            raise DomainError("不支持的边界类型。", "invalid_boundary_kind")
        if not isinstance(boundary.get("targets"), list) or not boundary["targets"]:
            raise DomainError("边界必须绑定至少一个实际组件或表面。", "missing_boundary_target")
        for t in boundary["targets"]:
            target(t.get("componentId"), t.get("faceId"))
        val = finite(boundary.get("value"))
        if val is None or val < 0 or val > 1e9:
            boundary["provenance"] = origin(original=boundary.get("value"), reason="边界参数缺失或无效，已采用工程估值。")
            val = 3 if boundary["kind"] == "space" else (293.15 if boundary["kind"] == "temperature" else 0)
            issues.append(issue("边界采用估值，请在条件核查中复核。", boundary["targets"][0]["componentId"], "boundaries"))
        boundary["value"], boundary["unit"] = val, "W/m²" if boundary["kind"] == "heatflux" else "K"
        boundary.setdefault("provenance", origin("provided", "项目输入"))
        boundary.setdefault("name", {"space": "空间辐射", "heatflux": "外热流", "temperature": "定温"}[boundary["kind"]])

    settings = p.setdefault("settings", {})
    settings["autoRun"] = bool(settings.get("autoRun", True))
    number(settings, "meshSizeMm", 10, (0, 10000), issues, units="mm", strict_min=True)
    number(settings, "spaceTemperatureK", 3, (0, 1e5), issues, units="K")
    number(settings, "initialTemperatureK", 293.15, (0, 1e5), issues, units="K", strict_min=True)
    orb = p.setdefault("orbit", {})
    for key, val in DEFAULT_ORBIT.items():
        if key != "season":
            bounds = {"semiMajorAxisKm": (6378.138, 1e7), "eccentricity": (0, .99), "inclinationDeg": (0, 180)}.get(key, (-1e9, 1e9))
            number(orb, key, val, bounds, issues, units="")
    if orb["semiMajorAxisKm"] * (1 - orb["eccentricity"]) <= 6378.137:
        orb["eccentricity"] = 0
        orb.setdefault("provenance", {})["eccentricity"] = origin(reason="原轨道近地点进入地球，暂按同半长轴圆轨道。")
        issues.append(issue("轨道近地点进入地球，暂改为圆轨道；请复核。", field="orbit"))
    if orb.get("season") not in {"spring", "summer", "autumn", "winter"}:
        orb["season"] = "summer"
    if p["geometry"]["status"] == "ready" and not p["cases"]:
        p["cases"] = [{"id": "case-default", "name": "默认稳态（外热流待补充）", "kind": "manual", "time": 0,
                       "season": orb["season"], "flux": {a: 0 for a in AXES}, "source": "工程估值：尚未导入或计算轨道外热流",
                       "valueKind": "incident-total", "opticalCoefficientsApplied": False}]
    if any(c.get("id") == "case-default" for c in p["cases"]):
        issues.append(issue("尚未设置外热流，默认工况六向为 0 W/m²；请计算轨道热流或导入工况。", field="cases"))
    case_ids = set()
    for case in p["cases"]:
        case.setdefault("id", uid("case-"))
        if case["id"] in case_ids:
            raise DomainError("工况 ID 重复。", "duplicate_case")
        case_ids.add(case["id"])
        case.setdefault("name", "稳态工况")
        case.setdefault("kind", "manual")
        case.setdefault("season", orb["season"])
        case["time"] = finite(case.get("time")) or 0
        case.setdefault("source", "手工/资料")
        flux = case.setdefault("flux", {})
        provenance = case.setdefault("fluxProvenance", flux.pop("provenance", {}))
        for axis in AXES:
            temporary = {axis: flux.get(axis), "provenance": provenance}
            flux[axis] = number(temporary, axis, 0, (0, 1e9), issues, units="W/m²")
        case.setdefault("valueKind", "incident-total")
        case.setdefault("opticalCoefficientsApplied", False)
    # Stable unique issues, no accumulation on repeated saves.
    p["issues"] = list({i["id"]: i for i in issues}.values())
    return p


def physical(project):
    return {key: copy.deepcopy(project[key]) for key in ("components", "links", "boundaries", "cases", "orbit", "settings")} | {
        "geometrySha256": project.get("geometry", {}).get("sha256")}


def standard(project):
    p = normalize(project)
    result = {k: copy.deepcopy(p[k]) for k in ("id", "name", "revision", "geometry", "components", "links", "boundaries", "cases", "orbit", "settings", "issues")}
    result.update({"schemaVersion": "1.0", "analysisType": "steady-state",
                   "units": {"length": "mm", "temperature": "K", "power": "W", "heatflux": "W/m²", "conductivity": "W/(m·K)", "contactResistance": "m²·K/W"},
                   "checks": {"parameterCompleteness": "complete" if p["components"] else "no-geometry", "estimatedCount": len([i for i in p["issues"] if i["severity"] == "warning"]),
                              "nxApplied": "not-executed", "readbackSource": None},
                   "expectedApplication": {"components": [{"componentId": c["id"], "stepIndex": c.get("stepIndex"), "kind": c["kind"], "material": c["material"], "powerW": c["power"], "faceIds": [f["id"] for f in c["faces"]]} for c in p["components"]],
                                           "linkIds": [l["id"] for l in p["links"]], "boundaryIds": [b["id"] for b in p["boundaries"]]},
                   "assumptions": ["源 STEP 几何单位归一为 mm；NX 执行器必须核对身份映射。", "首版稳态；时序数据保留并以工况共同时间取样。", "辐射关系配置值不代表求解得到的视因数。"],
                   "sourceDocuments": [{k: v for k, v in d.items() if k in ("id", "name", "sha256", "status")} for d in p["documents"]]})
    result["inputHash"] = digest(physical(p))
    return result
