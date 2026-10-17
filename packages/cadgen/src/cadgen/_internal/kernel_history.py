"""What the kernel's makers report while a feature runs: the history its elements are
named by (README law 20; STORE.md §2).

A feature (``kernel_trace``) is one build123d call, and inside it build123d runs OCCT
makers -- a boolean and the clean that unifies what it left, a prism, a fillet, a wire, a
face and the fixes it gets -- each of which knows, of the shapes it was given, which of
its results it modified or generated from each of their faces, edges and vertices. This
module patches those makers once per process (``install``, called by
``kernel_memo.install``): a maker's constructor and the methods that hand it shapes
(``Add``, ``SetArguments``, ``Init`` ...) note the shapes as its inputs, and the methods
that hand back what it made (``Shape``, ``Face``, ``Result`` ...) note its result, in a
list that exists only while a feature call is open (``recording``). A maker run by a
build123d call nested inside the feature's belongs to the feature.

Right after the call, ``compose`` follows every element of the feature's inputs through
those makers, in the order their results were taken: an element a maker still holds is
itself, one it reports as modified or generated becomes what it reports, and one copied
stays itself (a copy is not a change); so the chain a boolean is -- the boolean, then
the clean -- or a face from a wire -- the fixes, the face, the fix of the face -- reads as
one step from the feature's inputs to its results, and a maker whose result never
reaches them changes nothing. Each maker is asked only about sub-shapes of its own inputs:
OCCT answers a question about a shape it never saw with an exception, which costs
milliseconds, and a list it hands back is copied and emptied from its head, never
iterated (the same cost). The answer is the feature's ``History``: for each face, edge
and vertex of each result, by its ordinal there, the elements of the inputs it came from
-- input, kind, ordinal -- and how. Ordinals, so the kernel memo keeps a History with its
entry and a hit, which hands back the very shapes, reads it as the miss computed it
without running a maker.

How a maker reports (``_ADAPTERS``): a ``BRepBuilderAPI_MakeShape`` by ``Modified`` and
``Generated``; a prism or a revolution also by ``FirstShape``/``LastShape`` of each
sub-shape of its profile (an extrusion's top face is generated from the profile face; a
full revolution's seam is the profile edge itself); the clean
(``ShapeUpgrade_UnifySameDomain``), a ``BRepTools_ReShape`` and the fixes (``ShapeFix_*``,
through their context) by their ``History``; a sewing by ``Modified``/
``ModifiedSubShape``; a copy (``BRepBuilderAPI_Copy``) by ``Modified``, as the same
elements; a transformation (``Transform``, ``GTransform``) by ``Modified``, as modified,
so a mirrored copy is not its original; ``LocOpe_DPrism`` by ``Shapes``. A wire
(``BRepBuilderAPI_MakeWire``) reports nothing and copies every edge whose vertices it
merges: an edge of its result on the same curve object as an edge it was given, over
the same parameters, is that edge copied -- the identity of the kernel's curve, never a
measurement. A maker with no input (a primitive, an edge from a curve, a polygon from
points, a text) has nothing to report and is not patched.
"""

from __future__ import annotations

import contextlib
import functools
import itertools
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from OCP.TopAbs import TopAbs_COMPOUND, TopAbs_EDGE, TopAbs_FACE, TopAbs_SHELL, TopAbs_SOLID, TopAbs_VERTEX
from OCP.TopExp import TopExp
from OCP.TopoDS import TopoDS_Shape
from OCP.TopTools import TopTools_IndexedMapOfShape, TopTools_ListOfShape

__all__ = ["History", "KINDS", "compose", "install", "recording", "stats", "uninstall"]

# The element kinds named, in the order a source is preferred by (FreeCAD's NameKey: a
# vertex before an edge before a face), with the kernel's enum for each.
KINDS = (TopAbs_VERTEX, TopAbs_EDGE, TopAbs_FACE)
_KIND = {TopAbs_VERTEX: 0, TopAbs_EDGE: 1, TopAbs_FACE: 2}
# How an element came from a source: the same element copied, modified, generated.
SAME, MODIFIED, GENERATED = "I", "M", "G"
_STRENGTH = {SAME: 0, MODIFIED: 1, GENERATED: 2}

