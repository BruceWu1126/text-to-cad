# The store

`~/.cache/cadgen/` — where every model's result lives. Read this before
changing anything under `cadgen/store/`, the build pipeline that writes to it,
or a consumer that reads from it. It is written so that someone who only ran
`pip install cadgen` can act on every sentence.

Long on purpose, so jump to the section your change touches. The laws it
serves are in [`README.md`](README.md); where the two disagree, this file is
right.

| § | What it settles | Read it before |
|---|---|---|
| [1](#1-vocabulary) | The one word per concept, and the retired ones | naming anything |
| [2](#2-layout) | What lives under the root, **the two sides law**, identity by construction and its versions | adding an entry or a reader, bumping any version |
| [3](#3-tree-and-record) | Tree, record and provenance shapes; tokens and names, parts, meshes; exports and the kept rule; each kind's schema history | changing what a build writes |
| [4](#4-the-gate) | The freshness clauses, in order; restoring by key; healing a current tree | touching stale/current |
| [5](#5-invariants) | Each invariant with the failure it prevents | any store write |
| [6](#6-link-or-component) | Whether a child becomes a link or the parent's geometry | composition, materialize, packaging |
| [7](#7-concurrency) | Why there is no lock | concurrent builds, publish races |
| [8](#8-gc-eviction-and-the-cap) | The sweeper: retired kinds, eviction to the cap, unreachable objects, a store two cadgens share, and why a pass needs no lock | anything that deletes |
| [9](#9-the-daemon) | The build pool, job ledger and slots | daemon, workers, jobs |
| [9a](#9a-children) | Children: a child call decodes the stored result or builds the child, serially, in the caller's process; its own captures, once per run, failure, queued files | a decorated call's return, a child's build |
| [9b](#9b-editing-previews-and-explicit-saves) | The preview revision, the build feed's events (the result, the files it owes, then each file), explicit saves | what the viewer says while a build runs, what a door waits for |
| [10](#10-debugging) | `store why`, resolving a tree, resets smallest first | diagnosing staleness |
| [11](#11-never) | The explicit prohibitions | before proposing any of them |

## 1. Vocabulary

One word per concept; the code uses these words and no others.

| term | meaning |
|---|---|
| **model** | a parameterless decorated function — `@step` (with any stacked `@stl/@glb/@threemf`), `@stl/@glb/@threemf` alone (mesh-only), or `@dxf` (a drawing); identity = its resolved script path plus the function's name, spelled `script.py::function`; a file usually holds one (then the bare path names it and its default output is `<file>.<fmt>`) and may hold several (each writing `<function>.<fmt>`), which share the file's closure; its outputs are what its decorators declare |
| **parent / child** | models related by a call inside a body |
| **build** | running a model's function and publishing its result |
| **store** | the whole cache, `~/.cache/cadgen/`: `objects/` + `index/` |
| **object** | an immutable, content-addressed file in `objects/` — a component or a tree |
| **component** | exact encoded BREP plus an immutable effective face-color recipe; its part's display data is derived separately |
| **tree** | a model's result as an object: its own components + links, with placements, names, colors; its hash is the result's content address |
| **link** | a tree entry pointing at a child's tree hash, with placement and name |
| **result** | what a build made of a model's inputs: its tree, with the record and provenance written for it, stored under its key (`index/result`, `store/results.py`); the model's record names its current one |
| **key** | a result's identity: a hash of the model (its script and function) and what it was built from -- its closure, its constants by value, its children's keys and the producer (cadgen and the kernel) that built it -- with an empty argument digest (`store/results.py`); a parent records each child by it |
| **record** | the mutable per-model entry in `index/model`: the current result's tree and key, the producer that built it, its closure, its children by key and its outputs |
| **index** | the input-addressed side of the store: every entry under `index/`, each named by what produced it (§2) |
| **closure** | what a model's build depended on: the source it reaches, the files it read, the folders it listed, and the files its imports rely on not existing |
| **stale / current**, **gate** | the freshness state and the check that decides it |
| **claim** | what a write does to an object it finds already present: its mtime becomes now, less two ticks of its clock, so the sweeper's grace window covers it and the claiming process keeps what it verified of it (§8) |
| **evict** | drop a derived entry to keep the store under its cap; only the derived kinds are ever evicted (§8) |
| **part** | a component's display data under one producer: ONE index entry naming its display mesh and its selector table (§3), keyed by the construction that made the component -- its content, where no construction did -- and its lineage |
| **feature** | one call of a kernel construction from the model's own code, as the run's trace saw it (`_internal/kernel_trace.py`): named, within a part, `<function>.<operation><count>` -- `bracket.extrude1`, the first `extrude` of the function `bracket` among the features the part descends from -- or, in a part made of a shape an author labelled, by that label (§2) |
| **construction key** | what made a component: the kernel memo's key for the construction whose result it is -- the operation, its parameters by value and the keys of its inputs, restated stably with cadgen's version (`kernel_memo.construction_key`, README law 18). A component no construction of a run made (a child's decoded result, a file read) has none and goes by its content hash (§2) |
| **lineage** | a component's account of its elements: the feature that made the body, per face, edge and vertex its **name** -- its derivation from the features that made it, from what the kernel's makers reported: `f:bracket.make_box1.6;M:bracket.cut1`, the sixth face of the first box, modified by the first cut -- and the **table** that spells each derivation folded to a short atom (§2); an object the component's tree entry names |
| **token** | half of a ref, opaque: an occurrence's `o:` (hashed from its parent's token, its name and its rounded placement; an ordinal only tells identical siblings apart). An element no construction names -- every element of a file read -- goes by its **geometric token**, `f:`/`e:`/`v:` and twelve hex characters hashed from its geometry within its body: the fallback matcher, measured on demand (§2) |
| **document** | a CAD file as a reader takes it, named by its bytes' hash: `index/document` maps a STEP's bytes to one tree -- the result it was exported from or, for bytes no build wrote, its own content (§3, *Exports*) |
| **export** | a file a model owes that is written from its stored result, never by running its body: its STEP, by the build pool (`store/exports.py`), with the names writer -- each part's key and each face's and edge's name in the file; its sidecar, from its record; its declared meshes, cut from the tree its STEP's bytes map to (a mesh-only model's, from its result). Written and published by a pool job started as the result is recorded (§3, *Exports*) |
| **preview revision** | what a build announces the moment its result exists (§9b): the result tree its record names and its STEP is exported from, every component's part key and the annotations the sidecar will carry (articulation, finishes, keyframes), resolved against that tree -- artifact-side objects a viewer draws labelled as a preview until the written file replaces them in place, every part kept |
| **provenance** | where a build's features ran: the run's trace, kept as a code-side entry per model, `index/provenance` (§3) -- each feature's name, operation, file, line and inputs, and the components the run made -- read by `store where` and by nothing on a render path |
| **obsolete** | a part entry an older version of cadgen's derivation wrote, a result another cadgen stored by key, or a record, document or provenance entry at a key this cadgen does not own (another schema version's): no reader of this cadgen asks for it again, and the sweeper retires it once it is a week old (§2, §8) |
| **worker / spare / extra**, **job** | daemon vocabulary (the daemon's own documentation) |

Retired words: node, package, manifest, ref and scope (as store concepts), blob,
pin (a child's tree as its parent recorded it: a parent records the child's
key), and the read-back side's: read-back in any spelling (a tree parsed from
the STEP a build wrote, standing for that file -- the file is an export of the
result, §3) and correspondence (that tree's pairing with the result). They name
nothing in the store, in its code or in its documentation.

One word is NOT retired, and it has exactly one meaning:

- **descriptor** — a render/export-side word: the owned descriptor an
  appearance is applied to (README law 17). It is never a synonym for a tree,
  a component or a record; those three have their own words above.

## 2. Layout

```
~/.cache/cadgen/                      (CADGEN_CACHE_DIR overrides; else the platform cache dir)
  objects/ab/cdef…                    immutable, content-addressed, sharded like git
  index/document/<sha256(file bytes)>-v<schema>  ARTIFACT side: {schemaVersion, tree, kind, producer?, meshes?} for a file's bytes
  index/model/<sha256(script::function)>-v<schema>  records (input-addressed, mutable, atomic)
  index/output/<sha256(output path)>  {model}: which script wrote the file at this path
  index/provenance/<sha256(script::function)>-v<schema>  CODE side: which operation at which source frame made each component and element of the model's current result (§3)
  index/result/<key>                  CODE side: a model's result under its key -- the record and provenance written for it, and its tree (§3; derived, evictable, §8)
  index/component/<cid>               geometry-input entries → encoded BREP and intrinsic recipe
  index/bounds/<sha256(bounds key)>   boxes, leaf layouts, topology counts, geometric tokens and faces' exact areas of stored geometry, inline
  index/part/<digest>-v<version>      a part's display data → its display mesh (GLB) and selector table objects
  index/drawing/<sha256(scheme + document hash)>  a 2D drawing's render payload → object hash
  index/skin/<sha256(scheme + document + animation + meshes)>  a document's tube skins → object hash
  index/robot/<sha256(scheme + path + description hash + paired URDF hash)>  a robot description resolved for a player → object hash, plus its primitive meshes
```

Nothing else lives under the root. A build's progress is process state, not
content: the daemon's job ledger, read over its socket (§7, §9). Editing
previews use the same immutable objects, with ephemeral request handles in
that ledger (§9b); there is no preview directory or persistent session index.
A **retired kind** is one an older cadgen wrote and this one never reads
(`RETIRED_KINDS`, `store/paths.py`): `index/op`, the operation cache of cadgen
0.7.4 and earlier, and `index/surface`, `index/selector` and `index/mesh`, a
part's display data before `index/part` named its two objects in one. The
sweeper removes a retired kind's entries a week after their last write, with
every object only they named (§8): the release before parts still reads the
last three, and one sharing the store derives each again at most once a week.
Retiring a kind means moving it from `INDEX_KINDS` to `RETIRED_KINDS`. Any
other folder under `index/` belongs to a newer cadgen or to none, and nothing
here touches it.

An **obsolete** entry is the same thing one entry at a time, in a kind still
in use: a part entry whose key names an older version of cadgen's derivation
(`parts.obsolete_key`: the `-v<version>` its key ends in is older than
`PART_VERSION`). No reader of this cadgen computes that key again, but an
older cadgen sharing the store still may, and a read never writes. So the
sweeper retires an obsolete entry, with every object only it named, once it
was last written a week ago (`gc.OBSOLETE_RETIRE_AFTER_SECONDS`, §8): an older
cadgen still in use derives each again at most once a week, and an upgrade's
leftovers go within about a week. A newer cadgen's derived entry is never
obsolete. Kernel versions are not ordered here: a part of another build123d or
OCP is left to the cap.

Records, document entries and provenance entries carry their schema version in
their key, `<key>-v<schema>` (`index.versioned_key`; `RECORD_SCHEMA_VERSION` and
`DOCUMENT_SCHEMA_VERSION` in `store/records.py`, `PROVENANCE_SCHEMA_VERSION` in
`store/provenance.py`). A cadgen reads and writes only the keys of its own
versions (`gc.owned_key`): two cadgens of different schemas sharing a store --
a plugin pinned to one release, a checkout on the next -- each compile a
document and build a model once and then find their own entry, and neither
rewrites the other's (and with it its mesh ledger). A format change bumps the
version and so lands on new keys; nothing reads, recognises or migrates another
version's entry. Any key this cadgen does not own -- another schema version's,
or the unversioned key a cadgen before this rule wrote -- is obsolete here,
retired a week after its last write like the others (§8), and until then the
trees it names stay reachable. A result's key hashes its producer, so no other
cadgen asks for it, and another cadgen's result is obsolete here (§3).

`index/bounds` holds facts measured from stored geometry (`store/bounds.py`),
each keyed by what was measured and how -- a component's BREP object hash or a
leaf's BinTools digest, the placement it is measured in, the algorithm, and the
loaded kernel's versions -- and each a pure function of its key, so a hit never
differs from a measurement and an unknown kernel build just measures: bounding
boxes, six numbers inline; each component's **leaf layout** (how many leaves
its encoded shape has and where each sits), recorded as a publication measures
them and read to name per-leaf box keys without decoding the shape, by a
parent's bounds over its children's components (§6) and by the animation bake;
each component's **topology** (its face and edge counts, how many of each are
curved, its loose box), by which the adaptive edge policy classifies a result's
components (`step_scene_mesh.prototype_topology`,
`store.build._edge_rendering`) -- a build measures its own as it walks them and
reads a linked child's by the BREP its tree names, decoding one only where the
entry is missing, so no document of the result is assembled for them;
geometric tokens (below); and a face's exact area and centroid, keyed by its
component's BREP and the face's ordinal, measured when a view or a mate's axis
asks for them (§3, *A face's area*). Nothing in the index holds a value computed while a
model runs (README law 18): a model's own checks and operations always execute.

### The two sides of the store — a law

`objects/` and `index/document` are the **artifact side**: what geometry exists,
and which tree a file's bytes stand for. `index/model`, `index/output`,
`index/provenance` and `index/result` are the **code side**: what source
produced a result, what it depended on, which of its operations made each
piece, and the results a model's inputs have produced. The other kinds remember
reusable derivations, and part and drawing jobs consume only immutable artifact
inputs. `index/drawing`, `index/robot` and `index/skin` are render payloads,
each keyed by its scheme with the content hashes it is made from, so the same
bytes are never resolved twice and an upgrade lands on a new key instead of
invalidating an old one in place: a 2D document's flattened drawing (the
payload shape × the drawing library's release); a robot description (URDF,
SDF, or an SRDF with its paired URDF) resolved into the articulation and visual
list a page plays (`cadgen.robot_payload`; keyed by the description's path too,
since the payload names each link mesh by the absolute path it resolved beside
it, and naming the primitive meshes it draws, which a sweep keeps with it); and
a document's bending tubes as a CAD view plays them
(`_internal/tube_skin_payload.py`: the document, the digest of the sidecar
animation that bends them and the parts whose meshes they bind).
`cadgen export <file>` is a code-side handoff, not an artifact reader: it follows
`index/output` to the qualified model record even when source no longer exists,
and completes that record's export without executing or importing the source.
It never changes the artifact-only lookup used by readers below.
`index/document` is the document lookup: `sha256(file bytes)`, under this
cadgen's document schema (above), → the one tree those bytes stand for (§1,
*document*), plus a mesh ledger keyed by format × tolerances × pose ×
appearance, which the bare mesh doors read and write and a script run notes its
declared meshes in, so the two front doors never redo each other's work. Mesh
variants and model output entries carry their format's final serializer
revision, so a change to one format's encoding invalidates that format's final
exports alone. Animated exports capture the sidecar's `animation` keyframes
once, before mesh preparation, and the animation variant consumes that same
immutable text. Three properties, each enforced by a test:

1. **No object references a source file.** No tree or component carries a
   path, a script name, a line, a closure or source hash, or a record key.
   What an object carries of its making is its geometry's identity (§2): a
   component's construction key, and the lineage object that names its
   elements by features -- a function's name, an operation and an ordinal,
   never a file or a line. The same construction anywhere on disk is the same
   tree, and a file read is named by its bytes alone.
2. **A reader never consults a record.** A reader (`read_scene`, `snapshot`,
   `stl|3mf|glb build`, `step build` on a document), the viewer's catalog and
   render, and the mesh ledger find a tree in ONE lookup — hash the file's
   bytes, read `index/document`, read objects — and never open the code side:
   `index/model`, `index/output`, `index/provenance`, `index/result`. A miss is
   a compile job, never a refusal. The viewer reads no record at all — no
   exception: its status is artifact-side (not compiled / compiling / failed)
   and it never learns which model wrote a document. "Is this document behind
   its source" is `cadgen store why`'s and the build tree's question.
   Once selected, the tree and its document digest travel together. A reader
   must not select geometry, then hash a possibly replaced file to bind its
   annotations or export ledger. A snapshot rejects a topology manifest from
   a different selected document instead of combining revisions.
   A tree that links its children's (a model's result) reads flattened: the
   hierarchy its file was written with.
   `read_scene` retains verified native bytes and decodes prototypes on demand.
   Its occurrence and selector views stay bound to that captured revision;
   each `shape()` returns privately copied topology. An already open scene
   survives document replacement or deletion of its cached objects.
   A build's preview revision is artifact-side too: objects the build feed
   names, drawn labelled as a preview until the file is written, whose bytes
   then map to that very tree (§9b).
3. **Records are deletable.** `rm -rf index/model index/output
   index/provenance index/result` loses no artifact: every reader still works
   from objects; a rebuild re-creates the records without rebuilding a tree
   whose objects exist, and only `store where` knew anything that is gone.

`index/document` is written whenever a tree is published for a document: by a
model's build for the bytes its export wrote (the result they were written
from), by a compile job for the file it compiled. `index/output` is written
with the record, for `store why` and `store where`, which follow it to the
record and the provenance entry (§3); nothing in the viewer or on a render
path reads the code side.

There are exactly two ways a file is named, and that is the only distinction
the store makes:

- **Content-addressed** (`objects/`): the name is `sha256(bytes)`. Geometry
  objects are a **component** (the `.brep` bytes of one solid, and an
  eager-only component's pinned topology facts) and a **tree** (JSON); a part's
  display mesh and selector table are derived objects beside them. Writing an
  object is idempotent; valid bytes are never changed. Geometry completeness
  requires the entire verified required closure before publication. Display
  readiness is separate and disposable. A repair may restore bytes at their
  exact hash.
- **Input-addressed** (`index/`): the name is derived from what PRODUCED the
  entry (a script path, measured bytes and their placement, a component under a
  producer), and the entry is a small JSON file pointing at objects or recording
  facts. Entries are mutable and written temp + rename.

No directories per result, no hardlinks, no staging directories, no version
salts in object addresses. An input-addressed entry's key includes what its
value depends on, its extraction algorithm or schema version along with its
inputs; this does not change the content address of any object it produces.
The `.step` document, its sidecar and declared mesh files are
**outputs** in the project, not store contents; the record lists them with shas.

### Identity and versions

A component's id (cid) is the first 16 hexadecimal characters of its
`contentHash`, a hash of exactly these inputs
(`component_package.geometry_component_hash`): the string `GEOMETRY_SCHEME`
(currently `cadgen-geometry-input-v4`), the component's kind and codec, its
BREP bytes, its intrinsic face colours, an eager-only component's pinned
topology facts (§3), and the construction key and the lineage object its build
named it by (below; none for geometry no construction of a run made). That
string is the **only** version on a component's identity. Everything derived
from a component — its part (`PART_VERSION`, in its key), index payloads (their
`schemaVersion`) — carries its own version and validates its own
compatibility, so a fix in a producer retires that layer's entries alone and
never moves a cid.

- Bump `GEOMETRY_SCHEME` only when the same inputs must map to a different
  tree: a codec or interpretation change. It re-keys every user's store, so
  it is rare and deliberate, and the reason goes in the commit and here.
- Bump the derived artifact's own version for an extractor, selector-table,
  mesher, verifier or mesh-payload fix (`store/parts.PART_VERSION`). Never
  reach for geometry identity to invalidate a derived layer.
- Wiping a store is an operator action (`cadgen store gc`, `store forget`),
  never a hash side effect.

**A part is the construction that made it** (README law 20). The run's kernel
memo (README law 18) keys every construction the model's code calls -- the
operation, every setting it runs with (a boolean's fuzzy value and glue mode,
build123d's `SkipClean`, the bytes of the font a text reads), its parameters by
value and the keys of the shapes it was given -- and
`kernel_memo.construction_key` restates the key of the construction a
component's body came from as a stable digest: that call, the output of it the
body is (a split kept both ways is two parts), the effects later calls had on
it, and cadgen's version, so a key never outlives a release that might
construct differently. It is a function of the construction alone: nothing the
worker accumulated -- other models built before, other versions of this one --
enters it, and `CADGEN_KERNEL_MEMO=0` (hits off) gives the same keys. A part's key hashes
that construction key, the component's lineage object, its intrinsic face
colours and the producer (the kernel its display data is derived under). A
model's file maps to the result it was written from (§3, *Exports*), so a view
of the file draws the build's parts, the ones its preview named (§9b). A
construction the memo cannot key (an argument it
cannot hash) has no construction key, and neither has a shape no construction
of the run made (a child's decoded result, a file read), nor anything made of a
child's decoded result: the memo keys a decode by the child's tree hash for the
hits of the process that decoded it (§9a), and no construction key names a
decode, so what a parent makes of its child -- a cut, a fuse, a copy -- has
none, cold, hot or with hits off, whether the child was built inside the parent
or decoded. Such a component's
part is keyed by its content hash, its bytes. Nothing measures geometry to
name a part, and nothing but display data stands on a part's key: the gate,
the publish rule, the sweeper and every object address stay with bytes.

A whole-body `BRepBuilderAPI_Copy` keeps that source construction when its
reported same-element mapping is complete and bijective. This proof uses the
Copy's own source and result frames, even when an ancestor placement leaves its
elements without ordinary origins in the enclosing call's frame. Those ordinary
origins remain unknown. The copy freezes
the source's stable construction material, independently of the runtime memo
key of the enclosing call. An uncached assembly placement's copied descendants
stay uncached; copied foreign geometry gains no construction key. Later effects
on the source do not rename its independent copy, and geometric effects on the copy do
not retain its pristine identity. Partial or ambiguous histories and a source
changed within the same call establish no whole-copy identity.

**An element is its lineage.** The run's trace (`_internal/kernel_trace.py`)
records each kernel call from the model's own code as a **feature**, named
within each part among the features the part descends from (those that made
it and, transitively, whatever they were given), in call order:
`<function>.<operation><count>`, the enclosing function, the operation, and how
many of those features were that function's calls of that operation, this one
included (`bracket.extrude1`) -- or by the label an author gave the shape it made,
once that shape is handed to another construction, which pins the name (`lid`)
in every part made of that shape from that call on, the part's first feature to
claim a label keeping it; a part made before the label reached a construction
keeps the names it had, so labelling a shape after using it renames nothing
already made. The part the labelled shape is itself -- an assembly's leaf -- is
not named by it: there the label names the occurrence (`#lid`), so identical
constructions labelled apart (`screw:0`, `screw:1`) are one part, written once
and placed twice. A function or label that is not an ASCII identifier is
written as its ASCII form and the first eight hex digits of its sha256, so no
two are written alike.

An element's name is its **derivation**, from what the kernel reported, composed
as FreeCAD 1.0 composes it (`TopoShape::makeShapeWithElementMap`). The makers a
feature runs -- a boolean and the clean after it, a prism, a fillet, the face of
a wire and its fixes -- are recorded while it runs and asked, of each element
of the feature's inputs, what they modified or generated from it
(`_internal/kernel_history.py`), and the feature names every element of its
results from its inputs' names: an element an input holds unchanged (every
boolean here leaves alone what it does not change), or holds at another place
of the same shape (a placement), or a copy of one, **is** that element and has
its name; one the makers report is named from its first source -- a vertex
before an edge before a face, then by input, then by name -- and the step the
feature took, `M` modified, `G<kind>` generated from an element of that kind,
`MG<kind>` both, with up to four further sources (`f:bracket.make_box1.6;M:bracket.cut1`,
the trimmed sixth face of the box; `e:sk.make_rect1.2;Ge:part.extrude1`, the
side an extrusion generated from the rectangle's second edge, whose top face is
`f:sk.make_rect1.1;Gf:part.extrude1`); an element no maker reports is named
from a named parent -- an edge from a face, then a vertex from an edge, the
first by name, with its number among that parent's unnamed elements (`U`) --
or else from its lower elements, all of them named (`L`: an edge from its
vertices, a face from its outer wire's edges); and only an element left with
no source at all is the feature's own, its number among the elements of its
kind the feature made in the order of its results (`f:bracket.make_box1.6`:
every element of a primitive, and what the passes cannot reach). Elements of
one result a step made alike -- from the same sources, the same way: the pieces
of one face a cut split, the pieces of a ruled loft's long edge, reported as
generated from every vertex on it -- are numbered in the step, the second and
later by their place among what was reported of their first source (`;M2:`,
`;Gv2:`, FreeCAD's index), so a later operation that changes one renumbers no
other; of a component's elements still named alike -- two places of one shape
in one body -- the second and later, in the component's order, take `;D2`,
`;D3`. A derivation longer than 48 characters is folded to
the feature of its last step and the first twelve hex digits of its sha256
(`bracket.cut1~5d1e0c2f9a3b`), a further source that is no atom is folded
whatever its length, and a folded atom takes further steps like any other, so
every name stays short; the component's lineage object keeps what each folded
atom stands for (its `table`, §3), every one its names or that table reach.
Nothing is measured: no surface, curve or extent is compared to name anything.
A certified whole-body copy also keeps the source component's naming context:
its own ancestor closure, body and label cutoff, with the copied ordinals
remapped by the maker. Moving a whole assembly therefore cannot count a
sibling's primitive makers in a leaf's names or invent names for foreign
leaves. Derivation structures are captured at the copy boundary and names are
rendered on demand; a memo hit binds the context to the current run's features.
Each run retains its own native registrations and maker bindings: a nested
run's hits or entry replacements cannot remove the parent's source provenance.
An unreplayable native mutation retires the shared registration in every run
that still holds it; ordinary eviction does not invalidate unchanged geometry.
A later consuming construction can pin an author label to that particular
copied output. Distinct outputs of one assembly copy keep independent labels,
even if their source bodies shared native geometry; earlier geometry keeps its
original naming cutoff, and provenance can name both outputs at that copy call.
Simple Copy origins are stored as immutable feature-input indices and global
ordinal vectors. Reads reconstruct their exact history tuples, independently
of whether the whole-copy certificate is later withdrawn. Every native query
and placement lookup finishes during composition; ambiguous or chained reports
expand into the ordinary origin representation before composition returns.
An element passed on unchanged from a shape no feature made -- a child's, a
file's -- has no name and names nothing (it is no source, parent or lower
element of a name); an element made out of one alone falls to the passes, and
to the feature. A feature's file and line are never in a name -- they are the
provenance entry's (§3) -- so moving code renames nothing; a name changes when
its derivation does: a feature that starts or stops modifying the element, a
call of the same operation inserted earlier in the same function among the
part's own features (which renumbers the later ones, as a label never is). An
edit anywhere else in the model renames nothing. The grammar, which the
client's `cadRefs.js` mirrors (`cad_ref_syntax.ELEMENT_NAME_PATTERN`):
`[fev]:` and an atom -- `<feature>.<n>` or `<feature>~<12 hex>`, a feature one
or two segments `[A-Za-z_][A-Za-z0-9_]*` joined by a dot, numbers from 1 --
and its steps, each `;<op>:<feature>` with up to four `+[fev]:<atom>` (`op`
one of `M[n]`, `G[fev][n]`, `MG[fev][n]`, `U[n]`, `L`) or `;D<n>`. A construction the
memo cannot key is still a feature: its elements are named like any other's,
and it runs again on every run. The history a miss composed is kept with the
memo's entry, and the naming computed from it with that, so a hit -- which
hands back the miss's shapes -- names as the miss did without running a maker
(README law 18).
When every element of a requested result follows its first geometric input
unchanged, the trace can complete that result alone, including unknown foreign
elements. Complete identity tables are kept separately from full feature
structures as input indices and ordinals; hits bind them to the current run.
Native first-owner indexes are shared across requested siblings only within
that trace. Any unproved element uses the full feature computation in result
order, preserving its made ordinals. Certified copies still freeze their full
source structures and ordinal bindings before later geometry changes.
If model code makes another kernel call after a partial account, the trace
first completes those observed features, preserving their pre-call topology
and source ordinals even when that call changes native geometry in place.

**Geometric tokens are the fallback matcher.** An element no lineage names --
every element of a file read, and those a model took from a shape no feature
of its run made -- goes by its **geometric token**
(`_internal/geometric_tokens.py`): `f:`, `e:` or `v:` and twelve hexadecimal
characters, a hash under `GEOMETRIC_TOKEN_SCHEME` of the element's geometry
within its body (a face's surface class, its tight box rounded relative to the
body's extent and its oriented normal at its parametric middle; an edge's curve
class, its rounded end points and length; a vertex's rounded point; two
elements with equal signatures ranked by their exact values), so a token
survives a STEP round trip and whatever order OCCT lists the elements in.
Tokens are measured on demand and never on the way from a model's run to its
record: where a part's display data is derived (in the build pool, for the
elements its lineage does not name), and where a ref in token form -- one
copied from a file read, or before its element had a name -- is resolved
against a body named by lineage (`refs.py`, the one resolver: `read_scene`,
`store where`, a mate's axis, every ref). Either way a
body's tokens are remembered in `index/bounds` by its codec and BREP object
under the scheme string (`bounds.cached_tokens`), so they are measured once.

- Bump `GEOMETRIC_TOKEN_SCHEME` when the same shape must be named differently
  -- a change to what a signature reads or how it rounds -- and bump
  `PART_VERSION` with it, so the parts whose tables carry tokens at every old
  key are obsolete (§2) rather than left to the cap.
- A change to how the trace names features or follows lineage renames the
  elements a ref already named, and needs no bump: the lineage objects change
  with it, and the construction key carries cadgen's release, so each
  release's components and parts are its own.

## 3. Tree and record

### Tree

A tree is the JSON a model's build writes; its hash is the result's content
address. An example (`arm`: a bar plus two placements of a `peg` model):

```json
{
  "kind": "geometry-tree",
  "schemaVersion": 6,
  "label": "arm",
  "entryKind": "assembly",
  "units": "mm",
  "components": {
    "0c5932ad05ce64a6": {"kind": "native", "codec": "bintools-v4", "brep": "ebe7552f…", "faceColors": {}, "contentHash": "0c5932ad…", "construction": "7d2e91c0…", "lineage": "41b07e3a…"}
  },
  "occurrences": [
    {"id": "o:4b1f9e2a7c03", "name": "bar", "component": "0c5932ad05ce64a6", "transform": [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]}
  ],
  "links": [
    {"id": "o:e8a03d5b61f4", "name": "peg_left",  "tree": "265aee57…", "transform": [1,0,0,-15, 0,1,0,0, 0,0,1,2, 0,0,0,1]},
    {"id": "o:19c7f2d4a8b5", "name": "peg_right", "tree": "265aee57…", "transform": [1,0,0,15,  0,1,0,0, 0,0,1,2, 0,0,0,1]}
  ],
  "assembly": {"root": {"id": "o:0f3a6c8e2d17", "name": "arm", "nodeType": "assembly", "children": [
    {"id": "o:4b1f9e2a7c03", "name": "bar", "nodeType": "part", "leafPartIds": ["o:4b1f9e2a7c03"], "children": []},
    {"id": "o:e8a03d5b61f4", "name": "peg_left", "nodeType": "link", "tree": "265aee57…", "children": []},
    {"id": "o:19c7f2d4a8b5", "name": "peg_right", "nodeType": "link", "tree": "265aee57…", "children": []}
  ]}},
  "bbox": {"min": [-20, -4, 0], "max": [20, 4, 14]},
  "stats": {"occurrenceCount": 1, "linkCount": 2}
}
```

- `components` are geometry this model created itself, keyed by component id
  (`cid`, §2); each names its encoded `.brep` -- the exact unlocated BREP --
  its declared codec and its intrinsic recipe, and, where a construction of the
  model's run made it, its `construction` key and its `lineage` object (§2):
  what its part is keyed by and what names its faces, edges and vertices. The
  lineage object is canonical JSON (`component_package.lineage_bytes`):
  `{"made": <the feature that made the body>, "faces": [...], "edges": [...],
  "vertices": [...], "table": {<folded atom>: <derivation>}}`, each list the
  elements' names by ordinal, `null` for one passed on from a shape no feature
  made, and the table every folded atom a name or an entry reaches, and nothing
  else (`read_lineage` checks it): what the derivations stand for in full, the
  string table of FreeCAD's element maps, so the object is the content-hashed
  record of the names and a name in a file stays short. The recipe is each face ordinal's effective RGBA: absent native face
  ordinals are removed before hashing, and ordinal keys become strings before
  canonical sorting, so a JSON round trip past ordinal 9 keeps the identity. A
  uniform colour stays occurrence metadata; named PBR definitions and leaf
  assignments live in the tree's top-level `appearance`. Extractor, native
  runtime and display-data readiness never enter a component's or a tree's
  identity.
  Every occurrence, link and node is named by its **token** (`o:` and twelve
  hexadecimal characters): a hash of its parent's token, its name and its
  placement rounded (`_internal/tree_tokens.py`), with an ordinal appended only
  when two siblings share all three, and the root's from the tree's label
  alone. A token is therefore a function of the tree it is in, and of nothing
  outside it: inserting, removing or reordering a sibling changes no other
  token, the flattened tree (below) re-roots a child's tokens under its link's
  as the hierarchy of the model's file names them, and a sidecar's bindings --
  appearance assignments, kinematics, animation tracks -- name leaves by these
  tokens, which a compile of the file mints again from the names and
  placements it states. A model's result holds the exact authored geometry, in
  canonical BREP bytes; it is the same result on a miss and on a hit, at the
  top level and in a child call, whatever the model declares or a viewer
  shows, and a model's STEP is written from it (*Exports*, below).
  `occurrences` place components; `links` place children's trees. Two
  placements of one child are two links to one tree. Transforms are 16
  numbers, row-major, translation in the fourth column, in the parent's frame.

  Native components are privately decoded before publication. BinTools v4
  retains the existing decoded native semantics and original payload while
  checking exact point-bearing vertex records and referenced placements. Known
  point-parameter loss falls back to pinned BinTools v3, then BRepTools ASCIIv3;
  both alternates require full original native bytes and their own byte fixed
  point. Declared headers are checked before decoding. This is a specific
  known-hazard fence, not an equivalence claim for every possible native state.
  If no codec passes, an explicit `eager-only` component pins its topology
  facts, extracted from the live shape at capture, as an object in its geometry
  identity (`topology`): the facts its selector table is built from, as a JSON
  object (`_internal/topology_facts.py`), since the lossy bytes cannot stand in
  for the shape. What the facts hold is therefore part of an eager-only
  component's content hash and of no native component's. Native access raises
  `NativeUnavailable`: a reader of a file holding one may privately reparse
  that file's exact selected bytes, and the build exports the file in its own
  process from the live shape (*Exports*). A compile skips the private decode
  for a parsed prototype whose exact bintools-v4 bytes already exist as an
  object under a component entry declaring the same native recipe: the fence
  was proven for those bytes by the build that published them, and the parsed
  prototype — private to its parse, measured but never meshed — stands in as
  the prepared native input. A forced compile derives and fences every
  component again.

  **A worker remembers the components it published.** Preparing a component --
  naming it (`kernel_memo._Run.account`), encoding, privately decoding and hashing
  it -- is most of what a publication costs, and the kernel memo hands an unchanged
  part back as the very kernel object it was. So a worker keeps each component it
  published, in its memory and never in the store (`store/build.py`,
  `_Remembered`): its prepared entry -- BREP bytes and digest, face colours,
  construction key, lineage object and hash, content hash -- by the live object it
  was prepared from: the TShape, its orientation, its face colours. A publication
  of that object takes the remembered component in place of preparing it again,
  and only while all the preparation read is as it was: the construction key the
  run names it by (`kernel_memo.construction_key_in`); the rows the memo's ledger
  holds on each of its sub-shapes (`kernel_memo.ledger_snapshot`: every change the
  memo made to one since its maker returned it, flags included, which BinTools
  writes -- the representations an extrusion appends to a profile's edges the part
  shares, the flag a compound clears), with none met since of the changes the memo
  counts as unreplayable, failed to capture, or made to memoized geometry under an
  uncached call on a wrapper of it (`unreplayable`, `errors`, `tainted`: it notes no
  row for one, on whatever shapes share the sub-shape); and the run's naming of it
  (`kernel_trace.account_basis`): the features it descends from and those their
  inputs are registered to, each by its history (its memo entry's, which a hit
  hands on), with its operation, function, inputs and copy contexts, and the labels
  pinned on them by the time the part was made. Anything else prepares it as a
  fresh worker would, so a remembered component changes no tree, component, lineage
  or part key: a new kernel object, an appended representation, a recoloured face,
  a label pinned on a feature it descends from by a call it does not, is prepared
  again; a part relabelled or moved -- its occurrence's change -- is not. With hits
  off nothing is recalled, as every run makes new objects. Each feature name of a
  recalled component's lineage stands, in the provenance of the run (*Provenance*,
  below), for the feature holding the history it stood for. A change the memo does
  not see (a representation rewritten in place, a mutation outside the patched
  surface) is seen by neither the memo nor this, nor is one it forgets a root for
  without counting it (an uncached call changing a memoized root it was handed
  inside a foreign shape) on a sub-shape that root shares with a part that is not
  it: the root's own parts lose their construction key, and are prepared again. A
  worker remembers only what the memo holds, and forgets at its next publication
  what the memo let go (no hit can hand it back, and nothing here keeps it alive),
  within 8192 components and 512 MiB of their bytes, the least recently recalled
  first; a worker the daemon reclaims takes it all with it. The sub-shapes are
  hashed, to read the ledger's rows on them, the first time a component is
  recalled: until then, one that never comes back costs its publication only its
  naming's restatement. A recalled component was checked against its bytes by the
  publication that prepared it; its store objects are checked as every
  component's are.

  A view of a tree (`store/view.py`, `parts.view_from_geometry`) names a
  runtime producer separately from the tree: the loaded build123d, OCP
  and distribution versions. Each component of a view names its **part**: the
  key its display data is stored and read by, `<digest>-v<version>`
  (`parts.part_key`). The digest hashes what made the component -- its
  `construction` key (§2), or its `contentHash` for geometry no construction
  made -- its `lineage` object, its face colours and the producer, so a preview
  and the file exported from it draw with the same parts (§9b). The version
  (`parts.PART_VERSION`) is bumped by any change to the extractor, the selector
  table, the mesher, the verifier, the mesh payload or the geometric token
  scheme, and by any change in how the digest is hashed, so the parts at every
  key before it are obsolete (§2). No tolerance is in the key: a part has one
  display mesh. Unknown versions cannot create a shared persistent namespace.
  Derivation privately decodes only captured inputs; no source, model record,
  latest child or live authored shape is consulted.

  **A build has its parts' display data derived; views only read.** A build
  never measures or meshes geometry on its own thread: the moment its result
  exists -- a model's or a re-emit's -- it asks the build pool for the parts of
  its own components the store lacks (`parts.request_display`: one job, `kind:
  display`, or on the daemon's pool, for a batch past half a megabyte of BREP,
  one per such share, balanced by size, a pool slot each but the build's own)
  and queues them on its run. The command answers once its result is recorded;
  its worker awaits these jobs before finishing the files. A linked child's parts
  are its own build's, so a parent asks
  only for its own. A document compile queues every part of its tree on the same
  run and answers once its complete geometry is published and mapped to the
  captured document bytes. Native readers can use that geometry while display
  jobs finish; eager-only components still refuse exact native reconstruction.
  A direct call without a worker drains its display jobs before returning.
  A model whose result stands asks for those of the
  tree its file maps to (a mesh-only model's own), links expanded, when the run
  finishes its exports (below), after the STEP it may owe and before its
  declared meshes, and the children healed under it ask for none (§4).
  Which parts lack display data is asked of the store alone (one probe per
  part, no kernel import), so a model whose display data is stored starts no
  job. Display jobs finish after the result's `done` event, so their time is
  outside the model's time line. A part that fails to derive is a
  warning on the build, never its failure: the geometry is published, and a
  view goes on without that part, saying so (below). A run that fails after
  publishing a result still finishes the display jobs and exports it queued;
  its worker reports any finishing failure through the ledger.
  A build's kinematics read components only where a selector is named: a mate's
  parent and child resolve in the result tree's occurrences, group nodes and
  their labels, with no component read, and an `axis={"ref": ...}` reads the
  selector table of the one component its occurrence places, once the build's
  display job is done (a part it did not cover, by a job of its own;
  `_internal/kinematics_resolve.py`). A declared mesh export at a tolerance
  other than the display one still meshes in the build's process
  (`parts.mesh_at`, below). `read_step` never waits for a part.

  A view -- the CAD Viewer and the CAD app (`POST /__cad/parts`), a snapshot
  and `--mode list` (`store/view.py`), a mesh export -- reads what the build
  derived. A part the store lacks (a cleared, evicted or older store) is derived
  by ONE build-pool job for the request (`kind: display`,
  `daemon/artifacts.py`): one request, one job, one worker -- a daemon
  worker's, never a request's thread -- and correctness
  never depends on a store hit. A part that job cannot derive is left out of a view, which names
  it by the occurrences that place it, as a build's warning does: the CAD
  Viewer draws the rest under a warning, a snapshot and a list draw and list
  the rest and warn (the snapshot view's component carries why, `failure`,
  `store/view.py`), and one with nothing else to show fails. A mesh export
  fails instead, naming its file and the part: a file without a part is not
  the file asked for.

  **A face's area** is no part's: no reader takes it but a person looking at
  one face -- a view's Reference panel, its measure tool -- and a mate's axis
  through a face whose parameters give no origin (a torus's give its centre),
  which passes through the face's centroid (`_internal/kinematics_resolve.py`).
  So no part stores a face's area or centroid, and its derivation measures one
  only where the mesher must (a face its first pass leaves all but empty, below)
  or a geometric token reads it. A face is measured where it is asked for,
  exactly, on its component's stored BREP (GProps, `store.parts.measure_face`)
  -- for a view by ONE build-pool job per face (`kind: measure`), for a mate's
  axis by the build that resolves it -- and remembered in `index/bounds` by the
  component's BREP and the face's ordinal, so each face is measured once. A
  view asks `POST /__cad/measure` with the view it holds (`tree`, `viewId`,
  `producer`), the component and its part key (`cid`, `part`) and the face's
  ordinal (`face`): the store's answer at once, else the face's job, which
  every request for it joins while it runs, waited for only as long as the
  app's hold, then a 202 `{state: "measuring", retryMs}` that has the page ask
  again (`viewer/parts.py`, `FaceMeasures`); the server never measures.

  A part's display data is ONE index entry, `index/part/<key>`
  (`store/parts.py`), naming TWO objects -- its display mesh and its selector
  table -- with what a reader sizes each by without opening it: each object's
  byte length, the mesh's decoded-bytes estimate and counts (and
  `unmeshedFaceCount` when a face could not be meshed), the table's counts. One
  probe answers whether a part's display data is stored: the entry is valid and
  both objects are present at their sizes. Derivation (`parts._derive`) decodes
  the component's BREP once, extracts its **topology facts** in memory
  (`_internal/topology_extract.py`: ordinal tables of its solids, faces and
  edges holding exactly what the mesher and the selector table read and no
  tessellation input; an eager-only component's are the object its tree pins),
  meshes the component from them, builds the selector table from the BREP, the
  facts and the mesh, writes both objects and then the entry, so an entry
  always names complete objects. Nothing stores the facts but that pin, and
  nothing outside a derivation reads them.

  The **selector table** (`_internal/selector_table.py`, schema 6) is the one
  place a ref's facts are minted. From the exact BREP, the topology facts and
  the display mesh, per face, edge and vertex, the **element name** a ref ends
  in, which leads each row -- its lineage (§2), `f:bracket.make_box1.6;M:bracket.cut1`, or, for
  an element no lineage names, its geometric token, `f:9d2e8c1b5a70`, measured
  here, in the pool's job -- the parameters the facts carry and each edge's exact
  length and centre (no face's area or centroid: *A face's area*, above),
  each face's and edge's box (its display mesh's: a face's triangles', an edge's
  polyline's, so within the display chord of the exact box, which is measured
  where it is read -- a geometric token's signature),
  the plane normals, whether an edge can be referenced and its relevance, every adjacency
  (face–edge, edge–vertex), each edge's chain and each face's tangent group —
  the sets the viewer's connected selection grows a pick to. Ordinals stay in
  the table as the join to the mesh's face and edge tables, and never leave it:
  a ref is `<occurrence token>/<element name>`, and nothing positional
  (`cad_ref_syntax.py`, the one grammar the Python and the client share). The
  CAD Viewer reads the table when it needs it -- a pick, a measure, a node
  opened -- by the part's key, bound to the object its entry names
  (`GET /__tess_cache/<part>.selectors.json`), and joins it to the mesh by
  ordinal; a snapshot's `--focus` and `--hide` and a build's mate axes
  (`assembly_lookup`) read the same table from a view's
  `components/<cid>.selectors.json`, and `read_scene(path).resolve(ref)` names
  each element by the same name (`.entities("face")` lists them). So a ref the
  viewer's Copy gives resolves to the entity it names wherever it is read, and
  nothing in a browser mints one.

  Meshes are derived the same way, and by cadgen alone. The mesher
  (`_internal/tessellate.py`) runs OCCT's `BRepMesh_IncrementalMesh` on the
  exact BREP at the display tolerance (`tessellation_policy.DISPLAY_TESSELLATION`,
  the chord a fraction of the component's bounding diagonal), checks each pass
  for a face left empty, holed or off its exact surface and meshes a part with
  such a face again, whole and finer (unless its first pass took seconds),
  tessellates a face no pass gets right over its own parameters where that does
  no harm (`_internal/face_fallback.py`), and reads what the triangles do not
  say from the component's topology facts (each face's surface type, each
  edge's class) and its face colours. A body (`store/meshes.py`) is glTF 2.0 binary
  in the component's own units: ONE triangle primitive (positions, normals, u16
  indices up to 65,535 vertices, else u32), a face table whose rows give each
  face's ordinal, contiguous index range and colour, an edge table giving each
  edge's ordinal, class and run of polyline points, and the points; the JSON
  chunk is the canonical JSON for the body's part key, bounds and colour
  palette, which every reader rebuilds and requires before it views the arrays
  in place. Any glTF viewer draws it (its one node turns Z-up millimetres into
  Y-up metres); the tables are `extras`. A face no mesher covers -- OCCT refused
  it and the fallback's triangles missed its area by more than a quarter --
  never fails the component: it is meshed without that face, whose range is
  empty, and the body names it (`unmeshedFaces`, only in a body that has one;
  the part's entry counts them, `unmeshedFaceCount`). Every reader says so: a
  view or a list warns which faces of which part it does not draw or count, the
  CAD Viewer names the parts it draws without them, and a mesh export warns that
  its file has a hole there and is not watertight. A face smaller than the mesh
  resolves (area under the deflection squared) may have no triangles and is not
  named: its area is its first pass's triangles' where they cover that much, and
  is measured exactly (GProps) only for a face they leave all but empty. Any other failure -- OCCT raising, a body its
  encoding refuses -- is the component's `MeshProductionError`. A mesh export
  at the display tolerance
  writes the stored display meshes; one at any other tolerance meshes each part
  in the exporting process the way its display mesh was made -- from the BREP,
  its face colours and the topology facts extracted from that BREP
  (`parts.mesh_at`; the stored table may be another decode's of the same part,
  listing the elements in its own order) -- and stores nothing (the file is the output;
  `_internal/mesh_export.py`). A mesh of one BREP at one tolerance is the same
  bytes whichever body derived the part (README law 5). Tolerances outside the tessellation policy's
  bounds (`tessellation_policy.tolerance_refusal`) are refused, never meshed. No
  client writes a part: every reader probes and reads, and the routes refuse a
  POST to an object (405).

- `assembly.root` is the grouping the author's compound expressed; a link
  appears in it as a node of type `link`.

  **A model's STEP is an export of its result.** `record.tree` is the result,
  and a model that declares a STEP has it written from that stored tree once it
  is published (*Exports*, below): the file is a function of the tree and the
  writer. Its bytes map in `index/document` to the tree they were written
  from, so the file has no tree of its own -- its parts are the build's, its
  names the result's. The file written from the result is the result, and
  cadgen does not parse it to check: a file that reads back otherwise is a bug
  in the writer, fixed in the writer, never something a build or a door checks.
  A file no build of this store wrote is compiled from its bytes: the tree the
  compile publishes has the structure, names and placements the file has, each
  component read from the file with no construction key, its elements named by
  their geometric tokens.

  **Names are in the file; what they stand for is in the store.** The names
  writer puts each part's key and each element's name in the file (README law
  4), for any reader; a compile takes none of them. A store that never held
  the build that wrote a file -- another machine, an emptied cache, a result
  lost to damage -- compiles the file as it compiles any foreign file, and its
  elements go by their geometric tokens: a ref by name resolves where the store
  of the build that wrote the file is (or once the model is built again), and
  says so elsewhere; a ref by token resolves in either (the fallback matcher,
  §2).

  A compile of a document's bytes discovers no neighbouring model source: it
  passes the parsed scene straight to `store.build.build_document_tree`, whose
  fixed edge classes govern every compiled document, and leaves the STEP and its
  sidecar untouched; it verifies indexed component objects before reuse and
  derives failed entries again, and a known damaged closure or a forced compile
  derives every component. A model's and an annotated re-emit's tree is built
  from a compound (`build_tree_from_compound`), its edges classified from the
  topology it walks; public `read_step` geometry keeps its preparation.

  **Exports.** A build's STEP is written after its result and its record are
  published (§5, publish order), by the build pool (`store/exports.py`,
  artifact kind `export`, §9), which publishes it too: the job's request
  names the stored tree, the model, the digest of the result its record names
  (`exports.state_digest`: key, tree, resolved annotations, output recipes),
  the file, the bytes the build found there and what the model's previous
  export recorded, and the display jobs of the result -- never a script. The
  job stages the file beside the saved one, in a folder named after its own
  process (`temp_leftovers`), materializes the tree (`store.materialize`) --
  the build's own process assembles no document for it -- and writes it with
  the names writer: each part's key, under the producer the job runs (a
  runtime that cannot name its producer writes no key), and each face's and
  edge's name. It then waits for the result's display jobs (asking for the
  same work attaches to them), so a reader of the saved file finds its parts
  stored and never derives one twice, and publishes the file, in order: the
  sidecar written from the record (`exports.recorded_sidecar`); the expected
  pair checked (§7); the model's record read again, right before the write, to
  see that it still names this result; the staged copy and its sidecar moved
  into place, each atomically; the bytes mapped to the tree in
  `index/document` with the producer the export wrote with (never an older
  entry's hint), at once and finally; the record's output entry filled
  (`{sha256, writer}`, empty until then, which the gate reads as never
  written), merged into the latest record, which is read again first; and the
  file announced as saved (`published`, §9). A result holding a component the
  store cannot decode (an eager-only one, above) is written in the build's own
  process, from its live shape, and published there by the same function,
  before the command answers.

  **A newer result supersedes an older export.** An export writes only while
  the model's record names its result, and reads the record again before it
  writes, before the file replaces the saved one, before it writes the record
  and before each mesh. Once a newer build of the model has recorded its
  result, the older run's files are that build's to write: the older job
  answers that a newer result superseded it -- a run with no worker says so in
  one line (`<file> not written: a newer build of <model> replaced this
  result, and writes it`) -- and writes nothing more. A job also asks, before
  it writes and before it publishes, whether anyone still wants it (§9): one
  its build left before it began (a run with no worker, interrupted) writes
  nothing, and one already writing finishes into its staging folder, since an
  OCCT write is never cut off, and publishes nothing. A record gone with the
  store is no newer result: the publication fails, saying so. A child's export
  under its parent is superseded the same way, by the child's own record.
  Nothing waits on anything after the write: every door given the file -- the
  CAD Viewer's catalog read, `read_step` and `read_scene`, a snapshot, the mesh
  doors, `step compile`'s current check -- reads that entry and uses its tree.
  A door given a file a build is still writing waits for that write first
  (`doors.await_writes`), bounded by it: it asks the daemon's job ledger --
  process state, never a model record -- which lists each file a result owes
  the moment its job is handed over (`saving`) and the moment the file lands
  (`saved`, §9b), and waits while the newest result that owes the file has not
  saved it and its job runs. A newer result permanently supersedes older
  obligations in that ledger, even once its request finishes or its row
  expires; an older result's export is no wait. With no job writing the file,
  or no daemon, it reads at once. A build waits for a file its own request owes
  as for any other -- a pool job writes it, never the build -- giving up its
  job slot meanwhile. The viewer draws the build's preview while its catalog
  read waits. When two models write equal bytes, the last export's entry
  stands. The model's declared meshes (`@stl`/`@glb`/`@threemf`) are then cut
  by the same job, from the stored result tree and recorded appearance, keyed
  on the STEP bytes the job just saved -- never a door's lookup or a mutable
  disk sidecar; a model whose STEP is kept, or that writes none, has a mesh job
  (artifact kind `mesh`) for them alone, keyed on the STEP's recorded bytes or
  on its result tree. A mesh at the display tolerance is the store's display
  mesh; one at another tolerance is meshed in the job's worker
  (`parts.mesh_at`). Declared meshes are prepared in sibling staging folders
  too. Before each file replaces its destination, the job rechecks the record
  and the bytes the build found before the body ran (or its own previous
  export recorded meanwhile). It verifies the published bytes and binds the
  receipt to that result before recording it. A newer result discards the
  staged mesh; a foreign edit is refused, and neither changes the newer
  result's outputs. A mesh the record already names current is announced
  saved as it stands.

  **Exports are pool jobs, and the build answers without them.** Every call of
  a model, root or child, owes what its result owes the moment the result is
  recorded (`generation._Export`): its export or mesh job is started then, so a
  child's STEP is written on the pool while its parent's body goes on. In a
  daemon worker the job is handed to the supervisor (`authoring.supervised`,
  §9), which runs it, keeps the request's ledger row open until it ends and
  folds each file it saves into the row of the model that owes it; the build
  answers once every model's result is recorded, and its worker takes its next
  job at once -- nothing is left for it to finish. Text results include a
  `saving <file>` line for each file still owed; JSON results include `files`,
  mapping absolute paths to `saving` or `saved`. A re-emitted document's
  `BuildResult` carries its generated result tree and the paths still
  `saving`, even before the output exists. With no worker, an in-process
  command submits the jobs itself and awaits them before answering, relaying
  what they say, so no files are saving.

  A model's `done` event says its result is recorded; its `saved` result says
  each file has landed, the moment it does. A job that fails is its row's save failure and
  the ledger's reason (§9b), never the already answered command's exit. Files
  not published stay as they were, and each output still owed keeps
  `{since, error}` as `owed` on the record, written by the failing job while the
  record names its result. The next run says `<file> owed since <time>:
  <error>` first and exports that result again without running its body. A run
  that fails still owes what it handed over, and the jobs write it; an
  interrupted run with no worker abandons what it submitted for the next run.
  A current model owes what its files owe in the same way, and so does every
  child under it whose result stands (§4).

  **Explicit handoff.** `cadgen export <file>` waits for current writes and
  completes the named STEP, its recorded sidecar, or declared mesh through
  these same jobs, waiting for its own as a door waits for a write before it
  answers. A STEP-backed mesh finishes its STEP dependency
  first; a sidecar uses paired STEP publication. The output index identifies the
  qualified model and its captured tree, resolved annotations and effective mesh
  recipe, without loading source or evaluating current declarations. Changed or
  deleted source produces a notice, not a build. A matching saved receipt is
  ready even if derived objects have been collected; reproducing a missing file
  requires complete exact stored geometry and never falls back to source or
  display meshes. An existing file with no usable record is ready; a missing one
  fails. A newer result or recipe supersedes the retry, which follows the latest
  record before answering. Publication compares key, tree, annotations and all
  output recipes; completion receipts are merged into the latest matching record
  so a sibling export's receipt survives.

  **The kept rule.** A rebuild keeps its STEP -- writes nothing -- when its
  record's last build had the same tree, wrote the file with this writer
  (`step_export.STEP_WRITER_SCHEME`, on the output entry), the file on disk
  when the build started still has the bytes recorded, and the store maps those
  bytes (their document entry); `--force` always writes
  (`generation._export_plan`). The run says `kept STEP`. The rule asks only that
  the store maps the bytes, not that it maps them to this result: a compile of
  the file (a door opened it after a document-schema change, before the model's
  next run) or another model's export of equal bytes may stand for it, and refs
  by lineage would then miss and parts key otherwise. So a kept STEP whose
  document entry maps its bytes to another tree is mapped to this result: one
  index write, nothing exported. A kept STEP writes no bytes: its sidecar and
  its record's output entry are published by the build itself, by the same
  publication (`exports.publish_step`), before the command answers, so the
  file is the result's from the answer on. A rebuild that exports
  the bytes the file already has leaves the file, and its stamp, in place. A
  model whose result stands has its file exported again by its next run, with
  no body run (`generation._hit`, which says why), where another writer wrote
  it, the store lost its entry (the same bytes, kept in place, mapped again), or
  it is missing or not the file the record names. Bump `STEP_WRITER_SCHEME`
  with any change to the bytes cadgen writes for the same tree. A sidecar is a
  function of the record -- the kinematics, appearance and animation the build
  resolved -- and is written only where its bytes change (viewers version a
  sidecar by its stamp, and a new one reloads the model); a model whose result
  stands never takes the one beside its file: one on disk that is not the
  record's (deleted, foreign, another state's after a restore) is written again
  from the record, bound to the STEP's digest, even beside a kept STEP.

  Finishes that STEP does not carry persist in the sidecar's named
  `appearance.materials` library and `appearance.assignments` map, keyed by the
  result's leaf tokens -- the file's own, which a compile of it mints again --
  and resolved kinematics name the result's nodes by token the same way.
  Finishes always come from the file's sidecar, never from a tree's
  `appearance`: readers compose appearance into private descriptors, and files
  with identical STEP bytes share geometry while retaining their own finishes.
  Appearance-sensitive exports include the normalized appearance digest in their
  variant, including absence.

  A document entry may carry an exact loaded `producer` hint beside its tree,
  the producer a view derives its parts under. Rewriting the same tree keeps a
  valid hint and the mesh ledger; a new tree drops the ledger, and the hint
  unless an attested producer is given. A reader selects tree and hint from one
  atomic snapshot of the entry, geometry ignores an absent or invalid hint, and
  a prepared warm view can use a valid prior producer without importing the
  kernel. A file's bytes stay authoritative: they are reparsed without guessing
  colours the file does not contain.

- Consumers that speak the older flat shape (the viewer client, the mesh
  exports) read a **flattened** tree: `cadgen.store.trees.flatten` expands
  links recursively (tokens re-rooted: a child's nodes take the tokens the
  file's own hierarchy mints for them under the link's token, which is the
  token its node has once flattened -- a part child's one leaf by its name and
  composed placement, an assembly child's group by its name,
  `_internal/tree_tokens.py`), composes transforms, and merges components. `cadgen.store.view` lays that
  out as a temporary directory or serves it virtually; nothing of the sort is
  ever written INTO the store.

### Record

The mutable per-model entry, `index/model/<sha256(model ref)>-v<schema>` (the
record schema below, §2) where the model ref is `<resolved script path>::<function name>` — the script and the decorated
function, because a file may hold several models (each its own record, output
and job). An imported document's record is keyed on its own path (no function).
An example (`robot`: a base, two placements of `arm`, one of `peg`):

```json
{
  "kind": "record",
  "schemaVersion": 14,
  "model": "/abs/project/src/robot.py::robot",
  "script": "/abs/project/src/robot.py",
  "function": "robot",
  "entryKind": "assembly",
  "sourceKind": "python",
  "tree": "64429167…",
  "key": "9b1d3c0e…",
  "producer": {"cadgen": "0.8.0", "build123d": "0.11.1", "cadqueryOcp": "7.9.3.1.1"},
  "closure": {"hash": "e341ac84…", "files": ["robot.py", "lib/profiles.py"], "shas": {"robot.py": "ast1:…", "lib/profiles.py": "slice4:…"}, "names": {"lib/profiles.py": ["WIDTH", "bar"]}, "wholes": {"lib/profiles.py": "ast1:…"}, "own": {}, "static": false},
  "children": [
    {"model": "/abs/project/src/arm.py::arm", "key": "c161092b…"},
    {"model": "/abs/project/src/peg.py::peg", "key": "5f0e27a9…"}
  ],
  "outputs": {"/abs/project/STEP/robot.step": {"sha256": "823699b0…", "writer": "cadgen-step-writer-4"}},
  "modelSeconds": 1.84
}
```

- `key` is the result's identity (§1): computed as the record is written
  (`write_record`), from the model, the closure's hash, the constants, the
  children's keys and the producer -- two models of one file share a closure
  and are two results -- so a parent depends on a child by everything the child
  was built from. The result is stored under it too, `index/result/<key>`: the
  record as last written under that key and the provenance written beside it,
  with the tree, so a source brought back to a state already built is a hit on
  that result (§4). `store forget` drops a model's results with its record.
- `producer` is who built it: this cadgen's version and the kernel
  distributions', read from installed metadata (never the kernel). A record
  another producer built is rebuilt once (§4, clause 6).
- `children` is recorded from the CALLS the body made — every child wrapper
  entered during the body appends `(model, key)`, whether that child ended up
  linked, inlined, modified or discarded; a model called twice is listed once.
  It is never derived from links.
- `modelSeconds` is how long the build's model code ran: the body's call less
  its children's builds, the loading of their geometry, and `read_step`'s
  artifact loading (including a missing tree's compile and its wait)
  (`cadgen._internal.build_timing`). It is no input and no gate reads it; the
  next build compares its own model code against it and warns when that took
  more than twice as long and at least 30 s more. A record without it (a model
  last built with no body measured) is never a slowdown. A `--profile` build
  keeps the previous value and is compared with nothing: the profiler's
  overhead is not the model's.
- `closure.files` is the model's static reach (AST, transitive,
  first-party, absolute and relative imports alike — a `lib/` package's
  `from .chain import X` counts, and importing `lib.x` executes
  `lib/__init__.py`, so the package is in it) **stopping at model files**, plus files executed in its own
  frame, what importing its children ran in its process (below), and every
  data file the build read, whoever opened it -- Python's `open`, numpy, an
  OCCT reader in C++ (§5, every read is seen). Nothing is declared. A file
  read is not an input when the build wrote it, when it is one of the model's
  own outputs (the documents and meshes its decorators declare, and the
  sidecar beside its STEP), when it is code (Python source is the reach above; a compiled
  library is the environment's), or when it lies in the environment: the
  interpreter and its packages, cadgen, the store, and the folders the
  operating system owns (fonts, time zones). Four kinds of entry are not files: `<folder>/`, a folder
  the model's code listed (a glob of profiles), hashed by its sorted entry
  names, less the ones cadgen keeps only while it writes beside an output
  (a temp file, a STEP stage folder; `is_transient_name` in
  `cadgen._internal.atomic_replace`), so a sibling's build in that folder
  does not stale it; `!<path>`, a file that
  must stay absent — one the imports were resolved past (a package beside a
  module, an `__init__.py` a namespace package lacks, a module an earlier
  search root lacks) and would resolve to if it appeared, and every file
  that would satisfy an import that resolved in no search root: an optional
  part a model skips while it is missing (`try: from gear import gear`
  `except ImportError`), `<root>/gear.py` and `<root>/gear/__init__.py` in
  each root, a dotted name at the level its lookup stopped, a submodule a
  from-import names that the package's `__init__.py` does not bind, and an
  installed package's name, which a project file would shadow, but never a
  standard-library name (`sys.stdlib_module_names`: cadgen and the kernel
  import most of it before any model code runs, and how the interpreter was
  built decides whether one is a file at all) — hashed `absent`, so the module
  appearing is a change like any edit and `cadgen store why` says `closure
  changed: gear.py appeared`; `<import roots N>`, the digest of the first N
  search roots when an import was found past the script's own folder (a root
  added before those could shadow it); and `<environment NAME>`, a variable
  the model's own code read (`os.environ[...]`, `os.environ.get`,
  `os.getenv`, `in os.environ`), hashed by the value it read (`env:<sha256>`,
  never the value: it may be a secret) or `unset`. The reader is the frame
  that asked, past `os`'s own functions, as a listing is attributed: what
  cadgen, the standard library or an installed package reads, for itself or
  for the model, is not an input (a library caches its configuration, so
  whether it reads during a build depends on the process, not the model); a
  copy of the whole environment (`dict(os.environ)`, `os.environ.copy()`,
  its items) reads no one variable; and a variable the build set before
  reading it is its own. The gate re-hashes it from the environment of the
  process asking -- the caller's, which a daemon job runs in (§9) -- so a
  changed value is `environment variable NAME changed`, and a change to a
  variable the model never read changes nothing. The clips declared by `@step(animation=...)` are code,
  in the closure like the rest of the model's reach; the build bakes them to
  keyframes in the unified sidecar, and they never enter geometry identity. The
  boundary is decided statically by what the importer TAKES from a model
  file: only model functions (`from arm import arm`) → a result edge, file
  excluded, the child tracked by its key (also when that file declares several
  models); a module-level literal (`from plate
  import WIDTH` where `WIDTH = 40.0` — numbers, str, bool, None, tuples/
  lists/dicts of those) → a value edge, file excluded, the value tracked in
  `constants`; anything else (a helper function, a `bd.` object, an
  expression) → a source edge, file included. **Constants by value,
  functions by reach, models by key.** Hit and miss runs record identical
  closures by construction: a child's build opens captures of its own (§9a).
  `closure.static: true` marks a record whose
  inputs are not files (a document re-emitted by `cadgen step build`); the
  gate's clause 2 does not re-hash files for it.
- **A listing leaves out the model's own outputs** (`closure.own`): each one
  written in the listed folder, and each folder there one was written into,
  recorded per listing, so every re-hash -- gate clause 2, `cadgen store why`,
  the publish rule, the annotation refresh -- leaves out the same names. They
  are never its inputs, as a file it wrote itself is not: the listing is taken
  when the body returns, before a first build publishes them, and a rebuild
  lists the previous run's. So a model that lists its own folder is current
  after its first build. A sibling model's outputs stay in, since a body may
  list the folder to find them: a model that lists a folder its siblings build
  into reads stale once after their outputs first appear there.
- **Functions by reach** (`cadgen.store.reach`, the walk in
  `cadgen.store.closure`): a non-model file in the closure is hashed by the
  part of it the model can execute, not by its whole text, so editing a helper
  the model never reaches leaves it current. The record keeps the reached
  names per sliced file in `closure.names` and the file's whole-file hash, of
  the same bytes, in `closure.wholes`; a file absent from `names` (the script
  itself, a model file taken as source, a data file the build read, a file
  the fallbacks below made whole) is hashed whole as before. What a slice is, by
  construction:
  - A statement is an optional **definition** only inside a closed grammar:
    an undecorated function with no type parameters and with defaults and
    annotations consisting of closed literals; or a single-name
    assignment of closed literals. Closed literals contain only constants,
    literal containers without unpacking, and signed numeric constants. A
    repeated binding is never optional: replacing an object can run its
    finalizer. Function defaults/annotations referring to an object can also
    change its lifetime, so bare names there are not optional — unless
    annotations are never evaluated at definition time (`from __future__
    import annotations`, Python 3.14+): then annotations do not count, and a
    single-name annotated assignment of a closed literal is a definition too.
    Everything else is **preamble**, always hashed with its reads reached.
    This includes every class, decorator, call, evaluated annotation,
    augmented assignment, alias assignment, unpacking, property/subscript read,
    operator and formatted string. Neither a familiar module name nor the
    absence of a call node proves purity: Python protocols can execute user
    code implicitly. Unknown syntax defaults to preamble. This deliberately
    trades some cache hits for correct invalidation, while ordinary unused
    helper bodies and literal constants remain sliced by reach.
    An unshadowed `if __name__ == "__main__":` block never runs on import: it
    is in no slice, binds and rebinds nothing, and nothing in it makes its
    file dynamic (the script itself is always hashed whole, block included).
  - A **function edge** brings in that function's own source plus everything
    it can reach: every name its body, decorators, defaults and annotations
    load (also inside nested functions, lambdas and comprehensions, and names
    it declares `global`), each resolved to the definitions binding it in the
    same module — transitively — or, through an import binding, to a name in
    another project module, where the same rule continues. A name a
    function writes through `global` is bound by that function. A class edge
    brings in the whole class. A module-scope import executes its module's
    preamble, and every package on the way (`import a.b.c` executes `a`,
    `a.b` and `a.b.c`); a from-import of a name reaches that name whether or
    not it is used. Attribute chains on a module alias (`geo.plane`,
    `lib.geo.plane`) walk submodules and end on a name; a submodule name that
    the package's `__init__.py` also binds reaches that binding too (it wins
    at run time unless the submodule was imported first).
  - **Every file the build executed is a root.** The walk starts at the
    script and then, walked whole from the bytes that ran, at every
    first-party file the build executed that static reach never saw and no
    child owns — a plugin imported for its side effect, a module found through
    a `sys.path` insert — so what IT calls in a sliced file is in that slice.
  - **What importing a child runs is this model's input.** A child's model
    BODY never runs in this model's captures — it runs in the child's own build,
    inside this one or not at all — so it is tracked by the key (models by
    key). But importing the
    child runs its module body, the module bodies of the helpers it imports,
    and its model definitions' **headers** (decorators, defaults, annotations)
    here, and any of them can change what this body computes (a registry
    filled, a shared table patched). Each such file is in the closure by what
    its import runs: a child model file as an `islice1:` import slice (its
    preamble, each model definition counted by its header, never its body; a
    cadgen model decorator's literal arguments read as one placeholder, since
    evaluating a literal runs nothing), a helper only children import as a
    slice of what import-time code reaches, and whole when that is dynamic or
    the closure reflective. An import-time walk follows what import-time code
    reads; binding a name by `from x import y` runs none of `y`. What that
    code reaches in a file this closure shares is reached too.
  - **A child is only a call.** A model function taken from a model file is a
    result edge only while the importer calls it: an attribute of it
    (`arm.__wrapped__`, `arm.__cadgen_model__`) can reach its body, so the file
    is taken as source; and a model's wrapper carries no `__wrapped__`. A
    `@dxf` function is not a child at all: called inside another build it runs
    its body inline, so taking one is taking source.
  - **Anything dynamic falls back to whole files.** Per module, and the walk
    then descends into all of it: a star import (importer and target whole);
    `globals()`, `locals()` or a bare `vars()` (the module, and every module
    it imports: its module objects expose every name of theirs); a
    module-level `__getattr__`/`__dir__`; a module-scope read of a name nothing
    binds and no builtin answers; a name reached in a module that has no
    binding for it. A module alias used bare — `getattr(geo, name)`,
    `vars(geo)`, `geo.__dict__`, `geo` passed along — makes the target whole,
    and every module bound in it; a package alias used bare makes every file
    of that package whole; an attribute write or delete on a module alias
    (`geo.X = 1`, a monkeypatch) makes the target and the writer whole. Per
    closure: when any walked file can reach an arbitrary module's namespace
    by string or introspection — `exec`, `eval`, `compile`, `__import__`,
    `__builtins__`; any binding from `importlib`, `builtins`, `runpy`,
    `pkgutil`, `inspect`, `pydoc`, `gc`, `ctypes`, `pickle` and its kin, or
    another module that resolves names from strings; `sys.modules` or a frame
    through any alias of `sys` or `from sys import`; `__globals__`,
    `f_globals`/`f_locals`/`f_back` or any other frame attribute — every
    file of the closure is hashed whole and `names` is empty. The reach
    through model files is unchanged: a result or value edge stops there; a
    source edge into a model file takes it whole.
  - **Module-level side effects keep file-level tracking**: they are
    preamble, always hashed, their reads always reached — a constant table
    built by a call, a registry filled at import, a conditional definition.
  - **Static and deterministic.** The walk reads the bytes the exec hook
    captured when each module ran (§5, hash at execution) and resolves
    imports against the script's `sys.path` (its folder, then `PYTHONPATH`);
    its only run-time input is which first-party files executed, and those
    are in the closure either way. The gate never re-derives cross-module
    reach: a sliced file whose whole-file hash is still its `closure.wholes`
    keeps its recorded slice without being analysed, and one that moved is
    re-sliced on disk by its recorded names
    (`cadgen.store.closure.sliced_source_hash`: the names closed within the
    module, each marked bound or unbound, then the preamble and the reached
    definitions in source order, a digest of `ast.dump` per statement, so
    comments and formatting do not count). Any edit that would change what
    the walk reaches — a reached body calling something new, a new binding
    shadowing a name, an import added or changed — also changes a hashed
    statement or marker. A sliced file that turns dynamic hashes whole
    (`ast1:` against a recorded `slice4:`) and reads stale. `cadgen store
    why` prints a sliced file as `lib/profiles.py[WIDTH, bar, …]`.
  - **Lexical scopes decide what must be bound.** The analysis resolves every
    load with Python's scoping rules, in one pass per module: parameters and
    assignments belong to their function, a comprehension's targets to the
    comprehension, defaults, decorators, bases and a comprehension's first
    iterable to the enclosing scope, and a class body is invisible to every
    scope nested in it (a class-body comprehension reads the module's name).
    That resolution decides which names must be bound at module scope (the
    unresolved-name fallback). As edges, every name a statement loads is
    followed whatever scope binds it, so reach never rests on scoping alone;
    the only cost is reaching a definition whose name a local shares. Import
    aliases retain all candidate bindings across those scopes, so two nested
    imports named `dims` cannot hide one another. A submodule name taken
    through its package (`from lib import geo`) is part of the package
    `__init__.py`'s slice even while the `__init__.py` does not bind it,
    marked unbound, because a binding added there later wins over the
    submodule. Which files declare models is read from their bytes, never
    cached by path, so a warm process sees a decorator added or removed.
    A sliced file's hash starts `slice4:`, a whole file's `ast1:`.
- `constants` is `{"<model file, relative to the script>": {"<NAME>":
  "<sha256 of the literal's canonical repr>"}}` — every literal the model
  took from a model file by value. Empty for most models. The gate's clause
  2 re-hashes each value by importing the model file under a **kernel-guarded
  loader** (`cadgen.store.closure.module_constant_hashes`: the script's folder
  and the caller's `PYTHONPATH` on `sys.path`, a private module name, the bytes on disk compiled
  fresh) — a model file whose import pulls the kernel, or fails, reads as
  stale rather than as unchanged. This is why a model file's top level must
  stay kernel-free (`from cadgen import build123d as bd`, no `bd.` in module
  constants): the gate runs it.
- Python records may also carry `unannotatedTree` and `geometryClosure`.
  Together they permit one narrow
  metadata refresh: same-module literal `kinematics=` or `materials=` values
  (including literal constants used exclusively there) can be reapplied to a
  complete cached baseline without executing the model or rewriting STEP. The
  recorded geometry closure is derived from the exact source buffer that
  executed. Computed and imported annotations stay in that geometry
  fingerprint; an unchanged one can coexist with a literal edit by reusing its
  recorded value. Changing its expression or dependency, reflection, constants
  used anywhere else, a child under another key, incomplete trees, and changed output
  bytes fall back to the ordinary build. `animation=` clips are always computed:
  a refresh writes the record's baked keyframes back unchanged, and an edited
  clip is an ordinary build.
- A leaf has `children: []`. Roots and leaves have the same record. A record
  for an imported document (`sourceKind: "step"`) has the document's bytes as
  its closure. Cold compilation does not read earlier model/output records
  or preserve their declared exports when writing this bookkeeping.
- A **drawing** (`@dxf`) is a model like any other: the same wrapper, record,
  gate and job. `entryKind: "drawing"`, `tree: null` (gate clause 4 is
  vacuous), its `.dxf` as the one output, and `children` recorded by key from
  the models its body called — a flat pattern of `bracket()` goes stale when
  bracket is rebuilt. The viewer and `dxf snapshot` read the `.dxf`
  file directly; there is no drawing-specific freshness anywhere.
- A model's **outputs are whatever its decorators declare**. STEP is one
  output kind, not the primary: a model declared by `@stl`/`@glb`/`@threemf`
  alone has the same tree and record as any model, every stale declared mesh
  is (re)generated from that tree, and no `.step` (and no sidecar) is written
  — `outputs` simply lists no document.
- `outputs` carries each declared mesh's complete recipe before writing:
  `declared`, effective tessellation `chord`/`angle` as normalized strings, and
  its serialization `name`. A changed recipe clears the previous completion
  receipt. A STEP's entry says which writer writes it (`writer`);
  it, its sidecar and its declared meshes are pending -- an empty sha256, which clause 5 reads as
  never written -- from the record written at the result until the export lands
  (§3, *Exports*), and the STEP's sha256 lives there and nowhere else in the
  record. A failed export keeps an `owed` object on each unfinished output:
  `{"since": <Unix seconds>, "error": "<type>: <message>"}`. This explains the
  failed write on the next run; it does not stale or rebuild the stored geometry.

### Provenance

The build's account of its own code: `index/provenance/<sha256(model
ref)>-v<schema>` (`store/provenance.py`, schema 5), one entry per model beside
its record, on the code side (§2), written by the build with the record for
the result tree the record names and kept with the result under the record's
key (`store/results.py`), and never read by a reader, a view or the viewer. It
is the run's trace as the kernel boundary saw it
(`_internal/kernel_trace.py`, the trace the kernel memo keeps, README law 18),
kept rather than computed: no pass of the build measures anything for it. It
holds the **features** the body ran, in call order (§2) -- each one's
operation, the source frame in the model's own code that called it (file, line
and the enclosing function) and the features that made the shapes it was
given -- the **components** the build made, each with the feature every name
of its lineage stands for (this run's, for a component the worker remembered
publishing: the feature with the history the name stood for, §3 *Tree*), and the
**occurrences** of those components in the
result, each with the feature whose call made the shape it places. A component
names its elements by those features through its lineage object (§2, §3),
which is how an element finds the feature that made it and the one that last
modified it: its derivation, spelled out through the object's table, is every
step that made it (§2) -- made by the feature of its last step that made it
anew (generated it, or named it from a parent or from its lower elements),
else by the feature that made the element it came from; last modified by the
feature of the last `M` step after that -- and an element passed on from a
shape no feature made (a child's geometry, an imported file) has no derivation,
its origin unknown. A part one construction
made twice is one part (§3), named by its first making; each of its
occurrences keeps the call that made it, so each answers with its own line.
A file and a line are here and nowhere else, so an edit that moves code
renames nothing. The entry carries the hash of the tree it describes (`tree`);
an entry for another tree than the record's is behind it, and `store where`
says so rather than guessing.

`cadgen store where <document> <ref>` is the one reader: path →
`index/output` → the model → its record and its provenance entry, the file's
hash checked against the record's output entry (the bytes the model's build
wrote from its result), the
ref resolved in the tree the record names (by the one resolver every ref goes
through, `cadgen.refs`; a ref in geometric-token form is matched to its element
by geometry first, §2) to its occurrence and its component, whose lineage
spells the element's derivation and so names the features that made and last
modified it -- looked up in the entry that
describes that occurrence: the model's, or, for an occurrence a link placed, the
child's, by the occurrence's token in the child's own tree (through the
record's children, while the child's record still holds the key the parent
composed and its entry describes its tree), among the calls that made that occurrence. It is a code-side walk, like
`store why`'s, that no door, view or render path takes (§2, property 2). It
answers with the feature that made what the ref names, as `file:line in
function (operation)` with the features it was given, and for a face, an edge or
a vertex the one that last modified it. Without an entry, or with one for other
bytes, or for an element no feature made, it says it does not know, and the ref
still resolves to its geometry: provenance is never how a ref is found. The
entry's key is versioned like the record's, and a record a cadgen does not own
(§2) has an entry it does not own: both are obsolete together; `store forget`
drops the entry with the record, and nothing evicts it (§8).

### Schema history

Each kind carries its format version -- in its key (records, document and
provenance entries) or in its body (trees, results) -- and a format change
bumps it: nothing here reads another version's, and the next build or compile
writes this version's, reusing every object that exists (§8 says when the old
one goes). What each version changed:

- **Record**, schema 14 (`RECORD_SCHEMA_VERSION`, `store/records.py`): complete
  pending mesh recipes, so exports can be retried without source. 13 added the
  result's `key` and `producer`, and each child by its key. 12 named each child
  by its tree, and was the first written at the result, its STEP output pending
  until the export lands; 11, 10 and 9 named trees of schemas 6, 5 and 4; 8
  keyed appearance assignments by position. A record of an earlier schema is a
  miss: the model's next run rebuilds it once.
- **Document entry**, schema 13 (`DOCUMENT_SCHEMA_VERSION`): an exported file's
  bytes map to the result they were written from, at once and finally. 12 held
  such an entry pending until a parse of the file confirmed it, and mapped a
  file it found otherwise to its own content with what differed; 11 to 7 named
  trees of schemas 6 down to 2; 6 read names as §5 *Names a reader reads* says.
  An entry of an earlier schema is never read, and its bytes compile again.
- **Provenance entry**, schema 5 (`PROVENANCE_SCHEMA_VERSION`): each occurrence
  of a component the build made, with the feature whose call made its shape. 4
  dropped the document digest; 3 counted features by place.
- **Tree**, schema 6 (`TREE_SCHEMA`, `store/trees.py`): a leaf's token rounds
  its translation as the STEP file states it. 5 named a constructed component
  by its `construction` key and `lineage` object, 4 every occurrence, link and
  node by its `o:` token, and 3 pinned an eager-only component's topology
  facts; 6 keeps all three. No older tree is decoded.
- **Result**, schema 1 (`RESULT_SCHEMA_VERSION`, `store/results.py`): the
  first. Its key hashes its producer, so another cadgen's results are obsolete
  here (§8). Its embedded record must also use the current record schema.

## 4. The gate

`cadgen.store.gate.stale(model)` — one function for every model. Stale if any
of:

1. **No record.** Protects against reading a result that was never built or
   whose record was collected.
2. **`sha256(closure.files as they are now) != closure.hash`, or a constant
   in `constants` no longer hashes to its recorded value.** Protects against
   a source edit; the hash is a semantic hash of each file's Python
   (comments and formatting do not count), computed at execution time (§5) —
   over the whole file for the script and every file `closure.names` does not
   list, over the reached slice for a file it does (§3, functions by reach):
   editing a helper the model never reaches leaves it current; editing one it
   reaches, directly or through other helpers, or a module-level name a
   reached helper reads, makes it stale. A sliced file still at its recorded
   whole-file hash (`closure.wholes`) keeps its recorded slice unanalysed, so
   an unchanged project costs the gate the whole-file hashes and nothing more.
   A literal imported from a model file is compared as a value: a comment,
   a body edit or a new helper in that file leaves the importer current; a
   changed value (or the name no longer bound to a literal) makes it stale.
   A listed folder is re-hashed less the model's own outputs its
   `closure.own` records (§3); a file recorded absent by whether it still
   is; an environment variable the model read by its value in the asking
   process's environment, the caller's (§3, §9).
3. **A recorded child does not stand under the key recorded**: its result
   does not stand (`child stale`), or stands under another key (`child
   rebuilt`). A child stands under its record's key while its stored result
   stands (below), or -- its source moved on and was brought back -- under the
   key its source has now, where a stored result answers for it
   (`Verdict.candidate`), which the parent's run restores (below). Protects
   against a child built from other inputs than the parent composed: a child
   source edit rebuilds its parent, whatever geometry it yields, and the parent
   relinks the child's stored result. Recursion is memoized per evaluation:
   each model is asked once.
4. **The tree object, or any component it (transitively) references, is
   missing.** Protects against a collected or half-copied store.
5. **A declared output does not match `outputs`.** Protects against a deleted,
   hand-edited or foreign `.step`/sidecar/mesh file beside the model.
6. **The producer that built the record is not this one** (`producer
   changed: cadgen 0.7.21 -> 0.8.0`). Protects against a result another
   cadgen or kernel made: an upgrade rebuilds every model once.

A model's stored result is in the store when clauses 1–4 and 6 hold
(`Verdict.stored`, the record's key). It **stands** (`Verdict.key`) when
clause 5 then finds at most exports to owe -- its STEP, its sidecar, a declared
mesh, each written from the stored result and its record (§3, *Exports*): a
decorated call takes the result as it is, writing what the files owe without
running the body (a deleted STEP or sidecar is written again, not rebuilt),
and a parent depends on the child by it (clause 3). Only a drawing (`.dxf`) is
a run's to write. Before a body runs, the gate also names
the key the source has NOW (`Verdict.candidate`: this model, the record's
closure as it hashes now, its constants as they read now, its children's keys
as they stand, this producer); a result stored under that key (§3,
`index/result`) is restored -- record and provenance -- and taken as current
(`generation._restore_result`), so a source brought back to a state already
built never runs again, at any level of the tree: a parent that stands on a
child's candidate restores the child too. A record whose result is in the store
is that candidate itself, and is never restored over what it owes a run for.

**What a current model's children owe.** A model whose result stands runs no
body, so it calls no child; its run heals the tree instead (`generation._hit`):
every child whose result stands -- restored first where it stands on its
candidate -- is taken as the model is, recursively, each by its own export plan
(§3, *Exports*), children queued before their parent, as in a build, so one run
of the root leaves no file of the tree owed. Those children ask for no display
data of their own: the root's request covers its whole tree, links expanded. A
child that does not stand makes its parent stale, and the parent's run calls
it.

Clauses 4 and 5 verify each object and each output once per process: one
build evaluates the gate many times over the same closures, and a large
assembly's STEP is hundreds of megabytes. A later evaluation stats each file
and reuses the verdict while the file keeps the identity (device, inode, size,
mtime, ctime) observed around its verified read, under §10's settled-stamp
rule. Deleting, replacing, truncating or rewriting a file verifies that file
again, and for an object every tree above it in the closure: a component still
at its verified identity is taken on that identity, and a linked tree whose
whole closure holds is taken as verified, so a child another process rebuilt
costs the parent's next evaluation that child's closure and every tree above
it, and clause 4 reads nothing clause 3 just verified (§10). A build's own
checks of its STEP and sidecar -- before the body runs, before it publishes,
after its rename -- read through the same memo, and a file the build renames
into place is remembered by the digest its writer took (a rename keeps the
written file's device, inode, size and mtime, so a path that still shows that
identity, settled before the rename, holds those bytes). Its checks of its
result -- after it publishes the tree, when it announces it, when it claims its
record's closure, in the already-stale notice -- take the verification the job
already holds; the claims keep it (§8). A publish reads nothing of a closure
its job verified.

Each model's build asks the gate once before its body
(`generation.build_model`). That verdict answers the no-op check, the restore,
the check for a peer that published meanwhile, the annotation refresh and the
reuse check; it is taken again only after the build waited for a slot, which
can change it. The already-stale notice after publishing asks anew (§9a).

Mesh tolerances are export recipes, not separate result-key inputs. A declared
mesh's entry records the effective pair (an omitted tolerance is the display
tolerance's). For a current stored result, a changed recipe re-cuts the mesh
without running the body. Editing the declaration's source still participates
in the ordinary closure gate and may run the body. Argv flags are not inputs.
Imported STEPs are inputs (a
file the body reads is in the closure), not models. `--force` rebuilds the named
model only; its children go through the gate as usual. `cadgen store forget
<model.py>` drops the record instead, so the next run — not this one — rebuilds
it (§10, Resets).

## 5. Invariants

Each with the failure it prevents.

- **Hash at execution.** A closure file is hashed when the interpreter
  executes it (an audit hook on `exec`), not after the build. The execution
  window includes module initialization. The model loader records the semantic
  hash of the exact buffer it compiled before executing it, so a replacement
  during module loading cannot substitute a later file's identity. Prevents: a file
  edited during a long build being recorded with the bytes that did NOT run,
  which would make a stale result read as current forever. A first-party
  module is compiled from the bytes on disk, never from a `__pycache__` `.pyc`
  (CPython accepts one by whole-second mtime and size, so two same-length edits
  inside a second would run stale bytecode), and its loader records exactly
  those bytes; a build writes no bytecode for anything it imports.
  A data file is hashed after the body returns, and only while it still has
  the size and mtime it was opened with (the trace takes both at the open):
  one that changed or vanished since is recorded `changed while building`,
  which matches no file, so the next gate rebuilds.
- **Every read is seen.** A build runs inside a capture
  (`cadgen._internal.filetrace`). The tracer, one small native library per
  platform in `_runtime/native`, rewires every library loaded in the build's
  process -- and each one loaded later -- so its calls to the C library's
  file opens (kernel32's on Windows) pass through a wrapper that logs the
  file, its size and its mtime while the capture is open. Every reader ends
  there: Python's `open`, numpy, build123d's importers, OCCT, FreeType.
  Folders come from Python's `os.listdir` / `os.scandir` audit events, by
  frame: the import system's listings and cadgen's own are not the model's,
  and a listing leaves out the model's own outputs (§3).
  The environment variables the model's own code reads are seen the same
  way, by frame (§3). The gate's own reading inside a build (a child's files
  and outputs, hashed to decide whether it is current, a model file imported
  for its constants) is paused on its thread: a child is an input by its
  key. Prevents: a model whose data changed reading as current because
  nobody declared the file. Not seen: a data file the model looked for and
  did not find (one that appears later is no change -- a MODULE that appears
  later is, §3); a `.py` file read as text and `exec`'d rather than imported
  (source counts by reach, and reach follows imports); what a separate
  program the model runs reads -- so a model whose code starts one
  (`subprocess`, `os.system`, the `spawn` and `exec` families, by their audit
  events and by frame) is warned about each time it builds, root or child:
  `[cadgen] warning: <model> started <program>; files it reads are not
  inputs, so editing them will not rebuild it`, carried on its `done` event,
  which the root's build tree prints once (§9a); a copy of
  the whole environment, or a variable a library reads for the model; calls
  made inside the operating system's own libraries (the macOS shared cache);
  a file a third-party library caches for the life of a warm worker, after
  the first build that reads it.
- **Publish order.** Objects first (components, then the complete tree), then
  the output notes, the record (its STEP output pending, the result stored
  under its key) and the provenance entry; the export job is started then and
  writes the file into a private staging folder beside the output, outside the
  store, then publishes it (§3, *Exports*): the file moved into place
  atomically, its digest-bound sidecar, the document entry, the record's
  output entry. Prevents: a record pointing at a tree
  that does not exist yet, a `.step` whose sha the record has not seen, and a
  document entry naming a tree its bytes were not written from.
- **Canonical STEP bytes.** Before a written STEP is published, the writer
  canonicalizes what OCCT emitted: NAUO instance ids, presentation-style
  order, and the sign of zero — `-0.` is rewritten `0.`, because which IEEE
  zero a coordinate lands on follows the operation path that produced it, not
  the geometry.
  Prevents: one model writing two documents, so the packages and index
  entries keyed by the other spelling's bytes are orphaned.
- **Names a reader reads.** OCCT's STEP reader ends a string at an escaped quote
  (`''`) followed by spaces and `,` or `)`, so a part named `post (6')` or
  `x('',y)` would lose its product or come back renamed in every OCCT-based
  reader: the writer spells every quote in such a literal as Part 21's `\X\27`
  instead, which readers decode to the same name
  (`step_export.respell_misread_quotes`). OCCT writes a non-ASCII name as its
  UTF-8 read as Latin-1 and encoded again, trimming any byte its C library
  takes for a space from either end (`Bügel_ä` would come back `BÃ¼gel_Ã¤`), so
  a non-ASCII name reaches OCCT in ASCII, each run of non-ASCII characters as a
  Part 21 `\X2\` (basic plane) or `\X4\` directive (`step_export.ascii_name`);
  the name pass restores the single backslashes OCCT doubles, and every reader
  decodes the directives to the same name. A written document is ASCII. Every
  other literal keeps OCCT's spelling, and the writer reads a file again for
  that pass only when such a name is in it. Where a STEP gave a product or a
  usage no name, OCCT writes its label's address instead (`0:1:1:2`,
  `=>[0:1:1:2]`; a single-part document's root is one): that, a blank, the
  translator's default, a shape kind and digits alone read as no name
  (`step_scene_loader._normalize_label_name`), and a result names its nodes as
  that rule reads their labels (`store.build._own_name`), so a compile of a
  file mints the tokens of the result it was written from. STEP places no root
  product: a document of one part placed away from the identity is written
  inside an unnamed product of one, and a reader takes such a file as that one
  part, placed (`step_scene_loader.document_roots`); one part is its document,
  its occurrence named as the document's root. An occurrence with no name of
  its own shows its product's, unless that is another occurrence's own label
  -- a writer names a product its occurrences share after one of them, cadgen's
  after the last (`step_scene_loader._withhold_borrowed_product_names`) -- and
  otherwise a compile names it by its id. Readers show the tree's names as
  given.
- **Publish rule.** `cadgen.store.publish.decide`: a build rejects replacing a
  current record with a stale one — if the record on disk already reflects the
  closure as it is NOW and the build that finished ran against older sources,
  the result is discarded and an explicit save fails. The expected document
  and annotation digests also detect a competing edit during the build.
  These checks narrow conflicting publication; they are not atomic exclusion
  against another process's rename (§7).
- **Children from calls, never from links.** Prevents: a modified or discarded
  child dropping out of the dependency edge, so an edit to it would not reach
  the parent.
- **Closure boundary rule.** A model file reached only through its model
  function is a result edge (its key); a module-level literal taken from it is a
  value edge (`constants`); anything else taken from it is a source edge
  (file in the closure). A non-model file is in the closure by the names the
  model reaches in it from the script and from every file the build
  executed (§3, functions by reach), whole when anything dynamic is in the
  way; a file that ran here because a child was imported, by what its import
  runs; a file the imports rely on not existing, as `!<path>`. Constants by
  value, functions by reach, models by key. Prevents both false-current (a
  constant imported from a model file changing unnoticed; a helper edit hidden
  behind a `getattr` on its module; a child's module body patching what this
  body reads; a new `__init__.py` or module changing what an import finds) and
  false-stale (a child's internal edit, a comment beside a shared constant, or
  a helper no reached code calls — rebuilding every parent).
  One process-wide memo of immutable module-syntax recipes keyed by exact
  source bytes, bounded by 32 MiB of accounted inputs/recipes and 2048
  entries, serves the exec hook, every closure walk and every gate: each file
  revision is analysed once per process. Every lookup still reads the file and
  resolves current import availability, model classification and constant
  values; the recipes contain no resolved dependency graph or freshness
  verdict.
- **The two sides (§2, the law).** No object references source; no reader
  consults a record; records are deletable. Prevents: a moved or copied
  document rendering differently from its twin, a render path going stale or
  refusing because of a record's state, and a store-side cleanup destroying
  anything a user can see.
- **Keys and the run memo.** A parent composes the result a child's call
  decoded or built, and records the child by that result's key; a model is
  decoded or built once per run, however often a run calls it (§9a).
  Prevents: a parent's result mixing two versions of one child.
- **Objects are immutable.** An object is written once under its hash and
  never edited. Prevents: a component changing under every tree that shares
  it. Recovery of a document's objects may replace missing bytes or bytes that no
  longer match their address, using newly derived bytes verified against that
  address. This is explicit atomic repair, never deletion after a failed read.
  A writer that finds valid bytes leaves them untouched; racing repair writers
  publish the same content, so no reader sees an intermediate missing object.
- **Portability: a moved project is a set of new models over the same
  objects.** Nothing path-dependent enters an object: closure files are
  recorded relative to the script, trees hold geometry, names and placements
  only, and component ids are content hashes — so a moved or copied project
  hashes to the same closures and the same trees. Records ARE keyed by the
  resolved script path (and function), so after a move every model reads as unbuilt (clause
  1); its first build runs the body once, finds every component and tree
  already present (nothing is re-extracted or rewritten; the tree hash comes
  out identical), writes a new record and re-notes its outputs in
  `index/output`. The moved documents themselves never stopped rendering:
  `index/document` is keyed by their bytes, so every door and the viewer
  find the same tree at the new path before any rebuild; only `store why`
  reads the moved model as unbuilt until then. The records at the old path
  stay, with what their trees reach (a record is never evicted, §8), until
  `cadgen store forget <old script>` drops them. Prevents:
  two projects at different paths sharing one record and one overwriting the
  other's outputs list; and a path or timestamp changing a hash.
- **Outputs are not store contents.** The `.step`, sidecar and meshes live in
  the project; the record validates them by sha (clause 5). Prevents: a
  store wipe destroying a user's documents, and a document pretending to be
  current after a hand edit.
- **A hit is a read.** No reader writes the store: a part probe, a bounds
  hit, a document lookup, the gate, a decoded result --
  none refreshes a stamp or an mtime, so "recently used" for eviction means
  recently written (§8). Prevents: a store this user cannot write failing
  every build that only hits it.
- **A write claims what it reuses.** Bytes a write finds already present are
  claimed (their mtime becomes now, less two ticks of the clock that stamps
  them, §8) instead of skipped, and a publish claims its record's whole
  closure before it writes the record; the sweeper deletes only by rename,
  then recheck (§8). Prevents: a sweep that began before a publish deleting an
  object the new record reuses, out of a grace window that never saw the
  reuse.
- **No locks are needed for correctness.** Objects are idempotent, entries
  are temp+rename, the publish rule decides concurrent same-model outcomes,
  keys isolate parents. There is no lock layer (§7); two builders of one
  model may do the same kernel work twice, and the publish rule keeps one.

## 6. Link or component

Decided mechanically from the returned geometry and occurrence metadata.

- `cadgen.store.materialize.materialize(tree)` rebuilds a child's geometry as
  a build123d `Compound` and TAGS it with the tree hash and a handle to the
  shape it was built from, together with a private immutable baseline of its
  geometry and descendant metadata. The tag is internal; model authors need
  no additional imports or ownership helpers.
- Its process cache retains at most 64 MiB of immutable canonical BREP bytes.
  Each independent materialization reconstructs fresh kernel shapes (inside a
  model run, a tree's n-th decode does so once per process, and a later run's
  n-th decode is handed the same shapes, restored: §9a), and repeated
  occurrences of one component within that materialization share their
  prototype. Different face-color components sharing BREP bytes receive
  private topology, preventing XCAF from overwriting another variant's styles.
  Every occurrence owns its face-color and PBR maps. New byte-cache entries
  require a matching content digest; a hit still requires the object to exist
  on disk. Complete tree capture verifies every required object on each new
  materialization. Intrinsic recipes live in the immutable geometry tree, and
  each exposure receives a private dictionary. Clearing the byte memo leaves active consumers' shapes
  and appearance maps valid.
- When the parent's result is written, every tagged child whose native
  partner, geometry and descendant metadata still match its original baseline
  becomes a **link**. Everything else — geometry the parent made, a sub-shape
  it extracted, a child it modified (`housing() - holes`), a mirrored child —
  becomes the parent's own **components**. Modifying a child is legitimate and
  fully tracked (the child is still in `children` because it was called); it
  simply makes the parent own that geometry instead of linking.
  Native identity alone is insufficient: OCCT can mutate an existing TShape,
  and a nested label, color or placement can change independently. Verification
  reads private topology without altering caller geometry or meshing flags.
  Inside a model run a decoded child's leaves are the kernel memo's (§9a), and
  a check takes the digest an earlier check took of a leaf the memo holds while
  everything the memo knows of it is as it was then -- the same ledger rows on
  each of its sub-shapes, no change since that the memo could not replay
  (`materialize.HeldDigests`) -- so an unchanged child's geometry is digested
  once per worker, not at every publication that links it. A change the memo
  does not see (a raw OCCT edit of a leaf a check has digested) is seen by
  neither, as for a remembered component (§3, *Tree*); outside a run, and for
  geometry the memo does not hold, every check reads every leaf.
  Copying a modified child cannot establish a clean baseline for the old tree.
  Root placement, label and color remain link overrides. A part's root color
  replaces its previous color; an assembly's root color inherits into otherwise
  uncolored descendants, preserving explicit descendant colors.
  Native additions/removals are reconciled with surviving wrapper metadata.
  Conflicting native/wrapper hierarchy edits or ambiguous removal of identical
  occurrences fail explicitly instead of discarding geometry or guessing labels.
- A directly returned materialized root carries its component addresses through
  clean placement after that same full integrity check. Packaging reuses the
  pinned encoded BREP and intrinsic recipe instead of deriving their identity a second time.
  Forced extraction uses the pinned canonical bytes; deleted pinned assets
  cause an ordinary identity derivation from the owned geometry. No pointer-only
  shortcut bypasses the native mutation check.
- Placement that keeps the link: `child.moved(loc)` and `Location * child`
  (the same shape, re-placed). build123d's `child.located(loc)` deep-copies
  the geometry (`BRepBuilderAPI_Copy`), which serializes to different bytes —
  a new component id, so a component rather than a link: place a child with
  `moved()`. A new byte identity does not change a proven copy's construction
  identity or its element names (§2).
- **The materialize contract.** A parent may rely on: the child's exact
  geometry, its labels, colors, owned intrinsic PBR values and placements, as a compound whose children
  mirror the child's grouping. It receives nothing else — a child's sidecar
  content (kinematics, animation, export declarations) never rides up.
- A `link` in a tree is resolved by hash, so two placements of one child are
  two links to one object, and a child shared by many parents is stored once.
  Materialization requires the complete transitive object graph. A missing object
  is an error, never an empty subtree or a request for the child's newer record.
- Tight occurrence bounds live in `index/bounds`, keyed by each leaf's BinTools
  digest and its transform with the translation removed. Translation shifts
  those six bounds directly, so translated instances share the expensive
  surface calculation. Rotation still requires its own tight box;
  control-polygon bounds are not substituted. Measured, memory-hit and disk-hit
  boxes come from the same origin-normalized function, and cached numeric
  arrays are never mutated.
- A compile already owns each verified component's encoded BREP and each
  parsed occurrence's exact native placement. Its tight bounds key uses that
  BREP identity, codec, the leaf's ordinal and the leaf's rotation in
  `index/bounds`, then shifts the six native bounds by the leaf's translation.
  A result's leaves are measured the same way, through the one function
  (`component_package.placed_prototype_bounds`), each component placed by the
  transform the tree records: the prototype the build holds for its own
  components; a linked child's component from the boxes the store remembers
  for it at the kernel's own placement of each leaf (`bounds.composed_bounds`),
  else decoded from the store -- so no child is assembled for its parent's
  bounds, and the box is the same function of the tree whichever way it is
  found. The animation bake's leaf boxes find for a result what its
  publication measured. A miss measures every native leaf with the same
  optimal extrema operation; it never transforms a component AABB. A
  component that cannot be measured so (an eager-only child's) falls back to
  the whole composed native shape, after the same component validation and
  closure checks.

A tree's bounds (`_bbox_from_shape`) walk the native leaves of one composed
document with no Python callback between leaves, so within that call a
prototype's content digest is computed once per TShape encountered and
discarded when the call returns — never retained, never a substitute for the
next call's read. Vertex hashes (`determinism.py`) read their current native
point, including native point or location edits that leave Python coordinate
attributes unchanged.

## 7. Concurrency

No persistent build locks. CPU admission, and the coalescing of compiles and
artifact jobs, may wait; memory admission waits on builds in flight and fails
only when nothing running could make room (§9). Explicit builds
are not cancelled merely because a newer editing request exists.

- **Same model twice.** Both builds run. Each publishes objects (idempotent)
  and then consults the publish rule: the one whose closure matches the
  sources as they are now wins the record; the other's result is left as
  unreferenced objects for GC. A rejected explicit save reports failure. The
  files are written for the result the record names: an export whose result a
  newer record replaced writes nothing (§3, *Exports*).
- **Edit a child while its parent builds.** The parent composed the child's
  result when it called it, decoded or built in the parent's own process
  (§9a), and records the child by that result's key; a
  build of the child from the edited source, by another process meanwhile,
  publishes a record under another key. The parent is therefore already stale
  when it finishes — the next gate says so (clause 3, `child rebuilt`) and the
  next run rebuilds it. `cadgen store why` shows the two keys.
- **Edit a parent while a child builds.** Unrelated: the child's record and
  tree are its own. The parent's next build calls the child, finds it
  current, and records its key.
- **No dependency waits.** No build waits on another: a parent's children are
  decoded or built in its own process, one at a time, in its slot (§9a); two
  runs that need one stale child each build it (*Same model twice*). A build
  waits on a pool job only where its body needs one -- a compile of a file it
  reads, a part's selector table for a mate's axis, a file a door it opens is
  still owed -- with its slot yielded (§9), never on another build; what its
  results owe after it answers (display data, exports) is the supervisor's.
- **A sweep beside a build.** A pass never coordinates with builds: a write
  claims what it reuses, a publish claims its record's closure, and the
  sweeper deletes by rename, then recheck (§5, §8), so no record is written
  naming an object a concurrent pass took. The daemon runs its own passes
  only while it has no request in flight.
- **No locks.** There is no lock layer: every store write is atomic
  (temp + rename) and idempotent, the document is written to a temp file and
  moved into place, the record cross-validates the outputs by sha (gate
  clause 5), and the publish rule decides same-model outcomes. Progress is
  not on disk at all: the daemon keeps a ledger of every job it runs (state,
  phase n/total, the job's declared output paths — for a script that no longer
  imports, what it declared the last time this daemon could read it) and
  serves it with `daemon status`; the CAD Viewer matches jobs to the
  documents it shows by output path — a CLI build, a parent's child build and
  its own compile read alike — and nothing reads any of it to decide freshness.

Before a generated body runs, the build captures its target STEP and sidecar
digests (absence counts too). It checks source freshness (the publish rule)
and publishes its result's complete immutable objects and its record; its
export job writes the STEP into a private staging folder (§3, *Exports*), and
checks that the record still names the result and the expected output pair
before it moves the file into place. A concurrently written byte-identical
pair is idempotent, and so is the pair the same model's previous export saved
while this build's body ran -- the pair the record this build replaced names,
read again at publication from the result stored under that record's key,
which that export's publication rewrote: two edits in a row overlap routinely,
the first one's file landing during the second one's body, and it is the
model's own. Any other pair (another model's, a person's edit) makes the save
fail. After the final renames it checks the written digests before recording
the output. A STEP re-emission has an explicit immutable input/annotation
digest, not a Python closure; its output-pair check still applies.

Each rename is atomic; the group is not a transaction or compare-and-swap.
There is a check-to-rename race with independent CLI or external writers, and
an external writer can replace a successfully saved file later. A failure
before publication preserves the previous pair. A crash after the STEP rename
can leave a missing or mismatched annotation: the sidecar binds annotations to the
STEP's SHA-256, so readers reject that annotation instead of applying old
mates or finishes to new geometry. Material-only saves can retain the same
STEP digest, so refresh and output-pair conflict checks also observe sidecar
content. A stale/missing record does not block saved-byte reads;
missing derived objects are compiled from the bytes that actually exist.
Explicit regeneration repairs the annotation pair. No reader consults locks
or source to recover an artifact.

## 8. GC, eviction and the cap

One sweeper, `cadgen.store.gc`: by hand as `cadgen store gc [--dry-run]
[--grace-hours H] [--max-size [SIZE]]`, and by the daemon when idle (below). A
pass scans the store once (names, sizes, mtimes), marks once, and removes three
kinds of thing:

1. **Retired kinds and obsolete entries.** The entries of the folders in
   `RETIRED_KINDS` (`index/op`, `index/surface`, `index/selector`, `index/mesh`,
   §2) and the obsolete part entries go once a week old (§2), with every object
   only those entries named; a younger one stays, objects and all, like a
   current one, and a retired kind's younger entry is evicted under a cap like
   a derived one. An entry naming
   an object the grace window still keeps waits for the next pass, so that
   object goes with its entry rather than with the next full sweep, and an
   entry written again since the scan stays. The records, document entries
   and provenance entries at keys this cadgen does not own, and the results
   another cadgen stored by key (§3), go on the same rule -- a week after their
   last write, and not if written again since the scan; their trees' objects
   are left to the sweep (3), which takes what nothing else reaches.
2. **Evicted entries**, only under a cap: the derived kinds -- `result`,
   `part`, `component`, `bounds`, `drawing`, `skin`, `robot` -- least recently
   written first, until the store fits 80% of the cap. Sizes
   are deduplicated: an
   object goes only when nothing that stays still needs it, so evicting a
   component entry whose BREP a current tree places frees only the entry.
   When what no pass may remove leaves less room than the fifth of the cap
   above that 80%, the most recently written derived entries keep that fifth:
   a cap the records and documents outgrew never empties every derived cache.
   **Records, document entries, output entries and provenance entries are
   never evicted.**
3. **Unreachable objects.** Kept: every object in the closure, through links,
   of a current-schema record's `tree`, a current-schema document entry's
   tree or the tree of a result this cadgen stored by key while the entry
   stands, and of those trees of a record, document entry or result at
   a key this cadgen does not own while it is younger than a week, as far as
   this cadgen reads their format (the *protected* set); every object a surviving
   derived entry names; anything written or claimed within the grace window
   (default 1 h). Everything else goes, and so do temp files a crashed writer
   or an interrupted pass left, once past the grace window. A document keeps
   its geometry, through its entry's tree, after the model and output records
   are forgotten; its mesh ledger records hashes of output files, which root
   nothing.

**Recently used means recently written.** A hit is a read (§5). An entry's age
is when a build or a derivation last wrote it -- a publish rewrites every
component entry its tree has and the result under its record's key; a
derivation writes the part, bounds, drawing, skin or robot entry it computed --
and an object's is when a
publish last wrote or claimed it. A display cache that is only ever read ages, goes when the cap needs
the room, and costs one recomputation when it is next shown. Evicting never
changes an answer: every reader treats a missing entry or object as a miss.

**Deletion is rename, then recheck** (`objects.delete_unclaimed`). A write that
finds its bytes already present claims them (`objects.put_object`,
`objects.claim_object`), and a publish claims its record's whole closure --
its own components, and a child's tree it composed long before -- right before it
writes the record (`trees.claim_tree`). The sweeper renames an object it means
to delete, then reads the mtime again from the renamed file: a claim made
before the rename shows there, and the object goes back; a claim made after it
finds the object gone and writes the bytes again, or fails the publish when it
holds none -- never writing a record that names a missing object. So a pass may
run beside builds without a lock. The grace window is the whole protection for
an object a build holds before its publish, so do not sweep with `--grace-hours 0`,
or a window of a few seconds, while anything is building.

**A claim stamps the recent past, and keeps what was verified.** A claim sets
the object's mtime to now less two ticks of the clock its previous stamp shows
(`objects._claim`: 62.5 ms where stamps carry nanoseconds, 2 s on a
whole-second clock, 4 s on FAT) -- within any grace window a sweep may use,
and settled as it is set, since a later write must land in a newer tick. The
publishing process verified most of what it claims moments before (§4), and a
claim is the one write it makes to an object it has verified, so a claim
carries the verified identity (§10) forward instead of losing it: the stamp
before the claim must be the verified one (nothing wrote the file since the
read) and the stamp after it the claim's own, on the same device, inode and
size; otherwise the identity is forgotten and the next reader hashes the
bytes. What a foreign writer could do to the file in the microseconds between
that stat and the claim's own timestamp write is beyond any stamp; cadgen's
own writers never rewrite an object in place (temp + rename, §11), and a
reader that uses an object's bytes hashes them. A publish therefore claims on
identity what its job verified, reading nothing, and holds the bytes only of
what its own capture had to read -- which is what a claim that finds its object
gone writes back. In a store several users share, POSIX lets only an object's
owner set an explicit time, so a claim on another user's object stamps it now,
which needs only write access: the object is claimed all the same, and its
identity is forgotten, since a stamp of now is not settled as it is set.

**A store two cadgens share.** Every cadgen on a machine uses the same store
by default, and a pass can only judge what it can read. A newer cadgen's
records, document entries or trees may be in formats an older one rejects
(each carries a `schemaVersion`, and a format change bumps it, §2), so the
older one cannot tell which objects they still need -- and objects are shared
by content across versions. So a pass that finds a record, document entry or
tree in a newer format than its own, or a folder under `index/` it does not
know, written within the last 30 days (`NEWER_CADGEN_SECONDS`), removes
nothing at all and reports why (`deferred`); the newer cadgen collects the
store. Thirty days after the newer cadgen's last write, it is taken to be
gone, and passes resume: its records and trees are then a format nothing here
reads, like any older cadgen's, and its records and document entries, at keys
this cadgen does not own, are retired. The other way round needs only a week: a newer
cadgen treats an older format as garbage, which is how an upgrade frees the
space the old one used, and an older cadgen still in use rebuilds what it
needs -- a retired kind's or an obsolete part entry, a record or a document
entry, at most once a week (§2). Until then each version keeps its
own record and document entry at its own key, so neither recompiles a
document or rebuilds a model because the other one did. A pass
never touches anything outside its own folders: object shards
named by two hex digits, and the `index/` folders in `INDEX_KINDS` and
`RETIRED_KINDS`. This is why a new field that names objects, in a record, a
document entry or a tree, is a format change and bumps that `schemaVersion`:
an older sweeper cannot see it, and must know to stand down.

The mark reads JSON only: records, document entries, derived entries and each
tree they reach, once each, verified against its address. A leaf object counts
by being there; whoever reads its bytes verifies them and repairs or recompiles
a damaged one, so the sweep never reads a BREP, a mesh or a selector table.

**The cap** is `CADGEN_STORE_MAX` (`20G`, `500M`, `1.5GiB`, `0` for none;
default 20 GiB) over the apparent bytes of `objects/` and `index/`. `cadgen
store info` shows the size against it.

**The daemon's housekeeping.** When a request against a store finishes and no
request has been in flight for 30 s, the daemon looks at that store once, under
the cap the requesting client had in force (`CADGEN_STORE_MAX` is forwarded
with every request):

- larger than `max(cap, after + cap/5)` -- `after` being where its last pass
  under that cap ended -- gets a full pass with the cap;
- otherwise, a store holding a retired kind, parts, records or document
  entries that no retiring pass under this cadgen's versions
  (`gc.producer_versions`: the part version) has looked at for a day gets a
  retiring pass, which retires the week-old entries of the retired kinds and
  the week-old obsolete ones -- the records, document and provenance entries at
  keys this cadgen does not own and the results another cadgen stored among
  them -- and sweeps only the objects those entries named. Obsolete entries
  cannot be told from a stat walk, so when that pass ran is noted per store and per versions, in a note of
  its own beside `after`'s: a daemon of another release sharing the store keeps
  its own and never moves this one. So a store gets one such pass after an
  upgrade, then at most one a day however often the daemon idles or restarts,
  and an obsolete entry a pass kept for being younger than a week goes within
  a day of turning a week old (`housekeeping.RETIRE_INTERVAL_SECONDS`).

`after` is noted beside the daemon's socket, per store, in its state
directory, so a store whose records and documents alone hold more than the
cap costs one pass per fifth of the cap it grows -- never one per idle moment,
nor one per daemon start; losing the note costs one pass. The look itself is
a stat walk that keeps nothing per file. A pass runs as `python -m
cadgen.store.gc`, a process of its own, so the record it keeps per object and
entry leaves with it rather than staying in the daemon; the daemon closes its
stdin the moment a request arrives or it winds down, and the pass stops at its
next step. Every step leaves a consistent store, so stopping anywhere is safe
and the next idle look picks it up; even a pass killed between a rename and
its recheck leaves the object under a `.swept` name, which the next pass puts
back if it was claimed and deletes if not. `cadgen store gc --max-size` runs a
pass by hand.

## 9. The daemon

A build is one job: the request that names the model (`python model.py`,
`cadgen step build`), warm on a daemon worker. A child a parent's body calls is no job: it is decoded from its stored
result or built serially inside the parent's own process (§9a). What goes to
the pool is a document compile (`cadgen.daemon.executors.submit_compile`: a
door's, the CAD Viewer's, or a body's `read_step` of a file with no tree) and
the artifact jobs (§3: display, measure, export), all of them the
daemon's.

**Workers are persistent and warm.** The routing
key is the model (`script::function` — two models in one file are two
subjects, two workers): a request for a model whose worker is
idle takes it; whose worker is busy binds a spare as an **extra** for that
one job (the extra returns to the spare set after) -- nothing waits on another
build; a model with no worker
binds a spare and a replacement starts in the background; no spare means a
spawn. A build's worker is busy for its build alone: what the build's results
owe after it answers is the supervisor's (below), so the model's next edit
finds its worker idle, with the kernel memo that edit hits (README law 18). Spares load build123d/OCP as well as the lazy tool parsers before
announcing readiness; importing the supervisor never loads the kernel.
A worker starts in the system temp folder but keeps it off its import path
(`python -P`, as every process cadgen starts does: the daemon, the store's
gc, the Viewer), so a build imports exactly what
`python script.py` would (law 7), nothing another program or user left in
that folder stands in for cadgen, and no import lists it: other programs keep
changing it, and on the path every import that missed it would list it
again, tens of thousands of entries a time, by every worker of a burst.
Spares: `CADGEN_DAEMON_SPARES` (default 2). A request that names no model (a
document compile, an artifact job) borrows a spare without binding it;
borrowed workers count toward spare capacity while busy and fill a free spare
slot when they return, so a stream of artifact jobs reuses warm kernels, and
a subject-less burst keeps its surplus workers for two idle seconds, for an
asynchronous client's next poll (a zero-spare pool retires every returning
borrowed worker). Worker admission accounts for
resident memory and pending reservations,
and may reclaim idle workers or refuse work (§9 below). A worker is recycled after
`CADGEN_DAEMON_RECYCLE` jobs (default 1000) as a leak hedge, and the daemon
exits after `CADGEN_DAEMON_IDLE_TIMEOUT` seconds idle (default 3600). The
first client that needs the daemon starts it, and `cadgen viewer` starts it as
soon as its URL is announced, so a session's first build finds warm spares; a
viewer where none can start refuses to start, naming why.
Inside a worker, a compile or an artifact job is the same client call back to
the daemon; a parent's children build in the parent's worker, in the slot it
holds (§9a). A worker's job answers its client with its exit, once every
model's result is recorded. What a result owes after that -- its display data,
its files -- the build hands to the supervisor as it is recorded (the `owes`
frame, `authoring.supervised`): the supervisor registers the job in its ledger
on the owing model's row (the files it writes, the preview parts it derives,
`JobLedger.owe`), tells the worker so before the build goes on, and runs it as
an artifact job of its own, against the build's store and in its environment,
on a worker of the pool (`server._start_owed`). Such a job is never killed for a
client (it has none) and is one of the daemon's requests in flight, so a drain
for a new version and housekeeping wait for it. An export or mesh job says
each file it saves the moment it lands (the `published` frame), and the
supervisor folds it into the owing row; a display job's answer names its parts
in the build's preview. The request's row stays open until every job its rows
owe has ended (§9b).
**A job runs in its caller's environment**: every request
carries the client's whole environment and the worker applies it for that
job -- a name the caller has is set, a name it lacks is unset, which also
clears what a previous job's model code exported (`worker._apply_request_env`).
The store root, `PYTHONPATH` (absolutized against the caller's folder),
`CADGEN_STORE_MAX` and the telemetry switches reach a
job this way, and so does every variable a model reads (§3). Only the names
the daemon's machinery sets for itself are neither sent nor replaced
(`client.INTERNAL_ENV_VARS`: the worker's own daemon, address and state
directory, the request's job id, the install channel). Nothing else a job
sees comes from the process that started the daemon.

**Scratch outlives a killed process.** A process keeps its served views
(`cadgen-views/<pid>/`), an exported view (`cadgen-view-<pid>-*`), a build's
file-trace log (`cadgen-trace-<pid>-*.log`) and the private copy of a STEP
document it parses (`cadgen-step-import-<pid>-*`) in the system temp folder and
removes them as it ends; a killed one cannot -- routine, since a job whose
caller left ends by its worker being killed, and a view copies every component
it shows, an import the whole document. A worker sweeps them as it starts, on a
thread of its own, and the daemon again once it has reaped a worker that died
or was killed: the scratch of every pid no process holds, and an older cadgen's
pid-less scratch once a day old. A live process's is never touched, a pid it
cannot judge counts as live, and nothing in the store is involved
(`_internal/temp_leftovers.py`). A folder is renamed `cadgen-swept-<pid>-…`
before it is deleted, so a sweep killed midway leaves it condemned rather than
half there with a fresh mtime, for the next sweep to finish. A build stages its
STEP in a folder beside the output (`.cadgen-stage-p<pid>-h<host>-<stem>-*/`)
and publishes it from there; before it stages, it removes from that folder the
staging folders of this machine's builds that are gone (and an older cadgen's
unnamed ones once a day old). A live build's stays, and so does another
machine's: an output's folder can be shared, and a pid says nothing about
another machine.

**Silence means hung, not busy.** While a job runs, its worker emits a
heartbeat frame every 10 s (carrying the job's last announced phase and its
CPU clock). The supervisor consumes heartbeats; they are never relayed to the
client, never enter the job ledger and never count as progress. A worker that
sends no frame for 120 s is killed as hung, unless its CPU clock, read from
outside the process, advanced meanwhile: a native call that holds the GIL (a
long OCCT boolean) starves the heartbeat thread but is computing. A stopped
process, or a deadlock that holds the GIL, sends nothing and accrues no CPU. A
hang that releases the GIL (a network read with no timeout, a Python-level
deadlock) keeps beating and is not killed. A body's length is therefore
unbounded; the heartbeat stops before the job's exit frame, so none
reaches the next job. A starting worker is judged the same way: it is silent
until it has imported the kernel, a few CPU seconds that a busy machine, or a
burst of starts, spreads over minutes, so it is waited for while its CPU clock
moves and killed only after 120 s with neither its announcement nor CPU
progress. Meanwhile the job it is for is listed `queued`, detail `Starting a
geometry kernel`: nothing the job runs can say so before its worker exists.
Frames leave a worker on a descriptor of their own, never fd 1 itself: a job may
point fd 1 at stderr for a while (a STEP read sends the kernel's diagnostics
there), and a beat written there would reach the daemon's log, not the
supervisor, and get a long read killed as hung. A beat no one reads ends the
worker at once: its supervisor is gone, and with it its client's request, which
failed saying so (`lost the build service …`). A job whose caller
left is cancelled, not a crash, in the job ledger and in `cadgen daemon
status` alike, though its worker is killed.

The clock read is the worker's own, which is why a worker is its interpreter
itself: on Windows a virtual environment's `python.exe` is a launcher that runs
the base interpreter as a child, so the daemon starts that base interpreter
directly and names the environment to it as the launcher does
(`__PYVENV_LAUNCHER__`, as `multiprocessing` does). Through the launcher the
clock read would be the launcher's, which never moves, and every long native
call would be killed as a hang.

**A worker that dies is a crash, with its exit status** -- a signal on POSIX,
on Windows the exception code a fault ended it with (`0xC0000005`) -- unless
whoever asked left (the job is cancelled), someone signalled it to stop, or the
daemon's own installation was removed under it (a package manager replacing the
environment): then no worker can start, and the daemon retires as one with a
changed version token does, so its clients start the next from their own
installation.

**One daemon per address, by lock.** The daemon takes an exclusive lock keyed
by its socket address (`cadgen.daemon.transport.SingletonLock`: `flock` on
POSIX, `msvcrt.locking` on Windows, released by the kernel when the holder
dies) before it binds, and holds it while it serves that address — a private
socket is a private daemon. A daemon that stops serving removes its address and
then releases the lock, before any teardown — the socket file only while it is
the one it bound: a daemon whose state directory was removed under it, lock file
and all, can find a successor's socket there, bound under a fresh lock, and
leaves it alone. A second daemon starting for the
same address waits up to five seconds for the lock — its predecessor may be
between those two steps, or, when an upgrade replaces an earlier version, still
shutting its pool down with the lock held — and otherwise stands down, touching
nothing; the winner is by construction alone, so a socket file it finds is dead
and may be removed. Clients elect one spawner the same way and the rest wait
for the address; a spawner whose daemon stood down for a predecessor still
holding the lock starts it again, until the address answers or its wait ends.
The lock holder creates that address's authkey once via a
linked temp file and republishes its in-memory key if an external cleanup
replaces the file. This is the one lock cadgen keeps — a singleton for
the daemon, never a build lock (§7). Prevents: twenty clients starting at
once starting twenty daemons that unlink each other's live sockets.

**The address always binds.** On POSIX the address is a socket in the
daemon's state directory (`CADGEN_DAEMON_STATE_DIR`, else `cadgen-daemon/` in
the system temp folder), which holds its key, its locks and its log. A Unix
socket path has a ceiling (104 bytes on macOS), so when the state directory is
too deep for it the socket alone goes in `/tmp/cadgen-<uid>/`, named by a hash
of the path it would have had: every client of that state directory finds the
same one, whatever its own `TMPDIR`. Anyone can predict that folder's name in a
`/tmp` every user shares, so the daemon makes it mode 0700 and uses it only
while it is a folder of this user's own; one another user made first is never
used, and every door says so and names the remedy (a shorter
`CADGEN_DAEMON_STATE_DIR`). The key stays in the state directory, so a socket
planted in the short folder could not answer the handshake either. On Windows
the address is a pipe name, which has no path to outgrow.

**No daemon, no job.** A request no daemon takes fails, naming why
(`client.DaemonUnavailable`): what a start needs and this machine lacks, found
before any start (`client.unavailable`, what `cadgen doctor` reports: no local
IPC family, a socket folder another user owns, a state directory this user
cannot write), a start that failed, a daemon that exited instead of answering,
or one that did not answer within thirty seconds of its start -- those two name
its log. A request the daemon lost mid-job -- its worker died, the daemon was
killed or stopped answering -- fails and says so, naming the log. Nothing runs
a job anywhere else, and nothing runs it again: a job that ran half an hour and
is then silently rerun is worse than its failure.

**A client that never answers holds up no one.** Each connection proves the
authkey, and the daemon proves it back, on a thread of its own
(`cadgen.daemon.transport.Server`). Accepting waits a tenth of a second for that
before it takes the next connection; one that finishes later is handed over
then, and one that has not finished in ten seconds is dropped — its client
reads a daemon closing while it connects, and connects again. While one is
pending past its tenth of a second, accepting waits for no new connection, so a
burst of silent ones delays no one. A daemon that stops drops the connections
still proving the key, and waits for their threads before it exits. Prevents:
one connection whose challenge never reached its reader (another viewer thread
read it off a reused file descriptor) holding every later request.

**A daemon retires when its address no longer names its socket.** The supervisor
checks ownership every half second, independently of activity and the idle
timeout. A missing or replaced socket ends the daemon and its pool, even with
jobs in flight: their dependencies cannot reach that daemon either. A live
Windows pipe cannot lose its name this way. Every stop closes the listener,
which wakes the accept waiting on it through a pipe of the listener's own,
never by connecting to the address; cleanup removes only the socket it bound.
Prevents: a daemon whose socket file was deleted under it (its state directory
removed, a tmp cleaner) lingering with its workers where nothing can reach it,
or removing the successor's socket when it exits.

The **store root is a field on every request** (`store_root`), applied per
job in the worker, never inherited from whichever build spawned the daemon:
one daemon serves any number of isolated stores. The daemon holds no store
state of its own beyond where each store's last housekeeping pass ended
(§8). `cadgen daemon status` reports each worker's `model`,
`busy`, `jobs`, `extra`, plus `spares`, `imports` (cold spawns),
`concurrent` (extras bound) and `jobs running n/N, queued m, coalesced k`;
`--json` adds the **job ledger** (`cadgen.daemon.jobs`): every job the
daemon ran in the last 120 s -- a row per request, and one per child a request
built inside it (§9b) -- with its state (`submitted` → `queued` →
`building` [phase, done/total] → `done` | `failed`), the output paths it
declared, parsed statically from the script it names, and the files its
results still owe (§9b); a row that still owes a file stays listed until the
job writing it ends. The ledger is the CAD Viewer's only progress source and what a
door asks before it reads a file (§3, *Exports*), and it is process state,
never a file.

The CLI doors (`cadgen step build|compile`, `stl|3mf|glb build`)
are themselves dispatched through the daemon, so they
run on warm kernels; the subject-less commands (`store`, `doctor`, `daemon
status`, all snapshot orchestration) run in-process. STEP snapshots delegate
missing document compilation and part derivation to the build pool -- a daemon
worker's job -- so their request resolution and browser orchestration import no
kernel themselves.

When the daemon's runtime changes, a request carrying the new version is told
to restart. With nothing running, the old daemon releases its address and lock
before it answers, so the client's respawn binds at once. During a build, it
keeps its listener and singleton lock until active jobs and their dependencies
finish. New top-level requests keep asking during that drain until the successor
binds, for as long as a request waits on a silent daemon (the client's request
timeout), and then fail saying so; dependency
requests -- a pool job a running job waits on, a worker's slot -- remain
serviceable, so the work under way finishes.

CPU scheduling and reuse remain independent of memory admission:

1. **Job slots — one running build per core.** A counting semaphore of
   `N = os.cpu_count()` slots, the daemon's, shared by its workers
   (`CADGEN_JOBS` overrides; `daemon/broker.py`). A job takes a slot before its body runs and
   holds it through its emit -- a child built inside the body runs in the slot
   its parent holds (§9a) -- and it **yields the slot while it waits for a pool
   job** (a compile, a part's selector table a mate's axis reads, a file a
   door it opens is still owed; an export job, for the display jobs of its
   result) and reacquires -- queuing if it must -- when that wait ends.
   A waiting job holds no CPU slot, which is why a 1-slot pool still builds a
   tree and writes its files. Work under way goes first: a slot asked for to continue
   it -- an artifact job a build hands over or a view waits on, a job taking back
   the slot it yielded -- is granted ahead of every job not yet started, in order
   among themselves, and the rest in order (FIFO), so the jobs a build owes run
   ahead of builds that have not begun. Slots count kernel work only:
   the build pipeline takes one around a model body and its emit, in the
   build's own process, and each artifact job takes one of its own on its
   worker (§3: a build has its parts' display data derived, and its files
   written, by the pool). A door and a server
   hold no slot and derive nothing on a request's thread: a part whose display
   data the store lacks is one pool job. **Doors take
   none and never run a body**: `snapshot` and the mesh doors
   (`stl|3mf|glb build`) ask one question of a document — does the store have a
   tree for this file's bytes (`doors.document_tree`: `sha256(bytes)` →
   `index/document` → tree; no record is opened, §2 the law)? Yes → read it; a
   source that has moved on is the model's record's business, not the door's,
   so no document is ever refused. No → the door (or the CAD Viewer) submits a
   **compile job** to the pool (`executors.submit_compile`) that builds a tree
   from the bytes, generated or imported alike — the one door operation that is
   a job: it runs on a spare, holds a slot through its read and emit, coalesces
   on the document's bytes and shows in the tree. The tree shows `queued` when a
   slot did not come at once.
2. **In-flight coalescing.** A compile or an artifact job matching one already
   in flight (`(store, bytes)`; `(store, request)`) attaches to that job instead
   of starting another. In flight only, never a lookup into the past, and never
   a model build: a second `python a.py` still runs, on an extra, and two
   parents needing one stale child build it once per run (§9a), not per pool.
   This is also how a build's body, or its export job, waits for a display job
   the build handed over: it asks for the same work, and attaches.
   A compile answers with its exit, once its native result is stored: its
   display jobs are the supervisor's after it, as a build's are. Coalesced
   compile callers receive the owner's exit, and owner and follower ledger rows
   receive its completion status and error. If a native reader cannot read the
   geometry after the answer, it selects the current bytes again and asks for a
   fresh repair -- the answered compile is in flight no more, so nothing rejoins
   it; it reports a failed compile rather than resubmitting it. Readable
   geometry returns immediately, without waiting for display data.
3. **Idle unbind — 10 minutes** (`CADGEN_DAEMON_IDLE_UNBIND`). A bound worker
   idle that long returns to the spare set (spares beyond K exit); its model's
   next build rebinds a spare — no import repaid.
   Purely RAM: idle workers hold no slot and never block a new model.

**Memory admission.** The daemon sums worker RSS including extraction
descendants, pending spawn reservations, and retiring workers until they exit.
Idle workers are reclaimed oldest first. Busy/suspended workers retain at
least a worker reservation. That reservation is calibrated, not configured: it
starts at a 512 MiB seed and, on every accounting pass, becomes the lowest RSS
among workers that are idle and have served no job — what a worker costs once
it has imported the kernel and before it holds any geometry — never below the
seed. A worker that has run a body is excluded, so retained geometry cannot
inflate the reservation that keeps it resident; the dependency headroom is
derived from the same number. Ordinary root requests preserve dependency
headroom; dependency requests can spend it. A known oversized root reservation or
retained worker may use that headroom only as the sole worker charge, and
only within the total allowance. A request that cannot fit waits while builds
hold run slots or spawns are still starting, since each hands its charge back
when it finishes. It fails explicitly, with the build's geometry still owned,
only when nothing is in flight that could release memory, so builds all
waiting on jobs they cannot admit error instead of hanging.
Reclamation drops process state only; it never runs persistent-store GC.
Reservations and sampled RSS form a soft operating budget. Arbitrary future
native allocations cannot be predicted or stopped by this admission check;
active shared work is not killed merely to recover budget.

| Setting | Default |
|---|---|
| `CADGEN_MEMORY_MB` | 70% of discovered physical/cgroup RAM; `0` disables |
| `CADGEN_COMPONENT_MEMORY_MB` | 384 MiB per extraction subprocess |

The per-worker reservation and the dependency headroom have no settings; the
pool calibrates both.

This is a soft admission envelope, not a native allocator limit. A single
OCCT operation may grow between RSS samples. Where RSS cannot be enumerated,
reservations still apply. CPU slot counts remain upper
bounds, and extraction concurrency also fits a per-worker extraction ceiling
(a third of the budget, capped at 2048 MiB), which is not the admission
reservation and is likewise unconfigurable.
Geometry publication starts no extraction subprocesses: a build's display
data is derived by artifact jobs (§3), as a view's is. A display job for parts the
store lacks uses the shared artifact-job admission and one private derivation
per requested component; it has no model binding, declared output
or editing-producer order. Writing a stored tree to a staged STEP (`kind:
export`, `store/exports.py`) is an artifact job too: a request names a tree and
a staged file, never a script. On the daemon, an artifact job is never killed
when its caller leaves, as the CAD Viewer's do routinely: the worker stays warm
rather than being replaced by a fresh kernel import. Before each derivation
the worker asks its supervisor whether anyone still wants the job: its caller,
or an identical request that attached to it since. When no one does, the job
ends with what it has derived, all of it in the store, and a later identical
request starts its own job, which finds those there. Prevents: a job no one
wants keeping its worker busy for the rest of a 64-component request while the
next model's request waits for a kernel import. A build or a door whose caller
leaves before its answer is stopped. Once a build has answered, its worker
finishes the display data and files it owes even after its caller has left.

**Browser resources.** Disposable decoded meshes, selectors, BVHs, GPU buffers,
textures and worker work may have byte budgets and be reclaimed when unused;
admission counts replacement overlap and temporary allocations (estimated where
browser APIs measure nothing), and no scene's release invalidates another's
active owner. Such budgets and reservations are process state, never persistent
geometry or cache identity: they do not change exact objects, canonical tree
hashes or export tolerances, do not delete the disk cache, and must preserve a
usable view on denied work.

## 9a. Children

A child is a decorated model a parent's body calls. The call goes through the
one pipeline every model goes through (`generation.build_model`), in the
caller's own process, there and then:

- **The gate runs at the call** (§4). A child whose stored result stands, or is
  restored by key, is decoded from it (§6, `store.materialize`) and the call
  returns that geometry, the files it owes queued. Any other child is built --
  a build of its own, with captures of its own (below), publishing its own
  record -- and the call returns its result decoded. Either way the parent's
  features see the child the same way: a stored result, decoded. A child is
  never a job of the pool and never another process's; `--force` and
  `--profile` are the root's flags and never reach a child.
- **Keyed by its tree.** Inside the parent's run the decode is a kernel-memo
  entry keyed by the child's tree hash and the label it is handed back under
  (`kernel_memo.decoded`, README law 18), as an operation with no shape inputs
  is: the n-th decode of a tree in a build -- counted across the runs it nests,
  so a child built inside the body is never handed what an ancestor holds -- is
  handed the objects the n-th decode of an earlier build in this process made,
  restored to the state the decode returned them in, every change the memo saw
  a run make to them undone. So the
  parent's calls on its child -- placements, booleans, compounds -- are hits on
  its next run down to the call an edit changed, as a link to another document
  keeps its identity in FreeCAD; a child rebuilt is another tree and misses. The
  decodes are the memo's entries, bounded with them (LRU by entries) and gone
  with the worker the daemon reclaims (§9). A
  hit reads nothing from the store: the tree's hash is its content, and the gate
  verified its objects before the child stood (§4, clause 4). A decode is no
  feature and no construction key names it (§2): the child's elements keep no
  name in the parent, and the parent's tree is the same built cold, hot or with
  hits off, its child built inside it or decoded.
- **Serial.** Children build one at a time, in the order the body calls them,
  in the slot the parent holds (§9). A body waits for each call as it would for
  any function.
- **Once per run.** However often a run calls a child -- twice from one body,
  once from each of two parents -- it is decoded or built once
  (`authoring.BuildRun`), and every call composes the same tree.
- **Recorded from the calls, by key.** The parent's record lists every child
  the body called by the key of the result the call composed, a discarded call
  included: the body called it, so its result is this build's dependency, and
  its declared outputs are owed -- a child whose geometry the parent threw away
  still writes its STEP.
- **Captures of its own.** A child's build inside its parent's body opens its
  own file trace, execution hashes, kernel-memo run and build clock, each
  nested exclusively: what the child's body reads, lists, imports, constructs
  and spends is the child's and not the parent's, and the parent's capture
  sees none of the child's pipeline either -- its gate, its export, the hashing
  of what it read run under the trace's pause (`filetrace.paused`). As the
  child's build ends, the first-party modules its body first executed (a
  helper it imported itself) leave `sys.modules`, so the parent's body imports
  them itself, as it does when the child is decoded. So a child's closure is
  the same built alone or inside its parent, and a parent's closure is the same
  whether its child was decoded or built: a child is its parent's input by its
  key, never by what it read (§5). What importing the child's module ran in
  the parent's process is the parent's, as ever (§3).
- **Failure.** A child's build that fails raises in the parent's body as the
  exception the child's body raised: no wrapper. The body may catch it, but the
  run still fails with the child (`authoring.BuildFrame.failed`): a parent never
  publishes over a child it could not build, and its previous document stays.
- **Files owed.** A child's files are its own build's: its export job is
  started as its result is recorded and runs on the build pool while the
  parent's body goes on (§3, *Exports*), as do its display jobs; in a daemon
  worker they are the supervisor's, and the command answers with its stored
  results without waiting for them. A child's `done` says its result is
  recorded and its parent may compose it; its file lands when its export job
  publishes it, before or after that event.
- **Cycles.** A model called while it is itself being built is an error: a
  model may not depend on itself.

The top-level call renders the graph these calls reveal as a build tree on
stderr (`cadgen.cli_tree`): a TTY gets one refreshed block — `submitted`,
`queued`, `building · <phase> n/total`, `current`, `✓ <time>`, finished subtrees folded
to one line, current children counted on the parent's line; `--json` gets one
JSON line per model transition; any other non-TTY gets no transitions, only
each built model's time line and an already-stale notice. Every mode prints,
once per built model, the warning that its code started a program (§5, every
read is seen) and the build's own warnings, from the `started` and `warnings`
lists its `done` event carries as it carries its `timings`. A child's events
are the parent's process's own, so they reach the root as the parent's do, in
the worker's frames. After
publishing, each built model asks its gate once more, and its `done` event
says `already stale: …; rerun` when a source it was built from, a child's
among them, changed during the build.

After a successful top-level build, the caller waits for the checked source
result before returning, while the build pool writes the files. A proven discarded bare call in the actual real-file
`__main__` module then returns without materializing that tree into native
geometry. Any caller that consumes the result, any interactive or instrumented
execution, and any uncertain bytecode keeps the materialized return. The source result is published before the answer; declared-output completion and
any failure to save follow on the pool's jobs, in the ledger (§9b).

## 9b. Editing previews and explicit saves

Running existing decorated code publishes each model's complete preview the
moment its result exists, before any export, and answers then. The jobs the
result owes -- its files, its display data -- go on in the build pool, the
daemon supervisor's, until every declared output is written or its failure is
recorded (§3, *Exports*). No model author imports a session, cache or
ownership helper. Source files remain the durable authored inputs. The preview
is the result the record names: a worker crash fails that request, the jobs it
handed over still write what they owe, and a record whose files a job could
not write owes them, which the next run writes (§3, *Exports*); there is no
crash-resumable save queue hidden in the cache.

The daemon identifies each accepted request by an epoch and monotonic ordinal,
records its store root and declared output paths, and attaches producer IDs to
events. Assigning a producer sequence and delivering its event are serialized,
so concurrent preview completion cannot overtake an announced save. An editing
session selects the newest request for its output and store;
late older events cannot replace it. It may retain the previous visible model
while the newer request builds. A daemon restart expires request ordering; a
disconnected preview is labelled as such. The ledger is short-lived and
deletable, never the only durable copy of an authored change. A build's events
carry what that status reads and no more, and the feed has two of them per
file: the **result** -- the preview revision, naming the output, the result
tree, the producer its parts are keyed under, the part key of each component
the store holds, of each it does not yet (`pending`) and whether that is all
of them, with its annotations as the one object they are (below) -- and the
**file**, `saved`, the file-written result: the tree the bytes were written
from and their digest, the moment the file lands. The parts the store lacks
are derived by the display jobs the build handed over (§9): the supervisor
names each part in the preview as its job's answer stores it -- in a parent's
preview too, whose tree places a child's components by the same ids -- so the
preview is `complete` once nothing is pending; a part no job could derive
stays pending, and the job's failure is a warning on the row
(`warnings`), never its failure. Between the two, each file the result owes
-- its STEP, its declared meshes -- is named once, `saving`, with the tree it
is written from, the moment the supervisor takes the job that writes it, and
the job says each file as it lands (`saved`), a declared mesh found current
included. That pair is what a door reads to know a file is still being
written (§3, *Exports*). A newer result's job takes a file over from older
ones in the store (`superseded`). The ledger keeps nothing else of any of
them, each with the ledger's revision when it landed (the order of results
across rows). A child's row is `done` from its own done event, its file
saved or not; the request's row, the root's, stays open while any job its
rows owe runs, so it reads as building until its files land, and is `done`
when the last ends (`daemon/jobs.py`). A request that failed or was cancelled
closes at its end, and its jobs still write what they owe. A job that fails
fails the row that owed the file, with the job's reason -- a child row closed
already included, and the request's row when it closes. The ledger keeps
those failed obligations as `saveFailures`, per output. The feed reports
`failureStage: "save"` and `saveFailure: {output, tree}` for that file; an
already-saved STEP remains successful when a sibling mesh fails. A
provisional preview alone proves no successful result: a body or
annotation-validation failure stays a build failure. Kinematics, appearance
and animation travel in the sidecar for every durable reader, and in the
preview's annotations object for the viewer alone.

Model runs and explicit export retries advance editing order: a compile of saved
bytes is listed against the document but never hides a run's preview. A child built inside a
request (§9a) has a row of its own under it in the ledger -- a child in the
request model's own file too, since rows are keyed by model, not by script --
listed from its first event with its declared outputs, closed by its own done
or failed event or with the request, and its preview and saved file are its
row's.

These ephemeral preview handles are not GC roots. The normal grace period
protects newly published objects; explicit GC or cache deletion can expire an
older preview, including one retained after a failed save. The durable source
and saved STEP remain the recovery path.

The viewer shows the saved file: the catalog's bytes with the topology and
annotations that belong to them, replaced in place when a build writes new
bytes (the components it already holds are retained: the last paragraph). It
reads this channel (`GET /__cad/preview`) for status and for the **preview
revision**: whether a build of the file is queued or running and its phase, a
failure with its message, and, from the moment the build's result exists, the
result tree's hash and the part key of every component in it, with the parts
the store holds -- those it held already at once, the rest as the build pool's
jobs for them (§3) store them, before the file is written (the export waits
for them). The viewer reads those
objects as it reads any tree and any part and draws them, labelled as a
preview of the build in progress, then replaces them in place with the saved
file when the catalog names it, keeping every part: the file's bytes map to the
very tree the preview drew (§3, *Exports*). What
the sidecar will carry travels with the preview as its **annotations**: the
model's declarations resolved against the result tree as soon as it exists --
the mates into the articulation a page plays, `materials=` into finishes on
leaf tokens, the clips into keyframes -- published as one artifact-side object
the preview names, so Position, finishes and routines work on a preview as on
the saved file, by the same tokens; the save confirms them (nothing on screen
moves) and binds the file's own sidecar for every durable reader. A declaration
that fails to resolve is the build's failure, and the preview stays up without
it, under the failure. A preview is never shown as the file: nothing binds the
file's sidecar to it, it is not the catalog's entry, no snapshot, export or
`read_scene` reads it, and a save that fails leaves the preview labelled as
what it is, under a **Save failed** alert. Usable partial previews stay visible
too. A retry may carry only a `saving` or `saveFailure` tree; the viewer retains
the prior unsaved preview only when that tree matches exactly. A new request
with no recorded result cannot adopt an old preview as its own. An answer carries
no geometry of its own; the
server does no kernel work and exposes no source, closure or model record. A
finished build whose output is no longer the file on disk -- the bytes it saved
replaced or, if it saved nothing, the file written after it ended -- is
reported `superseded`, and its failure and its preview are no longer the news.
The viewer reads the catalog again when a build writes the file (`saved`),
finishes or is superseded, rather than at its next poll, and never announces a
background file write it did not perform. A saved-tree identity change clears
incompatible selection and measurement state; a preview replaced by its own
save clears nothing, since the tokens are the same.

The server reads one more thing off the channel, to save that catalog read its
time: when a build of the watched file has saved its outputs, it starts their
catalog rows on a thread of its own, the watched file first
(`cadgen.viewer.warm`). Law 1 holds for those rows as for every other: a row is
computed from its file's bytes, and its digest from those bytes. The tree the
ledger says the build saved is used only to start that tree's capture while the
bytes are read; a row shows it only if the bytes name it. Warming is best effort:
what it fails at is left to the read, and the channel answers regardless.

An open editing tab holds one request against an opaque ledger cursor scoped
to its output and store. A matching change wakes it immediately; unrelated jobs
do not cause browser updates. The cursor and job snapshot are captured under
the same lock, and a daemon restart changes the cursor's epoch. The daemon
admits at most 32 read-only waiters, separate from build workers and CPU slots;
saturation returns a snapshot without another thread and the client backs off
to 500 ms. A one-second heartbeat
still rechecks whether the file moved past the newest build, even with no build
event. Closing or switching the tab aborts its request; a disconnected feed
retries without starting a daemon or a model. This adds no persistent state.
The CAD app's tunnel (`cadgen.mcp.tunnel`) never holds the request: it drops the
cursor, so the daemon answers with a snapshot at once, and the client paces
itself (a host relays every call through a few slots all its views share).

The preview is the record's result: the model's final immutable result, which
becomes `record.tree` once the publication checks pass, before its exports (its
STEP output pending until written, §3), and which a parent composes before the
child's file is written. `record.tree` is never replaced by geometry
translated from the STEP. The bytes an export writes from the result map to it
once they exist (§3): no bytes are mapped to a tree before they are written
from it. The command's success reports the stored result; the ledger reports
whether each declared file was saved. The jobs a build owes write every
declared output, and a failure after the answer remains owed on the record.
`cadgen export <file>` completes the named file and its dependencies before
answering (§3, *Explicit handoff*).

An interactive viewer can retain a complete displayed component across a
replacement when its part and origin agree. Tree-specific placements and
appearance are recomposed. This is disposable browser ownership, not a new persistent cache
or source of geometry identity. Superseded or failed staging does not release
the last complete view's ownership. No automatic-save producer exists in this
runtime: all decorated runs have explicit completion obligations, so display
supersession does not cancel their exports.

## 10. Debugging

- Which record: `index/model/<sha256(script::function)>-v<schema>` —
  `cadgen store why <model.py>` prints it (every model of the file; name one
  as `model.py::function`), the gate's verdict clause by
  clause (with each child's recorded vs current key), the closure files (a
  sliced helper as `lib/profiles.py[WIDTH, bar, …]`, its reached names) and
  the tree's links. The verdict line names the first stale clause as a
  phrase: `no record`, `closure changed: <file>` (the record keeps each
  closure file's hash under `closure.shas`), `closure changed: <file>
  appeared` (a file the imports relied on not existing), `environment
  variable <NAME> changed`, `constant changed: <NAME> in
  <file>`, `closure file missing: <file>`, `child stale: <child.py>`,
  `child rebuilt: <child.py>`, `tree or components missing`, `never written:
  <path>`, `output missing: <path>`, `output changed: <path>`, `producer
  changed: <name> <recorded> -> <now>`.
- Which code made a part, a face or an edge of a generated STEP:
  `cadgen store where <file.step> <ref>`, the ref as the viewer copies it or
  `read_scene` resolves it (`#o:…`, `#o:…/f:…`, `#label/e:…`), from the
  model's provenance entry (§3): `made by src/bracket.py:12 in bracket (cut)`
  with the features it was given, and the one that last modified it. Exit 1
  and `unknown` with the reason when there is no entry, the entry is for other
  bytes, or no feature made the body; `--json` for the answer as data.
- Resolve a tree: `cadgen.store.trees.get_tree(hash)`; flattened with
  `capture_tree(hash)` for an owned verified flattened view and byte closure.
  Metadata-only consumers use `capture_tree(hash, retain_payloads=False)` to
  perform the same complete verification while releasing each raw object after
  reading it. Compact process-local metadata may be reused while every required
  immutable object retains the file identity observed around its verified read;
  deletion, damage or atomic replacement invalidates that snapshot, and the
  next request verifies again, by hash, what moved and takes on its identity
  what still holds: each object this process hashed to its address is
  remembered under the settled identity it had (`objects.verified_stamp`),
  each tree it verified under its whole closure, and a component entry
  already validated against its object's bytes needs no bytes while the
  object holds. A native capture (`retain_payloads=True`) reads every object,
  since it owns the bytes, and remembers what it verified all the same. A
  claim by this process keeps an identity (§8); any other write moves it. A
  read is only remembered once it is far enough past the write it observed
  that a further write must
  stamp a different mtime — a filesystem times writes by a clock of its own
  resolution (~15.6 ms on Windows, whose `st_ctime` is the creation time and
  never moves for a rewrite; a 100 Hz timer interrupt on Linux before 6.13,
  even where the stamp shows nanoseconds), and a same-size rewrite inside that
  tick is invisible to every stat field. No read settles in less than two
  Windows ticks. The metadata cache is byte-bounded, store-root
  isolated and returns a newly parsed flattened view to every caller. The gate's
  completeness check (`tree_complete`) is such a consumer, and its output
  digests follow the same rule (§4). The CAD Viewer's part routes are not:
  they verify a tree's component map once and keep it, for the eight trees
  they served last, and per request stat the tree and the objects of the
  components it names, so a deleted one sends the tree back through the
  complete verification.
  Components carry `brep`, `codec` and `faceColors`; display data resolves
  separately, by each component's part, through `store.parts` and `index/part`.
- `cadgen store info` sizes the store against its cap, names any retired
  kind still present and any `index/` folder it does not know, and says when
  a newer cadgen's writes keep passes off the store. `cadgen store gc
  --dry-run [--max-size [SIZE]]` reports what a pass would retire, evict and
  remove, and deletes nothing.
- **Resets, smallest first.** `python model.py --force` rebuilds one model
  now. `cadgen store forget <model.py>` drops that model's record, its
  provenance and the results it stored by key, so its next run, or a parent's,
  builds it again (`child stale` until then), its children untouched;
  `cadgen store forget <document>` drops this cadgen's `index/document` entry for the
  file's bytes and the record that wrote it, so the next open or door call
  compiles it again. `forget` never deletes objects — `cadgen store gc` does,
  for whatever no record, document entry or result reaches any more. Clearing
  the store is always safe:
  delete `~/.cache/cadgen` (or the `CADGEN_CACHE_DIR` directory); every model
  reads as stale and rebuilds; no project file is touched.
- The gate's producer clause (§4, clause 6) rebuilds every model once after a
  cadgen or kernel upgrade, so a record a cadgen with a bug built does not
  outlive the fix. A wrong record under the same producer is recovered with
  `forget` on the affected models (or the parents that link them), or by
  clearing the store.

## 11. Never

- Write a file into the store non-atomically (objects: temp + rename under
  the hash; entries: temp + rename).
- Put a path, a timestamp, or anything machine-specific into an object.
- Derive a model's dependencies from its tree's links.
- Add a version salt to an object's address, or a global schema number to component
  identity (§2, identity and versions).
- Add a lock that a reader consults to decide freshness — or any build lock
  at all; the publish rule and keys are the whole concurrency story.
- Let a door, the viewer, snapshot or any render path open the code side --
  `index/model`, `index/output`, `index/provenance`, `index/result` (§2,
  property 2). A reader's one lookup is `sha256(bytes)` → `index/document` →
  objects.
- Make a reader refuse, or a door rebuild from source: a missing tree is a
  compile job from the file's bytes; "behind its script" is `store why`'s.
- Write to the store on a read: no hit refreshes a stamp or an mtime (§5).
- Evict a record, a document entry, an output entry or a provenance entry, or
  delete an object any way but rename, then recheck (§8).
- Delete anything from a store a newer cadgen wrote to within the last 30
  days, or touch a folder under `index/` that is neither in `INDEX_KINDS` nor
  in `RETIRED_KINDS` (§8).
- Use process/display eviction as a reason to mutate exact geometry. Disposable
  memory budgets and worker reclamation follow §9 and never determine
  saved-artifact freshness.
- Let a decorator argument change the geometry a model produces: arguments
  place files, tune how they are written, and declare kinematics; the tree
  is the return value as returned (README law 16).
- Write a sidecar for anything but kinematics, finishes and keyframes, or copy
  into it what only a record needs (README law 17): a mesh door tessellates the
  document's tree and never reads a declaration back.
- Key a part made by a construction by its component's bytes, measure geometry to
  name a part or an element on the way from a model's run to its record, or name
  an occurrence or an element by an ordinal of a list or a position: parts and
  refs are named by what made them (§2, §3; README law 20).
- Carry a frame or a line in an object, a part, a document entry, a sidecar or a
  written file, or read `index/provenance` from anything but `store where` and
  `store why` (§3).
- Show a preview as the saved file, or bind a sidecar to one (§9b); map bytes
  to a tree before they are written from it (§3, *Exports*).
- Give a model's file a tree of its own beside its result: its bytes map to the
  result they were exported from (§3, *Exports*).
- Store a mesh no face of which was verified, or let a clock decide which faces
  are (§3; README law 1).
- Memoize a kernel operation anywhere but in the warm worker's memory, or
  answer a memo hit with anything but the live object the first run made
  (README law 18).
- Use a retired word (§1) in code or documentation.
