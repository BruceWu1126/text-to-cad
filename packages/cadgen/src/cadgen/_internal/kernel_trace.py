"""Features and lineage: what the kernel boundary saw during one model run, and
the names it gives the faces, edges and vertices of what the run made (STORE.md
§2 and §3; README law 20).

A **feature** is one call of a patched kernel construction (``kernel_memo``)
made from the model's own code: the operation, the innermost first-party
frame that called it (the file, relative to the model's folder, the line and
the enclosing function) and the features that made the shapes it was given. A
call with no first-party frame on the stack is cadgen's own and is not a
feature; a call nested inside another feature's operation belongs to that
feature. A feature is named within a part, among the features the part
descends from -- those that made it and, transitively, whatever they were
given -- in call order: ``<function>.<operation><count>``, the enclosing
function, the operation (``extrude``, ``fillet``, ``cut``, a constructor's
class in lower case) and how many of the part's features before it, this one
included, that function's calls of that operation were: ``dial.extrude1``. So
an edit elsewhere in the model -- another part's feature, a call no element of
this part descends from -- renames nothing here. An author's label pins the
name instead, in what is made of the labelled shape: a shape the model labels
and hands to another construction names the feature that made it by that label
(``lid``) in every part made from that call on that the feature is an ancestor
of, the part's first feature to claim a label keeping it -- never in a part made
before the label reached a construction, and not in the part that shape is itself:
there (an assembly's leaf) the label names the occurrence, and the elements keep
their construction's names, so identical constructions labelled apart are one
part. A function or a label that is not an ASCII identifier
is written as its ASCII form and a digest of it (``identifier``), so no two are
written alike. Lines and files are not in a name: they are where the trace
says a feature ran (the provenance entry, ``store where``).

**Lineage is the kernel's history, composed as FreeCAD 1.0 composes it**
(``TopoShape::makeShapeWithElementMap``). The makers a feature runs report, of each
element of its results, the elements of its inputs it was modified, generated or
copied from (``kernel_history``), and the feature names every element of its
results from its inputs' names, in this order:

1. **Identity.** An element one of its inputs holds where the operation saw it --
   the kernel keeps what an operation leaves alone, and every boolean here leaves
   alone what it does not change -- or holds at another place of the same shape (a
   placement), or a copy of one, is that element, and has its name.
2. **History.** An element the makers report is named from its first source -- a
   vertex before an edge before a face, then the input it is of, then by name --
   and the step the feature took: modified (``M``), generated from an element of a
   kind (``G<kind>``), both (``MG<kind>``), with up to four further sources.
3. **Reverse pass.** An unnamed edge of a named face, then an unnamed vertex of a
   named edge, is named from its first named parent (by name) and its number among
   that parent's unnamed sub-elements (``U``).
4. **Forward pass.** An unnamed edge whose vertices are all named, then an unnamed
   face whose outer wire's edges are, is named from the first of them by name, with
   up to four more (``L``).
5. **Made.** An element still unnamed is the feature's own: its number among the
   elements of its kind the feature made, in the order of its results, as every
   element of a primitive is.

An element passed on unchanged from a shape no feature made (a materialized child, an
imported file, a shape built outside the patched surface) has no name here -- its part
names it by its geometry (``geometric_tokens``) -- and names nothing: it is no parent
or lower element of a name, and a source from it is skipped, so an element made out of
one alone falls to the passes, and to the feature. Nothing here measures geometry:
what a step says is what the kernel reported.

An element's **name** is its kind and its derivation (``cad_ref_syntax``): the atom it
starts from (the n-th element of its kind a feature made, ``dial.make_box1.6``) and a
step per feature that made it out of another (``f:dial.make_box1.6;M:dial.cut1``,
``e:sk.make_rect1.2;Ge:part.extrude1``). A derivation longer than ``FOLD_LENGTH``
characters is **folded** to the feature of its last step and a digest of it
(``dial.cut1~5d1e0c2f9a3b``), and a further source that is not an atom is folded
whatever its length, so every name stays short; the component's lineage keeps what each
folded atom stands for (its ``table``), every one its names or that table reach.
Elements of one result a step made alike -- from the same sources, the same way: the
pieces of a face one cut splits, the pieces of a ruled loft's long edge, which it
reports as generated from every vertex on it -- are numbered in the step, the second
and later by their place among what was reported of their first source (``;M2:``,
``;Gv2:``, FreeCAD's index), so a later operation that changes one leaves the others'
numbers alone. Of a component's elements still named alike (two places of one shape
in one body), the second and later, in ordinal order, take ``;D2``, ``;D3``.

The **structure** of a feature's naming -- each element's rule: the input element it
is, its sources, its parent candidates, its lower elements, its number -- is computed
from its history and the live shapes once, the first time a component needs it, and kept
with the history, so a hit's feature (whose history is its miss's) reuses it; it depends
on whether the elements of the feature's inputs have names, so it is kept per account of
that (``_skey``). The names themselves are rendered per part, as a feature's name is the
part's: strings, from those structures, with no maker run and no map built on a warm run.

``Trace.account`` is a component's whole account: its lineage, the object its tree
entry names (``component_package``) -- the feature that made the body, per element
ordinal its name, and the table -- and the feature each feature name it holds stands
for; ``Trace.feature_rows`` is the run's features, what the provenance entry keeps
with those names (``cadgen.store.provenance``).
"""

from __future__ import annotations

import bisect
import hashlib
import re
import sys
import weakref
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from OCP.BRepTools import BRepTools
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_VERTEX
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS
from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape, TopTools_IndexedMapOfShape

from cadgen._internal.kernel_history import listed
from cadgen._internal.source_hash import is_first_party_source_file
from cadgen.cad_ref_syntax import folded_atoms

__all__ = ["FOLD_LENGTH", "Feature", "Trace", "identifier", "operation_name"]

