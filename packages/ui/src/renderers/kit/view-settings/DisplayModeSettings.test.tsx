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
const restores: (() => void)[] = [];
beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
  for (const [name, value] of Object.entries({ scrollIntoView() {}, hasPointerCapture: () => false, setPointerCapture() {}, releasePointerCapture() {} })) {
    const previous = Object.getOwnPropertyDescriptor(Element.prototype, name);
    Object.defineProperty(Element.prototype, name, { configurable: true, value });
    restores.push(() => previous ? Object.defineProperty(Element.prototype, name, previous) : delete (Element.prototype as any)[name]);
  }
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); while (restores.length) restores.pop()!(); });
let store: ReturnType<typeof createViewSettingsStore>;
function Harness({ features }: { features?: any }) {
  [store] = React.useState(() => createViewSettingsStore({ camera: { focalLength: 80 }, clip: { enabled: true, at: 4 } }));
  const { display, scene } = React.useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  return <><button>Outside</button><DisplayPopover
    projection={{ value: scene.camera.projection, onChange: value => store.patch({ camera: { projection: value } }) }}
    mode={{ value: display.mode, options: DISPLAY_MODE_OPTIONS.filter(option => !features || features.modes.includes(option.value)), onChange: store.selectMode }}>
    {mode => <DisplayModeSettings mode={mode} display={display} features={features}
      onPatch={patch => store.patchMode(mode, patch)} onReset={() => store.resetMode(mode)} />}
  </DisplayPopover></>;
}
// Render's page alone: Preview's settings (`PreviewSettings.jsx`), never a Display mode.
function PreviewRender() {
  [store] = React.useState(() => createViewSettingsStore({}));
  const { display } = React.useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  return <DisplayModeSettings mode="render" display={display} onPatch={patch => store.patchMode('render', patch)} onReset={() => store.resetMode('render')} />;
}
const settings = (mode: string) => screen.getByRole('dialog', { name: `${mode} settings` });
const picker = () => screen.getByRole('dialog', { name: 'Display mode', exact: true });
const trigger = () => screen.getByRole('button', { name: /^Display mode:/ });
async function open(user: ReturnType<typeof userEvent.setup>, mode: string) {
  if (trigger().getAttribute('aria-expanded') !== 'true') await user.click(trigger());
  const choice = within(screen.getByRole('radiogroup', { name: 'Display Mode', exact: true })).getByRole('radio', { name: mode, exact: true });
  if (choice.getAttribute('aria-checked') !== 'true') {
    await user.click(choice);
    await user.click(trigger());
  }
  await user.click(screen.getByRole('button', { name: `${mode} settings` }));
  return settings(mode);
}

