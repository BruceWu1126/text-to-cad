import { AtlasTexture } from 'three-gpu-pathtracer/src/webgpu/AtlasTexture.js';
import { rayDataStruct, traceQueuedRayStruct, intersectionResultStruct, rayQueueStruct, pixelQueueStruct } from 'three-gpu-pathtracer/src/webgpu/compute/wavefront/structs.js';
import { materialStruct } from 'three-gpu-pathtracer/src/webgpu/nodes/structs.wgsl.js';

const MiB = 1024 * 1024;
export const PREVIEW_RESOURCE_LIMITS = Object.freeze({ budgetBytes: 1024 * MiB, driverReserveBytes: 128 * MiB,
  bindingBytes: 256 * MiB, maxPixels: 1000000, minPixels: 16384, maxTextureBytes: 64 * MiB, maxTextureSide: 2048,
  maxObjects: 20000, maxTransforms: 20000, maxPrimitives: 100000, maxTextureSources: 1024 });
export const PREVIEW_ALLOCATION_LAYOUT = Object.freeze({
  rayState: rayDataStruct.getLength() * 4, queuedRay: traceQueuedRayStruct.getLength() * 4,
  intersection: intersectionResultStruct.getLength() * 4, queueHeader: rayQueueStruct.getLength() * 4,
  pixelHeader: pixelQueueStruct.getLength() * 4, material: materialStruct.getLength() * 4,
});
// oidn-webgpu 0.6.0 NativeUNetCore + the bundled rt_hdr_calb_cnrm.tza,
// recorded without executing GPU work (RecordingDevice, precision fp32). These
// Output tiles carry a 96-pixel halo per side (model receptive field 174).
// Totals include three RGBA inputs; weights are shared across shape plans.
// NativeUNetCore retains two shapes; a third can coexist until its queue fence.
export const PREVIEW_OIDN_PROFILE = Object.freeze({ archiveBytes: 1841160, weightsBytes: 3690768,
  shapesAtPeak: 3, networks: 2, tiles: Object.freeze({
    64: Object.freeze({ inputSide: 256, totalBytes: 46797728, largestBuffer: 16777216 }),
    128: Object.freeze({ inputSide: 320, totalBytes: 71045024, largestBuffer: 26214400 }),
    256: Object.freeze({ inputSide: 448, totalBytes: 135704480, largestBuffer: 51380224 }),
  }) });

const count = value => Number.isFinite(value) && value >= 0 ? value : Infinity;
const entriesOf = textures => textures instanceof Map ? [...textures.entries()]
  : (textures || []).map((texture, index) => [texture.key ?? String(index), texture]);
const sizeOf = value => value?.size ?? value?.length ?? value ?? 0;

export function estimatePreviewAtlas(textures) {
  const items = entriesOf(textures).map(([key, texture]) => ({ width: texture.width, height: texture.height,
    source: { uuid: String(key) } }));
  items.push({ width: 128, height: 128, source: { uuid: 'preview-floor-alpha' } });
  items.sort((a, b) => a.source.uuid.localeCompare(b.source.uuid));
  const side = Math.max(...items.map(item => Math.max(item.width, item.height)));
  if (!Number.isSafeInteger(side) || side <= 0) return { side: Infinity, pages: Infinity, bytes: Infinity };
  const atlas = new AtlasTexture();
  try {
    const pages = atlas._packTextures(items, side, side).pageCount;
    return { side, pages, bytes: side * side * pages * 4 };
  } finally { atlas.dispose(); }
}

// Plans decoded images only: the serializer creates independent resized images
// AFTER admission. UVs, channels, texture transforms, color spaces and material
// values are preserved, and editor-owned images are never resized in place.
export function planPreviewTextures(admission, { maxSide = 1024, maxAtlasBytes = PREVIEW_RESOURCE_LIMITS.maxTextureBytes,
  deviceLimits = {} } = {}) {
  const source = entriesOf(admission.textures);
  const sideLimit = Math.min(maxSide, deviceLimits.maxTextureDimension2D ?? 8192);
  for (let side = sideLimit; side >= 32; side = Math.floor(side / 2)) {
    const textures = source.map(([key, texture]) => {
      const scale = Math.min(1, side / Math.max(texture.width, texture.height));
      return { key, sourceWidth: texture.width, sourceHeight: texture.height,
        bytesPerPixel: texture.bytesPerPixel ?? 4,
        width: Math.max(1, Math.floor(texture.width * scale)), height: Math.max(1, Math.floor(texture.height * scale)) };
    });
    if (textures.some(texture => !Number.isSafeInteger(texture.width) || !Number.isSafeInteger(texture.height))) {
      return { ok: false, reason: 'Preview texture dimensions are invalid', textures: [] };
    }
    const atlas = estimatePreviewAtlas(textures);
    if (atlas.side > (deviceLimits.maxTextureDimension2D ?? 8192)) continue;
    if (atlas.pages > (deviceLimits.maxTextureArrayLayers ?? 256)) continue;
    if (atlas.bytes <= maxAtlasBytes) return { ok: true, textures, atlas,
      bytes: textures.reduce((sum, texture) => sum + texture.width * texture.height * texture.bytesPerPixel, 0) };
  }
  return { ok: false, reason: 'Preview textures exceed the device or memory budget', textures: [] };
}