# A derivation longer than this many characters is folded (``cad_ref_syntax``): long enough
# for a made atom and a step or two, short enough for a STEP file and a prompt.
FOLD_LENGTH = 48
# Further sources (history) and lower elements (forward pass) a step lists, as FreeCAD does.
_EXTRAS = 4
# The element kinds, by their index in ``kernel_history.KINDS``: vertex, edge, face.
_TOPABS = (TopAbs_VERTEX, TopAbs_EDGE, TopAbs_FACE)
_LETTER = "vef"
_PLURAL = ("vertices", "edges", "faces")
VERTEX, EDGE, FACE = 0, 1, 2
_HERE = Path(__file__).resolve().parent
# A kernel call reached through cadgen's own machinery before any model frame
# (a child's materialization, the store's own constructions) is cadgen's, not
# the model's: no feature. Its public helpers (``cadgen.geometry``,
# ``cadgen.assembly``) are called by the model and count as build123d does.
_OWN = tuple(_HERE.parent / name for name in ("store", "_internal", "daemon"))
_NOT_IDENTIFIER = re.compile(r"[^A-Za-z0-9_]+")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# What a digest of a name looks like at its end, which a name written as it is never has.
_DIGESTED = re.compile(r"_[0-9a-f]{8}$")
# The frames Python names itself, each written as its word; a function of that name
# is not one of them, and takes a digest.
_FRAMES = {"<module>": "module", "<lambda>": "lambda", "<genexpr>": "genexpr", "<listcomp>": "listcomp",
           "<dictcomp>": "dictcomp", "<setcomp>": "setcomp"}
_FRAME_WORDS = frozenset(_FRAMES.values())
# Placements compare rounded, as an occurrence's placement does (tree_tokens): only to tell
# the places of one shape in another apart.
_POSITION_QUANTUM = 1e-6
_DIRECTION_QUANTUM = 1e-9


def identifier(text: str) -> str:
    """``text`` as one name segment, no two texts alike: an ASCII identifier as it is,
    and the frame Python names ``<module>`` (or ``<lambda>``, ``<genexpr>``) as its
    word. Anything else -- a name beyond ASCII, a label with a colon, an identifier
    that is one of those words or ends as a digest does -- is its ASCII form (its runs
    of anything but ASCII letters, digits and underscores one underscore, trimmed)
    and the first eight hex digits of its sha256 (``корпус`` is ``_9f1c0b2e``,
    ``mounting_eye:lower`` is ``mounting_eye_lower_`` and its digest), so it is never
    a name another text is written as."""
    text = str(text)
    word = _FRAMES.get(text)
    if word is not None:
        return word
    if _IDENTIFIER.fullmatch(text) and text not in _FRAME_WORDS and not _DIGESTED.search(text):
        return text
    form = _NOT_IDENTIFIER.sub("_", text).strip("_")
    digest = hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:8]
    if not form:
        return f"_{digest}"
    return f"{'_' if form[0].isdigit() else ''}{form}_{digest}"


def operation_name(op: str) -> str:
    """A feature's operation as its name spells it: the method without its class
    (``Solid.extrude`` is ``extrude``), a constructor's class in lower case."""
    return identifier(op.rsplit(".", 1)[-1].lower())


@dataclass
class Feature:
    """One kernel construction the model's code called."""

    index: int
    op: str
    file: str
    line: int
    function: str
    # The feature of each shape the call was given, in call order; None for a
    # shape no feature of this run made.
    inputs: list[int | None]
    # The input shapes as passed and the results as returned: handle copies,
    # so a later in-place move of the object does not move what the operation
    # saw; None for an empty shape or a child model's promise not yet forced.
    handles: list[Any] = field(default_factory=list, repr=False)
    results: list[Any] = field(default_factory=list, repr=False)
    # The author's label for what this feature made, when one pins its name, and the
    # feature whose call pinned it: a part made before that call is named as if the
    # label had never been set.
    label: str | None = None
    pinned_at: int | None = None
    # What the makers the call ran report of its results (``kernel_history.History``),
    # with the structures of its naming kept in it; None where it could not be composed.
    history: Any = field(default=None, repr=False)
    # Functions of the feature alone, computed once: the operation as its name spells
    # it, the name without its count (``<function>.<operation>``, as written) -- every
    # part's naming reads them for each feature it descends from -- and, on first use,
    # the first position at which each TShape was returned.
    operation: str = field(init=False, repr=False)
    stem: str = field(init=False, repr=False)
    _positions: dict[Any, int] | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.operation = operation_name(self.op)
        self.stem = f"{identifier(self.function)}.{self.operation}"

    def position_of(self, tshape: Any) -> int | None:
        """The first position among the results at which ``tshape`` was returned, or None."""
        positions = self._positions
        if positions is None:
            positions = self._positions = {}
            for position, result in enumerate(self.results):
                if result is not None:
                    positions.setdefault(result.TShape(), position)
        return positions.get(tshape)

    def row(self) -> dict[str, Any]:
        """The feature as the provenance entry records it: where it ran, and the
        features that made what it was given, by their place in the run."""
        return {"op": self.op, "file": self.file, "line": self.line, "function": self.function,
                "inputs": list(self.inputs)}


@dataclass
class _Structure:
    """One feature's naming: ``rules[position][kind][ordinal - 1]`` for each element of
    each result (None for an empty result), each rule one of ``("x",)`` passed on from
    a shape no feature made, no name; ``("s", input, ordinal)`` that input's element;
    ``("h", sources)`` named by history, each source ``(input, kind, ordinal, how)``;
    ``("u", candidates)`` from a parent, each ``(kind, ordinal, number)``; ``("l",
    lowers)`` from its lower elements, each ``(kind, ordinal)``; ``("m", number)`` made."""

    rules: list[list[list[tuple]] | None]


