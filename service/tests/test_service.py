import io
import json
import math
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from thermal_service.app import create_app
from thermal_service.geometry import available, safe_zip
from thermal_service.nx_adapter import NXAdapter
from thermal_service.runs import find_run
from thermal_service.schema import new_project, normalize, standard
from thermal_service.storage import DomainError


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, adapter=NXAdapter(url=""), start_scheduler=False)
    with TestClient(app) as client:
        yield client


def wait_documents(client, pid):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        p = client.get(f"/api/projects/{pid}").json()
        if all(d["status"] != "analyzing" for d in p["documents"]):
            return p
        time.sleep(.01)
    pytest.fail("document analysis did not finish")


def test_nonblocking_estimates_are_finite_complete_and_traceable():
    p = new_project("malformed")
    p["components"] = [{"id": "c1", "kind": "shell", "power": "invalid", "conductivity": -3,
                         "emissivity": 4, "density": None, "specificHeat": "nan", "faces": []}]
    p["geometry"]["status"] = "ready"
    p = normalize(p)
    c = p["components"][0]
    for key in ("power", "conductivity", "density", "specificHeat", "emissivity", "absorptivity", "thickness"):
        assert math.isfinite(c[key])
        assert c["provenance"][key]["status"] == "estimated"
    assert c["provenance"]["material"]["status"] == "estimated"
    assert standard(p)["checks"]["parameterCompleteness"] == "complete"
    assert normalize(p) == p


def test_revision_conflict_and_no_run_on_rename(client):
    p = client.post("/api/projects", json={"name": "alpha"}).json()
    changed = client.patch(f"/api/projects/{p['id']}", json={"revision": p["revision"], "name": "beta"})
    assert changed.status_code == 200
    assert changed.json()["runs"] == []
    stale = client.patch(f"/api/projects/{p['id']}", json={"revision": p["revision"], "name": "oops"})
    assert stale.status_code == 409
    assert stale.json()["code"] == "revision_conflict"


@pytest.mark.skipif(not available(), reason="CadQuery optional")
def test_mcp_component_evidence_survives_unchanged_estimate_and_changed_power(client):
    p = client.post("/api/projects/example").json()
    evidence = {"status": "estimated", "source": "approved equipment budget", "reason": "electric input conservatively becomes heat"}
    components = p["components"]
    components[1]["power"] = 22
    components[1]["provenance"]["power"] = evidence
    components[1]["provenance"]["conductivity"] = {"status": "estimated", "source": "approved material approximation", "reason": "not a measured alloy"}
    changed = client.patch(f"/api/projects/{p['id']}", json={"revision": p["revision"], "components": components})
    assert changed.status_code == 200, changed.text
    c = changed.json()["components"][1]
    assert c["power"] == 22
    assert c["provenance"]["power"] == evidence
    assert c["provenance"]["conductivity"]["source"] == "approved material approximation"
    assert c["faces"] == components[1]["faces"]


@pytest.mark.skipif(not available(), reason="CadQuery optional in lightweight test environments")
def test_real_example_preview_queue_snapshots_and_cancel(client):
    p = client.post("/api/projects/example").json()
    assert p["geometry"]["status"] == "ready", p
    assert len(p["components"]) == 3
    assert len(p["links"]) >= 2
    glb = client.get(p["geometry"]["previewUrl"])
    assert glb.content[:4] == b"glTF"
    assert p["runs"] == []
    first = client.post(f"/api/projects/{p['id']}/runs", json={"revision": p["revision"]}).json()
    assert first["status"] == "queued"
    assert "result" not in first
    client.app.state.scheduler.tick()
    r = client.get(f"/api/projects/{p['id']}/runs/{first['id']}").json()
    assert r["status"] == "waiting_connection"
    assert r["inputSnapshot"]["components"][1]["power"] == 18
    components = p["components"]
    components[1]["power"] = 22
    updated = client.patch(f"/api/projects/{p['id']}", json={"revision": p["revision"], "components": components}).json()
    assert len(updated["runs"]) == 1
    client.post(f"/api/projects/{p['id']}/runs", json={"revision": updated["revision"]})
    updated = client.get(f"/api/projects/{p['id']}").json()
    assert len(updated["runs"]) == 2
    old = client.get(f"/api/projects/{p['id']}/runs/{first['id']}").json()
    assert old["inputSnapshot"]["components"][1]["power"] == 18
    duplicate = client.post(f"/api/projects/{p['id']}/runs", json={"revision": updated["revision"]}).json()
    assert duplicate["id"] == updated["runs"][-1]["id"]
    cancelled = client.post(f"/api/projects/{p['id']}/runs/{first['id']}/cancel").json()
    assert cancelled["status"] == "cancelled"
    exported = client.get(f"/api/projects/{p['id']}/export")
    with zipfile.ZipFile(io.BytesIO(exported.content)) as z:
        assert json.loads(z.read("manifest.json"))["nxExecution"] == "not-completed"


