"""A model's STEP is an export of its result (STORE.md §3, *Exports*).

The build publishes its result and its preview first; the build pool writes the file from
the stored tree, and the file's bytes map to that result the moment it is published: every
door given the file reads that entry, and nothing parses the file to check it.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import re
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support.cad_test_roots import IsolatedCadRoots
from tests.python.support.inline_artifacts import inline_artifacts
from tests.python.support.paths import add_repo_path

add_repo_path("packages/cadgen/src")

from cadgen.daemon.artifacts import submit_artifact as daemon_submit_artifact  # noqa: E402 - the job's real dispatch

MODEL = """
from cadgen import step, label_shape
from cadgen import build123d as bd

@step()
def rig():
    plate = label_shape(bd.Box(20, 10, 4) - bd.Pos(5, 0, 0) * bd.Cylinder(2, 4), "plate")
    pin = label_shape(bd.Pos(15, 0, 6) * bd.Cylinder(2, 12), "pin")
    return bd.Compound(children=[plate, pin], label="rig")


if __name__ == "__main__":
    rig()
"""


class FilesAsExportsTest(unittest.TestCase):
    def setUp(self) -> None:
        roots = IsolatedCadRoots(self, prefix="files-as-exports-")
        # Artifact jobs run where they are submitted: this checks what the export
        # writes, and the in-process build finishes its files before it returns.
        self.enterContext(inline_artifacts())
        self.root = roots.cad_root
        self.script = self.root / "rig.py"
        self.script.write_text(MODEL, encoding="utf-8")
        self.step = self.root / "rig.step"

    def build(self, *, force: bool = False) -> tuple[str, str]:
        """The run's outcome (``built``, or ``current`` where the body did not run) and
        what it said on stderr, as ``python rig.py --json`` says them."""
        from cadgen.cli._run_model import run_model_argv

        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(0, run_model_argv([str(self.script), "--json", *(["--force"] if force else [])]),
                             stderr.getvalue())
        (line,) = [line for line in stdout.getvalue().splitlines() if line.startswith("{")]
        return json.loads(line)["outcome"], stderr.getvalue()

    def record(self) -> dict:
        from cadgen.store.index import model_ref
        from cadgen.store.records import read_record

        return read_record(model_ref(self.script, "rig"))

    def entry(self) -> dict:
        from cadgen.store.records import document_entry_for_hash

        return document_entry_for_hash(hashlib.sha256(self.step.read_bytes()).hexdigest())

    def test_the_file_is_the_result_the_moment_it_is_published(self) -> None:
        from cadgen.step_export import STEP_WRITER_SCHEME
        from cadgen.store.parts import part_key
        from cadgen.store.trees import capture_tree

        self.build()
        record, entry = self.record(), self.entry()
        self.assertEqual(record["tree"], entry["tree"], "the bytes map to the result they were written from")
        self.assertEqual({"schemaVersion", "tree", "kind", "producer"}, set(entry), "the entry is final as written")
        digest = hashlib.sha256(self.step.read_bytes()).hexdigest()
        self.assertEqual({"sha256": digest, "writer": STEP_WRITER_SCHEME}, record["outputs"][str(self.step.resolve())])
        descriptor, _payloads = capture_tree(record["tree"])
        expected = {occurrence["name"]: part_key(descriptor["components"][occurrence["component"]], entry["producer"])
                    for occurrence in descriptor["occurrences"]}
        written = self.step.read_bytes()
        products = re.findall(rb"\bPRODUCT\(\s*'[^']*'\s*,\s*'([^']*)'\s*,\s*'([^']*)'", written)
        self.assertEqual(expected, {name.decode(): key.decode() for name, key in products if key},
                         "each product carries the key of the result's corresponding part")
        self.assertRegex(written, rb"\bADVANCED_FACE\(\s*'f:", "the file carries its faces' names")
        self.assertRegex(written, rb"\bEDGE_CURVE\(\s*'e:", "the file carries its edges' names")

    def test_the_preview_precedes_the_one_document_the_export_assembles(self) -> None:
        """The build assembles no XCAF document of its result before its preview is
        published: the export, on the pool, assembles the one document a build makes, after
        the preview -- so the result is on screen before any STEP-writer step runs."""
        from cadgen import step_export
        from cadgen.daemon import executors

        documents, previews = [], []
        assemble, emit = step_export._create_bin_xcaf_doc, executors.emit_event

        def assembled(*args, **kwargs):
            documents.append(len(previews))  # the preview events published before this document
            return assemble(*args, **kwargs)

        def emitted(event):
            if event.get("preview"):
                previews.append(len(documents))  # the documents assembled before this preview
            return emit(event)

        with mock.patch.object(step_export, "_create_bin_xcaf_doc", side_effect=assembled), \
                mock.patch.object(executors, "emit_event", side_effect=emitted):
            self.assertEqual("built", self.build()[0])
        self.assertTrue(previews, "the build published its preview")
        self.assertEqual(0, previews[0], "a document was assembled before the first preview")
        self.assertEqual(1, len(documents), "the export assembles the one document")
        self.assertGreaterEqual(documents[0], 1, "the export's document came after the preview")

    def test_a_door_answers_from_the_files_entry(self) -> None:
        """A door given the build's file reads the entry its bytes map to and answers with
        the result: no compile of the file, and no job about it."""
        from cadgen.step_scene import read_scene

        self.build()
        never = AssertionError("a door's job about the build's own file")
        with mock.patch("cadgen.daemon.executors.submit_compile", side_effect=never), \
                mock.patch("cadgen.daemon.artifacts.submit_artifact", side_effect=never):
            scene = read_scene(self.step)
        self.assertEqual({"plate", "pin"}, {leaf.label for leaf in scene.leaves()})

    def test_a_declared_mesh_export_joins_the_build_without_a_doors_lookup_of_its_file(self) -> None:
        """A model's declared meshes are cut from its result and keyed on its STEP's bytes
        as its record names them -- the build's own knowledge -- so the command returns
        without a door's lookup of the file it just wrote."""
        self.script.write_text(MODEL.replace("from cadgen import step, label_shape", "from cadgen import step, stl, label_shape")
                               .replace("@step()", "@step()\n@stl"), encoding="utf-8")
        door = AssertionError("a door's lookup of the model's own file")
        with mock.patch("cadgen._internal.doors.document_snapshot", side_effect=door):
            outcome, said = self.build()
        self.assertEqual("built", outcome)
        self.assertIn("wrote STL", said)
        digest = hashlib.sha256(self.step.read_bytes()).hexdigest()
        stl = self.record()["outputs"][str(self.step.with_suffix(".stl").resolve())]
        self.assertEqual(digest, stl["document"])
        self.assertTrue(self.step.with_suffix(".stl").is_file())

    def test_a_runtime_that_cannot_name_its_producer_writes_names_and_no_part_key(self) -> None:
        """A part key names its producer, so a runtime that cannot name its own writes its
        elements' names and no part key; the file maps to its result like any other, and the
        next run keeps it."""
        from cadgen.step_export import STEP_WRITER_SCHEME

        with mock.patch("cadgen.store.parts.producer_identity", side_effect=ValueError("no kernel version")):
            outcome, _said = self.build()
        self.assertEqual("built", outcome)
        digest = hashlib.sha256(self.step.read_bytes()).hexdigest()
        self.assertEqual({"sha256": digest, "writer": STEP_WRITER_SCHEME},
                         self.record()["outputs"][str(self.step.resolve())])
        self.assertEqual(self.record()["tree"], self.entry()["tree"])
        written = self.step.read_bytes()
        products = re.findall(rb"\bPRODUCT\(\s*'[^']*'\s*,\s*'[^']*'\s*,\s*'([^']*)'", written)
        self.assertTrue(products)
        self.assertEqual({b""}, set(products), "no product claims a part key without its producer")
        self.assertRegex(written, rb"\bADVANCED_FACE\(\s*'f:")
        self.assertRegex(written, rb"\bEDGE_CURVE\(\s*'e:")
        self.assertEqual("current", self.build()[0])
        self.assertEqual(written, self.step.read_bytes())

    def test_a_rerun_during_an_export_in_flight_waits_for_it_rather_than_exporting_again(self) -> None:
        # A run that finds its result current while a job of the previous run is still
        # writing the file asks the ledger first, as a door does, and reads the record that
        # write fills: never "was never written; writing it again" and a second export.
        from cadgen.store.index import model_ref
        from cadgen.store.records import write_record

        self.build()
        landed = self.record()
        pending = json.loads(json.dumps(landed))
        pending["outputs"][str(self.step.resolve())]["sha256"] = ""
        write_record(model_ref(self.script, "rig"), pending)
        asked = []

        def await_writes(path):
            asked.append(Path(path).resolve())
            write_record(model_ref(self.script, "rig"), landed)  # the export lands while the run waits

        with mock.patch("cadgen._internal.doors.await_writes", side_effect=await_writes):
            outcome, said = self.build()
        self.assertEqual("current", outcome)
        self.assertIn(self.step.resolve(), asked, "the run judged the file without asking the ledger")
        self.assertNotIn("writing it again", said)
        self.assertNotIn("never written", said)

    def test_a_file_whose_entry_the_store_lost_is_mapped_again(self) -> None:
        """A current model's file the store has no entry for is exported again by its next
        run: the same bytes, which keep the file and its stamp, mapped to the result --
        never left for a door to compile the result's own file as a foreign one."""
        import os

        from cadgen.store.index import remove_entry
        from cadgen.store.records import document_key

        self.build()
        digest = hashlib.sha256(self.step.read_bytes()).hexdigest()
        stamp = os.stat(self.step)
        producer = self.entry().get("producer")
        self.assertIsNotNone(producer, "the build's entry names no producer")
        remove_entry("document", document_key(digest))
        self.assertIsNone(self.entry())
        outcome, said = self.build()
        self.assertEqual("current", outcome)
        self.assertIn("rig.step has no entry in the store; writing it again", said)
        self.assertEqual(digest, hashlib.sha256(self.step.read_bytes()).hexdigest())
        after = os.stat(self.step)
        self.assertEqual((stamp.st_mtime_ns, stamp.st_ino), (after.st_mtime_ns, after.st_ino), "the file kept its stamp")
        entry = self.entry()
        self.assertEqual(self.record()["tree"], entry["tree"])
        self.assertEqual(producer, entry.get("producer"), "the entry says what the file written again carries")
        self.assertEqual("current", self.build()[0])


