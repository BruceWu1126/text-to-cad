import assert from "node:assert/strict";
import test from "node:test";
import { previewCoverStep } from "./previewCover.js";

test("Preview reveals the first raster picture of the published model", () => {
  assert.equal(previewCoverStep({ presented: true, loading: false }), "lift");
  assert.equal(previewCoverStep({ presented: false, loading: false }), "wait");
  assert.equal(previewCoverStep({ presented: true, loading: true }), "wait");
});
