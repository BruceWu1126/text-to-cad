# cadgen

The published distribution: CAD models written as Python, built by cadgen's engine
(`cadgen.engine`) into live XCAF documents and the files those models declare (XBF, STEP, GLB, STL,
3MF, DXF), and the tools that show and check them. One PyPI package carrying the Python under
`src/cadgen/` and what it serves that is not Python under `src/cadgen/_runtime/`: the CAD
Viewer's client, the CAD app page, and a headless browser bundle of the viewer's scene, built
from their JavaScript packages and bundled in at build time (the JS *source* never ships).

```python
from cadgen import step, xbf
import build123d as bd

@xbf("XBF/arm.xbf")                      # XBF/arm.xbf beside the script
@step("arm.step")         # and its STEP, a translation of that document
def arm():
    return bd.Box(10, 20, 30)


if __name__ == "__main__":
    arm()
```

```bash
python arm.py                                 # builds it in the engine's warm worker
cadgen export XBF/arm.xbf arm.glb arm.stl     # more translations of the document it wrote
cadgen snapshot XBF/arm.xbf arm.png           # a still of it, drawn by OCCT
cadgen serve XBF/arm.xbf                      # the document on a local page
cadgen where XBF/arm.xbf                      # its build closure
```

**PURPOSE** — the engine and its command surface: models built in a warm worker whose memo
makes an edit cost what it changed, documents and their exports, kinematics and motions,
materials, references to geometry, snapshots drawn by OCCT; the CAD Viewer (`cadgen viewer`: a
launcher for a page on the installation’s engine, serving every CAD file by its absolute path); CAD beside an agent's chat (`cadgen mcp`: a stateless stdio peer of the engine’s
HTTP MCP endpoint, with tools and the app page owned by the engine); the URDF, SDF and SRDF validators; and
engineering drawings (`@eng_drawing`).

**MAY DEPEND ON** — the Python ecosystem it declares (OCP/build123d lazily, never at
`import cadgen`) and the bundled runtime. Never app code, never JavaScript source at runtime.

**DEPENDED ON BY** — every skill (as a pinned installed distribution). The CAD Viewer is not a
dependent but a part: the engine serves `cadgen.viewer`’s client, HTTP application and
every STEP and `.xbf` a page opens on one authenticated listener, without a relay.

## The rest of the package's documentation

This file holds the LAWS. The engine's own README holds the mechanisms they constrain; where
the two disagree, the engine's README is right.

| Document | What it is for | Go there when |
|---|---|---|
| [`src/cadgen/engine/README.md`](src/cadgen/engine/README.md) | The engine: models and decorators, the daemon and its workers, progress, the viewer and the snapshot, exports, kinematics, couplings and motions, materials, imports, placements, the mesh and its wire format, element names, references, body retention, memo functions and the build closure | changing anything a build, an export, a snapshot or a served document does |

## The design laws

These are LAWS, not conventions: a change that violates them is wrong even when it works.
Laws 1 to 13 and 19 to 21 are the engine's; 14 to 18 hold for the whole package.

1. **An xbf carries its build closure as pointers and digests** -- script and function,
   modules, data inputs, versions and the key; never source, never lines on elements, never
   input contents, nothing of git's; names remain the per-element tie to code (the engine's
   **Build closure**).
2. **Nothing to disk.** The engine writes the files a model declares -- the xbf and the exports
   its decorators name, nothing beside them -- and the operating system's coordination for its
   warm process: a socket and a lock in `tempfile.gettempdir()/cadgen-engine`. It writes no
   sidecar, record of a build, cache, index, memo, mesh or parsed document, and no derived file
   beside an input. A restart is cold, but for the xbf itself: a model whose xbf holds the
   closure the model has now is not run.
