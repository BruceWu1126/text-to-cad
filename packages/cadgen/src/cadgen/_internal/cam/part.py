"""The part a CAM verb machines: one leaf occurrence of a STEP document, its faces by selector, and the geometric
questions feature recognition and the later checks ask of it.

The document is opened and the occurrence picked exactly as ``cadgen fea`` does (:mod:`cadgen._internal.step_open`).
Everything is in the document's frame, in mm. Face ``N`` here is the viewer's ``#o1.fN``: the ordinals come from
the same canonical map the scene's selectors use (:func:`cadgen._internal.entity_ordinals.entity_map`), over one
placed copy of the occurrence, so adjacency and ray casts see a single consistent shape.

Stdlib only at import: OCP is imported inside the functions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["LINEAR_TOL", "PROBE_MM", "Part", "PartFace", "Vec", "add", "cross", "dot", "open_part", "parallel",
           "perpendicular", "refuse_mirrored", "same", "scale", "sub", "unit"]

Vec = tuple[float, float, float]

#: Positions closer than this (mm) are the same position.
LINEAR_TOL = 1e-3
#: Two unit vectors whose dot is beyond 1 - this are parallel (about 0.1 degree).
PARALLEL_TOL = 2e-6
#: How far (mm) off a surface a probe point starts, so it is clear of the surface it starts from.
PROBE_MM = 0.01


def dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def add(a: Vec, b: Vec) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def scale(a: Vec, k: float) -> Vec:
    return (a[0] * k, a[1] * k, a[2] * k)


def cross(a: Vec, b: Vec) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def unit(a: Vec) -> Vec:
    length = math.sqrt(dot(a, a))
    if length == 0:
        raise ValueError("a direction must not be zero")
    return (a[0] / length, a[1] / length, a[2] / length)


def parallel(a: Vec, b: Vec) -> bool:
    """Unit vectors along the same line, either way round."""
    return abs(dot(a, b)) > 1 - PARALLEL_TOL


def same(a: Vec, b: Vec) -> bool:
    """Unit vectors pointing the same way."""
    return dot(a, b) > 1 - PARALLEL_TOL


@dataclass
class PartFace:
    """One face of the part, by its selector ordinal, with the facts recognition reads."""

    ordinal: int
    ref: str
    face: Any = field(repr=False)  # TopoDS_Face, placed in the document frame
    #: ``plane``, ``cylinder``, ``cone``, ``extrusion`` or ``other``.
    surface: str
    area: float
    center: Vec
    #: Edge ordinals (``#o1.eN``) of the whole face, and of its outer wire alone.
    edges: frozenset[int]
    outer_edges: frozenset[int]
    #: A plane's normal, pointing out of the material.
    normal: Vec | None = None
    #: A cylinder's or an extrusion's axis direction; a cylinder's axis point and radius.
    axis: Vec | None = None
    axis_point: Vec | None = None
    radius: float | None = None
    #: A cylinder's angular span in radians (2 pi for a whole one), and its extent along the axis from axis_point.
    span: float | None = None
    v_range: tuple[float, float] | None = None
    #: A cylinder whose axis lies outside the material (a hole wall or an inside corner), not a boss.
    concave: bool = False
    #: A cone's half angle, radians.
    half_angle: float | None = None


class Part:
    """One leaf occurrence of a document, opened for machining."""

    def __init__(self, document: Path, ref: str, shape: Any):
        from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
        from OCP.TopExp import TopExp
        from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape

        from cadgen._internal.entity_ordinals import entity_map
        from cadgen.geometry import _items

        self.document = document
        self.ref = ref
        self.shape = shape
        self._face_map = entity_map(shape, "face")
        self._edge_map = entity_map(shape, "edge")
        ancestors = TopTools_IndexedDataMapOfShapeListOfShape()
        TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, ancestors)
        self._edge_faces: dict[int, tuple[int, ...]] = {}
        for index in range(1, ancestors.Extent() + 1):
            edge = self._edge_map.FindIndex(ancestors.FindKey(index))
            faces = sorted({self._face_map.FindIndex(face) for face in _items(ancestors.FindFromIndex(index))})
            self._edge_faces[edge] = tuple(faces)
        self.faces: tuple[PartFace, ...] = tuple(self._read_face(i) for i in range(1, self._face_map.Extent() + 1))
        self._intersector = None
        self._classifiers: list | None = None
        self._spans: dict[tuple[int, Vec, Vec], tuple[float, float, float, float, float, float]] = {}
        self._samples: dict[int, list[tuple[Vec, Vec]]] = {}

    # --- faces --------------------------------------------------------------------------------------------

    def face(self, ordinal: int) -> PartFace:
        return self.faces[ordinal - 1]

    def edge(self, ordinal: int) -> Any:
        """The edge ``#o1.eN`` as a TopoDS_Edge in the document frame; a ValueError when the part has no such edge."""
        from OCP.TopoDS import TopoDS

        if not 1 <= ordinal <= self._edge_map.Extent():
            raise ValueError(f"{self.ref}.e{ordinal}: the part has edges e1 to e{self._edge_map.Extent()}")
        return TopoDS.Edge_s(self._edge_map.FindKey(ordinal))

    def neighbours(self, ordinal: int, edges=None) -> list[int]:
        """The faces sharing an edge with this one (only across ``edges`` when given), in ordinal order."""
        found: set[int] = set()
        for edge in self.face(ordinal).edges if edges is None else edges:
            found.update(self._edge_faces.get(edge, ()))
        found.discard(ordinal)
        return sorted(found)

    def _read_face(self, ordinal: int) -> PartFace:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.BRepGProp import BRepGProp, BRepGProp_Face
        from OCP.BRepTools import BRepTools
        from OCP.GeomAbs import GeomAbs_Cone, GeomAbs_Cylinder, GeomAbs_Plane, GeomAbs_SurfaceOfExtrusion
        from OCP.GProp import GProp_GProps
        from OCP.gp import gp_Pnt, gp_Vec
        from OCP.TopoDS import TopoDS

        from cadgen._internal.entity_ordinals import entity_map

        face = TopoDS.Face_s(self._face_map.FindKey(ordinal))
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, props)
        centre = props.CentreOfMass()
        edges = frozenset(self._edge_map.FindIndex(edge) for edge in _keys(entity_map(face, "edge")))
        outer = BRepTools.OuterWire_s(face)
        outer_edges = frozenset(self._edge_map.FindIndex(edge) for edge in _keys(entity_map(outer, "edge"))) \
            if not outer.IsNull() else edges
        adaptor = BRepAdaptor_Surface(face)
        kind = adaptor.GetType()
        info = PartFace(ordinal, f"{self.ref}.f{ordinal}", face, "other", float(props.Mass()),
                        (centre.X(), centre.Y(), centre.Z()), edges, outer_edges)
        umin, umax, vmin, vmax = BRepTools.UVBounds_s(face)
        point, normal = gp_Pnt(), gp_Vec()
        BRepGProp_Face(face).Normal((umin + umax) / 2, (vmin + vmax) / 2, point, normal)
        if normal.Magnitude() > 0:
            normal.Normalize()
        n = (normal.X(), normal.Y(), normal.Z())
        if kind == GeomAbs_Plane:
            info.surface, info.normal = "plane", n
        elif kind == GeomAbs_Cylinder:
            cylinder = adaptor.Cylinder()
            axis = cylinder.Axis()
            info.surface = "cylinder"
            info.axis = _vec(axis.Direction())
            info.axis_point = _vec(axis.Location())
            info.radius = float(cylinder.Radius())
            info.span = float(umax - umin)
            info.v_range = (float(vmin), float(vmax))
            p = (point.X(), point.Y(), point.Z())
            radial = sub(p, add(info.axis_point, scale(info.axis, dot(sub(p, info.axis_point), info.axis))))
            info.concave = dot(n, radial) < 0
        elif kind == GeomAbs_Cone:
            cone = adaptor.Cone()
            info.surface = "cone"
            info.axis = _vec(cone.Axis().Direction())
            info.axis_point = _vec(cone.Axis().Location())
            info.half_angle = abs(float(cone.SemiAngle()))
        elif kind == GeomAbs_SurfaceOfExtrusion:
            info.surface = "extrusion"
            info.axis = _vec(adaptor.Direction())
        return info

    def is_wall(self, ordinal: int, direction: Vec) -> bool:
        """A face a tool along ``direction`` cuts with its side: a plane parallel to it, or a cylinder or an
        extruded surface whose axis runs along it."""
        face = self.face(ordinal)
        if face.surface == "plane":
            return abs(dot(face.normal, direction)) < math.sqrt(PARALLEL_TOL)
        if face.surface in ("cylinder", "extrusion"):
            return parallel(face.axis, direction)
        return False

    # --- extents ------------------------------------------------------------------------------------------

    def extent(self, ordinals, direction: Vec, across: Vec | None = None) -> tuple[float, float, float, float, float, float]:
        """The tight box of these faces in a frame whose z is ``direction`` and whose x is ``across`` (any
        perpendicular when omitted): ``(xmin, xmax, ymin, ymax, zmin, zmax)``, so z is the span along ``direction``."""
        across = across or perpendicular(direction)
        boxes = []
        for ordinal in ordinals:
            key = (ordinal, direction, across)
            if key not in self._spans:
                self._spans[key] = self._box(self.face(ordinal).face, direction, across)
            boxes.append(self._spans[key])
        return (min(b[0] for b in boxes), max(b[1] for b in boxes), min(b[2] for b in boxes),
                max(b[3] for b in boxes), min(b[4] for b in boxes), max(b[5] for b in boxes))

    @staticmethod
    def _box(shape, direction: Vec, across: Vec):
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
        from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt, gp_Trsf
        from OCP.TopLoc import TopLoc_Location

        trsf = gp_Trsf()
        trsf.SetTransformation(gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(*direction), gp_Dir(*across)))
        box = Bnd_Box()
        BRepBndLib.AddOptimal_s(shape.Moved(TopLoc_Location(trsf)), box, False, False)
        xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
        return (xmin, xmax, ymin, ymax, zmin, zmax)

    # --- probes -------------------------------------------------------------------------------------------

    def samples(self, ordinal: int) -> list[tuple[Vec, Vec]]:
        """Up to nine points well inside the face, each with the face's outward normal there."""
        if ordinal not in self._samples:
            self._samples[ordinal] = self._sample(self.face(ordinal).face)
        return self._samples[ordinal]

    @staticmethod
    def _sample(face) -> list[tuple[Vec, Vec]]:
        from OCP.BRepGProp import BRepGProp_Face
        from OCP.BRepTools import BRepTools
        from OCP.BRepTopAdaptor import BRepTopAdaptor_FClass2d
        from OCP.gp import gp_Pnt, gp_Pnt2d, gp_Vec
        from OCP.TopAbs import TopAbs_IN

        umin, umax, vmin, vmax = BRepTools.UVBounds_s(face)
        inside = BRepTopAdaptor_FClass2d(face, 1e-7)
        surface = BRepGProp_Face(face)
        found: list[tuple[Vec, Vec]] = []
        for steps in (3, 7, 15):
            for i in range(steps):
                for j in range(steps):
                    u = umin + (umax - umin) * (i + 0.5) / steps
                    v = vmin + (vmax - vmin) * (j + 0.5) / steps
                    if inside.Perform(gp_Pnt2d(u, v)) != TopAbs_IN:
                        continue
                    point, normal = gp_Pnt(), gp_Vec()
                    surface.Normal(u, v, point, normal)
                    if normal.Magnitude() == 0:
                        continue
                    normal.Normalize()
                    found.append(((point.X(), point.Y(), point.Z()), (normal.X(), normal.Y(), normal.Z())))
            if found:
                break
        if len(found) > 9:
            found = [found[k * len(found) // 9] for k in range(9)]
        return found

    def clear(self, start: Vec, direction: Vec) -> bool:
        """Nothing of the part lies along the ray from ``start`` (on or just off a face) in ``direction``."""
        from OCP.gp import gp_Dir, gp_Lin, gp_Pnt
        from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector

        if self._intersector is None:
            self._intersector = IntCurvesFace_ShapeIntersector()
            self._intersector.Load(self.shape, 1e-7)
        self._intersector.Perform(gp_Lin(gp_Pnt(*start), gp_Dir(*direction)), PROBE_MM / 2, 1e12)
        return self._intersector.IsDone() and self._intersector.NbPnt() == 0

    def inside(self, point: Vec) -> bool:
        """``point`` is in the material of any of the part's solids (not in air, not on the surface)."""
        from OCP.BRepClass3d import BRepClass3d_SolidClassifier
        from OCP.gp import gp_Pnt
        from OCP.TopAbs import TopAbs_IN

        from cadgen._internal.entity_ordinals import shape_entities

        if self._classifiers is None:  # one per solid, loaded once: building one costs far more than a query
            self._classifiers = [BRepClass3d_SolidClassifier(solid) for solid in shape_entities(self.shape)]
        for classifier in self._classifiers:
            classifier.Perform(gp_Pnt(*point), 1e-7)
            if classifier.State() == TopAbs_IN:
                return True
        return False

    def longest_line(self, ordinal: int) -> Vec | None:
        """The direction of the face's longest straight edge, or ``None`` when it has none."""
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.GeomAbs import GeomAbs_Line
        from OCP.TopoDS import TopoDS

        best, direction = 0.0, None
        for edge in sorted(self.face(ordinal).edges):
            curve = BRepAdaptor_Curve(TopoDS.Edge_s(self._edge_map.FindKey(edge)))
            if curve.GetType() != GeomAbs_Line:
                continue
            length = curve.LastParameter() - curve.FirstParameter()
            if length > best + LINEAR_TOL:
                best, direction = length, _vec(curve.Line().Direction())
        return direction


def perpendicular(direction: Vec) -> Vec:
    """A unit vector across ``direction``: world X for a Z or Y direction, world Y for an X one, else X made square."""
    for candidate in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)):
        if not parallel(candidate, direction) and abs(dot(candidate, direction)) < 1e-9:
            return candidate
    reference = (1.0, 0.0, 0.0) if abs(direction[0]) < 0.9 else (0.0, 1.0, 0.0)
    return unit(sub(reference, scale(direction, dot(reference, direction))))


