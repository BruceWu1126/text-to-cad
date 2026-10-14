"""One component's display mesh, made as the CAD Viewer made it before meshes moved into the store.

This is the tessellator cadgen 0.7.21 ran in the browser (its tessellation version 9), in Python,
function for function: the same sampling, grid, earcut (``cadgen._internal.earcut``, the copy
three.js ships), sliver flips, refinement, edge pinning and cross-face conformity, at the same
tolerances, so a part is drawn as it was drawn there. Its geometry comes from OCCT, not from a
container of single-precision copies: each analytic surface by its formula and every other surface
through OCCT's evaluation, each trim's pcurve and each edge's curve converted as the browser's were
(a 2D B-spline per pcurve; lines, circles and ellipses as they are, other edge curves as B-splines).

Where the browser's tessellator went wrong, this one does not:

- a freeform face's normals are its surface's derivatives, not central differences, which averaged
  the two sides of a closed profile's corner along its seam, and at a pole, where the derivatives
  have no cross product, the pole's own limiting normal, where the browser's was noise or nothing
  and refinement split on toward it;
- a freeform face's pole is one point, its model vertex: each row OCCT evaluates there is a hair
  off it, which left the pole a fan of slivers on no surface (``_snap_poles``);
- earcut's zero-area triangles over a straight run of trim are flipped away before what is left of
  them is dropped (``_flip_slivers``): dropped first, they left a T-junction on the run; and a
  triangle whose edge runs along a line of constant u or v past a vertex on it is split there
  (``_split_iso_junctions``): trims in double precision put their corners exactly in line;
- a cell whose triangles do not cover what the loops leave of it -- a hole crossing its side -- is
  cut again from its loops as a whole (``_cell_regions``);
- a face whose geometry raises is left out and named, and the rest of the part is meshed; a face
  narrower than the tolerance its trim is sampled to draws as nothing and is not named.

Faces triangulate in their parameters: trim loops are sampled adaptively from their pcurves, a grid
sized by the surface's curvature covers the face, cells inside it are two triangles each and cells
a trim crosses are clipped to it and earcut, then triangles refine by edge splitting wherever a
chord's midpoint, a triangle's centre or its facet strays from the true surface. Each model edge's
exact 3D curve is sampled once per component, and every adjacent face's boundary conforms to it:
boundary vertices are pinned to that shared polyline by arc fraction, their positions come from one
function, and a final pass fan-splits every boundary to the union of the fractions both sides hold,
so the component is watertight across faces. The display edges are those same polylines.

``mesh_component`` packs the result into the part's GLB body (``cadgen.store.meshes``): positions,
normals and triangles face by face in ordinal order, each model edge's polyline with its class,
and the faces that came out empty, which every reader names.
"""

from __future__ import annotations

import math
import sys
from bisect import bisect_left, bisect_right
from typing import Any

import numpy as np

from cadgen._internal.earcut import triangulate_shape

# The browser tessellator's defaults: the tolerances a caller does not vary.
_DEFAULT_CHORD = 1.5e-3
_LOOP_TOLERANCE = 5e-4
_MAX_REFINE_DEPTH = 7
_MIN_LOOP_SEGMENTS = 8
_EPSILON = sys.float_info.epsilon
# A component with no extent: the floor under its scale.
_SCALE_FLOOR = 1e-6
# The triangles a round of refinement must have to run over arrays rather than one at a time.
_ARRAY_ROUND = 64


class MeshProductionError(ValueError):
    """A component that could not be meshed."""


class _Unresolved(Exception):
    """A face narrower than the tolerance it is sampled to: its trim samples to fewer than three
    points, or to no extent in one parameter. It draws as nothing, as the tolerance allows."""


# --- Geometry ------------------------------------------------------------------------------


class _Surface:
    """A face's surface in the component's frame: ``value(u, v)`` and ``normal(u, v, uv_box)``, and
    ``values(us, vs)`` and ``normals(us, vs, uv_box)`` over arrays, to the same bits. An analytic
    surface by its formula, as the browser evaluated it, its normal analytic so a pole or an apex
    keeps its limiting direction; any other through OCCT, its normal the cross product of its
    derivatives."""

    __slots__ = ("kind", "value", "normal", "values", "normals", "origin", "xdir", "ydir", "radius", "semi_angle")

    def __init__(self, face) -> None:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_SurfaceType as T
        from OCP.gp import gp_Pnt, gp_Vec

        adaptor = BRepAdaptor_Surface(face, False)
        kind = adaptor.GetType()
        self.radius = 0.0
        self.semi_angle = 0.0
        self.origin = self.xdir = self.ydir = None
        analytic = {T.GeomAbs_Plane: ("plane", adaptor.Plane), T.GeomAbs_Cylinder: ("cylinder", adaptor.Cylinder),
                    T.GeomAbs_Cone: ("cone", adaptor.Cone), T.GeomAbs_Sphere: ("sphere", adaptor.Sphere),
                    T.GeomAbs_Torus: ("torus", adaptor.Torus)}.get(kind)
        if analytic is None:
            self.kind = "other"
            point, d_u, d_v = gp_Pnt(), gp_Vec(), gp_Vec()
            d0, d1 = adaptor.D0, adaptor.D1

            def value(u: float, v: float) -> list[float]:
                d0(u, v, point)
                return [point.X(), point.Y(), point.Z()]

            def normal(u: float, v: float, uv_box) -> list[float]:
                # Where one derivative vanishes -- a pole, a collapsed edge -- the cross product
                # says nothing: the normal is the surface's just inside the face, toward its
                # middle, which is the pole's own limiting direction.
                for nudge in (0.0, 1e-7, 1e-5, 1e-3):
                    if nudge:
                        u0, u1, v0, v1 = uv_box
                        d1(u + ((u0 + u1) / 2 - u) * nudge, v + ((v0 + v1) / 2 - v) * nudge, point, d_u, d_v)
                    else:
                        d1(u, v, point, d_u, d_v)
                    ux, uy, uz = d_u.X(), d_u.Y(), d_u.Z()
                    vx, vy, vz = d_v.X(), d_v.Y(), d_v.Z()
                    nx = uy * vz - uz * vy
                    ny = uz * vx - ux * vz
                    nz = ux * vy - uy * vx
                    length = math.sqrt(nx * nx + ny * ny + nz * nz)
                    if length > max(ux * ux + uy * uy + uz * uz, vx * vx + vy * vy + vz * vz) * 1e-12:
                        return [nx / length, ny / length, nz / length]
                return [0.0, 0.0, 0.0]

            def values(us: np.ndarray, vs: np.ndarray) -> np.ndarray:
                return np.array([value(u, v) for u, v in zip(us.tolist(), vs.tolist())], np.float64).reshape(-1, 3)

            def normals(us: np.ndarray, vs: np.ndarray, uv_box) -> np.ndarray:
                return np.array([normal(u, v, uv_box) for u, v in zip(us.tolist(), vs.tolist())],
                                np.float64).reshape(-1, 3)

            self.value, self.normal, self.values, self.normals = value, normal, values, normals
            return
        self.kind, accessor = analytic
        geometry = accessor()
        frame = geometry.Position()
        o, x, y, z = (frame.Location(), frame.XDirection(), frame.YDirection(), frame.Direction())
        ox, oy, oz = o.X(), o.Y(), o.Z()
        xx, xy, xz = x.X(), x.Y(), x.Z()
        yx, yy, yz = y.X(), y.Y(), y.Z()
        zx, zy, zz = z.X(), z.Y(), z.Z()
        self.origin, self.xdir, self.ydir = [ox, oy, oz], [xx, xy, xz], [yx, yy, yz]
        cos, sin = math.cos, math.sin

        def mix(a: float, b: float, c: float) -> list[float]:
            return [ox + a * xx + b * yx + c * zx, oy + a * xy + b * yy + c * zy, oz + a * xz + b * yz + c * zz]

        def mixes(a, b, c) -> np.ndarray:
            return np.stack(np.broadcast_arrays(ox + a * xx + b * yx + c * zx, oy + a * xy + b * yy + c * zy,
                                                oz + a * xz + b * yz + c * zz), axis=1)

        np_cos, np_sin = np.cos, np.sin

        if self.kind == "plane":
            def value(u: float, v: float) -> list[float]:
                return mix(u, v, 0.0)

            def values(us: np.ndarray, vs: np.ndarray) -> np.ndarray:
                return mixes(us, vs, 0.0)
        elif self.kind == "cylinder":
            r = self.radius = geometry.Radius()

            def value(u: float, v: float) -> list[float]:
                return mix(r * cos(u), r * sin(u), v)

            def values(us: np.ndarray, vs: np.ndarray) -> np.ndarray:
                return mixes(r * np_cos(us), r * np_sin(us), vs)
        elif self.kind == "cone":
            r0 = self.radius = geometry.RefRadius()
            angle = self.semi_angle = geometry.SemiAngle()
            sin_a, cos_a = sin(angle), cos(angle)

            def value(u: float, v: float) -> list[float]:
                r = r0 + v * sin_a
                if abs(r) < (abs(r0) + abs(v)) * _EPSILON * 4:
                    r = 0.0
                return mix(r * cos(u), r * sin(u), v * cos_a)

            def values(us: np.ndarray, vs: np.ndarray) -> np.ndarray:
                r = r0 + vs * sin_a
                r = np.where(np.abs(r) < (abs(r0) + np.abs(vs)) * _EPSILON * 4, 0.0, r)
                return mixes(r * np_cos(us), r * np_sin(us), vs * cos_a)
        elif self.kind == "sphere":
            r = self.radius = geometry.Radius()

            def value(u: float, v: float) -> list[float]:
                cv = cos(v)
                if abs(cv) < _EPSILON * 4:
                    cv = 0.0
                return mix(r * cv * cos(u), r * cv * sin(u), r * sin(v))

            def values(us: np.ndarray, vs: np.ndarray) -> np.ndarray:
                cv = np_cos(vs)
                cv = np.where(np.abs(cv) < _EPSILON * 4, 0.0, cv)
                return mixes(r * cv * np_cos(us), r * cv * np_sin(us), r * np_sin(vs))
        else:
            major, minor = geometry.MajorRadius(), geometry.MinorRadius()

            def value(u: float, v: float) -> list[float]:
                ring = major + minor * cos(v)
                return mix(ring * cos(u), ring * sin(u), minor * sin(v))

            def values(us: np.ndarray, vs: np.ndarray) -> np.ndarray:
                ring = major + minor * np_cos(vs)
                return mixes(ring * np_cos(us), ring * np_sin(us), minor * np_sin(vs))

        # Analytic normals keep the limiting direction at a sphere's pole and a cone's apex.
        cross = (xy * yz - xz * yy, xz * yx - xx * yz, xx * yy - xy * yx)
        handedness = -1.0 if cross[0] * zx + cross[1] * zy + cross[2] * zz < 0 else 1.0
        kind = self.kind
        latitude_of = (lambda _v: -self.semi_angle) if kind == "cone" else (lambda _v: 0.0) if kind == "cylinder" \
            else (lambda v: v)

        def normal(u: float, v: float, _uv_box) -> list[float]:
            if kind == "plane":
                nx, ny, nz = 0.0, 0.0, 1.0
            else:
                latitude = latitude_of(v)
                nx = cos(u) * cos(latitude)
                ny = sin(u) * cos(latitude)
                nz = sin(latitude)
            s = handedness
            return [s * (nx * xx + ny * yx + nz * zx), s * (nx * xy + ny * yy + nz * zy), s * (nx * xz + ny * yz + nz * zz)]

        def normals(us: np.ndarray, vs: np.ndarray, _uv_box) -> np.ndarray:
            if kind == "plane":
                nx, ny, nz = np.zeros(len(us)), np.zeros(len(us)), np.ones(len(us))
            else:
                latitude = vs if kind in ("sphere", "torus") else np.full(len(us), latitude_of(0.0))
                nx = np_cos(us) * np_cos(latitude)
                ny = np_sin(us) * np_cos(latitude)
                nz = np_sin(latitude)
            s = handedness
            return np.stack((s * (nx * xx + ny * yx + nz * zx), s * (nx * xy + ny * yy + nz * zy),
                             s * (nx * xz + ny * yz + nz * zz)), axis=1)

        self.value, self.normal, self.values, self.normals = value, normal, values, normals


class _PCurve:
    """A trim's curve on its face, as the browser's were converted: a 2D B-spline, its range its own."""

    __slots__ = ("edge_ord", "reversed", "range", "n", "value")

    def __init__(self, curve, edge_ord: int, reversed_: bool) -> None:
        from OCP.gp import gp_Pnt2d

        point = gp_Pnt2d()
        d0 = curve.D0
        self.edge_ord = edge_ord
        self.reversed = reversed_
        self.range = (curve.FirstParameter(), curve.LastParameter())
        self.n = curve.NbPoles()

        def value(t: float) -> list[float]:
            d0(t, point)
            return [point.X(), point.Y()]

        self.value = value


class _Curve:
    """A model edge's exact 3D curve: a line, circle or ellipse as it is, else its B-spline."""

    __slots__ = ("kind", "range", "n", "value")


class _Face:
    __slots__ = ("ord", "reversed", "uv", "surface", "loops", "poles")


class _Meta:
    """A loop segment that runs along a shared edge polyline: its edge and its ends' fractions."""

    __slots__ = ("ord", "f0", "f1")

    def __init__(self, ord_: int, f0: float, f1: float) -> None:
        self.ord, self.f0, self.f1 = ord_, f0, f1


class _Ring(list):
    """A sampled loop: ``[u, v]`` points, with each segment's edge ordinal and shared-edge meta."""

    __slots__ = ("ords", "metas")


class _Shared:
    __slots__ = ("curve", "params", "points", "fractions", "closed", "length", "boundary_subset")


