"""The files ``cadgen cam plan`` writes: the result GLB, and the toolpaths as the GLB draws them.

The GLB is what the viewer shows, and any glTF viewer shows it too: the part, the stock, each setup's fixture and each
tool as triangle meshes, each operation's toolpath as a real ``LINES`` primitive. Written by hand (glTF 2.0 is a JSON
header and one binary buffer) like FEA's (``_internal/fea/outputs.py``), and byte-deterministic (README law 5): the
same meshes, paths and extras give the same bytes.

The contract the viewer reads (``camResult.js``) is below; change it only with the viewer.

Space
    One frame for the whole file: the PART's frame (the STEP's own coordinates), in glTF metres and axes like every
    cadgen GLB: CAD ``(x, y, z)`` mm is glTF ``(x, z, -y) / 1000``. Each setup's toolpaths are moved back from the
    setup's frame to the part's (``Frame.point_to_part``); ``extras.frame`` says so.

Nodes, in this order (``scenes[0].nodes`` lists them all; every node but a tool's has no transform)
    - ``part``: mesh 0, the part's faces, with the result's extras on ``meshes[0].extras`` (a mesh, since the viewer's
      check skips non-meshes). Its ``_FACE`` attribute (float) is each vertex's face ordinal, 1-based: the
      ``#o1.fN`` selector's ``N``.
    - ``stock``: the stock as it starts (phase 2 adds one per operation, after it).
    - ``fixture:<setup id>``: the volumes that hold the stock in that setup (vise jaws and floor, a plate, a chuck),
      for each setup that has any.
    - ``tool:<tool id>``: each tool the job cuts with, revolved from its profile with its holder. Its mesh is in the
      tool's own frame (tip at the origin, axis along glTF +Y, which is CAD +Z); the node's ``translation`` puts the
      tip where the tool's first toolpath starts and its ``rotation`` is that toolpath's setup's
      (``extras.setups[i].quaternion``). A viewer playing the job sets both from the path at time t.
    - ``path:<op index>:<op name>``: one per operation that moves the tool, a ``LINES`` primitive (mode 1, two
      vertices per segment, no indices) through all its toolpaths in running order, with four float vertex
      attributes: ``_T`` the job time at that vertex in seconds (non-decreasing along the primitive; a dwell is the
      step from one segment's end to the next one's start), ``_KIND`` the move's kind (:data:`KIND_CODES`), ``_OP``
      the operation's index in the job, ``_TOOL`` the index in ``extras.tools`` (the job's ``tools``, in order) of
      the tool cutting that segment: an operation may run more than one (an engraving's ``clear_tool`` first), so a
      viewer playing the job takes the tool from the segment, not from the operation. Arcs are drawn as chords within :data:`ARC_DEFLECTION_MM` of the arc,
      helices included; a canned cycle as the moves it stands for (``toolpath.expand_cycle``), all of kind ``cycle``.

Numpy is imported inside the functions; this module is stdlib-only at import.
"""

from __future__ import annotations

import json
import math
import struct
from typing import TYPE_CHECKING, Any, Sequence

if TYPE_CHECKING:
    import numpy as np

    from cadgen._internal.cam.toolpath import Toolpath

__all__ = ["ARC_DEFLECTION_MM", "GENERATOR", "KIND_CODES", "Glb", "gltf_space", "gltf_quaternion", "lines_of",
           "path_segments", "tessellate"]

GENERATOR = "cadgen cam"
#: ``_KIND`` per move kind: both arc directions are one code, and every move a canned cycle stands for is ``cycle``.
KIND_CODES = {"rapid": 0, "feed": 1, "plunge": 2, "arc_cw": 3, "arc_ccw": 3, "lead": 4, "cycle": 5}
#: How far a drawn chord may stand off its arc, mm.
ARC_DEFLECTION_MM = 0.05

_ARRAY, _ELEMENT = 34962, 34963
_FLOAT, _UINT = 5126, 5125
_TRIANGLES, _LINES = 4, 1
#: The materials, by name, in the order the file lists them: plain PBR colours a viewer's theme may replace.
_MATERIALS = (
    ("part", {"pbrMetallicRoughness": {"baseColorFactor": [0.72, 0.74, 0.77, 1.0], "metallicFactor": 0.3,
                                       "roughnessFactor": 0.6}}),
    ("stock", {"alphaMode": "BLEND", "doubleSided": True,
               "pbrMetallicRoughness": {"baseColorFactor": [0.55, 0.62, 0.72, 0.22], "metallicFactor": 0.0,
                                        "roughnessFactor": 0.9}}),
    # A see-through steel, never shiny: lit, an opaque grey fixture reads as the part's grey and the part is lost on it.
    ("fixture", {"alphaMode": "BLEND", "doubleSided": True,
                 "pbrMetallicRoughness": {"baseColorFactor": [0.23, 0.29, 0.37, 0.4], "metallicFactor": 0.0,
                                          "roughnessFactor": 0.9}}),
    ("tool", {"pbrMetallicRoughness": {"baseColorFactor": [0.85, 0.66, 0.2, 1.0], "metallicFactor": 0.8,
                                       "roughnessFactor": 0.35}}),
    ("toolpath", {"pbrMetallicRoughness": {"baseColorFactor": [0.1, 0.45, 0.95, 1.0], "metallicFactor": 0.0,
                                           "roughnessFactor": 1.0}}),
)
_MATERIAL_INDEX = {name: index for index, (name, _) in enumerate(_MATERIALS)}


