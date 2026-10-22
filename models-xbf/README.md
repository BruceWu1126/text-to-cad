# models-xbf

The staging corpus for cadgen's xbf engine (`cadgen.engine`): copies of models
from `models/`, ported to the engine, so it is exercised on real models and the
migration of `models/` is rehearsed. When everything migrates, these fold back
into `models/` and this folder goes away. `models/` itself is untouched.

Each model keeps the layout it had in `models/`: its sources in `src/`, and
everything a build writes in `XBF/` beside them (the `.xbf` documents). A model
declares its xbf, and an export only where its project needs the file on every
build: the planetary gear (`assemblies`), `hypercar`, `f1` and `w16` declare the GLB that carries
their motions as clips. Otherwise STEP, GLB, STL and 3MF are translations of the xbf,
written on demand by `cadgen export`. A drawing
declares its DXF alone, in `DXF/`. `XBF/` and `DXF/` are gitignored; nothing else
is written anywhere. `tom` also reads vendor STEP from a local,
gitignored `tom/STEP/imported/` (its `src/README.md` says how to populate it) and builds from
`tom/` with `src` on `PYTHONPATH`.

## Build and snapshot

From a model's `src/` folder, with the engine's environment (OCP, build123d,
numpy and cadgen on the path):

```
python SCRIPT.py                                      # every model in it, as its decorators declare
python -m cadgen.engine snapshot ../XBF/NAME.xbf OUT.png
```

A model script is Python and takes no options (the engine's law 11): running it asks
the engine's daemon to build every model it defines, each writing what its decorators
declare, and the script's own process builds nothing. The build runs in the script's
warm worker (the daemon starts on demand; `python -m cadgen.engine stop` stops it), so
a second build of an unchanged model replays it from memory. `snapshot` draws with OCCT
(on Linux with no screen, under Xvfb). `python -m cadgen.engine serve ../XBF/NAME.xbf` shows a
document in the browser. Nothing is written beside a model but what its decorators declare:
no sidecar, no cache.

## The port

The migration a user makes, and nothing more:

- `from cadgen import step` -> `from cadgen.engine import xbf, ...`;
  cadgen's build123d proxy -> `import build123d as bd`.
- `@step(out="../STEP/x.step", ...)` -> `@xbf(out="../XBF/x.xbf")`, and nothing
  else: the original's `@step`/`@stl`/`@threemf` are dropped (`cadgen export`
  writes any of them from the xbf), unless the project reads the file: juno's
  link models declare the 3MF its URDF references, and the planetary gear the
  GLB whose clip the docs hero plays. A STEP another model reads is an input,
  committed beside its source: `examples/STEP/` holds the two that
  `electronics_enclosure_base_import.py` reads. Decorators take `out=` and nothing else.
- `materials=` finishes -> `Material` values set on the parts
  (`part.material = ...`) and `paint(part, faces, material)` for a face finish.
- Mates (`AssemblyHelper` frames, `KINEMATICS` revolute/fastened) -> build123d
  joints (`RigidJoint`, `RevoluteJoint`) placed with `connect_to`, which the
  engine harvests into the document's mechanism. The as-built pose is where the
  joints connect the parts. Couplings are `couple(...)` in the body (one control
  driving several joints by ratio: the qdd's `drive`, locked, as a gear train is);
  poses are `configuration(...)` in the body.
- Animation (`animation=`, `cadgen.clip`) -> `motion(...)` in the body: a
  definition the xbf keeps (never frames), which a GLB export bakes into a clip.
  The motorbike carries one (`steer`), the hypercar one (`doors`) and the f1 two
  (`drs`, `steering`: functions of time solving closed loops), each baked into a
  declared `@glb`; clips that move parts no joint moves (explodes, tours) are not
  ported.
- A model is one output file: `@xbf` marks the dfm trays, the examples, the
  moonwatch `case` and `movement`, `qdd_actuator`, `motorbike`, `tom`, and
  `hypercar`, `f1` and `w16` (each with its `@glb`), and `@dxf` the eight drawings, and
  nothing else. A big piece of a model -- a layer of an assembly, a stage of a
  derivation -- can be a `@memo` function: called in the model's body it is a
  sub-assembly of that build, it writes no file, and a later build whose edit
  does not reach it replays it instead of running it. A verifying build
  (`MODEL.build(verify=True)` from Python) checks every replayed call against a fresh run.
- `@dxf` stays `@dxf` (`from cadgen.engine import dxf`): its `{layer: shape}` dict
  becomes a compound of labelled children, a part per layer.

### When to use `@memo`

Rarely. The kernel memo already caches every kernel call in any model, markers or
not, so `@memo` only saves the Python cost of the memo hits a replayed function
avoids (0.1-0.4 ms per hit). It pays only where a function holds seconds of kernel
work that an edit elsewhere leaves untouched: roughly a thousand kernel calls or
more. Measured here: the movement's 3 layers halve an edit (a leaf edit runs in
about 4.5 s), and tom's 6 stages save 1.3-3 s per edit. It does not pay on small
pieces: the motorbike's 21 part markers and qdd's 7 makers saved under 0.1 s, and
the movement's 19 part groups added nothing beyond its 3 layers, so those models
carry none of them. A `@memo` function must also return an assigned result (it
names what its `return` statement makes in itself, so one returning `a - b` would
rename those elements), and a marker costs a model body that is no longer one
piece. Nested `@xbf` is never needed for caching.

