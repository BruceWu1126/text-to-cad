import React, { useState } from 'react';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import XbfPositionSection, { DEFAULT_POSE_VALUE, NO_PRESET_VALUE } from '../../../dist/renderers/xbf/XbfPositionSection.js';
import FileSidebar from '../../../dist/renderers/kit/tools/FileSidebar.js';
import { xbfPoseValues, xbfSamePose } from '@text-to-cad/core/lib/xbf/xbfKinematics.js';
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

// A served structure's kinematics (`cadgen/engine/mechanism.py`): a base, an arm on revolute `elbow`
// and a sleeve on cylindrical `slide`, each pair's frames the identity; configuration `raised`.
const I = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
const kinematics = {
  schema: 'cadgen.kinematics/1', name: 'rig', ground: 0, loop: null,
  links: [{ name: 'base', instances: [0], matrix: I }, { name: 'arm', instances: [1], matrix: I }, { name: 'sleeve', instances: [2], matrix: I }],
  pairs: [
    { label: 'elbow', type: 'revolute', links: [0, 1], frames: [I, I], limits: [[-10, 120]], units: ['deg'] },
    { label: 'slide', type: 'cylindrical', links: [0, 2], frames: [I, I], limits: [[-180, 180], [0, 50]], units: ['deg', 'mm'] },
  ],
  asBuilt: { elbow: 30, slide: [0, 10] }, configurations: { raised: { elbow: 90 } },
};

/** The renderer's Position state over the section, in the file sidebar as the shell draws it. */
function Position({ onPose }: { onPose: (values: any) => void }) {
  const [layout, setLayout] = useState<any>({ panels: {}, collapsed: {} });
  const [state, setState] = useState<{ preset: string, given: any }>({ preset: DEFAULT_POSE_VALUE, given: null });
  const values = xbfPoseValues(kinematics, state.given || (state.preset === DEFAULT_POSE_VALUE || state.preset === NO_PRESET_VALUE ? null : state.preset));
  onPose(values);
  const asBuilt = xbfPoseValues(kinematics);
  const preset = xbfSamePose(values, asBuilt) ? DEFAULT_POSE_VALUE : state.given ? NO_PRESET_VALUE : state.preset;
  return <FileSidebar layout={layout} onLayoutChange={(patch: any) => setLayout((current: any) => ({ ...current, ...patch }))}>
    <XbfPositionSection kinematics={kinematics} values={values} preset={preset} changed={preset !== DEFAULT_POSE_VALUE}
      onValues={(given: any) => setState({ preset: NO_PRESET_VALUE, given })} onPreset={(name: string) => setState({ preset: name, given: null })}
      onReset={() => setState({ preset: DEFAULT_POSE_VALUE, given: null })} />
  </FileSidebar>;
}

it('Position: a control per value of each pair within its limits, the configurations as presets, as built the default', async () => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
  const user = userEvent.setup({ pointerEventsCheck: 0 });
  const onPose = vi.fn();
  render(<Position onPose={onPose} />);
  const panel = screen.getByRole('region', { name: 'Position controls' });
  const pose = () => within(panel).getByRole('button', { name: /^Pose: / });
  const reset = () => within(panel.querySelector<HTMLElement>('[data-sidebar-head]')!).getByRole('button', { name: 'Reset position' }) as HTMLButtonElement;
  // An angle for the revolute pair, an angle and a distance for the cylindrical one, each within its limits.
  expect(within(panel).getAllByRole('slider').map(thumb => [thumb.getAttribute('aria-label'), thumb.getAttribute('aria-valuemin'),
    thumb.getAttribute('aria-valuemax'), thumb.getAttribute('aria-valuenow')]))
    .toEqual([['elbow', '-10', '120', '30'], ['slide angle', '-180', '180', '0'], ['slide position', '0', '50', '10']]);
  expect([pose().getAttribute('aria-label'), reset().disabled]).toEqual(['Pose: Default', true]);
  // A configuration chosen: its values, the rest as built.
  await user.click(pose());
  await user.click(within(document.querySelector<HTMLElement>('[role=menu][aria-label="Pose"]')!).getByRole('menuitemradio', { name: 'raised' }));
  expect([pose().getAttribute('aria-label'), onPose.mock.lastCall![0]]).toEqual(['Pose: raised', { elbow: [90], slide: [0, 10] }]);
  // A value set by hand is Custom; Reset puts the document back as built.
  const position = within(panel).getByLabelText('slide position value in mm') as HTMLInputElement;
  await user.clear(position);
  await user.type(position, '25{Enter}');
  expect([pose().getAttribute('aria-label'), onPose.mock.lastCall![0]]).toEqual(['Pose: Custom', { elbow: [90], slide: [0, 25] }]);
  await user.click(reset());
  expect([pose().getAttribute('aria-label'), onPose.mock.lastCall![0], reset().disabled]).toEqual(['Pose: Default', { elbow: [30], slide: [0, 10] }, true]);
});

it('Position says a mechanism that closes a loop has no pose, and offers no controls', () => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
  render(<XbfPositionSection kinematics={{ ...kinematics, loop: ['elbow', 'slide'] }} values={{}} preset={DEFAULT_POSE_VALUE} changed={false}
    onValues={() => {}} onPreset={() => {}} onReset={() => {}} />);
  expect(screen.getByText(/Not a tree: pairs elbow, slide close a loop/)).toBeTruthy();
  expect(screen.queryAllByRole('slider')).toEqual([]);
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
