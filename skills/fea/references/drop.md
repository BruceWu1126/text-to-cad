# Drop (estimate) (`drop`)

**An estimate, never an impact simulation.** It answers "what if I drop it" quickly: one equivalent
steady load, G g on the whole part, the faces that hit the floor held fixed, solved as a static
study. Say "estimate" every time you quote it, and the stopping distance or time you assumed (it
sets the answer). For a deeper check, [`impact`](impact.md) simulates the impact itself.

## When to use it

- "Will it survive a 1 m drop onto concrete", "what if it falls off the desk"; a quick ranking of
  designs or drop orientations (which face to land on hurts most).
- Not for: how far it bounces, a crack growing, a thin shell denting, a part hitting another, or
  anything that depends on the stress wave. Those need `impact`.

## The study

Keys beyond the [shared ones](study-file.md#shared-by-every-analysis); reads density:

```json
{"analysis": "drop", "material": "abs", "drop": {"height_mm": 1000, "onto": ["#o1.f5"], "stop_mm": 2},
 "view": {"checks": [{"kind": "stress", "label": "Drop"}]}}
```

- `drop.height_mm`: how far it falls, in mm (1000 for 1 m). `drop.onto`: the faces that hit the
  floor, held fixed. No `fixtures` or `loads` keys: the drop makes both.
- How it stops, exactly one of:
  - `stop_mm`: distance to stop (constant deceleration), G = height / stop: 1000 mm in 2 mm is 500 g.
  - `impact_ms`: the impact's length, a half-sine pulse peaking at π v / (2 τ), impact speed
    v = √(2 g h): 1 m stopping in 1.5 ms peaks at 473 g.
- `drop.direction` (optional): the way it falls, `[x, y, z]`, like `[0, 0, -1]` for straight down.
  Left out, the landing faces' mean outward normal (the bottom face's -Z for a part dropped flat);
  give it when they face different ways (an edge or a corner landing).
- `drop.dynamic`: `true` also runs a `transient` check ([transient.md](transient.md)) on the same
  mesh: the landing faces held and shaken by a half-sine pulse of the estimate's peak G g and the
  impact's speed change (τ = π v / (2 G g), `impact_ms` when given), followed until the first mode
  has rung once after it, 2 % damping. The larger response is reported: when the pulse's peak stress
  passes the same peak held steady, the static solve is repeated at that many times G (an equivalent
  steady load), so every number, field and check carries it. The summary's `dynamic` block:
  `pulse_ms`, the transient's `max_von_mises_MPa`, `factor` (its peak over the steady one),
  `first_mode_Hz`, `governs`, `G_reported`; a CLI line and info finding `drop_dynamic` say it in
  words. A part ringing much faster than the pulse answers it as the steady load (factor about 1,
  the estimate governs); a first mode near 1 / (1.3 τ) can reach about 1.7×. Off (or `false`): the
  estimate alone.
- Checks: `stress` (labelled "Drop" unless you give a label) and `displacement`, as for static.

### Choosing the stopping distance

Nothing in the part tells the solver how it stops: the floor and whatever crushes do. Pick a
number, say it, offer a range when the answer is close: hard plastic or metal on concrete or tile
0.5 to 2 mm (or 0.5 to 2 ms); wood, carpet, a rubber foot or bumper 2 to 10 mm; packaging foam, its
crush, often 10 to 50 mm. Halving the stop doubles the load and every stress: a part passing at
2 mm with a safety factor under 2 fails at 1 mm.

## What comes out

- Summary: `estimate: true`, `estimate_line`, `drop` (height, stop, impact speed in m/s, `G`, the
  travel `direction`, the `acceleration_g` solved) and every static number: peak von Mises, safety
  factor, displacement, applied force (mass × G g) and the reactions that balance it.
- The CLI's first line is the estimate line, word for word: "Estimate: 1 m drop stopping in 2 mm,
  500 g equivalent static load; not an impact simulation (analysis impact simulates the impact)".
- The GLB's `extras.analysis`: `estimate: true`, `tier: 2`, the limits; verdict "Estimate · OK up to
  1.6× this drop". `extras.study.drop` echoes the drop; `loads`, the equivalent `acceleration`.
- `extras.analysis.fall`, what the viewer moves it by: `height_mm`, unit `direction`, `speed_m_s`,
  `floor` (`point_mm`, where it lands first, `normal` facing up), `impact_ms`, the assumed stop's
  length (2 d / v for `stop_mm`, the half-sine for `impact_ms`, the pulse with `dynamic`).
- The viewer shows it as kinematics, falling straight down onto a see-through floor; What you see's
  **Time** row scrubs height, floor, the stop (stress and deformation ramping to the estimate's peak
  and back) and rest, valued in real time from landing; presets Released, Contact, Peak, At rest,
  opening on Peak; Preview's playbar plays **Drop**, the stop slowed ("slowed 2,000×"). The motion
  is illustrative: the numbers are the estimate's steady load, nothing in between is solved.
- An `info` finding `drop_estimate` says it is an estimate and names the stopping distance assumed.
  The load control scales the drop: 2× is the same drop stopping in half the distance.

## Judging the answer (a hand check)

- The result equals the static study with `onto` fixed and an `acceleration` of G g pointing away
  from the floor, to the last digit.
- The applied force is mass × G × 9.81 N: a 6 cm³ ABS block (6.24 g) at 500 g is 30.6 N.
- Landing flat on its base, loaded by its own inertia, the stress grows with height above the base,
  σ ≈ ρ · G · g · h: a 10 mm tall ABS block at 500 g, 1.04e-9 × 500 × 9806.65 × 10 ≈ 0.05 MPa,
  small. It concentrates where mass hangs off a thin section (a boss on a thin wall, a cantilevered
  tab, a heavy part on a thin stand).
- An overhanging arm of length L, landing on its base, is a cantilever under its own weight times G:
  root stress ≈ G × (its weight per length) × L² / 2 / its section modulus.

## Limits

- One equivalent steady load: no stress wave, no rebound, no vibration after the hit, no floor
  stiffness, no contact beyond the faces held, no damage or permanent bending. Real peaks at the
  impact point and in fast-ringing thin features can be higher.
- The landing faces are held perfectly rigid. Linear material below yield: a safety factor under 1
  means it yields or cracks, how badly not modelled. Report it as an estimate of an equivalent
  steady load, with the stopping distance assumed.

## When the model is big

[Static's ladder](linear-static.md#when-the-model-is-big-the-static-ladder): an iterative solver,
fine at the peak and coarse away, small far fillets left out, simpler elements, half a symmetric part.
