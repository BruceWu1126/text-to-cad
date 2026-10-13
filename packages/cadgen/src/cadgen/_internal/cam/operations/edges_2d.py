"""2D edge work: a chamfer along top edges, a V-carve of a closed area, and a cut along a curve.

Each operation reads its own keys (``parse``, stdlib) and plans on the shared layer (:mod:`.context`,
:mod:`.geometry2d`, :mod:`.emit`), like :mod:`.milling_2d`. The two cone tools (``chamfer_mill``, ``v_bit``) are
read through :func:`cone`: half the included ``tip_angle_deg``, the flat tip's radius, and how tall the cone is
before the tool reaches its full diameter (the profile's ``tip_length_mm``). At ``h`` mm above its tip a cone
cuts a circle of radius ``tip radius + h x tan(half angle)``; every depth below comes from that.

``chamfer_2d``
    Breaks the top edge of each ``profile`` (from outside), ``pocket`` and ``hole`` (from inside) it is pointed at
    (``all: true``: every one the setup reaches) with a chamfer ``width_mm`` wide (0.5) measured across the top
    face; its depth down the wall follows the tool's angle (``width / tan(half angle)``: a 90 degree tool cuts a
    45 degree chamfer as deep as it is wide). The tip runs ``tip_offset_mm`` (0.2) below the chamfer's bottom, so
    the cone's surface, not its point, makes the chamfer and the point never rides the edge: the tool's centre
    stands ``tip radius + tip_offset x tan(half angle)`` off the wall in air, and the cone meets the top face
    ``width_mm`` in from the edge. One pass round each edge at that height, climb, entered from the edge's height
    down a ramp along the pass no steeper than ``ramp_angle_deg`` (3), or plunged where the tool is off the stock
    (the wall must already be cut: phase 1 has no stock simulation to check it). Refused when the cone is
    too short for the chamfer, when the tip or the tool's body would touch the part anywhere but the chamfer, when
    the tool does not fit inside a pocket or hole, and when stock still stands over the part's top (face it first).
    A chamfer the part already models (inclined faces or cones rising from the top of the walls, or a hole's
    countersink) is cut as modelled: its own width and height, and the tool's angle must match its angle within
    half a degree. A selector naming such a chamfer face picks the feature it belongs to.

``engrave``
    V-carves a closed area with a ``v_bit``: a ``region`` drawn in the setup's plane (carved down from the top of
    the material) or a ``pocket`` of the part (carved down from its top, at most to its floor). The tool's centre
    runs along the area's medial axis (the line of points as far from two sides as each other) at the depth where
    its cone just touches the nearest side: ``(distance to the boundary - tip radius) / tan(half angle)``, so the
    cut meets the boundary at the top and never crosses it. The axis comes from the Voronoi diagram of points
    sampled densely round the boundary (edges wholly inside the area), with branches pruned where the cone at
    their junction already covers them to the tolerance. Where the axis would go deeper than ``max_depth_mm`` the
    bottom is flat: a flat end mill named by ``clear_tool`` (another tool of the job) first clears the area deeper
    than that, then the V-bit cuts the walls down to it (round the flat's edge at that depth). A carve that needs
    a flat bottom and has no ``clear_tool`` is refused, as is one deeper than the V-bit's cone. ``stepdown_mm``
    (none: one pass) splits the carve into levels, each following the axis no deeper than its level; it is the
    clearing's stepdown too (default half the flat's diameter). The V-bit enters the axis where it meets the
    surface (a corner), or where it has already cut at least as deep, and descends along the carve itself at the
    V's own slope (a pointed tool cutting on its centre); an axis piece with no such end is ramped into along
    itself no steeper than 3 degrees and never deeper than the carve, and a round area with no axis but its middle
    is spiralled into along its cone. Single-line text is not built.

``trace``
    Cuts along a curve ``depth_mm`` deep: a chain of the part's edges (``features``: edge selectors, followed
    end to end, open or closed, and level in the setup's plane; the depth is below the edges) or a ``region``
    (closed; the depth is below the top of the material). ``offset`` ``none`` (the default) puts the tool's centre
    on the curve; ``left`` or ``right`` (looking along the curve, the region in the order its points are listed)
    moves it the cutting radius at the top of the cut to that side. In ``stepdown_mm`` levels (half the tool's
    diameter), each entered down a ramp along the curve no steeper than ``ramp_angle_deg`` (3), or plunged where
    the tool is off the stock.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from cadgen._internal.cam.operations.base import OperationBase, ParseContext, PlanError, finding
from cadgen._internal.cam.values import choice, known, number

if TYPE_CHECKING:
    from cadgen._internal.cam.operations.context import PlanContext, Planned

__all__ = ["Chamfer2dOperation", "ChamferParams", "EngraveOperation", "EngraveParams", "TraceOperation",
           "TraceParams", "cone", "medial_axis"]

_CONES = ("chamfer_mill", "v_bit")
#: The chamfer's width across the top face when the job does not say, mm, and how far under the chamfer's bottom
#: the tip runs.
DEFAULT_CHAMFER_MM = 0.5
DEFAULT_TIP_OFFSET_MM = 0.2
#: A modelled chamfer whose angle differs from the tool's by more than this, degrees, is refused.
ANGLE_MATCH_DEG = 0.5
#: How far apart the boundary is sampled for the medial axis, mm (coarser on very long outlines).
AXIS_SPACING_MM = 0.1
_MAX_SAMPLES = 20000
#: The longest step along a carved axis, mm: depths are read at least this often.
AXIS_STEP_MM = 0.25
_KEY = 9


# --- shared -------------------------------------------------------------------------------------------------

def cone(tool, profile) -> tuple[float, float, float]:
    """``(half angle in radians, tip radius, cone height)`` of a chamfer mill or V-bit."""
    half = math.radians(float(tool.size["tip_angle_deg"]) / 2)
    return half, float(tool.size.get("tip_diameter_mm", 0.0)) / 2, float(profile.tip_length_mm)


def _mm(raw: dict, key: str, where: str, default=None, *, zero: bool = False):
    if key not in raw:
        return default
    if zero:
        return number(raw[key], where=f"{where}.{key}", minimum=0)
    return number(raw[key], where=f"{where}.{key}", positive=True)


def _region_polygon(ctx: "PlanContext"):
    from shapely.geometry import Polygon

    poly = Polygon(ctx.operation.region)
    if not poly.is_valid:
        raise ValueError(f"{ctx.where}.region: the outline crosses itself")
    return poly


def _local(ref: str, occurrence: str) -> str:
    ref = ref.strip()
    return ref if ref.startswith("#") else f"{occurrence}.{ref}"


def _ordinal(ref: str, letter: str) -> int | None:
    head, _, tail = ref.rpartition(f".{letter}")
    return int(tail) if head and tail.isdigit() else None


def _faced(ctx: "PlanContext", what: str) -> None:
    """Refuse when stock still stands over the part's top: these operations start from a cut surface."""
    if ctx.top_z > ctx.part_top_z + ctx.tol:
        raise PlanError("unreachable", f"{ctx.where}: {what} starts from the part's top at z {ctx.part_top_z:g}, and the stock still "
                         f"stands {ctx.top_z - ctx.part_top_z:g} mm over it; face it first (a facing before this "
                         "operation in the same setup)")


