"""Adaptive clearing (adaptive_2d): the plate's pocket, the stock round its outline, a pocket with an island, a slot
1.3 tool diameters wide, a slot too narrow for a helix, and a whole ``cadgen cam plan`` run.

The core assertion: walked along the planned toolpath every 0.2 mm, the tool's radial engagement never exceeds the cap,
measured on an engagement model of its own (the area the tool's circle sweeps that still stood, per mm of travel, on a
grid finer than the planner's). Then as for every 2.5D operation: the toolpath is sound (no gouge, no steep descent into
material, no sideways rapid under the retract plane); the posted program read back is the toolpath; the area aimed at
is cleared to the stock left; the same job plans the same moves.
"""

from __future__ import annotations

import copy
import json
import math
import tempfile
import unittest
from pathlib import Path

from tests.python.support.cam_fixtures import (assert_backplot, assert_sound, path_points, plate_step, swept_at,
                                               value_error)

#: The plate's pocket and outline (test_cam_features pins these).
POCKET, OUTLINE = "#o1.f19", "#o1.f2"

#: Stock 2 mm round the part, 1 mm above and below; the WCS on the stock top centre, so the part's top is z -1.
BASE = {
    "machine": {"kind": "mill", "post": "linuxcnc"},
    "material": "aluminum-6061",
    "stock": {"type": "box", "margin_mm": [2, 2, 1, 1]},
    "setups": [{"id": "top", "fixture": {"type": "plate"}}],
    "tools": [{"id": "T1", "type": "flat_endmill", "diameter_mm": 6, "flutes": 3, "flute_length_mm": 20}],
}
#: The model's cell, mm (the planner's is an eightieth of the diameter, 0.075 mm here), and the slack on the cap, as a
#: fraction of the diameter: the two grids' rounding.
CELL_MM, SLACK = 0.03, 0.01


def job(*operations) -> dict:
    return {**copy.deepcopy(BASE), "operations": [{"op": "adaptive_2d", "tool": "T1", **op} for op in operations]}


def pocket_of(step) -> str:
    """The selector of the one pocket a test part has."""
    from cadgen._internal.cam.features import recognize
    from cadgen._internal.cam.part import open_part

    return next(f.ref for f in recognize(open_part(Path(step))) if f.kind == "pocket")


def island_step(directory) -> Path:
    """A 40 x 34 x 8 mm block standing on z = 0, centred: a 26 x 22 x 4 mm pocket with R4 corners from the top and a
    6 x 6 mm island standing in its middle up to the top face (passages 10 and 8 mm wide round it)."""
    from build123d import Align, Axis, Box, Pos, export_step, fillet

    base = (Align.CENTER, Align.CENTER, Align.MIN)
    block = Box(40, 34, 8, align=base)
    cutter = Pos(0, 0, 4) * Box(26, 22, 5, align=base)
    block -= fillet(cutter.edges().filter_by(Axis.Z), 4)
    block += Pos(0, 0, 4) * Box(6, 6, 4, align=base)
    path = Path(directory) / "island.step"
    export_step(block, str(path))
    return path


def slot_step(directory, width: float, name: str, length: float = 30.0) -> Path:
    """A 44 x 20 x 8 mm block standing on z = 0, centred: a slot ``width`` mm wide and ``length`` mm long overall
    (round ends), 3 mm deep from the top, along x."""
    from build123d import Align, Box, Cylinder, Pos, export_step

    base = (Align.CENTER, Align.CENTER, Align.MIN)
    block = Box(44, 20, 8, align=base)
    straight = length - width
    block -= Pos(0, 0, 5) * Box(straight, width, 4, align=base)
    for x in (-straight / 2, straight / 2):
        block -= Pos(x, 0, 5) * Cylinder(width / 2, 4, align=base)
    path = Path(directory) / f"{name}.step"
    export_step(block, str(path))
    return path


def plan_one(step, document, index: int = 0):
    from cadgen._internal.cam.job import parse_job
    from cadgen._internal.cam.operations import get_operation
    from cadgen._internal.cam.operations.context import PlanContext

    parsed = parse_job(document)
    ctx = PlanContext.from_step(step, parsed, index, shared={})
    return parsed, ctx, get_operation("adaptive_2d").plan(ctx.params, ctx)


