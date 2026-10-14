import React from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import InkOverlay from '../../../../../dist/renderers/kit/tools/draw/InkOverlay.js';
import { ViewerHostContext } from '../../../../../dist/host/context.js';
import { testHost } from '../../../../../dist/host/testing/host.js';

// Projecting needs a WebGL scene: here a mark goes into the drawing as it was made.
vi.mock('../../../../../dist/renderers/kit/tools/draw/ink3d.js', () => ({
  projectInk: (_runtime: unknown, marks: unknown[]) => marks,
  inkOnScreen: () => [],
  moveInk: () => [],
}));

beforeEach(() => { vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function mount(placement = 'surface') {
  const drawing = { ink: [], selected: [], tool: 'text', color: '#ff2d55', strokeWidth: 2, strokeStyle: 'solid', placement,
    add: vi.fn(), remove: vi.fn(), replace: vi.fn(), select: vi.fn(), undo: vi.fn(), redo: vi.fn() };
  render(<ViewerHostContext.Provider value={testHost()}><InkOverlay drawing={drawing} runtimeRef={{ current: {} }} /></ViewerHostContext.Provider>);
  return { drawing, surface: document.querySelector('[data-cad-drawing-overlay]') as HTMLElement };
}
// A press on the surface as a browser makes it: the pointerdown (whose work React has done by the
// time the next event comes), then the mousedown, whose default focuses what was pressed.
function press(surface: HTMLElement, x: number, y: number) {
  fireEvent.pointerDown(surface, { button: 0, buttons: 1, pointerId: 1, clientX: x, clientY: y });
  if (fireEvent.mouseDown(surface, { button: 0, clientX: x, clientY: y })) surface.focus();
  fireEvent.pointerUp(surface, { button: 0, pointerId: 1, clientX: x, clientY: y });
}
const field = () => screen.queryByRole('textbox', { name: 'Text' }) as HTMLTextAreaElement | null;

it('writes text where the surface was pressed and puts it down as a card on Enter, Shift+Enter making a new line', async () => {
  const { drawing, surface } = mount();
  press(surface, 120, 80);
  // The press that opened the text leaves the keyboard in it.
  expect(field()).not.toBeNull();
  expect(document.activeElement).toBe(field());
  expect(field()!.style).toMatchObject({ left: '120px', top: '80px' });
  await userEvent.keyboard('Fender{Shift>}{Enter}{/Shift}radius{Enter}');
  expect(field()).toBeNull();
  expect(drawing.add).toHaveBeenCalledTimes(1);
  expect(drawing.add.mock.calls[0][0]).toMatchObject([{ kind: 'text', text: 'Fender\nradius', color: '#ff2d55', placement: 'surface', corners: [{ x: 120, y: 80 }, {}, {}, {}] }]);
  // The keyboard is the surface's again, for ⌘Z.
  expect(document.activeElement).toBe(surface);
});

it('puts the text down when the surface is pressed elsewhere, without opening another', async () => {
  const { drawing, surface } = mount();
  press(surface, 40, 40);
  await userEvent.keyboard('Edge');
  press(surface, 200, 150);
  expect(field()).toBeNull();
  expect(drawing.add.mock.calls.map(([items]) => items[0].text)).toEqual(['Edge']);
});

it('puts each mark down with the drawing\'s placement: in space, when that is the setting', async () => {
  const { drawing, surface } = mount('space');
  press(surface, 40, 40);
  await userEvent.keyboard('Datum{Enter}');
  expect(drawing.add.mock.calls[0][0]).toMatchObject([{ kind: 'text', text: 'Datum', placement: 'space' }]);
});

it('drops the text on Escape, and puts nothing down for none written', async () => {
  const { drawing, surface } = mount();
  press(surface, 40, 40);
  await userEvent.keyboard('gone{Escape}');
  expect(field()).toBeNull();
  expect(document.activeElement).toBe(surface);
  press(surface, 60, 60);
  press(surface, 80, 80);
  expect(drawing.add).not.toHaveBeenCalled();
});
