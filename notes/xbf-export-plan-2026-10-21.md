# xbf engine: build / export / snapshot plan (agreed 2026-10-21)

Status: AGREED with Jake in the xbf engine session; not started. Sequenced after the cold-cache merge and
the implicit-bodies work. Implementation is from scratch inside `packages/cadgen/src/cadgen/engine/`
(a new `export` package); the current `exports.py` and the four export decorator entries are deleted,
not refactored. Opus/high, one agent.

## Principle

The xbf is the model's result: the only lossless form (geometry, assembly, names on every element,
colours, materials, joints, configurations, per-body keys for the cold cache), the document the viewer
serves and the next build patches. STEP, GLB, STL and 3MF are *exports of a document*: derived, lossy,
produced on demand or declared as derived files. FreeCAD's model: the `.FCStd` is the document;
"Export" is an action on it, never part of recompute.

One writer, one document, two triggers: a derived file is the same `translate(...)` call whether a
decorator declares it or a verb asks for it. Same document, same options -> same bytes.

## Export matrix (push-back accepted)

- Sources: documents only -- `.xbf`, `.step` (foreign STEPs are read into a document first), or a
  `script.py::model` / code string (built first, in the worker).
- Destinations: `.xbf`, `.step`, `.glb`, `.stl`, `.3mf`.
- Meshes are NEVER sources (no B-rep): no mesh->STEP, no mesh->xbf, no mesh->mesh.
- xbf->xbf only with `--part` (extract a sub-assembly into its own document); a posed xbf is refused
  (a pose is a request, never a document). STEP->xbf is supported and useful.

## Commands (`cadgen xbf <verb>` during the isolation round; `cadgen <verb>` at migration)

### build
```
cadgen build SCRIPT[::MODEL] ...                 every model of the script, or one
cadgen build --code 'SOURCE' | -  [--name N]     one model from source text (stdin with -)
  [--xbf [F]] [--step [F]] [--glb [F]] [--stl [F]] [--threemf [F]] [--snapshot [F]]
  [--pose NAME|JSON] [--part NAME] [--json] [--no-daemon]
```
- Runs in the script's warm worker (code mode: the worker keyed by `--name`, default `model`).
- Writes the xbf, wakes the viewer (long-poll version advances) BEFORE any derived file is written,
  then writes derived files; returns when every file exists (skills rely on that).
- Unchanged document -> files kept, not rewritten.
- Output flags are ADDITIVE and select-and-override on a script: no flags -> what the decorators
  declare; `--xbf` alone -> only the xbf; `--xbf --step out.step --glb` -> xbf + STEP at that path +
  GLB at its declared/default path. Flag name is `--xbf`, not `--xbf-only`.
- Code mode: no decorators; the code assigns its final shape to `result` (required; the error names
  it); flags are the only declarations and each takes a path; `--xbf F` gives it the cold cache like
  any model; element names come from the code's assignment targets with `name` as the function part;
  provenance (`where`, `made_by`) is live-only since the code is not on disk.
- `--pose`/`--part` apply to every export output that takes them. `--animate` is NOT on build (edge
  case; use `export` or Python).
- Report per model: bodies run / replayed / loaded, memo misses, timings, files written or kept;
  `--json` the full report.