_LOCAL = threading.local()
_ORDER = itertools.count(1)
_STATS = {"queries": 0, "raised": 0, "errors": 0}
_installed = False


def stats() -> dict[str, int]:
    """History queries asked, how many raised (the never-ask-what-it-never-saw rule makes
    that zero) and compositions that failed (the feature is then named without history)."""
    return dict(_STATS)


@dataclass
class History:
    """A feature's history: ``origins[(result position, kind, ordinal)]`` is a tuple of
    ``(input index, source kind, source ordinal, how, place)`` -- kinds by their index in
    :data:`KINDS`, ordinals in ``TopExp.MapShapes`` of the shape as given or returned,
    ``how`` one of ``I`` (copied), ``M`` (modified), ``G`` (generated), and ``place`` the
    element's position among what the maker reported of that source (a tuple, one
    position per maker of the chain that made it out of another: FreeCAD's index, which
    tells apart elements made alike from the same sources). An element no maker reported
    has no entry. ``structures`` is the trace's naming of the feature, computed from it
    once and kept with it (``kernel_trace``)."""

    origins: dict[tuple[int, int, int], tuple[tuple[int, int, int, str, tuple[int, ...]], ...]] = field(
        default_factory=dict)
    structures: dict[Any, Any] = field(default_factory=dict, repr=False)


# --- recording ------------------------------------------------------------------------------


class _Record:
    """One maker the feature ran: the shapes it was given and what it handed back."""

    __slots__ = ("maker", "adapter", "inputs", "result", "order")

    def __init__(self, maker: Any, adapter: "_Adapter") -> None:
        self.maker = maker
        self.adapter = adapter
        self.inputs: list[Any] = []
        self.result: Any = None
        self.order = 0


@contextlib.contextmanager
def recording() -> Iterator[list[_Record]]:
    """The makers run on this thread until the block ends, in a list (``compose``'s third
    argument). The list holds them alive; it is the feature call's, and gone with it."""
    previous = getattr(_LOCAL, "records", None), getattr(_LOCAL, "index", None)
    records: list[_Record] = []
    _LOCAL.records, _LOCAL.index = records, {}
    try:
        yield records
    finally:
        _LOCAL.records, _LOCAL.index = previous


def _record(maker: Any, adapter: "_Adapter") -> _Record | None:
    records = getattr(_LOCAL, "records", None)
    if records is None:
        return None
    index = _LOCAL.index
    record = index.get(id(maker))
    if record is None or record.maker is not maker:
        record = index[id(maker)] = _Record(maker, adapter)
        records.append(record)
    return record


def _note_inputs(record: _Record, values: Any) -> None:
    """The shapes among a call's arguments, as the maker was handed them: a list of shapes
    copied and emptied from its head, never iterated."""
    for value in values:
        if isinstance(value, TopoDS_Shape):
            if not value.IsNull():
                record.inputs.append(value.Located(value.Location()))
        elif isinstance(value, TopTools_ListOfShape):
            record.inputs.extend(_listed(value))
        elif hasattr(value, "Length") and hasattr(value, "Value") and type(value).__name__.startswith("TopTools_"):
            for index in range(1, value.Length() + 1):
                shape = value.Value(index)
                if isinstance(shape, TopoDS_Shape) and not shape.IsNull():
                    record.inputs.append(shape.Located(shape.Location()))


def _listed(shapes: Any) -> list[Any]:
    """A ``TopTools_ListOfShape``'s members: copied, then taken from the copy's head."""
    copy = TopTools_ListOfShape()
    copy.Assign(shapes)
    found = []
    while copy.Size():
        found.append(copy.First())
        copy.RemoveFirst()
    return found


# --- adapters: how each maker reports -------------------------------------------------------


