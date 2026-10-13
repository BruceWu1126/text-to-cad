"""Shared fixtures for the CAM suites (``tests/python/packages/cadgen/test_cam*.py``).

Importing this puts cadgen's source on ``sys.path``. Heavy imports (build123d,
shapely, numpy, OCP) stay inside the helpers, so the job and registry tests run
on the standard library alone.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from contextlib import contextmanager

from tests.python.support.paths import REPO_ROOT, add_repo_path

add_repo_path("packages/cadgen/src")

#: What CAM must not import before it plans: the kernel, the geometry and the numerics.
HEAVY = ("OCP", "build123d", "shapely", "numpy", "scipy")


def heavy_at_import(*modules: str) -> str:
    """The heavy modules importing ``modules`` pulls into a fresh interpreter: "" when they are stdlib-only at import."""
    code = (f"import sys; sys.path.insert(0, 'packages/cadgen/src'); import {', '.join(modules)};"
            f"print(','.join(m for m in {HEAVY!r} if m in sys.modules))")
    return subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout.strip()


def value_error(case: unittest.TestCase, call, *args, **kwargs) -> str:
    """The message of the ValueError ``call(*args, **kwargs)`` must raise."""
    with case.assertRaises(ValueError) as caught:
        call(*args, **kwargs)
    return str(caught.exception)


# --- Stand-in operations -------------------------------------------------------------------------------
# No operation is built until its phase-1 task lands. These stand in for four of them, registered the way a
# built one is (its family module defines its class), so the job parser's delegation can be tested now.

from cadgen._internal.cam.operations.base import OperationBase  # noqa: E402
from cadgen._internal.cam.values import number  # noqa: E402


class StubFacing(OperationBase):
    name, phase, word = "facing", 1, "Face"
    keys = frozenset({"stepdown_mm"})
    tool_types = ("flat_endmill", "face_mill")
    targets = ("all", "region")
    target_required = False

    def parse(self, raw, ctx):
        return {"stepdown_mm": number(raw.get("stepdown_mm", 1.0), where=f"{ctx.where}.stepdown_mm", positive=True)}


class StubPocket2d(StubFacing):
    name, word = "pocket_2d", "Pocket"
    tool_types = ("flat_endmill", "bull_endmill", "ball_endmill")
    targets = ("features", "region")
    target_required = True


class StubDrill(OperationBase):
    name, phase, word = "drill", 1, "Drill"
    keys = frozenset({"depth_mm"})
    tool_types = ("drill", "spot_drill")

    def parse(self, raw, ctx):
        return {"depth_mm": None if "depth_mm" not in raw else number(raw["depth_mm"], where=f"{ctx.where}.depth_mm", positive=True)}


class StubTap(StubDrill):
    name, word = "tap", "Tap"
    tool_types = ("tap",)


STUBS = {"facing": "StubFacing", "pocket_2d": "StubPocket2d", "drill": "StubDrill", "tap": "StubTap"}


def stub_operations(*names: str):
    """``REGISTRY`` with these operations (all four stand-ins by default) built as this module's classes."""
    from unittest import mock

    from cadgen._internal.cam.operations import REGISTRY, Entry

    entries = {name: Entry(name, REGISTRY[name].phase, __name__, STUBS[name], REGISTRY[name].word)
               for name in (names or STUBS)}
    return mock.patch.dict(REGISTRY, entries)


class StubPost:
    """Stands in for a post in tests that only parse jobs (a real post imports nothing heavy either)."""


def stub_posts(*names: str):
    """The posts' ``REGISTRY`` with these posts (grbl and haas by default) built as :class:`StubPost`."""
    from dataclasses import replace
    from unittest import mock

    from cadgen._internal.cam.posts import REGISTRY

    entries = {name: replace(REGISTRY[name], module=__name__, cls="StubPost") for name in (names or ("grbl", "haas"))}
    return mock.patch.dict(REGISTRY, entries)


@contextmanager
def stubbed():
    """The four stand-in operations and the grbl and haas posts, built, for a job that parses."""
    with stub_operations(), stub_posts():
        yield


# --- Part fixtures -------------------------------------------------------------------------------------
# build123d parts written as STEP into a directory the test owns (never models/). Heavy imports stay inside.

#: The plate every phase-1 suite machines: 100 x 60 x 10 mm standing on z = 0, centred in x and y. From the top
#: (+Z): a 40 x 20 x 5 mm pocket with R3 inside corners, centred; two 6 mm through holes; one 5 mm blind hole.
PLATE = {
    "size_mm": (100.0, 60.0, 10.0),
    "pocket": {"center": (0.0, 0.0), "length_mm": 40.0, "width_mm": 20.0, "depth_mm": 5.0, "corner_radius_mm": 3.0},
    "through_holes": ((-35.0, 15.0), (35.0, 15.0)),
    "through_diameter_mm": 6.0,
    "blind_hole": (35.0, -15.0),
    "blind_diameter_mm": 5.0,
    "blind_depth_mm": 6.0,
}


def plate_step(directory, name: str = "plate", turned_deg: float = 0.0):
    """:data:`PLATE` written to ``directory/name.step``, turned ``turned_deg`` about Z when asked."""
    from pathlib import Path

    from build123d import Align, Axis, Box, Cylinder, Pos, Rot, export_step, fillet

    base = (Align.CENTER, Align.CENTER, Align.MIN)
    length, width, height = PLATE["size_mm"]
    plate = Box(length, width, height, align=base)
    pocket = PLATE["pocket"]
    # Every cutter overshoots the top by 1 mm, so no boolean meets a coplanar face.
    cutter = Pos(*pocket["center"], height - pocket["depth_mm"]) * Box(
        pocket["length_mm"], pocket["width_mm"], pocket["depth_mm"] + 1, align=base)
    plate -= fillet(cutter.edges().filter_by(Axis.Z), pocket["corner_radius_mm"])
    for x, y in PLATE["through_holes"]:
        plate -= Pos(x, y, -1) * Cylinder(PLATE["through_diameter_mm"] / 2, height + 2, align=base)
    depth = PLATE["blind_depth_mm"]
    plate -= Pos(*PLATE["blind_hole"], height - depth) * Cylinder(PLATE["blind_diameter_mm"] / 2, depth + 1, align=base)
    if turned_deg:
        plate = Rot(0, 0, turned_deg) * plate
    step = Path(directory) / f"{name}.step"
    export_step(plate, str(step))
    return step


def slotted_block_step(directory, name: str = "block"):
    """An 80 x 40 x 20 mm block standing on z = 0, centred in x and y: an 8 mm wide, 5 mm deep slot along x
    across the top; a step 10 mm down over its last 10 mm in +x, which the slot runs into; and a 5 mm through
    hole at (-25, 12) under a 9 mm counterbore 4 mm deep."""
    from pathlib import Path

    from build123d import Align, Box, Cylinder, Pos, export_step

    base = (Align.CENTER, Align.CENTER, Align.MIN)
    block = Box(80, 40, 20, align=base)
    block -= Pos(0, 0, 15) * Box(100, 8, 10, align=base)
    block -= Pos(30, 0, 10) * Box(30, 50, 20, align=(Align.MIN, Align.CENTER, Align.MIN))
    block -= Pos(-25, 12, -1) * Cylinder(2.5, 30, align=base)
    block -= Pos(-25, 12, 16) * Cylinder(4.5, 10, align=base)
    step = Path(directory) / f"{name}.step"
    export_step(block, str(step))
    return step


