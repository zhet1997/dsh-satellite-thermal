"""Isolate native CAD calls that can hold the GIL from the HTTP service."""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import threading
from pathlib import Path

from .storage import DomainError, atomic_json


class GeometryProcesses:
    def __init__(self, timeout_seconds=None):
        self.timeout_seconds = float(timeout_seconds if timeout_seconds is not None
                                     else os.environ.get("THERMAL_GEOMETRY_TIMEOUT_SECONDS", "300"))
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("THERMAL_GEOMETRY_TIMEOUT_SECONDS must be a finite positive number")
        self._lock = threading.Lock()
        self._children = set()
        self._closed = False

    def _command(self, job, output):
        return [sys.executable, "-m", "thermal_service.geometry_worker", str(job), str(output)]

    @staticmethod
    def _kill(process):
        if process.poll() is None:
            process.kill()
        # Reap only this manager's child; never target unrelated services.
        process.wait(timeout=5)

    def close(self):
        with self._lock:
            self._closed = True
            children = list(self._children)
        for process in children:
            self._kill(process)

    def run(self, upload, directory, *, example=False):
        directory = Path(directory).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        job, output = directory / "geometry-job.json", directory / "geometry-result.json"
        atomic_json(job, {"upload": str(Path(upload).resolve()) if upload else None,
                          "directory": str(directory), "example": example})
        # Paths are unique per operation; an old result can never satisfy a new
        # request. Files also preserve diagnostics after a timeout or native crash.
        output.unlink(missing_ok=True)
        with (directory / "geometry-worker.log").open("wb") as log:
            with self._lock:
                if self._closed:
                    raise DomainError("服务正在关闭，导入已中断，原文件已保留。", "geometry_interrupted", 503)
                process = subprocess.Popen(self._command(job, output),
                    cwd=str(Path(__file__).resolve().parents[1]), stdin=subprocess.DEVNULL,
                    stdout=log, stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                self._children.add(process)
            try:
                try:
                    process.wait(timeout=self.timeout_seconds)
                except subprocess.TimeoutExpired as exc:
                    self._kill(process)
                    raise DomainError(
                        f"几何导入超过 {self.timeout_seconds:g} 秒，已停止本次导入，原文件已保留。可重试或简化模型；其他项目可继续使用。",
                        "geometry_timeout", 504) from exc
                with self._lock:
                    if self._closed:
                        raise DomainError("服务关闭中断了导入，原文件已保留，请重新导入。", "geometry_interrupted", 503)
                if process.returncode != 0 or not output.is_file():
                    raise DomainError("几何计算进程异常退出，原文件已保留，请重试并检查服务日志。", "geometry_worker_failed", 500)
                result = json.loads(output.read_text(encoding="utf-8"))
                if not result.get("ok"):
                    error = result.get("error", {})
                    raise DomainError(error.get("detail", "几何导入失败，原文件已保留。"),
                                      error.get("code", "geometry_failed"), error.get("status", 422))
                generated = result["generated"]
                for key in ("stepPath", "previewPath"):
                    path = Path(generated[key]).resolve()
                    if not path.is_relative_to(directory) or not path.is_file():
                        raise DomainError("几何计算返回的文件无效，原文件已保留。", "geometry_worker_failed", 500)
                    generated[key] = path
                return generated
            finally:
                if process.poll() is None:
                    self._kill(process)
                with self._lock:
                    self._children.discard(process)
