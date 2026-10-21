# OCCT stills: scope (2026-10-21)

Decision (Jake, 2026-10-21): OCCT draws the stills (`cadgen snapshot`). three.js stays the live viewer and
adopts OCCT's look for the regular display modes. Preview is consolidated afterwards into one studio look,
designed in OCCT first and made similar in the viewer. Start with the regular display modes, which Jake is
happy with as the prototype draws them.

Prototype: branch `xbf-occt-snapshot` @ 6a807ec17 (base 842f2171c), worktree `agent-occt-snap`, package
`cadgen/engine/render/` (shapes from the daemon's XBM buffers, no OCCT meshing; one presentation per part shared
by its instances; `compare.py` contact sheets; spike report in the session transcript, timings in the scratchpad
`occtsnap/bench.json`).

What it has: pixel parity on geometry, cameras (incl. perspective), pose, explode, clip plane and side, and
annotation stroke / measurement positions; modes solid, shaded, parts, x-ray, hidden-line, wireframe; warm
frames 0.04–0.13 s; cold = xbf read + mesh, as Chromium.

What it lacks: text drawn as a screen label (the viewer lays text on the surface, in its plane); no measurement
end rings or label box; grid without axis lines; clip cap darker and showing what is behind it; preview with a
visible floor disc; explode computed through node; `--display` refuses `exploded` and the verb has no
annotation/measurement flags; a Retina font-size fudge; a headless GL context on macOS only so far.

## Phase 1 — OCCT stills for the regular display modes (start here)

1. **Floor.** Remove the disc. The floor becomes a shadow-catching plane that reaches past the frame and blends
   into the backdrop, so nothing has a rim. The real studio is Phase 3. *(started 2026-10-21)*
2. **Annotation text fidelity.** A text mark is drawn as a textured quad at its four corners: the viewer's font,
   the mark's `fontPx` at the view it was authored in, line height 1.25, its colour, unlit, with the viewer's
   depth rule. The authoring view then reproduces it pixel for pixel and any other view foreshortens it as the
   viewer does. Measurement end rings (3.5 px) and the label box are drawn as the viewer draws them, in screen
   space. The comparison fixture's annotation is re-authored through the viewer's Annotate tool at the iso view,
   not by hand (the hand-made quad had its corners reversed, which is why the Chromium side read mirrored).
   *(started 2026-10-21)*
3. **The view travels with the annotation.** The annotation file records the camera it was authored in (added if
   it does not already), and `cadgen snapshot DOC OUT.png --annotation FILE [--camera authored]` recreates that
   view; `--measurements FILE`; `--display` accepts `exploded` with an amount.
4. **Explode in Python.** `explodedView.js`'s layout ported to `engine/render/explode.py`; no node at run time;
   a parity test against the JS on the fixture (offsets equal to 1e-6).
