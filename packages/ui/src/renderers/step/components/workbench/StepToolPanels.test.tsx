import React, { useState } from 'react';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ModelingTreeView from '../../../../../dist/renderers/step/components/workbench/ModelingTree.js';
import { useStepPanels } from '../../../../../dist/renderers/step/components/workbench/StepPanels.js';
import { useModelTools } from '../../../../../dist/renderers/step/components/workbench/ModelTools.js';
import { buildPositionSection } from '../../../../../dist/renderers/step/components/workbench/MotionControlsSection.js';
import { KeptPositionPanel, PositionToolIcon } from '../../../../../dist/renderers/kit/inspector/kinematicsControls.js';
import FloatingToolBar from '../../../../../dist/renderers/kit/tools/FloatingToolBar.js';
import ToolPanel from '../../../../../dist/renderers/kit/tools/ToolPanel.js';
import ToolStack from '../../../../../dist/renderers/kit/tools/ToolStack.js';
import { TOOL_PANEL_WIDTH } from '../../../../../dist/renderers/kit/tools/toolStackLayout.js';
import { ViewerMobileContext } from '../../../../../dist/file-viewer/responsive.js';

// The STEP renderer's panels in the tool stack — Select's Features (or a part file's Select card), the kept effects
// (Explode, Clip), Position — each where it is decided, under the stack that holds them. The
// viewport that picks and draws is the browser suite's (`StepRenderer.browser.test.mjs`).

// jsdom lays nothing out: the stack's column is given a height, and a panel its own style's size.
const STACK_HEIGHT = 600;
const restores: (() => void)[] = [];
function override(target: object, key: string, descriptor: PropertyDescriptor) {
  const previous = Object.getOwnPropertyDescriptor(target, key);
  Object.defineProperty(target, key, { configurable: true, ...descriptor });
  restores.push(() => previous ? Object.defineProperty(target, key, previous) : delete (target as any)[key]);
}
beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
  override(HTMLElement.prototype, 'clientHeight', { get(this: HTMLElement) { return this.hasAttribute('data-cad-tool-stack') ? STACK_HEIGHT : 0; } });
  override(Element.prototype, 'scrollIntoView', { value() {} });
  for (const name of ['setPointerCapture', 'releasePointerCapture']) override(Element.prototype, name, { value() {} });
  override(Element.prototype, 'hasPointerCapture', { value: () => false });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); while (restores.length) restores.pop()!(); });

// An assembly: a hinge of a base and an arm, and a pin beside it.
const partNode = (id: string, name: string) => ({ id, nodeType: 'part', name, leafPartIds: [id], children: [] });
const stepRoot = { id: 'document', nodeType: 'assembly', name: 'fixture', children: [
  { id: 'o1.1', nodeType: 'assembly', name: 'hinge', children: [partNode('o1.1.1', 'base'), partNode('o1.1.2', 'arm')] }, partNode('o1.2', 'pin')] };

const rows = () => [...document.querySelectorAll('[aria-label="Modeling tree"] button')].map(button => button.getAttribute('aria-label'))
  .filter(label => /^(?:Select|Expand|Collapse) /.test(label || ''));
const locks = () => [...document.querySelectorAll<HTMLElement>('[aria-label="Modeling tree"] [data-disclosure-locked]')].map(mark => mark.dataset.disclosureLocked);
// The Features panel, on screen or closed (`hidden`, still mounted).
const features = () => document.querySelector<HTMLElement>('section[data-tool-panel][aria-label="Features"]')!;
const shown = () => [...document.querySelectorAll<HTMLElement>('[data-cad-tool-stack] section[data-tool-panel]')].filter(node => !node.hidden).map(node => node.getAttribute('aria-label'));

/** The layout a host keeps, applying what the stack writes back. */
function useLayout(initial: any = { panels: {}, collapsed: {}, closed: {} }) {
  const [layout, setLayout] = useState<any>(initial);
  return [layout, (patch: any) => setLayout((current: any) => ({ ...current, ...(typeof patch === 'function' ? patch(current) : patch) }))] as const;
}
function Features({ mode = 'all', initialLayout = undefined as any, mobile = false }) {
  const [layout, change] = useLayout(initialLayout);
  const [expanded, setExpanded] = useState<string[]>([]);
  // The shell hands the stack the viewer's layout, as it hands every panel (`RendererShell.jsx`).
  return <ViewerMobileContext.Provider value={mobile}><ToolStack layout={layout} onLayoutChange={change} mobile={mobile}>
    <ModelingTreeView stepRoot={stepRoot} active mode={mode} onLoadTopology={() => {}}
      partControls={{ isAssemblyView: true, expandedTreeNodeIds: expanded,
        onToggleTreeNode: (id: string) => setExpanded(current => current.includes(id) ? current.filter(value => value !== id) : [...current, id]) }} />
  </ToolStack></ViewerMobileContext.Provider>;
}

