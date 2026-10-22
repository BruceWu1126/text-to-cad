# radial models

`models/radial` ported to `cadgen.engine`: a nine-cylinder, single-row, supercharged,
air-cooled radial aircraft engine, modelled as a museum restoration of the 1930s-40s
archetype, unbranded, with a sectioned cylinder and a front crankcase window. One
script, `radial.py`, holds one model:

| Model | Writes | What it is |
| --- | --- | --- |
| `radial` | `XBF/radial.xbf` | The engine: eighteen systems, their mechanism, the `drive` coupling, two configurations and the `running`, `explode` and `exploded-running` motions |

Build from this folder: `python radial.py`.

```
python radial.py
python -m cadgen.engine snapshot ../XBF/radial.xbf radial.png
python -m cadgen.engine snapshot ../XBF/radial.xbf bdc.png --pose cylinder1_bdc
python -m cadgen.engine export ../XBF/radial.xbf radial.glb      # the three clips, baked
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
its first part carries the joint, paired by label with a joint on the link it turns or
slides on. Everything stands at 0 as built, the authored rest: crank angle 0, cylinder 1
at firing TDC. `lib/mechanism.py`'s `assemble` makes the engine, its joints, coupling,
configurations and motions.

- The gear train is linear in the crank angle, so it is one coupling, `drive`, in
  degrees of crank, its range the crank's limits (-1 to 721): the crank 1, the cam ring
  -1/8, the idler -2/3, the tappet rollers 3/2, the propeller shaft (the carrier, the
  propeller riding it) 2/3 and its planets 4/3, the impeller 10, the blower gears -3. A
  coupling is a constraint: its joints move through it alone, so every motion and
  configuration sets `drive`, never a gear.
- The crank train and the valvetrain are not linear in it. The master rod turns on the
  crankpin, each articulating rod on its knuckle pin, each piston slides in its bore,
  each tappet in its guide, each pushrod turns on its tappet's axle, each rocker on its
  shaft and each valve slides in its head's guide: joints of their own, each with the
  travel one cycle takes it through.
- Two configurations, each a crank angle with every joint where `kin.py` puts it:
  `cylinder1_bdc` (180 degrees, the bottom of cylinder 1's power stroke) and
  `cylinder1_overlap` (360: its TDC between exhaust and intake, both valves open).
- Three motions, each a function of time stored as its samples, which a GLB exported
  from the xbf carries as clips of their names (the original looped `running` and
  `exploded-running`; a player repeats a clip):
  - `running`, the 720-degree four-stroke cycle in 8 s: `drive` and every free joint
    from `kin.py` (`mechanism.running_values`).
  - `explode`, the original's teardown, 24 s from the rest pose with nothing turning
    (below).
  - `exploded-running`, `running` with the engine held partly apart (below).

  No decorator declares the GLB: its bake keys every moving placement at every key
  (`running` alone keyed 493 placements at 2,425 times and wrote 140 MB in 138 s, and
  the exploded clips move all 2,400), which a declared GLB would repeat on every edit.

Two approximations, where `kin.py` is exact: a pushrod turns about one fixed axis
through its tappet socket (normal to its rest and full-lift directions; it turns about
0.6 degrees), where `kin.py` turns it about the axis between its rest and current
directions; and the valve springs, which `kin.py` compresses as tubes, stay as built in
their cylinder.

### The exploded clips

The original played `explode` and `exploded-running` as JavaScript translating parts by
label. Their data is `lib/explode_plan.json`, verbatim from the original's generated
`lib/anim_js.py`: the teardown `lib/explodeplan.py` planned (1,360 units -- a part, or
parts leaving together -- each a track of smoothstep-eased straight moves, a fastener's
its own moves after its host's) and the layout `lib/explodedrun.py` made (25 groups at
constant offsets over the running cycle: the cylinders 205 mm out along their bores,
their rocker covers 60 mm further, the crankcase halves, cam section, nose and
propeller forward, the blower and accessory case back). `lib/explode.py` turns them
into joints:

- **One link per unit** (per exploded-running group, where a unit's parts are in
  several: the crankcase front half leaves with the cam ring and the nose case's rear),
  its parts fastened to its first, on a prismatic joint
  `x_<part>` along its own move, held by its host's link (a fastener rides its host, as
  it did) or by the root, the ground; units that move alike (one holder, line and
  timing) share a link, 825 moving joints in all. A slide's value
  at each instant is the old offset's projection on its line: exact wherever the
  unit's moves relative to its holder lie on one line, so 1,356 of the 1,360 units
  follow their old tracks exactly where nothing below forces otherwise; the other four
  (the three propeller blades and the collector's bottom) bend in the original, and
  take the straight line from rest to their end with the old timing.
- **The running groups keep their joints** and take their translations where those
  can be exact. A free joint whose part leaves along its own axis leaves on it: each
  piston slides out of its bore on its own joint, and the master rod's joint is a
  cylindrical one (turn, slide), so it slides off the crankpin. The gear train cannot
  slide (the coupling's joints move through it alone), so its members leave on links
  of their own: four systems' compounds slide on the root (`x_crankshaft`, `x_cam`,
  `x_reduction`, `x_blower`) as the crank, the cam ring, the propeller shaft and the
  impeller moved, holding those members' joints; and carriers, parts round about a
  member's axis on slides of their own, hold the rest: each planet turns on its bearing,
  each blower gear on its bushing. Carriers hold three free groups too: each tappet
  slides in its guide, each valve in its head's guide, each rocker turns on its needle
  bearing. A carrier no longer turns with its part (a bearing's race and needles, a
  bushing, about the part's own axis, inside it). The group's other parts that leave on
  their own ride its link on slides along its axis.
- **exploded-running** holds every slide at its offset while `drive` and the free
  joints run the cycle: exact for every shown part but the few below. The cam section
  (ring, idler, tappets) stands 310 mm forward on `x_cam`, and each pushrod rides its
  cylinder on its tappet's axle, which slides on the line from the cam section's offset
  to its cylinder's: the pushrod keeps its lift and swing, seated in its rocker, as in
  the original.

Deliberate differences, every one measured by `explode.Plan.report()` (a link off the
old clips by more than 0.5 mm):

- The teardown, after a part's own move: the tappet guides, tappets and rollers ride
  the cam section 840 mm forward when the cam ring leaves (17.3 s), 6 s after they came
  out (so the tappets stand on the cam ring in exploded-running); the rocker shafts and
  their washers and nuts ride their cylinders out (up to 510 mm) after being drawn (so
  they stay in the rockers' bosses in exploded-running); the propeller blades ride the
  propeller shaft's carrier 1,580 mm forward when it leaves (3.6 s), as no part of the
  turning propeller stays put.
- The pushrods: a pushrod turns on its tappet's axle and cannot also slide, and its old
  draw (up its own length, before its tappet) is not on the axle's line, so it leaves
  with its tappet and along the axle's line, 400 to 700 mm from its old track at worst.
  The axle (a 25 mm pin) goes with the pushrod, in both exploded clips.
- In exploded-running, fasteners whose old layout group is not their host's stay with
  their hosts: 16 exhaust-flange nuts with the exhaust (205 mm off their cylinders'
  offset), and the rear crankcase drain and two scavenge bolts and their wires (62 to
  360 mm).
- The 466 parts the original hid in exploded-running (the propeller blades, the
  ignition, exhaust, intake, mount, pushrod tubes, crankcase through-bolts and fuel
  line) are shown: a motion cannot hide a part. Each stays where its link puts it, at
  rest unless it rides a moving link (the blades turn with the propeller shaft, 430 mm
  forward), so the cylinders stand clear of their exhaust stacks, intake pipes and
  leads.
- The springs' compression and the old clips' display labels ("Exploded view
  (teardown)") have no engine equivalent.

The original's `lib/explodeplan.py`, `explodegeo.py`, `explodelayout.py`,
`explodedrun.py` and `explodecheck.py` (the planner that made the data, from the old
pipeline's built geometry, and its checks) are not ported: the data is kept as they
left it.

## Not ported

- The valve springs' compression (a tube deformation, no joint's).
- The clips' looping: a motion has no loop, a player repeats a clip.
- The validation tools (`lib/gate.py`, the interference gate; `lib/animcheck.py`, the
  clips against `kin.py`) and `tools/` (`engine.py`, which serialised builds and
  renders; `critic_pack.py`): they read the old pipeline's stored documents and
  animation bake. `lib/clips.py` and `lib/systems.py` (the clip contract and the
  systems' order) live on in `lib/mechanism.py` and `radial.py`.
- The systems' own STEP files, and `render/presentation.json` (the old renderer's rig).

The original's hand-off notes are kept beside this folder: [`REPORT.md`](../REPORT.md),
[`BUILDING.md`](../BUILDING.md), [`GAUNTLET.md`](../GAUNTLET.md), [`BUGS.md`](../BUGS.md).
