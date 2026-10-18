# Supported exports

Read this file when the user requests STL, 3MF, or native GLB output files from
CAD geometry. A `.step` comes from running the model script
([model contract](step-generation.md)); 2D DXF belongs to the `$dxf` skill.
Native GLB exports are ordinary glTF 2.0 binary for external tools: Y-up, one
material per distinct part or face colour.

## Declare the exports the model always has

Stack `@stl`, `@glb` or `@threemf` on the model for maintained mesh outputs. For
example, a project-root `bracket.py`:

```python
from cadgen import build123d as bd
from cadgen import glb, step, stl


@step(out="STEP/bracket.step")
@stl(out="STL/bracket.stl")
@glb(out="GLB/bracket.glb")
def bracket():
    return bd.Box(40, 20, 6)


if __name__ == "__main__":
    bracket()
```

Every run writes the STEP and the declared meshes, and restores declared outputs
that were deleted or edited. Mesh declarations alone create no sidecar, and the
export commands below never read them. Stacking order does not matter.

## A model with no STEP

A function with `@stl`, `@glb` or `@threemf` and no `@step` is a full model (same
tree, record, no-op and composition: `spacer()` inside another model links like
any child) that writes its meshes and no `.step` or sidecar. Its format's
`snapshot` renders it, and `cadgen store why spacer.py` explains its freshness.

```python
from cadgen import build123d as bd
from cadgen import stl


@stl(out="STL/spacer.stl", mesh_tolerance=4e-4)
def spacer():
    return bd.Cylinder(6, 3) - bd.Cylinder(2.5, 3)


if __name__ == "__main__":
    spacer()
```

Maintained draft/print variants can keep the model's stem in separate subfolders:

```python
@stl(out="STL/draft/bracket.stl", mesh_tolerance=8e-3)
@stl(out="STL/print/bracket.stl", mesh_tolerance=4e-4)
```

## Tool

One door per format (`cadgen stl build`, `cadgen 3mf build`, `cadgen glb build`),
each taking a saved STEP/STP and an optional OUT (default: a sibling with the
format's extension; an explicit OUT is relative to the working directory):

```bash
cadgen stl build STEP/model.step                     # writes STEP/model.stl
cadgen stl build STEP/model.step meshes/model.stl    # one ad-hoc export
```

An unchanged export reports `current`; `--force` re-exports without rebuilding
the source model. A mesh door never writes a `.step`. `cadgen glb build
--animation CLIP` writes a clip into the GLB as glTF animation
([exporting the clip inside a GLB](kinematics.md#exporting-the-clip-inside-a-glb)).

## Rendering a mesh file

Each mesh format has a `snapshot` verb with `cadgen step snapshot`'s grammar:

```bash
cadgen stl snapshot STL/bracket.stl tmp/bracket_mesh.png
cadgen 3mf snapshot 3MF/bracket.3mf tmp/bracket_3mf.png
cadgen glb snapshot meshes/bracket.glb tmp/bracket_glb.png
```

A mesh carries no CAD topology. Its snapshot door accepts `--display solid`
(the default) or `--display parts` (each object or mesh its own colour), `--grid`
for the measuring grid, and `--preview` for Preview's photographic view. Inline JSON
and JSON files are the Viewer's display object: `mode`, `grid`, `appearance`,
`camera`, `clip` and `exploded`. `display.clip` cuts it (`at` in the file's own
unit: a GLB's metres, a 3MF's declared unit, an STL's own numbers) and
`display.exploded` moves a GLB's nodes apart (a GLB of one is refused as it renders).
The `shaded`, `xray`, `hidden-line` and `wireframe` modes are STEP-only and are
refused by name, as `exploded` is for an STL or a 3MF.
Mesh doors do not have
`--focus`/`--hide`, `--kinematics`, or `--animation`/`--time`; meshes have no
canonical CAD occurrences, kinematics,
or document animation clips for those controls to act on. `cadgen step snapshot`
refuses a mesh input and names the door that takes it.

The picture is the scene the CAD Viewer draws for the same file, built by the same code:
an STL or a 3MF is its objects in their colours, and a GLB is its own glTF scene (nodes,
skins, morph targets and authored materials), so `--preview` shows the finish the
file authored. A GLB's clips play in the viewer; its snapshot is the file at rest.

For a mesh-only model, this is its required visual review. When a STEP is also
produced, review that document and render the mesh when tessellation or external
tool output is the question. Pure format conversion with unchanged geometry
follows the skip cases in [snapshot review](snapshot-review.md).

## Mesh tolerance

Mesh exports are cadgen's mesh of each component's exact surfaces. Faces that
meet share their boundary vertices, and identical inputs produce identical bytes.
An export that names no tolerance is written at the display tolerance, from the
very meshes the CAD Viewer and snapshots draw.

A mesh decorator takes `mesh_tolerance=` and `mesh_angular_tolerance=`; an export
command takes the same two as flags (the model's declarations are not read):

```bash
--mesh-tolerance FLOAT           # chord tolerance RELATIVE to each component's
                                 # bounding diagonal (default 5e-4)
--mesh-angular-tolerance FLOAT   # max normal spread across a triangle edge,
                                 # radians (default 0.35)
```

`--json` results report the effective pair.

Linear tolerance is relative, not millimetres: for a chord deviation of X mm on a
part whose bounding diagonal is D mm, pass X/D (0.1 mm on a 200 mm part is
`5e-4`). Linear tolerance must be from `5e-5` to `0.05` and angular tolerance
from `0.05` to `1.5708` radians, on flags and decorators alike; finer settings
take minutes per curved face without visible gain. A value outside is refused
before anything builds.

A face no mesher can cover (typically a sliver a boolean left, narrower than the
chord tolerance) never fails the export: the part is written without it, and the
result's `warnings` name it, for example `#o:4b1f9e2a7c03 pin: 1 face
(f:pin.make_box1.2;M:pin.cut1) could not be meshed, so pin.stl has a hole in place of it: it is
not watertight`. Repairing the face in the model, or a finer `--mesh-tolerance`,
closes the hole.
