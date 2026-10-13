"""2.5D milling: facing, profiles and pockets, cut in flat passes stepping down.

Each operation reads its own keys (``parse``, stdlib), and plans (``plan``) on the shared layer: the context
(:mod:`.context`), the slices and offsets (:mod:`.geometry2d`) and the linking and entries (:mod:`.emit`). Climb
milling throughout (the material on the tool's right, the spindle turning clockwise).

``facing``
    Flattens the stock's top (``all``, the default) or a ``region`` down to the part's top, or to ``z_mm`` (setup
    frame) or ``depth_mm`` below the stock top. Rows across the setup's X, ``stepover`` apart (a fraction of the
    tool's diameter: 0.7 for a face mill, 0.4 for an end mill), each running off the stock at both ends so the tool
    enters in air; ``pattern`` ``zigzag`` (the default: back and forth, staying down where the turn is off the stock)
    or ``one_way`` (lift and come back, every row cut the same way). ``stepdown_mm`` (1 mm) per level, the last
    exactly the floor; ``finish_mm`` leaves that much for a last pass on its own. Refused when the floor would cut
    the part.

``contour_2d``
    Cuts round an outline: a ``profile`` (or ``all: true``: every outline the setup reaches), a ``pocket`` or
    ``hole`` (from inside), or a ``region``. ``side`` is ``outside`` (a profile's and a region's default),
    ``inside`` (a pocket's or a hole's) or ``on`` (the tool's centre on the outline). Down to the feature's bottom
    (a through outline ``breakthrough_mm`` below it), or ``z_mm`` / ``depth_mm`` below the material top (a region
    needs one), in ``stepdown_mm`` levels (half the tool's diameter). ``finish_passes`` passes at the final depth
    take off the last ``finish_mm`` (equal shares; with no ``finish_mm`` they are spring passes). ``tabs``
    ``{count, width_mm, height_mm}`` (width the tool's diameter but at least 3 mm, height 1 mm) leave that many
    bridges, evenly round each ring, ``height_mm`` tall at the bottom: the passes under their top lift over them.
    ``lead_radius_mm`` (the tool's radius; 0 for none) is a quarter-circle lead on and off each pass from the air
    side, used where it stays clear of the part and the lead on starts off the stock. Elsewhere a pass enters along
    itself: a helix inside a pocket or hole where one fits, else a ramp along its first straight run, else laps of
    the ring sloping down; ``ramp_angle_deg`` (3) is the steepest any of them goes.

``pocket_2d``
    Clears a ``pocket`` (islands left standing) or a ``region`` down to its floor (or ``z_mm`` / ``depth_mm``):
    ``pattern`` ``offset`` (the default: rings offset from the walls inward by ``stepover``, 0.4 of the diameter,
    cut from the innermost out, each ring joined to the next at depth) or ``zigzag`` (rows, then a ring round the
    walls). It enters each level with a helix inside the pocket (a ramp where none fits), from the level above.
    ``finish_mm`` leaves that much on the walls for one ring at the final depth; ``finish_floor_mm`` that much on
    the floor for a last level of its own (its outer ring finishes the walls too). Inside corners tighter than the
    tool's radius are left with the tool's radius: ``stats["corner_left"]`` lists them for the checks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from cadgen._internal.cam.operations.base import OperationBase, ParseContext
from cadgen._internal.cam.values import choice, known, number, obj, whole

if TYPE_CHECKING:
    from cadgen._internal.cam.operations.context import PlanContext, Planned

__all__ = ["Contour2dOperation", "ContourParams", "FacingOperation", "FacingParams", "Pocket2dOperation",
           "PocketParams", "Tabs", "facing_floor"]

_END_MILLS = ("flat_endmill", "bull_endmill")


# --- shared parsing ---------------------------------------------------------------------------------------------

def _fraction(raw: dict, key: str, ctx: ParseContext, default: float) -> float:
    """A fraction of the tool's diameter, over 0 and up to 1."""
    if key not in raw:
        return default
    value = number(raw[key], where=f"{ctx.where}.{key}", positive=True)
    if value > 1:
        raise ValueError(f"{ctx.where}.{key}: a fraction of the tool's diameter, over 0 and up to 1, got {value:g}")
    return value


