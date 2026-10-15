"""A face no mesher covers leaves its part drawn without it, and every door says so.

A tiny assembly -- a 20 x 10 x 5 plate and a radius-5 pin -- whose pin's side is made a
face the tessellator cannot cover: its geometry raises inside OCCT, as a vendor STEP's
broken face can. That one face once failed the whole document: the list, the view and
the STL all ended in a traceback. Now the pin is meshed without it, the plate whole, and
each door does its work and names the face it left out: the list counts the pin without
it, the view draws the rest, and the STL is written open where the face was.

The compile meshes every part before it reports done; here it runs in this process
(``step.compile``), so the refusal reaches it, and every door after it only reads.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from tests.python.support.cad_test_roots import ClassCadRoots
from tests.python.support.paths import add_repo_path
from tests.python.support.png import read_png

add_repo_path("packages/cadgen/src")

SIZE = (320, 240)
# The pin by its occurrence token, the face by its element token: the refs a person pastes.
WARNED = r"^#o:[0-9a-f]{12} pin: 1 face \(f:[0-9a-f]{12}\) could not be meshed, so "


def write_assembly(path) -> None:
    from build123d import Compound, Location, Solid

    from cadgen.step_export import export_build123d_step_file

    plate = Solid.make_box(20, 10, 5)
    plate.label = "plate"
    pin = Solid.make_cylinder(5, 20).moved(Location((30, 0, 0)))
    pin.label = "pin"
    export_build123d_step_file(Compound(children=[plate, pin], label="asm"), path)


def refusing_the_pins_side(real):
    """``tessellate._frame`` raising for a cylinder, as OCCT does reading a face whose geometry it
    cannot evaluate: the face is read with no trim, so it is meshed as nothing and named."""
    def frame(geometry):
        if type(geometry).__name__ == "gp_Cylinder":
            raise RuntimeError("Standard_Failure: the surface cannot be evaluated")
        return real(geometry)
    return frame


class UnmeshedFaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._roots = ClassCadRoots(prefix="unmeshed-face-")
        cls.workspace = cls._roots.cad_root
        document = cls.workspace / "asm.step"
        write_assembly(document)
        import cadgen.step as step_door
        import cadgen.stl as stl_door
        from cadgen._internal import tessellate

        with mock.patch.dict(os.environ, {"CADGEN_DAEMON": "0"}), \
                mock.patch.object(tessellate, "_frame", refusing_the_pins_side(tessellate._frame)):
            step_door.compile(document)
        # Each door only reads what the compile stored: none starts a derivation job.
        with mock.patch.dict(os.environ, {"CADGEN_DAEMON": "0"}), \
                mock.patch("cadgen.daemon.artifacts.submit_artifact", side_effect=AssertionError("a door derived")):
            cls.listing = step_door.snapshot(document, mode="list")
            cls.view = step_door.snapshot(document, cls.workspace / "asm.png", width=SIZE[0], height=SIZE[1])
            cls.export = stl_door.build(document, cls.workspace / "asm.stl")
        cls.image = read_png((cls.workspace / "asm.png").read_bytes())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._roots.cleanup()

    def test_the_list_counts_the_pin_without_its_side_and_says_so(self) -> None:
        self.assertTrue(self.listing.ok)
        rows = {row["name"]: row for row in self.listing.parts}
        self.assertEqual((12, 24), (rows["plate"]["triangleCount"], rows["plate"]["vertexCount"]), "the plate is whole")
        # The pin's two caps, and nothing of its side.
        self.assertGreater(rows["pin"]["triangleCount"], 0)
        [warning] = self.listing.warnings
        self.assertRegex(warning, WARNED + "its triangle count leaves it out$")

    def test_the_view_draws_the_rest_and_says_so(self) -> None:
        self.assertTrue(self.view.ok)
        self.assertEqual(1, len(self.view.files))
        self.assertEqual(SIZE, (self.image.width, self.image.height))
        background = self.image.pixel(0, 0)
        drawn = sum(self.image.pixel(x, y) != background for x in range(0, SIZE[0], 4) for y in range(0, SIZE[1], 4))
        self.assertGreater(drawn, 200, "the view drew the plate and what it has of the pin")
        [warning] = self.view.warnings
        self.assertRegex(warning, WARNED + "the view does not draw it$")

    def test_the_stl_is_written_open_where_the_side_was_and_says_so(self) -> None:
        import trimesh

        self.assertTrue(self.export.ok)
        [written] = self.export.files
        mesh = trimesh.load(written.path)
        self.assertGreater(len(mesh.faces), 12, "the plate and the pin's caps")
        self.assertFalse(mesh.is_watertight)
        [warning] = self.export.warnings
        self.assertRegex(warning, WARNED + r"asm\.stl has a hole in place of it: it is not watertight$")


if __name__ == "__main__":
    unittest.main()
