import assert from 'node:assert/strict';
import { PNG } from 'pngjs';

// The Draw tool end to end in a real browser: the viewer's own ink, drawn over the real viewport
// and projected into the real model. Unit tests cover the session, the ink geometry and the
// projection; this is the flow a person actually uses.
//
// Draw is a KIT tool, so this body takes an already-open page and knows nothing about the frame
// that mounted it: one scenario, run under each frame that offers Draw. Today that is STEP
// (`serveStepHarness`).
//
// Ink is read off screenshots of the viewport, by colour, with the chrome hidden -- and with Draw's
// own preview canvas hidden, so what is counted is the ink the scene draws in WebGL, in the model.

// The chrome over the viewport (the strip and its panels, the cube and its axes, menus) and the
// stroke preview: none of it is ink in the model.
const HIDDEN = '[data-cad-tool-groups], [aria-label="View cube"], [data-slot=popover-content], '
  + '[data-cad-drawing-overlay] canvas { visibility: hidden !important; }';
// Neon red (#ff2d55, the pen's default) and neon green (#39ff14). The fixture's parts are blue and
// orange; orange is told from the red by its blue channel.
const RED = (r, g, b) => r > 200 && g < 100 && b > 50 && b < 150;
const GREEN = (r, g, b) => g > 200 && r < 140 && b < 100;
const NONE = 10;

/**
 * @param {{ page: import('playwright').Page, pane: import('playwright').Locator, errors: string[],
 *   update?: () => Promise<void> }} view
 *   A page the harness has opened on a file whose renderer offers Draw; `update`, where the frame's
 *   harness has one, saves the file again and settles once the new revision is on screen.
 */
