"""A STEP snapshot's parts, as Python knows them: ``--mode list``, and what focus/hide keep.

``cadgen step snapshot --mode list`` answers here, from the tree and the store,
and never starts a browser. One row per placed occurrence (``#o1.2``), in the
descriptor's order:

- ``ref`` and ``name`` are the occurrence's id and name exactly as
  :func:`cadgen.assembly_lookup.assembly_occurrence_rows` gives them -- the rows
  every selector, ``--focus`` and ``--hide`` resolve against, so a listed ref
  is one those accept;
- ``bounds`` is that row's box: the component's exact box (from its selector
  table), placed by the occurrence's transform, rounded to a nanometre;
- ``triangleCount`` and ``vertexCount`` are the part's stored display mesh's, as
  its record counts them (``cadgen.store.parts``): read, as the view the list is
  made from read it (``store.view.view_dir_for``).

A part whose mesh leaves out a face no mesher could cover is listed, drawn and
counted without it, and :func:`unmeshed_warnings` names the faces in the result's
warnings, for a list and a view alike.

``focus``/``hide`` select the same way the page's scene does for a view: an
occurrence matches a ref that IS its id, an ancestor group's id (``o1.2``
covers ``o1.2.3``), or its name.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

__all__ = ["LIST_BOUNDS_DECIMALS", "filter_occurrences", "list_rows", "unmeshed_warnings"]

# Three decimals of a millimetre is a nanometre: far below any tolerance a model
# is built to, and a fifth of the payload full float64 noise would be.
LIST_BOUNDS_DECIMALS = 3


def _selector_values(value: object) -> list[str]:
    values = value if isinstance(value, list) else str(value or "").split(",")
    out = []
    for item in values:
        text = str(item or "").strip()
        text = text[1:] if text.startswith("#") else text
        out.extend(part.strip() for part in text.split(",") if part.strip())
    return out


def _matches(row: Mapping[str, Any], selector: str) -> bool:
    occurrence_id = str(row.get("id") or "")
    if occurrence_id == selector or occurrence_id.startswith(f"{selector}."):
        return True
    return str(row.get("name") or "") == selector


def filter_occurrences(rows: Sequence[Mapping[str, Any]], selection: Mapping[str, Any] | None) -> list:
    """The rows ``selection``'s focus keeps and its hide does not remove.

    Raises ``ValueError`` when nothing is left: an empty picture is never the
    answer to a selection.
    """
    selection = selection if isinstance(selection, Mapping) else {}
    focus = _selector_values(selection.get("focus"))
    hide = _selector_values(selection.get("hide"))
    if not focus and not hide:
        return list(rows)
    kept = [
        row for row in rows
        if (not focus or any(_matches(row, selector) for selector in focus))
        and not any(_matches(row, selector) for selector in hide)
    ]
    if not kept:
        raise ValueError("No renderable parts remain after applying focus/hide filters")
    return kept


def _rounded(values: Sequence[float]) -> list:
    out = []
    for value in values:
        number = round(float(value), LIST_BOUNDS_DECIMALS)
        out.append(0 if number == 0 else (int(number) if number == int(number) else number))
    return out


def _records(descriptor: Mapping[str, Any], cids) -> dict:
    """``{cid: record}`` of the stored parts of ``cids`` (``cadgen.store.parts``)."""
    from cadgen.store.parts import probe

    components = descriptor.get("components") if isinstance(descriptor.get("components"), Mapping) else {}
    records = {}
    for cid in cids:
        key = str((components.get(cid) or {}).get("part") or "")
        record = probe(key) if key else None
        if record is not None:
            records[cid] = record
    return records


def has_surfaces(descriptor: Mapping[str, Any]) -> bool:
    """Whether any placed occurrence's part has a face to draw, by its selector table's
    face count. A model with none -- empty, or only curves and points -- is drawn as
    nothing, which nobody would guess from a blank image. A part the store does not
    hold is taken to have faces: this never claims an absence it did not see."""
    occurrences = descriptor.get("occurrences") if isinstance(descriptor.get("occurrences"), list) else []
    placed = list(dict.fromkeys(str(row.get("component") or "") for row in occurrences if isinstance(row, Mapping)))
    records = _records(descriptor, placed)
    return any(cid not in records or records[cid]["selectors"]["faceCount"] > 0 for cid in placed)


def unmeshed_warnings(
    descriptor: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    consequence: str,
) -> list[str]:
    """A warning per component whose stored display mesh leaves a face no
    mesher could cover undrawn (``cadgen.store.meshes``), naming the faces, the
    occurrences of ``rows`` that place it, and ``consequence``. A probe per component
    when none is missing."""
    from cadgen.store.meshes import unmeshed_warning
    from cadgen.store.parts import stored_unmeshed_faces

    components = descriptor.get("components") if isinstance(descriptor.get("components"), Mapping) else {}
    placed: dict[str, list[tuple[str, str]]] = {}
    for row in rows:
        if row.get("component"):
            placed.setdefault(str(row["component"]), []).append((str(row["id"]), str(row.get("name") or "")))
    warnings = []
    for cid, occurrences in placed.items():
        key = str((components.get(cid) or {}).get("part") or "")
        faces = stored_unmeshed_faces(key) if key else []
        if faces:
            warnings.append(unmeshed_warning(occurrences, faces, consequence))
    return warnings


def list_rows(
    descriptor: Mapping[str, Any],
    package_dir: Path,
    *,
    selection: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """The ``--mode list`` rows for a STEP tree (see the module docstring)."""
    from cadgen.assembly_lookup import assembly_occurrence_rows

    rows = filter_occurrences(assembly_occurrence_rows(descriptor, package_dir), selection)
    records = _records(descriptor, sorted({str(row["component"]) for row in rows if row.get("component")}))
    listed = []
    for row in rows:
        mesh = (records.get(str(row.get("component"))) or {}).get("glb") or {}
        triangles, vertices = int(mesh.get("indexCount") or 0) // 3, int(mesh.get("vertexCount") or 0)
        box = row.get("bbox") if isinstance(row.get("bbox"), Mapping) else None
        listed.append({
            "ref": f"#{row['id']}",
            "name": str(row.get("name") or row["id"]),
            "triangleCount": triangles,
            "vertexCount": vertices,
            "bounds": {"min": _rounded(box["min"]), "max": _rounded(box["max"])} if box else None,
        })
    return listed
