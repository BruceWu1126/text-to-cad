import React, { forwardRef, useEffect, useImperativeHandle } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

// The shell's chrome — the strip, the tool stack, the file sidebar, the view's actions, Display's dropdown and preview —
// under the smallest renderer that mounts it (`renderers/shell-harness`), with the WebGL viewport
// replaced by an empty box: nothing asserted here is drawn by it. What the viewport is handed is
// recorded, so a test can read what the shell asked of it, and a view update it is handed is
// acknowledged at once, as the real one does once it holds the frame.
const viewportProps: { current: any } = { current: null };
// What the viewport's handle answers, where a test gives it one (the Studio's picture, `pictureProgress`).
const viewportHandle: { current: any } = { current: null };
vi.mock('../../../../dist/renderers/kit/shell/ShellViewport.js', () => ({
  default: forwardRef(function MockViewport(props: any, ref) {
    viewportProps.current = props;
    useImperativeHandle(ref, () => viewportHandle.current, []);
    const update = props.viewUpdate;
    useEffect(() => { if (update?.revision) update.binding.complete(update.revision); }, [update?.revision, update?.binding]);
    return <div data-mock-viewport=""><canvas />{typeof props.children === 'function'
      ? props.children({ runtimeRef: { current: null }, hostRef: { current: null }, mountRef: { current: null }, viewerReadyTick: 0, commitScene: () => true })
      : null}</div>;
  }),
}));
import HarnessRenderer from '../../../../dist/renderers/shell-harness/HarnessRenderer.js';
import { createHarnessRenderer } from '../../../../dist/renderers/shell-harness/index.js';
import { FileViewer } from '../../../../dist/file-viewer/index.js';
import { viewerLinks } from '../../../../dist/file-viewer/navigation/links.js';
import { ViewerElementContext, ViewerHostContext } from '../../../../dist/host/context.js';
import { testHost } from '../../../../dist/host/testing/host.js';
import { ViewerMobileContext } from '../../../../dist/file-viewer/responsive.js';
import { TOOL_PANEL_WIDTH } from '../../../../dist/renderers/kit/tools/toolStackLayout.js';
import { FILE_SIDEBAR_SURFACE_CLASS, FLOATING_CHROME_SURFACE_CLASS, FLOATING_SURFACE_CLASS } from '../../../../dist/lib/floatingSurface.js';
import { SolidModeIcon } from '../../../../dist/renderers/kit/view-settings/DisplayModeOptions.js';

// jsdom lays nothing out, so the few sizes the chrome reads are given: a viewer 1280 × 800 whose
// stack column (under the strip) and the sidebar panel's room (under its tiles) are each 600px
// tall, and each panel and the sidebar's as big as its own style says.
const VIEWER = { width: 1280, height: 800 }, STACK_HEIGHT = 600;
const restores: (() => void)[] = [];
function override(target: object, key: string, descriptor: PropertyDescriptor) {
  const previous = Object.getOwnPropertyDescriptor(target, key);
  Object.defineProperty(target, key, { configurable: true, ...descriptor });
  restores.push(() => previous ? Object.defineProperty(target, key, previous) : delete (target as any)[key]);
}
beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal('matchMedia', (query: string) => ({ matches: false, media: query, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
  override(HTMLElement.prototype, 'clientHeight', { get(this: HTMLElement) { return this.hasAttribute('data-cad-tool-stack') || this.matches('[data-file-sidebar-room] > :not([data-file-sidebar-tools])') ? STACK_HEIGHT : 0; } });
  const rect = Element.prototype.getBoundingClientRect;
  override(Element.prototype, 'getBoundingClientRect', { value(this: HTMLElement) {
    if (this.hasAttribute('data-cad-scene-backdrop')) return DOMRect.fromRect({ x: 0, y: 0, ...VIEWER });
    if (this.matches('section[data-tool-panel], aside[data-cad-file-sidebar]')) return DOMRect.fromRect({ x: 8, y: 48, width: parseFloat(this.style.width) || 0, height: parseFloat(this.style.maxHeight) || 100 });
    return rect.call(this);
  } });
  for (const name of ['setPointerCapture', 'releasePointerCapture']) override(Element.prototype, name, { value() {} });
  override(Element.prototype, 'hasPointerCapture', { value: () => true });
  override(Element.prototype, 'scrollIntoView', { value() {} });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); while (restores.length) restores.pop()!(); viewportProps.current = null; viewportHandle.current = null; });

