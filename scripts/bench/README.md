# Benchmarks

Manual commands that measure cadgen and the CAD Viewer. None of them runs in
CI, and what they write goes under an ignored `tmp/` directory, never into the
tree (`AGENTS.md`). [`viewer-memory/`](viewer-memory/) measures viewer loading
and lifecycle (its helper units run in `scripts/test/test-js.sh`), and
`occt_parity.py` how alike the viewer and the engine's OCCT prototype draw an
`.xbf`'s regular display modes.

## Viewer against OCCT, mode by mode

```sh
PYTHONPATH=packages/cadgen/src .venv/bin/python scripts/bench/occt_parity.py \
  --doc motorbike=models-xbf/motorbike/XBF/motorbike.xbf --out tmp/bench/occt-parity/now
```

Each `--doc NAME=PATH` (an `.xbf`, built first) is drawn in Solid, Shaded,
Parts, X-ray, Hidden line and Wireframe at iso and front (`--modes`,
`--cameras`, `--size`) twice: by the OCCT prototype, whose look is the
reference (`cadgen/engine/render/look.py`), and by the snapshot page, from the
engine daemon's meshes (`CADGEN_ENGINE_DIR` picks the daemon). Per picture pair
it prints the silhouettes' IoU, the median and 95th-percentile CIE76 colour
difference over the pixels both give the model, and, for the line modes, the
share of inked pixels that differ visibly; and it writes each picture,
`metrics.json`, `table.json` and a contact sheet per document (viewer, OCCT and
the difference, 1800 px wide). Its hue check reads the metal faces that face up
(normal within 25 degrees of +Z, metallic 0.5 or more, the authored colour's
chroma 10 or more) in each Solid iso picture and prints, per renderer, how far
their CIELab hue turns from the authored colour's and the share of its chroma
they keep: a washed-out top face is a hue near 0 at a chroma ratio well under 1.
`--preview` adds Preview at each camera, OCCT's as `cadgen snapshot --preview`
draws it and the page's with the job's `"preview": true`; its studio is no flat
backdrop, so its colour difference is measured where OCCT's picture leaves its own
studio drawn without the model, and its IoU is n/a. `--only chromium` keeps the OCCT pictures already there while the viewer side is
tuned. macOS only: the prototype's GL context is.
