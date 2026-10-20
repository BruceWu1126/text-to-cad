# qdd_actuator models

| Script            | Artifact                                   | Description                                                   |
|-------------------|--------------------------------------------|---------------------------------------------------------------|
| qdd_actuator.py   | XBF/qdd_actuator.xbf, XBF/qdd_actuator.step | Quasi-direct-drive actuator, 4.5:1 planetary, 153 occurrences |

Build from this folder: `python -m cadgen.engine build qdd_actuator.py::qdd_actuator`.

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
