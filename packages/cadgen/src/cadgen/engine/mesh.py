"""Tessellation: one part as one buffer a renderer reads without parsing.

**The tolerance is FreeCAD's default, and nothing else.** A part is meshed with
``BRepMesh_IncrementalMesh`` at an absolute linear deflection of ``(Lx + Ly + Lz) / 300 *
DEVIATION`` of its own bounding box (``DEVIATION`` 0.5: FreeCAD's Deviation of 0.5 %) and an
angular deflection of ``ANGULAR`` (28.5 degrees), with the kernel's face parallelism on. The
constant pair is configurable nowhere.

**BRepMesh as it is.** No check, no second pass, no other tessellator. A face the mesher
leaves without triangles is left out of the buffer -- its face-table row has no indices --
and named: ``Mesh.unmeshed`` maps its ordinal to what ``BRepCheck_Analyzer`` says of that
face (``check``), so the report reads "its wire intersects itself", not "the mesher failed".

A part is meshed from its binary shape bytes (``shape_bytes``: geometry only, at the
identity), read into a fresh shape: the mesh is a function of those bytes alone -- the same
in this process, in a pool process handed the bytes, and in a model's worker -- never of a
triangulation some earlier call left on the shape, and the caller's shape is not written
to. (A topological copy is not enough: measured on a large document, a copy and the bytes
read back meshed 65 of 987 parts with their triangles' corners in another order.)

The buffer (``Mesh.to_bytes``, format ``XBM2``) holds float32 positions and surface normals,
uint32 triangle indices, a face table of (face ordinal, first index, index count), an edge
table of (edge ordinal, first point, point count, class) with the edges' polylines, and a
vertex table of (vertex ordinal, x, y, z) for every vertex of the part. Ordinals are
``TopExp.MapShapes`` ordinals of the part, the ordinals its element names are kept by.
Selecting a face is a range of the index buffer; an edge, a range of the polyline buffer.

An edge's class is read from the B-rep alone (``edge_class``): ``FEATURE`` between two
faces that meet at an angle, ``TANGENT`` between two that meet at least G1
(``BRep_Tool.Continuity``, after ``BRepLib.EncodeRegularity`` on the copy), ``SEAM`` where a
face closes on itself, ``BOUNDARY`` with one face, ``NONMANIFOLD`` with more than two.
Degenerate edges are not in the table. An edge's polyline is its polygon on the
triangulation of the first of its faces that has one.

The arrays are read from the kernel without a Python loop per node: the part's
triangulations and polygons are written, as triangulation-only faces and edges, into the
kernel's binary shape format in memory (``BinTools``) and read back with numpy from that
one buffer; a buffer that does not read back as expected falls back to the kernel's
accessors, one node at a time.

The header is 80 little-endian bytes: the magic ``XBM2``, the format version, the counts of
nodes, indices, faces, edges, edge points and vertices, the byte offset of each of the seven
arrays, the two deflections, and three reserved words; then the arrays, each 4-byte
aligned, at their offsets.
"""

from __future__ import annotations

import io
import math
import struct
from dataclasses import dataclass, field
from typing import Any

import numpy as np

__all__ = ["ANGULAR", "BOUNDARY", "DEVIATION", "FEATURE", "HEADER", "MAGIC", "Mesh", "NONMANIFOLD", "SEAM",
           "TANGENT", "Topology", "check", "deflection", "mesh_shape"]

# FreeCAD's default tessellation: Deviation 0.5 (per cent of the box's summed sides, /300 as
# FreeCAD writes it) and an angular deflection of 28.5 degrees.
DEVIATION = 0.5
ANGULAR = math.radians(28.5)

FEATURE, TANGENT, SEAM, BOUNDARY, NONMANIFOLD = 1, 2, 3, 5, 6
CLASS_NAMES = {FEATURE: "feature", TANGENT: "tangent", SEAM: "seam", BOUNDARY: "boundary",
               NONMANIFOLD: "non-manifold"}

