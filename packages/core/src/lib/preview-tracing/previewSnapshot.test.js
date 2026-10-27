import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import { shareGeometry } from '../render/sharedGeometry.js';
import { snapshotPreviewScene } from './previewSnapshot.js';
import { reconstructPreviewScene } from './previewReconstruct.js';

function fixture() {
  const scene = new THREE.Scene(), root = new THREE.Group(), geometry = new THREE.BoxGeometry();
  const red = new THREE.MeshPhysicalMaterial({ color: 0xff0000, transmission: 1, thickness: 0.4 });
  const blue = new THREE.MeshStandardMaterial({ color: 0x0000ff });
  const a = new THREE.Mesh(shareGeometry(THREE, geometry), [red, blue, red, blue, red, blue]);
  const b = new THREE.Mesh(shareGeometry(THREE, geometry), blue); b.position.x = 3;
  root.add(a, b); scene.add(root);
  const runtime = { modelGroup: root, scene, photographicStudio: null, previewConfiguration: {}, renderer: { domElement: { width: 320, height: 240 }, toneMapping: 0, toneMappingExposure: 1, getClearAlpha: () => 0 } };
  return { runtime, geometry, a, b };
}

test('worker transfer owns geometry copies and preserves shared prototypes, per-face materials and transforms', async () => {
  const { runtime, geometry, a, b } = fixture();
  const snapshot = await snapshotPreviewScene(runtime, { yieldWork: async () => {} });
  assert.equal(snapshot.payload.geometries.length, 1);
  const payload = structuredClone(snapshot.payload, { transfer: snapshot.transfers });
  assert.ok(geometry.attributes.position.array.byteLength > 0, 'the source buffer stays attached');
  const scene = reconstructPreviewScene(payload);
  assert.equal(scene.objects[0].geometry, scene.objects[1].geometry);
  assert.equal(scene.objects[0].material.length, 6);
  assert.equal(scene.objects[0].material[0].transmission, 1);
  assert.equal(scene.objects[0].material[0].thickness, 0.4);
  assert.equal(scene.objects[1].matrix.elements[12], 3);
  assert.equal(a.geometry.attributes.position, b.geometry.attributes.position);
  scene.dispose(); snapshot.lease.release();
});

test('a cancelled snapshot stops during bounded copying without detaching source data', async () => {
  const { runtime, geometry } = fixture(), abort = new AbortController();
  await assert.rejects(snapshotPreviewScene(runtime, { signal: abort.signal, yieldWork: async () => { abort.abort(); } }), { name: 'AbortError' });
  assert.ok(geometry.attributes.position.array.byteLength > 0);
});

test('resized shared floating-point textures keep channels, transforms and source pixels intact', async () => {
  const { runtime, geometry } = fixture();
  geometry.setAttribute('uv1', geometry.getAttribute('uv'));
  // The Preview geometry wrappers were made before this test adds UV1.
  for (const mesh of runtime.modelGroup.children) mesh.geometry.setAttribute('uv1', geometry.getAttribute('uv'));
  const width = 2050, height = 2, source = new Float32Array(width * height * 4);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const i = (y * width + x) * 4; source[i] = x / width; source[i + 1] = y; source[i + 2] = 0.25; source[i + 3] = 1;
  }
  const texture = new THREE.DataTexture(source, width, height, THREE.RGBAFormat, THREE.FloatType);
  texture.colorSpace = THREE.SRGBColorSpace; texture.offset.set(0.2, 0.3); texture.repeat.set(2, 3); texture.rotation = 0.4;
  const second = texture.clone(); second.colorSpace = THREE.NoColorSpace; second.channel = 1;
  const material = runtime.modelGroup.children[0].material[0]; material.map = texture; material.roughnessMap = second;
  const snapshot = await snapshotPreviewScene(runtime, { yieldWork: async () => {} });
  assert.equal(snapshot.payload.textures.length, 2);
  assert.equal(snapshot.payload.textures[0].width, 2048);
  assert.equal(snapshot.payload.textures[0].data, snapshot.payload.textures[1].data, 'one private copy for a shared source');
  const payload = structuredClone(snapshot.payload, { transfer: snapshot.transfers });
  assert.equal(source.byteLength, width * height * 16);
  assert.equal(source[2], 0.25);
  const rebuilt = reconstructPreviewScene(payload), restored = rebuilt.objects[0].material[0];
  assert.equal(restored.map.source, restored.roughnessMap.source);
  assert.equal(restored.map.type, THREE.FloatType);
  assert.equal(restored.map.colorSpace, THREE.SRGBColorSpace);
  assert.equal(restored.roughnessMap.colorSpace, THREE.NoColorSpace);
  assert.equal(restored.roughnessMap.channel, 1);
  assert.deepEqual(restored.map.offset.toArray(), [0.2, 0.3]);
  assert.deepEqual(restored.map.repeat.toArray(), [2, 3]);
  assert.equal(restored.map.rotation, 0.4);
  assert.equal(restored.map.image.data[2], 0.25);
  assert.ok(restored.map.image.data[0] >= 0 && restored.map.image.data[0] <= 1);
  rebuilt.dispose(); snapshot.lease.release();
});
