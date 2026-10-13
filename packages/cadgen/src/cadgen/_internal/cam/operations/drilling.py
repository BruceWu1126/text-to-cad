"""Drilling and hole cycles: each hole one canned cycle (:class:`~cadgen._internal.cam.toolpath.Cycle`), which a post
writes as its G81 ... G87 where the controller has it and as plain moves where it does not.

Every operation here cuts ``hole`` features: the ``features`` it names, or with ``all: true`` every hole the setup's
tool reaches that suits the tool (below). A named hole that does not suit the tool is refused in a sentence naming
its field; ``all: true`` skips the ones that do not suit and is refused only when none does.

**Which holes suit a tool.** A drill, reamer or boring bar suits a hole of its own diameter within
``diameter_tol_mm`` (0.05 mm), either way. A tap suits a hole modelled at its major diameter or at its tap drill
(the major diameter less one pitch), within ``diameter_tol_mm`` (0.15 mm: tap drill charts round, M8's 6.8 for
8 - 1.25). A counterbore suits a hole with a counterbore on the side the tool comes from, of the tool's diameter
(0.05 mm), its pilot (when the tool states one) no wider than the hole; a back bore the same with the counterbore on
the far side. A spot drill suits any hole whose spot fits in its diameter.

**Order.** A nearest-neighbour tour from where the tool is (the previous toolpath's end with the same tool, else the
setup's origin), ties broken by the hole's selector, never the order of the job's list.

**Depth** (z of the tool's tip; ``depth_mm`` on a drill, tap, reamer or boring bar is the full-diameter depth below
the hole's opening, shallower than the hole's own):

- blind: the full diameter reaches the hole's floor, so the tip goes the tool's point length (``tip_length_mm``: 0
  for a tap, reamer or boring bar) under it, but never past the hole's own deepest point: a flat-bottomed hole (no
  point) or one whose point is shorter than the tool's stops the tip there, and a ``rest_material`` warning says how
  far short of the floor the full diameter ends.
- through: through the hole's length, plus the tool's point length, plus ``breakthrough_mm`` (1 mm).
- spot drill: to the depth at which its cone is ``spot_diameter_mm`` across (default: the hole's countersink if it
  has one, else the hole's diameter), or ``depth_mm`` below the hole's opening.
- counterbore: its cutting face (the pilot's length above the tip) to the counterbore's floor.
- back bore: from below the stock (its head ``retract_mm`` under the stock's bottom) up until the top of its head
  reaches the top of the far-side counterbore; the bar is shifted ``shift_mm`` along ``shift_xy`` to pass through the
  hole, so the shift must be at least half the difference of the two diameters and leave the spindle's axis in the
  hole. It returns to the height it started from (G98), never to its R plane, which is under the part.

**Heights.** The R plane is ``retract_mm`` above the top of the material (the stock top, or an earlier whole-stock
facing's floor). The tool comes to each group of cycles at the retract height (from the clearance height when the
retract plane is not provably clear: :func:`~cadgen._internal.cam.operations.emit.retract_clear`). From one hole to
the next it stays at the R plane (G99) only when the straight move across at R is provably clear (nothing stands
above R: the part and the stock are under it, so only a fixture can, tested the same way); else the cycle returns to
the height it started from (G98) and the tool links over at the retract or clearance height. The last hole of a
chain returns to R as well, so the chain is one G99 block, and the tool lifts straight up from there.

**Cycles and their defaults.** ``drill`` is G81 (``cycle`` ``drill``, or ``peck`` / ``chip_break`` with
``peck_mm``; a ``dwell_s`` makes it G82). ``peck`` is G83 with ``peck_mm`` (1 x the diameter); ``chip_break`` G73
with ``peck_mm`` (0.5 x the diameter: it never leaves the hole, so the peck is what breaks the chip). ``spot_drill``
and ``counterbore`` are G82 with ``dwell_s`` 0.2 s (a spot and a counterbore's floor are cleaned up by a pause at the
bottom). ``tap`` is G84 for a floating holder, ``rigid_tap`` G84 synchronised to the spindle; the pitch is the tap's
own ``pitch_mm`` (the job requires it; no thread is guessed from a diameter), and the feed is exactly rpm x pitch.
``ream`` is G85 (G89 with a dwell). ``boring_cycle`` is G86 (``cycle`` ``bore``: the spindle stops at the bottom and
the bar rapids out, leaving a line) or G76 (``fine_bore``: the spindle orients and the bar shifts ``shift_mm`` off
the wall along ``shift_xy`` before coming out; both required, no default for which way the bar's edge faces).

``stock_to_leave_mm`` means nothing to a hole cycle (the tool's diameter is the hole's), so anything but 0 is refused.

Stdlib only at import, like every module under ``operations/``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cadgen._internal.cam.operations.base import OperationBase, ParseContext, PlanError, finding
from cadgen._internal.cam.values import choice, known, number

if TYPE_CHECKING:
    from cadgen._internal.cam.features import SetupFeature
    from cadgen._internal.cam.operations.context import PlanContext, Planned

__all__ = [
    "BackBoreOperation", "BoringCycleOperation", "ChipBreakOperation", "CounterboreOperation", "DEFAULT_BREAKTHROUGH_MM",
    "DEFAULT_DWELL_S", "DrillOperation", "HoleParams", "PeckOperation", "ReamOperation", "RigidTapOperation",
    "SpotDrillOperation", "TapOperation", "tour",
]

#: How far a through hole's tool goes past the hole's far end, beyond its point, mm.
DEFAULT_BREAKTHROUGH_MM = 1.0
#: The pause at the bottom of a spot and of a counterbore, s.
DEFAULT_DWELL_S = 0.2
#: Peck depth, x the tool's diameter: a peck drill's (G83, out to R each time) and a chip break's (G73).
PECK_X_D = 1.0
CHIP_BREAK_X_D = 0.5
#: How closely a tool's diameter must match a hole's, mm (``diameter_tol_mm``'s default): a drill, reamer, boring
#: bar, counterbore or back bore, and a tap (against its major diameter or its tap drill).
DIAMETER_TOL_MM = 0.05
TAP_DIAMETER_TOL_MM = 0.15
#: The least room a back-bore bar's head keeps from the hole's wall as it passes through, mm.
BACK_BORE_PASS_MM = 0.1

_EPS = 1e-6
#: How :meth:`_HoleOperation.unsuited` says a hole is out of this setup's reach rather than the wrong size for the tool
#: (the PlanError's kind: ``unreachable``, else ``tool_too_large``: the tool does not fit the hole).
_OUT_OF_REACH = ("has no counterbore", "has its counterbore on", "is blind")


@dataclass(frozen=True)
class HoleParams:
    """What a hole operation's ``parse`` makes of its keys (``None``: not stated, or not taken)."""

    kind: str                              # the Cycle kind
    depth_mm: float | None = None
    breakthrough_mm: float = DEFAULT_BREAKTHROUGH_MM
    diameter_tol_mm: float = DIAMETER_TOL_MM
    peck_mm: float | None = None
    dwell_s: float = 0.0
    spot_diameter_mm: float | None = None
    shift_mm: float | None = None
    shift_xy: tuple[float, float] | None = None


