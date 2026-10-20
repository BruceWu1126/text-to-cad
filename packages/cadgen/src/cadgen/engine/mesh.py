"""Tessellation: one part as one buffer a renderer reads without parsing.

A part is meshed with ``BRepMesh_IncrementalMesh`` at a linear deflection relative to
its bounding diagonal (``RELATIVE``) and an angular deflection (``ANGULAR``), and read
out as plain arrays: float32 positions and normals, uint32 triangle indices, a face
table of (face ordinal, first index, index count), an edge table of (edge ordinal,
first point, point count) and the edges' polylines, taken from each edge's polygon on
its first face's triangulation. Ordinals are ``TopExp.MapShapes`` ordinals of the
part, the ordinals its element names are kept by. Selecting a face is a range of the
index buffer; an edge, a range of the polyline buffer.

The arrays are read from the kernel without a Python loop per node: the part's
triangulations and polygons are written, as triangulation-only faces and edges, into
the kernel's binary shape format in memory (``BinTools``) and read back with numpy
from that one buffer; a buffer that does not read back as expected falls back to the
kernel's accessors, one node at a time.

The wire format (``Mesh.to_bytes``): a 64-byte little-endian header -- the magic
``XBM1``, the format version, the counts of vertices, indices, faces, edges and edge
points, the byte offset of each of the six arrays, and the two deflections -- then the
arrays, each 4-byte aligned, at their offsets.
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass
from typing import Any

import numpy as np

__all__ = ["ANGULAR", "HEADER", "MAGIC", "Mesh", "RELATIVE", "deflection", "mesh_shape"]

# Linear deflection as a fraction of the part's bounding diagonal, and the angular one
# in radians: a part looks alike at any size.
RELATIVE = 0.0015
ANGULAR = 0.35
MAGIC = b"XBM1"
VERSION = 1
HEADER = struct.Struct("<4s12I2fI")
assert HEADER.size == 64


@dataclass
class Mesh:
    positions: np.ndarray  # (V, 3) float32
    normals: np.ndarray  # (V, 3) float32
    indices: np.ndarray  # (I,) uint32
    faces: np.ndarray  # (F, 3) uint32: ordinal, first index, index count
    edges: np.ndarray  # (E, 3) uint32: ordinal, first point, point count
    edge_points: np.ndarray  # (P, 3) float32
    linear: float = 0.0
    angular: float = 0.0

    @property
    def triangles(self) -> int:
        return len(self.indices) // 3

    def to_bytes(self) -> bytes:
        arrays = [np.ascontiguousarray(self.positions, "<f4"), np.ascontiguousarray(self.normals, "<f4"),
                  np.ascontiguousarray(self.indices, "<u4"), np.ascontiguousarray(self.faces, "<u4"),
                  np.ascontiguousarray(self.edges, "<u4"), np.ascontiguousarray(self.edge_points, "<f4")]
        offsets = []
        at = HEADER.size
        for array in arrays:
            offsets.append(at)
            at += array.nbytes  # every element is 4 bytes: every offset stays aligned
        header = HEADER.pack(MAGIC, VERSION, len(self.positions), len(self.indices), len(self.faces),
                             len(self.edges), len(self.edge_points), *offsets, self.linear, self.angular, 0)
        return b"".join([header, *(array.tobytes() for array in arrays)])

    @staticmethod
    def from_bytes(data: bytes) -> "Mesh":
        (magic, version, vertices, indices, faces, edges, points, *rest) = HEADER.unpack_from(data)
        if magic != MAGIC or version != VERSION:
            raise ValueError("not a mesh buffer")
        offsets, (linear, angular, _reserved) = rest[:6], rest[6:]

        def view(at: int, dtype: str, count: int, width: int) -> np.ndarray:
            array = np.frombuffer(data, dtype, count * width, offsets[at])
            return array.reshape(-1, width) if width > 1 else array

        return Mesh(view(0, "<f4", vertices, 3), view(1, "<f4", vertices, 3), view(2, "<u4", indices, 1),
                    view(3, "<u4", faces, 3), view(4, "<u4", edges, 3), view(5, "<f4", points, 3), linear, angular)


def deflection(shape: Any, relative: float = RELATIVE) -> float:
    """The linear deflection of ``shape``: ``relative`` of its bounding diagonal."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box, False)
    if box.IsVoid():
        return 0.1
    diagonal = box.SquareExtent() ** 0.5
    return max(diagonal * relative, 1e-4)


