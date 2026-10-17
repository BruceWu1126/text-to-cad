import React, { Profiler } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import QuickEdit, { quickEditReferenceIds } from '../../../../../dist/renderers/kit/tools/quick-edit/QuickEdit.js';
import { ViewerHostContext } from '../../../../../dist/host/context.js';
import { testHost } from '../../../../../dist/host/testing/host.js';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const resource = { kind: 'workspace-file', path: '/models/parts/bracket.step', revision: 'r1' } as const;
const face = { resource, target: { kind: 'cad-selector', selectors: ['o1.f2'] } } as const;
const composer = { kind: 'composer', available: true } as const;

function mount(host = testHost(), props: Record<string, unknown> = {}) {
  const view = (next: Record<string, unknown>) => <ViewerHostContext.Provider value={host}><div data-slot="cad-file-view">
    <QuickEdit resource={resource} {...props} {...next} />
  </div></ViewerHostContext.Provider>;
  const rendered = render(view({}));
  return { ...rendered, update: (next: Record<string, unknown>) => rendered.rerender(view(next)) };
}
const box = () => screen.queryByRole('region', { name: 'Quick Edit' });
// A press in the view, as a pick is: what a selection that opens the box follows.
const press = () => {
  const view = document.querySelector('[data-slot="cad-file-view"]')!;
  fireEvent.pointerDown(view);
  fireEvent.pointerUp(view, { button: 0 });
};
const field = () => screen.getByRole('textbox', { name: 'Describe your changes' }) as HTMLElement;
// The note is an editable field: what is written in it, and writing in it, as typing would.
const note = () => field().textContent;
const write = (value: string) => { field().textContent = value; fireEvent.input(field()); };
const buttons = () => within(box()!).getAllByRole('button').map(button => button.getAttribute('aria-label') || button.textContent);

it('follows what it would carry while the note is empty: open for the person with the keyboard, for an agent without it', () => {
  const edge = { resource, target: { kind: 'cad-selector', selectors: ['o1.e3'] } } as const;
  const { update } = mount();
  // With nothing to carry there is nothing at all: no box, and no button standing in for one.
  expect(box()).toBeNull();
  expect(screen.queryByRole('button')).toBeNull();
  // An agent's selection (no press of the person's) opens it, and leaves their keyboard where it was.
  update({ references: [face] });
  expect(box()).not.toBeNull();
  expect(document.activeElement).not.toBe(field());
  update({ references: [] });
  expect(box()).toBeNull();
  // The person's pick opens it with the keyboard in its note; the header counts what is picked.
  press();
  update({ references: [face] });
  expect(document.activeElement).toBe(field());
  expect(within(box()!).queryByText('bracket.step')).toBeNull();
  expect(box()!.querySelector('[data-quick-edit-chip="references"]')!.textContent).toBe('1 ref');
  // Each pick of theirs puts the keyboard back in the note.
  field().blur();
  press();
  update({ references: [face, edge] });
  expect(document.activeElement).toBe(field());
  expect(box()!.querySelector('[data-quick-edit-chip="references"]')!.textContent).toBe('2 refs');
  // A reference naming several picks counts each, and each is listed by its id.
  const several = [{ resource, target: { kind: 'cad-selector', selectors: ['o1.f2', 'o1.e3', 'o1.e4'] } }] as const;
  update({ references: several });
  expect(box()!.querySelector('[data-quick-edit-chip="references"]')!.textContent).toBe('3 refs');
  expect(quickEditReferenceIds(several)).toEqual(['o1.f2', 'o1.e3', 'o1.e4']);
  // A note keeps it open when the selection goes.
  write('Round it.');
  update({ references: [] });
  expect(note()).toBe('Round it.');
  // The X clears the note and has the renderer clear what it carries; the box goes with that.
  const clear = vi.fn();
  update({ references: [face, edge], onClear: clear });
  fireEvent.click(within(box()!).getByRole('button', { name: 'Close Quick Edit' }));
  expect([note(), clear.mock.calls.length]).toEqual(['', 1]);
  update({ references: [] });
  expect(box()).toBeNull();
  // Escape keeps a note, box and all; in an empty box it is the viewer's own (whose clear closes it).
  const escape = vi.fn();
  update({ references: [face], onEscape: escape });
  write('Round it.');
  fireEvent.keyDown(field(), { key: 'Escape' });
  expect([box() !== null, escape.mock.calls.length]).toEqual([true, 0]);
  write('');
  fireEvent.keyDown(field(), { key: 'Escape' });
  expect(escape.mock.calls.length).toBe(1);
});