class Trace:
    """The features of one run, in call order, and the lineage they give."""

    def __init__(self, model_dir: Path | None) -> None:
        self.model_dir = Path(model_dir).resolve() if model_dir is not None else None
        self.features: list[Feature] = []
        self._first_party: dict[str, str] = {}
        self._display: dict[str, str] = {}
        # id(shape) -> (weakref, feature index): the feature that returned each
        # live result object. A dead reference, or a recycled id, answers nothing.
        self._by_object: dict[int, tuple[weakref.ref, int]] = {}
        # The same for the call that made each live object: the first feature that
        # returned it itself, never one that only held it (a compound it was attached to).
        self._made_object: dict[int, tuple[weakref.ref, int]] = {}
        self._made_by: Callable[[Any], int | None] = lambda _tshape: None
        # Per feature, as it is recorded: the features it descends from (itself and,
        # transitively, every feature that made what it was given) as bits by index; per
        # stem, the features of that stem; per label, the features that claim it, in order.
        self._ancestors: list[int] = []
        self._stems: dict[str, int] = {}
        self._labelled: dict[str, list[int]] = {}
        # Per run: where each input came from (``_source``), the structure each feature is
        # named by and what it was computed from (``_skey``), the element each element that is
        # an input's resolves to (``_origin``), and maps of results in their own frames.
        self._sources: dict[tuple[int, int], tuple] = {}
        self._structures: dict[int, _Structure] = {}
        self._skeys: dict[int, bytes] = {}
        self._origins: dict[tuple, tuple | None] = {}
        self._inputs: dict[tuple, tuple | None] = {}
        self._local: dict[tuple, Any] = {}
        self._places: dict[tuple, dict[Any, list[int]]] = {}

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

    def made(self, shape: Any) -> int | None:
        """The feature whose call made this very object -- the first that returned it
        itself -- or None."""
        found = self._made_object.get(id(shape))
        if found is None:
            return None
        reference, index = found
        return index if reference() is shape else None

    def given(self, shapes: list[Any], feature_of: Callable[[Any], int | None]) -> list[tuple[Any, int | None, Any]]:
        """The inputs of a call as it is given them, taken before it runs: each shape,
        the feature that made it (``feature_of``) and a handle of its ``TopoDS`` then --
        an in-place operation (``clean``) leaves its input object wrapping the result."""
        return [(shape, feature_of(shape), handle_of(shape)) for shape in shapes]

    def record(self, op: str, frame: tuple[str, int, str], inputs: list[tuple[Any, int | None, Any]],
               results: list[Any], made: list[Any] = ()) -> Feature:
        """One feature: ``inputs`` are the call's, as :meth:`given` took them,
        ``results`` the live result objects the call returned and the children they hold,
        ``made`` the objects it returned itself. A labelled input pins the name of the
        feature that made it."""
        file, line, function = frame
        feature = Feature(
            index=len(self.features), op=op, file=file, line=line, function=function,
            inputs=[index for _shape, index, _handle in inputs],
            handles=[handle for _shape, _index, handle in inputs],
            results=[handle_of(shape) for shape in results],
        )
        self.features.append(feature)
        ancestors = 1 << feature.index
        for given in feature.inputs:
            if given is not None:
                ancestors |= self._ancestors[given]
        self._ancestors.append(ancestors)
        self._stems[feature.stem] = self._stems.get(feature.stem, 0) | (1 << feature.index)
        for shape, index, _handle in inputs:
            if index is not None:
                self._pin(index, getattr(shape, "__dict__", {}).get("label"), at=feature.index)
        for shape in results:
            try:
                self._by_object[id(shape)] = (weakref.ref(shape), feature.index)
            except TypeError:
                pass
        for shape in made:
            try:
                found = self._made_object.get(id(shape))
                if found is None or found[0]() is not shape:
                    self._made_object[id(shape)] = (weakref.ref(shape), feature.index)
            except TypeError:
                pass
        return feature

    def _pin(self, index: int, label: object, *, at: int) -> None:
        """Name feature ``index`` by the author's ``label``, the first one it is
        handed on with -- by the call of feature ``at`` -- in what is made of the shape
        it made from that call on (:meth:`_names`); a label that is an occurrence token
        (a tree's, not an author's) pins nothing. Of a part's features, the first to
        claim a label keeps it."""
        from cadgen._internal.tree_tokens import is_occurrence_token

        if not isinstance(label, str) or not label or is_occurrence_token(label):
            return
        feature = self.features[self.unwrapped(index)]
        if feature.label is None:
            feature.label, feature.pinned_at = identifier(label), at
            bisect.insort(self._labelled.setdefault(feature.label, []), feature.index)

    def unwrapped(self, index: int) -> int:
        """The feature that made what feature ``index`` returned, through the features
        that only place or hold another's: a placement (``moved``, ``located``, a copy
        that keeps the shape) returns the very shape it was given, and a compound built
        of one shape (build123d wraps every part it builds in one) is that shape."""
        from OCP.TopoDS import TopoDS_Iterator

        while True:
            feature = self.features[index]
            result = feature.results[0] if len(feature.results) == 1 else None
            if result is None:
                return index
            held = result.TShape()
            if feature.operation == "compound":
                children = TopoDS_Iterator(result)
                child = children.Value() if children.More() else None
                if child is None:
                    return index
                children.Next()
                if children.More():
                    return index
                held = child.TShape()
            inner = next((feature.inputs[j] for j, handle in enumerate(feature.handles)
                          if handle is not None and feature.inputs[j] is not None and feature.inputs[j] != index
                          and handle.TShape() == held), None)
            if inner is None:
                return index
            index = inner

    # --- the payload ---------------------------------------------------------------------

    def feature_rows(self) -> list[dict[str, Any]]:
        """The run's features in call order, as the provenance entry records them."""
        return [feature.row() for feature in self.features]

    def account(self, body: Any, made_by: Callable[[Any], int | None]) -> tuple[dict[str, Any] | None, dict[str, int]]:
        """The lineage of one unlocated component ``body`` (a ``TopoDS_Shape``):
        ``{"made": <feature name or None>, "faces": [...], "edges": [...],
        "vertices": [...], "table": {<folded atom>: <derivation>}}``, each list
        holding an element name or None per ``TopExp.MapShapes_s`` ordinal, the table
        every folded atom the names reach; None when no feature of the run made any of
        it. ``made_by`` answers, for a ``TopoDS_TShape``, the feature of this run that
        made it. With it, the feature (its index in the run) each feature name the
        lineage holds stands for, with the features those were given: what ``store
        where`` answers a name of this part with."""
        self._made_by = made_by
        maker = self._maker(made_by, body)
        owners = self._owners(made_by, body)
        if maker is None and not owners:
            return None, {}
        # A body build123d only wrapped or placed was made by what it wraps.
        made_feature = None if maker is None else self.unwrapped(maker)
        roots = [index for _shape, index in owners] + ([made_feature] if made_feature is not None else [])
        names = self._names(roots, made_feature, made_at=max(roots))
        renderer = _Renderer(self, names)
        payload: dict[str, Any] = {"made": None if made_feature is None else names[made_feature]}
        named = False
        # The features the names hold, spelled out or folded into the table, as bits.
        held = 0
        for kind in (FACE, EDGE, VERTEX):
            elements = _map(body, _TOPABS[kind])
            texts = []
            for ordinal in range(1, elements.Extent() + 1):
                node = self._component_node(body, owners, elements.FindKey(ordinal), kind, ordinal)
                rendered = None if node is None else renderer.rendered(node)
                texts.append(None if rendered is None else rendered[0])
                held |= 0 if rendered is None else rendered[2]
            written = renderer.distinct(texts)
            payload[_PLURAL[kind]] = [None if text is None else f"{_LETTER[kind]}:{text}" for text in written]
            named = named or any(text is not None for text in written)
        if maker is None and not named:
            return None, {}
        payload["table"] = renderer.closed([name for kind in (FACE, EDGE, VERTEX)
                                            for name in payload[_PLURAL[kind]] if name is not None])
        stands: set[int] = set() if made_feature is None else {made_feature}
        while held:
            low = held & -held
            stands.add(low.bit_length() - 1)
            held ^= low
        for index in list(stands):
            stands.update(given for given in self.features[index].inputs if given is not None)
        return payload, {names[index]: index for index in sorted(stands) if index in names}

    def _names(self, roots: list[int], body: int | None, *, made_at: int) -> "_Names":
        """The names of the features a part descends from -- ``roots`` and, transitively,
        every feature that made what one of them was given (:class:`_Names`)."""
        ancestors = 0
        for index in roots:
            if index is not None:
                ancestors |= self._ancestors[index]
        return _Names(self, ancestors, body, made_at)

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

    def _component_node(self, body: Any, owners: list[tuple[Any, int]], element: Any, kind: int,
                        ordinal: int) -> tuple | None:
        """The element of a feature's result that element ``ordinal`` of the component
        ``body`` is: ``(feature, position, kind, ordinal there)``, or None."""
        for shape, index in owners:
            whole = shape is body
            local = element if whole else _relative(element, shape)
            if not whole and not self._own_map(shape, kind).Contains(local):
                continue
            located = self._located(index, shape)
            if located is None:
                return None
            position, location = located
            if whole and location is None:
                return index, position, kind, ordinal
            found = self._local_map(index, position, kind).FindIndex(local if location is None else local.Moved(location))
            return (index, position, kind, found) if found else None
        return None

    # --- where things are ----------------------------------------------------------------

    def _own_map(self, shape: Any, kind: int) -> Any:
        key = ("own", shape.TShape(), kind)
        found = self._local.get(key)
        if found is None:
            found = self._local[key] = _map(shape.Located(TopLoc_Location()), _TOPABS[kind])
        return found

    def _local_map(self, index: int, position: int, kind: Any) -> Any:
        """``TopExp.MapShapes`` of result ``position`` of feature ``index`` in its own frame,
        of one kind (an index of ``_TOPABS``, or a ``TopAbs`` type)."""
        key = (index, position, kind)
        found = self._local.get(key)
        if found is None:
            result = self.features[index].results[position]
            topabs = _TOPABS[kind] if isinstance(kind, int) else kind
            found = self._local[key] = _map(result.Located(TopLoc_Location()), topabs)
        return found

    def _located(self, index: int, shape: Any) -> tuple[int, Any] | None:
        """Where feature ``index`` returned ``shape``: ``(position, None)`` for a result
        that is it, else ``(position, location)`` for the first result holding it --
        where ``shape`` is placed, if a place of it is, else its first place -- the
        location of it inside that result (unlocated); None when no result holds it."""
        feature = self.features[index]
        tshape = shape.TShape()
        position = feature.position_of(tshape)
        if position is not None:
            return position, None
        topabs = shape.ShapeType()
        for position, result in enumerate(feature.results):
            if result is None:
                continue
            key = (index, position, topabs)
            places = self._places.get(key)
            if places is None:
                elements = self._local_map(index, position, topabs)
                places = self._places[key] = {}
                for ordinal in range(1, elements.Extent() + 1):
                    places.setdefault(elements.FindKey(ordinal).TShape(), []).append(ordinal)
            found = places.get(tshape)
            if not found:
                continue
            elements = self._local_map(index, position, topabs)
            relative = _transform(result.Location().Inverted().Multiplied(shape.Location()))
            ordinal = next((at for at in found if _transform(elements.FindKey(at).Location()) == relative), found[0])
            return position, elements.FindKey(ordinal).Location()
        return None

    def _source(self, index: int, j: int) -> tuple:
        """Where input ``j`` of feature ``index`` came from: ``("followed", feature,
        position, location)`` -- the feature that made it, where it returned it
        (:meth:`_located`); ``("foreign",)`` for a shape no other feature made;
        ``("lost",)`` for one a feature made that the trace cannot find in what it
        returned."""
        found = self._sources.get((index, j))
        if found is not None:
            return found
        feature = self.features[index]
        handle = feature.handles[j]
        found = ("foreign",)
        if handle is not None:
            # A later mutation can re-register the same TShape; it made no earlier input.
            makers = [up for up in dict.fromkeys((self._made_by(handle.TShape()), feature.inputs[j]))
                      if up is not None and up < index]
            if makers:
                found = ("lost",)
                for up in makers:
                    located = self._located(up, handle)
                    if located is not None:
                        found = ("followed", up, *located)
                        break
        self._sources[(index, j)] = found
        return found

    def _input_node(self, index: int, j: int, kind: int, ordinal: int) -> tuple | None:
        """The element of a result of the feature that made input ``j`` of feature
        ``index`` that that input's element ``ordinal`` of ``kind`` is, or None."""
        key = (index, j, kind, ordinal)
        found = self._inputs.get(key, False)
        if found is not False:
            return found
        source = self._source(index, j)
        if source[0] != "followed":
            found = None
        else:
            _followed, up, position, location = source
            if location is None:
                found = up, position, kind, ordinal
            else:
                local = self._own_map(self.features[index].handles[j], kind).FindKey(ordinal)
                at = self._local_map(up, position, kind).FindIndex(local.Moved(location))
                found = (up, position, kind, at) if at else None
        self._inputs[key] = found
        return found

    # --- structures ----------------------------------------------------------------------

    def _rule(self, node: tuple) -> tuple | None:
        index, position, kind, ordinal = node
        structure = self._structures.get(index)
        rules = (structure if structure is not None else self._structure(index)).rules[position]
        if rules is None or not 0 < ordinal <= len(rules[kind]):
            return None
        return rules[kind][ordinal - 1]

    def _structure(self, index: int) -> _Structure:
        """Feature ``index``'s naming (:class:`_Structure`), with every feature it descends
        from named first, without recursion."""
        found = self._structures.get(index)
        if found is not None:
            return found
        stack = [(index, False)]
        while stack:
            current, ready = stack.pop()
            if current in self._structures:
                continue
            if ready:
                self._structures[current] = self._built(current)
                continue
            stack.append((current, True))
            for j in range(len(self.features[current].handles)):
                source = self._source(current, j)
                if source[0] == "followed" and source[1] not in self._structures:
                    stack.append((source[1], False))
        return self._structures[index]

    def _skey(self, index: int) -> bytes:
        """What a feature's structure was computed from, beside its own history and shapes:
        for each input, the same of the feature that made it, or that none of this run did.
        The same in every run of the same code, so a hit's structure is its miss's."""
        found = self._skeys.get(index)
        if found is None:
            parts = []
            for j in range(len(self.features[index].handles)):
                source = self._source(index, j)
                parts.append(self._skey(source[1]) if source[0] == "followed" else source[0].encode())
            found = self._skeys[index] = hashlib.blake2b(b"|".join(parts), digest_size=12).digest()
        return found

    def _built(self, index: int) -> _Structure:
        feature = self.features[index]
        key = self._skey(index)
        history = feature.history
        if history is not None:
            found = history.structures.get(key)
            if found is not None:
                return found
        structure = self._computed(index)
        if history is not None:
            history.structures[key] = structure
        return structure

    def _same(self, index: int, j: int, kind: int, ordinal: int) -> tuple | None:
        """The rule of an element that is input ``j``'s element ``ordinal``: that element,
        nothing where it is a shape's no feature made, unnamed where the trace cannot
        follow the input."""
        source = self._source(index, j)
        if source[0] == "foreign":
            return ("x",)
        node = self._input_node(index, j, kind, ordinal)
        if node is None:
            return None
        rule = self._rule(node)
        if rule is None:
            return None
        return ("x",) if rule[0] == "x" else ("s", j, ordinal)

    def _sames(self, index: int, j: int, kind: int, count: int) -> list[tuple | None]:
        """:meth:`_same` of each of the ``count`` elements of one kind of input ``j`` of
        feature ``index``, by ordinal, together: an input that is a result as it was
        returned has its elements' rules in that result's structure, by the same ordinals."""
        source = self._source(index, j)
        if source[0] == "foreign":
            return [("x",)] * count
        if source[0] != "followed":
            return [None] * count
        _followed, up, position, location = source
        if location is not None:
            return [self._same(index, j, kind, ordinal) for ordinal in range(1, count + 1)]
        rules = self._structure(up).rules[position]
        held = () if rules is None else rules[kind]
        return [None if ordinal > len(held) else ("x",) if held[ordinal - 1][0] == "x" else ("s", j, ordinal)
                for ordinal in range(1, count + 1)]

    def _computed(self, index: int) -> _Structure:
        feature = self.features[index]
        origins = feature.history.origins if feature.history is not None else {}
        given = [None if handle is None else [_map(handle, topabs) for topabs in _TOPABS] for handle in feature.handles]
        held_inputs = [(j, held) for j, held in enumerate(given) if held is not None]
        first_inputs: dict[int, tuple[Any, list[tuple[int, int]]]] = {}
        sames: dict[tuple[int, int], list[tuple | None]] = {}

        def same(j: int, kind: int, ordinal: int) -> tuple | None:
            found = sames.get((j, kind))
            if found is None:
                found = sames[(j, kind)] = self._sames(index, j, kind, given[j][kind].Extent())
            return found[ordinal - 1]

        made = [TopTools_IndexedMapOfShape() for _kind in _TOPABS]
        rules: list[list[list[tuple]] | None] = []
        for position, result in enumerate(feature.results):
            if result is None:
                rules.append(None)
                continue
            maps = [_map(result, topabs) for topabs in _TOPABS]
            partners = [j for j, handle in enumerate(feature.handles)
                        if handle is not None and handle.TShape() == result.TShape() and not handle.IsSame(result)]
            table: list[list[tuple | None]] = [[None] * maps[kind].Extent() for kind in range(3)]
            for kind in range(3):
                by_history = False
                lookup = None
                if len(held_inputs) > 1 and maps[kind].Extent():
                    lookup = first_inputs.get(kind)
                    if lookup is None:
                        lookup = first_inputs[kind] = _input_index(held_inputs, kind)
                for ordinal in range(1, maps[kind].Extent() + 1):
                    element = maps[kind].FindKey(ordinal)
                    rule = None
                    if lookup is None:
                        for j, held in held_inputs:
                            at = held[kind].FindIndex(element)
                            if at:
                                rule = same(j, kind, at)
                                break
                    else:
                        elements, owners = lookup
                        at = elements.FindIndex(element)
                        if at:
                            j, source_ordinal = owners[at - 1]
                            rule = same(j, kind, source_ordinal)
                    if rule is None and partners:
                        rule = same(partners[0], kind, ordinal)
                    if rule is None:
                        rule = self._history_rule(origins.get((position, kind, ordinal)), kind, same)
                        by_history = by_history or rule is not None
                    table[kind][ordinal - 1] = rule
                if by_history:
                    _ranked(table[kind])
            _passes(result, maps, table)
            for kind in range(3):
                for ordinal, rule in enumerate(table[kind], 1):
                    if rule is None:
                        table[kind][ordinal - 1] = ("m", made[kind].Add(maps[kind].FindKey(ordinal)))
            rules.append(table)
        return _Structure(rules)

    @staticmethod
    def _history_rule(found: tuple | None, kind: int,
                      same: Callable[[int, int, int], tuple | None]) -> tuple | None:
        """The rule history gives an element with origins ``found``: a copy is its source;
        else its sources that have names, with the places it has among what was reported of
        them (``_ranked`` reads them), or nothing (the element falls to the passes).
        ``same`` is :meth:`_same` of the feature's inputs."""
        if not found:
            return None
        for j, source_kind, ordinal, how, _place in found:
            if how == "I" and source_kind == kind:
                return same(j, kind, ordinal)
        named = []
        for j, source_kind, ordinal, how, place in found:
            if how == "I":
                continue
            rule = same(j, source_kind, ordinal)
            if rule is not None and rule[0] != "x":
                named.append((j, source_kind, ordinal, how, place))
        if not named:
            return None
        named.sort()
        return "h", tuple(source[:4] for source in named), 1, named[0][4]

    def _origin(self, node: tuple) -> tuple | None:
        """The element ``node`` resolves to through the inputs it is: the first, back
        through the features, that is no input's; None where that is a shape's no
        feature made."""
        found = self._origins.get(node, False)
        if found is not False:
            return found
        path = []
        current: tuple | None = node
        while current is not None:
            known = self._origins.get(current, False)
            if known is not False:
                current = known
                break
            path.append(current)
            rule = self._rule(current)
            if rule is None or rule[0] == "x":
                current = None
                break
            if rule[0] != "s":
                break
            index, _position, kind, _ordinal = current
            current = self._input_node(index, rule[1], kind, rule[2])
        for visited in path:
            self._origins[visited] = current
        return current


