# Resource ownership and reuse

How the shared render code owns, shares, reuses and releases the expensive
things: component geometry, GPU buffers, BVH accelerators and decode
workers. Workspace resource reads and their cache
generations are specified in [workspace services](workspace-resources.md).

The one-sentence laws are in [the package README](../README.md#the-laws-that-live-here);
this file is the mechanism each of them constrains. Nothing here may be traded
away for speed: **reuse is an optimization, never a change in exact geometry,
persistent cache identity or occurrence identity.**

| § | Covers |
|---|---|
| [1](#1-shared-ownership-and-disposal) | A scene's sibling over its source's buffers, and who disposes |
| [2](#2-the-demand-boundary) | Raycast accelerators built only when a ray needs one |
| [3](#3-reuse-without-changing-geometry) | Recomposition and instancing |
| [4](#4-decode-workers) | The GLB and STL workers, leased by render sessions |
| [5](#5-preview-tracing) | Private path-tracing geometry, workers and GPU resources |

## 1. Shared ownership and disposal

A buffer has one owner, the scene that built it; a sibling borrows it and frees only what
is its own.

- Disposable resource admission never changes exact geometry or persistent
  cache identity.
- A scene's sibling (`createSibling()` of `lib/render/glbScene.js`,
  `lib/render/meshScene.js`, `lib/urdf/robotScene.js` and `lib/xbf/xbfScene.js`) draws over the source's
  geometry through `lib/render/sharedGeometry.js`: `shareGeometry` gives it the
  source's index and attribute objects, so one renderer drawing both scenes uploads
  each buffer once, and copies only what the sibling rewrites for itself (a robot's
  vertex colours). `releaseGeometry` lets go of the shared attributes before
  disposing, so only what the sibling owns is freed. The source stays the owner and
  is disposed after its siblings. A GLB sibling restores the authored local transforms,
  bone transforms and morph weights captured at scene creation; the editor's current
  routine, explosion and selection overlays never become Preview's baseline.
- `createCadWebGlRenderer` (`common/webglRenderer.js`) takes the `canvas` it draws
  on (an `OffscreenCanvas`), so one renderer, and one upload of each resource, can
  serve several views; disposing it and losing its context are its caller's.

## 2. The demand boundary

- Raycast accelerators may be deferred until a ray reaches a component's
  local bounds. The first ray uses exact stock intersection; surface builds
  enter a single worker queue in idle time and are shared by occurrences.
- Admission covers private position/index copies, worker scratch, and the
  result before creating an isolate. Display arrays are never transferred.
  Each worker ends with its reservation; only serialized BVH nodes and the
  indirect triangle permutation return. Byte accounting includes both the
  packed BVH nodes and that indirect permutation.
- Failed or pending builds keep stock picking. A result must match the
  geometry's attributes, arrays, versions, groups, draw range and live
  ownership; the last geometry release cancels queued or active work.

## 3. Reuse without changing geometry

### Recomposition

An engine document read again with the last version as `previous`
(`loadXbfDocument(source, { previous })`, `lib/xbf/xbfDocument.js`) keeps every part buffer
whose geometry key the new version still holds, and the geometry built over it; only the
parts the edit changed are fetched and built. Nothing of that reuse enters saved geometry or
cache identity.

### Instancing

An engine document draws one `Mesh` per instance over one shared prototype geometry
(`lib/xbf/xbfScene.js`, `lib/render/sharedGeometry.js`), with no instanced draws of its own;
a rebuild keeps the buffers and geometries whose keys it still holds, as above.

### Revision reuse

Snapshot source scopes remain isolated (`lib/renderAssetSourceScope.js`: the document
it renders). Placement and appearance belong to the document's instances.

## 4. Decode workers

The GLB and STL decode workers are one each, and render sessions lease them. Releasing
one owner never cancels another owner's work; the final release retires the worker and
its pending work.

## 5. Preview tracing

`lib/preview-tracing/progressivePreview.js` owns Preview's presentation canvas and dedicated
worker session. `previewSnapshot.js` collects admitted topology in yielded batches and copies
geometry in bounded chunks. Only private buffers and images are transferred: the editor's
geometry, materials, textures and device remain its own. Shared prototype identities, material
slots, UV channels and texture transforms survive reconstruction in the worker. Texture
reduction creates private images; it never changes source geometry or source images.

The worker owns scene reconstruction, BVH construction, packing, WebGPU tracing and OIDN.
`scene.js` retains private prototype BVHs across pose changes and builds them synchronously
inside that worker, where terminating the session can interrupt preparation. Exact repeated
prototypes share one private geometry. BVH construction reuses the transferred worker-owned
arrays; it does not copy them again. Reconstruction retains their single disposal owner.
Generated environment, floor geometry/material/alpha
map and GPU buffers belong to the worker. Private instance objects are disposed separately
from their cached prototype geometry.
The denoiser's guide scene expands those instances into ordinary meshes over the same
geometry and material slots, preserving world transforms while sharing shader programs.
Textured guide materials have private copies with UV-channel/color-space shader keys;
their textures remain borrowed. Releasing a guide scene disposes its render objects and
private material copies, never borrowed geometry, source materials or textures.

One replaceable camera/pose input and one unacknowledged image bound the worker message queue.
Pose matrices are copied together so a picture cannot combine different animation ticks.
Motion retains the last completed traced picture while a new low-resolution traced frame is
prepared; rigid pose changes refit the private scene. Scene changes cancel the old generation.
Exit aborts copying and terminates the worker without awaiting a GPU fence. Stale images are
closed, and a generation token prevents late results from attaching to a closed view.

`resourcePolicy.js` estimates peak allocation before copies and again against device limits:
source/editor reserve, private copies and BVH scratch, CPU/GPU packed scene, textures and atlas,
ray queues, render targets, denoiser networks and intermediate outputs, and presentation.
Preparation reserves the build-stage peak; rendering is admitted separately using the actual
BVH sizes, before packing and render-target allocation. Preview sessions in the same page
reserve against one policy budget. A session retains its highest reservation through quality
changes and carries forward the largest denoiser shapes, image targets and ray pools when
planning a resize, so retained buffers remain accounted for alongside the new viewport.
Exit, cancellation or failure releases the reservation. This is conservative admission, not a measurement of free VRAM;
other tabs, applications and driver allocations remain outside its knowledge. Oversized
geometry is refused rather than simplified. A worker stall, allocation error or device loss
ends the session and reaches Preview's retry/exit presentation. Terminating a worker does not
isolate the browser from a shared GPU driver failure.

The denoiser owns its auxiliary targets, inference weights and intermediate GPU buffers,
and the current and previous completed output textures. It publishes a result only when
every tile is complete. Motion, resize, scene changes and disposal cancel in-flight
inference; a request token rejects late callbacks. A queue fence separates an already
submitted inference tile from subsequent tracing. Both raw and denoised work stop while
the document is hidden. The weights are bundled data, never a remote runtime download.
An unchanged view reuses its guide targets and one MRT identity across refinement passes;
camera, scene and viewport changes invalidate the guides.
