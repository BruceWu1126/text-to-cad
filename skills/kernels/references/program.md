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
| `revolve` | `profile`, `axis: {origin, direction}` (world), `angle` (degrees), `matrix` (the sketch plane) |
| `union`, `intersect` | `children: [node, ...]` |
| `subtract` | `base`, `tool` |
| `fillet`, `chamfer` | `child`, `size`, `select`: `{"all": true}`, `{"parallel": "X"\|"Y"\|"Z"}` or `{"custom": "..."}` (an edge selection the recorder can't describe) |

A `profile` is `{"entities": [...]}`, combined in order. Each entity has a
`center: [x, y]`, a `rotation` in degrees and a `mode` (`"add"` or
`"subtract"`):

- `{"type": "rect", "width", "height", "cornerRadius"}`
- `{"type": "circle", "radius"}`

## What records

BuildPart and BuildSketch, Locations (including GridLocations and
PolarLocations), Box, Cylinder, Cone, Sphere, Hole, Rectangle,
RectangleRounded, Circle, extrude, revolve, fillet, chamfer, Pos, Rot, and the
`+`, `-` and `&` algebra. A model using anything else fails to record with the
name of what's missing, so a program kernel never builds a guess.
