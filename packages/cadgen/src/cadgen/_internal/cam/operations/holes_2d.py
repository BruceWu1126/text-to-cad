"""2.5D slots and round features: slotting, round pockets and bosses, helical bores and milled threads.

Each operation reads its own keys (``parse``, stdlib) and plans on the shared layer: the context (:mod:`.context`), the
slices (:mod:`.geometry2d`), the linking and entries (:mod:`.emit`). Curves (helices, spirals, circles, a slot's
round ends) are sampled with :func:`~cadgen._internal.cam.toolpath.sample_curve` at an eighth of the tolerance, so
:func:`~cadgen._internal.cam.toolpath.fit_arcs` writes them back as arcs, helical where Z changes. Climb milling
throughout (the material on the tool's right, the spindle turning clockwise): counter-clockwise inside a hole or a
slot, clockwise round a boss.

Where the tool cuts with its outer edge on the inside of a circle (a bore's helix, a round pocket's finishing
circle, an internal thread), the feed is the feed at that edge: the centre's feed is scaled by the centre's radius
over the edge's (never under :data:`ARC_FEED_FLOOR` of it), as a machinist compensates an internal arc.

``slot``
    Cuts a ``slot`` (a feature: open at one end or both) or a straight ``region`` (a rectangle or an obround, with
    ``depth_mm`` or ``z_mm``) along its centre line, full width, ``stepdown_mm`` (a quarter of the tool's diameter)
    per level. Each level starts at an open end where there is one (off the stock, so the tool plunges in air), else
    ramps in along the slot (``ramp_angle_deg``, 3). A tool narrower than the slot then widens it with rings round
    the slot, ``stepover`` (0.4 of the diameter) apart, out to the walls; ``finish_mm`` leaves that much on both walls
    for one ring at the final depth. A closed end is round (the slot's half width) or square (cut to the tool's
    radius, its corners left at it). A tool wider than the slot is refused.

``circular``
    A round pocket (a ``hole`` larger than the tool, or a ``region``'s largest inscribed circle) or a round boss (a
    cylindrical ``profile``, or a ``region``'s bounding circle with ``boss: true``), in ``stepdown_mm`` levels (half
    the diameter). A pocket enters each level with a helix at its centre, spirals out ``stepover`` (0.4) per turn and
    ends with a whole finishing circle. A boss clears the stock round it out to ``clear_mm`` from its wall (default:
    the whole stock round it, so the tool starts off the stock and plunges in air; else it enters along a lap of the
    outer circle), spiralling in to a whole finishing circle. Through holes go ``breakthrough_mm`` (0.5) past the
    bottom.

``bore``
    Helical interpolation of a ``hole`` to size: the tool's centre on a circle of the hole's radius less the tool's,
    down from the top of the material at ``pitch_mm`` per turn (default: 0.2 x the tool's diameter, and no steeper
    at the centre's circle than ``ramp_angle_deg``, 3, the steepest the tool goes down into material) to the bottom,
    a whole circle there, then over to the hole's axis and straight up. A ``pitch_mm`` steeper than that is refused
    (``unreachable``). Through holes go ``breakthrough_mm`` (0.5) past the bottom. ``target: counterbore`` bores the
    hole's counterbore to its diameter and depth instead. Refused when the tool does not fit, when it is within a
    tenth of its radius of the hole's size (drill or ream it), and when it would leave a core standing in the middle
    (a hole over twice the tool's diameter: use ``circular``).

``thread_mill``
    Mills an internal thread in a ``hole`` (the hole is the tap drill size, made by an earlier operation) or, with
    ``external: true``, an external thread round a cylindrical ``profile``, with a single-point thread mill:
    ``pitch_mm`` (required, and the tool's own), ``thread_depth_mm`` (default 0.6134 x pitch, the ISO metric thread's
    depth: the internal thread's major diameter is the hole's plus twice that, the external's minor the boss's less
    twice that), ``hand`` ``right`` (default) or ``left``. One helix, one turn per pitch, over the hole's or the boss's
    length (and ``breakthrough_mm``, 0.5, past a through hole's bottom), entered and left by quarter-circle leads; climb,
    so a right-hand internal thread is cut from the bottom up and a right-hand external one from the top down.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cadgen._internal.cam.operations.base import OperationBase, ParseContext, PlanError
from cadgen._internal.cam.operations.common import (
    feed_findings,
    floor_from,
    floor_keys,
    fraction,
    mm,
    ramp_angle,
    region_polygon,
    tool_diameter,
)
from cadgen._internal.cam.values import choice, flag, known, number

if TYPE_CHECKING:
    from cadgen._internal.cam.operations.context import PlanContext, Planned

__all__ = ["ARC_FEED_FLOOR", "BoreOperation", "BoreParams", "CircularOperation", "CircularParams",
           "ISO_THREAD_DEPTH", "SlotOperation", "SlotParams", "ThreadMillOperation", "ThreadParams"]

Point = tuple[float, float, float]

_END_MILLS = ("flat_endmill", "bull_endmill")
#: The least share of the feed an internal arc's compensation keeps.
ARC_FEED_FLOOR = 0.25
#: The ISO metric thread's depth, x pitch (ISO 68-1: h3 = 0.6134 P).
ISO_THREAD_DEPTH = 0.6134
#: A bore's default pitch, x the tool's diameter (no steeper than its ramp angle at the tool's centre).
BORE_PITCH = 0.2
#: How far past a through hole's bottom the round operations go by default, mm.
BREAKTHROUGH_MM = 0.5
#: How close to the hole's size a tool may be before interpolating it makes no sense, x the tool's radius.
SNUG = 0.1
#: How far off the stock an open slot's end runs, beyond the tool's radius, mm.
APPROACH_MM = 1.0


# --- shared ---------------------------------------------------------------------------------------------------

def _round_path(cx: float, cy: float, a0: float, sweep: float, radius, z, tol: float) -> list[Point]:
    """Points along a curve round ``(cx, cy)`` from angle ``a0`` turning ``sweep`` radians (+ counter-clockwise):
    ``radius(f)`` and ``z(f)`` for ``f`` from 0 to 1, sampled for :func:`fit_arcs` at half the tolerance."""
    from cadgen._internal.cam.toolpath import sample_curve

    def at(f: float):
        a = a0 + sweep * f
        rr = radius(f)
        return (cx + rr * math.cos(a), cy + rr * math.sin(a), z(f))

    return sample_curve(at, 0.0, 1.0, max_deflection=tol / 8, min_segments=max(4, math.ceil(abs(sweep) / (math.pi / 4))))


def _const(value: float):
    return lambda f: value


def _line(a: float, b: float):
    return lambda f: a + (b - a) * f


def _edge_feed(ctx: "PlanContext", centre_radius: float, edge_radius: float) -> float:
    """The feed for the tool's centre on an internal arc, so its edge moves at the operation's feed."""
    feed = ctx.feeds.feed_mm_min
    if edge_radius <= 0:
        return feed
    return feed * max(ARC_FEED_FLOOR, min(1.0, centre_radius / edge_radius))


def _breakthrough(raw: dict, ctx: ParseContext) -> float:
    return mm(raw, "breakthrough_mm", ctx.where, BREAKTHROUGH_MM, zero=True)


def _hole_floor(ctx: "PlanContext", found, depth, z, breakthrough: float) -> tuple[float, bool]:
    """A hole's floor (z) and whether it is through: its bottom (a through hole ``breakthrough`` below it), or
    ``z_mm`` / ``depth_mm``, never under a blind hole's bottom."""
    size = found.feature.size
    bottom = found.bottom[2]
    through = bool(size.get("through")) and bottom <= ctx.part_bottom_z + 1e-6
    floor = floor_from(ctx, depth, z, bottom - (breakthrough if through else 0.0),
                        deepest=None if through else bottom, what=f"the bottom of {found.feature.ref}")
    return floor, through


