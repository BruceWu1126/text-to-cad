# BRepMesh as-is: display-mesh faults on our models

2026-10-20. Engine at 8b896c3f4 (`cadgen.engine.document.Loaded` reads every input, `cadgen.engine.mesh._extract` makes
the buffer), OCP 7.9.3.1.1, build123d 0.11.1. No engine code was written; scripts are beside this file.

## Conclusion

At FreeCAD's default (P1), over 5,456 parts and 162,117 faces:
- **36 faces (in 14 parts) get no triangulation.** BRepMesh flags every one itself (`GetStatusFlags` = `SelfIntersectingWire`: the discretized wire crosses itself). 32 are on geometry BRepCheck calls valid. Most are slivers or strips of at most 6 linear-deflection² in area; radial's head faces are 12–17. The other 4 are on two tom parts BRepCheck calls invalid. One is large: radial `accessory:case` face 224, 8,180 mm², is lost at every policy.
- **42 parts have odd (open) triangle edges.** All are single solids except the two f14d wing panels (4 and 6 solids), so touching solids explain none of them. 14 of the 42 hold a missing face, so their odd edges are the hole's rim; the wing panels are among these. The other 28 have short cracks on B-rep edges with no face missing. 10 of those are the identical tom servo details: 84 edges of at most 0.28 mm, the same at every deflection. The one interior fault is hypercar `exhaust_primary`, a swept B-spline tube: its triangulation overlaps itself inside the face, and it gets worse as the mesh gets finer.
- **Curved faces stay on their surface.** Only 17 of 71,151 curved faces are more than 1.5× the deflection off, and 6 are more than 2×. 10 of the 17 are analytic (torus, sphere, cone) at 1.5–2×. The worst is that exhaust tube at 5.75× (8.5 mm). The f1 floor panel's 48× does not reproduce at any policy; f1's worst face is 2.7× at P1. The first pass read 139× and 38× on two faces, but those numbers were wrong: the gradient projection had stopped at a local minimum (table 4b).
- **No `IMeshTools_Parameters` knob restores a missing face or closes a crack.** `EnableControlSurfaceDeflectionAllSurfaces` brings the analytic faces under 1× for 2–13% more triangles on those parts; it is the one knob worth setting. Turning `ControlSurfaceDeflection` off cuts swept B-spline parts' triangles 3–40× and removes the exhaust tube's overlaps, but its deviation results are mixed: one face improves from 2.7× to 0.4×, while others worsen (1.8× to 5.6×, 4.2× to 10.8×). MinSize, AdjustMinSize, ForceFaceDeflection and InternalVerticesMode change nothing.
- **Re-meshing the part at linear/10 restores half the missing faces** (18 of 36), but not reliably: some come back at /2 and are lost again at /4. It does not restore radial's large face, 10 of f14d's 14, or tom's invalid ones. It closes some cracks (w16 couplers, moonwatch barrel bridge) but not tom's.

## What was measured

Policies, each on a fresh triangulation (`BRepTools.Clean_s` before every mesh), `BRepMesh_IncrementalMesh(shape, linear, False, angular, True)`:
P1 linear = (Lx+Ly+Lz)/300 × 0.5, angular 0.4974; P2 linear = 5e-4 × diagonal, angular 0.35; P3 linear = 1.5e-3 × diagonal, angular 0.35
(boxes from `BRepBndLib.Add_s(shape, box, False)`). Every distinct prototype is meshed once per policy.

