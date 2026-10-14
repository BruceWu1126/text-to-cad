# Fabrication

## Sources

Where the user names no fab, start from these two, and read the rule-selection
rules in `SKILL.md` before citing either:

- [JLCPCB capabilities](https://jlcpcb.com/capabilities/pcb-capabilities): track and space,
  drills, annular rings, copper to edge, silkscreen and solder mask, per layer count.
- [KiCad's design rule checks](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html#design-rule-checking):
  what each DRC violation type means and which Board Setup value it reads.

$pcb's manufacturing reference tabulates the standard-tier limits of the fabs
`fab=` knows (JLCPCB, PCBWay, OSH Park, Aisler, Eurocircuits, Seeed Fusion,
NextPCB). Recheck the fab's page for anything near a limit.

## The board's rules are the first question

KiCad's DRC only checks the numbers in the project's Board Setup and its
`.kicad_dru` custom rules. A clean DRC proves the board meets *those*, not the
fab's.

- A `@pcb` board: its rules are `fab=`'s (or `rules=`). Confirm `fab=` names the
  fab it will be ordered from.
- A board drawn in KiCad: open `.kicad_pro` (Board Setup > Constraints are under
  `board.design_settings.rules`) and compare minimum track width, clearance, via
  and hole sizes, annular ring and copper-to-edge with the fab's page. A rule
  looser than the fab's is a **fail** even with a clean DRC.

## Review

Run `cadgen pcb validate board.kicad_pcb`. Its codes are KiCad's DRC types:

| Code | Meaning | Usual fix |
| --- | --- | --- |
| `drc.clearance`, `drc.hole_clearance` | copper or a hole closer than the rules | move or narrow; check the rule is the fab's |
| `drc.copper_edge_clearance` | copper too close to the outline | pull it in; edge connectors and castellations are a fab option, not a default |
| `drc.track_width`, `drc.annular_width` | below the rules' minimum | widen; a ring under the fab's minimum breaks out of the drill |
| `drc.solder_mask_bridge` | pads too close for a mask web between them | fine-pitch parts: confirm the fab's minimum web, or accept a gang opening |
| `drc.silk_over_copper` | silkscreen over a mask opening (KiCad: "clipped by solder mask") | the fab removes it: move the text or outline off the pads |
| `drc.silk_overlap`, `drc.silk_edge_clearance` | silkscreen over silkscreen, or off the edge | move references; small boards often hide them on Fab layers instead |
| `drc.courtyards_overlap` | parts placed into each other | spread them; an assembler needs the courtyard |
| `drc.starved_thermal` | a pad reaches its pour through too few spokes | add spokes or a track; it may not solder well |
| `parity.*` | board and schematic disagree | update the board from the schematic before ordering |

Silkscreen findings never stop a fab, which clips the artwork itself, but the
result is unreadable references and polarity marks. Treat them as **review**
items, and as **fail** when a polarity mark or pin-1 indicator is the clipped
part.

Then by inspection, with a snapshot and `pcb.read_board`:

- Outline: closed, one `Edge.Cuts` loop per board, internal cutouts with radii the
  fab's router can cut (often 0.5 mm minimum inside corners).
- Layer count, thickness, copper weight and finish match what will be ordered;
  ENIG for fine pitch or gold fingers, HASL otherwise.
- Text height and stroke on silkscreen meet the fab's minimum (JLCPCB: 1 mm height,
  0.15 mm stroke) or it prints as a smear.
- Mounting holes: plated or not as intended, clearance to copper on both sides.
- Panelization, V-score or mouse bites only if the user panels it; a fab panels a
  single board for you.

## Worked reasoning example

The following numbers are illustrative inputs, not default limits.

A board drawn in KiCad has a clean DRC with a 0.09 mm minimum clearance in its
Board Setup. The user orders 2-layer boards from JLCPCB, whose standard 2-layer
space is 0.10 mm. The DRC passing proves nothing about the 0.09-0.10 mm gaps:
set the rule to 0.10 mm, rerun `cadgen pcb validate`, and report what fails.
