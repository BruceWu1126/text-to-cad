import test from 'node:test';
import assert from 'node:assert/strict';
import { PreviewDenoiser } from './previewDenoiser.js';

function fixture() {
  let finish, fail, aborted = 0, completed = 0, errors = 0;
  const network = { tileExecute(inputs) { finish = inputs.done; fail = inputs.error; return () => { aborted++; }; }, dispose() {} };
  const denoiser = new PreviewDenoiser({ initUNetFromURL: async () => network, auxWeightsUrl: 'local' }, {
    onComplete: () => completed++, onError: () => errors++,
  });
  const gpuTexture = { createView() {} };
  denoiser.init({ initTexture() {}, backend: { device: {}, get() { return { texture: gpuTexture }; } } });
  denoiser._renderAuxiliaryBuffers = () => ({ textures: [{}, {}] });
  denoiser._copyOutputToTexture = () => { denoiser._texture = { dispose() {} }; };
  return { denoiser, finish: () => finish({}), fail: () => fail(new Error('device')), counts: () => ({ aborted, completed, errors }) };
}

test('only a complete denoise pass becomes presentable', async () => {
  const f = fixture();
  await f.denoiser.run({ width: 2, height: 2 });
  assert.equal(f.denoiser.status, 'denoising');
  assert.equal(f.denoiser.texture, null);
  f.finish();
  assert.equal(f.denoiser.status, 'complete');
  assert.ok(f.denoiser.texture);
  assert.equal(f.counts().completed, 1);
  f.denoiser.dispose();
});

test('reset cancels an in-flight pass and ignores late callbacks', async () => {
  const f = fixture();
  await f.denoiser.run({ width: 2, height: 2 });
  f.denoiser.reset();
  f.finish(); f.fail();
  assert.deepEqual(f.counts(), { aborted: 1, completed: 0, errors: 0 });
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

test('a later pass retains the previous denoised image until the new one completes', async () => {
  const f = fixture();
  await f.denoiser.run({ width: 2, height: 2 });
  f.finish();
  const first = f.denoiser.texture;
  await f.denoiser.run({ width: 2, height: 2 });
  assert.equal(f.denoiser.texture, first);
  f.finish();
  assert.notEqual(f.denoiser.texture, first);
  assert.equal(f.counts().completed, 2);
  f.denoiser.dispose();
});
