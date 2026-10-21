# Corpus materials: before and after

Branch `xbf-corpus-materials` in `.claude/worktrees/agent-materials`, five commits on
df59c1563, one per model (not pushed):

| Commit | Model |
| --- | --- |
| 561b7272b | motorbike: `lib.paint(..., color=, material=)`; the materials sit in `lib/spec.py` beside the palette |
| aa2f48f4f | qdd_actuator: `_label(..., color=, material=)`; the materials sit under the palette |
| 42d20359a | moonwatch: the finishes through `.but(...)` on specific entries, the original's clearcoat back as `coat`, the chronograph works assigned |
| 49397dbd2 | tom: the finishes through `.but(...)` on 5052-H32, 4140, 304, C360 and FR4; the vendor boards take FR4 by label |
| ed71c1372 | examples: the L-bracket Steel 1018, the enclosure ABS, the mating fixture one anodised 6061 on its assembly |

Every material is `color=None` (the leather, ferrite, polyimide, sapphire, ruby and ink of the
projects' own included), so the colour a part was authored in is its base colour in the file
and in the viewer, and the material changes only the finish. Where no colour was authored
(the L-bracket, the enclosure) the catalogue's colour shows.

## How the pictures were made

- **Before**: 842f2171c's engine and corpus sources, in a detached worktree, built cold in their
  own daemon, drawn with the browser runtime built before the materials merge (copied from
  `agent-a1cb246fec605ab31` at 14:14: it has no authored-finish path, so it draws colours under
  the theme's finish).
- **After**: this branch's sources, built cold by this branch's engine (df59c1563's code), drawn
  with the runtime built for df59c1563 (it draws a material's metallic, roughness, IOR,
  anisotropy, transmission, coat and emission).
- `cadgen xbf snapshot`, 1600x1200, iso camera: Solid (`--display solid`), Shaded, Preview
  (`--preview`), and a Preview close-up (`--camera '{"preset":"iso","zoom":Z,"target":[x,y,z]}'`,
  the same on both sides). Sheets are downscaled to 1800 px wide.
- A pixel differs when any RGB channel moved by more than 10/255; the fraction is of all
  1,920,000 pixels, background included.

## Pixels that differ, before vs after

| Model | Solid | Shaded | Preview | Preview close-up |
| --- | --- | --- | --- | --- |
| motorbike | 26.2% | 26.9% | 5.4% | 17.0% |
| qdd_actuator | 52.8% | 54.6% | 41.4% | 96.3% |
| case | 35.5% | 38.9% | 31.9% | 55.9% |
| movement | 48.9% | 52.3% | 28.7% | 54.4% |
| tom | 4.1% | 4.5% | 3.9% | 14.1% |

tom's arm fills a small share of its frame; its close-up (the electronics bay: 5052 plates, C360
standoffs, 304 nuts, 4140 screws, the FR4 HAT) is where the change shows.

Sheets: `sheets/motorbike.png`, `sheets/qdd_actuator.png`, `sheets/case.png`,
`sheets/movement.png`, `sheets/tom.png` (rows: Solid, Shaded, Preview, Preview close-up;
columns: before, after).

## What each model carries now

Read back from the after documents: parts grouped by the material they carry (instances in
brackets), and the look fields that differ from the catalogue entry of that name (a project's
own material: from a `Material`'s defaults). Colour is never listed: it is the part's own.

### motorbike

| Parts | Material | Description | Look beyond the catalogue's |
| --- | --- | --- | --- |
| frame, front_fork, handlebar, damper, center_stand (5) | Steel 1018 | Steel tube, gloss black paint | metallic 0, roughness 0.5, coat 1, coat_roughness 0.1 |
| steering_cover, leg_shield, front_fender, under_seat_body, rear_fender (5) | ABS | Moulded ABS panel, painted and clear-coated | roughness 0.45, coat 1, coat_roughness 0.06 |
| front_axle, rear_axle, piston_rod (3) | Steel 4140 | Hard-chromed alloy steel | roughness 0.12 |
| grip (2) | TPU 95A | Thermoplastic polyurethane for flexible prints | - |
| brake_lever (2) | Aluminum 6061-T6 | Precipitation-hardened structural aluminium: the usual machined and plate alloy | - |
| tire (2) | NBR 70A | Nitrile rubber: O-rings and oil seals | - |
| cast_rim, engine, cvt_cover, cvt_inner_plate, crank_cover, brake_drum (7) | Aluminum 6061-T6 | Cast aluminium, as cast | roughness 0.6, anisotropy 0 |
| brake_disc (1) | Steel 4140 | Ground brake disc | roughness 0.3 |
| headlight_shell, turn_signal_housing, exhaust, mirror_shell (8) | Stainless 304 | Polished stainless | roughness 0.1, anisotropy 0 |
| headlight_lens (1) | Polycarbonate | Tough clear thermoplastic | - |
| turn_signal_lens, tail_light_lens (5) | Acrylic (PMMA) | Clear cast or extruded acrylic sheet | - |
| coil_spring (1) | Spring steel 1095 | Spring steel, yellow powder coat | metallic 0, coat 0.8, coat_roughness 0.15 |
| seat (1) | Leather (own) | Saddle leather over the seat foam | roughness 0.65, coat 0.2, coat_roughness 0.4 |
| tail_light_housing (1) | ABS | Acrylonitrile butadiene styrene, moulded or printed | - |
| mirror_glass (2) | Glass, soda-lime | Silvered mirror glass | metallic 1, roughness 0.03, transmission 0 |

### qdd_actuator

| Parts | Material | Description | Look beyond the catalogue's |
| --- | --- | --- | --- |
| housing, front_retainer, rear_cover (3) | Aluminum 6061-T6 | Bead-blasted 6061-T6, hard-anodised | roughness 0.6, anisotropy 0 |
| retainer_screws (8) | Steel 4140 | Alloy steel, black oxide | roughness 0.55 |
| connector_body (2) | Nylon PA6 | Polyamide 6, moulded or machined | - |
| connector_collar (2) | Steel 1018 | Connector coupling nut | - |
| connector_o_ring (2) | NBR 70A | Nitrile rubber: O-rings and oil seals | - |
| connector_pins (10) | Brass C360 | Gold-plated contacts | roughness 0.2 |
| driver_board, encoder_board (2) | FR4 | Woven glass and epoxy circuit-board laminate under green solder mask | - |
| fet, mcu, encoder_ic (8) | ABS | Acrylonitrile butadiene styrene, moulded or printed | - |
| bus_capacitor (6) | Aluminum 5052-H32 | Drawn aluminium capacitor can | - |
| encoder_magnet_ring (1) | Ferrite magnet (own) | Diametric encoder magnet ring | roughness 0.7 |
| lamination_stack (1) | Steel 1018 | Laminated electrical steel, varnished | roughness 0.5, coat 0.3, coat_roughness 0.3 |
| windings (24) | Copper C110 | Enamelled magnet wire | roughness 0.25, coat 1, coat_roughness 0.1 |
| rotor_bell (1) | Steel 1018 | Rotor bell and back iron | - |
| magnets_north, magnets_south (28) | Neodymium magnet N42 | Sintered rare-earth magnet, nickel plated; brittle, no structural values | - |
| inner_race, outer_race, balls, inner_ring, outer_ring, rollers (42) | Steel 4140 | Through-hardened bearing steel, ground | roughness 0.12 |
| sun_gear, ring_gear, planet_gear (5) | Steel 4140 | Hardened and ground gear steel | roughness 0.3 |
| planet_carrier, output_flange (2) | Aluminum 7075-T6 | High-strength zinc-alloyed aluminium for stressed parts | - |
| flexure_disc (1) | Titanium Ti-6Al-4V | Alpha-beta titanium alloy | - |
| strain_gauge (4) | Polyimide film (own) | Foil strain gauge on a polyimide carrier | roughness 0.3, coat 0.5, coat_roughness 0.1 |
| cable_tube (1) | Aluminum 6063-T5 | Extruded tube, dark anodised | roughness 0.45, anisotropy 0 |

### moonwatch case

| Parts | Material | Description | Look beyond the catalogue's |
| --- | --- | --- | --- |
| case_middle, caseback (2) | Stainless 316 | Brushed Steel | metallic 0.9, roughness 0.5, anisotropy 0.5 |
| bezel_ring, crown, crown_tube, pusher_cap, pusher_tube (7) | Stainless 304 | Polished Steel | metallic 0.92, roughness 0.12, anisotropy 0, coat 0.25, coat_roughness 0.08 |
| bezel_insert (1) | Aluminum 6061-T6 | Bezel Anodized | metallic 0.35, roughness 0.5, anisotropy 0, coat 0.2, coat_roughness 0.3 |
| tachymeter_scale (1) | Matte Print (own) | printed ink, no density | roughness 0.85 |
| crystal (1) | Glass, soda-lime | Glass | roughness 0.05, coat 0.3, coat_roughness 0.05 |
| crystal_gasket, caseback_o_ring, crown_o_ring, pusher_o_ring (5) | NBR 70A | Rubber (nitrile gaskets) | - |
| caseback_sapphire (1) | Sapphire (own) | Glass Back | roughness 0.03, ior 1.77, transmission 1, coat 0.02, coat_roughness 0.05 |
| caseback_retaining_ring, pusher_spring, spring_bar (5) | Stainless 304 | Satin Steel | metallic 0.95, anisotropy 0.4, coat 0.1, coat_roughness 0.2 |
| case_polish (6) | Stainless 316 | Case Mirror Polish | metallic 0.95, roughness 0.08, anisotropy 0, coat 0.3, coat_roughness 0.05 |

The bezel ring's snailed top annulus is painted Satin Steel (`paint(ring, faces, ...)`), as before.

### moonwatch movement

| Parts | Material | Description | Look beyond the catalogue's |
| --- | --- | --- | --- |
| main_plate, barrel_bridge, barrel_bridge:stripe_shadow, train_bridge, train_bridge:stripe_shadow, pallet_bridge, pallet_bridge:stripe_shadow, balance_cock, +5 more (29) | Brass C360 | Copper Plate | metallic 0.9, roughness 0.34, coat 0.08, coat_roughness 0.3 |
| plate_jewel, balance_lower_cap_jewel, bridge_jewel, pallet_stone, impulse_jewel, shock_cap_jewel (20) | Ruby (own) | Ruby Jewel (synthetic corundum) | roughness 0.05, ior 1.77, transmission 0.8, coat 1, coat_roughness 0.03 |
| balance_lower_shock_ring, barrel_arbor, center_pinion, third_pinion, fourth_pinion, escape_pinion, crown_wheel, crown_wheel_core, +21 more (39) | Stainless 304 | Polished Steel | metallic 0.92, roughness 0.12, anisotropy 0, coat 0.25, coat_roughness 0.08 |
| barrel_drum, barrel_lid, chaton, shock_chaton (11) | Brass C360 | Brass Part | metallic 0.9, roughness 0.38 |
| mainspring, hairspring, hairspring_stud, minute_jumper_spring, hour_recorder_friction_spring, clutch_spring, operating_spring, reset_spring, +1 more (9) | Stainless 304 | Satin Steel | metallic 0.95, anisotropy 0.4, coat 0.1, coat_roughness 0.2 |
| center_wheel, third_wheel, fourth_wheel, ratchet_wheel, escape_wheel, balance_wheel, timing_screw, minute_wheel, +6 more (29) | Brass C360 | Gilt Wheel | metallic 0.95, roughness 0.24, coat 0.1, coat_roughness 0.15 |
| barrel_bridge:anglage, train_bridge:anglage, pallet_bridge:anglage, chrono_bridge:anglage, coupling_bridge:anglage (11) | Brass C360 | Anglage Mirror | metallic 0.9, roughness 0.1, coat 0.2, coat_roughness 0.08 |
| screw (31) | Steel 1018 | Blued Screw | metallic 0.9, roughness 0.14, coat 0.25, coat_roughness 0.08 |
| click, click_spring, pallet_fork, setting_lever, yoke, setting_lever_spring, column_wheel, minute_jumper, +4 more (12) | Steel 1018 | Grained Lever | metallic 0.95, roughness 0.34, anisotropy 0.5, coat 0.08, coat_roughness 0.2 |

### tom

| Parts | Material | Description | Look beyond the catalogue's |
| --- | --- | --- | --- |
| direct_servo_clamp, shoulder_yaw_yoke, shoulder_pitch_yoke, elbow_roll_link_plate_pair, servo6_fixed_gripper_frame, gripper_right_claw, servo_carrier_5052_3p175mm (10) | Aluminum 5052-H32 | 5052-H32 mill finish | metallic 0.85, roughness 0.45 |
| shoulder_yaw_servo_detail_01, shoulder_yaw_servo_detail_02, shoulder_yaw_servo_detail_03, shoulder_yaw_servo_detail_04, shoulder_yaw_servo_detail_05, shoulder_yaw_servo_detail_06, shoulder_yaw_servo_detail_10, shoulder_yaw_servo_detail_12, +106 more (498) | none | | |
| clamp_standoff_screw_1, shoulder_yaw_yoke__hex_socket_m3x6__03, shoulder_yaw_yoke__hex_socket_m3x6__04, shoulder_yaw_yoke__hex_socket_m3x6__05, shoulder_yaw_yoke__hex_socket_m3x6__08, shoulder_yaw_yoke__hex_socket_m3x6__09, shoulder_yaw_yoke__hex_socket_m3x6__10, shoulder_yaw__hex_socket_m3x6__center, +56 more (102) | Steel 4140 | Black oxide alloy steel | metallic 0.72, roughness 0.6 |
| innomaker_pcb_32mm_27mm_pattern, Board (2) | FR4 | FR-4 solder mask | metallic 0.05, roughness 0.55 |
| camera_rear_m2_nut_1 (4) | Stainless 304 | 304 stainless | metallic 0.92, anisotropy 0 |
| base_m2p5_10mm_spacer_1, pi_hat_m2p5_10mm_spacer_1, carrier_m2p5_20mm_spacer_1 (12) | Brass C360 | Brass | metallic 0.9, roughness 0.34 |

The vendor parts (servo bodies and details, the Pi and HAT components, the thumbscrew) carry
none, as in the original: its finishes go by the arm's own colours, and only the two boards
are named by label.

### examples

| Parts | Material | Description | Look beyond the catalogue's |
| --- | --- | --- | --- |
| l_bracket_gussets_two_hole_directions (1) | Steel 1018 | Low-carbon mild steel, cold drawn bar | - |
| electronics_enclosure_base (1) | ABS | Acrylonitrile butadiene styrene, moulded or printed | - |
| base_plate, cylindrical_peg, square_block, cone, sphere, triangular_prism, cube_top_pad, cube_top_left_post, +2 more (11) | Aluminum 6061-T6 | Bead-blasted 6061-T6, colour-anodised | roughness 0.6, anisotropy 0 |

## Checks

- **Element names**: per instance (part name, face, edge and vertex names, tree), each after
  document against the same model built at df59c1563: identical for all of them -- motorbike
  (46 instances), qdd_actuator (153), case (29), movement (191), tom (628), and the three
  examples. The case's tachymeter text has two variants across cold builds of one source;
  in the other one that part's element names differ (below).
- **Cold vs warm bytes** (a cold build of df59c1563's sources, the branch's sources built warm
  on it, a cold build of the branch's sources in a fresh daemon, and a warm no-op): identical
  for motorbike, qdd_actuator and the three examples, and for the case whenever its cold build's
  text came out as the warm one's. The movement and tom differ in one part's geometry each, which
  OCCT makes differently in every cold build of the same source (the warm build replays the first
  cold build's through the kernel memo): the movement's `column_wheel` (six cold builds, six
  BReps, three of them of df59c1563's sources) and tom's `shoulder_pitch_yoke` (three cold
  builds, three BReps); warm and cold structures (names, colours, materials, tree) are
  identical. The case's `tachymeter_scale` (a `bd.Text` sketch) comes out in two BReps across
  cold builds of the same source (three of four in one, here), the second with other element
  names for that part; the cold build whose text matched is byte-identical to the warm build.
  None of this is a material effect. Every warm no-op rewrote nothing.
- **Every material name resolves**: each model builds, and the moonwatch's label assignments
  raise naming a label no leaf carries; none did.
- **Engine suite**: `scripts/test/test-python.sh tests/python/packages/cadgen/engine` at
  ed71c1372: 206 tests, OK (no engine code changed).

## What the pictures show

- Metals read as metal in every shaded mode: the case's polished crown, pushers and lugs, the
  motorbike's exhaust and lamp shells, the qdd's output flange now reflect the studio
  instead of wearing the theme's matte finish; painted parts (the motorbike's frame and
  bodywork, the shock spring) are dielectric under a coat.
- In Solid and Shaded, an up-facing metallic face of a light or saturated colour washes toward
  cream or yellow: the movement's copper-gilt main plate and bridges, the gilt wheels, and the
  qdd's orange retainer. The studio reflection the viewer gives authored finishes (0.5) and the
  theme's key light saturate the red channel there; side faces and Preview keep the authored
  hue. The finishes keep the original's roughness (0.24-0.38 for the gilt and copper parts); the
  qdd's anodised aluminium is bead-blasted at 0.6, which keeps the orange closer.
- The transmissive parts (the motorbike's PMMA and polycarbonate lenses, the crystal, the
  sapphire back, the rubies) show what lies behind them through their tint, so the turn-signal
  lenses read darker amber than the opaque amber they were drawn in before.
- The motorbike's mirror glass is catalogue glass silvered (metallic, no transmission): a mirror
  reflects; as transmissive glass it would show the dark shell behind it.
