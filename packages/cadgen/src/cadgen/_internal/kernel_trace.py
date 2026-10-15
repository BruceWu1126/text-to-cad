"""Features and lineage: what the kernel boundary saw during one model run, and
which feature made, and which last modified, each face and edge of the result
(STORE.md §3, Provenance; README law 20).

A **feature** is one call of a patched kernel construction (``kernel_memo``)
made from the model's own code: the operation, the innermost first-party
frame that called it (the file, relative to the model's folder, the line and
the enclosing function), an ordinal ``n`` counting that line's runs of that
operation, and the features that made the shapes it was given. Its id is
``fe:<op>@<file>:<line>#<n>``. A call with no first-party frame on the stack
is cadgen's own and is not a feature; a call nested inside another feature's
operation belongs to that feature.

Lineage is by geometry, not by the kernel's history objects. After the body,
each component of the result is named by its fingerprint and its faces and
edges by their tokens (``geometry_fingerprint``), and every element is followed
back through the features: at each feature, an element of the result that an
input holds with the same surface (or curve) and the same extent is
**unchanged** there and inherits its lineage from that input; one that an input
holds on the same surface with another extent was **modified** by the feature;
any other element was **made** by it. Surfaces, curves and extents are compared
in the frame the operation saw them (the inputs as they were passed, the result
as it was returned), rounded absolutely, so a placed copy of a body keeps its
faces' lineage. The walk stops at a shape no feature made (a materialized child,
an imported file, a shape built outside the patched surface): its elements are
of unknown origin, and a feature that modified one is still named.

The payload (``Trace.provenance``) is what the build writes beside its record
(``cadgen.store.provenance``): the features in call order, and per component
fingerprint the feature that made the body and, per face and edge token, the
feature that made it and the one that last modified it, as indices into the
feature list.
"""

from __future__ import annotations

import math
import os
import sys
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from cadgen._internal.source_hash import is_first_party_source_file

__all__ = ["Feature", "Trace", "edge_signature", "face_signature"]

# Lineage compares geometry rounded absolutely: positions and lengths to a
# micron, directions to a nanoradian's worth, like an occurrence's placement
# (tree_tokens). A face or edge an operation left alone keeps its surface and
# its extent to far better than this; one it trimmed keeps the surface alone.
POSITION_QUANTUM = 1e-6
DIRECTION_QUANTUM = 1e-9
_SAMPLES = 3
_HERE = Path(__file__).resolve().parent
# A kernel call reached through cadgen's own machinery before any model frame
# (a child's materialization, the store's own constructions) is cadgen's, not
# the model's: no feature. Its public helpers (``cadgen.geometry``,
# ``cadgen.assembly``) are called by the model and count as build123d does, and
# so does the store's hook on ``Compound.__init__`` (``store/_ready_children``),
# which passes the model's own construction through.
_OWN = tuple(_HERE.parent / name for name in ("store", "_internal", "daemon"))
_PASS_THROUGH = (_HERE.parent / "store" / "_ready_children.py",)


@dataclass
class Feature:
    """One kernel construction the model's code called."""

    index: int
    op: str
    file: str
    line: int
    function: str
    n: int
    # The feature of each shape the call was given, in call order; None for a
    # shape no feature of this run made.
    inputs: list[int | None]
    # The input shapes as passed and the results as returned: handle copies,
    # so a later in-place move of the object does not move what the operation
    # saw. The lineage tables are computed from these on demand.
    handles: list[Any] = field(default_factory=list, repr=False)
    results: list[Any] = field(default_factory=list, repr=False)
    _tables: dict[tuple[int, str], dict[int, tuple]] = field(default_factory=dict, repr=False)

    @property
    def id(self) -> str:
        return f"fe:{self.op}@{self.file}:{self.line}#{self.n}"

    def row(self) -> dict[str, Any]:
        """The feature as the provenance entry records it."""
        return {"id": self.id, "op": self.op, "file": self.file, "line": self.line,
                "function": self.function, "n": self.n, "inputs": list(self.inputs)}