it("each Select mode holds the tree: Parts, Faces and Edges every assembly open, locked; All the person's own tree again", () => {
  const view = render(<Features />);
  // All: the tree is the person's own — open the hinge. A part is a leaf.
  expect(rows()).toEqual(['Expand hinge', 'Select hinge', 'Select pin']);
  fireEvent.click(screen.getByRole('button', { name: 'Expand hinge' }));
  expect(rows()).toEqual(['Collapse hinge', 'Select hinge', 'Select base', 'Select arm', 'Select pin']);
  expect(locks()).toEqual([]);
  // Parts, Faces and Edges: every assembly open, and no disclosure to press.
  for (const mode of ['parts', 'faces', 'edges']) {
    view.rerender(<Features mode={mode} />);
    expect(rows()).toEqual(['Select hinge', 'Select base', 'Select arm', 'Select pin']);
    expect(locks()).toEqual(['open']);
  }
  // All: the tree the person left — the hinge open — and unlocked.
  view.rerender(<Features mode="all" />);
  expect(rows()).toEqual(['Collapse hinge', 'Select hinge', 'Select base', 'Select arm', 'Select pin']);
  expect(locks()).toEqual([]);
});

it("Features is its filter and its tree — Select's modes are the strip's — with no X, nothing to fold, and no stored choice closes it, on a desktop or a phone", async () => {
  const user = userEvent.setup();
  render(<Features initialLayout={{ panels: {}, collapsed: {}, closed: { tree: true } }} />);
  expect(shown()).toEqual(['Features']);
  const search = screen.getByRole('textbox', { name: 'Filter model' }) as HTMLInputElement;
  expect(search.getAttribute('placeholder')).toBe('Filter…');
  // Its filter row is the filter alone, with no X beside it, and no fold.
  expect([...features().querySelectorAll('[data-slot=tree-filter] button')].map(button => button.getAttribute('aria-label'))).toEqual([]);
  expect(within(features()).queryByRole('button', { name: /^(?:Close|Collapse|Expand) features$/ })).toBeNull();
  await user.click(search);
  await user.keyboard('ba');
  expect(within(features()).getByRole('status').textContent).toMatch(/match/);
  cleanup();
  render(<Features mobile />);
  expect(shown()).toEqual(['Features']);
});

it("Features opens as tall as its rows need, up to the whole column, on desktop and phone alike, and is the person's to size by its corner", () => {
  const phone = render(<Features mobile />);
  expect(features().style.maxHeight).toBe(`${STACK_HEIGHT}px`);
  phone.unmount();
  render(<Features />);
  expect([...features().querySelectorAll('[role=separator]')].map(handle => handle.getAttribute('aria-label'))).toEqual(['Resize features']);
  expect(features().style.maxHeight).toBe(`${STACK_HEIGHT}px`);
});

// What Select shows (`StepPanels.js`): an assembly's tree; a part file has no tree, its one row
// being all there would be, and nothing else — its modes are the strip's. What is picked is
// Quick Edit's, as chips (`StepReferenceSection.test.tsx`): no panel of the stack shows it.
const face = { id: 'o1.1.1.f2', normalizedSelector: 'o1.1.1.f2', selectorType: 'face', occurrenceId: 'o1.1.1', pickData: { surfaceType: 'plane', area: 20 } };
function SelectPanels({ assembly = false, references = [] as any[] }) {
  const partRoot = { id: '__step_model__', nodeType: 'part', name: 'hinge.step', leafPartIds: ['__model__'], children: [] };
  return useStepPanels({ selectActive: true, selectedEntry: { file: '/models/hinge.step' }, isAssemblyView: assembly,
    stepTreeRoot: assembly ? stepRoot : partRoot, selectedReferences: references });
}
function Panels(props: any) {
  const [layout, change] = useLayout();
  return <ToolStack layout={layout} onLayoutChange={change}><SelectPanels {...props} /></ToolStack>;
}

