"""Prepare real geometry/input fixtures and analytic references, never results.

Run while the local service is available. All snapshots are exported from its
normal API; reference values are labelled analytic, separate from NX evidence.
"""
from __future__ import annotations

import csv
import json
import math
import os
import time
from pathlib import Path

import cadquery as cq
import httpx


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "runtime" / "nx-validation-inputs"
DATA = ROOT / "runtime" / "service-data"
AXES = ["+X", "-X", "+Y", "-Y", "+Z", "-Z"]
OUT.mkdir(parents=True, exist_ok=True)
HEADERS = {"Authorization": f"Bearer {os.environ['THERMAL_SERVICE_TOKEN']}"} if os.environ.get("THERMAL_SERVICE_TOKEN") else {}
client = httpx.Client(base_url=os.environ.get("THERMAL_VALIDATION_URL", "http://127.0.0.1:8790"), headers=HEADERS, timeout=120, trust_env=False)


def request(method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    response.raise_for_status()
    return response.json()


def save_fixture(key, p):
    snapshot = request("GET", f"/api/projects/{p['id']}/standard")
    # These fixture runs are explicitly submitted to the worker by the verifier;
    # they do not drain unrelated user queues when a solver is connected.
    snapshot["run"] = {"id": f"validation-{key}-{p['revision']}", "revision": p["revision"], "caseId": None}
    folder = OUT / key
    folder.mkdir(exist_ok=True)
    snap_path = folder / "snapshot.json"
    snap_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    response = client.get(f"/api/projects/{p['id']}/artifacts/{p['geometry']['artifactId']}")
    response.raise_for_status()
    step_path = folder / "geometry.step"
    step_path.write_bytes(response.content)
    return {"key": key, "projectId": p["id"], "revision": p["revision"], "snapshot": str(snap_path), "step": str(step_path),
            "componentCount": len(p["components"]), "faceCount": sum(len(c["faces"]) for c in p["components"])}


def project_for(key, shapes):
    folder = OUT / key
    folder.mkdir(exist_ok=True)
    step = folder / "source.step"
    cq.exporters.export(cq.Compound.makeCompound(shapes), str(step))
    p = request("POST", "/api/projects", json={"name": f"NX 验证输入 · {key}（尚未验收）"})
    p = request("PATCH", f"/api/projects/{p['id']}", json={"revision": p["revision"], "settings": {"autoRun": False}})
    with step.open("rb") as fp:
        p = request("POST", f"/api/projects/{p['id']}/geometry", files={"file": (key + ".step", fp, "application/step")})
    for _ in range(120):
        p = request("GET", f"/api/projects/{p['id']}")
        if p["geometry"]["status"] != "importing":
            break
        time.sleep(.25)
    if p["geometry"]["status"] != "ready":
        raise RuntimeError(p["geometry"])
    return p


def apply(p, **changes):
    return request("PATCH", f"/api/projects/{p['id']}", json={"revision": p["revision"], **changes})


def fixed(component, x, temperature, name):
    face = min(component["faces"], key=lambda f: abs(f["center"][0] - x))
    return {"id": name, "name": name, "kind": "temperature", "targets": [{"componentId": component["id"], "faceId": face["id"]}],
            "value": temperature, "unit": "K", "provenance": {"status": "provided", "source": "解析基准边界"}}


def base_components(p, conductivity=100, power=0):
    for component in p["components"]:
        component.update({"material": "benchmark-isotropic", "conductivity": conductivity, "density": 2700,
                          "specificHeat": 900, "power": power, "emissivity": .8, "absorptivity": .2})
    return p["components"]


def zero_cases():
    return [{"id": "case-benchmark", "name": "解析基准：无轨道热流", "kind": "manual", "time": 0, "season": "summer",
             "flux": {axis: 0 for axis in AXES}, "source": "解析基准明确零外热流", "valueKind": "absorbed", "opticalCoefficientsApplied": True}]


fixtures, expected = [], []

# Source-backed demo useful for verifying topology/mesh mapping, not an analytic
# reference or actual computed thermal result.
example = request("POST", "/api/projects/example")
for run in example["runs"]:
    request("POST", f"/api/projects/{example['id']}/runs/{run['id']}/cancel")
example = apply(example, settings={"autoRun": False})
fixtures.append(save_fixture("example", example))

# Real mixed dimensions: one solid and one independent sheet, thickness 2 mm.
solid = cq.Workplane("XY").box(40, 40, 20).translate((-45, 0, 10)).val()
sheet = cq.Face.makeFromWires(cq.Workplane("XY").rect(70, 60).val()).translate((45, 0, 0))
p = project_for("solid-sheet", [solid, sheet])
components = base_components(p, conductivity=167, power=2)
for c in components:
    if c["kind"] == "shell":
        c["thickness"] = 2
p = apply(p, components=components, cases=zero_cases(), settings={"meshSizeMm": 5, "initialTemperatureK": 293.15})
fixtures.append(save_fixture("solid-sheet", p))

# Fourier bar: 0.1 m long, 1e-4 m² section, k=100 W/(m K).
p = project_for("conduction", [cq.Workplane("XY").box(100, 10, 10).val()])
components = base_components(p)
p = apply(p, components=components, links=[], boundaries=[fixed(components[0], -50, 300, "cold-end"), fixed(components[0], 50, 320, "hot-end")],
          cases=zero_cases(), settings={"meshSizeMm": 2, "initialTemperatureK": 310})
fixtures.append(save_fixture("conduction", p))
expected.extend([{"case": "conduction", "quantity": "steady_heat_flow_magnitude_W", "reference": 2., "absoluteTolerance": .02, "basis": "k*A*(Th-Tc)/L; other surfaces adiabatic"},
                 {"case": "conduction", "quantity": "mean_temperature_K", "reference": 310., "absoluteTolerance": .2, "basis": "linear field T(x)=300+20*(x+0.05)/0.1, x in m"}])

# Two half-bars with area-normalized contact resistance 0.0005 m² K/W.
p = project_for("contact", [cq.Workplane("XY").box(50, 10, 10).translate((-25, 0, 0)).val(), cq.Workplane("XY").box(50, 10, 10).translate((25, 0, 0)).val()])
components = base_components(p)
left, right = sorted(components, key=lambda c: c["center"][0])
for link in p["links"]:
    link.update({"value": .0005, "provenance": {"status": "provided", "source": "解析接触热阻基准"}})
p = apply(p, components=components, links=p["links"], boundaries=[fixed(left, -50, 300, "cold-end"), fixed(right, 50, 320, "hot-end")],
          cases=zero_cases(), settings={"meshSizeMm": 2, "initialTemperatureK": 310})
fixtures.append(save_fixture("contact", p))
expected.extend([{"case": "contact", "quantity": "steady_heat_flow_magnitude_W", "reference": 20/15, "absoluteTolerance": .03, "basis": "20/[0.1/(100*1e-4)+0.0005/1e-4]"},
                 {"case": "contact", "quantity": "left_contact_temperature_K", "reference": 300+20/15*5, "absoluteTolerance": .3, "basis": "cold end plus Q*left bulk resistance"},
                 {"case": "contact", "quantity": "right_contact_temperature_K", "reference": 320-20/15*5, "absoluteTolerance": .3, "basis": "hot end minus Q*right bulk resistance"}])

# Radiating isothermal-limit cube, with high finite conductivity. This reference
# intentionally distinguishes its assumption from finite-element temperatures.
p = project_for("radiation", [cq.Workplane("XY").box(100, 100, 100).val()])
components = base_components(p, conductivity=1000, power=10)
p = apply(p, components=components, links=[], cases=zero_cases(), settings={"meshSizeMm": 10, "initialTemperatureK": 250})
fixtures.append(save_fixture("radiation", p))
temperature = (10/(5.670374419e-8*.8*.06)+3**4)**.25
expected.extend([{"case": "radiation", "quantity": "isothermal_limit_temperature_K", "reference": temperature, "absoluteTolerance": .5,
                  "basis": "[Q/(sigma*epsilon*A)+Tspace^4]^0.25; A=0.06 m², Q=10 W, epsilon=0.8, Tspace=3 K; finite k=1000 requires small gradient"},
                 {"case": "radiation", "quantity": "net_radiated_power_W", "reference": 10., "absoluteTolerance": .2, "basis": "steady energy balance with zero external incident flux"}])

with (OUT / "analytic-expected.csv").open("w", newline="", encoding="utf-8-sig") as fp:
    writer = csv.DictWriter(fp, fieldnames=["case", "quantity", "reference", "absoluteTolerance", "basis"])
    writer.writeheader()
    writer.writerows(expected)
record = {"kind": "validation-inputs-and-analytic-references-only", "nxExecuted": False, "fixtures": fixtures,
          "analyticExpected": str(OUT / "analytic-expected.csv"), "warning": "These are inputs/reference values, not NX results; actual solver evidence must be recorded separately."}
(ROOT / "runtime" / "nx-test-inputs.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(record, ensure_ascii=False, indent=2))
