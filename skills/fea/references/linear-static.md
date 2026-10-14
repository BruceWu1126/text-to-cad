# Linear static checklist

What the solver assumes, how to set a study up so the answer means something, and how to tell a
good answer from a bad one. The keys are in [study-file.md](study-file.md).

## What the model is

- Small displacements, small strains: results scale linearly with the load. A part that deflects by
  more than a few percent of its own size, buckles, or contacts something else is outside the model.
- Linear elastic material below yield (isotropic, or orthotropic where the material gives its
  directions). The safety factor tells you whether that assumption held; below 1 the real part
  yields and the stresses reported are not what it sees.
- Fixtures are perfectly rigid. A `fixed` face is held still and bonded: a real bolt, weld or clamp
  is softer and spreads load, so a fixed face is the stiffest possible support. A `roller` face
  slides freely in its own plane and is held only along its normal (on a curved face, its normal
  at each point): a frictionless support.

## Fixed or roller

- `fixed` where the part is bolted, welded or clamped. It also stops the face shrinking sideways as
  the part is squeezed (Poisson's effect), so the stress right at it reads high: read the peak a
  little away from it. Fixing a whole hole wall is stiffer than a real screw with a washer, so
  peaks at fixed holes read high.
- `roller` where the part rests on a smooth support it can slide on, and on a symmetry plane when
  you model half a part yourself (the cut face moves only in its plane). A block on rollers on
  three faces that meet at a corner, pulled on the opposite face, is in pure uniaxial stress:
  σ = F/A everywhere and it narrows by ν times its stretch, the textbook answer, which a fixed face
  would spoil at the clamp.
- Rollers alone must stop the part sliding and turning every way: three faces whose normals are
  not all in one plane do it; one flat face does not (the part could slide along it), and the run
  says the part is not held.
- Loads are static and do not follow the deformation. A `gravity` or `acceleration` load is a
  steady body load (the part's weight, or a constant g-load), not a moving one: no vibration, no
  impact, no fatigue, no thermal strain, no preload.
- An assembly's parts are bonded where they touch (within 0.1 mm): the glued faces carry load as
  if welded, with no slip, no clearance, no preload and no bolt stiffness. A bonded joint is
  stiffer than a real bolted one and exaggerates the stress at its edge. Under `static`, a part
  resting on another without being bonded carries nothing: parts that press and slide are the
  `contact` analysis, and bolts the `bolt` analysis.

## Choosing faces

1. Prefer references the user selected in the Viewer: they arrive as `part.step#o1.f17` and are
   exact.
2. Otherwise run `cadgen fea faces part.step` and match by the hint (`plane, normal -Z, largest`),
   the centre, and the area. "The base", "the mounting face" and "the bottom" usually mean the
   largest planar face with a downward normal; "the top of the upright" is a small planar face at
   the highest Z. A hole is a `cylinder`.
3. When two faces match a description equally, ask, quoting both selectors and where they are. Do
   not guess between a face and its mirror image.
4. Restate what you chose before solving: "fixing #o1.f17 (3943 mm², bottom), 500 N in -Y on
   #o1.f22 (640 mm², top of the back plate)". A wrong face is cheaper to catch here than after a
   run.

## Setting the load

- A force is the total over its faces, spread uniformly. 500 N on a 640 mm² face is 0.78 MPa of
  traction. A force on a hole's cylindrical face approximates a pin or bolt load.
- A pressure is normal to the face and positive pushing in. Convert bar to MPa (1 bar = 0.1 MPa)
  and psi to MPa (1 psi = 0.006895 MPa) before writing it down.
- Weight of a held object: mass in kg times 9.81 gives newtons, as a `force` on the face it rests
  on. The part's own weight is a `gravity` load (below).
- A moment: two equal and opposite forces on two faces a known distance apart.

## Body loads: gravity and acceleration

Two loads act on the whole volume instead of on faces. Neither has `faces`; each takes `vector_g`,
in g (1 g = 9.81 m/s²).

```json
"loads": [
  {"faces": ["#o1.f2"], "type": "force", "vector_N": [0, 0, -200]},
  {"type": "gravity", "vector_g": [0, 0, -1]},
  {"type": "acceleration", "vector_g": [5, 0, 0]}
]
```

- `gravity`: the part's own weight, pulling along `vector_g`. `[0, 0, -1]` is Earth's gravity with
  +Z up. Add it when the weight matters against the other loads: a long arm, a heavy casting, a
  large plate held at one edge. For a small bracket carrying a large force it changes little;
  leave it out unless asked.
- `acceleration`: the part itself is accelerated by `vector_g` (a vehicle braking, a robot arm
  swinging, a part on a spinning arm at a known g). It feels an inertial load the opposite way:
  speeding up at 5 g in +X loads every bit of the part toward -X, like a passenger pressed back
  into a seat. This is a steady g-load, not a shake or an impact.
- Both need a density greater than zero (steel 7.85e-9, aluminium 2.7e-9 t/mm³;
  [`loads`](study-file.md#loads)). In an assembly they act on every part, each with its own density.
- The sidecar's `applied_force_N` includes them (mass times acceleration), so the reactions still
  balance it: a 2 kg part under `gravity` alone shows about 19.6 N applied and 19.6 N of reaction.
- A drop, as an estimate, is the [`drop`](drop.md) analysis: the faces that hit the floor fixed and
  an `acceleration` of G g pointing away from the floor, G = drop height / stopping distance (a
  1 m drop that stops in 2 mm is 500 g). Report it as an estimate of an equivalent steady load,
  never as an impact simulation, and say the stopping distance you assumed.
- The `load_scale` control scales the body loads with the rest, because the answer is linear in
  every load.

## Mesh

- First run at the default size (a fortieth of the bounding diagonal). `mesh.size_mm` is the
  largest element; small features (thin walls, fillets, chamfers, small holes) make the mesher go
  finer there by itself, with elements under about their radius or thickness, so they, not only
  the bounding box, can set the element count.
- The run reports both the nodal and the Gauss-point von Mises peak. When the Gauss-point value is
  more than 50 % above the nodal one, the peak is not resolved; when that matters the run lists a
  `peak_concentration` finding. When the safety factor is under 3 the run solves again by itself at
  half the element size (and half the size at fillets and holes too), capped to keep the solve under
  the degrees-of-freedom budget (so a part near the limit takes minutes and may get less than half);
  the finer result is the one written and reported, the sidecar's `refined` block records both sizes
  and peaks, and `mesh_not_converged` is listed when the peak moved more than 10 %.
- To check convergence yourself, solve twice and compare the peaks; how depends on where the peak
  is. Away from small features, halve `mesh.size_mm` (`--mesh-size` on the command line). At a
  fillet, hole or chamfer, halving it changes little: the elements there are already sized by the
  feature, not by `mesh.size_mm` (the run's slow-solve warning says so when small features set the
  mesh). Set `mesh.size_mm` to about a third of that feature's radius instead, then halve that;
  this refines the whole part, so watch the degrees of freedom.
- A peak that moves less than about 10 % between the two runs is converged enough for a first
  answer. A peak that keeps growing sits on a singularity: a clamped edge, a sharp inside corner, a
  point load. Report the value a short distance away from it, say why, and suggest a fillet or a
  softer fixture if the location is real.
- Element count grows as the cube of the refinement. Above about 400 000 degrees of freedom the
  solve is slow and the run says so. A model too big for the machine is not refused: the run
  adapts it to fit (below).

## When the model is big: the static ladder

The steps, their order and their costs are the table in [`fit`](study-file.md#fit): `iterative`,
`local_refine`, `defeature`, `linear_elements` (with `idealise`), `symmetry`. Each note says what
it cost: `local_refine` "the peak is still meshed at 0.8 mm; peak stress moved 2.1 % between
passes", `defeature` "Left out 6 small fillets far from the load to mesh it". Report every step in
plain words: "To fit this machine, it meshed coarsely away from the hole; the peak there is still
meshed finely, and it moved 2 % between the coarse and fine passes." Never report an adapted result
as if no step was taken.

## Which solver runs: CalculiX or the built-in one

- A static study is solved by CalculiX (`ccx`), which is faster, and otherwise by cadgen's built-in
  solver. Nothing else changes: the same mesh, loads, fixtures, checks and outputs, and the same
  answer. On a straight-edged mesh the two agree to CalculiX's seven printed digits; at a curved
  hole or fillet they differ slightly, from how each integrates a curved element (CalculiX at 4
  points, the built-in solver at 11): within 0.2 % on the max von Mises, the safety factor and the
  displacement, far less than a finer mesh moves them. The one number that can differ more is the
  Gauss-point peak at a sharp inside corner (25 % on a clevis's slot corner): a spike the mesh
  cannot resolve with either solver. Read it as a spike, never quote it as the part's stress.
- Nothing needs installing: the first study CalculiX suits downloads it once into cadgen's cache (9
  to 21 MB by platform; the run prints `cadgen: downloading the CalculiX solver`). A `ccx` already
  on the PATH is used instead; `CADGEN_CCX=/path/to/ccx` names one, and `CADGEN_CCX=0` turns it
  off. With no network, or on a platform cadgen has no build for, the built-in solver runs, and a
  failed download says why.
- The built-in solver keeps what CalculiX would not solve the same way: an orthotropic or layered
  material, a thermal strain, a roller on a sloped or curved face, linear elements, and models over
  150 000 degrees of freedom (CalculiX's direct solver needs far more memory there).
- The result says which ran: `mesh.solver` in the sidecar (`calculix 2.23 (C3D10)`, or the built-in
  `superlu` or `amg+cg (…)`), also at the end of the first line the command prints. If CalculiX
  fails, the built-in solver answers instead and a warning says why.

## Judging the answer

Before trusting a run, estimate the answer by hand and compare:

- A cantilever of length L with a tip load F: tip deflection F L³ / (3 E I), root bending stress
  F L (h/2) / I, with I = b h³ / 12 for a rectangle.
- A plate with a hole in tension: about three times the nominal stress at the hole's edge.
- A thin ring or cylinder under pressure p: hoop stress p r / t.
- A body load: the reaction equals mass times acceleration, density times volume times 9806.65 (g
  in mm/s²) times `vector_g`, in N when density is in t/mm³ and volume in mm³. A cantilever of
  length L under its own weight w per unit length: tip deflection w L⁴ / (8 E I), root bending
  stress w L² (h/2) / (2 I).

If the model is within roughly 20 % of the estimate, it is doing what you asked. If it is off by a
factor, the load, the fixture or the units are wrong; check the reactions in the sidecar first
(they must equal minus the applied force), then the face choice, then the material.
