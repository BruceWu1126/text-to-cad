import React from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import PreviewChrome from '../../../../dist/renderers/kit/tools/PreviewChrome.js';

afterEach(cleanup);
it('keeps editor controls hidden throughout Preview and restores them on exit', () => {
  const { container, rerender } = render(<PreviewChrome active><button>Tool</button></PreviewChrome>);
  const chrome = container.querySelector('[data-preview-chrome]')!;
  expect(chrome.hasAttribute('hidden')).toBe(true);
  expect(chrome.hasAttribute('inert')).toBe(true);
  fireEvent.pointerMove(document.body);
  expect(screen.queryByRole('button', { name: 'Tool' })).toBeNull();
  rerender(<PreviewChrome active={false}><button>Tool</button></PreviewChrome>);
  expect(chrome.hasAttribute('hidden')).toBe(false);
  expect(chrome.hasAttribute('inert')).toBe(false);
  expect(screen.getByRole('button', { name: 'Tool' })).toBeTruthy();
});

it("puts Preview's own bar over the view's top edge, the name at its left and its controls at its right, and its panel under it, only while it is up", () => {
  const view = render(<PreviewChrome active name="arm.step" controls={<button>Exit preview</button>} panel={<div data-testid="settings" />} />);
  const bar = view.container.querySelector<HTMLElement>('[data-preview-bar]')!;
  expect([bar.querySelector('[data-preview-name]')!.textContent, bar.querySelector('[data-preview-name]')!.nextElementSibling!.hasAttribute('data-preview-controls')]).toEqual(['arm.step', true]);
  expect(screen.getByRole('button', { name: 'Exit preview' }).closest('[data-preview-controls]')).not.toBeNull();
  expect(screen.getByTestId('settings').closest('[data-preview-panel]')).not.toBeNull();
  view.rerender(<PreviewChrome active={false} name="arm.step" controls={<button>Exit preview</button>} panel={<div data-testid="settings" />} />);
  expect(view.container.querySelector('[data-preview-bar]')).toBeNull();
  expect(screen.queryByRole('button', { name: 'Exit preview' })).toBeNull();
});
