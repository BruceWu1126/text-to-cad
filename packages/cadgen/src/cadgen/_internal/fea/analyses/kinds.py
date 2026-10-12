"""The check kinds every analysis shares: how each is written in ``view.checks``, parsed and checked.

One :class:`~cadgen._internal.fea.analyses.base.CheckSpec` per kind (spec
section 6). Parsing only: the analysis that offers a kind judges it. The study
primitives (a number, a list of faces or of objects, a short label, a band)
live here too, so the study, the analyses and the kinds all say an error the
same way. Plus :func:`field_max_over`, the largest value of a nodal field over
some faces (or the whole model), which a displacement, temperature,
acceleration or contact check reads. Stdlib only at import.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from cadgen._internal.fea.analyses.base import CheckSpec

if TYPE_CHECKING:
    import numpy as np

__all__ = [
    "ACCELERATION", "BOLT_LOAD", "BUCKLING", "CHECK_SPECS", "CONTACT_PRESSURE", "CRACK_LIFE", "CREEP_STRAIN", "CRITICAL_SPEED",
    "DISPLACEMENT", "ELECTRIC_FIELD", "FATIGUE", "FILL_LEVEL", "FRACTURE", "FREQUENCY", "FieldMax", "JOINT_SEPARATION",
    "JOINT_SLIP", "MACH", "MASS_SAVED", "PLASTIC_STRAIN", "PLY_FAILURE", "PRESSURE_DROP", "SOUND_LEVEL", "STABILITY", "STRESS",
    "TEMPERATURE", "VELOCITY", "VOLTAGE", "WALL_PRESSURE", "band", "check_max", "entries", "faces", "field_max_over",
    "json_text", "known", "number", "parse_check", "text", "view_checks", "whole",
]


def json_text(value: Any) -> str:
    """A value as the JSON an agent wrote it: true, null, "text", not Python's True, None, 'text'."""
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(value)


def number(value: Any, *, where: str, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where}: expected a number, got {json_text(value)}")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{where}: expected a finite number, got {result}")
    if positive and not result > 0:
        raise ValueError(f"{where}: must be > 0, got {result}")
    return result


def faces(entry: dict, *, where: str) -> tuple[str, ...]:
    refs = entry.get("faces")
    if isinstance(refs, str):
        refs = [refs]
    if not isinstance(refs, list) or not refs or not all(isinstance(f, str) and f.strip() for f in refs):
        raise ValueError(f"{where}: 'faces' must be a non-empty list of face references like \"#o1.f17\"")
    return tuple(f.strip() for f in refs)


def text(entry: dict, key: str, *, where: str) -> dict:
    if key not in entry:
        return {}
    if not isinstance(entry[key], str) or not entry[key].strip():
        raise ValueError(f"{where}.{key}: expected a short piece of text")
    return {key: entry[key].strip()}


def known(entry: dict, keys, where: str, words: str) -> None:
    """``entry`` names only ``keys``; else a ValueError at ``where`` ending in ``words`` (what it does take)."""
    unknown = set(entry) - set(keys)
    if unknown:
        raise ValueError(f"{where}: unknown keys {sorted(unknown)}; {words}")


def entries(document: dict, key: str, example: str) -> list[dict]:
    """``document[key]``, a list of objects (empty when left out)."""
    raw = document.get(key)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"study.{key}: expected a list like [{example}]")
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"{key}[{index}]: expected an object like {example}")
    return raw


def whole(value: Any, *, where: str, words: str, low: int = 1, high: int | None = None) -> int:
    """A whole number from ``low`` to ``high`` (no top when ``None``)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < low or (high is not None and value > high):
        raise ValueError(f"{where}: {words}, got {json_text(value)}")
    return value


def band(raw: Any, *, where: str, words: str) -> tuple[float, float]:
    """``[low, high]`` in Hz, both positive, low below high."""
    if not isinstance(raw, list) or len(raw) != 2:
        raise ValueError(f"{where}: {words}")
    low, high = (number(value, where=where, positive=True) for value in raw)
    if not low < high:
        raise ValueError(f"{where}: low ({low:g}) must be below high ({high:g})")
    return low, high


def view_checks(document: dict) -> list[tuple[int, dict]]:
    """``view.checks``' objects with their index, before any is parsed."""
    view = document.get("view")
    checks = view.get("checks") if isinstance(view, dict) else None
    return [(i, c) for i, c in enumerate(checks) if isinstance(c, dict)] if isinstance(checks, list) else []


