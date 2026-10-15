"""Provenance: the build's account of its own code, on the store's code side
(STORE.md §3, Provenance; README law 20).

``index/provenance/<sha256(model ref)>-v<PROVENANCE_SCHEMA_VERSION>``, one
entry per model beside its record, written by the build right after the
record for the result tree the record names::

    {
      "kind": "provenance", "schemaVersion": 1,
      "model": "/abs/src/bracket.py::bracket",
      "tree": "<the record's tree>", "stepHash": "<sha256 of the STEP written>",
      "features": [{"id": "fe:cut@src/bracket.py:12#1", "op": "cut", "file": "src/bracket.py",
                    "line": 12, "function": "bracket", "n": 1, "inputs": [0, 1]}, …],
      "components": {"<fingerprint>": {"made": 2, "faces": {"f:9d2e8c1b5a70": [2, 5]}, "edges": {…}}}
    }

``features`` are the kernel constructions the model's code called, in call
order (``_internal/kernel_trace.py``); ``inputs`` and every value under
``components`` index that list (null: made outside the run). A component is
named by its fingerprint and its faces and edges by their tokens, never by an
ordinal, so an entry written for the body the build held answers for the body
a document read back; each element has the feature that made it and the one
that last modified it (null when none has).

Readers never open it (STORE.md §2, the two sides). Its one reader is
``cadgen store where <document> <ref>``: path → ``index/output`` → the model
→ its record and this entry, the file's hash checked against ``stepHash``,
then the ref resolved in the document tree the record names (a label as
``read_scene`` resolves it) to its occurrence and component, the component's
fingerprint found in this entry or in a pinned child's, and the element's
token looked up. Without an entry, or with one for other bytes, it says it
does not know; nothing here finds or renders geometry. The entry goes with
``store forget`` and is never evicted (§8); like a record, one at a key this
cadgen does not own is obsolete.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any, Iterator

from cadgen.store.index import model_key, read_entry, remove_entry, resolve_model_ref, versioned_key, write_entry

PROVENANCE_KIND = "provenance"
# A format change bumps it and lands on new keys; another version's entries are
# never read, and are retired a week after their last write (gc, STORE.md §8).
PROVENANCE_SCHEMA_VERSION = 1

__all__ = [
    "PROVENANCE_KIND",
    "PROVENANCE_SCHEMA_VERSION",
    "Where",
    "describe",
    "provenance_key",
    "read_provenance",
    "remove_provenance",
    "where",
    "write_provenance",
]


def provenance_key(model: Path | str) -> str:
    """A model's provenance key: its identity's hash under this cadgen's schema."""
    return versioned_key(model_key(model), PROVENANCE_SCHEMA_VERSION)


def write_provenance(model: Path | str, payload: dict[str, Any], *, tree: str, step_hash: str) -> None:
    """The entry for ``model``'s build that published ``tree`` and wrote the
    document whose bytes hash to ``step_hash`` (empty for a model that writes
    no STEP). Idempotent; atomic."""
    ref = resolve_model_ref(model)
    body = {
        "kind": PROVENANCE_KIND,
        "schemaVersion": PROVENANCE_SCHEMA_VERSION,
        "model": ref,
        "tree": str(tree or ""),
        "stepHash": str(step_hash or ""),
        "features": list(payload.get("features") or []),
        "components": dict(payload.get("components") or {}),
    }
    write_entry(PROVENANCE_KIND, provenance_key(ref), body)


def read_provenance(model: Path | str) -> dict[str, Any] | None:
    data = read_entry(PROVENANCE_KIND, provenance_key(model))
    if data is None or data.get("kind") != PROVENANCE_KIND or data.get("schemaVersion") != PROVENANCE_SCHEMA_VERSION:
        return None
    return data


def remove_provenance(model: Path | str) -> None:
    remove_entry(PROVENANCE_KIND, provenance_key(model))


# --- where ------------------------------------------------------------------------------------


