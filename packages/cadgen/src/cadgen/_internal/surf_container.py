"""The SURF container, read without the kernel.

``cadgen._internal.surface_extract`` writes a component's SURF: ``SURF``, the
format version and the JSON index's length (two little-endian u32), the JSON
index (faces, edges, shapes, their metrics and boxes), then the binary payload
the index points into. Reading it is plain bytes and JSON, so a process that
only reads -- the build pool checking an eager-only component's pinned surface,
the CAD Viewer's server -- reads it here and never loads OCCT. The store keeps
no SURF but that pinned one: a part's display data is derived from one extracted
in memory (``cadgen.store.parts``).
"""

from __future__ import annotations

import json
import struct

__all__ = ["SURF_MAGIC", "SURF_VERSIONS_READ", "read_surf", "validate_surface_bytes"]

SURF_MAGIC = b"SURF"
# The formats every reader reads (``surface_extract.SURF_VERSION`` writes the newest):
# an older build may have pinned a format 2 surface in an eager-only component's
# geometry identity, and format 3 only removed fields.
SURF_VERSIONS_READ = (2, 3)


def read_surf(data: bytes) -> tuple[dict, memoryview]:
    """``(index, payload)`` of a SURF container's bytes; ``ValueError`` for anything else."""
    if data[:4] != SURF_MAGIC:
        raise ValueError("not a SURF container")
    _version, json_len = struct.unpack_from("<II", data, 4)
    index = json.loads(data[12:12 + json_len].decode("utf-8"))
    return index, memoryview(data)[12 + json_len:]


def validate_surface_bytes(payload: bytes) -> dict:
    """The index of a SURF container's bytes, checked to be one of a format this cadgen
    reads with whole ordinal tables; ``ValueError`` for anything else."""
    if len(payload) < 12 or payload[:4] != SURF_MAGIC:
        raise ValueError("invalid SURF container")
    version, size = struct.unpack_from("<II", payload, 4)
    if version not in SURF_VERSIONS_READ or size > len(payload) - 12 or (len(payload) - 12 - size) % 4:
        raise ValueError("invalid SURF version/length")
    index = json.loads(payload[12:12 + size])
    if type(index) is not dict or index.get("version") != version:
        raise ValueError("invalid SURF index")
    for name in ("faces", "edges"):
        rows = index.get(name)
        if type(rows) is not list or any(type(row) is not dict for row in rows) or [row.get("ord") for row in rows] != list(range(1, len(rows) + 1)):
            raise ValueError("invalid SURF ordinal table")
        if type(index.get("counts")) is not dict or index["counts"].get(name) != len(rows):
            raise ValueError("invalid SURF counts")
    return index
