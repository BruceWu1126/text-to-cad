"""The job file: what ``cadgen cam plan`` is asked to make.

A job is JSON (a path, an inline JSON string, or a dict from a library caller) of this shape::

    {
      "machine": {"kind": "mill", "axes": 3, "post": "grbl", "travel_mm": [800, 600, 120],
                  "max_rpm": 24000, "max_feed_mm_min": 5000, "rapid_mm_min": 5000, "units": "mm"},
      "material": "aluminum-6061",                 # or {"name", "surface_m_min", "chipload_mm", "hardness_hb"}
      "stock": {"type": "box", "margin_mm": [2, 2, 1, 1]},   # box | cylinder | sheet | {"from": "stock.step"}
      "setups": [{"id": "top", "direction": "+Z", "origin": "stock_top_center", "clearance_mm": 10, "retract_mm": 3,
                  "fixture": {"type": "vise", "jaw_height_mm": 20, "grip_mm": 5}}],
      "tools": [{"id": "T1", "type": "flat_endmill", "diameter_mm": 6, "flutes": 3, "flute_length_mm": 18,
                 "stickout_mm": 30, "holder": {"diameter_mm": 32, "length_mm": 40}, "material": "carbide"}],
      "operations": [{"op": "pocket_2d", "setup": "top", "tool": "T1", "features": ["#o1.f12"]}],
      "view": {"show": {"rapids": true, "stock": "final"}}
    }

Every key and every enum is a closed vocabulary (README law 9): an unknown one is a ValueError naming its path
(``operations[2].stepover: ...``). ``machine.post`` is required and never guessed: a wrong post can crash a
machine; a post not built yet is refused like an operation not built yet. An operation's own keys are read by the
operation (``operations/``); this module reads the keys every operation shares (:data:`COMMON_KEYS`) and checks
what an operation names exists and suits it: its setup, its tool (a tap cannot pocket) and the machine.

Units: mm, mm/min, m/min, rpm, everywhere in the job; ``machine.units: "inch"`` changes only what the G-code is
written in. Box stock's margin is ``[x, y, z_top, z_bottom]``: added on each side in x and in y, above the part and
below it. A setup's ``clearance_mm`` (default 10) is the height above the stock top the tool moves between features
at, and ``retract_mm`` (default 3) the height above the stock top it lifts to between passes; an operation may
override either. The ``*_corner`` origins are the min-X, min-Y corner (front left, looking down the tool).

Stdlib only, so a malformed job fails in milliseconds, before any part is opened.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cadgen._internal.cam.values import an, choice, flag, json_text, known, number, obj, selectors, whole, words

__all__ = [
    "AXIS_DIRECTIONS", "COMMON_KEYS", "COOLANTS", "DEFAULT_CLEARANCE_MM", "DEFAULT_RETRACT_MM", "FIXTURES", "Fixture",
    "Holder", "Job", "MACHINE_KINDS", "MATERIALS", "Machine", "Material", "ORIGINS", "Operation", "STOCK_TYPES",
    "Setup", "Stock", "TOOL_MATERIALS", "TOOL_TYPES", "Tool", "parse_job",
]

JOB_KEYS = ("machine", "material", "stock", "setups", "tools", "operations", "view")

# --- machine -------------------------------------------------------------------------------------------

#: Each machine kind and the axis counts it may have (the first is its default).
MACHINE_KINDS: dict[str, tuple[int, ...]] = {
    "mill": (3, 4, 5), "router": (3, 4, 5), "lathe": (2,), "laser": (2,), "plasma": (2,), "waterjet": (2,),
    "wire_edm": (2, 4), "sinker_edm": (3,),
}
MACHINE_KEYS = ("kind", "axes", "post", "travel_mm", "max_rpm", "max_feed_mm_min", "rapid_mm_min", "units",
                "spindle_spinup_s")
UNITS = ("mm", "inch")

# --- material, stock, setups -----------------------------------------------------------------------------

#: The material table's names (feeds.py holds their numbers, surface speed in m/min).
MATERIALS = ("aluminum-6061", "aluminum-7075", "mild-steel", "stainless-304", "brass", "acetal", "hdpe", "acrylic",
             "mdf", "plywood", "hardwood")
MATERIAL_KEYS = ("name", "surface_m_min", "chipload_mm", "hardness_hb")
_CUSTOM_MATERIAL = '{"name", "surface_m_min", "chipload_mm"}'
#: Each stock type and the keys it takes besides ``type``.
STOCK_TYPES: dict[str, tuple[str, ...]] = {
    "box": ("margin_mm",),
    "cylinder": ("margin_mm", "diameter_mm", "axis"),
    "sheet": ("margin_mm", "thickness_mm"),
}
#: The stock each machine kind starts from (``from``: a STEP of the blank).
STOCK_FOR_KIND: dict[str, tuple[str, ...]] = {
    "mill": ("box", "cylinder", "from"), "router": ("box", "cylinder", "sheet", "from"), "lathe": ("cylinder", "from"),
    "laser": ("sheet",), "plasma": ("sheet",), "waterjet": ("sheet",), "wire_edm": ("box", "sheet", "from"),
    "sinker_edm": ("box", "cylinder", "from"),
}
AXES = ("X", "Y", "Z")
AXIS_DIRECTIONS = ("+X", "-X", "+Y", "-Y", "+Z", "-Z")
#: Where a setup's work coordinate zero sits, by keyword; or ``[x, y, z]`` in the part's frame. A ``*_corner`` is
#: the min-X, min-Y corner (front left).
ORIGINS = ("stock_top_center", "stock_top_corner", "part_bbox_top_center", "part_bbox_top_corner",
           "part_bbox_center", "part_bbox_bottom_center", "part_bbox_bottom_corner")
SETUP_KEYS = ("id", "direction", "origin", "clearance_mm", "retract_mm", "fixture")
#: Heights above the stock top, mm: where the tool moves between features, and where it lifts between passes.
DEFAULT_CLEARANCE_MM = 10.0
DEFAULT_RETRACT_MM = 3.0
#: Each fixture type and the sizes it takes (mm) besides ``type``; collisions are checked against it. ``grip_mm`` is
#: how far the jaws reach up the stock from its bottom; ``parallels_mm`` the height of the parallels the stock sits
#: on in a vise, whose grip is its ``jaw_height_mm`` less them (``setups`` holds the model).
FIXTURES: dict[str, tuple[str, ...]] = {
    "vise": ("jaw_height_mm", "jaw_width_mm", "grip_mm", "parallels_mm", "jaw_thickness_mm", "parallel_width_mm",
             "floor_thickness_mm"),
    "plate": ("thickness_mm", "border_mm"),
    "chuck": ("diameter_mm", "jaw_length_mm", "grip_mm"),
    "collet": ("diameter_mm", "length_mm", "grip_mm"),
    "sheet": (),
    "none": (),
}
#: Fixture sizes that may be 0: a vise's stock may sit on its floor (no parallels), a plate may end at the stock.
FIXTURE_ZERO_OK = ("parallels_mm", "border_mm")
#: The fixtures a machine kind holds work in, where it is narrower than every fixture.
FIXTURES_FOR_KIND: dict[str, tuple[str, ...]] = {
    "lathe": ("chuck", "collet", "none"), "laser": ("sheet", "none"), "plasma": ("sheet", "none"),
    "waterjet": ("sheet", "none"),
}

# --- tools ---------------------------------------------------------------------------------------------

TOOL_MATERIALS = ("carbide", "hss")
#: Keys every tool held in a holder takes.
TOOL_COMMON = ("id", "type", "material", "stickout_mm", "holder", "comment")
#: Keys a beam, jet or wire takes: it has no holder, stickout or tool material.
KERF_COMMON = ("id", "type", "comment")
KERF_TOOLS = ("laser", "plasma", "waterjet", "wire")
#: A rotating cutter's optional keys.
_CUTTER = ("flutes", "flute_length_mm", "shank_diameter_mm")
#: Each tool type: (the keys it needs, the keys it may take), beyond :data:`TOOL_COMMON` (or :data:`KERF_COMMON`).
TOOL_TYPES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # Milling cutters.
    "flat_endmill": (("diameter_mm",), _CUTTER),
    "face_mill": (("diameter_mm",), _CUTTER),
    "ball_endmill": (("diameter_mm",), _CUTTER),
    "bull_endmill": (("diameter_mm", "corner_radius_mm"), _CUTTER),
    "chamfer_mill": (("diameter_mm", "tip_angle_deg"), (*_CUTTER, "tip_diameter_mm")),
    "v_bit": (("diameter_mm", "tip_angle_deg"), (*_CUTTER, "tip_diameter_mm")),
    "thread_mill": (("diameter_mm", "pitch_mm"), _CUTTER),
    "slitting_saw": (("diameter_mm", "width_mm"), ("flutes", "shank_diameter_mm")),
    # Hole makers.
    "drill": (("diameter_mm",), (*_CUTTER, "tip_angle_deg")),
    "spot_drill": (("diameter_mm",), (*_CUTTER, "tip_angle_deg")),
    "tap": (("diameter_mm", "pitch_mm"), _CUTTER),
    "reamer": (("diameter_mm",), _CUTTER),
    "boring_bar": (("diameter_mm",), ("flute_length_mm", "shank_diameter_mm")),
    "back_bore": (("diameter_mm",), ("flute_length_mm", "shank_diameter_mm")),
    "counterbore": (("diameter_mm",), (*_CUTTER, "pilot_diameter_mm")),
    # Lathe inserts.
    "turning_insert": (("nose_radius_mm",), ()),
    "grooving_insert": (("width_mm",), ("nose_radius_mm",)),
    "parting_blade": (("width_mm",), ()),
    "threading_insert": ((), ("nose_radius_mm",)),
    # Beams, jets and wires: a kerf, not a cutter.
    "laser": (("kerf_mm",), ()),
    "plasma": (("kerf_mm",), ()),
    "waterjet": (("kerf_mm",), ()),
    "wire": (("diameter_mm",), ()),
    "electrode": ((), ("undersize_mm",)),
}
_TOOL_ID = re.compile(r"T([1-9][0-9]{0,3})")

# --- operations and view -------------------------------------------------------------------------------

#: The keys every operation takes; the rest are the operation's own (its ``keys``).
COMMON_KEYS = ("op", "setup", "tool", "features", "region", "all", "rpm", "feed_mm_min", "plunge_mm_min",
               "stock_to_leave_mm", "coolant", "clearance_mm", "retract_mm")
COOLANTS = ("off", "flood", "mist", "air")
VIEW_KEYS = ("show",)
#: What the viewer draws: booleans, and which stock (``none`` hides it).
SHOW_KEYS = ("rapids", "stock", "fixtures", "tool")
SHOW_STOCK = ("initial", "final", "none")


@dataclass(frozen=True)
class Machine:
    kind: str
    axes: int
    post: str
    #: The machine's travel, mm: ``(x, y, z)``, or ``(x, z)`` for a lathe; ``None`` when not stated.
    travel_mm: tuple[float, ...] | None = None
    max_rpm: float | None = None
    max_feed_mm_min: float | None = None
    rapid_mm_min: float | None = None
    units: str = "mm"
    #: Seconds to wait after the spindle starts before cutting (G4 after M3); ``None``: the post's default (3 s on
    #: grbl, which does not wait for the spindle; 0 on the controllers that do).
    spindle_spinup_s: float | None = None


@dataclass(frozen=True)
class Material:
    name: str
    #: Surface speed (m/min) and chip load (mm per tooth), set only when the job states them.
    surface_m_min: float | None = None
    #: A chip load for every tool, or ``((diameter_mm, chip load), ...)`` by tool diameter, smallest first.
    chipload_mm: float | tuple[tuple[float, float], ...] | None = None
    hardness_hb: float | None = None

    @property
    def table(self) -> bool:
        """The material is one of the table's (feeds.py reads its numbers)."""
        return self.name in MATERIALS


