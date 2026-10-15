"""cadgen's mesh library on its own: the tessellator it meshes a face with where OCCT does not, and
the evaluation every verification stands on.

Every display mesh is ``cadgen._internal.tessellate``'s: OCCT's triangulation, verified and repaired
by cadgen's native library, which also meshes the faces OCCT refuses or the cost guard keeps from it.
Here that tessellator is made to mesh whole solids (every face guarded) and held to the contract: a
solid comes out closed (each edge of the welded mesh used by two triangles), wound outward, with its
volume and each face's area those of the exact BREP; the faults it fixes in what it was ported from
stay fixed: a freeform pole, a tip whose row is one point, a straight run of trim through a corner, a
trim point or corner the grid moved off its trim, a row of slots in line, a hole crossing a grid
cell's side, and a face narrower than its tolerance drawn as nothing. The B-splines, conics and
swept surfaces the library evaluates itself evaluate as OCCT evaluates them, point and normal; a
face's grid and its pinning are driven directly, through the library's probe of them, with its trim
given; and three.js's earcut is the one it triangulates with, triangle for triangle.
"""

from __future__ import annotations

import ctypes
import math
import unittest

import numpy as np

from tests.python.support.paths import add_repo_path

add_repo_path("packages/cadgen/src")

# Three.js's ShapeUtils.triangulateShape on each contour (three 0.186.1): a square with a hexagonal
# hole, and the L of a cylinder face whose notch puts a corner on a straight run of its trim.
HEXAGON = [[5 + 2 * math.cos(-i * math.pi / 3), 5 + 2 * math.sin(-i * math.pi / 3)] for i in range(6)]
THREE_HOLE = [[0, 7, 8], [6, 7, 0], [3, 0, 8], [5, 6, 0], [2, 3, 8], [5, 0, 1], [2, 8, 9], [4, 5, 1], [1, 2, 9],
              [9, 4, 1]]
NOTCHED = [[1.614573175190183, 205.20562748477136], [1.7074157383752555, 205.20562748477136],
           [1.8002583015603282, 205.20562748477136], [1.8931008647454006, 205.20562748477136],
           [1.9859434279307142, 205.20562748477136], [1.9859434279307142, 202.20562748477136],
           [1.9682306659351254, 202.20562748477138], [1.9682306659351223, 176.70562748477136],
           [1.9004099376670602, 176.70562748477136], [1.8325892093989973, 176.70562748477136],
           [1.7647684811309343, 176.70562748477136], [1.6969477528628756, 176.70562748477136],
           [1.6969477528628785, 202.20562748477136], [1.6351668196084361, 202.20562748477136],
           [1.614573175190183, 202.20562748477136]]
THREE_NOTCHED = [[1, 0, 14], [12, 11, 10], [8, 7, 6], [6, 5, 4], [2, 1, 14], [12, 10, 9], [9, 8, 6], [6, 4, 3],
                 [3, 2, 14], [12, 9, 6], [6, 3, 14], [13, 12, 6], [6, 14, 13]]
# An F-14D tail's tip: three rulings' curves between one tip and one end (their poles between the
# two), of which each pair of neighbours spans a face.
TIP = (10197.13014247, -6822.580175597, 188.88269353019)
END = (10718.79328787, -6822.580175597, 162.36234147519)
RULINGS = [
    [(10242.58769661, -6887.709318404, 141.43950596893), (10381.30838298, -6797.190512061, 177.72103638234),
     (10363.24001077, -6933.381924877, 109.39172175973), (10539.37240266, -6798.507714813, 169.98554112507),
     (10544.50776362, -6871.679130125, 136.80404909617), (10654.02536321, -6845.015407374, 147.8514997435)],
    [(10242.68511232, -6872.008417081, 132.26548425969), (10381.27040685, -6803.311283572, 181.29739735446),
     (10363.40574044, -6906.670574828, 93.784305332551), (10539.33639671, -6804.310943829, 173.37636232041),
     (10544.58120248, -6859.84268016, 129.88802263952), (10654.0589203, -6839.606870656, 144.69129688358)],
    [(10242.75338225, -6853.434926026, 125.83623558883), (10381.2437928, -6810.551893048, 183.80374786842),
     (10363.5218855, -6875.072196431, 82.846467544563), (10539.31116338, -6811.175914192, 175.75268465245),
     (10544.63266918, -6845.840668872, 125.04120024116), (10654.08243744, -6833.20880431, 142.47659425032)],
]


