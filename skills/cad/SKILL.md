---
name: cad
description: Create/edit parametric CAD models, organize CAD projects, export STEP/STL/3MF/GLB files, resolve prompt references, and measure geometry with cadgen. Open and visually review existing STEP/STP, STL, 3MF and GLB files in CAD Viewer.
license: MIT
---

# CAD modeling and inspection

Provenance: maintained in [earthtojake/text-to-cad](https://github.com/earthtojake/text-to-cad).
Use the installed local skill files for the current interface.

## Start with the task

| Task | Start with | Reference |
| --- | --- | --- |
| **Create or edit a part or assembly** | The project's model script, or a new one (below). | [Model contract](references/step-generation.md), [build123d](references/build123d-modeling.md), [positioning](references/positioning.md) |
| **Organize a CAD project** | The project's existing layout. | [Project layout](references/project-layout.md), [starters](references/project-template.md) |
| **Export STL, 3MF or GLB** | A mesh decorator on the model, or a format's `build` command on a saved STEP. | [Mesh exports](references/supported-exports.md) |
| **Resolve a reference from a prompt** | `read_scene` on its document, then `scene.resolve(ref)` (below). | [Reference syntax](references/inspection-and-validation.md#reference-syntax) |
| **Find the code that made a part, a face or an edge** | `cadgen store where <file.step> <ref>` on the generated STEP. | [Which code made it](references/inspection-and-validation.md#which-code-made-it) |
| **Measure or check geometry** | A Python script using build123d and `cadgen.geometry`. | [Inspection](references/inspection-and-validation.md) |
| **See a model, a pose or a clip** | `cadgen step snapshot`, or a mesh format's `snapshot`. | [Snapshots](references/snapshot-review.md) |
| **Articulate or animate a model** | `kinematics=` or `animation=` on the model's decorator. | [Kinematics and animation](references/kinematics.md) |
| **Open an existing STEP/STP, STL, 3MF or GLB** | Show it to the user. | [Show the model](#show-the-model) |
| **A sidecar schema is refused, or source or commands written for another cadgen version fail** | Their migration: until a refused sidecar is migrated the model renders as a plain part, without kinematics, materials or animation. | [Migrations](references/migrations.md) |

For 2D DXF drawings use `$dxf`; this skill owns any 3D part the drawing projects.
Use the corresponding robot-description skill for URDF, SRDF or SDF.

## Setup and paths

Run cadgen through [uv](https://docs.astral.sh/uv/), so this skill's commands share
one installation, and its warm build daemon, with the CAD app's server:

- `cadgen` below means `uvx --no-config --managed-python --python 3.13 --from cadgen==0.8.0 cadgen`
- `python` below means `uvx --no-config --managed-python --python 3.13 --from cadgen==0.8.0 python`

`cadgen doctor <skill-dir>` reports the installation in use, whether it is the one
this skill pins, whether the CAD kernel loads, and whether the build daemon every
build runs on can start. Each command's `--help` lists its flags.

CLI paths and `read_scene`/`read_step` paths are relative to the working
directory, as are a model's own file reads unless anchored on `__file__`.
Decorator `out=` paths are **relative to the model script**.

## Create or edit a model

A model is a plain Python script whose parameterless decorated function returns
a build123d shape; running the script builds it. For example, `src/bracket.py`:

```python
from cadgen import build123d as bd
from cadgen import step

WIDTH = 40.0


@step(out="../STEP/bracket.step")
def bracket():
    body = bd.Box(WIDTH, 20, 6)
    body.label = "bracket"
    return body


if __name__ == "__main__":
    bracket()
```

```bash
python src/bracket.py
```

- A run rebuilds what changed and is a no-op otherwise. Format build and snapshot
  commands read saved files and never run source.
- Before a file leaves cadgen, run `cadgen export <file>` to finish its saved output without running source.
- The viewer, snapshots and mesh exports read model units as millimetres, +Z up.
- A configuration is a plain factory the model calls with its values; a second
  configuration is a second model. cadgen runs a model file's top level to check
  whether it is current, and so does every parent that imports it, so a top level
  of imports, constants and definitions keeps that check cheap. The lazy `bd`
  import above (with `from __future__ import annotations` when annotations name
  `bd` types) lets a current model finish without loading the CAD kernel.
- An assembly calls its child models in its body and places their results.
  `.moved()` and `Location * shape` copy the shape, and everything under it, on
  every call, so a pass that walks a finished assembly to re-place or rewrite it
  re-copies the whole tree on every build; changing geometry where it is defined
  (the part, its factory or its child model) does not. Rebuilding a child does not
  rebuild the assemblies that use it: run the parent. Refs, mates and clips can
  name parts by their labels.
- Rebuilds are per model: an unchanged model is skipped whole, a changed one runs
  whole, and any edit to a file makes every model in it stale. Child models in
  their own files keep a big assembly's iterations fast, and each child also runs
  on its own.
- `cadgen.read_step` reads a vendor STEP with its colours, warm from the store when
  it can. Every file a build opens is a tracked input, whatever opens it, and so
  is every environment variable its code reads; nothing is declared. A model that
  reads its own output is never current, and geometry that depends on time,
  randomness or the working directory is invisible to the cache.
- `$step-parts` finds real vendor models of purchasable parts such as fasteners,
  bearings and servos.
- A STEP's sidecar (`<name>.step.json`: kinematics, materials, animation
  keyframes) is a build output, rewritten by every run.
- An image without a scale sets no fit dimension, and a drawing's units, projection
  and revision decide how its numbers read. A missing or conflicting dimension that
  changes the result is the person's to settle; a value chosen in its place is an
  assumption.

## Mesh exports

Stack `@stl`, `@threemf` or `@glb` on a model for meshes every run maintains; a
model may declare meshes and no STEP. A one-off export from a saved STEP:

```bash
cadgen stl build STEP/bracket.step STL/bracket.stl
cadgen 3mf build STEP/bracket.step 3MF/bracket.3mf
cadgen glb build STEP/bracket.step GLB/bracket.glb
```

Omitting OUT writes a sibling file with the format's extension. See
[mesh exports](references/supported-exports.md) for tolerances and animated GLB.

## Prompt references and inspection

A reference such as `/work/robot/STEP/assembly.step#o:4b1f9e2a7c03/f:bracket.make_box1.6`
identifies geometry in a particular saved document: an occurrence by its token
(`o:` and twelve hexadecimal characters) and, after a slash, one of its faces,
edges or vertices by the element's name (`f:`, `e:`, `v:`, then how the model's
operations made it: the element an operation made, and a `;` step for each
operation that changed it since). Its
file part is the document's absolute path as the CAD viewer copied it, with the
file's real name and extension (in quotes when it holds a space or `#`). Open that
path with `read_scene`, and pass the whole reference to `resolve()`:

```python
from cadgen import read_scene

scene = read_scene("/work/robot/STEP/assembly.step")
selection = scene.resolve("/work/robot/STEP/assembly.step#o:4b1f9e2a7c03/f:bracket.make_box1.6")
face = selection.shape()  # owned native geometry, in document world coordinates
print(selection.ref, face.area)
```

A Quick Edit prompt names its document under `File:`, selected entities under
`References:`, and snapshot context as inline JSON or a `.snapshot.json` path.
Read `measurements` for endpoints in model units and displayed labels; these are
reference dimensions, not constraints automatically applied to geometry.
To render, save inline JSON to a temporary `.snapshot.json` and run
`cadgen snapshot --job <snapshot file>` (`--job` takes a file path).
Inspect supplied annotations before editing. Without annotations, the captured
view is optional context; render it when useful to the request. After editing,
replay relevant views to check the change. See
[annotations and Quick Edit views](references/snapshot-review.md#annotations-and-quick-edit-views)
for the packet schema and capture behavior.

A reference into a file that is not a STEP names what the viewer picked in it, in that
file's own terms (none is a STEP selector `read_scene` resolves):

| File | Reference | Means |
| --- | --- | --- |
| URDF, SRDF, SDF | `/work/arm/arm.urdf#upper_arm` | the link `upper_arm` |
| GLB | `/work/out/model.glb#n12` | the glTF node `nodes[12]` (0-based, the file's own index) |
| 3MF | `/work/out/plate.3mf#o2.1` | the 2nd `<build><item>`'s 1st `<component>` (1-based); `#o3` is the 3rd build item |
| STL | the file alone, on `File:` | one mesh: nothing in it is picked |

A measurement taken on a mesh (a GLB, a 3MF or a robot's links) is between two points of
its triangles, in the file's unit, and APPROXIMATE: its label starts with `≈` ("≈ 0.0425 m").
Never read a width across two facets of a tessellated curve as an exact diameter; measure
the source model for that.

A bare `#o:4b1f9e2a7c03/f:bracket.make_box1.6` belongs to the document the
conversation is about. A ref names geometry, never a position. An occurrence's
token comes from its place in the tree (its parent, its name, its placement). An
element's name is how the model's operations made it, as the CAD kernel reported
it. It starts at the element an operation made from nothing -- named by the
function that called it, the operation, how many times that function had called
it in making that part, and the element's number among those it made
(`f:bracket.make_box1.6`, the sixth face of the first box `bracket` made) -- and
adds a step for each operation that made it out of another:
`f:bracket.make_box1.6;M:bracket.cut1` once the first cut trimmed it,
`e:sk.make_rect1.2;Ge:part.extrude1` for the side an extrusion generated from a
profile edge. A long one is folded to its last operation and a digest
(`e:bracket.cut1~5d1e0c2f9a3b`); copy names whole. A label on a shape a later
operation makes something of names the shape's operation there (`f:boss.3`; a
part placed as it is keeps its own names, its label naming the occurrence). So a
ref keeps resolving to the same face through a rebuild with other dimensions,
code moved to other lines, a sibling inserted beside it, an edit to another part
and the STEP round trip; it changes when an operation starts or stops changing
that face, or an earlier call of the same operation in the same function is
added to or removed from that part's making. A face of an imported file is
named by its geometry instead (`f:9d2e8c1b5a70`, twelve hexadecimal
characters), and a ref in that form also finds the face with that geometry in a
rebuilt model. A part label stands in for an occurrence token
(`#housing/f:housing.make_cylinder1.1`). Names come from the CAD viewer's copy
button, or from a script: each entity of
`read_scene(path).resolve("#housing").entities("face")` carries its ref. There
is no inspect CLI: checks are Python scripts, and the
[inspection reference](references/inspection-and-validation.md) covers label
aliases, enumeration and measurement.

## Checks and diagnostics

`read_scene` and `read_step` read the saved document, so a check through them
tests what was written:

```python
from cadgen import read_scene

scene = read_scene("STEP/assembly.step")
for occurrence in scene.leaves():  # every placed part
    print(occurrence.ref, occurrence.label)
bracket = scene.resolve("#bracket").shape()  # exact geometry, world coordinates
print(bracket.bounding_box().size)  # the envelope: not a hole's diameter or a wall's thickness
print(bracket.volume, len(bracket.faces()))
```

- A mesh-only model has no STEP; called from plain Python, it returns its
  geometry.
- A snapshot renders a saved document as the viewer draws it
  (`cadgen step snapshot STEP/bracket.step tmp/review.png`; each mesh format has
  the same verb). It shows arrangement and appearance, not dimensions.
- Measuring the saved document is what shows a request's dimensions and
  clearances. In an assembly, nothing interferes when `overlap_volume` is 0 for
  every pair of solids apart from fits named on purpose, and a part meant to rest
  on another touches it when their `closest_points` distance is 0. Both describe
  the saved placement, not a pose or a clip
  ([interference and contact](references/inspection-and-validation.md#measurements)).
- A check that fails or cannot decide has shown nothing about the design.
- A retry that leaves out or shrinks a specified feature (a fillet skipped, a
  radius reduced) builds a different design from the one asked for.
- The reply is the person's record of the work: the files written, the checks run
  and what they showed, the requirements no check covered, and every assumption or
  departure from the request.
- Every build prints where its time went, split into model code and cadgen. A
  rise in model code comes from the model's newest code, and
  `python <model>.py --profile` shows where.
  `cadgen store why <model>.py` explains an unexpected rebuild or no-op, and
  `--force` rebuilds one model. More in the
  [model contract](references/step-generation.md#progress-and-runtime-diagnostics).
- `cadgen store where STEP/bracket.step "#bracket/f:bore.make_cylinder1.1;M:bore.cut1"` names the
  line of the model's code whose operation made the face a ref names and the
  one that last modified it, from the build's own account of the run
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
  cadgen viewer --host 127.0.0.1 --json --detach
  ```

  `--detach` returns once the viewer answers requests and leaves it running in the
  background: always pass it, since a foreground viewer never exits (and piping its
  output through `tail` can hide the URL for good). It starts this machine's one viewer,
  or reuses it. Read `url` from its one JSON line (never guess the port), and for each
  file return `url?file=<its URL-encoded absolute path>`. If it fails to launch, say so.

Generate changed artifacts first: the viewer never runs model scripts. Existing
STEP files compile on open when needed. Exact topology selection and measurement
require STEP; supported mesh viewers also offer approximate measurements on triangles.
