# cadgen.engine

A model is a parameterless function that returns a build123d shape, decorated
with `@xbf`. The engine runs it in a warm worker, saves the result as an XCAF
document (`.xbf`, BinXCAF), and writes the derived files the model declares,
each a translation of that document.

```python
import build123d as bd
from cadgen.engine import xbf, step

@xbf                      # XBF/arm.xbf beside the script; @xbf(out="path.xbf") to choose
@step("arm.step")         # also @glb, @stl, @threemf; stacked or alone; out= is every decorator's only argument
def arm():
    return bd.Box(10, 20, 30)
```

A function worth skipping on a later build is marked `@memo` (**Memo functions**): an
assembly's parts, a stage of a long derivation. It writes no file; the model that calls
it does.

```python
from cadgen.engine import memo, xbf

@memo
def wheel(radius, spokes=5):     # replayed, not run, while what it is made of is unchanged
    ...

@xbf
def bike():
    return bd.Compound(children=[wheel(300), wheel(300).moved(bd.Pos(1200, 0, 0))], label="bike")
```

A part's material is set on the shape, as its colour is: `part.material =
materials.aluminium_6061` (or `materials("6061")`: a catalogue of common engineering
materials, **Materials**), or a `Material(...)` constant of the project's own for any other.
`paint(part, faces, material)` gives faces one. On an assembly, a material is every part's
below it that has none: a whole-model default. Set materials on finished parts, after the
last operation: an edit of one is then no kernel call. Two limits of OCCT's STEP writer show in a STEP export: it names the density
unit gram³·cm² (the exponents wrong, the number right: the reader gives the density back as
written), and it writes materials sharing a name as one entity, so a STEP gives every such
material the first one's description.

An assembly's mechanism is build123d's joints and nothing else: `RevoluteJoint`,
`LinearJoint`, `CylindricalJoint`, `BallJoint` and `RigidJoint` on its parts, placed with
`connect_to(..., angle=, position=, angles=)`. The values they are connected at are the
as-built configuration, the pose every file of the model shows; `configuration(assembly, name,
**values)` names another, by joint label, and never moves the build; `motion(assembly, name,
...)` names a motion (**Motions**). Connect parts before putting them in the compound that holds
them; where `connect_to` cannot place them (a nested assembly), give the two joints one label
where the parts stand.

```python
from cadgen.engine import configuration, xbf

@xbf
def hinge():
    base, arm = bd.Box(100, 60, 20), bd.Box(120, 20, 10, align=(bd.Align.MIN, bd.Align.CENTER, bd.Align.MIN))
    base.label, arm.label = "base", "arm"
    bd.RevoluteJoint("elbow", base, axis=bd.Axis((40, 0, 20), (0, 1, 0)), angular_range=(-10, 120))
    bd.RigidJoint("mount", arm)
    base.joints["elbow"].connect_to(arm.joints["mount"], angle=30)   # as built: elbow 30
    return configuration(bd.Compound(children=[base, arm], label="hinge"), "raised", elbow=90)
```

## Laws

1. **An xbf carries its build closure as pointers and digests** -- script and
   function, modules, data inputs, versions, the key, and the repository commit when
   there is one; never source, never lines on elements, never input contents; names
   remain the per-element tie to code (**Build closure**).
2. **Nothing to disk.** The engine writes the files a model declares -- the xbf
   and the exports its decorators name, nothing beside them -- and the operating
   system's coordination for its warm process: a socket and a lock in
   `tempfile.gettempdir()/cadgen-engine`. It writes no sidecar, record of a build,
   cache, index, memo, mesh or parsed document, and no derived file beside an input
   (a STEP converted to an xbf is an export the author asks for). A restart is cold,
   but for the xbf itself: a model whose xbf holds the closure the model has now is
   not run (**Build closure**).
3. **Standalone.** `cadgen.engine` imports nothing of cadgen outside itself:
   OCP, build123d, numpy and the standard library only. A test holds it.
4. **The file is the document.** The xbf is the native document: units in
   millimetres, prototypes and instances with placements, a name on every part
   and instance, colours per part and per painted face, materials per part and
   per face given one (`XCAFDoc_Material`: name, description, density, and one
   `TDataStd_NamedData` beside it for the rest; `XCAFDoc_VisMaterial`: PBR), element names
   on face, edge and vertex sub-shape
   labels, and an assembly's mechanism (links, pairs, ground, configurations: see
   Kinematics); its main label names what wrote it (`cadgen.engine xbf/1 (cadgen
   <release>)`, `document.Loaded.writer`) and holds the model's build closure
   (**Build closure**). No code location is on an element. STEP, GLB, STL and 3MF
   are translations of it (**Exports**).
   `cadgen xbf export` writes any of them from the xbf alone.
5. **The memo is the only cache, and it lives in the worker.** Every kernel
   operation a model runs is keyed by its inputs. A hit hands back the objects
   the first run made, and a miss makes everything downstream of it miss. So an
   edit costs what it changed, and an element keeps the name its feature history
   gave it across edits. Tessellations live in the serving process's memory,
   keyed by a part's geometry, under a size cap. Across a restart, a model's only
   cache is its own xbf, taken whole or not at all (**Build closure**).
6. **One file per model.** A model is one xbf, plus the exports it declares --
   translations of that xbf, written after it. There is no second file of a
   model: no sidecar, no record of what a build did beside the xbf's own closure.
7. **Provenance is served live, never written.** What made an element -- the
   feature, its operation, the function, file and line of its call -- lives in
   the trace of the worker that built the document, and is asked of it while it
   lives (`made_by` on the facts endpoints, `cadgen where`). A document
   opened with no such worker has none to give, and says so (`made_by: null`).
8. **Poses and motion definitions, never frames.** The document carries
   kinematics -- the mechanism, its configurations (each a pose: where its parts
   stand) and its motions (each a definition: waypoints, an ease, a loop) -- and
   the document holds poses and motion definitions, never baked frames or clips.
   A GLB export is the one place a motion is baked, into a clip of the GLB; the
   engine plays nothing.
9. **Names are the durable tie to code.** An element's name is spelled from the
   function that made its feature and the author's name for that feature (the
   target its result is assigned to, or a label), never from a line or a file:
   moving code renames nothing, and the file holds no line to go stale. A line
   is what the worker serves, now.

## Layout

| Module | What it is |
| --- | --- |
| `model.py` | the decorators (`out` alone); `Model` (`.build`, `.export`, `.snapshot`, `.document`); identity `script.py::function`; a child model called in a body is a sub-assembly built in the same run; `@memo` and its calls; `python model.py` asking the daemon; `paint`, `configuration`, `motion`; `read_step`, `read_xbf` |
| `api.py` | the verbs as functions, through the daemon: `build`, `export`, `snapshot`, `serve`, `where`, `status`, `stop`, `daemon` (`cadgen.build`, ...) |
| `materials.py` | `Material`, a physical material and its look (OpenPBR's glTF subset), and the catalogue of common ones: the module, called |
| `mechanism.py` | kinematics: build123d's joints harvested into AP242's links and pairs, the document's subtree, the structure's `kinematics`, poses (forward kinematics from the ground), and motions (`motion`, a function's samples, `Timeline`: a motion's pair values over time) |
| `build.py` | one build: load the script (or code) afresh, one memo run, document, element names, files |
| `previous.py` | the model's build closure on its xbf's main label (`manifest`), the release from the version beside the code, and the closure made again before the body runs (`again`) |
| `document.py` | result to XCAF (prototypes, instances, names, colours, materials, element names), a warm build's patch of the document it kept, atomic BinXCAF save, read-back of an xbf or a foreign STEP |
| `reader.py` | what a model imports: an XCAF document described as data and assembled into a build123d tree (`read_step`, `read_xbf`); the reader process a build's STEP files are parsed in |
| `export/` | `translate` and the kind registry (`__init__`); an xbf packaged with its closure's files (`cadz`); STEP through the XCAF writer (`step`); placements and meshes from the one mesh cache (`mesh`); GLB, 3MF, binary STL (`glb`, `threemf`, `stl`); a motion baked into a glTF clip (`motion`); a view made a document again, for a posed or parted STEP and an xbf (`tree`) |
| `mesh.py` | per-part tessellation at FreeCAD's default tolerance, edge classes, element facts, and the wire format |
| `serve.py`, `page/` | HTTP: structure JSON and one binary buffer per part; a minimal three.js page |
| `access.py` | which pages may read a served document from another origin: loopback ones |
| `snapshot.py` | `cadgen xbf snapshot`: a document drawn by the CAD Viewer's own scene builder, or a mesh file by its mesh renderers, into a PNG; a script's model drawn from its worker's memory; `CADGEN_SNAPSHOT_RENDERER=occt` draws a document with `render/` |
| `render/` | the stills drawn by OCCT from the engine's meshes: a document held and drawn, and the daemon's render process per served document (`service.py`); the scene (`occt.py`); the viewer's Explode, ported (`explode.py`); a part's buffer as OCCT shapes (`shapes.py`); the headless OpenGL context per platform (`context.py`) |
| `daemon.py`, `worker.py`, `client.py` | one daemon per installation, one warm worker per model script, connect-or-start; a worker's memory cap |
| `cli.py` | `cadgen xbf ...` (also `python -m cadgen.engine`), and `cadgen where` (`where_main`) |
| `kernel/` | the memo (`memo.py`), maker history (`history.py`), element naming (`trace.py`), deterministic kernel settings (`determinism.py`) |

## CLI

```
cadgen xbf build SCRIPT[::MODEL] ...             every model of the script, or one
cadgen xbf build --code 'SOURCE' | - [--name N]  one model from source text (stdin with -)
    [--xbf [F]] [--step [F]] [--glb [F]] [--stl [F]] [--threemf [F]] [--json] [--no-daemon] [--verify]