def round_features_step(directory, name: str = "round"):
    """An 80 x 60 x 10 mm plate standing on z = 0, centred in x and y: a 20 mm through hole at (-20, 0), and a 30 mm
    round boss 5 mm tall standing on the top at (18, 0)."""
    from pathlib import Path

    from build123d import Align, Box, Cylinder, Pos, export_step

    base = (Align.CENTER, Align.CENTER, Align.MIN)
    plate = Box(80, 60, 10, align=base)
    plate -= Pos(-20, 0, -1) * Cylinder(10, 12, align=base)
    plate += Pos(18, 0, 10) * Cylinder(15, 5, align=base)
    step = Path(directory) / f"{name}.step"
    export_step(plate, str(step))
    return step


#: A drill's point: 118 degrees included, so its length past the full diameter is the radius / tan(59 degrees).
DRILL_POINT_DEG = 118.0


def drilled_block_step(directory, name: str = "drilled"):
    """A 60 x 40 x 30 mm block standing on z = 0, centred in x and y, drilled from the top (+Z):

    - at (-15, 0): a 6.8 mm blind hole, 12 mm of full diameter and a 118 degree drill point below;
    - at (15, 10): a 5 mm through hole under a 10 mm, 90 degree countersink;
    - at (15, -10): a 6 mm through hole, crossed at z = 15 by a 6 mm hole along x right through the block.
    """
    import math
    from pathlib import Path

    from build123d import Align, Box, Cone, Cylinder, Pos, Rot, export_step

    base = (Align.CENTER, Align.CENTER, Align.MIN)
    block = Box(60, 40, 30, align=base)
    point = 3.4 / math.tan(math.radians(DRILL_POINT_DEG / 2))
    block -= Pos(-15, 0, 18) * Cylinder(3.4, 13, align=base)
    block -= Pos(-15, 0, 18) * Cone(0, 3.4, point, align=(Align.CENTER, Align.CENTER, Align.MAX))
    block -= Pos(15, 10, -1) * Cylinder(2.5, 32, align=base)
    block -= Pos(15, 10, 27.5) * Cone(2.5, 6, 3.5, align=base)  # radius 5 at the top face, 1 mm overshoot
    block -= Pos(15, -10, -1) * Cylinder(3, 32, align=base)
    block -= Pos(0, -10, 15) * Rot(0, 90, 0) * Cylinder(3, 62)
    step = Path(directory) / f"{name}.step"
    export_step(block, str(step))
    return step


def island_pocket_step(directory, name: str = "island"):
    """An 80 x 60 x 10 mm plate standing on z = 0, centred: a 50 x 40 x 5 mm pocket with R4 corners from the top,
    and a 10 x 10 mm island standing in its middle up to the top face."""
    from pathlib import Path

    from build123d import Align, Axis, Box, Pos, export_step, fillet

    base = (Align.CENTER, Align.CENTER, Align.MIN)
    plate = Box(80, 60, 10, align=base)
    cutter = Pos(0, 0, 5) * Box(50, 40, 6, align=base)
    plate -= fillet(cutter.edges().filter_by(Axis.Z), 4)
    plate += Pos(0, 0, 5) * Box(10, 10, 5, align=base)
    step = Path(directory) / f"{name}.step"
    export_step(plate, str(step))
    return step

# --- G-code back-plot ----------------------------------------------------------------------------------
# Reads any phase-1 post's program back into moves (absolute, mm, cycles expanded the way expand_cycle does), so a
# test can assert "the posted program is the planned toolpath". It is stricter than the controllers where a program
# would be wrong: it refuses a cut while the spindle is stopped, a block other than a bare X/Y while a canned cycle
# is modal (write G80 first), and a boring bar's shift off the wall while its spindle is not oriented.

import math  # noqa: E402
import re  # noqa: E402
from dataclasses import dataclass  # noqa: E402

_WORD = re.compile(r"([A-Za-z])\s*([-+]?(?:\d+\.?\d*|\.\d+))")
_CYCLE_KINDS = {"81": "drill", "82": "spot", "83": "peck", "73": "chip_break", "84": "tap", "85": "ream",
                "89": "ream", "86": "bore", "76": "fine_bore", "87": "back_bore"}
#: What a dwell's P means on each controller, as its own documentation says (not as the post says): Fanuc whole
#: milliseconds; Haas seconds when P has a decimal point, else milliseconds; the rest seconds.
_DWELL_UNIT = {"fanuc": "ms", "haas": "point", "linuxcnc": "s", "mach3": "s", "grbl": "s"}
#: The longest sideways rapid read as a boring bar's shift off the wall (a dog-leg out, along Z, and back), mm.
_SHIFT_MM = 5.0


@dataclass(frozen=True)
class Plotted:
    """One move read back from a program: ``kind`` is rapid (G0), feed (G1), arc_cw or arc_ccw; ``xyz`` in mm, an
    axis ``None`` while the program has not said where it is (after a tool change or a move in machine coordinates);
    ``segment`` counts the tool changes before it; ``spindle`` is on, off, oriented, or cycle (inside a canned
    cycle, which runs the spindle itself)."""

    kind: str
    xyz: tuple
    feed: float | None = None
    center: tuple | None = None
    dwell_s: float = 0.0
    segment: int = 0
    spindle: str = "on"


def _plain(move) -> str:
    return {"rapid": "rapid", "arc_cw": "arc_cw", "arc_ccw": "arc_ccw"}.get(move.kind, "feed")


