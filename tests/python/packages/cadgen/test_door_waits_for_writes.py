"""A door given a file a build is still writing waits for that write, then reads the bytes
(STORE.md §3, *Exports*).

A build answers once its results are recorded, and the build pool writes its files after:
each is a job the build handed the daemon's supervisor. The daemon's job ledger lists each
file such a job owes (``saving``) and the moment it lands (``saved``): a door asks the ledger,
never a model record, waits while the newest result that owes the file has not saved it, and
reads the file once it has -- or once the job has ended without it. With no job writing the
file it does not wait. A door inside the request that owes the file waits too: a pool job
writes it, never the build.

The ledger here is a real one, answering the door's watch as the daemon's does.
"""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support.paths import add_repo_path

add_repo_path("packages/cadgen/src")

from cadgen.daemon.jobs import JobLedger  # noqa: E402


class DoorWaitsForWrites(unittest.TestCase):
    TREE = "a" * 64

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.store = self.root / "store"
        self.document = self.root / "part.step"
        self.document.write_bytes(b"ISO-10303-21; the file before the build")
        environment = mock.patch.dict(os.environ, {"CADGEN_CACHE_DIR": str(self.store)})
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("CADGEN_JOB_ID", None)
        self.ledger = JobLedger()
        self.asked: list[str | None] = []
        self.waiting = threading.Event()

        def watch_jobs(after=None, *, output=None, store_root=None):
            self.asked.append(after)
            if after is not None:
                self.waiting.set()  # the door saw the file being written and waits for a change
            return self.ledger.watch(after, output=output, store_root=store_root)

        watching = mock.patch("cadgen.daemon.client.watch_jobs", side_effect=watch_jobs)
        watching.start()
        self.addCleanup(watching.stop)

    def _export_owed(self):
        """A build's request, and the export job it handed over, which owes the file."""
        request = self.ledger.start(tool="run", subject=str(self.root / "part.py"), store_root=str(self.store))
        job = self.ledger.owe(request, str(self.root / "part.py"), {
            "kind": "export", "model": f"{self.root / 'part.py'}::part", "tree": self.TREE, "state": "b" * 64,
            "step": str(self.document), "name": "part", "expected": {}, "previous": {"key": None, "outputs": {}},
            "displays": [], "meshes": []}, store_root=str(self.store))
        return request, job

    def _door_reads(self) -> tuple[threading.Thread, dict]:
        from cadgen._internal.doors import document_target

        read: dict[str, bytes] = {}

        def door() -> None:
            read["bytes"] = document_target(self.document, suffixes=(".step",)).read_bytes()

        thread = threading.Thread(target=door, daemon=True)
        thread.start()
        return thread, read

    def _write_lands(self, job, thread: threading.Thread) -> None:
        self.assertTrue(self.waiting.wait(30), "the door never waited for the write")
        self.document.write_bytes(b"ISO-10303-21; the file the build wrote")
        self.ledger.published(job, {"output": str(self.document), "tree": self.TREE, "documentHash": "d" * 64})
        thread.join(30)
        self.assertFalse(thread.is_alive(), "the door went on waiting after the file landed")

    def test_a_door_with_no_job_writing_the_file_reads_it_at_once(self):
        from cadgen._internal.doors import document_target

        self.assertEqual(document_target(self.document, suffixes=(".step",)), self.document)
        self.assertEqual(self.asked, [None], "one look at the ledger, and no wait")

    def test_a_door_opened_during_a_slow_write_reads_the_new_bytes(self):
        _request, job = self._export_owed()
        thread, read = self._door_reads()
        self.assertTrue(self.waiting.wait(30))
        self.assertNotIn("bytes", read, "the door read nothing while the file was being written")
        self._write_lands(job, thread)
        self.assertEqual(read["bytes"], b"ISO-10303-21; the file the build wrote")

    def test_a_door_given_a_file_a_build_writes_for_the_first_time_finds_it_written(self):
        self.document.unlink()
        _request, job = self._export_owed()
        thread, read = self._door_reads()
        self._write_lands(job, thread)
        self.assertEqual(read["bytes"], b"ISO-10303-21; the file the build wrote")

    def test_a_door_waits_no_longer_than_the_job_that_owed_the_file(self):
        # The export failed before the file landed: the door reads what is on disk.
        request, job = self._export_owed()
        self.ledger.finish(request, 0)
        thread, read = self._door_reads()
        self.assertTrue(self.waiting.wait(30))
        self.ledger.finish(job, 1, error="the export failed")
        thread.join(30)
        self.assertEqual(read["bytes"], b"ISO-10303-21; the file before the build")

    def test_a_door_inside_the_request_that_owes_the_file_waits_for_its_job_too(self):
        request, job = self._export_owed()
        with mock.patch.dict(os.environ, {"CADGEN_JOB_ID": request["id"]}):
            thread, read = self._door_reads()
            self._write_lands(job, thread)
        self.assertEqual(read["bytes"], b"ISO-10303-21; the file the build wrote")


if __name__ == "__main__":
    unittest.main()