def _vec(value) -> Vec:
    return (float(value.X()), float(value.Y()), float(value.Z()))


def _keys(indexed_map) -> list:
    return [indexed_map.FindKey(index) for index in range(1, indexed_map.Extent() + 1)]


def refuse_mirrored(ref: str, location) -> None:
    """A ValueError for an occurrence placed with a mirroring transform (a TopLoc_Location, or None for none).

    Refused rather than baked in: a mirror turns every face inside out, and a part is machined as it is made.
    """
    if location is not None and location.Transformation().IsNegative():
        raise ValueError(f"{ref} is placed mirrored in its assembly; cam machines a part as it is made, so export "
                         "the mirrored part as a solid of its own and plan that")


def open_part(target: Path, occurrence: str | None = None) -> Part:
    """The part ``target`` holds (or its ``occurrence``), opened and picked as ``cadgen fea`` does."""
    from cadgen._internal.step_open import open_scene, pick_occurrence

    scene = open_scene(Path(target))
    owner = pick_occurrence(scene, occurrence)
    if owner.prototype_id is None:
        leaves = [leaf.ref for leaf in scene.leaves() if leaf.ref.startswith(owner.ref + ".")]
        raise ValueError(f"{owner.ref} is a group of {len(leaves)} parts; cam machines one part, so pass --occurrence "
                         f"{' or '.join(leaves[:4])}{' ...' if len(leaves) > 4 else ''}")
    refuse_mirrored(owner.ref, owner._node.location)
    # The placed occurrence shares its faces with the scene's prototype, so its face N is the scene's #fN: checked
    # face by face, since a wrong number would send every selector to the wrong face.
    part = Part(Path(target), owner.ref, scene._placed(owner))
    table = scene._table(owner, "face")
    if table.Extent() != len(part.faces) or not all(
            face.face.IsPartner(table.FindKey(face.ordinal)) for face in part.faces):
        raise RuntimeError(f"{owner.ref}: its faces are numbered differently from the scene's selectors; "
                           "cam cannot name them")
    return part
