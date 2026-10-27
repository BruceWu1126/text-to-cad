import test from 'node:test';
import assert from 'node:assert/strict';
import { setImmediate } from 'node:timers/promises';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { createProgressivePreview } from './progressivePreview.js';

function setup(t, snapshotScene) {
  const navigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { gpu: {} } });
  const previous = { document: globalThis.document, Worker: globalThis.Worker, OffscreenCanvas: globalThis.OffscreenCanvas };
  const nodes = [], listeners = new Map();
  globalThis.document = { addEventListener: (name, callback) => listeners.set(name, callback), removeEventListener: name => listeners.delete(name), visibilityState: 'visible', createElement: () => ({ style: {}, width: 1, height: 1,
    getContext: () => ({ clearRect() {}, drawImage() {} }), remove() { nodes.splice(nodes.indexOf(this), 1); } }) };
  globalThis.Worker = class {}; globalThis.OffscreenCanvas = class {};
  t.after(() => { for (const [key, value] of Object.entries(previous)) { if (value === undefined) delete globalThis[key]; else globalThis[key] = value; } if (navigatorDescriptor) Object.defineProperty(globalThis, 'navigator', navigatorDescriptor); else delete globalThis.navigator; });
  const root = new THREE.Group(), scene = new THREE.Scene(); scene.add(root);
  const element = { width: 320, height: 240, style: { opacity: '0.7' }, parentElement: { appendChild: node => nodes.push(node) } };
  const runtime = { modelGroup: root, scene, renderer: { domElement: element }, previewConfiguration: {} };
  const camera = new THREE.PerspectiveCamera(); camera.updateMatrixWorld();
  const messages = [], workers = [], timers = new Map(), failures = [];
  let next = 0;
  const workerFactory = () => {
    const worker = { terminated: false, postMessage(message) { messages.push(message); if (message.type === 'probe') queueMicrotask(() => this.onmessage?.({ data: { type: 'probe', limits: {} } })); }, terminate() { this.terminated = true; }, emit(data) { this.onmessage({ data }); } };
    workers.push(worker); return worker;
  };
  const controller = createProgressivePreview(runtime, { workerFactory, snapshotScene, requestRender() {}, onFailure: error => failures.push(error.message),
    setTimer: callback => { const id = ++next; timers.set(id, callback); return id; }, clearTimer: id => timers.delete(id) });
  return { controller, runtime, camera, workers, messages, timers, failures, nodes, element, listeners };
}
const snapshot = async () => ({ payload: {}, transfers: [], sourceMeshes: [] });

test('a stalled worker is terminated and reports an actionable failure', async t => {
  const f = setup(t, snapshot);
  f.controller.render(null, null, f.camera); await setImmediate();
  assert.equal(f.workers.length, 1);
  const deadline = [...f.timers.values()].at(-1); deadline();
  assert.equal(f.workers[0].terminated, true);
  assert.match(f.failures[0], /stopped making progress/);
  assert.equal(f.controller.progress.state, 'fallback');
  f.controller.dispose();
});

test('disposing during a pending snapshot terminates the worker and closes untransferred images', async t => {
  let resolve; const pending = new Promise(done => { resolve = done; });
  const f = setup(t, () => pending); let closed = 0;
  f.controller.render(null, null, f.camera); await setImmediate(); f.controller.dispose();
  resolve({ payload: {}, transfers: [{ close() { closed++; } }], sourceMeshes: [] }); await setImmediate();
  assert.equal(closed, 1); assert.equal(f.workers[0].terminated, true); assert.equal(f.nodes.length, 0);
});

test('camera input is coalesced, stale frames are closed and exit restores the editor canvas', async t => {
  const f = setup(t, snapshot); f.controller.render(null, null, f.camera); await setImmediate();
  const worker = f.workers[0], frame = { width: 2, height: 2, close() { this.closed = true; } };
  worker.emit({ type: 'frame', bitmap: frame, progress: { state: 'ready', presentedFrames: 1, motion: true } });
  assert.equal(frame.closed, true); assert.equal(f.element.style.opacity, '0');
  f.camera.position.x = 1; f.controller.invalidate(); f.controller.render(null, null, f.camera, { moving: true });
  f.camera.position.x = 2; f.controller.invalidate(); f.controller.render(null, null, f.camera, { moving: true });
  f.camera.position.x = 3; f.controller.invalidate(); f.controller.render(null, null, f.camera, { moving: true });
  assert.equal(f.messages.filter(m => m.type === 'input').length, 1);
  worker.emit({ type: 'input-ack' });
  assert.equal(f.messages.filter(m => m.type === 'input').at(-1).camera.position[0], 3);
  const staleHandler = worker.onmessage;
  f.controller.dispose(); assert.equal(worker.terminated, true); assert.equal(f.element.style.opacity, '0.7');
  const stale = { close() { this.closed = true; } }; staleHandler({ data: { type: 'frame', bitmap: stale } });
  assert.equal(stale.closed, true); assert.equal(f.nodes.length, 0);
});


