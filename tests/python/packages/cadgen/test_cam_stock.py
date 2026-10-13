"""Stock and setups: the stock holds the part as stated, each setup's WCS sits where its origin says, heights are
absolute, the fixture sits where its grip says, and transforms go there and back.

The part is a 100 x 60 x 10 mm plate from (0, 0, 0) to (100, 60, 10), built here with build123d.
"""

from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from tests.python.support.cam_fixtures import heavy_at_import

from cadgen._internal.cam.job import Fixture, Setup, Stock

MARGIN = (2.0, 3.0, 1.0, 4.0)  # x, y each side; 1 mm above the part, 4 mm below it


def plate():
    from build123d import Box, Pos

    return Pos(50, 30, 5) * Box(100, 60, 10)


def placed(setup: Setup, stock: Stock = Stock("box", MARGIN), part=None, **options):
    from cadgen._internal.cam.setups import setup_frame
    from cadgen._internal.cam.stock import make_stock

    part = plate() if part is None else part
    return setup_frame(setup, part, make_stock(stock, part), **options)


class StockTests(unittest.TestCase):
    def test_import_is_stdlib_only(self):
        self.assertEqual(heavy_at_import("cadgen._internal.cam.stock", "cadgen._internal.cam.setups"), "")

    def test_box_margins_are_per_side_with_their_own_top_and_bottom(self):
        from cadgen._internal.cam.stock import make_stock

        stock = make_stock(Stock("box", MARGIN), plate())
        self.assertEqual(stock.bounds, ((-2, -3, -4), (102, 63, 11)))
        self.assertEqual(stock.size, (104, 66, 15))

    def test_cylinder_holds_the_part_about_its_centre_line(self):
        from build123d import Cylinder, Pos

        from cadgen._internal.cam.stock import make_stock

        shaft = Pos(5, 5, 20) * Cylinder(10, 40)
        stock = make_stock(Stock("cylinder", (1.0, 2.0)), shaft)
        self.assertAlmostEqual(stock.diameter_mm, 22, places=6)
        self.assertEqual((stock.length_mm, stock.bounds[0][2], stock.bounds[1][2]), (44, -2, 42))
        bar = make_stock(Stock("cylinder", (0.0, 0.0), diameter_mm=25), shaft)
        self.assertEqual((bar.diameter_mm, bar.bounds[0][:2]), (25, (-7.5, -7.5)))
        across_x = make_stock(Stock("cylinder", (0.0, 0.0), axis="X"), plate())
        self.assertAlmostEqual(across_x.diameter_mm, 2 * math.hypot(30, 5), places=6)
        with self.assertRaisesRegex(ValueError, "stock.diameter_mm: a 18 mm bar does not hold the part"):
            make_stock(Stock("cylinder", (0.0, 0.0), diameter_mm=18), shaft)

    def test_sheet_and_a_blank_from_step(self):
        from build123d import Box, Pos, export_step

        from cadgen._internal.cam.stock import make_stock

        sheet = make_stock(Stock("sheet", (5.0, 5.0), thickness_mm=12), plate())
        self.assertEqual(sheet.bounds, ((-5, -5, 0), (105, 65, 12)))
        with tempfile.TemporaryDirectory() as folder:
            blank, beside = Path(folder, "blank.step"), Path(folder, "beside.step")
            export_step(Pos(50, 30, 6) * Box(110, 70, 12), blank)
            export_step(Pos(250, 30, 6) * Box(110, 70, 12), beside)
            self.assertEqual(make_stock(Stock("from", path=blank), plate()).bounds, ((-5, -5, 0), (105, 65, 12)))
            with self.assertRaisesRegex(ValueError, "stock.from: the blank in beside.step .* does not hold the part"):
                make_stock(Stock("from", path=beside), plate())