def gltf_space(points_mm) -> "np.ndarray":
    """CAD ``(x, y, z)`` mm -> glTF ``(x, z, -y)`` metres, float32: the convention of every cadgen GLB."""
    import numpy as np

    points = np.asarray(points_mm, dtype=float).reshape(-1, 3)
    out = np.empty(points.shape, dtype=np.float32)
    out[:, 0] = points[:, 0] / 1000.0
    out[:, 1] = points[:, 2] / 1000.0
    out[:, 2] = -points[:, 1] / 1000.0
    return out


def gltf_quaternion(rotation) -> list[float]:
    """The glTF rotation ``[x, y, z, w]`` that takes a direction in a setup's frame to the part's, both in glTF axes.

    ``rotation`` is the setup's (``Frame.rotation``: rows are the setup's axes in the part's frame), so the CAD map is
    its transpose, and in glTF axes ``C R^T C^T`` with ``C`` the axis swap of :func:`gltf_space`. Rounded to 9
    places, ``w`` made 0 or more, so one rotation has one spelling.
    """
    import numpy as np

    c = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]])
    m = c @ np.asarray(rotation, dtype=float).T @ c.T
    trace = m[0, 0] + m[1, 1] + m[2, 2]
    if trace > 0:
        s = 2.0 * math.sqrt(trace + 1.0)
        q = ((m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s, 0.25 * s)
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = 2.0 * math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2])
        q = (0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s, (m[2, 1] - m[1, 2]) / s)
    elif m[1, 1] > m[2, 2]:
        s = 2.0 * math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2])
        q = ((m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s, (m[0, 2] - m[2, 0]) / s)
    else:
        s = 2.0 * math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1])
        q = ((m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s, (m[1, 0] - m[0, 1]) / s)
    norm = math.sqrt(sum(value * value for value in q))
    q = [value / norm for value in q]
    if q[3] < 0:
        q = [-value for value in q]
    return [round(value, 9) + 0.0 for value in q]


# --- meshes ---------------------------------------------------------------------------------------------

def tessellate(shape: Any, *, deflection_mm: float, faces: bool = False):
    """``(positions, triangles, face_of_vertex)``: a solid's surface as triangles, mm, wound outward.

    One vertex list per face (so a sharp edge stays sharp under vertex normals); ``face_of_vertex`` is each vertex's
    face ordinal in the shape's own face numbering (1-based, the ``#fN`` of a selector) when ``faces``, else ``None``.
    The shape is copied before meshing (meshing writes into a shape), and meshed on one thread, so the same shape
    gives the same triangles.
    """
    import numpy as np
    from OCP.BRep import BRep_Tool
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopAbs import TopAbs_REVERSED
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    from cadgen._internal.cam.stock import occ_shape
    from cadgen._internal.entity_ordinals import entity_map

    copy = BRepBuilderAPI_Copy(occ_shape(shape)).Shape()
    BRepMesh_IncrementalMesh(copy, deflection_mm, False, 0.3, False)
    face_map = entity_map(copy, "face")
    positions: list = []
    triangles: list = []
    owner: list = []
    count = 0
    for ordinal in range(1, face_map.Extent() + 1):
        face = TopoDS.Face_s(face_map.FindKey(ordinal))
        location = TopLoc_Location()
        mesh = BRep_Tool.Triangulation_s(face, location)
        if mesh is None:
            continue
        trsf = location.Transformation()
        nodes = []
        for k in range(1, mesh.NbNodes() + 1):
            point = mesh.Node(k).Transformed(trsf)
            nodes.append((point.X(), point.Y(), point.Z()))
        flip = face.Orientation() == TopAbs_REVERSED
        for k in range(1, mesh.NbTriangles() + 1):
            a, b, c = mesh.Triangle(k).Get()
            triangles.append((count + a - 1, count + c - 1, count + b - 1) if flip else
                             (count + a - 1, count + b - 1, count + c - 1))
        positions.extend(nodes)
        owner.extend([ordinal] * len(nodes))
        count += len(nodes)
    return (np.asarray(positions, dtype=float).reshape(-1, 3), np.asarray(triangles, dtype=np.uint32).reshape(-1, 3),
            np.asarray(owner, dtype=float) if faces else None)


def _vertex_normals(positions: "np.ndarray", triangles: "np.ndarray") -> "np.ndarray":
    import numpy as np

    normals = np.zeros_like(positions, dtype=float)
    if len(triangles):
        a = positions[triangles[:, 1]] - positions[triangles[:, 0]]
        b = positions[triangles[:, 2]] - positions[triangles[:, 0]]
        face = np.cross(a, b)
        for corner in range(3):
            np.add.at(normals, triangles[:, corner], face)
    length = np.linalg.norm(normals, axis=1, keepdims=True)
    length[length == 0] = 1.0
    return (normals / length).astype(np.float32)


# --- toolpaths as lines ----------------------------------------------------------------------------------

def _arc_points(start, move, deflection: float) -> list[tuple[float, float, float]]:
    """The points after ``start`` along an arc or helix, every chord within ``deflection`` of it."""
    from cadgen._internal.cam.toolpath import arc_sweep

    cx, cy = move.center
    radius = math.hypot(start[0] - cx, start[1] - cy)
    sweep = arc_sweep(start, move)
    step = 2.0 * math.acos(1.0 - deflection / radius) if radius > deflection else math.pi / 2
    count = max(1, math.ceil(abs(sweep) / step - 1e-9))
    a0 = math.atan2(start[1] - cy, start[0] - cx)
    points = []
    for i in range(1, count):
        f = i / count
        angle = a0 + sweep * f
        points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle), start[2] + (move.xyz[2] - start[2]) * f))
    points.append(tuple(move.xyz))
    return points


