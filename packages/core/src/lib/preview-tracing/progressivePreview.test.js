import test from 'node:test';
import assert from 'node:assert/strict';
import { createProgressivePreview } from './progressivePreview.js';

test('raw reference diagnostics reject unbounded or invalid sample requests', () => {
  for (const value of [0, -1, 1.5, 1025, Infinity, NaN]) {
    assert.throws(() => createProgressivePreview({}, { diagnostic: { rawReferenceSamples: value } }), /rawReferenceSamples/);
  }
});
