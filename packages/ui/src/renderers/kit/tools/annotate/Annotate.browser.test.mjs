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