def backplot(text: str, post: str) -> list[Plotted]:
    """The moves a program makes, read back; ValueError naming the line where it would cut wrong. ``post`` names the
    controller (its documented dwell unit)."""
    from cadgen._internal.cam.toolpath import Cycle, Move, expand_cycle

    unit = _DWELL_UNIT[post]
    scale, pos, motion, feed, rpm, segment = 1.0, [None, None, None], None, None, None, 0
    cycle, return_r, initial, spindle, just_started = None, False, None, "off", False
    out: list[Plotted] = []

    def add(kind, xyz, *, center=None, at_feed=None, dwell=0.0, state=None):
        out.append(Plotted(kind, tuple(xyz), at_feed, center, dwell, segment, state or spindle))
        pos[:] = list(xyz)

    def dwell_seconds(raw: str) -> float:
        if unit == "ms" or (unit == "point" and "." not in raw):
            return float(raw) / 1000.0
        return float(raw)

    for number, line in enumerate(text.splitlines(), 1):
        line = re.sub(r"\([^)]*\)", "", line).split(";")[0].strip()
        if not line or line == "%" or line.upper().startswith("O"):
            continue
        words = [(letter.upper(), raw) for letter, raw in _WORD.findall(line) if letter.upper() != "N"]
        gs = [raw.rstrip(".") if raw.endswith(".") else raw for letter, raw in words if letter == "G"]
        ms = [int(float(raw)) for letter, raw in words if letter == "M"]
        vals = {letter: raw for letter, raw in words if letter not in ("G", "M")}
        num = {letter: float(raw) for letter, raw in vals.items()}
        where = f"line {number} ({line})"
        if "80" in gs:
            cycle, motion = None, None
        codes = [code for code in gs if code in _CYCLE_KINDS]
        if cycle is not None and (codes or [code for code in gs if code not in ("98", "99")] or ms
                                  or not ("X" in num or "Y" in num) or "Z" in num):
            raise ValueError(f"{where}: a canned cycle is still modal; only a bare X / Y repeats it, so end it "
                             "with G80 first")
        if "20" in gs:
            scale = 25.4
        if "21" in gs:
            scale = 1.0
        if "S" in num:
            rpm = num["S"]
        if "F" in num:
            feed = num["F"] * scale
        for code in ms:
            if code in (3, 4):
                spindle, just_started = "on", True
            elif code in (5, 0, 6):
                spindle = "off"
            elif code == 19:
                spindle = "oriented"
        if 6 in ms or 0 in ms:
            segment += 1
            pos[:] = [None, None, None]
        if "91" in gs:
            if "28" not in gs:
                raise ValueError(f"{where}: incremental moves are not read")
            pos[2] = None
            continue
        if "53" in gs:
            for axis, letter in enumerate("XYZ"):
                if letter in num:
                    pos[axis] = None
            continue
        if "4" in gs:
            if just_started:
                just_started = False   # the spindle's spin-up wait, not a dwell of the toolpath's
                continue
            last = out[-1]
            out[-1] = Plotted(last.kind, last.xyz, last.feed, last.center, last.dwell_s + dwell_seconds(vals["P"]),
                              last.segment, last.spindle)
            continue
        if "98" in gs:
            return_r = False
        if "99" in gs:
            return_r = True
        target = [num[letter] * scale if letter in num else pos[axis] for axis, letter in enumerate("XYZ")]
        cuts = codes or "33.1" in gs or (any(code in gs for code in ("1", "2", "3"))
                                         and any(letter in num for letter in "XYZ"))
        if cuts and spindle != "on":
            raise ValueError(f"{where}: cuts while the spindle is {'stopped' if spindle == 'off' else spindle}")
        if "33.1" in gs:
            just_started = False
            top = tuple(pos)
            add("feed", (pos[0], pos[1], num["Z"] * scale), at_feed=num["K"] * scale * rpm)
            add("feed", top, at_feed=num["K"] * scale * rpm)
            continue
        if codes:
            initial = pos[2]
            cycle = {"kind": _CYCLE_KINDS[codes[0]]}
            for letter in "ZRQIJ":
                if letter in num:
                    cycle[letter] = num[letter] * scale
            cycle["P"] = dwell_seconds(vals["P"]) if "P" in vals else 0.0
            cycle["F"] = feed
            motion = "cycle"
        elif any(code in gs for code in ("0", "1", "2", "3")):
            motion = next(code for code in gs if code in ("0", "1", "2", "3"))
        if not any(letter in num for letter in "XYZ"):
            continue
        just_started = False
        if motion == "cycle":
            kind = cycle["kind"]
            shift, shift_xy = cycle.get("Q"), None
            if kind in ("fine_bore", "back_bore") and ("I" in cycle or "J" in cycle):
                shift_xy = (cycle.get("I", 0.0), cycle.get("J", 0.0))
                shift = math.hypot(*shift_xy)
            built = Cycle(kind, cycle["R"], cycle["Z"],
                          peck_mm=cycle.get("Q") if kind in ("peck", "chip_break") else None,
                          pitch_mm=cycle["F"] / rpm if kind in ("tap", "rigid_tap") else None,
                          shift_mm=shift if kind in ("fine_bore", "back_bore") else None,
                          dwell_s=cycle["P"], shift_xy=shift_xy)
            end_z = cycle["R"] if return_r else initial
            move = Move("cycle", (target[0], target[1], end_z), cycle["F"], cycle=built)
            for part in expand_cycle(move, tuple(pos)):
                add(_plain(part), part.xyz, at_feed=part.feed, dwell=part.dwell_s, state="cycle")
        elif motion in ("2", "3"):
            center = (pos[0] + num.get("I", 0.0) * scale, pos[1] + num.get("J", 0.0) * scale)
            add("arc_cw" if motion == "2" else "arc_ccw", target, center=center, at_feed=feed)
        elif motion in ("0", "1"):
            add("rapid" if motion == "0" else "feed", target, at_feed=None if motion == "0" else feed)
        else:
            raise ValueError(f"{where}: a move with no motion mode")
    _check_shifts(out)
    return out


def _check_shifts(out: list[Plotted]) -> None:
    """A boring bar's shift (a short sideways rapid, then along Z, then back to where it was) is made with the
    spindle oriented, as is any sideways rapid straight after a cut that went down or across (the tool is still at
    the bottom of its hole; a cut up and out, a ream's, is not)."""
    def xy(move):
        return move.xyz[0], move.xyz[1]

    def sideways(a, b) -> float:
        if None in (*a, *b):
            return math.inf
        return math.hypot(b[0] - a[0], b[1] - a[1])

    for index in range(1, len(out)):
        before, move = out[index - 1], out[index]
        if move.kind != "rapid" or move.spindle in ("oriented", "cycle") or before.segment != move.segment:
            continue
        across = sideways(xy(before), xy(move))
        if not 1e-9 < across < math.inf:
            continue
        came_from = out[index - 2].xyz[2] if index >= 2 else None
        at_bottom = came_from is None or before.xyz[2] is None or before.xyz[2] <= came_from + 1e-9
        if before.kind != "rapid" and before.spindle != "cycle" and at_bottom:
            raise ValueError(f"a sideways rapid to {move.xyz} straight after a cut, with the spindle {move.spindle}: "
                             "a boring bar shifts off the wall only when oriented (M19)")
        if index + 2 < len(out) and across <= _SHIFT_MM:
            up, back = out[index + 1], out[index + 2]
            if (up.kind == back.kind == "rapid" and sideways(xy(move), xy(up)) < 1e-9
                    and sideways(xy(back), xy(before)) < 1e-9):
                raise ValueError(f"a boring bar's shift to {move.xyz} with the spindle {move.spindle}, not oriented "
                                 "(M19)")


def planned_moves(toolpaths) -> list[Plotted]:
    """The toolpaths' moves as a back-plot reads them: cycles expanded, plunges and leads as feeds."""
    from cadgen._internal.cam.toolpath import expand_cycle, walk

    out = []
    for toolpath in toolpaths:
        for start, move in walk(toolpath.moves, toolpath.start):
            parts = expand_cycle(move, start) if move.kind == "cycle" else (move,)
            for part in parts:
                out.append(Plotted(_plain(part), part.xyz, part.feed, part.center, part.dwell_s))
    return out