def _feed_findings(ctx: "PlanContext") -> tuple[dict, ...]:
    return tuple(ctx.feeds.findings)


def _widest(profile, low: float, high: float) -> float:
    """The tool's largest radius between ``low`` and ``high`` mm above its tip."""
    steps = 64
    heights = [low + (high - low) * k / steps for k in range(steps + 1)]
    heights += [s.z0 for s in profile.sections if low <= s.z0 <= high]
    return max(profile.radius_at(h) for h in heights)


def _descent(path: list, top: float, angle_deg: float) -> list:
    """Back and forth along the start of an open ``path`` from ``top`` down to its height, ending at its start, no
    steeper than ``angle_deg``: the entry of an open cut where the tool is not in air. It never goes deeper than
    the path itself at any point it passes (a V-carve's path is the deepest the cone may go there)."""
    z = path[0][2]
    drop = top - z
    if drop <= 1e-9:
        return [tuple(path[0])]
    need = drop / math.tan(math.radians(angle_deg))
    prefix, walked = [tuple(path[0])], 0.0
    for a, b in zip(path, path[1:]):
        prefix.append(tuple(b))
        walked += math.dist(a[:2], b[:2])
        if walked >= need / 2:
            break
    if walked <= 1e-9:
        raise ValueError("the curve has no length to ramp along")
    legs = max(2, math.ceil(need / walked - 1e-9))
    legs += legs % 2                       # an even number of legs ends where it started
    out, done, total = [(prefix[0][0], prefix[0][1], top)], 0.0, legs * walked
    for leg in range(legs):
        run = prefix if leg % 2 == 0 else prefix[::-1]
        for a, b in zip(run, run[1:]):
            done += math.dist(a[:2], b[:2])
            out.append((b[0], b[1], max(b[2], top - drop * min(done / total, 1.0))))
    out[-1] = tuple(path[0])
    return out


# --- chamfer_2d ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ChamferParams:
    width_mm: float | None       # None: 0.5 mm, or the width the part models
    tip_offset_mm: float
    ramp_angle_deg: float


@dataclass(frozen=True)
class _Edge:
    """One top edge to chamfer, in the setup's frame."""

    ref: str
    side: str                    # outside (a profile) | inside (a pocket or a hole)
    outline: Any                 # the wall's outline at the edge (shapely Polygon)
    top_z: float                 # where the chamfer meets the top face (the sharp edge's height)
    width_mm: float
    modelled: bool


