# UI redesign split

Branch: `claude/ui-redesign`

Worktree: `/Users/jakefitzgerald/.codex/worktrees/ui-redesign-split/text-to-cad`

Base: `24573128ceaab564f497d7c12f995c41784c9539` (`origin/main`, fetched before branching). Completed source: `d92e3d8830629999e60cc34d79ebf612b3b90ab2` (`claude/568-ui-layout`). This split treated the source worktree as read-only. Final source UI and review fixes were synchronized before validation. No models, environment directories, generated runtime assets or release versions are committed.

Implementation commit: `8b4ed42f12ec89d7a9a6c35361b1f07acab6bc91`. This report is the final separate commit. No PR was opened. Publication of the validated `claude/ui-redesign` branch is delegated to the parent task; no push to main or the engine PR branch is part of this split.

## Scope and classification

The final source diff from the base contains 1,166 paths: **236 UI, 110 mixed, 820 engine**. UI paths were ported; mixed paths were split as listed below; engine paths were excluded. The initial inventory had 1,169 paths (239 UI, 47 mixed code, 17 mixed docs, 1 lockfile, 865 engine); final classification promoted the necessary core/Python annotation and display files from the coarse engine bucket after line-level review. Main's old `lib/drawing/fillRegion.js` and its test are additionally removed as part of the native annotation cutover.

Included: centred toolbar and responsive file sidebar, Find headings, independent Select filters and Position knobs, native Annotate, Quick Edit reference chips and saved annotation/job handoff, per-mode Display settings with Projection in the same menu, independent preview/work states, single active motion, web/MCP host integration and associated documentation/tests.

Excluded: display tessellator replacement, per-part GLB/selector-table store, OCCT mesher changes, source schema migration, Python animation baking/CUBICSPLINE/tube skins, daemon/token changes, model migrations, and unrelated engine snapshot parity work. Main's geometry, tessellation/LOD, SURF, JS animation, robot parser and snapshot section implementations remain.

### Mixed paths

