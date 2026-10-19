"""A build's source result: the tree its run published, named before its files are
written and never replaced by a later record. A top-level call hands back exactly that
tree; a child a parent's body called owes its declared outputs and its failure to the run."""

from __future__ import annotations

import os
import contextlib
import io
import json
import runpy
import sys
import unittest
from pathlib import Path
from unittest import mock

from cadgen.daemon import executors
from tests.python.support.tmp_root import generated_cad_directory


class SourceJobResults(unittest.TestCase):
    def setUp(self):
        temporary = generated_cad_directory(prefix="source-job-results-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        environment = mock.patch.dict(os.environ, {
            "CADGEN_CACHE_DIR": str(self.root / "store"),
            "CADGEN_JOBS": "2",
        })
        environment.start()
        self.addCleanup(environment.stop)

    def event(self, model, tree):
        return executors.model_event(model, "building", sourceResult={"model": str(model), "tree": tree})

    def test_a_result_announced_before_completion_stands_whatever_the_exit(self):
        model = f"{self.root / 'child.py'}::child"
        job = executors.Job(model)
        job._observe(self.event(model, "tree-1"))
        self.assertFalse(job.done)
        self.assertTrue(job.result_ready)
        job._observe(self.event(model, "tree-2"))  # a later announcement never replaces the first
        job._finish(1)
        self.assertEqual(job.wait_result(), "tree-1", "failed persistence cannot rewrite the published source result")

    def test_wrong_function_result_and_success_without_a_result_do_not_resolve(self):
        model = f"{self.root / 'child.py'}::left"
        job = executors.Job(model)
        job._observe(self.event(f"{self.root / 'child.py'}::right", "other-tree"))
        job._finish(0)
        with self.assertRaisesRegex(RuntimeError, "source result"):
            job.wait_result()

    def test_top_level_return_keeps_the_completed_jobs_exact_tree(self):
        import cadgen.authoring as authoring
        from cadgen.store.build import build_tree_from_compound
        from cadgen.store.records import read_record, write_record
        from build123d import Box

        script = self.root / "part.py"
        script.write_text("from cadgen import step, build123d as bd\n@step\ndef part():\n    return bd.Box(4, 3, 2)\n", encoding="utf-8")
        namespace = runpy.run_path(str(script))
        original = authoring._build

        def finish_then_newer(definition):
            code = original(definition)
            record = read_record(definition.ref)
            record["tree"] = build_tree_from_compound(Box(40, 3, 2), root_name="part")[0]
            write_record(definition.ref, record)
            return code

        with mock.patch.object(sys, "argv", [str(script)]), \
             mock.patch.object(authoring, "_build", side_effect=finish_then_newer), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = namespace["part"]()
        self.assertAlmostEqual(sum(solid.volume for solid in result.solids()), 24)

    def test_current_call_emits_source_result_and_returns_independent_geometry(self):
        from cadgen.store.materialize import TREE_TAG

        script = self.root / "part.py"
        script.write_text(
            "from cadgen import step, build123d as bd\n@step\ndef part():\n"
            "    return bd.Box(4, 3, 2)\n",
            encoding="utf-8",
        )
        namespace = runpy.run_path(str(script))
        with mock.patch.object(sys, "argv", [str(script)]), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            first = namespace["part"]()
            second = namespace["part"]()
        self.assertEqual(getattr(first, TREE_TAG), getattr(second, TREE_TAG))
        self.assertFalse(first.wrapped.IsPartner(second.wrapped))

    def test_current_gate_and_cli_keep_the_checked_tree_when_the_record_moves(self):
        from cadgen.cli._run_model import run_model_argv
        from cadgen.store import gate
        from cadgen.store.build import build_tree_from_compound
        from cadgen.store.records import read_record, write_record
        from build123d import Box

        script = self.root / "part.py"
        script.write_text("from cadgen import step, build123d as bd\n@step\ndef part():\n    return bd.Box(4, 3, 2)\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run_model_argv([str(script)]), 0)
        record = read_record(script)
        checked_tree = record["tree"]
        newer_tree = build_tree_from_compound(Box(40, 3, 2), root_name="part")[0]
        original = gate.stale

        def check_then_replace(model, **kwargs):
            verdict = original(model, **kwargs)
            self.assertFalse(verdict.stale)
            record["tree"] = newer_tree
            write_record(script, record)
            return verdict

        output = io.StringIO()
        with mock.patch.object(gate, "stale", side_effect=check_then_replace), \
             executors.capture_source_result(f"{script}::part") as captured, \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run_model_argv([str(script), "--json"]), 0)
        captured._finish(0)
        self.assertEqual(captured.wait_result(), checked_tree)
        result = [json.loads(line) for line in output.getvalue().splitlines() if line.startswith('{"ok":')][-1]
        self.assertEqual(result["tree"], checked_tree)
        self.assertEqual(read_record(script)["tree"], newer_tree)

    def test_a_mesh_job_cuts_the_result_it_pins_and_nothing_once_a_newer_record_replaced_it(self):
        from build123d import Box
        from cadgen.daemon.artifacts import normalize_request
        from cadgen.store.build import build_tree_from_compound
        from cadgen.store.exports import run_meshes, state_digest
        from cadgen.store.records import read_record, write_record

        script = self.root / "part.py"
        script.write_text("from cadgen import stl, build123d as bd\n@stl\ndef part():\n    return bd.Box(4, 3, 2)\n", encoding="utf-8")
        from cadgen._internal.mesh_export import mesh_recipe

        model = f"{script.resolve()}::part"
        old = build_tree_from_compound(Box(4, 3, 2), root_name="part")[0]
        newer = build_tree_from_compound(Box(40, 3, 2), root_name="part")[0]
        mesh = str((self.root / "part.stl").resolve())
        # The record a build writes at its result: the mesh's recipe listed, its bytes owed.
        write_record(model, {"tree": old, "outputs": {mesh: {"sha256": "", **mesh_recipe("stl", None, None, "part")}}})
        request = normalize_request({
            "kind": "mesh", "model": model, "tree": old, "state": state_digest(read_record(model)), "step": None,
            "name": "part", "expected": {mesh: None}, "previous": {"key": None, "outputs": {}}, "displays": [],
            "meshes": [{"fmt": "stl", "path": mesh, "chord": None, "angle": None}]})
        seen = []

        def export(source, jobs, **kwargs):
            seen.append(source.tree)
            for job in jobs:
                job.out.write_bytes(b"requested mesh result")
            return {"ok": True, "files": [{"path": str(job.out), "format": job.fmt, "triangleCount": 12} for job in jobs]}

        with mock.patch("cadgen._internal.mesh_export.run_mesh_exporter", side_effect=export):
            self.assertEqual([saved["output"] for saved in run_meshes(request)["saved"]], [mesh])
            write_record(model, {"tree": newer, "outputs": {}})
            Path(mesh).unlink()
            self.assertEqual(run_meshes(request)["superseded"], mesh)
        self.assertEqual(seen, [old])
        self.assertFalse(Path(mesh).exists(), "a newer result's file is its own build's to write")

    def test_a_failure_after_the_body_is_reported_at_the_model_s_own_line(self):
        """What cadgen refuses of the result once the body returned (a face colour's
        ordinal) has no model frame on its way: the report names the model's function
        and its line, not the line of cadgen's that raised."""
        plate = self.root / "plate.py"
        plate.write_text(
            "from cadgen import step, build123d as bd\n\n\n@step\ndef plate():\n    box = bd.Box(4, 3, 2)\n"
            "    box.cad_face_ordinal_colors = {0: (1.0, 0.0, 0.0, 1.0)}\n    return box\n",
            encoding="utf-8",
        )
        from cadgen.cli._run_model import run_model_argv

        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            self.assertEqual(run_model_argv([str(plate)]), 1)
        report = err.getvalue()
        self.assertIn("a face is named by its ordinal, a whole number from 1 (got 0)", report)
        self.assertRegex(report, r"plate\.py:4 in plate\n.*@step\n")
        self.assertNotIn("raised in", report)

    def _nested(self, *, discard: bool, child_fails: bool) -> tuple[int, str]:
        child = self.root / "child.py"
        parent = self.root / "parent.py"
        child.write_text(
            "from cadgen import step, build123d as bd\n@step\ndef child():\n"
            + ("    raise RuntimeError('the child is wrong on purpose')\n" if child_fails else "    return bd.Box(4, 3, 2)\n"),
            encoding="utf-8",
        )
        parent.write_text(
            "from cadgen import step, build123d as bd\nfrom child import child\n@step\ndef parent():\n"
            + ("    try:\n        piece = child()\n    except Exception:\n        piece = None\n" if child_fails
               else "    piece = child()\n")
            + ("    return bd.Box(7, 3, 2)\n" if discard
               else "    return bd.Compound(children=[bd.Pos(8, 0, 0) * piece, bd.Pos(-8, 0, 0) * piece])\n"),
            encoding="utf-8",
        )
        (self.root / "parent.step").write_bytes(b"previous parent document")
        from cadgen.cli._run_model import run_model_argv

        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = run_model_argv([str(parent)])
        return code, err.getvalue()

    def test_a_discarded_child_is_still_built_whole_and_is_the_parents_dependency(self):
        # The body called it, so its declared outputs are written and the parent's
        # record lists it, whatever the body made of the geometry.
        from cadgen.store.records import read_record

        code, err = self._nested(discard=True, child_fails=False)
        self.assertEqual(code, 0, err)
        self.assertTrue((self.root / "child.step").is_file(), "the discarded child's declared STEP was not written")
        self.assertNotEqual((self.root / "parent.step").read_bytes(), b"previous parent document")
        self.assertEqual(read_record(self.root / "parent.py")["children"],
                         [{"model": f"{self.root / 'child.py'}::child", "key": read_record(self.root / "child.py")["key"]}])

    def test_a_child_failure_the_body_catches_still_fails_the_run_and_keeps_the_parents_file(self):
        from cadgen.store.records import read_record

        code, err = self._nested(discard=True, child_fails=True)
        self.assertNotEqual(code, 0)
        # The child's own exception, naming its error and its line: no wrapper.
        self.assertIn("FAILED: RuntimeError: the child is wrong on purpose", err)
        self.assertRegex(err, r"child\.py:4 in child")
        self.assertNotIn("a child model failed to build", err)
        self.assertEqual((self.root / "parent.step").read_bytes(), b"previous parent document")
        self.assertIsNone(read_record(self.root / "parent.py"))
        self.assertIsNone(read_record(self.root / "child.py"))


if __name__ == "__main__":
    unittest.main()