class Chamfer2dOperation(OperationBase):
    name, phase, word = "chamfer_2d", 1, "Chamfer"
    keys = frozenset({"width_mm", "tip_offset_mm", "ramp_angle_deg"})
    tool_types = _CONES
    targets = ("features", "all")
    kinds = ("profile", "pocket", "hole")

    def parse(self, raw: dict, ctx: ParseContext) -> ChamferParams:
        from cadgen._internal.cam.operations.emit import DEFAULT_RAMP_DEG

        known(raw, self.keys, where=ctx.where, takes=f"chamfer_2d takes {', '.join(sorted(self.keys))}")
        angle = number(raw.get("ramp_angle_deg", DEFAULT_RAMP_DEG), where=f"{ctx.where}.ramp_angle_deg",
                       positive=True, below=90)
        return ChamferParams(_mm(raw, "width_mm", ctx.where),
                             _mm(raw, "tip_offset_mm", ctx.where, DEFAULT_TIP_OFFSET_MM, zero=True), angle)

    def _named(self, ctx: "PlanContext") -> list:
        """``[(selector, SetupFeature)]``: the features pointed at, a chamfer face standing for its feature."""
        if ctx.operation.all:
            named = [(f.feature.ref, f) for f in ctx.features if f.feature.kind in self.kinds]
            if not named:
                raise PlanError("empty_operation", f"{ctx.where}.all: setup {ctx.setup.setup.id} reaches no outline, pocket or hole")
            return named
        from cadgen._internal.cam.features import feature_for

        reached = [found.feature for found in ctx.features]
        out = []
        for ref in ctx.operation.features:
            local = _local(ref, ctx.part.ref)
            feature = feature_for(reached, local, self.kinds)
            hit = next((found for found in ctx.features if found.feature is feature), None) if feature else None
            ordinal = _ordinal(local, "f")
            if hit is None and ordinal is not None:
                hit = next((found for found in ctx.features if found.feature.kind in self.kinds and
                            ordinal in [o for o, _, _ in _chamfer_faces(ctx, found)]), None)
            if hit is None:
                ctx.targets(self.kinds)      # raises the sentence naming what the selector is
                raise ValueError(f"{ctx.where}.features: {ref} is not the top edge of an outline, pocket or hole")
            out.append((ref, hit))
        return out

    def plan(self, params: ChamferParams, ctx: "PlanContext") -> "Planned":
        from shapely.geometry import LineString

        from cadgen._internal.cam.operations import geometry2d as g2
        from cadgen._internal.cam.operations.context import Planned
        from cadgen._internal.cam.operations.emit import Emitter, entry_for

        half, tip, height = cone(ctx.tool, ctx.profile)
        tol = ctx.tol
        emitter = Emitter(ctx)
        stats: dict = {"edges": [], "tip_offset_mm": params.tip_offset_mm}
        centre = tip + params.tip_offset_mm * math.tan(half)        # the tool's centre off the wall
        for ref, found in self._named(ctx):
            edge = _edge_of(ctx, ref, found, params, half)
            depth = edge.width_mm / math.tan(half)
            _reach(edge.width_mm, half, height, params.tip_offset_mm, ctx.tool, ctx.where)
            if edge.top_z >= ctx.part_top_z - tol:
                _faced(ctx, "a chamfer on the part's top edge")
            tip_z = edge.top_z - depth - params.tip_offset_mm
            if edge.side == "outside":
                rings = g2.ring_paths(g2.offset(edge.outline, centre, tol), tip_z, material="inside")
            else:
                area = g2.offset(edge.outline, -centre, tol)
                if area.is_empty:
                    raise PlanError("tool_too_large", f"{ctx.where}: the {2 * centre:g} mm the chamfer tool's tip needs does not fit "
                                     f"inside {ref}")
                rings = g2.ring_paths(area, tip_z, material="outside")
            points = [p for ring in rings for p in ring]
            # Under the chamfer the cone is narrower than its distance to the wall: the tip and the cone's lower end
            # must stay off everything there, the wall and anything else standing near.
            if g2.intrusion(ctx, points, radius=centre) > tol:
                raise PlanError("unreachable", f"{ctx.where}: the tip {params.tip_offset_mm:g} mm under the chamfer on {ref} would "
                                 f"touch the part at z {tip_z:g}; lower tip_offset_mm or cut this edge another way")
            above = g2.shadow(ctx, edge.top_z)
            if not above.is_empty:
                body = _widest(ctx.profile, edge.top_z - tip_z, max(ctx.part_top_z - tip_z, edge.top_z - tip_z))
                gap = min(above.distance(LineString([p[:2] for p in ring])) for ring in rings)
                if gap < body - tol:
                    raise PlanError("tool_too_large", f"{ctx.where}: the {2 * body:g} mm tool body over the chamfer on {ref} hits the "
                                     f"part above the edge ({gap:.3g} mm away); use a smaller tool")
            for ring in rings:
                # From the edge's height the tool is clear of the part; it ramps down along the ring into the edge.
                entry, path = entry_for(ctx, ring, angle_deg=params.ramp_angle_deg)
                emitter.go(path, rapid_to=edge.top_z, entry=entry)
            stats["edges"].append({"ref": edge.ref, "side": edge.side, "width_mm": round(edge.width_mm, 6),
                                   "depth_mm": round(depth, 6), "top_z": round(edge.top_z, 6),
                                   "tip_z": round(tip_z, 6), "modelled": edge.modelled})
        return Planned(emitter.toolpath(), stats, _feed_findings(ctx))

    def human_lines(self, stats: dict) -> list[str]:
        return [f"{e['ref']} {e['side']}: {e['width_mm']:g} mm chamfer, {e['depth_mm']:g} mm down the wall"
                + (" (as modelled)" if e["modelled"] else "") for e in stats["edges"]]


def _reach(width: float, half: float, height: float, tip_offset: float, tool, where: str) -> None:
    """Refuse a chamfer the tool's cone is too short (too narrow) to cut."""
    need = width / math.tan(half) + tip_offset
    if need > height + 1e-9:
        reach = max(height - tip_offset, 0.0) * math.tan(half)
        raise PlanError("unreachable", f"{where}: a {width:g} mm chamfer with the tip {tip_offset:g} mm under it needs {need:g} mm "
                         f"of cone, and {tool.id}'s cone is {height:g} mm tall ({tool.diameter_mm:g} mm across at "
                         f"{float(tool.size['tip_angle_deg']):g} degrees): it chamfers at most {reach:.3g} mm wide; "
                         "use a wider tool or a smaller width_mm or tip_offset_mm")


def _chamfer_faces(ctx: "PlanContext", found) -> list[tuple[int, float, float]]:
    """The faces the part models as a chamfer on the feature's top edge: ``(ordinal, height, angle from the tool
    axis in radians)``: inclined planes or cones about the tool axis, facing up, rising from the walls' top."""
    from cadgen._internal.cam.part import dot, parallel

    def make():
        part, direction = ctx.part, tuple(float(c) for c in ctx.setup.direction)
        refs = found.feature.refs
        walls = [o for o in (_ordinal(r, "f") for r in refs) if o is not None and part.is_wall(o, direction)]
        if not walls:
            return []
        wall_top = max(part.extent([w], direction)[5] for w in walls)
        out, seen = [], set(walls)
        for wall in walls:
            for other in part.neighbours(wall):
                if other in seen:
                    continue
                seen.add(other)
                face = part.face(other)
                if face.surface == "cone" and not parallel(face.axis, direction) and \
                        not parallel(tuple(-c for c in face.axis), direction):
                    continue
                if face.surface not in ("plane", "cone"):
                    continue
                samples = part.samples(other)
                if not samples:
                    continue
                rise = dot(samples[0][1], direction)       # sin of the face's angle from the tool axis
                if not 0.02 < rise < 0.998:
                    continue
                low, high = part.extent([other], direction)[4:]
                if abs(low - wall_top) > 1e-3:
                    continue
                out.append((other, high - low, math.asin(rise)))
        return sorted(out)

    return ctx.cached(("chamfer_faces", found.feature.ref), make)


