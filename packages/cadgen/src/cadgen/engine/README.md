# cadgen.engine

A model is a parameterless function that returns a build123d shape, decorated
with `@xbf`. The engine runs it in a warm worker, saves the result as an XCAF
document (`.xbf`, BinXCAF), and writes the derived files the model declares,
each a translation of that document.

```python
import build123d as bd
from cadgen.engine import xbf, step

@xbf                      # XBF/arm.xbf beside the script; @xbf(out="path.xbf") to choose
@step("arm.step")         # also @glb, @stl, @threemf, @snapshot; stacked or alone
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
materials.aluminium` (a catalogue of common engineering materials, names and densities
from FreeCAD's material cards), or `Material("name", density, color=..., metallic=...,
roughness=...)` for any other. `paint(part, faces, material)` gives faces one. On an
assembly, a material is every part's below it that has none: a whole-model default.
Set materials on finished parts, after the last operation: an edit of one is then no
kernel call. Two limits of OCCT's STEP writer show in a STEP export: it names the density
unit gram³·cm² (the exponents wrong, the number right: the reader gives the density back as
written), and it writes materials sharing a name as one entity, so a STEP gives every such
material the first one's description.

An assembly's mechanism is build123d's joints and nothing else: `RevoluteJoint`,
`LinearJoint`, `CylindricalJoint`, `BallJoint` and `RigidJoint` on its parts, placed with
`connect_to(..., angle=, position=, angles=)`. The values they are connected at are the
as-built configuration; `configuration(assembly, name, **values)` names another, by joint
label. Connect parts before putting them in the compound that holds them; where `connect_to`
cannot place them (a nested assembly), give the two joints one label where the parts stand.

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

1. **Nothing to disk.** The engine writes the files a model declares -- the xbf
   and the exports its decorators name, nothing beside them -- and the operating
   system's coordination for its warm process: a socket and a lock in
   `tempfile.gettempdir()/cadgen-engine`. It writes no sidecar, record of a build,
   cache, index, memo, mesh or parsed document. A restart is cold, but for the xbf
   itself: a model whose xbf holds the key the model has now is not run
   (**Previous document**).
2. **Standalone.** `cadgen.engine` imports nothing of cadgen outside itself:
   OCP, build123d, numpy and the standard library only. A test holds it.
3. **The file is the document.** The xbf is the native document: units in
   millimetres, prototypes and instances with placements, a name on every part
   and instance, colours per part and per painted face, materials per part and
   per face given one (`XCAFDoc_Material`: name, description, density;
   `XCAFDoc_VisMaterial`: PBR), element names on face, edge and vertex sub-shape
   labels, and an assembly's mechanism (links, pairs, ground, configurations: see
   Kinematics); its main label names what wrote it (`cadgen.engine xbf/1 (cadgen
   <release>)`, `document.Loaded.writer`) and holds the model's key: digests, and the
   data files the model read by their paths relative to the document (**Previous
   document**). No script path and no code location is in it. STEP, GLB, STL and 3MF
   are translations of it (**Exports**).
   `cadgen xbf export` writes any of them from the xbf alone.
4. **The memo is the only cache, and it lives in the worker.** Every kernel
   operation a model runs is keyed by its inputs. A hit hands back the objects
   the first run made, and a miss makes everything downstream of it miss. So an
   edit costs what it changed, and an element keeps the name its feature history
   gave it across edits. Tessellations live in the serving process's memory,
   keyed by a part's geometry, under a size cap. Across a restart, a model's only
   cache is its own xbf, taken whole or not at all (**Previous document**).
5. **One file per model.** A model is one xbf, plus the exports it declares --
   translations of that xbf, written after it. There is no second file of a
   model: no sidecar, no manifest, no record of what a build did.
6. **Provenance is served live, never written.** What made an element -- the
   feature, its operation, the function, file and line of its call -- lives in
   the trace of the worker that built the document, and is asked of it while it
   lives (`made_by` on the facts endpoints, `cadgen xbf where`). A document
   opened with no such worker has none to give, and says so (`made_by: null`).
7. **Poses, never time.** The document carries kinematics -- the mechanism and
   its configurations, each a pose: where its parts stand, how they may move --
   and never time: no animation, no clip, no keyframe, ever. Nothing in the
   engine keys, plays or bakes motion.
8. **Names are the durable tie to code.** An element's name is spelled from the
   function that made its feature and the author's name for that feature (the
   target its result is assigned to, or a label), never from a line or a file:
   moving code renames nothing, and the file holds no line to go stale. A line
   is what the worker serves, now.

## Layout

| Module | What it is |
| --- | --- |
| `model.py` | the decorators and their options; `Model` (`.build`, `.export`, `.snapshot`, `.document`); identity `script.py::function`; a child model called in a body is a sub-assembly built in the same run; `@memo` and its calls; `python model.py` asking the daemon; `paint` |
| `api.py` | the verbs as functions, through the daemon: `build`, `export`, `snapshot`, `serve`, `where`, `status`, `stop`, `daemon` (`cadgen.build`, ...) |
| `materials.py` | `Material`, a physical material and its PBR look, and the catalogue of common ones |
| `mechanism.py` | kinematics: build123d's joints harvested into AP242's links and pairs, the document's subtree, the structure's `kinematics`, and poses (forward kinematics from the ground) |
| `build.py` | one build: load the script (or code) afresh, one memo run, document, element names, files |
| `previous.py` | the model's key on its xbf's main label, and the xbf asked before the body runs |
| `document.py` | result to XCAF (prototypes, instances, names, colours, materials, element names), a warm build's patch of the document it kept, atomic BinXCAF save, read-back of an xbf or a foreign STEP |
| `export/` | `translate` and the kind registry (`__init__`); STEP through the XCAF writer (`step`); placements and meshes from the one mesh cache (`mesh`); GLB, 3MF, binary STL (`glb`, `threemf`, `stl`); a view made a document again, for a posed or parted STEP and an xbf (`tree`) |
| `mesh.py` | per-part tessellation at FreeCAD's default tolerance, edge classes, element facts, and the wire format |
| `serve.py`, `page/` | HTTP: structure JSON and one binary buffer per part; a minimal three.js page |
| `access.py` | which pages may read a served document from another origin: loopback ones |
| `snapshot.py` | `cadgen xbf snapshot`: a document drawn by the CAD Viewer's own scene builder, or a mesh file by its mesh renderers, into a PNG; also the `@snapshot` decorator's name |
| `daemon.py`, `worker.py`, `client.py` | one daemon per installation, one warm worker per model script, connect-or-start |
| `cli.py` | `cadgen xbf ...` (also `python -m cadgen.engine`) |
| `kernel/` | the memo (`memo.py`), maker history (`history.py`), element naming (`trace.py`), deterministic kernel settings (`determinism.py`) |

## CLI

```
cadgen xbf build SCRIPT[::MODEL] ...             every model of the script, or one
cadgen xbf build --code 'SOURCE' | - [--name N]  one model from source text (stdin with -)
    [--xbf [F]] [--step [F]] [--glb [F]] [--stl [F]] [--threemf [F]] [--snapshot [F]]
    [--pose NAME|JSON] [--part NAME] [--json] [--no-daemon] [--verify]
