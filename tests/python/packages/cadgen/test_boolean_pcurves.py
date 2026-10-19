"""A boolean the kernel memo sees runs non-destructive, and writes the p-curves a destructive
one writes on planes into the edges it made, and into no edge it was given (README law 18,
``kernel_memo._store_pcurves``)."""

from __future__ import annotations

import unittest


def _edges(shape):
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape

    found = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape.wrapped, TopAbs_EDGE, found)
    return [found.FindKey(ordinal) for ordinal in range(1, found.Extent() + 1)]


def _planes_bounded(result, edge) -> int:
    """How many plane faces of ``result`` the edge bounds."""
    from OCP.BRep import BRep_Tool
    from OCP.Geom import Geom_Plane
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape

    from cadgen._internal.kernel_history import listed

    ancestors = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(result.wrapped, TopAbs_EDGE, TopAbs_FACE, ancestors)
    faces = {face.TShape(): face for face in listed(ancestors.FindFromKey(edge))}
    return sum(isinstance(BRep_Tool.Surface_s(TopoDS.Face_s(face)), Geom_Plane) for face in faces.values())


class BooleanPcurveTest(unittest.TestCase):
    def setUp(self) -> None:
        from cadgen._internal import kernel_memo

        kernel_memo.install()

    def test_a_boolean_writes_its_own_edges_p_curves_on_planes_and_no_input_edges(self) -> None:
        from build123d import Location, Polygon, Rectangle, extrude

        # Extrusions, whose sides are planes with no p-curve stored (a primitive's has
        # them): the fuse's own edges, where the rib stands on the plate, get none either
        # unless the boolean writes them.
        plate = extrude(Rectangle(20, 20), 4)
        rib = extrude(Polygon((0, 0), (6, 0), (6, 3), (0, 6), align=None), 6).moved(Location((0, 0, 4)))
        given = {edge.TShape(): edge.TShape().Curves().Extent() for shape in (plate, rib) for edge in _edges(shape)}

        result = plate.fuse(rib)

        self.assertEqual({tshape: tshape.Curves().Extent() for tshape in given}, given,
                         "the boolean writes into no edge it was given")
        made = [edge for edge in _edges(result) if edge.TShape() not in given]
        self.assertTrue(made, "the fuse makes the edges where the rib meets the plate")
        for edge in made:
            planes = _planes_bounded(result, edge)
            self.assertGreater(planes, 0)
            # Its 3D curve, and its curve on each plane it bounds.
            self.assertGreaterEqual(edge.TShape().Curves().Extent(), 1 + planes)


if __name__ == "__main__":
    unittest.main()