def _part() -> str:
    """A part key the meshes name: any will do, at this cadgen's part version."""
    from cadgen.store.parts import PART_VERSION

    return "0" * 64 + f"-v{PART_VERSION}"


def _mesh(shape, *, library: bool = True, report: dict | None = None):
    """The shape's display mesh as the store holds it, decoded -- every face meshed by the library's
    own tessellator when ``library`` (the fallback, made the primary for the test), else as the store
    meshes it."""
    from cadgen._internal.selector_table import build_selector_table
    from cadgen._internal.tessellate import _mesh_component
    from cadgen._internal.topology_extract import extract_topology_facts
    from cadgen.store.meshes import decode_payload
    from cadgen.tessellation_policy import DISPLAY_TESSELLATION

    topods = getattr(shape, "wrapped", shape)
    table = build_selector_table(topods, extract_topology_facts(topods))
    body = _mesh_component(topods, table, {}, part=_part(), chord=DISPLAY_TESSELLATION["chordTolerance"],
                           angle=DISPLAY_TESSELLATION["angleTolerance"], report=report,
                           guard=(lambda _ordinal: True) if library else None)
    return decode_payload(body)


def _odd_edges(positions: np.ndarray, triangles: np.ndarray) -> int:
    """Edges of the mesh welded where its float32 vertices are one point that an odd number of
    triangles use: a crack, a T-junction or a hole."""
    _, label = np.unique(np.ascontiguousarray(positions, np.float32).view(np.dtype((np.void, 12))).ravel(),
                         return_inverse=True)
    welded = label.ravel()[triangles]
    edges = np.sort(np.concatenate([welded[:, [0, 1]], welded[:, [1, 2]], welded[:, [2, 0]]]), axis=1)
    _keys, counts = np.unique(edges[:, 0] * (int(label.max()) + 1) + edges[:, 1], return_counts=True)
    return int((counts % 2).sum())


def _probe_face(surface: str, box, loop, lines=(), *, chord_limit=math.inf, angle_limit=math.inf):
    """One face's grid and its vertices pinned to its component's edges, by the library's probe of
    them (``cadgen_tessellate_pinned``), its trim given rather than sampled: (each vertex's (u, v),
    the edges it is pinned to, the triangles). ``surface`` is "plane", the XY plane in its own
    parameters, or "flat", the same plane as a surface the library has no formula for, which it
    snaps a vertex on as it does on a curved one. ``loop`` is its points (u, v, edge, fractions):
    the segment from each to the next runs along that model edge (0 for none) at those fractions
    (f0, f1) of its polyline, or None where it runs along no shared edge; ``lines`` each edge's
    straight 3D curve (start, end), by ordinal, sampled to 1e-3."""
    from cadgen._internal import tessellate as T

    model = T._Model(1, max([len(lines)] + [point[2] for point in loop]))
    kind = T._ANALYTIC["plane"] if surface == "plane" else T._S_HOST
    model.face_i[0] = [1, 0, kind, -1, 0, 0, 0, 0]
    model.face_d[0] = [*box, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    model.surfaces[0] = (lambda u, v: (u, v, 0.0), lambda u, v, _box: (0.0, 0.0, 1.0))
    for ordinal, line in enumerate(lines, 1):
        if line is not None:
            start, end = line
            model.edge_i[ordinal - 1] = [T._C_LINE, 0, -1, 0, 0, 0, 0, 0, 0, 0, 0]
            model.edge_d[ordinal - 1] = [0.0, 1.0, *start, *[0.0] * 6, *(b - a for a, b in zip(start, end)), 0.0,
                                         0.0]
    failure: list = []
    request, keep = T._request(model, chord=1e-3, angle=math.inf, deflection=1e-3, failure=failure)
    probe = T._library().cadgen_tessellate_pinned
    double, int32 = ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_int32)
    probe.restype = ctypes.c_int32
    probe.argtypes = [ctypes.POINTER(T._Input), ctypes.c_int32, double, int32, ctypes.c_int32, int32, double,
                      ctypes.c_double, ctypes.c_double, double, int32, int32, ctypes.c_int64,
                      ctypes.POINTER(ctypes.c_int64)]
    points = np.array([point[:2] for point in loop], np.float64)
    ords = np.array([point[2] for point in loop], np.int32)
    fractions = np.array([point[3] or (math.nan, math.nan) for point in loop], np.float64)
    sizes = np.array([len(loop)], np.int32)
    capacity = 100_000
    uv, pinned, triangles = np.zeros((capacity, 2)), np.zeros((capacity, 2), np.int32), np.zeros((capacity, 3), np.int32)
    counts = (ctypes.c_int64 * 2)()
    status = probe(ctypes.byref(request), 0, points.ctypes.data_as(double), sizes.ctypes.data_as(int32), 1,
                   ords.ctypes.data_as(int32), fractions.ctypes.data_as(double), chord_limit, angle_limit,
                   uv.ctypes.data_as(double), pinned.ctypes.data_as(int32), triangles.ctypes.data_as(int32),
                   capacity, counts)
    del keep
    assert status == 0 and not failure, (status, failure)
    return uv[:counts[0]], pinned[:counts[0]], triangles[:counts[1]]