def _cylinder_of(ctx: "PlanContext", found) -> tuple[float, float, float]:
    """``(cx, cy, radius)`` of a round profile (a boss) in the setup's frame; ValueError when its walls are not one
    whole cylinder along the tool."""
    from cadgen._internal.cam.part import parallel

    feature = found.feature
    direction = tuple(float(c) for c in ctx.setup.direction)
    walls = [ctx.part.face(int(ref.rsplit(".f", 1)[1])) for ref in feature.refs]
    first = walls[0]
    round_ = all(w.surface == "cylinder" and not w.concave and parallel(w.axis, direction)
                 and abs(w.radius - first.radius) < 1e-6 for w in walls)
    if round_:
        centres = [ctx.to_setup(w.axis_point)[:2] for w in walls]
        round_ = all(math.dist(c, centres[0]) < 1e-6 for c in centres) and \
            sum(w.span for w in walls) >= 2 * math.pi - 1e-6
    if not round_:
        raise ValueError(f"{ctx.where}: {feature.ref} is not a round boss (its outline is not one whole cylinder "
                         "along the tool); cut it with contour_2d")
    cx, cy = ctx.to_setup(first.axis_point)[:2]
    return round(cx, 6), round(cy, 6), float(first.radius)


def _hole_size(ctx: "PlanContext", found, *, tool_radius: float, room: float, what: str) -> None:
    """Refuse a tool that does not fit round ``room`` (the centre's circle radius), or fits so snugly that
    interpolating it makes no sense."""
    if room < -1e-9:
        raise PlanError("tool_too_large", f"{ctx.where}: the {2 * tool_radius:g} mm tool does not fit in {what}")
    if room < max(SNUG * tool_radius, 0.02):
        raise PlanError("tool_too_large", f"{ctx.where}: the {2 * tool_radius:g} mm tool is within {max(room, 0):.3g} mm of {what}'s "
                         "size: there is no room to interpolate it; drill or ream it to size")


# --- slot -----------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SlotParams:
    stepdown_mm: float
    stepover: float
    finish_mm: float
    ramp_angle_deg: float
    depth_mm: float | None
    z_mm: float | None


@dataclass(frozen=True)
class _Slot:
    ref: str
    centre: tuple[float, float]
    #: The centre line's direction (unit, x y), and the slot's width and floor.
    u: tuple[float, float]
    width: float
    floor: float
    #: Each end (back along -u, then on along +u): ``(kind, distance)``, kind ``open`` (the distance is where the tool's
    #: centre ends, off the stock), ``round`` or ``square`` (the distance is to the end wall).
    ends: tuple[tuple[str, float], tuple[str, float]]


