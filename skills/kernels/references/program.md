# The recorded program

Kernels with `"input": "program"` get the model as a kernel-neutral CSG tree,
recorded by running the model program against a stand-in for build123d
(`scripts/kernel_tools/record.py`). The file is JSON:

```json
{"ok": true, "model": "bracket", "root": { ... }}
```

Lengths are mm, matrices are row-major 4x4 placing the node in the world, and
Z is up.

| Node | Fields |
| --- | --- |
| `box` | `size: [l, w, h]`, `matrix` (centered on its origin) |
| `cylinder` | `radius`, `height`, `matrix` (centered) |
| `cone` | `bottomRadius`, `topRadius`, `height`, `matrix` (centered) |
| `sphere` | `radius`, `matrix` |
| `hole` | `radius`, `depth` (`null` = through the whole part), `matrix`: build123d's hole, a cylinder reaching `depth` along both +Z and -Z |
| `prism` | `profile`, `amount`, `both`, `matrix`: the profile (sketch-local XY) extruded along the matrix's +Z, both ways when `both` |
| `revolve` | `profile`, `axis: {origin, direction}` (world), `angle` (degrees, counter-clockwise about the axis from the sketch's radial direction), `matrix` (the sketch plane) |
| `union`, `intersect` | `children: [node, ...]` |
| `subtract` | `base`, `tool` |
| `fillet`, `chamfer` | `child`, `size`, `select`: `{"all": true}`, `{"parallel": "X"\|"Y"\|"Z"}` or `{"custom": "..."}` (an edge selection the recorder can't describe), and `tools: {cut, fill}` read from the real build (below) |
| `loft` | `matrix` (Z along the loft), `stations` (ascending Z), `rings` (one matching 2D outline per station), `mesh` |
| `sweep` | `section` (2D outline), `path` (points), `frames` (the section's X and Y at each point), `closed`, `mesh` |

A `profile` is `{"entities": [...]}`, combined in order. Each entity has a
`center: [x, y]`, a `rotation` in degrees and a `mode` (`"add"` or
`"subtract"`):

- `{"type": "rect", "width", "height", "cornerRadius"}`
- `{"type": "circle", "radius"}`
- `{"type": "polygon", "points": [[x, y], ...]}` (fillet and chamfer tools,
  glyph outlines)

A `mesh` is `{"vertices": [x, y, z, ...], "triangles": [i, j, k, ...]}`:
closed, outward-facing, sharing vertices.

## Read from the real build

Some answers aren't in the program text, so `scripts/kernel_tools/resolve.py`
runs the model once more in build123d and reads them; the kernels still
build every solid:

- **Fillets and chamfers**: the selected edges and the faces on each side.
  Each straight edge becomes a `prism`, each circular edge a `revolve`, of the
  material the fillet or chamfer removes (convex edges, `tools.cut`) or adds
  (concave edges, `tools.fill`). A kernel uses them where its own shapes
  (rounded profile corners, a rounded box) don't fit. Other edges are left
  sharp, with a note.
- **Text**: each glyph's outline, as `polygon` entities (holes subtract).
- **offset**: which faces are openings. The shell becomes plain nodes: the
  part minus a copy shrunk by the thickness, whose faces in an opening's
  plane move out instead. Shrinking is exact for boxes, cylinders, spheres,
  extrusions and revolves; separate solids that only touch keep a wall
  between them (noted).
- **loft**: the sections, paired as OpenCASCADE pairs them (each section is
  split at the angles of every section's corners and sampled evenly between).
- **sweep**: the path, sampled with its exact tangents, and the section,
  carried along it on rotation-minimising frames.

Curves are sampled within 0.002 mm and sampled outlines are scaled to their
face's exact area. `run` adds the resolver's notes to the run's notes.

Starting build123d takes seconds, so the answers are cached per input (the
recorded program, the curves made and the model's source) in
`$CAD_KERNELS_HOME/_resolve` (by default `~/.cache/text-to-cad/kernels/_resolve`):
only the first run of an edited model pays for the real build.

## What records

BuildPart, BuildSketch and BuildLine (its curves only as a sweep's path),
Locations (including GridLocations and PolarLocations), Box, Cylinder, Cone,
Sphere, Hole, Rectangle, RectangleRounded, Circle, Text, extrude, revolve,
loft, sweep, offset, fillet, chamfer, Pos, Rot, and the `+`, `-` and `&`
algebra. A model using anything else fails to record with the
name of what's missing, so a program kernel never builds a guess.