MAGIC = b"XBM2"
VERSION = 2
HEADER = struct.Struct("<4sI6I7I2f3I")
assert HEADER.size == 80


@dataclass
class Mesh:
    positions: np.ndarray  # (V, 3) float32
    normals: np.ndarray  # (V, 3) float32
    indices: np.ndarray  # (I,) uint32
    faces: np.ndarray  # (F, 3) uint32: ordinal, first index, index count
    edges: np.ndarray  # (E, 4) uint32: ordinal, first point, point count, class
    edge_points: np.ndarray  # (P, 3) float32
    vertices: np.ndarray  # (X, 4) float32: ordinal, x, y, z
    linear: float = 0.0
    angular: float = 0.0
    unmeshed: dict[int, str] = field(default_factory=dict)  # face ordinal: its BRepCheck verdict

    @property
    def triangles(self) -> int:
        return len(self.indices) // 3

    @property
    def nbytes(self) -> int:
        return sum(array.nbytes for array in (self.positions, self.normals, self.indices, self.faces, self.edges,
                                              self.edge_points, self.vertices))

    def to_bytes(self) -> bytes:
        arrays = [np.ascontiguousarray(self.positions, "<f4"), np.ascontiguousarray(self.normals, "<f4"),
                  np.ascontiguousarray(self.indices, "<u4"), np.ascontiguousarray(self.faces, "<u4"),
                  np.ascontiguousarray(self.edges, "<u4"), np.ascontiguousarray(self.edge_points, "<f4"),
                  np.ascontiguousarray(self.vertices, "<f4")]
        offsets = []
        at = HEADER.size
        for array in arrays:
            offsets.append(at)
            at += array.nbytes  # every element is 4 bytes: every offset stays aligned
        header = HEADER.pack(MAGIC, VERSION, len(self.positions), len(self.indices), len(self.faces),
                             len(self.edges), len(self.edge_points), len(self.vertices), *offsets,
                             self.linear, self.angular, 0, 0, 0)
        return b"".join([header, *(array.tobytes() for array in arrays)])

    @staticmethod
    def from_bytes(data: bytes) -> "Mesh":
        (magic, version, *rest) = HEADER.unpack_from(data)
        if magic != MAGIC or version != VERSION:
            raise ValueError("not a mesh buffer")
        counts, offsets, (linear, angular) = rest[:6], rest[6:13], rest[13:15]
        nodes, indices, faces, edges, points, vertices = counts

        def view(at: int, dtype: str, count: int, width: int) -> np.ndarray:
            array = np.frombuffer(data, dtype, count * width, offsets[at])
            return array.reshape(-1, width) if width > 1 else array

        found = Mesh(view(0, "<f4", nodes, 3), view(1, "<f4", nodes, 3), view(2, "<u4", indices, 1),
                     view(3, "<u4", faces, 3), view(4, "<u4", edges, 4), view(5, "<f4", points, 3),
                     view(6, "<f4", vertices, 4), linear, angular)
        found.unmeshed = {int(ordinal): "" for ordinal, _first, count in found.faces if count == 0}
        return found


