# Logo CAD sources

`models/branding` ported to `cadgen.engine`: the brand's block-letter logos as
solids, each a plain model with the STEP it delivers.

| Model | Writes | Solids |
| --- | --- | --- |
| `logo_c.py` | `XBF/logo_c.xbf`, `XBF/logo_c.step` | 1 |
| `logo_cad.py` | `XBF/logo_cad.xbf`, `XBF/logo_cad.step` | 3 |
| `logo_texttocad.py` | `XBF/logo_texttocad.xbf`, `XBF/logo_texttocad.step` | 9 |

Build from this folder, in any order:

```
python logo_c.py
python logo_cad.py
python logo_texttocad.py
```

Each block is 10 mm. Every letter is 30 mm wide, 40 mm tall and extruded 20 mm in
+Z. The letter fronts lie in XY with the bottom at Y=0; consecutive letters have a
5 mm gap. Letters are separate named blue solids, with through-openings in A, D and
O. TEXTTOCAD's "TO" is the lighter blue. There is no background plate or support
geometry.

`profiles.json` is the vector generator's exact front outlines, which
`scripts/brand/generate-logos.mjs` writes here. The models read it through
`lib/logo_shape.py`, so a change to it rebuilds the logos.

The port: `from cadgen import step` -> `from cadgen.engine import step, xbf`, each
model declaring its xbf and the STEP it delivers, both in `XBF/`; cadgen's
build123d proxy -> `import build123d as bd`; `cadgen.srgb` -> `lib/color.py`'s
`srgb`. The `__main__` calls go: running a script builds its model.
