"""The mesh contract: what every display mesh cadgen stores must be, whichever algorithm made it.

README law 1 and STORE.md §3: a part's display mesh is closed (each edge of the welded mesh used by
an even number of triangles), covers every face of its BREP that the tolerance can resolve and
nothing outside it (each face's area is the exact face's, no face is left silently empty), lies on
its exact surfaces within the display tolerance (its vertices and the interior of its triangles
alike), is wound outward with its normals the surfaces' own, and has no folded or degenerate
triangle. A solid of every surface kind cadgen meets -- planes and cylinders round a hole, fillets,
a sphere and a torus closing on themselves, a cone to its apex, a loft of degree 14, a spline and
an ellipse extruded, a spline revolved, a plate made of rational B-splines, and windows cut through
a sphere -- is built here and held to each clause through the store's own mesher, at the one
display tolerance. The plate is meshed twice more with every curved face the library's own: once
dropped from what OCCT meshed, as a face it refused, once kept from OCCT as the cost guard keeps a
face; either way the library meshes it over the polylines its planar neighbours fixed, and the
solid is held to every clause as before. The scoreboard (the repository's manual benchmark)
measures the same clauses over a corpus of real models; this test says they hold at all.
"""

from __future__ import annotations

import contextlib
import math
import unittest
from unittest import mock

import numpy as np

from tests.python.support.paths import add_repo_path

add_repo_path("packages/cadgen/src")

SAMPLE = 24  # the longest triangles of each face whose interiors are projected onto the surface
AREA_TOLERANCE = 1e-2
TRIANGLE_BUDGET = 4.0  # against OCCT's BRepMesh at the same tolerances
# A triangle is folded when its facet points against the normal it is drawn with by more than the
# rounding of a sliver seen edge-on: the cosine between them below this.
FOLDED_COSINE = -1e-3


def _shapes() -> dict:
    """The solids, one of each surface kind, as build123d shapes."""
    from build123d import Axis, Box, BuildLine, BuildPart, BuildSketch, Circle, Cone, Cylinder, Ellipse, \
        Location, Locations, Mode, Plane, Polygon, Polyline, Solid, Sphere, Spline, Torus, extrude, fillet, loft, \
        make_face, revolve
    from OCP.BRepBuilderAPI import BRepBuilderAPI_NurbsConvert

    with BuildPart() as gear:  # sixteen teeth round a bore
        with BuildSketch(Plane.XY):
            Polygon(*[(r * math.cos(math.pi * k / 16), r * math.sin(math.pi * k / 16))
                      for k, r in enumerate([20, 24] * 16)])
        extrude(amount=8)
        Cylinder(6, 20, mode=Mode.SUBTRACT)
    with BuildPart() as vase:  # a loft through circles: a B-spline surface of degree 14
        for z, radius in ((0, 10), (8, 14), (16, 8), (24, 11)):
            with BuildSketch(Plane.XY.offset(z)):
                Circle(radius)
        loft()
    with BuildPart() as cam:  # a spline and an elliptical hole, extruded
        with BuildSketch():
            with BuildLine():
                Spline((0, 0), (10, 4), (20, -2), (30, 3))
                Polyline((30, 3), (30, -12), (0, -12), (0, 0))
            make_face()
            with Locations((15, -5)):
                Ellipse(6, 3, mode=Mode.SUBTRACT)
        extrude(amount=6)
    with BuildPart() as bell:  # a spline, revolved
        with BuildSketch(Plane.XZ):
            with BuildLine():
                Spline((0, 0), (8, 2), (6, 10), (9, 18))
                Polyline((9, 18), (0, 18), (0, 0))
            make_face()
        revolve(axis=Axis.Z)
    plate = fillet((Box(40, 30, 12) - Cylinder(5, 20)).edges().group_by()[-1], 2)
    return {
        "plate": plate + Sphere(6).moved(Location((12, 0, 6))),
        "gear": gear.part,
        "sphere": Sphere(7),
        "torus": Torus(12, 4),
        "cone": Cone(10, 0, 20),
        "vase": vase.part,
        "cam": cam.part,
        "bell": bell.part,
        "nurbs plate": Solid(BRepBuilderAPI_NurbsConvert(plate.wrapped, True).Shape()),
        "windows": Sphere(10) - Box(12, 30, 9),
    }


