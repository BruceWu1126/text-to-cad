# Tom CAD models (rev4), on cadgen.engine

A copy of `src/` from the tom-cad repository as committed on its `main` branch
(`git -C <tom-cad> archive main src`, commit 7f79c52), ported to `cadgen.engine`.
`tom.py` builds the production arm, Tom rev4: shoulder yaw, shoulder pitch, elbow
pitch, elbow roll, wrist pitch, wrist yaw and the gripper jaw.

```text
src/
  tom.py            production root -> XBF/tom.xbf, XBF/tom.step
  stages/           rev4 derivation chain, child models (alone: -> XBF/stages/)
  base/             common_base_plate (child model), legacy/base_clamp (rev3 clamp datums)
  camera/           camera_payload (child model): Innomaker U20CAM + M2 screws/nuts
  common/           gripper_frame (child model of the tom2 stage)
  end_effector/     gripper datums and the servo6 gripper/camera factories the stages use
  hardware/         screws, nuts, standoffs (read pinned STEP/imported/hardware)
  purchased/        readers for pinned vendor STEP (servo, Pi 5, HAT, camera)
  lib/              shared factories and the revision layers tom.py applies
  shoulder_yaw_*.py rev3 yoke/bracket datums the stages still read
```

## The vendor inputs (not committed)

The model reads 536 MB of vendor and recovered STEP from `STEP/imported/`, resolved
relative to this model's folder (`Path(__file__).resolve().parents[2] / "STEP" /
"imported"`, `VENDOR_DIR`). They are tom-cad's read-only data and are never committed
here (`models-xbf/tom/STEP/` is gitignored). Before the first build, put a copy beside
`src/`, from `models-xbf/tom`:

```
mkdir -p STEP
cp -c -R /path/to/tom-cad/STEP/imported STEP/imported   # an APFS clone; plain cp -R elsewhere
chmod -R a-w STEP/imported                               # read-only, as in tom-cad
```

(tom-cad keeps them in Git LFS: check it out with LFS so the files are the STEP data, not
pointers. A symlink to tom-cad's folder works too.)

## Build

From `models-xbf/tom`, with `src` on `PYTHONPATH` (the stages import `lib`, `purchased`, ...
as top-level packages):

```
PYTHONPATH=<cadgen>/src:src python -m cadgen.engine build src/tom.py::tom
PYTHONPATH=<cadgen>/src:src python -m cadgen.engine snapshot XBF/tom.xbf tom.png
```

The xbf lands in `XBF/` beside `src/`. A cold build takes minutes and its worker
holds most of 20 GB; a second build of the unchanged model replays from the worker's memory.

## What the port changed

- `from cadgen import ...` -> `build123d`, `cadgen.engine` (`xbf`, `read_step`);
  `@step(out='…/STEP/x.step', kinematics=…, animation=…)` -> `@xbf(out='…/XBF/x.xbf')`
  alone (`cadgen xbf export` writes the STEP from it).
- Mates: the revision layers compute the arm's kinematics as typed mates, which the build
  also reads as data (`lib/joint_zero.py` poses the arm at joint zero by their axes;
  `lib/joint_limits.py` audits their ranges). `cadgen.revolute`/`fastened` are replaced by the
  same records in `lib/mates.py`, and `lib/joints.py` turns the final list into build123d
  joints on the built occurrences: a `RevoluteJoint` (with the audited range) on the parent and
  a `RigidJoint` on the child per revolute mate, a `RigidJoint` pair per fastened mate, each in
  its part's own frame, placed with `connect_to` at the as-built pose. build123d's `connect_to`
  composes local locations, so where parent and child sit in different compounds it would move
  the child: there the joints are authored and the child keeps its pose (the build log lists
  those mates as `unplaced`). The stages' own `kinematics=` blocks and the mates' presets
  ("poses") are not ported: build123d joints have no poses.
- Animation: the three clips (`showcase`, `wrist`, `approach`) and their beat table
  (`lib/showcase_keys.json`) are removed; the engine has no animation. The pinned occurrence
  ids and their check stay (`lib/tom_showcase.py`).
- Materials: `lib/materials.py`'s finishes are `Material` values set on the parts at the end
  of the build (`apply_materials`): the 5052 sheet (catalogue aluminium), brass standoffs,
  304 stainless nuts and black-oxide screws, matched by the finish colours the parts already
  carry. Board finishes are defined but, as before, assigned to nothing.
- `cadgen.color.srgb` -> `lib/color.py`; `cadgen.flatten` -> a copy in `lib/flatten.py`
  (only `lib/flat_project.py` imports it).
