"""Regression for native CAD calls starving every HTTP thread of the GIL."""
import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from thermal_service.app import create_app
from thermal_service.geometry_process import GeometryProcesses
from thermal_service.nx_adapter import NXAdapter


@pytest.fixture
def native_worker(tmp_path, monkeypatch):
    # PyDLL deliberately retains the GIL across this native call, unlike CDLL
    # or time.sleep. It reproduces the observed OpenCascade failure mode without
    # depending on a particular STEP file or a permanently stuck CAD operation.
    script = tmp_path / "native_worker.py"
    script.write_text('''
import ctypes
import hashlib
import json
import os
import sys
from pathlib import Path
job_path, output = map(Path, sys.argv[1:])
job = json.loads(job_path.read_text(encoding="utf-8"))
folder = Path(job["directory"])
source = Path(job["upload"]) if job["upload"] else None
if source and source.read_bytes() == b"HOLD_GIL":
    (folder / "native-started").write_text(str(os.getpid()))
    if os.name == "nt":
        ctypes.PyDLL("kernel32").Sleep(ctypes.c_ulong(60000))
    else:
        ctypes.PyDLL(None).sleep(ctypes.c_uint(60))
step, preview = folder / "geometry.step", folder / "preview.glb"
step.write_bytes(b"fixture STEP")
preview.write_bytes(b"glTF fixture")
result = {"components": [{"id": "c1", "name": "fixture", "kind": "solid", "faces": []}],
          "links": [], "issues": [], "stepPath": str(step), "previewPath": str(preview),
          "sha256": hashlib.sha256(step.read_bytes()).hexdigest(), "triangleCount": 1,
          "identityMapping": "fixture"}
output.write_text(json.dumps({"ok": True, "generated": result}), encoding="utf-8")
''', encoding="utf-8")
    monkeypatch.setattr(GeometryProcesses, "_command",
                        lambda self, job, output: [sys.executable, str(script), str(job), str(output)])
    return script


def wait_until(check, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(.02)
    raise AssertionError("Timed out waiting for expected geometry state")


def upload(client, pid, data):
    response = client.post(f"/api/projects/{pid}/geometry",
                           files={"file": ("test.step", data, "application/step")})
    assert response.status_code == 200, response.text
    return response.json()


def test_native_gil_hang_keeps_api_responsive_and_queue_recovers(tmp_path, monkeypatch, native_worker):
    monkeypatch.setenv("THERMAL_GEOMETRY_TIMEOUT_SECONDS", "2")
    app = create_app(tmp_path / "projects", NXAdapter(url=""), start_scheduler=False)
    with TestClient(app) as client:
        p = client.post("/api/projects", json={"name": "blocked CAD"}).json()
        pid = p["id"]
        upload(client, pid, b"HOLD_GIL")
        folder = app.state.store.directory(pid)
        wait_until(lambda: list(folder.glob("imports/*/native-started")))
        # These exact actions became indefinitely stuck in the original service.
        started = time.monotonic()
        assert client.post("/api/projects", json={"name": "still usable"}).status_code == 200
        assert len(client.get("/api/projects").json()["projects"]) == 2
        assert client.get("/api/health").status_code == 200
        assert time.monotonic() - started < 1
        assert client.get(f"/api/projects/{pid}").json()["geometry"]["status"] == "importing"

        def failed():
            state = client.get(f"/api/projects/{pid}").json()
            return state if state["geometry"]["status"] == "error" else None
        failed_project = wait_until(failed)
        assert failed_project["geometry"]["errorCode"] == "geometry_timeout"
        assert "原文件已保留" in failed_project["geometry"]["error"]
        assert next(folder.glob("imports/*/source.step")).read_bytes() == b"HOLD_GIL"
        assert not app.state.geometry_processes._children

        upload(client, pid, b"VALID_FIXTURE")
        wait_until(lambda: client.get(f"/api/projects/{pid}").json()["geometry"]["status"] == "ready")
        p = client.get(f"/api/projects/{pid}").json()
        assert p["components"][0]["name"] == "fixture"
        assert client.get(p["geometry"]["previewUrl"]).content == b"glTF fixture"


def test_service_shutdown_reaps_its_native_worker(tmp_path, monkeypatch, native_worker):
    monkeypatch.setenv("THERMAL_GEOMETRY_TIMEOUT_SECONDS", "60")
    app = create_app(tmp_path / "projects", NXAdapter(url=""), start_scheduler=False)
    with TestClient(app) as client:
        p = client.post("/api/projects", json={}).json()
        upload(client, p["id"], b"HOLD_GIL")
        wait_until(lambda: list(app.state.store.directory(p["id"]).glob("imports/*/native-started")))
        children = list(app.state.geometry_processes._children)
        assert len(children) == 1 and children[0].poll() is None
        started = time.monotonic()
    assert time.monotonic() - started < 2
    assert all(child.poll() is not None for child in children)


def test_worker_entrypoint_does_not_import_http_app_or_cad_on_import(tmp_path):
    import subprocess
    service = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "-c", "import sys; import thermal_service.geometry_worker; "
        "assert 'thermal_service.app' not in sys.modules; assert 'cadquery' not in sys.modules"],
        cwd=service, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
