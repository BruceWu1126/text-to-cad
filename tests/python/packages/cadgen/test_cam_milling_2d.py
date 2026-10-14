"""2.5D milling on the plate: facing, contour_2d and pocket_2d.

For every operation: the posted program, read back, is the planned toolpath (linuxcnc and fanuc); the swept tool never
enters the part beyond the tolerance, read both from the slices (geometry2d.intrusion) and from the B-rep by the kernel
(cam_fixtures.brep_intrusion); no descent goes into material steeper than the ramp angle (cam_fixtures.descents_ok);
the walls and floor it is aimed at are reached within stock-to-leave plus the tolerance; no rapid moves sideways under
the retract plane; the same job plans the same moves twice. Then the same checks on what the plate does not have: a
bar of stock, a side setup, a vise, a wall that bulges between its top and bottom, a facing inside a region.
"""

from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from tests.python.support.cam_fixtures import (
    PLATE, assert_backplot, assert_sound, path_points, plate_step, swept_at, value_error,
)

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


def plan_all(step, document, shared: dict) -> list:
    """``[(ctx, planned)]`` for every operation of ``document`` on ``step``."""
    from cadgen._internal.cam.job import parse_job
    from cadgen._internal.cam.operations import get_operation
    from cadgen._internal.cam.operations.context import PlanContext

    job = parse_job(document)
    out = []
    for index in range(len(job.operations)):
        ctx = PlanContext.from_step(step, job, index, shared=shared)
        out.append((ctx, get_operation(ctx.operation.op).plan(ctx.params, ctx)))
    return out


def plan_error(case: unittest.TestCase, call, *args):
    """The PlanError ``call(*args)`` must raise."""
    from cadgen._internal.cam.operations.base import PlanError

    with case.assertRaises(PlanError) as caught:
        call(*args)
    return caught.exception


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

    def test_no_gouge_no_steep_descent_into_material_no_low_rapid(self):
        for ctx, planned in self.each():
            assert_sound(self, ctx, planned, slices=True)
            tp = planned.toolpath
            self.assertEqual((tp.tool, tp.rpm, tp.coolant), (ctx.tool.id, ctx.feeds.rpm, ctx.operation.coolant))

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

    def test_what_cannot_be_planned_says_why(self):
        import copy

        from cadgen._internal.cam.operations import get_operation
        from cadgen._internal.cam.operations.context import PlanContext

        def refused(index, tools=(), **keys):
            document = copy.deepcopy(JOB)
            document["tools"] += list(tools)
            document["operations"][index].update(keys)
            ctx = PlanContext.from_step(self.step, document, index, shared=self.shared)
            return plan_error(self, get_operation(ctx.operation.op).plan, ctx.params, ctx)

        deep = refused(2, depth_mm=6)
        self.assertEqual(deep.kind, "too_deep")
        self.assertIn("operations[2]: cutting to z -7 goes 1 mm under the floor of #o1.f19 at z -6, into the part",
                      str(deep))
        self.assertEqual(refused(0, z_mm=-3).kind, "too_deep")                    # facing into the part
        self.assertEqual(refused(5, z_mm=1).kind, "empty_operation")              # a floor over the material
        wide = refused(2, [{"id": "T9", "type": "flat_endmill", "diameter_mm": 24}], tool="T9")
        self.assertEqual((wide.kind, wide.finding()["type"]), ("tool_too_large", "tool_too_large"))

    def test_a_vertical_drop_into_material_is_an_internal_error(self):
        from cadgen._internal.cam.operations.emit import Emitter

        emitter = Emitter(self.contexts[2])
        emitter.go([(0.0, 0.0, -2.0), (5.0, 0.0, -2.0)], rapid_to=TOP)     # in the pocket, cut clear by a pass
        self.assertIn("internal: vertical drop into material at (5, 0, -3)",
                      value_error(self, emitter.cut, [(5.0, 0.0, -3.0)]))