def _curved_faces(topods) -> set[int]:
    """The ordinals of the solid's faces on a curved surface."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_SurfaceType as T
    from OCP.TopoDS import TopoDS

    faces = _face_map(topods)
    return {ordinal for ordinal in range(1, faces.Extent() + 1)
            if BRepAdaptor_Surface(TopoDS.Face_s(faces.FindKey(ordinal)), False).GetType() != T.GeomAbs_Plane}


def _refusing(real, ordinals: set[int]):
    """``tessellate._occt_mesh`` with ``ordinals`` dropped from what OCCT meshed, as faces it refused."""
    def occt_mesh(topods, face_map, skip, deflection, angle):
        return {index: block for index, block in real(topods, face_map, skip, deflection, angle).items()
                if index + 1 not in ordinals}
    return occt_mesh


def _mesh(topods, *, library: set[int] = frozenset(), refused: bool = False):
    """The solid's display mesh as the store derives it, decoded, with its deflection in millimetres
    and what the library found and did per face (``tessellate._mesh_component``'s report).
    ``library`` names faces the library meshes instead of OCCT: kept from OCCT as the cost guard
    keeps a face, or, with ``refused``, dropped from what OCCT meshed as faces it refused."""
    from cadgen._internal import tessellate
    from cadgen._internal.selector_table import build_selector_table
    from cadgen._internal.topology_extract import extract_topology_facts
    from cadgen.store.meshes import decode_payload
    from cadgen.store.parts import PART_VERSION
    from cadgen.tessellation_policy import DISPLAY_TESSELLATION
    from OCP.BRepBndLib import BRepBndLib
    from OCP.Bnd import Bnd_Box

    table = build_selector_table(topods, extract_topology_facts(topods))
    report: dict = {}
    guard = (lambda ordinal: ordinal in library) if library and not refused else None
    refusal = (mock.patch.object(tessellate, "_occt_mesh", _refusing(tessellate._occt_mesh, library))
               if refused else contextlib.nullcontext())
    with refusal:
        body = tessellate._mesh_component(topods, table, {}, part="0" * 64 + f"-v{PART_VERSION}",
                                          chord=DISPLAY_TESSELLATION["chordTolerance"],
                                          angle=DISPLAY_TESSELLATION["angleTolerance"], report=report, guard=guard)
    box = Bnd_Box()
    BRepBndLib.Add_s(topods, box, False)
    x0, y0, z0, x1, y1, z1 = box.Get()
    return decode_payload(body), DISPLAY_TESSELLATION["chordTolerance"] * math.dist((x0, y0, z0), (x1, y1, z1)), report


def _odd_edges(positions, triangles) -> int:
    """Edges of the mesh welded where its float32 vertices coincide that an odd number of triangles
    use: a crack, a T-junction or a hole."""
    _, label = np.unique(np.ascontiguousarray(positions, np.float32).view(np.dtype((np.void, 12))).ravel(),
                         return_inverse=True)
    welded = label.ravel()[triangles]
    keep = (welded[:, 0] != welded[:, 1]) & (welded[:, 1] != welded[:, 2]) & (welded[:, 2] != welded[:, 0])
    welded = welded[keep]
    edges = np.sort(np.concatenate([welded[:, [0, 1]], welded[:, [1, 2]], welded[:, [2, 0]]]), axis=1)
    _keys, counts = np.unique(edges[:, 0] * (int(label.max()) + 1) + edges[:, 1], return_counts=True)
    return int((counts % 2).sum())


def _face_map(topods):
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape

    faces = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(topods, TopAbs_FACE, faces)
    return faces


def _exact_area_and_perimeter(face) -> tuple[float, float]:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    area, length = GProp_GProps(), GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, area)
    BRepGProp.LinearProperties_s(face, length)
    return area.Mass(), length.Mass()


def _surface_distance_and_fold(face, positions, triangles) -> tuple[float, int]:
    """Over the face's longest triangles, the farthest any sampled point of them (centroid, edge
    midpoints, an interior point) lies from the exact surface, and how many of them are folded
    against the surface's normal where they sit."""
    from OCP.BRep import BRep_Tool
    from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
    from OCP.GeomLProp import GeomLProp_SLProps
    from OCP.TopAbs import TopAbs_REVERSED
    from OCP.TopLoc import TopLoc_Location
    from OCP.gp import gp_Pnt

    location = TopLoc_Location()
    surface = BRep_Tool.Surface_s(face, location)
    transform = location.Transformation()
    inverse = transform.Inverted()
    sign = -1.0 if face.Orientation() == TopAbs_REVERSED else 1.0
    a, b, c = (positions[triangles[:, k]] for k in range(3))
    longest = np.maximum(np.maximum(np.linalg.norm(b - a, axis=1), np.linalg.norm(c - b, axis=1)),
                         np.linalg.norm(a - c, axis=1))
    worst, folded = 0.0, 0
    for k in np.argsort(-longest)[:SAMPLE].tolist():
        facet = np.cross(b[k] - a[k], c[k] - a[k])
        samples = ((a[k] + b[k] + c[k]) / 3, (a[k] + b[k]) / 2, (b[k] + c[k]) / 2, (c[k] + a[k]) / 2,
                   0.6 * a[k] + 0.25 * b[k] + 0.15 * c[k])
        for index, point in enumerate(samples):
            projector = GeomAPI_ProjectPointOnSurf(gp_Pnt(*point.tolist()).Transformed(inverse), surface)
            if not projector.NbPoints():
                continue
            worst = max(worst, projector.LowerDistance())
            if index == 0 and np.linalg.norm(facet) > 0:
                u, v = projector.LowerDistanceParameters()
                props = GeomLProp_SLProps(surface, u, v, 1, 1e-9)
                if props.IsNormalDefined():
                    normal = props.Normal().Transformed(transform)
                    n = sign * np.array([normal.X(), normal.Y(), normal.Z()])
                    folded += int(np.dot(facet, n) / (np.linalg.norm(facet) * np.linalg.norm(n)) < FOLDED_COSINE)
    return worst, folded