def path_segments(toolpath: "Toolpath", *, t0: float, rapid_mm_min: float,
                  deflection: float = ARC_DEFLECTION_MM) -> list[tuple[tuple, tuple, float, float, int]]:
    """A timed toolpath as straight segments ``(start, end, t_start, t_end, kind code)``, setup frame, mm, s.

    ``t0`` is the job time the toolpath starts at (the moves' ``t`` count on from it). A move takes its time over its
    length; its dwell follows it, so the next segment starts that much later. A segment of no length is left out.
    """
    from cadgen._internal.cam.toolpath import ARC_KINDS, expand_cycle, move_seconds, walk

    out = []
    before = float(t0)
    for here, move in walk(toolpath.moves, toolpath.start):
        if move.kind == "cycle":
            t = before
            for sub_here, sub in walk(expand_cycle(move, here), here):
                seconds = move_seconds(sub_here, sub, rapid_mm_min=rapid_mm_min)
                if tuple(sub_here) != tuple(sub.xyz):
                    out.append((tuple(sub_here), tuple(sub.xyz), t, t + seconds - sub.dwell_s, KIND_CODES["cycle"]))
                t += seconds
        else:
            motion = max(move.t - before - move.dwell_s, 0.0)
            points = [tuple(here)] + (_arc_points(here, move, deflection) if move.kind in ARC_KINDS else [tuple(move.xyz)])
            spans = len(points) - 1
            for i in range(spans):
                if points[i] != points[i + 1]:
                    out.append((points[i], points[i + 1], before + motion * i / spans, before + motion * (i + 1) / spans,
                                KIND_CODES[move.kind]))
        before = move.t
    return out


# --- the file -----------------------------------------------------------------------------------------------

