# motorbike models

Every `.py` directly under `src/` is a model (`@xbf` + the `@step` the
original declared, both into `../XBF/`). Shared code lives in `src/lib/` and is
never a model. The assembly composes its children by CALLING the sibling models
(`frame()`, `seat()`, …): each child runs in the assembly's build as a
sub-assembly, and a child called twice is one prototype placed twice. A child
built on its own writes its own xbf and STEP.

| Script               | Artifact                  | Description                                              |
|----------------------|---------------------------|----------------------------------------------------------|
| motorbike.py         | XBF/motorbike.xbf      | Full assembly: 25 linked children (23 models), build123d joints |
| frame.py             | XBF/frame.xbf          | Welded underbone frame + steel floor pan (the fixed root) |
| center_stand.py      | XBF/center_stand.xbf   | Center stand, folded-up pose under the floor pan          |
| front_fork.py        | XBF/front_fork.xbf     | Telescopic fork + chrome axle on the steering axis        |
| handlebar.py         | XBF/handlebar.xbf      | Bar, stem collar, grips and levers; owns `MIRROR_MOUNT_*` |
| front_fender.py      | XBF/front_fender.xbf   | Cream arc band over the front tire                        |
| front_wheel.py       | XBF/front_wheel.xbf    | Tire + five-spoke cast rim + brake disc                   |
| rear_wheel.py        | XBF/rear_wheel.xbf     | Tire + five-spoke cast rim + drum hub                     |
| engine.py            | XBF/engine.xbf         | Unit powertrain doubling as the swingarm (CVT + covers)   |
| exhaust.py           | XBF/exhaust.xbf        | Head pipe + oval chrome muffler, rider right              |
| rear_shock.py        | XBF/rear_shock.xbf     | Coilover between the frame lug and the engine boss        |
| leg_shield.py        | XBF/leg_shield.xbf     | Cream step-through apron                                  |
| steering_cover.py    | XBF/steering_cover.xbf | Head-tube shroud above the apron                          |
| under_seat_body.py   | XBF/under_seat_body.xbf| Rear body panel carrying seat and tail                    |
| rear_fender.py       | XBF/rear_fender.xbf    | Cream arc over the rear tire                              |
| seat.py              | XBF/seat.xbf           | Brown saddle                                              |
| headlight.py         | XBF/headlight.xbf      | Chrome shell + clear dome lens                            |
| tail_light.py        | XBF/tail_light.xbf     | Housing + red lens                                        |
| turn_signal_left.py  | XBF/turn_signal_left.xbf| Front-left amber signal; the assembly links it 2x (rear-left is `Pos * turn_signal_left()`) |
| turn_signal_right.py | XBF/turn_signal_right.xbf| Its mirror image (same `lib/trim.py` factory, right side); linked 2x the same way |
| mirror_left.py       | XBF/mirror_left.xbf    | Left bar-end mirror at `handlebar.MIRROR_MOUNT_LEFT` (a constant from a model file: tracked by value) |
| mirror_right.py      | XBF/mirror_right.xbf   | Its mirror image at `handlebar.MIRROR_MOUNT_RIGHT` — its own model, because STEP cannot express a reflection |

Build from `src/`: `python -m cadgen.engine build motorbike.py` (the whole
bike), or any child script on its own. No imported sources — every artifact is
generated.

## `src/lib/` — shared code, no models

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
`motorbike.py` import them from the model file. A constant imported from a
model file is tracked by VALUE, so only a changed mount makes the mirrors stale.

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
lamps and signals on the frame and bodywork; the rear shock rigidly on both the
frame lug and the engine boss.

Not ported: the old `KINEMATICS` presets (`ride`, `turned_left`, `stand_down`,
`bump`): build123d joints have no poses.
