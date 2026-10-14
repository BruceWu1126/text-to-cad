import React from 'react';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { useDrawingSession } from './session.js';
import { DrawingToolbar } from './toolbar.jsx';

afterEach(cleanup);

let session: ReturnType<typeof useDrawingSession>;
function Panel() {
  session = useDrawingSession(true);
  return <DrawingToolbar drawing={session} />;
}

it('sets where ink is drawn from the settings dropdowns: on the model by default, in space when chosen', async () => {
  render(<Panel />);
  const settings = screen.getByRole('group', { name: 'Drawing settings' });
  expect(within(settings).getAllByRole('button').map(button => button.getAttribute('aria-label')))
    .toEqual(['Color', 'Stroke width', 'Line style', 'Placement']);
  expect(session.placement).toBe('surface');
  fireEvent.pointerDown(within(settings).getByRole('button', { name: 'Placement' }), { button: 0, ctrlKey: false });
  const menu = await screen.findByRole('menu', { name: 'Placement' });
  expect(within(menu).getAllByRole('menuitemradio').map(item => [item.getAttribute('aria-label'), item.getAttribute('aria-checked')]))
    .toEqual([['On model', 'true'], ['In space', 'false']]);
  fireEvent.click(within(menu).getByRole('menuitemradio', { name: 'In space' }));
  // Chosen, the menu closes; what is drawn next goes in space.
  expect(screen.queryByRole('menu', { name: 'Placement' })).toBeNull();
  expect(session.placement).toBe('space');
});

it("groups the shapes behind one button, the last one taken up on it", async () => {
  render(<Panel />);
  const tools = screen.getByRole('group', { name: 'Drawing tools' });
  expect(within(tools).getAllByRole('button').map(button => button.getAttribute('aria-label')))
    .toEqual(['Pen', 'Rectangle', 'Shapes', 'Text', 'Eraser']);
  fireEvent.pointerDown(within(tools).getByRole('button', { name: 'Shapes' }), { button: 0, ctrlKey: false });
  fireEvent.click(await screen.findByRole('menuitemradio', { name: 'Arrow' }));
  expect(session.tool).toBe('arrow');
  expect(within(tools).getAllByRole('button').map(button => button.getAttribute('aria-label')))
    .toEqual(['Pen', 'Arrow', 'Shapes', 'Text', 'Eraser']);
  // The pen taken up again, the shapes button keeps the arrow.
  fireEvent.click(within(tools).getByRole('button', { name: 'Pen' }));
  expect(within(tools).getByRole('button', { name: 'Arrow' }).getAttribute('aria-pressed')).toBe('false');
});
