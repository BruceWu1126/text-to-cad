import { isSharedGeometry } from '../render/sharedGeometry.js';
import { planPreviewResources, previewResourcePool } from './resourcePolicy.js';

// Only owned copies cross the worker boundary: the editor keeps every source
// buffer and image. Work is yielded between bounded copies and object batches.
const COPY_CHUNK = 1024 * 1024;
const MAX_COPY_BYTES = 1024 * 1024 * 1024;
const tick = () => new Promise(resolve => setTimeout(resolve, 0));
const cancelled = signal => { if (signal?.aborted) throw new DOMException('Preview cancelled', 'AbortError'); };

export async function collectPreviewObjects(root, { signal, yieldWork = tick } = {}) {
  const meshes = [], nodes = [], traversal = [root]; let scanned = 0;
  root.parent?.updateWorldMatrix(true, false);
  while (traversal.length) {
    cancelled(signal);
    const object = traversal.pop();
    if (++scanned > 20000) throw new Error('scene budget');
    nodes.push({ object, parent: object.parent, visible: object.visible });
    if (!object.visible) continue;
    object.updateWorldMatrix(false, false);
    if (object.isMesh || object.isLine || object.isPoints || object.isSprite) meshes.push(object);
    for (let i = object.children.length - 1; i >= 0; i--) traversal.push(object.children[i]);
    if (scanned % 32 === 0) { await yieldWork(); cancelled(signal); }
  }
  return { meshes, nodes };
}

