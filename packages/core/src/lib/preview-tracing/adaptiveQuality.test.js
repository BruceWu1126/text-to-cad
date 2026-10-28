import test from 'node:test';
import assert from 'node:assert/strict';
import { createPreviewQualityController } from './adaptiveQuality.js';
import { previewQualityProfile, withPreviewRayBudget } from './resourcePolicy.js';

test('sustained slow batches retain selected image dimensions while reducing the path pool', () => {
  const controller = createPreviewQualityController({ viewport: { width: 1920, height: 1080 } });
  const initial = controller.profile;
  let smallest = initial.frameBudget;
  for (let i = 0; i < 160; i++) {
    const next = controller.observeBatch(i % 9 ? 50 : 4);
    smallest = Math.min(smallest, next.frameBudget);
    assert.deepEqual([next.width, next.height, next.samples], [initial.width, initial.height, initial.samples]);
    assert.deepEqual([next.motionWidth, next.motionHeight], [initial.width, initial.height]);
    assert.ok(next.frameBudget <= initial.frameBudget);
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

test('motion dimensions do not collapse when the bounded ray pool is smaller than the image', () => {
  for (const [width, height] of [[1, 4096], [4096, 1], [1333, 750], [63, 65]]) {
    const initial = previewQualityProfile({ mode: 'manual', value: 100 }, { width, height });
    for (const slots of [1, 7, 512, 4096]) {
      const profile = withPreviewRayBudget(initial, slots);
      assert.equal(profile.frameBudget, slots);
      assert.deepEqual([profile.motionWidth, profile.motionHeight], [initial.width, initial.height]);
    }
  }
});
