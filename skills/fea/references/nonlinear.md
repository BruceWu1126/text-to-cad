# Permanent bend / Stretch (`nonlinear`, lite)

It answers "does it bend for good, and how much", "at what load does it give way" (a metal past
yield) and "how far does this rubber part stretch or squash" (large deformation). The load is
stepped from nothing to full and the part followed, so the answer is not a linear solve's line.

**Lite; its limits (in every result):** small-strain J2 metal plasticity with isotropic hardening
only, no rate or temperature effects, Neo-Hookean rubber, loads that keep their direction.

## When to use it

- A static study says the part yields (safety factor under 1): does it bend for good, by how
  much, does it still hold (a bracket overloaded once, a clip bent past its spring, a tab pushed
  flat)? "At what load does it give way?": pushed with more than it can take, it reports where it
  collapses ("Collapses at about 70 % of the load"), never a refusal.
- A rubber or elastomer part stretched or squashed by more than a few percent: a gasket, a grip,
  a bumper, a seal, a flexure in TPU.
- Not for: repeated or reversed loads (fatigue, cyclic plasticity), creep, impact or anything fast,
  heat softening, contact between parts (`contact`), or a metal past large strains (tens of
  percent: necking and tearing are not modelled).

## The study

Keys beyond the [shared ones](study-file.md#shared-by-every-analysis):

- `fixtures` and `loads`: required, body loads included; the full load, which the solver ramps up.
- `steps`: equal load steps to start with, 1 to 200, default 10. A step that does not converge is
  halved and retried (up to six times, to 1/64 of a step); it then carries on at the size that
  got through.
- `material` must say how it behaves past the linear range, one of:
  - Metal (J2 plasticity): `plasticity: {"tangent_MPa": 2000}` (or `tangent_MPa` at the top of
    the object), the slope of the stress-strain curve past `yield_MPa`, under E; `0` is perfectly
    plastic (the limit-load case). Steel's 2000 MPa is a common bilinear guess; use the grade's own
    curve (UTS minus yield over the strain to UTS) when the user has it.
  - Rubber (Neo-Hookean): `hyperelastic: {"model": "neo_hookean", "mu_MPa": 0.6, "bulk_MPa": 300}`,
    μ the shear modulus, the bulk modulus about 500 μ for a nearly incompressible rubber. It needs
    no `E_MPa`, `nu` or `yield_MPa` (its small-strain E and ν come from μ and the bulk; no yield).
  - Neither is refused with a sentence saying which block to add. In an assembly, a part with
    neither stays elastic (in a rubber study, an elastic part is a Neo-Hookean of its own E and ν;
    a metal part's plasticity is then left out, with a warning).
- Checks, judged at the last load carried; a part that collapses fails every check, whatever its
  numbers there:
  - `plastic_strain`: `limit_percent`, the most equivalent plastic (permanent) strain allowed.
    Close past 0.9 of it. Row line "0.4 % permanent, limit 0.2 %". The default with no
    `view.checks`: `{"kind": "plastic_strain", "limit_percent": 0.2}`, the 0.2 % offset that
    defines yield. A rubber reads 0 ("Springs back").
  - `stress` (refused for a rubber with no yield) and `displacement`: as static.
- No load control (`load_scale` is not a drive): the response is not proportional to the load.
  Drives: `frame` (the load-step scrubber), `field`, `deformation`, `threshold`; with no view the
  scrubber, the field and the deformation.

Metal (perfectly plastic steel, pushed past its collapse load), then rubber (a pad stretched):

```json
{"analysis": "nonlinear", "material": {"name": "steel", "plasticity": {"tangent_MPa": 0}},
 "mesh": {"size_mm": 2}, "steps": 10, "fixtures": [{"faces": ["#o1.f1"]}],
 "loads": [{"faces": ["#o1.f2"], "type": "force", "vector_N": [0, 0, -127]}],
 "view": {"checks": [{"kind": "plastic_strain", "limit_percent": 0.2},
                     {"kind": "displacement", "limit_mm": 2}]}}
```

```json
{"analysis": "nonlinear",
 "material": {"name": "rubber 50A (estimate)",
              "hyperelastic": {"model": "neo_hookean", "mu_MPa": 0.82, "bulk_MPa": 409}},
 "steps": 8, "fixtures": [{"faces": ["#o1.f5"]}],
 "loads": [{"faces": ["#o1.f6"], "type": "force", "vector_N": [0, 0, 30]}],
 "view": {"checks": [{"kind": "displacement", "limit_mm": 5}]}}
```

### Rubber from its Shore A hardness: a first guess, labelled as an estimate

No rubber is in the material table. Knowing only a hardness, offer Gent's relation as a first
guess, said as an estimate in the study ("estimate" in the material's `name`) and in your reply:
E ≈ 0.0981 (56 + 7.62336 S) / (0.137505 (254 − 2.54 S)) MPa, μ = E/3, bulk = 500 μ. A measured
stress-strain curve beats it; hardness to modulus scatters by ±30 % between compounds.

| Shore A | 30 | 40 | 50 | 60 | 70 | 80 |
| --- | --- | --- | --- | --- | --- | --- |
| μ_MPa | 0.38 | 0.56 | 0.82 | 1.20 | 1.84 | 3.12 |
| bulk_MPa | 190 | 282 | 409 | 601 | 920 | 1559 |

## What comes out

- Load steps as pseudo-time (`series.kind` "time", unit "%"), frames labelled "60 % load", at most
  24 (evenly spread, the last load carried always kept and opened on), scrubbed (Load step) and
  played (Play). Each has `von_mises` (MPa; for rubber the true stress), `displacement` and, for a
  metal, `plastic_strain` (equivalent, %). Nodal values never pass the integration points' range.
- Summary: `status` ("Carries the full load" or "Collapses at about 70 % of the load"),
  `collapsed`, `load_percent` (the last load carried), `collapse_percent`, `material_model`,
  `max_von_mises_MPa` (and the Gauss-point value), `max_displacement_mm`,
  `max_plastic_strain_percent` (metal), each with where, `applied_force_N` (carried),
  `requested_force_N`, `reaction_force_N`, `steps_requested`, `steps` (converged), `step_cuts`,
  `smallest_step_percent`, `newton_iterations`, `adaptive`, `frames`, `checks`. Curves: the largest
  displacement (and plastic strain) against the load in %.
- Findings: `collapses` (error: "The part collapses at about 70 % of the load: it carries about
  88 N, not the full load"), `bends_for_good` (error, the plastic_strain check failing),
  `stays_elastic` (info: nothing yielded, a static study says the same), `load_steps_cut` (info).
  CLI: the status, peak stress, displacement and permanent strain; what was carried of what was
  asked; the steps and Newton iterations.

### What "collapses" means

At each load step Newton's method looks for equilibrium. Where it finds none, or the part runs
away (a step moves it more than 500 times as far as the unloaded part would move for the same added
load: a plastic hinge has formed, or a rubber part snaps through), the step is halved, down to 1/64
of the starting step. When even that fails, the part has collapsed at the last load it carried.
That load is the result: report "collapses at about N % of the load (X N)", not a tool failure.

## Judging the answer (hand checks)

- A bar pulled past yield (bilinear): strain = σ/E + (σ − σy)/H, H = E·Et/(E − Et). Steel at
  350 MPa with Et = 2000 MPa: 0.00175 + 100/2020 = 5.1 %. The solver matches it to round-off.
- A cantilever's plastic collapse (rectangular b × h, length L, perfectly plastic): the fully
  plastic moment σy b h²/4 at the root, so P = σy b h² / (4 L), 1.5 times the load that first
  yields it. Solved within 5 % (the fixed face's constraint and the mesh read a little high:
  about 4.5 % at L/h = 13, 3 elements through the depth).
- Rubber in plain tension (nearly incompressible Neo-Hookean): nominal stress μ (λ − 1/λ²); μ
  = 0.6 MPa stretched to twice its length carries 1.05 MPa, matched within 0.2 %. In shear the
  shear stress is μ γ, linear even at large strain.
- Sanity: below yield it equals a static study; past it the stress stays near yield (perfectly
  plastic: never above) and the displacement grows faster than the load.

## Limits

- Metal plasticity is small strain: rotations and strains must stay small (a few percent); past
  that the displacement is indicative only. Von Mises (J2) yield, bilinear curve, isotropic
  hardening only: no kinematic hardening (Bauschinger effect), so reversed and cyclic loading are
  not modelled. Mean dilatation (a constant pressure per element) keeps plastic flow from locking.
- No rate or temperature effects (no creep, strain-rate hardening or heat softening). Rubber is
  Neo-Hookean (one constant μ, plus the bulk modulus): fair to stretches of about 100 % in tension,
  stiffening beyond not captured; no Mullins softening on reloading, viscoelasticity or damping.
- Dead loads keep size and direction as the part deforms (a pressure does not turn with the
  surface); fixtures are rigid; the load only steps up (no falling branch past a collapse).

## When the model is big

Ladder ([shared rungs](study-file.md#shared-by-every-analysis)), in order:

- `iterative`: Newton-Krylov: each Newton step solved by GMRES with a multigrid preconditioner (the
  hierarchy kept through a load step).
- `adaptive_steps`: the load steps grow (up to 4 times the starting step) while Newton converges in
  four iterations or fewer, and are still cut where it does not: fewer solves where the part answers
  smoothly. For a metal its note says the plastic strain is followed in larger steps; the collapse
  load is still found to within the smallest step. Exact for rubber.
- `local_refine` (around the stress peak), `defeature` (far from loads and fixtures), `symmetry`.