@dataclass(frozen=True)
class Stock:
    type: str                                   # box | cylinder | sheet | from
    #: box ``(x, y, z_top, z_bottom)``, cylinder ``(radial, axial)``, sheet ``(x, y)``; zero when not stated.
    margin_mm: tuple[float, ...] = ()
    diameter_mm: float | None = None            # cylinder: the bar size, when stated (then no radial margin)
    axis: str = "Z"                             # cylinder: its axis in the part's frame
    thickness_mm: float | None = None           # sheet: when stated, else the part's
    path: Path | None = None                    # from: the blank's STEP


@dataclass(frozen=True)
class Fixture:
    type: str
    #: Its sizes, mm, by key (``jaw_height_mm``, ``grip_mm``).
    size: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Setup:
    id: str
    #: The tool axis in the part's frame: ``+Z``, ... or a face selector the tool looks along the normal of.
    direction: str = "+Z"
    #: An :data:`ORIGINS` keyword, or a point ``(x, y, z)`` in the part's frame, mm.
    origin: str | tuple[float, float, float] = "stock_top_center"
    fixture: Fixture = Fixture("none")
    #: Heights above the stock top, mm (see the module's docstring).
    clearance_mm: float = DEFAULT_CLEARANCE_MM
    retract_mm: float = DEFAULT_RETRACT_MM


