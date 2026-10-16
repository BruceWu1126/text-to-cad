"""One saved document read two ways, for tests.

A build's document tree takes its structure -- products, names, placements, colours --
from the STEP it wrote, and each leaf written from one authored occurrence takes that
occurrence's component, its bytes, construction key and lineage
(``cadgen.store.build.build_tree_through_step``); a cold compile of the same bytes
reads every component from the file. ``assert_same_document`` holds the two to
everything they share."""

from __future__ import annotations

import math
from typing import Any, Mapping


def document_shape(tree: Mapping[str, Any]) -> dict[str, Any]:
    """``tree`` as a reader of its file sees it: each occurrence's component replaced by
    its kind and face colours, and no bounds (compare those with a tolerance:
    :func:`assert_same_document`). Which occurrences share a component is not read:
    a parse shares one prototype among products of equal geometry, a build keeps the
    components it authored."""
    components = tree.get("components") or {}
    occurrences = []
    for occurrence in tree.get("occurrences") or []:
        entry = components.get(str(occurrence.get("component") or "")) or {}
        occurrences.append({**occurrence, "component": [entry.get("kind"), entry.get("faceColors")]})
    shape = {key: value for key, value in tree.items() if key not in ("components", "bbox", "occurrences")}
    shape["occurrences"] = occurrences
    return shape


def assert_same_document(test: Any, warm: Mapping[str, Any], cold: Mapping[str, Any], *, places: float = 1e-6) -> None:
    """A build's document tree ``warm`` and a cold compile ``cold`` of the bytes it wrote
    describe one document: the same structure and appearance, components alike occurrence
    by occurrence, and bounds within ``places``."""
    test.assertEqual(document_shape(warm), document_shape(cold))
    for side in ("min", "max"):
        for built, read in zip((warm.get("bbox") or {}).get(side) or [], (cold.get("bbox") or {}).get(side) or []):
            test.assertTrue(math.isclose(built, read, abs_tol=places), f"bbox {side}: {built} != {read}")
