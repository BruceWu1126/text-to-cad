"""2D edge work on the plate: chamfer_2d, engrave (V-carve) and trace.

The posted program, read back, is the planned toolpath (linuxcnc and fanuc); every toolpath is sound
(cam_fixtures.assert_sound, the engraving's clear_tool pass too); the same job plans the same moves twice;
a chamfer is as wide as asked at the top and touches nothing under it; a V-carve goes as deep as its widest point
allows and its cone never crosses the region's boundary; a trace runs at its depth, its side of the curve.
"""

from __future__ import annotations

import copy
import math
import tempfile
import unittest
from pathlib import Path

from tests.python.support.cam_fixtures import assert_backplot, assert_sound, path_points, plate_step, value_error

POCKET, OUTLINE = "#o1.f19", "#o1.f2"
RECT = [[-10, 15], [10, 15], [10, 25], [-10, 25]]          # 20 x 10 mm on the plate's top, clear of the pocket
SQUARE = [[-45, -25], [-25, -25], [-25, -5], [-45, -5]]

#: The stock's top is the part's top (no margin above), so the WCS zero is on the plate's top face.
JOB = {
    "machine": {"kind": "mill", "post": "linuxcnc"},
    "material": "aluminum-6061",
    "stock": {"type": "box", "margin_mm": [2, 2, 0, 1]},
    "setups": [{"id": "top"}],
    "tools": [{"id": "T1", "type": "v_bit", "diameter_mm": 12, "tip_angle_deg": 90, "flutes": 2},
              {"id": "T2", "type": "flat_endmill", "diameter_mm": 3, "flutes": 2, "flute_length_mm": 10},
              {"id": "T3", "type": "chamfer_mill", "diameter_mm": 6, "tip_angle_deg": 90, "flutes": 2},
              {"id": "T4", "type": "flat_endmill", "diameter_mm": 2, "flutes": 2, "flute_length_mm": 6}],
    "operations": [
        {"op": "chamfer_2d", "tool": "T3", "features": [OUTLINE, POCKET]},
        {"op": "engrave", "tool": "T1", "region": RECT},
        {"op": "engrave", "tool": "T1", "region": RECT, "max_depth_mm": 2, "clear_tool": "T2", "stepdown_mm": 1},
        {"op": "trace", "tool": "T4", "region": SQUARE, "depth_mm": 1, "offset": "inside"},
    ],
}
TOP = 0.0