def reach(ctx, ref: str, side: str, z: float, *, corners: bool = True):
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
    material being the area the operation clears there. Entries (moves down through a level) cut what they pass
    through without being measured: a ramp's or a helix's load is held by its angle."""
    from cadgen._internal.cam.operations.adaptive import CutMap

    r = ctx.radius
    levels = sorted({z for target in planned.stats["targets"] for z in target["levels"]}, reverse=True)
    maps = {}
    for target in planned.stats["targets"]:
        for z in target["levels"]:
            area = reach(ctx, target["ref"], target["side"], z)
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


class Case:
    """One planned operation, planned once for every assertion of its class; ``step(directory)`` writes its part."""

    operation: dict

    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.step = cls.make(Path(tmp.name))
        cls.document = job(cls.operation_for(cls.step))
        cls.job, cls.ctx, cls.planned = plan_one(cls.step, cls.document)

    @classmethod
    def operation_for(cls, step) -> dict:
        return cls.operation

    def test_the_engagement_never_exceeds_the_cap(self):
        cap = self.ctx.params.max_engagement
        measured = max_engagement(self.ctx, self.planned)
        self.assertLessEqual(measured, cap + SLACK)
        self.assertGreater(measured, 0.6 * cap)                       # and it does load the tool
        self.assertLessEqual(self.planned.stats["engagement_measured"], cap)

    def test_the_toolpath_is_sound(self):
        assert_sound(self, self.ctx, self.planned)

    def test_the_area_is_cleared_to_the_stock_left_and_the_floor(self):
        ctx, planned = self.ctx, self.planned
        points = path_points(planned.toolpath, step=0.1)   # fine enough to follow an arc round a slot's end
        for target in planned.stats["targets"]:
            bottom = next(f for f in ctx.features if f.feature.ref == target["ref"]).bottom[2]
            self.assertAlmostEqual(target["floor_z"], bottom + ctx.operation.stock_to_leave_mm, delta=1e-6)
            self.assertAlmostEqual(min(p[2] for p in points), target["floor_z"], delta=1e-6)
            for z in target["levels"]:
                swept = swept_at(points, z, ctx.radius).buffer(ctx.tol)
                left = reach(ctx, target["ref"], target["side"], z, corners=False).difference(swept)
                self.assertLess(left.area, 1e-3, f"{target['ref']} z {z}")


class PlatePocket(Case, unittest.TestCase):
    make = staticmethod(plate_step)
    operation = {"features": [POCKET], "max_engagement": 0.15}

    def test_the_posted_program_read_back_is_the_toolpath(self):
        from cadgen._internal.cam.posts import get_post

        for post in ("linuxcnc", "fanuc"):
            text = get_post(post).program(self.job, self.job.setups[0], [self.planned.toolpath], part="plate")
            assert_backplot(self, text, post, [self.planned.toolpath])

    def test_inside_corners_tighter_than_the_tool_turns_are_left_and_said(self):
        from cadgen._internal.cam.operations import get_operation

        ctx, planned = self.ctx, self.planned
        # The plate's R3 corners: the tool's centre turns them at R1.5 (a quarter of its diameter), so R4.5 is left.
        self.assertEqual(planned.stats["corner_left"],
                         [{"ref": POCKET, "corner_radius_mm": 3.0, "tool_leaves_radius_mm": 4.5}])
        swept = swept_at(path_points(planned.toolpath, step=0.2), -6.0, ctx.radius)
        left = reach(ctx, POCKET, "inside", -6.0).difference(swept)
        self.assertAlmostEqual(left.area, 4 * (4.5 ** 2 - 3 ** 2) * (1 - math.pi / 4), delta=0.05)
        self.assertEqual(len([piece for piece in left.geoms if piece.area > 0.01]), 4)
        self.assertIn("#o1.f19: R3 inside corners left at R4.5 for a finishing pass",
                      get_operation("adaptive_2d").human_lines(self.planned.stats))

    def test_the_feed_is_raised_for_the_thinner_chip(self):
        from cadgen._internal.cam.feeds import chip_thinning, feeds_for_operation
        from cadgen._internal.cam.operations import get_operation

        ctx, planned = self.ctx, self.planned
        thinned = feeds_for_operation(ctx.operation, ctx.tool, self.job.material, self.job.machine,
                                      radial_engagement=0.15)
        self.assertEqual(planned.stats["radial_engagement"], 0.15)
        self.assertAlmostEqual(planned.stats["feed_mm_min"], thinned.feed_mm_min, places=4)
        self.assertAlmostEqual(thinned.feed_mm_min / ctx.feeds.feed_mm_min, chip_thinning(0.15), places=2)
        cuts = {move.feed for move in planned.toolpath.moves if move.kind in ("feed", "arc_cw", "arc_ccw")}
        self.assertEqual(len(cuts), 1)
        self.assertAlmostEqual(cuts.pop(), thinned.feed_mm_min, places=4)
        self.assertIn("feed 3,566 mm/min with chip thinning x1.40",
                      "\n".join(get_operation("adaptive_2d").human_lines(planned.stats)))
        self.assertEqual(planned.stats["targets"][0]["levels"], [-6.0])   # 6 mm deep: one level of the flutes

    # --- the job's keys and what cannot be planned ------------------------------------------------------------

    def test_its_keys_are_read_and_checked(self):
        from cadgen._internal.cam.job import parse_job

        def bad(**keys):
            document = copy.deepcopy(self.document)
            document["operations"][0].update(keys)
            return value_error(self, parse_job, document)

        self.assertIn("operations[0].max_engagement: a fraction of the tool's diameter, over 0 and up to 1, got 1.5",
                      bad(max_engagement=1.5))
        self.assertIn('operations[0].side: "on" is not one of inside or outside', bad(side="on"))
        self.assertIn("unknown keys ['stepover']", bad(stepover=0.4))

    def test_what_cannot_be_planned_says_why(self):
        from cadgen._internal.cam.operations import get_operation
        from cadgen._internal.cam.operations.base import PlanError
        from cadgen._internal.cam.operations.context import PlanContext

        def refused(**keys):
            document = copy.deepcopy(self.document)
            document["operations"][0].update(keys)
            ctx = PlanContext.from_step(self.step, document, 0, shared={})
            with self.assertRaises(ValueError) as caught:
                get_operation("adaptive_2d").plan(ctx.params, ctx)
            return caught.exception

        deep = refused(depth_mm=7)
        self.assertIsInstance(deep, PlanError)
        self.assertEqual(deep.kind, "too_deep")
        self.assertIn("cutting to z -7 goes 1 mm under the floor of #o1.f19 at z -6, into the part", str(deep))
        self.assertIn("operations[0].side: #o1.f19 is a pocket, cleared from the inside", str(refused(side="outside")))


class PlateOutside(Case, unittest.TestCase):
    """The stock round the plate's outline, 0.2 mm left on its walls and under it: entered from the air beside it."""

    make = staticmethod(plate_step)
    operation = {"features": [OUTLINE], "side": "outside", "max_engagement": 0.2, "stock_to_leave_mm": 0.2}

    def test_it_is_entered_from_the_air(self):
        from cadgen._internal.cam.operations.emit import in_air

        first = next(move for move in self.planned.toolpath.moves if move.kind == "plunge")
        self.assertTrue(in_air(self.ctx, first.xyz))
        self.assertFalse(any(move.kind in ("arc_cw", "arc_ccw") and move.xyz[2] != -10.8
                             for move in self.planned.toolpath.moves))   # no helix