def tour(points: dict[str, tuple[float, float]], start: tuple[float, float]) -> list[str]:
    """The keys of ``points`` (x, y) in a nearest-neighbour tour from ``start``: each next the nearest to the last,
    ties broken by key (to a nanometre), so the same holes always come out in the same order."""
    left = sorted(points)
    here, order = start, []
    while left:
        best = min(left, key=lambda key: (round(math.dist(here, points[key]), 6), key))
        order.append(best)
        left.remove(best)
        here = points[best]
    return order


# --- parsing --------------------------------------------------------------------------------------------------

def _mm(raw: dict, key: str, ctx: ParseContext, default=None, *, zero: bool = False):
    if key not in raw:
        return default
    return number(raw[key], where=f"{ctx.where}.{key}", minimum=0 if zero else None, positive=not zero)


def _shift_xy(raw: dict, ctx: ParseContext) -> tuple[float, float] | None:
    if "shift_xy" not in raw:
        return None
    value = raw["shift_xy"]
    where = f"{ctx.where}.shift_xy"
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{where}: the way the bar moves off the wall, [x, y] in the setup's plane, like [1, 0]")
    xy = tuple(number(item, where=f"{where}[{index}]") for index, item in enumerate(value))
    if math.hypot(*xy) < 1e-9:
        raise ValueError(f"{where}: the way the bar moves off the wall must not be [0, 0]")
    return xy


