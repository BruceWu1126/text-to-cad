# Composite (`composite`, lite)

A flat laminated part: plies of a unidirectional ply material (a lamina) stacked at angles.
**Lite; its limits (in every result):** linear (small deflections, every ply elastic up to failure),
no delamination and no progressive damage (the answer is the first ply to fail), and classical
laminate theory's assumptions (plies perfectly bonded, each in plane stress: through-thickness
stresses are not judged).

## When to use it

- "Will this carbon or glass fibre plate hold", "which ply fails first", "how far does the panel
  bend", "is [0/90]s enough or do I need ±45s": a flat panel, skin, plate, lid or bracket plate laid
  up from carbon/epoxy, glass/epoxy or aramid plies, with a known layup (or ask: angles, ply
  thickness, ply material).
- Not for: a curved shell, ribs, steps in thickness or bosses (the part must be a flat plate of even
  thickness, or the study is refused with a sentence saying so); a foam or honeycomb sandwich core
  (no core model); delamination, impact damage, buckling of the laminate, or anything after the
  first ply fails. A solid block of one directional material (wood, a printed part treated as
  orthotropic, a single crystal) is not a layup: give `static` or `modal` an
  [orthotropic material](materials.md#orthotropic-materials).

## The study

Keys beyond the [shared ones](study-file.md#shared-by-every-analysis), with no `material` (the
plies carry their own):

- `fixtures` and `loads` ([static](linear-static.md)); a body load needs every ply material's
  `density_t_per_mm3`. A skin face's load acts on the plate, a side face's on its edge; a fixed side
  face clamps that edge.
- `laminae`: the ply materials by name, each with `E1_MPa` (along the fibres), `E2_MPa` (across),
  `nu12`, `G12_MPa` and five positive strengths: `Xt_MPa`, `Xc_MPa` (along, tension and
  compression), `Yt_MPa`, `Yc_MPa` (across) and `S_MPa` (in-plane shear). Optional: `G13_MPa`
  (default G12), `G23_MPa` (default E2 / (2 (1 + nu23))), `nu23` (default nu12),
  `density_t_per_mm3`, `name`; each default assumes transverse isotropy, listed in a
  `lamina_assumed` finding. Take every number from the prepreg's datasheet; never make them up.
- `layup`: the plies **from the bottom face up**, each `{"material", "angle_deg", "thickness_mm"}`.
  `material` names a lamina in `laminae`, or is a lamina object itself. The plies' thicknesses must
  add up to the plate's thickness (within 2 %).
  - The bottom face is the one the laminate's normal (the plate's, turned so its largest component
    is positive) points away from: for a plate in XY, ply 1 is on the −Z face. A symmetric layup's
    order does not matter.
  - `angle_deg` (−180 to 180) is measured from the 0° direction, turning toward 90° (the normal
    × 0°): for a plate in XY with the default axis, +45° points between +X and +Y.
- `layup_axis` (optional): the 0° direction as `[x, y, z]`, laid into the plate. Default: global X
  (global Y for a plate that faces X).
- Checks: `ply_failure`, the worst ply's failure index against 1 (optional `criterion`
  `"tsai_wu"` or `"max_stress"`; none: the larger), close past 0.9, row "Worst ply 3 (+45°), failure
  index 0.82", titles "A ply fails" / "Close to failing" / "Every ply holds"; the default with no
  `view.checks`. `displacement`: `limit_mm`, optional `faces` (as static). No `stress` check: a
  laminate has no single yield strength; the failure index is its strength check.
- View drives `field` (`failure_index`, `von_mises`, `displacement`), `deformation`, `threshold`;
  with no view, the field and the deformation. No `load_scale` (Tsai-Wu does not scale with load).