@dataclass(frozen=True)
class Holder:
    diameter_mm: float
    length_mm: float


@dataclass(frozen=True)
class Tool:
    id: str                                     # "T1"
    number: int                                 # 1: the machine's tool table slot
    type: str
    #: ``None`` for a beam, jet or wire.
    material: str | None = "carbide"
    diameter_mm: float | None = None
    flutes: int | None = None
    flute_length_mm: float | None = None
    stickout_mm: float | None = None
    shank_diameter_mm: float | None = None
    holder: Holder | None = None
    #: Its type's own sizes, by key (``corner_radius_mm``, ``tip_angle_deg``, ``pitch_mm``, ``kerf_mm``, ...).
    size: dict = field(default_factory=dict)
    comment: str | None = None


@dataclass(frozen=True)
class Operation:
    op: str
    setup: str                                  # a Setup.id
    tool: str                                   # a Tool.id
    features: tuple[str, ...] = ()
    #: A closed outline in the setup's plane, mm: ``((x, y), ...)``.
    region: tuple[tuple[float, float], ...] | None = None
    all: bool = False
    rpm: float | None = None
    feed_mm_min: float | None = None
    plunge_mm_min: float | None = None
    stock_to_leave_mm: float = 0.0
    coolant: str = "off"
    #: The setup's heights, or the operation's own when it overrides them.
    clearance_mm: float = DEFAULT_CLEARANCE_MM
    retract_mm: float = DEFAULT_RETRACT_MM
    #: What the operation's own ``parse`` made of its keys.
    params: Any = None


@dataclass(frozen=True)
class Job:
    machine: Machine
    material: Material
    stock: Stock
    setups: tuple[Setup, ...]
    tools: tuple[Tool, ...]
    operations: tuple[Operation, ...]
    #: What the viewer draws (``{"show": {...}}``), checked; ``None`` for its defaults.
    view: dict | None = None
    #: The document as plain JSON (a copy, never the caller's), echoed into the sidecar so a result names its inputs.
    source: dict = field(default_factory=dict, compare=False)

    def setup(self, setup_id: str) -> Setup:
        return next(setup for setup in self.setups if setup.id == setup_id)

    def tool(self, tool_id: str) -> Tool:
        return next(tool for tool in self.tools if tool.id == tool_id)