def _mm(raw: dict, key: str, ctx: ParseContext, default: float | None = None, *, zero: bool = False) -> float | None:
    if key not in raw:
        return default
    if zero:
        return number(raw[key], where=f"{ctx.where}.{key}", minimum=0)
    return number(raw[key], where=f"{ctx.where}.{key}", positive=True)


def _floor_keys(raw: dict, ctx: ParseContext) -> tuple[float | None, float | None]:
    """``depth_mm`` (below the material top) or ``z_mm`` (setup frame), at most one."""
    if "depth_mm" in raw and "z_mm" in raw:
        raise ValueError(f"{ctx.where}: give depth_mm (below the top of the material) or z_mm (in the setup's frame), "
                         "not both")
    depth = _mm(raw, "depth_mm", ctx)
    z = number(raw["z_mm"], where=f"{ctx.where}.z_mm") if "z_mm" in raw else None
    return depth, z


def _angle(raw: dict, ctx: ParseContext) -> float:
    from cadgen._internal.cam.operations.emit import DEFAULT_RAMP_DEG

    if "ramp_angle_deg" not in raw:
        return DEFAULT_RAMP_DEG
    return number(raw["ramp_angle_deg"], where=f"{ctx.where}.ramp_angle_deg", positive=True, below=90)


def _diameter(ctx: ParseContext) -> float:
    return float(ctx.tool.diameter_mm)


def _stepdown(raw: dict, ctx: ParseContext, default: float) -> float:
    return _mm(raw, "stepdown_mm", ctx, default)


def _region_polygon(ctx: "PlanContext"):
    from shapely.geometry import Polygon

    poly = Polygon(ctx.operation.region)
    if not poly.is_valid:
        raise ValueError(f"{ctx.where}.region: the outline crosses itself")
    return poly


def _floor_from(ctx: "PlanContext", depth: float | None, z: float | None, default: float | None, *,
                deepest: float | None, what: str) -> float:
    """The floor z: ``z_mm``, ``depth_mm`` below the material top, else ``default``; never under ``deepest``."""
    if z is not None:
        floor = z
    elif depth is not None:
        floor = ctx.top_z - depth
    elif default is not None:
        floor = default
    else:
        raise ValueError(f"{ctx.where}: a region has no depth of its own; give depth_mm (below the top of the "
                         "material) or z_mm (in the setup's frame)")
    if deepest is not None and floor < deepest - 1e-6:
        raise ValueError(f"{ctx.where}: cutting to z {floor:g} goes {deepest - floor:g} mm under {what} at z "
                         f"{deepest:g}, into the part")
    return floor


def _feed_findings(ctx: "PlanContext") -> tuple[dict, ...]:
    return tuple(ctx.feeds.findings)


def _xy(points) -> list[tuple[float, float]]:
    return [(p[0], p[1]) for p in points]


def _rotate_nearest(ring: list, point) -> list:
    """A closed ring (last point its first) restarted at its vertex nearest ``point``."""
    pts = ring[:-1]
    index = min(range(len(pts)), key=lambda k: (math.dist(pts[k][:2], point[:2]), k))
    turned = pts[index:] + pts[:index]
    return turned + [turned[0]]


def _within(area, a, b, tol: float) -> bool:
    """The straight move from ``a`` to ``b`` stays where the tool's centre may go."""
    from shapely.geometry import LineString, Point

    if math.dist(a[:2], b[:2]) < 1e-9:
        return area.buffer(tol).contains(Point(a[0], a[1]))
    return area.buffer(tol).contains(LineString([a[:2], b[:2]]))


# --- facing -----------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class FacingParams:
    stepover: float          # fraction of the diameter
    stepdown_mm: float
    pattern: str             # zigzag | one_way
    depth_mm: float | None
    z_mm: float | None
    finish_mm: float


def facing_floor(params: FacingParams, setup) -> float:
    """The z a facing cuts down to in its placed setup (a SetupFrame): ``z_mm``, ``depth_mm`` under the stock top,
    else the part's top."""
    if params.z_mm is not None:
        return params.z_mm
    if params.depth_mm is not None:
        return setup.stock_top_z - params.depth_mm
    return setup.part_bounds[1][2]


