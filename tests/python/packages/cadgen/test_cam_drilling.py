"""Drilling and hole cycles on the plate (and the slotted block for counterbores).

Every cycle, posted, reads back as the planned toolpath: natively on fanuc, natively or expanded on linuxcnc, expanded
on grbl (which refuses taps and oriented bores in its own words). Holes go in a nearest-neighbour tour, not file
order; a blind hole's tool never passes the hole's floor; a through hole's full diameter breaks through by
breakthrough_mm; the tool stays at the R plane between holes (G99) only when the way across is clear; every cycle is
sound (cam_fixtures.assert_sound); the same job plans the same moves twice.
"""

from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from tests.python.support.cam_fixtures import (
    assert_backplot, assert_sound, plate_step, slotted_block_step, value_error,
)

#: The plate's holes as `cadgen cam features` names them: the 5 mm blind hole at (35, -15), the 6 mm through holes
#: at (-35, 15) and (35, 15).
BLIND, LEFT, RIGHT = "#o1.f7", "#o1.f16", "#o1.f17"
#: The slotted block's 5 mm through hole under a 9 x 4 mm counterbore on its top.
CBORE = "#o1.f14"

#: Stock 1 mm over and under the plate, WCS on the stock top: the plate's top is z -1, the blind hole's floor z -7,
#: its bottom z -11.
TOP, BLIND_FLOOR, BOTTOM = -1.0, -7.0, -11.0

TOOLS = [
    {"id": "T1", "type": "drill", "diameter_mm": 6},
    {"id": "T2", "type": "drill", "diameter_mm": 5},
    {"id": "T3", "type": "spot_drill", "diameter_mm": 10},
    {"id": "T4", "type": "tap", "diameter_mm": 6, "pitch_mm": 1},
    {"id": "T5", "type": "reamer", "diameter_mm": 6},
    {"id": "T6", "type": "boring_bar", "diameter_mm": 6},
]
OPERATIONS = [
    {"op": "drill", "tool": "T1", "all": True},
    {"op": "drill", "tool": "T2", "features": [BLIND]},
    {"op": "peck", "tool": "T1", "features": [RIGHT, LEFT]},
    {"op": "chip_break", "tool": "T1", "all": True, "breakthrough_mm": 2},
    {"op": "spot_drill", "tool": "T3", "all": True},
    {"op": "tap", "tool": "T4", "all": True},
    {"op": "rigid_tap", "tool": "T4", "features": [LEFT]},
    {"op": "ream", "tool": "T5", "all": True, "dwell_s": 0.5},
    {"op": "boring_cycle", "tool": "T6", "all": True},
    {"op": "boring_cycle", "tool": "T6", "features": [RIGHT], "cycle": "fine_bore", "shift_mm": 0.2,
     "shift_xy": [1, 0]},
]
JOB = {"machine": {"kind": "mill", "post": "fanuc"}, "material": "aluminum-6061",
       "stock": {"type": "box", "margin_mm": [2, 2, 1, 1]}, "setups": [{"id": "top"}], "tools": TOOLS,
       "operations": OPERATIONS}
#: The block from the top (counterbore) and from below (back bore: the counterbore is then on the far side).
BLOCK_JOB = {"machine": {"kind": "mill", "post": "fanuc"}, "material": "aluminum-6061",
             "stock": {"type": "box", "margin_mm": [2, 2, 1, 1]},
             "setups": [{"id": "top"}, {"id": "under", "direction": "-Z"}],
             "tools": [{"id": "T7", "type": "counterbore", "diameter_mm": 9, "pilot_diameter_mm": 5},
                       {"id": "T8", "type": "back_bore", "diameter_mm": 9}],
             "operations": [{"op": "counterbore", "setup": "top", "tool": "T7", "all": True},
                            {"op": "back_bore", "setup": "under", "tool": "T8", "features": [CBORE], "shift_mm": 2.2}]}
#: Fanuc writes each kind as this canned cycle.
FANUC = {"drill": "G81", "peck": "G83", "chip_break": "G73", "spot": "G82", "tap": "G84", "rigid_tap": "G84",
         "ream": "G89", "bore": "G86", "fine_bore": "G76", "back_bore": "G87"}


def plan_all(step, job, shared):
    from cadgen._internal.cam.job import parse_job
    from cadgen._internal.cam.operations import get_operation
    from cadgen._internal.cam.operations.context import PlanContext

    parsed = parse_job(job)
    out = []
    for index in range(len(parsed.operations)):
        ctx = PlanContext.from_step(step, parsed, index, shared=shared)
        out.append((ctx, get_operation(ctx.operation.op).plan(ctx.params, ctx)))
    return parsed, out


