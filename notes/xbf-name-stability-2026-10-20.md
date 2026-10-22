# xbf element-name survival measurement (engine @ 64517d1d5, 2026-10-20)

Measured by an Opus/medium agent (scripts and raw snapshots in the recovery session's scratchpad `names/`); the agent could not write its report file, so this is its report as relayed, verified against `names/snaps/<model>/compare.json`.

## Result

Every element name survived every requested dimension edit, faces and edges alike; no part was renamed without a geometry change; reverts and no-op rebuilds restored every key and name.

| edit | parts | key changed | renamed with key unchanged | faces kept | edges kept |
|---|---|---|---|---|---|
| juno KNEE_Z −290 → −291 | 363 | 6 (knee forks, knee bosses) | 0 | 3707/3707 | 7980/7980 |
| hypercar LOWER_ARM 22 → 23 | 987 | 2 (hinge_mount_lower L/R) | 0 | 7365/7365 | 16139/16139 |
| hypercar SHUT_GAP 5.0 → 5.5 | 987 | 16 body panels | 0 | 7365/7365 | 16139/16139 |
| tom pattern_shift_x −2.0 → −2.1 | 605 | 2 (elbow plate pair, shoulder yoke) | 0 | 13677/13677 | 39383/39383 |

## Coverage (what the 100% is measured over)

- juno: 100% of faces and edges named.
- hypercar: 89% — the 72 unnamed parts are every wheel part: `lib/wheels.py:_bake` places them with a raw `BRepBuilderAPI_Transform` (wheels.py:741), which the trace never sees.
- tom: 20% of faces, 21% of edges — 420 prototypes (about 55k faces) are imported vendor parts; `read_step` gives their faces no names. The two parts tom's edit changes are mostly unnamed (25/188 and 0/262 faces).

## Fresh-process builds

- Names and keys identical after every revert.
- xbf bytes differ between a cold and a warm build by the TShape `Checked` flag (0111000 vs 0101000) on 61/363 juno and 132/987 hypercar prototypes — not a naming fault; being fixed by the determinism agent.
- A second cold build in a fresh process: juno byte-identical; hypercar and tom not — geometry keys moved on 3 parts (hypercar `details/mirror_housing:right`; tom `elbow_roll_link_plate_pair`, `servo6_fixed_gripper_frame`), names identical. Likely OCCT parallel-boolean nondeterminism; handed to the determinism agent to diagnose and cost.

## Topology-changing edits (juno thigh, a 4 mm through-hole added two ways, then reverted)

- Appended after the chamfer, left side: FreeCAD-grade — exactly the 5 faces the hole cuts are renamed, each keeping its derivation plus the new step.
- Inserted before the relief cut: RENUMBERING FAULT — feature names count calls within a function (`<function>.<op><n>`), so every later cut/cylinder in the function is renumbered although its call, geometry and inputs are unchanged (6 of 9 lost faces, 13 of 14 lost edges). FreeCAD keeps a feature's name when another is inserted before it.
- Appended, mirrored side: LABEL FAULT — the author's label `knee_fork` names "the last feature that made the labelled shape", so it moved from the relief cut to the appended cut and renamed 6 untouched mirrored faces.
- Mirrored names fold to `_mirror_children.mirror1~<digest>`: any upstream change shows only as a new opaque digest.

## Imported parts

- `read_step` parts get no face names; part names are the file's (`NAUO4`, `SMA-5401`, or `=>[0:1:1:192]` when the file has none).
- Imported shapes that pass through a feature get brittle names: an element ordinal across the WHOLE file (`f:shoulder_pitch_servo.377778`), or a placement claiming the unnamed input's elements with placement-wide ordinals (`f:visit.moved247.<n>`, 151 tom parts): any upstream change renumbers them.

## Proposed fixes (not implemented; `kernel/trace.py` is owned by the per-symbol agent this round)

1. Feature identity independent of call count within a function (fixes the insert renumbering). Options need a decision: call-site line (fragile to line shifts), digest of the call's source text (renames when a parameter changes), or the assignment target / author label as the feature's name with a positional fallback (closest to FreeCAD's stored feature names).
2. Pin a label to the feature it first named, or use it as a prefix for the part's names, never "the last feature".
3. Trace raw OCP transforms and copies (`BRepBuilderAPI_Transform`, `GTransform`, `Copy`) as features; fallback: an unnamed part whose topology maps one-to-one onto a named shape inherits its names by ordinal.
4. Stable names for imported elements: ordinal within the element's own product, never across the file; a placement or compound never claims an unnamed input's elements; optionally read the file's own sub-shape names (`read.stepcaf.subshapes.name`).
5. Normalise the `Checked` flag before saving (with the determinism agent).