cadgen xbf export SRC DST [DST ...] [--pose NAME|JSON] [--part NAME] [--no-inputs] [--no-daemon]
cadgen xbf snapshot SRC OUT.png [--display MODE|exploded=A|JSON] [--grid] [--camera C|authored] [--preview]
                    [--pose NAME|JSON] [--annotation FILE] [--measurements FILE]
                    [--width W --height H | --size-profile P] [--no-daemon] [--json]
cadgen xbf serve DOC [--port N] [--three DIR] [--foreground]
cadgen xbf status | stop | daemon
cadgen where DOC [REF]                      the build closure; with REF: PART/face N (edge, vertex), an element name,
                                            or a part or an assembly: its volume and mass
```

These are the commands as invoked today: `where` is the first verb at the top level, and
the `xbf` level disappears at the cutover (`cadgen build`, `cadgen export`, ...).

`SCRIPT` alone builds every decorated function in it; `python SCRIPT [flags]` is the
same build (the script's own process builds nothing: it asks the daemon, and exits with
the build's status). The build returns once every file is on disk. If the document is
unchanged, the xbf is not rewritten, nor is a derived file whose options and stat are
what the build left (**Exports**).
A warm build does not make the document again: the worker holds the XCAF
document it last wrote and patches it (`document.Kept`). A prototype whose label
holds what a cold build would make of it keeps that label untouched; one that
changed or went is cleared at its place in the order and made again at the tag a
cold build gives it, with the assemblies that place it, and the references a cold
build orders by label (a colour's, a material's, a part's placements) are put back
in that order. The mechanism is written again only when a joint, a configuration
or an occurrence it names changed. So the file a patch writes is the cold build's,
byte for byte, and the document the worker serves is the one it wrote.
Nor does the day: the creation date the BinXCAF driver writes in the file's
header is always `01/01/2000` (`document.WRITTEN_DATE`, put over the driver's in
place), so two cold builds of the same code are the same bytes on any day.
If a body raises, the error is reported with the model's frames and the
previous files stay. The daemon starts on demand and exits after an idle hour
(`CADGEN_ENGINE_IDLE` seconds). A worker whose resident memory is over
`CADGEN_ENGINE_WORKER_RSS_MB` after a build (default: a quarter of the machine's memory,
at least 2 GiB; `0` for no cap) is retired: the report says so (`retired`), the worker
exits once it has answered, and the next build is a fresh worker's -- a restart, which
takes the xbf whole when its closure is unchanged (**Build closure**). A render process (**The viewer and the
snapshot**) is held to the same cap after each picture. `CADGEN_ENGINE_DIR` moves its socket and lock.
`CADGEN_ENGINE_MEMO=0` turns memo hits off. `CADGEN_ENGINE_MESH=processes|inline`
picks how parts are meshed. The default, `processes`, uses a warm pool of
processes started with the server, each handed one part's shape as bytes,
largest first, a large part meshed with the kernel's face parallelism too.
`inline` meshes one part at a time over the kernel's own threads. Python
threads add nothing, because the kernel's mesher holds the interpreter's lock.

A served document stays in memory. `serve` reads its file once; from then on
the script's worker sends each build's document to the daemon the moment the
xbf is in place, before the exports are written: its structure, made from the
build's tree, the meshes of the parts that are new to it and to the daemon,
meshed in the worker, and the shapes the daemon lacks. The daemon puts it in
place of the one it serves and answers the waiting page. The file is read
again only when another process rewrote it.

## The viewer and the snapshot

The CAD Viewer opens an `.xbf` by asking this engine to serve it (the `serve` op, through the
daemon) and handing the page the document's URL here; the page reads the structure and every
part buffer from the engine itself and waits on `/version` for the next build. So a part's
bytes leave the engine's memory once, straight to the page, and an edit reaches the page the
moment the engine has it. Every answer names a loopback page's origin as an allowed reader
(`access.py`: `localhost`, `127.0.0.1`, `[::1]`, any port); a page from anywhere else gets no
such header and its browser keeps the answer from it.

**The look.** The regular display modes draw a document as OCCT's raster draws it, one frozen
record (`render/look.py`) that the OCCT prototype (`render/occt.py`) draws by too: each face's
colour, linear, squared (OCCT's ASPECT material: the part's colour times itself), lit by an
ambient 0.5 and two white directional lights at radiance 1 (a key travelling (-0.35, 0.55, -1),
a fill (0.6, 0.2, -0.3)) with a faint Phong highlight (0.08, exponent 44.8), and no environment,
exposure or tone curve: the sum is clamped and sRGB-encoded. Edges are unlit in their class's ink,
1 px; X-ray's faces are lit at 0.22 opacity, Hidden line's are the backdrop's colour. Phong draws
no material's finish, so the look has none: a metal, a glass, a coat or a glow is drawn in Preview,
its colour in the regular modes. The engine serves the record as `GET /api/look`; the page reads
it once per engine, beside the document, and the document's scene brings it as its own lighting
and surface rule (the bundled `@text-to-cad/core`'s `lib/xbf/xbfLook.js`), which the viewer's
viewport and the snapshot page put up in place of the theme's. Preview is drawn as any file's is.

`cadgen xbf snapshot` draws a document as the viewer does. The engine serves and meshes it (the
daemon's meshes, or `--no-daemon` a server of this process), and a headless browser opens
cadgen's snapshot page (the package's built `_runtime/browser`, read as files) with one job for
the xbf family: the page loads the document from the engine and builds the viewer's own scene
of it, dressed in the look the viewer resolves for the same display settings. `--display` takes
a mode (`solid`, `shaded`, `parts`, `xray`, `hidden-line`, `wireframe`), `exploded=A` (the parts
moved apart as the viewer's Explode moves them, by an amount from 0 to 1; with a mode,
`hidden-line,exploded=0.6`), or the display object (`mode`, `grid`, `appearance`, `camera`,
`clip`, `exploded`: `{"enabled", "amount"}`, 0.5 unless given); `--json` reports what was drawn
and each stage's milliseconds (`serveMs`, `meshMs`, `browserMs`, `loadSourceMs`, `buildModelMs`,
`captureMs`, `totalMs`). `parts` and `exploded` for a document that places one part are refused
by name before any browser starts. `--annotation FILE` draws the viewer's annotation over the
model -- each mark on the part it was drawn on, wherever the picture places that part -- from an
annotation file or the Quick Edit packet that carries one (the `.snapshot.json` the viewer writes),
and `--measurements FILE` Measure's dimensions (`[{start, end, label, color}]`). A mark names the
view it was drawn in by label alone (`"view": "view 1"`); the view itself -- its camera
(`position`, `target`, `up`, `orthographicHalfHeight`, the snapshot's own camera object), size and
projection -- is a job of the packet. `--camera authored` draws the picture from the view of the
last mark, exactly (no fit), at its size and projection unless the flags say otherwise: from the
packet given as `--annotation`, else the newest packet beside the annotation file that draws this
document; with none, it is refused by name. A face the mesher left without triangles is named in a
warning. Nothing is written but OUT.

`SRC` may also be a mesh file (`.glb`, `.stl`, `.3mf`): nothing meshes it, the page reads its
bytes and draws them with the mesh renderers the viewer's GLB and mesh tabs call (the bundle's
`headlessScene.js` families); `--pose` is refused for it, a mesh has no mechanism. `SRC` may be a script's model too
(`SCRIPT.py::MODEL`, `cadgen.snapshot(model, ...)`, `Model.snapshot`): the model is built in its
worker and drawn from the document that worker holds, served by a server of the worker's over its
one mesh cache, whether or not the model declares an xbf; nothing but OUT is written. A picture is
never a declaration: no decorator and no build flag draws one.

## Exports

The xbf is the model's result; STEP, GLB, STL and 3MF are exports of a document, and an xbf
can be one too. One writer, `export.translate(document, kind, path, pose=, part=)`,
writes every one, whether a decorator declares the file or `cadgen xbf export` asks for it: the
same document and the same options write the same bytes. Every file is written beside its
target and moved over it.

- **Sources are documents**: an `.xbf`, a `.step` (read into a document first), or a script's
  model (`SCRIPT.py::MODEL`, built first, in its worker). A mesh is never a source: it has no
  B-rep. An xbf is written from a STEP, or from an xbf with `--part`; a posed xbf is refused (a
  pose is a request, never a document). Several destinations read the document once; an export
  runs in the worker that built the document while the file is as it left it, else in the
  daemon's documents worker, which reads it.
- **STEP** (AP242, `export/step.py`): the assembly with instance and product names, colours per
  part and per painted face, material names and densities, element names on face, edge and
  vertex sub-shape labels. Read back through the engine's own reader, the instance tree, every
  name (on the same elements: the reader numbers edges and vertices in its own order), colours
  and materials' names and densities are the xbf's. OCCT's writer drops the look (metallic,
  roughness, emissive and the rest: a material reads back matte), every physical field but the
  density, a face's material (the face keeps its colour), the mechanism and its configurations,
  and the model's key and provenance.
- **GLB**: one mesh per prototype, a node per placement named by its path, a PBR material per
  look (a paint is a primitive of its own) with an extension for each part of the look past
  metallic-roughness (`KHR_materials_ior`, `_transmission`, `_volume`, `_clearcoat`,
  `_anisotropy`, `_emissive_strength`; **Materials**), and every motion of the document an
  animation of the same name (below); **3MF**: an object per prototype, a build item per
  placement, looks through the materials extension (base materials, `pbmetallic` display
  properties, painted triangles naming their own); **STL**: binary, every placed triangle.
  Their meshes are the process's one mesh cache's (`serve.MESHES`), the viewer's: a part the
  build meshed for the viewer is not meshed again for a GLB, nor the other way round.
- `--pose` places the mechanism's links by forward kinematics before anything is written;
  `--part` (an occurrence's path, `board/rack`, or a node's name) writes that subtree as a
  document of its own, its root at the subtree's frame, an xbf of it with the mechanism's share
  of it (the links wholly inside, the pairs between them, their configurations). Every assembly
  of a posed or parted document is an assembly of its own placement.
- `--pose` (on `export` and `snapshot`) and `--part` (on `export`) are the verbs' alone: ad hoc, never cached, never
  declared.
- **A `.cadz`** (`export/cadz.py`) packages an xbf for delivery: the xbf, its build closure's
  source files (the script and every module the closure lists) and, unless `--no-inputs`, its
  inputs, zipped at their places relative to one another, with sorted entries and one fixed
  date. Extracted and built, a drift-free model writes the xbf's bytes again (the closure's
  `commit` aside, which names the repository the original was built in). Self-contained
  delivery is this export, never a property of the document.

**Motions in a GLB.** Each motion (**Motions**) is baked into a glTF animation of its name:
CUBICSPLINE translation and rotation channels for each placement it moves, over one key time
accessor (`export/motion.py`). The keys start at the motion's waypoints (a pingpong's way back
too: its clip is twice the duration) and the halfway point between each two; at a key every
moving placement is where forward kinematics through the mechanism (gears included) puts it,
its in- and out-tangents the one-sided derivatives there, so a step's speed may jump at a
waypoint. A step is halved while at any eighth of it the Hermite strays from the true forward
kinematics by more than 1e-3 of the model's size (the diagonal of what the GLB places) in a
placement's origin or 1e-3 radian in its rotation, at most 4,097 keys. A moved placement's node
stands by translation and rotation, every other by its matrix; the animation's
`extras.cadgen` holds the loop (None, `loop`, `pingpong`) and the duration. STEP, STL and 3MF
ignore motions, and neither `export` nor `@glb` takes an argument for them. On the corpus's
motorbike (2,053 mm across, so a 2.05 mm tolerance), `steer` (a ±20° pingpong sweep of the head
tube over 2 s, a 4 s clip) bakes to 5 keys on 15 placements, within 1.26 mm and 2.1e-4 rad of
forward kinematics at eight points inside every step.

**Decorators carry only `out`.** `@xbf(out=None)` makes the function a model (`XBF/<function>.xbf`
beside the script); `@step(out=None)`, `@glb`, `@stl` and `@threemf` declare translations of
the built document (`<function><suffix>` beside the script). No decorator takes anything else:
a model's material, colours, joints, the pose its joints connect its parts at, its
configurations and its motions are written in its body. Every declared file shows the model as
built: where its joints' `connect_to` placed the parts. A model that wants a second pose on
every build is a second model function with other joint values. A child model's files are
written only when it is built as a target. A model with no `@xbf` writes its derived files and
nothing else.

**Build flags** are additive and select and override on a script: no flag writes what the
decorators declare; `--xbf` alone writes the xbf alone; `--xbf --step out.step --glb` the xbf,
the STEP at `out.step` and the GLB where the model declares it (or its default). A flag with a
path builds one model.
A build whose request names its own files is not taken from the previous document (its files
say nothing of the options they were written with); the memo replays it as ever. The xbf is
saved first and the viewer woken with it: the worker waits for the daemon to hold the patch
(`{"op": "patched"}` on its pipe) before it writes a derived file.

**Code** (`--code SOURCE`, `-` for stdin, `--name N`, default `model`) is one model with no
decorators: the code assigns its final shape to `result` (refused by name otherwise), its files
are the flags alone and each takes a path, it runs in the warm worker of its name, and its
elements are named from its assignment targets with `N` as the function (`f:N.body.3`). Its
source is held in memory only (`sources.MEMORY`), under the path `(code)/N.py` of the working
folder, which no file holds, so its provenance is live only. `--xbf F` gives it the cold cache
as any model has.

**Functions** (`api`, also `cadgen.build`, `cadgen.export`, `cadgen.snapshot`, `cadgen.serve`,
`cadgen.where`): one per command, each through the daemon. `build(target)` returns a `Report`,
`build(code=...)` a `Built` (`.files`, `.report`, `.document` and `.shape` asked of the worker
that holds them, `.export(...)`); `export(source, *destinations, pose=, part=, inputs=)` a list of
`Written`; `snapshot(source, out, ...)` a `Drawn`; `where(doc)` the build closure an xbf holds,
`where(doc, ref)` an element's facts. A `Model` builds, exports and draws itself the same way
(`.build()`, `.export()`, `.snapshot()`), and `.document` is its xbf as last built, read-only
(`.document.closure` its closure).

## Kinematics

At document time the engine harvests every joint on every part of the result and pairs them
two ways: by `connect_to` where the author used it, and, for every joint `connect_to` did not
join, by label -- a moving joint and a `RigidJoint` of the same label on another part are its
pair (the nearest in the tree; two alike are refused by name), and two rigid joints of one label,
each the other's nearest, fasten their parts. `connect_to` wins where both would pair a joint.
build123d's `connect_to` composes locations within each part's own compound, so it cannot place
a part in a nested assembly: there the model puts each part where it stands, authors the two
joints there, and the label pairs them. The engine never moves a part: every value, as built
included, is computed from where the two joints' frames stand in the built tree. The pairs make
a mechanism in ISO 10303-105's terms (STEP AP242's kinematics):

- **Links** are occurrences that move as one: two parts whose rigid joints are connected
  are one link. The **ground** is every link no `connect_to` moved (the joint whose
  `connect_to` is called stays put; the other's part moves), and every part no joint places.
  A part below a jointed sub-assembly moves with it.
- **Pairs**: a revolute joint is a `REVOLUTE_PAIR`, a linear one a `PRISMATIC_PAIR`, a
  cylindrical one a `CYLINDRICAL_PAIR`, a ball a `SPHERICAL_PAIR`, each `_WITH_RANGE` where
  its range is finite, named by the moving joint's label (a label two pairs share takes the
  path of the part that holds it: `hand/left:knuckle`). Link 1 holds the moving joint, link 2
  the rigid one. Its frames, `transform_item_1` and `transform_item_2`, are
  AXIS2_PLACEMENT_3Ds in their links' frames (a link's frame: its first occurrence's
  placement as built) that coincide at value 0; link 2 stands at
  `link_1 · item_1 · M(v) · item_2⁻¹`, `M` a turn about Z, a slide along Z, both
  (cylindrical: angle, then distance) or build123d's intrinsic X-Y-Z rotation (spherical).
  Degrees and millimetres.
- A **gear pair** drives one pair from another (`gear_ratio`, the second's value that times
  the first's). build123d's joints cannot express a coupling, so no model authors one; the
  document, its readers and every pose honour it.
- **Configurations**: the as-built values and each named configuration, as pair values.
- **Motions**: each named motion, as AP242's `INTERPOLATED_CONFIGURATION_SEQUENCE`: its
  segments' times (`t_parameter`) and states (a configuration it names, or pair values).

**Geometry is never held hostage by a mechanism.** What the harvest finds inconsistent is a
warning, said by name, and the document is written all the same: a pair whose part stands
where it cannot put it (moved after `connect_to`, connected after its compound was made, a
rigid joint off the joint's axis) or that joins two parts rigid joints make one body is kept
with its frames and limits, no value, and `inconsistent: <reason>` (the pair's
`cadgen_inconsistent` string), and its links hold as built; a moving joint matching two rigid
joints alike, a linear joint connected to a revolute one (a pin in a slot, which no AP242 lower
pair is) and a configuration value a pair cannot take are left out; pairs that close a loop
are kept and not posed. The warnings travel with the mechanism (a `TDataStd_ExtStringArray` on
its label), in the structure's `kinematics.warnings` and the build report's
`kinematics.warnings`; posing a warned pair is refused by name, and the viewer's Position says
why beside it. The one harvest error is a configuration naming a joint its assembly does not
hold: it means nothing to store.

The xbf holds the mechanism as a subtree under `Main():100`, found by the
`TDataStd_UAttribute` of its GUID, of standard attributes only: `TDataStd_Name` on every
label, `TDataStd_NamedData` for an entity's AP242 name (`entity`) and attributes (named as
the standard names them), `TDataStd_ReferenceList` for references, `TDataStd_RealArray` and
`TDataStd_IntegerArray` for a configuration's values, `XCAFDoc_AssemblyItemRef` for an
occurrence (`mechanism.py` draws the tree); a motion's ease and loop are `cadgen_ease` and
`cadgen_loop` strings in its `NamedData`, AP242 having no such attribute. **STEP carries none of it**: OCCT reads AP242
kinematics but cannot write them, so every STEP, posed or not, is placements only.

`--pose` on `cadgen xbf snapshot` and `cadgen xbf export` poses the mechanism at a
configuration (its name) or at joint values (`'{"elbow": 45}'`, the rest as built): the
engine places each link by composing its pairs from the ground down, exact forward
kinematics, and draws or writes that (a posed STEP is written from the document's tree with
every placement its own). No flag is as built. An unknown configuration or joint, a value out
of range, a pair a gear drives, a pair kept inconsistent and a pair of a loop are refused by
name before anything renders or is written.

## Motions

A motion is a definition in the model's code, beside its configurations:

```python
from cadgen.engine import configuration, motion

