import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import { inspectPreviewScene, createPreviewFloorMaterial } from './scene.js';

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


test('traced floor uses configured physical albedo and receives light instead of emitting it', () => {
  const source = new THREE.MeshStandardMaterial({
    color: new THREE.Color('#e7e7e5').multiplyScalar(0.25), emissive: '#e7e7e5', emissiveIntensity: 0.85,
    opacity: 0.6, transparent: true, roughness: 0.35, envMapIntensity: 0.22,
  });
  const before = source.clone();
  const physical = createPreviewFloorMaterial(source, { backdrop: { groundColor: '#e7e7e5' } });
  assert.equal(physical.color.getHexString(), 'e7e7e5');
  assert.equal(physical.emissive.getHex(), 0);
  assert.equal(physical.emissiveIntensity, 0);
  assert.deepEqual([physical.opacity, physical.transparent, physical.roughness], [0.6, true, 0.35], 'floor controls remain authoritative');
  assert.deepEqual(source.color, before.color);
  assert.deepEqual(source.emissive, before.emissive);
  assert.equal(source.emissiveIntensity, before.emissiveIntensity, 'the immediate raster studio keeps its own material');
  physical.dispose(); source.dispose(); before.dispose();
});


test('unsupported surface relief keeps the authored silhouette and bump shading on raster', () => {
  for (const channel of ['displacementMap', 'bumpMap']) {
    const texture = new THREE.Texture({ width: 4, height: 4 });
    const material = new THREE.MeshStandardMaterial({ [channel]: texture });
    const geometry = new THREE.BoxGeometry();
    const root = new THREE.Group(); root.add(new THREE.Mesh(geometry, material));
    assert.equal(inspectPreviewScene(root).reason, 'unsupported material', channel);
    assert.equal(material[channel], texture, 'admission does not strip the authored effect');
    geometry.dispose(); material.dispose(); texture.dispose();
  }
});

test('a clearcoat normal map requires the same tangent basis as the base normal map', () => {
  const texture = new THREE.Texture({ width: 4, height: 4 });
  const material = new THREE.MeshPhysicalMaterial({ clearcoat: 1, clearcoatNormalMap: texture });
  const geometry = new THREE.BoxGeometry();
  const root = new THREE.Group(); root.add(new THREE.Mesh(geometry, material));
  assert.equal(inspectPreviewScene(root).reason, 'unsupported material');
  geometry.computeTangents();
  assert.equal(inspectPreviewScene(root).reason, null);
  geometry.dispose(); material.dispose(); texture.dispose();
});


test('object-space normal maps fall back instead of being interpreted in tangent space', () => {
  const texture = new THREE.Texture({ width: 4, height: 4 });
  const geometry = new THREE.BoxGeometry(); geometry.computeTangents();
  const material = new THREE.MeshStandardMaterial({ normalMap: texture, normalMapType: THREE.ObjectSpaceNormalMap });
  const root = new THREE.Group(); root.add(new THREE.Mesh(geometry, material));
  assert.equal(inspectPreviewScene(root).reason, 'unsupported material');
  material.normalMapType = THREE.TangentSpaceNormalMap;
  assert.equal(inspectPreviewScene(root).reason, null);
  geometry.dispose(); material.dispose(); texture.dispose();
});

test('a texture never substitutes another UV channel when its authored channel is absent', () => {
  const texture = new THREE.Texture({ width: 4, height: 4 }); texture.channel = 1;
  const geometry = new THREE.BoxGeometry();
  const material = new THREE.MeshStandardMaterial({ map: texture });
  const root = new THREE.Group(); root.add(new THREE.Mesh(geometry, material));
  assert.equal(inspectPreviewScene(root).reason, 'unsupported material');
  geometry.setAttribute('uv1', geometry.getAttribute('uv').clone());
  assert.equal(inspectPreviewScene(root).reason, null);
  geometry.dispose(); material.dispose(); texture.dispose();
});

test('UV-oriented anisotropy requires an authored tangent while bare CAD anisotropy remains supported', () => {
  const geometry = new THREE.BoxGeometry(), uv = geometry.getAttribute('uv');
  const material = new THREE.MeshPhysicalMaterial({ anisotropy: 0.4 });
  const root = new THREE.Group(); root.add(new THREE.Mesh(geometry, material));
  assert.equal(inspectPreviewScene(root).reason, 'unsupported material');
  geometry.deleteAttribute('uv');
  assert.equal(inspectPreviewScene(root).reason, null, 'untextured CAD keeps its normal-based anisotropic finish');
  geometry.setAttribute('uv', uv); geometry.computeTangents();
  assert.equal(inspectPreviewScene(root).reason, null);
  geometry.dispose(); material.dispose();
});

