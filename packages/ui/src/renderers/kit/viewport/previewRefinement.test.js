import { setImmediate } from "node:timers";
import assert from "node:assert/strict";
import test from "node:test";
import { createPreviewRefinement, renderPreviewFrame } from "./previewRefinement.js";

const rest = { moving: false, still: true, sceneChanged: false };
const ready = () => ({ hasVisibleModel: true, environmentReady: true });
const flush = () => new Promise(resolve => setImmediate(resolve));

test("Preview presents raster and motion before loading optional refinement", async () => {
  let loads = 0, requests = 0, renders = 0, disposed = 0;
  const invalidations = [];
  const traced = { width: 100, height: 50 };
  const refinement = createPreviewRefinement(ready(), {
    requestRender: () => requests++,
    load: async () => {
      loads++;
      return { createProgressivePreview: (_runtime, { requestRender }) => ({
        visible: true,
        captureCanvas: traced,
        render: () => { renders++; requestRender(); return { refining: true, visible: true }; },
        invalidate: options => invalidations.push(options),
        dispose: () => disposed++
      }) };
    }
  });
  refinement.render(null, null, null, { ...rest, still: false, sceneChanged: true });
  refinement.render(null, null, null, { ...rest, moving: true });
  await flush();
  assert.equal(loads, 0);
  assert.deepEqual(refinement.render(null, null, null, rest), { refining: false, visible: false });
  await flush();
  assert.equal(loads, 1);
  assert.equal(requests, 1, "load completion asks for a frame without changing scene state");
  assert.deepEqual(refinement.render(null, null, null, rest), { refining: true, visible: true });
  assert.equal(renders, 1);
  assert.equal(refinement.captureCanvas, traced);
  refinement.invalidate({ sceneChanged: false });
  assert.deepEqual(invalidations, [{ sceneChanged: false }]);
  refinement.dispose(); refinement.dispose();
  assert.equal(disposed, 1);
  assert.equal(refinement.captureCanvas, null);
});

test("closing Preview discards a late tracer import", async () => {
  let resolveLoad, constructions = 0, requests = 0;
  const refinement = createPreviewRefinement(ready(), {
    requestRender: () => requests++,
    load: () => new Promise(resolve => { resolveLoad = resolve; })
  });
  refinement.render(null, null, null, rest);
  await flush();
  refinement.dispose();
  resolveLoad({ createProgressivePreview: () => { constructions++; } });
  await flush();
  assert.equal(constructions, 0);
  assert.equal(requests, 0);
});

test("unavailable and failed refinement retain raster without retry loops", async () => {
  for (const failOnLoad of [true, false]) {
    let loads = 0, disposed = 0;
    const refinement = createPreviewRefinement(ready(), {
      requestRender() {},
      load: async () => {
        loads++;
        if (failOnLoad) throw new Error("unavailable");
        return { createProgressivePreview: () => ({ render() { throw new Error("device lost"); }, dispose() { disposed++; } }) };
      }
    });
    refinement.render(null, null, null, rest);
    await flush();
    assert.equal(refinement.render(null, null, null, rest).visible, false);
    assert.equal(refinement.render(null, null, null, rest).refining, false);
    assert.equal(refinement.progress.state, "fallback");
    assert.equal(loads, 1);
    assert.equal(disposed, failOnLoad ? 0 : 1);
  }
});


test("a visible trace refines alone and a fallback redraws raster in the same frame", () => {
  const calls = [];
  let visible = true;
  const refinement = { visible: true, get captureCanvas() { throw new Error("a frame must not capture"); }, render() { calls.push("trace"); return { visible, refining: visible }; } };
  const raster = () => { calls.push("raster"); return { refining: false }; };
  assert.equal(renderPreviewFrame(refinement, null, null, null, rest, raster).refining, true);
  assert.deepEqual(calls, ["trace"]);
  calls.length = 0; visible = false;
  renderPreviewFrame(refinement, null, null, null, rest, raster);
  assert.deepEqual(calls, ["trace", "raster"]);
  calls.length = 0;
  renderPreviewFrame(refinement, null, null, null, { ...rest, moving: true }, raster);
  assert.deepEqual(calls, ["raster", "trace"]);
});


test("capture failure releases tracing and leaves a raster capture available", async () => {
  let disposed = 0;
  const refinement = createPreviewRefinement(ready(), {
    requestRender() {},
    load: async () => ({ createProgressivePreview: () => ({
      visible: true,
      get captureCanvas() { throw new Error("device lost during capture"); },
      dispose() { disposed++; }
    }) })
  });
  refinement.render(null, null, null, rest);
  await flush();
  assert.equal(refinement.visible, true);
  assert.equal(refinement.captureCanvas, null);
  assert.equal(refinement.visible, false);
  assert.equal(refinement.progress.state, "fallback");
  assert.equal(disposed, 1);
});
