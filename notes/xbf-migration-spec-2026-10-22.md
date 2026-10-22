# xbf migration spec: nine pieces (2026-10-22)
For Jake. Engine: work head **d5fd6c66a** (engine + #568, main 0.7.25/0.7.26 merged); old pipeline: **origin/main 2f793659f**,
cited `file:line` unless a path starts `engine/`. Probes ran in scratch (`--no-daemon`, or a daemon on a private
`CADGEN_ENGINE_DIR`, stopped after). Folded in: refs resolve by name in the latest build; a plain pool; couplings in; Chromium
deleted; motion review dropped; a model's declared files are written whenever it runs in a build (target or child); file
builds are `python model.py`, with no options (a script builds every model it defines); code builds are `cadgen build 'SOURCE'
[flags]` (`-` reads stdin).

## 1. References to geometry (B3)
**Old.** `cadgen.read_scene(path).resolve(line)` gives a `Selection` per ref, with `.shape()` (a world-placed copy) and
`.entities(kind)` (`step_scene.py:44-213`); grammar `cad_ref_syntax.py` + `label_refs.py`, taught in the cad skill's
`inspection-and-validation.md` (`#o1.2.f7`, `#housing.e3`, `part.step#o1.2.f7,f8`, shared labels as `cast_rim:5spoke_1`):
indices into one revision. `_internal/self_input.py` refuses a model's own output. **Copy Prompt today:** main's STEP renderer
copies indices (`/abs/STEP/kit.step#o1.2.f7`); the engine's xbf renderer (`packages/ui/src/renderers/xbf/xbfChips.jsx`) copies
names (`/abs/XBF/kit.xbf#base/f:plate_shape.body.6;M:plate_shape.relief`) with three defects: the owner is the instance path's
last segment, so both placements of a part copy one ref (motorbike: 5 labels on 2 leaves each); an owner outside the label
grammar, or an unnamed element, falls back to unresolvable text (`kit/axle/wheel face 3`); and `cadgen where` refuses the
copied line (probed: "neither PART/face N … nor an element name"). **It must carry the element's name, never its ordinal, and
an owner naming exactly one occurrence.** **Grammar** (one parser in `engine/refs.py`, mirrored by
`packages/core/src/lib/cadRefs.js`; the parity JSON holds both):
```
LINE     = [FILE "#"] SELECTOR ("," SELECTOR)*    a bare NAME after an owner belongs to it (#wheel_2/f:a,f:b)
SELECTOR = OWNER | OWNER "/" NAME | NAME          a bare NAME: a document of one part
OWNER    = LABEL | LABEL "_" N | "o" N ("." N)*   label | alias of a shared label | occurrence path
NAME     = f:… | e:… | v:…                        kernel/trace.py ELEMENT_NAME, unchanged
```
A shared label gets aliases in tree order (`wheel_1`, `wheel_2`) and the bare label is refused with the list (main's
`label_refs.build_label_aliases`: authored labels reserved, groups and parts in one namespace). `oN.N…` is 1-based component
indices from the root (`Loaded.instance_paths` + 1), the owner of last resort for a label outside the grammar; #568's `o:`
tokens go. An element no build named (a foreign STEP served raw) is named as a model's read names it, `f:<product>.<ordinal>`.
**API.** `select(source, ref, kind=None) -> ShapeList` (`cadgen.engine.select`, later `cadgen.select`). `source` is a path
(`.xbf`, `.step`), the tree `read_xbf`/`read_step` returned, or a live shape in a body. It returns build123d shapes in world
coordinates, one per ref (a part's Solid/Compound, an assembly's Compound, a Face/Edge/Vertex), each with its canonical ref as
`label`; `kind="face"` (edge, vertex) returns every such element under the ref, each labelled (was `entities(kind)`); a
function, so the readers stay plain build123d. **Mapping:** `read_xbf`'s tree is the occurrence tree (an assembly a
`Compound`, children in component order); each leaf carries `{kind: {ordinal: name}}` (`kernel/imported.ELEMENT_NAMES`) by the
product shape's `TopExp.MapShapes` ordinals, the ones `faceNames` and XBM2 use. `select` goes owner → node, name → ordinal →
`MapShapes(leaf.wrapped).FindKey`, moved by `leaf.parent.global_location` (probed: a leaf's shape is in its parent's frame; a
wheel placed at z=110 has its centre at z=10). Structure paths are not unique (`kit2/axle/wheel` twice; `export --part`
silently takes the first, `engine/export/__init__.py:336-339`), so `--part`, `cadgen where DOC LINE` and the copy move onto
this grammar. On a live shape, names come from the run's trace (`run.account`); probed, they equal the document's.

