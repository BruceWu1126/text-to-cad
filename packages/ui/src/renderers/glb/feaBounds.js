/**
 * The box an FEA result's model is drawn in over its whole series. The file's positions carry one
 * frame's displacement (the first: a nonlinear run's first load step, a mode's first shape), but
 * Study opens on another (the last load step, the peak) and a scrubber or Play shows them all: a
 * rubber strap stretched to twice its length at the last step leaves the box of its first. The
 * viewport frames that box and fits the camera's near and far planes to it, so a model drawn
 * outside it is sliced off by the near plane in an oblique view (the top view, looking down the
 * other axis, still shows it whole). The box here holds every frame at the file's own deformation
 * scale, so the framing never jumps while the series plays (as a GLB's animation clips are
 * sampled into theirs at load, `createGlbScene`). Preview frames the camera once on it, so it
 * also holds the analysis's routine as it is drawn: a Load ramp from no load to the load shown,
 * a Vibrate swung out both ways, each at the scale the routine plays at (`routineScale`: a piezo
 * bimorph's stroke raised to a tenth of its size, which the file's own scale does not reach), and
 * the moving part stays in view without the camera zooming while it plays. A result with no series
 * and no routine is drawn as the file holds it: its box is the file's.
 */
import { feaAnalysis } from "./fea/analyses/index.js";
import { DISPLACEMENT, deformationAt } from "./fea/series.js";
import { routineScale } from "./feaResult.js";

// A harmonic frame turns through its phase (re·cos − im·sin): sampled at this many phases for its box.
const PHASES = 16;

/**
 * `scene.bounds` grown to hold every frame of `fea`'s series and its routine (`routine`, the analysis's
 * by default) as they are drawn at `scale` (the deformation shown, the file's by default) and `load`
 * (the load shown, 1 by default), as `{ min, max }` in the scene's space; null where there is nothing to
 * grow (no series and no routine, no positions).
 */
export function feaDrawnBounds(THREE, scene, fea, { scale = fea?.deformationScale, load = 1, routine } = {}) {
  const mesh = fea?.mesh;
  const frames = fea?.series?.frames || [];
  const geometry = mesh?.geometry;
  const position = geometry?.getAttribute("position");
  const plays = routine === undefined ? (fea ? feaAnalysis(fea).routine(fea) : null) : routine;
  if (!scene?.bounds || !position || position.itemSize !== 3 || (frames.length < 2 && !plays)) return null;
  const count = position.count;
  const at = position.array;
  const baked = geometry.getAttribute(DISPLACEMENT);
  const fileScale = Number(fea.deformationScale) || 0;
  const drawn = (Number(scale) || 0) * (Number(load) || 0);
  const rest = new Float32Array(at.length);
  const bakedArray = baked?.itemSize === 3 && baked.count === count ? baked.array : null;
  for (let i = 0; i < at.length; i += 1) rest[i] = at[i] - (bakedArray ? fileScale * bakedArray[i] : 0);
  const low = [Infinity, Infinity, Infinity];
  const high = [-Infinity, -Infinity, -Infinity];
  const vectorsOf = (name) => {
    const vectors = name ? geometry.getAttribute(name) : null;
    return vectors?.itemSize === 3 && vectors.count === count ? vectors.array : null;
  };
  // The positions `rest + Σ factor · vectors`, into the box.
  const take = (terms) => {
    for (let i = 0; i < count; i += 1) {
      for (let c = 0; c < 3; c += 1) {
        let value = rest[3 * i + c];
        for (const [vectors, factor] of terms) value += factor * vectors[3 * i + c];
        if (value < low[c]) low[c] = value;
        if (value > high[c]) high[c] = value;
      }
    }
  };
  // The file's own shape, and the part at rest (a ramp starts from no load).
  take(bakedArray ? [[bakedArray, fileScale]] : []);
  if (plays) take([]);
  const kind = plays?.kind;
  const swings = kind === "vibrate" || (kind === "load_ramp" && feaAnalysis(fea).family !== "static");
  for (let index = 0; index < Math.max(frames.length, 1); index += 1) {
    const { attribute, imaginary } = frames.length ? deformationAt(fea, index) : { attribute: bakedArray ? DISPLACEMENT : null, imaginary: null };
    const real = vectorsOf(attribute);
    if (!real) continue;
    const im = vectorsOf(imaginary);
    // Vibrate and a Load ramp outside the static family play at their own scale, raised to be seen.
    const factor = swings ? routineScale(mesh, [attribute, ...(imaginary ? [imaginary] : [])], scale) * load : frames.length ? fileScale : drawn;
    if (kind === "vibrate") {
      // Out one way and back the other; a harmonic frame through its phase.
      for (let p = 0; p < (im ? PHASES : 2); p += 1) {
        const angle = im ? (2 * Math.PI * p) / PHASES : p === 0 ? Math.PI / 2 : -Math.PI / 2;
        take(im ? [[real, factor * Math.cos(angle)], [im, -factor * Math.sin(angle)]] : [[real, factor * Math.sin(angle)]]);
      }
    } else {
      take([[real, factor]]);
      if (frames.length && !swings && drawn !== fileScale) take([[real, drawn]]);
    }
  }
  if (!low.every(Number.isFinite)) return null;
  scene.object3D?.updateWorldMatrix?.(true, true);
  const box = new THREE.Box3(new THREE.Vector3(...low), new THREE.Vector3(...high)).applyMatrix4(mesh.matrixWorld);
  box.union(new THREE.Box3(new THREE.Vector3(...scene.bounds.min), new THREE.Vector3(...scene.bounds.max)));
  return { min: box.min.toArray(), max: box.max.toArray() };
}
