# Function couplings in AP242 terms: scope

Date: 2026-10-22. Base: `origin/claude/relaxed-knuth-hhrtva` at `bf236ba28`. Read-only: no code
changed, nothing committed.

Question (Jake): `couple(assembly, name, *, range=(0, 1), lock=False, **joints)` moves each joint by
ratio, joint = as built + ratio × c. A slider-crank, a cam or a four-bar's follower needs a
function of the control. Is a function-valued coupling in line with the kinematics standards
FreeCAD and other CAD tools support, how does it get represented in a file, and what is its AP242
representation?

## The answer in brief

1. No standard and no CAD tool I checked has a joint-to-joint function as a primitive. Their
   explicit joint relations are linear: gear, rack and pinion, screw, and the two- and three-joint
   couplers that AP242 edition 4 added in 2025.
2. They reach non-linear motion two other ways. Geometry plus a solver: cam pairs (a point or a
   curve on a curve) and closed loops. Or sampled results: mechanism states over a parameter, or
   link placements along paths.
3. A function coupling is therefore no standard relation. Its stored form is a standard
   representation, though: an AP242 `INTERPOLATED_CONFIGURATION_SEQUENCE`, mechanism states at
   parameter values with linear interpolation. Part 105 does not require the parameter to be
   time, so the control can be the parameter.
4. Files: the xbf keeps it, in the mechanism subtree that already mirrors AP242's names. A STEP
   carries no kinematics, before or after this change. A GLB shows it only through a motion that
   sweeps the coupling.

Recommendation:
- Extend `cadgen_coupling` with a per-joint relation: `ratio` (exact, as today) or `curve`
  (sampled).
- Store a curve as a child `INTERPOLATED_CONFIGURATION_SEQUENCE`: nodes in the control, absolute
  pair values, `linear_interpolation_type`, and the tolerance it was sampled to.
- Sample with the motions' adaptive sampler, but interpolate linearly, not by spline.
- Emit no AP242 higher pair.
- Size: 7 to 8 days for tree mechanisms. Add 2 to 3 days so a locked function coupling can close
  a loop, which I recommend.

Two things the proposal as stated would trip on, found while scoping:
- Motions resolve a coupling to pair values at document time and ease between them in pair
  space. That is exact for a ratio and wrong for a function. A sweep of a crank from 0 to 360
  stores the slider at 0 at both ends, so the slider never moves.
- The memo keys an assembly's `xbf_couplings`. A callable has no key (`_normalize` raises
  `_Unkeyable`), so every keyed operation on or above that assembly would run plain.

Both have a fix in the plan (section 6).

---

## 1. Which AP242 entities express each relation

Sources: ISO 10303-105:2019 (Part 105, the kinematics resource AP242 uses), read through the
STEP Tools AIM pages and the SMRL clause text; the CAx-IF / JT-IF *Recommended Practices for STEP
AP242 Edition 4 Domain Model XML Kinematics*, release 1.3 (2025-10-20). Capitals are Part 21
(AIM) entities. CamelCase names are the edition 4 XML Domain Model.

### Table 1: relations

