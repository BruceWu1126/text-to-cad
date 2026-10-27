import * as THREE from 'three';
import { SAH } from 'three-mesh-bvh';
import { GenerateMeshBVHWorker } from 'three-mesh-bvh/worker';
import { BVHTraversalHelper } from 'three-mesh-bvh/src/core/BVHTraversalHelper.js';
import { createStudioEnvironmentTexture } from '../../common/environmentMap.js';
import { planPreviewTextures } from './resourcePolicy.js';
import { isSharedGeometry } from '../render/sharedGeometry.js';

// Bound the synchronous top-level build and memory before copying any buffers.
export const PREVIEW_LIMITS = Object.freeze({
  bindingBytes: 256 * 1024 * 1024,
  sceneBytes: 512 * 1024 * 1024,
  transforms: 20000,
  primitives: 100000,
  texturePixels: 4194304,
  textureSources: 1024,
  textureSide: 2048,
});

const FLOOR_ALPHA_SOURCE_ID = 'preview-floor-alpha';

const UV_TEXTURES = Object.freeze([
  'map', 'metalnessMap', 'roughnessMap', 'transmissionMap', 'emissiveMap', 'normalMap',
  'clearcoatMap', 'clearcoatNormalMap', 'clearcoatRoughnessMap', 'iridescenceMap',
  'iridescenceThicknessMap', 'specularColorMap', 'specularIntensityMap', 'alphaMap',
  'sheenColorMap', 'sheenRoughnessMap', 'anisotropyMap',
]);

function unsupportedSurfaceMapping(material, geometry) {
  const uv = geometry.getAttribute('uv'), tangent = geometry.getAttribute('tangent');
  if (material.normalMap && material.normalMapType !== THREE.TangentSpaceNormalMap) return true;
  if ((material.normalMap || material.clearcoatNormalMap) && (!tangent || !uv)) return true;
  // The raster derives an oriented tangent from UV derivatives when none was authored;
  // the tracer cannot reproduce that basis. Untextured CAD's normal-based basis is shared.
  if (material.anisotropy > 0 && uv && !tangent) return true;
  return UV_TEXTURES.some(name => {
    const texture = material[name];
    if (!texture) return false;
    const channel = texture.channel;
    return !Number.isInteger(channel) || channel < 0 || channel > 7
      || !geometry.getAttribute(channel === 0 ? 'uv' : `uv${channel}`);
  });
}

// GLB Preview owns a lightweight wrapper per mesh, while those wrappers share
// the exact source attributes and index. Canonicalize only such wrappers whose
// complete topology and attribute identities agree; locally rewritten attributes
// or groups therefore remain separate private prototypes.
export function canonicalPreviewGeometries(meshes) {
  const keys = new Map(), canonical = new Map(), identities = new WeakMap();
  let next = 1;
  const id = object => {
    if (!object) return 0;
    if (!identities.has(object)) identities.set(object, next++);
    return identities.get(object);
  };
  for (const mesh of meshes) {
    const geometry = mesh.geometry;
    if (canonical.has(geometry)) continue;
    if (!isSharedGeometry(geometry)) { canonical.set(geometry, geometry); continue; }
    const attributes = Object.entries(geometry.attributes).sort(([a], [b]) => a.localeCompare(b)).map(([name, attr]) => [name, id(attr)]);
    const morphs = Object.entries(geometry.morphAttributes || {}).sort(([a], [b]) => a.localeCompare(b))
      .map(([name, list]) => [name, list.map(id)]);
    const key = JSON.stringify([id(geometry.index), attributes, morphs, geometry.morphTargetsRelative, geometry.groups, geometry.drawRange]);
    const first = keys.get(key) || geometry;
    keys.set(key, first);
    canonical.set(geometry, first);
  }
  return canonical;
}

