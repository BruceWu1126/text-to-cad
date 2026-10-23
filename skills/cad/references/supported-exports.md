# Exports

Read this file when the user asks for STEP, STL, 3MF or GLB files, a part on its own,
a model in another pose, or a package of a model's sources. 2D DXF belongs to `$dxf`.

The `.xbf` is the model; every other format is a translation of it, written by one
writer from the document alone. Two ways to ask for one, which write the same bytes:

- **A decorator** declares a file every build of the model keeps current
  ([decorators](model-contract.md#decorators-declare-files)):

  ```python
  from cadgen.engine import glb, step, stl, threemf, xbf
  import build123d as bd


  @xbf(out="XBF/bracket.xbf")
  @step(out="STEP/bracket.step")
  @stl(out="STL/bracket.stl")
  @threemf(out="3MF/bracket.3mf")
  @glb(out="GLB/bracket.glb")
  def bracket():
      return bd.Box(40, 20, 6)
  ```

- **`cadgen export`** writes files from a document once, by each destination's
  suffix, reading the document a single time:

  ```bash
  cadgen export XBF/bracket.xbf STL/bracket.stl GLB/bracket.glb 3MF/bracket.3mf
  ```

A source is an `.xbf`, or a `.step` read into a document first. A mesh is never a
source: it has no B-rep. Each file is written beside its target and moved over it,
so a failed export leaves the old file whole. `--json` prints what was written as
one object (`{"ok": true, "source", "files": [{"path", "kind", "bytes", "seconds"}]}`),
and a failure as `{"ok": false, "error": ...}` with exit status 1, as every command's
`--json` does.

## Formats

- **STEP** (AP242): the assembly with its instance and product names, colours per
  part and per painted face, materials' names and densities, and the element names on
  every face, edge and vertex, so a STEP read back keeps them. It drops the rest of a
  material's look and physical fields, a face's material (the face keeps its colour),
  and the mechanism: a STEP is placements only, as built.
- **GLB**: glTF 2.0, Y up, in metres. One mesh per part, a node per placement named
  by its occurrence, a PBR material per look (with the glTF extensions for IOR,
  transmission, clear coat, anisotropy and emission where a material has them), and
  each of the model's motions as an animation of the same name
  ([configuration sequences](kinematics.md#configuration-sequences)): the one export that carries motion. A GLB the model
  declares (`@glb`) also bakes the model's `animation()`s ([animation](animation.md)); one
  `cadgen export` writes from the `.xbf` carries the motions only.
- **3MF**: an object per part, a build item per placement, in millimetres, with
  colours and materials through the materials extension.
- **STL**: binary, every placed triangle, in millimetres.
- **DXF**: of a planar document only (`$dxf`); a solid is refused.
- **xbf**: from a STEP (converting a vendor file, below).

Mesh exports and the viewer draw the same meshes, made at one tolerance: FreeCAD's
default (a chord deviation of 0.5% of the mean side of each part's bounding box, and
28.5 degrees), configurable nowhere. A face the mesher leaves without triangles is left out, so a mesh export has
a hole there; the snapshot of the document names such faces in a warning, and they
are usually slivers a boolean left. Repair the face in the model.

## Poses and parts

An export takes a document and destinations, nothing else: the file holds what the
code built, every part where it stands, in its colours, with its motion. A posed or
partial file is a function in the model's code, never an option.

```python
@step(out="../STEP/arm_folded.step")
def arm_folded():
    return posed(arm(), "folded")              # or posed(arm(), {"elbow": 45})

@stl(out="../STL/wheel.stl")
def wheel():
    return wheel_part()                         # the part's own function, in its own frame
```

`posed` is `cadgen.ap242`'s ([kinematics](kinematics.md#posed-files)). A printable
part, or a robot link's mesh in the link's frame, is the function that makes that
part, declared with its own file.

## A vendor STEP

```bash
cadgen export vendor/motor.step vendor/motor.xbf
```

writes an xbf of the STEP: the same geometry, names, colours and materials, read
many times faster by `read_xbf` than the STEP by `read_step`. Convert once, keep the
xbf beside the STEP, and read the xbf in models
([inputs](model-contract.md#inputs)).

## Other tools

STL and 3MF files are in millimetres and a GLB in metres, so a URDF or SDF scales an
STL or 3MF link mesh by `0.001` and takes a GLB as it is. A check of an export as a
mesh is a snapshot of that file
([mesh files](snapshot-review.md#mesh-files-robots-and-drawings)).