def deflection(shape: Any) -> float:
    """The linear deflection of ``shape``: FreeCAD's, ``(Lx + Ly + Lz) / 300 * DEVIATION`` of
    its bounding box, with a floor for a box that is void or a point."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box, False)
    if box.IsVoid():
        return 0.1
    box.SetGap(0.0)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    return max(((xmax - xmin) + (ymax - ymin) + (zmax - zmin)) / 300.0 * DEVIATION, 1e-4)


def shape_bytes(shape: Any) -> bytes:
    """A part's shape as the kernel's binary shape bytes, geometry only (no triangulation),
    at the identity: the canonical form a part is meshed from."""
    from OCP.BinTools import BinTools, BinTools_FormatVersion_VERSION_4
    from OCP.TopLoc import TopLoc_Location

    stream = io.BytesIO()
    BinTools.Write_s(shape.Located(TopLoc_Location()), stream, False, False, BinTools_FormatVersion_VERSION_4)
    return stream.getvalue()


def read_shape(data: bytes) -> Any:
    """The shape of ``shape_bytes``: a fresh one, sharing nothing with any other."""
    from OCP.BinTools import BinTools
    from OCP.TopoDS import TopoDS_Shape

    shape = TopoDS_Shape()
    BinTools.Read_s(shape, io.BytesIO(bytes(data)))
    return shape


def mesh_shape(shape: Any, *, parallel: bool = True) -> Mesh:
    """Mesh one part (a shape, or its ``shape_bytes``) at its own frame: its location is not
    applied. ``parallel`` is the kernel's face parallelism; it does not change the result."""
    from OCP.BRepMesh import BRepMesh_IncrementalMesh

    local = read_shape(shape if isinstance(shape, (bytes, bytearray, memoryview)) else shape_bytes(shape))
    linear = deflection(local)
    BRepMesh_IncrementalMesh(local, linear, False, ANGULAR, parallel)
    mesh = _extract(local)
    mesh.linear, mesh.angular = linear, ANGULAR
    return mesh


# --- the B-rep's own facts -----------------------------------------------------------------


def _unique_faces(owners: Any) -> list[Any]:
    """The distinct faces of an ancestor list (a seam's face is in it twice). Read with
    ``First``/``Last``: the bound list's Python iterator costs milliseconds per list."""
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_ListOfShape

    size = owners.Size()
    if size == 0:
        listed = []
    elif size <= 2:
        listed = [owners.First(), owners.Last()][:size]
    else:
        rest = TopTools_ListOfShape()
        rest.Assign(owners)
        listed = []
        while not rest.IsEmpty():
            listed.append(rest.First())
            rest.RemoveFirst()
    found: list[Any] = []
    for owner in listed:
        if not any(owner.IsSame(held) for held in found):
            found.append(owner)
    return [TopoDS.Face_s(face) for face in found]


def edge_class(edge: Any, faces: list[Any]) -> int:
    """One edge's class from its distinct faces (see the module docstring)."""
    from OCP.BRep import BRep_Tool
    from OCP.GeomAbs import GeomAbs_G1

    if any(BRep_Tool.IsClosed_s(edge, face) for face in faces):
        return SEAM
    if len(faces) == 1:
        return BOUNDARY
    if len(faces) > 2:
        return NONMANIFOLD
    if len(faces) == 2 and int(BRep_Tool.Continuity_s(edge, faces[0], faces[1])) >= int(GeomAbs_G1):
        return TANGENT
    return FEATURE


def check(face: Any) -> str:
    """What ``BRepCheck_Analyzer`` says of one face: ``valid``, or each fault it finds on
    the face, its wires and its edges, the wires' first ("wire 1: SelfIntersectingWire;
    face: UnorientableShape")."""
    import OCP.BRepCheck as brepcheck
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_WIRE
    from OCP.TopExp import TopExp_Explorer

    analyzer = BRepCheck_Analyzer(face)

    def faults(statuses: Any) -> list[str]:
        return [str(status).rsplit(".", 1)[-1].removeprefix("BRepCheck_") for status in statuses
                if status != brepcheck.BRepCheck_NoError]

    found: list[str] = []
    for kind, label in ((TopAbs_WIRE, "wire"), (TopAbs_EDGE, "edge")):
        explorer = TopExp_Explorer(face, kind)
        at = 0
        while explorer.More():
            at += 1
            result = analyzer.Result(explorer.Current())
            if result is not None:
                for text in dict.fromkeys(faults(result.Status()) + faults(result.StatusOnShape(face))):
                    found.append(f"{label} {at}: {text}")
            explorer.Next()
    result = analyzer.Result(face)
    if result is not None:
        found += [f"face: {text}" for text in dict.fromkeys(faults(result.Status()))]
    if not found:
        return "valid"
    shown = list(dict.fromkeys(found))
    return "; ".join(shown[:6]) + (f"; and {len(shown) - 6} more" if len(shown) > 6 else "")


