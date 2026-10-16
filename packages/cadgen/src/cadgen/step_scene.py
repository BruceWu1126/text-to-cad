"""Saved STEP readers and exact, revision-scoped scene selection.

``read_step`` returns native build123d geometry. ``read_scene`` retains the
occurrence hierarchy and the document's refs (occurrence tokens, element
names: README law 20); geometry is acquired only when requested. Neither reader
discovers or executes source scripts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator, Literal

if TYPE_CHECKING:
    from build123d import Shape
    from cadgen._internal.step_scene_types import LoadedStepScene

__all__ = ["read_step", "read_scene", "StepScene", "Occurrence", "Selection"]

EntityKind = Literal["face", "edge", "vertex"]
_PLURAL = {"face": "faces", "edge": "edges", "vertex": "vertices"}


def _resolve_input(step_path: Path | str, *, reader: str) -> Path:
    """Resolve a STEP a model asked for: it must exist, and must not be an
    output of the model reading it. The build's trace sees the read itself.
    """
    resolved = Path(step_path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(
            f"{reader}: no STEP file at {resolved}. Check the path — it resolves "
            "relative to the process's working directory, so anchor a model's own "
            "inputs on its file: Path(__file__).parent / '../STEP/part.step'."
        )
    from cadgen._internal.self_input import refuse_own_output

    refuse_own_output(resolved, reader=reader)
    return resolved


def read_scene(step_path: Path | str) -> StepScene:
    """Open one saved STEP revision, using its canonical native cache.

    A cache miss compiles the document, never its source. No tessellation or
    display derivation is required. STEP coordinates are in mm.
    Keep this scene open for repeated queries; calling again reads/hashes the
    document again so a replacement cannot masquerade as the old revision.
    """
    from cadgen._internal.doors import STEP_SUFFIXES, document_target

    path = _resolve_input(step_path, reader="read_scene")
    document_target(path, suffixes=STEP_SUFFIXES)
    from cadgen._internal.step_scene_package import load_step_scene_cached

    return StepScene(load_step_scene_cached(path, lazy=True))


@dataclass(frozen=True)
class Selection:
    """An exact reference within one scene; ``shape()`` returns an owned copy."""

    _scene: StepScene = field(repr=False)
    occurrence_ref: str
    kind: Literal["occurrence", "face", "edge", "vertex"]
    _ordinal: int | None = None

    @property
    def ref(self) -> str:
        """The ref that names this selection: ``#<occurrence token>``, or
        ``#<occurrence token>/<element name>`` for a face, edge or vertex."""
        if self.kind == "occurrence":
            return self.occurrence_ref
        return f"{self.occurrence_ref}/{self._scene._name(self)}"

    def shape(self) -> Shape:
        """Native geometry in world coordinates, privately owned by the caller.

        An occurrence group is an unfused compound of its descendants.
        Modifying the result cannot change this scene or another result.
        """
        from cadgen.geometry import _cast, _copy

        result = _cast(_copy(self._scene._placed(self)))
        occurrence = self._scene._occurrences[self.occurrence_ref]
        result.label = occurrence.label or ""
        color = occurrence._node.color
        if color is not None:
            result.color = color
        return result

    def entities(self, kind: EntityKind) -> Iterator[Selection]:
        """Enumerate the faces, edges or vertices inside this selection.

        Each carries its own ref: the occurrence's token and the element's name.
        Asking a face for edges gives the leaf's edges that bound it.
        """
        if kind not in _PLURAL:
            raise ValueError(f"unknown entity kind: {kind!r}")
        yield from self._scene._entities(self, kind)


@dataclass(frozen=True)
class Occurrence(Selection):
    _node: Any = field(default=None, repr=False, compare=False)

    @property
    def label(self) -> str | None:
        return self._node.name or self._node.source_name

    @property
    def prototype_id(self) -> str | None:
        """Shared geometry identity within this scene; None for a group."""
        return self._scene._prototype_ids.get(self._node.prototype_key)

    @property
    def children(self) -> tuple[Occurrence, ...]:
        return tuple(self._scene._occurrences[_node_ref(node)] for node in self._node.children)


def _node_ref(node) -> str:
    return "#" + str(node.id)


