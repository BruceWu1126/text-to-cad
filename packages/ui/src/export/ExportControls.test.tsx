import React from 'react';
import { cleanup, render, screen, waitFor, within, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ExportControls } from '../../dist/export/ExportControls.js';
import { useExportView } from '../../dist/export/context.js';

Object.assign(globalThis, { React });
beforeEach(() => { vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} }); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const source = { doc: 'doc', revision: 'v1', file: '/part.xbf', name: 'part.xbf', part: null, pose: null, preview: false };
function Document() { useExportView(source); return <div>Model</div>; }
function mount(service: any, downloads: any) {
  return render(<ExportControls service={service} downloads={downloads} file="/part.xbf">{({ navbar, overlay }: any) => <><nav>{navbar}</nav><Document />{overlay}</>}</ExportControls>);
}
const formats = { epoch: 'epoch', formats: [{ format: 'step', label: 'STEP', extension: '.step', reason: null }] };
const destination = () => ({ name: 'local.step', write: vi.fn(async () => {}) });
function server() {
  const waiting = new Map<string, (value: any) => void>();
  const jobs = new Map<string, any>();
  const service = {
    options: vi.fn(async () => formats),
    create: vi.fn(async (request: any) => {
      const job = { ...request, id: request.requestId, filename: 'part.step', state: 'queued', stage: 'Queued', sequence: 0, createdAt: Date.now() / 1000 };
      jobs.set(job.id, job); return job;
    }),
    status: vi.fn((id: string, _sequence: number, signal: AbortSignal) => new Promise((resolve, reject) => {
      waiting.set(id, resolve); signal.addEventListener('abort', () => reject(signal.reason), { once: true });
    })),
    cancel: vi.fn(async (id: string) => {
      const job = { ...jobs.get(id), state: 'cancelled', stage: 'Export cancelled', sequence: 1 };
      jobs.set(id, job); waiting.get(id)?.(job); return job;
    }),
    release: vi.fn(async () => ({})),
    download: vi.fn(async () => new Response('bytes')),
  };
  return { service, jobs, waiting };
}
async function open(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: 'Export', exact: true }));
  await waitFor(() => expect((screen.getByRole('button', { name: 'Choose location & export…' }) as HTMLButtonElement).disabled).toBe(false));
}

it('queues independent exports after destination selection; each toast cancels its own job', async () => {
  const { service } = server();
  let choose!: (value: any) => void;
  const downloads = { choose: vi.fn(() => new Promise(resolve => { choose = resolve; })) };
  mount(service, downloads);
  const user = userEvent.setup();
  await open(user);
  await user.click(screen.getByRole('button', { name: 'Choose location & export…' }));
  expect(service.create).not.toHaveBeenCalled();
  await act(async () => choose(destination()));
  await waitFor(() => expect(service.create).toHaveBeenCalledTimes(1));
  await open(user);
  await user.click(screen.getByRole('button', { name: 'Choose location & export…' }));
  await act(async () => choose(destination()));
  await waitFor(() => expect(service.create).toHaveBeenCalledTimes(2));
  const toasts = screen.getAllByRole('region', { name: 'Export local.step' });
  expect(toasts).toHaveLength(2);
  await user.click(within(toasts[1]).getByRole('button', { name: 'Cancel', exact: true }));
  await waitFor(() => expect(service.cancel).toHaveBeenCalledTimes(1));
  expect(service.cancel.mock.calls[0][0]).toBe(service.create.mock.calls[1][0].requestId);
  expect(within(toasts[0]).getByRole('button', { name: 'Cancel', exact: true })).toBeTruthy();
});

it('a cancelled destination creates no job; unsupported browser explains the limitation', async () => {
  const { service } = server();
  const view = mount(service, { choose: vi.fn(async () => null) });
  const user = userEvent.setup();
  await open(user);
  await user.click(screen.getByRole('button', { name: 'Choose location & export…' }));
  expect(service.create).not.toHaveBeenCalled();
  view.unmount();
  mount(service, { unavailable: 'A local save picker is required.', choose: vi.fn() });
  await user.click(await screen.findByRole('button', { name: 'Export', exact: true }));
  expect(screen.getByText('A local save picker is required.')).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Choose location & export…' })).toBeNull();
});

it('save failure retains the artifact; retry saves without a second generation', async () => {
  const { service, jobs, waiting } = server();
  const first = destination(); first.write.mockRejectedValueOnce(new Error('Disk full'));
  const second = destination();
  const downloads = { choose: vi.fn().mockResolvedValueOnce(first).mockResolvedValueOnce(second) };
  mount(service, downloads);
  const user = userEvent.setup();
  await open(user);
  await user.click(screen.getByRole('button', { name: 'Choose location & export…' }));
  await waitFor(() => expect(waiting.size).toBe(1));
  const [id, job] = [...jobs.entries()][0];
  await act(async () => waiting.get(id)!({ ...job, state: 'ready', stage: 'Ready to save', bytes: 5, expiresAt: Date.now() / 1000 + 3600 }));
  expect(await screen.findByText('Disk full')).toBeTruthy();
  await user.click(screen.getByRole('button', { name: 'Retry save…' }));
  await waitFor(() => expect(second.write).toHaveBeenCalledTimes(1));
  expect(service.create).toHaveBeenCalledTimes(1);
  expect(service.download).toHaveBeenCalledTimes(2);
  expect(await screen.findByText('Saved')).toBeTruthy();
});

it('restored requests reconnect by the same identity and ask for a new local destination', async () => {
  const { service } = server();
  const request = { requestId: 'restored-request', epoch: 'epoch', source: { doc: 'doc', revision: 'v1', part: null, pose: null }, format: 'step' };
  service.create.mockImplementation(async value => ({ ...value, id: value.requestId, filename: 'part.step', state: 'ready', stage: 'Ready to save', sequence: 1, createdAt: Date.now() / 1000, expiresAt: Date.now() / 1000 + 3600, bytes: 5 }));
  const chosen = destination();
  const downloads = { restore: () => [{ request, filename: 'local.step' }], remember: vi.fn(), choose: vi.fn(async () => chosen) };
  mount(service, downloads);
  expect(await screen.findByText('Ready to save')).toBeTruthy();
  expect(service.create).toHaveBeenCalledWith(request, expect.any(AbortSignal));
  expect(downloads.choose).not.toHaveBeenCalled();
  expect(service.download).not.toHaveBeenCalled();
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: 'Choose location & save…' }));
  expect(await screen.findByText('Saved')).toBeTruthy();
  expect(chosen.write).toHaveBeenCalledTimes(1);
  expect(service.create).toHaveBeenCalledTimes(1);
});
