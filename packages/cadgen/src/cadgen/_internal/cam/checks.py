"""What a machinist checks on a planned job, as findings in plain words.

The KiCad findings' shape (``check``, ``severity``, ``type``, ``summary``, ``description``, ``items``), errors first,
like FEA's. ``run.py`` calls :func:`check_job` once, after every operation is planned, and reports :data:`CHECK_KINDS`
with a verdict each (the worst severity among its findings, else ``pass``). An error means the job is not fit to
run: no G-code is written. Positions in a summary are the setup's work coordinates (what the G-code says, ``X Y Z``);
an item's ``at`` is the same point in the part's frame, for the viewer.

Errors
    - ``gouge``: the tool's cutting envelope, swept along every move (rapids too), enters the part by more than the
      operation's tolerance. Measured on the B-rep by the kernel (``BRepExtrema_DistShapeShape``,
      :func:`brep_intrusion`), against the part less the cut the operation is meant to make that the model does not
      show (:data:`INTENDED_CUTS`: a thread, a chamfer, an engraving). Cost: a few kernel distance queries per
      toolpath, more only where the tool comes within the probe of the part (about 1 s for the four-operation plate).
    - ``holder_collision``: the shank above the flutes, or the holder, hits the part (the same B-rep sweep with the
      tool's body above its cutting length, run only when that body comes down below the part's top), or comes
      down into the stock below the top of the material the operation starts from (the holder always; the shank
      only where it is wider than the cutter, since a shank as wide as the cutter follows it into its own cut and a
      cut deeper than the flutes is ``too_deep``). Phase 1 does not simulate the stock: the stock checked is the
      initial stock's footprint, with nothing taken out of it, so the description says so.
    - ``fixture_collision``: any part of the tool (cutter, shank, holder) inside one of the fixture's volumes
      (``setups.FixtureEnvelope``) along any move: a contour into a vise jaw, a drill through the stock into the
      plate or the vise floor under it. Swept analytically (vertical cylinders against boxes and cylinders).
    - ``unreachable``, ``tool_too_large``, ``tool_too_small``: an operation's ``PlanError``; it is not planned.
    - ``too_deep``: an operation cuts deeper below the top of its material than the tool's flutes are long (its
      reach for a boring bar, back-bore or thread mill, which work inside a hole): the shank would be buried in the
      cut above the flutes. Also a ``PlanError`` from an operation that saw it first.
    - ``over_travel``: a setup's toolpaths span more than the machine's ``travel_mm`` on an axis. The work offset
      is assumed free to sit anywhere on the machine, so only the span is compared, and the finding says so.
    - ``no_post_support``: the post refused the job (``posts.base.PostRefusal``: a tap on a controller with no
      tapping cycle, arcs on one with none); ``run.py`` raises it from the post's refusal, not here.
    - ``empty_operation``: an operation planned no cutting move (or its ``PlanError``).

Warnings
    - ``corner_left``: a pocket's inside corner tighter than the tool's radius (plus stock to leave): material stays.
    - ``rest_material``: what an operation could not reach (a drill point shorter than the hole's, a V-carve's flat
      a clear tool does not fit), passed on from the operation.
    - ``rpm_clamped``, ``feed_clamped``: the machine's limits slowed a tool; one finding per tool and sentence,
      listing every operation it applies to.
    - ``full_slot``: more than :data:`FULL_SLOT_DIAMETERS` tool diameters of cutting at the tool's full width
      (``stats["full_slot_mm"]``: a pocket's links from ring to ring) outside adaptive clearing.
    - ``thin_wall``: a hole or pocket an operation cuts stands less than :data:`THIN_WALL_MM` from the part's outside
      walls (a kernel distance between their faces).
    - ``long_stickout``: a tool out of its holder more than :data:`LONG_STICKOUT_RATIO` times its diameter.
    - ``uncut_feature``: a hole, pocket or slot one of the job's setups reaches that no operation cuts (a spot drill
      or a chamfer does not make it).
    - ``through_stock_bottom``: a cut below the stock's bottom (a through drill's point): whatever is under the stock
      there must allow it. Not said when the same cut is a ``fixture_collision``.
    - ``assumes_prepared_hole``: a tap or an internal thread mill on a hole no earlier operation drills, bores or
      mills.

Notes (``info``, not checks)
    ``assumed_fixture`` (the fixture sizes the collision checks assumed), ``unused_tool`` (a tool the job lists and
    no operation runs: it is not drawn or checked, so its type may be one with no tool shape yet), ``cycle_time``,
    ``tool_changes`` and ``post``.

The B-rep sweep (:func:`brep_intrusion`, :func:`swept_runs`, :func:`intended_part`) is also what the operation
suites assert soundness with. Stdlib only at import; OCP, shapely and numpy are imported inside the functions.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from cadgen._internal.cam.operations.base import PLAN_ERROR_KINDS, finding

if TYPE_CHECKING:
    from cadgen._internal.cam.job import Job
    from cadgen._internal.cam.operations.context import PlanContext, Planned
    from cadgen._internal.cam.run import PlannedOp

__all__ = ["CHECK_KINDS", "ERROR_KINDS", "FULL_SLOT_DIAMETERS", "INFO_KINDS", "INTENDED_CUTS", "LONG_STICKOUT_RATIO",
           "THIN_WALL_MM", "UNSWEPT", "WARNING_KINDS", "brep_intrusion", "check_job", "intended_part", "swept_runs",
           "toolpath_context"]

ERROR_KINDS = ("gouge", "holder_collision", "fixture_collision", "unreachable", "tool_too_large", "tool_too_small",
               "too_deep", "over_travel", "no_post_support", "empty_operation")
WARNING_KINDS = ("corner_left", "rest_material", "rpm_clamped", "feed_clamped", "full_slot", "thin_wall",
                 "long_stickout", "uncut_feature", "through_stock_bottom", "assumes_prepared_hole")
#: Notes: what the job assumed or comes to, not checks.
INFO_KINDS = ("assumed_fixture", "unused_tool", "cycle_time", "tool_changes", "post")
#: The checks the result lists, each with its verdict.
CHECK_KINDS = ERROR_KINDS + WARNING_KINDS
assert set(PLAN_ERROR_KINDS) <= set(ERROR_KINDS)

#: Stickout over this many tool diameters deflects and chatters (the usual L/D rule).
LONG_STICKOUT_RATIO = 4.0
#: Full-width cutting longer than this many tool diameters in one operation is worth a warning.
FULL_SLOT_DIAMETERS = 5.0
#: A wall thinner than this, mm, between a hole or pocket and the part's outside.
THIN_WALL_MM = 1.0
#: How far the tool envelope is shrunk before it is measured against the part, so touching is not entering, mm.
PROBE_MM = 0.005
#: Points along a move that changes height are no further apart than this, mm, in the B-rep sweep.
SWEEP_STEP_MM = 0.5
#: Below this distance, mm, a measured clearance is contact.
_TOUCH = 1e-9
#: Operations whose cut does not make a hole, pocket or slot (it marks or edges one).
_MARKS = ("spot_drill", "chamfer_2d")
#: Operations that make a hole a tap or thread mill can follow.
_HOLE_MAKERS = ("drill", "peck", "chip_break", "ream", "boring_cycle", "bore", "circular", "pocket_2d", "back_bore")
#: Tools that work inside a hole an earlier operation made: their limit is their reach, not their cutting length.
_REACHING = ("boring_bar", "back_bore", "thread_mill")


# --- words -------------------------------------------------------------------------------------------------

def _n(value: float) -> str:
    """A number as a person reads it: up to three decimals, no trailing zeros, never ``-0``."""
    value = round(float(value), 3) + 0.0
    return f"{value:g}"


def _xyz(point) -> str:
    return "X{} Y{} Z{}".format(*(_n(c) for c in point))


def _label(planned_op: "PlannedOp") -> str:
    return planned_op.ctx.label


def _item(planned_op: "PlannedOp", text: str, *, tool: str | None = None, at=None, **extra) -> dict:
    """One finding item: the operation, its tool and setup, and where (``at``: setup frame in, part frame out)."""
    ctx = planned_op.ctx
    item = {"text": text, "operation": planned_op.index, "setup": ctx.setup.setup.id, "tool": tool or ctx.tool.id}
    if at is not None:
        item["at"] = [round(float(c), 3) + 0.0 for c in ctx.to_part(at)]
    item.update(extra)
    return item


# --- the job's checks ----------------------------------------------------------------------------------------

def check_job(job: "Job", planned_ops, *, part, stock, setups: dict, features: dict) -> list[dict]:
    """Every finding of a planned job (not yet sorted): the operations' own (feeds clamps merged per tool), then
    each check of the module's docstring, then the notes. ``planned_ops`` are ``run.PlannedOp``, in running order;
    an operation that raised ``PlanError`` is not among them (its finding is already the run's)."""
    found: list[dict] = []
    found += _operation_findings(planned_ops)
    hits_fixture: set[int] = set()
    for planned_op in planned_ops:
        for toolpath in planned_op.planned.toolpaths:
            ctx = toolpath_context(planned_op.ctx, toolpath)
            found += _gouge(planned_op, ctx, toolpath)
            found += _holder(planned_op, ctx, toolpath)
            fixture = _fixture(planned_op, ctx, toolpath)
            if fixture:
                hits_fixture.add(planned_op.index)
            found += fixture
            found += _too_deep(planned_op, ctx, toolpath)
        found += _empty(planned_op)
        if planned_op.index not in hits_fixture:
            found += _below_stock(planned_op)
        found += _corners(planned_op)
        found += _full_slot(planned_op)
    found += _over_travel(job, planned_ops, setups)
    found += _long_stickout(job, planned_ops)
    touched = {planned_op.index: _touched(planned_op, features) for planned_op in planned_ops}
    found += _uncut(job, planned_ops, features, touched, part)
    found += _thin_walls(planned_ops, features, touched, part)
    found += _prepared_holes(planned_ops, touched)
    found += _notes(job, planned_ops, setups)
    return found


def _operation_findings(planned_ops) -> list[dict]:
    """The operations' own findings, each item naming its operation by index (``naming_operation``), with the feeds'
    clamps merged: one per kind, tool and sentence, listing every operation it applies to (the same clamp on six
    operations is one thing to fix)."""
    from cadgen._internal.cam.operations.base import naming_operation

    out: list[dict] = []
    merged: dict[tuple, dict] = {}
    for planned_op in planned_ops:
        for item in planned_op.planned.findings:
            if item["type"] not in ("rpm_clamped", "feed_clamped"):
                out.append(naming_operation(item, planned_op.index, _label(planned_op)))
                continue
            first = item["items"][0] if item["items"] else {}
            where = first.get("where", "")
            summary = item["summary"].removeprefix(f"{where}, ") if where else item["summary"]
            key = (item["type"], first.get("tool"), summary, item["description"])
            if key not in merged:
                merged[key] = {"finding": item, "summary": summary, "where": [], "items": []}
                out.append(merged[key])
            entry = merged[key]
            if where and where not in entry["where"]:
                entry["where"].append(where)
            entry["items"] += [{"text": f"{i.get('where', '')} {i.get('tool', '')}".strip(), "operation": planned_op.index,
                                "tool": i.get("tool")} for i in item["items"]]
    result = []
    for item in out:
        if "finding" not in item:
            result.append(item)
            continue
        where = item["where"]
        summary = item["summary"]
        if where:
            summary += f", in {where[0]}" if len(where) == 1 else f", in {len(where)} operations: {', '.join(where)}"
        source = item["finding"]
        result.append(finding(source["severity"], source["type"], summary, source["description"], item["items"]))
    return result


# --- moves ---------------------------------------------------------------------------------------------------

def _flat(toolpath):
    """``(start, move)`` for every plain move of ``toolpath``, canned cycles expanded into the moves they stand for."""
    from cadgen._internal.cam.toolpath import expand_cycle, walk

    out = []
    for here, move in walk(toolpath.moves, toolpath.start):
        if move.kind == "cycle":
            out += list(walk(expand_cycle(move, here), here))
        else:
            out.append((tuple(here), move))
    return out


def _polyline(here, move, sag: float = 0.005) -> list[tuple[float, float, float]]:
    """Points from ``here`` to the move's end; an arc sampled so each chord strays at most ``sag`` mm."""
    from cadgen._internal.cam.toolpath import arc_sweep

    end = tuple(move.xyz)
    if move.kind not in ("arc_cw", "arc_ccw"):
        return [tuple(here), end]
    cx, cy = move.center
    radius = math.hypot(here[0] - cx, here[1] - cy)
    a0 = math.atan2(here[1] - cy, here[0] - cx)
    sweep = arc_sweep(here, move)
    step = 2 * math.acos(max(-1.0, 1 - sag / radius)) if radius > sag else math.pi / 2
    count = max(2, math.ceil(abs(sweep) / max(step, 1e-6)))
    return [tuple(here)] + [(cx + radius * math.cos(a0 + sweep * k / count), cy + radius * math.sin(a0 + sweep * k / count),
                             here[2] + (end[2] - here[2]) * k / count) for k in range(1, count + 1)]


def _segments(toolpath, *, cutting: bool | None = None):
    """The toolpath as straight segments ``[(a, b, kind)]`` (arcs as fine chords, cycles expanded, the start
    included); only cutting moves with ``cutting=True``."""
    out = []
    for here, move in _flat(toolpath):
        if cutting and move.kind == "rapid":
            continue
        points = _polyline(here, move)
        out += [(a, b, move.kind) for a, b in zip(points, points[1:])]
    return out


def toolpath_context(ctx: "PlanContext", toolpath) -> "PlanContext":
    """``ctx`` with the tool that runs ``toolpath`` (the operation's own, or another of the job's: an engraving's
    clear_tool)."""
    from dataclasses import replace

    from cadgen._internal.cam.tools import profile

    if toolpath.tool == ctx.tool.id:
        return ctx
    tool = ctx.job.tool(toolpath.tool)
    return replace(ctx, tool=tool, profile=profile(tool))


# --- gouge -----------------------------------------------------------------------------------------------------

def _gouge(planned_op: "PlannedOp", ctx: "PlanContext", toolpath) -> list[dict]:
    if not toolpath.moves:
        return []
    shape = intended_part(ctx, planned_op.planned)
    solids = _ToolSolids(_cutting_outline(ctx.profile))
    depth, where = 0.0, None
    for run in swept_runs(toolpath):
        d, at = _intrusion(shape, run, solids)
        if d > depth:
            depth, where = d, at
    if depth <= ctx.tol:
        return []
    near = f" near {_xyz(where)}" if where is not None else ""
    return [finding(
        "error", "gouge",
        f"{_label(planned_op)}: {ctx.tool.id} cuts {_n(depth)} mm into the part{near} (setup {ctx.setup.setup.id})",
        f"the tool's cutting edges reach past the part's surface by more than the {_n(ctx.tol)} mm tolerance, so the "
        "part would come out undersize there. Measured on the part's B-rep with the tool swept along every move, "
        "rapids included; check the operation's features, stock_to_leave_mm and the tool's size",
        [_item(planned_op, f"{ctx.tool.id} {_n(depth)} mm into the part", tool=ctx.tool.id, at=where,
               depth_mm=round(depth, 3))])]


# --- holder and shank ----------------------------------------------------------------------------------------

def _holder(planned_op: "PlannedOp", ctx: "PlanContext", toolpath) -> list[dict]:
    """The shank above the flutes and the holder against the part (B-rep sweep) and the stock (its footprint below
    the material top)."""
    import numpy as np

    profile = ctx.profile
    body = _body_outline(profile)
    if body is None or not toolpath.moves:
        return []
    segments = _segments(toolpath)
    if not segments:
        return []
    ends = np.asarray([(a, b) for a, b, _ in segments], dtype=float)
    lowest = float(ends[:, :, 2].min())
    out = []
    bottom = profile.cutting_length_mm
    if lowest + bottom < ctx.part_top_z - ctx.tol:
        shape = intended_part(ctx, planned_op.planned)
        solids = _ToolSolids(body)
        for run in swept_runs(toolpath):
            depth, where = _intrusion(shape, run, solids, depth=False)
            if depth > 0:
                what = _body_word(profile)
                out.append(finding(
                    "error", "holder_collision",
                    f"{_label(planned_op)}: {ctx.tool.id}'s {what} hits the part near {_xyz(where)} "
                    f"(setup {ctx.setup.setup.id})",
                    f"above its {_n(bottom)} mm of flutes the tool is {_body_text(profile)}, and the part stands in "
                    "its way; a longer stickout, a slimmer holder or a shallower cut clears it",
                    [_item(planned_op, f"{ctx.tool.id} {what} against the part", at=where, part=what)]))
                break
    out += _holder_stock(planned_op, ctx, segments)
    return out


def _holder_stock(planned_op: "PlannedOp", ctx: "PlanContext", segments) -> list[dict]:
    import numpy as np
    import shapely

    from cadgen._internal.cam.operations.geometry2d import stock_outline

    profile = ctx.profile
    top = ctx.top_z
    pieces = [piece for piece in profile.envelope()
              if piece.part == "holder" or (piece.part == "shank" and piece.radius > ctx.radius + ctx.tol)]
    if not pieces:
        return []
    stock = None
    ends = np.asarray([(a, b) for a, b, _ in segments], dtype=float)
    for piece in pieces:
        low = np.minimum(ends[:, 0, 2], ends[:, 1, 2]) + piece.z0
        candidates = np.nonzero(low < top - ctx.tol)[0]
        if not len(candidates):
            continue
        if stock is None:
            stock = stock_outline(ctx)
        lines = shapely.linestrings([ends[i, :, :2] if np.hypot(*(ends[i, 1, :2] - ends[i, 0, :2])) > 1e-9
                                     else ends[i, :, :2] + [[0, 0], [1e-9, 0]] for i in candidates])
        distance = shapely.distance(lines, stock)
        hit = np.nonzero(distance < piece.radius - ctx.tol)[0]
        if not len(hit):
            continue
        index = candidates[hit[0]]
        a, b = ends[index]
        where = tuple(a if a[2] <= b[2] else b)
        what = "holder" if piece.part == "holder" else "shank"
        return [finding(
            "error", "holder_collision",
            f"{_label(planned_op)}: {ctx.tool.id}'s {what} comes down into the stock near {_xyz(where)} "
            f"(setup {ctx.setup.setup.id}), below the top of the material at Z{_n(top)}",
            f"the {what} ({_n(2 * piece.radius)} mm across, from {_n(piece.z0)} mm above the tip) would need "
            f"{_n(top - (where[2] + piece.z0))} mm more room. Phase 1 does not simulate the stock: this compares against "
            "the stock as it was before any operation, taken down only to the top of the material this operation "
            "starts from, so an earlier operation may have cleared the way; a longer stickout clears it for sure",
            [_item(planned_op, f"{ctx.tool.id} {what} against the stock", at=where, part=what)])]
    return []


def _body_word(profile) -> str:
    return "shank" if profile.holder_diameter_mm is None else "shank or holder"


def _body_text(profile) -> str:
    text = f"a {_n(profile.shank_diameter_mm)} mm shank up to {_n(profile.stickout_mm)} mm from the tip"
    if profile.holder_diameter_mm is not None:
        text += f", then a {_n(profile.holder_diameter_mm)} mm holder"
    return text


# --- fixture ---------------------------------------------------------------------------------------------------

def _fixture(planned_op: "PlannedOp", ctx: "PlanContext", toolpath) -> list[dict]:
    """Every part of the tool (cutter, shank, holder as cylinders) against each fixture volume, along every move."""
    import numpy as np
    import shapely

    fixture = ctx.setup.fixture
    if not fixture.volumes or not toolpath.moves:
        return []
    segments = _segments(toolpath)
    ends = np.asarray([(a, b) for a, b, _ in segments], dtype=float)
    low_z = np.minimum(ends[:, 0, 2], ends[:, 1, 2])
    high_z = np.maximum(ends[:, 0, 2], ends[:, 1, 2])
    tol = ctx.tol
    out = []
    for volume in fixture.volumes:
        if hasattr(volume, "radius"):
            vz0, vz1 = volume.z_min, volume.z_max
        else:
            vz0, vz1 = volume.min[2], volume.max[2]
        for piece in ctx.profile.envelope():
            near = np.nonzero((low_z + piece.z0 < vz1 - tol) & (high_z + piece.z1 > vz0 + tol))[0]
            if not len(near):
                continue
            starts, stops = ends[near, 0, :2], ends[near, 1, :2]
            if hasattr(volume, "radius"):
                distance = _point_segment(np.asarray(volume.center, dtype=float), starts, stops) - volume.radius
            else:
                lines = shapely.linestrings([np.asarray([a, b if np.hypot(*(b - a)) > 1e-9 else a + [1e-9, 0]])
                                             for a, b in zip(starts, stops)])
                distance = shapely.distance(lines, shapely.box(volume.min[0], volume.min[1], volume.max[0],
                                                                volume.max[1]))
            hit = np.nonzero(distance < piece.radius - tol)[0]
            if not len(hit):
                continue
            a, b = ends[near[hit[0]]]
            where = tuple(a if a[2] <= b[2] else b)
            what = "cutter" if piece.part == "cutter" else piece.part
            into = vz1 - (where[2] + piece.z0)
            depth = f" {_n(into)} mm" if 0 < into < vz1 - vz0 else ""
            out.append(finding(
                "error", "fixture_collision",
                f"{_label(planned_op)}: {ctx.tool.id}'s {what} runs{depth} into the {volume.name} near {_xyz(where)} "
                f"(setup {ctx.setup.setup.id})",
                f"the {fixture.type} is modelled from setup {ctx.setup.setup.id}'s fixture ({_sizes(fixture)}); "
                "move the cut, change how the stock is held (taller parallels, a sacrificial plate) or shorten the "
                "cut",
                [_item(planned_op, f"{ctx.tool.id} {what} against the {volume.name}", at=where, fixture=volume.name,
                       part=what)]))
            break
    return out


def _point_segment(point, starts, stops):
    """Distances from one 2D point to many segments."""
    import numpy as np

    ab = stops - starts
    span = np.maximum((ab * ab).sum(axis=1), 1e-18)
    t = np.clip(((point - starts) * ab).sum(axis=1) / span, 0.0, 1.0)
    return np.hypot(*(starts + ab * t[:, None] - point).T)


def _sizes(fixture) -> str:
    sizes = ", ".join(f"{key} {_n(value)}" for key, value in sorted(fixture.sizes.items())
                      if key != "jaws_above_stock_mm")
    if fixture.assumed:
        sizes += f"; assumed, as the job does not state them: {', '.join(fixture.assumed)}"
    return sizes


# --- depth -----------------------------------------------------------------------------------------------------

def _too_deep(planned_op: "PlannedOp", ctx: "PlanContext", toolpath) -> list[dict]:
    segments = _segments(toolpath, cutting=True)
    if not segments:
        return []
    lowest = min(min(a[2], b[2]) for a, b, _ in segments)
    profile = ctx.profile
    reaching = profile.type in _REACHING
    limit = profile.reach_mm if reaching else profile.cutting_length_mm
    depth = ctx.top_z - lowest
    if depth <= limit + ctx.tol:
        return []
    what = f"{_n(limit)} mm of reach" if reaching else f"{_n(limit)} mm of flutes"
    above = "the tool's neck and shank would be buried" if reaching else "the shank would rub in the cut above them"
    return [finding(
        "error", "too_deep",
        f"{_label(planned_op)}: {ctx.tool.id} cuts {_n(depth)} mm deep with {what}: {above}",
        f"measured from the top of the material at Z{_n(ctx.top_z)} down to the deepest cutting move at Z{_n(lowest)} "
        f"(setup {ctx.setup.setup.id}); use a tool with longer flutes (or reach), or cut less deep",
        [_item(planned_op, f"{ctx.tool.id} {_n(depth)} mm deep", depth_mm=round(depth, 3), limit_mm=round(limit, 3))])]


def _below_stock(planned_op: "PlannedOp") -> list[dict]:
    ctx = planned_op.ctx
    bottom = ctx.setup.stock_bottom_z
    out = []
    for toolpath in planned_op.planned.toolpaths:
        segments = _segments(toolpath, cutting=True)
        if not segments:
            continue
        a, b, _ = min(segments, key=lambda s: min(s[0][2], s[1][2]))
        where = a if a[2] <= b[2] else b
        under = bottom - where[2]
        if under <= ctx.tol:
            continue
        out.append(finding(
            "warning", "through_stock_bottom",
            f"{_label(planned_op)}: {toolpath.tool} goes {_n(under)} mm below the bottom of the stock, down to "
            f"Z{_n(where[2])} (setup {ctx.setup.setup.id}; the stock's bottom is at Z{_n(bottom)})",
            "whatever is under the stock there must allow it: a spoilboard or sacrificial plate, or a gap between "
            "parallels; the fixture as modelled is clear of it",
            [_item(planned_op, f"{toolpath.tool} {_n(under)} mm under the stock", tool=toolpath.tool, at=where,
                   below_mm=round(under, 3))]))
    return out


def _empty(planned_op: "PlannedOp") -> list[dict]:
    if any(move.kind != "rapid" for tp in planned_op.planned.toolpaths for move in tp.moves):
        return []
    return [finding(
        "error", "empty_operation", f"{_label(planned_op)} plans no cutting move: it cuts nothing",
        "check its features, region or depth: an operation that cuts nothing is a mistake in the job",
        [_item(planned_op, _label(planned_op))])]


# --- from the operations' numbers ----------------------------------------------------------------------------------

def _corners(planned_op: "PlannedOp") -> list[dict]:
    out = []
    ctx = planned_op.ctx
    for corner in planned_op.planned.stats.get("corner_left", ()) if isinstance(planned_op.planned.stats, dict) else ():
        out.append(finding(
            "warning", "corner_left",
            f"{_label(planned_op)}: {ctx.tool.id} leaves the R{_n(corner['corner_radius_mm'])} inside corners of "
            f"{corner['ref']} at R{_n(corner['tool_leaves_radius_mm'])}: material stays in each corner",
            "a round tool cuts an inside corner no tighter than its radius (plus stock_to_leave_mm); a tool of "
            f"{_n(2 * corner['corner_radius_mm'])} mm or less across cuts these corners, or leave them if the part "
            "allows",
            [_item(planned_op, corner["ref"], ref=corner["ref"])]))
    return out


def _full_slot(planned_op: "PlannedOp") -> list[dict]:
    ctx = planned_op.ctx
    stats = planned_op.planned.stats
    length = stats.get("full_slot_mm", 0.0) if isinstance(stats, dict) else 0.0
    if planned_op.operation.op.startswith("adaptive") or not length > FULL_SLOT_DIAMETERS * ctx.profile.diameter_mm:
        return []
    return [finding(
        "warning", "full_slot",
        f"{_label(planned_op)}: {ctx.tool.id} cuts {_n(length)} mm at its full width, every flute in the cut at once",
        f"over {_n(FULL_SLOT_DIAMETERS)} tool diameters of full-width cutting (the links from one ring or row to the "
        "next): it loads the tool hardest and packs the chips; a smaller stepdown or adaptive_2d clearing is gentler",
        [_item(planned_op, f"{ctx.tool.id} {_n(length)} mm at full width", length_mm=round(length, 3))])]


def _long_stickout(job: "Job", planned_ops) -> list[dict]:
    from cadgen._internal.cam.tools import profile

    users: dict[str, list] = {}
    for planned_op in planned_ops:
        for toolpath in planned_op.planned.toolpaths:
            if toolpath.moves:
                users.setdefault(toolpath.tool, [])
                if planned_op not in users[toolpath.tool]:
                    users[toolpath.tool].append(planned_op)
    out = []
    for tool in job.tools:
        if tool.id not in users or not tool.diameter_mm:
            continue                # an unused tool (perhaps of a type with no shape yet) is not looked at
        shape = profile(tool)
        ratio = shape.stickout_mm / shape.diameter_mm
        if ratio <= LONG_STICKOUT_RATIO + 1e-9:
            continue
        stated = "" if tool.stickout_mm is not None else (
            " The stickout is assumed (the job states no stickout_mm): the flute length plus one diameter.")
        out.append(finding(
            "warning", "long_stickout",
            f"{tool.id} sticks out {_n(shape.stickout_mm)} mm, {ratio:.1f} times its {_n(shape.diameter_mm)} mm "
            "diameter: it will bend and chatter",
            f"over {_n(LONG_STICKOUT_RATIO)} times the diameter a tool deflects enough to mark the walls and break "
            f"easily; hold it shorter, or take lighter cuts.{stated}",
            [_item(planned_op, f"{_label(planned_op)} {tool.id}", tool=tool.id) for planned_op in users[tool.id]]))
    return out


# --- features --------------------------------------------------------------------------------------------------

def _refs(value) -> list[str]:
    """Every ``ref`` in an operation's stats."""
    if isinstance(value, dict):
        out = [value["ref"]] if isinstance(value.get("ref"), str) else []
        for item in value.values():
            if isinstance(item, (list, dict)):
                out += _refs(item)
        return out
    if isinstance(value, list):
        return [ref for item in value for ref in _refs(item)]
    return []


def _touched(planned_op: "PlannedOp", features: dict) -> set[str]:
    """The refs of the features (holes, pockets, slots) of its setup an operation cuts: those its ``features`` and
    its stats name, and those whose opening a ``region`` holds."""
    from cadgen._internal.cam.features import feature_for

    ctx = planned_op.ctx
    found = features.get(ctx.setup.setup.id, ())
    reached = [f.feature for f in found]
    occurrence = ctx.part.ref
    names = list(planned_op.operation.features) + _refs(planned_op.planned.stats)
    out = set()
    for name in names:
        name = name.strip()
        feature = feature_for(reached, name if name.startswith("#") else f"{occurrence}.{name}")
        if feature is not None:
            out.add(feature.ref)
    if planned_op.operation.region is not None:
        from shapely.geometry import Point, Polygon

        area = Polygon(planned_op.operation.region)
        for item in found:
            if item.feature.kind in ("hole", "pocket", "slot") and area.contains(Point(item.top[0], item.top[1])):
                out.add(item.feature.ref)
    return out


def _uncut(job: "Job", planned_ops, features: dict, touched: dict, part) -> list[dict]:
    from cadgen._internal.cam.features import feature_for

    cut = set()
    for planned_op in planned_ops:
        if planned_op.operation.op not in _MARKS:
            cut |= touched[planned_op.index]
    planned = {planned_op.index for planned_op in planned_ops}
    for index, operation in enumerate(job.operations):
        if index in planned:      # an operation that could not be planned: its error finding says so already
            continue
        reached = [found.feature for found in features.get(operation.setup, ())]
        for name in operation.features:
            name = name.strip()
            feature = feature_for(reached, name if name.startswith("#") else f"{part.ref}.{name}")
            if feature is not None:
                cut.add(feature.ref)
    seen: dict[str, tuple] = {}
    for setup in job.setups:
        for item in features.get(setup.id, ()):
            feature = item.feature
            if feature.kind in ("hole", "pocket", "slot") and feature.ref not in cut:
                seen.setdefault(feature.ref, (feature, []))[1].append(setup.id)
    out = []
    for ref, (feature, setup_ids) in seen.items():
        size = _feature_size(feature)
        out.append(finding(
            "warning", "uncut_feature",
            f"{ref} ({feature.kind}{size}) is reached from setup{'s' if len(setup_ids) > 1 else ''} "
            f"{' and '.join(setup_ids)} but no operation cuts it",
            "the stock stays solid there; if the part needs it, add an operation for it",
            [{"text": f"{ref} {feature.kind}", "ref": ref, "setup": setup_ids[0]}]))
    return out


def _feature_size(feature) -> str:
    size = feature.size
    if feature.kind == "hole":
        text = f", {_n(size['diameter_mm'])} mm"
        return text + (" through" if size.get("through") else f", {_n(size['depth_mm'])} mm deep")
    if "length_mm" in size and "width_mm" in size:
        text = f", {_n(size['length_mm'])} x {_n(size['width_mm'])} mm"
        return text + (f", {_n(size['depth_mm'])} mm deep" if "depth_mm" in size else "")
    return ""


def _thin_walls(planned_ops, features: dict, touched: dict, part) -> list[dict]:
    """A hole or pocket an operation cuts against the outside walls of its setup's profiles: the kernel's distance
    between their faces."""
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape

    out = []
    done = set()
    for planned_op in planned_ops:
        found = features.get(planned_op.ctx.setup.setup.id, ())
        profiles = [f.feature for f in found if f.feature.kind == "profile"]
        if not profiles:
            continue
        for item in found:
            feature = item.feature
            if feature.kind not in ("hole", "pocket") or feature.ref not in touched[planned_op.index] \
                    or feature.ref in done:
                continue
            done.add(feature.ref)
            walls = _faces(part, [r for r in feature.refs if part.is_wall(_ordinal(r), feature.axis)])
            if walls is None:
                continue
            for outline in profiles:
                outside = _faces(part, outline.refs)
                measure = BRepExtrema_DistShapeShape(walls, outside)
                if not measure.IsDone():
                    continue
                gap = measure.Value()
                if gap < THIN_WALL_MM - 1e-6:
                    point = measure.PointOnShape1(1)
                    at = [round(c, 3) + 0.0 for c in (point.X(), point.Y(), point.Z())]
                    out.append(finding(
                        "warning", "thin_wall",
                        f"{feature.ref} ({feature.kind}) leaves a {_n(gap)} mm wall to the part's outside "
                        f"({outline.ref}) at its thinnest",
                        f"a wall under {_n(THIN_WALL_MM)} mm bends away from the cutter and chatters; take light "
                        "finishing passes on it, support it, or check the part needs it that thin",
                        [{"text": f"{feature.ref} to {outline.ref}", "ref": feature.ref, "at": at,
                          "operation": planned_op.index, "wall_mm": round(gap, 3)}]))
                    break
    return out


def _ordinal(ref: str) -> int:
    return int(ref.rsplit(".f", 1)[1])


def _faces(part, refs):
    shapes = [part.face(_ordinal(ref)).face for ref in refs]
    return _compound(shapes)


def _prepared_holes(planned_ops, touched: dict) -> list[dict]:
    out = []
    for position, planned_op in enumerate(planned_ops):
        op = planned_op.operation.op
        params = planned_op.operation.params
        if op not in ("tap", "rigid_tap", "thread_mill") or (op == "thread_mill" and getattr(params, "external", False)):
            continue
        made = set()
        for earlier in planned_ops[:position]:
            if earlier.operation.op in _HOLE_MAKERS:
                made |= touched[earlier.index]
        for ref in sorted(touched[planned_op.index] - made):
            out.append(finding(
                "warning", "assumes_prepared_hole",
                f"{_label(planned_op)}: no earlier operation makes {ref} before {planned_op.ctx.tool.id} threads it, so "
                "the job assumes the hole is already there",
                "a tap or thread mill needs the hole at its size first: drill, bore or mill it in an earlier operation "
                "of this job, or have it drilled before the job runs",
                [_item(planned_op, ref, ref=ref)]))
    return out


# --- the machine -----------------------------------------------------------------------------------------------

def _over_travel(job: "Job", planned_ops, setups: dict) -> list[dict]:
    travel = job.machine.travel_mm
    if not travel:
        return []
    out = []
    for setup in job.setups:
        points = []
        for planned_op in planned_ops:
            if planned_op.operation.setup != setup.id:
                continue
            for toolpath in planned_op.planned.toolpaths:
                if toolpath.moves:
                    low, high = toolpath.bounds()
                    points += [low, high]
        if not points:
            continue
        low = [min(p[axis] for p in points) for axis in range(3)]
        high = [max(p[axis] for p in points) for axis in range(3)]
        for axis, name in enumerate("XYZ"):
            if axis >= len(travel):
                continue
            span = high[axis] - low[axis]
            if span <= travel[axis] + 1e-6:
                continue
            out.append(finding(
                "error", "over_travel",
                f"Setup {setup.id} spans {_n(span)} mm in {name}, more than the machine's {_n(travel[axis])} mm {name} "
                "travel",
                f"the tool tip goes from {name}{_n(low[axis])} to {name}{_n(high[axis])}, rapids included. The work "
                "offset is assumed free to sit anywhere on the machine, so only this span is compared with "
                "machine.travel_mm; no placement of the work makes it fit",
                [{"text": f"setup {setup.id} {name} {_n(span)} mm", "setup": setup.id, "axis": name,
                  "span_mm": round(span, 3)}]))
    return out


def _notes(job: "Job", planned_ops, setups: dict) -> list[dict]:
    from cadgen._internal.cam.posts import get_post

    out = []
    for index, setup in enumerate(job.setups):
        fixture = setups[setup.id].fixture
        if fixture.assumed and any(op.operation.setup == setup.id for op in planned_ops):
            sizes = ", ".join(f"{key} {_n(fixture.sizes[key])}" for key in fixture.assumed if key in fixture.sizes)
            out.append(finding(
                "info", "assumed_fixture",
                f"Setup {setup.id}'s {fixture.type} is modelled with sizes the job does not state: {sizes}",
                f"the fixture collision checks use these; state the real sizes in setups[{index}].fixture to check "
                "against them",
                [{"text": f"setup {setup.id} {fixture.type}", "setup": setup.id, "assumed": list(fixture.assumed)}]))
    named = {op.tool for op in job.operations}
    for planned_op in planned_ops:
        named |= {tp.tool for tp in planned_op.planned.toolpaths}
    for index, tool in enumerate(job.tools):
        if tool.id not in named:
            out.append(finding(
                "info", "unused_tool", f"{tool.id} ({tool.type.replace('_', ' ')}) is listed but no operation uses it",
                "it is not drawn or checked; leave it out of tools if it is not meant for this job",
                [{"text": tool.id, "tool": tool.id}]))
    toolpaths = [tp for planned_op in planned_ops for tp in planned_op.planned.toolpaths if tp.moves]
    if not toolpaths:
        return out
    seconds = max(tp.moves[-1].t for tp in toolpaths)
    minutes, rest = divmod(round(seconds), 60)
    clock = f"{minutes} min {rest} s" if minutes else f"{rest} s"
    out.append(finding(
        "info", "cycle_time", f"Cycle time {clock}: the moves at their feeds and the rapids",
        "tool changes, spindle spin-up and setup changes are not in it", []))
    running = [tp.tool for tp in toolpaths]
    changes = [b for a, b in zip(running, running[1:]) if a != b]
    order = [running[0]] + changes
    out.append(finding(
        "info", "tool_changes",
        f"{len(changes)} tool change{'' if len(changes) == 1 else 's'}" + (f": {' then '.join(order)}" if changes else ""),
        "each time the running order switches tool, across setups too; loading the first tool is not one", []))
    post = get_post(job.machine.post)
    out.append(finding(
        "info", "post", f"G-code for {post.controller} ({post.name} post)",
        "prove the program out on the machine before cutting: run it in the air or single block first", []))
    return out


# --- the B-rep sweep -------------------------------------------------------------------------------------------
# How far a tool's envelope enters the part's B-rep along a toolpath, read by the kernel (BRepExtrema_DistShapeShape).

#: Segments per quarter circle where a tool's outline curves (a ball's end, a bull's corner).
_ARC_STEPS = 64

#: What an operation is meant to cut out of the part as modelled (the model does not show it), by operation: the
#: gouge check measures against the part less exactly that volume, so the tool may go that far and no further.
INTENDED_CUTS = {
    "chamfer_2d": "the chamfer asked for along each top edge the part does not model as chamfered",
    "tap": "the thread: the tap's diameter over the length of a hole drilled at its tap drill size",
    "rigid_tap": "the thread: the tap's diameter over the length of a hole drilled at its tap drill size",
    "thread_mill": "the thread: thread_depth_mm into the hole's wall (or the boss's), over its length",
    "engrave": "a region carved into the part's surface: the region, down to its deepest carve or max_depth_mm",
    "trace": "a region's curve cut into the part's surface: the groove the tool's cutting radius makes along it on "
             "its offset side, depth_mm deep",
}
#: Moves the B-rep sweep leaves out, and why.
UNSWEPT = {
    "oriented_bar": "an oriented boring bar's shift (fine_bore, back_bore) moves its one-sided edge off the wall or "
                    "passes its eccentric head through the hole; a round envelope cannot stand for that bar, so the "
                    "shifted moves are left out and the bar is checked on its axis, cutting",
}


def _cutting_outline(profile) -> list[tuple[float, float]]:
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


def _body_outline(profile) -> list[tuple[float, float]] | None:
    """The tool above its cutting length (shank, neck, holder) as ``(r, z)`` from the axis at the top of the
    cutting length to the axis at the top of the holder; ``None`` when nothing is above the flutes."""
    bottom = profile.cutting_length_mm
    if profile.length_mm <= bottom + 1e-9:
        return None
    points = [(0.0, bottom), (profile.radius_at(bottom + 1e-6), bottom)]
    points += [(r, z) for r, z in profile.outline() if z > bottom + 1e-9]
    out = [points[0]]
    for point in points[1:]:
        if point != out[-1]:
            out.append(point)
    return out if len(out) >= 3 else None


class _ToolSolids:
    """The pieces a toolpath's swept envelope is made of, from a half outline ``[(r, z)]`` (axis to axis), shrunk by
    ``shrink`` mm all round (so touching is not reading as entering): the envelope at a point, a straight level move
    as a prism of its cross-section, a level arc as that cross-section turned about the arc's centre; and a ``rise``:
    what a move that changes height sweeps above the top of the envelope at its lowest point, a column of the
    envelope's top radius ``rise`` tall over a level piece (the envelope never narrows going up, below its top, for a
    cutter; for a body above the flutes the level piece at the lowest point and that column still hold what the move
    sweeps, as the column is as wide as the top, the holder)."""

    def __init__(self, half):
        from shapely.geometry import Polygon

        self.width = max(r for r, _ in half)
        self.top = half[-1][1]
        self.top_radius = max(r for r, z in half if abs(z - self.top) < 1e-12)
        self.full = Polygon([(r, z) for r, z in half] + [(-r, z) for r, z in reversed(half)]).buffer(0)
        self._cache: dict = {}

    def _section(self, shrink: float, rise: float = 0.0):
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
        key = ("tool", shrink, rise)
        if key not in self._cache:
            from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol
            from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt
            from shapely.geometry import box

            section = self._section(shrink, rise)
            half = None if section is None else section.intersection(box(0, -1e4, 1e4, 1e4))
            if half is None or half.is_empty or half.geom_type != "Polygon":
                self._cache[key] = None
            else:
                face = self._face([(u, 0.0, z) for u, z in list(half.exterior.coords)[:-1]])
                self._cache[key] = BRepPrimAPI_MakeRevol(face, gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1))).Shape()
        return self._cache[key]

    def piece(self, spec, shrink: float, rise: float = 0.0):
        """One piece from its spec: ``("at", xyz)``, ``("line", a, b)`` (level), ``("arc", a, centre, sweep)``,
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
            # Tighter than the envelope: what lies past the arc's centre is swept by its other side, so the
            # cross-section is cut there (its turned sector and the envelope at both ends are then the swept volume).
            from shapely.geometry import box

            clipped = section.intersection(box(-radius, -1e4, 1e4, 1e4))
            if clipped.is_empty or clipped.geom_type != "Polygon":
                return None
            coords = list(clipped.exterior.coords)[:-1]
        face = self._face([(radius + u, 0.0, z) for u, z in coords])
        turned = BRepPrimAPI_MakeRevol(face, gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), abs(sweep)).Shape()
        return self._placed(turned, start, (centre[0], centre[1], a[2]))


def _pieces(toolpath, step: float) -> list:
    """The specs of the pieces a toolpath sweeps: level lines and arcs whole (the envelope itself where one ends and
    the next does not carry straight on), every other move sampled ``step`` apart."""
    from cadgen._internal.cam.toolpath import arc_sweep

    def heading(here, move, at_end: bool):
        if move.kind in ("arc_cw", "arc_ccw"):
            cx, cy = move.center
            p = move.xyz if at_end else here
            sign = 1.0 if move.kind == "arc_ccw" else -1.0
            return (-(p[1] - cy) * sign, (p[0] - cx) * sign)
        return (move.xyz[0] - here[0], move.xyz[1] - here[1])

    flat = _flat(toolpath)
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
            # the top of the envelope there) with the column of the envelope's top radius over it (all it sweeps
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
                continue   # carries straight on: the next piece starts with the whole cross-section here
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


def _spec_point(spec):
    """A point of a piece, for saying where it is."""
    if spec[0] in ("bound", "rise"):
        return _spec_point(spec[1] if spec[0] == "bound" else spec[2])
    return tuple(spec[1])


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


def _intrusion(part_shape, toolpath, solids: _ToolSolids, step: float = SWEEP_STEP_MM, *, probe: float = PROBE_MM,
               depth: bool = True):
    """``(depth, where)``: how far (mm) the envelope enters ``part_shape`` along ``toolpath`` and a point of the
    toolpath near the deepest place (``None`` when clear). ``depth=False`` stops at the first contact: ``(probe,
    where)`` when the envelope meets the part, ``(0, None)`` when not. See :func:`brep_intrusion`."""
    specs = _pieces(toolpath, step)

    def touching(chunk: list) -> list:
        if _distance(part_shape, [solids.piece(spec, probe) for spec in chunk]) > _TOUCH:
            return []
        if len(chunk) == 1:
            return touching(chunk[0][2]) if chunk[0][0] == "bound" else chunk
        middle = len(chunk) // 2
        return touching(chunk[:middle]) + touching(chunk[middle:])

    clearance = _distance(part_shape, [solids.piece(spec, probe) for spec in specs])
    if clearance > _TOUCH:
        return (probe - clearance if depth else 0.0), None
    found = touching(specs)
    if not found:
        return (probe if depth else 0.0), None
    if not depth:
        return probe, _spec_point(found[0])
    low, high = probe, solids.width
    while high - low > 1e-3:
        middle = (low + high) / 2
        if _distance(part_shape, [solids.piece(spec, middle) for spec in found]) > _TOUCH:
            high = middle
        else:
            low = middle
    deepest = next((spec for spec in found if _distance(part_shape, [solids.piece(spec, low)]) <= _TOUCH), found[0])
    return high, _spec_point(deepest)


def brep_intrusion(part_shape, toolpath, profile, step: float = SWEEP_STEP_MM, *, probe: float = PROBE_MM) -> float:
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
    return _intrusion(part_shape, toolpath, _ToolSolids(_cutting_outline(profile)), step, probe=probe)[0]


def swept_runs(toolpath) -> list:
    """``toolpath`` as the B-rep sweep takes it: whole, but for an oriented boring bar's cycles (:data:`UNSWEPT`),
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


def intended_part(ctx: "PlanContext", planned: "Planned"):
    """The part as the operation is meant to leave it, in the setup's frame: ``ctx.part_shape`` less its
    :data:`INTENDED_CUTS` (the part itself for any other operation)."""
    op = ctx.operation.op
    if op not in INTENDED_CUTS:
        return ctx.part_shape
    key = ("intended_part", repr(ctx.operation))
    if key not in ctx.cache:
        if op == "chamfer_2d":
            ctx.cache[key] = _chamfered(ctx, planned.stats)
        else:
            ctx.cache[key] = _less(ctx.part_shape, _intended_solids(ctx))
    return ctx.cache[key]


def _intended_solids(ctx: "PlanContext") -> list:
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
        for _ref, found in ctx.targets((kind,)):
            if params.external:
                wall = next(ctx.part.face(_ordinal(r)) for r in found.feature.refs
                            if ctx.part.face(_ordinal(r)).surface == "cylinder")
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


def _chamfered(ctx: "PlanContext", stats):
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
        walls = [_ordinal(r) for r in found.feature.refs]
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
