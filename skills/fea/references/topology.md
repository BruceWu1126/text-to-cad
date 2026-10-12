# Lighten it (`topology`, lite)

From the part as a block of material (the design space), held and loaded as a static study, it works
out which material carries the load and which can go. **Lite; its limits (in every result):** linear
elastic and one material, a fixed design mesh of linear tetrahedra (members thinner than about three
elements cannot form, and the surface is a smoothed staircase), a verification solve on the kept
design elements whose stress is a guide, and loads that stay where they are (no self-weight). The
answer is a concept shape to rebuild in CAD, not a finished part: say so whenever you quote it.

## When to use it

- "Where can I take material out", "what shape should this bracket be", "make it lighter but keep
  it stiff": a bracket, arm, mount or plate with known holds and loads and a block it must fit in.
  "The stiffest part from 30 % of the material" (`objective: "stiffest"`, the default); "the least
  material that still holds" (`"lightest"`, with a stress check, or a displacement check for "must
  not move more than 0.2 mm").
- Not for: a part already shaped as it must be (run `static`), a part whose weight is most of its
  load, an assembly (one part at a time with `--occurrence`), or buckling, vibration or fatigue of
  the new shape (rebuild it, then run those).

## The study

Keys beyond the [shared ones](study-file.md#shared-by-every-analysis) (`loads`: forces and
pressures on faces only, no `gravity` or `acceleration`):