class Edges(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cadgen._internal.cam.job import parse_job
        from cadgen._internal.cam.operations import get_operation
        from cadgen._internal.cam.operations.context import PlanContext

        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.step = plate_step(Path(tmp.name))
        cls.job = parse_job(JOB)
        cls.shared: dict = {}
        cls.contexts, cls.planned = [], []
        for index in range(len(cls.job.operations)):
            ctx = PlanContext.from_step(cls.step, cls.job, index, shared=cls.shared)
            cls.contexts.append(ctx)
            cls.planned.append(get_operation(ctx.operation.op).plan(ctx.params, ctx))

    def each(self):
        for index, (ctx, planned) in enumerate(zip(self.contexts, self.planned)):
            with self.subTest(op=f"{index} {ctx.operation.op}"):
                yield ctx, planned

    def plan(self, document, index):
        from cadgen._internal.cam.operations import get_operation
        from cadgen._internal.cam.operations.context import PlanContext

        ctx = PlanContext.from_step(self.step, document, index, shared=self.shared)
        return ctx, (lambda: get_operation(ctx.operation.op).plan(ctx.params, ctx))

    # --- every operation ---------------------------------------------------------------------------------

    def test_the_posted_program_read_back_is_the_toolpath(self):
        from cadgen._internal.cam.posts import get_post

        for ctx, planned in self.each():
            for post in ("linuxcnc", "fanuc"):
                text = get_post(post).program(self.job, self.job.setups[0], planned.toolpaths, part="plate")
                assert_backplot(self, text, post, planned.toolpaths)
            for tp in planned.toolpaths:
                self.assertFalse([m for m in tp.moves if m.kind == "plunge" and m.xyz[2] < TOP - 1e-6
                                  and tp.tool == ctx.tool.id and ctx.operation.op != "engrave"])

    def test_no_gouge_no_steep_descent_into_material_no_low_rapid(self):
        for ctx, planned in self.each():
            assert_sound(self, ctx, planned)

    def test_the_same_job_plans_the_same_moves(self):
        from cadgen._internal.cam.operations import get_operation
        from cadgen._internal.cam.operations.context import PlanContext

        fresh: dict = {}
        for ctx, planned in self.each():
            again = PlanContext.from_step(self.step, JOB, ctx.index, shared=fresh)
            self.assertEqual(get_operation(again.operation.op).plan(again.params, again), planned)

    # --- chamfer_2d --------------------------------------------------------------------------------------

    def test_a_chamfer_is_as_wide_as_asked_and_touches_nothing_under_it(self):
        from shapely.geometry import Point

        from cadgen._internal.cam.operations.geometry2d import section

        ctx, planned = self.contexts[0], self.planned[0]
        self.assertEqual([(e["ref"], e["side"], e["width_mm"]) for e in planned.stats["edges"]],
                         [(OUTLINE, "outside", 0.5), (POCKET, "inside", 0.5)])
        tip_z = TOP - 0.5 - 0.2                                  # 90 degrees: as deep as wide, the tip 0.2 under it
        walls = section(ctx, TOP - 0.3).boundary                 # the walls just under the top, before the chamfer
        points = [p for p in path_points(planned.toolpath, step=0.3) if abs(p[2] - tip_z) < 1e-6]
        self.assertGreater(len(points), 200)
        for p in points:
            off = walls.distance(Point(p[:2]))
            self.assertAlmostEqual((TOP - p[2]) * 1.0 - off, 0.5, delta=ctx.tol)          # cone radius at the top
            self.assertLess((TOP - 0.5 - 0.05 - p[2]) * 1.0, off - 0.04)                   # just under the chamfer

    def test_a_chamfer_the_cone_cannot_reach_or_a_wrong_tool_is_refused(self):
        from cadgen._internal.cam.job import parse_job
        from cadgen._internal.cam.operations.base import PlanError

        document = copy.deepcopy(JOB)
        document["operations"][0]["width_mm"] = 3
        ctx, run = self.plan(document, 0)
        with self.assertRaises(PlanError) as caught:
            run()
        self.assertEqual(caught.exception.kind, "unreachable")
        self.assertIn("a 3 mm chamfer with the tip 0.2 mm under it needs 3.2 mm of cone, and T3's cone is 3 mm tall",
                      str(caught.exception))
        document = copy.deepcopy(JOB)
        document["operations"][0]["tool"] = "T2"
        self.assertIn("T2 is a flat_endmill, and chamfer_2d cuts with a chamfer_mill or v_bit",
                      value_error(self, parse_job, document))

    # --- engrave -------------------------------------------------------------------------------------------

    def assert_inside_the_cone(self, ctx, toolpath, region, floor):
        from shapely.geometry import Point, Polygon

        area = Polygon(region)
        points = [p for p in path_points(toolpath, step=0.2) if p[2] < TOP - 1e-6]
        self.assertAlmostEqual(min(p[2] for p in points), floor, delta=0.02)
        for p in points:
            self.assertTrue(area.buffer(ctx.tol).contains(Point(p[:2])), p)
            self.assertLessEqual(TOP - p[2], area.boundary.distance(Point(p[:2])) + ctx.tol, p)   # tan 45 = 1
        return points

    def test_a_v_carve_goes_as_deep_as_its_widest_point_and_never_past_the_boundary(self):
        ctx, planned = self.contexts[1], self.planned[1]
        self.assert_inside_the_cone(ctx, planned.toolpath, RECT, TOP - 5 / math.tan(math.radians(45)))
        points = path_points(planned.toolpath, step=0.2)
        for corner in RECT:                                     # it runs out to each corner at no depth
            self.assertLess(min(math.dist(p[:2], corner) + abs(p[2] - TOP) for p in points), 0.05)
        self.assertEqual(planned.before, ())

    def test_a_capped_v_carve_clears_a_flat_bottom_first_with_its_clear_tool(self):
        from shapely.geometry import Point, Polygon

        ctx, planned = self.contexts[2], self.planned[2]
        (clear,) = planned.before
        self.assertEqual((clear.tool, planned.toolpath.tool), ("T2", "T1"))
        self.assert_inside_the_cone(ctx, planned.toolpath, RECT, TOP - 2)
        self.assertEqual(planned.stats["areas"][0]["levels"], [-1.0, -2.0])
        flat = Polygon(RECT).buffer(-2, join_style="mitre")   # where the V is deeper than 2 mm
        cutting = [p for p in path_points(clear, step=0.2) if p[2] < TOP - 1e-6]
        self.assertAlmostEqual(min(p[2] for p in cutting), TOP - 2, delta=1e-6)
        for p in cutting:
            self.assertGreaterEqual(flat.exterior.distance(Point(p[:2])), 1.5 - ctx.tol)
            self.assertTrue(flat.contains(Point(p[:2])))

    def test_a_v_carve_that_needs_a_flat_bottom_and_has_no_clear_tool_is_refused(self):
        from cadgen._internal.cam.operations.base import PlanError

        document = copy.deepcopy(JOB)
        del document["operations"][2]["clear_tool"]
        ctx, run = self.plan(document, 2)
        with self.assertRaises(PlanError) as caught:
            run()
        self.assertEqual(caught.exception.kind, "unreachable")
        self.assertIn("name a flat end mill to clear it with clear_tool", str(caught.exception))

    # --- trace ---------------------------------------------------------------------------------------------

    def level_points(self, toolpath, z):
        points = [p for p in path_points(toolpath, step=0.3) if abs(p[2] - z) < 1e-6]
        self.assertGreater(len(points), 100)
        self.assertAlmostEqual(min(p[2] for p in path_points(toolpath)), z, delta=1e-6)
        return points

    def test_a_trace_round_a_loop_runs_at_its_depth_inside_or_outside_it(self):
        from shapely.geometry import Point, Polygon

        square = Polygon(SQUARE)
        ctx, planned = self.contexts[3], self.planned[3]
        document = copy.deepcopy(JOB)
        document["operations"][3]["offset"] = "outside"
        _, run = self.plan(document, 3)
        for inside, toolpath in ((True, planned.toolpath), (False, run().toolpath)):
            for p in self.level_points(toolpath, TOP - 1):
                self.assertEqual(square.contains(Point(p[:2])), inside)
                self.assertAlmostEqual(square.exterior.distance(Point(p[:2])), 1.0, delta=ctx.tol)
        document["operations"][3]["offset"] = "left"
        _, run = self.plan(document, 3)
        self.assertIn("operations[3].offset: region is a closed loop; offset it inside or outside, not left",
                      value_error(self, run))

    def test_an_open_chain_of_edges_is_walked_from_its_start(self):
        from shapely.geometry import LineString, Point, box

        from cadgen._internal.cam.operations.edges_2d import _edge_points

        ctx = self.contexts[3]
        top = next(f for f in ctx.part.faces if f.surface == "plane" and f.normal[2] > 0.99 and f.center[2] > 9.99
                   and f.area > 5000)
        edges = []                                                # the plate's top outline but its front edge
        for e in sorted(top.outer_edges):
            ends = [ctx.to_setup(p) for p in _edge_points(ctx, e)]
            if not all(abs(p[1] + 30) < 1e-6 for p in ends):
                edges.append(f"#o1.e{e}")
        self.assertEqual(len(edges), 3)
        document = copy.deepcopy(JOB)
        document["operations"][3] = {"op": "trace", "tool": "T4", "features": edges, "depth_mm": 1, "offset": "left"}
        _, run = self.plan(document, 3)
        self.assertIn("operations[3].start:", value_error(self, run))
        chain = LineString([(-50, -30), (-50, 30), (50, 30), (50, -30)])
        plate = box(-50, -30, 50, 30)
        for start, inside in (([-50, -30], False), ([50, -30], True)):   # walked clockwise, left is outside
            document["operations"][3]["start"] = start
            ctx, run = self.plan(document, 3)
            for p in self.level_points(run().toolpath, TOP - 1):
                self.assertAlmostEqual(chain.distance(Point(p[:2])), 1.0, delta=ctx.tol)
                if abs(p[1] + 30) > 1.5:
                    self.assertEqual(plate.contains(Point(p[:2])), inside, (start, p))
        document["operations"][3]["offset"] = "inside"
        _, run = self.plan(document, 3)
        self.assertIn("is an open path with no inside; offset it left or right", value_error(self, run))


if __name__ == "__main__":
    unittest.main()
