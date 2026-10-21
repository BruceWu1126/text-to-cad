import React, { useState } from 'react';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { xbfDrivenPairs, xbfPoseValues, xbfSamePose } from '@text-to-cad/core/lib/xbf/xbfKinematics.js';
import FileSidebar from '../../../dist/renderers/kit/tools/FileSidebar.js';
import ToolPanel from '../../../dist/renderers/kit/tools/ToolPanel.js';
import XbfPositionSection, { DEFAULT_POSE_VALUE, NO_PRESET_VALUE } from '../../../dist/renderers/xbf/XbfPositionSection.js';
import XbfModelTree from '../../../dist/renderers/xbf/XbfModelTree.js';
import { SelectModeMenu as XbfSelectModeMenu, XBF_SELECT_DEFAULT } from '../../../dist/renderers/xbf/XbfSelectionModes.js';
import { presentXbfTree, xbfPickFrontier } from '../../../dist/renderers/xbf/xbfTree.js';
import { useXbfSelectionChips, xbfMassText, xbfMeasurementRows } from '../../../dist/renderers/xbf/xbfChips.js';
import { selectGeometryMode, treeShape } from '../../../dist/renderers/xbf/xbfSelectionFilter.js';
import { xbfModelMenu, xbfPartMenu } from '../../../dist/renderers/xbf/xbfMenus.js';
import { assemblyPartMenuEntries } from '../../../dist/renderers/xbf/xbfMenuItems.js';
import ModelingTreeView from '../../../dist/renderers/step/components/workbench/ModelingTree.js';
import { SelectModeMenu as StepSelectModeMenu, STEP_SELECT_DEFAULT } from '../../../dist/renderers/step/components/workbench/SelectionModes.js';
import { buildPositionSection } from '../../../dist/renderers/step/components/workbench/MotionControlsSection.js';
import { referenceMeasurements } from '../../../dist/renderers/step/workbench/referenceMeasurements.js';
import { selectRenderer } from '../../../dist/file-viewer/registry.js';
import { createStepRenderer } from '../../../dist/renderers/step/index.js';
import { createXbfRenderer } from '../../../dist/renderers/xbf/index.js';

// An engine document's panels are a STEP's: the same Model tree over the same assembly, the same
// selection filter, the same Position section, the same chips. Where it is feasible the two
// renderers' panels are mounted over one fixture and their controls compared, so the xbf cannot
// grow a control the STEP does not have. The viewport is the browser's.

Object.assign(globalThis, { React });
const restores: (() => void)[] = [];
function override(target: object, key: string, descriptor: PropertyDescriptor) {
  const previous = Object.getOwnPropertyDescriptor(target, key);
  Object.defineProperty(target, key, { configurable: true, ...descriptor });
  restores.push(() => previous ? Object.defineProperty(target, key, previous) : delete (target as any)[key]);
}
beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
  override(HTMLElement.prototype, 'clientHeight', { get(this: HTMLElement) { return this.hasAttribute('data-file-sidebar-room') ? 600 : 0; } });
  override(Element.prototype, 'scrollIntoView', { value() {} });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); while (restores.length) restores.pop()!(); });