configuration(arm, "folded", shoulder=0, elbow=0)
configuration(arm, "reach", shoulder=45, elbow=90)
motion(arm, "cycle", [(0, "folded"), (2, "reach"), (5, "folded")])          # waypoints, eased
motion(arm, "wave", sweep={"wrist": (-30, 30)}, duration=2, loop="pingpong")
motion(arm, "nod", lambda t: {"elbow": 45 + 20 * math.sin(t)}, duration=6)  # a function of time
```

`motion(assembly, name, waypoints_or_fn=None, *, sweep=None, duration=None, ease="cubic",
loop=None)` takes one of: waypoints `[(t, pose), ...]` (seconds from 0, increasing; `pose` a
configuration's name or `{joint: value}`, the rest as built; the last time is the duration), a
sweep `{joint: (from, to)}` with `duration` (two waypoints), or a callable `t -> {joint: value}`
with `duration`. `ease` is `cubic` (each step eased in and out, resting at every waypoint),
`linear` or `spline` (a cubic Hermite through the waypoints, each one's slope its neighbours'
secant); `loop` is None, `"loop"` or `"pingpong"`. A motion never moves the build.

The document keeps the definition (**Kinematics**), never frames. A callable is stored as its
samples, eased `spline`: eight equal steps over its duration, each step halved while the
samples' spline strays from the function by more than the tolerance at a quarter, half or three
quarters of it -- 1e-3 radian for an angle, 1e-3 of the model's size (its bounding box's
diagonal, from the B-rep's geometry) for a distance -- at most 4,097 samples. The same code
samples at the same times, so the file is a function of the code. A motion naming a
configuration or a joint its assembly lacks fails the build by name; one setting a value its
pair cannot take, or moving a pair kept inconsistent or closing a loop, is left out and warned.
A warm build that changes only a motion rewrites the mechanism's subtree and converts no
prototype; the file is the cold build's bytes.

## Materials

A `Material` (`materials.py`) is frozen and SI, and a field it does not know is `None`, never a
guess. Its physical half: `name` (what the catalogue looks it up by), `family` (`metal`,
`plastic`, `elastomer`, `composite`, `wood`, `glass` or `other`), `standard` (the designation:
`UNS A96061`), `description`, `source` (where its numbers came from), `density` kg/m³,
`young_modulus` Pa, `poisson_ratio`, `yield_strength` and `ultimate_strength` Pa,
`thermal_expansion` 1/K, `thermal_conductivity` W/(m·K), `specific_heat` J/(kg·K), and
`isotropic` (False for a laminate or a ply). Its look is a strict subset of OpenPBR, cut to what
glTF carries: `color` (linear RGBA, alpha its opacity), `metallic`, `roughness`, `ior` (1.5),
`anisotropy` and `anisotropy_rotation` (radians), `transmission`, `transmission_color` and
`transmission_depth` (metres), `coat` and `coat_roughness`, `emissive` (RGB) and
`emissive_strength` (1). Each look parameter is one glTF field; the module's docstring is the
contract that maps OpenPBR, the `Material`, glTF and Blender's Principled BSDF onto one another.
Every field is checked as it is made, by name (0-1 ranges, positive physical values). `but(**changes)`
is a copy with changes; `mass(volume_m3)` is kilograms, or None without a density.

The catalogue is one object, the module itself: `materials("6061")` looks an entry up in any
case by its name, its attribute or an alias (`"6061-T6"`, `"al 6061"`, `"steel"`, `"delrin"`),
and a miss raises `LookupError` naming the three nearest; `materials.aluminium_6061` is an
entry; `materials.names()` is every name by family and `materials.by(family="metal")` the
entries a field selects. There is nothing to register: a project's own materials are
`Material(...)` constants in its own module, and `materials.steel_1018.but(roughness=0.2)` a
variant. Its 44 entries:

| Family | Entries |
| --- | --- |
| metals | Aluminum 6061-T6, 5052-H32, 6063-T5, 7075-T6; Steel 1018, A36, S235JR, 4140; Spring steel 1095; Stainless 304, 316, 17-4 PH; Brass C360, C260; Copper C110; Titanium Ti-6Al-4V; Zinc ZA-8; Neodymium magnet N42 (a density, nothing structural) |
| printing plastics | PLA, PETG, ABS, ASA, Nylon PA12, Nylon PA6-CF, TPU 95A (an elastomer), Polycarbonate, SLA resin |
| engineering plastics | Acetal (POM), Acrylic (PMMA), PTFE, PEEK, HDPE, UHMW-PE, Nylon PA6, Polypropylene |
| sheet, composites, wood | FR4, Carbon fibre laminate and Birch plywood (both not isotropic), MDF, Hardboard |
| elastomers, other | Silicone 50A, NBR 70A, EPDM foam, Glass (soda-lime) |

`aluminium`, `steel`, `stainless_steel`, `brass`, `copper`, `titanium`, `nylon`, `acrylic`,
`glass`, `abs_plastic` and `acetal` are short names for the common choice. The seed values were
taken once from public material cards (FreeCAD's) and typical datasheets, as a starting point;
each entry's `source` names which. The looks are the engine's own: metals metallic with
roughness by finish (polished 0.2, machined 0.35-0.45, cast 0.6), brushed aluminium and
stainless anisotropic, plastics dielectric, glass and the clear plastics transmissive, rubber
rough.

In the file a material is XCAF's own `XCAFDoc_Material` (name, description, density) and
`XCAFDoc_VisMaterial` (PBR: colour and alpha, metallic, roughness, emissive, IOR), and every
other field in one `TDataStd_NamedData` on the material's label, named as the schema names it
(none for a material of a name and a density). Read back it is the material, its PBR factors as
32-bit floats. A material change converts the prototypes it is on; a cold and a warm build write
the same bytes. The structure serves every field of each part's and each painted face's material;
a GLB writes the look as glTF's extensions (the volume's thickness the part's, the smallest extent
of its box; anisotropy along a tangent the mesh carries for it). The viewer draws the look in
Preview; its regular modes draw the material's colour in the engine's look, which draws no finish
(**The viewer and the snapshot**). A part's chip card and `cadgen where` weigh it: volume by
density, summed over an assembly.

## Imports

`read_step(path)` reads a `.step`/`.stp`, `read_xbf(path)` an engine document; both copy the
file's geometry into the model's document (`reader.py`). The result is a compound tree of the
file's assembly: an assembly a `Compound` of its components, a part a handle of its own on its
product's geometry (a move places that occurrence, never its siblings), every node named as the
file names that occurrence (its own name, else its product's), coloured and given its material
as the file has them; an xbf's painted faces and face materials come too, and each element keeps
the name its file gives it (**Element names**). An xbf's joints, configurations and motions are
not imported: a part is a part. Linking between models is done by importing code, never by a
file reference. Both are file reads the memo keys by the file's bytes, so a later build's read
of the same bytes is the same objects, and both are the build closure's inputs.

**The fast input is an xbf.** Reading one is many times faster than parsing a STEP of the same
geometry (tom-sized: seconds against most of a minute) and keeps the engine's names losslessly.
Convert a vendor STEP once, keep the xbf beside it, and read the xbf:

```
cadgen xbf export vendor.step vendor.xbf
```

The conversion is the author's: the engine writes no derived file beside an input of its own.
`read_xbf` of a converted STEP is `read_step` of the STEP: the same geometry, names, colours and
materials.

**A STEP is parsed in a reader process.** OCCT's XCAF reader works in many times the memory of
what it makes (2.3 GB for a 354 MB file) and the allocator keeps most of it, so a worker that
parsed a STEP would hold that for its life. A build starts one reader process on its first STEP
read, reuses it for the build's other reads and closes it when the build ends; the reader
answers with the document described as data (products, names, colours, materials, element
names, components) and one `BinTools` dump of the parts' shapes and the components' locations,
and the worker assembles the tree from them. A shape written and read once is what a saved
document reads back, so the tree is a function of the file. An xbf is read in the worker by the
document's own retrieval driver.

## Placements

A part used ten times is one prototype placed ten times, in the worker's memory as in the
document, as FreeCAD's `App::Link` places one. build123d places a shape by deep-copying it
-- `moved`, `located`, `copy.copy`, and the transforms, which deep-copy before they replace
the copy's geometry -- and its deep copy runs `BRepBuilderAPI_Copy` on every node, so each
placement of a vendor assembly was a whole new B-rep. Under the memo a deep copy makes new
objects of the same classes, each with its own label, colour, material, paints, joints and
children, and each a new handle on the SAME `TShape` (`kernel/memo.py`, `_sharing`); so
does `copy.deepcopy(shape)` in a model. A model that wants a B-rep of its own copies it
with `BRepBuilderAPI_Copy`.

build123d's own operations never change a shape they are given in place. `ShapeFix_Shape`
does (it fixes the sub-shapes it is handed): run by a model on a shape a placement shares,
it is given a copy of that shape first, a `copy` feature of the model's, and the other
placements stay as they were (counted: the memo's `copied_on_write`). A `BRep_Builder` a
model drives itself on a placed shape is not seen: it changes every placement.

A file read again in one build from the same site -- a helper reading a screw for each
place it goes -- is decoded once: the read hands back the first read's tree again, new
objects on the same TShapes. A read from another site is another feature, named apart, and
decoded apart: one B-rep carries one set of names.

The document keys a part by its TShape, orientation, paints and materials
(`document.collect`), then merges parts of one geometry key whose elements are named alike
and which hold the same colour, paints and materials (`document.merge`): a helper building
the same bracket for each place it goes is one prototype too. Placements painted or given
materials apart are prototypes apart, the second its own topology over the same geometry
(`document._apart`: the shape tool holds one label per shape).

## Mesh

One tolerance, FreeCAD's default, configurable nowhere: a linear deflection of
`(Lx + Ly + Lz) / 300 * 0.5` of the part's own bounding box (FreeCAD's
Deviation of 0.5 %) and an angular deflection of 28.5 degrees, both absolute,
with the kernel's face parallelism on, through `IMeshTools_Parameters` with
`EnableControlSurfaceDeflectionAllSurfaces` set (analytic faces held to the
deflection; every other knob stays at its default). `BRepMesh_IncrementalMesh`
is used as it is: no check before it, no second pass, no finer retry, no other
tessellator. A face it leaves without triangles is left out and named: the
part's structure lists it under `unmeshed` (ordinals) with a verdict under
`unmeshedCheck` -- the mesher's own status first (`GetStatusFlags` of the
part's mesh), then `BRepCheck_Analyzer`'s on that face ("BRepMesh:
SelfIntersectingWire; BRepCheck: valid") -- and the rest of the part is drawn.
A part is meshed from its binary shape bytes read into a fresh shape, so its
mesh is a function of those bytes alone. Those are its read-back bytes, the
bytes its document holds of it. A build's live part is written and read once to
get them, as saving its document does. A part read from a document (the served
document, `cadgen xbf export`) is written as it was read. So a worker, the
server and an export all mesh one geometry key from the same bytes.

Every edge has a class read from the B-rep: 1 feature (two faces at an angle),
2 tangent (two faces meeting at least G1), 3 seam (a face closing on itself),
5 boundary (one face), 6 non-manifold (more than two). Degenerate edges are left
out.

What the kernel measures of an element is asked for, never extracted ahead:
`GET /api/doc/<id>/part/<key>/face/<ordinal>` is its area, centroid, surface
type, exact bounding box and `BRepCheck` verdict (and a round face's radii);
`.../edge/<ordinal>` its length, curve type, class, faces and endpoints (and a
circle's radius and sweep); `.../vertex/<ordinal>` its position;
`.../groups` how a pick of one face or edge grows, as a STEP's selector table
says: each face's tangent group (faces joined across tangent edges of one
solid) and each edge's chain (edges that run on through a shared vertex). Each also answers `made_by`: the element's name, the feature that made
it (its name's last step), that feature's operation, the function, file and line
of its call, and, for a name that folds its derivation, the derivation spelled
out. It comes from the trace of the worker that built the document, asked over
the worker's pipe (a `where` request, answered by the worker's reading thread,
during a build too) and never from a file: a document served cold, with no live
worker that built it (a daemon started again, `serve --foreground`), answers
`made_by: null`, and so does an element the served version names otherwise than
that worker's last build. The line is the call's in the run that named the part.
`cadgen where DOC REF` serves the document on demand and prints one
element's facts and `made_by`; `REF` is `PART/face N` (`edge`, `vertex`), `PART`
a part's or an instance's name as the structure spells it, or an element name
in the viewer's ref grammar (`f:plate.relief.3`). A `REF` that names a part or an assembly
(its name, or its path: `kit/arm`, the document's name for all of it) answers what it weighs:
each part's volume, measured on its solids (`.../part/<key>/volume`), times its material's
density, summed -- `{ref, parts, volume (mm³), mass (kg, null when no part has a material),
unweighed (the parts without one)}`, and for one part its material and density.

## Wire format

`GET /api/doc/<id>` returns the structure as JSON: prototypes (key, name,
colour, `faceNames`, `edgeNames`, `vertexNames`, `faceColors` by ordinal, `material` and
`faceMaterials` by ordinal -- each every field of the `Material`, named in camelCase
(`youngModulus`, `transmissionColor`, ...), its colour the base colour it is drawn in, SI
-- and once meshed `unmeshed`/`unmeshedCheck`), instances (prototype, path, a 4x4 world matrix,
colour), the tree, the document's version, and for an assembly with joints `kinematics`: the
links (name, the instances each moves, its frame as built), the pairs (label, type, the two
links, the two frames as 4x4 matrices in their links' frames, limits and units per value; a
gear's ratio and the two pairs it `drives`), the ground link, `asBuilt` and `configurations`
(name → joint → value: a number, or a list for a cylindrical or spherical pair), `motions`
(name → `{duration, ease, loop, keys}`, each key `{t, configuration}` or `{t, values}`), and
`loop` (the pairs closing a loop, or null). A page poses the instances from it: each link's
placement at the values over its placement as built, applied to its instances.
`POST /api/doc/<id>/pose` (`{"pose": NAME | {joint: value}}`) answers the id of the
document served at that pose, its `kinematics.pose` the values it stands at.
`GET /api/look` is the look the regular display modes are drawn in (**The viewer and the
snapshot**), the same for every document.
`GET /api/doc/<id>/version?after=V&wait=S` answers as soon as the version is not
`V` (or after `S` seconds, at most 60): the page waits on it, so a build's patch
reaches it at once. The answer carries the patch's timings.

Between the worker and the daemon a patch is one frame on the worker's pipe: a
JSON line `{"id", "patch": {"path", "structure", "meshes": [[key, size,
unmeshed]], "shapes": [[key, size]], "timings"}, "bytes": N}`, then exactly `N`
bytes: the meshes' buffers, then the shapes' binary shape bytes, in the order
listed.

`GET /api/doc/<id>/part/<key>` returns one part's buffer. A client reads it
with typed-array views, without parsing. The header grew from 64 to 80 bytes in
`XBM2` (the vertex table's count and offset did not fit). The 80-byte
little-endian header is:

| Bytes | Field |
| --- | --- |
| 0-3 | magic `XBM2` |
| 4-7 | format version (2) |
| 8-31 | counts: nodes, indices, faces, edges, edge points, vertices (u32 each) |
| 32-59 | byte offsets of the seven arrays below (u32 each) |
| 60-67 | linear and angular deflection (f32 each) |
| 68-79 | reserved |

Then the arrays, each 4-byte aligned: positions `f32[N*3]`, surface normals
`f32[N*3]`, triangle indices `u32[I]`, the face table `u32[F*3]` (face ordinal,
first index, index count; an unmeshed face's count is 0), the edge table
`u32[E*4]` (edge ordinal, first point, point count, class), the edge polylines
`f32[P*3]`, and the vertex table `f32[X*4]` (vertex ordinal, x, y, z) of every
vertex of the part. Ordinals are the part's `TopExp.MapShapes` ordinals, the
same ones its element names use. Selecting a face reads a range of the index
buffer, an edge a range of the polyline buffer, a vertex a row of the vertex
table.

`POST /api/doc/<id>/mesh?mode=inline|processes[&drop=1]` meshes every part not
held yet (with `drop=1`, every part again), in parallel, and returns the time it
took, the bytes, the triangles and the unmeshed faces.

## Element names

Every face, edge and vertex of a part is named as FreeCAD's topological naming names
it: by the feature history that made it, so it keeps its name unless that history
changed (`kernel/trace.py`). A name is its kind and its derivation: the atom it
starts from, the n-th element of its kind a feature made, and a step per feature that
made it out of another (`f:plate.body.3;M:plate.relief`: face 3 of the box, modified
by the relief cut). A long derivation folds to its last feature and a digest
(`f:plate.relief~5d1e0c2f9a3b`); the worker that built it spells it out. Every name is a
reference the CAD Viewer's ref grammar parses (`cadRefs` in the bundled `@text-to-cad/core` runtime, mirrored as
`kernel/trace.py`'s `ELEMENT_NAME`): a feature name has at most one dot, every segment
is an identifier, and an atom ends in a number or a digest.

A feature is the author's name for its result. A call whose result an assignment
stores is `<function>.<target>` (`relief = body - hole` in `plate` is `plate.relief`;
`self.body = ...` is `body`; each target of `a, b = ...` names its own); the calls the
same statement made on the way are `<function>.<target>_<operation><n>`
(`plate.relief_make_cylinder1`). The first statement in a function to assign a target
names it as it is; a later one -- a reassignment, `plate = plate - hole` -- is identified
by its statement's skeleton, its syntax tree with every literal (number, string)
masked: it is `<target>_<tag>`, the tag six letters of that skeleton
(`plate_cfjpgj`), counted only among the statements of the same skeleton in the
function (`plate_cfjpgj2` for the second, or for a loop's second run). So inserting a
step of another shape renames none of the steps around it, and a dimension edit -- a
literal -- keeps every name. Two statements of one skeleton still count in order:
inserting one before another of its shape renames the later one, and a statement
inserted before a function's first assignment to a target becomes the one named as it
is. A helper that makes a shape in its `return` statement and hands it back unassigned
(`return bd.Pos(x, 0, 0) * bd.Cylinder(r, h)`) makes it for its caller: its calls are
named by the caller's assignment the result flows into, as the calls that statement
made on the way (`plate.plate_cfjpgj_make_cylinder1`), through further helpers that
return it too. A call whose result is assigned nowhere (a return expression of the
model, an expression statement) is `<function>.<operation><count>` (`plate.fuse1`),
counted among the part's unassigned calls. Nothing is counted in an order OCCT picks: calls
given pieces of the same features' results (a loop over a sketch's faces) count by where
their pieces are, a compound's own elements are numbered piece by piece in that order, and
elements still named alike take `;D2`, `;D3` in it -- OCCT hands a boolean's or a text's
pieces back in an order the heap sets, so two cold builds would otherwise name them apart. So inserting a feature renames only what it
touches, and renaming a variable renames that feature's elements and nothing else. An author's `label` on a shape handed to a later construction
names, in what that construction makes, the feature that first made the labelled shape
(back along what each feature was made of, to where the shape stops being one chain):
a cut appended to it after takes nothing over. A shape a model places, copies or fixes past
build123d, with OCCT's `BRepBuilderAPI_Transform`, `_GTransform` or `_Copy`,
`ShapeFix_Shape`, `ShapeUpgrade_UnifySameDomain` or `BRepFilletAPI_MakeFillet`, is a
feature like any other (`kernel/memo.py`, `_MAKER_FEATURES`): its elements are named
from what the maker reports, or, where it reports nothing, from the shape it was given
by ordinal when the two map one-to-one. A placement copies nothing (**Placements**): a
placed part is its prototype and has its prototype's names, whichever placement the
model labelled. A label on one of several shapes a feature made -- one part of a file
read -- names what is made of that shape alone, never what is made of its siblings; and
placements of one shape labelled apart name their occurrences and pin none of those
labels on the shape they share, where one placement's label would rename what is made of
every other.

An element of a shape read from a file (`read_step`, `read_xbf`) is named within its own product,
the part of the file it belongs to, however often the file places it: by the name the
file gives it (`read.stepcaf.subshapes.name`; a STEP this engine wrote names every face,
and those names come back as they were; `NONE`, the schema's empty name, is none),
else by its ordinal in the product
(`f:<feature>.<ordinal>`), never by an ordinal across the file. A placement or a compound
of a shape no name reaches never claims its elements as its own.

A warm build that hands a part back unchanged keeps its names
while the functions they come from assign the same targets; a rename names it again.

A child model or a `@memo` function names what its `return` statement makes in itself
(`stage.cut1`), never by its caller's assignment as a plain helper's return is: the engine
calls it, and a replay cannot follow the caller. So marking a helper that returns a
construction unassigned (`return body - holes`) renames those elements once; marking one
that returns an assigned name (`return body`) renames nothing.

## Body retention

A body -- a model's, or a `@memo` call's (**Memo functions**), whose key also holds its
arguments -- is one coarse entry above its calls, kept in the worker's memory
(`kernel/memo.py`, `body`). Its key is what it is made of:

- the symbols its function reaches (`sources.py`, `Symbols`): the global names
  its code and the code nested in it load, read through module attributes, its
  defaults and closure cells, and from there, transitively, every first-party
  function, class, child model and value. A function counts by its code: its
  instructions with their constants and names, its variables, arguments and
  flags, but not its file, its lines or its docstring. A class counts by its
  bases and its attributes. A value counts by a canonical form: numbers,
  strings, containers, enums, build123d vectors, locations and colours, numpy
  arrays, first-party dataclass and class instances. Each global binding the
  code reads counts as what it is bound to, by the module that holds it, so
  rebinding a name is an edit. The walk stops at what is not first-party (the
  standard library, build123d, OCP, numpy): the engine's own code covers those;
- every file it reads through the engine, by its bytes: `read_step`, `read_xbf`;
- every file it reads itself, observed, by its bytes: an audit hook on `open`
  (`kernel/memo.py`, `_audited`) hears each file the body opens for reading
  (`open`, `json.load`, `Path.read_text`, `np.loadtxt`), Python's own files
  aside (a `.py` or `.pyc`, one of the standard library or an installed
  package). When the body returns, each is held as its size, mtime and blake2b
  digest, or as missing. On the next build a file with the same size and mtime
  is the same; one whose stat moved is hashed again, and the same bytes keep
  the body (and the new stat); other bytes, or a file gone or come, run it. A
  file opened only for writing is an output, not an input. A pipe, a socket or a
  device has no bytes to hold: the body runs on every build, and the report's
  `retention.fallbacks` names the file. `retention.reads` lists, per body, the
  files it read;
- the engine's own code.

A model's inputs are its code, what it reads through the engine, and every file
its body reads, observed, by their bytes. What the engine cannot see is the
author's: the clock, environment variables, randomness, a file only tested for
(`Path.exists`, `os.stat`: an open is heard, a lookup is not), and OCCT's
readers driven directly instead of through `read_step` or `read_xbf`.

A module falls back to its bytes, and those of every first-party module it
imports, wherever the walk cannot follow a reference by its value: a `getattr`,
`setattr`, `hasattr` or `delattr` with a computed name, `globals()`, `locals()`,
`vars()`, `eval`, `exec`, `compile`, `__import__` or `importlib` in a function
the body reaches (that function's module falls back); a `from x import *`; a
first-party module the code holds as a value (stored in a local it uses other
than to read attributes, passed, or put in a container); a module a function
imports when it runs that the script's load does not import (whether it is in
memory would depend on which bodies ran); a value with no canonical form (a
shape made at import, an open file). Where no module holds what cannot be
keyed, the body always runs. The report's `retention` gives the time keys took
and, per body, each fallback with its reason.

When the key is what it was in the body's last run in this worker, the body is
not called. That run is replayed instead: each entry it hit or made is bound to
this run as a hit binds it, and each feature it recorded is recorded again, in
order. The result is the same live objects, as the body returned them. The trace
is the one a run leaves, so names, the xbf and the caller's own calls come out as
if the body had run. A body that ran a call plain, or whose entries are gone, runs.

Retained bodies follow the memo's retention: what the last four builds used. An
edit to a constant or a function re-runs the bodies that reach it, and the
bodies that hold their results. A comment, a blank line or a docstring re-runs
nothing. An edit to the root re-runs the root alone. A changed input file
re-runs the body that reads it and the bodies that hold its result. The
report's `memo` counts `bodies_run` and `bodies_skipped`, and `bodies` says of each
body of the model how it went: `run` or `replayed`.
`CADGEN_ENGINE_BODIES=0` turns retention off and nothing else. Otherwise a body
must be a function of its inputs: a body that reads the clock or the environment
is not seen to change, and nor is state one body leaves in a module for another
(a registry it fills). A
replayed body's features keep the `file:line` of the run that recorded them,
so after a line-only edit the provenance the worker serves can name a line that moved.

Keys are worked out once a build, for every body the build asks about, against
the namespaces as they are when the first body asks. A code object is read once
per value of it (a module loaded again makes equal code objects), and a file's
dynamic spots once per digest, in the worker's memory.

Measured on a 60,000-call model whose 14 bodies all ran, the logging added
nothing measurable to the build (body 12.4–12.6 s, against 12.7 s with retention
off). The logs do keep about a million more objects alive (2.6 million against
1.6 million). The worker's cyclic collection after each build has to walk them.
Measured with retention on, that collection takes 2.0–2.2 s on a 987-part model,
about 1.3 s of which it also takes with retention off, and 0.7–0.8 s on a
619-part model. It runs after the answer, so only a build sent while it is still
running waits for it.

The read hook costs nothing measurable: on a small model, built in-process, a
no-op took a median 50–51 ms and an edit 0.89–0.95 s with the hook, against
51 ms and 0.89 s with no hook installed.

## Memo functions

`@memo`, bare, marks a function the engine may replay instead of executing (`model.py`,
`Memo`). A call of one inside a build is a body (**Body retention**), named by the
function and a digest of its arguments (`parts.py::wheel(3f2a…)`, the report's `bodies`):
its code is keyed as a model body's is, its kernel calls are logged, and a later build
with the same key replays it -- the same live objects, its features recorded again, so
names and provenance are what a run leaves. A second call with the same arguments in one
build is a new placement of the first's result, as a child model's is. It declares and
writes no file. Outside a build it is its function. Its contract:

- **Arguments by value**, snapshotted at the call (`memo.call_key`): None, bools,
  numbers, strings and bytes, tuples, lists and dicts of them (in order, at most
  `memo.ARGUMENT_VALUES` values), enums and paths; build123d's value types (`Location`,
  `Vector`, `Axis`, `Plane`, `Color`, `Rotation`) and a `Material`; a shape this process's
  operations made, by its memo key, and a replay needs that very TShape again. A call
  given anything else -- a callable, an arbitrary object, a shape made past the engine,
  too many values -- runs every time, as a plain call of its caller.
- **Geometry back**: a shape, or a tuple, list or dict whose leaves are shapes. A call
  returning anything else runs every time it is called (its log stays, for the bodies
  that called it).
- **No methods**: `@memo` on a function whose first parameter is `self` or `cls` raises
  at decoration.
- **Pure**, the author's part: it reads only what its key holds -- its arguments, the
  code and values it reaches, the files it opens -- and writes nothing outside itself. A
  module global it sets for another function to read, a cache it fills on a shared
  object, an argument it mutates: none of it is seen, and a replay leaves it undone.

Each call that runs every time is named in the report's `retention.fallbacks` with why.
The restart key (**Build closure**) folds a `@memo` function's code in as it folds a
child model's.

**When to mark a function.** `@memo` saves only the Python cost of the kernel-memo hits a
replayed function avoids, 0.1–0.4 ms per hit, so mark a function only when it holds seconds of
kernel work an edit elsewhere leaves untouched: roughly a thousand kernel calls or more. Never
per part, never on a helper called many times; whole sub-assembly stages are the usual
candidates (tom's six stages, movement's three layers: 1.3–3 s each, and twice as fast on an
edit), while per-part markers measured under 0.1 s. Nested `@xbf` is never needed for caching:
the operation-level memo handles every kernel call in any model, markers or not.

`cadgen xbf build --verify` runs every body the memo would replay -- a model's and every
`@memo` call's -- and compares each fresh result with the logged one: per shape its
class, label, colour, paints, material and placement, and each leaf's geometry (the same
TShape, else the same binary BRep digest). The report's `verify` gives each body checked
as `same`, `differs: <where>`, or `follows <function>` for one whose result holds a body
that differs. A function whose result differs is marked to run on every call for the
worker's life (`memo.always()`), the bodies that call it with it, and a model that calls it
is not taken from its file. A verifying build does not ask the previous document.

## Build closure

A model's xbf is its cache, as a FreeCAD document is its own: opened, it is what was last
computed, and only what changed is computed again. Its main label holds the model's build
closure (`previous.py`, a `TDataStd_AsciiString` beside the writer's, schema `cadgen.engine
closure/1`), pointers and digests only:

```
schema   "cadgen.engine closure/1"
engine   {"release": "cadgen 0.8.0", "format": "xbf/1", "digest": ...}   the version VERSION stamps; the engine's code
libs     {"build123d": ..., "OCP": ..., "numpy": ...}
source   {"kind": "script", "path": "../src/tom.py", "function": "tom", "commit": "<sha>" | null}
         | {"kind": "code", "name": "bracket", "digest": ...}             code's text is never written