/** The tab's settings as a host hands them over, counting every write. */
function tabSettings(initial: object = {}) {
  let settings: any = { toolStack: { panels: {}, collapsed: {} }, ...initial };
  const listeners = new Set<() => void>();
  const store = { writes: 0, getSnapshot: () => settings, subscribe: (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    update(patch: object) { store.writes += 1; settings = { ...settings, ...patch }; listeners.forEach(listener => listener()); } };
  return store;
}
type Settings = ReturnType<typeof tabSettings>;
function frame({ name = 'panel.harness', preferences = tabSettings(), state = undefined as unknown, mobile = false, onStateChange = (_: unknown) => {}, appearance = { colorScheme: 'light' } as object, notice = null as React.ReactNode, viewer = false } = {}) {
  const props = { source: { id: 'one' }, file: { path: `/models/${name}`, name, kind: 'file' }, document: null, notice,
    onReady() {}, onOpenFile() {}, appearance, state, onStateChange, reload() {}, data: { services: { preferences } } };
  // `viewer`: inside a viewer's element, as FileViewer draws it, where the viewer's keys are its own.
  const element = () => {
    const shell = <ViewerHostContext.Provider value={testHost()}><ViewerMobileContext.Provider value={mobile}>
      <HarnessRenderer {...(props as any)} /></ViewerMobileContext.Provider></ViewerHostContext.Provider>;
    if (!viewer) return shell;
    const ref = { current: null as HTMLDivElement | null };
    return <ViewerElementContext.Provider value={ref}><div ref={node => { ref.current = node; }}>{shell}</div></ViewerElementContext.Provider>;
  };
  const view = render(element());
  return { ...view, preferences, remount: () => { view.unmount(); return render(element()); } };
}
const panel = (label: string) => document.querySelector<HTMLElement>(`section[data-tool-panel][aria-label="${label}"]`);
const shown = () => [...document.querySelectorAll<HTMLElement>('[data-cad-tool-stack] section[data-tool-panel]')].filter(node => !node.hidden).map(node => node.getAttribute('aria-label'));
// The file sidebar's sections, mounted, top to bottom; its panel; and its tiles, each a section's.
const sections = () => [...document.querySelectorAll<HTMLElement>('[data-cad-file-sidebar] section[data-tool-panel]')].filter(node => !node.closest('[hidden]')).map(node => node.getAttribute('aria-label'));
const bar = () => document.querySelector<HTMLElement>('aside[data-cad-file-sidebar]')!;
const isOpen = () => bar().hasAttribute('data-open');
const tiles = () => within(screen.getByRole('group', { name: 'File sidebar' })).getAllByRole('button');
const tileNames = () => tiles().map(button => button.getAttribute('aria-label'));
const pressedTiles = () => tiles().filter(button => button.getAttribute('aria-pressed') === 'true').map(button => button.getAttribute('aria-label'));
const tile = (name: string) => within(screen.getByRole('group', { name: 'File sidebar' })).getByRole('button', { name, exact: true });
const handles = (label: string) => [...panel(label)!.querySelectorAll('[role=separator]')].map(handle => handle.getAttribute('aria-label'));
const width = (label: string) => parseFloat(panel(label)!.style.width);
const layout = (preferences: Settings) => preferences.getSnapshot().toolStack;
const tool = (name: string) => within(screen.getByRole('group', { name: 'Interaction tools' })).getByRole('button', { name });
function drag(handle: Element, [dx, dy]: [number, number], { release = true } = {}) {
  fireEvent.pointerDown(handle, { button: 0, pointerId: 1, clientX: 100, clientY: 100 });
  fireEvent.pointerMove(handle, { pointerId: 1, clientX: 100 + dx, clientY: 100 + dy });
  if (release) fireEvent.pointerUp(handle, { pointerId: 1, clientX: 100 + dx, clientY: 100 + dy });
}

it("the strip is at the top centre with the stack under it, the file sidebar's tiles at the top-left: no section open until a tile is pressed, its panel a tool panel's width, capped at the room under the tiles, and nothing is stored until a person sizes it", () => {
  const { preferences } = frame();
  // The strip and the stack under it, 8px down from the viewer's top, centred, stopping 8px above
  // the cube's line (the cube itself 8px off the bottom); the sidebar's room 8px in from the top
  // and left, down the viewer's full height to 8px off its bottom.
  expect(document.querySelector<HTMLElement>('[data-cad-tool-groups]')!.style).toMatchObject({ top: '8px', left: '8px', right: '8px', bottom: 'calc(16px + 6rem)' });
  expect(document.querySelector<HTMLElement>('[data-file-sidebar-room]')!.style).toMatchObject({ top: 'var(--cad-sidebar-top, 8px)', left: '8px', bottom: '8px' });
  // A tile per section; none open as the file opens, the panel away and out of reach.
  expect([tileNames(), sections(), shown()]).toEqual([['Harness tree', 'Reference'], ['Harness tree', 'Harness reference'], []]);
  expect([isOpen(), bar().hasAttribute('inert')]).toEqual([false, true]);
  fireEvent.click(tile('Harness tree'));
  expect([isOpen(), bar().hasAttribute('inert'), pressedTiles()]).toEqual([true, false, ['Harness tree']]);
  expect(TOOL_PANEL_WIDTH).toBe(164);
  expect([bar().style.width, bar().style.maxHeight]).toEqual([`${TOOL_PANEL_WIDTH}px`, `${STACK_HEIGHT}px`]);
  // A section is the panel's width and its content's height: no width, cap, scroller or grip of its own.
  for (const label of ['Harness tree', 'Harness reference']) {
    expect([panel(label)!.style.width, panel(label)!.style.maxHeight], label).toEqual(['', '']);
    expect(panel(label)!.querySelector('[data-radix-scroll-area-viewport]'), label).toBeNull();
    expect(handles(label), label).toEqual([]);
  }
  // The panel scrolls, as one, and is the person's to size by one grip in its bottom-right corner
  // (Quick Edit's, drawn inside it).
  expect(bar().querySelector('[data-file-sidebar-scroller]')).not.toBeNull();
  const grip = screen.getByRole('separator', { name: 'Resize file sidebar' });
  expect([grip.getAttribute('data-resize-grip'), grip.parentElement]).toEqual(['bottom-right', bar()]);
  expect(grip.className).toMatch(/\babsolute bottom-0\b.*\bright-0 cursor-nwse-resize\b/);
  // A kept effect is the stack's: fixed, at the one width, with no grip.
  fireEvent.click(tool('Keep'));
  expect(shown()).toEqual(['Kept controls']);
  expect([width('Kept controls'), handles('Kept controls')]).toEqual([TOOL_PANEL_WIDTH, []]);
  expect(tool('Keep').getAttribute('aria-pressed')).toBe('true');
  expect([preferences.writes, layout(preferences)]).toEqual([0, { panels: {}, collapsed: {} }]);
});

it("each section's panel is sized by its corner, on its own: width and cap in one write when the pointer lets go, the keyboard by 16px and Home and End to the bounds, and a remount opens at what was left", () => {
  const { preferences, remount } = frame();
  fireEvent.click(tile('Harness tree'));
  const corner = screen.getByRole('separator', { name: 'Resize file sidebar' });
  drag(corner, [100, -100], { release: false });
  // Mid-drag: the panel follows the corner; nothing is written yet.
  expect([bar().style.width, bar().style.maxHeight]).toEqual([`${TOOL_PANEL_WIDTH + 100}px`, `${STACK_HEIGHT - 100}px`]);
  expect(corner.hasAttribute('data-dragging')).toBe(true);
  expect(preferences.writes).toBe(0);
  fireEvent.pointerUp(corner, { pointerId: 1, clientX: 200, clientY: 0 });
  expect(preferences.writes).toBe(1);
  expect(layout(preferences).panels).toEqual({ 'sidebar:tree': { width: TOOL_PANEL_WIDTH + 100, height: STACK_HEIGHT - 100 } });
  expect([bar().style.width, bar().style.maxHeight]).toEqual([`${TOOL_PANEL_WIDTH + 100}px`, `${STACK_HEIGHT - 100}px`]);
  // From the keyboard, one axis a key: Left/Right the width, Up/Down the cap; Home and End both, to their bounds.
  fireEvent.keyDown(corner, { key: 'ArrowLeft' });
  fireEvent.keyDown(corner, { key: 'ArrowDown' });
  expect(layout(preferences).panels['sidebar:tree']).toEqual({ width: TOOL_PANEL_WIDTH + 84, height: STACK_HEIGHT - 84 });
  fireEvent.keyDown(corner, { key: 'Home' });
  // Narrower than a tool panel, down to room for a heading's name and its buttons.
  expect(layout(preferences).panels['sidebar:tree']).toEqual({ width: 120, height: 64 });
  fireEvent.keyDown(corner, { key: 'End' });
  expect(layout(preferences).panels['sidebar:tree']).toEqual({ width: VIEWER.width / 2, height: STACK_HEIGHT });
  // Another section's panel has a size of its own: the default, until it is sized.
  fireEvent.click(tile('Reference'));
  expect([bar().style.width, bar().style.maxHeight]).toEqual([`${TOOL_PANEL_WIDTH}px`, `${STACK_HEIGHT}px`]);
  remount();
  fireEvent.click(tile('Harness tree'));
  expect([bar().style.width, bar().style.maxHeight]).toEqual([`${VIEWER.width / 2}px`, `${STACK_HEIGHT}px`]);
  // Reset puts it back.
  act(() => preferences.update({ toolStack: { panels: {}, collapsed: {} } }));
  expect([bar().style.width, bar().style.maxHeight]).toEqual([`${TOOL_PANEL_WIDTH}px`, `${STACK_HEIGHT}px`]);
});

it("the sidebar's tiles are there whatever the tool, a tool's own section coming and going with it; one section is open at a time, another tile taking its place, and its X or a second press of its tile closes it, its content kept", () => {
  const { preferences } = frame();
  expect(tool('Select').getAttribute('aria-pressed')).toBe('true');
  fireEvent.click(tool('Annotate'));
  expect(tileNames()).toEqual(['Harness tree', 'Reference']);
  fireEvent.click(tool('Pose'));
  expect(tileNames()).toEqual(['Harness tree', 'Reference', 'Position']);
  const closed = () => ['Harness tree', 'Harness reference', 'Harness position'].filter(label => panel(label)!.hasAttribute('data-sidebar-closed'));
  fireEvent.click(tile('Reference'));
  expect([pressedTiles(), closed()]).toEqual([['Reference'], ['Harness tree', 'Harness position']]);
  // Its head is its name and its X: nothing folds.
  const head = panel('Harness reference')!.querySelector<HTMLElement>('[data-sidebar-head]')!;
  expect([within(head).getByRole('heading').textContent, within(head).getAllByRole('button').map(button => button.getAttribute('aria-label'))])
    .toEqual(['Reference', ['Close reference']]);
  expect(panel('Harness reference')!.querySelector('[aria-expanded]')).toBeNull();
  fireEvent.click(tile('Position'));
  expect([pressedTiles(), closed()]).toEqual([['Position'], ['Harness tree', 'Harness reference']]);
  // A closed section stays mounted: the tree's search is as it was left.
  fireEvent.click(tile('Harness tree'));
  fireEvent.change(screen.getByRole('textbox', { name: 'Filter tree' }), { target: { value: 'Row 1' } });
  fireEvent.click(tile('Reference'));
  fireEvent.click(tile('Harness tree'));
  expect((screen.getByRole('textbox', { name: 'Filter tree' }) as HTMLInputElement).value).toBe('Row 1');
  // A second press of its tile closes it, and so does its X: the panel slides away.
  fireEvent.click(tile('Harness tree'));
  expect([pressedTiles(), isOpen()]).toEqual([[], false]);
  fireEvent.click(tile('Reference'));
  fireEvent.click(within(panel('Harness reference')!).getByRole('button', { name: 'Close reference' }));
  expect([pressedTiles(), isOpen()]).toEqual([[], false]);
  // Pose put down: its section, and its tile, go with it.
  fireEvent.click(tool('Pose'));
  expect([tileNames(), preferences.writes]).toEqual([['Harness tree', 'Reference'], 0]);
});

it("on a phone the sidebar's tiles lead the strip, ruled off from its tools, and open their panel under it at a phone's width with no grip; Annotate's controls are its chevron's menu", () => {
  frame({ mobile: true });
  const strip = screen.getByRole('group', { name: 'Interaction tools' });
  // No toolbar of the sidebar's own: its tiles are the strip's first group.
  expect(document.querySelectorAll('[data-file-sidebar-tools]')).toHaveLength(1);
  const group = within(strip).getByRole('group', { name: 'File sidebar' });
  expect([within(group).getAllByRole('button').map(button => button.getAttribute('aria-label')), group.nextElementSibling!.hasAttribute('data-strip-rule')])
    .toEqual([['Harness tree', 'Reference'], true]);
  // Its room hangs under the strip, at the left, down to a little past the view's middle.
  expect(document.querySelector<HTMLElement>('[data-file-sidebar-room]')!.style).toMatchObject({ left: '8px', bottom: '40%' });
  fireEvent.click(within(group).getByRole('button', { name: 'Reference' }));
  expect([isOpen(), within(group).getByRole('button', { name: 'Reference' }).getAttribute('aria-pressed'), bar().style.width, bar().querySelector('[data-file-sidebar-corner]')])
    .toEqual([true, 'true', '256px', null]);
  fireEvent.click(tool('Annotate'));
  expect(tool('Annotate').getAttribute('aria-pressed')).toBe('true');
  expect([screen.queryByRole('toolbar', { name: 'Annotation controls' }), Boolean(screen.getByRole('button', { name: 'Annotation settings' }))]).toEqual([null, true]);
});

const displayPopover = () => document.querySelector<HTMLElement>('[data-display-settings]');
// Display: the current mode button and its settings in the shared popover.
const displayButton = (root: HTMLElement = document.body) => within(root).getByRole('button', { name: /^Display mode: / });
const displayLit = (root?: HTMLElement) => displayButton(root).getAttribute('aria-expanded') === 'true';
const displayMenu = () => document.querySelector<HTMLElement>('[role=dialog][aria-label="Display mode"]');
async function openDisplay(user: ReturnType<typeof userEvent.setup>, root?: HTMLElement) {
  await user.click(displayButton(root));
  await user.click(within(displayMenu()!).getByRole("button", { name: /settings$/ }));
  await waitFor(() => expect(displayPopover()).not.toBeNull());
}
// A control by its name, whatever it says it is set to ("Projection: Orthographic" is Projection).
const named = (button: Element) => button.getAttribute('aria-label')!.split(':')[0];
// The view's controls: the strip's last, after its tools and a rule.
const barButtons = () => [...document.querySelector('[role=group][aria-label="Interaction tools"] [data-view-controls]')?.querySelectorAll('button') ?? []].map(named);
// The Studio's own, in the view's top-right corner.
const cornerButtons = () => [...document.querySelector('[data-preview-corner]')?.querySelectorAll('button') ?? []].map(named);
// The strip's tools, before the view's controls.
const strip = () => [...screen.getByRole('group', { name: 'Interaction tools' }).querySelectorAll('button')].filter(button => !button.closest('[data-view-controls]')).map(button => button.getAttribute('aria-label'));

it('while the model loads the viewer shows none of its own chrome, and all of it returns with the model', () => {
  frame();
  const stage = (name: string) => act(() => { fireEvent.click(document.querySelector(`[data-harness-stage="${name}"]`)!); });
  expect(screen.getByRole('group', { name: 'Interaction tools' })).toBeTruthy();
  expect(barButtons()).toEqual(['Display mode', 'Studio']);
  stage('finding');
  expect(screen.queryByRole('group', { name: 'Interaction tools' })).toBeNull();
  expect(document.querySelector('[data-cad-tool-stack]')).toBeNull();
  expect(barButtons()).toEqual([]);
  expect(viewportProps.current.isLoading).toBe(true);
  stage('idle');
  expect(screen.getByRole('group', { name: 'Interaction tools' })).toBeTruthy();
  expect(barButtons()).toEqual(['Display mode', 'Studio']);
  // With nothing to work on yet, the strip is idle together, and so are Display and the Studio.
  stage('unready');
  expect(strip()).toEqual(['Select', 'Annotate', 'Keep', 'Pose']);
  const idle = (name: string) => (name === 'Display' ? displayButton() : name === 'Studio' ? screen.getByRole('button', { name }) : tool(name)).hasAttribute('disabled');
  expect(['Select', 'Annotate', 'Display', 'Studio'].map(idle)).toEqual([true, true, true, true]);
  stage('idle');
  expect(['Select', 'Annotate', 'Display', 'Studio'].map(idle)).toEqual([false, false, false, false]);
});

it("Display and the Studio are a 3D view's, as its renderer declares: a view that does not declare it has neither, not disabled ones, and a request for the Studio leaves it as it is", () => {
  // The Studio asked for from outside the strip, as a link or a host request would.
  const ask = () => act(() => { fireEvent.click(document.querySelector('[data-harness-ask-preview]')!); });
  // A 3D view (the harness's, declared `previewable`): Display and the Studio end the strip, and a request enters it.
  frame();
  expect(barButtons()).toEqual(['Display mode', 'Studio']);
  ask();
  expect(viewportProps.current.previewMode).toBe(true);
  cleanup();
  // A view whose renderer does not declare it (`flat.harness`, a 2D view's stand-in): neither on the
  // strip, no rule, and the same request leaves the normal view on screen, its tools up.
  frame({ name: 'flat.harness' });
  expect(barButtons()).toEqual([]);
  for (const name of [/^Display mode: /, 'Studio']) expect(screen.queryByRole('button', { name })).toBeNull();
  expect(document.querySelector('[data-strip-rule]')).toBeNull();
  ask();
  expect(viewportProps.current.previewMode).toBe(false);
  expect(document.querySelector('[data-preview-chrome]')!.hasAttribute('inert')).toBe(false);
  expect(strip()).toEqual(['Annotate']);
});

it('a load the model did not survive leaves only the card saying so, and a failed update it survives keeps the chrome', () => {
  frame();
  const stage = (name: string) => act(() => { fireEvent.click(document.querySelector(`[data-harness-stage="${name}"]`)!); });
  stage('broken');
  expect(screen.getByText('Couldn’t load the harness model')).toBeTruthy();
  expect(screen.queryByRole('group', { name: 'Interaction tools' })).toBeNull();
  expect(document.querySelector('[data-cad-tool-stack]')).toBeNull();
  expect(barButtons()).toEqual([]);
  expect(document.querySelector('[data-viewport-status]')).toBeNull();
  expect(viewportProps.current.viewCube).toBe(false);
  stage('failed');
  expect(screen.getByRole('group', { name: 'Interaction tools' })).toBeTruthy();
  expect(barButtons()).toEqual(['Display mode', 'Studio']);
  expect(viewportProps.current.viewCube).toBe(true);
});

it("a dismissed alert's own icon, at the navbar's right, brings its card back; it goes with the card's return, and when the alert clears", async () => {
  // The harness under the real FileViewer, whose navbar's right holds nothing of its own, nor the
  // view's controls (they end the strip): only a dismissed alert's icon.
  const host = testHost({ links: viewerLinks({ version: '0.7.5' }), environment: { colorScheme: 'light', platform: 'darwin' },
    files: { id: 'one', stat: async (path: string) => ({ path, name: path.split('/').pop()!, kind: 'file', extension: 'harness', size: 1 }) } });
  render(<FileViewer file="/models/one.harness" host={host as any} renderers={[createHarnessRenderer({ preferences: tabSettings() })]}
    state={{}} onStateChange={() => {}} />);
  await screen.findByRole('button', { name: 'Studio' });
  const right = () => [...document.querySelector('[data-viewer-navbar] > div')!.querySelectorAll('a, button')].map(node => node.getAttribute('aria-label'));
  const card = () => screen.queryByRole('alert');
  const stage = (name: string) => act(() => { fireEvent.click(document.querySelector(`[data-harness-stage="${name}"]`)!); });
  const view: string[] = [];
  expect([right(), barButtons()]).toEqual([view, ['Display mode', 'Studio']]);
  expect(document.querySelectorAll('[data-viewer-navbar] [data-view-controls] button')).toHaveLength(0);
  stage('failed');
  expect(card()!.textContent).toContain('Harness update failed');
  expect(right()).toEqual(view, 'the card is up: no icon');
  // Put away: the card's own icon, in the error's colour, named after the alert, at the navbar's right.
  fireEvent.click(within(card()!).getByRole('button', { name: 'Dismiss' }));
  expect(card()).toBeNull();
  expect(right()).toEqual(['Harness update failed', ...view]);
  const icon = screen.getByRole('button', { name: 'Harness update failed' });
  expect(icon.querySelector('svg')!.getAttribute('class')).toMatch(/\blucide-circle-alert\b.*\btext-destructive\b/);
  // Pressed, it brings the card back and goes.
  fireEvent.click(icon);
  expect(card()!.textContent).toContain('Harness update failed');
  expect(right()).toEqual(view);
  // Put away again, and then the alert clears: no card, and no icon; raised again, the card shows.
  fireEvent.click(within(card()!).getByRole('button', { name: 'Dismiss' }));
  expect(right()[0]).toBe('Harness update failed');
  stage('idle');
  expect([card(), right()]).toEqual([null, view]);
  stage('failed');
  expect(card()).not.toBeNull();
  expect(right()).toEqual(view);
});

it("the host's notice waits for the model, then takes the top-right with Quick Edit stacked under it", () => {
  const notice = <div role="dialog" aria-label="A notice" />;
  const corner = () => [...document.querySelector('[data-viewport-top-right-content]')?.children ?? []]
    .map(child => child.hasAttribute('data-viewport-notice') ? 'notice' : child.hasAttribute('data-quick-edit') ? 'Quick Edit' : child.tagName);
  const stage = (name: string) => act(() => { fireEvent.click(document.querySelector(`[data-harness-stage="${name}"]`)!); });
  frame({ notice });
  stage('finding');
  expect(screen.queryByRole('dialog', { name: 'A notice' })).toBeNull();
  stage('broken');  // a load the model did not survive: only its card
  expect(screen.queryByRole('dialog', { name: 'A notice' })).toBeNull();
  stage('idle');
  expect(corner()).toEqual(['notice', 'Quick Edit']);
  cleanup();
  // Shown small, the view has no Quick Edit and no strip, and the notice still asks.
  frame({ notice, appearance: { colorScheme: 'light', compact: true } });
  expect(corner()).toEqual(['notice']);
});

it('a host showing the view small gets the model alone: no tools, no view actions, no Quick Edit, no cube', () => {
  frame({ appearance: { colorScheme: 'light', compact: true } });
  expect(screen.queryByRole('group', { name: 'Interaction tools' })).toBeNull();
  expect(document.querySelector('[data-cad-tool-stack]')).toBeNull();
  expect(barButtons()).toEqual([]);
  expect(document.querySelector('[data-quick-edit]')).toBeNull();
  expect(viewportProps.current.viewCube).toBe(false);
});

it("Display and the Studio end the strip; Render is no mode of Display's, and a mode's settings leave Annotate and kept tools in place", async () => {
  const user = userEvent.setup({ skipHover: true });
  const { remount } = frame();
  expect([strip(), barButtons()]).toEqual([['Select', 'Annotate', 'Keep', 'Pose'], ['Display mode', 'Studio']]);
  const solid = render(<SolidModeIcon />).container.querySelector('svg')!;
  expect(displayButton().querySelector('svg')!.innerHTML).toEqual(solid.innerHTML);
  await user.click(displayButton());
  // The modes, then the projection: Render is the Studio's alone.
  expect(within(displayMenu()!).getAllByRole('radio').map(item => [item.textContent, item.getAttribute('aria-checked')]))
    .toEqual([['Solid', 'true'], ['Orthographic', 'true'], ['Perspective', 'false']]);
  await user.click(within(displayMenu()!).getByRole('radio', { name: 'Perspective', exact: true }));
  expect(within(displayMenu()!).getByRole('radio', { name: 'Perspective', exact: true }).getAttribute('aria-checked')).toBe('true');
  await user.keyboard('{Escape}');
  await user.click(tool('Keep'));
  await user.click(tool('Annotate'));
  const stack = shown();
  const controls = screen.getByRole('toolbar', { name: 'Annotation controls' });
  await openDisplay(user);
  expect(displayPopover()!.getAttribute('aria-label')).toBe('Solid settings');
  expect([shown(), screen.getByRole('toolbar', { name: 'Annotation controls' })]).toEqual([stack, controls]);
  await user.click(within(displayPopover()!).getByRole('checkbox', { name: 'Grid', exact: true }));
  expect(displayButton().getAttribute('aria-label')).toBe('Display mode: Solid');
  await user.keyboard('{Escape}');
  expect(displayPopover()).toBeNull();
  expect(displayMenu()).toBeNull();
  expect(tool('Annotate').getAttribute('aria-pressed')).toBe('true');
  await openDisplay(user);
  await user.pointer({ keys: '[MouseLeft]', target: document.querySelector('[data-mock-viewport] canvas')! });
  await waitFor(() => expect(displayPopover()).toBeNull());
  expect(tool('Keep').getAttribute('aria-pressed')).toBe('true');
  await openDisplay(user);
  remount();
  expect([displayPopover(), displayLit()]).toEqual([null, false]);
});

// The Studio, entered as its tile enters it: the view covered first, then the Studio under the cover.
async function enterStudio(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'Studio' }));
  await screen.findByRole('button', { name: 'Exit studio' });
}
const studioSettings = () => screen.queryByRole('region', { name: 'Studio settings' });

