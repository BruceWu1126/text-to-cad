import React from 'react';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it } from 'vitest';
import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger } from '@text-to-cad/ui/primitives/dropdown-menu';
import { useDrawingSession } from './session.js';
import { InkStyleSubmenus } from './toolbar.jsx';

afterEach(cleanup);
// A submenu places itself with a ResizeObserver, which jsdom has none of.
globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as any;

let session: ReturnType<typeof useDrawingSession>;
function Menu({ lineStyle = false }) {
  session = useDrawingSession(true);
  return <DropdownMenu open modal={false}>
    <DropdownMenuTrigger>Tool</DropdownMenuTrigger>
    <DropdownMenuContent aria-label="Tool"><InkStyleSubmenus drawing={session} lineStyle={lineStyle} /></DropdownMenuContent>
  </DropdownMenu>;
}

it("a Draw tool's menu sets what is drawn next in submenus, each naming the value in hand; line style is the shapes' only", async () => {
  render(<Menu />);
  const menu = screen.getByRole('menu', { name: 'Tool' });
  expect(within(menu).getAllByRole('menuitem').map(item => item.getAttribute('aria-label')))
    .toEqual(['Color: Neon red', 'Stroke width: Medium', 'Placement: On model']);
  const trigger = within(menu).getByRole('menuitem', { name: 'Placement: On model' });
  await userEvent.setup().click(trigger);
  // The submenu is named for its trigger.
  const placement = await screen.findByRole('menu', { name: 'Placement: On model' });
  expect(within(placement).getAllByRole('menuitemradio').map(item => [item.getAttribute('aria-label'), item.getAttribute('aria-checked')]))
    .toEqual([['On model', 'true'], ['In space', 'false']]);
  fireEvent.click(within(placement).getByRole('menuitemradio', { name: 'In space' }));
  expect(session.placement).toBe('space');
  cleanup();
  render(<Menu lineStyle />);
  expect(within(screen.getByRole('menu', { name: 'Tool' })).getAllByRole('menuitem').map(item => item.getAttribute('aria-label')))
    .toEqual(['Color: Neon red', 'Stroke width: Medium', 'Line style: Solid', 'Placement: On model']);
});
