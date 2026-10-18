import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { MissingCadFile } from '../../dist/cad-viewer/MissingCadFile.js';

beforeEach(() => vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} })));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const FILE = '/parts/new.step';
const TREE = 'a'.repeat(64);
function client() {
  let update: (next: object) => void = () => {};
  const stop = vi.fn();
  return {
    refresh: vi.fn(async () => {}), stop,
    observeEditingPreview: vi.fn((_file: string, next: typeof update) => { update = next; return stop; }),
    publish: (next: object) => act(() => update({ output: FILE, epoch: 'e', revision: 1, ...next })),
  };
}

it('a missing STEP with no matching job stays missing; a failed build after a provisional preview is not a save failure', () => {
  const service = client();
  render(<MissingCadFile file={FILE} client={service as never} entry={null} />);
  expect(screen.getByText('File does not exist')).toBeTruthy();
  service.publish({ epoch: undefined, state: 'disconnected' });
  expect(screen.getByText('File does not exist')).toBeTruthy();
  service.publish({ state: 'done', superseded: true, saved: { tree: TREE, documentHash: 'deleted-file' } });
  expect(screen.getByText('File does not exist')).toBeTruthy();
  service.publish({ state: 'failed', error: 'Invalid mate declaration', preview: { tree: TREE, parts: {}, complete: false } });
  expect(screen.getByText('Couldn’t open the model')).toBeTruthy();
  expect(screen.getByText('Invalid mate declaration')).toBeTruthy();
  expect(screen.queryByText('Couldn’t save the model')).toBeNull();
  expect(screen.queryByText(/viewing its.*preview/)).toBeNull();
});

it('a first failed save is shown without inventing a file or preview; a later confirmed save reopens through the injected reload', () => {
  const service = client();
  const reload = vi.fn();
  const view = (entry: object | null) => <MissingCadFile file={FILE} client={service as never} entry={entry as never} reload={reload} />;
  const { rerender, unmount } = render(view(null));
  service.publish({ state: 'failed', failureStage: 'save', saveFailure: { output: FILE, tree: TREE }, error: 'Disk full' });
  expect(screen.getByText('Couldn’t save the model')).toBeTruthy();
  expect(screen.getByText('Disk full')).toBeTruthy();
  expect(screen.getByText(/cadgen export/)).toBeTruthy();
  expect(screen.queryByText(/viewing/)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(reload).toHaveBeenCalledTimes(1);
  reload.mockClear();
  service.publish({ revision: 2, state: 'building', saving: { tree: TREE } });
  expect(screen.queryByText('Couldn’t save the model')).toBeNull();
  service.publish({ revision: 2, state: 'done', saved: { tree: TREE, documentHash: 'saved' } });
  expect(reload).not.toHaveBeenCalled();
  rerender(view({ file: FILE, documentHash: 'old' }));
  expect(reload).not.toHaveBeenCalled();
  rerender(view({ file: '/parts/other.step', documentHash: 'saved' }));
  expect(reload).not.toHaveBeenCalled();
  rerender(view({ file: FILE, documentHash: 'saved' }));
  expect(reload).toHaveBeenCalledTimes(1);
  rerender(view({ file: FILE, documentHash: 'saved' }));
  expect(reload).toHaveBeenCalledTimes(1);
  unmount();
  expect(service.stop).toHaveBeenCalledTimes(1);
  service.publish({ revision: 3, state: 'failed', failureStage: 'save', saveFailure: { output: FILE, tree: TREE } });
  expect(reload).toHaveBeenCalledTimes(1);
});

it('non-STEP files keep the ordinary missing-file presentation without a build subscription', () => {
  const service = client();
  render(<MissingCadFile file="/parts/new.stl" client={service as never} entry={null} />);
  expect(screen.getByText('File does not exist')).toBeTruthy();
  expect(service.observeEditingPreview).not.toHaveBeenCalled();
});