# --- loading -------------------------------------------------------------------------------------------

def _refuse_constant(name: str):
    raise ValueError(f"{name} is not a number a job can hold")


def _parse_text(text: str, what: str) -> Any:
    try:
        return json.loads(text, parse_constant=_refuse_constant)
    except ValueError as exc:  # JSONDecodeError is a ValueError
        raise ValueError(f"{what} does not parse as JSON: {exc}") from exc


def _load_document(job: str | dict | Path) -> tuple[dict, Path]:
    """The job document as plain JSON, and the folder its relative paths (``stock.from``) are read from.

    A dict is copied through JSON, so it is read exactly as the same job in a file would be (tuples become lists),
    NaN and infinity are refused, and nothing parsed shares the caller's lists.
    """
    if isinstance(job, dict):
        try:
            text = json.dumps(job, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"job: not plain JSON: {exc}") from exc
        return json.loads(text), Path.cwd()
    if isinstance(job, Path):
        return _read_json_file(job), job.parent
    text = str(job).strip()
    if text.startswith("{"):
        return _parse_text(text, "job: inline JSON"), Path.cwd()
    path = Path(text).expanduser()
    return _read_json_file(path), path.parent


def _read_json_file(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"job file does not exist: {path}")
    document = _parse_text(path.read_text(encoding="utf-8"), f"job file {path}")
    if not isinstance(document, dict):
        raise ValueError(f"job file {path} must hold one JSON object")
    return document


def _entries(document: dict, key: str, example: str) -> list[dict]:
    """``document[key]``, a non-empty list of objects."""
    raw = document.get(key)
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{key}: expected a list like [{example}]")
    for index, entry in enumerate(raw):
        obj(entry, where=f"{key}[{index}]", example=example)
    return raw


