import assert from 'node:assert/strict';
import test from 'node:test';
import { createPreviewSceneRequests } from './previewSceneRequests.js';

test('lighting changes rebuild the scene even between animated pose frames', () => {
  const changes = [], state = {}, runtime = { progressivePreview: { invalidate: change => changes.push(change) } };
  let frames = 0;
  const requests = createPreviewSceneRequests(() => runtime, state, () => frames++);
  requests.requestMotionFrame();
  requests.requestRender(); // A lighting or appearance change during playback.
  requests.requestMotionFrame();
  assert.deepEqual(changes, [{ transformsChanged: true }, { sceneChanged: true }, { transformsChanged: true }]);
  assert.deepEqual(state, { shadowsDirty: true, sceneChanged: true });
  assert.equal(frames, 3);
});