def _margin(entry: dict, where: str, *, low: float = 1.0, words: str) -> float:
    margin = number(entry["margin"], where=f"{where}.margin", positive=True)
    if margin < low:
        raise ValueError(f"{where}.margin: {words}; must be {low:g} or more, got {margin:g}")
    return margin


def _limit(entry: dict, key: str, where: str, words: str) -> float:
    if key not in entry:
        raise ValueError(f"{where}.{key}: {words}")
    return number(entry[key], where=f"{where}.{key}", positive=True)


def _with_faces(entry: dict, check: dict, where: str) -> dict:
    if "faces" in entry:
        check["faces"] = list(faces(entry, where=where))
    return check


def _probes(entry: dict, check: dict, where: str, example: str) -> dict:
    if "probes" in entry:
        labels = entry["probes"]
        if isinstance(labels, str):
            labels = [labels]
        if not isinstance(labels, list) or not labels or not all(isinstance(label, str) and label.strip() for label in labels):
            raise ValueError(f"{where}.probes: the labels of the study's probes to judge, like [\"{example}\"]")
        check["probes"] = [label.strip() for label in labels]
    return check


def _spec(kind: str, keys: tuple[str, ...], label: str, scaling: str, body: Callable[[dict, str], dict],
          *, unique: bool = False) -> CheckSpec:
    accepted = frozenset({"kind", "label", *keys})

    def parse(entry: dict, where: str) -> dict:
        unknown = set(entry) - accepted
        if unknown:
            raise ValueError(f"{where}: unknown keys {sorted(unknown)}; a {kind} check takes {sorted(accepted)}")
        return {"kind": kind, **body(entry, where), **text(entry, "label", where=where)}

    return CheckSpec(kind, accepted, parse, label, scaling, unique)  # type: ignore[arg-type]


def _limited(kind: str, key: str, label: str, scaling: str, words: str, *, on_faces: bool = False) -> CheckSpec:
    """A check of one positive limit ``key`` (``words`` when it is missing), over some ``faces`` when ``on_faces``."""
    def body(entry: dict, where: str) -> dict:
        check = {key: _limit(entry, key, where, words)}
        return _with_faces(entry, check, where) if on_faces else check

    return _spec(kind, (key, "faces") if on_faces else (key,), label, scaling, body)


def _margined(kind: str, label: str, scaling: str, words: str, default: float | None) -> CheckSpec:
    """A check held to an optional ``margin`` of 1 or more (``default`` when left out; none when ``None``)."""
    def body(entry: dict, where: str) -> dict:
        if "margin" in entry:
            return {"margin": _margin(entry, where, words=words)}
        return {} if default is None else {"margin": default}

    return _spec(kind, ("margin",), label, scaling, body, unique=True)


def _frequency(entry: dict, where: str) -> dict:
    given = [key for key in ("min_Hz", "avoid_Hz") if key in entry]
    if len(given) != 1:
        raise ValueError(f"{where}: a frequency check takes min_Hz (the lowest the mode may be) or avoid_Hz "
                         "([low, high], a band to keep clear of), exactly one")
    if given == ["min_Hz"]:
        check: dict = {"min_Hz": number(entry["min_Hz"], where=f"{where}.min_Hz", positive=True)}
        if "mode" in entry:
            check["mode"] = whole(entry["mode"], where=f"{where}.mode", words="the mode number, 1 for the first")
        return check
    if "mode" in entry:
        raise ValueError(f"{where}.mode: a band check looks at every mode; mode goes with min_Hz only")
    return {"avoid_Hz": list(band(entry["avoid_Hz"], where=f"{where}.avoid_Hz",
                                  words="the band as [low, high] in Hz, like [110, 130]"))}


def _temperature(entry: dict, where: str) -> dict:
    if "max_C" not in entry:
        raise ValueError(f"{where}.max_C: a temperature check needs the hottest it may get, in °C")
    return _with_faces(entry, {"max_C": number(entry["max_C"], where=f"{where}.max_C")}, where)


def _fatigue(entry: dict, where: str) -> dict:
    check: dict = {}
    if "cycles" in entry:
        check["cycles"] = number(entry["cycles"], where=f"{where}.cycles", positive=True)
    check["margin"] = (
        _margin(entry, where, words="a fatigue margin below 1 means it wears out before the cycles it needs")
        if "margin" in entry else 1.5
    )
    return check


#: The ply failure criteria a ply_failure check may name.
PLY_CRITERIA = ("tsai_wu", "max_stress")


