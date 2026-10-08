from __future__ import annotations

import copy
import hashlib
import io
import json
import logging
import os
import secrets
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware

from . import __version__, documents, geometry, heatflux
from .geometry_process import GeometryProcesses
from .nx_adapter import NXAdapter
from .runs import Scheduler, find_run, queue_requested, request_cancel
from .schema import new_project, normalize, origin, physical, standard
from .storage import DomainError, Store, digest, now, uid


def clean(value):
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items() if not k.startswith("_")}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value


def expect_revision(project, payload):
    revision = payload.get("revision")
    if isinstance(revision, bool) or revision != project["revision"]:
        raise DomainError("项目已被其他窗口或聊天修改。请刷新后重新保存，避免覆盖新参数。", "revision_conflict", 409)


def save_changed(store, project, before, force=False):
    project = normalize(project)
    physical_changed = physical(project) != before
    if force or physical_changed:
        project["revision"] += 1
        project["updatedAt"] = now()
        if physical_changed and project["settings"]["autoRun"] and project["geometry"]["status"] == "ready":
            queue_requested(store, project)
    store.save(project)
    return project


def create_app(data_dir=None, adapter=None, start_scheduler=True):
    store = Store(data_dir or os.environ.get("THERMAL_DATA_DIR", str(Path(__file__).parents[1] / "data")))
    adapter = adapter or NXAdapter()
    scheduler = Scheduler(store, adapter)
    imports = ThreadPoolExecutor(max_workers=1, thread_name_prefix="thermal-import")
    geometry_processes = GeometryProcesses()

    @asynccontextmanager
    async def lifespan(app):
        # An interrupted import is recoverable from its retained source; never
        # leave the browser polling an importing state indefinitely after reboot.
        with store.lock:
            for p in store.list():
                if p["geometry"]["status"] == "importing":
                    p["geometry"]["status"] = "error"
                    p["geometry"]["error"] = "服务重启中断了导入，原文件已保留，请重新导入。"
                    store.save(p)
        if start_scheduler:
            scheduler.start()
        yield
        scheduler.stop()
        geometry_processes.close()
        imports.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="Satellite Thermal Engineering Service", version=__version__, lifespan=lifespan)
    app.state.store, app.state.scheduler, app.state.adapter = store, scheduler, adapter
    app.state.geometry_processes = geometry_processes

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        token = os.environ.get("THERMAL_SERVICE_TOKEN", "")
        if token:
            auth = request.headers.get("authorization", "")
            if not secrets.compare_digest(auth, f"Bearer {token}"):
                return JSONResponse({"detail": "服务需要有效访问凭据。", "code": "unauthorized"}, status_code=401)
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > geometry.MAX_UPLOAD + 1024 * 1024:
            return JSONResponse({"detail": "上传超过当前服务上限。", "code": "upload_too_large"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse({"detail": exc.detail, "code": exc.code}, status_code=exc.status)

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({"detail": "输入数据格式无效，请检查数值、文件内容和对象引用。", "code": "invalid_input"}, status_code=422)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__, "solver": adapter.health(),
                "capabilities": {"geometry": geometry.available(), "documents": True, "nx": bool(adapter.url), "analysis": ["steady-state"], "transient": "planned"}}

    @app.get("/api/projects")
    def projects():
        with store.lock:
            return {"projects": [clean(p) for p in store.list()]}

    @app.post("/api/projects")
    def create(payload: dict):
        p = normalize(new_project(payload.get("name")))
        with store.lock:
            store.save(p)
        return clean(p)

    def finish_geometry(pid, upload, directory, source, filename, operation):
        try:
            generated = geometry_processes.run(upload, directory, example=source == "example")
            with store.lock:
                p = store.get(pid)
                if p.get("_geometryOperation") != operation:
                    return
                before = physical(p)
                step_art = store.register(p, generated["stepPath"], "geometry.step", "application/step")
                preview_art = store.register(p, generated["previewPath"], "preview.glb", "model/gltf-binary")
                p["geometry"] = {"status": "ready", "source": source, "filename": filename, "sha256": generated["sha256"],
                                 "artifactId": step_art["id"], "previewUrl": preview_art["url"], "units": "mm", "coordinateSystem": "Z-up",
                                 "identityMapping": generated["identityMapping"], "triangleCount": generated["triangleCount"],
                                 "previewSource": "source-step-tessellation", "nxReadback": False}
                p["components"], p["links"] = generated["components"], generated["links"]
                p["boundaries"] = [{"id": "b-space", "name": "对空间辐射", "kind": "space", "targets": [{"componentId": c["id"]} for c in p["components"]],
                                    "value": p["settings"]["spaceTemperatureK"], "unit": "K", "provenance": origin("estimated", "默认空间环境", reason="按全部外表面申请空间辐射；执行器须计算可见性，避免内部表面向深空重复辐射。") }]
                p["_importIssues"] = generated["issues"]
                for doc in p["documents"]:
                    records = doc.get("_records", [])
                    applied, unmatched = documents.apply_records(p, records)
                    doc["appliedValues"], doc["unmatchedComponents"] = applied, unmatched
                if source == "example":
                    for c, power in zip(p["components"], [0., 18., 8.]):
                        c["power"] = power
                        c["provenance"]["power"] = origin("provided", "教学演示参数；不是实测资料")
                    p["example"] = True
                save_changed(store, p, before, force=True)
        except Exception as exc:
            logging.getLogger(__name__).exception("Geometry import failed")
            with store.lock:
                p = store.get(pid)
                if p.get("_geometryOperation") != operation:
                    return
                p["geometry"]["status"] = "error"
                p["geometry"]["error"] = exc.detail if isinstance(exc, DomainError) else "几何导入失败，原文件已保留。请检查 STEP 或工程服务依赖。"
                p["geometry"]["errorCode"] = exc.code if isinstance(exc, DomainError) else "geometry_failed"
                p["updatedAt"] = now()
                store.save(p)

    @app.post("/api/projects/example")
    def example():
        p = normalize(new_project("示例 · 舱板与两台设备（未求解）"))
        folder = store.directory(p["id"]) / "imports" / uid("g-")
        operation = uid("import-")
        p["_geometryOperation"] = operation
        p["geometry"] = {"status": "importing", "source": "example", "filename": "example.step"}
        with store.lock:
            store.save(p)
        # Preserve the synchronous example response while native work shares the
        # bounded queue and process isolation used by uploaded geometry.
        imports.submit(finish_geometry, p["id"], None, folder, "example", "example.step", operation).result()
        return clean(store.get(p["id"]))

    @app.get("/api/projects/{pid}")
    def get_project(pid):
        with store.lock:
            return clean(store.get(pid))

    @app.patch("/api/projects/{pid}")
    def patch(pid, payload: dict):
        allowed = {"revision", "name", "components", "links", "boundaries", "cases", "orbit", "settings"}
        if set(payload) - allowed:
            raise DomainError("包含不支持修改的字段。", "unknown_field")
        with store.lock:
            p = store.get(pid)
            expect_revision(p, payload)
            before = physical(p)
            if "components" in payload:
                incoming = payload["components"]
                if not isinstance(incoming, list):
                    raise DomainError("组件必须是数组。")
                old = {c["id"]: c for c in p["components"]}
                if set(c.get("id") for c in incoming) != set(old) or len(incoming) != len(old):
                    raise DomainError("参数编辑不能删除或增加 STEP 组件，请重新导入几何。", "geometry_identity_changed")
                merged = []
                for c in incoming:
                    previous = old[c["id"]]
                    item = copy.deepcopy(previous)
                    material_changed = "material" in c and c["material"] != previous.get("material")
                    editable = {"name", "kind", "material", "conductivity", "density", "specificHeat", "emissivity", "absorptivity", "power", "thickness", "temperatureLimitMin", "temperatureLimitMax", "shell", "businessComponentId", "thermalLoadScope"}
                    for key in editable:
                        if key in c:
                            if c[key] != previous.get(key):
                                item.setdefault("provenance", {})[key] = origin("manual", "网页或 DSH 原生工具编辑", c[key])
                            item[key] = c[key]
                    # MCP data imports carry their actual evidence, including
                    # confirmed estimates whose numeric values are unchanged.
                    # Keep the usual manual origin when no evidence is supplied.
                    supplied_origins = c.get("provenance", {})
                    if not isinstance(supplied_origins, dict):
                        raise DomainError("参数来源必须是对象。", "invalid_provenance")
                    for key, evidence in supplied_origins.items():
                        if key not in editable:
                            continue
                        if (not isinstance(evidence, dict)
                                or evidence.get("status") not in {"manual", "estimated", "derived", "provided"}
                                or not isinstance(evidence.get("source"), str)
                                or not evidence["source"].strip()):
                            raise DomainError("参数来源缺少有效状态或依据。", "invalid_provenance")
                        # A table edit returns the component's old evidence as
                        # well as its new value. That stale evidence must not
                        # undo the manual origin assigned above. An explicitly
                        # updated source/reason remains authoritative for MCP.
                        if (key in c and c[key] != previous.get(key)
                                and evidence == previous.get("provenance", {}).get(key)):
                            continue
                        item.setdefault("provenance", {})[key] = copy.deepcopy(evidence)
                    if material_changed:
                        # Changing material refreshes values that were only
                        # estimated from its old library entry. Explicit values
                        # supplied in the same edit remain authoritative.
                        for property_key in ("conductivity", "density", "specificHeat"):
                            was_estimated = previous.get("provenance", {}).get(property_key, {}).get("status") == "estimated"
                            explicitly_changed = property_key in c and c[property_key] != previous.get(property_key)
                            if was_estimated and not explicitly_changed:
                                item.pop(property_key, None)
                    if "faces" in c:
                        faces_by_id = {f["id"]: f for f in previous["faces"]}
                        if set(f.get("id") for f in c["faces"]) != set(faces_by_id):
                            raise DomainError("参数编辑不能更改源几何面身份。", "face_identity_changed")
                        item["faces"] = []
                        for f in c["faces"]:
                            face = copy.deepcopy(faces_by_id[f["id"]])
                            for key in ("emissivity", "absorptivity", "side", "name"):
                                if key in f:
                                    face[key] = f[key]
                            item["faces"].append(face)
                    merged.append(item)
                p["components"] = merged
            for key in ("links", "boundaries", "cases"):
                if key in payload:
                    if not isinstance(payload[key], list) or len(payload[key]) > 20000:
                        raise DomainError("列表格式无效或超过上限。")
                    p[key] = copy.deepcopy(payload[key])
            for key in ("orbit", "settings"):
                if key in payload:
                    if not isinstance(payload[key], dict):
                        raise DomainError("参数格式必须是对象。")
                    p[key].update(payload[key])
            rename = "name" in payload and str(payload["name"]) != p["name"]
            if rename:
                p["name"] = str(payload["name"])[:160]
            normalized = normalize(p)
            if rename and physical(normalized) == before:
                normalized["revision"] += 1
                normalized["updatedAt"] = now()
                store.save(normalized)
                p = normalized
            else:
                p = save_changed(store, p, before)
            return clean(p)

    async def receive(upload: UploadFile, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        try:
            with path.open("wb") as fp:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > geometry.MAX_UPLOAD:
                        raise DomainError("上传超过上限。", "upload_too_large", 413)
                    fp.write(chunk)
            if not size:
                raise DomainError("文件为空。", "empty_file")
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            await upload.close()

    @app.post("/api/projects/{pid}/geometry")
    async def import_step(pid, file: UploadFile = File(...)):
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in {".step", ".stp", ".zip"}:
            raise DomainError("请选择 STEP/STP 或几何交付 ZIP。", "unsupported_geometry")
        with store.lock:
            p = store.get(pid)
            if p["geometry"]["status"] == "importing":
                raise DomainError("本项目已有几何正在导入，请稍后再试。", "import_in_progress", 409)
        folder = store.directory(pid) / "imports" / uid("g-")
        path = folder / ("source" + suffix)
        await receive(file, path)
        with store.lock:
            p = store.get(pid)
            operation = uid("import-")
            p["_geometryOperation"] = operation
            p["geometry"] = {"status": "importing", "source": "geometry-package" if suffix == ".zip" else "step", "filename": Path(file.filename or "geometry.step").name}
            store.register(p, path, p["geometry"]["filename"])
            p["updatedAt"] = now()
            store.save(p)
        imports.submit(finish_geometry, pid, path, folder, p["geometry"]["source"], p["geometry"]["filename"], operation)
        return clean(p)

    def consume_document(pid, path, name):
        p = store.get(pid)
        extracted = documents.extract(path, name, p["components"])
        with store.lock:
            p = store.get(pid)
            before = physical(p)
            artifact = store.register(p, path, name)
            text_path = path.with_name(path.stem + ".extracted.txt")
            text_path.write_text(extracted["text"], encoding="utf-8")
            text_artifact = store.register(p, text_path, name + ".txt", "text/plain; charset=utf-8")
            applied, unmatched = documents.apply_records(p, extracted["records"])
            p["documents"].append({"id": uid("doc-"), "name": name, "status": "applied" if applied else "needs-review",
                                   "excerpt": extracted["text"][:6000], "sourceUrl": artifact["url"], "textUrl": text_artifact["url"], "sha256": artifact["sha256"],
                                   "appliedValues": applied, "unmatchedComponents": unmatched, "_records": extracted["records"]})
            if extracted["cases"]:
                p["cases"] = extracted["cases"]
            p = save_changed(store, p, before, force=True)
            return clean(p)

    @app.post("/api/projects/{pid}/documents")
    async def import_document(pid, file: UploadFile = File(...)):
        store.get(pid)
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in {".xlsx", ".csv", ".tsv", ".docx", ".pdf", ".json", ".txt", ".md"}:
            raise DomainError("支持 Excel/CSV、Word、文字 PDF、JSON 和文本。", "unsupported_document")
        path = store.directory(pid) / "documents" / (uid("source-") + suffix)
        await receive(file, path)
        return consume_document(pid, path, Path(file.filename or path.name).name)

    @app.post("/api/projects/{pid}/text")
    def import_text(pid, payload: dict):
        store.get(pid)
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 500000:
            raise DomainError("请输入非空文本，长度不超过 500000 字符。", "invalid_text")
        path = store.directory(pid) / "documents" / (uid("text-") + ".txt")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return consume_document(pid, path, "粘贴的热参数资料")

    @app.get("/api/projects/{pid}/standard")
    def get_standard(pid):
        with store.lock:
            return standard(store.get(pid))

    @app.post("/api/projects/{pid}/heatflux")
    def compute_heatflux(pid, payload: dict):
        with store.lock:
            p = store.get(pid)
            if "revision" in payload:
                expect_revision(p, payload)
            before = physical(p)
            if payload.get("orbit"):
                p["orbit"].update(payload["orbit"])
            p = normalize(p)
            folder = store.directory(pid) / "heatflux" / uid("flux-")
            path = heatflux.calculate(p, folder, payload.get("time"), payload.get("season"))
            p["heatfluxSource"] = store.register(p, path, "six-direction-incident-flux.csv", "text/csv")
            p = save_changed(store, p, before)
            return clean(p)

    @app.post("/api/projects/{pid}/runs")
    def start_run(pid, payload: dict):
        with store.lock:
            p = store.get(pid)
            expect_revision(p, payload)
            queued = queue_requested(store, p, payload.get("caseId"))
            if not queued:
                raise DomainError("请先导入几何并生成标准工况。", "geometry_not_ready", 409)
            store.save(p)
            result = clean(queued[0])
            result["queuedRunIds"] = [run["id"] for run in queued]
            return result

    @app.get("/api/projects/{pid}/runs/{rid}")
    def get_run(pid, rid):
        with store.lock:
            p = store.get(pid)
            run = clean(find_run(p, rid))
            run["inputSnapshot"] = store.snapshot(pid, rid)
            return run

    @app.post("/api/projects/{pid}/runs/{rid}/cancel")
    def cancel_run(pid, rid):
        with store.lock:
            p = store.get(pid)
            run = request_cancel(p, rid)
            store.save(p)
            return clean(run)

    @app.get("/api/projects/{pid}/artifacts/{artifact_id}")
    def artifact(pid, artifact_id):
        with store.lock:
            p = store.get(pid)
            path, item = store.artifact(p, artifact_id)
        return FileResponse(path, media_type=item["mediaType"], filename=item["name"])

    @app.get("/api/projects/{pid}/export")
    def export(pid):
        with store.lock:
            p = store.get(pid)
            # A temporary on-disk archive avoids retaining large native projects
            # twice in RAM. Entries come exclusively from allowlisted artifacts.
            path = store.directory(pid) / (uid("export-") + ".zip")
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("standard-input.json", json.dumps(standard(p), ensure_ascii=False, indent=2))
                archive.writestr("project.json", json.dumps(clean(p), ensure_ascii=False, indent=2))
                archive.writestr("manifest.json", json.dumps({"schemaVersion": "1.0", "createdAt": now(), "projectId": pid,
                      "revision": p["revision"], "nxExecution": "completed" if any(r["status"] == "completed" for r in p["runs"]) else "not-completed",
                      "notice": "源 STEP 预览不等于 NX 回读；仅含真实生成的文件。未完成求解时不含虚构温度。",
                      "runs": [{"id": r["id"], "revision": r["revision"], "status": r["status"]} for r in p["runs"]]}, ensure_ascii=False, indent=2))
                for r in p["runs"]:
                    archive.writestr(f"runs/{r['id']}/input.json", json.dumps(store.snapshot(pid, r["id"]), ensure_ascii=False, indent=2))
                size = 0
                for aid in p.get("_artifacts", {}):
                    source, item = store.artifact(p, aid)
                    size += source.stat().st_size
                    if size > 2 * 1024**3:
                        raise DomainError("数据包超过 2 GiB，请分别下载工件。", "export_too_large", 413)
                    archive.write(source, f"artifacts/{aid}/{Path(item['name']).name}")
        from starlette.background import BackgroundTask
        return FileResponse(path, media_type="application/zip", filename=f"thermal-{pid}-r{p['revision']}.zip", background=BackgroundTask(path.unlink, missing_ok=True))

    return app


app = create_app()
