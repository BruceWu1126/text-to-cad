import assert from 'node:assert/strict';
import { test } from 'node:test';
import { runAnnotateScenario } from '../../../harness/annotateScenario.mjs';
import { serveStepHarness } from '../../../harness/stepScenario.mjs';

// Annotate under the frame that ships it: the STEP renderer, over the committed
// two-part fixture. It is a kit tool, so the scenario itself
// (`harness/annotateScenario.mjs`) knows nothing about STEP.

test('Annotate puts ink into the model that orbits with it, undoes, redoes, recolours and clears, stays when Annotate is left — where Select picks, haloes and deletes it — and goes when the model updates', async (t) => {
  const harness = await serveStepHarness(t);
  await runAnnotateScenario(await harness.open());
});


test('narrow views keep Annotate, Quick Edit and the resizable sidebar in separate reachable areas', async (t) => {
  const harness = await serveStepHarness(t);
  const { page, pane, errors } = await harness.open();
  await page.waitForFunction(() => window.cadHarness.a.controller?.readState().loading === false);
  await page.evaluate(() => window.cadHarness.a.controller.select({ selectors: ['o1.1'] }));
  const quickEdit = pane.getByRole('region', { name: 'Quick Edit', exact: true });
  await quickEdit.getByRole('textbox').fill('Keep this note while annotating.');
  await pane.getByRole('button', { name: 'Annotate', exact: true }).click();
  const controls = pane.getByRole('toolbar', { name: 'Annotation controls', exact: true });
  for (const width of [900, 768, 640, 512, 390]) {
    await page.setViewportSize({ width, height: 600 });
    await page.waitForFunction(() => {
      const rect = selector => document.querySelector(selector)?.getBoundingClientRect();
      const header = rect('[data-cad-top-controls]'), right = rect('[data-quick-edit-box]'), left = rect('[data-cad-file-sidebar]');
      return header && right && left && right.top >= header.bottom && left.top >= header.bottom && left.right + 7 <= right.left;
    });
    const layout = await controls.evaluate(row => {
      const buttons = [...row.querySelectorAll('button')].map(button => button.getBoundingClientRect());
      return { count: buttons.length, top: buttons.map(box => box.top), x: buttons.map(box => box.left) };
    });
    assert.equal(layout.count, 14);
    assert.ok(layout.top.slice(0, 7).every(y => y === layout.top[0]), `${width}px: seven tools on the first row`);
    assert.ok(layout.top.slice(7).every(y => y === layout.top[7]) && layout.top[7] > layout.top[0], `${width}px: seven settings/actions on the second row`);
    assert.deepEqual(layout.x.slice(0, 7), layout.x.slice(7), `${width}px: the two rows align`);
    // A browser click checks hit-testing as well as the box geometry: no higher layer covers it.
    await controls.getByRole('button', { name: 'Line', exact: true }).click();
    await controls.getByRole('button', { name: 'Pen', exact: true }).click();
    await quickEdit.getByRole('textbox').click();
    await pane.getByRole('button', { name: 'Hide file sidebar', exact: true }).click();
    await pane.getByRole('button', { name: 'Show file sidebar', exact: true }).click();
    await pane.getByRole('separator', { name: 'Resize file sidebar', exact: true }).press('End');
    await page.waitForFunction(() => {
      const left = document.querySelector('[data-cad-file-sidebar]').getBoundingClientRect();
      const right = document.querySelector('[data-quick-edit-box]').getBoundingClientRect();
      return left.right + 7 <= right.left;
    });
  }
  assert.deepEqual(errors, []);
});