def _ply_failure(entry: dict, where: str) -> dict:
    """A laminate's worst ply against a failure index of 1: Tsai-Wu and max-stress (the larger) by default."""
    if "criterion" not in entry:
        return {}
    if entry["criterion"] not in PLY_CRITERIA:
        raise ValueError(f"{where}.criterion: {json_text(entry['criterion'])} is not one of {list(PLY_CRITERIA)} "
                         "(leave it out to judge by the larger of the two)")
    return {"criterion": entry["criterion"]}


def _bolt_load(entry: dict, where: str) -> dict:
    """A bolt's force after loading against ``limit_N``, else (no limit given) its proof load from its size and grade."""
    if "limit_N" in entry:
        return {"limit_N": number(entry["limit_N"], where=f"{where}.limit_N", positive=True)}
    return {}


def _joint_separation(entry: dict, where: str) -> dict:
    """The clamp a bolted joint loses against what it has: it opens at none left, or under ``min_clamp_N``."""
    if "min_clamp_N" in entry:
        clamp = number(entry["min_clamp_N"], where=f"{where}.min_clamp_N")
        if clamp < 0:
            raise ValueError(f"{where}.min_clamp_N: the clamp force the joint must keep, N, zero or more; got {clamp:g}")
        return {"min_clamp_N": clamp}
    return {}


def _sound_level(entry: dict, where: str) -> dict:
    """The loudest sound pressure level allowed, dB (re 20 µPa in air), over some faces, at named probes, or anywhere."""
    check = {"limit_dB": _limit(entry, "limit_dB", where, "a sound_level check needs the loudest level allowed, in dB "
                                "(a quiet office is about 40, a busy street 70, hearing damage starts near 85)")}
    if "faces" in entry and "probes" in entry:
        raise ValueError(f"{where}: a sound_level check judges faces or probes, not both")
    return _with_faces(entry, _probes(entry, check, where, "ear"), where)


def _mass_saved(entry: dict, where: str) -> dict:
    """The least share of the part's mass a lightened design must save, percent (topology's; 25 when left out)."""
    if "min_percent" not in entry:
        return {"min_percent": 25.0}
    value = number(entry["min_percent"], where=f"{where}.min_percent", positive=True)
    if value >= 100:
        raise ValueError(f"{where}.min_percent: the share of the mass to save, under 100 %, got {value:g}")
    return {"min_percent": value}


def _crack_life(entry: dict, where: str) -> dict:
    """The cycles a growing crack lasts (Paris's law, to the size where K reaches K_IC) against the cycles needed."""
    if "cycles" not in entry:
        raise ValueError(f"{where}.cycles: a crack_life check needs the cycles the part must last with its crack, like 1e5")
    check = {"cycles": number(entry["cycles"], where=f"{where}.cycles", positive=True)}
    check["margin"] = (_margin(entry, where, words="a crack-life margin below 1 means it breaks before the cycles it needs")
                       if "margin" in entry else 2.0)
    return check


def _voltage(entry: dict, where: str) -> dict:
    """A piezo sensor's signal: the voltage an open electrode makes must reach ``min_V`` (piezo's sensing check)."""
    check: dict = {"min_V": _limit(entry, "min_V", where, "a voltage check needs the least signal the sensor must make, "
                                   "in volts, like 0.5")}
    if "electrode" in entry:
        if not isinstance(entry["electrode"], str) or not entry["electrode"].strip():
            raise ValueError(f"{where}.electrode: the name of the open electrode whose voltage is judged")
        check["electrode"] = entry["electrode"].strip()
    return check


def _critical_speed(entry: dict, where: str) -> dict:
    """Every critical speed kept clear of the operating speed range by ``margin_percent`` (15 when left out), at the
    ``orders`` asked (once per revolution when left out); ``backward`` judges the backward-whirl criticals too."""
    check: dict = {"margin_percent": 15.0}
    if "margin_percent" in entry:
        margin = number(entry["margin_percent"], where=f"{where}.margin_percent")
        if not 0 <= margin < 100:
            raise ValueError(f"{where}.margin_percent: the separation to keep between a critical speed and the operating "
                             f"speeds, percent, 0 up to 100 (API 684 asks about 15), got {margin:g}")
        check["margin_percent"] = margin
    if "orders" in entry:
        orders = entry["orders"]
        if isinstance(orders, (int, float)) and not isinstance(orders, bool):
            orders = [orders]
        if not isinstance(orders, list) or not orders:
            raise ValueError(f"{where}.orders: the multiples of the spin to judge, like [1] (once per revolution) or [1, 2]")
        check["orders"] = [number(order, where=f"{where}.orders", positive=True) for order in orders]
    if "backward" in entry:
        if not isinstance(entry["backward"], bool):
            raise ValueError(f"{where}.backward: true to judge the backward-whirl critical speeds too, got {json_text(entry['backward'])}")
        check["backward"] = entry["backward"]
    return check


