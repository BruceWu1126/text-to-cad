"""cadgen's tessellator draws a part as the CAD Viewer's tessellator drew it: closed, true to its BREP.

Every display mesh is ``cadgen._internal.tessellate``'s, the browser tessellator of cadgen 0.7.21 in
Python. A solid comes out closed (each edge of the welded mesh used by two triangles), wound
outward, with its volume and each face's area those of the exact BREP; three.js's earcut is the
one it triangulates with, triangle for triangle; and the faults it fixes in what it was ported
from stay fixed: a freeform pole, a tip whose row is one point, a straight run of trim through a
corner, a row of slots in line, a hole crossing a grid cell's side, and a face narrower than its
tolerance named as a hole.
"""

from __future__ import annotations

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


def _mesh(shape):
    """The shape's display mesh as the store holds it, decoded."""
    from cadgen._internal.selector_table import build_selector_table
    from cadgen._internal.tessellate import mesh_component
    from cadgen._internal.topology_extract import extract_topology_facts
    from cadgen.store.meshes import decode_payload
    from cadgen.tessellation_policy import DISPLAY_TESSELLATION

    topods = getattr(shape, "wrapped", shape)
    table = build_selector_table(topods, extract_topology_facts(topods))
    body = mesh_component(topods, table, {}, part="0" * 64 + "-v4", chord=DISPLAY_TESSELLATION["chordTolerance"],
                          angle=DISPLAY_TESSELLATION["angleTolerance"])
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


def _plane_face(u0: float, u1: float, v0: float, v1: float):
    """A planar face record in its own parameters, for driving the grid triangulation directly."""
    from cadgen._internal import tessellate

    class Plane:
        kind = "plane"

        def value(self, u: float, v: float) -> list[float]:
            return [u, v, 0.0]

        def normal(self, u: float, v: float, _box) -> list[float]:
            return [0.0, 0.0, 1.0]

    face = tessellate._Face()
    face.ord, face.reversed, face.surface, face.uv, face.loops = 1, False, Plane(), [u0, u1, v0, v1], []
    return face