export async function snapshotPreviewScene(runtime, { signal, yieldWork = tick, maxCopyBytes = MAX_COPY_BYTES, deviceLimits = {}, lease: existingLease = null } = {}) {
  const { meshes, nodes } = await collectPreviewObjects(runtime.modelGroup, { signal, yieldWork });
  const identity = new WeakMap(); let nextIdentity = 1;
  const id = object => { if (!object) return 0; if (!identity.has(object)) identity.set(object, nextIdentity++); return identity.get(object); };
  const geometries = [], materials = [], textures = [], transfers = [], objects = [], sourceMeshes = [];
  const geometryIds = new Map(), materialIds = new Map(), textureIds = new Map(), shared = new Map(), imageCopies = new Map();
  let bytes = 0, steps = 0;
  const checkpoint = async () => { cancelled(signal); if (++steps % 32 === 0) { await yieldWork(); cancelled(signal); } };
  // Discover and admit the complete scene before allocating any private copy.
  const admission = { geometries: new Set(), materials: new Set(), textures: new Map(), uvChannels: new Set(['uv']),
    vertices: 0, triangles: 0, groups: 0, cloneBytes: 0, objectCount: nodes.length, reason: null };
  const seenShared = new Set();
  for (const mesh of meshes) {
    await checkpoint();
    if (!mesh.isMesh || mesh.isSkinnedMesh || mesh.isInstancedMesh || mesh.morphTargetInfluences?.length) throw new Error('unsupported geometry');
    const g = mesh.geometry;
    if (!g?.attributes?.position || !g.attributes.normal || Object.values(g.attributes).some(a => a.isInterleavedBufferAttribute) || g.index?.isInterleavedBufferAttribute) throw new Error('unsupported geometry');
    let unique = !admission.geometries.has(g);
    if (unique && isSharedGeometry(g)) {
      const key = JSON.stringify([id(g.index), Object.entries(g.attributes).sort(([a], [b]) => a.localeCompare(b)).map(([name, attr]) => [name, id(attr)]), g.groups, g.drawRange]);
      if (seenShared.has(key)) unique = false; else seenShared.add(key);
    }
    if (unique) {
      admission.geometries.add(g); admission.vertices += g.attributes.position.count;
      admission.triangles += (g.index?.count ?? g.attributes.position.count) / 3;
      for (const [name, attribute] of Object.entries(g.attributes)) { admission.cloneBytes += attribute.array.byteLength; if (/^uv(?:[1-7])?$/.test(name)) admission.uvChannels.add(name); }
      if (g.index) admission.cloneBytes += g.index.array.byteLength;
    }
    admission.groups += Math.max(g.groups.length, 1);
    for (const slot of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
      await checkpoint();
      if (!slot) throw new Error('unsupported material');
      admission.materials.add(slot);
      for (const value of Object.values(slot)) if (value?.isTexture) {
        const image = value.source?.data, width = image?.naturalWidth || image?.width, height = image?.naturalHeight || image?.height;
        if (!Number.isSafeInteger(width) || !Number.isSafeInteger(height) || width < 1 || height < 1) throw new Error('unsupported texture');
        const key = `${value.source?.uuid}:${value.colorSpace}`;
        admission.textures.set(key, { width, height, source: value.source, bytesPerPixel: image?.data?.byteLength ? image.data.byteLength / (width * height) : 4 });
      }
    }
  }
  const view = runtime.renderer.domElement;
  const plan = planPreviewResources({ admission, viewport: { width: view.width || view.clientWidth, height: view.height || view.clientHeight }, performance: runtime.previewConfiguration?.performance, phase: 'build', ownedGeometry: true, deviceLimits, budgetBytes: previewResourcePool.availableBytes + (existingLease?.bytes || 0) });
  if (!plan.ok) throw new Error(plan.reason);
  const lease = existingLease || previewResourcePool.reserve(plan.memory.peakBytes);
  if (!lease || (existingLease && !lease.resize(plan.memory.peakBytes))) throw new Error('Preview resource budget is in use');
  const texturePlan = new Map(plan.texturePlan.map(item => [item.key, item]));
  const copyArray = async array => {
    if (!ArrayBuffer.isView(array) || array instanceof DataView) throw new Error('unsupported geometry buffer');
    bytes += array.byteLength;
    if (bytes > maxCopyBytes) throw new Error('scene budget');
    const result = new array.constructor(array.length);
    const n = Math.max(1, Math.floor(COPY_CHUNK / array.BYTES_PER_ELEMENT));
    for (let i = 0; i < array.length; i += n) { cancelled(signal); result.set(array.subarray(i, i + n), i); await yieldWork(); }
    transfers.push(result.buffer);
    return result;
  };
  const texture = async value => {
    if (textureIds.has(value)) return textureIds.get(value);
    if (value.isRenderTargetTexture || value.isVideoTexture || value.isCompressedTexture || value.isCubeTexture || value.isData3DTexture || value.isDataArrayTexture) throw new Error('unsupported texture');
    const image = value.source?.data;
    const width = image?.naturalWidth || image?.width, height = image?.naturalHeight || image?.height;
    if (!(width > 0 && height > 0)) throw new Error('unsupported texture');
    const selected = texturePlan.get(`${value.source?.uuid}:${value.colorSpace}`);
    if (!selected) throw new Error('Preview texture plan is unavailable');
    const imageKey = `${value.source?.uuid}:${selected.width}x${selected.height}`;
    let copy = imageCopies.get(imageKey);
    if (!copy) {
      let data, bitmap;
      if (value.isDataTexture && image.data) {
        if (selected.width === width && selected.height === height) data = await copyArray(image.data);
        else {
          const channels = image.data.length / (width * height);
          if (!Number.isInteger(channels) || channels < 1 || channels > 4) throw new Error('unsupported texture');
          const length = selected.width * selected.height * channels;
          bytes += length * image.data.BYTES_PER_ELEMENT;
          if (bytes > maxCopyBytes) throw new Error('scene budget');
          data = new image.data.constructor(length);
          for (let y = 0; y < selected.height; y++) {
            const sy = Math.min(height - 1, Math.floor((y + 0.5) * height / selected.height));
            for (let x = 0; x < selected.width; x++) {
              const sx = Math.min(width - 1, Math.floor((x + 0.5) * width / selected.width));
              for (let c = 0; c < channels; c++) data[(y * selected.width + x) * channels + c] = image.data[(sy * width + sx) * channels + c];
            }
            if (y % 32 === 0) { await yieldWork(); cancelled(signal); }
          }
          transfers.push(data.buffer);
        }
      } else {
        bytes += selected.width * selected.height * 4;
        if (bytes > maxCopyBytes) throw new Error('scene budget');
        bitmap = await createImageBitmap(image, { colorSpaceConversion: 'none', premultiplyAlpha: 'none', resizeWidth: selected.width, resizeHeight: selected.height, resizeQuality: 'high' });
        if (signal?.aborted) { bitmap.close(); cancelled(signal); }
        transfers.push(bitmap);
      }
      copy = { data, bitmap }; imageCopies.set(imageKey, copy);
    }
    const item = { ...copy, width: selected.width, height: selected.height, isDataTexture: !!value.isDataTexture,
      mapping: value.mapping, channel: value.channel, wrapS: value.wrapS, wrapT: value.wrapT,
      magFilter: value.magFilter, minFilter: value.minFilter, format: value.format, type: value.type,
      colorSpace: value.colorSpace, flipY: value.flipY, premultiplyAlpha: value.premultiplyAlpha,
      unpackAlignment: value.unpackAlignment, generateMipmaps: value.generateMipmaps,
      offset: value.offset.toArray(), repeat: value.repeat.toArray(), center: value.center.toArray(), rotation: value.rotation,
      matrixAutoUpdate: value.matrixAutoUpdate, matrix: value.matrix.toArray(), sourceId: value.source?.uuid };
    const index = textures.length; textures.push(item); textureIds.set(value, index); return index;
  };
  const material = async value => {
    if (materialIds.has(value)) return materialIds.get(value);
    if (!value || !(value.isMeshStandardMaterial || value.isMeshPhysicalMaterial) || value.visible === false || value.clippingPlanes?.length || value.displacementMap || value.bumpMap) throw new Error('unsupported material');
    const fields = {};
    for (const [name, item] of Object.entries(value)) {
      if (name.startsWith('is') || ['id', 'uuid', 'type', 'name', 'version', 'userData'].includes(name) || item == null) continue;
      if (item.isTexture) fields[name] = { texture: await texture(item) };
      else if (item.isColor) fields[name] = { color: item.toArray() };
      else if (item.isVector2 || item.isVector3 || item.isVector4) fields[name] = { vector: item.toArray() };
      else if (['number', 'boolean', 'string'].includes(typeof item)) fields[name] = item;
      else if (Array.isArray(item) && item.every(n => typeof n === 'number')) fields[name] = [...item];
    }
    const index = materials.length; materials.push({ physical: !!value.isMeshPhysicalMaterial, fields }); materialIds.set(value, index); return index;
  };
  const geometry = async value => {
    if (geometryIds.has(value)) return geometryIds.get(value);
    if (!value || Object.values(value.attributes).some(a => a.isInterleavedBufferAttribute) || value.index?.isInterleavedBufferAttribute || Object.values(value.morphAttributes || {}).some(a => a.length)) throw new Error('unsupported geometry');
    let key = null;
    if (isSharedGeometry(value)) {
      key = JSON.stringify([id(value.index), Object.entries(value.attributes).sort(([a], [b]) => a.localeCompare(b)).map(([name, attr]) => [name, id(attr)]), value.groups, value.drawRange]);
      if (shared.has(key)) { const index = shared.get(key); geometryIds.set(value, index); return index; }
    }
    const attr = async a => ({ array: await copyArray(a.array), itemSize: a.itemSize, normalized: a.normalized, usage: a.usage, gpuType: a.gpuType });
    const attributes = {};
    for (const [name, item] of Object.entries(value.attributes)) attributes[name] = await attr(item);
    const index = geometries.length;
    geometries.push({ attributes, index: value.index ? await attr(value.index) : null, groups: value.groups.map(group => ({ ...group })), drawRange: { ...value.drawRange } });
    geometryIds.set(value, index); if (key) shared.set(key, index); return index;
  };
  try {
    for (const mesh of meshes) {
      await checkpoint();
      if (!mesh.isMesh || mesh.isSkinnedMesh || mesh.isInstancedMesh || mesh.morphTargetInfluences?.length) throw new Error('unsupported geometry');
      const slots = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
      const materialSlots = [];
      for (const slot of slots) materialSlots.push(await material(slot));
      objects.push({ geometry: await geometry(mesh.geometry), materials: materialSlots, array: Array.isArray(mesh.material), matrix: mesh.matrixWorld.toArray(), renderOrder: mesh.renderOrder });
      sourceMeshes.push(mesh);
    }
    const ground = runtime.photographicStudio?.ground;
    let floor = null;
    if (ground?.visible) {
      ground.updateWorldMatrix(true, false);
      floor = { geometry: await geometry(ground.geometry), material: await material(ground.material), matrix: ground.matrixWorld.toArray() };
    }
    // Update cached hierarchy parent-first and latch the complete placement set
    // together after yielded copying, so a pose cannot mix animation ticks.
    runtime.modelGroup.parent?.updateWorldMatrix(true, false);
    for (const node of nodes) {
      if (node.object.parent !== node.parent || node.object.visible !== node.visible) throw new Error('Preview scene changed during preparation');
      if (node.visible) node.object.updateWorldMatrix(false, false);
    }
    sourceMeshes.forEach((mesh, index) => { objects[index].matrix = mesh.matrixWorld.toArray(); });
    const studio = runtime.photographicStudio;
    const backdrop = studio?.backdrop;
    return { payload: { geometries, materials, textures, objects, floor,
      studio: { floorFade: studio?.floorFade?.value, backdrop: backdrop ? { visible: backdrop.object?.visible !== false,
        color: backdrop.uniforms?.uColor?.value?.toArray(), shape: backdrop.uniforms?.uShape?.value?.toArray() } : null },
      configuration: structuredClone(runtime.previewConfiguration || {}), resourcePlan: { profile: plan.profile, memory: plan.memory, sourceGeometryBytes: admission.cloneBytes, sourceTextureBytes: [...admission.textures.values()].reduce((sum, texture) => sum + texture.width * texture.height * texture.bytesPerPixel, 0), objectCount: admission.objectCount, geometryCount: admission.geometries.size, admission: { vertices: admission.vertices, triangles: admission.triangles, groups: admission.groups, cloneBytes: admission.cloneBytes, uvCount: admission.uvChannels.size, materialCount: admission.materials.size } }, environmentRotation: runtime.scene.environmentRotation.toArray(),
      environmentIntensity: runtime.scene.environmentIntensity, toneMapping: runtime.renderer.toneMapping,
      exposure: runtime.renderer.toneMappingExposure, clearAlpha: runtime.renderer.getClearAlpha() }, transfers, sourceMeshes, sourceNodes: nodes, sourceGround: floor ? ground : null, bytes, lease };
  } catch (error) { lease.release(); for (const item of transfers) item.close?.(); throw error; }
}
