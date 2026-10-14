"""One component's display mesh, made as the CAD Viewer made it before meshes moved into the store.

This is the tessellator cadgen 0.7.21 ran in the browser (its tessellation version 9), function for
function: the same sampling, grid, earcut (mapbox/earcut 3.0.2, the copy three.js ships), sliver
flips, refinement, edge pinning and cross-face conformity, at the same tolerances, so a part is
drawn as it was drawn there. It runs as cadgen's native library, written in C (its sources are
``native/tessellate`` in cadgen's source tree; ``_runtime/native`` holds one build per platform,
loaded here with ctypes, which runs it with the GIL released); this module reads the component's
geometry through OCCT into the flat tables the library takes, evaluates for it any surface it has
no formula for, and packs what it returns. Its geometry comes from OCCT, not from a container of
single-precision copies, and the library evaluates it itself: each analytic surface by its formula,
each B-spline surface, trim and edge curve by its poles and knots (de Boor's algorithm, so a clamped
end is its end pole exactly), each conic as OCCT writes it, a surface of extrusion or revolution by
its basis curve, swept; and any other surface through OCCT's own evaluation, which it asks this
module for. Each trim's pcurve and each edge's curve is read as the browser's were converted: a 2D
B-spline per pcurve, lines, circles and ellipses as they are, other edge curves as B-splines.

Where the browser's tessellator went wrong, this one does not:

- a freeform face's normals are its surface's derivatives, not central differences, which averaged
  the two sides of a closed profile's corner along its seam, and at a pole, where the derivatives
  have no cross product, the pole's own limiting normal, where the browser's was noise or nothing
  and refinement split on toward it;
- a freeform face's pole is one point, its model vertex: each row evaluated there is a hair off
  it, which left the pole a fan of slivers on no surface; and where a row evaluates exactly to that
  point, the triangles reaching it are kept until the faces are made to meet: dropped first, they
  left a T-junction along the edge into the pole;
- earcut's zero-area triangles over a straight run of trim are flipped away before what is left of
  them is dropped: dropped first, they left a T-junction on the run; and a triangle whose edge runs
  along a line of constant u or v past a vertex on it is split there: trims in double precision put
  their corners exactly in line;
- a cell whose triangles do not cover what the loops leave of it -- a hole crossing its side -- is
  cut again from its loops as a whole;
- a vertex the grid moved onto a grid line or a neighbour, by up to one source ULP, is still pinned
  to the trim it was made on, whichever cell the move took it into: the browser held a vertex to a
  trim more tightly than that and looked for the trim in the vertex's own cell alone, so one moved
  past either was never pinned, and the face beside it met it with a crack;
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

import ctypes
import math
import platform
import sys
import threading
from array import array
from typing import Any, Callable

import numpy as np

_ABI = 1  # tessellate's TESS_ABI: the tables it reads and the result it returns

# The tables' columns (api.c), and the kinds of surface and curve the library evaluates itself.
_FACE_I, _FACE_D, _PCURVE_I, _PCURVE_D, _EDGE_I, _EDGE_D, _BSURF_I, _BSURF_D, _POLE_D = 8, 20, 8, 2, 9, 16, 10, 4, 7
_SWEPT_I, _SWEPT_D = 2, 6
_ANALYTIC = {"plane": 1, "cylinder": 2, "cone": 3, "sphere": 4, "torus": 5}
_S_BSPLINE, _S_HOST, _S_EXTRUSION, _S_REVOLUTION = 6, 7, 8, 9
_C_LINE, _C_CIRCLE, _C_ELLIPSE, _C_BSPLINE, _C_HYPERBOLA, _C_PARABOLA = 1, 2, 3, 4, 5, 6
_ASK_PCURVE, _ASK_CURVE, _ASK_SURFACE, _ASK_NORMAL = 0, 1, 2, 3
_FACE_MESHED, _FACE_UNRESOLVED = 1, 2
_MAX_DEGREE = 25

_CALLBACK = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_int32, ctypes.c_int32, ctypes.c_double,
                             ctypes.c_double, ctypes.POINTER(ctypes.c_double))


class _Input(ctypes.Structure):
    _fields_ = [
        ("face_count", ctypes.c_int32), ("edge_count", ctypes.c_int32), ("pcurve_count", ctypes.c_int32),
        ("bsurface_count", ctypes.c_int32), ("swept_count", ctypes.c_int32), ("basis_count", ctypes.c_int32),
        ("chord", ctypes.c_double), ("angle", ctypes.c_double),
        ("face_i", ctypes.POINTER(ctypes.c_int32)), ("face_d", ctypes.POINTER(ctypes.c_double)),
        ("loop_i", ctypes.POINTER(ctypes.c_int32)), ("pcurve_i", ctypes.POINTER(ctypes.c_int32)),
        ("pcurve_d", ctypes.POINTER(ctypes.c_double)), ("edge_i", ctypes.POINTER(ctypes.c_int32)),
        ("edge_d", ctypes.POINTER(ctypes.c_double)), ("swept_i", ctypes.POINTER(ctypes.c_int32)),
        ("swept_d", ctypes.POINTER(ctypes.c_double)), ("basis_i", ctypes.POINTER(ctypes.c_int32)),
        ("basis_d", ctypes.POINTER(ctypes.c_double)), ("bsurface_i", ctypes.POINTER(ctypes.c_int32)),
        ("bsurface_d", ctypes.POINTER(ctypes.c_double)), ("pole_d", ctypes.POINTER(ctypes.c_double)),
        ("reals", ctypes.POINTER(ctypes.c_double)), ("callback", _CALLBACK), ("user", ctypes.c_void_p),
    ]


class _Result(ctypes.Structure):
    _fields_ = [
        ("abi", ctypes.c_int32), ("error", ctypes.c_int32), ("message", ctypes.c_char * 160),
        ("scale", ctypes.c_double), ("face_count", ctypes.c_int32), ("edge_count", ctypes.c_int32),
        ("face_state", ctypes.POINTER(ctypes.c_int32)), ("face_vertices", ctypes.POINTER(ctypes.c_int64)),
        ("face_triangles", ctypes.POINTER(ctypes.c_int64)), ("positions", ctypes.POINTER(ctypes.c_float)),
        ("normals", ctypes.POINTER(ctypes.c_float)), ("triangles", ctypes.POINTER(ctypes.c_uint32)),
        ("vertex_total", ctypes.c_int64), ("triangle_total", ctypes.c_int64),
        ("edge_points", ctypes.POINTER(ctypes.c_int64)), ("points", ctypes.POINTER(ctypes.c_double)),
        ("point_total", ctypes.c_int64),
    ]


class MeshProductionError(ValueError):
    """A component that could not be meshed."""


_LOCK = threading.Lock()
_LIBRARY: ctypes.CDLL | None = None


def _library_name() -> str:
    system = {"darwin": "macos", "win32": "windows"}.get(sys.platform, sys.platform)
    machine = platform.machine().lower()
    machine = {"amd64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
    suffix = {"macos": "dylib", "windows": "dll"}.get(system, "so")
    return f"tessellate-{system}-{machine}.{suffix}"


def _library() -> ctypes.CDLL:
    """The tessellator for this platform, loaded once."""
    global _LIBRARY
    with _LOCK:
        if _LIBRARY is None:
            from cadgen.assets import AssetMissing, runtime_build_hint, runtime_root

            path = runtime_root() / "native" / _library_name()
            if not path.is_file():
                raise AssetMissing(f"cadgen's tessellator for this platform ({path.name}) is missing, and without it "
                                   "no part can be meshed. " + runtime_build_hint(path, overridable=False))
            library = ctypes.CDLL(str(path))
            library.cadgen_tessellate_abi.restype = ctypes.c_int32
            library.cadgen_tessellate.argtypes = [ctypes.POINTER(_Input)]
            library.cadgen_tessellate.restype = ctypes.POINTER(_Result)
            library.cadgen_tessellate_free.argtypes = [ctypes.POINTER(_Result)]
            library.cadgen_tessellate_free.restype = None
            if library.cadgen_tessellate_abi() != _ABI:
                raise RuntimeError(f"cadgen's tessellator ({path}) is not the one this cadgen was built with")
            _LIBRARY = library
        return _LIBRARY


# --- The component's geometry, as the library reads it --------------------------------------


class _Model:
    """The component's faces, loops, pcurves, edges and B-spline surfaces as flat tables, and the
    host's evaluators for whatever the library evaluates through it."""

    def __init__(self, face_count: int, edge_count: int) -> None:
        self.face_i = np.zeros((face_count, _FACE_I), np.int32)
        self.face_d = np.zeros((face_count, _FACE_D), np.float64)
        self.face_i[:, 3] = -1
        self.loop_i: list[int] = []
        self.pcurve_i: list[list[int]] = []
        self.pcurve_d: list[float] = []
        self.edge_i = np.zeros((edge_count, _EDGE_I), np.int32)
        self.edge_d = np.zeros((edge_count, _EDGE_D), np.float64)
        self.bsurface_i: list[list[int]] = []
        self.bsurface_d: list[float] = []
        self.swept_i: list[list[int]] = []
        self.swept_d: list[float] = []
        self.basis_i: list[list[int]] = []
        self.basis_d: list[list[float]] = []
        self.pole_d: list[float] = []
        self.reals = array("d")  # every B-spline's knots, poles and weights
        self.pcurves: list[Callable | None] = []  # each pcurve's host evaluator, where it has one
        self.curves: list[Callable | None] = [None] * edge_count
        self.surfaces: dict[int, tuple[Callable, Callable]] = {}  # face index -> (value, normal)

    def bspline(self, curve) -> tuple[int, int, int, int, int]:
        """A B-spline curve's degree, poles, rationality and where its knots and poles are. Its
        arrays are read an element at a time: OCP iterates one fifty times slower."""
        degree, count, rational = curve.Degree(), curve.NbPoles(), int(curve.IsRational())
        reals = self.reals
        extend, knot, multiplicity, pole = reals.extend, curve.Knot, curve.Multiplicity, curve.Pole
        knots_at = len(reals)
        for k in range(1, curve.NbKnots() + 1):
            extend((knot(k),) * multiplicity(k))
        poles_at = len(reals)
        for i in range(1, count + 1):
            extend(pole(i).Coord())
        if rational:
            extend(curve.Weight(i) for i in range(1, count + 1))
        return degree, count, rational, knots_at, poles_at