class StepScene:
    """A saved document revision with immutable occurrence and selector views.

    References name the document's geometry: an occurrence by the token of its
    place in the document's tree, an element by its name -- its lineage, the
    feature of the model's run that made it, or, where it has none, the geometric
    token of its geometry within its body -- so a ref survives a rebuild that keeps
    the construction. A ref in token form also finds the element of a body named
    by lineage whose geometry it names. The scene does not apply kinematics/animation
    poses.
    """

    def __init__(self, scene: LoadedStepScene):
        from cadgen.label_refs import build_label_aliases

        self._loaded = scene
        if any(node.id is None for node in self._walk(scene.roots)):
            from cadgen._internal.step_scene_package import scene_tokens

            scene_tokens(scene)
        self._occurrences: dict[str, Occurrence] = {}
        self._maps: dict[tuple[Any, str], Any] = {}
        self._names: dict[Any, dict] = {}
        self._tokens: dict[Any, dict] = {}
        self._prototype_ids = {key: f"p{i}" for i, key in enumerate(scene.prototype_shapes, 1)}
        for node in self._walk(scene.roots):
            ref = _node_ref(node)
            self._occurrences[ref] = Occurrence(self, ref, "occurrence", _node=node)
        self._leaves = tuple(o for o in self._occurrences.values() if o._node.prototype_key is not None)
        self._aliases = build_label_aliases(
            {"id": o.ref[1:], "name": o.label} for o in self._occurrences.values()
        )

    @staticmethod
    def _walk(roots) -> Iterator[Any]:
        stack = list(reversed(roots))
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(node.children))

    @property
    def document_hash(self) -> str:
        return self._loaded.step_hash

    @property
    def roots(self) -> tuple[Occurrence, ...]:
        return tuple(self._occurrences[_node_ref(node)] for node in self._loaded.roots)

    def leaves(self) -> Iterator[Occurrence]:
        """Placed geometry occurrences; a leaf may contain several solids."""
        return iter(self._leaves)

    def resolve(self, ref: str) -> Selection:
        """Resolve one ref: an occurrence token, an element ref, an exact label alias,
        or a file-prefixed ref.

        A file prefix -- the path under the viewer's root, or an absolute path --
        must name this scene's document as a path does (links resolved, a relative
        one read from the working directory, else matched against the end of the
        document's path).
        Ambiguous labels raise with numbered candidates. A bare element name
        (``#f:…``) requires exactly one leaf. No fuzzy matching or first match.
        """
        from cadgen.cad_ref_syntax import parse_selector, ref_prefix_names, split_cad_ref
        from cadgen.label_refs import resolve_label_selectors

        prefix, text = split_cad_ref(ref)
        if prefix and not ref_prefix_names(prefix, str(self._loaded.step_path)):
            raise ValueError(f"reference names {prefix!r}, but this scene is {str(self._loaded.step_path)!r}")
        parsed = parse_selector(text)
        if parsed is None or parsed.selector_type == "opaque":
            raise ValueError(f"invalid reference: {ref!r}; pass one occurrence or element ref")
        if parsed.label:
            text = resolve_label_selectors([text], self._aliases)[0]
            parsed = parse_selector(text)
        oid = "#" + parsed.occurrence_id if parsed.occurrence_id else ""
        if not oid:
            if len(self._leaves) != 1:
                raise ValueError(f"{ref!r} needs an occurrence token in an assembly")
            oid = self._leaves[0].ref
        occurrence = self._occurrences.get(oid)
        if occurrence is None:
            raise ValueError(f"unknown occurrence: {oid}")
        if parsed.selector_type == "occurrence":
            return occurrence
        kind = parsed.selector_type
        if occurrence._node.prototype_key is None:
            raise ValueError(f"{oid} is a group; enumerate its entities or specify a leaf occurrence")
        names = self._element_names(occurrence)[_PLURAL[kind]]
        if parsed.element in names:
            return Selection(self, oid, kind, names.index(parsed.element) + 1)
        from cadgen._internal.geometric_tokens import TOKEN_RE

        if TOKEN_RE.fullmatch(parsed.element):
            # A geometric token (copied from a file read, or before its element had a name)
            # matches an element by its geometry, whatever names it now.
            tokens = self._element_tokens(occurrence)[_PLURAL[kind]]
            if parsed.element in tokens:
                return Selection(self, oid, kind, tokens.index(parsed.element) + 1)
        elif (self._loaded.prototype_identity.get(occurrence._node.prototype_key) or (None, None))[1] is None:
            # Names live in the store of the build that wrote the file, not in the file: read
            # with another store, its elements are named by their geometry.
            raise ValueError(
                f"unknown {kind} reference: {ref!r}: the {_PLURAL[kind]} of {oid} are named by their geometry here, "
                "since no build this store holds wrote these bytes; a name resolves with the store of the build "
                "that wrote the file, or after building its model again")
        raise ValueError(f"unknown {kind} reference: {ref!r} ({len(names)} {_PLURAL[kind]} in {oid})")

    def _element_names(self, occurrence) -> dict:
        """The names of the occurrence's prototype's elements by ordinal: its lineage's,
        and the geometric token of each element no lineage names, measured once per
        prototype, only when one has none (``geometric_tokens.element_names``)."""
        key = occurrence._node.prototype_key
        if key not in self._names:
            from cadgen._internal.geometric_tokens import element_names

            self._names[key] = element_names((self._loaded.prototype_identity.get(key) or (None, None))[1],
                                             self._loaded.prototype_shapes[key],
                                             component=self._loaded.prototype_components.get(key))
        return self._names[key]

    def _element_tokens(self, occurrence) -> dict:
        """The geometric tokens of the occurrence's prototype's elements by ordinal: the
        fallback matcher, measured the first time a ref asks for it and remembered by the
        stored component's bytes (``geometric_tokens.component_tokens``)."""
        key = occurrence._node.prototype_key
        if key not in self._tokens:
            from cadgen._internal.geometric_tokens import component_tokens

            self._tokens[key] = component_tokens(self._loaded.prototype_shapes[key],
                                                 self._loaded.prototype_components.get(key))
        return self._tokens[key]

    def _name(self, selection: Selection) -> str:
        occurrence = self._occurrences[selection.occurrence_ref]
        return self._element_names(occurrence)[_PLURAL[selection.kind]][selection._ordinal - 1]

    def _table(self, occurrence, kind):
        from cadgen._internal.entity_ordinals import entity_map

        key = (occurrence._node.prototype_key, kind)
        if key not in self._maps:
            self._maps[key] = entity_map(self._loaded.prototype_shapes[key[0]], kind)
        return self._maps[key]

    def _local(self, selection):
        occurrence = self._occurrences[selection.occurrence_ref]
        if selection.kind == "occurrence":
            return self._loaded.prototype_shapes[occurrence._node.prototype_key]
        return self._table(occurrence, selection.kind).FindKey(selection._ordinal)

    def _placed(self, selection):
        occurrence = self._occurrences[selection.occurrence_ref]
        if occurrence._node.prototype_key is None:
            from OCP.BRep import BRep_Builder
            from OCP.TopoDS import TopoDS_Compound

            compound, builder = TopoDS_Compound(), BRep_Builder()
            builder.MakeCompound(compound)
            for child in occurrence.children:
                builder.Add(compound, self._placed(child))
            return compound
        local = self._local(selection)
        location = occurrence._node.location
        return local.Moved(location) if location is not None else local

    def _entities(self, selection, kind):
        from cadgen._internal.entity_ordinals import entity_map

        occurrence = self._occurrences[selection.occurrence_ref]
        if occurrence._node.prototype_key is None:
            for child in occurrence.children:
                yield from self._entities(child, kind)
            return
        table = self._table(occurrence, kind)
        if selection.kind == "occurrence":
            ordinals = range(1, table.Extent() + 1)
        elif selection.kind == kind:
            ordinals = [selection._ordinal]
        else:
            subset = entity_map(self._local(selection), kind)
            ordinals = [table.FindIndex(subset.FindKey(i)) for i in range(1, subset.Extent() + 1)]
        for ordinal in ordinals:
            yield Selection(self, occurrence.ref, kind, ordinal)


def read_step(step_path: Path | str, *, label: str | None = None) -> Any:
    """Read a STEP file as build123d geometry.

    Usable in a ``@step`` body (composing a vendor part into an assembly) and in
    a ``@dxf`` body (deriving a cut profile from one) alike. The returned shape
    is the file's geometry, as ``build123d.import_step`` would read it — the
    root itself, not a wrapper — with per-occurrence and prototype STEP colors
    applied. When the store holds a tree for the file's bytes the read is warm
    (tens of milliseconds instead of a full text-STEP parse), and it is the
    SAME geometry: a model's tree is built from the STEP it wrote, re-read, so a
    warm read, a cold parse and ``import_step`` agree by construction
    (``cadgen.store.build.build_tree_through_step``).

    Inside a build the file is an input, as every file a build reads is, and a
    model's own output is refused. A missing file raises here rather than deep inside the importer, because
    "the vendor STEP is not where the model thinks it is" is the whole message.
    """
    from cadgen._internal.step_scene import import_step

    return import_step(_resolve_input(step_path, reader="read_step"), label=label)