@pytest.mark.parametrize("name", ["../outside.step", "/outside.step", "C:/outside.step", "x\\..\\outside.step"])
def test_malicious_zip_rejected_before_writing(tmp_path, name):
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(name, "not step")
    with pytest.raises(DomainError):
        safe_zip(path, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_unknown_target_is_not_silently_reassigned():
    p = new_project("test")
    p["components"] = [{"id": "a", "faces": []}, {"id": "b", "faces": []}]
    p["links"] = [{"source": "a", "target": "missing", "kind": "contact", "value": .001}]
    with pytest.raises(DomainError, match="不存在"):
        normalize(p)


@pytest.mark.skipif(not available(), reason="CadQuery optional")
def test_document_identity_and_units_do_not_spread_unrelated_numbers(client):
    p = client.post("/api/projects/example").json()
    pid = p["id"]
    csv = "组件名称,功耗(kW),温限上限(℃)\n演示载荷,0.021,65\n不存在的组件,999,12\n"
    p = client.post(f"/api/projects/{pid}/documents", files={"file": ("thermal.csv", csv.encode(), "text/csv")}).json()
    p = wait_documents(client, pid)
    c = p["components"][1]
    assert c["power"] == 21
    assert c["temperatureLimitMax"] == 338.15
    assert p["components"][2]["power"] == 8
    assert p["documents"][-1]["unmatchedComponents"] == ["不存在的组件"]
    text = client.post(f"/api/projects/{pid}/text", json={"text": "卫星总质量 200 kg。一个无关设备功耗 100 W。"}).json()
    text = wait_documents(client, pid)
    assert [c["power"] for c in text["components"]] == [0, 21, 8]
    assert text["documents"][-1]["status"] == "needs-review"


def test_heatflux_shared_time_and_no_optical_double_count(client):
    p = client.post("/api/projects", json={"name": "orbit"}).json()
    result = client.post(f"/api/projects/{p['id']}/heatflux", json={"time": 123.4}).json()
    assert len(result["cases"]) == 3
    for case in result["cases"]:
        assert not case["opticalCoefficientsApplied"]
        assert case["recommendation"]["allDirectionsUseSameTime"]
        for axis, value in case["flux"].items():
            if axis != "provenance":
                assert value == pytest.approx(sum(case["fluxComponents"][axis].values()))
    assert result["cases"][-1]["time"] == 123.4


def test_auth_and_path_are_checked(client, monkeypatch):
    monkeypatch.setenv("THERMAL_SERVICE_TOKEN", "test-token")
    assert client.get("/api/health").status_code == 401
    assert client.get("/api/health", headers={"Authorization": "Bearer test-token"}).status_code == 200
    assert client.get("/api/projects/%2e%2e%5csecret", headers={"Authorization": "Bearer test-token"}).status_code == 400


def test_active_cancel_requires_executor_ack(client):
    p = client.post("/api/projects", json={"name": "cancel"}).json()
    store = client.app.state.store
    p["runs"] = [{"id": "run-test", "status": "solving", "stage": "solving", "events": [], "message": "", "_jobId": "nx-1"}]
    store.save(p)
    cancelled = client.post(f"/api/projects/{p['id']}/runs/run-test/cancel").json()
    assert cancelled["status"] == "cancel_requested"
    assert "_jobId" not in cancelled


@pytest.mark.skipif(not available(), reason="CadQuery optional")
def test_lost_submit_response_is_reconciled_before_cancel(client):
    import httpx
    p = client.post("/api/projects/example").json()
    client.post(f"/api/projects/{p['id']}/runs", json={"revision": p["revision"]})
    p = client.get(f"/api/projects/{p['id']}").json()
    scheduler = client.app.state.scheduler
    adapter = scheduler.adapter
    accepted, cancelled = [], []
    adapter.health_cache = {"configured": True, "connected": True, "message": "ready"}
    adapter.health = lambda: adapter.health_cache
    def submit(snapshot, path):
        accepted.append(snapshot["run"]["id"])
        if len(accepted) == 1:
            raise httpx.ReadTimeout("Worker accepted before response was lost")
        return {"id": "remote-same-job", "status": "solving"}
    def cancel(job):
        cancelled.append(job)
        return {"id": job, "status": "cancelled", "message": "Confirmed stopped"}
    adapter.submit, adapter.cancel = submit, cancel
    scheduler.tick()
    rid = p["runs"][0]["id"]
    state = client.post(f"/api/projects/{p['id']}/runs/{rid}/cancel").json()
    assert state["status"] == "cancel_requested"
    adapter.health_cache["connected"] = True
    scheduler.tick()
    final = client.get(f"/api/projects/{p['id']}/runs/{rid}").json()
    assert accepted == [rid, rid]
    assert cancelled == ["remote-same-job"]
    assert final["status"] == "cancelled"


@pytest.mark.skipif(not available(), reason="CadQuery optional")
def test_material_change_refreshes_only_estimated_properties(client):
    p = client.post("/api/projects/example").json()
    p["components"][0]["material"] = "Copper"
    p["components"][0]["specificHeat"] = 400
    p = client.patch(f"/api/projects/{p['id']}", json={"revision": p["revision"], "components": p["components"]}).json()
    c = p["components"][0]
    assert c["conductivity"] == 385
    assert c["density"] == 8960
    assert c["specificHeat"] == 400
    assert c["provenance"]["conductivity"]["status"] == "estimated"
    assert c["provenance"]["specificHeat"]["status"] == "manual"


def test_empty_nx_completion_cannot_pass_as_real_result(client):
    store = client.app.state.store
    p = normalize(new_project("evidence"))
    p["components"] = [{"id": "a", "faces": []}]
    p["geometry"]["status"] = "ready"
    p = normalize(p)
    from thermal_service.runs import queue_run
    run = queue_run(store, p)
    store.save(p)
    client.app.state.scheduler.apply_remote(p["id"], run["id"], "worker-1", {"status": "completed", "readback": {"source": "nx"}, "result": {"source": "nx", "components": []}})
    updated = find_run(store.get(p["id"]), run["id"])
    assert updated["status"] == "failed"


@pytest.mark.skipif(not available(), reason="CadQuery optional")
def test_cold_and_hot_are_separate_queued_runs(client):
    p = client.post("/api/projects/example").json()
    original = client.post(f"/api/projects/{p['id']}/runs", json={"revision": p["revision"]}).json()["id"]
    p = client.post(f"/api/projects/{p['id']}/heatflux", json={}).json()
    assert len(p["runs"]) == 1
    assert p["runs"][0]["id"] == original
    manual = client.post(f"/api/projects/{p['id']}/runs", json={"revision": p["revision"]}).json()
    assert len(manual["queuedRunIds"]) == 2
    updated = client.get(f"/api/projects/{p['id']}").json()
    assert {run["caseId"] for run in updated["runs"][1:]} == {case["id"] for case in p["cases"]}
    assert len(client.get(f"/api/projects/{p['id']}").json()["runs"]) == 3


def test_export_sanitizes_windows_style_source_filename(client):
    p = client.post("/api/projects", json={"name": "safe export"}).json()
    imported = client.post(f"/api/projects/{p['id']}/documents", files={"file": ("..\\..\\escaped.txt", b"source note", "text/plain")})
    assert imported.status_code == 200
    export = client.get(f"/api/projects/{p['id']}/export")
    with zipfile.ZipFile(io.BytesIO(export.content)) as z:
        assert all("\\" not in name and ".." not in Path(name).parts for name in z.namelist())
        assert any(name.endswith("/escaped.txt") for name in z.namelist())


def test_live_native_artifacts_are_not_downloaded_until_terminal(client):
    import hashlib
    from thermal_service.runs import queue_run
    store = client.app.state.store
    p = new_project("live artifacts")
    p["components"] = [{"id": "a", "faces": []}]
    p["geometry"]["status"] = "ready"
    p = normalize(p)
    run = queue_run(store, p)
    store.save(p)
    scheduler = client.app.state.scheduler
    downloaded = []
    def download(job, name, path):
        downloaded.append(name)
        path.write_bytes(b"actual-preview")
    scheduler.adapter.download = download
    native = {"name": "case.fem", "sha256": hashlib.sha256(b"still-writing").hexdigest()}
    scheduler.apply_remote(p["id"], run["id"], "worker", {"status": "modeling", "artifacts": [native]})
    assert downloaded == []
    assert find_run(store.get(p["id"]), run["id"])["status"] == "modeling"
    mesh = {"name": "mesh.glb", "sha256": hashlib.sha256(b"actual-preview").hexdigest()}
    scheduler.apply_remote(p["id"], run["id"], "worker", {"status": "solving", "artifacts": [native, mesh], "readback": {"source": "nx", "meshArtifact": "mesh.glb"}})
    assert downloaded == ["mesh.glb"]
    updated = find_run(store.get(p["id"]), run["id"])
    assert updated["status"] == "solving"
    assert updated["readback"]["meshUrl"].startswith("/api/projects/")
    # A preview publication changing during download is retried, not reported
    # as a failed NX solve; a previous verified artifact remains immutable.
    mesh["sha256"] = hashlib.sha256(b"newly-written-preview").hexdigest()
    scheduler.apply_remote(p["id"], run["id"], "worker", {"status": "solving", "artifacts": [mesh], "readback": {"source": "nx", "meshArtifact": "mesh.glb"}})
    assert find_run(store.get(p["id"]), run["id"])["status"] == "solving"