class SetupTests(unittest.TestCase):
    def assertPoint(self, actual, expected):
        for a, e in zip(actual, expected):
            self.assertAlmostEqual(float(a), e, places=9)

    def test_stock_top_center_is_zero_at_the_stock_top(self):
        top = placed(Setup("top"))
        self.assertEqual(top.frame.origin, (50, 30, 11))
        self.assertEqual((top.stock_top_z, top.stock_bottom_z), (0, -15))
        self.assertEqual(top.stock_bounds, ((-52, -33, -15), (52, 33, 0)))
        self.assertEqual(top.part_bounds, ((-50, -30, -11), (50, 30, -1)))

    def test_corners_are_min_x_min_y_and_points_are_the_part_frame(self):
        self.assertEqual(placed(Setup("a", origin="stock_top_corner")).frame.origin, (-2, -3, 11))
        self.assertEqual(placed(Setup("b", origin="part_bbox_bottom_corner")).frame.origin, (0, 0, 0))
        self.assertEqual(placed(Setup("c", origin="part_bbox_top_center")).frame.origin, (50, 30, 10))
        self.assertEqual(placed(Setup("d", origin="part_bbox_center")).frame.origin, (50, 30, 5))
        self.assertEqual(placed(Setup("e", origin=(1.0, 2.0, 3.0))).frame.origin, (1, 2, 3))
        corner = placed(Setup("f", origin="stock_top_corner"))
        self.assertEqual(corner.stock_bounds, ((0, 0, -15), (104, 66, 0)))

    def test_a_minus_z_setup_turns_the_part_over(self):
        under = placed(Setup("under", direction="-Z"))
        self.assertEqual(under.frame.rotation, ((1, 0, 0), (0, -1, 0), (0, 0, -1)))
        self.assertEqual(under.frame.origin, (50, 30, -4))  # the stock's bottom face, 4 mm under the part, is on top
        self.assertPoint(under.frame.point_to_setup((50, 30, 10)), (0, 0, -14))
        self.assertEqual(under.part_bounds[1][2], -4)
        corner = placed(Setup("under", direction="-Z", origin="stock_top_corner"))
        self.assertEqual(corner.frame.origin, (-2, 63, -4))  # front left once turned over: min X, max Y of the part
        self.assertEqual(corner.stock_bounds[0], (0, 0, -15))

    def test_side_and_face_directions(self):
        side = placed(Setup("side", direction="+X"))
        self.assertEqual(side.frame.origin, (102, 30, 3.5))
        self.assertPoint(side.frame.vector_to_setup((1, 0, 0)), (0, 0, 1))
        self.assertEqual(side.stock_top_z, 0)
        normals = {"#o1.f4": (0.0, 1.0, 1.0)}
        tilted = placed(Setup("tilted", direction="#o1.f4"), face_normal=normals.__getitem__)
        self.assertPoint(tilted.frame.vector_to_setup((0, 1, 1)), (0, 0, math.sqrt(2)))
        self.assertAlmostEqual(tilted.stock_top_z, 0, places=6)
        with self.assertRaisesRegex(ValueError, "#o1.f4: a face selector needs the part's faces"):
            placed(Setup("tilted", direction="#o1.f4"))

    def test_clearance_and_retract_are_absolute_heights_above_the_stock_top(self):
        top = placed(Setup("top", origin="part_bbox_bottom_corner", clearance_mm=15, retract_mm=2))
        self.assertEqual((top.stock_top_z, top.clearance_z, top.retract_z, top.above_stock(5)), (11, 26, 13, 16))

    def test_the_vise_jaws_stand_grip_above_the_stock_bottom(self):
        vise = Fixture("vise", {"jaw_height_mm": 20, "grip_mm": 5, "parallels_mm": 10})
        fixture = placed(Setup("top", fixture=vise)).fixture
        front, back, front_parallel, _ = fixture.volumes
        self.assertEqual((front.min, front.max), ((-75, -53, -30), (75, -33, -10)))
        self.assertEqual((back.min[1], back.max[2]), (33, -10))
        self.assertEqual((front_parallel.min, front_parallel.max), ((-52, -33, -25), (52, -27, -15)))
        self.assertEqual(fixture.assumed, ("jaw_width_mm",))
        derived = placed(Setup("top", fixture=Fixture("vise", {"jaw_height_mm": 20, "parallels_mm": 10}))).fixture
        self.assertEqual((derived.sizes["grip_mm"], derived.volumes[0].max[2]), (10, -5))
        self.assertIn("grip_mm", derived.assumed)
        with self.assertRaisesRegex(ValueError, "fixture.grip_mm: the jaws hold 20 mm of stock that is 15 mm tall"):
            placed(Setup("top", fixture=Fixture("vise", {"grip_mm": 20})))
        chuck = placed(Setup("top", fixture=Fixture("chuck", {"diameter_mm": 160, "jaw_length_mm": 25, "grip_mm": 6})))
        (jaws,) = chuck.fixture.volumes
        self.assertEqual((jaws.center, jaws.radius, jaws.z_min, jaws.z_max), ((0, 0), 80, -34, -9))
        self.assertEqual(placed(Setup("top")).fixture.volumes, ())

    def test_transforms_round_trip(self):
        import numpy as np

        from cadgen._internal.cam.stock import bounds_of

        normals = {"#o1.f9": (0.3, -0.5, 0.8)}
        setup = placed(Setup("odd", direction="#o1.f9", origin=(7.0, -3.0, 2.0)), face_normal=normals.__getitem__)
        frame = setup.frame
        points = np.random.default_rng(1).uniform(-100, 100, (20, 3))
        self.assertTrue(np.allclose(frame.point_to_part(frame.point_to_setup(points)), points, atol=1e-9))
        self.assertTrue(np.allclose(frame.vector_to_part(frame.vector_to_setup(points)), points, atol=1e-9))
        self.assertTrue(np.allclose(frame.point_to_setup((7, -3, 2)), 0, atol=1e-12))
        back = frame.shape_to_part(frame.shape_to_setup(plate()))
        self.assertTrue(np.allclose(np.asarray(bounds_of(back)), np.asarray(((0, 0, 0), (100, 60, 10))), atol=1e-6))
        top = placed(Setup("top", origin="stock_top_corner")).frame
        corners = top.point_to_setup([(0, 0, 0), (100, 60, 10)])
        self.assertTrue(np.allclose(corners, bounds_of(top.shape_to_setup(plate())), atol=1e-9))
        self.assertEqual(placed(Setup("top", direction="#o1.f9"), face_normal=normals.__getitem__),
                         placed(Setup("top", direction="#o1.f9"), face_normal=normals.__getitem__))

    def test_reachable_only_along_the_tool_axis(self):
        from cadgen._internal.cam.setups import reachable

        top, under = placed(Setup("top")), placed(Setup("under", direction="-Z"))
        self.assertEqual([reachable(d, top) for d in ("+Z", (0, 0, 3), "-Z", "+Y")], [True, True, False, False])
        self.assertEqual([reachable(d, under) for d in ("+Z", "-Z")], [False, True])
        self.assertFalse(reachable((0, math.sin(0.01), math.cos(0.01)), top))


if __name__ == "__main__":
    unittest.main()