class Drilling(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.step = plate_step(Path(tmp.name))
        cls.block = slotted_block_step(Path(tmp.name))
        cls.job, cls.planned = plan_all(cls.step, JOB, {})
        cls.block_job, cls.block_planned = plan_all(cls.block, BLOCK_JOB, {})

    def each(self):
        for job, planned in ((self.job, self.planned), (self.block_job, self.block_planned)):
            for ctx, result in planned:
                with self.subTest(op=f"{ctx.index} {ctx.operation.op}"):
                    yield job, ctx, result

    def cycles(self, index: int):
        return [move.cycle for move in self.planned[index][1].toolpath.moves if move.kind == "cycle"]

    def test_each_cycle_posts_natively_or_expanded_and_reads_back_as_planned(self):
        from cadgen._internal.cam.posts import get_post

        for job, ctx, result in self.each():
            kind = result.stats["cycle"]
            setup = job.setup(ctx.operation.setup)
            for post in ("fanuc", "linuxcnc", "grbl"):
                if post == "grbl" and kind in ("tap", "rigid_tap", "fine_bore", "back_bore"):
                    message = value_error(self, get_post(post).program, job, setup, [result.toolpath])
                    self.assertIn("tapping" if "tap" in kind else "orient its spindle (M19)", message)
                    continue
                text = get_post(post).program(job, setup, [result.toolpath], part="plate")
                assert_backplot(self, text, post, [result.toolpath])
                if post == "fanuc":
                    self.assertIn(FANUC[kind], text)
                if post == "grbl":
                    self.assertNotRegex(text, r"G(7[36]|8[1-9])\b")

    def test_no_gouge_no_steep_descent_into_material_no_low_rapid(self):
        for job, ctx, result in self.each():
            assert_sound(self, ctx, result)

    def test_holes_go_in_a_nearest_neighbour_tour_from_where_the_tool_is(self):
        from cadgen._internal.cam.operations import get_operation
        from cadgen._internal.cam.operations.context import PlanContext
        from cadgen._internal.cam.operations.drilling import tour

        # All three holes are 38.08 mm from the origin: the tie goes to the selector, then the nearest each time.
        self.assertEqual([hole["ref"] for hole in self.planned[4][1].stats["holes"]], [LEFT, RIGHT, BLIND])
        self.assertEqual([hole["ref"] for hole in self.planned[2][1].stats["holes"]], [LEFT, RIGHT])  # not file order
        ctx = PlanContext.from_step(self.step, self.job, 4, shared={}, start=(40.0, -20.0, 5.0))
        moved = get_operation("spot_drill").plan(ctx.params, ctx)
        self.assertEqual([hole["ref"] for hole in moved.stats["holes"]], [BLIND, RIGHT, LEFT])
        self.assertEqual(tour({"a": (0, 0), "b": (10, 0), "c": (1, 0), "d": (11, 0)}, (5, 0)), ["c", "a", "b", "d"])

    def test_a_blind_hole_is_never_cut_past_its_floor(self):
        for job, ctx, result in self.each():
            for hole in result.stats["holes"]:
                if (hole["x"], hole["y"]) == (35.0, -15.0):
                    self.assertGreaterEqual(hole["z"], BLIND_FLOOR - 1e-9)
        # The 5 mm drill in the flat-bottomed hole: its tip stops on the floor, and a warning says its full diameter
        # ends a point's length short of it.
        ctx, result = self.planned[1]
        point = 2.5 / math.tan(math.radians(59))
        self.assertAlmostEqual(result.stats["holes"][0]["z"], BLIND_FLOOR, places=6)
        self.assertAlmostEqual(result.stats["holes"][0]["full_diameter_z"], BLIND_FLOOR + point, places=5)
        self.assertEqual([found["type"] for found in result.findings], ["rest_material"])

    def test_a_through_hole_breaks_through_by_breakthrough(self):
        point = 3.0 / math.tan(math.radians(59))
        for index, breakthrough in ((0, 1.0), (2, 1.0), (3, 2.0)):
            for cycle in self.cycles(index):
                self.assertAlmostEqual(cycle.z, BOTTOM - point - breakthrough, places=5)
        for index in (5, 7, 8):          # flat-ended tools: no point; the tap goes to the blind hole's floor
            self.assertEqual(sorted(cycle.z for cycle in self.cycles(index)),
                             [BOTTOM - 1.0, BOTTOM - 1.0, BLIND_FLOOR][:len(self.cycles(index))])
        self.assertEqual({cycle.peck_mm for cycle in self.cycles(2)}, {6.0})
        self.assertEqual({cycle.peck_mm for cycle in self.cycles(3)}, {3.0})
        self.assertEqual({cycle.dwell_s for cycle in self.cycles(4)}, {0.2})

    def test_counterbore_and_back_bore_cut_the_counterbore_from_their_sides(self):
        (top, cbore), (under, back) = self.block_planned
        self.assertAlmostEqual(cbore.stats["holes"][0]["counterbore_floor_z"], -5.0, places=6)  # top -1, 4 deep
        self.assertAlmostEqual(cbore.stats["holes"][0]["z"], -10.0, places=6)                    # the 5 mm pilot
        cycle = next(move for move in back.toolpath.moves if move.kind == "cycle")
        self.assertEqual(cycle.cycle.return_to, "initial")
        self.assertLess(cycle.cycle.r, under.setup.stock_bottom_z)
        self.assertAlmostEqual(back.stats["holes"][0]["counterbore_top_z"] - cycle.cycle.z, 9 * 0.25, places=6)

    def test_it_stays_at_r_between_holes_only_when_the_way_across_is_clear(self):
        self.assertEqual([cycle.return_to for cycle in self.cycles(4)], ["r", "r", "r"])   # one G99 chain
        self.assertEqual([cycle.return_to for cycle in self.cycles(6)], ["initial"])       # a hole on its own: G98
        from cadgen._internal.cam.posts import get_post

        text = get_post("fanuc").program(self.job, self.job.setups[0], [self.planned[0][1].toolpath], part="plate")
        self.assertEqual(text.count("G99 G81"), 1)
        self.assertIn(" X35. Y15.\n", text)
        # Vise jaws 43 mm proud of the stock and a 50 mm holder: nothing is clear at R or at the retract plane, so
        # every cycle returns to where it started, and the tool goes over the jaws at the clearance height.
        job = {**JOB, "setups": [{"id": "top", "clearance_mm": 60,
                                  "fixture": {"type": "vise", "jaw_height_mm": 60, "grip_mm": 55}}],
               "tools": [{**TOOLS[2], "stickout_mm": 30, "holder": {"diameter_mm": 50, "length_mm": 40}}],
               "operations": [OPERATIONS[4]]}
        _, [(ctx, result)] = plan_all(self.step, job, {})
        moves = result.toolpath.moves
        self.assertEqual([m.cycle.return_to for m in moves if m.kind == "cycle"], ["initial"] * 3)
        tops = [m.xyz[2] for m in moves if m.kind == "rapid"]
        self.assertGreaterEqual(tops.count(ctx.heights.clearance), 3)

    def test_the_same_job_plans_the_same_moves(self):
        _, again = plan_all(self.step, JOB, {})
        self.assertEqual([result for _, result in again], [result for _, result in self.planned])

    def test_tools_and_holes_that_do_not_suit_are_refused_in_a_sentence(self):
        from cadgen._internal.cam.job import parse_job

        def plan_one(operation, tools=TOOLS):
            return plan_all(self.step, {**JOB, "tools": tools, "operations": [operation]}, {})

        self.assertIn("T1 is a drill, and tap cuts with a tap",
                      value_error(self, parse_job, {**JOB, "operations": [{"op": "tap", "tool": "T1", "all": True}]}))
        self.assertIn("tools[0].pitch_mm", value_error(self, parse_job, {
            **JOB, "tools": [{"id": "T4", "type": "tap", "diameter_mm": 6}], "operations": []}))
        def plan_error(operation, tools=TOOLS):
            from cadgen._internal.cam.operations.base import PlanError

            with self.assertRaises(PlanError) as caught:
                plan_one(operation, tools)
            return caught.exception.kind, str(caught.exception)

        kind, text = plan_error({"op": "drill", "tool": "T1", "features": [BLIND]})
        self.assertEqual(kind, "tool_too_large")
        self.assertIn("operations[0].features[0]: #o1.f7 is a 5 mm hole, and T1 is a 6 mm drill", text)
        tap8 = [{"id": "T9", "type": "tap", "diameter_mm": 8, "pitch_mm": 1.25}]
        kind, text = plan_error({"op": "tap", "tool": "T9", "all": True}, tap8)
        self.assertEqual(kind, "empty_operation")
        self.assertIn("no hole setup top reaches suits T9", text)
        self.assertEqual(plan_error({"op": "drill", "tool": "T2", "features": [BLIND], "depth_mm": 8})[0], "too_deep")
        cbore = [{"id": "T7", "type": "counterbore", "diameter_mm": 9}]
        self.assertEqual(plan_error({"op": "counterbore", "tool": "T7", "features": [LEFT]}, cbore)[0], "unreachable")
        self.assertIn("needs shift_mm", value_error(self, parse_job, {**JOB, "operations": [
            {"op": "boring_cycle", "tool": "T6", "all": True, "cycle": "fine_bore"}]}))


if __name__ == "__main__":
    unittest.main()
