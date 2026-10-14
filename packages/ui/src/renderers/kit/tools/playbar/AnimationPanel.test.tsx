import React from 'react';
import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { AnimationToolMenu, KeptAnimationPanel } from '../../../../../dist/renderers/kit/tools/playbar/AnimationPanel.js';
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
  clock: createAnimationClock(), onPlayToggle: vi.fn(), onScrub: vi.fn(), onSpeedChange: vi.fn(), onLoopToggle: vi.fn(), onClipSelect: vi.fn(), ...patch });

it("the tool's menu, its chevron on the strip: no routines for one, then Speed, Loop and Autoplay", async () => {
  const user = userEvent.setup();
  const runtime = routines([TURN], { speed: 0.5 });
  const onAutoplayChange = vi.fn();
  render(<AnimationToolMenu runtime={runtime} autoplay={false} onAutoplayChange={onAutoplayChange} onRoutine={vi.fn()} />);
  await user.click(screen.getByRole('button', { name: 'Animation settings' }));
  const menu = screen.getByRole('menu', { name: 'Animation settings' });
  expect(within(menu).queryAllByRole('menuitemradio')).toEqual([]);
  expect(within(menu).getByRole('menuitem', { name: 'Animation speed: 0.5×' })).toBeTruthy();
  expect(within(menu).getAllByRole('menuitemcheckbox').map(item => item.textContent)).toEqual(['Loop', 'Autoplay']);
  await user.click(within(menu).getByRole('menuitemcheckbox', { name: 'Loop' }));
  expect(runtime.onLoopToggle).toHaveBeenLastCalledWith(false);
  // Ticking a checkbox leaves the menu open.
  await user.click(within(menu).getByRole('menuitemcheckbox', { name: 'Autoplay' }));
  expect(onAutoplayChange).toHaveBeenLastCalledWith(true);
  await user.click(within(menu).getByRole('menuitem', { name: 'Animation speed: 0.5×' }));
  // jsdom has no geometry for the submenu's pointer grace area; the keyboard path is the same handler.
  screen.getByRole('menuitemradio', { name: '2×' }).focus();
  await user.keyboard('{Enter}');
  expect(runtime.onSpeedChange).toHaveBeenLastCalledWith(2);
});

it('lists the routines first when there are several, the one in hand checked; choosing one plays it', async () => {
  const user = userEvent.setup();
  const onRoutine = vi.fn();
  render(<AnimationToolMenu runtime={routines([TURN, OPEN])} autoplay={false} onAutoplayChange={vi.fn()} onRoutine={onRoutine} />);
  await user.click(screen.getByRole('button', { name: 'Animation settings' }));
  const menu = screen.getByRole('menu', { name: 'Animation settings' });
  expect(within(menu).getAllByRole('menuitemradio').map(item => [item.textContent, item.getAttribute('aria-checked')]))
    .toEqual([['Turn', 'true'], ['Open', 'false']]);
  await user.click(within(menu).getByRole('menuitemradio', { name: 'Open' }));
  expect(onRoutine).toHaveBeenLastCalledWith('open');
});

it('a frame kept with the tool down is one line: Animation, the time it paused at, and an X back to rest', async () => {
  const user = userEvent.setup();
  const runtime = routines([TURN], { elapsedSec: 2.5, onRelease: vi.fn() });
  render(<KeptAnimationPanel runtime={runtime} />);
  const panel = screen.getByRole('region', { name: 'Animation kept' });
  expect(panel.textContent).toContain('2.50s');
  expect(within(panel).queryByRole('slider')).toBeNull();
  await user.click(within(panel).getByRole('button', { name: 'Reset animation' }));
  expect(runtime.onRelease).toHaveBeenCalledOnce();
});
