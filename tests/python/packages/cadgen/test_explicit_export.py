"""A requested saved file is completed from its recorded result, without source execution."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support.paths import REPO_ROOT, add_repo_path
from tests.python.support.tmp_root import generated_cad_directory

add_repo_path("packages/cadgen/src")

SOURCE = '''from pathlib import Path
from cadgen import step, stl, glb
from cadgen import build123d as bd

HERE = Path(__file__).resolve().parent

@glb(out="colored.glb")
@stl(out="mesh.stl", mesh_tolerance=0.05, mesh_angular_tolerance=0.3)
@step(out="named-shape.step")
def part():
    (HERE / "body-ran").write_text("ran")
    shape = bd.Box(8, 5, 3)
    shape.color = bd.Color("red")
    return shape

if __name__ == "__main__":
    part()
'''
MESH_ONLY = SOURCE.replace('@step(out="named-shape.step")\n', '')
PAINTED = SOURCE.replace('@step(out="named-shape.step")',
    '@step(out="named-shape.step", materials={"definitions": {"paint": '
    '{"baseColor": "#123456", "roughness": 0.2}}, '
    '"assignments": [{"targets": ["#body"], "material": "paint"}]})').replace(
        '    shape.color = bd.Color("red")', '    shape.label = "body"')


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExplicitExport(unittest.TestCase):
    def setUp(self):
        temporary = generated_cad_directory(prefix="stored-export-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.script = self.root / "part.py"
        self.env = {**os.environ, "CADGEN_CACHE_DIR": str(self.root / "store"),
                    "PYTHONPATH": str(REPO_ROOT / "packages/cadgen/src")}
        self.env.pop("CADGEN_DAEMON_CHILD", None)
        patch = mock.patch.dict(os.environ, self.env)
        patch.start()
        self.addCleanup(patch.stop)

    def command(self, *argv):
        return subprocess.run([sys.executable, *argv], cwd=self.root, env=self.env,
                              capture_output=True, text=True, timeout=300)

    def build(self, source=SOURCE):
        from cadgen._internal.doors import await_writes

        self.script.write_text(source, encoding="utf-8")
        built = self.command(self.script.name, "--json")
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        entry = json.loads(built.stdout.splitlines()[-1])
        for path in entry["files"]:
            await_writes(path)
        (self.root / "body-ran").unlink(missing_ok=True)
        return entry

    def export(self, name, *, succeeds=True):
        done = self.command("-m", "cadgen.cli", "export", name)
        if succeeds:
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertTrue((self.root / name).is_file())
            self.assertIn(f"ready {name}", done.stdout)
        else:
            self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        return done

    def test_cli_retries_step_and_dependent_mesh_with_changed_broken_and_deleted_source(self):
        from cadgen.store.records import read_record

        self.build()
        originals = {name: digest(self.root / name) for name in ("named-shape.step", "mesh.stl", "colored.glb")}
        record = read_record(self.script)
        recipe = record["outputs"][str(self.root / "mesh.stl")]
        self.assertEqual({field: recipe[field] for field in ("declared", "chord", "angle", "name")},
                         {"declared": "stl", "chord": "0.05", "angle": "0.3", "name": "named-shape"})
        self.script.write_text("raise AssertionError('source must not execute')\n", encoding="utf-8")
        current = self.export("mesh.stl")
        self.assertIn("changed since the stored result", current.stderr)
        for source in ("this is invalid Python !", None):
            with self.subTest(source=source):
                if source is None:
                    self.script.unlink()
                else:
                    self.script.write_text(source, encoding="utf-8")
                for name in originals:
                    (self.root / name).unlink()
                self.export("mesh.stl")  # restores the owed STEP dependency first
                self.assertTrue((self.root / "named-shape.step").is_file())
                done = self.export("colored.glb")
                self.assertIn("missing" if source is None else "changed since", done.stderr)
                self.assertEqual({name: digest(self.root / name) for name in originals}, originals)
                self.assertFalse((self.root / "body-ran").exists())
        self.assertTrue((self.root / "mesh.stl").read_bytes().startswith(b"cad named-shape"))

    def test_mesh_only_result_preserves_the_recorded_name_and_tolerances(self):
        from cadgen.store.records import read_record

        self.build(MESH_ONLY)
        original = (self.root / "mesh.stl").read_bytes()
        recipe = read_record(self.script)["outputs"][str(self.root / "mesh.stl")]
        self.assertEqual(recipe["name"], "part")
        self.script.unlink()
        (self.root / "mesh.stl").unlink()
        self.export("mesh.stl")
        self.assertEqual((self.root / "mesh.stl").read_bytes(), original)
        self.assertFalse((self.root / "part.step").exists())
        self.assertFalse((self.root / "body-ran").exists())

    def test_mesh_retry_uses_captured_appearance_even_when_the_sidecar_changes(self):
        from cadgen._internal.source_sidecar import read_source_sidecar, write_source_sidecar
        from cadgen.store.records import read_record

        self.build(PAINTED)
        path = self.root / "colored.glb"
        original = path.read_bytes()
        record = read_record(self.script)
        self.assertTrue(record.get("appearance"))
        step = self.root / "named-shape.step"
        sidecar = read_source_sidecar(step, document_hash=digest(step))
        sidecar["appearance"]["materials"]["paint"]["baseColor"] = "#ffffff"
        write_source_sidecar(step, sidecar)
        self.script.unlink()
        path.unlink()
        from cadgen._internal.export_saved import export_saved
        with mock.patch("cadgen._internal.source_sidecar.read_source_sidecar",
                        side_effect=AssertionError("an export must read its captured annotations")):
            export_saved(path)
        self.assertEqual(path.read_bytes(), original)

    def test_cli_retries_a_recorded_sidecar_with_its_step_without_source(self):
        self.build(PAINTED)
        step = self.root / "named-shape.step"
        sidecar = self.root / "named-shape.step.json"
        originals = step.read_bytes(), sidecar.read_bytes()
        self.script.unlink()
        step.unlink()
        sidecar.unlink()
        done = self.export(sidecar.name)
        self.assertIn("is missing", done.stderr)
        self.assertEqual((step.read_bytes(), sidecar.read_bytes()), originals)
        self.assertFalse((self.root / "body-ran").exists())

    def test_a_static_mesh_recipe_rejects_an_animated_receipt(self):
        from cadgen.store.records import read_record, write_record

        self.build()
        path = self.root / "colored.glb"
        original = path.read_bytes()
        record = read_record(self.script)
        record["outputs"][str(path)]["anim"] = "another clip"
        write_record(record["model"], record)
        self.script.unlink()
        self.export(path.name)
        self.assertEqual(path.read_bytes(), original)
        self.assertIsNone(read_record(self.script)["outputs"][str(path)].get("anim"))

    def test_a_changed_recipe_is_pending_before_the_current_result_export_starts(self):
        from dataclasses import replace

        from cadgen.authoring import build_run, supervised
        from cadgen._internal.generation import _hit, _selected_specs_for_targets
        from cadgen.cli_logging import CliLogger
        from cadgen.store.records import read_record

        self.build(MESH_ONLY)
        previous = read_record(self.script)
        _all, (spec,) = _selected_specs_for_targets([str(self.script)])
        spec = replace(spec, mesh_exports=tuple(replace(item, mesh_tolerance=0.025) if item.fmt == "stl"
                                               else item for item in spec.mesh_exports))
        handed = []
        with supervised(lambda request, model: handed.append(request)), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            with build_run():
                _hit(spec, previous["tree"], logger=CliLogger("cadgen"), display=False)
        current = read_record(self.script)
        self.assertFalse((self.root / "body-ran").exists(), "a current result does not run its body")
        self.assertEqual((current["key"], current["tree"]), (previous["key"], previous["tree"]))
        self.assertEqual(current["outputs"][str(self.root / "mesh.stl")],
                         {"sha256": "", "declared": "stl", "chord": "0.025", "angle": "0.3", "name": "part"})
        self.assertEqual([(request["kind"], [mesh["path"] for mesh in request["meshes"]]) for request in handed],
                         [("mesh", [str(self.root / "mesh.stl")])], "the changed recipe's mesh is owed, alone")

    def test_loss_fails_without_rebuilding_or_replacing_the_existing_file(self):
        from cadgen.store.objects import object_path
        from cadgen.store.index import remove_entry
        from cadgen.store.records import document_key, read_record

        self.build()
        record = read_record(self.script)
        object_path(record["tree"]).unlink()
        remove_entry("document", document_key(digest(self.root / "named-shape.step")))
        self.script.write_text("raise AssertionError('no source fallback')", encoding="utf-8")
        current = self.export("named-shape.step")
        self.assertIn("changed since", current.stderr)
        (self.root / "mesh.stl").unlink()
        failed = self.export("mesh.stl", succeeds=False)
        self.assertIn("no complete stored result", failed.stderr)
        self.assertFalse((self.root / "mesh.stl").exists())

    def test_an_unrecorded_existing_file_is_ready_and_a_missing_one_fails(self):
        path = self.root / "foreign.step"
        path.write_bytes(b"foreign document")
        self.export(path.name)
        self.assertEqual(path.read_bytes(), b"foreign document")
        failed = self.export("missing.step", succeeds=False)
        self.assertIn("no recoverable stored result", failed.stderr)

    def test_old_embedded_record_schema_is_not_restored(self):
        from cadgen.store.index import write_entry
        from cadgen.store.records import RECORD_SCHEMA_VERSION, read_record
        from cadgen.store.results import read_result

        self.build()
        record = read_record(self.script)
        snapshot = read_result(record["key"])
        snapshot["record"]["schemaVersion"] = RECORD_SCHEMA_VERSION - 1
        write_entry("result", record["key"], snapshot)
        self.assertIsNone(read_result(record["key"]))

    def exporting_with(self, before):
        """``export_saved``'s export job run in this process, ``before(request)`` first: what
        happens while a pool job writes the file."""
        from cadgen.daemon import artifacts
        from tests.python.support.inline_artifacts import inline_artifacts

        def run(request):
            if request["kind"] in ("export", "mesh"):
                before(request)
            return artifacts.execute(request)

        return inline_artifacts(run)

    def test_foreign_destination_write_during_retry_is_refused(self):
        from cadgen._internal.export_saved import export_saved
        from cadgen.store.records import read_record

        self.build()
        path = self.root / "named-shape.step"
        path.unlink()
        with self.exporting_with(lambda _request: path.write_bytes(b"foreign document")):
            with self.assertRaisesRegex(RuntimeError, "changed during the build"):
                export_saved(path)
        self.assertEqual(path.read_bytes(), b"foreign document")
        self.assertIn("owed", read_record(self.script)["outputs"][str(path)])

    def test_explicit_retry_follows_a_new_result_instead_of_returning_a_skipped_export(self):
        from cadgen._internal.export_saved import export_saved
        from cadgen._internal.doors import await_writes
        from cadgen.store.records import read_record

        self.build()
        path = self.root / "named-shape.step"
        path.unlink()
        original = read_record(self.script)["tree"]
        newer = []

        def a_newer_build_lands(_request):
            if newer:
                return
            self.script.write_text(SOURCE.replace("bd.Box(8, 5, 3)", "bd.Box(9, 5, 3)"), encoding="utf-8")
            done = self.command(self.script.name)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            await_writes(path)
            newer.append(read_record(self.script)["tree"])
            path.unlink()  # the newest result still owes the requested file

        with self.exporting_with(a_newer_build_lands):
            self.assertEqual(export_saved(path), path)
        self.assertNotEqual(newer[0], original)
        from cadgen.store.records import tree_for_document_hash
        self.assertEqual(tree_for_document_hash(digest(path)), newer[0])

    def test_step_receipt_keeps_a_sibling_completion_that_lands_while_its_job_waits(self):
        from cadgen._internal.export_saved import export_saved
        from cadgen._internal.mesh_export import record_mesh_export
        from cadgen.store.records import read_record, write_record

        self.build()
        path = self.root / "named-shape.step"
        mesh = self.root / "mesh.stl"
        record = read_record(self.script)
        source_hash = record["outputs"][str(path)]["sha256"]
        record["outputs"][str(mesh)]["sha256"] = ""
        write_record(record["model"], record)
        path.unlink()

        def sibling_lands(_request):
            record_mesh_export(mesh, model=record["model"], document_hash=source_hash, fmt="stl",
                               mesh_tolerance=0.05, mesh_angular_tolerance=0.3)

        with self.exporting_with(sibling_lands):
            export_saved(path)
        updated = read_record(self.script)
        self.assertEqual(updated["outputs"][str(mesh)]["sha256"], digest(mesh))
        self.assertEqual(updated["outputs"][str(path)]["sha256"], digest(path))

    def test_export_waits_for_an_in_flight_build_write_before_returning_ready(self):
        """A file a build's job is still writing is waited for, not exported again: the
        command answers ready once the job's file lands (``doors.await_writes``)."""
        import threading

        from cadgen._internal import export_saved as exporting
        from cadgen.daemon.jobs import JobLedger
        from cadgen.store.records import read_record

        self.build()
        path = self.root / "named-shape.step"
        written = path.read_bytes()
        path.unlink()
        ledger, waiting = JobLedger(), threading.Event()
        request = ledger.start(tool="run", subject=str(self.script), store_root=str(self.root / "store"))
        job = ledger.owe(request, str(self.script), {
            "kind": "export", "model": read_record(self.script)["model"], "tree": "a" * 64, "state": "b" * 64,
            "step": str(path), "name": "named-shape", "expected": {}, "previous": {"key": None, "outputs": {}},
            "displays": [], "meshes": []}, store_root=str(self.root / "store"))

        def watch_jobs(after=None, *, output=None, store_root=None):
            if after is not None:
                waiting.set()
            return ledger.watch(after, output=output, store_root=store_root)

        answered = {}
        with mock.patch("cadgen.daemon.client.watch_jobs", side_effect=watch_jobs), \
                mock.patch.object(exporting, "_queue", side_effect=AssertionError("exported a file being written")):
            exporter = threading.Thread(target=lambda: answered.setdefault("path", exporting.export_saved(path)))
            exporter.start()
            self.assertTrue(waiting.wait(30), "the export never waited for the write")
            self.assertNotIn("path", answered)
            path.write_bytes(written)
            ledger.published(job, {"output": str(path), "tree": "a" * 64, "documentHash": digest(path)})
            exporter.join(30)
        self.assertEqual(answered["path"], path)


if __name__ == "__main__":
    unittest.main()