- `python model.py [same flags]` == `cadgen build model.py ...`: running a model script directly asks
  the daemon to build it (the script's own process never builds). A script's argv is the build's;
  a model script with its own command line uses `cadgen build` instead.

### export
```
cadgen export SRC DST [DST ...] [--pose NAME|JSON] [--part NAME]
                        [--animate SPEC --duration S [--loop|--pingpong] [--ease E]]   (GLB only)
```
- Format from each DST suffix; several in one call read once, write many.
- From the warm worker when it holds the document; else read (xbf fast, STEP slow).
- `--animate` short grammar only: `folded,reach,folded` (waypoints between named configurations,
  equally spaced over `--duration`) or `elbow=0..90,wrist=-30..30` (joint sweeps). Anything richer
  is Python.

### snapshot
```
cadgen snapshot SRC OUT.png [--display MODE|JSON] [--camera PRESET|az:el] [--grid] [--preview]
  [--pose NAME|JSON] [--width N] [--height N] [--size-profile P] [--json]
  SRC: .xbf | .step | .glb | .stl | .3mf
```
- Documents: the engine meshes with the viewer's mesher and cache (nothing new meshed after a build),
  serves over HTTP; headless Chromium (Playwright, software GL) renders the engine's page with the
  same `packages/core` scene the viewer slice draws with. A snapshot is the viewer's frame.
- Mesh files: no Python meshing; the render page loads them with the shared mesh renderers from
  `packages/core` (to be added to the engine's render page in this work -- not a migration dependency).
- `--pose` for documents only.

### serve DOC [--port N] / where DOC REF / status / stop / daemon / viewer FILE / mcp -- unchanged.

## Decorators

- `@xbf(out=None)` -- this function is a model; `out` defaults to `XBF/<name>.xbf` beside the script.
  The ONLY argument.
- Derived-file decorators take `out` plus exactly the options their verb takes (they ARE the verb run
  after the build): `@step(out, pose=None, part=None)`, `@glb(out, pose=None, part=None, animate=None)`,
  `@stl(out, pose=None, part=None)`, `@threemf(out, pose=None, part=None)`,
  `@snapshot(out, display=..., camera=..., grid=..., preview=..., pose=..., width=..., height=...)`
  (optional, same mechanism).
- No material, params, children, kinematics or animation arguments anywhere: those are written in
  the body (`part.material`, `paint`, build123d joints, `configuration(...)`).
- A nested decorated function called from a parent is a shape in the parent's tree; its `out` is
  honoured only when it is built as a target.

## Functions (`import cadgen`) -- one per command, the CLI a thin argparse over it

| command  | function                                                                                           |
|----------|----------------------------------------------------------------------------------------------------|
| build    | `build(target=None, *, code=None, name=None, xbf=None, step=None, glb=None, stl=None, threemf=None, snapshot=None, pose=None, part=None) -> Report / Built` |
| export   | `export(source, *destinations, pose=None, part=None, animate=None) -> list[Written]`                |
| snapshot | `snapshot(source, out, *, display=None, camera=None, grid=False, preview=False, pose=None, width=None, height=None) -> Drawn` |
| serve    | `serve(source, port=0) -> Served` (`.url`)                                                          |
| where    | `where(source, ref) -> Facts`                                                                       |
| status/stop/daemon | `status()`, `stop()`, `daemon()`                                                         |
| viewer   | `viewer(path) -> Served`                                                                            |

`@xbf` returns a `Model` with `.build()`, `.export(*destinations, pose=None, part=None, animate=None)`,
`.snapshot(out, **options)`, `.document` (the built document, read-only).
Code mode returns `Built` with `.shape` (the build123d object), `.document`, `.files`, `.export(...)`.

Internals, one per concern, all in the engine: `engine.build.build(model, ...)`,
`engine.export.translate(document, kind, path, pose=None, part=None, animate=None)`,
`engine.snapshot.snapshot(document|mesh, out, options)`, `engine.document.read/save/patch`,
`engine.client` (the daemon RPC every public function goes through).

## The export package

- `export/__init__.py` -- `translate(...)` and the kind registry.
- `export/step.py` -- AP242 via OCCT's STEPCAF writer: assembly with instance and product names,
  colours, material names and densities, element names on faces and edges from the document's labels.
  Fidelity proven by reading our STEP back through the engine's reader (names, colours, materials,
  instance tree). Joints, configurations, provenance never (OCCT has no writer).
- `export/mesh.py` -- placements (prototype x instance location) and prototype meshes from ONE mesh
  cache, the viewer's (today's exporter keeps a private second cache: delete it).
- `export/glb.py` -- one mesh per prototype, a node per placement with its name, a PBR material per
  part material (metallic, roughness, emissive, alpha; base colour from the part; paints as separate
  primitives). Animation: see below.
- `export/threemf.py` -- object per prototype, build item per placement, colours/materials via the
  3MF materials extension.
- `export/stl.py` -- binary, every placed triangle.
- Files are written to a temp beside the target and moved over it.

## GLB animation (motion description, not keyframes)

- Nothing is written but the GLB; no keyframe file ever. The xbf never holds time.
- `animate` is a motion description in one of three forms: waypoints `[(0, "folded"), (2, "reach")]`
  (configuration names or joint dicts, eased between); a sweep `{"elbow": (0, 90)}` with `duration`,
  `loop`/`pingpong`; a callable `t -> pose`.
- Keyframes are to motion what triangles are to surfaces: the exporter runs forward kinematics
  through the mechanism (couplings included) at adaptively chosen times until interpolated node
  transforms stay within a tolerance of the true FK, and writes CUBICSPLINE channels per placed
  instance -- few keys where motion is smooth, dense where it isn't. Standard glTF.
