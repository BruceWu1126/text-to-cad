import { test } from 'node:test';
import { runDrawScenario } from '../../../harness/drawScenario.mjs';
import { serveStepHarness } from '../../../harness/stepScenario.mjs';

// Draw under the frame that ships it: the STEP renderer, over the committed
// two-part fixture. It is a kit tool, so the scenario itself
// (`harness/drawScenario.mjs`) knows nothing about STEP.

test('Draw puts ink into the model that orbits with it, undoes, redoes, recolours and clears, stays when Draw is left — where Select picks, haloes and deletes it — and goes when the model updates', async (t) => {
  const harness = await serveStepHarness(t);
  await runDrawScenario(await harness.open());
});
