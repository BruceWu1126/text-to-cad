# f14d models

`models/f14d` ported to `cadgen.engine`: a Grumman F-14D Super Tomcat, wings at
20 degrees, canopy closed, gear down, a clean airframe. `f14d.py` is the one model:

| Script | Writes | What it is |
| --- | --- | --- |
| `f14d.py` | `XBF/f14d.xbf` | The aircraft: the ten systems below, in occurrence order |
| `airframe.py` ... `details.py` (10) | nothing | One system each the aircraft calls in its body: nine `@memo` stages and `nozzles`, a plain function |

Build from this folder: `python f14d.py`.

```
python f14d.py
python -m cadgen.engine snapshot ../XBF/f14d.xbf f14d.png
```

## How it is built

`lib/` is the shared part library, one module per system, each exporting
`build()`, unchanged but for `import build123d as bd` and four things:

- `compound_from_instances` is `lib/context.py`'s (the instanced fastener, wick
  and perforation rows, each a prototype placed N times as
  `prototype.moved(location)`), where the original imported cadgen's. The old
  `inlets.py` imported cadgen's under a `try` and dropped its bleed perforations
  and fasteners when it failed; it imports `lib/context.py`'s now.
- `lib/__init__.py` makes `lib` a package. As a namespace package (no
  `__init__.py`, as in the original) it was never imported afresh: the engine
  drops a build's first-party modules by their file, and a namespace package
  has none, so `from lib import wings` handed the next build the previous
  build's module, `lib/*.py` fell out of the model's closure, and an edit to one
  went unseen.
- `geometry._slopes` kept its tangents in a module-level dict keyed by `id()`,
  which the model's closure digests: every build saw `lib/geometry.py` change and
  re-ran nine bodies. It computes them each call now (a handful of floats).
- `nozzles._revolve` revolves the profile's face: `bd.make_face` hands back a
  sketch (a compound), which `Solid.revolve` cannot downcast, and the original
  then ran OCCT's revol builder itself, past the engine's memo, so every call
  after it ran uncached. The 366 nozzle parts come out the same (labels and
  volumes).

The order of `SYSTEMS` in `f14d.py` is the occurrence order:

    airframe   cockpit   wings   inlets   nozzles
    empennage   aft   nose_gear   main_gear   details

Each system is a function in its own file (`wings.py`: `wings()` returns
`lib/wings.py`'s group). Nine are `@memo` stages that an edit elsewhere replays
from the worker's memory, each seconds of kernel work cold (load 25-70):
`airframe` 574 s, `wings` 22 s, `empennage` 13 s, `nose_gear` 5.7 s, `details`
5.4 s, `cockpit` 5.0 s, `main_gear` 3.4 s, `inlets` 3.3 s, `aft` 2.4 s; 31,686
kernel calls in all. `nozzles` holds 0.5 s and is a plain function: whenever the
aircraft's body runs, the kernel memo replays its calls.

Which skin cutters the airframe applies is decided in `airframe.py`, as before: the
diverter slots and the cockpit opening; the 41 cosmetic panel recesses stay dropped.

## Measured

On 2026-10-22, one machine (10 cores), its load 25-70 from other work:

| Build | Time | What ran |
| --- | --- | --- |
| cold | 644 s | every body; the airframe skin's cut 574 s; the worker peaked near 16 GB during it |
| no-op | 0.9-1.5 s | 10 bodies replayed; the xbf kept |
| edit: `lib/empennage.py` `RUDDER_GAP` 16 -> 18 | 3.8 s | `empennage` and the aircraft's body (3,034 kernel calls, 45 misses), 8 stages replayed |
| revert | 3.0 s | the same, every call a hit |

The reverted xbf holds the cold build's structure, names, colours and geometry
keys, part for part, and is the cold file's size; 639 bytes of its BRep data
differ from the cold file's: what the edit ran again on the memo's hits (the
empennage, and the nozzles in the aircraft's body) writes some BRep entries in
another order than its cold run did.

## Not ported

- The `teardown` and `explodedHold` clips: they translate the ten systems and the
  wing and aft parts apart and fade the skin, which no joint does, and the viewer's
  Explode shows the parts apart. The aircraft has no kinematics, so it has no
  motion and declares no GLB.
- `../validate.py` (the symmetry check and the old `inspect validate` /
  `inspect interfere` calls) and `../render/` (the old snapshot jobs, the
  presentation theme and display): tooling of the old pipeline. An xbf is drawn in
  the engine's one look.
- Materials: the original had none (colours only, the paint chips of the
  Tactical Paint Scheme), so none were added.