def _evaluate(model, what: int, index: int, params: np.ndarray) -> np.ndarray:
    """The library's own evaluation (``cadgen_tessellate_probe``) of a face's surface point or normal,
    or an edge's point, at each row of ``params`` (u, v) or (t, 0): one point per row."""
    from cadgen._internal import tessellate as T

    failure: list = []
    request, keep = T._request(model, chord=5e-4, angle=0.35, deflection=1e-3, failure=failure)
    probe = T._library().cadgen_tessellate_probe
    double = ctypes.POINTER(ctypes.c_double)
    probe.restype = ctypes.c_int32
    probe.argtypes = [ctypes.POINTER(T._Input), ctypes.c_int32, ctypes.c_int32, ctypes.c_int32, double, double]
    params = np.ascontiguousarray(params, np.float64)
    out = np.zeros((len(params), 3), np.float64)
    status = probe(ctypes.byref(request), what, index, len(params), params.ctypes.data_as(double),
                   out.ctypes.data_as(double))
    del keep
    assert status == 0 and not failure, (status, failure)
    return out


def _earcut(contour, holes) -> list[list[int]]:
    """The library's earcut (``cadgen_tessellate_earcut``) of a contour and its holes."""
    from cadgen._internal import tessellate as T

    earcut = T._library().cadgen_tessellate_earcut
    double, int32 = ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_int32)
    earcut.restype = ctypes.c_int32
    earcut.argtypes = [double, ctypes.c_int32, int32, ctypes.c_int32, int32, ctypes.c_int32]
    points = np.array([*contour, *[point for hole in holes for point in hole]], np.float64)
    starts = np.array(np.cumsum([len(contour)] + [len(hole) for hole in holes])[:-1], np.int32)
    out = np.zeros(3 * len(points) * 3, np.int32)
    made = earcut(points.ctypes.data_as(double), len(points), starts.ctypes.data_as(int32), len(holes),
                  out.ctypes.data_as(int32), len(out))
    assert made >= 0
    return out[:made].reshape(-1, 3).tolist()