def _kind(value: Any) -> str:
    return str(getattr(value, "name", value)).rsplit(".", 1)[-1].removeprefix("GeomAbs_").lower()


def _point(point: Any) -> list[float]:
    return [point.X(), point.Y(), point.Z()]


class Topology:
    """A part's faces, edges and vertices by ordinal, and what the kernel measures of each
    when asked (``face``, ``edge``, ``vertex``): nothing is measured ahead. It reads the
    part as the mesher does, from its shape bytes, so continuity it encodes is written on
    no one else's shape."""

    def __init__(self, shape: Any) -> None:
        from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_VERTEX
        from OCP.TopExp import TopExp
        from OCP.TopTools import TopTools_IndexedMapOfShape

        self.shape = read_shape(shape if isinstance(shape, (bytes, bytearray, memoryview)) else shape_bytes(shape))
        self.maps = {}
        for name, kind in (("face", TopAbs_FACE), ("edge", TopAbs_EDGE), ("vertex", TopAbs_VERTEX)):
            found = TopTools_IndexedMapOfShape()
            TopExp.MapShapes_s(self.shape, kind, found)
            self.maps[name] = found
        self._ancestors: Any = None

    def _get(self, kind: str, ordinal: int) -> Any:
        from OCP.TopoDS import TopoDS

        found = self.maps[kind]
        if not 1 <= ordinal <= found.Extent():
            raise KeyError(f"the part has no {kind} {ordinal} (it has {found.Extent()})")
        shape = found.FindKey(ordinal)
        return {"face": TopoDS.Face_s, "edge": TopoDS.Edge_s, "vertex": TopoDS.Vertex_s}[kind](shape)

    def face(self, ordinal: int) -> dict[str, Any]:
        from OCP.Bnd import Bnd_Box
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.BRepBndLib import BRepBndLib
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps

        face = self._get("face", ordinal)
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, props)
        box = Bnd_Box()
        BRepBndLib.AddOptimal_s(face, box, False, False)
        bounds = list(box.Get()) if not box.IsVoid() else None
        return {"kind": "face", "ordinal": ordinal, "area": props.Mass(), "centroid": _point(props.CentreOfMass()),
                "surface": _kind(BRepAdaptor_Surface(face, False).GetType()),
                "box": None if bounds is None else {"min": bounds[:3], "max": bounds[3:]}, "check": check(face)}

    def edge(self, ordinal: int) -> dict[str, Any]:
        from OCP.BRep import BRep_Tool
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.BRepGProp import BRepGProp
        from OCP.BRepLib import BRepLib
        from OCP.GProp import GProp_GProps
        from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
        from OCP.TopExp import TopExp
        from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape

        edge = self._get("edge", ordinal)
        if self._ancestors is None:
            BRepLib.EncodeRegularity_s(self.shape, 1e-3)  # as the mesher classes edges
            self._ancestors = TopTools_IndexedDataMapOfShapeListOfShape()
            TopExp.MapShapesAndAncestors_s(self.shape, TopAbs_EDGE, TopAbs_FACE, self._ancestors)
        degenerate = BRep_Tool.Degenerated_s(edge)
        at = self._ancestors.FindIndex(edge)
        faces = _unique_faces(self._ancestors.FindFromIndex(at)) if at else []
        kind = 0 if degenerate else (edge_class(edge, faces) if faces else BOUNDARY)
        props = GProp_GProps()
        BRepGProp.LinearProperties_s(edge, props)
        first, last = TopExp.FirstVertex_s(edge, True), TopExp.LastVertex_s(edge, True)
        vertices = self.maps["vertex"]
        return {"kind": "edge", "ordinal": ordinal, "length": props.Mass(),
                "curve": "degenerate" if degenerate else _kind(BRepAdaptor_Curve(edge).GetType()),
                "class": CLASS_NAMES.get(kind, "degenerate"), "classCode": kind,
                "faces": [self.maps["face"].FindIndex(face) for face in faces],
                "endpoints": [_point(BRep_Tool.Pnt_s(vertex)) for vertex in (first, last) if not vertex.IsNull()],
                "vertices": [vertices.FindIndex(vertex) for vertex in (first, last) if not vertex.IsNull()]}

    def vertex(self, ordinal: int) -> dict[str, Any]:
        from OCP.BRep import BRep_Tool

        vertex = self._get("vertex", ordinal)
        return {"kind": "vertex", "ordinal": ordinal, "position": _point(BRep_Tool.Pnt_s(vertex)),
                "tolerance": BRep_Tool.Tolerance_s(vertex)}


