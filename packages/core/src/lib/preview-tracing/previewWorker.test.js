import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';

test('an input queued before the first worker frame does not prevent preparation', async t => {
  const names = ['self', 'postMessage', 'navigator', 'setTimeout'];
  const descriptors = new Map(names.map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  const tasks = [], messages = [];
  Object.defineProperty(globalThis, 'self', { configurable: true, value: {} });
  Object.defineProperty(globalThis, 'postMessage', { configurable: true, value: message => messages.push(message) });
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { gpu: {} } });
  Object.defineProperty(globalThis, 'setTimeout', { configurable: true, value: callback => { tasks.push(callback); return tasks.length; } });
  t.after(() => { for (const [name, descriptor] of descriptors) { if (descriptor) Object.defineProperty(globalThis, name, descriptor); else delete globalThis[name]; } });
  await import('./previewWorker.js');
  const camera = new THREE.PerspectiveCamera(); camera.updateMatrixWorld();
  const record = { type: camera.type, position: camera.position.toArray(), quaternion: camera.quaternion.toArray(), scale: camera.scale.toArray(),
    projection: camera.projectionMatrix.toArray(), projectionInverse: camera.projectionMatrixInverse.toArray() };
  const scene = { textures: [], materials: [], geometries: [], objects: [], floor: null, studio: { backdrop: null }, configuration: {},
    environmentRotation: [0, 0, 0, 'XYZ'], environmentIntensity: 1, toneMapping: 0, exposure: 1, clearAlpha: 0 };
  await self.onmessage({ data: { type: 'init', scene, width: 32, height: 32, camera: record } });
  await self.onmessage({ data: { type: 'input', width: 32, height: 32, camera: record, performance: { mode: 'auto', value: 50 } } });
  assert.ok(messages.some(message => message.type === 'input-ack'));
  assert.ok(tasks.length > 0); tasks.shift()();
  await Promise.resolve(); await Promise.resolve();
  assert.ok(messages.some(message => message.type === 'progress' && message.stage === 'admission'), 'the worker reached real scene admission');
  assert.ok(messages.some(message => message.type === 'error' && /empty scene/.test(message.message)));
});