class _Names:
    """The names of the features one part descends from (``ancestors``, bits by index),
    each computed when first asked: counted among them in call order -- a label pinned
    by the time the part was made (by feature ``made_at``, the last of its own), the first
    of the part's features to claim it keeping it, else ``<function>.<operation><count>``,
    every feature counting in its stem. ``body`` is the feature that made the part's body,
    whose own label names the occurrence and pins nothing here: identical constructions
    labelled apart are one part, named alike. A label pinned later -- set on a shape after
    it was given to the construction that made this part -- renames nothing here."""

    def __init__(self, trace: Trace, ancestors: int, body: int | None, made_at: int) -> None:
        self.trace = trace
        self.ancestors = ancestors
        self.body = body
        self.made_at = made_at
        self.found: dict[int, str] = {}

    def __contains__(self, index: object) -> bool:
        return isinstance(index, int) and index >= 0 and (self.ancestors >> index) & 1 == 1

    def __getitem__(self, index: int) -> str:
        name = self.get(index)
        if name is None:
            raise KeyError(index)
        return name

    def get(self, index: int, default: str | None = None) -> str | None:
        if index not in self:
            return default
        name = self.found.get(index)
        if name is None:
            name = self.found[index] = self._name(index)
        return name

    def _name(self, index: int) -> str:
        trace = self.trace
        feature = trace.features[index]
        label = feature.label
        if label is not None and self._pins(index):
            claimer = next((other for other in trace._labelled[label] if other in self and self._pins(other)), None)
            if claimer == index:
                return label
        stem = feature.stem
        return f"{stem}{(self.ancestors & trace._stems[stem] & ((2 << index) - 1)).bit_count()}"

    def _pins(self, index: int) -> bool:
        feature = self.trace.features[index]
        return feature.pinned_at is not None and feature.pinned_at <= self.made_at and index != self.body


