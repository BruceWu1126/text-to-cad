# Manufacturing: the fab's limits, `cadgen pcb drc`, and the export jobs

A fab makes a board from Gerber and drill files; an assembler adds a placement file and a
bill of materials. All of them come out of KiCad's own export jobs on the saved board, and
the limits they are made to are the board's design rules. Nothing here is automatic: the
script sets the rules, runs the exports it needs, and `cadgen pcb drc` is the gate you run
before handing anything over.

## The fab's limits are the design rules

`cadgen pcb drc` checks the board against the numbers in its own design rules
([board API](board-api.md#design-rules)), not against any fab. Set them to the fab's
standard-tier limits before the first routed build, so every finding is a real one. Numbers
from each fab's own capability pages (October 2026); check the page before ordering anything
near a limit, and expect a surcharge for anything finer than the standard tier.

| Fab (standard 2-layer service) | `min_track_width` / `min_clearance` | `min_through_drill` | `copper_edge_clearance` | Assembly |
| --- | --- | --- | --- | --- |
| JLCPCB | 0.10 / 0.10 mm | 0.3 mm | 0.2 mm | yes, from LCSC's stock |
| PCBWay | 0.127 / 0.127 mm | 0.2 mm | 0.3 mm | yes, any parts |
| OSH Park | 0.152 / 0.152 mm | 0.254 mm | 0.381 mm | no: bare boards |
| Aisler | 0.2 / 0.15 mm (HASL) | 0.3 mm | 0.3 mm | yes, from MPN |
| Eurocircuits | 0.15 / 0.15 mm | 0.35 mm | 0.25 mm | yes |
| Seeed Fusion | 0.152 / 0.152 mm | 0.3 mm | 0.3 mm | yes |
| NextPCB | 0.127 / 0.127 mm | 0.3 mm | 0.2 mm | yes |

Via annular ring (`min_via_annular_width`, usually 0.13 mm at the standard tier), hole to
hole (`hole_to_hole_min`, 0.5 mm), silkscreen text (`min_silk_text_height` 1 mm,
`min_silk_text_thickness` 0.15 mm) and solder mask web (`rules.solder_mask_paste.mask_min_width`)
are on the same pages. Four-layer and finer services have their own numbers.

## `cadgen pcb drc`

```bash
cadgen pcb drc boards/blinky.kicad_pcb
```

Runs KiCad's design rule check (`kicad-cli pcb drc`, the same KiCad that built the board)
on the saved file: every violation of the board's rules and custom rules, and every
unconnected item, printed as text with KiCad's rule name, the items and their positions.
Exit 1 when any error-severity violation or unconnected item remains; warnings (silkscreen
over a pad, a dangling via) print and do not fail. It never runs inside a build and never
writes into the project. Run it after every build that changed copper; a board goes to a fab
only when it exits 0.

## The export jobs

Exports are KiCad's jobs on the **saved** board, so a script that exports refills, saves,
then exports at the end of its function ([board API](board-api.md#exports)); a separate
export script that opens the saved board is the same calls. What each fab needs:

| Need | Job | Writes |
| --- | --- | --- |
| The board itself | `board.export_gerbers(folder, create_gerber_job_file=True)` then `board.export_drill(folder)` | one Gerber X2 file per layer with Protel extensions (`.gtl`, `.gbl`, `.gts`, `.gbs`, `.gto`, `.gbo`, `.gtp`, `.gbp`, `.gm1`), the `.gbrjob`, and an Excellon `.drl` with plated and unplated holes combined (`combine_pth_npth=False` for two files). Zip the folder; every fab takes that zip |
| Placement | `board.export_position(path, PositionExportSettings)` with `use_drill_place_file_origin=True`, `exclude_dnp=True` | `<name>.pos-top.csv` and `<name>.pos-bottom.csv` (one file with `single_file=True`): reference, value, footprint, x, y, rotation, side |
| Board statistics | `board.export_stats(path, format=StatsOutputFormat.SOF_JSON)` | size, layer count, pad, via and hole counts: what the order form asks |
| A picture | `board.export_svg`/`export_png`/`export_pdf` with `PlotSettings.layers` | KiCad's own plot of the layers named |
| Other formats | `export_3d` (STEP, GLB, VRML…), `export_ipc2581`, `export_odb`, `export_gencad`, `export_dxf`, `export_ipc_d356` | when a fab or an enclosure asks for one |

Each returns a `JobResult`; read `.succeeded` and `.message`. Set the drill/place origin
(`board.set_origin(BoardOriginType.BOT_DRILL, ...)`) on a corner of the outline so the
placement file's coordinates are the fab's.

**Bill of materials.** A board alone has no BOM job (KiCad's BOM is the schematic's). Keep the
part list as data in the script, one row per part with its reference, value, footprint and
part number, and write the CSV from it with Python's `csv`:
`Comment, Designator, Footprint, LCSC Part #` is JLCPCB's required header and every other
assembler accepts it; add `Quantity, MPN, Manufacturer, Description`. Group rows by identical
value, footprint and part number; leave do-not-populate parts out of the BOM and the
placement file (`exclude_dnp`).

## Ordering

Upload the Gerber zip on the fab's PCB order page; board size and layer count are read from
it. 1.6 mm, 1 oz copper and HASL (or ENIG for fine pitch) are the usual defaults. For
assembly, upload the BOM and the placement file as well and check the fab's placement
preview: a footprint whose zero rotation differs from the fab's model shows up rotated there
and is corrected on their page.

- **JLCPCB**: parts need an `LCSC` number in the BOM; prefer its "basic" parts for passives
  and common ICs.
- **PCBWay**: any parts; give each an `MPN` and manufacturer so its BOM review can quote them.
- **OSH Park**: bare boards; upload the Gerbers.
- **Aisler** and **Eurocircuits**: take the KiCad project itself (zip the `.kicad_pro` and
  `.kicad_pcb`); Aisler assembles from `MPN` fields, Eurocircuits reads placement from it.
- **Seeed Fusion** and **NextPCB**: Gerbers, and for assembly the BOM and placement files.

Never order on the user's behalf: hand them the files and the steps.

## Before handing off

- The build line says `0 unconnected` and `cadgen pcb drc` exits 0 with no warning you have
  not read.
- The design rules are the fab's limits for the service that will be ordered.
- Every part has a value, and parts to be assembled have a part number in the BOM.
- The silkscreen labels connectors and polarity (pin 1, +/-, LED and diode orientation) and
  the DRC did not report them clipped.
- Mounting holes and connectors match the enclosure.
- For anything beyond a first prototype, $pcb-dfm reviews the board before ordering.
