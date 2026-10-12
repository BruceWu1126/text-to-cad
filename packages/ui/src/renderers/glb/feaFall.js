/**
 * Where a dropped part is drawn while its Drop routine plays (`fea/fall.js` keeps the time): turned so
 * the way it falls points down, the floor level through the point it meets first, lifted above the
 * floor as it falls, carried by an impact's centre of mass where the frames say, and never drawn
 * through the floor. The deformation itself stays in the mesh's positions (`applyDeformation`); the
 * fall is the mesh's matrix, so the markers and the colours ride with it, and the floor is a plane of
 * its own beside the mesh. Also the box all of that is drawn in, which Preview frames.
 */
import { DISPLACEMENT, deformationAt } from "./fea/series.js";
import { filePositions } from "./feaResult.js";

// The mesh's own axes are glTF's (y up) in metres; the file's are the part's (z up) in mm.
const gltfAxes = ([x, y, z]) => [x, z, 0 - y];
const metres = (mm) => gltfAxes(mm.map((value) => value / 1000));

/**
 * The fall in the mesh's own space: `along` (unit, the way it falls), `pivot` (the point it meets the floor at,
 * metres), `turn` (the quaternion that points `along` down), `heightM` and an impact's `centres` (its centre of
 * mass's travel at each frame, metres). null for a result with no fall.
 */
export function fallFrame(THREE, fea) {
  const fall = fea?.analysis?.fall;
  if (!fall) return null;
  const along = new THREE.Vector3(...gltfAxes(fall.direction)).normalize();
  if (!(along.lengthSq() > 0)) return null;
  return {
    fall, along,
    pivot: new THREE.Vector3(...metres(fall.floor.point)),
    turn: new THREE.Quaternion().setFromUnitVectors(along, new THREE.Vector3(0, -1, 0)),
    heightM: fall.heightMm / 1000,
    centres: fall.centres ? fall.centres.map((centre) => new THREE.Vector3(...metres(centre))) : null,
  };
}

/** How far past the floor `positions` (the mesh's, moved by `centre`) reach along the fall, metres: above 0 is through it. */
export function pastFloor(positions, frame, centre = null) {
  const { along, pivot } = frame;
  const offset = (centre ? centre.dot(along) : 0) - pivot.dot(along);
  let most = -Infinity;
  for (let i = 0; i + 2 < positions.length; i += 3) {
    const reach = positions[i] * along.x + positions[i + 1] * along.y + positions[i + 2] * along.z;
    if (reach > most) most = reach;
  }
  return Number.isFinite(most) ? most + offset : 0;
}

/**
 * The mesh's matrix in its own space for a pose of the fall: moved by `centre`, turned about the floor point so the
 * fall points down, and lifted `lift` metres above the floor (`out`, a Matrix4).
 */
export function fallMatrix(THREE, frame, { lift = 0, centre = null } = {}, out = new THREE.Matrix4()) {
  const turned = new THREE.Matrix4().makeRotationFromQuaternion(frame.turn);
  const toPivot = new THREE.Matrix4().makeTranslation(-frame.pivot.x + (centre?.x || 0), -frame.pivot.y + (centre?.y || 0), -frame.pivot.z + (centre?.z || 0));
  const back = new THREE.Matrix4().makeTranslation(frame.pivot.x, frame.pivot.y + lift, frame.pivot.z);
  return out.copy(back).multiply(turned).multiply(toPivot);
}

/**
 * The box, in the mesh's own space, the motion draws the part in: turned, its frames (or its own displacement) at
 * `scale`, each moved by its centre of mass, on the floor, and (`height`) lifted to its height. null for a result
 * with no fall.
 */
export function fallBox(THREE, fea, frame, scale = fea?.deformationScale, height = true) {
  const geometry = fea?.mesh?.geometry;
  const position = geometry?.getAttribute("position");
  if (!frame || !position || position.itemSize !== 3) return null;
  const at = filePositions(fea.mesh);
  const baked = geometry.getAttribute(DISPLACEMENT);
  const fileScale = Number(fea.deformationScale) || 0;
  const bakedArray = baked?.itemSize === 3 && baked.count * 3 === at.length ? baked.array : null;
  const frames = fea.series?.frames || [];
  const drawn = Number(scale) || 0;
  const vectors = (frames.length ? frames.map((_, index) => deformationAt(fea, index).attribute) : [DISPLACEMENT])
    .map((name) => (name ? geometry.getAttribute(name) : null)).map((entry) => (entry?.itemSize === 3 && entry.count * 3 === at.length ? entry.array : null));
  const box = new THREE.Box3();
  const point = new THREE.Vector3();
  const matrix = new THREE.Matrix4();
  // The part at rest, and each frame (a drop's own displacement at its peak), with its centre of mass and kept off the floor.
  [null, ...vectors].forEach((vector, index) => {
    const centre = index > 0 ? frame.centres?.[index - 1] || null : null;
    const drawnAt = new Float32Array(at.length);
    for (let i = 0; i < at.length; i += 1) drawnAt[i] = at[i] - (bakedArray ? fileScale * bakedArray[i] : 0) + (vector ? drawn * vector[i] : 0);
    const lift = Math.max(0, pastFloor(drawnAt, frame, centre));
    fallMatrix(THREE, frame, { centre, lift }, matrix);
    for (let i = 0; i + 2 < drawnAt.length; i += 3) box.expandByPoint(point.set(drawnAt[i], drawnAt[i + 1], drawnAt[i + 2]).applyMatrix4(matrix));
  });
  if (box.isEmpty()) return null;
  // Let go from its height: the same part, that far up.
  if (height) box.union(box.clone().translate(new THREE.Vector3(0, frame.heightM, 0)));
  return box;
}

/**
 * The box a drop's motion is drawn in, in the scene's space as `scene.bounds` is: with `height`, the whole fall, what
 * Preview frames; without, the part on the floor through its impact, what the tools view frames and the stage stands
 * under. null for a result with no fall.
 */
export function feaFallBounds(THREE, scene, fea, { scale, height = true } = {}) {
  const frame = fallFrame(THREE, fea);
  const box = frame ? fallBox(THREE, fea, frame, scale, height) : null;
  if (!box) return null;
  scene?.object3D?.updateWorldMatrix?.(true, true);
  box.applyMatrix4(fea.mesh.matrixWorld);
  return { min: box.min.toArray(), max: box.max.toArray() };
}

/** Two boxes `{ min, max }` as one. */
export const unionBounds = (a, b) => (!a ? b : !b ? a : {
  min: a.min.map((value, axis) => Math.min(value, b.min[axis])), max: a.max.map((value, axis) => Math.max(value, b.max[axis])) });
