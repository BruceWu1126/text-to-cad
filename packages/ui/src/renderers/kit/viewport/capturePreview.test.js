import assert from "node:assert/strict";
import test from "node:test";
import { capturePreview } from "./capturePreview.js";

test("Preview captures only the displayed traced picture", async () => {
  const events = [], overlay = { width: 200, height: 100 };
  const context = { fillRect() {}, drawImage(...args) { events.push(args); } };
  const png = new Blob(["pixels"], { type: "image/png" });
  const canvas = { getContext: () => context, toBlob: callback => callback(png) };
  const runtime = { renderer: { domElement: { width: 400, height: 200 } }, progressivePreview: { captureCanvas: overlay }, drawCurrentFrame() {} };
  assert.equal(await capturePreview(runtime, { createCanvas: () => canvas }), png);
  assert.deepEqual(events, [[overlay, 0, 0, 400, 200]]);
});

test("Preview refuses capture while the traced picture is unavailable", () => {
  const runtime = { renderer: { domElement: {} }, progressivePreview: { captureCanvas: null }, drawCurrentFrame() {} };
  assert.throws(() => capturePreview(runtime), /not ready/);
});