def _faces_and_edges(topods, face_map, edge_map):
    """The component's faces (surface, uv box, loops of pcurves) and edges (exact curves) in
    ordinal order. A face whose surface or trim cannot be read has no loops, so it comes out
    empty, and is named."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
    from OCP.Geom2d import Geom2d_TrimmedCurve
    from OCP.Geom2dConvert import Geom2dConvert
    from OCP.TopAbs import TopAbs_REVERSED, TopAbs_WIRE
    from OCP.TopExp import TopExp, TopExp_Explorer
    from OCP.TopoDS import TopoDS

    faces = []
    for ordinal in range(1, face_map.Extent() + 1):
        face = TopoDS.Face_s(face_map.FindKey(ordinal))
        entry = _Face()
        entry.ord = ordinal
        entry.reversed = face.Orientation() == TopAbs_REVERSED
        entry.surface, entry.uv, entry.loops, entry.poles = None, [0.0, 0.0, 0.0, 0.0], [], []
        faces.append(entry)
        try:
            entry.surface = _Surface(face)
            u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
            surface = BRep_Tool.Surface_s(face)
            if surface is not None:
                # A bound a hair past a non-periodic domain's edge is that edge.
                su0, su1, sv0, sv1 = surface.Bounds()
                if not surface.IsUPeriodic():
                    u0, u1 = min(max(u0, su0), su1), min(max(u1, su0), su1)
                if not surface.IsVPeriodic():
                    v0, v1 = min(max(v0, sv0), sv1), min(max(v1, sv0), sv1)
            entry.uv = [u0, u1, v0, v1]
            wires = TopExp_Explorer(face, TopAbs_WIRE)
            while wires.More():
                loop = []
                walker = BRepTools_WireExplorer(TopoDS.Wire_s(wires.Current()), face)
                while walker.More():
                    edge = walker.Current()
                    curve = BRep_Tool.CurveOnSurface_s(edge, face, 0.0, 0.0)
                    if curve is None:
                        raise ValueError("an edge with no pcurve on its face")
                    first, last = BRep_Tool.Range_s(edge, face)
                    bspline = Geom2dConvert.CurveToBSplineCurve_s(Geom2d_TrimmedCurve(curve, first, last))
                    if bspline.IsPeriodic():
                        bspline.SetNotPeriodic()
                    pcurve = _PCurve(bspline, edge_map.FindIndex(edge), edge.Orientation() == TopAbs_REVERSED)
                    loop.append(pcurve)
                    if BRep_Tool.Degenerated_s(edge):
                        # A pole: the edge's one point, along the stretch of parameters that is it
                        # (none where the edge is a point in this face's parameters too).
                        start, end = pcurve.value(pcurve.range[0]), pcurve.value(pcurve.range[1])
                        if start != end:
                            axis = 1 if abs(start[1] - end[1]) <= abs(start[0] - end[0]) else 0
                            vertex = BRep_Tool.Pnt_s(TopExp.FirstVertex_s(edge))
                            entry.poles.append((axis, start[axis], min(start[1 - axis], end[1 - axis]),
                                                max(start[1 - axis], end[1 - axis]),
                                                [vertex.X(), vertex.Y(), vertex.Z()]))
                    walker.Next()
                if loop:
                    entry.loops.append(loop)
                wires.Next()
        except Exception:  # noqa: BLE001 - a face OCCT cannot read is left empty
            entry.loops = []
    edges = []
    for ordinal in range(1, edge_map.Extent() + 1):
        try:
            edges.append(_edge_curve(TopoDS.Edge_s(edge_map.FindKey(ordinal))))
        except Exception:  # noqa: BLE001 - an edge OCCT cannot read has no polyline
            edges.append(None)
    return faces, edges


def _edge_curve(edge) -> _Curve | None:
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.Geom import Geom_TrimmedCurve
    from OCP.GeomAbs import GeomAbs_CurveType as T
    from OCP.GeomConvert import GeomConvert
    from OCP.gp import gp_Pnt

    if BRep_Tool.Degenerated_s(edge):
        return None
    adaptor = BRepAdaptor_Curve(edge)
    first, last = adaptor.FirstParameter(), adaptor.LastParameter()
    kind = {T.GeomAbs_Line: "line", T.GeomAbs_Circle: "circle", T.GeomAbs_Ellipse: "ellipse"}.get(adaptor.GetType())
    curve = _Curve()
    point = gp_Pnt()
    if kind is not None:
        d0 = adaptor.D0
        curve.kind, curve.range, curve.n = kind, (first, last), None
    else:
        located = BRep_Tool.Curve_s(edge, 0.0, 0.0)
        if located is None:
            return None
        bspline = GeomConvert.CurveToBSplineCurve_s(Geom_TrimmedCurve(located, first, last))
        if bspline.IsPeriodic():
            bspline.SetNotPeriodic()
        d0 = bspline.D0
        curve.kind, curve.range, curve.n = "bspline", (bspline.FirstParameter(), bspline.LastParameter()), \
            bspline.NbPoles()

    def value(t: float) -> list[float]:
        d0(t, point)
        return [point.X(), point.Y(), point.Z()]

    curve.value = value
    return curve


def _distance(a, b) -> float:
    dx, dy, dz = a[0] - b[0], a[1] - b[1], a[2] - b[2]
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _singular_u(surface: _Surface, v: float) -> bool:
    """Whether the surface's u-line at ``v`` is one point: a sphere's pole, a cone's apex."""
    if surface.kind == "sphere":
        return abs(math.cos(v)) < 1e-12
    if surface.kind == "cone":
        return abs(surface.radius + v * math.sin(surface.semi_angle)) < (abs(surface.radius) + abs(v)) * 1e-12
    return False


# --- Loop sampling -------------------------------------------------------------------------


def _sample_loop_polygon(face: _Face, loop: list[_PCurve], tolerance: float, shared_edges: dict | None) -> _Ring:
    """The loop's points; segment i (point i to i + 1) lies on model edge ``ords[i]``, and
    ``metas[i]`` is its shared-edge meta where it runs along a shared polyline (fractions in that
    polyline's own direction, whichever way the loop runs)."""
    points = _Ring()
    ords: list[int] = []
    metas: list[_Meta | None] = []
    bound_snaps = []
    if face.surface.kind != "plane":
        for d in range(2):
            lo, hi = face.uv[d * 2], face.uv[d * 2 + 1]
            bound_snaps.append((d, lo, hi, max(abs(lo), abs(hi), hi - lo, 1e-12) * 2 ** -23))
    for pcurve in loop:
        forward = not pcurve.reversed
        shared = shared_edges.get(pcurve.edge_ord) if (pcurve.edge_ord and shared_edges is not None) else None
        segment = None
        fractions = None
        if shared is not None and len(shared.points) >= 2:
            mapped = _map_shared_edge_to_pcurve(face, pcurve, tolerance, shared)
            if mapped is not None:
                segment, fractions = mapped
        if segment is None:
            segment = _sample_pcurve_params(face, pcurve, tolerance)[1]
        # A bound's representation error snapped: past a sphere's pole or a cone's apex by one ULP
        # is across the singularity.
        for uv in segment:
            for d, lo, hi, epsilon in bound_snaps:
                if abs(uv[d] - lo) <= epsilon:
                    uv[d] = lo
                elif abs(uv[d] - hi) <= epsilon:
                    uv[d] = hi
        # An edge that is one point in this face's parameters adds no segment.
        first = segment[0]
        if all(uv[0] == first[0] and uv[1] == first[1] for uv in segment):
            continue
        if not forward:
            segment.reverse()
            if fractions is not None:
                fractions.reverse()
        # Each segment's last point is the next pcurve's first.
        ord_ = pcurve.edge_ord or 0
        for i in range(len(segment) - 1):
            points.append(segment[i])
            ords.append(ord_)
            metas.append(_Meta(pcurve.edge_ord, fractions[i], fractions[i + 1]) if fractions is not None else None)
    points.ords = ords
    points.metas = metas
    return points


def _sample_pcurve_params(face: _Face, pcurve: _PCurve, tolerance: float) -> tuple[list[float], list[list[float]]]:
    t0, t1 = pcurve.range
    uv_of = pcurve.value
    value = face.surface.value
    cache: dict[float, list[float]] = {}

    def xyz_at(t: float) -> list[float]:
        point = cache.get(t)
        if point is None:
            uv = uv_of(t)
            point = cache[t] = value(uv[0], uv[1])
        return point

    start3 = xyz_at(t0)
    end3 = xyz_at(t1)
    # A closed pcurve (a full turn) starts subdivided, or the chord test degenerates.
    closed = _distance(start3, end3) <= tolerance
    initial = max(_MIN_LOOP_SEGMENTS if closed else 2, pcurve.n if pcurve.n is not None else 2)
    params = [t0 + ((t1 - t0) * i) / initial for i in range(initial + 1)]
    # Refine parameter intervals until the 3D midpoint deviation is small.
    depth = 0
    while depth < _MAX_REFINE_DEPTH:
        split = False
        next_params = [params[0]]
        for i in range(len(params) - 1):
            a = params[i]
            b = params[i + 1]
            mid = (a + b) / 2
            pa = xyz_at(a)
            pb = xyz_at(b)
            pm = xyz_at(mid)
            dx = pm[0] - (pa[0] + pb[0]) / 2
            dy = pm[1] - (pa[1] + pb[1]) / 2
            dz = pm[2] - (pa[2] + pb[2]) / 2
            if math.sqrt(dx * dx + dy * dy + dz * dz) > tolerance:
                next_params.append(mid)
                split = True
            next_params.append(b)
        params = next_params
        if not split:
            break
        depth += 1
    return params, [uv_of(t) for t in params]


def _map_shared_edge_to_pcurve(face: _Face, pcurve: _PCurve, tolerance: float, shared: _Shared):
    """The shared polyline in this face's parameters, by arc fraction: ``(uvs, fractions)`` in
    pcurve-forward order with each point's fraction in the polyline's own convention, or None where
    the mapping cannot be trusted."""
    own_params, own_uvs = _sample_pcurve_params(face, pcurve, tolerance)
    if len(own_uvs) < 2:
        return None
    value = face.surface.value
    own3 = [value(uv[0], uv[1]) for uv in own_uvs]
    cumulative = [0.0]
    for i in range(1, len(own3)):
        cumulative.append(cumulative[i - 1] + _distance(own3[i], own3[i - 1]))
    total = cumulative[-1]
    if not total > 0:
        return None
    cumulative = [c / total for c in cumulative]
    # Does the shared polyline run with or against the pcurve?
    shared_start = shared.points[0]
    shared_end = shared.points[-1]
    d_same = _distance(shared_start, own3[0]) + _distance(shared_end, own3[-1])
    d_flip = _distance(shared_start, own3[-1]) + _distance(shared_end, own3[0])
    flip = d_flip < d_same

    uvs: list[list[float]] = []
    fractions: list[float] = []
    params: list[float] = []
    subset = shared.boundary_subset
    count = len(subset)
    cursor = 0
    for step in range(count):
        shared_index = subset[count - 1 - step if flip else step]
        f = shared.fractions[shared_index]
        g = 1 - f if flip else f  # arc position in the pcurve's direction
        while cursor + 1 < len(cumulative) - 1 and cumulative[cursor + 1] < g:
            cursor += 1
        # The ends map to the pcurve's own range ends: no drift at a corner.
        if step == 0:
            t = own_params[0]
        elif step == count - 1:
            t = own_params[-1]
        else:
            c0 = cumulative[cursor]
            c1 = cumulative[cursor + 1]
            w = (g - c0) / (c1 - c0) if c1 > c0 else 0
            t = own_params[cursor] + w * (own_params[cursor + 1] - own_params[cursor])
        uv = pcurve.value(t)
        # The mapped point must land near the shared point it claims to be.
        if _distance(value(uv[0], uv[1]), shared.points[shared_index]) > tolerance * 2:
            return None
        uvs.append(uv)
        fractions.append(f)
        params.append(t)

    # The polyline is sampled to the curve's chord error, but the surface can bulge between its
    # points: densify by the surface's chord error so the first row of triangles keeps the face's
    # own tolerance. Planes cannot bulge.
    if face.surface.kind != "plane":
        points3 = [value(uv[0], uv[1]) for uv in uvs]
        for _depth in range(4):
            split = False
            next_uvs = [uvs[0]]
            next_fractions = [fractions[0]]
            next_params = [params[0]]
            next_points3 = [points3[0]]
            for i in range(len(uvs) - 1):
                pa = points3[i]
                pb = points3[i + 1]
                t_mid = (params[i] + params[i + 1]) / 2
                uv_mid = pcurve.value(t_mid)
                pm = value(uv_mid[0], uv_mid[1])
                dx = pm[0] - (pa[0] + pb[0]) / 2
                dy = pm[1] - (pa[1] + pb[1]) / 2
                dz = pm[2] - (pa[2] + pb[2]) / 2
                if math.sqrt(dx * dx + dy * dy + dz * dz) > tolerance:
                    next_uvs.append(uv_mid)
                    next_fractions.append((fractions[i] + fractions[i + 1]) / 2)
                    next_params.append(t_mid)
                    next_points3.append(pm)
                    split = True
                next_uvs.append(uvs[i + 1])
                next_fractions.append(fractions[i + 1])
                next_params.append(params[i + 1])
                next_points3.append(points3[i + 1])
            uvs, fractions, params, points3 = next_uvs, next_fractions, next_params, next_points3
            if not split:
                break
    return uvs, fractions


def _edge_point_at(shared: _Shared, fraction: float) -> list[float]:
    """THE position of arc fraction ``fraction`` on a shared edge: every face computes boundary
    positions through this one function, so a point is the same floats on both sides of its edge.
    Between polyline knots it evaluates the exact curve at an interpolated parameter, so a minted
    point lies on the model edge."""
    fractions = shared.fractions
    last = len(fractions) - 1
    if fraction <= fractions[0]:
        return shared.points[0]
    if fraction >= fractions[last]:
        return shared.points[last]
    lo = 0
    hi = last
    while lo + 1 < hi:
        mid = (lo + hi) >> 1
        if fractions[mid] <= fraction:
            lo = mid
        else:
            hi = mid
    f0 = fractions[lo]
    f1 = fractions[lo + 1]
    if fraction == f0:
        return shared.points[lo]
    if fraction == f1:
        return shared.points[lo + 1]
    w = (fraction - f0) / (f1 - f0) if f1 > f0 else 0
    t = shared.params[lo] + w * (shared.params[lo + 1] - shared.params[lo])
    return shared.curve.value(t)


