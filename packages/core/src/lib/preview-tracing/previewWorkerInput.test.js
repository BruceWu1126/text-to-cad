import assert from 'node:assert/strict';
import test from 'node:test';
import { mergePreviewInput } from './previewWorkerInput.js';

test('latest camera and quality retain the latest queued pose during preparation', () => {
  const pose = new Float32Array([1, 2, 3]), floor = [4, 5, 6];
  const first = { camera: 'old', transforms: pose, floorMatrix: floor, frameChanged: true, performance: { value: 20 } };
  const combined = mergePreviewInput(first, { camera: 'new', performance: { value: 90 }, frameChanged: false });
  assert.equal(combined.camera, 'new'); assert.equal(combined.performance.value, 90);
  assert.equal(combined.transforms, pose); assert.equal(combined.floorMatrix, floor); assert.equal(combined.frameChanged, true);
  const newer = new Float32Array([7]);
  assert.equal(mergePreviewInput(combined, { transforms: newer }).transforms, newer);
});
