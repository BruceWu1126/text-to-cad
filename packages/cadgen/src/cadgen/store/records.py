"""Records: the mutable per-model entry in ``index/model/``.

``index/model/<sha256(model ref)>-v<RECORD_SCHEMA_VERSION>`` (``record_key``).
A record says what a model's build depended on and what it produced::

    {
      "model": "/abs/src/robot.py",
      "entryKind": "assembly",
      "tree": "<tree object hash>",
      "key": "<result key>",
      "producer": {"cadgen": "0.8.0", "build123d": "…", "cadqueryOcp": "…"},
      "closure": {"hash": "…", "files": ["robot.py", "lib/frame.py"]},
      "children": [{"model": "/abs/src/arm.py::arm", "key": "…"}],
      "outputs": {"/abs/STEP/robot.step": {"sha256": "…", "writer": "cadgen-step-writer-4"}, …}
    }

``key`` is the result's identity: a hash of the model, the closure, the
constants, the children's keys and the producer, computed by ``write_record``
from what the record holds (``cadgen.store.results``), under which the record
is also stored as written (``index/result``). ``children`` is recorded from the
CALLS the body made, each by its key, never derived from the tree's links —
that is what makes the link/component decision in ``cadgen.store.trees`` safe.
``closure.files`` are relative to the script's folder and hashed by
``cadgen.store.closure``.

Identity is the SCRIPT. A generated document carries no tie back to its
source (a generated file is independent of its script).

Two sides, two index kinds (STORE.md §2, the law):

- ``index/document/<sha256(document BYTES)>-v<DOCUMENT_SCHEMA_VERSION>``
  (``document_key``) → ``{"tree": …, "kind": …}`` (:func:`note_document_tree`).
  ARTIFACT → ARTIFACT. The only thing a reader (a door, the viewer, snapshot)
  consults to find the tree for a file: hash the bytes, look it up, read
  objects. Written whenever a tree is published for a document — by the
  model's build for its ``.step`` output, by a compile job for the file it
  compiled. The same bytes anywhere on disk resolve to the same tree; a file
  with no entry is compiled, never refused, and no record is opened.
- ``index/output/<sha256(output PATH)>`` → ``{"model": …}``. CODE-SIDE
  dependency memory: which model wrote the file at this path. Read by
  ``store why`` and provenance — never by the viewer or a render path.

Both schema versions are in the KEY: a cadgen reads and writes only its own
version's record and document entry, and one of another version sharing the
store keeps its own beside them, so neither recompiles what the other wrote nor
rewrites the other's entry. Nothing reads another version's entry; the sweeper
retires it once it is a week old (``gc``, STORE.md §8).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cadgen.store.index import (
    iter_entries,
    key_version,
    model_key,
    path_key,
    read_entry,
    remove_entry,
    resolve_model_ref,
    split_model_ref,
    versioned_key,
    write_entry,
)

RECORD_KIND = "record"
# A format change bumps it and lands on new keys (record_key); another version's
# records are never read.
# Schema 6 requires execution-time input hashes, including DXF.
# Older records may claim current input bytes for geometry built before an edit,
# or lack the import-time, absent-file and search-root entries a closure now
# carries. Their next source run rebuilds; saved-document mappings and objects
# stay valid.
# Schema 9: the tree a record names is geometry schema 4 (store.trees.TREE_SCHEMA), whose ids
# are tokens, and its appearance assignments are keyed by them (README law 20).
# Schema 10: the tree a record names is geometry schema 5, whose components carry the
# construction key and lineage their build named them by.
# Schema 11: the tree a record names is geometry schema 6, whose leaves' tokens round a
# translation as the STEP file states it.
# Schema 12: a model's STEP is an export of the tree it names: the record is written at
# the source result, its STEP output pending (an empty sha256) until the export lands,
# then its sha256 and writer. No separate document tree, correspondence maps or writer
# input.
# Schema 13: the record carries its result's key and the producer that built it, and
# names each child by the child's key (store/results.py), never by its tree.
# Schema 14: declared meshes carry complete pending recipes, before any file is
# written, so a stored result can reproduce them without its source.
RECORD_SCHEMA_VERSION = 14
# A format change bumps it and lands on new keys (document_key).
# Schema 6: an XCAF label entry (`=>[0:1:1:2]`) reads as no name, and an
# occurrence without one of its own never shows a shared product's name that is
# another occurrence's label, so the same bytes compile to a tree that names
# those occurrences differently. An older version's entry is never read and the
# document compiles again; its components are unchanged.
# Schema 7: the producer hint names the kernel alone (build123d, OCP and
# its distribution); the part version (store.parts.PART_VERSION) versions the
# extraction, the selector table and the mesh derived under it.
# Schema 8: the tree an entry names is geometry schema 3 (store.trees.TREE_SCHEMA),
# and the hint is ``producer``. A schema-7 entry names a schema-2 tree this cadgen
# cannot read: on its own key it would be taken for a hit that no view can open.
# Schema 9: the tree an entry names is geometry schema 4, its ids tokens.
# Schema 10: the tree an entry names is geometry schema 5: a saved document's leaves
# carry the construction key and lineage of the components they were written from.
# Schema 11: the tree an entry names is geometry schema 6, whose leaves' tokens round a
# translation as the STEP file states it.
# Schema 12: an exported file's bytes map to the result tree it was written from,
# pending (``verified`` false) until its verify answers, or to its own content and what
# differs (``differ``) when it does not hold that tree.
# Schema 13: an exported file's bytes map to the result tree it was written from, at
# once and finally: nothing parses the file to check it.
DOCUMENT_SCHEMA_VERSION = 13


def record_key(model: Path | str) -> str:
    """A model's record key: its identity's hash (``model_key``) under this
    cadgen's record schema."""
    return versioned_key(model_key(model), RECORD_SCHEMA_VERSION)