A [0/90]s carbon/epoxy panel (T300/5208, Tsai and Hahn's textbook lamina) clamped on two edges:

```json
{"analysis": "composite",
 "laminae": {"t300": {"E1_MPa": 181000, "E2_MPa": 10300, "nu12": 0.28, "G12_MPa": 7170,
                      "Xt_MPa": 1500, "Xc_MPa": 1500, "Yt_MPa": 40, "Yc_MPa": 246, "S_MPa": 68,
                      "density_t_per_mm3": 1.6e-9}},
 "layup": [{"material": "t300", "angle_deg": 0,  "thickness_mm": 0.25},
           {"material": "t300", "angle_deg": 90, "thickness_mm": 0.25},
           {"material": "t300", "angle_deg": 90, "thickness_mm": 0.25},
           {"material": "t300", "angle_deg": 0,  "thickness_mm": 0.25}],
 "fixtures": [{"faces": ["#o1.f1", "#o1.f2"]}],
 "loads": [{"faces": ["#o1.f6"], "type": "pressure", "pressure_MPa": 0.001}]}
```

## How it solves

- **Thin plate** (thickness under 5 % of its smaller span): its mid-surface as a Reissner-Mindlin
  shell (the same MITC3 triangles as static's shell idealisation), with classical laminate
  theory's stiffness: A, B and D from the plies (Q̄ = T(θ)ᵀ Q T(θ) per ply, A = Σ Q̄ t,
  B = Σ Q̄ (z₂² − z₁²)/2, D = Σ Q̄ (z₂³ − z₁³)/3) and the plies' transverse shear (5/6 Σ G t), the
  fields mapped onto the real part. The method, not a ladder step: `"method": "shell"` and a
  `composite_method` finding.
- **Thick plate**: meshed as a solid, every point taking its ply's 3D orthotropic stiffness by its
  height. Ply interfaces need not follow the elements: keep the element size near the ply
  thickness for an answer that resolves each ply (`mesh.size_mm`).
- Each ply's in-plane stresses in its own fibre axes (σ1, σ2, τ12) are judged at its top and bottom
  (thick: at every integration point inside it):
  - Tsai-Wu index F1 σ1 + F2 σ2 + F11 σ1² + F22 σ2² + F66 τ12² + 2 F12 σ1 σ2, with F1 = 1/Xt − 1/Xc,
    F2 = 1/Yt − 1/Yc, F11 = 1/(Xt Xc), F22 = 1/(Yt Yc), F66 = 1/S², F12 = −½ √(F11 F22);
  - max-stress index, the largest of σ1/Xt (or −σ1/Xc), σ2/Yt (or −σ2/Yc) and |τ12|/S.
- Fields: `failure_index` (the governing criterion, the larger of Tsai-Wu and max stress, enveloped
  over the plies and drawn unsmoothed: each point shows the worst of the elements around it, so the
  colour bar tops out at the `ply_failure` check's own value), `von_mises` (the plies' in-plane von
  Mises, enveloped), `displacement`.
- Summary: `method`, the `layup` (plies, `notation` such as "[0/90]s", thickness),
  `max_failure_index`, `worst_ply` (number, angle, material, criterion, both indices, its σ1, σ2,
  τ12 and where), every ply's maxima under `plies`, and for the shell A, B and D under `detail`.

## How to judge the answer (hand checks)

- **ABD.** For [0/90]s with plies of thickness t0 (h = 4 t0): A11 = A22 = (Q11 + Q22)/2 · h,
  A12 = Q12 h, A66 = Q66 h, B = 0, D11 = (7 Q11 + Q22)/8 · h³/12, D22 = (Q11 + 7 Q22)/8 · h³/12,
  D12 = Q12 h³/12, D66 = Q66 h³/12, with Q11 = E1/(1 − ν12ν21), Q22 = E2/(1 − ν12ν21),
  Q12 = ν12 Q22, Q66 = G12. Compare `summary.detail.D`.
- **Deflection.** A long plate clamped along its two long edges a apart bends like a clamped-clamped
  beam per unit width: centre w = q a⁴ / (384 D11); transverse shear adds about q a² / (8 · 5/6 ·
  Σ G13 t), small past a span of about 50 thicknesses. No simply supported edges (fixtures clamp).
- **One ply.** Tsai-Wu by hand on the worst ply's σ1, σ2, τ12 must give `worst_ply.tsai_wu`.

## Limits

- Small strain and deflection: a thin plate deflecting more than about its thickness is stiffer in
  reality (membrane action). CLT transverse shear by a single 5/6 factor. Flat plates of even
  thickness only; one laminate per part; no assemblies.
- The ply-by-ply failure index does not scale with the load (Tsai-Wu is quadratic): to find the
  load at failure, run again at another load, or read the max-stress index, which is linear.

## Ladder (when it is big)

The thin path is already small: it takes no rung. The thick (solid) path takes, in order
([shared rungs](study-file.md#shared-by-every-analysis)): `iterative` (AMG + CG, or matrix-free CG,
on the layered stiffness); `local_refine` (around the failure-index peak, its words quoting how far
that peak moved); `symmetry`, only when every ply is also its own mirror image about the plane (0°
and 90° plies about planes along their axes; a ±45° ply is not, so a ±45 layup is never cut).
