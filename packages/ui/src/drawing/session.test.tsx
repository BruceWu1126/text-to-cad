import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { useDrawingSession } from './session.js';

afterEach(cleanup);
const CAD = { tool: 'freedraw', color: '#ff2d55' };
const stroke = (id: string, extra = {}) => ({ id, kind: 'stroke', part: null, points: new Float32Array(6), color: '#ff2d55', width: 2, dash: 'solid', ...extra });
const view = (x: number) => ({ camera: { position: [x, -100, 0], target: [0, 0, 0], up: [0, 0, 1], orthographicHalfHeight: 50 }, width: 1600, height: 1200 });

it('holds the ink, each add, erase or move one step to undo and redo; clear is one more, discard leaves nothing to undo', () => {
  const session = renderHook(() => useDrawingSession(true, CAD));
  const current = () => session.result.current;
  expect(current()).toMatchObject({ ready: true, tool: 'freedraw', color: '#ff2d55', strokeWidth: 2, strokeStyle: 'solid', placement: 'surface', hasContent: false });
  act(() => current().add([stroke('a'), stroke('b')]));
  act(() => current().remove(['a']));
  act(() => current().replace([stroke('b', { color: '#00e5ff' })]));
  expect(current().ink.map(item => [item.id, item.color])).toEqual([['b', '#00e5ff']]);
  act(() => current().undo());
  act(() => current().undo());
  expect(current().ink.map(item => item.id)).toEqual(['a', 'b']);
  act(() => current().redo());
  expect(current().ink.map(item => item.id)).toEqual(['b']);
  act(() => current().clear());
  expect(current()).toMatchObject({ hasContent: false, canUndo: true });
  act(() => current().undo());
  expect(current().hasDrawing).toBe(true);
  act(() => current().discard());
  expect(current()).toMatchObject({ hasContent: false, canUndo: false, canRedo: false });
});

it('a colour, weight or line style chosen with ink selected restyles it as one step; placement only changes what is drawn next', () => {
  const session = renderHook(() => useDrawingSession(true, CAD));
  const current = () => session.result.current;
  act(() => current().add([stroke('a'), stroke('b')]));
  act(() => current().select(['a']));
  act(() => { current().selectColor('#39ff14'); });
  act(() => { current().selectStrokeWidth(4); });
  act(() => { current().selectStrokeStyle('dashed'); });
  act(() => { current().selectPlacement('space'); });
  expect(current().ink.map(item => [item.id, item.color, item.width, item.dash])).toEqual([['a', '#39ff14', 4, 'dashed'], ['b', '#ff2d55', 2, 'solid']]);
  expect(current()).toMatchObject({ color: '#39ff14', strokeWidth: 4, strokeStyle: 'dashed', placement: 'space' });
  act(() => current().undo());
  expect(current().ink[0].dash).toBe('solid');
});

it('names the view ink goes down in: one drawn in before that is the same, or a new one; discard forgets them', () => {
  const session = renderHook(() => useDrawingSession(true, CAD));
  const current = () => session.result.current;
  let labels: (string | undefined)[] = [];
  act(() => { labels = [current().noteView(view(0)), current().noteView(view(1)), current().noteView(view(100)), current().noteView(null)]; });
  expect(labels).toEqual(['view 1', 'view 1', 'view 2', undefined]);
  expect(current().views.map(entry => entry.label)).toEqual(['view 1', 'view 2']);
  act(() => current().discard());
  expect(current().views).toEqual([]);
});

it('put down, Draw goes back to the pen and selects nothing, and keeps the ink, the colour, the weight, the line style and the placement', () => {
  const session = renderHook(({ active }) => useDrawingSession(active, CAD), { initialProps: { active: true } });
  const current = () => session.result.current;
  act(() => { current().add([stroke('a')]); current().selectTool('selection'); current().select(['a']); });
  act(() => { current().selectColor('#ffffff'); current().selectStrokeWidth(4); current().selectStrokeStyle('dotted'); current().selectPlacement('space'); });
  session.rerender({ active: false });
  expect(current()).toMatchObject({ tool: 'freedraw', selected: [], color: '#ffffff', strokeWidth: 4, strokeStyle: 'dotted', placement: 'space', hasDrawing: true });
});
