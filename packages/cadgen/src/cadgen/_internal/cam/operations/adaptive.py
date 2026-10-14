"""Adaptive clearing: 2.5D roughing whose radial engagement never exceeds a stated cap.

``adaptive_2d``
    Clears a ``pocket`` (islands left standing), a ``region``, or the stock round a ``profile`` (``side: outside``,
    the profile's default; a pocket and a region are cleared from the ``inside``) down to the floor plus
    ``stock_to_leave_mm`` (or ``z_mm`` / ``depth_mm``), leaving ``stock_to_leave_mm`` on the walls too. Each level is
    ``stepdown_mm`` deep, by default the deepest the flutes allow: the smallest of the flute length, twice the
    diameter and the whole depth. ``max_engagement`` (0.1 of the diameter) caps how wide a cut the tool's side takes
    anywhere; the feed is raised for the thinner chip (chip thinning, :mod:`cadgen._internal.cam.feeds`).
    ``ramp_angle_deg`` (3) is the steepest the helix or ramp that enters each level goes.

How a level is cleared (clean room; no commercial adaptive's method is followed):

The cut is tracked on a fine grid (:class:`CutMap`, cells an eightieth of the diameter): which of the level's stock is
still standing. The tool centre walks in short steps (a twentieth of the diameter) keeping the standing material on
its right (climb milling) and steering each step: of the directions it may take (a limited turn first, wider when
that is not enough, at most straight back), it takes the one turned furthest towards the material whose newly cut
area per mm of travel stays under the target engagement, and that keeps the tool's centre where it may go (the
region offset in from the walls by the radius and the stock to leave, its convex corners rounded to a quarter of the
diameter). The target is three quarters of the cap: a step's load is steered as a whole, and a stretch of path
straddling two steps (where the walk turned towards the material) reads up to about a fifth more.

The walk follows the material's edge round: out from the entry as a spiral, along the walls, and in loops of its own
where a passage is too narrow to go straight on at that engagement (trochoidal milling emerges from the steering; it
is not drawn). Where it has cut nothing for a diameter and then meets material again, the stretch is replaced by a
straight move across the cut when that is allowed; far from any material it strides along the walls. It ends when it
has gone round the region and a circle more, or turned two and a half turns, without cutting. The next walk starts
at the standing material nearest the tool: straight there at depth when that move stays under the engagement, else
beside it where the tool's circle is already cut, fed to at depth through the cut when the straight move allows,
else over the top and straight down onto the cut (never into stock). When nothing the walks are meant to reach
stands, one ring round each wall at the exact offset takes the last of the wall stock. Every move's engagement is
measured as it is cut, and a level whose measured engagement would exceed the cap is refused, never posted.

Inside corners: a tool whose centre turns a corner sharply wraps the corner's wall and takes a cut far wider than any
cap (round a corner of the tool's own radius, a quarter of its circle at once). So the centre turns no corner tighter
than a quarter of the diameter, and an inside corner drawn tighter than the radius, the stock to leave and that is left
at that radius for a finishing pass: ``stats["corner_left"]`` lists them, as pocket_2d's does.

Each level is entered with a helix in the middle of the region (a ramp along its longest straight where no helix
fits), from the level above; the stock round a profile is entered from the air beside it.

The engagement model (:class:`CutMap`): a move from ``a`` to ``b`` cuts the material standing inside the tool's
circle swept from ``a`` to ``b`` and not inside its circle at ``a``; that area over the move's length is the radial
engagement width, as a straight cut of that width would remove it. It counts a cell as cut only once one tool circle
covers it whole, so it errs towards more engagement, never less.

Stdlib only at import; numpy and shapely are imported inside the functions.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cadgen._internal.cam.operations import base as _base
from cadgen._internal.cam.operations.base import OperationBase, ParseContext
from cadgen._internal.cam.operations.milling_2d import (_END_MILLS, _angle, _floor_from, _floor_keys, _fraction, _mm,
                                                        _region_polygon)
from cadgen._internal.cam.values import choice, known

if TYPE_CHECKING:
    from cadgen._internal.cam.operations.context import PlanContext, Planned

__all__ = ["Adaptive2dOperation", "AdaptiveParams", "CutMap", "DEFAULT_MAX_ENGAGEMENT", "engagement_angle_deg"]

#: The radial engagement cap when the job does not state one, as a fraction of the diameter.
DEFAULT_MAX_ENGAGEMENT = 0.1
#: The engagement a step aims at, as a fraction of the cap (see the module's docstring).
TARGET_FRACTION = 0.75
#: Grid cell, step length and tightest steering turn, as fractions of the diameter.
CELL_FRACTION = 0.0125
STEP_FRACTION = 0.05
TURN_RADIUS_FRACTION = 0.25
#: How much longer a step is where no material is within reach of it.
AIR_STRIDE = 4
#: The walks leave this fraction of the target engagement on the walls for the final wall rings.
WALL_BAND_FRACTION = 0.4
#: How far beyond the stock the tool may go round a profile, besides its radius, mm.
AIR_MARGIN_MM = 0.5

PlanError = getattr(_base, "PlanError", None)
if PlanError is None:  # until operations/base.py carries it (the shared layer's fix adds it there)
    class PlanError(ValueError):
        """A well-formed operation that cannot be planned on this part; ``kind`` is the finding it becomes."""

        def __init__(self, kind: str, summary: str, description: str = "", items=()):
            super().__init__(summary)
            self.kind, self.summary, self.description, self.items = kind, summary, description, tuple(items)


def engagement_angle_deg(width_mm: float, diameter_mm: float) -> float:
    """The engagement angle (degrees of the tool's circle in the cut) of a straight cut ``width_mm`` wide."""
    f = min(max(width_mm / diameter_mm, 0.0), 1.0)
    return math.degrees(math.acos(1.0 - 2.0 * f))


# --- the engagement model ------------------------------------------------------------------------------------

class CutMap:
    """The material standing at one level, on a grid of square cells ``cell`` mm wide over ``bounds`` (grown by the
    radius), and the tool of ``radius`` sweeping it (see the module's docstring)."""

    def __init__(self, bounds, radius: float, cell: float):
        import numpy as np

        x0, y0, x1, y1 = bounds
        pad = radius + 3 * cell
        self.cell, self.radius = float(cell), float(radius)
        self.x0, self.y0 = x0 - pad, y0 - pad
        self.nx = int(math.ceil((x1 + pad - self.x0) / cell))
        self.ny = int(math.ceil((y1 + pad - self.y0) / cell))
        self.xs = self.x0 + (np.arange(self.nx) + 0.5) * cell
        self.ys = self.y0 + (np.arange(self.ny) + 0.5) * cell
        self._xs32, self._ys32 = self.xs.astype(np.float32), self.ys.astype(np.float32)
        self.solid = np.zeros((self.ny, self.nx), dtype=bool)
        self.r2 = radius * radius
        # A cell is cut once its far corner is inside the tool's circle.
        self.cut2 = max(radius - cell * math.sqrt(0.5), 0.0) ** 2

    def inside(self, geom):
        """The cells whose centres are in ``geom`` (a shapely geometry), as a boolean grid."""
        import numpy as np
        import shapely

        if geom is None or geom.is_empty:
            return np.zeros_like(self.solid)
        shapely.prepare(geom)
        x, y = np.meshgrid(self.xs, self.ys)
        return shapely.contains_xy(geom, x, y)

    def fill(self, geom) -> None:
        """Material stands wherever ``geom`` is."""
        self.solid |= self.inside(geom)

    def _window(self, xmin, ymin, xmax, ymax):
        i0 = max(int(math.floor((xmin - self.x0) / self.cell)), 0)
        i1 = min(int(math.ceil((xmax - self.x0) / self.cell)) + 1, self.nx)
        j0 = max(int(math.floor((ymin - self.y0) / self.cell)), 0)
        j1 = min(int(math.ceil((ymax - self.y0) / self.cell)) + 1, self.ny)
        return slice(j0, max(j0, j1)), slice(i0, max(i0, i1))

    def _capsule(self, a, b):
        """(window, squared distance of each cell centre in it to the segment a-b, to a)."""
        r = self.radius
        rows, cols = self._window(min(a[0], b[0]) - r, min(a[1], b[1]) - r, max(a[0], b[0]) + r, max(a[1], b[1]) + r)
        # Relative to a, in single precision: well under a cell either way.
        x = self._xs32[cols][None, :] - a[0]
        y = self._ys32[rows][:, None] - a[1]
        dx, dy = b[0] - a[0], b[1] - a[1]
        length2 = dx * dx + dy * dy
        to_a = x * x + y * y
        if length2 < 1e-18:
            return (rows, cols), to_a, to_a
        t = ((x * dx + y * dy) * (1.0 / length2)).clip(0.0, 1.0)
        ex, ey = x - t * dx, y - t * dy
        return (rows, cols), ex * ex + ey * ey, to_a

    def _empty(self, a, b) -> bool:
        r = self.radius
        rows, cols = self._window(min(a[0], b[0]) - r, min(a[1], b[1]) - r, max(a[0], b[0]) + r, max(a[1], b[1]) + r)
        return not self.solid[rows, cols].any()

    def standing(self, center, radius: float | None = None) -> float:
        """The area (mm2) of material standing inside a circle of ``radius`` (the tool's) at ``center``."""
        rows, cols = self._window(center[0] - (radius or self.radius), center[1] - (radius or self.radius),
                                  center[0] + (radius or self.radius), center[1] + (radius or self.radius))
        x = self.xs[cols][None, :]
        y = self.ys[rows][:, None]
        near = (x - center[0]) ** 2 + (y - center[1]) ** 2 <= (radius or self.radius) ** 2
        return float((self.solid[rows, cols] & near).sum()) * self.cell * self.cell

    def gain(self, a, b) -> float:
        """The engagement width (mm) of a short step from ``a`` to ``b``, from the tool's circle at ``b`` alone: what
        the steering asks of each candidate step (cheaper than :meth:`engagement`, and within a sliver of it)."""
        r = self.radius
        length = math.dist(a[:2], b[:2])
        if length < 1e-12:
            return 0.0
        rows, cols = self._window(b[0] - r, b[1] - r, b[0] + r, b[1] + r)
        block = self.solid[rows, cols]
        if not block.any():
            return 0.0
        # Relative to b, in single precision: the crescent is decided to well under a cell.
        x = self._xs32[cols][None, :] - (b[0] - 0.0)
        y = self._ys32[rows][:, None] - (b[1] - 0.0)
        ux, uy = a[0] - b[0], a[1] - b[1]
        d_b = x * x + y * y
        d_a = d_b - 2 * (x * ux + y * uy) + (ux * ux + uy * uy)
        new = (d_b <= self.r2) & (d_a > self.r2) & block
        return float(new.sum()) * self.cell * self.cell / length

    def any_near(self, center, radius: float, among=None) -> bool:
        """Material stands within the square round ``center`` of half-side ``radius`` (of the cells ``among``, a
        boolean grid, when given): a quick test that there is nothing to steer by."""
        rows, cols = self._window(center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius)
        if among is None:
            return bool(self.solid[rows, cols].any())
        return bool((self.solid[rows, cols] & among[rows, cols]).any())

    def engagement(self, a, b) -> float:
        """The radial engagement width (mm) of the move from ``a`` to ``b`` on the material standing before it: the
        standing area the tool's circle sweeps that was not inside it at ``a``, over the move's length."""
        length = math.dist(a[:2], b[:2])
        if length < 1e-12 or self._empty(a, b):
            return 0.0
        window, swept, at_a = self._capsule(a, b)
        new = (swept <= self.r2) & (at_a > self.r2)
        return float((self.solid[window] & new).sum()) * self.cell * self.cell / length

    def sweep(self, a, b) -> tuple[float, int]:
        """Cut the move from ``a`` to ``b``: ``(engagement width mm, cells cut)``."""
        length = math.dist(a[:2], b[:2])
        if self._empty(a, b):
            return 0.0, 0
        window, swept, at_a = self._capsule(a, b)
        block = self.solid[window]
        width = 0.0
        if length >= 1e-12:
            width = float((block & (swept <= self.r2) & (at_a > self.r2)).sum()) * self.cell * self.cell / length
        cut = block & (swept <= self.cut2)
        count = int(cut.sum())
        block &= ~cut
        return width, count

    def clear_disc(self, center, radius: float) -> None:
        """Mark the material inside a circle of ``radius`` at ``center`` cut (an entry's bore)."""
        rows, cols = self._window(center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius)
        x = self.xs[cols][None, :]
        y = self.ys[rows][:, None]
        inner = max(radius - self.cell * math.sqrt(0.5), 0.0) ** 2
        self.solid[rows, cols] &= ~(((x - center[0]) ** 2 + (y - center[1]) ** 2) <= inner)

    def point(self, row: int, col: int) -> tuple[float, float]:
        return float(self.xs[col]), float(self.ys[row])


# --- the operation ----------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class AdaptiveParams:
    max_engagement: float        # fraction of the diameter
    stepdown_mm: float | None    # None: the flutes' depth (see the module's docstring)
    side: str | None             # inside | outside; None: the target's own
    ramp_angle_deg: float
    depth_mm: float | None
    z_mm: float | None


def round_corners(area, radius: float, tol: float):
    """The region the tool's centre may go with its convex corners rounded to at least ``radius``: the pieces an
    opening (shrink, then grow back) cuts off, where they are corners (they fit in a circle of twice ``radius``), are
    left off; a narrow passage it cuts off is kept. A tool whose centre turns a corner tighter than that wraps the
    corner's wall and takes a cut far wider than any cap (see the module's docstring)."""
    import shapely
    from shapely.ops import unary_union

    from cadgen._internal.cam.operations import geometry2d as g2

    opened = g2.offset(g2.offset(area, -radius, tol), radius, tol)
    rest = [piece for piece in g2.polygons(area.difference(opened))
            if shapely.minimum_bounding_radius(piece) > 2 * radius]
    return g2.offset(unary_union([opened, *rest]).intersection(area), 0.0, tol)


class _Level:
    """One level of one target being cleared: the grid, where the tool's centre may go, and the numbers."""

    def __init__(self, ctx, centre_area, material, z: float, above: float, target_w: float):
        import shapely

        from cadgen._internal.cam.operations import geometry2d as g2

        r = ctx.radius
        self.ctx, self.z, self.above, self.target_w = ctx, z, above, target_w
        self.area = centre_area
        shapely.prepare(self.area)
        self.room = self.area.buffer(ctx.tol / 4)   # where a straight move may run: the region, to a hair
        shapely.prepare(self.room)
        self.cell = CELL_FRACTION * 2 * r
        self.step = STEP_FRACTION * 2 * r
        self.turn = self.step / (TURN_RADIUS_FRACTION * 2 * r)
        self.map = CutMap(g2.offset(centre_area, r, ctx.tol).bounds, r, self.cell)
        # The material this level removes: the stock the tool's circle can reach.
        self.map.fill(material.intersection(g2.offset(centre_area, r, ctx.tol)))
        # The cells a tool circle can cut whole: the last cell along the walls is only ever cut in part, so it is no
        # reason to steer.
        self.live = self.map.inside(g2.offset(centre_area, r - 2 * self.cell, ctx.tol))
        band = WALL_BAND_FRACTION * target_w
        inner = g2.offset(centre_area, -band, ctx.tol)
        # What the walks must cut; the rest (a band along the walls, the corners) is the wall rings'.
        self.reach = self.map.inside(g2.offset(inner, r, ctx.tol)) if not inner.is_empty else self.map.inside(None)
        self.max_w = 0.0
        self._area = self._length = 0.0
        self.restarts = 0
        self.lifts = 0

    def allowed(self, x: float, y: float) -> bool:
        import shapely

        return bool(shapely.contains_xy(self.area, x, y))

    def segment_allowed(self, a, b) -> bool:
        from shapely.geometry import LineString

        if math.dist(a[:2], b[:2]) < 1e-9:
            return self.allowed(a[0], a[1])
        return self.room.contains(LineString([a[:2], b[:2]]))

    def record(self, width: float, a, b) -> None:
        """Note a cut move's engagement. Moves shorter than half a step are pooled with the next, so a sliver at the
        end of a tiny move does not read as a wide cut."""
        length = math.dist(a[:2], b[:2])
        self._area += width * length
        self._length += length
        if self._length >= self.step / 2:
            self.max_w = max(self.max_w, self._area / self._length)
            self._area = self._length = 0.0


class Adaptive2dOperation(OperationBase):
    name, phase, word = "adaptive_2d", 1, "Adaptive clearing"
    keys = frozenset({"max_engagement", "stepdown_mm", "side", "ramp_angle_deg", "depth_mm", "z_mm"})
    tool_types = _END_MILLS
    targets = ("features", "region")
    kinds = ("pocket", "profile")

    def parse(self, raw: dict, ctx: ParseContext) -> AdaptiveParams:
        known(raw, self.keys, where=ctx.where, takes=f"adaptive_2d takes {', '.join(sorted(self.keys))}")
        side = choice(raw["side"], ("inside", "outside"), where=f"{ctx.where}.side") if "side" in raw else None
        depth, z = _floor_keys(raw, ctx)
        return AdaptiveParams(_fraction(raw, "max_engagement", ctx, DEFAULT_MAX_ENGAGEMENT),
                              _mm(raw, "stepdown_mm", ctx), side, _angle(raw, ctx), depth, z)

    # The targets: (ref, side, centre_area(z), bottom z or None, feature or None).
    def _targets(self, params: AdaptiveParams, ctx: "PlanContext") -> list[tuple]:
        from shapely.geometry import Polygon
        from shapely.ops import unary_union

        from cadgen._internal.cam.operations import geometry2d as g2

        r, stl, tol = ctx.radius, ctx.operation.stock_to_leave_mm, ctx.tol
        if ctx.operation.region is not None:
            if params.side == "outside":
                raise ValueError(f"{ctx.where}.side: a region is cleared from the inside; side outside clears the "
                                 "stock round a profile")
            poly = _region_polygon(ctx)
            return [("region", "inside", lambda z, p=poly: g2.offset(p.difference(g2.shadow(ctx, z)), -(r + stl), tol),
                     None, None)]
        found = []
        for ref, feature in ctx.targets(self.kinds):
            kind = feature.feature.kind
            own = "outside" if kind == "profile" else "inside"
            if params.side is not None and params.side != own:
                raise ValueError(f"{ctx.where}.side: {ref} is a {kind}, cleared from the {own}")
            if kind == "pocket":
                def centre(z, f=feature):
                    return g2.offset(g2.feature_region(ctx, f, z), -(r + stl), tol)
            else:
                def centre(z, f=feature):
                    keep = unary_union([Polygon(p.exterior) for p in g2.polygons(g2.shadow(ctx, z))]
                                       + [g2.feature_region(ctx, f, z)])
                    room = g2.offset(g2.stock_outline(ctx, z), r + AIR_MARGIN_MM, tol)
                    return g2.offset(room.difference(g2.offset(keep, r + stl, tol)), 0.0, tol)
            found.append((ref, own, centre, feature.bottom[2], feature))
        return found

    def plan(self, params: AdaptiveParams, ctx: "PlanContext") -> "Planned":
        from cadgen._internal.cam.feeds import chip_thinning, feeds_for_operation
        from cadgen._internal.cam.operations import geometry2d as g2
        from cadgen._internal.cam.operations.context import Planned
        from cadgen._internal.cam.operations.emit import Emitter

        cap = params.max_engagement
        feeds = feeds_for_operation(ctx.operation, ctx.tool, ctx.job.material, ctx.job.machine, where=ctx.label,
                                    radial_engagement=cap)
        ctx = dataclasses.replace(ctx, feeds=feeds)
        r, stl = ctx.radius, ctx.operation.stock_to_leave_mm
        diameter = 2 * r
        target_w = TARGET_FRACTION * cap * diameter
        emitter = Emitter(ctx)
        corner = TURN_RADIUS_FRACTION * diameter   # the tightest the tool's centre turns a corner
        stats: dict = {"targets": [], "corner_left": [], "max_engagement": cap, "radial_engagement": cap,
                       "chip_thinning": round(chip_thinning(cap), 6), "feed_mm_min": round(feeds.feed_mm_min, 6)}
        worst = 0.0
        restarts = lifts = 0
        for ref, side, centre_at, bottom, feature in self._targets(params, ctx):
            if bottom is None:
                floor = _floor_from(ctx, params.depth_mm, params.z_mm, None, deepest=None, what="")
            else:
                floor = _floor_from(ctx, params.depth_mm, params.z_mm, bottom + stl, deepest=None, what="")
                if floor < bottom - 1e-6:
                    raise PlanError("too_deep", f"{ctx.where}: cutting to z {floor:g} goes {bottom - floor:g} mm under "
                                    f"the floor of {ref} at z {bottom:g}, into the part")
            depth = ctx.top_z - floor
            if depth <= 1e-9:
                raise PlanError("empty_operation",
                                f"{ctx.where}: {ref} has nothing to cut: its floor at z {floor:g} is not under the top "
                                f"of the material at z {ctx.top_z:g}")
            stepdown = params.stepdown_mm
            if stepdown is None:
                stepdown = min(ctx.tool.flute_length_mm or 2 * diameter, 2 * diameter, depth)
            levels = g2.depth_steps(ctx.top_z, floor, stepdown)
            above = ctx.top_z
            for z in levels:
                area = round_corners(centre_at(z), corner, ctx.tol)
                if area.is_empty:
                    raise PlanError("tool_too_large", f"{ctx.where}: the {diameter:g} mm tool does not fit in {ref} at "
                                    f"z {z:g}")
                level = _Level(ctx, area, g2.stock_outline(ctx, z), z, above, target_w)
                self._clear(emitter, level, ref)
                worst = max(worst, level.max_w)
                restarts += level.restarts
                lifts += level.lifts
                if level.max_w > cap * diameter + 1e-9:
                    raise PlanError(
                        "tool_too_large",
                        f"{ctx.where}: {ref} at z {z:g} cannot be cleared at {cap:g} of the diameter: a passage is too "
                        f"narrow for the {diameter:g} mm tool to turn in (it would cut {level.max_w / diameter:.2f})",
                        "use a smaller tool, a higher max_engagement, or pocket_2d")
                above = z
            stats["targets"].append({"ref": ref, "side": side, "floor_z": round(floor, 6),
                                     "stepdown_mm": round(stepdown, 6), "levels": [round(z, 6) for z in levels]})
            drawn = feature.feature.size.get("corner_radius_mm") if feature is not None else None
            if drawn is not None and drawn + ctx.tol < r + stl + corner:
                stats["corner_left"].append({"ref": ref, "corner_radius_mm": drawn,
                                             "tool_leaves_radius_mm": round(r + stl + corner, 6)})
        stats.update({"engagement_measured": round(worst / diameter, 4),
                      "engagement_angle_deg": round(engagement_angle_deg(worst, diameter), 2),
                      "restarts": restarts, "lifts": lifts})
        return Planned(emitter.toolpath(), stats, tuple(feeds.findings))

    # --- one level -------------------------------------------------------------------------------------------

    def _clear(self, emitter, level: _Level, ref: str) -> None:
        from cadgen._internal.cam.operations import geometry2d as g2

        for piece in g2.polygons(level.area):
            budget = 40 * max(piece.area, 1.0) / (level.target_w * level.step) + 20000
            steps = 0
            while True:
                found = self._start(level, piece, emitter.here)
                if found is None:
                    break
                if found == "enter":
                    start, heading = self._enter(emitter, level, piece, ref)
                else:
                    start, heading, target_cell, direct = found
                    if direct:
                        self._cut(emitter, level, direct)
                    else:
                        self._link(emitter, level, start)
                    level.restarts += 1
                cut, walked = self._walk(emitter, level, piece, start, heading)
                steps += walked
                if found != "enter" and cut < 4:
                    # Nothing came off from here: leave that bit to the wall rings rather than come back for it.
                    row, col = target_cell
                    level.reach[max(row - 2, 0):row + 3, max(col - 2, 0):col + 3] = False
                if steps > budget:
                    raise PlanError("unreachable", f"{level.ctx.where}: {ref} at z {level.z:g}: adaptive clearing "
                                    "did not finish; the region is too tangled for this tool")
            self._walls(emitter, level, piece)

    def _start(self, level: _Level, piece, here):
        """Where the next walk starts: ``"enter"`` when nothing of the piece is cut yet at this level, ``None`` when
        the walks have nothing left, else ``(start, heading, cell, direct)`` for the standing material nearest the
        tool: straight to where the tool's circle takes it when that move stays under the engagement (``direct``: its
        points), else beside it where the tool's circle is already cut, heading so the material is on its right."""
        import numpy as np
        from shapely.geometry import Point
        from shapely.ops import nearest_points

        m = level.map
        left = m.solid & level.reach
        if not left.any():
            return None
        open_ = ~m.solid
        near_cut = np.zeros_like(left)
        near_cut[1:, :] |= open_[:-1, :]
        near_cut[:-1, :] |= open_[1:, :]
        near_cut[:, 1:] |= open_[:, :-1]
        near_cut[:, :-1] |= open_[:, 1:]
        rows, cols = np.nonzero(left & near_cut)
        if rows.size == 0:
            return "enter"
        if here is None:
            point = piece.representative_point()
            here = (point.x, point.y, level.z)
        dist = (m.xs[cols] - here[0]) ** 2 + (m.ys[rows] - here[1]) ** 2
        order = np.argsort(dist, kind="stable")
        r = m.radius
        reach = r + 2 * m.cell
        tiny = 0.25 * level.target_w * level.step
        at_depth = abs(here[2] - level.z) < 1e-9 and level.allowed(here[0], here[1])
        for rank, k in enumerate(order[:256]):
            mx, my = m.point(int(rows[k]), int(cols[k]))
            if at_depth and rank < 8:
                near = nearest_points(piece, Point(mx, my))[0]
                tx, ty = round(near.x, 6), round(near.y, 6)
                if math.dist((tx, ty), (mx, my)) <= r - 2 * m.cell and math.dist((tx, ty), here[:2]) > 1e-6:
                    path = self._path(level, here, (tx, ty, level.z))
                    if path is not None:
                        heading = math.atan2(ty - here[1], tx - here[0])
                        return path[-1], heading, (int(rows[k]), int(cols[k])), path[1:]
            best = None
            for n in range(24):
                angle = 2 * math.pi * n / 24
                qx, qy = round(mx + reach * math.cos(angle), 6), round(my + reach * math.sin(angle), 6)
                if not level.allowed(qx, qy) or m.standing((qx, qy)) > tiny:
                    continue
                d = math.dist((qx, qy), here[:2])
                if best is None or d < best[0] - 1e-12:
                    best = (d, (qx, qy, level.z), math.atan2(my - qy, mx - qx) + math.pi / 2)
            if best is not None:
                return best[1], best[2], (int(rows[k]), int(cols[k])), None
        if not (m.solid & level.reach & ~near_cut).any() and rows.size < 64:
            level.reach[rows, cols] = False   # slivers no tool circle can be put beside: the wall rings' to take
            return self._start(level, piece, here)
        return "enter"

    @staticmethod
    def _path(level: _Level, a, b):
        """The straight move from ``a`` to ``b`` at the level, in steps, when it stays where the tool's centre may go
        and no step of it cuts over the target engagement; else ``None``."""
        from cadgen._internal.cam.toolpath import densify

        if not level.segment_allowed(a, b):
            return None
        points = densify([a, b], max_segment=level.step)
        for p, q in zip(points, points[1:]):
            if level.map.engagement(p, q) > level.target_w:
                return None
        return points

    def _enter(self, emitter, level: _Level, piece, ref: str):
        """Enter a piece nothing of which is cut at this level: a helix at its widest point, then a full circle at the
        level; a ramp along its longest straight where no helix fits. Returns where the walk starts and its heading."""
        from shapely.geometry import LineString, Point
        from shapely.ops import polylabel

        from cadgen._internal.cam.operations.emit import HELIX_FRACTION, HELIX_MIN_FRACTION
        from cadgen._internal.cam.toolpath import Helix, Ramp, sample_curve

        ctx, m, z = level.ctx, level.map, level.z
        r, tol = ctx.radius, ctx.tol
        angle = ctx.params.ramp_angle_deg
        centre = polylabel(piece, tolerance=tol)
        cx, cy = round(centre.x, 6), round(centre.y, 6)
        clearance = piece.boundary.distance(Point(cx, cy))
        radius = min(HELIX_FRACTION * r, clearance - tol)
        if radius >= HELIX_MIN_FRACTION * r:
            landing = (cx + radius, cy, z)
            circle = sample_curve(lambda t: (cx + radius * math.cos(t), cy + radius * math.sin(t), z), 0.0,
                                  2 * math.pi, max_deflection=tol / 8, min_segments=16)
            circle[-1] = landing
            emitter.go([landing], rapid_to=level.above, entry=Helix((cx, cy), angle))
            m.clear_disc((cx, cy), radius + r)
            self._cut(emitter, level, circle[1:])
            return landing, math.pi / 2
        # No helix fits: ramp along the longest straight from the widest point.
        best = None
        for n in range(16):
            ux, uy = math.cos(2 * math.pi * n / 16), math.sin(2 * math.pi * n / 16)
            far = LineString([(cx, cy), (cx + 4 * r * ux, cy + 4 * r * uy)])
            inside = far.intersection(piece.buffer(-tol / 4))
            length = inside.length if inside.geom_type == "LineString" else 0.0
            if best is None or length > best[0] + 1e-9:
                best = (length, (round(cx + length * ux, 6), round(cy + length * uy, 6)))
        if best is None or best[0] < 2 * tol:
            raise PlanError("unreachable", f"{ctx.where}: no room to enter {ref} at z {z:g} with a helix or a ramp")
        toward = best[1]
        start = (cx, cy, z)
        emitter.go([start], rapid_to=level.above, entry=Ramp(toward, angle))
        m.sweep(start, (*toward, z))
        m.clear_disc((cx, cy), r)
        return start, math.atan2(toward[1] - cy, toward[0] - cx) + math.pi / 2

    def _cut(self, emitter, level: _Level, points) -> None:
        """Cut on through ``points`` (at the level), measuring and recording each move's engagement."""
        here = emitter.here
        for point in points:
            width, _ = level.map.sweep(here, point)
            level.record(width, here, point)
            here = point
        emitter.cut(points)

    def _link(self, emitter, level: _Level, start, toward=None) -> None:
        """From where the tool is to ``start``: at depth when the straight move stays where the centre may go and cuts
        under the target engagement; else over the top and straight down onto the cut, or (where stock still stands
        under the tool's circle at ``start``) down a ramp towards ``toward`` from the level above."""
        from cadgen._internal.cam.operations.emit import in_air
        from cadgen._internal.cam.toolpath import PLUNGE, Ramp

        here = emitter.here
        if here is not None and abs(here[2] - level.z) < 1e-9:
            path = self._path(level, here, start)
            if path is not None:
                self._cut(emitter, level, path[1:])
                return
        level.lifts += 1
        if toward is not None and level.map.standing(start) > 0:
            emitter.go([start], rapid_to=level.above, entry=Ramp(toward[:2], level.ctx.params.ramp_angle_deg))
            level.map.sweep(start, toward)
            return
        # Over the start the level is already cut (its tool circle stands clear), so nothing stands over it down to
        # the level: the tool comes straight down onto it, a plunge only where it is in air.
        if in_air(level.ctx, start[:2]):
            emitter.go([start], rapid_to=level.above, entry=PLUNGE)
        else:
            emitter.go([start], rapid_to=level.z, entry=PLUNGE)

    def _walk(self, emitter, level: _Level, piece, start, heading: float) -> tuple[int, int]:
        """Walk from ``start`` keeping the material on the right (see the module's docstring). Returns the cells cut
        and the steps taken."""
        m = level.map
        step, turn, target = level.step, level.turn, level.target_w
        # The walk follows the cut round (along a wall already cut, say) as far as the region's perimeter and a turn
        # before it gives up on finding material again; a stretch that cut nothing is replaced by a straight move
        # across the cut where that is allowed.
        air_limit = piece.length + 2 * math.pi * m.radius
        shortcut = 2 * m.radius
        stride = AIR_STRIDE * step
        p = (start[0], start[1])
        points: list[tuple[float, float, float]] = []
        kept = 0           # points up to the last that cut anything
        air = 0.0          # how far the walk has gone since then
        air_turn = 0.0
        total = 0
        steps = 0
        while True:
            steps += 1
            # Far from any material the walk only follows the walls, in longer strides.
            near = m.any_near(p, m.radius + stride + m.cell, level.live)
            length = step if near else stride

            def at(phi: float):
                return (round(p[0] + length * math.cos(phi), 6), round(p[1] + length * math.sin(phi), 6))

            def ok(phi: float) -> bool:
                q = at(phi)
                if not level.allowed(q[0], q[1]):
                    return False
                if not near:   # a stride: the whole of it inside, as it may cut a corner round an island
                    return level.segment_allowed(p, q)
                # With its middle inside too, a step strays out of a rounded edge by at most a quarter of its sag.
                if not level.allowed((p[0] + q[0]) / 2, (p[1] + q[1]) / 2):
                    return False
                return m.gain(p, q) <= target

            chosen = None
            for spread in (turn * length / step, 3 * turn * length / step, math.pi / 2, math.pi):
                left = heading + spread
                if not ok(left):
                    continue
                right = heading - spread
                if ok(right):
                    chosen = right
                else:
                    lo, hi = left, right       # lo is fine, hi is not
                    for _ in range(7):
                        mid = (lo + hi) / 2
                        if ok(mid):
                            lo = mid
                        else:
                            hi = mid
                    chosen = lo
                break
            if chosen is None:
                break
            q = at(chosen)
            if air > shortcut and near and m.gain(p, q) > 0:
                # Back to the material after a stretch that cut nothing: cross the cut straight when allowed.
                last = points[kept - 1] if kept else (start[0], start[1], level.z)
                across = self._path(level, last, (p[0], p[1], level.z))
                if across is not None and math.dist(last[:2], p) < air - 1e-9:
                    for a, b in zip(across, across[1:]):
                        width, count = m.sweep(a, b)
                        level.record(width, a, b)
                        total += count
                    points[kept:] = across[1:]
                    kept = len(points)   # what was swept stays in the path; the air run goes on counting
            width, count = m.sweep(p, q)
            level.record(width, p, q)
            points.append((q[0], q[1], level.z))
            total += count
            turn_by = chosen - heading
            heading, p = chosen, q
            if count:
                kept = len(points)     # the tail after the last cut is dropped: it cut nothing
                air = air_turn = 0.0
            else:
                air += length
                air_turn += turn_by
                if air > air_limit or abs(air_turn) > 2.5 * math.pi:
                    break
        points = points[:kept]
        if points:
            emitter.cut(points)
        return total, steps

    def _walls(self, emitter, level: _Level, piece) -> None:
        """One ring round each wall of the piece at the exact offset, wherever the stock reaches it: it takes the last
        of the wall stock exactly, whatever the grid left."""
        from cadgen._internal.cam.operations import geometry2d as g2
        from cadgen._internal.cam.toolpath import densify

        from shapely.geometry import LineString

        m = level.map
        stock = g2.stock_outline(level.ctx, level.z)
        for ring in g2.ring_paths(piece, level.z, material="outside"):
            if stock.distance(LineString([p[:2] for p in ring])) >= m.radius:
                continue    # round the outside of the stock, in the air
            dense = densify(ring, max_segment=level.step)
            here = emitter.here or dense[0]
            pts = dense[:-1]
            index = min(range(len(pts)), key=lambda k: (round(m.standing(pts[k]), 9),
                                                          round(math.dist(pts[k][:2], here[:2]), 9), k))
            loop = pts[index:] + pts[:index] + [pts[index]]
            # Should the tool have to come down onto the ring where stock stands, it ramps along the ring's first
            # straight (a diameter of it at most).
            toward = loop[1]
            for point in loop[2:]:
                if math.dist(point[:2], loop[0][:2]) > 2 * m.radius or not level.segment_allowed(loop[0], point):
                    break
                toward = point
            self._link(emitter, level, loop[0], toward)
            self._cut(emitter, level, loop[1:])

    def human_lines(self, stats: dict) -> list[str]:
        lines = [f"{t['ref']} {t['side']}: {len(t['levels'])} level(s) {t['stepdown_mm']:g} mm deep to z "
                 f"{t['floor_z']:g}" for t in stats["targets"]]
        for left in stats["corner_left"]:
            lines.append(f"{left['ref']}: R{left['corner_radius_mm']:g} inside corners left at "
                         f"R{left['tool_leaves_radius_mm']:g} for a finishing pass")
        lines.append(f"engagement at most {stats['max_engagement'] * 100:g}% of the diameter (measured "
                     f"{stats['engagement_measured'] * 100:.1f}%, {stats['engagement_angle_deg']:g} degrees); feed "
                     f"{stats['feed_mm_min']:,.0f} mm/min with chip thinning x{stats['chip_thinning']:.2f}")
        return lines
