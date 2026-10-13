"""The toolpath: the one format every operation emits and every post reads.

A toolpath is a sequence of :class:`Move` in the setup's frame (mm, the tool along +Z until phase 4). A move
knows where it ends, not where it starts: its start is the previous move's end, or the toolpath's ``start`` for the
first. Every function here that measures moves takes that ``start``.

- ``rapid``: positioning at the machine's rapid rate (G0); ``feed`` is ``None``.
- ``feed``: a straight cut at ``feed`` mm/min (G1).
- ``plunge``: a straight cut down the tool axis into material (G1, at the plunge feed).
- ``lead``: a straight cut into or out of the cut proper (G1); its own kind so the viewer can tell it apart.
- ``arc_cw`` / ``arc_ccw``: an arc in the XY plane round ``center`` (G2 / G3), looking down the tool. Z may
  change along it (a helix, linear in the angle). An arc sweeps less than a full turn: a full turn is two arcs.
- ``cycle``: a canned drilling cycle (G81 ... G87) at the hole ``xyz[:2]``; ``cycle`` holds its parameters and
  ``xyz[2]`` is the height it returns to: its R plane (G99) or the height it started from (G98). The cycle may
  say which (``Cycle.return_to``); :func:`cycle_return` gives it either way. Before cutting, the tool rapids
  across at the height it starts from to above the hole: :func:`rapids_below` counts that move.
  :func:`expand_cycle` gives the plain moves it stands for, for a post that has no such cycle, for timing and for
  the back-plot.

Any move but a cycle may carry ``dwell_s``: a pause at its end (G4). ``t`` is the cumulative time at the end of the
move, in seconds, filled by :func:`timed`.

Coordinates, centres and feeds are rounded to a nanometre when a move is made (and -0.0 written 0.0), so the same
toolpath has the same repr and the same hash everywhere.

Linking (:func:`link`) moves the tool from the end of one cut to the start of the next, and is the one place that
decides how high the tool travels. It never rapids sideways below the retract plane: the only rapid below it is
straight down onto a height the caller declares clear.

Stdlib only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Iterable, Iterator, Sequence

__all__ = [
    "ARC_RADIUS_TOL_MM", "CYCLE_KINDS", "CHIP_BREAK_MM", "Cycle", "Heights", "Helix", "Lengths", "MOVE_KINDS", "Move",
    "PECK_CLEARANCE_MM", "PLUNGE", "Plunge", "RETURN_TO", "Ramp", "Toolpath", "arc_sweep", "bounds", "check",
    "cycle_return", "densify", "expand_cycle", "fit_arcs", "lengths", "link", "move_length", "move_seconds",
    "rapids_below", "sample_curve", "timed", "walk",
]

MOVE_KINDS = ("rapid", "feed", "plunge", "arc_cw", "arc_ccw", "lead", "cycle")
ARC_KINDS = ("arc_cw", "arc_ccw")
#: Canned cycles, by what they do (the G-code each is usually written as).
CYCLE_KINDS = (
    "drill",        # G81: feed in, rapid out
    "spot",         # G82: feed in, dwell, rapid out
    "peck",         # G83: pecks, rapid out to R between each
    "chip_break",   # G73: pecks, a short retract between each
    "tap",          # G84: feed in, spindle reverses, feed out
    "rigid_tap",    # G84 with the spindle synchronised (M29 / G84.2)
    "ream",         # G85 (G89 with a dwell): feed in, feed out
    "bore",         # G86: feed in, spindle stops, rapid out
    "fine_bore",    # G76: feed in, spindle orients, tool shifts off the wall, rapid out
    "back_bore",    # G87: shift, rapid through the hole, shift back, feed up, shift, rapid out
)
#: The cycle parameters each kind takes beyond R and Z (``dwell_s`` is optional where taken).
_CYCLE_NEEDS = {"peck": ("peck_mm",), "chip_break": ("peck_mm",), "tap": ("pitch_mm",), "rigid_tap": ("pitch_mm",),
                "fine_bore": ("shift_mm",), "back_bore": ("shift_mm",)}
_CYCLE_DWELLS = frozenset(CYCLE_KINDS) - {"drill"}

#: What a cycle returns to: the height it started from (G98) or its R plane (G99).
RETURN_TO = ("initial", "r")

#: How close a peck drill comes back down above the last peck's bottom before feeding again (G83's "d", a
#: controller setting), and how far a chip-breaking cycle backs off between pecks (G73's "d"), mm.
PECK_CLEARANCE_MM = 0.5
CHIP_BREAK_MM = 0.2
#: An arc's start and end must sit this close to the same radius round its centre, mm.
ARC_RADIUS_TOL_MM = 0.002

_DIGITS = 6           # coordinates and feeds to a nanometre (mm), times to a microsecond
_EPS = 1e-9

Point = tuple[float, float, float]


def _snap(value, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{where}: expected a finite number, got {value!r}")
    return round(float(value), _DIGITS) + 0.0


def _point(value, size: int, where: str) -> tuple[float, ...]:
    if not isinstance(value, (tuple, list)) or len(value) != size:
        raise ValueError(f"{where}: expected {size} numbers, got {value!r}")
    return tuple(_snap(item, f"{where}[{index}]") for index, item in enumerate(value))


# --- moves ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Cycle:
    """A canned cycle's parameters, in the setup frame.

    ``r`` is the R plane's height and ``z`` the depth the cycle cuts to: under ``r`` for every cycle but a back
    bore, which starts below the part (``r``) and cuts up to ``z``. ``peck_mm`` (peck, chip_break), ``pitch_mm``
    (tap, rigid_tap, mm per turn) and ``shift_mm`` (fine_bore, back_bore: how far the tool moves off the wall) are
    given exactly when the kind takes them; ``dwell_s`` is a pause at the depth (any kind but drill).

    ``return_to`` (``"initial"``: G98, ``"r"``: G99) says which height the move's ``xyz[2]`` is; ``None`` leaves it
    to be read from the heights (:func:`cycle_return`). A back bore returns to where it started, never to its R
    plane, which is under the part. ``shift_xy`` is the (x, y) way an oriented boring bar moves off the wall
    (fine_bore, back_bore), any length but 0; ``None`` means +X, ``(1, 0)``.
    """

    kind: str
    r: float
    z: float
    peck_mm: float | None = None
    pitch_mm: float | None = None
    shift_mm: float | None = None
    dwell_s: float = 0.0
    return_to: str | None = None
    shift_xy: tuple[float, float] | None = None

    def __post_init__(self):
        if self.kind not in CYCLE_KINDS:
            raise ValueError(f"cycle: {self.kind!r} is not one of {', '.join(CYCLE_KINDS)}")
        object.__setattr__(self, "r", _snap(self.r, "cycle.r"))
        object.__setattr__(self, "z", _snap(self.z, "cycle.z"))
        needs = _CYCLE_NEEDS.get(self.kind, ())
        for key in ("peck_mm", "pitch_mm", "shift_mm"):
            value = getattr(self, key)
            if key in needs:
                if value is None or _snap(value, f"cycle.{key}") <= 0:
                    raise ValueError(f"cycle.{key}: a {self.kind} cycle needs a {key} above 0, got {value!r}")
                object.__setattr__(self, key, _snap(value, f"cycle.{key}"))
            elif value is not None:
                raise ValueError(f"cycle.{key}: a {self.kind} cycle takes no {key}")
        dwell = _snap(self.dwell_s, "cycle.dwell_s")
        if dwell < 0:
            raise ValueError(f"cycle.dwell_s: must be 0 or more, got {dwell:g}")
        if dwell and self.kind not in _CYCLE_DWELLS:
            raise ValueError("cycle.dwell_s: a drill cycle has no dwell; a spot cycle is a drill cycle with one")
        object.__setattr__(self, "dwell_s", dwell)
        if self.kind == "back_bore" and not self.r < self.z:
            raise ValueError(f"cycle: a back bore starts below the part (r={self.r:g}) and cuts up to z={self.z:g}, "
                             "so r must be under z")
        if self.kind != "back_bore" and not self.z < self.r:
            raise ValueError(f"cycle: the depth z={self.z:g} must be under the R plane r={self.r:g}")
        if self.return_to is not None and self.return_to not in RETURN_TO:
            raise ValueError(f"cycle.return_to: {self.return_to!r} is not one of initial (G98), r (G99), or None")
        if self.kind == "back_bore" and self.return_to == "r":
            raise ValueError("cycle.return_to: a back bore returns to the height it started from; its R plane is "
                             "under the part")
        if self.shift_xy is not None:
            if self.shift_mm is None:
                raise ValueError(f"cycle.shift_xy: a {self.kind} cycle does not shift off the wall")
            shift = _point(self.shift_xy, 2, "cycle.shift_xy")
            if math.hypot(*shift) <= _EPS:
                raise ValueError("cycle.shift_xy: the way the tool shifts must not be (0, 0)")
            object.__setattr__(self, "shift_xy", shift)


@dataclass(frozen=True)
class Move:
    """One move of a toolpath, ending at ``xyz`` (setup frame, mm). See the module docstring for the kinds."""

    kind: str
    xyz: Point
    #: mm/min; ``None`` for a rapid, and for a cycle its cutting feed (a tap's is rpm x pitch).
    feed: float | None = None
    #: The operation's index in the job.
    op: int = 0
    #: Cumulative seconds at the end of this move (:func:`timed`).
    t: float = 0.0
    #: An arc's centre ``(x, y)``.
    center: tuple[float, float] | None = None
    cycle: Cycle | None = None
    #: A pause at the end of the move, seconds (G4); a cycle's dwell is its own.
    dwell_s: float = 0.0

    def __post_init__(self):
        if self.kind not in MOVE_KINDS:
            raise ValueError(f"move: {self.kind!r} is not one of {', '.join(MOVE_KINDS)}")
        object.__setattr__(self, "xyz", _point(self.xyz, 3, "move.xyz"))
        if self.kind == "rapid":
            if self.feed is not None:
                raise ValueError("move.feed: a rapid runs at the machine's rapid rate and has no feed")
        else:
            feed = None if self.feed is None else _snap(self.feed, "move.feed")
            if feed is None or feed <= 0:
                raise ValueError(f"move.feed: a {self.kind} move needs a feed above 0 mm/min, got {self.feed!r}")
            object.__setattr__(self, "feed", feed)
        if isinstance(self.op, bool) or not isinstance(self.op, int) or self.op < 0:
            raise ValueError(f"move.op: the operation's index, a whole number 0 or more, got {self.op!r}")
        t = _snap(self.t, "move.t")
        if t < 0:
            raise ValueError(f"move.t: must be 0 or more, got {t:g}")
        object.__setattr__(self, "t", t)
        if self.kind in ARC_KINDS:
            if self.center is None:
                raise ValueError(f"move.center: an {self.kind} needs its centre (x, y)")
            object.__setattr__(self, "center", _point(self.center, 2, "move.center"))
        elif self.center is not None:
            raise ValueError(f"move.center: a {self.kind} move has no centre")
        if self.kind == "cycle":
            if not isinstance(self.cycle, Cycle):
                raise ValueError("move.cycle: a cycle move needs its Cycle")
        elif self.cycle is not None:
            raise ValueError(f"move.cycle: a {self.kind} move has no cycle")
        dwell = _snap(self.dwell_s, "move.dwell_s")
        if dwell < 0:
            raise ValueError(f"move.dwell_s: must be 0 or more, got {dwell:g}")
        if dwell and self.kind == "cycle":
            raise ValueError("move.dwell_s: a cycle's dwell is its cycle's dwell_s")
        object.__setattr__(self, "dwell_s", dwell)


def walk(moves: Iterable[Move], start: Sequence[float]) -> Iterator[tuple[Point, Move]]:
    """Each move with the point it starts from."""
    here = _point(start, 3, "start")
    for move in moves:
        yield here, move
        here = move.xyz


# --- arcs ----------------------------------------------------------------------------------------------

def arc_sweep(start: Sequence[float], move: Move) -> float:
    """The angle an arc turns through, radians, signed (+ counter-clockwise), never a full turn."""
    cx, cy = move.center
    a0 = math.atan2(start[1] - cy, start[0] - cx)
    a1 = math.atan2(move.xyz[1] - cy, move.xyz[0] - cx)
    turn = (a1 - a0) % (2 * math.pi) if move.kind == "arc_ccw" else -((a0 - a1) % (2 * math.pi))
    return turn


def _radius(point: Sequence[float], center: Sequence[float]) -> float:
    return math.hypot(point[0] - center[0], point[1] - center[1])


def _arc_length(start: Point, move: Move) -> float:
    planar = abs(arc_sweep(start, move)) * _radius(start, move.center)
    return math.hypot(planar, move.xyz[2] - start[2])


# --- canned cycles -------------------------------------------------------------------------------------

def expand_cycle(move: Move, start: Sequence[float], *, peck_clearance_mm: float = PECK_CLEARANCE_MM,
                 chip_break_mm: float = CHIP_BREAK_MM, shift_direction: tuple[float, float] = (1.0, 0.0)
                 ) -> tuple[Move, ...]:
    """The plain moves a cycle move stands for, from ``start`` (where the tool is before it).

    The tool rapids across at its starting height to the hole, down to the R plane, cuts, and comes back to the
    move's end height. Cutting down is ``plunge``, cutting up (a ream's or a tap's way out, a back bore) ``feed``,
    the dwell sits on the move that reaches the depth. ``peck_clearance_mm`` and ``chip_break_mm`` are the
    controller's G83 and G73 settings; ``shift_direction`` is the way an oriented boring bar moves off the wall
    (G76, G87) when the cycle's own ``shift_xy`` is ``None``. A tap's expansion is motion only: the spindle
    reversal at the bottom is the post's to write.
    """
    if move.kind != "cycle":
        raise ValueError(f"expand_cycle: a {move.kind} move is not a cycle")
    start = _point(start, 3, "start")
    _check_cycle(start, move)
    cycle, feed, op = move.cycle, move.feed, move.op
    x, y, end_z = move.xyz
    r, z = cycle.r, cycle.z
    if cycle.shift_xy is not None:
        shift_direction = cycle.shift_xy
    out: list[Move] = []

    def go(kind: str, px: float, py: float, pz: float, *, dwell: float = 0.0):
        last = out[-1].xyz if out else start
        if (px, py, pz) != tuple(last) or dwell:
            out.append(Move(kind, (px, py, pz), None if kind == "rapid" else feed, op, dwell_s=dwell))

    go("rapid", x, y, start[2])
    if cycle.kind == "back_bore":
        length = math.hypot(*shift_direction)
        if not length:
            raise ValueError("expand_cycle: shift_direction must not be (0, 0)")
        sx, sy = (cycle.shift_mm * value / length for value in shift_direction)
        go("rapid", x + sx, y + sy, start[2])
        go("rapid", x + sx, y + sy, r)
        go("rapid", x, y, r)
        go("feed", x, y, z, dwell=cycle.dwell_s)
        go("rapid", x + sx, y + sy, z)
        go("rapid", x + sx, y + sy, end_z)
        go("rapid", x, y, end_z)
        return tuple(out)
    go("rapid", x, y, r)
    if cycle.kind in ("peck", "chip_break"):
        q = cycle.peck_mm
        bottom, count = r, 0
        while True:
            count += 1
            target = r - count * q
            final = target <= z + _EPS
            target = z if final else target
            if count > 1:
                if cycle.kind == "peck":
                    go("rapid", x, y, min(bottom + peck_clearance_mm, r))
                # chip_break: already backed off chip_break_mm above the last bottom
            go("plunge", x, y, target, dwell=cycle.dwell_s if final else 0.0)
            if final:
                break
            bottom = target
            go("rapid", x, y, r if cycle.kind == "peck" else min(bottom + chip_break_mm, r))
        go("rapid", x, y, end_z)
        return tuple(out)
    go("plunge", x, y, z, dwell=cycle.dwell_s)
    if cycle.kind in ("tap", "rigid_tap", "ream"):
        go("feed", x, y, r)
    elif cycle.kind == "fine_bore":
        length = math.hypot(*shift_direction)
        if not length:
            raise ValueError("expand_cycle: shift_direction must not be (0, 0)")
        sx, sy = (cycle.shift_mm * value / length for value in shift_direction)
        go("rapid", x + sx, y + sy, z)
        go("rapid", x + sx, y + sy, end_z)
    go("rapid", x, y, end_z)
    return tuple(out)


def _check_cycle(start: Point, move: Move) -> None:
    cycle = move.cycle
    end_z = move.xyz[2]
    if cycle.kind == "back_bore":
        if not start[2] > cycle.z:
            raise ValueError(f"cycle: a back bore starts above the part, at {start[2]:g}, over its top z={cycle.z:g}")
        if abs(end_z - start[2]) > _EPS:
            raise ValueError(f"cycle: a back bore returns to the height it started from ({start[2]:g}), "
                             f"not {end_z:g} (its R plane is under the part)")
        return
    if start[2] < cycle.r - _EPS:
        raise ValueError(f"cycle: the tool starts at {start[2]:g}, under the cycle's R plane {cycle.r:g}")
    if abs(end_z - cycle.r) > _EPS and abs(end_z - start[2]) > _EPS:
        raise ValueError(f"cycle: a cycle returns to its R plane ({cycle.r:g}) or to the height it started from "
                         f"({start[2]:g}), not {end_z:g}")
    if cycle.return_to == "r" and abs(end_z - cycle.r) > _EPS:
        raise ValueError(f"cycle: return_to r, so it ends at its R plane ({cycle.r:g}), not {end_z:g}")
    if cycle.return_to == "initial" and abs(end_z - start[2]) > _EPS:
        raise ValueError(f"cycle: return_to initial, so it ends at the height it started from ({start[2]:g}), "
                         f"not {end_z:g}")


def cycle_return(move: Move, start: Sequence[float]) -> str:
    """``"initial"`` (G98) or ``"r"`` (G99): what a cycle move returns to, from ``start`` (where the tool is before
    it). The cycle's ``return_to`` when set; else read from the heights: its R plane when it ends there and started
    above it, else the height it started from (a cycle starting on its R plane is G98 and G99 at once; it reads as
    ``"initial"``)."""
    if move.kind != "cycle":
        raise ValueError(f"cycle_return: a {move.kind} move is not a cycle")
    start = _point(start, 3, "start")
    _check_cycle(start, move)
    if move.cycle.return_to is not None:
        return move.cycle.return_to
    if move.cycle.kind != "back_bore" and abs(move.xyz[2] - move.cycle.r) <= _EPS < start[2] - move.cycle.r:
        return "r"
    return "initial"


def _expanded(start: Point, move: Move) -> tuple[Move, ...]:
    return expand_cycle(move, start) if move.kind == "cycle" else (move,)


# --- measuring -----------------------------------------------------------------------------------------

def move_length(start: Sequence[float], move: Move) -> float:
    """How far the tool travels, mm: an arc along its helix, a cycle along its expanded moves."""
    start = _point(start, 3, "start")
    if move.kind == "cycle":
        return sum(move_length(here, part) for here, part in walk(expand_cycle(move, start), start))
    if move.kind in ARC_KINDS:
        return _arc_length(start, move)
    return math.dist(start, move.xyz)


def move_seconds(start: Sequence[float], move: Move, *, rapid_mm_min: float) -> float:
    """How long the move takes: rapids at ``rapid_mm_min``, the rest at their feed, plus any dwell."""
    if not (isinstance(rapid_mm_min, (int, float)) and math.isfinite(rapid_mm_min) and rapid_mm_min > 0):
        raise ValueError(f"rapid_mm_min: the machine's rapid rate in mm/min, above 0, got {rapid_mm_min!r}")
    start = _point(start, 3, "start")
    if move.kind == "cycle":
        parts = expand_cycle(move, start)
        return sum(move_seconds(here, part, rapid_mm_min=rapid_mm_min) for here, part in walk(parts, start))
    rate = rapid_mm_min if move.kind == "rapid" else move.feed
    return move_length(start, move) / rate * 60.0 + move.dwell_s


def timed(moves: Iterable[Move], *, start: Sequence[float], rapid_mm_min: float, t0: float = 0.0) -> tuple[Move, ...]:
    """The moves with ``t`` filled: the cumulative seconds at the end of each, counted from ``t0``."""
    out, t = [], float(t0)
    for here, move in walk(moves, start):
        t += move_seconds(here, move, rapid_mm_min=rapid_mm_min)
        out.append(replace(move, t=t))
    return tuple(out)


@dataclass(frozen=True)
class Lengths:
    """How far the tool travels, mm: cutting (at a feed) and rapid."""

    cut_mm: float
    rapid_mm: float


def lengths(moves: Iterable[Move], *, start: Sequence[float]) -> Lengths:
    cut = rapid = 0.0
    for here, move in walk(moves, start):
        for part_start, part in walk(_expanded(here, move), here):
            if part.kind == "rapid":
                rapid += move_length(part_start, part)
            else:
                cut += move_length(part_start, part)
    return Lengths(cut, rapid)


def bounds(moves: Iterable[Move], *, start: Sequence[float]) -> tuple[Point, Point]:
    """The box the tool tip sweeps, ``((xmin, ymin, zmin), (xmax, ymax, zmax))``, start included."""
    points: list[Sequence[float]] = [_point(start, 3, "start")]
    for here, move in walk(moves, start):
        for part_start, part in walk(_expanded(here, move), here):
            points.append(part.xyz)
            if part.kind in ARC_KINDS:
                points.extend(_arc_extremes(part_start, part))
    low = tuple(min(point[axis] for point in points) for axis in range(3))
    high = tuple(max(point[axis] for point in points) for axis in range(3))
    return low, high


def _arc_extremes(start: Point, move: Move) -> list[Point]:
    """The points where an arc crosses +X, +Y, -X and -Y of its centre (z at either end bounds it)."""
    cx, cy = move.center
    radius = _radius(start, move.center)
    a0 = math.atan2(start[1] - cy, start[0] - cx)
    sweep = arc_sweep(start, move)
    out = []
    for quarter in range(4):
        angle = quarter * math.pi / 2
        turned = (angle - a0) % (2 * math.pi) if sweep > 0 else (a0 - angle) % (2 * math.pi)
        if turned < abs(sweep):
            out.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle), start[2]))
    return out


def check(moves: Iterable[Move], *, start: Sequence[float]) -> None:
    """ValueError naming the first move that cannot be run: an arc whose ends sit at different radii, or a zero or
    full turn; a cycle the tool does not start above, or that returns to neither its R plane nor its start height."""
    for index, (here, move) in enumerate(walk(moves, start)):
        try:
            if move.kind in ARC_KINDS:
                r0, r1 = _radius(here, move.center), _radius(move.xyz, move.center)
                if r0 < ARC_RADIUS_TOL_MM:
                    raise ValueError("an arc's start sits on its centre")
                if abs(r0 - r1) > ARC_RADIUS_TOL_MM:
                    raise ValueError(f"the arc starts {r0:.4f} mm from its centre and ends {r1:.4f} mm from it")
                if math.hypot(here[0] - move.xyz[0], here[1] - move.xyz[1]) < _EPS:
                    raise ValueError("an arc that ends where it starts is a full turn (or none); write two arcs")
            elif move.kind == "cycle":
                _check_cycle(here, move)
        except ValueError as exc:
            raise ValueError(f"moves[{index}] ({move.kind}): {str(exc).removeprefix('cycle: ')}") from None


def rapids_below(moves: Iterable[Move], *, start: Sequence[float], z: float) -> list[int]:
    """The indices of the rapids that move sideways with either end under ``z`` (the retract plane).

    Straight down and straight up are not counted: up leaves the cut, and down is onto a height the caller
    declared clear. A cycle counts when its approach does: it rapids across at the height it starts from to above
    its hole, so a cycle starting under ``z`` away from its hole (the next hole of a G99 chain, at the R plane) is
    listed. The rest of a cycle's motion (pecks, a boring bar's shift inside its hole) is the cycle's own; it is not
    looked into.
    """
    found = []
    for index, (here, move) in enumerate(walk(moves, start)):
        sideways = math.hypot(move.xyz[0] - here[0], move.xyz[1] - here[1]) > _EPS
        if move.kind == "rapid" and sideways and min(here[2], move.xyz[2]) < z - _EPS:
            found.append(index)
        elif move.kind == "cycle" and sideways and here[2] < z - _EPS:
            found.append(index)
    return found


# --- one operation's toolpath --------------------------------------------------------------------------

@dataclass(frozen=True)
class Toolpath:
    """One operation's moves, with what the spindle does while it runs them.

    ``start`` is where the tool is before the first move (where the last toolpath, or the tool change, left it).
    ``rpm`` is ``None`` for a machine with no spindle. Made checked (:func:`check`): every move is the operation's,
    and a tap cycle feeds at exactly rpm x pitch.
    """

    op: int
    tool: str
    rpm: float | None
    coolant: str
    start: Point
    moves: tuple[Move, ...] = field(default_factory=tuple)

    def __post_init__(self):
        from cadgen._internal.cam.job import COOLANTS

        if self.coolant not in COOLANTS:
            raise ValueError(f"toolpath.coolant: {self.coolant!r} is not one of {', '.join(COOLANTS)}")
        if self.rpm is not None and _snap(self.rpm, "toolpath.rpm") <= 0:
            raise ValueError(f"toolpath.rpm: above 0, or None for no spindle, got {self.rpm!r}")
        object.__setattr__(self, "start", _point(self.start, 3, "toolpath.start"))
        object.__setattr__(self, "moves", tuple(self.moves))
        for index, move in enumerate(self.moves):
            if not isinstance(move, Move):
                raise ValueError(f"toolpath.moves[{index}]: expected a Move, got {type(move).__name__}")
            if move.op != self.op:
                raise ValueError(f"toolpath.moves[{index}]: belongs to operation {move.op}, not {self.op}")
            if move.kind == "cycle" and move.cycle.pitch_mm is not None:
                if self.rpm is None or abs(move.feed - self.rpm * move.cycle.pitch_mm) > 1e-3:
                    raise ValueError(f"toolpath.moves[{index}]: a {move.cycle.kind} cycle feeds at rpm x pitch "
                                     f"({self.rpm} x {move.cycle.pitch_mm:g}), not {move.feed:g} mm/min")
        check(self.moves, start=self.start)

    @property
    def end(self) -> Point:
        return self.moves[-1].xyz if self.moves else self.start

    def lengths(self) -> Lengths:
        return lengths(self.moves, start=self.start)

    def bounds(self) -> tuple[Point, Point]:
        return bounds(self.moves, start=self.start)

    def seconds(self, rapid_mm_min: float) -> float:
        return sum(move_seconds(here, move, rapid_mm_min=rapid_mm_min) for here, move in walk(self.moves, self.start))

    def timed(self, rapid_mm_min: float, t0: float = 0.0) -> "Toolpath":
        return replace(self, moves=timed(self.moves, start=self.start, rapid_mm_min=rapid_mm_min, t0=t0))


# --- arc fitting ---------------------------------------------------------------------------------------

#: The most points one fitted line or arc may span (keeps fitting a long polyline near linear in time).
_MAX_SPAN = 256


def fit_arcs(points: Sequence[Sequence[float]], tol: float, *, feed: float, op: int = 0) -> tuple[Move, ...]:
    """Lines and arcs through a polyline, within ``tol`` of it everywhere.

    ``points[0]`` is where the tool already is; the moves end at the later points, a line or an arc (helical when
    Z changes linearly along it) spanning as many of them as stay within ``tol`` mm, the longer of the two
    winning, a line on a tie. An arc turns no more than half a turn, so a full circle comes back as two arcs.
    Deterministic: the same points give the same moves.

    The points must be sampled finer than ``tol``: an arc is fitted only where every chord of the polyline sags
    from it by less than ``tol``, so a circle sampled coarsely (a 90-gon of radius 20 sags 0.012 mm) stays lines at
    ``tol`` 0.01. Sample curves with a chord deflection of ``tol / 4`` or less (:func:`sample_curve`, or
    :func:`densify` for a polyline that already is the shape).
    """
    if not (isinstance(tol, (int, float)) and math.isfinite(tol) and tol > 0):
        raise ValueError(f"fit_arcs: tol must be above 0 mm, got {tol!r}")
    pts: list[Point] = []
    for index, point in enumerate(points):
        point = tuple(float(value) for value in _point(point, 3, f"points[{index}]"))
        if not pts or math.dist(point, pts[-1]) > _EPS:
            pts.append(point)
    out: list[Move] = []
    i = 0
    while i < len(pts) - 1:
        j_line = _extend(i, pts, lambda a, b: _line_fits(pts, a, b, tol))
        j_arc, center, ccw = i + 1, None, True
        j = i + 2
        while j < len(pts) and j - i <= _MAX_SPAN:
            fit = _arc_fits(pts, i, j, tol)
            if fit is None:
                break
            j_arc, (center, ccw) = j, fit
            j += 1
        if center is not None and j_arc > j_line:
            out.append(Move("arc_ccw" if ccw else "arc_cw", pts[j_arc], feed, op, center=center))
            i = j_arc
        else:
            out.append(Move("feed", pts[j_line], feed, op))
            i = j_line
    return tuple(out)


def sample_curve(curve, t0: float, t1: float, *, max_deflection: float, min_segments: int = 4,
                 max_depth: int = 20) -> list[Point]:
    """Points along ``curve(t)`` (a function giving ``(x, y, z)``) from ``t0`` to ``t1``, close enough that no chord
    strays more than about ``max_deflection`` mm from the curve: each of ``min_segments`` equal steps is halved
    until the curve's midpoint lies within ``max_deflection`` of the chord. Smooth curves only: a feature narrower
    than a step can be missed. For :func:`fit_arcs` at ``tol``, pass ``max_deflection=tol / 4``.
    """
    if not (isinstance(max_deflection, (int, float)) and math.isfinite(max_deflection) and max_deflection > 0):
        raise ValueError(f"sample_curve: max_deflection must be above 0 mm, got {max_deflection!r}")
    if not (isinstance(min_segments, int) and min_segments >= 1):
        raise ValueError(f"sample_curve: min_segments must be a whole number 1 or more, got {min_segments!r}")

    def at(t: float) -> Point:
        return tuple(float(value) for value in _point(tuple(curve(t)), 3, f"curve({t:g})"))

    out = [at(t0)]

    def split(ta: float, pa: Point, tb: float, pb: Point, depth: int):
        tm = (ta + tb) / 2
        pm = at(tm)
        if depth < max_depth and _segment_distance(pm, pa, pb) > max_deflection:
            split(ta, pa, tm, pm, depth + 1)
            split(tm, pm, tb, pb, depth + 1)
        else:
            out.append(pb)

    ts = [t0 + (t1 - t0) * k / min_segments for k in range(min_segments + 1)]
    points = [out[0]] + [at(t) for t in ts[1:]]
    for k in range(min_segments):
        split(ts[k], points[k], ts[k + 1], points[k + 1], 0)
    return out


def densify(points: Sequence[Sequence[float]], *, max_segment: float) -> list[Point]:
    """The polyline with every segment split evenly into pieces no longer than ``max_segment`` mm. It adds points
    on the chords, so it cannot bring a polyline closer to the curve it was sampled from: resample curves with
    :func:`sample_curve` instead. Use it where the polyline is the shape (a pocket's straight walls) and a later
    step wants points no further apart than ``max_segment``."""
    if not (isinstance(max_segment, (int, float)) and math.isfinite(max_segment) and max_segment > 0):
        raise ValueError(f"densify: max_segment must be above 0 mm, got {max_segment!r}")
    pts = [tuple(float(value) for value in _point(point, 3, f"points[{index}]")) for index, point in enumerate(points)]
    out = pts[:1]
    for a, b in zip(pts, pts[1:]):
        pieces = max(1, math.ceil(math.dist(a, b) / max_segment - 1e-9))
        out.extend(tuple(a[n] + (b[n] - a[n]) * k / pieces for n in range(3)) for k in range(1, pieces + 1))
    return out


def _segment_distance(p: Sequence[float], a: Sequence[float], b: Sequence[float]) -> float:
    ab = [b[k] - a[k] for k in range(3)]
    span = sum(value * value for value in ab)
    s = 0.0 if span <= 0 else max(0.0, min(1.0, sum((p[k] - a[k]) * ab[k] for k in range(3)) / span))
    return math.dist(p, [a[k] + s * ab[k] for k in range(3)])


def _extend(i: int, pts: list, fits) -> int:
    j = i + 1
    while j + 1 < len(pts) and j + 1 - i <= _MAX_SPAN and fits(i, j + 1):
        j += 1
    return j


def _line_fits(pts: list, i: int, j: int, tol: float) -> bool:
    a, b = pts[i], pts[j]
    ab = [b[k] - a[k] for k in range(3)]
    span = sum(value * value for value in ab)
    if span <= _EPS * _EPS:
        return False
    for k in range(i + 1, j):
        p = pts[k]
        s = max(0.0, min(1.0, sum((p[n] - a[n]) * ab[n] for n in range(3)) / span))
        if math.dist(p, [a[n] + s * ab[n] for n in range(3)]) > tol:
            return False
    return True


def _circumcenter(a, b, c) -> tuple[float, float] | None:
    ax, ay = a[0], a[1]
    bx, by = b[0] - ax, b[1] - ay
    cx, cy = c[0] - ax, c[1] - ay
    d = 2 * (bx * cy - by * cx)
    scale = max(bx * bx + by * by, cx * cx + cy * cy)
    if abs(d) <= 1e-12 * scale:
        return None
    b2, c2 = bx * bx + by * by, cx * cx + cy * cy
    return ax + (cy * b2 - by * c2) / d, ay + (bx * c2 - cx * b2) / d


def _arc_fits(pts: list, i: int, j: int, tol: float):
    """``(centre, ccw)`` when one arc through ``pts[i]`` and ``pts[j]`` stays within ``tol`` of every point and every
    chord between them and turns one way, by at most half a turn; else ``None``."""
    center = _circumcenter(pts[i], pts[(i + j) // 2], pts[j])
    if center is None:
        return None
    radius = _radius(pts[i], center)
    angles = [0.0]
    direction = 0
    for k in range(i, j):
        u = (pts[k][0] - center[0], pts[k][1] - center[1])
        v = (pts[k + 1][0] - center[0], pts[k + 1][1] - center[1])
        step = math.atan2(u[0] * v[1] - u[1] * v[0], u[0] * v[0] + u[1] * v[1])
        sign = 1 if step > 0 else -1 if step < 0 else 0
        if sign == 0 or (direction and sign != direction):
            return None
        direction = sign
        angles.append(angles[-1] + abs(step))
    sweep = angles[-1]
    if sweep > math.pi + 1e-9:
        return None
    z0, z1 = pts[i][2], pts[j][2]
    errors = []
    for n, k in enumerate(range(i, j + 1)):
        radial = _radius(pts[k], center) - radius
        along = pts[k][2] - (z0 + (z1 - z0) * angles[n] / sweep)
        errors.append(math.hypot(radial, along))
    for n, k in enumerate(range(i, j)):
        chord = math.hypot(pts[k + 1][0] - pts[k][0], pts[k + 1][1] - pts[k][1])
        sagitta = radius - math.sqrt(max(radius * radius - chord * chord / 4, 0.0))
        if max(errors[n], errors[n + 1]) + sagitta > tol:
            return None
    return center, direction > 0


# --- linking -------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Heights:
    """The two travel heights, as z in the setup frame: ``clearance`` (between features, over everything) and
    ``retract`` (between passes, over the stock); retract at or under clearance."""

    clearance: float
    retract: float

    def __post_init__(self):
        object.__setattr__(self, "clearance", _snap(self.clearance, "heights.clearance"))
        object.__setattr__(self, "retract", _snap(self.retract, "heights.retract"))
        if self.retract > self.clearance:
            raise ValueError(f"heights: retract ({self.retract:g}) must be at or under clearance ({self.clearance:g})")

    @classmethod
    def above(cls, stock_top_z: float, *, clearance_mm: float, retract_mm: float) -> "Heights":
        """From ``clearance_mm`` and ``retract_mm`` above a stock top z. Operations use :meth:`from_frame`."""
        return cls(stock_top_z + clearance_mm, stock_top_z + retract_mm)

    @classmethod
    def from_frame(cls, frame, operation=None) -> "Heights":
        """An operation's heights in a placed setup (a :class:`~cadgen._internal.cam.setups.SetupFrame`): its
        ``clearance_mm`` and ``retract_mm``, or the setup's when ``operation`` is ``None``, through
        ``SetupFrame.above_stock``, the one place a height above the stock becomes a z."""
        source = frame.setup if operation is None else operation
        return cls(frame.above_stock(source.clearance_mm), frame.above_stock(source.retract_mm))


@dataclass(frozen=True)
class Plunge:
    """Enter straight down the tool axis, at the entry feed."""


PLUNGE = Plunge()


@dataclass(frozen=True)
class Ramp:
    """Enter along a straight ramp, zig-zagging between the cut's start and ``toward`` (x, y), never steeper than
    ``angle_deg`` from the horizontal; it ends at the cut's start."""

    toward: tuple[float, float]
    angle_deg: float

    def __post_init__(self):
        object.__setattr__(self, "toward", _point(self.toward, 2, "ramp.toward"))
        if not 0 < _snap(self.angle_deg, "ramp.angle_deg") < 90:
            raise ValueError(f"ramp.angle_deg: between 0 and 90, got {self.angle_deg!r}")


@dataclass(frozen=True)
class Helix:
    """Enter along a helix round ``center`` (x, y) through the cut's start, in half turns, never steeper than
    ``angle_deg`` from the horizontal; counter-clockwise unless ``cw``."""

    center: tuple[float, float]
    angle_deg: float
    cw: bool = False

    def __post_init__(self):
        object.__setattr__(self, "center", _point(self.center, 2, "helix.center"))
        if not 0 < _snap(self.angle_deg, "helix.angle_deg") < 90:
            raise ValueError(f"helix.angle_deg: between 0 and 90, got {self.angle_deg!r}")


def link(start: Sequence[float], end: Sequence[float], *, heights: Heights, retract_is_clear: bool, feed: float,
         op: int, entry: Plunge | Ramp | Helix = PLUNGE, rapid_to: float | None = None) -> tuple[Move, ...]:
    """The moves from ``start`` (the end of one cut) to ``end`` (the start of the next).

    Up to the travel height, across, down, in:

    1. Rapid straight up to the travel height: the retract plane when ``retract_is_clear`` (the caller knows
       nothing stands above it between the two points: no clamp, jaw or uncut boss), else the clearance plane.
       A tool already higher stays at its height.
    2. Rapid across, at that height, to above where the entry starts.
    3. Rapid straight down to ``rapid_to``: by default (``None``) the retract plane (or ``end`` when that is
       higher). A lower ``rapid_to`` is the caller's declaration that no stock stands above it anywhere over the
       entry (the last pass's floor, or air beside the stock); it must be at or above ``end``.
    4. Enter, from ``rapid_to`` down to ``end``, at ``feed``: a plunge (``PLUNGE``), a :class:`Ramp` or a
       :class:`Helix`.

    The only rapid under the retract plane is step 3's straight-down one.

    Callers should pass ``rapid_to``: the stock top over uncut stock, or the last pass's floor where the stock is
    already cut away. Left at the retract plane, a ramp or helix starts its descent up in the air and spends that
    part of its length cutting nothing (a ramp 5 mm into the stock from a 3 mm retract drops 3 of its 8 mm in air).
    """
    start = _point(start, 3, "start")
    end = _point(end, 3, "end")
    if not isinstance(retract_is_clear, bool):
        raise ValueError("link: retract_is_clear must be True or False: say whether the retract plane is clear")
    travel = max(start[2], heights.retract if retract_is_clear else heights.clearance, end[2])
    if rapid_to is None:
        down = max(heights.retract, end[2]) if heights.retract < travel else travel
    else:
        down = _snap(rapid_to, "rapid_to")
        if not end[2] - _EPS <= down <= travel + _EPS:
            raise ValueError(f"link: rapid_to ({down:g}) must be between the cut's start ({end[2]:g}) and the travel "
                             f"height ({travel:g})")
    entry_moves, entry_xy = _entry(entry, end, down, feed, op)
    out: list[Move] = []

    def rapid(x: float, y: float, z: float):
        last = out[-1].xyz if out else start
        if (x, y, z) != tuple(last):
            out.append(Move("rapid", (x, y, z), None, op))

    rapid(start[0], start[1], travel)
    rapid(entry_xy[0], entry_xy[1], travel)
    rapid(entry_xy[0], entry_xy[1], down)
    out.extend(entry_moves)
    return tuple(out)


def _entry(entry, end: Point, top: float, feed: float, op: int) -> tuple[list[Move], tuple[float, float]]:
    """The entry's moves from ``top`` down to ``end``, and the (x, y) they start from."""
    drop = top - end[2]
    if isinstance(entry, Plunge):
        return ([Move("plunge", end, feed, op)] if drop > _EPS else []), end[:2]
    if isinstance(entry, Ramp):
        length = math.hypot(entry.toward[0] - end[0], entry.toward[1] - end[1])
        if length < _EPS:
            raise ValueError("link: a ramp's toward point must not be the cut's start")
        if drop <= _EPS:
            return [], end[:2]
        legs = max(1, math.ceil(drop / (length * math.tan(math.radians(entry.angle_deg))) - 1e-9))
        ends = [entry.toward if (legs - leg) % 2 else end[:2] for leg in range(legs + 1)]
        moves = [Move("feed", (*ends[leg], top - drop * leg / legs), feed, op) for leg in range(1, legs + 1)]
        return moves, ends[0]
    if isinstance(entry, Helix):
        cx, cy = entry.center
        radius = math.hypot(end[0] - cx, end[1] - cy)
        if radius < 10 * ARC_RADIUS_TOL_MM:
            raise ValueError("link: a helix's centre must not be the cut's start")
        opposite = (2 * cx - end[0], 2 * cy - end[1])
        if drop <= _EPS:
            return [], end[:2]
        halves = max(1, math.ceil(drop / (math.pi * radius * math.tan(math.radians(entry.angle_deg))) - 1e-9))
        ends = [opposite if (halves - half) % 2 else end[:2] for half in range(halves + 1)]
        kind = "arc_cw" if entry.cw else "arc_ccw"
        moves = [Move(kind, (*ends[half], top - drop * half / halves), feed, op, center=entry.center)
                 for half in range(1, halves + 1)]
        return moves, ends[0]
    raise ValueError(f"link: entry must be PLUNGE, a Ramp or a Helix, got {entry!r}")
