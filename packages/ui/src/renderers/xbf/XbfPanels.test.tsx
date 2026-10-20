import React, { useState } from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import XbfTreeSection from '../../../dist/renderers/xbf/XbfTreeSection.js';
import XbfElementSection from '../../../dist/renderers/xbf/XbfElementSection.js';
import { buildXbfTree, xbfTreeAncestors } from '../../../dist/renderers/xbf/xbfTree.js';
import { useSceneSelection } from '../../../dist/renderers/kit/tools/select/useSceneSelection.js';
import { selectRenderer } from '../../../dist/file-viewer/registry.js';
import { createStepRenderer } from '../../../dist/renderers/step/index.js';
import { createXbfRenderer } from '../../../dist/renderers/xbf/index.js';

Object.assign(globalThis, { React });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

// A served structure as the engine answers it: an assembly placing a plate and, in a sub-assembly,
// two instances of one peg (the same path twice: an instance is its index, not its path).
const structure = {
  name: 'stand', version: '1-1', units: 'mm',
  prototypes: [
    { index: 0, key: 'k-plate', name: 'plate', color: null, faceNames: {}, edgeNames: {}, faceColors: {} },
    { index: 1, key: 'k-peg', name: 'peg', color: null, faceNames: {}, edgeNames: {}, faceColors: {} },
  ],
  instances: [
    { prototype: 0, path: 'stand/plate', matrix: [], color: null },
    { prototype: 1, path: 'stand/pegs/peg', matrix: [], color: null },
    { prototype: 1, path: 'stand/pegs/peg', matrix: [], color: null },
  ],
  tree: { name: 'stand', children: [
    { name: 'plate', prototype: 0, instance: 0 },
    { name: 'pegs', children: [{ name: 'left', prototype: 1, instance: 1 }, { name: 'right', prototype: 1, instance: 2 }] },
  ] },
};

it('the tree is the engine\'s: assemblies, then a row per placed part selecting its instance', () => {
  const tree = buildXbfTree(structure);
  const [root] = tree.roots;
  expect(root.kind).toBe('assembly');
  expect(root.partIds).toEqual(['i0', 'i1', 'i2']);
  const pegs = root.children[1];
  expect(pegs.children.map((node: any) => [node.label, node.detail, node.selectionId])).toEqual([['left', 'peg', 'i1'], ['right', 'peg', 'i2']]);
  expect(xbfTreeAncestors(tree, tree.byInstance.get('i2'))).toEqual(['n0', 'n0.1']);
  expect(buildXbfTree(null).roots).toEqual([]);
});

function Tree({ onHidden }: { onHidden: (ids: string[]) => void }) {
  const [hidden, setHidden] = useState<string[]>([]);
  const scene = { hasPart: () => true, setHighlight: vi.fn() };
  const selection = useSceneSelection({ scene, requestRender: () => {} });
  return <>
    <XbfTreeSection structure={structure} selection={selection} hidden={hidden} onHiddenChange={(ids: string[]) => { setHidden(ids); onHidden(ids); }} status="3 parts" />
    <output data-testid="selected">{selection.selected.join(',')}</output>
  </>;
}

it('a row selects its instance; its eye hides what it holds; its target isolates it and shows all again', () => {
  const onHidden = vi.fn();
  render(<Tree onHidden={onHidden} />);
  fireEvent.click(screen.getByRole('button', { name: 'Expand pegs' }));
  fireEvent.click(screen.getByRole('button', { name: 'Select right' }));
  expect(screen.getByTestId('selected').textContent).toBe('i2');
  fireEvent.click(screen.getByRole('button', { name: 'Hide pegs' }));
  expect(onHidden).toHaveBeenLastCalledWith(['i1', 'i2']);
  fireEvent.click(screen.getByRole('button', { name: 'Show pegs' }));
  expect(onHidden).toHaveBeenLastCalledWith([]);
  fireEvent.click(screen.getByRole('button', { name: 'Isolate plate' }));
  expect(onHidden).toHaveBeenLastCalledWith(['i1', 'i2']);
  fireEvent.click(screen.getByRole('button', { name: 'Show all, from plate' }));
  expect(onHidden).toHaveBeenLastCalledWith([]);
  expect(screen.getByText('3 parts')).toBeTruthy();
});

it('the element panel names the picked element and shows what the engine measures of it', async () => {
  const fetch = vi.fn(async () => new Response(JSON.stringify({ kind: 'face', ordinal: 4, area: 80, centroid: [-20, 0, 0], surface: 'plane',
    box: { min: [-20, -10, -2], max: [-20, 10, 2] }, check: 'valid', meshed: true })));
  vi.stubGlobal('fetch', fetch);
  const element = { id: 'i0/face/4', kind: 'face', ordinal: 4, key: 'k-plate', name: 'f:stand.make_box1.3', edgeClass: '', path: 'stand/plate' };
  render(<XbfElementSection element={element} source="http://127.0.0.1:9/api/doc/abc" version="1-1" />);
  expect(screen.getByText('f:stand.make_box1.3')).toBeTruthy();
  expect(screen.getByRole('status').textContent).toMatch(/exact shape/);
  await waitFor(() => expect(screen.getByText('80 mm²')).toBeTruthy());
  expect(fetch.mock.calls[0][0]).toBe('http://127.0.0.1:9/api/doc/abc/part/k-plate/face/4');
  expect(screen.getByText('plane')).toBeTruthy();
});

it('the element panel says, in the engine\'s words, when it cannot measure', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ error: 'no face 99' }), { status: 404 })));
  const element = { id: 'i0/face/99', kind: 'face', ordinal: 99, key: 'k-plate', name: '', edgeClass: '', path: 'stand/plate' };
  render(<XbfElementSection element={element} source="http://127.0.0.1:9/api/doc/abc" version="1-1" />);
  await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/no face 99/));
  expect(screen.getByText('(no element name)')).toBeTruthy();
});

it('an .xbf has its own renderer, prepared from its catalog entry', async () => {
  let disposed = 0;
  const client = { resolveEntry: async (path: string) => ({ file: path, kind: 'xbf' }), createRenderSession: () => ({ dispose: () => { disposed += 1; } }) } as never;
  const xbf = createXbfRenderer({ client });
  const step = createStepRenderer({ client });
  const file = (path: string) => ({ path, name: path, kind: 'file', size: 1, extension: path.split('.').at(-1), mediaType: 'cad' });
  expect(xbf.id).toBe('xbf');
  expect(selectRenderer([step, xbf], file('arm.xbf') as never)).toBe(xbf);
  expect(selectRenderer([step, xbf], file('arm.step') as never)).toBe(step);
  const prepared = await xbf.prepare({ file: file('arm.xbf'), source: {}, signal: new AbortController().signal } as never);
  prepared.dispose?.();
  expect(disposed).toBe(1);
});
