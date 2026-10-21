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
