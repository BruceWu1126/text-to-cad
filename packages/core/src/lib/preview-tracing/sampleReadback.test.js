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