def _edge_of(ctx: "PlanContext", ref: str, found, params: ChamferParams, half: float) -> _Edge:
    from shapely.geometry import Point

    from cadgen._internal.cam.operations import geometry2d as g2

    feature = found.feature
    side = "outside" if feature.kind == "profile" else "inside"
    tol = ctx.tol
    modelled: tuple[float, float, float] | None = None       # (width, height, angle from the axis)
    if feature.kind == "hole" and feature.size.get("countersink"):
        sink = feature.size["countersink"]
        radius = float(feature.size["diameter_mm"]) / 2
        width = float(sink["diameter_mm"]) / 2 - radius
        angle = math.radians(float(sink["angle_deg"]) / 2)
        modelled = (width, width / math.tan(angle), angle)
        outline = Point(found.top[0], found.top[1]).buffer(radius, quad_segs=g2.quad_segs(radius, tol))
        top_z = found.top[2]
    else:
        faces = _chamfer_faces(ctx, found)
        outline = g2.feature_region(ctx, found, found.top[2])
        top_z = found.top[2]
        if faces:
            heights = [h for _, h, _ in faces]
            angles = [a for _, _, a in faces]
            if max(heights) - min(heights) > 1e-3 or max(angles) - min(angles) > math.radians(ANGLE_MATCH_DEG):
                raise PlanError("unreachable", f"{ctx.where}: the chamfer {feature.ref} models is not the same all round "
                                 f"({min(heights):.3g} to {max(heights):.3g} mm tall); chamfer it with another "
                                 "operation")
            modelled = (heights[0] * math.tan(angles[0]), heights[0], angles[0])
            top_z = found.top[2] + heights[0]
    if modelled is None:
        width = params.width_mm if params.width_mm is not None else DEFAULT_CHAMFER_MM
        return _Edge(ref, side, outline, top_z, width, False)
    width, _, angle = modelled
    if abs(math.degrees(angle - half)) > ANGLE_MATCH_DEG:
        raise PlanError("unreachable", f"{ctx.where}: {feature.ref} models a chamfer {math.degrees(angle):.3g} degrees off the "
                         f"wall, and {ctx.tool.id} cuts one {math.degrees(half):.3g} degrees off it; use a "
                         f"{2 * math.degrees(angle):.3g} degree tool")
    if params.width_mm is not None and abs(params.width_mm - width) > 1e-3:
        raise PlanError("unreachable", f"{ctx.where}.width_mm: {feature.ref} models a {width:.3g} mm chamfer and width_mm asks for "
                         f"{params.width_mm:g}; leave width_mm out to cut the modelled one")
    return _Edge(ref, side, outline, top_z, width, True)


# --- engrave ------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class EngraveParams:
    max_depth_mm: float | None
    clear_tool: str | None
    stepdown_mm: float | None


def medial_axis(poly, tol: float, spacing: float = AXIS_SPACING_MM) -> list[list[tuple[float, float]]]:
    """The medial axis of a shapely Polygon (holes allowed) as polylines of (x, y), pruned of branches whose
    every point's inscribed circle lies within ``tol`` of the circle at their junction; a single point (its pole
    of inaccessibility) when nothing is left (a circle, a regular polygon). Deterministic: sorted, rounded."""
    from shapely.geometry import MultiPoint, Point
    from shapely.ops import polylabel, voronoi_diagram

    perimeter = poly.exterior.length + sum(ring.length for ring in poly.interiors)
    spacing = max(spacing, perimeter / _MAX_SAMPLES)
    samples: dict[tuple[float, float], None] = {}
    for ring in (poly.exterior, *poly.interiors):
        coords = list(ring.coords)
        for a, b in zip(coords, coords[1:]):
            count = max(1, math.ceil(math.dist(a, b) / spacing - 1e-9))
            for k in range(count):
                samples[(round(a[0] + (b[0] - a[0]) * k / count, _KEY), round(a[1] + (b[1] - a[1]) * k / count, _KEY))] = None
    diagram = voronoi_diagram(MultiPoint(list(samples)), edges=True)
    boundary = poly.boundary

    def lines(geom):
        if geom.geom_type == "LineString":
            yield geom
        for item in getattr(geom, "geoms", ()):
            yield from lines(item)

    graph: dict[tuple, set] = {}
    for line in lines(diagram):
        if not poly.contains(line):
            continue
        coords = [(round(x, _KEY), round(y, _KEY)) for x, y in line.coords]
        for a, b in zip(coords, coords[1:]):
            if a != b:
                graph.setdefault(a, set()).add(b)
                graph.setdefault(b, set()).add(a)
    radius = {node: boundary.distance(Point(node)) for node in graph}
    chains = _chains(graph)
    # Prune leaf branches the circle at their junction already covers, until none is left to prune.
    while True:
        degree = {node: len(near) for node, near in graph.items()}
        pruned = False
        for chain in chains:
            ends = [chain[0], chain[-1]]
            if len(chains) == 1 or chain[0] == chain[-1]:
                continue
            leaf = [end for end in ends if degree[end] == 1]
            junction = [end for end in ends if degree[end] >= 3]
            if len(leaf) != 1 or len(junction) != 1:
                continue
            j = junction[0]
            if all(radius[p] + math.dist(p, j) <= radius[j] + tol for p in chain):
                for a, b in zip(chain, chain[1:]):
                    graph[a].discard(b)
                    graph[b].discard(a)
                for node in chain:
                    if node != j and not graph[node]:
                        del graph[node]
                pruned = True
                break
        if not pruned:
            break
        chains = _chains(graph)
    if len(chains) == 1 and chains[0][0] != chains[0][-1]:
        # One branch left: when the circle at its widest point covers it, that point is the whole axis.
        m = max(chains[0], key=lambda p: (radius[p], p))
        if all(radius[p] + math.dist(p, m) <= radius[m] + tol for p in chains[0]):
            chains = []
    if not chains:
        centre = polylabel(poly, tolerance=tol)
        return [[(round(centre.x, 6), round(centre.y, 6))]]
    # A branch ending near a corner of the boundary runs on into the corner.
    vertices = [tuple(c) for ring in (poly.exterior, *poly.interiors) for c in ring.coords[:-1]]
    degree = {node: len(near) for node, near in graph.items()}
    out = []
    for chain in chains:
        chain = list(chain)
        for at_end in (False, True):
            end = chain[-1] if at_end else chain[0]
            if degree.get(end) != 1 or radius[end] > 2 * spacing:
                continue
            corner = min(vertices, key=lambda v: (math.dist(v, end), v))
            if math.dist(corner, end) <= 3 * spacing:
                if at_end:
                    chain.append(corner)
                else:
                    chain.insert(0, corner)
        out.append(chain)
    return sorted(out)


