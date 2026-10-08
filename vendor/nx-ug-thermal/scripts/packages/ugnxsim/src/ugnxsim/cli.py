from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .case import DOMAIN_STAGES, check_case
from .deployment import (
    DEFAULT_WINDOWS_WORKSPACE_CONFIG,
    load_windows_workspace,
    resolve_workspace_path,
)
from .doctor import doctor
from .io import read_json
from .linux_control import package_run, publish_run, pull_run, push_run, status_run
from .run import execute_stage, start_run
from .thermal_data import prepare_thermal_inputs


DEFAULT_NX_BASE = Path(r"D:\NX12")


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _workspace_config_path(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    if os.name != "nt":
        return None
    return DEFAULT_WINDOWS_WORKSPACE_CONFIG


def _workspace(explicit: Path | None) -> dict[str, object] | None:
    config_path = _workspace_config_path(explicit)
    return load_windows_workspace(config_path) if config_path else None


def _workspace_dir(workspace: dict[str, object] | None) -> Path | None:
    return Path(str(workspace["workspace_dir"])) if workspace else None


def _nx_base(value: Path | None, workspace: dict[str, object] | None) -> Path:
    if value is not None:
        return value.resolve()
    if workspace:
        return Path(str(workspace["nx_base"]))
    return DEFAULT_NX_BASE.resolve()


def _runs_root(value: Path | None, workspace: dict[str, object] | None) -> Path:
    if value is not None:
        return value.resolve()
    if workspace:
        return Path(str(workspace["runs_root"]))
    raise ValueError("--runs-root is required when no Windows workspace config is available")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="nxcase")
    commands = root.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare", help="normalize a raw-STEP steady thermal case on Linux")
    prepare.add_argument("--workspace", type=Path, required=True)
    prepare.add_argument("--component-json", type=Path)
    prepare.add_argument("--layout-json", type=Path)
    prepare.add_argument("--assembly-step", type=Path)
    prepare.add_argument("--overrides", type=Path)

    package = commands.add_parser("package", help="freeze a Windows NX12 execution package")
    package.add_argument("--workspace", type=Path, required=True)
    package.add_argument("--target-config", type=Path)

    for name in ("push", "pull"):
        command = commands.add_parser(name)
        command.add_argument("--run", type=Path, required=True)
        command.add_argument("--target-config", type=Path)

    publish = commands.add_parser("publish")
    publish.add_argument("--run", type=Path, required=True)
    publish.add_argument("--workspace", type=Path)

    probe = commands.add_parser("doctor")
    probe.add_argument("--domain", choices=("thermal", "structural", "fov", "all"), default="all")
    probe.add_argument("--nx-base", type=Path)
    probe.add_argument("--work-dir", type=Path)
    probe.add_argument("--workspace-config", type=Path)

    check = commands.add_parser("check")
    check.add_argument("--case", type=Path, required=True)
    check.add_argument("--source-run", type=Path, required=True)
    check.add_argument("--workspace-config", type=Path)

    start = commands.add_parser("start")
    start.add_argument("--case", type=Path, required=True)
    start.add_argument("--source-run", type=Path, required=True)
    start.add_argument("--runs-root", type=Path)
    start.add_argument("--nx-base", type=Path)
    start.add_argument("--workspace-config", type=Path)

    stage = commands.add_parser("stage")
    stage.add_argument("stage")
    stage.add_argument("--run", type=Path, required=True)
    stage.add_argument("--runtime-python", type=Path)
    stage.add_argument("--remote-workspace")
    stage.add_argument("--remote-target-config", type=Path)

    run = commands.add_parser("run")
    run.add_argument("--case", type=Path, required=True)
    run.add_argument("--source-run", type=Path, required=True)
    run.add_argument("--runs-root", type=Path)
    run.add_argument("--nx-base", type=Path)
    run.add_argument("--runtime-python", type=Path)
    run.add_argument("--workspace-config", type=Path)
    run.add_argument("--remote-workspace", required=True)
    run.add_argument("--remote-target-config", type=Path)

    status = commands.add_parser("status")
    status.add_argument("--run", type=Path, required=True)
    status.add_argument("--target-config", type=Path)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "prepare":
            value = prepare_thermal_inputs(
                args.workspace,
                component_path=args.component_json,
                layout_path=args.layout_json,
                assembly_step_path=args.assembly_step,
                overrides_path=args.overrides,
            )
        elif args.command == "package":
            value = package_run(args.workspace, target_config=args.target_config)
        elif args.command == "push":
            value = push_run(args.run, target_config=args.target_config)
        elif args.command == "pull":
            value = pull_run(args.run, target_config=args.target_config)
        elif args.command == "publish":
            value = publish_run(args.run, workspace=args.workspace)
        elif args.command == "doctor":
            workspace = _workspace(args.workspace_config)
            nx_base = _nx_base(args.nx_base, workspace)
            work_dir = args.work_dir.resolve() if args.work_dir else (_runs_root(None, workspace) if workspace else None)
            value = doctor(nx_base, args.domain, work_dir)
            if workspace:
                value["workspace_config"] = workspace
        elif args.command == "check":
            workspace = _workspace(args.workspace_config)
            workspace_dir = _workspace_dir(workspace)
            value = check_case(
                resolve_workspace_path(args.case, workspace_dir),
                resolve_workspace_path(args.source_run, workspace_dir),
            )
        elif args.command == "start":
            workspace = _workspace(args.workspace_config)
            workspace_dir = _workspace_dir(workspace)
            run_dir = start_run(
                resolve_workspace_path(args.case, workspace_dir),
                resolve_workspace_path(args.source_run, workspace_dir),
                _runs_root(args.runs_root, workspace),
                _nx_base(args.nx_base, workspace),
                windows_workspace=workspace,
            )
            value = {"ok": True, "run_dir": str(run_dir)}
        elif args.command == "stage":
            value = execute_stage(
                args.run.resolve(),
                args.stage,
                args.runtime_python,
                remote_workspace=args.remote_workspace,
                remote_target_config=args.remote_target_config,
            )
        elif args.command == "run":
            workspace = _workspace(args.workspace_config)
            workspace_dir = _workspace_dir(workspace)
            run_dir = start_run(
                resolve_workspace_path(args.case, workspace_dir),
                resolve_workspace_path(args.source_run, workspace_dir),
                _runs_root(args.runs_root, workspace),
                _nx_base(args.nx_base, workspace),
                windows_workspace=workspace,
            )
            manifest = read_json(run_dir / "run.json")
            results = {}
            for stage_name in DOMAIN_STAGES[manifest["domain"]]:
                results[stage_name] = execute_stage(
                    run_dir,
                    stage_name,
                    args.runtime_python,
                    remote_workspace=args.remote_workspace,
                    remote_target_config=args.remote_target_config,
                )
            value = {"ok": True, "run_dir": str(run_dir), "stages": results}
        else:
            run_dir = args.run.resolve()
            if (run_dir / "run-manifest.json").is_file():
                value = status_run(run_dir, target_config=args.target_config)
            else:
                value = {
                    "ok": True,
                    "run": read_json(run_dir / "run.json"),
                    "checkpoints": read_json(run_dir / "reports" / "checkpoints.json"),
                }
        _print(value)
        return 0 if not isinstance(value, dict) or value.get("ok", True) else 1
    except Exception as exc:
        _print({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
