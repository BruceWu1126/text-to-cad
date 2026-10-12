# Contact (`contact`, lite)

It answers "what happens where parts press or slide on each other instead of being glued": how hard
and where they press, whether a joint opens, whether friction holds, parts on a rigid floor.
**Lite; its limits (in every result):** small sliding, node-to-surface, static, linear elastic parts.

## When to use it, and when bonded or free is enough

Every other analysis glues an assembly's touching pairs (`bonded`, the default: welded, glued or
clamped so hard they never part or slide, passing tension as readily as compression) or leaves a
pair apart (`free`: they do not touch under the load, or you want one left out). Use `contact` when
the parts only press (compression, never tension, sliding unless friction holds): a pin or shaft
pressing on a plate or bore, a block or foot resting on another part, a part on a floor or bench
(a rigid plane), a joint that may open on one side under a moment, a clamp that holds by friction,
"how hard do these press" (bearing or Hertz contact pressure), or "does this lift off".

Not for: impact or anything fast (`impact`), bolts with pretension (`bolt`, [bolt.md](bolt.md)),
parts that slide far along each other (more than a fraction of an element), press fits (an overlap
counts as touching, not as an interference to be pushed apart), rubber or metal past yield
(`nonlinear`: its parts stay bonded).

## The study

Keys beyond the [shared ones](study-file.md#shared-by-every-analysis):

- `connections`: the pairs that press, `{"between": [A, B], "type": "contact", "friction": 0.2}`.
  `friction` is Coulomb's coefficient, 0 (frictionless, the default) to 2: about 0.1 oiled steel,
  0.2 dry steel on steel, 0.3 to 0.5 aluminium or plastics on metal. Other touching pairs stay
  bonded unless `free`; every other analysis refuses `contact` with "not yet supported". The parts
  need not touch at first: the gap of a pin a hair off its hole, or a sphere touching a plate at a
  point, closes first. A pair joined through other bonded parts is refused (the glue would join it).
- `rigid_planes`: rigid floors, `[{"point_mm": [0, 0, 0], "normal": [0, 0, 1], "parts": ["plate"],
  "friction": 0.3}]`, holding a part as a fixture would but only against pressing. `normal` points
  to the parts (turned round when they are on the other side); `parts` defaults to every part,
  `friction` to 0.
- `loads` (body loads included) are required; `fixtures` are optional when a rigid plane holds the
  parts. A part may rest on a contact or a plane alone; one held by nothing (no fixture, rigid plane
  or contact with a held part) is refused, naming it. To model a quarter or half of a symmetric
  contact yourself, put `roller` fixtures ([study-file.md](study-file.md#fixtures)) on the cut planes
  of every part, so each slides in the plane but never through it (a ball pressed on a block, cut
  along x = 0 and y = 0: four roller faces, the load a quarter of the whole).
- `steps`: equal load steps to start with, 1 to 200, default 5. A step that does not converge, or
  whose contact forces do not settle (the balancing re-solve finds no equilibrium), is halved and
  retried (up to six times); one still unsettled at the smallest step is kept and said plainly:
  "Contact did not settle at 80% of the load: the forces here do not balance, so this result is not
  reliable" (an error finding, `extras.analysis.warnings` and `extras.analysis.unsettled`, that
  frame's label "80 % load · did not settle", every check failing). Report it as not reliable,
  never its numbers: try more `steps`, a finer mesh where the parts touch, or friction 0.
- Time limit: once the path has spent the time target (`fit.seconds`, 600 s by default) without
  getting through a step, a step needing a cut is not cut again: the path stops there ("could not be
  followed past 40%", an out-of-time warning, `out_of_time` in the summary), never an hour of
  cutting. A path still getting through its steps is never stopped; a bolted joint's load path gets
  the whole target after its preload.
- Friction's stick spring is a hundredth of the normal penalty, so stick and slip settle in a few
  Newton iterations; faces modelled flush (apart by round-off) touch from the first iteration, so a
  part held only by contact or a bolt has something under it at once. Pushed past what friction
  holds, a part slides away (error `slides_away`).
- Checks, judged at the last load solved: `stress` (the default with no `view.checks`) and
  `displacement` as static, and `contact_pressure`: `limit_MPa` (for bearing, often 1.5 times the
  yield for a static load; for rolling or Hertz contact, the material's allowable), optional
  `faces`; close past 0.9 of it, row line "Peak 380 MPa, limit 400 MPa".
- No load control (`load_scale` is not a drive): contact opens, closes and slides, so the response
  is not proportional to the load. Drives: `frame` (the load-step scrubber), `field`, `deformation`,
  `threshold`; with no view the scrubber, the field and the deformation.

A pin pressed into a plate that rests on a bench, dry steel:

```json
{"analysis": "contact", "material": "steel", "steps": 5,
 "connections": [{"between": ["pin", "plate"], "type": "contact", "friction": 0.2}],
 "rigid_planes": [{"point_mm": [0, 0, 0], "normal": [0, 0, 1], "parts": ["plate"]}],
 "loads": [{"faces": ["#o1.1.f3"], "type": "force", "vector_N": [0, 0, -2000]}],
 "view": {"checks": [{"kind": "stress"}, {"kind": "contact_pressure", "limit_MPa": 400}]}}
```

## What comes out

- Load steps (`series.kind` "time", unit "%"), frames labelled "60 % load", at most 24, opened on
  the last, scrubbed (Load step) and played (Play): `von_mises` (MPa), `displacement` and
  `contact_pressure` (MPa, on the faces in contact, else 0). Study names each pair ("pin presses on
  plate, friction 0.2") with what it did under the load, and each rigid plane under "Rigid floor".
- Summary: `status` ("Carries the full load", or "Stops at about 70 % of the load" when Newton finds
  no equilibrium past it: each check judged on its own number at the last load carried, with
  `collapsed_at_percent`, the stop leading the viewer's takeaway); `max_von_mises_MPa`,
  `max_displacement_mm`, `max_contact_pressure_MPa`, each with where; `contacts`, per pair and
  plane: `touching`, `force_N`, `area_mm2` (pressing), `peak_MPa`, `peak_at`, `friction_N` (sideways
  force friction carries), `slipping_share` (with friction), `max_slide_mm`; the steps, Newton
  iterations, `contact_updates`. Curves: largest displacement and total contact force against load %.
- Findings: `contact_presses` (info, each pair: "pin presses on plate: 2000 N over 12 mm², peak
  contact pressure 380 MPa"), `peak_contact_pressure` (info, where), `contact_separates` (warning:
  "they separate under this load and no longer touch"), `contact_slides` or `contact_sticks` (info,
  with friction; or "slides freely" without); for a part held only by contacts `comes_loose` (error:
  lifts off all of them) and `slides_away` (error: friction cannot hold it; its displacement is
  then meaningless); `contact_stops` (error: no equilibrium past a load), `load_steps_cut` (info).

## Judging the answer (hand checks)

- Two flat blocks pressed by F over area A: pressure F/A everywhere; within 1 % on a 3 mm mesh of
  10 mm blocks. Pulled apart: no contact pressure, and the part underneath takes no load (a contact
  never pulls); a part held only by the contact comes loose, and says so.
- A sphere of radius R on a flat (Hertz), both elastic: the contact radius a = (3 F R / 4 E*)^(1/3)
  and the peak pressure p0 = 3 F / (2 π a²), with 1/E* = (1 − ν1²)/E1 + (1 − ν2²)/E2 (a rigid
  flat: 1/E* = (1 − ν²)/E). Steel on steel, R = 10 mm, F = 20 kN: a = 1.11 mm, p0 = 7.8 GPa; solved a
  within 2 % and p0 within 7 % with elements of about a/4 where they touch (0.3 mm here), the peak
  closing on Hertz as they shrink. Point and line contacts need that fine a mesh there: set
  `mesh.size_mm` small or let the ladder's `local_refine` refine at the peak.
- Friction: pushed sideways by T, pressed by N, a part sticks when T < μ N (friction carries all of
  T), slides when T > μ N (it carries μ N exactly). Micro-slip near the edges of a part pressed on a
  stiffer one is normal, where the sideways traction of its own bulge passes μ × local pressure.
- Sanity: frictionless and bonded agree when the joint only presses and nothing slides; a contact
  pair's force equals the load it carries (the reaction at the fixtures).

## Limits

- Small sliding: each surface node is paired once, before loading, with the surface it faces;
  parts may slide only a fraction of an element along each other (further, or turning far, is
  beyond it). Node-to-surface: the pressure is resolved to the mesh; a flat contact needs a few
  elements across it, a point or line contact (a ball, a roller, a knife edge) elements of about a
  quarter of the contact width where it touches.
- Static: no impact, inertia or rate effects. Coulomb friction, one coefficient at rest and sliding.
  The parts stay linear elastic: a pressure past yield is reported, not followed into plasticity.
  An initial overlap within the contact tolerance counts as just touching (no press fit).
- A part held only by contacts is kept from drifting where they do not hold it (sideways with no
  friction) by a very weak spring, a hundred-millionth of its stiffness; if that carries the load
  (the part lifts off or slides away), the result says so and the displacement means nothing.

## When the model is big

Ladder ([shared rungs](study-file.md#shared-by-every-analysis); as `nonlinear`'s), in order:

- `iterative`: Newton-Krylov, each Newton step solved by GMRES with a multigrid preconditioner.
- `adaptive_steps`: the load steps grow (up to 4 times the starting step) while the contacts settle
  in a few Newton iterations, still cut where they do not. Its note says friction's slip is followed
  in larger steps; the final contact is solved to the same tolerance.
- `local_refine`: fine where the coarse pass peaked in contact pressure (how to resolve a point
  contact on a big model); `defeature`: far from loads and fixtures; `symmetry` (one part on a rigid
  plane only): when part, fixtures, loads, checks and every rigid plane are symmetric.
