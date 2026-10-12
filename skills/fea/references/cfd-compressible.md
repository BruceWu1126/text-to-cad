# Fast gas flow (`cfd_compressible`, lite)

What [Flow](cfd.md) answers when the gas is fast enough that its density changes with its speed:
past about **Mach 0.3** (about 100 m/s in air). It also says how close to the speed of sound the
gas gets, whether the passage chokes (the mass flow stops growing with the pressure ratio), and
where a shock stands. Steady ideal-gas flow, in-house (no outside software); `map_to_structure`
adds a static solve as in `cfd`. Lite; its limits (in every result): **"Steady, ideal gas,
adiabatic (one total temperature); an inviscid core with frictionless walls past the laminar
range, laminar walls in it; no turbulence model; shocks are captured over a few elements."**

## When to use it

- A nozzle, a valve or an orifice under a real pressure ratio, a high-speed air duct, a gas jet:
  "how much air does this nozzle pass at 1.5 bar?", "does it choke?", "what Mach number does the
  throat reach?", "where does the shock stand?", "what pressure does this valve seat see?"
  (`map_to_structure`: "will it hold?")
- Any gas a `cfd` or `cfd_turbulent` result shows past about Mach 0.3 (speed over 343 m/s, air at
  20 °C): their incompressible answer is a few percent off at Mach 0.3, badly past Mach 0.5.
- Not for liquids, slow gas ([cfd](cfd.md) or [cfd_turbulent](cfd-turbulent.md); this one warns
  and gives the same answer), heat through the walls, combustion, anything time-varying, or a
  supersonic jet leaving the outlet (it warns; below).

## The study

Keys beyond the [shared ones](study-file.md#shared-by-every-analysis). Internal flow (through the
part), a reservoir at 1.5 bar absolute feeding a nozzle that blows into the room:

```json
{"analysis": "cfd_compressible",
 "mesh": {"size_mm": 1.5},
 "flow": {"kind": "internal", "gas": "air",
          "inlets": [{"opening": "x_min", "total_pressure_Pa": 150000, "total_temperature_C": 20}],
          "outlets": [{"opening": "x_max", "pressure_Pa": 101325}]},
 "view": {"checks": [{"kind": "mach", "limit": 0.8},
                     {"kind": "pressure_drop", "limit_Pa": 30000}]}}
```

A fixed mass flow instead: `"inlets": [{"opening": "x_min", "mass_flow_kg_s": 0.004,
"total_temperature_C": 20}]`. External flow (around the part): `{"kind": "external", "gas": "air",
"velocity_m_s": [150, 0, 0], "pressure_Pa": 101325, "temperature_C": 20}`.

- **Pressures are absolute** (1 atm is 101325 Pa), unlike `cfd`'s gauge: density follows them.
- `flow.gas`: `"air"` (default: gamma 1.4, R 287 J/kg K, Sutherland viscosity), `"nitrogen"`,
  `"helium"`, `"co2"`, or `{"name", "gamma", "gas_constant_J_kgK", "viscosity_Pa_s"}` (the
  viscosity at 20 °C).
- Internal flow, openings as [`cfd`'s](cfd.md#the-study):
  - `inlets`: each `{"opening", "total_pressure_Pa" or "mass_flow_kg_s", "total_temperature_C"}`,
    the reservoir's (the gas at rest upstream); a total pressure must be above the outlet's.
    `"profile"` (`"uniform"` or `"developed"`) goes with a mass flow only.
  - `outlets`: each `{"opening", "pressure_Pa"}`, the static pressure it leaves into (default 101325).
- `flow.walls`: `"auto"` (default), `"slip"` or `"no_slip"`. Auto picks laminar no-slip walls in the
  laminar range (Re up to 2000 internal, 1000 external) and frictionless walls (an inviscid core)
  past it, where a laminar wall would be wrong and a fast gas core is nearly inviscid; the friction
  loss is then not counted, and it says so.
- `mesh.size_mm`: as `cfd`'s (a quarter of the narrowest opening's hydraulic diameter).
  `map_to_structure`: as `cfd`'s; the wall pressure applied is that above the first outlet's (the
  room the part sits in).
- Checks, each close past 0.9 of its limit: `mach` (`limit`, the fastest Mach number in the gas;
  titled "Too fast", "Close to the limit", "Within the speed limit"), `pressure_drop` (`limit_Pa`,
  inlet static to outlet static), `velocity` (`limit_m_s`); `stress` and `displacement` mapped.

## What comes out

