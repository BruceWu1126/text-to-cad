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
