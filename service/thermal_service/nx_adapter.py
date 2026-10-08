from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx


class NXAdapter:
    """NX protocol boundary. Never substitutes analytic/example temperatures."""

    def __init__(self, url=None, token=None):
        self.url = (url if url is not None else os.environ.get("THERMAL_NX_WORKER_URL", "")).rstrip("/")
        self.token = token if token is not None else os.environ.get("THERMAL_NX_WORKER_TOKEN", "")
        if self.url and urlparse(self.url).scheme not in {"http", "https"}:
            raise ValueError("THERMAL_NX_WORKER_URL must use HTTP(S)")
        self.health_cache = {"configured": bool(self.url), "connected": False,
                             "message": "尚未配置 NX 执行器，可继续编辑；配置后自动调度。" if not self.url else "等待连接检查。"}

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def client(self, timeout=20):
        return httpx.Client(base_url=self.url, headers=self.headers, timeout=timeout, follow_redirects=False, trust_env=False)

    def health(self):
        if not self.url:
            return self.health_cache
        try:
            with self.client(timeout=5) as client:
                response = client.get("/health")
                response.raise_for_status()
                data = response.json()
            connected = bool(data.get("connected", data.get("configured", False)))
            self.health_cache = {"configured": True, "connected": connected,
                                 "message": "NX 执行器已连接。" if connected else "执行器可达，NX 环境尚未准备完成。"}
        except (httpx.HTTPError, ValueError):
            self.health_cache = {"configured": True, "connected": False, "message": "暂时无法连接 NX 执行器；队列保留，项目仍可编辑。"}
        return self.health_cache

    def submit(self, snapshot, geometry: Path):
        with self.client(timeout=120) as client, geometry.open("rb") as fp:
            response = client.post("/jobs", data={"snapshot": json.dumps(snapshot, ensure_ascii=False), "idempotencyKey": snapshot["run"]["id"],
                                                 "sha256": snapshot["geometry"]["sha256"]},
                                   files={"geometry": ("geometry.step", fp, "application/step")})
            response.raise_for_status()
            return response.json()

    def poll(self, job):
        with self.client() as client:
            response = client.get(f"/jobs/{quote(job, safe='')}")
            response.raise_for_status()
            return response.json()

    def cancel(self, job):
        with self.client() as client:
            response = client.post(f"/jobs/{quote(job, safe='')}/cancel", json={"confirm": True})
            response.raise_for_status()
            return response.json()

    def download(self, job, name, path, max_size=1024 * 1024 * 1024):
        size = 0
        partial = path.with_suffix(path.suffix + ".part")
        try:
            with self.client(timeout=120) as client, client.stream("GET", f"/jobs/{quote(job, safe='')}/artifacts/{quote(name, safe='')}") as response:
                response.raise_for_status()
                with partial.open("wb") as fp:
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > max_size:
                            raise ValueError("NX artifact exceeds allowed size")
                        fp.write(chunk)
            partial.replace(path)
        finally:
            partial.unlink(missing_ok=True)
