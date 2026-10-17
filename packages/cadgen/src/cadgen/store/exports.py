"""A model's STEP as an export of its stored result (STORE.md §3, *Exports*).

A build publishes its result tree before anything is written from it. Its STEP is then
written from that tree by the build pool -- the export job, :func:`export_step`, an
artifact job (``daemon/artifacts.py``) whose request names a tree and a staged file, never
a script. The build moves the written file into place and maps its bytes to the tree they
were written from: the file is the result, and nothing parses it to check.

:func:`export_step` materializes the tree (``store.materialize``) and writes it with the
names writer (``step_export.export_build123d_step_file``): each component's part key, on
its product, and its faces' and edges' names, on the entities written for them
(``step_names``). The file is a function of the tree and the writer
(``step_export.STEP_WRITER_SCHEME``) alone.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

__all__ = ["export_step"]


class _Spans:
    """What a writer's logger is told (``CliLogger``'s ``timed`` and ``warning``), kept to
    hand back: each step and the seconds it took, and the warnings, which the build that
    asked relays to its own logger."""

    def __init__(self) -> None:
        self.rows: list[list[Any]] = []
        self.warnings: list[str] = []

    @contextmanager
    def timed(self, label: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            self.rows.append([label, time.perf_counter() - started])

    def warning(self, message: str) -> None:
        self.warnings.append(str(message))

    def debug(self, message: str) -> None:
        pass


def export_step(tree_hash: str, out: Path, *, shapes: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Write the stored tree ``tree_hash`` to ``out`` and return the file's ``sha256``, the
    producer whose part keys it carries (``producer``) and the writer's ``spans`` and
    ``warnings``. ``shapes`` gives the build's own live shapes by cid for components the
    store cannot decode (an eager-only component's), which only the build's own process
    holds (STORE.md §3). A part key names its producer: a runtime that cannot name its own
    writes its elements' names and no part key, under no producer."""
    from cadgen.store.parts import producer_identity
    from cadgen.store.trees import capture_tree

    spans = _Spans()
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        producer = producer_identity()
    except ValueError:
        producer = None
    from cadgen.step_export import export_build123d_step_file
    from cadgen.store.materialize import materialize_descriptor

    with spans.timed("materialize"):
        descriptor, payloads = capture_tree(tree_hash)
        placed: dict[int, str] = {}
        # Written, never handed to model code: untagged, so nothing is captured to tell an
        # edit of it apart (``materialize``'s partner evidence).
        document = materialize_descriptor(descriptor, captured_objects=payloads, shapes=shapes, placed=placed)
    digest = export_build123d_step_file(document, out, logger=spans,
                                        names=_namer(descriptor, payloads, placed, producer))
    return {"sha256": digest, "producer": producer, "spans": spans.rows, "warnings": spans.warnings}


def _namer(descriptor: Mapping[str, Any], payloads: Mapping[str, bytes], placed: Mapping[int, str],
           producer: dict | None) -> Callable[[Any], Any]:
    """What the file names each placed leaf of the materialized ``descriptor`` by: its
    component's part key under ``producer`` (none under no producer) and its lineage's names
    for its faces and edges (``step_names.ComponentNames``); None for a shape no component
    placed."""
    from cadgen._internal.component_package import read_lineage
    from cadgen._internal.step_names import ComponentNames
    from cadgen.store.parts import part_key

    components = descriptor["components"]
    found: dict[str, Any] = {}

    def names(shape: Any) -> Any:
        cid = placed.get(id(shape))
        if cid is None:
            return None
        if cid not in found:
            entry = components[cid]
            lineage = read_lineage(payloads[entry["lineage"]]) if entry.get("lineage") else None
            found[cid] = ComponentNames(part=part_key(entry, producer) if producer is not None else None,
                                        faces=tuple(lineage["faces"]) if lineage else (),
                                        edges=tuple(lineage["edges"]) if lineage else ())
        return found[cid]

    return names
