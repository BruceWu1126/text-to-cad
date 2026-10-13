"""Slots and round features: slot, circular, bore and thread_mill.

For every operation: the posted program, read back, is the planned toolpath (linuxcnc and fanuc); the swept tool reaches
the walls it is aimed at and goes no further (a thread mill exactly its thread's depth further); it is sound
(cam_fixtures.assert_sound: no gouge, no steep descent into material, no sideways rapid under the retract plane); the
same job plans the same moves twice. Then the sizes: a bore's diameter, a slot's
width, a helix's pitch, a thread's hand; and the refusals of a tool that does not fit.
"""

from __future__ import annotations

import copy
import math
import tempfile
import unittest
from pathlib import Path

from tests.python.support.cam_fixtures import (
    assert_backplot,
    assert_sound,
    path_points,
    round_features_step,
    slotted_block_step,
    value_error,
)

#: The fixtures' features as `cadgen cam features` names them.
HOLE, BOSS = "#o1.f7", "#o1.f8"          # round plate: the 20 mm through hole, the 30 mm boss
SLOT, CBORED = "#o1.f4", "#o1.f14"       # slotted block: the 8 mm slot, the 5 mm hole under a 9 x 4 counterbore

_BASE = {"machine": {"kind": "mill", "post": "linuxcnc"}, "material": "aluminum-6061",
         "stock": {"type": "box", "margin_mm": [2, 2, 1, 1]}, "setups": [{"id": "top"}]}

#: The round plate's stock top is z 0: the boss's top z -1, the plate's top (the boss's foot) z -6, its bottom z -16.
ROUND = {**_BASE,
         "tools": [{"id": "T1", "type": "flat_endmill", "diameter_mm": 10, "flutes": 3, "flute_length_mm": 25},
                   {"id": "T2", "type": "flat_endmill", "diameter_mm": 6, "flutes": 3, "flute_length_mm": 20},
                   {"id": "T3", "type": "thread_mill", "diameter_mm": 12, "pitch_mm": 1.5, "flute_length_mm": 20},
                   {"id": "T4", "type": "thread_mill", "diameter_mm": 10, "pitch_mm": 2, "flute_length_mm": 20},
                   {"id": "T5", "type": "flat_endmill", "diameter_mm": 25}],
         "operations": [
             {"op": "circular", "tool": "T1", "features": [HOLE]},
             {"op": "bore", "tool": "T1", "features": [HOLE]},
             {"op": "circular", "tool": "T1", "features": [BOSS], "clear_mm": 12},
             {"op": "thread_mill", "tool": "T3", "features": [HOLE], "pitch_mm": 1.5},
             {"op": "thread_mill", "tool": "T4", "features": [BOSS], "pitch_mm": 2, "external": True},
         ]}
#: The slotted block's stock top is z 0: the slot's floor z -6.
BLOCK = {**_BASE,
         "tools": [{"id": "T1", "type": "flat_endmill", "diameter_mm": 8, "flutes": 3, "flute_length_mm": 20},
                   {"id": "T2", "type": "flat_endmill", "diameter_mm": 6, "flutes": 3, "flute_length_mm": 20},
                   {"id": "T3", "type": "flat_endmill", "diameter_mm": 10}],
         "operations": [
             {"op": "slot", "tool": "T1", "features": [SLOT]},
             {"op": "slot", "tool": "T2", "features": [SLOT], "finish_mm": 0.2},
             # An obround 8 mm wide round the line from (-30, -12) to (10, -12), 1 mm deep: the stock over the part.
             {"op": "slot", "tool": "T2", "depth_mm": 1,
              "region": [[cx + 4 * math.cos(a0 + math.pi * k / 16), -12 + 4 * math.sin(a0 + math.pi * k / 16)]
                         for cx, a0 in ((-30, math.pi / 2), (10, -math.pi / 2)) for k in range(17)]},
             {"op": "bore", "tool": "T2", "features": [CBORED], "target": "counterbore"},
         ]}


def _plan(step, job, index, shared):
    from cadgen._internal.cam.operations import get_operation
    from cadgen._internal.cam.operations.context import PlanContext

    ctx = PlanContext.from_step(step, job, index, shared=shared)
    return ctx, get_operation(ctx.operation.op).plan(ctx.params, ctx)


