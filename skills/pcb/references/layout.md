# Layout: placing a board that works

KiCad's DRC proves a board can be made; it does not prove the board works, and it knows
nothing about a part being on the wrong side of the board. These rules cover what the checks
cannot see. Place in small functional groups, route each before the next, and look at the
board after every group (snapshot it, or show it).

## Before placing

- Fix the outline and the mounting holes first, from the enclosure when there is one, so
  the board fits before anything is on it. Draw the outline centred on KiCad's origin (0, 0)
  unless the enclosure's drawing gives you a better frame, and set the drill/place origin on
  a corner of the outline for the exports.
- Decide the stack: 2 layers for most small boards; 4 (signal, ground, power, signal) for
  fast digital, RF or dense microcontroller boards.
- Group the parts into subcircuits (power input, regulator, MCU, each driver, each
  connector), decide where each group goes, and write the script as one function per group
  that places and routes it. The groups are reusable; the board is their composition.
- Keep every pad's position in a dict as you place (`pads["U1.3"] = pad.position`): every
  track starts and ends at one of these, never at a number copied by hand.

## Placement

- Connectors on the board's edges, mating side outward, with keepout zones for the housings
  and the cables that plug in. Mounting holes clear of copper.
- Power path in a line: input connector → protection → regulator → its output capacitors →
  loads. Nothing crosses it.
- Decoupling capacitors as close to their IC's power pin as the courtyards allow, on the same
  side, the smallest value nearest the pin, with a short path to the ground pour.
- Crystals within a few millimetres of their MCU pins, nothing routed under them, ground
  around them.
- Switching regulators: input capacitor, switch node and inductor in a tight loop; keep the
  switch node small and away from sensitive traces.
- Hot parts (regulators, motor drivers) near the edge or over a copper area with vias to the
  other side.
- Leave room: courtyards may not overlap (a DRC error), and tracks need space between parts:
  a few millimetres between rows of fine-pitch pads.
- Pins that connect should face each other: rotate a part rather than route around it.
  Rotations of 0, 90, 180, 270 unless the layout needs otherwise.
- Parts on the bottom (`layer=BoardLayer.BL_B_Cu`) only when the top is full or the design
  needs it; two-sided assembly costs more.

## Plan the fanout before routing

A fine-pitch part (QFN, BGA, TSSOP, a USB-C receptacle) decides the routing around it. Before
any long track:

- For each pin, decide which direction its net leaves and on which layer; a short stub from
  the pad in that direction, then a via if it changes layer. Alternate stub directions on
  dense rows so vias do not collide.
- BGA: inner pads escape between outer ones, one via per pad in the dog-bone pattern, or
  through-hole vias in the channel between pad rows; count the channels against the nets
  that must cross them before placing the part.
- Power and ground pins of the part go to the pour or plane through the nearest via; keep
  the decoupling capacitor between the pin and that via.
- Only then place the neighbours, where the fanouts point.

## Reading the results

- `cadgen pcb drc` findings give KiCad's rule, the items and their positions: go to that
  point in the script (the pad dict says whose pad it is).
- `N unconnected` in the build line: pads a copper path does not join to their net. Find
  which with a check script ([inspection](inspection.md)) and route them.
- After a clean DRC, look at the board (`cadgen pcb snapshot board.kicad_pcb tmp/board.png`,
  or show it): crowding, a capacitor far from its pin, a connector facing inward, a track
  cutting a pour in two are not errors. Fix them by placement before you route more.