- Same idea as the #568 animation collapse (keys, no JS), new code on the engine's mechanism; the
  old clip DSL does not come over.
- Symmetric forms: `@glb(out, animate=...)` == `export(out, animate=...)` == `cadgen export --animate`
  (short grammar). Not on `cadgen build`.

## Push-backs recorded

1. Meshes never sources; xbf->xbf only with `--part`; posed xbf refused.
2. Code mode: `out` required per output flag, exactly one model, worker keyed by `--name`; code lives
   in the transcript so `where`/`made_by` are live-only.
3. Snapshot of mesh files is part of this work (not deferred to migration).

## Tests (cheapest level each)

STEP round trip through our reader (names on faces and edges, colours, materials, instance tree);
GLB parsed from its JSON chunk (nodes, meshes, materials, a paint as its own primitive); 3MF unzipped
and parsed; STL triangle count equals the placed meshes; kept-not-rewritten on an unchanged document;
`--xbf` select-and-override; standalone derived decorator writes one file and nothing else; export
from a script builds then writes; `--pose` moves a placement; `--part` extracts a subtree (to xbf and
to STEP); the viewer's version advances before a derived file is written (a deliberately slow fake
writer); code mode (`result` required, names, warm second build, cold cache via `--xbf`); `python
model.py` triggers the daemon; animation: a sweep produces channels whose sampled transforms match FK
within tolerance; snapshot of a GLB/STL/3MF; policy suite's nothing-to-disk and standalone rules.

## Sequencing

cold-cache merge -> implicit bodies (core retention; benchmarked) -> benchmark batch -> this export
package (additive; cannot move the numbers). The export brief must not assume "a body is a decorated
function" anywhere.

## Amendment 2026-10-22 (Jake): pose and motion live in the code, decorators carry only `out`

- `@xbf(out)`, `@step(out)`, `@glb(out)`, `@stl(out)`, `@threemf(out)`: no other argument. `pose=`/`part=`
  are REMOVED from the decorators and from `build`; they stay on the verbs only (`export SRC DST --pose --part`,
  `snapshot SRC OUT --pose`), which are ad-hoc and never cached.
- The as-built pose is whatever the model's joints produce (`joint.connect_to(child, angle=...)` places the
  child): the model returns the assembly as built. `configuration(...)` only NAMES presets for `--pose`, the
  Position panel and motions; it never changes the build. A model that wants a second pose exported on every
  build is a second model function with different joint angles.
- `@snapshot` is REMOVED (Jake, 2026-10-22): snapshots are the verb (`cadgen xbf snapshot SRC OUT.png
  [view options]`) and the function (`cadgen.snapshot(source, out, **options)`, `Model.snapshot(...)`) only.
  No derived-file decorator carries any option; `build` has no `--snapshot`. Open decision 6 is closed by
  construction: nothing a decorator says can go stale.
- Motions are definitions in the code, beside configurations, stored in the document as data (never baked
  frames); the GLB export bakes every motion as a glTF clip of the same name; STEP/STL/3MF ignore them;
  no `animate=` kwarg anywhere, no `--animate` flag.

```python
from cadgen.engine import xbf, glb, configuration, motion

@glb("out/tom.glb")
@xbf
def tom():
    arm = build_arm()                                   # build123d joints inside
    configuration(arm, "folded", shoulder=0, elbow=0)
    configuration(arm, "reach", shoulder=45, elbow=90)
    motion(arm, "cycle", [(0, "folded"), (2, "reach"), (5, "folded")])        # waypoints, eased
    motion(arm, "wave", sweep={"wrist": (-30, 30)}, duration=2, loop="pingpong")
    motion(arm, "nod", lambda t: {"elbow": 45 + 20 * math.sin(t)}, duration=6)  # a function of time
    return arm                                           # as built: the pose the joints' connect_to produced
```

`motion(assembly, name, waypoints_or_fn=None, *, sweep=None, duration=None, ease="cubic", loop=None)`:
one of waypoints (time → configuration name or joint dict), a sweep (joint → (from, to)), or a callable
t → joint dict; `duration` in seconds (waypoints imply it); `loop` None | "loop" | "pingpong". The
export runs forward kinematics through the mechanism (couplings included) at adaptively chosen times until
the interpolated node transforms stay within a tolerance of the true FK, and writes CUBICSPLINE channels per
placed instance. Viewer: the GLB renderer gets an animation bar (clip picker, play/pause, scrub, loop,
speed); the xbf viewer's Position panel can preview a named motion.
Law wording: "the document holds poses and motion definitions, never baked frames or clips."