class _HoleOperation(OperationBase):
    """What every hole cycle shares: targets, parsing, the tour, the heights, the moves."""

    phase = 1
    targets = ("features", "all")
    #: The Cycle kind it writes (``drill`` may change it from its keys).
    cycle_kind = "drill"
    #: The keys it takes, beyond COMMON_KEYS.
    keys = frozenset({"depth_mm", "breakthrough_mm", "diameter_tol_mm"})
    default_dwell = 0.0
    tol_default = DIAMETER_TOL_MM

    # --- parse -------------------------------------------------------------------------------------------

    def parse(self, raw: dict, ctx: ParseContext) -> HoleParams:
        known(raw, self.keys, where=ctx.where, takes=f"{self.name} takes {', '.join(sorted(self.keys))}")
        return HoleParams(
            kind=self.cycle_kind,
            depth_mm=_mm(raw, "depth_mm", ctx),
            breakthrough_mm=_mm(raw, "breakthrough_mm", ctx, DEFAULT_BREAKTHROUGH_MM, zero=True),
            diameter_tol_mm=_mm(raw, "diameter_tol_mm", ctx, self.tol_default, zero=True),
            dwell_s=_mm(raw, "dwell_s", ctx, self.default_dwell, zero=True),
        )

    # --- which holes ---------------------------------------------------------------------------------------

    def _holes(self, params: HoleParams, ctx: "PlanContext") -> list[tuple[str, "SetupFeature"]]:
        if ctx.operation.stock_to_leave_mm:
            raise ValueError(f"{ctx.where}.stock_to_leave_mm: a hole cycle cuts to its tool's diameter and leaves "
                             "nothing on the wall; leave it out")
        if not ctx.operation.all:
            named = ctx.targets(("hole",))
            seen: dict = {}
            for index, (ref, found) in enumerate(named):
                if found.feature.ref in seen:
                    raise ValueError(f"{ctx.where}.features[{index}]: {ref} is the same hole as "
                                     f"features[{seen[found.feature.ref]}]; each hole is cut once")
                seen[found.feature.ref] = index
                why = self.unsuited(params, ctx, found)
                if why:
                    kind = "unreachable" if why.startswith(_OUT_OF_REACH) else "tool_too_large"
                    raise PlanError(kind, f"{ctx.where}.features[{index}]: {ref} {why}",
                                    items=[{"text": f"{ctx.label} {ref}", "ref": ref, "operation": ctx.label}])
            return named
        reached = [(found.feature.ref, found) for found in ctx.features if found.feature.kind == "hole"]
        suited = [(ref, found) for ref, found in reached if not self.unsuited(params, ctx, found)]
        if not suited:
            sizes = ", ".join(f"{ref} {found.feature.size['diameter_mm']:g} mm" for ref, found in reached) or "none"
            raise PlanError("empty_operation", f"{ctx.where}.all: no hole setup {ctx.setup.setup.id} reaches suits "
                            f"{ctx.tool.id} ({self._tool_words(ctx)}); holes reached: {sizes}")
        return suited

    def _tool_words(self, ctx: "PlanContext") -> str:
        return f"a {ctx.tool.diameter_mm:g} mm {ctx.tool.type.replace('_', ' ')}"

    def unsuited(self, params: HoleParams, ctx: "PlanContext", found: "SetupFeature") -> str:
        """Why the tool does not suit the hole, or ""."""
        hole = found.feature.size["diameter_mm"]
        tool = float(ctx.tool.diameter_mm)
        if abs(tool - hole) > params.diameter_tol_mm + 1e-9:
            return (f"is a {hole:g} mm hole, and {ctx.tool.id} is {self._tool_words(ctx)}: more than "
                    f"diameter_tol_mm ({params.diameter_tol_mm:g} mm) {'over' if tool > hole else 'under'} it")
        return ""

    # --- depth -----------------------------------------------------------------------------------------------

    def tip_z(self, params: HoleParams, ctx: "PlanContext", ref: str, found: "SetupFeature") -> tuple[float, dict, list]:
        """The z the tool's tip goes to, what the stats say about the hole, and any findings."""
        size = found.feature.size
        top, bottom = found.top[2], found.bottom[2]
        point = ctx.profile.tip_length_mm
        through = bool(size.get("through"))
        if params.depth_mm is not None:
            reach = top - params.depth_mm
            if reach < bottom - _EPS:
                raise PlanError("too_deep", f"{ctx.where}.depth_mm: {params.depth_mm:g} mm is deeper than {ref}, "
                                f"which is {top - bottom:g} mm {'through' if through else 'deep'}")
            if not through or reach > bottom + _EPS:
                return self._blind(ctx, ref, reach, None if through else bottom, size, point)
        if through:
            tip = bottom - point - params.breakthrough_mm
            return tip, {"through": True, "full_diameter_z": round(tip + point, 6)}, []
        return self._blind(ctx, ref, bottom, bottom, size, point)

    def _blind(self, ctx: "PlanContext", ref: str, floor: float, hole_floor: float | None, size: dict,
               point: float) -> tuple[float, dict, list]:
        """The tip for a full diameter down to ``floor``, never under the hole's own deepest point."""
        tip = floor - point
        found: list[dict] = []
        if hole_floor is not None:
            deepest = hole_floor - float(size.get("point_mm", 0.0))
            if tip < deepest - _EPS:
                short = deepest - tip
                tip = deepest
                what = "flat floor" if "point_mm" not in size else f"{size['point_mm']:g} mm point"
                found.append(finding(
                    "warning", "rest_material",
                    f"{ctx.label}: {ctx.tool.id}'s full diameter stops {short:g} mm short of {ref}'s floor",
                    f"the hole has a {what} and the tool's point is {point:g} mm long, so the tip stops at the hole's "
                    "deepest point and the corner of the floor is left; finish it with a flat tool if it matters",
                    [{"text": f"{ctx.label} {ref}", "ref": ref, "operation": ctx.label}]))
        return tip, {"through": False, "full_diameter_z": round(tip + point, 6)}, found

    # --- the cycle -----------------------------------------------------------------------------------------

    def cycle_for(self, params: HoleParams, ctx: "PlanContext", ref: str, found: "SetupFeature", r: float):
        """``(Cycle, stats, findings)`` for one hole, its R plane at ``r``; ``return_to`` filled in later."""
        from cadgen._internal.cam.toolpath import Cycle

        tip, stats, found_ = self.tip_z(params, ctx, ref, found)
        if params.kind == "back_bore":
            if tip <= r + _EPS:
                raise PlanError("empty_operation", f"{ctx.where}: {ref} has nothing to cut: the back bore's top "
                                f"z {tip:g} is not over its R plane z {r:g}")
        elif tip >= r - _EPS:
            raise PlanError("empty_operation", f"{ctx.where}: {ref} has nothing to cut: its depth z {tip:g} is not "
                            f"under the R plane z {r:g}")
        kind = params.kind
        dwell = params.dwell_s
        if kind == "drill" and dwell:
            kind = "spot"
        peck = params.peck_mm if kind in ("peck", "chip_break") else None
        if kind in ("peck", "chip_break") and peck is None:
            peck = (PECK_X_D if kind == "peck" else CHIP_BREAK_X_D) * float(ctx.tool.diameter_mm)
        pitch = float(ctx.tool.size["pitch_mm"]) if kind in ("tap", "rigid_tap") else None
        shift = params.shift_mm if kind in ("fine_bore", "back_bore") else None
        cycle = Cycle(kind, r, tip, peck_mm=peck, pitch_mm=pitch, shift_mm=shift, dwell_s=dwell,
                      shift_xy=params.shift_xy if shift is not None else None)
        return cycle, stats, found_

    def r_plane(self, ctx: "PlanContext") -> float:
        return round(ctx.top_z + ctx.operation.retract_mm, 6)

    def plan(self, params: HoleParams, ctx: "PlanContext") -> "Planned":
        from dataclasses import replace

        from cadgen._internal.cam.operations.context import Planned
        from cadgen._internal.cam.operations.emit import retract_clear
        from cadgen._internal.cam.toolpath import Heights, Move, Toolpath, link

        holes = dict(self._holes(params, ctx))
        xy = {ref: (round(found.top[0], 6), round(found.top[1], 6)) for ref, found in holes.items()}
        here_xy = (ctx.start[0], ctx.start[1]) if ctx.start is not None else (0.0, 0.0)
        order = tour(xy, here_xy)
        r = self.r_plane(ctx)
        heights = ctx.heights
        start_z = heights.retract
        if r > start_z + _EPS:   # never: the material top is at or under the stock top
            raise ValueError(f"{ctx.where}: the R plane z {r:g} is over the retract plane z {start_z:g}")
        at_r = replace(ctx, heights=Heights(max(heights.clearance, r), r))
        planned = []
        findings = list(ctx.feeds.findings)
        for ref in order:
            cycle, stats, found = self.cycle_for(params, ctx, ref, holes[ref], r)
            planned.append((ref, cycle, stats))
            findings += found
        chained = [cycle.kind != "back_bore" and nxt.kind != "back_bore" and retract_clear(at_r, xy[a], xy[b])
                   for (a, cycle, _), (b, nxt, _) in zip(planned, planned[1:])] + [False]

        moves: list = []
        start = tuple(ctx.start) if ctx.start is not None else (*xy[order[0]], heights.clearance)
        here = start
        holes_out = []
        for index, (ref, cycle, stats) in enumerate(planned):
            x, y = xy[ref]
            if not (index and chained[index - 1]):
                end = (x, y, start_z)
                clear = retract_clear(ctx, here, end)
                moves += link(here, end, heights=heights, retract_is_clear=clear, feed=ctx.feeds.feed_mm_min,
                              op=ctx.index, rapid_to=start_z)
                here = moves[-1].xyz if moves else here
            # A chain of holes linked at R all return to R (one G99 block, then bare X Y), the last one too: the
            # tool lifts from there. A hole on its own returns to where it started (G98).
            back = "r" if chained[index] or (index and chained[index - 1]) else "initial"
            cycle = replace(cycle, return_to=back)
            end_z = r if back == "r" else here[2]
            move = Move("cycle", (x, y, end_z), ctx.feeds.feed_mm_min, ctx.index, cycle=cycle)
            moves.append(move)
            here = move.xyz
            holes_out.append({"ref": ref, "x": x, "y": y, "z": round(cycle.z, 6), "return_to": back, **stats})
        if here[2] < heights.clearance:
            moves.append(Move("rapid", (here[0], here[1], heights.clearance), None, ctx.index))
        toolpath = Toolpath(ctx.index, ctx.tool.id, ctx.feeds.rpm, ctx.operation.coolant, start, tuple(moves))
        stats = {"cycle": planned[0][1].kind, "r_z": r, "holes": holes_out, "moves_at_r": sum(chained)}
        if planned[0][1].peck_mm is not None:
            stats["peck_mm"] = planned[0][1].peck_mm
        if planned[0][1].dwell_s:
            stats["dwell_s"] = planned[0][1].dwell_s
        return Planned(toolpath, stats, tuple(findings))

    def human_lines(self, stats: dict) -> list[str]:
        holes = stats["holes"]
        chained = stats["moves_at_r"]
        deepest = min(hole["z"] for hole in holes)
        line = (f"{len(holes)} hole(s), {stats['cycle'].replace('_', ' ')} cycle from R z {stats['r_z']:g}, "
                f"deepest tip z {deepest:g}")
        if chained:
            line += f", {chained} move(s) between holes at R"
        return [line]