@dataclass(frozen=True)
class _Adapter:
    """``prepare(maker)`` returns ``query(sub-shape) -> [(shape, how)]``, or None for a
    maker with nothing to report; ``generator`` makers make new elements from elements
    they keep (a prism's sides from the profile it keeps as its bottom), so every
    sub-shape of their inputs is asked about, where any other maker is asked only about
    those its result no longer holds."""

    prepare: Callable[[Any], Callable[[Any], list[tuple[Any, str]]] | None]
    generator: bool = False


def _made(maker: Any) -> Callable[[Any], list[tuple[Any, str]]]:
    def query(sub: Any) -> list[tuple[Any, str]]:
        return ([(shape, MODIFIED) for shape in _listed(maker.Modified(sub))]
                + [(shape, GENERATED) for shape in _listed(maker.Generated(sub))])
    return query


def _swept(maker: Any) -> Callable[[Any], list[tuple[Any, str]]]:
    """A sweep's ``FirstShape(s)`` is ``s`` where the sweep began -- itself, or the copy a
    sweep asked to copy its profile makes (build123d's revolution does) -- and its
    ``LastShape(s)`` is generated from ``s``, unless it is the first again (a full turn)."""
    made = _made(maker)

    def query(sub: Any) -> list[tuple[Any, str]]:
        found = made(sub)
        first, last = maker.FirstShape(sub), maker.LastShape(sub)
        if not first.IsNull() and not first.IsSame(sub):
            found.append((first, SAME))
        if not last.IsNull() and not last.IsSame(sub) and (first.IsNull() or not last.IsSame(first)):
            found.append((last, GENERATED))
        return found
    return query


def _from_history(history: Any) -> Callable[[Any], list[tuple[Any, str]]] | None:
    if history is None:
        return None

    def query(sub: Any) -> list[tuple[Any, str]]:
        return ([(shape, MODIFIED) for shape in _listed(history.Modified(sub))]
                + [(shape, GENERATED) for shape in _listed(history.Generated(sub))])
    return query


def _recorded(maker: Any) -> Callable[[Any], list[tuple[Any, str]]] | None:
    return _from_history(maker.History())


def _fixed(maker: Any) -> Callable[[Any], list[tuple[Any, str]]] | None:
    context = maker.Context()
    return None if context is None else _from_history(context.History())


def _sewn(maker: Any) -> Callable[[Any], list[tuple[Any, str]]]:
    def query(sub: Any) -> list[tuple[Any, str]]:
        if maker.IsModified(sub):
            shape = maker.Modified(sub)
        elif maker.IsModifiedSubShape(sub):
            shape = maker.ModifiedSubShape(sub)
        else:
            return []
        return [] if shape.IsNull() or shape.IsSame(sub) else [(shape, MODIFIED)]
    return query


def _copied(maker: Any) -> Callable[[Any], list[tuple[Any, str]]]:
    def query(sub: Any) -> list[tuple[Any, str]]:
        return [(shape, SAME) for shape in _listed(maker.Modified(sub))]
    return query


def _transformed(maker: Any) -> Callable[[Any], list[tuple[Any, str]]]:
    def query(sub: Any) -> list[tuple[Any, str]]:
        return [(shape, MODIFIED) for shape in _listed(maker.Modified(sub))]
    return query


def _tapered(maker: Any) -> Callable[[Any], list[tuple[Any, str]]]:
    def query(sub: Any) -> list[tuple[Any, str]]:
        return [(shape, GENERATED) for shape in _listed(maker.Shapes(sub))]
    return query


_MAKER = _Adapter(_made)
_GENERATOR = _Adapter(_made, generator=True)
_WIRE = _Adapter(lambda maker: None)  # composed by its own rule (_wire_copies)

