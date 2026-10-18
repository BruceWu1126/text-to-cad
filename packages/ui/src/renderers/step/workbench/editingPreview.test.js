import assert from "node:assert/strict";
import test from "node:test";
import { editingBuildActive, initialEditingPreview, previewAnnotationsFor, reduceEditingPreview } from "./editingPreview.js";

const update = (revision, extra = {}) => ({ epoch: "a", revision, output: "/part.step", state: "building", ...extra });
const TREE = "t".repeat(64);
const PART = `${"1".repeat(64)}-v6`;
const named = (extra = {}) => ({ tree: TREE, producer: { producerKey: "kernel" }, parts: { c1: PART }, complete: false, annotations: null, ...extra });

test("the newest build is followed and a late answer about an older one is ignored", () => {
  const first = reduceEditingPreview(initialEditingPreview(), update(1));
  const second = reduceEditingPreview(first, update(2));
  assert.equal(second.revision, 2);
  assert.equal(reduceEditingPreview(second, update(1, { state: "failed", error: "late" })), second);
});

test("a daemon epoch change restarts the ordering", () => {
  const before = reduceEditingPreview(initialEditingPreview(), update(99, { state: "failed", error: "Disk full" }));
  const restarted = reduceEditingPreview(before, { ...update(1), epoch: "b" });
  assert.equal(restarted.revision, 1);
  assert.equal(restarted.error, "");
});

test("a running build keeps its phase narration and a finished or quiet one clears it", () => {
  const building = reduceEditingPreview(initialEditingPreview(), update(7, {
    phase: "Building geometry", detail: "finger linkage", updatedAt: 1234,
  }));
  const later = reduceEditingPreview(building, update(7, { phase: "", detail: "", updatedAt: null }));
  assert.deepEqual([later.phase, later.detail, later.updatedAt], ["Building geometry", "finger linkage", 1234]);
  const done = reduceEditingPreview(later, update(7, { state: "done" }));
  assert.deepEqual([done.phase, done.detail, done.updatedAt], ["", "", 0]);
  const quiet = reduceEditingPreview(done, { state: "disconnected" });
  assert.deepEqual([quiet.state, quiet.phase], ["disconnected", ""]);
});

test("a build the file moved past keeps no failure", () => {
  const failed = reduceEditingPreview(initialEditingPreview(), update(3, { state: "failed", error: "Disk full" }));
  assert.equal(failed.error, "Disk full");
  const moved = reduceEditingPreview(failed, update(3, { state: "failed", superseded: true, error: null }));
  assert.deepEqual([moved.superseded, moved.error], [true, ""]);
});

test("the saved file is kept from the moment the feed names it, and a build that wrote nothing has none", () => {
  const building = reduceEditingPreview(initialEditingPreview(), update(5, { preview: named() }));
  assert.equal(building.saved, null);
  const written = reduceEditingPreview(building, update(5, { preview: named(), saved: { tree: TREE, documentHash: "d".repeat(64) } }));
  assert.deepEqual(written.saved, { tree: TREE, documentHash: "d".repeat(64) });
  assert.equal(written.state, "building", "the file lands before the build's done");
  assert.equal(reduceEditingPreview(written, update(5, { state: "done", saved: { tree: "", documentHash: "" } })).saved, null);
});

test("only a queued or running build is active", () => {
  for (const state of ["submitted", "queued", "building"]) assert.equal(editingBuildActive({ state }), true, state);
  for (const state of ["done", "failed", "disconnected", undefined]) assert.equal(editingBuildActive({ state }), false, String(state));
});

test("the preview revision is kept from the moment the result exists, as the feed names it", () => {
  const before = reduceEditingPreview(initialEditingPreview(), update(4, { preview: null }));
  assert.equal(before.preview, null, "none before the build's result exists");
  const first = reduceEditingPreview(before, update(4, { preview: named() }));
  assert.deepEqual(first.preview, { tree: TREE, producer: { producerKey: "kernel" }, parts: { c1: PART }, complete: false, annotations: null });
  // One more part each time the build derives one, then the annotations, then all of them named.
  const more = reduceEditingPreview(first, update(4, { preview: named({ parts: { c1: PART, c2: PART }, annotations: "a".repeat(64) }) }));
  assert.deepEqual(Object.keys(more.preview.parts), ["c1", "c2"]);
  assert.equal(more.preview.annotations, "a".repeat(64));
  const whole = reduceEditingPreview(more, update(4, { state: "done", preview: named({ parts: { c1: PART, c2: PART }, complete: true }) }));
  assert.equal(whole.preview.complete, true);
  // A build that could not name its parts: no producer, and the view keys them as for a saved file.
  assert.equal(reduceEditingPreview(first, update(4, { preview: named({ producer: null }) })).preview.producer, null);
});