# --- extraction ----------------------------------------------------------------------------


@dataclass
class _Face:
    ordinal: int
    triangulation: int  # its index among the distinct triangulations, -1 for none
    transform: np.ndarray | None  # 3x4, or None at the identity
    reversed: bool


def _transform(location: Any) -> np.ndarray | None:
    if location.IsIdentity():
        return None
    value = location.Transformation().Value
    return np.array([[value(row, column) for column in (1, 2, 3, 4)] for row in (1, 2, 3)])


def _extract(shape: Any) -> Mesh:
    from OCP.BinTools import BinTools, BinTools_FormatVersion_VERSION_4
    from OCP.BRep import BRep_Builder, BRep_Tool
    from OCP.BRepLib import BRepLib, BRepLib_ToolTriangulatedShape
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED, TopAbs_VERTEX
    from OCP.TopExp import TopExp
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS, TopoDS_Compound, TopoDS_Edge, TopoDS_Face
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape, TopTools_IndexedMapOfShape

    faces = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_FACE, faces)
    builder = BRep_Builder()
    holder = TopoDS_Compound()
    builder.MakeCompound(holder)
    triangulations: dict[Any, int] = {}
    held: list[Any] = []
    face_rows: list[_Face] = []
    by_face: dict[Any, int] = {}
    unmeshed: dict[int, str] = {}
    for ordinal in range(1, faces.Extent() + 1):
        face = TopoDS.Face_s(faces.FindKey(ordinal))
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face, location)
        if triangulation is None or triangulation.NbTriangles() == 0:
            face_rows.append(_Face(ordinal, -1, None, False))
            unmeshed[ordinal] = check(face)
            continue
        tshape = face.TShape()
        index = triangulations.get(tshape)
        if index is None:
            BRepLib_ToolTriangulatedShape.ComputeNormals_s(face, triangulation)
            index = triangulations[tshape] = len(held)
            held.append(triangulation)
            bare = TopoDS_Face()
            builder.MakeFace(bare, triangulation)
            builder.Add(holder, bare)
        face_rows.append(_Face(ordinal, index, _transform(location), face.Orientation() == TopAbs_REVERSED))
        by_face[tshape] = index

    BRepLib.EncodeRegularity_s(shape, 1e-3)  # the copy's own edges: continuity for the classes
    edges = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, edges)
    ancestors = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, ancestors)
    polygons: dict[tuple, int] = {}
    polygon_list: list[Any] = []
    # ordinal, class, polygon (-1 for none), its face's transform
    edge_rows: list[tuple[int, int, int, np.ndarray | None]] = []
    for ordinal in range(1, edges.Extent() + 1):
        edge = TopoDS.Edge_s(edges.FindKey(ordinal))
        if BRep_Tool.Degenerated_s(edge):
            continue
        at = ancestors.FindIndex(edge)
        owners = _unique_faces(ancestors.FindFromIndex(at)) if at else []
        kind = edge_class(edge, owners) if owners else BOUNDARY
        found = None
        for face in owners:
            location = TopLoc_Location()
            triangulation = BRep_Tool.Triangulation_s(face, location)
            index = by_face.get(face.TShape()) if triangulation is not None else None
            if index is None:
                continue
            polygon = BRep_Tool.PolygonOnTriangulation_s(edge, triangulation, location)
            if polygon is not None:
                found = (polygon, triangulation, index, location)
                break
        if found is None:
            edge_rows.append((ordinal, kind, -1, None))
            continue
        polygon, triangulation, face_index, location = found
        key = (edge.TShape(), face_index)
        index = polygons.get(key)
        if index is None:
            index = polygons[key] = len(polygon_list)
            polygon_list.append((polygon, face_index))
            bare = TopoDS_Edge()
            builder.MakeEdge(bare)
            builder.UpdateEdge(bare, polygon, triangulation)
            builder.Add(holder, bare)
        edge_rows.append((ordinal, kind, index, _transform(location)))

    vertex_map = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_VERTEX, vertex_map)
    vertices = np.zeros((vertex_map.Extent(), 4), dtype=np.float32)
    for ordinal in range(1, vertex_map.Extent() + 1):
        point = BRep_Tool.Pnt_s(TopoDS.Vertex_s(vertex_map.FindKey(ordinal)))
        vertices[ordinal - 1] = (ordinal, point.X(), point.Y(), point.Z())

    stream = io.BytesIO()
    BinTools.Write_s(holder, stream, True, True, BinTools_FormatVersion_VERSION_4)
    read = _read_dump(stream.getvalue(), len(held), len(polygon_list))
    if read is None:
        read = _read_accessors(held, [polygon for polygon, _index in polygon_list])
    mesh = _assemble(face_rows, edge_rows, polygon_list, *read, vertices)
    mesh.unmeshed = unmeshed
    return mesh