it('opens with an annotation while there is ink, taking the keyboard once the pen lifts, and closes when the ink goes', () => {
  const annotation = (present: boolean) => ({ present });
  const view = () => document.querySelector('[data-slot="cad-file-view"]')!;
  const { update } = mount();
  update({ annotation: annotation(false) });
  expect(box()).toBeNull();
  fireEvent.pointerDown(view());
  update({ annotation: annotation(true) });
  expect(box()!.querySelector('[data-quick-edit-ref="annotation"]')!.textContent).toBe('Annotation');
  expect(document.activeElement).not.toBe(field());
  fireEvent.pointerUp(view(), { button: 0 });
  expect(document.activeElement).toBe(field());
  update({ annotation: annotation(false) });
  expect(box()).toBeNull();
  update({ annotation: annotation(true) });
  write('A boss here.');
  update({ annotation: null });
  expect(note()).toBe('A boss here.');
});

it('offers the buttons the host can carry out, the rightmost primary and pressed by Enter', async () => {
  const send = vi.fn(async () => ({ status: 'sent' as const, partIds: [] }));
  const deliver = vi.fn(async () => ({ status: 'added' as const, partIds: [] }));
  const clear = vi.fn();
  const used = vi.fn();
  mount(testHost({ promptContext: { getSnapshot: () => composer, subscribe: () => () => {}, deliver, send }, usage: { used } }), { references: [face], onClear: clear });
  expect(buttons()).toEqual(['Close Quick Edit', 'Copy Prompt', 'Queue', 'Send']);
  expect(within(box()!).getByRole('button', { name: 'Send' }).hasAttribute('disabled')).toBe(true);
  write('Make it 2 mm thicker.');
  fireEvent.keyDown(field(), { key: 'Enter', shiftKey: true });
  expect(send).not.toHaveBeenCalled();
  fireEvent.keyDown(field(), { key: 'Enter' });
  expect(send).toHaveBeenCalledTimes(1);
  const context = (send.mock.calls[0] as unknown[])[0] as any;
  expect(context.parts.map((part: any) => [part.id, part.kind])).toEqual([['text', 'text'], ['file', 'reference'], ['reference-0', 'reference']]);
  // Gone: the note cleared, and what it carried with it -- and the host told, once, for its count.
  await waitFor(() => expect(clear).toHaveBeenCalledTimes(1));
  expect(note()).toBe('');
  expect(deliver).not.toHaveBeenCalled();
  expect(used.mock.calls).toEqual([['quickEdit']]);
});

