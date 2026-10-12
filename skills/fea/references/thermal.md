# Heat (`thermal`)

The settled temperature from held faces, heat put in (a chip, a heater, a motor), convection to air
or liquid, and radiation (to the surroundings, and between faces that see each other).

## When to use it

- "How hot does the chip side get", "will the case stay under 60 °C", "is this heat sink enough".
- A warm-up, a duty cycle, "how long until it is too hot": [`thermal_transient`](thermal-transient.md).
  "Does the heat stress or warp it": [`thermal_stress`](thermal-stress.md), which runs this first.

## The study

Keys beyond the [shared ones](study-file.md#shared-by-every-analysis):

```json
{"analysis": "thermal", "material": "aluminum-6061-t6",
 "temperatures": [{"faces": ["#o1.f1"], "C": 25}],
 "heat": [{"faces": ["#o1.f7"], "W": 15}, {"faces": ["#o1.f8"], "W_per_m2": 2000}],
 "convection": [{"faces": ["#o1.f3", "#o1.f4"], "h_W_m2K": 10, "ambient_C": 25}],
 "view": {"checks": [{"kind": "temperature", "max_C": 85, "faces": ["#o1.f7"], "label": "Chip side"}]}}
```

- `temperatures`: faces held at `C` (a cold plate, a wall the part is bolted to, water at a known
  temperature). A face with nothing on it is insulated: no heat crosses it.
- `heat`: heat in through faces. `W` is the total power, spread evenly over all its faces' area;
  `W_per_m2` is a heat flux. Exactly one of the two per entry. A negative value takes heat out.
- `convection`: air or liquid carrying heat away. `h_W_m2K` is how well it does (still air about
  5-10, a fan 25-100, water 500 or more), `ambient_C` its temperature.
- `radiation` (optional): see [Radiation](#radiation). Reads `conductivity_W_mK`. No `fixtures`,
  `loads` or `margin`: nothing moves.
- **At least one `temperatures`, `convection` or `radiation` entry is required.** A part only
  heated, with nowhere for the heat to go, has no steady temperature, and the study is refused with
  that sentence. Add the air around it, the face it sits on, or what it radiates to.
- Check `temperature`: `max_C`, the hottest it may get, and optional `faces` (else the whole part).
  Measured from the coolest temperature the study sets (lowest held or ambient, the result's
  `reference_C`): 84 °C against 100 °C with 25 °C air has used 59/75 = 0.79 of its room; close past
  0.9. A limit at or under that reference is refused. Row: "Hottest 84 °C, limit 100 °C"; with no
  check, the verdict shows none.

## Radiation

```json
{"radiation": [{"faces": ["#o1.f3"], "emissivity": 0.9, "ambient_C": 25},
               {"faces": ["#o1.f8", "#o1.f9"], "emissivity": 0.8, "ambient_C": 25, "surface_to_surface": true}]}
```

- Each entry's faces radiate as a grey, diffuse surface: `emissivity` (0 to 1, required) and the
  surroundings' `ambient_C`. Emissivity depends on the finish far more than the alloy, so there is
  no table value: polished aluminium about 0.05, bare machined aluminium 0.1, anodised or painted
  0.8-0.95, oxidised steel 0.8. Ask, or say which you assumed.
- Without `surface_to_surface`, a face sees its surroundings whole, losing ε σ (T⁴ − T∞⁴) per area
  as if nothing of the part stood in its way: right for an outside face of a convex part.
- With `"surface_to_surface": true`, the entry's faces also exchange heat with every other such
  face (fins facing each other, the inside of a box, two plates). cadgen computes the view factors:
  the faces' surface triangles grouped into at most 300 patches (one per face only if more faces
  than that radiate), each pair's view factor by the contour integral from every triangle's
  centroid, obstruction by ray casting against every surface triangle of the part. Whatever a face
  does not see of the other radiating faces goes to its entry's `ambient_C`; a face nobody named
  counts as surroundings too (it blocks rays, but does not radiate back). The patches' radiosity is
  solved exactly for grey surfaces.
- A face may be in one radiation entry only. Radiation is nonlinear (T⁴, in kelvin inside), solved
  by Newton's method from the warmest temperature the study sets; without it the solve is linear.
- Summary `radiation` (counted in `heat_out_W`): each entry's `net_W` out (negative where it takes
  heat in), `radiated_W`; with surface-to-surface, the `view_factors` between entries (row i: the
  share of entry i's radiation reaching entry j), `patches`, the method and the rays cast. CLI:
  "radiated 100 W from #o1.f6 (emissivity 0.9, to 25 °C)".
- Hand checks: a plate radiating Q from area A to surroundings at T∞ (kelvin) settles at
  T = (Q / (ε σ A) + T∞⁴)^¼: 100 W from 0.01 m² at ε 0.9 into 25 °C is 398.8 °C; the solver matches
  the closed form to 0.001 % of the rise. Two black, directly opposed 50 mm squares 50 mm apart see
  each other by F = 0.1998 (the analytic formula); the solver's view factor is within 0.1 %, and the
  heat the cold one takes in, A F σ (T_h⁴ − T_c⁴), within 0.1 %.
- Radiation matters past about 100 °C, or for a dull part cooled only by still air: at 50 °C a
  black face loses about 6 W/m²K, comparable to still air's convection.

## What comes out

- Fields: `temperature` (°C, coloured from its own coolest to its hottest) and `heat_flux` (the
  conducted heat flow's size, W/m², "Heat flow" in the viewer). Nothing deforms; no load control.
- Summary: `max_temperature_C`, `min_temperature_C`, `max_at_mm`, `max_heat_flux_W_m2`,
  `heat_in_W`, `heat_out_W`, `heat_balance` (their mismatch as a share), `reference_C` and the
  `checks`. An assembly adds each part's `max_temperature_C` under `parts`.
- CLI: "hottest 84 °C at [x, y, z] mm, coolest 25 °C" and "heat in 15 W, out 15 W".
- Findings: `temperature_over_limit` (error) and `temperature_close_to_limit` (warning) per check,
  `no_heat` (info) when nothing heats it, and `heat_balance` (warning) when in and out differ by more
  than 1 %: the solve did not settle, so check it on a finer mesh before trusting it.

## Judging the answer (a hand check)

- A slab held at two temperatures conducts Q = k A ΔT / L: a 10 × 10 mm aluminium bar 20 mm long,
  100 °C to 20 °C, passes 167 × 1e-4 × 80 / 0.02 = 66.8 W, falling linearly; solver well under 1 %.
- Heat out to air: Q = h A (T_surface - T_air). 15 W into 0.01 m² of surface in still air (h = 10)
  needs 150 °C above the air: a plausibility check, and a sign that a heat sink or a fan is needed.
- A pin fin (a rod held hot at its base in air, its tip insulated): T_tip - T_air = (T_base -
  T_air) / cosh(mL), m = √(hP / (kA)). A 4 × 4 mm aluminium pin 50 mm long in h = 50 keeps 72 % of
  its base excess at the tip; the solver matches within 2 %.
- In series (a spreader bonded to a mount): the resistances L / (k A) add; the joint sits where the
  drop across each matches its share.

## Limits

- Conduction with constant properties (no temperature-dependent conductivity or emissivity); no
  contact resistance at bonded joints.
- Radiation is grey and diffuse through a transparent medium (no gas absorbs); an unnamed face
  radiates nothing. View factors are between patches of up to a few hundred per study: a fine
  detail inside one patch is averaged.
- `h` is a number you give, not a flow calculation, often the largest uncertainty: say which.
- Steady state only: how long it takes to get there is `thermal_transient`.

## When the model is big

Ladder ([shared rungs](study-file.md#shared-by-every-analysis)), in order: `iterative` (multigrid,
for the temperatures), `local_refine` (around the hottest region), `defeature`, `linear_elements`, `symmetry`.