test('private batches preserve prototype groups, material identities and world transforms', async () => {
  const { groupPreviewMeshes, createPreviewObjects } = await import('./scene.js');
  const root = new THREE.Group(), carrier = new THREE.Group();
  const geometry = new THREE.BoxGeometry();
  const a = new THREE.MeshStandardMaterial({ color: 'red' }), b = new THREE.MeshStandardMaterial({ color: 'blue' });
  const first = new THREE.Mesh(geometry, [a, b]);
  const second = new THREE.Mesh(geometry, [a, b]);
  const different = new THREE.Mesh(geometry, [b, a]);
  const mirrored = new THREE.Mesh(geometry, [a, b]);
  carrier.position.set(11, 3, -2); carrier.rotation.z = 0.3;
  first.position.set(1, 2, 3); first.scale.set(2, 0.5, 1.5);
  second.position.set(-4, 5, 6); second.rotation.x = 0.7;
  mirrored.scale.x = -1;
  root.add(carrier, mirrored, different); carrier.add(first, second); root.updateMatrixWorld(true);
  const originalIndex = Array.from(geometry.index.array), originalGroups = structuredClone(geometry.groups);
  const groups = groupPreviewMeshes([first, second, mirrored, different]);
  const privateGeometry = geometry.clone();
  const objects = createPreviewObjects(groups, new Map([[geometry, privateGeometry]]));
  const batch = objects.find(object => object.isInstancedMesh);
  assert.equal(batch.count, 2);
  assert.deepEqual(batch.material, [a, b]);
  assert.deepEqual(batch.geometry.groups, originalGroups);
  const matrix = new THREE.Matrix4();
  const sameMatrix = expected => matrix.elements.forEach((value, i) => assert.ok(Math.abs(value - expected.elements[i]) < 1e-5));
  batch.getMatrixAt(0, matrix); sameMatrix(first.matrixWorld);
  batch.getMatrixAt(1, matrix); sameMatrix(second.matrixWorld);
  const mirrorCopy = objects.find(object => !object.isInstancedMesh && object.matrix.determinant() < 0);
  assert.deepEqual(mirrorCopy.matrix.elements, mirrored.matrixWorld.elements);
  assert.equal(objects.length, 3, 'mirrored placements and different slot ordering remain independent');
  assert.deepEqual(Array.from(geometry.index.array), originalIndex);
  assert.deepEqual(geometry.groups, originalGroups);
  assert.equal(first.geometry, geometry);
  batch.dispose(); privateGeometry.dispose(); geometry.dispose(); a.dispose(); b.dispose();
});

test('thousands of repeated placements are admitted against packed size and actual device limits', async () => {
  const { previewSceneBudget, previewPreparedBudget, groupPreviewMeshes } = await import('./scene.js');
  const { MeshBVH } = await import('three-mesh-bvh');
  const root = new THREE.Group(), geometry = new THREE.BoxGeometry(), material = new THREE.MeshStandardMaterial();
  for (let i = 0; i < 2400; i++) {
    const mesh = new THREE.Mesh(geometry, material);
    mesh.position.x = i * 2; root.add(mesh);
  }
  const admission = inspectPreviewScene(root);
  assert.equal(admission.reason, null);
  assert.equal(admission.geometries.size, 1);
  assert.equal(groupPreviewMeshes(admission.meshes).length, 1);
  assert.equal(previewSceneBudget(admission, undefined, { maxStorageBufferBindingSize: 512, maxBufferSize: 512 }), 'scene budget');
  const privateGeometry = geometry.clone();
  privateGeometry.boundsTree = new MeshBVH(privateGeometry, { targetLeafSize: 5 });
  assert.equal(previewPreparedBudget(admission, groupPreviewMeshes(admission.meshes), new Map([[geometry, privateGeometry]]),
    { maxStorageBufferBindingSize: 128 * 1024 * 1024, maxBufferSize: 256 * 1024 * 1024 }), null);
  privateGeometry.dispose(); geometry.dispose(); material.dispose();
});

