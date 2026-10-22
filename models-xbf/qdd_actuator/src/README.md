# qdd_actuator models

| Script            | Artifact                                   | Description                                                   |
|-------------------|--------------------------------------------|---------------------------------------------------------------|
| qdd_actuator.py   | XBF/qdd_actuator.xbf                       | Quasi-direct-drive actuator, 4.5:1 planetary, 153 occurrences |

Build from this folder: `python -m cadgen.engine build qdd_actuator.py::qdd_actuator`.

The makers (`make_stator`, `make_rotor`, `make_cross_roller_bearing`,
`make_torque_sensor`, `make_driver_pcb`, `make_encoder_pcb`, `make_retainer_screws`)
are plain functions in one body, like the repeated helpers (`make_ball_bearing(z)`,
the gears): the kernel memo caches their kernel calls, and a `@memo` on the seven
saved under 0.1 s of an edit (see "When to use `@memo`" in `models-xbf/README.md`).

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

Joints (build123d, placed with `connect_to` at their as-built pose): every
rigid mount of the original as a `RigidJoint` pair; the old fastened mates the
mounts do not already state (`rear_race`, `front_race`, `flange_mount`,
`xroller_inner`); and the old revolute mates as `RevoluteJoint`s on the
housing (`rotor_spin`, `rear_cage`, `front_cage`, `output`, `roller_cage`) and
on the carrier (`planet1_spin`..`planet3_spin`), each with the range of the
closed drive cycle at its member's rate.

Not ported: the `drive` coupling that geared them all off one input (build123d
joints have no coupling), the `rest`/`quarter_output`/`full_output` poses, and
the `drive`/`inspect`/`teardown` animation clips (the engine has no animation).
