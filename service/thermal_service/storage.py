from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


def now():
    return datetime.now(timezone.utc).isoformat()


def uid(prefix=""):
    return prefix + uuid.uuid4().hex


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def file_digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fp:
        while chunk := fp.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


class DomainError(Exception):
    def __init__(self, detail, code="invalid_input", status=422):
        self.detail, self.code, self.status = detail, code, status
        super().__init__(detail)


def valid_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,96}", value):
        raise DomainError("无效的对象标识。", "invalid_id", 400)
    return value


def safe_filename(name):
    basename = PurePosixPath(str(name).replace("\\", "/")).name
    basename = re.sub(r'[\x00-\x1f<>:"|?*]', "_", basename).strip(" .")
    if not basename or basename.upper().split(".", 1)[0] in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        basename = "artifact-" + (basename or "file")
    return basename[:180]


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as fp:
            json.dump(value, fp, ensure_ascii=False, indent=2, allow_nan=False)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class Store:
    """A single-process transactional store. Run Uvicorn with one worker.

    Project writes and queue decisions share one lock. Run snapshots are written
    once; status is in a separate file. Atomic rename prevents torn reads after
    a service crash. The lock never spans a remote request.
    """

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def directory(self, pid):
        return self.root / valid_id(pid)

    def get(self, pid):
        path = self.directory(pid) / "project.json"
        if not path.is_file():
            raise DomainError("项目不存在。", "project_not_found", 404)
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, project):
        atomic_json(self.directory(project["id"]) / "project.json", project)

    def list(self):
        result = []
        for item in self.root.glob("*/project.json"):
            try:
                result.append(json.loads(item.read_text(encoding="utf-8")))
            except (ValueError, OSError):
                # A damaged project cannot make every healthy project disappear.
                continue
        return sorted(result, key=lambda p: p["updatedAt"], reverse=True)

    def snapshot(self, pid, rid):
        path = self.directory(pid) / "runs" / valid_id(rid) / "input.json"
        if not path.is_file():
            raise DomainError("运行不存在。", "run_not_found", 404)
        return json.loads(path.read_text(encoding="utf-8"))

    def freeze(self, pid, rid, snapshot):
        path = self.directory(pid) / "runs" / valid_id(rid) / "input.json"
        if path.exists():
            raise DomainError("运行输入已冻结，不能覆盖。", "immutable_run", 409)
        atomic_json(path, snapshot)

    def artifact(self, project, artifact_id):
        valid_id(artifact_id)
        item = project.get("_artifacts", {}).get(artifact_id)
        if not item:
            raise DomainError("文件不存在。", "artifact_not_found", 404)
        root = self.directory(project["id"]).resolve()
        path = (root / item["path"]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise DomainError("文件不存在。", "artifact_not_found", 404)
        return path, item

    def register(self, project, path, name=None, media_type="application/octet-stream"):
        root = self.directory(project["id"]).resolve()
        path = Path(path).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise DomainError("不能注册项目外部文件。", "invalid_artifact", 400)
        relative = path.relative_to(root).as_posix()
        existing = next((key for key, item in project.get("_artifacts", {}).items() if item["path"] == relative), None)
        key = existing or uid("a-")
        item = {"path": relative, "name": safe_filename(name or path.name), "mediaType": media_type, "size": path.stat().st_size,
                "sha256": file_digest(path)}
        project.setdefault("_artifacts", {})[key] = item
        return {"id": key, "name": item["name"], "url": f"/api/projects/{project['id']}/artifacts/{key}",
                "size": item["size"], "sha256": item["sha256"], "mediaType": media_type}

    def public(self, project):
        return {key: copy.deepcopy(value) for key, value in project.items() if not key.startswith("_")}