it('copies the note with its references by their files\' absolute paths, and the files made for it — each view\'s picture, the views as a job, the annotation — by theirs, keeping it all in the box', async () => {
  let copied: Promise<string> | string = '';
  const host = testHost({ clipboard: { writeText: async text => { copied = text; }, readText: async () => '', writeImage: async () => {} } });
  const annotation = { present: true };
  const views = [{ label: 'current' }, { label: 'view 1' }];
  const prompt = {
    views: () => views,
    files: vi.fn(async () => ({ job: '/tmp/p/bracket-1.job.json', annotation: '/tmp/p/bracket-1.annotation.json',
      images: [{ label: 'current', path: '/tmp/p/bracket-1-current.png' }, { label: 'view 1', path: '/tmp/p/bracket-1-view-1.png' }] })),
    read: vi.fn(async () => new Blob()),
    annotation: () => ({ units: 'mm', parts: {}, marks: [] }),
  };
  const clear = vi.fn();
  mount(host, { references: [face], annotation, prompt, onClear: clear });
  // The annotation is a chip of its own, Annotation; without a way to wipe it, it has no X.
  expect(buttons()).toEqual(['Close Quick Edit', 'Annotation', 'Copy Prompt']);
  write('Round this edge.');
  fireEvent.click(within(box()!).getByRole('button', { name: 'Copy Prompt' }));
  // Copied, nothing has gone yet: the note and what it carries stay, and the button says so.
  await waitFor(() => expect(within(box()!).getByRole('button', { name: 'Prompt copied' })).toBeTruthy());
  expect([note(), clear.mock.calls.length]).toEqual(['Round this edge.', 0]);
  expect(await copied).toBe('Round this edge.\n\nFile: /models/parts/bracket.step\nReferences:\n/models/parts/bracket.step#o1.f2'
    + '\nCurrent view: /tmp/p/bracket-1-current.png\nView 1: /tmp/p/bracket-1-view-1.png'
    + '\nViews (cadgen snapshot --job): /tmp/p/bracket-1.job.json\nAnnotation: /tmp/p/bracket-1.annotation.json');
  expect(prompt.files).toHaveBeenCalledWith(views);
  // Copied as text, no file's bytes were read.
  expect(prompt.read).not.toHaveBeenCalled();
  // The Annotation chip copies the annotation as its annotation file's JSON, where the host keeps no files.
  fireEvent.click(within(box()!).getByRole('button', { name: 'Annotation' }));
  await waitFor(() => expect(copied).toBe(JSON.stringify(prompt.annotation(), null, 2)));
});

it('the Annotation chip copies the path of a file it saves, and that path pasted in the note is its pill', async () => {
  let copied = '';
  const host = testHost({ clipboard: { writeText: async text => { copied = text; }, readText: async () => '', writeImage: async () => {} } });
  const prompt = { views: () => [], files: vi.fn(), read: vi.fn(), annotation: () => ({ units: 'mm', parts: {}, marks: [] }),
    save: vi.fn(async () => '/tmp/p/bracket-1.annotation.json') };
  mount(host, { annotation: { present: true }, prompt });
  fireEvent.click(within(box()!).getByRole('button', { name: 'Annotation' }));
  await waitFor(() => expect(copied).toBe('/tmp/p/bracket-1.annotation.json'));
  expect(prompt.save).toHaveBeenCalledWith('annotation', prompt.annotation());
  fireEvent.paste(field(), { clipboardData: { getData: () => '/tmp/p/bracket-1.annotation.json' } });
  const pill = field().querySelector('[data-quick-edit-note-ref]')!;
  expect([pill.querySelector('svg')?.getAttribute('data-pick-glyph'), pill.textContent]).toEqual(['pencil', 'Annotation']);
  // It goes as the path it stands for.
  expect((pill as HTMLElement).dataset.quickEditNoteRef).toBe('/tmp/p/bracket-1.annotation.json');
});

it('a click on a pill in the note selects it whole, highlighted as selected text, so a Backspace takes it', () => {
  mount(testHost(), { references: [face] });
  write('Round ');
  fireEvent.paste(field(), { clipboardData: { getData: () => '/models/parts/hinge.step#o1.e4' } });
  const pill = field().querySelector('[data-quick-edit-note-ref]') as HTMLElement;
  fireEvent.mouseDown(pill.firstChild!, { button: 0 });
  const range = document.getSelection()!.getRangeAt(0);
  expect([range.startContainer, range.startOffset, range.endOffset]).toEqual([field(), 1, 2]);
  expect(pill.dataset.selected).toBe('');
  // The selection moving off it unmarks it.
  document.getSelection()!.collapse(field(), 0);
  document.dispatchEvent(new Event('selectionchange'));
  expect(pill.dataset.selected).toBeUndefined();
});

