import test from 'node:test';
import assert from 'node:assert/strict';
import { validateSnapshotRenderJob } from './snapshotJobValidation.js';

test('snapshot Preview validates its settings and refuses unsupported overlays', () => {
  assert.doesNotThrow(() => validateSnapshotRenderJob({ preview: true, previewSettings: { lighting: { quality: 'final' }, floor: { opacity: 0.6 } } }));
  assert.throws(() => validateSnapshotRenderJob({ preview: true, previewSettings: { lighting: { quailty: 'final' } } }), /quailty/);
  assert.throws(() => validateSnapshotRenderJob({ preview: true, previewSettings: null }), /preview/);
  assert.throws(() => validateSnapshotRenderJob({ preview: true, annotation: {} }), /annotations or measurements/);
  assert.throws(() => validateSnapshotRenderJob({ preview: true, measurements: [] }), /annotations or measurements/);
});
