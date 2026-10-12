"""A build derives its parts' display data before it reports done; a view only reads.

A two-part model declaring an STL at the display tolerance, built in this process with the
daemon off (STORE.md §3, a build derives its parts' display data): the build meshes each
part once and the STL is written from those meshes; a list and a view of the saved STEP
start no job; a part the store lost is derived alone, by one job; and a no-op build of the
model repairs a cleared store.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support.inline_artifacts import inline_artifacts
from tests.python.support.paths import add_repo_path
from tests.python.support.tmp_root import generated_cad_directory

add_repo_path("packages/cadgen/src")

MODEL = """
from cadgen import step, stl
from cadgen import build123d as bd


@stl(out="../STL/pair.stl")
@step(out="../STEP/pair.step")
def pair():
    block = bd.Box(10, 10, 10)
    block.label = "block"
    peg = bd.Pos(20, 0, 0) * bd.Cylinder(3, 10)
    peg.label = "peg"
    return bd.Compound(children=[block, peg], label="pair")


if __name__ == "__main__":
    pair()
"""

NO_JOB = AssertionError("a view started a derivation job")


class BuildDerivesDisplayData(unittest.TestCase):
    def setUp(self) -> None:
        temporary = generated_cad_directory(prefix="build-display-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / "src").mkdir()
        self.script = self.root / "src/pair.py"
        self.script.write_text(MODEL, encoding="utf-8")
        self.document = self.root / "STEP/pair.step"
        self.store = self.root / "store"
        patch = mock.patch.dict(os.environ, {"CADGEN_CACHE_DIR": str(self.store), "CADGEN_DAEMON": "0"})
        patch.start()
        self.addCleanup(patch.stop)

    def build(self, mesher=None) -> tuple[str, int]:
        """Run the model in this process: its outcome word, and how many meshes OCCT made
        (by ``mesher`` when given). What it printed is ``self.output``."""
        from cadgen._internal import occt_mesh
        from cadgen.cli._run_model import run_model_argv

        output = io.StringIO()
        with mock.patch.object(occt_mesh, "mesh_component", wraps=mesher or occt_mesh.mesh_component) as meshed, \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = run_model_argv([str(self.script)])
        self.output = output.getvalue()
        self.assertEqual(code, 0, self.output)
        return self.output.strip().splitlines()[-1].split(" ", 1)[0], meshed.call_count

    def listing(self):
        from cadgen import step

        listed = step.snapshot(self.document, mode="list")
        self.assertTrue(listed.ok)
        return {row["name"]: row["triangleCount"] for row in listed.parts}

    def test_the_build_meshes_each_part_once_and_every_view_after_it_only_reads(self) -> None:
        from cadgen import stl

        self.assertEqual(self.build(), ("built", 2), "one display mesh a part; the STL is written from them")
        self.assertTrue((self.root / "STL/pair.stl").is_file())
        with mock.patch("cadgen.daemon.artifacts.submit_artifact", side_effect=NO_JOB), \
                mock.patch("cadgen.store.parts.derive", side_effect=NO_JOB):
            counts = self.listing()
            exported = stl.build(self.document, self.root / "again.stl", force=True)
        self.assertTrue(exported.ok)
        self.assertEqual(set(counts), {"block", "peg"})
        self.assertEqual(counts["block"], 12)

    def test_a_part_the_store_lost_is_derived_alone_by_one_job(self) -> None:
        from cadgen.store import parts

        self.build()
        expected = self.listing()
        [lost] = [key for key in (self.store / "index/part").iterdir()
                  if parts.probe(key.name)["glb"]["indexCount"] // 3 != 12]
        lost.unlink()
        with inline_artifacts() as jobs:
            self.assertEqual(self.listing(), expected)
        [job] = [call.args[0] for call in jobs.call_args_list]
        self.assertEqual((job["kind"], len(job["cids"])), ("display", 1))
        self.assertIsNotNone(parts.probe(lost.name), "the job stored the part again")

    def test_a_no_op_build_repairs_a_cleared_store(self) -> None:
        self.build()
        shutil.rmtree(self.store / "index/part")
        self.assertEqual(self.build(), ("current", 2))
        self.assertEqual(self.build(), ("current", 0))
        with mock.patch("cadgen.daemon.artifacts.submit_artifact", side_effect=NO_JOB):
            self.assertEqual(set(self.listing()), {"block", "peg"})

    def test_a_no_op_build_after_a_kernel_upgrade_derives_under_this_runtime(self) -> None:
        """The document names the producer of the kernel it was built under, whose parts the
        store no longer holds. A no-op build derives them under this runtime's producer, says
        nothing about it, and re-attests the document, so views ask no worker for the old one."""
        import json

        from cadgen.store import parts

        self.build()
        [entry] = list((self.store / "index/document").iterdir())
        document = json.loads(entry.read_text())
        document["surfaceProducer"] = {"build123d": "0.0.1", "ocp": "7.0.0", "cadqueryOcp": "7.0.0.0"}
        entry.write_text(json.dumps(document))
        shutil.rmtree(self.store / "index/part")
        self.assertEqual(self.build(), ("current", 2))
        self.assertNotIn("warning", self.output)
        self.assertEqual(json.loads(entry.read_text())["surfaceProducer"], parts.producer_identity())
        with mock.patch("cadgen.daemon.artifacts.submit_artifact", side_effect=NO_JOB):
            self.assertEqual(set(self.listing()), {"block", "peg"})

    def test_a_part_that_cannot_be_meshed_is_a_warning_on_the_build_naming_it(self) -> None:
        from cadgen._internal import occt_mesh

        real = occt_mesh.mesh_component

        def mesher(topods, table, face_colors, **options):
            if len(table["faces"]) == 3:  # the peg, a cylinder; the block has six faces
                raise ValueError("boom")
            return real(topods, table, face_colors, **options)

        self.assertEqual(self.build(mesher)[0], "built", "the build stands")
        warnings = [line for line in self.output.splitlines() if "could not be meshed" in line]
        self.assertEqual(warnings, ["[cadgen] warning: pair: #o1.2 peg could not be meshed "
                                    "(MeshProductionError: ValueError: boom); views show it as failed"])


if __name__ == "__main__":
    unittest.main()
