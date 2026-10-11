import assert from 'node:assert/strict';
import test from 'node:test';
import { modelingSelectionPaths } from './modelingSelection.js';

const part = id => ({ id: `model:${id}`, kind: 'part', selectionId: id, leafPartIds: [id], children: [] });
const tree = [{ id: 'model:root', kind: 'assembly', selectionId: 'root', leafPartIds: ['o1', 'o2'], children: [part('o1'), part('o2')] }];
const face = (occurrenceId, n) => ({ selectorType: 'face', occurrenceId, normalizedSelector: `${occurrenceId}.f${n}` });
const ends = paths => paths.map(path => path.at(-1).id);

test('a picked face or edge is revealed at the row of the part it belongs to', () => {
  assert.deepEqual(ends(modelingSelectionPaths(tree, [face('o2', 3)])), ['model:o2']);
  assert.deepEqual(ends(modelingSelectionPaths(tree, [face('o1', 1), face('o1', 2)])), ['model:o1'], 'once per part');
  assert.deepEqual(ends(modelingSelectionPaths(tree, [face('elsewhere', 1)])), []);
});

test('part selection prefers the exact part over an assembly containing that part', () => {
  assert.deepEqual(ends(modelingSelectionPaths(tree, [], ['o2'])), ['model:o2']);
  assert.deepEqual(ends(modelingSelectionPaths(tree, [], ['root'])), ['model:root']);
});

test('a part file is one row, and every pick on it is revealed there', () => {
  const lone = [{ id: 'model:__step_model__', kind: 'part', selectionId: '__step_model__', leafPartIds: ['__model__'], children: [] }];
  assert.deepEqual(ends(modelingSelectionPaths(lone, [face('o1', 4)])), ['model:__step_model__']);
});
