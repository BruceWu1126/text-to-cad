# Assembly and sourcing

What an assembler needs from the board and its BOM, and what
`cadgen pcb review` reports about part numbers. Skip this file for a bare board
the user solders by hand, and say so.

## Sources

- [JLCPCB assembly capabilities](https://jlcpcb.com/capabilities/pcb-assembly-capabilities)
  and its parts library: what is stocked, basic versus extended parts.
- The chosen assembler's BOM and placement file requirements (PCBWay, Aisler,
  Eurocircuits each document theirs).

## Review

`cadgen pcb review board.kicad_pcb`:

| Code | What it measured | Read it as |
| --- | --- | --- |
| `review.part_unsourced` | fitted parts with pads that the BOM lists and that have neither an `LCSC` nor an `MPN` field | **fail** for an assembled order: the assembler cannot match the row |
| `review.mpn_footprints` | one `LCSC` or `MPN` on parts with different footprints | **fail**: one of them has the wrong number or the wrong footprint |

Only the field names `LCSC` and `MPN` (any case) count, because they are what
cadgen's BOM export reads. A number in `Mfr PN` or `LCSC Part #` never reaches
the BOM: move it. Parts excluded from the BOM (solder jumpers, net ties) and
holes, test points, fiducials and logos (`H`, `MH`, `TP`, `FID`, `LOGO`
references) are made, not bought, and are skipped.

Never invent a part number. Search the assembler's library for a part matching
the value, package and rating, give the user the candidates, and let them pick.

Then, with `cadgen pcb bom board.kicad_pcb` and `cadgen pcb pos board.kicad_pcb`
written to `tmp/` and read:

- Every BOM row's footprint matches the part number's package (0402 vs 0603,
  SOT-23 vs SOT-23-5).
- DNP parts are marked do-not-populate, so they are left out of both files.
- Polarity: diodes, LEDs, electrolytics and ICs have a pin-1 or polarity mark on
  silkscreen that the DRC did not clip (see [fabrication](fabrication.md)).
- Rotation: the assembler's zero orientation can differ from KiCad's; tell the
  user to check the placement preview on the order page, where a wrong rotation
  shows and is corrected.
- Parts on both sides double the assembly setup; say so if one side could hold them.
- Fiducials: three on the board, or on the panel, if the assembler asks for them.
- Clearance round tall parts and connectors for the nozzle and for hand soldering
  of through-hole parts the assembler does not place.

## Cost, kept apart

For JLCPCB, "basic" parts carry no setup fee and "extended" ones do. Suggest a
basic equivalent for passives and common ICs as a cost note, never as a
manufacturing finding.

## Worked reasoning example

The following numbers are illustrative inputs, not default limits.

`review.part_unsourced` names 41 parts. The user orders bare boards from OSH Park
and hand-solders: the finding does not apply, and the report says why. The same
board ordered assembled from JLCPCB: **fail**, with each part's value and
footprint listed so the user can choose `LCSC` numbers, and a note that resistors
and capacitors are usually basic parts.