class IslandPocket(Case, unittest.TestCase):
    """A pocket with an island: cleared round it, the island left standing."""

    make = staticmethod(island_step)

    @classmethod
    def operation_for(cls, step) -> dict:
        return {"features": [pocket_of(step)], "max_engagement": 0.3}


class NarrowSlot(Case, unittest.TestCase):
    """A slot 1.3 tool diameters wide: no room to go straight on at the cap, so the tool loops (trochoidal) and the cap
    still holds; two levels, as stepdown_mm asks."""

    make = staticmethod(lambda directory: slot_step(directory, 7.8, "slot", length=15.0))

    @classmethod
    def operation_for(cls, step) -> dict:
        return {"features": [pocket_of(step)], "max_engagement": 0.15, "stepdown_mm": 2}

    def test_it_steps_down_as_asked(self):
        self.assertEqual(self.planned.stats["targets"][0]["levels"], [-2.0, -4.0])

    def test_the_same_job_plans_the_same_moves(self):
        self.assertEqual(plan_one(self.step, self.document)[2], self.planned)


class TightSlot(Case, unittest.TestCase):
    """A slot barely wider than the tool: no helix fits, so each level is entered down a ramp along it; at full
    engagement allowed (a slot is cut at full width), the ramp is the only way down."""

    make = staticmethod(lambda directory: slot_step(directory, 6.4, "tight"))

    @classmethod
    def operation_for(cls, step) -> dict:
        return {"features": [pocket_of(step)], "max_engagement": 1.0}

    def test_the_engagement_never_exceeds_the_cap(self):
        # A full-width cap: nothing to bound but the planner's own reading (no cut is wider than the tool; the model
        # reads a U-turn at a slot's end a little wider, as it sweeps more than its travel).
        self.assertLessEqual(self.planned.stats["engagement_measured"], 1.0)

    def test_each_level_is_entered_down_a_ramp(self):
        moves = self.planned.toolpath.moves
        start = self.planned.toolpath.start
        from cadgen._internal.cam.toolpath import walk

        helix = [m for here, m in walk(moves, start) if m.kind in ("arc_cw", "arc_ccw") and m.xyz[2] < here[2] - 1e-9]
        self.assertEqual(helix, [])                                                 # no helix: none fits
        down = [m for m in moves if m.kind == "feed" and m.xyz[2] > -4.0 + 1e-6]
        self.assertGreater(len(down), 2)                                        # zig-zags, not one plunge
        self.assertTrue(all(abs(m.xyz[1]) < 0.2 + 1e-6 for m in down))         # along the slot
        self.assertFalse([m for m in moves if m.kind == "plunge" and m.xyz[2] < -1.0])


class EndToEnd(unittest.TestCase):
    def test_cam_plan_runs_a_job_with_adaptive_clearing(self):
        from cadgen.cam import plan

        with tempfile.TemporaryDirectory() as tmp:
            step = plate_step(Path(tmp))
            job_file = Path(tmp) / "job.json"
            job_file.write_text(json.dumps(job({"features": [POCKET], "max_engagement": 0.3})), encoding="utf-8")
            result = plan(step, job=str(job_file))
            self.assertTrue(result.ok, result.findings)
            self.assertEqual([op["op"] for op in result.operations], ["adaptive_2d"])
            self.assertIn("G2", result.nc[0].read_text(encoding="utf-8").replace("G02", "G2"))


if __name__ == "__main__":
    unittest.main()
