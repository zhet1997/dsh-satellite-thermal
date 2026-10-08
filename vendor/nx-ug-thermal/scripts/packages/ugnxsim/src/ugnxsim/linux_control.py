from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import secrets
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from . import __version__
from .deployment import (
    UPLOAD_TARGET_PREFIX,
    load_remote_target,
    require_run_id,
    skill_root,
    windows_download_bundle,
    windows_run_root,
)
from .io import file_record, read_json, sha256_file, write_json
from .thermal_data import build_thermal_case


INPUT_FILES = (
    "thermal-inputs.json", "geometry-semantics.json", "source-manifest.json",
    "assumptions.json", "components.csv", "contacts.csv", "environment.csv",
)
RETURN_FILES = (
    "native.vtu", "summary.json", "energy_balance.json", "run-manifest.json", "solver-log-summary.txt",
)
WINDOWS_STAGES = ("preflight", "model", "solve", "extract", "finalize")
STAGE_TIMEOUTS = {"preflight": 120, "model": 1800, "solve": 7800, "extract": 1800, "finalize": 120}
MAX_MCP_TRANSFER_BYTES = 10 * 1024 * 1024 * 1024


def _new_run_id() -> str:
    return "thermal_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S_") + secrets.token_hex(4)


def _zip_write_file(archive: zipfile.ZipFile, source: Path, relative: str) -> dict[str, Any]:
    archive.write(source, relative)
    return {"path": relative, "size_bytes": source.stat().st_size, "sha256": sha256_file(source)}


def _bytes_record(relative: str, data: bytes) -> dict[str, Any]:
    return {"path": relative, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _safe_archive_path(value: object) -> str:
    path = PurePosixPath(str(value))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"Unsafe execution-package path: {value!r}")
    return path.as_posix()