class LiveShapeExportTest(unittest.TestCase):
    """A result holding a component the store cannot decode (an eager-only one) has its STEP
    written from the build's own live shape, never by the pool: while the file is owed, the
    model owes a run (the gate, clause 5), and every run that cannot write it fails, loudly."""

    WIRE = """
from cadgen import step
from cadgen import build123d as bd

@step
def m():
    return bd.Wire.make_circle(3)


if __name__ == "__main__":
    m()
"""

    def setUp(self) -> None:
        roots = IsolatedCadRoots(self, prefix="live-export-")
        self.enterContext(inline_artifacts())
        self.root = roots.cad_root
        self.script = self.root / "m.py"
        self.script.write_text(self.WIRE, encoding="utf-8")

    def run_model(self) -> tuple[int, dict]:
        from cadgen.cli._run_model import run_model_argv

        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = run_model_argv([str(self.script), "--json"])
        (line,) = [line for line in stdout.getvalue().splitlines() if line.startswith("{")]
        return code, json.loads(line)

    def test_a_step_only_a_run_can_write_fails_every_run_with_a_message(self) -> None:
        from cadgen.store.gate import stale
        from cadgen.store.index import model_ref

        for attempt in ("first", "second"):
            code, answer = self.run_model()
            self.assertEqual(code, 1, attempt)
            self.assertFalse(answer["ok"], attempt)
            self.assertRegex(answer["error"], r"\S", f"{attempt}: an error names what failed")
            self.assertFalse((self.root / "m.step").exists(), attempt)
            verdict = stale(model_ref(self.script, "m"))
            self.assertIsNotNone(verdict.stored, "the result is in the store")
            self.assertIsNone(verdict.key, "and owes a run while its STEP is")


