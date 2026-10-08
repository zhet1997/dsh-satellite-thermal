import threading
import time

from fastapi.testclient import TestClient

from thermal_service import documents
from thermal_service.app import create_app
from thermal_service.nx_adapter import NXAdapter
from thermal_service.schema import new_project, normalize


def ready(client):
    p = new_project("equipment")
    p["geometry"]["status"] = "ready"
    p["components"] = [{"id": "c1", "name": "computer", "faces": []}]
    p = normalize(p)
    client.app.state.store.save(p)
    return p


def test_manual_runs_and_session_isolation_persist(tmp_path):
    with TestClient(create_app(tmp_path, NXAdapter(url=""), False)) as client:
        p = ready(client)
        client.put("/api/sessions/session-a/binding", json={"projectId": p["id"], "selection": {"componentId": "c1"}}).raise_for_status()
        assert client.get("/api/sessions/session-b/context").json()["bound"] is False
        p["components"][0]["power"] = 12
        changed = client.patch(f"/api/projects/{p['id']}", json={"revision": p["revision"], "components": p["components"], "settings": {"autoRun": True}}).json()
        assert changed["runs"] == []
        assert changed["settings"]["autoRun"] is False
        run = client.post(f"/api/projects/{p['id']}/runs", json={"revision": changed["revision"]}).json()
        changed["components"][0]["power"] = 24
        updated = client.patch(f"/api/projects/{p['id']}", json={"revision": changed["revision"], "components": changed["components"]}).json()
        assert len(updated["runs"]) == 1
        assert client.get(f"/api/projects/{p['id']}/runs/{run['id']}").json()["inputSnapshot"]["components"][0]["power"] == 12
    with TestClient(create_app(tmp_path, NXAdapter(url=""), False)) as client:
        context = client.get("/api/sessions/session-a/context").json()
        assert context["projectId"] == p["id"]
        assert context["revision"] == updated["revision"]
        assert context["selection"] == {"componentId": "c1"}
        assert client.get("/api/sessions/session-b/context").json()["bound"] is False
        other = client.post("/api/projects", json={"name": "other"}).json()
        client.put("/api/sessions/session-a/binding", json={"projectId": other["id"]})
        late = client.put("/api/sessions/session-a/binding", json={"projectId": p["id"], "selection": {"componentId": "c1"}, "updateSelectionOnly": True})
        assert late.status_code == 409
        assert client.get("/api/sessions/session-a/context").json()["projectId"] == other["id"]
        # Sharing is explicit, and selections remain per-session.
        client.put("/api/sessions/session-b/binding", json={"projectId": p["id"]})
        assert client.get("/api/sessions/session-b/context").json()["selection"] == {}


def test_slow_document_does_not_block_create_or_overwrite_manual_value(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = documents.extract
    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(documents, "extract", slow)
    with TestClient(create_app(tmp_path, NXAdapter(url=""), False)) as client:
        p = ready(client)
        try:
            uploaded = client.post(f"/api/projects/{p['id']}/documents?role=auxiliary",
                files={"file": ("report.txt", ("Mechanical acceptance only\n" * 100 + "computer power 40 W\n" + "Scheduling details\n" * 100).encode())}).json()
            assert entered.wait(2)
            assert uploaded["documents"][0]["status"] == "analyzing"
            assert client.post("/api/projects", json={"name": "not blocked"}).status_code == 200
            p["components"][0]["power"] = 18
            edited = client.patch(f"/api/projects/{p['id']}", json={"revision": p["revision"], "components": p["components"]})
            assert edited.status_code == 200
        finally:
            release.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            updated = client.get(f"/api/projects/{p['id']}").json()
            if updated["documents"][0]["status"] != "analyzing":
                break
            time.sleep(.01)
        doc = updated["documents"][0]
        assert doc["status"] == "needs-review"
        assert doc["screening"]["selectedLines"] == 3
        assert updated["components"][0]["power"] == 18
        assert updated["runs"] == []
        assert any("冲突" in i["message"] for i in updated["issues"])
        snippet = client.get(f"/api/projects/{p['id']}/documents/{doc['id']}/snippets?query=power").json()
        assert len(snippet["snippets"]) == 1
        assert "101 行" in snippet["snippets"][0]["location"]
        assert "40 W" in snippet["snippets"][0]["text"]


def test_pdf_page_evidence_and_no_estimated_completeness(tmp_path):
    _, snippets, info = documents.screen("[PDF 页 1]\n采购说明\n[PDF 页 2]\n工作温度 -10 至 45 ℃", "报告.pdf", "auxiliary")
    assert "PDF 页 2" in snippets[-1]["location"]
    assert info["mayMissRelevantContent"]
    with TestClient(create_app(tmp_path, NXAdapter(url=""), False)) as client:
        p = ready(client)
        data = client.get(f"/api/projects/{p['id']}/coverage").json()
        params = next(d for d in data["dimensions"] if d["name"] == "热参数")
        limits = next(d for d in data["dimensions"] if d["name"] == "工作温限")
        assert params["confirmed"] == 0
        assert params["estimated"] == params["total"]
        assert limits["missing"] == 2
