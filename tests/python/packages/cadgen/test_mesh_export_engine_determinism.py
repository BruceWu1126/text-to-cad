"""A document's mesh bytes do not depend on what warmed the store first.

Law 5: same inputs, same bytes. Every mesh is OCCT's, derived once per component
and tolerance into the store's content-addressed mesh entries, and three paths
reach them: a build exporting its own declared mesh, which derives them in its
worker from its already loaded kernel and shapes; a
door's export, which derives them in build-pool jobs; and a snapshot, which
derives the meshes it draws. They share a key, so whichever arrives first
decides what every later export of that document reads -- and the bytes must
not care which one that was.

One boundary is geometry's, not bytes': the build's own export meshes the body
the build held, and a door's from an empty store meshes that body as read from
the file -- one part (law 20), other bytes, their faces in another order -- so
across it the two meshes are compared within the tolerance they were made at
(law 5: by geometry, never by file hash).

The fixture is a box, a CYLINDER and a second box: an analytic curved face is
what carries a seam, and a box-only model would pass either way.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support.mesh_geometry import binary_stl_triangles, mesh_deviation, tolerance_of
from tests.python.support.paths import add_repo_path

add_repo_path("packages/cadgen/src")

REPO = Path(__file__).resolve().parents[4]
PYTHON = sys.executable

MODEL = textwrap.dedent("""\
    from cadgen import step, stl


    @step
    @stl
    def fixture():
        from build123d.geometry import Location
        from build123d.topology import Compound, Solid

        base = Solid.make_box(40, 30, 6)
        base.label = "base"
        shaft = Solid.make_cylinder(5, 24).locate(Location((20, 15, 6)))
        shaft.label = "shaft"
        arm = Solid.make_box(10, 8, 4).locate(Location((30, 20, 30)))
        arm.label = "arm"
        model = Compound(children=[base, shaft, arm])
        model.label = "fixture"
        return model


    if __name__ == "__main__":
        fixture()
    """)

DOOR_MODULES = {"glb": "glb_build", "3mf": "threemf_build", "stl": "stl_build"}

class MeshExportEngineDeterminismTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="mesh-engine-determinism-")).resolve()
        self.stores: set[str] = set()
        self.addCleanup(self._cleanup)
        self.base_env = dict(os.environ)
        self.base_env.update({
            "CADGEN_COMPONENT_WORKERS": "1",
            "PYTHONPATH": str(REPO / "packages/cadgen/src"),
        })

    def _cleanup(self) -> None:
        from cadgen.daemon.client import watch_jobs

        # A script may answer before its exports finish. Even a failed
        # assertion keeps every private store until its owned requests end.
        # If the ledger disconnects, retain the directory rather than race it.
        if self.stores:
            with mock.patch.dict(os.environ, self.base_env):
                update = watch_jobs()
                self.assertIsNotNone(update, "the build daemon did not answer the fixture's cleanup watch")
                while any(os.path.realpath(job.get("storeRoot", "")) in self.stores and job["finishedAt"] is None
                          for job in update["jobs"]):
                    update = watch_jobs(update["jobsCursor"])
                    self.assertIsNotNone(update, "the build daemon disconnected before the fixture finished")
        shutil.rmtree(self.root)

    def _run(self, argv: list[str], *, cwd: Path, store: Path, await_output: str = "fixture.step") -> None:
        env = dict(self.base_env, CADGEN_CACHE_DIR=str(store))
        self.stores.add(os.path.realpath(store))
        proc = subprocess.run(
            [PYTHON, *argv], cwd=str(cwd), env=env,
            capture_output=True, text=True, timeout=600,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        from cadgen._internal.doors import await_writes

        with mock.patch.dict(os.environ, env):
            await_writes(cwd / await_output)

    def _cli(self, module: str, *args: str, cwd: Path, store: Path) -> None:
        self._run(["-c", f"from cadgen.cli.{module} import main; raise SystemExit(main())", *args],
                  cwd=cwd, store=store)

    def _doors(self, *, cwd: Path, store: Path) -> None:
        """Every door's export of the document, `out.<format>`, one after another in ONE
        process, as a warm worker serves them: each reads the store the ones before it
        filled, exactly as separate runs on that store would."""
        calls = "".join(
            f"code = __import__('cadgen.cli.{module}', fromlist=['main']).main(['fixture.step', 'out.{fmt}'])\n"
            "if code:\n    raise SystemExit(code)\n"
            for fmt, module in DOOR_MODULES.items()
        )
        self._run(["-c", calls], cwd=cwd, store=store)

    def _document(self, name: str) -> Path:
        """A fresh directory holding ONLY the written document (law 1)."""
        directory = self.root / name
        directory.mkdir()
        for artifact in self.source.parent.glob("fixture.step*"):
            if artifact.suffix != ".py":
                (directory / artifact.name).write_bytes(artifact.read_bytes())
        return directory

    def test_meshes_are_the_same_bytes_built_cold_and_snapshot_warmed(self) -> None:
        build = self.root / "build"
        build.mkdir()
        self.source = build / "fixture.py"
        self.source.write_text(MODEL, encoding="utf-8")
        # The ordinary client hands the build to a daemon worker. STEP may
        # already be saved while that worker still owes the declared STL.
        self._run([str(self.source)], cwd=build, store=self.root / "build-store", await_output="fixture.stl")
        self.assertTrue((build / "fixture.step").is_file(), "the model script writes its STEP")
        built = (build / "fixture.stl").read_bytes()

        cold_dir, warm_dir = self._document("cold"), self._document("warm")
        cold_store, warm_store = self.root / "store-cold", self.root / "store-warm"

        # The warm store renders the document FIRST, which fills the mesh store
        # for the snapshot. Its exports then read those entries instead of
        # deriving their own.
        self._cli("step_snapshot", "fixture.step", "shot.png", "--width", "200", "--height", "150",
                  cwd=warm_dir, store=warm_store)

        self._doors(cwd=cold_dir, store=cold_store)
        self._doors(cwd=warm_dir, store=warm_store)
        for fmt in DOOR_MODULES:
            with self.subTest(format=fmt):
                out = f"out.{fmt}"
                cold_bytes = (cold_dir / out).read_bytes()
                warm_bytes = (warm_dir / out).read_bytes()
                self.assertEqual(
                    cold_bytes, warm_bytes,
                    f"{fmt} exported {len(cold_bytes)} bytes from a cold store and "
                    f"{len(warm_bytes)} from a snapshot-warmed one, and they differ: the "
                    "mesh a document exports depends on which path reached the store "
                    "first (law 5)",
                )
        from cadgen.tessellation_policy import DISPLAY_TESSELLATION

        own, door = binary_stl_triangles(built), binary_stl_triangles((cold_dir / "out.stl").read_bytes())
        self.assertLessEqual(
            mesh_deviation(own, door), tolerance_of(DISPLAY_TESSELLATION["chordTolerance"], own, door),
            "the build's own STL, meshed in its process from the body it held, describes other "
            "geometry than a door's, meshed in build-pool jobs from the body as read from the file (law 5)",
        )


if __name__ == "__main__":
    unittest.main()
