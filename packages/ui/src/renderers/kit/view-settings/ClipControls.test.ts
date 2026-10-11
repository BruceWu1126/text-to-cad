import { expect, it } from 'vitest';
import { clipApplied, clipSummary, flippedClip } from '../../../../dist/renderers/kit/view-settings/ClipControls.js';

const view = (clip: Record<string, unknown>) => ({ clip: { enabled: true, axis: 'y', offsets: { x: 1, y: 0.3, z: 1 }, invert: false, ...clip } });

it('Flip keeps the other side of the same plane, and leaves an uncut axis uncut', () => {
  // A 70% cut along Y (the plane at 0.3, the lower side kept) flips to the upper side of the same plane: a 30% cut.
  const flipped = flippedClip(view({}));
  expect(flipped).toEqual({ invert: true, offsets: { x: 0, y: 0.3, z: 0 }, enabled: true });
  const after = view(flipped);
  expect([clipApplied(after), clipSummary(after)]).toEqual([true, '30%']);
  // And back: the very settings it started from.
  expect(flippedClip(after)).toEqual({ invert: false, offsets: { x: 1, y: 0.3, z: 1 }, enabled: true });
  // With no cut, Flip only chooses the side: still no cut.
  const neutral = { clip: { enabled: false, axis: 'x', offsets: { x: 1, y: 1, z: 1 }, invert: false } };
  expect(flippedClip(neutral)).toMatchObject({ invert: true, enabled: false });
  // A cut that took everything away (its plane at the far end) keeps everything once flipped: no cut.
  expect(flippedClip(view({ offsets: { x: 1, y: 0, z: 1 } }))).toMatchObject({ invert: true, enabled: false });
});
