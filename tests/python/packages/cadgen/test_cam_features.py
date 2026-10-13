"""``cadgen cam features``: what a part offers a machinist, read from its B-rep, by the selectors a job names.

Each test opens a build123d part written to a temporary directory.
"""

from __future__ import annotations

import contextlib
import io
import json
import math
import tempfile
import unittest
from pathlib import Path

from tests.python.support.cam_fixtures import (
    DRILL_POINT_DEG,
    PLATE,
    drilled_block_step,
    heavy_at_import,
    island_pocket_step,
    plate_step,
    slotted_block_step,
    value_error,
)


def _directory(cls) -> Path:
    tmp = tempfile.TemporaryDirectory()
    cls.addClassCleanup(tmp.cleanup)
    return Path(tmp.name)


def _listed(result) -> list[tuple]:
    return [(f.kind, f.size, f.reach) for f in result.features]


def _of(result, kind: str) -> list:
    return [f for f in result.features if f.kind == kind]


def _mass(shape) -> tuple[float, tuple[float, float, float]]:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(getattr(shape, "wrapped", shape), props)
    centre = props.CentreOfMass()
    return props.Mass(), (centre.X(), centre.Y(), centre.Z())


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
                        "corner_radius_mm": pocket["corner_radius_mm"], "islands": []}, ("+Z",)),
            ("profile", {"length_mm": 100.0, "width_mm": 60.0, "height_mm": 10.0, "axis": "Z"}, ("+Z", "-Z")),
        ])
        self.assertEqual((self.top.occurrence, self.top.direction), ("#o1", "+Z"))
        self.assertIn("5 mm blind, 6 mm deep, axis Z, at X 35 Y -15", self.top.human_lines()[2])

    def test_every_face_number_is_the_scenes_selector(self):
        from cadgen._internal.cam.part import open_part
        from cadgen.step_scene import read_scene

        scene, part = read_scene(self.step), open_part(self.step)
        self.assertEqual(len(part.faces), 19)
        for face in part.faces:
            area, centre = _mass(scene.resolve(face.ref).shape())
            self.assertAlmostEqual(face.area, area, places=6, msg=face.ref)
            self.assertLess(math.dist(face.center, centre), 1e-6, face.ref)
        pocket = _of(self.top, "pocket")[0]
        self.assertEqual(len(pocket.refs), 9)  # the floor, four walls and four R3 corners
        self.assertAlmostEqual(_mass(scene.resolve(pocket.ref).shape())[1][2], 5.0, places=6)

    def test_without_a_direction_each_side_is_a_face_and_the_outline_is_round_the_thin_axis(self):
        faces = {f.reach for f in _of(self.everything, "face")}
        self.assertEqual(faces, {("+X",), ("-X",), ("+Y",), ("-Y",), ("+Z",), ("-Z",)})
        self.assertEqual([f.reach for f in _of(self.everything, "profile")], [("+Z", "-Z")])
        self.assertEqual(len(self.everything.features), 11)

    def test_a_setup_frame_sees_the_features_its_tool_reaches(self):
        from cadgen._internal.cam.features import features_in_frame
        from cadgen._internal.cam.job import Setup, Stock
        from cadgen._internal.cam.part import open_part
        from cadgen._internal.cam.setups import setup_frame
        from cadgen._internal.cam.stock import make_stock

        part = open_part(self.step)
        under = setup_frame(Setup("under", direction="-Z"), part.shape, make_stock(Stock("box", (0, 0, 0, 0)), part.shape))
        seen = features_in_frame(part, under)  # turned over: the tool comes from below
        self.assertEqual([s.feature.kind for s in seen], ["face", "hole", "hole", "profile"])
        hole = seen[1]
        self.assertEqual([round(c, 6) + 0.0 for c in hole.top + hole.bottom], [-35.0, -15.0, 0.0, -35.0, -15.0, -10.0])

    def test_the_cli_writes_the_listing_as_json(self):
        from cadgen import cli

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["cam", "features", str(self.step), "--json"]), 0)
        payload = json.loads(out.getvalue())
        self.assertEqual((payload["ok"], len(payload["features"])), (True, 11))
        by_ref = {(f["kind"], f["ref"]): f for f in payload["features"]}
        self.assertEqual(by_ref["hole", "#o1.f16"]["reach"], ["+Z", "-Z"])

    def test_a_direction_is_one_of_the_six_axes(self):
        from cadgen import cam

        self.assertIn('direction: "Z" is not a tool direction; use +X, -X, +Y, -Y, +Z or -Z',
                      value_error(self, cam.features, self.step, direction="Z"))

    def test_a_plate_turned_on_the_table_measures_the_same(self):
        from cadgen import cam

        turned = cam.features(plate_step(_directory(type(self)), "turned", turned_deg=30), direction="+Z")
        pocket, outline = _of(turned, "pocket")[0].size, _of(turned, "profile")[0].size
        self.assertEqual((pocket["length_mm"], pocket["width_mm"]), (40.0, 20.0))
        self.assertEqual((outline["length_mm"], outline["width_mm"]), (100.0, 60.0))


