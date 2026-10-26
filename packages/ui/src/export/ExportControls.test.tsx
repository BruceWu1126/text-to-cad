import React from 'react';
import { cleanup, render, screen, waitFor, within, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ExportRequestError } from '@text-to-cad/core/client';
import { ExportControls } from '../../dist/export/ExportControls.js';
import { useExportView } from '../../dist/export/context.js';
import { I18nProvider } from '../../dist/i18n/index.js';
import { ViewerMobileContext } from '../../dist/file-viewer/responsive.js';

Object.assign(globalThis, { React });
beforeEach(() => { vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} }); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const part = { doc: 'doc', revision: 'v1', file: '/part.xbf', name: 'part.xbf' };
type Shown = typeof part & { planar?: boolean };
function Document({ view }: { view: Shown }) { useExportView(view); return <div>Model</div>; }
function mount(service: any, downloads: any, { locale = 'en', phone = false, view = part as Shown } = {}) {
  // The overlay renders inside the viewer, which says whether its width is a phone's.
  return render(<I18nProvider locale={locale}><ExportControls service={service} downloads={downloads} file={view.file}>{({ navbar, overlay }: any) => <><nav>{navbar}</nav><Document view={view} /><ViewerMobileContext.Provider value={phone}>{overlay}</ViewerMobileContext.Provider></>}</ExportControls></I18nProvider>);
}
const later = () => Date.now() / 1000 + 3600;
function server() {
  const waiting = new Map<string, (value: any) => void>();
  const jobs = new Map<string, any>();
  const service = {
    epoch: vi.fn(async () => 'epoch'),
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
const menu = () => screen.queryByRole('menu', { name: 'Export' });
async function exportOnce(user: ReturnType<typeof userEvent.setup>, format = 'STEP') {
  await user.click(screen.getByRole('button', { name: 'Export', exact: true }));
  await user.click(within(menu()!).getByRole('menuitem', { name: format }));
}

it('the menu is the formats the file\'s kind writes, there at once: nothing is asked of the server before a choice', async () => {
  const kinds: [string, string[]][] = [
    ['/bracket.step', ['STEP', 'STL', '3MF', 'GLB', 'XBF']],
    ['/part.xbf', ['STEP', 'STL', '3MF', 'GLB', 'XBF']],
    ['0f8e2a7c-3b1d-5e4f-9a6b-7c8d9e0f1a2b', ['STEP', 'STL', '3MF', 'GLB', 'XBF']],
    ['/scene.glb', ['STL', '3MF']],
    ['/part.stl', ['3MF', 'GLB']],
    ['/kit.3mf', ['STL', 'GLB']],
  ];
  // A document the view draws flat on z=0 writes a DXF too; a mesh file never does.
  kinds.push(['/plate.xbf#planar', ['STEP', 'STL', '3MF', 'GLB', 'DXF', 'XBF']], ['/scene.glb#planar', ['STL', '3MF']]);
  for (const [shown, labels] of kinds) {
    const [file, planar] = [shown.replace('#planar', ''), shown.endsWith('#planar')];
    const { service } = server();
    const { unmount } = mount(service, { save: vi.fn() }, { view: { ...part, file, name: file.split('/').pop()!, ...(planar ? { planar } : {}) } });
    const user = userEvent.setup({ skipHover: true });
    await user.click(screen.getByRole('button', { name: 'Export', exact: true }));
    const items = within(menu()!).getAllByRole('menuitem');
    expect(items.map(item => item.textContent), shown).toEqual(labels);
    // Plain items alone: no checks, no icons, no heading, no button, no select, no rule, no note.
    for (const item of items) expect(item.querySelector('svg')).toBeNull();
    expect(within(menu()!).queryByRole('button')).toBeNull();
    expect(within(menu()!).queryByRole('combobox')).toBeNull();
    expect(within(menu()!).queryByRole('separator')).toBeNull();
    expect([service.epoch, service.create].map(call => call.mock.calls.length)).toEqual([0, 0]);
    unmount();
  }
  // A view whose file writes nothing, with no exports to show, has no Export at all.
  const { service } = server();
  mount(service, { save: vi.fn() }, { view: { ...part, file: '/arm.urdf', name: 'arm.urdf' } });
  expect(screen.queryByRole('button', { name: 'Export', exact: true })).toBeNull();
});

it('a choice starts its export at once: the toast asks the session\'s epoch, submits with it, and the record keeps it', async () => {
  const { service } = server();
  const downloads = { save: vi.fn(), remember: vi.fn() };
  mount(service, downloads);
  const user = userEvent.setup({ skipHover: true });
  await exportOnce(user, 'GLB');
  await waitFor(() => expect(menu()).toBeNull());
  await waitFor(() => expect(service.create).toHaveBeenCalledTimes(1));
  expect(service.epoch).toHaveBeenCalledTimes(1);
  const request = service.create.mock.calls[0][0];
  expect(request).toEqual({ requestId: expect.any(String), epoch: 'epoch', source: { doc: 'doc', revision: 'v1' }, format: 'glb' });
  await waitFor(() => expect(downloads.remember).toHaveBeenLastCalledWith([{ request, filename: 'part.glb', delivered: false }]));
  expect(await screen.findByRole('region', { name: 'Export part.step' })).toBeTruthy();
  // An xbf's XBF is the file's own bytes: the server copies the original, never writes an xbf of an xbf.
  await exportOnce(user, 'XBF');
  await waitFor(() => expect(service.create).toHaveBeenCalledTimes(2));
  expect(service.create.mock.calls[1][0]).toMatchObject({ format: 'original', epoch: 'epoch' });
  await waitFor(() => expect(downloads.remember).toHaveBeenLastCalledWith([{ request, filename: 'part.glb', delivered: false },
    { request: service.create.mock.calls[1][0], filename: 'part.xbf', delivered: false }]));
});

it('the keyboard opens the menu, walks it and chooses', async () => {
  const { service } = server();
  mount(service, { save: vi.fn() });
  const user = userEvent.setup({ skipHover: true });
  await user.tab();
  expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Export', exact: true }));
  await user.keyboard('{Enter}');
  await waitFor(() => expect(document.activeElement?.textContent).toBe('STEP'));
  await user.keyboard('{ArrowDown}');
  expect(document.activeElement?.textContent).toBe('STL');
  await user.keyboard('{Enter}');
  await waitFor(() => expect(service.create).toHaveBeenCalledTimes(1));
  expect(service.create.mock.calls[0][0].format).toBe('stl');
  await user.click(screen.getByRole('button', { name: 'Export', exact: true }));
  await user.keyboard('{Escape}');
  await waitFor(() => expect(menu()).toBeNull());
});

it('an export the server refuses says why in its toast, as a job\'s failure does', async () => {
  const { service } = server();
  service.epoch.mockRejectedValueOnce(new ExportRequestError('Forbidden.', 403));
  service.create.mockRejectedValueOnce(new ExportRequestError('The source changed. Open Export again to use the displayed revision.', 409));
  mount(service, { save: vi.fn() });
  const user = userEvent.setup({ skipHover: true });
  await exportOnce(user);
  expect((await screen.findByRole('alert')).textContent).toBe('Forbidden.');
  expect(service.create).not.toHaveBeenCalled();
  await exportOnce(user, 'STL');
  await waitFor(() => expect(screen.getAllByRole('alert').map(alert => alert.textContent)).toEqual([
    'Forbidden.', 'The source changed. Open Export again to use the displayed revision.']));
});

it('queues independent exports with no picker; each toast cancels its own job', async () => {
  const { service } = server();
  const downloads = { save: vi.fn() };
  mount(service, downloads);
  const user = userEvent.setup({ skipHover: true });
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
  const user = userEvent.setup({ skipHover: true });
  await exportOnce(user);
  await waitFor(() => expect(jobs.size).toBe(1));
  const [id] = [...jobs.keys()];
  await finish(id);
  await waitFor(() => expect(downloads.save).toHaveBeenCalledTimes(1));
  expect(downloads.save).toHaveBeenCalledWith(`/s/session/__cad/exports/${id}/file`, 'part.step');
  await waitFor(() => expect(downloads.remember).toHaveBeenLastCalledWith([expect.objectContaining({ filename: 'part.step', delivered: true })]));
  expect(screen.getByText('Download started')).toBeTruthy();
  expect(screen.queryByText('Ready to save')).toBeNull();
  await user.click(screen.getByRole('button', { name: 'Dismiss export status' }));
  expect(screen.queryByRole('region', { name: 'Export part.step' })).toBeNull();
  // Show exports follows the formats, after a rule, and brings the toasts back.
  await user.click(screen.getByRole('button', { name: 'Export', exact: true }));
  expect(within(menu()!).getAllByRole('menuitem').map(item => item.textContent)).toEqual(['STEP', 'STL', '3MF', 'GLB', 'XBF', 'Show exports (1)']);
  await user.click(within(menu()!).getByRole('menuitem', { name: 'Show exports (1)' }));
  await user.click(await screen.findByRole('button', { name: 'Download again' }));
  expect(downloads.save).toHaveBeenCalledTimes(2);
  expect(service.create).toHaveBeenCalledTimes(1);
  expect(service.release).not.toHaveBeenCalled();
});

it('a restored request reconnects by identity and downloads only what it never handed over', async () => {
  const { service } = server();
  const request = (id: string) => ({ requestId: id, epoch: 'epoch', source: { doc: 'doc', revision: 'v1' }, format: 'step' });
  service.create.mockImplementation(async value => ({ ...value, id: value.requestId, filename: `${value.requestId}.step`, state: 'ready', stage: 'Ready to save', sequence: 1, createdAt: Date.now() / 1000, finishedAt: Date.now() / 1000, expiresAt: later(), bytes: 5 }));
  const downloads = { save: vi.fn(), remember: vi.fn(), restore: () => [
    { request: request('handed-over'), filename: 'one.step', delivered: true },
    { request: request('never-handed'), filename: 'two.step', delivered: false }] };
  mount(service, downloads);
  await waitFor(() => expect(service.create).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(downloads.save).toHaveBeenCalledTimes(1));
  expect(downloads.save).toHaveBeenCalledWith('/s/session/__cad/exports/never-handed/file', 'never-handed.step');
  expect(screen.getAllByRole('button', { name: 'Download again' })).toHaveLength(2);
  // Each names the session it was made in: a server restarted since refuses it, never runs it again.
  expect(service.epoch).not.toHaveBeenCalled();
});

it('an expired artifact offers no download but Export again; Remove releases it', async () => {
  const { service } = server();
  service.create.mockImplementation(async value => ({ ...value, id: value.requestId, filename: 'old.step', state: 'ready', stage: 'Ready to save', sequence: 1, createdAt: 1, finishedAt: 2, expiresAt: Date.now() / 1000 - 1, bytes: 5 }));
  const downloads = { save: vi.fn(), restore: () => [{ request: { requestId: 'old', epoch: 'epoch', source: { doc: 'doc', revision: 'v1' }, format: 'step' }, filename: 'old.step', delivered: true }] };
  mount(service, downloads);
  expect(await screen.findByText('Expired')).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Download again' })).toBeNull();
  expect(screen.getByRole('button', { name: 'Export again' })).toBeTruthy();
  const user = userEvent.setup({ skipHover: true });
  await user.click(screen.getByRole('button', { name: 'Remove' }));
  expect(service.release).toHaveBeenCalledWith('old');
  await waitFor(() => expect(screen.queryByRole('region', { name: 'Export old.step' })).toBeNull());
  expect(downloads.save).not.toHaveBeenCalled();
});

it('once the browser has fetched the file it is gone: Download again becomes Export again, which runs it again', async () => {
  const { service, jobs, waiting, finish } = server();
  const downloads = { save: vi.fn(), remember: vi.fn() };
  mount(service, downloads);
  const user = userEvent.setup({ skipHover: true });
  await exportOnce(user);
  await waitFor(() => expect(jobs.size).toBe(1));
  const [id] = [...jobs.keys()];
  await finish(id);
  expect(await screen.findByRole('button', { name: 'Download again' })).toBeTruthy();
  // The ready job's status is asked again; it answers when the server deletes the fetched file.
  await waitFor(() => expect(service.status).toHaveBeenCalledTimes(2));
  await act(async () => waiting.get(id)!({ ...jobs.get(id), state: 'expired', stage: 'Export downloaded', fetched: true, sequence: 2,
    finishedAt: Date.now() / 1000, expiresAt: later() }));
  expect(await screen.findByText('Downloaded')).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Download again' })).toBeNull();
  await user.click(screen.getByRole('button', { name: 'Export again' }));
  await waitFor(() => expect(service.create).toHaveBeenCalledTimes(2));
  const [first, again] = service.create.mock.calls.map(call => call[0]);
  expect(again).toEqual({ ...first, requestId: expect.any(String) });
  expect(again.requestId).not.toBe(first.requestId);
  expect(screen.getAllByRole('region', { name: 'Export part.step' })).toHaveLength(1);
  expect(downloads.save).toHaveBeenCalledTimes(1);
});

it('the menu and the toast speak the page language, never the server stage', async () => {
  const { service } = server();
  mount(service, { save: vi.fn() }, { locale: 'ja' });
  const user = userEvent.setup({ skipHover: true });
  await user.click(screen.getByRole('button', { name: 'エクスポート', exact: true }));
  await user.click(screen.getByRole('menuitem', { name: 'STEP' }));
  expect(await screen.findByText(/^開始を待っています · /)).toBeTruthy();
  expect(screen.queryByText(/Waiting for worker/)).toBeNull();
});

it('on a phone the toasts sit at the view\'s bottom with 36px buttons; a desktop keeps them top-right', async () => {
  for (const phone of [false, true]) {
    const { service } = server();
    const { container, unmount } = mount(service, { save: vi.fn() }, { phone });
    const user = userEvent.setup({ skipHover: true });
    await exportOnce(user);
    const toast = await screen.findByRole('region', { name: 'Export part.step' });
    const stack = container.querySelector('[data-export-stack]')!;
    const buttons = within(toast).getAllByRole('button');
    if (phone) {
      expect(stack.className).toMatch(/\bbottom-2\b/);
      expect(stack.className).not.toMatch(/\btop-2\b/);
      for (const button of buttons) expect(button.className).toMatch(/\b(?:h-9|size-9)\b/);
    } else {
      expect(stack.className).toMatch(/\btop-2\b.*\bitems-end\b/);
      for (const button of buttons) expect(button.className).toMatch(/\b(?:h-6|size-6)\b/);
    }
    unmount();
  }
});
