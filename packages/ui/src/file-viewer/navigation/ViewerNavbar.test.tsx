import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ViewerNavbar } from '../../../dist/file-viewer/navigation/ViewerNavbar.js';
import { RECENT_ROWS, recentRows } from '../../../dist/file-viewer/navigation/RecentList.js';
import { ControlUsageProvider } from '../../../dist/usage/controls.js';

beforeEach(() => vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const FILE = '/home/me/models/a.step';
const MODEL = '6f1c2a4e-1b2c-5d3e-8f4a-0123456789ab';
const chevron = () => document.querySelector('[data-file-name] svg.lucide-chevron-down');
const glyph = () => document.querySelector('[data-file-name] [data-glyph]')?.getAttribute('data-glyph');
const now = Date.now() / 1000;
const ROWS = [
  { path: FILE, name: 'a.step', kind: 'file', opened: now - 2 * 3600, updated: now - 30 * 86400, missing: false },
  { path: MODEL, name: 'bracket', kind: 'model', opened: null, updated: now - 5 * 60, missing: false },
  { path: '/home/me/models/gone.stl', name: 'gone.stl', kind: 'file', opened: now - 3 * 86400, updated: null, missing: true },
];
const explorer = (list = async () => []) => ({ source: { list: vi.fn(list), search: vi.fn(async () => ({ paths: [], truncated: false })) }, onOpen: vi.fn() });
const recent = (rows = ROWS) => ({ list: vi.fn(async () => rows), onOpen: vi.fn() });

it('shows the name alone, with no glyph, and a chevron where it opens the name menu; a plain name has none', () => {
  const { rerender } = render(<ViewerNavbar file={FILE} explorer={explorer()} />);
  const trigger = document.querySelector('[data-file-name]')!;
  expect(trigger.tagName).toBe('BUTTON');
  expect(trigger.textContent).toBe('a.step');
  expect(glyph()).toBeUndefined();
  expect([...trigger.querySelectorAll('svg')]).toEqual([chevron()]);
  expect(chevron()?.getAttribute('aria-hidden')).toBe('true');
  expect(chevron()?.getAttribute('class')).toContain('shrink-0');
  rerender(<ViewerNavbar file={FILE} explorer={null} />);
  expect(document.querySelector('[data-file-name]')!.tagName).toBe('SPAN');
  expect(chevron()).toBeNull();
  rerender(<ViewerNavbar file={MODEL} name="bracket" kind="model" explorer={null} />);
  expect([document.querySelector('[data-file-name]')!.textContent, document.querySelector('[data-file-name] svg')]).toEqual(['bracket', null]);
});

it('opens Recent and Files as two tabs, on Files over a file and on Recent over a model', async () => {
  const user = userEvent.setup();
  render(<ViewerNavbar file={FILE} explorer={explorer()} recent={recent()} />);
  await user.click(document.querySelector<HTMLElement>('[data-file-name]')!);
  const tabs = within(document.querySelector<HTMLElement>('[data-file-explorer]')!).getAllByRole('tab');
  expect(tabs.map(tab => [tab.textContent, tab.getAttribute('aria-selected')])).toEqual([['Recent', 'false'], ['Files', 'true']]);
  // Files is the explorer itself: its breadcrumb and filter.
  expect(document.querySelector('[data-file-explorer] [data-folder-explorer]')).not.toBeNull();
  expect(screen.getByRole('textbox', { name: 'Filter files' })).toBeTruthy();
  cleanup();
  render(<ViewerNavbar file={MODEL} name="bracket" kind="model" explorer={{ ...explorer(), folder: '/home/me/models' }} recent={recent()} />);
  await user.click(document.querySelector<HTMLElement>('[data-file-name]')!);
  expect(screen.getByRole('tab', { name: 'Recent' }).getAttribute('aria-selected')).toBe('true');
  expect(await screen.findByText('bracket', { selector: '[data-recent-row] *' })).toBeTruthy();
});

it('lists Recent by last activity, each row its glyph, name and age, the one on screen marked, and a row opens its document', async () => {
  const user = userEvent.setup();
  const menu = recent();
  render(<ViewerNavbar file={MODEL} name="bracket" kind="model" explorer={explorer()} recent={menu} />);
  await user.click(document.querySelector<HTMLElement>('[data-file-name]')!);
  await screen.findByText('gone.stl');
  const rows = [...document.querySelectorAll<HTMLElement>('[data-recent-row]')];
  expect(rows.map(row => [row.querySelector('[data-glyph]')?.getAttribute('data-glyph'), row.textContent])).toEqual([
    ['model', 'bracket5m ago'], ['file', 'a.step2h ago'], ['file', 'gone.stl3d ago']]);
  expect(rows.map(row => row.getAttribute('aria-current'))).toEqual(['true', null, null]);
  expect((rows[2] as HTMLButtonElement).disabled).toBe(true);
  // No footer, no extra rows: the Home is one click away.
  expect(document.querySelector('[data-recent-list]')!.querySelectorAll('button').length).toBe(3);
  // Down from the menu walks into the rows.
  fireEvent.keyDown(document.querySelector('[data-file-explorer]')!, { key: 'ArrowDown' });
  expect(document.activeElement).toBe(rows[0]);
  await user.click(rows[1]);
  expect(menu.onOpen).toHaveBeenCalledWith(FILE);
  expect(document.querySelector('[data-file-explorer]')).toBeNull();
});

it('keeps Recent to the eight latest, and a menu with one of the two has no tabs', async () => {
  const many = Array.from({ length: 12 }, (_, index) => ({ path: `/m/${index}.step`, name: `${index}.step`, opened: index, updated: null }));
  expect(recentRows(many).map(row => row.name)).toEqual(['11.step', '10.step', '9.step', '8.step', '7.step', '6.step', '5.step', '4.step']);
  expect(RECENT_ROWS).toBe(8);
  const user = userEvent.setup();
  render(<ViewerNavbar file={MODEL} name="bracket" kind="model" recent={recent()} />);
  await user.click(document.querySelector<HTMLElement>('[data-file-name]')!);
  await screen.findByText('a.step');
  expect(screen.queryByRole('tab')).toBeNull();
});

it('starts a model\'s Files in the folder its record registered', async () => {
  const user = userEvent.setup();
  const files = explorer(async () => [{ path: '/home/me/models/b.step', name: 'b.step', kind: 'file' }]);
  render(<ViewerNavbar file={MODEL} name="bracket" kind="model" explorer={{ ...files, folder: '/home/me/models' }} recent={recent()} />);
  await user.click(document.querySelector<HTMLElement>('[data-file-name]')!);
  await act(async () => { await user.click(screen.getByRole('tab', { name: 'Files' })); });
  await screen.findByText('b.step');
  expect(files.source.list).toHaveBeenCalledWith('/home/me/models', expect.anything());
});

it("counts the name menu's tabs as a person sees them, Recent and Files (the explorer), and a row opened from Recent", async () => {
  const user = userEvent.setup();
  const reports: { control: string; count: number }[][] = [];
  const view = render(<ControlUsageProvider report={counts => reports.push(counts)}>
    <ViewerNavbar file={MODEL} name="bracket" kind="model" explorer={explorer()} recent={recent()} /></ControlUsageProvider>);
  await user.click(document.querySelector<HTMLElement>('[data-file-name]')!); // opens on Recent
  await user.click(screen.getByRole('tab', { name: 'Files' }));
  await user.click(screen.getByRole('tab', { name: 'Recent' }));
  await user.click((await screen.findByText('a.step', { selector: '[data-recent-row] *' })).closest('button')!);
  view.unmount();
  const said = Object.fromEntries(reports.flat().map(({ control, count }) => [control, count]));
  expect(said).toEqual({ recent: 2, explorer: 1, recent_open: 1 });
});
