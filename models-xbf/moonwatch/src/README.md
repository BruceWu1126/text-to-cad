# moonwatch models (case and movement)

The two main documents of `models/moonwatch`, ported to `cadgen.engine`:

| Script            | What it is                                | Description                                                     |
|-------------------|-------------------------------------------|-----------------------------------------------------------------|
| case.py           | model -> XBF/case.xbf                     | Case cluster: middle, bezel stack, crystal, caseback, crown, pushers, lugs |
| movement.py       | model -> XBF/movement.xbf                 | Complete movement = movement_base + keyless_works + chrono_works (sub-assemblies) |
| movement_base.py  | `@memo` layer of the movement             | Main plate, barrel, going train, bridges, escapement, balance  |
| keyless_works.py  | `@memo` layer of the movement             | Stem, winding/sliding pinions, setting lever, yoke, motion works |
| chrono_works.py   | `@memo` layer of the movement             | Column wheel, levers, lateral clutch, runners, hammers          |

Build from this folder: `python -m cadgen.engine build case.py movement.py`. The
two models are the only files a build writes.

Only the three layers are `@memo`: each holds seconds of kernel work (movement_base
alone is ~130 s cold), so an edit to one layer runs it and replays the other two, and
a leaf edit costs about 4.5 s. The groups of parts inside a layer (`lib/mvt_*.py`,
`_plate_parts` ... `_chrono_bridge_parts`) are plain functions: marking each of the
19 added nothing beyond the layers (see "When to use `@memo`" in
`models-xbf/README.md`). The case stays one body: `lib/case.py`'s band builder leaves
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
FINISHES["satin_steel"])` in `case.py`). The chronograph works, which had no
assignments in the original, take the base's finishes by the same rules
(`MODEL_MATERIAL_ASSIGNMENTS["chrono_works"]`), so every part of both
documents has a material.

## Not ported

`moonwatch.py` (the full watch: its `KINEMATICS` mates and couplings and its
`ANIMATION` clips), `dial.py`, `bracelet.py` and `finishing_sampler.py`. The
animation is dropped by decision (the xbf has no animation).