def _ring(points):
    from cadgen._internal import tessellate

    ring = tessellate._Ring([list(point) for point in points])
    ring.ords, ring.metas = [0] * len(points), [None] * len(points)
    return ring


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
        shapes = {"gear": gear.part, "mixed": mixed, "torus": Torus(12, 4), "sphere": Sphere(7)}
        for name, shape in shapes.items():
            with self.subTest(name):
                mesh = _mesh(shape)
                positions = np.asarray(mesh.positions, np.float64)
                triangles = np.asarray(mesh.indices, np.int64).reshape(-1, 3)
                self.assertEqual([], list(mesh.unmeshed_faces))
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
        from cadgen._internal.earcut import triangulate_shape

        self.assertEqual(THREE_HOLE, triangulate_shape([[0, 0], [10, 0], [10, 10], [0, 10]], [HEXAGON]))
        self.assertEqual(THREE_NOTCHED, triangulate_shape([list(point) for point in NOTCHED], []))

    def test_a_corner_on_a_straight_run_of_trim_leaves_no_t_junction(self) -> None:
        # Earcut joins the notch's corner to the run it stands on with two triangles of no area
        # (the last two of THREE_NOTCHED). Dropped, they left the run's middle points on one
        # edge of the triangle above them, and the neighbouring face's points along it a crack.
        from cadgen._internal.tessellate import _grid_triangulate

        grid = _grid_triangulate(_plane_face(0, 3, 170, 210), [_ring(NOTCHED)], math.inf)
        corners = [grid.vertex_ids[(u, v)] for u, v in NOTCHED]
        outline = {frozenset((corners[i], corners[(i + 1) % len(corners)])) for i in range(len(corners))}
        uses: dict[frozenset, int] = {}
        for t in range(0, len(grid.triangles), 3):
            a, b, c = grid.triangles[t:t + 3]
            for edge in (frozenset((a, b)), frozenset((b, c)), frozenset((c, a))):
                uses[edge] = uses.get(edge, 0) + 1
        self.assertEqual(outline, {edge for edge, count in uses.items() if count == 1}, "the boundary is the trim")

    def test_a_trim_point_moved_onto_a_grid_line_is_still_pinned_to_its_edge(self) -> None:
        # A radial engine's cylinder whose trim runs 2.3 nm above its parameters' lowest point:
        # each grid line crossing it made a vertex the grid moved onto its bottom line (one source
        # ULP), past the tolerance pinning held vertices to their trim by, so they were never
        # pinned to the edge and the face beside it met them with a crack.
        from cadgen._internal import tessellate

        class Flat:
            kind = "other"

            def value(self, u: float, v: float) -> list[float]:
                return [u, v, 0.0]

        face = tessellate._Face()
        face.ord, face.reversed, face.surface, face.uv, face.loops, face.poles = 1, False, Flat(), [0, 8, -21, -2], [], []
        rise = -21.0 + 2.3e-6
        ring = _ring([[0.0, -21.0], [1.0, rise], [8.0, rise], [8.0, -2.0], [0.0, -2.0]])
        ring.ords = [1, 1, 2, 3, 4]
        ring.metas = [tessellate._Meta(1, 0.0, 0.125), tessellate._Meta(1, 0.125, 1.0), None, None, None]
        line = tessellate._Curve()
        line.kind, line.range, line.n, line.value = "line", (0.0, 1.0), None, lambda t: [8.0 * t, -21.0, 0.0]
        shared = {1: tessellate._sample_shared_edge(line, 1e-3)}

        grid = tessellate._grid_triangulate(face, [ring], math.inf, 1.0)
        us = np.array([u for u, _v in grid.uv_verts])
        vs = np.array([v for _u, v in grid.uv_verts])
        boundary: dict = {}
        tessellate._pin_boundary(grid, us, vs, [face.surface.value(u, v) for u, v in grid.uv_verts], boundary, shared)
        crossings = [i for i, (u, v) in enumerate(grid.uv_verts) if 1 < u < 8 and v < -20.99]
        self.assertEqual(6, len(crossings), "grid lines u = 2 to 7 cross the trim")
        self.assertEqual([], [grid.uv_verts[i] for i in crossings if 1 not in [ord_ for ord_, _f in boundary.get(i, [])]])

    def test_a_corner_moved_across_a_cell_border_is_pinned_to_both_its_edges(self) -> None:
        # A hand's palm whose trim turns a corner a hair left of a grid line: the grid moved the
        # corner onto the line, into the cell to its right, where only one of its two edges runs,
        # and pinning, held to that cell, labelled it with the one edge. The mesh edge along the
        # other was never split to its neighbour face's points.
        from cadgen._internal import tessellate

        class Flat:
            kind = "other"

            def value(self, u: float, v: float) -> list[float]:
                return [u, v, 0.0]

        face = tessellate._Face()
        face.ord, face.reversed, face.surface, face.uv, face.loops, face.poles = 1, False, Flat(), [0, 8, 0, 4], [], []
        corner = [4.0 - 1e-9, 2.0]
        ring = _ring([[1.0, 2.0], [1.0, 0.5], [7.0, 0.5], [7.0, 3.0], corner])
        ring.ords = [3, 4, 5, 1, 2]
        ring.metas = [None, None, None, tessellate._Meta(1, 0.0, 1.0), tessellate._Meta(2, 0.0, 1.0)]
        shared = {}
        for ordinal, (start, end) in ((1, ([7.0, 3.0], corner)), (2, (corner, [1.0, 2.0]))):
            line = tessellate._Curve()
            line.kind, line.range, line.n = "line", (0.0, 1.0), None
            line.value = lambda t, a=start, b=end: [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]), 0.0]
            shared[ordinal] = tessellate._sample_shared_edge(line, 1e-3)

        grid = tessellate._grid_triangulate(face, [ring], math.inf, 1.0)
        us = np.array([u for u, _v in grid.uv_verts])
        vs = np.array([v for _u, v in grid.uv_verts])
        boundary: dict = {}
        tessellate._pin_boundary(grid, us, vs, [face.surface.value(u, v) for u, v in grid.uv_verts], boundary, shared)
        [at] = [i for i, (u, v) in enumerate(grid.uv_verts) if abs(u - 4.0) < 1e-6 and abs(v - 2.0) < 1e-6]
        self.assertEqual(4.0, grid.uv_verts[at][0], "the grid moved the corner onto its line")
        self.assertEqual({1, 2}, {ord_ for ord_, _f in boundary.get(at, [])})

    def test_a_cell_a_hole_crosses_is_cut_where_the_face_is(self) -> None:
        # A cell of a plate whose round hole crosses its side, and one whose hole covers its
        # corner: what the cut covers is the cell less the hole, to the last digit.
        from cadgen._internal.earcut import triangulate_shape
        from cadgen._internal.tessellate import _cell_regions, _clip_polygon_to_cell, _covered, _polygon_area

        square = [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]]
        for cx, cy, r in ((0.5, 0.5, 1.0), (1.5, 1.0, 0.8), (1.0, 0.5, 0.69)):
            hole = [[cx + r * math.cos(-2 * math.pi * i / 48), cy + r * math.sin(-2 * math.pi * i / 48)] for i in range(48)]
            regions = _cell_regions([_ring(square), _ring(hole)], 0.5, 1.5, 0.0, 1.0)
            covered = sum(_covered(outline + [point for inner in holes for point in inner],
                                   triangulate_shape([list(point) for point in outline],
                                                     [[list(point) for point in inner] for inner in holes]))
                          for outline, holes in regions)
            exact = abs(_polygon_area(_clip_polygon_to_cell(square, 0.5, 1.5, 0.0, 1.0))) - abs(
                _polygon_area(_clip_polygon_to_cell(hole, 0.5, 1.5, 0.0, 1.0)))
            self.assertAlmostEqual(exact, covered, places=12)


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
            mesh_component(box, table, {}, part="0" * 64 + "-v4", chord=5e-4, angle=0.35)


if __name__ == "__main__":
    unittest.main()
