# Heat over time (`thermal_transient`)

## When to use it

- "How hot after ten minutes", "how long until it reaches 60 °C", "a 5-minute burst every hour",
  "it cools down between shots"; a part that never settles in the time that matters (a short burst
  into a big block). For "once it has settled", the steady [`thermal`](thermal.md) is faster and exact.

## The study

Keys beyond the [shared ones](study-file.md#shared-by-every-analysis):

```json
{"analysis": "thermal_transient", "material": "aluminum-6061-t6",
 "initial_C": 25, "end_s": 600, "step_s": "auto",
 "heat": [{"faces": ["#o1.f7"], "W": 15, "history": [[0, 1], [300, 1], [301, 0]]}],
 "convection": [{"faces": ["#o1.f3"], "h_W_m2K": 10, "ambient_C": 25}],
 "view": {"checks": [{"kind": "temperature", "max_C": 85}]}}
```

- `temperatures`, `heat`, `convection`: as in [thermal.md](thermal.md#the-study). `history` on any:
  a schedule `[[t_s, factor], ...]`, straight between points, flat outside them, multiplying the
  entry's `W`, `W_per_m2`, `C` or `ambient_C` (the example: 15 W for 300 s, off within a second).
- `radiation`: as in [thermal.md](thermal.md#radiation); each step solves its T⁴ by Newton's method.
- `initial_C`: the whole part's temperature at t = 0 (default 20 °C). `end_s`: how long to follow
  it, in seconds (required). `step_s`: the time step, or `"auto"` (default), a two-hundredth of
  `end_s`; a step shorter than the fastest change you care about is enough.
- No anchor needed (an insulated part warming up has an answer), but a `heat`, `temperatures`,
  `convection` or `radiation` entry must change its temperature. Reads `conductivity_W_mK`,
  `specific_heat_J_kgK` and density.
- Check `temperature`: `max_C`, optional `faces`, judged on the hottest over all time, measured from
  the coolest temperature the study sets (held, ambient or `initial_C`). Its `at` says when
  (`time_s`) and which frame; the viewer jumps there when the check is chosen.

## What comes out

- Fields `temperature` and `heat_flux` per frame (up to 24, evenly from t = 0 to `end_s`, plus the
  hottest step); curve `max_temperature_C` at every step (the sidecar's `curves`). The viewer opens
  on the hottest frame with a Time scrubber; Play runs the frames.
- Summary: `max_temperature_C` and `max_at_s` (the hottest moment), `max_at_mm`,
  `final_max_temperature_C`, `min_temperature_C`, `max_heat_flux_W_m2`, `initial_C`, `end_s`,
  `step_s`, `steps`, `adaptive`, the smallest and largest step, `frames`, `reference_C`, `checks`;
  with radiation, `radiation_at_end` (each entry at `end_s`, as `thermal`'s `radiation`).
- CLI: "hottest 64 °C at 5 min, at [x, y, z] mm; 41 °C at the end", "followed from 25 °C for 10 min
  in 200 steps", and with radiation "at the end, radiated ...".
- Findings: the temperature check's (as `thermal`), and `still_heating` (info) when the hottest
  moment is the last: run longer, or solve it steady (the steady `thermal` gives where it ends).

## Judging the answer (a hand check)

- A small, conductive part cools (or warms) as one lump when h × (volume / area) / k is under 0.1:
  T(t) = T_air + (T_0 - T_air) e^(-t/τ), τ = ρ c V / (h A). A 10 mm aluminium cube in h = 100 has
  τ = 2700 × 896 × (0.01 / 6) / 100 = 40 s; from 100 °C in 20 °C air it is at 31 °C after 80 s
  (solver within 1.5 % at the automatic step). Settling takes about 3 to 5 τ.
- Into an insulated part every joule is stored: the mean rises P t / (m c), 5 W into 2.7 g of
  aluminium for a minute is 124 °C.
- Radiative cooling of a small part (one lump): ρ c V dT/dt = −ε σ A (T⁴ − T∞⁴), whose integral is
  t = (ρ c V / ε σ A) [G(T0) − G(T)], G(T) = (ln((T − a)/(T + a)) − 2 atan(T/a)) / (4 a³), a = T∞ (in
  kelvin). A 10 mm aluminium cube at ε 0.9 from 500 °C into 25 °C is at 161.1 °C after 300 s; the
  solver reads 161.6 °C at 1 s steps (0.4 % of the rise; backward Euler runs slightly warm), 161.3 °C
  with adaptive steps.

## Limits

- As [thermal.md](thermal.md#limits) (constant properties, grey diffuse radiation, `h` given). A
  radiation entry has no schedule: its ambient and emissivity hold for the whole run.
- Backward Euler takes each step's heat at its end: a heater that switches off between two steps
  is missed for that step, so keep `step_s` below the shortest on-time. It also damps fast changes
  a little: with the automatic step the lumped cube runs about 1 % warm at 2 τ.

## When the model is big

Ladder ([shared rungs](study-file.md#shared-by-every-analysis)): `iterative` (multigrid),
`local_refine` (where it is hottest), `defeature`, `linear_elements`, `symmetry`, and
`adaptive_steps`: the time step grows while the temperatures change slowly and shrinks where they
change fast, each step checked against two half steps (step doubling, kept within 0.2 % of the
temperature range a step). It stops at its [time limit](study-file.md#fit) (twice `fit.seconds`, at
least 30 s) where it has got to, and says so.
