"""Export jobs: queued cancellation, identity, retention and streaming leases, without a kernel."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cadgen.viewer.exports import ExportError, ExportJobs, specification


class ExportsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 1000
        self.entered, self.finish = threading.Event(), threading.Event()
        self.calls = []
        def run(spec, target, progress):
            if target is None:
                return {"formats": []}
            self.calls.append(spec)
            self.entered.set()
            if not self.finish.wait(10):
                raise RuntimeError("test worker was not released")
            progress({"label": "Writing"})
            target.write_bytes(b"model")
            return {"files": []}
        self.jobs = ExportJobs(SimpleNamespace(touch=lambda: None), run=run, clock=lambda: self.now, root=self.tmp.name)
        self.jobs._source = lambda spec: ({}, {}, "bracket")
        self.addCleanup(self.finish.set)

    def request(self, name="one"):
        return {"requestId": "request-" + name * 16, "epoch": self.jobs.epoch,
                "source": {"doc": "d", "revision": "v"}, "format": "step"}

    def settled(self, ident):
        with self.jobs.changed:
            self.assertTrue(self.jobs.changed.wait_for(
                lambda: self.jobs.jobs[ident]["state"] in ("ready", "failed", "cancelled") and not self.jobs.executing, 10))
        return self.jobs.get(ident)

    def test_multiple_jobs_cancel_queue_and_running_without_publishing_partial(self):
        first = self.jobs.create(self.request())
        self.assertTrue(self.entered.wait(10))
        second = self.jobs.create(self.request("two"))
        self.assertEqual(second["state"], "queued")
        self.assertEqual(self.jobs.cancel(second["id"])["state"], "cancelled")
        self.assertEqual(self.jobs.cancel(first["id"])["state"], "cancelling")
        self.finish.set()
        self.assertEqual(self.settled(first["id"])["state"], "cancelled")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    def test_idempotency_is_not_a_second_export_and_changed_request_conflicts(self):
        original = self.request()
        first = self.jobs.create(original)
        self.assertEqual(self.jobs.create(original)["id"], first["id"])
        with self.assertRaises(ExportError) as caught:
            self.jobs.create({**original, "format": "stl"})
        self.assertEqual(caught.exception.status, 409)
        self.finish.set()
        self.assertEqual(self.settled(first["id"])["state"], "ready")
        self.assertEqual(len(self.calls), 1)
        with patch("cadgen.viewer.exports.MAX_RETAINED_BYTES", 1):
            self.assertEqual(self.jobs.create(original)["id"], first["id"])
        self.jobs.release(first["id"])

    def test_artifact_can_be_saved_twice_and_transfer_prevents_expiry_cleanup(self):
        self.finish.set()
        ident = self.jobs.create(self.request())["id"]
        ready = self.settled(ident)
        for _ in range(2):
            with self.jobs.artifact(ident) as path:
                self.assertEqual(path.read_bytes(), b"model")
        with self.jobs.artifact(ident) as path:
            self.now = ready["expiresAt"] + 1
            self.assertEqual(self.jobs.get(ident)["state"], "ready")
            self.assertTrue(path.exists())
        self.assertFalse(path.exists())
        self.assertEqual(self.jobs.get(ident)["state"], "expired")
        with self.assertRaises(ExportError) as caught:
            with self.jobs.artifact(ident):
                pass
        self.assertEqual(caught.exception.status, 410)
        self.now += 86400
        with self.assertRaises(ExportError):
            self.jobs.get(ident)
        with self.assertRaises(ExportError) as caught:
            self.jobs.create(self.request())
        self.assertEqual(caught.exception.status, 410)
        self.assertEqual(len(self.calls), 1)

    def test_engine_stop_removes_every_artifact_and_discards_running_work(self):
        from cadgen.viewer.engine_app import EngineApp

        self.finish.set()
        ready = self.settled(self.jobs.create(self.request())["id"])
        self.assertEqual(len(list(Path(self.tmp.name).iterdir())), 1)
        self.finish.clear(); self.entered.clear()
        running = self.jobs.create(self.request("two"))
        self.assertTrue(self.entered.wait(10))
        engine = SimpleNamespace(lock=threading.RLock(), pages={"page": SimpleNamespace(_exports=self.jobs)},
                                 _default=SimpleNamespace(_exports=None), _mcp=None, analytics=Mock())
        EngineApp.close(engine)
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])
        self.finish.set()
        self.assertEqual(self.settled(running["id"])["state"], "cancelled")
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])
        with self.assertRaises(ExportError):
            with self.jobs.artifact(ready["id"]):
                pass

    def test_stale_epoch_closed_formats_and_nonfinite_pose_are_refused(self):
        for change in ({"epoch": "old"}, {"format": "cadz"}, {"format": []}, {"output": "/tmp/unasked"}):
            with self.assertRaises(ExportError):
                self.jobs.create({**self.request(), **change})
        for pose in ({"joint": float("nan")}, {"joint": True}, {"joint": "45"}):
            with self.assertRaises(ExportError):
                specification({"doc": "d", "revision": "v", "pose": pose})

    def test_route_download_and_no_requested_server_path(self):
        self.finish.set()
        ident = self.jobs.create(self.request())["id"]
        self.settled(ident)
        response = Mock()
        self.jobs.handle(SimpleNamespace(path=f"/__cad/exports/{ident}/file", method="GET", query={}), response)
        args = response.stream_file.call_args.args
        self.assertEqual(args[0].read_bytes(), b"model")
        self.assertIn("attachment;", dict(args[3])["content-disposition"])
        self.jobs.release(ident)


if __name__ == "__main__":
    unittest.main()
