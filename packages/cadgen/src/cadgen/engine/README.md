# cadgen.engine

A model is a parameterless function that returns a build123d shape, declared
with `@model` or any file decorator. The engine runs it in a warm worker, holds the result as an XCAF
document in memory (`@xbf` explicitly writes its BinXCAF file), and writes the derived files the model declares,
each a translation of that document. A drawing is a model like any other: its body
returns 2D geometry (a sketch, a face, wires or edges), and `@dxf` declares its DXF.

```python
from cadgen.engine import xbf, step
import build123d as bd

@xbf                      # XBF/arm.xbf beside the script; @xbf(out="path.xbf") to choose
@step("arm.step")         # also @glb, @stl, @threemf, @dxf; stacked or alone; out= is every decorator's only argument
def arm():
    return bd.Box(10, 20, 30)


if __name__ == "__main__":
    arm()
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

An assembly's mechanism is build123d's joints: `RevoluteJoint`,
`LinearJoint`, `CylindricalJoint`, `BallJoint` and `RigidJoint` on its parts, placed with
`connect_to(..., angle=, position=, angles=)`. The values they are connected at are the
as-built configuration, the pose every file of the model shows; `configuration(assembly, name,
**values)` names another, by joint label, and never moves the build; `couple(assembly, name,
**joints)` constrains several joints to move together by ratio (**Kinematics**); `motion(assembly, name, ...)`
names a motion (**Motions**). Connect parts before putting them in the compound that holds
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
   function, modules, data inputs, versions and the key; never source, never lines on
   elements, never input contents, and nothing of a version control's (the engine calls
   no git); names remain the per-element tie to code (**Build closure**).
2. **Nothing to disk.** The engine writes the files a model declares -- the xbf
   and the exports its decorators name, nothing beside them -- and the operating
   system's coordination for its warm process: a socket and a lock in
   `tempfile.gettempdir()/cadgen-engine`. It writes no sidecar, record of a build,
   cache, index, memo, mesh or parsed document, and no derived file beside an input
   (a STEP converted to an xbf is an export the author asks for). A restart is cold,
   but for the xbf itself: a model whose xbf holds the closure the model has now is
   not run (**Build closure**).
3. **Standalone.** `cadgen.engine` imports nothing of cadgen outside itself:
   OCP, build123d (and ezdxf, build123d's own DXF library, which the DXF export writes
   and checks a drawing with), numpy, Pillow (the renderer's rasteriser) and the standard
   library only. A test holds it.
4. **XCAF is the document; files are explicit.** A live model holds an XCAF document;
   `@xbf` or `--xbf` serializes it as native BinXCAF. It holds: units in
   millimetres, prototypes and instances with placements, a name on every part
   and instance, colours per part and per painted face, materials per part and
   per face given one (`XCAFDoc_Material`: name, description, density, and one
   `TDataStd_NamedData` beside it for the rest; `XCAFDoc_VisMaterial`: PBR), element names
   on face, edge and vertex sub-shape
   labels, and an assembly's mechanism (links, pairs, ground, configurations: see
   Kinematics); its main label names what wrote it (`cadgen.engine xbf/4 (cadgen
   <release>)`, `document.Loaded.writer`) and holds the model's build closure
   (**Build closure**). No code location is on an element. STEP, GLB, STL, 3MF and,
   of a planar document, DXF are translations of it (**Exports**).
   `cadgen export` writes any of them from the xbf alone.
5. **The memo is the only cache, and it lives in the worker.** Every kernel
   operation a model runs is keyed by its inputs. A hit hands back the objects
   the first run made, and a miss makes everything downstream of it miss. So an
   edit costs what it changed, and an element keeps the name its feature history
   gave it across edits. Tessellations live in the serving process's memory,
   keyed by a part's geometry, under a size cap. Across a restart, a model's only
   cache is its own xbf, taken whole or not at all (**Build closure**).
6. **One document per model, only declared files.** Bare `@model` and code built
   with `--model NAME` keep their document in the worker's memory. File decorators
   imply a model and write only their named outputs. No implicit XBF, source file,
   sidecar, registry, cache or thumbnail is written for an in-memory model.
   Model addresses and the viewer's In memory list disappear when their workers
   retire or the engine restarts. Reusing a code model's name updates that address.
7. **Provenance is served live, never written.** What made an element -- the
   feature, its operation, the function, file and line of its call -- lives in
   the trace of the worker that built the document, and is asked of it while it
   lives (`made_by` on the facts endpoints, `cadgen where`). A document
   opened with no such worker has none to give, and says so (`made_by: null`).
8. **Poses and motion definitions, never frames.** The document carries
   kinematics -- the mechanism, its configurations (each a pose: where its parts
   stand) and its motions (each a definition: waypoints and an ease) -- and
   the document holds poses and motion definitions, never baked frames or clips.
   A GLB export is the one place a motion is baked, into a clip of the GLB; the
   engine plays nothing.
9. **Names are the durable tie to code.** An element's name is spelled from the
   function that made its feature and the author's name for that feature (the
   target its result is assigned to, or a label), never from a line or a file:
   moving code renames nothing, and the file holds no line to go stale. A line
   is what the worker serves, now.
10. **A file decorator defines an output file.** Whenever the model it decorates
   runs in a build -- the build's target, or a child another model's body calls --
   that file is written, once per build, from the model's own document: a declared
   file is what the code that last ran makes, whichever model ran it (**Exports**).
11. **Decoration only declares; a model script is Python.** Importing cadgen or
    defining a decorated function never builds a model, writes its outputs, starts a
    worker, or exits the caller. An explicit call builds the model and writes its
    declared files, then returns to the caller. Python controls which calls run and
    in what order, including its `__main__` block and conditions; watchers never
    change that control flow. cadgen consumes no script arguments. Engine switches
    are environment variables.
12. **`cadgen build` takes code and requires an output declaration.** Its one
    positional argument is source text assigning `result`. At least one of
    `--model NAME`, `--xbf`, `--step`, `--glb`, `--stl`, `--threemf`, or `--dxf` is
    required before code can run. `--model NAME` declares a named document in
    memory; the file flags declare files, additive among themselves. `--name`
    alone declares no output. A script is built by calling its models (law 11).
13. **The document's kinematics are AP242's, and cadgen stores no kinematic fact the
   standard cannot name.** Every entity and attribute of the mechanism subtree is ISO
   10303-105's as Part 21 writes it (edition 4's domain model's where Part 21 has none);
   only the root says the schema and the units. What the standard cannot say is not
   patched with an attribute of cadgen's own: it is dropped, or refused by name at document
   time (`test_kinematics`' conformance test walks the subtree and holds it).

14. **Connected means warm.** A worker is not retired for idleness while any served
    document it built has a version wait outstanding or completed a sync within `serve.LIVE`
    seconds. The memory cap still applies. Closing a page never stops the engine or its
    worker; once its last sync expires, ordinary idle retirement applies.

15. **One HTTP owner per installation.** The daemon serves page sessions and document
    reads on one authenticated listener. A launcher exits after handing its client the
    engine URL; closing a client never stops the engine. Each page has its own catalog root.
16. **No kernel in request threads.** Reading XBF/STEP, meshing, topology facts and poses
    run in child processes. The daemon holds structures and buffers, never live kernel objects.

## Architecture

One daemon per installation owns the worker pool, served documents and HTTP listener.
A model's worker is a separate process, holding its memo and live kernel objects.
The distribution injects its HTTP application through `daemon.HTTP_FACTORY`; this
standalone engine imports no application module. The application serves the built
viewer and per-launch catalog sessions on that listener. Document routes run in
process, with no network relay. Kernel reads, topology, poses and meshes run in
reader/mesh subprocesses; even an inline-mesh request stays outside the daemon.

The daemon maps each built document to its worker. HTTP watcher accounting has its
own lock, independent of document reads and pool admission. Outstanding version
waits and recent syncs preserve that worker from idle retirement and keep the daemon
alive. The worker-count cap still uses LRU eviction to admit a new script, and the
per-worker memory cap remains enforced after each build.

The installation's private socket supplies its HTTP token. Page URLs exchange it
for a same-site cookie; programmatic clients use Bearer authentication. All routes
retain the application's Host and cross-site mutation checks. Explicit host/port
requests must match an existing listener. A second installed version has a different
installation identity and therefore a different engine.

## Layout

| Module | What it is |
| --- | --- |
| `model.py` | the decorators (`out` alone); `Model` (`.build`, `.export`, `.snapshot`, `.document`); identity `script.py::function`; a child model called in a body is a sub-assembly built in the same run, and the models a target's run ran (`BuildContext.children`); `@memo` and its calls; explicit model calls asking the daemon; a script's module and package (`module_of`); `paint`, `configuration`, `motion`; `read_step`, `read_xbf` |
| `api.py` | the verbs as functions, through the daemon: `build`, `export`, `snapshot`, `serve`, `where`, `status`, `stop`, `daemon` (`cadgen.build`, ...); `observers`, told after each build, export and snapshot what it did, as plain facts (kinds, counts, times, the worker's part, an error's type and frames): the engine counts nothing, and cadgen's telemetry is an observer cadgen appends |
| `materials.py` | `Material`, a physical material and its look (OpenPBR's glTF subset), and the catalogue of common ones: the module, called |
| `mechanism.py` | kinematics: build123d's joints harvested into AP242's links and pairs, couplings (`couple`), the document's subtree, the structure's `kinematics`, poses (forward kinematics from the ground), and motions (`motion`, a function's samples, `Timeline`: a motion's pair values over time) |
| `build.py` | one build: load the script (as Python imports it: a package's module by its dotted name) or code afresh, one memo run, document, element names, files: every model's that ran |
| `previous.py` | the model's build closure on its xbf's main label (`manifest`), the release from the version beside the code, and the closure made again before the body runs (`again`) |
| `document.py` | result to XCAF (prototypes, instances, names, colours, materials, element names; a mesh face refused, `mesh_faces`), a warm build's patch of the document it kept, atomic BinXCAF save, read-back of an xbf or a foreign STEP |
| `reader.py` | what a model imports: an XCAF document described as data and assembled into a build123d tree (`read_step`, `read_xbf`); the reader process a build's STEP files are parsed in; a mesh file as data (`read_mesh`, `Mesh`); a read of a file the build writes refused (`refuse_written`) |
| `refs.py` | references to geometry: the ref grammar, occurrence paths, resolution by name in the latest build, `select` (**References**) |
| `export/` | `translate` and the kind registry (`__init__`); an xbf packaged with its closure's files (`cadz`); STEP through the XCAF writer (`step`); placements and meshes from the one mesh cache (`mesh`); GLB, 3MF, binary STL (`glb`, `threemf`, `stl`); a planar document's drawing through build123d's DXF exporter, with the drawing checks (`dxf`); a motion baked into a glTF clip (`motion`); a view made a document again, for a posed or parted STEP and an xbf (`tree`) |
| `mesh.py` | per-part tessellation at FreeCAD's default tolerance, edge classes, element facts, and the wire format |
| `serve.py`, `page/` | HTTP: structure JSON and one binary buffer per part; a STEP read in a reader process, held by its bytes; a minimal three.js page |
| `access.py` | which pages may read a served document from another origin: loopback ones |
| `snapshot.py` | `cadgen snapshot`: one dispatcher by suffix -- a document, a mesh file, a robot or a DXF drawn by OCCT (`render/`) into a PNG; a script's model drawn from its worker's memory |
| `formats/` | the files the stills draw that are not documents, each read into a structure and part buffers: STL (numpy), 3MF (zip + XML), GLB (OCCT's glTF reader, textures and all), URDF/SDF/SRDF (XML, the viewer's forward kinematics), DXF (group codes, flattened as the viewer's pane draws it) |
| `render/` | the stills drawn by OCCT from the engine's meshes: a document held and drawn, and the daemon's render process per served document and per file (`service.py`); the scene (`occt.py`); a DXF drawn in the 2D look (`drawing.py`); the viewer's Explode, ported (`explode.py`); a part's buffer as OCCT shapes (`shapes.py`); the headless OpenGL context per platform (`context.py`) |
| `daemon.py`, `worker.py`, `client.py` | one daemon per installation, one warm worker per model script, at most one per core alive (`Pool`), connect-or-start; a worker's memory cap |
| `progress.py` | where a build is: the author's `report` and `track`, the engine's stages heard where they begin, the frames, and the terminal's bar |
| `atomic_replace.py` | the one rename every written file is moved into place with: cadgen's bounded Windows retry, kept in the engine |
| `cli.py` | the verbs as commands: `cadgen build`, `export`, `snapshot`, `serve`, `where`, `status`, `stop`, `daemon` (`parser_for`, `run`; also `python -m cadgen.engine VERB`) |
| `kernel/` | the memo (`memo.py`), maker history (`history.py`), element naming (`trace.py`), deterministic kernel settings (`determinism.py`) |

## CLI

```
python SCRIPT.py                             the models the script explicitly calls
cadgen build SOURCE | - [--name N]           one model from source text (stdin with -), its files
    [--xbf [F]] [--step [F]] [--glb [F]] [--stl [F]] [--threemf [F]] [--dxf [F]] [--json] [--verify] [--no-daemon]