class KeptRuleTest(unittest.TestCase):
    """A build keeps its STEP -- writes nothing -- only while its record's last build had the
    same tree, wrote the file with this writer, the file still has the bytes it recorded,
    and the store maps those bytes; the plan says why a file is written again."""

    TREE, DIGEST, STEP = "a" * 64, "b" * 64, "/work/STEP/part.step"

    def setUp(self) -> None:
        from cadgen.store.records import note_document_tree

        IsolatedCadRoots(self, prefix="kept-rule-")
        # The file's bytes map to the tree they were written from.
        note_document_tree(self.DIGEST, self.TREE)

    _ON_DISK = object()

    def plan(self, *, output: dict | None = None, tree: str | None = None, on_disk: object = _ON_DISK,
             force: bool = False):
        from cadgen._internal.generation import _export_plan
        from cadgen.step_export import STEP_WRITER_SCHEME

        output = {"sha256": self.DIGEST, "writer": STEP_WRITER_SCHEME} if output is None else output
        return _export_plan({"tree": self.TREE, "outputs": {self.STEP: output}}, self.STEP, tree or self.TREE,
                            force=force, on_disk=self.DIGEST if on_disk is self._ON_DISK else on_disk)

    def test_a_file_is_kept_while_its_tree_writer_and_bytes_agree(self) -> None:
        from cadgen.step_export import STEP_WRITER_SCHEME

        kept = self.plan()
        self.assertEqual(({"sha256": self.DIGEST, "writer": STEP_WRITER_SCHEME}, ""), (kept.kept, kept.why))
        self.assertEqual({"sha256": self.DIGEST, "writer": STEP_WRITER_SCHEME}, kept.output())
        for why, plan, said in (("another result", self.plan(tree="c" * 64), ""),
                                ("other bytes on disk", self.plan(on_disk="f" * 64), "is not the file the record names"),
                                ("never written", self.plan(output={"sha256": "", "writer": STEP_WRITER_SCHEME},
                                                            on_disk=None), "was never written"),
                                ("an export that never finished, the earlier file still there",
                                 self.plan(output={"sha256": "", "writer": STEP_WRITER_SCHEME}),
                                 "holds an earlier result; its export never finished"),
                                ("forced", self.plan(force=True), ""),
                                ("another writer", self.plan(output={"sha256": self.DIGEST, "writer": "cadgen-step-writer-3"}),
                                 "was written by another writer")):
            with self.subTest(why):
                self.assertIsNone(plan.kept)
                self.assertEqual(said, plan.why)
                self.assertEqual({"sha256": "", "writer": STEP_WRITER_SCHEME}, plan.output())

    def test_a_file_whose_entry_the_store_lost_is_written_again_to_the_same_bytes(self) -> None:
        from cadgen.store.index import remove_entry
        from cadgen.store.records import document_key

        remove_entry("document", document_key(self.DIGEST))
        plan = self.plan()
        self.assertEqual((None, "has no entry in the store"), (plan.kept, plan.why))


if __name__ == "__main__":
    unittest.main()