def assert_backplot(case: unittest.TestCase, text: str, post: str, toolpaths, *, tol: float = 1e-3,
                    feed_tol: float = 0.06) -> None:
    """The program's back-plot is the toolpaths: per tool, after the rapids that bring the tool to its toolpath's
    start, the same moves within ``tol`` mm (feeds within ``feed_tol`` mm/min), then only rapids (the retract)."""
    segments: dict[int, list[Plotted]] = {}
    for move in backplot(text, post):
        segments.setdefault(move.segment, []).append(move)
    runs: list[list] = []
    for toolpath in toolpaths:
        if runs and runs[-1][-1].tool == toolpath.tool:
            runs[-1].append(toolpath)
        else:
            runs.append([toolpath])
    got_segments = [moves for moves in segments.values() if moves]
    case.assertEqual(len(got_segments), len(runs), f"{post}: one stretch of moves per tool")

    def close(a, b) -> bool:
        return all(x is not None and abs(x - y) <= tol for x, y in zip(a, b))

    for moves, run in zip(got_segments, runs):
        start = run[0].start
        k = next((index for index, move in enumerate(moves) if move.kind == "rapid" and close(move.xyz, start)), None)
        case.assertIsNotNone(k, f"{post}: the tool never reaches {start}")
        case.assertTrue(all(move.kind == "rapid" for move in moves[:k]), f"{post}: only rapids bring the tool to its start")
        rest, want = moves[k + 1:], planned_moves(run)
        case.assertGreaterEqual(len(rest), len(want), f"{post}: {len(rest)} moves, planned {len(want)}")
        for index, (got, expected) in enumerate(zip(rest, want)):
            label = f"{post} {run[0].tool} move {index}: got {got}, planned {expected}"
            case.assertEqual(got.kind, expected.kind, label)
            case.assertTrue(close(got.xyz, expected.xyz), label)
            if expected.center is not None:
                case.assertTrue(close(got.center, expected.center), label)
            if expected.feed is None:
                case.assertIsNone(got.feed, label)
            else:
                case.assertLessEqual(abs(got.feed - expected.feed), feed_tol, label)
            case.assertAlmostEqual(got.dwell_s, expected.dwell_s, delta=1e-3, msg=label)
        case.assertTrue(all(move.kind == "rapid" for move in rest[len(want):]), f"{post}: only rapids after the cut")


# --- Sweeping a toolpath -------------------------------------------------------------------------------
# Points along a toolpath's moves, for the checks the operation suites make with geometry2d.intrusion.

def path_points(toolpath, step: float = 0.5) -> list[tuple[float, float, float]]:
    """Points along every move of ``toolpath`` (cycles expanded), no further apart than ``step`` mm, each move's
    end included; arcs and helices along their curve."""
    from cadgen._internal.cam.toolpath import arc_sweep, expand_cycle, walk

    out = [tuple(toolpath.start)]
    for start, move in walk(toolpath.moves, toolpath.start):
        parts = expand_cycle(move, start) if move.kind == "cycle" else (move,)
        for here, part in walk(parts, start):
            if part.kind in ("arc_cw", "arc_ccw"):
                cx, cy = part.center
                radius = math.hypot(here[0] - cx, here[1] - cy)
                a0 = math.atan2(here[1] - cy, here[0] - cx)
                sweep = arc_sweep(here, part)
                count = max(2, math.ceil(abs(sweep) * radius / step))
                for k in range(1, count + 1):
                    f = k / count
                    out.append((cx + radius * math.cos(a0 + sweep * f), cy + radius * math.sin(a0 + sweep * f),
                                here[2] + (part.xyz[2] - here[2]) * f))
            else:
                count = max(1, math.ceil(math.dist(here, part.xyz) / step))
                out += [tuple(here[n] + (part.xyz[n] - here[n]) * k / count for n in range(3)) for k in range(1, count + 1)]
    return out


def swept_at(points, z: float, radius: float):
    """The area a tool of ``radius`` sweeps at height ``z``: round each run of ``points`` that stays at ``z``."""
    from shapely.geometry import LineString, Point
    from shapely.ops import unary_union

    runs, run = [], []
    for point in points:
        if abs(point[2] - z) < 1e-6:
            run.append(point[:2])
        else:
            if run:
                runs.append(run)
            run = []
    if run:
        runs.append(run)
    shapes = [LineString(r) if len(r) > 1 else Point(r[0]) for r in runs]
    return unary_union([shape.buffer(radius, quad_segs=64) for shape in shapes])


# --- Gouges and descents, measured independently of the planner ---------------------------------------
# brep_intrusion asks the kernel (BRepExtrema_DistShapeShape) how far the tool's cutting envelope enters the part's
# B-rep; descents_ok checks that every steep descent ends in air or in material an earlier cut already took away.

#: Segments per quarter circle where a tool's outline curves (a ball's end, a bull's corner).
_ARC_STEPS = 64


def _tool_outline(profile) -> list[tuple[float, float]]:
    """The tool's cutting envelope as ``(r, z)`` from the axis at the tip, round its side, to the axis at the top of
    its cutting length; arcs sampled finely (inside the true arc, so it never reads a gouge that is not there)."""
    top = profile.cutting_length_mm
    points = [(0.0, 0.0)]
    for section in profile.sections:
        if section.z0 > top + 1e-12:
            break
        if section.arc is not None:
            zc, rc, radius = section.arc
            a0 = math.asin(max(-1.0, min(1.0, (section.z0 - zc) / radius)))
            a1 = math.asin(max(-1.0, min(1.0, (min(section.z1, top) - zc) / radius)))
            steps = [(rc + radius * math.cos(a0 + (a1 - a0) * k / _ARC_STEPS),
                      zc + radius * math.sin(a0 + (a1 - a0) * k / _ARC_STEPS)) for k in range(_ARC_STEPS + 1)]
        else:
            steps = [(section.r0, section.z0), (section.radius(min(section.z1, top)), min(section.z1, top))]
        points += [p for p in steps if p != points[-1]]
    if points[-1][1] < top:
        points.append((points[-1][0], top))
    points.append((0.0, top))
    return points


