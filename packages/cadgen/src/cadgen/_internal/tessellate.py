"""One component's display mesh: OCCT triangulates, and cadgen's native mesh library proves every
face of the result against the exact geometry or repairs it.

Two steps that are one law (``STORE.md`` §3). OCCT's ``BRepMesh_IncrementalMesh`` meshes the
component at the display tolerance -- the chord tolerance as an absolute deflection against the
component's bounding diagonal, the angle in radians -- from no triangulation, discretizing each
model edge once and triangulating each face in its own parameters, so the body is closed across
its faces by construction. The library (``native/tessellate`` in cadgen's source tree; one build
per platform in ``_runtime/native``, loaded here with ctypes and run with the GIL released) then
VERIFIES every face against the exact geometry it evaluates itself -- each analytic surface by its
formula, each B-spline surface, pcurve and edge curve by its poles and knots (de Boor's algorithm,
so a clamped end is its end pole exactly), each conic as OCCT writes it, a surface of extrusion or
revolution by its basis curve, swept; any other surface through OCCT, which it asks this module
for -- and REPAIRS what fails, locally: a triangle off its surface is refined in place about new
vertices on the surface, a folded one unfolded by flipping an edge, a hole inside a face ear-cut
in the face's parameters, two faces disagreeing along their edge made to meet, and a face OCCT
refused, or no repair brought right, meshed by the library's own tessellator over the polylines
its neighbours already fixed -- a grid in the face's parameters, cells clipped to the trim and
ear-cut, refined to the surface -- kept only where it proves out. No clock decides what is checked.

This module's half is reading and packing. It reads the component's geometry through OCCT into the
flat tables the library takes (``_model``), decides which faces OCCT does not get -- the swept
tubes whose cost OCCT does not survive (``tessellation_policy.cost_guard``, a judgement of the
surface alone) -- meshes the rest with OCCT and reads the triangulation back fast: every face's
positions, normals, parameters and triangles through OCCT's own glTF writer (one primitive per
face, no call per vertex) and every edge's polygon on triangulation (``_polygons``: the knots the
faces either side share, which become the library's fixed edge polylines, and in a face OCCT did
not mesh their place in that face's parameters). What the library returns is packed into the
part's GLB body (``cadgen.store.meshes``): positions, normals and triangles face by face in
ordinal order, each model edge's polyline with its class, the faces that came out empty, which
every reader names (``unmeshedFaces``), and the faces drawn though still off their surface past
the verification bound, named too (``unverifiedFaces``). A face narrower than the tolerance its
trim is sampled to, or under the deflection squared in area, draws as nothing and is not named.
"""

from __future__ import annotations

import ctypes
import json
import math
import platform
import struct
import sys
import tempfile
import threading
from array import array
from pathlib import Path
from typing import Any, Callable

import numpy as np

_ABI = 2  # tessellate's TESS_ABI: the tables it reads and the result it returns

# The tables' columns (api.c), and the kinds of surface and curve the library evaluates itself.
_FACE_I, _FACE_D, _PCURVE_I, _PCURVE_D, _EDGE_I, _EDGE_D, _BSURF_I, _BSURF_D, _POLE_D = 8, 22, 12, 2, 11, 16, 10, 4, 7
_SWEPT_I, _SWEPT_D, _MESH_I, _REPORT_N = 2, 6, 5, 10
_ANALYTIC = {"plane": 1, "cylinder": 2, "cone": 3, "sphere": 4, "torus": 5}
_S_BSPLINE, _S_HOST, _S_EXTRUSION, _S_REVOLUTION = 6, 7, 8, 9
_C_LINE, _C_CIRCLE, _C_ELLIPSE, _C_BSPLINE, _C_HYPERBOLA, _C_PARABOLA = 1, 2, 3, 4, 5, 6
_ASK_PCURVE, _ASK_CURVE, _ASK_SURFACE, _ASK_NORMAL = 0, 1, 2, 3
# Who meshes a face (mesh_i column 0), and what the library says of it after (face_state).
_MESH_NONE, _MESH_OCCT, _MESH_GUARDED = 0, 1, 2
_FACE_NONE, _FACE_MESHED, _FACE_UNRESOLVED, _FACE_UNMESHED, _FACE_UNVERIFIED = 0, 1, 2, 3, 4
_MADE_BY = {0: "none", 1: "occt", 2: "library"}
_REPORT_FIELDS = ("rounds", "splits", "flips", "folded", "holesFilled", "holeEdgesLeft", "openEdges", "freeEdges",
                  "fallbacks", "areaOk")
_MAX_DEGREE = 25
# glTF's component types for indices.
_INDEX_TYPES = {5121: np.uint8, 5123: np.uint16, 5125: np.uint32}

_CALLBACK = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_int32, ctypes.c_int32, ctypes.c_double,
                             ctypes.c_double, ctypes.POINTER(ctypes.c_double))
_I32P, _F64P = ctypes.POINTER(ctypes.c_int32), ctypes.POINTER(ctypes.c_double)


