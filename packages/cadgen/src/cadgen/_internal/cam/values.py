"""The job file's primitives: a number, a whole number, a choice, a list of selectors, a known key set.

The job parser and every operation's ``parse`` read their values through these, so an error names its field and
says what was wrong the same way everywhere: ``operations[2].stepdown_mm: must be > 0, got -1``. Stdlib only.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

__all__ = ["an", "choice", "flag", "json_text", "known", "number", "obj", "plain", "plain_text", "selectors", "whole",
           "words"]

#: A number written in scientific notation (``2.6105e-05``), as Python's ``:g`` writes a very small or large one.
_SCIENTIFIC = re.compile(r"(?<![\w.#])-?\d+(?:\.\d+)?[eE][-+]?\d+(?![\w.])")


def plain(value: float, places: int = 3) -> str:
    """A number as a person reads it: up to ``places`` decimals, no trailing zeros, never ``-0`` and never in
    scientific notation (``0.000026`` is ``0``, ``1500000`` is ``1500000``)."""
    value = round(float(value), places) + 0.0
    text = f"{value:.{places}f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def plain_text(text: str) -> str:
    """``text`` with any number in scientific notation written plainly (:func:`plain`): a finding never says
    ``2.6105e-05 mm``."""
    return _SCIENTIFIC.sub(lambda match: plain(float(match.group(0))), text)


def json_text(value: Any) -> str:
    """A value as the JSON an agent wrote it: true, null, "text", not Python's True, None, 'text'."""
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(value)


def an(number: str) -> str:
    """A number as a sentence says it after "a": ``a 6``, but ``an 8``, ``an 11``, ``an 18,000`` (said "an eight",
    "an eleven", "an eighteen thousand")."""
    digits = str(number).split(".")[0].replace(",", "")
    vowel = digits.startswith("8") or (digits[:2] in ("11", "18") and len(digits) % 3 == 2)
    return f"{'an' if vowel else 'a'} {number}"


def words(options) -> str:
    """``a, b or c``: a closed set as a sentence names it."""
    options = [str(option) for option in options]
    return options[0] if len(options) == 1 else f"{', '.join(options[:-1])} or {options[-1]}"


def number(value: Any, *, where: str, positive: bool = False, minimum: float | None = None,
           below: float | None = None) -> float:
    """A finite number; ``positive`` wants it > 0, ``minimum`` at least that, ``below`` under that."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where}: expected a number, got {json_text(value)}")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{where}: expected a finite number, got {result}")
    if positive and not result > 0:
        raise ValueError(f"{where}: must be > 0, got {result:g}")
    if minimum is not None and result < minimum:
        raise ValueError(f"{where}: must be {minimum:g} or more, got {result:g}")
    if below is not None and not result < below:
        raise ValueError(f"{where}: must be under {below:g}, got {result:g}")
    return result


def whole(value: Any, *, where: str, low: int = 1, high: int | None = None) -> int:
    """A whole number from ``low`` to ``high`` (no top when ``None``)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < low or (high is not None and value > high):
        span = f"from {low} to {high}" if high is not None else f"of {low} or more"
        raise ValueError(f"{where}: expected a whole number {span}, got {json_text(value)}")
    return value


def flag(value: Any, *, where: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{where}: true or false, got {json_text(value)}")
    return value


def choice(value: Any, options, *, where: str) -> str:
    """One of a closed set of names."""
    if not isinstance(value, str) or value not in options:
        raise ValueError(f"{where}: {json_text(value)} is not one of {words(options)}")
    return value


def obj(value: Any, *, where: str, example: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{where}: expected an object like {example}")
    return value


def known(entry: dict, keys, *, where: str, takes: str = "") -> None:
    """``entry`` names only ``keys``; else a ValueError naming the unknown ones and what it does take."""
    unknown = set(entry) - set(keys)
    if unknown:
        takes = takes or f"it takes {', '.join(sorted(keys))}"
        raise ValueError(f"{where}: unknown keys {sorted(unknown)}; {takes}")


def selectors(value: Any, *, where: str, kinds: tuple[str, ...] = ("face", "edge")) -> tuple[str, ...]:
    """A non-empty list of the viewer's selectors (``#o1.f12``), each a face or edge (or a label naming one), each once.

    A single string stands for a list of one, and its errors name ``where`` itself, with no ``[0]``. Only the syntax is
    checked here: what a selector points at is resolved when the part is opened.
    """
    from cadgen.cad_ref_syntax import parse_selector, split_cad_ref  # stdlib only

    single = isinstance(value, str)
    items = [value] if single else value
    if not isinstance(items, list) or not items:
        raise ValueError(f"{where}: list the selectors `cadgen cam features` prints, like [\"#o1.f12\"]")
    refs: list[str] = []
    for index, ref in enumerate(items):
        at = where if single else f"{where}[{index}]"
        if not isinstance(ref, str) or not ref.strip():
            raise ValueError(f"{at}: expected a selector like \"#o1.f12\", got {json_text(ref)}")
        parsed = parse_selector(split_cad_ref(ref.strip())[1])
        kind = None if parsed is None else parsed.selector_type
        if kind not in (*kinds, "label"):
            what = f"an {kind}" if kind in ("edge", "occurrence") else f"a {kind}" if kind else "not a selector"
            raise ValueError(f"{at}: {json_text(ref)} is {what}; name a {words(kinds)} `cadgen cam features` lists, "
                             f"like \"#o1.f12\"")
        if ref.strip() in refs:
            raise ValueError(f"{at}: {json_text(ref)} is listed twice")
        refs.append(ref.strip())
    return tuple(refs)
