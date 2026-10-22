# Joints, mechanisms and motion in the xbf

Question: how should assemblies, joints (mates) and motion become native parts of
cadgen's `.xbf`, the BinXCAF document that `cadgen.engine` writes? Where possible
the answer should borrow FreeCAD's and AP242's models instead of cadgen's own
vocabulary.

Evidence base:
- Engine at `64517d1d5`, `packages/cadgen/src/cadgen/engine/`.
- OCP `cadquery-ocp 7.9.3.1.1`, which wraps OCCT 7.9.3 (`OCP/.dylibs/libTK*.7.9.3.dylib`).
- FreeCAD sources on the `releases/FreeCAD-1-0` and `-1-1` branches.
- The ISO 10303-105 / AP242 AIM pages at steptools.com.
- The CAx-IF / MBx-IF kinematics recommended practice.
- The prototype `roundtrip.py`, saved beside this file, with its output in `roundtrip.out`.

Short names for sources:
- `R10` = `https://raw.githubusercontent.com/FreeCAD/FreeCAD/releases/FreeCAD-1-0/src/Mod/Assembly/`
- `R11` = the same path on the 1.1 branch.
- `AIM` = `https://www.steptools.com/stds/stp_aim/html/`

---

## 0. What exists today (verified)

### OCP and OCCT
- **No kinematics in OCP.** The venv's OCP has 320 modules and none contains "Kin".
  - `XCAFDoc_DocumentTool` offers these tools: Shape, Color, Layer, Material,
    VisMaterial, DimTol, Notes, View, ClippingPlane.
  - It has no kinematic tool. Verified with `dir()`.
- **The compiled OCCT does contain kinematics entities.** `libTKDESTEP.7.9.3.dylib` exports 202
  `StepKinematics_*` symbols: every pair, every `*WithRange`, every `*PairValue`,
  `MechanismRepresentation`, `MechanismStateRepresentation`, the topology
  structures and `KinematicPropertyMechanismRepresentation`.
  - Its strings include `REVOLUTE_PAIR`, `REVOLUTE_PAIR_WITH_RANGE`, `KINEMATIC_JOINT`
    and `MECHANISM_REPRESENTATION`.
  - So OCCT 7.9.3 can parse AP242 kinematics into a raw `StepData` model.
  - OCP binds none of these classes. `libTKXCAF` has 0 kinematic symbols.

### The engine
- **No kinematics, joints, clips or articulation anywhere.** `grep -i kinemat|articulat|joint|clip`
  over `engine/*.py` and its README finds nothing.
- **The ported benchmark models keep their mechanism in a variable that nothing reads.**
  - `juno.py`, `hypercar.py` and `tom.py` each still define `KINEMATICS = ...`.
  - Each is decorated `@xbf(out=...) @step(out=...)` only, and nothing consumes `KINEMATICS`.
  - So every port has lost its mechanism.
- **The engine writes STEP as AP214IS, not AP242.** `exports.write_step` sets
  `write.step.schema = AP214IS`.

### How the mechanism is described today
juno describes its mechanism four times:
1. build123d joints through `AssemblyHelper` + `revolute_attach` (`RevoluteJoint` →
   `RigidJoint`, `connect_to(angle=...)`), which position the parts.
2. `cadgen.revolute(...)` mates, which describe the motion.
3. `lib/chain.py`, for the URDF.
4. `ANIMATION_JS`, which re-implements the forward kinematics in JavaScript.

tom and juno both re-zero:
- `rezeroed_kinematics`, and "limits = the URDF travel range minus the authored angle".
- They do this because cadgen's rule is "authored placement is q = 0"
  (`kinematics.py`).

---

## 1. FreeCAD 1.0's Assembly workbench, as data

All of this is read from the source; the wiki was behind a bot wall.

### Joint types
- `JointTypes` in `R10/JointObject.py`, unchanged in 1.1: **Fixed, Revolute,
  Cylindrical, Slider, Ball, Distance, Parallel, Perpendicular, Angle, RackPinion,
  Screw, Gears, Belt**.
- `enum class JointType` in `R10/App/AssemblyObject.h` mirrors it.
- `Distance` has about 40 sub-kinds in `enum class DistanceType`, detected from the
  pair of selected geometries (PointPoint, PlanePlane, CylinderCylinder, …).

