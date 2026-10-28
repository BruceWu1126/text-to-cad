import test from 'node:test';
import assert from 'node:assert/strict';
import { PreviewDenoiser } from './previewDenoiser.js';

function fixture() {
  const rgbStats = { tileCount: 20, durationMs: 300, inputPixelCount: 3179520, tileTimeMs: { min: 8, median: 10, mean: 11, max: 16 } };
  const alphaStats = { tileCount: 20, durationMs: 310, inputPixelCount: 3179520, tileTimeMs: { min: 9, median: 11, mean: 12, max: 18 } };
  let rgbFinish, alphaFinish, rgbFail, alphaFail, aborted = 0, completed = 0, errors = 0, initializations = 0;
  let alphaStartedResolve;
  const schedules = [];
  const alphaStarted = new Promise(resolve => { alphaStartedResolve = resolve; });
  const rgbNetwork = { getRuntimeInfo: () => ({ lastExecution: rgbStats }), tileExecute(inputs) { schedules.push(inputs.scheduling); rgbFinish = inputs.done; rgbFail = inputs.error; return () => { aborted++; }; }, dispose() {} };
  const alphaNetwork = { getRuntimeInfo: () => ({ lastExecution: alphaStats }), tileExecute(inputs) {
    assert.equal(inputs.denoiseAlpha, true);
    schedules.push(inputs.scheduling);
    alphaFinish = inputs.done; alphaFail = inputs.error; alphaStartedResolve();
    return () => { aborted++; };
  }, dispose() {} };
  const denoiser = new PreviewDenoiser({ initUNetFromURL: async () => initializations++ === 0 ? rgbNetwork : alphaNetwork, auxWeightsUrl: 'local' }, {
    onComplete: () => completed++, onError: () => errors++,
  });
  const gpuTexture = { createView() {} };
  denoiser.init({ initTexture() {}, backend: { device: {}, get() { return { texture: gpuTexture }; } } });
  denoiser._renderAuxiliaryBuffers = () => ({ textures: [{}, {}] });
  denoiser._copyOutputToTexture = (rgb, alpha) => { assert.equal(rgb.label, 'rgb'); assert.equal(alpha.label, 'alpha'); denoiser._texture = { dispose() {} }; };
  return { denoiser, finishRGB: () => rgbFinish({ label: 'rgb' }), finishAlpha: () => alphaFinish({ label: 'alpha' }), alphaStarted,
    schedules, rgbStats, alphaStats, failRGB: () => rgbFail(new Error('device')), failAlpha: () => alphaFail(new Error('device')), counts: () => ({ aborted, completed, errors, initializations }) };
}

async function finish(f) { f.finishRGB(); await f.alphaStarted; f.finishAlpha(); }

test('only the completed RGB and alpha passes become presentable', async () => {
  const f = fixture();
  await f.denoiser.run({ width: 2, height: 2 });
  assert.equal(f.denoiser.status, 'denoising');
  assert.equal(f.denoiser.texture, null);
  f.finishRGB(); await f.alphaStarted;
  assert.equal(f.denoiser.status, 'denoising-alpha');
  assert.equal(f.denoiser.texture, null, 'RGB alone is not presented');
  f.finishAlpha();
  assert.equal(f.denoiser.status, 'complete');
  assert.ok(f.denoiser.texture);
  assert.equal(f.counts().completed, 1);
  assert.equal(f.counts().initializations, 2, 'RGB and alpha use separate network shader state');
  f.denoiser.dispose();
});

test('interactive scheduling applies to both guided passes', async () => {
  const f = fixture();
  await f.denoiser.run({ width: 2, height: 2 }, { scheduling: 'event-loop' });
  await finish(f);
  assert.deepEqual(f.schedules, ['event-loop', 'event-loop']);
  f.denoiser.dispose();
});

test('reset cancels an in-flight RGB pass and ignores late callbacks', async () => {
  const f = fixture();
  await f.denoiser.run({ width: 2, height: 2 });
  f.denoiser.reset();
  f.finishRGB(); f.failRGB();
  assert.deepEqual(f.counts(), { aborted: 1, completed: 0, errors: 0, initializations: 1 });
  assert.equal(f.denoiser.texture, null);
  f.denoiser.dispose();
});

test('reset also cancels alpha and ignores a late result', async () => {
  const f = fixture();
  await f.denoiser.run({ width: 2, height: 2 });
  f.finishRGB(); await f.alphaStarted;
  f.denoiser.reset(); f.finishAlpha(); f.failAlpha();
  assert.deepEqual(f.counts(), { aborted: 1, completed: 0, errors: 0, initializations: 2 });
  assert.equal(f.denoiser.texture, null);
  f.denoiser.dispose();
});

test('initialization failure reports a fallback without an unhandled rejection', async () => {
  let errors = 0;
  const d = new PreviewDenoiser({ initUNetFromURL: async () => { throw new Error('bad weights'); }, auxWeightsUrl: 'local' }, { onError: () => errors++ });
  d.init({ backend: { device: {} } });
  d._renderAuxiliaryBuffers = () => ({ textures: [{}, {}] });
  await d.run({ width: 2, height: 2 });
  assert.equal(d.status, 'failed');
  assert.equal(errors, 1);
  d.dispose();
});

