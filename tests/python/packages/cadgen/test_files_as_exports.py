"""A model's STEP is an export of its result (STORE.md §3, *Exports*).

The build publishes its result and its preview first; the build pool writes the file from
the stored tree and a parse of it is compared with the tree. A file that verifies is the
result; one that does not shows its own content, the build or the next door that touches it
says so, and the model's next run writes it again without names -- without running its body.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support.cad_test_roots import IsolatedCadRoots
from tests.python.support.paths import add_repo_path

add_repo_path("packages/cadgen/src")

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
        import os

        roots = IsolatedCadRoots(self, prefix="files-as-exports-")
        self.enterContext(mock.patch.dict(os.environ, {"CADGEN_DAEMON": "0"}))
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

    def test_the_file_is_the_result_and_says_so_once_verified(self) -> None:
        from cadgen._internal.step_scene_loader import load_step_scene
        from cadgen.step_export import STEP_WRITER_SCHEME

        self.build()
        record, entry = self.record(), self.entry()
        self.assertEqual(record["tree"], entry["tree"], "the bytes map to the result they were written from")
        self.assertNotIn("verified", entry, "a build with no daemon waits for its verify")
        digest = hashlib.sha256(self.step.read_bytes()).hexdigest()
        self.assertEqual({"sha256": digest, "writer": STEP_WRITER_SCHEME}, record["outputs"][str(self.step.resolve())])
        names = load_step_scene(self.step).prototype_step_names.values()
        self.assertTrue(names and all(component.part and component.faces for component in names),
                        "the file names its parts and their elements")

    def test_a_door_confirms_a_pending_entry_before_it_answers(self) -> None:
        from cadgen.step_scene import read_scene
        from cadgen.store.records import note_document_tree

        self.build()
        entry = self.entry()
        digest = hashlib.sha256(self.step.read_bytes()).hexdigest()
        note_document_tree(digest, entry["tree"], producer=entry["producer"], verified=False)
        with mock.patch("cadgen.store.exports.verify_document", wraps=__import__(
                "cadgen.store.exports", fromlist=["verify_document"]).verify_document) as verify:
            scene = read_scene(self.step)
        self.assertEqual(1, verify.call_count)
        self.assertNotIn("verified", self.entry())
        self.assertEqual({"plate", "pin"}, {leaf.label for leaf in scene.leaves()})

    def test_a_file_that_does_not_hold_its_result_is_its_own_and_is_written_again_plain(self) -> None:
        from cadgen._internal.step_scene_loader import load_step_scene
        from cadgen._internal.step_verify import Mismatch

        # A writer that loses a face's place: what the verify finds is the file's.
        lost = [Mismatch(None, None, "faces off their place")]
        with mock.patch("cadgen._internal.step_verify.verify_scene", return_value=lost):
            outcome, said = self.build()
        self.assertEqual("built", outcome)
        self.assertIn("does not hold the result it was written from", said)
        record, entry = self.record(), self.entry()
        self.assertNotEqual(record["tree"], entry["tree"], "the file shows its own content")
        self.assertTrue(entry["differ"])
        written = self.step.read_bytes()

        # The next run writes it again without names, and never runs the body.
        self.assertEqual("current", self.build()[0])
        self.assertNotEqual(written, self.step.read_bytes())
        record, entry = self.record(), self.entry()
        self.assertTrue(record["outputs"][str(self.step.resolve())]["plain"])
        self.assertEqual(record["tree"], entry["tree"], "the plain file holds the result")
        self.assertTrue(all(not component.part and not component.faces
                            for component in load_step_scene(self.step).prototype_step_names.values()))
        # And it is kept from then on, plain.
        plain = self.step.read_bytes()
        self.assertEqual("current", self.build()[0])
        self.assertEqual(plain, self.step.read_bytes())

    def test_a_plain_file_that_does_not_hold_its_result_is_kept_as_its_own(self) -> None:
        """Written plain, a result is written to the same bytes again: a plain file that
        does not hold its result shows its own content, and the next run keeps it rather
        than write and verify it again -- the warning promises no rewrite."""
        from cadgen._internal.step_verify import Mismatch

        lost = [Mismatch(None, None, "faces off their place")]
        with mock.patch("cadgen._internal.step_verify.verify_scene", return_value=lost):
            said = self.build()[1]
            self.assertIn("the model's next run writes it without names", said)
            outcome, said = self.build()
            self.assertEqual("current", outcome)
            self.assertIn("does not hold the result it was written from, so it shows its own content: ", said)
            self.assertNotIn("next run", said)
            entry = self.entry()
            self.assertTrue(entry["differ"] and entry["plain"])
            plain = self.step.read_bytes()
            with mock.patch("cadgen.store.exports.export_step") as export:
                outcome, said = self.build()
            self.assertEqual(("current", 0), (outcome, export.call_count))
            self.assertEqual(plain, self.step.read_bytes())
            self.assertNotIn("does not hold the result", said)


class KeptRuleTest(unittest.TestCase):
    """A build keeps its STEP -- writes and verifies nothing -- only while its record's last
    build had the same tree, wrote the file with this writer, the file still has the bytes
    it recorded, and those bytes held the result; a file that did not is written plain."""

    TREE, DIGEST, STEP = "a" * 64, "b" * 64, "/work/STEP/part.step"

    def setUp(self) -> None:
        IsolatedCadRoots(self, prefix="kept-rule-")

    def plan(self, *, output: dict | None = None, tree: str | None = None, on_disk: str | None = None,
             force: bool = False):
        from cadgen._internal.generation import _export_plan
        from cadgen.step_export import STEP_WRITER_SCHEME

        output = {"sha256": self.DIGEST, "writer": STEP_WRITER_SCHEME} if output is None else output
        return _export_plan({"tree": self.TREE, "outputs": {self.STEP: output}}, self.STEP, tree or self.TREE,
                            force=force, on_disk=on_disk or self.DIGEST)

    def test_a_file_is_kept_while_its_tree_writer_and_bytes_agree(self) -> None:
        from cadgen.step_export import STEP_WRITER_SCHEME

        kept = self.plan()
        self.assertEqual({"sha256": self.DIGEST, "writer": STEP_WRITER_SCHEME}, kept.kept)
        for why, plan in (("another result", self.plan(tree="c" * 64)),
                          ("other bytes on disk", self.plan(on_disk="f" * 64)),
                          ("forced", self.plan(force=True)),
                          ("another writer", self.plan(output={"sha256": self.DIGEST, "writer": "cadgen-step-writer-3"}))):
            with self.subTest(why):
                self.assertIsNone(plan.kept)
                self.assertFalse(plan.plain)

    def test_a_file_that_did_not_hold_its_result_is_written_plain(self) -> None:
        from cadgen.step_export import STEP_WRITER_SCHEME
        from cadgen.store.records import note_document_tree

        note_document_tree(self.DIGEST, "d" * 64, differ=["the file: faces off their place"])
        marked = self.plan()
        self.assertIsNone(marked.kept)
        self.assertTrue(marked.plain)
        self.assertEqual({"sha256": "", "writer": STEP_WRITER_SCHEME, "plain": True}, marked.output())
        # Another result is written with names again.
        self.assertFalse(self.plan(tree="c" * 64).plain)

    def test_a_plain_file_that_did_not_hold_its_result_is_kept(self) -> None:
        """The same result is written plain to the same bytes: nothing is gained by writing
        and verifying them again."""
        from cadgen.step_export import STEP_WRITER_SCHEME
        from cadgen.store.records import note_document_tree

        note_document_tree(self.DIGEST, "d" * 64, plain=True, differ=["the file: faces off their place"])
        output = {"sha256": self.DIGEST, "writer": STEP_WRITER_SCHEME, "plain": True}
        kept = self.plan(output=output)
        self.assertEqual(output, kept.kept)
        self.assertTrue(kept.plain)
        self.assertIsNone(self.plan(output=output, force=True).kept)


if __name__ == "__main__":
    unittest.main()