cadgen export SRC DST [DST ...] [--pose NAME|JSON] [--part OWNER] [--no-inputs] [--no-daemon] [--json]
                                             SRC an .xbf or a .step
cadgen snapshot SRC OUT.png [--display MODE|exploded=A|JSON] [--grid] [--camera C|authored] [--preview]
                [--pose NAME|JSON] [--annotation FILE] [--measurements FILE]
                [--width W --height H | --size-profile P] [--no-daemon] [--json]
cadgen snapshot --job PACKET [SRC] [the same flags, each over every job]
                                             every picture a Quick Edit packet (or a job file) names
cadgen serve DOC [--port N] [--three DIR] [--foreground]
cadgen where DOC [REF]                       the build closure; with REF (**References**): an element's facts and
                                             what made it, or a part's or an assembly's volume and mass
cadgen status [--json] | stop | daemon
```

`python -m cadgen.engine VERB ...` takes the same verbs, the engine's own entry.

A verb's `--json` (`build`, `export`, `snapshot`, `status`) prints its answer as one JSON object
on stdout, and a failure as `{"ok": false, "error": ...}` there too, exit status 1, so a caller
reading stdout always finds an answer (`cli.answered`); without `--json` a failure is its words
on stderr, exit 2 for a request refused by name before anything ran and 1 for one that failed.

`python SCRIPT.py` executes normal Python. Decoration only registers a model;
imports and definitions build nothing. A call such as `arm()` asks the daemon to
build that model and the children its body calls, prints its report when called
from `__main__`, and returns its shape when the caller uses it. A failed build
raises; a successful call resumes the script. Put executable model calls under
`if __name__ == "__main__":` so other models can import their definitions.
`Model.build(verify=, daemon=)` explicitly requests a build and returns its report.
No watcher selects, suppresses or introduces calls in a Python script.

A script is plain Python, loaded afresh by each build as Python imports it
(`build.load_script`), its folder first on the module search path as `python SCRIPT` has
it, so a module beside it imports by its own name. A script inside a package -- its folder,
and each above it up to the topmost, holding an `__init__.py` -- is that package's module by
its dotted name (`proj.parts.model`), imported as `python -m proj.parts.model` imports it:
its packages first, from their root, which the search path holds next. So its relative imports
(`from .lib import WIDTH`, `from ..common import dims`) and the package by its name
(`import proj.common.dims`) resolve, in a build, in the warm worker build after build, and
under `python -m`; and `python proj/parts/model.py` takes its package the moment the script
imports the engine (`model.as_package_module`), so a relative import written above that
import fails as Python's own does. A script in a folder with no `__init__.py` is the module
its stem names. An edit to any module the script imports is read on the next build, which
re-runs the bodies that reach it (**Body retention**). The script and every first-party module
it imports are compiled from their bytes on disk, never from a `__pycache__` `.pyc`, and a
build writes no bytecode: CPython takes a `.pyc` as current by the source's whole-second mtime
and its size, so a same-length edit inside the second another tool compiled the file would
otherwise run that tool's code.
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
previous files stay. Builds run in the daemon's warm workers, at most one per core alive
(**The daemon and its workers**). `CADGEN_ENGINE_DIR` moves its socket and lock.
`CADGEN_ENGINE_MEMO=0` turns memo hits off. `CADGEN_VIEWER_WARM=0` keeps `cadgen viewer`
from warming the engine at its launch (**The viewer and the snapshot**). `CADGEN_ENGINE_MESH=processes|inline`
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

A served STEP is read in a reader process of the server's, never in the daemon
(the XCAF reader's working memory is many times its result's, and its allocator
keeps it): one read per process, the next process started and warm meanwhile,
answering the structure and each part's shape bytes, which are all the daemon
keeps. The read is held by the file's bytes, so the same bytes served again --
the file touched, copied, or put back -- are not parsed again while the daemon
lives, and a part's key is made from those bytes and its label, so its mesh is
not made again either. A STEP is read outside the server's lock (every other
document is served meanwhile), and one rewritten while it is served is read
again in the background: the page keeps the old version until the new one is
in. Its structure says how long the parse took and which process made it
(`read`: `{seconds, process}`).

## The daemon and its workers

One daemon per installation (`daemon.py`), started on demand by the first command that needs
it and gone after an idle hour (`CADGEN_ENGINE_IDLE` seconds; a page waiting on a served
document keeps it). It holds a pool of warm workers, one per model script (and one per code
name, and one for the documents no worker built), each a process whose memo lives as long as
it does. Its rules are a standard pool's, and nothing else: no memory budget, no admission
guess, no refusal; the operating system does the rest.

- **A cap.** At most `CADGEN_ENGINE_WORKERS` workers are alive (default: the machine's
  cores). A build for a script whose worker is alive goes to it, waiting while it builds
  something else: one build at a time per worker, builds of different scripts at once.
- **First come, first served.** A build for a script with no worker starts one under the cap.
  At the cap, the least recently used idle worker is retired first -- its stdin closed, so it
  exits -- and its slot is the new one's. With every worker busy, the build waits for the first
  to come free, and every build that needs a slot after it waits behind it. A build that waits
  for its own script's busy worker takes no slot.
- **Unwatched idle workers go.** A worker idle `CADGEN_ENGINE_WORKER_IDLE` seconds (default:
  the daemon's idle time, an hour) is retired only when none of its served documents has
  an outstanding version wait or a sync within `serve.LIVE` (90 seconds).
- **A memory cap.** A worker whose resident memory is over `CADGEN_ENGINE_WORKER_RSS_MB` after
  a build (default: a quarter of the machine's memory, at least 2 GiB; `0` for no cap) is
  retired: the report says so (`retired`), and the worker exits once it has answered.

A retired script's next build is a fresh worker's: a restart, which takes the xbf whole when
its closure is unchanged (**Build closure**), and else builds the model cold. A render
process (**The viewer and the snapshot**) is no worker and does not count toward the cap; it
is held to the same memory cap after each picture, and retired after the same idle time.

On Windows every process the engine starts -- each worker, STEP reader and render process --
has a console of its own and no window (`daemon.windowless`: `CREATE_NO_WINDOW`), and the daemon
a process group of its own too (`daemon.detached`), never no console at all: a console program
started with none gets a new console, with a window (#597). What they start in turn (the
meshing and reading pools) shares their windowless console.

`cadgen status` says what the daemon holds (`--json`: the same as data, `api.status()`):

```
engine daemon 98307, up 2h10m · workers 2/10 · idle timeout 1h · RSS cap 16.0 GB
  busy  tom.py         98310   12 builds   3.7 GB  building tom.py::tom · Building geometry · 3/7 stages · 41s
  idle  motorbike.py   98410    3 builds   0.6 GB  4m
  waiting  2 builds, first come first served
