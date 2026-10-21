# moonwatch models (case and movement)

The two main documents of `models/moonwatch`, ported to `cadgen.engine`:

| Script            | Artifact                                  | Description                                                     |
|-------------------|-------------------------------------------|-----------------------------------------------------------------|
| case.py           | XBF/case.xbf                              | Case cluster: middle, bezel stack, crystal, caseback, crown, pushers, lugs |
| movement.py       | XBF/movement.xbf                          | Complete movement = movement_base + keyless_works + chrono_works (sub-assemblies) |
| movement_base.py  | XBF/movement_base.xbf (built alone)       | Main plate, barrel, going train, bridges, escapement, balance  |
| keyless_works.py  | XBF/keyless_works.xbf (built alone)       | Stem, winding/sliding pinions, setting lever, yoke, motion works |
| chrono_works.py   | XBF/chrono_works.xbf (built alone)        | Column wheel, levers, lateral clutch, runners, hammers          |

Build from this folder: `python -m cadgen.engine build case.py movement.py`.

## Materials

The original's `materials=` finishes (`lib/materials.py`) are `Material`
values set on the parts before they are composed, by the same leaf-label
assignments (`apply_materials`). Each finish keeps the catalogue's name and
density where the metal or glass is a catalogue one, with the finish's own
metallic/roughness and the old finish name as the description:

| Old finish | Material |
| --- | --- |
| brushed_steel, case_mirror_polish, polished_steel, satin_steel | catalogue `stainless_steel` (X5CrNi18-10) |
| blued_screw, grained_lever | catalogue `steel` (Steel-Generic) |
| brass_part, gilt_wheel, copper_plate, anglage_mirror | catalogue `brass` (Copper-464) |
| bezel_anodized | catalogue `aluminium` |
| glass | catalogue `glass` |
| glass_back | `Sapphire`, 3980 kg/m^3 |
| ruby_jewel | `Ruby`, 3980 kg/m^3 |
| rubber | `NBR`, 1000 kg/m^3 |
| matte_print | `Matte Print`, no density |

Every finish has `color=None`, so parts keep their authored colours; the old
clearcoat factors have no `Material` field and are dropped. One face finish is
painted: the bezel ring is polished steel and its snailed top annulus satin
(`paint(ring, faces, FINISHES["satin_steel"])` in `case.py`). The
chronograph works had no assignments and still have none.

## Not ported

`moonwatch.py` (the full watch: its `KINEMATICS` mates and couplings and its
`ANIMATION` clips), `dial.py`, `bracelet.py` and `finishing_sampler.py`. The
animation is dropped by decision (the xbf has no animation).
