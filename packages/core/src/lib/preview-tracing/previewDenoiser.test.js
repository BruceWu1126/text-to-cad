import test from 'node:test';
import assert from 'node:assert/strict';
import { PreviewDenoiser } from './previewDenoiser.js';

function fixture() {
  let rgbFinish, alphaFinish, rgbFail, alphaFail, aborted = 0, completed = 0, errors = 0, initializations = 0;
  let alphaStartedResolve;
  const schedules = [];
  const alphaStarted = new Promise(resolve => { alphaStartedResolve = resolve; });
  const rgbNetwork = { tileExecute(inputs) { schedules.push(inputs.scheduling); rgbFinish = inputs.done; rgbFail = inputs.error; return () => { aborted++; }; }, dispose() {} };
  const alphaNetwork = { tileExecute(inputs) {
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
    schedules, failRGB: () => rgbFail(new Error('device')), failAlpha: () => alphaFail(new Error('device')), counts: () => ({ aborted, completed, errors, initializations }) };
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
  const rgbNetwork = { tileExecute(input) { rgbDone = input.done; return () => {}; }, dispose() {} };
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
