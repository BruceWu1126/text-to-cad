# Benchmarks

Manual commands that measure cadgen and the CAD Viewer. None of them runs in CI:
what they write goes under an ignored `tmp/` folder, never into the tree
(`AGENTS.md`), and only their pure helpers are tested
(`tests/python/global/test_bench_helpers.py`). Performance numbers come from one
batch, run once every change it measures is in the branch, on a quiet machine:
numbers taken under other work's load, or by agents benching beside each other,
do not compare.

## Photographic Preview material review

`preview_render.mjs` drives the production FileViewer and GLB Preview renderer in
Chromium on a hardware WebGPU adapter. `preview_report.py` makes a contact sheet,
matched native-resolution centre/contact crops and supporting image differences.
The standing CAD fixtures are
[`models/tests/preview_studio`](../../models/tests/preview_studio/README.md).
They cover machined metal, a coated product with fasteners and a gauge, thick glass
with liquid and contents, and a mixed-material cavity. The existing
`models/moonwatch/src/finishing_sampler.py` supplies a fifth fine-detail case.

Build the shared exports, build/export the selected models with the development
CAD environment, then run on a quiet GPU. No images or model exports are committed:

```sh
npm run build:packages
.venv/bin/python models/tests/preview_studio/exports.py --out "$PWD/tmp/preview-corpus/glb"
node scripts/bench/preview_render.mjs --corpus tmp/preview-corpus/glb --out tmp/preview-corpus/review
.venv/bin/python scripts/bench/preview_report.py tmp/preview-corpus/review
```

The render command writes a raster studio baseline, the first fully denoised
production image, the converged production image and a raw 512-sample reference
for each GLB, with the same stored appearance, studio settings, camera and size
(1200×900 by default). The reference uses the controller's bounded constructor
diagnostic option; it does not rewrite production source or create a UI setting.
Controller, admission helpers, worker entrypoints and UI all consume the compiled
package exports. Mixing source and compiled copies can break module-local geometry
ownership markers and produce false admission failures.
`renders.json` records the actual camera/configuration, progress, long tasks and
timings. Retained rows are updated by file/profile when a run is resumed.

`--file name.glb` selects one case (repeatable). `--profiles raster,candidate` omits
the slower reference pass; `--profiles reference --reference-samples 512` adds it
later in the same output folder. `--quality preview|final` selects production
quality. `--transparent` uses a transparent backdrop and the studio's 0.6 floor
opacity to expose residual alpha grain; the report composites transparent pixels
over charcoal. This diagnostic saves the controller's unflattened foreground,
because the host screenshot API paints its CSS backdrop. Width is bounded
256–2400, height 256–1800, and raw references 1–1024
samples. Use only ignored `tmp/` or an external temporary output directory.

`--fit-rest` uses an explicit camera fitted to the loaded GLB's current geometry,
useful for a static beauty of a file whose automatic camera also covers a large
exploded-animation envelope. The chosen camera is recorded, and this does not
change production framing policy.

For a native engine/XBF check, first serve an existing document from an isolated
engine with the current Viewer build; do not rebuild a large source model just to
open a saved XBF. `cadgen serve /absolute/path/model.xbf` prints its authenticated
page URL. Then drive that actual page:

```sh
node scripts/bench/preview_render.mjs --native-url "$URL" \
  --out tmp/preview-corpus/native --quality preview --exercise-lifecycle
node scripts/bench/preview_render.mjs --native-url "$URL" \
  --out tmp/preview-corpus/native-pointer --quality preview --exercise-pointer
```

Native mode records first/final denoise progress and guide/network timing,
main-thread long tasks, the actual camera and stage, and screenshots. Lifecycle
mode plays/pauses a routine only when that saved document exposes one, then measures
exit and reentry. A missing routine is recorded rather than claimed as tested.
Pointer mode performs a modest wheel zoom and real orbit drag during refinement,
records camera response and visible canvas layers, then waits for resumed final
denoising and writes `native-beauty.png`. The default camera is retained as a
separate baseline. Fallback, denoising failure and exhausted sample budgets fail
the run; a large model's raster fallback is not a tracing success.

`preview_report.py FOLDER --scene NAME` selects technical comparison cases;
`--beauty-scene NAME` independently selects the clean two-column beauty sheet
(both repeatable). The report never invents a missing candidate image. Native
captures can be copied into the review folder as `NAME-candidate.png` for a beauty
sheet while retaining their separate native provenance JSON.

Review the full images and crops for highlight shape, smooth shading versus lost
detail, recess and contact lighting, glass thickness/refraction, alpha grain,
silhouettes and tiny features. A completed denoiser or a lower image error is not
a verdict of photorealism. Raw references retain Monte Carlo noise and are
convergence comparisons, not ground-truth photographs. This corpus and these
manual rendering measurements do not run in CI; the renderer's small functional
fixtures remain in its browser tests.

## The corpus batch

`corpus_bench.py` builds each model in `corpus.json` with the xbf engine, cold to
warm, and times every step:

```sh
.venv/bin/python scripts/bench/corpus_bench.py --dry-run         # the config and the interpreter, checked
.venv/bin/python scripts/bench/corpus_bench.py --max-load 16     # every model; each step waits for load <= 16 and no other busy cadgen build
.venv/bin/python scripts/bench/corpus_bench.py --model l_bracket # one; repeat --model for more
```

