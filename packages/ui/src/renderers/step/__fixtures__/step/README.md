# The STEP browser-test fixture

What a browser test has to serve to open one STEP in the viewer.
`StepRenderer.browser.test.mjs` serves it through
`renderers/harness/stepScenario.mjs`.

The model is `hinge_block`: a 20 × 20 × 10 mm **base** in `#3A6EA5` with a Ø6 mm
bore through it, and a 10 × 8 × 8 mm **arm** in `#D9772B` at x = 15, joined by
one revolute mate. Every property is there for a test:

| property | what it buys |
| --- | --- |
| two parts, two distinct colours | a part's pixels can be told from the other's, so "hidden", "exploded" and "posed" are measurements rather than guesses about total ink |
| the bore | a cylindrical face beside planar ones, so a face pick has something to report a diameter for |
| one revolute mate `hinge` (0…90°) | the panel's Position section, the Position tool and its one knob |
| one named pose `open` | the named-pose jump |
| one routine `swing` | its playbar in preview |

## The files, and which request each answers

| file | bytes | the request it answers |
| --- | ---: | --- |
| `assembly.json` | 3,220 | `GET /__cad/store?file=<tree>/assembly.json&documentHash=…` — the view descriptor. Carries `kind: "assembly-package"`, the `tree`, the `viewId`, the attested `producer`, two components (each naming its `part` and its `fingerprint`), two occurrences and the model box. Every id is a token the build minted: the root `o:895cdc947985`, the base `o:cec517897e79`, the arm `o:5da881c8dcfe`. |
| `components/552fc5fd1b854ab4.selectors.json` | 7,887 | `GET /__tess_cache/<part>.selectors.json?object=…&maxBytes=…` for the base: its part record's `selectors`. cadgen's selector table (schema 2): each face's, edge's and vertex's token, the facts, adjacency, chains and tangent groups picking and measuring join to the mesh by ordinal. |
| `components/df492f79c6123df5.selectors.json` | 6,089 | the same, for the arm. |
| `components/<cid>.glb` | 2,604–11,020 | the parts' display meshes (GLB bodies), one per component, each its part record's `glb`: what the harness's part store serves (`/__tess_cache/` probe, batch and single reads, by each component's `part`). |
| `hinge_block.step.json` | 2,247 | **no request at all.** The harness puts its `animation` section inline on the catalog entry, which is what the real scanner does, and the renderer plays the routine's keyframes straight from there; its tracks name the arm by its token. |
| `hinge_block.articulation.json` | 554 | **no request at all.** cadgen's articulation of the sidecar's kinematics (`cadgen.articulation.step_articulation` over `assembly.json` and the sidecar), which the harness puts inline on the catalog entry as `articulation`, as the real scanner does; the Position tool plays it. `tests/python/packages/cadgen/test_articulation.py` asserts it is what cadgen writes today. |

Total 33,621 bytes with the meshes and tables. No Git attribute applies here (`git check-attr -a` on these
paths prints nothing), and the repository carries no LFS. Keep it that way — an
LFS pointer would be rejected by name at `renderAssetClient.js`'s readers.

The `.step` itself is **not** committed, because the viewer never fetches it:
the catalog names a store view, not the document.

## How the sidecar is bound

`read_source_sidecar` refuses a sidecar whose `schemaVersion` is not current or
whose `documentHash` is not the digest of the STEP bytes being resolved, and the
catalog entry then carries no `articulation` and no `animation` — so the file
silently has no Position section and nothing to play in preview. This one is bound: `schemaVersion` is 11 and
`documentHash` is
`3c1e7edf8593d6019706ff355445199971b740e2bd0661eda6ade9b961082f58`, the SHA-256
of the generated `hinge_block.step`, which is also the `documentHash` in
`assembly.json`. Both were written by the build, not by hand.

## Regenerating

`source/hinge_block.py` is the model verbatim, its routine a Python clip.
`source/make_view.py` builds it in a scratch directory with a store of its own,
as a person builds it, and copies out everything the viewer is served: the view
descriptor (`assembly.json`, as `descriptor_for_view` serves it for the saved
document's tree), the document-bound sidecar, cadgen's articulation of its
kinematics, and each component's part -- its display mesh and its selector
table -- read from the store under the part's key. Every id in them is minted
by the build: the occurrences' tokens from the tree, the elements' from their
geometry, so a rebuild of the unchanged model mints the same ones. Run it from
the repository root with the repo's Python whenever the view schema, the sidecar
schema, the tree's tokens or the model moves:

```sh
.venv/bin/python packages/ui/src/renderers/step/__fixtures__/step/source/make_view.py
```

When only the mesher or the table moves, the parts are rewritten alone, from the
shapes: `source/make_meshes.py` rebuilds the two parts, meshes them with cadgen's
producer at the display tolerance, and writes them in the committed selector
tables' ordinals, each face and edge matched by its token (the name its
geometry gives it, the same in every build), so the committed table and the mesh
stay joined by ordinal; it also writes each component's `part` (cadgen's key for
it under the view's producer) and the view's `viewId` into `assembly.json`.
`source/make_selectors.py` rebuilds each table from its exact BREP and topology
facts (`cadgen._internal.selector_table`) in the committed table's ordinals,
matched the same way. Run them from the repository root, the meshes first:

```sh
.venv/bin/python packages/ui/src/renderers/step/__fixtures__/step/source/make_meshes.py
.venv/bin/python packages/ui/src/renderers/step/__fixtures__/step/source/make_selectors.py
```

When the STEP bytes or the view change, the component ids, `tree`, `viewId` and
`documentHash` change with them, and `stepScenario.mjs` reads every one of them,
the occurrences' tokens included, out of `assembly.json` rather than hard-coding
them; a sidecar schema move that leaves the STEP bytes alone rewrites
`hinge_block.step.json` only.
