"""A schematic's plot payload: its index on the plot's sheets, and KiCad's ERC on them.

:mod:`cadgen.kicad.plot` draws the sheets; this reads what the payload carries beside them, in
the frame of each sheet's picture (:mod:`cadgen.kicad.schematic_index`'s sheet frame).
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Sequence

from cadgen.kicad.phrasing import kind_title, summarize
from cadgen.kicad.refs import selector_or_none
from cadgen.kicad.schematic_index import export_netlist, read_index

__all__ = ["erc_payload", "payload_index"]


def payload_index(root: Path, install, plotted: Sequence[str]) -> dict:
    """The index a schematic's plot payload carries as ``schematic``, for the staged root sheet
    ``root`` the plot drew (``plotted``: the names of its pictures): KiCad's netlist exported
    beside it (one ``kicad-cli`` run), on the plot's sheets in KiCad's page order, then any
    picture the hierarchy does not name. The plot orders its pictures as these sheets."""
    root = Path(root)
    index = read_index(root, netlist=export_netlist(root, install), project=root.with_suffix(".kicad_pro"))
    pictures = set(plotted)
    names = [sheet.name for sheet in index.sheets if sheet.name in pictures]
    names += [name for name in plotted if name not in set(names)]
    return index.aligned(names).as_json()


_SYMBOL = re.compile(r"^Symbol (\S+)(?: Pin (\S+))?")
# KiCad 10.0.6's ERC JSON says "mm" but writes page millimetres divided by 100; later releases
# write millimetres. An item's position is read at whichever scale lands on what it names.
_SCALES = (100.0, 1.0)


def _near(entries: list, sheet: int, at: list[float]) -> dict | None:
    return next((entry for entry in entries if entry.get("sheet") == sheet
                 and math.dist(entry.get("at") or (math.inf, math.inf), at) < 0.01), None)


def _erc_item(index: dict, sheet: int | None, position, text: str) -> tuple[str | None, list[float] | None]:
    """A board reference to what an ERC item names, and where it is on its sheet: the pin or the
    label's net at its point, else the pin or symbol KiCad's words name."""
    pins, labels = index.get("pins", []), [label for label in index.get("labels", []) if label.get("net")]
    if sheet is not None and isinstance(position, dict):
        x, y = float(position.get("x", 0.0)), float(position.get("y", 0.0))
        for scale in _SCALES:
            at = [round(x * scale, 4), round(y * scale, 4)]
            if pin := _near(pins, sheet, at):
                return selector_or_none("pad", ref=pin["part"], pad=pin["number"]), at
            if label := _near(labels, sheet, at):
                return selector_or_none("net", net=label["net"]), at
    match = _SYMBOL.match(text)
    if match and match[2]:
        pin = next((pin for pin in pins if pin["part"] == match[1] and pin["number"] == match[2]
                    and (sheet is None or pin.get("sheet") == sheet)), None)
        return selector_or_none("pad", ref=match[1], pad=match[2]), (pin.get("at") if pin else None)
    at = [round(float(position.get("x", 0.0)) * _SCALES[0], 4), round(float(position.get("y", 0.0)) * _SCALES[0], 4)] \
        if isinstance(position, dict) and sheet is not None else None
    return (selector_or_none("part", ref=match[1]) if match else None), at


def erc_payload(report: Path, index: dict) -> list[dict]:
    """KiCad's ERC of the schematic, for its plot payload: each finding in a plain sentence, each
    item with the reference it names on the sheet ``index`` (:func:`payload_index`'s) draws it on."""
    data = json.loads(Path(report).read_text(encoding="utf-8"))
    sheets = {sheet["path"]: number for number, sheet in enumerate(index.get("sheets", []))}
    found: list[dict] = []
    for sheet in data.get("sheets", []) or []:
        number = sheets.get(str(sheet.get("path", "")))
        for violation in sheet.get("violations", []) or []:
            items = []
            for item in violation.get("items", []) or []:
                text = str(item.get("description", ""))
                ref, at = _erc_item(index, number, item.get("pos"), text)
                items.append({"text": text, "ref": ref, "at": at, "sheet": number})
            kind, description = str(violation.get("type", "")), str(violation.get("description", ""))
            entry = {
                "check": "erc", "severity": str(violation.get("severity", "error")), "type": kind, "description": description,
                "summary": summarize("erc", kind, description, [item["text"] for item in items]),
                "title": kind_title(kind, description), "items": items,
            }
            if entry not in found:
                found.append(entry)
    return found
