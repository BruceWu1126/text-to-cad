# Drilling and hole cycles

Each hole is one canned cycle (G81 to G87), which the post writes as the controller's cycle where it
has it and as plain moves where it does not ([machines-and-posts.md](machines-and-posts.md)). Every
operation here cuts `hole` features: the `features` it names, or with `all: true` every hole the
setup reaches that suits the tool. A named hole that does not suit the tool is refused, naming the
field; `all: true` skips the ones that do not suit and is refused (`empty_operation`) only when none
does. `stock_to_leave_mm` means nothing to a hole cycle (the tool's diameter is the hole's), so any
value but 0 is refused.

## Which holes suit a tool

| Tool | Suits a hole |
| --- | --- |
| `drill`, `reamer`, `boring_bar` | of its own diameter within `diameter_tol_mm` (0.05 mm), either way |
| `tap` | modelled at its major diameter or at its tap drill (the major less one pitch: 6.8 for M8 x 1.25), within `diameter_tol_mm` (0.15 mm) |
| `counterbore` | with a counterbore of its diameter (0.05 mm) on the side the tool comes from; its pilot, when stated, no wider than the hole |
| `back_bore` | a through hole with a counterbore of its diameter on the far side |
| `spot_drill` | any hole whose spot fits in its diameter |

## Order and heights

Holes are cut in a nearest-neighbour tour from where the tool is (the last toolpath's end with the
same tool, else the work zero), ties broken by selector, never in the job's order. The R plane is
`retract_mm` above the top of the material. From one hole to the next the tool stays at R (G99) when
the move across at R is clear; else the cycle returns to where it started (G98) and the tool moves
over at the retract or clearance height.

## Depth

The depth is where the tool's tip goes. `depth_mm` (on a drill, tap, reamer or boring bar) is the
full-diameter depth below the hole's opening, no deeper than the hole's own.

- **Blind hole:** the full diameter reaches the hole's floor, so the tip goes the tool's point length
  under it, but never past the hole's own deepest point: a flat-bottomed hole stops the tip there, and
  a `rest_material` warning says how far short of the floor the full diameter ends.
- **Through hole:** the hole's length, plus the tool's point length, plus `breakthrough_mm` (1 mm). Make
  sure that much stands clear under the part (a spoilboard, parallels, a plate with a hole).

## `drill`

G81: down in one and out. Tool: `drill`.

| Key | Meaning | Default |
| --- | --- | --- |
| `cycle` | `drill`, `peck` (G83) or `chip_break` (G73) | `drill` |
| `peck_mm` | the depth of each peck; with `peck` or `chip_break` only | as for `peck` and `chip_break` |
| `dwell_s` | a pause at the bottom (makes G81 a G82) | 0 |
| `depth_mm` | the full-diameter depth below the opening | the hole's |
| `breakthrough_mm` | past a through hole's far side, beyond the point | 1 |
| `diameter_tol_mm` | how closely the tool's diameter must match the hole's | 0.05 |

## `spot_drill`

G82 with a short dwell: a spot to start a drill straight, or a small countersink. Tool: `spot_drill`.

| Key | Meaning | Default |
| --- | --- | --- |
| `spot_diameter_mm` | the spot's diameter at the hole's opening (no wider than the tool) | the hole's countersink if it has one, else the hole's diameter |
| `depth_mm` | the tip's depth below the opening, instead of a diameter | |
| `dwell_s` | the pause at the bottom | 0.2 |

Give `spot_diameter_mm` or `depth_mm`, not both. A spot that goes past a blind hole's bottom is
refused (`too_deep`).

## `peck` and `chip_break`

`peck` is G83: each peck comes out to R, clearing the chips, for a deep hole. `chip_break` is G73:
each peck backs off a little and never leaves the hole, breaking a stringy chip. Tool: `drill`. The
keys are `drill`'s but `cycle`:

| Key | Meaning | Default |
| --- | --- | --- |
| `peck_mm` | the depth of each peck | `peck`: 1 x the tool's diameter; `chip_break`: 0.5 x |

## `tap` and `rigid_tap`

G84: down to depth at exactly `rpm x pitch`, reverse, and out. `tap` is for a floating
(tension-compression) holder; `rigid_tap` synchronises the feed to the spindle (Fanuc's `M29`,
LinuxCNC's `G33.1`), for a machine with rigid tapping. Tool: `tap`, whose `pitch_mm` is the thread's
(the job requires it; no thread is guessed from a diameter). Keys: `depth_mm`, `breakthrough_mm`,
`diameter_tol_mm` (0.15), `dwell_s` (0).

The hole must be drilled first, at the tap drill size, by an earlier operation. grbl refuses both
(it has no tapping: written as moves, the feed would not follow the spindle and the tap would break);
Mach3 refuses `rigid_tap`. Thread mill the hole instead on those ([milling-2d.md](milling-2d.md#thread_mill)).

## `ream`

G85: in and out at feed, for a precise hole (G89 with a dwell). Tool: `reamer`. Keys: `depth_mm`,
`breakthrough_mm`, `diameter_tol_mm`, `dwell_s` (0). Drill the hole a little under size first.

## `boring_cycle`

A boring bar finishing a hole. Tool: `boring_bar`.

| Key | Meaning | Default |
| --- | --- | --- |
| `cycle` | `bore` (G86: the spindle stops at the bottom and the bar comes out, leaving a line) or `fine_bore` (G76: the spindle orients and the bar shifts off the wall before coming out) | `bore` |
| `shift_mm` | how far the bar moves off the wall; `fine_bore` only, and required with it | |
| `shift_xy` | which way, `[x, y]` in the setup's plane, away from the bar's cutting edge once the spindle is oriented; `fine_bore` only, and required with it (cadgen does not guess which way the edge faces) | |
| `depth_mm`, `breakthrough_mm`, `diameter_tol_mm`, `dwell_s` | as for `drill` | |

The program's header says which way the bar's tip must point at the spindle orient position. grbl
and Mach3 cannot orient the spindle, so they refuse `fine_bore`.

## `counterbore`

G82 with a short dwell: a counterbore tool cut down from the side the tool comes from, its cutting
face to the counterbore's floor. Tool: `counterbore`. Keys: `diameter_tol_mm` (0.05), `dwell_s`
(0.2). The depth is the counterbore's own. A hole with no counterbore, or with it on the far side
(use `back_bore`), is refused.

## `back_bore`

G87: a back-boring bar passes through the hole shifted off centre, comes back on centre under the
part, and cuts the far-side counterbore upward; it returns to where it started (G98), never to its R
plane, which is under the part. Tool: `back_bore`.

| Key | Meaning | Default |
| --- | --- | --- |
| `shift_mm` | how far the bar moves off centre to pass through the hole: at least half the difference of the two diameters (plus 0.1 mm room) and under the hole's radius | required |
| `shift_xy` | which way, `[x, y]` | `[1, 0]` (+X) |
| `diameter_tol_mm` | | 0.05 |
| `dwell_s` | | 0 |

The hole must be through. grbl and Mach3 refuse a back bore; LinuxCNC and Haas write it as moves with
the spindle oriented, Fanuc as G87 when the shift is +X.
