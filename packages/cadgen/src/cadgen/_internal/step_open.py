"""Open a STEP document and pick the part occurrence a verb works on, for ``cadgen fea`` and ``cadgen cam``.

Stdlib only at import: :func:`cadgen.step_scene.read_scene` loads the kernel when it is called.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cadgen.step_scene import Selection, StepScene

__all__ = ["open_scene", "pick_occurrence"]


def open_scene(target: Path) -> "StepScene":
    """The document at ``target`` (a .step or .stp), opened as a scene."""
    from cadgen._internal.doors import STEP_SUFFIXES, document_target
    from cadgen.step_scene import read_scene

    return read_scene(document_target(target, suffixes=STEP_SUFFIXES))


def pick_occurrence(scene: "StepScene", occurrence: str | None) -> "Selection":
    """The occurrence ``--occurrence`` names, else the document's only part (an error naming the choices if it has
    several)."""
    if occurrence:
        owner = scene.resolve(occurrence)
        if owner.kind != "occurrence":
            raise ValueError(f"{occurrence} is not an occurrence reference")
        return owner
    leaves = list(scene.leaves())
    if len(leaves) != 1:
        raise ValueError(
            f"the document has {len(leaves)} part occurrences; pass --occurrence "
            f"{' or '.join(leaf.ref for leaf in leaves[:4])}{' ...' if len(leaves) > 4 else ''}"
        )
    return leaves[0]
