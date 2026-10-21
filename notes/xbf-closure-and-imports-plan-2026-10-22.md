# xbf engine: build closure in the file, read_step rework, cloud shape (proposal, 2026-10-22)

Status: LOCKED by Jake 2026-10-22 (with the corrections below). Supersedes the "script pointer" and "sealed source" discussions: the
file carries pointers and digests, never code. Builds on the restart cache (ca679fca1) and its data-file
index, which this replaces with a complete closure record.

## 0. Names (correction)

The commands are `cadgen build`, `cadgen export`, `cadgen snapshot`, `cadgen where`, `cadgen serve`,
`cadgen status|stop|daemon`. During the isolation round the engine's verbs are registered under the
temporary `cadgen xbf …` namespace; that level disappears at the cutover. EXCEPTION, now: `cadgen where`
is registered at the top level immediately (the first verb to move up), dispatching to the engine.

## 1. The build closure in the xbf (the manifest)

One record on the document's main label, schema `cadgen.engine closure/1`, replacing today's key record:

```
engine    "cadgen 0.8.0" (from VERSION — fixes the stale "0.7.3" metadata stamp), xbf format version
libs      build123d, OCP, numpy versions
source    {kind: "script", path: "src/tom.py", function: "tom", commit: "<git sha or null>"}
          | {kind: "code", name: "bracket", digest: "9f3c…"}
modules   [["src/lib/current_arm_parts.py", "9f3c…"], ["src/stages/tom2_symmetric_yokes.py", "5e02…"], …]
          the model's own modules (the per-symbol closure's files), digests of their code objects with
          line tables stripped — a comment or blank line changes nothing
inputs    [["file", "../STEP/imported/gripper.step", "c0de…"], ["read", "../src/lib/tom_occurrence_ids.json", "77ab…"], …]
          every file a body read (read_step / read / open), relative to the xbf's folder
key       one digest over all of the above: what the restart cache compares
```

- No source text, no line numbers, no per-element code references, no input contents. Law 1 reads:
  *an xbf carries its build closure as pointers and digests — script and function, modules, data inputs,
  versions, the key, and the repository commit when there is one; never source, never lines on elements,
  never input contents; names remain the per-element tie to code.*
- Validation on a restart: re-hash the listed modules (by importing the script) and inputs, compare; equal →
  the file is taken whole (as now). Unequal → the build report names what changed:
  `rebuilt: src/lib/current_arm_parts.py changed` / `gripper.step changed` / `input missing`.
- `cadgen where DOC` with no ref prints the manifest. `Model.document` / `cadgen.where(doc)` expose it.
- Code mode: `source` is `{kind: code, name, digest}`; the string is never written; multi-file bundles list
  their modules like scripts do.
- Self-contained delivery is an EXPORT, not a property of the document:
  `cadgen export tom.xbf tom.cadz` — a zip of the xbf, the closure's source files and (unless
  `--no-inputs`) its inputs. Extract and `cadgen build` rebuilds byte-identically on a drift-free fixture.

## 2. read_step / read_xbf rework

1. **Two import primitives, copy semantics.** `read_step(path)` reads `.step/.stp` (and `.brep`, as today);
   `read_xbf(path)` reads an engine document. Both copy the imported geometry into the model's document
   (names, colours, materials carried; an xbf's joints and configurations are NOT imported — a part is a
   part). Linking between models is done by importing code, never by file references.
2. **xbf is the fast input.** Reading an xbf is 10–20× faster than parsing a STEP of the same geometry
   (tom-sized: 2 s vs 45 s) and carries our names losslessly. RECOMMENDED PRACTICE, documented in the engine
   README and the cad skill: convert a vendor STEP once — `cadgen export vendor.step vendor.xbf` — keep the
   xbf beside it, and `read_xbf("vendor.xbf")`. The conversion is explicit; the engine writes no hidden
   derived file (no cache store).
3. **STEP parses leave the worker's memory.** A STEP is parsed by a reader helper process (one per build,
   started on the first read, reused for the build's reads, exited at the end), which returns the shapes as
   BinTools bytes plus the label tree (product names, element names, colours, materials) as data; the worker
   rebuilds the build123d tree from them and memoises by the file's digest as now. The reader's working
   memory (2.3 GB for the 354 MB file) dies with the helper; the worker keeps only the result.
4. **Worker memory cap.** After a build, a worker whose RSS exceeds `CADGEN_ENGINE_WORKER_RSS_MB`
   (default: a generous fraction of RAM) is retired; the next build is a restart (seconds when nothing
   changed). The build report says when this happened.
5. **tom stops importing the whole original arm.** `src/tom.py` reads the 354 MB recovered `tom_rev3/tom.step`
   to pick parts out of it. The parts it uses are extracted once (`cadgen export tom.step --part <occ>
   parts/<name>.xbf`) into `models-xbf/tom/STEP/imported/parts/` (untracked, like the rest of tom's vendor
   inputs) and imported with `read_xbf`. Corpus-only change; `../tom-cad` untouched. Expected: cold 146 →
   ~100 s, worker RSS 3.7 → ~1.4 GB; warm edits and restart unchanged.

## 3. Cloud

Out of scope: the cloud discussion was a thought experiment and nothing here depends on it.

## 4. What is explicitly NOT in this plan

- No sealed source in the xbf (two sources of truth; code travels with the file).
- No geometry cache store, local or cloud (restored geometry is not live geometry; see the fixed-point note).
- No per-body loading on restart (blocked by OCCT's BinXCAF round-trip drift; revisit only with a canonical
  geometry key).
- No hidden xbf written beside a vendor STEP.

## 5. Tests (engine suite)

Manifest round trip (write, read, every field); a comment-only edit leaves module digests unchanged; a code
edit names its module in the rebuild reason; an input edit names its file; a missing input falls back
cleanly; `commit` recorded in a git fixture, null outside one; code-mode manifest; `.cadz` export → extract →
build equals the original bytes; `read_xbf` of a converted STEP equals `read_step` of the STEP in geometry, names, colours and
materials; a STEP parse does not raise the worker's RSS beyond the result (measured on a large fixture);
the memory cap retires a worker and the next build is a restart; the policy suite's nothing-to-disk rule.

## 6. Assessment to report

tom before/after: cold, no-op, edit, restart timings; worker RSS after cold and after edit; the manifest as
written; the parts/ import list. Corpus: every model builds cold and warm, bytes identical cold/warm, names
identical except where tom's per-part imports rename imported elements (list them).

## 7. Sequencing

On 71742d40d: one Opus/high agent for §0, §1 and §2.1–2.4 (engine), then one Opus/medium agent for §2.5
(corpus), then the benchmark batch.
