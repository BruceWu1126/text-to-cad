"""The pinned occurrence ids of src/tom.py, checked against the tree the build makes.

The original module also generated the arm's animation clips (showcase, wrist, approach) from the
control showcase's beat table; the engine has no animation, so the clips, their table
(showcase_keys.json) and the generator are not ported. What stays is the check: an occurrence id
is a tree position (the root is o1, each child appends its 1-based index), and the ids pinned in
tom_occurrence_ids.json must still name the labelled occurrences, or the build fails.
"""
from __future__ import annotations

import json
from pathlib import Path

__all__ = ["occurrence_ids", "check_occurrence_ids"]

HERE = Path(__file__).resolve().parent
IDS_PATH = HERE / "tom_occurrence_ids.json"


def occurrence_ids() -> dict[str, str]:
    return json.loads(IDS_PATH.read_text())


def _id_key(oid: str) -> tuple[int, ...]:
    return tuple(int(x) for x in oid[1:].split("."))


def check_occurrence_ids(assembly, ids: dict[str, str] | None = None) -> None:
    """Fail the build when the pinned ids no longer name the labelled occurrences.

    Occurrence ids are tree positions: the root is o1 and each child appends its 1-based index.
    """
    ids = occurrence_ids() if ids is None else ids
    found: dict[str, str] = {}

    def walk(shape, oid: str) -> None:
        if shape.label in ids and shape.label not in found:
            found[shape.label] = oid
        for index, child in enumerate(shape.children or (), 1):
            walk(child, f"{oid}.{index}")

    walk(assembly, "o1")
    wrong = {label: (pinned, found.get(label)) for label, pinned in ids.items() if found.get(label) != pinned}
    if wrong:
        current = dict(sorted(found.items(), key=lambda kv: _id_key(kv[1])))
        raise ValueError(f"showcase occurrence ids moved {wrong}; write this to {IDS_PATH}:\n"
                         + json.dumps(current, indent=1))