def _freeform_solids() -> dict:
    """Solids whose surfaces, trims and edges the library evaluates itself: a loft (a B-spline
    surface of degree 14), a spline and an elliptical hole extruded, a spline revolved, and a
    filleted plate round a hole with every surface, trim and edge made a B-spline (its hole and
    rounds rational ones)."""
    from build123d import Axis, Box, BuildLine, BuildPart, BuildSketch, Circle, Cylinder, Ellipse, Locations, Mode, \
        Plane, Polyline, Solid, Spline, extrude, fillet, loft, make_face, revolve
    from OCP.BRepBuilderAPI import BRepBuilderAPI_NurbsConvert

    with BuildPart() as vase:
        for z, radius in ((0, 10), (8, 14), (16, 8), (24, 11)):
            with BuildSketch(Plane.XY.offset(z)):
                Circle(radius)
        loft()
    with BuildPart() as cam:
        with BuildSketch():
            with BuildLine():
                Spline((0, 0), (10, 4), (20, -2), (30, 3))
                Polyline((30, 3), (30, -12), (0, -12), (0, 0))
            make_face()
            with Locations((15, -5)):
                Ellipse(6, 3, mode=Mode.SUBTRACT)
        extrude(amount=6)
    with BuildPart() as bell:
        with BuildSketch(Plane.XZ):
            with BuildLine():
                Spline((0, 0), (8, 2), (6, 10), (9, 18))
                Polyline((9, 18), (0, 18), (0, 0))
            make_face()
        revolve(axis=Axis.Z)
    plate = fillet((Box(40, 30, 12) - Cylinder(5, 20)).edges().group_by()[-1], 2)
    nurbs = Solid(BRepBuilderAPI_NurbsConvert(plate.wrapped, True).Shape())
    return {"vase": vase.part, "cam": cam.part, "bell": bell.part, "plate": nurbs}