test("an older revision's preview is ignored, a new epoch resets it, and a superseded build has none", () => {
  const second = reduceEditingPreview(initialEditingPreview(), update(2, { preview: named() }));
  assert.equal(reduceEditingPreview(second, update(1, { preview: named({ tree: "u".repeat(64) }) })), second);
  const restarted = reduceEditingPreview(second, { ...update(1), epoch: "b" });
  assert.equal(restarted.preview, null);
  const moved = reduceEditingPreview(second, update(2, { state: "done", superseded: true, preview: null }));
  assert.equal(moved.preview, null);
  // A build the server no longer lists has no preview; a poll that failed keeps the one in hand.
  assert.equal(reduceEditingPreview(second, { error: "offline" }).preview, second.preview);
  assert.equal(reduceEditingPreview(second, { state: "disconnected", revision: null }).preview, null);
});

test("annotations bind only when they were resolved against the preview's tree", () => {
  const preview = named({ annotations: "a".repeat(64) });
  const object = { kind: "preview-annotations", schemaVersion: 1, tree: TREE, articulation: null, appearance: null, display: null, animation: null };
  assert.equal(previewAnnotationsFor(preview, object), object);
  assert.equal(previewAnnotationsFor(preview, { ...object, tree: "u".repeat(64) }), null, "another tree's object");
  assert.equal(previewAnnotationsFor(preview, null), null);
  assert.equal(previewAnnotationsFor(null, object), null);
});

test("save failure requires the feed's recorded-result milestone, never a provisional preview", () => {
  const provisional = reduceEditingPreview(initialEditingPreview(), update(1, { preview: named() }));
  const invalid = reduceEditingPreview(provisional, update(1, { state: "failed", error: "invalid mate", preview: named() }));
  assert.equal(invalid.failureStage, "build");
  assert.equal(invalid.preview.tree, TREE);
  const failed = reduceEditingPreview(provisional, update(1, {
    state: "failed", failureStage: "save", saveFailure: { output: "/part.step", tree: TREE }, error: "disk full",
  }));
  assert.equal(failed.failureStage, "save");
  assert.deepEqual(failed.saveFailure, { output: "/part.step", tree: TREE });
  assert.equal(failed.preview.tree, TREE, "an exact tree milestone can latch the preview without reannouncing it");
});

test("a retry reuses an unsaved preview only when it names the same recorded tree, and success clears the failure", () => {
  const failed = reduceEditingPreview(initialEditingPreview(), update(1, {
    state: "failed", preview: named(), failureStage: "save", saveFailure: { output: "/part.step", tree: TREE },
  }));
  const retry = reduceEditingPreview(failed, update(2));
  assert.equal(retry.preview, null, "an ordinary new build has not established a result");
  const owing = reduceEditingPreview(retry, update(2, { saving: { tree: TREE } }));
  assert.equal(owing.preview.tree, TREE);
  const again = reduceEditingPreview(owing, update(2, {
    state: "failed", failureStage: "save", saveFailure: { output: "/part.step", tree: TREE },
  }));
  assert.equal(again.preview.tree, TREE, "a failed export retry keeps the usable unsaved preview");
  const done = reduceEditingPreview(again, update(3, { state: "done", saved: { tree: TREE, documentHash: "d".repeat(64) } }));
  assert.equal(done.failureStage, undefined);
  assert.equal(done.saveFailure, null);
  assert.equal(done.retainedPreview, null);
  const different = reduceEditingPreview(retry, update(2, { saving: { tree: "other" } }));
  assert.equal(different.preview, null);
  assert.equal(different.retainedPreview, null, "another tree cannot inherit the preview");
  for (const next of [{ state: "disconnected" }, update(1, { state: "failed", superseded: true }), { ...update(1), epoch: "new" }]) {
    assert.equal(reduceEditingPreview(failed, next).retainedPreview, null, JSON.stringify(next));
  }
});
