# Manual validation models

`models/tests` ported to `cadgen.engine`: the three cases its README lists. **CI
tests must never read, import, build, or depend on anything here**, as for
`models/tests`: automated tests make their own small fixtures in fresh temporary
directories.

Agents may use these models for manual browser reviews, reproducing edge cases,
checking exports and debugging new features. They are small and quick to rebuild.
Each case builds into an `XBF/` beside its script (gitignored).

## Cases

| Folder | Model | Manual checks |
| --- | --- | --- |
| `render_floor/` | `floor_positions` | Three coloured blocks above, across and below Z=0. Render's floor stays at the authored origin; aligning to the lowest point is an explicit option. |
| `viewer_concurrency/` | `assembly` | A 32-part drilled-plate assembly. Change `REVISION` and rebuild while the viewer is loading to check update handoffs, progress and replacement geometry. |
| `unified_annotations/` | `annotation_fixture` | An 11-part assembly: brushed steel on the base and shaft (each keeping its own colour), a blue paint on the arm (its own colour green at alpha 0.6; the paint's blue at opacity 0.5 is what shows), the arm's `swing` joint and the shaft's `lift`, a configuration (`open`: swing 45) and a motion (`demo`: the shaft lifting 8 mm and back over 4 s). Check materials, Position, motion in both view modes, a motion-only edit (the mechanism rewritten, no prototype converted), snapshots and the GLB clip. |

## Run manually

From each case's folder, with the engine's environment (`cadgen` on the path):

```bash
# Keep manual runs on a daemon of their own.
export CADGEN_ENGINE_DIR="$(mktemp -d /tmp/cadgen-manual-engine.XXXXXX)"
python source.py                     # render_floor/, viewer_concurrency/
python annotation_fixture.py         # unified_annotations/
python -m cadgen.engine snapshot XBF/annotation_fixture.xbf /tmp/open.png --pose open
python -m cadgen.engine export XBF/annotation_fixture.xbf /tmp/annotations.glb   # demo, baked as a clip
```

## The port

- `from cadgen import step` -> `from cadgen.engine import xbf`; each case writes its xbf
  where its STEP was written, in `XBF/` beside the script.
- `unified_annotations`: the sidecar's `materials=` are `Material` values on the parts
  (steel the catalogue's, in the old finish, `color=None`; the paint its own, with its
  colour); the `swing` mate is a build123d revolute joint and the `open` pose a
  configuration; the clip, which moved the shaft by hand, is a motion of a new prismatic
  joint, `lift` (-8 to 8 mm). The old viewer multiplied the paint's opacity into the
  arm's (0.3); a part with a material wears the material's colour, so it shows 0.5.

## Not ported

- `joint_playground/joint_playground.urdf`: an authored URDF of primitives, no model,
  and no doc names it. It stays in `models/tests`, and the engine's snapshot draws it as
  it is (`python -m cadgen.engine snapshot models/tests/joint_playground/joint_playground.urdf out.png`).
