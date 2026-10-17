import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'node:http';
import { build } from 'esbuild';
import { chromium } from 'playwright';
import { PNG } from 'pngjs';
import { writeGlb } from '@text-to-cad/core/glb/writeGlb.js';

// Inline fixtures, served from memory: two boxes with authored colours, the same
// pair with one box driven by a clip whose first key IS the rest pose, and bytes
// that are not a GLB at all.
function box([x, y, z], size) {
  const [a, b, c] = [x + size, y + size, z + size];
  const corners = [[x, y, z], [a, y, z], [a, b, z], [x, b, z], [x, y, c], [a, y, c], [a, b, c], [x, b, c]];
  const faces = [[0, 2, 1, 0, 3, 2], [4, 5, 6, 4, 6, 7], [0, 1, 5, 0, 5, 4], [2, 3, 7, 2, 7, 6], [1, 2, 6, 1, 6, 5], [3, 0, 4, 3, 4, 7]];
  return new Float32Array(faces.flat().flatMap(index => corners[index]));
}
const primitives = [
  { name: 'base', node: 'base', positions: box([0, 0, 0], 0.02), color: '#d02020' },
  { name: 'rider', node: 'rider', positions: box([0.03, 0, 0], 0.01), color: '#2040d0' },
];
const bytes = glb => Buffer.from(glb.buffer, glb.byteOffset, glb.byteLength);
const FILES = {
  'static.glb': bytes(writeGlb({ primitives }, { preset: 'export' })),
  'animated.glb': bytes(writeGlb({ primitives }, { preset: 'export', animations: [{
    name: 'slide', times: new Float32Array([0, 1]),
    channels: [{ node: 'rider', translation: new Float32Array([0, 0, 0, 0, 0.03, 0]) }],
  }] })),
  'broken.glb': Buffer.from('glTF is not what this is'),
};

// The harness renders its panes at a fixed CSS size; the spec draws them smaller, so a software
// GL (CI's SwiftShader) has fewer pixels to fill and a capture fewer to read.
const HARNESS_SIZE = '<style>#root > div { width: 800px !important; height: 500px !important; }</style>';

