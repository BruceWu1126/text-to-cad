"""A model's STEP is an export of its stored result (``store.exports``): the stored tree
written with the names writer, each part by its key and each element by its name."""

from __future__ import annotations

import hashlib
import os
import re
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support.paths import add_repo_path
from tests.python.support.tmp_root import generated_cad_directory

add_repo_path("packages/cadgen/src")


class ExportTest(unittest.TestCase):
    def setUp(self) -> None:
        from build123d import Compound, Location, Solid

        from cadgen.store.build import build_tree_from_compound

        scratch = generated_cad_directory(prefix="store-exports-")
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        environment = mock.patch.dict(os.environ, {"CADGEN_CACHE_DIR": str(self.root / "store")})
        environment.start()
        self.addCleanup(environment.stop)
        plate = Solid.make_box(20, 10, 4) - Solid.make_cylinder(2, 4).moved(Location((5, 5, 0)))
        plate.label, plate.color = "plate", (0.2, 0.4, 0.6, 1)
        pin = Solid.make_cylinder(2, 12).moved(Location((15, 5, 0)))
        pin.label = "pin"
        self.tree, self.result, _ = build_tree_from_compound(Compound(children=[plate, pin], label="rig"),
                                                             root_name="rig")

    def export(self, name: str) -> tuple[Path, dict]:
        from cadgen.store.exports import export_step

        path = self.root / name
        return path, export_step(self.tree, path)

    def test_an_export_writes_the_stored_tree_with_its_names(self) -> None:
        from cadgen.store.parts import part_key, producer_identity

        path, result = self.export("rig.step")
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), result["sha256"])
        self.assertEqual(producer_identity(), result["producer"], "its part keys are this producer's")
        spans = [label for label, _seconds in result["spans"]]
        self.assertIn("materialize", spans)
        self.assertIn("write STEP file rig.step", spans)
        expected = {occurrence["name"]: part_key(self.result["components"][occurrence["component"]], result["producer"])
                    for occurrence in self.result["occurrences"]}
        products = re.findall(rb"\bPRODUCT\(\s*'[^']*'\s*,\s*'([^']*)'\s*,\s*'([^']*)'", path.read_bytes())
        self.assertEqual(expected, {name.decode(): key.decode() for name, key in products if key},
                         "each product carries the key of the stored part it represents")

    def test_an_export_captures_no_evidence_for_model_code(self) -> None:
        """What an export materializes is written, never handed to model code: it is not
        tagged as a materialized tree, whose evidence (every shape's geometry digest) only
        tells an edit by model code apart -- 3 s of the moonwatch's export."""
        from cadgen.store import materialize

        with mock.patch.object(materialize, "_capture_materialized_state",
                               wraps=materialize._capture_materialized_state) as capture:
            path, result = self.export("untagged.step")
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), result["sha256"])
        self.assertEqual(0, capture.call_count)


    def job(self, **pins) -> dict:
        """An export job's request for the model ``rig.py::rig`` whose record names this tree."""
        from cadgen.daemon.artifacts import normalize_request
        from cadgen.store.exports import state_digest
        from cadgen.store.records import read_record, write_record

        model = f"{self.root / 'rig.py'}::rig"
        write_record(model, {"tree": self.tree, "outputs": {}, "closure": {"hash": "c"}})
        step = str((self.root / "rig.step").resolve())
        return normalize_request({
            "kind": "export", "model": model, "tree": self.tree, "state": state_digest(read_record(model)),
            "step": step, "name": "rig", "expected": {step: None}, "previous": {"key": None, "outputs": {}},
            "displays": [], "meshes": [], **pins})

    def test_a_job_nobody_wants_or_a_newer_result_replaced_writes_nothing(self) -> None:
        """Asked before it writes and before it publishes: a job its build left writes nothing,
        and one whose result the record no longer names leaves the file to the newer result."""
        from cadgen.store.exports import run_export

        abandoned = run_export(self.job(), keep_going=lambda: False)
        self.assertTrue(abandoned["abandoned"])
        superseded = run_export(self.job(state="e" * 64))
        self.assertEqual(superseded["superseded"], str((self.root / "rig.step").resolve()))
        self.assertFalse((self.root / "rig.step").exists())
        self.assertEqual([path.name for path in self.root.iterdir() if path.name.startswith(".cadgen-stage")], [])

    def test_a_job_publishes_the_file_and_says_so_as_it_lands(self) -> None:
        from cadgen.store.exports import run_export
        from cadgen.store.records import read_record, tree_for_document_hash

        heard = []
        outcome = run_export(self.job(), published=heard.append)
        step = str((self.root / "rig.step").resolve())
        digest = hashlib.sha256((self.root / "rig.step").read_bytes()).hexdigest()
        self.assertEqual(heard, [{"output": step, "tree": self.tree, "documentHash": digest}])
        self.assertEqual(outcome["saved"], heard)
        self.assertEqual(tree_for_document_hash(digest), self.tree, "the bytes map to the tree they came from")
        self.assertEqual(read_record(f"{self.root / 'rig.py'}::rig")["outputs"][step]["sha256"], digest)


class ArtifactVocabularyTest(unittest.TestCase):
    """An export or mesh job names a stored tree, the model's result and the files it writes,
    and nothing else."""

    TREE = "a" * 64

    def test_an_export_and_a_mesh_job_are_closed_requests(self) -> None:
        from cadgen.daemon.artifacts import normalize_request

        step = str(Path("/work/part.step").resolve())
        mesh = str(Path("/work/part.stl").resolve())
        export = {"kind": "export", "model": f"{Path('/work/part.py').resolve()}::part", "tree": self.TREE,
                  "state": "b" * 64, "step": step, "name": "part", "expected": {step: None, mesh: "c" * 64},
                  "previous": {"key": None, "outputs": {step: "d" * 64}}, "displays": [],
                  "meshes": [{"fmt": "stl", "path": mesh, "chord": 0.1, "angle": None}]}
        self.assertEqual(export, normalize_request(dict(export)))
        meshes = {**export, "kind": "mesh", "step": None, "expected": {mesh: None}, "previous": {"key": None, "outputs": {}}}
        self.assertEqual(meshes, normalize_request(dict(meshes)))
        for bad in ({**export, "step": "part.step"},
                    {**export, "step": str(Path("/work/model.py").resolve())},
                    {**export, "model": "part.py::part"},
                    {**export, "tree": "x"},
                    {**export, "out": step},
                    {**export, "expected": {str(Path("/work/other.step").resolve()): None}},
                    {**export, "meshes": [{"fmt": "glb", "path": mesh, "chord": None, "angle": None}]},
                    {**export, "meshes": [{"fmt": "stl", "path": mesh, "chord": -1, "angle": None}]},
                    {**meshes, "meshes": []},
                    {**export, "displays": [{"kind": "display", "tree": self.TREE, "cids": [], "producer": {}}]}):
            with self.subTest(request=bad), self.assertRaises(ValueError):
                normalize_request(bad)


if __name__ == "__main__":
    unittest.main()