class _Renderer:
    """The names of one part's elements: each element's derivation rendered with the
    part's feature names (``Trace._names``), folded where long, and the table of what
    each folded atom stands for."""

    def __init__(self, trace: Trace, names: _Names) -> None:
        self.trace = trace
        self.names = names
        # node -> (text, the feature of its last step, or of its atom, and every feature the
        # text names -- spelled out or in what it folds -- as bits by index), or None
        self.texts: dict[tuple, tuple[str, str, int] | None] = {}
        self.table: dict[str, str] = {}

    def rendered(self, node: tuple) -> tuple[str, str, int] | None:
        """The element's name -- its text, its last feature and the features it holds -- or
        None, each element it is made of rendered first, without recursion."""
        origin = self.trace._origin(node)
        if origin is None:
            return None
        texts = self.texts
        if origin in texts:
            return texts[origin]
        stack = [origin]
        open_: set[tuple] = set()
        depends: dict[tuple, tuple[tuple, list[tuple | None]]] = {}
        while stack:
            current = stack[-1]
            if current in texts:
                stack.pop()
                continue
            found = depends.get(current)
            if found is None:
                found = depends[current] = self._depends(current)
            needs = [need for need in found[1] if need is not None and need not in texts]
            if needs and current not in open_:
                open_.add(current)
                stack.extend(need for need in needs if need not in open_)
                continue
            texts[current] = self._composed(current, *found)
            stack.pop()
        return texts[origin]

    def _depends(self, node: tuple) -> tuple[tuple, list[tuple | None]]:
        """An element's rule, and the elements its name is made of -- its sources, its
        parent candidates, its lower elements, in the rule's order -- each as the element it
        resolves to (``Trace._origin``), or None."""
        rule = self.trace._rule(node) or ("x",)
        index, position, _kind, _ordinal = node
        if rule[0] == "h":
            found = [self.trace._input_node(index, j, source_kind, ordinal)
                     for j, source_kind, ordinal, _how in rule[1]]
        elif rule[0] == "u":
            found = [(index, position, parent_kind, parent) for parent_kind, parent, _number in rule[1]]
        elif rule[0] == "l":
            found = [(index, position, lower_kind, lower) for lower_kind, lower in rule[1]]
        else:
            return rule, []
        return rule, [None if element is None else self.trace._origin(element) for element in found]

    def _composed(self, node: tuple, rule: tuple, origins: list[tuple | None]) -> tuple[str, str, int] | None:
        """The name of ``node`` by its ``rule``, the elements it is made of (``origins``,
        :meth:`_depends`) rendered."""
        texts = self.texts
        index = node[0]
        feature = self.names.get(index)
        if feature is None or rule[0] in ("x", "s"):
            return None
        if rule[0] == "m":
            return f"{feature}.{rule[1]}", feature, 1 << index
        if rule[0] == "h":
            sources = []
            for (j, source_kind, _ordinal, how), origin in zip(rule[1], origins):
                rendered = None if origin is None else texts.get(origin)
                if rendered is not None:
                    sources.append((source_kind, j, rendered, how))
            if not sources:
                return None
            sources.sort(key=lambda source: (source[0], source[1], source[2][0]))
            hows = {how for _kind, _j, _rendered, how in sources}
            base_kind = sources[0][0]
            op = "M" if hows == {"M"} else ("G" if hows == {"G"} else "MG") + _LETTER[base_kind]
            if rule[2] > 1:
                op += str(rule[2])
            extras = _distinct([(source_kind, rendered) for source_kind, _j, rendered, _how in sources])
            return self._step(sources[0][2], op, feature, index, extras[1:1 + _EXTRAS])
        if rule[0] == "u":
            candidates = []
            for (_parent_kind, _parent, number), origin in zip(rule[1], origins):
                rendered = None if origin is None else texts.get(origin)
                if rendered is not None:
                    candidates.append((rendered[0], number, rendered))
            if not candidates:
                return None
            _text, number, rendered = min(candidates)
            return self._step(rendered, f"U{number}" if number > 1 else "U", feature, index, [])
        if rule[0] == "l":
            lowers = []
            for (lower_kind, _lower), origin in zip(rule[1], origins):
                rendered = None if origin is None else texts.get(origin)
                if rendered is None:
                    return None
                lowers.append((rendered[0], lower_kind, rendered))
            lowers.sort(key=lambda lower: lower[0])
            extras = _distinct([(lower_kind, rendered) for _text, lower_kind, rendered in lowers])
            return self._step(lowers[0][2], "L", feature, index, extras[1:1 + _EXTRAS])
        return None

    def _step(self, base: tuple[str, str, int], op: str, feature: str, index: int,
              extras: list[tuple[int, tuple[str, str, int]]]) -> tuple[str, str, int]:
        """``base`` and a step feature ``index`` (named ``feature``) took, its further
        sources as atoms, folded where long."""
        text = f"{base[0]};{op}:{feature}" + "".join(f"+{_LETTER[kind]}:{self.atom(rendered)}"
                                                    for kind, rendered in extras)
        held = base[2] | 1 << index
        for _kind, rendered in extras:
            held |= rendered[2]
        return (self.folded(text, feature) if len(text) > FOLD_LENGTH else text), feature, held

    def atom(self, rendered: tuple[str, str, int]) -> str:
        """A rendered derivation as an atom: itself where it is one, else folded."""
        text, feature, _held = rendered
        return text if ";" not in text else self.folded(text, feature)

    def folded(self, text: str, feature: str) -> str:
        """``text`` as an atom: ``feature`` and a digest of it, the table spelling it."""
        atom = f"{feature}~{hashlib.sha256(text.encode()).hexdigest()[:12]}"
        self.table[atom] = text
        return atom

    def distinct(self, texts: list[str | None]) -> list[str | None]:
        """One kind's names of a component, each named alike after the first given the
        next ``;D<n>`` in ordinal order."""
        seen: Counter = Counter()
        found: list[str | None] = []
        for text in texts:
            if text is None:
                found.append(None)
                continue
            seen[text] += 1
            if seen[text] == 1:
                found.append(text)
                continue
            marked = f"{text};D{seen[text]}"
            if len(marked) > FOLD_LENGTH:
                marked = self.folded(marked, _features(text)[-1])
            found.append(marked)
        return found

    def closed(self, names: list[str]) -> dict[str, str]:
        """The table entries element ``names`` reach: every folded atom they or the entries
        name."""
        closed: dict[str, str] = {}
        pending = folded_atoms("\n".join(names))
        while pending:
            reached = pending.pop()
            if reached not in closed:
                closed[reached] = self.table[reached]
                if "~" in closed[reached]:
                    pending.extend(folded_atoms(closed[reached]))
        return dict(sorted(closed.items()))