| Path | Resolution |
| --- | --- |
| `apps/mcp/README.md` | Port UI layout/display/annotation documentation; keep main engine and storage contracts. |
| `apps/mcp/src/App.test.tsx` | Use main protocol 5 in recovered host/UI fixtures. |
| `apps/mcp/src/host/host.test.ts` | Retain main ranged SURF/tessellation transport fixture; add new prompt-file handoff coverage. |
| `apps/mcp/src/host/server.ts` | Retained main unchanged; excluded the source engine delta. |
| `apps/web/README.md` | Port UI layout/display/annotation documentation; keep main engine and storage contracts. |
| `apps/web/docs/backend.md` | Port UI layout/display/annotation documentation; keep main engine and storage contracts. |
| `apps/web/docs/storage.md` | Port UI layout/display/annotation documentation; keep main engine and storage contracts. |
| `package-lock.json` | Regenerated workspace lock: remove Excalidraw/fonts and their unused dependencies; retain versions of remaining packages. |
| `packages/cadgen/SNAPSHOTS.md` | Per-mode display and annotation/job replay; retain main section mode and engine output details. |
| `packages/cadgen/src/cadgen/_internal/snapshot_door.py` | Add annotation and measurement flags/forwarding; preserve main section, animation and kernel interfaces. |
| `packages/cadgen/src/cadgen/mcp/tunnel.py` | Use existing asynchronous polling cadence for prompt-file status only; no engine or protocol change. |
| `packages/cadgen/src/cadgen/snapshot_cli.py` | Add annotation/job loading, absolute dependency replay and overlay refusal; replace display validation only; keep main sources, sections and JS animation. |
| `packages/cadgen/src/cadgen/snapshot_core.py` | Strict per-mode display schema and annotation keys/evaluation error handling; retain main tessellation, scene, section and process machinery. |
| `packages/cadgen/src/cadgen/viewer/http_app.py` | Prompt-file create/save/status/file routes, request-size/restart/lifetime handling; remove retired sketch and font routes; keep all engine routes. |
| `packages/cadgen/src/cadgen/viewer/main.py` | Close prompt-file browser session at server shutdown. |
| `packages/cadgen/src/cadgen/viewer/sketches.py` | Remove retired UI drawing/attachment surface and its tests/docs; no replacement engine behavior. |
| `packages/core/README.md` | Update display/prompt/annotation API descriptions; retain main source/scene/reconstruction laws. |
| `packages/core/docs/render-pipeline.md` | Per-mode public settings and clip.at; keep main LOD, runtime pose and section pipeline. |
| `packages/core/package.json` | Remove retired drawing entrypoint; normal compiled wildcard exports serve native annotation. |
| `packages/core/src/client/attachments.js` | Remove retired UI drawing/attachment surface and its tests/docs; no replacement engine behavior. |
| `packages/core/src/client/attachments.test.js` | Remove retired UI drawing/attachment surface and its tests/docs; no replacement engine behavior. |
| `packages/core/src/client/index.ts` | Export prompt-files client in place of retired attachment client. |
| `packages/core/src/common/cadScene.js` | Replace normalized clip offsets with model-coordinate clip.at; retain main scene generation and geometry. |
| `packages/core/src/common/cadScene.test.js` | Port recovered UI behavior assertions; use main parameters/description/SURF fixtures where needed. |
| `packages/core/src/common/headlessScene.test.js` | Port recovered UI behavior assertions; use main parameters/description/SURF fixtures where needed. |
| `packages/core/src/common/renderEdges.js` | Rename annotation overlay render ordering only. |
| `packages/core/src/common/renderMeshScene.js` | Add per-viewport annotation/measurement overlays to still captures; preserve main SURF, LOD, tube deformation and section runtime. |
| `packages/core/src/common/renderMeshScene.test.js` | Port recovered UI behavior assertions; use main parameters/description/SURF fixtures where needed. |
| `packages/core/src/common/renderOptions.js` | Expose owned annotation/measurement overlays during render; retain main tubeDeformationState/tubeGpuState close-up predicate. |
| `packages/core/src/common/sceneSettings.test.js` | Port recovered UI behavior assertions; use main parameters/description/SURF fixtures where needed. |
| `packages/core/src/common/source.test.js` | Port recovered UI behavior assertions; use main parameters/description/SURF fixtures where needed. |
| `packages/core/src/lib/viewer/modelRuntime.test.js` | Port recovered UI behavior assertions; use main parameters/description/SURF fixtures where needed. |
| `packages/ui/README.md` | Current UI laws and native annotation; retain main model-format/runtime contracts. |
| `packages/ui/docs/annotation.md` | Port UI layout/display/annotation documentation; keep main engine and storage contracts. |
| `packages/ui/docs/cad-renderer.md` | Current UI mechanism; keep main schema9 sidecars, JS modules, SURF/LOD and robot parsers. |
| `packages/ui/docs/drawing.md` | Remove retired UI drawing/attachment surface and its tests/docs; no replacement engine behavior. |
| `packages/ui/docs/feature-detection.md` | Retained main unchanged; excluded the source engine delta. |
| `packages/ui/docs/file-viewer.md` | Port UI layout/display/annotation documentation; keep main engine and storage contracts. |
| `packages/ui/docs/loading.md` | Retained main unchanged; excluded the source engine delta. |
| `packages/ui/docs/lod.md` | Retained main unchanged; excluded the source engine delta. |
| `packages/ui/docs/render-mode.md` | Port UI layout/display/annotation documentation; keep main engine and storage contracts. |
| `packages/ui/docs/render-types.md` | Retained main unchanged; excluded the source engine delta. |
| `packages/ui/docs/settings-ui.md` | Port UI layout/display/annotation documentation; keep main engine and storage contracts. |
| `packages/ui/docs/view-updates.md` | Port UI layout/display/annotation documentation; keep main engine and storage contracts. |
| `packages/ui/docs/viewer-host.md` | Port UI layout/display/annotation documentation; keep main engine and storage contracts. |
| `packages/ui/src/renderers/harness/stepScenario.mjs` | Keep main schema9/SURF and JS sidecar fixture; add failed-revision UI scenario, not per-part records. |
| `packages/ui/src/renderers/robot/LinkDetails.jsx` | Chip card content from main description/linkName/mesh resolvers, preserving old engine facts. |
| `packages/ui/src/renderers/robot/LinksSection.jsx` | New Find, sidebar and chip interaction; main description link tree. |
| `packages/ui/src/renderers/robot/LinksSection.test.tsx` | Port recovered UI behavior assertions; use main parameters/description/SURF fixtures where needed. |
| `packages/ui/src/renderers/robot/PositionControls.jsx` | Sidebar heading/reset behavior from source; main joints/named poses remain the model. |
| `packages/ui/src/renderers/robot/PositionControls.test.tsx` | Port recovered UI behavior assertions; use main parameters/description/SURF fixtures where needed. |
| `packages/ui/src/renderers/robot/RobotRenderer.browser.test.mjs` | Current UI flow tests against main inline XML/GLB fixture; retain four controllable joint handles, exclude source four-bar payload case. |
| `packages/ui/src/renderers/robot/RobotRenderer.jsx` | Port sidebar/filter/preview UI using main description, meshPath, SRDF groups and scene.setJointValues; no serialized robot payload. |
| `packages/ui/src/renderers/robot/RobotTools.test.tsx` | Port recovered UI behavior assertions; use main parameters/description/SURF fixtures where needed. |
| `packages/ui/src/renderers/robot/SdfSection.jsx` | Retained main unchanged; excluded the source engine delta. |
| `packages/ui/src/renderers/robot/robotMotion.js` | Retained main unchanged; excluded the source engine delta. |
| `packages/ui/src/renderers/robot/robotMotion.test.js` | Retained main unchanged; excluded the source engine delta. |
| `packages/ui/src/renderers/robot/robotReferences.jsx` | New chip cards use injected main meshPath and groupNamesByLink with description/linkName. |
| `packages/ui/src/renderers/robot/useLinkSelection.js` | Port preview shown gating over main link/visual selection APIs. |
| `packages/ui/src/renderers/robot/useRobotVisibility.js` | Port preview shown gating over main visibility state. |
| `packages/ui/src/renderers/step/ModelTree.browser.test.mjs` | Port recovered UI behavior assertions; use main parameters/description/SURF fixtures where needed. |
| `packages/ui/src/renderers/step/StepHover.browser.test.mjs` | Port recovered UI behavior assertions; use main parameters/description/SURF fixtures where needed. |
| `packages/ui/src/renderers/step/StepQaFixes.browser.test.mjs` | Use main single-part XCAF-name fixture with native reference chips. |
| `packages/ui/src/renderers/step/StepRenderer.browser.test.mjs` | Current UI flow tests against main fixture; preserve progressive SURF tests, exclude source selector-table identity case. |
| `packages/ui/src/renderers/step/StepSurface.jsx` | Port sidebar/filter/annotation/preview/failed-revision UI; retain main asset loader, source appearance, LOD, selector runtime and parameter poses. |
| `packages/ui/src/renderers/step/components/workbench/ModelPartMenu.jsx` | Retained main unchanged; excluded the source engine delta. |
| `packages/ui/src/renderers/step/components/workbench/ModelViewControls.js` | Normalize resolved clip/exploded groups individually; never validate resolved scene.view as sparse public display input. |
| `packages/ui/src/renderers/step/components/workbench/ModelingTree.jsx` | Port source UI presentation/state; retain main data/runtime APIs and exclude new engine records. |
| `packages/ui/src/renderers/step/components/workbench/ModelingTree.test.tsx` | Port recovered UI behavior assertions; use main parameters/description/SURF fixtures where needed. |
| `packages/ui/src/renderers/step/components/workbench/PoseControlsSection.js` | Display main parameters/manifest poses through new sidebar and Select controls. |
| `packages/ui/src/renderers/step/components/workbench/StepPanels.js` | Port source UI presentation/state; retain main data/runtime APIs and exclude new engine records. |
| `packages/ui/src/renderers/step/components/workbench/StepReferenceSection.js` | Port source UI presentation/state; retain main data/runtime APIs and exclude new engine records. |
| `packages/ui/src/renderers/step/components/workbench/StepReferenceSection.test.tsx` | Port recovered UI behavior assertions; use main parameters/description/SURF fixtures where needed. |
| `packages/ui/src/renderers/step/scene/StepSceneLayers.jsx` | Port source UI presentation/state; retain main data/runtime APIs and exclude new engine records. |
| `packages/ui/src/renderers/step/scene/useStepDisplay.js` | Retained main unchanged; excluded the source engine delta. |
| `packages/ui/src/renderers/step/scene/useStepSceneSync.test.js` | Per-mode display fixture plus main meshSourceAdoptionRef and lodCameraChangeRef. |
| `packages/ui/src/renderers/step/workbench/edgeChainSelection.js` | Retained main unchanged; excluded the source engine delta. |
| `packages/ui/src/renderers/step/workbench/edgeChainSelection.test.js` | Retained main unchanged; excluded the source engine delta. |
| `packages/ui/src/renderers/step/workbench/modelingLocalFeatures.test.js` | Retain main inference tests; remove tests of the retired recognition-tree presentation API. |
| `packages/ui/src/renderers/step/workbench/modelingPresentation.js` | New leaf-only sidebar presentation; retain independent main reconstruction/inference modules. |
| `packages/ui/src/renderers/step/workbench/modelingPresentation.test.js` | Port recovered UI behavior assertions; use main parameters/description/SURF fixtures where needed. |
| `packages/ui/src/renderers/step/workbench/modelingSelection.js` | Port source UI presentation/state; retain main data/runtime APIs and exclude new engine records. |
| `packages/ui/src/renderers/step/workbench/modelingSelection.test.js` | Port recovered UI behavior assertions; use main parameters/description/SURF fixtures where needed. |
| `packages/ui/src/renderers/step/workbench/motionRestore.js` | Retained main unchanged; excluded the source engine delta. |
| `packages/ui/src/renderers/step/workbench/motionRestore.test.js` | Retained main unchanged; excluded the source engine delta. |
| `packages/ui/src/renderers/step/workbench/stepViewSlices.js` | Retained main unchanged; excluded the source engine delta. |
| `packages/ui/src/renderers/step/workbench/tangentFaceSelection.js` | Retained main unchanged; excluded the source engine delta. |
| `packages/ui/src/renderers/step/workbench/tangentFaceSelection.test.js` | Retained main unchanged; excluded the source engine delta. |
| `packages/ui/src/renderers/step/workbench/useStepMotion.js` | Port independent preview/work state and one-motion behavior onto main parameters, manifest poses and JS update() animation. |
| `packages/ui/src/renderers/step/workbench/useStepMotion.test.js` | Retained main unchanged; excluded the source engine delta. |
| `packages/ui/src/renderers/step/workbench/useStepMotion.test.tsx` | Port recovered UI behavior assertions; use main parameters/description/SURF fixtures where needed. |
| `packages/ui/src/renderers/step/workbench/useStepMotionControls.js` | Use main parameters and manifest.poses, preserving optional poses access; port reset/preview behavior. |
| `packages/ui/src/renderers/step/workbench/useStepMotionControls.test.tsx` | Port recovered UI behavior assertions; use main parameters/description/SURF fixtures where needed. |
| `packages/ui/src/renderers/step/workbench/viewerAlerts.js` | Retained main unchanged; excluded the source engine delta. |
| `packages/ui/src/renderers/step/workbench/viewerAlerts.test.js` | Retained main unchanged; excluded the source engine delta. |
| `packages/ui/src/renderers/step/workbench/viewerPickMode.test.js` | Keep Render picking coverage; remove source-only baked tube-skin fixture/imports. |
| `skills/cad/SKILL.md` | Only Quick Edit annotation/job instructions changed; engine authoring instructions retained. |
| `skills/cad/references/snapshot-review.md` | Per-mode display and annotation replay; preserve main tessellation and section syntax. |
| `skills/sdf/SKILL.md` | Port UI layout/display/annotation documentation; keep main engine and storage contracts. |
| `skills/srdf/SKILL.md` | Port UI layout/display/annotation documentation; keep main engine and storage contracts. |
| `skills/urdf/SKILL.md` | Port UI layout/display/annotation documentation; keep main engine and storage contracts. |
| `tests/python/global/test_snapshot_viewer_theme_parity.py` | Update only display/annotation fixtures and strict-contract assertions; preserve main engine cases. |
| `tests/python/packages/cadgen/test_snapshot_family_scenes.py` | Per-mode fixtures plus real annotation/measurement pixel replay; retain main section and fixture engine. |
| `tests/python/packages/cadgen/test_snapshot_render_schema.py` | Update only display/annotation fixtures and strict-contract assertions; preserve main engine cases. |
| `tests/python/packages/cadgen/test_snapshot_requests.py` | Update only display/annotation fixtures and strict-contract assertions; preserve main engine cases. |
| `tests/python/packages/cadgen/viewer/test_http_layer.py` | Remove obsolete Excalidraw asset test, retain general static-asset and MIME coverage. |
| `tests/python/packages/cadgen/viewer/test_prompt_files.py` | Native annotation/job persistence and replay contract tests, including legacy-input rejection. |
| `tests/python/packages/cadgen/viewer/test_sketches.py` | Remove retired UI drawing/attachment surface and its tests/docs; no replacement engine behavior. |
| `tests/python/skills/cad/snapshot/test_cli.py` | Update only display/annotation fixtures and strict-contract assertions; preserve main engine cases. |
| `tests/python/skills/dxf/test_snapshot_cli.py` | Update only display/annotation fixtures and strict-contract assertions; preserve main engine cases. |

