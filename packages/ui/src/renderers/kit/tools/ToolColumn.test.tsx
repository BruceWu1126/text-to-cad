import React from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ToolColumn from '../../../../dist/renderers/kit/tools/ToolColumn.js';

// The column alone, for the cases a renderer with a strip and a tree never reaches: a column whose
// one tool's panel needs no strip, and a stack with no panel to put away. (The column under the
// shell — the strip, its stack, the person's choice and the motion — is `ShellChrome.test.tsx`'s.)
beforeEach(() => { vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} }); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("a column with no strip has no strip's row, and its panel's toggle floats at the panel's top-right corner", () => {
  const changes: boolean[] = [];
  const column = (hidden: boolean) => <ToolColumn tools={[]} hidden={hidden} onHiddenChange={next => changes.push(next)}>
    <section aria-label="Animation controls" data-tool-panel="fixed">Play</section>
  </ToolColumn>;
  const { rerender } = render(column(false));
  expect(document.querySelector('[data-cad-tool-row]')).toBeNull();
  const hide = screen.getByRole('button', { name: 'Hide panels' });
  expect(hide.getAttribute('aria-controls')).toBe(screen.getByRole('region', { name: 'Animation controls' }).parentElement!.id);
  fireEvent.click(hide);
  expect(changes).toEqual([true]);
  // The choice is the caller's to keep: drawn hidden, the panel is put away and the toggle stays.
  rerender(column(true));
  expect(screen.queryByRole('region', { name: 'Animation controls' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Show panels' }));
  expect(changes).toEqual([true, false]);
});

it('a stack with no panel shown has no toggle, whatever was chosen, and its strip stays', () => {
  render(<ToolColumn tools={[{ id: 'select', label: 'Select', icon: <span>S</span>, onSelect: () => {} }]} hidden onHiddenChange={() => {}}>
    <section aria-label="Tree" data-tool-panel="tree" hidden>Rows</section>
  </ToolColumn>);
  expect(screen.queryByRole('button', { name: /^(?:Hide|Show) panels$/ })).toBeNull();
  expect(screen.getByRole('group', { name: 'Interaction tools' })).toBeTruthy();
});
