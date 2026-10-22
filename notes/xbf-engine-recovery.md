# xbf engine rewrite — recovery note (2026-10-20, ~02:35Z)

Written by the recovery session for "cadgen 0.8" (original Claude session 948d1094, cut off 02:13Z on the Fable limit). Jake's instruction on recovery: only the xbf engine rewrite matters; the memo-fix and dependency-graph pieces are dropped for now.

## What the dead agent reached (a612fa9d, Opus/high, 01:20Z–02:15Z)

Worktree `/Users/jakefitzgerald/robots/text-to-cad/.claude/worktrees/agent-a612fa9d192e34a6d`, branch `claude/cadgen-engine-xbf` at b1334f75c. **Nothing committed**; the whole engine is uncommitted in that tree (backed up as `tracked.diff` + `untracked.tar` in the recovery session's scratchpad `xbf-handover/`). No README.md, no HANDOFF.md (the cut-off was an API error, not low context).

Files (9,100 lines): `packages/cadgen/src/cadgen/engine/` — `model.py` (decorators `xbf/step/glb/stl/threemf/paint/read_step`, identity `script.py::fn`, children as sub-assemblies, a model called twice is one prototype placed twice), `build.py` (in-process build: load script, one memo run, document, names, xbf written only when bytes change, exports in declaration order, sidecar `.xbf.json`), `document.py` (result → XCAF prototypes/instances, mm, names, colours, element names on sub-shape labels, geometry key GUID per part, BinXCAF atomic save, read-back incl. foreign STEP), `exports.py` (STEPCAF with face names; own binary STL, GLB, 3MF writers from the engine's tessellation), `mesh.py` (BRepMesh relative+angular deflection; one buffer per part: f32 positions/normals, u32 indices, face table, edge table, edge polylines; read via BinTools+numpy; wire format `XBM1` 64-byte header), `serve.py` (HTTP: `/api/docs`, `/api/doc/<id>`, `/api/doc/<id>/part/<key>`, `POST .../mesh` parallel threads or processes; mtime-polled reload), `daemon.py` + `worker.py` + `client.py` (one daemon, one worker per script, Unix socket + lock in `$TMPDIR/cadgen-engine`, idle exit), `cli.py` (`cadgen xbf build|export|serve|status|stop|daemon`), `page/` (three.js viewer with face hover → element name, verified in the browser pane), `kernel/{memo,history,trace,determinism}.py` (lean copies; memo carries fixes 1 and 2 — `rowed` index, `_map_subs` pruning at registered roots — and maker counting; fix 3's decode-ordinal rule is NOT visible in the copy — `_decode_key` is absent — and the memo agent's commit 5 correction (53c631703) postdates the copy).
Three tracked edits: `cadgen/cli/__init__.py` registers `xbf`, `select_checks.py` gets an ENGINE_CODE rule, `test_public_surface.py` lists the xbf verbs.

Tests (`tests/python/packages/cadgen/engine/`): 23 tests, all passing at 02:04Z via plain `python -m unittest` (test_build 9 + test_standalone 2, test_memo 9, test_daemon 3). Never run through `scripts/test/test-python.sh`.

Benchmarks: only juno ported (`scratchpad/engine/bench/juno`, via `port.py`; `bench.py` is the harness). At load 15–19: cold 28.5 s wall (to beat 35 s ✓), warm no-op 6.1–8.4 s wall / body 4.5–6.6 s and warm edit 6.8 s (to beat 2.8–3.5 s ✗), xbf 7.9 MB in 0.26–0.37 s (31% of the STEP ✓), STEP 25.2 MB in 1.0 s (✓), whole-document mesh 244 prototypes of 363 parts in 2.14 s on 10 threads (processes mode unmeasured: bench.py crashes parsing the serve URL after `stop`). Hypercar and tom not ported or measured.

Where it stopped: profiling the juno warm floor. Every build has 206 "uncached" (unkeyable) calls; ~3 s of the warm body is `cut`/`common` at `lib/head.py:174` (24 calls each) and `:147`, whose inputs come from shapes no memo operation made — it was reading build123d's `scale` (`Shape.scale`) and the model's `_try_fillet` retry loop when it died. Next step was making those constructions memo operations.

Two daemons from its runs are still alive: pid 48070 (default state dir) and pid 92892 (`CADGEN_ENGINE_DIR=/tmp/eng-juno`, idle 7200 s).

## What is resuming

A fresh Opus 5.5 / high subagent in its own worktree cut from b1334f75c: imports the uncommitted tree, runs the suite through the repo runner, commits at once, writes the README, then continues at the warm-floor investigation, ports hypercar and tom, benchmarks all three, and reports. Never pushes, VERSION untouched.

## Outcome (2026-10-20 ~04:00Z)

Engine complete on `worktree-agent-a1cb246fec605ab31` @ 64517d1d5 (worktree `.claude/worktrees/agent-a1cb246fec605ab31`), 12 commits over b1334f75c, nothing pushed, VERSION untouched. Engine suite 75 OK, policy suite 200 OK (re-run by the recovery session). Decisions: FreeCAD's tolerance default + `EnableControlSurfaceDeflectionAllSurfaces`, BRepMesh as-is (fault measurement: `brepmesh-fault-measurement-2026-10-20.md` beside this note), XBM2 buffers with edge classes and vertices, facts on demand, the served document patched in memory by the build, body-level retention (unchanged bodies replayed from a log, names identical).

Merged bench, load 6–14, xbf on disk / with STEP [number to beat]: juno no-op 0.39 s, edit 1.33 / 2.26 [2.8–3.5], cold 22.9 [35]; hypercar no-op 3.6, real hinge edit 5.1 / 11.4 [5–7], cold 169 [~240], STEP 6.3 [7.8]; tom no-op 3.6 [floor 6.7], edit 9.2 / 29.7 [24–29], cold 244 [420–600], STEP 20.5 [24–26]. Whole-document mesh on the pool: 0.33 / 2.6 / 2.2 s. Edit → pixels 1.1 / 4.1 / 8.1 s from build start, before the build returns.

Known gaps (from both branches): invalidation is per module, not per symbol (an edit to a module every body imports re-runs all of them); cold vs warm xbf differ by one TShape flag bit in some parts (names identical); a worker's mesh and a cold read's mesh of one key may order triangles differently; juno's root body always runs (one uncached call into cadgen's kinematics helper); some vendor part names are the STEP's own; STEP is ~15% larger with face/edge names. Not started: viewer/snapshot cutover to the engine; cold builds / parallel bodies.