def _curve_row(model: _Model, curve) -> tuple[list[int], list[float]] | None:
    """A curve the library evaluates itself with its own parameters -- a line, a conic, a B-spline (a
    periodic one over its one period, wrapping), a Bézier curve as the B-spline it is -- as a row
    of the edges' columns; None for any other (an offset curve)."""
    from OCP.Geom import (Geom_BezierCurve, Geom_BSplineCurve, Geom_Circle, Geom_Ellipse, Geom_Hyperbola, Geom_Line,
                          Geom_Parabola, Geom_TrimmedCurve)
    from OCP.GeomConvert import GeomConvert

    while isinstance(curve, Geom_TrimmedCurve):
        curve = curve.BasisCurve()
    row_i, row_d = [0] * _EDGE_I, [0.0] * _EDGE_D
    row_i[2] = -1
    if isinstance(curve, Geom_Line):
        line = curve.Lin()
        o, d = line.Location(), line.Direction()
        row_i[0] = _C_LINE
        row_d[2:5], row_d[11:14] = (o.X(), o.Y(), o.Z()), (d.X(), d.Y(), d.Z())
        return row_i, row_d
    conics = ((Geom_Circle, _C_CIRCLE, "Circ"), (Geom_Ellipse, _C_ELLIPSE, "Elips"),
              (Geom_Hyperbola, _C_HYPERBOLA, "Hypr"), (Geom_Parabola, _C_PARABOLA, "Parab"))
    for kind_class, kind, accessor in conics:
        if isinstance(curve, kind_class):
            conic = getattr(curve, accessor)()
            row_i[0] = kind
            row_d[2:11] = _frame(conic)[:9]
            if kind == _C_CIRCLE:
                row_d[14] = conic.Radius()
            elif kind == _C_PARABOLA:
                row_d[14] = conic.Focal()
            else:
                row_d[14], row_d[15] = conic.MajorRadius(), conic.MinorRadius()
            return row_i, row_d
    if isinstance(curve, Geom_BezierCurve):
        curve = GeomConvert.CurveToBSplineCurve_s(curve)
    if not isinstance(curve, Geom_BSplineCurve) or curve.Degree() > _MAX_DEGREE:
        return None
    periodic = curve.IsPeriodic()
    if periodic:
        curve = curve.Copy()
        curve.SetNotPeriodic()
    degree, count, rational, knots_at, poles_at = model.bspline(curve)
    row_i[0], row_i[2] = _C_BSPLINE, count
    row_i[3:9] = (degree, count, rational, knots_at, poles_at, int(periodic))
    row_d[0:2] = (curve.FirstParameter(), curve.LastParameter())
    return row_i, row_d


