import React, { forwardRef, useImperativeHandle } from 'react';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, expect, it, vi } from 'vitest';

// The GLB renderer's CAM surfaces under the real shell, with only what loads the file and the WebGL viewport
// replaced: a job's Select panel is Job (its summary, findings, setups, tools and operations, and its Time row), its
// toolpaths are drawn again as fat lines that follow the time shown, and the tool stands on them there.
const loaded = vi.hoisted(() => ({ scene: null as any, own: null as any, animation: null as any }));
vi.mock('../../../dist/renderers/kit/shell/ShellViewport.js', () => ({
  default: forwardRef(function StandInViewport(props: any, ref) {
    useImperativeHandle(ref, () => ({ requestRender() {} }));
    return <div data-stand-in-viewport="">{typeof props.children === 'function' ? props.children({ hostRef: { current: null }, runtimeRef: { current: null }, mountRef: { current: null }, viewerReadyTick: 1, commitScene: () => true }) : props.children}</div>;
  })
}));
vi.mock('../../../dist/renderers/glb/useGlbScene.js', () => ({
  useGlbScene: () => ({ scene: loaded.scene, revision: 'one', busy: false, error: null, progress: null })
}));
// The renderer's own clip (the job) is kept to be played by hand.
vi.mock('../../../dist/renderers/glb/useGlbAnimation.js', () => ({ useGlbAnimation: (_document: any, _render: any, own: any) => { loaded.own = own; return loaded.animation; } }));
import GlbRenderer, { loadCamView } from '../../../dist/renderers/glb/GlbRenderer.js';
import { ViewerHostContext } from '../../../dist/host/context.js';
import { testHost } from '../../../dist/host/testing/host.js';
import { DRILL, FACING, STICKOUT, camNode, camScene } from './__tests__/camFile.js';

