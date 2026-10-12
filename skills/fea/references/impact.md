# Drop impact (`impact`, lite)

It answers "what really happens when it hits the floor": dropped from a height, the part is followed
from touching a rigid floor (the stress wave, the floor's push, peak g and stress, and permanent
strain when plasticity is given). The deeper check after a [`drop`](drop.md) estimate.

**Limits (in every result): "Rigid floor; linear tets; elastic unless plasticity is given."**

## When to use it, and when the drop estimate is enough

- Use `drop` first: quick, and enough when you can say how far the part stops (a rubber foot, a
  padded case) or how long the impact lasts; its answer is an assumed equivalent steady load.
- Use `impact` when nobody can say the stopping distance (a hard part onto a hard floor), when the
  stress wave matters (a long part landing on its end, a thin wall far from the landing point), or
  for the peak g, how long it stays on the floor or whether it is left bent for good.
- Not for: a soft or yielding floor (the floor is rigid), a part hitting another part, cracks or
  fracture, rubber (no hyperelastic material here), or how far it bounces after the first impact
  (the run follows a short window after landing).

## The study

Keys beyond the [shared ones](study-file.md#shared-by-every-analysis):

```json
{"analysis": "impact", "material": "abs", "window_ms": "auto", "mesh": {"size_mm": 3, "order": 1},
 "drop": {"height_mm": 1000, "direction": [0, 0, -1], "floor": "rigid", "friction": 0.0},
 "plasticity": {"tangent_MPa": 200},
 "view": {"checks": [{"kind": "stress"}, {"kind": "acceleration", "limit_g": 2000},
                     {"kind": "plastic_strain", "limit_percent": 1}]}}
```

- `drop.height_mm` (required): how far it falls. The impact speed is v = √(2 g h): 1 m is 4.43 m/s.
- `drop.direction`: `[x, y, z]` from the part toward the floor, in its own axes; default `[0, 0, -1]`
  (straight down); `[1, 0, 0]` lands it on its +X side, `[1, 1, -1]` on a corner.
- `drop.floor`: `"rigid"`, the only floor modelled (and the default). `drop.friction`: the Coulomb
  coefficient between the part and the floor, default 0.
- `window_ms`: how long after first contact to follow it, or `"auto"` (the default): three times
  the contact-time estimate below. That is a guess (a corner landing on a soft part stays down
  several times longer), so an automatic window still pressing at its end goes on until the first
  impact is over, plus 20 %, up to four times itself (`extended_to_pulse_end` in the summary). Still
  pressing at the end of the run is an info finding `still_in_contact`: give a longer window.
- `plasticity` (optional), `{"tangent_MPa": 200}`: bilinear J2, E up to the yield, then the tangent
  slope (0 is perfectly plastic). It needs the material's yield; `tangent_MPa` may be left out when
  the material object gives one. Without it the part stays elastic and the stress can pass yield.
- No fixtures or loads: it flies free until it lands, naming no faces; those that met the floor are
  found and written back (`extras.study.drop.onto`). `mesh.order`: 1 (linear tets), the only one.
- Checks: `stress` (the default): peak von Mises over the run against yield, with the study's
  margin. `acceleration`, `limit_g`, labelled "Peak g": peak rigid-body g (the floor's push over the
  mass), or with `faces` their mean deceleration (a board or battery mounted there).
  `plastic_strain`, `limit_percent`: permanent strain at the end; refused, saying so, without
  `plasticity`.

The contact-time estimate is the elastic one: a stress wave runs from the landing point to the far
end and back while the part is pressed on the floor, t_c = 2 L / c, with L the part's length along
the fall and c = √(E/ρ) (5.05 km/s for steel and aluminium, 1.45 km/s for ABS). The window is 3 t_c.

## What it writes

- A time series (`extras.series`, kind `time`): up to 24 frames from first contact, evenly spaced,
  plus the peak stress's moment (opened on). Each has the von Mises stress and the displacement
  relative to the centre of mass (the squash and ring, not the fall). Envelopes: `von_mises_peak`
  (the most each point reached), `displacement_peak`; with plasticity, `plastic_strain` (%, at the
  end). Sidecar curves: `contact_force_N` (the floor's push against time), `rigid_body_g` (over the
  mass, in g).
- Summary: `contact_ms` (when the first impact ended), `contact_time_estimate_ms`, `window_ms`,
  `end_ms`, `step_us`, `steps`, `peak_g` and `peak_g_at_ms`, `peak_force_N`, `max_von_mises_MPa`
  and when and where, `safety_factor`, `max_displacement_mm`, `rebound_speed_m_s` (the centre of
  mass's speed away from the floor at the end), `mass_added_percent`, `subcycle`, `filter_us`, the
  `unfiltered` peaks, and with plasticity `max_plastic_strain_percent`.
- Stress and push are filtered at the mesh's resolution (first-order, four element transits,
  `filter_us`), like a crash test's channel filter: the first node's single-step bump and a coarse
  mesh's ringing behind a wave front are not the part's. Quote them, not `summary.unfiltered`.
- The fall, for the viewer (`extras.analysis.fall`): `height_mm`, unit `direction`, `speed_m_s`,
  `floor` (`point_mm`, where it first meets it, `normal` facing up), `impact_ms` (the contact time,
  null while still on the floor), `end_ms`, `rebound_m_s` (as the summary's), `centre_mm` (the
  centre of mass's travel from first contact at each frame, mm).
- The viewer moves it as kinematics moves a STEP, turned so the fall points down onto a
  see-through floor. The **Time** row scrubs the whole motion (released, falling, the solved frames
  on the floor, rebound, at rest), its value the real time from landing ("−0.45 s", "10.1 µs");
  presets Released, Contact, Peak, At rest; opens on the peak. Preview's playbar plays it as
  **Drop**: the fall at real time, the impact slowed by a round factor ("slowed 2,000×") to a couple
  of seconds, the hop and the rest (reduced motion: only the impact). Then Field, Deformation, a
  "Dropped" row ("1 m drop onto a rigid floor"), the drop arrow; takeaway starts "Rigid floor · ".

## How to judge the answer (hand checks)

- **A bar dropped end-on** (the benchmark): it stays on the floor for 2 L / c, the stress behind the
  wave is ρ c v and the floor pushes with ρ c v A, so the peak is c v / L in g after dividing by
  9806.65 mm/s². A steel bar 100 mm long dropped 1 m: 39.6 µs, 175 MPa, about 22,800 g; cadgen's
  test gets these within 2 %, 3 % and 1 % (Poisson's ratio 0, so the 3D bar is the theory's 1D bar;
  with steel's 0.3 its lateral inertia makes it ring about 15 % above ρ c v behind the wave front).
- **Any compact part**: peak stress near the landing point of order ρ c v whatever its shape (steel
  at 1 m: 175 MPa; aluminium 60 MPa; ABS about 7 MPa), concentrated by corners and thin walls; far
  above that is a stress concentration or a too-coarse mesh.
- **Peak g**: of order c v / L: thousands of g for a short, stiff part on a hard floor (why the drop
  estimate's assumed stopping distance matters so much). **Perfectly plastic metal**: the stress
  stops at yield, the floor pushes with yield × landing area, plastic strain sits at the landing end.

## Limits (say them)

- **Rigid floor**: a real floor gives a little (wood, a carpet, a padded case lengthen the impact
  and lower the g a lot). Use `drop` with a stopping distance for a soft landing.
- **Linear tets**: constant-strain, they read peak bending stress low; mesh finely near stress
  concentrations, several elements through the thinnest wall at least.
- **Elastic unless plasticity is given**: without `plasticity`, a stress past yield is reported but
  the material never yields; with it, J2 bilinear only (no rate effects, no damage, no fracture).
- The floor is a penalty spring (10 E A / h per surface node, damped at 20 % of critical so a
  landed node stays down); a little bulk viscosity damps ringing behind a wave front; gravity acts
  throughout. No air, sound or heat. One short window from first contact, not a bounce sequence.

## Adapted to fit (ladder steps)

Cost is element count times steps, and the step is set by the smallest element (dt = 0.9 · l_min /
c, a few tenths of a microsecond for millimetre elements in metal), so a small fillet or a sliver
can make a run slow. The [ladder](study-file.md#shared-by-every-analysis), in order:

1. `iterative`: the force loop is already matrix-free (no stiffness matrix is stored); never said.
2. `window`: stop once the first impact is over (floor stopped pushing, part stopped or turned back)
   plus 20 % of it. Accuracy: the first impact and its peak kept; no second bounce or later ringing.
3. `subcycling`: the smallest-stable-step elements and their neighbours step several times per step
   of the rest. Accuracy: each region within its own stability limit, the larger step's motion
   interpolated where they meet; if mass scaling (or a remesh) evens the steps after, it says it
   was not needed after all.
4. `mass_scaling`: mass added to the smallest elements so their step grows, at most 5 % of the
   part's, always reported with the share and its effect on peak g (measured by rerunning the first
   10 % unscaled when that costs no more than the run itself, else stated as up to the share). Quote
   both ("Added 5.0% to the part's mass ... peak g moved 0.5% against an unscaled rerun").
5. `local_refine` (around the peak), 6. `defeature` (far from the landing).

A result that took a step with an accuracy cost says "adapted". A run past its time limit (twice
`fit.seconds`, at least 30 s) stops where it got to and says so ([`fit`](study-file.md#fit)).