// One assembly, twice: a hinge of a base and two arms of one part, and a pin beside it -- as the
// engine serves it, and as a STEP's tree holds it.
const structure = {
  name: 'fixture', version: '1-1', units: 'mm',
  prototypes: [
    { index: 0, key: 'k-base', name: 'base', color: null, faceNames: {}, edgeNames: {}, faceColors: {} },
    { index: 1, key: 'k-arm', name: 'arm', color: null, faceNames: { 4: 'f:arm.top' }, edgeNames: {}, faceColors: {} },
    { index: 2, key: 'k-pin', name: 'pin', color: null, faceNames: {}, edgeNames: {}, faceColors: {} },
  ],
  instances: [
    { prototype: 0, path: 'fixture/hinge/base', matrix: [], color: null },
    { prototype: 1, path: 'fixture/hinge/arm_1', matrix: [], color: null },
    { prototype: 1, path: 'fixture/hinge/arm_2', matrix: [], color: null },
    { prototype: 2, path: 'fixture/pin', matrix: [], color: null },
  ],
  tree: { name: 'fixture', children: [
    { name: 'hinge', children: [{ name: 'base', prototype: 0, instance: 0 }, { name: 'arm_1', prototype: 1, instance: 1 }, { name: 'arm_2', prototype: 1, instance: 2 }] },
    { name: 'pin', prototype: 2, instance: 3 },
  ] },
};
const partNode = (id: string, name: string, componentId: string) => ({ id, nodeType: 'part', name, componentId, leafPartIds: [id], children: [] });
const stepRoot = { id: 'document', nodeType: 'assembly', name: 'fixture', children: [
  { id: 'o1.1', nodeType: 'assembly', name: 'hinge', children: [partNode('o1.1.1', 'base', 'c-base'), partNode('o1.1.2', 'arm_1', 'c-arm'), partNode('o1.1.3', 'arm_2', 'c-arm')] },
  partNode('o1.2', 'pin', 'c-pin')] };

it("the tree is a STEP's: the document's wrapper is no row, repeated parts fold, a collapsed assembly is picked as a unit", () => {
  const tree = presentXbfTree(structure);
  expect(tree.roots.map((node: any) => [node.kind, node.label, node.selectionId])).toEqual([['assembly', 'hinge', 'n0.0'], ['part', 'pin', 'i3']]);
  expect(tree.roots[0].children.map((node: any) => node.label)).toEqual(['base', 'arm (2)']);
  expect(tree.roots[0].children[1].memberSelectionIds).toEqual(['i1', 'i2']);
  // A press on an arm takes the hinge while it is shut, the arm once it is open.
  expect(xbfPickFrontier(tree, 'i1', new Set())).toBe('n0.0');
  expect(xbfPickFrontier(tree, 'i1', new Set(['n0.0']))).toBe('i1');
  expect(presentXbfTree(null).roots).toEqual([]);
});

// The controls a tree draws, in order: its rows' names and their actions.
const controls = () => [...document.querySelectorAll('[aria-label="Modeling tree"] button, [aria-label="Modeling tree"] [data-disclosure-locked]')]
  .map(element => element.getAttribute('aria-label') || `locked:${(element as HTMLElement).dataset.disclosureLocked}`);
const heading = () => [...document.querySelectorAll('section[data-tool-panel][aria-label="Model tree"] input, section[data-tool-panel][aria-label="Model tree"] header button')]
  .map(element => element.getAttribute('aria-label'));

function Trees({ which, mode }: { which: 'step' | 'xbf', mode: string }) {
  const [layout, setLayout] = useState<any>({ panels: {}, collapsed: {} });
  const [expanded, setExpanded] = useState<string[]>([]);
  const toggle = (id: string) => setExpanded(current => current.includes(id) ? current.filter(value => value !== id) : [...current, id]);
  const partControls = { isAssemblyView: true, expandedTreeNodeIds: expanded, onToggleTreeNode: toggle, onFocusTreeNode: () => {}, hiddenPartIds: [] };
  return <FileSidebar layout={layout} onLayoutChange={(patch: any) => setLayout((current: any) => ({ ...current, ...patch }))}>
    {which === 'step'
      ? <ModelingTreeView stepRoot={stepRoot} active mode={mode} onLoadTopology={() => {}} partControls={partControls} />
      : <XbfModelTree tree={presentXbfTree(structure)} active mode={mode} partControls={partControls} />}
  </FileSidebar>;
}

