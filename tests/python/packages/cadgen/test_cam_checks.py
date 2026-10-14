"""The plan's checks (``_internal/cam/checks.py``): each finding fires on a job built to trigger it, in words an
engineer agrees with, and stays quiet on the clean plate job.

The clean job is test_cam_plan's four operations (facing, pocket_2d, contour_2d, drill) plus a round pocket for the
plate's blind hole, so no feature is left uncut: it has notes and nothing else, and every check passes. The rest each
plan one job on the plate (or a thin-walled block) that trips several checks at once; gouge, a holder hitting the
part and an empty toolpath are checked on hand-made toolpaths, since the planner never makes them. Also here: a post's
refusal is a no_post_support finding (no G-code), an engraving's clear tool joins the same tool's last toolpath, and
the G-code header names each tool's plunge feed.
"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from tests.python.packages.cadgen.test_cam_plan import JOB, LEFT, POCKET, RIGHT
from tests.python.support.cam_fixtures import plate_step

#: The plate's 5 mm blind hole, 6 mm deep, as `cadgen cam features` names it.
BLIND = "#o1.f7"
SEVERITIES = ("error", "warning", "info")


def setUpModule():
    global DIR, STEP
    tmp = tempfile.TemporaryDirectory()
    unittest.addModuleCleanup(tmp.cleanup)
    DIR = Path(tmp.name)
    STEP = plate_step(DIR)


def thin_wall_step(directory):
    """A 40 x 30 x 10 mm block standing on z = 0, centred: a 20 x 10 x 5 mm pocket (R2 corners) 0.6 mm from its -X
    side."""
    from build123d import Align, Axis, Box, Pos, export_step, fillet

    base = (Align.CENTER, Align.CENTER, Align.MIN)
    block = Box(40, 30, 10, align=base)
    cutter = Pos(-20 + 0.6 + 10, 0, 5) * Box(20, 10, 6, align=base)
    block -= fillet(cutter.edges().filter_by(Axis.Z), 2)
    step = Path(directory) / "thin.step"
    export_step(block, str(step))
    return step


def plan(name: str, job: dict, step=None):
    from cadgen.cam import plan as cam_plan

    return cam_plan(step or STEP, DIR / name / "part.cam.glb", job=job)


def of(result, kind: str) -> list[dict]:
    return [f for f in result.findings if f["type"] == kind]


def verdicts(result) -> dict:
    return {check["type"]: check["status"] for check in json.loads(result.sidecar.read_text(encoding="utf-8"))["checks"]}


class Words:
    def assert_plain(self, findings):
        """Every finding in the KiCad shape, said in plain sentences: no em dash, no Python value leaking through."""
        for item in findings:
            with self.subTest(finding=item["summary"]):
                self.assertEqual(set(item), {"check", "severity", "type", "summary", "description", "items"})
                self.assertIn(item["severity"], SEVERITIES)
                text = item["summary"] + item["description"]
                for bad in ("\u2014", "None", "{", "e-0", "1e-"):
                    self.assertNotIn(bad, text)


class CleanPlate(Words, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        job = copy.deepcopy(JOB)
        job["tools"].append({"id": "T4", "type": "flat_endmill", "diameter_mm": 4, "flutes": 2,
                             "flute_length_mm": 12})
        job["tools"].append({"id": "T5", "type": "slitting_saw", "diameter_mm": 40, "width_mm": 2})
        job["operations"].append({"op": "circular", "tool": "T4", "features": [BLIND]})
        cls.result = plan("clean", job)

    def test_only_notes(self):
        from cadgen._internal.cam.checks import CHECK_KINDS

        r = self.result
        self.assertTrue(r.ok)
        self.assertEqual([(f["severity"], f["type"]) for f in r.findings],
                         [("info", "assumed_fixture"), ("info", "unused_tool"), ("info", "unused_tool"),
                          ("info", "cycle_time"), ("info", "tool_changes"), ("info", "post")])
        self.assertEqual(verdicts(r), dict.fromkeys(CHECK_KINDS, "pass"))
        self.assert_plain(r.findings)

    def test_the_notes_say_what_was_assumed_and_what_the_job_comes_to(self):
        summaries = [f["summary"] for f in self.result.findings]
        self.assertEqual(summaries[0], "Setup top's plate is modelled with sizes the job does not state: "
                                       "thickness_mm 12, border_mm 25")
        # T3 is test_cam_plan's spare; a slitting saw has no tool shape yet, which is fine while nothing runs it.
        self.assertEqual(summaries[1:3], ["T3 (flat endmill) is listed but no operation uses it",
                                          "T5 (slitting saw) is listed but no operation uses it"])
        self.assertRegex(summaries[3], r"^Cycle time \d+ min \d+ s: the moves at their feeds and the rapids$")
        self.assertEqual(summaries[4:], ["2 tool changes: T1 then T2 then T4", "G-code for LinuxCNC (linuxcnc post)"])

    def test_the_header_names_each_tools_plunge_feed(self):
        text = self.result.nc[0].read_text(encoding="ascii")
        # One line for T1 across its three operations (the contour's slower feed is not a second rate).
        self.assertEqual(len([line for line in text.splitlines() if line.startswith("(T1:")]), 1)
        self.assertIn("(T1: D6 mm flat_endmill 3 flutes: 21221 rpm, feed 2546.5 mm/min, plunge 764 mm/min)", text)
        self.assertIn("(T2: D6 mm drill: 14854 rpm, feed 1782.5 mm/min)", text)


class MachineAndFixture(Words, unittest.TestCase):
    """A small, slow machine; the drill's point goes through the stock into the plate under it; T1's stickout is the
    default (flutes plus a diameter)."""

    @classmethod
    def setUpClass(cls):
        job = copy.deepcopy(JOB)
        job["machine"].update(travel_mm=[100, 300, 100], max_rpm=10000, max_feed_mm_min=1000)
        job["stock"]["margin_mm"] = [2, 2, 1, 1]
        del job["tools"][0]["stickout_mm"]
        cls.result = plan("machine", job)

    def test_not_planned(self):
        r = self.result
        self.assertFalse(r.ok)
        self.assertEqual(r.nc, ())
        self.assert_plain(r.findings)
        v = verdicts(r)
        self.assertEqual((v["fixture_collision"], v["over_travel"], v["rpm_clamped"], v["gouge"]),
                         ("error", "error", "warning", "pass"))

    def test_the_drill_runs_into_the_plate_under_the_stock(self):
        [found] = of(self.result, "fixture_collision")
        self.assertEqual(found["summary"], "operations[3] (drill): T2's cutter runs 1.803 mm into the plate near "
                                           "X-35 Y15 Z-13.803 (setup top)")
        self.assertIn("assumed, as the job does not state them: thickness_mm, border_mm", found["description"])
        self.assertEqual(found["items"][0]["at"], [-35.0, 15.0, -2.803])   # the part's frame
        self.assertEqual(of(self.result, "through_stock_bottom"), [])     # the collision says it

    def test_over_travel_compares_the_span(self):
        [found] = of(self.result, "over_travel")
        self.assertEqual(found["summary"], "Setup top spans 112 mm in X, more than the machine's 100 mm X travel")
        self.assertIn("work offset is assumed free to sit anywhere", found["description"])

    def test_one_clamp_per_tool_lists_its_operations(self):
        rpm = of(self.result, "rpm_clamped")
        self.assertEqual([f["items"][0]["tool"] for f in rpm], ["T1", "T2"])
        self.assertEqual(rpm[0]["summary"], "T1 runs at the machine's top speed of 10,000 rpm, under the 21,221 rpm "
                                            "the material and tool call for, in 3 operations: operations[0] (facing), "
                                            "operations[1] (pocket_2d), operations[2] (contour_2d)")
        self.assertEqual([i["operation"] for i in rpm[0]["items"]], [0, 1, 2])
        self.assertEqual(len(of(self.result, "feed_clamped")), 2)

    def test_long_stickout(self):
        [found] = of(self.result, "long_stickout")
        self.assertEqual(found["summary"], "T1 sticks out 26 mm, 4.3 times its 6 mm diameter: it will bend and chatter")
        self.assertIn("The stickout is assumed", found["description"])


class NoFixture(unittest.TestCase):
    def test_a_through_drill_with_nothing_under_the_stock_is_a_warning(self):
        job = copy.deepcopy(JOB)
        job["stock"]["margin_mm"] = [2, 2, 1, 1]
        job["setups"][0]["fixture"] = {"type": "none"}
        job["operations"] = [{"op": "drill", "tool": "T2", "all": True}]
        result = plan("none", job)
        self.assertTrue(result.ok)
        self.assertEqual([(f["severity"], f["type"]) for f in result.findings if f["severity"] != "info"],
                         [("warning", "through_stock_bottom"), ("warning", "uncut_feature"),
                          ("warning", "uncut_feature")])   # the pocket, and the 5 mm hole a 6 mm drill skips


class GrblDrillAndTap(Words, unittest.TestCase):
    """grbl, a vise on 5 mm parallels: a through drill, a drill in the flat-bottomed blind hole, and a tap in a hole
    no operation drilled."""

    @classmethod
    def setUpClass(cls):
        job = copy.deepcopy(JOB)
        job["machine"]["post"] = "grbl"
        job["stock"]["margin_mm"] = [2, 2, 1, 1]
        job["setups"][0]["fixture"] = {"type": "vise", "parallels_mm": 5, "jaw_height_mm": 15}
        job["tools"] += [{"id": "T4", "type": "drill", "diameter_mm": 5},
                         {"id": "T5", "type": "tap", "diameter_mm": 7, "pitch_mm": 1}]
        job["operations"] = [{"op": "drill", "tool": "T2", "features": [LEFT]},
                             {"op": "drill", "tool": "T4", "features": [BLIND]},
                             {"op": "tap", "tool": "T5", "features": [RIGHT]}]
        cls.result = plan("grbl", job)

    def test_a_post_refusal_is_a_finding_and_no_program(self):
        r = self.result
        self.assertFalse(r.ok)
        self.assertEqual((r.nc, r.glb), ((), None))
        [found] = of(r, "no_post_support")
        self.assertEqual(found["summary"], "The grbl post cannot write this job: operations[2] (tap) has a tap cycle, "
                                           "and Grbl 1.1 has no tapping cycle")
        self.assertTrue(found["description"].endswith("No G-code was written"))
        self.assertEqual(verdicts(r)["no_post_support"], "error")
        self.assert_plain(r.findings)

    def test_through_the_stock_bottom_between_the_parallels(self):
        [found] = of(self.result, "through_stock_bottom")
        self.assertEqual(found["summary"], "operations[0] (drill): T2 goes 1.803 mm below the bottom of the stock, down "
                                           "to Z-13.803 (setup top; the stock's bottom is at Z-12)")
        self.assertIn("a gap between parallels", found["description"])
        self.assertEqual(of(self.result, "fixture_collision"), [])

    def test_a_tap_with_no_hole_made_before_it(self):
        [found] = of(self.result, "assumes_prepared_hole")
        self.assertEqual(found["summary"], f"operations[2] (tap): no earlier operation makes {RIGHT} before T5 threads "
                                           "it, so the job assumes the hole is already there")

    def test_the_operations_own_warnings_pass_through(self):
        [rest] = of(self.result, "rest_material")
        self.assertIn(f"short of {BLIND}'s floor", rest["summary"])
        self.assertEqual([f["items"][0]["ref"] for f in of(self.result, "uncut_feature")], [POCKET])


class CuttingFaults(Words, unittest.TestCase):
    """Pockets with a tool too big for the corners, one small enough to slot, one too short with its holder too
    close; a -Z setup naming a hole only +Z reaches; a contour above the stock."""

    @classmethod
    def setUpClass(cls):
        job = copy.deepcopy(JOB)
        job["tools"] = [
            {"id": "T6", "type": "flat_endmill", "diameter_mm": 10, "flutes": 3, "flute_length_mm": 20,
             "stickout_mm": 30},
            {"id": "T7", "type": "flat_endmill", "diameter_mm": 6, "flutes": 3, "flute_length_mm": 4,
             "stickout_mm": 5, "holder": {"diameter_mm": 20, "length_mm": 30}},
            {"id": "T8", "type": "flat_endmill", "diameter_mm": 3, "flutes": 2, "flute_length_mm": 9,
             "stickout_mm": 12},
        ]
        job["setups"] = [{"id": "top"}, {"id": "under", "direction": "-Z"}]
        job["operations"] = [
            {"op": "pocket_2d", "setup": "top", "tool": "T6", "features": [POCKET]},
            {"op": "pocket_2d", "setup": "top", "tool": "T8", "features": [POCKET]},
            {"op": "pocket_2d", "setup": "top", "tool": "T7", "features": [POCKET]},
            {"op": "circular", "setup": "under", "tool": "T8", "features": [BLIND]},
            {"op": "contour_2d", "setup": "top", "tool": "T6", "all": True, "z_mm": 5},
        ]
        cls.result = plan("faults", job)

    def test_verdicts(self):
        v = verdicts(self.result)
        self.assertEqual({kind: v[kind] for kind in ("too_deep", "holder_collision", "unreachable", "empty_operation",
                                                     "corner_left", "full_slot", "gouge", "fixture_collision")},
                         {"too_deep": "error", "holder_collision": "error", "unreachable": "error",
                          "empty_operation": "error", "corner_left": "warning", "full_slot": "warning",
                          "gouge": "pass", "fixture_collision": "pass"})
        self.assertEqual(self.result.findings[0]["severity"], "error")   # errors first
        self.assert_plain(self.result.findings)

    def test_corner_left(self):
        [found] = of(self.result, "corner_left")
        self.assertEqual(found["summary"], f"operations[0] (pocket_2d): T6 leaves the R3 inside corners of {POCKET} "
                                           "at R5: material stays in each corner")

    def test_full_slot(self):
        [found] = of(self.result, "full_slot")
        self.assertRegex(found["summary"], r"^operations\[1\] \(pocket_2d\): T8 cuts [\d.]+ mm at its full width, "
                                           r"every flute in the cut at once$")

    def test_too_deep_and_the_holder_in_the_stock(self):
        [deep] = of(self.result, "too_deep")
        self.assertEqual(deep["summary"], "operations[2] (pocket_2d): T7 cuts 6 mm deep with 4 mm of flutes: the shank "
                                          "would rub in the cut above them")
        [holder] = of(self.result, "holder_collision")
        self.assertRegex(holder["summary"], r"^operations\[2\] \(pocket_2d\): T7's holder comes down into the stock near "
                                            r"X\S+ Y\S+ Z\S+ \(setup top\), below the top of the material at Z0$")
        self.assertIn("Phase 1 does not simulate the stock", holder["description"])

    def test_uncut_features_name_the_setups_that_reach_them(self):
        summaries = [f["summary"] for f in of(self.result, "uncut_feature")]
        self.assertIn(f"{LEFT} (hole, 6 mm through) is reached from setups top and under but no operation cuts it",
                      summaries)
        self.assertNotIn(POCKET, " ".join(summaries))


class ThinWall(Words, unittest.TestCase):
    def test_a_pocket_close_to_the_outside(self):
        step = thin_wall_step(DIR)
        job = {"machine": {"kind": "mill", "post": "linuxcnc"}, "material": "aluminum-6061",
               "stock": {"type": "box", "margin_mm": [2, 2, 1, 1]}, "setups": [{"id": "top"}],
               "tools": [{"id": "T1", "type": "flat_endmill", "diameter_mm": 4, "flutes": 2, "flute_length_mm": 12}],
               "operations": [{"op": "pocket_2d", "tool": "T1", "features": ["#o1.f15"]}]}
        result = plan("thin", job, step)
        [found] = of(result, "thin_wall")
        self.assertRegex(found["summary"], r"^#o1\.f15 \(pocket\) leaves a 0\.6 mm wall to the part's outside \(#o1\.f\d+\) "
                                           r"at its thinnest$")
        self.assertTrue(result.ok)      # a warning
        self.assert_plain(result.findings)


class HandMadeToolpaths(Words, unittest.TestCase):
    """What the planner never makes, checked on toolpaths written by hand in the plate's top setup (part top z -1,
    bottom z -11, the -X wall at x -50)."""

    @classmethod
    def setUpClass(cls):
        from cadgen._internal.cam.job import parse_job
        from cadgen._internal.cam.operations.context import PlanContext

        job = copy.deepcopy(JOB)
        job["setups"][0]["fixture"] = {"type": "none"}
        job["tools"].append({"id": "T9", "type": "flat_endmill", "diameter_mm": 6, "flute_length_mm": 9,
                             "stickout_mm": 9.5, "holder": {"diameter_mm": 30, "length_mm": 40}})
        job["operations"] = [{"op": "facing", "tool": "T1"}, {"op": "facing", "tool": "T9"}]
        cls.job = parse_job(job)
        shared: dict = {}
        cls.ctx = [PlanContext.from_step(STEP, cls.job, index, shared=shared) for index in range(2)]

    def check(self, index: int, moves):
        from cadgen._internal.cam.checks import check_job
        from cadgen._internal.cam.operations.context import Planned
        from cadgen._internal.cam.run import PlannedOp
        from cadgen._internal.cam.toolpath import Move, Toolpath

        ctx = self.ctx[index]
        start = (*moves[0][1][:2], ctx.heights.clearance)
        made = tuple(Move(kind, xyz, None if kind == "rapid" else ctx.feeds.plunge_mm_min, index)
                     for kind, xyz in moves)
        toolpath = Toolpath(index, ctx.tool.id, ctx.feeds.rpm, "off", start, made)
        planned_op = PlannedOp(index, ctx.operation, ctx, Planned(toolpath))
        found = check_job(self.job, (planned_op,), part=ctx.part, stock=ctx.stock, setups={"top": ctx.setup},
                          features={"top": ctx.features})
        self.assert_plain(found)
        return found

    def test_a_plunge_into_the_part_is_a_gouge(self):
        found = self.check(0, [("plunge", (-30, -20, -4)), ("rapid", (-30, -20, 10))])
        [gouge] = [f for f in found if f["type"] == "gouge"]
        self.assertEqual(gouge["summary"], "operations[0] (facing): T1 cuts 3 mm into the part near X-30 Y-20 Z-4 "
                                           "(setup top)")
        self.assertAlmostEqual(gouge["items"][0]["depth_mm"], 3.0, delta=2e-3)

    def test_a_holder_coming_down_beside_the_part_hits_it(self):
        found = self.check(1, [("plunge", (-53.5, 0, -11)), ("rapid", (-53.5, 0, 10))])
        self.assertEqual([f["type"] for f in found if f["type"] == "gouge"], [])
        part = [f for f in found if f["type"] == "holder_collision" and "hits the part" in f["summary"]]
        self.assertEqual([f["summary"] for f in part],
                         ["operations[1] (facing): T9's shank or holder hits the part near X-53.5 Y0 Z-11 (setup top)"])

    def test_rapids_alone_are_an_empty_operation(self):
        found = self.check(0, [("rapid", (-60, 0, 10)), ("rapid", (60, 0, 10))])
        self.assertEqual([(f["type"], f["summary"]) for f in found if f["severity"] == "error"],
                         [("empty_operation", "operations[0] (facing) plans no cutting move: it cuts nothing")])


class EngraveClearTool(unittest.TestCase):
    def test_the_clear_tools_pass_joins_the_same_tools_last_toolpath(self):
        job = copy.deepcopy(JOB)
        job["tools"].append({"id": "T8", "type": "v_bit", "diameter_mm": 10, "tip_angle_deg": 90})
        job["operations"] = [{"op": "facing", "tool": "T1"},
                             {"op": "engrave", "tool": "T8", "region": [[-40, -25], [-20, -25], [-20, -12], [-40, -12]],
                              "max_depth_mm": 1, "clear_tool": "T1"}]
        result = plan("engrave", job)
        self.assertTrue(result.ok, [f["summary"] for f in result.findings if f["severity"] == "error"])
        text = result.nc[0].read_text(encoding="ascii")
        self.assertLess(text.index("(Op 1: engrave with T1)"), text.index("(Op 1: engrave with T8)"))


if __name__ == "__main__":
    unittest.main()