def _features(text: str) -> list[str]:
    """The features a derivation this module wrote names, in order, its last step's last:
    its atoms' and its steps'."""
    atom, *steps = text.split(";")
    found = [atom.split("~", 1)[0] if "~" in atom else atom.rsplit(".", 1)[0]]
    for step in steps:
        head, *extras = step.split("+")
        found.extend(extra[2:].split("~", 1)[0] if "~" in extra else extra[2:].rsplit(".", 1)[0] for extra in extras)
        if ":" in head:
            found.append(head.split(":", 1)[1])
    return found


def _ranked(rules: list[tuple | None]) -> None:
    """Tell apart, in one result's elements of one kind, those history made alike -- the
    same sources, taken the same way: a ruled loft reports each piece of a long edge as
    generated from every vertex on it -- by their place among what was reported of their
    first source, FreeCAD's index: the second and later are numbered in their step
    (``;Gv2:``), where a later operation leaves the number alone."""
    alike: dict[tuple, list[int]] = {}
    for at, rule in enumerate(rules):
        if rule is not None and rule[0] == "h":
            alike.setdefault(rule[1], []).append(at)
    for members in alike.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda at: (rules[at][3], at))
        for rank, at in enumerate(members, 1):
            _tag, sources, _rank, place = rules[at]
            rules[at] = ("h", sources, rank, place)