export function previewQualityProfile(performance = {}, viewport = {}, effectiveValue = performance.mode === 'manual' ? (performance.value ?? 50) : 100) {
  const value = Math.max(0, Math.min(100, Number(effectiveValue) || 0)), q = value / 100;
  const width = Math.max(1, Math.floor(Number(viewport.width) || 1));
  const height = Math.max(1, Math.floor(Number(viewport.height) || 1));
  const maxPixels = Math.round(PREVIEW_RESOURCE_LIMITS.minPixels + (PREVIEW_RESOURCE_LIMITS.maxPixels - PREVIEW_RESOURCE_LIMITS.minPixels) * q * q);
  const scale = Math.min(1, Math.sqrt(maxPixels / (width * height)));
  return withPreviewRayBudget({ mode: performance.mode === 'manual' ? 'manual' : 'auto', requestedValue: performance.value ?? 50,
    effectiveValue: value, width: Math.max(1, Math.floor(width * scale)), height: Math.max(1, Math.floor(height * scale)),
    frameBudget: Math.round(4096 + 61440 * q * q), samples: performance.mode === 'manual' ? Math.round((32 + 224 * q * q) / 8) * 8 : 128,
    firstDenoiseSamples: performance.mode === 'manual' ? Math.round(4 + 12 * q) : 32, denoiseIntervalSamples: Math.round(8 + 24 * q),
    denoiseTileSize: 64, textureSide: 2048,
    maxOutstandingBatches: 1, targetBatchMs: 12 });
}

// Three's guide raster pass uploads ordinary attributes in addition to the
// path tracer's packed storage buffers. Its WebGPU backend expands unnormalized
// 8/16-bit integers to 32-bit and aligns vertex strides to four bytes.
export function estimatePreviewGuideInputs(admission, texturePlan) {
  let geometryBytes = 0;
  if (admission.geometries && typeof admission.geometries.values === 'function') {
    for (const geometry of admission.geometries.values()) {
      for (const attribute of Object.values(geometry.attributes)) {
        const array = attribute.array;
        const expand = !attribute.normalized && [Int8Array, Uint8Array, Int16Array, Uint16Array].includes(array.constructor);
        const stride = attribute.itemSize * (expand ? 4 : array.BYTES_PER_ELEMENT);
        geometryBytes += attribute.count * Math.ceil(stride / 4) * 4;
      }
      if (geometry.index) geometryBytes += geometry.index.count * 4;
    }
  } else geometryBytes = count(admission.cloneBytes) * 4;
  let textureBytes = 0;
  for (const texture of texturePlan.textures || []) {
    let { width, height } = texture;
    for (;;) {
      textureBytes += width * height * texture.bytesPerPixel;
      if (width === 1 && height === 1) break;
      width = Math.max(1, Math.floor(width / 2)); height = Math.max(1, Math.floor(height / 2));
    }
  }
  return { geometryBytes, textureBytes: texturePlan.textures ? textureBytes : Math.ceil(texturePlan.bytes * 2) };
}

