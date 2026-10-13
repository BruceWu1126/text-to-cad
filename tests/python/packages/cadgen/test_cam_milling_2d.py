"""2.5D milling on the plate: facing, contour_2d and pocket_2d.

For every operation: the posted program, read back, is the planned toolpath (linuxcnc and fanuc); the swept tool never
enters the part beyond the tolerance; the walls and floor it is aimed at are reached within stock-to-leave plus the
tolerance; no rapid moves sideways under the retract plane; the same job plans the same moves twice.
"""

from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from tests.python.support.cam_fixtures import PLATE, assert_backplot, path_points, plate_step, swept_at, value_error

#: The plate's faces as `cadgen cam features` names them (test_cam_features pins these).
POCKET, OUTLINE = "#o1.f19", "#o1.f2"

#: Stock 2 mm round the plate, 1 mm above and below; the WCS on the stock top centre, so the part's top is z -1, the
#: pocket floor z -6 and the part's bottom z -11.
JOB = {
    "machine": {"kind": "mill", "post": "linuxcnc"},
    "material": "aluminum-6061",
    "stock": {"type": "box", "margin_mm": [2, 2, 1, 1]},
    "setups": [{"id": "top"}],
    "tools": [{"id": "T1", "type": "flat_endmill", "diameter_mm": 6, "flutes": 3, "flute_length_mm": 20},
              {"id": "T2", "type": "flat_endmill", "diameter_mm": 8, "flutes": 3, "flute_length_mm": 20}],
    "operations": [
        {"op": "facing", "tool": "T1"},
        {"op": "contour_2d", "tool": "T1", "all": True, "tabs": {"count": 4, "width_mm": 5, "height_mm": 2}},
        {"op": "pocket_2d", "tool": "T1", "features": [POCKET]},
        {"op": "pocket_2d", "tool": "T2", "features": [POCKET]},
        {"op": "pocket_2d", "tool": "T1", "features": [POCKET], "pattern": "zigzag", "finish_mm": 0.3,
         "finish_floor_mm": 0.2, "stock_to_leave_mm": 0.1},
        {"op": "contour_2d", "tool": "T1", "features": [OUTLINE], "finish_passes": 2, "finish_mm": 0.4,
         "stock_to_leave_mm": 0.1, "coolant": "flood"},
    ],
}
TOP, POCKET_FLOOR, BOTTOM = -1.0, -6.0, -11.0