def _sample_shared_edge(curve: _Curve, tolerance: float) -> _Shared:
    """One model edge's exact curve sampled: the polyline every adjacent face conforms to, and the
    display edge."""
    t0, t1 = curve.range
    value = curve.value
    start = value(t0)
    end = value(t1)
    closed = False
    if curve.kind != "line":
        extent = _distance(start, end)
        for fraction in (0.25, 0.5, 0.75):
            extent = max(extent, _distance(start, value(t0 + fraction * (t1 - t0))))
        # Closure is a geometric fact, not a matter of the display tolerance.
        closed = _distance(start, end) <= extent * 2 ** -21
    initial = 1 if curve.kind == "line" else max(8 if closed else 4, curve.n if curve.n is not None else 2)
    params = [t0 + ((t1 - t0) * i) / initial for i in range(initial + 1)]
    cache: dict[float, list[float]] = {}

    def at(t: float) -> list[float]:
        point = cache.get(t)
        if point is None:
            point = cache[t] = value(t)
        return point

    depth = 0
    while depth < _MAX_REFINE_DEPTH:
        split = False
        next_params = [params[0]]
        for i in range(len(params) - 1):
            a = params[i]
            b = params[i + 1]
            mid = (a + b) / 2
            pa = at(a)
            pb = at(b)
            pm = at(mid)
            dx = pm[0] - (pa[0] + pb[0]) / 2
            dy = pm[1] - (pa[1] + pb[1]) / 2
            dz = pm[2] - (pa[2] + pb[2]) / 2
            if math.sqrt(dx * dx + dy * dy + dz * dz) > tolerance:
                next_params.append(mid)
                split = True
            next_params.append(b)
        params = next_params
        if not split:
            break
        depth += 1
    points = [value(t) for t in params]
    if closed and len(points) > 1:
        # The seam is one point: the same object at both ends.
        points[-1] = points[0]
    fractions = [0.0]
    for i in range(1, len(points)):
        fractions.append(fractions[i - 1] + _distance(points[i], points[i - 1]))
    total = fractions[-1]
    if total > 0:
        fractions = [f / total for f in fractions]
        fractions[-1] = 1
    # Face boundaries need only the loop tolerance, not the display edge's chord tolerance: the
    # subset keeps the points dropping which would stray past it (ends always kept).
    boundary_subset = [0]
    boundary_tolerance = tolerance * (_LOOP_TOLERANCE / _DEFAULT_CHORD)
    anchor = 0
    for i in range(1, len(points)):
        if i == len(points) - 1:
            boundary_subset.append(i)
            break
        a = points[anchor]
        b = points[i + 1]
        abx, aby, abz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        ab_len2 = abx * abx + aby * aby + abz * abz
        worst = 0.0
        for k in range(anchor + 1, i + 1):
            p = points[k]
            apx, apy, apz = p[0] - a[0], p[1] - a[1], p[2] - a[2]
            t = (apx * abx + apy * aby + apz * abz) / ab_len2 if ab_len2 > 0 else 0
            clamped = max(0, min(1, t))
            qx, qy, qz = a[0] + clamped * abx, a[1] + clamped * aby, a[2] + clamped * abz
            dx, dy, dz = p[0] - qx, p[1] - qy, p[2] - qz
            worst = max(worst, math.sqrt(dx * dx + dy * dy + dz * dz))
            if worst > boundary_tolerance:
                break
        if worst > boundary_tolerance:
            boundary_subset.append(i)
            anchor = i
    shared = _Shared()
    shared.curve, shared.params, shared.points, shared.fractions = curve, params, points, fractions
    shared.closed, shared.length, shared.boundary_subset = closed, total, boundary_subset
    return shared


# --- Face triangulation --------------------------------------------------------------------


def _polygon_area(points) -> float:
    area = 0.0
    count = len(points)
    for i in range(count):
        x0, y0 = points[i][0], points[i][1]
        x1, y1 = points[(i + 1) % count][0], points[(i + 1) % count][1]
        area += x0 * y1 - x1 * y0
    return area / 2


def _crossing_segments(loops: list[_Ring]) -> list[_Meta]:
    """The shared-edge segments (their metas) of closed loops that properly cross another of the
    loops' segments, in the order they are found. Segments meeting at an end do not count."""
    segments = []
    for points in loops:
        count = len(points)
        metas = getattr(points, "metas", None)
        for i in range(count):
            p = points[i]
            q = points[(i + 1) % count]
            segments.append((min(p[0], q[0]), max(p[0], q[0]), p, q, metas[i] if metas is not None else None))
    segments.sort(key=lambda segment: segment[0])

    def side(a, b, c) -> float:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    crossing: list[_Meta] = []
    seen: set[int] = set()
    for i in range(len(segments)):
        s_lo, s_hi, sp, sq, s_meta = segments[i]
        j = i + 1
        while j < len(segments) and segments[j][0] <= s_hi:
            _t_lo, _t_hi, tp, tq, t_meta = segments[j]
            if side(sp, sq, tp) * side(sp, sq, tq) < 0 and side(tp, tq, sp) * side(tp, tq, sq) < 0:
                for meta in (s_meta, t_meta):
                    if meta is not None and id(meta) not in seen:
                        seen.add(id(meta))
                        crossing.append(meta)
            j += 1
    return crossing


def _grid_steps_for_direction(face: _Face, uv_box, chord_limit: float, direction: int) -> int:
    u0, u1, v0, v1 = uv_box
    span = u1 - u0 if direction == 0 else v1 - v0
    if span <= 0:
        return 1
    probes = 4
    value = face.surface.value
    worst = 0.0
    for line in range(probes + 1):
        across = v0 + ((v1 - v0) * line) / probes if direction == 0 else u0 + ((u1 - u0) * line) / probes
        for i in range(probes):
            t0 = (u0 if direction == 0 else v0) + (span * i) / probes
            t1 = t0 + span / probes
            tm = (t0 + t1) / 2
            if direction == 0:
                pa, pb, pm = value(t0, across), value(t1, across), value(tm, across)
            else:
                pa, pb, pm = value(across, t0), value(across, t1), value(across, tm)
            dx = pm[0] - (pa[0] + pb[0]) / 2
            dy = pm[1] - (pa[1] + pb[1]) / 2
            dz = pm[2] - (pa[2] + pb[2]) / 2
            worst = max(worst, math.sqrt(dx * dx + dy * dy + dz * dz))
    if worst <= chord_limit:
        return 1
    # Chord error grows as the square of a step: the steps each probe interval needs.
    per_interval = math.sqrt(worst / chord_limit)
    return min(256, max(1, math.ceil(probes * per_interval)))


def _inside_centres(loops: list[_Ring], grid_u: list[float], grid_v: list[float]) -> np.ndarray:
    """``_point_in_loops`` at every cell's centre at once, (steps_u, steps_v): a crossing
    counts where the centre lies strictly left of it, as there."""
    xs0, ys0, xs1, ys1 = [], [], [], []
    for points in loops:
        count = len(points)
        for i in range(count):
            xs0.append(points[i][0])
            ys0.append(points[i][1])
            xs1.append(points[(i + 1) % count][0])
            ys1.append(points[(i + 1) % count][1])
    x0, y0, x1, y1 = (np.asarray(values, float) for values in (xs0, ys0, xs1, ys1))
    centres_u = np.array([(grid_u[i] + grid_u[i + 1]) / 2 for i in range(len(grid_u) - 1)])
    inside = np.zeros((len(centres_u), len(grid_v) - 1), dtype=bool)
    for j in range(len(grid_v) - 1):
        v = (grid_v[j] + grid_v[j + 1]) / 2
        straddle = (y0 > v) != (y1 > v)
        if not straddle.any():
            continue
        a0, b0, a1, b1 = x0[straddle], y0[straddle], x1[straddle], y1[straddle]
        crossings = np.sort(((a1 - a0) * (v - b0)) / (b1 - b0) + a0)
        inside[:, j] = (len(crossings) - np.searchsorted(crossings, centres_u, side="right")) % 2 == 1
    return inside


def _clip_polygon_to_cell(points, cu0: float, cu1: float, cv0: float, cv1: float) -> list:
    """Sutherland-Hodgman against the axis-aligned cell."""
    output = points
    for axis, bound, keep_below in ((0, cu0, False), (0, cu1, True), (1, cv0, False), (1, cv1, True)):
        source = output
        output = []
        count = len(source)
        if count == 0:
            return []
        previous = source[count - 1]
        previous_in = previous[axis] <= bound if keep_below else previous[axis] >= bound
        for i in range(count):
            current = source[i]
            current_in = current[axis] <= bound if keep_below else current[axis] >= bound
            if current_in != previous_in:
                t = (bound - previous[axis]) / (current[axis] - previous[axis])
                output.append([previous[0] + t * (current[0] - previous[0]), previous[1] + t * (current[1] - previous[1])])
            if current_in:
                output.append(current)
            previous, previous_in = current, current_in
        if len(output) < 3:
            return []
    return output


def _point_in_loops(loops, u: float, v: float) -> bool:
    """Whether (u, v) lies inside the loops by the even-odd rule."""
    inside = False
    for points in loops:
        count = len(points)
        for i in range(count):
            x0, y0 = points[i][0], points[i][1]
            x1, y1 = points[(i + 1) % count][0], points[(i + 1) % count][1]
            if (y0 > v) != (y1 > v) and u < ((x1 - x0) * (v - y0)) / (y1 - y0) + x0:
                inside = not inside
    return inside


def _cell_regions(loops, cu0: float, cu1: float, cv0: float, cv1: float) -> list[tuple[list, list]] | None:
    """The part of the face inside the cell as polygons with holes, ``[(contour, holes)]``, or
    None where the loops cannot be followed through it.

    Clipping each loop to the cell on its own (``_clip_polygon_to_cell``) gives a hole that
    crosses a side of the cell as a piece lying along that side, and earcut cannot cut a hole
    out of the outline it touches: it covers part of the hole, or leaves part of the face
    bare. Here each loop is cut at the cell's sides into the runs that lie inside it, and the
    runs are joined end to end by the stretches of the sides that lie inside the face, so a
    hole crossing a side is a bite out of the outline. Loops wholly inside the cell nest by
    the even-odd rule: a hole in what holds them, or an island in a hole."""
    width, height = cu1 - cu0, cv1 - cv0
    if not width > 0 or not height > 0:
        return None
    # Runs are the parts of the loops inside the OPEN cell: a stretch of loop lying along a side
    # is that side, so it is outline, not a run.
    runs: list[list[list[float]]] = []
    rings: list[list[list[float]]] = []
    for points in loops:
        count = len(points)
        start = next((i for i in range(count) if not (cu0 < points[i][0] < cu1 and cv0 < points[i][1] < cv1)), None)
        if start is None:
            rings.append([list(point) for point in points])
            continue
        pieces = []
        for k in range(count):
            p, q = points[(start + k) % count], points[(start + k + 1) % count]
            # Liang-Barsky: the part of segment p -> q inside the cell.
            dx, dy = q[0] - p[0], q[1] - p[1]
            t0, t1 = 0.0, 1.0
            for pk, qk in ((-dx, p[0] - cu0), (dx, cu1 - p[0]), (-dy, p[1] - cv0), (dy, cv1 - p[1])):
                if pk == 0:
                    if qk <= 0:
                        t0, t1 = 1.0, 0.0
                        break
                elif pk < 0:
                    t0 = max(t0, qk / pk)
                else:
                    t1 = min(t1, qk / pk)
            pieces.append((t0, t1) if t1 > t0 else None)
        current = None
        for k in range(count):
            piece = pieces[k]
            p, q = points[(start + k) % count], points[(start + k + 1) % count]
            if piece is None:
                if current is not None:
                    runs.append(current)
                    current = None
                continue
            t0, t1 = piece
            enter = [p[0] + t0 * (q[0] - p[0]), p[1] + t0 * (q[1] - p[1])] if t0 > 0 else list(p)
            leave = [p[0] + t1 * (q[0] - p[0]), p[1] + t1 * (q[1] - p[1])] if t1 < 1 else list(q)
            if current is None:
                current = [enter]
            elif t0 > 0:
                # Out through a vertex on a side and back in at once: two runs.
                runs.append(current)
                current = [enter]
            current.append(leave)
            if t1 < 1:
                runs.append(current)
                current = None
        if current is not None:
            runs.append(current)

    def snap(point: list[float]) -> tuple[float, list[float]]:
        """The point put on the nearest side, and its place along the outline (0 to 4,
        anticlockwise from the corner (cu0, cv0))."""
        u, v = min(max(point[0], cu0), cu1), min(max(point[1], cv0), cv1)
        side = min((v - cv0, 0), (cu1 - u, 1), (cv1 - v, 2), (u - cu0, 3))[1]
        if side == 0:
            return (u - cu0) / width, [u, cv0]
        if side == 1:
            return 1 + (v - cv0) / height, [cu1, v]
        if side == 2:
            return 2 + (cu1 - u) / width, [u, cv1]
        return 3 + (cv1 - v) / height, [cu0, v]

    def inward(s: float) -> list[float]:
        """The point of the outline at place ``s``, a hair inside the cell: whether the face is
        there is whether the face borders that stretch of outline."""
        s %= 4
        nudge_u, nudge_v = width * 1e-7, height * 1e-7
        if s < 1:
            return [cu0 + s * width, cv0 + nudge_v]
        if s < 2:
            return [cu1 - nudge_u, cv0 + (s - 1) * height]
        if s < 3:
            return [cu1 - (s - 2) * width, cv1 - nudge_v]
        return [cu0 + nudge_u, cv1 - (s - 3) * height]

    corners = ([cu0, cv0], [cu1, cv0], [cu1, cv1], [cu0, cv1])
    polygons: list[list[list[float]]] = []
    if runs:
        ends = []  # (place, run, which end: 0 its first point, 1 its last)
        for r, run in enumerate(runs):
            s0, run[0] = snap(run[0])
            s1, run[-1] = snap(run[-1])
            ends.extend(((s0, r, 0), (s1, r, 1)))
        ends.sort()
        n = len(ends)
        # The stretch of outline from each end to the next, anticlockwise, and whether it lies
        # inside the face: its middle says, or, where two ends meet at one place, the stretch
        # before it (the face changes sides at every end).
        length = [ends[k + 1][0] - ends[k][0] for k in range(n - 1)] + [ends[0][0] + 4 - ends[-1][0]]
        inside = [None] * n
        for k in range(n):
            if length[k] > 0:
                inside[k] = _point_in_loops(loops, *inward(ends[k][0] + length[k] / 2))
        if all(state is None for state in inside):
            return None
        for _pass in range(2):
            for k in range(n):
                if inside[k] is None and inside[k - 1] is not None:
                    inside[k] = not inside[k - 1]
        slot = {(r, which): k for k, (_s, r, which) in enumerate(ends)}
        used = [False] * len(runs)
        for first in range(len(runs)):
            if used[first]:
                continue
            polygon: list[list[float]] = []
            r, forward = first, True
            for _step in range(len(runs) + 1):
                if used[r]:
                    return None
                used[r] = True
                polygon.extend(runs[r] if forward else runs[r][::-1])
                k = slot[(r, 1 if forward else 0)]
                ahead, behind = inside[k], inside[k - 1]
                if ahead == behind:
                    return None
                s_from = ends[k][0]
                if ahead:
                    following, reach = (k + 1) % n, length[k]
                    c = math.floor(s_from) + 1
                    while c < s_from + reach:
                        polygon.append(list(corners[c % 4]))
                        c += 1
                else:
                    following, reach = (k - 1) % n, length[k - 1]
                    c = math.ceil(s_from) - 1
                    while c > s_from - reach:
                        polygon.append(list(corners[c % 4]))
                        c -= 1
                _s, r, which = ends[following]
                forward = which == 0
                if r == first:
                    if not forward:
                        return None
                    break
            else:
                return None
            polygons.append(polygon)
    elif sum(_point_in_loops(loops, *inward(s)) for s in (0.5, 1.5, 2.5, 3.5)) >= 3:
        polygons.append([list(corner) for corner in corners])
    # Loops wholly inside the cell: each lies in as many of the others as its even-odd depth.
    contours = [(polygon, []) for polygon in polygons]
    depth_of = []
    for ring in rings:
        u, v = ring[0]
        depth = sum(_point_in_loops([other], u, v) for other in polygons + [o for o in rings if o is not ring])
        depth_of.append(depth)
    for ring, depth in sorted(zip(rings, depth_of), key=lambda item: item[1]):
        if depth % 2 == 0:
            contours.append((ring, []))
            continue
        u, v = ring[0]
        holder = next((c for c in reversed(contours) if _point_in_loops([c[0]], u, v)), None)
        if holder is None:
            return None
        holder[1].append(ring)
    return [(_deduplicated(contour), [_deduplicated(hole) for hole in holes]) for contour, holes in contours]