// Recorded aligned rectangular allocations for this bundled model. fp16 is
// selected only when the actual OIDN device supports shader-f16, matching its
// existing precision:auto behavior. Input tiles are clipped to the image.
export function estimatePreviewInference(profile, shaderF16 = false, retainedBounds = null) {
  const tile = PREVIEW_OIDN_PROFILE.tiles[profile.denoiseTileSize];
  const width = Math.max(retainedBounds?.inputWidth ?? 0, Math.min(tile.inputSide, Math.ceil(profile.width / 16) * 16));
  const height = Math.max(retainedBounds?.inputHeight ?? 0, Math.min(tile.inputSide, Math.ceil(profile.height / 16) * 16));
  const weightsBytes = shaderF16 ? 2291984 : PREVIEW_OIDN_PROFILE.weightsBytes;
  const shapeBytes = width * height * (shaderF16 ? 352.875 : 657.75) + 656;
  // Three shapes bound two cached plans and an evicted plan still fenced on GPU.
  return { bytes: 2 * (weightsBytes + PREVIEW_OIDN_PROFILE.shapesAtPeak * shapeBytes),
    largestBuffer: Math.max(shaderF16 ? 473088 : 645120, width * height * (shaderF16 ? 128 : 256)),
    inputWidth: width, inputHeight: height, shapesAtPeak: 3, precision: shaderF16 ? 'fp16' : 'fp32' };
}

export function estimatePreviewResources({ admission, viewport, profile, texturePlan, packedNodes = null,
  retainedBytes = 0, driverReserveBytes = PREVIEW_RESOURCE_LIMITS.driverReserveBytes, ownedGeometry = false, shaderF16 = false, inferenceBounds = null }) {
  const vertices = count(admission.vertices) + 4, triangles = count(admission.triangles) + 2;
  const materials = sizeOf(admission.materials ?? admission.materialCount) + 1;
  const transforms = count(packedNodes?.transforms ?? admission.groups) + 1;
  const uvCount = sizeOf(admission.uvChannels ?? admission.uvCount ?? 1);
  const sourceGeometry = count(admission.sourceGeometryBytes ?? admission.cloneBytes);
  const privateGeometry = count(admission.cloneBytes);
  const sourceTextureBytes = admission.sourceTextureBytes ?? entriesOf(admission.textures).reduce((sum, [, texture]) => sum + texture.width * texture.height * (texture.bytesPerPixel ?? 4), 0);
  // Source decoded pixels plus their editor GPU upload/mips. This is a reserve,
  // not a query of resident assets or available VRAM. A mip chain can approach
  // twice its base size for 1-by-N images. Driver allocations remain opaque.
  // Heap layouts vary by engine. Explicitly reserve source, serialized and worker
  // hierarchy records, plus material/geometry descriptors, before any copies.
  const objectCount = count(admission.objectCount ?? admission.meshes?.length ?? admission.groups);
  const geometryCount = sizeOf(admission.geometries ?? admission.geometryCount);
  const metadataBytes = objectCount * 4096 * 3 + materials * 4096 * 2 + geometryCount * 1024 * 2 + count(admission.groups) * 64;
  const sourceReserve = metadataBytes + sourceGeometry * 2 + sourceTextureBytes * 3
    + count(viewport.width) * count(viewport.height) * 32 + driverReserveBytes;
  const sourceReserveBytes = Math.ceil(sourceReserve);
  const nodeBytes = packedNodes?.nodeBytes ?? triangles * 64;
  const packed = { vertices: vertices * (64 + uvCount * 16), indices: triangles * 12,
    materials: materials * PREVIEW_ALLOCATION_LAYOUT.material, transforms: transforms * 160, nodes: nodeBytes + 256 };
  const packedBytes = Object.values(packed).reduce((sum, value) => sum + value, 0);
  // Networks and guide targets survive a quality/viewport resize. Retain their
  // componentwise high-water bounds alongside the NEW editor viewport reserve.
  const pixels = Math.max(profile.width * profile.height, inferenceBounds?.targetPixels ?? 0);
  const raySlots = Math.max(profile.frameBudget, inferenceBounds?.frameBudget ?? 0);
  const slots = Math.min(pixels, raySlots);
  const ray = PREVIEW_ALLOCATION_LAYOUT;
  const rayBuffers = { state: slots * ray.rayState, queue: slots * ray.queuedRay + ray.queueHeader,
    intersections: slots * ray.intersection, pixels: pixels * 4 + ray.pixelHeader };
  const rayBytes = rayBuffers.state + 2 * rayBuffers.queue + 2 * rayBuffers.intersections + rayBuffers.pixels;
  const oidn = PREVIEW_OIDN_PROFILE;
  const inference = { ...estimatePreviewInference(profile, shaderF16, inferenceBounds), targetPixels: pixels, frameBudget: raySlots };
  const inferenceBytes = inference.bytes;
  const geometryCopies = ownedGeometry ? 1 : 2;
  // Two RGBA32f accumulations, R32u samples, a retained low-res texture; the
  // guide MRT has two RGBA8 attachments+4xMSAA+depth. OIDN keeps two full-image
  // outputs per network and current/previous RGBA16f publications. Presentation
  // reserves worker/main RGBA canvases and copies. Include all at full size.
  // Motion's single-pass filter has one RGBA16f output (8 B/pixel). Keep a
  // second output allowance for resize retirement, in addition to the existing
  // neural publication/resize reserve; neither renderer borrows the other's slack.
  const targets = { tracing: pixels * 52, guides: pixels * 56, denoising: pixels * 80,
    motionFiltering: pixels * 16, presentation: pixels * 16 };
  const targetBytes = Object.values(targets).reduce((sum, value) => sum + value, 0);
  const atlasBytes = texturePlan.atlas.bytes;
  const guideInputs = estimatePreviewGuideInputs(admission, texturePlan);
  const convertedAttributeBytes = Math.max(0, guideInputs.geometryBytes - privateGeometry);
  const cpuBytes = privateGeometry * geometryCopies + convertedAttributeBytes + nodeBytes + packedBytes + 2 * oidn.archiveBytes;
  const environmentBytes = 512 * 256 * 60 + 256 * 12;
  const gpuBytes = packedBytes + atlasBytes + guideInputs.geometryBytes + guideInputs.textureBytes + 2 * rayBytes + targetBytes + inferenceBytes + environmentBytes;
  const buildBytes = sourceReserveBytes + privateGeometry * geometryCopies + nodeBytes + triangles * 24 + texturePlan.bytes + environmentBytes + retainedBytes;
  const renderBytes = sourceReserveBytes + cpuBytes + gpuBytes + texturePlan.bytes + retainedBytes;
  return { peakBytes: Math.max(buildBytes, renderBytes), buildBytes, renderBytes, sourceReserveBytes,
    cpuBytes, gpuBytes, retainedBytes, metadataBytes, guideInputs, convertedAttributeBytes, packed, rayBuffers, targets, inferenceBytes,
    atlas: texturePlan.atlas, environmentBytes, inference, geometryCopies, largestInferenceBuffer: inference.largestBuffer,
    estimated: true, freeVramKnown: false };
}

