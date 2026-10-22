# hypercar models

`models/hypercar` ported to `cadgen.engine`: a mid-engine hypercar whose body
panels are cut from one master surface. `hypercar.py` is the one model:

| Script | Writes | What it is |
| --- | --- | --- |
| `hypercar.py` | `XBF/hypercar.xbf`, `XBF/hypercar.glb` | The car: 13 system stages, 706 parts, the dihedral doors' mechanism, the `doors` motion (a clip of the GLB) |
| `body.py` ... `details.py` (13) | nothing | One system each, a `@memo` stage the car calls in its body |

Build from this folder: `python hypercar.py`.

```
python hypercar.py
python -m cadgen.engine snapshot ../XBF/hypercar.xbf open.png --pose open
python -m cadgen.engine snapshot ../XBF/hypercar.xbf half.png --pose '{"doors": 0.5}'
cadgen export ../XBF/hypercar.xbf half.step --pose '{"doors": 0.5}'
```

## How it is built

`lib/` holds the part builders, one module per system (each `build()` returns the
system's labelled group), plus `surfaces.py` (the one master body surface every
panel is cut from), `palette.py` (colours, authored as sRGB hex) and `context.py`
(`group`, `style`, and `compound_from_instances`, which places a prototype N
times as `prototype.moved(location)` where the original called cadgen's). The
builders are unchanged but for `import build123d as bd` and the wheels: each corner
places its axle's wheel prototype (`base * leaf`) instead of baking a world-space
copy of it, which the old viewer needed and an xbf does not.

The order of `SYSTEMS` in `hypercar.py` is the occurrence order:

    body   glazing   lighting   chassis
    suspension_front   suspension_rear   wheels   brakes
    powertrain   interior   aero   hinge   details

Each system is a `@memo` function in its own file (`body.py`: `body()` returns
`lib/body.py`'s group): a stage of the car's build that an edit elsewhere replays
from the worker's memory. Every one holds more than a thousand kernel calls,
counted on a warm worker by running each alone (`wheels` 23,875, `details` 9,923,
`lighting` 4,019, `body` 3,856, `interior` 3,281, `suspension_front` 2,769,
`chassis` 2,457, `glazing` 1,991, `brakes` 1,758, `suspension_rear` 1,724, `aero`
1,573, `hinge` 1,522, `powertrain` 1,253; the car's own body, one), so each saves
its share of the 60,000 hits a re-run of the whole car would take.

Measured on 2026-10-22 (one machine, its load 5-25 from other work): cold 186-211 s
(the bodies 162-183 s, the GLB 15-17 s); a no-op 1.3-1.4 s, both files kept; the old
benchmark's edit (`LOWER_ARM`'s first point 22 -> 23 in `lib/hinge.py`) 3.6-4.5 s,
`hinge` and the car run and 12 stages replayed. The edited xbf and GLB are the bytes
a cold build of the edited code writes, and reverting the edit writes the first
cold build's bytes again.

## Kinematics

The dihedral synchro-helix doors. Each door is a build123d `CylindricalJoint` on
the chassis group -- rotation and axial travel about the same tower axis, from
`lib/hinge.py`'s constants -- and a `RigidJoint` of the same label on the door
skin, where it stands shut: the parts live in different systems' groups, so
`connect_to` cannot place them, and the label pairs them as built (shut, 0 and 0).
Everything that rides the door (side glass, door cards and pull, the mirror's five
parts, the hinge's door bracket and lower lug) is fastened to it by a pair of
`RigidJoint`s of one label, which makes it part of the door's link.

| Joint | Range | Rides it |
| --- | --- | --- |
| `door_left` | turn -62 .. 0 deg, travel 0 .. 310 mm | the left door and its 11 riders |
| `door_right` | turn 0 .. 62 deg, travel 0 .. 310 mm | the right door and its 11 riders |

The `doors` coupling drives both from one 0..1 control: `couple(car, "doors",
door_left=(-62, 310), door_right=(62, 310))`, the old `doors` coupling. The old
poses are `shut`, as built, and the configuration `open` (`doors=1`). The old
`doors` clip is the motion `doors`, the coupling swept 0 to 1 over 3.5 s and back
(`loop="pingpong"`, a 7 s clip in the GLB).

## Not ported

- The `showcase` and `explode` clips: they translate whole systems and part
  groups apart, which no joint does, and the viewer's Explode shows the parts
  apart.
- `render/presentation_theme.json` and `presentation_display.json`, the old
  snapshot's theme and edge styling: an xbf is drawn in the engine's one look.
