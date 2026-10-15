"""Geometric identity: a component's fingerprint and its elements' tokens.

A component's bytes name its object (the cid, ``component_package``); its
**geometry** names its part and the elements a ref points at (README law 20,
STORE.md §2). Both names here are hashes of rounded geometry under one scheme
string, ``GEOMETRY_FINGERPRINT_SCHEME``, and of nothing positional: the order
OCCT lists faces, edges or vertices in never enters a fingerprint, and an
element's token holds no ordinal. So the body a build holds and the same body
read back from the STEP it wrote -- different bytes, faces listed in another
order -- have one fingerprint, and each face one token.

Rounding is relative to the body's extent: the quantum is the power of two at
or below ``RELATIVE_QUANTUM`` times the diagonal of the body's tight box (a
power of two so the quantum itself is exact and moves only when the extent
crosses one). Bodies that agree to that quantum are drawn alike at the display
chord tolerance anyway. Rounding makes the name tolerant where a byte hash is
exact: a body whose coordinates straddle a rounding boundary before and after
its round trip costs one extra mesh derivation and renames one ref, never a
wrong answer, and nothing that decides freshness or whether bytes were written
reads a fingerprint.

What is read, and why (measured over the sample corpus: STORE.md §2):

- The fingerprint hashes the scheme, the quantum (so a scaled copy is another
  body), the kinds of the body's solids (or shells, or the bare shape), its
  counts of faces, edges and vertices, its vertex points rounded and sorted,
  and its faces' signatures sorted.
- A face's signature is its surface class, its tight box rounded and its
  oriented normal at its parametric middle rounded to quarters (an orientation
  signal that tells the coincident faces of two solids apart). Not its centroid
  and not its area: both are integrals over the trim, and the STEP translation
  moves them by more than the quantum on faces whose boundary it approximates
  again, where the box's extremes survive like vertices do.
- An edge's signature is its curve class, its rounded end points as a sorted
  pair, and its rounded length. A vertex's is its rounded point.
- A surface or curve class is the kernel's type with the pairs the STEP
  translation exchanges folded together: a Bezier reads back as a B-spline, a
  torus can read back as a surface of revolution.
- Two elements of one body with equal signatures (the coincident faces of two
  solids in a compound, two vertices at one point) are told apart
  deterministically: ordered by their exact values, each after the first
  hashes its rank as well.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

__all__ = [
    "ELEMENT_TOKEN_RE",
    "GEOMETRY_FINGERPRINT_SCHEME",
    "RELATIVE_QUANTUM",
    "element_tokens",
    "fingerprint",
    "geometry_identity",
    "is_element_token",
    "quantum_for_extent",
]

# The ONLY version on a geometric name. Bump it when the same shape must be
# named differently (what the fingerprint reads, or how it rounds, changes),
# and bump cadgen.store.parts.PART_VERSION with it (STORE.md §2).
GEOMETRY_FINGERPRINT_SCHEME = "cadgen-geometry-fingerprint-v1"
# The rounding quantum as a fraction of the body's extent, before it is taken
# to a power of two: 2**-12 is 2.4e-4, half the display chord tolerance
# (tessellation_policy.DISPLAY_TESSELLATION, 5e-4 of the extent). Finer quanta
# straddle no more often over the corpus and keep more near-coincident elements
# apart; coarser ones merge them and their tiebreaks drift.
RELATIVE_QUANTUM = 2.0 ** -12
# The normal of a face is rounded to this many steps per unit: an orientation
# signal, telling the coincident faces of two solids apart, not a measurement.
_NORMAL_STEPS = 4
_HEX = 12
ELEMENT_TOKEN_RE = re.compile(r"^[fev]:[0-9a-f]{12}$")
# Types the STEP translation exchanges read as one class.
_TYPE_CLASSES = {
    "beziersurface": "spline", "bsplinesurface": "spline",
    "beziercurve": "spline", "bsplinecurve": "spline",
    "torus": "revolution", "surfaceofrevolution": "revolution",
}


def is_element_token(value: object) -> bool:
    return isinstance(value, str) and ELEMENT_TOKEN_RE.match(value) is not None


def quantum_for_extent(extent: float) -> float:
    """The rounding quantum of a body whose tight box has diagonal ``extent``:
    the power of two at or below ``RELATIVE_QUANTUM * extent``. A body with no
    extent (a vertex, a point-like shape) rounds at 2**-40."""
    scaled = float(extent) * RELATIVE_QUANTUM
    if not math.isfinite(scaled) or scaled <= 0.0:
        return 2.0 ** -40
    return 2.0 ** math.floor(math.log2(scaled))


def _digest(kind: str, material: Any) -> str:
    payload = json.dumps(material, separators=(",", ":"), sort_keys=False, allow_nan=False)
    return hashlib.sha256(f"{GEOMETRY_FINGERPRINT_SCHEME}\0{kind}\0{payload}".encode()).hexdigest()


def _rounded(value: float, quantum: float) -> int:
    scaled = value / quantum
    if not math.isfinite(scaled):
        raise ValueError("geometry coordinate is not finite")
    return int(round(scaled))


def _enum(value: Any, prefix: str) -> str:
    name = getattr(value, "name", str(value))
    name = name[len(prefix):].lower() if name.startswith(prefix) else name.lower()
    return _TYPE_CLASSES.get(name, name)


def _tight_box(shape: Any) -> tuple[float, ...] | None:
    """A face's (or a body's) tight box, six numbers; None when it bounds nothing.
    ``BRepBndLib::AddOptimal`` with no triangulation: meshing here would mutate
    the shared TShape."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    try:
        BRepBndLib.AddOptimal_s(shape, box, False, False)
    except Exception:  # noqa: BLE001 - the loose box bounds what the tight one cannot
        box = Bnd_Box()
        BRepBndLib.Add_s(shape, box, False)
    if box.IsVoid():
        return None
    return tuple(float(value) for value in box.Get())