def _distinct(named: list[tuple[int, tuple[str, str, int]]]) -> list[tuple[int, tuple[str, str, int]]]:
    """Sources or lower elements, each kind and name once, in order: a name twice adds
    nothing to a name made of it."""
    seen: set[tuple[int, str]] = set()
    found = []
    for kind, rendered in named:
        if (kind, rendered[0]) not in seen:
            seen.add((kind, rendered[0]))
            found.append((kind, rendered))
    return found


def _passes(result: Any, maps: list[Any], table: list[list[tuple | None]]) -> None:
    """The reverse and forward passes over one result's elements (``maps``, by kind),
    naming in ``table`` what identity and history left unnamed (None): an edge of a named
    face, then a vertex of a named edge, from its parents (``u``), each with its number
    among that parent's unnamed elements of its kind; then an edge whose vertices are all
    named, and a face whose outer wire's edges are, from those (``l``). Only the unnamed
    are visited: a parent is found from its child, through the result's map of ancestors."""
    if all(rule is not None for rules in table for rule in rules):
        return

    def named(kind: int, ordinal: int) -> bool:
        rule = table[kind][ordinal - 1]
        return rule is not None and rule[0] != "x"

    for parent_kind, child_kind in ((FACE, EDGE), (EDGE, VERTEX)):
        children = [ordinal for ordinal, rule in enumerate(table[child_kind], 1) if rule is None]
        if not children:
            continue
        ancestors = TopTools_IndexedDataMapOfShapeListOfShape()
        TopExp.MapShapesAndAncestors_s(result, _TOPABS[child_kind], _TOPABS[parent_kind], ancestors)
        # Per parent, each of its unnamed children's number among them, in its own order.
        numbers: dict[int, dict[int, int]] = {}
        candidates: dict[int, list[tuple[int, int, int]]] = {}
        for child in children:
            at = ancestors.FindIndex(maps[child_kind].FindKey(child))
            if not at:
                continue
            parents = sorted({maps[parent_kind].FindIndex(shape) for shape in listed(ancestors.FindFromIndex(at))}
                             - {0})
            for parent in parents:
                if not named(parent_kind, parent):
                    continue
                counted = numbers.get(parent)
                if counted is None:
                    counted = numbers[parent] = {}
                    subs = _map(maps[parent_kind].FindKey(parent), _TOPABS[child_kind])
                    for index in range(1, subs.Extent() + 1):
                        sub = maps[child_kind].FindIndex(subs.FindKey(index))
                        if sub and table[child_kind][sub - 1] is None:
                            counted[sub] = len(counted) + 1
                if child in counted:
                    candidates.setdefault(child, []).append((parent_kind, parent, counted[child]))
        for child, found in candidates.items():
            table[child_kind][child - 1] = ("u", tuple(found))
    for ordinal in range(1, maps[EDGE].Extent() + 1):
        if table[EDGE][ordinal - 1] is None:
            lowers = _lowers(TopExp_Explorer(maps[EDGE].FindKey(ordinal), TopAbs_VERTEX), maps[VERTEX], named, VERTEX)
            if lowers:
                table[EDGE][ordinal - 1] = ("l", tuple((VERTEX, lower) for lower in lowers))
    for ordinal in range(1, maps[FACE].Extent() + 1):
        if table[FACE][ordinal - 1] is None:
            wire = BRepTools.OuterWire_s(TopoDS.Face_s(maps[FACE].FindKey(ordinal)))
            if wire.IsNull():
                continue
            lowers = _lowers(TopExp_Explorer(wire, TopAbs_EDGE), maps[EDGE], named, EDGE)
            if lowers:
                table[FACE][ordinal - 1] = ("l", tuple((EDGE, lower) for lower in lowers))