def _swept_surface(model: _Model, face) -> tuple[int, int] | None:
    """The face's surface as a surface of linear extrusion or of revolution whose basis curve the
    library evaluates, (its kind, its row in the swept table); None for any other."""
    from OCP.BRep import BRep_Tool
    from OCP.Geom import Geom_RectangularTrimmedSurface, Geom_SurfaceOfLinearExtrusion, Geom_SurfaceOfRevolution

    surface = BRep_Tool.Surface_s(face)  # in the component's frame
    while isinstance(surface, Geom_RectangularTrimmedSurface):
        surface = surface.BasisSurface()
    if isinstance(surface, Geom_SurfaceOfLinearExtrusion):
        kind, origin, direction = _S_EXTRUSION, (0.0, 0.0, 0.0), surface.Direction()
    elif isinstance(surface, Geom_SurfaceOfRevolution):
        axis = surface.Axis()
        location = axis.Location()
        kind, origin, direction = _S_REVOLUTION, (location.X(), location.Y(), location.Z()), axis.Direction()
    else:
        return None
    row = _curve_row(model, surface.BasisCurve())
    if row is None:
        return None
    model.basis_i.append(row[0])
    model.basis_d.append(row[1])
    model.swept_i.append([kind, len(model.basis_i) - 1])
    model.swept_d.extend((*origin, direction.X(), direction.Y(), direction.Z()))
    return kind, len(model.swept_i) - 1


def _frame(geometry) -> list[float]:
    frame = geometry.Position()
    return [*frame.Location().Coord(), *frame.XDirection().Coord(), *frame.YDirection().Coord(),
            *frame.Direction().Coord()]


def _host_surface(adaptor):
    """A surface the library has no formula for, evaluated through OCCT: its point, and its normal as
    the cross product of its derivatives -- where one vanishes (a pole, a collapsed edge), the
    surface's just inside the face, toward its middle, which is the pole's own limiting direction."""
    from OCP.gp import gp_Pnt, gp_Vec

    point, d_u, d_v = gp_Pnt(), gp_Vec(), gp_Vec()
    d0, d1 = adaptor.D0, adaptor.D1

    def value(u: float, v: float) -> tuple[float, float, float]:
        d0(u, v, point)
        return point.X(), point.Y(), point.Z()

    def normal(u: float, v: float, uv_box) -> tuple[float, float, float]:
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
                return nx / length, ny / length, nz / length
        return 0.0, 0.0, 0.0

    return value, normal


