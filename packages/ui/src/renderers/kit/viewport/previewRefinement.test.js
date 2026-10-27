import { setImmediate } from "node:timers";
import assert from "node:assert/strict";
import test from "node:test";
import { createPreviewRefinement, renderPreviewFrame } from "./previewRefinement.js";
const ready = () => ({ hasVisibleModel: true, environmentReady: true });
const frame = { moving: false, still: true, sceneChanged: false };
const flush = () => new Promise(resolve => setImmediate(resolve));

test("Preview waits for tracing and never draws a raster picture", async () => {
  let requests = 0, disposed = 0, renders = 0;
  const status = [], canvas = {};
  const refinement = createPreviewRefinement(ready(), { requestRender: () => requests++, onStatus: value => status.push(value),
    load: async () => ({ createProgressivePreview: () => ({ visible: true, captureCanvas: canvas, progress: { state: "ready" },
      render() { renders++; return { visible: true, refining: false }; }, invalidate() {}, dispose() { disposed++; } }) }) });
  assert.equal(renderPreviewFrame(refinement, null, null, null, frame).visible, false);
  assert.deepEqual(status, [{ state: "loading", reason: "", phase: "preparing" }]);
  await flush(); assert.equal(requests, 1);
  assert.equal(renderPreviewFrame(refinement, null, null, null, { ...frame, moving: true }).visible, true);
  assert.equal(renders, 1); assert.equal(refinement.captureCanvas, canvas);
  assert.deepEqual(status.at(-1), { state: "ready", reason: "", phase: "preparing" });
  refinement.dispose(); refinement.dispose(); assert.equal(disposed, 1);
});

test("a late import is ignored after exit", async () => {
  let resolveLoad, constructions = 0;
  const refinement = createPreviewRefinement(ready(), { requestRender() {}, load: () => new Promise(resolve => { resolveLoad = resolve; }) });
  refinement.render(null, null, null, frame); await flush(); refinement.dispose();
  resolveLoad({ createProgressivePreview: () => { constructions++; } }); await flush();
  assert.equal(constructions, 0);
});

test("load or device failures surface once and never invoke raster", async () => {
  for (const failOnLoad of [true, false]) {
    let loads = 0, disposed = 0; const status = [];
    const refinement = createPreviewRefinement(ready(), { requestRender() {}, onStatus: value => status.push(value), load: async () => {
      loads++; if (failOnLoad) throw new Error("unavailable");
      return { createProgressivePreview: () => ({ render() { throw new Error("device lost"); }, dispose() { disposed++; } }) };
    } });
    refinement.render(null, null, null, frame); await flush(); refinement.render(null, null, null, frame);
    assert.equal(refinement.progress.state, "fallback");
    assert.equal(status.at(-1).state, "failed");
    assert.match(status.at(-1).reason, failOnLoad ? /unavailable/ : /device lost/);
    assert.equal(loads, 1); assert.equal(disposed, failOnLoad ? 0 : 1);
  }
});


test("a stalled renderer import fails once, ignores late completion and clears its deadline on disposal", async () => {
  let resolveLoad, expire, clears = 0, constructions = 0, requests = 0;
  const status = [];
  const refinement = createPreviewRefinement(ready(), { requestRender: () => requests++, onStatus: value => status.push(value),
    setTimer: callback => { expire = callback; return 1; }, clearTimer: () => { clears++; },
    load: () => new Promise(resolve => { resolveLoad = resolve; }) });
  refinement.render(null, null, null, frame); await flush();
  expire(); assert.equal(refinement.progress.state, "fallback"); assert.match(status.at(-1).reason, /did not load/);
  resolveLoad({ createProgressivePreview: () => { constructions++; } }); await flush();
  refinement.render(null, null, null, frame); refinement.dispose();
  assert.equal(constructions, 0); assert.equal(status.filter(value => value.state === "failed").length, 1);
  assert.equal(requests, 1); assert.equal(clears, 1);
});

test("disposing a pending renderer import cancels its deadline", async () => {
  let clears = 0;
  const refinement = createPreviewRefinement(ready(), { requestRender() {}, setTimer: () => 1, clearTimer: () => { clears++; }, load: () => new Promise(() => {}) });
  refinement.render(null, null, null, frame); await flush(); refinement.dispose();
  assert.equal(clears, 1);
});
