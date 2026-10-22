# Measure audit (recovered from session 931f4bad, "3D grease pencil demo ideas")

Worktree: .claude/worktrees/3d-grease-pencil-demo-08523f, branch claude/3d-grease-pencil-demo-08523f.
All uncommitted, on top of the ~66 uncommitted Annotate/line-style/Pencil+Shapes files. Nothing pushed.

## What Measure does now (STEP)
- A press takes an entity, whole: vertex (an edge end within 10px), else edge, else face. Hover uses
  the same tolerance as the press, so what lights is what a press takes. Shift, or the filter with
  nothing ticked, gives a free point.
- Readings come from the pair (packages/core/src/lib/viewer/measureEntities.js, new): vertex-vertex
  distance + deltas; parallel faces/edges or vertex-face/edge ⟂; circle/cylinder vs anything from
  its centre/axis (C-C for two holes, ⟂ hole to face/edge); angles ∠ (acute); else the min distance.
  A zero gap (edge in its face, vertex on its face) falls back to the two points' distance.
- A press on the held entity, or on empty space, releases the first pick.
- Assemblies: a press on a part whose topology isn't loaded loads it (Select's pattern) and re-presses
  there (StepSurface handleMeasureWantsTopology + useStepPicking measureAtRef). Before this every
  press on qdd_actuator was a free point (this is the same on main).
- Meshes (GLB/3MF/robot): a press takes the point on the surface, with no triangle corner or side
  snapping, and the mesh Measure filter menu is removed.

## Verified
- Headless plate.step: ∠ 90°, C-C 60 / 116.62, hole centre to side ⟂ 10, hole to edge ⟂ 32, edges ⟂ 80,
  toggle/empty-press release. qdd_actuator: C-C 28.00 between flange holes (was 28.10 free points).
- core 1367 pass; UI/web suites: see the session's report.

## Not done / follow-ups
- scripts/test/check-kit-boundaries.mjs fails on ~24 lines from the earlier Annotate work ("topology",
  "explode" named in kit files: RendererShell inkTopology, AnnotationOverlay topologyAt, annotation3d,
  SubToolbar comment). CI's JS job will fail on it.
- Before a part's first press in an assembly, hover over it still says "Point".
