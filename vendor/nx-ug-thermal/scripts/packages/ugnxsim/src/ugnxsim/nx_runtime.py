from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
from typing import Any

from .io import read_json, require_ascii_path, write_json
from .process import run_process


def windows_file_version(path: Path) -> str | None:
    if os.name != "nt" or not path.is_file():
        return None

    class VSFixedFileInfo(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_uint32)
            for name in (
                "signature",
                "struct_version",
                "file_version_ms",
                "file_version_ls",
                "product_version_ms",
                "product_version_ls",
                "file_flags_mask",
                "file_flags",
                "file_os",
                "file_type",
                "file_subtype",
                "file_date_ms",
                "file_date_ls",
            )
        ]

    size = ctypes.windll.version.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return None
    buffer = ctypes.create_string_buffer(size)
    if not ctypes.windll.version.GetFileVersionInfoW(str(path), 0, size, buffer):
        return None
    value = ctypes.c_void_p()
    length = ctypes.c_uint()
    if not ctypes.windll.version.VerQueryValueW(buffer, "\\", ctypes.byref(value), ctypes.byref(length)):
        return None
    info = ctypes.cast(value, ctypes.POINTER(VSFixedFileInfo)).contents
    return ".".join(
        str(number)
        for number in (
            info.product_version_ms >> 16,
            info.product_version_ms & 0xFFFF,
            info.product_version_ls >> 16,
            info.product_version_ls & 0xFFFF,
        )
    )


def run_nx_journal(
    *,
    nx_base: Path,
    journal_name: str,
    task: dict[str, Any],
    run_dir: Path,
    timeout: int,
    env_extra: dict[str, str] | None = None,
) -> dict[str, Any]:
    require_ascii_path(run_dir)
    journal = Path(__file__).resolve().parent / "nx_journals" / f"{journal_name}.py"
    executable = nx_base / "NXBIN" / "run_journal.exe"
    if not journal.is_file():
        raise FileNotFoundError(f"NX journal is missing: {journal}")
    if not executable.is_file():
        raise FileNotFoundError(f"NX run_journal is missing: {executable}")
    task_path = run_dir / "tmp" / f"{journal_name}.task.json"
    result_path = run_dir / "tmp" / f"{journal_name}.result.json"
    log_path = run_dir / "logs" / f"{journal_name}.log"
    write_json(task_path, {**task, "result_path": str(result_path)})
    env = os.environ.copy()
    env["UGII_BASE_DIR"] = str(nx_base)
    env["UGII_ROOT_DIR"] = str(nx_base / "NXBIN")
    env["UGII_TMP_DIR"] = str(run_dir / "tmp")
    env["UGII_OCC_PARTIAL_LOADING"] = "FALSE"
    env["PATH"] = str(nx_base / "NXBIN") + os.pathsep + env.get("PATH", "")
    env["UGNXSIM_TASK"] = str(task_path)
    if env_extra:
        env.update(env_extra)
    completed = run_process(
        [str(executable), str(journal)],
        cwd=run_dir,
        log_path=log_path,
        env=env,
        timeout=timeout,
        encoding="gbk" if os.name == "nt" else "utf-8",
    )
    if completed.returncode != 0:
        raise RuntimeError(f"NX journal {journal_name!r} exited with {completed.returncode}; see {log_path}")
    if not result_path.is_file():
        raise RuntimeError(f"NX journal {journal_name!r} produced no result JSON; see {log_path}")
    result = read_json(result_path)
    if not result.get("ok"):
        raise RuntimeError(f"NX journal {journal_name!r} failed: {result.get('error')}; see {log_path}")
    return result


def write_journal_result(task_path: str, result: dict[str, Any]) -> None:
    with open(task_path, "r", encoding="utf-8-sig") as stream:
        task = json.load(stream)
    with open(task["result_path"], "w", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
