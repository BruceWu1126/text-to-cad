"""Tool shapes: the radius at a height for the shaped ends, and a closed, deterministic mesh for the GLB."""

from __future__ import annotations

import math
import unittest
from collections import Counter

from tests.python.support.cam_fixtures import value_error

from cadgen._internal.cam.job import Holder, Tool
from cadgen._internal.cam.tools import PROFILE_TYPES, profile


def tool(kind: str, **size) -> Tool:
    return Tool("T1", 1, kind, diameter_mm=6, flute_length_mm=18, stickout_mm=30, holder=Holder(32, 40), size=size)


SIZES = {"bull_endmill": {"corner_radius_mm": 1}, "chamfer_mill": {"tip_angle_deg": 90},
         "v_bit": {"tip_angle_deg": 60}, "thread_mill": {"pitch_mm": 1}, "tap": {"pitch_mm": 1},
         "counterbore": {"pilot_diameter_mm": 3}}


class ToolsTest(unittest.TestCase):
    def test_radius_at_a_height_follows_each_end_shape(self):
        ball = profile(tool("ball_endmill"))
        self.assertAlmostEqual(ball.radius_at(1.0), math.sqrt(9 - 4))
        self.assertEqual((ball.radius_at(0.0), ball.radius_at(5.0), ball.tip_length_mm), (0.0, 3.0, 3.0))
        bull = profile(tool("bull_endmill", corner_radius_mm=1))
        self.assertEqual(bull.radius_at(0.0), 2.0)
        self.assertAlmostEqual(bull.radius_at(0.5), 2 + math.sqrt(1 - 0.25))
        chamfer = profile(tool("chamfer_mill", tip_angle_deg=90, tip_diameter_mm=1))
        self.assertAlmostEqual(chamfer.radius_at(1.0), 1.5)
        vbit = profile(tool("v_bit", tip_angle_deg=60))
        self.assertAlmostEqual(vbit.radius_at(1.0), math.tan(math.radians(30)))
        self.assertAlmostEqual(vbit.tip_length_mm, 3 / math.tan(math.radians(30)))

    def test_the_holder_is_above_the_stickout_and_the_widest_radius_wins_at_the_step(self):
        flat = profile(tool("flat_endmill"))
        self.assertEqual((flat.radius_at(29.0), flat.radius_at(30.0), flat.radius_at(70.0), flat.radius_at(71.0)),
                         (3.0, 16.0, 16.0, 0.0))
        self.assertEqual([(part.part, part.radius) for part in flat.envelope()],
                         [("cutter", 3.0), ("shank", 3.0), ("holder", 16.0)])

    def test_every_mesh_is_closed_outward_and_deterministic(self):
        import numpy as np

        for kind in PROFILE_TYPES:
            with self.subTest(kind=kind):
                shape = profile(tool(kind, **SIZES.get(kind, {})))
                positions, triangles = shape.mesh()
                again = shape.mesh()
                self.assertEqual((positions.tobytes(), triangles.tobytes()), (again[0].tobytes(), again[1].tobytes()))
                edges = Counter((a, b) for tri in triangles.tolist() for a, b in zip(tri, tri[1:] + tri[:1]))
                self.assertTrue(all(count == 1 and (b, a) in edges for (a, b), count in edges.items()))
                corners = positions[triangles].astype(float)
                volume = float(np.einsum("ij,ij->i", corners[:, 0], np.cross(corners[:, 1], corners[:, 2])).sum()) / 6
                self.assertGreater(volume, 0)
                self.assertAlmostEqual(float(positions[:, 2].min()), 0.0)
                self.assertAlmostEqual(float(positions[:, 2].max()), shape.length_mm, places=4)

    def test_a_tool_with_no_shape_yet_is_refused(self):
        insert = Tool("T9", 9, "turning_insert", size={"nose_radius_mm": 0.4})
        self.assertIn("no tool shape yet", value_error(self, profile, insert))


if __name__ == "__main__":
    unittest.main()