def _chains(graph: dict) -> list[list[tuple]]:
    """The graph's edges as polylines between nodes that are not simple links (ends and junctions); a loop of simple
    links on its own is one closed polyline."""
    used: set[frozenset] = set()
    chains = []
    stops = sorted(node for node, near in graph.items() if len(near) != 2)
    for start in stops:
        for nxt in sorted(graph[start]):
            if frozenset((start, nxt)) in used:
                continue
            chain = [start, nxt]
            used.add(frozenset((start, nxt)))
            while len(graph[chain[-1]]) == 2:
                ahead = [n for n in sorted(graph[chain[-1]]) if frozenset((chain[-1], n)) not in used]
                if not ahead:
                    break
                used.add(frozenset((chain[-1], ahead[0])))
                chain.append(ahead[0])
            chains.append(chain)
    for start in sorted(graph):
        for nxt in sorted(graph[start]):
            if frozenset((start, nxt)) in used:
                continue
            chain = [start, nxt]
            used.add(frozenset((start, nxt)))
            while chain[-1] != start:
                ahead = [n for n in sorted(graph[chain[-1]]) if frozenset((chain[-1], n)) not in used]
                if not ahead:
                    break
                used.add(frozenset((chain[-1], ahead[0])))
                chain.append(ahead[0])
            chains.append(chain)
    return sorted(chains)