beforeAll(() => loadCamView());
beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal('matchMedia', (query: string) => ({ matches: false, media: query, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
});
afterEach(() => { loaded.own = null; loaded.animation = null; cleanup(); vi.unstubAllGlobals(); });

async function mount({ host = testHost() } = {}) {
  const root = await camScene();
  loaded.scene = { document: { scene: root }, object3D: root, bounds: { min: [0, 0, 0], max: [1, 1, 1] }, restBounds: { min: [0, 0, 0], max: [1, 1, 1] }, revision: 'one' };
  const settings = { toolStack: { panels: {}, collapsed: {} } };
  const catalog = { entries: [], error: null };
  const preferences = { getSnapshot: () => settings, subscribe: () => () => {}, update() {} };
  const client = { resources: {}, subscribe: () => () => {}, getSnapshot: () => catalog };
  const slot = document.createElement('div');
  document.body.append(slot);
  const props = { source: { id: 'one' }, file: { path: '/models/plate.cam.glb', name: 'plate.cam.glb', kind: 'file' }, document: null, navbarSlot: slot,
    onReady() {}, onOpenFile() {}, appearance: { colorScheme: 'light' }, onStateChange() {}, onNavigationActionsChange() {}, reload() {},
    data: { client, entry: { path: '/models/plate.cam.glb', hash: 'h' }, services: { preferences } } };
  const view = render(<ViewerHostContext.Provider value={host}><GlbRenderer {...(props as any)} /></ViewerHostContext.Provider>);
  return { ...view, root };
}
const job = () => screen.getByRole('region', { name: 'Job' });
const near = (values: ArrayLike<number>) => Array.from(values, (value) => +value.toFixed(6));
// The fat lines an operation's path is drawn as (the file's own LINES hidden under them).
const drawn = (root: any, index: number) => {
  const lines: any[] = [];
  root.traverse((object: any) => { if (object.name.startsWith(`cam-path:${index}:`)) lines.push(object); });
  return lines;
};

it('lists the job under Select: the summary, the findings card, the setup, the tools that cut and every operation with its time', async () => {
  await mount();
  expect(screen.getByRole('button', { name: 'Select' }).getAttribute('aria-pressed')).toBe('true');
  const panel = job();
  expect(panel.querySelector('[data-cam-cycle-time]')!.textContent).toBe('7 s');
  // Each part of the caption kept whole on its line.
  expect(panel.querySelector('[data-cam-summary-caption]')!.textContent).toBe('2\u00a0operations · 1\u00a0tool\u00a0change · grbl');
  // The shared findings card: the warning under Suggestions, the note left to the summary; with nothing to fix it starts shut.
  expect(panel.querySelector('[data-cam-group-detail]')!.textContent).toBe('1 suggestion');
  expect(within(panel).queryByText('Suggestions')).toBeNull();
  act(() => { fireEvent.click(within(panel).getByRole('button', { name: 'Expand Findings' })); });
  expect(within(panel).getByText('Suggestions')).toBeTruthy();
  expect(within(panel).getByRole('button', { name: STICKOUT.summary })).toBeTruthy();
  expect(panel.querySelector('[data-cam-row="setup:top"]')!.textContent).toContain('+Z · vise');
  expect(panel.querySelector('[data-cam-row="tool:T1"]')!.textContent).toBe('T16 mm flat endmill21,221 rpm ·2,547 mm/min');
  expect(panel.querySelector('[data-cam-row="tool:T3"]')).toBeNull();
  expect(within(panel).getByRole('button', { name: 'Isolate 1 Face' }).textContent).toBe('1 Face4 s');
  expect(within(panel).getByRole('button', { name: 'Isolate 2 Drill' }).textContent).toBe('2 Drill2 s');
});

it('draws each path as fat lines in place of the file\'s own, and an operation\'s eye hides its path; isolating one shows it alone', async () => {
  const { root } = await mount();
  expect(camNode(root, 'path:0:facing').visible).toBe(false);
  expect(drawn(root, 0).map((line) => line.name).sort()).toEqual(['cam-path:0:feed', 'cam-path:0:rapid']);
  expect(drawn(root, 0).every((line) => line.visible)).toBe(true);
  act(() => { fireEvent.click(screen.getByRole('button', { name: 'Hide 1 Face' })); });
  expect(drawn(root, 0).some((line) => line.visible)).toBe(false);
  expect(drawn(root, 1).every((line) => line.visible)).toBe(true);
  expect(job().querySelector('[data-cam-row="op:0"]')!.hasAttribute('data-cam-hidden')).toBe(true);
  act(() => { fireEvent.click(screen.getByRole('button', { name: 'Show 1 Face' })); });
  act(() => { fireEvent.click(screen.getByRole('button', { name: 'Isolate 2 Drill' })); });
  expect(drawn(root, 0).some((line) => line.visible)).toBe(false);
  expect(drawn(root, 1).every((line) => line.visible)).toBe(true);
  // Leaving the job puts the file's lines back.
  cleanup();
  expect(camNode(root, 'path:0:facing').visible).toBe(true);
  expect(drawn(root, 0)).toEqual([]);
});

it('the Time row stands the tool on its path at that moment and cuts the path there; the readout names the operation', async () => {
  const { root, container } = await mount();
  const t1 = camNode(root, 'tool:T1');
  const t2 = camNode(root, 'tool:T2');
  // It opens at the job's end: the drill back up out of its hole, the face mill put away.
  expect([t1.visible, t2.visible]).toEqual([false, true]);
  expect(near(t2.position.toArray())).toEqual(DRILL[2].to);
  const value = screen.getByRole('textbox', { name: 'Job time value' });
  act(() => { fireEvent.change(value, { target: { value: '2' } }); fireEvent.blur(value); });
  expect([t1.visible, t2.visible]).toEqual([true, false]);
  expect(near(t1.position.toArray())).toEqual([0.01, 0.001, 0]);
  // The feed under way stops at the tool; the drill has not begun.
  const feed = drawn(root, 0).find((line) => line.name.endsWith(':feed'));
  expect(feed.geometry.instanceCount).toBe(1);
  expect(near(feed.geometry.attributes.instanceEnd.data.array.slice(3, 6))).toEqual([0.01, 0.001, 0]);
  expect(drawn(root, 1).some((line) => line.visible)).toBe(false);
  expect(container.querySelector('[data-cam-readout-operation]')!.textContent).toBe('1 Face');
  expect(container.querySelector('[data-cam-readout-time]')!.textContent).toBe('2 s / 7 s');
  // A preset is an operation's start.
  act(() => { fireEvent.click(screen.getByRole('combobox', { name: 'Preset' })); });
  act(() => { fireEvent.click(screen.getByRole('option', { name: 'Start of 2 Drill' })); });
  expect(near(t2.position.toArray())).toEqual(DRILL[0].from);
  // The playbar's clip plays the job and lets go back at the Time row's moment.
  const [clip] = loaded.own;
  expect([clip.label, clip.duration]).toEqual(['Job', 7]);
  act(() => { clip.play.apply(3.5); });
  expect([t1.visible, t2.visible]).toEqual([true, false]);
  expect(near(t1.position.toArray())).toEqual([0.02, 0.001, -0.005]);
  act(() => { clip.play.release(); });
  expect(near(t2.position.toArray())).toEqual(DRILL[0].from);
  // Leaving the job puts the tools back where the file placed them.
  cleanup();
  expect(near(t1.position.toArray())).toEqual(FACING[0].from);
  expect(t1.visible).toBe(true);
});

it('with reduced motion the playbar plays the job a whole operation a second, the tool never sweeping', async () => {
  const { root } = await mount({ host: testHost({ environment: { colorScheme: 'light', reducedMotion: true } as any }) });
  const [clip] = loaded.own;
  expect(clip.duration).toBe(2);
  act(() => { clip.play.apply(0.4); });
  const t1 = camNode(root, 'tool:T1');
  // Half way through the first second the first operation is whole and its tool at its end.
  expect(near(t1.position.toArray())).toEqual(FACING[2].to);
  expect(drawn(root, 0).map((line) => line.geometry.instanceCount).sort()).toEqual([1, 2]);
  expect(drawn(root, 1).some((line) => line.visible)).toBe(false);
  act(() => { clip.play.apply(1.2); });
  expect(near(camNode(root, 'tool:T2').position.toArray())).toEqual(DRILL[2].to);
});

it('Display gates the rapids, the stock, the fixtures and the tool, after Surfaces', async () => {
  const { root } = await mount();
  act(() => { fireEvent.click(screen.getByRole('button', { name: 'Display' })); });
  const titles = ['Rapids', 'Stock', 'Fixtures', 'Tool'];
  for (const title of titles) expect(screen.getByText(title)).toBeTruthy();
  act(() => { fireEvent.click(screen.getByRole('button', { name: 'Disable Stock' })); });
  expect(camNode(root, 'stock').visible).toBe(false);
});