def _bspline_surface(model: _Model, face) -> int | None:
    """The face's surface as a B-spline the library evaluates, where it is one or converts to one
    exactly (a Bézier patch), its index in the table; None for any other."""
    from OCP.BRep import BRep_Tool
    from OCP.Geom import Geom_BezierSurface, Geom_BSplineSurface, Geom_RectangularTrimmedSurface
    from OCP.GeomConvert import GeomConvert

    surface = BRep_Tool.Surface_s(face)  # in the component's frame
    while isinstance(surface, Geom_RectangularTrimmedSurface):
        surface = surface.BasisSurface()
    if isinstance(surface, Geom_BezierSurface):
        surface = GeomConvert.SurfaceToBSplineSurface_s(surface)
    if not isinstance(surface, Geom_BSplineSurface):
        return None
    if surface.UDegree() > _MAX_DEGREE or surface.VDegree() > _MAX_DEGREE:
        return None
    periodic_u, periodic_v = surface.IsUPeriodic(), surface.IsVPeriodic()
    if periodic_u or periodic_v:
        # The same surface over one period, its knots clamped; a parameter past the period wraps.
        surface = surface.Copy()
        if periodic_u:
            surface.SetUNotPeriodic()
        if periodic_v:
            surface.SetVNotPeriodic()
    u0, u1, v0, v1 = surface.Bounds()
    rational = int(surface.IsURational() or surface.IsVRational())
    count_u, count_v = surface.NbUPoles(), surface.NbVPoles()
    reals = model.reals
    knots_u = len(reals)
    for k in range(1, surface.NbUKnots() + 1):
        reals.extend((surface.UKnot(k),) * surface.UMultiplicity(k))
    knots_v = len(reals)
    for k in range(1, surface.NbVKnots() + 1):
        reals.extend((surface.VKnot(k),) * surface.VMultiplicity(k))
    poles_at = len(reals)
    for i in range(1, count_u + 1):
        for j in range(1, count_v + 1):
            pole = surface.Pole(i, j)
            reals.extend((pole.X(), pole.Y(), pole.Z()))
    if rational:
        reals.extend(surface.Weight(i, j) for i in range(1, count_u + 1) for j in range(1, count_v + 1))
    model.bsurface_i.append([surface.UDegree(), surface.VDegree(), count_u, count_v, rational, int(periodic_u),
                             int(periodic_v), knots_u, knots_v, poles_at])
    model.bsurface_d.extend((u0, u1, v0, v1))
    return len(model.bsurface_i) - 1