it('a pasted reference is a chip in the note, where it was pasted, and goes as the reference it stands for; the rest of a paste is text', async () => {
  let copied: Promise<string> | string = '';
  const host = testHost({ clipboard: { writeText: async text => { copied = text; }, readText: async () => '', writeImage: async () => {} } });
  mount(host, { references: [face] });
  const paste = (text: string) => fireEvent.paste(field(), { clipboardData: { getData: () => text } });
  write('Round ');
  // A line that is a reference alone, into any file (a robot's link too), is a chip; the rest stays text.
  paste('/models/parts/hinge.step#o1.e4,o1.e5\nand\n/robots/arm.urdf#upper_arm');
  const chips = [...field().querySelectorAll('[data-quick-edit-note-ref]')];
  expect(chips.map(chip => [chip.textContent, chip.getAttribute('contenteditable')]))
    .toEqual([['hinge.step · o1.e4, o1.e5', 'false'], ['arm.urdf · upper_arm', 'false']]);
  // A reference within a sentence is the sentence's.
  paste('see /models/parts/hinge.step#o1.e9');
  expect(field().querySelectorAll('[data-quick-edit-note-ref]')).toHaveLength(2);
  fireEvent.click(within(box()!).getByRole('button', { name: 'Copy Prompt' }));
  expect(await waitFor(() => copied)).toBe('Round /models/parts/hinge.step#o1.e4,o1.e5 \nand\n/robots/arm.urdf#upper_arm see /models/parts/hinge.step#o1.e9'
    + '\n\nFile: /models/parts/bracket.step\nReferences:\n/models/parts/bracket.step#o1.f2');
  // The box's X empties the note, chips and all (the selection here is fixed, so the box stays).
  fireEvent.click(within(box()!).getByRole('button', { name: 'Close Quick Edit' }));
  expect([note(), field().childNodes.length]).toEqual(['', 0]);
});

it('every chip\'s copy pasted is its pill, as its chip shows it: a measurement, the annotation, a named part', async () => {
  let copied: Promise<string> | string = '';
  const host = testHost({ clipboard: { writeText: async text => { copied = text; }, readText: async () => '', writeImage: async () => {} } });
  const nameReference = () => ({ name: 'base', kind: 'solid' });
  mount(host, { references: [face], nameReference });
  const paste = (text: string) => fireEvent.paste(field(), { clipboardData: { getData: () => text } });
  const pills = () => [...field().querySelectorAll('[data-quick-edit-note-ref]')]
    .map(pill => [pill.querySelector('svg')?.getAttribute('data-pick-glyph'), pill.textContent]);
  const mark = { start: [0, 0, 0], end: [10, 0, 0], label: '10.00 mm', color: '#d59090' };
  const annotation = { units: 'mm', parts: {}, marks: [{ id: 'a', kind: 'stroke' }] };
  paste(JSON.stringify(mark));
  paste(JSON.stringify(annotation, null, 2));
  paste('/models/parts/bracket.step#o1.2');
  expect(pills()).toEqual([['ruler', '10.00 mm'], ['pencil', 'Annotation'], ['solid', 'base']]);
  // Each goes as what was copied: the measurement and the annotation as their JSON.
  fireEvent.click(within(box()!).getByRole('button', { name: 'Copy Prompt' }));
  const text = await waitFor(() => copied);
  expect(text).toContain(JSON.stringify(mark));
  expect(text).toContain(JSON.stringify(annotation));
});

