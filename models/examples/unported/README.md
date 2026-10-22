# examples: unported models

The 24 part models of the old `examples` project that the port to `cadgen.engine` did
not take: it ported a selection of five (`../src/README.md`) and gives no other reason.
Each is kept with its old sources in the layout it had: the script in `src/`, and the
helpers the scripts import in `src/lib/` (`part_common`, `simple_model_library`) as
the old project had them. The sources are unchanged, written for cadgen before the
engine.

| Script | What it is (the old README's) |
| --- | --- |
| `cam_follower_roller.py` | Cam follower roller with central bearing bore and rounded outer profile |
| `centrifugal_impeller.py` | Single solid centrifugal impeller |
| `circular_flange.py` | Circular flange model |
| `clevis_bracket_lightening_cutouts.py` | Clevis bracket model |
| `cylindrical_cap.py` | Cylindrical cap with hollow interior, top boss, and rounded external edges |
| `cylindrical_spacer_sleeve.py` | Cylindrical spacer sleeve with a central through-bore and rounded rim edges |
| `flywheel_disk.py` | Flywheel disk with central bore, annular rim, and lightening holes |
| `gusset_plate.py` | Gusset plate with a triangular web, base holes, and softened perimeter edges |
| `keyed_shaft_hub.py` | Keyed shaft hub with central bore, keyway slot, and bolt-hole pattern |
| `mounting_plate.py` | Mounting plate with central circular cutout, elongated side slot, four corner holes, and rounded edges (it also declared STL, 3MF and GLB) |
| `open_top_electronics_enclosure.py` | Open-top electronics enclosure model |
| `print_in_place_hinge.py` | Print-in-place barrel hinge for FDM printing |
| `print_in_place_multi_pivot_phone_holder.py` | Print-in-place multi-pivot holder with a phone/tablet cradle for FDM printing |
| `pulley_wheel.py` | Pulley wheel with a central hub, outer groove, and circular through-bore |
| `radial_engine_cylinder.py` | Radial-engine-style cylinder model |
| `rectangular_calibration_block.py` | Rectangular calibration block model |
| `rectangular_clamp_block.py` | Rectangular clamp block with a split slot and two transverse screw holes |
| `retainer_plate.py` | Retainer plate with elongated slot, two circular holes, and chamfered perimeter |
| `shaft_collar.py` | Shaft collar with a central bore, radial set-screw hole, and chamfered faces |
| `small_enclosure_cover.py` | Small enclosure cover with raised rim, corner screw holes, and shallow recessed center |
| `spur_gear_blank.py` | Spur gear blank with central bore, raised hub, and simplified perimeter teeth (it also declared STL, 3MF and GLB) |
| `square_mounting_block.py` | Square mounting block with a vertical through-hole and two side clearance holes |
| `stepped_shaft_keyway.py` | Stepped shaft with keyway model |
| `t_slot_slider_block.py` | T-slot slider block with central channel, side relief cuts, and mounting holes |