class _ToolSolids:
    """The pieces a toolpath's swept cutting envelope is made of, shrunk by ``shrink`` mm all round (so touching
    is not reading as entering): the tool at a point, a straight level move as a prism of the tool's cross-section,
    a level arc as that cross-section turned about the arc's centre; and a ``rise``: what a move that changes height
    sweeps above the top of the cutting length at its lowest point, a column of the tool's top radius ``rise`` tall
    over a level piece (the cutter's radius never shrinks going up its cutting length, so the level piece at the
    lowest point and that column hold everything the move sweeps)."""

    def __init__(self, profile):
        from shapely.geometry import Polygon

        half = _tool_outline(profile)
        self.width = max(r for r, _ in half)
        self.top = half[-1][1]
        self.top_radius = max(r for r, z in half if abs(z - self.top) < 1e-12)
        self.full = Polygon([(r, z) for r, z in half] + [(-r, z) for r, z in reversed(half)]).buffer(0)
        self._cache: dict = {}

    def _section(self, shrink: float, rise: float = 0.0):
        """The cross-section through the axis, shrunk: a shapely polygon in (u, z), u across the axis; or None. With
        ``rise``, the column over the cutting length instead."""
        key = ("section", shrink, rise)
        if key not in self._cache:
            if rise:
                from shapely.geometry import box

                whole = box(-self.top_radius, self.top, self.top_radius, self.top + rise)
            else:
                whole = self.full
            section = whole.buffer(-shrink, join_style="mitre") if shrink > 0 else whole
            polys = [section] if section.geom_type == "Polygon" else list(getattr(section, "geoms", ()))
            polys = [poly for poly in polys if poly.area > 1e-9]
            self._cache[key] = max(polys, key=lambda poly: poly.area) if polys else None
        return self._cache[key]

    @staticmethod
    def _face(points):
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
        from OCP.gp import gp_Pnt

        polygon = BRepBuilderAPI_MakePolygon()
        for x, y, z in points:
            polygon.Add(gp_Pnt(x, y, z))
        polygon.Close()
        return BRepBuilderAPI_MakeFace(polygon.Wire(), True).Face()

    @staticmethod
    def _placed(shape, angle: float, at):
        from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
        from OCP.TopLoc import TopLoc_Location

        turn, move = gp_Trsf(), gp_Trsf()
        turn.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), angle)
        move.SetTranslation(gp_Vec(*at))
        return shape.Moved(TopLoc_Location(move.Multiplied(turn)))

    def tool(self, shrink: float, rise: float = 0.0):
        """The tool at the origin (tip there, axis +Z), shrunk (with ``rise``, its column); ``None`` when nothing is
        left."""
        key = ("tool", shrink, rise)
        if key not in self._cache:
            from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol
            from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt
            from shapely.geometry import box

            section = self._section(shrink, rise)
            half = None if section is None else section.intersection(box(0, -1e3, 1e3, 1e3))
            if half is None or half.is_empty or half.geom_type != "Polygon":
                self._cache[key] = None
            else:
                face = self._face([(u, 0.0, z) for u, z in list(half.exterior.coords)[:-1]])
                self._cache[key] = BRepPrimAPI_MakeRevol(face, gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1))).Shape()
        return self._cache[key]

    def piece(self, spec, shrink: float, rise: float = 0.0):
        """One piece, from its spec: ``("at", xyz)``, ``("line", a, b)`` (level), ``("arc", a, centre, sweep)``,
        ``("rise", height, level spec)`` (the column over that level piece) or ``("bound", ...)``."""
        from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism, BRepPrimAPI_MakeRevol
        from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Vec

        if spec[0] == "bound":
            return _compound([self.piece(item, shrink) for item in spec[1:2] + spec[3:]])
        if spec[0] == "rise":
            return self.piece(spec[2], shrink, spec[1])
        if spec[0] == "at":
            tool = self.tool(shrink, rise)
            return None if tool is None else self._placed(tool, 0.0, spec[1])
        section = self._section(shrink, rise)
        if section is None:
            return None
        coords = list(section.exterior.coords)[:-1]
        if spec[0] == "line":
            _, a, b = spec
            length = math.dist(a[:2], b[:2])
            face = self._face([(0.0, u, z) for u, z in coords])
            prism = BRepPrimAPI_MakePrism(face, gp_Vec(length, 0, 0)).Shape()
            return self._placed(prism, math.atan2(b[1] - a[1], b[0] - a[0]), a)
        _, a, centre, sweep = spec
        radius = math.hypot(a[0] - centre[0], a[1] - centre[1])
        start = math.atan2(a[1] - centre[1], a[0] - centre[0]) + min(sweep, 0.0)
        if radius < self.width:
            # Tighter than the tool: what lies past the arc's centre is swept by the tool's other side, so the
            # cross-section is cut there (its turned sector and the tool at both ends are then the swept volume).
            from shapely.geometry import box

            clipped = section.intersection(box(-radius, -1e3, 1e3, 1e3))
            if clipped.is_empty or clipped.geom_type != "Polygon":
                return None
            coords = list(clipped.exterior.coords)[:-1]
        face = self._face([(radius + u, 0.0, z) for u, z in coords])
        turned = BRepPrimAPI_MakeRevol(face, gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), abs(sweep)).Shape()
        return self._placed(turned, start, (centre[0], centre[1], a[2]))


def _pieces(toolpath, solids: _ToolSolids, step: float) -> list:
    """The specs of the pieces a toolpath sweeps: level lines and arcs whole (the tool itself where one ends and the
    next does not carry straight on), every other move sampled ``step`` apart."""
    from cadgen._internal.cam.toolpath import arc_sweep, expand_cycle, walk

    def heading(here, move, at_end: bool):
        if move.kind in ("arc_cw", "arc_ccw"):
            cx, cy = move.center
            p = move.xyz if at_end else here
            sign = 1.0 if move.kind == "arc_ccw" else -1.0
            return (-(p[1] - cy) * sign, (p[0] - cx) * sign)
        return (move.xyz[0] - here[0], move.xyz[1] - here[1])

    flat = []
    for start, move in walk(toolpath.moves, toolpath.start):
        parts = expand_cycle(move, start) if move.kind == "cycle" else (move,)
        flat += list(walk(parts, start))
    specs = [("at", tuple(float(c) for c in toolpath.start))]
    for index, (here, part) in enumerate(flat):
        end = tuple(part.xyz)
        level = abs(end[2] - here[2]) < 1e-9
        piece = None
        if level and part.kind in ("arc_cw", "arc_ccw"):
            piece = ("arc", tuple(here), tuple(part.center), arc_sweep(here, part))
        elif level and math.dist(here[:2], end[:2]) > 1e-9:
            piece = ("line", tuple(here), end)
        if piece is None:
            # A move that changes height: first as its level counterpart at its lowest point (all it sweeps, below
            # the top of the cutting length there) with the column of the tool's top radius over it (all it sweeps
            # above that), sampled only where those touch the part.
            low = min(here[2], end[2])
            a, b = (here[0], here[1], low), (end[0], end[1], low)
            if part.kind in ("arc_cw", "arc_ccw"):
                bound = ("arc", a, tuple(part.center), arc_sweep(here, part))
            elif math.dist(a[:2], b[:2]) > 1e-9:
                bound = ("line", a, b)
            else:
                bound = ("at", a)
            samples = [("at", point) for point in _along_move(here, part, step)]
            # Above the top of its cutting length there, the column of the tool's top radius over the same path.
            under = [bound] if bound[0] == "at" else [bound, ("at", a), ("at", b)]
            rise = [("rise", abs(end[2] - here[2]), item) for item in under]
            specs.append(("bound", bound, samples, ("at", a), ("at", b), *rise))
            continue
        specs.append(piece)
        following = flat[index + 1] if index + 1 < len(flat) else None
        if following is not None and abs(following[1].xyz[2] - end[2]) < 1e-9:
            a, b = heading(here, part, True), heading(following[0], following[1], False)
            size = math.hypot(*a) * math.hypot(*b)
            if size > 0 and (a[0] * b[0] + a[1] * b[1]) / size > math.cos(math.radians(1.0)):
                continue   # carries straight on: the next piece starts with the tool's whole cross-section here
        specs.append(("at", end))
    return specs


def _along_move(here, move, step: float) -> list[tuple[float, float, float]]:
    """Points along one move after its start, no further apart than ``step`` mm, its end included."""
    from cadgen._internal.cam.toolpath import arc_sweep

    if move.kind in ("arc_cw", "arc_ccw"):
        cx, cy = move.center
        radius = math.hypot(here[0] - cx, here[1] - cy)
        a0 = math.atan2(here[1] - cy, here[0] - cx)
        sweep = arc_sweep(here, move)
        count = max(2, math.ceil(math.hypot(abs(sweep) * radius, move.xyz[2] - here[2]) / step))
        return [(cx + radius * math.cos(a0 + sweep * k / count), cy + radius * math.sin(a0 + sweep * k / count),
                 here[2] + (move.xyz[2] - here[2]) * k / count) for k in range(1, count + 1)]
    count = max(1, math.ceil(math.dist(here, move.xyz) / step))
    return [tuple(here[n] + (move.xyz[n] - here[n]) * k / count for n in range(3)) for k in range(1, count + 1)]