3. **Standalone.** `cadgen.engine` imports nothing of cadgen outside itself: OCP, build123d (and
   ezdxf, build123d's own DXF library), numpy, Pillow and the standard library only. cadgen
   reaches into it, never the other way: its telemetry is an observer the engine calls with
   plain facts (`engine.api.observers`).
4. **XCAF is the document; files are explicit.** A live model holds an XCAF document;
   `@xbf` or `--xbf` serializes it as native BinXCAF. It holds: units in millimetres,
   prototypes and instances with placements, a name on every part and instance, colours and
   materials per part and per painted face, element names on face, edge and vertex labels, and
   an assembly's mechanism. STEP, GLB, STL, 3MF and, of a planar document, DXF are translations
   of it; `cadgen export` writes any of them from the xbf alone.
5. **The memo is the only cache, and it lives in the worker.** Every kernel operation a model
   runs is keyed by its inputs. A hit hands back the objects the first run made, and a miss
   makes everything downstream of it miss. So an edit costs what it changed, and an element
   keeps the name its feature history gave it across edits. Across a restart, a model's only
   cache is its own xbf, taken whole or not at all.
6. **One document per model, only declared files.** Bare `@model` and code built
   with `--model NAME` keep their document in the worker's memory. File decorators
   imply a model and write only their named outputs. No implicit XBF, source file,
   sidecar, registry, cache or thumbnail is written for an in-memory model.
   Model addresses and the viewer's In memory list disappear when their workers
   retire or the engine restarts. Reusing a code model's name updates that address.
7. **Provenance is served live, never written.** What made an element -- the feature, its
   operation, the function, file and line of its call -- lives in the trace of the worker that
   built the document, and is asked of it while it lives (`made_by`, `cadgen where`). A document
   opened with no such worker has none to give, and says so (`made_by: null`).
8. **Poses and motion definitions, never frames.** The document carries kinematics -- the
   mechanism, its configurations and its motions -- as poses and definitions, never baked frames
   or clips. A GLB export is the one place a motion is baked, into a clip of the GLB; the engine
   plays nothing.
9. **Names are the durable tie to code.** An element's name is spelled from the function that
   made its feature and the author's name for that feature, never from a line or a file: moving
   code renames nothing, and the file holds no line to go stale.
10. **A file decorator requires an explicit output path.** `out` is its only argument;
    omitted paths and `None` are refused. Whenever the model it decorates runs in a
    build -- the build's target, or a child another model's body calls -- that file is written,
    once per build, from the model's own document. A model with no file decorator writes nothing.
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
    alone declares no output. The Python `cadgen.build()` function also accepts an already-built
    build123d shape, with the same output declarations. A script may call that function
    or its decorated models (law 11).
13. **The document's kinematics are AP242's, and cadgen stores no kinematic fact the
    standard cannot name.** Every entity and attribute of the mechanism subtree is ISO
    10303-105's as Part 21 writes it (edition 4's domain model's where Part 21 has none);
    only the root says the schema and the units. What the standard cannot say is not
    patched with an attribute of cadgen's own: it is dropped, or refused by name at document
    time (the engine's **Kinematics**; a conformance test walks the subtree and holds it).
14. **The package ships alone.** The installed distribution is the whole world: the Python, the
    bundled `_runtime/`, and this document. It works with the repository it was built from gone
    -- and its markdown must read that way, referring to nothing outside the package.
    *Pressure-test*: every sentence in the package's markdown must be true and actionable for
    someone who only ran `pip install cadgen`. Naming a bundled thing ("the JavaScript runtime
    bundled at build time") passes; a repository path to its source, a repository script, or a
    repository workflow does not.
15. **No backwards compatibility.** Hard cutovers only. A retired surface does not exist: no
    alias, no shim, and no error that recognizes its old spelling to explain what replaced it. A
    removed command, flag, argument, key, module or name gets what the current contract's
    ordinary validation says -- an unknown command, an unrecognized argument, an unknown key, an
    `ImportError`, an `AttributeError`.
    *Pressure-test*: use a removed spelling; the error must read exactly as it would for a name
    that never existed.
16. **Closed vocabularies.** Every declaration surface has a closed key/kind set. An unknown key
    is an error naming the supported set, never silently ignored.
17. **Loud failure or correct output, nothing between.** The cardinal sin is plausible-wrong
    output at exit 0. No silent fallbacks, no globs, no guessing; a failed write or render leaves
    NO file at the requested path.
18. **Nothing waits on the network.** Once installed, cadgen works offline: every command,
    build, snapshot, server and page. Its own requests are two: telemetry (`analytics.py`, usage
    counts under a random id, sent by default once a `cadgen` command has said so, and never
    after the person's no) and the daily version check (`updates.py`). Both run in the
    background and fail silently: no start, command, build, request or tool call waits on them.
    A command sends nothing itself: what it counted -- an engine verb's build, export or
    snapshot (`_internal/engine_telemetry.py`, cadgen's observer of the engine), a drawing, a
    crash -- is kept in a small file beside the settings (`analytics.spool`), which the CAD app's
    and the viewer's background sends take, and a command that finds it has waited more than a
    window, with no other process sending, starts a sender detached that sends it and exits
    (`_internal/telemetry_send.py`). A process that exits sends nothing either: its last batch is
    kept beside the settings, and the next process that sends sends it, in the background. One
    wait remains, bounded: `cadgen telemetry off`, which waits for the deletion it asks for.
    The pages load nothing from the internet: everything they show ships in the wheel.
    *Pressure-test*: with an installed cadgen and every request refused (a proxy on a closed
    port), and again with every request hanging, start a server, open a view, call each tool,
    and run a build and a snapshot: each works, and nothing waits on a request beyond the one
    wait above.

19. **Connected means warm.** A served document's outstanding version wait or recent
    sync protects the worker that built it from idle retirement. The engine outlives its
    clients; a page closing never stops a worker. Memory and worker-count caps still apply
    (the engine's **Architecture** and **The daemon and its workers**).

20. **One engine serves the installation.** A page never replaces or re-executes it. Page sessions and document reads share its
    authenticated HTTP listener. `serve` and `viewer` always start or reuse it, with no
    standalone serving mode. Launchers exit; clients closing never stop it. Kernel
    operations run in child processes, never in the engine's request threads. Each
    page session keeps its own root; each MCP session owns its views and resource. The
    stdio shim owns only connection metadata. Sidebar sharing stays in memory, and host
    quirks stay in the shim and page. See the engine's **Architecture**.

21. **Python functions own operations; decorators and CLIs expose them.** Code and
    supplied-shape builds use the same document/output writer as decorated models.
    `cadgen.build(shape, model="part", step="part.step")` transfers the shape and its
    metadata in memory to a worker; the server never decodes native geometry. The
    caller's already-executed operations have no worker memo or feature provenance.
    Every successful server-backed build reports its address and localhost viewer URL,
    including a build with file outputs. A cold `daemon=False` build has no served URL.

## The shape of the package

```
src/cadgen/
  __init__.py            # the root: the engine's surface (xbf, step, ..., select, report)
                         #   and its verbs as functions (build, export, snapshot, serve,
                         #   where, status, stop, daemon), every name loaded on first use;
                         #   the font guard and the kernel-import recorder, installed first
  engine/                # the engine: models, the daemon and its workers, documents,
                         #   exports, the mesh, references, the OCCT stills (its README)
  cli/                   # the `cadgen` command: a module per command, each a shell over
                         #   the function or parser that does the work
  viewer/                # engine HTTP application (engine_app), launcher (main), routes (http_app),
                         #   files by absolute path (backend), a file's catalog row
                         #   (scanner), the explorer's reads (folders), the model library
                         #   every CAD view shares (recents), a STEP's and an .xbf's serve
                         #   by the engine and in-process document reads (documents), a DXF's
                         #   payload (drawings), a robot's (robots), Quick Edit's packets
                         #   (prompt_files)
  mcp/                   # stateless stdio proxy (shim), engine sessions (http), tools and
                         #   launches (server), open views (views), the viewer routes
                         #   in-process (tunnel), roots, the page (ui), and the CAD Viewer
                         #   link for a host that renders no MCP Apps (browser)
  urdf.py, sdf.py,       # the robot validators: one public namespace per format, its
  srdf.py                #   `validate` verb, and the checks behind it (*_source.py,
                         #   *_validation.py, sdf_external.py, findings.py, xml_common.py)
  results.py             # what a `validate` verb returns: the JSON line protocol, typed
  robot_payload.py       # a robot description resolved for the viewer's page: its
                         #   articulation, its visuals in rest space, the facts a person
                         #   reads off it; held in memory
  drawing_payload.py     # a DXF flattened by ezdxf for the viewer's pane
  eng_drawing.py         # @eng_drawing: Sheets and Views over a part, one PDF
  flatten.py             # flat patterns: exact 2D geometry from a solid's planar faces
  geometry.py            # measurements of native geometry: closest points, overlap,
                         #   soundness, mass properties
  analytics.py           # telemetry: usage counts and crash reports, on by default once
                         #   a command has said so, never after a no
  settings.py            # the person's settings: settings.json in the state directory
  features.py,           # the CAD views' features a person can turn off, and the
  language.py            #   language they speak
  updates.py             # the daily version check, for a copy nothing else keeps current
  assets.py              # where the bundled runtime is
  build123d.py           # `from cadgen import build123d as bd`, the lazy re-export
  color.py               # sRGB helpers
  check_fonts.py         # `python -m cadgen.check_fonts`: the font that breaks
                         #   `import build123d`
  _internal/             # the launch command, the font guard, the kernel-import recorder,
                         #   the engine observer and the telemetry sender, the install
                         #   channel, atomic replace, file locks, the CLI derivation
  _runtime/              # BUILT: the CAD Viewer client, the CAD app page and the headless
                         #   browser bundle -- produced when the wheel is packaged, never
                         #   committed, never edited
```

Commands: `cadgen build`, `export`, `snapshot`, `serve`, `where`, `status`, `stop` and
`daemon` are the engine's verbs; `cadgen dxf snapshot`, `urdf snapshot` and `sdf snapshot` its
snapshot for those files alone; `cadgen urdf|sdf|srdf validate` the validators; `cadgen
doctor` the installation and a skill's pin; `cadgen viewer` the engine page launcher;
`cadgen mcp` the stateless proxy an agent host starts; and `cadgen telemetry` the person's answer. A
model builds by running its script: `python model.py`.

The viewer exposes `POST /__cad/clipboard` for explicit viewport PNG copies. It
uses the same host and custom-header POST gates as other mutation routes, limits
images to 20 MiB, and delegates native delivery to `viewer/clipboard.py`. It never
reads the clipboard. Unsupported desktop clipboard environments return an error.