def _faces(model: _Model, face_map, edge_map, *, host_all: bool) -> None:
    """Each face's surface, uv box, trim loops of pcurves and poles. A face whose surface or trim
    cannot be read has no loops, so it comes out empty, and is named."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
    from OCP.Geom import Geom_ConicalSurface, Geom_CylindricalSurface, Geom_Plane, Geom_ToroidalSurface
    from OCP.Geom2d import Geom2d_BSplineCurve, Geom2d_Circle, Geom2d_Ellipse, Geom2d_Line, Geom2d_TrimmedCurve
    from OCP.Geom2dConvert import Geom2dConvert
    from OCP.GeomAbs import GeomAbs_SurfaceType as T
    from OCP.gp import gp_Pnt2d, gp_Vec2d
    from OCP.TopAbs import TopAbs_REVERSED, TopAbs_WIRE
    from OCP.TopExp import TopExp, TopExp_Explorer
    from OCP.TopoDS import TopoDS

    analytic = {T.GeomAbs_Plane: "plane", T.GeomAbs_Cylinder: "cylinder", T.GeomAbs_Cone: "cone",
                T.GeomAbs_Sphere: "sphere", T.GeomAbs_Torus: "torus"}
    # The surfaces whose bounds BRepTools.UVBounds never clips a trim's box to: unbounded or periodic
    # in each direction (a trimmed one has bounds of its own).
    unclipped = (Geom_Plane, Geom_CylindricalSurface, Geom_ConicalSurface, Geom_ToroidalSurface)
    tangent = gp_Vec2d()
    curve_on_surface, edge_range, degenerated = BRep_Tool.CurveOnSurface_s, BRep_Tool.Range_s, BRep_Tool.Degenerated_s
    find_index = edge_map.FindIndex
    reals, pcurve_i, pcurve_d, pcurves = model.reals, model.pcurve_i, model.pcurve_d, model.pcurves
    for index in range(face_map.Extent()):
        ordinal = index + 1
        face = TopoDS.Face_s(face_map.FindKey(ordinal))
        row_i, row_d = [0] * _FACE_I, [0.0] * _FACE_D
        row_i[0] = ordinal
        row_i[1] = int(face.Orientation() == TopAbs_REVERSED)
        row_i[3] = -1
        row_i[4] = len(model.loop_i) // 2
        row_i[6] = len(model.pole_d) // _POLE_D
        loops: list[list[int]] = []
        try:
            adaptor = BRepAdaptor_Surface(face, False)
            kind = analytic.get(adaptor.GetType())
            if kind is not None:
                geometry = {"plane": adaptor.Plane, "cylinder": adaptor.Cylinder, "cone": adaptor.Cone,
                            "sphere": adaptor.Sphere, "torus": adaptor.Torus}[kind]()
                row_i[2] = _ANALYTIC[kind]
                row_d[4:16] = _frame(geometry)
                if kind in ("cylinder", "sphere"):
                    row_d[16] = geometry.Radius()
                elif kind == "cone":
                    row_d[16], row_d[17] = geometry.RefRadius(), geometry.SemiAngle()
                elif kind == "torus":
                    row_d[18], row_d[19] = geometry.MajorRadius(), geometry.MinorRadius()
            else:
                bspline = None if host_all else _bspline_surface(model, face)
                swept = None if host_all or bspline is not None else _swept_surface(model, face)
                if bspline is not None:
                    row_i[2], row_i[3] = _S_BSPLINE, bspline
                elif swept is not None:
                    row_i[2], row_i[3] = swept
                else:
                    row_i[2] = _S_HOST
                    model.surfaces[index] = _host_surface(adaptor)
            surface = BRep_Tool.Surface_s(face)
            # A face on a surface that leaves its trims' boxes whole, trimmed by lines, conics and
            # B-splines: its UV bounds made of the points BRepTools.UVBounds bounds each trim by, so
            # OCCT need not find each trim again (on a plane, by projecting its edge).
            box_u: list | None = [] if type(surface) in unclipped else None
            box_v: list = []
            wires = TopExp_Explorer(face, TopAbs_WIRE)
            point = gp_Pnt2d()
            while wires.More():
                loop_first, loop_size = len(pcurve_i), 0
                wire = TopoDS.Wire_s(wires.Current())
                walker = BRepTools_WireExplorer(wire, face)
                while walker.More():
                    edge = walker.Current()
                    curve = curve_on_surface(edge, face, 0.0, 0.0)
                    if curve is None:
                        raise ValueError("an edge with no pcurve on its face")
                    first, last = edge_range(edge, face)
                    edge_ord = find_index(edge)
                    reversed_ = int(edge.Orientation() == TopAbs_REVERSED)
                    loop_size += 1
                    if type(curve) is Geom2d_Line and not host_all and first < last and not degenerated(edge):
                        # What Geom2dConvert makes of a line trimmed to the range: degree 1 between its
                        # points at the range's ends.
                        start, end = curve.Value(first).Coord(), curve.Value(last).Coord()
                        at = len(reals)
                        reals.extend((first, first, last, last, *start, *end))
                        pcurve_i.append([edge_ord, reversed_, 0, 2, 1, 0, at, at + 4])
                        pcurve_d.extend((first, last))
                        pcurves.append(None)
                        if box_u is not None:
                            box_u.extend((start[0], end[0]))
                            box_v.extend((start[1], end[1]))
                        walker.Next()
                        continue
                    bspline = Geom2dConvert.CurveToBSplineCurve_s(Geom2d_TrimmedCurve(curve, first, last))
                    if bspline.IsPeriodic():
                        bspline.SetNotPeriodic()
                    count = bspline.NbPoles()
                    if host_all or bspline.Degree() > _MAX_DEGREE:
                        pcurve_i.append([edge_ord, reversed_, 1, count, 0, 0, 0, 0])
                        pcurves.append(_pcurve_value(bspline))
                        box_u = None
                    else:
                        degree, count, rational, knots_at, poles_at = model.bspline(bspline)
                        pcurve_i.append([edge_ord, reversed_, 0, count, degree, rational, knots_at, poles_at])
                        pcurves.append(None)
                        if box_u is not None:
                            if type(curve) is Geom2d_BSplineCurve:
                                if _bspline_bounded(curve, first, last):
                                    box_u.extend(reals[poles_at:poles_at + 2 * count:2])
                                    box_v.extend(reals[poles_at + 1:poles_at + 2 * count:2])
                                else:
                                    box_u = None
                            elif type(curve) in (Geom2d_Circle, Geom2d_Ellipse) and first < last:
                                if not _conic_bounds(curve, type(curve) is Geom2d_Circle, first, last, point,
                                                     tangent, box_u, box_v):
                                    box_u = None
                            else:
                                box_u = None
                    pcurve_d.extend((bspline.FirstParameter(), bspline.LastParameter()))
                    if degenerated(edge):
                        # A pole: the edge's one point, along the stretch of parameters that is it
                        # (none where the edge is a point in this face's parameters too).
                        bspline.D0(bspline.FirstParameter(), point)
                        start = [point.X(), point.Y()]
                        bspline.D0(bspline.LastParameter(), point)
                        end = [point.X(), point.Y()]
                        if start != end:
                            axis = 1 if abs(start[1] - end[1]) <= abs(start[0] - end[0]) else 0
                            vertex = BRep_Tool.Pnt_s(TopExp.FirstVertex_s(edge))
                            model.pole_d.extend((axis, start[axis], min(start[1 - axis], end[1 - axis]),
                                                 max(start[1 - axis], end[1 - axis]), vertex.X(), vertex.Y(),
                                                 vertex.Z()))
                    walker.Next()
                if box_u is not None and loop_size != wire.NbChildren():
                    box_u = None  # an edge the walk passed over bounds the face too
                if loop_size:
                    loops.append([loop_first, loop_size])
                wires.Next()
            box = _trim_box(box_u, box_v) if box_u else None
            u0, u1, v0, v1 = box if box is not None else BRepTools.UVBounds_s(face)
            if surface is not None:
                # A bound a hair past a non-periodic domain's edge is that edge.
                su0, su1, sv0, sv1 = surface.Bounds()
                if not surface.IsUPeriodic():
                    u0, u1 = min(max(u0, su0), su1), min(max(u1, su0), su1)
                if not surface.IsVPeriodic():
                    v0, v1 = min(max(v0, sv0), sv1), min(max(v1, sv0), sv1)
            row_d[0:4] = (u0, u1, v0, v1)
        except Exception:  # noqa: BLE001 - a face OCCT cannot read is left empty
            loops = []
        for first, size in loops:
            model.loop_i.extend((first, size))
        row_i[5] = len(loops)
        row_i[7] = len(model.pole_d) // _POLE_D - row_i[6]
        model.face_i[index], model.face_d[index] = row_i, row_d


def _bspline_bounded(curve, first: float, last: float) -> bool:
    """Whether BndLib_Box2dCurve bounds a B-spline trim over [first, last] by the poles of its
    conversion here: it bounds it by the poles of the curve segmented to the range -- segmented
    alike in the conversion -- or, where the range is the curve's own to within PConfusion, by the
    curve's own poles, which are its conversion's where the curve is clamped and the range exactly
    its own."""
    if curve.IsPeriodic():
        return False
    t1, t2 = curve.FirstParameter(), curve.LastParameter()
    low, high = (t1 if first < t1 else first), (t2 if last > t2 else last)
    if high < low:
        return False
    if abs(t1 - low) <= 1e-9 and abs(t2 - high) <= 1e-9:
        clamped = curve.Multiplicity(1) == curve.Multiplicity(curve.NbKnots()) == curve.Degree() + 1
        return clamped and first == t1 and last == t2
    return True


def _adjust_to_period(t: float, period: float) -> float:
    """BndLib_Box2dCurve::AdjustToPeriod."""
    if t < 0.0:
        t = t + (1 + int(-t / period)) * period
    elif t > period:
        t = t - int(t / period) * period
    return 0.0 if t == period else t


def _conic_bounds(curve, circle: bool, first: float, last: float, point, tangent, box_u: list,
                  box_v: list) -> bool:
    """The points BndLib_Box2dCurve bounds a circle or ellipse trim by over [first, last], added to
    the box's coordinates: its ends, then each of its points of least and greatest x and y the range
    passes, worked out as it works them out and each evaluated as it evaluates them (D1). False
    where it cannot."""
    position = curve.Position()
    x_dir, y_dir = position.XDirection(), position.YDirection()
    cos_bt, sin_bt, cos_gm, sin_gm = x_dir.X(), x_dir.Y(), y_dir.X(), y_dir.Y()
    r1, r2 = (curve.Radius(), curve.Radius()) if circle else (curve.MajorRadius(), curve.MinorRadius())
    two_pi = math.pi + math.pi
    a11, a12, a21, a22 = -r1 * cos_bt, r2 * cos_gm, -r1 * sin_bt, r2 * sin_gm
    extremes = []
    for lx, ly in ((0.0, 1.0), (1.0, 0.0)):
        bx = lx * a21 - ly * a11
        by = lx * a22 - ly * a12
        b = math.sqrt(bx * bx + by * by)
        if not b > 0.0:
            return False
        cos_fi, sin_fi = bx / b, by / b
        if not -1.0 <= cos_fi <= 1.0:
            return False
        fi = math.acos(cos_fi)
        if sin_fi < 0.0:
            fi = two_pi - fi
        extremes.extend((_adjust_to_period(two_pi - fi, two_pi), _adjust_to_period(math.pi - fi, two_pi)))

    def add(t: float) -> None:
        curve.D1(t, point, tangent)
        box_u.append(point.X())
        box_v.append(point.Y())

    add(first)
    add(last)
    t1z = _adjust_to_period(first, two_pi)
    if abs(t1z) < 1e-14:
        t1z = 0.0
    t2z = t1z + (last - first)
    if abs(t2z - two_pi) < 1e-14:
        t2z = two_pi
    for t in extremes:
        t = t + two_pi if t < t1z else t
        if t <= t2z:
            add(t)
    return True


def _trim_box(box_u: list, box_v: list) -> tuple[float, float, float, float] | None:
    """BRepTools.UVBounds of a face from the points it bounds each trim by: the least and greatest
    of each coordinate, less and plus Bnd_Box2d's gap of 0 (which turns a greatest -0 into 0).
    Bnd_Box2d keeps the first of equal least values, so None where a least 0 comes with each sign
    (the order the box met them in decides), or where a coordinate is NaN."""
    box = []
    for values in (box_u, box_v):
        if any(value != value for value in values):
            return None
        low = min(values)
        if low == 0.0 and len({math.copysign(1.0, value) for value in values if value == 0.0}) > 1:
            return None
        box.extend((low - 0.0, max(values) + 0.0))
    return box[0], box[1], box[2], box[3]


def _pcurve_value(curve):
    from OCP.gp import gp_Pnt2d

    point = gp_Pnt2d()
    d0 = curve.D0

    def value(t: float) -> tuple[float, float]:
        d0(t, point)
        return point.X(), point.Y()

    return value


def _edges(model: _Model, edge_map, *, host_all: bool) -> None:
    """Each model edge's exact 3D curve: a line, circle or ellipse as it is, else its B-spline."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.Geom import Geom_TrimmedCurve
    from OCP.GeomAbs import GeomAbs_CurveType as T
    from OCP.GeomConvert import GeomConvert
    from OCP.gp import gp_Pnt
    from OCP.TopoDS import TopoDS

    kinds = {T.GeomAbs_Line: _C_LINE, T.GeomAbs_Circle: _C_CIRCLE, T.GeomAbs_Ellipse: _C_ELLIPSE}
    for index in range(edge_map.Extent()):
        try:
            edge = TopoDS.Edge_s(edge_map.FindKey(index + 1))
            if BRep_Tool.Degenerated_s(edge):
                continue
            adaptor = BRepAdaptor_Curve(edge)
            first, last = adaptor.FirstParameter(), adaptor.LastParameter()
            kind = kinds.get(adaptor.GetType())
            row_i, row_d = [0] * _EDGE_I, [0.0] * _EDGE_D
            if kind is not None:
                evaluate, host = adaptor, host_all
                row_d[0:2] = (first, last)
                if kind == _C_LINE:
                    line = adaptor.Line()
                    row_d[2:5], row_d[11:14] = line.Location().Coord(), line.Direction().Coord()
                else:
                    conic = adaptor.Circle() if kind == _C_CIRCLE else adaptor.Ellipse()
                    row_d[2:11] = _frame(conic)[:9]
                    if kind == _C_CIRCLE:
                        row_d[14] = conic.Radius()
                    else:
                        row_d[14], row_d[15] = conic.MajorRadius(), conic.MinorRadius()
                row_i[:3] = (kind, int(host), -1)
            else:
                located = BRep_Tool.Curve_s(edge, 0.0, 0.0)
                if located is None:
                    continue
                bspline = GeomConvert.CurveToBSplineCurve_s(Geom_TrimmedCurve(located, first, last))
                if bspline.IsPeriodic():
                    bspline.SetNotPeriodic()
                evaluate = bspline
                row_d[0:2] = (bspline.FirstParameter(), bspline.LastParameter())
                host = host_all or bspline.Degree() > _MAX_DEGREE
                row_i[:3] = (_C_BSPLINE, int(host), bspline.NbPoles())
                if not host:
                    degree, count, rational, knots_at, poles_at = model.bspline(bspline)
                    row_i[3:9] = (degree, count, rational, knots_at, poles_at, 0)
            model.edge_i[index], model.edge_d[index] = row_i, row_d
            if host:
                point = gp_Pnt()
                d0 = evaluate.D0

                def value(t: float, d0=d0, point=point) -> tuple[float, float, float]:
                    d0(t, point)
                    return point.X(), point.Y(), point.Z()

                model.curves[index] = value
        except Exception:  # noqa: BLE001 - an edge OCCT cannot read has no polyline
            model.edge_i[index] = 0
            model.edge_d[index] = 0.0