def _covered(points: list[list[float]], triangles: list[list[int]]) -> float:
    """The area earcut's triangles over ``points`` cover."""
    area = 0.0
    for a, b, c in triangles:
        ax, ay = points[a]
        bx, by = points[b]
        cx, cy = points[c]
        area += abs((bx - ax) * (cy - ay) - (cx - ax) * (by - ay))
    return area / 2


def _deduplicated(points: list[list[float]]) -> list[list[float]]:
    """The polygon without a point repeating the one before it (the last against the first)."""
    kept = [point for i, point in enumerate(points) if point != points[i - 1]] if len(points) > 1 else points
    return kept or points[:1]


class _Grid:
    __slots__ = ("uv_verts", "triangles", "vertex_ids", "segments", "segments_by_cell", "min_u", "min_v", "du", "dv",
                 "steps_u", "steps_v")

    def cell_of(self, u: float, v: float) -> tuple[int, int]:
        return (min(self.steps_u - 1, max(0, math.floor((u - self.min_u) / self.du))),
                min(self.steps_v - 1, max(0, math.floor((v - self.min_v) / self.dv))))


def _grid_triangulate(face: _Face, loops: list[_Ring], chord_limit: float, angle_limit: float = math.inf,
                      flip: bool = True) -> _Grid | None:
    min_u = min_v = math.inf
    max_u = max_v = -math.inf
    for points in loops:
        for u, v in points:
            if u < min_u:
                min_u = u
            if u > max_u:
                max_u = u
            if v < min_v:
                min_v = v
            if v > max_v:
                max_v = v
    if not max_u > min_u or not max_v > min_v:
        return None
    uv_box = (min_u, max_u, min_v, max_v)
    steps_u = min(256, max(_grid_steps_for_direction(face, uv_box, chord_limit, 0),
                           math.ceil((max_u - min_u) / angle_limit)))
    steps_v = min(256, max(_grid_steps_for_direction(face, uv_box, chord_limit, 1),
                           math.ceil((max_v - min_v) / angle_limit) if face.surface.kind == "sphere" else 1))
    du = (max_u - min_u) / steps_u
    dv = (max_v - min_v) / steps_v
    grid = _Grid()
    grid.min_u, grid.min_v, grid.du, grid.dv, grid.steps_u, grid.steps_v = min_u, min_v, du, dv, steps_u, steps_v
    cell_of = grid.cell_of

    # Loop segments bucketed by the cells their boxes touch: which cells a trim crosses here, and
    # which segment a boundary vertex lies on when it is pinned.
    crossed: set[int] = set()
    segments: list[tuple] = []
    segments_by_cell: dict[int, list[int]] = {}
    for points in loops:
        ords = points.ords
        metas = points.metas
        count = len(points)
        for i in range(count):
            x0, y0 = points[i][0], points[i][1]
            x1, y1 = points[(i + 1) % count][0], points[(i + 1) % count][1]
            segment_index = len(segments)
            segments.append((x0, y0, x1, y1, ords[i] or 0, metas[i]))
            ca0, cb0 = cell_of(min(x0, x1), min(y0, y1))
            ca1, cb1 = cell_of(max(x0, x1), max(y0, y1))
            for cu in range(ca0, ca1 + 1):
                for cv in range(cb0, cb1 + 1):
                    key = cu * steps_v + cv
                    crossed.add(key)
                    bucket = segments_by_cell.get(key)
                    if bucket is None:
                        segments_by_cell[key] = bucket = []
                    bucket.append(segment_index)

    uv_verts: list[list[float]] = []
    vertex_ids: dict[tuple[float, float], int] = {}
    # Two vertices within one source ULP on both axes are one vertex: a grid line and a trim, or
    # two trims meeting at a corner, a hair apart. Opposite periodic seams stay distinct.
    epsilon_u = max(abs(min_u), abs(max_u), max_u - min_u, 1e-12) * 2 ** -23
    epsilon_v = max(abs(min_v), abs(max_v), max_v - min_v, 1e-12) * 2 ** -23
    nearby: dict[tuple[int, int], list[int]] = {}
    curved = face.surface.kind != "plane"
    floor = math.floor

    def vertex_id(u: float, v: float) -> int:
        iu = _round_half_up((u - min_u) / du)
        iv = _round_half_up((v - min_v) / dv)
        grid_u = max_u if iu == steps_u else min_u + iu * du
        grid_v = max_v if iv == steps_v else min_v + iv * dv
        if curved:
            if abs(u - grid_u) <= epsilon_u:
                u = grid_u
            if abs(v - grid_v) <= epsilon_v:
                v = grid_v
        key = (u, v)
        found = vertex_ids.get(key)
        if found is not None:
            return found
        cu = floor(u / epsilon_u)
        cv = floor(v / epsilon_v)
        # Planes keep exact keys: conformity looks their boundary vertices up by the uv it
        # projected them to.
        if curved:
            for a in (-1, 0, 1):
                if found is not None:
                    break
                for b in (-1, 0, 1):
                    if found is not None:
                        break
                    for candidate in nearby.get((cu + a, cv + b), ()):
                        x, y = uv_verts[candidate]
                        if abs(x - u) <= epsilon_u and abs(y - v) <= epsilon_v:
                            found = candidate
                            break
        if found is None:
            found = len(uv_verts)
            uv_verts.append([u, v])
            if curved:
                nearby.setdefault((cu, cv), []).append(found)
        vertex_ids[key] = found
        return found

    triangles: list[int] = []
    # Where earcut joins three points of one straight run of trim -- a run with a corner on it,
    # the neck of an L-shaped face -- it emits zero-area triangles over the run, and the
    # triangle it hangs above them spans the whole run: dropped, they leave the run's middle
    # points on that one edge, a T-junction no conformity can split. The flips take them out
    # instead (each one, flipped with its neighbour, makes two that stand on the run), and only
    # those no flip could take are dropped.
    collinear: set[int] = set()
    degenerate_limit = abs((max_u - min_u) * (max_v - min_v)) * 1e-12 or 1e-30
    # Grid lines' coordinates are canonical: adjacent cells address their border with the same
    # floats, or every border becomes an unwelded crack.
    grid_us = [max_u if iu == steps_u else min_u + iu * du for iu in range(steps_u + 1)]
    grid_vs = [max_v if iv == steps_v else min_v + iv * dv for iv in range(steps_v + 1)]
    inside = None
    for iu in range(steps_u):
        cu0 = grid_us[iu]
        cu1 = grid_us[iu + 1]
        for iv in range(steps_v):
            cv0 = grid_vs[iv]
            cv1 = grid_vs[iv + 1]
            if iu * steps_v + iv not in crossed:
                # An uncrossed cell is wholly inside or outside: its centre says which.
                if inside is None:
                    inside = _inside_centres(loops, grid_us, grid_vs)
                if not inside[iu, iv]:
                    continue
                a = vertex_id(cu0, cv0)
                b = vertex_id(cu1, cv0)
                c = vertex_id(cu1, cv1)
                d = vertex_id(cu0, cv1)
                triangles.extend((a, b, c, a, c, d))
                continue
            # A cell a trim crosses: every loop clipped to it, the piece earcut.
            clipped = [piece for piece in (_clip_polygon_to_cell(points, cu0, cu1, cv0, cv1) for points in loops)
                       if len(piece) >= 3]
            if not clipped:
                continue
            outer_index = 0
            outer_abs_area = 0.0
            for i in range(len(clipped)):
                abs_area = abs(_polygon_area(clipped[i]))
                if abs_area > outer_abs_area:
                    outer_abs_area = abs_area
                    outer_index = i
            if outer_abs_area <= degenerate_limit:
                continue
            contour = [[x, y] for x, y in clipped[outer_index]]
            holes = [[[x, y] for x, y in piece] for i, piece in enumerate(clipped) if i != outer_index]
            try:
                cell_faces = triangulate_shape(contour, holes)
            except Exception:  # noqa: BLE001 - a piece earcut cannot take is skipped, as there
                continue
            pieces = [(contour + [point for hole in holes for point in hole], cell_faces)]
            # What earcut made must cover what the loops leave of the cell. Where it does not --
            # a hole crossing a side of the cell, which this clipping hands earcut as a hole
            # touching its outline -- the cell is cut again from its loops as a whole
            # (``_cell_regions``), and that cut is taken where it covers what it must.
            region = outer_abs_area - sum(abs(_polygon_area(piece)) for i, piece in enumerate(clipped) if i != outer_index)
            slack = (cu1 - cu0) * (cv1 - cv0) * 1e-7
            if abs(_covered(*pieces[0]) - region) > slack:
                rebuilt = _cell_regions(loops, cu0, cu1, cv0, cv1)
                if rebuilt is not None:
                    try:
                        cut = [(outline + [point for hole in inner for point in hole],
                                triangulate_shape([list(point) for point in outline], [[list(point) for point in hole]
                                                                                        for hole in inner]))
                               for outline, inner in rebuilt if len(outline) >= 3]
                    except Exception:  # noqa: BLE001 - earcut's refusal leaves the first cut
                        cut = None
                    if cut is not None and abs(sum(_covered(*piece) for piece in cut) - region) <= slack:
                        pieces = cut
            for local_points, cell_faces in pieces:
                local_ids = [vertex_id(x, y) for x, y in local_points]
                for a, b, c in cell_faces:
                    ax, ay = local_points[a]
                    bx, by = local_points[b]
                    cx, cy = local_points[c]
                    cross = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay)
                    if local_ids[a] == local_ids[b] or local_ids[b] == local_ids[c] or local_ids[c] == local_ids[a]:
                        continue
                    if abs(cross) / 2 <= degenerate_limit:
                        if not flip:
                            continue
                        collinear.add(len(triangles))
                    triangles.extend((local_ids[a], local_ids[b], local_ids[c]))
    if flip:
        _flip_slivers(face, uv_verts, triangles, degenerate_limit, collinear)
    if collinear:
        triangles = [index for t in range(0, len(triangles), 3) if t not in collinear for index in triangles[t:t + 3]]
    triangles = _split_iso_junctions(uv_verts, triangles)
    grid.uv_verts, grid.triangles, grid.vertex_ids = uv_verts, triangles, vertex_ids
    grid.segments, grid.segments_by_cell = segments, segments_by_cell
    return grid


def _split_iso_junctions(uv_verts: list[list[float]], triangles: list[int]) -> list[int]:
    """Each triangle fanned to the vertices lying inside one of its edges along a line of
    constant u or v. A trim that runs along one -- a row of slots whose ends line up, a step --
    puts its corners exactly on that line, and earcut's diagonals may then run along it straight
    past them: the triangles on either side meet at different points, and the face opens along
    the line. The browser's single-precision trims rarely lay their corners exactly in line."""
    used = sorted(set(triangles))
    along_u: dict[float, list[float]] = {}  # a line of constant v: the u of each vertex on it
    along_v: dict[float, list[float]] = {}
    for index in used:
        u, v = uv_verts[index]
        along_u.setdefault(v, []).append(u)
        along_v.setdefault(u, []).append(v)
    for values in (*along_u.values(), *along_v.values()):
        values.sort()

    def inside(p: int, q: int) -> list[float] | None:
        """The coordinates along edge (p, q), from p toward q, of the vertices strictly inside it."""
        (pu, pv), (qu, qv) = uv_verts[p], uv_verts[q]
        if pv == qv:
            line, start, end = along_u[pv], pu, qu
        elif pu == qu:
            line, start, end = along_v[pu], pv, qv
        else:
            return None
        lo, hi = (start, end) if start < end else (end, start)
        between = line[bisect_right(line, lo):bisect_left(line, hi)]
        if not between:
            return None
        return between if start < end else between[::-1]

    if not any(len(values) > 2 for values in (*along_u.values(), *along_v.values())):
        return triangles
    ids = {}
    for index in used:
        ids[(uv_verts[index][0], uv_verts[index][1])] = index
    out: list[int] = []
    pending = [tuple(triangles[t:t + 3]) for t in range(len(triangles) - 3, -3, -3)]
    while pending:
        a, b, c = pending.pop()
        for p, q, r in ((a, b, c), (b, c, a), (c, a, b)):
            between = inside(p, q)
            if between is None:
                continue
            (pu, pv), (qu, qv) = uv_verts[p], uv_verts[q]
            points = [ids[(x, pv)] for x in between] if pv == qv else [ids[(pu, y)] for y in between]
            # A fan from the opposite corner, each piece wound as the triangle was; each goes back
            # on the stack, in order, for its other edges.
            chain = [p, *points, q]
            pending.extend((chain[k], chain[k + 1], r) for k in range(len(chain) - 2, -1, -1))
            break
        else:
            out.extend((a, b, c))
    return out


def _round_half_up(value: float) -> int:
    """JavaScript's ``Math.round``: the nearest integer, a half toward positive infinity (exact, where
    ``floor(value + 0.5)`` rounds 0.49999999999999994 up)."""
    whole = math.floor(value)
    return whole + 1 if value - whole >= 0.5 else whole