class EngraveOperation(OperationBase):
    name, phase, word = "engrave", 1, "V-carve"
    keys = frozenset({"max_depth_mm", "clear_tool", "stepdown_mm"})
    tool_types = ("v_bit",)
    targets = ("features", "region")
    kinds = ("pocket",)

    def parse(self, raw: dict, ctx: ParseContext) -> EngraveParams:
        from cadgen._internal.cam.values import json_text

        known(raw, self.keys, where=ctx.where, takes=f"engrave takes {', '.join(sorted(self.keys))}")
        clear = raw.get("clear_tool")
        if clear is not None and (not isinstance(clear, str) or not clear):
            raise ValueError(f"{ctx.where}.clear_tool: the id of a flat end mill in this job's tools, like \"T2\"; "
                             f"got {json_text(clear)}")
        if clear == ctx.tool.id:
            raise ValueError(f"{ctx.where}.clear_tool: {clear} is the V-bit itself; name a flat end mill to clear the "
                             "flat bottom with")
        return EngraveParams(_mm(raw, "max_depth_mm", ctx.where), clear, _mm(raw, "stepdown_mm", ctx.where))

    def plan(self, params: EngraveParams, ctx: "PlanContext") -> "Planned":
        from shapely.geometry import Point

        from cadgen._internal.cam.operations import geometry2d as g2
        from cadgen._internal.cam.operations.context import Planned
        from cadgen._internal.cam.operations.emit import Emitter
        from cadgen._internal.cam.toolpath import densify

        from cadgen._internal.cam.operations.emit import DEFAULT_RAMP_DEG

        half, tip, height = cone(ctx.tool, ctx.profile)
        tan = math.tan(half)
        tol, angle = ctx.tol, DEFAULT_RAMP_DEG
        areas = []                                    # (ref, polygon, surface z, deepest allowed below it)
        if ctx.operation.region is not None:
            areas.append(("region", _region_polygon(ctx), ctx.top_z, params.max_depth_mm))
        else:
            _faced(ctx, "a V-carve of a pocket's outline")
            for ref, found in ctx.targets(self.kinds):
                cap = found.depth if params.max_depth_mm is None else min(params.max_depth_mm, found.depth)
                areas.append((ref, g2.feature_region(ctx, found, found.top[2]), found.top[2], cap))
        v_ctx, clear_paths, findings = ctx, [], list(_feed_findings(ctx))
        plans, stats = [], {"areas": []}
        for ref, poly, surface, cap in areas:
            from shapely.ops import polylabel

            pole = polylabel(poly, tolerance=tol / 4)
            deepest = max(0.0, (poly.boundary.distance(Point(pole)) - tip) / tan)
            floor_depth = cap if cap is not None and deepest > cap + 1e-9 else None
            if floor_depth is None and deepest > height + 1e-9:
                raise PlanError("unreachable", f"{ctx.where}: {ref} is {2 * (deepest * tan + tip):.3g} mm across at its widest and "
                                 f"{ctx.tool.id} carves at most {ctx.tool.diameter_mm:g} mm wide ({height:.3g} mm "
                                 "deep); give max_depth_mm and a flat clear_tool for a flat bottom, or a wider V-bit")
            if floor_depth is not None and floor_depth > height + 1e-9:
                raise PlanError("too_deep", f"{ctx.where}.max_depth_mm: {floor_depth:g} mm is deeper than {ctx.tool.id}'s "
                                 f"{height:.3g} mm cone; the walls above it would be cut by the shank")
            if floor_depth is not None and params.clear_tool is None:
                raise PlanError("unreachable", f"{ctx.where}: {ref} needs a {deepest:.3g} mm deep carve, and the V-bit stops at "
                                 f"{floor_depth:g} mm; the bottom beyond it is flat: name a flat end mill to clear it "
                                 "with clear_tool")
            reach = floor_depth if floor_depth is not None else deepest
            if reach > ctx.profile.cutting_length_mm + 1e-9:
                raise PlanError("too_deep", f"{ctx.where}: {ref} is carved {reach:.3g} mm deep, past {ctx.tool.id}'s cutting "
                                 f"length of {ctx.profile.cutting_length_mm:g} mm")
            # The axis, sampled finely, each point at the depth where the cone touches the nearest side.
            paths, seeded = [], {}
            for chain in medial_axis(poly, tol):
                if len(chain) > 1:
                    chain = [p[:2] for p in densify([(x, y, 0.0) for x, y in chain], max_segment=AXIS_STEP_MM)]
                else:   # one point (a round area): a spiral in to it along the cone, no plunge
                    chain = _spiral(poly, chain[0], tip, tan, floor_depth, angle)
                paths.append([(x, y, surface - _carve(poly, x, y, tip, tan, floor_depth)) for x, y in chain])
            ring_z = None
            if floor_depth is not None:
                ring_z = surface - floor_depth
                flat = g2.offset(poly, -(tip + floor_depth * tan), tol)
                rings = [list(ring) for ring in g2.ring_paths(flat, ring_z, material="outside")]
                paths += rings
                # The clear tool takes the flat down to the floor first: the V-bit enters these rings in air.
                seeded.update({(round(p[0], 6), round(p[1], 6)): ring_z for ring in rings for p in ring})
                cleared, left = self._clear(params, ctx, ref, flat, surface, ring_z)
                clear_paths.append(cleared)
                findings += left
            plans.append((ref, poly, surface, paths, floor_depth, seeded))
            stats["areas"].append({"ref": ref, "surface_z": round(surface, 6), "deepest_mm": round(deepest, 6),
                                   "floor_z": None if ring_z is None else round(ring_z, 6)})
        before = ()
        if clear_paths:
            clear_ctx, clear_emitter = clear_paths[0]
            for _, other in clear_paths[1:]:
                for move in other.moves:
                    clear_emitter.moves.append(move)
            before = (clear_emitter.toolpath(),)
            findings += [f for f in clear_ctx.feeds.findings]
            v_ctx = replace(ctx, start=None)          # the V-bit comes in after a tool change
        emitter = Emitter(v_ctx)
        for ref, poly, surface, paths, floor_depth, seeded in plans:
            levels = [surface - (floor_depth if floor_depth is not None else max(surface - min(p[2] for path in paths
                                                                                                  for p in path), 0.0))]
            if params.stepdown_mm is not None:
                from cadgen._internal.cam.operations.geometry2d import depth_steps

                levels = depth_steps(surface, levels[0], params.stepdown_mm) or levels
            previous, cut = surface, dict(seeded)
            for level in levels:
                runs = [run for path in paths for run in _below(path, previous, level)]
                self._carve_runs(emitter, runs, surface, cut, angle, tol)
                previous = level
            stats["areas"][[a["ref"] for a in stats["areas"]].index(ref)]["levels"] = [round(z, 6) for z in levels]
        return Planned(emitter.toolpath(), stats, tuple(findings), before=before)

    @staticmethod
    def _carve_runs(emitter, runs: list, surface: float, cut: dict, angle_deg: float, tol: float) -> None:
        """Cut each run, carrying on without lifting where the next one starts where the tool is. Otherwise the next
        is the nearest run end the tool can enter without descending into material: one on the surface, or one the
        tool has already cut at least as deep (``cut``: the depth cut so far by (x, y), kept across levels), where it
        drops through the cut it made; only
        when no run has such an end is one entered down a ramp along itself (laps of a closed run, back and forth
        along an open one), never deeper than the run at any point and no steeper than ``angle_deg``."""
        from cadgen._internal.cam.operations.emit import AlongPath
        from cadgen._internal.cam.toolpath import PLUNGE

        def key(point) -> tuple:
            return (round(point[0], 6), round(point[1], 6))

        def open_at(point) -> bool:
            return point[2] >= surface - tol or point[2] >= cut.get(key(point), math.inf) - 1e-9

        left = list(runs)
        while left:
            here = emitter.here
            best = None
            for index, run in enumerate(left):
                for flip in (False, True):
                    if flip and (len(run) == 1 or run[0] == run[-1]):
                        continue
                    end = run[-1] if flip else run[0]
                    joined = here is not None and math.dist(end, here) < 1e-6
                    rank = 0 if joined else 1 if open_at(end) else 2
                    far = 0.0 if here is None else round(math.dist(end[:2], here[:2]), 9)
                    candidate = (rank, far, index, flip)
                    best = candidate if best is None or candidate < best else best
            rank, _, pick, flip = best
            run = left.pop(pick)
            run = run[::-1] if flip else run
            if rank == 0:
                emitter.cut(run[1:])
            elif rank == 1:
                emitter.go(run, rapid_to=run[0][2] if key(run[0]) in cut else surface, entry=PLUNGE)
            elif len(run) > 2 and run[0] == run[-1]:
                emitter.go(run, rapid_to=surface, entry=AlongPath(angle_deg))
            else:
                if len(run) == 1:
                    raise ValueError("a single carve point is entered by its spiral")
                emitter.go(_descent(run, surface, angle_deg) + run[1:], rapid_to=surface, entry=PLUNGE)
            for point in run:
                cut[key(point)] = min(point[2], cut.get(key(point), math.inf))

    @staticmethod
    def _clear(params: EngraveParams, ctx: "PlanContext", ref: str, flat, surface: float, floor: float):
        """The flat bottom ``flat`` cleared down to ``floor`` with the clear tool, as offset rings like a pocket:
        ``((context, emitter), findings)``."""
        from cadgen._internal.cam.feeds import feeds_for
        from cadgen._internal.cam.operations import geometry2d as g2
        from cadgen._internal.cam.operations.emit import Emitter
        from cadgen._internal.cam.operations.milling_2d import Pocket2dOperation, PocketParams
        from cadgen._internal.cam.tools import profile

        at = f"{ctx.where}.clear_tool"
        tool = next((t for t in ctx.job.tools if t.id == params.clear_tool), None)
        if tool is None:
            raise ValueError(f"{at}: {params.clear_tool} is not a tool in this job; tools: "
                             f"{', '.join(t.id for t in ctx.job.tools)}")
        if tool.type != "flat_endmill":
            raise ValueError(f"{at}: {tool.id} is a {tool.type}; a flat bottom is cleared with a flat_endmill")
        feeds = feeds_for(tool, ctx.job.material, ctx.job.machine, where=f"{ctx.label} clearing")
        clear = replace(ctx, tool=tool, profile=profile(tool), feeds=feeds, start=None)
        if surface - floor > clear.profile.cutting_length_mm + 1e-9:
            raise PlanError("too_deep", f"{at}: {tool.id}'s {clear.profile.cutting_length_mm:g} mm flutes do not reach the "
                             f"{surface - floor:g} mm deep flat bottom of {ref}")
        r = clear.radius
        centres = g2.offset(flat, -r, ctx.tol)
        if centres.is_empty:
            raise PlanError("tool_too_large", f"{at}: the {2 * r:g} mm {tool.id} does not fit the flat bottom of {ref}; use a smaller "
                             "tool or a smaller max_depth_mm")
        step = params.stepdown_mm or r
        pocket = PocketParams("offset", 0.4, step, 0.0, 0.0, 3.0, None, None)
        emitter = Emitter(clear)
        previous = surface
        for z in g2.depth_steps(surface, floor, step):
            Pocket2dOperation()._level(emitter, clear, pocket, centres, z, previous, None)
            previous = z
        left = flat.difference(g2.offset(centres, r, ctx.tol)).area
        found = []
        if left > 0.01:
            found.append(finding(
                "warning", "rest_material",
                f"{ctx.label}: {tool.id} leaves {left:.3g} mm2 of the flat bottom of {ref} where it does not fit",
                "the V-bit's pass round the flat cuts the walls, but in corners tighter than the flat tool the floor "
                "keeps a little material; a smaller clear_tool takes it", [{"text": ref, "tool": tool.id}]))
        return (clear, emitter), found

    def human_lines(self, stats: dict) -> list[str]:
        lines = []
        for area in stats["areas"]:
            text = f"{area['ref']}: V-carved {area['deepest_mm']:.3g} mm deep at its widest"
            if area["floor_z"] is not None:
                text = f"{area['ref']}: V-carved to a flat bottom at z {area['floor_z']:g}"
            lines.append(text + f" in {len(area['levels'])} level(s)")
        return lines