@dataclasses.dataclass
class Where:
    """What ``store where`` answers for one ref of one document."""

    document: str
    ref: str
    kind: str = "occurrence"
    known: bool = False
    reason: str | None = None
    model: str | None = None
    component: str | None = None
    made: dict[str, Any] | None = None
    modified: dict[str, Any] | None = None
    inputs: list[dict[str, Any]] = dataclasses.field(default_factory=list)

    def unknown(self, reason: str) -> "Where":
        self.known = False
        self.reason = reason
        return self


def where(document: Path | str, ref: str) -> Where:
    """Which feature made, and which last modified, what ``ref`` names in
    ``document``. A ref that cannot be read or that names another file raises
    ``ValueError``; a question the store cannot answer is a ``Where`` that is
    not ``known``, with its ``reason``."""
    from cadgen._internal.step_hash import step_file_hash
    from cadgen.cad_ref_syntax import ensure_ref_file_matches, parse_selector, split_cad_ref
    from cadgen.store.records import model_for_output, read_record
    from cadgen.store.trees import flatten

    path = Path(document).expanduser().resolve()
    answer = Where(document=str(path), ref=str(ref))
    prefix, text = split_cad_ref(ref)
    ensure_ref_file_matches(prefix, str(path), source_label="ref")
    parsed = parse_selector(text)
    if parsed is None or parsed.selector_type == "opaque":
        raise ValueError(f"invalid reference: {ref!r}; pass one occurrence or element ref")
    answer.kind = "occurrence" if parsed.selector_type == "label" else parsed.selector_type
    if not path.is_file():
        return answer.unknown(f"no file at {path}")
    model = model_for_output(path)
    if model is None:
        return answer.unknown("the store does not remember a model writing this file")
    answer.model = model
    record = read_record(model)
    if record is None:
        return answer.unknown("the model has no record; run it")
    entry = read_provenance(model)
    if entry is None:
        return answer.unknown("the model has no provenance entry; run it again")
    if entry.get("tree") != record.get("tree"):
        return answer.unknown("the provenance entry describes another build of the model; run it again")
    if entry.get("stepHash") != step_file_hash(path):
        return answer.unknown("the file's bytes are not the ones this entry describes; run the model")
    document_tree = str(record.get("documentTree") or "")
    flat = flatten(document_tree) if document_tree else None
    if flat is None:
        return answer.unknown("the record names no document tree for this file")

    occurrence_id, token = _resolve(parsed, text, flat)
    answer.ref = "#" + (f"{occurrence_id}/{token}" if token else occurrence_id)
    row = next((occ for occ in flat.get("occurrences") or [] if str(occ.get("id")) == occurrence_id), None)
    if row is None:
        return answer.unknown("a group is made of its occurrences; ask about one of them")
    component = (flat.get("components") or {}).get(str(row.get("component") or "")) or {}
    fingerprint = str(component.get("fingerprint") or "")
    if not fingerprint:
        return answer.unknown("the component has no fingerprint (its geometry could not be read back)")
    answer.component = fingerprint
    found = next(((owner, described) for owner, described in _entries(model, record, entry)
                  if fingerprint in (described.get("components") or {})), None)
    if found is None:
        return answer.unknown("no feature of the model or of its children made this body: it was read "
                              "from a file, or built outside the kernel's constructions")
    answer.model, described = found
    lineage = (described["components"][fingerprint])
    features = described.get("features") or []
    if answer.kind == "occurrence":
        made, modified = lineage.get("made"), None
    elif answer.kind == "vertex":
        return answer.unknown("provenance names faces and edges; a vertex has no feature of its own")
    else:
        element = (lineage.get(answer.kind + "s") or {}).get(token)
        if element is None:
            return answer.unknown("the entry does not name this element: the document's body differs "
                                  "from the one the build held")
        made, modified = element[0], element[1]
    answer.made = _feature(features, made)
    answer.modified = _feature(features, modified)
    if answer.made is None:
        return answer.unknown("this geometry came from outside the run: a file read, or a shape built "
                              "outside the kernel's constructions" + (
                                  f"; last modified by {answer.modified['id']}" if answer.modified else ""))
    answer.inputs = [feature for feature in (_feature(features, index) for index in dict.fromkeys(answer.made.get("inputs") or []))
                     if feature is not None]
    answer.known = True
    return answer


