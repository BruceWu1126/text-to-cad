# cadgen.engine

A model is a parameterless function that returns a build123d shape, decorated
with `@xbf`. The engine runs it in a warm worker, saves the result as an XCAF
document (`.xbf`, BinXCAF), and writes the exports the model declares from that
document.

```python
import build123d as bd
from cadgen.engine import xbf, step

@xbf                      # arm.xbf beside the script; @xbf(out="path.xbf") to choose
@step(out="arm.step")     # also @glb, @stl, @threemf; stacked or alone
def arm():
    return bd.Box(10, 20, 30)
```

## Laws

1. **Nothing to disk.** The engine writes the files a model declares -- the xbf
   and the exports its decorators name, nothing beside them -- and the operating
   system's coordination for its warm process: a socket and a lock in
   `tempfile.gettempdir()/cadgen-engine`. It writes no sidecar, record of a build,
   cache, index, memo, mesh or parsed document. A restart is cold.
2. **Standalone.** `cadgen.engine` imports nothing of cadgen outside itself:
   OCP, build123d, numpy and the standard library only. A test holds it.
3. **The file is the document.** The xbf is the native document: units in
   millimetres, prototypes and instances with placements, a name on every part
   and instance, colours per part and per painted face, and element names on
   face, edge and vertex sub-shape labels; its main label names what wrote it
   (`cadgen.engine xbf/1 (cadgen <release>)`, `document.Loaded.writer`). No script
   path and no code location is in it. STEP, GLB, STL and 3MF are translations of it.
   `cadgen xbf export` writes any of them from the xbf alone.
4. **The memo is the only cache, and it lives in the worker.** Every kernel
   operation a model runs is keyed by its inputs. A hit hands back the objects
   the first run made, and a miss makes everything downstream of it miss. So an
   edit costs what it changed, and an element keeps the name its feature history
   gave it across edits. Tessellations live in the serving process's memory,
   keyed by a part's geometry, under a size cap.

## Layout

| Module | What it is |
| --- | --- |
| `model.py` | the decorators; identity `script.py::function`; a child model called in a body is a sub-assembly built in the same run |
| `reads.py` | the files a model reads through the engine: `read_bytes`, `read_text`, `read_json`, keyed by their bytes |
| `build.py` | one build: load the script afresh, one memo run, document, element names, files |
| `document.py` | result to XCAF (prototypes, instances, names, colours, element names), atomic BinXCAF save, read-back of an xbf or a foreign STEP |
| `exports.py` | STEP through the XCAF writer; binary STL, GLB and 3MF from the engine's tessellation |
| `mesh.py` | per-part tessellation at FreeCAD's default tolerance, edge classes, element facts, and the wire format |
| `serve.py`, `page/` | HTTP: structure JSON and one binary buffer per part; a minimal three.js page |
| `access.py` | which pages may read a served document from another origin: loopback ones |
| `snapshot.py` | `cadgen xbf snapshot`: a document drawn by the CAD Viewer's own scene builder into a PNG |
| `daemon.py`, `worker.py`, `client.py` | one daemon per installation, one warm worker per model script, connect-or-start |
| `cli.py` | `cadgen xbf ...` (also `python -m cadgen.engine`) |
| `kernel/` | the memo (`memo.py`), maker history (`history.py`), element naming (`trace.py`), deterministic kernel settings (`determinism.py`) |

## CLI

```
cadgen xbf build SCRIPT[::FUNCTION] ... [--json]   build in the script's warm worker
cadgen xbf export DOC [--step F] [--glb F] [--stl F] [--threemf F]
cadgen xbf serve DOC [--port N] [--three DIR] [--foreground]
cadgen xbf snapshot DOC OUT.png [--display MODE|JSON] [--grid] [--camera C] [--preview]
                    [--width W --height H | --size-profile P] [--no-daemon] [--json]
cadgen xbf where DOC REF                    REF: PART/face N (edge, vertex), or an element name
cadgen xbf status | stop | daemon
```