def _id(value: Any, *, where: str, example: str) -> str:
    """A setup's name: text, with no space around it (operations name it exactly)."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}: name it, like {example}; operations name it")
    if value != value.strip():
        raise ValueError(f"{where}: {json_text(value)} has space around it; write {json_text(value.strip())}")
    return value


def _sizes(entry: dict, keys, *, where: str) -> dict:
    """The positive sizes (mm, degrees) among ``keys`` that ``entry`` states."""
    return {key: number(entry[key], where=f"{where}.{key}", positive=True) for key in keys if key in entry}


def _margins(value: Any, *, where: str, count: int, names: str, single: bool) -> tuple[float, ...]:
    """A list of ``count`` margins, each zero or more; or, where ``single``, one number for all of them."""
    if single and not isinstance(value, list):
        return (number(value, where=where, minimum=0),) * count
    if not isinstance(value, list) or len(value) != count:
        either = "one margin for every side, or " if single else ""
        raise ValueError(f"{where}: {either}{names} in mm, got {json_text(value)}")
    return tuple(number(item, where=f"{where}[{index}]", minimum=0) for index, item in enumerate(value))


def _heights(entry: dict, where: str, clearance: float, retract: float) -> tuple[float, float]:
    """``clearance_mm`` and ``retract_mm`` above the stock top, each defaulting to the one given: retract at or under
    clearance, both above the stock."""
    if "clearance_mm" in entry:
        clearance = number(entry["clearance_mm"], where=f"{where}.clearance_mm", positive=True)
    if "retract_mm" in entry:
        retract = number(entry["retract_mm"], where=f"{where}.retract_mm", positive=True)
    if retract > clearance:
        key = "retract_mm" if "retract_mm" in entry else "clearance_mm"
        raise ValueError(f"{where}.{key}: the retract height ({retract:g} mm) must be at or under the clearance height "
                         f"({clearance:g} mm), both above the stock top")
    return clearance, retract


# --- sections ------------------------------------------------------------------------------------------

def _machine(raw: Any, post_override: str | None) -> Machine:
    from cadgen._internal.cam.posts import REGISTRY as POSTS, built, get_post

    machine = obj(raw, where="machine", example='{"kind": "mill", "post": "grbl"}')
    known(machine, MACHINE_KEYS, where="machine", takes=f"a machine takes {', '.join(MACHINE_KEYS)}")
    if "kind" not in machine:
        raise ValueError(f"machine.kind: required, one of {words(MACHINE_KINDS)}")
    kind = choice(machine["kind"], MACHINE_KINDS, where="machine.kind")
    allowed = MACHINE_KINDS[kind]
    axes = whole(machine.get("axes", allowed[0]), where="machine.axes", low=2, high=5)
    if axes not in allowed:
        raise ValueError(f"machine.axes: a {kind} has {words(allowed)} axes, got {axes}")
    today = ", ".join(built(kind, axes)) or "none yet"
    post = post_override if post_override is not None else machine.get("post")
    where = "--post" if post_override is not None else "machine.post"
    if post is None:
        raise ValueError(f"machine.post: required, and never guessed: a wrong post can crash a machine. Name the "
                         f"controller's dialect (or pass --post); built today for a {kind}: {today}")
    entry = POSTS.get(post) if isinstance(post, str) else None
    if entry is None:
        raise ValueError(f"{where}: {json_text(post)} is not a post cadgen knows; built today for a {kind}: {today}")
    if kind not in entry.kinds:
        raise ValueError(f"{where}: {post} writes for a {words(entry.kinds)}, not a {kind}; built today for a {kind}: {today}")
    if entry.fewest_axes > axes:
        raise ValueError(f"{where}: {post} writes for a {entry.fewest_axes}-axis {kind}, and this one has {axes} axes; "
                         f"built today for it: {today}")
    try:
        get_post(post)
    except ValueError as exc:
        raise ValueError(f"{where}: {str(exc).removeprefix('post: ')}") from None
    travel = None
    if "travel_mm" in machine:
        count = 2 if kind == "lathe" else 3
        names = "[x, z]" if count == 2 else "[x, y, z]"
        raw_travel = machine["travel_mm"]
        if not isinstance(raw_travel, list) or len(raw_travel) != count:
            raise ValueError(f"machine.travel_mm: a {kind}'s travel is {names} in mm, got {json_text(raw_travel)}")
        travel = tuple(number(value, where=f"machine.travel_mm[{index}]", positive=True)
                       for index, value in enumerate(raw_travel))
    limits = _sizes(machine, ("max_rpm", "max_feed_mm_min", "rapid_mm_min"), where="machine")
    units = choice(machine.get("units", "mm"), UNITS, where="machine.units")
    spinup = None
    if "spindle_spinup_s" in machine:
        spinup = number(machine["spindle_spinup_s"], where="machine.spindle_spinup_s", minimum=0)
    return Machine(kind, axes, post, travel, units=units, spindle_spinup_s=spinup, **limits)


def _material(raw: Any) -> Material:
    if raw is None:
        raise ValueError(f"material: required: feeds and speeds are worked out from it; one of {words(MATERIALS)}, "
                         f"or {_CUSTOM_MATERIAL} for another")
    if isinstance(raw, str):
        if raw.strip() not in MATERIALS:
            raise ValueError(f"material: {json_text(raw)} is not in the table ({words(MATERIALS)}); for another "
                             f"material give {_CUSTOM_MATERIAL}")
        return Material(raw.strip())
    material = obj(raw, where="material", example='{"name": "bronze", "surface_m_min": 90, "chipload_mm": 0.03}')
    known(material, MATERIAL_KEYS, where="material", takes=f"a material takes {', '.join(MATERIAL_KEYS)}")
    name = material.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("material.name: name the material, like \"bronze\"")
    name = name.strip()
    if name not in MATERIALS:
        missing = [key for key in ("surface_m_min", "chipload_mm") if key not in material]
        if missing:
            raise ValueError(f"material: {json_text(name)} is not in the table, so give its {' and '.join(missing)} "
                             f"(the table has {words(MATERIALS)})")
    chipload = _chipload(material["chipload_mm"]) if "chipload_mm" in material else None
    sizes = _sizes(material, ("surface_m_min", "hardness_hb"), where="material")
    return Material(name, chipload_mm=chipload, **sizes)


def _chipload(raw: Any) -> float | tuple[tuple[float, float], ...]:
    """One chip load (mm per tooth), or ``{"<tool diameter mm>": chip load}``."""
    if not isinstance(raw, dict):
        return number(raw, where="material.chipload_mm", positive=True)
    if not raw:
        raise ValueError('material.chipload_mm: a chip load, or one per tool diameter like {"3": 0.02, "6": 0.04}')
    bands: dict[float, float] = {}
    for key, value in raw.items():
        try:
            diameter = float(key)
        except ValueError:
            diameter = math.nan
        if not (math.isfinite(diameter) and diameter > 0):
            raise ValueError(f"material.chipload_mm: {json_text(key)} is not a tool diameter in mm; "
                             'key each chip load by diameter, like {"3": 0.02, "6": 0.04}')
        if diameter in bands:
            raise ValueError(f"material.chipload_mm: {json_text(key)} is {an(format(diameter, 'g'))} mm tool diameter listed twice")
        bands[diameter] = number(value, where=f"material.chipload_mm.{key}", positive=True)
    return tuple(sorted(bands.items()))


def _stock(raw: Any, kind: str, base: Path) -> Stock:
    allowed = STOCK_FOR_KIND[kind]
    if raw is None:
        raise ValueError(f"stock: required: what the {kind} starts from, one of {words(allowed)}")
    stock = obj(raw, where="stock", example='{"type": "box", "margin_mm": [2, 2, 1, 1]}')
    if "from" in stock:
        known(stock, ("from",), where="stock", takes="a stock read from a file takes only from")
        if "from" not in allowed:
            raise ValueError(f"stock.from: a {kind} starts from {words(allowed)}")
        if not isinstance(stock["from"], str) or not stock["from"].strip():
            raise ValueError("stock.from: the path of a STEP of the blank, like \"stock.step\"")
        path = (base / Path(stock["from"].strip()).expanduser()).resolve()
        if not path.is_file():
            raise ValueError(f"stock.from: no file at {path}")
        return Stock("from", path=path)
    if "type" not in stock:
        raise ValueError(f"stock.type: required, one of {words(allowed)}")
    stock_type = choice(stock["type"], STOCK_TYPES, where="stock.type")
    if stock_type not in allowed:
        raise ValueError(f"stock.type: a {kind} starts from {words(allowed)}, not {stock_type}")
    keys = STOCK_TYPES[stock_type]
    known(stock, ("type", *keys), where="stock", takes=f"a {stock_type} stock takes type, {', '.join(keys)}")
    count, names, single = {"box": (4, "[x, y, z_top, z_bottom]", False), "cylinder": (2, "[radial, axial]", True),
                            "sheet": (2, "[x, y]", True)}[stock_type]
    margin = _margins(stock.get("margin_mm", [0] * count), where="stock.margin_mm", count=count, names=names,
                      single=single)
    sizes = _sizes(stock, ("diameter_mm", "thickness_mm"), where="stock")
    if "diameter_mm" in sizes and margin[0] > 0:
        raise ValueError(f"stock.diameter_mm: a bar of a stated diameter takes no radial margin "
                         f"(margin_mm gives {margin[0]:g} mm); give one or the other")
    axis = choice(stock.get("axis", "Z"), AXES, where="stock.axis")
    return Stock(stock_type, margin, axis=axis, **sizes)


def _fixture(raw: Any, kind: str, where: str) -> Fixture:
    fixture = obj(raw, where=where, example='{"type": "vise", "jaw_height_mm": 20, "grip_mm": 5}')
    if "type" not in fixture:
        raise ValueError(f"{where}.type: required, one of {words(FIXTURES)}")
    fixture_type = choice(fixture["type"], FIXTURES, where=f"{where}.type")
    allowed = FIXTURES_FOR_KIND.get(kind, tuple(FIXTURES))
    if fixture_type not in allowed:
        raise ValueError(f"{where}.type: a {kind} holds work in {words(allowed)}, not a {fixture_type}")
    keys = FIXTURES[fixture_type]
    known(fixture, ("type", *keys), where=where,
          takes=f"a {fixture_type} takes type" + (f", {', '.join(keys)}" if keys else " only"))
    sizes = {key: number(fixture[key], where=f"{where}.{key}", positive=key not in FIXTURE_ZERO_OK, minimum=0)
             for key in keys if key in fixture}
    return Fixture(fixture_type, sizes)


def _setups(document: dict, kind: str) -> tuple[Setup, ...]:
    setups: list[Setup] = []
    for index, entry in enumerate(_entries(document, "setups", '{"id": "top", "direction": "+Z"}')):
        where = f"setups[{index}]"
        known(entry, SETUP_KEYS, where=where, takes=f"a setup takes {', '.join(SETUP_KEYS)}")
        setup_id = _id(entry.get("id"), where=f"{where}.id", example='"top"')
        if any(setup.id == setup_id for setup in setups):
            raise ValueError(f"{where}.id: {json_text(setup_id)} is the id of an earlier setup too")
        direction = entry.get("direction", "+Z")
        if direction not in AXIS_DIRECTIONS:
            if not isinstance(direction, str) or "#" not in direction:
                raise ValueError(f"{where}.direction: {json_text(direction)} is not one of {words(AXIS_DIRECTIONS)} "
                                 "or a face selector like \"#o1.f4\"")
            direction = selectors(direction, where=f"{where}.direction", kinds=("face",))[0]
        origin = entry.get("origin", "stock_top_center")
        if isinstance(origin, list):
            if len(origin) != 3:
                raise ValueError(f"{where}.origin: a point is [x, y, z] in mm, got {json_text(origin)}")
            origin = tuple(number(value, where=f"{where}.origin[{i}]") for i, value in enumerate(origin))
        elif not isinstance(origin, str) or origin not in ORIGINS:
            raise ValueError(f"{where}.origin: {json_text(origin)} is not one of {words(ORIGINS)} (a corner is the "
                             "min-X, min-Y corner, front left) or a point [x, y, z]")
        clearance, retract = _heights(entry, where, DEFAULT_CLEARANCE_MM, DEFAULT_RETRACT_MM)
        fixture = _fixture(entry["fixture"], kind, f"{where}.fixture") if "fixture" in entry else Fixture("none")
        setups.append(Setup(setup_id, direction, origin, fixture, clearance, retract))
    return tuple(setups)


def _tools(document: dict) -> tuple[Tool, ...]:
    tools: list[Tool] = []
    for index, entry in enumerate(_entries(document, "tools", '{"id": "T1", "type": "flat_endmill", "diameter_mm": 6}')):
        where = f"tools[{index}]"
        tool_id = entry.get("id")
        match = _TOOL_ID.fullmatch(tool_id) if isinstance(tool_id, str) else None
        if match is None:
            raise ValueError(f"{where}.id: name the tool by its slot in the machine's tool table, like \"T1\", "
                             f"got {json_text(tool_id)}")
        if any(tool.id == tool_id for tool in tools):
            raise ValueError(f"{where}.id: {tool_id} is the id of an earlier tool too")
        if "type" not in entry:
            raise ValueError(f"{where}.type: required, one of {words(TOOL_TYPES)}")
        tool_type = choice(entry["type"], TOOL_TYPES, where=f"{where}.type")
        needs, may = TOOL_TYPES[tool_type]
        kerf = tool_type in KERF_TOOLS
        keys = (*(KERF_COMMON if kerf else TOOL_COMMON), *needs, *may)
        known(entry, keys, where=where, takes=f"a {tool_type} takes {', '.join(keys)}"
              + (" (a beam, jet or wire has no holder, stickout or tool material)" if kerf else ""))
        missing = [key for key in needs if key not in entry]
        if missing:
            raise ValueError(f"{where}.{missing[0]}: a {tool_type} needs its {missing[0]}")
        sizes = _sizes(entry, (*needs, *may, "stickout_mm"), where=where)
        if "flutes" in entry:
            sizes["flutes"] = whole(entry["flutes"], where=f"{where}.flutes", high=32)
        if "tip_angle_deg" in sizes and not sizes["tip_angle_deg"] < 180:
            raise ValueError(f"{where}.tip_angle_deg: an included angle under 180, got {sizes['tip_angle_deg']:g}")
        _tool_sizes_agree(sizes, where)
        holder = None
        if "holder" in entry:
            raw = obj(entry["holder"], where=f"{where}.holder", example='{"diameter_mm": 32, "length_mm": 40}')
            known(raw, ("diameter_mm", "length_mm"), where=f"{where}.holder", takes="a holder takes diameter_mm, length_mm")
            for key in ("diameter_mm", "length_mm"):
                if key not in raw:
                    raise ValueError(f"{where}.holder.{key}: required; collisions are checked against the holder")
            holder = Holder(**_sizes(raw, ("diameter_mm", "length_mm"), where=f"{where}.holder"))
        comment = entry.get("comment")
        if comment is not None and (not isinstance(comment, str) or not comment.strip()):
            raise ValueError(f"{where}.comment: expected a short piece of text")
        material = None if kerf else choice(entry.get("material", "carbide"), TOOL_MATERIALS, where=f"{where}.material")
        explicit = {key: sizes.pop(key) for key in ("diameter_mm", "flutes", "flute_length_mm", "stickout_mm",
                                                     "shank_diameter_mm") if key in sizes}
        tools.append(Tool(tool_id, int(match.group(1)), tool_type, material, holder=holder, size=sizes,
                          comment=comment.strip() if comment else None, **explicit))
    return tuple(tools)


def _tool_sizes_agree(sizes: dict, where: str) -> None:
    """The sizes a tool states describe a tool that can exist."""
    diameter = sizes.get("diameter_mm")
    if "corner_radius_mm" in sizes and diameter is not None and sizes["corner_radius_mm"] > diameter / 2:
        raise ValueError(f"{where}.corner_radius_mm: {sizes['corner_radius_mm']:g} mm is more than half the "
                         f"{diameter:g} mm diameter")
    for key in ("tip_diameter_mm", "pilot_diameter_mm"):
        if key in sizes and diameter is not None and not sizes[key] < diameter:
            raise ValueError(f"{where}.{key}: {sizes[key]:g} mm must be under the {diameter:g} mm diameter")
    if "stickout_mm" in sizes and "flute_length_mm" in sizes and sizes["stickout_mm"] < sizes["flute_length_mm"]:
        raise ValueError(f"{where}.stickout_mm: {sizes['stickout_mm']:g} mm out of the holder is shorter than its "
                         f"{sizes['flute_length_mm']:g} mm flutes")


def _region(raw: Any, where: str) -> tuple[tuple[float, float], ...]:
    """A closed outline: at least three distinct ``[x, y]`` points in the setup's plane, mm, enclosing an area."""
    example = "like [[0, 0], [40, 0], [40, 20]]"
    if not isinstance(raw, list) or len(raw) < 3:
        raise ValueError(f"{where}: a closed outline of at least three [x, y] points in the setup's plane, {example}")
    points: list[tuple[float, float]] = []
    for index, point in enumerate(raw):
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError(f"{where}[{index}]: a point is [x, y] in mm, got {json_text(point)}")
        points.append(tuple(number(value, where=f"{where}[{index}][{i}]") for i, value in enumerate(point)))
    if len(set(points)) < 3:
        raise ValueError(f"{where}: an outline needs at least three different points, {example}")
    area = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1])) / 2
    if abs(area) < 1e-9:
        raise ValueError(f"{where}: the points lie on one line and enclose no area, {example}")
    return tuple(points)


