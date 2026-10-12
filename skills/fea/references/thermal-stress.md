# Heat stress (`thermal_stress`)

Stress and movement from the part's temperature, plus any mechanical loads. The steady
[`thermal`](thermal.md) solve runs first, on the same mesh; its temperatures drive the static solve.

## When to use it

- "It's bolted down at both ends and gets to 120 °C: does it buckle the brackets or crack", "how
  much does it grow", "will the hot side bow": a part held where it would grow (clamped, bolted to a
  cooler or a different metal), or heated unevenly. Free to grow and evenly heated, it has no stress.

## The study

Keys beyond the [shared ones](study-file.md#shared-by-every-analysis):

```json
{"analysis": "thermal_stress", "material": "steel",
 "fixtures": [{"faces": ["#o1.f1"]}, {"faces": ["#o1.f2"]}],
 "temperatures": [{"faces": ["#o1.f2"], "C": 120}],
 "convection": [{"faces": ["#o1.f3", "#o1.f4"], "h_W_m2K": 10, "ambient_C": 25}],
 "reference_C": 20,
 "view": {"checks": [{"kind": "stress"}, {"kind": "displacement", "limit_mm": 0.1}]}}
```

- The heat: `temperatures`, `heat`, `convection` and `radiation`, exactly as
  [`thermal`](thermal.md#the-study) (at least one held temperature, convection or radiation). A
  part that only [radiates](thermal.md#radiation) settles where it radiates what it takes in.
- `fixtures` (required), `loads` (optional, gravity and acceleration included) and checks `stress`
  and `displacement`, as [static](linear-static.md).
- `reference_C`: the stress-free temperature, as made or assembled (default 20 °C); the stress
  comes from the change from it, α (T - reference). Reads `conductivity_W_mK` and
  `expansion_per_K`, each part its own, so two metals bonded together bow as they would.

## What comes out

- All that static writes, plus the `temperature` field, summary `max_temperature_C`, `min_temperature_C`
  and `reference_C`, the thermal solve's summary under the sidecar's `upstream`, and an `info`
  finding `heated` (the temperature range against the stress-free one).
- A peak over 1.1× the field's 99th percentile (a clamped edge that cannot grow is singular in heat
  stress) caps the von Mises colours there so the rest still reads: the field carries `capped`
  (`quantile`, `peak`), the colour bar says "≥". On a clamped face (`singular_clamped_edge`) that
  peak is the mesh's, not the part's: the stress check and summary `safety_factor` judge the stress a
  little away from the edge, where the colours stop (`judged_away_from_edge_MPa`), the check keeps
  the edge's peak as `edge_peak`, and the viewer says so under Strength. Any other capped peak is
  judged as it is, with static's notes on peaks at a fixture and peaks that rise on a finer mesh.
- The viewer's Heat row scrubs the heat coming on (every temperature the same share of the way from
  the reference to the solved one, stress and deformation with it: a ramp of a linear result, said
  "ramped"), with presets Ambient and Heated; Preview's playbar plays it. The load control scales
  the temperature change and the mechanical loads together ("OK up to 1.6× this heat"), exactly,
  as both act linearly; 2× means twice the rise above `reference_C`.
- CLI: the stress and displacement lines, then "temperatures 25 to 120 °C, stress-free at 20 °C".

## Judging the answer (a hand check)

- Free to grow (held at one end only): ΔL = α L ΔT, no stress away from the fixed face; a 100 mm
  steel bar heated 100 °C grows 11e-6 × 100 × 100 = 0.11 mm (solver within 1 %).
- Clamped at both ends it cannot grow, so it is pressed by σ = -E α ΔT. Steel heated 100 °C:
  200 000 × 11e-6 × 100 = 220 MPa, near yield, from heat alone; the solver matches within 2 % in the
  middle, and the walls each push back with σ × area.
- Radiating: a part taking in Q and radiating it from area A settles at T = (Q / (ε σ A) + T∞⁴)^¼
  (kelvin); clamped it carries E α (T − reference). A 100 × 5 × 5 mm steel bar taking 1 W, radiating
  from every face at ε 0.9 into 20 °C, settles near 88 °C: clamped at both ends, about 150 MPa.
- A plate hot on one side and cool on the other bows toward the hot side; held flat, it carries
  about E α ΔT / (2 (1 - ν)) at its faces.
- A full `fixed` face also stops it growing sideways: read the stress a little away from it first.

## Limits

- One-way (the movement does not change the heat), linear and elastic, constant properties: E and
  α at room temperature, no softening when hot, no creep. A safety factor under 1 means it yields;
  how far is not modelled. Steady temperatures only: a warm-up's hottest moment is not computed yet.

## When the model is big

Static's [ladder](linear-static.md#when-the-model-is-big-the-static-ladder), after the thermal
solve's own (an iterative solver for the temperatures).