it("a part file shows no panel under Select, picked or not; an assembly its tree", () => {
  const view = render(<Panels />);
  expect(shown()).toEqual([]);
  // A pick changes nothing here.
  view.rerender(<Panels references={[face]} />);
  expect(shown()).toEqual([]);
  view.unmount();
  render(<Panels assembly references={[face]} />);
  expect(shown()).toEqual(['Features']);
});

// ---- the kept effects: Explode and Clip ---------------------------------------------------------

const neutral = () => ({ exploded: { enabled: false, amount: 0 }, clip: { enabled: false, axis: 'x', invert: false } });
const part = (id: string) => ({ id, bounds: { min: [0, 0, 0], max: [10, 10, 10] } });
let viewSettings = neutral();
/** Select, Measure, Explode and Clip on a strip over a stack, and the Display settings they write — as the STEP surface composes them. */
function Workbench({ extraPanels = null as React.ReactNode }) {
  const [view, setView] = useState(neutral);
  const [selectedTool, setTool] = useState('references');
  const [layout, change] = useLayout();
  viewSettings = view;
  const store = { patch: (patch: any) => setView(current => ({
    exploded: { ...current.exploded, ...patch.exploded },
    clip: { ...current.clip, ...patch.clip } })) };
  const modelTools = useModelTools({ modelKey: 'fixture', view, features: { sections: ['exploded', 'clip'] }, store,
    mesh: { parts: [part('a'), part('b')], bounds: { min: [0, 0, 0], max: [10, 10, 10] } }, disabled: false, selectedTool, onSelect: setTool });
  const own = (id: string, label: string) => ({ id, label, active: selectedTool === id, icon: <span>{label[0]}</span>, onSelect: () => setTool(id) });
  return <>
    <FloatingToolBar tools={[own('references', 'Select'), own('measure', 'Measure'), ...modelTools.tools]} />
    <ToolStack layout={layout} onLayoutChange={change}>{extraPanels}{modelTools.panels}</ToolStack>
  </>;
}
const tool = (name: string) => within(screen.getByRole('group', { name: 'Interaction tools' })).getByRole('button', { name });
const pressed = () => within(screen.getByRole('group', { name: 'Interaction tools' })).getAllByRole('button')
  .filter(button => button.getAttribute('aria-pressed') === 'true').map(button => button.getAttribute('aria-label'));
const region = (name: string) => screen.queryByRole('region', { name });
const slider = (name: string) => screen.getByRole('slider', { name });