export async function runDrawScenario({ page, pane, errors, update }) {
  const viewport = pane.locator('[aria-busy] > div > canvas').first();
  const panel = pane.locator('[data-tool-panel][aria-label="Drawing controls"]');
  const strip = pane.getByRole('group', { name: 'Interaction tools' });
  const settings = panel.getByRole('group', { name: 'Drawing settings' });
  const tool = name => strip.getByRole('button', { name, exact: true });
  const setting = name => settings.getByRole('button', { name, exact: true });
  // A shape from the Shapes' menu, which takes Draw up on it.
  const shape = async name => {
    await strip.getByRole('button', { name: /^Shape: / }).click();
    await page.getByRole('menuitemradio', { name, exact: true }).click();
  };
  const names = group => group.getByRole('button').evaluateAll(buttons => buttons.map(button => button.getAttribute('aria-label')));
  const position = () => page.evaluate(() => window.cadHarness.a.controller.readState().camera.position);
  // How far the camera has moved since `from`, in model units (a readback differs in its last bits).
  const moved = async from => Math.hypot(...(await position()).map((value, index) => value - from[index]));

  // The ink on screen: how many pixels are neon red and neon green, and where the red sits.
  const ink = async () => {
    const { width, height, data } = PNG.sync.read(await viewport.screenshot({ style: HIDDEN }));
    const seen = { red: 0, green: 0, x: 0, y: 0 };
    for (let y = 0; y < height; y += 1) for (let x = 0; x < width; x += 1) {
      const index = (y * width + x) * 4, [r, g, b] = [data[index], data[index + 1], data[index + 2]];
      if (RED(r, g, b)) { seen.red += 1; seen.x += x; seen.y += y; } else if (GREEN(r, g, b)) seen.green += 1;
    }
    if (seen.red) { seen.x /= seen.red; seen.y /= seen.red; }
    return seen;
  };
  // The view reaches what `wanted` asks of its ink: screenshots until it does, failing with the last.
  const until = async (what, wanted, timeout = 10000) => {
    const deadline = Date.now() + timeout;
    for (;;) {
      const seen = await ink();
      if (wanted(seen)) return seen;
      if (Date.now() > deadline) assert.fail(`${what}: ${JSON.stringify(seen)}`);
    }
  };
  const drag = async (from, to) => {
    await page.mouse.move(...from); await page.mouse.down(); await page.mouse.move(...to, { steps: 10 }); await page.mouse.up();
  };

  const draw = tool('Pencil');
  await viewport.waitFor();
  await page.waitForFunction(() => window.cadHarness.a.controller?.readState().loading === false);
  await until('no ink before Draw', seen => seen.red < NONE && seen.green < NONE);

  // The Pencil opens on the pen; Draw's panel holds what is drawn next.
  await draw.click();
  const surface = pane.locator('[data-cad-drawing-overlay]');
  await surface.waitFor();
  assert.equal(await draw.getAttribute('aria-pressed'), 'true', 'the Pencil is the tool');
  assert.deepEqual(await names(settings), ['Color', 'Stroke width', 'Line style', 'Placement']);
  const copy = panel.getByRole('button', { name: 'Copy drawing', exact: true });
  assert.equal(await copy.count(), 0, 'nothing to copy before there is ink');
  // Across the middle of the view, clear of the tool stack at its left and Quick Edit at its right.
  const box = await surface.boundingBox();
  const at = (x, y) => [box.x + box.width / 2 + x, box.y + box.height / 2 + y];

  // A pen stroke across the model: red ink in the model, there once the pen lifts.
  const before = await position();
  await drag(at(-80, 10), at(60, 40));
  const stroke = await until('the stroke is in the model', seen => seen.red > 100);
  await copy.waitFor();
  assert.ok(await moved(before) < 1e-6, 'drawing never moves the camera');

  // Orbited — a right drag over the drawing is the view's — the view keeps the ink where it was
  // drawn on the model: still red, somewhere else.
  await page.mouse.move(...at(150, 150));
  await page.mouse.down({ button: 'right' });
  await page.mouse.move(...at(250, 190), { steps: 8 });
  await page.mouse.up({ button: 'right' });
  // The orbit eases out: the camera is at rest once two readings 100 ms apart agree.
  await page.waitForFunction(() => {
    const now = window.cadHarness.a.controller.readState().camera.position.join();
    const resting = window.__drawCamera === now;
    window.__drawCamera = now;
    return resting;
  }, null, { polling: 100 });
  const orbited = await until('the ink orbits with the model',
    seen => seen.red > 50 && Math.hypot(seen.x - stroke.x, seen.y - stroke.y) > 20);
  assert.ok(await moved(before) > 1e-3, 'a right drag orbited the camera');

  // Undo (the viewer's, whatever the tool) takes the stroke away, Redo brings it back where the view
  // now has it.
  await page.keyboard.press('ControlOrMeta+KeyZ');
  await until('Undo removes the stroke', seen => seen.red < NONE);
  await page.keyboard.press('ControlOrMeta+Shift+KeyZ');
  await until('Redo brings it back', seen => seen.red > 50 && Math.hypot(seen.x - orbited.x, seen.y - orbited.y) < 10);

  // Another colour for what is drawn next: a green rectangle beside the red stroke. Clear takes it all.
  await setting('Color').click();
  await page.getByRole('menuitemradio', { name: 'Neon green', exact: true }).click();
  await shape('Rectangle');
  await drag(at(-60, -120), at(60, -40));
  await until('a green rectangle beside the red', seen => seen.green > 100 && seen.red > 50);
  await panel.getByRole('button', { name: 'Clear drawing', exact: true }).click();
  await until('Clear drawing removes all ink', seen => seen.red < NONE && seen.green < NONE);
  await copy.waitFor({ state: 'detached' });

  // Left with ink drawn, Draw's surface and tools go; the ink stays in the model, under the
  // Drawing panel's heading.
  await draw.click();
  await drag(at(-80, 10), at(60, 40));
  await shape('Rectangle');
  await drag(at(-60, -120), at(60, -40));
  const drawn = await until('the green stroke and rectangle are in the model', seen => seen.green > 200);
  await pane.getByRole('group', { name: 'Interaction tools' }).getByRole('button', { name: 'Select', exact: true }).click();
  await surface.waitFor({ state: 'detached' });
  assert.equal(await panel.isVisible(), true, 'the Drawing heading stays with the ink');
  await until('the ink stays after Draw', seen => seen.green > 100);

  // Select picks a mark of the drawing as it picks a part, and Delete deletes what it picked: the
  // stroke, not the rectangle beside it.
  await page.mouse.click(...at(-10, 25));
  await page.keyboard.press('Delete');
  await until('Delete deletes the stroke and keeps the rectangle', seen => seen.green > 100 && seen.green < drawn.green - 50);

  // The rectangle picked, its bar: Fill fills it in its colour, Delete deletes it, fill and all.
  await page.mouse.click(...at(0, -120));
  const bar = pane.getByRole('toolbar', { name: 'Selected drawing' });
  await bar.waitFor();
  await bar.getByRole('button', { name: 'Fill', exact: true }).click();
  assert.equal(await bar.getByRole('button', { name: 'Fill', exact: true }).getAttribute('aria-pressed'), 'true', 'Fill is lit once filled');
  await until('the rectangle is still drawn, filled', seen => seen.green > 100);
  await bar.getByRole('button', { name: 'Delete', exact: true }).click();
  await until('Delete on the bar deletes the rectangle and its fill', seen => seen.green < NONE);
  await bar.waitFor({ state: 'detached' });
  await page.keyboard.press('ControlOrMeta+KeyZ');
  await until('Undo brings them back', seen => seen.green > 100);

  // An update of the model discards ink drawn over the revision before it, and the panel with it.
  if (update) {
    await update();
    await until('the update discards the ink', seen => seen.green < NONE && seen.red < NONE);
    await panel.waitFor({ state: 'detached' });
  }
  assert.deepEqual(errors, []);
}