class SlotOperation(OperationBase):
    name, phase, word = "slot", 1, "Slot"
    keys = frozenset({"stepdown_mm", "stepover", "finish_mm", "ramp_angle_deg", "depth_mm", "z_mm"})
    tool_types = _END_MILLS
    targets = ("features", "region")

    def parse(self, raw: dict, ctx: ParseContext) -> SlotParams:
        known(raw, self.keys, where=ctx.where, takes=f"slot takes {', '.join(sorted(self.keys))}")
        depth, z = floor_keys(raw, ctx.where)
        return SlotParams(mm(raw, "stepdown_mm", ctx.where, tool_diameter(ctx) / 4),
                          fraction(raw, "stepover", ctx.where, 0.4), mm(raw, "finish_mm", ctx.where, 0.0, zero=True),
                          ramp_angle(raw, ctx.where), depth, z)

    # --- the slots ----------------------------------------------------------------------------------------

    def _slots(self, params: SlotParams, ctx: "PlanContext") -> list[_Slot]:
        if ctx.operation.region is not None:
            return [self._from_region(params, ctx)]
        return [self._from_feature(params, ctx, ref, found) for ref, found in ctx.targets(("slot",))]

    @staticmethod
    def _from_region(params: SlotParams, ctx: "PlanContext") -> _Slot:
        from cadgen._internal.cam.operations import geometry2d as g2

        poly = region_polygon(ctx)
        rect = poly.minimum_rotated_rectangle
        (x0, y0), (x1, y1), (x2, y2) = list(rect.exterior.coords)[:3]
        a, b = math.hypot(x1 - x0, y1 - y0), math.hypot(x2 - x1, y2 - y1)
        (dx, dy), length, width = ((x1 - x0, y1 - y0), a, b) if a >= b else ((x2 - x1, y2 - y1), b, a)
        u = _canonical(dx / length, dy / length)
        if poly.symmetric_difference(rect).area > 0.25 * width * width + 1e-6:
            raise ValueError(f"{ctx.where}.region: a slot is straight; draw it as a rectangle or an obround round its "
                             "centre line")
        kind = "round" if rect.area - poly.area > 0.1 * width * width else "square"
        floor = floor_from(ctx, params.depth_mm, params.z_mm, None, deepest=None, what="")
        if poly.intersection(g2.shadow(ctx, floor)).area > 1e-6:
            raise PlanError("gouge", f"{ctx.where}.region: the slot runs into the part above z {floor:g}")
        centre = (round(rect.centroid.x, 6), round(rect.centroid.y, 6))
        return _Slot("region", centre, u, width, floor, ((kind, length / 2), (kind, length / 2)))

    @staticmethod
    def _from_feature(params: SlotParams, ctx: "PlanContext", ref: str, found) -> _Slot:
        from shapely.geometry import LineString, Point as ShapelyPoint

        from cadgen._internal.cam.operations import geometry2d as g2

        feature = found.feature
        walls = [ctx.part.face(int(r.rsplit(".f", 1)[1])) for r in feature.refs[1:]]
        flat = sorted((w for w in walls if w.surface == "plane"), key=lambda w: (-w.area, w.ordinal))
        if not flat:
            raise PlanError("unreachable", f"{ctx.where}: {ref} has no flat wall to run along")
        origin = ctx.to_setup(flat[0].center)
        tip = ctx.to_setup(tuple(c + n for c, n in zip(flat[0].center, flat[0].normal)))
        nx, ny = tip[0] - origin[0], tip[1] - origin[1]
        norm = math.hypot(nx, ny)
        u = _canonical(-ny / norm, nx / norm)
        width = float(feature.size["width_mm"])
        floor = floor_from(ctx, params.depth_mm, params.z_mm, found.bottom[2], deepest=found.bottom[2],
                            what=f"the floor of {ref}")
        cx, cy = found.bottom[0], found.bottom[1]
        material = g2.section(ctx, (found.bottom[2] + found.top[2]) / 2)
        stock = g2.stock_outline(ctx)
        (sx0, sy0, _), (sx1, sy1, _) = ctx.setup.stock_bounds
        far = 2 * (sx1 - sx0 + sy1 - sy0)
        nx, ny = -u[1], u[0]
        ends = []
        for sign in (-1.0, 1.0):
            hits = []
            for side in (0.0, 0.9, -0.9):
                ox, oy = cx + side * width / 2 * nx, cy + side * width / 2 * ny
                ray = LineString([(ox, oy), (ox + sign * far * u[0], oy + sign * far * u[1])])
                hit = ray.intersection(material)
                hits.append(None if hit.is_empty else ShapelyPoint(ox, oy).distance(hit))
            if hits[0] is None:
                ray = LineString([(cx, cy), (cx + sign * far * u[0], cy + sign * far * u[1])])
                inside = ray.intersection(stock)
                out = max([0.0] + [sign * ((x - cx) * u[0] + (y - cy) * u[1])
                                   for piece in g2.polygons(inside.buffer(1e-9)) for x, y in piece.exterior.coords])
                ends.append(("open", out + ctx.radius + APPROACH_MM))
                continue
            sides = [h for h in hits[1:] if h is not None]
            kind = "round" if sides and hits[0] - min(sides) > 0.25 * width / 2 else "square"
            ends.append((kind, hits[0]))
        return _Slot(ref, (round(cx, 6), round(cy, 6)), u, width, floor, (ends[0], ends[1]))

    # --- planning -----------------------------------------------------------------------------------------

    def plan(self, params: SlotParams, ctx: "PlanContext") -> "Planned":
        from cadgen._internal.cam.operations import geometry2d as g2
        from cadgen._internal.cam.operations.context import Planned
        from cadgen._internal.cam.operations.emit import Emitter, entry_for

        r, tol = ctx.radius, ctx.tol
        stl = ctx.operation.stock_to_leave_mm
        emitter = Emitter(ctx)
        stats: dict = {"slots": []}
        for slot in self._slots(params, ctx):
            if 2 * (r + stl) > slot.width + tol:
                raise PlanError("tool_too_large", f"{ctx.where}: the {2 * r:g} mm tool is wider than {slot.ref}, a {slot.width:g} mm "
                                 "slot" + (f" less {stl:g} mm stock to leave on each wall" if stl else ""))
            # Start at an open end where there is one: the tool plunges in air there.
            back, on = slot.ends
            u = slot.u
            if on[0] == "open" and back[0] != "open":
                back, on, u = on, back, (-u[0], -u[1])
            a = self._end(slot, back, -1.0, u, r, stl)
            b = self._end(slot, on, 1.0, u, r, stl)
            if (b[0] - a[0]) * u[0] + (b[1] - a[1]) * u[1] < 1e-3:
                raise PlanError("tool_too_large", f"{ctx.where}: {slot.ref} is no longer than it is wide for the {2 * r:g} mm tool; "
                                 "cut it with circular or bore")
            final = slot.width / 2 - r - stl
            rough = final - params.finish_mm if final > tol else 0.0
            step = params.stepover * 2 * r
            count = math.ceil(rough / step - 1e-9) if rough > tol else 0
            rings = [rough * k / count for k in range(1, count + 1)]
            levels = g2.depth_steps(ctx.top_z, slot.floor, params.stepdown_mm)
            if not levels:
                raise PlanError("empty_operation", f"{ctx.where}: {slot.ref} has nothing to cut: its floor at z {slot.floor:g} is not "
                                 f"under the top of the material at z {ctx.top_z:g}")
            previous = ctx.top_z
            for index, z in enumerate(levels):
                path = [(a[0], a[1], z), (b[0], b[1], z)]
                entry, cut = entry_for(ctx, path, angle_deg=params.ramp_angle_deg)
                emitter.go(cut, rapid_to=previous, entry=entry)
                widths = rings + ([final] if params.finish_mm > 0 and final > tol and index == len(levels) - 1
                                  and final - (rings[-1] if rings else 0.0) > 1e-6 else [])
                for h in widths:
                    emitter.cut(self._ring(a, b, u, h, back[0] == "round", on[0] == "round", z, tol))
                previous = z
            cut_width = 2 * (final + r) if final > tol else 2 * r
            stats["slots"].append({"ref": slot.ref, "width_mm": round(cut_width, 6), "floor_z": round(slot.floor, 6),
                                   "levels": [round(z, 6) for z in levels], "ends": [back[0], on[0]],
                                   "rings": len(rings), "finish_mm": params.finish_mm})
        return Planned(emitter.toolpath(), stats, feed_findings(ctx))

    @staticmethod
    def _end(slot: _Slot, end: tuple[str, float], sign: float, u, r: float, stl: float) -> tuple[float, float]:
        """Where the tool's centre line ends at one end: off the stock at an open end, at a round end's centre, the
        tool's radius (and the stock to leave) short of a square end's wall."""
        kind, distance = end
        if kind == "round":
            distance -= slot.width / 2
        elif kind == "square":
            distance -= r + stl
        return (round(slot.centre[0] + sign * distance * u[0], 6), round(slot.centre[1] + sign * distance * u[1], 6))

    @staticmethod
    def _ring(a, b, u, h: float, round_a: bool, round_b: bool, z: float, tol: float) -> list[Point]:
        """A counter-clockwise ring ``h`` from the centre line ``a`` to ``b`` (round round a round end, straight across
        any other), starting beside ``b`` on the right."""
        rx, ry = u[1], -u[0]
        start = (b[0] + h * rx, b[1] + h * ry, z)
        out = [start]
        right = math.atan2(ry, rx)
        if round_b:
            out += _round_path(b[0], b[1], right, math.pi, _const(h), _const(z), tol)[1:]
        else:
            out.append((b[0] - h * rx, b[1] - h * ry, z))
        out.append((a[0] - h * rx, a[1] - h * ry, z))
        if round_a:
            out += _round_path(a[0], a[1], right + math.pi, math.pi, _const(h), _const(z), tol)[1:]
        else:
            out.append((a[0] + h * rx, a[1] + h * ry, z))
        out.append(start)
        return out

    def human_lines(self, stats: dict) -> list[str]:
        return [f"{s['ref']}: {s['width_mm']:g} mm wide in {len(s['levels'])} level(s) to z {s['floor_z']:g}, ends "
                f"{' and '.join(s['ends'])}" + (f", {s['rings']} widening ring(s)" if s["rings"] else "")
                + (f", walls finished from {s['finish_mm']:g} mm" if s["finish_mm"] else "") for s in stats["slots"]]