// The tracer maps BVH root n to material group n. Overlap, unsorted ranges
// or gaps on a single-material mesh would otherwise change what gets drawn.
function validMaterialGroups(geometry, material) {
  const groups = geometry.groups, total = geometry.index?.count ?? geometry.getAttribute('position').count;
  if (!groups.length) return !Array.isArray(material);
  let end = 0;
  for (const group of groups) {
    if (!Number.isInteger(group.start) || !Number.isInteger(group.count) || group.start < end ||
        group.start % 3 !== 0 || group.count <= 0 || group.count % 3 !== 0 || group.start + group.count > total) return false;
    if (Array.isArray(material) && !material[group.materialIndex]) return false;
    if (!Array.isArray(material) && group.start !== end) return false;
    end = group.start + group.count;
  }
  return Array.isArray(material) || end === total;
}

function collapsedTransform(matrix) {
  const e = matrix.elements;
  return [0, 1, 2, 4, 5, 6, 8, 9, 10].every(index => e[index] === 0);
}

export function inspectPreviewScene(root, limits = PREVIEW_LIMITS) {
  const geometries = new Set(), materials = new Set(), textures = new Map(), meshes = [];
  // The studio floor's plane has uv even when the model has none. The tracer
  // packs the union of channels across the whole scene into every vertex.
  const uvChannels = new Set(['uv']);
  let triangles = 0, vertices = 0, groups = 0, texturePixels = 0, cloneBytes = 0, collapsed = 0, reason = null;
  root.updateMatrixWorld(true);
  root.traverseVisible(object => {
    if (reason) return;
    if (object.isLine || object.isPoints || object.isSprite) { reason = 'unsupported primitive'; return; }
    if (!object.isMesh) return;
    if (collapsedTransform(object.matrixWorld)) { collapsed++; return; }
    if (!Number.isFinite(object.matrixWorld.determinant()) || object.matrixWorld.determinant() === 0) { reason = 'singular transform'; return; }
    if (object.isSkinnedMesh || object.isInstancedMesh || object.morphTargetInfluences?.length) { reason = 'animated geometry'; return; }
    const geometry = object.geometry, position = geometry?.getAttribute('position');
    if (!position || !geometry.getAttribute('normal') || Object.values(geometry.attributes).some(attr => attr.isInterleavedBufferAttribute) || geometry.index?.isInterleavedBufferAttribute ||
        geometry.drawRange.start !== 0 || Number.isFinite(geometry.drawRange.count)) { reason = 'unsupported geometry'; return; }
    const slots = Array.isArray(object.material) ? object.material : [object.material];
    if (slots.some(material => !material || !(material.isMeshStandardMaterial || material.isMeshPhysicalMaterial) ||
      material.visible === false || material.clippingPlanes?.length || material.displacementMap || material.bumpMap ||
      Object.values(material).some(value => value?.isTexture && (value.isRenderTargetTexture || value.isVideoTexture)) ||
      unsupportedSurfaceMapping(material, geometry))) { reason = 'unsupported material'; return; }
    if (!validMaterialGroups(geometry, object.material)) { reason = 'invalid material groups'; return; }
    slots.forEach(material => {
      materials.add(material);
      for (const texture of Object.values(material)) {
        if (!texture?.isTexture) continue;
        const textureKey = `${texture.source?.uuid}:${texture.colorSpace}`;
        if (textures.has(textureKey)) continue;
        const image = texture.source?.data;
        const width = image?.naturalWidth || image?.width, height = image?.naturalHeight || image?.height;
        if (!Number.isSafeInteger(width) || !Number.isSafeInteger(height) || width <= 0 || height <= 0) { reason = 'texture budget'; continue; }
        const bytesPerPixel = image.data?.byteLength ? image.data.byteLength / (width * height) : 4;
        if (!Number.isFinite(bytesPerPixel) || bytesPerPixel <= 0 || texture.isCompressedTexture) { reason = 'unsupported texture'; continue; }
        textures.set(textureKey, { width, height, bytesPerPixel, source: texture.source });
        texturePixels += width * height;
      }
    });
    meshes.push(object);
    groups += Math.max(geometry.groups.length, 1);

  });
  if (!reason && !meshes.length && !collapsed) reason = 'empty scene';
  const canonical = canonicalPreviewGeometries(meshes);
  for (const geometry of canonical.values()) {
    if (geometries.has(geometry)) continue;
    geometries.add(geometry);
    const position = geometry.getAttribute('position');
    triangles += (geometry.index?.count ?? position.count) / 3;
    vertices += position.count;
    for (const [name, attr] of Object.entries(geometry.attributes)) {
      if (/^uv(?:[1-7])?$/.test(name)) uvChannels.add(name);
      cloneBytes += attr.array.byteLength;
    }
    if (geometry.index) cloneBytes += geometry.index.array.byteLength;
  }
  const admission = { meshes, geometries, canonical, materials, textures, triangles, vertices, groups, texturePixels, cloneBytes, uvChannels, reason };
  if (!reason && ((limits.triangles != null && triangles > limits.triangles) || (limits.vertices != null && vertices > limits.vertices) ||
      (limits.meshes != null && meshes.length > limits.meshes) || (limits.groups != null && groups > limits.groups) ||
      (limits.materials != null && materials.size > limits.materials) || textures.size > (limits.textureSources ?? PREVIEW_LIMITS.textureSources))) admission.reason = 'scene budget';
  if (!admission.reason) admission.reason = previewSceneBudget(admission, limits);
  return admission;
}

