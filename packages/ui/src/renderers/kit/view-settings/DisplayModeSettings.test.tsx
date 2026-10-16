import React from 'react';
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { EDGELESS_VIEW_FEATURES } from '@text-to-cad/core/common/viewSettings.js';
import { createViewSettingsStore } from './viewSettingsStore.js';
import { DisplayModeSettings } from '../../../../dist/renderers/kit/view-settings/DisplayModeSettings.js';
import DisplayPopover from '../../../../dist/renderers/kit/shell/DisplayPopover.js';
import { DISPLAY_MODE_OPTIONS } from '../../../../dist/renderers/kit/view-settings/DisplayModeOptions.js';

Object.assign(globalThis, { React });
beforeEach(() => vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
let store: ReturnType<typeof createViewSettingsStore>;
function Harness({ features }: { features?: any }) {
  [store] = React.useState(() => createViewSettingsStore({ camera: { focalLength: 80 }, clip: { enabled: true, at: 4 } }));
  const { display } = React.useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  return <DisplayPopover mode={{ value: display.mode, options: DISPLAY_MODE_OPTIONS.filter(option => !features || features.modes.includes(option.value)), onChange: store.selectMode }}>
    {mode => <DisplayModeSettings mode={mode} display={display} features={features}
      onPatch={patch => store.patchMode(mode, patch)} onReset={() => store.resetMode(mode)} />}
  </DisplayPopover>;
}
const settings = (mode: string) => screen.getByRole('menu', { name: `${mode} settings` });
async function open(user: ReturnType<typeof userEvent.setup>, mode: string) {
  if (screen.getByRole('button', { name: /^Display mode:/ }).getAttribute('aria-expanded') !== 'true') await user.click(screen.getByRole('button', { name: /^Display mode:/ }));
  await user.click(screen.getByRole('menuitem', { name: `${mode} settings` }));
  return settings(mode);
}

it('edits an inactive mode, keeps its settings across switching, and resets only that mode', async () => {
  const user = userEvent.setup({ skipHover: true });
  render(<Harness />);
  const solid = await open(user, 'Solid');
  expect(within(solid).getByRole('menuitem', { name: 'Reset Solid' }).getAttribute('data-disabled')).toBe('');
  await user.click(within(solid).getByRole('menuitemcheckbox', { name: 'Grid', exact: true }));
  expect(store.getSnapshot().scene.view.grid.enabled).toBe(true);
  expect(store.getSnapshot().scene.view.axes.enabled).toBe(false);
  await user.keyboard('{Escape}');
  await user.click(screen.getByRole('menuitemradio', { name: 'Render', exact: true }));
  const inactive = await open(user, 'Solid');
  expect(within(inactive).getByRole('menuitemcheckbox', { name: 'Grid', exact: true }).getAttribute('aria-checked')).toBe('true');
  await user.click(within(inactive).getByRole('menuitemcheckbox', { name: 'Flat shading' }));
  expect(store.getSnapshot().display.mode).toBe('render');
  expect(store.getSnapshot().display.modes.solid.flat).toBe(true);
  await user.click(within(inactive).getByRole('menuitem', { name: 'Reset Solid' }));
  expect(store.getSnapshot().display).toEqual({ mode: 'render', camera: { focalLength: 80 }, clip: { enabled: true, at: 4 } });
  expect(screen.queryByText('Custom')).toBeNull();
});

it('X-ray opacity and Render lighting sliders keep keyboard control without selecting their mode', async () => {
  const user = userEvent.setup({ skipHover: true });
  render(<Harness />);
  const xray = await open(user, 'X-ray');
  const opacity = within(xray).getByRole('slider', { name: 'Surface opacity' });
  act(() => opacity.focus());
  await user.keyboard('{ArrowRight}');
  expect(store.getSnapshot().display.modes.xray.opacity).toBeCloseTo(0.23);
  expect(store.getSnapshot().display.mode).toBe('solid');
  await user.keyboard('{Escape}');
  await waitFor(() => expect(screen.queryByRole('menu', { name: 'X-ray settings' })).toBeNull());
  const renderSettings = await open(user, 'Render');
  await user.click(within(renderSettings).getByRole('menuitem', { name: 'Lighting' }));
  const exposure = screen.getByRole('slider', { name: 'Exposure' });
  act(() => exposure.focus());
  await user.keyboard('{ArrowRight}');
  expect(store.getSnapshot().display.modes.render.lighting.exposure).toBeCloseTo(0.1);
  expect(store.getSnapshot().display.mode).toBe('solid');
  await user.keyboard('{Escape}');
  expect(screen.queryByRole('slider', { name: 'Exposure' })).toBeNull();
  expect(settings('Render')).toBeTruthy();
});

it('format capabilities remove edge modes and settings; colour and palette edits remain available', async () => {
  const user = userEvent.setup({ skipHover: true });
  render(<Harness features={EDGELESS_VIEW_FEATURES} />);
  const solid = await open(user, 'Solid');
  expect(screen.getAllByRole('menuitemradio').map(item => item.textContent)).toEqual(['Solid', 'Render']);
  expect(within(solid).queryByRole('menuitemcheckbox', { name: 'Edges' })).toBeNull();
  await user.click(within(solid).getByRole('menuitem', { name: 'Part colours' }));
  const choice = screen.getByRole('menuitemradio', { name: 'By part' });
  act(() => choice.focus()); await user.keyboard('{Enter}');
  expect(store.getSnapshot().display.modes.solid.parts.colorMode).toBe('by-part');
  await user.keyboard('{Escape}');
  await user.click(within(solid).getByRole('menuitem', { name: 'Part palette' }));
  const add = screen.getByRole('menuitem', { name: 'Add colour' });
  act(() => add.focus()); await user.keyboard('{Enter}');
  expect(store.getSnapshot().display.modes.solid.parts.colors.length).toBeGreaterThan(1);
});
