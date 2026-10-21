# motorbike models

`motorbike.py` is the one model (`@xbf`, into `../XBF/motorbike.xbf`). Every other
`.py` directly under `src/` is one part of it: a `@memo` function the assembly
composes by CALLING it (`frame()`, `seat()`, …). Each runs in the assembly's build
as a sub-assembly, writes no file of its own, and on a later build whose edit does
not reach it is replayed from the worker's memory instead of run; a part called
twice is one prototype placed twice. Shared code lives in `src/lib/` and is never
marked.

| Script               | What it is                | Description                                              |
|----------------------|---------------------------|----------------------------------------------------------|
| motorbike.py         | XBF/motorbike.xbf         | Full assembly: 23 occurrences of 21 parts, build123d joints |
| frame.py             | `@memo` part              | Welded underbone frame + steel floor pan (the fixed root) |
| center_stand.py      | `@memo` part              | Center stand, folded-up pose under the floor pan          |
| front_fork.py        | `@memo` part              | Telescopic fork + chrome axle on the steering axis        |
| handlebar.py         | `@memo` part              | Bar, stem collar, grips and levers; owns `MIRROR_MOUNT_*` |
| front_fender.py      | `@memo` part              | Cream arc band over the front tire                        |
| front_wheel.py       | `@memo` part              | Tire + five-spoke cast rim + brake disc                   |
| rear_wheel.py        | `@memo` part              | Tire + five-spoke cast rim + drum hub                     |
| engine.py            | `@memo` part              | Unit powertrain doubling as the swingarm (CVT + covers)   |
| exhaust.py           | `@memo` part              | Head pipe + oval chrome muffler, rider right              |
| rear_shock.py        | `@memo` part              | Coilover between the frame lug and the engine boss        |
| leg_shield.py        | `@memo` part              | Cream step-through apron                                  |
| steering_cover.py    | `@memo` part              | Head-tube shroud above the apron                          |
| under_seat_body.py   | `@memo` part              | Rear body panel carrying seat and tail                    |
| rear_fender.py       | `@memo` part              | Cream arc over the rear tire                              |
| seat.py              | `@memo` part              | Brown saddle                                              |
| headlight.py         | `@memo` part              | Chrome shell + clear dome lens                            |
| tail_light.py        | `@memo` part              | Housing + red lens                                        |
| turn_signal_left.py  | `@memo` part              | Front-left amber signal; the assembly links it 2x (rear-left is `Pos * turn_signal_left()`) |
| turn_signal_right.py | `@memo` part              | Its mirror image (same `lib/trim.py` factory, right side); linked 2x the same way |
| mirror_left.py       | `@memo` part              | Left bar-end mirror at `handlebar.MIRROR_MOUNT_LEFT` (a constant from another file: tracked by value) |
| mirror_right.py      | `@memo` part              | Its mirror image at `handlebar.MIRROR_MOUNT_RIGHT` — its own part, because a reflection is new geometry |

Build from `src/`: `python -m cadgen.engine build motorbike.py`. A part script
defines no model and builds nothing on its own. No imported sources — every
artifact is generated.

## `src/lib/` — shared code, no models, no `@memo`

| Module          | Role                                                                 |
|-----------------|----------------------------------------------------------------------|
| `lib/spec.py`   | **Single source of truth**: coordinates, palette, every hardpoint     |
| `lib/lib.py`    | Geometry vocabulary: revolved bands, lofts, swept tubes, coils        |
| `lib/wheels.py` | `build_front_wheel` / `build_rear_wheel`                              |
| `lib/chassis.py`| `build_frame` / `build_center_stand`                                  |
| `lib/frontend.py`| `build_front_fork` / `build_handlebar` / `build_front_fender`        |
| `lib/drivetrain.py`| `build_engine` / `build_exhaust` / `build_rear_shock`              |
| `lib/bodywork.py`| leg shield, steering cover, under-seat body, rear fender, seat       |
| `lib/trim.py`   | headlight, tail light, turn signal, mirror (`build_mirror(side, base)`) |

Every builder authors geometry DIRECTLY in the bike frame from `lib/spec.py`,
so a part entry and the assembly place identical geometry and the assembly
composes its children at identity. No builder restates a shared dimension.
The one hardpoint outside `lib/spec.py` is the pair of bar-end mirror mounts,
which `handlebar.py` owns; `mirror_left.py`, `mirror_right.py` and
`motorbike.py` import them from that file. A constant imported from another
file is tracked by VALUE, so only a changed mount runs the mirrors again.

Imports need no setup — `src/` is on `sys.path` because the script lives there:

```python
from lib import spec as S
from lib import chassis as B
```

## Joints (`motorbike.py` only)

The assembly states its relationships as build123d joints on world-coincident
datums, each placed with `connect_to` at its as-built pose. The revolute ones
carry the ranges the old typed mates declared:

| Joint (fixed side)   | Fixed → moving             | Range            |
|----------------------|----------------------------|------------------|
| `head_tube_axis`     | frame → front_fork         | -40 .. 40 deg    |
| `front_axle_hub`     | front_fork → front_wheel   | ±3600 deg        |
| `engine_pivot`       | frame → engine             | -5 .. 12 deg     |
| `rear_axle_hub`      | engine → rear_wheel        | ±3600 deg        |
| `stand_bracket`      | frame → center_stand       | -76.2 .. 0 deg   |

Every other mount is a `RigidJoint` pair: handlebar, front fender and mirrors
on the fork and bar; exhaust on the engine; apron, shroud, body, seat, fenders,
lamps and signals on the frame and bodywork; the rear shock by its upper eye on
the frame lug only. Its lower eye on the engine boss is not jointed: a second
rigid mount would close a loop through the shock and weld the engine to the
frame, so `engine_pivot` could not move. The old kinematics left the shock
unmated for the same reason; it stays put while the engine swings.

One motion is in the model's code: `steer`, `head_tube_axis` swept from -20 to
20 degrees over 2 s and back (`motion(bike, "steer", sweep=..., duration=2,
loop="pingpong")`). The xbf keeps it as a definition; `cadgen xbf export
../XBF/motorbike.xbf bike.glb` bakes it into a glTF clip named `steer`.

Not ported: the old `KINEMATICS` presets (`ride`, `turned_left`, `stand_down`,
`bump`); each would be a `configuration(...)` in the body.
