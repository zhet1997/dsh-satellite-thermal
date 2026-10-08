from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from ..io import write_json


def solve_nastran(run_dir: Path, nx_base: Path, timeout: int = 3600) -> dict[str, Any]:
    deck = run_dir / "nx" / "structural_case.bdf"
    if not deck.exists():
        raise FileNotFoundError(f"NX Nastran deck is missing: {deck}")
    for suffix in (".f04", ".f06", ".log", ".op2", ".xdb", ".pch", ".plt"):
        path = deck.with_suffix(suffix)
        if path.exists():
            path.unlink()
    scratch = run_dir / "tmp" / "nastran"
    scratch.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    executable = nx_base / "NXNASTRAN" / "bin" / "nastran.exe"
    if not executable.is_file():
        raise FileNotFoundError(f"NX Nastran executable is missing: {executable}")
    env["UGII_BASE_DIR"] = str(nx_base)
    command = [
        str(executable),
        str(deck),
        "old=no",
        "scr=yes",
        "sdirectory=%s" % scratch,
    ]
    completed = subprocess.run(
        command,
        cwd=deck.parent,
        env=env,
        capture_output=True,
        text=True,
        encoding="latin-1",
        errors="replace",
        timeout=timeout,
    )
    launcher_log = run_dir / "logs" / "nastran_launcher.log"
    launcher_log.write_text((completed.stdout or "") + (completed.stderr or ""), encoding="utf-8")
    f06 = deck.with_suffix(".f06")
    op2 = deck.with_suffix(".op2")
    fatal_lines: list[str] = []
    end_of_job = False
    if f06.exists():
        text = f06.read_text(encoding="latin-1", errors="replace")
        fatal_lines = [line.strip() for line in text.splitlines() if "FATAL" in line.upper()]
        end_of_job = "END OF JOB" in text.upper()
    result = {
        "ok": f06.exists() and op2.exists() and end_of_job and not fatal_lines,
        "returncode": completed.returncode,
        "deck": str(deck),
        "f06": str(f06),
        "op2": str(op2),
        "f06_exists": f06.exists(),
        "op2_exists": op2.exists(),
        "end_of_job": end_of_job,
        "fatal_lines": fatal_lines[:20],
    }
    write_json(run_dir / "nx" / "solve_report.json", result)
    if not result["ok"]:
        raise RuntimeError(f"NX Nastran solve failed; see {f06 if f06.exists() else launcher_log}")
    return result
