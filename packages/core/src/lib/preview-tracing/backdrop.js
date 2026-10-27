import * as THREE from 'three';

const smooth = (a, b, x) => { const t = Math.max(0, Math.min(1, (x - a) / (b - a))); return t * t * (3 - 2 * t); };

// The studio backdrop is a screen-space shader in the raster renderer. Recreate its
// gradient on a private canvas behind the transparent trace, including its alpha; it is
// never the raster canvas (which also contains the model).
export function drawPreviewBackdrop(canvas, camera, studio, width, height, opacity = 1) {
  const scale = Math.min(1, 256 / Math.max(width, height));
  const w = Math.max(1, Math.round(width * scale)), h = Math.max(1, Math.round(height * scale));
  canvas.width = w; canvas.height = h;
  const context = canvas.getContext('2d');
  const image = context.createImageData(w, h), pixels = image.data;
  const uniforms = studio?.backdrop?.uniforms;
  const color = uniforms?.uColor?.value || new THREE.Vector3(1, 1, 1);
  const shape = uniforms?.uShape?.value || new THREE.Vector3(0, 0, 0);
  if (studio?.backdrop?.object?.visible === false) opacity = 0;
  const projection = camera.projectionMatrix.elements;
  const aspect = Math.abs(projection[0]) > 1e-12 ? Math.abs(projection[5] / projection[0]) : 1;
  const cameraRotation = new THREE.Matrix4().extractRotation(camera.matrixWorld);
  const near = new THREE.Vector3(), far = new THREE.Vector3();
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const sx = 2 * (x + 0.5) / w - 1, sy = 1 - 2 * (y + 0.5) / h;
    near.set(sx, sy, -1).applyMatrix4(camera.projectionMatrixInverse);
    far.set(sx, sy, 1).applyMatrix4(camera.projectionMatrixInverse).sub(near).transformDirection(cameraRotation);
    const reach = Math.min(1, Math.hypot(sx * aspect, sy - 0.12) / Math.hypot(aspect, 1.12));
    const glow = 1 - smooth(0, 0.75, reach);
    const corner = smooth(0.3, 1, reach);
    const wall = smooth(0, 0.55, far.z);
    const shade = 1 - shape.y * Math.min(1, corner + shape.z * wall);
    const lift = shape.x * glow * glow;
    const offset = (y * w + x) * 4;
    pixels[offset] = Math.round(255 * Math.max(0, Math.min(1, (color.x + (1 - color.x) * lift) * shade)));
    pixels[offset + 1] = Math.round(255 * Math.max(0, Math.min(1, (color.y + (1 - color.y) * lift) * shade)));
    pixels[offset + 2] = Math.round(255 * Math.max(0, Math.min(1, (color.z + (1 - color.z) * lift) * shade)));
    pixels[offset + 3] = Math.round(255 * opacity);
  }
  context.putImageData(image, 0, 0);
}