# --- the drills ---------------------------------------------------------------------------------------------------

class DrillOperation(_HoleOperation):
    name, word = "drill", "Drill"
    tool_types = ("drill",)
    keys = _HoleOperation.keys | {"cycle", "peck_mm", "dwell_s"}

    def parse(self, raw: dict, ctx: ParseContext) -> HoleParams:
        params = super().parse(raw, ctx)
        kind = choice(raw.get("cycle", self.cycle_kind), ("drill", "peck", "chip_break"), where=f"{ctx.where}.cycle")
        peck = _mm(raw, "peck_mm", ctx)
        if peck is not None and kind == "drill":
            raise ValueError(f"{ctx.where}.peck_mm: a plain drill cycle goes down in one; give cycle peck or "
                             "chip_break to peck")
        return HoleParams(kind, params.depth_mm, params.breakthrough_mm, params.diameter_tol_mm, peck, params.dwell_s)


class PeckOperation(DrillOperation):
    name, word = "peck", "Peck drill"
    cycle_kind = "peck"
    keys = _HoleOperation.keys | {"peck_mm", "dwell_s"}


class ChipBreakOperation(PeckOperation):
    name, word = "chip_break", "Chip-break drill"
    cycle_kind = "chip_break"


class SpotDrillOperation(_HoleOperation):
    name, word = "spot_drill", "Spot drill"
    tool_types = ("spot_drill",)
    cycle_kind = "spot"
    keys = frozenset({"spot_diameter_mm", "depth_mm", "dwell_s"})
    default_dwell = DEFAULT_DWELL_S

    def parse(self, raw: dict, ctx: ParseContext) -> HoleParams:
        known(raw, self.keys, where=ctx.where, takes=f"{self.name} takes {', '.join(sorted(self.keys))}")
        if "spot_diameter_mm" in raw and "depth_mm" in raw:
            raise ValueError(f"{ctx.where}: give spot_diameter_mm (the spot's diameter at the hole's opening) or "
                             "depth_mm (below the opening), not both")
        spot = _mm(raw, "spot_diameter_mm", ctx)
        if spot is not None and spot > float(ctx.tool.diameter_mm) + 1e-9:
            raise ValueError(f"{ctx.where}.spot_diameter_mm: a {spot:g} mm spot is wider than {ctx.tool.id}'s "
                             f"{ctx.tool.diameter_mm:g} mm")
        return HoleParams("spot", depth_mm=_mm(raw, "depth_mm", ctx), dwell_s=_mm(raw, "dwell_s", ctx, DEFAULT_DWELL_S,
                                                                                  zero=True),
                          spot_diameter_mm=spot)

    def _spot(self, params: HoleParams, found: "SetupFeature") -> float | None:
        if params.depth_mm is not None:
            return None
        if params.spot_diameter_mm is not None:
            return params.spot_diameter_mm
        size = found.feature.size
        return float(size["countersink"]["diameter_mm"]) if "countersink" in size else float(size["diameter_mm"])

    def unsuited(self, params: HoleParams, ctx: "PlanContext", found: "SetupFeature") -> str:
        spot = self._spot(params, found)
        if spot is not None and spot > float(ctx.tool.diameter_mm) + 1e-9:
            return (f"wants a {spot:g} mm spot, wider than {ctx.tool.id}'s {ctx.tool.diameter_mm:g} mm; give "
                    "spot_diameter_mm or depth_mm")
        return ""

    def tip_z(self, params, ctx, ref, found):
        top = found.top[2]
        spot = self._spot(params, found)
        if spot is None:
            depth = params.depth_mm
        else:
            angle = float(ctx.tool.size.get("tip_angle_deg", 90.0))
            depth = spot / 2 / math.tan(math.radians(angle) / 2)
        deepest = found.bottom[2] - float(found.feature.size.get("point_mm", 0.0))
        if not found.feature.size.get("through") and top - depth < deepest - _EPS:
            raise PlanError("too_deep", f"{ctx.where}: a {depth:g} mm deep spot goes past the bottom of {ref}")
        stats = {"spot_depth_mm": round(depth, 6)}
        if spot is not None:
            stats["spot_diameter_mm"] = round(spot, 6)
        return top - depth, stats, []