## Engine dependencies and adaptations

| Dependency in source | Main-based resolution |
| --- | --- |
| Per-part GLB records and lazy selector tables | Retain main assembly/SURF loader, artifact client, selector runtime and LOD. New tree/filter/pick UI consumes the existing reference objects. No new records are produced or read. |
| Source articulation controls and baked animation | Position consumes main `parameters` and `manifest.poses`; Animation keeps main JavaScript `update()` modules and clock. Preview and single-motion UI wrap these existing APIs. |
| Tube skins / baked tube fixtures | Retain main CPU/GPU tube deformation. Restore main close-up predicate and exclude source-specific tube-skin tests. |
| Source robot payload / primitive GLBs | Use main XML description loading, `setJointValues`, mesh-path resolution and SRDF groups. Preview hides work selections/visibility and uses opening joint values; exit restores them. |
| Selector graph data | Retain main `chainEndpoints`, `adjacentSelectors` and `visibilityClass` graph helpers. Exclude source-only selector-table tests; main graph tests remain. |
| MCP protocol 7 | UI host remains protocol 5 to match main Python server. Prompt-file polling is the only tunnel change. |
| Snapshot pipeline | Add overlays and job dependencies around main still rendering; retain main section, animation and DXF routing. Annotation and measurements are accepted only for view snapshots and rejected explicitly in unsupported modes. |
| Display schema | Adopt the strict sparse per-mode public shape in JS/Python. Resolved internal scene view remains main's flat runtime shape; individual clip/exploded normalization bridges the UI. No legacy aliases. |
| Clip positions | Public `clip.at` is a model-coordinate millimeter position. Adapt the existing main clip plane builder; no tessellation or geometry format change. |
| Failed rebuild presentation | Keep main retained mesh state; sidebar kind/tree and permitted part actions reflect the last good visible revision after a build failure. |

