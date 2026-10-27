import * as THREE from 'three';
import { SAH } from 'three-mesh-bvh';
import { GenerateMeshBVHWorker } from 'three-mesh-bvh/worker';
import { createStudioEnvironmentTexture } from '../../common/environmentMap.js';

// Bound the synchronous top-level build and memory before copying any buffers.
export const PREVIEW_LIMITS = Object.freeze({ triangles: 750000, vertices: 900000, meshes: 500, groups: 2000, materials: 512, texturePixels: 4194304, textureSide: 2048 });

export function inspectPreviewScene(root, limits = PREVIEW_LIMITS) {
  const geometries = new Set(), materials = new Set(), textures = new Set(), meshes = [];
  let triangles = 0, vertices = 0, groups = 0, texturePixels = 0, reason = null;
  root.updateMatrixWorld(true);
  root.traverseVisible(object => {
    if (reason) return;
    if (object.isLine || object.isPoints || object.isSprite) { reason = 'unsupported primitive'; return; }
    if (!object.isMesh) return;
    if (object.isSkinnedMesh || object.isInstancedMesh || object.morphTargetInfluences?.length) { reason = 'animated geometry'; return; }
    const geometry = object.geometry, position = geometry?.getAttribute('position');
    if (!position || !geometry.getAttribute('normal') || Object.values(geometry.attributes).some(attr => attr.isInterleavedBufferAttribute) || geometry.index?.isInterleavedBufferAttribute ||
        geometry.drawRange.start !== 0 || Number.isFinite(geometry.drawRange.count)) { reason = 'unsupported geometry'; return; }
    const slots = Array.isArray(object.material) ? object.material : [object.material];
    if (slots.some(material => !material || !(material.isMeshStandardMaterial || material.isMeshPhysicalMaterial) ||
      material.visible === false || material.clippingPlanes?.length || Object.values(material).some(value => value?.isTexture && (value.isRenderTargetTexture || value.isVideoTexture)) ||
      (material.normalMap && (!geometry.getAttribute('tangent') || !geometry.getAttribute('uv'))))) { reason = 'unsupported material'; return; }
    if (slots.length > 1 && (!geometry.groups.length || geometry.groups.some(g => !slots[g.materialIndex]))) { reason = 'invalid material groups'; return; }
    slots.forEach(material => {
      materials.add(material);
      for (const texture of Object.values(material)) {
        if (!texture?.isTexture || textures.has(texture.source)) continue;
        textures.add(texture.source);
        const image = texture.source?.data;
        const width = image?.naturalWidth || image?.width, height = image?.naturalHeight || image?.height;
        if (!(width > 0 && height > 0) || width > (limits.textureSide ?? PREVIEW_LIMITS.textureSide) || height > (limits.textureSide ?? PREVIEW_LIMITS.textureSide)) { reason = 'texture budget'; continue; }
        texturePixels += width * height;
      }
    });
    meshes.push(object);
    groups += Array.isArray(object.material) ? geometry.groups.length : 1;
    if (!geometries.has(geometry)) {
      geometries.add(geometry);
      triangles += (geometry.index?.count ?? position.count) / 3;
      vertices += position.count;
    }
  });
  if (!reason && !meshes.length) reason = 'empty scene';
  if (!reason && (triangles > limits.triangles || vertices > limits.vertices || meshes.length > limits.meshes || groups > (limits.groups ?? PREVIEW_LIMITS.groups) || materials.size > limits.materials || texturePixels > (limits.texturePixels ?? PREVIEW_LIMITS.texturePixels))) reason = 'scene budget';
  return { meshes, geometries, triangles, vertices, reason };
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
      entry.geometry.dispose(); entries.delete(source); return null;
    },
    set(source, geometry) { entries.set(source, { geometry, signature: signature(source) }); },
    retain(sources) { for (const [source, entry] of entries) if (!sources.has(source)) { entry.geometry.dispose(); entries.delete(source); } },
    dispose() { for (const entry of entries.values()) entry.geometry.dispose(); entries.clear(); },
  };
}

export async function preparePreviewScene(runtime, admission, signal, cache) {
  const scene = new THREE.Scene(), copied = new Map();
  const worker = new GenerateMeshBVHWorker();
  const build = geometry => new Promise((resolve, reject) => {
    if (signal.aborted) { reject(new Error('cancelled')); return; }
    const abort = () => reject(new Error('cancelled'));
    signal.addEventListener('abort', abort, { once: true });
    // WorkerBase.generate installs an unobserved finally promise which rejects on
    // worker errors. This job is sequential, so call its underlying task directly.
    worker.runTask(worker.worker, geometry, { strategy: SAH, targetLeafSize: 5 }).then(resolve, reject)
      .finally(() => signal.removeEventListener('abort', abort));
  });
  try {
    cache.retain(admission.geometries);
    for (const source of admission.geometries) {
      if (signal.aborted) throw new Error('cancelled');
      let geometry = cache.get(source);
      if (!geometry) {
        geometry = source.clone();
        try { geometry.boundsTree = await build(geometry); } catch (error) { geometry.dispose(); throw error; }
        if (signal.aborted) { geometry.dispose(); throw new Error('cancelled'); }
        cache.set(source, geometry);
      }
      copied.set(source, geometry);
    }
    if (signal.aborted) throw new Error('cancelled');
    for (const source of admission.meshes) {
      const mesh = new THREE.Mesh(copied.get(source.geometry), source.material);
      mesh.matrixAutoUpdate = false;
      mesh.matrix.copy(source.matrixWorld);
      scene.add(mesh);
    }
    const ground = runtime.photographicStudio?.ground;
    if (ground?.visible && ground.material?.isMeshStandardMaterial) {
      const copy = new THREE.Mesh(ground.geometry.clone(), ground.material.clone());
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
      fade.minFilter = THREE.LinearFilter; fade.magFilter = THREE.LinearFilter;
      fade.needsUpdate = true;
      copy.material.alphaMap = fade;
      copy.material.transparent = true;
      copy.matrixAutoUpdate = false;
      ground.updateWorldMatrix(true, false);
      copy.matrix.copy(ground.matrixWorld);
      try { copy.geometry.boundsTree = await build(copy.geometry); } catch (error) { copy.geometry.dispose(); copy.material.alphaMap?.dispose(); copy.material.dispose(); throw error; }
      scene.add(copy);
      scene.userData.previewGround = copy;
    }
    const light = runtime.photographicStudio?.keyLight;
    if (light?.visible) {
      const clone = light.clone();
      light.updateWorldMatrix(true, false);
      clone.position.setFromMatrixPosition(light.matrixWorld);
      light.target.updateWorldMatrix(true, false);
      clone.target.position.setFromMatrixPosition(light.target.matrixWorld);
      scene.add(clone, clone.target);
    }
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
    worker.dispose();
  }
}

export function disposePreviewScene(scene) {
  scene?.environment?.dispose();
  scene?.userData.previewGround?.geometry.dispose();
  scene?.userData.previewGround?.material.alphaMap?.dispose();
  scene?.userData.previewGround?.material.dispose();
}