# (module, class, adapter): the makers build123d 0.11.1's topology modules construct that
# are given shapes. A class OCP no longer has is an error: the table is this version's.
_ADAPTERS = (
    ("OCP.BRepAlgoAPI", "BRepAlgoAPI_Cut", _MAKER),
    ("OCP.BRepAlgoAPI", "BRepAlgoAPI_Fuse", _MAKER),
    ("OCP.BRepAlgoAPI", "BRepAlgoAPI_Common", _MAKER),
    ("OCP.BRepAlgoAPI", "BRepAlgoAPI_Section", _MAKER),
    ("OCP.BRepAlgoAPI", "BRepAlgoAPI_Splitter", _MAKER),
    ("OCP.BOPAlgo", "BOPAlgo_Splitter", _MAKER),
    ("OCP.ShapeUpgrade", "ShapeUpgrade_UnifySameDomain", _Adapter(_recorded)),
    ("OCP.BRepTools", "BRepTools_ReShape", _Adapter(_recorded)),
    ("OCP.ShapeFix", "ShapeFix_Shape", _Adapter(_fixed)),
    ("OCP.ShapeFix", "ShapeFix_Face", _Adapter(_fixed)),
    ("OCP.ShapeFix", "ShapeFix_Wire", _Adapter(_fixed)),
    ("OCP.ShapeFix", "ShapeFix_Solid", _Adapter(_fixed)),
    ("OCP.ShapeFix", "ShapeFix_Wireframe", _Adapter(_fixed)),
    ("OCP.BRepBuilderAPI", "BRepBuilderAPI_Copy", _Adapter(_copied)),
    ("OCP.BRepBuilderAPI", "BRepBuilderAPI_Transform", _Adapter(_transformed)),
    ("OCP.BRepBuilderAPI", "BRepBuilderAPI_GTransform", _Adapter(_transformed)),
    ("OCP.BRepBuilderAPI", "BRepBuilderAPI_Sewing", _Adapter(_sewn)),
    ("OCP.BRepBuilderAPI", "BRepBuilderAPI_MakeWire", _WIRE),
    ("OCP.BRepBuilderAPI", "BRepBuilderAPI_MakeFace", _MAKER),
    ("OCP.BRepBuilderAPI", "BRepBuilderAPI_MakeSolid", _MAKER),
    ("OCP.BRepPrimAPI", "BRepPrimAPI_MakePrism", _Adapter(_swept, generator=True)),
    ("OCP.BRepPrimAPI", "BRepPrimAPI_MakeRevol", _Adapter(_swept, generator=True)),
    ("OCP.BRepPrimAPI", "BRepPrimAPI_MakeHalfSpace", _GENERATOR),
    ("OCP.BRepFilletAPI", "BRepFilletAPI_MakeFillet", _MAKER),
    ("OCP.BRepFilletAPI", "BRepFilletAPI_MakeChamfer", _MAKER),
    ("OCP.BRepOffsetAPI", "BRepOffsetAPI_ThruSections", _GENERATOR),
    ("OCP.BRepOffsetAPI", "BRepOffsetAPI_MakePipe", _GENERATOR),
    ("OCP.BRepOffsetAPI", "BRepOffsetAPI_MakePipeShell", _GENERATOR),
    ("OCP.BRepOffsetAPI", "BRepOffsetAPI_MakeOffset", _GENERATOR),
    ("OCP.BRepOffsetAPI", "BRepOffsetAPI_MakeThickSolid", _GENERATOR),
    ("OCP.BRepOffsetAPI", "BRepOffsetAPI_MakeFilling", _GENERATOR),
    ("OCP.BRepOffsetAPI", "BRepOffsetAPI_DraftAngle", _MAKER),
    ("OCP.BRepOffset", "BRepOffset_MakeOffset", _GENERATOR),
    ("OCP.BRepFeat", "BRepFeat_MakeDPrism", _GENERATOR),
    ("OCP.BRepFeat", "BRepFeat_SplitShape", _MAKER),
    ("OCP.LocOpe", "LocOpe_DPrism", _Adapter(_tapered, generator=True)),
)
# The methods that hand a maker shapes after it is constructed, and those that hand back
# what it made; each patched where the class has it.
_SETTERS = ("Add", "AddWire", "AddVertex", "SetArguments", "SetTools", "Init", "Initialize", "Load")
_FINISHERS = ("Shape", "Face", "Edge", "Wire", "Vertex", "Solid", "Shell", "Result", "SewedShape")


