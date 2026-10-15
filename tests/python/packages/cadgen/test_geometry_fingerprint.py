"""Geometric identity (``cadgen._internal.geometry_fingerprint``): a body's fingerprint and
its elements' tokens are functions of rounded geometry, of nothing positional, and
survive the STEP round trip; other geometry has another name."""

from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from tests.python.support.paths import add_repo_path

add_repo_path("packages/cadgen/src")

from build123d import Box, Compound, Cylinder, Pos, Rot, Solid, export_step, import_step  # noqa: E402

from cadgen._internal.geometry_fingerprint import (  # noqa: E402
    ELEMENT_TOKEN_RE,
    GEOMETRY_FINGERPRINT_SCHEME,
    RELATIVE_QUANTUM,
    element_tokens,
    fingerprint,
    geometry_identity,
    is_element_token,
    quantum_for_extent,
)


def _topods(shape):
    return getattr(shape, "wrapped", shape)


def _roundtrip(shape, directory: Path):
    path = directory / "shape.step"
    export_step(shape, str(path))
    return import_step(str(path))


class FingerprintTest(unittest.TestCase):
    def test_the_identity_is_tokens_by_ordinal_under_the_scheme(self) -> None:
        identity = geometry_identity(_topods(Box(10, 20, 30)))
        self.assertEqual(sorted(identity), ["edges", "faces", "fingerprint", "vertices"])
        self.assertRegex(identity["fingerprint"], r"^[0-9a-f]{64}$")
        self.assertEqual([len(identity[name]) for name in ("faces", "edges", "vertices")], [6, 12, 8])
        for name, prefix in (("faces", "f"), ("edges", "e"), ("vertices", "v")):
            for token in identity[name]:
                self.assertTrue(is_element_token(token) and token.startswith(prefix + ":"), token)
                self.assertRegex(token, ELEMENT_TOKEN_RE)
            self.assertEqual(len(set(identity[name])), len(identity[name]), "tokens are distinct")
        self.assertEqual(fingerprint(_topods(Box(10, 20, 30))), identity["fingerprint"])
        self.assertEqual(element_tokens(_topods(Box(10, 20, 30))), {k: identity[k] for k in ("faces", "edges", "vertices")})
        self.assertTrue(GEOMETRY_FINGERPRINT_SCHEME.startswith("cadgen-geometry-fingerprint-v"))
        self.assertFalse(is_element_token("f7"))
        self.assertFalse(is_element_token("o:0123456789ab"))

    def test_the_same_geometry_in_other_bytes_has_one_name(self) -> None:
        # Built twice, and built then read back from STEP: other bytes, another face order.
        a, b = _topods(Box(10, 20, 30)), _topods(Box(10, 20, 30))
        self.assertEqual(geometry_identity(a), geometry_identity(b))
        with tempfile.TemporaryDirectory() as directory:
            original = Box(10, 20, 30) - Pos(0, 0, 10) * Cylinder(3, 20)
            read_back = _roundtrip(original, Path(directory))
        before, after = geometry_identity(_topods(original)), geometry_identity(_topods(read_back))
        self.assertEqual(before["fingerprint"], after["fingerprint"])
        for name in ("faces", "edges", "vertices"):
            self.assertEqual(sorted(before[name]), sorted(after[name]), f"{name} tokens survive the round trip")

    def test_other_geometry_has_another_name(self) -> None:
        box = geometry_identity(_topods(Box(10, 20, 30)))
        for other in (Box(10, 20, 31), Box(20, 20, 30), Box(10, 20, 30).scale(2), Pos(0.1, 0, 0) * Box(10, 20, 30),
                      Rot(0, 0, 90) * Box(10, 20, 30), Box(10, 20, 30) - Cylinder(1, 40)):
            self.assertNotEqual(geometry_identity(_topods(other))["fingerprint"], box["fingerprint"], str(other))
        # A scaled copy rounds to the same integers at its own quantum: the quantum is in the name.
        self.assertNotEqual(quantum_for_extent(10.0), quantum_for_extent(20.0))

    def test_elements_are_named_by_their_geometry_not_their_ordinal(self) -> None:
        box = _topods(Box(10, 20, 30))
        identity = geometry_identity(box)
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.TopAbs import TopAbs_FACE
        from OCP.TopExp import TopExp
        from OCP.TopTools import TopTools_IndexedMapOfShape
        from OCP.TopoDS import TopoDS

        faces = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(box, TopAbs_FACE, faces)
        # The face a token names has the geometry the same face has in another build:
        # the token of the +Z cap of one box is the token of the +Z cap of the other.
        def top_token(shape):
            ident = geometry_identity(shape)
            found = TopTools_IndexedMapOfShape()
            TopExp.MapShapes_s(shape, TopAbs_FACE, found)
            for ordinal in range(1, found.Extent() + 1):
                face = TopoDS.Face_s(found.FindKey(ordinal))
                plane = BRepAdaptor_Surface(face).Plane()
                if abs(plane.Location().Z() - 15.0) < 1e-9:
                    return ident["faces"][ordinal - 1]
            raise AssertionError("no top face")

        self.assertEqual(top_token(box), top_token(_topods(Rot(0, 0, 180) * Box(10, 20, 30))))
        self.assertEqual(len(set(identity["faces"])), 6)

    def test_coincident_elements_are_told_apart_deterministically(self) -> None:
        # Two solids sharing a face: two faces with one geometry, two tokens, the same twice.
        pair = Compound(children=[Box(10, 10, 10), Pos(10, 0, 0) * Box(10, 10, 10)])
        first, second = geometry_identity(_topods(pair)), geometry_identity(_topods(pair))
        self.assertEqual(first, second)
        self.assertEqual(len(first["faces"]), 12)
        self.assertEqual(len(set(first["faces"])), 12)
        self.assertEqual(len(set(first["vertices"])), len(first["vertices"]))

    def test_an_edge_the_kernel_built_without_vertices_is_named_by_its_curve(self) -> None:
        # A closed edge made straight from its curve has no vertices (a loft or sweep
        # leaves such edges); its ends are read off the curve, not off a null vertex.
        from OCP.BRep import BRep_Builder
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
        from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt
        from OCP.Geom import Geom_Circle
        from OCP.TopoDS import TopoDS_Edge

        from cadgen._internal.geometry_fingerprint import _edge_facts

        circle = Geom_Circle(gp_Ax2(gp_Pnt(1, 2, 3), gp_Dir(0, 0, 1)), 5.0)
        bare = TopoDS_Edge()
        BRep_Builder().MakeEdge(bare, circle, 1e-7)
        with_vertices = BRepBuilderAPI_MakeEdge(circle).Edge()
        for bare_end, vertex_end in zip(_edge_facts(bare)["ends"], _edge_facts(with_vertices)["ends"]):
            for a, b in zip(bare_end, vertex_end):
                self.assertAlmostEqual(a, b, places=9)
        self.assertAlmostEqual(_edge_facts(bare)["length"], 2 * math.pi * 5.0, places=6)

    def test_the_quantum_is_a_power_of_two_relative_to_the_extent(self) -> None:
        for extent in (0.001, 1.0, 37.5, 1e4):
            quantum = quantum_for_extent(extent)
            self.assertEqual(math.log2(quantum), round(math.log2(quantum)), "a power of two")
            self.assertLessEqual(quantum, extent * RELATIVE_QUANTUM)
            self.assertGreater(quantum, extent * RELATIVE_QUANTUM / 2)
        self.assertEqual(quantum_for_extent(0.0), 2.0 ** -40)
        self.assertEqual(fingerprint(_topods(Solid.make_box(1, 1, 1))), fingerprint(_topods(Box(1, 1, 1, align=None))))


if __name__ == "__main__":
    unittest.main()