class FacingOperation(OperationBase):
    name, phase, word = "facing", 1, "Face"
    keys = frozenset({"stepover", "stepdown_mm", "pattern", "depth_mm", "z_mm", "finish_mm"})
    tool_types = ("face_mill", "flat_endmill", "bull_endmill")
    targets = ("all", "region")
    target_required = False

    def parse(self, raw: dict, ctx: ParseContext) -> FacingParams:
        known(raw, self.keys, where=ctx.where, takes=f"facing takes {', '.join(sorted(self.keys))}")
        stepover = _fraction(raw, "stepover", ctx, 0.7 if ctx.tool.type == "face_mill" else 0.4)
        pattern = choice(raw.get("pattern", "zigzag"), ("zigzag", "one_way"), where=f"{ctx.where}.pattern")
        depth, z = _floor_keys(raw, ctx)
        return FacingParams(stepover, _stepdown(raw, ctx, 1.0), pattern, depth, z,
                            _mm(raw, "finish_mm", ctx, 0.0, zero=True))

    def plan(self, params: FacingParams, ctx: "PlanContext") -> "Planned":
        from shapely.geometry import LineString

        from cadgen._internal.cam.operations import geometry2d as g2
        from cadgen._internal.cam.operations.context import Planned
        from cadgen._internal.cam.operations.emit import Emitter, entry_for, in_air

        r, tol = ctx.radius, ctx.tol
        target = _region_polygon(ctx) if ctx.operation.region is not None else g2.stock_outline(ctx)
        floor = facing_floor(params, ctx.setup)
        reach = target.buffer(r)
        hit = g2.shadow(ctx, floor).intersection(reach)
        if hit.area > 1e-6:
            raise ValueError(f"{ctx.where}: facing down to z {floor:g} cuts into the part, whose top is at z "
                             f"{ctx.part_top_z:g}; face to the part's top or above it")
        top = ctx.top_z
        if params.finish_mm and floor + params.finish_mm < top - 1e-9:
            heights = g2.depth_steps(top, floor + params.finish_mm, params.stepdown_mm) + [floor]
        else:
            heights = g2.depth_steps(top, floor, params.stepdown_mm)
        emitter = Emitter(ctx)
        rows = self._rows(target, r, params.stepover * 2 * r)
        previous = top
        for z in heights:
            lines = [[(a[0], a[1], z), (b[0], b[1], z)] for a, b in rows]
            for index, line in enumerate(lines):
                if params.pattern == "zigzag" and index % 2:
                    line.reverse()
                here = emitter.here
                if index and params.pattern == "zigzag" and here is not None and abs(here[2] - z) < 1e-9 and \
                        in_air(ctx, here) and in_air(ctx, line[0]) and \
                        g2.stock_outline(ctx).distance(LineString([here[:2], line[0][:2]])) >= r + tol:
                    emitter.cut(line)       # the turn is off the stock: stay down
                    continue
                entry, path = entry_for(ctx, line)
                emitter.go(path, rapid_to=previous, entry=entry)
            previous = z
        stats = {"floor_z": round(floor, 6), "levels": [round(z, 6) for z in heights], "rows": len(rows),
                 "stepover_mm": round(params.stepover * 2 * r, 6), "pattern": params.pattern}
        return Planned(emitter.toolpath(), stats, _feed_findings(ctx))

    @staticmethod
    def _rows(target, r: float, step: float) -> list[tuple[tuple[float, float], tuple[float, float]]]:
        """Rows along X covering ``target``: the tool hangs a tenth of its diameter over the first and last edge,
        and each row runs on past the target until the tool is off it by its radius and a millimetre."""
        from shapely.geometry import LineString

        x0, y0, x1, y1 = target.bounds
        over = 0.2 * r
        approach = max(1.0, 0.1 * r)
        first, last = y0 + r - over, y1 - r + over
        if last <= first:
            ys = [(y0 + y1) / 2]
        else:
            count = math.ceil((last - first) / step - 1e-9) + 1
            ys = [first + (last - first) * k / (count - 1) for k in range(count)]
        reach = target.buffer(r + approach)
        rows = []
        for y in ys:
            line = LineString([(x0 - 2 * r - 2 * approach, y), (x1 + 2 * r + 2 * approach, y)]).intersection(reach)
            pieces = [line] if line.geom_type == "LineString" else list(getattr(line, "geoms", ()))
            for piece in sorted((p for p in pieces if p.length > 1e-6), key=lambda p: p.bounds[0]):
                (ax, _), (bx, _) = piece.coords[0], piece.coords[-1]
                a, b = sorted([(round(ax, 6), round(y, 6)), (round(bx, 6), round(y, 6))])
                rows.append((a, b))
        return rows

    def human_lines(self, stats: dict) -> list[str]:
        return [f"faced to z {stats['floor_z']:g} in {len(stats['levels'])} level(s), {stats['rows']} rows "
                f"{stats['stepover_mm']:g} mm apart, {stats['pattern'].replace('_', ' ')}"]


