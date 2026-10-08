import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile

from thermal_executor.core import JobStore, digest, safe_geometry
from thermal_executor.worker import Worker

STEP = b"ISO-10303-21;\nHEADER;ENDSEC;DATA;ENDSEC;END-ISO-10303-21;"
SNAPSHOT = {"schemaVersion":"1.0","analysisType":"steady-state","components":[{"id":"c-a","stepIndex":1,"kind":"solid","conductivity":167,"emissivity":.8,"absorptivity":.3,"power":1}],"run":{"id":"run-a"}}

class FrozenJobs(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = JobStore(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def submit(self, **kwargs):
        return self.store.submit(copy.deepcopy(SNAPSHOT), "part.step", STEP, **kwargs)

    def test_transfer_hash_and_frozen_hash_both_checked(self):
        with self.assertRaisesRegex(ValueError,"transfer SHA"):
            self.submit(expected_sha="0"*64)
        job = self.submit(expected_sha=digest(STEP))
        self.store.verify_inputs(job["id"])
        (self.store.path(job["id"])/"input"/"geometry.step").write_bytes(STEP+b"tamper")
        with self.assertRaisesRegex(ValueError,"Frozen input"):
            self.store.verify_inputs(job["id"])

    def test_idempotency_never_overwrites_different_snapshot(self):
        first = self.submit(idempotency_key="same")
        self.assertEqual(first["id"], self.submit(idempotency_key="same")["id"])
        changed = copy.deepcopy(SNAPSHOT)
        changed["components"][0]["power"] = 99
        with self.assertRaisesRegex(ValueError,"different frozen"):
            self.store.submit(changed,"part.step",STEP,"same")
        content = json.loads((self.store.path(first["id"])/"input"/"snapshot.json").read_text())
        self.assertEqual(content["components"][0]["power"],1)

    def test_archive_traversal_rejected_even_if_unselected_entry(self):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,"w") as archive:
            archive.writestr("part.step",STEP)
            archive.writestr("../escape.txt","bad")
        with self.assertRaisesRegex(ValueError,"Unsafe"):
            safe_geometry("a.zip",stream.getvalue())

    def test_valid_archive_is_read_not_extracted(self):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,"w") as archive:
            archive.writestr("nested/part.stp",STEP)
        self.assertEqual(safe_geometry("a.zip",stream.getvalue()),STEP)
        self.assertEqual(list(Path(self.temp.name).iterdir()),[])

    def test_unknown_artifact_and_path_traversal_denied(self):
        job=self.submit()
        directory=self.store.path(job["id"])
        (directory/"results"/"result.json").write_text('{}')
        self.store.register_artifacts(job["id"])
        self.assertTrue(self.store.artifact(job["id"],"result.json").is_file())
        for name in ("../state.json","state.json","/etc/passwd"):
            with self.assertRaises(KeyError):
                self.store.artifact(job["id"],name)

    def test_restart_never_requeues_active_native_job(self):
        active=self.submit(idempotency_key="active")
        self.store.update(active["id"],status="solving",stage="solve")
        queued=self.submit(idempotency_key="queued")
        JobStore(self.temp.name).recover()
        self.assertEqual(self.store.get(active["id"])["status"],"failed")
        self.assertTrue(self.store.get(active["id"])["recoveryRequired"])
        self.assertEqual(self.store.get(queued["id"])["status"],"queued")

    def test_cancellation_requires_confirmation_and_no_process_for_queue(self):
        job=self.submit()
        worker=Worker(self.store)
        with self.assertRaisesRegex(ValueError,"confirm"):
            worker.cancel(job["id"],False)
        self.assertEqual(worker.cancel(job["id"],True)["status"],"cancelled")

    def test_cancel_never_kills_reused_pid_or_unrelated_process(self):
        import psutil
        job=self.submit()
        worker=Worker(self.store)
        unrelated=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"])
        owned=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"])
        try:
            worker.processes[job["id"]]={owned.pid:psutil.Process(owned.pid).create_time()}
            worker.terminate_recorded(job["id"])
            owned.wait(timeout=3)
            self.assertIsNone(unrelated.poll())
            worker.processes[job["id"]]={unrelated.pid:psutil.Process(unrelated.pid).create_time()-100}
            worker.terminate_recorded(job["id"])
            self.assertIsNone(unrelated.poll())
        finally:
            for process in (owned,unrelated):
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=3)

    def test_cancellation_during_stage_transition_is_not_overwritten(self):
        job=self.submit()
        worker=Worker(self.store)
        worker.active=job["id"]
        before_write,allow_write,cancel_started,cancel_finished=(threading.Event() for _ in range(4))
        update=self.store.update
        errors=[]

        def delayed_update(job_id,**changes):
            if changes.get("status")=="modeling":
                before_write.set()
                if not allow_write.wait(3):
                    raise RuntimeError("Test transition barrier timed out")
            return update(job_id,**changes)

        def transition():
            try:
                worker.advance_stage(job["id"],"model","modeling",20)
            except Exception as error:
                errors.append(error)

        def cancel():
            cancel_started.set()
            try:
                worker.cancel(job["id"],True)
            except Exception as error:
                errors.append(error)
            finally:
                cancel_finished.set()

        self.store.update=delayed_update
        stage_thread=threading.Thread(target=transition)
        cancel_thread=threading.Thread(target=cancel)
        stage_thread.start()
        try:
            self.assertTrue(before_write.wait(3))
            cancel_thread.start()
            self.assertTrue(cancel_started.wait(3))
            self.assertFalse(cancel_finished.wait(.1),"Cancellation must serialize with the pending stage transition")
        finally:
            allow_write.set()
            stage_thread.join(3)
            if cancel_thread.ident is not None:
                cancel_thread.join(3)
            self.store.update=update
        self.assertFalse(errors)
        self.assertFalse(stage_thread.is_alive())
        self.assertFalse(cancel_thread.is_alive())
        self.assertEqual(self.store.get(job["id"])["status"],"cancel_requested")
        self.assertFalse(worker.advance_stage(job["id"],"readback","checking",50))
        self.assertEqual(self.store.get(job["id"])["status"],"cancelled")

if __name__=="__main__":
    unittest.main()