it("three surfaces: the strip, the sidebar's tiles and the stack share the light chrome surface; the sidebar's panel and the Studio's settings, read while they are up, are between; Display's dropdown is the most opaque", async () => {
  const user = userEvent.setup();
  frame();
  expect(new Set([FLOATING_CHROME_SURFACE_CLASS, FILE_SIDEBAR_SURFACE_CLASS, FLOATING_SURFACE_CLASS]).size).toBe(3);
  const classes = (element: Element) => element.className.split(/\s+/);
  const has = (element: Element, surface: string) => surface.split(' ').every(name => classes(element).includes(name));
  expect(has(screen.getByRole('group', { name: 'Interaction tools' }), FLOATING_CHROME_SURFACE_CLASS)).toBe(true);
  expect(has(screen.getByRole('group', { name: 'File sidebar' }), FLOATING_CHROME_SURFACE_CLASS)).toBe(true);
  fireEvent.click(tool('Keep'));
  expect(has(panel('Kept controls')!, FLOATING_CHROME_SURFACE_CLASS)).toBe(true);
  // The sidebar's panel is read for as long as a section is open, and its sections have no surface of their own.
  expect(has(bar(), FILE_SIDEBAR_SURFACE_CLASS)).toBe(true);
  expect(has(panel('Harness tree')!, FLOATING_CHROME_SURFACE_CLASS)).toBe(false);
  // Display's settings are read while they are up: their text must not compete with the model.
  await openDisplay(user);
  expect(has(displayPopover()!, FLOATING_SURFACE_CLASS)).toBe(true);
  await user.keyboard('{Escape}');
  await enterStudio(user);
  await user.click(screen.getByRole('button', { name: 'Studio settings' }));
  expect(has(studioSettings()!, FILE_SIDEBAR_SURFACE_CLASS)).toBe(true);
});

