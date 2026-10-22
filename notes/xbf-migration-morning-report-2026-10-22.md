# Migration morning report — 2026-10-22

The migration is done on the working branch and on the PR. **CI IS GREEN at bf236ba28** (run 39803671195: all 11 checks, both cadgen jobs passing for real, Linux 1199 tests with no skips, Windows 1186 with the 27 GDI drawing skips, Windows job 361 s against 1170 s). Morning head was **30a170056** on
`worktree-agent-a1cb246fec605ab31`; PR #568 "cadgen 0.8: the xbf engine replaces the old pipeline"
(https://github.com/earthtojake/text-to-cad/pull/568) is fast-forwarded to it with a new description. Nothing
touched main or VERSION. CI's first run: 9 checks pass, the `cadgen (Linux)` and `cadgen (Windows)` test jobs
fail; classified and fixed at 7a66f59b0: a real Windows bug (relpath across drive letters, 150 of 191 failures), GPU-less Windows drawing tests now skip with a reason, Linux's two DXF rows turned out to be a real llvmpipe defect (dotted circles and arcs from 1 px lines under 8x MSAA), so no Linux golden was committed: round 2 draws drawings supersampled through OCCT's RenderResolutionScale instead. Round 2 (f835bdd9e): Windows 191 -> ~20 failures (relpath fix); an SRDF engine bug found on the way (`read_srdf` globbed `*.urdf` and `*.URDF`, which on a case-insensitive file system count the same file twice, so every SRDF was refused as having two robots; applies on macOS too); report paths spelled with `/`; the remaining ~10 Windows failures are subprocesses that reach the daemon and time out at 300 s (pool, progress, script imports, where, telemetry, viewer xbf route), being diagnosed with a helper that dumps the daemon's state and log on timeout and fails fast when no daemon is coming.
Round 3 (eb43523a6 → 22f9ef0ff, run 39791759286): the Linux DXF rows PASS (drawings are aliased 1 px hairlines with MSAA off on every platform; supersampling was measured and rejected, ink fell to 55%). The Linux render suite had been silently SKIPPING itself in every earlier run (one Xvfb connection refusal, cached, turned the whole file into skips); now that it runs it shows three edge-heavy rows over the bound on llvmpipe (xray 2.1%, hidden-line 1.6%, wireframe 2.3%: the same multisampled-line class as the DXF) and one uncharacterized error (DaemonRenderTest lists no render process after a rebuild). WINDOWS IS A PRODUCT DEFECT, not CI polish: the daemon starts and listens at +3.7 s, but EVERY worker hangs for the full 300 s inside numpy's import (`from . import _multiarray_umath`, reached through build123d from `determinism.install`), while the test process and the command import numpy fine; the innermost frames come with the next run. Decisions taken within Jake's rules: the GPU look is not changed; if the three Linux pictures are the dotted defect, MSAA goes off for software renderers only (llvmpipe/softpipe/SwiftShader) with Linux goldens for those rows; on Linux CI 'no GL context' fails instead of skipping and a refused connection is retried per test; the Windows hang is chased as a product bug.
Round 4 (f2ece2f4b → 6c36a2f1d, run 39800997657): WINDOWS DEADLOCK FOUND AND FIXED. The worker's request thread sat in a synchronous ReadFile on its stdin pipe (`for line in sys.stdin`); on Windows every other synchronous call on that pipe waits behind it, and a loading DLL's C runtime calls GetFileType on the standard handles (OpenBLAS does), so numpy's DLL load waited for the daemon's next request while the daemon waited for the build: no worker ever built on Windows. Fix: the worker reads requests on a duplicate of the pipe, a handle of its own, and its stdin (fd 0, `sys.stdin`) is the null device, as its protocol already went out on a duplicate of stdout; on every platform a model that reads stdin no longer swallows protocol lines. Also landed: `render.context.driver()` no longer caches a refused connection (each canvas asks again; a test refuses one and draws the next); one gate `tests/python/support/opengl.require()` for every drawing test, skip-with-reason except on CI with a DISPLAY where it fails (Windows keeps its skip); the Linux job's ~134 s test_render cost is noted in the PR text; DaemonRenderTest's empty listing = the rebuilt version never reached the render process and it was retired as idle at the same 60 s the test stopped polling (why the patch did not land on Linux is open; the wait now names what it saw); the Windows memory test samples committed memory every 2 ms during the parse.
Round 5 (bf236ba28, run 39803671195 queued): the stdin fix WORKS, Windows workers build; the Windows suite ran in 379 s instead of 1170 s with test_daemon finishing (1185 tests ran). Windows 3 → 0: another PRODUCT BUG, the worker memory cap (`worker.resident_mb()` made the same untyped GetCurrentProcess call and returned None, so no worker was ever retired on Windows, and `_physical_mb()` had no Windows branch, so no default cap; now GlobalMemoryStatusEx); the state-folder check expects `.port` on Windows; a test-harness cp1252 decode fixed. Linux edges classified from pictures (scratchpad `ci-edges/`, sent to Jake): the lines are whole, anti-aliased differently, beaded along diagonals, 95-96% of the macOS ink; Linux goldens committed for xray/hidden-line/wireframe and the renderer left alone (the per-platform golden mechanism stays). The Xvfb refusals' real cause: every Canvas opened its own X connection and a held scene keeps its canvas, so a process held one X client per scene against Xvfb's 256-client limit shared by every parallel drawing process; `driver()` now keeps one display connection per thread. DaemonRenderTest passes with the X-connection fix (the same client-limit refusal most likely broke the render process's patch; never caught in the act).
Follow-ups recorded in the PR text, not in this PR: the MCP server reads `sys.stdin.buffer` on its main thread while worker threads may load DLLs (same deadlock shape on Windows); Mesa's opengl32 on the Windows runner so the drawing tests stop skipping there.

## State per step

| Step | State |
|---|---|
| Gate: every lane merged green | held at 33d65530f |
| Delete the old engine and its dependencies; verbs top-level; laws 1–12 | done (d1ae594a3 + fixes): 790 files, −167k lines, 278 old test files; store, old daemon/pool, sidecar, native tracer, Node builders, old STEP writer, tessellation artifacts, old parsers and snapshot doors, the STEP renderer; Chromium and Playwright out of the wheel |
| Merge main 0.7.26 | done (69d4be720): 44-file resolution listed in the merge; Pencil, Shapes, pick menus, entity Measure and look-at on the xbf renderer |
| Tests and CI | done in the deletion: Xvfb step on the Linux job, selection rules pruned; first CI run on the PR as above |
| Skills, README, docs site, hero | done (1d376f69f): every skill on the engine's commands, documented-commands test green, hero = `planetary.glb` with its baked clip |
| Corpus | done: all 19 projects on the engine under `models/`; flagged models kept under `unported/` (below) |
| Benchmark harness + batch | done: `scripts/bench/corpus_bench.py` + `compare.py`; batch on 30a170056 below |
| Push to the PR, description | done |

## Flagged models (kept under `models/<project>/unported/`, each with its reason in a README)

- tendon_hand: 56 of 112 — 25 read the frozen palm STEP that lives outside git, 8 the 250 MB native hand,
  7 clearance studies on the old step_scene, 6 other frozen STEPs, 8 generated validation JSON, 1 broken
  import, `palm_little_review` fails on 0.03 mm fillets.
- moonwatch: the whole-watch, dial, bracelet and finishing_sampler models (case and movement are ported).
- examples: 24 parts outside the five the port selected.
- tests: `joint_playground.urdf` (a URDF of primitives; the snapshot draws it as it is).
Everything else ported in full: assemblies (hero gear, mars rover, humanoids…), branding, dfm, drawings (8),
f1, f14d, falcon_heavy, hypercar, juno, lyra, motorbike, qdd, radial, w16, tom.

## Builds and edits: main's engine vs the new one (batch on 30a170056, load 7–18)

| model | old cold | new cold | old no-op | new no-op | old edit | new edit | file |
|---|---|---|---|---|---|---|---|
| tom | 420–600 s | **97.5 s** | 6.7 s | **1.35 s** (4.0 before the read fix) | 24–29 s | **8.5 s** | 419 MB STEP → 59 MB xbf (3.3 s) |
| hypercar | ~240 s | **187 s** | — | 3.3 s | 5–7 s | **5.0 s** (incl. its 42 MB GLB) | 224 MB STEP → 42 MB xbf (0.9 s) |
| juno | 35 s | **23.1 s** | — | 0.36 s | 2.8–3.5 s | **1.8 s** | 22.6 MB STEP → 7.5 MB xbf (0.3 s) |
| f1 | — | 494 s | — | 2.4 s | — | 7.5 s | 110 MB xbf |
| movement | — | 176 s | — | 0.84 s | — | 5.0 s | 17 MB |
| case | — | 19.0 s | — | 0.25 s | — | 8.7 s (the case body reruns) | 7 MB |
| motorbike | — | 8.7 s | — | 0.17 s | — | 0.46 s | 2.4 MB |
| qdd | — | 5.5 s | — | 0.21 s | — | 1.2 s | 2.5 MB |
| planetary gear | — | 3.4 s | — | 0.11 s | — | 0.42 s | 0.9 MB + GLB |
| l_bracket | — | 2.6 s | — | 0.10 s | — | 0.23 s | 0.05 MB |

Walls are the whole `python model.py` process including every declared file. Old numbers: 19 Oct logs
(`scripts/bench/old-engine-numbers.json` cites each). Models without an old number were never benchmarked on
main; the harness marks them "rebuild on main to fill" if you want them. Whole-document mesh (cold / warm):
tom 1.9 / 0.95 s, hypercar 2.8 / 2.4, f1 3.1 / 2.7; a warm snapshot through the daemon 0.09–0.19 s on every
model. Restart with nothing changed: 2–4 s on every model; tom 6.3 s after the read fix (97 s in the batch: its body read two .brep parts past the engine, which left it unkeyed; fixed at 7a66f59b0). Worker
RSS after a cold build: tom 1.9 GB, hypercar 2.5 GB, f1 2.4 GB. Full tables: `tmp/bench/batch2/table.md`
and `tmp/bench/compare.md` in the engine worktree.

## Engine gaps the ports found (fixed overnight unless marked)

1. Namespace packages (`lib/` without `__init__.py`) were never re-imported, so edits went unseen — fixed.
2. Raw OCCT calls outside the memo's list ran uncached and silently — the report now names them with the remedy.
3. A changed module is reported with the binding that changed (`dims.py: WIDTH changed`).
4. Posed STEP export took over 20 min on the hypercar (the writer's reorder made 2.3 M kernel calls) — fixed: 6 s.
5. A warm STEP numbered its assembly-usage ids on from earlier writes — fixed: renumbered per export.
6. `python model.py` paid 2.5 s of build123d import before asking the daemon — fixed: the engine import hands
   off first; motorbike no-op 0.17 s, edit 0.42 s end to end.
7. OPEN: cold builds drift on a few parts (w16 3/794, tom's yokes, moonwatch's tachymeter): parallel-boolean drift.
8. OPEN: a replayed body can write BRep entries in a different order than cold (f1 casing, f14d, radial's
   barrels): geometry and names identical, bytes differ.
9. OPEN, your decision: every commit rewrites every xbf, because the closure records the commit.
10. OPEN: `couple` is linear only; slider-cranks and cams need a motion. Function-of-time motions bake large
    GLBs (radial: 140 MB). A prismatic pair's RigidJoint must be a pure translation at the axis origin (doc).
11. OPEN: the viewer's `connect_to` on a sub-assembly with its own joints silently leaves its base part on the
    ground when posing; a derived file is rewritten when its xbf changes even if its bytes are identical; the
    sculpted humanoid's worker holds 5.8 GB and f14d's airframe stage reaches the 16 GB default cap.
12. tom's restart miss — fixed (7a66f59b0): a read past the engine now still keys the body by the file's bytes.

## What else changed by decision overnight

- A refused or failed snapshot removes a stale picture at OUT instead of leaving the previous one (law 16);
  four tests rewritten.
- `cadgen export` takes `.xbf`/`.step` sources only; `--part` is on the refs grammar.
- Quick Edit's attachment label names `cadgen snapshot --annotation`.
- The old project documents the fold-back had dropped (falcon_heavy research and provenance, radial's report,
  dfm's scripts) were restored beside their projects.

## Decisions for you

1. Every commit rewrites every xbf: keep, or record the commit outside the file.
2. The replayed-body BRep order drift: accept, or fund a canonical writer.
3. Rebuild the seven models with no old number on main for a fuller table, or leave the three flagships.
4. CI is green; nothing left there. Optional look call for GPU-less Linux (llvmpipe): keep the beaded anti-aliased edges now recorded as Linux goldens, or turn MSAA off for software renderers only (solid aliased edges); pictures in the session scratchpad `ci-edges/`.
5. The render track (Blender draft PR) and the release remain parked by your word.

Demo viewer: port 5251 on 30a170056, models rebuilding at the time of writing (reload the tabs).

## End of day (2026-10-22, ~15:30 EDT): PR #568 whole at 8aa3fd951

Every branch of the day is merged and pushed; the PR description is rewritten to match. Landed after the morning:
the git commit out of the closure and the key on the format alone (xbf/2); drift accepted with evidence and
`--verify` order-free; the sub-assembly ground-link fix; the UI port (Preview floor at the lowest point, prompt view
pose + ink touches) and eight viewer restorations (reload state, packet hidden/isolated, measure undo, build
progress + failed card, cad_view selection, ink cleared on a new version, SDF count); the brightness fix (viewer
environment baked OCCT's way; regular modes isotropic; p95 30→9 fixture, 29→1 qdd); hidden/isolated parts left
out of the stills; `--display light|dark` on drawings; the AP242 kinematics (law 13, conformance test, couplers,
standard interpolation, no loop/spline/unlocked couplings, xbf/3); the CLI restorations from the gap audit
(`--job` with output settings and robot visual ids, DXF replay, `package://`, viewer prewarm, `--json` failures,
OUT folder, Windows console flags, telemetry kinds; `--mode list` dropped by decision); the corpus recovery of
every animation routine in the AP242 vocabulary with the moonwatch fan fix and the hero regenerated identical;
the sampler kink fix and xbf/4. Notes in this folder: `migration-gap-audit-2026-10-22.md`,
`ap242-function-couplings-scope-2026-10-22.md`, `xbf-post-migration-followups-2026-10-22.md`.

Open for Jake: the eight corpus decisions from the recovery report (GLB sizes, qdd carrier, hypercar door drift,
rover wheel range, juno ankle/handstand, f1 lift, radial sliders and collisions; the sampler one is fixed), the
render-scripts draft PR, the Windows follow-ups, the docs rewrite last, the release. The moonwatch grand-tour
video renders under a Codex babysitter (expected ~17:30).
