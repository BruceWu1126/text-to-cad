import React from 'react';
import { cleanup, render, screen, waitFor, within, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ExportControls } from '../../dist/export/ExportControls.js';
import { useExportView } from '../../dist/export/context.js';
import { I18nProvider } from '../../dist/i18n/index.js';

Object.assign(globalThis, { React });
beforeEach(() => { vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} }); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const source = { doc: 'doc', revision: 'v1', file: '/part.xbf', name: 'part.xbf', part: null, pose: null, preview: false };
function Document() { useExportView(source); return <div>Model</div>; }
function mount(service: any, downloads: any, locale = 'en') {
  return render(<I18nProvider locale={locale}><ExportControls service={service} downloads={downloads} file="/part.xbf">{({ navbar, overlay }: any) => <><nav>{navbar}</nav><Document />{overlay}</>}</ExportControls></I18nProvider>);
}
const formats = { epoch: 'epoch', formats: [{ format: 'step', label: 'STEP', extension: '.step', reason: null }] };
const later = () => Date.now() / 1000 + 3600;
function server() {
  const waiting = new Map<string, (value: any) => void>();
  const jobs = new Map<string, any>();
  const service = {
    options: vi.fn(async () => formats),
    create: vi.fn(async (request: any) => {
      const job = { ...request, id: request.requestId, filename: 'part.step', state: 'queued', stage: 'Waiting for worker', sequence: 0, createdAt: Date.now() / 1000 };
      jobs.set(job.id, job); return job;
    }),
    status: vi.fn((id: string, _sequence: number, signal: AbortSignal) => new Promise((resolve, reject) => {
      waiting.set(id, resolve); signal.addEventListener('abort', () => reject(signal.reason), { once: true });
    })),
    cancel: vi.fn(async (id: string) => {
      const job = { ...jobs.get(id), state: 'cancelled', stage: 'Operation cancelled', sequence: 1 };
      jobs.set(id, job); waiting.get(id)?.(job); return job;
    }),
    release: vi.fn(async () => ({})),
    fileUrl: vi.fn((id: string) => `/s/session/__cad/exports/${id}/file`),
  };
  const finish = async (id: string) => act(async () => waiting.get(id)!({ ...jobs.get(id), state: 'ready', stage: 'Ready to save', sequence: 1, bytes: 5, finishedAt: Date.now() / 1000, expiresAt: later() }));
  return { service, jobs, waiting, finish };
}
async function exportOnce(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: 'Export', exact: true }));
  await user.click(await screen.findByRole('button', { name: 'Export and download' }));
}

it('queues independent exports with no picker; each toast cancels its own job', async () => {
  const { service } = server();
  const downloads = { save: vi.fn() };
  mount(service, downloads);
  const user = userEvent.setup();
  await exportOnce(user);
  await waitFor(() => expect(service.create).toHaveBeenCalledTimes(1));
  await exportOnce(user);
  await waitFor(() => expect(service.create).toHaveBeenCalledTimes(2));
  const toasts = screen.getAllByRole('region', { name: 'Export part.step' });
  expect(toasts).toHaveLength(2);
  expect(within(toasts[0]).getByText(/^Waiting to start · \d+s$/)).toBeTruthy();
  await user.click(within(toasts[1]).getByRole('button', { name: 'Cancel', exact: true }));
  await waitFor(() => expect(service.cancel).toHaveBeenCalledTimes(1));
  expect(service.cancel.mock.calls[0][0]).toBe(service.create.mock.calls[1][0].requestId);
  expect(within(toasts[0]).getByRole('button', { name: 'Cancel', exact: true })).toBeTruthy();
  expect(downloads.save).not.toHaveBeenCalled();
});