it("parity: the Model tree's controls are a STEP's under every shape Select's filter gives it", () => {
  const seen: Record<string, string[][]> = {};
  for (const which of ['step', 'xbf'] as const) {
    const view = render(<Trees which={which} mode="all" />);
    fireEvent.click(screen.getByRole('button', { name: 'Model tree' }));
    const states = [heading(), controls()];
    fireEvent.click(screen.getByRole('button', { name: 'Expand hinge' }));
    fireEvent.click(screen.getByRole('button', { name: 'Expand arm (2)' }));
    states.push(controls());
    for (const mode of ['parts', 'faces']) { view.rerender(<Trees which={which} mode={mode} />); states.push(controls()); }
    seen[which] = states;
    cleanup();
  }
  expect(seen.xbf).toEqual(seen.step);
  expect(seen.xbf[1]).toEqual(['Expand hinge', 'Select hinge', 'Isolate hinge', 'Hide hinge', 'Select pin', 'Isolate pin', 'Hide pin']);
});

it("parity: Select's filter offers what a STEP's does, ticked alike when a file opens", async () => {
  const user = userEvent.setup({ pointerEventsCheck: 0 });
  const items: Record<string, string[]> = {};
  expect([...XBF_SELECT_DEFAULT]).toEqual([...STEP_SELECT_DEFAULT]);
  for (const [which, Menu] of [['step', StepSelectModeMenu], ['xbf', XbfSelectModeMenu]] as const) {
    render(<Menu value={XBF_SELECT_DEFAULT} onChange={() => {}} assembly posable connected={{ edgeChain: false, tangentFaces: false }} onConnectedChange={() => {}} />);
    await user.click(screen.getByRole('button', { name: /^Selection filter/ }));
    items[which] = within(document.querySelector<HTMLElement>('[role=menu]')!).getAllByRole('menuitemcheckbox')
      .map(item => `${item.textContent}:${item.getAttribute('aria-checked')}`);
    cleanup();
  }
  expect(items.xbf).toEqual(items.step);
  expect(items.xbf.map(item => item.split(':')[0])).toEqual(['Select all', 'Parts', 'Faces', 'Edges', 'Position', 'Annotations', 'Measurement', 'Group edges', 'Group faces']);
  // The geometry ticked is picked as one mode, which shapes the tree.
  expect(['all', 'parts', 'topology', 'none'].map(mode => treeShape(mode))).toEqual(['all', 'parts', 'faces', 'all']);
  expect(selectGeometryMode(['faces', 'edges'])).toBe('topology');
});

// A STEP's Position over a hinge's articulation, and an engine document's over a mechanism of the
// same hinge: one revolute pair with one named configuration.
const I = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
const hingeKinematics = {
  schema: 'cadgen.kinematics/1', name: 'rig', ground: 0, loop: null,
  links: [{ name: 'base', instances: [0], matrix: I }, { name: 'arm', instances: [1], matrix: I }],
  pairs: [{ label: 'hinge', type: 'revolute', links: [0, 1], frames: [I, I], limits: [[-180, 180]], units: ['deg'] }],
  asBuilt: { hinge: 0 }, configurations: { open: { hinge: 90 } },
};
const hingeArticulation = { url: '/hinge.step.json', articulation: { schemaVersion: 2,
  controls: [{ id: 'hinge', label: 'hinge', unit: 'deg', min: -180, max: 180, default: 0 }],
  joints: [], handles: [], carries: {}, poses: { open: { hinge: 90 } }, opening: { hinge: 0 } } };