### A joint is a document object with properties
A joint is an `App::FeaturePython` with a `JointObject.Joint` proxy. Its properties come
from `Joint.createProperties` in `R10/JointObject.py`:

| Property | Type (1.0) | Meaning |
|---|---|---|
| `JointType` | PropertyEnumeration | one of the 13 above |
| `Reference1`, `Reference2` | PropertyXLinkSubHidden (1.1: XLinkSub) | `[component, ["Part.Body.Face6", "Part.Body.Vertex3"]]`: an element and a vertex on it |
| `Placement1`, `Placement2` | PropertyPlacement | the joint coordinate system on each side, **relative to that object's own placement**; Z is the joint axis |
| `Offset1`, `Offset2` | PropertyPlacement | right-multiplied onto the placement found from the reference |
| `Detach1`, `Detach2` | PropertyBool | the frame stops following its reference |
| `Distance`, `Distance2` | PropertyFloat (1.1: PropertyLength) | offset/angle/ratio parameters (Gears, Belt, Screw, RackPinion use them) |
| `Activated` | PropertyBool (1.1: `Suppressed`) | |
| `EnableLengthMin/Max`, `LengthMin/Max`, `EnableAngleMin/Max`, `AngleMin/Max` | Bool / Float (1.1: Length / Angle) | limits: length along Z, angle between the X axes |

How `Placement1`/`Placement2` are found: `UtilsAssembly.findPlacement` takes the
reference's geometry, and Z comes from it:
- a circle's axis;
- a line's direction;
- a face's `surface.Rotation`, which is the normal of a plane or the axis of a cylinder;
- for a vertex, the identity.

### Grounding
- `Assembly_ToggleGrounded` makes a `GroundedJoint` object (`R10/CommandCreateJoint.py`).
- It has two properties: `ObjectToGround` (PropertyLink; 1.1: LinkGlobal) and `Placement`.
- The solver fixes each grounded part to an assembly marker with an `ASMTFixedJoint`
  (`fixGroundedParts` in `R10/App/AssemblyObject.cpp`).
- `solve()` returns −6 when nothing is grounded.

### Storage
- `AssemblyObject` is an `App::Part` subclass (`R10/App/AssemblyObject.h`).
- Its joints live in an `Assembly::JointGroup`, a `DocumentObjectGroup` (`R10/App/JointGroup.h`).
- Each joint is a FeaturePython in `Document.xml`. This is inferred from FreeCAD's
  standard format, not read from a sample file.

### The solver
OndselSolver (MbD). `makeMbdJointOfType` maps the joint types onto ASMT classes:

| FreeCAD joint | ASMT class |
|---|---|
| Fixed | `ASMTFixedJoint` |
| Revolute | `ASMTRevoluteJoint` |
| Cylindrical | `ASMTCylindricalJoint` |
| Slider | `ASMTTranslationalJoint` |
| Ball | `ASMTSphericalJoint` |
| Parallel | `ASMTParallelAxesJoint` |
| Perpendicular | `ASMTPerpendicularJoint` |
| Angle | `ASMTAngleJoint` |
| RackPinion | `ASMTRackPinionJoint`(pitchRadius) |
| Screw | `ASMTScrewJoint`(pitch) |
| Gears | `ASMTGearJoint`(radiusI, radiusJ) |
| Belt | `ASMTGearJoint` with radiusJ negated |

Limits go to `ASMTRotationLimit` and `ASMTTranslationLimit`. The solver can export
its own `.asmt` file (`exportAsASMT`).

### Motion
- **1.0 has none.** `Assembly_CreateView` makes exploded views, which are static placements.
- **1.1 adds `Assembly_CreateSimulation`** (`R11/CommandCreateSimulation.py`):
  - A `Simulation` object with a start time, end time, step and fps.
  - `Motion` children, each with `Joint`, `MotionType` (Angular or Linear) and a
    `Formula` string such as `"1.0*time"`.
  - Run with `runKINEMATIC()` and played on a QTimer in the viewport. Nothing is exported.

### STEP export carries no joints
- `Import/App/ExportOCAF2.cpp` writes shapes, components with their locations, colours,
  names and visibility, and nothing else.
- Joint objects have no shape, so they get no label.
- The STEP file keeps the solved placements and loses every joint, limit, ground and
  motion. Only the solver's own `.asmt` keeps the mechanism.

