"""Durable frozen input, idempotency and artifact boundaries independent of NX."""
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import threading
from datetime import datetime, timezone
from uuid import uuid4
import zipfile

TERMINAL = {"completed", "failed", "cancelled"}
MAX_GEOMETRY_BYTES = 256 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024

def now():
    return datetime.now(timezone.utc).isoformat()

def digest(data):
    return hashlib.sha256(data).hexdigest()

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()

def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(canonical(value))
    os.replace(temporary, path)

def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

def safe_geometry(filename, data):
    """Only read one bounded STEP entry; do not extract paths from an archive."""
    if not data or len(data) > MAX_GEOMETRY_BYTES:
        raise ValueError("Geometry is empty or exceeds 256 MiB")
    extension = Path(filename).suffix.lower()
    if extension in {".step", ".stp"}:
        if b"ISO-10303-21" not in data[:4096].upper():
            raise ValueError("Geometry is not an ISO-10303-21 STEP file")
        return data
    if extension != ".zip":
        raise ValueError("Geometry must be STEP/STP or ZIP")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        if len(members) > 4096:
            raise ValueError("Geometry archive has too many entries")
        total, steps = 0, []
        for member in members:
            path = PurePosixPath(member.filename.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts or ":" in member.filename:
                raise ValueError("Unsafe geometry archive path")
            if ((member.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError("Geometry archive may not contain symbolic links")
            total += member.file_size
            if total > MAX_GEOMETRY_BYTES:
                raise ValueError("Expanded geometry exceeds 256 MiB")
            if path.suffix.lower() in {".step", ".stp"}:
                steps.append(member)
        if len(steps) != 1:
            raise ValueError("Geometry archive must contain exactly one STEP file")
        return safe_geometry(steps[0].filename, archive.read(steps[0]))

def validate_snapshot(snapshot):
    data = canonical(snapshot)
    if len(data) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Snapshot exceeds 16 MiB")
    if snapshot.get("analysisType", "steady-state") != "steady-state":
        raise ValueError("Worker supports steady-state analysis only")
    components = snapshot.get("components", [])
    cases = snapshot.get("cases", [])
    case_id = snapshot.get("run", {}).get("caseId")
    if len(cases) > 1 and not case_id:
        raise ValueError("Multiple cases require an explicit run.caseId; submit one job per case")
    if case_id and case_id not in {c.get("id") for c in cases}:
        raise ValueError("run.caseId does not identify a frozen case")
    if not components:
        raise ValueError("Snapshot has no components")
    ids, indices = set(), set()
    for component in components:
        cid, index = component.get("id"), component.get("stepIndex")
        if not isinstance(cid, str) or not cid or cid in ids:
            raise ValueError("Component IDs must be unique")
        ids.add(cid)
        if not isinstance(index, int) or index < 1 or index in indices:
            raise ValueError("stepIndex must be unique and one-based")
        indices.add(index)
        if component.get("kind") not in {"solid", "shell"}:
            raise ValueError("Component kind must be solid or shell")
        for name in ("conductivity", "emissivity", "absorptivity", "power"):
            value = component.get(name)
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("Standard snapshot missing finite %s for %s" % (name, cid))
        if component["conductivity"] <= 0 or component["power"] < 0:
            raise ValueError("Invalid conductivity or power")
        if not 0 <= component["emissivity"] <= 1 or not 0 <= component["absorptivity"] <= 1:
            raise ValueError("Optical coefficients must be between zero and one")
        if component["kind"] == "shell" and not float(component.get("thickness", 0)) > 0:
            raise ValueError("Shell thickness must be positive in mm")
    if indices != set(range(1, len(components) + 1)):
        raise ValueError("stepIndex sequence must cover every imported body")
    return data

class JobStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def path(self, job_id):
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise KeyError(job_id)
        path = self.root / job_id
        if not path.is_dir():
            raise KeyError(job_id)
        return path

    def list(self):
        with self.lock:
            return [load_json(path) for path in sorted(self.root.glob("*/state.json"))]

    def get(self, job_id):
        with self.lock:
            return load_json(self.path(job_id) / "state.json")

    def update(self, job_id, **values):
        with self.lock:
            state = self.get(job_id)
            previous_stage = state["stage"]
            state.update(values)
            state["updatedAt"] = now()
            if "message" in values or state["stage"] != previous_stage:
                state["events"].append({"time": state["updatedAt"], "stage": state["stage"], "message": state["message"]})
            atomic_json(self.path(job_id) / "state.json", state)
            return state

    def submit(self, snapshot, filename, geometry, idempotency_key="", expected_sha=None):
        frozen = validate_snapshot(snapshot)
        if expected_sha and digest(geometry) != expected_sha:
            raise ValueError("Geometry transfer SHA-256 mismatch")
        step = safe_geometry(filename, geometry)
        input_hash = digest(frozen + b"\x00" + step)
        key = str(idempotency_key or input_hash)
        with self.lock:
            for state in self.list():
                if state["idempotencyKey"] == key:
                    if state["inputHash"] != input_hash:
                        raise ValueError("Idempotency key already refers to different frozen input")
                    return state
            job_id = uuid4().hex
            directory = self.root / job_id
            directory.mkdir()
            for subdir in ("input", "nx", "raw", "results", "logs", "stages", "tmp", "journals"):
                (directory / subdir).mkdir()
            (directory / "input" / "snapshot.json").write_bytes(frozen)
            (directory / "input" / "geometry.step").write_bytes(step)
            atomic_json(directory / "input" / "manifest.json", {"schemaVersion": "1.0", "snapshotSha256": digest(frozen), "geometrySha256": digest(step), "transferSha256": digest(geometry), "filename": Path(filename).name})
            state = {"id": job_id, "status": "queued", "stage": "queued", "progress": 0, "message": "Frozen input accepted", "createdAt": now(), "updatedAt": now(), "inputHash": input_hash, "idempotencyKey": key, "events": [], "issues": [], "artifacts": []}
            atomic_json(directory / "state.json", state)
            return state

    def verify_inputs(self, job_id):
        directory = self.path(job_id)
        manifest = load_json(directory / "input" / "manifest.json")
        for name, field in (("snapshot.json", "snapshotSha256"), ("geometry.step", "geometrySha256")):
            if digest((directory / "input" / name).read_bytes()) != manifest[field]:
                raise ValueError("Frozen input changed after submission: " + name)

    def register_artifacts(self, job_id):
        directory = self.path(job_id)
        artifacts = []
        index = {}
        allowed = {".prt", ".fem", ".sim", ".bun", ".vtu", ".glb", ".json", ".csv", ".log", ".txt"}
        for subdir in ("nx", "results", "raw", "logs", "input", "stages"):
            for path in sorted((directory / subdir).rglob("*")):
                if path.is_file() and not path.is_symlink() and (path.suffix.lower() in allowed or path.name == "MSGF"):
                    relative = path.relative_to(directory).as_posix()
                    name = path.name if path.name not in index else relative.replace("/", "__")
                    index[name] = relative
                    artifacts.append({"name": name, "size": path.stat().st_size, "sha256": digest(path.read_bytes())})
        atomic_json(directory / "artifact-index.json", index)
        self.update(job_id, artifacts=artifacts)
        return artifacts

    def artifact(self, job_id, name):
        state = self.get(job_id)
        if name not in {row["name"] for row in state["artifacts"]}:
            raise KeyError(name)
        directory = self.path(job_id)
        index = load_json(directory / "artifact-index.json")
        if name not in index:
            raise KeyError(name)
        path = (directory / index[name]).resolve()
        if not path.is_relative_to(directory) or path.is_symlink() or not path.is_file():
            raise KeyError(name)
        return path

    def recover(self):
        for state in self.list():
            if state["status"] not in TERMINAL | {"queued", "waiting_connection"}:
                self.update(state["id"], status="failed", stage="interrupted", message="Worker restarted during execution; recorded processes and native files retained for recovery", recoveryRequired=True)
                self.register_artifacts(state["id"])
