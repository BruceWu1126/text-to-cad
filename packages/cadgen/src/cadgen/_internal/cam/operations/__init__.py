"""Every operation ``cadgen cam`` knows, by name, in the order the skill and the viewer list them.

The registry is written once, here: all 55 names of the design, each with the phase it is built in, the family
module and class that implement it, and the word the viewer shows. An operation is built when its family module
defines its class: marking one built is writing that class, nothing here changes. Until then
:func:`get_operation` refuses it in a sentence naming its phase, never an ImportError. Stdlib only: the family
modules import lazily.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cadgen._internal.cam.operations.base import Phase
from cadgen._internal.cam.values import json_text

if TYPE_CHECKING:
    from cadgen._internal.cam.operations.base import Operation

__all__ = ["Entry", "OPERATION_NAMES", "REGISTRY", "built", "get_operation"]

_PACKAGE = "cadgen._internal.cam.operations"


@dataclass(frozen=True)
class Entry:
    name: str
    phase: Phase
    module: str            # "cadgen._internal.cam.operations.milling_2d"
    cls: str               # "Pocket2dOperation"
    word: str


def _entry(name: str, phase: Phase, family: str, word: str) -> Entry:
    """``pocket_2d`` in ``milling_2d`` is ``milling_2d.Pocket2dOperation``."""
    cls = "".join(part[:1].upper() + part[1:] for part in name.split("_")) + "Operation"
    return Entry(name, phase, f"{_PACKAGE}.{family}", cls, word)


REGISTRY: dict[str, Entry] = {entry.name: entry for entry in (
    # Phase 1: 2.5D milling.
    _entry("facing", 1, "milling_2d", "Face"),
    _entry("contour_2d", 1, "milling_2d", "Profile"),
    _entry("pocket_2d", 1, "milling_2d", "Pocket"),
    _entry("adaptive_2d", 1, "milling_2d", "Adaptive clearing"),
    _entry("slot", 1, "holes_2d", "Slot"),
    _entry("bore", 1, "holes_2d", "Helical bore"),
    _entry("circular", 1, "holes_2d", "Round pocket or boss"),
    _entry("thread_mill", 1, "holes_2d", "Thread mill"),
    _entry("chamfer_2d", 1, "edges_2d", "Chamfer"),
    _entry("engrave", 1, "edges_2d", "V-carve"),
    _entry("trace", 1, "edges_2d", "Trace"),
    # Phase 1: drilling and hole cycles.
    _entry("drill", 1, "drilling", "Drill"),
    _entry("spot_drill", 1, "drilling", "Spot drill"),
    _entry("peck", 1, "drilling", "Peck drill"),
    _entry("chip_break", 1, "drilling", "Chip-break drill"),
    _entry("tap", 1, "drilling", "Tap"),
    _entry("rigid_tap", 1, "drilling", "Rigid tap"),
    _entry("ream", 1, "drilling", "Ream"),
    _entry("boring_cycle", 1, "drilling", "Boring bar"),
    _entry("counterbore", 1, "drilling", "Counterbore"),
    _entry("back_bore", 1, "drilling", "Back bore"),
    # Phase 2: 3D milling over the stock simulation.
    _entry("adaptive_3d", 2, "milling_3d", "3D adaptive roughing"),
    _entry("pocket_3d", 2, "milling_3d", "Z-level roughing"),
    _entry("parallel", 2, "milling_3d", "Parallel"),
    _entry("contour_3d", 2, "milling_3d", "Waterline"),
    _entry("scallop", 2, "milling_3d", "Scallop"),
    _entry("pencil", 2, "milling_3d", "Pencil"),
    _entry("spiral", 2, "milling_3d", "Spiral"),
    _entry("radial", 2, "milling_3d", "Radial"),
    _entry("morph", 2, "milling_3d", "Morph"),
    _entry("steep_shallow", 2, "milling_3d", "Steep and shallow"),
    _entry("ramp", 2, "milling_3d", "Ramp"),
    _entry("horizontal", 2, "milling_3d", "Flats"),
    _entry("rest", 2, "milling_3d", "Rest machining"),
    # Phase 3: turning.
    _entry("lathe_face", 3, "turning", "Face"),
    _entry("lathe_rough", 3, "turning", "Rough turn"),
    _entry("lathe_finish", 3, "turning", "Finish turn"),
    _entry("lathe_groove", 3, "turning", "Groove"),
    _entry("lathe_part", 3, "turning", "Part off"),
    _entry("lathe_thread", 3, "turning", "Thread"),
    _entry("lathe_bore", 3, "turning", "Bore"),
    _entry("lathe_drill", 3, "turning", "Drill on centre"),
    # Phase 3: sheet cutting.
    _entry("laser_cut", 3, "cutting", "Laser cut"),
    _entry("laser_engrave", 3, "cutting", "Laser engrave"),
    _entry("plasma_cut", 3, "cutting", "Plasma cut"),
    _entry("waterjet_cut", 3, "cutting", "Waterjet cut"),
    # Phase 3: EDM.
    _entry("wire_contour", 3, "edm", "Wire contour"),
    _entry("wire_open", 3, "edm", "Wire open profile"),
    _entry("sinker", 3, "edm", "Sinker"),
    # Phase 4: multi-axis.
    _entry("indexed", 4, "multi_axis", "3+2 indexed"),
    _entry("rotary_wrap", 4, "multi_axis", "Rotary wrap"),
    _entry("rotary_parallel", 4, "multi_axis", "Rotary surface"),
    _entry("swarf", 4, "multi_axis", "Swarf"),
    _entry("multiaxis_contour", 4, "multi_axis", "5-axis contour"),
    _entry("flowline_5x", 4, "multi_axis", "5-axis flowline"),
)}
OPERATION_NAMES: tuple[str, ...] = tuple(REGISTRY)

#: One instance per entry (not per name), so a test that swaps an entry never meets the old instance.
_INSTANCES: dict[Entry, "Operation"] = {}


def _cls(entry: Entry):
    """The entry's class, or ``None`` while its family module does not define it."""
    try:
        module = importlib.import_module(entry.module)
    except ModuleNotFoundError as exc:
        if exc.name == entry.module:
            return None
        raise
    return getattr(module, entry.cls, None)


def built() -> tuple[str, ...]:
    """The names this cadgen has built, in registry order."""
    return tuple(name for name, entry in REGISTRY.items() if _cls(entry) is not None)


def get_operation(name: str) -> "Operation":
    """The operation's instance. An unknown name: ValueError listing every name. A registered name not built
    yet: ValueError 'operation: "adaptive_3d" is phase 2, not built yet; built today: ...'. Never ImportError."""
    entry = REGISTRY.get(name) if isinstance(name, str) else None
    if entry in _INSTANCES:
        return _INSTANCES[entry]
    if entry is None:
        raise ValueError(f"operation: {json_text(name)} is not an operation cadgen knows; use one of {', '.join(OPERATION_NAMES)}")
    cls = _cls(entry)
    if cls is None:
        today = ", ".join(built()) or "none yet"
        raise ValueError(f"operation: {json_text(name)} is phase {entry.phase}, not built yet; built today: {today}")
    instance = cls()
    _INSTANCES[entry] = instance
    return instance