class Trace:
    """The features of one run, in call order, and the lineage they give."""

    def __init__(self, model_dir: Path | None) -> None:
        self.model_dir = Path(model_dir).resolve() if model_dir is not None else None
        self.features: list[Feature] = []
        self._runs: dict[tuple[str, str, int], int] = {}
        self._first_party: dict[str, str] = {}
        self._display: dict[str, str] = {}
        # id(shape) -> (weakref, feature index): the feature that returned each
        # live result object. A dead reference, or a recycled id, answers nothing.
        self._by_object: dict[int, tuple[weakref.ref, int]] = {}
        self._signatures: dict[tuple, list] = {}
        self._chains: dict[tuple[int, int, str, int], tuple[int | None, int | None]] = {}

    # --- recording ---------------------------------------------------------------------

    def frame(self) -> tuple[str, int, str] | None:
        """The innermost first-party frame of the current stack: ``(file, line,
        function)``, the file relative to the model's folder; None when the
        call is cadgen's own (one of its internal frames comes first) or no
        model code is on the stack."""
        frame = sys._getframe(1)
        while frame is not None:
            filename = frame.f_code.co_filename
            verdict = self._first_party.get(filename)
            if verdict is None:
                verdict = self._first_party[filename] = _classify(filename)
            if verdict == "model":
                return self._display_path(filename), frame.f_lineno, frame.f_code.co_name
            if verdict == "own":
                return None
            frame = frame.f_back
        return None

    def _display_path(self, filename: str) -> str:
        shown = self._display.get(filename)
        if shown is None:
            path = Path(filename)
            shown = path.as_posix()
            if self.model_dir is not None:
                try:
                    shown = path.resolve().relative_to(self.model_dir).as_posix()
                except (ValueError, OSError):
                    shown = path.as_posix()
            self._display[filename] = shown
        return shown

    def feature_of(self, shape: Any) -> int | None:
        """The feature that returned this very object, or None."""
        found = self._by_object.get(id(shape))
        if found is None:
            return None
        reference, index = found
        return index if reference() is shape else None

    def record(self, op: str, frame: tuple[str, int, str], inputs: list[tuple[Any, int | None]],
               results: list[Any]) -> Feature:
        """One feature: ``inputs`` are ``(shape, its feature or None)`` in call
        order, ``results`` the live result objects the call returned."""
        file, line, function = frame
        n = self._runs[(op, file, line)] = self._runs.get((op, file, line), 0) + 1
        feature = Feature(
            index=len(self.features), op=op, file=file, line=line, function=function, n=n,
            inputs=[index for _shape, index in inputs],
            handles=[_handle(shape) for shape, _index in inputs],
            results=[_handle(shape) for shape in results],
        )
        self.features.append(feature)
        for shape in results:
            try:
                self._by_object[id(shape)] = (weakref.ref(shape), feature.index)
            except TypeError:
                pass
        return feature

    # --- the payload ---------------------------------------------------------------------

    def provenance(self, shape: Any, made_by: Callable[[Any], int | None]) -> dict[str, Any]:
        """The provenance payload for the shape a model returned. ``made_by``
        answers, for a ``TopoDS_TShape``, the feature of this run that made it.

        Components are the result's leaves as the tree builder walks them: a
        node with no children, that is not a placed child model. Each is named
        by its fingerprint and its faces and edges by their tokens, computed on
        the unlocated body exactly as the tree names them."""
        from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
        from OCP.TopLoc import TopLoc_Location

        from cadgen._internal.geometry_fingerprint import geometry_identity

        components: dict[str, Any] = {}
        for leaf in _leaves(shape):
            unlocated = leaf.wrapped.Located(TopLoc_Location())
            identity = geometry_identity(unlocated)
            fingerprint = identity["fingerprint"]
            if fingerprint in components:
                continue
            faces, edges = _map(unlocated, TopAbs_FACE), _map(unlocated, TopAbs_EDGE)
            component: dict[str, Any] = {"made": self._owner(made_by, unlocated, None)}
            component["faces"] = {
                token: self._lineage(made_by, unlocated, faces.FindKey(ordinal), "face")
                for ordinal, token in enumerate(identity["faces"], 1)
            }
            component["edges"] = {
                token: self._lineage(made_by, unlocated, edges.FindKey(ordinal), "edge")
                for ordinal, token in enumerate(identity["edges"], 1)
            }
            components[fingerprint] = component
        return {"features": [feature.row() for feature in self.features], "components": components}

    def _owner(self, made_by: Callable[[Any], int | None], body: Any, element: Any) -> int | None:
        """The feature that made the body holding ``element`` (the body itself
        when it is one feature's result, else the solid or shell of it that is),
        or None."""
        from cadgen._internal.entity_ordinals import shape_entities

        candidates = [body] if element is None else [body, *shape_entities(body)]
        for candidate in candidates:
            index = made_by(candidate.TShape())
            if index is None:
                continue
            if element is None or candidate is body or _map(candidate, element.ShapeType()).Contains(element):
                return index
        return None

    def _lineage(self, made_by: Callable[[Any], int | None], body: Any, element: Any, kind: str) -> list[int | None]:
        """``[made, modified]`` for one element of a component: feature indices or None."""
        owner = self._owner(made_by, body, element)
        if owner is None:
            return [None, None]
        position = self._result_position(owner, element)
        if position is None:
            return [None, None]
        made, modified = self._chain(made_by, owner, position, kind, element)
        return [made, modified]

    def _result_position(self, index: int, element: Any) -> int | None:
        """Which result of feature ``index`` holds ``element``: a position in its results."""
        feature = self.features[index]
        for position, handle in enumerate(feature.results):
            if _map(_local(handle), element.ShapeType()).Contains(element):
                return position
        return None

    def _chain(self, made_by: Callable[[Any], int | None], index: int, position: int, kind: str,
               element: Any) -> tuple[int | None, int | None]:
        """Follow ``element`` (a sub-shape of result ``position`` of feature ``index``,
        in that result's local frame) back through the features. Iterative, memoized
        per node: the chain of a body the model built step by step is as long as
        its steps."""
        path: list[tuple[int, int, str, int]] = []
        current: tuple[int, int, Any] | None = (index, position, element)
        answer: tuple[int | None, int | None] = (None, None)
        while current is not None:
            index, position, element = current
            feature = self.features[index]
            ordinal = _map(_local(feature.results[position]), element.ShapeType()).FindIndex(element)
            node = (index, position, kind, ordinal)
            if node in self._chains:
                answer = self._chains[node]
                break
            path.append(node)
            entry = self._table(feature, position, kind).get(ordinal)
            if entry is None or entry[0] == "made":
                answer = (index, None)
                break
            _tag, j, k, _modified = entry
            input_handle = feature.handles[j]
            up = made_by(input_handle.TShape())
            up_position = None if up is None else _position_of(self.features[up], input_handle)
            if up is None or up_position is None:
                answer = (None, None)
                break
            current = (up, up_position, _map(_local(input_handle), element.ShapeType()).FindKey(k))
        # Unwind: a node whose entry was "modified" is the last modification seen
        # from above, so it overrides what the chain below it answered.
        for node in reversed(path):
            index, position, kind, ordinal = node
            entry = self._table(self.features[index], position, kind).get(ordinal)
            if entry is not None and entry[0] == "from" and entry[3]:
                answer = (answer[0], index)
            self._chains[node] = answer
        return answer

    def _table(self, feature: Feature, position: int, kind: str) -> dict[int, tuple]:
        """Per element ordinal of one result (its local frame): ``("made",)`` or
        ``("from", input index, input element ordinal, modified)``."""
        key = (position, kind)
        table = feature._tables.get(key)
        if table is not None:
            return table
        from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE

        topabs = TopAbs_FACE if kind == "face" else TopAbs_EDGE
        same: dict[tuple, tuple[int, int]] = {}
        surface: dict[tuple, tuple[int, int]] = {}
        for j, handle in enumerate(feature.handles):
            for k, signature in enumerate(self._signatures_of(handle, topabs, kind), 1):
                same.setdefault(signature, (j, k))
                surface.setdefault(signature[0], (j, k))
        table = {}
        for ordinal, signature in enumerate(self._signatures_of(feature.results[position], topabs, kind), 1):
            found = same.get(signature)
            if found is not None:
                table[ordinal] = ("from", found[0], found[1], False)
                continue
            found = surface.get(signature[0])
            table[ordinal] = ("from", found[0], found[1], True) if found is not None else ("made",)
        feature._tables[key] = table
        return table

    def _signatures_of(self, handle: Any, topabs: Any, kind: str) -> list[tuple]:
        """The signatures of a shape's faces or edges in the frame it was seen in,
        by local ordinal; computed once per (body, placement)."""
        key = (handle.TShape(), _placement(handle), kind)
        found = self._signatures.get(key)
        if found is None:
            location = handle.Location()
            elements = _map(_local(handle), topabs)
            signature = face_signature if kind == "face" else edge_signature
            found = [signature(elements.FindKey(i).Moved(location)) for i in range(1, elements.Extent() + 1)]
            self._signatures[key] = found
        return found