def _stability(entry: dict, where: str) -> dict:
    """The smallest log decrement of the whirl modes over the operating speeds against ``min_log_dec`` (0.1 when
    left out): below 0 a whirl grows (unstable), under the minimum it is barely damped."""
    if "min_log_dec" not in entry:
        return {"min_log_dec": 0.1}
    return {"min_log_dec": number(entry["min_log_dec"], where=f"{where}.min_log_dec", positive=True)}


def _fill_level(entry: dict, where: str) -> dict:
    """The highest a liquid may reach, mm above the inside's floor (the brim when left out), at named probes or anywhere."""
    check: dict = {}
    if "limit_mm" in entry:
        check["limit_mm"] = number(entry["limit_mm"], where=f"{where}.limit_mm", positive=True)
    return _probes(entry, check, where, "left wall")


STRESS = _margined("stress", "Strength", "linear", "a safety factor below 1 means the part yields", None)
DISPLACEMENT = _limited("displacement", "limit_mm", "Displacement", "linear",
                        "a displacement check needs the most it may move, in mm", on_faces=True)
FREQUENCY = _spec("frequency", ("min_Hz", "avoid_Hz", "mode"), "Vibration", "none", _frequency)
BUCKLING = _margined("buckling", "Buckling", "inverse", "a buckling margin below 1 means it buckles at this load", 3.0)
TEMPERATURE = _spec("temperature", ("max_C", "faces"), "Heat", "none", _temperature)
ACCELERATION = _limited("acceleration", "limit_g", "Shaking", "linear",
                        "an acceleration check needs the most it may take, in g", on_faces=True)
FATIGUE = _spec("fatigue", ("cycles", "margin"), "Fatigue life", "none", _fatigue)
PRESSURE_DROP = _limited("pressure_drop", "limit_Pa", "Flow resistance", "none",
                         "a pressure_drop check needs the most it may lose, in Pa")
VELOCITY = _limited("velocity", "limit_m_s", "Flow speed", "none", "a velocity check needs the fastest it may flow, in m/s")
PLASTIC_STRAIN = _limited("plastic_strain", "limit_percent", "Permanent bend", "none",
                          "a plastic_strain check needs the most permanent strain allowed, in percent")
CONTACT_PRESSURE = _limited("contact_pressure", "limit_MPa", "Contact", "none",
                            "a contact_pressure check needs the most it may press, in MPa", on_faces=True)
CREEP_STRAIN = _limited("creep_strain", "limit_percent", "Creep", "none",
                        "a creep_strain check needs the most creep strain allowed, in percent")
PLY_FAILURE = _spec("ply_failure", ("criterion",), "Ply failure", "none", _ply_failure, unique=True)
ELECTRIC_FIELD = _limited("electric_field", "limit_kV_mm", "Arcing", "linear", "an electric_field check needs the "
                          "strongest field allowed, in kV/mm (dry air breaks down at about 3)", on_faces=True)
BOLT_LOAD = _spec("bolt_load", ("limit_N",), "Bolt load", "none", _bolt_load)
JOINT_SEPARATION = _spec("joint_separation", ("min_clamp_N",), "Joint separation", "none", _joint_separation)
JOINT_SLIP = _spec("joint_slip", (), "Joint slip", "none", lambda entry, where: {})
#: The fastest Mach number a gas flow may reach (the flow speed over the local speed of sound).
MACH = _limited("mach", "limit", "Mach number", "none", "a mach check needs the fastest Mach number allowed, like 0.8")
SOUND_LEVEL = _spec("sound_level", ("limit_dB", "faces", "probes"), "Sound", "none", _sound_level)
MASS_SAVED = _spec("mass_saved", ("min_percent",), "Mass saved", "none", _mass_saved, unique=True)
#: The crack's stress intensity K against the material's fracture toughness K_IC, held to a margin (1.5 when left out).
FRACTURE = _margined("fracture", "Crack", "linear", "a fracture margin below 1 means the crack grows at this load", 1.5)
CRACK_LIFE = _spec("crack_life", ("cycles", "margin"), "Crack life", "none", _crack_life, unique=True)
VOLTAGE = _spec("voltage", ("min_V", "electrode"), "Signal", "none", _voltage)
CRITICAL_SPEED = _spec("critical_speed", ("margin_percent", "orders", "backward"), "Critical speed", "none", _critical_speed,
                       unique=True)