// The tracer packs each unique prototype once: four vec4 vertex fields plus
// one vec4 per UV channel, uint32 triangle indices, one 160-byte transform per
// placement/BVH root and 1104 bytes per material. Account for the private CPU
// copies and peak CPU+GPU packed buffers, not a flat number of placements.
// Use the tracer's own skyline allocator for a CPU-only estimate. The floor
// alpha map uses a stable private source ID, so its atlas ordering is the same
// here as in the prepared scene. Budget the larger of enabled/disabled floor.

export function previewSceneBudget(admission, limits = PREVIEW_LIMITS, deviceLimits = null, packedNodes = null) {
  const binding = Math.min(limits.bindingBytes ?? PREVIEW_LIMITS.bindingBytes,
    deviceLimits?.maxStorageBufferBindingSize ?? Infinity, deviceLimits?.maxBufferSize ?? Infinity);
  const vertexBytes = (admission.vertices + 4) * (64 + 16 * admission.uvChannels.size);
  const indexBytes = (admission.triangles + 2) * 12;
  const materialBytes = (admission.materials.size + 1) * 1104;
  const transformBytes = (admission.groups + 1) * 160;
  const nodeBytes = (packedNodes?.nodeBytes ?? 0) + 256;
  // Include atlas page waste as well as private CPU copies and packed buffers.
  const texturePlan = planPreviewTextures(admission, { maxSide: limits.textureSide ?? PREVIEW_LIMITS.textureSide,
    maxAtlasBytes: limits.sceneBytes ?? PREVIEW_LIMITS.sceneBytes, deviceLimits: deviceLimits || {} });
  if (!texturePlan.ok) return 'scene budget';
  const atlas = texturePlan.atlas;
  const atlasBytes = atlas.bytes;
  const packedBytes = vertexBytes + indexBytes + materialBytes + transformBytes + nodeBytes;
  const peakBytes = admission.cloneBytes + 2 * packedBytes + atlasBytes;
  // Before worker builds we do not know actual BVH node counts. Bound the
  // worst-case private CPU build separately, then check actual packed nodes
  // against the device and combined CPU/GPU budget after the workers finish.
  const worstPrivateBytes = admission.cloneBytes + admission.triangles * 64;
  if (worstPrivateBytes > (limits.sceneBytes ?? PREVIEW_LIMITS.sceneBytes) ||
      atlas.pages > (deviceLimits?.maxTextureArrayLayers ?? Infinity) ||
      atlas.side > (deviceLimits?.maxTextureDimension2D ?? Infinity) ||
      admission.groups > (limits.transforms ?? PREVIEW_LIMITS.transforms) ||
      (packedNodes && packedNodes.primitives > (limits.primitives ?? PREVIEW_LIMITS.primitives)) ||
      Math.max(vertexBytes, indexBytes, materialBytes, transformBytes, nodeBytes) > binding ||
      peakBytes > (limits.sceneBytes ?? PREVIEW_LIMITS.sceneBytes)) return 'scene budget';
  return null;
}