class Glb:
    """A glTF 2.0 binary being assembled: meshes and nodes in the order they are added, one buffer."""

    def __init__(self):
        self.blobs: list[bytes] = []
        self.views: list[dict] = []
        self.accessors: list[dict] = []
        self.meshes: list[dict] = []
        self.nodes: list[dict] = []

    def _add(self, array: "np.ndarray", *, kind: str, component: int, target: int, bounds: bool = False) -> int:
        import numpy as np

        array = np.ascontiguousarray(array)
        data = array.tobytes()
        offset = sum(len(blob) for blob in self.blobs)
        self.blobs.append(data + b"\0" * (-len(data) % 4))
        self.views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(data), "target": target})
        accessor = {"bufferView": len(self.views) - 1, "componentType": component, "count": int(array.shape[0]),
                    "type": kind}
        if bounds:
            accessor["min"] = [float(x) for x in array.min(axis=0)]
            accessor["max"] = [float(x) for x in array.max(axis=0)]
        self.accessors.append(accessor)
        return len(self.accessors) - 1

    def _scalar(self, values) -> int:
        import numpy as np

        return self._add(np.asarray(values, dtype=np.float32).reshape(-1, 1), kind="SCALAR", component=_FLOAT,
                         target=_ARRAY)

    def triangles(self, name: str, positions_mm, triangles, *, material: str, extras: dict | None = None,
                  attributes: dict | None = None, translation=None, rotation=None) -> int | None:
        """A triangle mesh from CAD mm (written in glTF metres and axes) and its node; ``None`` (nothing added) when
        it has no triangles. ``attributes`` are more float scalars per vertex (``_FACE``)."""
        import numpy as np

        triangles = np.asarray(triangles, dtype=np.uint32).reshape(-1, 3)
        if not len(triangles):
            return None
        pos = gltf_space(positions_mm)
        attrs = {
            "POSITION": self._add(pos, kind="VEC3", component=_FLOAT, target=_ARRAY, bounds=True),
            "NORMAL": self._add(_vertex_normals(pos.astype(float), triangles), kind="VEC3", component=_FLOAT,
                                target=_ARRAY),
        }
        for key, values in (attributes or {}).items():
            attrs[key] = self._scalar(values)
        primitive = {"attributes": attrs,
                     "indices": self._add(triangles.reshape(-1, 1), kind="SCALAR", component=_UINT, target=_ELEMENT),
                     "material": _MATERIAL_INDEX[material], "mode": _TRIANGLES}
        return self._mesh(name, primitive, extras, translation, rotation)

    def lines(self, name: str, points_gltf, attributes: dict, *, material: str) -> int | None:
        """A ``LINES`` mesh: ``points_gltf`` (2n, 3) already in glTF metres, segment by segment; ``attributes`` float
        scalars per vertex. ``None`` (nothing added) when there is no segment."""
        import numpy as np

        points = np.asarray(points_gltf, dtype=np.float32).reshape(-1, 3)
        if not len(points):
            return None
        attrs = {"POSITION": self._add(points, kind="VEC3", component=_FLOAT, target=_ARRAY, bounds=True)}
        for key, values in attributes.items():
            attrs[key] = self._scalar(values)
        return self._mesh(name, {"attributes": attrs, "material": _MATERIAL_INDEX[material], "mode": _LINES},
                          None, None, None)

    def _mesh(self, name, primitive, extras, translation, rotation) -> int:
        mesh = {"name": name, "primitives": [primitive]}
        if extras is not None:
            mesh["extras"] = extras
        self.meshes.append(mesh)
        node = {"mesh": len(self.meshes) - 1, "name": name}
        if translation is not None:
            node["translation"] = [float(value) for value in translation]
        if rotation is not None:
            node["rotation"] = [float(value) for value in rotation]
        self.nodes.append(node)
        return len(self.nodes) - 1

    def bytes(self) -> bytes:
        gltf = {
            "asset": {"version": "2.0", "generator": GENERATOR},
            "scene": 0,
            "scenes": [{"nodes": list(range(len(self.nodes)))}],
            "nodes": self.nodes,
            "meshes": self.meshes,
            "materials": [{"name": name, **body} for name, body in _MATERIALS],
            "buffers": [{"byteLength": sum(len(blob) for blob in self.blobs)}],
            "bufferViews": self.views,
            "accessors": self.accessors,
        }
        header = json.dumps(gltf, separators=(",", ":"), allow_nan=False).encode("utf-8")
        header += b" " * (-len(header) % 4)
        binary = b"".join(self.blobs)
        total = 12 + 8 + len(header) + 8 + len(binary)
        return b"".join([struct.pack("<4sII", b"glTF", 2, total), struct.pack("<II", len(header), 0x4E4F534A), header,
                         struct.pack("<II", len(binary), 0x004E4942), binary])


def lines_of(segments: Sequence[tuple], op: int, to_part, tools: Sequence[int]) -> tuple["np.ndarray", dict]:
    """The vertices and attributes of one operation's ``LINES``: ``segments`` from :func:`path_segments` (setup
    frame), ``to_part`` the setup's ``Frame.point_to_part``, ``tools`` each segment's tool as its index in the job's
    tools."""
    import numpy as np

    if len(tools) != len(segments):
        raise ValueError(f"internal: {len(segments)} segments and {len(tools)} tools")
    ends = np.asarray([point for segment in segments for point in segment[:2]], dtype=float).reshape(-1, 3)
    points = gltf_space(to_part(ends)) if len(ends) else np.zeros((0, 3), np.float32)
    times = [t for segment in segments for t in segment[2:4]]
    kinds = [segment[4] for segment in segments for _ in range(2)]
    return points, {"_T": times, "_KIND": kinds, "_OP": [op] * len(times),
                    "_TOOL": [tool for tool in tools for _ in range(2)]}