it('Explode and Clip open neutral, stack under the strip one width, are headed by their name and amount, with an X only once kept, and a second press does nothing', async () => {
  const user = userEvent.setup({ pointerEventsCheck: 0 });
  render(<Workbench />);
  await user.click(tool('Explode'));
  expect(pressed()).toEqual(['Explode']);
  expect(screen.getByRole('group', { name: 'Interaction tools' }).querySelector('[role=separator]')).toBeNull();
  expect(slider('Explode amount').getAttribute('aria-valuenow')).toBe('0');
  expect(viewSettings.exploded.enabled).toBe(false);
  fireEvent.keyDown(slider('Explode amount'), { key: 'End' });
  expect(viewSettings.exploded).toEqual({ enabled: true, amount: 1 });
  await user.click(tool('Clip'));
  // Only the tool in hand is lit; the applied Explode is kept, its panel in the stack.
  expect(pressed()).toEqual(['Clip']);
  const clip = region('Clip controls')!;
  expect(viewSettings.clip.enabled).toBe(false);
  // The body is the axis and ONE slider: no typed value, no Flip. Changing the axis stays neutral.
  expect(within(clip).queryByLabelText('Clip amount value')).toBeNull();
  expect(within(clip).queryByRole('checkbox', { name: 'Flip' })).toBeNull();
  for (const axis of ['Y', 'Z', 'X']) {
    await user.click(within(clip).getByRole('combobox', { name: 'Clip axis' }));
    await user.click(screen.getByRole('option', { name: axis }));
    expect(within(clip).getByRole('combobox', { name: 'Clip axis' }).textContent).toBe(axis);
    expect(['aria-valuemin', 'aria-valuemax', 'aria-valuenow'].map(name => slider('Clip amount').getAttribute(name))).toEqual(['0', '100', '0']);
    expect(viewSettings.clip.enabled).toBe(false);
  }
  fireEvent.keyDown(slider('Clip amount'), { key: 'ArrowRight' });
  expect(viewSettings.clip.enabled).toBe(true);
  fireEvent.keyDown(slider('Clip amount'), { key: 'Home' });
  expect(viewSettings.clip.enabled).toBe(false);
  fireEvent.keyDown(slider('Clip amount'), { key: 'PageUp' });
  expect(viewSettings.clip.enabled).toBe(true);
  // Neither folds: a heading of its name and its amount; each a fixed panel the one width. The X is
  // there while the effect is set, in hand or kept: both are applied.
  const buttons = (label: string) => [...region(label)!.querySelector<HTMLElement>('[data-tool-panel-heading]')!.querySelectorAll('button')]
    .map(button => button.getAttribute('aria-label'));
  for (const [name, label] of [['Explode', 'Explode controls'], ['Clip', 'Clip controls']]) {
    const heading = region(label)!.querySelector<HTMLElement>('[data-tool-panel-heading]')!;
    expect(within(heading).getByRole('heading').textContent).toBe(name);
    expect(heading.textContent).toMatch(new RegExp(`^${name}\\d+%$`));
    expect([region(label)!.getAttribute('data-tool-panel'), region(label)!.style.width]).toEqual(['fixed', `${TOOL_PANEL_WIDTH}px`]);
  }
  expect([buttons('Explode controls'), buttons('Clip controls')]).toEqual([['Close explode controls'], ['Close clip controls']]);
  // A second press on the tool in hand does nothing. The panel of the tool in hand leads the stack.
  await user.click(tool('Clip'));
  expect([pressed(), shown()]).toEqual([['Clip'], ['Explode controls', 'Clip controls']]);
  const leads = () => [...document.querySelectorAll<HTMLElement>('[data-cad-tool-stack] section[data-tool-panel]')]
    .filter(panel => panel.style.order === '-1').map(panel => panel.getAttribute('aria-label'));
  expect(leads()).toEqual(['Clip controls']);
  // Select is the tool again; both applied effects keep their panels, in the order they were taken
  // up, each with its X, and neither tool lit.
  await user.click(tool('Select'));
  expect(pressed()).toEqual(['Select']);
  expect(shown()).toEqual(['Explode controls', 'Clip controls']);
  expect(leads()).toEqual([]);
  expect([buttons('Explode controls'), buttons('Clip controls')]).toEqual([['Close explode controls'], ['Close clip controls']]);
  // A press on a kept Clip takes it up again as it is, its X still there: the cut is set.
  await user.click(tool('Clip'));
  expect([pressed(), viewSettings.clip.enabled, buttons('Clip controls')]).toEqual([['Clip'], true, ['Close clip controls']]);
  // In hand, its X takes the cut away and leaves Clip the tool, its panel neutral and without an X.
  await user.click(within(region('Clip controls')!).getByRole('button', { name: 'Close clip controls' }));
  expect([pressed(), viewSettings.clip.enabled, buttons('Clip controls')]).toEqual([['Clip'], false, []]);
  fireEvent.keyDown(slider('Clip amount'), { key: 'PageUp' });
  await user.click(tool('Select'));
  // The kept Clip's X removes it; Explode keeps its amount.
  await user.click(within(region('Clip controls')!).getByRole('button', { name: 'Close clip controls' }));
  expect(region('Clip controls')).toBeNull();
  expect(viewSettings.clip.enabled).toBe(false);
  expect(slider('Explode amount').getAttribute('aria-valuenow')).toBe('100');
  await user.click(within(region('Explode controls')!).getByRole('button', { name: 'Close explode controls' }));
  expect(region('Explode controls')).toBeNull();
  expect(viewSettings.exploded.enabled).toBe(false);
  // A neutral Clip is the tool in hand, not an effect kept beside Select; reopening starts neutral.
  await user.click(tool('Clip'));
  expect(pressed()).toEqual(['Clip']);
  expect([viewSettings.clip.enabled, viewSettings.clip.invert]).toEqual([false, false]);
  await user.click(tool('Select'));
  expect(region('Clip controls')).toBeNull();
  expect(pressed()).toEqual(['Select']);
});

