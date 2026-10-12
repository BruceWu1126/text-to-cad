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
    shape_by_id: dict[str, dict[str, Any]]
    face_by_id: dict[str, dict[str, Any]]
    edge_by_id: dict[str, dict[str, Any]]
    vertex_by_id: dict[str, dict[str, Any]]
    single_occurrence_id: str
    # Label aliases for this index's occurrences, filled in by
    # cadgen.label_refs.attach_label_aliases once the row set is final. Empty until then, and
    # empty forever for inputs whose parts carry no usable labels -- those stay reachable by
    # their numeric ids, exactly as before labels existed.
    label_aliases: dict[str, Any] = field(default_factory=dict)
    # The instance tree's INTERIOR nodes -- subassemblies -- keyed by occurrence id, as
    # the assembly.json records them (name, nodeType, and a transform where one is
    # written). Rows above are leaves, because only a leaf owns geometry; this is the
    # rest of the tree, and it is what lets a command answer a group ref with the node's
    # own identity instead of an anonymous id. Filled in by
    # cadgen.assembly_lookup.merge_assembly_occurrences; empty for part entries, which
    # have no interior nodes to describe.
    group_nodes: dict[str, dict[str, Any]] = field(default_factory=dict)


def build_selector_index(manifest: dict[str, Any]) -> SelectorIndex:
    occurrences = _table_rows(manifest, "occurrences", "occurrenceColumns")
    shapes = _table_rows(manifest, "shapes", "shapeColumns")
    faces = _table_rows(manifest, "faces", "faceColumns")
    edges = _table_rows(manifest, "edges", "edgeColumns")
    vertices = _table_rows(manifest, "vertices", "vertexColumns")

    occurrence_by_id = {str(row.get("id")): row for row in occurrences if row.get("id")}
    shape_by_id = {str(row.get("id")): row for row in shapes if row.get("id")}
    face_by_id = {str(row.get("id")): row for row in faces if row.get("id")}
    edge_by_id = {str(row.get("id")): row for row in edges if row.get("id")}
    vertex_by_id = {str(row.get("id")): row for row in vertices if row.get("id")}

    leaf_occurrence_ids = tuple(
        sorted(
            {
                str(row.get("occurrenceId"))
                for row in shapes
                if isinstance(row.get("occurrenceId"), str) and row.get("occurrenceId")
            }
        )
    )
    single_occurrence_id = leaf_occurrence_ids[0] if len(leaf_occurrence_ids) == 1 else ""

    return SelectorIndex(
        occurrences=occurrences,
        shapes=shapes,
        faces=faces,
        edges=edges,
        vertices=vertices,
        occurrence_by_id=occurrence_by_id,
        shape_by_id=shape_by_id,
        face_by_id=face_by_id,
        edge_by_id=edge_by_id,
        vertex_by_id=vertex_by_id,
        single_occurrence_id=single_occurrence_id,
    )


def label_resolution_error(raw_selector: str, index: SelectorIndex) -> str | None:
    """Why a LABEL selector does not resolve here, in the resolver's own words, or None.

    ``canonicalize_selector`` answers "what does this name" and says None for a label it
    cannot place; this answers "why not". The resolver already computed the useful part --
    the numbered candidates of a duplicate (``#cast_rim_1 (o1.7.2), ...``), or that the
    label is unknown and ``snapshot --mode list`` shows the names -- and ``snapshot``
    prints it. Every error path that would otherwise say "did not resolve" asks here so
    ``inspect`` says the same thing. None for a numeric or opaque selector, and for a
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
    parsed = syntax.parse_selector(raw_selector)
    if parsed is None:
        return None
    if parsed.label:
        # A label names an occurrence; turn it into one before the numeric logic below runs, so
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
    if parsed.selector_type == "opaque":
        return parsed.canonical
    if parsed.occurrence_id:
        return parsed.canonical
    if not index.single_occurrence_id:
        return None
    if parsed.selector_type == "shape":
        return f"{index.single_occurrence_id}.s{parsed.ordinal}"
    if parsed.selector_type == "face":
        return f"{index.single_occurrence_id}.f{parsed.ordinal}"
    if parsed.selector_type == "edge":
        return f"{index.single_occurrence_id}.e{parsed.ordinal}"
    if parsed.selector_type == "vertex":
        return f"{index.single_occurrence_id}.v{parsed.ordinal}"
    return parsed.canonical


def display_selector(selector: str, index: SelectorIndex) -> str:
    if not selector or not index.single_occurrence_id:
        return selector
    prefix = f"{index.single_occurrence_id}."
    if selector.startswith(prefix):
        tail = selector[len(prefix) :]
        if tail.startswith(("s", "f", "e", "v")):
            return tail
    return selector


def lookup_selector(selector: str, index: SelectorIndex) -> tuple[str, dict[str, Any]] | None:
    normalized = canonicalize_selector(selector, index)
    if not normalized:
        return None
    if normalized in index.occurrence_by_id:
        return "occurrence", index.occurrence_by_id[normalized]
    if normalized in index.shape_by_id:
        return "shape", index.shape_by_id[normalized]
    if normalized in index.face_by_id:
        return "face", index.face_by_id[normalized]
    if normalized in index.edge_by_id:
        return "edge", index.edge_by_id[normalized]
    if normalized in index.vertex_by_id:
        return "vertex", index.vertex_by_id[normalized]
    return None
