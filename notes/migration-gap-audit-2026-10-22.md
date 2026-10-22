# Migration gap audit: PR #568 (cadgen 0.8) against main 0.7.26 — 2026-10-22

The question: what can main's user do that the migration's user cannot, with no decision behind it?

- **main:** origin/main 2f793659f (0.7.26). It is an ancestor of the PR head, so the diff is the whole migration.
- **PR head:** `claude/relaxed-knuth-hhrtva` at **e67174446** (later than cbfb3e1c6).
- **Surfaces:** the viewer, the CLI and Python API, the MCP CAD app, the skills, and the API/docs site.
- **How:** code on both trees, every `--help` on both, and a browser pass on the PR head.
- **Browser pass:** the head's own `cadgen viewer` on port 5391, with an isolated engine daemon and state folder. I opened a built `models/motorbike/XBF/motorbike.xbf`, a `cadgen export` of it to `.step`, and a built `models/juno/juno.urdf`.
- **Runtime checks:** phone layout, Preview, reload, Copy Prompt, a `package://` URDF, DXF stills, and `--json` failures.
- **Labels:** "runtime-confirmed" means I saw it happen on the head. "Code-confirmed" means I read both trees.

Classes: **kept**, **changed by decision**, **dropped by decision**, **DROPPED UNNOTICED**. Paths are relative to the repo root. "main:" is the 0.7.26 tree; "head:" is e67174446.

---

## 1. The answer

### 1a. The two gaps found earlier today (still open at the head)

**K1. A STEP or xbf view is not restored on reload.** Tree expansion, hidden parts, isolated parts and the pose are lost.
- Runtime-confirmed. I hid `seat`, expanded `front_wheel`, posed `head_tube_axis` to 20° and chose X-ray, then reloaded. The camera and X-ray came back. The hidden part, the expansion and the pose did not. The tab store held `renderer: {}`.
- main: `packages/ui/src/renderers/step/workbench/stepViewSlices.js:10-67` (slices `tree`, `pose`, `largeFile`).
- head: `XbfRenderer.jsx:189-196` passes no `rendererState`.
- `largeFile` goes with LOD and topology loading (decided).

**K2. Stills ignore hidden and isolated parts.** `--focus`, `--hide` and a job's `selection.hide` / `selection.focus` are gone.
- main: `packages/cadgen/src/cadgen/_internal/snapshot_door.py:201-202`, `snapshot_core.py:1228`.
- head: `engine/snapshot.py:60` has no hidden or isolated set.
- Two related halves:
  - The robot viewer still sends `hide` (`RobotRenderer.jsx:176`), and the stills ignore that too.
  - The viewer no longer puts hide/focus into Quick Edit packets (see N2).

### 1b. New DROPPED UNNOTICED items

Most user-visible first. Some were in the morning scope note's hole list (B4, B7, B8, B10, B15). None was fixed or decided; I flag those.

**Viewer and the in-chat CAD app**

**N1. No build progress and no "update failed" alert on an open STEP or xbf.** (Scope note B10 named this. Only the `cadgen status` half landed.)
- **main:** while `python model.py` rebuilt an open model, the view said "Building · phase n/total". A failed build put a non-blocking "Couldn't update the model" card, with the error, over the last good model. This worked in the web viewer and in the CAD app.
- **main evidence:**
  - `packages/ui/src/renderers/step/StepSurface.jsx:737` (`buildViewerEditAlert`), `:1574-1576` (progress, `editPending`)
  - `packages/cadgen/src/cadgen/viewer/preview.py:1-7`
  - `packages/core/src/client/editingPreviewFeed.js`
  - `apps/mcp/src/host/sync.ts:64-126` (`previews`)
- **head:** the engine serves `building` and `failed` (`engine/serve.py:8-14`), but nothing passes them on:
  - the relay forwards only `after` and `wait` (`viewer/xbf.py:205-217`);
  - the page reads only `version` and `patch` (`packages/core/src/lib/xbf/xbfDocument.js:192-197`; `useXbfScene.js:80-90`).
  - The model just refreshes when a build lands. A failed rebuild is invisible.
- Code-confirmed by me and three auditors.
- **Size:** small to medium, 60–120 lines. Relay `progress=`, return `building`/`failed`, and map them to the load progress and an alert, in the viewer and in the CAD app's sync.

