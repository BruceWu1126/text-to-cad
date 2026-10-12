# usd-brep-demo models

EXPERIMENTAL. Parts modeled with NVIDIA's [usd-brep](https://github.com/NVIDIA-Omniverse/usd-brep)
(the SMLib NURBS kernel) instead of build123d, then handed to cadgen as exact
OpenCascade B-rep by `cadgen.usd_brep_bridge.to_build123d`. Everything after
the model function (the store, the STEP writer, SURF extraction, GLB, snapshots,
the CAD Viewer) is the ordinary OpenCascade pipeline.

usd-brep is not a cadgen dependency: its wheel is CPython 3.12 on Linux or
Windows only, so these scripts need an environment that has both, for example
from the repository root:

```bash
uv run --python 3.12 --with usd-brep==1.0.0 --with-editable packages/cadgen \
  python models/usd-brep-demo/src/hub_flange_g2.py
```

| Script | Artifact | Description |
|--------|----------|-------------|
| `circular_flange_ub.py` | `STEP/circular_flange_ub.step` | `examples/src/circular_flange.py` line for line in usd-brep; same solid (volume equal to 1e-9) |
| `hub_flange_g2.py` | `STEP/hub_flange_g2.step + GLB/hub_flange_g2.glb` | Hub flange whose hub-to-plate blend is curvature-continuous (G2), which build123d's circular fillets cannot make |

`verify_bridge.py` (beside `src/`, not a model) converts a dozen representative
usd-brep solids and checks each against usd-brep's own geometry: OpenCascade
validity, volume, face count and surface deviation.

## What the bridge does and does not carry

- Exact geometry and topology: planes, cylinders, cones, spheres, tori, NURBS;
  seams, poles and trims; circles and lines are recognized as such.
- Solid regions only (sheet bodies and wire edges are dropped), one Brep per call.
- Fillet and blend faces stay NURBS (OpenCascade writes tori for the same
  rounds), so the STEP is larger, and OpenCascade's default-precision volume of a
  NURBS face is off by ~1e-4 relative; pass an accuracy to `VolumeProperties`.
- Names, colors and materials are set on the returned build123d shape, as usual.
