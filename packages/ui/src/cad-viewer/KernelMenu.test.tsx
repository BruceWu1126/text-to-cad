import React from 'react';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { KernelMenu, useKernelVariants } from './KernelMenu.js';
Object.assign(globalThis, { React });
afterEach(cleanup);

const family = [
  { kernel: 'brep', file: '/m/STEP/a.step', current: true },
  { kernel: 'sdm', file: '/m/STEP/.kernels/sdm/a.stl', current: false },
  { kernel: 'usd-brep', file: '/m/STEP/.kernels/usd-brep/a.step', current: false },
];

test('the Kernel menu names the build on screen and shows another in its place', () => {
  const opened: string[] = [];
  render(<KernelMenu variants={family} onOpen={file => opened.push(file)} />);
  const trigger = screen.getByRole('button', { name: 'Kernel: BREP' });
  fireEvent.keyDown(trigger, { key: 'Enter' });
  expect(screen.getAllByRole('menuitemradio').map(item => [item.textContent, item.getAttribute('aria-checked')]))
    .toEqual([['BREP', 'true'], ['Emergent Matter SDM', 'false'], ['NVIDIA usd-brep', 'false']]);
  fireEvent.click(screen.getByRole('menuitemradio', { name: 'NVIDIA usd-brep' }));
  expect(opened).toEqual(['/m/STEP/.kernels/usd-brep/a.step']);
});

test('a model built on one kernel has no menu', () => {
  const { container } = render(<KernelMenu variants={[]} onOpen={() => {}} />);
  expect(container.innerHTML).toBe('');
});

test('variants are read for the file on screen, again on focus, and never for a client that cannot list them', async () => {
  const kernelVariants = vi.fn(async (file: string) => family.map(v => ({ ...v, current: v.file === file })));
  const seen: unknown[] = [];
  function Probe({ client, file }: { client: object; file: string }) {
    seen.push(useKernelVariants(client as never, file));
    return null;
  }
  const { rerender } = render(<Probe client={{ kernelVariants }} file="/m/STEP/a.step" />);
  await act(async () => {});
  expect(kernelVariants).toHaveBeenCalledWith('/m/STEP/a.step', expect.objectContaining({ signal: expect.any(AbortSignal) }));
  expect(seen.at(-1)).toHaveLength(3);
  await act(async () => { window.dispatchEvent(new Event('focus')); });
  expect(kernelVariants).toHaveBeenCalledTimes(2);
  // Another file's answer is never shown for this one, and no file reads nothing.
  rerender(<Probe client={{ kernelVariants }} file="" />);
  expect(seen.at(-1)).toEqual([]);
  rerender(<Probe client={{}} file="/m/STEP/a.step" />);
  await act(async () => {});
  expect(seen.at(-1)).toEqual([]);
  expect(kernelVariants).toHaveBeenCalledTimes(2);
});
