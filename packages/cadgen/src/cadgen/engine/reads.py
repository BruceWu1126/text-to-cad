"""The files a model reads through the engine: ``read_bytes``, ``read_text``, ``read_json``.

Each is one operation of the memo (``kernel.memo.wrap_function``), keyed by the file's
bytes as ``read_step`` is, wherever the file is: a body that reads its configuration
through them stays retained, and runs again only when that file's bytes change. The
bytes are what the memo holds; text and JSON are decoded from them on every call, so
each call hands back an object of its own, which the body may change. A file a body
opens past them (``open``, ``Path.read_text``, ``np.loadtxt``) makes it run on every
build (``kernel/memo.py``, ``_audited``).
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable

__all__ = ["read_bytes", "read_json", "read_text"]

_READ: Callable[[str], bytes] | None = None


def _read(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def read_bytes(path: Any) -> bytes:
    """A file's bytes. Inside a build, read once per version of the file: a later read of
    the same bytes is the memo's hit, and a body that reads it replays while they stay."""
    global _READ
    if _READ is None:
        from cadgen.engine.kernel.memo import wrap_function

        _READ = wrap_function("read_bytes", _read)
    return _READ(os.fspath(path))


def read_text(path: Any, encoding: str = "utf-8") -> str:
    """A file's text (``read_bytes``, decoded)."""
    return read_bytes(path).decode(encoding)


def read_json(path: Any) -> Any:
    """A JSON file's value (``read_bytes``, parsed): a new object on every call."""
    return json.loads(read_bytes(path))
