# The model contract

Read this file when authoring or rebuilding a model script, composing models into
assemblies, deciding what a build runs, or reading files into a model.

## A model script is Python

A model is a parameterless function that returns a build123d shape, decorated with
`@model` or a file decorator such as `@xbf`. Calling it builds it:

```python
from cadgen.engine import glb, step, xbf
import build123d as bd

THICKNESS = 6.0


@xbf(out="../XBF/bracket.xbf")
@step(out="../STEP/bracket.step")
@glb(out="../GLB/bracket.glb")
def bracket():
    body = bd.Box(40, 20, THICKNESS)
    body.label = "bracket"
    return body


if __name__ == "__main__":
    bracket()
```

```bash
python src/bracket.py
```

- **Decoration only declares.** Imports and function definitions never build or
  exit the script. `python model.py` runs normal Python; each explicit model call
  builds that model and its children, writes its declared outputs, and returns.
  Keep executable calls under `if __name__ == "__main__":` so imports are safe.
  Watchers never change which calls execute. The engine consumes no script
  arguments; engine switches are environment variables ([the daemon](#the-daemon)).
- **A script is plain Python, loaded afresh by each build.** Its folder is first on
  the module search path, so a module beside it imports by its own name. A script
  inside a package (its folder holds an `__init__.py`) is that package's module, so
  its relative imports (`from ..lib.dims import WIDTH`) work under
  `python proj/parts/block.py` and `python -m proj.parts.block` alike. An edit to any
  module the script imports is read on the next build.
- **A model takes no parameters and returns a build123d shape**: a part, or a
  `Compound` of labelled children (an assembly). A drawing returns 2D geometry
  (`$dxf`). A variant is a second model function; values shared between models live
  in module constants (`THICKNESS = 6.0`) that the others import.
- **A body that raises fails its model**: the report shows the model's frames, the
  files its last good build wrote stay, and the script exits 1.

## Decorators declare models and files

- `@model` declares a model with no file output. File decorators also declare a model.

- `@xbf("XBF/part.xbf")` declares the native document. `@step("part.step")`,
  `@glb("part.glb")`, `@stl("part.stl")`, `@threemf("part.3mf")` and
  `@dxf("part.dxf")` declare translations; DXF requires planar geometry.
- **Every file decorator requires `out`**, its only argument, as a positional
  path or `out="..."`. Relative paths are relative to the script. `None` and omitted
  paths fail. Materials, colours, joints, configurations and motions belong in the body.
- **A declared file is written whenever its model runs in a build**, as the build's
  target or as a child another model calls, once per build, from that model's own
  document. The `.xbf` comes first (an open viewer shows it at once), then the files
  derived from it.
- **The same code writes the same bytes.** A document that did not change is not
  rewritten (`kept`), nor is a derived file the build left as it was; two builds of
  the same code are the same files on any machine, on any day.
- A model without `@xbf` writes its other files and no document; it builds and
  composes like any other.

## What a build runs

Each script has a warm worker in the daemon, and the worker keeps every kernel
operation its models ran, keyed by its inputs. A hit hands back the objects the
first run made; a miss makes everything downstream of it miss. So an edit costs
what it changed, and an element keeps the name its feature history gave it.

Above that, a body (a model's, or a `@memo` call's) is skipped, its last run
replayed, while what it is made of is unchanged:

- the code it reaches: the functions it calls, by their code (not their lines or
  docstrings), and the values it reads (module constants, defaults, data classes),
  through every first-party module it imports;
- every file it reads, by its bytes ([inputs](#inputs));
- the engine's own code and versions.

A comment, a blank line or a docstring re-runs nothing. An edit to a constant
re-runs the bodies that read it and the bodies that hold their results. A changed
input file re-runs the body that reads it. What the engine cannot see is yours to
keep out of a body: the clock, environment variables, randomness, a file only
tested for (`Path.exists`), a folder listed (`glob`, `os.listdir`), and state one
body leaves in a module for another. A body must be a function of its inputs.

After the daemon restarts (it exits after an idle hour), a model's cache is its own
`.xbf`: the document carries its build closure (the script and function, a digest
of each module the model reaches, each input file's digest, the engine and library
versions), and a build whose closure is unchanged takes the document whole and runs
nothing. `cadgen where XBF/bracket.xbf` prints that closure. Anything else builds,
and the report says why: `rebuilt: ../src/lib/arm_parts.py changed`.

A verifying build runs every body the engine would replay and compares each fresh
result with the logged one; from Python,
`python -c "import bracket; print(bracket.bracket.build(verify=True).models[0]['verify'])"`.
A body whose result differs is marked to run on every call for the worker's life.
`CADGEN_ENGINE_BODIES=0` turns replay off, and `CADGEN_ENGINE_MEMO=0` every memo hit.

## Children and assemblies

A model in another script is a child: import it and call it in the body.

```python
# src/assembly.py
from cadgen.engine import step, xbf
import build123d as bd

from plate import THICKNESS, plate
from standoff import HEIGHT, standoff

PITCH = 30.0


@xbf(out="../XBF/assembly.xbf")
@step(out="../STEP/assembly.step")
def assembly():
    base = plate()
    post = standoff()
    z = (THICKNESS + HEIGHT) / 2
    left = bd.Pos(-PITCH / 2, 0, z) * post
    right = bd.Pos(PITCH / 2, 0, z) * post
    left.label, right.label = "standoff_left", "standoff_right"
    return bd.Compound(children=[base, left, right], label="assembly")
```

- **A child builds in the same run**, in the caller's worker, and returns what its
  body returned, before the caller placed or labelled it. It writes the files it
  declares after its caller's: running the assembly writes `XBF/plate.xbf`,
  `XBF/standoff.xbf` and their exports too. A child called twice is one model,
  written once. Importing a model builds nothing.
- **Running a child's script alone builds the child** and leaves the assemblies
  that use it as they were: run the assembly to update them.
- **A placement shares its shape.** `Pos(...) * part`, `part.moved(...)` and
  `part.located(...)` make a new placement of the same geometry, so a part used ten
  times is one part placed ten times, in memory and in the document. A model that
  needs a B-rep of its own copies it with `BRepBuilderAPI_Copy`.
- **Label every placement**: the label names the occurrence in refs, the viewer's
  tree, the STEP's instances and the GLB's nodes. Labels that siblings share are
  numbered among them (`finger.2`).
- **A child's mechanism stays in its own document.** An assembly's joints are
  authored in the assembly's body, on the parts it places ([kinematics](kinematics.md)).
- **Outside a build a model is its function**: `bracket()` called from plain Python
  runs the body there and returns the shape, cold. A drawing script uses this
  (`$engineering-drawing`); for a big model, read its document with `read_xbf`
  instead.

## Memo functions

`@memo` marks a function the engine may replay instead of running. Called in a
build, a call is a body of its own, keyed by its code and its arguments; a later
build with the same key replays it, names and all. It writes no file.

```python
from cadgen.engine import memo, xbf
import build123d as bd


@memo
def frame(length):
    rails = [bd.Pos(0, y, 0) * bd.Box(length, 10, 10) for y in (-40, 40)]
    return bd.Compound(children=rails, label="frame")


@xbf("XBF/rig.xbf")
def rig():
    base = frame(200)
    ...
```

- **Mark rarely.** The memo already keeps every kernel call of any model, so a
  replay saves only the Python cost of those hits (0.1 to 0.4 ms each). Mark a
  function holding seconds of kernel work that an edit elsewhere leaves alone,
  roughly a thousand kernel calls or more: whole sub-assembly stages (measured: 1.3
  to 3 s each, twice as fast on an edit). Never mark per part, or a helper called
  many times: those measured under 0.1 s. A nested `@xbf` is never needed for
  caching.
- **Arguments by value**: numbers, strings, bools, `None`, tuples, lists and dicts
  of them, enums and paths; build123d's `Location`, `Vector`, `Axis`, `Plane`,
  `Color` and `Rotation`; a `Material`; a shape this build made. A call given
  anything else runs every time, as does one returning anything but geometry (a
  shape, or a tuple, list or dict of shapes). Each is named in the report's
  `retention.fallbacks`.
- **No methods**, and **pure**: it reads only what its key holds and changes
  nothing outside itself. A global it sets for another function, or an argument it
  mutates, is not seen, and a replay leaves it undone.
- **Return an assigned name.** A `@memo` function names what its `return` statement
  makes in itself, so marking a helper that returns `body - holes` renames those
  elements once; one that returns `body` renames nothing.

## Inputs

What a body reads is an input of its build, by the file's bytes, and a changed file
re-runs the body that read it:

- **`read_xbf(path)` and `read_step(path)`** copy a document's geometry into the
  model as a build123d compound tree: each assembly a `Compound`, each part named,
  coloured and given its material as the file has them, each element keeping the
  name the file gives it. An `.xbf`'s joints, configurations and motions are not
  imported: a part is a part.
- **A vendor STEP is converted once**: `cadgen export vendor.step vendor.xbf` writes
  an xbf of it, which reads many times faster than parsing the STEP (seconds against
  most of a minute for a large assembly) and keeps its names. Keep it beside the
  STEP and read the xbf. The conversion is yours: the engine writes nothing beside an
  input.
- **`read_mesh(path)`** reads an `.stl` as data, never a shape: `vertices`,
  `triangles`, `name` and `bounding_box()`. A document holds B-rep alone, so a mesh
  is measured and built from, never placed in a model:

  ```python
  from pathlib import Path

  from cadgen.engine import read_mesh, xbf
  import build123d as bd

  HERE = Path(__file__).resolve().parent


  @xbf("XBF/enclosure.xbf")
  def enclosure():
      pcb = read_mesh(HERE / "scans/pcb.stl").bounding_box()   # an input, by the scan's bytes
      return bd.Box(pcb.size.X + 6, pcb.size.Y + 6, pcb.size.Z + 4)
  ```

- **Every file the body's Python opens** for reading is an input too: `open`,
  `json.load`, `Path.read_text`, `np.loadtxt`. A file opened only for writing is not.
- **A read past the engine runs the body on every build**: build123d's
  `import_step`, `import_brep` and `import_stl`, and OCCT's readers driven directly,
  read files in C++ where the engine cannot hear them. Use `read_step`, `read_xbf`
  or `read_mesh`.
- **A model never reads a file its own build writes**: its own `.xbf` or export, a
  child's or a sibling's. Such a read is refused. Select from the live shape instead
  ([references](inspection-and-validation.md#references)).

Anchor paths on the script, so a build reads the same file from any working folder:

```python
from pathlib import Path

from cadgen.engine import read_xbf, xbf
import build123d as bd

HERE = Path(__file__).resolve().parent


@xbf("XBF/rig.xbf")
def rig():
    motor = read_xbf(HERE / "../vendor/motor.xbf")
    motor.label = "motor"
    return bd.Compound(children=[bd.Box(120, 80, 6), bd.Pos(0, 0, 20) * motor], label="rig")
```

## Code: `cadgen build`

`cadgen build` builds one model from source text, for a quick shape or a probe. The
code assigns its final shape to `result`; output flags declare its destination. A
file flag writes its kind at the path given or its CLI default (`--xbf` is `XBF/<name>.xbf` in the working folder):

```bash
cadgen build 'import build123d as bd
result = bd.Box(10, 10, 10) - bd.Cylinder(3, 10)' --name cube --xbf --step out/cube.step
```

`--model NAME` explicitly declares an in-memory model. At least one of `--model`
or a file flag is required; a command with no output declaration fails before
executing its code. Building the same named model again updates `code::NAME`.
The name is required with `--model`, and is unique within this installation's
engine. A script model is addressed by its absolute path and function name,
`/path/parts.py::bracket`, so functions in different scripts do not clash.
`cadgen serve`, `snapshot`, `export`, and `where` accept these addresses.
`--model` keeps no files or restart cache; the viewer's In memory list disappears
when its workers retire or the engine restarts. `--model` cannot use `--no-daemon`.

```bash
cadgen build 'import build123d as bd; result = bd.Box(10, 20, 3)' --model bracket
cadgen snapshot code::bracket bracket.png
cadgen export code::bracket bracket.step
```

`-` reads the code from stdin; `--name` (default `model`) names it, its elements and
its warm worker; `--json` prints the report; `--verify` verifies its bodies. A
script is never built this way: a script builds by running it.

## Progress and the report

A build says where it is: on a terminal, `python model.py` draws a bar on stderr,
each finished stage a line. A long body can name its own progress; both calls do
nothing outside a build:

```python
from cadgen.engine import report, track, xbf


@xbf("XBF/hand.xbf")
def hand():
    fingers = [finger(name) for name in track(FINGERS)]   # "Building geometry  2/5  middle"
    report("palm loft")                                    # "Building geometry  palm loft"
    ...
```

Each model's report says how it built, then each file:

```text
built src/assembly.py::assembly warm in 0.17s: bodies 3 run; 11 misses, 0 hits; body 0.01s, ...
  rebuilt: ../src/plate.py changed
  XBF/assembly.xbf (written, 0.0 MB)
  src/plate.py::plate, a child
    XBF/plate.xbf (written, 0.0 MB)
```

`cold` is a fresh worker, `warm` one that built the script before; `bodies` counts
those run, replayed or loaded from the file, and `misses` and `hits` the kernel's
memo. `Model.build()` returns the same report as data, a dict per model: `memo`,
`bodies` (each `run`, `replayed` or `loaded`), `cache` (`used`, `from`, `reason`,
`changed`), `retention` (each body's `fallbacks`, the reasons it runs on every
build, and the files it `reads`), `timings`, `files` and `child_models`:

```bash
python -c "import bracket; print(bracket.bracket.build().models[0]['retention'])"
```

## The daemon

One daemon per installation, started by the first build and gone after an idle
hour. It keeps a warm worker per model script (and one per `cadgen build` name),
each with its own memo, and builds of different scripts run at once.

- `cadgen status` lists the workers: each one's script, builds, memory and what it
  is building now (`--json` as data). `cadgen stop` stops the daemon and its
  workers.
- At most `CADGEN_ENGINE_WORKERS` workers live (default: the machine's cores); at
  the cap the least recently used idle one is retired. A worker idle for
  `CADGEN_ENGINE_WORKER_IDLE` seconds, or over `CADGEN_ENGINE_WORKER_RSS_MB` of
  memory after a build (default: a quarter of the machine's, at least 2 GiB; `0` for
  no cap), is retired, and its script's next build starts cold.
- `CADGEN_ENGINE_IDLE` sets the daemon's idle time in seconds, and
  `CADGEN_ENGINE_DIR` where it keeps its socket and lock (one daemon per folder). A
  build asked with another `PYTHONPATH`, `PATH` or `VIRTUAL_ENV` replaces its
  script's worker.
- Nothing else is written: no cache folder, and no record of a build beside the
  `.xbf`.

A successful server-backed build reports its model address and localhost viewer URL,
including when it writes files. In Python, `cadgen.build(shape, model="part", step="part.step")`
accepts an already-built build123d shape; its document and exports use the same worker
writer. The operations that created that supplied shape ran in the caller and have
no worker memo or feature provenance. Use a decorated function for warm geometry replay.