def _lowers(explorer: Any, elements: Any, named: Callable[[int, int], bool], kind: int) -> list[int] | None:
    """The ordinals of what ``explorer`` visits, each once, when all of them are named."""
    found: list[int] = []
    while explorer.More():
        ordinal = elements.FindIndex(explorer.Current())
        if not ordinal or not named(kind, ordinal):
            return None
        if ordinal not in found:
            found.append(ordinal)
        explorer.Next()
    return found or None


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
    if path.parent == _HERE:
        return "other"
    if any(root in path.parents for root in _OWN):
        return "own"
    return "model" if is_first_party_source_file(path) else "other"


def handle_of(shape: Any) -> Any:
    """A handle copy of the shape's ``TopoDS`` from its own state, or None: an
    empty shape, or a child model's promise or a reference assembly, whose
    property would force what the body only placed."""
    state = getattr(shape, "__dict__", None)
    wrapped = None if state is None else state.get("_wrapped")
    return None if wrapped is None else wrapped.Located(wrapped.Location())


def _map(shape: Any, kind: Any) -> Any:
    found = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, kind, found)
    return found


def _input_index(given: list[tuple[int, list[Any]]], kind: int) -> tuple[Any, list[tuple[int, int]]]:
    """An element's first input and ordinal, under the input maps' ``IsSame`` equality.
    A first input with no name still owns the element; later inputs never replace it.
    Kept only while one feature's structure is computed, shared by all its results."""
    elements = TopTools_IndexedMapOfShape()
    owners: list[tuple[int, int]] = []
    for j, held in given:
        source = held[kind]
        for ordinal in range(1, source.Extent() + 1):
            at = elements.Add(source.FindKey(ordinal))
            if at > len(owners):
                owners.append((j, ordinal))
    return elements, owners


def _relative(element: Any, shape: Any) -> Any:
    """``element`` (of a shape ``shape`` is part of, in that shape's frame) in
    ``shape``'s own frame."""
    location = shape.Location()
    return element if location.IsIdentity() else element.Moved(location.Inverted())


def _transform(location: Any) -> tuple[int, ...]:
    """A placement rounded as positions and directions are: only to tell places apart."""
    transform = location.Transformation()
    return tuple(int(round(transform.Value(row, column) / (_POSITION_QUANTUM if column == 4 else _DIRECTION_QUANTUM)))
                 for row in (1, 2, 3) for column in (1, 2, 3, 4))
