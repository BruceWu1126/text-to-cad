import assert from "node:assert/strict";
import test from "node:test";
import { goneFromEngine } from "./useXbfScene.js";

test("only a document the engine no longer holds is opened again: not a lost connection, not a refusal", () => {
  assert.equal(goneFromEngine(Object.assign(new Error("the document's version: the engine answered 404"), { status: 404 })), true);
  assert.equal(goneFromEngine(Object.assign(new Error("the engine answered 500"), { status: 500 })), false);
  assert.equal(goneFromEngine(new TypeError("Failed to fetch")), false);
  assert.equal(goneFromEngine(null), false);
});
