# w16 models

`models/w16` ported to `cadgen.engine`: a quad-turbo 8.0 L W16, a sectioned
museum cutaway with working kinematics. `w16.py` is the one model:

| Script | Writes | What it is |
| --- | --- | --- |
| `w16.py` | `XBF/w16.xbf`, `XBF/w16.glb` | The engine: the thirteen systems below, its mechanism (174 joints), the `crank` motion (a clip of the GLB) |
| `block.py` ... `ancillaries.py` (13) | nothing | One system each, a `@memo` stage the engine calls in its body |

Build from this folder: `python w16.py`.

```
python w16.py
python -m cadgen.engine snapshot ../XBF/w16.xbf w16.png
python -m cadgen.engine export ../XBF/w16.xbf w16.step
```

## How it is built

`lib/` holds the part builders, one module per system, plus the shared
vocabulary (`spec.py`, the single source of every number and the frame; `kin.py`,
the pure kinematics; `geo.py`, `castings.py`, `fasteners.py`), unchanged but for
`import build123d as bd` and three things:

- `geo.cut_fuzzy`, `geo.fuse_fuzzy` and the heads' skin harvester (now
  `geo.common_fuzzy`) ran OCCT's fuzzy booleans themselves. A boolean run past
  build123d runs past the engine's memo too, and every body that held one ran on
  every build. They run the same fuzzy operation through build123d's boolean now
  (`Shape._bool_op`, no clean, as before; the fuse keeps its simplified result
  through build123d's clean), so the memo keys them.
- `palette.py`'s finishes, the original's `materials=` declaration, are
  `Material` values: each the catalogue's entry for the alloy, steel, rubber or
  composite in the finish's look (metallic, roughness, clear coat), with
  `color=None`, so the palette stays the colour that shows; Inconel 625 and the
  heat wrap are the project's own. `apply_finishes` gives every part the finish
  of its colour, as the original's `material_compound` grouped them by colour
  (its `material_<finish>` groups, which existed for the old assignments, are
  gone: each system is its parts).
- `lib/kinematics.py` replaces `lib/animation.py`: the mechanism as build123d
  joints and the `crank` motion (below).

The museum section is the model's own geometry, as before: `spec.SECTIONED`
cuts bank 1's statics for x > `SECTION_X` (cylinder 3's centreline), keeping the
centre spine and opening the crankcase to the pan rail; moving parts are never
cut, and the sectioned bank's front turbo is lifted off.

The order of `SYSTEMS` in `w16.py` is the occurrence order:

    block   crank   pistons   heads   valvetrain   cams   camdrive
    covers   oil_system   turbos   exhaust   induction   ancillaries

