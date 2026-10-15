"""The selector index: every row a ref can resolve to, keyed by the ref that names it.

An occurrence row is keyed by its token (``o:…``); a face, edge or vertex row by
``<occurrence token>/<element token>``, the one composition rule
(``cad_ref_syntax.element_ref``). The rows come from the tree and the parts'
selector tables (``assembly_lookup``); nothing here mints a name.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cadgen import cad_ref_syntax as syntax


def _table_rows(manifest: dict[str, Any], table_name: str, columns_name: str) -> list[dict[str, Any]]:
    """Materialize one manifest table into dicts keyed by its declared column names."""
    columns = manifest.get("tables", {}).get(columns_name)
    rows = manifest.get(table_name)
    if not isinstance(columns, list) or not isinstance(rows, list):
        return []
    materialized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, list):
            continue
        materialized.append({str(columns[index]): row[index] for index in range(min(len(columns), len(row)))})
    return materialized


@dataclass(frozen=True)
class SelectorIndex:
    occurrences: list[dict[str, Any]]
    shapes: list[dict[str, Any]]
    faces: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    vertices: list[dict[str, Any]]
    occurrence_by_id: dict[str, dict[str, Any]]
    face_by_id: dict[str, dict[str, Any]]
    edge_by_id: dict[str, dict[str, Any]]
    vertex_by_id: dict[str, dict[str, Any]]
    # The one leaf of a document that has one, so a bare element token (``#f:…``) names an
    # element of it; "" in an assembly.
    single_occurrence_id: str
    # Label aliases for this index's occurrences, filled in by
    # cadgen.label_refs.attach_label_aliases once the row set is final. Empty until then, and
    # empty forever for inputs whose parts carry no usable labels -- those stay reachable by
    # their tokens, exactly as before labels existed.
    label_aliases: dict[str, Any] = field(default_factory=dict)
    # The instance tree's INTERIOR nodes -- subassemblies -- keyed by token, as the tree
    # records them (name, nodeType, child count). Rows above are leaves, because only a leaf
    # owns geometry; this is the rest of the tree, and it is what lets a command answer a group
    # ref with the node's own identity instead of an anonymous id. Filled in by
    # cadgen.assembly_lookup.merge_assembly_occurrences; empty for part entries, which
    # have no interior nodes to describe.
    group_nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Every tree node's leaf occurrences (a leaf's: itself), by token: a group ref's members.
    # A token holds no hierarchy, so the tree is the one source of it (``occurrence_groups``).
    members: dict[str, list[str]] = field(default_factory=dict)


def build_selector_index(manifest: dict[str, Any]) -> SelectorIndex:
    """An index over one table's rows: an occurrence row keyed by its ``id``, each element
    row by ``id`` where the table carries one (a composed index's do; a component's own
    table carries tokens alone, which :mod:`cadgen.assembly_lookup` composes with the
    occurrence that places it)."""
    occurrences = _table_rows(manifest, "occurrences", "occurrenceColumns")
    shapes = _table_rows(manifest, "shapes", "shapeColumns")
    faces = _table_rows(manifest, "faces", "faceColumns")
    edges = _table_rows(manifest, "edges", "edgeColumns")
    vertices = _table_rows(manifest, "vertices", "vertexColumns")

    def by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {str(row["id"]): row for row in rows if row.get("id")}

    occurrence_by_id = by_id(occurrences)
    leaf_ids = [row_id for row_id in occurrence_by_id if syntax.OCCURRENCE_SELECTOR_RE.match(row_id)]
    return SelectorIndex(
        occurrences=occurrences,
        shapes=shapes,
        faces=faces,
        edges=edges,
        vertices=vertices,
        occurrence_by_id=occurrence_by_id,
        face_by_id=by_id(faces),
        edge_by_id=by_id(edges),
        vertex_by_id=by_id(vertices),
        single_occurrence_id=leaf_ids[0] if len(leaf_ids) == 1 else "",
    )


def label_resolution_error(raw_selector: str, index: SelectorIndex) -> str | None:
    """Why a LABEL selector does not resolve here, in the resolver's own words, or None.

    ``canonicalize_selector`` answers "what does this name" and says None for a label it
    cannot place; this answers "why not". The resolver already computed the useful part --
    the numbered candidates of a duplicate (``#cast_rim_1 (o:…), ...``), or that the
    label is unknown and ``snapshot --mode list`` shows the names -- and ``snapshot``
    prints it. Every error path that would otherwise say "did not resolve" asks here so
    every command says the same thing. None for a token or opaque selector, and for a
    label that resolves.
    """
    parsed = syntax.parse_selector(raw_selector)
    if parsed is None or not parsed.label:
        return None
    from cadgen.label_refs import LabelResolutionError, resolve_label_selectors

    try:
        resolve_label_selectors([raw_selector], getattr(index, "label_aliases", None))
    except LabelResolutionError as error:
        return str(error)
    return None


def canonicalize_selector(raw_selector: str, index: SelectorIndex) -> str | None:
    """The ref ``raw_selector`` names in ``index``'s terms: a label resolved to its occurrence
    token, a bare element token qualified by the document's one leaf; None for a label that
    does not resolve or a bare element in an assembly."""
    parsed = syntax.parse_selector(raw_selector)
    if parsed is None:
        return None
    if parsed.label:
        # A label names an occurrence; turn it into one before the token logic below runs, so
        # every caller downstream sees the same canonical form it always has. A label that
        # does not resolve is None here like any other miss; the reason is
        # `label_resolution_error`'s to give, where the error message is built.
        from cadgen.label_refs import LabelResolutionError, resolve_label_selectors

        try:
            resolved = resolve_label_selectors([raw_selector], getattr(index, "label_aliases", None))
        except LabelResolutionError:
            return None
        parsed = syntax.parse_selector(resolved[0]) if resolved else None
        if parsed is None:
            return None
    if parsed.selector_type == "opaque" or parsed.occurrence_id:
        return parsed.canonical
    if not index.single_occurrence_id:
        return None
    return syntax.element_ref(index.single_occurrence_id, parsed.token)


def lookup_selector(selector: str, index: SelectorIndex) -> tuple[str, dict[str, Any]] | None:
    normalized = canonicalize_selector(selector, index)
    if not normalized:
        return None
    if normalized in index.occurrence_by_id:
        return "occurrence", index.occurrence_by_id[normalized]
    if normalized in index.face_by_id:
        return "face", index.face_by_id[normalized]
    if normalized in index.edge_by_id:
        return "edge", index.edge_by_id[normalized]
    if normalized in index.vertex_by_id:
        return "vertex", index.vertex_by_id[normalized]
    return None
