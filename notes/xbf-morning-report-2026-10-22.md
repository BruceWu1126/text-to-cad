# xbf engine — morning report, 2026-10-22

Working branch `worktree-agent-a1cb246fec605ab31`, head **2b603b063**. Nothing pushed; `main` untouched;
`VERSION` unchanged. Every suite green on the head: engine 179 tests, policy 200 (1 skipped), core 1044,
ui 375, web 54, web build, bundle `--check`, browser gate (format + camera).

## What landed overnight (merge order)

| merge | what |
|---|---|
| 8d7977b41 | `@memo`: the explicit replay marker (bare, no file semantics); corpus moved onto it — motorbike's 21 part scripts, movement's 3 layers + 19 part groups, qdd's 7 makers, tom's 6 stages; `cadgen xbf build --verify` re-runs replayable bodies and flags impure ones |
| 7f4088606 | the from-scratch export package: `engine/export/` (translate + step/glb/stl/3mf writers on the viewer's one mesh cache), `engine/api.py` (`cadgen.build/export/snapshot/serve/where`), build flags with select-and-override and code mode (`--code`, `-`, `--name`, `result`), `cadgen xbf export SRC DST…` with `--pose/--part`, STEP→xbf, `--part` extraction, derived-file decorators taking exactly their verb's options, `@snapshot`, `Model.plan/build/export/snapshot/document`, `python model.py` asks the daemon, snapshot of .glb/.stl/.3mf/.step, old `exports.py` deleted |
| 2b603b063 | examples README line for the new export form |

Earlier the same evening (before you went to bed): ca679fca1 restart cache, 46392ed69 names-cost fix,
4d14653c2 header date, e346887e3 document patch, 976becfa6 corpus joints + one xbf per model, fb99bdb30
name stability, 06cded397 geometry sharing, d6fda9a12 viewer pick/press + far-cap mesh fix, a2a8dac8d gate.

## Final batch (batch 4) on 2b603b063 — zero failures

| model | cold wall / body | no-op | edit wall / body (misses; run+replayed) | revert | restart | xbf MB |
|---|---|---|---|---|---|---|
| dfm (5 docs) | 11.6 / 0.5 | 0.12 | 0.41 / 0.21 (105) | 0.18 | 10.9 | 0.05 |
| qdd_actuator (8 bodies now) | 5.1 / 2.2 | 0.19 | 0.97 / 0.49 (19; 1+7) | 0.52 | 2.5 | 2.5 |
| motorbike (22 bodies) | 8.2 / 5.6 | 0.16 | 0.26 / 0.03 (7; 2+20) | 0.25 | 2.4 | 2.4 |
| moonwatch case | 18.9 / 15.2 | 0.23 | 8.71 / 7.73 (766) | 0.95 | 2.6 | 6.9 |
| moonwatch movement (23 bodies now) | 176 / 169 | 0.79 | 4.50 / 2.92 (224; 3+20) | 1.63 | 3.3 | 17.0 |
| examples (5 docs) | 9.0 / 0.3 | 0.12 | 0.21 / 0.10 (64) | 0.14 | 8.7 | 0.07 |
| **tom** (7 bodies, 220 parts) | 146 / 128 | **1.48** | **8.27** / 3.71 (110; 1+7) | 7.01 | **6.11** | 58.9 |

Mesh/snapshot unchanged (tom whole-document mesh 1.85 cold / 0.88 warm, snapshot 1.04 s).

**tom against the old engine (19 Oct numbers, same machine):** edit answered 8.3 s (was 24–29 s, then a
24–26 s STEP); no-op 1.5 s (was 6.7); cold 146 s (was 7–10 min); restart with nothing changed 6.1 s (was a
cold build); worker RSS 3.7 GB (was 11+); file 59 MB xbf (was 419 MB STEP). Exports are now verbs: a STEP of
tom costs 7.5 s when asked for, never in the loop.

## What did not land, and why
- **Edit after a restart still runs every body** (146 s on tom). Per-body loading from the file is blocked
  by OCCT: BinXCAF read→write is not byte-stable (four doubles off by one ulp on `l_bracket`; the motorbike
  drifts 3,839 more bytes on a second round trip, no fixed point), so a loaded document cannot become the
  kept one without breaking byte identity and drifting on every cycle. Restart-with-nothing-changed is cached;
  restart-then-edit is a cold build.
- **GLB animation**: deferred by design; `animate=` exists on `translate`/`export`/`@glb` and refuses non-None.
- **`viewer()`** not added to the api (the plan said that verb is unchanged).

## Findings worth knowing
- `--verify` found a real, pre-existing nondeterminism: tom's `tom2_symmetric_yokes` stage is not
  reproducible — a fresh run makes 47 cache misses and changes an intermediate (`shoulder_pitch_yoke`); it is
  the source of the edit→revert byte drift we had attributed to "accepted boolean drift". Movement's
  `column_wheel` differs across cold processes the same way. Mechanism: parallel booleans drift a few bytes,
  every downstream op keyed on those bytes misses.
- Two unused tom variants (`tom2_500mm`, `tom2_600mm_solid`) fail identically on every head
  (`ShapeUpgrade` "Courbes non jointives"): model geometry faults, pre-existing.
- The browser gate failed once on STEP under an agent's own parallel load (ERR_CONNECTION_REFUSED to the
  preview server) and passed on every quiet run since; not classified.
