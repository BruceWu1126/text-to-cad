# moonwatch models

Every document of `models/moonwatch`, ported to `cadgen.engine`:

| Script               | What it is                                | Description                                                     |
|----------------------|-------------------------------------------|-----------------------------------------------------------------|
| case.py              | model -> XBF/case.xbf                     | Case cluster: middle, bezel stack, crystal, caseback, crown, pushers, lugs |
| dial.py              | model -> XBF/dial.xbf                     | Three-register step dial, applied indices, printed tracks, full hand stack at 10:09:38 |
| bracelet.py          | model -> XBF/bracelet.xbf                 | Flat three-link bracelet, end links, fold-over clasp            |
| finishing_sampler.py | model -> XBF/finishing_sampler.xbf        | Standing coupon exercising the shared finishing vocabulary      |
| movement.py          | model -> XBF/movement.xbf                 | Complete movement = movement_base + keyless_works + chrono_works (sub-assemblies) |
| movement_base.py     | `@memo` layer of the movement             | Main plate, barrel, going train, bridges, escapement, balance  |
| keyless_works.py     | `@memo` layer of the movement             | Stem, winding/sliding pinions, setting lever, yoke, motion works |
| chrono_works.py      | `@memo` layer of the movement             | Column wheel, levers, lateral clutch, runners, hammers          |
| moonwatch.py         | model -> XBF/moonwatch.xbf, XBF/moonwatch.glb | The full watch: case + dial/hands + movement + bracelet, its mechanism, and its four clips |

Build from this folder: `python case.py`, `python dial.py`, `python bracelet.py`,
`python finishing_sampler.py`, `python movement.py`, `python moonwatch.py`. The models'
files are the only files the builds write.

The case, dial and bracelet are plain functions in `lib/watch.py` (each with its
finishes set), which their models return and the full watch composes; `moonwatch.py`
composes the movement from the same three `@memo` layers `movement.py` does. No model
calls another.

Only the three layers are `@memo`: each holds seconds of kernel work (movement_base
alone is ~130 s cold), so an edit to one layer runs it and replays the other two, and
a leaf edit costs about 4.5 s. The groups of parts inside a layer (`lib/mvt_*.py`,
`_plate_parts` ... `_chrono_bridge_parts`) are plain functions: marking each of the
19 added nothing beyond the layers (see "When to use `@memo`" in
`models/README.md`). The case stays one body: `lib/case.py`'s band builder leaves
`_BAND_CHAMFER_APPLIED` in its module for the polish builder to read, state a replay
would not set, so no builder of the case is a `@memo`.

## Materials

The original's `materials=` finishes (`lib/materials.py`) are `Material`
values set on the parts before they are composed, by the same leaf-label
assignments (`apply_materials`). Each finish is the catalogue's entry for the
metal, glass or rubber in the finish's look (`.but(...)`), or the project's own
where the catalogue has none, with the old finish name as the description. A
finish keeps the original's metallic, roughness and clearcoat (`coat`,
`coat_roughness`), and takes the look fields its name implies:

| Old finish | Material | Look beyond the original's metallic, roughness and coat |
| --- | --- | --- |
| brushed_steel | Stainless 316 | anisotropy 0.5 (brushed) |
| case_mirror_polish | Stainless 316 | no grain |
| polished_steel | Stainless 304 | no grain |
| satin_steel | Stainless 304 | anisotropy 0.4 (satin) |
| blued_screw | Steel 1018 | |
| grained_lever | Steel 1018 | anisotropy 0.5 (straight grain) |
| brass_part, copper_plate, gilt_wheel | Brass C360 | |
| anglage_mirror | Brass C360 | roughness 0.1 (mirror; 0.14 before) |
| bezel_anodized | Aluminum 6061-T6 | no grain |
| glass | Glass, soda-lime | transmissive, ior 1.52 |
| glass_back | `Sapphire` (corundum, 3980 kg/m^3) | transmissive, ior 1.77 |
| ruby_jewel | `Ruby` (corundum, 3980 kg/m^3) | transmission 0.8, ior 1.77 |
| rubber | NBR 70A | |
| matte_print | `Matte Print`, no density | |

