"""Adaptive clearing (adaptive_2d) on the plate and the island pocket.

The core assertion: walked along the planned toolpath every 0.2 mm, the tool's radial engagement never exceeds the cap,
measured on an engagement model of its own (the area the tool's circle sweeps that still stood, per mm of travel, on a
finer grid than the planner's). Then as for every 2.5D operation: the posted program read back is the toolpath; the
tool never enters the part; the area it is aimed at is cleared to the stock left; no rapid moves sideways under the
retract plane; the same job plans the same moves.
"""

from __future__ import annotations

import copy
import math
import tempfile
import time
import unittest
from pathlib import Path

from tests.python.support.cam_fixtures import (assert_backplot, island_pocket_step, path_points, plate_step, swept_at,
                                               value_error)

#: The plate's pocket and outline (test_cam_features pins these), and the island pocket's pocket.
POCKET, OUTLINE, ISLAND_POCKET = "#o1.f19", "#o1.f2", "#o1.f15"

#: Stock 2 mm round the part, 1 mm above and below; the WCS on the stock top centre, so the part's top is z -1.
BASE = {
    "machine": {"kind": "mill", "post": "linuxcnc"},
    "material": "aluminum-6061",
    "stock": {"type": "box", "margin_mm": [2, 2, 1, 1]},
    "setups": [{"id": "top"}],
    "tools": [{"id": "T1", "type": "flat_endmill", "diameter_mm": 6, "flutes": 3, "flute_length_mm": 20}],
}
PLATE_JOB = {**BASE, "operations": [
    {"op": "adaptive_2d", "tool": "T1", "features": [POCKET], "max_engagement": 0.15},
    {"op": "adaptive_2d", "tool": "T1", "features": [OUTLINE], "side": "outside", "max_engagement": 0.15,
     "stepdown_mm": 6, "stock_to_leave_mm": 0.2},
]}
ISLAND_JOB = {**BASE, "operations": [
    {"op": "adaptive_2d", "tool": "T1", "features": [ISLAND_POCKET], "max_engagement": 0.2, "coolant": "flood"},
]}
#: The model's cell, mm (the planner's is an eightieth of the diameter), and the slack on the cap, as a fraction of the
#: diameter: the two grids' rounding.
CELL_MM, SLACK = 0.03, 0.01


def _plan_all(case_cls, step, document):
    from cadgen._internal.cam.job import parse_job
    from cadgen._internal.cam.operations import get_operation
    from cadgen._internal.cam.operations.context import PlanContext

    job = parse_job(document)
    shared: dict = {}
    out = []
    for index in range(len(job.operations)):
        ctx = PlanContext.from_step(step, job, index, shared=shared)
        began = time.perf_counter()
        planned = get_operation("adaptive_2d").plan(ctx.params, ctx)
        out.append((ctx, planned, time.perf_counter() - began))
    return job, out