it('keeps the note and says why when it did not go, and counts no use', async () => {
  const deliver = vi.fn(async () => ({ status: 'failed' as const, message: 'The chat is busy.' }));
  const clear = vi.fn();
  // A host whose count fails: the edit is unhurt either way.
  const used = vi.fn(() => { throw new Error('the count'); });
  mount(testHost({ promptContext: { getSnapshot: () => composer, subscribe: () => () => {}, deliver }, usage: { used } }), { references: [face], onClear: clear });
  write('Shorter.');
  await act(async () => { fireEvent.click(within(box()!).getByRole('button', { name: 'Queue' })); });
  expect(within(box()!).getByRole('alert').textContent).toBe('The chat is busy.');
  expect([note(), clear.mock.calls.length, used.mock.calls.length]).toEqual(['Shorter.', 0, 0]);
  deliver.mockResolvedValueOnce({ status: 'added', partIds: [] } as any);
  await act(async () => { fireEvent.click(within(box()!).getByRole('button', { name: 'Queue' })); });
  await waitFor(() => expect(clear).toHaveBeenCalledTimes(1));
  expect([used.mock.calls.length, within(box() ?? document.body).queryByRole('alert')]).toEqual([1, null]);
});

it('is sized by its corner while it is open, rendering nothing for the drag, and opens at the default size again', async () => {
  let commits = 0;
  const view = (references: readonly unknown[]) => <Profiler id="quick-edit" onRender={() => { commits += 1; }}>
    <ViewerHostContext.Provider value={testHost()}><QuickEdit resource={resource} references={references} /></ViewerHostContext.Provider>
  </Profiler>;
  const { rerender } = render(view([face]));
  const size = () => [box()!.style.width, (box()!.querySelector('[data-quick-edit-note-box]') as HTMLElement).style.height];
  expect(size()).toEqual(['', '']);
  // jsdom measures the box and its note as 0, so a drag of the corner 300px left and 100px down
  // makes them that: drawn on the next frame, and nothing renders, then or when the pointer lets go.
  const corner = box()!.querySelector('[data-quick-edit-resize]')!;
  const before = commits;
  fireEvent.pointerDown(corner, { button: 0, pointerId: 1, clientX: 400, clientY: 100 });
  fireEvent.pointerMove(corner, { pointerId: 1, clientX: 100, clientY: 200 });
  await waitFor(() => expect(size()).toEqual(['300px', '100px']));
  fireEvent.pointerUp(corner, { pointerId: 1, clientX: 100, clientY: 200 });
  expect(commits).toBe(before);
  // The size holds for as long as the box is open.
  write('Wider.');
  expect(size()).toEqual(['300px', '100px']);
  // Closed, it is forgotten: the next box is 15rem wide, its note growing with what is written.
  fireEvent.click(within(box()!).getByRole('button', { name: 'Close Quick Edit' }));
  rerender(view([]));
  expect(box()).toBeNull();
  rerender(view([face]));
  expect(size()).toEqual(['', '']);
});

it('keeps its note through a reload of the view: out of sight while it loads, and back as it was', () => {
  const { update } = mount(testHost(), { references: [face] });
  write('Thicker here.');
  update({ hidden: true });
  expect(document.querySelector('[data-quick-edit]')!.className).toMatch(/\bhidden\b/);
  update({ hidden: false });
  expect(note()).toBe('Thicker here.');
});

it('docked on a phone it is a bottom sheet peeking at its header and chips, keeps the keyboard down on a pick, and opens from its write button', async () => {
  const { update } = mount(testHost(), { docked: true });
  press();
  update({ references: [face] });
  // A bottom sheet (shadcn's drawer), peeking: its note and buttons are out of reach until it opens.
  expect(document.querySelector('[data-quick-edit-sheet]')).not.toBeNull();
  // Peeking, its way in is a field-like button under the chips; the note and buttons are out of reach.
  const opener = () => within(box()!).queryByRole('button', { name: 'Describe your changes…' });
  const compose = () => box()!.querySelector('[data-quick-edit-compose]')!;
  const note = () => box()!.querySelector<HTMLElement>('[data-quick-edit-note]')!;
  expect([opener()?.getAttribute('aria-expanded'), compose().hasAttribute('inert'), document.activeElement === note()]).toEqual(['false', true, false]);
  fireEvent.click(opener()!);
  await waitFor(() => expect(document.activeElement).toBe(note()));
  expect([opener(), compose().hasAttribute('inert')]).toEqual([null, false]);
  expect(box()!.querySelector('[data-quick-edit-resize]')).toBeNull();
});