**Names across edits (latest build only, decided).** Probed on a plate whose hole is cut from a box:

| edit | hole wall `f:plate.relief_make_cylinder1.1;M:plate.relief` | top face `f:plate.body.6;M:plate.relief` |
|---|---|---|
| hole moved (0,0)→(20,5), r 5→8 | its moved self: centroid (20,5,0), 301.6 mm² | resolves |
| round hole made square | fails; the cut now makes `f:plate.relief_make_box1.1`…`.4;M:plate.relief` | resolves |
| hole deleted | fails | fails: the face is now `f:plate.body.6` |
| boss fused on top | resolves | fails: now `f:plate.body.6;M:plate.relief;M:plate.boss` |

Moved or resized: its moved self. Deleted: fails, naming it. A survivor whose making changed (a feature starts or stops
touching it) is renamed and fails too; the error lists the names its atom has now (`f:plate.body.6`), never picks one. **A
model reading its own output** is refused at the read: any file a model of the run writes (its own, its children's, its
siblings'). Allowed, the file sits in `inputs` while the build rewrites it, so the key moves every build and no restart hits;
without a digest it would depend on its own past. A picked element comes from the live shape instead:
```python
kit = read_xbf("XBF/kit.xbf")                               # checks/clearance.py: the latest build
rim = select(kit, "wheel_2/f:wheel.rim.1")[0]               # a Face, world coordinates
@xbf                                                        # the user picked #base/f:plate.body.6;M:plate.relief
def bracket():
    base = plate()
    top = select(base, "f:plate.body.6;M:plate.relief")[0]  # a live shape: this run's names
    return base + bd.Pos(top.center()) * bd.Cylinder(4, 10)
```
Commands: `cadgen where XBF/kit.xbf "/abs/XBF/kit.xbf#wheel_2/f:wheel.rim.1"`; `cadgen export XBF/kit.xbf w.step --part
wheel_2`. **Cost:** `engine/refs.py` ~200 lines, cadRefs.js +40, xbfChips.jsx copies aliases and `oN.N`. Motorbike (2.4 MB, 46
leaves): `read_xbf` 0.21 s, a face by name 0.33 ms; memo-keyed in a build. **Open:** aliases renumber when a same-label
occurrence is inserted earlier (main's rule); keep them, or allow `/` in owners (paths; a grammar change in both)?

## 2. DXF on the engine (B5)
**Old.** `@dxf(out=)` (`authoring.py:653`) builds through the old daemon (:896) or cold. The body returns 2D build123d
geometry: a bare Sketch/Face/Wire/Edge is layer `CUT`, a `{layer: shape}` dict or a compound of labelled children a layer each
(`_internal/dxf_emit.py:66, 182-208`). `_internal/generation_runner.py:412-438` writes it: `emit_dxf` (build123d `ExportDXF`,
mm, edges sorted by content, ezdxf provenance pinned), `drawing_checks` (units, zero-length, duplicates, open cut profiles,
empty), an atomic write, a store record. The viewer's `/__cad/drawing` (`viewer/drawings.py:49`) caches `drawing_payload`
(ezdxf) in the store. Skill: `python X.py`, `cadgen dxf snapshot`, `cadgen store why`, `--force`. 8 model scripts; flat
patterns are author code (`flatten.flat_pattern(part(), …)`). **Design.** `@dxf(out=None)` is a derived-file decorator like
`@step`: `export.translate(document, "dxf", path)` of a planar document. A bare shape is one part (layer `CUT`); a Compound of
labelled children a part per layer, named by its label; the dict form goes; an edge off z=0 fails the build by name. `cadgen
export flat.xbf flat.dxf` writes the same bytes; a 3D document is refused ("tom.xbf is 3D: a DXF is drawn from 2D geometry a
model returns"). The body runs in the warm worker, memo-keyed; the file is rewritten only when its bytes change; no record, no
cache. A model it calls is a child in the same run, and its declared files are written too. `dxf_emit` and `drawing_checks`
move to `engine/export/dxf.py`. The viewer's route keeps reading the file (one path for every DXF; a vendor DXF has no
document), its cache an in-memory LRU of 8. Skill: `python X.py`, `cadgen snapshot X.dxf X.png`, `cadgen export part.xbf
part.dxf`; `store why` → the report's `rebuilt:` line; `--force` → `--verify`.
```python
@dxf                                                       # gasket.dxf beside the script, layer CUT
def gasket():
    with bd.BuildSketch() as cut:
        bd.Rectangle(60, 40)
        with bd.GridLocations(50, 30, 2, 2):
            bd.Circle(HOLE_D / 2, mode=bd.Mode.SUBTRACT)
    return cut.sketch
@dxf(out="../DXF/bracket_flat.dxf")                        # bracket: an @xbf model, a child here; its files written too
def bracket_flat():
    cut, bend = flat_pattern(bracket(), coordinate=3.0, kerf=0.15), bend_lines(bracket())
    cut.label, bend.label = "CUT", "BEND"
    return bd.Compound(children=[cut, bend])               # two layers
```
Commands: `python gasket.py`; `cadgen snapshot gasket.dxf gasket.png`. **Cost:** ~900 lines move into the engine (`dxf_emit`
433, `drawing_checks` 476); the old `@dxf` path and `store/drawings.py` go. Measured: main, no daemon (scratch store): no-op
0.74 s, edit 3.4 s (the old warm daemon not run); engine warm worker (a tiny model): no-op 0.19 s, edit 0.24 s, first build
3.97 s; gasket emit 10 ms, checks 4 ms; viewer payload 49 ms, then 4 ms. **Open:** law 3 names ezdxf (`import build123d`
already loads it), or the checks stay outside the engine?

## 3. Every snapshot kind drawn by OCCT (B6, widened)
**Old** (Playwright pages, all deleted with Chromium). Mesh files (`mesh_snapshot_verb`, `_internal/snapshot_door.py:454`):
`solid` (default) or `parts`, `grid`, `preview`, light/dark, camera preset/`az:el`/JSON, projection and focal length, clip (in
the file's unit: GLB metres, 3MF's declared unit, STL numbers), `exploded` (a GLB of several parts), annotation, size
profiles; other modes refused; part colour `#b6c4ce` and the Parts palette (`common/displaySettings.js`), edges at 16°
(`common/cadScene.js:68`). Robots (:536): the page parsed and posed them (`lib/urdf/`: parseUrdf.js 576 lines, kinematics.js
634, fourBarCoupling.js), degrees, link meshes STL/3MF/GLB (never DAE), a metre grid. DXF (:363): `headlessDrawingRender.js`,
theme ink on the backdrop, plan view only. **Kernel-free parsers cadgen has:** `urdf_source.py`, `sdf_source.py` (stdlib ET;
joints with type, links, limits, mimic, but no origins, axes or visuals) and `drawing_payload.py` (ezdxf). None for meshes:
the engine only writes them (`export/glb.py`, `threemf.py`, `stl.py`). **Design.** One dispatcher by suffix in
`engine/snapshot.py`. Every kind becomes a structure plus XBM2 buffers, which the render path already draws, so modes, camera,
grid, clip, Preview, annotation and size are the documents' own. Mesh buffers come from a new `mesh.buffer(positions, indices,
edge_angle=16)`: one face, plus the viewer's feature edges.

| kind | drawn as |
|---|---|
| STL | numpy reader (binary/ASCII): one part in `#b6c4ce`, numbers as mm |
| 3MF | stdlib zip+XML of `3D/3dmodel.model`: objects, components, build items, base materials, colour groups, pbmetallic, `unit` |
| GLB | stdlib+numpy: node tree (names, TRS/matrix) as instances, triangles, materials 1:1 to `Material` looks (incl. the KHR extensions the engine writes); rest pose, no playback; textures as their factor, warned |
| URDF/SDF/SRDF | cadgen parses visuals and joint origins/axes (stdlib ET); FK in numpy, ported from kinematics.js + fourBarCoupling.js and held to a golden; `--pose` replaces `--joint-values`; SRDF group states are poses |
| DXF | the payload in an orthographic top view: 1 px unlit lines in their colour or the theme ink, unlit fills, text in the plane |

Examples: `cadgen snapshot motor.glb m.png --display parts --grid`; `cadgen snapshot arm.urdf a.png --pose '{"elbow": 45}'
--camera iso`; `cadgen snapshot gasket.dxf g.png --display '{"appearance": "dark"}'`. **Cost:** ~1,400 lines (GLB 250, 3MF
150, STL 60, buffers 80, robot scene + FK 550, DXF 250, dispatcher 60); stills measured at 2.4 s for the first (GL context),
0.10–0.33 s warm through the daemon. **The deletion blocks on this and on B16:** every still needs GL on Linux (Xvfb/EGL in
CI) and Windows (a hidden HWND; CI runners likely offer only GDI GL 1.1). **Open:** mesh and robot stills use the engine's
look (`render/look.py`): do the viewer's mesh tabs read `GET /api/look` for parity, or does OCCT get a theme look? GLB
textures in v1? OBJ/DAE links?

## 4. Progress (B10)
**Old.** `cadgen.progress.report(detail)`, `track(items, label=)` (`progress.py`), no-ops outside a build, feed
`coordination.BuildRun` (`ProgressEvent`: phase, label, done, total, determinate, detail, elapsedMs; details ≥ 100 ms apart,
`coordination/phases.py`). The CLI drew a bar on a TTY only (`cli_progress.py:30-39`); the daemon's job ledger
(`daemon/jobs.py`) fed `daemon status --json` and the viewer, which polled it every 0.25 s (`viewer/build_progress.py`).
**Design.** The author's API is unchanged: `from cadgen.engine import report, track` (later `cadgen`), same signatures, no-ops
outside a build. On its existing pipe the worker sends `{"id", "progress": {"model", "outputs": [paths], "phase":
"body"|"names"|"document"|"write", "label", "detail", "done", "total", "determinate", "elapsedMs"}}` per engine stage,
`report` and `track` item (details ≥ 100 ms apart). `Worker._reader` keeps the last frame, relays it to the caller as it does
`log` lines, and wakes the `/version` waiters of a served document whose path is in `outputs`. The CLI draws main's bar on
stderr when it is a TTY, nothing otherwise; the `--json` report gains `progress` (the last frame, so a failed build says where
it was); `cadgen status` shows `building tom.py::tom · Building geometry · stages 3/7 · 41s`. The viewer's
`/version?after=V&wait=S` long poll answers `{version, patch, building, failed}` when either changes; `useXbfScene.js` already
holds `progress` in this shape, so nothing polls.
```python
@xbf
def hand():
    fingers = [finger(name) for name in track(FINGERS)]  # "Building geometry  2/5  middle"
    report("palm loft")                                   # "Building geometry  palm loft"
    return bd.Compound(children=[palm(), *fingers], label="hand")
```
Commands: `python hand.py` (the bar); `cadgen status` from another shell. **Cost:** ~150 lines; one JSON line per event.

## 5. Telemetry (B14)
**Old.** Client `analytics.py`: `SCHEMA = 5` (:147); ten events, tool to health (:230-241), exceptions with frames (user
frames `<user>`, :315); `DO_NOT_TRACK`/`CADGEN_TELEMETRY` (:392); a flush every 300 s, no network on exit, a spool. Daemon
recorder `daemon/telemetry.py` (process `daemon`). Counted at `daemon/server.py:412` (a build ends), `:433, :556` (a worker
died), `:509` (exit-frame crashes), `:614, :841` (daemon errors), `:769` (commands' counts); `daemon/client.py:811-840`
(`hand_over`, else the spool); `snapshot_cli.py:1864-1879` (snapshots, kinematics/animation features);
`eng_drawing.py:1477-1486` (drawing); `authoring.py:911-914`, `cli/__init__.py:385-387` (cold builds); `cli/__init__.py:338`,
`_internal/cli_from_function.py:441-447` (crashes); MCP `mcp/server.py:301, 337, 470, 774`; viewer `viewer/http_app.py:475,
709, 790-795`, `viewer/main.py:692-697`. Receiver `apps/api/src/events.mjs`: `KINDS` = file formats (:68), `READERS`
(:140-229), `SCHEMAS` 1–5 (:240-254); unknown is a 400. **Design** (counted outside the engine, law 3). `engine/api.py` gains
`observers`, a list it calls after each verb with a plain dict of facts; `cadgen/__init__` appends cadgen's recorder, as it
already installs the engine's font guard. Answers carry `worker.fresh`, `retired`, the daemon's health deltas (workers
started, crashed, retired since the last answer that carried them, so each counts once) and `error {type, frames, engine}`.

| engine | event |
|---|---|
| `python model.py` (via `script`), `cadgen build 'SOURCE'` (via `command`), `cadgen.build()` | `build` (kind `xbf`; `cached` = `cache.used`), `build_failure` |
| `cadgen export` / `cadgen snapshot` | `build` of the destination's kind (via `command`) / `snapshot`, `snapshot_failure` by source kind, `xbf` included |
| daemon facts | `health`: workers, crashes, recycles (retirements); refusals stay 0 |
| engine bugs; documents | `exception` (engine frames kept, user frames `<user>`); `feature`: assembly, declared_mesh, kinematics, animation, drawing |

`where`, `serve`, `status` count nothing; MCP and viewer sites stay; `FILE_KINDS` gains `.xbf`. **Sending:** commands spool
(unchanged) and the viewer's and MCP's 300 s flush drains it; with neither running, a command whose spool is over 300 s old
starts `python -P -m cadgen._internal.telemetry_send` detached (`CREATE_NO_WINDOW|CREATE_NEW_PROCESS_GROUP`), which sends and
exits: the old daemon's role; no verb waits on the network. **Receiver:** schema 6 = 5 plus kind `xbf` and process `cli`; 1–5
still read (standing rule); env switches unchanged, checked before anything is spooled. Examples: `python bracket.py` spools
`{"builds": [{"kind": "xbf", "via": "script", "ok": true, "seconds": 0.24}]}`; `cadgen snapshot arm.xbf a.png` with no GL
counts `snapshot_failure(xbf, render_error)`. **Cost:** ~200 lines in cadgen, 40 in the engine, 15 in the receiver;
`daemon/telemetry.py` (320) goes. **Open:** process `cli`, or reuse `daemon`? t2c-analytics needs `xbf`.

## 6. Worker policy (D3)
**Old.** Slots `CADGEN_JOBS` = cores, FIFO (`daemon/broker.py:49-56`); memory admission against 70% of RAM
(`CADGEN_MEMORY_MB`, `daemon/memory.py:121`) from a 512 MiB seed per worker (:23), waiting, then refusing after a 2 s stall
(`daemon/pool.py:688-757`); 2 prewarmed spares (`pool.py:57`), recycle after 1000 jobs (:58), unbind after 600 s idle (:59);
daemon idle hour (`daemon/server.py:53`); no RSS cap (the store held the state). **Engine today:** a worker per script
(`engine/daemon.py` `Daemon.worker`), no count cap, no idle retirement, retirement over the RSS cap after a build (max(RAM/4,
2 GiB), `CADGEN_ENGINE_WORKER_RSS_MB`); the daemon leaves after an idle hour. **Design (no budgets, no admission guesses).**
(1) `CADGEN_ENGINE_WORKERS` caps live workers, default `os.cpu_count()` (10 here). (2) A build for a script with no worker
starts one under the cap; at the cap the least recently used idle worker is retired first (stdin closed); every worker busy,
the build waits FIFO for the first free. Nothing is refused. (3) `CADGEN_ENGINE_WORKER_IDLE` (default 1800 s): the daemon's
`watch` loop retires a worker idle that long. (4) The RSS cap is unchanged; render processes keep theirs, take the idle
timeout, and do not count toward the cap. `cadgen status` (RSS as measured after each worker's last build):
```
engine daemon 98307, up 2h10m · workers 2/10 · idle timeout 30m · RSS cap 16.0 GB
  busy  tom.py         98310  12 builds  3.7 GB  Building geometry · stages 3/7 · 41s
  idle  motorbike.py   98410   3 builds  0.6 GB  4m
retired: finger_01.py (LRU) 3m ago · qdd_actuator.py (idle) 41m ago · tom.py (RSS) 2h ago
```
**113 scripts (tendon_hand).** Measured per small worker: 2.9 s kernel import, 0.46 GB RSS (0.29 GB private). An assembly that
calls its parts builds them all in its one worker, writing every part's declared files (decided). Run script by script (`for f
in src/*.py; do python "$f"; done`, or `xargs -P 10`), today's engine starts 113 workers and keeps them for the hour (~52 GB
RSS, 33 GB private; about 5.5 min of imports in a loop). Under the cap, 10 live at once (~4.6 GB plus memos), the rest wait
FIFO, the 10 last built stay warm, and any other part's next build is a restart (its xbf taken whole when unchanged, ~3 s;
else that part built cold). **A per-project worker is not worth it:** it saves the import and shares helper memos, but one
`sys.modules` (`load_script` imports by stem: two `parts.py` collide), a memo keeping what the last 4 builds used
(`RUNS_KEPT`, `engine/kernel/memo.py:154`: 113 scripts evict one another), one segfault (§7) or RSS retirement losing every
script's memo, and two scripts' builds in series. **Cost:** ~120 lines in `engine/daemon.py`. **Open:** idle 30 min, or the
daemon's hour? On tom an edit after retirement is a 146 s cold build; an unchanged restart 6.1 s.

## 7. Invisible reads (D5)
**Engine today.** Keyed: `read_step`/`read_xbf` (memo `wrap_function`), build123d's `import_step`/`import_brep`
(`_IMPORTER_TARGETS`, `engine/kernel/memo.py:284-287`), Python `open` (audit hook, :378), a text's font. Probed: `import_svg`
is heard (Python `open`), `import_brep` keyed; `import_stl` (C++ `RWStl`) and `Mesher().read` (lib3mf) are unseen: a model
sized from `cube.stl` kept a 40 mm box after the STL became 25 mm (`cache: {used: true, from: file}`). An `import_stl` face in
a result segfaults the build (`ShapeCustom.DirectFaces_s`, `engine/document.py:338`). **Design.** (1) `read_mesh(path) ->
Mesh` for `.stl`, `.3mf`, `.glb` (§3's readers): `vertices` (N×3, mm), `triangles` (M×3), `name`, `.bounding_box()` →
`BoundBox`; data, not a shape (a mesh is never a B-rep source); keyed like `read_step` (`_FILE_OPS`, closure input `["file",
path, digest]`). (2) `import_stl`, `import_svg`, `Mesher.read` join `_IMPORTER_TARGETS`, keyed by their path's bytes. (3) Raw
OCCT readers are wrapped at `memo.install()` as `_MAKER_TARGETS` are (`STEP/STEPCAF/IGESControl_Reader.ReadFile`,
`RWStl.ReadFile_s`, `BRepTools.Read_s`, `BinTools.Read_s` with a path, `RWMesh_CafReader.Perform`): a call marks the body
unkeyable (`retainable = keyed = False`), so it runs every build with a null closure key, and `retention.fallbacks` says
"STEPControl_Reader.ReadFile reads ../vendor.step past the engine: the body runs every build (read_step keys it)". (4)
`os.listdir`/`os.scandir` events key a folder by its sorted names, so a new file in a globbed folder re-runs the body. (5) A
surfaceless (mesh) face in a result is refused by name, not a crash. **What the old native tracer caught**
(`_internal/filetrace.py`: a C-library `open` interposer per platform, plus listdir/scandir events): every open by any C code,
so unlisted OCCT readers, C extensions (trimesh's), FreeType loading a font outside a text. After (2)–(4), only C readers
missing from the wrap list stay unseen, as the README says.
```python
@xbf
def enclosure():
    pcb = read_mesh("../scans/pcb.stl").bounding_box()   # keyed by its bytes
    return bd.Box(pcb.size.X + 6, pcb.size.Y + 6, pcb.size.Z + 4) - pcb_pocket(pcb)
```
Command: `python enclosure.py`; its report's `retention.reads` lists `../scans/pcb.stl`. **Cost:** ~150 lines; a read file is
hashed once per change.

## 8. Engineering drawings (`@eng_drawing`)
**Old.** `@eng_drawing(out="….pdf")` (`eng_drawing.py:1494`), in its own script, runs in-process (no daemon, no store); the
body returns Sheets, its part from `cadgen.read_step(…/STEP/part.step)` through the store's BREP cache (`step_scene.py:273`)
or a live shape. **Change: only the input.** The part is the engine's document: `read_xbf(path)` reads the model's last xbf
in-process, with names, colours and materials (no daemon, no store; motorbike 0.21 s); or, **stacked on the model**,
`read_xbf(model)` takes an `@xbf` `Model`, runs it through its warm worker when anything changed (writing its declared files),
then reads its xbf, so the PDF matches the code. The Sheet/View language, `out=`, PDF only, the checks, no CLI and no viewer:
unchanged. A vendor STEP still reads through `read_step`; convert it once (`cadgen export vendor.step vendor.xbf`).
```python
from cadgen.engine import read_xbf
from cadgen.eng_drawing import eng_drawing, Sheet
from bracket import bracket                         # the @xbf model beside this script
@eng_drawing(out="../PDF/bracket_drawing.pdf")
def bracket_drawing():
    part = read_xbf(bracket)                        # or read_xbf(Path(__file__).parent / "XBF/bracket.xbf")
    sheet = Sheet("A3", title="BRACKET", part_number="BRK-001", revision="A")
    top, front, right, iso = sheet.three_views(part, iso=True)
    top.hole((35, 25, 15), 3, depth=8, count=4)
    return sheet
```
Command: `python bracket_drawing.py`. **Cost:** ~15 lines (`read_xbf` takes a `Model`); the skill's step 1 rewritten.
**Open:** I read "stacked" as `read_xbf(model)`; `@eng_drawing` above `@xbf` would change the drawing language.

## 9. Couplings (B11, item 15)
**Old.** `couple(name, {dof: ratio}, limits=(0, 1))` (`kinematics.py:272`): a virtual DOF `x` sets each DOF to `ratio × x`;
five models on main (mars rover 7, planetary gear, hypercar doors' turn and travel, moonwatch 2, qdd); poses named it.
**Design.** `couple(assembly, name, *, range=(0, 1), **joints)`, shaped like `configuration`/`motion`. The control `c` moves
each joint from as built: `as_built + ratio × c` (a pair of several values takes a tuple, as in `configuration`); `c = 0` is
as built, so a coupling never moves the build. A control, not a constraint: each joint still poses alone. Its name poses
wherever a joint's does: `configuration(…, curl=0.5)`, `motion` waypoints and sweeps, `--pose '{"curl": 0.6}'`; configurations
and motions resolve it to pair values at document time, so AP242 states and the GLB bake are unchanged. Refused by name: at
pose time, a coupling with one of its joints, `c` outside `range`, a value past a pair's limits; at harvest, an unknown joint,
a joint a gear drives, a name that is also a joint's. **Stored** with the "as built" row's shape:
```
:6 "couplings"
  :c <name>   NamedData{entity: cadgen_coupling, lower_limit, upper_limit},
              ReferenceList[pair per term], RealArray[ratio per term], IntegerArray[value index per term]
```
AP242 has no virtual DOF, so the entity is the engine's own (like `cadgen_ease`). A gear pair cannot carry it: a gear couples
single values of the pairs moving two links, with no offset, so a door's turn and travel or a 0–1 control are out of reach;
foreign gear pairs stay honoured. The structure gains `kinematics.couplings: {name: {range, terms: [[pair, value index,
ratio]]}}`. **Viewer:** `XbfPositionSection.jsx` shows a slider per coupling under the Pose row (0 = as built); dragging it
poses every term through the page's FK (`xbfKinematics.js`), the pair sliders following; moving one pair after leaves the
coupling's slider where it was, as Pose keeps the preset it last chose.
```python
CURL = {"index_mcp_flexion": 90, "index_pip": 100, "index_dip": 70}   # degrees at a full curl
@xbf
def index_finger():
    base, proximal, middle, distal = phalanges()                     # four labelled parts
    for joint, (parent, child) in zip(CURL, [(base, proximal), (proximal, middle), (middle, distal)]):
        hinge = knuckle_axis(parent, child)                          # a bd.Axis at the knuckle
        bd.RevoluteJoint(joint, parent, axis=hinge, angular_range=(0, CURL[joint]))
        bd.RigidJoint(joint, child, joint_location=bd.Location(hinge.position))
        parent.joints[joint].connect_to(child.joints[joint], angle=0)  # as built: straight
    finger = bd.Compound(children=[base, proximal, middle, distal], label="index_finger")
    couple(finger, "index_curl", **CURL)                             # one control: 0 straight, 1 a fist
    return motion(finger, "grip", sweep={"index_curl": (0, 1)}, duration=1.5, loop="pingpong")
@xbf
def hypercar():
    chassis, doors = build_chassis(), {side: build_door(side) for side in ("left", "right")}
    for side, door in doors.items():
        hinge = bd.Axis(HELIX_ORIGIN[side], HELIX_DIR[side])         # the helical hinge
        bd.CylindricalJoint(f"door_{side}", chassis, axis=hinge,
                            angular_range=tuple(sorted((0, SWEEP[side]))), linear_range=(0, TRAVEL))
        bd.RigidJoint(f"door_{side}", door, joint_location=bd.Location(hinge.position))
        chassis.joints[f"door_{side}"].connect_to(door.joints[f"door_{side}"], angle=0, position=0)
    car = bd.Compound(children=[chassis, *doors.values()], label="hypercar")
    couple(car, "doors", door_left=(SWEEP["left"], TRAVEL), door_right=(SWEEP["right"], TRAVEL))
    return configuration(car, "open", doors=1)                       # shut is as built
```
Commands: `python index_finger.py`, then `cadgen snapshot XBF/index_finger.xbf c.png --pose '{"index_curl": 0.6}'` (MCP 54°,
PIP 60°, DIP 42°); `cadgen export XBF/hypercar.xbf half.step --pose '{"doors": 0.5}'`. **Cost:** ~180 lines in `mechanism.py`
and ~60 of JS; main's 12 calls port mechanically (values become relative to as built, the same where a model is built at
zero). **Open:** should a coupling optionally lock its joints (their sliders hidden) for gear trains (moonwatch, planetary)?