def _canonical(x: float, y: float) -> tuple[float, float]:
    """A direction pointing its larger component positive, so a line has one direction whatever the faces say."""
    if abs(x) > abs(y) + 1e-9 and x < 0 or abs(x) <= abs(y) + 1e-9 and y < 0:
        x, y = -x, -y
    return (round(x, 12) + 0.0, round(y, 12) + 0.0)


# --- circular -------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class CircularParams:
    boss: bool | None
    stepdown_mm: float
    stepover: float
    ramp_angle_deg: float
    clear_mm: float | None
    breakthrough_mm: float
    depth_mm: float | None
    z_mm: float | None


class CircularOperation(OperationBase):
    name, phase, word = "circular", 1, "Round pocket or boss"
    keys = frozenset({"boss", "stepdown_mm", "stepover", "ramp_angle_deg", "clear_mm", "breakthrough_mm", "depth_mm",
                      "z_mm"})
    tool_types = _END_MILLS
    targets = ("features", "region")
    kinds = ("hole", "profile")

    def parse(self, raw: dict, ctx: ParseContext) -> CircularParams:
        known(raw, self.keys, where=ctx.where, takes=f"circular takes {', '.join(sorted(self.keys))}")
        boss = flag(raw["boss"], where=f"{ctx.where}.boss") if "boss" in raw else None
        clear = mm(raw, "clear_mm", ctx.where)
        if clear is not None and boss is False:
            raise ValueError(f"{ctx.where}.clear_mm: how far round a boss to clear; a pocket has none")
        depth, z = floor_keys(raw, ctx.where)
        return CircularParams(boss, mm(raw, "stepdown_mm", ctx.where, tool_diameter(ctx) / 2),
                              fraction(raw, "stepover", ctx.where, 0.4), ramp_angle(raw, ctx.where), clear,
                              _breakthrough(raw, ctx), depth, z)

    def _targets(self, params: CircularParams, ctx: "PlanContext") -> list[dict]:
        """``{ref, boss, cx, cy, radius, floor}`` per target."""
        import shapely

        if ctx.operation.region is not None:
            poly = region_polygon(ctx)
            floor = floor_from(ctx, params.depth_mm, params.z_mm, None, deepest=None, what="")
            if params.boss:
                circle = shapely.minimum_bounding_circle(poly)
                radius = shapely.minimum_bounding_radius(poly)
                cx, cy = circle.centroid.x, circle.centroid.y
            else:
                line = shapely.maximum_inscribed_circle(poly, ctx.tol / 4)
                (cx, cy), edge = line.coords[0], line.coords[1]
                radius = math.dist((cx, cy), edge)
            return [{"ref": "region", "boss": bool(params.boss), "cx": round(cx, 6), "cy": round(cy, 6),
                     "radius": radius, "floor": floor, "region": True}]
        out = []
        for ref, found in ctx.targets(self.kinds):
            if found.feature.kind == "hole":
                if params.boss:
                    raise ValueError(f"{ctx.where}.boss: {ref} is a hole; a boss is a round profile")
                floor, _ = _hole_floor(ctx, found, params.depth_mm, params.z_mm, params.breakthrough_mm)
                out.append({"ref": ref, "boss": False, "cx": round(found.top[0], 6), "cy": round(found.top[1], 6),
                            "radius": found.feature.size["diameter_mm"] / 2, "floor": floor, "region": False})
                continue
            if params.boss is False:
                raise ValueError(f"{ctx.where}.boss: {ref} is an outline; circular cuts round it as a boss")
            cx, cy, radius = _cylinder_of(ctx, found)
            bottom = found.bottom[2]
            through = bottom <= ctx.part_bottom_z + 1e-6
            floor = floor_from(ctx, params.depth_mm, params.z_mm, bottom - (params.breakthrough_mm if through else 0),
                                deepest=None if through else bottom, what=f"the foot of {ref}")
            out.append({"ref": ref, "boss": True, "cx": cx, "cy": cy, "radius": radius, "floor": floor,
                        "region": False})
        return out

    def plan(self, params: CircularParams, ctx: "PlanContext") -> "Planned":
        from cadgen._internal.cam.operations import geometry2d as g2
        from cadgen._internal.cam.operations.context import Planned
        from cadgen._internal.cam.operations.emit import Emitter

        emitter = Emitter(ctx)
        stats: dict = {"targets": []}
        for target in self._targets(params, ctx):
            levels = g2.depth_steps(ctx.top_z, target["floor"], params.stepdown_mm)
            if not levels:
                raise PlanError("empty_operation", f"{ctx.where}: {target['ref']} has nothing to cut: its bottom at z "
                                 f"{target['floor']:g} is not under the top of the material at z {ctx.top_z:g}")
            if target["boss"]:
                done = self._boss(params, ctx, emitter, target, levels)
            else:
                done = self._pocket(params, ctx, emitter, target, levels)
            stats["targets"].append({"ref": target["ref"], "kind": "boss" if target["boss"] else "pocket",
                                     "diameter_mm": round(2 * target["radius"], 6),
                                     "floor_z": round(target["floor"], 6), "levels": [round(z, 6) for z in levels],
                                     **done})
        return Planned(emitter.toolpath(), stats, feed_findings(ctx))

    @staticmethod
    def _pocket(params: CircularParams, ctx, emitter, target: dict, levels: list[float]) -> dict:
        from shapely.geometry import Point as ShapelyPoint

        from cadgen._internal.cam.operations import geometry2d as g2
        from cadgen._internal.cam.operations.emit import HELIX_FRACTION, in_air
        from cadgen._internal.cam.toolpath import PLUNGE, Helix

        r, tol = ctx.radius, ctx.tol
        cx, cy, radius = target["cx"], target["cy"], target["radius"]
        what = f"{target['ref']} ({2 * radius:g} mm)"
        last = radius - r - ctx.operation.stock_to_leave_mm
        _hole_size(ctx, None, tool_radius=r, room=last, what=what)
        if target["region"] and ShapelyPoint(cx, cy).buffer(radius).intersection(
                g2.shadow(ctx, target["floor"])).area > 1e-6:
            raise PlanError("gouge", f"{ctx.where}.region: the round pocket runs into the part above z {target['floor']:g}")
        first = min(HELIX_FRACTION * r, last)
        step = params.stepover * 2 * r
        turns = math.ceil((last - first) / step - 1e-9) if last - first > 1e-6 else 0
        previous = ctx.top_z
        for z in levels:
            spiral = [(cx + first, cy, z)]
            if turns:
                spiral = _round_path(cx, cy, 0.0, 2 * math.pi * turns, _line(first, last), _const(z), tol)
            circle = _round_path(cx, cy, 0.0, 2 * math.pi, _const(last), _const(z), tol)
            entry = PLUNGE if in_air(ctx, spiral[0][:2]) else Helix((cx, cy), params.ramp_angle_deg)
            emitter.go(spiral, rapid_to=previous, entry=entry)
            emitter.cut(circle[1:], feed=_edge_feed(ctx, last, last + r))
            previous = z
        return {"turns": turns, "diameter_cut_mm": round(2 * (last + r), 6)}

    @staticmethod
    def _boss(params: CircularParams, ctx, emitter, target: dict, levels: list[float]) -> dict:
        from shapely.geometry import Point as ShapelyPoint

        from cadgen._internal.cam.operations import geometry2d as g2
        from cadgen._internal.cam.operations.emit import AlongPath, in_air
        from cadgen._internal.cam.toolpath import PLUNGE

        r, tol = ctx.radius, ctx.tol
        stl = ctx.operation.stock_to_leave_mm
        cx, cy, radius = target["cx"], target["cy"], target["radius"]
        last = radius + r + stl
        if params.clear_mm is not None:
            outer = radius + params.clear_mm
        else:
            outer = max(math.dist((cx, cy), xy) for piece in g2.polygons(g2.stock_outline(ctx))
                        for xy in piece.exterior.coords)
        first = max(outer + r, last)
        reach = ShapelyPoint(cx, cy).buffer(first + r, quad_segs=64)
        boss = ShapelyPoint(cx, cy).buffer(radius + tol, quad_segs=64)
        other = g2.shadow(ctx, target["floor"]).intersection(reach).difference(boss)
        if other.area > 1e-6:
            raise PlanError("unreachable", f"{ctx.where}: other parts of the part stand within {first + r - radius:.4g} mm of "
                             f"{target['ref']} above z {target['floor']:g}; give clear_mm to clear less round it")
        step = params.stepover * 2 * r
        turns = math.ceil((first - last) / step - 1e-9) if first - last > 1e-6 else 0
        previous = ctx.top_z
        for z in levels:
            ring = _round_path(cx, cy, 0.0, -2 * math.pi, _const(first), _const(z), tol)
            spiral = _round_path(cx, cy, 0.0, -2 * math.pi * turns, _line(first, last), _const(z), tol) if turns else []
            circle = _round_path(cx, cy, 0.0, -2 * math.pi, _const(last), _const(z), tol)
            if in_air(ctx, ring[0][:2]):
                emitter.go(spiral or circle, rapid_to=previous, entry=PLUNGE)
            else:
                emitter.go(ring, rapid_to=previous, entry=AlongPath(params.ramp_angle_deg))
                emitter.cut(spiral[1:])
            emitter.cut(circle[1:])
            previous = z
        return {"turns": turns, "diameter_cut_mm": round(2 * (last - r), 6), "cleared_to_mm": round(first + r, 6)}

    def human_lines(self, stats: dict) -> list[str]:
        return [f"{t['ref']}: {t['kind']} {t['diameter_cut_mm']:g} mm in {len(t['levels'])} level(s) to z "
                f"{t['floor_z']:g}" for t in stats["targets"]]


