/**
 * A pose motion (`fea/poses.js`) on the model: the exaggeration its displacement is drawn at, and the box the model is
 * drawn in over its whole timeline, so the camera frames it once and never zooms while it plays.
 */
import { DISPLACEMENT } from "./fea/series.js";
import { filePositions, routineScale } from "./feaResult.js";

/** Moments of the timeline the box samples, besides its named states. */
const SAMPLES = 96;

/**
 * The exaggeration a pose's displacement is drawn at: the Deformation shown, or, for a motion that says how far it
 * moves (`raise.peakMm`, its shapes each 1 mm at their largest), enough to move it a tenth of the model's size where
 * the Deformation would move it less (`routineScale`'s rule). Returns `{ scale, raised }`: `raised` the factor when
 * it was raised, else null.
 */
export function poseScale(mesh, pose, shown) {
  const wanted = Number(shown) || 0;
  if (!pose?.raise || !pose.vector?.length || !(pose.raise.peakMm > 0)) return { scale: wanted, raised: null };
  const enough = routineScale(mesh, pose.vector.map(([name]) => name), 1e-12) / pose.raise.peakMm;
  return enough > wanted ? { scale: enough, raised: enough } : { scale: wanted, raised: null };
}

/**
 * `scene.bounds` grown to hold the model over a pose motion's whole timeline, drawn at `scale` (the Deformation it
 * opens on), as `{ min, max }` in the scene's space; null where it moves nothing.
 */
export function feaPoseBounds(THREE, scene, fea, motion, { scale = fea?.deformationScale } = {}) {
  const mesh = fea?.mesh;
  const position = mesh?.geometry?.getAttribute("position");
  if (!scene?.bounds || !position || !motion?.poseAt) return null;
  const rest = filePositions(mesh);
  const baked = mesh.geometry.getAttribute(DISPLACEMENT);
  const fileScale = Number(fea.deformationScale) || 0;
  const low = [Infinity, Infinity, Infinity];
  const high = [-Infinity, -Infinity, -Infinity];
  const moments = [...Array.from({ length: SAMPLES + 1 }, (_, i) => (motion.duration * i) / SAMPLES), ...motion.states.map((state) => state.at)];
  let moved = false;
  for (const at of moments) {
    const pose = motion.poseAt(at, {});
    if (!pose?.vector?.length) continue;
    const terms = pose.vector.map(([name, weight]) => [mesh.geometry.getAttribute(name)?.array, Number(weight) || 0]).filter(([array]) => array);
    if (!terms.length) continue;
    moved = true;
    const drawn = poseScale(mesh, pose, scale).scale;
    for (let i = 0; i < rest.length; i += 1) {
      let v = 0;
      for (const [array, weight] of terms) v += weight * array[i];
      const value = rest[i] - (baked ? fileScale * baked.array[i] : 0) + drawn * v;
      const c = i % 3;
      if (value < low[c]) low[c] = value;
      if (value > high[c]) high[c] = value;
    }
  }
  if (!moved || !low.every(Number.isFinite)) return null;
  scene.object3D?.updateWorldMatrix?.(true, true);
  const box = new THREE.Box3(new THREE.Vector3(...low), new THREE.Vector3(...high)).applyMatrix4(mesh.matrixWorld);
  box.union(new THREE.Box3(new THREE.Vector3(...scene.bounds.min), new THREE.Vector3(...scene.bounds.max)));
  return { min: box.min.toArray(), max: box.max.toArray() };
}
