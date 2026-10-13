"""2.5D milling: facing, profiles and pockets, cut in flat passes stepping down.

Each operation reads its own keys (``parse``, stdlib), and plans (``plan``) on the shared layer: the context
(:mod:`.context`), the slices and offsets (:mod:`.geometry2d`) and the linking and entries (:mod:`.emit`). Climb
milling throughout (the material on the tool's right, the spindle turning clockwise).

``facing``
    Flattens the stock (``all``, the default) or a ``region`` down to the part's top, or to ``z_mm`` (setup frame) or
    ``depth_mm`` below the stock top. Rows across the setup's X, ``stepover`` apart (a fraction of the tool's
    diameter: 0.7 for a face mill, 0.4 for an end mill). Facing the whole stock, each level's rows cover the stock
    standing between that level and the one above (a bar lying across the tool widens as it goes down) and run off
    it at both ends; ``pattern`` ``zigzag`` (the default: back and forth, staying down where the turn is in air) or
    ``one_way`` (lift and come back, every row cut the same way). In a region the tool's centre stays inside it, so
    the tool hangs over its edge by a tenth of its diameter and no further; zigzag rows stay down across the turns,
    one-way rows lift just clear of the material to go back. ``stepdown_mm`` (1 mm) per level, the last exactly the
    floor; ``finish_mm`` leaves that much for a last pass on its own. Refused (``too_deep``) when what the tool
    sweeps at the floor would cut the part.

``contour_2d``
    Cuts round an outline: a ``profile`` (or ``all: true``: every outline the setup reaches), a ``pocket`` or
    ``hole`` (from inside), or a ``region``. ``side`` is ``outside`` (a profile's and a region's default),
    ``inside`` (a pocket's or a hole's) or ``on`` (the tool's centre on the outline, travelling the way the target's
    own side would: clockwise round a profile or a region, counter-clockwise round a pocket or a hole). Down to the
    feature's bottom (a through outline ``breakthrough_mm`` below it: 0.3 mm through sheet stock, else 0), or
    ``z_mm`` / ``depth_mm`` below the material top (a region needs one), in ``stepdown_mm`` levels (half the tool's
    diameter). ``finish_passes`` passes at the final depth take off the last ``finish_mm`` (equal shares; with no
    ``finish_mm`` they are spring passes); one at a new offset drops into the rough slot beside it and steps across.
    ``tabs`` ``{count, width_mm, height_mm}`` (width the tool's diameter but at least 3 mm; height a quarter of the
    cut's depth but at least 1 mm and at most half the depth) leave that many bridges, evenly round each ring,
    ``height_mm`` tall at the bottom: the passes under their top rise straight onto each tab and ramp down off it
    along the ring ahead. ``lead_radius_mm`` (the tool's radius; 0 for none) is a quarter-circle lead on and off each
    pass from the air side, used where it stays clear of the part and the fixture and the lead on starts in air.
    Elsewhere a pass enters along itself: a helix inside a pocket or hole where one fits, else a ramp along its first
    straight run, else laps of the ring sloping down; ``ramp_angle_deg`` (3) is the steepest any of them goes.

``pocket_2d``
    Clears a ``pocket`` (islands left standing) or a ``region`` down to its floor (or ``z_mm`` / ``depth_mm``):
    ``pattern`` ``offset`` (the default: rings offset from the walls inward by ``stepover``, 0.4 of the diameter,
    cut from the innermost out, each ring joined to the next at depth) or ``zigzag`` (rows, then a ring round the
    walls). It enters each level with a helix inside the pocket (a ramp where none fits), from the level above.
    ``finish_mm`` leaves that much on the walls for one ring at the final depth, entered from the area just cleared
    at that depth; ``finish_floor_mm`` that much on the floor for a last level of its own (its outer ring finishes the
    walls too). Inside corners tighter than the tool's radius are left with the tool's radius:
    ``stats["corner_left"]`` lists them for the checks; ``stats["full_slot_mm"]`` is the length of the links cut at
    depth from one ring or row to the next.

An operation that cannot be planned on this part raises
:class:`~cadgen._internal.cam.operations.base.PlanError`: ``empty_operation`` (nothing to cut), ``too_deep`` (a floor
under the part's), ``tool_too_large`` (the tool does not fit) or ``unreachable`` (no way in).
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
           "PocketParams", "Tabs", "facing_floor", "tab_height"]

_END_MILLS = ("flat_endmill", "bull_endmill")
#: How far a facing's tool hangs over the edge of what it faces, as a fraction of its radius (a tenth of its diameter).
_OVERHANG = 0.2
#: How high over the material a one-way facing in a region lifts to go back for its next row, mm.
_HOVER_MM = 0.5


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
        from cadgen._internal.cam.operations.base import PlanError

        raise PlanError("too_deep", f"{ctx.where}: cutting to z {floor:g} goes {deepest - floor:g} mm under {what} at "
                        f"z {deepest:g}, into the part")
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


def _pieces(line, y: float) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """The pieces of a row (a shapely intersection along X at ``y``) left to right, each ``(left, right)``."""
    pieces = [line] if line.geom_type == "LineString" else list(getattr(line, "geoms", ()))
    rows = []
    for piece in sorted((p for p in pieces if p.geom_type == "LineString" and p.length > 1e-6),
                        key=lambda p: p.bounds[0]):
        (ax, _), (bx, _) = piece.coords[0], piece.coords[-1]
        a, b = sorted([(round(ax, 6), round(y, 6)), (round(bx, 6), round(y, 6))])
        rows.append((a, b))
    return rows


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
        from shapely.ops import unary_union

        from cadgen._internal.cam.operations import geometry2d as g2
        from cadgen._internal.cam.operations.base import PlanError
        from cadgen._internal.cam.operations.context import Planned
        from cadgen._internal.cam.operations.emit import DEFAULT_RAMP_DEG, Emitter, entry_for, fixtures_clear, ramp_down

        r, tol = ctx.radius, ctx.tol
        step = params.stepover * 2 * r
        floor = facing_floor(params, ctx.setup)
        top = ctx.top_z
        if params.finish_mm and floor + params.finish_mm < top - 1e-9:
            heights = g2.depth_steps(top, floor + params.finish_mm, params.stepdown_mm) + [floor]
        else:
            heights = g2.depth_steps(top, floor, params.stepdown_mm)
        centre = None
        if ctx.operation.region is not None:
            # Inside a region the tool's centre stays a tenth of its diameter short of reaching the region's edge
            # with its own: it hangs over the edge by that much and no further.
            centre = g2.offset(_region_polygon(ctx), -(r - _OVERHANG * r), tol)
            if centre.is_empty:
                raise PlanError("tool_too_large", f"{ctx.where}: the {2 * r:g} mm tool does not fit in the region",
                                items=["region"])
        rows_at: dict = {}
        previous = top
        for z in heights:
            if centre is not None:
                rows_at[z] = self._rows_inside(centre, step)
            else:
                rows_at[z] = self._rows(self._stock_band(ctx, z, previous), r, step)
            previous = z
        swept = unary_union([LineString([a, b]).buffer(r, quad_segs=16) for rows in rows_at.values() for a, b in rows])
        hit = g2.shadow(ctx, floor).intersection(swept)
        if hit.area > 1e-6:
            raise PlanError("too_deep", f"{ctx.where}: facing down to z {floor:g} cuts into the part, whose top is "
                            f"at z {ctx.part_top_z:g}; face to the part's top or above it", items=[ctx.part.ref])
        emitter = Emitter(ctx)
        angle = DEFAULT_RAMP_DEG
        previous = top
        for z in heights:
            lines = [[(a[0], a[1], z), (b[0], b[1], z)] for a, b in rows_at[z]]
            band = None if centre is not None else self._stock_band(ctx, z, previous)
            for index, line in enumerate(lines):
                if params.pattern == "zigzag" and index % 2:
                    line.reverse()
                here = emitter.here
                if index and here is not None and abs(here[2] - z) < 1e-9:
                    turn = LineString([here[:2], line[0][:2]]) if math.dist(here[:2], line[0][:2]) > 1e-9 else None
                    if params.pattern == "zigzag":
                        # Stay down where the turn is in air at this height (off the stock still standing) or,
                        # in a region, where it stays inside the area the tool's centre may go.
                        if centre is not None:
                            down = _within(centre, here, line[0], tol)
                        else:
                            down = turn is None or (band.distance(turn) >= r + tol and fixtures_clear(ctx, turn, z))
                        if down:
                            emitter.cut(line)
                            continue
                    elif centre is not None:
                        # One way in a region: up just clear of the material, back over the row just cut, across
                        # to the next row's start, and ramp down onto it.
                        hover = min(previous + _HOVER_MM, ctx.heights.retract)
                        back = (lines[index - 1][0][0], lines[index - 1][0][1])
                        if _within(centre, back, line[0], tol):
                            emitter.cut([(here[0], here[1], hover), (back[0], back[1], hover),
                                         (line[0][0], line[0][1], hover)])
                            emitter.cut(ramp_down(line, hover, angle) + line[1:])
                            continue
                entry, path = entry_for(ctx, line, angle_deg=angle)
                emitter.go(path, rapid_to=previous, entry=entry)
            previous = z
        rows = len(rows_at[heights[0]]) if heights else 0
        stats = {"floor_z": round(floor, 6), "levels": [round(z, 6) for z in heights], "rows": rows,
                 "stepover_mm": round(step, 6), "pattern": params.pattern}
        return Planned(emitter.toolpath(), stats, _feed_findings(ctx))

    @staticmethod
    def _stock_band(ctx: "PlanContext", z: float, above: float):
        """The stock standing between ``z`` and ``above`` (what a level at ``z`` takes off, everything over
        ``above`` being faced already), sliced at both ends, the middle, every millimetre of a blank read from a
        STEP, and a bar's widest height where it is in between."""
        from shapely.ops import unary_union

        from cadgen._internal.cam.operations import geometry2d as g2

        heights = {z, (z + above) / 2, above - g2.NUDGE_MM}
        if ctx.stock.type == "from":
            count = max(1, math.ceil(above - z))
            heights |= {z + (above - z) * k / count for k in range(1, count)}
        if ctx.stock.type == "cylinder":
            (x0, y0, z0), (x1, y1, z1) = ctx.stock.bounds
            middle = ctx.to_setup(((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2))[2]
            if z < middle < above:
                heights.add(middle)
        return unary_union([g2.stock_outline(ctx, h) for h in sorted(heights)])

    @staticmethod
    def _rows(target, r: float, step: float) -> list[tuple[tuple[float, float], tuple[float, float]]]:
        """Rows along X covering ``target``: the tool hangs a tenth of its diameter over the first and last edge,
        and each row runs on past the target until the tool is off it by its radius and a millimetre."""
        from shapely.geometry import LineString

        if target is None or target.is_empty:
            return []
        x0, y0, x1, y1 = target.bounds
        over = _OVERHANG * r
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
            rows += _pieces(line, y)
        return rows

    @staticmethod
    def _rows_inside(centre, step: float) -> list[tuple[tuple[float, float], tuple[float, float]]]:
        """Rows along X across ``centre`` (where the tool's centre may go), from its lowest Y to its highest, at most
        ``step`` apart."""
        from shapely.geometry import LineString

        x0, y0, x1, y1 = centre.bounds
        count = max(1, math.ceil((y1 - y0) / step - 1e-9))
        ys = [y0 + (y1 - y0) * k / count for k in range(count + 1)] if y1 - y0 > 1e-6 else [(y0 + y1) / 2]
        rows = []
        for y in ys:
            # Just inside the area's lowest and highest edge, so the row meets it as a line, not a point.
            at = min(max(y, y0 + 1e-6), y1 - 1e-6) if y1 - y0 > 2e-6 else y
            rows += _pieces(LineString([(x0 - 1, at), (x1 + 1, at)]).intersection(centre), at)
        return rows

    def human_lines(self, stats: dict) -> list[str]:
        return [f"faced to z {stats['floor_z']:g} in {len(stats['levels'])} level(s), {stats['rows']} rows "
                f"{stats['stepover_mm']:g} mm apart, {stats['pattern'].replace('_', ' ')}"]


# --- contour_2d ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Tabs:
    count: int
    width_mm: float
    #: ``None``: a quarter of the cut's depth but at least 1 mm, and never more than half the depth.
    height_mm: float | None


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
    #: ``None``: 0.3 mm through sheet stock (into the spoilboard), else 0.
    breakthrough_mm: float | None


def tab_height(tabs: Tabs, depth: float) -> float:
    """The height of ``tabs`` on a cut ``depth`` mm deep: as stated, else a quarter of the depth but at least 1 mm,
    and never more than half the depth."""
    if tabs.height_mm is not None:
        return tabs.height_mm
    return min(max(1.0, 0.25 * depth), 0.5 * depth)


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
                        None if "height_mm" not in spec else
                        number(spec["height_mm"], where=f"{ctx.where}.tabs.height_mm", positive=True))
            if side == "on":
                raise ValueError(f"{ctx.where}.tabs: tabs hold a part cut free round an outline; side on cuts along "
                                 "it and frees nothing")
        depth, z = _floor_keys(raw, ctx)
        return ContourParams(side, _stepdown(raw, ctx, diameter / 2), passes, finish, tabs,
                             _mm(raw, "lead_radius_mm", ctx, diameter / 2, zero=True), _angle(raw, ctx), depth, z,
                             _mm(raw, "breakthrough_mm", ctx, None, zero=True))

    # The targets: {ref, side, own (the side the target is cut from), outline (a function of z), bottom (z, None for a
    # region), through}.
    def _targets(self, params: ContourParams, ctx: "PlanContext") -> list[dict]:
        from cadgen._internal.cam.operations import geometry2d as g2
        from cadgen._internal.cam.operations.base import PlanError

        found = []
        if ctx.operation.region is not None:
            poly = _region_polygon(ctx)
            found.append({"ref": "region", "side": params.side or "outside", "own": "outside",
                          "outline": lambda z, p=poly: p, "bottom": None, "through": False})
            return found
        if ctx.operation.all:
            named = [(f.feature.ref, f) for f in ctx.features if f.feature.kind == "profile"]
            if not named:
                raise PlanError("unreachable", f"{ctx.where}.all: setup {ctx.setup.setup.id} reaches no outline of the "
                                "part")
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
            found.append({"ref": ref, "side": side, "own": own, "bottom": feature.bottom[2], "through": through,
                          "outline": lambda z, f=feature: g2.feature_region(ctx, f, z)})
        return found

    def plan(self, params: ContourParams, ctx: "PlanContext") -> "Planned":
        from cadgen._internal.cam.operations import geometry2d as g2
        from cadgen._internal.cam.operations.base import PlanError
        from cadgen._internal.cam.operations.context import Planned
        from cadgen._internal.cam.operations.emit import Emitter

        r, tol = ctx.radius, ctx.tol
        stl = ctx.operation.stock_to_leave_mm
        breakthrough = params.breakthrough_mm
        if breakthrough is None:
            breakthrough = 0.3 if ctx.stock.type == "sheet" else 0.0
        emitter = Emitter(ctx)
        stats: dict = {"targets": [], "passes": 0}
        heights = []
        for target in self._targets(params, ctx):
            side = target["side"]
            if target["bottom"] is None:
                floor = _floor_from(ctx, params.depth_mm, params.z_mm, None, deepest=None, what="")
            else:
                default = target["bottom"] - (breakthrough if target["through"] else 0.0)
                deepest = None if target["through"] else target["bottom"]
                floor = _floor_from(ctx, params.depth_mm, params.z_mm, default, deepest=deepest,
                                    what=f"the bottom of {target['ref']}")
            final = 0.0 if side == "on" else r + stl
            rough = final + params.finish_mm if side != "on" else 0.0
            depths = g2.depth_steps(ctx.top_z, floor, params.stepdown_mm)
            if not depths:
                raise PlanError("empty_operation", f"{ctx.where}: {target['ref']} has nothing to cut: its bottom at z "
                                f"{floor:g} is not under the top of the material at z {ctx.top_z:g}",
                                items=[target["ref"]])
            passes = [(z, rough, False) for z in depths]
            for k in range(1, params.finish_passes + 1):
                share = params.finish_mm * (params.finish_passes - k) / params.finish_passes
                passes.append((floor, final + share if side != "on" else 0.0, True))
            tab_top = None
            if params.tabs:
                height = tab_height(params.tabs, ctx.top_z - floor)
                heights.append(height)
                tab_top = floor + height
                if tab_top >= ctx.top_z - 1e-9:
                    raise ValueError(f"{ctx.where}.tabs.height_mm: {height:g} mm tabs stand as tall as the cut "
                                     f"({ctx.top_z - floor:g} mm)")
            previous = {}
            slot = {}   # the rough rings at the floor, by their place in the level: a finishing pass drops in there
            for z, distance, finishing in passes:
                outline = target["outline"](z)
                area, rings = self._rings(outline, side, target["own"], distance, z, tol, ctx, target["ref"])
                for place, ring in enumerate(rings):
                    # Over the entry the material stands at the last pass of this ring at this offset, else at the
                    # top: a finishing pass at a new offset meets the wall stock the rough passes left full height,
                    # so it drops into the rough slot beside it instead (``slot``) and steps across.
                    # A ring starts where its outline says (geometry2d.start_at), so it is known by that point.
                    key = (round(ring[0][0], 4), round(ring[0][1], 4), round(distance, 6))
                    above = previous.get(key, ctx.top_z)
                    beside = None
                    if finishing and key not in previous and place in slot:
                        beside = self._beside(slot[place], ring, abs(rough - distance), tol)
                    if tab_top is not None and z < tab_top - 1e-9:
                        ring = self._with_tabs(ring, params.tabs, r, tab_top, ctx, params.ramp_angle_deg)
                    self._pass(emitter, ctx, params, ring, z, above, side, area, beside=beside)
                    previous[key] = z
                    if not finishing and abs(z - floor) < 1e-9:
                        slot[place] = ring
                    stats["passes"] += 1
            stats["targets"].append({"ref": target["ref"], "side": side, "floor_z": round(floor, 6),
                                     "depths": [round(z, 6) for z in depths], "finish_passes": params.finish_passes,
                                     "tab_top_z": None if tab_top is None else round(tab_top, 6)})
        if params.tabs:
            stats["tabs"] = {"count": params.tabs.count, "width_mm": params.tabs.width_mm,
                             "height_mm": round(max(heights), 6) if heights else params.tabs.height_mm}
        return Planned(emitter.toolpath(), stats, _feed_findings(ctx))

    @staticmethod
    def _rings(outline, side: str, own: str, distance: float, z: float, tol: float, ctx, ref: str):
        """``(area, rings)``: the rings a pass at ``z`` follows, and for an inside pass the area inside them where the
        tool's centre may go (``None`` otherwise). Side ``on`` runs the way the target's own side would (``own``):
        round a profile or a region clockwise, as an outside cut does, round a pocket or a hole counter-clockwise (its
        islands clockwise), as an inside cut does, so what that side keeps is climb cut."""
        from cadgen._internal.cam.operations import geometry2d as g2
        from cadgen._internal.cam.operations.base import PlanError

        if side == "outside":
            return None, g2.ring_paths(g2.offset(outline, distance, tol), z, material="inside")
        if side == "inside":
            area = g2.offset(outline, -distance, tol)
            if area.is_empty:
                raise PlanError("tool_too_large", f"{ctx.where}: the {2 * ctx.radius:g} mm tool does not fit inside "
                                f"{ref}", items=[ref])
            return area, g2.ring_paths(area, z, material="outside")
        return None, g2.ring_paths(outline, z, material="inside" if own == "outside" else "outside")

    @staticmethod
    def _beside(rough: list, ring: list, gap: float, tol: float):
        """The point of the rough ring ``rough`` (cut at the floor) to drop into for a finishing ``ring`` at a new
        offset: its start, or the vertex nearest the ring's start, when that is no further than the offsets differ
        (a straight step across onto the ring); else ``None``."""
        start = ring[0]
        candidates = [rough[0]] + sorted(rough[:-1], key=lambda p: math.dist(p[:2], start[:2]))[:1]
        for point in candidates:
            if abs(point[2] - start[2]) < 1e-9 and math.dist(point[:2], start[:2]) <= gap + 10 * tol:
                return tuple(point)
        return None

    @staticmethod
    def _with_tabs(ring: list, tabs: Tabs, r: float, tab_top: float, ctx, angle_deg: float) -> list:
        """The ring lifting to ``tab_top`` over ``tabs.count`` stretches spaced evenly along it, each long enough to
        leave ``tabs.width_mm`` of wall standing between the tool's two sides. It rises straight up onto a tab and
        comes down off it by zig-zagging along the ring ahead (:func:`~.emit.ramp_down`), back to the tab's end at
        depth, so the tab stays square."""
        from cadgen._internal.cam.operations.emit import ramp_down

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
            s = s % total if s > total + 1e-12 else s
            index = max(i for i in range(len(lengths)) if lengths[i] <= s + 1e-12)
            index = min(index, len(ring) - 2)
            span = lengths[index + 1] - lengths[index]
            f = 0.0 if span <= 0 else (s - lengths[index]) / span
            a, b = ring[index], ring[index + 1]
            return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)

        def stretch(a: float, b: float) -> list:
            """The ring at depth from ``a`` to ``b`` along it (``b`` may run past its end, round to its start)."""
            marks = [s for s in lengths if a < s < b] + [s + total for s in lengths if a < s + total < b]
            return [(*at(s), z) for s in [a] + sorted(marks) + [b]]

        marks = sorted({s for cut in cuts for s in cut} | set(lengths))
        out, level, last = [ring[0]], z, 0.0
        for s in marks[1:]:
            middle = (last + s) / 2
            height = tab_top if any(lo < middle < hi for lo, hi in cuts) else z
            if height > level:   # straight up onto the tab
                out.append((out[-1][0], out[-1][1], height))
            elif height < level:  # off its end: ramp down along the ring ahead, back to here at depth
                ahead = min([lo for lo, _ in cuts if lo > last + 1e-9], default=cuts[0][0] + total)
                out += ramp_down(stretch(last, ahead), level, angle_deg)
            level = height
            x, y = at(s)
            out.append((x, y, height))
            last = s
        return [p for k, p in enumerate(out) if k == 0 or math.dist(p, out[k - 1]) > 1e-9]

    @staticmethod
    def _pass(emitter, ctx, params: ContourParams, ring: list, z: float, above: float, side: str, area, *,
              beside=None) -> None:
        """One pass round ``ring``: led on and off from the air side where the leads stay clear of the part and the
        fixture and the lead on starts in air; else, for a finishing pass at a new offset, dropped into the rough
        slot ``beside`` it and stepped across onto it; else entered along the ring (a helix inside ``area``, where
        the tool's centre may go inside an outline, when one fits)."""
        from shapely.geometry import LineString

        from cadgen._internal.cam.operations import geometry2d as g2
        from cadgen._internal.cam.operations.emit import entry_for, fixtures_clear, in_air, lead_arc
        from cadgen._internal.cam.toolpath import PLUNGE

        tol, r = ctx.tol, ctx.radius
        lead_in = lead_out = []
        if params.lead_radius_mm > 0 and side != "on":
            material = g2.shadow(ctx, z)
            arc_in = lead_arc(ring, params.lead_radius_mm, at_end=False, air_left=True, tol=tol)
            arc_out = lead_arc(ring, params.lead_radius_mm, at_end=True, air_left=True, tol=tol)

            def clear(points) -> bool:
                line = LineString(_xy(points))
                return (material.is_empty or material.distance(line) >= r - tol / 4) and \
                    fixtures_clear(ctx, line, min(p[2] for p in points))

            if clear(arc_in) and in_air(ctx, arc_in[0]):
                lead_in = arc_in[:-1]
            if clear(arc_out):
                lead_out = arc_out[1:]
        if lead_in:
            entry, path, above = PLUNGE, lead_in + ring, above
        elif beside is not None:
            entry, path, above = PLUNGE, [beside] + ring, beside[2]
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

        from cadgen._internal.cam.operations.base import PlanError

        r = ctx.radius
        stl = ctx.operation.stock_to_leave_mm
        emitter = Emitter(ctx)
        stats: dict = {"pockets": [], "corner_left": [], "full_slot_mm": 0.0}
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
                raise PlanError("empty_operation", f"{ctx.where}: {ref} has nothing to cut: its floor at z {floor:g} is "
                                f"not under the top of the material at z {ctx.top_z:g}", items=[ref])
            previous = ctx.top_z
            for index, (z, distance) in enumerate(levels):
                region = region_at(z)
                area = g2.offset(region, -distance, ctx.tol)
                if area.is_empty:
                    if region.area < 1e-6:
                        raise PlanError("too_deep", f"{ctx.where}: the part fills {ref} at z {z:g}; pocketing it would "
                                        "cut into the part", items=[ref])
                    raise PlanError("tool_too_large", f"{ctx.where}: the {2 * r:g} mm tool does not fit in {ref} at z "
                                    f"{z:g}", items=[ref])
                wall = None  # wall stock with no floor pass to take it: one ring at the final depth
                if params.finish_mm > 0 and params.finish_floor_mm == 0 and index == len(levels) - 1:
                    wall = g2.offset(region, -(r + stl), ctx.tol)
                self._level(emitter, ctx, params, area, z, previous, wall, stats)
                previous = z
            stats["pockets"].append({"ref": ref, "floor_z": round(floor, 6),
                                     "levels": [round(z, 6) for z, _ in levels], "pattern": params.pattern})
            if found is not None:
                corner = found.feature.size.get("corner_radius_mm")
                if corner is not None and corner + ctx.tol < r + stl:
                    stats["corner_left"].append({"ref": ref, "corner_radius_mm": corner,
                                                 "tool_leaves_radius_mm": round(r + stl, 6)})
        stats["full_slot_mm"] = round(stats["full_slot_mm"], 6)
        return Planned(emitter.toolpath(), stats, _feed_findings(ctx))

    def _level(self, emitter, ctx, params: PocketParams, area, z: float, above: float, wall, stats=None) -> None:
        """One level of every separate area: the clearing, then (at the last rough level) the finishing ring, entered
        from the cleared area at this level (dropped in from the level above and stepped across), never from the top.
        Links cut at depth from one ring or row to the next add their length to ``stats["full_slot_mm"]``."""
        from cadgen._internal.cam.operations import geometry2d as g2

        step = params.stepover * 2 * ctx.radius
        for root in g2.polygons(area):
            if params.pattern == "offset":
                sequence = self._offset_rings(root, step, z, ctx.tol)
            else:
                sequence = self._zigzag(root, step, z)
            for fresh, path, near in sequence:
                self._cut(emitter, ctx, params, root, path, z, above, fresh=fresh, near=near, stats=stats)
            if wall is not None:
                for piece in g2.polygons(wall):
                    if piece.intersects(root):
                        for ring in g2.ring_paths(piece, z, material="outside"):
                            self._cut(emitter, ctx, params, piece, ring, z, above, fresh=False, cleared=root)

    @staticmethod
    def _cut(emitter, ctx, params: PocketParams, root, path: list, z: float, above: float, *, fresh: bool,
             near=None, stats=None, cleared=None) -> None:
        """Cut ``path`` at ``z``: straight on from where the tool is when that stays inside ``root`` (a link at
        depth), else from the area ``cleared`` at this level (dropped in from ``above`` and stepped across), else
        through an entry (:func:`~.emit.entry_for`) from ``above``."""
        from shapely.geometry import Point
        from shapely.ops import nearest_points

        from cadgen._internal.cam.operations.emit import entry_for
        from cadgen._internal.cam.toolpath import PLUNGE

        here = emitter.here
        if not fresh and here is not None and abs(here[2] - z) < 1e-9:
            if path[0] == path[-1] and len(path) > 2:
                path = _rotate_nearest(path, here)
            if _within(root, here, path[0], ctx.tol):
                if stats is not None:
                    stats["full_slot_mm"] += math.dist(here[:2], path[0][:2])
                emitter.cut(path)
                return
        if cleared is not None:
            start = Point(path[0][0], path[0][1])
            q = nearest_points(cleared.boundary, start)[0]
            drop = (round(q.x, 6), round(q.y, 6), z)
            if _within(root, drop, path[0], ctx.tol):
                emitter.go([drop] + list(path), rapid_to=above, entry=PLUNGE)
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
