"""Build a model's TREE from its returned geometry.

Walks the compound a model returned. Every leaf becomes a content-addressed
**component** (encoded ``.brep`` plus an intrinsic face-color recipe); every
subtree that is a child model's materialized geometry, found intact, becomes a
**link** to that child's tree. The decision is mechanical (§Tree in STORE.md):

- a materialized child with its original native partner, geometry and
  descendant occurrence metadata → link at its world location; root label
  and color overrides are still intact;
- anything else — geometry the model made, an extracted sub-shape, a modified
  child (``housing() - holes``: a new TShape), a mirrored child (a new TShape)
  → the model's own components. No error path.

Each native component is privately decoded and validated before publication.
A part's display data is a separate artifact derivation and never delays native
geometry readiness. A component without an admitted native codec explicitly
pins its topology facts; no later producer receives the original live shape.
A component the model's run made carries what the run names it by -- its
construction key and its lineage (``kernel_memo``, ``kernel_trace``) -- and
nothing on the way measures its geometry for a name.

A worker remembers the components it published (:class:`_Remembered`), in its
memory and never the store, by the live kernel object each was prepared from: the
TShape the memo hands back on a hit, its orientation and face colours. A later
publication of that object takes the remembered component -- its bytes, colours,
construction key and lineage -- in place of naming, serializing and hashing it
again, and only while all it stood on is as it was: the construction key the run
names it by, the rows the memo's ledger holds on each of its sub-shapes
(``kernel_memo.ledger_snapshot``: every change the memo made to one since its maker
returned it, flags included) with no change the memo could not replay met since,
and what naming it reads of the run (``kernel_trace.account_basis``: the features it
descends from, by their histories, and the labels and names they have). Anything
else prepares it as a fresh worker does, so a remembered component never changes
an output.

A model's result is this tree, whether it declares STEP or only meshes: its STEP is
written from the published tree afterwards, by the build pool (``store/exports.py``),
and the written bytes map to it. A compile of a STEP's bytes (:func:`build_document_tree`)
builds the document's own tree: its structure, names and placements as the file has
them, every component read from the file.
"""

from __future__ import annotations

import copy
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol

from cadgen._internal.tree_tokens import is_occurrence_token
from cadgen.coordination import PHASE_COMPONENTS, PHASE_FINALIZE, PHASE_PACKAGE
from cadgen.coordination import resolve as resolve_progress
from cadgen.store.index import write_entry
from cadgen.store.materialize import (
    PARTNER_TAG,
    ROOT_LOC_TAG,
    TREE_TAG,
    HeldDigests,
    _ComponentIdentity,
    _location_from_matrix,
    materialized_children,
    subshape_hashes,
)
from cadgen.store.objects import claim_object, put_object, read_verified_object
from cadgen.store.trees import COMPONENT_OBJECTS, put_tree


class Namer(Protocol):
    """What a build names the components it prepares by, and tells the calls that made
    them by: the model's run (``kernel_memo._Run``), whose trace and registrations say
    whether a component it remembers publishing is still named as it was."""

    trace: Any

    def account(self, shape: Any) -> tuple[str | None, Mapping[str, Any] | None, Mapping[str, int]]:
        """A component's construction key and lineage, either None, and the feature of the
        run each feature name of the lineage stands for."""

    def placed_by(self, shape: Any) -> int | None:
        """The feature whose call made the shape a leaf places, or None."""

    def made_by(self, tshape: Any) -> int | None:
        """The feature of the run that made ``tshape``, or None."""


def _tagged_intact(node: Any, held: HeldDigests | None = None) -> str | None:
    """The tree hash when ``node`` is a materialized child still carrying the
    geometry it was materialized with (placement may differ), else None. ``held`` is
    the publication's reading of the kernel memo its checks share (``HeldDigests``)."""
    tag = getattr(node, TREE_TAG, None)
    if not tag:
        return None
    holder = getattr(node, PARTNER_TAG, None)
    intact = getattr(holder, "intact", None)
    if intact is None:
        return None
    try:
        return str(tag) if intact(node, held=held) else None
    except Exception:  # noqa: BLE001 - an odd wrapper is not intact
        return None


def compound_has_children(shape: Any) -> bool:
    """Whether ``shape`` is a Compound placing children — the ONE fact packaging
    turns on. A Compound with children becomes occurrences (one per child, links
    where a child is an intact model result); anything else is one component.
    Nothing but the shape itself decides this: no declaration, no inference from
    source."""
    try:
        if tuple(getattr(shape, "children", ()) or ()):
            return True
    except TypeError:
        pass
    wrapped = getattr(shape, "wrapped", None)
    if wrapped is None:
        return False
    try:
        from OCP.TopAbs import TopAbs_COMPOUND
        from OCP.TopoDS import TopoDS_Iterator

        if wrapped.ShapeType() != TopAbs_COMPOUND:
            return False
        iterator = TopoDS_Iterator(wrapped)
        count = 0
        while iterator.More():
            count += 1
            if count > 1:
                return True
            iterator.Next()
    except Exception:  # noqa: BLE001 - an odd wrapper packages as one component
        return False
    return False


