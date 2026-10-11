// The display tolerance is cadgen's: nothing is drawn until the host installs the one its
// server published, and every mesh request names that pair.
import assert from "node:assert/strict";
import test from "node:test";

import { displayTessellation, installDisplayTessellation } from "./displayTessellation.js";
import { TEST_DISPLAY_TESSELLATION } from "./testing.js";

test("the host installs cadgen's display tolerance before a STEP model is drawn", () => {
  assert.throws(() => displayTessellation(), /installDisplayTessellation/);
  for (const bad of [null, {}, { chordTolerance: 1 }, { chordTolerance: 0, angleTolerance: 0.35 }]) {
    assert.throws(() => installDisplayTessellation(bad), /cadgen's display tolerance is/);
  }
  installDisplayTessellation(TEST_DISPLAY_TESSELLATION);
  assert.deepEqual(displayTessellation(), TEST_DISPLAY_TESSELLATION);
});