test('shear and tangent-mapped placements keep individual guide geometry', async () => {
  const { groupPreviewMeshes } = await import('./scene.js');
  const root = new THREE.Group(), parent = new THREE.Group(), geometry = new THREE.BoxGeometry();
  const plain = new THREE.MeshStandardMaterial(), normalMap = new THREE.Texture({ width: 4, height: 4 });
  const mapped = new THREE.MeshStandardMaterial({ normalMap });
  parent.scale.set(2, 1, 1); root.add(parent);
  const sheared = new THREE.Mesh(geometry, plain); sheared.rotation.z = 0.7; parent.add(sheared);
  const a = new THREE.Mesh(geometry, mapped), b = new THREE.Mesh(geometry, mapped); root.add(a, b);
  root.updateMatrixWorld(true);
  const groups = groupPreviewMeshes([sheared, a, b]);
  assert.deepEqual(groups.map(group => group.meshes.length), [1, 1, 1]);
  geometry.dispose(); plain.dispose(); mapped.dispose(); normalMap.dispose();
});

test('shared geometry wrappers reuse a prototype without merging distinct materials or changed attributes', async () => {
  const { shareGeometry, releaseGeometry } = await import('../render/sharedGeometry.js');
  const { groupPreviewMeshes, createPreviewObjects } = await import('./scene.js');
  const source = new THREE.BoxGeometry();
  source.setAttribute('color', new THREE.Float32BufferAttribute(new Float32Array(source.attributes.position.count * 3).fill(0.5), 3));
  const first = shareGeometry(THREE, source), second = shareGeometry(THREE, source);
  const graded = shareGeometry(THREE, source, { own: ['color'] });
  const regrouped = shareGeometry(THREE, source); regrouped.clearGroups();
  const a = new THREE.MeshStandardMaterial(), b = a.clone();
  const root = new THREE.Group();
  root.add(new THREE.Mesh(first, a), new THREE.Mesh(second, b), new THREE.Mesh(graded, a), new THREE.Mesh(regrouped, a));
  const admission = inspectPreviewScene(root);
  assert.equal(admission.reason, null);
  assert.equal(admission.geometries.size, 3, 'only equal shared wrappers share the same private prototype');
  assert.equal(admission.canonical.get(second), first);
  const privateMap = new Map([...admission.geometries].map(g => [g, g.clone()]));
  const groups = groupPreviewMeshes(admission.meshes, admission.canonical);
  const objects = createPreviewObjects(groups, privateMap);
  assert.equal(objects.length, 4, 'independently mutable material clones remain independent');
  assert.ok(objects.every(object => object.isInstancedMesh && object.count === 1));
  assert.equal(objects[0].geometry, objects[1].geometry, 'singleton instances reuse their private BVH');
  assert.notEqual(objects[0].material, objects[1].material);
  assert.equal(first.getAttribute('color'), source.getAttribute('color'));
  assert.notEqual(graded.getAttribute('color'), source.getAttribute('color'));
  for (const object of objects) object.dispose();
  for (const geometry of privateMap.values()) geometry.dispose();
  for (const geometry of [first, second, graded, regrouped]) releaseGeometry(geometry);
  source.dispose(); a.dispose(); b.dispose();
});

test('ambiguous material group ranges stay on raster rather than paint the wrong faces', () => {
  const root = new THREE.Group(), geometry = new THREE.BoxGeometry();
  const materials = [new THREE.MeshStandardMaterial(), new THREE.MeshStandardMaterial()];
  const mesh = new THREE.Mesh(geometry, materials); root.add(mesh);
  geometry.groups[1].start = geometry.groups[0].start + 3;
  assert.equal(inspectPreviewScene(root).reason, 'invalid material groups', 'overlapping groups cannot map to BVH roots');
  geometry.groups[1].start = 6;
  geometry.groups.reverse();
  assert.equal(inspectPreviewScene(root).reason, 'invalid material groups', 'unordered groups cannot map to BVH roots');
  geometry.groups.reverse(); geometry.groups[0].start = 3; geometry.groups[0].count = 3;
  mesh.material = materials[0];
  assert.equal(inspectPreviewScene(root).reason, 'invalid material groups', 'single material draws its whole geometry in raster');
  geometry.dispose(); materials.forEach(material => material.dispose());
});

test('admission budgets actual atlas page shape and colorspace variants', async () => {
  const { previewSceneBudget } = await import('./scene.js');
  const root = new THREE.Group(), geometry = new THREE.BoxGeometry();
  const strip = new THREE.Texture({ width: 2048, height: 1 });
  const second = strip.clone(); second.colorSpace = THREE.SRGBColorSpace;
  root.add(new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ map: strip, emissiveMap: second })));
  const admission = inspectPreviewScene(root);
  assert.equal(admission.textures.size, 2, 'same source in two colorspaces has two tracer atlas entries');
  assert.equal(previewSceneBudget(admission, { sceneBytes: 1024 * 1024 }), 'scene budget', 'a thin texture still allocates a full atlas page');
  geometry.dispose(); root.children[0].material.dispose(); strip.dispose(); second.dispose();
});
