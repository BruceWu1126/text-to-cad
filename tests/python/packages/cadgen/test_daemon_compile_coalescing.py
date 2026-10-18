"""Coalesced compiles: a second request for a document's bytes joins the job in flight,
finishes with its exit and its reason, and is a request of its own in the ledger."""
from __future__ import annotations

import concurrent.futures
import contextlib
import json
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from cadgen.daemon import server
from cadgen.daemon.broker import Broker
from cadgen.daemon.jobs import JobLedger
from tests.python.support.tmp_root import generated_cad_directory


class Connection:
    def __init__(self):
        self.frames = []

    def send(self, raw):
        self.frames.append(json.loads(raw))


class DisconnectableConnection(Connection):
    def __init__(self):
        super().__init__()
        self.disconnected = threading.Event()
        self.failed_send = threading.Event()

    def send(self, raw):
        if self.disconnected.is_set():
            self.failed_send.set()
            raise OSError("peer closed")
        super().send(raw)

    def recv(self, _timeout=None):
        return b"" if self.disconnected.is_set() else None


class CompileWorker:
    """A warm worker compiling one document: narrates on stderr, holds at a barrier, exits."""

    pid = 123
    extra = False

    def __init__(self, *, exit_code=0):
        self.exit_code = exit_code
        self.started = threading.Event()  # its first frame was relayed
        self.allow_exit = threading.Event()
        self.killed = False

    def send(self, request):
        self.request = request

    def alive(self):
        return not self.killed

    def kill(self):
        self.killed = True
        self.allow_exit.set()

    def frames(self, **_kwargs):
        yield {"stream": "stderr", "data": "[cadgen] compiling\n"}
        self.started.set()
        if not self.allow_exit.wait(5):
            raise AssertionError("Test did not release the completion barrier")
        if self.exit_code:
            yield {"stream": "stderr", "data": "[cadgen] FAILED: RuntimeError: the document is wrong on purpose\n"}
        yield {"exit": self.exit_code}


