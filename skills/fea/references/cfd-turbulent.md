# Turbulent flow (`cfd_turbulent`, lite)

What [Flow](cfd.md) answers (pressure lost through or around the part, speed, push on the part)
when the flow is turbulent: water in a pipe faster than a few cm/s, air in a duct, a stream past a
body. Steady Reynolds-averaged flow with k-omega SST and wall functions, in-house (no outside
software); `map_to_structure` adds a static solve as in `cfd`. Lite; its limits (in every result):
**"Steady RANS (k-omega SST), wall functions, incompressible".**

## When to use it

- A `cfd` result warned that its Reynolds number is past the laminar range ("Re 4200 is past the
  laminar range ..."): run the same study with `"analysis": "cfd_turbulent"` and report this one.
- Before running, work out Re as [`cfd`'s warning](cfd.md#the-reynolds-warning-never-a-refusal)
  does; above **Re 2000 internal** or **Re 1000 external**, start here.
- Below those use `cfd`: the flow is likely laminar, and a turbulence model overstates the pressure
  drop. This analysis still runs there and warns (`reynolds_laminar_range`).
- Not for gas faster than about Mach 0.3 (about 100 m/s in air), where its density changes: use
  [`cfd_compressible`](cfd-compressible.md). Not for heat transfer, free surfaces, or anything
  time-varying (vortex shedding shows only as its steady average).

## The study

Exactly `cfd`'s study ([cfd.md](cfd.md#the-study)), keys beyond the [shared
ones](study-file.md#shared-by-every-analysis): `flow`, `fluid`, optional `map_to_structure`, checks
`pressure_drop` and `velocity` (plus `stress` and `displacement` when mapped). One key is added:

- `turbulence_intensity`: the fluctuating share of the mean speed. Internal flow: per inlet
  (default `0.05`, 5 %). External flow: on `flow` (default `0.01`, a calm free stream).

```json
{"analysis": "cfd_turbulent",
 "mesh": {"size_mm": 1.0},
 "flow": {"kind": "internal", "fluid": "water",
          "inlets": [{"opening": "x_min", "velocity_m_s": 1.0}],
          "outlets": [{"opening": "x_max", "pressure_Pa": 0}]},
 "view": {"checks": [{"kind": "pressure_drop", "limit_Pa": 50},
                     {"kind": "velocity", "limit_m_s": 3}]}}
```

External: `"flow": {"kind": "external", "fluid": "air", "velocity_m_s": [20, 0, 0], "turbulence_intensity": 0.01}`.

- An inlet's `profile`: `"developed"` (default) is the settled turbulent flow of a long straight
  duct of the opening's own shape (its speed, k and omega, solved on the opening by the same
  model); the intensity is not read. `"uniform"` is one speed everywhere, with k = 1.5 (I U)² from
  the intensity and a length scale of 0.07 D; it adds the entrance's extra loss (a 2-diameter pipe
  fed uniform reads about 25 % over the developed drop).
- `mesh.size_mm`: the element size at the walls and openings; default a tenth of the narrowest
  opening's hydraulic diameter (internal), L/8 (external, the free stream four times coarser).
  Turbulent flow needs a finer mesh than laminar: at a sixth of the diameter a pipe's drop reads
  about 10 % low.

## What comes out

