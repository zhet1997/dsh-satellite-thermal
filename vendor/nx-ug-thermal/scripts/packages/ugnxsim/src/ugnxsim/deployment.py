from __future__ import annotations

import os
import re
from pathlib import Path, PureWindowsPath
from typing import Any

from .io import read_json


WINDOWS_WORKSPACE_SCHEMA = "nx-ug-thermal-workspace/1"
DEFAULT_WINDOWS_WORKSPACE_CONFIG = Path(
    r"C:\ProgramData\PowerShellMcp\config\nx-ug-thermal-workspace.json"
)
RUN_ID_PATTERN = re.compile(r"^thermal_[0-9]{8}_[0-9]{6}_[a-f0-9]{8}$")
MCP_SERVER = "windows-powershell"
UPLOAD_TARGET_PREFIX = "nx-ug-thermal"


def skill_root() -> Path:
    configured = os.environ.get("UGNXSIM_SKILL_ROOT")
    if configured:
        return Path(configured).resolve()
    return Path(__file__).resolve().parents[5]


def default_remote_target_config() -> Path:
    configured = os.environ.get("NX_UG_CONFIG_FILE") or os.environ.get("CONFIG_FILE")
    if configured:
        return Path(configured).resolve()
    return skill_root().parents[2] / "config.json"


def _required_string(parent: dict[str, Any], field: str, label: str) -> str:
    value = parent.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}.{field} must be a non-empty string")
    return value.strip()


def load_remote_target(path: Path | None = None) -> dict[str, Any]:
    config_path = (path or default_remote_target_config()).resolve()
    app_config = read_json(config_path)
    dsh = app_config.get("dsh")
    if not isinstance(dsh, dict) or not isinstance(dsh.get("mcpServers"), dict):
        raise ValueError(f"Application config has no dsh.mcpServers object: {config_path}")
    server = dsh["mcpServers"].get(MCP_SERVER)
    if not isinstance(server, dict):
        raise ValueError(f"Configured MCP server is missing: {MCP_SERVER!r}")
    value = server.get("remoteTarget")
    if not isinstance(value, dict):
        raise ValueError(f"dsh.mcpServers.{MCP_SERVER}.remoteTarget must be an object")
    allowed_fields = {"workspace_dir", "download_dir", "run_journal"}
    if set(value) != allowed_fields:
        raise ValueError(f"Remote target must contain only {sorted(allowed_fields)}")
    workspace_dir = PureWindowsPath(_required_string(value, "workspace_dir", "remote target"))
    download_dir = PureWindowsPath(_required_string(value, "download_dir", "remote target"))
    run_journal = PureWindowsPath(_required_string(value, "run_journal", "remote target"))
    if not workspace_dir.is_absolute():
        raise ValueError("remote target workspace_dir must be an absolute Windows path")
    if not download_dir.is_absolute():
        raise ValueError("remote target download_dir must be an absolute Windows path")
    if not run_journal.is_absolute() or run_journal.name.lower() != "run_journal.exe":
        raise ValueError("remote target run_journal must be an absolute path to run_journal.exe")
    return {
        "mcp_server": MCP_SERVER,
        "workspace_dir": str(workspace_dir),
        "download_dir": str(download_dir),
        "run_journal": str(run_journal),
        "config_path": str(config_path),
    }


def require_run_id(run_id: str) -> str:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError(f"Invalid NX thermal run id: {run_id!r}")
    return run_id


def windows_run_root(target: dict[str, Any], run_id: str) -> PureWindowsPath:
    require_run_id(run_id)
    return PureWindowsPath(str(target["workspace_dir"])) / "runs" / run_id


def windows_download_bundle(target: dict[str, Any], run_id: str) -> PureWindowsPath:
    require_run_id(run_id)
    return PureWindowsPath(str(target["download_dir"])) / f"{run_id}-return.zip"


def load_windows_workspace(path: Path) -> dict[str, Any]:
    """Load the retained local workspace contract used by legacy FOV/structural flows."""
    config_path = path.resolve()
    value = read_json(config_path)
    if value.get("schema_version") != WINDOWS_WORKSPACE_SCHEMA:
        raise ValueError(f"Unsupported Windows workspace schema in {config_path}: {value.get('schema_version')!r}")
    result: dict[str, Any] = {"schema_version": WINDOWS_WORKSPACE_SCHEMA, "config_path": str(config_path)}
    for field in ("workspace_dir", "nx_base", "runs_root"):
        raw = value.get(field)
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"{config_path} must define a non-empty {field!r}")
        configured = Path(raw.strip())
        if not configured.is_absolute():
            raise ValueError(f"{field} must be an absolute Windows path: {raw!r}")
        result[field] = str(configured.resolve())
    workspace_dir = Path(result["workspace_dir"])
    nx_base = Path(result["nx_base"])
    runs_root = Path(result["runs_root"])
    if not workspace_dir.is_dir():
        raise FileNotFoundError(f"Windows workspace directory is missing: {workspace_dir}")
    if not nx_base.is_dir():
        raise FileNotFoundError(f"NX base directory is missing: {nx_base}")
    runs_root.mkdir(parents=True, exist_ok=True)
    return result


def resolve_workspace_path(path: Path, workspace_dir: Path | None) -> Path:
    if path.is_absolute() or workspace_dir is None:
        return path.resolve()
    resolved_workspace = workspace_dir.resolve()
    resolved = (resolved_workspace / path).resolve()
    try:
        resolved.relative_to(resolved_workspace)
    except ValueError as exc:
        raise ValueError(f"Relative path escapes the configured Windows workspace: {path}") from exc
    return resolved