cadgen xbf export SRC DST [DST ...] [--pose NAME|JSON] [--part NAME] [--no-daemon]
cadgen xbf snapshot SRC OUT.png [--display MODE|JSON] [--grid] [--camera C] [--preview] [--pose NAME|JSON]
                    [--width W --height H | --size-profile P] [--no-daemon] [--json]
cadgen xbf serve DOC [--port N] [--three DIR] [--foreground]
cadgen xbf where DOC REF                    REF: PART/face N (edge, vertex), or an element name
cadgen xbf status | stop | daemon
```

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
(`CADGEN_ENGINE_IDLE` seconds). `CADGEN_ENGINE_DIR` moves its socket and lock.
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

`cadgen xbf snapshot` draws a document as the viewer does. The engine serves and meshes it (the
daemon's meshes, or `--no-daemon` a server of this process), and a headless browser opens
cadgen's snapshot page (the package's built `_runtime/browser`, read as files) with one job for
the xbf family: the page loads the document from the engine and builds the viewer's own scene
of it, dressed in the look the viewer resolves for the same display settings. `--display` takes
a mode (`solid`, `shaded`, `parts`, `xray`, `hidden-line`, `wireframe`) or the display object
(`mode`, `grid`, `appearance`, `camera`, `clip`); `--json` reports what was drawn and each
stage's milliseconds (`serveMs`, `meshMs`, `browserMs`, `loadSourceMs`, `buildModelMs`,
`captureMs`, `totalMs`). `display.exploded` (a document's parts stand where it places them) and
`parts` for a document that places one part are refused by name before any browser starts. A
face the mesher left without triangles is named in a warning. Nothing is written but OUT.

`SRC` may also be a mesh file (`.glb`, `.stl`, `.3mf`): nothing meshes it, the page reads its
bytes and draws them with the mesh renderers the viewer's GLB and mesh tabs call (the bundle's
`headlessScene.js` families); `--pose` is refused for it, a mesh has no mechanism. A build's own
picture (`@snapshot`, `--snapshot`) is drawn from the document in the worker's memory, served by
a server of the worker's over its one mesh cache: what the build meshed is not meshed again.

## Exports

The xbf is the model's result; STEP, GLB, STL and 3MF are exports of a document, and an xbf
can be one too. One writer, `export.translate(document, kind, path, pose=, part=, animate=)`,
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
  and materials' names and densities are the xbf's. OCCT's writer drops the PBR look (metallic,
  roughness, emissive: a material reads back matte), a face's material (the face keeps its
  colour), the mechanism and its configurations, and the model's key and provenance.
- **GLB**: one mesh per prototype, a node per placement named by its path, a PBR material per
  look (a paint is a primitive of its own); **3MF**: an object per prototype, a build item per
  placement, looks through the materials extension (base materials, `pbmetallic` display
  properties, painted triangles naming their own); **STL**: binary, every placed triangle.
  Their meshes are the process's one mesh cache's (`serve.MESHES`), the viewer's: a part the
  build meshed for the viewer is not meshed again for a GLB, nor the other way round.
- `--pose` places the mechanism's links by forward kinematics before anything is written;
  `--part` (an occurrence's path, `board/rack`, or a node's name) writes that subtree as a
  document of its own, its root at the subtree's frame, an xbf of it with the mechanism's share
  of it (the links wholly inside, the pairs between them, their configurations). Every assembly
  of a posed or parted document is an assembly of its own placement.
- `animate` (GLB motion) is reserved: it takes nothing yet.

**Decorators.** `@xbf(out=None)` makes the function a model (`XBF/<function>.xbf` beside the
script): its only argument. A derived-file decorator is its verb run on the built document, and
takes `out` (`<function><suffix>` beside the script) and exactly that verb's options:
`@step(out, pose=, part=)`, `@glb(out, pose=, part=, animate=)`, `@stl` and `@threemf` as
`@step`, `@snapshot(out, display=, camera=, grid=, preview=, pose=, width=, height=)`. Nothing
else: a model's material, colours, joints and configurations are written in its body. A child
model's files are written only when it is built as a target. A model with no `@xbf` writes its
derived files and nothing else.

**Build flags** are additive and select and override on a script: no flag writes what the
decorators declare; `--xbf` alone writes the xbf alone; `--xbf --step out.step --glb` the xbf,
the STEP at `out.step` and the GLB where the model declares it (or its default). A flag with a
path builds one model. `--pose` and `--part` apply to every derived file whose verb takes them.
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
that holds them, `.export(...)`); `export(source, *destinations)` a list of `Written`;
`snapshot(source, out, ...)` a `Drawn`. A `Model` builds, exports and draws itself the same way
(`.build()`, `.export()`, `.snapshot()`), and `.document` is its xbf as last built, read-only.

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
occurrence (`mechanism.py` draws the tree). **STEP carries none of it**: OCCT reads AP242
kinematics but cannot write them, so every STEP, posed or not, is placements only.

`--pose` on `cadgen xbf snapshot` and `cadgen xbf export` poses the mechanism at a
configuration (its name) or at joint values (`'{"elbow": 45}'`, the rest as built): the
engine places each link by composing its pairs from the ground down, exact forward
kinematics, and draws or writes that (a posed STEP is written from the document's tree with
every placement its own). No flag is as built. An unknown configuration or joint, a value out
of range, a pair a gear drives, a pair kept inconsistent and a pair of a loop are refused by
name before anything renders or is written.

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
`cadgen xbf where DOC REF` serves the document on demand and prints one
element's facts and `made_by`; `REF` is `PART/face N` (`edge`, `vertex`), `PART`
a part's or an instance's name as the structure spells it, or an element name
in the viewer's ref grammar (`f:plate.relief.3`).

## Wire format

`GET /api/doc/<id>` returns the structure as JSON: prototypes (key, name,
colour, `faceNames`, `edgeNames`, `vertexNames`, `faceColors` by ordinal, `material` and
`faceMaterials` by ordinal -- each `{name, density, color, metallic, roughness,
emissive, description}`, its colour the base colour it is drawn in, density in kg/m^3
-- and once meshed `unmeshed`/`unmeshedCheck`), instances (prototype, path, a 4x4 world matrix,
colour), the tree, the document's version, and for an assembly with joints `kinematics`: the
links (name, the instances each moves, its frame as built), the pairs (label, type, the two
links, the two frames as 4x4 matrices in their links' frames, limits and units per value; a
gear's ratio and the two pairs it `drives`), the ground link, `asBuilt` and `configurations`
(name → joint → value: a number, or a list for a cylindrical or spherical pair), and `loop`
(the pairs closing a loop, or null). A page poses the instances from it: each link's
placement at the values over its placement as built, applied to its instances.
`POST /api/doc/<id>/pose` (`{"pose": NAME | {joint: value}}`) answers the id of the
document served at that pose, its `kinematics.pose` the values it stands at.
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

An element of a shape read from a file (`read_step`) is named within its own product,
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
- every file it reads through the engine, by its bytes: `read_step`;
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
readers driven directly instead of through `read_step`.

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
The restart key (**Previous document**) folds a `@memo` function's code in as it folds a
child model's.

`cadgen xbf build --verify` runs every body the memo would replay -- a model's and every
`@memo` call's -- and compares each fresh result with the logged one: per shape its
class, label, colour, paints, material and placement, and each leaf's geometry (the same
TShape, else the same binary BRep digest). The report's `verify` gives each body checked
as `same`, `differs: <where>`, or `follows <function>` for one whose result holds a body
that differs. A function whose result differs is marked to run on every call for the
worker's life (`memo.always()`), the bodies that call it with it, and a model that calls it
is not taken from its file. A verifying build does not ask the previous document.

## Previous document

A model's previous xbf is its cache, as a FreeCAD document is its own: opened, it is
what was last computed, and only what changed is computed again. Its main label holds
the model's key (`previous.py`, a `TDataStd_AsciiString` beside the writer's): a digest
of the root body's retention key -- the symbols it reaches, every child model's among
them, the engine's own code -- with the versions of build123d, OCP and numpy, and of
the bytes of every file the model read (through `read_step` or the importers, a font,
or opened itself); those files by their paths relative to the document's folder, so a
fresh process can read them again; and how many bodies the model is. Digests and data
paths only: no source, no script path, no line.

Before a body the memo would not replay runs, the build asks the document: the key the
model has now -- its source key now, each file it read hashed again -- against the one
the xbf holds. The same key, in an xbf written by this engine that reads whole, and the
body is not run: the report says `cache: {"used": true, "from": "file"}`, every file
`written: false`, `memo.bodies_loaded` the bodies the document is, `bodies` the root
`loaded`, `timings` the key (`key`) and the read (`read`). A later build in the same
worker takes it from memory without reading it (`"from": "memory"`), as it does a file
it wrote itself whose root the memo cannot replay. Anything else builds as before, and
`cache.reason` says why: no file yet, a declared export missing, written by another
engine or program, a file that does not read, no key in it, its code changed, a file it
reads changed, or replayed from memory. `CADGEN_ENGINE_MEMO=0` and
`CADGEN_ENGINE_BODIES=0` turn it off with the rest of retention.

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
