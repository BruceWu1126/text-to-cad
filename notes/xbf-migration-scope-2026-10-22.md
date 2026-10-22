# xbf migration scope: what `cadgen.engine` replaces, what it lacks (2026-10-22)

Read-only inventory. Sources: work head **bbce82504** (`worktree-agent-a1cb246fec605ab31`: PR #568's unreleased
0.8.0 tree plus the engine), **origin/main 2f793659f** (0.7.26, what ships), **origin/xbf-render 7986c2764** (OCCT
stills, snapshot flags, warm render service; lanes C e35f2b4b6 and G 6dcc56b60 are not merged into it, and it is not
merged into the work head), origin/xbf-notes b8e367155. I ran every verb's `--help` on main and on both engine heads,
plus one `--no-daemon` build. I did not run the old pipeline, a daemon, or any snapshot.
Status words: **ported**, **diff** (ported with a difference), **dropped** (decided, see C), **missing**, **n/a** (not
the engine's job; it stays outside the engine but must survive the deletion).

## A. What migrates

| Old pipeline on main | Engine replacement | Status |
|---|---|---|
| `@step/@stl/@glb/@threemf` + `python model.py` (authoring.py, generation*, cli/_run_model, daemon `run`) | `@xbf`, `@step/@glb/@stl/@threemf(out)`, `@memo`; `python model.py` and `cadgen xbf build` go through the daemon (model.py, build.py, worker.py) | diff |
| Build daemon: pool, broker, admission, slots, prewarm (cadgen.daemon 6.6k lines, coordination 0.7k) | One daemon per installation; one warm worker per script; RSS cap per worker; exits after an idle hour (daemon/worker/client.py) | diff |
| Store (cadgen/store 10.8k lines, STORE.md, `cadgen store`, CADGEN_CACHE_DIR/STORE_MAX) | Memo in the worker; restart taken from the xbf's closure | dropped |
| Dependency graph: closure, gate, native file tracer (native/filetrace.c, `_runtime/native` built with zig), source_hash | Per-symbol body keys, `open()` audit hook, closure manifest in the xbf | dropped/diff |
| Sidecar: schema-9 `.step.json`, `.step.js` modules, animation_source, mesh_animation | None; provenance served live (`made_by`, `cadgen where`) | dropped |
| STEP writer (step_export*, step_reemit, step_assemble, step_hash) | export/step.py (XCAF writer; names, colours, materials, density) | diff |
| Mesh exports through the Node builders (`_runtime/node`, mesh_export, node_runtime) | export/glb, stl, threemf in pure Python, using the one mesh cache | diff |
| Tessellation and topology artifacts (tessellation, step_scene_mesh, surfaces, glb_topology, `/__tess_cache`) | mesh.py (BRepMesh at FreeCAD's default, XBM2 buffers), served by serve.py | diff |
| Kinematics vocabulary (kinematics.py, kinematics_resolve) | mechanism.py: build123d joints, `configuration`, `motion`, AP242 subtree | diff |
| step_scene: `read_step`, `read_scene`, Selection | reader.py: `read_step`, `read_xbf` (reader process, memo-keyed) | diff (no resolver: B3) |
| STEP and mesh snapshots (snapshot_core/cli STEP branch, snapshot_video, per-format doors) | snapshot.py + render/ (OCCT stills behind `CADGEN_SNAPSHOT_RENDERER=occt` on xbf-render) | diff |
| Parsers for `step build/compile`, `stl/3mf/glb build`, `store`, `daemon status` | engine/cli.py and api.py (`cadgen.build/export/snapshot/serve/where`) | diff |

**Stays outside the engine and must survive the deletion:** the viewer server (needs rewiring, B1), the MCP server
(B2), urdf/sdf/srdf validate and snapshot, `dxf snapshot` and drawing_payload (its cache lives in the store: B6),
`@eng_drawing`, flatten, dxf_emit, geometry, drawing_checks, doctor, telemetry/analytics, updates (the version feed,
used only by the viewer and MCP), `_internal/launch` (`uvx … --from cadgen==V`, unchanged), and dev_install.

**Size:** the deletion is about 35–40k lines of Python: store 10.8k, daemon 6.6k, coordination 0.7k, most of
`_internal` (15.5k), about 8k of top-level modules, and 2.1k of CLI. The bundle loses `_runtime/node` (no Node at
runtime) and the zig file tracer. About 150 test files go with it (E). The engine is 19.8k lines on the work head and
24.4k on xbf-render.

**Counts (old → engine):**
- **CLI verbs.** main registers 24. Thirteen are the engine's to replace: 1 ported (`daemon`), 10 diff (`step build`
  and the three mesh `build`s become `export`; the five `snapshot`s become one `snapshot` covering 5 of 9 kinds;
  `daemon status` becomes `status`), and 2 dropped (`step compile`, `store`). `python model.py` is diff. The other 11
  stay (n/a): doctor, dxf snapshot, urdf/sdf/srdf validate, urdf/sdf snapshot, mcp, telemetry, viewer, viewer stop.
  The engine adds `build`, `serve`, `stop` and `where`.
- **Flags.** The replaced verbs and the model run have 28 unique flags: 9 ported (camera, display, grid, preview,
  annotation [xbf-render], width, height, size-profile, json), 4 diff (kinematics→`--pose`, debug→`--json` timings,
  glb `--animation`→every motion baked, force→`--verify`/`CADGEN_ENGINE_MEMO=0`), 6 dropped (materials, mesh-tolerance,
  mesh-angular-tolerance, store dry-run/grace-hours/max-size), 8 missing (job, mode list, time, video, focus, hide,
  view-labels, verbose) and 1 n/a (joint-values). The engine adds 16: `--xbf/--step/--glb/--stl/--threemf [F]`,
  `--code`, `--name`, `--verify`, `--no-daemon`, `--pose`, `--part`, `--no-inputs`, `--measurements`, `--port`,
  `--three`, `--foreground`.
- **Decorators.** main has 6: 4 diff (step/stl/glb/threemf, which now take `out` only), 1 missing (`@dxf`), 1 n/a
  (`@eng_drawing`). Of their 6 distinct arguments, 1 is ported (`out`) and 5 are dropped. The engine adds `@xbf` and
  `@memo`.
- **Root API.** main's `cadgen.__all__` has 27 names: 3 stay (`__version__`, `build123d`, `srgb`), 5 diff (step, stl,
  glb, threemf, read_step), 10 dropped (revolute, slider, cylindrical, fastened, couple, MateTarget, AssemblyHelper,
  target, compound_from_instances, ensure_step_topology_artifact), 7 missing (dxf, read_scene, StepScene, Occurrence,
  Selection, report, track) and 2 undecided (label_shape, label_text).

