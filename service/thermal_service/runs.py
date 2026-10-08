from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import threading
import time
from pathlib import Path

import httpx

from .schema import standard
from .storage import DomainError, file_digest, now, uid

TERMINAL = {"completed", "failed", "cancelled"}
ACTIVE = {"preparing", "modeling", "checking", "solving", "extracting", "cancel_requested"}
ALLOWED = TERMINAL | ACTIVE | {"queued", "waiting_connection"}


def event(run, stage, message):
    if run.get("stage") != stage or run.get("message") != message:
        run.setdefault("events", []).append({"time": now(), "stage": stage, "message": message})
        run["events"] = run["events"][-500:]
    run["stage"], run["message"], run["updatedAt"] = stage, message, now()


def find_run(project, rid):
    run = next((r for r in project["runs"] if r["id"] == rid), None)
    if not run:
        raise DomainError("运行不存在。", "run_not_found", 404)
    return run


def queue_run(store, project, case_id=None):
    if project["geometry"]["status"] != "ready" or not project["components"]:
        raise DomainError("请先导入可用几何。", "geometry_not_ready", 409)
    if case_id is None:
        if len(project["cases"]) != 1:
            raise DomainError("多工况必须分别创建具有明确 caseId 的运行。", "case_required")
        case_id = project["cases"][0]["id"]
    if not any(c["id"] == case_id for c in project["cases"]):
        raise DomainError("所选工况不存在。", "case_not_found")
    snapshot = standard(project)
    existing = next((r for r in project["runs"] if r.get("inputHash") == snapshot["inputHash"] and r.get("caseId") == case_id and r["status"] not in TERMINAL), None)
    if existing:
        return existing
    stamp = now()
    run = {"id": uid("run-"), "projectId": project["id"], "revision": project["revision"], "createdAt": stamp, "updatedAt": stamp,
           "status": "queued", "stage": "queued", "progress": 0, "message": "已冻结本次输入，等待执行。", "issues": copy.deepcopy(project["issues"]),
           "events": [{"time": stamp, "stage": "queued", "message": "输入快照已保存，后续编辑不会覆盖本次运行。"}],
           "inputHash": snapshot["inputHash"], "artifacts": []}
    if case_id:
        run["caseId"] = case_id
        run["caseName"] = next(c["name"] for c in project["cases"] if c["id"] == case_id)
    snapshot["run"] = {"id": run["id"], "caseId": case_id, "revision": project["revision"]}
    store.freeze(project["id"], run["id"], snapshot)
    project["runs"].append(run)
    return run


def queue_requested(store, project, case_id=None):
    """Each immutable NX job has exactly one named steady-state case."""
    if case_id is not None:
        return [queue_run(store, project, case_id)]
    return [queue_run(store, project, case["id"]) for case in project["cases"]]


