"""A schematic's plot payload: its index on the plot's sheets.

:mod:`cadgen.kicad.plot` draws the sheets; this reads what the payload carries beside them, in
the frame of each sheet's picture (:mod:`cadgen.kicad.schematic_index`'s sheet frame).
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from cadgen.kicad.schematic_index import export_netlist, read_index

__all__ = ["payload_index"]


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