### What to take from FreeCAD
1. A joint references **an element of an occurrence**, not just the occurrence, and
   derives its frame from that element.
2. The frame is stored **relative to the referenced object**.
3. Ground is explicit.

What not to take: the solver, or placements computed from constraints.

---

## 2. STEP AP242 kinematics (ISO 10303-105, in the AP242 AIM)

### The structure
- **The mechanism is a graph.**
  - `kinematic_joint` is a topological `edge` whose `edge_start` and `edge_end` are
    `kinematic_link`s (AIM `t_kinematic_joint.html`).
  - `kinematic_topology_structure` (or its directed or network variants) collects the
    joints.
  - `mechanism_representation` holds a set of `pair_representation_relationship` and
    names that topology (AIM `t_mechanism_representation.html`).
- **A link** is a `rigid_link_representation`, tied to the part's
  `shape_representation` by `kinematic_link_representation_association`.
- **A pair** (`kinematic_pair`) has three attributes: `joint`, and two frames
  `transform_item_1` and `transform_item_2` (`rigid_placement` = `axis2_placement_3d`).
  - Each frame **lives in its own link's representation**: WR1 and WR2 of
    `pair_representation_relationship` (AIM `t_kinematic_pair.html`,
    `t_pair_representation_relationship.html`).
  - This is FreeCAD's Placement1/Placement2, said the standard's way.
- **Ground.** `kinematic_property_mechanism_representation.base` is the base link.
  `mechanism_base_placement` places it against a `kinematic_ground_representation`.
  - The CAx-IF practice: one BaseLink per mechanism; other fixed parts join it through
    `fully_constrained_pair`.

### Pair taxonomy (AIM `t_kinematic_pair.html`)
- **Low-order**, with booleans `t_x … r_z` for the free directions: revolute, prismatic,
  cylindrical, spherical, spherical_with_pin, planar, universal, homokinetic,
  unconstrained, fully_constrained.
- **With motion coupling:** screw (`pitch`), rack_and_pinion (`pinion_radius`), gear
  (`radius_first_link`, `radius_second_link`, `bevel`, `helical_angle`, `gear_ratio`),
  linear_flexible_and_pinion.
- **High-order:** point_on_surface, point_on_planar_curve, sliding/rolling surface and
  curve, planar_curve, surface.
- **Actuated:** `actuated_kinematic_pair`, which marks the driven directions.

### Limits
- `*_with_range` subtypes carry them. All are OPTIONAL; when both are present, lower < upper.
- Revolute: `lower_limit_actual_rotation` / `upper_limit_actual_rotation`.
- Prismatic: `…_actual_translation`.
- Cylindrical: both.
- Gear: `…_rotation_1`.

### States and motion
- **States.** `mechanism_state_representation` is a set of `pair_value` items, one per
  pair (for example `revolute_pair_value.actual_rotation`). This is exactly a named pose.
- **Motion, on paper.**
  - `interpolated_configuration_sequence` is a list of segments, each a state with a
    `t_parameter` and an interpolation (discontinuous, synchronous or linear).
  - `link_motion_representation_along_path` covers prescribed or resulting paths.
  - OCCT implements none of the motion entities: the master `StepKinematics/FILES.cmake`
    has no `kinematic_path` and no `interpolated_configuration_*`.

