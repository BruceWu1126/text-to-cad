# Minimal project starters

A single part and a small assembly, in the [project layout](project-layout.md)'s
default structure. Commands below run from the CAD project root.

## One part

`src/plate.py`:

```python
# src/plate.py
from cadgen.engine import xbf
import build123d as bd

WIDTH = 60.0
DEPTH = 40.0
THICKNESS = 4.0


@xbf(out="../XBF/plate.xbf")
def plate():
    body = bd.Box(WIDTH, DEPTH, THICKNESS)
    body.label = "plate"
    return body
```

```bash
python src/plate.py
```

A second run replays the model and keeps its file. A one-off part can instead use a
flat folder and `@xbf("XBF/plate.xbf")` to put the document beside the script.

## An assembly

Start with the plate above and add `src/standoff.py`:

```python
# src/standoff.py
from cadgen.engine import xbf
import build123d as bd

HEIGHT = 12.0
OUTER_D = 8.0
BORE_D = 3.4


@xbf(out="../XBF/standoff.xbf")
def standoff():
    post = bd.Cylinder(OUTER_D / 2, HEIGHT) - bd.Cylinder(BORE_D / 2, HEIGHT)
    post.label = "standoff"
    return post
```

Then `src/assembly.py` places two of them on the top of the centered plate:

```python
# src/assembly.py
from cadgen.engine import xbf
import build123d as bd

from plate import THICKNESS, plate
from standoff import HEIGHT, standoff

PITCH = 30.0


@xbf(out="../XBF/assembly.xbf")
def assembly():
    base = plate()
    post = standoff()
    z = (THICKNESS + HEIGHT) / 2
    left = bd.Pos(-PITCH / 2, 0, z) * post
    right = bd.Pos(PITCH / 2, 0, z) * post
    left.label, right.label = "standoff_left", "standoff_right"
    return bd.Compound(children=[base, left, right], label="assembly")
```

```bash
python src/assembly.py
cadgen inspect XBF/assembly.xbf
```

Running the root builds its children in the same run, and each writes its own
`.xbf`; running a child alone leaves the root's document as it was until the root
runs. `cadgen inspect` prints what the root's document was built from. For mating
datums and joint relationships, see [positioning](positioning.md).

## Adding capabilities

| Need | Add |
| --- | --- |
| STEP, STL, 3MF or GLB output | A decorator (`@step`, `@stl`, `@threemf`, `@glb`) on the model, or `cadgen export` from its `.xbf`. See [exports](supported-exports.md). |
| Shared factory or hole pattern | A plain helper under `src/lib/`, with `src/lib/__init__.py`. |
| Left/right geometry | Mirror inline, or separate models for independent files and reuse. |
| Subassembly | A model that calls its child models. |
| Motion | build123d joints in the assembly's body, then `mechanism_state`, `gear_pair` / `rack_and_pinion_pair` / `screw_pair` and `configuration_sequence`. See [kinematics](kinematics.md). |
| Materials | `part.material = materials.aluminium_6061`. See [colour and materials](build123d-modeling.md#colour-and-materials). |
| 2D drawing | A separate drawing model using `$dxf`. |
| Vendor CAD | The vendor STEP under `vendor/`, converted once with `cadgen export vendor/motor.step vendor/motor.xbf` and read with `read_xbf` (an anchored path). |
