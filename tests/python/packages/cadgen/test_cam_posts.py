"""Posts: each dialect's header, tool change, arc, peck cycle and footer, byte for byte; the same input gives the
same bytes; inch converts; every post's program, read back, is the toolpath it was given; refusals.

Stdlib only. The operations are stand-ins (the job only needs to parse); the posts are the real ones.
"""

from __future__ import annotations

import copy
import unittest

from tests.python.support.cam_fixtures import assert_backplot, stub_operations, value_error

from cadgen._internal.cam.job import parse_job
from cadgen._internal.cam.posts import POST_NAMES, built, get_post
from cadgen._internal.cam.toolpath import Cycle, Move, Toolpath

PHASE_1 = ("grbl", "linuxcnc", "fanuc", "haas", "mach3")

JOB = {
    "machine": {"kind": "mill", "axes": 3, "post": "fanuc", "units": "mm"},
    "material": "aluminum-6061",
    "stock": {"type": "box", "margin_mm": [2, 2, 1, 0]},
    "setups": [{"id": "top", "direction": "+Z", "origin": "stock_top_center"}],
    "tools": [
        {"id": "T1", "type": "flat_endmill", "diameter_mm": 6, "flutes": 3},
        {"id": "T2", "type": "drill", "diameter_mm": 5},
        {"id": "T3", "type": "tap", "diameter_mm": 6, "pitch_mm": 1},
    ],
    "operations": [
        {"op": "facing", "tool": "T1", "coolant": "flood"},
        {"op": "drill", "tool": "T2", "features": ["#o1.f20"]},
        {"op": "tap", "tool": "T3", "features": ["#o1.f21"]},
    ],
}


def job(units: str = "mm"):
    document = copy.deepcopy(JOB)
    document["machine"]["units"] = units
    with stub_operations():
        return parse_job(document)


def mill() -> Toolpath:
    """T1: down, a cut with a dwell, a helical arc, an arc, up."""
    moves = (Move("rapid", (10, 0, 10)), Move("rapid", (10, 0, 1)), Move("plunge", (10, 0, -1), 300),
             Move("feed", (20, 0, -1), 1000, dwell_s=0.5),
             Move("arc_ccw", (10, 10, -1.5), 1000, center=(10, 0)),
             Move("arc_cw", (0, 0, -1.5), 1000, center=(10, 0)), Move("rapid", (0, 0, 10)))
    return Toolpath(0, "T1", 8000, "flood", (0, 0, 10), moves)


#: Every cycle kind but the taps: two pecks back to the initial level (G98), the rest back to R (G99).
CYCLES = (
    ((5, 5, 10), Cycle("peck", 2, -8, peck_mm=3)),
    ((15, 5, 10), Cycle("peck", 2, -8, peck_mm=3)),
    ((25, 5, 2), Cycle("spot", 2, -1, dwell_s=0.5)),
    ((35, 5, 2), Cycle("drill", 2, -6)),
    ((45, 5, 2), Cycle("chip_break", 2, -6, peck_mm=1.5)),
    ((55, 5, 2), Cycle("ream", 2, -6, dwell_s=0.3)),
    ((65, 5, 2), Cycle("peck", 2, -6, peck_mm=2.5, dwell_s=0.2)),
    ((75, 5, 2), Cycle("bore", 2, -6)),
    ((85, 5, 2), Cycle("fine_bore", 2, -6, shift_mm=0.2)),
    ((95, 5, 2), Cycle("back_bore", -12, -5, shift_mm=1)),
)


def accepted(post, cycles):
    out = []
    for xyz, cycle in cycles:
        move = Move("cycle", xyz, 200, 1, cycle=cycle)
        try:
            post.route(move, "test")
        except ValueError:
            continue
        out.append(move)
    return out


def drill(post) -> Toolpath:
    moves = accepted(post, CYCLES)
    return Toolpath(1, "T2", 3000, "off", (0, 0, 10), (*moves, Move("rapid", (moves[-1].xyz[0], 5, 10), op=1)))


def tap(post) -> Toolpath | None:
    for kind in ("rigid_tap", "tap"):
        moves = accepted(post, (((0, 20, 2), Cycle(kind, 2, -10, pitch_mm=1)),))
        if moves:
            moves = [Move("cycle", move.xyz, 500, 2, cycle=move.cycle) for move in moves]
            return Toolpath(2, "T3", 500, "off", (0, 20, 10), (*moves, Move("rapid", (0, 20, 10), op=2)))
    return None


