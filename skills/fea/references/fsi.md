# Flow and bending (`fsi`, lite)

The flow pushes the part and the part bends: how far, how hard, and does the bend change the flow.
Steady two-way, in-house: [`cfd`](cfd.md)'s or [`cfd_turbulent`](cfd-turbulent.md)'s flow and
`static`'s linear elastic bending, iterated until they agree. Lite; its limits (in every result), the flow's own too:
**"Steady flow two-way coupled to a linear elastic part: no flutter, vortex shedding or other
unsteady motion; small-to-moderate deflection, the flow mesh following the part (ALE mesh
motion)."**

## When to use it, and when one-way is enough

- "How far does this flap, reed, fin or valve leaf bend in the flow, and how hard is it stressed?"
  when it bends enough to change the flow; "does this soft tube or hose swell under its own flow,
  and how much does that lower the pressure drop?"
- One-way is enough when the part barely moves (a stiff manifold, a metal pipe, a bracket in a
  breeze): `cfd` with `map_to_structure` ([cfd.md](cfd.md#the-study)), one flow solve, the wall
  pressure onto the part once. Rule of thumb: a largest displacement under about 1 % of the gap or
  opening the flow passes through changes the answer by no more than a few percent. Every `fsi`
  result reports the one-way answer beside its own, so one run tells you whether it mattered.
- Not for anything that moves in time: flutter, galloping, vortex-induced vibration, a valve that
  slams. Not for large deflection (a flap whose tip moves more than about a tenth of its length),
  contact (a reed closing on its seat) or a part that yields.

## The study

Keys beyond the [shared ones](study-file.md#shared-by-every-analysis):

```json
{"analysis": "fsi", "material": {"name": "silicone", "E_MPa": 5, "nu": 0.3, "yield_MPa": 3},
 "mesh": {"size_mm": 0.8},
 "fixtures": [{"faces": ["#o1.f9"]}],
 "flow": {"kind": "internal", "fluid": "water", "regime": "auto",
          "inlets": [{"opening": "x_min", "velocity_m_s": 0.5}],
          "outlets": [{"opening": "x_max", "pressure_Pa": 0}]},
 "coupling": {"method": "aitken", "tolerance": 0.001, "max_iterations": 25},
 "view": {"checks": [{"kind": "stress"},
                     {"kind": "displacement", "limit_mm": 0.5},
                     {"kind": "pressure_drop", "limit_Pa": 2000}]}}
```

- `flow`: everything a [`cfd`](cfd.md#the-study) study's `flow` takes, plus `regime`: `"auto"`
  (default: laminar below Re 2000 inside the part, Re 1000 around it, turbulent above),
  `"laminar"` or `"turbulent"`. A turbulent flow takes `turbulence_intensity` as
  [cfd-turbulent.md](cfd-turbulent.md#the-study) says; a laminar one refuses it.
- `material` and `fixtures` (both required, as in a static study); no `loads`: the flow is the load.
- `coupling` (optional):
  - `method`: `"aitken"` (default: Aitken's dynamic relaxation, settling most couplings in 3-8
    iterations) or `"fixed_point"` (the same share of each new displacement every time).
  - `relaxation`: the first share taken (Aitken) or every share (fixed point); default 1.
  - `tolerance`: settled when the displacement and the load each changed by less than this share
    of themselves in the last iteration; default 0.001. `max_iterations`: default 25.
- `mesh.size_mm`: the element size of the part and the flow's walls and openings. Every face the
  fluid wets moves with the part; the fluid's own openings stay put.
- Checks: `stress` (default), `displacement` (`limit_mm`, optional `faces`), `pressure_drop` (`limit_Pa`).

## What comes out

- [Tracers](cfd.md#what-comes-out) stream past the part as bent (the moved fluid mesh). Fields, at
  the settled state: `von_mises` (MPa, opened on), `pressure` (wall, Pa, signed), `wall_shear` (Pa),
  `displacement` (the shape, exaggerated by the deformation control).
- Summary: the flow's numbers as `cfd` gives them (`reynolds`, `flow_regime`, `pressure_drop_Pa`,
  `flow_rate_L_min`, `max_velocity_m_s`, the wall pressure and shear ranges, `force_N`); the part's
  (`max_von_mises_MPa`, `max_displacement_mm` and where, `safety_factor`); `coupling` (`method`,
  `iterations`, `converged`, `residual`, `load_change`, `tolerance`, each iteration's `history` and
  `relaxation`, `ramped`, `flow_solves`); `one_way` (the deflection, stress, pressure drop and force
  of the flow at rest pushing the part once) and `two_way_vs_one_way` (`displacement_change_pct`,
  `pressure_drop_change_pct`, `pressure_drop_change_Pa`).
- CLI: "internal laminar flow of syrup: Re 0.25, coupled two ways with the part's bending",
  "coupling: 4 iterations (aitken), converged to 2.2e-05", "largest displacement 0.5284 mm (one-way
  0.568 mm, -6.96%)", "pressure drop 1414 Pa (one-way 1413 Pa, +0.0856%) ...".
- Viewer: Study says "Water at 0.5 m/s bends the flap" (flow in and out, held at, made of); Details
  "Coupling: settled in 4 iterations"; the takeaway leads "Laminar · " or "Turbulent · ".

## How it is solved

A partitioned, strongly coupled iteration on two meshes, the part's and the fluid's:

1. The flow is solved around the part as it stands (Taylor-Hood Navier-Stokes, or RANS k-omega SST).
2. Its traction on each wetted wall triangle (pressure along the wall's normal, viscous shear from
   the velocity gradient in the element the triangle closes; turbulent: the wall function's) goes to
   the part's nearest surface triangle on the same face: pressure along the part's own normal, shear
   as a vector, integrated as a static pressure load is (an even pressure gives exactly static's).
3. The part's linear elastic bending is solved (its stiffness factored once).
4. The displacement at each fluid wall node (the quadratic field on the part's surface triangles)
   is carried into the fluid by a harmonic mesh-motion solve, each element's stiffness the inverse
   of its volume, so small elements next to the part move almost rigidly (ALE). Openings stay put;
   a node on an opening's rim keeps to its plane.
5. The displacement is relaxed (Aitken, or a fixed share); repeat until it and the load settle. The
   first pass, the one-way answer, is kept for the summary.

A relaxed step that would fold the moved flow mesh over is halved, up to four times. A coupling
that grows for three iterations running is restarted once with the inflow ramped up in steps (25 %,
50 %, 100 %) and a first relaxation of at most 0.5, each stage starting from the one before.
Each flow solve prints a progress line every 10 s ("flow solve 2: flow: Newton step 3 ...") and
stops at what is left of the coupling's time budget (twice the study's `fit.seconds` or of the
default 600 s, whichever is longer; at least 30 s each); the coupling starts no iteration past it.
Such a run reports its last state as not settled: "the coupling stopped at its time budget", plus
the flow's own "the flow solve stopped at its time limit ... approximate" when that solve did not.

## Judging the answer (hand checks)

- A soft tube swelling under its own flow, away from its ends: the bore grows by δ = p R_m² (1 − ν²)
  / (E t), p the local wall pressure (both ends held; a free end drops the (1 − ν²)). Tested: a 2 mm
  bore, 0.2 mm wall, E 1 MPa agrees within 5 % along its length; a 2 mm bore, 0.5 mm wall, E 0.5 MPa
  swells evenly, within 15 %, its one-way deflection exactly four times the same tube's at E 2 MPa,
  two-way settling in a few iterations a little below one-way.
- Its pressure drop: Poiseuille with the radius following the pressure, dp/dx = −8 μ Q / (π a⁴),
  a = R + c p, c = R_m² (1 − ν²) / (E t), integrates to a_in⁵ = R⁵ + 5 c (8 μ Q / π) L; the test's
  tube agrees within 1 % (3027 Pa against 3010 Pa; rigid, 3200 Pa).
- A flap in a stream leans less two-way than one-way (leaning, it blocks less flow and is pushed
  less): the test's soft flap 7 % less (0.528 mm against 0.568 mm); fixed relaxation to ten times
  tighter tolerance lands within 0.2 %.
- Turek and Hron's FSI1 (a flag behind a cylinder in a 2D channel, Re 20) is not reproduced: it is
  2D, and a quasi-2D slab here has no-slip walls on its faces, so its answer would not be FSI1's.

## When it does not settle

The run never refuses: it reports its last state and the change it still made, saying "the flow
and the bending did not settle in N coupling iterations ... this is the last state, not a converged
answer" (warnings, a `coupling_not_converged` finding, the CLI's "NOT converged", the verdict's
"not converged"). Say so first when you report, then offer, in order:

- raise `coupling.max_iterations` (a residual still falling only needs more); lower
  `coupling.relaxation` (0.5, 0.3), or `"fixed_point"` with a small one, for one that overshoots
  back and forth; solve a slower flow first, whether the deflection is still small-to-moderate;
- a part that bends so far the flow mesh folds ("the moved flow mesh folded over") is past this
  solver's range: stiffen it in the model, or report the one-way answer as an upper bound.

A turbulent flow settles less tightly (the RANS march has its own tolerance): a very soft part in
it may stop short of the default tolerance.

## Limits

- The answer is the balance the flow and the part settle to, if they settle. The load is the
  deformed flow's, applied to the part's shape at rest.
- The fluid mesh is moved, never rebuilt: deflections past a fraction of the gap next to the part
  fold it. The [flow pages' shared limits](cfd.md#limits) (one part, bounding-box openings).

## When the model is big

Ladder ([shared rungs](study-file.md#shared-by-every-analysis)), in order: `continuation` (above
Re 100: the inflow ramped up in steps, each flow solve reaching its Re by continuation),
`iterative` for the flow and the bending, `fluid_coarsen` (the core coarser, walls kept), and
`local_refine` (around the peak stress, its note how far the peak moved; taken only when the
part's solve is most of the cost, as two passes repeat the whole coupling).
