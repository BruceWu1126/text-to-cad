# Creep (`creep`, lite)

It answers "how far does it slowly stretch or sag under a load held for months or years, usually
hot", and "how much of its stress relaxes". The load goes on at once (the elastic answer at 0 h),
then the part is followed through time as its creep strain grows.

**Lite; its limits (in every result; the verdict reads "Steady creep · "):** Norton's power law
(steady, secondary creep, with optional time hardening), no tertiary creep and no rupture, one
temperature throughout, isotropic and small strain, constant loads.

## When to use it

- A metal part held hot under a steady load for a long time: a bracket by an exhaust, a turbine or
  boiler part, a hot bolt or clamp, a heater support. Creep matters in metals from about 0.3 to 0.4
  of their absolute melting point: steel above about 350 to 450 °C, aluminium alloys 100 to 150 °C.
- A plastic part under a steady load at room temperature or a little above (a printed bracket, a
  snap held closed), when the user has the grade's creep data as a power law.
- "Will this preload or clamp relax?": a part held at a fixed stretch loses stress frame by frame.
- Not for: rupture life or time to failure (tertiary creep and damage are not modelled), loads that
  cycle or come and go (creep-fatigue), a temperature that changes across the part or over time,
  or large strains (tens of percent).

## The study

Keys beyond the [shared ones](study-file.md#shared-by-every-analysis):

- `fixtures` and `loads` (required, body loads included), on at 0 h and held for `duration_h`
  (required), in hours; a year is 8760 h.
- `steps`: equal time steps to start with, 1 to 1000, default 20. Each step's error (backward
  against forward Euler) is kept under 0.2 % of the stress by halving the step and retaking it, so
  the early, fast creep gets small steps by itself.
- `temperature_C` (optional): the temperature the part runs at, a note (nothing is heated); when
  the material's creep block names another, the result warns that the data do not match.
- `material` must carry the creep law, `creep: {"A": ..., "n": ..., "m": 0, "units": "MPa, hours"}`:
  - the creep strain rate is A σⁿ tᵐ, σ the von Mises stress in MPa;
  - `A` (required, > 0) and `n` (required, 1 to 20; metals are usually 3 to 8) are Norton's
    constants from the grade's creep data at the temperature it runs at;
  - `m` (optional, over −1 and at most 0, default 0) is time hardening: 0 is steady (secondary)
    creep, a negative m makes the creep slow down (primary creep);
  - `units`: "MPa, hours" (A per hour, the default) or "MPa, seconds" (A per second, converted:
    A per hour = A per second × 3600^(m+1)); `temperature_C` and `source` (optional) say where the
    numbers hold and come from.
  - cadgen has no creep table. A material with no creep block is refused with a sentence asking for
    one; in an assembly, such a part stays elastic, with a warning. Ask the user for the grade's
    creep data (a datasheet's Norton constants, or two points on its minimum creep rate against
    stress line: n = log(rate₂/rate₁) / log(σ₂/σ₁), A = rate₁ / σ₁ⁿ). Never make the numbers up.
- Checks (a run whose creep ran away, no equilibrium even in the smallest step, fails them all):
  - `creep_strain`: `limit_percent`, the most equivalent creep strain allowed at the end of the
    hold. Close past 0.9 of it. Row line "0.8 % creep after 10,000 h, limit 1 %"; titles "Creeps
    too far" / "Close to the limit" / "Holds its shape". The default with no `view.checks`:
    `{"kind": "creep_strain", "limit_percent": 1}`, a common design limit over a service life.
  - `stress`: the largest peak von Mises over the hold (usually the instant the load goes on), as
    static. `displacement`: at the end of the hold, as static.
- No load control (`load_scale` is not a drive): creep goes as the stress to the power n, not in
  proportion to the load. Drives: `frame` (the time scrubber), `field`, `deformation`,
  `threshold`; with no view the scrubber, the field and the deformation.

A steel bracket held hot for ten thousand hours (the creep constants are illustrative, not a
grade's data: use the grade's own):

```json
{"analysis": "creep", "mesh": {"size_mm": 3}, "duration_h": 10000, "temperature_C": 600,
 "material": {"name": "steel",
              "creep": {"A": 1.875e-16, "n": 5, "m": 0, "units": "MPa, hours",
                        "temperature_C": 600, "source": "the supplier's creep data sheet"}},
 "fixtures": [{"faces": ["#o1.f1"]}],
 "loads": [{"faces": ["#o1.f2"], "type": "force", "vector_N": [0, 0, -60]}],
 "view": {"checks": [{"kind": "creep_strain", "limit_percent": 1},
                     {"kind": "displacement", "limit_mm": 0.5}]}}
```