def _resolve(parsed: Any, text: str, flat: dict[str, Any]) -> tuple[str, str]:
    """``(occurrence token, element token or "")`` for a parsed ref against the
    flattened document tree: a label as ``read_scene`` resolves it, a bare
    element token in a one-leaf document."""
    from cadgen.cad_ref_syntax import parse_selector
    from cadgen.label_refs import build_label_aliases, resolve_label_selectors

    occurrences = [str(occ.get("id")) for occ in flat.get("occurrences") or []]
    if parsed.label:
        aliases = build_label_aliases({"id": node.get("id"), "name": node.get("name")} for node in _nodes(flat))
        parsed = parse_selector(resolve_label_selectors([text], aliases)[0])
    occurrence_id = parsed.occurrence_id
    if not occurrence_id:
        if len(occurrences) != 1:
            raise ValueError(f"{text!r} needs an occurrence token in an assembly")
        occurrence_id = occurrences[0]
    if occurrence_id not in {str(node.get("id")) for node in _nodes(flat)}:
        raise ValueError(f"unknown occurrence: #{occurrence_id}")
    return occurrence_id, parsed.token


def _nodes(flat: dict[str, Any]) -> list[dict[str, Any]]:
    root = (flat.get("assembly") or {}).get("root")
    found: list[dict[str, Any]] = []
    pending = [root] if isinstance(root, dict) else []
    while pending:
        node = pending.pop()
        found.append(node)
        pending.extend(reversed([child for child in node.get("children") or [] if isinstance(child, dict)]))
    return found


def _entries(model: str, record: dict[str, Any], entry: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """The model's entry, then each pinned child's while the child's record and
    entry still describe the pinned tree, children first-called first, their
    own children after them."""
    from cadgen.store.records import read_record

    yield model, entry
    seen = {model}
    pending = [record]
    while pending:
        current = pending.pop(0)
        for child in current.get("children") or []:
            child_model, pin = str(child.get("model") or ""), str(child.get("tree") or "")
            if not child_model or child_model in seen:
                continue
            seen.add(child_model)
            child_record = read_record(child_model)
            child_entry = read_provenance(child_model)
            if child_record is None or child_entry is None:
                continue
            if child_record.get("tree") != pin or child_entry.get("tree") != pin:
                continue
            yield child_model, child_entry
            pending.append(child_record)


def _feature(features: list[dict[str, Any]], index: Any) -> dict[str, Any] | None:
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(features):
        return None
    return features[index]


def _frame(feature: dict[str, Any]) -> str:
    return f"{feature.get('file')}:{feature.get('line')} in {feature.get('function')} ({feature.get('op')})"


def describe(answer: Where) -> list[str]:
    """``store where``'s lines for one answer."""
    lines = [f"document  {answer.document}", f"ref       {answer.ref}"]
    if answer.model:
        lines.append(f"model     {answer.model}")
    if not answer.known:
        lines.append(f"unknown   {answer.reason}")
        return lines
    assert answer.made is not None
    lines.append(f"{answer.kind:<9} made by {_frame(answer.made)}   {answer.made.get('id')}")
    if answer.inputs:
        lines.append("          inputs " + ", ".join(str(feature.get("id")) for feature in answer.inputs))
    if answer.kind != "occurrence":
        if answer.modified is not None:
            lines.append(f"          last modified by {_frame(answer.modified)}   {answer.modified.get('id')}")
        else:
            lines.append("          not modified since")
    return lines