retired: finger_01.py (LRU) 3m ago · qdd_actuator.py (idle) 41m ago · tom.py (RSS) 2h ago
serving http://127.0.0.1:52811: 1 document
```

A worker's memory is as it measured it after its last build; a busy one's build is where its
last progress frame says (**Progress**); the time is how long it has been busy, or idle. A
retirement's reason is `LRU` (room at the cap), `idle`, `RSS` (over its memory cap),
`environment` (a build asked for the script with another `PYTHONPATH`, `PATH` or `VIRTUAL_ENV`:
its worker is replaced in its slot) or `exited` (it died).

## Progress

A build says where it is (`progress.py`). The author's half is cadgen's own API, unchanged:
`report(detail)` and `track(items, label=)`, from `cadgen.engine`, each a no-op outside a build.

```python
from cadgen.engine import report, track, xbf

@xbf
def hand():
    fingers = [finger(name) for name in track(FINGERS)]  # "Building geometry  2/5  middle"
    report("palm loft")                                   # "Building geometry  palm loft"
    return bd.Compound(children=[palm(), *fingers], label="hand")
```

`track` counts the items done and names the one in flight (`label(item)`, or the item itself
when it is a string); a sized iterable gives the count its total, and a lazy one is passed
through lazily, counted without one. `report` names what runs now. A body the memo replays
runs nothing, and says nothing. The engine's half is its stages, said where the build reaches
them (`build.py`, through `progress.current`): `load` (the script loads), `body` (a model's body runs: "Building
geometry"), `names` (its result collected and its elements named), `document` (its XCAF document
made) and `write` (each file written: its name the detail, counted of the model's files).

Each is a frame, the whole of where the build is:

```json
{"model": "hand.py::hand", "outputs": ["/abs/XBF/hand.xbf"], "phase": "body", "label": "Building geometry",
 "detail": "middle", "done": 2, "total": 5, "determinate": true, "elapsedMs": 4120}
```

`model` is as the model's report names it, `outputs` the files its build writes, `done` of
`total` the running `track`'s count (`total` null, `determinate` false, without one) and
`elapsedMs` counts from the build's start. A phase opens with a frame of its own; the author's
come at least 100 ms apart, the latest held one sent when the interval is up, so the last
frame is the build's last state. The worker sends each on its pipe (`{"id", "progress"}`), and
the daemon:

- passes it to the build's caller as it does the log's lines (`client.request(on_progress=)`):
  `python model.py` and `cadgen build` draw a bar on stderr when it is a terminal -- each
  phase finished a line with its time, the one in flight repainted in place -- and nothing when
  it is not (`progress.Bar`; `api.build(progress=)` takes the frames instead, `False` none);
- keeps a busy worker's last, which `cadgen status` shows (**The daemon and its workers**);
- wakes the pages waiting on a served document among the frame's `outputs` (below).

Each model's report holds its last frame (`progress`, which `--json` prints), so a failed build
says where it was; a worker that dies during a build fails it with where it was in the words.

**The page's route.** `GET /api/doc/<id>/version?after=V&progress=N&wait=S` answers
`{"version", "patch", "building", "failed", "progress"}` once the version is not `V`, or the
count `progress` is not `N`, or after `S` seconds (60 at most); without `progress=` it waits on
the version alone. `building` is the last frame of the build writing the document now, null when
none is; `failed` is the last build of it that failed, `{"model", "error", "progress"}` -- its
error, and its last frame -- null once another begins or one succeeds; `progress` is a count that
moves whenever either does. A page sends back the last answer's `progress`, so each frame and the
build's end wake it, and nothing polls.

## The viewer and the snapshot

The CAD Viewer and its document reads share the daemon's HTTP listener. The page
reads `/__cad/api/doc/<id>` and waits on its `/version` route; dispatch calls the
engine directly. XBF/STEP loading and geometry facts run in child processes. A
launcher creates or reuses a page session, prints its authenticated URL and exits.
Each session has its own starting directory and catalog. The standalone engine's
minimal page and snapshot server remain available without the enclosing application.

**The look.** The regular display modes draw a document as OCCT's PBR raster draws it, one frozen
record (`render/look.py`) that the OCCT prototype (`render/occt.py`, its finishes and light in
`render/finish.py`) draws by too: one white directional key at radiance 4 travelling
(-0.35, 0.55, -1), and one environment lighting every surface at 0.5, the viewer's neutral studio
baked as a 512 x 256 Radiance image beside the record (`render/studio.hdr`); no ambient term, fill,
shadow, exposure or tone curve: the sum is clamped and sRGB-encoded. Every surface answers that
light as OCCT's PBR program does (the record's `brdf`, `occt`): single scattering, Schlick-GGX
geometry, OCCT's split-sum table, the roughness as given, no anisotropy, which the viewer draws in
place of three's own physical model (the bundled `@text-to-cad/core`'s `lib/viewer/surfaceBrdf.js`),
its environment prefiltered as OCCT bakes it (`lib/viewer/ggxEnvironment.js`: each roughness the
image convolved with that roughness's GGX lobe, the diffuse light OCCT's nine spherical harmonics
of the irradiance). A part without a material,
and every fill (Parts), wears a plain dielectric finish (roughness 0.5) over its colour, linear,
squared, which keeps the saturated colours of OCCT's old Phong look. A part with a material wears
its finish in Solid and Shaded on both renderers, its colour as authored, reflecting that
environment; X-ray draws it in the plain finish. What OCCT's raster has no term for it
approximates (`render/finish.py`): a clear coat eases the base's roughness toward the coat's,
anisotropy is dropped (and the viewer drops it too), transmission is opacity over the colour tinted
by the transmission colour.
Edges are unlit in their class's ink, 1 px; X-ray's faces are lit at 0.22 opacity, Hidden line's
are the backdrop's colour. The engine serves the record as `GET /api/look` and its image as
`GET /api/look/environment`; the page reads both once per engine, beside the document, and the
document's scene brings them as its own lighting and surface rule (the bundled
`@text-to-cad/core`'s `lib/xbf/xbfLook.js`), which the viewer's viewport and the snapshot page put
up in place of the theme's. Preview is drawn as any file's is.

`cadgen snapshot` draws a document as the viewer does. The engine serves and meshes it (the
daemon's meshes, or `--no-daemon` a server of this process), and OCCT draws it from those meshes
(`render/`, below), in the look the viewer resolves for the same display settings. `--display` takes
a mode (`solid`, `shaded`, `parts`, `xray`, `hidden-line`, `wireframe`), `exploded=A` (the parts
moved apart as the viewer's Explode moves them, by an amount from 0 to 1; with a mode,
`hidden-line,exploded=0.6`), `light` or `dark` (the appearance, all a drawing takes), or the display
object (`mode`, `grid`, `appearance`, `camera`, `clip`, `exploded`: `{"enabled", "amount"}`, 0.5
unless given; `hidden` and `isolated`: lists of part refs, an occurrence's path in the refs
grammar, which every look -- Preview, X-ray, Explode, Clip -- leaves out or alone draws, as the
viewer's hide and isolate do, and which a Quick Edit packet given to `--annotation` carries; a
robot's parts are named by the viewer's visual ids too, `link:v1` (`link:v1/object/0` one object of
it); a ref that names nothing is refused by name); `--json` reports what was drawn
and each stage's milliseconds (`serveMs`, `meshMs`, `sceneMs` when a scene is made, `presentMs`,
`frameMs`, `drawMs`, `renderMs`, `totalMs`; a file no document: `readMs` its reader, `openMs` its
render process opening it). `parts` and `exploded` for a document that places one
part are refused by name before anything renders. `--annotation FILE` draws the viewer's annotation over the
model -- each mark on the part it was drawn on, wherever the picture places that part -- from an
annotation file or the Quick Edit packet that carries one (the `.snapshot.json` the viewer writes),
and `--measurements FILE` Measure's dimensions (`[{start, end, label, color}]`). A mark names the
view it was drawn in by label alone (`"view": "view 1"`); the view itself -- its camera
(`position`, `target`, `up`, `orthographicHalfHeight`, the snapshot's own camera object), size and
projection -- is a job of the packet. `--camera authored` draws the picture from the view of the
last mark, exactly (no fit), at its size and projection unless the flags say otherwise: from the
packet given as `--annotation`, else the newest packet beside the annotation file that draws this
document; with none, it is refused by name. A face the mesher left without triangles is named in a
warning. Nothing is written but OUT. OUT may be a folder -- one that is there, or a path ending in a
separator -- and the picture is then `<SRC's stem>_<UTC time>.png` in it (a model's name for a model,
`snapshot.picture_path`), the path the verb prints and reports (`output`).