class _Input(ctypes.Structure):
    _fields_ = [
        ("face_count", ctypes.c_int32), ("edge_count", ctypes.c_int32), ("pcurve_count", ctypes.c_int32),
        ("bsurface_count", ctypes.c_int32), ("swept_count", ctypes.c_int32), ("basis_count", ctypes.c_int32),
        ("chord", ctypes.c_double), ("angle", ctypes.c_double), ("deflection", ctypes.c_double),
        ("repair_at", ctypes.c_double), ("verified_at", ctypes.c_double),
        ("face_i", _I32P), ("face_d", _F64P),
        ("mesh_i", _I32P), ("mesh_xyz", _F64P), ("mesh_nrm", _F64P), ("mesh_uv", _F64P), ("mesh_tri", _I32P),
        ("loop_i", _I32P), ("pcurve_i", _I32P), ("pcurve_d", _F64P), ("knot_nodes", _I32P), ("knot_uv", _F64P),
        ("edge_i", _I32P), ("edge_d", _F64P), ("edge_poly_xyz", _F64P), ("edge_poly_t", _F64P),
        ("swept_i", _I32P), ("swept_d", _F64P), ("basis_i", _I32P), ("basis_d", _F64P),
        ("bsurface_i", _I32P), ("bsurface_d", _F64P), ("pole_d", _F64P), ("reals", _F64P),
        ("callback", _CALLBACK), ("user", ctypes.c_void_p),
    ]