A model in `corpus.json` names its project folder (`root`, relative to this repository),
working folder (`cwd`, within that project), import folders (`pythonpath`, within the
project), and `targets`: Python scripts relative to `cwd`. Each script runs with its
own `__main__` control flow in a separate client process. `document` selects a model
that those calls actually built, as `script.py::function` relative to `cwd`. It does
not execute that function. The selected report supplies the document's actual id;
missing, failed or ambiguous selections fail the step. `xbf` names a separate export
destination, independent of the selected function's name. `edit` is `{file, from, to}`,
with `from` occurring exactly once; `needs` lists required inputs. `note` describes the
case. `comparison_note` records a workload difference that prevents a build speedup
comparison with the recorded baseline.

The models, smallest first, are `l_bracket`, `planetary_gear`, `qdd`, `motorbike`,
`case`, `juno`, `movement`, `tom`, `hypercar` and `f1`. Tom stays in its own repository:
its default root is `../tom-cad`. A worktree or isolated clone may have no such sibling;
give `--config` a copy of `corpus.json` with the relative path to the external checkout
or copied checkout. `--dry-run` checks configuration, files, edits and the selected
interpreter's cadgen import (`--cadgen-src`), without building anything. It does not
infer calls from source or promise that a script calls the selected model.

Each project is copied into a fresh output folder. Source stays unchanged except for
the configured edit and its revert. Local Git metadata, virtual environments,
`node_modules`, scratch directories and XBF/DXF outputs are excluded at every level;
pinned inputs such as `STEP/imported` are retained. A symlink is refused, so a copied
edit or output cannot write through it into the original checkout. Files use APFS
clones where available. An existing output folder is refused.

Every model gets private engine, state, cache and launcher directories. The door is
under a fresh batch folder in `--engine-dir`; the other three directories are under
`<out>/runtime/<model>`. The engine uses a free port, no standby worker, telemetry off,
and a browser stub that records any attempted launch in `<out>/browser-requests.log`.
The batch never stops another batch's engine or clears its cache. Steps are:

- `cold`: a fresh daemon and empty private document cache;
- `noop`: the unchanged script, on the same daemon;
- `edit`, `revert`: the configured edit, then the file's original bytes;
- `restart`: stop successfully, then run with the private document cache retained;
- `restart-cold`: stop successfully, remove only that private cache's `documents/`,
  then run again. Bytecode stays warm; declared or exported files are never cache inputs;
- `export`: explicitly export the selected document id to `xbf`, recording bytes and
  translation time separately from build walls;
- `serve`, `mesh-cold`, `mesh-warm`, `structure`: serve that model id, mesh every part
  (`POST /api/doc/<id>/mesh`), mesh again after dropping those meshes on the now-warm
  pool, then read the page's structure. No file is substituted for the model document;
- `snapshot-first`, `snapshot`: draw that document id twice with `cadgen snapshot --json`.

The small measurement driver runs the target with Python's `runpy` and records reports
returned by `api.build_script` when the script's explicit model calls reach it. It
introduces no model call, decorator or export. A build wall covers interpreter startup,
the whole script and report capture; `wall_api` sums its calls' engine round trips.
Rows include timings, memo counts, bodies run/replayed/loaded, cache source/reason,
declared files and worker memory. HTTP and command result shapes are checked; a failed
step records its error and ends that model. Failure to stop preserves its working files.

The batch writes `tmp/bench/corpus-<time>/` (`--out` selects another fresh folder):
`rows.jsonl`, appended after every step; `table.md`, updated after each model; snapshots;
and private runtime state. The first row records the commit, interpreter, platform and
configuration. `--keep` retains source copies with the edit reverted. The normal exit
stops each private daemon. These are manual measurements, not CI tests; the global
suite tests the pure configuration, copy, selection, aggregation and comparison helpers.

## Viewer against OCCT, mode by mode

```sh
PYTHONPATH=packages/cadgen/src .venv/bin/python scripts/bench/occt_parity.py \
  --doc motorbike=models/motorbike/XBF/motorbike.xbf --out tmp/bench/occt-parity/now
```

`occt_parity.py` measures how alike the viewer and OCCT draw an `.xbf`'s regular
display modes. Each `--doc NAME=PATH` (an `.xbf`, built first) is drawn in Solid,
Shaded, Parts, X-ray, Hidden line and Wireframe at iso and front (`--modes`,
`--cameras`, `--size`) twice, both from the engine daemon's meshes
(`CADGEN_ENGINE_DIR` picks the daemon): by OCCT, whose look is the reference
(`cadgen/engine/render/look.py`) and which `cadgen snapshot` draws with; and by the
viewer's own scene code, `@text-to-cad/core`'s headless renderer, which the bench
builds at the start of each run (`scripts/bundle/lib/snapshot_runtime.sh`, into the
ignored `tmp/bench/snapshot-page`; the wheel does not carry it), then loads in a
headless Chromium and drives itself. Per picture pair it prints the silhouettes' IoU, the
median and 95th-percentile CIE76 colour difference over the pixels both give the
model, and, for the line modes, the share of inked pixels that differ visibly; and
it writes each picture, `metrics.json`, `table.json` and a contact sheet per
document (viewer, OCCT and the difference, 1800 px wide). Its hue check reads the
metal faces that face up (normal within 25 degrees of +Z, metallic 0.5 or more, the
authored colour's chroma 10 or more) in each Solid iso picture and prints, per
renderer, how far their CIELab hue turns from the authored colour's and the share of
its chroma they keep: a washed-out top face is a hue near 0 at a chroma ratio well
under 1. The bench's `--preview` adds its internal OCCT photographic rig at each camera
and the viewer's with its job's `"preview": true`; its studio is
no flat backdrop, so its colour difference is measured where OCCT's picture leaves
its own studio drawn without the model, and its IoU is n/a. `--only chromium` keeps
the OCCT pictures already there while the viewer's side is tuned (`--only occt`, the
viewer's). OCCT's offscreen drawing is proven on macOS; on Linux it needs an X
server (Xvfb on a machine with no screen).