def _carve(poly, x: float, y: float, tip: float, tan: float, cap: float | None) -> float:
    """How deep the cone goes at (x, y) to touch the nearest side, at most ``cap``."""
    from shapely.geometry import Point

    depth = max(0.0, (poly.boundary.distance(Point(x, y)) - tip) / tan)
    return depth if cap is None else min(depth, cap)


def _spiral(poly, centre, tip: float, tan: float, cap: float | None, angle_deg: float) -> list[tuple[float, float]]:
    """A spiral in to ``centre`` (a round area's middle) from where its cone first touches the surface, close enough
    that following the cone down it slopes on average no steeper than ``angle_deg``."""
    from shapely.geometry import Point
    from shapely.ops import nearest_points

    clear = poly.boundary.distance(Point(centre))
    start = clear - tip
    if start <= 1e-6:
        return [centre]
    near = nearest_points(Point(centre), poly.boundary)[1]
    a0 = math.atan2(near.y - centre[1], near.x - centre[0])
    drop = start / tan if cap is None else min(cap, start / tan)
    turns = max(1, math.ceil(drop / (math.pi * start * math.tan(math.radians(angle_deg))) - 1e-9))
    per_turn = max(32, math.ceil(2 * math.pi * start / AXIS_STEP_MM))
    count = turns * per_turn
    out = []
    for k in range(count + 1):
        f = k / count
        rho, a = start * (1 - f), a0 + 2 * math.pi * turns * f
        out.append((round(centre[0] + rho * math.cos(a), 6), round(centre[1] + rho * math.sin(a), 6)))
    return out


def _below(path: list, previous: float, level: float) -> list[list]:
    """The runs of ``path`` still to cut between ``previous`` (the last level) and ``level``: its stretches deeper
    than ``previous``, each with the point either side (already at its own depth), held no deeper than ``level``."""
    if len(path) == 1:
        return [[(path[0][0], path[0][1], max(path[0][2], level))]] if path[0][2] < previous - 1e-9 else []
    deep = [p[2] < previous - 1e-9 for p in path]
    runs, k = [], 0
    while k < len(path):
        if not deep[k]:
            k += 1
            continue
        j = k
        while j + 1 < len(path) and deep[j + 1]:
            j += 1
        lo, hi = max(k - 1, 0), min(j + 1, len(path) - 1)
        runs.append([(p[0], p[1], max(p[2], level)) for p in path[lo:hi + 1]])
        k = j + 1
    closed = len(path) > 2 and path[0] == path[-1]
    if closed and len(runs) > 1 and deep[0] and deep[-1]:
        runs[0] = runs[-1][:-1] + runs[0]        # the run through the closing point is one run
        runs.pop()
    return runs


# --- trace --------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class TraceParams:
    depth_mm: float
    offset: str                  # none | left | right
    stepdown_mm: float
    ramp_angle_deg: float