// One harness bundle, one server and one browser for the file; every test opens its own page.
// (A browser's first WebGL page pays for compiling the viewer's shaders; later pages reuse them.)
let temporary, server, browser;
before(async () => {
  temporary = await mkdtemp(join(tmpdir(), 'text-to-cad-glb-browser-'));
  await build({ entryPoints: [fileURLToPath(new URL('../harness/index.tsx', import.meta.url))], outfile: join(temporary, 'harness.js'), bundle: true, format: 'esm', platform: 'browser', conditions: ['production'], jsx: 'automatic', loader: { '.webp': 'dataurl', '.avif': 'dataurl', '.svg': 'dataurl' } });
  const bundle = await readFile(join(temporary, 'harness.js'));
  const css = await readFile(new URL('../../../dist/styles.css', import.meta.url));
  server = createServer((request, response) => {
    const url = new URL(request.url, 'http://test');
    const root = url.pathname.split('/')[1];
    const name = url.pathname.split('/').pop();
    if (url.pathname === '/harness.js') { response.setHeader('Content-Type', 'text/javascript'); response.end(bundle); }
    else if (url.pathname === '/styles.css') { response.setHeader('Content-Type', 'text/css'); response.end(css); }
    else if (url.pathname.endsWith('/__cad/catalog')) {
      response.setHeader('Content-Type', 'application/json');
      response.end(JSON.stringify({ entries: Object.entries(FILES).map(([file, data]) => (
        { kind: 'glb', file: `/models/${file}`, url: `/${file}`, hash: `${root}-${file}`, bytes: data.length })) }));
    } else if (url.pathname.endsWith('/__cad/server')) {
      response.setHeader('Content-Type', 'application/json'); response.end(JSON.stringify({ backend: 'cadgen' }));
    } else if (FILES[name]) { response.setHeader('Content-Type', 'model/gltf-binary'); response.end(FILES[name]); }
    else { response.setHeader('Content-Type', 'text/html'); response.end(`<!doctype html><html><head><title>Host title</title><link rel="stylesheet" href="/styles.css">${HARNESS_SIZE}</head><body><div id="root"></div><script type="module" src="/harness.js"></script></body></html>`); }
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  browser = await chromium.launch({ headless: true, args: (process.platform === 'darwin' && process.env.CAD_TEST_SWIFTSHADER !== '1')
    ? ['--use-angle=metal'] : ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
});
after(async () => {
  await browser?.close();
  if (server) await new Promise(resolve => server.close(resolve));
  if (temporary) await rm(temporary, { recursive: true, force: true });
});

// `record` opens the page as a previous session left its tab (`window.__cadTabRecord`).
async function open(t, file, { record = null } = {}) {
  const page = await browser.newPage({ viewport: { width: 800, height: 500 }, deviceScaleFactor: 1 });
  t.after(() => page.close());
  page.setDefaultTimeout(15000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    window.Worker = undefined;
    for (const name of ['localStorage', 'sessionStorage']) {
      Object.defineProperty(window, name, { get() { throw new Error(`Renderer accessed ${name}`); } });
    }
  });
  if (record) await page.addInitScript(stored => { window.__cadTabRecord = stored; }, record);
  await page.goto(`http://127.0.0.1:${server.address().port}/?file=${file}`);
  return { page, errors, pane: page.getByTestId('one') };
}

const ready = pane => pane.locator('[aria-busy="false"] > div > canvas').first().waitFor();

// A GLB has no interaction tools, so it has no strip; its view's controls end the navbar: Display, then Present.
const noTools = async (pane) => {
  assert.equal(await pane.getByRole('group', { name: 'Interaction tools' }).count(), 0, 'a GLB has no tools, and no strip');
  assert.deepEqual(await pane.locator('[data-viewer-navbar] [data-view-controls]').getByRole('button').evaluateAll(buttons => buttons.map(button => button.getAttribute('aria-label'))),
    ['Display mode: Solid', 'Present'], 'its view\'s controls end the navbar');
  for (const name of ['Orbit', 'Annotate', 'Draw', 'Select', 'Measure', 'Position']) {
    assert.equal(await pane.getByRole('button', { name, exact: true }).count(), 0, name);
  }
};
// The file sidebar's tiles (an animated GLB's Animation), and a section opened by its tile.
const tileNames = pane => pane.locator('[data-file-sidebar-tools] button').evaluateAll(buttons => buttons.map(button => button.getAttribute('aria-label')));
async function openSection(pane, name) {
  const tile = pane.getByRole('group', { name: 'File sidebar' }).getByRole('button', { name, exact: true });
  if (await tile.getAttribute('aria-pressed') !== 'true') await tile.click();
  await pane.locator(`[data-tool-panel][aria-label="${name}"]`).waitFor();
}
const animationSection = pane => pane.locator('[data-tool-panel][aria-label="Animation"]');
const displayButton = pane => pane.locator('[data-viewer-navbar] [data-view-controls]').getByRole('button', { name: /^Display mode: / });
// Display's settings: its per-mode settings page.
async function openDisplay(pane) {
  await displayButton(pane).click();
  await pane.page().getByRole('button', { name: 'Solid settings', exact: true }).click();
  await pane.page().locator('[data-display-settings="solid"]').waitFor();
}
// The display modes Display's menu offers, by id.
async function displayModes(pane) {
  await displayButton(pane).click();
  const menu = pane.page().locator('[role=dialog][aria-label="Display mode"]');
  const modes = await menu.locator('[data-display-mode-option]').evaluateAll(items => items.map(item => item.getAttribute('data-display-mode-option')));
  await pane.page().keyboard.press('Escape');
  await menu.waitFor({ state: 'detached' });
  return modes;
}
// Display's settings: a dropdown, portaled out of the viewer.
const displayPanel = pane => pane.page().locator('[data-display-settings="solid"]');
// The viewport's own pixels, without any chrome over them: what a host capture returns.
async function capture(page) {
  const encoded = await page.evaluate(async () => {
    const blob = await window.cadHarness.a.controller.capture();
    const data = new Uint8Array(await blob.arrayBuffer());
    let binary = ''; for (const byte of data) binary += String.fromCharCode(byte);
    return { type: blob.type, base64: btoa(binary) };
  });
  assert.equal(encoded.type, 'image/png');
  return PNG.sync.read(Buffer.from(encoded.base64, 'base64'));
}
// Colours covering a real share of the picture: the faces of the model, never antialiased rims.
function dominantColors(image, { minShare = 0.004 } = {}) {
  const counts = new Map();
  for (let offset = 0; offset < image.data.length; offset += 4) {
    const key = (image.data[offset] << 16) | (image.data[offset + 1] << 8) | image.data[offset + 2];
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  const total = image.width * image.height;
  // The corner pixel is the backdrop, whatever share of the picture the model takes. The
  // backdrop is a GRADIENT, so its neighbouring shades are backdrop too: matching only the
  // exact corner colour counts a few of them as "colours of the model" as soon as the model
  // leaves enough of the frame uncovered.
  const backdrop = [image.data[0], image.data[1], image.data[2]];
  const isBackdrop = key => Math.max(Math.abs((key >> 16) - backdrop[0]),
    Math.abs(((key >> 8) & 255) - backdrop[1]), Math.abs((key & 255) - backdrop[2])) <= 12;
  return [...counts].filter(([key, count]) => !isBackdrop(key) && count / total >= minShare)
    .sort((left, right) => right[1] - left[1]).map(([key]) => [key >> 16, (key >> 8) & 255, key & 255]);
}
const differingPixels = (left, right) => {
  assert.deepEqual([left.width, left.height], [right.width, right.height]);
  let count = 0;
  for (let offset = 0; offset < left.data.length; offset += 4) {
    if (left.data[offset] !== right.data[offset] || left.data[offset + 1] !== right.data[offset + 1] || left.data[offset + 2] !== right.data[offset + 2]) count += 1;
  }
  return count;
};
const settle = page => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
// Render is the Studio's: in by Present and out by Exit studio, both in the navbar, each once the view has
// come together under its cover.
async function studio(page, pane, enter) {
  await pane.getByRole('button', { name: enter ? 'Present' : 'Exit studio', exact: true }).click();
  await pane.getByRole('button', { name: enter ? 'Exit studio' : 'Present', exact: true }).waitFor();
  await page.waitForFunction(() => !document.querySelector('[data-testid="one"] [data-preview-cover]')
    && document.querySelector('[data-testid="one"] [aria-busy="false"]'));
}
// The capture once the view has come to rest: the same picture twice running.
async function stillCapture(page) {
  let last = await capture(page);
  for (let attempt = 0; attempt < 40; attempt += 1) {
    await settle(page);
    const next = await capture(page);
    if (differingPixels(last, next) === 0) return next;
    last = next;
  }
  assert.fail('the view never came to rest');
}
// A capture that shows `expected` exactly, once the change that leads to it has landed: a capture
// at another size is that change still landing (the navbar coming back after preview resizes the view).
async function captureMatching(page, expected, message) {
  let differing = -1;
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const next = await capture(page);
    differing = next.width === expected.width && next.height === expected.height ? differingPixels(expected, next) : Infinity;
    if (differing === 0) return;
    await settle(page);
  }
  assert.equal(differing, 0, message);
}
const display = (page, patch) => page.evaluate(next => window.cadHarness.a.controller.setDisplaySettings(next), patch);

test('a static GLB opens on its native scene with no tools: display settings, orbit, host commands and state all work', async (t) => {
  const { page, pane, errors } = await open(t, 'static.glb');
  await ready(pane);
  assert.deepEqual(errors, []);

  // Nothing of a GLB picks, measures, poses or is drawn on: the viewport simply
  // orbits, pans and zooms, with no tools over it.
  await noTools(pane);
  assert.equal(await pane.locator('[data-quick-edit]').count(), 0, 'Quick Edit is a STEP file\'s: a GLB has nothing to pick');
  assert.equal(await pane.locator('[data-view-controls]').getByRole('button', { name: 'Present', exact: true }).count(), 1,
    'a GLB is 3D: Present ends the navbar after Display');
  assert.equal(await pane.locator('[data-file-sidebar-tools]').count(), 0, 'a static GLB has nothing for the file sidebar');

  // A GLB has no panel of its own: its only settings are Display's, and Display is never
  // where a file opens, so the model has the room.
  assert.equal(await pane.locator('[data-tool-panel]').count(), 0, 'nothing in the tool stack');
  assert.deepEqual(await displayModes(pane), ['solid'], 'a GLB has no edges to draw: no mode made of them');
  await openDisplay(pane);
  const displayMenu = displayPanel(pane);
  assert.equal(await displayMenu.getByRole('tab').count(), 0, 'Display has no tabs inside it');
  assert.equal(await displayMenu.getByRole('combobox', { name: 'Mode', exact: true }).count(), 0, 'its menu chooses the mode');
  assert.equal(await displayMenu.getByRole('checkbox', { name: 'Flat shading', exact: true }).count(), 1);
  for (const section of ['Edges', 'Cross-section', 'Explode']) assert.equal(await displayMenu.getByRole('heading', { name: section, exact: true }).count(), 0, section);
  await displayButton(pane).click();
  await displayPanel(pane).waitFor({ state: 'detached' });
  // The column closing reaches the scene as a resize; let that frame land before comparing pictures.
  await page.waitForFunction(() => document.querySelector('[data-testid="one"] [aria-busy] > div > canvas').width >= 790);
  await settle(page);

  // The Studio draws the model in Render, on the studio's floor; leaving it takes the studio down.
  await studio(page, pane, true);
  await page.waitForFunction(() => window.__cadStage()?.studioGround);
  await studio(page, pane, false);
  await page.waitForFunction(() => !window.__cadStage()?.studioGround);

  // Surfaces, on the native scene. Flat is unlit: each part is one colour, its own.
  const shaded = await capture(page);
  assert.ok(dominantColors(shaded).length >= 4, 'shaded faces differ');
  await display(page, { modes: { solid: { flat: true } } });
  const original = dominantColors(await capture(page));
  assert.equal(original.length, 2, `two parts, two flat colours: ${JSON.stringify(original)}`);
  assert.ok(original.some(([r, g, b]) => r > 150 && g < 90 && b < 90), `the authored red: ${JSON.stringify(original)}`);
  assert.ok(original.some(([r, g, b]) => b > 150 && r < 90 && g < 130), `the authored blue: ${JSON.stringify(original)}`);
  await display(page, { modes: { solid: { flat: true, parts: { colorMode: 'single', color: '#00c040' } } } });
  const single = dominantColors(await capture(page));
  assert.equal(single.length, 1, `one colour for every part: ${JSON.stringify(single)}`);
  assert.ok(single[0][1] > single[0][0] + 60 && single[0][1] > single[0][2] + 60, 'and it is the chosen green');
  await display(page, { modes: { solid: { flat: true, parts: { colorMode: 'by-part' } } } });
  const palette = dominantColors(await capture(page));
  assert.equal(palette.length, 2, `a palette colour per part: ${JSON.stringify(palette)}`);
  assert.notDeepEqual(palette.sort(), original.sort());
  // Original colours and shaded surfaces restore the initial look.
  await display(page, { modes: { solid: { flat: false, parts: { colorMode: 'original' } } } });
  assert.equal(differingPixels(shaded, await capture(page)), 0, 'the look is fully reversible');

  // Dragging orbits.
  const before = await page.evaluate(() => window.cadHarness.a.controller.readState().camera);
  const canvas = await pane.locator('[aria-busy] > div > canvas').first().boundingBox();
  await page.mouse.move(canvas.x + canvas.width / 2, canvas.y + canvas.height / 2);
  await page.mouse.down();
  await page.mouse.move(canvas.x + canvas.width / 2 + 140, canvas.y + canvas.height / 2 + 40, { steps: 6 });
  await page.mouse.up();
  await page.waitForFunction(position => JSON.stringify(window.cadHarness.a.controller.readState().camera.position) !== position, JSON.stringify(before.position));

  // The drag's damping coasts for a moment; a host camera lands on a view at rest.
  // At rest: the live camera the same over several frames running.
  await page.waitForFunction(() => new Promise(resolve => {
    let still = 0, last = null;
    const step = () => {
      const position = JSON.stringify(window.__cadCamera().position);
      still = position === last ? still + 1 : 0;
      last = position;
      if (still >= 5) resolve(true); else requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }));

  // Host commands drive the mounted view, and one that makes no sense here fails loudly.
  const camera = await page.evaluate(async () => {
    const controller = window.cadHarness.a.controller;
    const current = controller.readState().camera;
    return (await controller.setCamera({ ...current, position: [90, -70, 60], target: [10, 5, 5], zoom: 1.3 })).camera;
  });
  assert.deepEqual(camera.position.map(Math.round), [90, -70, 60]);
  const state = await page.evaluate(() => window.cadHarness.a.controller.readState());
  assert.deepEqual([state.resource.path, state.revision, state.loading, state.selection], ['/models/static.glb', 'one-static.glb', false, []]);
  const declined = await page.evaluate(() => window.cadHarness.a.controller.select({ selectors: ['o1.f1'] }).then(() => '', error => error.message));
  assert.match(declined, /A GLB has nothing to select/);
  assert.match(await page.evaluate(() => window.cadHarness.a.controller.clearSelection().then(() => '', error => error.message)), /no selection to clear/);

  // A secondary press is the camera's: no menu of the viewer's, and the browser's own stays off the canvas.
  await page.evaluate(() => {
    window.nativeMenu = [];
    document.addEventListener('contextmenu', event => window.nativeMenu.push(event.defaultPrevented));
  });
  await page.mouse.click(canvas.x + canvas.width / 2, canvas.y + canvas.height / 2, { button: 'right' });
  // The press has reached the page, and a menu it opened would be up two frames on.
  await page.waitForFunction(() => window.nativeMenu.length > 0);
  await settle(page);
  assert.equal(await page.getByRole('menu').count(), 0);
  assert.deepEqual(await page.evaluate(() => window.nativeMenu), [true]);

  // A host's capture request goes through the prompt port: the view, of the whole file.
  await page.evaluate(() => window.cadHarness.capture());
  await page.waitForFunction(() => window.cadHarness.captures.length === 1);
  const captured = await page.evaluate(() => window.cadHarness.captures[0]);
  assert.deepEqual([captured.file, captured.type, captured.references.length], ['/models/static.glb', 'image/png', 1]);
  assert.deepEqual(captured.references[0].target, { kind: 'whole-resource' });

  // Camera and Display settings belong to this file under this renderer's id, and survive a remount.
  await display(page, { modes: { solid: { parts: { colorMode: 'single', color: '#00c040' }, grid: { enabled: false } } } });
  await page.waitForFunction(() => window.cadHarness.state.renderers?.[JSON.stringify(['/models/static.glb', 'glb'])]?.display?.modes?.solid?.parts?.colorMode === 'single');
  const stored = await page.evaluate(() => window.cadHarness.state.renderers);
  assert.deepEqual(Object.keys(stored), [JSON.stringify(['/models/static.glb', 'glb'])], 'keyed by [absolute path, renderer id]');
  const left = await page.evaluate(() => window.cadHarness.a.controller.readState());
  await page.evaluate(() => window.cadHarness.mounted(false));
  await pane.locator('canvas').first().waitFor({ state: 'detached' });
  await page.evaluate(() => window.cadHarness.mounted(true));
  await ready(pane);
  await page.waitForFunction(() => window.cadHarness.a.controller?.readState().loading === false);
  const reopened = await page.evaluate(() => window.cadHarness.a.controller.readState());
  // Within a thousandth: the camera read when leaving can still be coasting its last hundredth.
  assert.ok(reopened.camera.position.every((value, axis) => Math.abs(value - left.camera.position[axis]) <= 1e-3 * Math.max(1, Math.abs(left.camera.position[axis]))),
    `reopening restores the camera the file was left at: ${JSON.stringify([reopened.camera.position, left.camera.position])}`);
  assert.ok(Math.abs(reopened.camera.zoom - left.camera.zoom) < 1e-6);
  assert.deepEqual([reopened.display.modes.solid.parts.colorMode, reopened.display.modes.solid.parts.color, reopened.display.modes.solid.grid.enabled], ['single', '#00c040', false]);

  assert.deepEqual(errors, []);
});

test('an animated GLB opens at rest with no tools, its clips the file sidebar\'s Animation section; in the Studio they play in Render without moving the camera or the floor, Orbit turns the camera, and leaving puts the model back at rest', async (t) => {
  const { page, pane, errors } = await open(t, 'animated.glb');
  await ready(pane);
  await noTools(pane);
  // Its routines are the sidebar's: one tile, its section closed as the file opens.
  assert.deepEqual(await tileNames(pane), ['Animation']);
  assert.equal(await animationSection(pane).isVisible(), false, 'no section open as the file opens');
  await page.waitForFunction(() => window.cadHarness.a.controller?.readState().loading === false);
  const toolsRest = await stillCapture(page);

  await studio(page, pane, true);
  // The Studio's top-left: the sidebar's Animation alone; the model in Render, on its floor.
  assert.deepEqual(await tileNames(pane), ['Animation']);
  await openSection(pane, 'Animation');
  const section = animationSection(pane);
  await section.getByRole('button', { name: 'Play animation', exact: true }).waitFor();
  await page.waitForFunction(() => window.__cadStage()?.studioGround);
  const rest = await stillCapture(page);
  // It opens paused, at the clip's start.
  assert.equal(await section.getByRole('button', { name: 'Pause animation', exact: true }).count(), 0, 'it opens paused');
  const time = section.getByRole('slider', { name: 'Animation time' });
  assert.equal(Number(await time.getAttribute('aria-valuenow')), 0);
  // Scrubbing away and back is the rest pose again.
  await time.focus();
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('ArrowLeft');
  await captureMatching(page, rest, 'a clip scrubbed back to 0 is the rest pose');

  // Playing moves the model, never the floor; pausing leaves it where it stopped.
  const floor = await page.evaluate(() => window.__cadStage().studioGround);
  await section.getByRole('button', { name: 'Play animation', exact: true }).click();
  await page.waitForFunction(() => Number(document.querySelector('[data-testid="one"] [data-cad-file-sidebar] [role="slider"][aria-label="Animation time"]')?.getAttribute('aria-valuenow')) > 0.25);
  await section.getByRole('button', { name: 'Pause animation', exact: true }).click();
  assert.ok(differingPixels(rest, await capture(page)) > 200, 'the rider moved');
  assert.deepEqual(await page.evaluate(() => window.__cadStage().studioGround), floor);

  // Orbit, in the Studio's settings, turns the camera; turned off again, it stops.
  await pane.getByRole('button', { name: 'Studio settings', exact: true }).click();
  const orbit = pane.getByRole('region', { name: 'Studio settings' }).getByRole('checkbox', { name: 'Orbit', exact: true });
  assert.equal(await orbit.getAttribute('aria-checked'), 'false', 'the Studio opens still');
  const held = await page.evaluate(() => window.__cadCamera().position);
  await orbit.click();
  await page.waitForFunction(start => window.__cadCamera().position.some((value, axis) => Math.abs(value - start[axis]) > 1e-6), held);
  await orbit.click();

  // Leaving puts the clip back at rest, in the tools view's own camera and mode.
  await studio(page, pane, false);
  await page.waitForFunction(() => !window.__cadStage()?.studioGround);
  await captureMatching(page, toolsRest, 'the tools view is at rest again');
  assert.deepEqual(errors, []);
});

test("an animated GLB's Speed and Loop are the file's: chosen in its Animation section, the Studio's routine plays with them, and every visit to the Studio opens still", async (t) => {
  const { page, pane, errors } = await open(t, 'animated.glb');
  await ready(pane);
  const settingsButton = animationSection(pane).getByRole('button', { name: 'Playback settings' });
  const menu = page.getByRole('menu', { name: 'Playback settings', exact: true });
  const settings = async () => {
    await settingsButton.click();
    const read = [await menu.getByRole('menuitem', { name: /^Animation speed: / }).getAttribute('aria-label'),
      await menu.getByRole('menuitemcheckbox', { name: 'Loop', exact: true }).getAttribute('aria-checked')];
    await page.keyboard.press('Escape');
    await menu.waitFor({ state: 'detached' });
    return read;
  };
  // A Speed chosen and Loop turned over, by keyboard (an item easing in never reads as stable under the pointer).
  const choose = async speed => {
    await settingsButton.click();
    await menu.getByRole('menuitem', { name: /^Animation speed: / }).click();
    await page.getByRole('menuitemradio', { name: `${speed}×`, exact: true }).press('Enter');
    await menu.waitFor({ state: 'detached' });
    await settingsButton.click();
    await menu.getByRole('menuitemcheckbox', { name: 'Loop', exact: true }).press('Enter');
    await page.keyboard.press('Escape');
    await menu.waitFor({ state: 'detached' });
  };
  const orbit = pane.getByRole('region', { name: 'Studio settings' }).getByRole('checkbox', { name: 'Orbit', exact: true });
  await openSection(pane, 'Animation');
  assert.deepEqual(await settings(), ['Animation speed: 1×', 'true'], 'the clip\'s own');
  await choose(0.5);
  assert.deepEqual(await settings(), ['Animation speed: 0.5×', 'false']);
  await studio(page, pane, true);
  await openSection(pane, 'Animation');
  assert.deepEqual(await settings(), ['Animation speed: 0.5×', 'false'], 'the file\'s, in the Studio too');
  await pane.getByRole('button', { name: 'Studio settings', exact: true }).click();
  await orbit.click();
  assert.equal(await orbit.getAttribute('aria-checked'), 'true');
  await studio(page, pane, false);
  await studio(page, pane, true);
  await pane.getByRole('button', { name: 'Studio settings', exact: true }).click();
  assert.equal(await orbit.getAttribute('aria-checked'), 'false', 'still again');
  await openSection(pane, 'Animation');
  assert.deepEqual(await settings(), ['Animation speed: 0.5×', 'false'], 'the file\'s, kept');
  await studio(page, pane, false);
  assert.deepEqual(errors, []);
});

test('a corrupt GLB raises the viewer\'s load alert, with reload and details', async (t) => {
  const { page, pane } = await open(t, 'broken.glb');
  const alert = pane.getByRole('alert');
  await alert.waitFor();
  assert.match(await alert.innerText(), /Couldn’t load the model/);
  assert.match(await alert.innerText(), /broken\.glb/);
  // Nothing on screen is the file's to work on: the card alone, with no tools or view actions.
  for (const name of [/^Display mode: /, 'Present']) {
    assert.equal(await pane.getByRole('button', { name, exact: true }).count(), 0, `no ${name} over a failed load`);
  }
  void page;
});