class TapOperation(_HoleOperation):
    name, word = "tap", "Tap"
    tool_types = ("tap",)
    cycle_kind = "tap"
    keys = _HoleOperation.keys | {"dwell_s"}
    tol_default = TAP_DIAMETER_TOL_MM

    def _tool_words(self, ctx):
        return f"a {ctx.tool.diameter_mm:g} x {ctx.tool.size['pitch_mm']:g} mm tap"

    def unsuited(self, params, ctx, found):
        hole = found.feature.size["diameter_mm"]
        major = float(ctx.tool.diameter_mm)
        drill = major - float(ctx.tool.size["pitch_mm"])
        if min(abs(hole - major), abs(hole - drill)) > params.diameter_tol_mm + 1e-9:
            return (f"is a {hole:g} mm hole, and {ctx.tool.id} is {self._tool_words(ctx)}: the hole is neither its "
                    f"major diameter nor its {drill:g} mm tap drill within diameter_tol_mm "
                    f"({params.diameter_tol_mm:g} mm)")
        return ""


class RigidTapOperation(TapOperation):
    name, word = "rigid_tap", "Rigid tap"
    cycle_kind = "rigid_tap"


class ReamOperation(_HoleOperation):
    name, word = "ream", "Ream"
    tool_types = ("reamer",)
    cycle_kind = "ream"
    keys = _HoleOperation.keys | {"dwell_s"}


