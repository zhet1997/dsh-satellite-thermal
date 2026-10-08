"""Authenticated HTTP transfer adapter for an independently deployed NX workstation."""
import base64
from contextlib import asynccontextmanager
import hmac
import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from .core import JobStore, MAX_GEOMETRY_BYTES, MAX_SNAPSHOT_BYTES
from .worker import Worker

def create_app(root=None, executable=None, token=None, start_worker=True):
    store = JobStore(root or os.environ.get("NX_WORK_ROOT", str(Path.cwd() / "nx-jobs")))
    worker = Worker(store, executable if executable is not None else os.environ.get("NX_JOURNAL_EXECUTABLE", ""), int(os.environ.get("NX_STAGE_TIMEOUT_SECONDS", "7200")))
    secret = token if token is not None else os.environ.get("NX_WORKER_TOKEN", "")

    @asynccontextmanager
    async def lifespan(app):
        if start_worker:
            worker.start()
        yield
        worker.close()

    app = FastAPI(title="NX thermal executor", version="0.1.0", lifespan=lifespan)
    app.state.store, app.state.worker = store, worker

    @app.middleware("http")
    async def authentication(request, call_next):
        if secret and not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + secret):
            return JSONResponse(status_code=401, content={"detail": "Worker authentication required"})
        length = request.headers.get("content-length")
        if length and (not length.isdigit() or int(length) > 2 * MAX_GEOMETRY_BYTES):
            return JSONResponse(status_code=413, content={"detail": "Request too large"})
        return await call_next(request)

    @app.exception_handler(KeyError)
    async def not_found(request, error):
        return JSONResponse(status_code=404, content={"detail": "Job or registered artifact not found"})

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse(status_code=422, content={"detail": str(error)})

    @app.get("/health")
    def health():
        return worker.health()

    @app.post("/jobs")
    async def submit(request: Request):
        if request.headers.get("content-type", "").startswith("multipart/form-data"):
            async with request.form(max_files=1, max_fields=5, max_part_size=MAX_SNAPSHOT_BYTES) as form:
                raw = form.get("snapshot", "")
                if not isinstance(raw, str) or len(raw.encode()) > MAX_SNAPSHOT_BYTES:
                    raise ValueError("Invalid snapshot field")
                snapshot = json.loads(raw)
                geometry = form.get("geometry")
                if geometry is None or not hasattr(geometry, "read"):
                    raise ValueError("Missing geometry upload")
                data = await geometry.read(MAX_GEOMETRY_BYTES + 1)
                state = store.submit(snapshot, geometry.filename or "geometry.step", data, form.get("idempotencyKey", snapshot.get("run", {}).get("id", "")), form.get("sha256") or None)
        else:
            raw = await request.body()
            if len(raw) > 2 * MAX_GEOMETRY_BYTES:
                raise HTTPException(413, "Request too large")
            body = json.loads(raw)
            if not isinstance(body, dict) or not isinstance(body.get("geometry"), dict) or not isinstance(body.get("snapshot"), dict):
                raise ValueError("JSON submission requires snapshot and geometry objects")
            geometry = body["geometry"]
            if not isinstance(geometry.get("contentBase64"), str) or not isinstance(geometry.get("filename"), str):
                raise ValueError("Geometry requires filename and contentBase64 strings")
            data = base64.b64decode(geometry["contentBase64"], validate=True)
            state = store.submit(body["snapshot"], geometry["filename"], data, body.get("idempotencyKey", ""), geometry.get("sha256"))
        worker.wake.set()
        return state

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str):
        return store.get(job_id)

    @app.post("/jobs/{job_id}/cancel")
    async def cancel(job_id: str, request: Request):
        body = await request.json()
        return worker.cancel(job_id, body.get("confirm"))

    @app.get("/jobs/{job_id}/artifacts/{name:path}")
    def artifact(job_id: str, name: str):
        path = store.artifact(job_id, name)
        return FileResponse(path, filename=path.name)

    return app

def main():
    import uvicorn
    host = os.environ.get("NX_WORKER_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "localhost", "::1"} and not os.environ.get("NX_WORKER_TOKEN"):
        raise SystemExit("NX_WORKER_TOKEN is required when listening on a network interface")
    uvicorn.run(create_app(), host=host, port=int(os.environ.get("NX_WORKER_PORT", "8791")))

if __name__ == "__main__":
    main()
