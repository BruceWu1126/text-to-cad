import assert from "node:assert/strict";
import test from "node:test";
import { capturePreview } from "./capturePreview.js";

test("Preview captures the displayed trace or raster immediately after its live draw", async () => {
  for (const traced of [true, false]) {
    const events = [];
    const raster = { width: 400, height: 200 };
    const overlay = { width: 200, height: 100 };
    const context = { fillStyle: "", fillRect() { events.push("background"); }, drawImage(...args) { events.push(args); } };
    const png = new Blob(["pixels"], { type: "image/png" });
    const canvas = { getContext: () => context, toBlob: callback => callback(png) };
    const runtime = {
      renderer: { drawingCanvas: raster, domElement: { width: 400, height: 200 } },
      progressivePreview: { visible: traced, captureCanvas: traced ? overlay : null },
      drawCurrentFrame() { events.push("draw"); }
    };
    assert.equal(await capturePreview(runtime, { backgroundColor: "#fff", createCanvas: () => canvas }), png);
    assert.deepEqual(events, ["draw", "background", [traced ? overlay : raster, 0, 0, 400, 200]]);
    assert.equal(canvas.width, 400); assert.equal(canvas.height, 200);
  }
});


test("a tracing readback failure redraws raster before capture", async () => {
  const order = [], raster = { width: 100, height: 50 };
  const canvas = { getContext: () => ({ drawImage(source) { order.push(source); } }), toBlob: callback => callback(new Blob()) };
  const runtime = {
    renderer: { domElement: raster },
    progressivePreview: { visible: true, get captureCanvas() { order.push("readback"); return null; } },
    drawCurrentFrame() { order.push("draw"); }
  };
  await capturePreview(runtime, { createCanvas: () => canvas });
  assert.deepEqual(order, ["draw", "readback", "draw", raster]);
});
