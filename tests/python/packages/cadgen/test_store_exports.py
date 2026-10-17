"""A model's STEP is an export of its stored result (``store.exports``): the stored tree
written with the names writer, each part by its key and each element by its name."""

from __future__ import annotations

import hashlib
import os
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
        from cadgen._internal.step_scene_loader import load_step_scene
        from cadgen.store.parts import producer_identity

        path, result = self.export("rig.step")
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), result["sha256"])
        self.assertEqual(producer_identity(), result["producer"], "its part keys are this producer's")
        spans = [label for label, _seconds in result["spans"]]
        self.assertIn("materialize", spans)
        self.assertIn("write STEP file rig.step", spans)
        names = load_step_scene(path).prototype_step_names.values()
        self.assertTrue(names and all(component.part for component in names), "every part carries its key")

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


class ArtifactVocabularyTest(unittest.TestCase):
    """An export names a stored tree and a staged STEP, and nothing else."""

    TREE = "a" * 64

    def test_an_export_is_a_closed_request(self) -> None:
        from cadgen.daemon.artifacts import normalize_request

        out = str(Path("/work/stage/part.step").resolve())
        export = {"kind": "export", "tree": self.TREE, "out": out}
        self.assertEqual(export, normalize_request(dict(export)))
        for bad in ({**export, "out": "part.step"},
                    {**export, "out": str(Path("/work/model.py").resolve())},
                    {**export, "tree": "x"},
                    {**export, "producer": None}):
            with self.subTest(request=bad), self.assertRaises(ValueError):
                normalize_request(bad)


if __name__ == "__main__":
    unittest.main()