- [Tracers](cfd.md#what-comes-out) as `cfd`'s. Fields on the wetted surface: `pressure` (Pa above the outlet's, signed), `mach` (speed over the
  local speed of sound), `temperature` (the gas's, °C: it cools as it speeds up, T = T0 / (1 +
  0.2 M²) in air), `speed` (as `cfd`'s); mapped, `von_mises` and `displacement`, the part deformed.
- Summary: `mass_flow_kg_s` (in) and `outflow_kg_s`, `flow_balance`, `max_mach` and where,
  `max_velocity_m_s`, `outlet_mach`, `pressure_ratio` (inlet total over outlet static),
  `inlet_total_pressure_Pa`, `inlet_pressure_Pa`, `outlet_pressure_Pa`, `pressure_drop_Pa`,
  `choked` (the gas reached Mach 1), `shock` (`at_mm`, its sharpness, the elements it spans; null
  without one), `mach` (`value`, `limit`, `regime`: subsonic, transonic or supersonic, `checked`),
  `walls`, `reynolds` (from the solved mass flow), the wall pressure, Mach and temperature ranges,
  `force_N`, `solve` (steps, residual, any continuation) and `fluid_mesh`.
- Findings: `mach_past_checked` (warning, below), `shock` (info: where it stands),
  `gas_flow_unsettled` (warning), `mach_over_limit` / `pressure_drop_over_limit` /
  `velocity_over_limit` (error) and their `_close_to_limit`, `flow_balance`, static ones mapped.
- A march that does not settle (a limit cycle at a shock or along a wall: its residual not halving
  in 40 steps stops it early, at its best state) is [not settled](cfd.md#what-comes-out) and
  `gas_flow_unsettled` says so.

## The speed warning (never a refusal)

The fastest flow it is checked to is a normal shock in a nozzle's diverging part with **Mach 1.8
just ahead of it as captured** (1.96 by the tables). Past that, or when the gas leaves the outlet
faster than sound, it still solves (or says how far it got) and warns, in a sentence and as data
(`summary.mach`, `extras.analysis.mach`): "Mach 2.10 is past Mach 1.8, the fastest this solver
is checked to: ...", or "... the gas leaves the outlet faster than sound: the outlet's pressure is
held there though a supersonic exit sets its own ...". A supersonic exit (a nozzle blowing at its design pressure ratio or below) is not
modelled well: report the throat's numbers and the mass flow, which the exit does not change once
the throat chokes, and say the exit's are not to be trusted.

## Judging the answer (a hand check with the isentropic tables)

With p0 and T0 the reservoir's, M the Mach number and gamma 1.4 (air):

- T / T0 = 1 / (1 + 0.2 M²), p / p0 = (T / T0)^3.5, and A / A* = (1/M) [(1 + 0.2 M²) / 1.2]³.
  Mach 0.3: p / p0 = 0.939, Mach 0.5: 0.843, Mach 1: 0.528.
- Subsonic nozzle: the outlet's p / p0 gives the exit Mach number, it A_exit / A*, then each
  section's Mach number its own A / A*. Matched within 1 % at every section of a gentle nozzle at
  p_out / p0 = 0.95 and 0.92, 1.6 mm elements in a 4 mm throat.
- Choking: once the throat reaches Mach 1, no lower outlet pressure raises the mass flow
  ṁ = p0 A_throat sqrt(gamma / (R T0)) (2 / (gamma + 1))^3 = 0.0404 p0 A_throat / sqrt(T0) (SI);
  matched within 0.2 %.
- A normal shock: in a converging-diverging nozzle with p_out / p0 between the subsonic-choked value
  and the one for a shock at the exit, a shock stands in the diverging part: p_exit A_exit /
  (p0 A_throat) fixes the exit Mach number, the total pressure lost fixes the shock's Mach number,
  and its A / A* is where it stands. Benchmark (exit to throat area 1.69, p_out / p0 = 0.75): the
  tables put it 12.6 mm into a 25 mm diverging part with Mach 1.69 ahead; the solver within 2 % of
  the diverging length, the Mach ahead smeared to about 1.5. At p_out / p0 = 0.6 (Mach 1.96 ahead)
  within 2 % too.
- Slow flow: under Mach 0.3 it matches `cfd`'s laminar pipe pressure drop within 1 % (and warns).

## Limits

- Steady, ideal gas (p = ρ R T), adiabatic with one total temperature (the inlets' mean): the
  temperature follows the speed. No heat through the walls, no combustion, no condensation.
- No turbulence model: friction loss in a long fast gas line is not counted (frictionless walls) or
  a lower bound (laminar walls past the laminar range, which it warns about).
- Shocks are captured by an added bulk viscosity over about three elements (a finer mesh places
  them more sharply): their place is good to about two elements, the Mach number just ahead of a
  strong shock reads low, and a shock touching the outlet or the inlet is unreliable.
- An outlet holds its static pressure: a supersonic outflow is not modelled (it warns). And the
  [flow pages' shared limits](cfd.md#limits).

## When the model is big or the flow is hard

Ladder ([shared rungs](study-file.md#shared-by-every-analysis)), in order:

- `fluid_coarsen`: as [`cfd`'s](cfd.md#when-the-model-is-big) (free stream and far field coarser).
- `continuation`: when the pressure ratio could choke the passage (the drive expanded to the outlet
  passes Mach 0.5), the pressure difference stepped up (50 %, then 100 %), each stage from the one
  before: "Reached the full pressure ratio in steps (50%, 100% of the drive) from a gentler flow,
  so the march starts the choked flow near its answer". Exact (the same steady flow). Without the
  rung, a march that does not settle falls back to it by itself.
- `iterative`: GMRES with a multigrid preconditioner; a stalled Krylov solve is finished by the
  direct solver, and it says so.
