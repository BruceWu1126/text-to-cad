import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import { inspectPreviewScene } from './scene.js';

test('admission counts a shared prototype once and keeps material groups', () => {
  const root = new THREE.Group(), geometry = new THREE.BoxGeometry();
  const material = [new THREE.MeshStandardMaterial({ color: 'red' }), new THREE.MeshStandardMaterial({ color: 'blue' })];
  geometry.groups.forEach((group, index) => { group.materialIndex = index % 2; });
  root.add(new THREE.Mesh(geometry, material), new THREE.Mesh(geometry, material));
  const before = Array.from(geometry.index.array);
  const result = inspectPreviewScene(root);
  assert.equal(result.reason, null);
  assert.equal(result.meshes.length, 2);
  assert.equal(result.geometries.size, 1);
  assert.equal(result.triangles, geometry.index.count / 3);
  assert.deepEqual(Array.from(geometry.index.array), before);
  assert.deepEqual(geometry.groups.map(group => group.materialIndex), [0, 1, 0, 1, 0, 1]);
  assert.equal(geometry.boundsTree, undefined);
});

test('admission rejects the whole scene when unsupported or over budget', () => {
  const root = new THREE.Group(), geometry = new THREE.BoxGeometry(), material = new THREE.MeshStandardMaterial();
  root.add(new THREE.Mesh(geometry, material), new THREE.InstancedMesh(geometry, material, 2));
  assert.equal(inspectPreviewScene(root).reason, 'animated geometry');
  root.remove(root.children[1]);
  assert.equal(inspectPreviewScene(root, { triangles: 1, vertices: 100, meshes: 100, materials: 100 }).reason, 'scene budget');
  root.children[0].material = new THREE.MeshBasicMaterial();
  assert.equal(inspectPreviewScene(root).reason, 'unsupported material');
});


test('prototype cache retains only unchanged private geometry and never disposes the source', async () => {
  const { createPreviewGeometryCache } = await import('./scene.js');
  const cache = createPreviewGeometryCache();
  const source = new THREE.BoxGeometry(), copy = source.clone();
  let released = 0, sourceReleased = 0;
  copy.addEventListener('dispose', () => released++);
  source.addEventListener('dispose', () => sourceReleased++);
  cache.set(source, copy);
  assert.equal(cache.get(source), copy);
  source.attributes.position.needsUpdate = true;
  assert.equal(cache.get(source), null);
  assert.equal(released, 1);
  assert.equal(sourceReleased, 0);
  cache.dispose();
});

test('texture admission rejects oversized images before GPU allocation', () => {
  const root = new THREE.Group();
  const texture = new THREE.Texture({ width: 4096, height: 4096 });
  root.add(new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial({ map: texture })));
  assert.equal(inspectPreviewScene(root).reason, 'texture budget');
});


test('visible non-mesh primitives keep the whole scene on raster', () => {
  const root = new THREE.Group();
  root.add(new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial()));
  const line = new THREE.LineSegments(new THREE.BufferGeometry(), new THREE.LineBasicMaterial());
  root.add(line);
  assert.equal(inspectPreviewScene(root).reason, 'unsupported primitive');
  line.visible = false;
  assert.equal(inspectPreviewScene(root).reason, null, 'hidden CAD edges do not prevent tracing');
});