def _occt_triangles(topods, deflection: float, angle: float) -> int:
    """How many triangles OCCT's BRepMesh makes of the solid at the same tolerances."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRepTools import BRepTools
    from OCP.IMeshTools import IMeshTools_Parameters
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    copy = BRepBuilderAPI_Copy(topods).Shape()  # meshed apart, so the solid under test keeps no triangulation
    BRepTools.Clean_s(copy)
    params = IMeshTools_Parameters()
    params.Deflection, params.Angle, params.InParallel = deflection, angle, False
    BRepMesh_IncrementalMesh(copy, params)
    faces = _face_map(copy)
    total = 0
    for ordinal in range(1, faces.Extent() + 1):
        poly = BRep_Tool.Triangulation_s(TopoDS.Face_s(faces.FindKey(ordinal)), TopLoc_Location())
        total += poly.NbTriangles() if poly is not None else 0
    return total


class _Case:
    """One solid, meshed once for every clause."""

    def __init__(self, shape, **how) -> None:
        self.shape = shape
        self.mesh, self.deflection, self.report = _mesh(shape.wrapped, **how)
        self.positions = np.asarray(self.mesh.positions, np.float64)
        self.triangles = np.asarray(self.mesh.indices, np.int64).reshape(-1, 3)
        self.faces = _face_map(shape.wrapped)

    def face(self, ordinal: int):
        from OCP.TopoDS import TopoDS

        return TopoDS.Face_s(self.faces.FindKey(ordinal))


class MeshContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        shapes = _shapes()
        cls.cases = {name: _Case(shape) for name, shape in shapes.items()}
        curved = _curved_faces(shapes["plate"].wrapped)
        cls.cases["plate, curved faces refused"] = _Case(shapes["plate"], library=curved, refused=True)
        cls.cases["plate, curved faces guarded"] = _Case(shapes["plate"], library=curved)

    def test_a_face_occt_refused_or_the_guard_kept_from_it_is_the_librarys_and_the_solid_whole(self) -> None:
        # The clauses above hold on these cases like any other; this is who meshed what.
        curved = _curved_faces(self.cases["plate"].shape.wrapped)
        self.assertGreaterEqual(len(curved), 5, "the bore, the rounds and the sphere")
        for name, listed in (("plate, curved faces refused", "refused"), ("plate, curved faces guarded", "guarded")):
            with self.subTest(name):
                report = self.cases[name].report
                self.assertEqual(set(report[listed]), curved)
                self.assertEqual({ordinal for ordinal, facts in report["faces"].items() if facts["madeBy"] == "library"},
                                 curved, "the library made every face OCCT did not, and no other")
                self.assertEqual(([], []), (report["unmeshed"], report["unverified"]))

    def test_every_solid_is_closed(self) -> None:
        for name, case in self.cases.items():
            with self.subTest(name):
                self.assertGreater(len(case.triangles), 0)
                self.assertEqual(0, _odd_edges(case.positions, case.triangles),
                                 f"{name}: an open edge, a crack or a T-junction")

    def test_every_face_is_covered_to_its_trim_and_none_is_silently_empty(self) -> None:
        for name, case in self.cases.items():
            with self.subTest(name):
                self.assertEqual([], list(case.mesh.unmeshed_faces), f"{name} names a face it could not mesh")
                for ordinal, start, count, _colour in case.mesh.faces.tolist():
                    exact, perimeter = _exact_area_and_perimeter(case.face(ordinal))
                    if exact < case.deflection * case.deflection:
                        continue
                    self.assertGreater(count, 0, f"{name}: face {ordinal} ({exact:.3g} mm²) is silently empty")
                    t = case.triangles[start // 3:(start + count) // 3]
                    p = case.positions
                    area = float(np.linalg.norm(np.cross(p[t[:, 1]] - p[t[:, 0]], p[t[:, 2]] - p[t[:, 0]]),
                                                axis=1).sum() / 2)
                    # Polygonizing the boundary within the chord tolerance moves at most perimeter × d
                    # of area; anything beyond that, and beyond 1%, is trim the mesh missed or invented.
                    self.assertLessEqual(abs(area - exact), max(AREA_TOLERANCE * exact, perimeter * case.deflection),
                                         f"{name}: face {ordinal}'s area is {area:.4g} mm² against {exact:.4g} exact")

    def test_every_triangle_lies_on_its_surface_within_the_tolerance(self) -> None:
        for name, case in self.cases.items():
            with self.subTest(name):
                for ordinal, start, count, _colour in case.mesh.faces.tolist():
                    if not count:
                        continue
                    t = case.triangles[start // 3:(start + count) // 3]
                    distance, folded = _surface_distance_and_fold(case.face(ordinal), case.positions, t)
                    self.assertLessEqual(distance, 2 * case.deflection,
                                         f"{name}: face {ordinal} strays {distance / case.deflection:.2f} d off "
                                         "its surface")
                    self.assertEqual(0, folded, f"{name}: face {ordinal} has a triangle folded against its surface")

    def test_every_solid_is_wound_outward_with_the_surfaces_normals_and_no_degenerate_triangle(self) -> None:
        for name, case in self.cases.items():
            with self.subTest(name):
                a, b, c = (case.positions[case.triangles[:, k]] for k in range(3))
                facets = np.cross(b - a, c - a)
                self.assertTrue(bool((np.linalg.norm(facets, axis=1) > 0).all()), f"{name}: a degenerate triangle")
                volume = float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6)
                # A mesh within d of its surface everywhere encloses a volume within area x d of
                # the solid's (an inscribed mesh falls short by up to that); a face wound inward
                # would be off by twice its own volume share.
                self.assertAlmostEqual(1.0, volume / case.shape.volume,
                                       delta=case.shape.area * case.deflection / case.shape.volume,
                                       msg=f"{name}: wound outward, its volume the solid's")
                normals = np.asarray(case.mesh.normals, np.float64)[case.triangles].sum(axis=1)
                cosine = np.einsum("ij,ij->i", facets, normals) / (np.linalg.norm(facets, axis=1)
                                                                   * np.maximum(np.linalg.norm(normals, axis=1), 1e-300))
                against = np.nonzero(cosine < FOLDED_COSINE)[0]
                self.assertEqual([], against.tolist(), f"{name}: triangles folded against the normals they are drawn with")

    def test_no_solid_costs_more_than_a_few_times_occts_triangles(self) -> None:
        from cadgen.tessellation_policy import DISPLAY_TESSELLATION

        for name, case in self.cases.items():
            with self.subTest(name):
                occt = _occt_triangles(case.shape.wrapped, case.deflection, DISPLAY_TESSELLATION["angleTolerance"])
                self.assertLessEqual(len(case.triangles), TRIANGLE_BUDGET * max(occt, 1),
                                     f"{name}: {len(case.triangles)} triangles against OCCT's {occt}")


if __name__ == "__main__":
    unittest.main()