// Match three-mesh-bvh's ClusteredBVH cut at <64 triangles. This is evaluated
// once per private prototype after its worker-built BVH exists, before setScene
// can synchronously allocate the TLAS. Instanced batches need only one primitive
// per BVH root and placement; a singleton mesh uses its cluster cut points.
function countClusters(bvh) {
  let count = 0;
  for (let root = 0; root < bvh._roots.length; root++) {
    BVHTraversalHelper.setBVH(bvh, root);
    try {
      BVHTraversalHelper.traverseBuffer((_depth, isLeaf, node) => {
        const size = BVHTraversalHelper.getRangeEnd(node) - BVHTraversalHelper.getRangeStart(node);
        if (size < 64 || isLeaf) { count++; return true; }
        return false;
      });
    } finally { BVHTraversalHelper.reset(); }
  }
  return count;
}

export function previewPreparedAllocation(groups, geometries) {
  let blasBytes = 0, primitives = 0;
  const clusters = new Map();
  for (const geometry of geometries.values()) {
    const bvh = geometry.boundsTree;
    // The tracer packs the root index into seven bits.
    if (bvh._roots.length > 128) return { reason: 'scene budget' };
    blasBytes += bvh._roots.reduce((sum, root) => sum + root.byteLength, 0);
  }
  for (const group of groups) {
    const bvh = geometries.get(group.geometry).boundsTree;
    if (group.instancingSafe) primitives += group.meshes.length * bvh._roots.length;
    else {
      if (!clusters.has(bvh)) clusters.set(bvh, countClusters(bvh));
      primitives += clusters.get(bvh);
    }
  }
  // A binary TLAS has fewer than 2 nodes per primitive (32 bytes/node).
  return { primitives, transforms: groups.reduce((sum, group) => sum + group.meshes.length * geometries.get(group.geometry).boundsTree._roots.length, 0),
    nodeBytes: blasBytes + primitives * 64 };
}

export function previewPreparedBudget(admission, groups, geometries, deviceLimits) {
  const allocation = previewPreparedAllocation(groups, geometries);
  return allocation.reason || previewSceneBudget(admission, PREVIEW_LIMITS, deviceLimits, allocation);
}

// Cache only complete private BVHs. A look/pose change reuses each prototype, while a
// changed buffer or group invalidates it. Eviction never touches source geometry.
export function createPreviewGeometryCache() {
  const entries = new Map(), identities = new WeakMap();
  let nextIdentity = 1;
  const identity = value => { if (!value) return null; if (!identities.has(value)) identities.set(value, nextIdentity++); return identities.get(value); };
  const signature = geometry => JSON.stringify([
    identity(geometry.index), identity(geometry.index?.array), geometry.index?.version, geometry.index?.count,
    Object.entries(geometry.attributes).map(([name, attr]) => [name, identity(attr), identity(attr.array), attr.version, attr.count]),
    geometry.groups, geometry.drawRange,
  ]);
  return {
    get(source) {
      const entry = entries.get(source);
      if (!entry) return null;
      if (entry.signature === signature(source)) return entry.geometry;
      if (entry.owned) entry.geometry.dispose(); entries.delete(source); return null;
    },
    set(source, geometry, { owned = true } = {}) { entries.set(source, { geometry, owned, signature: signature(source) }); },
    retain(sources) { for (const [source, entry] of entries) if (!sources.has(source)) { if (entry.owned) entry.geometry.dispose(); entries.delete(source); } },
    dispose() { for (const entry of entries.values()) if (entry.owned) entry.geometry.dispose(); entries.clear(); },
  };
}

