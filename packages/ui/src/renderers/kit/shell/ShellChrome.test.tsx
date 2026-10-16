import React, { forwardRef } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

// The shell's chrome — the strip, the tool stack, the file sidebar, the view's actions, Display's dropdown and preview —
// under the smallest renderer that mounts it (`renderers/shell-harness`), with the WebGL viewport
// replaced by an empty box: nothing asserted here is drawn by it. What the viewport is handed is
// recorded, so a test can read what the shell asked of it.
const viewportProps: { current: any } = { current: null };
vi.mock('../../../../dist/renderers/kit/shell/ShellViewport.js', () => ({
  default: forwardRef(function MockViewport(props: any, _ref) {
    viewportProps.current = props;
    return <div data-mock-viewport=""><canvas />{typeof props.children === 'function'
      ? props.children({ runtimeRef: { current: null }, hostRef: { current: null }, mountRef: { current: null }, viewerReadyTick: 0, commitScene: () => true })
      : null}</div>;
  }),
}));
import HarnessRenderer from '../../../../dist/renderers/shell-harness/HarnessRenderer.js';
import { createHarnessRenderer } from '../../../../dist/renderers/shell-harness/index.js';
import { FileViewer } from '../../../../dist/file-viewer/index.js';
import { viewerLinks } from '../../../../dist/file-viewer/navigation/links.js';
import { ViewerHostContext } from '../../../../dist/host/context.js';
import { testHost } from '../../../../dist/host/testing/host.js';
import { ViewerMobileContext } from '../../../../dist/file-viewer/responsive.js';
import { TOOL_PANEL_WIDTH } from '../../../../dist/renderers/kit/tools/toolStackLayout.js';
import FileSidebar from '../../../../dist/renderers/kit/tools/FileSidebar.js';
import ToolPanel from '../../../../dist/renderers/kit/tools/ToolPanel.js';
import { FLOATING_CHROME_SURFACE_CLASS, FLOATING_SURFACE_CLASS } from '../../../../dist/lib/floatingSurface.js';
import { PerspectiveProjectionIcon } from '../../../../dist/renderers/kit/camera/ProjectionModeIcons.js';
import { SolidModeIcon } from '../../../../dist/renderers/kit/view-settings/DisplayModeOptions.js';

// jsdom lays nothing out, so the few sizes the chrome reads are given: a viewer 1280 × 800 whose
// stack column (under the strip) and sidebar room are each 600px tall, and each panel and the
// sidebar as big as its own style says.
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
  override(HTMLElement.prototype, 'clientHeight', { get(this: HTMLElement) { return this.hasAttribute('data-cad-tool-stack') || this.hasAttribute('data-file-sidebar-room') ? STACK_HEIGHT : 0; } });
  const rect = Element.prototype.getBoundingClientRect;
  override(Element.prototype, 'getBoundingClientRect', { value(this: HTMLElement) {
    if (this.hasAttribute('data-cad-scene-backdrop')) return DOMRect.fromRect({ x: 0, y: 0, ...VIEWER });
    if (this.matches('section[data-tool-panel], aside[data-cad-file-sidebar]')) return DOMRect.fromRect({ x: 8, y: 48, width: parseFloat(this.style.width) || 0, height: parseFloat(this.style.maxHeight) || 100 });
    return rect.call(this);
  } });
  for (const name of ['setPointerCapture', 'releasePointerCapture']) override(Element.prototype, name, { value() {} });
  override(Element.prototype, 'hasPointerCapture', { value: () => true });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); while (restores.length) restores.pop()!(); viewportProps.current = null; });

