# juno models

`models/juno` ported to `cadgen.engine`: a ~1.40 m research humanoid with 27 body
degrees of freedom, and the 28 link meshes its URDF references. One script,
`juno.py`, holds every model:

| Model | Writes | What it is |
| --- | --- | --- |
| `juno` | `XBF/juno.xbf` | The robot: 28 links, 363 occurrences, 27 revolute joints, 5 configurations |
| `pelvis` ... `hand_right` (28) | `XBF/<link>.xbf`, `XBF/<link>.3mf` | One URDF link alone, in its part-local frame; `../juno.urdf` references the 3MF |

Build from this folder: `python juno.py` builds all 29. `../juno.urdf` and
`../juno.srdf` are authored (not generated); the URDF's meshes are `XBF/<link>.3mf`.

```
python juno.py
python -m cadgen.engine snapshot ../XBF/juno.xbf juno.png --pose wave_right
python -m cadgen.engine snapshot ../juno.urdf urdf.png
```

## How it is built

`lib/` holds the part builders unchanged (`build123d` imported directly) and
`lib/chain.py`, the chain spec the URDF and SRDF mirror: offsets, travel, the
athletic stance and the SRDF group states.

`links()` is the script's one `@memo` function: all 28 links (~4,300 kernel
calls, ~23 s cold). The robot and each link model call it, so a build makes every
link once. The first call runs it, or replays it when no edit reaches a builder,
and every other call is a new placement of its result. Measured: 30.8 s cold with
it, 56.2 s without it, when the robot and the link models each build the links.
Because every link model reaches `links()`, an edit to any builder changes every
link model's closure. All 29 xbfs are written again, and each 3MF is written with
the same bytes unless its mesh changed.

## Kinematics

The old `AssemblyHelper` frames and typed mates are one thing now: each joint is a
build123d `RevoluteJoint` on the parent link and a `RigidJoint` on the child
(`lib/juno_lib.revolute_attach`), connected root to leaf at the athletic ready
stance. That stance is the as-built configuration. Values are the absolute
angles, not deltas from it, and each joint's range is the URDF's travel
(`lib/chain.py`). The SRDF's five group states are configurations: `zero`,
`athletic_ready` (as built), `t_pose`, `wave_right`, `squat`. The pelvis is the
ground. Soles stand at z = -874.6 as built.

## Not ported

- The seven animation clips (`walkLoop`, `strideLoop`, `runLoop`, `jumpLoop`,
  `danceLoop`, `handstandLoop`, `kickLoop`). They move the whole body: root
  bounce, toe and palm pivots, IK-anchored feet. A motion poses joints from a
  fixed ground, and the project needs no demo clip, so they are dropped.
- Per-link STEP files. The xbf replaces them, and `cadgen export` writes one
  on demand.
- The 28 one-link scripts. Each link is a model of `juno.py` now, so no model
  calls another.
