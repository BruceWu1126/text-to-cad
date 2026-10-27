import * as THREE from 'three';

// This scene is private to the worker. None of its arrays, materials, textures
// or object transforms are owned by the editor.
export function reconstructPreviewScene(payload) {
  const sources = new Map();
  const textures = payload.textures.map(item => {
    const texture = item.isDataTexture
      ? new THREE.DataTexture(item.data, item.width, item.height, item.format, item.type)
      : new THREE.Texture(item.bitmap);
    if (item.sourceId) {
      if (sources.has(item.sourceId)) {
        texture.source = sources.get(item.sourceId);
      } else { texture.source.uuid = item.sourceId; sources.set(item.sourceId, texture.source); }
    }
    for (const key of ['mapping', 'channel', 'wrapS', 'wrapT', 'magFilter', 'minFilter', 'format', 'type', 'colorSpace', 'flipY', 'premultiplyAlpha', 'unpackAlignment', 'generateMipmaps', 'rotation', 'matrixAutoUpdate']) if (item[key] !== undefined) texture[key] = item[key];
    texture.offset.fromArray(item.offset); texture.repeat.fromArray(item.repeat); texture.center.fromArray(item.center); texture.matrix.fromArray(item.matrix); texture.needsUpdate = true;
    return texture;
  });
  const materials = payload.materials.map(item => {
    const result = item.physical ? new THREE.MeshPhysicalMaterial() : new THREE.MeshStandardMaterial();
    for (const [key, value] of Object.entries(item.fields)) {
      if (value && typeof value === 'object' && 'texture' in value) result[key] = textures[value.texture];
      else if (value && typeof value === 'object' && 'color' in value) result[key].fromArray(value.color);
      else if (value && typeof value === 'object' && 'vector' in value) result[key].fromArray(value.vector);
      else result[key] = value;
    }
    return result;
  });
  const geometries = payload.geometries.map(item => {
    const geometry = new THREE.BufferGeometry();
    const attribute = value => {
      const result = new THREE.BufferAttribute(value.array, value.itemSize, value.normalized);
      if (value.usage != null) result.setUsage(value.usage);
      if (value.gpuType != null) result.gpuType = value.gpuType;
      return result;
    };
    for (const [name, value] of Object.entries(item.attributes)) geometry.setAttribute(name, attribute(value));
    if (item.index) geometry.setIndex(attribute(item.index));
    for (const group of item.groups) geometry.addGroup(group.start, group.count, group.materialIndex);
    geometry.setDrawRange(item.drawRange.start, item.drawRange.count);
    return geometry;
  });
  const root = new THREE.Group(), objects = payload.objects.map(item => {
    const slots = item.materials.map(index => materials[index]);
    const object = new THREE.Mesh(geometries[item.geometry], item.array ? slots : slots[0]);
    object.matrixAutoUpdate = false; object.matrix.fromArray(item.matrix); object.matrixWorldNeedsUpdate = true; object.renderOrder = item.renderOrder; root.add(object); return object;
  });
  let ground = null;
  if (payload.floor) {
    ground = new THREE.Mesh(geometries[payload.floor.geometry], materials[payload.floor.material]);
    ground.matrixAutoUpdate = false; ground.matrix.fromArray(payload.floor.matrix); ground.matrixWorldNeedsUpdate = true;
  }
  const backdrop = payload.studio.backdrop;
  const studio = { ground, floorFade: { value: payload.studio.floorFade }, backdrop: backdrop ? {
    object: { visible: backdrop.visible }, uniforms: { uColor: { value: new THREE.Vector3().fromArray(backdrop.color || [1, 1, 1]) }, uShape: { value: new THREE.Vector3().fromArray(backdrop.shape || [0, 0, 0]) } },
  } : null };
  const scene = new THREE.Scene(); scene.environmentRotation.fromArray(payload.environmentRotation); scene.environmentIntensity = payload.environmentIntensity; scene.add(root); if (ground) scene.add(ground);
  return { scene, modelGroup: root, photographicStudio: studio, objects,
    dispose() { for (const texture of textures) { texture.dispose(); texture.image?.close?.(); } for (const material of materials) material.dispose(); for (const geometry of geometries) geometry.dispose(); scene.clear(); } };
}
