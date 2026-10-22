# assemblies models

`models/assemblies` ported to `cadgen.engine`: the repo's demo assemblies, one
group per assembly. `src/<group>/` holds the group's scripts and its own `lib/`,
and every model writes into `XBF/<group>/`. A group imports its siblings and its
own `lib/`, and nothing across groups. There are 23 models. The old project had
27 decorated files: `link_robot` and `research_humanoid` were three each, and
each is one model now.

Build a group from its folder; each script builds every model it defines:

```
cd planetary_gear_assembly && python planetary_gear_assembly.py
python -m cadgen.engine snapshot ../../XBF/planetary_gear_assembly/planetary_gear_assembly.xbf gear.png --pose half_cycle
```

| Script | Writes (in `XBF/<group>/`) | What it is |
| --- | --- | --- |
| `compact_humanoid/compact_humanoid.py` | `compact_humanoid.xbf` | 28-DOF compact humanoid concept; each actuator axis is an unconnected joint on its cartridge, so there is no mechanism (none before either) |
| `cutaway_turbofan_engine/cutaway_turbofan_engine.py` | `cutaway_turbofan_engine.xbf` | Labeled multi-body cutaway turbofan |
| `flying_car/flying_car.py` | `flying_car.xbf` | Four-rotor flying car concept |
| `link_robot/link_robot.py` | `link_robot.xbf` | A base, two placements of `link_arm()` and a spare `link_pin()`: one pin B-rep placed five times, two levels deep |
| `lunar_rover_corner_assembly/lunar_rover_corner_assembly.py` | `lunar_rover_corner_assembly.xbf` | Lunar rover corner: wheel, hub motor, suspension |
| `mars_rover_concept/mars_rover_concept.py` | `mars_rover_concept.xbf` | Mars rover on terrain: 27 joints, 7 couplings, 4 configurations |
| `mechanical_iris_aperture/mechanical_iris_aperture.py` | `mechanical_iris_aperture.xbf` | Labeled mechanical iris aperture |
| `miniature_spiral_staircase/miniature_spiral_staircase.py` | `miniature_spiral_staircase.xbf` | Labeled miniature spiral staircase |
| `motorcycle_fidgets/motorcycle_{helmet,seat,shock,wheel}_fidget.py` | `motorcycle_*_fidget.xbf` | Four Cherry MX switch fidgets; the group shares `lib/mx_switch_socket.py` and `lib/color.py` |
| `motorcycle_shock_absorber/motorcycle_shock_absorber.py` | `motorcycle_shock_absorber.xbf` | Coilover damper; the rod rides a linear joint, `damper_stroke` |
| `pelican_riding_bicycle/pelican_riding_bicycle.py` | `pelican_riding_bicycle.xbf` | Pelican riding a bicycle |
| `photo_coffee_cup/photo_coffee_cup.py` | `photo_coffee_cup.xbf` | Photo-inspired takeaway coffee cup |
| `planetary_gear_assembly/planetary_gear_assembly.py` | `planetary_gear_assembly.xbf`, `.glb` | The docs site's hero: a gear train locked to one coupling, `drive`, and the `meshCycle` motion its GLB carries as a clip |
| `planetary_gear_stage/planetary_gear_stage.py` | `planetary_gear_stage.xbf` | Simplified planetary gear stage (`lib/gear_teeth.py`) |
| `research_humanoid/research_humanoid.py` | `research_humanoid.xbf` | Adult-scale humanoid: 28 body joints, and 15 per hand from `lib.build_hand(side)` |
| `robotic_hand_end_effector/robotic_hand_end_effector.py` | `robotic_hand_end_effector.xbf` | Cybernetic robotic hand end-effector |
| `sculpted_humanoid/sculpted_humanoid.py` | `sculpted_humanoid.xbf` | Sculpted humanoid: 58 revolute joints in an athletic pose |
| `six_axis_industrial_robot_arm/six_axis_industrial_robot_arm.py` | `six_axis_industrial_robot_arm.xbf` | Six-axis industrial robot arm display assembly |
| `six_blade_open_propeller/six_blade_open_propeller.py` | `six_blade_open_propeller.xbf` | Six-blade open propeller |
| `spiral_staircase/spiral_staircase.py` | `spiral_staircase.xbf` | Miniature spiral staircase |