class BoringCycleOperation(_HoleOperation):
    name, word = "boring_cycle", "Boring bar"
    tool_types = ("boring_bar",)
    cycle_kind = "bore"
    keys = _HoleOperation.keys | {"cycle", "dwell_s", "shift_mm", "shift_xy"}

    def parse(self, raw: dict, ctx: ParseContext) -> HoleParams:
        params = super().parse(raw, ctx)
        kind = choice(raw.get("cycle", "bore"), ("bore", "fine_bore"), where=f"{ctx.where}.cycle")
        shift, xy = _mm(raw, "shift_mm", ctx), _shift_xy(raw, ctx)
        if kind == "bore" and (shift is not None or xy is not None):
            raise ValueError(f"{ctx.where}: a bore cycle (G86) does not shift off the wall; give cycle fine_bore "
                             "to shift")
        if kind == "fine_bore" and (shift is None or xy is None):
            raise ValueError(f"{ctx.where}: a fine_bore cycle needs shift_mm (how far the bar moves off the wall) "
                             "and shift_xy (which way, [x, y], away from the bar's cutting edge once the spindle "
                             "is oriented); cadgen does not guess which way the edge faces")
        return HoleParams(kind, params.depth_mm, params.breakthrough_mm, params.diameter_tol_mm, None,
                          params.dwell_s, shift_mm=shift, shift_xy=xy)