it('a neutral effect leaves with the next tool, an applied one stays until cleared, and a panel whose value passes through neutral under a held pointer stays until it lets go', async () => {
  const user = userEvent.setup();
  render(<Workbench />);
  await user.click(tool('Clip'));
  expect(pressed()).toEqual(['Clip']);
  await user.click(tool('Select'));
  expect(region('Clip controls')).toBeNull();
  await user.click(tool('Explode'));
  expect(viewSettings.exploded.amount).toBe(0);
  await user.click(tool('Measure'));
  expect(region('Explode controls')).toBeNull();
  // Applied, both stay under another tool.
  await user.click(tool('Explode'));
  fireEvent.keyDown(slider('Explode amount'), { key: 'End' });
  await user.click(tool('Clip'));
  fireEvent.keyDown(slider('Clip amount'), { key: 'PageUp' });
  await user.click(tool('Measure'));
  expect(shown()).toEqual(['Explode controls', 'Clip controls']);
  // Back to zero from the keyboard, the cut is gone and so is its panel.
  fireEvent.keyDown(slider('Clip amount'), { key: 'Home' });
  expect(region('Clip controls')).toBeNull();
  // A press held in a kept panel while its value passes through zero keeps the panel under the pointer...
  const heading = () => region('Explode controls')!.querySelector('[data-tool-panel-heading]')!;
  fireEvent.pointerDown(heading());
  fireEvent.keyDown(slider('Explode amount'), { key: 'Home' });
  expect(viewSettings.exploded.amount).toBe(0);
  expect(region('Explode controls')).not.toBeNull();
  fireEvent.keyDown(slider('Explode amount'), { key: 'PageUp' });
  act(() => { window.dispatchEvent(new Event('pointerup')); });
  expect(region('Explode controls')).not.toBeNull();
  // ...and let go at zero, it goes.
  fireEvent.pointerDown(heading());
  fireEvent.keyDown(slider('Explode amount'), { key: 'Home' });
  expect(region('Explode controls')).not.toBeNull();
  act(() => { window.dispatchEvent(new Event('pointerup')); });
  expect(region('Explode controls')).toBeNull();
});

it('a short viewer: the tree gives way first, a kept effect never; the column is bounded by the stack and scrolls rather than cutting a panel', () => {
  render(<Workbench extraPanels={<SelectPanels assembly references={[face]} />} />);
  fireEvent.click(tool('Explode'));
  fireEvent.keyDown(slider('Explode amount'), { key: 'PageUp' });
  fireEvent.click(tool('Clip'));
  fireEvent.keyDown(slider('Clip amount'), { key: 'PageUp' });
  expect(shown()).toEqual(['Features', 'Explode controls', 'Clip controls']);
  // How each gives way (`ToolPanel.jsx`'s fit): the tree first, the kept effects never.
  expect(Object.fromEntries([...document.querySelectorAll<HTMLElement>('[data-cad-tool-stack] section[data-tool-panel]')]
    .map(panel => [panel.getAttribute('aria-label'), panel.getAttribute('data-tool-panel')]))).toEqual({
    Features: 'tree', 'Explode controls': 'fixed', 'Clip controls': 'fixed' });
  // The panels' column is exactly the stack's height, inside the stack's own scroll region.
  const column = features().parentElement!;
  expect(column.style.maxHeight).toBe(`${STACK_HEIGHT}px`);
  expect(column.closest('[data-tool-stack-scroller]')).not.toBeNull();
});

// ---- Position ------------------------------------------------------------------------------------

const hinge = { url: '/hinge.step.json', articulation: { schemaVersion: 2,
  controls: [{ id: 'hinge', label: 'hinge', unit: 'deg', min: -180, max: 180, default: 0 }],
  joints: [], carries: {}, handles: [], poses: { open: { hinge: 90 } }, opening: { hinge: 0 } } };
