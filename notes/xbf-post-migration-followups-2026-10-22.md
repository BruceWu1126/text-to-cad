# Post-migration follow-ups — 2026-10-22

PR #568 is green at bf236ba28 (mergeable, clean, +151k −285k, 2,654 files). Everything below is what is left
after it, grouped: decisions only Jake can make, engineering follow-ups, and the release path.

## A. Decisions (Jake)

1. **Every commit rewrites every xbf.** The closure manifest records the commit hash, so after any commit every
   model's xbf differs in that field alone and the next run writes it again. Keep (simplest; nothing else on
   disk), compute the commit live in `cadgen where` instead of storing it, or drop it from the file.
2. **Replayed-body BRep order drift.** A memo-replayed body can write BRep entries in a different order than its
   cold run (f1 gearbox casing, f14d, radial's barrels): bytes differ, geometry and names identical; `--verify`
   reports it. Accept and document, or fund a canonical BRep writer.
3. **Parallel-boolean drift on cold builds.** A few parts differ by bytes between two cold builds (w16 3 of 794,
   tom's yokes, moonwatch's tachymeter): OCCT's parallel booleans order faces differently. Accept, build those
   bodies single-threaded (slower), or the canonical writer of item 2 (same fix).
4. **Headless-Linux edge look.** llvmpipe (what a GPU-less Linux box or container gets) draws beaded anti-aliased
   edges in X-ray, hidden-line and wireframe (pictures sent; `ci-edges/` in the session scratchpad). Keep as
   recorded (Linux goldens), or MSAA off for software renderers only (solid aliased edges). Small.
5. **The UI branch `claude/568-ui-layout`** (d92e3d883, WIP from 2026-10-16 on the OLD #568: per-mode display
   settings, annotation replay) targets the STEP renderer, which is now deleted. Replay it onto the xbf
   renderer after the merge, or redo it from the design.
6. **The release bump** (patch/minor/major or an exact version) is yours; the PR carries no bump. Items 14 and
   16 below are the ones I would land first.
7. **Prune or port the flagged corpus models** (item 19).

## B. Engineering follow-ups (each its own PR)

8. **Non-linear couplings.** `couple` is linear only (one control, several joints, fixed ratios); slider-cranks
   and cams need a function, and function-of-time motions bake large GLBs (radial 140 MB). Either a callable or
   table coupling, or document the limit. Medium.
9. **`connect_to` on a sub-assembly with its own joints** leaves its base part on the ground when posing, in the
   viewer and in `export --pose`. Bug. Medium.
10. **Derived files rewritten with identical bytes.** A 3MF/GLB is rewritten when its xbf changes even when the
    derived bytes are the same. Compare before writing. Small.
11. **Worker memory after cold builds.** The sculpted humanoid's worker holds 5.8 GB; f14d's airframe stage
    reaches the 16 GB default cap. The memo needs to release what no key references after a build, or per-model
    caps. Measure first. Medium.
12. **Raw OCCT calls outside the memo list** (e.g. `BRepPrimAPI_MakeCylinder`) run uncached; the report names
    them now. Cover the common raw ops, or make the skill's rule explicit. Small to medium.
13. **LinearJoint pairing sharp edge.** A prismatic pair's RigidJoint must be a pure translation at the axis
    origin (documented). Accept any orientation or refuse loudly. Small.
14. **One-time sweep of the old cache store** (approved by Jake in the migration decisions) is NOT in the PR.
    Nothing removes the 0.7.x store at `~/.cache/cadgen` (`$XDG_CACHE_HOME/cadgen`; `%LOCALAPPDATA%\cadgen` on
    Windows), which can hold gigabytes. First daemon start of 0.8 removes it once. Small; before the release.
15. **Tube deformation and explode plans** from the old viewer (tendon_hand's cable tubes via
    `packages/core/docs/tube-skins.md`) have no equivalent on the engine; the xbf viewer dropped them with the
    STEP renderer. Decide whether tube skins return. Medium.
16. **MCP server stdin deadlock shape (Windows).** `cadgen mcp` reads `sys.stdin.buffer` on its main thread while
    its worker threads may load DLLs: the same deadlock this PR fixed in the engine worker. Check with the
    journal harness, fix like the worker (requests on a handle of their own). Small to medium; before a Windows
    release claim.
17. **Mesa opengl32 on the Windows runner** so the 27 skipped drawing tests draw there (GDI's OpenGL 1.1 draws
    nothing). Small CI change; until then Windows never tests a still.
18. **DaemonRenderTest's lost patch** (a rebuilt version not reaching the render process on Linux) passes since
    the one-X-connection-per-thread fix; the cause was never caught in the act. Watch; no work unless it recurs.

## C. Corpus

19. **Flagged models under `unported/`:** tendon_hand 56 of 112 (25 read the frozen palm STEP outside git, 8 the
    250 MB native hand, 7 clearance studies on the old step_scene, 6 other frozen STEPs, 8 generated JSON, 1
    broken import, 1 fails on 0.03 mm fillets); moonwatch's whole-watch, dial, bracelet and finishing_sampler;
    24 example parts; `joint_playground.urdf`. Per group: port (the frozen STEPs need checking in or
    regenerating), prune (Jake: "we need to prune some models anyway"), or keep flagged.
20. **Seven models without an old-engine number** (f1, movement, case, motorbike, qdd, planetary, l_bracket):
    rebuild on main for a fuller table, or leave the three flagships. Optional.
21. **Long cold builds are the builders' own kernel work** (f1 494 s, radial 3,195 s): nothing in the engine;
    splitting their stages with `@memo` would speed demo rebuilds. Optional.

## D. Product, docs, checks before the release

22. **Stills approximate clearcoat, anisotropy and transmission** (OCCT's raster has none; the viewer draws
    them). Accepted in the PR; a later stills upgrade or the render track closes it.
23. **Render track** (photoreal stills and mp4s): the Blender Cycles trial lives in the session scratchpad
    (`render-x/out`: samples, `compare_*.png`, `movement_explode_cycles.mp4`), proposal note
    `render-track-proposal-2026-10-21.md`. Push as a draft PR when wanted; design is Jake's.
24. **Long-form engine guide.** Skills, README, docs homepage, CONTRIBUTING and the engine README are rewritten;
    there is no single guide to authoring on the engine (decorators, the memo rule, joints/couplings/motions,
    the refs grammar, materials) beyond the cad skill's references. Decide whether the docs site gets one.
25. **Skill A/B harness re-run** on the migrated skills (the blind harness last compared skills on the old
    engine) to catch regressions in agent-authored models. Medium.
26. **Human pass in the agent apps.** Dev-install the migrated plugin in Claude Code, Codex and Cursor and run
    a model end to end (build, `cadgen snapshot`, the CAD app showing `.xbf`, Position panel, Copy Prompt refs).
    The migration was proven by suites and the demo viewer, not in the apps.
27. **Analytics dashboard.** Telemetry ships schema 6 with kind `xbf` and process `cli`; the receiver accepts any
    schema, but the private dashboard (`../t2c-analytics`) must learn the new kind and fields or xbf rows stay
    invisible. Small.
28. **Security audit re-run.** The paused audit of 0.7.26 (1 high: sidecar JS, now gone with the sidecar; 5 low;
    7 leads) waits for 0.8: re-run on the migrated tree, especially daemon socket auth, the relay routes and the
    viewer's xbf routes.

## E. Release path

29. Merge #568 on Jake's word (never unasked).
30. `Prepare Release` with the bump Jake names; items 14 and 16 first if they are to ship in 0.8.0.
31. After the merge: `Publish Release` builds the wheel, publishes to PyPI, writes `latest` and
    `claude-plugin`, deploys the docs and tags. Then item 26 against the published plugin, and item 28.
