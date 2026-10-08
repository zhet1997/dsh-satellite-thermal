"""Real service → NX worker validation. No simulated solver responses.

Run `prepare` first and wait for the executor owner to permit execution, then
`submit`, followed by `inspect` until terminal. This never writes credentials.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
RECORD = ROOT / "validation/integration-result.json"
SOURCE = ROOT / "runtime/nx-validation-inputs/conduction"
BASE = os.environ.get("THERMAL_VALIDATION_URL", "http://127.0.0.1:8790")
headers = {"Authorization": "Bearer " + os.environ["THERMAL_SERVICE_TOKEN"]} if os.environ.get("THERMAL_SERVICE_TOKEN") else {}
client = httpx.Client(base_url=BASE, headers=headers, timeout=120, trust_env=False)


def now():
    return datetime.now(timezone.utc).isoformat()


def request(method, url, **kwargs):
    response = client.request(method, url, **kwargs)
    response.raise_for_status()
    return response.json()


def save(record):
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    RECORD.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: record.get(key) for key in ("status", "projectId", "runId", "message", "checks")}, ensure_ascii=False))


def prepare():
    if RECORD.exists():
        previous = json.loads(RECORD.read_text("utf-8"))
        if previous.get("projectId"):
            response = client.get(f"/api/projects/{previous['projectId']}")
            if response.status_code == 200:
                print("Existing prepared project retained: " + previous["projectId"])
                return
    snapshot = json.loads((SOURCE / "snapshot.json").read_text("utf-8"))
    project = request("POST", "/api/projects", json={"name": "真实 NX 集成验收 · 导热基准"})
    pid = project["id"]
    project = request("PATCH", f"/api/projects/{pid}", json={"revision": project["revision"], "settings": {"autoRun": False}})
    with (SOURCE / "geometry.step").open("rb") as fp:
        project = request("POST", f"/api/projects/{pid}/geometry", files={"file": ("conduction.step", fp, "application/step")})
    for _ in range(120):
        project = request("GET", f"/api/projects/{pid}")
        if project["geometry"]["status"] != "importing":
            break
        time.sleep(.25)
    assert project["geometry"]["status"] == "ready", project["geometry"]
    assert {c["id"] for c in project["components"]} == {c["id"] for c in snapshot["components"]}
    for old, new in zip(snapshot["components"], project["components"]):
        assert [f["id"] for f in old["faces"]] == [f["id"] for f in new["faces"]]
        assert all(len(f["boundsMm"]) == 6 for f in new["faces"])
    patch = {key: snapshot[key] for key in ("components", "links", "boundaries", "cases", "orbit", "settings")}
    patch["settings"]["autoRun"] = False
    patch["revision"] = project["revision"]
    project = request("PATCH", f"/api/projects/{pid}", json=patch)
    assert project["runs"] == []
    assert project["settings"]["autoRun"] is False
    standard = request("GET", f"/api/projects/{pid}/standard")
    record = {"schemaVersion": "1.0", "preparedAt": now(), "status": "prepared", "projectId": pid,
              "revision": project["revision"], "caseId": project["cases"][0]["id"],
              "inputHash": standard["inputHash"], "geometrySha256": project["geometry"]["sha256"],
              "message": "输入和实际组件/面引用已核对，未提交 NX 运行。",
              "expected": {"minK": 300., "maxK": 320., "meanK": 310., "meanToleranceK": .2, "heatFlowW": 2., "heatFlowToleranceW": .02},
              "checks": {"componentIdsMatch": True, "faceIdsMatch": True, "boundsPresent": True, "autoRunDisabled": True, "noPrematureRuns": True}}
    save(record)


def submit():
    record = json.loads(RECORD.read_text("utf-8"))
    if record.get("runId"):
        print("Existing run retained: " + record["runId"])
        return
    pid = record["projectId"]
    project = request("GET", f"/api/projects/{pid}")
    run = request("POST", f"/api/projects/{pid}/runs", json={"revision": project["revision"], "caseId": record["caseId"]})
    record.update({"runId": run["id"], "submittedAt": now(), "status": run["status"], "message": run["message"]})
    save(record)


def retry():
    record = json.loads(RECORD.read_text("utf-8"))
    assert record["status"] in {"failed", "cancelled"}
    record.setdefault("priorAttempts", []).append({k: record.get(k) for k in ("runId", "status", "message", "events", "submittedAt", "lastCheckedAt")})
    record.pop("runId", None)
    record.pop("events", None)
    save(record)
    submit()


def rerun():
    """Submit a new immutable run after a terminal verification attempt.

    Keep the previous complete machine-readable evidence before refreshing the
    current record; never rewrite a historical project run or its input.
    """
    record = json.loads(RECORD.read_text("utf-8"))
    previous = request("GET", f"/api/projects/{record['projectId']}/runs/{record['runId']}")
    assert previous["status"] in {"completed", "failed", "cancelled"}
    history = ROOT / "validation/integration-history"
    history.mkdir(parents=True, exist_ok=True)
    (history / (record["runId"] + ".json")).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    record.setdefault("priorAttempts", []).append({k: record.get(k) for k in ("runId", "status", "message", "events", "submittedAt", "lastCheckedAt", "completedAt")})
    for key in ("runId", "events", "actual", "completedAt", "stage"):
        record.pop(key, None)
    record["checks"] = {key: value for key, value in record["checks"].items() if key in {"componentIdsMatch", "faceIdsMatch", "boundsPresent", "autoRunDisabled", "noPrematureRuns"}}
    record["verificationVersion"] = "2-native-target-set-membership"
    save(record)
    submit()


def verify_native_target_sets(condition, boundary):
    """Assert actual NX group members, not saved input labels."""
    def identities(targets):
        return {(t["componentId"], t.get("faceId")) for t in targets}
    expected_input = identities(boundary["targets"])
    assert identities(condition["expectedTargets"]) == expected_input
    assert condition["source"] == "NX reopened SIM"
    target_sets = condition["targetSets"]
    expected_sets = condition["expectedTargetSets"]
    assert isinstance(target_sets, list) and target_sets
    actual_by_index = {s["index"]: s for s in target_sets}
    assert len(actual_by_index) == len(target_sets)
    observed = set()
    for expected in expected_sets:
        actual = actual_by_index[expected["index"]]
        assert actual["inputComparison"] == "matched"
        assert actual["entityCount"] == expected["entityCount"]
        assert actual["group"] == expected["group"]
        if actual["entityCount"]:
            assert actual["mode"] == "faces"
            assert actual["source"] == "NX target group actual entity identities"
            assert re.fullmatch(r"[0-9a-f]{64}", actual["memberIdentitySha256"])
            assert actual["memberIdentitySha256"] == expected["memberIdentitySha256"]
            assert identities(actual["targets"]) == identities(expected["targets"])
        observed.update(identities(actual["targets"]))
    assert observed == expected_input
    assert identities(condition["targets"]) == observed


def inspect():
    record = json.loads(RECORD.read_text("utf-8"))
    pid, rid = record["projectId"], record["runId"]
    run = request("GET", f"/api/projects/{pid}/runs/{rid}")
    record.update({"lastCheckedAt": now(), "status": run["status"], "stage": run["stage"], "message": run["message"]})
    record["events"] = run.get("events", [])
    if run["status"] == "completed":
        result = run["result"]
        assert result["source"] == "nx"
        stats = result["components"]
        assert len(stats) == 1
        stat = stats[0]
        assert abs(stat["minK"] - 300) < .01
        assert abs(stat["maxK"] - 320) < .01
        assert abs(stat["meanK"] - 310) < .2
        assert run.get("readback")
        assert run["inputSnapshot"]["inputHash"] == record["inputHash"]
        readback = run["readback"]
        assert readback["source"] == "nx"
        assert readback["mesh"]["nodeCount"] > 0 and readback["mesh"]["elementCount"] > 0
        assert readback["components"][0]["conductivity"] == 100
        assert all(face["mappingErrorMm"] < 1e-6 for face in readback["faces"])
        actual_conditions = {c["inputId"]: c for c in readback["conditions"]}
        for boundary in run["inputSnapshot"]["boundaries"]:
            condition = actual_conditions[boundary["id"]]
            verify_native_target_sets(condition, boundary)
            temperature = next(p for p in condition["properties"] if p["name"] == "Temperature")
            actual_value, actual_unit = temperature["GetBaseScalarWithDataPropertyValue"]
            assert actual_unit == "Kelvin" and abs(actual_value - boundary["value"]) < 1e-8
        balance = result["energyBalance"]
        assert balance["status"] == "available"
        assert abs(balance["heatFlowIntoSinksW"] - record["expected"]["heatFlowW"]) <= record["expected"]["heatFlowToleranceW"]
        assert balance["relativeResidual"] <= .01
        directory = ROOT / "validation/artifacts/integration" / rid
        directory.mkdir(parents=True, exist_ok=True)
        artifacts = []
        for artifact in run.get("artifacts", []):
            response = client.get(artifact["url"])
            response.raise_for_status()
            content = response.content
            assert len(content) == artifact["size"]
            sha = hashlib.sha256(content).hexdigest()
            assert sha == artifact["sha256"]
            name = artifact["name"]
            assert Path(name).name == name and "\\" not in name
            (directory / name).write_bytes(content)
            artifacts.append({"name": name, "size": len(content), "sha256": sha, "mediaType": artifact.get("mediaType")})
        export = client.get(f"/api/projects/{pid}/export")
        export.raise_for_status()
        (directory / "project-export.zip").write_bytes(export.content)
        with zipfile.ZipFile(io.BytesIO(export.content)) as package:
            manifest = json.loads(package.read("manifest.json"))
            assert manifest["nxExecution"] == "completed"
            assert all("\\" not in name and ".." not in Path(name).parts for name in package.namelist())
        record["checks"].update({"realNXSource": True, "temperatureReferencePassed": True, "readbackPresent": True,
                                 "immutableInputHashMatched": True, "artifactDownloadsVerified": True, "exportManifestCompleted": True,
                                 "safeExportEntries": True, "actualMaterialReadbackMatched": True, "actualBoundaryTargetsAndValuesMatched": True,
                                 "nativeTargetSetMembershipMatched": True, "nativeTargetSetHashesMatched": True,
                                 "actualMeshPresent": True, "faceMappingExact": True, "heatFlowReferencePassed": True, "energyBalancePassed": True})
        record["actual"] = {"components": stats, "energyBalance": result.get("energyBalance"), "readback": run["readback"], "artifacts": artifacts,
                             "export": {"bytes": len(export.content), "sha256": hashlib.sha256(export.content).hexdigest(), "manifest": manifest}}
        record["completedAt"] = now()
        record["message"] = "真实 service→worker→NX→回读/结果/工件/ZIP 链路通过；导热温度、2 W 热流及能量平衡对照通过。"
    save(record)


{"prepare": prepare, "submit": submit, "retry": retry, "rerun": rerun, "inspect": inspect}[sys.argv[1]]()
