"""``cadgen cam features``: what a part offers a machinist, read from its B-rep, by the selectors a job names.

Each test opens a build123d part written to a temporary directory.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tests.python.support.cam_fixtures import PLATE, heavy_at_import, plate_step, slotted_block_step, value_error


def _directory(cls) -> Path:
    tmp = tempfile.TemporaryDirectory()
    cls.addClassCleanup(tmp.cleanup)
    return Path(tmp.name)


def _listed(result) -> list[tuple]:
    return [(f.kind, f.size, f.reach) for f in result.features]


class Plate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cadgen import cam

        cls.step = plate_step(_directory(cls))
        cls.everything = cam.features(cls.step)
        cls.top = cam.features(cls.step, direction="+Z")

    def test_from_the_top_it_lists_exactly_the_face_the_holes_the_pocket_and_the_outline(self):
        through, blind, pocket = PLATE["through_diameter_mm"], PLATE["blind_diameter_mm"], PLATE["pocket"]
        self.assertEqual(_listed(self.top), [
            ("face", {"length_mm": 100.0, "width_mm": 60.0, "area_mm2": self.top.features[0].size["area_mm2"]}, ("+Z",)),
            ("hole", {"diameter_mm": blind, "depth_mm": PLATE["blind_depth_mm"], "through": False, "axis": "Z"}, ("+Z",)),
            ("hole", {"diameter_mm": through, "depth_mm": 10.0, "through": True, "axis": "Z"}, ("+Z", "-Z")),
            ("hole", {"diameter_mm": through, "depth_mm": 10.0, "through": True, "axis": "Z"}, ("+Z", "-Z")),
            ("pocket", {"length_mm": pocket["length_mm"], "width_mm": pocket["width_mm"], "depth_mm": pocket["depth_mm"],
                        "corner_radius_mm": pocket["corner_radius_mm"]}, ("+Z",)),
            ("profile", {"length_mm": 100.0, "width_mm": 60.0, "height_mm": 10.0, "axis": "Z"}, ("+Z", "-Z")),
        ])
        self.assertEqual((self.top.occurrence, self.top.direction), ("#o1", "+Z"))
        self.assertIn("5 mm blind, 6 mm deep, axis Z, at X 35 Y -15", self.top.human_lines()[2])

    def test_every_ref_is_the_face_it_says(self):
        from build123d import CenterOf

        from cadgen.step_scene import read_scene

        scene = read_scene(self.step)
        hole, pocket = self.top.features[2], self.top.features[4]
        wall, floor = scene.resolve(hole.ref).shape(), scene.resolve(pocket.ref).shape()
        self.assertEqual((wall.geom_type.name, round(wall.radius, 6)), ("CYLINDER", 3.0))
        self.assertAlmostEqual(wall.center(CenterOf.MASS).X, PLATE["through_holes"][0][0], places=6)
        self.assertEqual((floor.geom_type.name, round(floor.center(CenterOf.MASS).Z, 6)), ("PLANE", 5.0))
        self.assertEqual(len(pocket.refs), 9)  # the floor, four walls and four R3 corners
        blind = scene.resolve(self.top.features[1].refs[1]).shape()
        self.assertEqual((blind.geom_type.name, round(blind.center(CenterOf.MASS).Z, 6)), ("PLANE", 4.0))

    def test_without_a_direction_each_side_is_a_face_and_each_axis_an_outline(self):
        faces = {f.reach for f in self.everything.features if f.kind == "face"}
        self.assertEqual(faces, {("+X",), ("-X",), ("+Y",), ("-Y",), ("+Z",), ("-Z",)})
        outlines = [f.reach for f in self.everything.features if f.kind == "profile"]
        self.assertEqual(outlines, [("+Z", "-Z"), ("+X", "-X"), ("+Y", "-Y")])
        self.assertEqual(len(self.everything.features), 13)

    def test_a_setup_frame_sees_the_features_its_tool_reaches(self):
        from cadgen._internal.cam.features import features_in_frame, frame
        from cadgen._internal.cam.part import open_part

        seen = features_in_frame(open_part(self.step), frame((0, 0, -1)))  # flipped: the tool comes from below
        self.assertEqual([s.feature.kind for s in seen], ["face", "hole", "hole", "profile"])
        hole = seen[1]
        self.assertEqual([round(c, 6) + 0.0 for c in hole.top + hole.bottom], [-35.0, -15.0, 0.0, -35.0, -15.0, -10.0])

    def test_the_cli_writes_the_listing_as_json(self):
        from cadgen import cli

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["cam", "features", str(self.step), "--json"]), 0)
        payload = json.loads(out.getvalue())
        self.assertEqual((payload["ok"], len(payload["features"])), (True, 13))
        self.assertEqual(payload["features"][7]["refs"], ["#o1.f16"])

    def test_a_direction_is_one_of_the_six_axes(self):
        from cadgen import cam

        self.assertIn('direction: "Z" is not a tool direction; use +X, -X, +Y, -Y, +Z or -Z',
                      value_error(self, cam.features, self.step, direction="Z"))


class SlottedBlock(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cadgen import cam

        cls.result = cam.features(slotted_block_step(_directory(cls)), direction="+Z")

    def test_an_open_slot_and_a_step_beside_it(self):
        slots = [f.size for f in self.result.features if f.kind == "slot"]
        self.assertEqual(slots, [{"width_mm": 8.0, "length_mm": 70.0, "depth_mm": 5.0}])
        # The step's floor is a face: the slot's walls stand on the slot floor, not on the step.
        self.assertIn("flat 40 x 10 mm at Z 10", [f.hint for f in self.result.features if f.kind == "face"])

    def test_a_counterbore_is_blind_on_its_shoulder_over_a_through_hole(self):
        holes = [(f.size["diameter_mm"], f.size["depth_mm"], f.size["through"], len(f.refs))
                 for f in self.result.features if f.kind == "hole"]
        self.assertEqual(holes, [(9.0, 4.0, False, 2), (5.0, 16.0, True, 1)])
        self.assertNotIn("pocket", [f.kind for f in self.result.features])


class Imports(unittest.TestCase):
    def test_recognition_is_stdlib_only_until_it_opens_a_part(self):
        self.assertEqual(heavy_at_import("cadgen._internal.cam.features", "cadgen._internal.cam.run"), "")


if __name__ == "__main__":
    unittest.main()