def _operations(document: dict, machine: Machine, material: Material, setups: tuple[Setup, ...],
                tools: tuple[Tool, ...]) -> tuple[Operation, ...]:
    from cadgen._internal.cam.operations import OPERATION_NAMES, get_operation
    from cadgen._internal.cam.operations.base import ParseContext

    by_setup = {setup.id: setup for setup in setups}
    by_tool = {tool.id: tool for tool in tools}
    operations = []
    for index, entry in enumerate(_entries(document, "operations", '{"op": "pocket_2d", "tool": "T1", "features": ["#o1.f12"]}')):
        where = f"operations[{index}]"
        name = entry.get("op")
        if not isinstance(name, str):
            raise ValueError(f"{where}.op: name the operation, one of {', '.join(OPERATION_NAMES)}")
        try:
            operation = get_operation(name)
        except ValueError as exc:
            raise ValueError(f"{where}.op: {str(exc).removeprefix('operation: ')}") from None
        known(entry, (*COMMON_KEYS, *operation.keys), where=where,
              takes=f"{name} takes {', '.join([*COMMON_KEYS, *sorted(operation.keys)])}")
        if machine.kind not in operation.machine_kinds:
            raise ValueError(f"{where}.op: {name} runs on a {words(operation.machine_kinds)}, and this job's machine "
                             f"is a {machine.kind}")
        setup = _named(entry, "setup", by_setup, where, plural="setups")
        tool = _named(entry, "tool", by_tool, where, plural="tools")
        if tool.type not in operation.tool_types:
            raise ValueError(f"{where}.tool: {tool.id} is a {tool.type}, and {name} cuts with a "
                             f"{words(operation.tool_types)}")
        target = _target(entry, operation, where)
        feeds = {key: number(entry[key], where=f"{where}.{key}", positive=True)
                 for key in ("rpm", "feed_mm_min", "plunge_mm_min") if key in entry}
        stock_to_leave = number(entry.get("stock_to_leave_mm", 0.0), where=f"{where}.stock_to_leave_mm", minimum=0)
        coolant = choice(entry.get("coolant", "off"), COOLANTS, where=f"{where}.coolant")
        clearance, retract = _heights(entry, where, setup.clearance_mm, setup.retract_mm)
        own = {key: value for key, value in entry.items() if key not in COMMON_KEYS}
        params = operation.parse(own, ParseContext(where, machine, material, setup, tool))
        operations.append(Operation(name, setup.id, tool.id, stock_to_leave_mm=stock_to_leave, coolant=coolant,
                                    clearance_mm=clearance, retract_mm=retract, params=params, **target, **feeds))
    return tuple(operations)


