import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { FileSheetValueInput, valueInputWidth } from '../../../../dist/renderers/kit/inspector/FileSheet.js';

// The value badge beside a slider — a joint angle, a thickness, a playback speed. It carries a
// draft while it is being typed into and commits it once, and Enter has to be a commit in its own
// right: it used to ask the input to blur and rely on the blur event to write the value, which is
// only true while the input is still the active element. When it was not, the typed number sat in
// the draft looking applied and moved nothing, then landed later, in one jump, on the next focus
// change — a joint the person had already moved on from.

afterEach(() => cleanup());

it('commits on Enter even when no blur follows, and only once when one does', () => {
  const onValueCommit = vi.fn();
  const view = render(<FileSheetValueInput ariaLabel="hour slider value" value="0.00 deg" onValueCommit={onValueCommit} />);
  const input = view.getByLabelText('hour slider value') as HTMLInputElement;

  // Never focused, so `input.blur()` fires nothing: the keystroke is the only thing that can commit.
  fireEvent.change(input, { target: { value: '35' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(onValueCommit.mock.calls).toEqual([['35']]);

  // Focused, so Enter's own blur() really fires. That blur must not write the value again.
  onValueCommit.mockClear();
  input.focus();
  fireEvent.change(input, { target: { value: '12' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(onValueCommit.mock.calls).toEqual([['12']]);

  // And the guard that suppressed that blur is disarmed: the NEXT edit still commits.
  onValueCommit.mockClear();
  input.focus();
  fireEvent.change(input, { target: { value: '7' } });
  fireEvent.blur(input);
  expect(onValueCommit.mock.calls).toEqual([['7']]);
});

it('still discards the draft on Escape', () => {
  const onValueCommit = vi.fn();
  const view = render(<FileSheetValueInput ariaLabel="hour slider value" value="0.00 deg" onValueCommit={onValueCommit} />);
  const input = view.getByLabelText('hour slider value') as HTMLInputElement;

  input.focus();
  fireEvent.change(input, { target: { value: '35' } });
  fireEvent.keyDown(input, { key: 'Escape' });
  expect(onValueCommit).not.toHaveBeenCalled();
  expect(input.value).toBe('0.00 deg');
});


it('sizes the value box to hold its whole value, its padding and its borders: a large exaggeration is never cut off', () => {
  // "×6000" read "×600C" and "×9500000" crowded the box: its width left out the 1px borders and the × glyph.
  for (const value of ['0.00°', '×6000', '×42000', '0.00 mm', '×9500000', '×120000000', '×95000000000', '123456789012345']) {
    const width = valueInputWidth(value);
    const [, ch, rem, px] = /^w-\[calc\((\d+)ch\+([\d.]+)rem\+(\d+)px\)\]$/.exec(width) || [];
    expect(Number(ch), `${value}: ${width}`).toBeGreaterThanOrEqual(Math.min(value.length + 1, 14));
    expect([rem, px], value).toEqual(['0.75', '2']);
  }
});
