"""Turning an operation's passes into moves: the one place every 2.5D operation links, enters and cuts.

An operation decides *what* to cut: ordered passes, each a ring or an open polyline at a height, and for each where
its entry starts and how high the uncut material stands over it. :class:`Emitter` decides *how the tool gets there*:

- :meth:`Emitter.go` links from where the tool is to the start of a pass (:func:`~cadgen._internal.cam.toolpath.link`)
  and enters it, then cuts along it. The link travels at the retract height only when that is provably clear
  (:func:`retract_clear`: no fixture stands above it near the straight line across); else at the clearance height.
  It rapids down only to ``rapid_to``, which the operation passes as the top of the uncut material over the entry
  (the stock top, an earlier facing's floor, or the previous pass's floor where that pass already cut the entry
  point clear), and enters from there.
- :func:`entry_for` picks the entry: a plunge only where the tool is in air (its whole circle off the stock), a helix
  in a closed pocket where one fits, a ramp along the pass's first straight run, or (a closed pass with no straight
  run to ramp along, such as a circle) laps of the pass itself sloping down (:class:`AlongPath`).
- :meth:`Emitter.cut` cuts on from where the tool is, without lifting: arcs and lines through
  :func:`~cadgen._internal.cam.toolpath.fit_arcs` at half the tolerance, a straight drop as a plunge at the plunge
  feed.
- :func:`lead_arc` gives a quarter-circle lead on or off a pass, tangent to it, on the air side.
- :meth:`Emitter.toolpath` lifts to the clearance height and returns the :class:`~cadgen._internal.cam.toolpath.Toolpath`
  with the operation's tool, spindle speed and coolant.

shapely is imported inside the functions; this module is stdlib-only at import.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

from cadgen._internal.cam.toolpath import PLUNGE, Helix, Move, Plunge, Ramp, Toolpath, fit_arcs, link, sample_curve

if TYPE_CHECKING:
    from cadgen._internal.cam.operations.context import PlanContext

__all__ = ["AlongPath", "DEFAULT_RAMP_DEG", "Emitter", "entry_for", "in_air", "lead_arc", "retract_clear"]

Point = tuple[float, float, float]

#: The steepest a ramp or a helix enters at when an operation does not say, degrees from the horizontal.
DEFAULT_RAMP_DEG = 3.0
#: The shortest step a cut keeps, mm: under the coarsest post resolution (0.0001 in is 0.00254 mm), so every move the
#: toolpath holds is one the program writes.
MIN_STEP_MM = 3e-3
#: A helix's radius as a fraction of the tool's, when it fits, and the least it may be before a ramp is used.
HELIX_FRACTION = 0.5
HELIX_MIN_FRACTION = 0.1


def retract_clear(ctx: "PlanContext", a: Sequence[float], b: Sequence[float]) -> bool:
    """The straight line from ``a`` to ``b`` (x, y) at the retract height is clear: no fixture volume stands above the
    retract plane within reach of the tool (its widest, holder included, over the height the volume stands up) of
    that line. Nothing of the part or the stock is above the retract plane, which is above the stock top."""
    from shapely.geometry import LineString, Point as ShapelyPoint, box

    retract = ctx.heights.retract
    line = ShapelyPoint(a[0], a[1]) if math.dist(a[:2], b[:2]) < 1e-9 else LineString([a[:2], b[:2]])
    for volume in ctx.setup.fixture.volumes:
        top = volume.max[2] if hasattr(volume, "max") else volume.z_max
        if top <= retract:
            continue
        reach = max([envelope.radius for envelope in ctx.profile.envelope() if envelope.z0 < top - retract],
                    default=ctx.radius)
        if hasattr(volume, "max"):
            footprint = box(volume.min[0], volume.min[1], volume.max[0], volume.max[1])
        else:
            footprint = ShapelyPoint(*volume.center).buffer(volume.radius)
        if line.distance(footprint) <= reach + ctx.tol:
            return False
    return True


def in_air(ctx: "PlanContext", xy: Sequence[float], z: float | None = None) -> bool:
    """The tool's whole circle at ``xy`` is off the stock (by the tolerance): nothing to cut under it, so it may plunge
    there."""
    from shapely.geometry import Point as ShapelyPoint

    from cadgen._internal.cam.operations.geometry2d import stock_outline

    return stock_outline(ctx, z).distance(ShapelyPoint(xy[0], xy[1])) >= ctx.radius + ctx.tol


def entry_for(ctx: "PlanContext", path: Sequence[Sequence[float]], *, angle_deg: float = DEFAULT_RAMP_DEG,
              area=None, helix: bool = False, near=None) -> tuple[Plunge | Ramp | Helix, list[Point]]:
    """How the tool enters ``path`` (its first point is where the entry ends): a plunge where the tool is in air; in a
    closed pocket (``helix`` with ``area``, where the tool's centre may go), a helix whose circle stays inside
    ``area``; else a ramp along the path's first straight run when it is at least the tool's diameter long (or the
    path is open), else, for a closed path, :class:`AlongPath`. Returns the entry and the path to cut after it.

    A helix is centred, in order of preference: on the path's first straight run (its circle through the path's
    start, so the path follows on at once), at the middle of ``near`` (a polygon: the innermost ring of an offset
    clearing) or at the middle of the piece of ``area`` the path starts in; from the last two the tool feeds
    straight from the helix's end to the path's start, which must stay inside ``area`` too.
    """
    start = tuple(path[0])
    if in_air(ctx, start[:2]):
        return PLUNGE, list(path)
    toward = _along(path)
    if helix and area is not None:
        found = _helix(ctx, start, toward, area, near, angle_deg)
        if found is not None:
            entry, landing = found
            lead = [landing] if math.dist(landing[:2], start[:2]) > 1e-9 else []
            return entry, lead + list(path)
    closed = len(path) > 2 and math.dist(path[0], path[-1]) < 1e-9
    if toward is not None and (not closed or math.dist(toward, start[:2]) >= 2 * ctx.radius):
        return Ramp(toward, angle_deg), list(path)
    if closed:
        return AlongPath(angle_deg), list(path)
    raise ValueError(f"{ctx.label}: no room to ramp into the cut at ({start[0]:g}, {start[1]:g}) and the tool is not "
                     "in air there; a plunge would bury it")


@dataclass(frozen=True)
class AlongPath:
    """Enter a closed pass by following it down: whole laps of it from the entry height, sloping evenly to the pass's
    depth at its start, never steeper than ``angle_deg``. For a ring with no straight run long enough to ramp along
    (a circle, a small outline)."""

    angle_deg: float


def _descent(path: Sequence[Sequence[float]], top: float, angle_deg: float) -> list[Point]:
    """Laps of the closed ``path`` from ``top`` down to its own height, ending at its start; a point of the path
    standing higher (a tab) keeps its height."""
    z = path[0][2]
    lap = sum(math.dist(a[:2], b[:2]) for a, b in zip(path, path[1:]))
    need = (top - z) / math.tan(math.radians(angle_deg))
    laps = max(1, math.ceil(need / lap - 1e-9))
    total, walked = laps * lap, 0.0
    out = [(path[0][0], path[0][1], top)]
    for _ in range(laps):
        for a, b in zip(path, path[1:]):
            walked += math.dist(a[:2], b[:2])
            out.append((b[0], b[1], max(b[2], top - (top - z) * min(walked / total, 1.0))))
    out[-1] = tuple(path[0])
    return out


def _along(path: Sequence[Sequence[float]]) -> tuple[float, float] | None:
    """The far end of the path's first straight, level run from its start (the run a ramp zig-zags along), or
    ``None``."""
    start = path[0]
    best = None
    for point in path[1:]:
        if abs(point[2] - start[2]) > 1e-9:
            break
        if math.dist(point[:2], start[:2]) < 1e-6:
            continue
        if best is not None:
            ax, ay = best[0] - start[0], best[1] - start[1]
            bx, by = point[0] - start[0], point[1] - start[1]
            if abs(ax * by - ay * bx) > 1e-6 * math.hypot(ax, ay) * math.hypot(bx, by) or ax * bx + ay * by < 0:
                break
        best = point
    return None if best is None else (best[0], best[1])


def _helix(ctx: "PlanContext", start: Point, toward, area, near, angle_deg: float):
    """A helix inside ``area`` for a cut starting at ``start``: ``(Helix, landing)``, the landing being where it ends;
    ``None`` when none of a useful radius fits (see :func:`entry_for`)."""
    from shapely.geometry import LineString, Point as ShapelyPoint
    from shapely.ops import polylabel

    poly = next((p for p in _polys(area) if p.buffer(ctx.tol).contains(ShapelyPoint(start[0], start[1]))), None)
    if poly is None:
        return None
    room = poly.buffer(ctx.tol / 4)
    want = HELIX_FRACTION * ctx.radius
    least = HELIX_MIN_FRACTION * ctx.radius

    def fits(cx: float, cy: float, radius: float) -> bool:
        return radius >= least and room.contains(ShapelyPoint(cx, cy).buffer(radius, quad_segs=16))

    if toward is not None:
        length = math.dist(toward, start[:2])
        radius = min(want, length / 2)
        ux, uy = (toward[0] - start[0]) / length, (toward[1] - start[1]) / length
        cx, cy = round(start[0] + radius * ux, 6), round(start[1] + radius * uy, 6)
        if fits(cx, cy, math.dist((cx, cy), start[:2])):
            return Helix((cx, cy), angle_deg), start
    for piece in ([near] if near is not None else []) + [poly]:
        centre = polylabel(piece, tolerance=ctx.tol)
        cx, cy = round(centre.x, 6), round(centre.y, 6)
        clearance = min([poly.exterior.distance(ShapelyPoint(cx, cy))]
                        + [interior.distance(ShapelyPoint(cx, cy)) for interior in poly.interiors])
        radius = min(want, clearance - ctx.tol)
        if not fits(cx, cy, radius):
            continue
        dx, dy = start[0] - cx, start[1] - cy
        length = math.hypot(dx, dy)
        ux, uy = (dx / length, dy / length) if length > 1e-9 else (1.0, 0.0)
        landing = (cx + radius * ux, cy + radius * uy, start[2])
        if math.dist(landing[:2], start[:2]) > 1e-9 and not room.contains(LineString([landing[:2], start[:2]])):
            continue
        return Helix((cx, cy), angle_deg), landing
    return None


def _polys(area):
    from cadgen._internal.cam.operations.geometry2d import polygons

    return polygons(area)


def lead_arc(path: Sequence[Sequence[float]], radius: float, *, at_end: bool, air_left: bool, tol: float) -> list[Point]:
    """A quarter-circle lead of ``radius`` tangent to ``path`` at its start (``at_end`` False: from the air onto
    the path, ending at ``path[0]``) or at its end (from ``path[-1]`` off into the air), on the side of the
    travel the air is on (``air_left``), sampled finely enough for :func:`fit_arcs` at ``tol / 2``."""
    if at_end:
        p, q = path[-1], next(pt for pt in reversed(path[:-1]) if math.dist(pt[:2], path[-1][:2]) > 1e-9)
        tx, ty = p[0] - q[0], p[1] - q[1]
    else:
        p, q = path[0], next(pt for pt in path[1:] if math.dist(pt[:2], path[0][:2]) > 1e-9)
        tx, ty = q[0] - p[0], q[1] - p[1]
    length = math.hypot(tx, ty)
    tx, ty = tx / length, ty / length
    nx, ny = (-ty, tx) if air_left else (ty, -tx)
    cx, cy = p[0] + radius * nx, p[1] + radius * ny
    a_path = math.atan2(p[1] - cy, p[0] - cx)
    # Travelling along +t at p, the circle round c turns counter-clockwise when the centre is on the left.
    sense = 1.0 if air_left else -1.0
    if at_end:
        a0, a1 = a_path, a_path + sense * math.pi / 2
    else:
        a0, a1 = a_path - sense * math.pi / 2, a_path
    z = p[2]
    points = sample_curve(lambda t: (cx + radius * math.cos(t), cy + radius * math.sin(t), z), a0, a1,
                          max_deflection=tol / 8, min_segments=8)
    points[-1 if not at_end else 0] = tuple(p)
    return [tuple(point) for point in points]


class Emitter:
    """Moves for one operation, in order (see the module's docstring)."""

    def __init__(self, ctx: "PlanContext"):
        self.ctx = ctx
        self.moves: list[Move] = []
        self.start: Point | None = None if ctx.start is None else tuple(ctx.start)
        self.here: Point | None = self.start
        self.op = ctx.index
        self.feed = ctx.feeds.feed_mm_min
        self.plunge = ctx.feeds.plunge_mm_min

    def go(self, path: Sequence[Sequence[float]], *, rapid_to: float,
           entry: Plunge | Ramp | Helix | AlongPath = PLUNGE) -> None:
        """Link to ``path[0]``, enter it from ``rapid_to`` (the top of the uncut material over the entry) and cut along
        the path."""
        ctx = self.ctx
        end = tuple(float(c) for c in path[0])
        rapid_to = max(min(rapid_to, ctx.heights.retract), end[2])
        if isinstance(entry, AlongPath):
            descent = _descent(path, rapid_to, entry.angle_deg) if rapid_to > end[2] + 1e-9 else [end]
            self.go(descent, rapid_to=rapid_to, entry=PLUNGE)
            self.cut(path[1:])
            return
        feed = self.plunge if isinstance(entry, Plunge) else self.feed
        if self.here is None:
            probe = link((end[0], end[1], ctx.heights.clearance), end, heights=ctx.heights, retract_is_clear=False,
                         feed=feed, op=self.op, entry=entry, rapid_to=rapid_to)
            over = next((move.xyz for move in probe if move.kind == "rapid"), end)
            self.start = self.here = (over[0], over[1], ctx.heights.clearance)
        moves = link(self.here, end, heights=ctx.heights, retract_is_clear=False, feed=feed, op=self.op, entry=entry,
                     rapid_to=rapid_to)
        across = [move for move in moves if move.kind == "rapid"]
        if across and retract_clear(ctx, self.here, across[-1].xyz):
            moves = link(self.here, end, heights=ctx.heights, retract_is_clear=True, feed=feed, op=self.op,
                         entry=entry, rapid_to=rapid_to)
        self._add(moves)
        self.cut(path[1:])

    def cut(self, path: Sequence[Sequence[float]]) -> None:
        """Cut on from where the tool is through ``path`` without lifting: a straight drop is a plunge at the plunge
        feed, the rest lines and arcs at the feed."""
        if self.here is None:
            raise ValueError(f"{self.ctx.label}: a cut must start with go(), which brings the tool to it")
        kept: list[Point] = []
        last = self.here
        for index, point in enumerate(path):
            point = tuple(float(c) for c in point)
            if math.dist(point, last) < MIN_STEP_MM:   # a step no program can write: the post would drop it
                if index == len(path) - 1 and kept:
                    kept[-1] = last = point
                continue
            kept.append(point)
            last = point
        run: list[Point] = [self.here]
        for point in kept:
            last = run[-1]
            if math.dist(point[:2], last[:2]) < 1e-9 and point[2] < last[2] - 1e-9:
                self._flush(run)
                self._add([Move("plunge", point, self.plunge, self.op)])
                run = [point]
            else:
                run.append(point)
        self._flush(run)

    def _flush(self, run: list[Point]) -> None:
        if len(run) > 1:
            self._add(fit_arcs(run, self.ctx.tol / 2, feed=self.feed, op=self.op))

    def _add(self, moves) -> None:
        for move in moves:
            if move.kind not in ("arc_cw", "arc_ccw") and move.xyz == self.here and not move.dwell_s:
                continue  # goes nowhere
            self.moves.append(move)
            self.here = move.xyz

    def toolpath(self) -> Toolpath:
        """The toolpath, with the tool lifted straight up to the clearance height at its end."""
        ctx = self.ctx
        if self.here is not None and self.here[2] < ctx.heights.clearance:
            self._add([Move("rapid", (self.here[0], self.here[1], ctx.heights.clearance), None, self.op)])
        start = self.start or (0.0, 0.0, ctx.heights.clearance)
        return Toolpath(self.op, ctx.tool.id, ctx.feeds.rpm, ctx.operation.coolant, start, tuple(self.moves))