def _flip_slivers(face: _Face, uv_verts: list[list[float]], triangles: list[int], degenerate_limit: float,
                  collinear: set[int]) -> None:
    """Lawson flips over the base triangulation, in the face's parameters mapped through the
    surface's metric at their box's centre: earcut takes any convex corner for an ear, so a cell a
    curving trim crosses comes back as a stack of slivers, and refinement would split them on and
    on. A flip is made only where both new triangles keep the face's winding; an edge one triangle
    uses is never touched."""
    if not uv_verts:
        return
    min_u = min(uv[0] for uv in uv_verts)
    max_u = max(uv[0] for uv in uv_verts)
    min_v = min(uv[1] for uv in uv_verts)
    max_v = max(uv[1] for uv in uv_verts)
    if not max_u > min_u or not max_v > min_v:
        return
    # The first fundamental form at the centre, by central differences.
    value = face.surface.value
    cu, cv = (min_u + max_u) / 2, (min_v + max_v) / 2
    hu, hv = (max_u - min_u) * 1e-4, (max_v - min_v) * 1e-4
    pu1, pu0 = value(cu + hu, cv), value(cu - hu, cv)
    pv1, pv0 = value(cu, cv + hv), value(cu, cv - hv)
    su = [(pu1[d] - pu0[d]) / (2 * hu) for d in range(3)]
    sv = [(pv1[d] - pv0[d]) / (2 * hv) for d in range(3)]
    e = su[0] * su[0] + su[1] * su[1] + su[2] * su[2]
    f = su[0] * sv[0] + su[1] * sv[1] + su[2] * sv[2]
    g = sv[0] * sv[0] + sv[1] * sv[1] + sv[2] * sv[2]
    det = e * g - f * f
    # A singular or non-finite metric (a pole, a collapsed patch) has no plane to measure in.
    if not e > 0 or not det > e * g * 1e-12 or not math.isfinite(det):
        return
    root_e = math.sqrt(e)
    shear = f / root_e
    height = math.sqrt(det) / root_e
    plane = [(root_e * u + shear * v, height * v) for u, v in uv_verts]

    def angle_sum_past_pi(a: int, b: int, c: int, d: int) -> bool:
        """sin(alpha + beta) < 0 for the angles at c and d over edge (a, b), scaled by the four
        edges' lengths: the Delaunay test."""
        ax, ay = plane[a]
        bx, by = plane[b]
        cx, cy = plane[c]
        dx, dy = plane[d]
        cax, cay, cbx, cby = ax - cx, ay - cy, bx - cx, by - cy
        dax, day, dbx, dby = ax - dx, ay - dy, bx - dx, by - dy
        value_ = abs(cax * cby - cay * cbx) * (dax * dbx + day * dby) + (cax * cbx + cay * cby) * abs(dax * dby - day * dbx)
        return value_ < -1e-9 * math.sqrt((cax * cax + cay * cay) * (cbx * cbx + cby * cby)
                                          * (dax * dax + day * day) * (dbx * dbx + dby * dby))

    def orient(a: int, b: int, c: int) -> float:
        return ((uv_verts[b][0] - uv_verts[a][0]) * (uv_verts[c][1] - uv_verts[a][1])
                - (uv_verts[c][0] - uv_verts[a][0]) * (uv_verts[b][1] - uv_verts[a][1]))

    # Each edge (``low << 32 | high``) and the triangles that use it (their offsets, in order).
    owners: dict[int, list[int]] = {}
    find = owners.get

    def own(t: int) -> None:
        a, b, c = triangles[t], triangles[t + 1], triangles[t + 2]
        for p, q in ((a, b), (b, c), (c, a)):
            key = (p << 32 | q) if p < q else (q << 32 | p)
            owned = find(key)
            if owned is None:
                owners[key] = [t]
            else:
                owned.append(t)

    def disown(t: int) -> None:
        a, b, c = triangles[t], triangles[t + 1], triangles[t + 2]
        for p, q in ((a, b), (b, c), (c, a)):
            key = (p << 32 | q) if p < q else (q << 32 | p)
            owned = owners[key]
            owned.remove(t)
            if not owned:
                del owners[key]

    for t in range(0, len(triangles), 3):
        a, b, c = triangles[t], triangles[t + 1], triangles[t + 2]
        for p, q in ((a, b), (b, c), (c, a)):
            key = (p << 32 | q) if p < q else (q << 32 | p)
            owned = find(key)
            if owned is None:
                owners[key] = [t]
            else:
                owned.append(t)
    pending = list(owners)
    queued = set(pending)
    # Planar Delaunay flipping ends; the budget only guards against rounding at the margin.
    budget = len(triangles) * 4
    while pending and budget > 0:
        key = pending.pop()
        queued.discard(key)
        owned = find(key)
        if not owned or len(owned) != 2:
            continue
        t1, t2 = owned
        a = b = c = -1
        for k in range(3):
            p, q = triangles[t1 + k], triangles[t1 + (k + 1) % 3]
            if ((p << 32 | q) if p < q else (q << 32 | p)) == key:
                a, b, c = p, q, triangles[t1 + (k + 2) % 3]
        d = -1
        for k in range(3):
            p = triangles[t2 + k]
            if p != a and p != b:
                d = p
        if c < 0 or d < 0 or c == d:
            continue
        if not angle_sum_past_pi(a, b, c, d) or angle_sum_past_pi(c, d, b, a):
            continue
        # The face's winding, read from the larger of the two: a sliver of three consecutive trim
        # points has no sign of its own, and flipping it is what removes it.
        first = orient(a, b, c)
        second = orient(b, a, d)
        larger = first if abs(first) >= abs(second) else second
        sign = (larger > 0) - (larger < 0)
        if not sign or sign * orient(a, d, c) <= degenerate_limit or sign * orient(d, b, c) <= degenerate_limit:
            continue
        disown(t1)
        disown(t2)
        # Both new triangles stand: neither slot holds a collinear one any more.
        collinear.discard(t1)
        collinear.discard(t2)
        triangles[t1], triangles[t1 + 1], triangles[t1 + 2] = a, d, c
        triangles[t2], triangles[t2 + 1], triangles[t2 + 2] = d, b, c
        own(t1)
        own(t2)
        budget -= 1
        for p, q in ((a, d), (d, b), (b, c), (c, a)):
            next_key = (p << 32 | q) if p < q else (q << 32 | p)
            if next_key not in queued:
                queued.add(next_key)
                pending.append(next_key)


class _Raw:
    __slots__ = ("uv_verts", "xyz", "nrm", "triangles", "grid", "boundary", "loops", "singular_grid", "minted")


def _pin_boundary(grid: _Grid, us: np.ndarray, vs: np.ndarray, xyz: list, boundary: dict, shared_edges: dict) -> None:
    """Every vertex on a trim segment of a shared edge labelled with the edge and its fraction
    there (``boundary``) and put at that fraction's point (``xyz``), the pinning label first. Each
    vertex is held against the segments of its grid cell, all at once; the few that lie on one
    are then chosen among one by one, as the browser chose."""
    eps = max(float(np.max(np.abs(us))), float(np.max(np.abs(vs))), 1) * 1e-7
    eps_sq = eps * eps
    corner_eps_sq = eps_sq * 16
    segments = grid.segments
    cells = sorted(grid.segments_by_cell)
    if not cells or not len(us):
        return
    counts_by_cell = np.array([len(grid.segments_by_cell[key]) for key in cells], np.int64)
    flat = np.array([index for key in cells for index in grid.segments_by_cell[key]], np.int64)
    starts = np.cumsum(counts_by_cell) - counts_by_cell
    cell_keys = np.array(cells, np.int64)
    cu = np.minimum(grid.steps_u - 1, np.maximum(0, np.floor((us - grid.min_u) / grid.du))).astype(np.int64)
    cv = np.minimum(grid.steps_v - 1, np.maximum(0, np.floor((vs - grid.min_v) / grid.dv))).astype(np.int64)
    key = cu * grid.steps_v + cv
    at = np.minimum(np.searchsorted(cell_keys, key), len(cell_keys) - 1)
    crossed = cell_keys[at] == key
    vertices = np.nonzero(crossed)[0]
    if not len(vertices):
        return
    counts = counts_by_cell[at[vertices]]
    pair_vertex = np.repeat(vertices, counts)
    offsets = np.cumsum(counts) - counts
    pair_segment = flat[np.repeat(starts[at[vertices]], counts) + (np.arange(int(counts.sum())) - np.repeat(offsets, counts))]
    table = np.array([(x0, y0, x1, y1) for x0, y0, x1, y1, _ord, _meta in segments], np.float64).reshape(-1, 4)
    on_shared = np.array([meta is not None for *_rest, meta in segments], bool)
    px, py = us[pair_vertex], vs[pair_vertex]
    x0, y0, x1, y1 = (table[pair_segment, k] for k in range(4))
    dx, dy = x1 - x0, y1 - y0
    length_sq = dx * dx + dy * dy
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(length_sq > 0, ((px - x0) * dx + (py - y0) * dy) / length_sq, 0.0)
    t = np.maximum(0.0, np.minimum(1.0, t))
    qx, qy = x0 + t * dx, y0 + t * dy
    dist_sq = (px - qx) * (px - qx) + (py - qy) * (py - qy)
    on_segment = dist_sq < eps_sq
    # Adjacent pcurves can disagree about a shared corner by more than the eps: the corner caught
    # by its ends.
    d0 = (px - x0) * (px - x0) + (py - y0) * (py - y0)
    d1 = (px - x1) * (px - x1) + (py - y1) * (py - y1)
    at_start = ~on_segment & (d0 < corner_eps_sq)
    at_end = ~on_segment & ~at_start & (d1 < corner_eps_sq)
    dist_sq = np.where(on_segment, dist_sq, np.where(at_start, d0, d1))
    t = np.where(on_segment, t, np.where(at_start, 0.0, 1.0))
    found = on_shared[pair_segment] & (on_segment | at_start | at_end)
    chosen: dict[int, dict[int, tuple[float, float]]] = {}
    for vertex, segment_index, distance, along in zip(pair_vertex[found].tolist(), pair_segment[found].tolist(),
                                                      dist_sq[found].tolist(), t[found].tolist()):
        _x0, _y0, _x1, _y1, ord_, meta = segments[segment_index]
        # A vertex can lie on several model edges (a corner lies on both edges that meet there):
        # the best match per edge, or conformity is blind to the edge that lost.
        by_ord = chosen.setdefault(vertex, {})
        existing = by_ord.get(ord_)
        if existing is not None and existing[0] <= distance:
            continue
        # A segment's ends snap to their exact fractions, so a corner dedups across faces.
        along = 0 if along < 1e-9 else 1 if along > 1 - 1e-9 else along
        by_ord[ord_] = (distance, meta.f0 + along * (meta.f1 - meta.f0))
    for vertex, by_ord in chosen.items():
        labels = []
        pin = None
        for ord_, (distance, f) in by_ord.items():
            shared = shared_edges.get(ord_)
            if shared is None:
                continue
            fraction = 0 if (shared.closed and f >= 1 - 1e-12) else f
            labels.append([ord_, fraction])
            if pin is None or distance < pin[0]:
                pin = (distance, ord_, fraction, shared)
        if not labels:
            continue
        # The label the vertex is pinned through comes first.
        labels.sort(key=lambda label: 0 if (label[0] == pin[1] and label[1] == pin[2]) else 1)
        boundary[vertex] = labels
        xyz[vertex] = _edge_point_at(pin[3], pin[2])


def _edge_keys(triangles: np.ndarray) -> np.ndarray:
    """Each triangle's edges (a, b), (b, c), (c, a), in that order, as ``low << 32 | high``."""
    first = triangles.reshape(-1)
    second = triangles[:, [1, 2, 0]].reshape(-1)
    return (np.minimum(first, second) << 32) | np.maximum(first, second)


def _split(triangles: np.ndarray, marked: np.ndarray, first_index: int):
    """Each triangle (``triangles``, (t, 3)) split by its marked edges (``marked``, sorted edge
    keys) into four, three or two, as the browser split them, in order: the triangles, which of
    them are new, and each new midpoint's edge, ``(p, q)`` rows numbered from ``first_index`` in
    the order the triangles first reach them."""
    keys = _edge_keys(triangles)
    hit = marked[np.minimum(np.searchsorted(marked, keys), len(marked) - 1)] == keys
    reached = keys[hit]
    unique, first = np.unique(reached, return_index=True)
    order = np.argsort(first)
    number = np.empty(len(unique), np.int64)
    number[order] = first_index + np.arange(len(unique), dtype=np.int64)
    mid = np.full(len(keys), -1, np.int64)
    mid[hit] = number[np.searchsorted(unique, reached)]
    mid = mid.reshape(-1, 3)
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    mab, mbc, mca = mid[:, 0], mid[:, 1], mid[:, 2]
    sab, sbc, sca = mab >= 0, mbc >= 0, mca >= 0
    splits = sab.astype(np.int64) + sbc + sca
    counts = np.choose(splits, (1, 2, 3, 4))
    offsets = np.cumsum(counts) - counts
    out = np.empty((int(counts.sum()), 3), np.int64)
    whole = splits == 0
    out[offsets[whole]] = triangles[whole]
    four = splits == 3
    at = offsets[four]
    A, B, C, M1, M2, M3 = a[four], b[four], c[four], mab[four], mbc[four], mca[four]
    out[at] = np.stack((A, M1, M3), axis=1)
    out[at + 1] = np.stack((M1, B, M2), axis=1)
    out[at + 2] = np.stack((M3, M2, C), axis=1)
    out[at + 3] = np.stack((M1, M2, M3), axis=1)
    three = splits == 2
    at = offsets[three]
    # Turned so the edge left whole is (c, a).
    first_two, last_two = (sab & sbc)[three], (sbc & sca)[three]
    A, B, C, M1, M2, M3 = a[three], b[three], c[three], mab[three], mbc[three], mca[three]
    va = np.where(first_two, A, np.where(last_two, B, C))
    vb = np.where(first_two, B, np.where(last_two, C, A))
    vc = np.where(first_two, C, np.where(last_two, A, B))
    m1 = np.where(first_two, M1, np.where(last_two, M2, M3))
    m2 = np.where(first_two, M2, np.where(last_two, M3, M1))
    out[at] = np.stack((va, m1, m2), axis=1)
    out[at + 1] = np.stack((va, m2, vc), axis=1)
    out[at + 2] = np.stack((m1, vb, m2), axis=1)
    two = splits == 1
    at = offsets[two]
    # Turned so the split edge is (a, b).
    on_ab, on_bc = sab[two], sbc[two]
    A, B, C, M1, M2, M3 = a[two], b[two], c[two], mab[two], mbc[two], mca[two]
    va = np.where(on_ab, A, np.where(on_bc, B, C))
    vb = np.where(on_ab, B, np.where(on_bc, C, A))
    vc = np.where(on_ab, C, np.where(on_bc, A, B))
    m = np.where(on_ab, M1, np.where(on_bc, M2, M3))
    out[at] = np.stack((va, m, vc), axis=1)
    out[at + 1] = np.stack((m, vb, vc), axis=1)
    fresh = np.repeat(~whole, counts)
    ordered = unique[order]
    return out, fresh, np.stack((ordered >> 32, ordered & 0xFFFFFFFF), axis=1)