def install() -> bool:
    """Patch the makers of :data:`_ADAPTERS`, once per process. Returns installed-now."""
    global _installed
    if _installed:
        return False
    import importlib

    for module_name, class_name, adapter in _ADAPTERS:
        cls = getattr(importlib.import_module(module_name), class_name)
        _patch(cls, "__init__", _initializer(cls.__init__, adapter))
        for name in _SETTERS:
            original = cls.__dict__.get(name) or getattr(cls, name, None)
            if original is not None:
                _patch(cls, name, _setter(original, adapter))
        for name in _FINISHERS:
            original = cls.__dict__.get(name) or getattr(cls, name, None)
            if original is not None:
                _patch(cls, name, _finisher(original))
    _installed = True
    return True


def uninstall() -> bool:
    """Restore every method ``install`` replaced. Returns uninstalled-now."""
    global _installed
    if not _installed:
        return False
    import importlib

    for module_name, class_name, _adapter in _ADAPTERS:
        cls = getattr(importlib.import_module(module_name), class_name)
        for name in ("__init__", *_SETTERS, *_FINISHERS):
            patched = cls.__dict__.get(name)
            if patched is not None and getattr(patched, "__kernel_history__", False):
                setattr(cls, name, patched.__wrapped__)
    _installed = False
    return True


def _patch(cls: type, name: str, wrapper: Any) -> None:
    current = cls.__dict__.get(name)
    if current is not None and getattr(current, "__kernel_history__", False):
        return
    setattr(cls, name, wrapper)


def _initializer(original: Any, adapter: _Adapter) -> Any:
    if getattr(original, "__kernel_history__", False):
        original = original.__wrapped__

    @functools.wraps(original)
    def __init__(self: Any, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        record = _record(self, adapter)
        if record is not None:
            _note_inputs(record, (*args, *kwargs.values()))

    __init__.__kernel_history__ = True
    __init__.__wrapped__ = original
    return __init__


def _setter(original: Any, adapter: _Adapter) -> Any:
    if getattr(original, "__kernel_history__", False):
        original = original.__wrapped__

    @functools.wraps(original)
    def setter(self: Any, *args: Any, **kwargs: Any) -> Any:
        answer = original(self, *args, **kwargs)
        record = _record(self, adapter)
        if record is not None:
            _note_inputs(record, (*args, *kwargs.values()))
        return answer

    setter.__kernel_history__ = True
    setter.__wrapped__ = original
    return setter


def _finisher(original: Any) -> Any:
    if getattr(original, "__kernel_history__", False):
        original = original.__wrapped__

    @functools.wraps(original)
    def finisher(self: Any, *args: Any, **kwargs: Any) -> Any:
        answer = original(self, *args, **kwargs)
        index = getattr(_LOCAL, "index", None)
        if index is not None and isinstance(answer, TopoDS_Shape) and not answer.IsNull():
            record = index.get(id(self))
            if record is not None and record.maker is self and record.result is None:
                record.result = answer.Located(answer.Location())
                record.order = next(_ORDER)
        return answer

    finisher.__kernel_history__ = True
    finisher.__wrapped__ = original
    return finisher


# --- composition --------------------------------------------------------------------------


def _map(shape: Any, kind: Any) -> Any:
    found = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, kind, found)
    return found


