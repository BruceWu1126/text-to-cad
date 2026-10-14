# Parts: symbols, footprints, project libraries, and what a BOM orders by

## Find real names

Never guess a symbol or footprint name: look it up, then use it exactly.

```python
from cadgen import pcb

pcb.find_symbols("ldo 3.3")        # [("Regulator_Linear:AMS1117-3.3", "1A Low Dropout regulator..."), ...]
pcb.find_footprints("sot-223")     # ["Package_TO_SOT_SMD:SOT-223", ...]
```

A wrong name fails with the closest real ones. A symbol's pins must have pads on its
footprint, checked when the part is made.

## Project libraries

For a part KiCad's libraries lack, keep a project library beside the scripts, as the CAD
skill keeps shared helpers in `src/lib/`, and pass its folder to the board. Like `out=`, the
path is relative to the script:

```text
src/parts/parts.kicad_sym      symbols (or parts.kicad_symdir/, one file per symbol)
src/parts/parts.pretty/        footprints, one <Name>.kicad_mod each
```

```python
board = pcb.Board(outline=..., libraries=["parts"])          # folders, searched before KiCad's
u1 = board.part("parts:TPS62130", footprint="parts:QFN-16_3x3mm")
```

The build copies each part into the schematic and board, so the project opens anywhere.
Parts on step.parts or LCSC (`easyeda2kicad --full --lcsc_id C2837938`) usually come with
KiCad files. Copy them into the project library, and check the symbol's pin names and the
footprint's pad numbers against the datasheet.

## What a BOM orders by

A part's `properties` are its BOM fields, and they reach the schematic symbol and the
footprint alike:

| Field | For |
| --- | --- |
| `LCSC` | JLCPCB's assembly matches BOM rows by it (`C25804`) |
| `MPN` | every other assembler, and a distributor search (`RC0603FR-071KL`) |
| `Manufacturer` | with the MPN, for a BOM review |
| `Description` | a person reading the BOM |

`cadgen pcb review board.kicad_pcb` (the $pcb-dfm skill's measurement) makes two sourcing
suggestions, advice that never blocks a build or an export:

- **Part without a part number**: one suggestion naming every fitted part with pads and
  neither an `LCSC` nor an `MPN` field. Those two exact names (any case) are what the BOM's
  columns are read from: a number in `Mfr PN` or `LCSC Part #` never reaches it, so move it.
  Parts the BOM leaves out (a footprint excluded from it, like a solder jumper or a net tie)
  and mounting holes, test points, fiducials and logos (`H`, `MH`, `TP`, `FID`, `LOGO`
  references) are made, not bought, so they are skipped.
- **One part number, two footprints**: one `LCSC` or `MPN` on parts with different
  footprints. One of them has the wrong part number, or the wrong footprint: an assembler
  places one package for it.

Each names its parts by board reference. Choose parts the fab
stocks: for JLCPCB, its "basic" parts (no setup fee) for passives and common ICs.