Recommendations: keep these adapters at the UI boundary until main adopts any future engine schema through its own change. Do not copy source engine modules to satisfy UI imports.

## Capability differences and rename audit

No intended UI feature is knowingly omitted. Engine-only capabilities remain those of main: no source per-part selector-table identity guarantee, baked Python animation/tube skins or source four-bar robot payload path. Corresponding source-only tests are excluded; main's animation, geometry, graph and robot tests are retained. Main continues to offer section snapshots; annotation/measurement overlays deliberately refuse that mode rather than being silently dropped.

The native annotation cutover removes the old drawing editor/Excalidraw assets and public drawing attachment client. Tool ids, labels, paths, prompt payloads, saved files, CLI and job keys use `annotation`/`--annotation`; persisted jobs name absolute annotation dependencies for replay from another directory. Tests reject old `sketch` inputs. Genuine DXF/engineering drawings, Canvas drawing-buffer terminology and main CAD reconstruction sketches remain correctly named. There are no backward-compatibility aliases or migrations.

## Validation

Test dependencies were installed in the target worktree; no `.venv` or `node_modules` was copied, and tests do not consume the model corpus. Browser suites ran at concurrency 1, serialized with snapshot rendering. Tests use generated or tiny owned fixtures and isolated cache stores; no viewer instance was started or stopped.