# --- contour_2d ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Tabs:
    count: int
    width_mm: float
    height_mm: float


@dataclass(frozen=True)
class ContourParams:
    side: str | None             # outside | inside | on; None: the target's own
    stepdown_mm: float
    finish_passes: int
    finish_mm: float
    tabs: Tabs | None
    lead_radius_mm: float
    ramp_angle_deg: float
    depth_mm: float | None
    z_mm: float | None
    breakthrough_mm: float


class Contour2dOperation(OperationBase):
    name, phase, word = "contour_2d", 1, "Profile"
    keys = frozenset({"side", "stepdown_mm", "finish_passes", "finish_mm", "tabs", "lead_radius_mm",
                      "ramp_angle_deg", "depth_mm", "z_mm", "breakthrough_mm"})
    tool_types = _END_MILLS
    targets = ("features", "region", "all")
    kinds = ("profile", "pocket", "hole")

    def parse(self, raw: dict, ctx: ParseContext) -> ContourParams:
        known(raw, self.keys, where=ctx.where, takes=f"contour_2d takes {', '.join(sorted(self.keys))}")
        side = choice(raw["side"], ("outside", "inside", "on"), where=f"{ctx.where}.side") if "side" in raw else None
        diameter = _diameter(ctx)
        passes = whole(raw.get("finish_passes", 0), where=f"{ctx.where}.finish_passes", low=0, high=10)
        finish = _mm(raw, "finish_mm", ctx, 0.0, zero=True)
        if finish and not passes:
            raise ValueError(f"{ctx.where}.finish_mm: {finish:g} mm is left for finishing passes; give finish_passes "
                             "too")
        tabs = None
        if "tabs" in raw:
            spec = obj(raw["tabs"], where=f"{ctx.where}.tabs", example='{"count": 4, "width_mm": 6, "height_mm": 1}')
            known(spec, ("count", "width_mm", "height_mm"), where=f"{ctx.where}.tabs",
                  takes="tabs take count, width_mm, height_mm")
            if "count" not in spec:
                raise ValueError(f"{ctx.where}.tabs.count: how many tabs hold the part, like 4")
            tabs = Tabs(whole(spec["count"], where=f"{ctx.where}.tabs.count", low=1, high=64),
                        number(spec.get("width_mm", max(diameter, 3.0)), where=f"{ctx.where}.tabs.width_mm",
                               positive=True),
                        number(spec.get("height_mm", 1.0), where=f"{ctx.where}.tabs.height_mm", positive=True))
            if side == "on":
                raise ValueError(f"{ctx.where}.tabs: tabs hold a part cut free round an outline; side on cuts along "
                                 "it and frees nothing")
        depth, z = _floor_keys(raw, ctx)
        return ContourParams(side, _stepdown(raw, ctx, diameter / 2), passes, finish, tabs,
                             _mm(raw, "lead_radius_mm", ctx, diameter / 2, zero=True), _angle(raw, ctx), depth, z,
                             _mm(raw, "breakthrough_mm", ctx, 0.0, zero=True))

    # The targets: {ref, side, outline (a function of z), bottom (z, None for a region), through}.
    def _targets(self, params: ContourParams, ctx: "PlanContext") -> list[dict]:
        from cadgen._internal.cam.operations import geometry2d as g2

        found = []
        if ctx.operation.region is not None:
            poly = _region_polygon(ctx)
            found.append({"ref": "region", "side": params.side or "outside", "outline": lambda z, p=poly: p,
                          "bottom": None, "through": False})
            return found
        if ctx.operation.all:
            named = [(f.feature.ref, f) for f in ctx.features if f.feature.kind == "profile"]
            if not named:
                raise ValueError(f"{ctx.where}.all: setup {ctx.setup.setup.id} reaches no outline of the part")
        else:
            named = ctx.targets(self.kinds)
        for ref, feature in named:
            kind = feature.feature.kind
            own = "outside" if kind == "profile" else "inside"
            side = params.side or own
            if side != "on" and side != own:
                raise ValueError(f"{ctx.where}: {ref} is a {kind}; cutting it from the {side} goes into the part "
                                 f"(use side {own} or on)")
            through = (kind == "profile" or feature.feature.size.get("through", False)) and \
                feature.bottom[2] <= ctx.part_bottom_z + 1e-6
            found.append({"ref": ref, "side": side, "bottom": feature.bottom[2], "through": through,
                          "outline": lambda z, f=feature: g2.feature_region(ctx, f, z)})
        return found

    def plan(self, params: ContourParams, ctx: "PlanContext") -> "Planned":
        from cadgen._internal.cam.operations import geometry2d as g2
        from cadgen._internal.cam.operations.context import Planned
        from cadgen._internal.cam.operations.emit import Emitter

        r, tol = ctx.radius, ctx.tol
        stl = ctx.operation.stock_to_leave_mm
        emitter = Emitter(ctx)
        stats: dict = {"targets": [], "passes": 0}
        for target in self._targets(params, ctx):
            side = target["side"]
            if target["bottom"] is None:
                floor = _floor_from(ctx, params.depth_mm, params.z_mm, None, deepest=None, what="")
            else:
                default = target["bottom"] - (params.breakthrough_mm if target["through"] else 0.0)
                deepest = None if target["through"] else target["bottom"]
                floor = _floor_from(ctx, params.depth_mm, params.z_mm, default, deepest=deepest,
                                    what=f"the bottom of {target['ref']}")
            final = 0.0 if side == "on" else r + stl
            rough = final + params.finish_mm if side != "on" else 0.0
            depths = g2.depth_steps(ctx.top_z, floor, params.stepdown_mm)
            if not depths:
                raise ValueError(f"{ctx.where}: {target['ref']} has nothing to cut: its bottom at z {floor:g} is not "
                                 f"under the top of the material at z {ctx.top_z:g}")
            passes = [(z, rough, False) for z in depths]
            for k in range(1, params.finish_passes + 1):
                share = params.finish_mm * (params.finish_passes - k) / params.finish_passes
                passes.append((floor, final + share if side != "on" else 0.0, True))
            tab_top = floor + params.tabs.height_mm if params.tabs else None
            if tab_top is not None and tab_top >= ctx.top_z - 1e-9:
                raise ValueError(f"{ctx.where}.tabs.height_mm: {params.tabs.height_mm:g} mm tabs stand as tall as the "
                                 f"cut ({ctx.top_z - floor:g} mm)")
            previous = {}
            for z, distance, _ in passes:
                outline = target["outline"](z)
                area, rings = self._rings(outline, side, distance, z, tol, ctx, target["ref"])
                for ring in rings:
                    # Over the entry the material stands at the last pass of this ring at this offset, else at the
                    # top: a finishing pass at a new offset meets the wall stock the rough passes left full height.
                    # A ring starts where its outline says (geometry2d.start_at), so it is known by that point.
                    key = (round(ring[0][0], 4), round(ring[0][1], 4), round(distance, 6))
                    above = previous.get(key, ctx.top_z)
                    if tab_top is not None and z < tab_top - 1e-9:
                        ring = self._with_tabs(ring, params.tabs, r, tab_top, ctx)
                    self._pass(emitter, ctx, params, ring, z, above, side, area)
                    previous[key] = z
                    stats["passes"] += 1
            stats["targets"].append({"ref": target["ref"], "side": side, "floor_z": round(floor, 6),
                                     "depths": [round(z, 6) for z in depths], "finish_passes": params.finish_passes,
                                     "tab_top_z": None if tab_top is None else round(tab_top, 6)})
        if params.tabs:
            stats["tabs"] = {"count": params.tabs.count, "width_mm": params.tabs.width_mm,
                             "height_mm": params.tabs.height_mm}
        return Planned(emitter.toolpath(), stats, _feed_findings(ctx))

    @staticmethod
    def _rings(outline, side: str, distance: float, z: float, tol: float, ctx, ref: str):
        """``(area, rings)``: the rings a pass at ``z`` follows, and for an inside pass the area inside them where the
        tool's centre may go (``None`` otherwise)."""
        from cadgen._internal.cam.operations import geometry2d as g2

        if side == "outside":
            return None, g2.ring_paths(g2.offset(outline, distance, tol), z, material="inside")
        if side == "inside":
            area = g2.offset(outline, -distance, tol)
            if area.is_empty:
                raise ValueError(f"{ctx.where}: the {2 * ctx.radius:g} mm tool does not fit inside {ref}")
            return area, g2.ring_paths(area, z, material="outside")
        return None, g2.ring_paths(outline, z, material="outside")

    @staticmethod
    def _with_tabs(ring: list, tabs: Tabs, r: float, tab_top: float, ctx) -> list:
        """The ring lifting to ``tab_top`` over ``tabs.count`` stretches spaced evenly along it, each long enough to
        leave ``tabs.width_mm`` of wall standing between the tool's two sides."""
        lengths = [0.0]
        for a, b in zip(ring, ring[1:]):
            lengths.append(lengths[-1] + math.dist(a[:2], b[:2]))
        total = lengths[-1]
        half = tabs.width_mm / 2 + r
        if tabs.count * 2 * half >= total - 1e-6:
            raise ValueError(f"{ctx.where}.tabs: {tabs.count} tabs {tabs.width_mm:g} mm wide do not fit round a "
                             f"{total:.1f} mm outline with a {2 * r:g} mm tool")
        cuts = []
        for k in range(tabs.count):
            middle = total * (k + 0.5) / tabs.count
            cuts.append((middle - half, middle + half))
        z = ring[0][2]

        def at(s: float):
            index = max(i for i in range(len(lengths)) if lengths[i] <= s + 1e-12)
            index = min(index, len(ring) - 2)
            span = lengths[index + 1] - lengths[index]
            f = 0.0 if span <= 0 else (s - lengths[index]) / span
            a, b = ring[index], ring[index + 1]
            return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)

        marks = sorted({s for cut in cuts for s in cut} | set(lengths))
        out, level, last = [ring[0]], z, 0.0
        for s in marks[1:]:
            middle = (last + s) / 2
            height = tab_top if any(lo < middle < hi for lo, hi in cuts) else z
            if height != level:  # straight up onto the tab, or straight down off it
                out.append((out[-1][0], out[-1][1], height))
                level = height
            x, y = at(s)
            out.append((x, y, height))
            last = s
        return [p for k, p in enumerate(out) if k == 0 or math.dist(p, out[k - 1]) > 1e-9]

    @staticmethod
    def _pass(emitter, ctx, params: ContourParams, ring: list, z: float, above: float, side: str, area) -> None:
        """One pass round ``ring``: led on and off from the air side where the leads stay clear of the part and the
        lead on starts off the stock; else entered along the ring (a helix inside ``area``, where the tool's centre
        may go inside an outline, when one fits)."""
        from shapely.geometry import LineString

        from cadgen._internal.cam.operations import geometry2d as g2
        from cadgen._internal.cam.operations.emit import entry_for, in_air, lead_arc
        from cadgen._internal.cam.toolpath import PLUNGE

        tol, r = ctx.tol, ctx.radius
        lead_in = lead_out = []
        if params.lead_radius_mm > 0 and side != "on":
            material = g2.shadow(ctx, z)
            arc_in = lead_arc(ring, params.lead_radius_mm, at_end=False, air_left=True, tol=tol)
            arc_out = lead_arc(ring, params.lead_radius_mm, at_end=True, air_left=True, tol=tol)

            def clear(points) -> bool:
                return material.is_empty or material.distance(LineString(_xy(points))) >= r - tol / 4

            if clear(arc_in) and in_air(ctx, arc_in[0]):
                lead_in = arc_in[:-1]
            if clear(arc_out):
                lead_out = arc_out[1:]
        if lead_in:
            entry, path = PLUNGE, lead_in + ring
        else:
            entry, path = entry_for(ctx, ring, angle_deg=params.ramp_angle_deg, area=area, helix=area is not None)
        emitter.go(path + lead_out, rapid_to=above, entry=entry)

    def human_lines(self, stats: dict) -> list[str]:
        lines = []
        for target in stats["targets"]:
            lines.append(f"{target['ref']} {target['side']}: {len(target['depths'])} level(s) to z "
                         f"{target['floor_z']:g}" + (f", {target['finish_passes']} finishing pass(es)"
                                                      if target["finish_passes"] else ""))
        if "tabs" in stats:
            tabs = stats["tabs"]
            lines.append(f"{tabs['count']} tabs {tabs['width_mm']:g} mm wide, {tabs['height_mm']:g} mm tall")
        return lines