def _array(values, dtype, columns: int) -> np.ndarray:
    return np.ascontiguousarray(np.asarray(values, dtype).reshape(-1, columns) if len(values) else np.zeros((1, columns), dtype))


def _request(model: _Model, *, chord: float, angle: float, failure: list) -> tuple[_Input, Any]:
    """The library's input for the model, and what must outlive the call: the tables it points
    into and the callback through which it asks the host to evaluate (each failure noted in
    ``failure``)."""
    face_uv = model.face_d[:, 0:4]

    def callback(_user, what: int, index: int, a: float, b: float, out) -> int:
        try:
            if what == _ASK_PCURVE:
                out[0], out[1] = model.pcurves[index](a)
            elif what == _ASK_CURVE:
                out[0], out[1], out[2] = model.curves[index](a)
            elif what == _ASK_SURFACE:
                out[0], out[1], out[2] = model.surfaces[index][0](a, b)
            else:
                out[0], out[1], out[2] = model.surfaces[index][1](a, b, face_uv[index].tolist())
            return 0
        except BaseException as error:  # noqa: BLE001 - told to the library as a failed evaluation
            failure.append(error)
            return 1

    tables = {
        "face_i": _array(model.face_i, np.int32, _FACE_I), "face_d": _array(model.face_d, np.float64, _FACE_D),
        "loop_i": _array(model.loop_i, np.int32, 2), "pcurve_i": _array(model.pcurve_i, np.int32, _PCURVE_I),
        "pcurve_d": _array(model.pcurve_d, np.float64, _PCURVE_D), "edge_i": _array(model.edge_i, np.int32, _EDGE_I),
        "edge_d": _array(model.edge_d, np.float64, _EDGE_D), "swept_i": _array(model.swept_i, np.int32, _SWEPT_I),
        "swept_d": _array(model.swept_d, np.float64, _SWEPT_D), "basis_i": _array(model.basis_i, np.int32, _EDGE_I),
        "basis_d": _array(model.basis_d, np.float64, _EDGE_D), "bsurface_i": _array(model.bsurface_i, np.int32, _BSURF_I),
        "bsurface_d": _array(model.bsurface_d, np.float64, _BSURF_D), "pole_d": _array(model.pole_d, np.float64, _POLE_D),
        "reals": _array(model.reals, np.float64, 1),
    }
    hook = _CALLBACK(callback)
    request = _Input(face_count=len(model.face_i), edge_count=len(model.edge_i), pcurve_count=len(model.pcurve_i),
                     bsurface_count=len(model.bsurface_i), swept_count=len(model.swept_i),
                     basis_count=len(model.basis_i), chord=chord, angle=angle, callback=hook, user=None,
                     **{name: array.ctypes.data_as(ctypes.POINTER(ctypes.c_int32 if array.dtype == np.int32
                                                                  else ctypes.c_double))
                        for name, array in tables.items()})
    return request, (tables, hook)