Each system is a `@memo` function in its own file (`pistons.py`: `pistons()`
returns `lib/pistons.py`'s parts as one group): a stage of the engine that an
edit elsewhere replays from the worker's memory. Each holds seconds of kernel
work; cold, on a machine loaded by other work (load 25-70):

| Stage | Cold | Stage | Cold |
| --- | --- | --- | --- |
| `block` | 73 s | `covers` | 76 s |
| `crank` | 49 s | `oil_system` | 133 s |
| `pistons` | 175 s | `turbos` | 107 s |
| `heads` | 67 s | `exhaust` | 10 s |
| `valvetrain` | 30 s | `induction` | 137 s |
| `cams` | 32 s | `ancillaries` | 281 s |
| `camdrive` | 10 s | the engine's own body | 1 s |

47,091 kernel calls in all; `turbos` alone is 2,740 of them. Much of a stage's
time is its own validity checks (`geo.sound`, OCCT's `BRepAlgoAPI_Check`, run on
every part it builds), which no kernel hit saves: 77 of the turbos' 123 s in plain
Python.

## Kinematics

Every moving part is placed by `lib/kin.py` at theta = 0, the rest pose every
file shows, and its joint is authored where it stands, paired by label: the
moving joint on the part that carries it, a `RigidJoint` of the same label on
the part it moves where the moving joint holds it at 0 (a revolute joint's axis
frame; a prismatic joint's axis origin, keeping the part's axes, as build123d's
`relative_to` slides a part), so each stands at 0 as built, and whatever rides a
moving part fastened to it by a pair of
`RigidJoint`s of one label (618 of them: the crank's damper, flywheel,
sprockets and bolts, each piston's rings, pin and circlips, each rod's cap,
bolts and shells, each valve's spring, retainer and collets, each follower's
roller and axle, the cam sprockets, the turbo rotors' second wheel and nut).

| Joint | Kind | On -> moves | Range |
| --- | --- | --- | --- |
| `crank` | revolute about X | block -> crankshaft | free |
| `piston_1` ... `piston_16` | prismatic along the bore | block -> piston | the stroke from rest |
| `rod_1` ... `rod_16` | revolute about the wrist pin | piston -> rod | the swing from rest |
| `cam_1_intake` ... `cam_2_exhaust` | revolute about the cam axis | head -> camshaft | free |
| `valve_<tag>` (64) | prismatic along the stem | head -> valve | the lift from rest, to full lift |
| `follower_<tag>` (64) | revolute about the pivot ball | head -> follower | -45 .. 45 deg |
| `oil_pump` | revolute about the pump stack | block -> pump shaft | free |
| `turbo_<bank>_<pos>` (3) | revolute about the shaft | centre housing -> turbine wheel | free |
| `pulley_<name>` (5) | revolute about each pulley | block -> pulley | free |

The slider-crank and the valve train are closed loops (each rod's big end on
its crankpin, each roller on its lobe), so their joints are the loops' tree and
every pose of them is solved in Python by `lib/kin.py`, as the original's clip
solved it. The `crank` motion is that solve as a function of time: one 720 deg
four-stroke cycle in 6 s, looping -- 16 pistons and rods in the firing order
1-14-9-4-7-12-15-6-13-8-3-16-11-2-5-10, the cams at half speed, 64 valves and
their roller followers on the lobes, the oil pump and accessory pulleys at their
belt ratios, the turbo rotors 12 turns a cycle. The document keeps it as samples;
the GLB carries it as the clip `crank`.

Posed by the engine's forward kinematics at six crank angles, every rod's big end
lands on its crankpin as the crank carries it, and every wrist pin where its
piston carries it, within 1.2e-13 mm (the loops close). The GLB's `crank` clip
moves 792 placements over 129 keys.

## Measured

On 2026-10-22, one machine (10 cores), its load 25-150 from other work:

| Build | Time | What ran |
| --- | --- | --- |
| cold | 1,238 s (1,407 s at load 100-150) | every body; the GLB's bake 31 s |
| no-op | 2.7-6.0 s | 14 bodies replayed; both files kept |
| edit: `lib/turbos.py` `WG_CAN_D` 60 -> 62 | 66 s | `turbos` and the engine's body (65 kernel misses), 12 stages replayed; both files rewritten |
| revert | 65 s | the same, every call a hit; the xbf and the GLB are the bytes they were before the edit |

Two cold builds are not the same bytes: they differ in 3 of 794 parts. The
sump pan's parting-line bead (`castings.parting_line`, an offset of a slice of
the casting fused back) numbers 649 of its 1,012 faces otherwise and has another
geometry key; `intercooler_lid:1` and `lifting_eye:1_rear` have other geometry
keys. A no-op after either replays every body and keeps both files.

## Not ported

- The `running_reveal` and `explode` clips: they move whole systems, heads and
  the crank apart and fade the crankcase, which no joint does; the viewer's
  Explode shows the parts apart.
- The timing chains' motion: each link rides its loop in the chain's plane, a
  move no joint between two parts says, so the chains stand still while their
  sprockets turn.
- `lib/animation.py`, `lib/anim_check.py` (its check of the clips' poses) and
  `lib/critic_pack.py` (the blind-critic packets); `lib/collide.py`, the
  kinematic collision gate, is kept as it was (`cd src && python -m
  lib.collide`), not run here.
- `../BUILDING.md`, `../REPORT.md`, `../TODO.md`, `../GAUNTLET.md` and `../render/`:
  the original's brief, hand-off report and render jobs. An xbf is drawn in the
  engine's one look.