def toolpaths_for(post) -> list[Toolpath]:
    return [path for path in (mill(), drill(post), tap(post)) if path is not None]


def posted(name: str, units: str = "mm") -> str:
    post = get_post(name)
    parsed = job(units)
    return post.program(parsed, parsed.setups[0], toolpaths_for(post), part="plate.step", job_name="plate.cam.json")


#: Per post: the header's start, the second tool change, the helical arc, the peck cycle, the footer.
GOLDEN = {
    "grbl": (
        "(Part: plate.step)\n(Job: plate.cam.json)\n(Setup: top, tool axis +Z)\n(WCS: G54 at stock_top_center)\n"
        "(Post: grbl, for Grbl 1.1, units mm, dwell P in seconds)\n",
        "M5\nM9\n(Tool change: load T2, D5 mm drill, zero Z on it, then resume)\nM0\nS3000 M3\nG0 Z10\nG0 X0 Y0\n",
        "G1 X20 F1000\nG4 P0.5\nG3 X10 Y10 Z-1.5 I-10 J0\nG2 X0 Y0 I0 J-10\n",
        "G0 X5 Y5\nG0 Z2\nG1 Z-1 F200\nG0 Z2\nG0 Z-0.5\nG1 Z-4\n",
        "G0 Z10\nM5\nM9\nM2\n",
    ),
    "linuxcnc": (
        "(Post: linuxcnc, for LinuxCNC, units mm, dwell P in seconds)\n",
        "M5\nM9\nG53 G0 Z0\nT2 M6\nS3000 M3\nG0 X0 Y0\nG43 Z10 H2\n",
        "G3 X10 Y10 Z-1.5 I-10 J0\n",
        "G98 G83 X5 Y5 Z-8 R2 Q3 F200\nX15 Y5\nG80\n",
        "G0 Z2\nG33.1 Z-10 K1\nG0 Z10\nM5\nM9\nG53 G0 Z0\nM2\n",
    ),
    "fanuc": (
        "%\nO1000 (PLATE.STEP)\n(PART: PLATE.STEP)\n",
        "N190 M5\nN200 M9\nN210 G91 G28 Z0.\nN220 G90\nN230 T2 M6\nN240 S3000 M3\nN250 G0 X0. Y0.\nN260 G43 Z10. H2\n",
        "N160 G3 X10. Y10. Z-1.5 I-10. J0.\n",
        "N270 G98 G83 X5. Y5. Z-8. R2. Q3. F200.\nN280 X15. Y5.\nN290 G80\n",
        "N650 M29 S500\nN660 G99 G84 X0. Y20. Z-10. R2. F500.\nN670 G80\nN680 G0 Z10.\nN690 M5\nN700 M9\n"
        "N710 G91 G28 Z0.\nN720 G90\nN730 M30\n%\n",
    ),
    "haas": (
        "%\nO01000 (PLATE.STEP)\n(Part: plate.step)\n",
        "M5\nM9\nG53 G0 Z0.\nN2 T2 M6\nS3000 M3\nG0 X0. Y0.\nG43 Z10. H2\n",
        "G3 X10. Y10. Z-1.5 I-10. J0.\n",
        "G98 G83 X5. Y5. Z-8. R2. Q3. F200.\nX15. Y5.\nG80\nG99 G82 X25. Y5. Z-1. R2. P0.5 F200.\n",
        "G99 G84 X0. Y20. Z-10. R2. F500.\nG80\nG0 Z10.\nM5\nM9\nG53 G0 Z0.\nM30\n%\n",
    ),
    "mach3": (
        "(Dwell P is seconds: General Config G04 Dwell param in Ms must be off)\n",
        "M5\nM9\nT2 M6\nS3000 M3\nG43 H2\nG0 Z10\nG0 X0 Y0\n",
        "G2 X0 Y0 I0 J-10\n",
        "G98 G83 X5 Y5 Z-8 R2 Q3 F200\nX15 Y5\nG80\n",
        "G99 G84 X0 Y20 Z-10 R2 F500\nG80\nG0 Z10\nM5\nM9\nM30\n",
    ),
}


