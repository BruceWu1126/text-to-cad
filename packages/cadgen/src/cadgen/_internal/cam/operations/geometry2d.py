"""2D geometry for the 2.5D operations: the part and the stock sliced at a height, the area a feature gives the tool,
offsets by the tool's radius, the rings a pass follows, depth steps, and how far a tool would enter the part.

Everything is in the setup's frame (mm, the tool along +Z), as shapely geometry. Slices are exact: the B-rep cut
by a plane (OCP ``BRepAlgoAPI_Section``), its edges sampled at a chord deflection of :func:`deflection` (an eighth
of the tolerance) and each face of the planar arrangement kept when its inside point is in the solid. A mesh slice
would add the mesh's own error on top, so it is not used.

- :func:`section`: the part's material at one height. A height on a horizontal face (a floor, the top) is sliced
  :data:`NUDGE_MM` above it, so a floor is never part of the material over it.
- :func:`shadow`: the part's material at that height or anywhere above it: what a straight-sided tool whose tip
  is at that height must stay out of (its side reaches up past every level above).
- :func:`stock_outline`: the stock at a height.
- :func:`feature_region`: the area a feature gives the tool at a height: a pocket's or a hole's open area (islands
  as holes), or a profile's outline (holes filled).
- :func:`offset`: a shapely buffer with round joins fine enough that its arcs stray at most :func:`deflection`.
- :func:`ring_paths`: the rings of an offset area as point lists oriented for climb (or conventional) milling,
  ready for :func:`~cadgen._internal.cam.toolpath.fit_arcs`.
- :func:`depth_steps`: the levels a pass steps down through, the last exactly the floor.
- :func:`intrusion`: how far the swept tool enters the part at points along a path (negative: its clearance).

shapely and numpy are imported inside the functions; this module is stdlib-only at import.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Sequence

if TYPE_CHECKING:
    from cadgen._internal.cam.features import SetupFeature
    from cadgen._internal.cam.operations.context import PlanContext

__all__ = ["NUDGE_MM", "deflection", "depth_steps", "feature_region", "intrusion", "levels", "offset", "polygons",
           "quad_segs", "ring_paths", "section", "shadow", "start_at", "stock_outline"]

#: How far above a horizontal face a slice is taken, mm, so the face itself is never in it.
NUDGE_MM = 1e-3
#: Coordinates of slice edges are snapped to this, mm, so edges meeting at a vertex meet exactly.
_SNAP_DIGITS = 6
#: Below this area, mm2, a piece of a slice is the kernel's noise.
_AREA_EPS = 1e-6


def deflection(tol: float) -> float:
    """The chord deflection curves are sampled at: an eighth of the tolerance, so a slice (an eighth), an offset (an
    eighth) and the arc fitting (half) together stay within ``tol``."""
    return tol / 8


def quad_segs(radius: float, tol: float) -> int:
    """Segments per quarter circle for a round join of ``radius`` whose chords stray at most :func:`deflection`."""
    radius = abs(radius)
    limit = deflection(tol)
    if radius <= limit:
        return 1
    return max(1, math.ceil(math.pi / (4 * math.acos(1 - limit / radius)) - 1e-9))


def polygons(geom) -> list:
    """The polygons of a shapely geometry (a Polygon, a MultiPolygon or a collection), largest first by area then
    by position, so the order is the same every run."""
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        found = [geom]
    else:
        found = [part for item in getattr(geom, "geoms", ()) for part in polygons(item)]
    found = [poly for poly in found if poly.area > _AREA_EPS]
    return sorted(found, key=lambda poly: (-round(poly.area, 6), round(poly.bounds[0], 6), round(poly.bounds[1], 6)))


def _multi(found):
    from shapely.geometry import MultiPolygon

    return MultiPolygon(polygons(found))


# --- slices ------------------------------------------------------------------------------------------------

def levels(ctx: "PlanContext") -> tuple[float, ...]:
    """The heights (z in the setup's frame) of the part's faces square to the tool, and its top and bottom."""
    def make():
        from cadgen._internal.cam.part import parallel

        found = {round(ctx.part_top_z, _SNAP_DIGITS), round(ctx.part_bottom_z, _SNAP_DIGITS)}
        direction = tuple(float(c) for c in ctx.setup.direction)
        for face in ctx.part.faces:
            if face.surface == "plane" and parallel(face.normal, direction):
                found.add(round(ctx.to_setup(face.center)[2], _SNAP_DIGITS))
        return tuple(sorted(found))

    return ctx.cached("levels", make)


def _on_level(ctx: "PlanContext", z: float) -> bool:
    return any(abs(z - level) < NUDGE_MM / 2 for level in levels(ctx))


def _slice(shape, z: float, tol: float, inside) -> Any:
    """The material of ``shape`` (an OCP solid in the setup's frame) in the plane at ``z``, as a MultiPolygon;
    ``inside(x, y, z)`` says whether a point is in the material."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    from OCP.GCPnts import GCPnts_QuasiUniformDeflection
    from OCP.GeomAbs import GeomAbs_Line
    from OCP.gp import gp_Dir, gp_Pln, gp_Pnt
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp, TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from shapely.geometry import LineString
    from shapely.ops import polygonize, unary_union

    cut = BRepAlgoAPI_Section(shape, gp_Pln(gp_Pnt(0, 0, z), gp_Dir(0, 0, 1)), False)
    cut.Approximation(False)
    cut.Build()
    if not cut.IsDone():
        raise RuntimeError(f"the kernel could not slice the part at z {z:g}")

    def snap(x: float, y: float) -> tuple[float, float]:
        return (round(x, _SNAP_DIGITS) + 0.0, round(y, _SNAP_DIGITS) + 0.0)

    lines = []
    explorer = TopExp_Explorer(cut.Shape(), TopAbs_EDGE)
    while explorer.More():
        edge = TopoDS.Edge_s(explorer.Current())
        explorer.Next()
        curve = BRepAdaptor_Curve(edge)
        first, last = BRep_Tool.Pnt_s(TopExp.FirstVertex_s(edge)), BRep_Tool.Pnt_s(TopExp.LastVertex_s(edge))
        points = [snap(first.X(), first.Y())]
        if curve.GetType() != GeomAbs_Line:
            sampler = GCPnts_QuasiUniformDeflection(curve, deflection(tol), curve.FirstParameter(), curve.LastParameter())
            if not sampler.IsDone():
                raise RuntimeError(f"the kernel could not sample a slice edge at z {z:g}")
            inner = [sampler.Value(index) for index in range(2, sampler.NbPoints())]
            if edge.Orientation() == 1:  # TopAbs_REVERSED: the vertices run against the curve
                inner.reverse()
            points += [snap(p.X(), p.Y()) for p in inner]
        points.append(snap(last.X(), last.Y()))
        cleaned = [p for k, p in enumerate(points) if k == 0 or p != points[k - 1]]
        if len(cleaned) >= 2:
            lines.append(LineString(cleaned))
    if not lines:
        return _multi(None)
    faces = list(polygonize(unary_union(lines)))
    kept = []
    for face in faces:
        if face.area <= _AREA_EPS:
            continue
        point = face.representative_point()
        if inside(point.x, point.y, z):
            kept.append(face)
    return _multi(unary_union(kept) if kept else None)


def section(ctx: "PlanContext", z: float):
    """The part's material at height ``z`` (setup frame), as a MultiPolygon; :data:`NUDGE_MM` above a horizontal
    face when ``z`` is on one."""
    z = round(float(z), _SNAP_DIGITS) + 0.0
    at = z + NUDGE_MM if _on_level(ctx, z) else z

    def make():
        if not ctx.part_bottom_z < at < ctx.part_top_z:
            return _multi(None)
        return _slice(ctx.part_shape, at, ctx.tol, lambda x, y, h: ctx.part.inside(ctx.to_part((x, y, h))))

    return ctx.cached(("section", at), make)


def shadow(ctx: "PlanContext", z: float):
    """The part's material at height ``z`` or above it (setup frame): what a straight-sided tool whose tip is at
    ``z`` must keep out of. Sliced at ``z`` and just inside both ends of every slab between the levels above it, so
    a straight or tapered wall is held at its widest."""
    from shapely.ops import unary_union

    z = round(float(z), _SNAP_DIGITS) + 0.0

    def make():
        above = [level for level in levels(ctx) if level > z + NUDGE_MM]
        if not above:
            return section(ctx, z)
        high = above[0]
        slab = [z, z + NUDGE_MM, high - NUDGE_MM] if high - z > 3 * NUDGE_MM else [z, (z + high) / 2]
        # The slab up to the next level, then everything from that level up (kept, so each height costs one slab).
        return _multi(unary_union([section(ctx, h) for h in slab] + [shadow(ctx, high)]))

    return ctx.cached(("shadow", z), make)


def stock_outline(ctx: "PlanContext", z: float | None = None):
    """The stock at height ``z`` (setup frame; just under its top by default), as a MultiPolygon."""
    from shapely.geometry import box

    top = ctx.setup.stock_top_z
    z = top - NUDGE_MM if z is None else min(float(z), top - NUDGE_MM)
    if ctx.stock.type in ("box", "sheet") and _square(ctx):
        (x0, y0, _), (x1, y1, _) = ctx.setup.stock_bounds
        return _multi(box(x0, y0, x1, y1))

    def inside(x, y, h):
        from OCP.BRepClass3d import BRepClass3d_SolidClassifier
        from OCP.gp import gp_Pnt
        from OCP.TopAbs import TopAbs_IN

        classifier = BRepClass3d_SolidClassifier(ctx.stock_shape)
        classifier.Perform(gp_Pnt(x, y, h), 1e-7)
        return classifier.State() == TopAbs_IN

    return ctx.cached(("stock", round(z, _SNAP_DIGITS)), lambda: _slice(ctx.stock_shape, z, ctx.tol, inside))


def _square(ctx: "PlanContext") -> bool:
    """The setup's axes are the part's, up to sign: a box stock stays a box in the setup's frame."""
    return all(sorted(abs(round(c, 9)) for c in row) == [0.0, 0.0, 1.0] for row in ctx.setup.frame.rotation)


# --- feature areas ---------------------------------------------------------------------------------------------

def feature_region(ctx: "PlanContext", found: "SetupFeature", z: float):
    """The area the feature gives the tool at height ``z``, as a Polygon (setup frame):

    - ``pocket`` or ``hole``: the open area round it, bounded by the part's :func:`shadow` (islands are its holes);
    - ``profile``: the part's outline there, holes filled.

    Above the feature's top or below its bottom, its area just inside that end: a pocket's walls and an outline go
    straight up through the stock above the part, and a through outline straight down through a breakthrough.
    """
    from shapely.geometry import Point, Polygon, box

    feature = found.feature
    top, bottom = found.top[2], found.bottom[2]
    at = min(max(z, bottom + NUDGE_MM), top - NUDGE_MM) if top - bottom > 2 * NUDGE_MM else (top + bottom) / 2
    material = shadow(ctx, at)
    probe = Point(*_probe(ctx, found)[:2])
    if feature.kind in ("pocket", "hole"):
        (x0, y0, _), (x1, y1, _) = ctx.setup.stock_bounds
        span = max(x1 - x0, y1 - y0)
        air = box(x0 - span, y0 - span, x1 + span, y1 + span).difference(material)
        for poly in polygons(air):
            if poly.contains(probe):
                outer = Polygon(poly.exterior)
                if outer.bounds[0] <= x0 - span + 1e-6:
                    raise ValueError(f"{feature.ref}: the {feature.kind} is open to the side at z {z:g}; "
                                     "it has no closed outline to cut")
                return poly
        raise ValueError(f"{feature.ref}: no open area round the {feature.kind} at z {z:g}")
    if feature.kind == "profile":
        near = [poly for poly in polygons(material) if poly.exterior.distance(probe) < 10 * NUDGE_MM]
        if not near:
            raise ValueError(f"{feature.ref}: the outline is not found at z {z:g}")
        return Polygon(near[0].exterior)
    raise ValueError(f"{feature.ref}: a {feature.kind} gives no area to cut round")


def _probe(ctx: "PlanContext", found: "SetupFeature") -> tuple[float, float, float]:
    """A point that tells the feature's area apart (setup frame): on a pocket's floor, on a hole's axis, on a
    profile's largest wall."""
    feature = found.feature
    if feature.kind == "hole":
        return found.top
    ordinal = int(feature.ref.rsplit(".f", 1)[1])
    samples = ctx.part.samples(ordinal)
    if not samples:
        raise ValueError(f"{feature.ref}: the face has no inside point to find it by")
    return ctx.to_setup(samples[0][0])


# --- offsets and rings -------------------------------------------------------------------------------------

def offset(geom, distance: float, tol: float):
    """``geom`` grown by ``distance`` (shrunk when negative), round joins at :func:`quad_segs`, as a MultiPolygon."""
    if geom is None or geom.is_empty:
        return _multi(None)
    if abs(distance) < 1e-12:
        return _multi(geom)
    grown = geom.buffer(distance, quad_segs=quad_segs(distance, tol), join_style="round")
    return _multi(grown)


def _ccw(coords) -> bool:
    area = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(coords, coords[1:]))
    return area > 0


def ring_paths(area, z: float, *, material: str, climb: bool = True) -> list[list[tuple[float, float, float]]]:
    """The rings a pass follows round ``area`` (where the tool's centre goes), each a closed point list at ``z``
    (its last point its first), oriented so the material is on the tool's right for climb milling with a spindle
    turning clockwise (M3), on its left for conventional:

    - ``material="outside"``: the tool works inside ``area`` (a pocket's walls, round its islands): outer rings
      counter-clockwise, islands' rings clockwise;
    - ``material="inside"``: the tool goes round ``area`` from outside (an outside profile): its outer rings
      clockwise.

    Each ring starts at the middle of its longest edge (:func:`start_at`), a straight run to ramp along and to lead
    on and off at.
    """
    if material not in ("outside", "inside"):
        raise ValueError(f"ring_paths: material is outside or inside, got {material!r}")
    out = []
    for poly in polygons(area):
        rings = [(poly.exterior, material == "outside")]
        if material == "outside":
            rings += [(interior, False) for interior in poly.interiors]
        for ring, counter in rings:
            coords = [(float(x), float(y)) for x, y in ring.coords]
            if _ccw(coords) != (counter if climb else not counter):
                coords.reverse()
            out.append([(x, y, float(z)) for x, y in start_at(coords)])
    return out


def start_at(coords: Sequence[Sequence[float]]) -> list[tuple[float, ...]]:
    """A closed ring (last point its first) restarted at the middle of its longest edge; of edges equally long (to a
    nanometre), the one whose middle is lowest in Y, then in X, so the same outline starts at the same place
    whichever way its points were listed."""
    pts = [tuple(p) for p in coords[:-1]] if tuple(coords[0]) == tuple(coords[-1]) else [tuple(p) for p in coords]
    if len(pts) < 2:
        return pts

    def rank(k: int):
        a, b = pts[k], pts[(k + 1) % len(pts)]
        return (round(math.dist(a[:2], b[:2]), 6), -round((a[1] + b[1]) / 2, 6), -round((a[0] + b[0]) / 2, 6))

    index = max(range(len(pts)), key=rank)
    a, b = pts[index], pts[(index + 1) % len(pts)]
    middle = tuple((a[n] + b[n]) / 2 for n in range(len(a)))
    ring = [middle] + pts[index + 1:] + pts[:index + 1] + [middle]
    return ring


def depth_steps(top: float, floor: float, stepdown: float) -> list[float]:
    """The heights a pass cuts at from ``top`` down to ``floor``: as few equal steps as keep each at most
    ``stepdown``, the last exactly ``floor``, never below it. Empty when ``floor`` is not under ``top``."""
    if not stepdown > 0:
        raise ValueError(f"depth_steps: stepdown must be above 0 mm, got {stepdown!r}")
    if floor >= top - 1e-9:
        return []
    count = max(1, math.ceil((top - floor) / stepdown - 1e-9))
    return [top - (top - floor) * k / count for k in range(1, count)] + [floor]


# --- checks ------------------------------------------------------------------------------------------------

def intrusion(ctx: "PlanContext", points: Sequence[Sequence[float]], *, radius: float | None = None,
              quantum: float = 0.05) -> float:
    """How far (mm) a straight-sided tool of ``radius`` (the cutter's, by default) with its tip at each of
    ``points`` (setup frame) reaches into the part: its largest depth into the :func:`shadow` at the tip's height.
    Negative: the closest it comes, as a clearance.

    The height is read down to a multiple of ``quantum`` (but never below the level of a horizontal face it is on
    or above), so a helix sampled finely does not slice the part at every point; reading lower only ever adds
    material, so the answer errs toward an intrusion.
    """
    from shapely.geometry import Point

    radius = ctx.radius if radius is None else radius
    worst = -math.inf
    for x, y, z in points:
        floor = max([level for level in levels(ctx) if level <= z + 1e-9], default=-math.inf)
        at = max(math.floor(z / quantum + 1e-9) * quantum, floor)
        material = shadow(ctx, at)
        if material.is_empty:
            continue
        point = Point(x, y)
        clearance = -material.boundary.distance(point) if material.contains(point) else material.distance(point)
        worst = max(worst, radius - clearance)
    return worst