**N2. Quick Edit packets from STEP or xbf carry no pose, no hidden parts and no isolated parts.**
- **main:** each view in a note recorded the pose (Position off its default) and the hidden and isolated parts. The agent's replay showed what the person saw.
- **main evidence:** `StepSurface.jsx:1593-1598` (`viewState: { kinematics, animation, hide, focus }`).
- **head:** `XbfRenderer.jsx:189-196` passes no `viewState`.
- **Runtime-confirmed.** I hid `front_fender`, posed `head_tube_axis` to 20°, and pressed Copy Prompt with the clipboard stubbed in the page. The packet's one job held only camera, display and output. It had no pose, no `selection.hide` and no `selection.focus`.
- The pose half has a fix in flight (section 2). Hide/focus has none on the viewer side. The engine agent's K2 fix needs this input, so K2 alone will not close it.
- **Size:** small, about 10–30 lines plus the snapshot side (K2).

**N3. `cad_view` reports no selection for a STEP or xbf view, and screenshot-to-chat drops the selection's references.**
- **main:** `cad_view` told the agent what the user had selected, as references it could quote back (`/p/a.step#…`). A screenshot sent to the chat named the selected parts and faces.
- **main evidence:**
  - `StepSurface.jsx:1580-1589` (`promptReferences`, live state)
  - `apps/mcp/src/host/live.ts:20-26` reads `state.selection`
  - the tool text promises it: `packages/cadgen/src/cadgen/mcp/server.py:387, 406`
