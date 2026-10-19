"""The daemon's job ledger (``cadgen.daemon.jobs``): every job, whoever asked.

A job is listed with its declared output paths (from the script's decorators,
parsed statically; the document itself for a compile), follows the build tree's
event frames through submitted → building [phase n/total] → done | failed, and
stays listed for a while after it finishes so a failure is still visible.
"""

from __future__ import annotations

import json
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support.paths import add_repo_path

add_repo_path("packages/cadgen/src")

from cadgen.daemon.jobs import JobLedger, declared_outputs, failure_message  # noqa: E402

MODEL = """
from cadgen import step, stl
from cadgen import build123d as bd


@stl(out="../MESH/widget.stl")
@step(out="../STEP/widget.step")
def widget():
    return bd.Box(1, 1, 1)
"""


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class DeclaredOutputs(unittest.TestCase):
    def test_named_model_and_multi_model_request_declare_real_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "models.py"
            script.write_text(
                "from cadgen import step\n@step\ndef first(): pass\n@step(out='second.step')\ndef second(): pass\n",
                encoding="utf-8",
            )
            self.assertEqual(declared_outputs(f"{script}::first"), [str((Path(tmp) / "first.step").resolve())])
            self.assertEqual(declared_outputs(str(script)), [str((Path(tmp) / "first.step").resolve()), str((Path(tmp) / "second.step").resolve())])

    def test_a_model_scripts_outputs_are_its_declared_document_and_meshes(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "src" / "widget.py"
            script.parent.mkdir()
            script.write_text(textwrap.dedent(MODEL), encoding="utf-8")
            outputs = declared_outputs(str(script))
        self.assertEqual(
            [str((Path(tmp) / "STEP" / "widget.step").resolve()), str((Path(tmp) / "MESH" / "widget.stl").resolve())],
            [str(Path(p)) for p in outputs],
        )

    def test_a_sibling_default_and_a_compiles_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "plain.py"
            script.write_text("from cadgen import step\nfrom cadgen import build123d as bd\n\n@step\ndef plain():\n    return bd.Box(1, 1, 1)\n", encoding="utf-8")
            self.assertEqual([str(script.with_suffix(".step").resolve())], [str(Path(p)) for p in declared_outputs(str(script))])
            document = Path(tmp) / "vendor.step"
            self.assertEqual([str(document.resolve())], [str(Path(p)) for p in declared_outputs(str(document))])

    def test_an_unparseable_script_declares_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "broken.py"
            script.write_text("def (\n", encoding="utf-8")
            self.assertEqual([], declared_outputs(str(script)))

    def test_outputs_come_from_the_text_and_the_build_never_from_an_import(self):
        # The supervisor reads declarations in its own process, so a model's module top
        # must never run there: a fault at the top would take the daemon and every build
        # on it down (STORE.md §9, "importing the supervisor never loads the kernel").
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            marker = root / "imported.marker"
            script = root / "part.py"
            script.write_text(
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('the module top ran')\n"
                "from cadgen import step, stl\nfrom cadgen import build123d as bd\n\n"
                "@stl\n@step(out='STEP/part.step')\ndef part():\n    return bd.Box(1, 1, 1)\n",
                encoding="utf-8",
            )
            expected = [str((root / "STEP" / "part.step").resolve()), str((root / "STEP" / "part.stl").resolve())]
            self.assertEqual(expected, [str(Path(p)) for p in declared_outputs(str(script))])
            self.assertFalse(marker.exists(), "the supervisor's parse ran the module top")

    def test_an_output_only_an_import_can_evaluate_is_named_by_the_build(self):
        # `out=NAME + ".step"` is unknown to the text alone: the worker names it in the
        # model's first event, the row lists it from then on, and the subject's next run
        # is listed against it from the start -- without the supervisor ever importing
        # the helper the script reaches through its caller's PYTHONPATH.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            script = root / "a.py"
            script.write_text(
                "from cadgen import step\nfrom cadgen import build123d as bd\nfrom extlib.names import NAME\n\n"
                "@step(out=NAME + '.step')\ndef a():\n    return bd.Box(1, 1, 1)\n",
                encoding="utf-8",
            )
            named = str((root / "out" / "a_named.step").resolve())
            ledger = JobLedger(clock=Clock())
            first = ledger.start(tool="run", subject=str(script))
            self.assertEqual([], first["outputs"], "the text alone cannot evaluate NAME")
            ledger.observe({"event": {"model": str(script), "state": "building", "phase": "generate",
                                      "job": first["id"], "outputs": [named]}})
            self.assertEqual([named], first["outputs"])
            ledger.finish(first, 0)
            second = ledger.start(tool="run", subject=str(script))
            self.assertEqual([named], second["outputs"])

    def test_a_script_that_stops_parsing_is_listed_against_what_it_declared_last(self):
        # An edit that breaks the script fails the build at import, and its text no longer
        # parses: the job is still listed against the document the script writes, so the
        # viewer showing that document gets the failure, not silence.
        from cadgen.viewer.build_progress import build_progress_snapshot

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            script = root / "part.py"
            script.write_text(
                "from cadgen import step\nfrom cadgen import build123d as bd\n\n"
                "@step\ndef part():\n    return bd.Box(2, 2, 2)\n",
                encoding="utf-8",
            )
            output = str(script.with_suffix(".step").resolve())
            clock = Clock()
            ledger = JobLedger(clock=clock)
            built = ledger.start(tool="run", subject=str(script))
            self.assertEqual([output], built["outputs"])
            ledger.finish(built, 0)

            script.write_text("from cadgen import step\n\n@step\ndef part(:\n", encoding="utf-8")
            clock.now += 1
            broken = ledger.start(tool="run", subject=str(script))
            self.assertEqual([output], broken["outputs"])
            ledger.finish(broken, 1, error="SyntaxError: invalid syntax")
            status = build_progress_snapshot(output, jobs=ledger.snapshot())
            self.assertEqual("SyntaxError: invalid syntax", status["failed"]["error"])

            # A daemon that never read the script has nothing to list it against.
            self.assertEqual([], JobLedger(clock=Clock()).start(tool="run", subject=str(script))["outputs"])


class Lifecycle(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.ledger = JobLedger(retain_seconds=60.0, clock=self.clock)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.model = str((Path(self.tmp.name) / "widget.py").resolve())
        Path(self.model).write_text("from cadgen import step\nfrom cadgen import build123d as bd\n\n@step\ndef widget():\n    return bd.Box(1, 1, 1)\n", encoding="utf-8")

    def _event(self, model, state, **extra):
        return {"event": {"model": model, "state": state, **extra}}

    def test_simultaneous_requests_for_same_model_remain_distinct(self):
        first = self.ledger.start(tool="run", subject=self.model, store_root="/one")
        second = self.ledger.start(tool="run", subject=self.model, store_root="/two")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(self.ledger.snapshot()), 2)
        self.ledger.observe(self._event(self.model, "building", job=first["id"], phase="first only"))
        self.assertEqual(first["phase"], "first only")
        self.assertIsNone(second["phase"])
        self.assertNotEqual(first["storeRoot"], second["storeRoot"])

    def test_producer_updates_are_ordered_and_snapshot_is_not_mutable(self):
        job = self.ledger.start(tool="run", subject=self.model)
        output = str(Path(self.model).with_suffix(".step"))
        for sequence, tree in ((4, "latest"), (2, "late")):
            self.ledger.observe(self._event(self.model, "building", job=job["id"], sequence=sequence,
                                           preview={"output": output, "tree": tree, "kinematics": {"mates": []}}))
        snapshot = self.ledger.snapshot()[0]
        self.assertEqual(snapshot["previews"][output]["tree"], "latest")
        snapshot["previews"][output]["tree"] = "mutation"
        self.assertEqual(self.ledger.snapshot()[0]["previews"][output]["tree"], "latest")
        self.ledger.observe(self._event(self.model, "done", job=job["id"]))
        self.assertEqual(job["state"], "building", "one model completing does not finish a multi-model request")
        self.ledger.finish(job, 0)
        self.assertEqual(job["state"], "done")
        self.ledger.observe(self._event(self.model, "building", job=job["id"], sequence=100))
        self.assertEqual(job["state"], "done", "late forwarded events cannot reopen completed requests")

    def test_a_job_keeps_only_what_its_readers_read_of_a_result(self):
        # The viewer's status reads the names of a build's results: of a preview its tree,
        # the producer its parts are keyed under, the stored part keys and whether they are
        # all of them; of its annotations the one object; of a saved result its tree and
        # digest. The content of any of them rides in none: a payload carrying it keeps none.
        job = self.ledger.start(tool="run", subject=self.model)
        output = str(Path(self.model).with_suffix(".step"))
        self.ledger.observe(self._event(self.model, "building", job=job["id"], sequence=1, preview={
            "output": output, "tree": "source", "producer": {"scheme": 19}, "parts": {"c1": "k1"}, "complete": False,
            "kinematics": {"mates": [1] * 1000}, "appearance": {}, "animation": "export const clips = {};"}))
        self.ledger.observe(self._event(self.model, "building", job=job["id"], sequence=2, annotations={
            "output": output, "tree": "source", "object": "ann", "articulation": {"joints": [1] * 1000}}))
        self.ledger.observe(self._event(self.model, "building", job=job["id"], sequence=3, saved={
            "output": output, "tree": "document", "documentHash": "abc", "appearance": {"materials": {}}}))
        snapshot = self.ledger.snapshot()[0]
        # Each with the ledger's revision when it landed: the order of results across rows.
        self.assertEqual(snapshot["previews"][output], {"output": output, "tree": "source", "producer": {"scheme": 19},
                                                        "parts": {"c1": "k1"}, "complete": False, "sequence": 1,
                                                        "revision": 2})
        self.assertEqual(snapshot["annotations"][output],
                         {"output": output, "tree": "source", "object": "ann", "sequence": 2, "revision": 3})
        self.assertEqual(snapshot["savedResults"][output],
                         {"output": output, "tree": "document", "documentHash": "abc", "sequence": 3, "revision": 4})

    def test_a_childs_preview_is_its_own_rows_and_epochs_are_unique(self):
        parent = self.ledger.start(tool="run", subject=self.model)
        child_path = str(Path(self.model).with_name("child.py"))
        self.ledger.observe(self._event(child_path, "building", job=parent["id"],
                                       preview={"output": "child.step", "tree": "child"}))
        self.assertNotIn("previews", parent)
        child = next(job for job in self.ledger.snapshot() if job["subject"] == child_path)
        self.assertEqual(child["previews"][str(Path("child.step").resolve())]["tree"], "child")
        self.assertNotEqual(self.ledger.epoch, JobLedger().epoch)

    def test_a_job_follows_its_event_frames_to_done(self):
        job = self.ledger.start(tool="run", subject=self.model, argv=[self.model])
        self.assertEqual("submitted", job["state"])
        self.assertEqual([str(Path(self.model).with_suffix(".step"))], job["outputs"])
        self.ledger.observe(self._event(
            self.model, "building", phase="Storing parts", detail="finger linkage", done=3, total=9,
        ))
        listed = self.ledger.snapshot()[0]
        self.assertEqual(("building", "Storing parts", 3, 9), (listed["state"], listed["phase"], listed["done"], listed["total"]))
        self.assertEqual("finger linkage", listed["detail"])
        self.ledger.observe(self._event(self.model, "done"))
        self.ledger.finish(job, 0)
        listed = self.ledger.snapshot()[0]
        self.assertEqual(("done", 0), (listed["state"], listed["exit"]))

    def test_a_job_waiting_for_a_worker_says_so_until_it_has_one(self):
        # Nothing the job runs can say this: its worker does not exist yet.
        job = self.ledger.start(tool="run", subject=self.model)
        self.ledger.waiting(job, "Starting a geometry kernel")
        listed = self.ledger.snapshot()[0]
        self.assertEqual(("queued", "queued", "Starting a geometry kernel"),
                         (listed["state"], listed["phase"], listed["detail"]))
        self.ledger.waiting(job, None)
        self.ledger.observe(self._event(self.model, "building", job=job["id"], phase="generate"))
        listed = self.ledger.snapshot()[0]
        self.assertEqual(("building", "generate", ""), (listed["state"], listed["phase"], listed["detail"]))

    def test_a_non_zero_exit_is_a_failed_job(self):
        job = self.ledger.start(tool="run", subject=self.model)
        self.ledger.observe(self._event(self.model, "building", phase="generate"))
        self.ledger.finish(job, 1)
        self.assertEqual(("failed", 1), (self.ledger.snapshot()[0]["state"], self.ledger.snapshot()[0]["exit"]))

    def test_a_job_whose_requester_left_is_cancelled_and_the_document_builds_again(self):
        # The viewer that asked for a compile was closed mid-job: the daemon killed the
        # worker. That is no verdict on the document, so a viewer opened next compiles it
        # rather than reporting "the last compile of this document failed".
        from cadgen.viewer.build_progress import build_progress_snapshot

        document = str((Path(self.tmp.name) / "widget.step").resolve())
        job = self.ledger.start(tool="step-compile", subject=document)
        self.ledger.finish(job, 1, error="worker killed", cancelled=True)
        listed = self.ledger.snapshot()[0]
        self.assertEqual(("cancelled", None), (listed["state"], listed["error"]))
        self.assertIsNone(build_progress_snapshot(document, jobs=self.ledger.snapshot()))

    def test_a_child_built_inside_a_request_is_a_row_of_its_own_under_it(self):
        parent = str((Path(self.tmp.name) / "rig.py").resolve())
        request = self.ledger.start(tool="run", subject=parent, store_root=self.tmp.name)
        # The parent's worker builds the child inside its body: its events carry the
        # parent's job id (STORE.md §9a), and make the child a row under the request.
        before = self.ledger.watch(timeout=0)
        self.ledger.observe(self._event(self.model, "building", job=request["id"], sequence=1,
                                       parent=parent, phase="generate"))
        rows = self.ledger.snapshot()
        self.assertEqual([parent, self.model], [job["subject"] for job in rows])
        child = rows[1]
        self.assertEqual((child["request"], child["storeRoot"], child["tool"]),
                         (request["id"], str(Path(self.tmp.name).resolve()), "run"))
        self.assertEqual([str(Path(self.model).with_suffix(".step"))], child["outputs"])
        self.assertEqual(("building", "generate"), (child["state"], child["phase"]))
        self.assertEqual(request["state"], "submitted", "a child's transition is not the request's")
        after = self.ledger.watch(before["jobsCursor"], timeout=0)
        self.assertNotEqual(before["jobsCursor"], after["jobsCursor"])
        # The child's own done closes its row; its saved result, landing after, folds
        # into the closed row without reopening it.
        self.ledger.observe(self._event(self.model, "done", job=request["id"], sequence=2, parent=parent))
        child = self.ledger.snapshot()[1]
        self.assertEqual("done", child["state"])
        self.assertIsNotNone(child["finishedAt"])
        output = str(Path(self.model).with_suffix(".step"))
        self.ledger.observe(self._event(self.model, "building", job=request["id"], sequence=3, parent=parent,
                                       saved={"output": output, "tree": "t" * 64, "documentHash": "d" * 64}))
        child = self.ledger.snapshot()[1]
        self.assertEqual("done", child["state"])
        self.assertEqual(child["savedResults"][output]["tree"], "t" * 64)
        self.ledger.finish(request, 0)
        self.assertEqual(["done", "done"], [job["state"] for job in self.ledger.snapshot()])

    def test_a_child_in_the_requests_own_file_is_a_row_of_its_own_too(self):
        """Rows are keyed by model, not by script: ``pair.py::part`` built inside
        ``pair.py::asm`` is a child row under the request, while the request's own model
        -- spelled with its function, or bare as a file's sole model is -- is its row."""
        pair = (Path(self.tmp.name) / "pair.py").resolve()
        pair.write_text("from cadgen import step\nfrom cadgen import build123d as bd\n\n@step\ndef part():\n"
                        "    return bd.Box(1, 1, 1)\n\n@step\ndef asm():\n    return part()\n", encoding="utf-8")
        request = self.ledger.start(tool="run", subject=f"{pair}::asm", store_root=self.tmp.name)
        self.ledger.observe(self._event(f"{pair}::asm", "building", job=request["id"], sequence=1, phase="generate"))
        self.ledger.observe(self._event(f"{pair}::part", "building", job=request["id"], sequence=1, phase="generate"))
        rows = self.ledger.snapshot()
        self.assertEqual([f"{pair}::asm", f"{pair}::part"], [job["subject"] for job in rows])
        self.assertEqual(("building", "generate"), (rows[0]["state"], rows[0]["phase"]))
        self.assertEqual(request["id"], rows[1]["request"])
        sole = self.ledger.start(tool="run", subject=f"{self.model}::widget")
        self.ledger.observe(self._event(self.model, "building", job=sole["id"], sequence=1, phase="generate"))
        rows = self.ledger.snapshot()
        self.assertEqual(3, len(rows), "a sole model spelled bare is the request's own")
        self.assertEqual("building", rows[2]["state"])

    def test_a_requests_end_closes_the_child_rows_it_left_running(self):
        parent = str((Path(self.tmp.name) / "rig.py").resolve())
        request = self.ledger.start(tool="run", subject=parent)
        self.ledger.observe(self._event(self.model, "building", job=request["id"], sequence=1, parent=parent))
        # The worker died mid-child: nothing will ever close the child's row but the request.
        self.ledger.finish(request, 1, error="worker 42 died")
        child = self.ledger.snapshot()[1]
        self.assertEqual(("failed", 1, "worker 42 died"), (child["state"], child["exit"], child["error"]))
        self.assertIsNotNone(child["finishedAt"])

    def test_finished_jobs_are_retained_then_swept(self):
        job = self.ledger.start(tool="step-compile", subject=str(Path(self.tmp.name) / "vendor.step"))
        self.ledger.finish(job, 1)
        self.assertEqual(1, len(self.ledger.snapshot()))
        self.clock.now += 61.0
        self.assertEqual([], self.ledger.snapshot())

    # --- what a build owes once its result is recorded ---------------------------------

    TREE, OTHER, PRODUCER = "a" * 64, "b" * 64, {"build123d": "0.11", "ocp": "7.9", "cadqueryOcp": "7.9.1"}

    def _export(self, model, outputs, tree=TREE):
        """An export job's request for ``model`` writing ``outputs`` (the STEP first)."""
        step, *meshes = outputs
        return {"kind": "export", "model": f"{model}::widget", "tree": tree, "state": "c" * 64, "step": step,
                "name": "widget", "expected": {}, "previous": {"key": None, "outputs": {}}, "displays": [],
                "meshes": [{"fmt": "stl", "path": mesh, "chord": None, "angle": None} for mesh in meshes]}

    def _display(self, cids, tree=TREE):
        return {"kind": "display", "tree": tree, "cids": list(cids), "producer": self.PRODUCER}

    def _owe(self, request, model, artifact):
        return self.ledger.owe(request, model, artifact, store_root=request["storeRoot"])

    def _saved(self, job, output, tree=TREE):
        self.ledger.published(job, {"output": output, "tree": tree, "documentHash": "d" * 64})

    def test_a_file_a_result_owes_is_being_written_until_its_job_saves_it(self):
        from cadgen.daemon.jobs import being_written

        request = self.ledger.start(tool="run", subject=self.model)
        output = str(Path(self.model).with_suffix(".step"))
        self.assertFalse(being_written(self.ledger.snapshot(), output))
        job = self._owe(request, self.model, self._export(self.model, [output]))
        self.assertTrue(being_written(self.ledger.snapshot(), output))
        self._saved(job, output)
        self.assertFalse(being_written(self.ledger.snapshot(), output))
        self.assertEqual(self.ledger.snapshot()[0]["savedResults"][output]["documentHash"], "d" * 64)

    def test_a_request_that_ended_well_stays_open_until_the_jobs_it_handed_over_end(self):
        """Its process exits with its answer; the row reads as building, and is listed, until
        the last job its rows owe has ended -- then done."""
        from cadgen.daemon.jobs import being_written

        request = self.ledger.start(tool="run", subject=self.model)
        self.ledger.observe(self._event(self.model, "building", job=request["id"], sequence=1, phase="Saving STEP"))
        output = str(Path(self.model).with_suffix(".step"))
        job = self._owe(request, self.model, self._export(self.model, [output]))
        self.ledger.finish(request, 0)
        self.assertEqual(("building", None, None), (request["state"], request["exit"], request["finishedAt"]))
        self.clock.now += 600.0  # a STEP of hundreds of megabytes
        self.assertTrue(being_written(self.ledger.snapshot(), output))
        self._saved(job, output)
        self.ledger.finish(job, 0)
        self.assertEqual(("done", 0), (request["state"], request["exit"]))
        self.assertIsNotNone(request["finishedAt"])

    def test_only_the_newest_result_that_owes_a_file_is_waited_for(self):
        # Two edits in a row: the older run's export is skipped once the newer result is
        # recorded, so the newer one's save is the end of the wait.
        from cadgen.daemon.jobs import being_written

        output = str(Path(self.model).with_suffix(".step"))
        older = self.ledger.start(tool="run", subject=self.model)
        newer = self.ledger.start(tool="run", subject=self.model)
        older_job = self._owe(older, self.model, self._export(self.model, [output], tree=self.OTHER))
        newer_job = self._owe(newer, self.model, self._export(self.model, [output]))
        self.assertTrue(being_written(self.ledger.snapshot(), output))
        self._saved(newer_job, output)
        self.assertFalse(being_written(self.ledger.snapshot(), output), "the older run's export is no wait")
        self.ledger.finish(newer_job, 0)
        self.ledger.finish(newer, 0)
        self.assertFalse(being_written(self.ledger.snapshot(), output), "finishing the newer request cannot revive the older write")
        self.ledger.finish(older_job, 0)  # superseded: it wrote nothing, and fails nothing
        self.assertNotIn("saveFailures", older)
        self.clock.now += 121
        self.assertFalse(being_written(self.ledger.snapshot(), output), "expiry cannot revive it either")

    def test_a_newer_failed_write_does_not_revive_a_superseded_write(self):
        from cadgen.daemon.jobs import being_written

        output = str(Path(self.model).with_suffix(".step"))
        older = self.ledger.start(tool="run", subject=self.model)
        newer = self.ledger.start(tool="run", subject=self.model)
        self._owe(older, self.model, self._export(self.model, [output], tree=self.OTHER))
        newer_job = self._owe(newer, self.model, self._export(self.model, [output]))
        self.ledger.finish(newer_job, 1, error="publish refused")
        self.assertFalse(being_written(self.ledger.snapshot(), output))

    def test_a_failed_job_fails_each_file_it_did_not_save_never_a_preview_alone(self):
        """A save failure is per output: a mesh the job could not write fails, the STEP it
        saved first stays saved. A build that failed with only a preview owes no save."""
        output = str(Path(self.model).with_suffix(".step"))
        early = self.ledger.start(tool="run", subject=self.model)
        self.ledger.observe(self._event(self.model, "building", job=early["id"], sequence=1,
                                       preview={"output": output, "tree": "provisional"}))
        self.ledger.finish(early, 1, error="invalid mate declaration")
        self.assertNotIn("saveFailures", early, "a preview can exist before publication checks pass")

        request = self.ledger.start(tool="run", subject=self.model)
        mesh = str(Path(output).with_suffix(".stl"))
        job = self._owe(request, self.model, self._export(self.model, [output, mesh]))
        self._saved(job, output)
        self.ledger.finish(request, 0)
        self.ledger.finish(job, 1, error="mesh destination is read-only")
        self.assertNotIn("saving", request, "the request writes nothing more")
        self.assertEqual(set(request["saveFailures"]), {mesh})
        self.assertEqual(request["saveFailures"][mesh]["tree"], self.TREE)
        self.assertEqual(request["savedResults"][output]["tree"], self.TREE)
        self.assertEqual((request["state"], request["exit"], request["error"]),
                         ("failed", 1, "mesh destination is read-only"))

    def test_newer_results_supersede_retained_save_failures_only_in_their_store(self):
        output = str(Path(self.model).with_suffix(".step"))
        older = self.ledger.start(tool="run", subject=self.model, store_root="/one")
        self.ledger.finish(self._owe(older, self.model, self._export(self.model, [output], tree=self.OTHER)), 1,
                           error="disk full")
        other = self.ledger.start(tool="run", subject=self.model, store_root="/two")
        self._owe(other, self.model, self._export(self.model, [output]))
        self.assertNotIn("supersededOutputs", older)
        newer = self.ledger.start(tool="run", subject=self.model, store_root="/one")
        job = self._owe(newer, self.model, self._export(self.model, [output]))
        self.assertEqual(older["supersededOutputs"], [output])
        self.assertTrue(older["saveFailures"][output]["superseded"])
        self._saved(job, output)
        self.ledger.finish(job, 0)
        self.ledger.finish(newer, 0)
        self.assertNotIn("saveFailures", newer, "a successful retry carries no old failure")

    def test_a_cancelled_request_still_saves_what_its_jobs_write(self):
        """Its caller left: the request ends cancelled, and the job it handed over goes on --
        the result is recorded, and its file is the result's."""
        from cadgen.daemon.jobs import being_written

        request = self.ledger.start(tool="run", subject=self.model)
        output = str(Path(self.model).with_suffix(".step"))
        job = self._owe(request, self.model, self._export(self.model, [output]))
        self.ledger.finish(request, 1, error="client left", cancelled=True)
        self.assertEqual(request["state"], "cancelled")
        self.assertTrue(being_written(self.ledger.snapshot(), output))
        self._saved(job, output)
        self.ledger.finish(job, 0)
        self.assertEqual(request["state"], "cancelled")
        self.assertNotIn("saveFailures", request)
        self.assertIn(output, request["savedResults"])

    def test_a_failed_job_fails_the_closed_child_row_that_owed_its_file(self):
        # A child's row closes at its own done, before its file is written; its job failing
        # after is its failure, and the file's reader sees it on the child's row. A sibling
        # whose file landed stays done; the request fails with the reason when it closes.
        parent = str((Path(self.tmp.name) / "rig.py").resolve())
        request = self.ledger.start(tool="run", subject=parent)
        output = str(Path(self.model).with_suffix(".step"))
        self.ledger.observe(self._event(self.model, "building", job=request["id"], sequence=1, parent=parent))
        child_job = self._owe(request, self.model, self._export(self.model, [output]))
        self.ledger.observe(self._event(self.model, "done", job=request["id"], sequence=2, parent=parent))
        sibling = str((Path(self.tmp.name) / "pin.py").resolve())
        sibling_step = str(Path(sibling).with_suffix(".step"))
        self.ledger.observe(self._event(sibling, "building", job=request["id"], sequence=3, parent=parent))
        sibling_job = self._owe(request, sibling, self._export(sibling, [sibling_step], tree=self.OTHER))
        self.ledger.observe(self._event(sibling, "done", job=request["id"], sequence=4, parent=parent))
        self._saved(sibling_job, sibling_step, tree=self.OTHER)
        self.ledger.finish(sibling_job, 0)
        self.ledger.finish(request, 0)
        self.ledger.finish(child_job, 1, error="the STEP file or its annotations changed during the build")
        rows = {job["subject"]: job for job in self.ledger.snapshot() if job["tool"] == "run"}
        self.assertEqual(("failed", "the STEP file or its annotations changed during the build"),
                         (rows[self.model]["state"], rows[self.model]["error"]))
        self.assertEqual(("done", None), (rows[sibling]["state"], rows[sibling]["error"]),
                         "a row whose file landed stays done")
        self.assertEqual(rows[self.model]["saveFailures"][output]["tree"], self.TREE)
        self.assertNotIn("saveFailures", rows[sibling])
        self.assertEqual(("failed", 1), (rows[parent]["state"], rows[parent]["exit"]))

    def test_a_row_still_owing_a_file_is_kept_until_its_job_ends(self):
        from cadgen.daemon.jobs import being_written

        parent = str((Path(self.tmp.name) / "rig.py").resolve())
        request = self.ledger.start(tool="run", subject=parent)
        output = str(Path(self.model).with_suffix(".step"))
        self.ledger.observe(self._event(self.model, "building", job=request["id"], sequence=1, parent=parent))
        job = self._owe(request, self.model, self._export(self.model, [output]))
        self.ledger.observe(self._event(self.model, "done", job=request["id"], sequence=2, parent=parent))
        self.ledger.finish(request, 1, error="the parent's body failed")
        self.clock.now += 61.0  # a long export after the request failed
        self.assertTrue(being_written(self.ledger.snapshot(), output))
        self._saved(job, output)
        self.ledger.finish(job, 0)
        self.clock.now += 61.0
        self.assertEqual([], [row for row in self.ledger.snapshot() if row["tool"] == "run"])

    def test_the_display_jobs_a_build_handed_over_name_its_preview_parts_as_they_answer(self):
        """The preview names the parts the store holds and those its jobs derive (``pending``);
        each job's answer names its parts -- in the parent's preview too, whose tree places a
        child's components by the same ids, even one announced after the child's job answered.
        A job that fails leaves its parts unnamed and a warning, never a failed row."""
        from cadgen.daemon.artifacts import result_frame

        parent = str((Path(self.tmp.name) / "rig.py").resolve())
        request = self.ledger.start(tool="run", subject=parent)
        child_output, parent_output = (str(Path(path).with_suffix(".step")) for path in (self.model, parent))

        def preview(model, output, tree, parts, pending, sequence):
            self.ledger.observe(self._event(model, "building", job=request["id"], sequence=sequence, preview={
                "output": output, "tree": tree, "producer": self.PRODUCER, "parts": parts, "pending": pending,
                "complete": not pending}))

        preview(self.model, child_output, self.OTHER, {}, {"1" * 16: "k1"}, 1)
        child_display = self._display(["1" * 16], tree=self.OTHER)
        child_job = self._owe(request, self.model, child_display)
        self.ledger.record_artifact_result(child_job, result_frame(child_display, {"1" * 16: {}}))
        self.ledger.finish(child_job, 0)
        child = next(row for row in self.ledger.snapshot() if row["subject"] == self.model)
        self.assertEqual((child["previews"][child_output]["parts"], child["previews"][child_output]["complete"]),
                         ({"1" * 16: "k1"}, True))
        # The parent's preview, announced after the child's job answered: the child's part is named.
        preview(parent, parent_output, self.TREE, {}, {"1" * 16: "k1", "2" * 16: "k2", "3" * 16: "k3"}, 2)
        own = self._display(["2" * 16, "3" * 16])
        job = self._owe(request, parent, own)
        self.ledger.finish(request, 0)
        self.ledger.record_artifact_result(job, result_frame(own, {"2" * 16: {}}))
        self.ledger.finish(job, 1, error="component 3333333333333333: MeshProductionError: boom")
        [row] = [row for row in self.ledger.snapshot() if row["id"] == request["id"]]
        named = row["previews"][parent_output]
        self.assertEqual(named["parts"], {"1" * 16: "k1", "2" * 16: "k2"})
        self.assertEqual((named["pending"], named["complete"]), ({"3" * 16: "k3"}, False))
        self.assertEqual(row["warnings"], ["display data not derived: component 3333333333333333: "
                                           "MeshProductionError: boom"])
        self.assertEqual(("done", 0), (row["state"], row["exit"]), "display data is never the build's failure")

    def test_a_transition_for_an_unknown_finished_job_is_ignored(self):
        self.ledger.observe(self._event(self.model, "done"))
        self.assertEqual([], self.ledger.snapshot())


class WaitingForAWorker(unittest.TestCase):
    def test_a_request_is_listed_as_starting_a_worker_until_it_has_one(self):
        from cadgen.daemon import server

        ledger, seen, sent = JobLedger(), {}, []

        class Worker:  # runs the job at once
            pid, extra = 7, False

            def send(self, request):
                seen["sent"] = ledger.snapshot()[0]

            def frames(self, **_kwargs):
                yield {"exit": 0, "pid": self.pid}

            def alive(self):
                return True

        def acquire(model, *, dependency, on_start):
            on_start()  # no warm worker: the pool starts one for this request
            seen["starting"] = ledger.snapshot()[0]
            return Worker()

        worker_pool = mock.Mock()
        worker_pool.acquire.side_effect = acquire
        conn = mock.Mock()
        conn.send.side_effect = lambda raw: sent.append(json.loads(raw))
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(server, "_POOL", worker_pool), \
                mock.patch.object(server, "_JOBS", ledger), mock.patch.object(server, "_watch_client"), \
                mock.patch.object(server, "_log"):
            server._handle_request(conn, {"tool": "run", "argv": ["widget.py"], "cwd": tmp})
        self.assertEqual(("queued", "Starting a geometry kernel"),
                         (seen["starting"]["state"], seen["starting"]["detail"]))
        self.assertEqual("", seen["sent"]["detail"], "the wait was over before the job reached its worker")
        self.assertEqual({"exit": 0}, sent[-1])
        self.assertEqual("done", ledger.snapshot()[0]["state"])


class FailureMessageTest(unittest.TestCase):
    """The one line the ledger keeps about a failed job — what a reader shows as
    the reason, so the viewer never has to say only that "the last build failed"."""

    def test_the_cli_failed_line_wins_over_its_own_hint(self) -> None:
        output = (
            "[step-artifact] compile started\n"
            "[cadgen step compile] FAILED: RuntimeError: component be20 build failed: Unextractable: domain\n"
            "[cadgen step compile]   raised in cadgen/store/build.py:259\n"
            "[cadgen step compile] re-run with --verbose for the full traceback\n"
        )
        self.assertEqual(
            ("component be20 build failed: Unextractable: domain", "RuntimeError"),
            failure_message(output),
        )

    def test_a_verbose_traceback_ends_in_the_exception_line(self) -> None:
        output = (
            "Traceback (most recent call last):\n"
            '  File "x.py", line 1, in <module>\n'
            "    raise RuntimeError('failed to read STEP file: not a STEP')\n"
            "RuntimeError: failed to read STEP file: not a STEP\n"
        )
        self.assertEqual(("failed to read STEP file: not a STEP", "RuntimeError"), failure_message(output))

    def test_anything_else_is_the_last_line_that_is_not_the_hint(self) -> None:
        self.assertEqual(("and then died", None), failure_message("the worker said something\nand then died\n"))
        self.assertEqual(("", None), failure_message(""))
        self.assertEqual(
            ("something broke", None),
            failure_message("something broke\n[cadgen step compile] re-run with --verbose for the full traceback\n"),
        )

    def test_finish_records_the_reason_on_a_failed_job_only(self) -> None:
        ledger = JobLedger()
        job = ledger.start(tool="step-compile", subject="/tmp/x.step")
        ledger.finish(job, 1, error="failed to read STEP file: not a STEP")
        self.assertEqual("failed to read STEP file: not a STEP", ledger.snapshot()[0]["error"])
        ok = ledger.start(tool="step-compile", subject="/tmp/y.step")
        ledger.finish(ok, 0, error="ignored on success")
        self.assertIsNone([j for j in ledger.snapshot() if j["id"] == ok["id"]][0]["error"])


if __name__ == "__main__":
    unittest.main()
