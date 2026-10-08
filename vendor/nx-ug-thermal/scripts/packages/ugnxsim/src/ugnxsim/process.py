from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Sequence


class ProcessFailure(RuntimeError):
    def __init__(self, message: str, *, result: subprocess.CompletedProcess[str], log_path: Path):
        super().__init__(message)
        self.result = result
        self.log_path = log_path


def run_process(
    command: Sequence[str],
    *,
    cwd: Path,
    log_path: Path,
    env: dict[str, str] | None = None,
    timeout: int = 7200,
    encoding: str = "utf-8",
) -> subprocess.CompletedProcess[str]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(
            [str(value) for value in command],
            cwd=cwd,
            env=env or os.environ.copy(),
            capture_output=True,
            text=True,
            encoding=encoding,
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        log_path.write_text(output, encoding="utf-8")
        raise TimeoutError(f"Process timed out after {timeout}s; see {log_path}") from exc
    log_path.write_text((completed.stdout or "") + (completed.stderr or ""), encoding="utf-8")
    return completed


def parse_json_stdout(stdout: str) -> dict[str, Any]:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        raise ValueError("Process returned no JSON result on stdout")
    for line in reversed(lines):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("Process stdout contains no JSON object")