def _tessellate_face_raw(face: _Face, scale: float, chord: float, angle: float, shared_edges: dict | None) -> _Raw | None:
    if not face.loops:
        return None
    # Tolerances scale by the face's own extent where it is smaller than the component's: a small
    # curved feature refines as itself.
    rough_limit = _LOOP_TOLERANCE * scale
    loops0 = [points for points in (_sample_loop_polygon(face, loop, rough_limit, shared_edges) for loop in face.loops)
              if len(points) >= 3]
    if not loops0:
        raise _Unresolved
    min_u = min_v = math.inf
    max_u = max_v = -math.inf
    for points in loops0:
        for u, v in points:
            if u < min_u:
                min_u = u
            if u > max_u:
                max_u = u
            if v < min_v:
                min_v = v
            if v > max_v:
                max_v = v
    value = face.surface.value
    probes = [value(u, v) for u, v in ((min_u, min_v), (max_u, min_v), (min_u, max_v), (max_u, max_v),
                                        ((min_u + max_u) / 2, (min_v + max_v) / 2))]
    face_scale = 0.0
    for i in range(len(probes)):
        for j in range(i + 1, len(probes)):
            face_scale = max(face_scale, _distance(probes[i], probes[j]))
    local = max(min(scale, face_scale * 4), 1e-9)
    chord_limit = chord * local
    loop_limit = _LOOP_TOLERANCE * local
    loops = loops0
    if loop_limit < rough_limit:
        loops = [points for points in (_sample_loop_polygon(face, loop, loop_limit, shared_edges)
                                       for loop in face.loops) if len(points) >= 3]
    if not loops:
        raise _Unresolved

    # A rectangular sphere or cone patch that ends at a pole has a singular uv edge, where
    # adaptive splitting can invert facets: its regular angular grid is built to the chord and
    # angle criteria at once and its pole row collapsed, with no refinement after.
    u0, u1, v0, v1 = face.uv
    singular_grid = (len(loops) == 1 and (_singular_u(face.surface, v0) or _singular_u(face.surface, v1))
                     and all(u == u0 or u == u1 or v == v0 or v == v1 for u, v in loops[0]))
    grid = _grid_triangulate(face, loops, chord_limit / 3 if singular_grid else chord_limit,
                             angle / math.sqrt(2) if singular_grid else math.inf, flip=not singular_grid)
    if grid is None:
        raise _Unresolved
    uv_verts = grid.uv_verts
    triangles = grid.triangles
    if not triangles:
        return None

    surface = face.surface
    us = np.array([u for u, _v in uv_verts], np.float64)
    vs = np.array([v for _u, v in uv_verts], np.float64)
    points = surface.values(us, vs)
    if singular_grid:
        xyz = points.tolist()
        poles: dict[float, int] = {}
        remap: dict[int, int] = {}
        for i in range(len(uv_verts)):
            if not _singular_u(surface, uv_verts[i][1]):
                continue
            key = uv_verts[i][1]
            if key in poles:
                remap[i] = poles[key]
            else:
                poles[key] = i
        regular = []
        tiny = scale * 1e-12
        for t in range(0, len(triangles), 3):
            a, b, c = (remap.get(i, i) for i in triangles[t:t + 3])
            if _distance(xyz[a], xyz[b]) <= tiny or _distance(xyz[b], xyz[c]) <= tiny or _distance(xyz[c], xyz[a]) <= tiny:
                continue
            regular.extend((a, b, c))
        triangles = regular
    uv_box = face.uv
    normals = surface.normals(us, vs, uv_box)
    angle_cos = math.cos(angle)

    # Crack-free refinement, two phases a round: MARK the edges whose 3D midpoint strays past the
    # tolerance or whose ends' normals part past the angle, and a triangle's longest uv edge where
    # its centre strays or its facet tilts off the surface; then SUBDIVIDE by the marks, each
    # midpoint made once for the triangles either side. An edge's verdict depends on its ends
    # alone, so each is reached once; and a triangle a round leaves whole marks nothing in the
    # rounds after (its edges passed, and its own mark was none, or it would have been split), so
    # only the triangles the last split made are looked at. Each round's arithmetic runs over
    # arrays, to the bits the browser's one-at-a-time loop gave.
    # A round of a few triangles runs one at a time, a larger one over arrays: the same bits either
    # way, at the lower cost.
    xyz = points.tolist()
    nrm = normals.tolist()
    uv_list = [[u, v] for u, v in uv_verts]
    edge_bad: dict[int, bool] = {}
    sqrt = math.sqrt
    value = surface.value
    mesh = np.asarray(triangles, np.int64).reshape(-1, 3)
    fresh = np.ones(len(mesh), bool)
    for _depth in range(0 if singular_grid else _MAX_REFINE_DEPTH):
        new = mesh[fresh]
        if len(new) >= _ARRAY_ROUND:
            keys = _edge_keys(new)
            unjudged = np.array([k for k in np.unique(keys).tolist() if k not in edge_bad], np.int64)
            if len(unjudged):
                p, q = unjudged >> 32, unjudged & 0xFFFFFFFF
                mid = surface.values((us[p] + us[q]) / 2, (vs[p] + vs[q]) / 2)
                dx = mid[:, 0] - (points[p, 0] + points[q, 0]) / 2
                dy = mid[:, 1] - (points[p, 1] + points[q, 1]) / 2
                dz = mid[:, 2] - (points[p, 2] + points[q, 2]) / 2
                dot = normals[p, 0] * normals[q, 0] + normals[p, 1] * normals[q, 1] + normals[p, 2] * normals[q, 2]
                edge_bad.update(zip(unjudged.tolist(), ((np.sqrt(dx * dx + dy * dy + dz * dz) > chord_limit)
                                                        | (dot < angle_cos)).tolist()))
            bad = np.fromiter((edge_bad[k] for k in keys.tolist()), bool, len(keys)).reshape(-1, 3)
            marks = [keys.reshape(-1, 3)[bad]]
            quiet = new[~bad.any(axis=1)]
            if len(quiet):
                marks.append(_triangle_marks(quiet, us, vs, points, normals, surface, chord_limit, angle_cos))
            marked_keys = np.concatenate(marks)
        else:
            found: list[int] = []
            for a, b, c in new.tolist():
                any_edge = False
                for p, q in ((a, b), (b, c), (c, a)):
                    key = (p << 32 | q) if p < q else (q << 32 | p)
                    bad = edge_bad.get(key)
                    if bad is None:
                        up, vp = uv_list[p]
                        uq, vq = uv_list[q]
                        xp, xq = xyz[p], xyz[q]
                        sm = value((up + uq) / 2, (vp + vq) / 2)
                        dx = sm[0] - (xp[0] + xq[0]) / 2
                        dy = sm[1] - (xp[1] + xq[1]) / 2
                        dz = sm[2] - (xp[2] + xq[2]) / 2
                        np_, nq = nrm[p], nrm[q]
                        bad = edge_bad[key] = (sqrt(dx * dx + dy * dy + dz * dz) > chord_limit
                                               or np_[0] * nq[0] + np_[1] * nq[1] + np_[2] * nq[2] < angle_cos)
                    if bad:
                        found.append(key)
                        any_edge = True
                if not any_edge:
                    mark = _triangle_mark(a, b, c, uv_list, xyz, nrm, value, chord_limit, angle_cos)
                    if mark is not None:
                        found.append(mark)
            marked_keys = np.array(found, np.int64)
        marked = np.unique(marked_keys)
        if not len(marked):
            break
        mesh, fresh, midpoints = _split(mesh, marked, len(us))
        p, q = midpoints[:, 0], midpoints[:, 1]
        mu, mv = (us[p] + us[q]) / 2, (vs[p] + vs[q]) / 2
        if len(mu) >= _ARRAY_ROUND:
            made, turned = surface.values(mu, mv), surface.normals(mu, mv, uv_box)
        else:
            made = np.array([value(u, v) for u, v in zip(mu.tolist(), mv.tolist())], np.float64).reshape(-1, 3)
            turned = np.array([surface.normal(u, v, uv_box) for u, v in zip(mu.tolist(), mv.tolist())],
                              np.float64).reshape(-1, 3)
        us, vs = np.concatenate((us, mu)), np.concatenate((vs, mv))
        points, normals = np.concatenate((points, made)), np.concatenate((normals, turned))
        xyz.extend(made.tolist())
        nrm.extend(turned.tolist())
        uv_list.extend(np.stack((mu, mv), axis=1).tolist())
    uv_verts = uv_list
    triangles = mesh.reshape(-1).tolist()

    # Every boundary vertex pinned onto its shared edge polyline: loop points, cell-clip points and
    # refinement midpoints all lie on a sampled trim segment in uv, so a proximity pass over the
    # segment index finds them. Their positions come from ``_edge_point_at``, the one function both
    # faces of an edge use.
    boundary: dict[int, list[list]] = {}
    if shared_edges is not None:
        _pin_boundary(grid, us, vs, xyz, boundary, shared_edges)

    raw = _Raw()
    raw.uv_verts, raw.xyz, raw.nrm, raw.triangles, raw.grid = uv_verts, xyz, nrm, triangles, grid
    raw.boundary, raw.loops, raw.singular_grid, raw.minted = boundary, loops, singular_grid, set()
    return raw


def _triangle_mark(a: int, b: int, c: int, uv_verts, xyz, nrm, value, chord_limit: float,
                   angle_cos: float) -> int | None:
    """``_triangle_marks`` for one triangle: the key of the edge it marks, or None."""
    ua, va = uv_verts[a]
    ub, vb = uv_verts[b]
    uc, vc = uv_verts[c]
    sc = value((ua + ub + uc) / 3, (va + vb + vc) / 3)
    pa, pb, pc = xyz[a], xyz[b], xyz[c]
    fx = (pa[0] + pb[0] + pc[0]) / 3
    fy = (pa[1] + pb[1] + pc[1]) / 3
    fz = (pa[2] + pb[2] + pc[2]) / 3
    e1x, e1y, e1z = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
    e2x, e2y, e2z = pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]
    f0 = e1y * e2z - e1z * e2y
    f1 = e1z * e2x - e1x * e2z
    f2 = e1x * e2y - e1y * e2x
    facet = math.sqrt(f0 * f0 + f1 * f1 + f2 * f2)
    n = nrm[a]
    misaligned = facet > 1e-30 and abs((f0 * n[0] + f1 * n[1] + f2 * n[2]) / facet) < angle_cos
    dx, dy, dz = sc[0] - fx, sc[1] - fy, sc[2] - fz
    if not misaligned and math.sqrt(dx * dx + dy * dy + dz * dz) <= chord_limit:
        return None
    longest, longest_sq = (a, b), -1.0
    for p, q in ((a, b), (b, c), (c, a)):
        du = uv_verts[p][0] - uv_verts[q][0]
        dv = uv_verts[p][1] - uv_verts[q][1]
        sq = du * du + dv * dv
        if sq > longest_sq:
            longest_sq, longest = sq, (p, q)
    p, q = longest
    return (p << 32 | q) if p < q else (q << 32 | p)


def _triangle_marks(triangles: np.ndarray, us: np.ndarray, vs: np.ndarray, points: np.ndarray, normals: np.ndarray,
                    surface: _Surface, chord_limit: float, angle_cos: float) -> np.ndarray:
    """The edges triangles with no edge past the tolerance mark (keys): each one's longest uv edge
    where its centre strays from the surface, or where its facet tilts off the surface's normal (a
    cap of near-collinear uv points bridging the surface as a chord)."""
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    centre = surface.values((us[a] + us[b] + us[c]) / 3, (vs[a] + vs[b] + vs[c]) / 3)
    fx = (points[a, 0] + points[b, 0] + points[c, 0]) / 3
    fy = (points[a, 1] + points[b, 1] + points[c, 1]) / 3
    fz = (points[a, 2] + points[b, 2] + points[c, 2]) / 3
    e1x, e1y, e1z = points[b, 0] - points[a, 0], points[b, 1] - points[a, 1], points[b, 2] - points[a, 2]
    e2x, e2y, e2z = points[c, 0] - points[a, 0], points[c, 1] - points[a, 1], points[c, 2] - points[a, 2]
    f0 = e1y * e2z - e1z * e2y
    f1 = e1z * e2x - e1x * e2z
    f2 = e1x * e2y - e1y * e2x
    facet = np.sqrt(f0 * f0 + f1 * f1 + f2 * f2)
    with np.errstate(divide="ignore", invalid="ignore"):
        tilt = np.abs((f0 * normals[a, 0] + f1 * normals[a, 1] + f2 * normals[a, 2]) / facet)
    dx, dy, dz = centre[:, 0] - fx, centre[:, 1] - fy, centre[:, 2] - fz
    hit = ((facet > 1e-30) & (tilt < angle_cos)) | ~(np.sqrt(dx * dx + dy * dy + dz * dz) <= chord_limit)
    a, b, c = a[hit], b[hit], c[hit]
    sides = []
    for p, q in ((a, b), (b, c), (c, a)):
        du, dv = us[p] - us[q], vs[p] - vs[q]
        sides.append(du * du + dv * dv)
    # The first longest, as a strictly-longer scan keeps it; a side of no length (NaN) is never one.
    longest = np.argmax(np.nan_to_num(np.stack(sides, axis=1), nan=-np.inf), axis=1)
    first = np.choose(longest, (a, b, c))
    second = np.choose(longest, (b, c, a))
    return (np.minimum(first, second) << 32) | np.maximum(first, second)


