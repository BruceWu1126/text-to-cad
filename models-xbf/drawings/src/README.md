# drawings (a selection)

Two scripts from `models/drawings/src`, ported to `cadgen.engine`: a drawing is a
model whose body returns 2D build123d geometry, and `@dxf` declares its DXF, written
by build123d's own DXF exporter from the model's planar document.

| Script | Artifact | What it is |
| --- | --- | --- |
| `gasket_plate.py` | `DXF/gasket_plate.dxf` | Rounded gasket, bolt holes and a centre cutout on `CUT`, an open crosshair on `ENGRAVE` |
| `clamp_plate.py` | `DXF/clamp_plate.dxf` | The cut profile of a solid (`lib/clamp_plate_profile.py`): its top face laid onto z=0 |

Build from this folder, either one:

```
python gasket_plate.py
python clamp_plate.py
```

The port:

- `from cadgen import dxf` -> `from cadgen.engine import dxf`; cadgen's build123d proxy
  -> `import build123d as bd`. `@dxf(out=...)` is unchanged; the `__main__` call goes
  (running the script builds its models).
- Layers: the `{"CUT": ..., "ENGRAVE": ...}` dict becomes a compound of labelled children,
  a part per layer (`plate.label, crosshair.label = "CUT", "ENGRAVE"`); a layer of several
  shapes is one part of them all (`bd.Compound([line, line])`). A model returning one shape
  is the layer `CUT`.
- `cadgen.flatten.flat_pattern(plate(), ..., coordinate=THICKNESS_MM, kerf=0)` becomes
  the solid's top face moved onto z=0: one planar face at that height, no kerf, which is
  all the flat pattern did here. The helper stays a plain module, not a model.

The other six drawings (`angled_tab`, `cabinet_panel_drawing`, `l_bracket_flat`,
`label_plate`, `multi_bend_test_panel`, `u_channel_bracket`) are not ported.