// The total ceiling is an application policy, NOT available/free VRAM. Limits
// constrain each binding independently; quality reductions may only change work
// and raster/image precision, never geometry, transport or authored materials.
export function planPreviewResources({ admission, viewport = { width: 1, height: 1 }, performance = {},
  deviceLimits = {}, budgetBytes = PREVIEW_RESOURCE_LIMITS.budgetBytes, packedNodes = null, retainedBytes = 0, phase = 'render', denoiseTileSize = null, ownedGeometry = false, inferenceBounds = null } = {}) {
  if (!admission || admission.reason) return { ok: false, reason: admission?.reason || 'Preview scene is unavailable' };
  if (packedNodes?.reason) return { ok: false, reason: packedNodes.reason };
  if (count(admission.objectCount ?? admission.meshes?.length ?? admission.groups) > PREVIEW_RESOURCE_LIMITS.maxObjects
    || count(admission.groups) > PREVIEW_RESOURCE_LIMITS.maxTransforms
    || count(packedNodes?.transforms ?? admission.groups) > PREVIEW_RESOURCE_LIMITS.maxTransforms
    || count(packedNodes?.primitives ?? 0) > PREVIEW_RESOURCE_LIMITS.maxPrimitives
    || entriesOf(admission.textures).length > PREVIEW_RESOURCE_LIMITS.maxTextureSources) {
    return { ok: false, reason: 'Preview scene exceeds its object or placement budget' };
  }
  const desired = Number(performance.value ?? 50);
  if (!Number.isFinite(desired) || desired < 0 || desired > 100) return { ok: false, reason: "Preview desired quality must be between 0 and 100" };
  const requested = performance.mode === 'manual' ? desired : 100;
  // Texture detail is fixed for the session, so every safety profile uses this
  // same atlas. Avoid repeating skyline packing while lowering render targets.
  const texturePlan = planPreviewTextures(admission, { maxSide: PREVIEW_RESOURCE_LIMITS.maxTextureSide, deviceLimits });
  if (!texturePlan.ok) return { ok: false, reason: texturePlan.reason };
  let last = null;
  for (let value = requested; value >= 0; value = value === 0 ? -1 : Math.max(0, value - 10)) {
    let profile = previewQualityProfile(performance, viewport, value);
    const binding = Math.min(PREVIEW_RESOURCE_LIMITS.bindingBytes,
      deviceLimits.maxStorageBufferBindingSize ?? 128 * MiB, deviceLimits.maxBufferSize ?? 256 * MiB);
    profile = withPreviewRayBudget(profile, Math.min(profile.frameBudget, Math.floor(binding / PREVIEW_ALLOCATION_LAYOUT.rayState)));
    // Build admission covers the real copies, scratch and worst private BVH only.
    // Packed GPU buffers and networks are forbidden until a second admission
    // measures the actual completed BVH. Try larger output tiles only at this
    // same settled detail before considering a lower resource profile.
    const tileSizes = denoiseTileSize === null ? [256, 128, 64] : [denoiseTileSize];
    for (const tileSize of tileSizes) {
      if (!PREVIEW_OIDN_PROFILE.tiles[tileSize]) return { ok: false, reason: 'Invalid Preview denoising tile size' };
      profile = { ...profile, denoiseTileSize: tileSize };
      const estimates = estimatePreviewResources({ admission, viewport, profile, texturePlan, packedNodes, retainedBytes, ownedGeometry, shaderF16: deviceLimits.shaderF16 === true, inferenceBounds });
      const memory = { ...estimates, phase, peakBytes: phase === 'build' ? estimates.buildBytes : estimates.peakBytes };
      const dimension = deviceLimits.maxTextureDimension2D ?? 8192;
      const largest = Math.max(...Object.values(memory.packed), ...Object.values(memory.rayBuffers),
        memory.largestInferenceBuffer, profile.width * profile.height * 16);
      let reason = null;
      if (!Number.isFinite(memory.peakBytes) || !Number.isSafeInteger(memory.peakBytes) || !(budgetBytes > 0)) reason = 'Preview resource estimate is invalid';
      else if (phase !== 'build' && largest > binding) reason = 'Preview scene exceeds a device storage buffer limit';
      else if (profile.width > dimension || profile.height > dimension) reason = 'Preview image exceeds a device texture limit';
      else if (memory.peakBytes > budgetBytes) reason = `Preview estimated ${phase} peak memory exceeds its resource budget (${Math.ceil(memory.peakBytes / MiB)} MiB required, ${Math.floor(budgetBytes / MiB)} MiB allowed)`;
      last = { ok: !reason, reason, profile, memory: { ...memory, budgetBytes }, texturePlan: texturePlan.textures };
      if (!reason) return last;
    }
  }
  return last;
}

