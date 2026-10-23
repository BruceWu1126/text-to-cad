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
        # The record lives as long as the engine (the page session), never only a day: a reload
        # finds its export expired, and asking again by its request is that record, not a new run.
        self.now += 86400
        self.assertEqual(self.jobs.get(ident)["state"], "expired")
        self.assertEqual(self.jobs.create(self.request())["id"], ident)
        self.assertEqual(len(self.calls), 1)

    def download(self, ident, *, gone=False, headers=None):
        response = Mock(gone=gone)
        request = SimpleNamespace(path=f"/__cad/exports/{ident}/file", method="GET", query={},
                                  header=lambda name, default="": (headers or {}).get(name, default))
        self.jobs.handle(request, response)
        return response

    def test_a_file_the_browser_fetched_whole_is_deleted_at_once(self):
        self.finish.set()
        ident = self.jobs.create(self.request())["id"]
        self.settled(ident)
        path = Path(self.download(ident).stream_file.call_args.args[0])
        self.assertFalse(path.exists())
        job = self.jobs.get(ident)
        self.assertEqual((job["state"], job["fetched"]), ("expired", True))
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])
        self.assertEqual(self.download(ident).send_json.call_args.args[0], 410)

    def test_a_part_or_a_broken_transfer_keeps_the_file_for_another_fetch(self):
        self.finish.set()
        ident = self.jobs.create(self.request())["id"]
        self.settled(ident)
        self.download(ident, headers={"range": "bytes=0-1"})
        self.download(ident, gone=True)
        self.assertEqual(self.jobs.get(ident)["state"], "ready")
        self.download(ident)
        self.assertEqual(self.jobs.get(ident)["state"], "expired")

    def test_an_unfetched_file_is_kept_ten_minutes_then_deleted(self):
        self.finish.set()
        ident = self.jobs.create(self.request())["id"]
        ready = self.settled(ident)
        self.assertEqual(ready["expiresAt"], self.now + 600)
        with self.jobs.artifact(ident) as path:
            pass
        self.now += 599
        self.assertEqual(self.jobs.get(ident)["state"], "ready")
        self.assertTrue(path.exists())
        self.now += 1
        job = self.jobs.get(ident)
        self.assertEqual((job["state"], job.get("fetched")), ("expired", None))
        self.assertFalse(path.exists())

    def test_a_ready_job_s_status_waits_for_its_file_to_go(self):
        self.finish.set()
        ident = self.jobs.create(self.request())["id"]
        ready = self.settled(ident)
        answered = []
        waiter = threading.Thread(target=lambda: answered.append(self.jobs.get(ident, ready["sequence"], 25)))
        waiter.start()
        with self.jobs.changed:  # the waiter is parked on the job's sequence before the fetch ends
            self.assertTrue(self.jobs.changed.wait_for(lambda: waiter.is_alive(), 10))
        self.download(ident)
        waiter.join(10)
        self.assertEqual(answered[0]["state"], "expired")

    def test_engine_stop_removes_every_artifact_and_discards_running_work(self):
        from cadgen.viewer.engine_app import EngineApp

        self.finish.set()
        ready = self.settled(self.jobs.create(self.request())["id"])
        self.assertEqual(len(list(Path(self.tmp.name).iterdir())), 1)
        self.finish.clear(); self.entered.clear()
        running = self.jobs.create(self.request("two"))
        self.assertTrue(self.entered.wait(10))
        engine = SimpleNamespace(lock=threading.RLock(), _default=SimpleNamespace(_exports=self.jobs), _mcp=None,
                                 analytics=Mock())
        EngineApp.close(engine)
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])
        self.finish.set()
        self.assertEqual(self.settled(running["id"])["state"], "cancelled")
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])
        with self.assertRaises(ExportError):
            with self.jobs.artifact(ready["id"]):
                pass

    def test_a_long_session_keeps_exporting_and_forgotten_requests_never_regenerate(self):
        self.finish.set()
        first = self.request("first")
        for index in range(130):
            request = first if index == 0 else {**first, "requestId": f"request-{index:016d}"}
            ident = self.jobs.create(request)["id"]
            self.settled(ident)
            self.jobs.release(ident)
        self.assertLessEqual(len(self.jobs.jobs), 128)
        with self.assertRaises(ExportError) as caught:
            self.jobs.create(first)
        self.assertEqual(caught.exception.status, 410)
        self.assertEqual(len(self.calls), 130)

    def test_stale_epoch_closed_formats_and_nonfinite_pose_are_refused(self):
        for change in ({"epoch": "old"}, {"format": "zip"}, {"format": []}, {"output": "/tmp/unasked"}):
            with self.assertRaises(ExportError):
                self.jobs.create({**self.request(), **change})
        for pose in ({"joint": float("nan")}, {"joint": True}, {"joint": "45"}):
            with self.assertRaises(ExportError):
                specification({"doc": "d", "revision": "v", "pose": pose})

    def test_route_download_and_no_requested_server_path(self):
        self.finish.set()
        ident = self.jobs.create(self.request())["id"]
        self.settled(ident)
        sent = []
        response = Mock(gone=False)
        response.stream_file.side_effect = lambda path, *_rest: sent.append(path.read_bytes())
        self.jobs.handle(SimpleNamespace(path=f"/__cad/exports/{ident}/file", method="GET", query={}), response)
        self.assertEqual(sent, [b"model"])
        self.assertIn("attachment;", dict(response.stream_file.call_args.args[3])["content-disposition"])
        self.jobs.release(ident)

    def test_plain_download_names_the_file_for_every_browser(self):
        self.finish.set()
        self.jobs._source = lambda spec: ({}, {}, "ブラケット v2")
        ident = self.jobs.create(self.request())["id"]
        self.settled(ident)
        response = self.download(ident)
        disposition = dict(response.stream_file.call_args.args[3])["content-disposition"]
        self.assertEqual(disposition, "attachment; filename=\"_____ v2.step\"; filename*=UTF-8''%E3%83%96%E3%83%A9%E3%82%B1%E3%83%83%E3%83%88%20v2.step")
        self.jobs.release(ident)
        part = self.jobs.create({**self.request("two"), "source": {"doc": "d", "revision": "v", "part": "hinge/lid"}})
        self.assertEqual(self.settled(part["id"])["filename"], "ブラケット v2-part.step")
        self.jobs.release(part["id"])


if __name__ == "__main__":
    unittest.main()