## The models

| Model | Source | What it exercises |
| --- | --- | --- |
| `dfm-process-comparison` | 5 single-part trays | per-part catalogue materials (PLA, aluminium, ABS); lofts, fillets, drafted cones |
| `qdd_actuator` | one file, 153 occurrences | a 102-part assembly, modules as nested compounds; one body (no `@memo`: its makers save under 0.1 s); 54 build123d joints (rigid mounts, revolute drive train with ranges); the drive train a locked coupling, `drive`, with two configurations |
| `motorbike` | 21 part files + one model, one body | parts as sub-assemblies of one xbf, a part placed twice; revolute and rigid joints across parts; one motion, `steer` (`cadgen export ../XBF/motorbike.xbf bike.glb` carries it as a clip) |
| `moonwatch` | `case.py`, `movement.py` (3 `@memo` layers) | finishes as materials on all 220 parts (29 case, 191 movement): catalogue entries in each finish's look (coat, grain, transmission), own materials (sapphire, ruby, print ink), a painted face finish, transparent parts |
| `tom` | tom-cad `main` (7f79c52): 119 files, one model, 6 `@memo` stages and parts (each earns it) | a 619-part arm built from stages, 8 occurrences of Tom rev3's arm extracted once to xbf (`read_xbf`, never the 354 MB STEP) and 11 `read_step` vendor files (139 MB of local, uncommitted inputs: see its `src/README.md`); mates kept as data and authored as build123d joints; materials by finish; many-body retention |
| `examples` | `l_bracket`, `basic_shape_mating_test_fixture`, `electronics_enclosure_base`, `electronics_enclosure_base_import`, `electronics_enclosure_base_drawing` | fillets; an assembly placed by joints (`connect_to` moving parts); `read_step` of a part and of an assembly (imported naming), from STEP inputs committed in `examples/STEP/`; an `@eng_drawing` PDF of the enclosure read from its xbf (`read_xbf`), not a STEP |
| `hypercar` | 13 system files (`@memo` stages, 1,250-24,000 kernel calls each) + one model | a 706-part car whose panels share one master surface; a placed wheel prototype per axle; doors as cylindrical joints on the chassis paired by label across systems' groups, riders fastened by rigid-joint pairs; a coupling (`doors`, turn and travel), a configuration (`open`) and a motion baked into the declared `@glb` |
| `f1` | 32 part files, `stages.py` and one model; 12 `@memo` stages (990-11,400 kernel calls each) | an 833-part car of 28 children grouped in stages by the builders they share; two closed-loop mechanisms (the DRS four-bar, the steering) authored as their tree of joints -- revolute, prismatic and ball -- paired by label, every pose solved in Python: 3 configurations and 2 motions of time baked into the declared `@glb` |
| `drawings` | all eight scripts of `models/drawings` | `@dxf` drawings of planar documents: a layer per part (`CUT`, open `BEND` and `ENGRAVE` lines, a layer of several shapes one part of them all), bend fixtures in every orientation, `bd.Text` outlines engraved, a workshop drawing of three views and eleven dimensions, and a profile laid out from a solid's top face; byte-for-byte the old emitter's for the gasket |
| `juno` | one script: the robot and its 28 URDF links, 29 models | 27 revolute joints connected at the athletic stance (absolute angles, the URDF's travel as ranges) and the SRDF's five group states as configurations; each link a model writing the 3MF `juno.urdf` references; one `@memo` stage, `links()`, shared by all 29 (30.8 s cold against 56.2 s without it) |
| `assemblies` | 23 models in 21 groups | the demo assemblies: `AssemblyHelper` frames as build123d joints, the planetary gear's locked `drive` coupling and `meshCycle` motion (its GLB, the docs hero), the mars rover's 27 joints, 7 couplings and 4 configurations; no `@memo`, no model calling another |
| `tendon_hand` | 56 of the 112 one-model scripts, folded into 5 scripts by subsystem | the research hand's components, phalanges, routing, guide mounts and forearm, each model its own xbf; four shared `@memo` stages (each finger's guide combs, the forearm frame, its guide banks, the R5 supports) placed by several models; a STEP round trip made in memory; `anatomy_layout`'s finger joints with a curl coupling per finger and two configurations; 56 not ported (frozen checkpoints, generated validation output; listed in its `src/README.md`) |
| `radial` | 19 scripts -> one model, 18 `@memo` system stages | a sectioned nine-cylinder radial, 473 parts in 2,400 placements: finishes as catalogue materials on every part; the old animation's label contract made 122 joints (revolute and prismatic, fastened links), the gear train a locked coupling (`drive`), two configurations, and `running`, a function-of-time motion that a GLB export carries as a clip |
| `lyra` | one script: the hand and its 17 URDF links, 18 models | 16 revolute joints connected at the relaxed pose (absolute angles, the URDF's travel as ranges), the SRDF's seven group states as configurations and the five clips as motions of time (joint-space already); each link a model writing the 3MF `lyra.urdf` references; one `@memo` stage, `links()`, shared by all 18 (7.7 s cold against 11.1 s without it) |
| `falcon_heavy` | 3 scripts -> one script, 3 models | the full stack, a 270° cutaway and an exploded view of one vehicle library: 85 parts in 2,142 placements, 27 engines as `moved` placements of one Merlin; two `@memo` stages, `engine_prototype()` (one Merlin per build, not nine) and `build_vehicle(cutaway)` (the exploded view's vehicle a placement of the stack's): 7.1 s cold with none, 5.0 s with both |
| `branding` | `logo_c`, `logo_cad`, `logo_texttocad` | plain models with the STEP each delivers (`@xbf` + `@step`), extruded from a JSON input the body reads (`profiles.json`, a change to which rebuilds them); colours by `srgb` |
| `tests` | the three manual cases `models/tests/README.md` lists (`joint_playground.urdf`, no model and named nowhere, not ported) | manual-validation fixtures, never CI input: the Render floor, a 32-part rebuild during a load, and materials (a catalogue steel with `color=None`, a paint with its colour), a revolute and a prismatic joint, a configuration and a motion on 11 parts |
| `w16` | 13 system files (`@memo` stages, 10-281 s cold each) + one model | a 2,575-occurrence (794-part) quad-turbo W16 whose museum cutaway is its own geometry; finishes as catalogue materials by colour (`color=None`); OCCT's fuzzy booleans run through build123d's, so the memo keys them; the crank train as 174 joints paired by label (prismatic pistons and valves; revolute crank, rods, cams, followers, pump, pulleys, rotors) and 618 rigid fastenings, the slider-crank and valve loops solved in Python: the `crank` motion, one 720-degree cycle, baked into the declared `@glb` |

Each model's `src/README.md` says what was ported and what was dropped.

## First builds (2026-10-20, one machine, daemon cold then warm)

| Document | Parts / instances | Cold build | Warm no-op |
| --- | --- | --- | --- |
| dfm trays (each) | 1 / 1 | 2.4 s | replayed |
| qdd_actuator | 102 / 153 | 5.6 s | body re-runs, 0 misses (joints, below) |
| motorbike | 42 / 46 | 9.3 s | 21 children replayed, root re-runs (joints) |
| moonwatch case | 29 / 29 | 22.7 s | body re-runs, files rewritten (text sketch, below) |
| moonwatch movement | 191 / 191 | 233 s (movement_base 135 s, chrono_works 77 s) | replayed |
| examples (each) | 1-11 | 2.5 s | replayed; the fixture's root re-runs (joints) |
| tom | 619 / 628 | 347 s (worker ~12 GB after) | 2.9 s, all 7 bodies replayed |

Since 2026-10-21 tom's stages and the movement's layers are `@memo` functions; the
motorbike's parts and qdd's makers are plain functions in one body. The same element
names in every document, one xbf per model. A verifying build (`tom.build(verify=True)`) finds one
function whose fresh run differs from its logged one: tom's `tom2_symmetric_yokes` (47 misses on a second run, an
intermediate yoke's geometry; the arm tom writes is unchanged).

Since 2026-10-21 tom reads the parts of Tom rev3 it uses as xbf extracted from the
recovered `tom.step` (`cadgen export ... --part`), not the whole file: cold 165 s ->
107 s, the worker 2.1 -> 1.9 GB after it; edits, no-op and restart unchanged (9 s, 1 s, 6 s).

Known engine gaps these surface: a shape that carries a build123d joint makes
the compound holding it unkeyable, so a body that authors joints is never
retained; a `bd.Text` sketch fused with another sketch runs uncached; and a colour
written to STEP and read back with `read_step` comes back sRGB-encoded.