# --- counterbores -----------------------------------------------------------------------------------------------

def _counterbore_side(ctx: "PlanContext", found: "SetupFeature") -> str | None:
    """``near`` when the hole's counterbore is on the side the tool comes from, ``far`` on the other, ``None`` with
    no counterbore: read from the counterbore's wall, the hole's face of its diameter."""
    size = found.feature.size
    if "counterbore" not in size:
        return None
    radius = float(size["counterbore"]["diameter_mm"]) / 2
    middle = (found.top[2] + found.bottom[2]) / 2
    for ref in found.feature.refs:
        face = ctx.part.face(int(ref.rsplit(".f", 1)[1]))
        if face.surface == "cylinder" and face.radius is not None and abs(face.radius - radius) < 1e-3:
            return "near" if ctx.to_setup(face.center)[2] > middle else "far"
    return None


class CounterboreOperation(_HoleOperation):
    name, word = "counterbore", "Counterbore"
    tool_types = ("counterbore",)
    cycle_kind = "spot"
    keys = frozenset({"diameter_tol_mm", "dwell_s"})
    default_dwell = DEFAULT_DWELL_S
    side = "near"

    def parse(self, raw: dict, ctx: ParseContext) -> HoleParams:
        known(raw, self.keys, where=ctx.where, takes=f"{self.name} takes {', '.join(sorted(self.keys))}")
        return HoleParams(self.cycle_kind, diameter_tol_mm=_mm(raw, "diameter_tol_mm", ctx, DIAMETER_TOL_MM, zero=True),
                          dwell_s=_mm(raw, "dwell_s", ctx, self.default_dwell, zero=True))

    def unsuited(self, params, ctx, found):
        side = _counterbore_side(ctx, found)
        if side is None:
            return "has no counterbore"
        if side != self.side:
            other = "back_bore from below" if self.side == "near" else "counterbore from this side"
            return f"has its counterbore on the {side} side; cut it with a {other}"
        bore = float(found.feature.size["counterbore"]["diameter_mm"])
        tool = float(ctx.tool.diameter_mm)
        if abs(tool - bore) > params.diameter_tol_mm + 1e-9:
            return (f"has a {bore:g} mm counterbore, and {ctx.tool.id} is {self._tool_words(ctx)}: more than "
                    f"diameter_tol_mm ({params.diameter_tol_mm:g} mm) {'over' if tool > bore else 'under'} it")
        pilot = ctx.tool.size.get("pilot_diameter_mm")
        hole = float(found.feature.size["diameter_mm"])
        if self.side == "near" and pilot is not None and float(pilot) > hole + 1e-9:
            return f"is a {hole:g} mm hole, too small for {ctx.tool.id}'s {float(pilot):g} mm pilot"
        return ""

    def tip_z(self, params, ctx, ref, found):
        depth = float(found.feature.size["counterbore"]["depth_mm"])
        floor = found.top[2] - depth
        pilot = ctx.profile.tip_length_mm
        tip = floor - pilot
        deepest = found.bottom[2] - float(found.feature.size.get("point_mm", 0.0))
        if not found.feature.size.get("through") and tip < deepest - _EPS:
            raise PlanError("too_deep", f"{ctx.where}: {ctx.tool.id}'s {pilot:g} mm pilot would go past the bottom "
                            f"of {ref}")
        return tip, {"counterbore_floor_z": round(floor, 6)}, []