### OCCT
- **Reading only.** OCCT 7.6.0 added the entities
  ([commit 812ee2c9be](https://github.com/Open-Cascade-SAS/OCCT/commit/812ee2c9be),
  "0031388: support kinematics data in STEP format"). The 7.6.0 notes say only that
  kinematics "can be read now from a STEP file".
- **No XCAF layer.** RW classes exist (ReadStep, WriteStep), so the entities
  round-trip at the raw `StepData` level. But:
  - there is no `XCAFKinematics` package or `XCAFDoc_KinematicTool` on master or in any
    release;
  - `STEPCAFControl_Reader.cxx` and `STEPCAFControl_Writer.cxx` on master contain no
    "kinemat".
- **Consequence:** writing AP242 kinematics would mean building the entity graph by hand
  on a `StepData_StepModel`. OCP does not bind those classes, so that needs our own
  pybind or a hand-written Part 21 writer.
- The OCCT tracker was unreachable, so an unmerged branch cannot be ruled out.

### Adoption
- The live interoperability effort is **AP242 Domain Model XML**, not Part 21.
  - "STEP AP242 Ed.4 Domain Model XML Kinematics" Rel. 1.3, 2025-10-20, with Dassault
    and T-Systems as contacts
    (`mbx-if.org/.../rec_prac_ap242xml_kinematics_v1.3.pdf`). It gives CATIA and NX
    mapping notes:
    - fully_constrained is "fixed" in NX and "rigid" in CATIA;
    - universal is unsupported by NX, so it maps to two revolutes;
    - spherical_with_pin is supported by neither.
  - CAx-IF Round 48J test case KM2 (a gripper from Audi/VW, 2021) was run in that XML form.
- No public evidence that any mainstream CAD writes Part 105 kinematics into a Part 21 file.
- No evidence for Creo, SolidWorks or Inventor. FreeCAD writes none.
- In practice: **CATIA and NX exchange it as XML. Nobody exchanges it as Part 21, and
  OCCT only reads it.**

---

## 3. Mapping

How each cadgen concept maps to AP242, FreeCAD 1.0, OndselSolver and build123d.

| cadgen (`kinematics.py`, `articulation.py`) | AP242 pair / entity | FreeCAD 1.0 | Ondsel | build123d |
|---|---|---|---|---|
| `revolute(name, parent, child, axis, limits)` | `revolute_pair_with_range` (lower/upper_limit_actual_rotation) | Revolute (+AngleMin/Max) | ASMTRevoluteJoint + RotationLimit | `RevoluteJoint` → `RigidJoint` |
| `slider` | `prismatic_pair_with_range` (…_actual_translation) | Slider (+LengthMin/Max) | ASMTTranslationalJoint | `LinearJoint` |
| `cylindrical` (`.turn`, `.travel`) | `cylindrical_pair_with_range` (both ranges) | Cylindrical | ASMTCylindricalJoint | `CylindricalJoint` |
| `fastened` | **the same `kinematic_link`** (several occurrences, one rigid body); else `fully_constrained_pair` (CAx-IF's way to tie an extra fixed part to the base) | Fixed | ASMTFixedJoint | `RigidJoint`↔`RigidJoint` |
| — (no cadgen kind) | `spherical_pair` | Ball | ASMTSphericalJoint | `BallJoint` |
| — | `screw_pair` (pitch) | Screw | ASMTScrewJoint | — |
| — | `gear_pair` / `rack_and_pinion_pair` | Gears, Belt / RackPinion | ASMTGearJoint / RackPinionJoint | — |
| implicit: root of the mate tree | `kinematic_property_mechanism_representation.base` | GroundedJoint | fixGroundedPart | — |
| `parent` / `child` refs (`#label`) | `pair_representation_relationship` rep_1 / rep_2 → `kinematic_joint.edge_start/end` | Reference1 / Reference2 | marker I / J | the two joints' `parent` parts |
| `axis` (ref `#part.face`, or origin + dir) | `transform_item_1/2` (`axis2_placement_3d`, Z = axis, per link) | Placement1/2 from a referenced face/edge/vertex | markers | `Axis` on the joint |
| "authored placement is q = 0" | none; AP242 has frames, plus a state with values | frames coincide after solve | | `connect_to(angle=…)` is a value about a reference |
| `poses` | `mechanism_state_representation` + `pair_value`s | none (exploded views are placements) | | |
| `couple(name, {dof: ratio}, limits)` | **none.** `gear_pair`/`screw_pair`/`rack_and_pinion` couple the two links of one pair; `actuated_kinematic_pair` marks a driven direction; nothing names a virtual control over N pairs | Gears/Belt/Screw/RackPinion couple two parts, not N joints | | |
| articulation `controls` | none (closest: the actuated directions) | 1.1 Motion objects drive one joint each | | |
| `carries` | **derived.** It is link membership: the occurrences of a link, ownership pushed to the deepest link | derived by the solver | | |
| `handles` (what a drag writes) | none: user interface | drag is a solve (`preDrag`/`doDragStep`) | | |
| row `curve` (sampled nonlinear follower; only the URDF four-bar in `robot_payload.py`) | none for low-order pairs. A closed four-bar is a loop of revolute pairs, which needs a solver | a loop the solver closes | | |
| clips (`animation.py`, `_internal/animation_bake.py`) | `interpolated_configuration_sequence` (joint space, on paper; OCCT has no class) | 1.1 Simulation/Motion formulas, played in the viewport only | runKINEMATIC | — |

### Case: hypercar's doors
- Each door is today a `cylindrical` whose turn (62°) and travel (310 mm) are tied
  together by the `doors` coupling.
- A fixed lead between turn and travel inside one pair **is a `screw_pair`**:
  `pitch = 310 mm × 360/62 ≈ 1800 mm/rev`. FreeCAD's Screw joint is the same thing.
- What is left of `couple` is the left/right synchronisation: one control driving two
  pairs. That is the residue no standard models.

### Case: juno and tom
- The re-zeroing (limits minus the authored angle) disappears under AP242 semantics.
- The pair frames are the zero configuration, and the as-built angles are one
  `mechanism_state` ("as built"). Limits then stay the URDF's own.

---

## 4. Where joints should live in the xbf

### The options
| | XCAF subtree (proposed) | sidecar `<name>.xbf.json` | FreeCAD-style (element refs + solver) |
|---|---|---|---|
| Law 3, "the file is the document" | holds: travels with the xbf; `cadgen xbf export` sees it | breaks: an xbf copied alone loses its mechanism; the sidecar is build provenance | holds, but needs a solver at every read |
| Survives save/load | yes, proven below (BinXCAF, standard attributes only) | yes, as a separate file | — |
| References | **labels**: occurrence paths via `XCAFDoc_AssemblyItemRef`, pairs via `TDataStd_ReferenceList`; no name matching | names/`#labels` resolved against the tree again | element refs, re-resolved by the solver |
| Readable by the viewer | the engine reads it once and serves JSON (below) | the viewer reads JSON directly | the viewer would need a solver |
| Other OCCT tools | any OCCT reader (CAD Assistant, DRAW) loads it; only standard TDataStd/XCAFDoc attributes, no custom driver | invisible | — |
| STEP export | nothing (OCP cannot write AP242 kinematics; FreeCAD writes none either) | nothing | nothing |
| Future AP242 / OCCT kinematic tool | a 1:1 feed: entity names and attribute names are the standard's | a translation | a translation through a solve |

**Recommendation:** an XCAF subtree, shaped like AP242. The sidecar keeps provenance (which source line declared each pair), as it does for element names.

### The attribute scheme (prototyped)
- Root label `Main():100`, marked with a `TDataStd_UAttribute` of a cadgen GUID, so a
  reader finds it by GUID, not by tag. Tags 1–10 belong to `XCAFDoc_DocumentTool`'s
  tool labels.
- Every label has a `TDataStd_Name`.
- A label's AP242 entity is a `TDataStd_NamedData` string `entity`.
- Its AP242 attributes are NamedData reals and real arrays, named exactly as the
  standard names them.

```
0:1:100  "Kinematics"   UAttribute(cadgen kinematics GUID)
         NamedData{schema:"cadgen.kinematics/1", angle_unit:"deg"}   (length: the XCAF doc's mm)
  0:1:100:m  <mechanism>   NamedData{entity:"MECHANISM_REPRESENTATION"}
             ReferenceList[ground link]          ← kinematic_property_mechanism_representation.base
    :1 "links"
      :1:k <link>          NamedData{entity:"KINEMATIC_LINK"}
        :1:k:i             XCAFDoc_AssemblyItemRef(occurrence path "0:1:1:3/0:1:1:3:2")
                           (one per member occurrence: a fastened rider is a member)
    :2 "pairs"
      :2:j <pair>          NamedData{entity:"REVOLUTE_PAIR_WITH_RANGE",
                                     transform_item_1:[9], transform_item_2:[9],   ← axis2_placement_3d:
                                     lower_limit_actual_rotation, upper_limit_…}      location, axis(Z), ref_direction(X),
                           ReferenceList[link 1, link 2]   ← edge_start, edge_end     each in its link's frame
        :2:j:1 (optional)  XCAFDoc_AssemblyItemRef(occurrence + SubshapeIndex = the datum face)
                           ← FreeCAD's Reference: the element the frame was derived from (provenance)
    :3 "states"
      :3:s <pose>          NamedData{entity:"MECHANISM_STATE_REPRESENTATION"}
                           ReferenceList[pairs], RealArray[values], IntegerArray[dof: 0 rot, 1 trans]
    :4 "couplings"         (cadgen's extension: no AP242 entity)
      :4:c <control>       NamedData{entity:"cadgen:COUPLING", limits:[lo, hi]}
                           ReferenceList[pairs], RealArray[ratios], IntegerArray[dofs]
```

### Rules
- **A pair's semantics are AP242's.** At pair value 0, `transform_item_1` (on link 1)
  and `transform_item_2` (on link 2) coincide.
  - A revolute pair turns about their common Z, measured from X.
  - The child's world placement at value q is `parent_world · item_1 · R_z(q) · item_2⁻¹`.
- **cadgen's "authored = q = 0" is a special case,** not a rule.
  - The engine can write both frames from the as-built placement, so the as-built state is zero.
  - Or it can write the frames from build123d's joints and store the as-built values as
    a state (juno, tom).
- **A link is a set of occurrences.**
  - `fastened` becomes membership.
  - An occurrence belongs to one link. The ground link holds everything that does not move.
- **Variable sets are references plus arrays, never NamedData keys.** OCP exposes no key
  iterator for `TDataStd_NamedData` (its DataMaps have `Find`/`IsBound` only), so the
  fixed attribute names come from the schema.
- **Units.** Lengths are the document's (mm); angles follow `angle_unit` (degrees, as
  cadgen's controls are).

### The prototype
`roundtrip.py`, saved beside this file:
1. It makes a two-part `hinge` (base, arm) with the engine's own
   `document.collect` + `to_xcaf`.
2. It adds the subtree above: two links, one `REVOLUTE_PAIR_WITH_RANGE`, a ground
   reference, a state "raised" (elbow = 90°) and a coupling "lift" (elbow += 120·lift).
3. It saves with `document.save` (BinXCAF).
4. It reads the file into a fresh document with `document._read` (the BinXCAF retrieval
   driver).
5. It dumps the subtree, checks `Loaded`, evaluates the pose from the read-back frames,
   and writes a STEP through `exports.write_step`.

Output (abridged from `roundtrip.out`):

```
xbf: out/hinge.xbf 11459 bytes
0:1:100 "Kinematics" marker=5b0e7a64-… data={schema: cadgen.kinematics/1, angle_unit: deg}
  0:1:100:1 "hinge" {entity: MECHANISM_REPRESENTATION} refs=[0:1:100:1:1:1]          (ground = base)
    links: 0:1:100:1:1:1 "base" KINEMATIC_LINK  member occurrence "0:1:1:3/0:1:1:3:1"
           0:1:100:1:1:2 "arm"  KINEMATIC_LINK  member occurrence "0:1:1:3/0:1:1:3:2"
    pairs: 0:1:100:1:2:1 "elbow" REVOLUTE_PAIR_WITH_RANGE  lower -10.0 upper 120.0
           transform_item_1 [40,0,20, 0,1,0, 1,0,0]   (in base's frame)
           transform_item_2 [ 0,0,10, 0,1,0, 1,0,0]   (in arm's frame: arm is placed at (40,0,10))
           refs [0:1:100:1:1:1, 0:1:100:1:1:2]
    states: "raised" MECHANISM_STATE_REPRESENTATION refs [elbow] values [90.0] dofs [0]
    couplings: "lift" cadgen:COUPLING limits [0,1] refs [elbow] values [120.0] dofs [0]
engine Loaded still reads it: [('hinge/base', 0), ('hinge/arm', 1)]
arm tip at q=0  : [160.0, 0.0, 20.0] (as built: (160, 0, 20))
arm tip at q=30 : [143.923048, 0.0, -40.0]
arm tip at q=90 : [40.0, 0.0, -100.0]
STEP written: 33350 bytes; kinematic entities in it: none
```

What the run shows:
- Every attribute round-trips through BinXCAF, including `XCAFDoc_AssemblyItemRef`.
- The engine's reader is unaffected.
- The pose computed from the stored frames is exact; the q = 30° value matches the
  closed form (120 cos 30°, −120 sin 30°).
- The STEP export carries nothing, as expected.

### What the engine has to add
- **`to_xcaf` writes the subtree,** because a document is rebuilt from scratch each
  build. Label entries are deterministic for the same tree order, so the references are
  stable.
- **`Loaded` reads it into the structure** (§6).
- **Declaration source.** Either the model passes a declaration, or the engine harvests
  build123d's connected joints (`Joint.connected_to`) from the result's objects, which
  `collect` already walks through `obj.__dict__`.

---

## 5. Motion

### What exists elsewhere
- **AP242** has `interpolated_configuration_sequence`: mechanism states at
  `t_parameter`s with linear interpolation, so it is joint-space only. No tool writes it
  as Part 21, and OCCT has no class for it.
- **FreeCAD** has no native animation in 1.0. In 1.1, motions are formulas over joints,
  played in the viewport, never exported.
- **glTF** is the format that carries animation, and cadgen's bake already writes glTF
  samplers (CUBICSPLINE transforms, skins for tubes).

### cadgen's clips are not joint-space
Its clips are rigid transforms per occurrence plus opacity, visibility and tube
deformation (`animation_bake.py`). The models' own clips (juno's gaits, hypercar's tour)
re-describe the forward kinematics in JavaScript because "animation is deliberately
ignorant of kinematics". AP242 states cannot express an explode, a fade or a bending tube.

### Recommendation
1. **Clips stay derived data, outside the document.**
   - They are baked at build into keyframes and carried in the sidecar.
   - The `@glb` export written by the build animates from them (glTF `animations`).
   - `cadgen xbf export --glb` from an xbf alone writes a still. That is honest:
     the clip is the model's code, not the document's content.
2. **Joint-space motion belongs in the document,** as the standard shapes it: a sequence
   of `mechanism_state`s with times, under `states`, with an
   `entity: "INTERPOLATED_CONFIGURATION_SEQUENCE"` label referencing them.
   - It is cheap (one value per pair per key) and survives with the file.
   - The viewer plays it through the same articulation as a pose.
   - tom's showcase, a "validated performance on these mates", is exactly this.
   - It would let juno's gaits drop their re-implemented forward kinematics.
   - Offer it as the way a clip moves joints. Keep rigid/opacity/tube clips as they are.
3. **No AP242 Part 21 writing of motion.** Nobody reads it.

---

## 6. Position evaluation

### Where the pose is computed
**The page evaluates it.**
- For a tree mechanism, the viewer's affine rows plus rigid deltas (`core/common/articulation.js`)
  *are* exact forward kinematics: a fold of rigid transforms, exact to float precision.
  It is not an approximation.
- So the Position tool stays client-side and instant: no round trip per drag frame, and
  it fits law 19 (network never blocks).

**The engine evaluates it with the live kernel only where geometry must be posed:**
- a posed export (STEP or GLB at a pose);
- a snapshot at a pose;
- interference or clearance at a pose;
- measuring between parts at a pose;
- anything that is a loop. Loops are out of scope by design, as today.

The engine evaluates `parent_world · item_1 · R(q) · item_2⁻¹` with `gp_Trsf`, as the
prototype does.

### What the structure JSON must carry
`GET /api/doc/<id>` gains `kinematics`, made by the engine from the subtree. It is the
existing articulation artifact (schema 2), with occurrence ids replaced by instance
indices:

```json
"kinematics": {
  "schemaVersion": 3,
  "controls": [{"id": "elbow", "label": "elbow", "unit": "deg", "min": -10, "max": 120, "default": 0}],
  "joints":   [{"id": "elbow", "parent": null, "kind": "revolute",
                "origin": [40, 0, 20], "axis": [0, 1, 0],
                "turn": {"bias": 0, "terms": [["elbow", 1], ["lift", 120]]}}],
  "carries":  {"elbow": [1]},
  "handles":  [{"id": "elbow", "joint": "elbow", "dof": "turn", "control": "lift", "weight": 120, …}],
  "poses":    {"raised": {"elbow": 90}},
  "opening":  {"elbow": 0, "lift": 0}
}
```

The rules for building it:
- **origin and axis** are in the rest world: `parent_world · item_1`'s location and Z.
- **joint kind** comes from the pair entity (screw → a turn row and a travel row with
  `travel = pitch/360 · turn`).
- **carries** are the instance indices of the link's members. When a link is also an
  ancestor of deeper links, ownership goes to the deepest, as today.
  - Use instance indices rather than paths: two components may share a name, and
    `instances[i]` is what the page already holds.
- **controls, handles and opening** come from the pairs (sub-DOFs `.turn` and `.travel`)
  and from the couplings, exactly as `step_articulation` mints them today.
- **opening** is the as-built state when there is one (non-zero for juno and tom), else zero.
- **A screw pair contributes no control of its own beyond its rotation.**

For a document the page already holds, `tree_structure` builds the same from the tree in
memory. Its rule-for-rule twin of `Loaded` extends to the subtree.

---

## 7. Open questions

1. **Declaration surface.** Harvest build123d's own connected joints
   (`RevoluteJoint`/`LinearJoint`/`CylindricalJoint`/`BallJoint` → `RigidJoint`), or a
   declaration the decorator takes (`@xbf(kinematics=...)`), or both?
   - Harvesting removes juno's second description.
   - It depends on whether `.joints` and `connected_to` survive the memo: are they set on
     objects the memo hands back?
   - Groups (`#door:left` with eleven riders) need link membership that build123d joints
     do not express.
2. **Sub-assemblies.** A child model's own mechanism (hypercar's systems are child models):
   - Does it nest as a sub-mechanism, AP242's `kinematic_link_or_assembly`?
   - Or is everything flattened into the root mechanism with deep occurrence paths?
     The prototype supports deep paths.
3. **Which frame convention.** AP242 Part 21 puts a pair's frames in its links' frames;
   the CAx-IF XML practice uses absolute occurrence placements. The xbf should keep
   link-local frames (they survive re-placing the parent) and convert at any writer.
4. **Pairs beyond today's four kinds.** Spherical, screw and gear are free in the scheme.
   - Does the viewer grow a three-DOF ball row?
   - Does `couple` survive once screw and gear exist? The hypercar's left/right
     synchronisation is the only coupling left once screw exists.
5. **Writing AP242 at all.**
   - Part 21 kinematics: OCCT has the RW classes, OCP has no bindings, and nobody reads
     them in practice.
   - AP242 Domain Model XML is what CATIA and NX exchange.
   - Neither is worth building before a user asks.
   - Separately, should `write_step` move from AP214IS to AP242DIS for its own sake
     (names, PMI)?
6. **URDF.** The mechanism subtree is a sufficient source for a URDF export: links,
   revolute/prismatic/continuous joints, limits, mimic = coupling. Should `urdf` read the
   xbf instead of its own `lib/chain.py` mirror?
7. **Element references.** Should a pair keep its datum element reference (FreeCAD's
   Reference: a face ordinal through `AssemblyItemRef.SetSubshapeIndex`)?
   - It would let a re-build re-derive the frame from the named face rather than from
     stored numbers.
   - Element names already survive edits through the memo's trace.

---

## 8. Recommendation

Make the mechanism native to the xbf as an AP242-shaped XCAF subtree, in the scheme
prototyped above:
- links as sets of occurrence references (`XCAFDoc_AssemblyItemRef`);
- pairs with two per-link `axis2_placement_3d` frames, AP242's `*_with_range` limit names
  and AP242 entity names;
- a ground link;
- named poses as `mechanism_state`s.

It survives BinXCAF save and load, uses only standard OCCT attributes, and can feed a
future OCCT kinematic tool or AP242 writer one to one. STEP will carry no joints, as
FreeCAD's does not.

**Replace with the standard:**
- `revolute`/`slider`/`cylindrical`/`fastened` by revolute/prismatic/cylindrical pairs
  and link membership;
- cadgen's "authored placement is q = 0" by AP242's frames-plus-state, which deletes the
  re-zeroing in juno and tom;
- `poses` by mechanism states;
- "parent/child" by links with a ground;
- the turn/travel coupling inside one door by a `screw_pair`;
- and gain spherical, screw and gear pairs for free.

**Keep as cadgen's own,** marked `cadgen:` in the tree or derived and never stored:
- `couple`, reduced to what no standard has: one control driving several pairs;
- the viewer's `controls`, `handles`, `carries` and affine rows. These are derived, not
  stored: a projection the engine computes for the Position tool, which stays
  client-side and exact for trees;
- the URDF four-bar `curve`, which stays a robot-description feature;
- clips: rigid/opacity/tube choreography stays derived and baked into the sidecar and the
  GLB's glTF animation, with joint-space sequences, when added, stored as AP242-shaped
  configuration sequences in the document.

Do not adopt FreeCAD's solver or placements computed from constraints. Do take its
habit of tying a joint frame to a referenced element.