class Scheduler:
    """One executor lane, persisted snapshots and resumable idempotent dispatch."""

    def __init__(self, store, adapter, interval=2):
        self.store, self.adapter, self.interval = store, adapter, interval
        self.stop_event = threading.Event()
        self.thread = None
        self.last_health = 0

    def start(self):
        self.thread = threading.Thread(target=self.loop, name="thermal-nx-queue", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)

    def loop(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception:
                # The project/run state is retained. A transient I/O or worker
                # fault must not kill the scheduler for unrelated projects.
                import logging
                logging.getLogger(__name__).exception("Queue iteration failed")
            self.stop_event.wait(self.interval)

    def tick(self):
        if time.monotonic() - self.last_health > 10:
            self.adapter.health()
            self.last_health = time.monotonic()
        with self.store.lock:
            projects = self.store.list()
            active = [(p, r) for p in projects for r in p["runs"] if r["status"] in ACTIVE]
            candidates = sorted([(p, r) for p in projects for r in p["runs"] if r["status"] in {"queued", "waiting_connection"}], key=lambda pr: pr[1]["createdAt"])
            if active:
                project, run = min(active, key=lambda pr: pr[1]["createdAt"])
            elif candidates:
                project, run = candidates[0]
            else:
                return
            pid, rid = project["id"], run["id"]
            if not self.adapter.health_cache["connected"]:
                if run["status"] not in ACTIVE:
                    run["status"] = "waiting_connection"
                    event(run, "waiting_connection", self.adapter.health_cache["message"])
                    self.store.save(project)
                return
            job = run.get("_jobId")
            status = run["status"]
            if not job:
                if status == "cancel_requested" and not run.get("_dispatchAttempted"):
                    run["status"] = "cancelled"
                    event(run, "cancelled", "执行器尚未接收，本次运行已取消。")
                    self.store.save(project)
                    return
                if status != "cancel_requested":
                    run["status"] = "preparing"
                    event(run, "preparing", "正在传送冻结输入和 STEP 到 NX 执行器。")
                else:
                    event(run, "cancel_requested", "正在通过幂等任务标识确认执行端任务，再请求停止。")
                # Persist dispatch intent first: a lost POST response does not
                # prove the worker never accepted the job. Retrying uses the
                # same immutable run id and must return the same remote job.
                run["_dispatchAttempted"] = True
                self.store.save(project)
                snapshot = self.store.snapshot(pid, rid)
                geom_path, _ = self.store.artifact(project, snapshot["geometry"]["artifactId"])
        try:
            if not job:
                remote = self.adapter.submit(snapshot, geom_path)
                job = remote.get("id")
                if not isinstance(job, str) or not job:
                    raise ValueError("Worker returned no job id")
                with self.store.lock:
                    p = self.store.get(pid)
                    r = find_run(p, rid)
                    r["_jobId"] = job
                    # Persist remote ID before interpreting response or downloads.
                    self.store.save(p)
                    if r["status"] == "cancel_requested":
                        remote = None
                if remote is None:
                    remote = self.adapter.cancel(job)
            elif status == "cancel_requested":
                remote = self.adapter.cancel(job)
            else:
                remote = self.adapter.poll(job)
            self.apply_remote(pid, rid, job, remote)
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if 400 <= code < 500 and code not in {408, 409, 429}:
                with self.store.lock:
                    p = self.store.get(pid)
                    r = find_run(p, rid)
                    r["status"] = "failed"
                    event(r, "failed", f"NX 执行器拒绝请求（HTTP {code}）。输入和原始资料已保留，可修改后重试。")
                    self.store.save(p)
            else:
                self.connection_lost(pid, rid)
        except (httpx.HTTPError, OSError):
            self.connection_lost(pid, rid)
        except (ValueError, KeyError, TypeError) as exc:
            import logging
            logging.getLogger(__name__).warning("NX payload rejected for run %s: %s", rid, type(exc).__name__ + ": " + str(exc))
            with self.store.lock:
                p = self.store.get(pid)
                r = find_run(p, rid)
                r["status"] = "failed"
                event(r, "failed", "NX 返回的数据不符合执行契约，未标记为求解完成。")
                self.store.save(p)

    def connection_lost(self, pid, rid):
        with self.store.lock:
            p = self.store.get(pid)
            r = find_run(p, rid)
            event(r, r["stage"], "执行器暂时断连，已保留任务身份；恢复后继续查询，未宣称已取消或完成。")
            self.store.save(p)
        self.adapter.health_cache["connected"] = False

    def apply_remote(self, pid, rid, job, remote):
        remote_status = remote.get("status")
        if remote_status not in ALLOWED:
            raise ValueError("Unknown worker status")
        # Copy actual artifacts before exposing their URLs. The worker cannot
        # choose local paths or make the browser fetch arbitrary remote URLs.
        fetched = []
        readback_artifact = remote.get("readback", {}).get("meshArtifact")
        result_artifact = remote.get("result", {}).get("meshArtifact")
        for item in remote.get("artifacts", [])[:1000]:
            name = item.get("name", "")
            if not name or Path(name).name != name or "/" in name or "\\" in name or ":" in name or name in {".", ".."}:
                raise ValueError("Unsafe artifact name")
            if remote_status not in TERMINAL and name not in {readback_artifact, result_artifact}:
                # Native FEM/SIM and logs are still being written by later NX
                # stages. A previously published hash may already be obsolete.
                # Readback's geometric preview is the only intermediate file
                # needed by the browser; mirror all native evidence at the end.
                continue
            expected_sha = item.get("sha256")
            if expected_sha and not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
                raise ValueError("Invalid artifact checksum")
            folder = self.store.directory(pid) / "runs" / rid / "artifacts"
            folder.mkdir(parents=True, exist_ok=True)
            # Content-addressed paths keep previously downloaded evidence
            # immutable even if the worker later publishes a newer file.
            path = folder / (((expected_sha + "-") if expected_sha else "") + name)
            if not path.is_file() or (item.get("sha256") and file_digest(path) != item["sha256"]):
                self.adapter.download(job, name, path)
            if item.get("sha256") and file_digest(path) != item["sha256"]:
                if remote_status not in TERMINAL:
                    path.unlink(missing_ok=True)
                    continue
                raise ValueError("Artifact checksum mismatch")
            fetched.append((path, item))
        with self.store.lock:
            p = self.store.get(pid)
            run = find_run(p, rid)
            # Cancellation is acknowledged only by the executor. Completion
            # racing with cancellation remains completed with its real evidence.
            if run["status"] == "cancel_requested" and remote_status not in TERMINAL:
                remote_status = "cancel_requested"
            run["status"] = remote_status
            event(run, remote.get("stage", remote_status), str(remote.get("message", "NX 执行状态已更新。"))[:2000])
            progress = remote.get("progress", run["progress"])
            run["progress"] = min(100, max(0, float(progress))) if isinstance(progress, (int, float)) and math.isfinite(progress) else run["progress"]
            artifact_map = {a["name"]: a for a in run.get("artifacts", [])}
            for path, item in fetched:
                artifact_map[item["name"]] = self.store.register(p, path, item["name"], item.get("mediaType", "application/octet-stream"))
            run["artifacts"] = list(artifact_map.values())
            for key in ("readback", "result"):
                if isinstance(remote.get(key), dict):
                    value = copy.deepcopy(remote[key])
                    for url_key in ("meshUrl", "previewUrl"):
                        raw = value.pop(url_key, None)
                        name = value.get("meshArtifact" if url_key == "meshUrl" else "previewArtifact")
                        if name in artifact_map:
                            value[url_key] = artifact_map[name]["url"]
                        elif raw:
                            # Never trust an executor-provided external URL.
                            basename = str(raw).rsplit("/", 1)[-1]
                            if basename in artifact_map:
                                value[url_key] = artifact_map[basename]["url"]
                    run[key] = value
            if remote_status == "completed":
                result = run.get("result", {})
                readback = run.get("readback", {})
                expected_ids = {c["id"] for c in self.store.snapshot(pid, rid)["components"]}
                actual_ids = {c.get("id") for c in result.get("components", [])}
                evidence_names = set(artifact_map)
                has_native = any(name.lower().endswith((".sim", ".fem", ".prt")) for name in evidence_names)
                has_temperature = any(name.lower().endswith((".vtu", ".vtk", ".csv", ".json")) for name in evidence_names)
                if result.get("source") != "nx" or not readback or actual_ids != expected_ids or not has_native or not has_temperature:
                    run["status"] = "failed"
                    event(run, "failed", "执行端缺少完整 NX 回读、全部组件温度或原生工程/结果工件，不能判定完成。")
                else:
                    for stat in result.get("components", []):
                        if any(not isinstance(stat.get(k), (int, float)) or not math.isfinite(stat[k]) or stat[k] < 0 for k in ("minK", "maxK", "meanK")):
                            raise ValueError("Invalid NX temperature statistics")
                    run["progress"] = 100
            if isinstance(remote.get("issues"), list):
                run["issues"] = self.store.snapshot(pid, rid)["issues"] + remote["issues"]
            self.store.save(p)


def request_cancel(project, rid):
    run = find_run(project, rid)
    if run["status"] in TERMINAL:
        return run
    if run["status"] in {"queued", "waiting_connection"} and not run.get("_jobId"):
        run["status"] = "cancelled"
        event(run, "cancelled", "已取消尚未提交的队列任务。")
    else:
        run["status"] = "cancel_requested"
        event(run, "cancel_requested", "已请求取消，正在等待执行器确认停止。")
    return run