def mesh_shape(shape: Any, *, relative: float = RELATIVE, angular: float = ANGULAR, parallel: bool = True) -> Mesh:
    """Mesh one part, at its own frame (its location is not applied)."""
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopLoc import TopLoc_Location

    local = shape.Located(TopLoc_Location())
    linear = deflection(local, relative)
    BRepMesh_IncrementalMesh(local, linear, False, angular, parallel)
    mesh = _extract(local)
    mesh.linear, mesh.angular = linear, angular
    return mesh


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
    from OCP.BRepLib import BRepLib_ToolTriangulatedShape
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED
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
    by_face: dict[Any, tuple[int, Any]] = {}
    for ordinal in range(1, faces.Extent() + 1):
        face = TopoDS.Face_s(faces.FindKey(ordinal))
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face, location)
        if triangulation is None:
            face_rows.append(_Face(ordinal, -1, None, False))
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
        by_face[face.TShape()] = (index, triangulation)

    edges = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, edges)
    ancestors = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, ancestors)
    polygons: dict[tuple, int] = {}
    polygon_list: list[Any] = []
    edge_rows: list[tuple[int, int, np.ndarray | None]] = []  # ordinal, polygon, its face's transform
    for ordinal in range(1, edges.Extent() + 1):
        edge = TopoDS.Edge_s(edges.FindKey(ordinal))
        if BRep_Tool.Degenerated_s(edge):
            continue
        at = ancestors.FindIndex(edge)
        if not at:
            continue
        owners = ancestors.FindFromIndex(at)
        if owners.Size() == 0:
            continue
        face = TopoDS.Face_s(owners.First())
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face, location)
        if triangulation is None:
            continue
        found = by_face.get(face.TShape())
        if found is None:
            continue
        polygon = BRep_Tool.PolygonOnTriangulation_s(edge, triangulation, location)
        if polygon is None:
            continue
        key = (edge.TShape(), found[0])
        index = polygons.get(key)
        if index is None:
            index = polygons[key] = len(polygon_list)
            polygon_list.append((polygon, found[0]))
            bare = TopoDS_Edge()
            builder.MakeEdge(bare)
            builder.UpdateEdge(bare, polygon, triangulation)
            builder.Add(holder, bare)
        edge_rows.append((ordinal, index, _transform(location)))

    stream = io.BytesIO()
    BinTools.Write_s(holder, stream, True, True, BinTools_FormatVersion_VERSION_4)
    read = _read_dump(stream.getvalue(), len(held), len(polygon_list))
    if read is None:
        read = _read_accessors(held, [polygon for polygon, _index in polygon_list])
    return _assemble(face_rows, edge_rows, polygon_list, *read)


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
              polygons: list) -> Mesh:
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
    edge_table = np.zeros((len(edge_rows), 3), dtype=np.uint32)
    lines = []
    point_base = 0
    for row, (ordinal, index, transform) in enumerate(edge_rows):
        polygon = polygons[index]
        points, _corners, _normals = triangulations[polygon_list[index][1]]
        line = points[polygon.astype(np.int64) - 1]
        if transform is not None:
            line = line @ transform[:, :3].T + transform[:, 3]
        lines.append(line.astype(np.float32))
        edge_table[row] = (ordinal, point_base, len(line))
        point_base += len(line)

    def joined(items: list, width: int, dtype: Any) -> np.ndarray:
        return np.concatenate(items) if items else np.zeros((0, width) if width > 1 else 0, dtype=dtype)

    return Mesh(joined(positions, 3, np.float32), joined(normals, 3, np.float32), joined(indices, 1, np.uint32),
                face_table, edge_table, joined(lines, 3, np.float32))