class Elsewhere(unittest.TestCase):
    """The plate, and a filleted block, on what the plate job does not have."""

    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.dir = Path(tmp.name)
        cls.step = plate_step(cls.dir)
        cls.block = bulging_block_step(cls.dir)

    def plan(self, step, document):
        planned = plan_all(step, document, {})
        for ctx, done in planned:
            with self.subTest(op=f"{ctx.index} {ctx.operation.op}"):
                assert_sound(self, ctx, done)
        return planned

    def test_a_region_facing_keeps_its_tool_inside_the_region(self):
        from shapely.geometry import box

        region = [[-15, -5], [15, -5], [15, 5], [-15, 5]]
        for pattern in ("zigzag", "one_way"):
            document = dict(JOB, operations=[{"op": "facing", "tool": "T1", "region": region, "z_mm": -4,
                                              "pattern": pattern, "stepdown_mm": 2}])
            [(ctx, planned)] = self.plan(self.step, document)
            points = path_points(planned.toolpath, step=0.2)
            low = [p for p in points if p[2] < ctx.heights.retract - 1e-6]
            reach = max(max(abs(p[0]) for p in low), max(abs(p[1]) - 10 for p in low) + 15)
            self.assertLessEqual(reach + ctx.radius, 15 + 0.2 * ctx.radius + ctx.tol)   # a tenth of the diameter over
            # All of it is faced but the corners a round tool hanging that little over cannot reach.
            left = box(-15, -5, 15, 5).difference(swept_at(points, -4.0, ctx.radius).buffer(ctx.tol))
            self.assertLess(left.area, 0.8)
            for piece in getattr(left, "geoms", [left]):
                self.assertGreater(abs(piece.centroid.x), 14)
                self.assertGreater(abs(piece.centroid.y), 4)
            # It lifts to the retract plane once per level, not once per row.
            rises = [k for k, (a, b) in enumerate(zip(points, points[1:]))
                     if a[2] < ctx.heights.retract - 1e-6 <= b[2]]
            self.assertLessEqual(len(rises), len(planned.stats["levels"]))

    def test_bar_stock_is_faced_level_by_level_and_never_plunged_into(self):
        document = dict(JOB, stock={"type": "cylinder", "axis": "X", "margin_mm": [2, 2]},
                        operations=[{"op": "facing", "tool": "T2", "stepdown_mm": 5, "stepover": 0.8},
                                    {"op": "pocket_2d", "tool": "T1", "features": [POCKET]}])
        (face, faced), (pocket, _) = self.plan(self.step, document)
        self.assertEqual(pocket.top_z, faced.stats["floor_z"])    # the facing covered the whole bar
        swept = swept_at(path_points(faced.toolpath, step=0.2), faced.stats["floor_z"], face.radius)
        from cadgen._internal.cam.operations.geometry2d import stock_outline

        self.assertLess(stock_outline(face, faced.stats["floor_z"]).difference(swept.buffer(face.tol)).area, 1e-4)

    def test_a_side_setup(self):
        document = dict(JOB, setups=[{"id": "side", "direction": "+X"}],
                        operations=[{"op": "facing", "tool": "T1", "setup": "side"},
                                    {"op": "contour_2d", "tool": "T1", "setup": "side", "all": True, "depth_mm": 4},
                                    {"op": "pocket_2d", "tool": "T1", "setup": "side", "depth_mm": 3,
                                     "region": [[-12, -20], [4, -20], [4, 20], [-12, 20]]}])
        planned = self.plan(self.step, document)
        self.assertEqual([p.stats.get("floor_z", None) for _, p in planned][:1], [-2.0])

    def test_a_vise_keeps_the_leads_out_of_its_jaws(self):
        document = dict(JOB, stock={"type": "box", "margin_mm": [2, 7, 1, 1]},
                        setups=[{"id": "top", "fixture": {"type": "vise", "grip_mm": 6}}],
                        operations=[{"op": "contour_2d", "tool": "T1", "all": True, "lead_radius_mm": 8}])
        [(ctx, planned)] = self.plan(self.step, document)
        jaws = [volume for volume in ctx.setup.fixture.volumes if "jaw" in volume.name]
        self.assertEqual({volume.max[2] for volume in jaws}, {-6.0})
        from shapely.geometry import Point, box

        for x, y, z in path_points(planned.toolpath, step=0.2):
            for jaw in jaws:
                if z < jaw.max[2]:
                    footprint = box(jaw.min[0], jaw.min[1], jaw.max[0], jaw.max[1])
                    self.assertGreaterEqual(footprint.distance(Point(x, y)), ctx.radius, (x, y, z, jaw.name))
        levels = planned.stats["targets"][0]["depths"]
        self.assertTrue(any(z < -6 for z in levels) and any(z > -6 for z in levels))
        # Gripped closer than the cut can pass, the tool would come down onto a jaw: a fixture collision.
        document["stock"] = {"type": "box", "margin_mm": [2, 2, 1, 1]}
        from cadgen._internal.cam.operations import get_operation
        from cadgen._internal.cam.operations.context import PlanContext

        ctx = PlanContext.from_step(self.step, document, 0)
        self.assertEqual(plan_error(self, get_operation("contour_2d").plan, ctx.params, ctx).kind, "fixture_collision")

    def test_sheet_defaults_and_a_finishing_pass_dropped_into_the_rough_slot(self):
        document = dict(JOB, machine={"kind": "router", "post": "linuxcnc"},
                        stock={"type": "sheet", "margin_mm": [5, 5]},
                        operations=[{"op": "contour_2d", "tool": "T1", "all": True, "lead_radius_mm": 0,
                                     "finish_passes": 1, "finish_mm": 0.3, "tabs": {"count": 4}}])
        [(ctx, planned)] = self.plan(self.step, document)
        target = planned.stats["targets"][0]
        bottom = ctx.part_bottom_z
        self.assertAlmostEqual(target["floor_z"], bottom - 0.3)                   # through sheet: 0.3 mm on
        self.assertAlmostEqual(planned.stats["tabs"]["height_mm"], 0.25 * 10.3)   # a quarter of the cut
        moves = planned.toolpath.moves
        # The finishing ring: a rapid down to 1 mm (the feed height) over the rough slot's floor, a plunge into it,
        # then across onto its own offset.
        drops = [k for k, move in enumerate(moves) if move.kind == "rapid" and abs(move.xyz[2] - (bottom + 0.7)) < 1e-9]
        self.assertEqual(len(drops), 1)
        self.assertEqual((moves[drops[0] + 1].kind, moves[drops[0] + 1].xyz[2]), ("plunge", bottom - 0.3))
        drops = [drops[0] + 1]
        step = moves[drops[0] + 1]
        self.assertEqual(step.kind, "feed")
        self.assertAlmostEqual(math.dist(step.xyz[:2], moves[drops[0]].xyz[:2]), 0.3, delta=1e-6)

    def test_a_wall_that_bulges_between_its_top_and_bottom_is_not_cut(self):
        document = dict(JOB, stock={"type": "box", "margin_mm": [5, 5, 0, 0]},
                        operations=[{"op": "pocket_2d", "tool": "T1", "z_mm": -9.5,
                                     "region": [[20, -25], [40, -25], [40, 25], [20, 25]]}])
        self.plan(self.block, document)


