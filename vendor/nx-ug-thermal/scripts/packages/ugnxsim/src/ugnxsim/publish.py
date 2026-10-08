from __future__ import annotations

import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

from .deployment import load_remote_target
from .io import sha256_file


NATIVE_VTU_RELATIVE_PATH = PurePosixPath("02_sim/simulation/native.vtu")


def _remote_workspace(value: object) -> PurePosixPath:
    raw = str(value or "").strip()
    if not raw or not raw.startswith("/") or any(character in raw for character in "\r\n\0"):
        raise ValueError("publish requires an absolute POSIX --remote-workspace path")
    workspace = PurePosixPath(raw)
    if ".." in workspace.parts:
        raise ValueError("remote workspace cannot contain '..'")
    return workspace


def publish_temperature_vtu(context: dict[str, Any]) -> dict[str, Any]:
    run_dir = Path(context["run_dir"])
    workspace_value = context.get("windows_workspace_dir")
    if not isinstance(workspace_value, str) or not workspace_value.strip():
        raise ValueError("publish requires windows_workspace_dir from the run's workspace config")
    workspace_dir = Path(workspace_value)
    if not workspace_dir.is_dir():
        raise FileNotFoundError(f"Configured Windows workspace is missing: {workspace_dir}")
    source = run_dir / "reports" / "native.vtu"
    if not source.is_file() or source.stat().st_size <= 0:
        raise FileNotFoundError(f"A non-empty summarized thermal VTU is required: {source}")

    target_config_path = Path(context["remote_target_config"]) if context.get("remote_target_config") else None
    target_config = load_remote_target(target_config_path)
    remote_workspace = _remote_workspace(context.get("remote_workspace"))
    remote_vtu = remote_workspace / NATIVE_VTU_RELATIVE_PATH

    windows_vtu = workspace_dir.joinpath(*NATIVE_VTU_RELATIVE_PATH.parts)
    windows_vtu.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, windows_vtu)
    if windows_vtu.stat().st_size <= 0:
        raise RuntimeError(f"Windows workspace VTU is empty after publish: {windows_vtu}")

    safe_run_name = re.sub(r"[^A-Za-z0-9._-]", "_", run_dir.name)
    download_root = Path(target_config["download_dir"])
    download_root.mkdir(parents=True, exist_ok=True)
    download_source = download_root / f"{safe_run_name}-native.vtu"
    if download_source.exists():
        raise FileExistsError(f"MCP download source already exists: {download_source}")
    shutil.copy2(source, download_source)
    return {
        "ok": True, "ready": False, "action": "create_download",
        "source_vtu": str(source),
        "windows_vtu": str(windows_vtu),
        "mcp_server": target_config["mcp_server"],
        "tool": "create_download",
        "arguments": {
            "sourcePath": str(download_source),
            "fileName": "native.vtu",
            "ttlSeconds": 900,
        },
        "download_to": str(remote_vtu),
        "size_bytes": download_source.stat().st_size,
        "sha256": sha256_file(download_source),
    }