def _named(entry: dict, key: str, known_: dict, where: str, *, plural: str):
    """The setup or tool an operation names; the one setup when there is one and it names none."""
    if key not in entry:
        if key == "setup" and len(known_) == 1:
            return next(iter(known_.values()))
        raise ValueError(f"{where}.{key}: name the {key} it runs with, one of {words(known_)}")
    value = entry[key]
    if not isinstance(value, str) or value not in known_:
        raise ValueError(f"{where}.{key}: {json_text(value)} is not a {key} in this job; {plural}: {words(known_)}")
    return known_[value]


def _target(entry: dict, operation, where: str) -> dict:
    """What the operation cuts: its features, a region, or the whole part; at most one, and one it takes."""
    named = [key for key in ("features", "region", "all") if key in entry]
    if len(named) > 1:
        raise ValueError(f"{where}: name one of features, region or all, not {' and '.join(named)}")
    if not named:
        if operation.target_required:
            raise ValueError(f"{where}: name what {operation.name} cuts: {words(operation.targets)} "
                             "(feature selectors come from `cadgen cam features`)")
        return {}
    key = named[0]
    if key not in operation.targets:
        raise ValueError(f"{where}.{key}: {operation.name} takes {words(operation.targets)}, not {key}")
    if key == "features":
        return {"features": selectors(entry["features"], where=f"{where}.features")}
    if key == "region":
        return {"region": _region(entry["region"], f"{where}.region")}
    if not flag(entry["all"], where=f"{where}.all"):
        raise ValueError(f"{where}.all: true for the whole part, or leave it out")
    return {"all": True}


