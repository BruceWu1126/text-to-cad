"""The display tolerance and the export bounds: one policy, cadgen's.

`cadgen.tessellation_policy` decides what cadgen meshes a STEP model's components at for
display: one pair, configurable nowhere. The store's default mesh key, a snapshot job's
tolerances, a mesh export that names none and what the pages are told (the CAD Viewer's
server info, the CAD app's launch) all read it. Only a mesh export names tolerances of its
own, held to the policy's bounds.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support.paths import add_repo_path, repo_path

add_repo_path("packages/cadgen/src")

from cadgen import tessellation_policy as policy  # noqa: E402


class TessellationPolicyTests(unittest.TestCase):
    def test_the_display_tolerance_is_the_stores_default_mesh(self) -> None:
        from cadgen.store import meshes

        display = policy.DISPLAY_TESSELLATION
        self.assertEqual((meshes.DISPLAY_CHORD, meshes.DISPLAY_ANGLE),
                         (display["chordTolerance"], display["angleTolerance"]))
        self.assertEqual(meshes.tessellation_key("a" * 64),
                         meshes.tessellation_key("a" * 64, display["chordTolerance"], display["angleTolerance"]))
        for key, value in display.items():
            self.assertIsNone(policy.tolerance_refusal(key, value, name=key))

    def test_one_rule_bounds_a_tolerance_at_every_door_and_names_the_bound(self) -> None:
        from cadgen.metadata import normalize_mesh_numeric
        from cadgen.store import meshes

        def flag(key, value):
            normalize_mesh_numeric(value, field_name={"chordTolerance": "mesh_tolerance",
                                                      "angleTolerance": "mesh_angular_tolerance"}[key])

        def request(key, value):
            meshes.normalize_tessellations([{**policy.DISPLAY_TESSELLATION, key: value}])

        def stored(key, value):
            pair = {**policy.DISPLAY_TESSELLATION, key: value}
            key_ = meshes.tessellation_key("a" * 64, pair["chordTolerance"], pair["angleTolerance"])
            if meshes.meshable_key(key_) is None:
                raise ValueError("not a key anything may ask to have meshed")

        doors = {"flag": flag, "request": request, "stored key": stored}
        for key in ("chordTolerance", "angleTolerance"):
            floor, ceiling = policy.TESSELLATION_FLOORS[key], policy.TESSELLATION_CEILINGS[key]
            for door, check in doors.items():
                with self.subTest(key=key, door=door):
                    check(key, floor)
                    check(key, ceiling)
                    for outside, bound in ((floor * 0.99, f"at least {floor:g}"), (ceiling * 1.01, f"at most {ceiling:g}"),
                                           (1e300, f"at most {ceiling:g}")):
                        with self.assertRaises(ValueError) as caught:
                            check(key, outside)
                        if door != "stored key":
                            self.assertIn(bound, str(caught.exception))

    def test_an_export_that_names_no_tolerance_is_cut_from_the_display_mesh(self) -> None:
        from cadgen._internal import mesh_export
        from cadgen._internal.mesh_export import MeshExportJob, MeshSource, run_mesh_exporter
        from cadgen.cli_logging import CliLogger

        asked = []

        def stored_meshes(source, pairs, appearance, **_):
            asked.append(pairs)
            raise RuntimeError("stop before meshing")

        with mock.patch.object(mesh_export, "stored_meshes", stored_meshes), self.assertRaises(RuntimeError):
            run_mesh_exporter(MeshSource("0" * 64), [MeshExportJob(fmt="stl", out=Path("part.stl"))],
                              name="part", default_color=None, logger=CliLogger("test", verbose=False))
        display = policy.DISPLAY_TESSELLATION
        # One pair, the display one: the very meshes (store keys) the viewer and snapshots read.
        self.assertEqual([[(display["chordTolerance"], display["angleTolerance"])]], asked)

    def test_the_tests_copy_of_the_display_tolerance_is_the_one_cadgen_publishes(self) -> None:
        # Written by make_fixtures.py for the JS tests, which install it as a host does.
        text = repo_path("packages/core/src/lib/surf/fixtures/displayTessellation.js").read_text(encoding="utf-8")
        head = "export const DISPLAY_TESSELLATION = "
        literal = text[text.index(head) + len(head):].strip().removesuffix(";")
        self.assertEqual(policy.DISPLAY_TESSELLATION, json.loads(literal))


class RemovedTolerancesTests(unittest.TestCase):
    """A model, a model run and a snapshot name no mesh tolerance: each is refused as any
    argument, flag or key that does not exist is."""

    def test_step_takes_no_mesh_tolerance(self) -> None:
        from cadgen.authoring import step

        for name in ("mesh_tolerance", "mesh_angular_tolerance"):
            with self.subTest(name=name), self.assertRaisesRegex(TypeError, f"unexpected keyword argument: {name}"):
                step(**{name: 0.01})

    def test_a_model_run_takes_no_mesh_tolerance_flag(self) -> None:
        import contextlib
        import io

        from cadgen.cli._run_model import _build_parser

        for flag in ("--mesh-tolerance", "--mesh-angular-tolerance"):
            stderr = io.StringIO()
            with self.subTest(flag=flag), contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
                _build_parser("model.py").parse_args([flag, "0.01"])
            self.assertIn(f"unrecognized arguments: {flag}", stderr.getvalue())

    def test_a_snapshot_job_has_no_quality_key(self) -> None:
        from cadgen.snapshot_cli import prepare_render_job
        from cadgen.snapshot_core import SnapshotError

        job = {"input": "part.step", "quality": {"tessellation": {"chordTolerance": 1e-4}}}
        with self.assertRaisesRegex(SnapshotError, r"unknown render job key\(s\): quality"):
            prepare_render_job(job)


if __name__ == "__main__":
    unittest.main()
