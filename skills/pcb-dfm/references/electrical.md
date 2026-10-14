# Electrical

What `cadgen pcb review` measures for the board's own circuits, and what to look
at by hand. These are design reviews a fab never performs: a board that breaks
them is made fine and works badly.

## Sources

- [IPC-2221B](https://www.ipc.org/TOC/IPC-2221B.pdf) section 6.2 for conductor width
  against current and temperature rise; the review uses its outer-layer chart.
- The datasheet of each IC for decoupling values and placement, which wins over
  the review's distance.

## Review

`cadgen pcb review board.kicad_pcb`:

| Code | What it measured | Read it as |
| --- | --- | --- |
| `review.decoupling_missing` | an IC power input (KiCad pin type `power_in`, not a connector, jumper, test point or hole, fitted) with no capacitor from its net to ground | **fail** unless the datasheet needs none or the cap sits on a different net name; check the schematic |
| `review.decoupling_far` | the nearest capacitor on that pin's net is over 3 mm, pad centre to pad centre | **review**: move it beside the pin, on the same side, short return to the ground pour; bulk caps may sit further |
| `review.track_current` | a net's narrowest track is thinner than its `current=` needs (IPC-2221, 1 oz outer copper, 10 °C rise) | **fail** for a power net carrying that current; widen, pour, or split across layers |

The track check only knows a net's current if someone gave one: a `@pcb`
board's `board.net("VBUS", current=2.0)`, kept in the `.kicad_pro`. A board drawn
in KiCad has none. Ask the user for the current of every power net and compute
the width yourself from IPC-2221, or mark the check **unverified**.

Then by inspection:

- Every ground pin reaches the ground pour through a short path; no pour island
  is cut off (DRC reports islands only if the rules ask).
- Power entry: reverse-polarity, inrush and ESD parts beside the connector, before
  anything else on the net.
- Thermal: regulators and FETs dissipating more than a few hundred milliwatts have
  copper area and vias under them as the datasheet asks.
- High-speed and analog nets, if any: differential pairs routed together, no
  splits under them in the reference plane, crystals with short traces and
  ground guard.

Simulation ($pcb's testbenches) answers circuit questions; this review answers
layout ones. Say which a finding is.

## Worked reasoning example

The following numbers are illustrative inputs, not default limits.

`review.decoupling_far` reports `U2` pad 3 to `C8` at 3.67 mm. The datasheet asks
for 100 nF "as close as possible" to each supply pin. 3.67 mm on a 2-layer board
with a solid ground pour is a **review** item, not a fail: move `C8` to the pad's
side if routing allows, and say what was traded if it does not.
