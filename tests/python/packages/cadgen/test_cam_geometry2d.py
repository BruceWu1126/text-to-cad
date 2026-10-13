"""The 2D layer the 2.5D operations plan on: depth steps, offsets fine enough for the tolerance, and rings that run
climb. Shapely only; nothing here opens a part."""

from __future__ import annotations

import unittest

from tests.python.support import cam_fixtures  # noqa: F401  (puts cadgen's source on sys.path)


class Geometry(unittest.TestCase):
    def test_depth_steps_are_even_and_end_exactly_on_the_floor(self):
        from cadgen._internal.cam.operations.geometry2d import depth_steps

        self.assertEqual(depth_steps(0.0, -5.0, 2.0), [-5 / 3, -10 / 3, -5.0])
        self.assertEqual(depth_steps(0.0, -4.0, 2.0), [-2.0, -4.0])
        self.assertEqual(depth_steps(-1.0, -1.0, 2.0), [])

    def test_an_offset_arc_strays_no_more_than_an_eighth_of_the_tolerance(self):
        from shapely.geometry import Point

        from cadgen._internal.cam.operations.geometry2d import offset

        grown = offset(Point(0, 0).buffer(1e-9, quad_segs=1), 3.0, 0.01).geoms[0]
        worst = max(3.0 - Point(0, 0).distance(Point(*(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2))))
                    for a, b in zip(grown.exterior.coords, grown.exterior.coords[1:]))
        self.assertLessEqual(worst, 0.01 / 8)

    def test_rings_run_climb_with_the_material_on_the_right(self):
        from shapely.geometry import box

        from cadgen._internal.cam.operations.geometry2d import ring_paths

        def turn(ring):
            return sum(x0 * y1 - x1 * y0 for (x0, y0, _), (x1, y1, _) in zip(ring, ring[1:]))

        pocket = box(0, 0, 10, 10).difference(box(4, 4, 6, 6))
        outer, island = ring_paths(pocket, -1.0, material="outside")
        self.assertGreater(turn(outer), 0)      # round a pocket's walls: counter-clockwise
        self.assertLess(turn(island), 0)        # round an island: clockwise
        self.assertLess(turn(ring_paths(box(0, 0, 10, 10), -1.0, material="inside")[0]), 0)  # round a part: clockwise
        self.assertGreater(turn(ring_paths(box(0, 0, 10, 10), -1.0, material="inside", climb=False)[0]), 0)


if __name__ == "__main__":
    unittest.main()