`SCRIPT` alone builds every decorated function in it. The build returns once
every file is on disk. If the document is unchanged, the xbf is not rewritten.
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
type, exact bounding box and `BRepCheck` verdict; `.../edge/<ordinal>` its
length, curve type, class, faces and endpoints; `.../vertex/<ordinal>` its
position. Each also answers `made_by`: the element's name, the feature that made
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
colour, `faceNames`, `edgeNames`, `vertexNames`, `faceColors` by ordinal, and once meshed
`unmeshed`/`unmeshedCheck`), instances (prototype, path, a 4x4 world matrix,
colour), the tree, and the document's version.
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
reference the CAD Viewer's ref grammar parses (`packages/core`'s `cadRefs`, mirrored as
`kernel/trace.py`'s `ELEMENT_NAME`): a feature name has at most one dot, every segment
is an identifier, and an atom ends in a number or a digest.

A feature is the author's name for its result. A call whose result an assignment
stores is `<function>.<target>` (`relief = body - hole` in `plate` is `plate.relief`;
`self.body = ...` is `body`; each target of `a, b = ...` names its own); the calls the
same statement made on the way are `<function>.<target>_<operation><n>`
(`plate.relief_make_cylinder1`); the second assignment to one target in one function is
`<target>2`, then `<target>3`. A call whose result is not assigned (a return
expression, an expression statement) is `<function>.<operation><count>`
(`peg.make_cylinder1`), counted among the part's unassigned calls. So inserting a
feature renames only what it touches, and renaming a variable renames that feature's
elements and nothing else. An author's `label` on a shape handed to a later construction
names, in what that construction makes, the feature that first made the labelled shape
(back along what each feature was made of, to where the shape stops being one chain):
a cut appended to it after takes nothing over. A shape a model places, copies or fixes past
build123d, with OCCT's `BRepBuilderAPI_Transform`, `_GTransform` or `_Copy`,
`ShapeFix_Shape`, `ShapeUpgrade_UnifySameDomain` or `BRepFilletAPI_MakeFillet`, is a
feature like any other (`kernel/memo.py`, `_MAKER_FEATURES`): its elements are named
from what the maker reports, or, where it reports nothing, from the shape it was given
by ordinal when the two map one-to-one. So are the parts build123d's `moved` copies
while placing an assembly: a copy the makers left unaccounted is matched to its
original in the assembly by ordinal, and a part a later compound is built from those
copies is named in the copy that holds it.

An element of a shape read from a file (`read_step`) is named within its own product,
the part of the file it belongs to, however often the file places it: by the name the
file gives it (`read.stepcaf.subshapes.name`; a STEP this engine wrote names every face,
and those names come back as they were; `NONE`, the schema's empty name, is none),
else by its ordinal in the product
(`f:<feature>.<ordinal>`), never by an ordinal across the file. A placement or a compound
of a shape no name reaches never claims its elements as its own.

A warm build that hands a part back unchanged keeps its names
while the functions they come from assign the same targets; a rename names it again.

## Body retention

A model body is one coarse entry above its calls, kept in the worker's memory
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
- every file it reads through the engine, by its bytes: `read_step`, and
  `read_bytes`, `read_text` and `read_json` for a configuration or a table
  (`reads.py`; the bytes are the memo's entry, the text or the value decoded
  from them on every call, so a body may change what it was handed);
- the engine's own code.

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
report's `memo` counts `bodies_run` and `bodies_skipped`.
`CADGEN_ENGINE_BODIES=0` turns retention off and nothing else. A body that opens a
file itself, past the engine's readers (`open`, `Path.read_text`, `np.loadtxt`), is heard: an
audit hook on `open` (`kernel/memo.py`, `_audited`; Python's own files, a `.py` or
`.pyc` or one of the standard library or an installed package, aside) makes it run
on every build, as a plain kernel call does, and the report's `retention.fallbacks`
names the file. Otherwise a body must be a
function of what it reaches as the build starts: a body that reads the clock or
the environment is not seen to change, and
nor is state one body leaves in a module for another (a registry it fills). A
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
