"""The build feed reports what a build of a STEP file -- named by its absolute path -- is doing, and names
the objects of its preview revision: never geometry."""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from cadgen.viewer.preview import preview_status, preview_update
from tests.python.support.tmp_root import generated_cad_directory


class BuildStatusTests(unittest.TestCase):
    def setUp(self):
        temporary = generated_cad_directory(prefix="preview-feed-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.output = str(self.root / "new.step")
        self.store = str(self.root / "store")
        env = mock.patch.dict(os.environ, {"CADGEN_CACHE_DIR": self.store})
        env.start()
        self.addCleanup(env.stop)

    def job(self, revision=1, **extra):
        return {"id": f"epoch:job-{revision}", "epoch": "epoch", "sequence": revision,
                "tool": "run", "storeRoot": self.store, "outputs": [self.output], "state": "building",
                "subject": "/private/source.py", **extra}

    def test_a_running_build_is_its_state_and_nothing_of_the_job_or_its_geometry(self):
        result = preview_status(self.output, jobs=[self.job(
            phase="tessellating", previews={self.output: {"tree": "a" * 64}})])
        # `file` is the catalog's spelling of the file: its absolute path, with "/".
        self.assertEqual((result["state"], result["phase"], result["file"]),
                         ("building", "tessellating", self.output.replace(os.sep, "/")))
        # The preview revision is names: the tree published, no parts yet, no annotations.
        self.assertEqual(result["preview"], {"tree": "a" * 64, "producer": None, "parts": {}, "complete": False,
                                             "annotations": None})
        for private in ("subject", "storeRoot", "previews", "savedResults", "previewUnavailable", "superseded"):
            self.assertNotIn(private, result)
        self.assertIsNone(result["saved"], "nothing written yet")

    def test_the_saved_file_is_the_feeds_own_event_from_the_moment_it_lands(self):
        """A build records its result (its preview, then `done`) and writes its file after
        (STORE.md §3, *Exports*): the feed names the save the moment the ledger has it,
        whatever the build's state, and the viewer reads the catalog then."""
        building = self.job(previews={self.output: {"tree": "a" * 64}},
                            savedResults={self.output: {"tree": "a" * 64, "documentHash": "d" * 64}})
        result = preview_status(self.output, jobs=[building])
        self.assertEqual((result["state"], result["saved"]), ("building", {"tree": "a" * 64, "documentHash": "d" * 64}))
        # Another output the same build saved is not this file's event.
        other = self.job(savedResults={str(self.root / "other.step"): {"tree": "a" * 64, "documentHash": "d" * 64}})
        self.assertIsNone(preview_status(self.output, jobs=[other])["saved"])

    def test_the_status_names_the_preview_revision_and_its_annotations_by_their_objects(self):
        from cadgen.catalog import artifact_file_hash

        producer = {"build123d": "0.11.1", "ocp": "7.9.3.1", "cadqueryOcp": "7.9.3.1.1"}
        published = self.job(previews={self.output: {"tree": "a" * 64, "producer": producer, "parts": {"c1": "k1"},
                                                     "complete": True}},
                             annotations={self.output: {"tree": "a" * 64, "object": "b" * 64}})
        self.assertEqual(preview_status(self.output, jobs=[published])["preview"],
                         {"tree": "a" * 64, "producer": producer, "parts": {"c1": "k1"}, "complete": True,
                          "annotations": "b" * 64})
        # Annotations resolved against another tree are not this preview's.
        stale = self.job(previews={self.output: {"tree": "a" * 64}},
                         annotations={self.output: {"tree": "c" * 64, "object": "b" * 64}})
        self.assertIsNone(preview_status(self.output, jobs=[stale])["preview"]["annotations"])
        # Before the result exists there is no revision.
        self.assertIsNone(preview_status(self.output, jobs=[self.job()])["preview"])
        # A finished build keeps its preview until the file moves past it, when it is dropped.
        Path(self.output).write_bytes(b"saved document")
        done = self.job(state="done", finishedAt=2000.0, previews={self.output: {"tree": "a" * 64}},
                        savedResults={self.output: {"tree": "d" * 64, "documentHash": artifact_file_hash(Path(self.output))}})
        self.assertEqual(preview_status(self.output, jobs=[done])["preview"]["tree"], "a" * 64)
        Path(self.output).write_bytes(b"written by another build")
        moved = preview_status(self.output, jobs=[done])
        self.assertTrue(moved["superseded"])
        self.assertIsNone(moved["preview"])

    def test_changed_feed_uses_its_atomic_job_snapshot_and_only_exposes_cursor(self):
        payload = {"jobsCursor": "epoch:8", "jobs": [self.job()]}
        with mock.patch("cadgen.daemon.client.watch_jobs", return_value=payload) as watch, \
                mock.patch("cadgen.viewer.preview._daemon_jobs", side_effect=AssertionError("extra poll")):
            result = preview_update(self.output, after="epoch:7")
        watch.assert_called_once_with("epoch:7", output=self.output, store_root=self.store)
        self.assertEqual((result["feedCursor"], result["revision"]), ("epoch:8", 1))
        self.assertNotIn("jobs", result)

    def test_saturated_feed_keeps_cursor_and_requests_slow_client_retry(self):
        payload = {"jobsCursor": "epoch:8", "jobs": [], "jobsWatchLimited": True}
        with mock.patch("cadgen.daemon.client.watch_jobs", return_value=payload):
            result = preview_update(self.output, after="epoch:8")
        self.assertEqual(result["feedCursor"], "epoch:8")
        self.assertTrue(result["feedLimited"])

    def test_invalid_output_is_rejected_before_waiting_on_the_daemon(self):
        with mock.patch("cadgen.daemon.client.watch_jobs", side_effect=AssertionError("invalid wait")):
            for ref in ("new.step", str(self.root / "new.stl")):
                with self.subTest(ref=ref), self.assertRaises(ValueError):
                    preview_update(ref, after="epoch:7")

    def test_an_output_under_a_hidden_folder_is_reported(self):
        # A file named by its absolute path is never refused for where it is: an agent's worktree
        # under a dot-folder still gets its build's status.
        hidden = str(self.root / ".worktree" / "new.step")
        job = {**self.job(), "outputs": [hidden]}
        self.assertEqual(preview_status(hidden, jobs=[job])["state"], "building")

    def test_newest_request_wins_even_when_old_one_finishes_later(self):
        jobs = [self.job(1, state="done", updatedAt=1000), self.job(2, updatedAt=999)]
        self.assertEqual(preview_status(self.output, jobs=jobs)["revision"], 2)

    def test_compiling_saved_bytes_cannot_supersede_an_editing_request(self):
        jobs = [self.job(1), self.job(2, tool="step-compile", state="done")]
        self.assertEqual(preview_status(self.output, jobs=jobs)["revision"], 1)

    def test_other_store_and_output_are_not_visible(self):
        result = preview_status(self.output, jobs=[
            self.job(storeRoot=str(self.root / "other")),
            self.job(outputs=[str(self.root / "other.step")]),
        ])
        self.assertEqual(result["state"], "disconnected")

    def test_save_failure_is_a_recorded_result_for_this_output_not_merely_a_preview(self):
        from cadgen.daemon.jobs import JobLedger

        script = str(self.root / "source.py")
        tree = "a" * 64
        for preview in (None, {"tree": tree, "parts": {"c1": "part"}, "complete": False}):
            with self.subTest(preview=bool(preview)):
                ledger = JobLedger()
                request = ledger.start(tool="run", subject=script, store_root=self.store)
                def event(sequence, **extra):
                    ledger.observe({"event": {"job": request["id"], "model": script,
                                               "sequence": sequence, "state": "building", **extra}})
                if preview:
                    event(1, preview={"output": self.output, **preview})
                    ledger.finish(request, 1, error="invalid animation declaration")
                    invalid = preview_status(self.output, jobs=ledger.snapshot())
                    self.assertEqual(invalid["failureStage"], "build")
                    self.assertNotIn("saveFailure", invalid)
                    self.assertEqual(invalid["preview"]["parts"], {"c1": "part"})
                    request = ledger.start(tool="run", subject=script, store_root=self.store)
                    event(1, preview={"output": self.output, **preview})
                event(2, saving={"output": self.output, "tree": tree})
                pending = preview_status(self.output, jobs=ledger.snapshot())
                self.assertEqual(pending["saving"], {"tree": tree})
                ledger.finish(request, 1, error="disk full")
                failed = preview_status(self.output, jobs=ledger.snapshot())
                self.assertEqual((failed["state"], failed["failureStage"], failed["error"]),
                                 ("failed", "save", "disk full"))
                self.assertEqual(failed["saveFailure"], {"output": self.output, "tree": tree})
                self.assertNotIn("saving", failed)
                self.assertEqual(failed["preview"] is not None, preview is not None)

    def test_a_failed_mesh_save_does_not_fail_the_step_that_landed_in_the_same_run(self):
        from cadgen.catalog import artifact_file_hash

        mesh = str(self.root / "new.stl")
        Path(self.output).write_bytes(b"saved STEP")
        failed = self.job(state="failed", error="mesh write refused", finishedAt=2000.0,
                          savedResults={self.output: {"tree": "a" * 64, "documentHash": artifact_file_hash(Path(self.output))}},
                          saveFailures={mesh: {"tree": "a" * 64}})
        step = preview_status(self.output, jobs=[failed])
        self.assertEqual((step["state"], step["error"]), ("done", None))
        self.assertNotIn("failureStage", step)
        self.assertNotIn("saveFailure", step)

    def test_parent_and_children_report_their_own_failed_outputs_after_the_request_ends(self):
        from cadgen.catalog import artifact_file_hash
        from cadgen.daemon.jobs import JobLedger

        now = 1000.0
        ledger = JobLedger(clock=lambda: now)
        parent = str(self.root / "assembly.py")
        child = str(self.root / "child.py")
        child_step = str(self.root / "child.step")
        child_mesh = str(self.root / "child.stl")
        child_failed_step = str(self.root / "other.step")
        request = ledger.start(tool="run", subject=parent, store_root=self.store)
        def event(model, sequence, state="building", **extra):
            ledger.observe({"event": {"job": request["id"], "model": model, "sequence": sequence,
                                       "state": state, **extra}})
        event(parent, 1, saving={"output": self.output, "tree": "parent"})
        event(child, 1, saving={"output": child_step, "tree": "child"})
        event(child, 2, saving={"output": child_mesh, "tree": "child"})
        event(child, 3, saving={"output": child_failed_step, "tree": "child"})
        event(child, 4, state="done")
        # A child closes at result. An intervening write its save refuses is older than
        # the save failure, so it must not hide that failure as though it happened later.
        Path(child_failed_step).write_bytes(b"edited while exporting")
        os.utime(child_failed_step, (1001.0, 1001.0))
        Path(child_step).write_bytes(b"saved child")
        event(child, 5, saved={"output": child_step, "tree": "child", "documentHash": artifact_file_hash(Path(child_step))})
        now = 1002.0
        ledger.finish(request, 1, error="destination changed")
        jobs = ledger.snapshot()
        root = preview_status(self.output, jobs=jobs)
        unsaved_child = preview_status(child_failed_step, jobs=jobs)
        self.assertEqual(root["saveFailure"], {"output": self.output, "tree": "parent"})
        self.assertEqual(unsaved_child["saveFailure"], {"output": child_failed_step, "tree": "child"})
        self.assertNotIn("superseded", unsaved_child)
        self.assertEqual(preview_status(child_step, jobs=jobs)["state"], "done")
        os.utime(child_failed_step, (1003.0, 1003.0))
        self.assertTrue(preview_status(child_failed_step, jobs=jobs)["superseded"])

    def test_a_superseding_result_and_later_file_replacement_clear_a_save_failure(self):
        failed = self.job(state="failed", error="disk full", finishedAt=2000.0,
                          previews={self.output: {"tree": "a" * 64}},
                          saveFailures={self.output: {"tree": "a" * 64}})
        self.assertEqual(preview_status(self.output, jobs=[failed])["failureStage"], "save")
        superseded = preview_status(self.output, jobs=[{**failed, "supersededOutputs": [self.output]}])
        Path(self.output).write_bytes(b"replaced")
        os.utime(self.output, (3000.0, 3000.0))
        replaced = preview_status(self.output, jobs=[failed])
        for result in (superseded, replaced):
            self.assertTrue(result["superseded"])
            self.assertIsNone(result["preview"])
            self.assertIsNone(result["error"])
            self.assertNotIn("failureStage", result)
            self.assertNotIn("saveFailure", result)

    # The file moved on after the build: another installation's build, a checkout, a STEP written
    # by hand. The build's failure is no longer the news.
    def test_a_build_that_saved_is_moved_past_when_the_file_is_no_longer_its_bytes(self):
        from cadgen.catalog import artifact_file_hash

        Path(self.output).write_bytes(b"saved document")
        digest = artifact_file_hash(Path(self.output))
        done = self.job(state="done", finishedAt=2000.0, savedResults={self.output: {"tree": "a" * 64, "documentHash": digest}})
        self.assertNotIn("superseded", preview_status(self.output, jobs=[done]))
        Path(self.output).write_bytes(b"written by another build")
        moved = preview_status(self.output, jobs=[done])
        self.assertTrue(moved["superseded"])
        self.assertIsNone(moved["error"])

    def test_a_build_that_saved_nothing_is_moved_past_by_a_later_write(self):
        Path(self.output).write_bytes(b"before the build")
        os.utime(self.output, (1000.0, 1000.0))
        failed = self.job(state="failed", error="Disk full", finishedAt=2000.0)
        kept = preview_status(self.output, jobs=[failed])
        self.assertEqual(kept["error"], "Disk full")
        self.assertNotIn("superseded", kept)
        os.utime(self.output, (3000.0, 3000.0))
        moved = preview_status(self.output, jobs=[failed])
        self.assertTrue(moved["superseded"])
        self.assertIsNone(moved["error"])
        # A failed first build never wrote the file: its failure stands.
        os.remove(self.output)
        self.assertEqual(preview_status(self.output, jobs=[failed])["error"], "Disk full")
        # A build still running is never moved past: it writes the file next.
        self.assertNotIn("superseded", preview_status(self.output, jobs=[self.job()]))


if __name__ == "__main__":
    unittest.main()