class Plate(unittest.TestCase):
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

    # --- every operation ---------------------------------------------------------------------------------

    def test_the_posted_program_read_back_is_the_toolpath(self):
        from cadgen._internal.cam.posts import get_post

        for ctx, planned in self.each():
            for post in ("linuxcnc", "fanuc"):
                text = get_post(post).program(self.job, self.job.setups[0], [planned.toolpath], part="plate")
                assert_backplot(self, text, post, [planned.toolpath])

    def test_the_tool_never_enters_the_part(self):
        from cadgen._internal.cam.operations.geometry2d import intrusion

        for ctx, planned in self.each():
            self.assertLessEqual(intrusion(ctx, path_points(planned.toolpath)), ctx.tol)

    def test_no_rapid_moves_sideways_under_the_retract_plane(self):
        from cadgen._internal.cam.toolpath import rapids_below

        for ctx, planned in self.each():
            tp = planned.toolpath
            self.assertEqual(rapids_below(tp.moves, start=tp.start, z=ctx.heights.retract), [])
            self.assertEqual((tp.tool, tp.rpm, tp.coolant), (ctx.tool.id, ctx.feeds.rpm, ctx.operation.coolant))
            self.assertEqual({move.op for move in tp.moves}, {ctx.index})

    def test_the_same_job_plans_the_same_moves(self):
        from cadgen._internal.cam.operations import get_operation
        from cadgen._internal.cam.operations.context import PlanContext

        fresh: dict = {}
        for ctx, planned in self.each():
            again = PlanContext.from_step(self.step, JOB, ctx.index, shared=fresh)
            self.assertEqual(get_operation(again.operation.op).plan(again.params, again), planned)

    # --- what each one reaches ---------------------------------------------------------------------------

    def floor_and_cover(self, index: int, floor: float):
        ctx, planned = self.contexts[index], self.planned[index]
        points = path_points(planned.toolpath, step=0.2)
        self.assertAlmostEqual(min(p[2] for p in points), floor, delta=1e-6)
        return ctx, planned, swept_at(points, floor, ctx.radius)

    def test_facing_takes_the_1mm_margin_off_the_whole_top(self):
        from cadgen._internal.cam.operations.geometry2d import stock_outline

        ctx, planned, swept = self.floor_and_cover(0, TOP)
        self.assertLess(stock_outline(ctx).difference(swept.buffer(ctx.tol)).area, 1e-6)
        self.assertEqual(planned.stats["levels"], [TOP])
        self.assertEqual(self.contexts[1].top_z, TOP)  # the faced top is where the later operations start

    def test_contour_cuts_round_the_outline_to_the_bottom_leaving_four_tabs(self):
        from shapely.geometry import LineString

        from cadgen._internal.cam.operations.geometry2d import shadow

        ctx, planned, swept = self.floor_and_cover(1, BOTTOM)
        points = path_points(planned.toolpath, step=0.2)
        outline = shadow(ctx, -5.0).geoms[0].exterior
        for z in planned.stats["targets"][0]["depths"][:-1]:     # every level above the tabs goes right round
            ring = LineString([p[:2] for p in points if abs(p[2] - z) < 1e-6])
            far = max(ring.distance(outline.interpolate(s)) for s in range(0, int(outline.length), 2))
            self.assertLessEqual(far - ctx.radius, ctx.tol)
        # The only cut along the tabs' top is over them (the retract only passes through that height).
        lifts = [piece for piece in swept_at(points, BOTTOM + 2, 1e-3).geoms if piece.area > 1e-3]
        self.assertEqual(len(lifts), 4)
        for lift in lifts:                                       # each leaves a 5 mm tab between the tool's sides
            x0, y0, x1, y1 = lift.bounds
            self.assertAlmostEqual(max(x1 - x0, y1 - y0) - 2e-3 - 2 * ctx.radius, 5.0, delta=1e-3)

    def test_contour_finishing_passes_leave_the_stock_asked_for(self):
        from shapely.geometry import LineString

        from cadgen._internal.cam.operations.geometry2d import shadow

        ctx, planned, swept = self.floor_and_cover(5, BOTTOM)
        outline = shadow(ctx, -5.0).geoms[0].exterior
        stl = ctx.operation.stock_to_leave_mm
        rings = []
        for p in path_points(planned.toolpath, step=0.2):
            if abs(p[2] - BOTTOM) < 1e-6:
                rings.append(p[:2])
        nearest = min(LineString(rings).distance(outline.interpolate(s)) for s in range(0, int(outline.length), 5))
        self.assertAlmostEqual(nearest - ctx.radius, stl, delta=ctx.tol)
        self.assertEqual(planned.toolpath.coolant, "flood")

    def test_a_pocket_cut_with_its_corner_radius_is_cleared_to_the_corners(self):
        from cadgen._internal.cam.operations.geometry2d import feature_region

        ctx, planned, swept = self.floor_and_cover(2, POCKET_FLOOR)
        region = feature_region(ctx, ctx.features[[f.feature.ref for f in ctx.features].index(POCKET)], POCKET_FLOOR)
        self.assertAlmostEqual(region.area, 40 * 20 - 4 * 9 * (1 - math.pi / 4), delta=0.05)
        self.assertLess(region.difference(swept.buffer(ctx.tol)).area, 1e-4)
        self.assertEqual(planned.stats["corner_left"], [])

    def test_a_tool_wider_than_the_corners_leaves_them_and_says_so(self):
        from cadgen._internal.cam.operations.geometry2d import feature_region

        ctx, planned, swept = self.floor_and_cover(3, POCKET_FLOOR)
        self.assertEqual(planned.stats["corner_left"], [
            {"ref": POCKET, "corner_radius_mm": PLATE["pocket"]["corner_radius_mm"], "tool_leaves_radius_mm": 4.0}])
        region = feature_region(ctx, ctx.features[[f.feature.ref for f in ctx.features].index(POCKET)], POCKET_FLOOR)
        left = region.difference(swept)
        self.assertAlmostEqual(left.area, 4 * (16 - 9) * (1 - math.pi / 4), delta=0.02)   # R4 where R3 was drawn
        self.assertEqual(len(left.geoms), 4)
        from cadgen._internal.cam.operations import get_operation

        self.assertIn("R3 inside corners left at R4 by this tool",
                      "\n".join(get_operation("pocket_2d").human_lines(planned.stats)))

    def test_a_zigzag_pocket_finishes_walls_and_floor_to_the_stock_left(self):
        from shapely.geometry import box

        from cadgen._internal.cam.operations.geometry2d import feature_region

        ctx, planned, swept = self.floor_and_cover(4, POCKET_FLOOR)
        stl = ctx.operation.stock_to_leave_mm
        self.assertEqual(planned.stats["pockets"][0]["levels"][-2:], [POCKET_FLOOR + 0.2, POCKET_FLOOR])
        region = feature_region(ctx, ctx.features[[f.feature.ref for f in ctx.features].index(POCKET)], POCKET_FLOOR)
        # Leaving 0.1 mm makes the R3 corners R2.9 for a 6 mm tool: they are left, and said to be; the rest is reached.
        self.assertEqual(planned.stats["corner_left"][0]["tool_leaves_radius_mm"], 3.1)
        x0, y0, x1, y1 = region.bounds
        sides = region.difference(box(x0, y0, x0 + 4, y1)).difference(box(x1 - 4, y0, x1, y1))
        sides = sides.union(region.difference(box(x0, y0, x1, y0 + 4)).difference(box(x0, y1 - 4, x1, y1)))
        self.assertLess(sides.difference(swept.buffer(stl + ctx.tol)).area, 1e-4)
        self.assertGreater(region.difference(swept.buffer(stl - ctx.tol)).area, 0.5)   # and no closer than that

    # --- the job's keys ----------------------------------------------------------------------------------

    def test_each_operation_reads_its_own_keys(self):
        import copy

        from cadgen._internal.cam.job import parse_job

        def bad(index, **keys):
            document = copy.deepcopy(JOB)
            document["operations"][index].update(keys)
            return value_error(self, parse_job, document)

        self.assertIn("operations[0].stepover: a fraction of the tool's diameter, over 0 and up to 1, got 1.5",
                      bad(0, stepover=1.5))
        self.assertIn('operations[2].pattern: "spiral" is not one of offset or zigzag', bad(2, pattern="spiral"))
        self.assertIn("operations[1].tabs.count: expected a whole number from 1 to 64, got 0",
                      bad(1, tabs={"count": 0}))
        self.assertIn("operations[5].finish_mm: 0.4 mm is left for finishing passes; give finish_passes too",
                      bad(5, finish_passes=0))
        self.assertIn("give depth_mm (below the top of the material) or z_mm (in the setup's frame), not both",
                      bad(2, depth_mm=2, z_mm=-3))
        document = copy.deepcopy(JOB)
        document["tools"].append({"id": "T3", "type": "ball_endmill", "diameter_mm": 6})
        document["operations"][2]["tool"] = "T3"
        self.assertIn("T3 is a ball_endmill, and pocket_2d cuts with a flat_endmill or bull_endmill",
                      value_error(self, parse_job, document))

    def test_a_pocket_deeper_than_its_floor_is_refused(self):
        import copy

        from cadgen._internal.cam.operations import get_operation
        from cadgen._internal.cam.operations.context import PlanContext

        document = copy.deepcopy(JOB)
        document["operations"][2]["depth_mm"] = 6
        ctx = PlanContext.from_step(self.step, document, 2, shared=self.shared)
        self.assertIn("operations[2]: cutting to z -7 goes 1 mm under the floor of #o1.f19 at z -6, into the part",
                      value_error(self, get_operation("pocket_2d").plan, ctx.params, ctx))


if __name__ == "__main__":
    unittest.main()