/** STEP's Position panel as the tool stack draws it (`StepPanels.js`) over a pose runtime, and the strip's Position icon. */
function Position({ active = true }: { active?: boolean }) {
  const [values, setValues] = useState<Record<string, number>>({ hinge: 0 });
  const [activePose, setActivePose] = useState('');
  const runtime = { definition: hinge, parameterValues: values, activePose,
    onApplyPose: (name: string) => { setActivePose(name); setValues({ hinge: (hinge.articulation.poses as any)[name].hinge }); },
    onResetMotion: () => { setActivePose(''); setValues({ hinge: 0 }); },
    onParameterChange: (id: string, value: number) => { setActivePose(''); setValues(current => ({ ...current, [id]: value })); } };
  const position = buildPositionSection({ poseRuntime: runtime })!;
  return <>
    <button type="button" aria-label="Position"><PositionToolIcon /></button>
    <ToolPanel id="position" title={position.title} label="Position controls" fit="details" resizable collapsible={false} hidden={!active}
      closeLabel="Reset position" onClose={position.custom ? position.onReset : null}>{position.content}</ToolPanel>
    {position.custom && !active ? <KeptPositionPanel summary={position.summary} onReset={position.onReset} poseRow={position.poseRow} /> : null}
  </>;
}

it("Position is headed with, off the default pose, its X, offers Default beside a named pose, names every joint's slider, and keeps a pose off its default once put down", async () => {
  const user = userEvent.setup({ pointerEventsCheck: 0 });
  const view = render(<Position />);
  const panel = screen.getByRole('region', { name: 'Position controls' });
  const heading = panel.querySelector<HTMLElement>('[data-tool-panel-heading]')!;
  expect(within(heading).getByRole('heading').textContent).toBe('Position');
  // At the default pose, nothing to fold and no X.
  const headingButtons = () => [...panel.querySelector<HTMLElement>('[data-tool-panel-heading]')!.querySelectorAll('button')].map(button => button.getAttribute('aria-label'));
  expect(headingButtons()).toEqual([]);
  const preset = within(panel).getByRole('combobox', { name: 'Pose' });
  const value = within(panel).getByLabelText('hinge slider value') as HTMLInputElement;
  expect(preset.textContent).toBe('Default');
  expect(panel.querySelector('[data-position-header]')!.textContent).toContain('Pose');
  expect(within(panel).getAllByRole('slider').filter(thumb => !thumb.getAttribute('aria-label'))).toEqual([]);
  expect(within(panel).getByRole('slider', { name: 'hinge' })).toBeTruthy();
  await user.click(preset);
  expect(screen.getAllByRole('option').map(option => option.textContent)).toEqual(['Default', 'open']);
  await user.click(screen.getByRole('option', { name: 'open' }));
  expect([value.value, preset.textContent]).toEqual(['90.0°', 'open']);
  await user.click(preset);
  await user.click(screen.getByRole('option', { name: 'Default' }));
  expect([value.value, preset.textContent]).toEqual(['0.00°', 'Default']);
  // The strip's icon is the spline alone, posed or not. Off the default, the X comes, and puts the
  // default pose back.
  await user.clear(value);
  await user.type(value, '25{Enter}');
  expect(preset.textContent).toBe('Custom');
  expect(headingButtons()).toEqual(['Reset position']);
  await user.click(within(panel).getByRole('button', { name: 'Reset position' }));
  expect([value.value, preset.textContent, headingButtons()]).toEqual(['0.00°', 'Default', []]);
  await user.clear(value);
  await user.type(value, '25{Enter}');
  expect(screen.getByRole('button', { name: 'Position' }).querySelectorAll('svg')).toHaveLength(1);
  // Put down, the pose off its default is kept: headed "Position" with its X, and the Pose row to
  // switch between the named poses, Custom while the joints are set by hand.
  view.rerender(<Position active={false} />);
  const kept = () => screen.getByRole('region', { name: 'Position kept' });
  expect(kept().querySelector('[data-tool-panel-heading]')!.textContent).toBe('Position');
  expect(within(kept()).getByRole('combobox', { name: 'Pose' }).textContent).toBe('Custom');
  await user.click(within(kept()).getByRole('combobox', { name: 'Pose' }));
  await user.click(screen.getByRole('option', { name: 'open' }));
  expect([value.value, within(kept()).getByRole('combobox', { name: 'Pose' }).textContent]).toEqual(['90.0°', 'open']);
  // Its X puts the default pose back, and the kept panel goes.
  await user.click(within(kept()).getByRole('button', { name: 'Reset position' }));
  expect(screen.queryByRole('region', { name: 'Position kept' })).toBeNull();
  expect(value.value).toBe('0.00°');
});