def _verify_prepared_sources(workspace: Path, source_manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records = source_manifest.get("files")
    if not isinstance(records, list) or not records:
        raise ValueError("source-manifest.json contains no source files")
    verified: dict[str, dict[str, Any]] = {}
    for row in records:
        if not isinstance(row, dict):
            raise ValueError("source-manifest.json contains a non-object record")
        relative = _safe_archive_path(row.get("path"))
        source = (workspace / Path(relative)).resolve()
        try:
            source.relative_to(workspace)
        except ValueError as exc:
            raise ValueError(f"Source manifest path escapes workspace: {relative}") from exc
        if not source.is_file():
            raise FileNotFoundError(f"Prepared source is missing: {source}")
        actual = file_record(source, root=workspace)
        if actual["size_bytes"] != int(row.get("size_bytes", -1)) or actual["sha256"] != row.get("sha256"):
            raise RuntimeError(f"Prepared source changed after normalization: {relative}")
        verified[relative] = row
    return verified


def package_run(workspace: Path, *, target_config: Path | None = None) -> dict[str, Any]:
    workspace = workspace.resolve()
    inputs_dir = workspace / "02_sim" / "thermal" / "inputs"
    for name in INPUT_FILES:
        path = inputs_dir / name
        if not path.is_file() or path.stat().st_size <= 0:
            raise FileNotFoundError(f"Prepared thermal input is missing or empty: {path}")
    target = load_remote_target(target_config)
    run_id = _new_run_id()
    require_run_id(run_id)
    run_dir = workspace / "02_sim" / "thermal" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    for name in ("logs", "state", "returned"):
        (run_dir / name).mkdir()

    semantics = read_json(inputs_dir / "geometry-semantics.json")
    thermal_inputs = read_json(inputs_dir / "thermal-inputs.json")
    thermal_case = build_thermal_case(thermal_inputs, semantics)
    source_manifest = read_json(inputs_dir / "source-manifest.json")
    verified_sources = _verify_prepared_sources(workspace, source_manifest)
    bodies = semantics.get("bodies")
    if not isinstance(bodies, list) or not bodies:
        raise ValueError("geometry-semantics.json contains no bodies")
    geometry_source = semantics.get("geometry_source")
    if not isinstance(geometry_source, dict) or geometry_source.get("mode") != "simplified_assembly":
        raise ValueError("geometry-semantics.json must reference a simplified assembly STEP")
    windows_dir = skill_root() / "scripts" / "windows"
    if not windows_dir.is_dir():
        raise FileNotFoundError(f"Windows execution bundle is missing: {windows_dir}")
    geometry_manifest = {
        "schema_version": "nx-ug-thermal-geometry-manifest/1",
        "geometry_source": {
            "mode": geometry_source["mode"],
            "sha256": geometry_source["sha256"],
            "step_file": geometry_source["step_file"],
        },
        "bodies": [
            {"business_id": body["business_id"], "sha256": body["geometry_hash"], "step_file": body["step_file"]}
            for body in bodies
        ],
    }
    clean_semantics = json.loads(json.dumps(semantics))
    clean_semantics["geometry_source"].pop("source_step", None)
    for body in clean_semantics["bodies"]:
        body.pop("source_step", None)
    run_manifest: dict[str, Any] = {
        "schema_version": "nx-ug-thermal-run/1",
        "run_id": run_id,
        "package_version": __version__,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "workspace": str(workspace),
        "mcp_server": target["mcp_server"],
        "workspace_dir": target["workspace_dir"],
        "download_dir": target["download_dir"],
        "run_journal": target["run_journal"],
        "windows_run_root": str(windows_run_root(target, run_id)),
        "stages": list(WINDOWS_STAGES),
        "timeouts_seconds": STAGE_TIMEOUTS,
        "required_returns": list(RETURN_FILES),
    }
    entries: list[dict[str, Any]] = []
    payload = run_dir / "payload.zip"
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in INPUT_FILES:
            source = inputs_dir / name
            relative = f"inputs/{name}"
            if name == "geometry-semantics.json":
                data = (json.dumps(clean_semantics, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
                archive.writestr(relative, data)
                entries.append(_bytes_record(relative, data))
            else:
                entries.append(_zip_write_file(archive, source, relative))
        thermal_case_data = (json.dumps(thermal_case, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        archive.writestr("inputs/thermal-case.json", thermal_case_data)
        entries.append(_bytes_record("inputs/thermal-case.json", thermal_case_data))
        relative = _safe_archive_path(geometry_source["step_file"])
        if not relative.startswith("geometry/"):
            raise ValueError(f"Geometry must be packaged below geometry/: {relative}")
        source = Path(str(geometry_source["source_step"])).resolve()
        try:
            source_relative = source.relative_to(workspace).as_posix()
        except ValueError as exc:
            raise ValueError(f"Geometry source escapes workspace: {source}") from exc
        record = verified_sources.get(source_relative)
        if record is None or record.get("sha256") != geometry_source.get("sha256"):
            raise ValueError(f"Geometry source is not frozen in source-manifest.json: {source_relative}")
        entries.append(_zip_write_file(archive, source, relative))
        geometry_bytes = (json.dumps(geometry_manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        archive.writestr("cad/geometry_manifest.json", geometry_bytes)
        entries.append(_bytes_record("cad/geometry_manifest.json", geometry_bytes))
        for source in sorted(windows_dir.rglob("*")):
            if source.is_file() and "__pycache__" not in source.parts and source.suffix != ".pyc":
                entries.append(_zip_write_file(archive, source, source.relative_to(windows_dir).as_posix()))
        run_manifest["payload_entries"] = entries
        archive.writestr("run-manifest.json", (json.dumps(run_manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    run_manifest["payload"] = file_record(payload, root=run_dir)
    manifest_path = run_dir / "run-manifest.json"
    write_json(manifest_path, run_manifest)
    transfer_bundle = run_dir / "transfer.zip"
    with zipfile.ZipFile(transfer_bundle, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.write(payload, "payload.zip")
        archive.write(manifest_path, "run-manifest.json")
    write_json(run_dir / "state" / "linux.json", {
        "schema_version": "nx-ug-thermal-linux-state/1", "run_id": run_id, "status": "packaged",
    })
    return {
        "ok": True,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "payload": run_manifest["payload"],
        "transfer_bundle": file_record(transfer_bundle, root=run_dir),
    }


def push_run(run_dir: Path, *, target_config: Path | None = None) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    manifest = read_json(run_dir / "run-manifest.json")
    run_id = require_run_id(str(manifest["run_id"]))
    target = load_remote_target(target_config)
    if manifest.get("mcp_server") != target["mcp_server"]:
        raise ValueError("Run manifest MCP server does not match the configured target")
    transfer_bundle = run_dir / "transfer.zip"
    if not transfer_bundle.is_file() or transfer_bundle.stat().st_size <= 0:
        raise FileNotFoundError(f"Transfer bundle is missing or empty: {transfer_bundle}")
    if transfer_bundle.stat().st_size > MAX_MCP_TRANSFER_BYTES:
        raise ValueError("Transfer bundle exceeds the MCP 10 GiB upload limit")
    transfer_record = file_record(transfer_bundle)
    upload_target = f"{UPLOAD_TARGET_PREFIX}/{run_id}/transfer.zip"
    windows_root = str(windows_run_root(target, run_id)).replace("'", "''")
    bootstrap = (
        "$u='__COMPLETED_UPLOAD_TARGET_PATH__';"
        f"if((Get-FileHash -Algorithm SHA256 -LiteralPath $u).Hash.ToLowerInvariant() -ne '{transfer_record['sha256']}')"
        "{throw 'uploaded transfer bundle hash mismatch'};"
        f"$r='{windows_root}';if(Test-Path -LiteralPath $r){{throw 'run directory already exists'}};"
        "New-Item -ItemType Directory -Path $r -Force|Out-Null;Expand-Archive -LiteralPath $u -DestinationPath $r;"
        "$m=[IO.File]::ReadAllText(($r+'\\run-manifest.json'),[Text.Encoding]::UTF8)|ConvertFrom-Json;"
        "$p=$r+'\\payload.zip';$h=(Get-FileHash -Algorithm SHA256 -LiteralPath $p).Hash.ToLowerInvariant();"
        "if($h -ne $m.payload.sha256 -or (Get-Item -LiteralPath $p).Length -ne [int64]$m.payload.size_bytes)"
        "{throw 'uploaded payload verification failed'};"
        "$d=$r+'\\package';if(Test-Path -LiteralPath $d){throw 'package directory already exists'};"
        "Expand-Archive -LiteralPath $p -DestinationPath $d;"
        "& ($d+'\\Start-NxStage.ps1') -RunRoot $r -Stage preflight"
    )
    write_json(run_dir / "state" / "linux.json", {
        "schema_version": "nx-ug-thermal-linux-state/1", "run_id": run_id,
        "status": "upload_requested", "upload_target": upload_target,
    })
    return {
        "ok": True, "ready": False, "action": "create_upload",
        "run_id": run_id,
        "mcp_server": target["mcp_server"],
        "tool": "create_upload",
        "arguments": {
            "sourcePath": str(transfer_bundle),
            "targetPath": upload_target,
            "fileName": transfer_bundle.name,
            "sizeBytes": transfer_record["size_bytes"],
            "sha256": transfer_record["sha256"],
            "contentType": "application/zip",
            "ttlSeconds": 900,
        },
        "next": {
            "put_file": str(transfer_bundle),
            "complete_tool": "complete_upload",
            "powershell_template": bootstrap,
            "path_placeholder": "__COMPLETED_UPLOAD_TARGET_PATH__",
        },
    }


def status_run(run_dir: Path, *, target_config: Path | None = None) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    manifest = read_json(run_dir / "run-manifest.json")
    run_id = require_run_id(str(manifest["run_id"]))
    target = load_remote_target(target_config)
    state_path = windows_run_root(target, run_id) / "state" / "run-state.json"
    escaped = str(state_path).replace("'", "''")
    script = (
        f"$p='{escaped}';if(Test-Path -LiteralPath $p)"
        "{$s=[IO.File]::ReadAllText($p,[Text.Encoding]::UTF8)|ConvertFrom-Json;"
        "$alive=$false;if($s.status -eq 'running' -and $s.pid){$alive=$null -ne (Get-Process -Id ([int]$s.pid) -ErrorAction SilentlyContinue)};"
        "[ordered]@{run_id=$s.run_id;status=$s.status;current_stage=$s.current_stage;pid=$s.pid;pid_alive=$alive;"
        "updated_at=$s.updated_at;stages=$s.stages}|ConvertTo-Json -Depth 40 -Compress}"
        f"else{{[ordered]@{{run_id='{run_id}';status='not_started'}}|ConvertTo-Json -Compress}}"
    )
    return {
        "ok": True, "run_id": run_id, "local_state": read_json(run_dir / "state" / "linux.json"),
        "mcp_server": target["mcp_server"], "powershell": script,
    }


def pull_run(run_dir: Path, *, target_config: Path | None = None) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    manifest = read_json(run_dir / "run-manifest.json")
    run_id = require_run_id(str(manifest["run_id"]))
    target = load_remote_target(target_config)
    bundle_name = f"{run_id}-return.zip"
    remote_bundle = windows_download_bundle(target, run_id)
    local_bundle = run_dir / "return-bundle.zip"
    if not local_bundle.is_file():
        return {
            "ok": True, "ready": False, "action": "create_download", "run_id": run_id,
            "mcp_server": target["mcp_server"], "tool": "create_download",
            "arguments": {
                "sourcePath": str(remote_bundle), "fileName": bundle_name, "ttlSeconds": 900,
            },
            "download_to": str(local_bundle),
        }
    returned = run_dir / "returned"
    with tempfile.TemporaryDirectory(prefix="nxcase_pull_", dir=run_dir) as temporary:
        temporary_path = Path(temporary)
        with zipfile.ZipFile(local_bundle) as archive:
            names = archive.namelist()
            expected = {*RETURN_FILES, "return-manifest.json"}
            if len(names) != len(expected) or set(names) != expected:
                raise ValueError(f"Return bundle must contain exactly {sorted(expected)}")
            archive.extractall(temporary_path)
        return_manifest = read_json(temporary_path / "return-manifest.json")
        records = return_manifest.get("files")
        if not isinstance(records, list):
            raise ValueError("Remote return-manifest.json has no files list")
        by_name = {str(row.get("path")): row for row in records if isinstance(row, dict)}
        if set(by_name) != set(RETURN_FILES):
            raise ValueError(f"Remote return manifest must contain exactly {list(RETURN_FILES)}")
        for name in RETURN_FILES:
            path = temporary_path / name
            record = by_name[name]
            if path.stat().st_size != int(record["size_bytes"]) or sha256_file(path) != record["sha256"]:
                raise RuntimeError(f"Returned artifact failed verification: {name}")
        for name in RETURN_FILES:
            os.replace(temporary_path / name, returned / name)
        os.replace(temporary_path / "return-manifest.json", returned / "return-manifest.json")
    write_json(run_dir / "state" / "linux.json", {
        "schema_version": "nx-ug-thermal-linux-state/1", "run_id": run_id, "status": "pulled",
    })
    return {
        "ok": True, "ready": True, "run_id": run_id,
        "bundle_sha256": sha256_file(local_bundle),
        "returned": [str(returned / name) for name in RETURN_FILES],
    }


def publish_run(run_dir: Path, workspace: Path | None = None) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    manifest = read_json(run_dir / "run-manifest.json")
    run_id = require_run_id(str(manifest["run_id"]))
    workspace = (workspace or Path(str(manifest["workspace"]))).resolve()
    output = workspace / "02_sim" / "simulation"
    output.mkdir(parents=True, exist_ok=True)
    published = []
    for name in RETURN_FILES:
        source = run_dir / "returned" / name
        if not source.is_file() or source.stat().st_size <= 0:
            raise FileNotFoundError(f"Required returned artifact is missing or empty: {source}")
        target = output / name
        temporary = target.with_suffix(target.suffix + ".tmp")
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
        published.append(file_record(target, root=workspace))
    write_json(run_dir / "state" / "linux.json", {
        "schema_version": "nx-ug-thermal-linux-state/1", "run_id": run_id, "status": "published",
    })
    return {"ok": True, "run_id": run_id, "output_dir": str(output), "files": published}