| Relation | AP242 entity | What it stores | How a reader gets the driven value |
|---|---|---|---|
| Ratio between two rotations (gears, belts) | `GEAR_PAIR` (+ `_WITH_RANGE`, `GEAR_PAIR_VALUE`), a subtype of `LOW_ORDER_KINEMATIC_PAIR_WITH_MOTION_COUPLING`. Edition 4 XML: `KinematicPairWithMotionCoupling`, kind `gear_pair`. | Part 21: a pair between the two gear links, frames at the rolling circles, `radius_first_link`, `radius_second_link`, `bevel`, `helical_angle`, `gear_ratio`; value `actual_rotation_1`. Edition 4: `Pairs` = the two revolute pairs, plus `Radius` or `CouplingRatios` (Radius1/Radius2, negative when the gears turn the same way or their axes are opposed). | Part 21 derives `actual_rotation_2 = -actual_rotation_1 × gear_ratio`, and the gear pair closes a loop with the two gears' revolutes. Edition 4 relates the two pairs directly. |
| Turn with travel on one axis (lead screw, helical hinge) | `SCREW_PAIR` (`pitch`) | One pair, one value: `actual_rotation`. | `actual_translation` derived from the pitch. |
| Turn to travel on crossed axes | `RACK_AND_PINION_PAIR` (`pinion_radius`). Edition 4 XML: kind `rack_and_pinion_pair` (Pairs = prismatic, revolute; `Radius` or `CouplingRatios` = 2πR). | Value: `actual_displacement`. | Rotation derived. I could not verify the formula: the STEP Tools page shows `actual_rotation := 0.0`, which looks like a schema artifact. |
| Linear relation among 2 or 3 pairs | Edition 4 XML only: `KinematicPairWithMotionCoupling`, kinds `two_joint_coupler`, `three_joint_coupler`. I found no Part 21 name for it. | `Pairs`, `Scales`, `CouplingTypes` (rotation or translation per pair). | The receiving solver holds s1·d1 + s2·d2 (+ s3·d3) = 0. |
| Cam and follower | `POINT_ON_PLANAR_CURVE_PAIR` (`pair_curve`, `orientation`); `SLIDING_CURVE_PAIR` and `ROLLING_CURVE_PAIR`, subtypes of `PLANAR_CURVE_PAIR` (`curve_1`, `curve_2`, `orientation`); ranges as trimmed curves. | The cam profile as a curve on the cam link (the CAx-IF practice references exact B-rep), the follower's point or curve. Value: the contact point on each curve. | A contact solver. No lift law is stored. |
| Linkage loop (slider-crank, four-bar) | Plain `REVOLUTE_PAIR` and `PRISMATIC_PAIR`. The loops sit in a `KINEMATIC_TOPOLOGY_NETWORK_STRUCTURE` (`items: SET OF kinematic_loop`). The input pair can be an `ACTUATED_KINEMATIC_PAIR` (`r_z: bidirectional`). | Nothing beyond the pairs. | A loop solver. |
| A general function | No relation entity. Two sampled forms, Table 2. | | Interpolation. No solver. |

### Table 2: the sampled forms

| Form | Entities | Each sample holds | Notes |
|---|---|---|---|
| States over a parameter | `INTERPOLATED_CONFIGURATION_SEQUENCE` of `INTERPOLATED_CONFIGURATION_SEGMENT`s (`state`, `t_parameter`, `interpolation`), in an `INTERPOLATED_CONFIGURATION_REPRESENTATION` whose context, `GEOMETRIC_REPRESENTATION_CONTEXT_WITH_PARAMETER`, has an optional `parameter_unit`. A `KINEMATIC_CONTROL` uses it as a program; a `KINEMATIC_ANALYSIS_RESULT` as a result. | A `MECHANISM_STATE_REPRESENTATION`: a pair value per pair (`REVOLUTE_PAIR_VALUE.actual_rotation`, `PRISMATIC_PAIR_VALUE.actual_translation`, `CYLINDRICAL_PAIR_VALUE.actual_rotation` and `actual_translation`). | `interpolation_type`: `linear` (same rate, constant), `synchronous` (same rate, "may vary proportionally among the pairs"), `discontinuous`, `undefined`. The last segment's type says how to return to the first, so `discontinuous` there means open. No spline. Clause 8.2: "t is a non-decreasing parameter as motion progresses"; `parameter_unit` "need not be specified". |
| Link placements along paths | `KINEMATIC_PATH_DEFINED_BY_NODES` (`placement_start`, `t_start`, then `PATH_NODE`s: `POINT_TO_POINT_PATH`, `LINEAR_PATH`, `CIRCULAR_PATH`, `CURVE_BASED_PATH`, each with `placement_end`, `t_end`, `transition`), in a `PRESCRIBED_PATH` or `RESULTING_PATH` (`controlling_joints`). | A frame per moving link per node. | Frames, not poses. |

### What implementers exchange

The CAx-IF practice for edition 4 carries mapping notes for CATIA and NX. It exchanges:
- mechanisms as low-order pairs, higher pairs on exact curves, and the new motion couplings
  (CATIA and NX Gear, Cable and Rack; NX's two/three joint couplers; NX's Mechanical Cam Coupler as
  `sliding_curve_pair`);