def _run(model: _Model, *, chord: float, angle: float):
    """The library's mesh of the model: (per-face (positions, normals, triangles) by ordinal, each
    edge's polyline by ordinal, the component's scale, the faces too narrow to resolve)."""
    library = _library()
    failure: list[BaseException] = []
    request, keep = _request(model, chord=chord, angle=angle, failure=failure)
    pointer = library.cadgen_tessellate(ctypes.byref(request))
    if not pointer:
        raise MemoryError("cadgen's tessellator could not allocate its result")
    try:
        result = pointer.contents
        if result.error:
            message = result.message.decode("utf-8", "replace")
            if failure:
                raise MeshProductionError(f"{message}: {type(failure[0]).__name__}: {failure[0]}") from failure[0]
            raise MeshProductionError(message)
        faces, edges = result.face_count, result.edge_count
        states = np.ctypeslib.as_array(result.face_state, (faces,)).copy() if faces else np.zeros(0, np.int32)
        vertices = np.ctypeslib.as_array(result.face_vertices, (faces,)).copy() if faces else np.zeros(0, np.int64)
        triangles = np.ctypeslib.as_array(result.face_triangles, (faces,)).copy() if faces else np.zeros(0, np.int64)
        positions = (np.ctypeslib.as_array(result.positions, (result.vertex_total, 3)).copy() if result.vertex_total
                     else np.zeros((0, 3), np.float32))
        normals = (np.ctypeslib.as_array(result.normals, (result.vertex_total, 3)).copy() if result.vertex_total
                   else np.zeros((0, 3), np.float32))
        indices = (np.ctypeslib.as_array(result.triangles, (result.triangle_total, 3)).copy() if result.triangle_total
                   else np.zeros((0, 3), np.uint32))
        counts = np.ctypeslib.as_array(result.edge_points, (edges,)).copy() if edges else np.zeros(0, np.int64)
        points = (np.ctypeslib.as_array(result.points, (result.point_total, 3)).copy() if result.point_total
                  else np.zeros((0, 3), np.float64))
        scale = result.scale
    finally:
        library.cadgen_tessellate_free(pointer)
        del keep  # the tables it read and the callback it asked through, kept to here
    meshes = {}
    vertex_at = triangle_at = 0
    for index in range(faces):
        if states[index] != _FACE_MESHED:
            continue
        v, t = int(vertices[index]), int(triangles[index])
        meshes[index + 1] = (positions[vertex_at:vertex_at + v], normals[vertex_at:vertex_at + v],
                             indices[triangle_at:triangle_at + t])
        vertex_at += v
        triangle_at += t
    polylines = {}
    point_at = 0
    for index in range(edges):
        count = int(counts[index])
        if model.edge_i[index][0]:
            polylines[index + 1] = points[point_at:point_at + count]
        point_at += count
    unresolved = {index + 1 for index in range(faces) if states[index] == _FACE_UNRESOLVED}
    return meshes, polylines, scale, unresolved


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