def _view(raw: Any) -> dict | None:
    if raw is None:
        return None
    view = obj(raw, where="view", example='{"show": {"rapids": true, "stock": "final"}}')
    known(view, VIEW_KEYS, where="view", takes="a view takes show")
    show = obj(view.get("show", {}), where="view.show", example='{"rapids": true, "stock": "final"}')
    known(show, SHOW_KEYS, where="view.show", takes=f"it takes {', '.join(SHOW_KEYS)}")
    checked = {key: flag(show[key], where=f"view.show.{key}") for key in ("rapids", "fixtures", "tool") if key in show}
    if "stock" in show:
        checked["stock"] = choice(show["stock"], SHOW_STOCK, where="view.show.stock")
    return {"show": {key: checked[key] for key in SHOW_KEYS if key in checked}}


def parse_job(job: str | dict | Path | None, *, post: str | None = None) -> Job:
    """Validate a job document and return the typed :class:`Job`.

    ``post`` (``--post``) overrides ``machine.post``, checked the same way. The keys every operation shares are
    read here; each operation's own through its ``parse``.
    """
    if job is None:
        raise ValueError(
            "a job is required: --job job.json (or inline JSON) naming the machine and its post, the material, "
            "the stock, the setups, the tools and the operations; run `cadgen cam features IN.step` to list "
            "feature selectors"
        )
    document, base = _load_document(job)
    if not isinstance(document, dict):
        raise ValueError("job: expected one JSON object")
    known(document, JOB_KEYS, where="job", takes=f"a job takes {', '.join(JOB_KEYS)}")
    if "machine" not in document:
        raise ValueError("machine: required: its kind and its post, like {\"kind\": \"mill\", \"post\": \"grbl\"}")
    machine = _machine(document["machine"], post)
    material = _material(document.get("material"))
    stock = _stock(document.get("stock"), machine.kind, base)
    setups = _setups(document, machine.kind)
    tools = _tools(document)
    operations = _operations(document, machine, material, setups, tools)
    return Job(machine, material, stock, setups, tools, operations, _view(document.get("view")), source=document)
