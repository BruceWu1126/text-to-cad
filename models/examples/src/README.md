# examples

Every script of `models/examples/src`, ported to `cadgen.engine`: one `@xbf` each.

| Script | Artifacts | What it is |
| --- | --- | --- |
| `l_bracket.py` | `XBF/l_bracket.xbf` | L-bracket with gussets and a filleted transition (`lib/part_common.safe_fillet`) |
| `electronics_enclosure_base.py` | `XBF/electronics_enclosure_base.xbf` | Open-top enclosure with rounded corners and standoffs |
| `basic_shape_mating_test_fixture.py` | `XBF/basic_shape_mating_test_fixture.xbf` | Seven parts and a sub-assembly seated on a base plate by build123d joints |
| `electronics_enclosure_base_import.py` | `XBF/electronics_enclosure_base_import.xbf`, `XBF/basic_shape_mating_test_fixture_import.xbf` | `STEP/electronics_enclosure_base.step` and `STEP/basic_shape_mating_test_fixture.step` read back with `read_step` |
| `electronics_enclosure_base_drawing.py` | `PDF/electronics_enclosure_base_drawing.pdf` | **Engineering drawing** (`@eng_drawing`) of the enclosure, read from its xbf (`read_xbf`): three views with hidden lines and centre marks, measured dimensions, hole callouts, title block. Build the enclosure first. |
| `cam_follower_roller.py` | `XBF/cam_follower_roller.xbf` | Cam follower roller with central bearing bore and rounded outer profile (Steel 4140) |
| `centrifugal_impeller.py` | `XBF/centrifugal_impeller.xbf` | Single solid centrifugal impeller (Aluminum 6061-T6) |
| `circular_flange.py` | `XBF/circular_flange.xbf` | Circular flange with a six-hole bolt circle (Steel 1018) |
| `clevis_bracket_lightening_cutouts.py` | `XBF/clevis_bracket_lightening_cutouts.xbf` | Clevis bracket with lightening cutouts (Aluminum 7075-T6) |
| `cylindrical_cap.py` | `XBF/cylindrical_cap.xbf` | Cylindrical cap with hollow interior, top boss, and rounded external edges (Aluminum 6061-T6) |
| `cylindrical_spacer_sleeve.py` | `XBF/cylindrical_spacer_sleeve.xbf` | Cylindrical spacer sleeve with a central through-bore and rounded rim edges (Aluminum 6061-T6) |
| `flywheel_disk.py` | `XBF/flywheel_disk.xbf` | Flywheel disk with central bore, annular rim, and lightening holes (Steel 1018) |
| `gusset_plate.py` | `XBF/gusset_plate.xbf` | Gusset plate with a triangular web, base holes, and softened perimeter edges (Steel A36) |
| `keyed_shaft_hub.py` | `XBF/keyed_shaft_hub.xbf` | Keyed shaft hub with central bore, keyway slot, and bolt-hole pattern (Steel 1018) |
| `mounting_plate.py` | `XBF/mounting_plate.xbf` | Mounting plate with central cutout, side slot, four corner holes, rounded edges; also `XBF/mounting_plate.stl`, `.3mf`, `.glb` (Aluminum 6061-T6) |
| `open_top_electronics_enclosure.py` | `XBF/open_top_electronics_enclosure.xbf` | Open-top electronics enclosure with corner bosses (ABS) |
| `print_in_place_hinge.py` | `XBF/print_in_place_hinge.xbf` | Print-in-place barrel hinge for FDM printing: two leaves, one revolute joint (PLA) |
| `print_in_place_multi_pivot_phone_holder.py` | `XBF/print_in_place_multi_pivot_phone_holder.xbf` | Print-in-place multi-pivot phone/tablet holder: four links, three revolute joints (PLA) |
| `pulley_wheel.py` | `XBF/pulley_wheel.xbf` | Pulley wheel with a central hub, outer groove, and circular through-bore (Aluminum 6061-T6) |
| `radial_engine_cylinder.py` | `XBF/radial_engine_cylinder.xbf` | Radial-engine-style cylinder with cooling fins (Aluminum 6061-T6) |
| `rectangular_calibration_block.py` | `XBF/rectangular_calibration_block.xbf` | Rectangular calibration block with four holes and a chamfered top (Steel 1018) |
| `rectangular_clamp_block.py` | `XBF/rectangular_clamp_block.xbf` | Rectangular clamp block with a split slot and two transverse screw holes (Aluminum 6061-T6) |
| `retainer_plate.py` | `XBF/retainer_plate.xbf` | Retainer plate with elongated slot, two circular holes, and chamfered perimeter (Steel 1018) |
| `shaft_collar.py` | `XBF/shaft_collar.xbf` | Shaft collar with a central bore, radial set-screw hole, and chamfered faces (Steel 1018) |
| `small_enclosure_cover.py` | `XBF/small_enclosure_cover.xbf` | Small enclosure cover with raised rim, corner screw holes, and recessed center (ABS) |
| `spur_gear_blank.py` | `XBF/spur_gear_blank.xbf` | Spur gear blank with central bore, raised hub, and simplified perimeter teeth; also `XBF/spur_gear_blank.stl`, `.3mf`, `.glb` (Brass C360) |
| `square_mounting_block.py` | `XBF/square_mounting_block.xbf` | Square mounting block with a vertical through-hole and two side clearance holes (Aluminum 6061-T6) |
| `stepped_shaft_keyway.py` | `XBF/stepped_shaft_keyway.xbf` | Stepped shaft with keyway (Steel 4140) |
| `t_slot_slider_block.py` | `XBF/t_slot_slider_block.xbf` | T-slot slider block with central channel, side relief cuts, and mounting holes (Aluminum 6061-T6) |

The 24 parts after the first five are the old project's, ported as they were: `@step`
became `@xbf(out="../XBF/<name>.xbf")`, `build123d` is imported directly, and the two
that also declared STL, 3MF and GLB keep those as decorators on the same function,
writing beside the xbf. Their shared builders are `lib/simple_model_library.py` (the
`make_*` parts) and `lib/part_common.py`, plain modules; a colour is the sRGB hex itself,
which a shape takes as it is. Each takes a catalogue material, in brackets above: a part that
authors its own colour keeps it (`color=None`), one that does not takes the catalogue's.
The two print-in-place assemblies' `AssemblyHelper` frames are build123d joints: a
`RevoluteJoint` on the bored link and a `RigidJoint` on the pinned one, on the same pin
axis, paired by their label where the links stand as printed (open flat, 0 degrees).

Materials: the L-bracket is Steel 1018 and the enclosure ABS, each drawn in the
catalogue's colour (neither authored one); the fixture takes one material on its
assembly, which every part below it takes: bead-blasted Aluminum 6061-T6,
anodised in each part's own colour (`color=None`). The imports read the STEP
inputs as they are, without materials.

Each model declares its xbf alone, but the mounting plate and the gear blank (STL, 3MF and
GLB beside it). The import reads fixed STEP inputs committed in
`../STEP/`, exported once from the enclosure and the fixture; no model writes them
(re-export one with `cadgen export ../XBF/NAME.xbf ../STEP/NAME.step`).
Build from this folder, in any order: each script builds every model it defines, for example

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