# --- helpers ----------------------------------------------------------------------------


def _classify(filename: str) -> str:
    """``"model"`` for first-party source, ``"own"`` for cadgen's internal
    frames, ``"other"`` for everything skipped (build123d, cadgen's public
    helpers, the memo itself, the standard library)."""
    if not filename or filename.startswith("<"):
        return "other"
    try:
        path = Path(filename).resolve()
    except (OSError, RuntimeError):
        return "other"
    if path.parent == _HERE or path in _PASS_THROUGH:
        return "other"
    if any(root in path.parents for root in _OWN):
        return "own"
    return "model" if is_first_party_source_file(path) else "other"


def _handle(shape: Any) -> Any:
    wrapped = shape.wrapped
    return wrapped.Located(wrapped.Location())


def _local(handle: Any) -> Any:
    from OCP.TopLoc import TopLoc_Location

    return handle.Located(TopLoc_Location())


def _placement(handle: Any) -> tuple[int, ...]:
    transform = handle.Location().Transformation()
    return tuple(
        int(round(transform.Value(row, column) / (POSITION_QUANTUM if column == 4 else DIRECTION_QUANTUM)))
        for row in (1, 2, 3) for column in (1, 2, 3, 4)
    )


def _position_of(feature: Feature, handle: Any) -> int | None:
    tshape = handle.TShape()
    for position, result in enumerate(feature.results):
        if result.TShape() == tshape:
            return position
    return None