class SlottedBlock(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cadgen import cam

        cls.result = cam.features(slotted_block_step(_directory(cls)), direction="+Z")

    def test_an_open_slot_and_a_step_beside_it(self):
        self.assertEqual([f.size for f in _of(self.result, "slot")], [{"width_mm": 8.0, "length_mm": 70.0, "depth_mm": 5.0}])
        # The step's floor is a face: the slot's walls stand on the slot floor, not on the step.
        self.assertIn("flat 40 x 10 mm at Z 10", [f.hint for f in _of(self.result, "face")])

    def test_a_counterbore_is_part_of_the_hole_under_it(self):
        (hole,) = _of(self.result, "hole")
        self.assertEqual(hole.size, {"diameter_mm": 5.0, "depth_mm": 20.0, "through": True, "axis": "Z",
                                     "counterbore": {"diameter_mm": 9.0, "depth_mm": 4.0}})
        self.assertEqual(len(hole.refs), 3)  # the hole's wall, the counterbore's wall and its shoulder
        self.assertIn("cbore 9 x 4", hole.hint)
        self.assertEqual(_of(self.result, "pocket"), [])


class DrilledBlock(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cadgen import cam
        from cadgen.step_scene import read_scene

        step = drilled_block_step(_directory(cls))
        cls.holes = _of(cam.features(step), "hole")
        cls.scene = read_scene(step)

    def test_a_drill_point_is_the_floor_of_a_blind_hole(self):
        (hole,) = [f for f in self.holes if f.size["diameter_mm"] == 6.8]
        point = 3.4 / math.tan(math.radians(DRILL_POINT_DEG / 2))
        self.assertEqual((hole.size["depth_mm"], hole.size["through"], hole.reach), (12.0, False, ("+Z",)))
        self.assertAlmostEqual(hole.size["point_mm"], point, places=3)
        self.assertEqual([self.scene.resolve(ref).shape().geom_type.name for ref in hole.refs], ["CYLINDER", "CONE"])

    def test_a_hole_split_by_a_cross_hole_is_one_hole(self):
        crossed = sorted((f.size["axis"], f.size["depth_mm"], f.size["through"], len(f.refs))
                         for f in self.holes if f.size["diameter_mm"] == 6.0)
        self.assertEqual(crossed, [("X", 60.0, True, 2), ("Z", 30.0, True, 2)])

    def test_a_countersink_is_part_of_the_hole_under_it(self):
        (hole,) = [f for f in self.holes if f.size["diameter_mm"] == 5.0]
        self.assertEqual((hole.size["depth_mm"], hole.size["countersink"]), (30.0, {"diameter_mm": 10.0, "angle_deg": 90.0}))
        self.assertIn("csk 10 x 90", hole.hint)


class IslandPocket(unittest.TestCase):
    def test_an_island_is_listed_with_its_pocket_and_is_no_outline(self):
        from cadgen import cam

        result = cam.features(island_pocket_step(_directory(type(self))))
        (pocket,) = _of(result, "pocket")
        self.assertEqual(pocket.size["islands"], [{"length_mm": 10.0, "width_mm": 10.0, "height_mm": 5.0}])
        self.assertIn("1 island", pocket.hint)
        self.assertEqual(len(pocket.refs), 13)  # floor, 4 walls, 4 corners, the island's 4 walls
        self.assertEqual([(f.size["length_mm"], f.size["width_mm"]) for f in _of(result, "profile")], [(80.0, 60.0)])


class Placement(unittest.TestCase):
    def test_a_mirrored_occurrence_is_refused(self):
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf
        from OCP.TopLoc import TopLoc_Location

        from cadgen._internal.cam.part import refuse_mirrored

        mirror = gp_Trsf()
        mirror.SetMirror(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(1, 0, 0)))
        self.assertIn("#o1.2 is placed mirrored", value_error(self, refuse_mirrored, "#o1.2", TopLoc_Location(mirror)))
        refuse_mirrored("#o1.1", TopLoc_Location(gp_Trsf()))
        refuse_mirrored("#o1", None)


class Imports(unittest.TestCase):
    def test_recognition_is_stdlib_only_until_it_opens_a_part(self):
        self.assertEqual(heavy_at_import("cadgen._internal.cam.features", "cadgen._internal.cam.run"), "")


if __name__ == "__main__":
    unittest.main()
