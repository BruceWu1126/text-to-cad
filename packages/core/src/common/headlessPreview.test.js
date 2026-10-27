import assert from 'node:assert/strict';
import test from 'node:test';
import { createHeadlessPreview } from './headlessPreview.js';

test('headless Preview waits for the complete denoised frame, reuses its controller, and rejects raster fallback', async () => {
  const previous = globalThis.document;
  const host = { appendChild(canvas) { canvas.parentNode = this; }, remove() {} };
  globalThis.document = { createElement: () => host };
  const canvas = { parentNode: null, nextSibling: null, remove() { this.parentNode = null; } };
  const viewport = { THREE: {}, renderer: { domElement: canvas }, scene: {}, model: { root: {} }, studioRuntime: {}, studioConfiguration: {} };
  const queue = [], invalidations = [], captured = {};
  let progress = { state: 'ready', samples: 4, denoisedSamples: 0 }, disposed = false, renders = 0, requestRender;
  const controller = { get progress() { return progress; }, get captureCanvas() { return captured; },
    invalidate: value => invalidations.push(value), dispose: () => { disposed = true; }, render() { renders++; } };
  try {
    const session = createHeadlessPreview(viewport, { createController: (_runtime, options) => { requestRender = options.requestRender; return controller; }, schedule: fn => (queue.push(fn), queue.length), cancel: () => {} });
    const first = session.renderFrame({});
    queue.shift()();
    assert.equal(renders, 1);
    progress = { state: 'ready', samples: 4, denoisedSamples: 4 };
    requestRender(); queue.shift()();
    assert.equal(await first, captured);
    progress = { state: 'fallback', samples: 4, denoisedSamples: 0, fallbackReason: 'device unavailable' };
    const second = session.renderFrame({}, { sceneChanged: true });
    queue.shift()();
    await assert.rejects(second, /device unavailable/);
    assert.deepEqual(invalidations, [{ transformsChanged: true }]);
    session.dispose();
    assert.equal(disposed, true);
    await assert.rejects(session.renderFrame({}), /closed/);
  } finally { globalThis.document = previous; }
});
