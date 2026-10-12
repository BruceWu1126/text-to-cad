/**
 * A flow's tracers, drawn: dots carried along the paths cadgen traced through the solved velocity field
 * (`fea/poses.js`'s `readTracers`: each path's points `step` seconds of real time apart, mm in the part's own
 * axes). Several dots ride each path, spread evenly over its trip, and each starts again at its inlet when it reaches
 * the path's end, so the flow streams; at a moment `elapsed` on screen each is where its particle is after
 * `elapsed / slow` seconds of real flow, between two of its points. They are drawn as the markers are: solid where
 * in view, and a faint ghost where the part hides them (a closed duct's inside), and as children of the result mesh,
 * so Clip cuts them with it.
 */

/** Dots on each path. */
export const DOTS_PER_PATH = 6;
const SIZE_PX = 4;
const GHOST_OPACITY = 0.3;

/** CAD millimetres to the result's glTF space: (x, z, −y) in metres, as cadgen writes the part. */
const gltf = (x, y, z, out, at) => {
  out[at] = x / 1000;
  out[at + 1] = z / 1000;
  out[at + 2] = -y / 1000;
};

/**
 * Where each dot is after `seconds` of real flow: `(paths × DOTS_PER_PATH) × 3` glTF positions into `out`. A dot k of
 * a path whose trip lasts T is at (seconds + k T / DOTS_PER_PATH) mod T along it.
 */
export function tracerPositions(tracers, seconds, out = new Float32Array(tracers.paths.length * DOTS_PER_PATH * 3)) {
  let at = 0;
  for (const path of tracers.paths) {
    const points = path.length / 3;
    const trip = (points - 1) * tracers.step;
    for (let k = 0; k < DOTS_PER_PATH; k += 1) {
      const t = (((seconds + (k * trip) / DOTS_PER_PATH) % trip) + trip) % trip;
      const x = t / tracers.step;
      const i = Math.min(Math.floor(x), points - 2);
      const w = x - i;
      const a = 3 * i;
      gltf(path[a] + (path[a + 3] - path[a]) * w, path[a + 1] + (path[a + 4] - path[a + 1]) * w,
        path[a + 2] + (path[a + 5] - path[a + 2]) * w, out, at);
      at += 3;
    }
  }
  return out;
}

/** The tracers' dots under `parent` (the result mesh): `update(elapsed)` moves them, `style({ colour })` colours them. */
export function createFeaTracers(THREE, tracers, parent) {
  const positions = tracerPositions(tracers, 0);
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  const solid = new THREE.PointsMaterial({ size: SIZE_PX, sizeAttenuation: false, color: 0x18181b });
  const ghost = new THREE.PointsMaterial({ size: SIZE_PX, sizeAttenuation: false, color: 0x18181b, transparent: true,
    opacity: GHOST_OPACITY, depthTest: false, depthWrite: false });
  const group = new THREE.Group();
  group.name = "fea-tracers";
  for (const [material, order] of [[ghost, 10], [solid, 11]]) {
    const points = new THREE.Points(geometry, material);
    points.renderOrder = order;
    points.frustumCulled = false;
    points.raycast = () => {};
    group.add(points);
  }
  parent.add(group);
  return {
    object3D: group,
    update(elapsed) {
      tracerPositions(tracers, elapsed / tracers.slow, positions);
      geometry.getAttribute("position").needsUpdate = true;
    },
    style({ colour }) {
      solid.color.set(colour);
      ghost.color.set(colour);
    },
    dispose() {
      group.removeFromParent();
      geometry.dispose();
      solid.dispose();
      ghost.dispose();
    },
  };
}
