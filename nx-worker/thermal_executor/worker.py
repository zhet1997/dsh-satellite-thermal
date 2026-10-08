"""Single-license sequential execution; cancellation targets only captured process identities."""
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time

from .core import TERMINAL, atomic_json, digest, load_json, now

class Worker:
    def __init__(self, store, executable="", timeout=7200):
        self.store = store
        self.executable = str(executable)
        self.timeout = int(timeout)
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.thread = None
        self.active = None
        self.processes = {}
        self.guard = threading.RLock()

    def health(self):
        configured = bool(self.executable and Path(self.executable).is_file())
        return {"status": "ok", "version": "0.1.0", "configured": configured, "connected": configured,
                "message": "NX journal executable available; license and engineering validity checked during execution" if configured else "Configure NX_JOURNAL_EXECUTABLE on the NX workstation",
                "capabilities": {"nx": configured, "steadyState": True, "transient": False}, "activeJob": self.active}

    def start(self):
        self.store.recover()
        self.thread = threading.Thread(target=self.loop, name="nx-worker", daemon=True)
        self.thread.start()

    def close(self):
        # Do not kill active NX on web shutdown. Its state is recoverable and restart will not duplicate it.
        self.stop.set()
        self.wake.set()

    def cancel(self, job_id, confirmed):
        if confirmed is not True:
            raise ValueError("Cancellation requires confirm:true")
        with self.guard:
            state = self.store.get(job_id)
            if state["status"] in TERMINAL:
                return state
            if job_id != self.active:
                return self.store.update(job_id, status="cancelled", stage="cancelled", message="Queued job cancelled; no NX process started")
            state = self.store.update(job_id, status="cancel_requested", message="Cancellation requested; awaiting recorded process-tree termination")
            self.wake.set()
            return state

    def loop(self):
        while not self.stop.is_set():
            queued = [x for x in self.store.list() if x["status"] in {"queued", "waiting_connection"}]
            if not queued:
                self.wake.wait(1)
                self.wake.clear()
                continue
            job = min(queued, key=lambda x: x["createdAt"])
            if not self.health()["configured"]:
                if job["status"] != "waiting_connection":
                    self.store.update(job["id"], status="waiting_connection", stage="waiting_connection", message="NX journal executable is not configured")
                self.wake.wait(2)
                self.wake.clear()
                continue
            with self.guard:
                if self.store.get(job["id"])["status"] == "cancelled":
                    continue
                self.active = job["id"]
            try:
                self.execute(job["id"])
            except Exception as error:
                state = self.store.get(job["id"])
                detail = str(error).strip().splitlines()[-1] if str(error).strip() else type(error).__name__
                detail = detail.replace(str(self.store.path(job["id"])), "[job]")
                if self.executable:
                    detail = detail.replace(str(Path(self.executable).parent.parent), "[NX]")
                if state["status"] != "cancelled":
                    message = "NX %s 阶段失败：%s" % (state["stage"], detail[:1800])
                    self.store.update(job["id"], status="failed", message=message, issues=state["issues"] + [{"severity": "error", "message": message}])
                self.store.register_artifacts(job["id"])
            finally:
                with self.guard:
                    self.active = None

    def capture_processes(self, job_id, process):
        import psutil
        records = self.processes.setdefault(job_id, {})
        try:
            parent = psutil.Process(process.pid)
            for item in [parent] + parent.children(recursive=True):
                try:
                    records[item.pid] = item.create_time()
                except psutil.NoSuchProcess:
                    pass
        except psutil.NoSuchProcess:
            pass
        atomic_json(self.store.path(job_id) / "stages" / "processes.json", {"jobId": job_id, "processes": [{"pid": p, "created": c} for p, c in records.items()]})

    def terminate_recorded(self, job_id):
        import psutil
        alive = []
        for pid, created in list(self.processes.get(job_id, {}).items()):
            try:
                process = psutil.Process(pid)
                if abs(process.create_time() - created) < 0.01:
                    alive.append(process)
            except psutil.NoSuchProcess:
                pass
        for process in reversed(alive):
            try:
                process.terminate()
            except psutil.NoSuchProcess:
                pass
        _, remaining = psutil.wait_procs(alive, timeout=5)
        for process in remaining:
            try:
                if abs(process.create_time() - self.processes[job_id][process.pid]) < 0.01:
                    process.kill()
            except psutil.NoSuchProcess:
                pass
        _, remaining = psutil.wait_procs(remaining, timeout=5)
        if remaining:
            raise RuntimeError("Recorded NX processes have not acknowledged termination")

    def execute_stage(self, job_id, stage, task):
        directory = self.store.path(job_id)
        result_path = directory / "stages" / (stage + "-result.json")
        task = dict(task, stage=stage, result_path=str(result_path))
        task_path = directory / "stages" / (stage + "-task.json")
        atomic_json(task_path, task)
        environment = os.environ.copy()
        nx_base = Path(self.executable).resolve().parent.parent
        environment.update(NX_THERMAL_TASK=str(task_path), UGII_BASE_DIR=str(nx_base), UGII_TMP_DIR=str(directory / "tmp"))
        environment["PATH"] = str(Path(self.executable).parent) + os.pathsep + environment.get("PATH", "")
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        started = time.monotonic()
        record_path = directory / "stages" / (stage + ".json")
        with (directory / "logs" / (stage + ".stdout.log")).open("wb") as stdout, (directory / "logs" / (stage + ".stderr.log")).open("wb") as stderr:
            process = subprocess.Popen([self.executable, str(directory / "journals" / "pipeline.py")], cwd=directory, env=environment, stdout=stdout, stderr=stderr, creationflags=creationflags)
            atomic_json(record_path, {"status": "running", "stage": stage, "pid": process.pid, "startedAt": now()})
            while process.poll() is None:
                self.capture_processes(job_id, process)
                state = self.store.get(job_id)
                if state["status"] == "cancel_requested":
                    self.terminate_recorded(job_id)
                    process.wait(timeout=10)
                    self.store.update(job_id, status="cancelled", stage="cancelled", message="Recorded NX process tree stopped; partial native files retained")
                    atomic_json(record_path, {"status": "cancelled", "stage": stage, "exitCode": process.returncode, "finishedAt": now()})
                    raise RuntimeError("Job cancelled")
                if time.monotonic() - started > self.timeout:
                    self.terminate_recorded(job_id)
                    raise TimeoutError("NX stage exceeded configured timeout: " + stage)
                time.sleep(0.25)
        result = load_json(result_path) if result_path.is_file() else {"ok": False, "error": "NX produced no structured stage result; inspect stage logs"}
        atomic_json(record_path, {"status": "completed" if result.get("ok") and process.returncode == 0 else "failed", "stage": stage, "exitCode": process.returncode, "finishedAt": now()})
        if process.returncode != 0 or not result.get("ok"):
            raise RuntimeError(result.get("error", "NX stage failed")[:4000])
        return result

    def advance_stage(self, job_id, stage, status, progress):
        # cancel() uses this same lock: a request cannot land between the
        # cancellation check and a stage update that would overwrite it.
        with self.guard:
            state = self.store.get(job_id)
            if state["status"] == "cancel_requested":
                self.terminate_recorded(job_id)
                self.store.update(job_id, status="cancelled", stage="cancelled", message="Recorded processes stopped; cancelled between NX stages")
                return False
            if state["status"] in TERMINAL:
                return False
            self.store.update(job_id, status=status, stage=stage, progress=progress, message="NX " + stage)
            return True

    def execute(self, job_id):
        directory = self.store.path(job_id)
        if os.name == "nt" and not str(directory).isascii():
            raise ValueError("NX12 work root must contain ASCII characters only")
        self.store.verify_inputs(job_id)
        source = Path(__file__).parent / "journals"
        shutil.copytree(source, directory / "journals", dirs_exist_ok=True)
        scripts = {p.relative_to(directory / "journals").as_posix(): digest(p.read_bytes()) for p in (directory / "journals").rglob("*.py")}
        atomic_json(directory / "input" / "journal-manifest.json", scripts)
        task = {"run_dir": str(directory), "snapshot_path": str(directory / "input" / "snapshot.json"), "geometry_path": str(directory / "input" / "geometry.step"), "nx_base": str(Path(self.executable).parent.parent), "master_part": str(directory / "nx" / "geometry_master.prt"), "fem_path": str(directory / "nx" / "case.fem"), "sim_path": str(directory / "nx" / "thermal_case.sim"), "raw_results_dir": str(directory / "raw"), "output_dir": str(directory / "results"), "thermal_case_path": str(directory / "nx" / "thermal-case.json"), "solver_timeout_seconds": self.timeout}
        for stage, status, progress in (("import", "preparing", 5), ("model", "modeling", 20), ("readback", "checking", 50), ("solve", "solving", 60), ("extract", "extracting", 90)):
            if not self.advance_stage(job_id, stage, status, progress):
                return
            self.store.verify_inputs(job_id)
            self.execute_stage(job_id, stage, task)
            if stage == "readback":
                readback = load_json(directory / "results" / "readback.json")
                if not readback.get("components") or not readback.get("mesh", {}).get("nodeCount"):
                    raise RuntimeError("NX readback has no real components/mesh")
                self.store.update(job_id, readback=readback, message="NX model, mesh and applied conditions available before solving")
            self.store.register_artifacts(job_id)
        summary = load_json(directory / "results" / "summary.json")
        components = [{"id": cid, "minK": row["min_K"], "maxK": row["max_K"], "meanK": row["mean_K"]} for cid, row in summary["component_temperatures_K"].items()]
        snapshot = load_json(directory / "input" / "snapshot.json")
        if {x["id"] for x in components} != {x["id"] for x in snapshot["components"]}:
            raise RuntimeError("NX temperature result does not cover every input component")
        for suffix in (".prt", ".fem", ".sim", ".bun"):
            if not any(p.stat().st_size for p in (directory / "nx").rglob("*" + suffix)):
                raise RuntimeError("Missing native output " + suffix)
        from .results import solver_balance
        balance = solver_balance(directory, sum(float(c["power"]) for c in snapshot["components"]))
        atomic_json(directory / "results" / "energy_balance.json", balance)
        from .journals.mesh_export import write_temperature_glb
        temperature_range = write_temperature_glb(str(directory / "raw"), snapshot, str(directory / "results" / "temperature.glb"))
        self.store.register_artifacts(job_id)
        with self.guard:
            if self.store.get(job_id)["status"] == "cancel_requested":
                self.terminate_recorded(job_id)
                self.store.update(job_id, status="cancelled", stage="cancelled", message="取消请求已确认；完成的原生文件仍保留")
            else:
                self.store.update(job_id, status="completed", stage="completed", progress=100, message="NX steady-state outputs and component temperatures extracted", result={"source": "nx", "components": components, "energyBalance": balance, "meshArtifact": "temperature.glb", "temperatureArtifact": "native.vtu", "temperatureRangeK": temperature_range, "temperatureColorMap": {"interpolation":"linear-rgb", "stops":[[0,[0.05,0.2,1]],[0.33,[0,0.85,1]],[0.67,[1,0.95,0]],[1,[0.9,0.05,0]]]}})