def _refine_interior_post_conform(face: _Face, raw: _Raw, chord: float) -> None:
    """One more refinement over the fans conformity made, never splitting a boundary edge (its
    vertices are conformity's): born after the main refinement, they would otherwise stay flat
    across a curved surface."""
    if raw.singular_grid or not raw.minted:
        return
    uv_verts, xyz, nrm, boundary, minted = raw.uv_verts, raw.xyz, raw.nrm, raw.boundary, raw.minted
    triangles = raw.triangles
    origin = xyz[0]
    scale = 0.0
    for p in xyz:
        scale = max(scale, _distance(p, origin))
    chord_limit = chord * max(scale, 1e-9)
    value = face.surface.value
    normal = face.surface.normal
    uv_box = face.uv
    # As in the main refinement, a triangle a round leaves whole marks nothing after it.
    mesh = np.asarray(triangles, np.int64).reshape(-1, 3)
    fresh = np.ones(len(mesh), bool)
    for _depth in range(min(3, _MAX_REFINE_DEPTH)):
        marked: set[int] = set()
        for a, b, c in mesh[fresh].tolist():
            # Only the fans conformity made: the rest was refined by the main pass.
            if a not in minted and b not in minted and c not in minted:
                continue
            for p, q in ((a, b), (b, c), (c, a)):
                if p in boundary and q in boundary:
                    continue  # conformity's
                mu = (uv_verts[p][0] + uv_verts[q][0]) / 2
                mv = (uv_verts[p][1] + uv_verts[q][1]) / 2
                sm = value(mu, mv)
                if not math.isfinite(sm[0]):
                    continue
                xp, xq = xyz[p], xyz[q]
                dx = sm[0] - (xp[0] + xq[0]) / 2
                dy = sm[1] - (xp[1] + xq[1]) / 2
                dz = sm[2] - (xp[2] + xq[2]) / 2
                if math.sqrt(dx * dx + dy * dy + dz * dz) > chord_limit:
                    marked.add((p << 32 | q) if p < q else (q << 32 | p))
        if not marked:
            break
        mesh, fresh, midpoints = _split(mesh, np.array(sorted(marked), np.int64), len(uv_verts))
        for p, q in midpoints.tolist():
            mu = (uv_verts[p][0] + uv_verts[q][0]) / 2
            mv = (uv_verts[p][1] + uv_verts[q][1]) / 2
            uv_verts.append([mu, mv])
            xyz.append(value(mu, mv))
            nrm.append(normal(mu, mv, uv_box))
    raw.triangles = mesh.reshape(-1).tolist()


def _seam_image_predicate(face: _Face, uv_verts):
    """Whether two vertices at one position are the two images of a periodic face's seam, which
    must both survive: a seam pair spans the whole uv box in the wrapped direction, a duplicate
    lies strictly inside it."""
    u0, u1, v0, v1 = face.uv
    u_span = u1 - u0
    v_span = v1 - v0
    full_span = 1 - 1e-9

    def predicate(i: int, j: int) -> bool:
        return ((u_span > 0 and abs(uv_verts[i][0] - uv_verts[j][0]) >= u_span * full_span)
                or (v_span > 0 and abs(uv_verts[i][1] - uv_verts[j][1]) >= v_span * full_span))

    return predicate


def _compact_collapsed_triangles(triangles: list[int], xyz, remap: dict[int, int]) -> list[int]:
    """The weld's remap applied, each triangle two of whose corners are one point dropped: the
    same index, or the same exact position (a seam's two images)."""
    if not triangles:
        return triangles
    corners = np.asarray(triangles, dtype=np.int64).reshape(-1, 3)
    if remap:
        lookup = np.arange(len(xyz), dtype=np.int64)
        lookup[np.fromiter(remap.keys(), np.int64, len(remap))] = np.fromiter(remap.values(), np.int64, len(remap))
        corners = lookup[corners]
    points = np.asarray(xyz, dtype=np.float64)
    a, b, c = (points[corners[:, k]] for k in range(3))
    collapsed = ((corners[:, 0] == corners[:, 1]) | (corners[:, 1] == corners[:, 2]) | (corners[:, 2] == corners[:, 0])
                 | (a == b).all(axis=1) | (b == c).all(axis=1) | (c == a).all(axis=1))
    return corners[~collapsed].reshape(-1).tolist()


def _drop_float32_collapsed(triangles: list[int], xyz) -> list[int]:
    """The same rule at the precision the mesh is written in: two vertices one float32 point."""
    if not triangles:
        return triangles
    single = np.asarray(xyz, dtype=np.float64).astype(np.float32)
    corners = np.asarray(triangles, dtype=np.int64).reshape(-1, 3)
    a, b, c = (single[corners[:, k]] for k in range(3))
    collapsed = ((corners[:, 0] == corners[:, 1]) | (corners[:, 1] == corners[:, 2]) | (corners[:, 2] == corners[:, 0])
                 | (a == b).all(axis=1) | (b == c).all(axis=1) | (c == a).all(axis=1))
    return corners[~collapsed].reshape(-1).tolist()


def _fold_welded_labels(boundary: dict[int, list[list]], remap: dict[int, int]) -> None:
    """A welded vertex is the point it folds into: the survivor inherits every model edge it lay on."""
    for vertex, target in remap.items():
        labels = boundary.get(vertex)
        target_labels = boundary.get(target)
        if labels is not None and target_labels is not None:
            for label in labels:
                if not any(existing[0] == label[0] and existing[1] == label[1] for existing in target_labels):
                    target_labels.append(label)
        boundary.pop(vertex, None)


def _weld_by_position(face: _Face, raw: _Raw) -> None:
    """A face's boundary vertices at one exact position are one vertex, a seam's two images apart."""
    is_seam_image = _seam_image_predicate(face, raw.uv_verts)
    by_position: dict[tuple[float, float, float], list[int]] = {}
    remap: dict[int, int] = {}
    for vertex in raw.boundary:
        p = raw.xyz[vertex]
        key = (p[0], p[1], p[2])
        bucket = by_position.get(key)
        if bucket is None:
            by_position[key] = [vertex]
            continue
        target = next((candidate for candidate in bucket if not is_seam_image(candidate, vertex)), None)
        if target is not None:
            remap[vertex] = target
        else:
            bucket.append(vertex)
    # A seam-spanning triangle collapses on position alone: dropped even where nothing welded.
    raw.triangles = _compact_collapsed_triangles(raw.triangles, raw.xyz, remap)
    _fold_welded_labels(raw.boundary, remap)


def _conform_boundaries(raw_faces: list[tuple[_Face, _Raw]], shared_edges: dict, merge_tolerance: float) -> None:
    """Every face's boundary mesh edges fan-split to the UNION of the boundary fractions all faces
    hold on each model edge: a vertex on one side of an edge is on the other side too, at the same
    floats, so no T-junction is left."""

    def fraction_eps(ord_: int) -> float:
        # Source precision, never the display tolerance: points a face resolved stay resolved.
        shared = shared_edges.get(ord_)
        spatial = (merge_tolerance * 0.5) / shared.length if (shared is not None and shared.length) else 0
        return min(0.25, max(1e-9, spatial))

    def canonical_fraction(ord_: int, f: float) -> float:
        # A closed edge wraps: within eps of 1 is its seam, 0.
        shared = shared_edges.get(ord_)
        eps = fraction_eps(ord_)
        if f <= eps:
            return 0
        if f >= 1 - eps:
            return 0 if (shared is not None and shared.closed) else 1
        return f

    fractions_by_ord: dict[int, list[float]] = {}
    for _face, raw in raw_faces:
        for labels in raw.boundary.values():
            for ord_, f in labels:
                fractions_by_ord.setdefault(ord_, []).append(canonical_fraction(ord_, f))
    for ord_, values in fractions_by_ord.items():
        eps = fraction_eps(ord_)
        values.sort()
        unique: list[float] = []
        for f in values:
            if not unique or f - unique[-1] > eps:
                unique.append(f)
        # A closed edge's last fraction may have crept within eps of 1, which is 0.
        shared = shared_edges.get(ord_)
        if shared is not None and shared.closed and len(unique) > 1 and 1 - unique[-1] <= eps:
            unique.pop()
        fractions_by_ord[ord_] = unique

    def representative_of(ord_: int, f: float) -> float:
        union = fractions_by_ord.get(ord_)
        if union is None:
            return f
        lo = bisect_left(union, f)
        lo = min(lo, len(union) - 1)
        below = union[lo - 1] if lo > 0 else union[lo]
        return union[lo] if abs(union[lo] - f) <= abs(below - f) else below

    def planar_loops(face: _Face, raw: _Raw):
        """A planar face's trim loops through every union point of its edges, in the plane's own
        coordinates, with each vertex's model point and labels."""
        origin, xdir, ydir = face.surface.origin, face.surface.xdir, face.surface.ydir
        vertices: dict[tuple[float, float], dict] = {}
        loops = []
        for loop in raw.loops:
            points = _Ring()
            ords: list[int] = []
            metas: list[_Meta | None] = []
            for i in range(len(loop)):
                meta = loop.metas[i]
                shared = shared_edges.get(meta.ord) if meta is not None else None
                if shared is None:
                    points.append(loop[i])
                    ords.append(loop.ords[i])
                    metas.append(None)
                    continue

                def canonical(f: float, _ord=meta.ord) -> float:
                    return representative_of(_ord, canonical_fraction(_ord, f))

                def unwrap(f: float, original: float, _shared=shared) -> float:
                    return 1 if (_shared.closed and f == 0 and original > 0.5) else f

                start = unwrap(canonical(meta.f0), meta.f0)
                end = unwrap(canonical(meta.f1), meta.f1)
                eps = fraction_eps(meta.ord)
                between = [f for f in fractions_by_ord.get(meta.ord, ())
                           if min(start, end) + eps < f < max(start, end) - eps]
                if end < start:
                    between.reverse()
                fractions = [start, *between, end]
                for j in range(len(fractions)):
                    if j + 1 < len(fractions) and abs(fractions[j + 1] - fractions[j]) <= eps:
                        continue
                    f = canonical_fraction(meta.ord, fractions[j])
                    xyz = _edge_point_at(shared, f)
                    delta = (xyz[0] - origin[0], xyz[1] - origin[1], xyz[2] - origin[2])
                    uv = [delta[0] * xdir[0] + delta[1] * xdir[1] + delta[2] * xdir[2],
                          delta[0] * ydir[0] + delta[1] * ydir[1] + delta[2] * ydir[2]]
                    if j + 1 < len(fractions):
                        points.append(uv)
                        ords.append(meta.ord)
                        metas.append(_Meta(meta.ord, fractions[j], fractions[j + 1]))
                    key = (uv[0], uv[1])
                    vertex = vertices.get(key)
                    if vertex is not None:
                        vertex["labels"].append([meta.ord, f])
                    else:
                        vertices[key] = {"xyz": xyz, "labels": [[meta.ord, f]]}
            points.ords = ords
            points.metas = metas
            loops.append(points)
        return loops, vertices

    # A planar face's loops must not cross themselves, or its re-triangulation cannot fill them:
    # each crossing segment is split at its middle, on the exact curve, until none crosses, the new
    # points joining the union before any face takes it.
    for face, raw in raw_faces:
        if face.surface.kind != "plane":
            continue
        for _round in range(_MAX_REFINE_DEPTH):
            split = False
            for meta in _crossing_segments(planar_loops(face, raw)[0]):
                union = fractions_by_ord.get(meta.ord)
                if union is None:
                    continue
                lo = min(meta.f0, meta.f1)
                hi = max(meta.f0, meta.f1)
                # Far enough from both ends that no label's representative changes.
                if hi - lo <= fraction_eps(meta.ord) * 4:
                    continue
                mid = (lo + hi) / 2
                at = 0
                while at < len(union) and union[at] < mid:
                    at += 1
                union.insert(at, mid)
                split = True
            if not split:
                break

    for face, raw in raw_faces:
        if face.surface.kind == "plane":
            # A new point on a curved trim can cross an old triangle's diagonal: the planar region
            # is triangulated again from its final exact trim points instead (planar uv is an
            # exact inverse).
            loops, vertices = planar_loops(face, raw)
            built = _grid_triangulate(face, loops, math.inf)
            if built is not None and built.triangles:
                raw.uv_verts, raw.triangles, raw.grid, raw.loops = built.uv_verts, built.triangles, built, loops
                raw.boundary = {}
                xyz = []
                for i, (u, v) in enumerate(built.uv_verts):
                    pinned = vertices.get((u, v))
                    if pinned is not None:
                        raw.boundary[i] = pinned["labels"]
                        xyz.append(pinned["xyz"])
                    else:
                        xyz.append(face.surface.value(u, v))
                raw.xyz = xyz
                raw.nrm = [face.surface.normal(u, v, face.uv) for u, v in built.uv_verts]
        for vertex, labels in raw.boundary.items():
            for label in labels:
                label[1] = representative_of(label[0], canonical_fraction(label[0], label[1]))
            # The lowest (ord, f) label places the vertex, so two vertices at one model point
            # (in a face or across faces) take one position whichever edge pinned each.
            labels.sort(key=lambda label: (label[0], label[1]))
            ord_, f = labels[0]
            shared = shared_edges.get(ord_)
            if shared is not None:
                raw.xyz[vertex] = _edge_point_at(shared, f)
        # Snapping can land two boundary vertices on one point: welded, collapsed triangles dropped.
        _weld_by_position(face, raw)

    for face, raw in raw_faces:
        uv_verts, xyz, nrm, boundary = raw.uv_verts, raw.xyz, raw.nrm, raw.boundary
        if not boundary:
            continue
        # Only a region-boundary mesh edge (one triangle uses it) may be split: a diagonal that
        # merely joins two pinned vertices is a chord across the face.
        edge_use: dict[tuple[int, int], int] = {}
        triangles = raw.triangles
        for t in range(0, len(triangles), 3):
            a, b, c = triangles[t], triangles[t + 1], triangles[t + 2]
            for p, q in ((a, b), (b, c), (c, a)):
                key = (p, q) if p < q else (q, p)
                edge_use[key] = edge_use.get(key, 0) + 1
        surface = face.surface
        minted_ids: dict[tuple, int] = {}

        def vertex_at(ord_: int, f: float, a: int, b: int, w: float) -> int:
            # The pair is part of the key: a seam edge appears twice on one face, and side A's
            # vertex with side A's uv inside side B's triangles would evaluate on the wrong side.
            key = (ord_, f"{f:.12f}", min(a, b), max(a, b))
            found = minted_ids.get(key)
            if found is not None:
                return found
            uv = [uv_verts[b][0] if _singular_u(surface, uv_verts[a][1]) else
                  uv_verts[a][0] if _singular_u(surface, uv_verts[b][1]) else
                  uv_verts[a][0] + w * (uv_verts[b][0] - uv_verts[a][0]),
                  uv_verts[a][1] + w * (uv_verts[b][1] - uv_verts[a][1])]
            found = len(uv_verts)
            uv_verts.append(uv)
            xyz.append(_edge_point_at(shared_edges[ord_], f))
            nrm.append(surface.normal(uv[0], uv[1], face.uv))
            boundary[found] = [[ord_, f]]
            raw.minted.add(found)
            minted_ids[key] = found
            return found

        own_fractions: dict[int, list[float]] = {}
        for labels in boundary.values():
            for ord_, f in labels:
                own_fractions.setdefault(ord_, []).append(f)

        def holds_vertex_between(ord_: int, fp: float, fq: float) -> bool:
            own = own_fractions.get(ord_, [])
            eps = fraction_eps(ord_)
            shared = shared_edges.get(ord_)
            if shared is not None and shared.closed:
                forward = (fq - fp + 1) % 1
                start = fp if forward <= 0.5 else fq
                arc = forward if forward <= 0.5 else (fp - fq + 1) % 1
                return any(eps < (f - start + 1) % 1 < arc - eps for f in own)
            return any(min(fp, fq) + eps < f < max(fp, fq) - eps for f in own)

        def inserts_for(p: int, q: int):
            labels_p = boundary.get(p)
            labels_q = boundary.get(q)
            if labels_p is None or labels_q is None:
                return None
            if edge_use.get((p, q) if p < q else (q, p)) != 1:
                return None
            common = []
            for lp in labels_p:
                lq = next((label for label in labels_q if label[0] == lp[0]), None)
                if lq is not None:
                    common.append((lp, lq))
            if not common:
                return None
            # A face bounded by two model edges (an arc and its chord) has both corners on both:
            # the mesh edge runs along the one this face has no point between them on.
            if len(common) == 1:
                bp, bq = common[0]
            else:
                bp, bq = next(((lp, lq) for lp, lq in common if not holds_vertex_between(lp[0], lp[1], lq[1])),
                              common[0])
            union = fractions_by_ord.get(bp[0])
            if union is None:
                return None
            shared = shared_edges.get(bp[0])
            eps = fraction_eps(bp[0])
            between = []
            if shared is not None and shared.closed:
                # Fractions wrap on a closed edge: the SHORT arc from p to q.
                forward = (bq[1] - bp[1] + 1) % 1
                use_forward = forward <= 0.5
                start = bp[1] if use_forward else bq[1]
                arc = forward if use_forward else (bp[1] - bq[1] + 1) % 1
                if arc <= eps * 2:
                    return None
                for f in union:
                    d = (f - start + 1) % 1
                    if eps < d < arc - eps:
                        between.append((d / arc if use_forward else 1 - d / arc, f))
            else:
                lo = min(bp[1], bq[1])
                hi = max(bp[1], bq[1])
                if hi - lo <= eps * 2:
                    return None
                for f in union:
                    if lo + eps < f < hi - eps:
                        between.append(((f - bp[1]) / (bq[1] - bp[1]), f))
            if not between:
                return None
            between.sort(key=lambda entry: entry[0])
            return bp[0], between

        out: list[int] = []

        def emit(a: int, b: int, c: int, depth: int) -> None:
            if depth > 24:
                out.extend((a, b, c))
                return
            for p, q, r in ((a, b, c), (b, c, a), (c, a, b)):
                found = inserts_for(p, q)
                if found is not None:
                    ord_, between = found
                    previous = p
                    for chord_param, f in between:
                        m = vertex_at(ord_, f, p, q, chord_param)
                        emit(previous, m, r, depth + 1)
                        previous = m
                    emit(previous, q, r, depth + 1)
                    return
            out.extend((a, b, c))

        for t in range(0, len(triangles), 3):
            a, b, c = triangles[t], triangles[t + 1], triangles[t + 2]
            # Only an edge with both ends on the boundary is ever split.
            if (a in boundary) + (b in boundary) + (c in boundary) >= 2:
                emit(a, b, c, 0)
            else:
                out.extend((a, b, c))
        raw.triangles = out
        # Splitting can mint a vertex where a boundary vertex already is (one model point reached
        # through another edge): a second weld by exact position.
        _weld_by_position(face, raw)