**A job.** `--job PACKET` replays a whole Quick Edit packet -- or a job file in its words: a job, a list
of jobs, or `{"input", "jobs": [...], "annotation", "measurements"}` -- in one call
(`snapshot.job_pictures`, `api.snapshot_job`), on the machinery above: each job is a view, drawn with
its own `camera` (an output's own `camera` over it), its `display` (its `hidden` and `isolated` parts
among it; the packet's top-level copies are `--annotation`'s), its pose (`jointValues`, as `--pose`
takes it), `preview`, and -- a robot's -- the visuals its `selection.hide` names by the viewer's ids
(`link:v1`, each robot instance carrying its visual's), the packet's ink and dimensions over each
(a job's own `annotation` and `measurements` over the packet's); and each of its `outputs` (`path`,
`width`, `height`, `camera`, `label`) is a picture written at its path (a folder takes
`<stem>[_j<m>][_<n>]_<UTC time>.png`, the job's and the output's place where there are several). A
job's `output` settings finish its pictures: `padding` (0 to 0.15 of the picture each side, 0.04
unless given) and `tightFrame` (true unless given: the fit on every drawn vertex, else on the drawn
parts' box) frame a fitted camera, and a view's own camera -- a position, a target and, orthographic,
a half height -- with `tightFrame` false is drawn exactly, as the viewer writes every view
(`padding` 0, `tightFrame` false); `transparent` draws on nothing (OCCT's backdrop is a fill it draws
whole, so the picture is drawn on black and on white and each pixel's coverage read from the two,
in linear light: `service.matte`; Preview keeps its floor and its shadow, the sweep gone);
`renderScale` (1 to 3) draws the picture that many times its size and brings it down, each pixel
the mean of those it covers (`service.picture`): what the engine draws in pixels -- ink, words,
Measure, the grid -- is as heavy against the picture at any scale, while OCCT's GL lines (edges, a
drawing's hairlines) stay one pixel of the larger frame and read finer: OCCT's core profile draws no
line wider than a pixel, and this is that, not a bug; `sizeProfile` sizes an output that gives no
size. SRC, given, is drawn in place of every job's input, and a flag given overrides every job: the
display, the grid, the camera, Preview, the pose, the ink, the dimensions, the size of every
output; an OUT is refused. Every job is checked before anything renders (an unknown key, a setting
out of range, two outputs at one path, a file that is not there: refused by name, every file left as
it was), and a picture that fails leaves none of the job's. Through the daemon each picture is a frame
of the file's warm render process; `--no-daemon` serves each file once for all its pictures.
`--json` prints `{"ok": true, "pictures": [what each drew]}`.

**The OCCT stills.** A document is drawn by OCCT's own OpenGL renderer from the engine's meshes
(`render/`): one presentation per part and look, each
instance a connected object of it; the modes, the camera fit, clip, grid and Preview are the
page's, Explode is the viewer's layout ported to Python (held to the viewer by one golden its own
scene made), and the annotation's ink and the measurements are drawn over the picture. Through the daemon, a served document a snapshot asks
for gets a render process of its own, started on its first picture and holding the document --
each part's buffer from the daemon's one mesh cache (nothing meshed again), its OCCT shapes, a
scene and its presentations on a hidden OpenGL context: a picture of it is a frame of that
process, answered as PNG bytes that the verb writes to OUT (tom: about a tenth of a second). When
the served document is patched -- a build of it, or its file rewritten -- the process is sent the
new version at once with the buffers of the parts it lacks: the parts it shares with the last keep
their shapes, the new ones are made, and its scene is made again before any picture asks. It goes
with the daemon, and after a picture that leaves it over the workers' memory cap it says so
(`retired`) and exits. `--no-daemon`, and a model's own document in its worker, draw in that
process. The context is a hidden window: Cocoa on macOS, an X server's (Xvfb on a machine with no
screen) or OCCT's EGL on Linux, a hidden window on Windows; where none can be made the snapshot
fails saying so. Nothing is written but OUT.

`SRC` may also be a file the viewer opens that is no document, each read by its reader
(`formats/`) and drawn by OCCT in the same look, through the daemon by a render process of its own
per file (read again when a file it read changes) or `--no-daemon` in this process:

- **A mesh file** (`.stl`, `.3mf`, `.glb`) becomes a structure and part buffers as a document's are,
  so the modes, the camera fit, the grid, clip, Explode, Preview and the annotation are a
  document's. Each mesh carries the viewer's normals (creased at 30 degrees up to 10,000
  triangles, else flat) and its feature edges (three's `EdgesGeometry` at 16 degrees: the folds,
  the open rims), which Solid and the line modes draw. An STL is one part in the look's fill, its
  numbers millimetres; a 3MF is its objects placed by its build in its unit, each triangle in its
  colour group's or base material's colour (a slicer's generated default is none), a base
  material's `pbmetallic` its finish; a GLB is read by OCCT's glTF reader into an XCAF document --
  its node tree, its meshes, each primitive's material (with the extensions the engine writes:
  IOR, transmission, clear coat, anisotropy, emissive strength) and its base colour texture,
  drawn on its texture coordinates in the lit looks. `--pose` is refused: a mesh has no
  mechanism.
- **A robot** (`.urdf`, `.sdf`, `.srdf`) is its link visuals' meshes (STL, 3MF and GLB files, a
  `package://NAME/...` one in the ROS package the description is in: the nearest folder above it
  named NAME or holding a `package.xml` that names it, as the viewer finds it too; a
  box, cylinder, sphere or capsule made and meshed by the kernel), each link's in one colour of
  the Parts palette, placed by the viewer's forward kinematics (`lib/urdf/kinematics.js` and
  `fourBarCoupling.js`, ported and held to a golden its own code made: mimic joints and four-bar
  linkages followed) where the viewer opens the robot -- every control at zero, an SRDF's `home`
  group state over that. `--pose` names an SRDF group state (`group/name`, or a name one group
  has) or gives joint values (degrees, metres) over the opening pose; an unknown joint, a joint
  that is no control and a value past its limits are refused by name.
