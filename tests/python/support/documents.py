"""One saved document read two ways, for tests.

A build's document tree takes its structure -- products, names, placements, colours --
from the STEP it wrote, and each leaf written from one authored occurrence that reads
back as it takes that occurrence's component, its bytes, construction key and lineage
(``cadgen.store.build.build_tree_through_step``); a cold compile of the same bytes
reads every component from the file. ``assert_same_document`` holds the two to
everything they share."""

from __future__ import annotations

import math
from typing import Any, Mapping


def document_shape(tree: Mapping[str, Any]) -> dict[str, Any]:
    """``tree`` (a document tree, or one flattened) as a reader of its file sees it: each
    occurrence's component replaced by its kind, and no bounds or face colours (compare
    those with a tolerance: :func:`assert_same_document`) and no tree hash. Which
    occurrences share a component is not read: a parse shares one prototype among
    products of equal geometry, a build keeps the components it authored."""
    components = tree.get("components") or {}
    occurrences = []
    for occurrence in tree.get("occurrences") or []:
        entry = components.get(str(occurrence.get("component") or "")) or {}
        occurrences.append({**occurrence, "component": entry.get("kind")})
    shape = {key: value for key, value in tree.items() if key not in ("components", "bbox", "occurrences", "tree")}
    shape["occurrences"] = occurrences
    return shape


def face_colors(tree: Mapping[str, Any]) -> list[dict[str, list[float]]]:
    """Each occurrence's component's face colours, in occurrence order."""
    components = tree.get("components") or {}
    return [dict((components.get(str(occurrence.get("component") or "")) or {}).get("faceColors") or {})
            for occurrence in tree.get("occurrences") or []]


def assert_same_document(test: Any, warm: Mapping[str, Any], cold: Mapping[str, Any], *, places: float = 1e-6) -> None:
    """A build's document tree ``warm`` and a cold compile ``cold`` of the bytes it wrote
    describe one document: the same structure and appearance, components alike occurrence
    by occurrence, face colours to the single precision a STEP reader keeps, and bounds
    within ``places``."""
    test.assertEqual(document_shape(warm), document_shape(cold))
    for built, read in zip(face_colors(warm), face_colors(cold)):
        test.assertEqual(sorted(map(str, built)), sorted(map(str, read)))
        for ordinal, color in built.items():
            for a, b in zip(color, read.get(ordinal, read.get(str(ordinal)))):
                test.assertTrue(math.isclose(a, b, abs_tol=1e-6), f"face {ordinal}: {color} != {read}")
    for side in ("min", "max"):
        for built, read in zip((warm.get("bbox") or {}).get(side) or [], (cold.get("bbox") or {}).get(side) or []):
            test.assertTrue(math.isclose(built, read, abs_tol=places), f"bbox {side}: {built} != {read}")
