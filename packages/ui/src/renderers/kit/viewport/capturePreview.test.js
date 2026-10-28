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


test("a retained Preview capture preserves shape across a portrait viewport resize", async () => {
  const overlay = { width: 400, height: 300 }, draws = [];
  const canvas = { getContext: () => ({ drawImage(...args) { draws.push(args); } }),
    toBlob: callback => callback(new Blob(["pixels"], { type: "image/png" })) };
  const runtime = { renderer: { domElement: { width: 842, height: 927 } },
    progressivePreview: { captureCanvas: overlay }, drawCurrentFrame() {} };
  await capturePreview(runtime, { createCanvas: () => canvas });
  const [source, x, y, width, height] = draws[0];
  assert.equal(source, overlay);
  assert.equal(width / overlay.width, height / overlay.height, "horizontal and vertical scale stay equal");
  assert.equal(x + width / 2, canvas.width / 2);
  assert.equal(y + height / 2, canvas.height / 2);
  assert.ok(x >= 0 && y >= 0 && width <= canvas.width && height <= canvas.height);
});