def _finalize_face_mesh(face: _Face, raw: _Raw) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The face's float32 positions and normals (the surface's, turned with the face) and its
    triangles, wound the face's way."""
    positions = np.asarray(raw.xyz, dtype=np.float64).astype(np.float32).reshape(-1, 3)
    normals = np.asarray(raw.nrm, dtype=np.float64).reshape(-1, 3)
    if face.reversed:
        normals = -normals
    triangles = np.asarray(raw.triangles, dtype=np.uint32).reshape(-1, 3)
    if face.reversed:
        triangles = triangles[:, [0, 2, 1]]
    return positions, normals.astype(np.float32), np.ascontiguousarray(triangles)


def _tessellate_component(faces: list[_Face], edges: list[_Curve | None], chord: float, angle: float):
    """The component's mesh: each face's (positions, normals, triangles) by ordinal, each
    edge's polyline by ordinal, the component's scale, and the faces too narrow to resolve."""
    low = [math.inf, math.inf, math.inf]
    high = [-math.inf, -math.inf, -math.inf]

    def grow(p) -> None:
        for d in range(3):
            if p[d] < low[d]:
                low[d] = p[d]
            if p[d] > high[d]:
                high[d] = p[d]

    # The component's scale: a box from each pcurve's ends and middle.
    for face in faces:
        for loop in face.loops:
            for pcurve in loop:
                t0, t1 = pcurve.range
                for t in (t0, (t0 + t1) / 2, t1):
                    u, v = pcurve.value(t)
                    grow(face.surface.value(u, v))
    # A component with no faces is measured by its edges' curves.
    if not low[0] <= high[0]:
        for curve in edges:
            if curve is None:
                continue
            t0, t1 = curve.range
            for t in (t0, (t0 + t1) / 2, t1):
                grow(curve.value(t))
    dx, dy, dz = high[0] - low[0], high[1] - low[1], high[2] - low[2]
    extent = math.sqrt(dx * dx + dy * dy + dz * dz)
    scale = max(extent, _SCALE_FLOOR) if math.isfinite(extent) else _SCALE_FLOOR

    # One polyline per model edge, sampled from its exact curve at the chord tolerance: both
    # adjacent faces conform to it, and it is the edge drawn.
    shared_edges: dict[int, _Shared] = {}
    for ordinal, curve in enumerate(edges, 1):
        if curve is not None:
            shared_edges[ordinal] = _sample_shared_edge(curve, chord * scale)
    # Two points closer than this are one point of the model evaluated twice: the only distance
    # anything welds at.
    weld_tolerance = scale * 2 ** -21
    corner = _weld_corners(shared_edges, weld_tolerance)
    for face in faces:
        face.poles = [(axis, at, lo, hi, corner(point)) for axis, at, lo, hi, point in face.poles]

    raw_faces = []
    unresolved: set[int] = set()
    for face in faces:
        try:
            raw = _tessellate_face_raw(face, scale, chord, angle, shared_edges)
        except _Unresolved:
            unresolved.add(face.ord)
            continue
        except Exception:  # noqa: BLE001 - a face its geometry defeats is left out, and named
            raw = None
        if raw is not None:
            raw_faces.append((face, raw))
    _conform_boundaries(raw_faces, shared_edges, weld_tolerance)
    meshes = {}
    for face, raw in raw_faces:
        _refine_interior_post_conform(face, raw, chord)
        if face.poles:
            _snap_poles(face, raw, weld_tolerance)
        raw.triangles = _drop_float32_collapsed(raw.triangles, raw.xyz)
        meshes[face.ord] = _finalize_face_mesh(face, raw)
    polylines = {ordinal: shared.points for ordinal, shared in shared_edges.items()}
    return meshes, polylines, scale, unresolved


def _weld_corners(shared_edges: dict[int, _Shared], weld_tolerance: float):
    """Edge ends that coincide are one corner of the model, which each curve evaluates with its
    own rounding: each cluster becomes ONE point object, so a corner is the same floats whichever
    edge a face pinned it through. The first corner within the tolerance wins, as a scan in order
    would find it. Returns the function that names a point's corner, for the poles."""
    corners: list[list[float]] = []
    cells: dict[tuple[int, int, int], list[int]] = {}
    size = weld_tolerance if weld_tolerance > 0 else 1.0

    def cell(point) -> tuple[int, int, int]:
        return (math.floor(point[0] / size), math.floor(point[1] / size), math.floor(point[2] / size))

    def canonical_corner(point: list[float]) -> list[float]:
        cx, cy, cz = cell(point)
        best = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for index in cells.get((cx + dx, cy + dy, cz + dz), ()):
                        if (best is None or index < best) and _distance(corners[index], point) <= weld_tolerance:
                            best = index
        if best is not None:
            return corners[best]
        cells.setdefault((cx, cy, cz), []).append(len(corners))
        corners.append(point)
        return point

    for shared in shared_edges.values():
        points = shared.points
        if shared.closed:
            # A closed edge's seam can meet other edges' corners (a cylinder's seam and a line).
            welded = canonical_corner(points[0])
            points[0] = welded
            points[-1] = welded
            continue
        points[0] = canonical_corner(points[0])
        points[-1] = canonical_corner(points[-1])
        # Ends that weld into one corner make the edge closed, though its curve's ends miss by
        # more than the closure test allowed; a curve no longer than the weld distance stays open.
        if (shared.curve.kind != "line" and points[0] is points[-1]
                and any(_distance(point, points[0]) > weld_tolerance * 2 ** 10 for point in points)):
            shared.closed = True
    return canonical_corner


def _snap_poles(face: _Face, raw: _Raw, tolerance: float) -> None:
    """Every vertex on a pole put at the pole's one point. OCCT evaluates a collapsed row of a
    freeform surface with its own rounding at each parameter, and a hair off zero is a distinct
    float: each pole would be a cluster of vertices, and the triangles between them slivers on
    no surface. A vertex is on the pole where its parameters lie on the pole's stretch of them and
    its point within ``tolerance`` of the pole's."""
    for axis, at, lo, hi, point in face.poles:
        epsilons = [max(abs(face.uv[d * 2]), abs(face.uv[d * 2 + 1]), face.uv[d * 2 + 1] - face.uv[d * 2], 1e-12)
                    * 2 ** -23 for d in range(2)]
        for index, uv in enumerate(raw.uv_verts):
            if (abs(uv[axis] - at) <= epsilons[axis] and lo - epsilons[1 - axis] <= uv[1 - axis] <= hi + epsilons[1 - axis]
                    and _distance(raw.xyz[index], point) <= tolerance):
                raw.xyz[index] = point


# --- The component's body ------------------------------------------------------------------


def _maps(topods):
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape

    faces, edges = TopTools_IndexedMapOfShape(), TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(topods, TopAbs_FACE, faces)
    TopExp.MapShapes_s(topods, TopAbs_EDGE, edges)
    return faces, edges


def _rows(table: dict, kind: str, *names: str) -> list[tuple]:
    """``(ordinal, *names)`` of each of the table's ``kind`` rows (``faces`` or ``edges``)."""
    columns = table["tables"][f"{kind[:-1]}Columns"]
    at = [columns.index(name) for name in ("ordinal", *names)]
    return sorted(tuple(row[index] for index in at) for row in table[kind])


def mesh_component(topods, table: dict, face_colors: dict, *, part: str, chord: float, angle: float) -> bytes:
    """Mesh one unlocated component and return its GLB body, which names ``part``.

    ``table`` is the component's selector table (its faces' areas, its edges' classes) and
    ``face_colors`` its intrinsic face colours, by the same ordinals; ``chord`` is relative to the
    component's extent and ``angle`` in radians. A failure inside OCCT is the component's
    ``MeshProductionError``.
    """
    try:
        return _mesh_component(topods, table, face_colors, part=part, chord=chord, angle=angle)
    except MeshProductionError:
        raise
    except Exception as error:  # noqa: BLE001 - OCCT's own, told apart below
        # OCP raises OCCT's exception classes, whose Python bases differ between platform wheels:
        # one is known by the module its class comes from.
        if not type(error).__module__.startswith("OCP"):
            raise
        raise MeshProductionError(f"OCCT failed on the component: {type(error).__name__}: {error}") from error


def _mesh_component(topods, table: dict, face_colors: dict, *, part: str, chord: float, angle: float) -> bytes:
    from cadgen.store.meshes import encode_payload

    face_map, edge_map = _maps(topods)
    table_faces, table_edges = _rows(table, "faces", "area"), _rows(table, "edges", "visibilityClass")
    if len(table_faces) != face_map.Extent() or len(table_edges) != edge_map.Extent():
        raise MeshProductionError(
            f"the component's BREP has {face_map.Extent()} faces and {edge_map.Extent()} edges, "
            f"its selector table {len(table_faces)} and {len(table_edges)}"
        )
    faces, edges = _faces_and_edges(topods, face_map, edge_map)
    meshes, polylines, scale, unresolved = _tessellate_component(faces, edges, chord, angle)
    # A face too small for the mesh to resolve -- its area under the square of the chord, inside
    # the error every triangle may carry, or its trim narrower than the tolerance it is sampled
    # to -- may come out empty without being named.
    deflection = chord * scale
    unmeshed = [ordinal for ordinal, area in table_faces
                if ordinal not in unresolved and float(area or 0.0) >= deflection * deflection
                and not len(meshes.get(ordinal, ((), (), ()))[2])]

    position_parts, normal_parts, index_parts, face_ranges = [], [], [], []
    vertex_base = index_start = 0
    empty = (np.zeros((0, 3), np.float32), np.zeros((0, 3), np.float32), np.zeros((0, 3), np.uint32))
    for ordinal, _area in table_faces:
        positions, normals, triangles = meshes.get(ordinal, empty)
        if not len(triangles):
            positions, normals, triangles = empty
        position_parts.append(positions)
        normal_parts.append(normals)
        index_parts.append(triangles.reshape(-1) + np.uint32(vertex_base))
        color = face_colors.get(ordinal)
        face_ranges.append({"ord": ordinal, "color": [float(c) for c in color] if color else None,
                            "indexStart": index_start, "indexCount": int(triangles.size)})
        vertex_base += len(positions)
        index_start += int(triangles.size)
    positions = np.concatenate(position_parts).astype("<f4") if position_parts else np.zeros((0, 3), "<f4")
    normals = np.concatenate(normal_parts).astype("<f4") if normal_parts else np.zeros((0, 3), "<f4")
    indices = np.concatenate(index_parts).astype("<u4") if index_parts else np.zeros(0, "<u4")

    edges_out = []
    for ordinal, visibility in table_edges:
        points = polylines.get(ordinal)
        if points is not None and len(points) >= 2:
            edges_out.append((ordinal, str(visibility or "none"), np.asarray(points, dtype=np.float64).astype("<f4")))
    if len(positions):
        low, high = positions.min(axis=0), positions.max(axis=0)
    elif edges_out:
        stacked = np.concatenate([polyline for _, _, polyline in edges_out])
        low, high = stacked.min(axis=0), stacked.max(axis=0)
    else:
        low = high = np.zeros(3, np.float32)
    bounds = {"min": [float(v) for v in low], "max": [float(v) for v in high]}
    return encode_payload(part=part, positions=positions, normals=normals, indices=indices, face_ranges=face_ranges,
                          edges=edges_out, bounds=bounds, unmeshed_faces=unmeshed)