test('continuous camera input cannot hide a stalled GPU from the watchdog', async t => {
  const f = setup(t, snapshot); f.controller.render(null, null, f.camera); await setImmediate();
  const worker = f.workers[0], deadline = [...f.timers.values()].at(-1);
  for (let i = 0; i < 8; i++) {
    f.camera.position.x = i + 1; f.controller.invalidate(); f.controller.render(null, null, f.camera, { moving: true });
    worker.emit({ type: 'input-ack' });
  }
  assert.equal([...f.timers.values()].at(-1), deadline);
  deadline(); assert.equal(worker.terminated, true); assert.match(f.failures[0], /stopped making progress/);
  f.controller.dispose();
});

test('a hidden tab pauses immediately even when no render frame is scheduled', async t => {
  const f = setup(t, snapshot); f.controller.render(null, null, f.camera); await setImmediate();
  const worker = f.workers[0];
  worker.emit({ type: 'frame', bitmap: { width: 1, height: 1, close() {} }, progress: { state: 'ready', motion: true } });
  globalThis.document.visibilityState = 'hidden'; f.listeners.get('visibilitychange')();
  assert.deepEqual(f.messages.at(-1), { type: 'pause', paused: true }); assert.equal(f.timers.size, 0);
  globalThis.document.visibilityState = 'visible'; f.listeners.get('visibilitychange')();
  assert.deepEqual(f.messages.at(-1), { type: 'pause', paused: false });
  f.controller.dispose(); assert.equal(f.listeners.size, 0);
});

test('a failed worker post closes images that never crossed the boundary', async t => {
  let closed = 0, released = 0;
  const f = setup(t, async () => ({ payload: {}, transfers: [{ close() { closed++; } }], sourceMeshes: [], lease: { release() { released++; } } }));
  // A terminated canvas or a structured clone failure can throw synchronously.
  f.element.parentElement.appendChild = () => { throw new Error('presentation failed'); };
  f.controller.render(null, null, f.camera); await setImmediate();
  assert.equal(closed, 1); assert.equal(released, 1); assert.match(f.failures[0], /presentation failed/);
  f.controller.dispose();
});


test('OrbitControls last-bit jitter does not reset a settled Preview, while real movement still sends', async t => {
  const f = setup(t, snapshot), c = f.camera;
  c.up.set(0, 0, 1); c.position.set(-90.5426852290311, 2.532646328580711, -294.2220822220386);
  const controls = new OrbitControls(c); controls.target.set(1.714666264214979, 8.932812459486478, 2.2233599428971984);
  controls.update(); f.runtime.controls = { target: controls.target }; f.controller.render(null, null, c); await setImmediate();
  const worker = f.workers[0]; f.controller.render(null, null, c); worker.emit({ type: 'input-ack' });
  for (let i = 0; i < 1000; i++) {
    controls.update();
    f.controller.render(null, null, c); worker.emit({ type: 'input-ack' });
  }
  // Deterministically reproduce observed post-drag drift without depending on
  // platform-specific floating-point behavior in OrbitControls.
  const basePosition = c.position.clone(), baseQuaternion = c.quaternion.clone(), baseNear = c.near;
  for (let i = 0; i < 30; i++) {
    const t = i / 29;
    c.position.set(basePosition.x + t * 3e-6, basePosition.y - t * 2.5e-5, basePosition.z - t * 1.3e-5);
    c.quaternion.set(baseQuaternion.x + t * 3e-8, baseQuaternion.y, baseQuaternion.z - t * 3e-8, baseQuaternion.w);
    c.near = baseNear * (1 + t * 1.2e-6); c.updateProjectionMatrix();
    f.controller.render(null, null, c); worker.emit({ type: 'input-ack' });
  }
  assert.equal(f.messages.filter(message => message.type === 'input').length, 1, 'observed subpixel drift does not restart accumulation');
  const step = c.position.distanceTo(controls.target) * 4e-7;
  for (let i = 0; i < 6; i++) { c.position.x += step; f.controller.render(null, null, c); worker.emit({ type: 'input-ack' }); }
  const accumulated = f.messages.filter(message => message.type === 'input').length;
  assert.ok(accumulated > 1, 'small actual movements accumulate against the last sent view');
  c.position.x += 0.01; f.controller.invalidate(); f.controller.render(null, null, c);
  assert.equal(f.messages.filter(message => message.type === 'input').length, accumulated + 1, 'explicit movement still updates the view');
  f.controller.dispose();
});