- **A drawing** (`.dxf`) is drawn as the viewer's DXF pane draws it: from above, fitted with its
  16 px margin, lines, arcs, polylines, splines, ellipses, solids, solid hatches, blocks and
  dimensions one pixel wide in their pens (ACI 7 the look's ink), words set in the platform's UI
  face, on the light or dark paper (`display.appearance`, `render/look.py`'s `DRAWING`). Its
  camera is a plan view, the window a Quick Edit view of it names (`render/drawing.py`'s
  `plan_view`: `{position, target, up, orthographicHalfHeight}` looking straight down at the sheet,
  +Y up), drawn as that window -- given as `--camera`, as `authored` (the view the ink was last
  drawn in), or as a job's view; `--annotation`'s ink lies on the sheet in drawing coordinates,
  painted as the pane paints it (`paint_ink`: strokes round-capped as wide as the viewer drew them
  against its height, areas filled, a text mark's words filling its box), and `--measurements`
  Measure's dimensions over it. Every other flag is refused by name; what it does not draw
  (points, hatch patterns, images) is named in a warning.

`SRC` may be a script's model too
(`SCRIPT.py::MODEL`, `cadgen.snapshot(model, ...)`, `Model.snapshot`): the model is built in its
worker and drawn from the document that worker holds, served by a server of the worker's over its
one mesh cache, whether or not the model declares an xbf; the build writes what the model
declares (law 10), and the snapshot nothing but OUT. A picture is never a declaration: no
decorator and no build flag draws one.

## Exports

The xbf is the model's result; STEP, GLB, STL, 3MF and DXF are exports of a document, and an
xbf can be one too. One writer, `export.translate(document, kind, path, pose=, part=)`,
writes every one, whether a decorator declares the file or `cadgen export` asks for it: the
same document and the same options write the same bytes. Every file is written beside its
target and moved over it; a DXF only when its bytes change.

- **Sources are documents**: an `.xbf` or a `.step` (read into a document first); from Python,
  a `Model` (built first, in its worker) or built code (a `Built`) too. A mesh is never a
  source: it has no B-rep. An xbf is written from a STEP, or from an xbf with `--part`; a posed xbf is refused (a
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
  and the model's key and provenance. The writer's two run-dependent choices are settled
  before it writes: the header's time stamp is `step.WRITTEN_STAMP` (the xbf's date), and the
  colour presentations, which it keeps in a map keyed by shape, are put in the order of the
  shapes they style, so the same document is the same STEP in any process on any day.
- **GLB**: one mesh per prototype, a node per placement named by its path, a PBR material per
  look (a paint is a primitive of its own) with an extension for each part of the look past
  metallic-roughness (`KHR_materials_ior`, `_transmission`, `_volume`, `_clearcoat`,
  `_anisotropy`, `_emissive_strength`; **Materials**), and every motion of the document an
  animation of the same name (below); **3MF**: an object per prototype, a build item per
  placement, looks through the materials extension (base materials, `pbmetallic` display
  properties, painted triangles naming their own); **STL**: binary, every placed triangle.
  Their meshes are the process's one mesh cache's (`serve.MESHES`), the viewer's: a part the
  build meshed for the viewer is not meshed again for a GLB, nor the other way round.
- **DXF** (`export/dxf.py`): a planar document -- a model whose body returns a sketch, a face,
  wires or edges -- drawn by build123d's own `ExportDXF` (ezdxf), in millimetres, each edge
  where the document places it one entity, so a curve stays an ARC, CIRCLE, ELLIPSE or SPLINE.
  A document placing one part is the layer `CUT`; any other is a layer per part, named by its
  occurrence's name (`cut.label, bend.label = "CUT", "BEND"` in the compound returned), parts
  of one name sharing it; a layer of several shapes is one part of them all
  (`bd.Compound([a, b], label="ENGRAVE")`). A solid is refused (`tom.xbf is 3D`), and so is an edge with a point
  off z=0, by its element name. Layers are written in name order and edges in an order of
  their content (curve kind, sampled points, length), never the kernel's traversal, and
  ezdxf's provenance is pinned and its classes sorted: the same geometry is the same bytes in
  any process on any day, so a declared DXF is the bytes `cadgen export flat.xbf flat.dxf`
  writes. The drawing checks run on the document before it is written -- units set, no
  zero-length or duplicate entity, every cut profile closed, something drawn; open geometry
  only on a layer its name's whole tokens make a bend, engrave or reference one -- and an
  error refuses the file, saying which. `export.dxf.validate_dxf_file` runs them on any DXF.
- `--pose` places the mechanism's links by forward kinematics before anything is written;
  `--part` (one part or assembly as a ref's owner names it, **References**: `board/rack`,
  `kit2/axle.2/wheel`, `rack`) writes that subtree as a document of its own, its root at the
  subtree's frame, an xbf of it with the mechanism's share of it (the links wholly inside, the
  pairs between them, their configurations). Every assembly of a posed or parted document is an
  assembly of its own placement. An owner that names several occurrences (`kit2/axle/wheel`,
  where two axles each hold a wheel) is refused, each listed as the grammar tells them apart
  (`kit2/axle.1/wheel, kit2/axle.2/wheel`).
- `--pose` (on `export` and `snapshot`) and `--part` (on `export`) are the verbs' alone: ad hoc, never cached, never
  declared.
- **A `.cadz`** (`export/cadz.py`) packages an xbf for delivery: the xbf, its build closure's
  source files (the script and every module the closure lists) and, unless `--no-inputs`, its
  inputs, zipped at their places relative to one another, with sorted entries and one fixed
  date. Extracted and built, a drift-free model writes the xbf's bytes again. Self-contained
  delivery is this export, never a property of the document.

**Motions in a GLB.** Each motion (**Motions**) is baked into a glTF animation of its name:
CUBICSPLINE translation and rotation channels for each placement it moves, over one key time
accessor (`export/motion.py`). The keys start at the motion's waypoints and the halfway point
between each two; at a key every moving placement is where forward kinematics through the
mechanism (its couplings included) puts it,
its in- and out-tangents the one-sided derivatives there, so a step's speed may jump at a
waypoint. A step is halved while at any eighth of it the Hermite strays from the true forward
kinematics by more than 1e-3 of the model's size (the diagonal of what the GLB places) in a
placement's origin or 1e-3 radian in its rotation, at most 4,097 keys. A moved placement's node
stands by translation and rotation, every other by its matrix; the animation's
`extras.cadgen` holds the duration. A clip is the motion once, and a player repeats it. STEP,
STL and 3MF ignore motions, and neither `export` nor `@glb` takes an argument for them. On the
corpus's motorbike (2,053 mm across, so a 2.05 mm tolerance), `steer` (a ±20° sweep of the head
tube and back over 4 s) bakes to 5 keys on 15 placements, within 1.26 mm and 2.1e-4 rad of
forward kinematics at eight points inside every step.

**Decorators carry only `out`.** `@xbf(out=None)` makes the function a model (`XBF/<function>.xbf`
beside the script); `@step(out=None)`, `@glb`, `@stl`, `@threemf` and `@dxf` declare
translations of the built document (`<function><suffix>` beside the script). No decorator takes
anything else: a model's material, colours, joints, the pose its joints connect its parts at, its
configurations and its motions are written in its body. Every declared file shows the model as
built: where its joints' `connect_to` placed the parts. A model that wants a second pose on
every build is a second model function with other joint values. A model with no `@xbf` writes
its derived files and nothing else.

**Every model that runs writes its files** (law 10). A child model -- one another model's body
calls, in its file or another -- writes the files it declares after its target's are written,
once per build, from its own document: the result its body returned, before its caller placed
or labelled it, its closure the one a build of its own script makes (its folder, the modules its
script imports). So its xbf is the one its own build writes, byte for byte; a later build of it
takes that file (**Build closure**), and a file whose bytes are unchanged is kept. A child called
twice is one model, written once; a child that is also a target of the build writes as the
target; a child run inside a replayed body writes as one its caller ran; one that ran before its
caller raised writes all the same. A child writes what its decorators declare, never what
code's flags name: those are the code's own (law 12). A model taken from its file ran nothing,
and nor did its children. The target's report holds each child's under its identity
(`child_models`: its files, parts and timings; `--json` prints it with the target's), and a
child whose file cannot be written fails the build. A part is written free
(`document.unchecked`): OCCT marks a shape put in a compound as no longer free, which would
make a child's part, placed in its caller's assembly, other bytes than its own build's.

The xbf is saved first and the viewer woken with it: the worker waits for the daemon to hold
the patch (`{"op": "patched"}` on its pipe) before it writes a derived file.

**Code** (`cadgen build SOURCE`, `-` reading it from stdin; `--name N`, default `model`) is
one model with no decorators (law 12): the code assigns its final shape to `result` (refused by
name otherwise), it runs in the warm worker of its name, and its elements are named from its
assignment targets with `N` as the function (`f:N.body.3`). It declares nothing, so its file flags
are its declarations, additive among themselves: each writes its kind at `F`, or with no `F`
where a decorator with no path puts it, in the working folder (`--xbf` is `XBF/N.xbf`, `--step`
`N.step`). `--model N` declares the document in memory without a file.
With neither `--model` nor a file flag, the command fails before running code. A child model it calls writes what that model declares (law 10).
Its source is held in memory only (`sources.MEMORY`), under the path `(code)/N.py` of the working
folder, which no file holds, so its provenance is live only. `--xbf` gives it the cold cache as
any model has. `--json` prints the report, `--verify` verifies its bodies (**Memo functions**),
`--no-daemon` builds in this process.

**Functions** (`api`, also `cadgen.build`, `cadgen.export`, `cadgen.snapshot`, `cadgen.serve`,
`cadgen.where`): one per command, each through the daemon. `build(code, model=, name=, xbf=, ...)` takes
code, as its command does, and returns a `Built` (`.files`, `.report`, `.document` and `.shape`
asked of the worker that holds them, `.export(...)`); `export(source, *destinations, pose=, part=,
inputs=)` a list of `Written`; `snapshot(source, out, ...)` a `Drawn`; `where(doc)` the build
closure an xbf holds, `where(doc, ref)` an element's facts. A `Model` builds, exports and draws
itself the same way (`.build(verify=, daemon=)`, a `Report`: it and its children, as declared;
`.export()`, `.snapshot()`), and `.document` is its xbf as last built, read-only
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
- **Configurations**: the as-built values and each named configuration, as pair values.
- **Couplings**: each `couple(...)`, a constraint among pairs, as AP242's couplers (below).
- **Motions**: each named motion, as AP242's `INTERPOLATED_CONFIGURATION_SEQUENCE`: its
  segments' times (`t_parameter`), interpolation and states (a configuration it names, or pair
  values).

**What AP242 cannot hold is refused or left out, never patched** (law 13). A pair the built
tree contradicts fails the build by name: one whose part stands where it cannot put it (moved
after `connect_to`, connected after its compound was made, a rigid joint off the joint's axis)
or that joins two parts rigid joints make one body has no value the standard can hold. A moving
joint matching two rigid joints alike, a linear joint connected to a revolute one (a pin in a
slot, which no AP242 lower pair is) and a configuration value a pair cannot take are left out
and warned; pairs that close a loop are kept and not posed. The warnings are the build's report
(`kinematics.warnings`), never the document's. The harvest's errors are a configuration, a
motion or a coupling naming a joint its assembly does not hold, a coupling named as a joint is,
a coupling no coupler can hold (below), and a joint two couplings drive.

**Couplings.** `couple(assembly, name, **joints)` is a constraint: its joints move together,
each by its ratio, and only through it. Each joint is named by its label (a pair of several
values takes a ratio per value, as `configuration` takes a value per value). At value `c` each
joint stands at its as-built value plus its ratio times `c`: `c = 0` is as built, so a coupling
never moves the build. Its range is derived, never given: the tightest interval of `c` in which
every joint stays within its own limits (a joint with no limits bounds nothing; a side nothing
bounds is open). Its name poses wherever a joint's does -- `configuration(..., curl=0.5)`, a
motion's waypoints and sweeps, `--pose '{"curl": 0.6}'` -- and configurations and motions
resolve it to pair values at document time, so their AP242 states and a GLB's bake are as ever;
a pose, a configuration or a motion setting one of its joints alone is refused (a
configuration's or a motion's value left out, and warned). `range=` and `lock=` are refused by
name: a coupling's range is its joints' limits, and it is always its joints' only control.

In the document a coupling is the standard's couplers, chained from its first-named value: AP242
edition 4's `KinematicPairWithMotionCoupling` between two pairs' values, of `Kind` `gear_pair`
(two revolute rotations, `CouplingRatios`), `rack_and_pinion_pair` (a revolute rotation and a
prismatic translation, `CouplingRatios`) or `two_joint_coupler` (any other two values, `Scales`),
with `CouplingTypes` saying which value of each pair; and Part 105's `SCREW_PAIR` (`pitch`,
travel per 360 degrees of turn) for one cylindrical pair's turn and travel. Each holds
`s1 · d1 + s2 · d2 = 0` over the two values' displacements from as built, so the ratios read
back as written. A coupling of more than two values is chained as two-value couplers from its
first-named value, never one `three_joint_coupler`. A coupling of one value alone, one driving
a spherical pair (no coupler relates a ball's three angles) and one whose joints' limits leave
it no range around as built fail the build by name. A function coupling, a cam or a loop is no
part of the mechanism: a motion says it.

```python
import build123d as bd
from cadgen.engine import configuration, couple, motion, xbf

CURL = {"index_mcp_flexion": 90, "index_pip": 100, "index_dip": 70}  # degrees at a full curl
BONES = {"base": 30, "proximal": 40, "middle": 25, "distal": 20}     # lengths along +X

@xbf
def index_finger():
    bones, x = [], -BONES["base"]
    for name, length in BONES.items():
        bone = bd.Pos(x, 0, 0) * bd.Box(length, 16, 12, align=(bd.Align.MIN, bd.Align.CENTER, bd.Align.CENTER))
        bone.label, x = name, x + length
        bones.append(bone)
    for joint, parent, child in zip(CURL, bones, bones[1:]):
        knuckle = bd.Axis(child.location.position, (0, 1, 0))
        bd.RevoluteJoint(joint, parent, axis=knuckle, angular_range=(0, CURL[joint]))
        bd.RigidJoint(joint, child, bd.Plane(origin=knuckle.position, z_dir=knuckle.direction).location)
        parent.joints[joint].connect_to(child.joints[joint], angle=0)  # as built: straight
    finger = bd.Compound(children=bones, label="index_finger")
    couple(finger, "index_curl", **CURL)                              # one control: 0 straight, 1 a fist
    return motion(finger, "grip", [(0, {"index_curl": 0}), (1.5, {"index_curl": 1}), (3, {"index_curl": 0})])

SWEEP, TRAVEL = {"left": -70, "right": 70}, 40                       # a door's turn and travel when open

@xbf
def hypercar():
    chassis = bd.Box(4400, 900, 400, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
    chassis.label = "chassis"
    doors = []
    for side, y in (("left", 475), ("right", -475)):
        door = bd.Pos(400, y, 500) * bd.Box(1000, 50, 600)
        door.label = f"door_{side}"
        hinge = bd.Axis((800, 450 if side == "left" else -450, 500), (0.3, 0, 1))  # a helical hinge
        bd.CylindricalJoint(f"door_{side}", chassis, axis=hinge, angular_range=tuple(sorted((0, SWEEP[side]))),
                            linear_range=(0, TRAVEL))
        bd.RigidJoint(f"door_{side}", door, bd.Plane(origin=hinge.position, z_dir=hinge.direction).location)
        chassis.joints[f"door_{side}"].connect_to(door.joints[f"door_{side}"], angle=0, position=0)  # shut
        doors.append(door)
    car = bd.Compound(children=[chassis, *doors], label="hypercar")
    couple(car, "doors", door_left=(SWEEP["left"], TRAVEL), door_right=(SWEEP["right"], TRAVEL))
    return configuration(car, "open", doors=1)                        # shut is as built
```

`cadgen snapshot XBF/index_finger.xbf c.png --pose '{"index_curl": 0.6}'` draws the
knuckles at 54, 60 and 42 degrees: the picture of the same finger with no coupling at
`--pose '{"index_mcp_flexion": 54, "index_pip": 60, "index_dip": 42}'`; its range, `[0, 1]`, is
where every knuckle stays within its own. `cadgen export XBF/hypercar.xbf half.step --pose
'{"doors": 0.5}'` writes the doors a half turn and a half travel open. A gear train is a
coupling: `couple(actuator, "drive", rotor_spin=360, output=80, ...)`, a quasi-direct-drive
actuator's, ranged by its joints' limits. A coupling joint in no pair, or given as many ratios as
its pair has no values, is left out of it and warned. A label names the joint in each placement of a
sub-assembly placed several times, and a name two couplings share takes the path of the assembly
holding each (`hand/index:curl`), as a label two pairs share does.

The xbf holds the mechanism as a subtree under `Main():100`, found by the
`TDataStd_UAttribute` of its GUID, of standard attributes only: `TDataStd_Name` on every
label, `TDataStd_NamedData` for an entity's AP242 name (`entity`) and attributes (named as
the standard names them), `TDataStd_ReferenceList` for references, `TDataStd_RealArray` and
`TDataStd_IntegerArray` for a configuration's values, `XCAFDoc_AssemblyItemRef` for an
occurrence (`mechanism.py` draws the tree). A motion's segments say its ease as AP242's
`interpolation` (`linear_interpolation_type`, `synchronous_interpolation_type`; the last
segment's `discontinuous_interpolation_type`), and a coupling is its couplers under
`:6 "couplings"`, a label per coupling holding them. The structure serves couplings as
`kinematics.couplings`: `{name: {range, terms: [[pair, value index, ratio]]}}`, a side of the
range null where nothing bounds it. **STEP carries none of it**: OCCT reads AP242
kinematics but cannot write them, so every STEP, posed or not, is placements only.

`--pose` on `cadgen snapshot` and `cadgen export` poses the mechanism at a
configuration (its name) or at joint and coupling values (`'{"elbow": 45}'`, `'{"curl": 0.6}'`,
the rest as built): the engine places each link by composing its pairs from the ground down,
exact forward kinematics, and draws or writes that (a posed STEP is written from the document's
tree with every placement its own). No flag is as built. An unknown configuration, joint or
coupling, a value out of range (a joint's, a coupling's, or a joint's a coupling puts it at), a
coupling's joint set alone, a coupling set with one of its joints and a pair of a loop are
refused by name before anything renders or is written.

**The viewer's Position** shows a slider per coupling under the Pose row (0 as built, its range
the coupling's, an open side spanning one turn or 100 mm of its first value): dragging it poses
every joint it drives through the page's forward kinematics (`xbfKinematics.js`), and choosing a
configuration puts it where that configuration stands on it (0 where it is off its line). A
coupling's joints have no slider of their own.

## Motions

A motion is a definition in the model's code, beside its configurations:

```python
from cadgen.engine import configuration, motion

configuration(arm, "folded", shoulder=0, elbow=0)
configuration(arm, "reach", shoulder=45, elbow=90)
motion(arm, "cycle", [(0, "folded"), (2, "reach"), (5, "folded")])          # waypoints, eased
motion(arm, "wave", [(0, {"wrist": -30}), (1, {"wrist": 30}), (2, {"wrist": -30})], ease="linear")
motion(arm, "nod", lambda t: {"elbow": 45 + 20 * math.sin(t)}, duration=6)  # a function of time
```

`motion(assembly, name, waypoints_or_fn=None, *, sweep=None, duration=None, ease="cubic")`
takes one of: waypoints `[(t, pose), ...]` (seconds from 0, increasing; `pose` a configuration's
name or `{joint: value}`, the rest as built; the last time is the duration), a sweep
`{joint: (from, to)}` with `duration` (two waypoints), or a callable `t -> {joint: value}` with
`duration`. `ease` is `cubic` (each step eased in and out, every pair arriving together at every
waypoint: AP242's `synchronous_interpolation_type`) or `linear` (constant speed:
`linear_interpolation_type`). A motion is a finite clip, and a player repeats it: one that comes
back says its way back as waypoints. `loop=` and the `spline` ease are refused by name. A motion
never moves the build.

The document keeps the definition (**Kinematics**), never frames. A callable is stored as its
samples, interpolated linearly: eight equal steps over its duration, each step split while the
samples' line strays from the function by more than half the tolerance at any eighth of it --
the tolerance 1e-3 radian for an angle, 1e-3 of the model's size (its bounding box's diagonal,
from the B-rep's geometry) for a distance -- at most 4,097 samples. A step is split where it
strays most: a kink, where two straight runs meet, is the error's peak between two checks, so
the samples close in on it, and the half tolerance at the checks keeps the line within the whole
one between them (a zigzag with kinks off the grid is held within 0.55 of it, `sin` within 0.44,
where the quarter checks and middle splits let a kink stray to 1.06). The same code
samples at the same times, so the file is a function of the code. A motion naming a
configuration or a joint its assembly lacks fails the build by name; one setting a value its
pair cannot take, or moving a pair that closes a loop, is left out and warned.
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
Solid, Shaded and Preview, and a plain finish in X-ray and Parts. In Solid and Shaded a file's
finishes reflect the studio's neutral environment on those surfaces alone, so a part with no
material is drawn as ever; an engine document's reflect its look's environment, which lights every
surface of it, and OCCT's stills draw them the same way (**The look**). A part's chip card and
`cadgen where` weigh it: volume by density, summed over an assembly.

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

`read_mesh(path)` reads an `.stl` as data, never a shape: a `Mesh` of `vertices` (N x 3, the
file's numbers as millimetres; a point the triangles share is one vertex), `triangles` (M x 3
rows of indices into them), `name` (an ASCII STL's solid's, else the file's stem) and
`bounding_box()` (build123d's `BoundBox`), its arrays read-only. A mesh has no B-rep -- no
surface under a face, no curve under an edge -- so no document holds one: a model measures it
and builds B-rep from its numbers. Its read is keyed by the file's bytes as the others' are,
the same object again for the same bytes, and the file is one of the closure's inputs:

```python
@xbf
def enclosure():
    pcb = read_mesh(HERE / "scans/pcb.stl").bounding_box()   # keyed by the scan's bytes
    return bd.Box(pcb.size.X + 6, pcb.size.Y + 6, pcb.size.Z + 4)
```

A result holding a mesh face -- a triangulation with no surface, as build123d's `import_stl`
makes, or a face a model builds of `read_mesh`'s triangles -- is refused by the occurrence's
path before its document is made (`document.mesh_faces`), and the worker builds on: `kit/scan
is a mesh, not B-rep: face 1 is a triangulation with no surface (as build123d's import_stl
makes), and a document holds B-rep alone. Read the mesh as data with read_mesh(path) ...`. The
kernel's face modifier, which every part passes through (`document.direct`), crashed the
process on one.

build123d's own importers (`import_step`, `import_brep`, `import_stl`, `Mesher.read`) and
OCCT's readers read a file past the engine: a body that calls one runs on every build
(**Body retention**).

**The fast input is an xbf.** Reading one is many times faster than parsing a STEP of the same
geometry (tom-sized: seconds against most of a minute) and keeps the engine's names losslessly.
Convert a vendor STEP once, keep the xbf beside it, and read the xbf:

```
cadgen export vendor.step vendor.xbf
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
document, `cadgen export`) is written as it was read. So a worker, the
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
`cadgen where DOC REF` serves the document on demand and resolves `REF` (**References**):
an element answers its facts and `made_by`, with its canonical `ref`, its occurrence's `path`
(`hand/finger.2`), its part, key, kind, ordinal and name, its points (centroid, position,
endpoints, box) placed where that occurrence stands; a part or an assembly answers what it
weighs: each part's volume, measured on its solids (`.../part/<key>/volume`), times its material's
density, summed -- `{ref, parts, volume (mm³), mass (kg, null when no part has a material),
unweighed (the parts without one, by path)}`, and for one part its path, material and density.
A ref of several selectors answers a list. A ref that names nothing, or several things where
it names one, is refused by name (exit 2).

## Wire format

`GET /api/doc/<id>` returns the structure as JSON: prototypes (key, name,
colour, `faceNames`, `edgeNames`, `vertexNames`, `faceColors` by ordinal, `material` and
`faceMaterials` by ordinal -- each every field of the `Material`, named in camelCase
(`youngModulus`, `transmissionColor`, ...), its colour the base colour it is drawn in, SI
-- and once meshed `unmeshed`/`unmeshedCheck`), instances (prototype, path, a 4x4 world matrix,
colour), the tree, the document's version, and for an assembly with joints `kinematics`: the
links (name, the instances each moves, its frame as built), the pairs (label, type, the two
links, the two frames as 4x4 matrices in their links' frames, limits and units per value), the
ground link, `asBuilt` and `configurations` (name → joint → value: a number, or a list for a
cylindrical or spherical pair), `couplings` (name → `{range, terms}`), `motions` (name →
`{duration, ease, keys}`, each key `{t, configuration}` or `{t, values}`), `loop` (the pairs
closing a loop, or null) and the build's `warnings`. A page poses the instances from it: each link's
placement at the values over its placement as built, applied to its instances.
`POST /api/doc/<id>/pose` (`{"pose": NAME | {joint: value}}`) answers the id of the
document served at that pose, its `kinematics.pose` the values it stands at.
`GET /api/look` is the look the regular display modes are drawn in (**The viewer and the
snapshot**), the same for every document; `GET /api/look/environment` is its environment, the
Radiance image `render/studio.hdr`.
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

## References

A ref names a part, an assembly or one element of a document in the engine's own names
(`refs.py`), and it is resolved by name in the document as it is now -- the latest build. Nothing
of an earlier build is kept: an element a later build moved or resized resolves to where it is
now; one it deleted fails, naming it; one whose making changed (a feature started or stopped
touching it, so it is named otherwise) fails too, listing what its atom is named now (`plate.body.6
is now f:plate.body.6`), never picking one.

```
REF      = [FILE "#"] SELECTOR ("," SELECTOR)*     a bare ELEMENT after an owner is that owner's
SELECTOR = OWNER | [OWNER "/"] ELEMENT              no OWNER: the document's root
OWNER    = SEGMENT ("/" SEGMENT)*                   an occurrence's path, or its last segments
SEGMENT  = LABEL | LABEL "." N | "QUOTED" ["." N]   N: the N-th of the siblings so labelled
ELEMENT  = NAME | face N | edge N | vertex N        NAME: f:…, e:…, v:… (Element names)
```

An occurrence is named by the labels on the way to it. A label its siblings share takes its
number among them, in component order: `hand/finger.2` is the second component labelled `finger`
of `hand`, and a repeated part is always addressed by such a path, never by a number across the
document. An owner may start at any level (`palm`, `finger.2/face 7`) where what it names is one
occurrence; a path whose first segment names the root is read from the root. A label that names
several occurrences is refused with their paths (`finger names 3 occurrences in hand.xbf:
hand/finger.1, hand/finger.2, hand/finger.3`). A segment is read as a label first and as `LABEL.N`
only where no sibling bears it whole; a label holding `/`, `,` or a quote, or one that would read
as another sibling's, is JSON-quoted. An element of an assembly resolves from it when one part
below holds it, so a document of one part takes its elements by the document's name
(`plate/face 3`). A part's elements are numbered by their `TopExp.MapShapes` ordinals, the ones
its names, `faceNames` and the XBM2 buffer use. `FILE` (everything before the first `#`,
JSON-quoted where it holds one) must be the document resolved in: the same file, or the last
segments of its path. A canonical ref -- what `select` labels a shape with and `where` answers --
spells the element by its name (its ordinal only where it has none) under its part's label where
that label names one occurrence, else under the occurrence's path from the root; in a document of
one part, under the document's name. It is also what the CAD Viewer copies of an `.xbf`'s selection,
the file's path first (`/work/XBF/kit.xbf#kit/hand/finger.2/face 7`): the viewer's parser and speller
of this grammar (`cadRefs` in the bundled runtime) are held to this module's, case for case, by a
fixture both suites assert.

`select(source, ref, kind=None)` (`cadgen.engine.select`) is the geometry a ref names, as a
build123d `ShapeList` in world coordinates, one per selector: an element a `Face`, `Edge` or
`Vertex`, a part its shape, an assembly a `Compound` of its components (new placements on the
same geometry). `kind` (`"face"`, `"edge"`, `"vertex"`) is every such element under each instead:
of every part below an occurrence, or of an element (a face's edges; never an edge's faces).
`source` is a document's path (`.xbf`, `.step`), a shape -- `read_xbf`'s or `read_step`'s tree,
whose elements its file names, or a live shape in a model's body, whose elements are named as
this build names them (`trace`: what its document will hold) -- or a model: in a build its result
in this run (a child), outside one built warm through the daemon first, then its xbf read.

```python
kit = read_xbf("XBF/kit.xbf")                                   # a checks script: the latest build
rim = select(kit, "kit/wheel.2/f:wheel.rim.1")[0]               # a Face, world coordinates, labelled
edges = select(kit, "kit/base/f:base.body.6", kind="edge")      # its edges, each labelled

@xbf
def bracket():
    base = plate()                                              # the user picked plate/f:plate.body.6;M:plate.relief
    top = select(base, "f:plate.body.6;M:plate.relief")[0]      # a live shape: this build's names
    return base + bd.Pos(top.center(bd.CenterOf.MASS)) * bd.Cylinder(4, 10)
```

Outside a build every shape carries its canonical ref as its `label`. Inside one a label names
what is made of the shape it is on (**Element names**), so an element comes unlabelled, as
`faces()` gives it, a part or an assembly with its own label, and an element's `topo_parent` is
its part where the tree puts it: `bd.fillet(select(base, "f:…", kind="edge"), 1)` fillets `base`,
and what a selected element makes is named as what `faces()` gives makes. A document `select`
reads in a body is the model's own read, keyed by the file's bytes as `read_xbf`'s: the file is
one of the closure's `inputs`, and a changed file runs the body again.

**A model never reads a file its own build writes**: its own xbf or export, a child's, a
sibling's (`reader.refuse_written`: `select` asks before it reads, `read_xbf` and `read_step` as
they read the file). Read,
the file would be an input the build rewrites -- its key moving on every build, so the model would
never be taken from its file -- or, kept out of the key, a model depending on its own past. A
model selects what it makes from the live shape instead, by the same names, which are this
build's: so its own output never invalidates it.

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
- every file it reads through the engine, by its bytes: `read_step`, `read_xbf`, `read_mesh`;
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
its body reads, observed, by their bytes. A file read past the engine's readers is read
in C or C++, where the hook on `open` hears nothing: build123d's importers (`import_step`,
`import_brep`, `import_stl`, `Mesher.read`) and the OCCT readers a model may drive itself
(`STEPControl_Reader.ReadFile`, `STEPCAFControl_Reader.ReadFile` and `.Perform`, IGES's
readers, `RWStl.ReadFile_s`, `StlAPI`, `BRepTools.Read_s` and `BinTools.Read_s` given a
path, `RWMesh_CafReader.Perform`: glTF's and OBJ's; `kernel/memo.py`, `_READER_TARGETS`).
A body that calls one is never retained, so any edit runs it whole, and `retention.fallbacks`
names the call: `reads ../scans/pcb.stl through build123d's import_stl: its body is never retained
(any edit runs it whole); use read_step/read_xbf/read_mesh`. The file it named is one of its
inputs all the same, held to its bytes (a read of what is no regular file leaves its closure with no
key): a build with nothing changed takes its document, after a restart too, and one after the file
changed builds it. What the engine
cannot see is the author's: the clock, environment variables, randomness, a file only
tested for (`Path.exists`, `os.stat`: an open is heard, a lookup is not), a folder listed
(`glob`, `os.listdir`: a file added to it is not heard), and a file a C library opens for
a reader not on that list. The engine hears Python's `open`, its own readers and the calls on
that list, and none of the rest: no interposer on the C library's `open` hears every open by
any code (an OCCT reader of any name, a C extension's own reader, FreeType loading a font
outside a text), every folder listing, environment variable or program a model's code used.

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

A verifying build (`Model.build(verify=True)`, `cadgen build SOURCE --verify`) runs every
body the memo would replay -- a model's and every `@memo` call's -- and compares each fresh
result with the logged one: per shape its class, label, colour, paints, material and
placement, and each leaf's geometry: the same TShape, else the same binary BRep digest, else the
same geometry in another order of the BRep's entries (each face's area and centre, each edge's
length and centre, each vertex, sorted: what an order-only difference leaves alone, see **Known
and accepted**), which is `same`. The report's `verify` gives each body checked
as `same`, `differs: <where>`, or `follows <function>` for one whose result holds a body
that differs. A function whose result differs is marked to run on every call for the
worker's life (`memo.always()`), the bodies that call it with it, and a model that calls it
is not taken from its file. A verifying build does not ask the previous document.

## Known and accepted

Two byte-level drifts are accepted, on one condition: no bug and no major impact. Each is
the same geometry and the same names written in another order.

- **A replayed body's BRep order.** A body the memo replays can write its BRep entries in
  another order than its cold run did (the f1's casing, the f14d, the radial's barrels).
- **Parallel booleans on a cold build.** OCCT's parallel booleans can order one result's
  sub-shapes differently from one cold build to the next (the w16, 3 of 794 parts; tom's
  yokes; the moonwatch's tachymeter).

Why that holds, checked:

- **Nothing keys on a document's bytes.** The restart key is digests of the model's code
  and inputs and the format (**Build closure**); a derived file is translated from the
  document, and the write compares bytes only to leave an identical file untouched. The one
  place a document's bytes count is a document *read as an input* (`read_xbf`, `read_step`
  of another model's file), which is keyed by its bytes like any input: an order-only rewrite
  of that file builds the reading model once more, to the same result.
- **Names and refs resolve alike.** On the moonwatch's case (its tachymeter one of these),
  a cold build, a body re-run whose 2,673 kernel calls all replayed, and a second cold build
  in a new daemon wrote three different files of one size; their 15,646 element names are the
  same set, each on the same geometry (a face's area and centre, an edge's length and
  centre, a vertex's point), and refs resolve to the same elements through `cadgen where`.
  `test_memo_marker.py` holds the same for a cold build, a replay and a second cold build.
- **`--verify` reads it as `same`.** A leaf whose BRep differs only in order measures alike
  (**Memo functions**): a verification reports it `same`, never `differs`.

The snapshot's part listing (0.7's `--mode list`) is gone by decision: `read_xbf` and `read_step`
(a document's tree) and `cadgen where DOC REF` (what a ref names) are the facts route.

## Build closure

A model's xbf is its cache, as a FreeCAD document is its own: opened, it is what was last
computed, and only what changed is computed again. Its main label holds the model's build
closure (`previous.py`, a `TDataStd_AsciiString` beside the writer's, schema `cadgen.engine
closure/1`), pointers and digests only:

```
schema   "cadgen.engine closure/1"
engine   {"release": "cadgen 0.8.0", "format": "xbf/4", "digest": ...}   the format (keyed); the release and
                                                                    the engine's code digest (information only)
libs     {"build123d": ..., "OCP": ...}                             what decides the geometry
source   {"kind": "script", "path": "../src/tom.py", "function": "tom"}
         | {"kind": "code", "name": "bracket", "digest": ...}             code's text is never written
modules  [["../src/lib/arm_parts.py", "9f3c..."], ...]
inputs   [["file", "../STEP/gripper.step", "c0de..."], ["read", "../src/lib/ids.json", "77ab..."], ...]
bodies   how many bodies the model is
key      one digest over the format, libs, source, modules and inputs; null where the
         closure cannot say all the model is made of
```

Every path is relative to the xbf's folder. `modules` lists each first-party module the
script's load imported or the model reaches, with a digest of what the model reaches in it: its
functions' code objects with no line tables or docstrings, its classes, the values its bindings
hold (a path value inside the script's folder relative to it), and, for a module that falls back
to its bytes (**Body retention**), each file of its import closure by its bytes. A comment or a
blank line changes no digest; a module the model loads and does not reach holds the empty one.
`inputs` lists every file a body read -- through the engine (`file`: `read_step`, `read_xbf`,
`read_mesh`, build123d's importers), itself (`read`, a `null` digest for one it found missing),
or a text's font (`font`) -- by its digest; a file read past the engine's readers leaves the key
null (**Body retention**). `cadgen where DOC` prints the closure, `cadgen.where(doc)` and
`document.Loaded.closure` read it.

**When the format moves.** The key takes the engine's `format` alone (`previous.FORMAT`),
not the release that wrote a document nor a digest of the engine's code: a cadgen release,
or an engine edit, that leaves the written document alone keeps every xbf current. `FORMAT`
is bumped by hand (`xbf/3` to `xbf/4`) when what
an engine writes changes -- the document's structure, the attributes on its labels, the
closure's fields, or what the key covers -- and then every xbf builds once, cleanly: a
document of another format is "written by" another engine (`cache.reason`). The bumps so far:
`xbf/2`, the key left the release, the engine digest and numpy, and the closure its commit;
`xbf/3`, the mechanism subtree took AP242's form (**Kinematics**); `xbf/4`, a function
motion's samples are made for linear interpolation (**Motions**).

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
an input changed or is missing, the format, a library or the source moved.
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

## In-memory model addresses

`@model` declares a model with no files. `@step`, `@xbf` and the other file
decorators also declare models; stacking `@model` with them is optional. A
declaration does no work. Call the function explicitly to build it:

```python
from cadgen import model

@model
def bracket():
    import build123d as bd
    return bd.Box(10, 20, 3)

if __name__ == "__main__":
    bracket()
```

The address is `/absolute/path/parts.py::bracket`. Different scripts can use the
same function name. `cadgen build 'import build123d as bd; result = bd.Box(10, 20, 3)' --model bracket`
builds `code::bracket` in the installation's engine. That name is explicit and
unique within that engine; the same command with changed code updates the same
address and open viewers. Neither code nor an address list is saved. Names are
ASCII Python identifiers; no generated address or update flag is needed.

`cadgen serve ADDRESS`, `cadgen snapshot ADDRESS view.png`,
`cadgen export ADDRESS part.step`, and `cadgen where ADDRESS 'face 1'`
accept model addresses. `cadgen where 'ADDRESS#face 1'` uses the same reference
syntax. Opening a Python script in the viewer lists its declarations without
executing it; choosing one builds that function explicitly. The In memory
section lists the live models, their addresses, build times and states. An
expired code address needs another `build --model`; a script address can be
rebuilt from its source. `--model` needs the warm engine and cannot be combined
with CLI `--no-daemon`. An explicit export writes only the requested file.

Address parsing and declaration listing live in [addresses.py](addresses.py);
the daemon's transient address book lives in [models.py](models.py).