/** The tab's settings as a host hands them over, counting every write. */
function tabSettings(initial: object = {}) {
  let settings: any = { toolStack: { panels: {}, collapsed: {} }, ...initial };
  const listeners = new Set<() => void>();
  const store = { writes: 0, getSnapshot: () => settings, subscribe: (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    update(patch: object) { store.writes += 1; settings = { ...settings, ...patch }; listeners.forEach(listener => listener()); } };
  return store;
}
type Settings = ReturnType<typeof tabSettings>;
function frame({ name = 'panel.harness', preferences = tabSettings(), state = undefined as unknown, mobile = false, onStateChange = (_: unknown) => {}, onFullscreenChange = vi.fn(), appearance = { colorScheme: 'light' } as object, notice = null as React.ReactNode } = {}) {
  const props = { source: { id: 'one' }, file: { path: `/models/${name}`, name, kind: 'file' }, document: null, notice,
    onFullscreenChange, onReady() {}, onOpenFile() {}, appearance, state, onStateChange, reload() {}, data: { services: { preferences } } };
  const element = () => <ViewerHostContext.Provider value={testHost()}><ViewerMobileContext.Provider value={mobile}>
    <HarnessRenderer {...(props as any)} /></ViewerMobileContext.Provider></ViewerHostContext.Provider>;
  const view = render(element());
  return { ...view, preferences, remount: () => { view.unmount(); return render(element()); } };
}
const panel = (label: string) => document.querySelector<HTMLElement>(`section[data-tool-panel][aria-label="${label}"]`);
const shown = () => [...document.querySelectorAll<HTMLElement>('[data-cad-tool-stack] section[data-tool-panel]')].filter(node => !node.hidden).map(node => node.getAttribute('aria-label'));
// The file sidebar's sections on screen, top to bottom, and the bar itself.
const sections = () => [...document.querySelectorAll<HTMLElement>('[data-cad-file-sidebar] section[data-tool-panel]')].filter(node => !node.closest('[hidden]')).map(node => node.getAttribute('aria-label'));
const bar = () => document.querySelector<HTMLElement>('aside[data-cad-file-sidebar]')!;
const handles = (label: string) => [...panel(label)!.querySelectorAll('[role=separator]')].map(handle => handle.getAttribute('aria-label'));
const width = (label: string) => parseFloat(panel(label)!.style.width);
const layout = (preferences: Settings) => preferences.getSnapshot().toolStack;
const tool = (name: string) => within(screen.getByRole('group', { name: 'Interaction tools' })).getByRole('button', { name });
function drag(handle: Element, [dx, dy]: [number, number], { release = true } = {}) {
  fireEvent.pointerDown(handle, { button: 0, pointerId: 1, clientX: 100, clientY: 100 });
  fireEvent.pointerMove(handle, { pointerId: 1, clientX: 100 + dx, clientY: 100 + dy });
  if (release) fireEvent.pointerUp(handle, { pointerId: 1, clientX: 100 + dx, clientY: 100 + dy });
}

it('the strip is at the top centre with the stack under it, the file sidebar at the top-left: a tool panel\'s width, capped at the viewer\'s height, and nothing is stored until a person sizes it', () => {
  const { preferences } = frame();
  // The strip and the stack under it, 8px down from the viewer's top, centred, stopping 8px above
  // the cube's line (the cube itself 8px off the bottom); the sidebar's room 8px in from the top
  // and left, down the viewer's full height to 8px off its bottom.
  expect(document.querySelector<HTMLElement>('[data-cad-tool-groups]')!.style).toMatchObject({ top: '8px', left: '50%', bottom: 'calc(16px + 6rem)', transform: 'translateX(-50%)' });
  expect(document.querySelector<HTMLElement>('[data-file-sidebar-room]')!.style).toMatchObject({ top: '8px', left: '8px', bottom: '8px' });
  expect([sections(), shown()]).toEqual([['Harness tree', 'Harness reference'], []]);
  expect(TOOL_PANEL_WIDTH).toBe(164);
  expect([bar().style.width, bar().style.maxHeight]).toEqual([`${TOOL_PANEL_WIDTH}px`, `${STACK_HEIGHT}px`]);
  // A section is the bar's width and its content's height: no width, cap, scroller or grip of its own.
  for (const label of ['Harness tree', 'Harness reference']) {
    expect([panel(label)!.style.width, panel(label)!.style.maxHeight], label).toEqual(['', '']);
    expect(panel(label)!.querySelector('[data-radix-scroll-area-viewport]'), label).toBeNull();
    expect(handles(label), label).toEqual([]);
  }
  // The bar scrolls, as one, and is the person's to size by one grip in its bottom-right corner
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

it("the file sidebar is sized by its corner: width and cap in one write when the pointer lets go, the keyboard by 16px and Home and End to the bounds, and a remount opens at what was left", () => {
  const { preferences, remount } = frame();
  const corner = screen.getByRole('separator', { name: 'Resize file sidebar' });
  drag(corner, [100, -100], { release: false });
  // Mid-drag: the bar follows the corner; nothing is written yet.
  expect([bar().style.width, bar().style.maxHeight]).toEqual([`${TOOL_PANEL_WIDTH + 100}px`, `${STACK_HEIGHT - 100}px`]);
  expect(corner.hasAttribute('data-dragging')).toBe(true);
  expect(preferences.writes).toBe(0);
  fireEvent.pointerUp(corner, { pointerId: 1, clientX: 200, clientY: 0 });
  expect(preferences.writes).toBe(1);
  expect(layout(preferences).panels).toEqual({ sidebar: { width: TOOL_PANEL_WIDTH + 100, height: STACK_HEIGHT - 100 } });
  // From the keyboard, one axis a key: Left/Right the width, Up/Down the cap; Home and End both, to their bounds.
  fireEvent.keyDown(corner, { key: 'ArrowLeft' });
  fireEvent.keyDown(corner, { key: 'ArrowDown' });
  expect(layout(preferences).panels.sidebar).toEqual({ width: TOOL_PANEL_WIDTH + 84, height: STACK_HEIGHT - 84 });
  fireEvent.keyDown(corner, { key: 'Home' });
  // Narrower than a tool panel, down to room for a heading's name, its chevron and the toggle.
  expect(layout(preferences).panels.sidebar).toEqual({ width: 120, height: 64 });
  fireEvent.keyDown(corner, { key: 'End' });
  expect(layout(preferences).panels.sidebar).toEqual({ width: VIEWER.width / 2, height: STACK_HEIGHT });
  remount();
  expect([bar().style.width, bar().style.maxHeight]).toEqual([`${VIEWER.width / 2}px`, `${STACK_HEIGHT}px`]);
  // Reset puts it back.
  act(() => preferences.update({ toolStack: { panels: {}, collapsed: {} } }));
  expect([bar().style.width, bar().style.maxHeight]).toEqual([`${TOOL_PANEL_WIDTH}px`, `${STACK_HEIGHT}px`]);
});

it("the sidebar's sections are on screen whatever the tool, on a desktop and a phone, the tree with no X, and an older record's closed tree is nobody's", () => {
  for (const mobile of [false, true]) {
    const { preferences } = frame({ mobile, preferences: tabSettings({ toolStack: { panels: {}, collapsed: {}, closed: { tree: true } } }) });
    expect(sections()).toEqual(['Harness tree', 'Harness reference']);
    expect(within(panel('Harness tree')!).queryByRole('button', { name: /^Close/ })).toBeNull();
    expect(tool('Select').getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(tool('Select'));
    fireEvent.click(tool('Annotate'));
    expect(sections()).toEqual(['Harness tree', 'Harness reference']);
    fireEvent.click(tool('Pose'));
    expect([sections(), preferences.writes]).toEqual([['Harness tree', 'Harness reference', 'Harness position'], 0]);
    cleanup();
  }
});

it("a section folds at a press anywhere on its heading row but its other buttons, with no hover of the row's own, its chevron before its name; its content kept mounted, and which sections are folded is kept across a remount", () => {
  const { preferences, remount } = frame();
  const reference = panel('Harness reference')!;
  // The fold is the heading's button, named by the section, as an accordion's: its chevron, turned
  // down while open, then the name; the row has no hover background, under the head's rule.
  const fold = within(within(reference).getByRole('heading', { name: 'Reference' })).getByRole('button', { name: 'Reference' });
  const chevron = () => fold.querySelector('svg')!;
  expect([fold.getAttribute('aria-expanded'), chevron().classList.contains('rotate-90'), fold.firstElementChild === chevron()]).toEqual(['true', true, true]);
  expect(reference.querySelector('[data-tool-panel-heading]')!.className).not.toMatch(/\bhover:bg-/);
  expect(reference.querySelector('[data-sidebar-head]')!.className).toMatch(/\bborder-t\b/);
  // Its X is not the row's: a press on it leaves the section open.
  fireEvent.click(within(reference).getByRole('button', { name: 'Close harness reference' }));
  expect(reference.hasAttribute('data-collapsed')).toBe(false);
  fireEvent.click(reference.querySelector('[data-tool-panel-heading]')!);
  expect([fold.getAttribute('aria-expanded'), chevron().classList.contains('rotate-90')]).toEqual(['false', false]);
  expect(reference.hasAttribute('data-collapsed')).toBe(true);
  expect(within(reference).getByText('Fact 1')).toBeTruthy();
  expect(reference.querySelector('[data-tool-panel-body]')!.closest('[hidden]')).not.toBeNull();
  // The tree is headed by its name in the sidebar and folds as well; its find field, while open,
  // takes the heading row's place, and closing it brings the row back.
  const tree = panel('Harness tree')!;
  fireEvent.click(within(tree).getByRole('button', { name: 'Find in tree' }));
  expect([tree.querySelector('[data-tool-panel-heading]'), within(tree.querySelector('[data-sidebar-head]')!).getByText('Filter')]).toEqual([null, expect.anything()]);
  fireEvent.click(within(tree).getByRole('button', { name: 'Close find' }));
  expect(within(tree).queryByText('Filter')).toBeNull();
  fireEvent.click(within(tree).getByRole('heading', { name: 'Harness tree' }));
  expect(tree.hasAttribute('data-collapsed')).toBe(true);
  expect(layout(preferences).collapsed).toEqual({ reference: true, tree: true });
  remount();
  expect(panel('Harness reference')!.hasAttribute('data-collapsed')).toBe(true);
  fireEvent.click(within(panel('Harness reference')!).getByRole('button', { name: 'Reference' }));
  expect(layout(preferences).collapsed).toEqual({ tree: true });
});

it("a heading docked at the sidebar's bottom is the way to its section: a press opens it, if folded, and brings it to the top at once", () => {
  const { preferences } = frame();
  const reference = panel('Harness reference')!;
  fireEvent.click(within(reference).getByRole('button', { name: 'Reference' }));
  // The section's anchor 100px under the top of the bar's view: that far is what the bar scrolls.
  const scroller = bar().querySelector<HTMLElement>('[data-file-sidebar-scroller]')!;
  let scrollTop = 0;
  Object.defineProperty(scroller, 'scrollTop', { configurable: true, get: () => scrollTop, set: value => { scrollTop = value; } });
  scroller.getBoundingClientRect = () => DOMRect.fromRect({ x: 8, y: 20, width: 164, height: 400 });
  const heading = reference.querySelector<HTMLElement>('[data-sidebar-head]')!;
  const anchor = heading.previousElementSibling as HTMLElement;
  expect(anchor.hasAttribute('data-sidebar-anchor')).toBe(true);
  anchor.getBoundingClientRect = () => DOMRect.fromRect({ x: 8, y: 120 - scrollTop, width: 164, height: 0 });
  // Docked (the bar's own measure, which jsdom cannot make): a press does not fold it again.
  heading.setAttribute('data-stuck', '');
  fireEvent.click(within(reference).getByRole('heading', { name: 'Reference' }));
  expect([reference.hasAttribute('data-collapsed'), layout(preferences).collapsed, scrollTop]).toEqual([false, {}, 100]);
});

it("the sidebar slides away by the button on its first heading and back by the one it leaves in its corner, and stays as it was left", () => {
  const { preferences, remount } = frame();
  const buttons = (label: string) => [...panel(label)!.querySelector('[data-tool-panel-heading]')!.querySelectorAll('button')]
    .map(button => button.getAttribute('aria-label') || button.textContent);
  // The fold leads each row, its name; then its actions, its X, and on the first the bar's own toggle, last.
  expect([buttons('Harness tree'), buttons('Harness reference')]).toEqual([['Harness tree', 'Find in tree', 'Hide file sidebar'], ['Reference', 'Close harness reference']]);
  const show = () => screen.getByRole('button', { name: 'Show file sidebar', hidden: true });
  expect([bar().hasAttribute('data-open'), bar().hasAttribute('inert'), show().hasAttribute('inert')]).toEqual([true, false, true]);
  fireEvent.click(within(panel('Harness tree')!).getByRole('button', { name: 'Hide file sidebar' }));
  expect([bar().hasAttribute('data-open'), bar().hasAttribute('inert'), show().hasAttribute('inert'), layout(preferences).collapsed]).toEqual([false, true, false, { sidebar: true }]);
  remount();
  expect(bar().hasAttribute('data-open')).toBe(false);
  fireEvent.click(show());
  expect([bar().hasAttribute('data-open'), layout(preferences).collapsed]).toEqual([true, {}]);
});

it("a section alone does not fold: its heading is its name, and the bar's own button", () => {
  const settings = tabSettings();
  render(<FileSidebar layout={settings.getSnapshot().toolStack} onLayoutChange={() => {}}>
    <ToolPanel id="tree" label="Links" fit="tree"><p>Row</p></ToolPanel>
  </FileSidebar>);
  const links = panel('Links')!;
  expect(within(links).getByRole('heading').textContent).toBe('Links');
  expect(within(links).getAllByRole('button').map(button => button.getAttribute('aria-label'))).toEqual(['Hide file sidebar']);
  expect(links.querySelector('[data-tool-panel-fold-row]')).toBeNull();
});

const displayPopover = () => document.querySelector<HTMLElement>('[data-display-popover]');
// Display: its button, the display mode in hand ("Display mode: Solid"); its settings are its menu's Custom submenu.
const displayButton = (root: HTMLElement = document.body) => within(root).getByRole('button', { name: /^Display mode: / });
const displayLit = (root?: HTMLElement) => displayButton(root).getAttribute('aria-expanded') === 'true';
const displayMenu = () => document.querySelector<HTMLElement>('[role=menu][aria-label="Display mode"]');
async function openDisplay(user: ReturnType<typeof userEvent.setup>, root?: HTMLElement) {
  await user.click(displayButton(root));
  await user.click(displayMenu()!.querySelector<HTMLElement>('[data-display-mode-option="custom"]')!);
  await waitFor(() => expect(displayPopover()).not.toBeNull());
}
// A control by its name, whatever it says it is set to ("Projection: Orthographic" is Projection).
const named = (button: Element) => button.getAttribute('aria-label')!.split(':')[0];
// The view's controls: the strip's last, after its tools and a rule.
const barButtons = () => [...document.querySelector('[role=group][aria-label="Interaction tools"] [data-view-controls]')?.querySelectorAll('button') ?? []].map(named);
// Preview's own, in a row at the view's top centre.
const cornerButtons = () => [...document.querySelector('[data-preview-corner]')?.querySelectorAll('button') ?? []].map(named);
// The strip's tools, before the view's controls.
const strip = () => [...screen.getByRole('group', { name: 'Interaction tools' }).querySelectorAll('button')].filter(button => !button.closest('[data-view-controls]')).map(button => button.getAttribute('aria-label'));

it('while the model loads the viewer shows none of its own chrome, and all of it returns with the model', () => {
  frame();
  const stage = (name: string) => act(() => { fireEvent.click(document.querySelector(`[data-harness-stage="${name}"]`)!); });
  expect(screen.getByRole('group', { name: 'Interaction tools' })).toBeTruthy();
  expect(barButtons()).toEqual(['Display mode', 'Projection', 'Preview']);
  stage('finding');
  expect(screen.queryByRole('group', { name: 'Interaction tools' })).toBeNull();
  expect(document.querySelector('[data-cad-tool-stack]')).toBeNull();
  expect(barButtons()).toEqual([]);
  expect(viewportProps.current.isLoading).toBe(true);
  stage('idle');
  expect(screen.getByRole('group', { name: 'Interaction tools' })).toBeTruthy();
  expect(barButtons()).toEqual(['Display mode', 'Projection', 'Preview']);
  // With nothing to work on yet, the strip is idle together, and so are Display and Preview.
  stage('unready');
  expect(strip()).toEqual(['Select', 'Annotate', 'Keep', 'Pose']);
  const idle = (name: string) => (name === 'Display' ? displayButton() : name === 'Preview' ? screen.getByRole('button', { name }) : tool(name)).hasAttribute('disabled');
  expect(['Select', 'Annotate', 'Display', 'Preview'].map(idle)).toEqual([true, true, true, true]);
  stage('idle');
  expect(['Select', 'Annotate', 'Display', 'Preview'].map(idle)).toEqual([false, false, false, false]);
});

it("Display and Preview are a 3D view's, as its renderer declares: a view that does not declare it has neither, not disabled ones, and a request for Preview leaves it as it is", () => {
  const onFullscreenChange = vi.fn();
  // Preview asked for from outside the navbar, as a link or a host request would.
  const ask = () => act(() => { fireEvent.click(document.querySelector('[data-harness-ask-preview]')!); });
  // A 3D view (the harness's, declared `previewable`): Display and Preview end the strip, and a request enters Preview.
  frame({ onFullscreenChange });
  expect(barButtons()).toEqual(['Display mode', 'Projection', 'Preview']);
  ask();
  expect([viewportProps.current.previewMode, onFullscreenChange.mock.calls]).toEqual([true, [[true]]]);
  cleanup();
  onFullscreenChange.mockClear();
  // A view whose renderer does not declare it (`flat.harness`, a 2D view's stand-in): neither on the
  // strip, no rule, and the same request leaves the normal view on screen, its tools up.
  frame({ name: 'flat.harness', onFullscreenChange });
  expect(barButtons()).toEqual([]);
  for (const name of [/^Display mode: /, 'Preview']) expect(screen.queryByRole('button', { name })).toBeNull();
  expect(document.querySelector('[data-strip-rule]')).toBeNull();
  ask();
  expect([viewportProps.current.previewMode, onFullscreenChange.mock.calls]).toEqual([false, []]);
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
  expect(barButtons()).toEqual(['Display mode', 'Projection', 'Preview']);
  expect(viewportProps.current.viewCube).toBe(true);
});

it("a dismissed alert's own icon, at the navbar's right, brings its card back; it goes with the card's return, and when the alert clears", async () => {
  // The harness under the real FileViewer, whose navbar's right holds nothing of its own, nor the
  // view's controls (they end the strip): only a dismissed alert's icon.
  const host = testHost({ links: viewerLinks({ version: '0.7.5' }), environment: { colorScheme: 'light', platform: 'darwin' },
    files: { id: 'one', stat: async (path: string) => ({ path, name: path.split('/').pop()!, kind: 'file', extension: 'harness', size: 1 }) } });
  render(<FileViewer file="/models/one.harness" host={host as any} renderers={[createHarnessRenderer({ preferences: tabSettings() })]}
    state={{}} onStateChange={() => {}} />);
  await screen.findByRole('button', { name: 'Preview' });
  const right = () => [...document.querySelector('[data-viewer-navbar] > div')!.querySelectorAll('a, button')].map(node => node.getAttribute('aria-label'));
  const card = () => screen.queryByRole('alert');
  const stage = (name: string) => act(() => { fireEvent.click(document.querySelector(`[data-harness-stage="${name}"]`)!); });
  const view: string[] = [];
  expect([right(), barButtons()]).toEqual([view, ['Display mode', 'Projection', 'Preview']]);
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
  const corner = () => [...document.querySelector('[data-viewport-top-right]')?.children ?? []]
    .map(child => child.hasAttribute('data-viewport-notice') ? 'notice' : child.hasAttribute('data-quick-edit') ? 'Quick Edit' : child.tagName);
  const stage = (name: string) => act(() => { fireEvent.click(document.querySelector(`[data-harness-stage="${name}"]`)!); });
  // On a narrow view the column starts below the tool strip's row, so the strip stays in reach.
  const clearsStrip = () => document.querySelector('[data-viewport-top-right]')!.className.includes('@max-md/cad-viewport:top-');
  frame({ notice });
  stage('finding');
  expect(screen.queryByRole('dialog', { name: 'A notice' })).toBeNull();
  stage('broken');  // a load the model did not survive: only its card
  expect(screen.queryByRole('dialog', { name: 'A notice' })).toBeNull();
  stage('idle');
  expect(corner()).toEqual(['notice', 'Quick Edit']);
  expect(clearsStrip()).toBe(true);
  cleanup();
  // Shown small, the view has no Quick Edit and no strip, and the notice still asks.
  frame({ notice, appearance: { colorScheme: 'light', compact: true } });
  expect(corner()).toEqual(['notice']);
  expect(clearsStrip()).toBe(false);
});

it('a host showing the view small gets the model alone: no tools, no view actions, no Quick Edit, no cube', () => {
  frame({ appearance: { colorScheme: 'light', compact: true } });
  expect(screen.queryByRole('group', { name: 'Interaction tools' })).toBeNull();
  expect(document.querySelector('[data-cad-tool-stack]')).toBeNull();
  expect(barButtons()).toEqual([]);
  expect(document.querySelector('[data-quick-edit]')).toBeNull();
  expect(viewportProps.current.viewCube).toBe(false);
});

it("Display, Projection and Preview end the strip after a rule, Display the mode in hand with its menu of modes and Custom's settings: opened over Annotate it leaves Annotate in hand with its controls, and the stack as it was", async () => {
  const user = userEvent.setup();
  const { remount } = frame();
  // The view's controls end the strip, after its tools and a rule: Display, Projection, Preview; nothing of them in the navbar.
  expect([strip(), barButtons()]).toEqual([['Select', 'Annotate', 'Keep', 'Pose'], ['Display mode', 'Projection', 'Preview']]);
  const rule = document.querySelector('[data-strip-rule]')!;
  expect([rule.getAttribute('role'), rule.nextElementSibling!.hasAttribute('data-view-controls')]).toEqual(['separator', true]);
  // Display is the mode in hand, a plain tile: its icon (Solid, as a file opens); Projection's, the projection in hand's.
  const solid = render(<SolidModeIcon />).container.querySelector('svg')!;
  expect([displayButton().getAttribute('aria-label'), displayButton().getAttribute('data-display-mode'), displayButton().querySelector('svg')!.innerHTML,
    displayButton().querySelectorAll('svg').length]).toEqual(['Display mode: Solid', 'solid', solid.innerHTML, 1]);
  // Display's menu: the modes the file offers (the harness draws no CAD edges: none made of them),
  // the one in hand checked, then Custom, unchecked.
  await user.click(displayButton());
  const options = () => [...displayMenu()!.querySelectorAll('[data-display-mode-option]')]
    .map(item => [item.getAttribute('data-display-mode-option'), item.getAttribute('aria-checked')]);
  expect(options()).toEqual([['solid', 'true'], ['render', 'false'], ['grid', 'false'], ['custom', 'false']]);
  await user.click(within(displayMenu()!).getByRole('menuitemradio', { name: 'Render' }));
  expect([displayButton().getAttribute('aria-label'), displayMenu()]).toEqual(['Display mode: Render', null]);
  // Projection is its own tile, its icon the projection in hand (Render's preset looks in perspective);
  // its dropdown the two projections, and choosing one is the view's.
  const projection = screen.getByRole('button', { name: 'Projection: Perspective' });
  const perspective = render(<PerspectiveProjectionIcon />).container.querySelector('svg')!;
  expect([projection.getAttribute('data-projection'), projection.querySelector('svg')!.innerHTML]).toEqual(['perspective', perspective.innerHTML]);
  await user.click(projection);
  const projections = document.querySelector<HTMLElement>('[role=menu][aria-label="Projection"]')!;
  expect(within(projections).getAllByRole('menuitemradio').map(item => [item.textContent, item.getAttribute('aria-checked')]))
    .toEqual([['Orthographic', 'false'], ['Perspective', 'true']]);
  await user.click(within(projections).getByRole('menuitemradio', { name: 'Orthographic' }));
  expect(screen.getByRole('button', { name: 'Projection: Orthographic' }).getAttribute('data-projection')).toBe('orthographic');
  await user.click(tool('Keep'));
  await user.click(tool('Annotate'));
  expect(tool('Annotate').getAttribute('aria-pressed')).toBe('true');
  // Annotate's controls are a row under the strip, not a panel of the stack, with nothing to fold; the
  // stack holds the kept effect, and the sidebar is as it was.
  const stack = shown();
  expect([stack, sections()]).toEqual([['Kept controls'], ['Harness tree', 'Harness reference']]);
  const controls = screen.getByRole('toolbar', { name: 'Annotation controls' });
  expect(within(controls).queryByRole('button', { name: /^(?:Collapse|Expand) / })).toBeNull();
  // Custom is a submenu: the settings, starting from Solid's.
  await openDisplay(user);
  expect([displayPopover()!.getAttribute('aria-label'), displayLit()]).toEqual(['Display settings', true]);
  // Nothing joins the stack, and the tool in hand, its controls and the kept effect stay.
  expect([shown(), screen.getAllByRole('toolbar', { name: 'Annotation controls' })]).toEqual([stack, [controls]]);
  expect([tool('Annotate').getAttribute('aria-pressed'), tool('Keep').getAttribute('aria-pressed')]).toEqual(['true', 'true']);
  expect(viewportProps.current.annotationEnabled).toBe(true);
  // Nothing about it is a panel of the stack: its first heading is "Custom display" and its Reset,
  // with no body and no X, and it holds the file's view alone, none of the host's settings.
  expect(displayPopover()!.closest('[data-tool-panel]')).toBeNull();
  const heading = displayPopover()!.querySelector('[data-settings-section="display"] [data-settings-section-heading]')!;
  expect([heading.textContent, [...heading.querySelectorAll('button[aria-label]')].map(button => button.getAttribute('aria-label'))])
    .toEqual(['Custom display', ['Reset']]);
  expect(within(displayPopover()!).queryByRole('combobox', { name: 'Mode' })).toBeNull();
  expect([...displayPopover()!.querySelectorAll('[data-settings-section]')].map(section => section.getAttribute('data-settings-section')))
    .toEqual(['display', 'surfaces', 'grid-axes', 'lighting', 'background', 'floor']);
  // A setting changed is Custom: the button says so, and Custom is the one checked.
  await user.click(within(displayPopover()!).getByRole('button', { name: 'Enable Grid / Axes' }));
  expect([displayButton().getAttribute('aria-label'), displayButton().getAttribute('data-display-mode')]).toEqual(['Display mode: Custom', 'custom']);
  expect(options()).toEqual([['solid', 'false'], ['render', 'false'], ['grid', 'false'], ['custom', 'true']]);
  // Escape puts it away and leaves the stack's panels and the tool alone.
  await user.keyboard('{Escape}');
  await waitFor(() => expect(displayPopover()).toBeNull());
  expect([displayMenu(), shown(), tool('Annotate').getAttribute('aria-pressed')]).toEqual([null, stack, 'true']);
  // Choosing a mode puts it back as it comes.
  await user.click(displayButton());
  await user.click(within(displayMenu()!).getByRole('menuitemradio', { name: 'Render' }));
  expect(displayButton().getAttribute('aria-label')).toBe('Display mode: Render');
  // A press outside it — on the model — puts it away too, and the kept effect stays.
  await openDisplay(user);
  await user.pointer({ keys: '[MouseLeft]', target: document.querySelector('[data-mock-viewport] canvas')! });
  await waitFor(() => expect(displayPopover()).toBeNull());
  expect(tool('Keep').getAttribute('aria-pressed')).toBe('true');
  // Another opening of the file starts with it shut.
  await openDisplay(user);
  remount();
  expect([displayPopover(), displayLit()]).toEqual([null, false]);
});

it("two surfaces: the strip and the stack share the light chrome surface, and the file sidebar, Display's dropdown and a menu over the viewport the more opaque one", async () => {
  const user = userEvent.setup();
  frame();
  expect(FLOATING_CHROME_SURFACE_CLASS).not.toBe(FLOATING_SURFACE_CLASS);
  const classes = (element: Element) => element.className.split(/\s+/);
  const has = (element: Element, surface: string) => surface.split(' ').every(name => classes(element).includes(name));
  expect(has(screen.getByRole('group', { name: 'Interaction tools' }), FLOATING_CHROME_SURFACE_CLASS)).toBe(true);
  fireEvent.click(tool('Keep'));
  expect(has(panel('Kept controls')!, FLOATING_CHROME_SURFACE_CLASS)).toBe(true);
  // The sidebar is read for as long as the file is open, and its sections have no surface of their own.
  expect(has(bar(), FLOATING_SURFACE_CLASS)).toBe(true);
  expect(has(panel('Harness tree')!, FLOATING_CHROME_SURFACE_CLASS)).toBe(false);
  // Display's settings are read while they are up: their text must not compete with the model.
  await openDisplay(user);
  expect(has(displayPopover()!, FLOATING_SURFACE_CLASS)).toBe(true);
  await user.keyboard('{Escape}');
  await user.click(screen.getByRole('button', { name: 'Preview' }));
  await user.click(screen.getByRole('button', { name: 'Orbit' }));
  expect(has(screen.getByRole('menu'), FLOATING_SURFACE_CLASS)).toBe(true);
});

it("Preview is fullscreen: the strip with the view's controls, the stack and the sidebar step aside; its row at the top centre holds Orbit, Display — the file's own settings — and the way out, which brings back the tool in hand with its panel", async () => {
  const user = userEvent.setup();
  const onFullscreenChange = vi.fn();
  frame({ onFullscreenChange });
  await user.click(tool('Pose'));
  expect(sections()).toContain('Harness position');
  await openDisplay(user);
  await user.click(screen.getByRole('button', { name: 'Preview' }));
  const chrome = document.querySelector<HTMLElement>('[data-preview-chrome]')!;
  expect([chrome.hidden, chrome.hasAttribute('inert')]).toEqual([true, true]);
  expect(chrome.contains(document.querySelector('[data-cad-tool-stack]'))).toBe(true);
  expect(chrome.contains(bar())).toBe(true);
  expect(displayPopover()).toBeNull();
  // The page steps aside (the host hides its navbar), the strip goes with the tools view, and the
  // view's controls are preview's own row at the top centre: Orbit, then Display, then the way out
  // where Preview sat.
  expect(onFullscreenChange.mock.calls).toEqual([[true]]);
  expect([barButtons(), screen.queryByRole('group', { name: 'Interaction tools' })]).toEqual([[], null]);
  expect(cornerButtons()).toEqual(['Orbit', 'Display mode', 'Projection', 'Exit preview']);
  const corner = document.querySelector<HTMLElement>('[data-preview-corner]')!;
  expect([corner.parentElement!.className, corner.parentElement!.style.top]).toEqual([expect.stringMatching(/\bleft-1\/2\b.*-translate-x-1\/2/), '8px']);
  expect(within(corner).getByRole('button', { name: 'Exit preview' }).querySelector('svg.lucide-minimize-2')).not.toBeNull();
  expect(viewportProps.current.previewMode).toBe(true);
  // Display is preview's as well: the same dropdown over the file's own settings, preview's tools
  // still put away under it.
  await openDisplay(user, corner);
  expect([displayPopover()!.getAttribute('aria-label'), displayLit(corner)]).toEqual(['Display settings', true]);
  // (A click, not a pointer's path to it: jsdom has no geometry for a submenu's pointer grace area.)
  fireEvent.click(within(displayPopover()!).getByRole('button', { name: 'Enable Grid / Axes' }));
  expect([viewportProps.current.previewMode, chrome.hidden]).toEqual([true, true]);
  await user.click(within(corner).getByRole('button', { name: 'Exit preview' }));
  expect(onFullscreenChange.mock.calls).toEqual([[true], [false]]);
  expect(chrome.hidden).toBe(false);
  expect(viewportProps.current.previewMode).toBe(false);
  expect(barButtons()).toEqual(['Display mode', 'Projection', 'Preview']);
  // The stack comes back as it was, the tool in hand with its panel; Display, put away, stays shut,
  // even where Preview was asked for without a press outside it.
  expect(sections()).toContain('Harness position');
  expect(tool('Pose').getAttribute('aria-pressed')).toBe('true');
  expect([displayPopover(), displayLit()]).toEqual([null, false]);
  await openDisplay(user);
  // What was set in preview is the file's view: the tools view's Display holds it.
  expect(within(displayPopover()!).getByRole('button', { name: 'Disable Grid / Axes' })).toBeTruthy();
  act(() => { fireEvent.click(document.querySelector('[data-harness-ask-preview]')!); });
  expect([viewportProps.current.previewMode, displayPopover()]).toEqual([true, null]);
  await user.click(within(document.querySelector<HTMLElement>('[data-preview-corner]')!).getByRole('button', { name: 'Exit preview' }));
  expect([displayPopover(), displayLit()]).toEqual([null, false]);
});

it("preview's Orbit is the file's: kept between previews, written to the file's view, restored when it is reopened, and another file starts at the defaults", async () => {
  const user = userEvent.setup();
  const states: any[] = [];
  const settings = () => screen.getByRole('menu', { name: 'Orbit' });
  const choices = async () => {
    await user.click(screen.getByRole('button', { name: 'Orbit' }));
    const orbit = within(settings()).getByRole('menuitemcheckbox', { name: 'Orbit' }).getAttribute('aria-checked');
    const speed = within(settings()).getByRole('menuitem', { name: /^Orbit speed/ }).getAttribute('aria-label');
    await user.keyboard('{Escape}');
    return [orbit, speed];
  };
  const first = frame({ onStateChange: state => states.push(state) });
  await user.click(screen.getByRole('button', { name: 'Preview' }));
  // A static file has nothing under the model: no playbar, and Orbit leads the corner.
  expect(screen.queryByRole('toolbar', { name: 'Animation playback' })).toBeNull();
  expect(cornerButtons()).toEqual(['Orbit', 'Display mode', 'Projection', 'Exit preview']);
  // A fresh file orbits at 1×.
  expect(await choices()).toEqual(['true', 'Orbit speed: 1×']);
  // Orbit off, and its speed 2×.
  await user.click(screen.getByRole('button', { name: 'Orbit' }));
  await user.click(within(settings()).getByRole('menuitemcheckbox', { name: 'Orbit' }));
  expect(screen.getByRole('menu', { name: 'Orbit' })).toBeTruthy();
  const speedItem = within(settings()).getByRole('menuitem', { name: /^Orbit speed/ });
  await user.click(speedItem);
  // jsdom has no geometry for the submenu's pointer grace area; the keyboard path is the same handler.
  (await screen.findByRole('menuitemradio', { name: '2×' })).focus();
  await user.keyboard('{Enter}');
  // Leaving and re-entering preview keeps both.
  await user.click(screen.getByRole('button', { name: 'Exit preview' }));
  await user.click(screen.getByRole('button', { name: 'Preview' }));
  expect(await choices()).toEqual(['false', 'Orbit speed: 2×']);
  // The file's view holds them.
  first.unmount();
  const saved = states.at(-1);
  expect(saved.playback).toEqual({ orbit: false, orbitSpeed: 2, autoplay: false });
  // Reopened from that view: the same choices. Another file: the defaults.
  const reopened = frame({ state: saved });
  await user.click(screen.getByRole('button', { name: 'Preview' }));
  expect(await choices()).toEqual(['false', 'Orbit speed: 2×']);
  reopened.unmount();
  frame({ name: 'other.harness' });
  await user.click(screen.getByRole('button', { name: 'Preview' }));
  expect(await choices()).toEqual(['true', 'Orbit speed: 1×']);
});

it('the Display dropdown keeps its controls together: a dropdown or a color editor inside it goes first, and without taking the dropdown', async () => {
  override(Element.prototype, 'scrollIntoView', { value() {} });
  // An open listbox makes the page under it inert to the pointer (`pointer-events: none`); a press
  // there still reaches the listbox's outside-press layer, as it does in a browser. No hover on the way
  // to a press: jsdom has no geometry for the Custom submenu's pointer grace area, so a pointer's path
  // out of its trigger would close it.
  const user = userEvent.setup({ pointerEventsCheck: 0, skipHover: true });
  frame();
  await openDisplay(user);
  const popover = displayPopover()!;
  expect(popover.querySelectorAll('[data-settings-sections]').length).toBe(1);
  const style = within(popover).getByRole('combobox', { name: 'Surface style' });
  // (Found before a listbox opens: an open one hides the rest of the page from the accessibility tree.)
  const heading = within(popover).getByRole('heading', { name: 'Surfaces' });
  for (const dismiss of ['Escape', 'trigger', 'heading']) {
    await user.click(style);
    expect(screen.getByRole('listbox')).toBeTruthy();
    if (dismiss === 'Escape') await user.keyboard('{Escape}');
    else await user.click(dismiss === 'trigger' ? style : heading);
    expect(screen.queryByRole('listbox'), dismiss).toBeNull();
    expect(displayPopover(), `dismissing the dropdown with ${dismiss} keeps Display open`).toBe(popover);
  }
  // Solid draws no grid: its colour is there once Grid / Axes is on; Escape closes the colour editor, then Display.
  expect(within(popover).queryByRole('button', { name: 'Grid color' })).toBeNull();
  await user.click(within(popover).getByRole('button', { name: 'Enable Grid / Axes' }));
  await user.click(within(popover).getByRole('button', { name: 'Grid color' }));
  expect(screen.getByRole('spinbutton', { name: 'Color opacity' })).toBeTruthy();
  await user.keyboard('{Escape}');
  expect(screen.queryByRole('spinbutton', { name: 'Color opacity' })).toBeNull();
  expect(displayPopover()).toBe(popover);
  await user.keyboard('{Escape}');
  expect([displayPopover(), displayLit()]).toEqual([null, false]);
});