def _compound(shapes):
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    shapes = [shape for shape in shapes if shape is not None]
    if not shapes:
        return None
    compound, builder = TopoDS_Compound(), BRep_Builder()
    builder.MakeCompound(compound)
    for shape in shapes:
        builder.Add(compound, shape)
    return compound


def _distance(part_shape, shapes) -> float:
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape

    compound = _compound(shapes)
    if compound is None:
        return math.inf
    measure = BRepExtrema_DistShapeShape(part_shape, compound)
    if not measure.IsDone():
        raise RuntimeError("the kernel could not measure the tool against the part")
    return 0.0 if measure.InnerSolution() else measure.Value()


def brep_intrusion(part_shape, toolpath, profile, step: float = 0.5, *, probe: float = 0.005) -> float:
    """How far (mm) the tool's cutting envelope (``profile``, up its cutting length) enters ``part_shape`` (the part's
    B-rep in the setup's frame) anywhere along ``toolpath``, read by the kernel (BRepExtrema_DistShapeShape): level
    lines and arcs as their exact swept solids, every other move as the tool at points ``step`` mm apart (tried
    first as its level counterpart at its lowest point with the column of its top radius above that, which together
    sweep all it does).

    The envelope is shrunk by ``probe`` all round first, so a tool that only touches (a finished wall, a floor) reads
    as clear: the answer is then ``probe`` less the clearance the kernel found (at most ``probe``). Where the shrunk
    envelope still meets the part, the pieces that meet it are shrunk further until none does (bisection, to 1 um):
    the depth of the deepest point of the part inside the tool (from the tool's side or its bottom, whichever is
    nearer), at most the tool's radius.
    """
    solids = _ToolSolids(profile)
    specs = _pieces(toolpath, solids, step)

    def touching(chunk: list) -> list:
        if _distance(part_shape, [solids.piece(spec, probe) for spec in chunk]) > 1e-9:
            return []
        if len(chunk) == 1:
            return touching(chunk[0][2]) if chunk[0][0] == "bound" else chunk
        middle = len(chunk) // 2
        return touching(chunk[:middle]) + touching(chunk[middle:])

    clearance = _distance(part_shape, [solids.piece(spec, probe) for spec in specs])
    if clearance > 1e-9:
        return probe - clearance
    found = touching(specs)
    low, high = probe, solids.width
    while high - low > 1e-3:
        middle = (low + high) / 2
        if _distance(part_shape, [solids.piece(spec, middle) for spec in found]) > 1e-9:
            high = middle
        else:
            low = middle
    return high