it("the Studio is in Render: the strip, the view's controls and the stack step aside, the sidebar offers Position alone, bare, at the top-left; the top-right holds its settings (Orbit and Render's) and the way out, which brings back the tool in hand and the file's own mode", async () => {
  const user = userEvent.setup();
  frame();
  await user.click(tool('Pose'));
  expect(tileNames()).toEqual(['Harness tree', 'Reference', 'Position']);
  await openDisplay(user);
  await enterStudio(user);
  const chrome = document.querySelector<HTMLElement>('[data-preview-chrome]')!;
  expect([chrome.hidden, chrome.hasAttribute('inert')]).toEqual([true, true]);
  expect(chrome.contains(document.querySelector('[data-cad-tool-stack]'))).toBe(true);
  expect(displayPopover()).toBeNull();
  // The strip goes with the tools view.
  expect([barButtons(), screen.queryByRole('group', { name: 'Interaction tools' })]).toEqual([[], null]);
  // The top-left: the sidebar's Studio sections (Position, here) as tiles with no surface under them.
  expect(tileNames()).toEqual(['Position']);
  expect(screen.getByRole('group', { name: 'File sidebar' }).className).toMatch(/\bborder-transparent\b/);
  expect(chrome.contains(bar())).toBe(false);
  // The top-right: its settings and the way out, an X, 8px in.
  expect(cornerButtons()).toEqual(['Studio settings', 'Exit studio']);
  const corner = document.querySelector<HTMLElement>('[data-preview-corner]')!;
  expect([corner.parentElement!.style.top, corner.parentElement!.style.right]).toEqual(['8px', '8px']);
  expect(within(corner).getByRole('button', { name: 'Exit studio' }).querySelector('svg.lucide-x')).not.toBeNull();
  expect(viewportProps.current.previewMode).toBe(true);
  // Its settings are closed as it opens; they are Orbit, then Render's own, the file's.
  expect(studioSettings()).toBeNull();
  await user.click(within(corner).getByRole('button', { name: 'Studio settings' }));
  expect(within(studioSettings()!).getAllByRole('heading').map(heading => heading.textContent)).toEqual(expect.arrayContaining(['Settings', 'Orbit']));
  const floor = () => within(studioSettings()!).getByRole('checkbox', { name: 'Floor', exact: true });
  expect(floor().getAttribute('aria-checked')).toBe('true');
  fireEvent.click(floor());
  expect([viewportProps.current.previewMode, chrome.hidden]).toEqual([true, true]);
  await user.click(within(corner).getByRole('button', { name: 'Exit studio' }));
  expect(chrome.hidden).toBe(false);
  expect(viewportProps.current.previewMode).toBe(false);
  expect(barButtons()).toEqual(['Display mode', 'Studio']);
  // The tools view is back in the file's own mode (the Studio drew Render over it: `presentMode`).
  expect(displayButton().getAttribute('aria-label')).toBe('Display mode: Solid');
  // The tool in hand is as it was, its section's tile with it; Display, put away, stays shut.
  expect(tileNames()).toEqual(['Harness tree', 'Reference', 'Position']);
  expect(tool('Pose').getAttribute('aria-pressed')).toBe('true');
  expect([displayPopover(), displayLit()]).toEqual([null, false]);
  // What was set in the Studio is the file's Render settings: the next visit has it.
  act(() => { fireEvent.click(document.querySelector('[data-harness-ask-preview]')!); });
  expect(viewportProps.current.previewMode).toBe(true);
  await user.click(within(document.querySelector<HTMLElement>('[data-preview-corner]')!).getByRole('button', { name: 'Studio settings' }));
  expect(floor().getAttribute('aria-checked')).toBe('false');
  await user.click(within(document.querySelector<HTMLElement>('[data-preview-corner]')!).getByRole('button', { name: 'Exit studio' }));
  expect([displayPopover(), displayLit()]).toEqual([null, false]);
});