- motion as link placement snapshots: `LinkMotionAlongPath`, `KinematicPathDefinedByNodes`,
  `PointToPointPath` only, transition `discontinuous` recommended (CATIA's Animation).

It does not exchange:
- mechanism states: `Mechanism.MechanismState` is greyed out, and grey means "their use is
  discouraged";
- interpolated configuration sequences: no template;
- motion "fully defined based on the kinematic geometrical constraints": out of scope.

Edition 4 also deprecated the gear and rack mappings through
`LowOrderKinematicPairWithMotionCoupling` in favour of `KinematicPairWithMotionCoupling`, which
relates pairs rather than links.

### What this means for cadgen

- A ratio coupling between joints has the shape of edition 4's coupler: a relation between pairs.
- A locked one maps onto two-joint couplers chained from its first joint (s1 = r_k, s2 = −r_1).
  The practice says "displacement" without saying from where; if it means from each pair's zero,
  the as-built values must be folded in.
- An unlocked one is a control, not a constraint. No standard has a virtual degree of freedom,
  so it keeps cadgen's own entity, as today.
- A function coupling has no relation entity anywhere. Its samples are an interpolated
  configuration sequence with the control as t.

---

## 2. The recommended representation

### Authoring

`couple(assembly, name, *, range=(0, 1), lock=False, **joints)` keeps its signature. A joint takes:
- a number, or a sequence for a joint of several values: a ratio, as today. Joint = as built +
  ratio × c.
- a callable: a function of c giving the joint's change from as built (a number, or a tuple for a
  joint of several values). Joint = as built + f(c).

I chose the change, not the joint's value, for three reasons:
- A ratio is then the case f(c) = ratio × c, and the docs stay one sentence.
- Couplings sharing a joint still add.
- The author never needs the joint's zero. `lambda c: g(30 + c) - g(30)` works whatever the frames.

f(0) must be 0, which is "a coupling never moves the build". The harvest refuses anything else.