class BackBoreOperation(CounterboreOperation):
    name, word = "back_bore", "Back bore"
    tool_types = ("back_bore",)
    cycle_kind = "back_bore"
    keys = frozenset({"diameter_tol_mm", "dwell_s", "shift_mm", "shift_xy"})
    default_dwell = 0.0
    side = "far"

    def parse(self, raw: dict, ctx: ParseContext) -> HoleParams:
        known(raw, self.keys, where=ctx.where, takes=f"{self.name} takes {', '.join(sorted(self.keys))}")
        if "shift_mm" not in raw:
            raise ValueError(f"{ctx.where}.shift_mm: how far the bar moves off centre to pass through the hole; "
                             "required, with shift_xy if it is not +X")
        return HoleParams(self.cycle_kind, diameter_tol_mm=_mm(raw, "diameter_tol_mm", ctx, DIAMETER_TOL_MM, zero=True),
                          dwell_s=_mm(raw, "dwell_s", ctx, 0.0, zero=True), shift_mm=_mm(raw, "shift_mm", ctx),
                          shift_xy=_shift_xy(raw, ctx))

    def unsuited(self, params, ctx, found):
        why = super().unsuited(params, ctx, found)
        if why:
            return why
        if not found.feature.size.get("through"):
            return "is blind; a back bore reaches its counterbore through the hole"
        hole, tool = float(found.feature.size["diameter_mm"]), float(ctx.tool.diameter_mm)
        least = (tool - hole) / 2 + BACK_BORE_PASS_MM
        if params.shift_mm < least - 1e-9 or params.shift_mm >= hole / 2:
            return (f"is a {hole:g} mm hole: {ctx.tool.id}'s {tool:g} mm head passes through it shifted at least "
                    f"{least:g} mm and under {hole / 2:g} mm off centre, not shift_mm {params.shift_mm:g}")
        return ""

    def r_plane(self, ctx: "PlanContext") -> float:
        head = ctx.profile.cutting_length_mm
        return round(ctx.setup.stock_bottom_z - ctx.operation.retract_mm - head, 6)

    def tip_z(self, params, ctx, ref, found):
        depth = float(found.feature.size["counterbore"]["depth_mm"])
        top = found.bottom[2] + depth
        return top - ctx.profile.cutting_length_mm, {"counterbore_top_z": round(top, 6)}, []
