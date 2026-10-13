"""Every post (G-code dialect) ``cadgen cam`` knows, by name: the phase it is built in, the machines it writes for,
and the fewest axes those machines have (a 5-axis post writes rotary moves a 3-axis machine has no axis for).

A job must name its post: a wrong post can crash a machine, so cadgen never guesses one. A post is built when its
module defines its class (``posts/grbl.py`` defining ``GrblPost``): marking one built is writing that class, nothing
here changes. Until then :func:`get_post` refuses it in a sentence naming its phase, as an unbuilt operation is
refused. Stdlib only: the post modules import lazily.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

from cadgen._internal.cam.values import json_text

__all__ = ["POST_NAMES", "PostEntry", "REGISTRY", "built", "get_post"]

_PACKAGE = "cadgen._internal.cam.posts"


@dataclass(frozen=True)
class PostEntry:
    name: str
    phase: int
    kinds: tuple[str, ...]      # the machine kinds it writes for
    fewest_axes: int            # 5 for a 5-axis post
    module: str                 # "cadgen._internal.cam.posts.grbl"
    cls: str                    # "GrblPost"


def _entry(name: str, phase: int, kinds: tuple[str, ...], fewest_axes: int = 2) -> PostEntry:
    cls = "".join(part[:1].upper() + part[1:] for part in name.split("_")) + "Post"
    return PostEntry(name, phase, kinds, fewest_axes, f"{_PACKAGE}.{name}", cls)


REGISTRY: dict[str, PostEntry] = {entry.name: entry for entry in (
    # Phase 1: mills and routers.
    _entry("grbl", 1, ("mill", "router")),
    _entry("linuxcnc", 1, ("mill", "router")),
    _entry("fanuc", 1, ("mill", "router")),
    _entry("haas", 1, ("mill",)),
    _entry("mach3", 1, ("mill", "router")),
    # Phase 3: lathes, cutting and EDM.
    _entry("fanuc_lathe", 3, ("lathe",)),
    _entry("linuxcnc_lathe", 3, ("lathe",)),
    _entry("grbl_laser", 3, ("laser",)),
    _entry("linuxcnc_plasma", 3, ("plasma",)),
    _entry("waterjet", 3, ("waterjet",)),
    _entry("fanuc_wire", 3, ("wire_edm",)),
    _entry("sinker_iso", 3, ("sinker_edm",)),
    # Phase 4: 5-axis, with tool centre point control, and 3+2 through G68.2.
    _entry("linuxcnc_5x", 4, ("mill",), 5),
    _entry("fanuc_5x", 4, ("mill",), 5),
    _entry("haas_umc", 4, ("mill",), 5),
    _entry("fanuc_g68_2", 4, ("mill",), 5),
)}
POST_NAMES: tuple[str, ...] = tuple(REGISTRY)

_INSTANCES: dict[PostEntry, Any] = {}


def _cls(entry: PostEntry):
    """The entry's class, or ``None`` while its module does not define it."""
    try:
        module = importlib.import_module(entry.module)
    except ModuleNotFoundError as exc:
        if exc.name == entry.module:
            return None
        raise
    return getattr(module, entry.cls, None)


def built(kind: str | None = None, axes: int | None = None) -> tuple[str, ...]:
    """The posts this cadgen has built, in registry order; only those for ``kind`` with ``axes`` when given."""
    return tuple(name for name, entry in REGISTRY.items()
                 if (kind is None or kind in entry.kinds) and (axes is None or entry.fewest_axes <= axes)
                 and _cls(entry) is not None)


def get_post(name: str):
    """The post's instance. An unknown name, or one not built yet: ValueError in a sentence, never ImportError."""
    entry = REGISTRY.get(name) if isinstance(name, str) else None
    if entry in _INSTANCES:
        return _INSTANCES[entry]
    if entry is None:
        raise ValueError(f"post: {json_text(name)} is not a post cadgen knows; built today: {', '.join(built()) or 'none yet'}")
    cls = _cls(entry)
    if cls is None:
        raise ValueError(f"post: {json_text(name)} is phase {entry.phase}, not built yet; "
                         f"built today: {', '.join(built()) or 'none yet'}")
    instance = cls()
    _INSTANCES[entry] = instance
    return instance