// The raster floor is a display-colored sweep: most of its brightness is emissive,
// and only a quarter is diffuse. Those weights are presentation shaders, not a physical
// surface. Transport must see the configured albedo and a non-emitting floor so contact
// shadows, reflected parts and indirect illumination can form naturally.
export function createPreviewFloorMaterial(source, configuration = {}) {
  const material = source.clone();
  if (configuration.backdrop?.groundColor) material.color.set(configuration.backdrop.groundColor);
  material.emissive.set(0x000000);
  material.emissiveIntensity = 0;
  material.emissiveMap = null;
  material.envMapIntensity = 1;
  return material;
}

// Batch only exact prototype/material-slot identities. The tracer's InstancedMesh
// acceleration structure references each BLAS root once per placement instead of
// splitting every repeated mesh into hundreds of top-level clusters. Mirrored
// matrices stay as ordinary meshes: Three's guide-buffer raster does not support
// negatively scaled or sheared instances.
function hasShear(matrix) {
  const e = matrix.elements;
  const x = new THREE.Vector3(e[0], e[1], e[2]);
  const y = new THREE.Vector3(e[4], e[5], e[6]);
  const z = new THREE.Vector3(e[8], e[9], e[10]);
  const epsilon = 1e-6;
  return Math.abs(x.dot(y)) > epsilon * x.length() * y.length()
    || Math.abs(x.dot(z)) > epsilon * x.length() * z.length()
    || Math.abs(y.dot(z)) > epsilon * y.length() * z.length();
}

export function groupPreviewMeshes(meshes, canonical = new Map()) {
  const groups = new Map(), result = [], identities = new WeakMap();
  let nextIdentity = 1;
  const identity = material => {
    if (!identities.has(material)) identities.set(material, nextIdentity++);
    return identities.get(material);
  };
  for (const mesh of meshes) {
    const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    const geometry = canonical.get(mesh.geometry) || mesh.geometry;
    const key = `${Array.isArray(mesh.material) ? 'array' : 'single'}:${materials.map(identity).join(',')}`;
    // Three's WebGPU guide pass transforms instance normals but not the
    // tangent basis used for tangent-space normal maps. Keep those unbatched.
    if (materials.some(m => m.normalMap || m.clearcoatNormalMap) || mesh.matrixWorld.determinant() <= 0 || hasShear(mesh.matrixWorld)) { result.push({ geometry, material: mesh.material, meshes: [mesh], instancingSafe: false }); continue; }
    let byMaterial = groups.get(geometry);
    if (!byMaterial) { byMaterial = new Map(); groups.set(geometry, byMaterial); }
    let group = byMaterial.get(key);
    if (!group) {
      group = { geometry, material: mesh.material, meshes: [], instancingSafe: true };
      byMaterial.set(key, group); result.push(group);
    }
    group.meshes.push(mesh);
  }
  return result;
}

export function createPreviewObjects(groups, geometries) {
  return groups.map(group => {
    const geometry = geometries.get(group.geometry);
    if (!group.instancingSafe) {
      const mesh = new THREE.Mesh(geometry, group.material);
      mesh.matrixAutoUpdate = false;
      mesh.matrix.copy(group.meshes[0].matrixWorld);
      return mesh;
    }
    const mesh = new THREE.InstancedMesh(geometry, group.material, group.meshes.length);
    mesh.frustumCulled = false;
    group.meshes.forEach((source, index) => mesh.setMatrixAt(index, source.matrixWorld));
    mesh.instanceMatrix.needsUpdate = true;
    return mesh;
  });
}