Inputs: juno, hypercar and tom as `.xbf` (copied into `xbf/`); w16, radial, f1, f14d, moonwatch `movement` and `case`, and every `*.step` in
`models/motorbike/STEP` (motorbike.step holds the other motorbike files' parts too, so its parts are counted twice; it has no faults either way).
Every input read. **Not measured: the hand model (tendon_hand), which has no STEP output on disk.**

Metrics:
1. Triangles, buffer bytes (`Mesh.to_bytes`), wall time of the BRepMesh calls, 1-minute load average.
2. Faces with no triangulation, with BRepCheck and wire statuses, then a second pass (2b): BRepMesh status flags, area, shortest edge, ShapeAnalysis_Wire checks, BRepCheck on the clean part.
3. Odd edges: every triangle edge of a part as a pair of positions quantized at 1e-6 × diagonal; an edge used an odd number of times is open. 3b says how many lie between two nodes of B-rep edge polylines (boundary cracks) and how many edges more than two triangles use (overlapping triangles).
4. Deviation: on non-plane faces, up to 100 sampled triangle centroids per face. Each centroid's distance to the surface is the least of: bounded `GeomAPI_ProjectPointOnSurf`, Newton from the triangle's mean (u,v) (`ShapeAnalysis_Surface.NextValueOfUV`), and |S(mean uv) − centroid|. All three are real surface points, so the least is an upper bound on the true distance. Ratio = distance / part's linear deflection. Pass 1 used the gradient projection alone and gave false outliers (4b), so all deviation numbers here come from pass 2.
5. Knobs: the worst P1 cases (every missing face up to 10 per model, the 10 worst deviation faces, the 5 worst odd-edge parts per model) re-meshed through `IMeshTools_Parameters`. 3b adds re-meshing at linear/2, /4 and /10.

Caveats:
- Other agents' benchmarks ran on the same machine: load ran 6–165 on 10 cores. Compare policies within one model's rows (each part's P1/P2/P3 ran back to back), not wall times across models.
- The deviation metric samples centroids; it misses error between samples and vertex placement off the surface (that error is ~0: node-on-surface ≤ 2e-5 × linear, table 4b).
- A face listed in table 2 is missing from the display; its neighbours' rims then appear as odd edges in table 3.

## 1. Per model, per policy

Load = 1-minute load average on a 10-core machine (range over the model's parts). Wall = sum of `BRepMesh_IncrementalMesh` calls (parallel=True) over the model's prototypes, in this pass and in the first pass (same meshing; only the deviation metric changed between passes), each with its load range. Other agents' benchmarks shared the machine: compare policies within a row group, not across models.

| model | parts | faces | policy | triangles | buffer MB | mesh wall s | load | pass-1 wall s | pass-1 load | faces w/o triangulation | parts w/ odd edges | odd edges | curved faces | faces >1.5x | faces >2x | faces >5x | worst ratio |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| juno | 363 | 3707 | P1 | 161,858 | 6.2 | 0.9 | 7-7 | 0.7 | 6-7 | 0 | 0 | 0 | 1790 | 2 | 0 | 0 | 1.53 |
| juno | 363 | 3707 | P2 | 523,528 | 16.7 | 1.9 | 7-7 | 1.6 | 6-7 | 0 | 0 | 0 | 1790 | 11 | 0 | 0 | 1.86 |
| juno | 363 | 3707 | P3 | 270,540 | 9.4 | 1.2 | 7-7 | 0.9 | 6-7 | 0 | 0 | 0 | 1790 | 12 | 0 | 0 | 1.82 |
| hypercar | 987 | 8257 | P1 | 1,037,943 | 30.8 | 20.6 | 8-154 | 17.0 | 7-33 | 2 | 4 | 234 | 4760 | 3 | 3 | 1 | 5.75 |
| hypercar | 987 | 8257 | P2 | 4,680,414 | 122.1 | 259.2 | 8-154 | 192.8 | 7-33 | 0 | 4 | 2601 | 4760 | 21 | 13 | 1 | 7.95 |
| hypercar | 987 | 8257 | P3 | 2,020,193 | 56.1 | 53.9 | 8-154 | 45.0 | 7-33 | 0 | 3 | 1380 | 4760 | 16 | 4 | 1 | 7.61 |
| tom | 619 | 69196 | P1 | 940,748 | 45.6 | 6.4 | 9-15 | 7.3 | 13-14 | 4 | 19 | 1282 | 23545 | 0 | 0 | 0 | 1.30 |
| tom | 619 | 69196 | P2 | 1,413,928 | 61.9 | 7.1 | 9-15 | 7.8 | 13-14 | 4 | 19 | 1486 | 23545 | 123 | 58 | 32 | 7.11 |
| tom | 619 | 69196 | P3 | 1,256,530 | 57.2 | 6.8 | 9-15 | 8.0 | 13-14 | 6 | 21 | 1474 | 23543 | 58 | 28 | 0 | 2.37 |
| w16 | 798 | 23070 | P1 | 898,143 | 35.9 | 7.6 | 19-81 | 5.7 | 8-33 | 0 | 4 | 28 | 11476 | 6 | 0 | 0 | 1.67 |
| w16 | 798 | 23070 | P2 | 2,160,183 | 72.7 | 10.8 | 19-81 | 9.3 | 8-33 | 4 | 4 | 178 | 11476 | 44 | 7 | 0 | 4.56 |
| w16 | 798 | 23070 | P3 | 1,377,129 | 51.2 | 9.4 | 19-81 | 7.2 | 8-33 | 2 | 4 | 98 | 11476 | 6 | 2 | 0 | 2.28 |
| radial | 468 | 21073 | P1 | 1,259,507 | 43.6 | 13.7 | 46-165 | 8.2 | 20-45 | 5 | 5 | 383 | 12795 | 0 | 0 | 0 | 1.36 |
| radial | 468 | 21073 | P2 | 3,940,059 | 114.7 | 22.2 | 46-165 | 17.4 | 20-45 | 3 | 5 | 799 | 12795 | 44 | 10 | 1 | 5.14 |
| radial | 468 | 21073 | P3 | 2,127,492 | 68.1 | 17.6 | 46-165 | 10.9 | 20-45 | 4 | 5 | 504 | 12795 | 7 | 2 | 0 | 2.11 |
| f1 | 830 | 9343 | P1 | 1,471,238 | 44.3 | 27.8 | 13-163 | 17.4 | 13-52 | 1 | 4 | 41 | 4887 | 4 | 2 | 0 | 2.73 |
| f1 | 830 | 9343 | P2 | 5,815,458 | 154.4 | 96.5 | 13-163 | 80.5 | 13-52 | 1 | 3 | 77 | 4887 | 10 | 4 | 1 | 6.51 |
| f1 | 830 | 9343 | P3 | 2,957,786 | 83.3 | 49.8 | 13-163 | 37.8 | 13-52 | 1 | 4 | 53 | 4887 | 7 | 2 | 0 | 3.31 |
| f14d | 1127 | 14654 | P1 | 664,175 | 21.1 | 8.9 | 7-48 | 5.9 | 7-25 | 14 | 2 | 98 | 6922 | 2 | 1 | 0 | 2.04 |
| f14d | 1127 | 14654 | P2 | 1,770,901 | 50.0 | 21.0 | 7-48 | 15.7 | 7-25 | 10 | 2 | 54 | 6922 | 4 | 1 | 0 | 2.11 |
| f14d | 1127 | 14654 | P3 | 1,111,617 | 32.8 | 12.4 | 7-48 | 9.1 | 7-25 | 14 | 2 | 74 | 6922 | 4 | 0 | 0 | 1.68 |
| moonwatch/case | 24 | 3202 | P1 | 63,630 | 2.8 | 1.4 | 11-11 | 1.4 | 7-8 | 7 | 1 | 1138 | 687 | 0 | 0 | 0 | 1.30 |
| moonwatch/case | 24 | 3202 | P2 | 144,734 | 5.2 | 2.7 | 11-11 | 2.6 | 7-8 | 0 | 0 | 0 | 687 | 2 | 0 | 0 | 1.94 |
| moonwatch/case | 24 | 3202 | P3 | 91,709 | 3.7 | 0.5 | 11-11 | 0.5 | 7-8 | 3 | 1 | 591 | 687 | 1 | 0 | 0 | 1.63 |
| moonwatch/movement | 158 | 7959 | P1 | 159,125 | 7.7 | 1.5 | 11-12 | 1.2 | 8-8 | 3 | 3 | 45 | 3429 | 0 | 0 | 0 | 0.72 |
| moonwatch/movement | 158 | 7959 | P2 | 260,508 | 11.4 | 1.8 | 11-12 | 1.5 | 8-8 | 1 | 1 | 10 | 3431 | 0 | 0 | 0 | 1.01 |
| moonwatch/movement | 158 | 7959 | P3 | 207,443 | 9.6 | 1.6 | 11-12 | 1.3 | 8-8 | 1 | 2 | 19 | 3431 | 0 | 0 | 0 | 0.88 |
| motorbike (all files) | 82 | 1656 | P1 | 90,202 | 3.4 | 0.4 | 7-11 | 0.4 | 6-8 | 0 | 0 | 0 | 860 | 0 | 0 | 0 | 1.08 |
| motorbike (all files) | 82 | 1656 | P2 | 251,306 | 7.9 | 1.1 | 7-11 | 1.0 | 6-8 | 0 | 0 | 0 | 860 | 4 | 4 | 0 | 3.79 |
| motorbike (all files) | 82 | 1656 | P3 | 139,538 | 5.0 | 0.6 | 7-11 | 0.5 | 6-8 | 0 | 0 | 0 | 860 | 4 | 0 | 0 | 1.61 |

## 2. Faces with no triangulation after meshing

Total rows (face x policy): 90; P1: 36, P2: 23, P3: 31

| model | part | face | policy | surface | BRepCheck valid | face status | wire statuses | edge statuses |
|---|---|---|---|---|---|---|---|---|
| hypercar | lower_wishbone:left (#514) | 9 | P1 | Plane | True | - | ok | - |
| hypercar | lower_wishbone:right (#562) | 4 | P1 | Plane | True | - | ok | - |
| tom | =>[0:1:1:75] (#359) | 19 | P1 | Cylinder | False | UnorientableShape | SelfIntersectingWire | - |
| tom | =>[0:1:1:75] (#359) | 80 | P1 | Cylinder | False | - | ok | InvalidPolygonOnTriangulation |
| tom | =>[0:1:1:75] (#359) | 19 | P2 | Cylinder | False | UnorientableShape | SelfIntersectingWire | - |
| tom | =>[0:1:1:75] (#359) | 80 | P2 | Cylinder | False | - | ok | InvalidPolygonOnTriangulation |
| tom | =>[0:1:1:75] (#359) | 19 | P3 | Cylinder | False | UnorientableShape | SelfIntersectingWire | - |
| tom | =>[0:1:1:75] (#359) | 80 | P3 | Cylinder | False | - | ok | InvalidPolygonOnTriangulation |
| tom | =>[0:1:1:75] (#360) | 19 | P1 | Cylinder | False | UnorientableShape | SelfIntersectingWire | - |
| tom | =>[0:1:1:75] (#360) | 80 | P1 | Cylinder | False | - | ok | InvalidPolygonOnTriangulation |
| tom | =>[0:1:1:75] (#360) | 19 | P2 | Cylinder | False | UnorientableShape | SelfIntersectingWire | - |
| tom | =>[0:1:1:75] (#360) | 80 | P2 | Cylinder | False | - | ok | InvalidPolygonOnTriangulation |
| tom | =>[0:1:1:75] (#360) | 19 | P3 | Cylinder | False | UnorientableShape | SelfIntersectingWire | - |
| tom | =>[0:1:1:75] (#360) | 80 | P3 | Cylinder | False | - | ok | InvalidPolygonOnTriangulation |
| tom | =>[0:1:1:192] (#511) | 74 | P3 | Cylinder | True | - | ok | - |
| tom | =>[0:1:1:192] (#533) | 74 | P3 | Cylinder | True | - | ok | - |
| w16 | charge_coupler:2_1 (#713) | 9 | P2 | Plane | True | - | ok | - |
| w16 | charge_coupler:2_1 (#713) | 11 | P2 | Plane | True | - | ok | - |
| w16 | charge_coupler:2_1 (#713) | 9 | P3 | Plane | True | - | ok | - |
| w16 | charge_coupler:2_2 (#716) | 9 | P2 | Plane | True | - | ok | - |
| w16 | charge_coupler:2_2 (#716) | 11 | P2 | Plane | True | - | ok | - |
| w16 | charge_coupler:2_2 (#716) | 9 | P3 | Plane | True | - | ok | - |
| radial | heads:head_1 (#79) | 227 | P1 | Plane | True | - | ok | - |
| radial | heads:head_1 (#79) | 227 | P2 | Plane | True | - | ok | - |
| radial | heads:head_1 (#79) | 227 | P3 | Plane | True | - | ok | - |
| radial | heads:head_9 (#80) | 219 | P1 | Plane | True | - | ok | - |
| radial | heads:head_9 (#80) | 1576 | P1 | Plane | True | - | ok | - |
| radial | heads:head_9 (#80) | 219 | P2 | Plane | True | - | ok | - |
| radial | heads:head_9 (#80) | 219 | P3 | Plane | True | - | ok | - |
| radial | intake:elbow_9 (#317) | 13 | P1 | Plane | True | - | ok | - |
| radial | intake:elbow_9 (#317) | 13 | P3 | Plane | True | - | ok | - |
| radial | accessory:case (#340) | 224 | P1 | BSplineSurface | True | - | ok | - |
| radial | accessory:case (#340) | 224 | P2 | BSplineSurface | True | - | ok | - |
| radial | accessory:case (#340) | 224 | P3 | BSplineSurface | True | - | ok | - |
| f1 | sidepod_panel:left (#257) | 81 | P1 | Plane | True | - | ok | - |
| f1 | r_rocker:right (#625) | 44 | P2 | Plane | True | - | ok | - |
| f1 | r_rocker:right (#625) | 44 | P3 | Plane | True | - | ok | - |
| f14d | wing_panel:port (#140) | 54 | P1 | Plane | True | - | ok | - |
| f14d | wing_panel:port (#140) | 56 | P1 | Plane | True | - | ok | - |
| f14d | wing_panel:port (#140) | 57 | P1 | Plane | True | - | ok | - |
| f14d | wing_panel:port (#140) | 59 | P1 | Plane | True | - | ok | - |
| f14d | wing_panel:port (#140) | 66 | P1 | Plane | True | - | ok | - |
| f14d | wing_panel:port (#140) | 68 | P1 | Plane | True | - | ok | - |
| f14d | wing_panel:port (#140) | 69 | P1 | Plane | True | - | ok | - |
| f14d | wing_panel:port (#140) | 57 | P2 | Plane | True | - | ok | - |
| f14d | wing_panel:port (#140) | 59 | P2 | Plane | True | - | ok | - |
| f14d | wing_panel:port (#140) | 66 | P2 | Plane | True | - | ok | - |
| f14d | wing_panel:port (#140) | 68 | P2 | Plane | True | - | ok | - |
| f14d | wing_panel:port (#140) | 69 | P2 | Plane | True | - | ok | - |
| f14d | wing_panel:port (#140) | 54 | P3 | Plane | True | - | ok | - |
| f14d | wing_panel:port (#140) | 56 | P3 | Plane | True | - | ok | - |
| f14d | wing_panel:port (#140) | 57 | P3 | Plane | True | - | ok | - |
| f14d | wing_panel:port (#140) | 59 | P3 | Plane | True | - | ok | - |
| f14d | wing_panel:port (#140) | 66 | P3 | Plane | True | - | ok | - |
| f14d | wing_panel:port (#140) | 68 | P3 | Plane | True | - | ok | - |
| f14d | wing_panel:port (#140) | 69 | P3 | Plane | True | - | ok | - |
| f14d | wing_panel:stbd (#168) | 56 | P1 | Plane | True | - | ok | - |
| f14d | wing_panel:stbd (#168) | 58 | P1 | Plane | True | - | ok | - |
| f14d | wing_panel:stbd (#168) | 59 | P1 | Plane | True | - | ok | - |
| f14d | wing_panel:stbd (#168) | 61 | P1 | Plane | True | - | ok | - |
| f14d | wing_panel:stbd (#168) | 68 | P1 | Plane | True | - | ok | - |
| f14d | wing_panel:stbd (#168) | 70 | P1 | Plane | True | - | ok | - |
| f14d | wing_panel:stbd (#168) | 71 | P1 | Plane | True | - | ok | - |
| f14d | wing_panel:stbd (#168) | 59 | P2 | Plane | True | - | ok | - |
| f14d | wing_panel:stbd (#168) | 61 | P2 | Plane | True | - | ok | - |
| f14d | wing_panel:stbd (#168) | 68 | P2 | Plane | True | - | ok | - |
| f14d | wing_panel:stbd (#168) | 70 | P2 | Plane | True | - | ok | - |
| f14d | wing_panel:stbd (#168) | 71 | P2 | Plane | True | - | ok | - |
| f14d | wing_panel:stbd (#168) | 56 | P3 | Plane | True | - | ok | - |
| f14d | wing_panel:stbd (#168) | 58 | P3 | Plane | True | - | ok | - |
| f14d | wing_panel:stbd (#168) | 59 | P3 | Plane | True | - | ok | - |
| f14d | wing_panel:stbd (#168) | 61 | P3 | Plane | True | - | ok | - |
| f14d | wing_panel:stbd (#168) | 68 | P3 | Plane | True | - | ok | - |
| f14d | wing_panel:stbd (#168) | 70 | P3 | Plane | True | - | ok | - |
| f14d | wing_panel:stbd (#168) | 71 | P3 | Plane | True | - | ok | - |
| moonwatch/case | case_middle (#0) | 89 | P1 | Plane | True | - | ok | - |
| moonwatch/case | case_middle (#0) | 288 | P1 | Plane | True | - | ok | - |
| moonwatch/case | case_middle (#0) | 294 | P1 | Plane | True | - | ok | - |
| moonwatch/case | case_middle (#0) | 376 | P1 | Plane | True | - | ok | - |
| moonwatch/case | case_middle (#0) | 382 | P1 | Plane | True | - | ok | - |

(10 more rows in raw.json)

## 3. Open (odd-count) triangle edges per part, P1

Per model: parts with any odd edge at P1 / P2 / P3

| model | P1 | P2 | P3 |
|---|---|---|---|
| juno | 0 | 0 | 0 |
| hypercar | 4 | 4 | 3 |
| tom | 19 | 19 | 21 |
| w16 | 4 | 4 | 4 |
| radial | 5 | 5 | 5 |
| f1 | 4 | 3 | 4 |
| f14d | 2 | 2 | 2 |
| moonwatch/case | 1 | 0 | 1 |
| moonwatch/movement | 3 | 1 | 2 |
| motorbike (all files) | 0 | 0 | 0 |

Worst ten parts by odd-edge count at P1:

| model | part | solids | faces | odd edges P1 | longest odd edge (mm) | longest / P1 linear | odd P2 | odd P3 | null faces P1 |
|---|---|---|---|---|---|---|---|---|---|
| moonwatch/case | case_middle | 1 | 1529 | 1138 | 4.74 | 27.38 | 0 | 591 | 7 |
| radial | accessory:case | 1 | 254 | 265 | 59.7 | 35.29 | 478 | 375 | 1 |
| hypercar | exhaust_primary:rear_right_2 | 1 | 3 | 198 | 0.241 | 0.16 | 2222 | 1298 | 0 |
| tom | shoulder_yaw_servo_detail_10 | 1 | 60 | 84 | 0.283 | 6.98 | 94 | 90 | 0 |
| tom | shoulder_yaw_servo_detail_11 | 1 | 60 | 84 | 0.283 | 6.98 | 94 | 90 | 0 |
| tom | shoulder_pitch_servo_detail_10 | 1 | 60 | 84 | 0.283 | 6.98 | 94 | 90 | 0 |
| tom | shoulder_pitch_servo_detail_11 | 1 | 60 | 84 | 0.283 | 6.98 | 94 | 90 | 0 |
| tom | elbow_pitch_servo_detail_10 | 1 | 60 | 84 | 0.283 | 6.98 | 94 | 90 | 0 |
| tom | elbow_pitch_servo_detail_11 | 1 | 60 | 84 | 0.283 | 6.98 | 94 | 90 | 0 |
| tom | elbow_roll_servo_detail_10 | 1 | 60 | 84 | 0.283 | 6.98 | 94 | 90 | 0 |

## 4. Surface deviation of curved faces (triangle centroids projected on the face's surface)

Ratio = distance / the part's linear deflection under that policy; up to 100 centroids per face. Only faces whose max ratio > 1.5 are stored per face.

Worst twenty faces at P1:

| model | part | face | surface | triangles | max ratio | p99 ratio | gradient projection alone | same face P2 max | same face P3 max |
|---|---|---|---|---|---|---|---|---|---|
| hypercar | exhaust_primary:rear_right_2 (#780) | 1 | BSplineSurface | 47242 | 5.75 | 3.47 | 5.75 | 3.27 | 7.61 |
| hypercar | exhaust_primary:rear_right_3 (#782) | 1 | BSplineSurface | 33076 | 4.18 | 3.79 | 4.18 | 1.87 | 3.13 |
| f1 | f_torsion_anchor:right (#562) | 1 | BSplineSurface | 562 | 2.73 | 1.01 | 2.73 | <=1.5 | <=1.5 |
| f1 | tyre_tread:rl (#733) | 1 | SurfaceOfRevolution | 870 | 2.35 | 0.99 | 2.35 | <=1.5 | <=1.5 |
| hypercar | exhaust_primary:rear_left_1 (#769) | 1 | BSplineSurface | 22302 | 2.06 | 1.33 | 2.06 | <=1.5 | <=1.5 |
| f14d | refuel_probe_head:stbd (#1034) | 1 | Cone | 274 | 2.04 | 0.79 | 2.04 | 2.11 | <=1.5 |
| f1 | mgu_h_cable (#397) | 1 | BSplineSurface | 1588 | 1.82 | 0.92 | 1.82 | <=1.5 | <=1.5 |
| f14d | drain_mast:stbd_01 (#1113) | 1 | Cone | 274 | 1.68 | 0.74 | 1.68 | <=1.5 | <=1.5 |
| w16 | lash_adjuster:2_inr (#214) | 4 | Sphere | 341 | 1.67 | 1.18 | 1.67 | <=1.5 | <=1.5 |
| w16 | lash_adjuster:2_inf (#217) | 4 | Sphere | 341 | 1.67 | 1.18 | 1.67 | <=1.5 | <=1.5 |
| w16 | lash_adjuster:2_exr (#220) | 4 | Sphere | 341 | 1.67 | 1.18 | 1.67 | <=1.5 | <=1.5 |
| w16 | lash_adjuster:2_exf (#223) | 4 | Sphere | 341 | 1.67 | 1.18 | 1.67 | <=1.5 | <=1.5 |
| w16 | downpipe_flange:2_front (#537) | 3 | Torus | 611 | 1.62 | 1.25 | 1.62 | 2.03 | <=1.5 |
| w16 | downpipe_flange:2_rear (#560) | 3 | Torus | 611 | 1.62 | 1.25 | 1.62 | 2.03 | <=1.5 |
| juno | shoulder_roll_can (#145) | 2 | Torus | 587 | 1.53 | 1.26 | 1.53 | <=1.5 | <=1.5 |
| juno | shoulder_roll_can (#291) | 2 | Torus | 587 | 1.53 | 1.26 | 1.53 | <=1.5 | <=1.5 |
| f1 | pitot_boss (#30) | 1 | BSplineSurface | 948 | 1.51 | 0.79 | 1.51 | <=1.5 | <=1.5 |

Distribution of faces above 1.5x at P1 by surface type:

| surface | faces >1.5x | of which >5x |
|---|---|---|
| BSplineSurface | 6 | 1 |
| Torus | 4 | 0 |
| Sphere | 4 | 0 |
| Cone | 2 | 0 |
| SurfaceOfRevolution | 1 | 0 |

## 5. OCCT knobs (`IMeshTools_Parameters`) on the worst P1 cases

Cell: for a null face, whether it meshed (Y/N); for a deviation face, its max ratio; for an odd-edge part, the part's odd-edge count. `default` = the P1 call through `IMeshTools_Parameters` (ControlSurfaceDeflection on, InternalVerticesMode on, MinSize auto).

| model | part | face | kind | default | CSD_off | CSD_all | MinSize_0.1lin | AdjustMinSize | ForceFaceDefl | IntVert_off |
|---|---|---|---|---|---|---|---|---|---|---|
| hypercar | lower_wishbone:left (#514) | 9 | null Plane | N | N | N | N | N | N | N |
| hypercar | lower_wishbone:right (#562) | 4 | null Plane | N | N | N | N | N | N | N |
| tom | =>[0:1:1:75] (#359) | 19 | null Cylinder | N | N | N | N | N | N | N |
| tom | =>[0:1:1:75] (#359) | 80 | null Cylinder | N | N | N | N | N | N | N |
| tom | =>[0:1:1:75] (#360) | 19 | null Cylinder | N | N | N | N | N | N | N |
| tom | =>[0:1:1:75] (#360) | 80 | null Cylinder | N | N | N | N | N | N | N |
| radial | heads:head_1 (#79) | 227 | null Plane | N | N | N | N | N | N | N |
| radial | heads:head_9 (#80) | 219 | null Plane | N | N | N | N | N | N | N |
| hypercar | exhaust_primary:rear_right_2 (#780) | 1 | dev BSplineSurface | 5.75 | 6.56 | 5.75 | 5.75 | 5.75 | 5.75 | 5.75 |
| hypercar | exhaust_primary:rear_right_3 (#782) | 1 | dev BSplineSurface | 4.18 | 10.77 | 4.18 | 4.18 | 4.18 | 4.18 | 4.18 |
| f1 | f_torsion_anchor:right (#562) | 1 | dev BSplineSurface | 2.73 | 0.41 | 2.73 | 2.73 | 2.73 | 2.73 | 2.73 |
| f1 | tyre_tread:rl (#733) | 1 | dev SurfaceOfRevolution | 2.35 | 2.04 | 2.35 | 2.35 | 2.35 | 2.35 | 2.35 |
| hypercar | exhaust_primary:rear_left_1 (#769) | 1 | dev BSplineSurface | 2.06 | 3.47 | 2.06 | 2.06 | 2.06 | 2.06 | 2.06 |
| f14d | refuel_probe_head:stbd (#1034) | 1 | dev Cone | 2.04 | 2.04 | 0.78 | 2.04 | 2.04 | 2.04 | 2.04 |
| f1 | mgu_h_cable (#397) | 1 | dev BSplineSurface | 1.82 | 5.55 | 1.82 | 1.82 | 1.82 | 1.82 | 1.82 |
| f14d | drain_mast:stbd_01 (#1113) | 1 | dev Cone | 1.68 | 1.68 | 0.73 | 1.68 | 1.68 | 1.68 | 1.68 |
| moonwatch/case | case_middle (#0) | - | odd  | 1138 | 1138 | 1138 | 1138 | 1138 | 1138 | 1138 |
| radial | accessory:case (#340) | - | odd  | 265 | 265 | 265 | 265 | 265 | 265 | 265 |
| hypercar | exhaust_primary:rear_right_2 (#780) | - | odd  | 198 | 0 | 198 | 198 | 198 | 198 | 198 |
| tom | wrist_yaw_servo_detail_11 (#127) | - | odd  | 84 | 72 | 84 | 84 | 84 | 84 | 84 |

Triangle counts of the part per variant (same rows):

| model | part | default | CSD_off | CSD_all | MinSize_0.1lin | AdjustMinSize | ForceFaceDefl | IntVert_off |
|---|---|---|---|---|---|---|---|---|
| hypercar | lower_wishbone:left | 5802 | 2152 | 5866 | 5802 | 5804 | 5802 | 5802 |
| hypercar | lower_wishbone:right | 5848 | 2198 | 5912 | 5848 | 5848 | 5848 | 5848 |
| tom | =>[0:1:1:75] | 3196 | 2588 | 3214 | 3196 | 3238 | 3196 | 3196 |
| tom | =>[0:1:1:75] | 3196 | 2588 | 3214 | 3196 | 3238 | 3196 | 3196 |
| tom | =>[0:1:1:75] | 3196 | 2588 | 3214 | 3196 | 3238 | 3196 | 3196 |
| tom | =>[0:1:1:75] | 3196 | 2588 | 3214 | 3196 | 3238 | 3196 | 3196 |
| radial | heads:head_1 | 20965 | 20725 | 20991 | 20965 | 20965 | 20965 | 20965 |
| radial | heads:head_9 | 41125 | 39983 | 41193 | 41125 | 41129 | 41125 | 41125 |
| hypercar | exhaust_primary:rear_right_2 | 47294 | 1198 | 47294 | 47294 | 47294 | 47294 | 47294 |
| hypercar | exhaust_primary:rear_right_3 | 33126 | 1056 | 33126 | 33126 | 33126 | 33126 | 33126 |
| f1 | f_torsion_anchor:right | 740 | 252 | 740 | 740 | 740 | 740 | 740 |
| f1 | tyre_tread:rl | 1542 | 888 | 1542 | 1542 | 1542 | 1542 | 1542 |
| hypercar | exhaust_primary:rear_left_1 | 22350 | 1338 | 22350 | 22350 | 22350 | 22350 | 22350 |
| f14d | refuel_probe_head:stbd | 326 | 326 | 332 | 326 | 326 | 326 | 326 |
| f1 | mgu_h_cable | 1640 | 352 | 1640 | 1640 | 1640 | 1640 | 1640 |
| f14d | drain_mast:stbd_01 | 326 | 326 | 332 | 326 | 326 | 326 | 326 |
| moonwatch/case | case_middle | 17506 | 17354 | 17506 | 17506 | 17506 | 17506 | 17506 |
| radial | accessory:case | 24782 | 23946 | 24914 | 24782 | 24846 | 24782 | 24782 |
| hypercar | exhaust_primary:rear_right_2 | 47294 | 1198 | 47294 | 47294 | 47294 | 47294 | 47294 |
| tom | wrist_yaw_servo_detail_11 | 1672 | 1180 | 1672 | 1672 | 1672 | 1672 | 1672 |

## 2b. What the untriangulated faces are (P1)

`BRepMesh flags` = `GetStatusFlags()` of the part's mesher. Strip width ~ area / shortest edge is not computed; area and shortest edge are given against the linear deflection. `ShapeAnalysis` = `ShapeAnalysis_Wire` checks on the face's wires (2D/3D gaps, self-intersection, small edges). `alone` = the face copied out and meshed by itself.

| model | part | face | BRepMesh flags | area mm2 | area / linear^2 | edges | shortest edge / linear | ShapeAnalysis | BRepCheck (clean part) | alone meshes |
|---|---|---|---|---|---|---|---|---|---|---|
| f1 | sidepod_panel:left (#257) | 81 | SelfIntersectingWire | 105 | 5.12 | 4 | 0.146 | - | valid | False |
| f14d | wing_panel:port (#140) | 54 | SelfIntersectingWire | 834 | 1.53 | 5 | 0.0752 | wire-self-intersection | valid | False |
| f14d | wing_panel:port (#140) | 56 | SelfIntersectingWire | 823 | 1.51 | 5 | 0.0744 | wire-self-intersection | valid | False |
| f14d | wing_panel:port (#140) | 57 | SelfIntersectingWire | 165 | 0.303 | 4 | 0.0405 | - | valid | False |
| f14d | wing_panel:port (#140) | 59 | SelfIntersectingWire | 162 | 0.298 | 4 | 0.0399 | - | valid | False |
| f14d | wing_panel:port (#140) | 66 | SelfIntersectingWire | 82.1 | 0.151 | 4 | 0.0355 | - | valid | False |
| f14d | wing_panel:port (#140) | 68 | SelfIntersectingWire | 83 | 0.152 | 4 | 0.0354 | - | valid | False |
| f14d | wing_panel:port (#140) | 69 | SelfIntersectingWire | 315 | 0.577 | 6 | 0.0694 | wire-self-intersection | valid | False |
| f14d | wing_panel:stbd (#168) | 56 | SelfIntersectingWire | 834 | 1.53 | 5 | 0.0752 | wire-self-intersection | valid | False |
| f14d | wing_panel:stbd (#168) | 58 | SelfIntersectingWire | 823 | 1.51 | 5 | 0.0744 | wire-self-intersection | valid | False |
| f14d | wing_panel:stbd (#168) | 59 | SelfIntersectingWire | 165 | 0.303 | 4 | 0.0405 | - | valid | False |
| f14d | wing_panel:stbd (#168) | 61 | SelfIntersectingWire | 162 | 0.298 | 4 | 0.0399 | - | valid | False |
| f14d | wing_panel:stbd (#168) | 68 | SelfIntersectingWire | 82.1 | 0.151 | 4 | 0.0355 | - | valid | False |
| f14d | wing_panel:stbd (#168) | 70 | SelfIntersectingWire | 83 | 0.152 | 4 | 0.0354 | - | valid | False |
| f14d | wing_panel:stbd (#168) | 71 | SelfIntersectingWire | 315 | 0.577 | 6 | 0.0694 | wire-self-intersection | valid | False |
| hypercar | lower_wishbone:left (#514) | 9 | SelfIntersectingWire | 1.12 | 0.311 | 2 | 4.9 | - | valid | False |
| hypercar | lower_wishbone:right (#562) | 4 | SelfIntersectingWire | 1.12 | 0.311 | 2 | 4.9 | - | valid | False |
| moonwatch/case | case_middle (#0) | 89 | ReMesh,SelfIntersectingWire | 0.00678 | 0.226 | 4 | 0.00558 | - | valid | False |
| moonwatch/case | case_middle (#0) | 288 | ReMesh,SelfIntersectingWire | 0.00154 | 0.0514 | 4 | 0.000565 | - | valid | False |
| moonwatch/case | case_middle (#0) | 294 | ReMesh,SelfIntersectingWire | 0.00352 | 0.117 | 4 | 0.00129 | - | valid | False |
| moonwatch/case | case_middle (#0) | 376 | ReMesh,SelfIntersectingWire | 0.00217 | 0.0721 | 4 | 0.000565 | - | valid | False |
| moonwatch/case | case_middle (#0) | 382 | ReMesh,SelfIntersectingWire | 0.00486 | 0.162 | 4 | 0.00129 | - | valid | False |
| moonwatch/case | case_middle (#0) | 408 | ReMesh,SelfIntersectingWire | 0.00154 | 0.0514 | 4 | 0.000565 | - | valid | False |
| moonwatch/case | case_middle (#0) | 414 | ReMesh,SelfIntersectingWire | 0.00352 | 0.117 | 4 | 0.00129 | - | valid | False |
| moonwatch/movement | click (#46) | 107 | SelfIntersectingWire | 4.06e-08 | 0.000531 | 3 | 0.0153 | - | valid | False |
| moonwatch/movement | pallet_bridge (#54) | 14 | SelfIntersectingWire | 0.0006 | 3.23 | 2 | 11.6 | - | valid | False |
| moonwatch/movement | pallet_bridge (#54) | 17 | SelfIntersectingWire | 0.00119 | 6.39 | 2 | 14.5 | - | valid | False |
| radial | heads:head_1 (#79) | 227 | SelfIntersectingWire | 24.1 | 16.7 | 3 | 0.0808 | - | valid | False |
| radial | heads:head_9 (#80) | 219 | SelfIntersectingWire | 22.5 | 12.6 | 2 | 50.4 | wire-self-intersection | valid | False |
| radial | heads:head_9 (#80) | 1576 | SelfIntersectingWire | 24.3 | 13.7 | 3 | 2.09 | wire-self-intersection | valid | False |
| radial | intake:elbow_9 (#317) | 13 | SelfIntersectingWire | 0.182 | 0.652 | 3 | 74.3 | gap2d,gap3d,wire-self-intersection | valid | False |
| radial | accessory:case (#340) | 224 | ReMesh,SelfIntersectingWire | 8.18e+03 | 2.86e+03 | 14 | 8.58e-05 | gap2d | valid | False |
| tom | =>[0:1:1:75] (#359) | 19 | ReMesh,SelfIntersectingWire | 4.33e-16 | 3.54e-12 | 2 | 119 | - | INVALID | False |
| tom | =>[0:1:1:75] (#359) | 80 | ReMesh,SelfIntersectingWire | 0.00318 | 26 | 8 | 0.0789 | small-edge | INVALID | False |
| tom | =>[0:1:1:75] (#360) | 19 | ReMesh,SelfIntersectingWire | 4.33e-16 | 3.54e-12 | 2 | 119 | - | INVALID | False |
| tom | =>[0:1:1:75] (#360) | 80 | ReMesh,SelfIntersectingWire | 0.00318 | 26 | 8 | 0.0789 | small-edge | INVALID | False |

## 3b. Fault parts re-meshed at finer linear deflection (P1 linear / k, same angular)

Cell: untriangulated faces / odd edges (of which on B-rep edge polylines) / edges used by >2 triangles. BRepCheck is run on the part after `BRepTools.Clean_s`.

| model | part | solids | BRepCheck | flags /1 | /1 | /2 | /4 | /10 | longest odd edge / linear at /1 |
|---|---|---|---|---|---|---|---|---|---|
| f1 | sidepod_panel:left (#257) | 1 | valid | SelfIntersectingWire | 1 / 15 (15) / 39 | 0 / 8 (8) / 22 | 0 / 0 (0) / 80 | 0 / 0 (0) / 160 | 13.2 |
| f1 | wastegate_pipe (#388) | 1 | INVALID | - | 0 / 17 (17) / 0 | 0 / 27 (25) / 0 | 0 / 53 (49) / 0 | 0 / 46 (40) / 0 | 12.9 |
| f1 | rear_pickup_carrier:left (#459) | 1 | valid | - | 0 / 5 (5) / 0 | 0 / 0 (0) / 0 | 0 / 0 (0) / 0 | 0 / 0 (0) / 0 | 3.11 |
| f1 | survival_cell (#75) | 1 | valid | - | 0 / 4 (4) / 0 | 1 / 54 (54) / 0 | 0 / 0 (0) / 6 | 0 / 0 (0) / 7 | 10.4 |
| f14d | wing_panel:port (#140) | 6 | valid | SelfIntersectingWire | 7 / 49 (49) / 158 | 7 / 37 (37) / 159 | 5 / 31 (31) / 158 | 5 / 23 (23) / 189 | 42.1 |
| f14d | wing_panel:stbd (#168) | 4 | valid | SelfIntersectingWire | 7 / 49 (49) / 236 | 7 / 37 (37) / 237 | 5 / 31 (31) / 238 | 5 / 23 (23) / 274 | 42.1 |
| hypercar | lower_wishbone:left (#514) | 1 | valid | SelfIntersectingWire | 1 / 11 (11) / 1 | 1 / 11 (11) / 1 | 1 / 11 (11) / 1 | 0 / 0 (0) / 1 | 9.31 |
| hypercar | lower_wishbone:right (#562) | 1 | valid | SelfIntersectingWire | 1 / 11 (11) / 1 | 1 / 5 (5) / 1 | 1 / 5 (5) / 1 | 0 / 0 (0) / 1 | 9.31 |
| hypercar | exhaust_primary:rear_right_2 (#780) | 1 | valid | - | 0 / 198 (0) / 25 | 0 / 483 (0) / 117 | 0 / 1263 (0) / 464 | 0 / 8098 (0) / 1820 | 0.163 |
| hypercar | monocoque_tub:centre (#125) | 1 | INVALID | - | 0 / 14 (14) / 9 | 0 / 21 (21) / 113 | 0 / 7 (7) / 126 | 1 / 634 (43) / 353 | 54.9 |
| moonwatch/case | case_middle (#0) | 1 | valid | ReMesh,SelfIntersectingWire | 7 / 1138 (685) / 8 | 3 / 646 (334) / 0 | 0 / 0 (0) / 0 | 0 / 0 (0) / 0 | 27.4 |
| moonwatch/movement | pallet_bridge (#54) | 1 | valid | SelfIntersectingWire | 2 / 8 (8) / 0 | 0 / 0 (0) / 0 | 0 / 0 (0) / 0 | 0 / 0 (0) / 0 | 14.5 |
| moonwatch/movement | click (#46) | 1 | valid | SelfIntersectingWire | 1 / 10 (10) / 1 | 1 / 10 (10) / 1 | 1 / 10 (10) / 0 | 1 / 10 (10) / 0 | 0.0483 |
| moonwatch/movement | barrel_bridge (#15) | 1 | valid | - | 0 / 27 (27) / 11 | 0 / 0 (0) / 0 | 0 / 0 (0) / 0 | 0 / 0 (0) / 0 | 57.3 |
| radial | heads:head_9 (#80) | 1 | valid | SelfIntersectingWire | 2 / 60 (47) / 1 | 1 / 34 (21) / 1 | 1 / 34 (21) / 1 | 1 / 45 (24) / 1 | 4.49 |
| radial | accessory:case (#340) | 1 | valid | ReMesh,SelfIntersectingWire | 1 / 265 (167) / 5 | 1 / 308 (205) / 5 | 1 / 394 (263) / 5 | 1 / 591 (399) / 7 | 35.3 |
| radial | heads:head_1 (#79) | 1 | valid | SelfIntersectingWire | 1 / 28 (14) / 1 | 0 / 0 (0) / 1 | 1 / 31 (15) / 1 | 0 / 0 (0) / 1 | 4.63 |
| radial | intake:elbow_9 (#317) | 1 | valid | SelfIntersectingWire | 1 / 26 (26) / 0 | 1 / 34 (34) / 0 | 0 / 211 (211) / 0 | 2 / 1638 (1089) / 0 | 13.1 |
| radial | rocker9I:arm (#150) | 1 | valid | - | 0 / 4 (4) / 7 | 0 / 4 (4) / 7 | 0 / 4 (4) / 7 | 0 / 4 (4) / 7 | 8.88 |
| tom | =>[0:1:1:75] (#359) | 1 | INVALID | ReMesh,SelfIntersectingWire | 2 / 20 (20) / 15 | 2 / 23 (23) / 15 | 2 / 26 (26) / 15 | 2 / 31 (31) / 15 | 139 |
| tom | =>[0:1:1:75] (#360) | 1 | INVALID | ReMesh,SelfIntersectingWire | 2 / 20 (20) / 15 | 2 / 23 (23) / 15 | 2 / 26 (26) / 15 | 2 / 31 (31) / 15 | 139 |
| tom | shoulder_yaw_servo_detail_10 (#10) | 1 | valid | - | 0 / 84 (74) / 12 | 0 / 84 (74) / 12 | 0 / 88 (76) / 12 | 0 / 83 (77) / 12 | 6.98 |
| tom | shoulder_yaw_servo_detail_11 (#11) | 1 | valid | - | 0 / 84 (74) / 12 | 0 / 84 (74) / 12 | 0 / 88 (76) / 12 | 0 / 83 (77) / 12 | 6.98 |
| tom | shoulder_pitch_servo_detail_10 (#32) | 1 | valid | - | 0 / 84 (74) / 12 | 0 / 84 (74) / 12 | 0 / 88 (76) / 12 | 0 / 83 (77) / 12 | 6.98 |
| tom | shoulder_pitch_servo_detail_11 (#33) | 1 | valid | - | 0 / 84 (74) / 12 | 0 / 84 (74) / 12 | 0 / 88 (76) / 12 | 0 / 83 (77) / 12 | 6.98 |
| tom | elbow_pitch_servo_detail_10 (#67) | 1 | valid | - | 0 / 84 (74) / 12 | 0 / 84 (74) / 12 | 0 / 88 (76) / 12 | 0 / 83 (77) / 12 | 6.98 |
| tom | elbow_pitch_servo_detail_11 (#68) | 1 | valid | - | 0 / 84 (74) / 12 | 0 / 84 (74) / 12 | 0 / 88 (76) / 12 | 0 / 83 (77) / 12 | 6.98 |
| tom | elbow_roll_servo_detail_10 (#85) | 1 | valid | - | 0 / 84 (74) / 12 | 0 / 84 (74) / 12 | 0 / 88 (76) / 12 | 0 / 83 (77) / 12 | 6.98 |
| tom | elbow_roll_servo_detail_11 (#86) | 1 | valid | - | 0 / 84 (74) / 12 | 0 / 84 (74) / 12 | 0 / 88 (76) / 12 | 0 / 83 (77) / 12 | 6.98 |
| tom | wrist_pitch_servo_detail_10 (#102) | 1 | valid | - | 0 / 84 (74) / 12 | 0 / 84 (74) / 12 | 0 / 88 (76) / 12 | 0 / 83 (77) / 12 | 6.98 |
| tom | wrist_pitch_servo_detail_11 (#103) | 1 | valid | - | 0 / 84 (74) / 12 | 0 / 84 (74) / 12 | 0 / 88 (76) / 12 | 0 / 83 (77) / 12 | 6.98 |
| w16 | charge_coupler:1_2 (#664) | 1 | valid | - | 0 / 8 (8) / 0 | 0 / 8 (8) / 0 | 0 / 12 (12) / 0 | 0 / 0 (0) / 0 | 17.3 |
| w16 | charge_coupler:2_1 (#713) | 1 | valid | - | 0 / 8 (8) / 0 | 1 / 43 (21) / 0 | 1 / 43 (24) / 0 | 0 / 0 (0) / 0 | 37.7 |
| w16 | charge_coupler:2_2 (#716) | 1 | valid | - | 0 / 8 (8) / 0 | 1 / 43 (21) / 0 | 1 / 43 (24) / 0 | 0 / 0 (0) / 0 | 37.7 |
| w16 | water_pump_housing:1 (#741) | 1 | valid | - | 0 / 4 (4) / 9 | 0 / 4 (4) / 9 | 0 / 4 (4) / 9 | 0 / 4 (4) / 9 | 1.5 |

## 4b. Deviation metric cross-check (first measurement pass used the gradient projection alone)

Per face, 150 sampled triangles: max ratio by bounded gradient projection, bounded tree projection, Newton from the triangle's mean (u,v), and |S(mean uv) - centroid|. Node-on-surface error is |S(uv_node) - node| / linear over all nodes.

| part | face | surface | grad | tree | newton (min) | S(mean uv) | node error | edge tol / linear |
|---|---|---|---|---|---|---|---|---|
| arb_bar:centre (#610) | 5 | BSplineSurface | 5.79 | 8.24 | 0.58 | 1.13 | 1.6e-05 | 4.0e-05 |
| exhaust_primary:rear_right_2 (#780) | 1 | BSplineSurface | 5.16 | 25.64 | 5.16 | 7.57 | 1.5e-10 | 6.8e-08 |
| rocker:left (#537) | 1 | BSplineSurface | 7.83 | 0.68 | 0.68 | 1.76 | 1.0e-06 | 6.1e-06 |
| balance_cock (#67) | 1 | BSplineSurface | 38.79 | 0.54 | 0.37 | 0.59 | 8.5e-07 | 4.3e-06 |
| prop:hub_rear (#267) | 1 | SurfaceOfRevolution | 0.28 | 0.28 | 0.28 | 0.41 | 1.0e-05 | 6.8e-06 |