@dataclass
class _Walk:
    """A compound walked into tree parts, before anything is published: own
    occurrences (placing ``components`` by cid), links to children's trees, the
    grouping root, and per cid the shape that owns the geometry and its prepared
    component (``prepare_geometry_component``'s, or one this worker remembered)."""

    occurrences: list[dict[str, Any]] = field(default_factory=list)
    links: list[dict[str, Any]] = field(default_factory=list)
    components: dict[str, dict[str, Any]] = field(default_factory=dict)
    shapes: dict[str, Any] = field(default_factory=dict)
    prepared: dict[str, dict[str, Any]] = field(default_factory=dict)
    native_locations: dict[str, Any] = field(default_factory=dict)
    root: dict[str, Any] = field(default_factory=dict)
    # What the model's run says of what it made (``Namer``): per component the walk
    # prepared from its shapes, the feature each name of its lineage stands for (its
    # first leaf's); per occurrence of one, the feature whose call made the shape it
    # places. The build's provenance (STORE.md §3).
    names: dict[str, dict[str, int]] = field(default_factory=dict)
    placed: dict[str, int | None] = field(default_factory=dict)
    # The components this worker remembered publishing (``_recall``), and those it
    # prepared that it remembers once they are published (``_admit``); what checking a
    # remembered one reads, taken once per walk: the memo's ledger, the run's features
    # by history, each feature's and each part's naming restated (``account_basis``).
    recalled: set[str] = field(default_factory=set)
    pending: list[tuple[tuple, "_Remembered"]] = field(default_factory=list)
    ledger: dict[int, tuple] | None = None
    ledger_keys: set[int] | None = None
    unreplayed: int | None = None
    places: dict[int, int] | None = None
    bases: dict[Any, Any] = field(default_factory=dict)
    # Per closure this walk restated (by identity): the remembered one found equal to it,
    # and the identities of its histories.
    closures: dict[int, tuple] = field(default_factory=dict)
    held: dict[int, frozenset[int]] = field(default_factory=dict)

    def prototype(self, cid: str) -> Any:
        """The private native shape component ``cid`` was prepared from: a remembered
        native component's is decoded from its bytes, once, as its preparation decoded it."""
        prepared = self.prepared.get(cid)
        if prepared is None:
            return None
        if prepared["shape"] is None and cid in self.recalled and prepared["entry"]["kind"] == "native":
            from cadgen._internal.component_package import decoded_input

            prepared["shape"] = decoded_input(prepared["entry"], prepared["payload"])
        return prepared["shape"]

    def draft_tree(self, *, root_name: str) -> dict[str, Any]:
        """The tree these parts describe, minus the object hashes publishing adds."""
        return {
            "label": root_name,
            "units": "mm",
            "components": self.components,
            "occurrences": self.occurrences,
            "links": self.links,
            "assembly": {"root": self.root},
        }

    def tokenize(self, *, root_name: str) -> dict[str, str]:
        """Name every node by its token (``tree_tokens``), replacing the provisional
        positional ids the walk assigned, and return the mapping provisional -> token.

        A link to a part child is tokened as the one leaf it is once flattened:
        the link's name at the child's root occurrence composed with the link's
        placement -- what ``trees.flatten`` and the saved document name it by."""
        from cadgen._internal.tree_tokens import assign_tokens, retoken
        from cadgen.store.trees import compose_transforms, get_tree

        link_leaf: dict[str, Any] = {}
        for link in self.links:
            child = get_tree(str(link["tree"]))
            if child is None:
                raise FileNotFoundError(f"linked tree object missing: {link['tree']}")
            root = child["assembly"]["root"]
            if not root.get("children"):
                placed = next((row for row in child["occurrences"] if row["id"] == root["id"]), None)
                if placed is not None:
                    link_leaf[str(link["id"])] = compose_transforms(link["transform"], placed["transform"])
        mapping = assign_tokens(self.root, self.occurrences, self.links, label=root_name, link_leaf=link_leaf)
        tree = retoken({"occurrences": self.occurrences, "links": self.links, "assembly": {"root": self.root}}, mapping)
        self.occurrences[:] = tree["occurrences"]
        self.links[:] = tree["links"]
        self.root = tree["assembly"]["root"]
        self.native_locations = {mapping[key]: value for key, value in self.native_locations.items()}
        self.placed = {mapping[key]: value for key, value in self.placed.items()}
        return mapping