def _section(data: bytes, name: bytes, start: int) -> tuple[int, int]:
    at = data.find(b"\n" + name + b" ", start)
    if at < 0:
        raise ValueError(name)
    end = data.index(b"\n", at + 1)
    return int(data[at + len(name) + 2:end]), end + 1


def _read_dump(data: bytes, expected: int, expected_polygons: int) -> tuple[list, list] | None:
    """The triangulations and polygons of a ``BinTools`` dump (format 4), or None when the
    dump does not read as one: the counts differ, or the shapes section is not where the
    arrays end."""
    try:
        count, at = _section(data, b"PolygonOnTriangulations", 0)
        if count != expected_polygons:
            return None
        polygons = []
        for _ in range(count):
            n = int(np.frombuffer(data, "<i4", 1, at)[0])
            at += 4
            polygons.append(np.frombuffer(data, "<i4", n, at))
            at += 4 * n + 8  # the indices, the deflection
            has_parameters = data[at]
            at += 1
            if has_parameters:
                at += 8 * n
        count, at = _section(data, b"Triangulations", at)
        if count != expected:
            return None
        triangulations = []
        for _ in range(count):
            nodes, triangles = (int(value) for value in np.frombuffer(data, "<i4", 2, at))
            has_uv, has_normals = data[at + 8], data[at + 9]
            at += 18  # the counts, the two flags, the deflection
            points = np.frombuffer(data, "<f8", 3 * nodes, at).reshape(-1, 3)
            at += 24 * nodes
            if has_uv:
                at += 16 * nodes
            corners = np.frombuffer(data, "<i4", 3 * triangles, at).reshape(-1, 3)
            at += 12 * triangles
            normals = None
            if has_normals:
                normals = np.frombuffer(data, "<f4", 3 * nodes, at).reshape(-1, 3)
                at += 12 * nodes
            triangulations.append((points, corners, normals))
        if not data.startswith(b"\nTShapes ", at):
            return None
        return triangulations, polygons
    except (ValueError, IndexError):
        return None