Illustration (not tested; signs depend on the joints' axes):

```python
R, L = 40.0, 120.0                     # crank radius, rod length (mm); as built the crank is at 0

def rod_angle(c):                      # the rod's angle to the frame, crank at c degrees
    return -math.degrees(math.asin(R * math.sin(math.radians(c)) / L))

def slider_travel(c):                  # the slider's travel from as built (mm)
    t = math.radians(c)
    return R * math.cos(t) + math.sqrt(L ** 2 - (R * math.sin(t)) ** 2) - (R + L)

# The rod's joint sits on the crank, so its value is the rod's angle less the crank's.
couple(engine, "crank", range=(0, 360), lock=True,
       crank=1, rod=lambda c: rod_angle(c) - c, slider=slider_travel)
```

The example shows the risk too: forget the `- c` on the rod and the linkage breaks with no error.
Phase 2 (section 6) catches that.

### Sampling

- One grid per coupling over `[lower, upper]`. All its function terms share it.
- 0 is always a node. That node holds the as-built values exactly.
- The motions' sampler (`_sampled`), with the interpolation as an argument: eight equal steps,
  each halved while the interpolant misses the function by more than the tolerance at a quarter,
  a half or three quarters of it.
- The motions' tolerances: `ANGULAR_TOLERANCE` (1e-3 rad, 0.057°) per angle, `LINEAR_TOLERANCE` ×
  model size per distance, at most `MOST_KEYS` (4,097) nodes.
- Linear interpolation, for three reasons:
  1. It is AP242's `linear_interpolation_type`. A reader of the standard computes cadgen's value
     exactly.
  2. It never overshoots its nodes. Checking the joint limits at the nodes covers the whole range.
  3. The page inverts it segment by segment.
- cadgen already ships this shape. The robot viewer stores a URDF four-bar's crank as a `curve`
  over its driver (`robot_payload.py`, `_four_bar_curve`): linear between keys, 0 a key, a
  `period` for a driver that turns without limits, played by `curveValue` in
  `packages/core/src/common/articulation.js`.

I measured the cost with a scratch replica of `_sampled` (appendix). Model size 250 mm, so 0.25 mm
and 0.057°:

| Function over a full turn | Nodes, linear | Nodes, spline | A linear reader of the spline's nodes | Spline overshoot |
|---|---|---|---|---|
| Slider-crank, R 40, L 120 | 51 | 31 | 2.1 × the tolerance | none |
| Four-bar follower, 35/90/80/100 | 58 | 35 | 4.1 × the tolerance | 0.0003° |
| Disc cam: cycloidal 10 mm rise over 120°, dwell, return, dwell | 67 | 112 | 1.9 × the tolerance | −0.055 mm and 10.092 mm, past a follower range of (0, 10) |

Linear costs tens of nodes. The spline costs a standard reader 2 to 4 times the error bound, and on
the cam it would make a follower with range (0, 10) refuse its own coupling.

One honest caveat: the checks bound the error at the checked fractions only. On a 400,001-point
grid the four-bar's linear curve peaks at 1.0012 × the tolerance between checks. Sample to 0.9 of
the tolerance if the bound must hold everywhere.

### In the document

```
:6 "couplings"
  :c <name>          NamedData{entity: cadgen_coupling, lower_limit, upper_limit, lock}
                     ReferenceList[pair per ratio term], RealArray[ratio], IntegerArray[value index]   (as today)
    :1 "curve"       NamedData{entity: INTERPOLATED_CONFIGURATION_SEQUENCE,
                               cadgen_angle_tolerance (deg), cadgen_length_tolerance (mm)}
      :k <c_k>       NamedData{entity: INTERPOLATED_CONFIGURATION_SEGMENT, t_parameter: c_k,
                               interpolation: linear_interpolation_type
                                              (the last: discontinuous_interpolation_type)}
                     ReferenceList[pair per value], RealArray[value], IntegerArray[value index]
                     (a MECHANISM_STATE_REPRESENTATION's rows: each function term's pair value at c_k)
```

- Ratio terms stay exact rows. One coupling may hold both kinds.
- Values are absolute, because an AP242 state holds pair values. The page subtracts the as-built
  value to get a coupling's share.
- The segments reuse `_state` and `_read_state`. A motion's segments already have this shape.
- The tolerances use the document's units, so the file states its own error bound.
- A motion's segments carry `cadgen_ease` instead of AP242's `interpolation`. A coupling's
  interpolation is one the standard names, so it carries the standard attribute.

### In the structure the page reads

```json
"crank": {"range": [0, 360], "lock": true,
          "terms": [["crank", 0, 1.0]],
          "curve": {"nodes": [0, 5.625, 11.25, 22.5, 33.75, 45, ...],
                    "terms": [["rod", 0, [0, -7.4973, -14.9786, -29.8287, -44.4222, -58.6330, ...]],
                              ["slider", 0, [0, -0.2567, -1.0226, -4.0251, -8.8169, -15.0967, ...]]],
                    "tolerance": {"deg": 0.0573, "mm": 0.25}}}
```

(Values from the measured slider-crank, with crank, rod and slider at 0 as built. The rod's
values are its joint's: its angle less the crank's. Subtracting a linear term moves no node.)

### Refused by name at document time

- The function raises, returns no number, the wrong arity, or a non-finite value.
- f(0) is not 0 within the tolerance. The message names the as-built value.
- A joint stands past its limits at some node (this coupling alone).
- The curve does not settle within 4,097 nodes, which is what a jump does.
- As today: a joint in no pair, a locked joint another coupling drives, a name a joint has.

### Should the engine also emit the exact AP242 higher pair? No.

- The engine has no curve to reference. The function is opaque code, and nothing ties it to the
  cam's profile edge.
- Nothing would read it. cadgen's STEP carries no kinematics, and cadgen has no contact solver.
- The pair's geometry and the samples could disagree, with no rule for which wins. Law 16 forbids
  that kind of output.
- Linear couplings need nothing new either. If a STEP kinematics writer ever exists, it maps:
  - a locked ratio coupling between two pairs to an edition 4 coupler (`gear_pair`,
    `rack_and_pinion_pair` or `two_joint_coupler`);
  - a locked turn and travel on one cylindrical joint to `SCREW_PAIR`, pitch = travel per 360°
    (the hypercar door);
  - a function coupling to its sequence, plus `KINEMATIC_PATH_DEFINED_BY_NODES` per moving link
    for the CAx-IF importers.
- A real cam pair needs a different authoring surface: the model names the profile edge and the
  follower, the engine solves contact on the B-rep for the samples, and it records
  `POINT_ON_PLANAR_CURVE_PAIR` or `SLIDING_CURVE_PAIR`. Section 6 sizes it, not for now.

---

## 3. What another reader can evaluate, and what the samples lose

### Readers

| Reader | What it gets |
|---|---|
| cadgen's engine and page | The curve as stored. |
| Any OCAF program opening the xbf (a BinXCAF document) | The numbers under AP242 names. It reproduces cadgen's values only by following the layout in `mechanism.py`'s docstring. The interpolation is one line. |
| Any STEP reader | Nothing. A STEP is placements only, posed or not. |
| OCCT's STEP reader, if cadgen ever wrote the sequence into STEP | Nothing. OCCT 7.9.3's keyword table (checked in `libTKDESTEP`) has the pairs, pair values, ranges, `MECHANISM_REPRESENTATION` and `MECHANISM_STATE_REPRESENTATION`. It has no `INTERPOLATED_CONFIGURATION_*`, `KINEMATIC_PATH_*` or `LINK_MOTION_*` entity. |
| CATIA or NX through the CAx-IF practice | Probably nothing from a sequence, since the practice has no template for one. They take motion as link paths. |
| glTF players | A motion that sweeps the coupling, as a clip. A coupling no motion sweeps is not in the GLB. |

### What the samples lose against the Python function

| Loss | Size | Effect |
|---|---|---|
| Interpolation error | At most the tolerance at each check (0.057°, 1e-3 of the model's size); up to about 0.1 % more between checks (measured on the four-bar). | Invisible in a pose. A feature narrower than a quarter interval can slip between checks, as in motions. |
| The range | The function outside `[lower, upper]` is gone. | A pose or a motion outside it is refused. A crank turning twice needs `range=(0, 720)` until couplings get a `period`. |
| Derivatives | The slope jumps at every node. | Positions are fine. Velocity, acceleration and a cam's jerk law cannot be read from the file. |
| The inverse | f(c) = v has zero, one or many solutions. | The slider stands at −0.2567 mm at c = 5.625 and at c = 354.375. A cam's dwell maps a whole interval to one lift. The page must choose. |
| The formula | Not in the file. Law 1 keeps pointers and digests, never source. | Regenerating needs the code. |
| The constants | The samples hold this build's lengths and size. | Any edit to them, or to the model's size, resamples, as function motions do. |

---

## 4. Viewer, snapshot, exports and the baker

| Surface | Path today | Change |
|---|---|---|
| Position slider (`packages/ui/src/renderers/xbf/XbfPositionSection.jsx`) | One slider per coupling over its range. A locked coupling hides its joints' sliders. | None in the UI. `xbfCouplingControls` lists the curve terms' pairs in the tooltip. |
| FK on the page (`packages/core/src/lib/xbf/xbfKinematics.js`) | `coupledValues` adds each coupling's share. `xbfLinkPlacements` places links from pair values. | A curve term's share is the interpolated value less the as-built value. `xbfLinkPlacements` is unchanged. The search and lerp could come from `curveValue` in `articulation.js`, exported. |
| The slider's clamp (`xbfCouplingWrite`) | An interval from the ratios and the joints' limits. | For curve terms: walk the segments from the current c toward the request. Stop where a joint, with the other couplings' shares, reaches a limit. Exact per segment. |
| Where a pose stands on a coupling (`xbfCouplingValues`) | (v − as built) / ratio, or 0 off the line. | Use a ratio term when there is one. Else solve each segment of the first curve term, check the others, and keep the solution nearest the slider's last value. 0 when none fits, as today. |
| `--pose` on `snapshot` and `export`; `POST /pose` | `resolve` → `_coupled` → `link_worlds` / `posed_instances`. | `_coupled` evaluates curve terms. Nothing else changes. |
| Posed STEP | Placements from `posed_instances`. | None. |
| `--part` subtree (`export/tree.py`, `_mechanism`) | Shares a coupling's ratios with the subtree. | Shares its curve terms too. |
| Configurations | Resolved to pair values at document time. | Exact for one pose. Unchanged. |
| Motions (`_motions`) | Each key resolved to pair values at document time, then eased in pair space. | Breaks for functions (the still slider above). A motion that moves a function coupling is resampled instead: ease the coupling's value between waypoints, map it through the curve, keep the result as a function of time's samples (`_sampled`, spline, as function motions are). A waypoint naming a configuration gives the coupling value that configuration was authored with (0 when it names none). A waypoint setting one of the coupling's joints directly is refused, as a pose setting a coupling and one of its joints is today. Keeping c in the motion's states instead would put a cadgen-only row in an AP242 state. |
| GLB clip baker (`export/motion.py`) | `Timeline` → FK → adaptive keys. | None. Resampled motions are pair-value keys. |
| The build | | c = 0 is a node holding the as-built values exactly, and an f(0) off 0 by more than the tolerance fails the build. The as-built pose, every STEP, STL and 3MF, and the GLB's rest nodes stay put. |
| The memo (`kernel/memo.py`, `_shape_key`) | Keys an assembly's `xbf_configurations` and `xbf_couplings`, so a hit cannot hand back a stale coupling. | A callable raises `_Unkeyable`. Key it by its code reach, as body retention keys a body (`sources.py`, `Symbols`). |

---

## 5. FreeCAD, Onshape and SolidWorks

| | FreeCAD 1.1 (Assembly, Ondsel solver) | Onshape | SolidWorks | cadgen today | cadgen with curves |
|---|---|---|---|---|---|
| Linear joint relations | `Gears`, `Belt`, `RackPinion`, `Screw` joints, from radii and pitch | Gear, Rack and pinion, Screw and Linear relations, each a constant ratio | Gear, rack and pinion and screw mates; Linear/Linear coupler (a ratio) | Ratio couplings, as a control or locked | Same |
| Non-linear relation between joints | None as a joint | None as a relation | None as a mate | None | A function of the control per joint, stored as samples |
| Cam | No cam joint among its 13 types | Tangent mate (point to curve, surface to surface), solved | Cam-follower mate (cylinder, plane or point to tangent extruded faces), solved | None | The author's lift law, no geometry |
| Loops (four-bar, slider-crank) | Solved | Solved | Solved | Kept, not posed | Phase 2: closed by a locked function coupling and checked at every node |
| Motion over time | 1.1 Simulation: one joint driven by a formula of time, e.g. `C + AMP*sin(VEL*time - PHASE)` | Animate a mate | Motion study motors from data points (linear, cubic spline or Akima interpolation) or expressions (expressions not checked against official help) | Motions: waypoints, sweeps, functions of time | Same, through couplings |
| What the file keeps | Joints and formula text; the solver evaluates | Mates in the cloud model | Mates and motion study data | Pairs, couplings, states, motion definitions | The same plus curves |
| AP242 kinematics in STEP | No (OCCT maps none into a document) | None documented that I found | None documented that I found | No | No |

CATIA and NX are the systems that exchange AP242 kinematics, through the XML practice in section 1.
Their non-linear relations are cam couplers and loops, solved, like the three above.

Where cadgen would exceed them:
- The relation is data in the file, with its error bound. A reader evaluates it with no solver
  and no source.
- A relation no geometry expresses is one line: a tendon's curl law, a linkage you do not model,
  a cam's lift law before the cam exists.
- The same code gives the same samples. A solver's answer depends on its start and branch; on
  Onshape's forum, users report snail-cam followers that stick or flip.

Where cadgen falls short:
- No solver. The author writes the loop's or the cam's solution. Phase 2 checks it but does not
  derive it.
- No geometric cam. The lift law does not follow the cam's B-rep.
- `_tree` finds only the first loop of a mechanism today.

---

## 6. Plan, sizes and risks

### Phase 1: function couplings on tree mechanisms (7 to 8 days; the steps sum to 6.5 to 7.5)

| Step | Where | Days |
|---|---|---|
| `couple` takes callables; validation by name | `engine/mechanism.py` | 0.25 |
| The sampler takes the interpolation and a forced node at 0. Curve terms harvested and checked (f(0), arity, limits at nodes, settling). `Coupling` gains the nodes, curves and tolerance, and its `signature` them. | `engine/mechanism.py` | 1 |
| Memo key for a callable, by code reach | `engine/kernel/memo.py`, `engine/sources.py` | 0.5 |
| Evaluation: `_coupled`, `_coupling_json`, the `--part` share | `engine/mechanism.py`, `engine/export/tree.py` | 0.5 |
| Document write and read of the curve; the docstring's tree | `engine/mechanism.py` | 0.5 |
| Motions through function couplings resampled; configurations keep their authored coupling values | `engine/mechanism.py` | 1 |
| Page: curve shares, the segment-walk clamp, the inverse, the controls | `packages/core/src/lib/xbf/xbfKinematics.js` and its test | 1 to 1.5 |
| Position tooltip, one panel test | `packages/ui/src/renderers/xbf/` | 0.25 |
| Engine tests: slider-crank and cam fixtures; round trip; served; a `--pose` snapshot and a posed STEP equal the joints set by hand within the tolerance; a GLB clip of a crank sweep moves the slider along the curve; the refusals by name; a warm edit patches to the cold bytes; the same code writes the same bytes | `tests/python/packages/cadgen/engine/test_couplings.py` | 1 to 1.5 |
| Docs | `mechanism.py` docstring, engine README (Kinematics, Motions), `skills/cad/references/kinematics.md` | 0.5 |

### Phase 2: a loop closed by a locked function coupling (2 to 3 days, recommended)

- The harvest finds every loop, not only the first.
- Take a loop whose pairs a locked function coupling drives, all but the closing one. At every
  node, run FK through the tree, then `_extract` the closing pair's value from its two frames.
- That value joins the coupling's curve, so every pair's value is right.
- A node where the closing frames are not one motion of the pair apart fails the build by name.
  For example: "coupling 'crank' does not close the loop crank, rod, pin, slider at c = 135: the
  pin's frames are 2.3 mm apart".
- `block`, `resolve`, `_motions` and `xbfUnposable` then pose and move that loop through the
  coupling. Its pairs alone stay refused. `link_worlds` already skips a pair whose two links are
  placed.
- Why: without it, a sign or branch error in the author's formula poses a broken linkage at exit
  0, the output law 16 forbids. With it, the author keeps the closing joint and the engine checks
  the function.

### Not for now

- A geometric cam: an authoring call naming a profile edge and a follower; the engine solves
  contact on the B-rep for the samples and records the curve pair. 5 to 8 days.
- AP242 kinematics in STEP: a Part 21 writer for the mechanism outside OCCT, since OCP binds no
  `StepKinematics` class and OCCT has no document transfer. 2 to 3 weeks. Per the CAx-IF practice,
  the receivers would read motion as link paths, not as our sequences.
- A `period` for couplings, so a crank turns without limits, as the robot curve does. Half a day.

### Risks

| Risk | Effect | Mitigation |
|---|---|---|
| Motions resolve couplings in pair space | A crank sweep leaves the slider still | Resample (phase 1). A test sweeps a crank and checks the slider. |
| A callable in a memo key | Warm builds lose hits on and above the assembly | Key it by code reach. |
| A wrong formula or branch | A broken linkage at exit 0 | Phase 2. |
| Many inverses | A preset can show the coupling at another of its solutions | The solution nearest the slider's last value; 0 off the line, as today. |
| The sampling heuristic | A narrow feature slips between checks; a jump never settles and is refused | As motions. Sample to 0.9 of the tolerance if the bound must be strict. |
| A size-dependent tolerance | An unrelated edit resamples, and the mechanism subtree's bytes change | As function motions. Accepted. |
| Change versus value | An author returns the joint's value | f(0) ≠ 0 is refused, naming the as-built value. |
| Interop | No external reader consumes the curve | The AP242 shape buys fidelity and a future writer, not interop today. The docs say so. |

### Decisions for Jake

1. Linear rather than spline interpolation for curves. Recommended.
2. A callable gives the joint's change from as built, not its value. Recommended.
3. Phase 2 in scope. Recommended.

---

## Side findings

- **OCCT.** I found no `XCAFKinematics` or `XCAFDoc_KinematicTool` in OCCT master (GitHub code
  search) or in the 7.9.3 libraries cadgen ships (`libTKXCAF` holds no kinematics string). Since
  7.6.0, `StepKinematics` and `RWStepKinematics` parse pairs, values, ranges, mechanism and state
  entities into the STEP model. The entity tools also have `WriteStep` (checked on
  `RWStepKinematics_RWRevolutePair.pxx`, a "Read & Write tool"). Nothing maps them into or out of an
  XCAF document, and OCP 7.9.3.1.1 binds neither package. The docstring's "OCCT reads AP242
  kinematics but cannot write them" reads better as "OCCT parses them into its STEP model and maps
  none into a document; cadgen's OCP reaches neither".
- **The engine's gear pair.** cadgen reads `GEAR_PAIR` as "the second pair's value is `gear_ratio`
  times the first's". That is edition 4's `KinematicPairWithMotionCoupling` shape. Part 105's
  `GEAR_PAIR` is a pair between the two gears, with `actual_rotation_2 = -actual_rotation_1 ×
  gear_ratio`. Harmless while no model authors one.
- **Function motions.** They store spline samples under `cadgen_ease: spline`, from the same
  sampler. An AP242 reader would interpolate them linearly. On the three functions measured above,
  that linear reading missed by 2 to 4 times the tolerance. Two of the three eases have standard
  names: `linear` is `linear_interpolation_type`, and
  `cubic` reads as `synchronous_interpolation_type` (all pairs arrive together, rates proportional
  and varying). The motion writer could add AP242's `interpolation` attribute in a quarter day.
- **Docstring.** "AP242 has no virtual degree of freedom" stays true. It could name edition 4's
  coupler as the nearest standard form of a locked ratio coupling.

---

## Appendix: the measurement

A scratch script (session scratchpad, not kept) replicated `_sampled`: nine nodes over the range,
an interval halved while the interpolant misses the function by more than the tolerance at a
quarter, a half or three quarters, until none does. It compared linear and the motions' spline
(cubic Hermite, each node's slope its neighbours' secant). Tolerances: 0.0573° per angle and 0.25 mm
per distance (1e-3 of a 250 mm model). Functions, each over c in [0, 360]:

- Slider-crank, R 40 mm, L 120 mm: crank c; rod −asin(R sin c / L); slider
  R cos c + √(L² − R² sin² c) − (R + L).
- Four-bar (Freudenstein), crank 35, coupler 90, rocker 80, ground 100 (Grashof crank-rocker), as
  built at a 60° crank, one assembly branch: rocker angle by crank angle.
- Disc cam: cycloidal rise of 10 mm over 0 to 120°, dwell to 180°, cycloidal return to 300°,
  dwell.

The worst errors on a 400,001-point grid, as fractions of the tolerance, were: linear 0.97, 1.0012
and 0.90; spline 0.66, 0.98 and 0.84 (slider-crank, four-bar, cam).

---

## Sources

- ISO 10303-105:2019, SMRL text at STEP Tools: scope
  <https://downloads.steptools.com/stds/smrl/data/resource_docs/kinematics/sys/1_scope.htm>; clause 6
  (structure) `.../sys/6_schema.htm`; clause 8 (motion representation) `.../sys/8_schema.htm`; change
  annex `.../sys/g_change.htm`.
- AP242 AIM entity pages at <https://www.steptools.com/stds/stp_aim/html/>: `t_gear_pair`,
  `t_gear_pair_value`, `t_low_order_kinematic_pair_with_motion_coupling`, `t_screw_pair`,
  `t_screw_pair_value`, `t_rack_and_pinion_pair`, `t_rack_and_pinion_pair_value`,
  `t_linear_flexible_and_pinion_pair`, `t_point_on_planar_curve_pair`,
  `t_point_on_planar_curve_pair_value`, `t_planar_curve_pair`, `t_planar_curve_pair_range`,
  `t_sliding_curve_pair_value`, `t_rolling_curve_pair_value`, `t_actuated_kinematic_pair`,
  `t_actuated_direction`, `t_kinematic_topology_network_structure`, `t_mechanism_state_representation`,
  `t_interpolated_configuration_sequence`, `t_interpolated_configuration_segment`,
  `t_interpolated_configuration_representation`, `t_interpolation_type`,
  `t_geometric_representation_context_with_parameter`, `t_kinematic_path`,
  `t_kinematic_path_defined_by_nodes`, `t_path_node`, `t_link_motion_representation_along_path`,
  `t_prescribed_path`, `t_resulting_path`, `t_kinematic_control`, `t_kinematic_analysis_definition`,
  `t_kinematic_analysis_result`, `t_kinematic_result`, `t_revolute_pair_value`,
  `t_prismatic_pair_value`, `t_cylindrical_pair_value`.
- CAx-IF / JT-IF, *Recommended Practices for STEP AP242 Edition 4 Domain Model XML Kinematics*,
  release 1.3, 2025-10-20:
  <https://www.mbx-if.org/home/wp-content/uploads/2025/10/rec_prac_ap242xml_kinematics_v1.3.pdf>
  (sections 1.1.5, 2, 4.3, 4.3.2 to 4.3.4, 4.4, 5).
- OCCT: 7.6.0 release notes, <https://www.opencascade.com/sites/default/files/pdf/release_notes_7.6.0.pdf>;
  `src/DataExchange/TKDESTEP/RWStepKinematics/RWStepKinematics_RWRevolutePair.pxx` on master;
  GitHub code search for `XCAFDoc_KinematicTool`, `XCAFKinematics` and the users of
  `StepKinematics_MechanismRepresentation`; local checks of `libTKDESTEP.7.9.3.dylib` and
  `libTKXCAF.7.9.3.dylib` in the main checkout's `.venv`, and of OCP's modules.
- FreeCAD: `src/Mod/Assembly/JointObject.py` and `CommandCreateSimulation.py` at tag `1.1.4`
  (the simulation file is absent at `1.0.0`).
- Onshape: relations help, <https://cad.onshape.com/help/Content/Assembly/relations.htm>; the
  tangent-mate tech tip, <https://www.onshape.com/en/resource-center/tech-tips/advanced-mating-pin-slot-tangent-mate>;
  forum threads on cam followers.
- SolidWorks: Mechanical Mates (2023),
  <https://help.solidworks.com/2023/english/SolidWorks/sldworks/c_Mechanical_Mates.htm>;
  Linear/Linear Coupler, <https://help.solidworks.com/2021/English/SolidWorks/Sldworks/t_mates_linear-linear_coupler.htm>;
  Interpolation Method Comparison,
  <https://help.solidworks.com/2018/English/SolidWorks/motionstudies/c_interpolation_method_comparison.htm>.
- The earlier note in this folder, `xbf-joints-motion-design-2026-10-20.md`, reached the same OCCT
  and CAx-IF conclusions independently.