# --- bore -----------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class BoreParams:
    pitch_mm: float | None
    target: str              # hole | counterbore
    breakthrough_mm: float
    depth_mm: float | None
    z_mm: float | None
    ramp_angle_deg: float


class BoreOperation(OperationBase):
    name, phase, word = "bore", 1, "Helical bore"
    keys = frozenset({"pitch_mm", "target", "breakthrough_mm", "depth_mm", "z_mm", "ramp_angle_deg"})
    tool_types = _END_MILLS

    def parse(self, raw: dict, ctx: ParseContext) -> BoreParams:
        known(raw, self.keys, where=ctx.where, takes=f"bore takes {', '.join(sorted(self.keys))}")
        target = choice(raw.get("target", "hole"), ("hole", "counterbore"), where=f"{ctx.where}.target")
        depth, z = floor_keys(raw, ctx.where)
        if target == "counterbore" and (depth is not None or z is not None):
            raise ValueError(f"{ctx.where}: a counterbore is bored to its own depth; leave depth_mm and z_mm out")
        return BoreParams(mm(raw, "pitch_mm", ctx.where), target, _breakthrough(raw, ctx), depth, z,
                          ramp_angle(raw, ctx.where))

    def plan(self, params: BoreParams, ctx: "PlanContext") -> "Planned":
        from cadgen._internal.cam.operations.context import Planned
        from cadgen._internal.cam.operations.emit import Emitter

        r, tol = ctx.radius, ctx.tol
        stl = ctx.operation.stock_to_leave_mm
        emitter = Emitter(ctx)
        stats: dict = {"holes": []}
        for ref, found in ctx.targets(("hole",)):
            size = found.feature.size
            if params.target == "counterbore":
                if "counterbore" not in size:
                    raise PlanError("empty_operation", f"{ctx.where}.target: {ref} has no counterbore to bore")
                radius = size["counterbore"]["diameter_mm"] / 2
                floor = found.top[2] - size["counterbore"]["depth_mm"]
                what = f"{ref}'s {2 * radius:g} mm counterbore"
            else:
                radius = size["diameter_mm"] / 2
                floor, _ = _hole_floor(ctx, found, params.depth_mm, params.z_mm, params.breakthrough_mm)
                what = f"{ref} ({2 * radius:g} mm)"
            room = radius - r - stl
            _hole_size(ctx, found, tool_radius=r, room=room, what=what)
            if room > r + tol:
                raise PlanError("tool_too_small", f"{ctx.where}: boring {what} with the {2 * r:g} mm tool would leave a core "
                                 f"{2 * (room - r):.3g} mm across standing in its middle; use circular, which spirals out")
            top = ctx.top_z
            if floor >= top - 1e-9:
                raise PlanError("empty_operation", f"{ctx.where}: {ref} has nothing to bore: its bottom at z {floor:g} is not under the "
                                 f"top of the material at z {top:g}")
            steepest = 2 * math.pi * room * math.tan(math.radians(params.ramp_angle_deg))
            pitch = params.pitch_mm
            if pitch is None:
                pitch = math.floor(min(BORE_PITCH * 2 * r, steepest) * 1e4) / 1e4
            elif pitch > steepest + 1e-9:
                angle = math.degrees(math.atan(pitch / (2 * math.pi * room)))
                raise PlanError("unreachable", f"{ctx.where}.pitch_mm: {pitch:g} mm per turn round {what} is a "
                                f"{angle:.3g} degree helix at the tool's centre, steeper than ramp_angle_deg "
                                f"{params.ramp_angle_deg:g} lets the tool go down into material; give pitch_mm "
                                f"{math.floor(steepest * 1e4) / 1e4:g} or less, or a larger ramp_angle_deg")
            cx, cy = round(found.top[0], 6), round(found.top[1], 6)
            sweep = 2 * math.pi * (top - floor) / pitch
            helix = _round_path(cx, cy, 0.0, sweep, _const(room), _line(top, floor), tol)
            circle = _round_path(cx, cy, sweep, 2 * math.pi, _const(room), _const(floor), tol)
            emitter.go([helix[0]], rapid_to=top)
            emitter.cut(helix[1:] + circle[1:], feed=_edge_feed(ctx, room, room + r))
            emitter.cut([(cx, cy, floor)])          # off the wall to the axis; the toolpath lifts straight up
            stats["holes"].append({"ref": ref, "target": params.target, "diameter_mm": round(2 * (room + r), 6),
                                   "pitch_mm": pitch, "turns": round(sweep / (2 * math.pi), 6),
                                   "floor_z": round(floor, 6)})
        return Planned(emitter.toolpath(), stats, feed_findings(ctx))

    def human_lines(self, stats: dict) -> list[str]:
        return [f"{h['ref']}: {h['target']} bored to {h['diameter_mm']:g} mm, {h['pitch_mm']:g} mm per turn to z "
                f"{h['floor_z']:g}" for h in stats["holes"]]