def _model(topods, face_map, edge_map, *, host_all: bool = False) -> _Model:
    """The component's geometry as the library reads it. ``host_all`` evaluates every trim, edge
    and non-analytic surface through OCCT instead of the library's own B-splines and conics."""
    model = _Model(face_map.Extent(), edge_map.Extent())
    _faces(model, face_map, edge_map, host_all=host_all)
    _edges(model, edge_map, host_all=host_all)
    return model


def mesh_component(topods, table: dict, face_colors: dict, *, part: str, chord: float, angle: float) -> bytes:
    """Mesh one unlocated component and return its GLB body, which names ``part``.

    ``table`` is the component's selector table (its faces' areas, its edges' classes) and
    ``face_colors`` its intrinsic face colours, by the same ordinals; ``chord`` is relative to the
    component's extent and ``angle`` in radians. A failure inside OCCT or the tessellator is the
    component's ``MeshProductionError``.
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


def _mesh_component(topods, table: dict, face_colors: dict, *, part: str, chord: float, angle: float,
                    host_all: bool = False) -> bytes:
    from cadgen.store.meshes import encode_payload

    face_map, edge_map = _maps(topods)
    table_faces, table_edges = _rows(table, "faces", "area"), _rows(table, "edges", "visibilityClass")
    if len(table_faces) != face_map.Extent() or len(table_edges) != edge_map.Extent():
        raise MeshProductionError(
            f"the component's BREP has {face_map.Extent()} faces and {edge_map.Extent()} edges, "
            f"its selector table {len(table_faces)} and {len(table_edges)}"
        )
    meshes, polylines, scale, unresolved = _run(_model(topods, face_map, edge_map, host_all=host_all),
                                                 chord=chord, angle=angle)
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
