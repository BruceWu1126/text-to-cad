"""A document's compile is a daemon job, and the events it emits.

Tested with a real daemon (the test run's) on a real (temporary) store. The one job
a build hands to the pool is a document compile (``submit_compile``): what it must do is
compile into the store the job names, report the document's transitions through the event
sink, capture a failed job's output for the error, and never block the caller until
``wait()``.
"""

from __future__ import annotations

import hashlib
import itertools
import os
import threading
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support.paths import add_repo_path
from tests.python.support.tmp_root import temporary_directory

add_repo_path("packages/cadgen/src")

from cadgen.daemon import executors  # noqa: E402
from cadgen.daemon.jobs import JobLedger  # noqa: E402


class Events(unittest.TestCase):
    def setUp(self):
        for name, value in (("_EVENT_SINK", None), ("_EVENT_SEQUENCE", itertools.count(1))):
            patcher = mock.patch.object(executors, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        environment = mock.patch.dict(os.environ, {"CADGEN_JOB_ID": ""})
        environment.start()
        self.addCleanup(environment.stop)

    def test_concurrent_preview_cannot_overtake_an_announced_save(self):
        ledger = JobLedger()
        model = str(Path("event-model.step").resolve())
        job = ledger.start(tool="step-compile", subject=model)
        first_sink_entered = threading.Event()
        release_first = threading.Event()
        progress = threading.Event()
        delivered, errors = [], []

        class ObservedLock:
            def __init__(self):
                self.lock = threading.RLock()

            def __enter__(self):
                if not self.lock.acquire(blocking=False):
                    progress.set()
                    self.lock.acquire()
                return self

            def __exit__(self, *_):
                self.lock.release()

        def observe(event):
            try:
                if "saved" in event:
                    first_sink_entered.set()
                    if not release_first.wait(5):
                        errors.append("saved sink was never released")
                        return
                delivered.append(event["sequence"])
                ledger.observe({"event": event})
            except Exception as error:
                errors.append(error)
            finally:
                if "preview" in event:
                    progress.set()

        def emit(field):
            try:
                executors.emit_event({"model": model, "state": "building",
                                      field: {"output": model, "tree": "result-tree", "documentHash": "d" * 64}})
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=emit, args=(field,), daemon=True)
                   for field in ("saved", "preview")]
        with mock.patch.object(executors, "_EVENT_LOCK", ObservedLock()), \
                mock.patch.dict(os.environ, {"CADGEN_JOB_ID": job["id"]}):
            executors.set_event_sink(observe)
            try:
                threads[0].start()
                self.assertTrue(first_sink_entered.wait(5), "saved sink never started")
                threads[1].start()
                self.assertTrue(progress.wait(5), "preview neither contended nor arrived")
            finally:
                release_first.set()
                for thread in threads:
                    if thread.ident is not None:
                        thread.join(5)
            self.assertFalse(any(thread.is_alive() for thread in threads), "emitter did not retire")
        self.assertEqual(errors, [])
        self.assertEqual(delivered, [1, 2])
        saved = ledger.snapshot()[0]["savedResults"][model]
        self.assertEqual((saved["tree"], saved["sequence"]), ("result-tree", 1))

    def test_sink_can_inspect_and_replace_itself(self):
        delivered, installed, errors = [], [], []

        def first(event):
            try:
                installed.append(executors.sink_installed())
                executors.set_event_sink(delivered.append)
                delivered.append(event)
            except Exception as error:
                errors.append(error)

        lock = executors._EVENT_LOCK

        class DeadlineLock:
            def __enter__(self):
                if not lock.acquire(timeout=5):
                    raise TimeoutError("sink registry helper deadlocked")

            def __exit__(self, *_):
                lock.release()

        # Exercise the actual lock; a plain Lock fails its deadline instead of hanging.
        events = [{"state": "building"}, {"state": "done"}]
        with mock.patch.object(executors, "_EVENT_LOCK", DeadlineLock()):
            executors.set_event_sink(first)
            for event in events:
                executors.emit_event(event)
        self.assertEqual(errors, [])
        self.assertEqual(installed, [True])
        self.assertEqual(delivered, events)

    def test_forwarding_keeps_identity_and_local_tagging_does_not_mutate(self):
        delivered = []
        executors.set_event_sink(delivered.append)
        forwarded = {"job": "original-producer", "sequence": 29}
        local = {"state": "building"}
        with mock.patch.dict(os.environ, {"CADGEN_JOB_ID": "local-producer"}):
            executors.emit_event(forwarded)
            executors.emit_event(local)
        self.assertIs(delivered[0], forwarded)
        self.assertEqual(forwarded, {"job": "original-producer", "sequence": 29})
        self.assertEqual(local, {"state": "building"})
        self.assertEqual(delivered[1], {**local, "job": "local-producer", "sequence": 1})
        with mock.patch.dict(os.environ):
            os.environ.pop("CADGEN_JOB_ID", None)
            executors.emit_event(local)
        self.assertIs(delivered[2], local)

    def test_source_capture_precedes_optional_or_failing_sink(self):
        def failing(event):
            raise RuntimeError("reporting failed")

        for sink in (None, failing):
            with self.subTest(sink=sink):
                executors.set_event_sink(sink)
                with executors.capture_source_result(Path("event-model.py")) as capture:
                    executors.emit_event({"sourceResult": {"model": capture.model, "tree": "result-tree"}})
                self.assertTrue(capture.result_ready)
                self.assertEqual(capture.wait_result(timeout=0), "result-tree")


