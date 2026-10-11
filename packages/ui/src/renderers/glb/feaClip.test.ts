import * as THREE from 'three';
import { expect, it } from 'vitest';
import { createFeaClip } from '../../../dist/renderers/glb/feaClip.js';
import { pickFace, readFeaResult } from '../../../dist/renderers/glb/feaResult.js';

// A unit square in z = 0, placed by its parent 10 along X (the viewport's model offset), its two
// triangles on two faces of the source part.
function square() {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 1, 0]), 3));
  geometry.setIndex([0, 1, 2, 1, 3, 2]);
  geometry.setAttribute('color', new THREE.BufferAttribute(new Uint8Array(16), 4, true));
  geometry.setAttribute('_von_mises', new THREE.BufferAttribute(new Float32Array([0, 1, 2, 3]), 1));
  geometry.setAttribute('_face', new THREE.BufferAttribute(new Float32Array([0, 0, 0, 1]), 1));
  const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial());
  Object.assign(mesh.userData, { generator: 'cadgen fea', deformation_scale: 1, faces: ['#o1.f1', '#o1.f2'],
    fields: [{ attribute: '_VON_MISES', name: 'von Mises stress', units: 'MPa', min: 0, max: 3 }] });
  const root = new THREE.Group();
  root.add(mesh);
  const placed = new THREE.Group();
  placed.position.set(10, 0, 0);
  placed.add(root);
  placed.updateMatrixWorld(true);
  return { mesh, root };
}
const BOUNDS = { min: [0, 0, 0], max: [1, 1, 0] };
const half = (patch = {}) => ({ enabled: true, axis: 'x', offsets: { x: 0.5, y: 1, z: 1 }, invert: false, ...patch });
const drawn = (mesh: THREE.Mesh, material = mesh.material as THREE.Material) => {
  mesh.onBeforeRender(null as any, null as any, null as any, mesh.geometry, material, null as any);
  return material.clippingPlanes || [];
};

it('cuts where the slider says, in the space the mesh is drawn in, on whatever material it draws with', () => {
  const { mesh, root } = square();
  const clip = createFeaClip(THREE, mesh, root);
  expect(clip.set(half(), BOUNDS)).toBe(true);
  expect(clip.set(half(), BOUNDS)).toBe(false);
  const [plane] = drawn(mesh);
  // Halfway along X, moved with the model: the lower half kept.
  expect(plane.distanceToPoint(new THREE.Vector3(10.25, 0, 0))).toBeCloseTo(0.25, 9);
  expect(plane.distanceToPoint(new THREE.Vector3(10.75, 0, 0))).toBeCloseTo(-0.25, 9);
  // Inverted, the other half is kept.
  clip.set(half({ invert: true, offsets: { x: 0.5, y: 0, z: 0 } }), BOUNDS);
  expect(drawn(mesh)[0].distanceToPoint(new THREE.Vector3(10.75, 0, 0))).toBeCloseTo(0.25, 9);
  // A material the surface look swaps in is cut too; letting go clears every one it touched.
  const standIn = new THREE.MeshPhysicalMaterial();
  expect(drawn(mesh, standIn)).toHaveLength(1);
  clip.dispose();
  expect([(mesh.material as THREE.Material).clippingPlanes, standIn.clippingPlanes]).toEqual([null, null]);
});

it('cuts nothing while Clip is off or at its neutral end', () => {
  const { mesh, root } = square();
  const clip = createFeaClip(THREE, mesh, root);
  for (const settings of [null, half({ enabled: false }), half({ offsets: { x: 1, y: 1, z: 1 } })]) {
    clip.set(settings, BOUNDS);
    expect(drawn(mesh)).toHaveLength(0);
    expect(clip.plane()).toBeNull();
  }
});

it('a pick goes through what the cut took away to what it shows', () => {
  const { mesh, root } = square();
  const result = readFeaResult(root)!;
  const clip = createFeaClip(THREE, mesh, root);
  clip.set(half(), BOUNDS);
  const down = (x: number) => new THREE.Ray(new THREE.Vector3(x, 0.05, 1), new THREE.Vector3(0, 0, -1));
  expect(pickFace(result, down(10.1), clip.plane())?.ref).toBe('#o1.f1');
  expect(pickFace(result, down(10.9), clip.plane())).toBeNull();
  expect(pickFace(result, down(10.9))?.ref).toBe('#o1.f1');
});

it('cuts the markers under the mesh by the same plane, and says which of their points the cut keeps', () => {
  const { mesh, root } = square();
  // A marker built before the cut, and one added under the mesh after it (a rebuild): both cut on the mesh's next frame.
  const marker = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial());
  mesh.add(marker);
  const clip = createFeaClip(THREE, mesh, root);
  expect(clip.keeps([0.75, 0, 0], mesh)).toBe(true);
  clip.set(half(), BOUNDS);
  expect((marker.material as THREE.Material).clippingPlanes).toHaveLength(1);
  const later = new THREE.Mesh(new THREE.BufferGeometry(), [new THREE.MeshBasicMaterial(), new THREE.MeshBasicMaterial()]);
  mesh.add(later);
  drawn(mesh);
  expect((later.material as THREE.Material[]).map((material) => material.clippingPlanes?.length)).toEqual([1, 1]);
  // A label's point, in the mesh's space: kept on the lower half, gone with the upper one.
  expect(clip.keeps([0.25, 0.5, 0], mesh)).toBe(true);
  expect(clip.keeps([0.75, 0.5, 0], mesh)).toBe(false);
  // Flipped, the other half's.
  clip.set(half({ invert: true }), BOUNDS);
  expect([clip.keeps([0.25, 0.5, 0], mesh), clip.keeps([0.75, 0.5, 0], mesh)]).toEqual([false, true]);
  // No cut, nothing on the markers; letting go clears them.
  clip.set(null, BOUNDS);
  expect((marker.material as THREE.Material).clippingPlanes).toHaveLength(0);
  expect(clip.keeps([0.75, 0.5, 0], mesh)).toBe(true);
  clip.set(half(), BOUNDS);
  clip.dispose();
  expect((marker.material as THREE.Material).clippingPlanes).toBeNull();
});
