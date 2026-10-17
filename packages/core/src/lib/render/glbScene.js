import { clone as cloneSkinned } from "three/examples/jsm/utils/SkeletonUtils.js";
import { createSurfaceLook } from "../viewer/surfaceLook.js";
import { disposeGlbDocument } from "./glbMeshData.js";
import { releaseGeometry, shareGeometry } from "./sharedGeometry.js";

// Every mesh's materials as the file authored them, before any look touches them: what a sibling
// starts from (`createSibling`).
function materialList(object) {
  return Array.isArray(object.material) ? object.material : [object.material];
}
function copyMaterial(material) {
  // Material.copy deep-copies userData through JSON; glTF extras survive that, a viewer's own may not.
  const userData = material.userData;
  material.userData = {};
  try {
    const copy = material.clone();
    copy.userData = { ...userData };
    return copy;
  } finally {
    material.userData = userData;
  }
}

function boundsOf(THREE, root) {
  root.updateWorldMatrix(true, true);
  const box = new THREE.Box3().setFromObject(root, true);
  if (box.isEmpty()) return { min: [0, 0, 0], max: [0, 0, 0] };
  return { min: box.min.toArray(), max: box.max.toArray() };
}

/**
 * A GLB's scene (`../viewer/sceneContract.js`): the file's NATIVE glTF hierarchy,
 * always. Nodes, skins, morph targets and authored materials are the file's own;
 * the scene places them in CAD space, wears the look its host resolves, and owns
 * the document from here on (`dispose()` releases its geometry, materials and
 * textures). The ONE builder of a GLB's scene: the viewer's GLB renderer and the
 * snapshot CLI's headless stage both call it, so neither can draw a GLB the other
 * does not.
 *
 * @param {typeof import("three")} THREE
 * @param {{ scene: import("three").Object3D, clips: object[], cadRootMatrix: number[],
 *   animatedBounds: object | null, restBounds: object | null }} document  `buildGlbDocumentFromBuffer`.
 * @returns {import("../viewer/sceneContract.js").KitScene & { document: object, meshCount: number }}
 */
export function createGlbScene(THREE, document, { sibling = false } = {}) {
  const root = new THREE.Group();
  root.name = "glb-document";
  root.matrix.fromArray(document.cadRootMatrix);
  root.matrixAutoUpdate = false;
  root.matrixWorldNeedsUpdate = true;
  root.add(document.scene);
  document.scene.traverse((object) => {
    // The viewer owns one lighting rig in both Inspect and Render. Keep the authored
    // hierarchy, but never let embedded punctual lights build a second, file-specific one.
    if (object.isLight) { object.visible = false; return; }
    if (!object.isMesh) return;
    object.castShadow = true;
    // Three caches local culling bounds. Bone deformation and morph displacement
    // leave the rest-pose box while the hierarchy and the framing box stay correct.
    if (object.isSkinnedMesh || object.morphTargetInfluences?.length) object.frustumCulled = false;
  });
  // The authored materials, kept apart from the ones this scene's look will dress, for a sibling.
  const authored = new Map();
  document.scene.traverse((object) => {
    if (object.isMesh) authored.set(object, materialList(object).map(material => material && copyMaterial(material)));
  });
  const look = createSurfaceLook(THREE, document.scene);
  // One box for framing, lighting and the floor: a routine poses the model inside
  // the box sampled over every clip at load, so playing one never re-frames it.
  const bounds = document.animatedBounds || document.restBounds || boundsOf(THREE, root);
  let disposed = false;
  return {
    document,
    meshCount: look.meshCount,
    object3D: root,
    bounds,
    restBounds: bounds,
    setSurfaceLook(next) { if (!disposed) look.apply(next); },
    /**
     * A second scene of the same document (the viewer's Studio beside its editor): its own copy of
     * the hierarchy (skins rebound to its own bones), its own materials as the file authored them,
     * and its own look, over this scene's geometry and textures (`sharedGeometry.js`). Its
     * `document` is the same document around its own copy, so a routine plays on it alone. This
     * scene stays the owner of the geometry and textures, so the sibling is disposed first.
     */
    createSibling() {
      const copy = cloneSkinned(document.scene);
      const sources = [];
      document.scene.traverse(object => { if (object.isMesh) sources.push(object); });
      let index = 0;
      copy.traverse((object) => {
        if (!object.isMesh) return;
        const source = sources[index++];
        object.geometry = shareGeometry(THREE, source.geometry);
        const materials = (authored.get(source) || materialList(source)).map(material => material && copyMaterial(material));
        object.material = Array.isArray(source.material) ? materials : materials[0];
      });
      return createGlbScene(THREE, { ...document, scene: copy }, { sibling: true });
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      root.removeFromParent();
      look.dispose();
      for (const list of authored.values()) for (const material of list) material?.dispose();
      if (!sibling) { disposeGlbDocument(document); return; }
      // A sibling owns its copy's materials and its share of the geometry; textures and buffers
      // are its source's.
      document.scene.traverse((object) => {
        if (!object.isMesh) return;
        releaseGeometry(object.geometry);
        for (const material of materialList(object)) material?.dispose();
      });
    }
  };
}
