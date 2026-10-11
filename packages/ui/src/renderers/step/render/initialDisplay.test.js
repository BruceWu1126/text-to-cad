import assert from "node:assert/strict";
import test from "node:test";

import {
  initialDisplayFromProbe,
  probeInitialDisplay,
  producedDisplayPlan,
} from "./initialDisplay.js";
import { TEST_DISPLAY_TESSELLATION, installTestDisplayTessellation } from "@text-to-cad/core/lib/surf/testing.js";

// The display tolerance a cadgen server publishes, installed as a host installs it.
installTestDisplayTessellation();

const MIB = 1024 * 1024;

function cachedProbes(standard) {
  const requested = [];
  return {
    requested,
    probeEntries: async ([input], tessellation) => {
      requested.push(tessellation);
      return new Map(standard ? [[input, standard]] : []);
    },
  };
}

test("a stored display mesh opens the component without a SURF URL, sized by its body", async () => {
  const standard = { surfaceObject: "exact", byteLength: MIB, decodedBytes: 3 * MIB };
  const cache = cachedProbes(standard);
  const hit = await probeInitialDisplay({ surfaceInput: "input", maxInFlightBytes: 256 * MIB, ...cache });
  assert.equal(hit.cacheProbe, standard);
  assert.equal(hit.plan.estimatedBytes, 4 * MIB);
  assert.equal(hit.plan.fitsDecodeCap, true);
  assert.deepEqual(cache.requested, [TEST_DISPLAY_TESSELLATION],
    "the display mesh, and no other, is asked, by both its tolerances");
});

test("a missing, mismatched, refused or unsized display entry leaves the component cold", async () => {
  const row = { object: "standard", surfaceObject: "exact", byteLength: MIB, decodedBytes: 3 * MIB };
  for (const [standard, options] of [
    [null, {}],
    [{ ...row, surfaceObject: "different" }, {}],
    [row, { rejectedCacheObjects: new Set(["standard"]) }],
    [{ ...row, decodedBytes: NaN }, {}],
  ]) {
    assert.equal(await probeInitialDisplay({ surfaceInput: "input", surfaceObject: "exact",
      maxInFlightBytes: 256 * MIB, ...cachedProbes(standard), ...options }), null);
  }
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(probeInitialDisplay({ surfaceInput: "input", maxInFlightBytes: 256 * MIB,
    signal: controller.signal, probeEntries: async (_inputs, _options, { signal }) => signal.throwIfAborted() }),
  { name: "AbortError" });
});

test("a mesh too large to admit with the rest still opens, to load alone", () => {
  const huge = { surfaceObject: "exact", byteLength: MIB, decodedBytes: 300 * MIB };
  const warm = initialDisplayFromProbe(huge, { surfaceObject: "exact", maxInFlightBytes: 256 * MIB });
  assert.deepEqual(warm.plan, { estimatedBytes: 301 * MIB, fitsDecodeCap: false, reason: "warm" });
  assert.deepEqual(producedDisplayPlan(huge, { maxInFlightBytes: 256 * MIB }),
    { estimatedBytes: 301 * MIB, fitsDecodeCap: false, reason: "produced" });
  assert.equal(producedDisplayPlan(null, { maxInFlightBytes: 256 * MIB }), null, "no row, no plan");
});