| key | meaning | default |
| --- | --- | --- |
| `objective` | `"stiffest"`: the stiffest design from `volume_fraction` of the material; `"lightest"`: the least material whose design passes the study's stress check (and displacement checks, where it names them) | `"stiffest"` |
| `volume_fraction` | the share of the part's volume to keep, 0.01 to 1 (0.3 keeps 30 %); `lightest` starts its search there | 0.3 (`lightest`: 0.5) |
| `keep` | faces whose nearby material stays solid, beyond the ones that always do: the fixtures, the loaded faces and whole cylindrical holes (bolt holes) | none |
| `min_member_mm` | the thinnest bar or wall the design may make (the density filter's size); never under 2.5 design elements | 3 design elements |
| `penalty` | SIMP's exponent, reached by continuation from 1 | 3 |
| `symmetry` | planes the design must be symmetric about: `"x"` (through the part's middle) or `{"axis": "x", "at_mm": 20}` | none |
| `extrusion` | a direction the design must be prismatic along (cut from plate, extruded): `"z"` or `{"direction": [0, 0, 1]}` | none |
| `load_cases` | instead of `loads`: several load cases, `[{"name": "lift", "loads": [...]}, ...]`; the design is stiffest for their sum | one case |
| `iterations` | the most optimisation iterations, 10 to 2000 | 150 |

`mesh.size_mm` is the design element size, `mesh.order` 1 (linear tets; order 2 is refused). A
finer mesh makes thinner members and takes longer: about 1000 to 5000 elements is a quick concept,
20 000 to 100 000 a detailed one.

Checks: `mass_saved` (`min_percent`, the least share of the mass the design must save, 25 by
default; titles "Barely lighter", "Close", "Much lighter"), and `stress` and `displacement` judged
on the verified design. With none named: `mass_saved` and `stress`. For example:

```json
{"analysis": "topology", "material": "aluminum-6061-t6",
 "fixtures": [{"faces": ["#o1.f3", "#o1.f4"]}],
 "loads": [{"faces": ["#o1.f9"], "type": "force", "vector_N": [0, 0, -800]}], "mesh": {"size_mm": 2},
 "volume_fraction": 0.3, "keep": ["#o1.f12"], "min_member_mm": 6, "symmetry": ["x"],
 "view": {"checks": [{"kind": "mass_saved", "min_percent": 50}, {"kind": "stress"}]}}
```

The lightest version of the same bracket that must not move more than 0.1 mm:

```json
{"analysis": "topology", "objective": "lightest", "material": "aluminum-6061-t6",
 "fixtures": [{"faces": ["#o1.f3", "#o1.f4"]}],
 "loads": [{"faces": ["#o1.f9"], "type": "force", "vector_N": [0, 0, -800]}],
 "view": {"checks": [{"kind": "displacement", "limit_mm": 0.1}, {"kind": "stress"}]}}
```

## What comes out

- The GLB coloured by **Material kept** (the density: 1 stays, 0 goes), one frame per stage of the
  run ("Iteration 12" ... "Final design", at most 24). The viewer opens on the final design with
  "Show above 0.5", what goes drawn grey; its routine "Watch it form" plays the design forming.
  Stress and Displacement are the verified design's.
- `<name>.fea.design.stl` beside the GLB (sidecar `files.design_stl`): the surface at density 0.5,
  closed by the part's own faces where solid, Taubin-smoothed so it keeps its volume. Open it in
  the CAD Viewer next to the original part.
- Summary: `mass_saved_percent`, `design_volume_fraction` (the thresholded design that is built),
  `volume_fraction` (the optimiser's, on target within about 1 %), `compliance_Nmm` and
  `solid_compliance_Nmm` (the whole part solid, the baseline), `compliance_ratio` (how many times as
  flexible as the solid part the verified design is: 1.3 to 2.5 is usual at 30 to 50 %), the
  verified design's `max_von_mises_MPa`, `max_displacement_mm` and `safety_factor`, `kept_solid`
  (each face kept and why: fixture, load, bolt hole, kept), `iterations`, `converged`,
  `grey_percent` (still between solid and void; under 10 % is crisp) and, for `lightest`,
  `lightest_rounds` (each volume tried and whether it passed). Curves: compliance, volume and grey
  share against iteration.

## How to read it and rebuild it in CAD

1. See which load paths the design and the STL keep (bars from load to fixtures, a web, a rim
   round a hole).
2. Rebuild it as clean CAD in the original part's source, not by converting the STL: bars and webs
   sketched on its outline, as thick as the STL shows (at least `min_member_mm`), joints filleted,
   every kept face as it was. A plate part is usually one outline extruded through (`extrusion`
   makes that exact).
3. Run `static` on it with the same fixtures and loads; its stress is the number to trust (the
   verification stress here, on a staircase of linear tets, reads high at the staircase's corners
   and low in bending).

## Judging the answer (hand checks)

- Volume: `volume_fraction` within 1 % of the target; `mass_saved_percent` is 100 minus the kept
  share (a little more when loose pieces were left out: a warning says so).
- Stiffness: more flexible than the solid part (`compliance_ratio` > 1) but much stiffer than the
  same mass spread evenly (SIMP at penalty 3: a uniform 50 % part is 8× as flexible as solid; a good
  design at 50 % is 1.3 to 2×).
- Shape: a beam held at both ends and pushed in the middle (the MBB beam) gives an arch with
  diagonal struts; a cantilever a truss of two chords and diagonals. A symmetric problem gives a
  symmetric design (name `symmetry` to make it exact).
- Benchmarks (thin 2 mm steel slabs, 2 mm and 1.5 mm design elements): the MBB beam at 50 % keeps
  50.0 % and is 1.35× as flexible as the solid slab; the cantilever at 40 % is 2.1×. On a structured
  half-MBB mesh, halving the element size moves the optimum's compliance under 5 %.

## Limits

- One part, one isotropic or orthotropic material, linear elastic, small displacement; the
  stiffest for the loads given, not checked for buckling, fatigue or vibration (run those on the
  rebuilt part). Loads stay where they are: no self-weight, no pressure following the new surface.
- No draw-direction (a moulded or cast part's pull) or overhang (printing) constraint; `extrusion`
  and `symmetry` only. Use `extrusion` for plate-cut parts; rebuild a moulded part with draft by hand.

## When the model is big

Ladder ([shared rungs](study-file.md#shared-by-every-analysis)), in order:

1. `iterative`: AMG-preconditioned CG, warm-started from the last iteration's displacement, the
   multigrid hierarchy reused while it still converges quickly.
2. `symmetry`: also needs kept faces symmetric; sliding supports on the cut, design mirrored back.
3. `coarse_design`: the coarsest design mesh that fits from the requested size, with the member size
   it can resolve ("Used a coarser design mesh (5.3 mm elements, from 2 mm) to fit: it resolves
   members down to about 16 mm"); report it, since thinner members cannot form.

`local_refine` does not apply (the design mesh is fixed for the run). For finer detail, run a coarse
study, then a finer one on the part cut down to the region that matters, or a larger `fit.seconds`.