class _Result(ctypes.Structure):
    _fields_ = [
        ("abi", ctypes.c_int32), ("error", ctypes.c_int32), ("message", ctypes.c_char * 160),
        ("scale", ctypes.c_double), ("face_count", ctypes.c_int32), ("edge_count", ctypes.c_int32),
        ("face_state", _I32P), ("face_source", _I32P), ("face_worst", _F64P), ("face_report", ctypes.POINTER(ctypes.c_int64)),
        ("face_vertices", ctypes.POINTER(ctypes.c_int64)), ("face_triangles", ctypes.POINTER(ctypes.c_int64)),
        ("positions", ctypes.POINTER(ctypes.c_float)), ("normals", ctypes.POINTER(ctypes.c_float)),
        ("triangles", ctypes.POINTER(ctypes.c_uint32)),
        ("vertex_total", ctypes.c_int64), ("triangle_total", ctypes.c_int64),
        ("edge_points", ctypes.POINTER(ctypes.c_int64)), ("points", _F64P), ("point_total", ctypes.c_int64),
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
    """The mesh library for this platform, loaded once."""
    global _LIBRARY
    with _LOCK:
        if _LIBRARY is None:
            from cadgen.assets import AssetMissing, runtime_build_hint, runtime_root

            path = runtime_root() / "native" / _library_name()
            if not path.is_file():
                raise AssetMissing(f"cadgen's mesh library for this platform ({path.name}) is missing, and without it "
                                   "no part can be verified, and so none meshed. "
                                   + runtime_build_hint(path, overridable=False))
            library = ctypes.CDLL(str(path))
            library.cadgen_tessellate_abi.restype = ctypes.c_int32
            library.cadgen_tessellate.argtypes = [ctypes.POINTER(_Input)]
            library.cadgen_tessellate.restype = ctypes.POINTER(_Result)
            library.cadgen_tessellate_free.argtypes = [ctypes.POINTER(_Result)]
            library.cadgen_tessellate_free.restype = None
            if library.cadgen_tessellate_abi() != _ABI:
                raise RuntimeError(f"cadgen's mesh library ({path}) is not the one this cadgen was built with")
            _LIBRARY = library
        return _LIBRARY


# --- The component's geometry, as the library reads it --------------------------------------


class _Model:
    """The component's faces, loops, pcurves, edges and B-spline surfaces as flat tables, the
    host's evaluators for whatever the library evaluates through it, and -- once OCCT has meshed
    -- its triangulation and each edge's polygon on it."""

    def __init__(self, face_count: int, edge_count: int) -> None:
        self.face_i = np.zeros((face_count, _FACE_I), np.int32)
        self.face_d = np.zeros((face_count, _FACE_D), np.float64)
        self.face_i[:, 3] = -1
        self.loop_i: list[int] = []
        self.pcurve_i: list[list[int]] = []
        self.pcurve_d: list[float] = []
        # Per pcurve: (face index, the edge as the wire walks it, its pcurve, first, last, degenerated).
        self.pcurve_edges: list[tuple] = []
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
        self.guarded: set[int] = set()  # face indices the cost guard keeps from OCCT
        self.unreadable: set[int] = set()  # face indices whose trim OCCT could not read
        # OCCT's triangulation, read back (``_occt_mesh``): per face its block in the mesh arrays.
        self.mesh_i = np.zeros((face_count, _MESH_I), np.int32)
        self.mesh_xyz = np.zeros((0, 3), np.float64)
        self.mesh_nrm = np.zeros((0, 3), np.float64)
        self.mesh_uv = np.zeros((0, 2), np.float64)
        self.mesh_tri = np.zeros((0, 3), np.int32)
        # The polygons on triangulation (``_polygons``): each pcurve's knots as the face's mesh
        # vertices and in the face's parameters, and each edge's fixed polyline.
        self.knot_nodes: list[np.ndarray] = []
        self.knot_uv: list[np.ndarray] = []
        self.edge_poly: dict[int, tuple[np.ndarray, np.ndarray]] = {}  # edge index -> (points, params)

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


def _bspline_surface(model: _Model, face) -> tuple[int | None, dict | None]:
    """The face's surface as a B-spline the library evaluates, where it is one or converts to one
    exactly (a Bézier patch): its index in the table and its signature for the cost guard
    (``tessellation_policy.cost_guard``); (None, None) for any other."""
    from OCP.BRep import BRep_Tool
    from OCP.Geom import Geom_BezierSurface, Geom_BSplineSurface, Geom_RectangularTrimmedSurface
    from OCP.GeomConvert import GeomConvert

    surface = BRep_Tool.Surface_s(face)  # in the component's frame
    while isinstance(surface, Geom_RectangularTrimmedSurface):
        surface = surface.BasisSurface()
    if isinstance(surface, Geom_BezierSurface):
        surface = GeomConvert.SurfaceToBSplineSurface_s(surface)
    if not isinstance(surface, Geom_BSplineSurface):
        return None, None
    if surface.UDegree() > _MAX_DEGREE or surface.VDegree() > _MAX_DEGREE:
        return None, None
    periodic_u, periodic_v = surface.IsUPeriodic(), surface.IsVPeriodic()
    signature = {"degree": (surface.UDegree(), surface.VDegree()), "poles": (surface.NbUPoles(), surface.NbVPoles()),
                 "spans": (surface.NbUKnots() - 1, surface.NbVKnots() - 1),
                 "rational": bool(surface.IsURational() or surface.IsVRational()),
                 "periodic": (bool(periodic_u), bool(periodic_v))}
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
    return len(model.bsurface_i) - 1, signature


def _faces(model: _Model, face_map, edge_map) -> None:
    """Each face's surface, uv box, trim loops of pcurves and poles, and whether the cost guard
    keeps it from OCCT. A face whose surface or trim cannot be read has no loops, so it comes out
    empty, and is named."""
    from cadgen.tessellation_policy import cost_guard
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
    from OCP.Geom import (Geom_ConicalSurface, Geom_CylindricalSurface, Geom_Plane, Geom_RectangularTrimmedSurface,
                          Geom_ToroidalSurface)
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
                bspline, signature = _bspline_surface(model, face)
                swept = None if bspline is not None else _swept_surface(model, face)
                if bspline is not None:
                    row_i[2], row_i[3] = _S_BSPLINE, bspline
                    if cost_guard(signature):
                        model.guarded.add(index)
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
                    is_degenerated = degenerated(edge)
                    loop_size += 1
                    model.pcurve_edges.append((index, edge, curve, first, last, is_degenerated))
                    if type(curve) is Geom2d_Line and first < last and not is_degenerated:
                        # What Geom2dConvert makes of a line trimmed to the range: degree 1 between its
                        # points at the range's ends.
                        start, end = curve.Value(first).Coord(), curve.Value(last).Coord()
                        at = len(reals)
                        reals.extend((first, first, last, last, *start, *end))
                        pcurve_i.append([edge_ord, reversed_, 0, 2, 1, 0, at, at + 4, 0, 0, 0, 0])
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
                    if bspline.Degree() > _MAX_DEGREE:
                        pcurve_i.append([edge_ord, reversed_, 1, count, 0, 0, 0, 0, 0, 0, 0, 0])
                        pcurves.append(_pcurve_value(bspline))
                        box_u = None
                    else:
                        degree, count, rational, knots_at, poles_at = model.bspline(bspline)
                        pcurve_i.append([edge_ord, reversed_, 0, count, degree, rational, knots_at, poles_at, 0, 0, 0, 0])
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
                    if is_degenerated:
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
            # The surface's periods, which a face closed round one carries a seam of: OCCT's
            # triangulation puts a seam corner at either side of it, and the library unwraps.
            basis = surface
            while isinstance(basis, Geom_RectangularTrimmedSurface):
                basis = basis.BasisSurface()
            if basis is not None:
                row_d[20] = basis.UPeriod() if basis.IsUPeriodic() else 0.0
                row_d[21] = basis.VPeriod() if basis.IsVPeriodic() else 0.0
        except Exception:  # noqa: BLE001 - a face OCCT cannot read is left empty
            # Its pcurve rows so far stand (the table is flat) but no loop names them.
            loops = []
            model.unreadable.add(index)
            model.guarded.discard(index)
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


def _edges(model: _Model, edge_map) -> None:
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
            host = False
            if kind is not None:
                evaluate = adaptor
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
                row_i[:3] = (kind, 0, -1)
            else:
                located = BRep_Tool.Curve_s(edge, 0.0, 0.0)
                if located is None:
                    continue
                bspline = GeomConvert.CurveToBSplineCurve_s(Geom_TrimmedCurve(located, first, last))
                if bspline.IsPeriodic():
                    bspline.SetNotPeriodic()
                evaluate = bspline
                row_d[0:2] = (bspline.FirstParameter(), bspline.LastParameter())
                host = bspline.Degree() > _MAX_DEGREE
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


# --- OCCT's triangulation, read back ----------------------------------------------------------


def _diagonal(topods) -> float:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    BRepBndLib.Add_s(topods, box, False)
    if box.IsVoid():
        return 0.0
    x0, y0, z0, x1, y1, z1 = box.Get()
    diagonal = math.sqrt((x1 - x0) ** 2 + (y1 - y0) ** 2 + (z1 - z0) ** 2)
    return diagonal if math.isfinite(diagonal) else 0.0


def _occt_mesh(topods, face_map, skip: set[int], deflection: float, angle: float) -> dict[int, tuple]:
    """OCCT's mesh of every face of the component but ``skip``'s (face indices), made from no
    triangulation at the absolute ``deflection`` and ``angle``, one thread: each meshed face's
    (positions, normals, uv, triangles) by face index -- float32 positions in the component's frame
    and outward normals as OCCT's glTF writer places them, parameters in double, triangles wound
    outward. A face OCCT refused is not in the result."""
    from OCP.BRep import BRep_Builder, BRep_Tool
    from OCP.BRepLib import BRepLib_ToolTriangulatedShape
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRepTools import BRepTools
    from OCP.IMeshTools import IMeshTools_Parameters
    from OCP.Precision import Precision
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS, TopoDS_Compound

    # A stored BREP can carry triangles some earlier tool made, which OCCT keeps where they are
    # fine enough for it: every component is meshed from none.
    BRepTools.Clean_s(topods)
    if not face_map.Extent():
        return {}
    shape = topods
    if skip:
        # The faces OCCT gets, as one compound sharing the component's edges: each shared edge is
        # discretized once, and the triangulation lands on the faces the component holds.
        shape, builder = TopoDS_Compound(), BRep_Builder()
        builder.MakeCompound(shape)
        for index in range(face_map.Extent()):
            if index not in skip:
                builder.Add(shape, face_map.FindKey(index + 1))
    parameters = IMeshTools_Parameters()
    # OCCT refuses a deflection under its confusion tolerance, which a component of next to no
    # extent would otherwise ask for.
    parameters.Deflection = max(deflection, Precision.Confusion_s())
    parameters.Angle = angle
    parameters.InParallel = False
    BRepMesh_IncrementalMesh(shape, parameters)
    # Each triangulation gets OCCT's normals first, from the exact surface where it has one and
    # from the triangles around a node where it does not (a cone's apex, a revolved profile's
    # pole): left to the writer, such a node's normal is a fixed axis, often pointing into the
    # solid.
    triangulated = []
    explorer = TopExp_Explorer(topods, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face_s(explorer.Current())
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face, location)
        if triangulation is not None and triangulation.NbTriangles() > 0:
            if not triangulation.HasNormals():
                BRepLib_ToolTriangulatedShape.ComputeNormals_s(face, triangulation)
            triangulated.append((face_map.FindIndex(face) - 1, face, triangulation, location))
        explorer.Next()
    if not triangulated:
        return {}
    faces = _faces_from_gltf(topods, triangulated)
    return _faces_one_by_one(triangulated) if faces is None else faces


def _faces_from_gltf(topods, triangulated) -> dict[int, tuple] | None:
    """Each face's (positions, normals, uv, triangles), written by OCCT's glTF writer: one TRIANGLES
    primitive per triangulated face in the explorer's order, positions located, normals outward,
    the parameters as texture coordinates (the writer flips v about 1), indices wound outward.
    None when the writer's primitives cannot be matched to ``triangulated`` one for one; the caller
    then reads the faces itself."""
    from OCP.Message import Message_ProgressRange
    from OCP.RWGltf import RWGltf_CafWriter
    from OCP.RWMesh import RWMesh_CoordinateSystem
    from OCP.TColStd import TColStd_IndexedDataMapOfStringString
    from OCP.TCollection import TCollection_AsciiString, TCollection_ExtendedString
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    document = TDocStd_Document(TCollection_ExtendedString("cadgen-mesh"))
    XCAFDoc_DocumentTool.ShapeTool_s(document.Main()).AddShape(topods, False)
    with tempfile.TemporaryDirectory(prefix="cadgen-mesh-") as folder:
        path = Path(folder) / "component.glb"
        writer = RWGltf_CafWriter(TCollection_AsciiString(str(path)), True)
        writer.SetMergeFaces(False)
        writer.SetForcedUVExport(True)
        converter = writer.ChangeCoordinateSystemConverter()
        converter.SetInputCoordinateSystem(RWMesh_CoordinateSystem.RWMesh_CoordinateSystem_Zup)
        converter.SetOutputCoordinateSystem(RWMesh_CoordinateSystem.RWMesh_CoordinateSystem_Zup)
        if not writer.Perform(document, TColStd_IndexedDataMapOfStringString(), Message_ProgressRange()):
            return None
        data = path.read_bytes()
    json_length = struct.unpack_from("<I", data, 12)[0]
    gltf = json.loads(data[20:20 + json_length])
    binary = memoryview(data)[20 + json_length + 8:]

    def view(accessor_index: int, dtype, width: int) -> np.ndarray:
        accessor = gltf["accessors"][accessor_index]
        buffer_view = gltf["bufferViews"][accessor["bufferView"]]
        offset = buffer_view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
        return np.frombuffer(binary, dtype, accessor["count"] * width, offset).reshape(-1, width)

    # The writer puts a component's free edges and vertices beside its faces, as LINES and POINTS
    # primitives: the faces are its TRIANGLES (mode 4, the default).
    primitives = [primitive for mesh in gltf.get("meshes", []) for primitive in mesh["primitives"]
                  if primitive.get("mode", 4) == 4]
    if len(primitives) != len(triangulated):
        return None
    # The writer places a located shape with a node transform; the component is unlocated, so
    # every node must be the identity for the arrays to be its own.
    for node in gltf.get("nodes", []):
        if any(name in node for name in ("matrix", "translation", "rotation", "scale")):
            matrix = node.get("matrix")
            if matrix is None or not np.allclose(matrix, np.eye(4).ravel()):
                return None
    faces = {}
    for (index, _face, triangulation, _location), primitive in zip(triangulated, primitives):
        attributes = primitive["attributes"]
        if "TEXCOORD_0" not in attributes or "NORMAL" not in attributes:
            return None
        positions = view(attributes["POSITION"], np.float32, 3).copy()
        normals = view(attributes["NORMAL"], np.float32, 3).copy()
        uv = view(attributes["TEXCOORD_0"], np.float32, 2).astype(np.float64)
        uv[:, 1] = 1.0 - uv[:, 1]
        index_accessor = gltf["accessors"][primitive["indices"]]
        triangles = view(primitive["indices"], _INDEX_TYPES[index_accessor["componentType"]], 1).astype(np.int32).reshape(-1, 3)
        if (len(positions) != triangulation.NbNodes() or len(triangles) != triangulation.NbTriangles()
                or index in faces):
            return None
        faces[index] = (positions, normals, uv, triangles)
    return faces


def _faces_one_by_one(triangulated) -> dict[int, tuple]:
    """The same arrays read face by face: placed positions, normals turned to the face's
    orientation, the parameters, and triangles wound to it -- and turned once more by a mirroring
    placement, which carries the normals itself, as the glTF writer does."""
    from OCP.TopAbs import TopAbs_REVERSED

    faces = {}
    for index, face, triangulation, location in triangulated:
        if index in faces:
            continue
        transform = location.Transformation()
        count = triangulation.NbNodes()
        positions = np.empty((count, 3), np.float32)
        normals = np.empty((count, 3), np.float32)
        uv = np.empty((count, 2), np.float64)
        flip = -1.0 if face.Orientation() == TopAbs_REVERSED else 1.0
        turn = (flip < 0) != (transform.VectorialPart().Determinant() < 0)
        for node in range(1, count + 1):
            point = triangulation.Node(node).Transformed(transform)
            normal = triangulation.Normal(node).Transformed(transform)
            positions[node - 1] = (point.X(), point.Y(), point.Z())
            normals[node - 1] = (flip * normal.X(), flip * normal.Y(), flip * normal.Z())
            parameters = triangulation.UVNode(node)
            uv[node - 1] = (parameters.X(), parameters.Y())
        triangles = np.empty((triangulation.NbTriangles(), 3), np.int32)
        for k in range(1, triangulation.NbTriangles() + 1):
            a, b, c = triangulation.Triangle(k).Get()
            triangles[k - 1] = (a - 1, c - 1, b - 1) if turn else (a - 1, b - 1, c - 1)
        faces[index] = (positions, normals, uv, triangles)
    return faces


def _take_meshes(model: _Model, meshed: dict[int, tuple]) -> None:
    """OCCT's faces into the model's mesh arrays, each face's block named by ``mesh_i``: who meshes
    the face (OCCT, or the library for a guarded or unreadable one) and where its arrays are."""
    face_count = len(model.face_i)
    xyz, nrm, uv, tri = [], [], [], []
    vertex_at = triangle_at = 0
    for index in range(face_count):
        source = _MESH_GUARDED if index in model.guarded else _MESH_NONE if index in model.unreadable else _MESH_OCCT
        block = meshed.get(index) if source == _MESH_OCCT else None
        if block is None:
            model.mesh_i[index] = (source, vertex_at, 0, triangle_at, 0)
            continue
        positions, normals, parameters, triangles = block
        model.mesh_i[index] = (source, vertex_at, len(positions), triangle_at, len(triangles))
        xyz.append(positions.astype(np.float64))
        nrm.append(normals.astype(np.float64))
        uv.append(parameters)
        tri.append(triangles)
        vertex_at += len(positions)
        triangle_at += len(triangles)
    if xyz:
        model.mesh_xyz, model.mesh_nrm = np.concatenate(xyz), np.concatenate(nrm)
        model.mesh_uv, model.mesh_tri = np.concatenate(uv), np.concatenate(tri)


def _polygons(model: _Model, face_map, meshed: dict[int, tuple], deflection: float) -> None:
    """Each edge's polygon on triangulation, read off the faces OCCT meshed: the edge's fixed
    polyline (the first face's polygon, in the edge's own order), and per pcurve the knots as the
    face's mesh vertices (a meshed face) and in the face's parameters -- a meshed face's at its
    nodes, an unmeshed face's where its pcurve puts the edge's parameters, each checked against the
    polyline's point and found by the surface's inverse where the pcurve does not put it there."""
    from OCP.BRep import BRep_Tool
    from OCP.gp import gp_Pnt, gp_Pnt2d
    from OCP.ShapeAnalysis import ShapeAnalysis_Surface
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    triangulations: dict[int, tuple] = {}
    for index in meshed:
        location = TopLoc_Location()
        face = TopoDS.Face_s(face_map.FindKey(index + 1))
        triangulations[index] = (BRep_Tool.Triangulation_s(face, location), location)
    polygons: dict[int, tuple[np.ndarray, np.ndarray]] = {}  # pcurve index -> (nodes, params)
    def read(polygon):
        nodes = np.fromiter(polygon.Nodes(), np.int32, polygon.NbNodes()) - 1
        params = np.fromiter(polygon.Parameters().Array1(), np.float64, polygon.NbNodes())
        if not (params[1:] >= params[:-1]).all():
            order = np.argsort(params, kind="stable")
            nodes, params = nodes[order], params[order]
        return nodes, params

    for p, (index, edge, curve, _first, _last, degenerated) in enumerate(model.pcurve_edges):
        if degenerated or index not in triangulations:
            continue
        triangulation, location = triangulations[index]
        polygon = BRep_Tool.PolygonOnTriangulation_s(edge, triangulation, location)
        if polygon is None or not polygon.HasParameters() or polygon.NbNodes() < 2:
            continue
        nodes, params = read(polygon)
        face = TopoDS.Face_s(face_map.FindKey(index + 1))
        if BRep_Tool.IsClosed_s(edge, face):
            # A seam edge has a polygon per side. Which BRep_Tool returns for which orientation
            # of the edge does not follow the rule it applies to the pcurves, so the side is
            # chosen by where this pcurve is: the polygon whose first node is on it.
            other = BRep_Tool.PolygonOnTriangulation_s(TopoDS.Edge_s(edge.Reversed()), triangulation, location)
            if other is not None and other.HasParameters() and other.NbNodes() >= 2:
                other_nodes, other_params = read(other)
                uv = meshed[index][2]
                at = np.array(curve.Value(float(params[0])).Coord())
                other_at = np.array(curve.Value(float(other_params[0])).Coord())
                if (np.abs(uv[other_nodes[0]] - other_at).max() < np.abs(uv[nodes[0]] - at).max()):
                    nodes, params = other_nodes, other_params
        edge_index = model.pcurve_i[p][0] - 1
        if edge_index >= 0 and edge_index not in model.edge_poly and model.edge_i[edge_index][0] != 0:
            model.edge_poly[edge_index] = (meshed[index][0][nodes].astype(np.float64), params)
        polygons[p] = (nodes, params)
    # Each pcurve's knots: a meshed face's own polygon, else the edge's polyline in this face.
    knot_nodes: list[np.ndarray] = []
    knot_uv: list[np.ndarray] = []
    inverses: dict[int, Any] = {}
    for p, (index, edge, curve, first, last, degenerated) in enumerate(model.pcurve_edges):
        nodes = uv = None
        edge_index = model.pcurve_i[p][0] - 1
        polyline = model.edge_poly.get(edge_index) if edge_index >= 0 else None
        if p in polygons:
            nodes, params = polygons[p]
            uv = meshed[index][2][nodes]
            if polyline is not None and len(params) != len(polyline[1]):
                nodes = uv = None  # another face's polygon on this edge: labelled by position instead
        elif polyline is not None and not degenerated and index not in model.unreadable:
            points, params = polyline
            uv = np.array([curve.Value(float(t)).Coord() for t in params], np.float64).reshape(-1, 2)
            # Where the pcurve does not put the edge's parameter at the polyline's point (an edge
            # not SameParameter), the surface's inverse does -- on the period the pcurve is on,
            # not the one the inverse normalizes to.
            face = TopoDS.Face_s(face_map.FindKey(index + 1))
            surface = BRep_Tool.Surface_s(face)
            periods = model.face_d[index, 20:22]
            for k in range(len(params)):
                at = surface.Value(float(uv[k, 0]), float(uv[k, 1]))
                target = gp_Pnt(*points[k].tolist())
                if at.Distance(target) > deflection:
                    if index not in inverses:
                        inverses[index] = ShapeAnalysis_Surface(surface)
                    found = inverses[index].NextValueOfUV(gp_Pnt2d(float(uv[k, 0]), float(uv[k, 1])), target,
                                                          deflection * 1e-3, deflection)
                    if inverses[index].Gap() <= deflection:
                        where = np.array([found.X(), found.Y()])
                        for d in range(2):
                            if periods[d] > 0:
                                where[d] += round((uv[k, d] - where[d]) / periods[d]) * periods[d]
                        uv[k] = where
        row = model.pcurve_i[p]
        if nodes is not None:
            row[8], row[9] = sum(len(k) for k in knot_nodes), len(nodes)
            knot_nodes.append(nodes.astype(np.int32))
        if uv is not None:
            row[10], row[11] = sum(len(k) for k in knot_uv), len(uv)
            knot_uv.append(np.ascontiguousarray(uv, np.float64))
    model.knot_nodes, model.knot_uv = knot_nodes, knot_uv
    for edge_index, (points, params) in model.edge_poly.items():
        model.edge_i[edge_index][9] = sum(len(model.edge_poly[k][1]) for k in model.edge_poly if k < edge_index)
        model.edge_i[edge_index][10] = len(params)


def _array(values, dtype, columns: int) -> np.ndarray:
    if isinstance(values, np.ndarray):
        array_ = values.astype(dtype, copy=False).reshape(-1, columns) if values.size else np.zeros((1, columns), dtype)
        return np.ascontiguousarray(array_)
    return np.ascontiguousarray(np.asarray(values, dtype).reshape(-1, columns) if len(values) else np.zeros((1, columns), dtype))


def _request(model: _Model, *, chord: float, angle: float, deflection: float, failure: list) -> tuple[_Input, Any]:
    """The library's input for the model, and what must outlive the call: the tables it points
    into and the callback through which it asks the host to evaluate (each failure noted in
    ``failure``)."""
    from cadgen.tessellation_policy import VERIFICATION

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

    edge_poly = sorted(model.edge_poly.items())
    tables = {
        "face_i": _array(model.face_i, np.int32, _FACE_I), "face_d": _array(model.face_d, np.float64, _FACE_D),
        "mesh_i": _array(model.mesh_i, np.int32, _MESH_I), "mesh_xyz": _array(model.mesh_xyz, np.float64, 3),
        "mesh_nrm": _array(model.mesh_nrm, np.float64, 3), "mesh_uv": _array(model.mesh_uv, np.float64, 2),
        "mesh_tri": _array(model.mesh_tri, np.int32, 3),
        "loop_i": _array(model.loop_i, np.int32, 2), "pcurve_i": _array(model.pcurve_i, np.int32, _PCURVE_I),
        "pcurve_d": _array(model.pcurve_d, np.float64, _PCURVE_D),
        "knot_nodes": _array(np.concatenate(model.knot_nodes) if model.knot_nodes else np.zeros(0, np.int32), np.int32, 1),
        "knot_uv": _array(np.concatenate(model.knot_uv) if model.knot_uv else np.zeros((0, 2)), np.float64, 2),
        "edge_i": _array(model.edge_i, np.int32, _EDGE_I), "edge_d": _array(model.edge_d, np.float64, _EDGE_D),
        "edge_poly_xyz": _array(np.concatenate([points for _, (points, _) in edge_poly]) if edge_poly else np.zeros((0, 3)),
                                np.float64, 3),
        "edge_poly_t": _array(np.concatenate([params for _, (_, params) in edge_poly]) if edge_poly else np.zeros(0),
                              np.float64, 1),
        "swept_i": _array(model.swept_i, np.int32, _SWEPT_I), "swept_d": _array(model.swept_d, np.float64, _SWEPT_D),
        "basis_i": _array(model.basis_i, np.int32, _EDGE_I), "basis_d": _array(model.basis_d, np.float64, _EDGE_D),
        "bsurface_i": _array(model.bsurface_i, np.int32, _BSURF_I), "bsurface_d": _array(model.bsurface_d, np.float64, _BSURF_D),
        "pole_d": _array(model.pole_d, np.float64, _POLE_D), "reals": _array(model.reals, np.float64, 1),
    }
    hook = _CALLBACK(callback)
    request = _Input(face_count=len(model.face_i), edge_count=len(model.edge_i), pcurve_count=len(model.pcurve_i),
                     bsurface_count=len(model.bsurface_i), swept_count=len(model.swept_i),
                     basis_count=len(model.basis_i), chord=chord, angle=angle, deflection=deflection,
                     repair_at=VERIFICATION["repairAt"], verified_at=VERIFICATION["verifiedAt"],
                     callback=hook, user=None,
                     **{name: array_.ctypes.data_as(_I32P if array_.dtype == np.int32 else _F64P)
                        for name, array_ in tables.items()})
    return request, (tables, hook)


def _run(model: _Model, *, chord: float, angle: float, deflection: float) -> dict:
    """The library's verdict and mesh of the model: ``meshes`` (per face by ordinal: positions,
    normals, triangles), ``polylines`` (per edge by ordinal), ``scale``, ``states`` (per face by
    ordinal: the library's FACE_* state), and ``report`` (per face by ordinal: who made it, the
    worst sample off the surface in deflections, and what verification found and did)."""
    library = _library()
    failure: list[BaseException] = []
    request, keep = _request(model, chord=chord, angle=angle, deflection=deflection, failure=failure)
    pointer = library.cadgen_tessellate(ctypes.byref(request))
    if not pointer:
        raise MemoryError("cadgen's mesh library could not allocate its result")
    try:
        result = pointer.contents
        if result.error:
            message = result.message.decode("utf-8", "replace")
            if failure:
                raise MeshProductionError(f"{message}: {type(failure[0]).__name__}: {failure[0]}") from failure[0]
            raise MeshProductionError(message)
        faces, edges = result.face_count, result.edge_count

        def copy(pointer_, shape, empty):
            return np.ctypeslib.as_array(pointer_, shape).copy() if shape[0] else empty

        states = copy(result.face_state, (faces,), np.zeros(0, np.int32))
        sources = copy(result.face_source, (faces,), np.zeros(0, np.int32))
        worst = copy(result.face_worst, (faces,), np.zeros(0, np.float64))
        reports = copy(result.face_report, (faces, _REPORT_N), np.zeros((0, _REPORT_N), np.int64))
        vertices = copy(result.face_vertices, (faces,), np.zeros(0, np.int64))
        triangles = copy(result.face_triangles, (faces,), np.zeros(0, np.int64))
        positions = copy(result.positions, (result.vertex_total, 3), np.zeros((0, 3), np.float32))
        normals = copy(result.normals, (result.vertex_total, 3), np.zeros((0, 3), np.float32))
        indices = copy(result.triangles, (result.triangle_total, 3), np.zeros((0, 3), np.uint32))
        counts = copy(result.edge_points, (edges,), np.zeros(0, np.int64))
        points = copy(result.points, (result.point_total, 3), np.zeros((0, 3), np.float64))
        scale = result.scale
    finally:
        library.cadgen_tessellate_free(pointer)
        del keep  # the tables it read and the callback it asked through, kept to here
    meshes = {}
    vertex_at = triangle_at = 0
    for index in range(faces):
        v, t = int(vertices[index]), int(triangles[index])
        if t:
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
    report = {index + 1: {"madeBy": _MADE_BY.get(int(sources[index]), "none"), "worst": float(worst[index]),
                          **dict(zip(_REPORT_FIELDS, reports[index].tolist()))}
              for index in range(faces)}
    return {"meshes": meshes, "polylines": polylines, "scale": scale,
            "states": {index + 1: int(states[index]) for index in range(faces)}, "report": report}


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


def _model(topods, face_map, edge_map) -> _Model:
    """The component's geometry as the library reads it."""
    model = _Model(face_map.Extent(), edge_map.Extent())
    _faces(model, face_map, edge_map)
    _edges(model, edge_map)
    return model


def mesh_component(topods, table: dict, face_colors: dict, *, part: str, chord: float, angle: float) -> bytes:
    """Mesh one unlocated component and return its GLB body, which names ``part``.

    ``topods`` is a private decode of the component's BREP: meshing stores OCCT's triangulation on
    it. ``table`` is the component's selector table (its faces' exact areas, its edges' exact
    lengths and classes) and ``face_colors`` its intrinsic face colours, by the same ordinals;
    ``chord`` is relative to the component's extent and ``angle`` in radians. A failure inside
    OCCT or the library is the component's ``MeshProductionError``.
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
                    report: dict | None = None, guard: Callable[[int], bool] | None = None) -> bytes:
    """``mesh_component``'s work. ``report``, when given, receives what the library found and did
    per face (``_run``'s ``report``), the faces it named and who meshed what; ``guard``, when given,
    decides by face ordinal which faces the library meshes instead of OCCT, in place of the cost
    guard (a test of the library's own tessellator on a whole solid)."""
    from cadgen.store.meshes import encode_payload

    face_map, edge_map = _maps(topods)
    table_faces = _rows(table, "faces", "area")
    table_edges = _rows(table, "edges", "visibilityClass", "length")
    if len(table_faces) != face_map.Extent() or len(table_edges) != edge_map.Extent():
        raise MeshProductionError(
            f"the component's BREP has {face_map.Extent()} faces and {edge_map.Extent()} edges, "
            f"its selector table {len(table_faces)} and {len(table_edges)}"
        )
    deflection = chord * _diagonal(topods)
    model = _model(topods, face_map, edge_map)
    if guard is not None:
        model.guarded = {index for index in range(face_map.Extent()) if guard(index + 1)} - model.unreadable
    meshed = _occt_mesh(topods, face_map, model.guarded | model.unreadable, deflection, angle)
    _take_meshes(model, meshed)
    _polygons(model, face_map, meshed, deflection)
    outcome = _run(model, chord=chord, angle=angle, deflection=deflection)
    meshes, polylines, states = outcome["meshes"], outcome["polylines"], outcome["states"]
    # A face too small for the mesh to resolve -- its area under the square of the deflection,
    # inside the error every triangle may carry, or its trim narrower than the tolerance it is
    # sampled to -- may come out empty without being named; any other empty face is named.
    unmeshed = [ordinal for ordinal, area in table_faces
                if states[ordinal] != _FACE_UNRESOLVED and float(area or 0.0) >= deflection * deflection
                and ordinal not in meshes]
    unverified = [ordinal for ordinal in sorted(states) if states[ordinal] == _FACE_UNVERIFIED and ordinal in meshes]
    if report is not None:
        report.update(faces=outcome["report"], unmeshed=unmeshed, unverified=unverified, deflection=deflection,
                      guarded=sorted(index + 1 for index in model.guarded),
                      refused=sorted(index + 1 for index in range(face_map.Extent())
                                     if model.mesh_i[index][0] == _MESH_OCCT and index not in meshed))

    position_parts, normal_parts, index_parts, face_ranges = [], [], [], []
    vertex_base = index_start = 0
    empty = (np.zeros((0, 3), np.float32), np.zeros((0, 3), np.float32), np.zeros((0, 3), np.uint32))
    for ordinal, _area in table_faces:
        positions, normals, triangles = meshes.get(ordinal, empty)
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
    for ordinal, visibility, _length in table_edges:
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
                          edges=edges_out, bounds=bounds, unmeshed_faces=unmeshed, unverified_faces=unverified)
