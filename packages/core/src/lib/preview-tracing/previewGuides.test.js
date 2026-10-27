import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import { createPreviewGuideScene } from './previewGuides.js';

test('guide scene expands instances without changing source materials, geometry or transforms', () => {
  const root = new THREE.Scene(), geometry = new THREE.BoxGeometry();
  const material = [new THREE.MeshPhysicalMaterial({ color: 'red' }), new THREE.MeshPhysicalMaterial({ color: 'blue' })];
  const source = new THREE.InstancedMesh(geometry, material, 2);
  source.position.set(4, 2, 1);
  const one = new THREE.Matrix4().makeRotationZ(0.3).setPosition(3, 4, 5);
  const two = new THREE.Matrix4().makeScale(1, 2, 3).setPosition(-2, 8, 6);
  source.setMatrixAt(0, one); source.setMatrixAt(1, two); root.add(source); root.updateMatrixWorld(true);
  const guides = createPreviewGuideScene(root);
  assert.equal(guides.scene.children.length, 2);
  guides.scene.children.forEach((mesh, i) => {
    const expected = new THREE.Matrix4().multiplyMatrices(source.matrixWorld, i ? two : one);
    mesh.matrix.elements.forEach((value, j) => assert.ok(Math.abs(value - expected.elements[j]) < 1e-5));
    assert.equal(mesh.geometry, geometry);
    assert.equal(mesh.material[0], material[0]);
    assert.equal(mesh.material[1], material[1]);
    assert.equal(mesh.isInstancedMesh, undefined);
  });
  let guideDisposed = 0, materialDisposed = 0, geometryDisposed = 0;
  guides.scene.children.forEach(mesh => mesh.addEventListener('dispose', () => guideDisposed++));
  material[0].addEventListener('dispose', () => materialDisposed++);
  geometry.addEventListener('dispose', () => geometryDisposed++);
  guides.dispose();
  assert.equal(guideDisposed, 2);
  assert.equal(materialDisposed, 0);
  assert.equal(geometryDisposed, 0);
  source.dispose(); geometry.dispose(); material.forEach(m => m.dispose());
});

test('textured guide variants distinguish UV channel and colorspace without mutating authored materials', () => {
  const root = new THREE.Scene(), geometry = new THREE.BoxGeometry();
  const linear = new THREE.Texture({ width: 2, height: 2 });
  const uv1 = linear.clone(); uv1.channel = 1;
  const srgb = linear.clone(); srgb.colorSpace = THREE.SRGBColorSpace;
  const materials = [linear, uv1, srgb].map(map => new THREE.MeshPhysicalMaterial({ map, color: '#abc123', roughness: 0.2 }));
  materials.forEach(material => root.add(new THREE.Mesh(geometry, material)));
  const guides = createPreviewGuideScene(root);
  const variants = guides.scene.children.map(mesh => mesh.material);
  assert.equal(new Set(variants.map(material => material.customProgramCacheKey())).size, 3);
  variants.forEach((material, i) => {
    assert.notEqual(material, materials[i]);
    assert.equal(material.map, materials[i].map, 'authored texture stays borrowed');
    assert.equal(material.color.getHex(), materials[i].color.getHex());
    assert.equal(material.roughness, materials[i].roughness);
  });
  let clonesDisposed = 0, sourceDisposed = 0;
  variants.forEach(material => material.addEventListener('dispose', () => clonesDisposed++));
  materials.forEach(material => material.addEventListener('dispose', () => sourceDisposed++));
  guides.dispose();
  assert.equal(clonesDisposed, 3);
  assert.equal(sourceDisposed, 0);
  geometry.dispose(); materials.forEach(material => material.dispose()); [linear, uv1, srgb].forEach(texture => texture.dispose());
});
