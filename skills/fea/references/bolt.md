# Bolted joint (`bolt`, lite)

It answers "will this bolted joint hold": how hard each bolt pulls once tightened and loaded (its
preload plus its share of the load), whether the clamped faces open (separation) or slide (slip:
friction against the sideways force), and how hard head, nut and clamped faces press. **Lite; its
limits (in every result):** each bolt is a pretensioned spring, not a meshed bolt; it is linear
elastic with no thread; no fatigue of the bolt yet; and contact's own limits for the clamped faces.

## When to use it

- Bolts or screws with a preload (a tightening torque) clamping two parts: "is an M8 enough", "does
  the flange open under this pressure", "does the bracket slip under this side load", "what preload
  does 25 N·m give", "how much of the load does the bolt see".
- Not for: glued or welded parts (`bonded`), parts only pressing with no bolt (`contact`), a bolt's
  fatigue life (not yet: say so), a fitted bolt loaded in shear through its shank (here the shank
  never bears on the hole), thread stripping, a gasket.

## The study

A [`contact`](contact.md) study whose `connections` name bolts. Keys beyond the
[shared ones](study-file.md#shared-by-every-analysis):

- `connections`: one entry per bolt,
  `{"between": ["plate", "bracket"], "type": "bolt", "size": "M6", "preload_N": 5000, "holes": [...]}`:
  - `between`: the part under the head, then the part under the nut (or the tapped part), by
    occurrence ref or name. The two must touch where the bolt clamps them.
  - `size`: `"M3"` to `"M30"` (ISO coarse thread: its stress area, clearance hole and head bearing
    diameter come from the size), or `{"diameter_mm": 7}` (scaled from the nearest size).
  - Exactly one of `preload_N` (the clamp force it is tightened to) or `torque_Nm` (the tightening
    torque) with `nut_factor` K (0.2 by default: dry steel; about 0.15 oiled, 0.12 anti-seize).
  - `grade`: the property class, `"8.8"` by default (`"4.6"` to `"12.9"`, stainless `"A2-70"`,
    `"A4-80"` ...). Its proof load (proof stress times stress area) is the bolt check's limit.
  - `friction` (clamped faces): 0.2 default (dry steel), about 0.1 oiled, 0.3 to 0.5 blasted/painted.
  - `holes` (find them with `cadgen fea faces`): the cylindrical hole walls the bolt passes through,
    on either or both parts (axis and hole fitted to them; head and nut bear on an annulus from the
    hole out to the head's bearing diameter on the outermost flat faces), or the flat faces head and
    nut sit on, one on each part (a spot face or a washer face: all of it bears).
  - Several bolts may clamp one pair (an entry each, sharing the friction), in frictional contact on
    its own (add no `contact` for it); other pairs may be `contact`, `free` or bonded (the default).
- `fixtures` (or `rigid_planes`) and `loads`: as `contact`; without `loads`, solved at the preload
  alone. A part need not be fixed itself: the bolt and the contact hold it.
- `steps`: equal load steps after the preload, 1 to 200, default 5. A preload or load step whose
  contact forces do not settle is cut, and at the smallest step kept and marked as
  [contact does](contact.md#the-study): "Contact did not settle at 80% of the preload: ... not
  reliable", every check failing.
- Checks, judged at the last load solved (default: the first three). A path stopping short ("Stops
  at about 46 % of the load", contact not followed further) judges each on its own number there; the
  stop leads the takeaway, and all holding there is "Stops short of the load", never a pass.
  - `bolt_load`: each bolt's force after loading against `limit_N`, else its proof load; close past
    0.9. "Bolt overloaded" / "Close to the limit" / "Bolt holds", row "Bolt 6.1 kN, limit 12 kN".
  - `joint_separation`: the preload's clamp lost. Fails when all is (the faces open), or the clamp
    falls under `min_clamp_N` (optional, what a seal or joint needs to keep); close past 0.9. "Joint
    opens" / "Close to opening" / "Joint stays shut", row "Lost 1.9 kN, of 5 kN clamp".
  - `joint_slip`: sideways force on the clamped faces against friction × clamp; close past 0.9.
    "Joint slips" / "Close to slipping" / "Joint holds by friction", row "Sideways 950 N, friction
    holds up to 1 kN", at it "Sideways 2 kN, at the friction limit: slips", past it "Sideways 3 kN,
    friction holds only 2 kN"; CLI "sideways 1957 N, reaching the 1957 N friction holds, so it slips".
  - `stress` (needs every material's yield), `displacement`, `contact_pressure`: as `contact`.
- No load control (not proportional to the load). Drives: `frame` (the load-step scrubber, from
  "Preload"), `field`, `deformation`, `threshold`. The viewer's Load row plays it as kinematics: snug
  to preload (its solve ramped, said "ramped"), then the solved load steps; presets Snug, Preloaded,
  Service load; Preview's playbar the same.

A plate bolted to a bracket with one M6, tightened to 9 N·m, pulled off by 3 kN:

```json
{"analysis": "bolt", "material": "steel", "fixtures": [{"faces": ["#o1.2.f1"]}],
 "connections": [{"between": ["plate", "bracket"], "type": "bolt", "size": "M6", "torque_Nm": 9,
                  "grade": "8.8", "friction": 0.2, "holes": ["#o1.1.f7", "#o1.2.f7"]}],
 "loads": [{"faces": ["#o1.1.f2"], "type": "force", "vector_N": [0, 0, 3000]}],
 "view": {"checks": [{"kind": "bolt_load"}, {"kind": "joint_separation"}, {"kind": "joint_slip"},
                     {"kind": "stress"}]}}
```

## How it is solved

1. The clamped parts are meshed apart and pressed by augmented-Lagrange contact; each bolt is an
   axial spring between head and nut bearing areas, each end a uniform pressure (stiffening nothing).
2. The preload pulls head and nut together as the clamped faces settle sliding freely (as the nut
   turns); the bolt is then locked to carry exactly the preload ("Preload", the series' first frame).
3. The loads, in `steps` steps: each bolt a spring of VDI 2230's stiffness for a plain shank through
   the clamp, head and nut; the clamped faces stick until the sideways force passes friction.

## What comes out

- A series opening on "Preload" (0 %), then the load steps ("60 % load"): `von_mises`,
  `displacement`, `contact_pressure` (between the clamped faces and under each head and nut). Study
  names each bolt ("M6 bolt, 5 kN preload, clamps plate and bracket"), its loaded force the hint.
- Summary: `status` ("Joint holds", "Joint opens", "Joint slips"), `bolts` (per bolt: `preload_N`,
  `force_N` after loading, `increase_N`, `proof_N`, `stiffness_N_mm`, `clamp_length_mm`,
  `shortened_mm` (how much the shank was shortened to hit the preload), the head and nut bearing
  area and pressure, and `hand`: VDI 2230's bolt and joint stiffness and load factor Φ for this
  geometry), `joints` (per clamped pair: `preload_clamp_N`, `clamp_N` after loading, `open`,
  `opens_at_percent` (the share of this load the faces open at, from the clamp's fall), `shear_N`,
  `friction_holds_N`, `slips`, `slips_at_percent`), the contacts as `contact`. Curves:
  `bolt_force_N` and `clamp_force_N` against the load in %.
- Findings: `bolt_load` (info, per bolt: preload and loaded) or `bolt_overloaded` (error, past
  proof), `bolt_preload` (info, a torque turned into a preload), `bolt_slack` (warning),
  `joint_clamped` (info: the clamp left, the load it would open at) or `joint_opens` (error),
  `joint_slips` (error), and contact's findings for the clamped faces.

## Judging the answer (hand checks)

- **Torque to preload:** F = T / (K d). M6, 9 N·m, K = 0.2: F = 9000 / (0.2 × 6) = 7.5 kN.
  K scatters by ±25 % in practice: a torque-tightened preload is a range, not a number.
- **Proof load:** proof stress × stress area. M6 8.8: 580 MPa × 20.1 mm² = 11.7 kN; a common target
  preload is 70 to 75 % of it.
- **Load factor (VDI 2230):** of a separating load F_A brought in under head and nut, the bolt takes
  Φ F_A, the clamp loses (1 − Φ) F_A, Φ = k_b / (k_b + k_c). k_b, the bolt's stiffness:
  1 / [(0.5 d + l_K + 0.4 d)/(E A_N) + 0.5 d/(E A_d3)], A_N = π d²/4, A_d3 at the minor diameter;
  k_c, the clamped parts': a sleeve E π (D_A² − d_h²)/(4 l_K) when no wider than the head (a wider
  part's cone, VDI 2230 section 5.1.2.2, is stiffer). The summary's `hand` gives both. Benchmark:
  two round steel plates as wide as an M6 head (6 + 6 mm), 5 kN preload: hand Φ = 0.356, solved
  0.352; with a load brought in away from the bolt Φ is smaller (VDI's n < 1).
- **Separation:** the faces open at F_A = F_V / (1 − Φ). Benchmark: hand 7.76 kN, solved 7.72 kN;
  past it the bolt carries the whole load.
- **Slip:** it holds sideways up to μ × clamp × slip faces (an axial load lowers the clamp and so
  this). Benchmark: two 24 mm steel plates, μ = 0.2, 5 kN preload, one slip face: slips at 1.00 kN
  (hand 1.00 kN).
- **Bearing pressure** under the head: F / (π/4 (d_w² − d_h²)); M6 at 5 kN: 180 MPa; check it
  against the clamped material's limiting surface pressure (VDI 2230 lists it per material).
- Sanity: unloaded, the clamped faces carry exactly the preload (well within 1 %), reactions zero.

## Limits

- Each bolt is a pretensioned spring along its axis, not a meshed bolt: axial force only, spread
  evenly under head and nut, no bending. The shank never bears on the hole wall: once a joint slips,
  what happens next (the bolt in shear) is not modelled.
- Linear elastic, no thread: VDI 2230's plain-shank stiffness (fully threaded is about 10 to 20 %
  softer); past its proof load reported, not yielded. No bolt fatigue yet, no tightening torsion
  (the preload is the axial force alone). The faces settle sliding freely during the preload;
  friction holds from the tightened state on. [Contact's limits](contact.md#limits) apply to them.
  A part held by bolts and contacts alone is kept from drifting by a very weak spring; a slipping
  joint is reported as slipping, its displacement then meaning little.

## When the model is big

[Contact's ladder](contact.md#when-the-model-is-big): `iterative` (Newton-Krylov), `adaptive_steps`
(load steps grow while Newton settles quickly), `local_refine` (finer where contact pressure peaks:
under the heads, around the holes), `defeature` (far from the bolts; hole faces a bolt names are
never removed). `symmetry` is not taken: a bolt would be cut in two.