function XbfPosition({ kinematics = hingeKinematics, onPose = () => {} }: { kinematics?: any, onPose?: (values: any) => void }) {
  const [layout, setLayout] = useState<any>({ panels: {}, collapsed: {} });
  const [state, setState] = useState<{ preset: string, given: any }>({ preset: DEFAULT_POSE_VALUE, given: null });
  const values = xbfPoseValues(kinematics, state.given || (state.preset === DEFAULT_POSE_VALUE || state.preset === NO_PRESET_VALUE ? null : state.preset));
  onPose(values);
  const preset = xbfSamePose(values, xbfPoseValues(kinematics)) ? DEFAULT_POSE_VALUE : state.given ? NO_PRESET_VALUE : state.preset;
  return <FileSidebar layout={layout} onLayoutChange={(patch: any) => setLayout((current: any) => ({ ...current, ...patch }))}>
    <XbfPositionSection kinematics={kinematics} values={values} preset={preset} changed={preset !== DEFAULT_POSE_VALUE}
      onValues={(given: any) => setState({ preset: NO_PRESET_VALUE, given: Object.fromEntries(Object.entries(given).filter(([label]) => !xbfDrivenPairs(kinematics).has(label))) })}
      onPreset={(name: string) => setState({ preset: name, given: null })}
      onReset={() => setState({ preset: DEFAULT_POSE_VALUE, given: null })} />
  </FileSidebar>;
}
function StepPosition() {
  const [layout, setLayout] = useState<any>({ panels: {}, collapsed: {} });
  const [values, setValues] = useState<Record<string, number>>({ hinge: 0 });
  const [activePose, setActivePose] = useState('');
  const runtime = { definition: hingeArticulation, parameterValues: values, activePose,
    onApplyPose: (name: string) => { setActivePose(name); setValues({ hinge: 90 }); },
    onResetMotion: () => { setActivePose(''); setValues({ hinge: 0 }); },
    onParameterChange: (id: string, value: number) => { setActivePose(''); setValues(current => ({ ...current, [id]: value })); } };
  const position = buildPositionSection({ poseRuntime: runtime })!;
  return <FileSidebar layout={layout} onLayoutChange={(patch: any) => setLayout((current: any) => ({ ...current, ...patch }))}>
    <ToolPanel id="position" title={position.title} label="Position controls" fit="details">{position.content}</ToolPanel>
  </FileSidebar>;
}

// What the Position section shows, in order: its heading's controls, then its body's.
function positionControls() {
  const panel = screen.getByRole('region', { name: 'Position controls' });
  const head = panel.querySelector<HTMLElement>('[data-sidebar-head]')!;
  return {
    head: [within(head).getByRole('heading').textContent, ...[...head.querySelectorAll('button')].map(button => `${button.getAttribute('aria-label')}:${(button as HTMLButtonElement).disabled}`)],
    body: [...panel.querySelectorAll('[data-tool-panel-body] button, [data-tool-panel-body] input, [data-tool-panel-body] [role=slider]')]
      .map(element => `${element.getAttribute('role') || element.tagName.toLowerCase()}:${element.getAttribute('aria-label')}:${(element as HTMLInputElement).value ?? ''}`)
  };
}

it("parity: Position is a STEP's -- its heading, its Pose row, a named slider and value per joint -- through a configuration and back", async () => {
  const user = userEvent.setup({ pointerEventsCheck: 0 });
  const seen: Record<string, any[]> = {};
  for (const [which, Section] of [['step', StepPosition], ['xbf', XbfPosition]] as const) {
    render(<Section />);
    fireEvent.click(screen.getByRole('button', { name: 'Position' }));
    const states = [positionControls()];
    const panel = screen.getByRole('region', { name: 'Position controls' });
    await user.click(within(panel).getByRole('button', { name: /^Pose: / }));
    const menu = document.querySelector<HTMLElement>('[role=menu][aria-label="Pose"]')!;
    states.push(within(menu).getAllByRole('menuitemradio').map(item => `${item.textContent}:${item.getAttribute('aria-checked')}`));
    await user.click(within(menu).getByRole('menuitemradio', { name: 'open' }));
    states.push(positionControls());
    const value = within(panel).getByLabelText('hinge slider value') as HTMLInputElement;
    await user.clear(value);
    await user.type(value, '25{Enter}');
    states.push(positionControls());
    await user.click(within(panel.querySelector<HTMLElement>('[data-sidebar-head]')!).getByRole('button', { name: 'Reset position' }));
    states.push(positionControls());
    seen[which] = states;
    cleanup();
  }
  expect(seen.xbf).toEqual(seen.step);
  expect(seen.xbf[2].body).toContain('input:hinge slider value:90.0°');
});

