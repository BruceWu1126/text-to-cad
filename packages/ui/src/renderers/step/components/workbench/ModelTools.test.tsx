import React from 'react';
import { resolveViewSettings } from '@text-to-cad/core/common/viewSettings.js';
import { act, cleanup, render, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useModelTools } from '../../../../../dist/renderers/step/components/workbench/ModelTools.js';

beforeEach(() => { vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} }); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const neutral = () => resolveViewSettings({});

it('only the tool in hand has its row under the strip, an applied effect leaving none behind; taken up again it is as it was, unset it starts neutral; only the tool in hand is lit', () => {
  const props = { view: neutral(), features: { settings: ['exploded', 'clip'] }, store: { patch: vi.fn() },
    mesh: null, disabled: false, selectedTool: 'references', onSelect: vi.fn() };
  const { result, rerender } = renderHook(options => useModelTools(options), { initialProps: props });
  const active = () => result.current.tools.filter(tool => tool.active).map(tool => tool.id);
  // The tool's row on screen, by its name.
  const shown = () => { const view = render(<>{result.current.subToolbar}</>); const names = [...view.container.querySelectorAll('[role=toolbar][data-cad-sub-toolbar]')]
    .map(row => row.getAttribute('aria-label')); view.unmount(); return names; };
  act(() => result.current.tools.find(tool => tool.id === 'exploded').onSelect());
  expect(props.onSelect).toHaveBeenCalledWith('exploded');
  expect(props.store.patch).toHaveBeenLastCalledWith({ exploded: { enabled: false, amount: 0 } });
  rerender({ ...props, selectedTool: 'exploded' });
  expect([active(), shown()]).toEqual([['exploded'], ['Explode controls']]);
  // A second press on the tool in hand does nothing.
  act(() => result.current.tools.find(tool => tool.id === 'exploded').onSelect());
  expect(props.onSelect).toHaveBeenCalledOnce();
  // Applied, then put down: the effect stays on the model, and nothing of it on screen.
  const applied = { ...props, view: { ...neutral(), exploded: { enabled: true, amount: .4 } } };
  rerender({ ...applied, selectedTool: 'references' });
  expect([active(), shown()]).toEqual([[], []]);
  // Taken up again it is as it was: nothing is patched.
  props.store.patch.mockClear();
  act(() => result.current.tools.find(tool => tool.id === 'exploded').onSelect());
  expect(props.store.patch).not.toHaveBeenCalled();
});

it('a clip at zero keeps its row while in hand, and no tool leaves one behind', () => {
  const props = { view: { ...neutral(), clip: { ...neutral().clip, enabled: true, at: 5 } },
    features: { settings: ['clip'] }, store: { patch: vi.fn() }, mesh: null, disabled: false, selectedTool: 'clip', onSelect: vi.fn() };
  const { result, rerender } = renderHook(options => useModelTools(options), { initialProps: props });
  const kept = () => { const view = render(<>{result.current.subToolbar}</>); const count = view.container.querySelectorAll('[role=toolbar][data-cad-sub-toolbar]').length; view.unmount(); return count; };
  expect(kept()).toBe(1);
  rerender({ ...props, view: neutral() });
  expect(kept()).toBe(1);
  rerender({ ...props, view: neutral(), selectedTool: 'display' });
  expect(kept()).toBe(0);
});

it('offers Explode and Clip on every STEP, a single part too, and idles them while the model loads', () => {
  const part = (id: string) => ({ id, bounds: { min: [0, 0, 0], max: [1, 1, 1] } });
  const props = { view: neutral(), features: { settings: ['exploded', 'clip'] }, store: { patch: vi.fn() },
    mesh: null as null | object, disabled: true, selectedTool: 'references', onSelect: vi.fn() };
  const { result, rerender } = renderHook(options => useModelTools(options), { initialProps: props });
  const offered = () => result.current.tools.map(tool => `${tool.id}:${tool.disabled}`);
  // Loading, idle; then every STEP has both, a single part too (its Explode separates nothing).
  expect(offered()).toEqual(['exploded:true', 'clip:true']);
  rerender({ ...props, disabled: false, mesh: { parts: [part('a')] } });
  expect(offered()).toEqual(['exploded:false', 'clip:false']);
  rerender({ ...props, disabled: false, mesh: { parts: [part('a'), part('b')] } });
  expect(offered()).toEqual(['exploded:false', 'clip:false']);
});