class Programs(unittest.TestCase):
    def test_golden_snippets_per_post(self):
        for name, snippets in GOLDEN.items():
            text = posted(name)
            for snippet in snippets:
                with self.subTest(name, snippet=snippet.splitlines()[0]):
                    self.assertIn(snippet, text)
            prove = "Prove this program on the machine: single block, feed override low, in air first."
            self.assertIn(f"({prove.upper() if name == 'fanuc' else prove})\n", text)

    def test_the_same_input_gives_the_same_bytes(self):
        for name in PHASE_1:
            with self.subTest(name):
                self.assertEqual(posted(name).encode(), posted(name).encode())

    def test_an_inch_program_converts_every_coordinate_and_feed(self):
        text = posted("haas", "inch")
        self.assertIn("G20\n", text)
        self.assertNotIn("G21", text)
        self.assertIn("G1 Z-0.0394 F11.81\nG1 X0.7874 F39.37\n", text)
        self.assertIn("G3 X0.3937 Y0.3937 Z-0.0591 I-0.3937 J0.\n", text)
        for name in PHASE_1:
            with self.subTest(name):
                # 0.0001 in is the resolution (0.00127 mm at worst per number); a controller pecks in steps of the
                # rounded Q from the rounded R, so a peck's depth is off by one rounding per peck (0.01 mm by the 4th)
                assert_backplot(self, posted(name, "inch"), name, toolpaths_for(get_post(name)), tol=0.01, feed_tol=0.13)

    def test_every_posts_program_read_back_is_the_toolpath(self):
        for name in PHASE_1:
            with self.subTest(name):
                post = get_post(name)
                assert_backplot(self, posted(name), name, toolpaths_for(post))
        # grbl has no canned cycles: all of its drilling is expanded moves
        self.assertNotRegex(posted("grbl"), r"G(8\d|73|76)")
        self.assertEqual(len(drill(get_post("grbl")).moves), 9)   # fine and back bore refused, the rest expanded

    def test_a_post_refuses_what_its_controller_cannot_run(self):
        parsed = job()
        rigid = Toolpath(2, "T3", 500, "off", (0, 20, 10), (Move("cycle", (0, 20, 2), 500, 2, cycle=Cycle("rigid_tap", 2, -10, pitch_mm=1)),))
        for name, fragment in (("grbl", "grbl post: operations[2] (tap) has a rigid_tap cycle, and Grbl 1.1 has no "
                                        "spindle-synchronised (rigid) tapping"),
                               ("mach3", "mach3 post: operations[2] (tap) has a rigid_tap cycle, and Mach3 has no")):
            with self.subTest(name):
                message = value_error(self, get_post(name).program, parsed, parsed.setups[0], [mill(), rigid])
                self.assertIn(fragment, message)
                self.assertIn("the tap would break", message)
        bore = Toolpath(1, "T2", 3000, "off", (95, 5, 2), (Move("cycle", (95, 5, 2), 200, 1, cycle=CYCLES[-1][1]),))
        self.assertIn("grbl post: operations[1] (drill) has a back_bore cycle, and Grbl 1.1 can neither",
                      value_error(self, get_post("grbl").program, parsed, parsed.setups[0], [bore]))
        apart = Toolpath(0, "T1", 8000, "off", (50, 50, 10), (Move("rapid", (0, 0, 10)),))
        self.assertIn("fanuc post: operations[0] (facing) starts at (50, 50, 10), but the tool is at (0, 0, 10)",
                      value_error(self, get_post("fanuc").program, parsed, parsed.setups[0], [mill(), apart]))

    def test_programs_split_by_setup_and_number_each(self):
        document = copy.deepcopy(JOB)
        document["setups"].append({"id": "bottom", "direction": "-Z"})
        for operation, setup in zip(document["operations"], ("top", "bottom", "bottom")):
            operation["setup"] = setup
        with stub_operations():
            parsed = parse_job(document)
        post = get_post("fanuc")
        programs = post.programs(parsed, parsed.setups, toolpaths_for(post), part="plate.step")
        self.assertEqual(list(programs), ["top", "bottom"])
        self.assertIn("O1000 (PLATE.STEP)", programs["top"])
        self.assertIn("O1001 (PLATE.STEP)", programs["bottom"])
        self.assertIn("(SETUP: BOTTOM, TOOL AXIS -Z)", programs["bottom"])


class Registry(unittest.TestCase):
    def test_the_five_phase_1_posts_are_built_and_the_rest_still_refuse(self):
        self.assertEqual(built(), PHASE_1)
        self.assertEqual(POST_NAMES[:5], PHASE_1)
        self.assertEqual(value_error(self, get_post, "linuxcnc_5x"),
                         'post: "linuxcnc_5x" is phase 4, not built yet; built today: grbl, linuxcnc, fanuc, haas, mach3')
        self.assertIn('post: "marlin" is not a post cadgen knows', value_error(self, get_post, "marlin"))


if __name__ == "__main__":
    unittest.main()
