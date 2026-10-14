import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { useStepMeasure } from './useStepMeasure.js';
import { useViewerHistory } from '../../kit/shell/useViewerHistory.js';

afterEach(cleanup);

it("each measurement taken or deleted is a step in the file view's history; a new file's start over", () => {
  const hook = renderHook(({ fileKey }) => {
    const history = useViewerHistory();
    return { history, measure: useStepMeasure({ fileKey, revision: '1', picking: true, history }) };
  }, { initialProps: { fileKey: 'a.step' } });
  const current = () => hook.result.current;
  const measure = (from: number[], to: number[]) => {
    act(() => current().measure.onPick({ point: from }));
    act(() => current().measure.onPick({ point: to }));
  };
  measure([0, 0, 0], [3, 4, 0]);
  measure([0, 0, 0], [0, 0, 5]);
  const [first, second] = current().measure.measurements.map(item => item.id);
  act(() => current().measure.onDelete(first));
  expect(current().measure.measurements.map(item => item.id)).toEqual([second]);
  act(() => { current().history.undo(); });
  expect(current().measure.measurements.map(item => item.id)).toEqual([first, second]);
  act(() => { current().history.undo(); });
  expect(current().measure.measurements.map(item => item.id)).toEqual([first]);
  act(() => { current().history.redo(); });
  expect(current().measure.measurements.map(item => item.id)).toEqual([first, second]);
  // Undo and Redo are not steps of their own: the history is as long as it was.
  expect(current().history).toMatchObject({ canUndo: true, canRedo: true });
  hook.rerender({ fileKey: 'b.step' });
  expect(current().measure.measurements).toEqual([]);
  expect(current().history).toMatchObject({ canUndo: false, canRedo: false });
});
