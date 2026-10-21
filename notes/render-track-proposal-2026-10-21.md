# Cinematic renders: landscape, trials, proposal (2026-10-21) -- DRAFT

Status: DRAFT. Every planned render is done on the Mac (Metal numbers below); the exploration moves to a
cloud session with no GPU. See **State** at the end for what is not done and how to reproduce each output.

Goal: photoreal, cinematic stills and clips of our CAD models (hero stills, social mp4s), built from the
model data -- the engine's GLB (looks as glTF extensions, motions as CUBICSPLINE clips) -- not from the
viewer, the snapshot page or the OCCT stills prototype (that stays the documentation still). Machine: M1 Max,
32-core GPU, no hardware ray tracing (M3/M4 Max have it). Files: scratchpad `render-x/` (`out/` images and
mp4s, `scripts/` the code; a copy of the scripts in worktree `agent-render-x/tmp/render-x/`).

## Recommendation

**Blender Cycles, headless, driven by a renderer-neutral shot file: GLB in, graded PNG/mp4 out.** Its glTF
importer honours every look we export and imports `steer` as an action; Cycles on Metal + OIDN made the
best images, 25-52 s per 1080p hero and 5.7-8.2 s per 720p clip frame. No app is needed: the `bpy` wheel
(5.2.2, cp313 = cadgen's Python) ran the same script (bike hero 45.6 s on its first run vs 25.0 s warm in the app; not re-measured).
**Second: three.js + three-gpu-pathtracer (WebGPU) in headless Chromium**, which cadgen already ships:
glTF-native, MIT, images within a hair of Cycles in similar time (35-46 s per 1080p hero, 3.4 s per 720p
frame), and the same engine can be the viewer's interactive Render mode. EEVEE is the Blender route's
draft mode (1-1.3 s per 720p frame). Mitsuba is a reference renderer only: every feature is ours to build.

## Tried: same GLBs, shot files, lighting and one grade (Blender's OCIO config, AgX) for all

Shots (`shots.py`, a glTF-space camera per frame): movement hero 100 mm f/32 macro; bike hero 65 mm f/4,
low 3/4; movement rack-focus dolly (balance wheel -> column wheel, 100 mm f/16); bike 60-degree orbit with
`steer` playing (40 mm f/4). Light: procedural studio HDRIs (`make_env.py`: dark product set with rim
strips; car-commercial stage) and a glossy black floor. Wall time per frame, warm, denoise included.

| Candidate | Movement hero 1080p | Bike hero 1080p | 720p clip frame | First-run cost |
| --- | --- | --- | --- | --- |
| Blender 5.2 Cycles (Metal, OIDN on GPU) | 52.5 s @512 adaptive | 25.0 s @512 | rack 8.2 s, orbit 5.7 s (motion blur) @128 | Metal kernels compile once, ~220 s |
| Blender 5.2 EEVEE | 4.6 s @128 TAA | 4.1 s | rack 1.25 s, orbit 1.0 s @64 | shaders ~10 s |
| Mitsuba 3.9.1 metal_ad_rgb (+pyoidn) | 82.9 s @256 | 52.9 s @256 | orbit 5.45 s @64, grainy, no blur | JIT 1-2 s; recompiles per camera move unless its params are made opaque |
| three-gpu-pathtracer 0.0.27 WebGPU (+pyoidn) | 46.1 s @256 | 35.4 s @256 | orbit 3.4 s @64, clean, no blur | Chromium + BVH 1.6-2.2 s |

Outputs in `render-x/out/`: `compare_movement.png`, `compare_motorbike.png`, `compare_orbit_frame70.png`
(all four side by side); `blender/{movement,motorbike}_hero_{cycles,eevee}.png`; `blender/movement_rack_{cycles,eevee}.mp4`,
`blender/motorbike_orbit_{cycles,eevee}.mp4`; 9:16 crops `blender/*_cycles_9x16.mp4` and `.gif` loops;
`blender/movement_explode_cycles.mp4` (6.8 s per 720p frame @128); `blender/motorbike_daylight_cycles.png` (physical sky,
golden hour); `mitsuba/*_mitsuba.png`, `mitsuba/motorbike_orbit_mitsuba.mp4`; `three/*_three.png`,
`three/motorbike_orbit_three.mp4`.
Verdicts at a glance: Cycles is the reference (rubies, perlage, blued screws, contact shadows, floor
reflections); three.js is within a hair of it on both models; Mitsuba matches the lighting but stays grainy
on clear-coated paint and brushed brass; EEVEE is close on the bike and flatter on brass, and every set needs
tuning (its world-sun extraction cast the rim lights' shadow toward the camera; a planar probe mirrored the
wrong scene into the watch's bridges).

## Landscape ("looks" = our six extensions: ior, transmission, volume, clearcoat, anisotropy, emissive strength)

| Option | Ceiling | Speed, M1 Max / cloud | Install, licence | Looks, clips | Verdict |
| --- | --- | --- | --- | --- | --- |
| Blender Cycles 5.2 | product-viz/film path tracer, caustics, OIDN | above; an RTX 4090 ~10x (Open Data ~11-13k vs ~1k) | 907 MB app or 245 MB `bpy` wheel; GPL-3 | all six imported; clip -> action | **recommend** |
| Blender EEVEE 5.2 | raster + screen-space RT | 1-5 s per frame | same | same import; approximate glass | draft mode |
| three.js + three-gpu-pathtracer (WebGPU) | path tracer + MIS; no motion blur | above | a few MB of JS, MIT; Chromium (have it) | all six native (uses TANGENT); AnimationMixer | **second**, + viewer mode |
| Mitsuba 3.9 (metal_*) | research PT, spectral, polarised | above | 52 MB wheel, BSD-3 | no importer (ours); no anisotropy without UVs | reference |
| LuxCoreRender 2.11 | unbiased, bidirectional, best caustics, OIDN | no Metal backend: CPU, minutes | 84 MB `pyluxcore` wheel, Apache-2 | no importer | not tried |
| pbrt-v4 | spectral reference | CPU on Mac; GPU = OptiX only | source build, Apache-2 | no glTF | no |
| OCCT's own GLSL path tracer | old progressive PT, no denoiser | in-process (OCP) | nothing new, LGPL | XCAF looks only | low ceiling |
| RenderMan 27, Arnold | film grade | CPU on Mac; their GPU modes NVIDIA-only | non-commercial / Autodesk licences | via USD | licences rule out |
| KeyShot 2026 | the CAD-product incumbent | GUI app, cloud credits | $1,299/yr | STEP, glTF | reference look only |
| Unreal 5 (MRQ, Lumen, PT) | best cinematic tooling | its path tracer needs DXR: not on Mac | 50-100 GB, EULA | Interchange glTF | too heavy |
| Unity HDRP, Godot 4 | real time (Godot not photoreal) | HDRP's PT Windows-only | seats / MIT | partial KHR | no |
| USD/Hydra (usd-core 26.11) | interchange, not a renderer | the wheel has no imaging (no Storm, no usdrecord) | TOST | -- | only for many renderers |
| RealityKit, SceneKit, Metal RT | real-time PBR | -- | system | USDZ only | no |
| Cloud GPU: RunPod 4090 $0.34/h, L40S $0.79/h; Modal L40S ~$1.95/h by the second; farms (SheepIt, GarageFarm) | Cycles | ~5 s per hero, ~1-2 min per 5 s clip | the `bpy` wheel in a Linux image | same | `--cloud` opt-in |
| AI: OIDN | 4-8x fewer samples | 0.1-1.5 s per frame | Apache-2, everywhere | -- | **yes** (used) |
| AI: upscalers (Real-ESRGAN, FSR, MetalFX) | 720p -> 4K | -- | -- | invents engraving and text | drafts only |
| AI: diffusion relighting, AI video (Veo, Sora, Kling) | rewrites the product's pixels | -- | -- | geometry drifts | **no** on the product; yes for backplates, HDRIs, writing shots |

## What the data taught us (GLB fixes, not renderer work)

1. **Tessellation.** FreeCAD's default (0.5 %, 28.5 deg) facets silhouettes in close-ups. A render export at
   0.05 % / 5 deg (constants patched in a scratch process, `export_fine.py`) took 3-9 s for 6-10x the
   triangles (movement 171k -> 959k, bike 46k -> 590k; GLBs 15-31 MB). The engine's law is one tolerance.
2. **Volume thickness is written in mm in a metre GLB** (crystal 4.3, ruby 0.43, headlight 38).
3. **Transmissive parts are also alpha-faded** (sapphire alpha 0.05 with transmission 1; lenses 0.3/0.95): a
   path tracer applies both, so the sapphire is 95 % absent. `upgrade_glb.py` drops alpha where transmission
   > 0 and fixes the units; nothing else in the looks was touched.
4. **Anisotropy has TANGENTs but no UVs.** Blender's importer sets the strength but leaves the Tangent
   input unlinked (the GLB's TANGENTs go unused), Mitsuba cannot orient it, three.js honours it. UVs or a per-face brush direction (radial for
   turned parts, linear for milled ones) would also unlock textures: scratches, smudges, perlage maps.
5. **Knife edges.** CAD edges catch no light, the main tell of a CAD render; the movement's modelled anglage
   shows what edge breaks do. Options: Cycles' Bevel node (render-time rounded normals, Cycles only),
   normals rounded along class-1 (feature) edges in a render tessellation, or real fillets in the model.

## Thinking cinematic (and what each candidate makes easy or impossible)

- **Camera language is data.** Orbit, turntable (lights fixed, product turning, reflections sweeping),
  dolly-in reveal, crane, rack focus (rendered), a probe lens through the movement, an exploded build-up
  (rendered: parts lift by height above the plate, from the assembly), mechanism clips (`steer` rendered;
  the movement's train needs its couplings ported).
- **Lenses at true scale.** The GLB is in metres, so f-stops are physical: at 100 mm the watch's depth of
  field is 1-5 mm, real macro photography. 85-135 mm for product heroes, 35-50 mm low for vehicles.
- **Lighting sets.** Dark product set, car-commercial stage, physical sky at golden hour (rendered), white
  cyclorama, CC0 HDRIs. **Materials:** clear coat and real-IOR glass, sapphire and ruby are in the data now;
  brushing, micro-roughness and smudges need UVs; edge breaks need the mesh.
- **Motion and grade.** 180-degree motion blur (Cycles and EEVEE only), 48-60 fps slow motion, seamless loops;
  one OCIO pipeline (AgX plus looks or LUTs) for every engine, as here; bloom, glare, vignette and grain in
  Blender's compositor (elsewhere ffmpeg or Python).
- **Formats.** 16:9 at 1080p or 4K; 9:16 at 1080x1920, framed natively (the `_9x16` files are centre crops);
  1:1, 4:5; GIF/WebP loops for READMEs; ProRes or EXR for editors.
- Blender makes all of this easy (cost: size, GPL, a one-time kernel compile). three.js makes glTF fidelity
  and interactivity easy; motion blur, a compositor and clean caustics are out. Mitsuba makes physics exact
  and every feature ours to write. EEVEE makes drafts instant and every set a tuning job.

## As a product (sketch, not a design)

```
cadgen render DOC|MODEL.glb OUT.png|.mp4|.gif
    [--shot hero|turntable|orbit|dolly|rack|explode|motion:NAME|FILE.json] [--focus PART [--to PART]]
    [--look studio-dark|stage|cyclorama|daylight|FILE.hdr] [--lens 85] [--fstop 8] [--aspect 16:9|9:16|1:1|4:5]
    [--size 1080|4k] [--seconds 5] [--fps 24|60] [--quality draft|final] [--engine cycles|eevee|web] [--cloud]
```
- One pipeline: a render GLB (finer mesh, normalised looks) -> a shot file (presets resolved against the
  model's bounds, part names and motions, or written by an agent: "rack from balance_wheel to
  column_wheel") -> an engine worker in its own process -> OCIO grade -> ffmpeg. A daemon job with progress.
- Engines: `cycles`/`eevee` run the `bpy` wheel in its own uv environment (`uv run --with bpy==5.2.2
  --python 3.13`), the worker a separate GPL script; `web` is headless Chromium + three-gpu-pathtracer in
  `_runtime/browser`, nothing new to download.
- Viewer: a Render entry path-traces the current view progressively on the user's GPU (same GLB, same
  engine as `web`), with look and aspect pickers; "Export still/clip" queues `cadgen render` with that camera.
- Local vs cloud: stills and short 1080p clips locally (numbers above); 4K or long clips with `--cloud`, the
  same worker on a Linux GPU (Modal or RunPod), ~10x faster, cents per clip, opt-in.

## Dependencies and their weight

- Blender route: `bpy` 5.2.2, 245 MB download, 723 MB installed (macOS arm64 cp313; manylinux for the
  cloud); the first Cycles render on a machine compiles Metal kernels once (~220 s here). GPL-3, so the
  `bpy`-importing worker stays a separate process and script (cadgen is MIT). The app (907 MB) works the same.
- Web route: three 0.186, three-gpu-pathtracer 0.0.27 and three-mesh-bvh (MIT, a few MB) into
  `_runtime/browser`; Chromium is already there. It is young (0.0.x, WebGPU-only): pin it.
- Shared: OIDN (in Blender; `pyoidn` 48 MB otherwise), Blender's OCIO config (and the `opencolorio` wheel for
  non-Blender frames), ffmpeg (Blender's own, or `imageio-ffmpeg`).

## Open questions for Jake

1. Blender as cadgen's render engine through an on-demand `bpy` environment (the GPL worker in its own
   process), or "bring your own Blender"?
2. A render-grade tessellation (finer, with edge-break normals): an exception to one tolerance?
3. The GLB fixes (volume thickness in metres; no alpha on transmissive parts; UVs or brush directions for
   anisotropy and textures): in scope, and when?
4. A Render mode in the viewer (new UI, against "STEP UX only"), or the CLI first?
5. Cloud rendering: provider, accounts, who pays, the opt-in.
6. Presets only (shots x looks), or a shot-file language that agents write (a skill)?
7. AI-generated backplates and HDRIs, never touching the product's pixels: allowed?
8. Looks: our procedural studios only, or bundled/fetched CC0 HDRIs (Poly Haven)?

## State (2026-10-21 18:45, handing over to a cloud session)

- Blender 5.2.2 LTS arm64 (the Homebrew cask's own arm64 DMG, sha256-checked) was NOT installed in
  /Applications: the permission classifier refused it. It ran from `render-x/app/Blender.app`; Cycles saw
  "Apple M1 Max (GPU - 32 cores)" as a METAL device and every Cycles number above is Metal + OIDN on GPU.
  The brew under Rosetta resolves the x64 4.5.14 build; that download was deleted from brew's cache.
- Not done: LuxCore (not tried), native 9:16 framing (crops only), Mitsuba anisotropy (needs UVs), the
  in-browser OIDN (`oidn-web`; pyoidn used instead), an equal-quality (rather than equal-settings) timing
  pass, a GPU-contention-free rerun of the movement rack (8.2 s includes my concurrent tests; ~7 s clean).
- Every output (under `render-x/out/`): `compare_movement.png`, `compare_motorbike.png`,
  `compare_orbit_frame70.png`; `blender/movement_hero_{cycles,eevee}.png`, `blender/motorbike_hero_{cycles,eevee}.png`,
  `blender/motorbike_daylight_cycles.png`; `blender/movement_rack_{cycles,eevee}.mp4`,
  `blender/motorbike_orbit_{cycles,eevee}.mp4`, `blender/movement_explode_cycles.mp4`, each Cycles clip also
  `_9x16.mp4` and `.gif`; `bpy/motorbike_hero_cycles.png`; `mitsuba/{movement,motorbike}_hero_mitsuba.png`,
  `mitsuba/motorbike_orbit_mitsuba.mp4`; `three/{movement,motorbike}_hero_three.png`, `three/motorbike_orbit_three.mp4`.
  Frames in `render-x/frames/`, timings in each `*.timing.json` / `meta.json` (`collect.py` tabulates them).
- Scripts: scratch commit on branch `xbf-render-x`, `packages/cadgen/scripts-tmp-render/` (not for merge;
  paths assume the scratchpad layout `render-x/{glb,env,shots,app,venv,venv-bpy,web}`). Reproduce:
  1. GLBs: build the model (`python -m cadgen.engine build case.py movement.py motorbike.py`), then
     `RX_DEVIATION=0.05 RX_ANGULAR_DEG=5 python export_fine.py XBF/<m>.xbf glb/<m>-fine.glb` and
     `python upgrade_glb.py normalise glb/<m>-fine.glb glb/<m>-look.glb`.
  2. `python make_env.py env` (HDRIs), `python shots.py render-x` (shot files).
  3. Blender: `Blender -b --factory-startup --python blender_render.py -- shots/<shot>.json cycles|eevee OUT_DIR
     --samples N [--motion-blur]` (or `python bpy_run.py` with the same args in the `bpy` venv).
  4. Mitsuba: `python mitsuba_render.py shots/<shot>.json OUT_DIR --spp N` (venv: mitsuba 3.9.1, pyoidn, opencolorio).
  5. three.js: `python3 -m http.server 8765 --directory render-x`, then `node three/render.mjs <shot> RAW_DIR SPP 1 A:B
     4000000 -1` and `python three_post.py RAW_DIR OUT_DIR` (npm: three 0.186.1, three-gpu-pathtracer 0.0.27).
  6. `encode.sh FRAMES OUT.mp4 [vertical]`; `montage.py OUT.png "label=path" ...`.