| Check | Result |
| --- | --- |
| `npm run build:packages` | Passed; compiled exports include the final STEP adapters. |
| `scripts/bundle/bundle.sh` | Passed after final production changes; built all package, viewer, MCP, browser, Node and native outputs. |
| `npm --prefix packages/core test` | 1,309 passed, 1 existing opt-in memory-probe skip. |
| UI Node pass, using `node --test --test-concurrency=4 --import ./scripts/registerJsxLoader.mjs` on the runner's non-browser test collection | 745 passed. |
| UI Vitest: `node ../../node_modules/vitest/vitest.mjs run --config vitest.config.ts` from `packages/ui` | 337 passed across 68 files. |
| UI browser discovery: `UI_BROWSER_TEST_CONCURRENCY=1 npm --prefix packages/ui test`, followed by the affected STEP files with `node --test --test-concurrency=1 --test-reporter=tap --import ./scripts/registerJsxLoader.mjs` | All 63 unique cases passed, no skips; the final repaired STEP pass was 24/24. |
| `npm --prefix apps/web run test` | 57 passed. |
| `npm --prefix apps/mcp run test` | 44 passed across 5 files. |
| `scripts/test/test-api.sh` | 39 passed. |
| `node --test scripts/bench/viewer-memory/*.test.mjs` | 24 helper tests passed. |
| `npm --prefix packages/ui run typecheck` | Passed. |
| Dependency/kit boundary checkers and their Node tests | Checks passed; 23 helper tests passed. |
| `scripts/release/check-version.sh` | Passed; canonical version remains 0.7.21. |
| `scripts/test/test-python.sh --select all --keep-going` | 2,815 tests across 8 suites, 1 existing skip, zero failures/errors. |
| `scripts/test/test-global.sh` | 209 tests, 1 existing skip, zero failures/errors. |
| `git diff --cached --check` | Passed before the implementation commit. |

Browser results combine the initial complete discovery run with repaired files, rather than claiming a single clean initial run. Repaired cases cover missing Animation controls, automatic topology loading for the single-part sidebar, independent preview isolation, failed-revision interaction and changed floating-control hit locations. The retained main progressive SURF and warm-cache cases pass. Native annotation includes actual ink/measurement pixels and persisted job replay; its responsive browser case covers widths from 900px down to 390px.

Python suite counts: cadgen package 2,366 (one skip), CAD 281, DfAM 19, DFM 51, DXF 77, G-code 6, SendCutSend 6, STEP Parts 9. The package suite includes the real saved-annotation and measurement pixel replay regression.

On this main base, `bundle.sh --check` skips the viewer/MCP build stages. The final regular bundle above was therefore run explicitly after the final shared-package build; the normal implementation commit hook also passed its required `--check`. Generated output remains ignored.
