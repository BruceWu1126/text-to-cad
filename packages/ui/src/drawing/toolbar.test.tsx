import React from 'react';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { useDrawingSession } from './session.js';
import { DrawingToolbar } from './toolbar.jsx';

afterEach(cleanup);

let session: ReturnType<typeof useDrawingSession>;
const history = { canUndo: false, canRedo: false, undo() {}, redo() {} };
function Panel() {
  session = useDrawingSession(true);
  return <DrawingToolbar drawing={session} history={history} />;
}

it("Draw's panel: its tools then its history on one row, and what is drawn next as dropdowns, placement among them", async () => {
  render(<Panel />);
  expect(within(screen.getByRole('group', { name: 'Drawing tools' })).getAllByRole('button').map(button => button.getAttribute('aria-label')))
    .toEqual(['Pen', 'Line', 'Shapes', 'Eraser', 'Undo', 'Redo']);
  const settings = screen.getByRole('group', { name: 'Drawing settings' });
  expect(within(settings).getAllByRole('button').map(button => button.getAttribute('aria-label')))
    .toEqual(['Color', 'Stroke width', 'Line style', 'Placement']);
  expect(session.placement).toBe('surface');
  fireEvent.pointerDown(within(settings).getByRole('button', { name: 'Placement' }), { button: 0, ctrlKey: false });
  const menu = await screen.findByRole('menu', { name: 'Placement' });
  expect(within(menu).getAllByRole('menuitemradio').map(item => [item.getAttribute('aria-label'), item.getAttribute('aria-checked')]))
    .toEqual([['On model', 'true'], ['In space', 'false']]);
  fireEvent.click(within(menu).getByRole('menuitemradio', { name: 'In space' }));
  expect(session.placement).toBe('space');
});