Every finish has `color=None`, so parts keep their authored colours, and a
transmissive part's colour is its tint. One face finish is painted: the bezel
ring is polished steel and its snailed top annulus satin (`paint(ring, faces,
FINISHES["satin_steel"])` in `lib/watch.py`). The chronograph works, which had no
assignments in the original, take the base's finishes by the same rules
(`MODEL_MATERIAL_ASSIGNMENTS["chrono_works"]`), so every part of every
document has a material.

## The full watch

`moonwatch.py` is one model composing the four in its body, the movement cased by the
flip about X and the MOVT_Z_OFFSET lift. The old `KINEMATICS` and `ANIMATION` are
build123d joints, placed where the parts stand and paired by label:

- **Mates.** The six hands turn on the dial (revolute; lume, hubs and the chrono cap
  fastened to theirs), the going train, escapement and balance in the movement
  (cylindrical: the turn, and the tier slide the showcase fans them by; pinions,
  pallet stones, arbor, staff, impulse jewel and the 16 timing screws fastened), the
  chronograph runner and coupling wheel in the chronograph works (revolute), the crown
  on the case middle (cylindrical: winding turn and setting travel) and the two pushers
  (prismatic, inward along their axes). The stem is no longer fastened to the crown: it
  stays with the movement, which the clips lift out of the case.
- **Couplings and poses.** The gear ratios are two couplings, `running` (seconds of
  time) and `chrono` (seconds of chronograph running), and the old poses are
  configurations (`one_minute`, `half_hour`, `chrono_at_10min`, `start_pressed`,
  `reset_pressed`, `winding`, `setting`; `rest` is the watch as built).
- **The clips' joints.** Each part or group the old clips translated slides on a
  prismatic joint from the case middle along its own direction (`lift_*`); the
  movement rises on one (`movement_rise`) and flips on a revolute about X through its
  centre (`movement_flip`, on an inner `caliber` compound); its tiers fan on prismatic
  joints in its own frame (`tier_*`, each group's first part the lead, the rest
  fastened to it).
- **Clips.** `running`, `reveal`, `showcase` and `grand_tour` are motions, functions of
  time over those joints, which the declared `@glb` bakes into clips (animation is a
  GLB export's alone; the xbf keeps the definitions). A joint carries one direction:
  where the old showcase moved the face stack sideways and the reveal lifted it, both
  lift it now (the showcase higher, clear of the rising movement), and the crown and
  pushers, whose joints are their own travel, stay in the case.
- **The tier fan** (`showcase`, `grand_tour`) runs as the old clips meant it. Their tier
  offsets are in the cased watch frame, negative toward the caseback (the bridge side),
  which the movement's flip turns upward; the tier and train-wheel joints slide in the
  movement's own frame, which the casing flips (watch z = MOVT_Z_OFFSET - local z), so
  each slides by the offset's negative. Fixed 2026-10-22: the first port slid them by
  the offset itself, so after the flip the bridge-side tiers fell through the main plate
  into the case and the keyless works rose into it. Now, open, the barrel, the pallet,
  train and barrel bridges, the winding, the balance cock and the chronograph works stand
  above the plate in that order (the chronograph works' top about 48 mm up, the plate's
  13-17 mm), the keyless works about 7 mm below it, and no tier meets another; the face
  stack's extra 32 mm in `grand_tour` clears the chronograph works. As in the old clip,
  the chronograph works fan as one tier: the hour recorder they carry on the dial side
  passes up through the plate and the winding tier while the fan opens and closes.

New finishes for the dial, hands and bracelet follow the same rules as the case's:
`black_lacquer` and `lume_plot` are own materials (coatings, no density),
`painted_hand` is Steel 1018, `dial_polished_furniture` and `polished_link` Stainless
316. The finishing sampler, which had no assignments, takes each coupon's movement
counterpart's finish; the movement ring the full watch adds is satin steel.