def _map(shape: Any, kind: Any) -> Any:
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape

    found = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, kind, found)
    return found


def _leaves(shape: Any) -> list[Any]:
    """The result's components in tree order: every node without children that
    is not a placed child model, read off the node state without forcing a
    promise (``store.lazy``) or reading a reference compound's geometry."""
    from cadgen.store.materialize import TREE_TAG

    found: list[Any] = []
    pending = [shape]
    while pending:
        node = pending.pop()
        state = getattr(node, "__dict__", {})
        if TREE_TAG in state or any(key.startswith("_lazy_") for key in state):
            continue
        children = [child for child in (state.get("_NodeMixin__children") or ()) if hasattr(child, "__dict__")]
        if children:
            pending.extend(reversed(children))
        elif state.get("_wrapped") is not None:
            found.append(node)
    return found


def _q(value: float, quantum: float = POSITION_QUANTUM) -> int:
    scaled = value / quantum
    if not math.isfinite(scaled):
        raise ValueError("geometry coordinate is not finite")
    return int(round(scaled))


def _point(point: Any) -> tuple[int, int, int]:
    return _q(point.X()), _q(point.Y()), _q(point.Z())


def _direction(direction: Any) -> tuple[int, int, int]:
    """A direction rounded and sign-normalized: the line it spans, not its sense."""
    values = (_q(direction.X(), DIRECTION_QUANTUM), _q(direction.Y(), DIRECTION_QUANTUM), _q(direction.Z(), DIRECTION_QUANTUM))
    for value in values:
        if value:
            return values if value > 0 else tuple(-v for v in values)
    return values


def _axis_point(axis: Any) -> tuple[int, int, int]:
    """The point of an axis closest to the origin: names the line whatever point
    the kernel parametrizes it from."""
    location, direction = axis.Location(), axis.Direction()
    along = location.X() * direction.X() + location.Y() * direction.Y() + location.Z() * direction.Z()
    return (_q(location.X() - along * direction.X()), _q(location.Y() - along * direction.Y()),
            _q(location.Z() - along * direction.Z()))


def _samples(evaluate: Callable[..., Any], bounds: tuple[float, ...], count: int = _SAMPLES) -> tuple:
    """Points of a curve (two bounds) or surface (four) at a fixed grid over its bounds."""
    def steps(first: float, last: float) -> list[float]:
        return [first + (last - first) * i / (count - 1) for i in range(count)]

    if len(bounds) == 2:
        return tuple(_point(evaluate(u)) for u in steps(bounds[0], bounds[1]))
    return tuple(_point(evaluate(u, v)) for u in steps(bounds[0], bounds[1]) for v in steps(bounds[2], bounds[3]))


def _finite(first: float, last: float, fallback: tuple[float, float]) -> tuple[float, float]:
    return (first if math.isfinite(first) else fallback[0], last if math.isfinite(last) else fallback[1])


