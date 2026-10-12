# Cooled by flow (`conjugate_heat`, lite)

The flow is solved first (laminar [`cfd`](cfd.md) or k-omega SST [`cfd_turbulent`](cfd-turbulent.md)),
then the fluid's temperature and the part's together, the heat crossing the wetted walls; in-house.
Lite; its limits (in every result), the flow's own too: **"Steady; the flow carries the heat but is
not changed by it (no buoyancy, constant properties)."**

## When to use it

- "How hot does the chip get on this liquid cold plate at 1 L/min", "is this cooling channel
  enough", "how warm does the water come out"; a part warming the air blowing past it (`external`).
- "What heat transfer coefficient does this flow give", instead of guessing `h` for a `thermal`
  study's `convection`: solve this once, read the mean `heat_transfer_coefficient`, then iterate
  the design with the cheaper `thermal`.
- Not for natural convection (a still room, a heat sink with no fan), boiling or condensing, a
  fluid whose density or viscosity changes much with temperature, or a flow changing over time.
  Radiation as well as flow: add `radiation` in a `thermal` study with the `h` this one finds.

## The study

Keys beyond the [shared ones](study-file.md#shared-by-every-analysis):

```json
{"analysis": "conjugate_heat", "material": "aluminum-6061-t6",
 "mesh": {"size_mm": 0.75},
 "flow": {"kind": "internal", "fluid": "water", "regime": "auto",
          "inlets": [{"opening": "x_min", "velocity_m_s": 0.2, "temperature_C": 20}],
          "outlets": [{"opening": "x_max", "pressure_Pa": 0}]},
 "heat": [{"faces": ["#o1.f3"], "W": 40}],
 "convection": [{"faces": ["#o1.f5"], "h_W_m2K": 10, "ambient_C": 25}],
 "view": {"checks": [{"kind": "temperature", "max_C": 70, "faces": ["#o1.f3"], "label": "Chip side"},
                     {"kind": "pressure_drop", "limit_Pa": 5000}]}}
```

External (a part in a stream): `"flow": {"kind": "external", "fluid": "air", "velocity_m_s":
[2, 0, 0], "temperature_C": 25}`, with `"heat": [{"faces": ["#o1.f1"], "W": 5}]`.

- `flow`: everything a [`cfd`](cfd.md#the-study) study's `flow` takes, plus:
  - each inlet's `temperature_C` (required), or an external flow's `flow.temperature_C`;
  - `regime`: `"auto"` (default: laminar below Re 2000 inside the part, Re 1000 around it, turbulent
    above), `"laminar"` or `"turbulent"`. A turbulent flow takes `turbulence_intensity` as
    [cfd-turbulent.md](cfd-turbulent.md#the-study) says; a laminar one refuses it.
  - a fluid given by its numbers also needs `conductivity_W_mK` and `specific_heat_J_kgK`:
    `{"name": "oil", "density_kg_m3": 870, "viscosity_Pa_s": 0.03, "conductivity_W_mK": 0.13, "specific_heat_J_kgK": 2000}`.
    `"water"` (0.606 W/(m K), 4181 J/(kg K)) and `"air"` (0.0263 W/(m K), 1007 J/(kg K)) know
    theirs (20-27 °C, Incropera's tables).
- The part: `material` (reads `conductivity_W_mK`) and the thermal keys of
  [thermal.md](thermal.md#the-study) on its faces: `heat` (a chip, a heater), `temperatures` (a face
  held) and `convection` (air on the faces the flow does not wet). No anchor is needed: the fluid
  coming in at its temperature takes the heat away. No `radiation` here (see When to use it).
- `mesh.size_mm`: the element size of the part and the flow's walls and openings; left out, each
  takes its own default (the flow's a quarter of the narrowest opening's hydraulic diameter
  laminar, a tenth turbulent). Give about 6-8 elements across a laminar channel: a heat answer
  needs the wall layer resolved.
- Checks: `temperature` (the part; `max_C`, optional `faces`, measured from the coolest temperature
  the study sets, here usually the inlet's), `pressure_drop` (`limit_Pa`) and `velocity`
  (`limit_m_s`). None by default.

## What comes out

- [Tracers](cfd.md#what-comes-out) carried by the solved coolant velocity. Fields on the surface:
  - `temperature`: the part's (°C, signed; the colour bar runs from its own coolest);
  - `fluid_temperature` ("Fluid temperature"): the fluid's mixed (bulk, mixing-cup) temperature at
    each wetted point: the fluid within one passage width (external: two wall elements), in a thin
    slab across the local flow, weighted by the flow through it as the outlet's mixed temperature
    is. It runs from the inlet's temperature to the outlet's reported one and is not the wall's own
    (`temperature`); a face the fluid does not wet reads the inlet's temperature;
  - `wall_heat_flux` ("Heat into the flow", W/m², signed: negative where the fluid heats the part);
  - `heat_transfer_coefficient` (W/(m² K)): that flux over the wall's temperature less the local
    fluid's (internal) or the free stream's (external); 0 where they are equal;
  - `pressure` (the wall pressure, Pa) and `speed` ("Speed", m/s), as `cfd`'s.
- Nothing deforms; no load control. It opens on Temperature (other fields in Field), the takeaway
  leading with "Laminar · " or "Turbulent · ".
- Summary: `regime`, `reynolds`, `prandtl`, `peclet`; the part's `max_temperature_C`,
  `min_temperature_C`, `max_at_mm`; the fluid's `inlet_temperature_C`, `outlet_temperature_C` (all
  outlets mixed), `outlet_temperature_by_balance_C` (inlet plus the m c_p ΔT rise), each of the
  `outlets`, `max_fluid_temperature_C` and `min_fluid_temperature_C` (node extremes: stabilised
  advection can over- and undershoot a few degrees in a thin, fast thermal layer, so read the mixed
  temperatures, which the energy balance holds exactly); `heat_to_fluid_W` (from the walls),
  `heat_carried_W` (m c_p ΔT out), `fluid_balance` (their mismatch, a share), `mass_flow_kg_s`,
  `part_heat_in_W`, `part_balance`; `wetted_area_mm2`, `mean_wall_temperature_C`, the wall heat
  flux's range, `mean_heat_transfer_coefficient_W_m2K` (on the log-mean temperature difference,
  wall to fluid, in to out, as for a heat exchanger) and its `nusselt` on the inlet's hydraulic
  diameter; the flow's `pressure_drop_Pa`, `flow_balance`, `flow_rate_L_min`, `max_velocity_m_s`,
  wall pressure range; `fluid_mesh`; `checks`.
- CLI: "internal laminar flow of water: Re 40, Pr 6.9", "part hottest 24.1 °C ...", "fluid in at
  20 °C, out at 21.89 °C; walls put 1 W into it, it carries 1 W out", "mean heat transfer
  coefficient 1154 W/m²K (Nusselt 7.62) over 300 mm² wetted", "pressure drop ...".
- Findings: the checks' (temperature as `thermal`), `fluid_warms` (info: the outlet temperature and
  heat carried), and warnings: Re outside the regime solved, a heat balance off by over 1 %
  (`fluid_heat_balance`: the solve did not settle), the mixed outlet temperature and the balance's
  over 1 % of the rise apart (`outlet_temperature_spread`: a turbulent wall function lets a little
  fluid slip through its walls, which a small rise in a large flow magnifies; trust the balance's),
  an unsettled flow.

## How it is solved

- The flow: exactly the `cfd` or `cfd_turbulent` solve of the same `flow`, on the fluid's own mesh.
- The fluid's temperature: steady advection-diffusion, ρ c_p u · ∇T = ∇ · ((k + ρ c_p ν_t / 0.85) ∇T),
  on linear elements (the flow's own pressure space, so the solved velocity carries heat with no
  loss: what the walls put in leaves as m c_p ΔT to the solver's tolerance), stabilised by SUPG.
  An inlet brings fluid in at its temperature by Danckwerts' condition (the enthalpy coming in is
  fixed; holding the inlet plane would make it a false heat sink next to a hot wall). The part:
  the `thermal` conduction system on its own mesh.
- The tie: the meshes do not match at the wetted walls, so each fluid wall point takes the part's
  temperature there (its quadratic surface interpolated) and gives its heat back through the same
  weights, losing none. Laminar: one temperature at the wall. Turbulent: a film of the thermal law
  of the wall between them (h = ρ c_p u_τ / T⁺, Jayatilleke's T⁺ with Pr_t 0.85).
  The fluid and the part are solved as one system (direct, or GMRES with an incomplete LU).

## Judging the answer (hand checks)

- The fluid's energy balance: T_out = T_in + Q / (m c_p), m = ρ V A at the inlet. 1 W into a 4 mm
  bore of water at 0.01 m/s (m = 1.25e-4 kg/s) warms it 1.9 °C. The books and the mixed outlet
  temperature close to 1e-6 on the solved flow, which is within 1 % of ρ V A.
- Laminar, fully developed flow in a round pipe: Nu = h D / k = 3.66 with the wall at one
  temperature, 4.36 with an even heat flux into it; past the inlet, within about 0.05 Re Pr D. On a
  32 mm pipe of 4 mm bore at Pe 40 (0.5 mm elements), the bulk temperature's decay gives Nu 3.74
  (+2.2 %); at 0.4 mm elements, 3.655 (−0.1 %).
- Turbulent flow in a pipe: Dittus-Boelter Nu = 0.023 Re^0.8 Pr^0.4 (developed, Re > 10 000; a rough
  guide from about 4000), Gnielinski's correlation from 3000. A short channel reads higher for its
  entry. The 24 mm cold plate at Re 4000 reads a mean Nu of 38 (Dittus-Boelter 37.8).
- The part: from the wall to the hottest point it is conduction, as `thermal`'s hand checks; the
  wall sits about Q / (h A) above the fluid.

## Limits

- No natural convection, no property that varies with temperature (give properties at the mean
  fluid temperature), no boiling.
- Laminar entry and developed flow are resolved by the mesh: too few elements across a channel read
  h high. Turbulent walls use wall functions (y⁺ ≥ 30 by construction), so h is the law of the
  wall's, good to the 10-25 % of correlations.
- No contact resistance or coating at a wetted face; the [flow pages' shared limits](cfd.md#limits)
  (one part, bounding-box openings).

## When the model is big

Ladder ([shared rungs](study-file.md#shared-by-every-analysis)): `iterative` solvers for the flow and
the heat; `fluid_coarsen` (the flow's core coarser away from its walls, which keep their size: the
heat it takes from the walls may read a little high, the energy balance stays exact);
`continuation` (Reynolds continuation or longer pseudo-time steps for the flow); `local_refine`
around the hottest point, its note how far the hottest temperature moved between the passes.
