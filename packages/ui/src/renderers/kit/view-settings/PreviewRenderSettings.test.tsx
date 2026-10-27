import React from 'react';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createViewSettingsStore } from './viewSettingsStore.js';
import { PreviewRenderSettings } from '../../../../dist/renderers/kit/view-settings/PreviewRenderSettings.js';

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
// Preview's settings panel's body (`PreviewSettings.jsx`), wired as the shell wires it.
function Harness() {
  [store] = React.useState(() => createViewSettingsStore({ mode: 'xray', grid: true }));
  const { display, preview } = React.useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  return <><button>Outside</button>
    <PreviewRenderSettings settings={preview} display={display} onPatch={store.patchPreview} onReset={store.resetPreview} /></>;
}

it("has Preview's lighting, background and a floor that folds while off; editing it leaves the file's display", async () => {
  const user = userEvent.setup({ skipHover: true });
  render(<Harness />);
  for (const label of ['Exposure', 'Rotation', 'Softbox size', 'Fill', 'Background opacity', 'Floor opacity']) {
    expect(screen.getByRole('slider', { name: label })).toBeTruthy();
  }
  expect(screen.getByRole('button', { name: 'Reset to default' }).hasAttribute('disabled')).toBe(true);
  const exposure = screen.getByRole('slider', { name: 'Exposure' });
  act(() => exposure.focus());
  await user.keyboard('{ArrowRight}');
  expect(store.getSnapshot().preview.lighting.exposure).toBeCloseTo(0.1);
  expect(store.renderScene().view.lighting.exposure).toBeCloseTo(0.1);
  await user.click(screen.getByRole('checkbox', { name: 'Floor', exact: true }));
  expect(screen.queryByRole('button', { name: 'Floor colour' })).toBeNull();
  for (const label of ['Placement', 'Finish']) expect(screen.queryByRole('combobox', { name: label })).toBeNull();
  expect(screen.queryByRole('textbox', { name: 'Floor opacity value' })).toBeNull();
  expect(store.getSnapshot().display).toEqual({ mode: 'xray', grid: true });
  await user.click(screen.getByRole('button', { name: 'Reset to default' }));
  expect(store.getSnapshot().preview).toEqual({});
  expect(store.getSnapshot().display).toEqual({ mode: 'xray', grid: true });
});

it('keeps sparse defaults when a value field is only focused, and the precision of an existing setting', async () => {
  const user = userEvent.setup({ skipHover: true });
  render(<Harness />);
  const initial = store.getSnapshot().preview;
  const opacity = screen.getByRole('textbox', { name: 'Background opacity value' });
  await user.click(opacity);
  await user.tab();
  expect(store.getSnapshot().preview).toBe(initial);
  act(() => store.patchPreview({ background: { opacity: 0.4321 } }));
  const precise = store.getSnapshot().preview;
  await user.click(opacity);
  await user.tab();
  expect(store.getSnapshot().preview).toBe(precise);
  expect(store.getSnapshot().preview.background.opacity).toBe(0.4321);
  await user.click(opacity);
  await user.keyboard('500{Enter}');
  expect(store.getSnapshot().preview.background.opacity).toBe(1);
});

it('edits the full By-part palette, and nested popups own Escape and give the focus back', async () => {
  const user = userEvent.setup({ skipHover: true });
  render(<Harness />);
  const source = screen.getByRole('combobox', { name: 'Colour source' });
  await user.click(source);
  act(() => screen.getByRole('option', { name: 'Original', exact: true }).focus());
  await user.keyboard('{Escape}');
  expect(screen.queryByRole('listbox')).toBeNull();
  await waitFor(() => expect(document.activeElement).toBe(source));
  await user.click(source);
  await user.click(screen.getByRole('option', { name: 'By part' }));
  const initial = screen.getAllByRole('button', { name: /^Part colour \d+$/ }).length;
  await user.click(screen.getByRole('button', { name: 'Add colour' }));
  expect(store.getSnapshot().preview.parts.colors.length).toBe(initial + 1);
  await user.click(screen.getByRole('button', { name: `Part colour ${initial + 1}`, exact: true }));
  const hex = screen.getByRole('textbox', { name: 'Hex colour' });
  await user.clear(hex);
  await user.type(hex, '#22c55e{Enter}');
  expect(store.getSnapshot().preview.parts.colors.at(-1)).toBe('#22c55e');
  await user.keyboard('{Escape}');
  expect(screen.queryByRole('textbox', { name: 'Hex colour' })).toBeNull();
  await user.click(screen.getByRole('button', { name: 'Remove last colour' }));
  expect(store.getSnapshot().preview.parts.colors.length).toBe(initial);
  expect(store.getSnapshot().display.mode).toBe('xray');
});


it('starts with Auto quality, keeps manual quality across Auto, and leaves display and lighting unchanged', async () => {
  const user = userEvent.setup({ skipHover: true });
  render(<Harness />);
  const automatic = screen.getByRole('checkbox', { name: 'Auto quality' });
  const slider = screen.getByRole('slider', { name: 'Quality', exact: true });
  const number = screen.getByRole('textbox', { name: 'Quality value', exact: true });
  expect(automatic.getAttribute('aria-checked')).toBe('true');
  expect(slider.hasAttribute('data-disabled')).toBe(true);
  expect(number.hasAttribute('disabled')).toBe(true);
  expect(document.getElementById(slider.getAttribute('aria-describedby')!)?.textContent).toContain('Faster');
  expect(screen.queryByRole('combobox', { name: 'Quality', exact: true })).toBeNull();
  const originalScene = store.getSnapshot().scene;
  const originalLighting = store.renderScene().render.configuration.lighting;
  await user.click(automatic);
  act(() => slider.focus());
  await user.keyboard('{ArrowRight}');
  expect(store.getSnapshot().preview.quality).toEqual({ mode: 'manual', value: 51 });
  await user.click(number);
  await user.keyboard('200{Enter}');
  expect(store.getSnapshot().preview.quality.value).toBe(100);
  await user.click(automatic);
  expect(store.getSnapshot().preview.quality).toEqual({ mode: 'auto', value: 100 });
  await user.click(automatic);
  expect(store.getSnapshot().preview.quality).toEqual({ mode: 'manual', value: 100 });
  expect(store.getSnapshot().scene).toBe(originalScene);
  expect(store.renderScene().render.configuration.lighting).toEqual(originalLighting);
  expect(store.renderScene().render.configuration.performance).toEqual({ mode: 'manual', value: 100 });
  await user.click(screen.getByRole('button', { name: 'Reset to default' }));
  expect(store.getSnapshot().preview).toEqual({});
  expect(automatic.getAttribute('aria-checked')).toBe('true');
  expect(number.getAttribute('value')).toBe('50');
});