def face_signature(face: Any) -> tuple[tuple, tuple]:
    """``(surface, extent)`` of a face in world coordinates: the surface it lies
    on (its class and parameters, or sample points of a freeform surface) and
    where it lies on it (its vertex points, else its middle point, and its edge
    count), rounded absolutely."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepTools import BRepTools
    from OCP.GeomAbs import GeomAbs_SurfaceType
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_VERTEX
    from OCP.TopoDS import TopoDS

    topods_face = TopoDS.Face_s(face)
    adaptor = BRepAdaptor_Surface(topods_face, False)
    kind = adaptor.GetType()
    if kind == GeomAbs_SurfaceType.GeomAbs_Plane:
        plane = adaptor.Plane()
        normal, origin = plane.Axis().Direction(), plane.Location()
        offset = normal.X() * origin.X() + normal.Y() * origin.Y() + normal.Z() * origin.Z()
        direction = _direction(normal)
        raw = (_q(normal.X(), DIRECTION_QUANTUM), _q(normal.Y(), DIRECTION_QUANTUM), _q(normal.Z(), DIRECTION_QUANTUM))
        surface: tuple = ("plane", direction, _q(offset if raw == direction else -offset))
    elif kind == GeomAbs_SurfaceType.GeomAbs_Cylinder:
        cylinder = adaptor.Cylinder()
        surface = ("cylinder", _direction(cylinder.Axis().Direction()), _axis_point(cylinder.Axis()), _q(cylinder.Radius()))
    elif kind == GeomAbs_SurfaceType.GeomAbs_Cone:
        cone = adaptor.Cone()
        surface = ("cone", _point(cone.Apex()), _direction(cone.Axis().Direction()), _q(abs(cone.SemiAngle()), DIRECTION_QUANTUM))
    elif kind == GeomAbs_SurfaceType.GeomAbs_Sphere:
        sphere = adaptor.Sphere()
        surface = ("sphere", _point(sphere.Location()), _q(sphere.Radius()))
    elif kind == GeomAbs_SurfaceType.GeomAbs_Torus:
        torus = adaptor.Torus()
        surface = ("torus", _point(torus.Location()), _direction(torus.Axis().Direction()),
                   _q(torus.MajorRadius()), _q(torus.MinorRadius()))
    else:
        geom = BRep_Tool.Surface_s(topods_face)
        u0, u1, v0, v1 = geom.Bounds()
        umin, umax, vmin, vmax = BRepTools.UVBounds_s(topods_face)
        bounds = (*_finite(u0, u1, (umin, umax)), *_finite(v0, v1, (vmin, vmax)))
        surface = (str(kind).rsplit("_", 1)[-1].lower(), _samples(geom.Value, bounds))
    vertices = _map(face, TopAbs_VERTEX)
    points = sorted(_point(BRep_Tool.Pnt_s(TopoDS.Vertex_s(vertices.FindKey(i)))) for i in range(1, vertices.Extent() + 1))
    if not points:
        umin, umax, vmin, vmax = BRepTools.UVBounds_s(topods_face)
        points = [_point(adaptor.Value((umin + umax) / 2.0, (vmin + vmax) / 2.0))]
    extent = (tuple(points), _map(face, TopAbs_EDGE).Extent())
    return surface, extent


def edge_signature(edge: Any) -> tuple[tuple, tuple]:
    """``(curve, extent)`` of an edge in world coordinates: the curve it lies on
    (its class and parameters, or sample points of a freeform curve) and its
    end points, rounded absolutely."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_CurveType
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS

    topods_edge = TopoDS.Edge_s(edge)
    first_point = _point(BRep_Tool.Pnt_s(TopExp.FirstVertex_s(topods_edge, True)))
    last_point = _point(BRep_Tool.Pnt_s(TopExp.LastVertex_s(topods_edge, True)))
    extent = tuple(sorted((first_point, last_point)))
    if BRep_Tool.Degenerated_s(topods_edge):
        return ("degenerate", first_point), extent
    adaptor = BRepAdaptor_Curve(topods_edge)
    kind = adaptor.GetType()
    if kind == GeomAbs_CurveType.GeomAbs_Line:
        line = adaptor.Line()
        curve: tuple = ("line", _direction(line.Direction()), _axis_point(line.Position()))
    elif kind == GeomAbs_CurveType.GeomAbs_Circle:
        circle = adaptor.Circle()
        curve = ("circle", _point(circle.Location()), _direction(circle.Axis().Direction()), _q(circle.Radius()))
    elif kind == GeomAbs_CurveType.GeomAbs_Ellipse:
        ellipse = adaptor.Ellipse()
        curve = ("ellipse", _point(ellipse.Location()), _direction(ellipse.Axis().Direction()),
                 _q(ellipse.MajorRadius()), _q(ellipse.MinorRadius()))
    else:
        geom = BRep_Tool.Curve_s(topods_edge, 0.0, 0.0)
        bounds = _finite(geom.FirstParameter(), geom.LastParameter(),
                         (adaptor.FirstParameter(), adaptor.LastParameter()))
        curve = (str(kind).rsplit("_", 1)[-1].lower(), _samples(geom.Value, bounds, 5))
    return curve, extent
