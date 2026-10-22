# examples (a selection)

Five scripts from `models/examples/src`, ported to `cadgen.engine`:

| Script | Artifacts | What it is |
| --- | --- | --- |
| `l_bracket.py` | `XBF/l_bracket.xbf` | L-bracket with gussets and a filleted transition (`lib/part_common.safe_fillet`) |
| `electronics_enclosure_base.py` | `XBF/electronics_enclosure_base.xbf` | Open-top enclosure with rounded corners and standoffs |
| `basic_shape_mating_test_fixture.py` | `XBF/basic_shape_mating_test_fixture.xbf` | Seven parts and a sub-assembly seated on a base plate by build123d joints |
| `electronics_enclosure_base_import.py` | `XBF/electronics_enclosure_base_import.xbf`, `XBF/basic_shape_mating_test_fixture_import.xbf` | `STEP/electronics_enclosure_base.step` and `STEP/basic_shape_mating_test_fixture.step` read back with `read_step` |
| `electronics_enclosure_base_drawing.py` | `PDF/electronics_enclosure_base_drawing.pdf` | **Engineering drawing** (`@eng_drawing`) of the enclosure, read from its xbf (`read_xbf`): three views with hidden lines and centre marks, measured dimensions, hole callouts, title block. Build the enclosure first. |

Materials: the L-bracket is Steel 1018 and the enclosure ABS, each drawn in the
catalogue's colour (neither authored one); the fixture takes one material on its
assembly, which every part below it takes: bead-blasted Aluminum 6061-T6,
anodised in each part's own colour (`color=None`). The imports read the STEP
inputs as they are, without materials.

Each model declares its xbf alone. The import reads fixed STEP inputs committed in
`../STEP/`, exported once from the enclosure and the fixture; no model writes them
(re-export one with `cadgen export ../XBF/NAME.xbf ../STEP/NAME.step`).
Build from this folder, in any order: each script builds every model it defines.

```
python l_bracket.py
python electronics_enclosure_base.py
python basic_shape_mating_test_fixture.py
python electronics_enclosure_base_import.py
python electronics_enclosure_base_drawing.py   # after electronics_enclosure_base.py
```

The fixture's `AssemblyHelper` frames are build123d joints: a `RigidJoint` on
each part in its own frame and `connect_to` moving the part onto the base
(`offset=` as a second fixed joint, `<frame>:offset`, as the old helper made).

`electronics_enclosure_base_drawing.py` is cadgen's `@eng_drawing`, outside the
engine: a document over a part, run in the script's own process, never a model.
It read the STEP the enclosure wrote; it reads the enclosure's xbf now
(`read_xbf(../XBF/electronics_enclosure_base.xbf)`), so build the model first, and
takes every dimension's constants from the model module. Its PDF (gitignored, as
`XBF/` is) carries no date: an unchanged drawing writes the same bytes.

`electronics_enclosure_base_import.py` keeps the drawing's old read: `read_step` of
the enclosure's STEP, plus a read of the fixture's STEP to exercise an imported
assembly's names and colours.
