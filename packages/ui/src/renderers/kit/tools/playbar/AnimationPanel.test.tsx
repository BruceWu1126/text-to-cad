import React from 'react';
import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { AnimationToolbar } from '../../../../../dist/renderers/kit/tools/playbar/AnimationPanel.js';
import { createAnimationClock } from '../../../../../dist/renderers/kit/tools/playbar/animationClock.js';

const restores: (() => void)[] = [];
function override(target: object, key: string, descriptor: PropertyDescriptor) {
  const previous = Object.getOwnPropertyDescriptor(target, key);
  Object.defineProperty(target, key, { configurable: true, ...descriptor });
  restores.push(() => previous ? Object.defineProperty(target, key, previous) : delete (target as any)[key]);
}
beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
  // A Select's listbox, which jsdom cannot lay out or point at.
  override(Element.prototype, 'scrollIntoView', { value() {} });
  for (const name of ['setPointerCapture', 'releasePointerCapture']) override(Element.prototype, name, { value() {} });
  override(Element.prototype, 'hasPointerCapture', { value: () => false });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); while (restores.length) restores.pop()!(); });

const TURN = { id: 'turn', label: 'Turn', duration: 8 };
const OPEN = { id: 'open', label: 'Open', duration: 3 };
const routines = (clips: object[], patch = {}) => ({ clips, activeClipId: 'turn', elapsedSec: 2, playing: false, speed: 1, loopEnabled: true,
  clock: createAnimationClock(), onPlayToggle: vi.fn(), onScrub: vi.fn(), onSpeedChange: vi.fn(), onLoopToggle: vi.fn(), onClipSelect: vi.fn(), onRelease: vi.fn(), ...patch });

it("the tool's row is the playbar's, compact: no routines for one, Play, the scrubber, its Playback settings, then Reset", async () => {
  const user = userEvent.setup();
  const runtime = routines([TURN], { speed: 0.5 });
  const onAutoplayChange = vi.fn();
  render(<AnimationToolbar runtime={runtime} autoplay={false} onAutoplayChange={onAutoplayChange} />);
  const row = screen.getByRole('toolbar', { name: 'Animation controls' });
  expect(within(row).getAllByRole('button').map(button => button.getAttribute('aria-label')))
    .toEqual(['Play animation', 'Playback settings', 'Reset animation']);
  expect(row.querySelector('[data-animation-time]')).toBeNull();
  // Reset puts the routine back at rest, and can only while it is under way.
  const reset = within(row).getByRole('button', { name: 'Reset animation' });
  expect(reset.hasAttribute('disabled')).toBe(false);
  await user.click(reset);
  expect(runtime.onRelease).toHaveBeenCalledTimes(1);
  await user.click(within(row).getByRole('button', { name: 'Play animation' }));
  expect(runtime.onPlayToggle).toHaveBeenCalledTimes(1);
  await user.click(within(row).getByRole('button', { name: 'Playback settings' }));
  const menu = screen.getByRole('menu');
  expect(within(menu).getByRole('menuitem', { name: 'Animation speed: 0.5×' })).toBeTruthy();
  await user.click(within(menu).getByRole('menuitemcheckbox', { name: 'Loop' }));
  expect(runtime.onLoopToggle).toHaveBeenLastCalledWith(false);
  await user.click(within(menu).getByRole('menuitemcheckbox', { name: 'Autoplay' }));
  expect(onAutoplayChange).toHaveBeenLastCalledWith(true);
});

it('a file with several routines leads the row with their playlist, the one in hand checked; choosing one plays it', async () => {
  const user = userEvent.setup();
  const runtime = routines([TURN, OPEN]);
  render(<AnimationToolbar runtime={runtime} autoplay={false} onAutoplayChange={vi.fn()} />);
  const row = screen.getByRole('toolbar', { name: 'Animation controls' });
  expect(within(row).getAllByRole('button')[0].getAttribute('aria-label')).toBe('Routines');
  await user.click(within(row).getByRole('button', { name: 'Routines' }));
  const menu = screen.getByRole('menu');
  expect(within(menu).getAllByRole('menuitemradio').map(item => [item.textContent, item.getAttribute('aria-checked')]))
    .toEqual([['Turn', 'true'], ['Open', 'false']]);
  await user.click(within(menu).getByRole('menuitemradio', { name: 'Open' }));
  expect(runtime.onClipSelect).toHaveBeenLastCalledWith('open');
});

it('Reset is idle at rest: nothing playing and no time elapsed', () => {
  render(<AnimationToolbar runtime={routines([TURN], { elapsedSec: 0 })} autoplay={false} onAutoplayChange={vi.fn()} />);
  expect(screen.getByRole('button', { name: 'Reset animation' }).hasAttribute('disabled')).toBe(true);
});
