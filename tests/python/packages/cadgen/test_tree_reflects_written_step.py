"""What a saved STEP shows is its result.

The build writes its STEP from its result and maps the file's bytes to it
(``cadgen.store.exports``): ``read_step`` on the file answers from that result. This
suite pins the consequence from the outside: warm ``read_step`` == cold ``read_step`` ==
``import_step`` on the written bytes, for an assembly whose members are compounds and
links, and for a composed vendor part whose face colours must survive the round trip.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support.paths import add_repo_path
from tests.python.support.tmp_root import generated_cad_directory
from tests.python.support.warm_daemon import verified

CADGEN_SRC = add_repo_path("packages/cadgen/src")

_PIN = '''from cadgen import build123d as bd, step


@step
def pin():
    return bd.Cylinder(1, 6)


if __name__ == "__main__":
    pin()
'''

# A compound member (two solids in ONE occurrence), a plain member, and two
# links to the pin model: every shape of node the re-read must map back.
_ASSEMBLY = '''from pathlib import Path

from cadgen import build123d as bd, read_step, step

from pin import pin

HERE = Path(__file__).resolve().parent


@step
def rig():
    pair = bd.Compound([bd.Box(2, 2, 2), bd.Pos(4, 0, 0) * bd.Box(2, 2, 2)])
    pair.label = "pair"
    bar = bd.Pos(0, 6, 0) * bd.Box(10, 1, 1)
    bar.label = "bar"
    bar.color = bd.Color(0.2, 0.4, 0.8)
    vendor = read_step(HERE / "vendor.step")
    vendor.label = "vendor"
    left = pin().moved(bd.Pos(-8, 0, 0))
    left.label = "pin_left"
    right = pin().moved(bd.Pos(8, 0, 0) * bd.Rot(0, 90, 0))
    right.label = "pin_right"
    return bd.Compound(children=[pair, bar, vendor.moved(bd.Pos(0, 0, 8)), left, right], label="rig")


if __name__ == "__main__":
    rig()
'''


def _volumes(shape) -> list[float]:
    return sorted(round(solid.volume, 6) for solid in shape.solids())


class TreeReflectsWrittenStep(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = generated_cad_directory(prefix="tree-through-step-")
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name).resolve()
        self.store = self.project / "store"
        self.environment = dict(os.environ)
        self.environment.update(
            {
                "CADGEN_DAEMON": "0",
                "CADGEN_COMPONENT_WORKERS": "1",
                "CADGEN_CACHE_DIR": str(self.store),
                "PYTHONPATH": str(CADGEN_SRC),
            }
        )

    def _run(self, model: str) -> str:
        """Build ``model``; what it said."""
        completed = subprocess.run(
            [sys.executable, str(self.project / model), "--force"],
            cwd=str(self.project),
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=600,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        return completed.stdout + completed.stderr

    def _write(self, name: str, source: str) -> None:
        (self.project / name).write_text(source, encoding="utf-8")

    def _read_three_ways(self, step_path: Path):
        """(warm read_step, cold read_step, import_step) of one file."""
        import build123d as bd

        from cadgen import read_step
        from cadgen.catalog import result_tree_for

        with mock.patch.dict(os.environ, {"CADGEN_CACHE_DIR": str(self.store)}):
            self.assertIsNotNone(result_tree_for(step_path), "the build published no tree for its STEP")
            warm = read_step(step_path)
        with tempfile.TemporaryDirectory(prefix="empty-store-") as empty:
            with mock.patch.dict(os.environ, {"CADGEN_CACHE_DIR": empty}):
                self.assertIsNone(result_tree_for(step_path))
                cold = read_step(step_path)
        return warm, cold, bd.import_step(str(step_path))

    def test_compound_members_links_and_face_colours_round_trip(self) -> None:
        import build123d as bd

        from cadgen.step_export import export_build123d_step_file

        vendor = bd.Box(3, 3, 3)
        vendor.label = "vendor_part"
        # Two coloured faces on a vendor part (per-face colours as coloured XCAF
        # sub-shapes, the way a vendor STEP carries them; build123d's own
        # exporter writes only per-shape colours).
        vendor.cad_face_ordinal_colors = {1: (1.0, 0.0, 0.0, 1.0), 6: (0.0, 1.0, 0.0, 1.0)}
        export_build123d_step_file(vendor, self.project / "vendor.step")
        self._write("pin.py", _PIN)
        self._write("rig.py", _ASSEMBLY)
        self._run("pin.py")
        self._run("rig.py")

        step_path = self.project / "rig.step"
        warm, cold, imported = self._read_three_ways(step_path)
        self.assertEqual(_volumes(warm), _volumes(cold))
        self.assertEqual(_volumes(warm), _volumes(imported))
        self.assertEqual(len(_volumes(warm)), 6)  # pair(2) + bar + vendor + 2 pins

        from cadgen.catalog import result_descriptor_for
        from cadgen.store import parts
        from cadgen.store.objects import object_path
        from cadgen.store.trees import capture_tree

        with mock.patch.dict(os.environ, {"CADGEN_CACHE_DIR": str(self.store)}):
            descriptor = result_descriptor_for(step_path)
            by_name = {occ["name"]: occ for occ in descriptor["occurrences"]}
            # Saved colors are the STEP reader's native-precision values.
            for actual, expected in zip(by_name["bar"]["color"][:3], [0.2, 0.4, 0.8], strict=True):
                self.assertAlmostEqual(actual, expected, places=6)
            # The saved document is the result, its child pins links.
            from cadgen.store.records import read_record

            self.assertEqual(descriptor["tree"], read_record(self.project / "rig.py")["tree"])
            result = json.loads(object_path(descriptor["tree"]).read_text(encoding="utf-8"))
            self.assertEqual({link["name"] for link in result["links"]}, {"pin_left", "pin_right"})
            # The vendor part's face colours came back through the document: the effective
            # recipe its part's derivation meshes with carries them, by face ordinal.
            vendor = capture_tree(descriptor["tree"], retain_payloads=False)[0]["components"][by_name["vendor"]["component"]]
            _topods, colours = parts._decoded(vendor)
            coloured = {tuple(round(c, 3) for c in colour[:3]) for colour in colours.values()}
            self.assertEqual(coloured, {(1.0, 0.0, 0.0), (0.0, 1.0, 0.0)})


if __name__ == "__main__":
    unittest.main()
