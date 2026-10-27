import test from 'node:test';
import assert from 'node:assert/strict';
import { createPreviewQualityController } from './adaptiveQuality.js';
import { previewQualityProfile, withPreviewRayBudget } from './resourcePolicy.js';

test('sustained slow and noisy batches retain settled convergence dimensions and full motion coverage', () => {
  const controller = createPreviewQualityController({ viewport: { width: 1920, height: 1080 } });
  const initial = controller.profile;
  let smallest = initial.frameBudget;
  for (let i = 0; i < 160; i++) {
    const next = controller.observeBatch(i % 9 ? 50 : 4);
    smallest = Math.min(smallest, next.frameBudget);
    assert.deepEqual([next.width, next.height, next.samples], [initial.width, initial.height, initial.samples]);
    assert.ok(next.motionWidth * next.motionHeight <= next.frameBudget);
    assert.ok(next.frameBudget <= initial.frameBudget && next.motionWidth >= Math.ceil(initial.width * 0.25) && next.motionHeight >= Math.ceil(initial.height * 0.25));
  }
  assert.ok(smallest < initial.frameBudget);
});

test('recovery has hysteresis and cannot exceed the admitted work ceiling', () => {
  const controller = createPreviewQualityController({ performance: { mode: 'manual', value: 35 }, viewport: { width: 800, height: 600 } });
  const ceiling = controller.profile.frameBudget;
  const reduced = controller.observeBatch(80).frameBudget;
  for (let i = 0; i < 7; i++) assert.equal(controller.observeBatch(2).frameBudget, reduced);
  assert.ok(controller.observeBatch(2).frameBudget > reduced);
  for (let i = 0; i < 200; i++) controller.observeBatch(2);
  assert.equal(controller.profile.frameBudget, ceiling);
  assert.equal(controller.observeBatch(NaN).frameBudget, ceiling);
});

test('a reduced path pool always covers each low-resolution pixel within the fixed bounce passes', () => {
  for (const [width, height] of [[1, 4096], [4096, 1], [1333, 750], [63, 65]]) {
    const initial = previewQualityProfile({ mode: 'manual', value: 100 }, { width, height });
    for (const slots of [1, 7, 512, 4096]) {
      const profile = withPreviewRayBudget(initial, slots);
      assert.ok(Math.ceil(profile.width * profile.motionScale) * Math.ceil(profile.height * profile.motionScale) <= slots);
    }
  }
});


test('radial-scale motion stays recognizable during sustained expensive full-picture batches', () => {
  const admitted = { ...previewQualityProfile({}, { width: 1280, height: 900 }), width: 841, height: 591, frameBudget: 34202 };
  const controller = createPreviewQualityController({ admittedProfile: admitted });
  for (let i = 0; i < 100; i++) controller.observeBatch(500);
  const next = controller.profile;
  assert.deepEqual([next.motionWidth, next.motionHeight], [211, 148]);
  assert.ok(next.motionWidth * next.motionHeight <= next.frameBudget);
  assert.ok(next.frameBudget <= admitted.frameBudget);
  assert.deepEqual([next.width, next.height, next.samples], [admitted.width, admitted.height, admitted.samples]);
});
