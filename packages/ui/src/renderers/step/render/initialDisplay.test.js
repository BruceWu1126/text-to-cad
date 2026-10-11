import assert from "node:assert/strict";
import test from "node:test";

import {
  initialDisplayFromProbe,
  probeInitialDisplay,
  producedDisplayPlan,
} from "./initialDisplay.js";

const MIB = 1024 * 1024;
const PART = `${"d".repeat(64)}-v1`;

// A part's row as the store answers it, sized by its display mesh.
function row(glb = {}) {
  return { part: PART, glb: { object: "1".repeat(64), byteLength: MIB, decodedBytes: 3 * MIB, ...glb },
    selectors: { object: "2".repeat(64), byteLength: 64 } };
}

function cachedProbes(stored) {
  const requested = [];
  return {
    requested,
    probeEntries: async (parts, ...rest) => {
      requested.push([parts, ...rest]);
      return new Map(stored ? [[parts[0], stored]] : []);
    },
  };
}

test("a stored part opens the component, sized by its display mesh", async () => {
  const stored = row();
  const cache = cachedProbes(stored);
  const hit = await probeInitialDisplay({ part: PART, maxInFlightBytes: 256 * MIB, ...cache });
  assert.equal(hit.cacheProbe, stored);
  assert.equal(hit.plan.estimatedBytes, 4 * MIB);
  assert.equal(hit.plan.fitsDecodeCap, true);
  assert.deepEqual(cache.requested, [[[PART], { signal: undefined }]], "the part, and no other, is asked, by its key alone");
});

test("a missing, refused or unsized part leaves the component cold", async () => {
  for (const [stored, options] of [
    [null, {}],
    [row(), { rejectedCacheObjects: new Set(["1".repeat(64)]) }],
    [row({ decodedBytes: NaN }), {}],
  ]) {
    assert.equal(await probeInitialDisplay({ part: PART, maxInFlightBytes: 256 * MIB,
      ...cachedProbes(stored), ...options }), null);
  }
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(probeInitialDisplay({ part: PART, maxInFlightBytes: 256 * MIB,
    signal: controller.signal, probeEntries: async (_parts, { signal }) => signal.throwIfAborted() }),
  { name: "AbortError" });
});

test("a mesh too large to admit with the rest still opens, to load alone", () => {
  const huge = row({ decodedBytes: 300 * MIB });
  const warm = initialDisplayFromProbe(huge, { maxInFlightBytes: 256 * MIB });
  assert.deepEqual(warm.plan, { estimatedBytes: 301 * MIB, fitsDecodeCap: false, reason: "warm" });
  assert.deepEqual(producedDisplayPlan(huge, { maxInFlightBytes: 256 * MIB }),
    { estimatedBytes: 301 * MIB, fitsDecodeCap: false, reason: "produced" });
  assert.equal(producedDisplayPlan(null, { maxInFlightBytes: 256 * MIB }), null, "no row, no plan");
});
