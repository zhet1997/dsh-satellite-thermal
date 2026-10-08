from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .case import AGENT_STAGES, DOMAIN_STAGES, check_case, load_case
from .checkpoints import append_checkpoint, load_checkpoints, require_predecessor
from .io import copy_case_source, file_record, read_json, write_json
from .process import parse_json_stdout, run_process


def new_run_dir(runs_root: Path, case_id: str) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run_dir = runs_root / f"{case_id}_{stamp}"
    for name in ("logs", "nx", "reports", "results", "snapshots", "source_case", "source_inputs", "tmp"):
        (run_dir / name).mkdir(parents=True, exist_ok=False)
    return run_dir


def start_run(
    case_root: Path,
    source_run: Path,
    runs_root: Path,
    nx_base: Path,
    *,
    windows_workspace: dict[str, Any] | None = None,
) -> Path:
    checked = check_case(case_root, source_run)
    case = load_case(case_root)
    run_dir = new_run_dir(runs_root, str(case["id"]))
    # new_run_dir creates the placeholder to fail closed; replace it with the frozen tree.
    (run_dir / "source_case").rmdir()
    copy_case_source(case_root, run_dir / "source_case")
    inputs = []
    for relative in case.get("source_requirements", []):
        inputs.append(file_record(source_run / relative))
    manifest = {
        "schema_version": "ugnxsim-run/1",
        "package_version": __version__,
        "case_id": case["id"],
        "domain": case["domain"],
        "case_root": str(case_root.resolve()),
        "source_run": str(source_run.resolve()),
        "nx_base": str(nx_base.resolve()),
        "windows_workspace_config": windows_workspace.get("config_path") if windows_workspace else None,
        "windows_workspace_dir": windows_workspace.get("workspace_dir") if windows_workspace else None,
        "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_inputs": inputs,
        "case_check": checked,
    }
    write_json(run_dir / "run.json", manifest)
    write_json(run_dir / "reports" / "checkpoints.json", {"schema_version": "ugnxsim-checkpoints/1", "stages": []})
    return run_dir


def build_context(
    run_dir: Path,
    *,
    remote_workspace: str | None = None,
    remote_target_config: Path | None = None,
) -> dict[str, Any]:
    manifest = read_json(run_dir / "run.json")
    case_root = run_dir / "source_case"
    case = load_case(case_root)
    return {
        "schema_version": "ugnxsim-context/1",
        "run_dir": str(run_dir.resolve()),
        "case_root": str(case_root.resolve()),
        "source_run": manifest["source_run"],
        "nx_base": manifest["nx_base"],
        "windows_workspace_config": manifest.get("windows_workspace_config"),
        "windows_workspace_dir": manifest.get("windows_workspace_dir"),
        "remote_workspace": remote_workspace,
        "remote_target_config": str(remote_target_config.resolve()) if remote_target_config else None,
        "case": case,
    }


def _worker_environment() -> dict[str, str]:
    env = os.environ.copy()
    package_src = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = package_src + os.pathsep + env.get("PYTHONPATH", "")
    return env


def execute_stage(
    run_dir: Path,
    stage: str,
    runtime_python: Path | None = None,
    *,
    remote_workspace: str | None = None,
    remote_target_config: Path | None = None,
) -> dict[str, Any]:
    manifest = read_json(run_dir / "run.json")
    domain = str(manifest["domain"])
    stages = DOMAIN_STAGES[domain]
    if stage not in stages:
        raise ValueError(f"Unknown {domain} stage {stage!r}; expected one of {stages}")
    require_predecessor(run_dir, stages, stage)
    if any(row.get("stage") == stage for row in load_checkpoints(run_dir).get("stages", [])):
        raise RuntimeError(f"Stage {stage!r} already ran; repairs require a new run")
    context_path = run_dir / "tmp" / f"{stage}.context.json"
    context = build_context(
        run_dir,
        remote_workspace=remote_workspace,
        remote_target_config=remote_target_config,
    )
    write_json(context_path, context)
    python = runtime_python or Path(sys.executable)
    timeout = int(context["case"].get("timeouts", {}).get(stage, 7200))
    if stage in AGENT_STAGES[domain]:
        script = run_dir / "source_case" / "analysis" / f"{stage}.py"
        command = [str(python), "-m", "ugnxsim.stage_worker", "--script", str(script), "--context", str(context_path)]
    else:
        command = [
            str(python),
            "-m",
            "ugnxsim.domain_worker",
            "--domain",
            domain,
            "--stage",
            stage,
            "--context",
            str(context_path),
        ]
    completed = run_process(
        command,
        cwd=run_dir,
        log_path=run_dir / "logs" / f"{stage}.log",
        env=_worker_environment(),
        timeout=timeout,
    )
    try:
        result = parse_json_stdout(completed.stdout or "")
    except Exception as exc:
        raise RuntimeError(f"Stage {stage!r} returned no machine result; see {run_dir / 'logs' / (stage + '.log')}") from exc
    if completed.returncode != 0 or not result.get("ok"):
        raise RuntimeError(f"Stage {stage!r} failed: {result.get('error', 'unknown error')}; see {run_dir / 'logs' / (stage + '.log')}")
    append_checkpoint(run_dir, stage, result)
    return result