def build_tree_from_compound(
    compound: Any,
    *,
    root_name: str,
    force: bool = False,
    progress: Any | None = None,
    materials: object = None,
    namer: Namer | None = None,
    live: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Return ``(tree_hash, tree, stats)``. The tree's edge classes, the content-pure
    fields it carries beside its structure, are classified from the topology it is built
    from (:func:`_edge_rendering`) — never from a document of it.
    ``namer``, the model's run, names each component the walk prepares from a shape
    the run made -- its construction key and lineage -- and says which call made each
    leaf's shape: ``stats["account"]`` (``{"components", "occurrences"}``) is what the
    build's provenance records of them (``Namer``).
    ``live``, when given, is filled with the shape, unlocated, of every component
    the store cannot decode (an eager-only one's), by cid: what the build's own
    process writes its STEP with (``store/exports.py``).

    Packaging follows the RETURN VALUE: a Compound with children is walked into
    occurrences; a single shape is one component (``compound_has_children``).
    The tree's ``entryKind`` is then read off the tree (``tree_kind``).

    The components are the shapes as returned: the tree of every model's result,
    whose STEP, when it declares one, is written from it (``store/exports.py``).
    The build owns the exact bytes of what it publishes here, so a damaged copy of
    any of them in the store is written again."""
    progress = resolve_progress(progress)
    walk = _walk_compound(compound, root_name=root_name, progress=progress, namer=namer)
    root_name = walk.root["name"]  # as a reader of its file reads it
    from cadgen._internal.source_sidecar import resolve_materials
    from cadgen.store.trees import flatten_tree

    descriptor = flatten_tree(walk.draft_tree(root_name=root_name))
    inherited_appearance = descriptor.get("appearance")
    appearance = resolve_materials(descriptor, materials, inherited=inherited_appearance)
    if live is not None:
        from build123d import Location

        live.update({cid: walk.shapes[cid].located(Location()) for cid, entry in walk.components.items()
                     if entry.get("kind") == "eager-only" and cid in walk.shapes})
    return _publish_tree(
        walk, bbox_shape=compound, root_name=root_name, force=force, progress=progress,
        extra=_edge_rendering(descriptor, walk), appearance=appearance, base_appearance=inherited_appearance,
        authored_occurrence_bounds=True, repair_objects=True,
    )


def _edge_rendering(descriptor: dict[str, Any], walk: _Walk) -> dict[str, Any]:
    """The content-pure fields a result tree carries for its edges, ``capabilities`` and
    ``edgeRendering``: the classes a view draws, classified by the adaptive edge policy
    (``step_scene_mesh.edge_visibility_classes``) from the flattened result's topology --
    each distinct component's facts, remembered by its BREP
    (``bounds.cached_component_topology``) and measured from the walk's own prototype, or
    from the stored component decoded once, only where the index lacks them -- and each
    occurrence's placement. No document of the result is assembled for it."""
    from build123d import Location

    from cadgen._internal.component_package import decode_geometry_component
    from cadgen._internal.glb_topology import step_edge_capabilities
    from cadgen._internal.step_scene_mesh import edge_visibility_classes, prototype_topology, topology_hints
    from cadgen.store.bounds import cached_component_topology

    def measured(cid: str, entry: dict[str, Any]) -> dict[str, Any]:
        prototype = walk.prototype(cid)
        if prototype is None and cid in walk.shapes:
            prototype = walk.shapes[cid].located(Location())
        if prototype is None:
            prototype = decode_geometry_component(entry, read_verified_object(entry["brep"]))
        return prototype_topology(prototype.wrapped)

    topologies = {cid: cached_component_topology(str(entry["codec"]), str(entry["brep"]),
                                                 lambda cid=cid, entry=entry: measured(cid, entry))
                  for cid, entry in descriptor["components"].items()}
    classes = edge_visibility_classes(topology_hints(
        topologies, ((str(row["component"]), row["transform"]) for row in descriptor["occurrences"])))
    return {"capabilities": step_edge_capabilities(classes), "edgeRendering": {"visibilityClasses": list(classes)}}


def _document_walk(scene: Any) -> _Walk:
    """Walk parsed STEP products into the document's structure, with no filename or
    authored-result input, and no component yet: each leaf occurrence holds the
    parser's node (``_node``, its prototype and placement) until
    :func:`_attach_components` gives it one.

    A product holding a native compound remains that product, rather than
    acquiring another grouping from a reconstructed Python wrapper. Canonical
    ids are tokens minted from the document's own names and placements
    (``_Walk.tokenize``), several free roots grouped under a synthetic root. A
    product the STEP gives no name is ``part`` (a leaf), ``group`` (a product with
    children) or ``model`` (the root), and a file of one placed part is that part
    (``step_scene_loader.document_roots``).
    """
    from cadgen._internal.step_scene_loader import _selector_id, document_roots

    walk = _Walk()
    paths: set[str] = set()

    def node_name(node: Any, default: str) -> str:
        return str(node.name or node.source_name or default)

    def collect(node: Any, occurrence_id: str) -> dict[str, Any]:
        parsed_id = _selector_id(node.path)
        if parsed_id in paths:
            raise RuntimeError(f"STEP has duplicate product path {parsed_id}")
        paths.add(parsed_id)
        name = node_name(node, "group" if node.children else "part")
        if node.children:
            children = [collect(child, f"{occurrence_id}.{index}")
                        for index, child in enumerate(node.children, start=1)]
            leaves = [leaf for child in children for leaf in child["leafPartIds"]]
            return {"id": occurrence_id, "name": name, "nodeType": "subassembly",
                    "leafPartIds": leaves, "children": children}
        key = node.prototype_key
        if key is None or key not in scene.prototype_shapes:
            raise RuntimeError(f"STEP product {parsed_id} has no geometry")
        occurrence = {"id": occurrence_id, "name": name, "_node": node,
                      "transform": [float(value) for value in node.transform]}
        color = node.color if node.color is not None else scene.prototype_colors.get(key)
        if color is not None:
            occurrence["color"] = [float(c) for c in color]
        walk.occurrences.append(occurrence)
        # Publication-only native placement. Canonical bounds use this exact
        # location rather than reconstructing one from the serialized matrix,
        # which can re-orthogonalize rotations by a few ulps.
        walk.native_locations[occurrence_id] = node.location
        return {"id": occurrence_id, "name": name, "nodeType": "part",
                "leafPartIds": [occurrence_id], "children": []}

    if not scene.roots:
        raise RuntimeError("STEP has no product roots")
    roots = document_roots(scene.roots)
    if len(roots) == 1:
        walk.root = collect(roots[0], "o1")
        if walk.root["children"]:
            walk.root["nodeType"] = "assembly"
            walk.root["name"] = node_name(roots[0], "model")
    else:
        children = [collect(node, f"o1.{index}") for index, node in enumerate(roots, start=1)]
        walk.root = {"id": "o1", "name": "model", "nodeType": "assembly", "children": children,
                     "leafPartIds": [leaf for child in children for leaf in child["leafPartIds"]]}
    if not walk.occurrences:
        raise RuntimeError("STEP has no leaf geometry")
    walk.tokenize(root_name=walk.root["name"])
    return walk


def _attach_components(walk: _Walk, scene: Any, *, progress: Any, force: bool = False) -> Callable[[], Any]:
    """Give each leaf of a document walk its component, and return how to place
    them as the document places them: the shape an unmeasured document's bounds are
    taken of. Each leaf takes its parsed prototype, named as the stored component a
    scene read from a tree says it was (``prototype_identity``), whose exact encoded
    bytes are not decoded again when they are already published under the same
    native entry (``prepare_published_component``); a forced build derives every one.
    """
    from build123d import Compound

    from cadgen._internal.component_package import (
        _build123d_shape_from_topods, _component_id, _shape_brep_bytes, prepare_geometry_component,
        prepare_published_component, _normalized_face_colors,
    )
    from cadgen._internal.step_scene_mesh import _face_colors_by_ordinal, scene_occurrence_shape

    parsed_cids: dict[Any, str] = {}
    placed: list[Callable[[], Any]] = []
    progress.phase(PHASE_PACKAGE, total=len(walk.occurrences))
    for occurrence in walk.occurrences:
        node = occurrence.pop("_node")
        key = node.prototype_key
        placed.append(lambda node=node: scene_occurrence_shape(scene, node))
        cid = parsed_cids.get(key)
        if cid is None:
            prototype = scene.prototype_shapes[key]
            raw_colors = scene.prototype_face_colors.get(key)
            face_colors = _normalized_face_colors(
                _face_colors_by_ordinal(prototype, raw_colors) if raw_colors else None)
            identity = scene.prototype_identity.get(key)
            prepared = (prepare_geometry_component(prototype, face_colors=face_colors, identity=identity) if force
                        else prepare_published_component(prototype, face_colors=face_colors, identity=identity,
                                                         payload=_shape_brep_bytes(prototype)))
            cid = _component_id(prepared["entry"]["contentHash"])
            parsed_cids[key] = cid
            if cid not in walk.shapes:
                shape = _build123d_shape_from_topods(prototype)
                if face_colors:
                    shape.cad_face_ordinal_colors = face_colors
                walk.shapes[cid] = shape
                walk.prepared[cid] = prepared
                meta: dict[str, Any] = dict(prepared["entry"])
                color = scene.prototype_colors.get(key)
                if color is not None:
                    meta["color"] = [float(c) for c in color]
                walk.components[cid] = meta
        occurrence["component"] = cid
        progress.advance(detail=occurrence["name"])
    return lambda: Compound(children=[_build123d_shape_from_topods(place()) for place in placed])


def build_document_tree(
    scene: Any, *, force: bool = False, progress: Any | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Canonical ``(digest, tree, stats)`` from a parsed STEP scene: the compile of a
    document's bytes.

    The scene must describe the saved bytes. No source name, filename, model
    record, appearance annotation or author options enter this tree: its
    structure and every component are the file's.
    This writes immutable objects and component indexes, never document indexes.
    A compile miss can follow an unreadable indexed object, so canonical
    publication verifies reuse and atomically repairs damaged derived bytes.
    """
    from cadgen._internal.glb_topology import (
        STEP_EDGE_DEFAULT_RENDER_VISIBILITY_CLASSES, step_edge_capabilities,
    )

    progress = resolve_progress(progress)
    walk = _document_walk(scene)
    placed = _attach_components(walk, scene, progress=progress, force=force)
    return _publish_tree(
        walk, bbox_shape=None, root_name=walk.root["name"], force=force, progress=progress,
        extra={"capabilities": step_edge_capabilities(),
               "edgeRendering": {"visibilityClasses": list(STEP_EDGE_DEFAULT_RENDER_VISIBILITY_CLASSES)}},
        repair_objects=True, prepared_occurrence_bounds=True, prepare_bbox_shape=placed,
    )


def _own_name(label: object, default: str) -> str:
    """A node's name in the tree being walked: its label as a reader of a file written
    from the tree reads it (``step_scene_loader._normalize_label_name``: runs of
    whitespace as one space, a shape kind, digits alone or a blank as no name), unless
    that is an occurrence token. A name enters the node's token, so a result and the
    files written from it name every node alike. A label that is a token came from a
    tree, not an author (a node of a scene read from a document's tree, or of a
    materialized tree, carries one), and a token names nothing at its place here.
    ``default`` is what an
    unlabelled node is called: ``part`` for a leaf, ``group`` for a group -- never
    anything positional."""
    from cadgen._internal.step_scene_loader import _normalize_label_name

    text = _normalize_label_name(label) or ""
    return default if not text or is_occurrence_token(text) else text


def _walk_compound(compound: Any, *, root_name: str, progress: Any, namer: Namer | None = None) -> _Walk:
    from build123d import Location

    single_component = not compound_has_children(compound)

    from cadgen._internal.component_package import (
        _component_id,
        prepare_geometry_component,
        _normalized_face_colors,
        _occurrence_color,
        _occurrence_material,
        _transform_from_location,
    )

    walk = _Walk()
    occurrences = walk.occurrences
    links = walk.links
    components = walk.components
    shapes = walk.shapes
    hash_memo: dict[Any, str] = {}

    def _add_leaf(
        node: Any,
        world_loc: Any,
        occ_id: str,
        name: str | None = None,
        identity: _ComponentIdentity | None = None,
    ) -> dict[str, Any]:
        face_colors = _normalized_face_colors(getattr(node, "cad_face_ordinal_colors", None))
        if identity is not None:
            try:
                from cadgen._internal.component_package import validate_geometry_component
                validate_geometry_component(identity.entry(), read_verified_object(identity.brep), cid=identity.cid)
            except (OSError, ValueError, TypeError):
                # A lost or damaged pin cannot certify newly serialized geometry.
                identity = None
        if identity is not None:
            cid = identity.cid
            entry_meta = identity.entry()
        else:
            try:
                memo_key = (node.wrapped.TShape(), int(node.wrapped.Orientation()), tuple(face_colors.items()))
                cid = hash_memo.get(memo_key)
            except TypeError:
                memo_key, cid = None, None
            if cid is None:
                # A component this worker published before, still what it was, stands in.
                recalled = _recall(walk, node, memo_key, namer) if namer is not None and memo_key is not None else None
                if recalled is not None:
                    prepared, names = recalled
                else:
                    construction, lineage, names = namer.account(node) if namer is not None else (None, None, {})
                    prepared = prepare_geometry_component(
                        node, face_colors=face_colors,
                        identity=(construction, lineage) if namer is not None else None)
                entry_meta = dict(prepared["entry"])
                cid = _component_id(entry_meta["contentHash"])
                previous = walk.prepared.setdefault(cid, prepared)
                if previous["entry"]["contentHash"] != entry_meta["contentHash"]:
                    raise ValueError("short component ID collision")
                if recalled is None and namer is not None and memo_key is not None:
                    _remember(walk, node, memo_key, namer, construction, names, prepared)
                elif recalled is not None and previous is prepared:
                    walk.recalled.add(cid)
                if prepared["lineage"] is not None:
                    walk.names.setdefault(cid, dict(names))
                if memo_key is not None:
                    hash_memo[memo_key] = cid
            else:
                entry_meta = dict(walk.prepared[cid]["entry"])
        shapes.setdefault(cid, node)
        if namer is not None and cid in walk.names:
            walk.placed[occ_id] = namer.placed_by(node)
        node_color = getattr(node, "color", None)
        if node_color is not None:
            try:
                entry_meta["color"] = [float(c) for c in node_color.to_tuple()]
            except Exception:  # noqa: BLE001
                pass
        components.setdefault(cid, entry_meta)
        name = _own_name(getattr(node, "label", "") if name is None else name, "part")
        occurrence: dict[str, Any] = {
            "id": occ_id,
            "name": name,
            "component": cid,
            "transform": _transform_from_location(world_loc),
        }
        color = _occurrence_color(node)
        if color is not None:
            occurrence["color"] = color
        material = _occurrence_material(node)
        if material is not None:
            occurrence["material"] = material
            material_id = getattr(node, "_cadgen_material_id", None)
            if isinstance(material_id, str) and material_id:
                occurrence["materialId"] = material_id
        occurrences.append(occurrence)
        progress.advance(detail=name)
        return {"id": occ_id, "name": name, "nodeType": "part", "leafPartIds": [occ_id], "children": []}

    def _add_link(node: Any, world_loc: Any, occ_id: str, tree_hash: str) -> dict[str, Any]:
        # An unlabelled placement of a child goes by the child's own name.
        from cadgen.store.trees import get_tree

        child = get_tree(tree_hash)
        name = _own_name(getattr(node, "label", ""), str((child or {}).get("label") or "part"))
        # The link places the child's TREE FRAME. The node's location is
        # ``placement * root`` when the child's own root occurrence is placed
        # (a part returned as ``Pos(...) * body``), and the tree re-applies that
        # root on expansion, so divide it out here or it lands twice.
        root_matrix = getattr(node, ROOT_LOC_TAG, None)
        if root_matrix is not None:
            world_loc = world_loc * _location_from_matrix(list(root_matrix)).inverse()
        link: dict[str, Any] = {
            "id": occ_id,
            "name": name,
            "tree": tree_hash,
            "transform": _transform_from_location(world_loc),
        }
        color = _occurrence_color(node)
        if color is not None:
            link["color"] = color
        links.append(link)
        progress.advance(detail=name)
        return {"id": occ_id, "name": name, "nodeType": "link", "tree": tree_hash, "children": []}

    def _consume_spliced(node: dict[str, Any], parent_world_loc: Any, path: str) -> dict[str, Any]:
        if node.get("leaf"):
            return _add_leaf(node["shape"], parent_world_loc * node["world_loc"], path, name=node["name"])
        child_nodes = [
            _consume_spliced(child, parent_world_loc, f"{path}.{index}")
            for index, child in enumerate(node["children"], start=1)
        ]
        return {
            "id": path,
            "name": _own_name(node["name"], "group"),
            "nodeType": "subassembly",
            "leafPartIds": [leaf for cn in child_nodes for leaf in cn.get("leafPartIds", [cn["id"]])],
            "children": child_nodes,
        }

    def _walk(
        node: Any,
        parent_world_loc: Any,
        path: str,
        baseline: Any = None,
        *,
        identities_verified: bool = False,
    ) -> dict[str, Any]:
        node_loc = getattr(node, "location", None)
        world_loc = (parent_world_loc * node_loc) if node_loc is not None else parent_world_loc
        if baseline is None:
            baseline = getattr(getattr(node, PARTNER_TAG, None), "baseline", None)
        if path != "o1":
            tagged = _tagged_intact(node, held)
            if tagged is not None:
                return _add_link(node, world_loc, path, tagged)
        nested_tree = getattr(node, "_occurrence_tree", None)
        if nested_tree is not None:
            spliced = _consume_spliced(dict(nested_tree, leaf=False), world_loc, path)
            spliced["name"] = _own_name(getattr(node, "label", ""), "") or _own_name(nested_tree.get("name"), "group")
            return spliced
        child_shapes = materialized_children(node, baseline)
        if not child_shapes:
            identity = baseline.component if identities_verified and baseline is not None else None
            return _add_leaf(node, world_loc, path, identity=identity)
        child_nodes = [
            _walk(
                child,
                world_loc,
                f"{path}.{index}",
                child_baseline,
                identities_verified=identities_verified,
            )
            for index, (child, child_baseline) in enumerate(child_shapes, start=1)
        ]
        return {
            "id": path,
            "name": _own_name(getattr(node, "label", ""), "group"),
            "nodeType": "subassembly",
            "leafPartIds": [leaf for cn in child_nodes for leaf in cn.get("leafPartIds", [cn["id"]])],
            "children": child_nodes,
        }

    progress.phase(PHASE_PACKAGE)
    # What every partner check of this walk reads of the kernel memo, read once.
    held = HeldDigests()
    holder = getattr(compound, PARTNER_TAG, None)
    baseline = getattr(holder, "baseline", None)
    verified_baseline = None
    verify = getattr(holder, "verified_baseline", None)
    if verify is not None:
        try:
            verified_baseline = verify(compound, held=held)
        except Exception:  # noqa: BLE001 - unverifiable geometry takes the canonical slow path
            verified_baseline = None
    if single_component:
        identity = verified_baseline.component if verified_baseline is not None else None
        root = _add_leaf(
            compound,
            getattr(compound, "location", None) or Location(),
            "o1",
            identity=identity,
        )
        root["nodeType"] = "part"
    else:
        occurrence_tree = getattr(compound, "_occurrence_tree", None)
        if occurrence_tree is not None:
            root = _consume_spliced(dict(occurrence_tree, leaf=False), Location(), "o1")
        else:
            root = _walk(
                compound,
                Location(),
                "o1",
                baseline,
                identities_verified=verified_baseline is not None,
            )
        # A native compound with no wrapper children (a fuse whose pieces do not
        # all touch) walks to one leaf: it is that part, not a childless assembly.
        root["nodeType"] = "assembly" if root.get("children") else "part"
    held.keep()
    if not occurrences and not links:
        raise RuntimeError(f"model {root_name!r} has no geometry")
    # The root is the model: it goes by the tree's label, whatever the compound was
    # called, as a reader of its file reads that label. One part is its document, and
    # its file names its one product so (``step_export``).
    root["name"] = root_name = _own_name(root_name, "model" if root.get("children") else "part")
    if not root.get("children"):
        occurrences[0]["name"] = root_name
    walk.root = root
    walk.tokenize(root_name=root_name)
    return walk


def _publish_tree(
    walk: _Walk,
    *,
    bbox_shape: Any,
    root_name: str,
    force: bool,
    progress: Any,
    extra: dict[str, Any] | None,
    repair_objects: bool = False,
    prepared_occurrence_bounds: bool = False,
    authored_occurrence_bounds: bool = False,
    appearance: dict[str, Any] | None = None,
    base_appearance: dict[str, Any] | None = None,
    prepare_bbox_shape: Callable[[], Any] | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Publish verified geometry inputs before any disposable display work.

    ``authored_occurrence_bounds`` measures a result per leaf under the canonical key
    (:func:`_bbox_from_authored_occurrences`), ``prepared_occurrence_bounds`` a
    compiled document (:func:`_bbox_from_prepared_occurrences`); ``prepare_bbox_shape``
    stands in for ``bbox_shape`` when the document a miss measures is prepared on
    demand."""
    from cadgen._internal.component_package import _bbox_from_shape, validate_geometry_component

    occurrences, links = walk.occurrences, walk.links
    components, root = walk.components, walk.root
    built, reused = [], []
    progress.phase(PHASE_COMPONENTS, total=len(components))
    for cid, entry in components.items():
        prepared = walk.prepared.get(cid)
        payload = prepared["payload"] if prepared is not None else read_verified_object(entry["brep"])
        if cid not in walk.recalled:
            # A remembered component's bytes and entry were checked by the publication that
            # remembered them, and neither has changed since (``_admit``).
            validate_geometry_component(entry, payload, cid=cid)
        ready = not force
        for object_key in COMPONENT_OBJECTS:
            if not entry.get(object_key):
                continue
            try:
                read_verified_object(entry[object_key])
            except (OSError, ValueError):
                ready = False
        # Reused bytes are claimed for this publish, so a sweep that started
        # before it cannot take them (STORE.md §8); ones a sweep already took
        # are written again from the bytes in hand.
        if ready and all(claim_object(entry[key]) for key in COMPONENT_OBJECTS if entry.get(key)):
            reused.append(cid)
        else:
            # Capture owns these exact bytes; repair does not ask the live
            # authored shape or a newer child result to replace this input.
            put_object(payload, repair=True)
            for object_key in ("topology", "lineage"):
                if entry.get(object_key):
                    held = prepared.get(object_key) if prepared is not None else None
                    put_object(held if held is not None else read_verified_object(entry[object_key]), repair=True)
            built.append(cid)
        write_entry("component", cid, {"schemaVersion": 1, **entry})
        progress.advance()
    _admit(walk.pending)

    progress.phase(PHASE_FINALIZE)
    # Resolved intrinsic appearance has one home in schema-2 trees. Transient
    # materialized wrappers may have supplied inline private metadata while a
    # child became parent-owned geometry; flattening above already folded it
    # into the named library and assignments.
    for occurrence in occurrences:
        occurrence.pop("material", None)
        occurrence.pop("materialId", None)
    tree: dict[str, Any] = dict(extra or {})
    tree.update(
        {
            "label": root_name,
            "units": "mm",
            "components": components,
            "occurrences": occurrences,
            "links": links,
            "assembly": {"root": root},
        }
    )
    if appearance is not None:
        tree["appearance"] = appearance
    from cadgen.store.trees import tree_kind

    from cadgen.store.trees import TREE_KIND, TREE_SCHEMA, _validate_structure
    tree["kind"] = TREE_KIND
    tree["schemaVersion"] = TREE_SCHEMA
    tree["entryKind"] = tree_kind(tree)
    _validate_structure(tree, native=True)
    bbox = None
    if prepared_occurrence_bounds and not force:
        bbox = _bbox_from_prepared_occurrences(walk)
    if bbox is None and authored_occurrence_bounds:
        bbox = _bbox_from_authored_occurrences(walk, root_name=root_name)
    if bbox is None:
        bbox = _bbox_from_shape(bbox_shape if prepare_bbox_shape is None else prepare_bbox_shape())
    if bbox is not None:
        tree["bbox"] = bbox
    tree["stats"] = {"occurrenceCount": len(occurrences), "linkCount": len(links)}
    # The metadata-only refresh baseline: identical source geometry and child
    # pins with local @step materials removed, while inherited child appearance
    # remains. Publishing it is content-only and performs no kernel work.
    unannotated = dict(tree)
    if base_appearance is not None:
        unannotated["appearance"] = base_appearance
    else:
        unannotated.pop("appearance", None)
    unannotated_hash = put_tree(unannotated, repair=repair_objects)
    tree_hash = put_tree(tree, repair=repair_objects)
    stats = {
        "occurrences": len(occurrences),
        "links": len(links),
        "unique_components": len(components),
        "components_built": len(built),
        "components_reused": len(reused),
        "unannotatedTree": unannotated_hash,
        # What the model's run says of what this walk prepared from its shapes: what
        # the model's provenance entry says its build made (STORE.md §3).
        "account": {"components": {cid: walk.names[cid] for cid in sorted(walk.names)},
                    "occurrences": dict(sorted(walk.placed.items()))},
    }
    return tree_hash, tree, stats


def _bbox_from_authored_occurrences(walk: _Walk, *, root_name: str) -> dict[str, list[float]] | None:
    """The result's bounds, measured per leaf under the canonical key
    (``placed_prototype_bounds``): each occurrence's component placed by the transform
    the tree records -- the prototype the walk holds for the components it prepared; for
    a linked child's, or one this worker remembered publishing, the box the store
    remembers for it at that placement (``bounds.composed_bounds``), else the component
    decoded -- a remembered one from its own bytes, a child's from the store. So a
    child's geometry is never assembled for its parent's bounds, and the box is the same
    function of the tree whichever way it is found. None where a component cannot be
    measured so (an eager-only child's): the whole result is measured instead."""
    from cadgen._internal.component_package import decode_geometry_component, placed_prototype_bounds
    from cadgen.store.bounds import NotRemembered, composed_bounds
    from cadgen.store.materialize import _location_from_matrix
    from cadgen.store.trees import flatten_tree

    try:
        flattened = flatten_tree(walk.draft_tree(root_name=root_name))
        components = flattened["components"]
        decoded: dict[str, Any] = {}
        boxes: list[dict[str, list[float]]] = []
        for occurrence in flattened["occurrences"]:
            cid = str(occurrence["component"])
            entry = components[cid]
            prototype = (walk.prepared.get(cid) or {}).get("shape")
            if prototype is None:
                try:
                    boxes.append(composed_bounds([occurrence], components))
                    continue
                except NotRemembered:
                    prototype = walk.prototype(cid)
                    if prototype is None:
                        if cid not in decoded:
                            decoded[cid] = decode_geometry_component(entry, read_verified_object(entry["brep"]))
                        prototype = decoded[cid]
            box = placed_prototype_bounds(str(entry["codec"]), str(entry["brep"]), prototype,
                                          _location_from_matrix(list(occurrence["transform"])))
            if box is not None:
                boxes.append(box)
        if not boxes:
            return None
        return {
            "min": [min(box["min"][axis] for box in boxes) for axis in range(3)],
            "max": [max(box["max"][axis] for box in boxes) for axis in range(3)],
        }
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        return None


def _bbox_from_prepared_occurrences(walk: _Walk) -> dict[str, list[float]] | None:
    """A compiled document's exact bounds without serializing every placed occurrence.

    Each leaf is measured as the prototype its component was prepared from, at the
    exact native location of its parsed STEP occurrence, through
    ``placed_prototype_bounds``, which remembers every leaf's box under the key a
    composed reader looks up (``cadgen.store.bounds.PREPARED_OCCURRENCE_BOUNDS_ALGORITHM``),
    and only its removed final translation is applied. No transformed local AABB is
    used.

    Any incomplete private input falls back to the ordinary composed-shape
    path in ``_publish_tree``. Store component and draft-structure validation
    have already run before this helper is called.
    """
    try:
        from cadgen._internal.component_package import placed_prototype_bounds

        boxes: list[dict[str, list[float]]] = []
        for occurrence in walk.occurrences:
            cid = str(occurrence["component"])
            entry = walk.components[cid]
            prototype = walk.prepared[cid]["shape"]
            location = walk.native_locations[str(occurrence["id"])]
            if prototype is None or location is None:
                return None
            box = placed_prototype_bounds(str(entry["codec"]), str(entry["brep"]), prototype, location)
            if box is not None:
                boxes.append(box)
        if not boxes:
            return None
        return {
            "min": [min(box["min"][axis] for box in boxes) for axis in range(3)],
            "max": [max(box["max"][axis] for box in boxes) for axis in range(3)],
        }
    except Exception:  # noqa: BLE001 - ordinary native bounds remain the exact fallback
        return None


# --- the components a worker remembers publishing ----------------------------------------
#
# In the worker's memory, never the store, LRU-bounded by entries and bytes; a worker the
# daemon reclaims takes them with it (README law 18: nothing here outlives the worker).

_REMEMBERED_ENTRIES = 8192
_REMEMBERED_BYTES = 512 * 1024 * 1024
_REMEMBERED: "OrderedDict[tuple, _Remembered]" = OrderedDict()
_REMEMBERED_LOCK = threading.Lock()
_STATS = {"recalled": 0, "remembered": 0, "errors": 0, "bytes": 0}


@dataclass(eq=False)
class _Remembered:
    """A component this worker published, as its publication prepared it -- the entry,
    its BREP payload, an eager-only component's topology facts and its lineage object's
    bytes -- with all it stood on: the construction key the run named it by; how many
    changes the memo could not replay it had seen (``unreplayed``); the ledger
    as it stood (``ledger``: the publication's snapshot), which the first recall reads the
    pairs on the body's sub-shapes from -- the hash of each at the identity placement
    (``subshapes``), and the snapshot's ``(TShape, rows)`` pairs on those (``rows``),
    kept from then on in place of the snapshot; the run's naming of the body restated
    (``account_basis``: the part's own, and the closure of features it reads, with the
    histories its ids are of); and per feature name of its lineage, the history of the
    feature it stood for."""

    construction: str | None
    unreplayed: int
    ledger: tuple[dict[int, tuple], set[int]] | None
    subshapes: Any
    rows: dict[int, tuple] | None
    part: tuple
    closure: tuple
    histories: tuple
    prepared: dict[str, Any]
    names: dict[str, int]
    size: int


def clear_prepared() -> None:
    """Forget every component this worker remembers publishing: the next publication
    prepares each one as a fresh worker would."""
    with _REMEMBERED_LOCK:
        _REMEMBERED.clear()
        _STATS["bytes"] = 0


def prepared_stats() -> dict[str, int]:
    """Components recalled, remembered and errors survived (each a fresh preparation), and
    what is remembered now."""
    with _REMEMBERED_LOCK:
        return dict(_STATS, entries=len(_REMEMBERED))


def _recall(walk: _Walk, node: Any, key: tuple, namer: Namer) -> tuple[dict[str, Any], dict[str, int]] | None:
    """The prepared component and the feature each name of its lineage stands for in this
    run, of a leaf this worker published before -- while the run names it by the same
    construction key, the memo has met no change it could not replay since (one it notes
    no row for, wherever it was), it holds the same rows on the leaf's sub-shapes, and the
    run's naming of it is the same -- else None, and the leaf is prepared again."""
    with _REMEMBERED_LOCK:
        remembered = _REMEMBERED.get(key)
    if remembered is None:
        return None
    try:
        from cadgen._internal import kernel_memo

        if _unreplayed(walk) != remembered.unreplayed:
            return None
        if kernel_memo.construction_key_in(namer, node) != remembered.construction:
            return None
        subshapes, rows = _pairs_then(remembered, key, node)
        if _rows_on(walk, subshapes) != rows:
            return None
        basis = _basis(walk, node, namer)
        if basis is None or basis[0] != remembered.part or not _same_closure(walk, basis[1], remembered.closure):
            return None
        names = _resolved(walk, namer, remembered.names)
        if names is None:
            return None
    except Exception:  # noqa: BLE001 - checking a remembered component never fails a build: it is prepared again
        with _REMEMBERED_LOCK:
            _STATS["errors"] += 1
        return None
    with _REMEMBERED_LOCK:
        if _REMEMBERED.get(key) is remembered:
            _REMEMBERED.move_to_end(key)
        _STATS["recalled"] += 1
    return {**copy.deepcopy(remembered.prepared), "shape": None}, names


def _remember(walk: _Walk, node: Any, key: tuple, namer: Namer, construction: str | None,
              names: Mapping[str, int], prepared: dict[str, Any]) -> None:
    """Note a leaf this walk prepared, to be remembered once its publication has checked and
    published it (``_admit``), with what ``_recall`` checks as it stands now. A leaf the
    memo does not hold (no later hit can hand it back), whose naming cannot be restated, or
    whose names stand for a feature outside it, is not."""
    try:
        from cadgen._internal import kernel_memo

        if not kernel_memo.held((key[0],)):
            return
        basis = _basis(walk, node, namer)
        if basis is None:
            return
        part, closure, histories = basis
        held = walk.held.get(id(histories))
        if held is None:
            held = walk.held[id(histories)] = frozenset(map(id, histories))
        features = namer.trace.features
        by_history: dict[str, int] = {}
        for name, index in names.items():
            history = features[index].history
            if history is None or id(history) not in held:
                return
            by_history[name] = id(history)
        kept = {"entry": copy.deepcopy(prepared["entry"]), "payload": prepared["payload"],
                "topology": prepared["topology"], "lineage": prepared["lineage"]}
        size = len(kept["payload"]) + len(kept["topology"] or b"") + len(kept["lineage"] or b"") + 1024
        walk.pending.append((key, _Remembered(construction, _unreplayed(walk), _ledger(walk), None, None, part,
                                              closure, histories, kept, by_history, size)))
    except Exception:  # noqa: BLE001 - remembering is an optimization: this leaf is simply prepared next time
        with _REMEMBERED_LOCK:
            _STATS["errors"] += 1


def _admit(pending: list[tuple[tuple, _Remembered]]) -> None:
    """Remember what a publication prepared, now that it has checked and published it; and
    forget what the memo no longer holds -- no hit can hand it back, and remembering it
    would keep its geometry alive -- then, past the bounds, the least recently recalled."""
    if not pending:
        return
    from cadgen._internal import kernel_memo

    with _REMEMBERED_LOCK:
        for key, remembered in pending:
            replaced = _REMEMBERED.pop(key, None)
            if replaced is not None:
                _STATS["bytes"] -= replaced.size
            _REMEMBERED[key] = remembered
            _STATS["bytes"] += remembered.size
            _STATS["remembered"] += 1
        kept = kernel_memo.held(key[0] for key in _REMEMBERED)
        for key in [key for key in _REMEMBERED if key[0] not in kept]:
            _STATS["bytes"] -= _REMEMBERED.pop(key).size
        while _REMEMBERED and (len(_REMEMBERED) > _REMEMBERED_ENTRIES or _STATS["bytes"] > _REMEMBERED_BYTES):
            _key, forgotten = _REMEMBERED.popitem(last=False)
            _STATS["bytes"] -= forgotten.size
    pending.clear()


def _basis(walk: _Walk, node: Any, namer: Namer) -> tuple[tuple, tuple, tuple] | None:
    """The run's naming of ``node``'s body restated (``kernel_trace.account_basis``), with
    what the walk has restated already."""
    from OCP.TopLoc import TopLoc_Location

    from cadgen._internal.kernel_trace import account_basis

    return account_basis(namer.trace, node.wrapped.Located(TopLoc_Location()), namer.made_by, walk.bases)


def _same_closure(walk: _Walk, closure: tuple, remembered: tuple) -> bool:
    """Whether a closure this walk restated is a remembered one: compared once per walk,
    since every part the walk restates with the same ancestors shares one closure."""
    if closure is remembered or walk.closures.get(id(closure)) is remembered:
        return True
    if closure != remembered:
        return False
    walk.closures[id(closure)] = remembered
    return True


def _ledger(walk: _Walk) -> tuple[dict[int, tuple], set[int]]:
    """The walk's one reading of the memo's ledger (``kernel_memo.ledger_snapshot``), and
    its hashes: nothing changes the memo's state while a result is published."""
    if walk.ledger is None:
        from cadgen._internal import kernel_memo

        walk.ledger = kernel_memo.ledger_snapshot()
        walk.ledger_keys = set(walk.ledger)
    return walk.ledger, walk.ledger_keys


def _unreplayed(walk: _Walk) -> int:
    """How many changes the memo could not replay, or could not capture, this worker has met
    (``kernel_memo.stats``: the unreplayable, the errors and every input it tainted, an
    embedded root changed by an uncached call among them): each leaves a state its ledger
    holds no row for, on whatever shapes share the sub-shape it was on, so a component
    published before one may not be what it was."""
    if walk.unreplayed is None:
        from cadgen._internal import kernel_memo

        counts = kernel_memo.stats()
        walk.unreplayed = counts["unreplayable"] + counts["errors"] + counts["tainted"]
    return walk.unreplayed


def _rows_on(walk: _Walk, subshapes: Any) -> dict[int, tuple]:
    """The walk's ledger's pairs on the sub-shapes with these hashes."""
    ledger, keys = _ledger(walk)
    return {key: ledger[key] for key in keys.intersection(subshapes)}


def _pairs_then(remembered: _Remembered, key: tuple, node: Any) -> tuple[Any, dict[int, tuple]]:
    """A remembered component's sub-shape hashes and the ledger's pairs on them when it was
    published: read off its publication's snapshot the first time they are asked for --
    hashing a body's sub-shapes costs about what serializing it does, and a component
    prepared once may never be recalled -- and kept in its place from then on."""
    with _REMEMBERED_LOCK:
        subshapes, rows, ledger = remembered.subshapes, remembered.rows, remembered.ledger
    if subshapes is None:
        subshapes = subshape_hashes(node)
        snapshot, keys = ledger
        rows = {hashed: snapshot[hashed] for hashed in keys.intersection(subshapes)}
        with _REMEMBERED_LOCK:
            if remembered.subshapes is None:
                remembered.subshapes, remembered.rows, remembered.ledger = subshapes, rows, None
                grown = subshapes.itemsize * len(subshapes)
                remembered.size += grown
                if _REMEMBERED.get(key) is remembered:
                    _STATS["bytes"] += grown
    return subshapes, rows


def _resolved(walk: _Walk, namer: Namer, names: Mapping[str, int]) -> dict[str, int] | None:
    """Each name's feature in this run, from the history it stood for; None where a history
    is no feature's here, or more than one's."""
    if walk.places is None:
        places: dict[int, int] = {}
        shared: set[int] = set()
        for feature in namer.trace.features:
            if feature.history is not None:
                if id(feature.history) in places:
                    shared.add(id(feature.history))
                places[id(feature.history)] = feature.index
        walk.places = {history: index for history, index in places.items() if history not in shared}
    resolved = {name: walk.places.get(history) for name, history in names.items()}
    return None if None in resolved.values() else resolved