class TraceOperation(OperationBase):
    name, phase, word = "trace", 1, "Trace"
    keys = frozenset({"depth_mm", "offset", "stepdown_mm", "ramp_angle_deg"})
    tool_types = ("flat_endmill", "ball_endmill", "bull_endmill", "v_bit", "chamfer_mill")
    targets = ("features", "region")

    def parse(self, raw: dict, ctx: ParseContext) -> TraceParams:
        from cadgen._internal.cam.operations.emit import DEFAULT_RAMP_DEG

        known(raw, self.keys, where=ctx.where, takes=f"trace takes {', '.join(sorted(self.keys))}")
        if "depth_mm" not in raw:
            raise ValueError(f"{ctx.where}.depth_mm: how deep the cut runs under the curve, like 0.5")
        depth = number(raw["depth_mm"], where=f"{ctx.where}.depth_mm", positive=True)
        side = choice(raw.get("offset", "none"), ("none", "left", "right"), where=f"{ctx.where}.offset")
        angle = number(raw.get("ramp_angle_deg", DEFAULT_RAMP_DEG), where=f"{ctx.where}.ramp_angle_deg",
                       positive=True, below=90)
        return TraceParams(depth, side, _mm(raw, "stepdown_mm", ctx.where, float(ctx.tool.diameter_mm) / 2), angle)

    def plan(self, params: TraceParams, ctx: "PlanContext") -> "Planned":
        from cadgen._internal.cam.operations import geometry2d as g2
        from cadgen._internal.cam.operations.context import Planned
        from cadgen._internal.cam.operations.emit import Emitter, entry_for, in_air
        from cadgen._internal.cam.toolpath import PLUNGE

        if params.depth_mm > ctx.profile.cutting_length_mm + 1e-9:
            raise PlanError("too_deep", f"{ctx.where}.depth_mm: {params.depth_mm:g} mm is past {ctx.tool.id}'s cutting length "
                             f"of {ctx.profile.cutting_length_mm:g} mm")
        reach = min(ctx.radius, ctx.profile.radius_at(params.depth_mm)) if params.offset != "none" else 0.0
        emitter = Emitter(ctx)
        stats: dict = {"curves": [], "offset": params.offset, "offset_mm": round(reach, 6)}
        for ref, points, closed, base in self._curves(ctx):
            xy = _offset_curve(points, closed, params.offset, reach, ctx.tol, f"{ctx.where}: {ref}")
            floor = base - params.depth_mm
            levels = g2.depth_steps(base, floor, params.stepdown_mm)
            previous = base
            for z in levels:
                path = [(x, y, z) for x, y in xy]
                if closed:
                    entry, path = entry_for(ctx, path, angle_deg=params.ramp_angle_deg)
                    emitter.go(path, rapid_to=previous, entry=entry)
                elif in_air(ctx, path[0][:2]):
                    emitter.go(path, rapid_to=previous, entry=PLUNGE)
                else:
                    top = max(min(previous, ctx.heights.retract), z)
                    descent = _descent(path, top, params.ramp_angle_deg)
                    emitter.go(descent + path[1:], rapid_to=top, entry=PLUNGE)
                previous = z
            stats["curves"].append({"ref": ref, "closed": closed, "floor_z": round(floor, 6),
                                    "levels": [round(z, 6) for z in levels], "length_mm": round(
                                        sum(math.dist(a, b) for a, b in zip(xy, xy[1:])), 6)})
        return Planned(emitter.toolpath(), stats, _feed_findings(ctx))

    @staticmethod
    def _curves(ctx: "PlanContext") -> list[tuple[str, list, bool, float]]:
        """``[(ref, [(x, y)], closed, z)]``: the region, or the selected edges chained end to end."""
        if ctx.operation.region is not None:
            points = [tuple(map(float, p)) for p in ctx.operation.region]
            if points[0] != points[-1]:
                points.append(points[0])
            return [("region", points, True, ctx.top_z)]
        pieces = []
        for index, ref in enumerate(ctx.operation.features):
            at = f"{ctx.where}.features[{index}]"
            ordinal = _ordinal(_local(ref, ctx.part.ref), "e")
            if ordinal is None:
                raise ValueError(f"{at}: trace follows edges (like \"#o1.e12\") or a region; {ref} is not an edge")
            pieces.append((ref, [ctx.to_setup(p) for p in _edge_points(ctx, ordinal)]))
        out = []
        while pieces:
            ref, chain = pieces.pop(0)
            refs = [ref]
            grown = True
            while grown and math.dist(chain[0], chain[-1]) > 1e-3:
                grown = False
                for index, (other, points) in enumerate(pieces):
                    for flip in (False, True):
                        run = points[::-1] if flip else points
                        if math.dist(chain[-1], run[0]) <= 1e-3:
                            chain = chain + run[1:]
                        elif math.dist(chain[0], run[-1]) <= 1e-3:
                            chain = run[:-1] + chain
                        else:
                            continue
                        refs.append(other)
                        pieces.pop(index)
                        grown = True
                        break
                    if grown:
                        break
            zs = [p[2] for p in chain]
            if max(zs) - min(zs) > 1e-3:
                raise PlanError("unreachable", f"{ctx.where}: the edges {', '.join(refs)} climb {max(zs) - min(zs):.3g} mm; trace "
                                 "follows a curve level in the setup's plane")
            closed = len(chain) > 2 and math.dist(chain[0], chain[-1]) <= 1e-3
            xy = [(round(p[0], 6), round(p[1], 6)) for p in chain]
            if closed:
                xy[-1] = xy[0]
            out.append(("+".join(refs), xy, closed, max(zs)))
        return out

    def human_lines(self, stats: dict) -> list[str]:
        side = "" if stats["offset"] == "none" else f", {stats['offset_mm']:g} mm to the {stats['offset']}"
        return [f"{c['ref']}: {c['length_mm']:.1f} mm {'loop' if c['closed'] else 'path'} to z {c['floor_z']:g} in "
                f"{len(c['levels'])} level(s){side}" for c in stats["curves"]]


def _edge_points(ctx: "PlanContext", ordinal: int) -> list[tuple[float, float, float]]:
    """Points along the part's edge ``#o1.eN`` (document frame), from its first parameter to its last, sampled at
    the slices' deflection."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_QuasiUniformDeflection
    from OCP.GeomAbs import GeomAbs_Line

    from cadgen._internal.cam.operations.geometry2d import deflection

    curve = BRepAdaptor_Curve(ctx.part.edge(ordinal))
    first, last = curve.FirstParameter(), curve.LastParameter()
    if curve.GetType() == GeomAbs_Line:
        values = [curve.Value(first), curve.Value(last)]
    else:
        sampler = GCPnts_QuasiUniformDeflection(curve, deflection(ctx.tol), first, last)
        if not sampler.IsDone():
            raise ValueError(f"{ctx.part.ref}.e{ordinal}: the kernel could not sample the edge")
        values = [sampler.Value(index) for index in range(1, sampler.NbPoints() + 1)]
    return [(p.X(), p.Y(), p.Z()) for p in values]


def _offset_curve(points: list, closed: bool, side: str, distance: float, tol: float, where: str) -> list:
    """The curve moved ``distance`` to its ``side`` (looking along it), round at its corners, in the same direction;
    as it is for ``none``."""
    from shapely.geometry import LineString, Polygon

    from cadgen._internal.cam.operations import geometry2d as g2

    if side == "none" or distance <= 0:
        return list(points)
    segs = g2.quad_segs(distance, tol)
    if closed:
        poly = Polygon(points)
        ccw_given = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(points, points[1:])) > 0
        inward = (side == "left") == ccw_given
        moved = poly.buffer(-distance if inward else distance, quad_segs=segs, join_style="round")
        found = g2.polygons(moved)
        if len(found) != 1:
            raise PlanError("tool_too_large", f"{where}: moved {distance:g} mm to the {side}, the loop "
                             f"{'vanishes' if not found else 'splits in pieces'}")
        ring = [tuple(c) for c in found[0].exterior.coords]
        if found[0].exterior.is_ccw != ccw_given:
            ring.reverse()
        return [(round(x, 6), round(y, 6)) for x, y in g2.start_at(ring)]
    moved = LineString(points).offset_curve(distance if side == "left" else -distance, quad_segs=segs,
                                            join_style="round")
    if moved.is_empty or moved.geom_type != "LineString":
        raise PlanError("tool_too_large", f"{where}: moved {distance:g} mm to the {side}, the path falls apart")
    return [(round(x, 6), round(y, 6)) for x, y in moved.coords]
