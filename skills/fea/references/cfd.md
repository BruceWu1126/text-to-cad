# Flow (`cfd`, lite)

Steady, laminar, incompressible flow through or around the part, solved in-house (no outside
software); with `map_to_structure` the wall pressure is applied to a static solve, so stress and
displacement come too. Lite; its limits (in every result): **"Laminar, steady, incompressible; no
turbulence model."**

## When to use it

- "What pressure drop does this manifold / channel / nozzle cause at 0.5 m/s of water?"
- "How fast does the air go through this duct?", "is the flow split evenly between the outlets?"
- "How hard does the water push on this part?" (`map_to_structure`: "will the pressure crack it?")
- Flow around a body (`external`): the force of a slow air or water stream on it.
- Not for fast or turbulent flow (most air ducts at fan speeds, most water pipes above a few cm/s
  in a 1 cm bore): it runs and warns (below), but the numbers are a laminar lower bound; use
  [`cfd_turbulent`](cfd-turbulent.md). Gas past about Mach 0.3 (about 100 m/s in air), where its
  density changes: [`cfd_compressible`](cfd-compressible.md). The heat a flow carries into or out
  of the part (a coolant, a cold plate): [`conjugate_heat`](conjugate-heat.md), which solves this
  flow first. Not for free surfaces or anything time-varying.

## The study