class FacingDefaults(unittest.TestCase):
    def test_stepover_and_stepdown_suit_the_tool(self):
        from cadgen._internal.cam.job import parse_job

        tools = [{"id": "T1", "type": "face_mill", "diameter_mm": 50},
                 {"id": "T2", "type": "flat_endmill", "diameter_mm": 10, "flute_length_mm": 22},
                 {"id": "T3", "type": "flat_endmill", "diameter_mm": 3, "flute_length_mm": 3},
                 {"id": "T4", "type": "flat_endmill", "diameter_mm": 10, "material": "hss"}]
        job = parse_job(dict(JOB, tools=tools, operations=[{"op": "facing", "tool": t["id"]} for t in tools]))
        got = [(op.params.stepover, op.params.stepdown_mm) for op in job.operations]
        # A face mill 0.7 of its diameter, an end mill 0.6; a carbide end mill half its flutes deep, at most 2 mm.
        self.assertEqual(got, [(0.7, 1.0), (0.6, 2.0), (0.6, 1.5), (0.6, 1.0)])


def bulging_block_step(directory, name: str = "bulging"):
    """A 60 x 40 x 10 mm block standing on z = 0, centred, its top and bottom edges filleted R4.99: its sides stand
    straight for only 0.02 mm at mid-height and lean in above and below."""
    from build123d import Align, Box, export_step, fillet

    block = Box(60, 40, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    block = fillet(block.faces().sort_by().first.edges() + block.faces().sort_by().last.edges(), 4.99)
    step = Path(directory) / f"{name}.step"
    export_step(block, str(step))
    return step


if __name__ == "__main__":
    unittest.main()
