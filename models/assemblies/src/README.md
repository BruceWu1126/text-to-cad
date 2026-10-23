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
| `mars_rover_concept/mars_rover_concept.py` | `mars_rover_concept.xbf` | Mars rover on terrain: 69 joints, 1 coupling, 4 configurations, 4 motions |
| `mechanical_iris_aperture/mechanical_iris_aperture.py` | `mechanical_iris_aperture.xbf` | Labeled mechanical iris aperture |
| `miniature_spiral_staircase/miniature_spiral_staircase.py` | `miniature_spiral_staircase.xbf` | Labeled miniature spiral staircase |
| `motorcycle_fidgets/motorcycle_{helmet,seat,shock,wheel}_fidget.py` | `motorcycle_*_fidget.xbf` | Four Cherry MX switch fidgets; the group shares `lib/mx_switch_socket.py` and `lib/color.py` |
| `motorcycle_shock_absorber/motorcycle_shock_absorber.py` | `motorcycle_shock_absorber.xbf` | Coilover damper; the rod rides a linear joint, `damper_stroke` |
| `pelican_riding_bicycle/pelican_riding_bicycle.py` | `pelican_riding_bicycle.xbf` | Pelican riding a bicycle |
| `photo_coffee_cup/photo_coffee_cup.py` | `photo_coffee_cup.xbf` | Photo-inspired takeaway coffee cup |
| `planetary_gear_assembly/planetary_gear_assembly.py` | `planetary_gear_assembly.xbf`, `.glb` | The docs site's hero: a gear train driven by one coupling, `drive`, and the `meshCycle` and `inspectExplode` motions its GLB carries as clips |
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
  one; `cadgen export` writes one on demand.
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
  `carrier` turn on the ring (the ground), `pin1..3` slide radially out of the
  carrier (prismatic, held by the carrier) and `planet1..3` turn on their pins:
  the pins carry the planets, as the old explode moved each planet with its
  pin. `drive` is a coupling over the exact fixed-ring ratios (sun 1, carrier
  24/84, planets -(24/18)(60/84)); a coupling is a constraint, so the sun,
  carrier and planets turn only through it, and its range is the sun's limits,
  0..1260 (the old control's range; the old sun mate allowed -1260..1260).
  `quarter_cycle` and `half_cycle` are configurations. `meshCycle` is `drive`
  swept 0 -> 1260 over 6 s, linear, a seamless cycle a player repeats.
  `inspectExplode` is back: a function of time over 5 s setting `drive` (the
  same sweep) and each pin's slide, `16 * sine(progress)`. `@glb` bakes both
  into clips of their names; the STL and 3MF exports are dropped. What the
  coupling's constraint costs `inspectExplode`: the sun and carrier, coupled
  leaves with no part to carry a slide, no longer lift 7 mm and drop 4 mm; they
  and the pins stay in the gear plane, and only the planets and pins open
  radially. Each pin slides along the carrier's radial, so a planet moves
  outward from the sun at every point of its orbit; the old clip shifted each
  planet and pin along its rest radial in the world while it orbited, so half a
  carrier turn on, a planet moved toward the sun.
- `mars_rover_concept`: its 27 typed mates are build123d joints on its
  top-level groups (26 revolute, one linear: `lid_lift`). Of its 7 couplings
  one is a real linkage and stays: `rocker_split`, the differential bar making
  the rockers counter-rotate and taking half the split, a constraint (the
  rockers and the bar move only through it, over the rockers' +-12 deg). The
  six convenience controls are dropped, each pose and motion setting their
  joints itself:
  - `drive` (all six wheels by one knob): each wheel is driven on its own.
  - `steer` (the Ackermann-ish gearing, 1 / 0.79 / -1 / -0.79): the four
    steer joints set by those ratios (`_steer`).
  - `crab` (every corner to one heading): no pose or clip used it.
  - `bogie_pitch` (both bogies by one knob): the bogies pivot independently.
  - `solar_deploy` (both wings, mirrored): `solar_left` and `-solar_right`
    (`_solar`).
  - `cutaway` (lid 420 mm, panel -75 deg by one 0..1 knob): `lid_lift` and
    `panel_swing` (`_cutaway`).

  Its 4 poses are configurations: `stowed`, `deployed`, `arm_reach`,
  `cutaway_open`. Limits are offsets from the display pose, as before. Its four
  clips are motions, functions of time with the old clips' numbers:
  `driveAround` (18 s), `armCycle` (12 s), `deploySequence` (20 s) and
  `explodedAssembly` (12 s), none looped, as before. For them the rover gains
  42 prismatic joints:
  - `heave`: the chassis rides the terrain, which is the ground now and stays
    put while the body heaves over it; every group the chassis carries heaves
    with it, through its joints or its explode slide.
  - `explode_<group>`, one per group but the terrain and the chassis, each along
    the old clip's vector, at that vector's length times `sine(t/12) * 520`. A
    static group slides whole on a joint of the chassis. A jointed group (and the
    HGA mast, which holds the dish's joint) slides off its joint without dragging
    the groups hung on it: the group compound holds the slide, its parts ride it
    (the first the rigid half, the rest fastened), and the compound stays posed
    by its own joint and holds its children's. Every part reaches the old
    clip's place.
  - Deliberate differences: `driveAround` rolls the wheels four turns
    unwrapped, where the old clip wrapped the angle at 360 (a jump a joint's
    curve cannot take), so the six wheel joints range over +-1440 deg, four
    turns each way (+-360 before); the mast pitches about
    its head pivot as it heaves with the body, where the old clip pitched it
    about the unheaved pivot (at most 2.5 mm apart). `deploySequence` also
    thinned the dust layer's opacity as the deck opened, which no joint does:
    the dust layer stays as built.
- `miniature_spiral_staircase`: the `_highres` STL, 3MF and GLB at
  `mesh_tolerance=4e-4` are dropped. The engine meshes at one tolerance
  (FreeCAD's), configurable nowhere.
- `motorcycle_fidgets`: `cadgen.srgb(hex, alpha)` -> the `(hex, alpha)` pair, which a shape takes as it is.
