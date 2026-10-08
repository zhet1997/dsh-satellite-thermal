from __future__ import annotations

import hashlib
import importlib.util
import math
from datetime import datetime, timezone
from pathlib import Path

from .schema import AXES
from .storage import DomainError

SOURCE = Path(__file__).resolve().parents[1] / "vendor" / "compute_heat_flux.py"


def calculate(project, directory, selected_time=None, season=None):
    # Reuse the exact working-tree source instead of reimplementing orbital
    # arithmetic. Only unabsorbed direct/albedo/IR columns enter the contract.
    import sys
    spec = importlib.util.spec_from_file_location("thermal_original_heatflux", SOURCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    orbit = project["orbit"]
    season = season or orbit["season"]
    matching = [s for s in module.SEASONS if s[0] == season]
    if not matching:
        raise DomainError("季节应为 spring/summer/autumn/winter。", "invalid_season")
    key, label, month, day = matching[0]
    case = module.Case("lvlh", key, label, datetime(2026, month, day, tzinfo=timezone.utc),
                       orbit["semiMajorAxisKm"] * 1000, orbit["eccentricity"], orbit["inclinationDeg"],
                       orbit["raanDeg"], orbit["argumentOfPerigeeDeg"], orbit["trueAnomalyDeg"])
    directory.mkdir(parents=True, exist_ok=True)
    frame = module.run_case(case, directory, sample_step_s=30)
    profile = []
    for _, row in frame.iterrows():
        profile.append({"time": float(row["time_s"]), "faces": {a: float(row[a]) for a in AXES},
                        "components": {a: {"direct": float(row[f"{a}_direct"]), "albedo": float(row[f"{a}_albedo"]), "ir": float(row[f"{a}_ir"])} for a in AXES},
                        "inEclipse": bool(row["in_eclipse"])})
    # Equal projected reference area on all six directions is reproducible and
    # explicit. It recommends environment extrema, never temperature extrema.
    metric = [sum(s["faces"].values()) for s in profile]
    indexes = [("cold", "推荐冷态", min(range(len(metric)), key=metric.__getitem__)),
               ("hot", "推荐热态", max(range(len(metric)), key=metric.__getitem__))]
    if selected_time is not None:
        try:
            t = float(selected_time)
        except (ValueError, TypeError) as exc:
            raise DomainError("选定时间必须是有限秒数。", "invalid_time") from exc
        if not math.isfinite(t) or t < 0 or t > profile[-1]["time"]:
            raise DomainError("选定时间超出本轨道的计算区间。", "invalid_time")
        # Compute exact requested timestamp by linearly interpolating every
        # directional component with one shared fraction, preserving consistency.
        import numpy as np
        faces, components = {}, {}
        times = [p["time"] for p in profile]
        for axis in AXES:
            components[axis] = {part: float(np.interp(t, times, [p["components"][axis][part] for p in profile])) for part in ("direct", "albedo", "ir")}
            faces[axis] = sum(components[axis].values())
        profile.append({"time": t, "faces": faces, "components": components, "interpolated": True})
        metric.append(sum(faces.values()))
        indexes.append(("manual", "指定时刻", len(profile) - 1))
    sha = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    cases = []
    for kind, name, idx in indexes:
        sample = profile[idx]
        cases.append({"id": f"case-{key}-{kind}", "name": f"{label} · {name}", "kind": kind, "time": sample["time"], "season": key,
                      "flux": sample["faces"], "fluxComponents": sample["components"], "valueKind": "incident-components", "opticalCoefficientsApplied": False,
                      "source": "原 Agent Web 六向轨道热流计算", "recommendation": {"metric": "六向入射热流等权和", "metricValue": metric[idx], "unit": "W/m²",
                          "meaning": "环境载荷推荐，不代表求解后的最高/最低温度", "allDirectionsUseSameTime": True},
                      "calculation": {"sourceSha256": sha, "model": "Keplerian orbit, approximate solar ephemeris, cylindrical eclipse, LVLH",
                                      "coordinateConvention": "+Z 地心/-Z 天顶/+X 沿轨/-X 反沿轨/+Y 轨道法向", "year": 2026, "samplingSeconds": 30}})
    project["cases"] = cases
    project["heatfluxProfile"] = sorted(profile, key=lambda p: p["time"])
    project["heatfluxMethod"] = {"valueKind": "incident-components", "solarAbsorptivityApplied": False, "infraredEmissivityApplied": False,
                                "sourceSha256": sha, "recommendationMetric": "six-direction incident-flux sum", "sampleSeconds": 30}
    return directory / f"lvlh_{key}_timeseries.csv"
