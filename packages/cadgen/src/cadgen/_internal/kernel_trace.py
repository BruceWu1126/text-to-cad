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

Lineage is by identity and geometry, not by the kernel's history objects.
After the body, each component of the result is named by its fingerprint and
its faces and edges by their tokens (``geometry_fingerprint``), and every
element is followed back through the features: at each feature, an element of
the result whose TShape an input holds (the kernel keeps what an operation
leaves alone, and a placement keeps everything) or that an input holds with the
same surface (or curve) and the same extent is **unchanged** there and inherits
its lineage from that input; one that an input holds, and the result no longer
does, on the same surface with another extent was **modified** by the feature;
any other element was **made** by it. Surfaces, curves and extents are compared
in the frame the operation saw them (the inputs as they were passed, the result
as it was returned), rounded absolutely, and only for the elements an operation
touched. The walk stops at a shape no feature made (a materialized child, an
imported file, a shape built outside the patched surface): its elements are of
unknown origin, and a feature that modified one is still named.

The payload (``Trace.provenance``) is what the build writes beside its record
(``cadgen.store.provenance``): the features in call order, and per component
fingerprint the feature that made the body and, per face and edge token, the
feature that made it and the one that last modified it, as indices into the
feature list. It is computed once the tree has prepared its components, from
the live shape each was prepared from and the identity that preparation
measured, so no face or edge is measured twice.
"""

from __future__ import annotations

import math
import sys
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepTools import BRepTools
from OCP.GeomAbs import GeomAbs_CurveType, GeomAbs_SurfaceType
from OCP.Precision import Precision
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_VERTEX
from OCP.TopExp import TopExp
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS
from OCP.TopTools import TopTools_IndexedMapOfShape

from cadgen._internal.source_hash import is_first_party_source_file

__all__ = ["Feature", "Trace", "edge_signature", "face_signature"]

# Lineage compares geometry rounded absolutely: positions and lengths to a
# micron, directions to a nanoradian's worth, like an occurrence's placement
# (tree_tokens). A face or edge an operation left alone keeps its surface and
# its extent to far better than this; one it trimmed keeps the surface alone.
POSITION_QUANTUM = 1e-6
DIRECTION_QUANTUM = 1e-9
_SAMPLES = 3
_WINDOW = (-1.0, 1.0)
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
    # saw; None for an empty shape or a child model's promise not yet forced.
    # The lineage tables are computed from these on demand.
    handles: list[Any] = field(default_factory=list, repr=False)
    results: list[Any] = field(default_factory=list, repr=False)
    _tables: dict[tuple[int, str], dict[int, tuple]] = field(default_factory=dict, repr=False)
    # Per element kind, each result element's TShape -> the position of the
    # result holding it (the first, when a copy holds it too).
    _positions: dict[Any, dict[Any, int]] = field(default_factory=dict, repr=False)

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
        self._signatures_by_body: dict[tuple, dict[int, tuple]] = {}
        self._chains: dict[tuple[int, int, str, int], tuple[int | None, int | None]] = {}
        # (TShape, element kind) -> the map of a body's elements of that kind in
        # its own frame, each element's TShape -> its ordinal there, and the
        # TShapes by ordinal. A body is mapped once, however many features hold
        # it and elements ask.
        self._maps: dict[tuple[Any, Any], tuple[Any, dict[Any, int], list[Any]]] = {}

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

    def provenance(self, components: Mapping[str, tuple[Any, Mapping[str, Any] | None]],
                   made_by: Callable[[Any], int | None]) -> dict[str, Any]:
        """The provenance payload for a result tree: ``components`` are, by cid,
        the live shape each own component was prepared from and the
        ``geometry_identity`` that preparation measured on it (None for one
        prepared as eager-only bytes). ``made_by`` answers, for a
        ``TopoDS_TShape``, the feature of this run that made it.

        A component is named by its fingerprint and its faces and edges by
        their tokens, in the identity's ordinals, which are the unlocated
        body's. One without an identity names nothing a document can be asked
        about and is left out; of two components with one geometry, the first
        in tree order speaks for both."""
        payload: dict[str, Any] = {}
        for leaf, identity in components.values():
            if identity is None or identity["fingerprint"] in payload:
                continue
            unlocated = leaf.wrapped.Located(TopLoc_Location())
            faces, edges = self._elements(unlocated, TopAbs_FACE), self._elements(unlocated, TopAbs_EDGE)
            owners = self._owners(made_by, unlocated)
            component: dict[str, Any] = {"made": self._maker(made_by, unlocated)}
            component["faces"] = {
                token: self._lineage(made_by, owners, faces.FindKey(ordinal), "face")
                for ordinal, token in enumerate(identity["faces"], 1)
            }
            component["edges"] = {
                token: self._lineage(made_by, owners, edges.FindKey(ordinal), "edge")
                for ordinal, token in enumerate(identity["edges"], 1)
            }
            payload[identity["fingerprint"]] = component
        return {"features": [feature.row() for feature in self.features], "components": payload}

    def _maker(self, made_by: Callable[[Any], int | None], body: Any) -> int | None:
        """The feature that made ``body``, or None: the one its TShape is
        registered to, unless that feature was handed the TShape it returned
        (a placement of a shape no feature made)."""
        index = made_by(body.TShape())
        if index is not None and self._passed_through(index, body.TShape()):
            return None
        return index

    def _passed_through(self, index: int, tshape: Any) -> bool:
        return any(handle is not None and handle.TShape() == tshape for handle in self.features[index].handles)

    def _owners(self, made_by: Callable[[Any], int | None], body: Any) -> list[tuple[Any, int]]:
        """The shapes of a component some feature made, with that feature: the
        body itself when it is one feature's result, else each solid or shell
        of it that is."""
        index = made_by(body.TShape())
        if index is not None:
            return [(body, index)]
        from cadgen._internal.entity_ordinals import shape_entities

        found = []
        for entity in shape_entities(body):
            index = made_by(entity.TShape())
            if index is not None:
                found.append((entity, index))
        return found

    def _lineage(self, made_by: Callable[[Any], int | None], owners: list[tuple[Any, int]], element: Any,
                 kind: str) -> list[int | None]:
        """``[made, modified]`` for one element of a component: feature indices or None."""
        owner = next((index for shape, index in owners
                      if element.TShape() in self._ordinals(shape, element.ShapeType())), None)
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
        kind = element.ShapeType()
        positions = feature._positions.get(kind)
        if positions is None:
            positions = {}
            for position in range(len(feature.results) - 1, -1, -1):
                handle = feature.results[position]
                if handle is not None:
                    positions.update(dict.fromkeys(self._ordinals(handle, kind), position))
            feature._positions[kind] = positions
        return positions.get(element.TShape())

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
            ordinal = self._ordinals(feature.results[position], element.ShapeType()).get(element.TShape(), 0)
            node = (index, position, kind, ordinal)
            if node in self._chains:
                answer = self._chains[node]
                break
            if node in path:
                break  # the feature returned the TShape it was handed: no feature made this
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
            current = (up, up_position, self._elements(input_handle, element.ShapeType()).FindKey(k))
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
        topabs = TopAbs_FACE if kind == "face" else TopAbs_EDGE
        result = feature.results[position]
        _elements, kept, tshapes = self._mapped(result, topabs)
        table: dict[int, tuple] = {}
        # First by identity: the kernel keeps the faces and edges an operation
        # leaves alone (and a placement keeps them all), so an element whose
        # TShape an input holds passed through unchanged, whatever its frame.
        inputs = [(j, self._ordinals(handle, topabs)) for j, handle in enumerate(feature.handles) if handle is not None]
        for ordinal, tshape in enumerate(tshapes, 1):
            for j, ordinals in inputs:
                k = ordinals.get(tshape)
                if k is not None:
                    table[ordinal] = ("from", j, k, False)
                    break
        if len(table) < len(tshapes):
            # Then by geometry, among what the operation consumed: an input
            # element the result no longer holds is where a new one came from.
            same: dict[tuple, tuple[int, int]] = {}
            surface: dict[tuple, tuple[int, int]] = {}
            for j, handle in enumerate(feature.handles):
                if handle is None:
                    continue
                signatures = self._signatures(handle, kind)
                for k, tshape in enumerate(self._mapped(handle, topabs)[2], 1):
                    if tshape in kept:
                        continue
                    signature = self._signature(signatures, handle, topabs, kind, k)
                    same.setdefault(signature, (j, k))
                    surface.setdefault(signature[0], (j, k))
            signatures = self._signatures(result, kind)
            for ordinal in range(1, len(tshapes) + 1):
                if ordinal in table:
                    continue
                signature = self._signature(signatures, result, topabs, kind, ordinal)
                found = same.get(signature)
                if found is not None:
                    table[ordinal] = ("from", found[0], found[1], False)
                    continue
                found = surface.get(signature[0])
                table[ordinal] = ("from", found[0], found[1], True) if found is not None else ("made",)
        feature._tables[key] = table
        return table

    def _signatures(self, handle: Any, kind: str) -> dict[int, tuple]:
        """The signatures of a shape's faces or edges in the frame it was seen
        in, by ordinal, filled as they are asked for: one table per (body,
        placement)."""
        key = (handle.TShape(), _placement(handle), kind)
        found = self._signatures_by_body.get(key)
        if found is None:
            found = self._signatures_by_body[key] = {}
        return found

    def _signature(self, signatures: dict[int, tuple], handle: Any, topabs: Any, kind: str, ordinal: int) -> tuple:
        signature = signatures.get(ordinal)
        if signature is None:
            element = self._elements(handle, topabs).FindKey(ordinal).Moved(handle.Location())
            signature = signatures[ordinal] = (face_signature if kind == "face" else edge_signature)(element)
        return signature

    def _elements(self, shape: Any, kind: Any) -> Any:
        """``TopExp.MapShapes`` of ``shape``'s elements of ``kind``, in its own frame."""
        return self._mapped(shape, kind)[0]

    def _ordinals(self, shape: Any, kind: Any) -> dict[Any, int]:
        """Each element's TShape -> its ordinal in :meth:`_elements`."""
        return self._mapped(shape, kind)[1]

    def _mapped(self, shape: Any, kind: Any) -> tuple[Any, dict[Any, int], list[Any]]:
        key = (shape.TShape(), kind)
        found = self._maps.get(key)
        if found is None:
            elements = _map(_local(shape), kind)
            tshapes = [elements.FindKey(i).TShape() for i in range(1, elements.Extent() + 1)]
            ordinals: dict[Any, int] = {}
            for ordinal, tshape in enumerate(tshapes, 1):
                # The first ordinal of a TShape placed twice in one body speaks for both.
                ordinals.setdefault(tshape, ordinal)
            found = self._maps[key] = (elements, ordinals, tshapes)
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
    """A handle copy of the shape's ``TopoDS`` from its own state, or None: an
    empty shape, or a child model's promise or a reference assembly, whose
    property would force what the body only placed."""
    state = getattr(shape, "__dict__", None)
    wrapped = None if state is None else state.get("_wrapped")
    return None if wrapped is None else wrapped.Located(wrapped.Location())


def _local(handle: Any) -> Any:
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
        if result is not None and result.TShape() == tshape:
            return position
    return None


def _map(shape: Any, kind: Any) -> Any:
    found = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, kind, found)
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


def _bounded(first: float, last: float) -> tuple[float, float]:
    """A parameter's natural bounds, or a fixed window about zero where the
    kernel leaves it unbounded: its infinity is the finite float 2e100, where a
    hyperbola overflows and every offset surface samples alike. The window
    names the curve or surface the same whatever trim an edge or face takes
    from it."""
    if any(not math.isfinite(bound) or Precision.IsInfinite_s(bound) for bound in (first, last)):
        return _WINDOW
    return first, last


def face_signature(face: Any) -> tuple[tuple, tuple]:
    """``(surface, extent)`` of a face in world coordinates: the surface it lies
    on (its class and parameters, or sample points of a freeform surface) and
    where it lies on it (its vertex points, else its middle point, and its edge
    count), rounded absolutely."""
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
        bounds = (*_bounded(u0, u1), *_bounded(v0, v1))
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
        bounds = _bounded(geom.FirstParameter(), geom.LastParameter())
        curve = (str(kind).rsplit("_", 1)[-1].lower(), _samples(geom.Value, bounds, 5))
    return curve, extent
