# Two fluids (`multiphase`, lite)

What the liquid inside the part does over time: two immiscible fluids (water and air by default),
solved in-house. With `map_to_structure`, the wall pressure at the moment of the largest slosh is
applied to a static solve, so stress and displacement come too. Lite; its limits (in every result):
**"Laminar (no turbulence model), incompressible; the interface is smeared over a few elements and
the walls slide freely; no evaporation, boiling, mixing or foam."**

## When to use it

- A tank sloshing when shaken or braked: "will the water spill out of this tank when the truck brakes
  at 0.3 g?" (`fill_level` check), "how high does it climb the walls?", "how hard does the slosh
  push on the tank, and when?", "what pressure do the walls see?"
- "At what frequency does the liquid slosh?" (the summary gives linear theory's first mode; a short
  shake shows the real one in the force curve). Filling or draining a cavity; a bubble rising.
- Not for a single fluid filling the whole part with no free surface ([`cfd`](cfd.md)); fast,
  splashing or turbulent flow (breaking waves, jets: it runs, but the splash's fine structure is
  beyond a few elements across the interface); boiling, evaporation, foam or liquids that mix.

## The study

Keys beyond the [shared ones](study-file.md#shared-by-every-analysis):

```json
{"analysis": "multiphase",
 "mesh": {"size_mm": 5},
 "fluids": {"liquid": "water", "gas": "air"},
 "fill": {"fraction": 0.5},
 "acceleration": {"direction": [1, 0, 0], "amplitude_g": 0.3,
                  "history": {"shape": "half_sine", "duration_s": 0.2}},
 "end_s": 1.5,
 "probes": [{"label": "front wall", "at_mm": [3, 5, 0]}],
 "view": {"checks": [{"kind": "fill_level"},
                     {"kind": "wall_pressure", "limit_Pa": 2000}]}}
```

- The part is the container; the liquid fills its inside, closed or open on sides of its bounding
  box (an open-top tank opens on `z_max`). An unnamed open side is open to the air at 0 Pa (liquid
  reaching it spills; what flows back in, there or at an outlet, is the air outside, never liquid).
  A part with no inside is a study error.
- `fluids` (optional): `liquid` is `"water"` (998.2 kg/m³, 1.002e-3 Pa·s, 20 °C), `"oil"` (870, 0.1),
  `"glycerine"` (1261, 1.41) or `{"density_kg_m3", "viscosity_Pa_s", "name"}`; `gas` is `"air"`
  (1.204, 1.81e-5) or an object; the liquid must be the denser. `surface_tension_N_m` (default 0)
  is a number or `"auto"` (water on air, 0.0728 N/m), for millimetre parts and bubbles, not tanks.
- `fill` (optional, default half full): exactly one of
  - `{"level_mm": 25}`: the liquid's surface this far above the inside's floor (along gravity);
  - `{"fraction": 0.5}`: this share of the inside's volume, level;
  - `{"box_mm": [[x0, y0, z0], [x1, y1, z1]]}`: liquid inside this box only (a dam about to break);
  - `{"bubble": {"center_mm": [x, y, z], "radius_mm": r}}`: a full part with a bubble of gas.
- `gravity_m_s2` (optional): `[0, 0, -9.80665]` by default. `acceleration` (optional): the part's own,
  shaken, braked or turned: `direction` (`[x, y, z]`), `amplitude_g`, and `history` as in
  [transient.md](transient.md) (a table `[[t_s, factor], ...]`, `{"shape": "step"}` held (the default),
  `"ramp"` or `"half_sine"` with `duration_s`) or `{"shape": "sine", "frequency_Hz": f}` (shaken).
- `inlets` / `outlets` (optional, filling and draining): an open side each, as in
  [cfd.md](cfd.md#the-study): an inlet `{"opening": "z_max", "velocity_m_s": 0.2, "fluid": "liquid"}`
  (or `"gas"`), an outlet `{"opening": "z_min", "pressure_Pa": 0}`.
- `end_s` (optional): how long to follow it, s; default three periods of the first sloshing mode.
  `step_s` (optional, default `"auto"`, a fortieth of that period): the largest time step, s, also
  shrunk so the fastest fluid crosses at most half an element a step. `walls` (optional): `"slip"`
  (default: the fluid slides, so the waterline moves) or `"no_slip"`.
- `probes` (optional): vertical lines, each `{"label", "at_mm": [x, y, z]}` (any point on the line),
  following the liquid's height over time. With none, two go half an element in from each wall
  along the shake, at mid-width.
- `map_to_structure` (optional): `{"material", "fixtures"}`, as in [cfd.md](cfd.md#the-study)
  (`material` may sit at the top level; only a mapped study needs one).
- `view.checks`:
  - `{"kind": "fill_level"}`: the fill level against `limit_mm` (default: the brim, the inside's
    height). The fill level is the highest point of the liquid's surface anywhere in the inside over
    the run (at a wall, in a corner, a splash: where it would spill first), in mm above the inside's
    floor; with `"probes": ["front wall"]`, the highest along those probes' lines. A probe reads
    only its own line, so at most the fill level and usually less: the surface runs up highest in a
    corner of the wall ahead (a gallery tank braked at 0.3 g: 37.6 mm in the corner, 31.7 mm on the
    probe 2.5 mm in from that wall). The fluid ends at the brim, so the level never reads past it;
    liquid that leaves over the brim (an open side no inlet or outlet names) fails the check
    instead, once more than 1 % of the starting liquid has left (`SPILL_SHARE`: well past round-off,
    and past what the smeared surface alone carries out of an open top it only comes near, 0.02 %
    for a still tank 8 mm under the brim, 0.26 % for one stopped 2.4 mm under it). A spill is an
    error finding (`spills_over_brim`), a CLI line, the check's `spilled_mL` and `spilled_percent`
    and the takeaway, in the same words: "Spills over the brim: 2.3 mL left the tank (6.4 % of the
    liquid)". Titles "Spills over" / "Close to the brim" / "Stays in"; close at 90 %.
  - `{"kind": "wall_pressure", "limit_Pa": 2000}`: the hardest the fluids press on the walls at any
    time; `stress` and `displacement` when mapped.

## What comes out

- Time frames (up to 24: evenly spaced, plus the moments the liquid rises highest, presses hardest
  and pushes hardest); the Time scrubber opens on the highest rise, Play runs them.
- On the wetted walls, per frame: `water_fraction` (1 where the liquid touches, 0 the gas; outer
  faces 0) and the wall `pressure` (Pa, gauge: 0 at an open side, or at the top of a sealed part).
- Curves over time (sidecar `curves`): `fill_level_mm` (the surface's highest point anywhere),
  `height_<probe>_mm`, `sloshing_force_{x,y,z}_N` (the fluids' push on the part, less its push at
  rest, which is their weight), `moment_{x,y,z}_N_m` (about the inside's floor, below its middle),
  `max_wall_pressure_Pa`, `volume_change_percent`.
- Summary: `peak_fill_level_mm`, when (`peak_fill_level_at_s`) and where (`peak_fill_level_at_mm`, the
  point on the surface), `brim_mm`, `probes` (each line's point, peak, when, final), `max_wall_pressure_Pa`,
  `force_N` (`at_rest`, `sloshing_peak`, `sloshing_peak_N` and when), `moment_N_m`, `first_sloshing_Hz`
  (linear theory, below), `volume` (start, end, change, `net_inflow_L` through the openings, `spilled_mL`
  and `spilled_percent` over the brim, `error_percent` against what came in and went out, the largest
  per-step drift before it was put back), `spill` (the sentence, when it spilled), `max_speed_m_s`,
  `interface_mm` (the smeared surface's thickness), `fill.words` ("Half full of water"), mesh, march.
- The interface's 0.5 iso-surface is not drawn in the GLB (the part); the walls' water fraction and
  the probe heights show where the surface is.
- A long run reports progress (`two fluids: t = 0.42 s of 1.5 s, 80 steps ...`) and stops at its
  time limit as [`fit`](study-file.md#fit) says (`time_limit` warning, checks "(stopped at 420 ms)");
  its last frame is where it stopped, and the summary adds `stopped_at_s` and `planned_end_s`.

## How it is solved

Taylor-Hood elements (quadratic velocity, linear pressure) on netgen's tetrahedra of the inside,
density and viscosity from a level set (the signed distance to the surface, smoothed over about
0.35 element each side), surface tension as a continuum force, BDF2 in time with one linear solve a
step. The hydrostatic pressure is split off analytically from the level set, so a still tank stays
still to round-off on any mesh; as that is exact only while the level set is a distance, it is
re-distanced whenever the flow has stretched or squeezed it anywhere along the surface, and an inlet
lets its fluid in upwind at its own speed rather than pinning the inlet to it. The liquid's volume is
put back every step (conserved to round-off, less what leaves through an open side, plus inflow).

## Judging the answer (hand checks)

- Still liquid: floor pressure ρ g h (water, 25 mm deep: 245 Pa); the push on the floor is its weight.
- First sloshing mode of a rectangular tank, linear theory: ω² = g k tanh(k h), k = π / L, L the
  length along the shake, h the depth (60 mm long, 25 mm deep: 3.35 Hz). Small sloshes follow it; the
  engine reads its period within 5 % (2-4 % at about 12 elements along L). Shaken near it the slosh
  grows; well below, the surface just tilts by about atan(a / g). A steady acceleration a tilts the
  surface to slope a / g: the rise at the wall is about (L / 2)(a / g).
- Filling through an inlet into a part with air in it: the inlet starts at once and the air, a
  thousand times lighter, is pushed out first, reading several times the inlet's speed (a half-full
  10 mm duct fed at 0.05 m/s: about 0.35 m/s in the air at the top wall, mostly in the first step,
  easing after). Spilling liquid falls at about free fall: nothing should outrun the inlet's speed
  plus sqrt(2 g H) (H the part's height); a `max_speed_m_s` well past that is a fault, not physics.
- Tested against: the still column (floor pressure within 1 %, no spurious motion), the sloshing
  period (within 5 %), Hysing et al.'s rising bubble (Int. J. Numer. Meth. Fluids 60 (2009) 1259,
  test case 1: rise velocity within 15 % on a coarse mesh, 5 % measured), volume within 1 % a run.

## Limits

- No compressible gas (a sealed air pocket does not compress); no phase change (evaporation,
  boiling, condensation), mixing, foam or heat. Walls slide by default (no boundary-layer drag); the
  contact angle is not modelled.
- The interface is a few elements thick: features smaller than about two elements (droplets, thin
  films, spray) are smeared out, and a slosh smaller than an element is approximate (mesh finer).
- Air within the smeared band is carried along, so air speeds read high near the surface (the
  liquid's motion is not affected); `max_speed_m_s`, the fastest of either fluid, is usually the air's.

## When the model is big

Ladder ([shared rungs](study-file.md#shared-by-every-analysis)), in order:

- `adaptive_steps`: lets the step grow to a twentieth of the sloshing period, the fluid crossing at most
  one element a step (BDF2 then reads the period about 3 % long, against 0.8 % at the default 40 steps).
- `iterative`: GMRES with multigrid on the momentum and a pressure-Laplacian Schur complement, when
  memory is what misses.
- `local_refine`: fine only in the band the surface sweeps (its level plus or minus how far the
  shake can move it), 2.5 times coarser above and below.
- `fluid_coarsen`: the whole fluid mesh 1.6 times coarser; it says how much the surface is smeared.