- **head:** `XbfRenderer.jsx:172-176` reports only `{ selected: [ids] }` and passes no `promptReferences`. So the shell reports `selection: []` (`kit/shell/useRendererShell.js:575`), and capture sends `references: []` (`:441-455`). `apps/mcp` is unchanged and still reads `selection`.
- Code-confirmed by three auditors and me. I did not run the CAD app.
- **Size:** small, 5–15 lines (pass the chips' references as `promptReferences`) plus a test.
- Lesser half: the live state also lost hidden and isolated part ids, and the live `select({selectors})` command is declined (`xbf/tools.js:13-15`). No host in this repo calls either, so this is low.

**N4. A URDF or SDF that names its meshes by `package://` no longer opens.** The whole robot is refused, in the viewer and in `cadgen snapshot`.
- **main:** a description inside its ROS package folder found its meshes. `package://NAME/...` resolved to the nearest folder named NAME. This was the fix for issue #611 (PR #621, 0.7.23).
- **main evidence:** `packages/core/src/lib/urdf/meshAssetUrl.js:58-76`, `skills/urdf/references/meshes.md:32`.
- **head:** `packages/cadgen/src/cadgen/robot_payload.py:462-467` (SDF `:821-827`) and `engine/formats/robot.py:335-339` refuse with "the viewer resolves no package://".
- **Runtime-confirmed.** `/__cad/robot` answered with that error, and `cadgen snapshot` exited 2.
- Memory recorded it as a follow-up on 10-08 (pr568-occt-mesher-review.md:210). Nothing decided it.
- **Size:** small, about 25–30 lines of Python, a test, and the skill line put back.

**N5. Measurements on a STEP or xbf are no longer undo steps.**
- **main:** taking, deleting or clearing a measurement was a step of ⌘Z / ⇧⌘Z, like ink.
- **main evidence:** `StepSurface.jsx:1609` (`measure.history.current = shell.frame.history`).
- **head:** the xbf renderer never wires it. The other renderers get it through `kit/tools/sceneTools.jsx:43`. Code-confirmed.
- **Size:** one line and a test.

**N6. Ink is no longer cleared when the model rebuilds.**
- **main:** ink drawn over the old revision was discarded with its undo history, so Undo could not put old ink over the new model.
- **main evidence:** `StepSurface.jsx:913-918` (`frame.annotation.discard()`).
- **head:** nothing calls `discard()`. Code-confirmed.
- **Size:** about 5 lines on a version change.

**N7. `cadgen viewer` no longer starts the build daemon at launch.**
- **main:** the first build or STEP open after launching the viewer found a warm daemon.
- **main evidence:** `packages/cadgen/src/cadgen/viewer/main.py:436-443`, `:750-756`.
- **head:** no prewarm. In the browser pass the first open of the 6.3 MB `motorbike.step` read for about 25 s.
- **Size:** about 10 lines. Benchmark first (no-silent-perf-regressions).

**N8. The SDF sheet lost its "Unsupported geom." count.** Minor.
- **main:** it counted the visuals and collisions the viewer could not draw (`robot/SdfSection.jsx:62`).
- **head:** removed.
- **Size:** about 10–15 lines.

**CLI and stills**

**N9. `cadgen snapshot --job` is gone.** (Scope note B4 called it a hole to fix.)
- **main:** one call replayed a whole Quick Edit packet or a saved view set. That meant several views, per-view display, pose and selection, several outputs, and a TARGET to aim the same views at another file.
- **main evidence:** `snapshot_core.py:64-132`, `_internal/snapshot_door.py:223-225`. Main's packet route said agents render it "with `cadgen snapshot --job <file>`" (`viewer/prompt_files.py:1-10`). Jake designed packets for `--job` replay (memory prompt-views-sketch-jobs.md).
- **head:** one view per call, via `--annotation PACKET --camera authored`, with display and pose passed by hand (`skills/cad/references/snapshot-review.md:121-139`). That substitution was the migration agent's, not Jake's.
- **Size:** medium, about half a day to a day (a loop over jobs).

**N10. The job output settings went with `--job`.** These are a transparent background, `padding`, `tightFrame`, `renderScale` (1–3× supersampling) and several outputs per job.
- **main evidence:** `snapshot_core.py:100-109`.
- **head:** framing padding is fixed at 0.04 (`engine/render/occt.py:65`). Nothing reads `transparent` or `renderScale`. The viewer still writes `padding` and `tightFrame` into every packet, and nothing reads them (`viewer/prompt_files.py:147`).
- **Size:** small each, 30–100 lines. Transparency needs a check that OCCT's readback has alpha.

**N11. A DXF Quick Edit can no longer be drawn back.**
- **main:** `cadgen dxf snapshot --job` drew the plan-view window the person was looking at, with their ink on the sheet. The agent saw what the user circled on a drawing.
- **main evidence:** `packages/ui/docs/cad-renderer.md:325-349`, `snapshot_cli.py:1413-1470`.
- **head:** `--camera`, `--annotation` and `--measurements` are refused for drawings (`engine/render/drawing.py:25-46`). **Runtime-confirmed:** "--camera does not apply to it". Spec §3 planned annotation for every kind.
- **Size:** small to medium, about a day.

**N12. The `--json` failure envelope is gone, and `cadgen export` has no `--json`.** (Scope note B7.)
- **main:** under `--json`, a failure always printed `{"ok": false, "error": …}` on stdout and exited 1 (`_internal/cli_from_function.py:402-431`).
- **head:** **runtime-confirmed.** `cadgen snapshot … --json` on a missing file or a bad `--display` printed nothing on stdout and exited 2.
- **Size:** small, about 60 lines in `engine/cli.py`.

**N13. `--mode list` is gone.** (Scope note B8.)
- **main:** it listed every drawable part with refs, names and bounds, for any input (STEP, mesh, robot), without rendering (`snapshot_door.py:226`).
- **head:** only the Python `read_xbf` / `read_step` trees, which cover documents only.
- **Size:** small, about 80 lines.

**N14. OUT can no longer be a folder.** (Scope note B8.)
- **main:** a folder OUT got a timestamped file name (`snapshot_door.py:211-214`).
- **head:** OUT must be a `.png` (`engine/snapshot.py:533-534`).
- **Size:** about 20 lines.

**N15. Windows: engine processes may each open a console window again, the #597 regression.** (Scope note B15.)
- **main:** the 0.7.21 fix gave background processes a console with no window.
- **head:**
  - the daemon starts with `DETACHED_PROCESS|CREATE_NO_WINDOW` (`engine/client.py:50`);
  - workers, readers and render processes have no flags (`engine/daemon.py:149`, `reader.py:344`, `render/service.py:525`);
  - the head's own `viewer/main.py:515-527` says `DETACHED_PROCESS` re-creates #597.
- CI cannot see windows. Not run.
- **Size:** small (copy the viewer's detach flags), plus a Windows check.

**Telemetry**

**N16. Snapshots of DXF, URDF, SDF and SRDF are no longer counted.**
- **main:** every snapshot kind was counted with its outcome and seconds (`snapshot_cli.py:1856-1879`).
- **head:** the kind map knows only xbf/step/glb/stl/3mf/cadz (`engine/api.py:559-560`, `:647-655`). Everything else is "other", which `_internal/engine_telemetry.py:161` skips.
- **Size:** about 4 lines and a test row.

---

## 2. In flight: decided or fixed on a branch, not in the PR head

| Item | Where | State |
|---|---|---|
| Preview's floor at the model's lowest point (Jake wants it; the head stands it at Z=0) | `claude/568-ui-port-xbf` 8e63d2701 | local branch, not merged |
| Quick Edit view carries the pose (`jointValues`): half of N2 | same branch, 9ff303d1b | not merged; it adds no hide/focus |
| Ink "touches" name the face or edge, not just the part (main 0.7.25's `inkTouches`; the head names only the part, `annotation3d.js:530-537`) | same branch, 9ff303d1b | not merged |
| K1, rendererState | UI agent (memory) | no commit seen |
| K2, hidden/isolated in stills and packets | engine agent (memory) | no commit seen |
| Dead `kit/tools/ToolColumn.jsx` | same branch, f641402f2 deletes it | not merged |

---

## 3. Surface tables

### 3.1 Viewer: main's STEP renderer → the head's xbf renderer (draws STEP and xbf)

| Feature | Class | Note / evidence |
|---|---|---|
| Strip: Select (+filter), Pencil, Shapes, Measure (+filter), Explode, Clip; Projection, Display, Preview | kept | browser, xbf and STEP alike |
| Select modes All/Parts/Faces/Edges/Position, Group faces/edges | kept | `XbfSelectionModes.jsx` = main's `SelectionModes.jsx` |
| Display modes Solid/Shaded/Parts/X-ray/Hidden line/Wireframe, Grid | kept | browser |
| Clip axis, Part colors, Reverse, slider, Reset; caps | kept | browser |
| Explode (2+ parts), Reset | kept | `xbfScene.js` `setExplode` |
| Model tree: filter, ranked search, virtual rows, isolation bar + Exit, Show all, row Isolate/Hide, double-click isolates | kept | `XbfModelTree.jsx` (port of `ModelingTree.jsx`) |
| Part, topology and model menus (Copy Reference … Zoom to selection) | kept | `xbfMenus.js` vs `file-view/stepMenus.js` |
| Double-click: look at face/edge/ink, isolate, empty space leaves isolation | kept | ink look-at is the shell's |
| Measure readings, chips, cards, select/delete a measurement | kept | `XbfMeasure.jsx` |
| Quick Edit chips and cards (area, radii, length, sweep, size, volume; mass added) | kept | `xbfChips.jsx` |
| Position: Pose row, configurations, sliders, couplings, knobs; pose kept across a rebuild with unchanged joints | kept | `XbfPositionSection.jsx` |
| Position for a STEP file | dropped by decision | STEP carries no joints (Jake 10-20); no sidecar |
| Routines, Animation section, playbar for STEP | dropped by decision | animation is GLB-only |
| Feature recognition rows | dropped by decision | Jake approved 11baa4afc; never wired on main (`useStepModeling` had no caller) |
| LOD, coarse-first, detail refinement, completed-package cache | dropped by decision | Jake 10-11, memory one-glb-per-part-plan.md |
| Per-mode display settings | dropped by decision | listed |
| Tube skins | dropped by decision | listed |
| Reload restores tree/hidden/isolated/pose | **DROPPED UNNOTICED** | K1 |
| Prompt view state (pose, hide, focus) | **DROPPED UNNOTICED** | N2 |
| Live selection for agents; screenshot references | **DROPPED UNNOTICED** | N3 |
| Build progress, failed-build alert | **DROPPED UNNOTICED** | N1 |
| Measurements in undo history | **DROPPED UNNOTICED** | N5 |
| Ink discarded on rebuild | **DROPPED UNNOTICED** | N6 |
| Ink touches name faces/edges | DROPPED UNNOTICED, fix in flight | section 2 |
| Error card on a failed reference copy ("Could not copy reference") | DROPPED UNNOTICED (trivial) | head swallows it: `XbfRenderer.jsx:398` |
| Host `selectReference` command | kept in effect | declined by xbf, but no host in this repo sends it |

### 3.2 Viewer: kit, shell, other formats, backend

| Feature | Class | Note |
|---|---|---|
| Navbar, theme with resolved-mode icon, language, telemetry menu, home, explorer, recents, routing, drag-drop | kept | shell, library, tab-store, i18n byte-identical |
| i18n, five languages, 180 keys | kept | locale files identical; key use counts identical |
| Phone layout and More menu (Clip, Projection, Display, Preview) | kept | browser at 375×812 |
| Keyboard: tool keys, Escape layers, ⌘C, Delete, Undo/Redo | kept | kit identical (but N5 for measurements) |
| Preview: cover, settings, Orbit, Exit; hidden parts and pose carried in | kept | browser. Main also hid the sidebar in Preview (`RendererShell.jsx:632`), so no Position there is not a loss |
| DXF viewer: layers, Select, Pencil, Shapes, Measure, Quick Edit | kept | only comments changed |
| STL/3MF viewer | kept | byte-identical |
| GLB viewer, animation bar | kept | plus per-clip loop |
| Robot viewer: Links, details, Position, SRDF states, knobs, reload of pose and hidden links | kept | browser (juno); parsers moved to Python |
| Robot `package://` meshes | **DROPPED UNNOTICED** | N4 |
| SDF "Unsupported geom." count | **DROPPED UNNOTICED** (minor) | N8 |
| `cadgen viewer` flags | kept | identical |
| Daemon prewarm on viewer start | **DROPPED UNNOTICED** | N7 |
| `/__cad/artifact`, `/__cad/store`, `/__tess_cache`, sidecar asset | dropped or changed by decision | store, sidecar, STEP renderer |
| Home pictures of STEP files | changed (consequence of nothing on disk) | after the engine exits, STEP cards stay placeholders until opened |

### 3.3 CLI and Python API

| Feature | Class | Note |
|---|---|---|
| `stl/3mf/glb/step build` → `cadgen export SRC DST…` | kept | OUT now required |
| `step compile`, `store …`, `--force`, `CADGEN_CACHE_DIR` | dropped by decision | store |
| `step build --kinematics/--materials/--animation` on a foreign STEP | changed by decision | write an `@xbf` wrapper |
| Snapshot camera, display, grid, preview, annotation, sizes and profiles | kept | profile + width now refused; minimum 16 px |
| `--kinematics`/`--joint-values` → `--pose` | changed by decision | configurations |
| `--animation`, `--time`, `--video`, `--view-labels`, `quality.tessellation`, `timeoutSeconds` | dropped by decision | listed decisions |
| `--focus`, `--hide` | **DROPPED UNNOTICED** | K2 |
| `--job`, job outputs and output settings | **DROPPED UNNOTICED** | N9, N10 |
| DXF window and ink | **DROPPED UNNOTICED** | N11 |
| `--json` envelope, `export --json` | **DROPPED UNNOTICED** | N12 |
| `--mode list`, OUT folder | **DROPPED UNNOTICED** | N13, N14 |
| `--measurements` | added | main 0.7.25 had removed it (section 4) |
| `--mesh-tolerance`, decorator options other than `out` | dropped by decision | FreeCAD default; "decorators carry only out" |
| `python model.py` options | dropped by decision | listed |
| `CADGEN_DAEMON=0 python model.py` | kept (other route) | `--no-daemon` on verbs; `Model.build(daemon=False)` |
| `read_scene`/`Selection.resolve` → `select(source, ref)` | changed by decision | stable names; `document_hash`, `prototype_id` gone (minor) |
| Mates vocabulary, `compound_from_instances`, `label_shape/text` | dropped by decision | listed / scope §C |
| `@dxf`, `report/track`, packaged models, `eng_drawing` on `read_xbf`, `cadgen.geometry` | kept | scope holes B3, B5, B10, B12, B13 fixed |
| Windows console flags | **DROPPED UNNOTICED** | N15 |

### 3.4 MCP CAD app

| Feature | Class | Note |
|---|---|---|
| Tools and schemas (`cad_show`, `cad_view`, `cad_screenshot`, `cad_telemetry`, app tools), errors, server text | kept | `.xbf` added |
| `cad_view` selection; screenshot references | **DROPPED UNNOTICED** | N3 |
| Build feed in the in-chat view | **DROPPED UNNOTICED** | N1 (`sync.ts` `previews` removed) |
| Refresh on file change | kept | engine `/version` through the tunnel |
| Tunnel routes for store and tessellation | dropped by decision | store |
| Page/server protocol 5 → 7 | changed | they ship together |

### 3.5 Skills

| Capability | Class | Note |
|---|---|---|
| Model, assemblies, packages, inputs, exports, refs → geometry, measurement, clearance, mass | kept | rewritten for the engine |
| Repair loop, cad-brief, "snapshot after every edit", `checks/`, model catalog | changed by decision | 2dfc300b2, Jake "this all goes in the big PR" (pr568-occt-mesher-review.md:133) |
| Animation and tube-deformation references | dropped by decision | listed |
| Quick Edit replay (`--job`), focus/hide, output options, DXF replay | **DROPPED UNNOTICED** | N9–N11, K2 |
| URDF `package://` (meshes.md) | **DROPPED UNNOTICED** | N4 |
| Cadgen-free skills (bambu-labs, dfam-check, dfm, gcode, sendcutsend, step-parts) | kept | scripts byte-identical; their inputs can still be made |
| dfm's 50k–200k-triangle STL advice | changed by decision | one fixed mesh tolerance; results unchecked |
| Tests that built every model in the cad skill docs | lost guard (not a user feature) | `tests/python/skills/cad/test_documented_models.py` and `test_documented_project.py` deleted, no successor |

### 3.6 API, telemetry, docs site, manifests

| Feature | Class | Note |
|---|---|---|
| `/v1/versions`, `/v1/events`, `/v1/forget`, `/v1/health` | kept | receiver reads schemas 1–6 |
| Telemetry schema 6, kind `xbf`, process `cli` | changed by decision | spec §5 |
| Snapshot counts for DXF/URDF/SDF/SRDF | **DROPPED UNNOTICED** | N16 |
| Docs routes, privacy, README ↔ homepage parity | kept | |
| Docs hero as an animated GLB | changed by decision | Jake's brief |
| Plugin manifests and MCP configs | kept | version pins only |

---

## 4. Changed with no recorded decision (not losses, worth a word)

- **Measurements are back in Quick Edit and in stills.** That includes measurement chips in packets and `--measurements`. Main 0.7.25 removed them ("they are the person's alone"). The scope note flagged this (D1); no decision since.
- **Saved views of STEP files from 0.7.x are orphaned once.** Tab state is keyed by renderer id, and `step` became `xbf`. The no-backwards-compatibility rule probably covers this.
- **Old GLBs with `STEP_topology` and no `cadUpAxis` now open Y-up.** Decided as no backwards compatibility.
- **A large foreign STEP is read again after each daemon restart, with no step count and no cancel.** This follows from the store decision.

## 5. Checked and not a loss

- Position in Preview: main's shell already hid the sidebar in Preview, and its docs were stale.
- Feature recognition: dead code on main.
- Position copy/paste of joint values: dead on main.
- Host `selectReference` and live `select`: nothing in this repo sends them.
- i18n, phone layout and the More menu, Clip menu and caps, context menus, Select modes, chips, model tree: kept, as above.

## 6. Needs a runtime check, or uncertain

1. **CAD app:** select a face on a STEP and call `cad_view`. Expect `selection: []` on the head (N3). The CAD app was not run.
2. **Build feed:** make `python model.py` raise while its xbf is open, and run a slow build that calls `track()`. Expect no status and no alert (N1).
3. **Huge models:** main gated face/edge picking on large files. The head always picks. Hover over faces right after opening w16 or tom and watch for freezes. Not run; corpus builds take minutes.
4. **Robot refusals:** the head refuses a whole robot on any validator error, a `.dae` mesh, or a mesh path with no extension (`viewer/robots.py:17-21`). Main drew what it could. Which real descriptions this hits is unknown.
5. **STEP names:** a single-part STEP from 0.7.x with no product name. Main fell back to the file name, not `=>[0:1:1:2]`. Also check a wire-only STEP product's tree row.
6. **Windows:** console windows per engine process (N15).
7. **OCCT alpha readback**, to size the transparent-background fix (N10).
8. **dfm** verdicts on a head-exported STL of a curved part.

## 7. Leftovers from this audit

- Scratch worktree of the head, with node_modules and a built `apps/web/dist`. Remove it when no longer useful with `git worktree remove --force <path>`. Path: `/private/tmp/claude-501/-Users-jakefitzgerald-robots-text-to-cad--claude-worktrees-cadgen-warm-edits-regression-1c2012/af694b81-4881-420d-b8b0-df0499e5d2ee/scratchpad/gapaudit/branch`.
- The viewer on 5391 and its engine daemon are stopped. Auditor scratch files are under `…/scratchpad/gapaudit/{cli,skills,mcpapi,shell,shell-*}`.
- An untracked, gitignored `.claude/launch.json` sits in the auditor's own worktree, `agent-a8529962bd6f1e031`. The browser tool read the parent's launch file instead, so this one went unused.