## What comes out

- Time frames (`series.kind` "time", unit "h") labelled "0 h", "500 h", "10,000 h", at most 24,
  spread evenly over the hold from 0 h, opening on the end; scrubbed (Time) and played (Play). Each
  has `von_mises` (MPa), `displacement` and `creep_strain` (equivalent, %). Nodal values never pass
  the integration points' own range.
- Stress relaxation: where creep lets a peak shed load (a clamped root, a notch, a stretch held
  fixed) the stress frames fall: `initial_max_von_mises_MPa` (0 h), `max_von_mises_MPa` (the end),
  `relaxation_percent`; the curve `max_von_mises_MPa` gives the peak against time at every step.
- Summary: `status` ("Held for 10,000 h", "Creep runs away after about 6,200 h" or "Stopped at its
  time limit after 4,000 h", then `timed_out: true`), `stopped`, `duration_h`, `reached_h`,
  `temperature_C`, `creep_data_temperature_C`, `max_creep_strain_percent` (and the Gauss-point
  value), `initial_max_displacement_mm`, `max_displacement_mm`, each with where,
  `applied_force_N`, `reaction_force_N`, `steps_requested`, `steps`, `steps_cut`, `smallest_step_h`,
  `largest_step_h`, `newton_iterations`, `adaptive`, `frames`, `checks`. Curves: the peak stress
  and peak creep strain against time at every step, the largest displacement per frame.
- Findings: `creeps_too_far` (error, the creep_strain check failing), `creep_runs_away` (error),
  `stress_relaxes` (info: "The peak stress relaxes from 67 MPa to 48 MPa over the hold (28 %
  less)"), `time_steps_cut` (info). CLI: the status, creep strain and displacement (and at 0 h);
  the peak stress when loaded and at the end; the time steps and Newton iterations.

## Judging the answer (hand checks)

- A bar under a constant stress σ creeps ε_c = A σⁿ t (steady creep), or A σⁿ t^(m+1)/(m+1) with
  time hardening, on top of its elastic σ/E. 100 MPa with A = 1.875e-16, n = 5 for 1000 h:
  1.875e-6 per hour, 0.1875 % in 1000 h. Matched to round-off (the stress does not change, so
  backward Euler is exact).
- A bar held at a fixed stretch relaxes: σ(t) = [σ₀^(1−n) + (n−1) E A t]^(1/(1−n)). With
  E = 200 GPa, σ₀ = 100 MPa and the constants above it falls to 50 MPa in 1000 h; solved within
  about 1.2 % (backward Euler's step error, kept small by the step control).
- A beam in bending creeps faster at the surface, so its stress spreads inward and the peak falls:
  a steady-creep cantilever's root stress tends to the elastic value times (2n+1)/(3n) (for n = 5,
  0.73 of it), the tip sagging at a steady rate.
- Sanity: at 0 h it equals a static study; creep strain only grows; doubled stress creeps 2ⁿ faster.

## Limits

- Norton's power law is steady (secondary) creep; with m < 0 it also follows a primary creep that
  slows with time. Tertiary creep, damage and rupture are not modelled: a part near the end of its
  creep life reads safer than it is. Compare the stress with the grade's creep-rupture strength at
  the hold time separately.
- One temperature throughout: the constants are for one temperature and the part is taken to be
  at it. Creep changes steeply with temperature (a few tens of degrees can change the rate several
  times over), so data for the wrong temperature is the largest error there is.
- Isotropic and small strain: creep keeps the volume and follows the von Mises stress (J2 flow);
  printed and fibre-filled plastics creep differently along and across their layers. Rotations
  must stay small. Loads and fixtures are held constant (nothing unloaded or cycled); a pressure
  does not turn with the surface.

## When the model is big

Ladder ([shared rungs](study-file.md#shared-by-every-analysis); as
[`nonlinear`'s](nonlinear.md#when-the-model-is-big)), in order: `iterative` (Newton-Krylov: each
time step's Newton solve by GMRES with a multigrid preconditioner); `adaptive_steps`: the time steps also grow
past the starting size (up to 64 times it) where the creep is steady, still cut wherever a step's
error passes 0.2 % of the stress: fewer solves over a long, steady hold, at the same error control;
`local_refine` (around the stress peak); `defeature` (far from loads and fixtures); `symmetry`.
A run past its time limit (twice `fit.seconds`, at least 30 s) stops where it got to and says so
([`fit`](study-file.md#fit)).
