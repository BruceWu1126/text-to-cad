# xbf corpus benchmark — integrated head 06cded397 (2026-10-21)

One batch from the main thread (`corpus_bench.py config.json`), fresh daemon per model, this machine, load 7–10
external (the 30–45 readings are the batch's own cold builds). Wall = the CLI answered, including every declared
export (tom and the others declare @step beside @xbf). Timings in seconds; files in MB.

| model | cold wall / body | no-op | edit wall / body (misses) | revert | xbf write / MB | STEP write / MB | mesh cold / warm | snapshot |
|---|---|---|---|---|---|---|---|---|
| dfm-process-comparison (5 docs) | 11.7 / 0.6 | 0.12 | 0.47 / 0.26 (105) | 0.20 | 0.02 / 0.05 | 0.03 / 0.11 | 0.42 / 0.02 | 0.67 |
| qdd_actuator | 5.3 / 2.3 | 0.19 | 1.36 / 0.60 (19) | 0.91 | 0.16 / 2.5 | 0.32 / 5.9 | 0.29 / 0.07 | 0.71 |
| motorbike (22 bodies) | 8.5 / 5.8 | 0.15 | 0.52 / 0.04 (7; 2 run + 20 replayed) | 0.49 | 0.10 / 2.4 | 0.23 / 6.3 | 0.44 / 0.10 | 0.72 |
| moonwatch case | 19.6 / 15.3 | 0.23 | 9.6 / 7.8 (766) | 1.9 | 0.33 / 6.9 | 0.94 / 22.1 | 1.61 / 1.55 | 0.71 |
| moonwatch movement (4 bodies) | 178.9 / 170.2 | 0.75 | 6.9 / 3.0 (224; 2 run + 2 replayed) | 4.1 | 1.09 / 17.0 | 2.21 / 49.9 | 0.62 / 0.33 | 0.79 |
| examples (4 docs) | 8.8 / 0.3 | 0.11 | 0.21 / 0.09 (64) | 0.15 | 0.03 / 0.07 | 0.02 / 0.15 | 0.44 / 0.01 | 0.62 |
| tom (7 bodies, 220 parts) | 148.4 / 123.8 | 1.40 | 16.2 / 3.6 (110; 1 run + 7 replayed) | 14.9 | 3.2 / 58.9 | 7.5 / 183.7 | 1.82 / 0.85 | 0.98 |

Unmeshed faces (BRepMesh faults at FreeCAD's default, no repair layer): moonwatch case 8, movement 3, tom 2.

## tom against the recorded baseline (old engine, 19 Oct 2026, same machine)

| | old engine | xbf 64517d1d5 (20 Oct) | xbf 06cded397 (this batch) |
|---|---|---|---|
| edit answered, xbf only | 24–29 (body 10–11) | 9.2 | ~8.7 (16.2 with the 184 MB STEP) |
| edit answered with STEP | 24–29 + 24–26 after | 29.7 | 16.2 |
| all-hit no-op floor | 6.7 | 3.6 | 1.4 |
| cold build | 420–600 | 244 | 148 |
| STEP written | 419 MB / 24–26 s | 492 MB / 20.5 s | 184 MB / 7.5 s |
| xbf written | — | 174 MB / 9.2 s | 59 MB / 3.2 s |
| whole-document mesh (pool) | — | 2.2 s | 1.8 cold / 0.85 warm |
| worker RSS after cold | — | 11.1 GB | 4.2 GB (live heap 0.64 GB) |

What moved the numbers since 64517d1d5: geometry sharing on placement (one prototype per geometry, 619 → 221),
the merged document, per-symbol retention, read tracing; mesh fix for far-cap edges.

Earlier corpus batch (20 Oct, head 655069691 era, 4 of 6 models; motorbike and tom failed in the kinematics
harvest): qdd cold 5.6 / no-op 0.19 / edit 1.5; moonwatch case 20.6 / 0.27 / 10.4; movement 206 / 0.8 / 8.0;
dfm and examples sub-second no-ops. Rows: session scratchpad `corpus-bench/rows-batch1.jsonl`, this batch
`corpus-bench/rows.jsonl` + `batch2.log`.

## Batch 3 — head ca679fca1 (2026-10-22 02:25–03:05 local), one xbf per model, no exports in the loop

Adds a `restart` step: daemon stopped, build again with nothing changed (the xbf taken as the cold cache).
Load 6–8 external; the 13–28 readings are the batch's own cold builds.

| model | cold wall / body | no-op | edit wall / body (misses) | revert | restart | xbf write / MB | mesh cold / warm | snapshot |
|---|---|---|---|---|---|---|---|---|
| dfm-process-comparison (5 docs) | 12.3 / 0.5 | 0.13 | 0.41 / 0.21 (105) | 0.17 | 11.1 | 0.03 / 0.05 | 0.51 / 0.02 | 0.70 |
| qdd_actuator | 5.2 / 2.3 | 0.20 | 1.09 / 0.56 (19) | 0.61 | 2.5 | 0.16 / 2.5 | 0.32 / 0.08 | 0.72 |
| motorbike (22 bodies) | 8.2 / 5.5 | 0.17 | 0.28 / 0.03 (7) | 0.25 | 2.5 | 0.10 / 2.4 | 0.35 / 0.10 | 0.74 |
| moonwatch case | 18.8 / 15.2 | 0.23 | 8.95 / 7.98 (766) | 0.96 | 2.6 | 0.33 / 6.9 | 1.62 / 1.48 | 0.72 |
| moonwatch movement (4 bodies) | 181 / 174 | 0.84 | 4.95 / 3.28 (224) | 1.92 | 3.5 | 1.04 / 17.0 | 0.68 / 0.39 | 0.91 |
| examples (5 docs) | 9.4 / 0.3 | 0.12 | 0.20 / 0.09 (64) | 0.15 | 9.8 | 0.03 / 0.07 | 0.56 / 0.02 | 0.68 |
| tom (7 bodies, 220 parts) | 152 / 133 | 1.56 | **8.79** / 3.95 (110) | 7.33 | **6.1** | 3.49 / 58.9 | 1.97 / 0.92 | 1.05 |

tom against the old engine: edit answered 8.8 s (was 24–29 s + a STEP after), no-op 1.6 s (was 6.7),
restart with nothing changed 6.1 s (was a 7–10 min cold build; a cold build is now 152 s), file 59 MB.
The restart floor on small models (~2.5 s) is daemon + worker start and imports; dfm and examples pay it
five times (five documents each). What moved since batch 2: STEP out of the loop (−7.5 s on tom's edit),
the document patch, the names-cost fix (names back to 0.2 s warm), the restart cache.

## Batch 4 — head 2b603b063 (2026-10-22 04:20–05:00 local), @memo + export package merged

| model | cold wall / body | no-op | edit wall / body (misses; run+replayed) | revert | restart | xbf MB |
|---|---|---|---|---|---|---|
| dfm (5 docs) | 11.6 / 0.5 | 0.12 | 0.41 / 0.21 (105) | 0.18 | 10.9 | 0.05 |
| qdd_actuator (8 bodies) | 5.1 / 2.2 | 0.19 | 0.97 / 0.49 (19; 1+7) | 0.52 | 2.5 | 2.5 |
| motorbike (22 bodies) | 8.2 / 5.6 | 0.16 | 0.26 / 0.03 (7; 2+20) | 0.25 | 2.4 | 2.4 |
| moonwatch case | 18.9 / 15.2 | 0.23 | 8.71 / 7.73 (766) | 0.95 | 2.6 | 6.9 |
| moonwatch movement (23 bodies) | 176 / 169 | 0.79 | 4.50 / 2.92 (224; 3+20) | 1.63 | 3.3 | 17.0 |
| examples (5 docs) | 9.0 / 0.3 | 0.12 | 0.21 / 0.10 (64) | 0.14 | 8.7 | 0.07 |
| tom (7 bodies, 220 parts) | 146 / 128 | 1.48 | 8.27 / 3.71 (110; 1+7) | 7.01 | 6.11 | 58.9 |

Mesh/snapshot unchanged from batch 3. This is the corpus baseline for the next iteration.