def document_key(document_hash: str) -> str:
    """The document entry key for these bytes under this cadgen's document schema."""
    return versioned_key(document_hash, DOCUMENT_SCHEMA_VERSION)


def read_record(model: Path | str) -> dict[str, Any] | None:
    data = read_entry("model", record_key(model))
    if data is None:
        # A bare script path whose file is gone (or no longer parses) cannot name
        # its function: find the record by the script it recorded, when exactly one.
        script, function = split_model_ref(model)
        if function is None and script.suffix.lower() == ".py" and not script.is_file():
            found = records_for_script(script)
            data = found[0][1] if len(found) == 1 else None
    if data is None or data.get("kind") != RECORD_KIND or data.get("schemaVersion") != RECORD_SCHEMA_VERSION:
        return None
    return data


def records_for_script(script: Path | str) -> list[tuple[str, dict[str, Any]]]:
    """Every record whose model lives in ``script``: ``(model ref, record)`` pairs,
    in file order of the index."""
    import json

    resolved = _resolved(script)
    found: list[tuple[str, dict[str, Any]]] = []
    for key, entry_file in iter_entries("model"):
        if key_version(key) != RECORD_SCHEMA_VERSION:
            continue
        try:
            data = json.loads(entry_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("kind") == RECORD_KIND and data.get("schemaVersion") == RECORD_SCHEMA_VERSION and data.get("script") == resolved:
            found.append((str(data.get("model") or resolved), data))
    return found


def write_record(model: Path | str, payload: dict[str, Any]) -> str:
    """Write ``payload`` as ``model``'s record and return its key. Every record is
    keyed by what it holds (``results.key_for``), and the result at that key is the
    record as last written under it (``results.write_result``)."""
    from cadgen.store.results import key_for, write_result

    body = dict(payload)
    body["kind"] = RECORD_KIND
    body["schemaVersion"] = RECORD_SCHEMA_VERSION
    ref = resolve_model_ref(model)
    script, function = split_model_ref(ref)
    body["model"] = ref
    body["script"] = str(script)
    body["function"] = function
    body["key"] = key_for(body, ref)
    write_entry("model", record_key(ref), body)
    write_result(body["key"], ref, body)
    return body["key"]


def remove_record(model: Path | str) -> None:
    remove_entry("model", record_key(model))


def _resolved(path: Path | str) -> str:
    try:
        return str(Path(path).expanduser().resolve())
    except (OSError, ValueError, RuntimeError):
        import os

        return os.path.abspath(str(path))


def note_document_tree(
    document_hash: str, tree: str, *, kind: str = "step", producer: dict[str, Any] | None = None,
) -> None:
    """``index/document/<sha256(bytes)>-v<schema>`` → the tree describing those
    bytes. Artifact → artifact; idempotent; atomic. Keeps the entry's mesh ledger
    when the tree is unchanged (those meshes were cut from this same tree).

    An export's publication maps the bytes it wrote to the tree it wrote them from
    (``store/exports.py``); a compile, the bytes it read to the tree it built from them."""
    digest = str(document_hash or "").strip()
    tree_hash = str(tree or "").strip()
    if not digest or not tree_hash:
        return
    existing = read_entry("document", document_key(digest)) or {}
    payload: dict[str, Any] = {"schemaVersion": DOCUMENT_SCHEMA_VERSION, "tree": tree_hash, "kind": str(kind or "step")}
    meshes = existing.get("meshes")
    if existing.get("schemaVersion") == DOCUMENT_SCHEMA_VERSION and isinstance(meshes, dict) and str(existing.get("tree") or "") == tree_hash:
        payload["meshes"] = meshes
    from cadgen.store.parts import producer_fields
    if producer is not None:
        # Attestation belongs to the worker or validated artifact response;
        # lightweight readers must not import the kernel to persist its hint.
        selected = producer_fields(producer)
        payload["producer"] = selected
    elif existing.get("schemaVersion") == DOCUMENT_SCHEMA_VERSION and existing.get("tree") == tree_hash:
        try:
            payload["producer"] = producer_fields(existing.get("producer"))
        except (ValueError, TypeError):
            pass
    write_entry("document", document_key(digest), payload)


def document_entry_for_hash(document_hash: str) -> dict[str, Any] | None:
    """One atomic selected tree/hint snapshot; geometry ignores optional hints."""
    from cadgen.store.objects import is_object_hash

    if not is_object_hash(document_hash):
        return None
    entry = read_entry("document", document_key(document_hash))
    if not entry or entry.get("schemaVersion") != DOCUMENT_SCHEMA_VERSION or not is_object_hash(entry.get("tree")):
        return None
    return entry


def tree_for_document_hash(document_hash: str) -> str | None:
    """The tree a reader uses for these exact document bytes, or a cache miss."""
    entry = document_entry_for_hash(str(document_hash or "").strip())
    return entry["tree"] if entry else None


def note_document_mesh(document_hash: str, variant_key: str, sha256: str) -> None:
    """A mesh door's ledger, artifact-side: the mesh cut from THESE bytes at THIS
    variant (format × tolerances × pose) has this sha. Lives on the document
    entry so a door never opens a record. Best-effort (last write wins)."""
    digest = str(document_hash or "").strip()
    if not digest or not variant_key or not sha256:
        return
    entry = read_entry("document", document_key(digest))
    if not entry or not entry.get("tree") or entry.get("schemaVersion") != DOCUMENT_SCHEMA_VERSION:
        return
    meshes = dict(entry.get("meshes") or {})
    meshes[str(variant_key)] = str(sha256)
    entry["meshes"] = meshes
    write_entry("document", document_key(digest), entry)


def document_mesh_sha(document_hash: str, variant_key: str) -> str | None:
    digest = str(document_hash or "").strip()
    if not digest:
        return None
    entry = read_entry("document", document_key(digest)) or {}
    if entry.get("schemaVersion") != DOCUMENT_SCHEMA_VERSION:
        return None
    meshes = entry.get("meshes") or {}
    value = meshes.get(str(variant_key)) if isinstance(meshes, dict) else None
    return str(value) if value else None


def note_output(output_path: Path | str, model: Path | str) -> None:
    """Code-side memory: ``model`` wrote the file at ``output_path``. For
    ``store why`` and provenance — never for the viewer, and never for finding
    or rendering an artifact (that is ``note_document_tree``). Idempotent; atomic."""
    write_entry("output", path_key(output_path), {"model": resolve_model_ref(model)})


def forget_output(output_path: Path | str) -> None:
    remove_entry("output", path_key(output_path))


def recorded_model_for_output(output_path: Path | str) -> str | None:
    """The raw output index's model ref, independent of whether its source exists.

    Explicit export uses this code-side route to the recorded result. Ordinary
    provenance callers keep :func:`model_for_output`'s existing-source rule.
    """
    entry = read_entry("output", path_key(output_path)) or {}
    recorded = str(entry.get("model") or "").strip()
    return recorded or None


def export_state(record: dict[str, Any]) -> dict[str, Any]:
    """The publication identity: geometry, resolved annotations and output recipes.

    Receipts and owed errors change while one run saves; its recipes do not.
    Tolerances are not geometry-key inputs, so key/tree alone cannot identify
    the result an export is allowed to publish for.
    """
    import copy

    recipes = {}
    for path, entry in (record.get("outputs") or {}).items():
        if not isinstance(entry, dict):
            continue
        if entry.get("declared"):
            recipes[path] = {field: entry.get(field) for field in ("declared", "chord", "angle", "name")}
        elif entry.get("writer"):
            recipes[path] = {"writer": entry["writer"]}
    return copy.deepcopy({"key": record.get("key"), "tree": record.get("tree"), "recipes": recipes,
                          "annotations": {field: record.get(field) for field in
                                          ("kinematics", "appearance", "animation")}})


def model_for_output(output_path: Path | str) -> str | None:
    """The model (``script::fn``) the store remembers writing ``output_path``,
    when that script still exists, else None. MODEL-SIDE: ``store why`` / provenance."""
    recorded = recorded_model_for_output(output_path)
    if recorded:
        script, _function = split_model_ref(recorded)
        if script.is_file():
            return recorded
    return None


def source_for_document(document: Path | str) -> str:
    """MODEL-SIDE (``store why``, provenance — never the viewer or a render path): the
    model the store remembers writing ``document``, else the document itself
    (an imported source keys its own record on its path)."""
    document = Path(document)
    model = model_for_output(document)
    if model is not None:
        return model
    return _resolved(document)


def record_for_document(document: Path | str) -> dict[str, Any] | None:
    """MODEL-SIDE: the record behind a ``.step`` on disk (generated or imported),
    or None. A reader that needs a TREE uses ``tree_for_document_hash``."""
    return read_record(source_for_document(document))