it('Position drives a cylindrical pair by both its values and a geared pair through its gear, and says what it cannot pose', async () => {
  const user = userEvent.setup({ pointerEventsCheck: 0 });
  const onPose = vi.fn();
  const rig = {
    ...hingeKinematics,
    links: [...hingeKinematics.links, { name: 'sleeve', instances: [2], matrix: I }, { name: 'wheel', instances: [3], matrix: I }, { name: 'leg', instances: [4], matrix: I }],
    pairs: [
      ...hingeKinematics.pairs,
      { label: 'slide', type: 'cylindrical', links: [0, 2], frames: [I, I], limits: [[-180, 180], [0, 50]], units: ['deg', 'mm'] },
      { label: 'spin', type: 'revolute', links: [0, 3], frames: [I, I], limits: [[null, null]], units: ['deg'] },
      { label: 'mesh', type: 'gear', drives: ['hinge', 'spin'], ratio: 2 },
      { label: 'knee', type: 'revolute', links: [0, 4], frames: [I, I], limits: [[0, 90]], units: ['deg'], inconsistent: 'rig/leg is not where it can put it' },
    ],
    asBuilt: { hinge: 0, slide: [0, 10], spin: 0 }, configurations: {},
  };
  render(<XbfPosition kinematics={rig} onPose={onPose} />);
  fireEvent.click(screen.getByRole('button', { name: 'Position' }));
  const panel = screen.getByRole('region', { name: 'Position controls' });
  expect(within(panel).getAllByRole('slider').map(thumb => [thumb.getAttribute('aria-label'), thumb.getAttribute('aria-valuemin'), thumb.getAttribute('aria-valuemax')]))
    .toEqual([['hinge', '-180', '180'], ['slide angle', '-180', '180'], ['slide position', '0', '50'], ['spin', '-360', '360']]);
  // The geared row writes its driver.
  const spin = within(panel).getByLabelText('spin slider value') as HTMLInputElement;
  await user.clear(spin);
  await user.type(spin, '60{Enter}');
  expect(onPose.mock.lastCall![0]).toMatchObject({ hinge: [30], spin: [60] });
  // A pair the engine could not keep a value for is said, not drawn.
  expect(within(panel).getByText(/knee cannot be posed: rig\/leg is not where it can put it/)).toBeTruthy();
});

it("parity: a face's and an edge's card rows are a STEP's for the same measurements", () => {
  const cases = [
    ['face', { surface: 'cylinder', radius: 2, area: 50 }, { selectorType: 'face', pickData: { surfaceType: 'cylinder', params: { radius: 2 } } }, 50],
    ['face', { surface: 'plane', area: 12 }, { selectorType: 'face', pickData: { surfaceType: 'plane', params: {} } }, 12],
    ['edge', { curve: 'circle', radius: 3, sweepRadians: Math.PI, length: 3 * Math.PI }, { selectorType: 'edge', pickData: { curveType: 'circle', params: { radius: 3, sweepRadians: Math.PI }, length: 3 * Math.PI } }, null],
    ['edge', { curve: 'line', length: 7 }, { selectorType: 'edge', pickData: { curveType: 'line', params: {}, length: 7 } }, null],
  ] as const;
  for (const [kind, facts, reference, area] of cases) {
    expect(xbfMeasurementRows(kind, facts)).toEqual(referenceMeasurements(reference, { faceArea: area === null ? null : { state: 'ready', area } }).rows);
  }
});