class SolidsTests(unittest.TestCase):
    def test_a_solid_is_closed_wound_outward_and_true_to_its_brep(self) -> None:
        from build123d import Box, BuildPart, BuildSketch, Cylinder, Location, Mode, Plane, Polygon, Sphere, Torus, \
            extrude, fillet
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        from OCP.TopAbs import TopAbs_FACE
        from OCP.TopExp import TopExp
        from OCP.TopTools import TopTools_IndexedMapOfShape

        with BuildPart() as gear:  # sixteen teeth round a bore, the shape of a sun gear
            with BuildSketch(Plane.XY):
                Polygon(*[(r * math.cos(math.pi * k / 16), r * math.sin(math.pi * k / 16))
                          for k, r in enumerate([20, 24] * 16)])
            extrude(amount=8)
            Cylinder(6, 20, mode=Mode.SUBTRACT)
        plate = Box(40, 30, 12) - Cylinder(5, 20)
        mixed = fillet(plate.edges().group_by()[-1], 2) + Sphere(6).moved(Location((12, 0, 6)))
        shapes = {"gear": gear.part, "mixed": mixed, "torus": Torus(12, 4), "sphere": Sphere(7), **_freeform_solids()}
        for name, shape in shapes.items():
            with self.subTest(name):
                report: dict = {}
                mesh = _mesh(shape, report=report)
                self.assertEqual({"library"}, {facts["madeBy"] for facts in report["faces"].values()},
                                 "every face the library's own")
                positions = np.asarray(mesh.positions, np.float64)
                triangles = np.asarray(mesh.indices, np.int64).reshape(-1, 3)
                self.assertEqual([], list(mesh.unmeshed_faces))
                self.assertEqual([], list(mesh.unverified_faces), "every face on its surface")
                self.assertEqual(0, _odd_edges(mesh.positions, triangles), "the mesh is closed")
                a, b, c = (positions[triangles[:, k]] for k in range(3))
                volume = float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6)
                self.assertAlmostEqual(1.0, volume / shape.volume, delta=5e-3, msg="wound outward, its volume")
                facets = np.cross(b - a, c - a)
                normals = np.asarray(mesh.normals, np.float64)[triangles].sum(axis=1)
                self.assertGreater(float(np.mean(np.einsum("ij,ij->i", facets, normals) > 0)), 0.99,
                                   "the normals face the way the triangles wind")
                faces = TopTools_IndexedMapOfShape()
                TopExp.MapShapes_s(shape.wrapped, TopAbs_FACE, faces)
                for ordinal, start, count, _colour in mesh.faces.tolist():
                    props = GProp_GProps()
                    BRepGProp.SurfaceProperties_s(faces.FindKey(ordinal), props)
                    t = triangles[start // 3:(start + count) // 3]
                    area = float(np.linalg.norm(np.cross(positions[t[:, 1]] - positions[t[:, 0]],
                                                         positions[t[:, 2]] - positions[t[:, 0]]), axis=1).sum() / 2)
                    self.assertAlmostEqual(1.0, area / props.Mass(), delta=1e-2, msg=f"face {ordinal}'s area")

    def test_a_freeform_pole_is_one_point_with_the_poles_own_normal(self) -> None:
        # A sphere as a B-spline surface, its poles collapsed rows on the axis, as an aircraft's
        # skin closes at its nose: OCCT evaluates each row with its own rounding, a hair off zero,
        # and its derivatives there have no cross product. The pole once came out as a fan of
        # slivers on no surface, and its normals as nothing, which refinement split on and on.
        from build123d import Solid, Sphere
        from OCP.BRepBuilderAPI import BRepBuilderAPI_NurbsConvert

        radius = 30.0
        mesh = _mesh(Solid(BRepBuilderAPI_NurbsConvert(Sphere(radius).wrapped, True).Shape()))
        positions = np.asarray(mesh.positions, np.float64)
        normals = np.asarray(mesh.normals, np.float64)
        triangles = np.asarray(mesh.indices, np.int64).reshape(-1, 3)
        self.assertEqual(0, _odd_edges(mesh.positions, triangles))
        poles = np.abs(np.abs(positions[:, 2]) - radius) < 1e-9
        self.assertTrue(poles.any())
        self.assertTrue(np.allclose(normals[poles], np.sign(positions[poles, 2])[:, None] * [0, 0, 1], atol=1e-3),
                        "each pole's normal is the axis")
        analytic = len(np.asarray(_mesh(Sphere(radius)).indices)) // 3
        self.assertLess(len(triangles), 2 * analytic, "refined as the sphere it is, not on and on at its poles")

    def test_the_library_evaluates_its_surfaces_and_curves_as_occt_does(self) -> None:
        # Verification stands on the library's own evaluation of every surface and edge OCCT
        # meshed: each B-spline by its poles and knots, each conic by its formula, each surface of
        # extrusion or revolution by the curve it sweeps. Point for point and normal for normal,
        # over a grid of each face's parameters and along each edge, it is OCCT's.
        from cadgen._internal import tessellate as T
        from OCP.BRep import BRep_Tool
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.GeomLProp import GeomLProp_SLProps
        from OCP.TopoDS import TopoDS

        for name, shape in _freeform_solids().items():
            with self.subTest(name):
                face_map, edge_map = T._maps(shape.wrapped)
                model = T._model(shape.wrapped, face_map, edge_map)
                extent = shape.bounding_box().diagonal
                for index in range(face_map.Extent()):
                    u0, u1, v0, v1 = model.face_d[index, 0:4]
                    us, vs = np.meshgrid(np.linspace(u0, u1, 7), np.linspace(v0, v1, 7))
                    params = np.column_stack([us.ravel(), vs.ravel()])
                    surface = BRep_Tool.Surface_s(TopoDS.Face_s(face_map.FindKey(index + 1)))
                    expected = np.array([surface.Value(u, v).Coord() for u, v in params.tolist()])
                    points = _evaluate(model, T._ASK_SURFACE, index, params)
                    self.assertLess(float(np.abs(points - expected).max()), extent * 1e-9, f"face {index + 1}'s points")
                    normals = _evaluate(model, T._ASK_NORMAL, index, params)
                    for (u, v), normal in zip(params.tolist(), normals):
                        props = GeomLProp_SLProps(surface, u, v, 1, 1e-9)
                        if not props.IsNormalDefined():
                            continue
                        theirs = np.array(props.Normal().Coord())
                        self.assertGreater(abs(float(normal @ theirs)), 1 - 1e-7, f"face {index + 1}'s normal at {u}, {v}")
                for index in range(edge_map.Extent()):
                    if not model.edge_i[index][0]:
                        continue
                    curve = BRepAdaptor_Curve(TopoDS.Edge_s(edge_map.FindKey(index + 1)))
                    first, last = model.edge_d[index, 0:2]
                    params = np.column_stack([np.linspace(first, last, 9), np.zeros(9)])
                    points = _evaluate(model, T._ASK_CURVE, index, params)
                    # A B-spline's parameters are the library's conversion's; the ends and the
                    # points along it are the curve's own where the conversion keeps them (lines,
                    # conics, B-splines), which is every kind here.
                    expected = np.array([curve.Value(float(t)).Coord() for t in params[:, 0]])
                    self.assertLess(float(np.abs(points - expected).max()), extent * 1e-9, f"edge {index + 1}")

    def test_a_tip_whose_row_is_one_point_meets_its_neighbour_along_their_edge(self) -> None:
        # Two faces of an F-14D's tail, ruled B-splines whose rows close to one point at each end,
        # sharing the ruling between them. OCCT evaluates a face's tip row to exactly that point,
        # so the triangles that carried the face's boundary into the tip were one line in space and
        # were dropped before the faces were made to meet: the triangle beyond spanned the shared
        # edge from the tip without the point its neighbour put on it, a gap 0.2 mm wide.
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_Sewing
        from OCP.Geom import Geom_BSplineSurface
        from OCP.TColgp import TColgp_Array2OfPnt
        from OCP.TColStd import TColStd_Array1OfInteger, TColStd_Array1OfReal
        from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
        from OCP.TopExp import TopExp
        from OCP.TopoDS import TopoDS
        from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
        from OCP.gp import gp_Pnt

        sewing = BRepBuilderAPI_Sewing(1e-6)
        for column, (u0, u1) in enumerate(((3.0, 4.0), (4.0, 5.0))):
            poles = TColgp_Array2OfPnt(1, 2, 1, 8)
            for side in range(2):
                for row, point in enumerate([TIP, *RULINGS[column + side], END]):
                    poles.SetValue(side + 1, row + 1, gp_Pnt(*point))
            knots = []
            for values, multiplicity in (((u0, u1), 2), ((0.0, 1.0), 8)):
                array, mults = TColStd_Array1OfReal(1, 2), TColStd_Array1OfInteger(1, 2)
                for k, value in enumerate(values, 1):
                    array.SetValue(k, value)
                    mults.SetValue(k, multiplicity)
                knots += [array, mults]
            surface = Geom_BSplineSurface(poles, knots[0], knots[2], knots[1], knots[3], 1, 7)
            sewing.Add(BRepBuilderAPI_MakeFace(surface, 1e-7).Face())
        sewing.Perform()
        shape = sewing.SewedShape()
        owners = TopTools_IndexedDataMapOfShapeListOfShape()
        TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, owners)
        [shared] = [owners.FindKey(i) for i in range(1, owners.Extent() + 1) if owners.FindFromIndex(i).Size() == 2]
        curve = BRepAdaptor_Curve(TopoDS.Edge_s(shared))
        line = np.array([[p.X(), p.Y(), p.Z()] for p in (curve.Value(t) for t in
                         np.linspace(curve.FirstParameter(), curve.LastParameter(), 4001))])

        mesh = _mesh(shape)
        positions = np.asarray(mesh.positions, np.float32)
        _, label = np.unique(positions.view(np.dtype((np.void, 12))).ravel(), return_inverse=True)
        welded = label.ravel()[np.asarray(mesh.indices, np.int64).reshape(-1, 3)]
        points = np.zeros((int(label.max()) + 1, 3))
        points[label.ravel()] = positions
        edges = np.sort(np.concatenate([welded[:, [0, 1]], welded[:, [1, 2]], welded[:, [2, 0]]]), axis=1)
        pairs, counts = np.unique(edges, axis=0, return_counts=True)
        ends = points[pairs[counts % 2 == 1]]
        deflection = 5e-4 * float(np.linalg.norm(positions.max(axis=0) - positions.min(axis=0)))
        on_shared = np.linalg.norm(ends[:, :, None, :] - line[None, None], axis=3).min(axis=2) <= deflection
        self.assertEqual(0, int(on_shared.all(axis=1).sum()), "no edge along the shared ruling is left open")

    def test_a_row_of_slots_whose_ends_line_up_leaves_the_wall_closed(self) -> None:
        # A watch case's ring of slots: every slot's top corners lie exactly on one line of the
        # wall's parameters, and earcut ran diagonals along that line straight past them, so the
        # triangles above met the ones below at other points and the wall opened along the line.
        from build123d import Axis, Box, Compound, Cylinder, Location

        ring = Cylinder(19.3, 2.2) - Cylinder(18.5, 3)
        slots = [Box(3, 0.16, 1.1).moved(Location((19, 0, 0))).rotate(Axis.Z, 6 * k) for k in range(60)]
        mesh = _mesh(ring - Compound(slots))
        self.assertEqual(0, _odd_edges(mesh.positions, np.asarray(mesh.indices, np.int64).reshape(-1, 3)))

    def test_a_hole_crossing_the_side_of_a_grid_cell_is_cut_out_of_it(self) -> None:
        # The windows through a sphere cross many cells of its grid; clipped loop by loop, each
        # cell's piece of a window touches the cell's outline, and earcut covered part of the
        # window and left part of the sphere bare: a face 1.1% off its area.
        from build123d import Box, Sphere
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        from OCP.TopAbs import TopAbs_FACE
        from OCP.TopExp import TopExp
        from OCP.TopTools import TopTools_IndexedMapOfShape

        shape = Sphere(10) - Box(12, 30, 9)
        mesh = _mesh(shape)
        positions = np.asarray(mesh.positions, np.float64)
        triangles = np.asarray(mesh.indices, np.int64).reshape(-1, 3)
        self.assertEqual(0, _odd_edges(mesh.positions, triangles))
        faces = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(shape.wrapped, TopAbs_FACE, faces)
        for ordinal, start, count, _colour in mesh.faces.tolist():
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(faces.FindKey(ordinal), props)
            t = triangles[start // 3:(start + count) // 3]
            area = float(np.linalg.norm(np.cross(positions[t[:, 1]] - positions[t[:, 0]],
                                                 positions[t[:, 2]] - positions[t[:, 0]]), axis=1).sum() / 2)
            self.assertAlmostEqual(1.0, area / props.Mass(), delta=5e-3, msg=f"face {ordinal}'s area")


class TriangulationTests(unittest.TestCase):
    def test_earcut_triangulates_as_three_does(self) -> None:
        self.assertEqual(THREE_HOLE, _earcut([[0, 0], [10, 0], [10, 10], [0, 10]], [HEXAGON]))
        self.assertEqual(THREE_NOTCHED, _earcut(NOTCHED, []))

    def test_a_corner_on_a_straight_run_of_trim_leaves_no_t_junction(self) -> None:
        # Earcut joins the notch's corner to the run it stands on with two triangles of no area
        # (the last two of THREE_NOTCHED). Dropped, they left the run's middle points on one
        # edge of the triangle above them, and the neighbouring face's points along it a crack.
        uv, _pinned, triangles = _probe_face("plane", (0, 3, 170, 210), [(u, v, 0, None) for u, v in NOTCHED])
        vertex = {(u, v): i for i, (u, v) in enumerate(uv.tolist())}
        corners = [vertex[(u, v)] for u, v in NOTCHED]
        outline = {frozenset((corners[i], corners[(i + 1) % len(corners)])) for i in range(len(corners))}
        uses: dict[frozenset, int] = {}
        for a, b, c in triangles.tolist():
            for edge in (frozenset((a, b)), frozenset((b, c)), frozenset((c, a))):
                uses[edge] = uses.get(edge, 0) + 1
        self.assertEqual(outline, {edge for edge, count in uses.items() if count == 1}, "the boundary is the trim")

    def test_a_trim_point_moved_onto_a_grid_line_is_still_pinned_to_its_edge(self) -> None:
        # A radial engine's cylinder whose trim runs 2.3 nm above its parameters' lowest point:
        # each grid line crossing it made a vertex the grid moved onto its bottom line (one source
        # ULP), past the tolerance pinning held vertices to their trim by, so they were never
        # pinned to the edge and the face beside it met them with a crack.
        rise = -21.0 + 2.3e-6
        loop = [(0.0, -21.0, 1, (0.0, 0.125)), (1.0, rise, 1, (0.125, 1.0)), (8.0, rise, 2, None),
                (8.0, -2.0, 3, None), (0.0, -2.0, 4, None)]
        uv, pinned, _triangles = _probe_face("flat", (0, 8, -21, -2), loop, [((0.0, -21.0, 0.0), (8.0, -21.0, 0.0))],
                                             angle_limit=1.0)
        crossings = [i for i, (u, v) in enumerate(uv.tolist()) if 1 < u < 8 and v < -20.99]
        self.assertEqual(6, len(crossings), "grid lines u = 2 to 7 cross the trim")
        self.assertEqual([], [uv[i].tolist() for i in crossings if 1 not in pinned[i]])

    def test_a_corner_moved_across_a_cell_border_is_pinned_to_both_its_edges(self) -> None:
        # A hand's palm whose trim turns a corner a hair left of a grid line: the grid moved the
        # corner onto the line, into the cell to its right, where only one of its two edges runs,
        # and pinning, held to that cell, labelled it with the one edge. The mesh edge along the
        # other was never split to its neighbour face's points.
        corner = (4.0 - 1e-9, 2.0)
        loop = [(1.0, 2.0, 3, None), (1.0, 0.5, 4, None), (7.0, 0.5, 5, None), (7.0, 3.0, 1, (0.0, 1.0)),
                (*corner, 2, (0.0, 1.0))]
        lines = [((7.0, 3.0, 0.0), (*corner, 0.0)), ((*corner, 0.0), (1.0, 2.0, 0.0))]
        uv, pinned, _triangles = _probe_face("flat", (0, 8, 0, 4), loop, lines, angle_limit=1.0)
        [at] = [i for i, (u, v) in enumerate(uv.tolist()) if abs(u - 4.0) < 1e-6 and abs(v - 2.0) < 1e-6]
        self.assertEqual(4.0, uv[at][0], "the grid moved the corner onto its line")
        self.assertEqual({1, 2}, set(pinned[at].tolist()))


class UnmeshedFaceTests(unittest.TestCase):
    def test_a_face_narrower_than_its_tolerance_is_drawn_as_nothing_and_not_named(self) -> None:
        # A lens 1 um across between an arc and its chord, 10 mm long: its trim samples to its
        # two corners at the tolerance, which no mesh resolves, so it is no hole to warn about.
        from build123d import Face, Line, ThreePointArc, Wire

        lens = Face(Wire([Line((0, 0), (10, 0)), ThreePointArc((10, 0), (5, 0.001), (0, 0))]))
        mesh = _mesh(lens)
        self.assertEqual(0, len(mesh.indices))
        self.assertEqual([], list(mesh.unmeshed_faces))

    def test_a_component_whose_table_is_another_shapes_is_refused(self) -> None:
        from build123d import Box, Cylinder
        from cadgen._internal.selector_table import build_selector_table
        from cadgen._internal.tessellate import MeshProductionError, mesh_component
        from cadgen._internal.topology_extract import extract_topology_facts

        box, cylinder = Box(1, 2, 3).wrapped, Cylinder(1, 2).wrapped
        table = build_selector_table(cylinder, extract_topology_facts(cylinder))
        with self.assertRaisesRegex(MeshProductionError, "selector table"):
            mesh_component(box, table, {}, part=_part(), chord=5e-4, angle=0.35)


if __name__ == "__main__":
    unittest.main()
