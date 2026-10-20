# examples (a selection)

Four scripts from `models/examples/src`, ported to `cadgen.engine`:

| Script | Artifacts | What it is |
| --- | --- | --- |
| `l_bracket.py` | `XBF/l_bracket.xbf`, `.step` | L-bracket with gussets and a filleted transition (`lib/part_common.safe_fillet`) |
| `electronics_enclosure_base.py` | `XBF/electronics_enclosure_base.xbf`, `.step` | Open-top enclosure with rounded corners and standoffs |
| `basic_shape_mating_test_fixture.py` | `XBF/basic_shape_mating_test_fixture.xbf`, `.step`, `.3mf` | Seven parts and a sub-assembly seated on a base plate by build123d joints |
| `electronics_enclosure_base_import.py` | `XBF/electronics_enclosure_base_import.xbf`, `XBF/basic_shape_mating_test_fixture_import.xbf` | The two STEPs above read back with `read_step` |

Build from this folder, the import last (it reads the others' STEP outputs):

```
python -m cadgen.engine build l_bracket.py electronics_enclosure_base.py basic_shape_mating_test_fixture.py
python -m cadgen.engine build electronics_enclosure_base_import.py
```

The fixture's `AssemblyHelper` frames are build123d joints: a `RigidJoint` on
each part in its own frame and `connect_to` moving the part onto the base
(`offset=` as a second fixed joint, `<frame>:offset`, as the old helper made).

`electronics_enclosure_base_import.py` is the port of
`electronics_enclosure_base_drawing.py`, whose kernel call was `read_step` of
the enclosure's STEP; the engine has no drawing decorator, so the drawing is
dropped and the read is kept, plus a read of the fixture's STEP to exercise an
imported assembly's names and colours.
