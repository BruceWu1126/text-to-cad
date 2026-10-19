"""Foreign compiles publish native geometry before their display data: in a daemon worker the
display jobs are handed to its supervisor, and with no worker the compile awaits them."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import unittest
from unittest import mock

from tests.python.support.paths import add_repo_path
from tests.python.support.tmp_root import generated_cad_directory

add_repo_path("packages/cadgen/src")


class CompileAnswerAtResult(unittest.TestCase):
    def setUp(self):
        temporary = generated_cad_directory(prefix="compile-answer-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.enterContext(mock.patch.dict(os.environ, {"CADGEN_CACHE_DIR": str(self.root / "store")}))
        from build123d import Solid
        from cadgen.step_export import export_build123d_step_file

        self.document = self.root / "box.step"
        export_build123d_step_file(Solid.make_box(2, 3, 4), self.document)
        self.original = self.document.read_bytes()
        self.digest = hashlib.sha256(self.original).hexdigest()
        self.request = {"kind": "display", "tree": "a" * 64, "cids": ["1" * 16], "producer": {}}
        self.display = mock.Mock(keys={"component": "part"})
        self.display.wait.return_value = []

    def asking(self):
        """The compile's display data asked of a pool that runs nothing: its one request, and the
        job that would await it here."""
        return mock.patch.multiple("cadgen.store.parts",
                                   display_requests=mock.Mock(return_value=([self.request], {"1" * 16: "k"}, {})),
                                   submit_display=mock.Mock(return_value=self.display))

    def assert_native_result(self, tree):
        from cadgen import read_step
        from cadgen.store.records import tree_for_document_hash
        from cadgen.store.trees import tree_complete

        self.assertEqual(tree_for_document_hash(self.digest), tree)
        self.assertTrue(tree_complete(tree))
        self.assertAlmostEqual(read_step(self.document).volume, 24.0)
        self.assertEqual(self.document.read_bytes(), self.original)

    def test_a_supervised_compile_answers_with_readable_native_geometry_and_hands_its_display_over(self):
        from cadgen import step
        from cadgen.authoring import supervised

        handed = []
        with self.asking(), supervised(lambda request, model: handed.append((request, model))):
            result = step.compile(self.document)
        self.assertTrue(result.ok)
        self.assertEqual(handed, [(self.request, str(self.document))])
        self.display.wait.assert_not_called()
        with mock.patch("cadgen.store.parts.derive", side_effect=AssertionError("native read derived display")):
            self.assert_native_result(result.tree)

    def test_a_direct_compile_drains_its_display_job_before_returning(self):
        from cadgen import step
        from cadgen.store.records import tree_for_document_hash

        def wait():
            self.assert_native_result(tree_for_document_hash(self.digest))
            return []

        self.display.wait.side_effect = wait
        with self.asking():
            result = step.compile(self.document)
        self.assertTrue(result.ok)
        self.display.wait.assert_called_once_with()

    def test_display_failure_warns_and_keeps_native_geometry(self):
        from cadgen import step

        self.display.wait.side_effect = RuntimeError("display unavailable")
        stderr = io.StringIO()
        with self.asking(), contextlib.redirect_stderr(stderr):
            result = step.compile(self.document)
        self.assertTrue(result.ok)
        self.assertIn("warning: box: display data not derived (RuntimeError: display unavailable)", stderr.getvalue())
        self.assert_native_result(result.tree)

    def test_an_eager_only_result_pins_its_facts_without_claiming_native_reconstruction(self):
        from cadgen import step
        from cadgen._internal import component_package
        from cadgen.authoring import supervised
        from cadgen.store.trees import capture_tree

        with self.asking(), supervised(lambda request, model: None), \
                mock.patch.object(component_package, "_encode_brep",
                                  side_effect=component_package.CodecFidelityError("no exact codec")):
            result = step.compile(self.document)
        self.display.wait.assert_not_called()
        descriptor, objects = capture_tree(result.tree)
        [entry] = descriptor["components"].values()
        self.assertEqual(entry["kind"], "eager-only")
        self.assertIn(entry["topology"], objects)
        with self.assertRaises(component_package.NativeUnavailable):
            component_package.decode_geometry_component(entry, objects[entry["brep"]])

    def test_the_compile_cli_worker_answers_with_its_tree_and_hands_its_display_over(self):
        from cadgen.daemon import worker
        from cadgen.store.records import tree_for_document_hash

        frames = []
        with self.asking(), mock.patch.object(worker, "_emit", side_effect=frames.append), \
                mock.patch.object(worker.sys, "stdin", io.StringIO('{"kind": "owed", "job": "epoch:job-2"}\n')), \
                mock.patch.object(worker, "_enter", return_value=True), mock.patch.object(worker, "_park"), \
                mock.patch.object(worker, "_evict_first_party_modules"):
            code = worker._run({"tool": "step-compile", "argv": [str(self.document), "--json"],
                                "prog": "cadgen step compile", "store_root": str(self.root / "store")})
        self.assertEqual(code, 0)
        answers = [json.loads(frame["data"]) for frame in frames
                   if frame.get("stream") == "stdout" and frame.get("data", "").startswith('{"ok":')]
        self.assertEqual([answer["tree"] for answer in answers], [tree_for_document_hash(self.digest)])
        self.assertEqual([frame["owes"]["request"] for frame in frames if "owes" in frame], [self.request])
        self.display.wait.assert_not_called()


if __name__ == "__main__":
    unittest.main()