def _merged(found: tuple, more: tuple) -> tuple:
    """Origins ``more`` added to ``found``: one per source element, the stronger account
    kept where a source arrives twice (generated over modified over copied), and its
    first place."""
    by_source = {(j, kind, ordinal): (how, place) for j, kind, ordinal, how, place in found}
    for j, kind, ordinal, how, place in more:
        known = by_source.get((j, kind, ordinal))
        if known is None:
            by_source[(j, kind, ordinal)] = (how, place)
        else:
            stronger = how if _STRENGTH[how] > _STRENGTH[known[0]] else known[0]
            by_source[(j, kind, ordinal)] = (stronger, min(place, known[1]))
    return tuple((j, kind, ordinal, how, place) for (j, kind, ordinal), (how, place) in by_source.items())


def _then(first: str, second: str) -> str:
    """An element that came from its source ``first`` and then from that ``second``."""
    if first == SAME:
        return second
    if second == SAME:
        return first
    return GENERATED if GENERATED in (first, second) else MODIFIED


def _held(shape: Any) -> set:
    """The TShapes a shape is made of whole: itself and every compound, solid, shell and
    face it holds -- what one maker's result can reach another's, or a feature's, as."""
    found = {shape.TShape()}
    for kind in (TopAbs_COMPOUND, TopAbs_SOLID, TopAbs_SHELL, TopAbs_FACE):
        held = _map(shape, kind)
        found.update(held.FindKey(index).TShape() for index in range(1, held.Extent() + 1))
    return found


def _feeding(taken: list[_Record], results: list[Any]) -> list[_Record]:
    """The makers whose results reach the feature's results -- held by one of them, or
    by the input of a maker that does -- in order: what FreeCAD's mapper chain follows.
    build123d runs makers whose results it drops (a placement deep-copies its shape and
    keeps the original), and those report nothing of the feature."""
    wanted: set = set()
    for result in results:
        if result is not None:
            wanted |= _held(result)
    feeding = []
    for record in reversed(taken):
        if not wanted.isdisjoint(_held(record.result)):
            feeding.append(record)
            for shape in record.inputs:
                wanted |= _held(shape)
    feeding.reverse()
    return feeding


def compose(inputs: list[Any], results: list[Any], records: list[_Record]) -> History:
    """The feature's :class:`History`: ``inputs`` the shapes it was given as it was given
    them (None where empty), ``results`` the shapes it returned (None where empty) and
    ``records`` the makers it ran (``recording``)."""
    taken = sorted((record for record in records if record.result is not None), key=lambda record: record.order)
    if not taken or not any(shape is not None for shape in results):
        return History()
    taken = _feeding(taken, results)
    if not taken:
        return History()
    given: list[list | None] = [None] * len(inputs)
    reported = TopTools_IndexedMapOfShape()
    origins: list[tuple] = []

    def source(sub: Any, kind: int) -> tuple | None:
        index = reported.FindIndex(sub)
        if index:
            return origins[index - 1]
        for j, shape in enumerate(inputs):
            if shape is None:
                continue
            maps = given[j]
            if maps is None:
                maps = given[j] = [_map(shape, topabs) for topabs in KINDS]
            ordinal = maps[kind].FindIndex(sub)
            if ordinal:
                return ((j, kind, ordinal, SAME, ()),)
        return None

    def report(shape: Any, found: tuple) -> None:
        index = reported.Add(shape)
        if index > len(origins):
            origins.append(found)
        else:
            origins[index - 1] = _merged(origins[index - 1], found)

    for record in taken:
        if record.adapter is _WIRE:
            _wire_copies(record, source, report)
            continue
        query = _prepared(record)
        if query is None:
            continue
        held = None if record.adapter.generator else [_map(record.result, kind) for kind in KINDS]
        for shape in record.inputs:
            for kind, topabs in enumerate(KINDS):
                subs = _map(shape, topabs)
                for ordinal in range(1, subs.Extent() + 1):
                    sub = subs.FindKey(ordinal)
                    if held is not None and held[kind].Contains(sub):
                        continue
                    found = source(sub, kind)
                    if found is None:
                        continue
                    for at, (made, how) in enumerate(_asked(query, sub), 1):
                        made_kind = _KIND.get(made.ShapeType())
                        if made_kind is None or made.IsSame(sub):
                            continue
                        report(made, tuple((j, k, o, _then(was, how), (*place, at)) for j, k, o, was, place in found))
    history = History()
    if not origins:
        return history
    # Every element of every result, once, with each place it has among them: a result's
    # element, by its ordinal there -- and, for a result that is a maker's result at another
    # place (build123d's ``located`` places a copy), the maker's element at that ordinal.
    held = [TopTools_IndexedMapOfShape() for _kind in KINDS]
    places: list[list[list[tuple[int, int]]]] = [[] for _kind in KINDS]
    for position, result in enumerate(results):
        if result is None:
            continue
        sources = [result] + [record.result for record in taken
                              if record.result.IsPartner(result) and not record.result.IsSame(result)]
        for shape in sources:
            for kind, topabs in enumerate(KINDS):
                elements = _map(shape, topabs)
                for ordinal in range(1, elements.Extent() + 1):
                    index = held[kind].Add(elements.FindKey(ordinal))
                    if index > len(places[kind]):
                        places[kind].append([])
                    if (position, ordinal) not in places[kind][index - 1]:
                        places[kind][index - 1].append((position, ordinal))
    for index in range(1, reported.Extent() + 1):
        made = reported.FindKey(index)
        kind = _KIND[made.ShapeType()]
        at = held[kind].FindIndex(made)
        for position, ordinal in places[kind][at - 1] if at else ():
            key = (position, kind, ordinal)
            found = history.origins.get(key)
            history.origins[key] = origins[index - 1] if found is None else _merged(found, origins[index - 1])
    return history