// Pose-only changes preserve the packed scene and prototype BVHs. Refuse a
// transform that no longer satisfies the original instance admission rules so
// the caller can rebuild the private scene instead.
export function syncPreviewSceneTransforms(scene, sourceRoot, sourceGround = null) {
  const groups = scene?.userData.previewGroups, objects = scene?.userData.previewObjects;
  const ground = scene?.userData.previewGround;
  if (!groups || !objects || groups.length !== objects.length || Boolean(ground) !== Boolean(sourceGround?.visible)) return false;
  sourceRoot.updateWorldMatrix(true, true);
  const original = new Set(groups.flatMap(group => group.meshes));
  const visible = [];
  let invalid = false;
  sourceRoot.traverseVisible(object => {
    if (object.isLine || object.isPoints || object.isSprite) invalid = true;
    if (!object.isMesh || collapsedTransform(object.matrixWorld)) return;
    if (!Number.isFinite(object.matrixWorld.determinant()) || object.matrixWorld.determinant() === 0) invalid = true;
    visible.push(object);
  });
  if (invalid || visible.length !== original.size || visible.some(mesh => !original.has(mesh))) return false;
  for (const group of groups) if (group.instancingSafe && group.meshes.some(mesh => mesh.matrixWorld.determinant() <= 0 || hasShear(mesh.matrixWorld))) return false;
  if (ground) {
    sourceGround.updateWorldMatrix(true, false);
    ground.matrix.copy(sourceGround.matrixWorld);
    ground.matrixWorldNeedsUpdate = true;
  }
  groups.forEach((group, index) => {
    const object = objects[index];
    if (object.isInstancedMesh) {
      group.meshes.forEach((source, at) => object.setMatrixAt(at, source.matrixWorld));
      object.instanceMatrix.needsUpdate = true;
    } else object.matrix.copy(group.meshes[0].matrixWorld);
    object.matrixWorldNeedsUpdate = true;
  });
  return true;
}

