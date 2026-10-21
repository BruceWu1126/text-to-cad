# models-xbf

The staging corpus for cadgen's xbf engine (`cadgen.engine`): copies of models
from `models/`, ported to the engine, so it is exercised on real models and the
migration of `models/` is rehearsed. When everything migrates, these fold back
into `models/` and this folder goes away. `models/` itself is untouched.

Each model keeps the layout it had in `models/`: its sources in `src/`, and
everything a build writes in `XBF/` beside them (the `.xbf` document, its
`.xbf.json` sidecar). A model declares its xbf and no export: STEP, GLB, STL and 3MF are
translations of the xbf, written on demand by `cadgen xbf export`. `XBF/` is
gitignored; nothing else is written anywhere. `tom` also reads vendor STEP from a local,
gitignored `tom/STEP/imported/` (its `src/README.md` says how to populate it) and builds from
`tom/` with `src` on `PYTHONPATH`.

## Build and snapshot

From a model's `src/` folder, with the engine's environment (OCP, build123d,
numpy and cadgen on the path):

```
python -m cadgen.engine build SCRIPT.py::FUNCTION     # or SCRIPT.py: every model in it
python -m cadgen.engine snapshot ../XBF/NAME.xbf OUT.png
```

`build` runs in the script's warm worker (the daemon starts on demand; `python
-m cadgen.engine stop` stops it), so a second build of an unchanged model
replays it from memory. `snapshot` needs cadgen's built browser runtime
(`scripts/bundle/bundle.sh`, or `CADGEN_BROWSER_RUNTIME_DIR` pointing at one).
`python -m cadgen.engine serve ../XBF/NAME.xbf` shows a
document in the browser.

## The port

The migration a user makes, and nothing more:

- `from cadgen import step` -> `from cadgen.engine import xbf, ...`;
  cadgen's build123d proxy -> `import build123d as bd`.
- `@step(out="../STEP/x.step", ...)` -> `@xbf(out="../XBF/x.xbf")`, and nothing
  else: the original's `@step`/`@stl`/`@threemf` are dropped (`cadgen xbf export`
  writes any of them from the xbf). A STEP another model reads is an input,
  committed beside its source: `examples/STEP/` holds the two that
  `electronics_enclosure_base_import.py` reads. Decorators take `out=` and nothing else.
- `materials=` finishes -> `Material` values set on the parts
  (`part.material = ...`) and `paint(part, faces, material)` for a face finish.
- Mates (`AssemblyHelper` frames, `KINEMATICS` revolute/fastened) -> build123d
  joints (`RigidJoint`, `RevoluteJoint`) placed with `connect_to`, which the
  engine harvests into the document's mechanism. The as-built pose is where the
  joints connect the parts. Couplings have no build123d equivalent and are
  dropped; poses are `configuration(...)` in the body.
- Animation (`animation=`, `cadgen.clip`) -> `motion(...)` in the body: a
  definition the xbf keeps (never frames), which a GLB export bakes into a clip.
  The motorbike carries one (`steer`); the others' clips are not ported.
- A model is one output file: `@xbf` marks the dfm trays, the examples, the
  moonwatch `case` and `movement`, `qdd_actuator`, `motorbike` and `tom`, and
  nothing else. What a model is built from -- an assembly's parts, a derivation's
  stages, a group of parts -- is a `@memo` function: called in the model's body it
  is a sub-assembly of that build, it writes no file, and a later build whose edit
  does not reach it replays it instead of running it. `@memo` sits where an edit
  elsewhere leaves the function unchanged and its work is worth skipping, on
  functions that return an assigned result (a `@memo` function names what its
  `return` statement makes in itself, so one returning `a - b` would rename those
  elements). Helpers called many times with varying arguments stay plain: the
  kernel memo covers their calls. `cadgen xbf build --verify` checks every
  replayed call against a fresh run.

## The models

| Model | Source | What it exercises |
| --- | --- | --- |
| `dfm-process-comparison` | 5 single-part trays | per-part catalogue materials (PLA, aluminium, ABS); lofts, fillets, drafted cones |
| `qdd_actuator` | one file, 153 occurrences | a 102-part assembly, modules as nested compounds; 7 `@memo` makers (stator, rotor, bearings, sensor, boards, screws) replayed while an edit elsewhere runs the root; 54 build123d joints (rigid mounts, revolute drive train with ranges) |
| `motorbike` | 21 `@memo` parts + one model | parts as sub-assemblies of one xbf, a part placed twice; revolute and rigid joints across parts; one motion, `steer` (`cadgen xbf export ../XBF/motorbike.xbf bike.glb` carries it as a clip) |
| `moonwatch` | `case.py`, `movement.py` (3 `@memo` layers, 19 `@memo` part groups) | finishes as materials on 220 parts (29 case, 191 movement): catalogue names and densities with finish looks, own materials (sapphire, ruby, NBR, print ink), a painted face finish, transparent parts |
| `tom` | tom-cad `main` (7f79c52): 119 files, one model, 6 `@memo` stages and parts | a 619-part arm built from stages and 14 `read_step` vendor parts (536 MB of local, uncommitted inputs: see its `src/README.md`); mates kept as data and authored as build123d joints; materials by finish; many-body retention |
| `examples` | `l_bracket`, `basic_shape_mating_test_fixture`, `electronics_enclosure_base`, `electronics_enclosure_base_import` | fillets; an assembly placed by joints (`connect_to` moving parts); `read_step` of a part and of an assembly (imported naming), from STEP inputs committed in `examples/STEP/` |

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

Since 2026-10-21 the nested models are `@memo` functions: the same bodies (plus the
movement's 19 part groups and qdd's 7 makers), the same element names in every
document, one xbf per model. `--verify` finds one function whose fresh run differs from
its logged one: tom's `tom2_symmetric_yokes` (47 misses on a second run, an
intermediate yoke's geometry; the arm tom writes is unchanged).

Known engine gaps these surface: a shape that carries a build123d joint makes
the compound holding it unkeyable, so a body that authors joints is never
retained; a `bd.Text` sketch fused with another sketch runs uncached; a colour
written to STEP and read back with `read_step` comes back sRGB-encoded.