- [Tracers](cfd.md#what-comes-out) carried by the solved (mean) velocity.
- Fields on the part's surface: `pressure`, `speed` and unwetted faces as `cfd`'s;
  `wall_shear` is the wall function's ρ u_τ². Mapped: `von_mises` and `displacement` too.
- Summary: everything `cfd` gives (`reynolds` with `turbulent` instead of `laminar`, the checks),
  plus `turbulence` (`model`, `wall_treatment`, the eddy-to-fluid viscosity ratio
  `viscosity_ratio_mean` / `viscosity_ratio_max`, and `wall_yplus` min / mean / max of the wall
  function's distance), `solve` (pseudo-time `steps`, `factorizations`, the last `residual`,
  `converged`) and, for a developed inlet, `developed_inlets` (each opening's friction factor from
  its own cross-section solve).
- CLI: as `cfd`'s, plus the eddy viscosity and wall y+. Findings: `reynolds_laminar_range`
  (warning), `turbulent_unsettled` (warning: the march stopped short of its tolerance), the check
  findings, `flow_balance`, and the static findings when mapped.

## Judging the answer (a hand check)

A smooth straight pipe, fully developed: Δp = f (L / D) ρ U² / 2, with Darcy's friction factor f
from Colebrook's equation (the Moody chart, smooth wall):

1 / √f = −2 log₁₀(2.51 / (Re √f)), solved by repeating it from f = 0.02.

| Re | 10,000 | 20,000 | 50,000 | 100,000 |
| --- | --- | --- | --- | --- |
| f (smooth) | 0.0309 | 0.0259 | 0.0209 | 0.0180 |

Water at 1.0 m/s through a 10 mm bore 15 mm long: Re ≈ 10,000, f = 0.0309, Δp = 0.0309 × 1.5 ×
998 × 1² / 2 ≈ 23 Pa; the wall shear is f ρ U² / 8 ≈ 3.9 Pa. The solver's benchmark (that pipe,
fed developed, 1 mm elements) reads Δp within 2 % at Re 10,000 and 4 % at Re 50,000, and the
test holds it to 10 %.

- Turbulent drop grows nearly as the square of the speed (U^1.75 in a smooth pipe): double the
  speed, about 3.4 times the drop. A drop growing in proportion to the speed is laminar: use `cfd`.
- Bends, steps and splits add about K ρ U² / 2 each (K of order 0.2-1.5); the solver includes them.
- External: drag F = Cd × ½ ρ U² A; a bluff body (a cube, a plate face-on) has Cd of order 1.
- Mapped: the hoop stress check as [`cfd`'s](cfd.md#judging-the-answer-a-hand-check).

## Limits

- No heat, gravity, free surface or time variation; no wall roughness (smooth walls); no
  compressibility.
- Wall functions: the first element is kept in the log layer (its wall distance at least y+ 30),
  so the flow next to the wall is the law of the wall, not resolved. Separation and reattachment
  on a smooth curved wall are approximate.
- The mean flow is Taylor-Hood (quadratic velocity, linear pressure) with streamline diffusion
  where the mesh is coarse for the speed; k and omega are linear, stabilised by SUPG.
- The [flow pages' shared limits](cfd.md#limits) (one part, bounding-box openings, wall values).

## How it solves

Pseudo-transient continuation: from a Stokes-like start with a mixing-length eddy viscosity (or
the free stream's), each step solves the mean flow with the eddy viscosity frozen and a local
pseudo-time term, then k, then omega, each with the new velocity. The pseudo-time step grows while
the change per step shrinks and is halved when it stalls; the march stops when the velocity
changes by under 1e-5 of the reference speed in a step. A developed pipe settles in about 15 steps;
a body in a free stream takes 100 or more. A run that does not settle returns its last state with
a `turbulent_unsettled` warning, never a refusal, its checks [not settled](cfd.md#what-comes-out).

## When the model is big

Ladder ([shared rungs](study-file.md#shared-by-every-analysis)), in order:

- `fluid_coarsen`: as [`cfd`'s](cfd.md#when-the-model-is-big): "Coarsened the flow mesh away from
  the walls to fit: walls and openings are still meshed at 1.25 mm, the free stream at 3.12 mm".
- `continuation`: the pseudo-time march in longer steps (CFL from 50, growing 4× a step): "Marched
  to the steady turbulent flow in longer pseudo-time steps ... so it settles in fewer solves". The
  same steady flow, to the same tolerance; the stall rule still halves the step where it must.
- `iterative`: GMRES with a multigrid preconditioner on the velocity and the pressure mass matrix,
  where that saves memory; a stalled Krylov solve is finished by the direct solver.
