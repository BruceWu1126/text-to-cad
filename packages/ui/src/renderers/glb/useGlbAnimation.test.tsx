import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { clipLoops, useGlbAnimation } from '../../../dist/renderers/glb/useGlbAnimation.js';

afterEach(cleanup);

const clip = (name: string, loop?: string | null) => ({
  name, duration: 2, tracks: [], userData: loop === undefined ? {} : { cadgen: { loop, duration: 2 } }
});

it('a clip opens at the Loop its file gives it: a cadgen motion played once stays once, any other file loops', () => {
  expect(clipLoops(clip('steer', 'pingpong'))).toBe(true);
  expect(clipLoops(clip('cycle', 'loop'))).toBe(true);
  expect(clipLoops(clip('nod', null))).toBe(false);
  expect(clipLoops(clip('walk'))).toBe(true);

  const document = { scene: null, clips: [clip('nod', null), clip('steer', 'pingpong')] };
  const view = renderHook(() => useGlbAnimation(document, () => {}));
  expect(view.result.current!.clips.map((row: any) => row.label)).toEqual(['nod', 'steer']);
  expect(view.result.current!.loopEnabled).toBe(false);
  act(() => view.result.current!.onClipSelect('glb:1'));
  expect(view.result.current!.loopEnabled).toBe(true);
  // The viewer's own Loop wins once chosen, until the routine is put back at rest.
  act(() => view.result.current!.onLoopToggle(false));
  expect(view.result.current!.loopEnabled).toBe(false);
  act(() => view.result.current!.resetPlayback());
  expect(view.result.current!.loopEnabled).toBe(true);
});
