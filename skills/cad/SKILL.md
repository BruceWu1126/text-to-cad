---
name: cad
description: Create/edit parametric CAD models as Python scripts built into .xbf documents, export STEP/STL/3MF/GLB files, organize CAD projects, resolve prompt references, and measure geometry with cadgen. Open and visually review existing XBF, STEP/STP, STL, 3MF and GLB files in CAD Viewer.
license: MIT
---

# CAD modeling and inspection

Provenance: maintained in [earthtojake/text-to-cad](https://github.com/earthtojake/text-to-cad).
Use the installed local skill files for the current interface.

## Start with the task

| Task | Start with | Reference |
| --- | --- | --- |
| **Create or edit a part or assembly** | The project's model script, or a new one (below). | [Model contract](references/model-contract.md), [build123d](references/build123d-modeling.md), [positioning](references/positioning.md) |
| **Organize a CAD project** | The project's existing layout. | [Project layout](references/project-layout.md), [starters](references/project-template.md) |
| **Export STEP, STL, 3MF or GLB** | A decorator on the model, or `cadgen export` on its `.xbf`. | [Exports](references/supported-exports.md) |
| **Use a vendor STEP in a model** | `cadgen export vendor.step vendor.xbf` once, then `read_xbf` in the model. | [Inputs](references/model-contract.md#inputs) |
| **Resolve a reference from a prompt** | `cadgen inspect <document> '<ref>'` (below). | [References](references/inspection-and-validation.md#references) |
| **Find the code that made a face or an edge** | `cadgen inspect <document> <ref>`. | [Which code made it](references/inspection-and-validation.md#which-code-made-it) |
| **Measure or check geometry** | A Python check: `read_xbf` and `cadgen.geometry`. | [Inspection](references/inspection-and-validation.md) |
| **See a model or a pose** | `cadgen snapshot`. | [Snapshots](references/snapshot-review.md) |
| **Articulate a model** | build123d joints in the body, then `cadgen.ap242`'s `mechanism_state`, `gear_pair` / `rack_and_pinion_pair` / `screw_pair` and `configuration_sequence`. | [Kinematics](references/kinematics.md) |
| **Explode, reveal or showcase a model in a GLB** | `cadgen.gltf`'s `animation` in the body, with `@glb` stacked on `@xbf`. | [Animation](references/animation.md) |
| **Open an existing XBF, STEP/STP, STL, 3MF or GLB** | Show it to the user. | [Show the model](#show-the-model) |
| **Source or commands written for an older cadgen fail** | Their migration. | [Migrations](references/migrations.md) |

For 2D DXF drawings use `$dxf`; this skill owns any 3D part the drawing projects.
Use the corresponding robot-description skill for URDF, SRDF or SDF.

## Setup and paths

Run cadgen through [uv](https://docs.astral.sh/uv/), so this skill's commands share
one installation, and its engine server, with the CAD app's:

- `cadgen` below means `uvx --no-config --managed-python --python 3.13 --from cadgen==0.8.0 cadgen`
- `python` below means `uvx --no-config --managed-python --python 3.13 --from cadgen==0.8.0 python`

`cadgen doctor <skill-dir>` reports the installation in use, whether it is the one
this skill pins, whether the CAD kernel loads, and whether the engine server is
running. Each command's `--help` lists its flags.

Every build runs in the engine server, which the first build starts: one warm
worker per model script, kept between runs. `cadgen status` shows the workers and
what each is building; `cadgen stop` stops the daemon. Every build and every verb
goes through the engine.

Command-line paths, and the paths `read_xbf` and `read_step` take, are
relative to the working directory, as are a model's own file reads unless anchored
on `__file__`. Decorator `out=` paths are **relative to the model script**.

## Create or edit a model

A model is a parameterless function that returns a build123d shape, decorated with
`@xbf`: running its script builds it into an `.xbf`, cadgen's own document of the
model (its parts and placements, names, colours, materials and mechanism). For
example, `src/bracket.py`:

```python
from cadgen.engine import step, xbf
import build123d as bd

WIDTH = 40.0


@xbf(out="../XBF/bracket.xbf")
@step(out="../STEP/bracket.step")
def bracket():
    body = bd.Box(WIDTH, 20, 6)
    bore = bd.Cylinder(4, 6)
    body = body - bore
    body.label = "bracket"
    return body


if __name__ == "__main__":
    bracket()
```

```bash
python src/bracket.py
```

```text
built src/bracket.py::bracket cold in 2.33s: bodies 1 run; 6 misses, 0 hits; body 0.01s, ...
  XBF/bracket.xbf (written, 0.0 MB)
  STEP/bracket.step (written, 0.0 MB)
```

- **Decoration only declares.** Imports and definitions build nothing. The script's
  explicit model calls build their models and write their declared outputs; Python
  controls which calls run. Keep calls under `if __name__ == "__main__":`.
  cadgen consumes no script arguments, and never exits the caller at import time.
- **`import build123d as bd`, and use it attribute-style** (`bd.Box`). cadgen makes the
  import lazy: it binds at once and the kernel (~2.5 s) loads on the first attribute read,
  in the worker that builds the model, so a run the engine finds current never loads it.
  `from build123d import Box` reads the name at once and loads the kernel there, as does a
  module-level kernel value (a `bd.Align.CENTER` default, a `bd.Color` constant).
- `@model` declares a document without a file. Every file decorator implies a model.
- `@step`, `@glb`, `@stl`, `@threemf` and `@dxf` declare translations of the
  document; `@xbf` explicitly saves that document. Every file decorator requires `out=` (or a positional output path) and takes nothing else:
  materials, colours, joints, mechanism states and configuration sequences are written in the body. A
  model without `@xbf` writes its other declared files and keeps its document in memory.
- The script's worker keeps every kernel operation it ran, keyed by its inputs, so an
  edit runs what it reaches and replays the rest, and an unchanged model rewrites
  nothing (`kept`). After the daemon restarts, a model whose `.xbf` was built from
  the code and inputs it has now is taken from that file and nothing runs.
- An element is named by the function and the variable that made it, never by a
  line: `f:bracket.bore.1;M:bracket.body_dnjoch` is face 1 of `bore`, modified by
  the cut whose result `body` took. Moving code renames nothing, and a dimension
  edit keeps every name.
- An assembly calls its child models in its body and places their results. Each
  child builds in the same run and writes its own declared files. Running a child's
  script alone leaves the assemblies that use it as they were: run the assembly.
- A variant is a second model function: a model takes no parameters.
- `@memo` marks a function a later build may replay instead of running. The memo
  already keeps every kernel call, so mark only a stage holding seconds of kernel
  work that an edit elsewhere leaves alone, such as a sub-assembly; never a small
  part or a helper called many times
  ([memo functions](references/model-contract.md#memo-functions)).
- The files a body reads are inputs, by their bytes: `read_xbf`, `read_step`,
  `read_mesh`, and every file its Python opens (`open`, `json.load`, `np.load`). A
  file read past them (build123d's `import_step` or `import_stl`, an OCCT reader)
  makes the body run on every build. The clock, environment variables and
  randomness are not seen. A model never reads a file its own build writes.
- A vendor STEP is converted once and read as an xbf, which is many times faster than
  parsing the STEP: `cadgen export vendor.step vendor.xbf`, then `read_xbf` in the
  model. `$step-parts` finds real vendor models of purchasable parts such as
  fasteners, bearings and servos.
- Model units are millimetres, +Z up.
- An image without a scale sets no fit dimension, and a drawing's units, projection
  and revision decide how its numbers read. A missing or conflicting dimension that
  changes the result is the person's to settle; a value chosen in its place is an
  assumption.

## Exports

STEP, GLB, STL and 3MF files are translations of the `.xbf`, written by one writer:
a decorator for a file every build keeps current, `cadgen export` for one written
once from a document (an `.xbf` or a `.step`).

```bash
cadgen export XBF/bracket.xbf STL/bracket.stl GLB/bracket.glb 3MF/bracket.3mf
```

An export writes the document as its code built it, and takes no option that picks a
part or a state. A file of one part is that part's own model; a posed file is a model
returning the assembly posed (`posed(arm(), "folded")`,
[kinematics](references/kinematics.md#posed-files)).

A GLB carries each of the model's motions as an animation clip: animation exists
only there. Meshes are made at one tolerance everywhere, FreeCAD's default. See
[exports](references/supported-exports.md).

## Prompt references and inspection

A reference such as `/work/robot/XBF/arm.xbf#arm/fore/f:arm.fore.6` names geometry
in a document by the engine's own names: the file, `#`, a part or an assembly by
the labels on its way from the root (`hand/finger.2` is the second component
labelled `finger` in `hand`; a path may start at any level where it names one
occurrence), then, after a slash, one element by its name (`f:`, `e:` or `v:`) or
its number (`face 7`). The CAD viewer copies them for an `.xbf` or a STEP. Resolve
one with `cadgen inspect`:

```bash
cadgen inspect /work/robot/XBF/arm.xbf "fore/f:arm.fore.6"
```

It answers in JSON what the ref names, placed where its occurrence stands: an
element's canonical `ref`, `kind`, `ordinal` and `name`, the kernel's facts (a face's
`area`, `centroid`, `surface` and exact `box`; an edge's length and endpoints), and
`made_by`, the function, file and line of the code that made it; a part's or an
assembly's volume and mass. `cadgen.inspect(document, ref)` returns the same from
Python. Edit the code `made_by` names, or pick the element in the model's body with
build123d's selectors (`body.faces().sort_by(bd.Axis.Z)[-1]`), matching those facts.
A ref is resolved in the document as the latest build left it; one that names
nothing, or several things where it names one, is refused with what it could mean.
The [inspection reference](references/inspection-and-validation.md) has the grammar.

A Quick Edit prompt names its document under `File:`, selected entities under
`References:`, and a snapshot packet as inline JSON or a `.snapshot.json` path.
Save inline JSON to a temporary `.snapshot.json`, and draw the view the person
drew their ink in, ink included:

```bash
cadgen snapshot XBF/arm.xbf tmp/quick_edit.png --annotation tmp/arm.snapshot.json --camera authored
```

Inspect supplied annotations before editing. Without annotations, the captured
view is optional context; render it when useful to the request. After editing,
draw it again to check the change. See
[annotations and Quick Edit views](references/snapshot-review.md#annotations-and-quick-edit-views)
for the packet.

A reference into a file that is not a document names what the viewer picked in it,
in that file's own terms (none is a ref `cadgen inspect` resolves):

| File | Reference | Means |
| --- | --- | --- |
| URDF, SRDF, SDF | `/work/arm/arm.urdf#upper_arm` | the link `upper_arm` |
| GLB | `/work/out/model.glb#n12` | the glTF node `nodes[12]` (0-based, the file's own index) |
| 3MF | `/work/out/plate.3mf#o2.1` | the 2nd `<build><item>`'s 1st `<component>` (1-based); `#o3` is the 3rd build item |
| STL | the file alone, on `File:` | one mesh: nothing in it is picked |

## Checks and diagnostics

A check is a Python script that reads the document the latest build wrote, so it
tests what was written:

```python
from cadgen.engine import read_xbf
from cadgen.geometry import closest_points

kit = read_xbf("XBF/assembly.xbf")
parts = {part.label: part for part in kit.leaves}   # every placed part, world coordinates
plate, post = parts["plate"], parts["standoff_left"]
print(plate.bounding_box().size)              # the envelope: not a hole's diameter or a wall's thickness
print(closest_points(plate, post).distance)   # 0: the standoff sits on the plate
```

- A snapshot draws a document as the viewer does
  (`cadgen snapshot XBF/bracket.xbf tmp/review.png`). It shows arrangement and
  appearance, not dimensions.
- Measuring the document is what shows a request's dimensions and clearances. In an
  assembly, nothing interferes when `overlap_volume` is 0 for every pair of solids
  apart from fits named on purpose, and a part meant to rest on another touches it
  when their `closest_points` distance is 0. Both describe the parts as built, not a
  pose ([interference and contact](references/inspection-and-validation.md#measurements)).
- `cadgen inspect XBF/assembly.xbf assembly` weighs an assembly: each part's volume
  times its material's density, and the parts that have no material.
- A check that fails or cannot decide has shown nothing about the design.
- A retry that leaves out or shrinks a specified feature (a fillet skipped, a
  radius reduced) builds a different design from the one asked for.
- The reply is the person's record of the work: the files written, the checks run
  and what they showed, the requirements no check covered, and every assumption or
  departure from the request.
- Every build prints where its time went (`body`, element names, the document, each
  file) and why it built: `rebuilt: ../src/lib/arm_parts.py changed`. A body that
  runs on every build is named in `retention.fallbacks`, with why, in the report
  `bracket.build().models` returns from Python
  ([progress and the report](references/model-contract.md#progress-and-the-report)).
- `cadgen inspect XBF/bracket.xbf "bracket/f:bracket.bore.1;M:bracket.body_dnjoch"`
  names the function, file and line whose operation made the face a ref names
  ([which code made it](references/inspection-and-validation.md#which-code-made-it)):
  where to edit when a face is wrong.

### Show the model

Show the user each file you create or change, and any they ask to see. Snapshots and
validation don't replace this.

- If your tools include `cad_show` (your host may prefix it), use it with the file's
  absolute path, and follow its description for when to call it again. `cad_view` reads
  what the user selected; `cad_screenshot` shows you what they see.
- Otherwise run the CAD Viewer, from any folder:

  ```bash
  cadgen viewer --host 127.0.0.1 --json FILE [FILE ...]
  ```

  The command ensures this installation's engine, registers each file and prints
  its page, `/<id>`, then exits. Read each file's `url` from `documents` in its one
  JSON line (never guess the port); the URL carries its token query. A model's page is the
  `view:` line `python SCRIPT.py` prints; a script is never a document.
  Return that link to the user. If it fails to launch, say so.

Build changed models first: the viewer never runs model scripts. Show the model's
`.xbf`; an open `.xbf` follows every later build of it. The viewer opens a STEP as
a document too, read by the engine. Exact selection and measurement need an `.xbf`
or a STEP; mesh viewers measure approximately, on triangles.