it("the Studio is covered from the press until its picture has converged, then shows the finished picture", async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'requestAnimationFrame', 'cancelAnimationFrame'] });
  try {
    // The studio frame's progress, as the viewport reports it: samples drawn of those it accumulates.
    const picture = { sample: 0, samples: 16 };
    viewportHandle.current = { pictureProgress: () => ({ ...picture }) };
    frame();
    const cover = () => document.querySelector('[data-preview-cover]');
    const wait = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
    fireEvent.click(screen.getByRole('button', { name: 'Studio' }));
    // The press is answered at once, before the Studio itself is entered under it.
    expect(cover()?.textContent).toBe('Preparing studio…');
    await wait(100);
    expect([viewportProps.current.previewMode, cover() !== null]).toEqual([true, true]);
    // In Render, and still converging well past the shortest cover: it holds.
    picture.sample = 9;
    await wait(2000);
    expect([viewportProps.current.renderMode, cover() !== null]).toEqual([true, true]);
    // Every sample drawn: it lifts.
    picture.sample = 16;
    await wait(100);
    expect(cover()).toBeNull();
  } finally {
    vi.useRealTimers();
  }
});

it("the Studio opens still: Orbit, in its settings, turns the camera, and Orbit's speed is the file's, kept between visits and in its view, another file at the default", async () => {
  const user = userEvent.setup();
  const states: any[] = [];
  const enter = async () => { await enterStudio(user); await user.click(screen.getByRole('button', { name: 'Studio settings' })); };
  const orbit = () => within(studioSettings()!).getByRole('checkbox', { name: 'Orbit' });
  const speed = () => within(studioSettings()!).getByRole('combobox', { name: 'Orbit speed' }).textContent;
  // Its speed is a row of Orbit's, there while Orbit is on.
  const speedOnceOn = async () => { if (orbit().getAttribute('aria-checked') !== 'true') await user.click(orbit()); return speed(); };
  const first = frame({ onStateChange: state => states.push(state) });
  await enter();
  expect([orbit().getAttribute('aria-checked'), viewportProps.current.orbitPreview, within(studioSettings()!).queryByRole('combobox', { name: 'Orbit speed' })]).toEqual(['false', false, null]);
  await user.click(orbit());
  expect([orbit().getAttribute('aria-checked'), viewportProps.current.orbitPreview]).toEqual(['true', true]);
  // A fresh file orbits at 1×; 2× chosen.
  expect(speed()).toBe('1×');
  await user.click(within(studioSettings()!).getByRole('combobox', { name: 'Orbit speed' }));
  await user.click(screen.getByRole('option', { name: '2×' }));
  expect(viewportProps.current.previewOrbitSpeed).toBe(2);
  // Leaving and coming back: still again, at 2×.
  await user.click(screen.getByRole('button', { name: 'Exit studio' }));
  await enter();
  expect([orbit().getAttribute('aria-checked'), viewportProps.current.orbitPreview, await speedOnceOn()]).toEqual(['false', false, '2×']);
  // The file's view holds the speed, and no Orbit on or off.
  first.unmount();
  const saved = states.at(-1);
  expect(saved.playback).toEqual({ orbitSpeed: 2, autoplay: false });
  // Reopened from that view: 2×. Another file: 1×.
  const reopened = frame({ state: saved });
  await enter();
  expect(await speedOnceOn()).toBe('2×');
  reopened.unmount();
  frame({ name: 'other.harness' });
  await enter();
  expect(await speedOnceOn()).toBe('1×');
});

it('Escape puts the tool in hand down and takes Select up, what it set kept', async () => {
  const user = userEvent.setup();
  frame({ viewer: true });
  await user.click(tool('Annotate'));
  expect(tool('Annotate').getAttribute('aria-pressed')).toBe('true');
  act(() => { document.querySelector<HTMLElement>('[data-slot="cad-file-view"]')!.focus(); });
  await user.keyboard('{Escape}');
  expect([tool('Annotate').getAttribute('aria-pressed'), tool('Select').getAttribute('aria-pressed')]).toEqual(['false', 'true']);
});

it('Escape closes an inner choice before the settings surface', async () => {
  const user = userEvent.setup({ skipHover: true });
  frame();
  await openDisplay(user);
  const popover = displayPopover()!;
  await user.click(within(popover).getByRole('combobox', { name: 'Colour source' }));
  act(() => screen.getByRole('option', { name: 'Original', exact: true }).focus());
  await user.keyboard('{Escape}');
  expect(screen.queryByRole('option', { name: 'Original', exact: true })).toBeNull();
  expect(displayPopover()).toBe(popover);
  await user.keyboard('{Escape}');
  expect(displayPopover()).toBeNull();
  expect([displayMenu(), displayLit()]).toEqual([null, false]);
});