## B. Holes: old capabilities the engine lacks

Severity: **viewer** blocks the viewer or the in-chat app, **skill** blocks a documented skill workflow,
**platform** blocks an OS, **nice** is nice to have.

| # | Old capability (main) | Engine today | Sev | Smallest fix |
|---|---|---|---|---|
| B1 | The viewer opens STEP/STP. It compiles them through the old daemon and store (viewer/compiles, tess_cache, surfaces, warm, preview, store_paths) | Engine `serve` reads STEP, but `/__cad/xbf` refuses anything that is not `.xbf` (viewer/xbf.py) | viewer | Send `.step/.stp` through `engine_document` and draw them with the xbf renderer. Decide whether the STEP renderer (packages/ui, 227 files, 95 tests) retires |
| B2 | The MCP app shows every format through `/__cad` routes, tunnelled as tool calls (mcp/tunnel.py) | `.xbf` is refused (`SOURCE_EXTENSIONS`). The xbf page reads the engine's own random port directly, and only loopback origins may (access.py). So in-chat iframes, `--host` viewers and port-forwarded viewers cannot load it | viewer | Relay the engine's document routes under the viewer's origin (`/__cad/xbf/...`); the tunnel then carries them. Add `.xbf` to the MCP extensions and tool descriptions |
| B3 | Prompt refs resolve to geometry: `read_scene(path).resolve("…#o1.2.f7")` gives Selection.shape()/entities() (cad SKILL, inspection-and-validation.md) | `cadgen where DOC REF` returns facts only (area, centroid, radii, length, mass). Nothing maps an element name to a build123d sub-shape | skill | `read_xbf(path)` gains a `resolve(ref)` (or `engine.select`) over the names the reader already carries |
| B4 | Quick Edit replay: `cadgen snapshot --job X.snapshot.json` renders every view of a packet. Its jobs also carry outputs, padding, renderScale, tightFrame, transparent, `selection.hide/focus`, `quality.tessellation` | One view per call. A packet is read only to resolve `--camera authored`. The old packet validator (`_accepted` → `prepare_render_job_packet`) has no `xbf` kind, so an xbf Quick Edit packet is probably refused (I did not run it) | skill | `--job FILE` on the engine verb (one loop; SRC overrides inputs). One `cadgen snapshot` dispatcher by suffix |
| B5 | `@dxf` drawings. They build through the old daemon and record (`authoring._build`) | No engine home | skill | Re-home `@dxf` as an in-process decorator like `@eng_drawing` (body → dxf_emit → drawing_checks → atomic write), with no cache |
| B6 | Snapshots of DXF, URDF, SRDF and SDF | The engine refuses them. The old door lives in snapshot_core/snapshot_cli, and the DXF payload is cached in the store (`drawing_payload` → `cadgen.store.drawings`) | skill | Keep the old door for these 4 kinds with its STEP and store branches removed; cache drawings in memory |
| B7 | CLI contract: a `--json` failure is `{"ok": false, "error"}` on stdout with exit 1. Also `--verbose`, `--force`, and `--json` on every verb | Errors are stderr text with exit 1 or 2 (probed: `--code 'x=1' --json` printed no JSON). `export` has no `--json`; `status` always prints indented JSON | skill | One failure envelope in engine/cli.py, and `--json` on export, status and where |
| B8 | Snapshot `--focus/--hide` (Quick Edit jobs carry `selection.hide`), `--view-labels`, `--mode list`, OUT as a folder | Missing | skill/nice | focus/hide by occurrence path in the OCCT scene (it already has one presentation per instance); the rest are nice |
| B9 | Motion review: a still at clip time (`--animation/--time`), `--video` mp4/GIF, sidecar clips playing in the viewer | No still at time t and no video. The viewer's xbf Position poses but cannot play motions (the export plan promised a motion preview). Only an exported GLB plays | nice | `--pose MOTION@t` (forward kinematics via Timeline). The viewer reuses the GLB animation bar. Video waits for the render track (Phase 3, undecided) |
| B10 | Progress: `report()/track()`, `cadgen daemon status` progress, the viewer's build progress (build_progress.py) | No progress channel. `status` lists workers | nice | Send `report` lines over the worker's existing log frames; show them in `status` |
| B11 | Coupled joints (`couple`, gear trains, hypercar's door) | The document and every pose honour gear pairs, but nothing can author one (README §Kinematics) | nice | One engine word, `couple(...)`, which needs Jake's OK against "build123d joints only" |
| B12 | Models inside a Python package: the old runner used the dotted name, so relative imports resolved (step-generation.md) | `load_script` imports the file by `script.stem` with no package, so `from .x import y` fails | skill | Derive the dotted name from the `__init__.py` parents, as the old loader did |
| B13 | `@eng_drawing` input: the skill reads `cadgen.read_step(...)`, which uses the store's BREP cache | — | skill | Point it at the engine's `read_step`/`read_xbf` (both work outside a build) |
| B14 | Telemetry: builds, snapshots, daemon health and crash reports (cadgen.daemon.telemetry) | The engine counts nothing (law 3). The receiver's `KINDS` (apps/api/src/events.mjs) has no `xbf` | nice | Count in `cadgen.cli` around the engine's verbs (outside the engine); add `xbf` to the receiver, keeping old schemas readable |
| B15 | Windows: the old daemon used `CREATE_NO_WINDOW` with a new process group (fix for #597) | client.py starts the daemon with `DETACHED_PROCESS\|CREATE_NO_WINDOW`, which leaves it no console, and workers, readers and render processes are spawned with no flags. That re-creates #597: one console window per worker | platform | Copy `daemon.client.detach_kwargs()` and run the Windows job |
| B16 | Headless Linux snapshots (Chromium needs no display) | OCCT stills need an X server (Xvfb) or EGL. Neither is verified, and CI has no xvfb step, so render tests skip (dead tests under repo policy) | platform | Prove EGL, or keep the page as the Linux fallback for documents until it is proven; add the xvfb step to cadgen-linux |

## C. Agreed drops (with where each was decided)

| Drop | Decided |
|---|---|
| The cache store and anything else written to disk; restart is cold except for the xbf | Jake 2026-10-19 (memory engine-ground-up-rewrite); engine law 2; closure plan §4 (2026-10-22) |
| The dependency graph (closure/gate/file tracer/results by key) | Jake 2026-10-19 ("every build runs the body; the memo keeps it correct") |
| STEP as the build output; it is now an export of the xbf, and meshes are never sources | 2026-10-19 (`@xbf`, one file per decorator); export plan 2026-10-21; law 4 |
| The sidecar (`.xbf.json` and `.step.json`); provenance is never written | Jake 2026-10-20 item 6; commit cab4a958e; laws 6–7 |
| The mates vocabulary (`kinematics=`, revolute/slider/cylindrical/fastened/couple, MateTarget, AssemblyHelper) | Jake 2026-10-20 item 4 ("nothing of cadgen's mates vocabulary survives"); commit 81079ea22 |
| Animation and clips in the CAD document (JS modules, `animation=`, clip DSL); a GLB carries motions | Jake 2026-10-20 item 5; amended 2026-10-22 (motions as definitions; export-plan amendment); 4cbd3d361, b39b1069d; law 8 |
| `materials=`/finishes decorator arguments | Materials spec 2026-10-20 ("NO decorator argument"); README §Materials |
| The material card system, attribution, `.add()` registration | Materials merge ab74bface 2026-10-21 (carry-over item 6; README: "nothing to register") |
| The `@snapshot` decorator, `build --snapshot/--pose/--part`, `animate=`/`--animate` | Jake 2026-10-22 amendment; commit 0b35d620a |
| Every decorator option except `out` | Same amendment; README "Decorators carry only out" |
| Mesh tolerance knobs (`--mesh-tolerance`, `quality.tessellation`) | Jake 2026-10-20: FreeCAD default, configurable nowhere (README §Mesh; brepmesh-fault-measurement) |
| Per-copy names and prototypes (`compound_from_instances`) | Geometry sharing 2026-10-21 (carry-over 13; README §Placements) |
| The `cadgen xbf` prefix, replaced by `cadgen build/export/snapshot/serve/status/stop/daemon` | Closure plan §0, LOCKED 2026-10-22 (`where` is already top-level, a30592c0a) |
| `step compile`, `store info/why/forget/gc`, CADGEN_CACHE_DIR, CADGEN_STORE_MAX | Follow from the first two rows |
| Implicit body detection (`@memo` is explicit) | Rejected by Jake 2026-10-22 (carry-over 16) |
| Teaching errors for retired commands | Standing "no backwards compatibility" rule; #568 already removed `_RETIRED` |
| Chromium for document stills (OCCT draws them; the page stays for mesh files) | Jake 2026-10-21 (occt-snapshot-spike); the cut-over deletes `snapshot.renderer()` |
| Blocked, not dropped: loading one body at a time from the file after a restart | BinXCAF has no fixed point (closure plan §4; morning report item 3) |

## D. Possibly missed (neither decided nor ported)

1. **Migration base.** The work head is #568 (unreleased 0.8.0): 816 commits past 0.7.24, packages/core −33k/+12k
   lines, packages/ui ±17k, 206 files changed under `models/`. main has since shipped 0.7.25 (Pencil and Shapes,
   entity Measure, single-choice pick menus, tool keys; it also *removed* `measurements` from snapshot and Quick Edit)
   and 0.7.26. Three consequences:
   - The xbf renderer lacks 0.7.25's UI, which breaks the "UX equals STEP" rule.
   - xbf-render re-adds `--measurements`, which 0.7.25 removed.
   - Porting the slice onto main looks feasible: the xbf UI's 33 imports resolve on main except its 3 core libs and
     `host/context.js` (#568-only). Their APIs are unverified.
2. **No CI has ever run** on bbce82504 or xbf-render (no PR, no Actions run). Failures already known:
   - Engine launches without `-P` (3 sites, plus lane B's render process).
   - Direct `os.replace` calls (a policy test).
   - The torus mesh budget (25592 > 25568).
   - `previous._libraries` misses `cadquery-ocp-novtk`, so on a normal install the OCP version never enters the
     closure key, and an OCP upgrade does not invalidate xbf caches.
   - STEP export is not byte-stable from run to run (header timestamp, styled-item order), although the README says
     it is.
   - X-ray regressed under OCCT PBR (ΔE 7–15); that is an open decision.
3. **Worker sprawl.** Each script gets its own warm worker. There is no total cap and no idle-worker retirement; only
   the whole daemon exits, after an idle hour, and the per-worker cap (a quarter of RAM, at least 2 GiB) is checked
   only after a build. The old pool admitted work against a memory budget. Projects made of many part scripts
   (tendon_hand has 113 models) will hold one memo per script.
4. **Child outputs.** Under the old pipeline a parent build rewrote stale children's declared files. In the engine a
   child's files are written only when it is built as a target (README). URDF mesh sets and per-part STLs for printing
   now need `cadgen build a.py b.py …`, and no skill says so.
5. **Unseen inputs.** C-level reads other than `import_step`/`import_brep` (for example `bd.import_stl`, or OCCT
   readers called directly) are not keyed; the old native tracer heard every open. The cad skill's line "every file a
   build opens is an input" becomes false for those.
6. **Wheel contents.**
   - `engine/page/` is not in package-data, and the serve page also needs `node_modules/three`.
   - Lane C's `render/studio.hdr` must be added.
   - `check-wheel-contents.sh` checks nothing of the engine.
   - `_runtime/browser` stays: mesh, DXF and robot snapshots and the viewer need it. So does Playwright, which means
     the stills note's "drop Playwright at the cut-over" cannot happen while the page draws mesh files.
7. **Orphaned store.** The old store stays on users' disks (`~/.cache/cadgen`, `%LOCALAPPDATA%\cadgen`, capped at
   20 GB by default). Nothing removes it; add a one-time sweep or a release note.
8. **Daemon socket.** The engine keeps its socket in `$TMPDIR/cadgen-engine`, which is a shared `/tmp` on Linux. Unix
   connections have no auth (a token exists only on Windows), and the directory's owner is never checked. The old
   daemon had an auth key. Low risk.
9. **Cold reopens.** The first edit after a restart is a cold build (tom: 146 s). A large foreign STEP in the viewer is
   parsed again after every daemon restart, where the old store kept its compile; the mitigation is
   `cadgen export x.step x.xbf`.
10. **Fabrication meshes** share the display tolerance. Measured on a Ø100×20 cylinder STL: 37 segments, against about
    34 at the old default, so no regression on that case. There is no knob for a finer print mesh (by decision).
11. **GLB fixes owed** from the render track: transmission plus alpha fading twice, anisotropy without UVs.
12. **Docs drift.**
    - `models-xbf/README.md` still describes an `.xbf.json` sidecar.
    - The docs hero renders the planetary gear from the old component package plus its sidecar clip
      (`public/hero/planetary`, `sync-hero-step-assets.mjs`, and `check-hero-step-assets.mjs` inside `npm run check`).
    - The README telemetry section (3 lines) and the privacy page (9 mentions) describe build-daemon counts.
13. **Keep when rewriting `cadgen/__init__`:** the engine's font guard and kernel-import recorder come from
    `cadgen/__init__`, because the engine is a cadgen subpackage. The engine verbs are only lazily exposed for now
    (not in `__all__`), and `status/stop/daemon` collide with the old `cadgen daemon` names until the old daemon goes
    (morning report item 5).

## E. Dependent surfaces to update at migration

- **Skills.**
  - **cad:** SKILL.md and all 13 references. Every command changes: `python model.py`, `cadgen stl|3mf|glb build`,
    `cadgen step|stl snapshot`, `cadgen snapshot --job`, `read_scene().resolve`, `cadgen store why`, `--force`,
    `cadgen daemon status`.
  - **dxf** (SKILL.md and generator-templates): `@dxf`, `store why`, `--force`.
  - **engineering-drawing:** `read_step`.
  - **urdf, sdf, srdf:** `snapshot --job`, the viewer.
  - **cadgen-free skills** (sendcutsend, dfm, gcode, dfam-check, bambu-labs) consume STEP/STL from `$cad`, so `$cad`
    must tell agents to declare `@step`/`@stl` or run `cadgen export`.
  - No skill mentions the engine today.
- **MCP:** `mcp/server.py` (extensions and 3 description strings), `tunnel.py` routes, `apps/mcp` renderer
  registration. The `cad_show` description lists 7 formats.
- **Viewer:** the files in B1; drawings (B6); the `/__tess_cache` route; the STEP renderer (227 files) and 23 core
  modules that name the sidecar, tessCache or stepParameter, to keep or retire. The launcher contract (`viewer`,
  `--port/--new/--detach`) is unchanged.
- **Docs and prose:**
  - Docs site: content.ts ("STEP as the main output"), the hero (D12), the privacy policy.
  - README: telemetry text; the Node 20+ badge, since exports no longer need Node at runtime.
  - packages/cadgen README: rewrite its laws from engine/README.md.
  - Delete STORE.md and SNAPSHOTS.md. Review core/docs `tube-skins.md` (animation deformation) and
    render-pipeline.md.
  - CONTRIBUTING (store, daemon, CADGEN_CACHE_DIR, zig/Node bundle steps) and apps/web/README.
- **Tests to rewrite or delete:**
  - main's 166 top-level cadgen test files: about 131 exercise the old pipeline; 13 robot, 5 drawing and 17 generic
    files stay.
  - 31 viewer and MCP files (the STEP routes).
  - 20 skill tests: cad 15, dxf 5.
  - About 12 global tests: sidecar_and_package_layering, documented_commands, cli_main_guards, cache_root_sync,
    skill_requirements, python_test_runner_isolation, no_hash_seed_reexec, snapshot_viewer_theme_parity,
    cli_stream_contract, artifact_version_policy, node_builder_bundles, render_contract_sync.
  - `tests/browser/viewer-e2e.mjs`.
  - The engine suite (17 files, 6.3k lines on the work head; 20 on xbf-render) becomes the core suite.
- **CI:**
  - `select_checks.py`: the `ENGINE_CODE` rule is "final, a leaf" (packaging plus tests naming it), but after the
    cut-over the engine reaches skills, viewer, web and MCP, so it must become CADGEN_CONSUMERS. Remove the
    old-module rules.
  - `test.yml`: an xvfb step on cadgen-linux (`xvfb libgl1-mesa-dri fonts-dejavu-core`); the Windows job.
  - `bundle.sh`: drop `--node` and `--native`.
  - `check-wheel-contents.sh`.
- **Plugin manifests** (`.claude-plugin`, `.codex-plugin`, `.cursor-plugin`, gemini-extension, plugin.json,
  mcp.json): the launch command is unchanged, and "STEP, GLB, STL or 3MF" stays true. Only the release stamps change.
- **`models/` corpus:** 362 decorated files in 18 projects. 14 `@xbf` files in 6 projects are ported, plus tom (from
  ../tom-cad). Of what is left, 74 files use `kinematics=`, `materials=` or `animation=`, and 8 `@dxf` and 1
  `@eng_drawing` have no engine home (B5). The biggest unported projects are tendon_hand (113), f1 (33), juno (29),
  assemblies (27, which include the docs hero's planetary gear) and radial (19).

## F. Sequence

1. **Close the render track and get CI (1–1.5 agent-days, Opus/high).** Merge C, then G, into xbf-render
   (regenerate `look.json`), then xbf-render into the work head. Fix D2's failures and B15. Open a draft PR so the
   Linux (xvfb) and Windows jobs run. Decide X-ray.
2. **Pick the base (1–2 days).** Merge main (0.7.25/0.7.26) into the engine branch, or move the engine and slice onto
   main without #568's old-pipeline rewrite. Port Pencil, Shapes, Measure and the pick menus to the xbf renderer.
   Settle `--measurements`.
3. **Unblock the skills in the engine (2–3 days).** `--job` packets and one suffix dispatcher (engine kinds plus the
   old robot/DXF door, B4/B6), the ref resolver (B3), the CLI envelope (B7), `@dxf` re-homed (B5), packaged models
   (B12), focus/hide (B8).
4. **Cut over the viewer and MCP (2–3 days).** STEP through the engine's `serve` (B1). Relay the engine routes under
   the viewer's origin and the tunnel; `.xbf` in MCP (B2). The DXF cache in memory. The viewer's kernel jobs on the
   engine daemon.
5. **Names and laws (about 1 day).** Top-level verbs, delete the `xbf` level, the package README laws from the engine
   README, telemetry hooks in `cadgen.cli` and `xbf` in the receiver (B14).
6. **Prose (about 2 days).** Skills (E), docs site (hero as a GLB with a baked motion), README, CONTRIBUTING. Run the
   blind A/B skill harness on the rewritten cad skill.
7. **Port the corpus, then delete the old pipeline (3–5 days; corpus per project, Sonnet/medium, in parallel).** Fold
   models-xbf back into models/. Port the 12 remaining projects. Then delete the store, daemon, sidecar, tessellation,
   Node builders, native tracer, old parsers (and the STEP renderer, if it retires) with their ~150 tests. Update
   select_checks, test.yml and the bundle.
8. **Release (half a day, Jake's word).** Bump, check the wheel, run one main-thread benchmark batch old vs new
   (standing rule), and write the release note for the orphaned store.

## G. Jake's migration brief (2026-10-22, verbatim scope) and the gate

- Migrate the engine, remove the old engine and its dependencies.
- Migrate tests and CI.
- Migrate the entire sample models directory (problematic or slow models: leave them and flag them; prune
  some); xbf for everything, no nested decorators, memo only where high impact, the new skill guidance.
- Other plumbing: the docs hero becomes an animating GLB.
- Redesign the A/B benchmark for xbf files; then one overall benchmark, main-branch engine vs the new engine,
  builds/edits of the same models (xbf vs STEP as the user-facing output).
- Push latest to the PR and update its description.
- CI: not required 100% green while features iterate; fix low-hanging fruit (Windows integration, perf
  regressions diagnosable through CI); trim CI bloat.
- Permission: execute once the in-flight changes have landed and the engine is stable and feature complete
  (urdf, dxf, eng drawing covered). Gate: viewer cut-over, X-ray linear, build laws, references, pool +
  progress, telemetry, snapshots of every kind, couplings, DXF, reads + the three bugs, all merged and green.

### Status 2026-10-22 (overnight, running)
- Working branch pushed at 6a46561f4: + viewer cut-over (3cc0490c8), X-ray linear (e20b41c75), build laws
  10–12 (4760932e6), telemetry (6a46561f4). Demo viewer :5251 on 4760932e6+ (server and daemon restarted;
  models rebuilt with `python model.py`).
- Running: xbf-select (references), xbf-pool-progress, xbf-snapshot-kinds (Chromium deleted after it),
  xbf-dxf, xbf-couple. Queued: invisible reads + 3 bugs after select.
- Verbs step note: `cadgen export` takes `.xbf`/`.step` sources only (drop `SCRIPT.py::MODEL`); the
  telemetry test's command leg becomes an export of a built xbf.
- Gate caveat found: earlier browser-gate passes ran against a stale viewer client (bundle --check skips the
  viewer stage); every gate now runs after a full bundle.

### Migration log (2026-10-22, overnight)
- Gate held at 33d65530f (every lane merged green; final cut: the all-kinds snapshots, Chromium and Playwright
  out of the engine). Import-time hand-off: `python motorbike.py` no-op 0.17 s, edit 0.42 s end to end.
- migration-1 (deletion, top-level verbs, laws, CI rules) running. corpus-1 DONE @ ca7566333: juno (29
  models, 27 joints, SRDF states as configurations, one @memo `links()` 56→31 s cold) + assemblies (23 models,
  planetary gear hero as @xbf+@glb with a locked drive coupling and a 6 s meshCycle motion; mars rover 27
  joints/7 couplings/4 configurations; nothing flagged). corpus-2 (hypercar + f1) and corpus-3 (tendon_hand +
  radial) running.
- Engine gaps found by the ports (follow-ups, not blockers): raw OCCT `BRepPrimAPI_MakeCylinder` is not a memo
  op (bodies using it are never kept; use bd.Cylinder); `connect_to` on a sub-assembly with its own joints
  silently leaves its base part on the ground when posing; a derived file (3MF) is rewritten when its xbf
  changes even if its bytes are identical; the sculpted humanoid's worker holds 5.8 GB after a cold build.
- Pre-existing test notes: `test_no_daemon` viewer case dies with the old daemon (migration-1);
  `test_flex_followups` tearDown flaky under load.
- migration-1 MERGED (d1ae594a3: −167k lines, 790 files; verbs top-level; laws; CI rules + Xvfb), fixes
  78bbc446f (pyrefly platform imports; site-packages as library roots), MAIN MERGED (fae8be08f → 69d4be720;
  conflicts listed in the merge message; Pencil/Shapes/pick menus on xbf). Not ported from main: entity Measure
  and double-click look-at (xbf-measure-lookat agent running). Prose step (migration-prose) running.
- corpus-2 DONE @ 35996dbce: hypercar (706 parts, 13 memo stages, doors as a coupling + motion in the GLB;
  cold 186 s vs old ~240, edit 4.45 s vs old 5–7, no-op 1.3) and f1 (833 parts, 12 stages, DRS + steering
  joints with baked motions; cold ~9 min = the builders' own kernel work; edit 6.5 s). Engine gaps: f1 gearbox
  casing stores one edge's continuity entry elsewhere after a replay (bytes differ, names/geometry identical;
  evidence corpus-2/f1-keep/); `export --pose` on the hypercar hangs >20 min in the documents worker while the
  posed snapshot takes 2 s (BUG to fix). corpus-1 + corpus-2 merging into the working branch.
- Posed export hang FIXED (4db821c3a: the STEP writer's deterministic reorder made 2.3M kernel calls; now
  only the file's tail is renumbered: hypercar posed export 6.3 s, as-built 7.5 s, f1 posed 12.6 s).
  Entity Measure + look-at ported to the xbf renderer (399284ae7). Prose MERGED (1d376f69f: skills, README +
  docs homepage, privacy, hero = planetary.glb 179 KB with the meshCycle clip, contributor docs; global suite
  fully green). Five small fixes in progress (report crash on an unkeyed read, snapshot leaving OUT on failure,
  Quick Edit label, dead hero code, per-project README verbs).
- corpus-3 DONE @ 40f8c0765: tendon_hand 56/112 models ported (56 FLAGGED: 25 read the frozen palm STEP outside
  git, 8 the 250 MB native hand, 7 clearance studies on the old step_scene, 6 other frozen STEPs, 8 generated
  validation JSON, 1 broken import, 1 fails on 0.03 mm fillets); radial = one model, 18 memo stages, locked
  `drive` coupling, `running` motion (cold 3,195 s = kernel work; edit 48.8 s; GLB not declared: 140 MB/138 s).
  Engine gaps: (1) warm rebuilds of bodies with unkeyed calls (barrels 132, propeller) write different bytes
  from cold — the report should name uncached calls; repro corpus-3/stageprobe/; (2) couple is linear only
  (slider-crank/cam need a motion); (3) function-of-time motions bake huge GLBs; (4) LinearJoint pairing sharp
  edge (paired RigidJoint must keep the parent's orientation); (5) EVERY COMMIT REWRITES EVERY XBF (the closure
  records the commit; bytes differ only there) — decision for Jake; (6) no tube deformation, no explode plan.
- corpus-5 DONE + merged (65f81706e): lyra (18 models, 16 joints, 7 SRDF states, 5 motions in one GLB),
  falcon_heavy (3 models, 2,142 placements), branding, all 8 drawings, the @eng_drawing example on read_xbf,
  tests; only `joint_playground.urdf` flagged. STEP NAUO ids now renumbered per export (f7753ba77). bench-xbf
  merged (eee54d062: scripts/bench/corpus_bench.py + compare.py + old-engine-numbers.json; juno's old cold is
  35 s, not 28.5). corpus-4 DONE @ c3083d5a5: w16 (794 parts, 13 stages, 174 joints, `crank` motion baked,
  cold 1,238 s, edit 66 s) and f14d (903 parts, 9 stages, cold 644 s, edit 3.8 s); nothing flagged.
  Engine gaps from corpus-4: (1) namespace packages (lib/ without __init__.py) never re-imported — edits
  unseen (BUG, fixing); (2) raw OCCT calls uncached silently (report to name them, fixing); (3) a mutated
  module value makes the closure "changed" every build (report to name the binding, fixing); (4) cold w16
  builds differ in 3/794 parts (parallel-boolean drift, known); (5) a replayed body writes BRep entries in a
  different order than cold (f1/f14d; known determinism gap); (6) doc gap: a prismatic pair's RigidJoint must
  be a pure translation at the axis origin. f14d's airframe stage peaks ~16 GB = the default worker cap.
- PR: #568 "cadgen v0.8" (branch claude/relaxed-knuth-hhrtva) is an ancestor of the working branch → the push
  is a fast-forward + new description.