def _face_facts(face: Any) -> dict[str, Any]:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp, BRepGProp_Face
    from OCP.BRepTools import BRepTools
    from OCP.GProp import GProp_GProps
    from OCP.gp import gp_Pnt, gp_Vec

    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    normal = [0, 0, 0]
    try:
        umin, umax, vmin, vmax = BRepTools.UVBounds_s(face)
        point, vector = gp_Pnt(), gp_Vec()
        BRepGProp_Face(face).Normal((umin + umax) / 2.0, (vmin + vmax) / 2.0, point, vector)
        length = vector.Magnitude()
        if math.isfinite(length) and length > 1e-12:
            normal = [int(round(_NORMAL_STEPS * c / length)) for c in (vector.X(), vector.Y(), vector.Z())]
    except Exception:  # noqa: BLE001 - a face whose normal cannot be read is named without one
        normal = [0, 0, 0]
    return {
        "type": _enum(BRepAdaptor_Surface(face, False).GetType(), "GeomAbs_"),
        "box": _tight_box(face),
        "normal": normal,
        "area": float(props.Mass()),
    }


def _edge_facts(edge: Any) -> dict[str, Any]:
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopExp import TopExp

    degenerate = BRep_Tool.Degenerated_s(edge)
    first = BRep_Tool.Pnt_s(TopExp.FirstVertex_s(edge, True))
    last = BRep_Tool.Pnt_s(TopExp.LastVertex_s(edge, True))
    if degenerate:
        centre, length = first, 0.0
    else:
        props = GProp_GProps()
        BRepGProp.LinearProperties_s(edge, props)
        centre, length = props.CentreOfMass(), float(props.Mass())
    return {
        "type": "degenerate" if degenerate else _enum(BRepAdaptor_Curve(edge).GetType(), "GeomAbs_"),
        "ends": sorted([(first.X(), first.Y(), first.Z()), (last.X(), last.Y(), last.Z())]),
        "centre": (centre.X(), centre.Y(), centre.Z()),
        "length": length,
    }


def _point(point: tuple[float, ...], quantum: float) -> list[int]:
    return [_rounded(point[0], quantum), _rounded(point[1], quantum), _rounded(point[2], quantum)]


def _tokens(prefix: str, signatures: list[tuple[list, tuple]]) -> list[str]:
    """One token per element, in the elements' order. Equal signatures are
    ranked by their exact values (then by their position, for elements whose
    exact geometry coincides too) and each rank after the first is hashed in."""
    by_signature: dict[str, list[int]] = {}
    keys = [json.dumps(signature, separators=(",", ":")) for signature, _exact in signatures]
    for index, key in enumerate(keys):
        by_signature.setdefault(key, []).append(index)
    tokens = [""] * len(signatures)
    for members in by_signature.values():
        if len(members) == 1:
            tokens[members[0]] = f"{prefix}:{_digest(prefix, signatures[members[0]][0])[:_HEX]}"
            continue
        ranked = sorted(members, key=lambda index: (signatures[index][1], index))
        for rank, index in enumerate(ranked):
            material = signatures[index][0] if rank == 0 else [signatures[index][0], rank]
            tokens[index] = f"{prefix}:{_digest(prefix, material)[:_HEX]}"
    return tokens


