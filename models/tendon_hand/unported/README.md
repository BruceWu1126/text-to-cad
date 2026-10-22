# tendon_hand: unported models

The 56 models of the old `tendon_hand` project that the port to `cadgen.engine` left
out (`../src/README.md`, "Not ported"), kept with their old sources in the layout they
had: the scripts in `src/`, and the `src/lib/` modules and JSON data they import, as
the old project had them. `src/phalanx_continuous_representative_r5.py` is the old
script of a ported model, here because `phalanx_continuous_context_r5.py` imports it.
Nothing else of the old project is kept: its validation programs, hand-off notes and
render jobs are in its history (`git log -- models/tendon_hand/validation`).

The sources are unchanged, written for cadgen before the engine. Each model reads an
input the project's code does not make; the paths below are where the scripts look,
relative to this folder. The legacy checkout's `models/assemblies/STEP/anthropomorphic_hand`
(4.7 GB) holds the frozen inputs.

## The models, by what each reads

- **The frozen palm**, `STEP/imported/palm_frame_integration.step` (8.5 MB, outside
  Git): the main metacarpal truss that `lib/palm_frame.make_palm_frame_bodies` reads,
  and the bootstrap of a cycle (`palm_frame_integration` reads
  `palm_frame_candidate_review`, which reads `palm_guide_mounts_review`, which reads
  the frozen palm). Directly or through another model's STEP (25):
  `palm_frame_review`, `palm_frame_integration`, `palm_frame_candidate_review`,
  `palm_hardware_review`, `palm_hardware_context_review`, `palm_main_rom_review`,
  `palm_little_rom_review`, `palm_main_full_rom_review`, `palm_little_full_rom_review`,
  `palm_main_comb_rom_review`, `palm_little_comb_rom_review`,
  `palm_main_final_rom_review`, `palm_final_rom_context_review`,
  `palm_guide_mounts_review`, `index_palm_guide_mounts`, `middle_palm_guide_mounts`,
  `ring_palm_guide_mounts`, `palm_guide_bank_review`, `thumb_cmc_mounts_review`,
  `thumb_base_mounts_review`, `wrist_guide_mounts_review`, `cup_guide_mounts_review`,
  `integration_review`, `hand_progress_review`, `routing_layout_review`.
- **The frozen native hand**, `STEP/imported/integration_native_base.step` (250 MB)
  and its certificate `validation/integration_native_base_certificate.json`, with the
  native archives `lib/native_integration.py` overlays (8): `hand_mechanical_candidate`,
  `hand_mechanical_candidate_r11`, `hand_mechanical_candidate_r12`,
  `hand_mechanical_candidate_r13`, `hand_native_review`, `hand_export_repair_context`,
  `phalanx_continuous_context_r5`, `cmc_carrier_relief_review`; and the clearance
  studies built on `hand_mechanical_candidate`'s STEP through the old pipeline's
  `cadgen.step_scene` (7): `palm_thumb_arm_clearance_r11`, `radial_bank_surface_review`,
  `thumb_reaction_arm_clearance_r2`, `thumb_reaction_arm_clearance_r3`,
  `thumb_reaction_arm_clearance_r4`, `thumb_reaction_arm_clearance_r5`,
  `tip_bridge_repair_context`.
- **Other frozen STEP** (6): `radial_bank_arm_repair_review`
  (`STEP/imported/rigid_clearance_inputs.step`); `phalanx_beauty_review`,
  `phalanx_beauty_context`, `phalanx_sculpt_representative_context` and
  `pulley_hub_context` (`STEP/phalanx_beauty_native/middle_proximal_frame.step`); and
  `tip_export_repair_review` (`phalanx_beauty_review`'s STEP).
- **Generated validation output** (`validation/*.json`, written by the validation
  programs, outside Git) (8): `cmc_dorsal_ring_review`, `compact_cmc_yaw_review`,
  `compact_mcp_dorsal_review` and `joint_hardware_review`
  (`secondary_hardware_diagnostic.json`), `positive_yaw_bushing_review`,
  `radial_bank_screw_clearance_candidate`, `static_clearance_relief_review`,
  `thumb_reaction_arm_clearance_r6`.
- **Broken before the port** (1): `phalanx_comb_clearance_review` imports a name
  `lib/native_integration.py` no longer defines.
- **Too slow, then failing** (1): `palm_little_review`, the fifth metacarpal, the one
  palm part made from code alone. Built in the engine it ran 19.5 minutes and failed in
  `lib/palm_frame._blend_junctions`: three junction edges at blend node 3 take no
  0.03 mm fillet, one at a time or together. Run as plain build123d it was still
  running after 15 minutes.

Porting the palm chain would read the frozen palm as `tom` reads its vendor STEP:
converted once to xbf, local and uncommitted.
