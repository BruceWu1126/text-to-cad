import React from 'react';
import { act, cleanup, render, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useModelTools } from '../../../../../dist/renderers/step/components/workbench/ModelTools.js';

beforeEach(() => { vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} }); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const neutral = () => ({ exploded: { enabled: false, amount: 0 }, clip: { enabled: false, axis: 'x', invert: false } });

it('retains only applied effects after leaving and follows external resets and file changes; only the tool in hand is lit', () => {
  const props = { modelKey: 'one', view: neutral(), features: { sections: ['exploded', 'clip'] }, store: { patch: vi.fn() },
    mesh: null, disabled: false, selectedTool: 'references', onSelect: vi.fn() };
  const { result, rerender } = renderHook(options => useModelTools(options), { initialProps: props });
  const active = () => result.current.tools.filter(tool => tool.active).map(tool => tool.id);
  // The panels on screen, by their names: what is kept.
  const kept = () => { const view = render(<>{result.current.panels}</>); const names = [...view.container.querySelectorAll('section[data-tool-panel]')]
    .map(panel => panel.getAttribute('aria-label')); view.unmount(); return names; };
  act(() => result.current.tools.find(tool => tool.id === 'exploded').onSelect());
  expect(props.onSelect).toHaveBeenCalledWith('exploded');
  rerender({ ...props, selectedTool: 'exploded' });
  expect([active(), kept()]).toEqual([['exploded'], ['Explode controls']]);
  // A second press on the tool in hand does nothing.
  act(() => result.current.tools.find(tool => tool.id === 'exploded').onSelect());
  expect(props.onSelect).toHaveBeenCalledOnce();
  rerender({ ...props, selectedTool: 'references' });
  expect([active(), kept()]).toEqual([[], []]);
  const applied = { ...props, selectedTool: 'references', view: { ...neutral(), exploded: { enabled: true, amount: .4 } } };
  rerender(applied);
  // Kept: its panel stays, and its tool is not lit.
  expect([active(), kept()]).toEqual([[], ['Explode controls']]);
  rerender({ ...applied, view: neutral() });
  expect(kept()).toEqual([]);
  rerender(applied);
  rerender({ ...props, modelKey: 'two', selectedTool: 'references' });
  expect(kept()).toEqual([]);
});

it('returning a clip to zero removes its panel when another tool is chosen', () => {
  const props = { modelKey: 'one', view: { ...neutral(), clip: { ...neutral().clip, enabled: true, at: 5 } },
    features: { sections: ['clip'] }, store: { patch: vi.fn() }, mesh: null, disabled: false, selectedTool: 'clip', onSelect: vi.fn() };
  const { result, rerender } = renderHook(options => useModelTools(options), { initialProps: props });
  const kept = () => { const view = render(<>{result.current.panels}</>); const count = view.container.querySelectorAll('section[data-tool-panel]').length; view.unmount(); return count; };
  expect(kept()).toBe(1);
  rerender({ ...props, view: neutral() });
  expect(kept()).toBe(1);
  rerender({ ...props, view: neutral(), selectedTool: 'display' });
  expect(kept()).toBe(0);
});

it('offers Explode only where there are parts to separate, and idles it while that is unknown', () => {
  const part = (id: string) => ({ id, bounds: { min: [0, 0, 0], max: [1, 1, 1] } });
  const props = { modelKey: 'one', view: neutral(), features: { sections: ['exploded', 'clip'] }, store: { patch: vi.fn() },
    mesh: null as null | object, disabled: true, selectedTool: 'references', onSelect: vi.fn() };
  const { result, rerender } = renderHook(options => useModelTools(options), { initialProps: props });
  const offered = () => result.current.tools.map(tool => `${tool.id}:${tool.disabled}`);
  // Loading: nothing is known about the parts yet, so the strip shows Explode, idle.
  expect(offered()).toEqual(['exploded:true', 'clip:true']);
  // A single part has nothing to separate: Explode is not on the strip at all, never disabled.
  rerender({ ...props, disabled: false, mesh: { parts: [part('a')] } });
  expect(offered()).toEqual(['clip:false']);
  rerender({ ...props, disabled: false, mesh: { parts: [part('a'), part('b')] } });
  expect(offered()).toEqual(['exploded:false', 'clip:false']);
});