// Image dimensions are independent of the path pool. The upstream pixel queue
// schedules overflow pixels over later bounded submissions at the same pose.
export function withPreviewRayBudget(profile, requested = profile.frameBudget) {
  return { ...profile, frameBudget: Math.max(1, Math.floor(requested)),
    motionScale: 1, motionWidth: profile.width, motionHeight: profile.height };
}

// A page-local Preview-only reservation ledger. It coordinates the estimates of
// mounted views in this JavaScript realm; other tabs/apps and real free VRAM are
// deliberately unknown. Reserve before copying or creating a worker.
export function createPreviewResourcePool({ budgetBytes = PREVIEW_RESOURCE_LIMITS.budgetBytes } = {}) {
  let reservedBytes = 0;
  return {
    get reservedBytes() { return reservedBytes; },
    get availableBytes() { return budgetBytes - reservedBytes; },
    reserve(bytes) {
      if (!Number.isSafeInteger(bytes) || bytes <= 0 || bytes > budgetBytes - reservedBytes) return null;
      let held = bytes;
      reservedBytes += held;
      return {
        get bytes() { return held; },
        resize(next) {
          if (!held || !Number.isSafeInteger(next) || next <= 0 || next - held > budgetBytes - reservedBytes) return false;
          reservedBytes += next - held; held = next; return true;
        },
        release() { reservedBytes -= held; held = 0; },
      };
    },
  };
}
export const previewResourcePool = createPreviewResourcePool();

// Keep these original-source counters with the worker payload. Replanning from
// resized worker images must not pretend the editor released its full textures.
export function previewSourceStats(admission) {
  return { sourceGeometryBytes: admission.sourceGeometryBytes ?? admission.cloneBytes,
    sourceTextureBytes: admission.sourceTextureBytes ?? entriesOf(admission.textures).reduce((sum, [, texture]) =>
      sum + texture.width * texture.height * (texture.bytesPerPixel ?? 4), 0) };
}

export function reservePreviewResources(plan, { lease = null, pool = previewResourcePool } = {}) {
  if (!plan?.ok) return null;
  const bytes = plan.memory.peakBytes;
  return lease ? (lease.resize(bytes) ? lease : null) : pool.reserve(bytes);
}