class ReleasingCompileWorker(CompileWorker):
    """Native result, then deferred work: separate barriers for its answer and exit."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.allow_release = threading.Event()

    def frames(self, **_kwargs):
        yield {"stream": "stderr", "data": "[cadgen] compiling\n"}
        self.started.set()
        if not self.allow_release.wait(5):
            raise AssertionError("Test did not release the answer barrier")
        yield {"released": 0}
        if not self.allow_exit.wait(5):
            raise AssertionError("Test did not release the completion barrier")
        if self.exit_code:
            yield {"stream": "stderr", "data": "[cadgen] FAILED: RuntimeError: finishing failed on purpose\n"}
        yield {"exit": self.exit_code}


class CoalescedCompiles(unittest.TestCase):
    def setUp(self):
        temporary = generated_cad_directory(prefix="coalesced-compile-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.document = self.root / "vendor.step"
        self.document.write_text("ISO-10303-21;\n", encoding="utf-8")
        self.store = self.root / "store"
        self.ledger, self.broker = JobLedger(), Broker()

    def request(self, *, closure="same bytes"):
        return {"tool": "step-compile", "argv": [str(self.document)], "cwd": str(self.root),
                "store_root": str(self.store), "closure": closure, "coalesce": True, "dependency": True}

    def serving(self, worker_pool):
        stack = contextlib.ExitStack()
        for name, value in (("_POOL", worker_pool), ("_JOBS", self.ledger), ("_BROKER", self.broker)):
            stack.enter_context(mock.patch.object(server, name, value))
        stack.enter_context(mock.patch.object(server, "_log"))
        stack.enter_context(mock.patch.object(server, "CLIENT_LIVENESS_INTERVAL_SECONDS", 0.02))
        return stack

    def wait_until(self, predicate, message):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail(message)

    def exercise_follower(self, *, exit_code):
        worker = CompileWorker(exit_code=exit_code)
        worker_pool = mock.Mock()
        worker_pool.acquire.return_value = worker
        producer_conn, follower_conn = Connection(), Connection()
        with self.serving(worker_pool), mock.patch.object(server, "_watch_client"), \
             concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            producer = executor.submit(server._handle_request, producer_conn, self.request())
            try:
                self.assertTrue(worker.started.wait(3))
                producer_id = worker.request["job_id"]
                follower = executor.submit(server._handle_request, follower_conn, self.request())
                self.wait_until(lambda: self.broker.snapshot()["coalesced"] == 1, "the follower never attached")
                self.assertFalse(follower.done(), "a follower finished before the job it joined")
                self.assertEqual(follower_conn.frames, [], "a follower hears nothing of the job but its exit")
                worker.allow_exit.set()
                producer.result(timeout=3)
                follower.result(timeout=3)
            finally:
                worker.allow_exit.set()
        # A document binds no worker: the compile borrows a spare, once for both requests.
        worker_pool.acquire.assert_called_once_with("", dependency=True, on_start=mock.ANY, on_wait=mock.ANY)
        self.assertEqual(producer_conn.frames[-1], {"exit": exit_code})
        self.assertEqual(follower_conn.frames, [{"exit": exit_code}])
        owner, follower = self.ledger.snapshot()
        self.assertEqual(owner["id"], producer_id)
        self.assertNotEqual(owner["id"], follower["id"])
        self.assertEqual((owner["exit"], follower["exit"]), (exit_code, exit_code))
        self.assertEqual((owner["state"], follower["state"]), ("failed" if exit_code else "done",) * 2)
        self.assertIsNotNone(follower["finishedAt"])
        reason = "the document is wrong on purpose" if exit_code else None
        self.assertEqual((owner["error"], follower["error"]), (reason, reason), "the follower carries the owner's reason")
        self.assertEqual(self.broker.snapshot()["inflight"], 0)

    def test_a_follower_finishes_with_the_owners_exit(self):
        self.exercise_follower(exit_code=0)

    def test_a_follower_of_a_failed_owner_fails_with_its_reason(self):
        self.exercise_follower(exit_code=1)

    def exercise_released_followers(self, *, exit_code):
        worker = ReleasingCompileWorker(exit_code=exit_code)
        worker_pool = mock.Mock()
        worker_pool.acquire.return_value = worker

        class LeavesAtAnswer(DisconnectableConnection):
            def __init__(self):
                super().__init__()
                self.released = threading.Event()

            def send(self, raw):
                super().send(raw)
                if self.frames[-1] == {"released": 0}:
                    self.disconnected.set()
                    self.released.set()

        connections = [LeavesAtAnswer() for _ in range(3)]
        with self.serving(worker_pool), concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            owner = executor.submit(server._handle_request, connections[0], self.request())
            try:
                self.assertTrue(worker.started.wait(3))
                follower = executor.submit(server._handle_request, connections[1], self.request())
                self.wait_until(lambda: self.broker.snapshot()["coalesced"] == 1, "follower never attached")
                worker.allow_release.set()
                for connection in connections[:2]:
                    self.assertTrue(connection.released.wait(3), "the native answer was not relayed")
                late = executor.submit(server._handle_request, connections[2], self.request())
                self.assertTrue(connections[2].released.wait(3), "a late follower missed the retained answer")
                self.assertEqual(self.broker.snapshot()["inflight"], 1)
                self.assertFalse(worker.killed)
                for row in self.ledger.snapshot():
                    self.assertIsNone(row.get("finishedAt"), "release prematurely finished a ledger row")
                self.assertFalse(any(request.done() for request in (owner, follower, late)))
                worker.allow_exit.set()
                for request in (owner, follower, late):
                    request.result(timeout=3)
            finally:
                worker.allow_release.set()
                worker.allow_exit.set()
        worker_pool.acquire.assert_called_once_with("", dependency=True, on_start=mock.ANY, on_wait=mock.ANY)
        worker_pool.release.assert_called_once_with(worker, healthy=True, cancelled=False)
        expected = "finishing failed on purpose" if exit_code else None
        rows = self.ledger.snapshot()
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertEqual((row["state"], row["exit"], row["error"]),
                             ("failed" if exit_code else "done", exit_code, expected))
            self.assertIsNotNone(row["finishedAt"])
        for connection in connections:
            self.assertEqual([frame for frame in connection.frames if "released" in frame], [{"released": 0}])
            self.assertFalse(any("exit" in frame for frame in connection.frames), "the client left at its answer")
        self.assertFalse(worker.killed)
        self.assertEqual(self.broker.snapshot()["inflight"], 0)

    def test_present_and_late_followers_are_released_while_their_ledger_rows_finish_with_the_owner(self):
        self.exercise_released_followers(exit_code=0)

    def test_released_followers_keep_a_later_finishing_failure_in_their_ledger_rows(self):
        self.exercise_released_followers(exit_code=1)

    def test_an_owner_refused_admission_fails_its_request_and_frees_the_entry(self):
        worker_pool = mock.Mock()
        worker_pool.acquire.side_effect = server.pool_mod.MemoryAdmissionError("memory admission refused")
        conn = Connection()
        with self.serving(worker_pool):
            server._handle_request(conn, self.request())
        (job,) = self.ledger.snapshot()
        self.assertEqual((job["state"], job["error"]), ("failed", "memory admission refused"))
        self.assertEqual(self.broker.snapshot()["inflight"], 0)
        self.assertEqual(conn.frames[-1], {"exit": 1})
        worker_pool.release.assert_not_called()

    def test_disconnected_producer_continues_for_an_attached_consumer(self):
        worker = CompileWorker()
        worker_pool = mock.Mock()
        worker_pool.acquire.return_value = worker
        producer_conn, consumer_conn = DisconnectableConnection(), DisconnectableConnection()
        with self.serving(worker_pool), concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            producer = executor.submit(server._handle_request, producer_conn, self.request())
            self.assertTrue(worker.started.wait(3))
            consumer = executor.submit(server._handle_request, consumer_conn, self.request())
            self.wait_until(lambda: self.broker.snapshot()["coalesced"] == 1, "consumer never attached")

            producer_conn.disconnected.set()
            self.assertTrue(producer_conn.failed_send.wait(3), "producer disconnect was not observed")
            self.assertFalse(worker.killed, "canonical work was killed despite its attached consumer")
            worker.allow_exit.set()
            producer.result(timeout=3)
            consumer.result(timeout=3)

        self.assertFalse(worker.killed)
        self.assertEqual(consumer_conn.frames[-1], {"exit": 0})
        worker_pool.release.assert_called_once_with(worker, healthy=True, cancelled=False)
        self.assertEqual(self.broker.snapshot()["inflight"], 0)

    def test_disconnected_consumer_does_not_cancel_the_active_producer(self):
        worker = CompileWorker()
        worker_pool = mock.Mock()
        worker_pool.acquire.return_value = worker
        producer_conn, consumer_conn = DisconnectableConnection(), DisconnectableConnection()
        with self.serving(worker_pool), concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            producer = executor.submit(server._handle_request, producer_conn, self.request())
            self.assertTrue(worker.started.wait(3))
            consumer = executor.submit(server._handle_request, consumer_conn, self.request())
            self.wait_until(lambda: self.broker.snapshot()["coalesced"] == 1, "consumer never attached")

            consumer_conn.disconnected.set()
            consumer.result(timeout=3)
            self.assertFalse(worker.killed, "one subscriber canceled its producer's canonical work")
            self.assertEqual(self.broker.snapshot()["inflight"], 1)
            worker.allow_exit.set()
            producer.result(timeout=3)

        self.assertFalse(worker.killed)
        worker_pool.release.assert_called_once_with(worker, healthy=True, cancelled=False)


if __name__ == "__main__":
    unittest.main()