it('uses one heading settings button for the selected mode, right checks, back navigation, and outside dismissal', async () => {
  const user = userEvent.setup({ skipHover: true });
  render(<Harness />);
  await user.click(trigger());
  const root = picker();
  expect(within(root).getAllByRole('radiogroup').map(group => group.getAttribute('aria-label'))).toEqual(['Display Mode', 'Projection']);
  expect(within(root).queryByRole('heading')).toBeNull();
  expect(within(root).queryByRole('button', { name: /close/i })).toBeNull();
  for (const group of within(root).getAllByRole('radiogroup')) {
    const selected = within(group).getByRole('radio', { checked: true });
    expect(selected.querySelector('[data-state=checked]')?.parentElement?.className).toContain('right-2');
  }
  const settingsButton = within(root).getByRole('button', { name: 'Solid settings' });
  expect(within(root).getAllByRole('button', { name: /settings$/ })).toEqual([settingsButton]);
  expect(settingsButton.parentElement?.textContent).toBe('Display Mode');
  expect(settingsButton.closest('[role=radiogroup]')).toBeNull();
  expect(settingsButton.getAttribute('title')).toBeNull();
  await user.click(within(root).getByRole('radio', { name: 'Perspective' }));
  expect(store.getSnapshot().display.camera.projection).toBe('perspective');
  expect(picker()).toBe(root);
  await user.click(settingsButton);
  expect(settings('Solid')).toBe(root);
  expect(root.className).toContain('w-52');
  expect(screen.getByRole('button', { name: 'Back to Display' }).nextElementSibling!.textContent).toBe('Settings');
  expect(screen.getByRole('button', { name: 'Back to Display' })).toBe(document.activeElement);
  expect(store.getSnapshot().display.mode).toBe('solid');
  act(() => store.selectMode('xray'));
  expect(settings('X-ray')).toBe(root);
  expect(within(root).getByRole('slider', { name: 'Surface opacity' })).toBeTruthy();
  await user.click(screen.getByRole('button', { name: 'Back to Display' }));
  expect(picker()).toBe(root);
  expect(screen.getByRole('button', { name: 'X-ray settings' })).toBe(document.activeElement);
  await user.click(screen.getByRole('button', { name: 'Outside' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
});

it('edits the selected mode, keeps settings across switching, and resets only that mode', async () => {
  const user = userEvent.setup({ skipHover: true });
  render(<Harness />);
  const solid = await open(user, 'Solid');
  expect(within(solid).getByRole('button', { name: 'Reset to default' }).hasAttribute('disabled')).toBe(true);
  // A feature's switch is the checkbox ending its section's heading, the heading in the controls'
  // 11px; while it is off the section is that heading alone.
  const gridHeading = within(solid).getByRole('heading', { name: 'Grid' });
  const gridSwitch = within(gridHeading).getByRole('checkbox');
  expect([gridSwitch.getAttribute('aria-checked'), within(solid).getAllByRole('checkbox', { name: 'Grid' }).length,
    gridHeading.querySelector('label')!.lastElementChild === gridSwitch]).toEqual(['false', 1, true]);
  expect(gridHeading.querySelector('label')!.className).toContain('text-tiny');
  expect([within(solid).queryByRole('button', { name: 'Grid colour' }), within(solid).queryByRole('slider', { name: 'Grid opacity' })]).toEqual([null, null]);
  await user.click(within(solid).getByRole('checkbox', { name: 'Grid', exact: true }));
  expect(store.getSnapshot().scene.view.grid.enabled).toBe(true);
  expect(store.getSnapshot().scene.view.axes.enabled).toBe(false);
  expect(within(solid).getByRole('button', { name: 'Grid colour' }).hasAttribute('disabled')).toBe(false);
  await user.click(within(solid).getByRole('button', { name: 'Back to Display' }));
  await user.click(screen.getByRole('radio', { name: 'X-ray', exact: true }));
  const xray = await open(user, 'X-ray');
  await user.click(within(xray).getByRole('checkbox', { name: 'Axes', exact: true }));
  await user.click(within(xray).getByRole('button', { name: 'Back to Display' }));
  const selected = await open(user, 'Solid');
  expect(within(selected).getByRole('checkbox', { name: 'Grid', exact: true }).getAttribute('aria-checked')).toBe('true');
  await user.click(within(selected).getByRole('checkbox', { name: 'Flat shading' }));
  expect(store.getSnapshot().display.mode).toBe('solid');
  expect(store.getSnapshot().display.modes.solid.flat).toBe(true);
  await user.click(within(selected).getByRole('button', { name: 'Reset to default' }));
  expect(store.getSnapshot().display).toEqual({ mode: 'solid', camera: { focalLength: 80 }, clip: { enabled: true, at: 4 }, modes: { xray: { axes: { enabled: true } } } });
  expect(screen.queryByText('Custom')).toBeNull();
});

it('gives sliders and value drafts their keyboard input', async () => {
  const user = userEvent.setup({ skipHover: true });
  render(<Harness />);
  const xray = await open(user, 'X-ray');
  const opacity = within(xray).getByRole('slider', { name: 'Surface opacity' });
  act(() => opacity.focus());
  await user.keyboard('{ArrowRight}');
  expect(store.getSnapshot().display.modes.xray.opacity).toBeCloseTo(0.23);
  const input = within(xray).getByRole('textbox', { name: 'Surface opacity value' });
  await user.click(input);
  await user.keyboard('500{Enter}');
  expect(store.getSnapshot().display.modes.xray.opacity).toBe(1);
  await user.click(input);
  await user.keyboard('20{Escape}');
  expect(store.getSnapshot().display.modes.xray.opacity).toBe(1);
  expect(settings('X-ray')).toBe(xray);
});

it("Render's page, Preview's settings, has its lighting and a floor that folds while off; editing it leaves the file's mode", async () => {
  const user = userEvent.setup({ skipHover: true });
  render(<PreviewRender />);
  for (const label of ['Exposure', 'Rotation', 'Softbox size', 'Fill', 'Background opacity', 'Floor opacity']) {
    expect(screen.getByRole('slider', { name: label })).toBeTruthy();
  }
  const exposure = screen.getByRole('slider', { name: 'Exposure' });
  act(() => exposure.focus());
  await user.keyboard('{ArrowRight}');
  expect(store.getSnapshot().display.modes.render.lighting.exposure).toBeCloseTo(0.1);
  await user.click(screen.getByRole('checkbox', { name: 'Floor', exact: true }));
  expect(screen.queryByRole('button', { name: 'Floor colour' })).toBeNull();
  for (const label of ['Placement', 'Finish']) expect(screen.queryByRole('combobox', { name: label })).toBeNull();
  expect(screen.queryByRole('textbox', { name: 'Floor opacity value' })).toBeNull();
  expect(store.getSnapshot().display.mode).toBe('solid');
});

it('nested select and colour popups own Escape and return focus before the settings surface closes', async () => {
  const user = userEvent.setup({ skipHover: true });
  render(<Harness />);
  const solid = await open(user, 'Solid');
  const source = within(solid).getByRole('combobox', { name: 'Colour source' });
  await user.click(source);
  act(() => screen.getByRole('option', { name: 'Original', exact: true }).focus());
  await user.keyboard('{Escape}');
  expect(screen.queryByRole('listbox')).toBeNull();
  expect(settings('Solid')).toBe(solid);
  await waitFor(() => expect(document.activeElement).toBe(source));
  await user.click(within(solid).getByRole('button', { name: 'Edge colour' }));
  const hex = screen.getByRole('textbox', { name: 'Hex color' });
  await user.clear(hex);
  await user.type(hex, '#ef4444{Enter}');
  expect(store.getSnapshot().display.modes.solid.edges.color).toBe('#ef4444');
  await user.keyboard('{Escape}');
  expect(screen.queryByRole('textbox', { name: 'Hex color' })).toBeNull();
  expect(settings('Solid')).toBe(solid);
  await waitFor(() => expect(document.activeElement).toBe(within(solid).getByRole('button', { name: 'Edge colour' })));
  await user.keyboard('{Escape}');
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(trigger()).toBe(document.activeElement);
});

it('focusing value fields preserves sparse defaults and the precision of existing overrides', async () => {
  const user = userEvent.setup({ skipHover: true });
  render(<Harness />);
  const solid = await open(user, 'Solid');
  const initial = store.getSnapshot().display;
  // A disabled default remains absent; enabling only the guide must not pin any appearance.
  await user.click(within(solid).getByRole('checkbox', { name: 'Grid', exact: true }));
  const enabled = store.getSnapshot().display;
  const gridOpacity = within(solid).getByRole('textbox', { name: 'Grid opacity value' });
  await user.click(gridOpacity);
  await user.tab();
  expect(store.getSnapshot().display).toBe(enabled);
  expect(store.getSnapshot().display.modes.solid.grid).toEqual({ enabled: true });
  act(() => store.patchMode('solid', { grid: { opacity: 0.4321 } }));
  const precise = store.getSnapshot().display;
  await user.click(gridOpacity);
  await user.tab();
  expect(store.getSnapshot().display).toBe(precise);
  expect(store.getSnapshot().display.modes.solid.grid.opacity).toBe(0.4321);
  await user.click(within(solid).getByRole('button', { name: 'Reset to default' }));
  expect(store.getSnapshot().display).toEqual(initial);
});

it('filters format capabilities and preserves the full editable By-part palette', async () => {
  const user = userEvent.setup({ skipHover: true });
  render(<Harness features={EDGELESS_VIEW_FEATURES} />);
  await user.click(trigger());
  expect(within(screen.getByRole('radiogroup', { name: 'Display Mode', exact: true })).getAllByRole('radio').map(item => item.textContent)).toEqual(['Solid']);
  const solid = await open(user, 'Solid');
  expect(within(solid).queryByRole('checkbox', { name: 'Edges' })).toBeNull();
  await user.click(within(solid).getByRole('combobox', { name: 'Colour source' }));
  await user.click(screen.getByRole('option', { name: 'By part' }));
  const initial = within(solid).getAllByRole('button', { name: /^Part colour \d+$/ }).length;
  await user.click(within(solid).getByRole('button', { name: 'Add colour' }));
  expect(store.getSnapshot().display.modes.solid.parts.colors.length).toBe(initial + 1);
  await user.click(within(solid).getByRole('button', { name: `Part colour ${initial + 1}`, exact: true }));
  const hex = screen.getByRole('textbox', { name: 'Hex color' });
  await user.clear(hex);
  await user.type(hex, '#22c55e{Enter}');
  expect(store.getSnapshot().display.modes.solid.parts.colors.at(-1)).toBe('#22c55e');
  await user.keyboard('{Escape}');
  await user.click(within(solid).getByRole('button', { name: 'Remove last colour' }));
  expect(store.getSnapshot().display.modes.solid.parts.colors.length).toBe(initial);
  expect(store.getSnapshot().display.mode).toBe('solid');
});

it('radio keyboard navigation selects remembered modes and closes the picker', async () => {
  const user = userEvent.setup({ skipHover: true });
  render(<Harness />);
  await user.click(trigger());
  act(() => screen.getByRole('radio', { name: 'Solid', exact: true }).focus());
  // Radix focuses the next radio in a scheduled task while the arrow is held.
  await user.keyboard('{ArrowDown>}');
  await waitFor(() => expect(store.getSnapshot().display.mode).toBe('xray'));
  await user.keyboard('{/ArrowDown}');
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(trigger().getAttribute('aria-label')).toBe('Display mode: X-ray');
});