modules  [["../src/lib/arm_parts.py", "9f3c..."], ...]
inputs   [["file", "../STEP/gripper.step", "c0de..."], ["read", "../src/lib/ids.json", "77ab..."], ...]
bodies   how many bodies the model is
key      one digest over engine, libs, source (but its commit), modules and inputs; null where
         the closure cannot say all the model is made of
```

Every path is relative to the xbf's folder. `modules` lists each first-party module the
script's load imported or the model reaches, with a digest of what the model reaches in it: its
functions' code objects with no line tables or docstrings, its classes, the values its bindings
hold (a path value inside the script's folder relative to it), and, for a module that falls back
to its bytes (**Body retention**), each file of its import closure by its bytes. A comment or a
blank line changes no digest; a module the model loads and does not reach holds the empty one.
`inputs` lists every file a body read -- through the engine (`file`: `read_step`, `read_xbf`,
the importers), itself (`read`, a `null` digest for one it found missing), or a text's font
(`font`) -- by its digest. The commit is the repository's at the script, `git rev-parse HEAD`,
null outside one; it names where the code was and is no part of the key. `cadgen where DOC`
prints the closure, `cadgen.where(doc)` and `document.Loaded.closure` read it.

Before a body the memo would not replay runs, the build makes the closure again -- the script
loaded, its modules digested, each input hashed again -- and compares its key with the xbf's.
The same key, in an xbf written by this engine that reads whole, and the body is not run: the
report says `cache: {"used": true, "from": "file"}`, every file `written: false`,
`memo.bodies_loaded` the bodies the document is, `bodies` the root `loaded`, `timings` the key
(`key`) and the read (`read`). A later build in the same worker takes it from memory without
reading it (`"from": "memory"`), as it does a file it wrote itself whose root the memo cannot
replay. Anything else builds as before, and `cache.reason` says why: no file yet, a declared
export missing, written by another engine or program, a file that does not read, no key in it,
replayed from memory, or what changed, by name (`cache.changed`, each change; the CLI prints
`rebuilt: ../src/lib/arm_parts.py changed`): a module changed, is new or is no longer imported,
an input changed or is missing, the engine, a library or the source moved.
`CADGEN_ENGINE_MEMO=0` and `CADGEN_ENGINE_BODIES=0` turn it off with the rest of retention.

The document is taken whole or not at all, never one body's part of it. OCCT's BinXCAF
reader does not give back the doubles it was given: it makes each direction and placement
anew, and a direction made again is normalized again, a rotation orthogonalized again,
one unit in the last place apart. Measured on the corpus: an `l_bracket` read and saved
is four doubles apart, and `motorbike` read and saved twice is 3,839 bytes apart from
once (no fixed point). A document holding a body's result read back would drift from the
cold build's bytes, build after build. So what is written is always made from what this
process's bodies made, and a document read is never written or patched: the first edit
after a restart runs the model, and writes the cold build's bytes. A taken document has
no worker that built it: `made_by` is null until a build of it runs.