def _reach(ctx, ref: str, side: str, z: float, *, corners: bool = True):
    """The area the operation should clear at height ``z``: what the tool can reach of the stock there, its walls
    stock_to_leave_mm in from the part; with ``corners`` False, what it may not quite reach: the inside corners its
    centre turns no tighter than a quarter of the diameter round."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    from cadgen._internal.cam.operations import geometry2d as g2

    r, stl, tol = ctx.radius, ctx.operation.stock_to_leave_mm, ctx.tol
    found = next(f for f in ctx.features if f.feature.ref == ref)
    if side == "inside":
        centre = g2.offset(g2.feature_region(ctx, found, z), -(r + stl), tol)
    else:
        keep = unary_union([Polygon(p.exterior) for p in g2.polygons(g2.shadow(ctx, z))]
                           + [g2.feature_region(ctx, found, z)])
        centre = g2.offset(g2.stock_outline(ctx, z), r + 1.0, tol).difference(g2.offset(keep, r + stl, tol))
    if not corners:
        from cadgen._internal.cam.operations.adaptive import round_corners

        centre = round_corners(centre, r / 2, tol)
    return g2.offset(centre, r, tol).intersection(g2.stock_outline(ctx, z))


def max_engagement(ctx, planned) -> float:
    """The largest radial engagement (a fraction of the diameter) along the toolpath, every 0.2 mm, each level's
    material being the area the operation clears there.
    Entries (moves down through a level) cut what they pass through without being measured: a ramp's or a helix's
    load is held by its angle."""
    from cadgen._internal.cam.operations.adaptive import CutMap
    from cadgen._internal.cam.operations import geometry2d as g2

    r = ctx.radius
    levels = sorted({z for target in planned.stats["targets"] for z in target["levels"]}, reverse=True)
    maps = {}
    for target in planned.stats["targets"]:
        for z in target["levels"]:
            area = _reach(ctx, target["ref"], target["side"], z)
            maps[z] = CutMap(area.bounds, r, CELL_MM)
            maps[z].fill(area)
    tops = [ctx.top_z] + levels

    def level_of(height: float):
        for top, z in zip(tops, levels):
            if z - 1e-6 <= height < top - 1e-6:
                return z
        return None

    points = path_points(planned.toolpath, step=0.2)
    worst, pooled_area, pooled_length = 0.0, 0.0, 0.0
    for a, b in zip(points, points[1:]):
        if abs(a[2] - b[2]) < 1e-9 and any(abs(a[2] - z) < 1e-6 for z in levels):
            z = next(z for z in levels if abs(a[2] - z) < 1e-6)
            width, _ = maps[z].sweep(a, b)
            length = math.dist(a[:2], b[:2])
            pooled_area += width * length
            pooled_length += length
            if pooled_length >= 0.1:   # a sliver at the end of a tiny move is not a wide cut
                worst = max(worst, pooled_area / pooled_length)
                pooled_area = pooled_length = 0.0
            continue
        z = level_of(min(a[2], b[2]))
        if z is not None:
            maps[z].sweep(a, b)
    return worst / (2 * r)


class Adaptive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.plate = plate_step(Path(tmp.name))
        cls.island = island_pocket_step(Path(tmp.name))
        cls.plate_job, plate = _plan_all(cls, cls.plate, PLATE_JOB)
        cls.island_job, island = _plan_all(cls, cls.island, ISLAND_JOB)
        cls.runs = [(cls.plate_job, *run) for run in plate] + [(cls.island_job, *run) for run in island]

    def test_the_engagement_never_exceeds_the_cap(self):
        for _, ctx, planned, _ in self.runs:
            cap = ctx.params.max_engagement
            measured = max_engagement(ctx, planned)
            self.assertLessEqual(measured, cap + SLACK, ctx.operation.features)
            self.assertGreater(measured, 0.6 * cap)                       # and it does load the tool
            self.assertLessEqual(planned.stats["engagement_measured"], cap)

    def test_the_area_is_cleared_to_the_stock_left_and_the_floor(self):
        for _, ctx, planned, _ in self.runs:
            points = path_points(planned.toolpath, step=0.2)
            for target in planned.stats["targets"]:
                floor = target["floor_z"]
                bottom = next(f for f in ctx.features if f.feature.ref == target["ref"]).bottom[2]
                self.assertAlmostEqual(floor, bottom + ctx.operation.stock_to_leave_mm, delta=1e-6)
                self.assertAlmostEqual(min(p[2] for p in points), floor, delta=1e-6)
                for z in target["levels"]:
                    swept = swept_at(points, z, ctx.radius).buffer(ctx.tol)
                    left = _reach(ctx, target["ref"], target["side"], z, corners=False).difference(swept)
                    self.assertLess(left.area, 1e-3, f"{target['ref']} z {z}")

    def test_inside_corners_tighter_than_the_tool_turns_are_left_and_said(self):
        from cadgen._internal.cam.operations import get_operation

        _, ctx, planned, _ = self.runs[0]
        # The plate's R3 corners: the tool's centre turns them at R1.5 (a quarter of its diameter), so R4.5 is left.
        self.assertEqual(planned.stats["corner_left"],
                         [{"ref": POCKET, "corner_radius_mm": 3.0, "tool_leaves_radius_mm": 4.5}])
        swept = swept_at(path_points(planned.toolpath, step=0.2), -6.0, ctx.radius)
        left = _reach(ctx, POCKET, "inside", -6.0).difference(swept)
        self.assertAlmostEqual(left.area, 4 * (4.5 ** 2 - 3 ** 2) * (1 - math.pi / 4), delta=0.05)
        self.assertEqual(len([piece for piece in left.geoms if piece.area > 0.01]), 4)
        self.assertIn("#o1.f19: R3 inside corners left at R4.5 for a finishing pass",
                      get_operation("adaptive_2d").human_lines(planned.stats))

    def test_the_tool_never_enters_the_part(self):
        from cadgen._internal.cam.operations.geometry2d import intrusion

        for _, ctx, planned, _ in self.runs:
            self.assertLessEqual(intrusion(ctx, path_points(planned.toolpath)), ctx.tol)

    def test_the_posted_program_read_back_is_the_toolpath(self):
        from cadgen._internal.cam.posts import get_post

        for job, ctx, planned, _ in self.runs:
            for post in ("linuxcnc", "fanuc"):
                text = get_post(post).program(job, job.setups[0], [planned.toolpath], part="plate")
                assert_backplot(self, text, post, [planned.toolpath])

    def test_no_rapid_moves_sideways_under_the_retract_plane(self):
        from cadgen._internal.cam.toolpath import rapids_below

        for _, ctx, planned, _ in self.runs:
            tp = planned.toolpath
            self.assertEqual(rapids_below(tp.moves, start=tp.start, z=ctx.heights.retract), [])
            self.assertEqual((tp.tool, tp.rpm, tp.coolant), (ctx.tool.id, ctx.feeds.rpm, ctx.operation.coolant))
            self.assertEqual({move.op for move in tp.moves}, {ctx.index})

    def test_the_feed_is_raised_for_the_thinner_chip(self):
        from cadgen._internal.cam.feeds import chip_thinning, feeds_for_operation

        job, ctx, planned, _ = self.runs[0]
        cap = ctx.params.max_engagement
        thinned = feeds_for_operation(ctx.operation, ctx.tool, job.material, job.machine, radial_engagement=cap)
        self.assertEqual(planned.stats["radial_engagement"], cap)
        self.assertAlmostEqual(planned.stats["feed_mm_min"], thinned.feed_mm_min, places=4)
        self.assertAlmostEqual(thinned.feed_mm_min / ctx.feeds.feed_mm_min, chip_thinning(cap), places=2)
        cuts = {move.feed for move in planned.toolpath.moves if move.kind in ("feed", "arc_cw", "arc_ccw")}
        self.assertEqual(len(cuts), 1)
        self.assertAlmostEqual(cuts.pop(), thinned.feed_mm_min, places=4)
        self.assertIn("feed 3,566 mm/min with chip thinning x1.40",
                      "\n".join(__import__("cadgen._internal.cam.operations", fromlist=["get_operation"])
                                .get_operation("adaptive_2d").human_lines(planned.stats)))

    def test_levels_are_full_flute_depth_unless_stated(self):
        _, pocket, planned, _ = self.runs[0]
        self.assertEqual(planned.stats["targets"][0]["levels"], [-6.0])           # 6 mm deep: one level
        _, outside, planned, _ = self.runs[1]
        self.assertEqual(planned.stats["targets"][0]["levels"], [-5.4, -10.8])    # 10.8 mm in 6 mm steps

    def test_the_plate_pocket_plans_quickly_and_the_same_twice(self):
        from cadgen._internal.cam.operations import get_operation
        from cadgen._internal.cam.operations.context import PlanContext

        _, ctx, planned, seconds = self.runs[0]
        self.assertLess(seconds, 20.0)
        again = PlanContext.from_step(self.plate, PLATE_JOB, 0, shared={})
        self.assertEqual(get_operation("adaptive_2d").plan(again.params, again), planned)

    # --- the job's keys and what cannot be planned ------------------------------------------------------------

    def test_its_keys_are_read_and_checked(self):
        from cadgen._internal.cam.job import parse_job

        def bad(**keys):
            document = copy.deepcopy(PLATE_JOB)
            document["operations"][0].update(keys)
            return value_error(self, parse_job, document)

        self.assertIn("operations[0].max_engagement: a fraction of the tool's diameter, over 0 and up to 1, got 1.5",
                      bad(max_engagement=1.5))
        self.assertIn('operations[0].side: "on" is not one of inside or outside', bad(side="on"))
        self.assertIn("unknown keys ['stepover']", bad(stepover=0.4))

    def test_what_cannot_be_planned_says_why(self):
        from cadgen._internal.cam.operations import get_operation
        from cadgen._internal.cam.operations.context import PlanContext

        def refused(**keys):
            document = copy.deepcopy(PLATE_JOB)
            document["operations"][0].update(keys)
            ctx = PlanContext.from_step(self.plate, document, 0, shared={})
            with self.assertRaises(ValueError) as caught:
                get_operation("adaptive_2d").plan(ctx.params, ctx)
            return caught.exception

        deep = refused(depth_mm=7)
        self.assertEqual(deep.kind, "too_deep")
        self.assertIn("cutting to z -7 goes 1 mm under the floor of #o1.f19 at z -6, into the part", str(deep))
        self.assertIn("operations[0].side: #o1.f19 is a pocket, cleared from the inside", str(refused(side="outside")))


if __name__ == "__main__":
    unittest.main()
