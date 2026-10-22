# radial models

`models/radial` ported to `cadgen.engine`: a nine-cylinder, single-row, supercharged,
air-cooled radial aircraft engine, modelled as a museum restoration of the 1930s-40s
archetype, unbranded, with a sectioned cylinder and a front crankcase window. One
script, `radial.py`, holds one model:

| Model | Writes | What it is |
| --- | --- | --- |
| `radial` | `XBF/radial.xbf` | The engine: eighteen systems, their mechanism, the `drive` coupling, two configurations and the `running` motion |

Build from this folder: `python radial.py`.

```
python radial.py
python -m cadgen.engine snapshot ../XBF/radial.xbf radial.png
python -m cadgen.engine snapshot ../XBF/radial.xbf bdc.png --pose cylinder1_bdc
python -m cadgen.engine export ../XBF/radial.xbf radial.glb      # the running clip, baked
```

## How it is built

`lib/` holds the system builders and the shared modules (`spec.py`, the source of
truth for the frame and every number, with its sources; `kin.py`, every motion;
`geo.py`, `castings.py`, `fasteners.py`, `palette.py`), unchanged but for
`import build123d as bd`, `lib/color.py`'s `srgb` and the two changes below.

The old project was nineteen models: one per system, each with its own STEP and
worker, which `radial.py` called. Here the systems are `@memo` stages of the one model,
in occurrence (and explode) order:

    crankcase  crankshaft  rods  pistons  barrels  heads  valvetrain  cam  pushrods
    nose  reduction  propeller  blower  intake  accessory  ignition  exhaust  mount

Each is `lib/<system>.py`'s `build()`. An edit that does not reach a system's code
replays it from the worker's memory, so an edit costs its system and the root -- and
three more: `barrels`, `cam` and `propeller` run calls the memo cannot key (132 of
them in the barrels: shapes made past build123d's operations, and what is made of
them), so they never replay and run again, every call they can key a hit, whenever the
root does.

Measured on 2026-10-22 (one machine, its load 4-17 from other work): cold 3,195 s
(53 min; 19 bodies, 28,105 kernel calls; the xbf 70 MB: 473 parts in 2,400 placements,
122 pairs). A no-op takes 0.4 s and rewrites nothing. An edit to the mount ring's tube
radius (`lib/mount.py`, 14 -> 14.5 mm) runs `mount`, the root and the three above, and
replays the other 14 stages: 48.8 s. Reverting it takes 46.7 s and writes the same bytes
on every revert, but not the cold build's: the warm re-runs of `barrels` and
`propeller` write other bytes than their cold runs (alone in a model each, the
barrels' document is 569 bytes longer, with the same prototypes, names, keys,
placements and closure), where `cam`'s keep theirs. An engine gap, not a port choice.

- **Materials.** Every part has one of the palette's colours, and its system gives it
  the finish that colour names: `lib/palette.py`'s `FINISHES`, a catalogue material
  (aluminium 6061, steels 1018 and 4140, spring steel, stainless 304, copper, brass, NBR)
  in the original finish's look -- its metalness, roughness and clearcoat as
  `metallic`, `roughness`, `coat` and `coat_roughness`, its name the description, no
  grain -- or the project's own where the catalogue has none (section paint, bronze,
  gasket, alumina). Every finish keeps `color=None`, so a part stays the colour its
  builder gave it. The original grouped each system's parts by finish
  (`<system>__<finish>` compounds) for its decorator's `materials=` to target; the parts
  sit directly under their system now.
- **The heads' STEP check.** `heads.py` checks that its section cut and every head
  casting survive a STEP round trip (a cut face once came back from STEP as a
  metre-long runaway surface). The original wrote a temporary file and read it back with
  build123d's `import_step`, a read past the engine that would run the heads on every
  build; the same writer writes to a buffer now, and OCCT's reader reads it back from
  there.

## Kinematics

`lib/mechanism.py` authors build123d joints on the built systems' parts, chosen by the
original's label contract (`BUILDING.md`, "Moving parts: labels are the contract"):
every part labelled `<group>:<part>` whose group is a motion group (`crank`, `master`,
`artrod2`-`9`, `piston1`-`9`, `camring`, `camidler`, `tappet<k><v>` and its roller,
`pushrod<k><v>`, `rocker<k><v>`, `valve<k><v>`, `planet1`-`6`, `propshaft` and the
propeller, `impeller`, `blowergear1`-`3`) moves with that group. Each group is a link:
its first part carries the joint, paired by label with a joint on the part it turns or
slides on, and its other parts are fastened to it. Everything stands at 0 as built, the
authored rest: crank angle 0, cylinder 1 at firing TDC.

- The gear train is linear in the crank angle, so it is one locked coupling, `drive`,
  0-720 degrees of crank: the crank 1, the cam ring -1/8, the idler -2/3, the tappet
  rollers 3/2, the propeller shaft (the carrier, the propeller riding it) 2/3 and its
  planets 4/3, the impeller 10, the blower gears -3.
- The crank train and the valvetrain are not linear in it. The master rod turns on the
  crankpin, each articulating rod on its knuckle pin, each piston slides in its bore,
  each tappet in its guide, each pushrod turns on its tappet, each rocker on its shaft
  and each valve slides in its guide: joints of their own, each with the travel one
  cycle takes it through.
- Two configurations, each a crank angle with every joint where `kin.py` puts it:
  `cylinder1_bdc` (180 degrees, the bottom of cylinder 1's power stroke) and
  `cylinder1_overlap` (360: its TDC between exhaust and intake, both valves open).
- `running` is the 720-degree four-stroke cycle in 8 s, looped: a function of time that
  sets `drive` and every free joint from `kin.py` (`mechanism.running_values`), stored
  as its samples. A GLB exported from the xbf carries it as a clip of that name. No
  decorator declares the GLB: its bake keys all 493 moving placements at 2,425 times
  (the samples' spline holds every angle to 1e-3 rad), and the export took 138 s and
  wrote 140 MB, which a declared GLB would repeat on every edit.

Two approximations, where `kin.py` is exact: a pushrod turns about one fixed axis
through its tappet socket (normal to its rest and full-lift directions; it turns about
0.6 degrees), where `kin.py` turns it about the axis between its rest and current
directions; and the valve springs, which `kin.py` compresses as tubes, ride nothing.

## Not ported

- The `explode` teardown and the `exploded-running` clip: they translate 1,360 units
  and groups apart, which no joint does (the viewer's Explode shows the parts apart).
  With them go `lib/explode_plan.json` (the teardown `lib/explodeplan.py` planned from
  the built engine) and `lib/explodeplan.py`, `explodegeo.py`, `explodelayout.py`,
  `explodedrun.py` and `explodecheck.py`.
- The valve springs' compression (a tube deformation, no joint's).
- The validation tools (`lib/gate.py`, the interference gate; `lib/animcheck.py`, the
  clips against `kin.py`) and `tools/` (`engine.py`, which serialised builds and
  renders; `critic_pack.py`): they read the old pipeline's stored documents and
  animation bake. `lib/clips.py` and `lib/systems.py` (the clip contract and the
  systems' order) live on in `lib/mechanism.py` and `radial.py`.
- The systems' own STEP files, and `render/presentation.json` (the old renderer's rig).
- The hand-off notes (`REPORT.md`, `BUILDING.md`, `GAUNTLET.md`, `BUGS.md`) stay in
  `models/radial`.