## The port

- `@step(out="../../STEP/<group>/<name>.step")` became
  `@xbf(out="../../XBF/<group>/<name>.xbf")`, imported first. `build123d` is
  imported directly. Nothing else reads these models' STEP, so no model declares
  one; `cadgen xbf export` writes one on demand.
- No model calls another. `link_robot`'s arm and pin and `research_humanoid`'s
  hands are plain functions called in the one body (the hand models' two scripts
  are gone). The engine places a part used twice as one prototype.
- `AssemblyHelper` and `label_shape` became build123d itself. Each relation is a
  `RevoluteJoint` (or `LinearJoint`) on the fixed part and a `RigidJoint` on the
  moving one, connected at 0 where the parts stand. Every model with joints has
  its pose as the as-built configuration. A pair is named by its joint.
- `research_humanoid` fastens each joint's clevis yoke to the joint's fixed
  side. The old model exported no mechanism, so a yoke left standing when its
  limb posed never showed. Each hand's palm carries `<side>_hand_mount`, and the
  wrist carrier carries its twin; the label pairs them, since the palm sits
  inside the hand's own compound, where `connect_to` cannot place it.
- `motorcycle_shock_absorber` seats its spring on the lower perch only. A second
  rigid seat on the rod's perch would weld the rod to the body and lock the
  stroke.
- `flying_car`, `lunar_rover_corner_assembly`, `mars_rover_concept` and
  `pelican_riding_bicycle` made their `cylinder_between` with OCCT's
  `BRepPrimAPI_MakeCylinder`, a maker the memo does not key. Their calls ran
  uncached and their bodies were never kept. The helper is a placed
  `bd.Cylinder` now, the same cylinder.
- No model is a `@memo`. The kernel memo already replays every call an edit does
  not reach, so 17 of the 23 edits measured cost under a second. The two
  slowest edits land on expensive geometry itself: the sculpted humanoid's
  pelvis (11.7 s, its joint keepouts trimmed again) and the propeller's hub
  height (5.4 s: it sets every blade's root span and height). No function holds seconds of kernel
  work that an edit elsewhere leaves untouched.

## Kinematics, motions and what was dropped

- `planetary_gear_assembly`: the typed mates are build123d joints. `sun` and
  `carrier` turn on the ring (the ground), `planet1..3` turn on the carrier, and
  `pin1..3` are fastened to it. `drive` is a locked coupling over the exact
  fixed-ring ratios (sun 1, carrier 24/84, planets -(24/18)(60/84)), with range
  0..1260. `quarter_cycle` and `half_cycle` are configurations. `meshCycle` is
  `drive` swept 0 -> 1260 over 6 s, linear and looped, a seamless cycle; `@glb`
  bakes it into a clip of that name. The `inspectExplode` clip (radial
  translations, which no joint makes) and the STL and 3MF exports are dropped.
- `mars_rover_concept`: its 27 typed mates are build123d joints on its
  top-level groups (26 revolute, one linear: `lid_lift`). Its 7 couplings are
  `couple(...)`: `drive`, `steer`, `crab`, `rocker_split`, `bogie_pitch`,
  `solar_deploy`, `cutaway`. Its 4 poses are configurations: `stowed`,
  `deployed`, `arm_reach`, `cutaway_open`. Limits are offsets from the display
  pose, as before. The four animation clips (`driveAround`, `armCycle`,
  `deploySequence`, `explodedAssembly`) are dropped: no consumer needs a clip,
  and the chassis heave and the explode are no joint's.
- `miniature_spiral_staircase`: the `_highres` STL, 3MF and GLB at
  `mesh_tolerance=4e-4` are dropped. The engine meshes at one tolerance
  (FreeCAD's), configurable nowhere.
- `motorcycle_fidgets`: `cadgen.srgb` is the group's own `lib/color.py`.