# --- thread_mill ----------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ThreadParams:
    pitch_mm: float
    thread_depth_mm: float
    hand: str                # right | left
    external: bool
    breakthrough_mm: float


class ThreadMillOperation(OperationBase):
    name, phase, word = "thread_mill", 1, "Thread mill"
    keys = frozenset({"pitch_mm", "thread_depth_mm", "hand", "external", "breakthrough_mm"})
    tool_types = ("thread_mill",)

    def parse(self, raw: dict, ctx: ParseContext) -> ThreadParams:
        known(raw, self.keys, where=ctx.where, takes=f"thread_mill takes {', '.join(sorted(self.keys))}")
        if "pitch_mm" not in raw:
            raise ValueError(f"{ctx.where}.pitch_mm: the thread's pitch, mm per turn (1.5 for M10), is required")
        pitch = number(raw["pitch_mm"], where=f"{ctx.where}.pitch_mm", positive=True)
        own = float(ctx.tool.size["pitch_mm"])
        if abs(own - pitch) > 1e-9:
            raise ValueError(f"{ctx.where}.pitch_mm: {ctx.tool.id} is a thread mill for a {own:g} mm pitch, not "
                             f"{pitch:g} mm; name a thread mill ground for that pitch")
        depth = mm(raw, "thread_depth_mm", ctx.where, round(ISO_THREAD_DEPTH * pitch, 6))
        hand = choice(raw.get("hand", "right"), ("right", "left"), where=f"{ctx.where}.hand")
        external = flag(raw["external"], where=f"{ctx.where}.external") if "external" in raw else False
        return ThreadParams(pitch, depth, hand, external, _breakthrough(raw, ctx))

    def plan(self, params: ThreadParams, ctx: "PlanContext") -> "Planned":
        from cadgen._internal.cam.operations import geometry2d as g2
        from cadgen._internal.cam.operations.context import Planned
        from cadgen._internal.cam.operations.emit import Emitter, lead_arc

        r, tol, pitch, depth = ctx.radius, ctx.tol, params.pitch_mm, params.thread_depth_mm
        emitter = Emitter(ctx)
        stats: dict = {"threads": []}
        for ref, found in ctx.targets(("profile",) if params.external else ("hole",)):
            size = found.feature.size
            if params.external:
                cx, cy, radius = _cylinder_of(ctx, found)
                bottom = found.bottom[2]
                through = bottom <= ctx.part_bottom_z + 1e-6
                low = bottom - (params.breakthrough_mm if through else 0.0)
                high = found.top[2]
                room = radius - depth + r                 # the tool's centre, round the boss
                lead = r
            else:
                cx, cy = round(found.top[0], 6), round(found.top[1], 6)
                radius = size["diameter_mm"] / 2
                if r >= radius - tol:
                    raise PlanError("tool_too_large", f"{ctx.where}: the {2 * r:g} mm thread mill does not fit in {ref} "
                                     f"({2 * radius:g} mm)")
                low, _ = _hole_floor(ctx, found, None, None, params.breakthrough_mm)
                high = found.top[2] - (size["counterbore"]["depth_mm"] if "counterbore" in size else 0.0)
                room = radius + depth - r                 # the tool's centre, inside the hole
                lead = room / 2
            turns = max(1, math.ceil((high - low) / pitch - 1e-9))
            # Climb: counter-clockwise inside a hole, clockwise round a boss. A right-hand helix rises turning
            # counter-clockwise, so a right-hand internal thread is cut upward and a right-hand external one downward.
            ccw = not params.external
            up = (params.hand == "right") == ccw
            z0, z1 = (low, low + turns * pitch) if up else (low + turns * pitch, low)
            helix = _round_path(cx, cy, 0.0, (1 if ccw else -1) * 2 * math.pi * turns, _const(room), _line(z0, z1), tol)
            lead_in = lead_arc(helix, lead, at_end=False, air_left=True, tol=tol)
            lead_out = lead_arc(helix, lead, at_end=True, air_left=True, tol=tol)
            if params.external:
                material = g2.shadow(ctx, min(z0, z1))
                for point in (lead_in[0], lead_out[-1]):
                    if not material.is_empty and material.distance(_xy_point(point)) < r - tol:
                        raise PlanError("unreachable", f"{ctx.where}: the part stands in the way of the thread mill's lead round "
                                         f"{ref}; it needs {r:g} mm of room round the boss")
                emitter.go(lead_in, rapid_to=ctx.top_z)
                emitter.cut(helix[1:] + lead_out[1:])
            else:
                emitter.go([(cx, cy, z0)], rapid_to=ctx.top_z)
                emitter.cut(lead_in + helix[1:] + lead_out[1:], feed=_edge_feed(ctx, room, room + r))
                emitter.cut([(cx, cy, z1)])
            diameter = 2 * (room - r) if params.external else 2 * (room + r)
            stats["threads"].append({
                "ref": ref, "external": params.external, "hand": params.hand, "pitch_mm": pitch,
                "thread_depth_mm": depth, ("minor_diameter_mm" if params.external else "major_diameter_mm"):
                round(diameter, 6), "turns": turns, "from_z": round(z0, 6), "to_z": round(z1, 6)})
        return Planned(emitter.toolpath(), stats, feed_findings(ctx))

    def human_lines(self, stats: dict) -> list[str]:
        lines = []
        for t in stats["threads"]:
            kind, size = ("external", t["minor_diameter_mm"]) if t["external"] else ("internal", t["major_diameter_mm"])
            word = "minor" if t["external"] else "major"
            lines.append(f"{t['ref']}: {t['hand']}-hand {kind} thread, {t['pitch_mm']:g} mm pitch, {word} diameter "
                         f"{size:g} mm, {t['turns']} turn(s) from z {t['from_z']:g} to z {t['to_z']:g}")
        return lines


def _xy_point(point):
    from shapely.geometry import Point as ShapelyPoint

    return ShapelyPoint(point[0], point[1])
