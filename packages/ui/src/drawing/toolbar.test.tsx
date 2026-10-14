import React from 'react';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { useDrawingSession } from './session.js';
import { DrawingToolbar } from './toolbar.jsx';

afterEach(cleanup);

let session: ReturnType<typeof useDrawingSession>;
function Panel() {
  session = useDrawingSession(true);
  return <DrawingToolbar drawing={session} layout="panel" />;
}

it('sets where ink is drawn from the settings row: on the model by default, in space when chosen', () => {
  render(<Panel />);
  const settings = screen.getByRole('group', { name: 'Drawing settings' });
  expect(within(settings).getAllByRole('button').map(button => button.getAttribute('aria-label')))
    .toEqual(['Color', 'Stroke width', 'Line style', 'Placement', 'Undo', 'Redo', 'Clear drawing']);
  expect(session.placement).toBe('surface');
  fireEvent.click(within(settings).getByRole('button', { name: 'Placement' }));
  const options = screen.getByRole('radiogroup', { name: 'Placement' });
  expect(within(options).getAllByRole('radio').map(radio => [radio.getAttribute('aria-label'), radio.getAttribute('aria-checked')]))
    .toEqual([['On model', 'true'], ['In space', 'false']]);
  fireEvent.click(within(options).getByRole('radio', { name: 'In space' }));
  // Chosen, the picker closes; what is drawn next goes in space.
  expect(screen.queryByRole('radiogroup', { name: 'Placement' })).toBeNull();
  expect(session.placement).toBe('space');
});
