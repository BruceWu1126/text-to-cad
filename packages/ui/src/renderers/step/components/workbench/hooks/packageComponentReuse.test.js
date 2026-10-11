import assert from "node:assert/strict";
import test from "node:test";

import {
  matchingDisplayedPackageContext,
  retainedComponentMeshesForRevision,
} from "./packageComponentReuse.js";

const HASH_A = "a".repeat(64);
const HASH_B = "b".repeat(64);
const PART_A = `${"d".repeat(64)}-v1`;
const PART_B = `${"e".repeat(64)}-v1`;
const CID = "0123456789abcdef";

function descriptor(part = PART_A) {
  return { components: { [CID]: { part } } };
}

// The row a component's mesh was drawn from: its part's record.
function row(part = PART_A) {
  return { part, glb: { object: "1".repeat(64) }, selectors: { object: "2".repeat(64) } };
}

function meshUrl(tree) {
  return `/__cad/store?file=/${tree}&v=${tree}`;
}

test("a component whose part is unchanged keeps its mesh across a tree revision", () => {
  const meshData = { vertices: new Float32Array(3) };
  const previous = {
    descriptor: descriptor(),
    componentIdentityByCid: { [CID]: row() },
    meshUrl: meshUrl(HASH_A),
    componentMeshDataByCid: { [CID]: meshData },
  };
  const retained = retainedComponentMeshesForRevision({
    previous,
    descriptor: descriptor(),
    meshUrl: meshUrl(HASH_B),
  });
  assert.equal(retained[CID], meshData);
});

test("reuse rejects a changed part, and a mesh no part's row was drawn from", () => {
  const meshData = {};
  const previous = {
    descriptor: descriptor(),
    componentIdentityByCid: { [CID]: row() },
    meshUrl: meshUrl(HASH_A),
    componentMeshDataByCid: { [CID]: meshData },
  };
  assert.deepEqual(retainedComponentMeshesForRevision({
    previous,
    descriptor: descriptor(PART_B),
    meshUrl: meshUrl(HASH_B),
  }), {}, "another part cannot reuse the mesh");
  assert.deepEqual(retainedComponentMeshesForRevision({
    previous: { ...previous, componentIdentityByCid: {} },
    descriptor: descriptor(),
    meshUrl: meshUrl(HASH_B),
  }), {}, "a mesh with no row behind it is never reused");
});

test("A remains the bounded reuse source after B staging fails or is superseded by C", () => {
  const meshData = {};
  const displayedA = {
    file: "assembly.step",
    meshHash: "tree-a",
    complete: true,
    descriptor: descriptor(),
    componentIdentityByCid: { [CID]: row() },
    meshUrl: meshUrl(HASH_A),
    componentMeshDataByCid: { [CID]: meshData },
  };
  const visibleA = {
    file: "assembly.step",
    meshHash: "tree-a",
    assemblyInteractionReady: true,
  };

  const stagingB = { requestId: 2, file: "assembly.step", meshHash: "tree-b", complete: false };
  assert.equal(
    matchingDisplayedPackageContext(displayedA, visibleA, stagingB.file),
    displayedA,
    "staging does not replace displayed ownership",
  );
  assert.equal(
    matchingDisplayedPackageContext(stagingB, visibleA, stagingB.file),
    null,
    "partial work can never regain displayed ownership after failure",
  );

  const sourceForC = matchingDisplayedPackageContext(displayedA, visibleA, "assembly.step");
  const retainedForC = retainedComponentMeshesForRevision({
    previous: sourceForC,
    descriptor: descriptor(),
    meshUrl: meshUrl(HASH_B),
  });
  assert.equal(retainedForC[CID], meshData);
  assert.equal(
    matchingDisplayedPackageContext(displayedA, { ...visibleA, meshHash: "other" }, "assembly.step"),
    null,
    "a context not backing the visible mesh is never reused",
  );
});