STABILITY = _spec("stability", ("min_log_dec",), "Stability", "none", _stability, unique=True)
FILL_LEVEL = _spec("fill_level", ("limit_mm", "probes"), "Fill level", "none", _fill_level)
WALL_PRESSURE = _limited("wall_pressure", "limit_Pa", "Wall pressure", "none",
                         "a wall_pressure check needs the most the fluid may press on the walls, in Pa")

#: Every kind, by name, in the order of spec section 6.
CHECK_SPECS: dict[str, CheckSpec] = {spec.kind: spec for spec in (
    STRESS, DISPLACEMENT, FREQUENCY, BUCKLING, TEMPERATURE, ACCELERATION, FATIGUE, PRESSURE_DROP, VELOCITY,
    PLASTIC_STRAIN, CONTACT_PRESSURE, CREEP_STRAIN, PLY_FAILURE, ELECTRIC_FIELD,
    BOLT_LOAD, JOINT_SEPARATION, JOINT_SLIP, MACH,
    SOUND_LEVEL,
    MASS_SAVED,
    FRACTURE, CRACK_LIFE,
    VOLTAGE,
    CRITICAL_SPEED, STABILITY,
    FILL_LEVEL, WALL_PRESSURE,
)}


def parse_check(entry: Any, specs: tuple[CheckSpec, ...], *, where: str) -> dict:
    """One ``view.checks`` entry against the kinds an analysis offers (``specs``)."""
    if not isinstance(entry, dict):
        raise ValueError(f"{where}: expected an object like {{\"kind\": \"displacement\", \"limit_mm\": 0.5}}")
    kinds = {spec.kind: spec for spec in specs}
    kind = entry.get("kind")
    if kind not in kinds:
        raise ValueError(f"{where}.kind: {json_text(kind)} is not a check this cadgen makes; use one of {list(kinds)}")
    return kinds[kind].parse(entry, where)


@dataclass(frozen=True)
class FieldMax:
    """Where a nodal field is largest over some faces: the node, its value and place, and the face it is on."""

    node: int
    value: float
    at: tuple[float, float, float]
    #: The face the node is on (one of the asked faces), or ``None`` when it is on none of them.
    ref: str | None
    #: The asked faces, as the scene names them (bare ``#o1.fN``).
    faces: tuple[str, ...]


def field_max_over(
    values: "np.ndarray",
    refs: tuple[str, ...],
    *,
    boundary: "np.ndarray",
    boundary_ordinal: "np.ndarray",
    locations: "np.ndarray",
    face_ref: dict,
    ordinal_of: dict[str, int],
    where: str,
) -> FieldMax:
    """The largest of nodal ``values`` over the nodes of the surface triangles of faces ``refs``, else over every node.

    ``boundary`` is the boundary triangles in node ids, ``boundary_ordinal`` the
    face of each, ``face_ref`` the scene's ref of each face ordinal. A face
    wholly covered by a bonded joint has no surface: that is a ValueError at
    ``where``, in words the person can act on.
    """
    import numpy as np

    ordinals = sorted({ordinal_of[ref] for ref in refs})
    rows = np.isin(boundary_ordinal, ordinals) if refs else np.ones(len(boundary_ordinal), bool)
    nodes = np.unique(boundary[rows]) if refs else np.arange(len(values))
    if len(nodes) == 0:
        raise ValueError(
            f"{where}: {', '.join(refs)} {'is' if len(refs) == 1 else 'are'} wholly covered by a bonded joint, "
            "so there is no surface of it to judge; choose a face, or part of one, that is not covered by another part"
        )
    node = int(nodes[values[nodes].argmax()])
    on = rows & (boundary == node).any(axis=1)
    ordinal = int(boundary_ordinal[on][0]) if on.any() else 0
    return FieldMax(
        node=node,
        value=float(values[node]),
        at=tuple(float(c) for c in locations[node]),
        ref=face_ref[ordinal].ref if ordinal in face_ref else None,
        faces=tuple(face_ref[o].ref for o in ordinals),
    )


def check_max(values: "np.ndarray", refs, ctx, result, index: int) -> FieldMax:
    """:func:`field_max_over` on a solved result's nodes over faces ``refs`` (every node when empty), for
    ``view.checks[index]``."""
    return field_max_over(values, tuple(refs), boundary=result.boundary_quadratic,
                          boundary_ordinal=ctx.volume.boundary_ordinal, locations=result.dof_locations,
                          face_ref=ctx.volume.faces, ordinal_of=ctx.ordinal_of, where=f"view.checks[{index}]")