def _stock_shadow(ctx, z: float):
    """The stock at height ``z`` or above it (setup frame), as shapely: what a tool coming down to ``z`` meets. Above
    the material's top (``ctx.top_z``: an earlier facing took the rest off), nothing."""
    from shapely.ops import unary_union

    from cadgen._internal.cam.operations.geometry2d import stock_outline

    top = ctx.setup.stock_top_z
    if z >= ctx.top_z - 1e-9:
        return None
    key = ("test_stock_shadow", round(z, 4))
    if key not in ctx.cache:
        count = max(2, math.ceil(top - z) + 1)
        heights = [z + (top - z) * k / count for k in range(count + 1)]
        if ctx.stock.type == "cylinder":
            (x0, y0, z0), (x1, y1, z1) = ctx.stock.bounds
            middle = ctx.to_setup(((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2))[2]
            if z < middle < top:
                heights.append(middle)
        ctx.cache[key] = unary_union([stock_outline(ctx, h) for h in heights])
    return ctx.cache[key]


def descents_ok(toolpath, ctx, angle_deg: float | None = None, *, excuse=None) -> list[str]:
    """The descents in ``toolpath`` that go into material too steeply: every move down (a rapid, a plunge, a feed or
    a helix's arc; a canned cycle's own moves too, expanded) steeper than ``angle_deg`` (the operation's
    ``ramp_angle_deg``, else the default ramp angle) must end where the tool's whole circle is in air (off the stock
    standing at that height or above it) or in material an earlier cut of the same toolpath already took away (swept
    by the tool at that height or lower). ``excuse(here, move, cycle, cut)`` may pass a descent that is sound for a
    reason of its own (``cycle``: the canned cycle move it is part of, else ``None``; ``cut``: the segments cut so
    far); :func:`assert_sound` gives the only ones there are. Empty when every descent is fine; else one line per
    offending move."""
    import numpy as np
    from shapely.geometry import MultiLineString, Point

    from cadgen._internal.cam.operations.emit import DEFAULT_RAMP_DEG
    from cadgen._internal.cam.toolpath import arc_sweep, expand_cycle, walk

    if angle_deg is None:
        angle_deg = getattr(ctx.params, "ramp_angle_deg", DEFAULT_RAMP_DEG)
    slope = math.tan(math.radians(angle_deg)) * (1 + 1e-3)   # arc fitting may shorten a ramp's run a hair
    r, tol = ctx.radius, ctx.tol
    cut: list[tuple[tuple, tuple]] = []
    problems = []

    def cleared(p) -> bool:
        material = _stock_shadow(ctx, p[2])
        circle = Point(p[0], p[1]).buffer(r - tol, quad_segs=32)
        if material is None:
            return True
        left = circle.intersection(material)
        if left.area < 1e-6:
            return True
        if not cut:
            return False
        segs = np.asarray(cut, dtype=float)
        low = np.maximum(segs[:, 0, 2], segs[:, 1, 2]) <= p[2] + 1e-6
        a, b = segs[:, 0, :2], segs[:, 1, :2]
        ab = b - a
        span = np.maximum((ab * ab).sum(axis=1), 1e-18)
        t = np.clip(((np.asarray(p[:2]) - a) * ab).sum(axis=1) / span, 0.0, 1.0)
        near = np.hypot(*(a + ab * t[:, None] - np.asarray(p[:2])).T) <= 2 * r
        chosen = segs[low & near]
        if not len(chosen):
            return False
        lines = [[tuple(s[0, :2]), tuple(s[1, :2] if np.hypot(*(s[1, :2] - s[0, :2])) > 1e-9 else s[0, :2] + 1e-6)]
                 for s in chosen]
        swept = MultiLineString(lines).buffer(r, quad_segs=32)
        return left.difference(swept).area < 1e-6

    flat = []
    for start, move in walk(toolpath.moves, toolpath.start):
        if move.kind == "cycle":
            flat += [(here, part, move) for here, part in walk(expand_cycle(move, start), start)]
        else:
            flat.append((start, move, None))
    for here, move, cycle in flat:
        end = tuple(move.xyz)
        if move.kind in ("arc_cw", "arc_ccw"):
            across = abs(arc_sweep(here, move)) * math.hypot(here[0] - move.center[0], here[1] - move.center[1])
            points = [tuple(here)] + _along_move(here, move, 0.25)
        else:
            across = math.dist(here[:2], end[:2])
            points = [tuple(here), end]
        drop = here[2] - end[2]
        if drop > 1e-9 and (move.kind == "rapid" or drop > slope * across) and not cleared(end) and \
                not (excuse is not None and excuse(tuple(here), move, cycle, cut)):
            problems.append(f"{move.kind} down to ({end[0]:g}, {end[1]:g}, {end[2]:g}) from z {here[2]:g} goes into "
                            "material")
        if move.kind != "rapid":
            cut += [(tuple(a), tuple(b)) for a, b in zip(points, points[1:])]
    return problems


# --- Soundness: what every planned toolpath must be ----------------------------------------------------
# assert_sound runs the independent checks above on each toolpath an operation plans (its own tool's, and any it
# runs before with another tool). What they excuse is written here, each with the reason it is sound, and nothing
# else: no tolerance is widened for any operation.

#: The descents descents_ok passes for a reason of their own (:func:`_excuse`), and why each is sound.
EXCUSED_DESCENTS = {
    "canned_cycle": "a drilling operation's canned cycle goes down the tool's axis into its hole: that is what a "
                    "drill, tap, reamer or boring bar does, and the cycle's motion is the controller's",
    "v_carve": "a V-bit in engrave follows the carve down no steeper than its cone's slope, or drops straight onto a "
               "point it has already cut at least as deep: a pointed tool cutting on its centre, whose carve at each "
               "point is the deepest its cone may go there, so its cone higher up is inside the cut (edges_2d)",
    "thread_axis": "an internal thread_mill feeds down its hole's axis, the tool narrower than the hole, only inside "
                   "the hole an earlier operation made, which phase 1 has no stock simulation to see (holes_2d)",
}
#: Moves brep_intrusion does not sweep, and why.
UNSWEPT = {
    "oriented_bar": "an oriented boring bar's shift (fine_bore, back_bore) moves its one-sided edge off the wall or "
                    "passes its eccentric head through the hole; a round envelope cannot stand for that bar, so the "
                    "shifted moves are left out and the bar is checked on its axis, cutting",
}
#: What an operation is meant to cut out of the part as modelled (the model does not show it), by operation:
#: brep_intrusion measures against the part less exactly that volume, so the tool may go that far and no further.
INTENDED_CUTS = {
    "chamfer_2d": "the chamfer asked for along each top edge the part does not model as chamfered",
    "tap": "the thread: the tap's diameter over the length of a hole drilled at its tap drill size",
    "rigid_tap": "the thread: the tap's diameter over the length of a hole drilled at its tap drill size",
    "thread_mill": "the thread: thread_depth_mm into the hole's wall (or the boss's), over its length",
    "engrave": "a region carved into the part's surface: the region, down to its deepest carve or max_depth_mm",
    "trace": "a region's curve cut into the part's surface: the groove the tool's cutting radius makes along it on "
             "its offset side, depth_mm deep",
}


def assert_sound(case: unittest.TestCase, ctx, planned, *, slices: bool = False) -> None:
    """What every toolpath of ``planned`` must be: no gouge (read from the B-rep less the operation's
    :data:`INTENDED_CUTS`; from the slices too when ``slices``, which is cheap only where the plan sliced those heights
    already and exact only on straight walls: it reads a leaning wall up to a quantum lower), no steep descent into
    material (but :data:`EXCUSED_DESCENTS`), no sideways rapid under the retract plane, every move its operation's.
    A toolpath run with another tool (an engraving's clear_tool) is checked with that tool."""
    from cadgen._internal.cam.operations.geometry2d import intrusion
    from cadgen._internal.cam.toolpath import rapids_below

    for tp in planned.toolpaths:
        tool_ctx = toolpath_context(ctx, tp)
        with case.subTest(tool=tp.tool):
            if slices:
                case.assertLessEqual(intrusion(tool_ctx, path_points(tp)), ctx.tol)
            part = intended_part(tool_ctx, planned)
            for run in swept_runs(tp):
                case.assertLessEqual(brep_intrusion(part, run, tool_ctx.profile), ctx.tol)
            case.assertEqual(descents_ok(tp, tool_ctx, excuse=lambda *args: _excuse(tool_ctx, *args)), [])
            case.assertEqual(rapids_below(tp.moves, start=tp.start, z=ctx.heights.retract), [])
            case.assertEqual({move.op for move in tp.moves}, {ctx.index})


def toolpath_context(ctx, toolpath):
    """``ctx`` with the tool that runs ``toolpath`` (the operation's own, or another of the job's)."""
    from dataclasses import replace

    from cadgen._internal.cam.tools import profile

    if toolpath.tool == ctx.tool.id:
        return ctx
    tool = ctx.job.tool(toolpath.tool)
    return replace(ctx, tool=tool, profile=profile(tool))


def _excuse(ctx, here, move, cycle, cut) -> str | None:
    """The :data:`EXCUSED_DESCENTS` key that passes this descent, else ``None``."""
    from cadgen._internal.cam.operations import REGISTRY
    from cadgen._internal.cam.toolpath import arc_sweep

    op = ctx.operation.op
    if cycle is not None:
        return "canned_cycle" if REGISTRY[op].module.endswith(".drilling") else None
    end = tuple(move.xyz)
    vertical = math.dist(here[:2], end[:2]) < 1e-9
    if op == "engrave" and ctx.tool.type == "v_bit":
        if move.kind in ("feed", "arc_cw", "arc_ccw"):
            if move.kind == "feed":
                across = math.dist(here[:2], end[:2])
            else:
                across = abs(arc_sweep(here, move)) * math.hypot(here[0] - move.center[0], here[1] - move.center[1])
            half = math.radians(float(ctx.tool.size["tip_angle_deg"]) / 2)
            if here[2] - end[2] <= across / math.tan(half) * (1 + 1e-3):
                return "v_carve"
        if vertical and any(_on_segment(end, a, b) for a, b in cut):
            return "v_carve"
    if op == "thread_mill" and not ctx.params.external and vertical and move.kind != "rapid":
        for _, found in ctx.targets(("hole",)):
            if math.dist(found.top[:2], end[:2]) < 1e-6 and ctx.radius < found.feature.size["diameter_mm"] / 2:
                return "thread_axis"
    return None


def _on_segment(point, a, b) -> bool:
    """``point`` is on the cut from ``a`` to ``b`` (x, y within a micrometre), which went at least as deep there."""
    ax, ay, az = a
    dx, dy, dz = b[0] - ax, b[1] - ay, b[2] - az
    span = dx * dx + dy * dy
    t = 0.0 if span < 1e-18 else max(0.0, min(1.0, ((point[0] - ax) * dx + (point[1] - ay) * dy) / span))
    x, y, z = ax + dx * t, ay + dy * t, az + dz * t
    return math.hypot(point[0] - x, point[1] - y) < 1e-3 and z <= point[2] + 1e-6


def swept_runs(toolpath) -> list:
    """``toolpath`` as brep_intrusion sweeps it: whole, but for an oriented boring bar's cycles (:data:`UNSWEPT`),
    which are expanded with their shifted moves left out, the toolpath split where they were."""
    from dataclasses import replace

    from cadgen._internal.cam.toolpath import expand_cycle, walk

    if not any(move.kind == "cycle" and move.cycle.kind in ("fine_bore", "back_bore") for move in toolpath.moves):
        return [toolpath]
    runs, start, run = [], tuple(toolpath.start), []
    for here, move in walk(toolpath.moves, toolpath.start):
        if move.kind != "cycle" or move.cycle.kind not in ("fine_bore", "back_bore"):
            run.append(move)
            continue
        axis = move.xyz[:2]
        for index, (at, part) in enumerate(walk(expand_cycle(move, here), here)):
            off = math.dist(part.xyz[:2], axis) > 1e-9 or (index and math.dist(at[:2], axis) > 1e-9)
            if off:
                if run:
                    runs.append(replace(toolpath, start=start, moves=tuple(run)))
                run, start = [], tuple(part.xyz)
            else:
                run.append(part)
    if run:
        runs.append(replace(toolpath, start=start, moves=tuple(run)))
    return runs


def intended_part(ctx, planned):
    """The part as the operation is meant to leave it, in the setup's frame: ``ctx.part_shape`` less its
    :data:`INTENDED_CUTS` (the part itself for any other operation)."""
    op = ctx.operation.op
    if op not in INTENDED_CUTS:
        return ctx.part_shape
    key = ("test_intended_part", repr(ctx.operation))
    if key not in ctx.cache:
        if op == "chamfer_2d":
            ctx.cache[key] = _chamfered(ctx, planned.stats)
        else:
            ctx.cache[key] = _less(ctx.part_shape, _intended_solids(ctx))
    return ctx.cache[key]


def _intended_solids(ctx) -> list:
    """The solids :func:`intended_part` takes away, setup frame."""
    import shapely

    params, op = ctx.params, ctx.operation.op
    out = []
    if op in ("tap", "rigid_tap"):
        for found in ctx.features:
            if found.feature.kind == "hole" and found.feature.size["diameter_mm"] / 2 < ctx.radius - 1e-6:
                out.append(_cylinder(found.top[:2], ctx.radius, found.bottom[2] - 1.0, found.top[2]))
    elif op == "thread_mill":
        kind = "profile" if params.external else "hole"
        for ref, found in ctx.targets((kind,)):
            if params.external:
                wall = next(ctx.part.face(int(r.rsplit(".f", 1)[1])) for r in found.feature.refs
                            if ctx.part.face(int(r.rsplit(".f", 1)[1])).surface == "cylinder")
                (cx, cy, _), radius = ctx.to_setup(wall.axis_point), wall.radius
                ring = _less(_cylinder((cx, cy), radius + 1.0, found.bottom[2], found.top[2]),
                             [_cylinder((cx, cy), radius - params.thread_depth_mm, found.bottom[2] - 1.0,
                                        found.top[2] + 1.0)])
                out.append(ring)
            else:
                radius = found.feature.size["diameter_mm"] / 2 + params.thread_depth_mm
                out.append(_cylinder(found.top[:2], radius, found.bottom[2] - 1.0, found.top[2]))
    elif op == "engrave" and ctx.operation.region is not None:
        poly = shapely.Polygon(ctx.operation.region)
        v_bit = ctx.job.tool(ctx.operation.tool)        # the clear_tool's pass is checked against the same carve
        half = math.radians(float(v_bit.size["tip_angle_deg"]) / 2)
        tip = float(v_bit.size.get("tip_diameter_mm", 0.0)) / 2
        inscribed = shapely.maximum_inscribed_circle(poly, ctx.tol / 4).length
        depth = max(0.0, inscribed - tip) / math.tan(half)
        if params.max_depth_mm is not None:
            depth = min(depth, params.max_depth_mm)
        out.append(_prism(poly, ctx.top_z - depth, ctx.top_z + 100.0))
    elif op == "trace" and ctx.operation.region is not None:
        poly = shapely.Polygon(ctx.operation.region)
        reach = ctx.profile.radius_at(params.depth_mm)
        groove = {"none": lambda: poly.boundary.buffer(reach, quad_segs=64),
                  "inside": lambda: poly.difference(poly.buffer(-2 * reach, quad_segs=64)),
                  "outside": lambda: poly.buffer(2 * reach, quad_segs=64).difference(poly)}[params.offset]()
        out.append(_prism(groove, ctx.top_z - params.depth_mm, ctx.top_z + 100.0))
    return out


def _chamfered(ctx, stats):
    """The part with the chamfers of a chamfer_2d's ``stats`` that it does not model, in the setup's frame."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer

    from cadgen._internal.cam.part import same
    from cadgen._internal.cam.stock import occ_shape

    part = ctx.part
    direction = tuple(float(c) for c in ctx.setup.direction)
    half = math.radians(float(ctx.tool.size["tip_angle_deg"]) / 2)
    maker = BRepFilletAPI_MakeChamfer(occ_shape(part.shape))
    added = 0
    for edge in stats["edges"]:
        if edge["modelled"]:
            continue
        found = next(f for f in ctx.features if f.feature.ref == edge["ref"] or edge["ref"] in f.feature.refs)
        walls = [int(r.rsplit(".f", 1)[1]) for r in found.feature.refs]
        walls = [w for w in walls if part.is_wall(w, direction)]
        for wall in walls:
            for ordinal in sorted(part.face(wall).edges):
                others = [f for f in part._edge_faces[ordinal] if f != wall]
                top = next((part.face(f) for f in others if part.face(f).surface == "plane"
                            and same(part.face(f).normal, direction)), None)
                if top is None:
                    continue
                curve = BRepAdaptor_Curve(part.edge(ordinal))
                middle = curve.Value((curve.FirstParameter() + curve.LastParameter()) / 2)
                if abs(ctx.to_setup((middle.X(), middle.Y(), middle.Z()))[2] - edge["top_z"]) > 1e-3:
                    continue
                maker.Add(edge["width_mm"], edge["width_mm"] / math.tan(half), part.edge(ordinal), top.face)
                added += 1
    if not added:
        return ctx.part_shape
    maker.Build()
    if not maker.IsDone():
        raise RuntimeError("the kernel could not chamfer the part's edges")
    return ctx.setup.frame.shape_to_setup(maker.Shape())


def _cylinder(xy, radius: float, z0: float, z1: float):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(xy[0], xy[1], z0), gp_Dir(0, 0, 1)), radius, z1 - z0).Shape()


def _prism(area, z0: float, z1: float):
    """A shapely area (holes allowed) stood up from ``z0`` to ``z1``, as an OCP solid (a compound of several)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Pnt, gp_Vec
    from shapely.geometry.polygon import orient

    from cadgen._internal.cam.operations.geometry2d import polygons

    def wire(ring):
        polygon = BRepBuilderAPI_MakePolygon()
        for x, y in list(ring.coords)[:-1]:
            polygon.Add(gp_Pnt(x, y, z0))
        polygon.Close()
        return polygon.Wire()

    solids = []
    for poly in polygons(area):
        poly = orient(poly, 1.0)
        maker = BRepBuilderAPI_MakeFace(wire(poly.exterior), True)
        for interior in poly.interiors:
            maker.Add(wire(interior))
        solids.append(BRepPrimAPI_MakePrism(maker.Face(), gp_Vec(0, 0, z1 - z0)).Shape())
    return _compound(solids)


def _less(shape, tools):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

    for tool in tools:
        if tool is None:
            continue
        cut = BRepAlgoAPI_Cut(shape, tool)
        if not cut.IsDone():
            raise RuntimeError("the kernel could not take the intended cut out of the part")
        shape = cut.Shape()
    return shape
