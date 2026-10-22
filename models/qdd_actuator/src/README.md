# qdd_actuator models

| Script            | Artifact                                   | Description                                                   |
|-------------------|--------------------------------------------|---------------------------------------------------------------|
| qdd_actuator.py   | XBF/qdd_actuator.xbf                       | Quasi-direct-drive actuator, 4.5:1 planetary, 153 occurrences |

Build from this folder: `python qdd_actuator.py`.

The makers (`make_stator`, `make_rotor`, `make_cross_roller_bearing`,
`make_torque_sensor`, `make_driver_pcb`, `make_encoder_pcb`, `make_retainer_screws`)
are plain functions in one body, like the repeated helpers (`make_ball_bearing(z)`,
the gears): the kernel memo caches their kernel calls, and a `@memo` on the seven
saved under 0.1 s of an edit (see "When to use `@memo`" in `models/README.md`).

Materials: `_label(shape, name, *details, color=, material=)` gives every part a
material beside its colour, from the concept materials under the palette, each a
catalogue entry in the finish its colour names, with `color=None` so the palette
shows: bead-blasted, hard-anodised Aluminum 6061-T6 (housing, retainer, rear cover),
machined 7075-T6 (carrier, output flange), anodised 6063-T5 (cable tube); Steel
4140 black oxide (screws), ground (gears) and as bearing steel (races, balls,
rollers); Steel 1018 (laminations under varnish, rotor bell, connector collars);
enamelled Copper C110 (windings); Neodymium magnet N42 (rotor magnets, both pole
colours); FR4 (boards); ABS (FETs, MCU, encoder IC); a drawn 5052 can
(capacitors); Nylon PA6 (connector bodies); NBR 70A (o-rings); gold-plated Brass
C360 (contacts); Titanium Ti-6Al-4V (flexure); and two of the project's own, a
sintered `Ferrite magnet` (encoder ring) and `Polyimide film` (strain gauges).

Joints (build123d, placed with `connect_to` at their as-built pose, every value
0 as built). The old clips open the whole stack along the drive axis, so every
mount they open slides:

- Every mount of the original and the old fastened mates the mounts do not
  already state (`rear_race`, `front_race`, `flange_mount`) is a `LinearJoint`
  along +Z on the side that holds it, under the mount's own label, its travel
  the clips' (`_SLIDES`). A rider that turns with its member (the inner races,
  sun and encoder ring on the rotor; the torque sensor and flange on the
  carrier) slides on the member, so it keeps turning with it. `xroller_inner`
  stays fastened: the carrier and its bearing share a station (below).
- The old revolute mates are `RevoluteJoint`s, each with the range of the
  closed drive cycle at its member's rate: `rotor_spin` on the housing,
  `rear_cage` and `front_cage` on their rotor-bearing modules (each ball ring on
  the module that carries its outer race), `output` and `roller_cage` on the
  cross-roller bearing (the carrier on the bearing's outer ring), and
  `planet1_spin`..`planet3_spin` on the carrier.
- `rotor_lift` is the group-compound slide: the rotor compound turns on
  `rotor_spin` and holds the riders' slides, and its bell and magnets rise on
  `rotor_lift` (the bell holds the rigid half, the magnet rings are fastened to
  it).
- Two seats are new: `rear_bearing_seat` and `front_bearing_seat` slide each
  rotor-bearing module (its outer race) on the housing. The port gave those
  modules no joint (they stood with the ground), and the explode lifts each to
  its own station.
- `floor_lift`, held by the root, lifts the housing: the root is the ground, so
  the connectors are the one part fixed in the world and the housing rises
  82 mm, the old clips' `FLOOR_LIFT` rebase, exactly.

The `drive` coupling is the gear train: one control in input revolutions,
`couple(actuator, "drive", rotor_spin=360, output=80, ...)`, and a constraint,
so the rotor, cages, carrier and planets turn only through it. Its range is
the joints' limits over their rates, 0..4.5. The old `quarter_output` and
`full_output` poses are configurations naming it (`rest` is as built):
`cadgen snapshot ../XBF/qdd_actuator.xbf q.png --pose '{"drive": 2.25}'`.

Motions, the old `ANIMATION_JS` clips by name (no `@glb` is declared;
`cadgen export ../XBF/qdd_actuator.xbf qdd.glb` bakes all three). The old clips
looped; a motion has no loop, and a player repeats it:

- `drive` (12 s): `drive` swept 0 -> 4.5, linear: one closed cycle, 4.5 input
  turns (`DRIVE_CYCLE_DEG`), seamless at tooth and pole phase.
- `inspect` (12 s): the same cycle (`drive` by name) while every module slides
  to its station by `sin(pi * phase)`, the ring, sun and planets meshed.
- `teardown` (10 s): no drive; every module slides to its own station by
  `sin(pi * phase)`.

`inspect` and `teardown` are functions of time over `drive` and the slides
(`_explode`, the old `frame()`'s stations).

What the coupling's constraint costs the clips (deliberate differences): a
coupled joint turns only, so a turning member slides only with something that
carries it. The rotor (a compound) and the ball cages (each on its outer race)
keep their stations exactly. The carrier is one solid and turns on the
cross-roller bearing's outer ring, so the two share a station, the carrier's
250 (the bearing's rings and rollers were at 280), and the planets, turning on the carrier,
stand there too (theirs was 222 in `teardown`): in `inspect` the meshed ring,
sun and planets meet at 250 (`GEAR_CLUSTER`, 210 before). Every other module
stands where the old clips put it. `drive` is unaffected.

Other deliberate differences:

- In `teardown` the planets do not spread 14 mm radially
  (`PLANET_EXPLODE_RADIAL`). A planet is a leaf turning on the carrier, whose
  pins are one solid with it, so no part can carry the planet outward, and the
  port adds no parts. `build_actuator(explode=1)` still spreads them.
- The flange's seat on the flexure rim (`rim_face`/`mount_rear`) is datums
  only, no longer connected: the flange rides the carrier on `flange_mount`,
  the old fastened mate, and a second sliding seat would close a loop.