5. **Grid.** The viewer's design in OCCT: its cell rule and the red/green axis lines.
6. **Clip cap.** Caps that hide what is behind them even with shared presentations (per-instance presentations
   while a clip is on, if OCCT's capping cannot); the cap's colour the viewer's.
7. **Materials.** OCCT's raster PBR takes colour, alpha, metallic, roughness, ior and emissive. Coat, anisotropy
   and transmission have no raster equivalent in OCCT 7.9, so the stills approximate them (coat as lower
   roughness, anisotropy ignored, transmission as alpha with the tint). A documented gap, not a blocker; the
   path tracer can do glass later.
8. **Headless GL everywhere.** macOS: hidden Cocoa window (done). Linux: an offscreen EGL context with no X
   server, to be verified against OCP's OCCT build (cloud, CI); Xvfb only if EGL is impossible, and then that is
   the one dependency the stills keep. Windows: a hidden WNT window.
9. **The verb and the daemon.** `engine/snapshot.py` renders through `engine/render`; the Chromium page path
   leaves the engine at the cutover (the viewer's own browser tests keep their browser). A warm render process
   per served document in the daemon keeps presentations alive, so a warm snapshot is ~0.1 s; cold is read + mesh
   as now. No Playwright in the engine's snapshot path.
10. **Tests.** OCCT golden images of the material fixture per mode and flag (cheap, no browser); the explode
    parity unit test; the contact-sheet harness kept as a manual script under `scripts/bench/`.

Size: about 2–3 agent-days (Opus/high). Risks: item 8 (Linux headless context), item 6 (capping with shared
presentations).

### Status 2026-10-21 (evening)

- Integration branch `xbf-render` @ e4baacc00 = working head 87a821c40 + prototype + lane C part 1 (look.py,
  `/api/look`, rig slot, parity harness; regular modes matched under ΔE 1) + lane A (floor without rim, text
  marks drawn as the viewer's canvas text mapped on the quad, measurement overlay, viewer camera accepted).
- FINDINGS: OCCT's regular modes are Phong (no IBL, no tone curve, no exposure; part colour squared; light
  intensities ignored; Filmic only in the path tracer's display shader). The annotation file records NO camera:
  a mark names its view by label and the camera lives in Quick Edit's `.snapshot.json` packet under that name,
  so `--camera authored` resolves through the packet (lane B told). The viewer's Text tool places a card facing
  the authoring view, lifted 8 px toward the eye, not on the surface; OCCT now reproduces that.
- DECIDED by Jake 2026-10-21: finishes STAY in Solid and Shaded on both renderers; OCCT's regular modes move
  to PBR with one shared reflection image; lane C part 2 running. Lane A follow-up running: the preview
  backdrop light like the viewer's studio, soft baked contact shadow (its first version was a flat mid grey
  with a hard shadow). Grid + clip cap agent running (branch xbf-render-g).
- LANE B LANDED (xbf-render @ 9c4598389): explode in Python pinned to the viewer's layout by one golden
  (`packages/core/src/lib/xbf/__fixtures__/explode.json`, bit-exact); verb flags `--annotation FILE` (file or
  `.snapshot.json` packet), `--measurements FILE`, `--display exploded=0.6`, `--camera authored` (resolves the
  mark's view label through the packet; refused by name without one); `context.py` per platform (macOS proven;
  Linux Xw on $DISPLAY or EGL neutral window else a refusal naming Xvfb; Windows hidden HWND — both unverified);
  warm render process per served document (`render/service.py`, daemon `draw` op; patch = only changed parts
  remade; retired over the RSS cap; GC off in the process, scenes deleted between pictures); the switch
  `CADGEN_SNAPSHOT_RENDERER=occt` in `engine/snapshot.py::renderer()` (cut-over deletes `renderer()`, its branch
  and `_chromium`). Warm through the daemon: motorbike 0.10–0.15 s wall / 42–60 ms frame, tom 0.17–0.33 s /
  104–252 ms; first snapshot 2.4 / 7.7 s; render process RSS 0.36 / 0.58–0.66 GB. Engine suite 231.
  CI TO DO at merge: the drawing tests need `xvfb-run -a -s "-screen 0 1920x1080x24 +extension GLX"` +
  `libgl1-mesa-dri` on the Linux job, else they skip (dead tests); Windows runners likely skip (GDI GL 1.1).
  Decided: Pillow admitted to engine law 3 for the renderer (already a cadgen dependency); lane A amends.
  Known: portrait framing bug in Scene.frame/_pinned (lane A fixing); `snapshot --no-daemon` with a file-taken
  xbf fails "the build left no document" (engine agent fixing on the working branch).

## Phase 2 — the viewer's regular modes adopt OCCT's look

1. **One look spec** (`engine/render/look.py`, served to the viewer as JSON): key light direction and intensity,
   ambient, IBL strength, the tone curve (OCCT's Filmic) and exposure, per-mode surface defaults, edge colours
   and widths, x-ray opacity, the hidden-line rule, backdrop colours, the grid rule.
2. **three.js side** (the xbf scene and ShellViewport for xbf only): the same rig (directional key + ambient + the
   studio IBL at the spec's strength), the exact tone curve as a custom tone-mapping chunk, the spec's exposure;
   the surface look's colour "lift" removed for xbf.
3. **Measured parity:** contact sheets of the fixture and the corpus per mode; silhouette IoU (already 1.0) and
   colour ΔE median / 95th; target median ΔE < 3 in solid, shaded and parts; x-ray, hidden-line and wireframe
   by pixel diff.
4. The STEP renderer stays as it is until the cutover.

Size: about 1–2 agent-days.

## Phase 3 — the render track (NOT autonomous; designed with Jake)

Jake, 2026-10-21: the studio for Preview is subjective and needs his input. The goal is photorealistic,
high-quality renders for export — mp4s for social media — which may warrant a separate pipeline in both the
viewer and the CLI. So: Preview stays the viewer's live approximation (lane A's floor fix removes the disc,
nothing more is redesigned), and the render track is its own thing: its own verb (`cadgen render`, stills and
mp4) and its own viewer entry, separate from the display modes that lanes B and C are matching.

Candidate bases, to decide with Jake:
- **Blender headless via our GLB** (Cycles with denoising, EEVEE for fast; the glTF extensions we write carry
  ior, transmission, volume, clearcoat, anisotropy, emissive strength, and the baked motion clips import as
  animations; camera rigs, DOF, HDRI, turntables; ffmpeg to mp4). Dependency: a Blender install. Likely 1–60 s
  a frame at 1080p depending on engine and samples.
- **OCCT's path tracer** (in-house, same meshes, no external dependency; 10–216 s a frame at 256–512 samples,
  no denoiser; raster PBR without coat/anisotropy, glass by IOR only).
- **Browser path tracing in the viewer** (three-gpu-pathtracer; viewer-native "what you see is what you
  export"; weaker than Cycles; mp4 via MediaRecorder/ffmpeg.wasm).
Recommendation to put to Jake: Blender via the GLB, with the viewer's Preview as the live approximation.
Open questions for him: quality bar (Cycles-level vs good PBR raster), what moves (turntable, explode, motion
clips, camera paths), where it runs (local GPU / cloud), acceptable minutes per clip, Blender as a dependency.

## Later, optional

- Dropping Playwright from cadgen's dependencies at the cutover; the browser becomes the human's only.

## Not in scope

- The old pipeline's snapshots (STEP); they go with it at the cutover.
- OCCT inside the live viewer.