Keys beyond the [shared ones](study-file.md#shared-by-every-analysis); the other flow pages take
this `flow` and link here for it. Internal flow (through the part):

```json
{"analysis": "cfd",
 "mesh": {"size_mm": 1.0},
 "flow": {"kind": "internal", "fluid": "water",
          "inlets": [{"opening": "x_min", "velocity_m_s": 0.5}],
          "outlets": [{"opening": "x_max", "pressure_Pa": 0}]},
 "map_to_structure": {"material": "aluminum-6061-t6", "fixtures": [{"faces": ["#o1.f1"]}]},
 "view": {"checks": [{"kind": "pressure_drop", "limit_Pa": 2000},
                     {"kind": "velocity", "limit_m_s": 3},
                     {"kind": "stress"}]}}
```

External flow (around the part): `"flow": {"kind": "external", "fluid": "air", "velocity_m_s": [5, 0, 0]}`.
- `flow.fluid`: `"air"` (1.204 kg/m³, 1.81e-5 Pa·s), `"water"` (998.2 kg/m³, 1.002e-3 Pa·s), both
  at 20 °C, or `{"density_kg_m3", "viscosity_Pa_s", "name"}` for anything else (oil, glycol, syrup).
- Internal flow. Openings name a side of the part's bounding box: `x_min`, `x_max`, `y_min`,
  `y_max`, `z_min`, `z_max`; a port must reach it (its face lies on it, with the hole in it).
  `cadgen fea faces` shows which sides the ports are on.
  - `inlets`: each `{"opening", "velocity_m_s", "profile"}`. `velocity_m_s` is the mean speed in
    (the flow rate is speed × the opening's area). `profile` is `"developed"` (default: the settled
    laminar profile of the opening's own shape, as if a straight pipe fed it) or `"uniform"`.
  - `outlets`: each `{"opening", "pressure_Pa"}` (gauge, default 0), where the flow leaves freely.
  - At least one inlet and one outlet; each side is one opening (an inlet and an outlet cannot
    share one). Every port must be named: a side with no opening is refused, naming the sides it
    does open on; a passage that also opens on an unnamed side is refused, naming it.
- External flow: `velocity_m_s` is the free stream `[x, y, z]`, in a box reaching 2 L upstream,
  5 L downstream and 2 L to the sides (L the part's largest extent). The sides the stream enters
  through are inlets, those it leaves through outlets at 0 Pa, the rest slip walls.
- `mesh.size_mm`: the element size at the part's walls (and an internal flow's openings); default
  a quarter of the narrowest opening's hydraulic diameter (internal), L/8 (external, the free
  stream four times coarser).
- `map_to_structure`: `{"material", "fixtures"}` as in a static study (`material` may sit at the top
  level; only a mapped flow needs one). The wall pressure is applied face by face as a pressure load
  (one-way; no wall shear); the static fields and checks join the result. A part bending enough to
  change the flow (a soft flap, a thin reed, a swelling hose): [`fsi`](fsi.md), two-way with shear.
- Checks: `pressure_drop` (`limit_Pa`, the drop from the inlets to the outlets) and `velocity`
  (`limit_m_s`, the fastest point anywhere in the fluid), each close past 0.9 of its limit; plus
  `stress` and `displacement` when mapped (refused without `map_to_structure`). No check by default.

## What comes out

- Fields on the part's surface: `pressure` (wall pressure, Pa gauge, signed), `wall_shear` (Pa)
  and `speed` ("Speed", m/s): the wall is still (no slip), so it is the fastest the fluid moves
  within one passage width of each wetted point (the inlet's hydraulic diameter; an external flow,
  two wall elements), reaching at least the fastest point of the flow, so the `velocity` check's
  value is the top of its colour bar. A face the fluid does not wet (a pipe's outside) reads 0.
  Mapped: `von_mises` and `displacement` too, and the part deforms.
- **Tracers** (every flow page; `extras.analysis.motion`, kind `tracers`): 64 particles let go at
  the inlets (as dense as the flow through them; external: across the part's width), carried by
  the solved velocity (P2, midpoint steps), points `step_s` of real time apart. Preview streams
  dots along them, slowed or sped up by one round factor it says, inside the file's Clip cut.
- Summary: `reynolds` (`value`, `limit`, `kind`, `length_mm`, `laminar`), `max_velocity_m_s` and
  where, `flow_rate_m3_s` and `flow_rate_L_min` (in), `outflow_rate_m3_s`, `flow_balance` (their
  mismatch), `pressure_drop_Pa`, `inlet_pressure_Pa`, `outlet_pressure_Pa` (area means),
  `min/max_wall_pressure_Pa`, `max_wall_shear_Pa`, `max_speed_near_wall_m_s` and `speed_reach_mm`
  (how far the speed field looked), `force_N` (the push on the part, pressure and friction), `solve`
  (Picard and Newton steps, residual, continuation stages), `fluid_mesh` (elements, wall and
  free-stream sizes), `checks`; mapped, `max_von_mises_MPa`, `max_displacement_mm`, `yield_MPa`,
  `safety_factor`.
- CLI: the flow and its Re, the pressure drop, flow rate and fastest speed, the wall pressure
  range, shear and force, the solver steps, and the mapped stress. Findings:
  `reynolds_past_laminar`, `..._close_to_limit`, `flow_unsettled` and `flow_balance` (in and out
  differ by over 1 %) warnings, `pressure_drop_over_limit` / `velocity_over_limit` errors,
  `continuation` (info), and the static findings when mapped.
- A solve stopping short of its tolerance (1e-8) or at its time limit (the study's time target, or
  three times the estimate when the run went past it, never before three times the linear solves
  it is expected to need, continuation counted above Re 100: a loaded machine makes the solve
  slower, not shorter) returns its best state at the full Re: "the flow solve stopped at a residual
  of ..., its numbers are approximate". **Not settled** (every flow page): each check reads
  `settled: false`, its label ends "(flow not settled)", a pass becomes `close`, the CLI's solve
  line ends "(not settled)". Progress (step, Re stage, residual, seconds) logs at most every 10 s.

## The Reynolds warning (never a refusal)

Re = ρ V D / μ, with D the inlet's hydraulic diameter (4 × area / perimeter) for internal flow and
the part's largest extent for external flow. Above **Re 2000 internal** or **Re 1000 external**
the run still solves (reaching that Re by continuation when it must) and warns: "Re 4200 is past
the laminar range (laminar above Re 2000 is unreliable): real flow is likely turbulent, so this
pressure drop is a lower bound and the flow pattern may be wrong; run the same study as
cfd_turbulent for the turbulent answer". The number is also data, `summary.reynolds` (and
`extras.analysis.reynolds`): the verdict reads "Laminar · unreliable above Re 2000". Tell the user
the number, quote the drop only as a lower bound, then run the same study as
[`cfd_turbulent`](cfd-turbulent.md) (k-omega SST, wall functions) and report its drop.

## Judging the answer (a hand check)

- Straight round pipe (Hagen-Poiseuille): Δp = 8 μ L Q / (π R⁴), the parabola u = 2 U (1 − r²/R²),
  wall shear 4 μ U / R. Water at 0.01 m/s through a 4 mm bore 16 mm long: Q = 1.26e-7 m³/s,
  Δp = 0.32 Pa, τ = 0.020 Pa; matched within about 1 % (drop) and 2 % (profile), default mesh.
- Straight rectangular duct (Shah & London): Δp = (f·Re / 2) μ U L / D_h², D_h = 2 W H / (W + H),
  f·Re = 56.91 square, 62.19 at 2:1, 96 wide parallel plates. Water at 0.01 m/s through a 3 × 3 mm
  duct 16 mm long: Δp = 0.51 Pa, matched within about 3 % (sharp inlet corners need no rounding).
- Any duct, laminar: Δp grows in proportion to the speed (double it, double the drop); turbulent
  nearer the square, so a measured drop growing faster than the speed is not laminar. Bends, steps
  and splits add roughly K ρ U² / 2 each (K of order 0.5-1.5); the solver includes them, laminar.
- External: drag F = Cd × ½ ρ U² A; at low Re, Cd ≈ 24/Re for a sphere (Stokes), of order 2-3 for
  a box near Re 30. Sides 2 L away: a slow flow round a wide body reads a little high (blockage).
- Mapped: a tube under internal pressure p has a hoop stress at the bore of p (Ro² + Ri²)/(Ro² − Ri²).

## Limits

- No heat, gravity, free surface or time variation (vortex shedding shows only as a steady average,
  if at all).
- Galerkin Taylor-Hood elements with streamline diffusion where the mesh is coarse for the speed
  (cell Péclet over 1): it steadies a fast flow on a coarse mesh at some cost in accuracy where the
  flow changes along a streamline. Fully developed pipe flow is untouched.
- Shared by every flow page: one part only (pick one of an assembly with `--occurrence`); openings
  are bounding-box sides; wall values reach the part's faces from the nearest fluid node on the
  same face. The developed inlet profile assumes a straight feed; `"uniform"` adds an entrance loss.

## When the model is big

Ladder ([shared rungs](study-file.md#shared-by-every-analysis)), in order:

- `iterative`: MINRES for the Stokes start, GMRES after, multigrid-preconditioned on the velocity
  and the pressure mass matrix; a stalled Krylov solve is finished by the direct solver.
- `fluid_coarsen`: the free stream and far field 2.5× coarser each time, the part's walls (and an
  internal flow's openings) kept at the size asked for: "Coarsened the flow mesh away from the
  walls to fit: the part is still meshed at 2 mm, the free stream at 16 mm". Its note: the core is
  coarser; the pressure drop and wall shear are set at the walls. Skipped where every element is
  near a wall (a narrow pipe).
- `continuation`: above Re 100, when it saves solves, Re stepped up (Re/8, Re/4, Re/2, Re), each
  stage from the one before. Exact (the same steady flow). Without the rung the solve still falls
  back to it when Newton does not converge from the Stokes start (a `continuation` info finding).