- `@memo` gains are small in time (tom edit 9.3 → 8.0 s in the agent's A/B; batch: 8.8 → 8.3); its value is
  one xbf per model and `--verify`.

## Open decisions (numbered; my recommendation in italics)
1. **Confirm the `@memo` merge with its documented trade-offs**: stage/part scripts no longer build alone;
   a memo'd function names what its own `return` makes (helpers returning `a - b` stay plain); purity is the
   author's contract — `--verify` catches nondeterminism, not two functions coupled through module state (the
   case model's flag; left as one body); a verify mismatch marks the function always-run for the worker's
   life and skips the restart cache (tom's no-op 0.18 → 3.2 s after a verify run, by design); two same-argument
   calls share one result. *Confirm; none is a regression, names and bytes are identical on the corpus.*
2. **Parallel booleans** stay ON with the drift `--verify` now exposes (tom2 stage, column_wheel), or OFF
   (deterministic, ~1.6× slower cold). *Keep ON; fund the bounded investigation in 3, which is the same drift.*
3. **The BinXCAF fixed-point investigation** (why read→write drifts: locations, flags, tolerances) — if it
   can be canonicalised, per-body cold cache (edit after restart = one body) becomes possible and item 2's
   drift may fall with it. *Yes, Opus/high, time-boxed to a day; the prize is 146 s → ~10 s after a restart.*
4. **The data-file index in the xbf** (relative paths + hashes of the files a model read). *Keep.*
5. **Export deviations to accept**: `status/stop/daemon` not at top-level `cadgen` (name collision with the
   old `cadgen.daemon`/`cadgen.viewer` subpackages until migration); `snapshot.py` doubles as the decorator;
   `Built.document` is an `export.Source`, not a loaded document (OCP cannot save XCAF to a stream);
   posed/parted documents copy sub-assemblies instead of sharing prototypes; the default xbf path moved to
   `XBF/<name>.xbf` beside the script (explicit `out=` unaffected); a request naming its own files/pose/part
   skips the take-from-file cache. *Accept all.*
6. **A decorator-option-only change after a restart is not noticed by the restart cache** (the warm path
   notices), so a derived file could be stale after a restart. *Fix: fold derived-file options into the model
   key — small, queue it.*
7. **GLB animation**: build now (a day on the export package: motion description → FK → adaptive CUBICSPLINE
   keys) or when a demo needs it. *When needed.*
8. **tom's two broken variants** (`tom2_500mm`, `tom2_600mm_solid`): delete from the corpus or fix the
   geometry. *Delete; they are unreferenced.*
9. **The 13 unmeshed sliver faces** (case 8, movement 3, tom 2): fix at the geometry or ignore. *Ignore for now.*
10. **Viewer silhouette lines on curved faces** (STEP has them in Solid/Hidden-line; the xbf scene draws only
    B-rep edges). *Do, Sonnet/medium, it is a renderer feature with a reference implementation.*
11. **Subprocess STEP parse** to drop the 2–3 GB malloc high-water from reading a 354 MB vendor file.
    *Defer; the worker is 3.7 GB now.*
12. **The "ok" for migration** (replace the old pipeline with the engine) — yours alone, not proposed.

## Where things are
- Branch and worktree: `.claude/worktrees/agent-a1cb246fec605ab31`, head 2b603b063.
- Viewer for you: `localhost:5251` (restarted on the head; tabs for tom, motorbike, movement, qdd — reload).
- Plans: `xbf-export-plan-2026-10-21.md` (this folder); corpus baseline + batches 2–4:
  `xbf-corpus-baseline-2026-10-21.md`; batch rows and logs in the session scratchpad `corpus-bench/`.