# --- pocket_2d ------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class PocketParams:
    pattern: str                 # offset | zigzag
    stepover: float              # fraction of the diameter
    stepdown_mm: float
    finish_mm: float
    finish_floor_mm: float
    ramp_angle_deg: float
    depth_mm: float | None
    z_mm: float | None


class Pocket2dOperation(OperationBase):
    name, phase, word = "pocket_2d", 1, "Pocket"
    keys = frozenset({"pattern", "stepover", "stepdown_mm", "finish_mm", "finish_floor_mm", "ramp_angle_deg",
                      "depth_mm", "z_mm"})
    tool_types = _END_MILLS
    targets = ("features", "region")

    def parse(self, raw: dict, ctx: ParseContext) -> PocketParams:
        known(raw, self.keys, where=ctx.where, takes=f"pocket_2d takes {', '.join(sorted(self.keys))}")
        pattern = choice(raw.get("pattern", "offset"), ("offset", "zigzag"), where=f"{ctx.where}.pattern")
        depth, z = _floor_keys(raw, ctx)
        return PocketParams(pattern, _fraction(raw, "stepover", ctx, 0.4), _stepdown(raw, ctx, _diameter(ctx) / 2),
                            _mm(raw, "finish_mm", ctx, 0.0, zero=True), _mm(raw, "finish_floor_mm", ctx, 0.0, zero=True),
                            _angle(raw, ctx), depth, z)

    def plan(self, params: PocketParams, ctx: "PlanContext") -> "Planned":
        from cadgen._internal.cam.operations import geometry2d as g2
        from cadgen._internal.cam.operations.context import Planned
        from cadgen._internal.cam.operations.emit import Emitter

        r = ctx.radius
        stl = ctx.operation.stock_to_leave_mm
        emitter = Emitter(ctx)
        stats: dict = {"pockets": [], "corner_left": []}
        if ctx.operation.region is not None:
            poly = _region_polygon(ctx)
            targets = [("region", lambda z, p=poly: p.difference(g2.shadow(ctx, z)), None, None)]
        else:
            targets = [(ref, lambda z, f=found: g2.feature_region(ctx, f, z), found.bottom[2], found)
                       for ref, found in ctx.targets(("pocket",))]
        for ref, region_at, bottom, found in targets:
            floor = _floor_from(ctx, params.depth_mm, params.z_mm, bottom, deepest=bottom,
                                what=f"the floor of {ref}")
            rough_floor = floor + params.finish_floor_mm
            levels = [(z, r + stl + params.finish_mm) for z in g2.depth_steps(ctx.top_z, rough_floor, params.stepdown_mm)]
            if params.finish_floor_mm > 0:
                levels.append((floor, r + stl))
            if not levels:
                raise ValueError(f"{ctx.where}: {ref} has nothing to cut: its floor at z {floor:g} is not under the top "
                                 f"of the material at z {ctx.top_z:g}")
            previous = ctx.top_z
            for index, (z, distance) in enumerate(levels):
                region = region_at(z)
                area = g2.offset(region, -distance, ctx.tol)
                if area.is_empty:
                    if region.area < 1e-6:
                        raise ValueError(f"{ctx.where}: the part fills {ref} at z {z:g}; pocketing it would cut into "
                                         "the part")
                    raise ValueError(f"{ctx.where}: the {2 * r:g} mm tool does not fit in {ref} at z {z:g}")
                wall = None  # wall stock with no floor pass to take it: one ring at the final depth
                if params.finish_mm > 0 and params.finish_floor_mm == 0 and index == len(levels) - 1:
                    wall = g2.offset(region, -(r + stl), ctx.tol)
                self._level(emitter, ctx, params, area, z, previous, wall)
                previous = z
            stats["pockets"].append({"ref": ref, "floor_z": round(floor, 6),
                                     "levels": [round(z, 6) for z, _ in levels], "pattern": params.pattern})
            if found is not None:
                corner = found.feature.size.get("corner_radius_mm")
                if corner is not None and corner + ctx.tol < r + stl:
                    stats["corner_left"].append({"ref": ref, "corner_radius_mm": corner,
                                                 "tool_leaves_radius_mm": round(r + stl, 6)})
        return Planned(emitter.toolpath(), stats, _feed_findings(ctx))

    def _level(self, emitter, ctx, params: PocketParams, area, z: float, above: float, wall) -> None:
        """One level of every separate area: the clearing, then (at the last rough level) the finishing ring."""
        from cadgen._internal.cam.operations import geometry2d as g2

        step = params.stepover * 2 * ctx.radius
        for root in g2.polygons(area):
            if params.pattern == "offset":
                sequence = self._offset_rings(root, step, z, ctx.tol)
            else:
                sequence = self._zigzag(root, step, z)
            for fresh, path, near in sequence:
                self._cut(emitter, ctx, params, root, path, z, above, fresh=fresh, near=near)
            if wall is not None:
                for piece in g2.polygons(wall):
                    if piece.intersects(root):
                        for ring in g2.ring_paths(piece, z, material="outside"):
                            # Re-entered from above, the wall stock the roughing left stands full height.
                            self._cut(emitter, ctx, params, piece, ring, z, ctx.top_z, fresh=False)

    @staticmethod
    def _cut(emitter, ctx, params: PocketParams, root, path: list, z: float, above: float, *, fresh: bool,
             near=None) -> None:
        from cadgen._internal.cam.operations.emit import entry_for

        here = emitter.here
        if not fresh and here is not None and abs(here[2] - z) < 1e-9:
            if path[0] == path[-1] and len(path) > 2:
                path = _rotate_nearest(path, here)
            if _within(root, here, path[0], ctx.tol):
                emitter.cut(path)
                return
        entry, cut = entry_for(ctx, path, angle_deg=params.ramp_angle_deg, area=root, helix=True, near=near)
        emitter.go(cut, rapid_to=above, entry=entry)

    @staticmethod
    def _offset_rings(root, step: float, z: float, tol: float) -> list[tuple[bool, list, Any]]:
        """Rings offset inward from ``root`` by ``step`` until nothing is left, ordered innermost first: each branch
        of the nesting from its innermost ring out (a new branch is ``fresh``: entered again), its own outer ring
        last."""
        from cadgen._internal.cam.operations import geometry2d as g2

        def children(poly):
            return g2.polygons(g2.offset(poly, -step, tol))

        def visit(poly) -> list[tuple[bool, list, Any]]:
            out: list[tuple[bool, list, Any]] = []
            for child in children(poly):
                out += visit(child)
            rings = g2.ring_paths(poly, z, material="outside")
            if not out:  # the innermost ring of a branch: entered afresh, with a helix in its middle
                return [(True, rings[0], poly)] + [(False, ring, None) for ring in rings[1:]]
            return out + [(False, ring, None) for ring in rings]

        return visit(root)

    @staticmethod
    def _zigzag(root, step: float, z: float) -> list[tuple[bool, list, Any]]:
        """Rows along X across ``root``, back and forth, then its rings round the walls and islands."""
        from shapely.geometry import LineString

        from cadgen._internal.cam.operations import geometry2d as g2

        x0, y0, x1, y1 = root.bounds
        count = max(1, math.ceil((y1 - y0) / step - 1e-9))
        out: list[tuple[bool, list, Any]] = []
        flip = False
        for k in range(count):
            y = y0 + (y1 - y0) * (k + 0.5) / count
            line = LineString([(x0 - 1, y), (x1 + 1, y)]).intersection(root)
            pieces = [line] if line.geom_type == "LineString" else list(getattr(line, "geoms", ()))
            pieces = sorted((p for p in pieces if p.length > 1e-6), key=lambda p: p.bounds[0])
            for piece in (reversed(pieces) if flip else pieces):
                (ax, _), (bx, _) = piece.coords[0], piece.coords[-1]
                a, b = sorted((ax, bx))
                row = [(b, y, z), (a, y, z)] if flip else [(a, y, z), (b, y, z)]
                out.append((not out, row, None))
            flip = not flip
        out += [(False, ring, None) for ring in g2.ring_paths(root, z, material="outside")]
        return out

    def human_lines(self, stats: dict) -> list[str]:
        lines = [f"{p['ref']}: {len(p['levels'])} level(s) to z {p['floor_z']:g}, {p['pattern']}" for p in stats["pockets"]]
        for corner in stats["corner_left"]:
            lines.append(f"{corner['ref']}: R{corner['corner_radius_mm']:g} inside corners left at "
                         f"R{corner['tool_leaves_radius_mm']:g} by this tool")
        return lines