function Chips({ ids }: { ids: string[] }) {
  const tree = presentXbfTree(structure);
  const scene = {
    structure,
    describePart: (id: string) => ({ name: id }),
    describeElement: (id: string) => {
      const [, instance, kind, ordinal] = /^i(\d+)\/(face|edge)\/(\d+)$/.exec(id) || [];
      return instance ? { id, kind, ordinal: Number(ordinal), instance: Number(instance), key: 'k', name: kind === 'face' && ordinal === '4' ? 'f:arm.top' : '', path: structure.instances[Number(instance)].path } : null;
    },
    measureParts: () => ({ size: [10, 20, 30], volume: 600 }),
  };
  const { chips } = useXbfSelectionChips({ scene, tree, resource: { kind: 'workspace-file', path: '/models/fixture.xbf' }, selectedIds: ids,
    facts: new Map([['i1/face/4', { surface: 'plane', area: 12 }]]), onRemove: () => {}, onHover: () => {} });
  return <ul>{chips.map((chip: any) => <li key={chip.key}>{`${chip.label}|${chip.glyph}|${JSON.stringify(chip.compact || null)}`}</li>)}</ul>;
}

it("Quick Edit's chips name a selection as a STEP's do: a part or subassembly its own chip, faces and edges a chip per kind per part", () => {
  render(<Chips ids={['n0.0', 'i1/face/4', 'i1/face/5', 'i3/edge/2']} />);
  expect(screen.getAllByRole('listitem').map(item => item.textContent)).toEqual([
    'hinge|solid|null',
    'arm_1 · 2 faces|face|{"name":"arm_1","kind":"face","count":2}',
    'pin · edge 2|edge|{"name":"pin","kind":"edge","number":"2"}',
  ]);
});

function Card({ id, measured }: { id: string, measured: object }) {
  const tree = presentXbfTree(structure);
  const scene = { structure, describePart: (part: string) => ({ name: part }), describeElement: () => null, measureParts: () => measured };
  const { chips } = useXbfSelectionChips({ scene, tree, resource: null, selectedIds: [id], facts: new Map(), onRemove: () => {}, onHover: () => {} });
  return <div>{chips[0].card}</div>;
}

it("a part's card weighs it by its material, and an assembly's sums its parts", () => {
  expect(xbfMassText({ volume: 1000, mass: 1000e-9 * 7870, unweighed: 0 })).toBe('0.00787 kg');
  expect(xbfMassText({ volume: 1000, mass: 12.3456, unweighed: 0 })).toBe('12.35 kg');
  expect(xbfMassText({ volume: 1000, mass: null, unweighed: 1 })).toBe('no material');
  render(<Card id="i3" measured={{ size: [10, 20, 30], volume: 600, mass: null, unweighed: 1 }} />);
  expect(screen.getByText('Mass')).toBeTruthy();
  expect(screen.getByText('no material')).toBeTruthy();
  cleanup();
  render(<Card id="n0.0" measured={{ size: [10, 20, 30], volume: 6000, mass: 1.234, unweighed: 1 }} />);
  expect(screen.getByText('1.234 kg · 1 without material')).toBeTruthy();
});

it("the menus are a STEP's: a part's offers Copy Reference, Select, Isolate, Hide others, Hide, the tree's expansion and the framing", () => {
  const tree = presentXbfTree(structure);
  const part = xbfPartMenu({ tree, nodeId: 'i3', assembly: true, hiddenIds: [], isolatedIds: [], selectedNodeIds: ['i3'], expandedIds: [],
    copyText: '/models/fixture.xbf#pin', zoomSelectionAvailable: true });
  expect(assemblyPartMenuEntries(part).map((entry: any) => `${entry.separatorBefore ? '| ' : ''}${entry.label.props.children}${entry.disabled ? ' (off)' : ''}`)).toEqual([
    'Copy Reference', '| Deselect', 'Isolate', '| Hide others', 'Hide', '| Expand', 'Collapse (off)', 'Expand all', 'Collapse all (off)', '| Zoom to fit', 'Zoom to selection']);
  const model = xbfModelMenu({ tree, assembly: true, expandedIds: [], hiddenCount: 1, zoomSelectionAvailable: false });
  expect([model.showShowAll, model.showExpandCollapse, model.copyText]).toEqual([true, true, '']);
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