test('a later pass retains the previous denoised image until both new passes complete', async () => {
  const f = fixture();
  await f.denoiser.run({ width: 2, height: 2 });
  await finish(f);
  const first = f.denoiser.texture;
  await f.denoiser.run({ width: 2, height: 2 });
  assert.equal(f.denoiser.texture, first);
  f.finishRGB(); await Promise.resolve();
  assert.equal(f.denoiser.texture, first);
  f.finishAlpha();
  assert.notEqual(f.denoiser.texture, first);
  assert.equal(f.counts().completed, 2);
  f.denoiser.dispose();
});

test('reset while the alpha network loads prevents a late alpha pass', async () => {
  let rgbDone, releaseAlpha, requested, alphaRuns = 0, completed = 0, errors = 0, calls = 0;
  const requestedAlpha = new Promise(resolve => { requested = resolve; });
  const delayedAlpha = new Promise(resolve => { releaseAlpha = resolve; });
  const rgbNetwork = { getRuntimeInfo: () => ({ lastExecution: rgbStats }), tileExecute(input) { rgbDone = input.done; return () => {}; }, dispose() {} };
  const alphaNetwork = { tileExecute() { alphaRuns++; return () => {}; }, dispose() {} };
  const denoiser = new PreviewDenoiser({ auxWeightsUrl: 'local', initUNetFromURL: async () => {
    if (calls++ === 0) return rgbNetwork;
    requested(); return delayedAlpha;
  } }, { onComplete: () => completed++, onError: () => errors++ });
  denoiser.init({ initTexture() {}, backend: { device: {}, get() { return { texture: {} }; } } });
  denoiser._renderAuxiliaryBuffers = () => ({ textures: [{}, {}] });
  await denoiser.run({ width: 2, height: 2 });
  rgbDone({ label: 'rgb' });
  await requestedAlpha;
  denoiser.reset();
  releaseAlpha(alphaNetwork);
  await delayedAlpha; await Promise.resolve(); await Promise.resolve();
  assert.equal(alphaRuns, 0);
  assert.equal(completed, 0);
  assert.equal(errors, 0);
  assert.equal(denoiser.texture, null);
  denoiser.dispose();
});


test('completed RGB and alpha diagnostics capture independent tile execution summaries', async () => {
  const f = fixture();
  await f.denoiser.run({ width: 2, height: 2 });
  assert.equal(f.denoiser.timings.rgbExecution, null);
  f.finishRGB(); await f.alphaStarted;
  assert.deepEqual(f.denoiser.timings.rgbExecution, f.rgbStats);
  assert.equal(f.denoiser.timings.alphaExecution, null);
  const capturedMean = f.denoiser.timings.rgbExecution.tileTimeMs.mean;
  f.rgbStats.tileTimeMs.mean = 999;
  assert.equal(f.denoiser.timings.rgbExecution.tileTimeMs.mean, capturedMean, 'published stats do not borrow the library record');
  f.finishAlpha();
  assert.deepEqual(f.denoiser.timings.alphaExecution, f.alphaStats);
  await f.denoiser.run({ width: 2, height: 2 });
  assert.equal(f.denoiser.timings.rgbExecution, null);
  assert.equal(f.denoiser.timings.alphaExecution, null, 'a new picture cannot report a previous alpha pass as current');
  f.denoiser.dispose();
});


test('reset while the motion filter fence is pending rejects its stale output', async () => {
  const f = fixture();
  let finishFence;
  f.denoiser.renderer.backend.device.queue = { onSubmittedWorkDone: () => new Promise(resolve => { finishFence = resolve; }) };
  f.denoiser.fastFilter = { texture: { label: 'spatial' }, apply() {}, dispose() {} };
  const pending = f.denoiser.runFast({ width: 2, height: 2 });
  assert.equal(f.denoiser.running, true);
  assert.equal(f.denoiser.texture, null);
  f.denoiser.reset(); finishFence(); await pending;
  assert.equal(f.denoiser.texture, null);
  assert.equal(f.counts().completed, 0, 'cancelled output is never presented');
  f.denoiser.dispose();
});

test('motion filter publishes only after its GPU submission completes', async () => {
  const f = fixture();
  let finishFence;
  f.denoiser.renderer.backend.device.queue = { onSubmittedWorkDone: () => new Promise(resolve => { finishFence = resolve; }) };
  f.denoiser.fastFilter = { texture: { label: 'spatial' }, apply() {}, dispose() {} };
  const pending = f.denoiser.runFast({ width: 2, height: 2 });
  assert.equal(f.denoiser.texture, null);
  finishFence(); await pending;
  assert.equal(f.denoiser.texture.label, 'spatial');
  assert.equal(f.denoiser.filterMode, 'spatial');
  assert.equal(f.counts().completed, 1);
  f.denoiser.dispose();
});