def _maps(shape: Any):
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_VERTEX
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape

    maps = []
    for kind in (TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX):
        found = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(shape, kind, found)
        maps.append(found)
    return maps


def _bodies(shape: Any) -> list[str]:
    from cadgen._internal.entity_ordinals import shape_entities

    return sorted(_enum(sub.ShapeType(), "TopAbs_") for sub in shape_entities(shape))


def _extent(face_boxes: list[tuple[float, ...] | None], points: list[tuple[float, ...]]) -> float:
    """The diagonal of the union of the faces' tight boxes and the vertex points."""
    low = [math.inf] * 3
    high = [-math.inf] * 3
    for box in face_boxes:
        if box is None:
            continue
        for axis in range(3):
            low[axis] = min(low[axis], box[axis])
            high[axis] = max(high[axis], box[axis + 3])
    for point in points:
        for axis in range(3):
            low[axis] = min(low[axis], point[axis])
            high[axis] = max(high[axis], point[axis])
    if any(not math.isfinite(v) for v in (*low, *high)):
        return 0.0
    return math.sqrt(sum((high[axis] - low[axis]) ** 2 for axis in range(3)))


def geometry_identity(shape: Any) -> dict[str, Any]:
    """The fingerprint and every element token of one unlocated component
    shape (a ``TopoDS_Shape``), computed together: ``{"fingerprint": str,
    "faces": [token per face ordinal], "edges": [...], "vertices": [...]}``,
    the lists in ``TopExp.MapShapes_s`` order so a caller joins them to the
    topology facts and the mesh by ordinal."""
    from OCP.BRep import BRep_Tool
    from OCP.TopoDS import TopoDS

    faces, edges, vertices = _maps(shape)
    face_facts = [_face_facts(TopoDS.Face_s(faces.FindKey(i))) for i in range(1, faces.Extent() + 1)]
    edge_facts = [_edge_facts(TopoDS.Edge_s(edges.FindKey(i))) for i in range(1, edges.Extent() + 1)]
    points = []
    for i in range(1, vertices.Extent() + 1):
        point = BRep_Tool.Pnt_s(TopoDS.Vertex_s(vertices.FindKey(i)))
        points.append((point.X(), point.Y(), point.Z()))
    quantum = quantum_for_extent(_extent([facts["box"] for facts in face_facts], points))

    face_signatures = [
        ([facts["type"], [_rounded(v, quantum) for v in facts["box"]] if facts["box"] else None, facts["normal"]],
         (*(facts["box"] or ()), facts["area"]))
        for facts in face_facts
    ]
    edge_signatures = [
        ([facts["type"], [_point(facts["ends"][0], quantum), _point(facts["ends"][1], quantum)],
          _rounded(facts["length"], quantum)],
         (*facts["centre"], facts["length"]))
        for facts in edge_facts
    ]
    vertex_signatures = [(_point(point, quantum), point) for point in points]
    material = {
        "quantum": quantum,
        "bodies": _bodies(shape),
        "counts": [faces.Extent(), edges.Extent(), vertices.Extent()],
        "vertices": sorted(signature for signature, _exact in vertex_signatures),
        "faces": sorted(json.dumps(signature, separators=(",", ":")) for signature, _exact in face_signatures),
    }
    return {
        "fingerprint": _digest("fingerprint", material),
        "faces": _tokens("f", face_signatures),
        "edges": _tokens("e", edge_signatures),
        "vertices": _tokens("v", vertex_signatures),
    }


def fingerprint(shape: Any) -> str:
    """The fingerprint of one unlocated component shape: sha256 hex."""
    return geometry_identity(shape)["fingerprint"]


def element_tokens(shape: Any) -> dict[str, list[str]]:
    """The ``f:``, ``e:`` and ``v:`` tokens of one unlocated component shape,
    by ordinal (``geometry_identity`` without the fingerprint)."""
    identity = geometry_identity(shape)
    return {"faces": identity["faces"], "edges": identity["edges"], "vertices": identity["vertices"]}