class DaemonCompile(unittest.TestCase):
    def setUp(self):
        self.tmp = temporary_directory(prefix="cadgen-executors-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = self.root / "store"
        patcher = mock.patch.dict(os.environ, {"CADGEN_CACHE_DIR": str(self.store)})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("CADGEN_DAEMON_CHILD", None)
        self.events: list[dict] = []
        self.closed = threading.Event()  # the tree's line for the document closed: done or failed

        def observe(event: dict) -> None:
            self.events.append(event)
            if event.get("state") in ("done", "failed"):
                self.closed.set()

        executors.set_event_sink(observe)
        self.addCleanup(executors.set_event_sink, None)

    def _vendor_document(self) -> Path:
        # A document whose bytes the store has no tree for: written by the kernel's
        # own exporter, never by a build.
        from build123d import Box, export_step

        document = self.root / "vendor.step"
        export_step(Box(10.0, 4.0, 2.0), str(document))
        return document

    def test_a_compile_lands_its_tree_in_the_jobs_store_and_closes_its_line(self):
        from cadgen.store.records import tree_for_document_hash

        document = self._vendor_document()
        other = self.root / "other-store"
        job = executors.submit_compile(document, store_root=other, parent=Path("/models/parent.py"))
        self.assertFalse(job.done, "submit must return before the compile finishes")
        self.assertEqual(job.wait(timeout=300), 0, job.output())
        self.assertTrue(self.closed.wait(30), "the document's line never closed")
        digest = hashlib.sha256(document.read_bytes()).hexdigest()
        with mock.patch.dict(os.environ, {"CADGEN_CACHE_DIR": str(other)}):
            self.assertIsNotNone(tree_for_document_hash(digest), "the tree did not land in the job's store")
        self.assertIsNone(tree_for_document_hash(digest), "the compile wrote into the environment's store")
        states = [(e["model"], e["state"]) for e in self.events]
        self.assertEqual(states[0], (str(document), "submitted"))
        self.assertEqual(Path(self.events[0]["parent"]), Path(os.path.abspath("/models/parent.py")))
        self.assertEqual(states[1], (str(document), "building"))
        self.assertEqual(self.events[1]["phase"], "compile")
        self.assertEqual(states[-1], (str(document), "done"))

    def test_a_failed_compile_reports_failed_and_keeps_its_output(self):
        document = self.root / "broken.step"
        document.write_text("ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nthis is not a document\n", encoding="utf-8")
        job = executors.submit_compile(document)
        self.assertNotEqual(job.wait(timeout=300), 0)
        self.assertIn("broken.step", job.output())
        self.assertTrue(self.closed.wait(30), "the document's line never closed")
        self.assertEqual((self.events[-1]["model"], self.events[-1]["state"]), (str(document), "failed"))
        # The failed event carries the job's one-line reason: the ledger row a compile makes
        # under its build says why it failed, never only that.
        from cadgen.daemon.jobs import failure_message

        self.assertTrue(self.events[-1].get("error"), self.events[-1])
        self.assertEqual(self.events[-1]["error"], failure_message(job.output())[0])


if __name__ == "__main__":
    unittest.main()
