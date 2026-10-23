"""Document ids: one internal id for every document, and the one canonical spelling of a path.

An id is a lower-case canonical UUID string. A file's is ``file_id``: uuid5 of its canonical
path, inside the engine only -- a file's public name stays its path. A model's is ``model_id``:
uuid5 of its script's canonical path and its function's name, so any process that knows the
script and the function knows the id without asking the engine. Whether a text is an id is
``is_id``; what an id names is the engine's to say, never the uuid's version.

``canonical`` is the one spelling of a path the engine and the viewer key by: ``realpath``,
then, on macOS, each name as the file system spells it (``getattrlist``, which opens nothing), so
a symlink, a ``..`` and a case variant of one file are one key. On Windows ``realpath`` already
resolves the final path. A part of the path that does not exist keeps the spelling it was given
below the deepest part that does. Ids hash the canonical path exactly, with no case folding.

Nothing here reads, builds or persists a document, and nothing imports the kernel.
"""
from __future__ import annotations

import os
import re
import sys
import uuid
from typing import Any

__all__ = ["NAMESPACE", "canonical", "file_id", "is_id", "model_id"]

# cadgen's namespace for derived ids: written once, never changed.
NAMESPACE = uuid.UUID("ad198b15-841c-4d1f-81be-6aa78d2737c0")

_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")


_GETATTRLIST: Any = None


def _getattrlist() -> Any:
    """macOS's ``getattrlist(2)``, bound once; False where it cannot be."""
    global _GETATTRLIST
    if _GETATTRLIST is None:
        try:
            import ctypes

            class AttrList(ctypes.Structure):
                _fields_ = [("bitmapcount", ctypes.c_ushort), ("reserved", ctypes.c_uint16),
                            ("commonattr", ctypes.c_uint32), ("volattr", ctypes.c_uint32),
                            ("dirattr", ctypes.c_uint32), ("fileattr", ctypes.c_uint32),
                            ("forkattr", ctypes.c_uint32)]

            call = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True).getattrlist
            call.argtypes = [ctypes.c_char_p, ctypes.POINTER(AttrList), ctypes.c_void_p, ctypes.c_size_t,
                             ctypes.c_ulong]
            call.restype = ctypes.c_int
            _GETATTRLIST = (call, AttrList, ctypes)
        except (OSError, AttributeError):
            _GETATTRLIST = False
    return _GETATTRLIST


def _spelled(path: str) -> str | None:
    """The name of ``path``'s last part as the file system spells it (macOS: ``getattrlist``'s
    ``ATTR_CMN_NAME``, which opens nothing, so a model body's file reads never hear it); None for
    a path that does not exist, or where it cannot be asked."""
    bound = _getattrlist()
    if not bound:
        return None
    call, AttrList, ctypes = bound
    wanted = AttrList(5, 0, 0x00000001, 0, 0, 0, 0)  # ATTR_BIT_MAP_COUNT, ATTR_CMN_NAME
    answer = ctypes.create_string_buffer(12 + 1024)
    if call(os.fsencode(path), ctypes.byref(wanted), answer, len(answer), 1) != 0:  # FSOPT_NOFOLLOW
        return None
    raw = answer.raw
    offset = int.from_bytes(raw[4:8], sys.byteorder, signed=True)
    length = int.from_bytes(raw[8:12], sys.byteorder)
    return os.fsdecode(raw[4 + offset:4 + offset + length].split(b"\0", 1)[0])


def canonical(path: str | os.PathLike) -> str:
    """The one spelling of ``path`` every document key is made from (the module docstring)."""
    real = os.path.realpath(os.fspath(path))
    if sys.platform != "darwin":
        return real
    parts = real.split("/")[1:]
    spelled = ""
    for at, part in enumerate(parts):
        found = _spelled(f"{spelled}/{part}") if part else None
        if found is None:
            return "/".join([spelled, *parts[at:]])
        # The case alone is the file system's to say; any other difference (a hard link) is not.
        spelled = f"{spelled}/{found if found.casefold() == part.casefold() else part}"
    return spelled or "/"


def file_id(path: str | os.PathLike) -> str:
    """A file's document id: uuid5 of its canonical path."""
    return str(uuid.uuid5(NAMESPACE, "file\0" + canonical(path)))


def model_id(script: str | os.PathLike, function: str) -> str:
    """A model's document id: uuid5 of its script's canonical path and its function's name."""
    return str(uuid.uuid5(NAMESPACE, "model\0" + canonical(script) + "\0" + function))


def is_id(text: object) -> bool:
    """Whether ``text`` is a document id: a lower-case canonical UUID string."""
    return isinstance(text, str) and _ID.fullmatch(text) is not None