export async function preparePreviewScene(runtime, admission, signal, cache, deviceLimits,
  { buildBVH = null, onProgress = () => {}, adoptOwnedGeometry = false } = {}) {
  const scene = new THREE.Scene(), copied = new Map();
  const worker = buildBVH ? null : new GenerateMeshBVHWorker();
  const build = geometry => buildBVH ? Promise.resolve().then(() => {
    if (signal.aborted) throw new Error('cancelled');
    return buildBVH(geometry, { strategy: SAH, targetLeafSize: 5 }, signal);
  }) : new Promise((resolve, reject) => {
    if (signal.aborted) { reject(new Error('cancelled')); return; }
    const abort = () => reject(new Error('cancelled'));
    signal.addEventListener('abort', abort, { once: true });
    // WorkerBase.generate installs an unobserved finally promise which rejects on
    // worker errors. This job is sequential, so call its underlying task directly.
    worker.runTask(worker.worker, geometry, { strategy: SAH, targetLeafSize: 5 }).then(resolve, reject)
      .finally(() => signal.removeEventListener('abort', abort));
  });
  const ground = runtime.photographicStudio?.ground;
  const total = admission.geometries.size + Number(Boolean(ground?.visible && ground.material?.isMeshStandardMaterial));
  let completed = 0;
  try {
    onProgress({ phase: 'geometry', completed, total });
    cache.retain(admission.geometries);
    for (const source of admission.geometries) {
      if (signal.aborted) throw new Error('cancelled');
      let geometry = cache.get(source);
      if (!geometry) {
        // Only the dedicated worker may lend already-transferred private arrays.
        // Reconstruction remains their single disposal owner; normal callers clone.
        geometry = adoptOwnedGeometry ? source : source.clone();
        try { geometry.boundsTree = await build(geometry); } catch (error) { if (!adoptOwnedGeometry) geometry.dispose(); throw error; }
        if (signal.aborted) { if (!adoptOwnedGeometry) geometry.dispose(); throw new Error('cancelled'); }
        cache.set(source, geometry, { owned: !adoptOwnedGeometry });
      }
      copied.set(source, geometry);
      onProgress({ phase: 'geometry', completed: ++completed, total });
      if (buildBVH) await new Promise(resolve => setTimeout(resolve, 0));
    }
    if (signal.aborted) throw new Error('cancelled');
    const groups = groupPreviewMeshes(admission.meshes, admission.canonical);
    const reason = previewPreparedBudget(admission, groups, copied, deviceLimits);
    if (reason) throw new Error(reason);
    const objects = createPreviewObjects(groups, copied);
    scene.add(...objects);
    scene.userData.previewObjects = objects;
    scene.userData.previewGroups = groups;
    const allocation = previewPreparedAllocation(groups, copied);
    scene.userData.previewAllocation = allocation;
    if (ground?.visible && ground.material?.isMeshStandardMaterial) {
      const copy = new THREE.Mesh(ground.geometry.clone(), createPreviewFloorMaterial(ground.material, runtime.previewConfiguration));
      // The WebGPU tracer does not run three's onBeforeCompile floor shader. Bake
      // the same radial fade into its green-channel alpha map for ray transparency.
      const size = 128, pixels = new Uint8Array(size * size * 4);
      const inner = runtime.photographicStudio.floorFade?.value ?? (1.6 / 6);
      for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
        const radius = Math.hypot((x + 0.5) / size * 2 - 1, (y + 0.5) / size * 2 - 1);
        const t = Math.max(0, Math.min(1, (radius - inner) / (1 - inner)));
        const alpha = (1 - t * t * (3 - 2 * t)) ** 2;
        const i = (y * size + x) * 4;
        pixels[i] = pixels[i + 1] = pixels[i + 2] = Math.round(alpha * 255); pixels[i + 3] = 255;
      }
      const fade = new THREE.DataTexture(pixels, size, size);
      fade.source.uuid = FLOOR_ALPHA_SOURCE_ID;
      fade.minFilter = THREE.LinearFilter; fade.magFilter = THREE.LinearFilter;
      fade.needsUpdate = true;
      copy.material.alphaMap = fade;
      copy.material.transparent = true;
      copy.matrixAutoUpdate = false;
      ground.updateWorldMatrix(true, false);
      copy.matrix.copy(ground.matrixWorld);
      try { copy.geometry.boundsTree = await build(copy.geometry); } catch (error) { copy.geometry.dispose(); copy.material.alphaMap?.dispose(); copy.material.dispose(); throw error; }
      // The post-build admission includes the actual floor BLAS and its TLAS
      // cluster cut, rather than only the model prototypes.
      const floorPrimitives = countClusters(copy.geometry.boundsTree);
      allocation.primitives += floorPrimitives;
      allocation.transforms += copy.geometry.boundsTree._roots.length;
      allocation.nodeBytes += copy.geometry.boundsTree._roots.reduce((sum, root) => sum + root.byteLength, 0) + floorPrimitives * 64;
      scene.add(copy);
      scene.userData.previewGround = copy;
      onProgress({ phase: 'geometry', completed: ++completed, total });
    }
    // The HDR environment already contains the key softbox, fill and reflection cards.
    // A cloned raster spotlight would count the key twice and add a point-sized source
    // whose hard shadows ignore Softbox size. Trace the actual finite sources once;
    // environment importance sampling supplies their direct light and soft shadows.
    if (signal.aborted) throw new Error('cancelled');
    scene.environment = createStudioEnvironmentTexture(runtime.previewConfiguration);
    scene.environmentRotation.copy(runtime.scene.environmentRotation);
    scene.environmentIntensity = runtime.scene.environmentIntensity;
    scene.background = null;
    scene.userData.previewGeometries = [...copied.values()];
    return scene;
  } catch (error) {
    disposePreviewScene(scene);
    throw error;
  } finally {
    worker?.dispose();
  }
}

export function disposePreviewScene(scene) {
  scene?.environment?.dispose();
  for (const object of scene?.userData.previewObjects || []) if (object.isInstancedMesh) object.dispose();
  scene?.userData.previewGround?.geometry.dispose();
  scene?.userData.previewGround?.material.alphaMap?.dispose();
  scene?.userData.previewGround?.material.dispose();
}
