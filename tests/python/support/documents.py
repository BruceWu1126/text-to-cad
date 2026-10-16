"""One saved document read two ways, for tests.

A build's saved document is its result, the tree its STEP was exported from
(``cadgen.store.exports``); a cold compile of the same bytes reads every component from
the file. ``assert_same_document`` holds the two to everything they share, the colours
as drawn."""

from __future__ import annotations

import math
import struct
from typing import Any, Mapping


def document_shape(tree: Mapping[str, Any]) -> dict[str, Any]:
    """``tree`` (a document tree, or one flattened) as a reader of its file sees it: each
    occurrence's component replaced by its kind, and no bounds or face colours (compare
    those with a tolerance: :func:`assert_same_document`) and no tree hash, its colours to
    the single precision a STEP reader keeps. Which
    occurrences share a component is not read: a parse shares one prototype among
    products of equal geometry, a build keeps the components it authored."""
    components = tree.get("components") or {}
    occurrences = []
    for occurrence in tree.get("occurrences") or []:
        entry = components.get(str(occurrence.get("component") or "")) or {}
        shaped = {**occurrence, "component": entry.get("kind")}
        if occurrence.get("color") is not None:
            # A STEP reader keeps a colour to single precision.
            shaped["color"] = [struct.unpack("f", struct.pack("f", float(channel)))[0]
                               for channel in occurrence["color"]]
        occurrences.append(shaped)
    shape = {key: value for key, value in tree.items() if key not in ("components", "bbox", "occurrences", "tree")}
    shape["occurrences"] = occurrences
    return shape


def face_colors(tree: Mapping[str, Any]) -> list[tuple[dict[str, list[float]], Any]]:
    """Each occurrence's component's face colours and the occurrence's colour, in
    occurrence order."""
    components = tree.get("components") or {}
    return [(dict((components.get(str(occurrence.get("component") or "")) or {}).get("faceColors") or {}),
             occurrence.get("color"))
            for occurrence in tree.get("occurrences") or []]


def assert_same_document(test: Any, warm: Mapping[str, Any], cold: Mapping[str, Any], *, places: float = 1e-6) -> None:
    """A build's document tree ``warm`` and a cold compile ``cold`` of the bytes it wrote
    describe one document: the same structure and appearance, components alike occurrence
    by occurrence, drawn in the same colours face by face to the single precision a STEP
    reader keeps -- the same face colours, or, where the build's leaf is the occurrence
    it was written from, coloured as a whole, the compile's every face in that colour (a
    reader returns a placement's colour on its faces) -- and bounds within ``places``."""
    test.assertEqual(document_shape(warm), document_shape(cold))
    for (built, placed), (read, _read_placed) in zip(face_colors(warm), face_colors(cold)):
        if not built and read and placed is not None:
            for ordinal, color in read.items():
                test.assertTrue(all(math.isclose(a, b, abs_tol=1e-6) for a, b in zip(color, placed)),
                                f"face {ordinal}: {color} is not the occurrence's {placed}")
            continue
        test.assertEqual(sorted(map(str, built)), sorted(map(str, read)))
        for ordinal, color in built.items():
            for a, b in zip(color, read.get(ordinal, read.get(str(ordinal)))):
                test.assertTrue(math.isclose(a, b, abs_tol=1e-6), f"face {ordinal}: {color} != {read}")
    for side in ("min", "max"):
        for built, read in zip((warm.get("bbox") or {}).get(side) or [], (cold.get("bbox") or {}).get(side) or []):
            test.assertTrue(math.isclose(built, read, abs_tol=places), f"bbox {side}: {built} != {read}")