def _prepared(record: _Record) -> Callable[[Any], list[tuple[Any, str]]] | None:
    try:
        return record.adapter.prepare(record.maker)
    except Exception:  # noqa: BLE001 - a maker that cannot say what it did reports nothing
        _STATS["raised"] += 1
        return None


def _asked(query: Callable[[Any], list[tuple[Any, str]]], sub: Any) -> list[tuple[Any, str]]:
    _STATS["queries"] += 1
    try:
        return query(sub)
    except Exception:  # noqa: BLE001 - counted: a maker asked about a shape it never saw
        _STATS["raised"] += 1
        return []


def _wire_copies(record: _Record, source: Callable[[Any, int], tuple | None],
                 report: Callable[[Any, tuple], None]) -> None:
    """``BRepBuilderAPI_MakeWire`` reports nothing, and copies each edge whose vertices it
    merges with a neighbour's: an edge of the wire that is no edge it was given, on the
    same curve object over the same parameters as one it was, is that edge copied."""
    from OCP.BRep import BRep_Tool
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    def signature(edge: Any, alive: list) -> tuple | None:
        location = TopLoc_Location()
        curve = BRep_Tool.Curve_s(TopoDS.Edge_s(edge), location, 0.0, 0.0)
        if curve is None:
            return None
        alive.append(curve)  # the wrapper kept alive, so the same curve answers as the same object
        transform = location.Transformation()
        return (id(curve), BRep_Tool.Range_s(TopoDS.Edge_s(edge)),
                tuple(transform.Value(row, column) for row in (1, 2, 3) for column in (1, 2, 3, 4)))

    alive: list = []
    given: dict[tuple, Any] = {}
    held = TopTools_IndexedMapOfShape()
    for shape in record.inputs:
        edges = _map(shape, TopAbs_EDGE)
        for ordinal in range(1, edges.Extent() + 1):
            edge = edges.FindKey(ordinal)
            held.Add(edge)
            key = signature(edge, alive)
            if key is not None:
                given.setdefault(key, edge)
    if not given:
        return
    edges = _map(record.result, TopAbs_EDGE)
    for ordinal in range(1, edges.Extent() + 1):
        edge = edges.FindKey(ordinal)
        if held.Contains(edge):
            continue
        key = signature(edge, alive)
        original = given.get(key) if key is not None else None
        if original is None:
            continue
        found = source(original, _KIND[TopAbs_EDGE])
        if found is not None:
            report(edge, found)