it('a ready job is one plain download; a dismissed toast still downloads again until it expires', async () => {
  const { service, jobs, finish } = server();
  const downloads = { save: vi.fn(), remember: vi.fn() };
  mount(service, downloads);
  const user = userEvent.setup();
  await exportOnce(user);
  await waitFor(() => expect(jobs.size).toBe(1));
  const [id] = [...jobs.keys()];
  await finish(id);
  await waitFor(() => expect(downloads.save).toHaveBeenCalledTimes(1));
  expect(downloads.save).toHaveBeenCalledWith(`/s/session/__cad/exports/${id}/file`, 'part.step');
  expect(downloads.remember).toHaveBeenLastCalledWith([expect.objectContaining({ filename: 'part.step', delivered: true })]);
  expect(screen.getByText('Download started')).toBeTruthy();
  expect(screen.queryByText('Ready to save')).toBeNull();
  await user.click(screen.getByRole('button', { name: 'Dismiss export status' }));
  expect(screen.queryByRole('region', { name: 'Export part.step' })).toBeNull();
  await user.click(screen.getByRole('button', { name: 'Export', exact: true }));
  await user.click(await screen.findByRole('button', { name: 'Show exports (1)' }));
  await user.click(await screen.findByRole('button', { name: 'Download again' }));
  expect(downloads.save).toHaveBeenCalledTimes(2);
  expect(service.create).toHaveBeenCalledTimes(1);
  expect(service.release).not.toHaveBeenCalled();
});

it('a restored request reconnects by identity and downloads only what it never handed over', async () => {
  const { service } = server();
  const request = (id: string) => ({ requestId: id, epoch: 'epoch', source: { doc: 'doc', revision: 'v1', part: null, pose: null }, format: 'step' });
  service.create.mockImplementation(async value => ({ ...value, id: value.requestId, filename: `${value.requestId}.step`, state: 'ready', stage: 'Ready to save', sequence: 1, createdAt: Date.now() / 1000, finishedAt: Date.now() / 1000, expiresAt: later(), bytes: 5 }));
  const downloads = { save: vi.fn(), remember: vi.fn(), restore: () => [
    { request: request('handed-over'), filename: 'one.step', delivered: true },
    { request: request('never-handed'), filename: 'two.step', delivered: false }] };
  mount(service, downloads);
  await waitFor(() => expect(service.create).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(downloads.save).toHaveBeenCalledTimes(1));
  expect(downloads.save).toHaveBeenCalledWith('/s/session/__cad/exports/never-handed/file', 'never-handed.step');
  expect(screen.getAllByRole('button', { name: 'Download again' })).toHaveLength(2);
});

it('an expired artifact offers no download; Remove releases it', async () => {
  const { service } = server();
  service.create.mockImplementation(async value => ({ ...value, id: value.requestId, filename: 'old.step', state: 'ready', stage: 'Ready to save', sequence: 1, createdAt: 1, finishedAt: 2, expiresAt: Date.now() / 1000 - 1, bytes: 5 }));
  const downloads = { save: vi.fn(), restore: () => [{ request: { requestId: 'old', epoch: 'epoch', source: { doc: 'doc', revision: 'v1', part: null, pose: null }, format: 'step' }, filename: 'old.step', delivered: true }] };
  mount(service, downloads);
  expect(await screen.findByText('Expired')).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Download again' })).toBeNull();
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: 'Remove' }));
  expect(service.release).toHaveBeenCalledWith('old');
  await waitFor(() => expect(screen.queryByRole('region', { name: 'Export old.step' })).toBeNull());
  expect(downloads.save).not.toHaveBeenCalled();
});

it('the toast speaks the page language, never the server stage', async () => {
  const { service } = server();
  mount(service, { save: vi.fn() }, 'ja');
  const user = userEvent.setup();
  await user.click(await screen.findByRole('button', { name: 'エクスポート', exact: true }));
  await user.click(await screen.findByRole('button', { name: 'エクスポートしてダウンロード' }));
  expect(await screen.findByText(/^開始を待っています · /)).toBeTruthy();
  expect(screen.queryByText(/Waiting for worker/)).toBeNull();
});
