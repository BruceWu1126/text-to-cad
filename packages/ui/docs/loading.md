# Loading, memory, and live updates

What the viewport does in the background between "a file was selected" and
"the picture has settled": which mesh each part is drawn at, how a large
assembly arrives, what it refuses when memory runs short, and how it follows a
model that is being rebuilt while you watch.

All of it is the STEP package path of the STEP renderer. A triangle mesh (STL, 3MF) and a
GLB are drawn as they are: their renderers load the file whole, parse it once per revision
(an STL in a worker) and keep the decode cached, so reopening one fetches and parses nothing
(see [Mesh renderer](cad-renderer.md#mesh-renderer)).

The observable promises are in [the web app README](../../../apps/web/README.md#the-laws-that-bind-the-app);
this file is the mechanism behind them. None of it may change **exact**
geometry, measurements or explicit mesh-export tolerances.

| § | Covers |
|---|---|
| [1](#1-the-display-mesh) | The one mesh every part is drawn at, stored and produced meshes |
| [2](#2-progressive-loading) | Batches, the one STEP scene, framing, accelerators |
| [3](#3-admission-and-memory) | Budgets, reservations, what cannot fit |
| [4](#4-failure-and-cleanup) | A scene that fails to build, ownership |
| [5](#5-following-an-active-build) | The edit feed, what the viewport shows, the status heartbeat |

## 1. The display mesh

Every STEP part is drawn at cadgen's display tolerance (`cadgen.tessellation_policy`),
and only at it: it opens there and stays there, whatever the camera, the size of
its assembly or the memory left. The tolerance is cadgen's alone; the page never
names one. Each component of a view names its **part** (`part` in `assembly.json`),
and the part's record names the display mesh the page reads to draw it
(`partRow`, `@text-to-cad/core/lib/surf/tessellationCache.js`).

Every mesh the viewer draws is cadgen's (OCCT's mesh of the exact BREP, stored
by cadgen, derived by the build that made the model before it reported done); the
browser never tessellates. The parts' records are probed a chunk
of components at a time and their meshes read a batch at a time
(`createInitialDisplayPlans` in `renderers/step/render/initialDisplay.js`,
`packageBatchReads.js`), the first of each the size of the first publish, so the
first geometry waits on no more than it draws. A component whose part the probe
does not find is asked for in the `/__cad/surfaces` request, whose ready row
carries the part's record, which the component then reads like a stored one;
a part the store lacks altogether (a cleared or evicted store) is derived by one
cadgen build-pool job for the request, the loading screen saying it is meshing
parts meanwhile. Both are
sized by the stored body before their decode is admitted; one above the
concurrent decode cap runs alone only when the shared Viewer memory envelope
can reserve its complete estimate.

Preview draws the scene one quality tier up (`kit/viewport/renderProfile.js`: pixel
ratio, shadow and environment maps) at the same meshes. The chrome does not report
loading progress beyond its overlay: the STEP renderer records first geometry and
the whole model for benchmark harnesses only (`window.__cadViewerLoad`,
`useViewportLoadStatus`), and background file writing stays quiet.

## 2. Progressive loading

Components load with bounded concurrency and publish in batches whose ceilings
double from the first to the last (`packageProgressiveLoad.js`), so the model
paints while it loads and a cancel frees what was never published. The load order
puts the model's extreme-placed components first, so the first batch already
spans the model on screen.

Every publication lands in the ONE STEP scene the viewport adopted
(`renderers/step/scene/stepScene.js`): while the structural build key holds for the
same model, the live build reconciles its records in place, and the viewport is
told the scene changed (`viewport.commitScene()`) rather than handed a new scene —
so a progressive open never re-dresses every material or re-adopts anything.
Publishes share unchanged occurrence rows and tree metadata; placement,
appearance and changed bounds still update their records. The camera frames the
model once, on its first publish, on the box `assembly.json` declares for the
whole of it (`bbox`, carried as the composition's `declaredBounds`): the scene's
`restBounds` is that box from the start, so the framing, the orbit pivot, the zoom
ruler, Zoom to fit, preview's turntable and the ground's size are final before
most components have arrived, and later publishes move none of them. A descriptor
without a box leaves the scene `complete: false` until the last component is in:
it is framed on what arrived first and once more when it is whole, unless the
person has taken the camera by then.

Display raycast accelerators are requested only when a picking ray reaches
component bounds, then queued during idle time for one worker at a time.
Admission covers private input copies, worker scratch and the returned tree;
displayed arrays stay attached and unchanged. Releasing the last geometry owner
cancels its build, and stale results cannot attach to replacement geometry. The
first pick remains exact and may cost more on a dense component; merely loading
an assembly does not build an accelerator for every component. Inputs with a
separate merged face-selection proxy still build that proxy's accelerator on the
main thread during idle time. Canonical STEP selectors use the display meshes and
do not enter that separate path.

A closed tab keeps the complete package it showed (`render/completedPackageCache.js`,
bounded by count and bytes), so reopening it reads nothing; a same-file revision
reuses every component whose part it shares (`packageComponentReuse.js`).

## 3. Admission and memory

One ledger (`render/viewerMemoryPolicy.js`) accounts what the viewer owns or can
estimate: displayed arrays, selectors, BVHs, GPU uploads, asset caches, worker
heaps and a staged revision. A component's decode is admitted only when its
estimate fits the in-flight cap and the ledger can reserve it. Admission can
reclaim idle decode workers and the completed-package cache and retry while
preserving active consumers. Its ledger samples each live worker's own retained
estimate before admission, so a large component does not inflate every worker's
charge.

Display arrays shared with asset caches have one CPU charge for the entire
backing allocation, including unused sections of packed buffers. GPU charges
use uploaded view sizes; CPU-only edge inputs and picking allocations are
accounted for separately.

A component that cannot fit is not loaded: the ledger notes the limitation, the
load fails with its error, and the view stays up with what it drew. **Estimates
and sampled resource totals are a soft budget, not a hard browser RSS limit.**

## 4. Failure and cleanup

A scene sync that fails part way through an in-place reconciliation tears the
whole scene down (`render/sceneCleanup.js`) before it says so: it never
reconciles against already-disposed records. A cleanup that itself fails keeps
the scene reachable and its buffers charged until a real cleanup retry succeeds,
and the viewport says the scene could not be released.

## 5. Following an active build

STEP entries always follow active edits, showing the root preview before its
STEP save. Run the model normally; existing decorators need no new imports, and
the daemon must be running for live updates.

**The feed.** Updates arrive through a held request that wakes when this
output's build ledger changes. Unrelated jobs do not wake the tab. The server
admits 32 waiters independently of kernel workers; excess tabs retry every
500 ms. A server that answers at once instead (a host relaying requests through a
few slots all its views share, where a held request would take one) is paced by the feed: an answer
with nothing new waits out the rest of a second, and news is asked after again
within a tenth of one. An idle heartbeat revalidates saved bytes and missing geometry; closing
or switching the tab cancels the request. Older status responses cannot
overwrite newer cached progress, and saved revisions are verified from one
coherent file snapshot. Restarting the daemon expires the ephemeral session,
and rerunning the model reconnects it.

**What stays on screen.** The prior model stays visible while the next request
builds; failed updates remain visible while an idle disconnected feed retries
quietly. A complete model of the same file — a part's or an assembly's — also
remains visible while its rewritten file is rebuilt and while the replacement
meshes load (`replacingSameFileMesh`, `awaitingSameFileRevision`), reported as an
update ("Updating model…"), never as the loading screen — also when edits come
faster than revisions load, and one revision's load is cancelled for the next
(`meshStateAfterCancelledLoad`). Complete displayed component arrays remain available while a
replacement stages or fails. Reuse requires the same part; placements and
appearance come from the new tree. Selection, measurements and reference copying wait for matching
new geometry. A failed replacement preserves the view and reports its error;
only that file/hash stops retrying automatically. A first load that fails part
way keeps the parts it drew under its error. STEP pose and animation
metadata use their normal loading path, without a promise to retain the
previous pose. Snapshot source isolation is unchanged.

**Overlapping loads.** Geometry and reference loads own their cancellation
independently; a superseded request cannot cancel its replacement. Topology
requests for one file revision union rather than supersede: a batch in flight
finishes, and one no longer wanted is simply not published.

**After a save.** The view shows the saved file. When a build writes it, the new
revision replaces the one on screen in place, keeping every component whose identity
it shares; while the build runs the model on screen says it is updating, and a failed
build leaves it on screen under its alert.

**What the viewport shows.** The navbar carries no status. Opening and
updating are the viewport's loading overlay (`ViewerLoadingOverlay`). A failure is
a card over the viewport (`kit/status/ViewerAlertCard.jsx`) with its explanation,
its next step and the full diagnostic under Details: an error always shows, and a
failed update the model survives (`blocking: false`, the previous version still on
screen) has a Dismiss button and stays dismissed until the alert changes, or
clears and is raised again. A warning is a card too, one that can be dismissed
while the model is on screen: the card is the one place a problem is said. Once a
usable current view is displayed, saving, successful completion and idle
edit-feed state stay quiet. A sidecar this build cannot read raises
no alert: the model renders with no kinematics, no materials and no routine, and
the migration is announced where it can be acted on — the build and the cad
skill. Existing usable views remain visible during updates and failures.

**Opening.** Opening shows one step line — **Finding file**, **Importing
model** (cadgen compiling a file its store does not hold: reading it, then its
parts, counted), **Building model** (a model script running: every phase of its
build), **Reading model** (reading a model the store holds), **Meshing parts**
(cadgen deriving parts its store lacked: a cold open), **Loading geometry**
(reading stored meshes), **Preparing view**, or **Opening model** for a wait
with nothing more specific to say — with no headline above it; the loading mark itself says a
model is opening. Once the first parts are drawn, a cold open's remaining
meshing is the update line's "Meshing parts n/total…", not "Updating model…".
Counts measure
completed geometry items in the current stage, not assembly occurrences or an
overall ETA; uncounted stages are indeterminate. Render initialization uses the
same indicator against the destination backdrop until its first usable frame.
There is no elapsed-time clock: a long wait adds the step's detail line, and
interrupted progress explains that the viewer is waiting for a response before
offering recovery. Selection and edge preparation
report beside their controls, not as whole-model loading.