class Holes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cadgen._internal.cam.job import parse_job

        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.steps = {"round": round_features_step(Path(tmp.name)), "block": slotted_block_step(Path(tmp.name))}
        cls.jobs = {"round": ROUND, "block": BLOCK}
        cls.shared = {"round": {}, "block": {}}
        cls.runs = []
        for name, document in cls.jobs.items():
            job = parse_job(document)
            for index in range(len(job.operations)):
                ctx, planned = _plan(cls.steps[name], job, index, cls.shared[name])
                cls.runs.append((name, job, ctx, planned))

    def run_of(self, name: str, index: int):
        return next((ctx, planned) for job, _, ctx, planned in self.runs if job == name and ctx.index == index)

    def each(self):
        for name, job, ctx, planned in self.runs:
            with self.subTest(op=f"{name} {ctx.index} {ctx.operation.op}"):
                yield name, job, ctx, planned

    # --- every operation ---------------------------------------------------------------------------------

    def test_the_posted_program_read_back_is_the_toolpath(self):
        from cadgen._internal.cam.posts import get_post

        for name, job, ctx, planned in self.each():
            for post in ("linuxcnc", "fanuc"):
                text = get_post(post).program(job, job.setups[0], [planned.toolpath], part=name)
                assert_backplot(self, text, post, [planned.toolpath])

    def test_the_tool_reaches_the_walls_and_goes_no_further(self):
        from cadgen._internal.cam.operations.geometry2d import intrusion

        for name, job, ctx, planned in self.each():
            reach = intrusion(ctx, path_points(planned.toolpath, step=0.25))
            if ctx.operation.op == "thread_mill":       # a thread is cut its depth into the wall it was drawn at
                self.assertAlmostEqual(reach, ctx.params.thread_depth_mm, delta=ctx.tol)
            elif ctx.operation.region is not None:      # a region in the middle of the top comes nowhere near a wall
                self.assertLess(reach, -1.0)
            else:
                self.assertAlmostEqual(reach, 0.0, delta=ctx.tol)

    def test_no_gouge_no_steep_descent_into_material_no_low_rapid(self):
        for name, job, ctx, planned in self.each():
            assert_sound(self, ctx, planned)

    def test_the_same_job_plans_the_same_moves(self):
        fresh = {"round": {}, "block": {}}
        for name, job, ctx, planned in self.each():
            self.assertEqual(_plan(self.steps[name], self.jobs[name], ctx.index, fresh[name])[1], planned)

    # --- sizes -------------------------------------------------------------------------------------------

    def helices(self, toolpath):
        """``(sweep, dz)`` of every arc that changes height."""
        from cadgen._internal.cam.toolpath import arc_sweep, walk

        return [(arc_sweep(here, move), move.xyz[2] - here[2]) for here, move in walk(toolpath.moves, toolpath.start)
                if move.kind in ("arc_cw", "arc_ccw") and abs(move.xyz[2] - here[2]) > 1e-9]

    def test_a_bore_reaches_its_diameter_at_its_pitch_through_the_hole(self):
        ctx, planned = self.run_of("round", 1)
        (hole,) = planned.stats["holes"]
        self.assertEqual((hole["diameter_mm"], hole["floor_z"]), (20.0, -16.5))   # through, 0.5 mm past the bottom
        # 0.2 x the 10 mm tool is 2 mm, steeper than the 3 degree ramp angle at the centre's 5 mm circle.
        self.assertEqual(hole["pitch_mm"], 1.6464)
        helices = self.helices(planned.toolpath)
        self.assertTrue(helices)
        for sweep, dz in helices:
            self.assertGreater(sweep, 0)                                            # climb: counter-clockwise
            self.assertAlmostEqual(-dz / sweep * 2 * math.pi, 1.6464, delta=1e-3)
        points = path_points(planned.toolpath, step=0.2)
        self.assertAlmostEqual(max(math.hypot(p[0] + 20, p[1]) for p in points if abs(p[2] + 16.5) < 1e-6)
                               + ctx.radius, 10.0, delta=ctx.tol)
        ctx, planned = self.run_of("block", 3)
        self.assertEqual(planned.stats["holes"][0]["diameter_mm"], 9.0)            # the counterbore, to its floor
        self.assertEqual(planned.stats["holes"][0]["floor_z"], -5.0)

    def test_a_slot_is_cut_to_its_width(self):
        for index, rings in ((0, 0), (1, 1), (2, 1)):
            ctx, planned = self.run_of("block", index)
            (slot,) = planned.stats["slots"]
            self.assertEqual((slot["width_mm"], slot["rings"]), (8.0, rings))
            floor = slot["floor_z"]
            points = [p for p in path_points(planned.toolpath, step=0.2) if abs(p[2] - floor) < 1e-6]
            across = [p[1] - (-12.0 if index == 2 else 0.0) for p in points]
            self.assertAlmostEqual(max(across) + ctx.radius, 4.0, delta=ctx.tol)
            self.assertAlmostEqual(min(across) - ctx.radius, -4.0, delta=ctx.tol)
        self.assertEqual(self.run_of("block", 0)[1].stats["slots"][0]["ends"], ["open", "open"])
        self.assertEqual(self.run_of("block", 2)[1].stats["slots"][0]["ends"], ["round", "round"])

    def test_round_pockets_and_bosses_are_cut_to_size_climbing(self):
        ctx, planned = self.run_of("round", 0)
        self.assertEqual(planned.stats["targets"][0]["diameter_cut_mm"], 20.0)
        self.assertTrue(all(sweep > 0 for sweep, _ in self.helices(planned.toolpath)))   # helix entry, CCW
        ctx, planned = self.run_of("round", 2)
        target = planned.stats["targets"][0]
        self.assertEqual((target["kind"], target["diameter_cut_mm"], target["floor_z"]), ("boss", 30.0, -6.0))
        arcs = [m.kind for m in planned.toolpath.moves if m.kind.startswith("arc")]
        self.assertEqual(set(arcs), {"arc_cw"})                                      # round a boss: clockwise

    def test_threads_follow_their_pitch_and_hand(self):
        ctx, planned = self.run_of("round", 3)
        (thread,) = planned.stats["threads"]
        self.assertAlmostEqual(thread["thread_depth_mm"], 0.6134 * 1.5, places=6)
        self.assertAlmostEqual(thread["major_diameter_mm"], 20 + 2 * 0.6134 * 1.5, places=5)
        self.assertLess(thread["from_z"], thread["to_z"])                            # right hand inside: up
        for sweep, dz in self.helices(planned.toolpath):
            self.assertGreater(sweep, 0)
            self.assertAlmostEqual(dz / sweep * 2 * math.pi, 1.5, delta=1e-3)
        ctx, planned = self.run_of("round", 4)
        (thread,) = planned.stats["threads"]
        self.assertGreater(thread["from_z"], thread["to_z"])                         # right hand outside: down
        for sweep, dz in self.helices(planned.toolpath):
            self.assertLess(sweep, 0)
            self.assertAlmostEqual(dz / sweep * 2 * math.pi, 2.0, delta=1e-3)

    # --- refusals ----------------------------------------------------------------------------------------

    def refused(self, name: str, index: int, **keys) -> tuple[str, str]:
        from cadgen._internal.cam.operations.base import PlanError

        document = copy.deepcopy(self.jobs[name])
        document["operations"][index].update(keys)
        with self.assertRaises(PlanError) as caught:
            _plan(self.steps[name], document, index, self.shared[name])
        return caught.exception.kind, str(caught.exception)

    def test_a_tool_that_does_not_fit_is_refused(self):
        self.assertEqual(self.refused("block", 0, tool="T3"),
                         ("tool_too_large", "operations[0]: the 10 mm tool is wider than #o1.f4, a 8 mm slot"))
        kind, message = self.refused("round", 1, tool="T5")
        self.assertEqual(kind, "tool_too_large")
        self.assertIn("the 25 mm tool does not fit in #o1.f7 (20 mm)", message)
        kind, message = self.refused("round", 1, tool="T2")
        self.assertEqual(kind, "tool_too_small")
        self.assertIn("would leave a core 8 mm across standing in its middle; use circular", message)
        from cadgen._internal.cam.job import parse_job

        document = copy.deepcopy(ROUND)
        document["operations"][3]["pitch_mm"] = 1.0
        self.assertIn("operations[3].pitch_mm: T3 is a thread mill for a 1.5 mm pitch, not 1 mm",
                      value_error(self, parse_job, document))
        del document["operations"][3]["pitch_mm"]
        self.assertIn("operations[3].pitch_mm: the thread's pitch", value_error(self, parse_job, document))


if __name__ == "__main__":
    unittest.main()
