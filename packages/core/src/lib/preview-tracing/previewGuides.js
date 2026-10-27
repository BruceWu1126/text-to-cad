import { Matrix4, Mesh, Scene } from 'three/webgpu';

/**
 * A private guide scene over borrowed geometry and authored material values.
 * Three's WebGPU shader cache deliberately keys each InstancedMesh by object UUID,
 * even a count-one instance; ordinary meshes can share the same material shader.
 * Expand only this occasional raster pass and preserve exact material semantics.
 */
export function createPreviewGuideScene(source) {
  const scene = new Scene(), objects = [], matrix = new Matrix4(), clones = new Map();
  const guideMaterial = material => {
    // Three's ordinary-mesh WebGPU program key omits texture.channel and
    // colorSpace although both affect generated texture nodes. Keep distinct
    // shader variants on private clones; all authored values and textures stay.
    const textures = Object.entries(material).filter(([, value]) => value?.isTexture)
      .map(([name, texture]) => [name, texture.channel, texture.colorSpace]).sort(([a], [b]) => a.localeCompare(b));
    if (!textures.length) return material;
    if (clones.has(material)) return clones.get(material);
    const clone = material.clone();
    clone.onBeforeCompile = material.onBeforeCompile;
    const base = material.customProgramCacheKey();
    const variant = JSON.stringify(textures);
    clone.customProgramCacheKey = () => `${base}|preview-guide:${variant}`;
    clones.set(material, clone);
    return clone;
  };
  source.updateMatrixWorld(true);
  source.traverseVisible(object => {
    if (!object.isMesh) return;
    if (object.isInstancedMesh && object.instanceColor) throw new Error('unsupported guide instance colors');
    const count = object.isInstancedMesh ? object.count : 1;
    const material = Array.isArray(object.material) ? object.material.map(guideMaterial) : guideMaterial(object.material);
    for (let i = 0; i < count; i++) {
      const mesh = new Mesh(object.geometry, material);
      mesh.matrixAutoUpdate = false;
      if (object.isInstancedMesh) { object.getMatrixAt(i, matrix); mesh.matrix.multiplyMatrices(object.matrixWorld, matrix); }
      else mesh.matrix.copy(object.matrixWorld);
      mesh.frustumCulled = false;
      mesh.renderOrder = object.renderOrder;
      scene.add(mesh); objects.push(mesh);
    }
  });
  // RenderObject listens for this event. Geometry and materials belong to the
  // trace scene, so releasing the guide render objects must not dispose either.
  return { scene, dispose() { for (const object of objects) object.dispatchEvent({ type: 'dispose' }); objects.length = 0; scene.clear(); for (const clone of clones.values()) clone.dispose(); clones.clear(); } };
}
