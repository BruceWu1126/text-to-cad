import assert from 'node:assert/strict';
import test from 'node:test';
import { readPreviewSamples } from './sampleReadback.js';

test('a stuck GPU sample readback fails even when other GPU work completes', async () => {
  let expire, cleared = false;
  const promise = readPreviewSamples({ getSampleCountsAsync: () => new Promise(() => {}) }, { setTimer: callback => { expire = callback; return 1; }, clearTimer: () => { cleared = true; } });
  expire();
  await assert.rejects(promise, /sample readback stopped making progress/);
  assert.equal(cleared, true);
});

test('a completed readback cancels its deadline and returns real counts', async () => {
  let cleared = false;
  const counts = await readPreviewSamples({ getSampleCountsAsync: async () => ({ min: 7 }) }, { setTimer: () => 1, clearTimer: () => { cleared = true; } });
  assert.deepEqual(counts, { min: 7 }); assert.equal(cleared, true);
});


test('undispatched pixels cannot satisfy a motion sample target', async () => {
  let returned = new Uint32Array([4, 4, 0, 0, 5]);
  const calls = [];
  const path = {
    renderer: { compute: (kernel) => calls.push(kernel), getArrayBufferAsync: async () => returned.buffer },
    sampleCountTarget: { width: 3, height: 2 }, sampleCountersStorage: {},
    primeSampleCountersKernel: { kernel: 'prime' },
    tallySampleCountsKernel: { kernel: 'tally', getDispatchSize: (width, height) => [width, height] },
  };
  const tracer = { _pathTracer: path };
  assert.deepEqual(await readPreviewSamples(tracer, { fullCoverage: true }), { min: 0, coveredPixels: 5, pixels: 6 });
  returned = new Uint32Array([4, 4, 0, 0, 6]);
  assert.deepEqual(await readPreviewSamples(tracer, { fullCoverage: true }), { min: 4, coveredPixels: 6, pixels: 6 });
  assert.deepEqual(calls, ['prime', 'tally', 'prime', 'tally']);
});
