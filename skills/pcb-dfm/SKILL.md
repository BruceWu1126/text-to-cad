---
name: pcb-dfm
description: Design-for-manufacturing review of a KiCad printed circuit board before it is ordered - fab limits, silkscreen and solder mask, drills and copper to edge; decoupling, track width for current; BOM sourcing (LCSC, MPN), footprints, polarity, fiducials and placement for assembly. Use when the user asks whether a board can be made or assembled, asks for a PCB, KiCad or BOM review, a DFM or DFA check, or what to fix before ordering from JLCPCB, PCBWay or another fab.
license: MIT
---

# PCB DFM review

Provenance: maintained in [earthtojake/text-to-cad](https://github.com/earthtojake/text-to-cad).
Use the installed local skill files as the runtime source of truth.

Produce a manufacturability review of one KiCad board (`.kicad_pcb` with its
`.kicad_sch` and `.kicad_pro` beside it), for the fab and assembler it will be
ordered from. This is a guided review, not a fab's own engineering check. Report
what was not checked explicitly.

The board may come from a `@pcb` script ($pcb) or be drawn in KiCad. Either way
the KiCad files are what is reviewed; for a `@pcb` board, fixes go into its
script, never into the KiCad files.

## Setup

Run cadgen through [uv](https://docs.astral.sh/uv/), the same installation $pcb uses:

- `cadgen` below means `uvx --no-config --managed-python --python 3.13 --from cadgen==0.7.21 cadgen`
- `python` below means `uvx --no-config --managed-python --python 3.13 --from cadgen==0.7.21 python`, for `pcb.read_board`

KiCad 10 must be installed (`kicad-cli` on `PATH`, or named by `CADGEN_KICAD_CLI`);
$pcb's Setup says how.

## Evidence, in this order

1. **KiCad's own checks.** `cadgen pcb validate board.kicad_pcb` runs KiCad's ERC
   and DRC (zones refilled, schematic parity) on a copy of the project. Every
   error blocks ordering. Read every warning too: KiCad's silkscreen, mask and
   courtyard rules are warnings, and they are what a fab's CAM engineer flags.
2. **cadgen's review.** `cadgen pcb review board.kicad_pcb` measures what KiCad
   does not: IC power inputs with no decoupling capacitor or a far one, tracks
   too thin for a net's `current=`, parts the BOM cannot order, one part number
   on two footprints. It never fails; each finding is advice you weigh.
   [references/electrical.md](references/electrical.md) and
   [references/assembly.md](references/assembly.md) say how to read each one.
3. **The checklist.** What neither command measures: the fab's limits beyond
   the board's own rules, panel and edge details, polarity marks, placement
   rotations. Use the references below, a snapshot
   (`cadgen pcb snapshot board.kicad_pcb tmp/board.png`) and
   `pcb.read_board(path)` for positions.

Both commands take `--json` for the full findings document. Each finding names
its parts by board reference (`U2`, `C8`, `#net:VBUS`); `pcb.read_board(path).resolve(ref)`
answers where one is and, for a `@pcb` board, the script line that made it.

| Area | Reference | Measured by |
| --- | --- | --- |
| Fabrication | [references/fabrication.md](references/fabrication.md) | KiCad's DRC against the board's rules; the rest by inspection |
| Electrical | [references/electrical.md](references/electrical.md) | `cadgen pcb review` (decoupling, track current) |
| Assembly and sourcing | [references/assembly.md](references/assembly.md) | `cadgen pcb review` (part numbers); BOM and placement by inspection |

## Rule selection

1. Use the selected fab's own capability page for the board's layer count,
   copper weight and finish. A `@pcb` board was checked against `fab=`
   (JLCPCB unless it says otherwise); a board drawn in KiCad was checked against
   its own Board Setup, which may not be any fab's.
2. Where the user names no fab, review against JLCPCB's standard service and
   say so.
3. Record the source, its date and the conditions (layers, copper, finish) with
   every limit you adopt. If it cannot be checked, report the missing rule
   rather than invent one.

A fab's standard tier is a price, not a physical limit: finer work is often
possible at a surcharge. Say which it is.

## Report

Return a concise Markdown report in chat or the user's requested file:

- Scope: board file and revision, fab and service, layers, copper, finish,
  assembled or bare.
- Findings, errors first: what and where (board references), evidence (the
  command or measurement), the rule and its source, result, and the fix.
- Coverage: checks run, and checks not run with what is needed to run them. No
  findings is not a manufacturability approval.

| Item | Evidence | Rule | Result | Fix |
| --- | --- | --- | --- | --- |
| `U2` pad 3 to `C8` | `pcb review`: 3.67 mm centre to centre | decoupling within 3 mm (cadgen's review) | review | move `C8` beside pin 3 |

**pass** for a measured item within a cited limit; **fail** for a measured
violation or a KiCad error; **review** for a risk a person decides; **unverified**
where evidence or the fab's rule is missing. Keep cost suggestions (basic vs
extended parts, a cheaper finish) apart from manufacturing constraints.

## Fixing

A review request alone does not ask for edits or an order. When the user asks
for fixes:

- A `@pcb` board: edit its script with $pcb, rebuild, and rerun both commands.
- A board drawn in KiCad: say what to change where (board references and positions);
  the person edits it in KiCad. Then rerun both commands.

Recheck neighbours of every fix: a moved capacitor can open a clearance or
silkscreen finding elsewhere. Never order on the user's behalf.
