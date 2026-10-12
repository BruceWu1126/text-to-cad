# Shaking (`harmonic`)

"What happens on a shaker, or across a motor's speed range": the steady response to a sine shake
swept across a frequency range, shaken where it is held or pushed by forces that swing. It reports
the worst stress, displacement and acceleration over the sweep and the frequencies where they peak.

## When to use it

- "It goes on a shaker table at 1 g from 10 to 2000 Hz: does it survive?" A base shake along the
  table's axis with a stress check.
- "The motor runs from 1500 to 6000 rpm and has some out-of-balance: how much does the bracket
  move?" A force shake with the out-of-balance force as a load, swept over 25–100 Hz.
- "Does the sensor on this face see more than 10 g?" An acceleration check on that face.
- Use `modal` first when the question is only "where does it ring", `random_vibration` when the
  spec is a PSD in g²/Hz, and `shock` when it is a shock response spectrum.

## Study

Keys beyond the [shared ones](study-file.md#shared-by-every-analysis); reads density:

- `fixtures`: required (`fixed` faces). A base shake moves them.
- `excitation`, one of:
  - `{"type": "base", "direction": [0, 0, 1], "amplitude_g": 1}`: every fixture moves together
    along `direction` (any non-zero vector; normalised) with an acceleration amplitude of
    `amplitude_g` (default 1). A base shake takes no `loads`.
  - `{"type": "force"}`: the `loads` (`force` and `pressure` on faces) swing at each frequency with
    the size written. Body loads (`gravity`, `acceleration`) are not taken; shake the base instead.
- `sweep_Hz`: `[low, high]` in Hz, required.
- `damping_ratio`: modal damping as a share of critical, default 0.02 (2 %). Bolted metal
  structures sit around 0.01–0.05, plastics and rubber-mounted parts higher. The peak response is
  about 1/(2 × damping_ratio) times the static one, so this number matters: say what you assumed.
- Checks, each saying the frequency where it peaks (choosing it in the viewer jumps the Frequency
  scrubber there):
  - `stress` (the default when the view names none): the peak von Mises over the sweep against
    yield, judged with the study's `margin`.
  - `displacement` (`limit_mm`, optional `faces`): the largest motion relative to the fixtures.
  - `acceleration` (`limit_g`, optional `faces`): the largest absolute acceleration (the base's own
    shake included). Close at 90 % of the limit. Row line: "Peak 12 g, limit 10 g".
- View drives: `frame` (the Frequency scrubber), `deformation`, `field` (`von_mises`,
  `displacement`), `load_scale` (k times this shake), `threshold`. With no view it opens on the
  Frequency scrubber (at the peak) and the deformation.

```json
{"analysis": "harmonic", "material": "steel",
 "fixtures": [{"faces": ["#o1.f1"]}],
 "excitation": {"type": "base", "direction": [0, 0, 1], "amplitude_g": 1},
 "sweep_Hz": [10, 2000], "damping_ratio": 0.02,
 "view": {"checks": [
   {"kind": "stress"},
   {"kind": "acceleration", "limit_g": 10, "faces": ["#o1.f2"], "label": "Sensor"}]}}
```

A force shake:

```json
{"analysis": "harmonic", "material": "aluminum-6061-t6",
 "fixtures": [{"faces": ["#o1.f1"]}],
 "excitation": {"type": "force"},
 "loads": [{"faces": ["#o1.f7"], "type": "force", "vector_N": [0, 0, 5]}],
 "sweep_Hz": [25, 100],
 "view": {"checks": [{"kind": "displacement", "limit_mm": 0.2, "faces": ["#o1.f7"]}]}}
```

## What it writes

- `summary`: `excitation`, `sweep_Hz`, `damping_ratio`, `peak_Hz` (where the stress peaks, the
  frame the viewer opens on), `max_von_mises_MPa` (with its `_Hz` and `_at_mm`), `yield_MPa`,
  `safety_factor`, `max_displacement_mm`, `max_acceleration_g` (each with the frequency it peaks
  at), `modes` (each mode's frequency, its effective mass fraction along X, Y, Z and whether it was
  `used`), `modes_used`, `modes_up_to_Hz`, `effective_mass_fraction` (the share of the mass the
  modes move along the shake, `effective_mass_axis`), `total_mass_kg`, `frames`, `checks`.
- Sidecar curves: `max_displacement_mm` and `max_acceleration_g` against frequency over the whole
  sweep (a fine log grid with extra points around every resonance).
- The GLB: at most 24 frequency frames, the response peaks (up to 6) plus log-spaced frequencies
  across the sweep, labelled "490 Hz". Each carries von Mises at its peak over the cycle, the
  displacement's real part and its imaginary part (`_DISPLACEMENT_IM_F<i>`). The Viewer's routine,
  Vibrate, turns the chosen frame through one cycle.
- Findings: a failing stress or acceleration check ("The checked faces shake at 39.1 g at 490 Hz,
  more than the 10 g allowed"); `resonance` (info) says where it resonates and how much it moves and
  shakes there; `no_resonance_in_sweep` (info) when no natural frequency lies in the sweep;
  `modes_missing_mass` (warning) when the modes used move under 80 % of the mass along the shake.

## Hand check

A cantilever of length L shaken at its base along a bending direction with acceleration a (mm/s²,
1 g = 9806.65), first mode ω1 = 2π f1 (f1 from [modal.md](modal.md#hand-check)), damping ratio ζ:
at f1 the tip moves relative to the base by about 1.566 · a / ω1² · 1/(2ζ), and its acceleration
is about ω1² times that; far below f1 it follows the base and bends as under a steady load of a:
tip w = q L⁴ / (8 E I), q = ρ g A.

A 100 mm steel cantilever, 6 x 6 mm, shaken 1 g along Z with ζ = 0.02: f1 ≈ 490 Hz, tip
≈ 0.041 mm and ≈ 39 g at resonance, 0.0016 mm at 10 Hz.

For a force F at the tip: static deflection F L³ / (3 E I), times about 1/(2ζ) at f1. If a
reported peak is far from 1/(2ζ) times the static answer with one mode in the sweep, check the
damping and the fixtures.

## Limits

- Linear and steady state: every frequency is held long enough for the part to settle (a slow
  sweep). A fast sweep through a resonance peaks lower.
- Modal damping, one ratio for every mode. Real damping varies with the mode and the joints; the
  peak scales as 1/ζ.
- Modes up to 1.5 × the sweep's top. Mass that moves only in higher modes (and the mass on a fixed
  face, which never moves) is left out: `modes_missing_mass` says when this is over 20 %. Near a
  resonance the response is barely affected; far below the first mode, expect a few percent low.
- A real mount, softer than a fixed face, resonates lower.
- The displacement is relative to the fixtures; the acceleration is absolute. Von Mises at its
  peak over the cycle is sampled at 12 phases (at most about 1 % low).
- Stress is projected onto the nodes from each mode's stress, which smooths sharp corners a little
  more than the static solve does.

## Adapted to fit

Ladder ([shared rungs](study-file.md#shared-by-every-analysis)), in order:

- `reduce_modes` (first): keeps only the fewest modes holding 90 % of the mass moving along the
  shake (for a force shake, of the forces' static response), and says how many and the share:
  "Kept 3 of the 8 modes up to 3000 Hz, the ones holding 92% of the mass moving along Z, to fit".
  The search for modes also stops once that share is reached. Modes that do not move along the
  shake barely change the answer.
- `iterative`: the modes found with LOBPCG and a multigrid preconditioner. No accuracy cost.
- `local_refine` (around where the sweep stresses the part most), `defeature`, `linear_elements`.
- `symmetry` is declared but not taken yet: a symmetric half needs the antisymmetric modes too.