def _read_accessors(held: list, polygons: list) -> tuple[list, list]:
    """The same arrays read one node at a time, through the kernel's accessors."""
    triangulations = []
    for triangulation in held:
        nodes = triangulation.NbNodes()
        points = np.array([triangulation.Node(i).Coord() for i in range(1, nodes + 1)], dtype=float).reshape(-1, 3)
        corners = np.array([triangulation.Triangle(i).Get() for i in range(1, triangulation.NbTriangles() + 1)],
                           dtype=np.int32).reshape(-1, 3)
        normals = None
        if triangulation.HasNormals():
            normals = np.array([(lambda d: (d.X(), d.Y(), d.Z()))(triangulation.Normal(i))
                                for i in range(1, nodes + 1)], dtype=np.float32).reshape(-1, 3)
        triangulations.append((points, corners, normals))
    found = [np.array([polygon.Node(i) for i in range(1, polygon.NbNodes() + 1)], dtype=np.int32)
             for polygon in polygons]
    return triangulations, found


def _assemble(face_rows: list[_Face], edge_rows: list, polygon_list: list, triangulations: list,
              polygons: list, vertices: np.ndarray) -> Mesh:
    positions, normals, indices = [], [], []
    face_table = np.zeros((len(face_rows), 3), dtype=np.uint32)
    vertex_base = 0
    index_base = 0
    for row, face in enumerate(face_rows):
        face_table[row, 0] = face.ordinal
        face_table[row, 1] = index_base
        if face.triangulation < 0:
            continue
        points, corners, stored = triangulations[face.triangulation]
        if face.transform is not None:
            points = points @ face.transform[:, :3].T + face.transform[:, 3]
        corners = corners.astype(np.int64) - 1
        if face.reversed:
            corners = corners[:, [0, 2, 1]]
        a, b, c = points[corners[:, 0]], points[corners[:, 1]], points[corners[:, 2]]
        geometric = np.cross(b - a, c - a)
        if stored is not None and len(stored) == len(points):
            vertex_normals = stored.astype(np.float64)
            if face.transform is not None:
                vertex_normals = vertex_normals @ face.transform[:, :3].T
            # A triangulation is shared by both orientations of its face: its normals
            # point whichever way the first one to compute them faced.
            if np.einsum("ij,ij->", geometric, vertex_normals[corners[:, 0]]) < 0:
                vertex_normals = -vertex_normals
        else:
            vertex_normals = np.zeros_like(points)
            for column in range(3):
                np.add.at(vertex_normals, corners[:, column], geometric)
            lengths = np.linalg.norm(vertex_normals, axis=1, keepdims=True)
            vertex_normals = vertex_normals / np.where(lengths > 0, lengths, 1)
        positions.append(points.astype(np.float32))
        normals.append(vertex_normals.astype(np.float32))
        indices.append((corners + vertex_base).astype(np.uint32).ravel())
        vertex_base += len(points)
        index_base += corners.size
        face_table[row, 2] = corners.size
    edge_table = np.zeros((len(edge_rows), 4), dtype=np.uint32)
    lines = []
    point_base = 0
    for row, (ordinal, kind, index, transform) in enumerate(edge_rows):
        if index < 0:
            edge_table[row] = (ordinal, point_base, 0, kind)
            continue
        polygon = polygons[index]
        points, _corners, _normals = triangulations[polygon_list[index][1]]
        line = points[polygon.astype(np.int64) - 1]
        if transform is not None:
            line = line @ transform[:, :3].T + transform[:, 3]
        lines.append(line.astype(np.float32))
        edge_table[row] = (ordinal, point_base, len(line), kind)
        point_base += len(line)

    def joined(items: list, width: int, dtype: Any) -> np.ndarray:
        return np.concatenate(items) if items else np.zeros((0, width) if width > 1 else 0, dtype=dtype)

    return Mesh(joined(positions, 3, np.float32), joined(normals, 3, np.float32), joined(indices, 1, np.uint32),
                face_table, edge_table, joined(lines, 3, np.float32), vertices)
