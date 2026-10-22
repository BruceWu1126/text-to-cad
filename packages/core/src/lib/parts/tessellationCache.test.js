import assert from "node:assert/strict";
import test from "node:test";
import { Box3 } from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

import {
  decodeComponentTessellation,
  meshEdgePolylines,
  meshFaceRanges,
  partMeshMatches,
  partRow,
} from "./tessellationCache.js";
import { buildComponentMeshData } from "./partMeshData.js";
import { encodeMeshFixture, meshFixture, partRowFor, selectorTableFixture, testPartKey } from "./__tests__/meshFixtures.js";
import { buildComposedPackageMeshData } from "../assembly/meshData.js";

const P = testPartKey("11");
const P2 = testPartKey("22");

function componentFixture() {
  return {
    positions: new Float32Array([0, 0, 0, 2, 0, 0, 0, 3, 0]),
    normals: new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1]),
    indices: new Uint32Array([0, 1, 2]),
    faceRanges: [{ ord: 7, color: [0.2, 0.4, 0.6, 1], indexStart: 0, indexCount: 3 }],
    edges: [{
      ord: 9,
      visibilityClass: "boundary",
      polyline: new Float32Array([0, 0, 0, 2, 0, 0]),
    }],
    bounds: { min: [0, 0, 0], max: [2, 3, 0] },
  };
}

function encodedEntry(overrides = {}) {
  return encodeMeshFixture(componentFixture(), { part: P, ...overrides });
}

// The fixture triangle, decoded: what a render consumer holds.
function solidComponent() {
  return decodeComponentTessellation(encodedEntry()).component;
}

// The body with its JSON chunk rewritten by `mutate` and its BIN chunk kept.
function rewriteJson(bytes, mutate) {
  const source = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const jsonLength = source.getUint32(12, true);
  const gltf = JSON.parse(new TextDecoder().decode(bytes.subarray(20, 20 + jsonLength)));
  mutate(gltf);
  const json = new TextEncoder().encode(JSON.stringify(gltf));
  const padded = (json.length + 3) & ~3;
  const bin = bytes.subarray(20 + jsonLength);
  const result = new Uint8Array(20 + padded + bin.length);
  const view = new DataView(result.buffer);
  view.setUint32(0, source.getUint32(0, true), true);
  view.setUint32(4, source.getUint32(4, true), true);
  view.setUint32(8, result.length, true);
  view.setUint32(12, padded, true);
  view.setUint32(16, source.getUint32(16, true), true);
  result.set(json, 20);
  result.fill(0x20, 20 + json.length, 20 + padded);
  result.set(bin, 20 + padded);
  return result;
}

// The body with one u32 of a named table (`cadgen.faces`, `cadgen.edges`) set to `value`.
function rewriteTable(bytes, name, word, value) {
  const result = bytes.slice();
  const view = new DataView(result.buffer);
  const jsonLength = view.getUint32(12, true);
  const gltf = JSON.parse(new TextDecoder().decode(result.subarray(20, 20 + jsonLength)));
  const entry = gltf.bufferViews.find((bufferView) => bufferView.name === name);
  view.setUint32(28 + jsonLength + entry.byteOffset + 4 * word, value, true);
  return result;
}

test("a GLB body round-trips its arrays and tables as views and names its part", () => {
  const source = componentFixture();
  const bytes = encodedEntry();
  const decoded = decodeComponentTessellation(bytes, { part: P });
  assert.ok(decoded);
  assert.equal(decoded.part, P);
  const { component } = decoded;
  assert.ok(component.indices instanceof Uint16Array, "a component of at most 65,535 vertices indexes in u16");
  for (const field of ["positions", "normals", "indices"]) {
    assert.deepEqual([...component[field]], [...source[field]], field);
  }
  for (const field of ["positions", "normals", "indices", "faceTable", "edgeTable", "edgePoints"]) {
    assert.equal(component[field].buffer, bytes.buffer, `${field} is zero-copy`);
  }
  assert.deepEqual(meshFaceRanges(component), source.faceRanges);
  assert.deepEqual(meshEdgePolylines(component).map(({ ord, visibilityClass, polyline }) => [ord, visibilityClass, [...polyline]]),
    [[9, "boundary", [0, 0, 0, 2, 0, 0]]]);
  assert.deepEqual(component.bounds, source.bounds);

  const unalignedStorage = new Uint8Array(bytes.length + 1);
  unalignedStorage.set(bytes, 1);
  const unaligned = unalignedStorage.subarray(1);
  const copied = decodeComponentTessellation(unaligned, { part: P });
  assert.ok(copied);
  assert.deepEqual([...copied.component.positions], [...source.positions]);
  assert.notEqual(copied.component.positions.buffer, unalignedStorage.buffer, "unaligned input safely copies");
});

test("a component past 65,535 vertices indexes in u32, and the render buffers are u32 either way", () => {
  const vertices = 65536;
  const positions = new Float32Array(3 * vertices);
  for (let vertex = 0; vertex < vertices; vertex += 1) positions[3 * vertex] = vertex;
  const component = {
    positions, normals: new Float32Array(3 * vertices).fill(1 / Math.sqrt(3)),
    indices: new Uint32Array([0, 1, vertices - 1]),
    faceRanges: [{ ord: 1, indexStart: 0, indexCount: 3 }], edges: [],
    bounds: { min: [0, 0, 0], max: [vertices - 1, 0, 0] },
  };
  const wide = decodeComponentTessellation(encodeMeshFixture(component, { part: P }));
  assert.ok(wide.component.indices instanceof Uint32Array);
  assert.deepEqual([...wide.component.indices], [0, 1, vertices - 1]);
  assert.ok(buildComponentMeshData(wide.component).indices instanceof Uint32Array);
  assert.ok(buildComponentMeshData(solidComponent()).indices instanceof Uint32Array);
});

test("a stored mesh is a glTF 2.0 file a stock GLTFLoader draws", async () => {
  // A body cadgen wrote (`fixtures/slot.glb`), not one a test helper encoded.
  const { bytes, part } = meshFixture("slot");
  const { component } = decodeComponentTessellation(bytes, { part });
  const gltf = await new GLTFLoader().parseAsync(bytes.slice().buffer, "");
  const meshes = [];
  gltf.scene.traverse((object) => { if (object.isMesh) meshes.push(object); });
  assert.equal(meshes.length, 1, "one mesh, one triangle primitive");
  const geometry = meshes[0].geometry;
  assert.deepEqual([...geometry.getAttribute("position").array], [...component.positions]);
  assert.deepEqual([...geometry.getIndex().array], [...component.indices]);
  // Its node turns Z-up millimetres into Y-up metres: (x, y, z) mm is (x, z, -y) / 1000 m.
  const box = new Box3().setFromObject(gltf.scene);
  const { min, max } = component.bounds;
  const rounded = (value) => Math.round(value * 1e6) / 1e6 + 0;
  assert.deepEqual([...box.min.toArray(), ...box.max.toArray()].map(rounded),
    [min[0], min[2], -max[1], max[0], max[2], -min[1]].map((value) => rounded(value / 1000)));
});

test("empty imported components round-trip through the cache without changing assembly bounds", () => {
  // What cadgen stores for a product with no faces and no edges: empty arrays, zero bounds.
  const component = {
    positions: new Float32Array(0), normals: new Float32Array(0),
    indices: new Uint32Array(0), faceRanges: [], edges: [],
    bounds: { min: [0, 0, 0], max: [0, 0, 0] },
  };
  const bytes = encodeMeshFixture(component, { part: P });
  const decoded = decodeComponentTessellation(bytes);
  assert.ok(decoded, "an empty product entry is a valid complete payload");
  assert.equal(20 + new DataView(bytes.buffer).getUint32(12, true), bytes.length, "a JSON chunk alone");
  assert.ok(partMeshMatches(bytes, partRowFor(bytes)), "a part's whole mesh");
  for (const field of ["positions", "normals", "indices", "faceTable", "edgeTable", "edgePoints"]) {
    assert.equal(decoded.component[field].length, 0, field);
  }
  const emptyMesh = buildComponentMeshData(decoded.component);
  const solidMesh = buildComponentMeshData(solidComponent());
  const descriptor = { assembly: { root: {
    id: "root", nodeType: "assembly", children: [
      { id: "empty", nodeType: "part", children: [] },
      { id: "solid", nodeType: "part", children: [] },
    ],
  } }, occurrences: [
    { id: "empty", component: "empty", transform: [
      1, 0, 0, -1000, 0, 1, 0, -1000, 0, 0, 1, -1000, 0, 0, 0, 1,
    ] },
    { id: "solid", component: "solid" },
  ] };
  const assembly = buildComposedPackageMeshData(descriptor, new Map([
    ["empty", emptyMesh], ["solid", solidMesh],
  ]));
  assert.deepEqual(assembly.parts.map((part) => part.occurrenceId), ["empty", "solid"]);
  assert.deepEqual(assembly.missingComponentIds, []);
  assert.equal(assembly.parts[0].bounds, null);
  assert.equal(assembly.parts[0].triangleCount, 0);
  assert.equal(assembly.parts[1].triangleCount, 1);
  assert.deepEqual(assembly.bounds, solidMesh.bounds, "only real geometry frames the view");
  assert.deepEqual(assembly.assemblyRoot.bounds, solidMesh.bounds);
});

test("a wire-only imported component draws its edges and frames nothing", () => {
  // A STEP product holding only wires (a sketch, a reference curve) has no faces: cadgen stores
  // its edges' polylines, bounded by their points, and no triangles.
  const component = {
    positions: new Float32Array(0), normals: new Float32Array(0),
    indices: new Uint32Array(0), faceRanges: [],
    edges: [
      { ord: 1, visibilityClass: "feature", polyline: new Float32Array([0, 0, 0, 0, 0, 50]) },
      { ord: 2, visibilityClass: "feature", polyline: new Float32Array([10, 0, 50, 0, 10, 50, -10, 0, 50]) },
    ],
    bounds: { min: [-10, 0, 0], max: [10, 10, 50] },
  };
  const decoded = decodeComponentTessellation(encodeMeshFixture(component, { part: P }));
  assert.ok(decoded, "a wire-only product is a valid complete payload");
  assert.deepEqual(meshEdgePolylines(decoded.component).map((edge) => edge.ord), [1, 2]);

  const wireMesh = buildComponentMeshData(decoded.component);
  assert.deepEqual([...wireMesh.cadEdgeIndices], [0, 1, 2, 3, 3, 4], "its edges reach the mesh data");
  const solidMesh = buildComponentMeshData(solidComponent());
  const assembly = buildComposedPackageMeshData({ assembly: { root: {
    id: "root", nodeType: "assembly", children: [
      { id: "wire", nodeType: "part", children: [] },
      { id: "solid", nodeType: "part", children: [] },
    ],
  } }, occurrences: [
    { id: "wire", component: "wire", transform: [1, 0, 0, 1000, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1] },
    { id: "solid", component: "solid" },
  ] }, new Map([["wire", wireMesh], ["solid", solidMesh]]));
  assert.deepEqual(assembly.parts.map((part) => part.occurrenceId), ["wire", "solid"]);
  assert.equal(assembly.parts[0].bounds, null, "nothing draws a part without triangles");
  assert.deepEqual(assembly.bounds, solidMesh.bounds, "so it cannot move the camera");
});

test("each edge class draws as its line class, and none draws nothing", () => {
  const classes = ["feature", "tangent", "seam", "degenerate", "boundary", "nonManifold", "unknown", "none"];
  const component = {
    ...componentFixture(),
    edges: classes.map((visibilityClass, index) => ({
      ord: index + 1, visibilityClass, polyline: new Float32Array([index, 0, 0, index, 1, 0]),
    })),
  };
  const decoded = decodeComponentTessellation(encodeMeshFixture(component, { part: P }));
  const { cadEdgePositions, cadEdgeClassRanges } = buildComponentMeshData(decoded.component);
  assert.deepEqual(cadEdgeClassRanges.map(({ classId, pointCount, segmentCount }) => [classId, pointCount, segmentCount]),
    [["feature", 8, 4], ["tangent", 2, 1], ["seam", 2, 1], ["degenerate", 2, 1]]);
  // Feature first, in edge order, with the classes that draw as features (boundary,
  // nonManifold, unknown) after the feature edge itself.
  assert.deepEqual([0, 1, 2, 3].map((point) => cadEdgePositions[6 * point]), [0, 4, 5, 6]);
});

test("cadgen's stored meshes decode as the bodies their records describe", () => {
  for (const name of ["sun_gear", "cam_follower_roller", "slot"]) {
    const fixture = meshFixture(name);
    const row = partRow(fixture.part, fixture.row);
    assert.ok(row, `${name} row`);
    assert.ok(partMeshMatches(fixture.bytes, row), `${name}: its mesh is the one its record names`);
    const decoded = decodeComponentTessellation(fixture.bytes, { part: fixture.part });
    assert.ok(decoded, `${name} decodes`);
    assert.equal(decoded.component.positions.length, 3 * row.glb.vertexCount);
    assert.equal(decoded.component.normals.length, 3 * row.glb.vertexCount);
    assert.equal(decoded.component.indices.length, row.glb.indexCount);
    // Its mesh holds every face and edge of the part's selector table.
    const { stats } = selectorTableFixture(name);
    assert.deepEqual([row.glb.faceCount, row.glb.edgeCount], [stats.faceCount, stats.edgeCount], name);
    assert.ok(row.glb.faceCount > 0 && row.glb.edgeCount > 0);
  }
});

test("decode rejects a body of another part, or of none, as a cache miss", () => {
  const bytes = encodedEntry();
  assert.equal(decodeComponentTessellation(bytes, { part: P2 }), null);
  assert.equal(decodeComponentTessellation(bytes, { part: `${P.slice(0, -1)}2` }), null, "another version is another part");

  const cad = (mutate) => rewriteJson(bytes, (gltf) => mutate(gltf.extras.cadgen));
  assert.equal(decodeComponentTessellation(cad((c) => { c.part = P2; }), { part: P }), null);
  assert.equal(decodeComponentTessellation(cad((c) => { c.part = P.slice(0, 64); })), null, "a digest is no part key");
  assert.equal(decodeComponentTessellation(cad((c) => { delete c.part; })), null);
  assert.equal(decodeComponentTessellation(cad((c) => { c.part = P2; }), { part: P2 }).part, P2, "a body is the part it names");
});

test("decode rejects corrupt, truncated and foreign containers", () => {
  const bytes = encodedEntry();
  assert.equal(decodeComponentTessellation(null), null);
  assert.equal(decodeComponentTessellation(new Uint8Array(4)), null);
  assert.equal(decodeComponentTessellation(bytes.subarray(0, bytes.length - 4)), null);
  const wrongMagic = bytes.slice();
  new DataView(wrongMagic.buffer).setUint32(0, 0x53534554, true);
  assert.equal(decodeComponentTessellation(wrongMagic), null, "a TESS body is not one");
  const glTF1 = bytes.slice();
  new DataView(glTF1.buffer).setUint32(4, 1, true);
  assert.equal(decodeComponentTessellation(glTF1), null);
  const longer = new Uint8Array(bytes.length + 4);
  longer.set(bytes);
  assert.equal(decodeComponentTessellation(longer), null, "the header's length is the body's");
  const badCount = rewriteJson(bytes, (gltf) => { gltf.accessors[0].count = -1; });
  assert.equal(decodeComponentTessellation(badCount), null);
});

test("decode rejects a JSON chunk that is not the canonical one for its values", () => {
  const bytes = encodedEntry();
  const row = partRowFor(bytes);
  const mutations = [
    (g) => { g.extras.cadgen.bounds.min = [null, 0, 0]; },
    (g) => { g.extras.cadgen.bounds.min[0] = g.extras.cadgen.bounds.max[0] + 1; },
    (g) => { g.extras.cadgen.faceColors = [[1, 0, 0]]; },
    (g) => { g.extras.cadgen.edgeClasses = ["none"]; },
    (g) => { g.extras.cadgen.faces.count = 2; },
    (g) => { g.extras.cadgen.edges = null; },
    (g) => { g.extras.cadgen.extension = "refused"; },
    (g) => { g.extras.other = {}; },
    (g) => { g.accessors[0].max = [2, 3, 1]; },
    (g) => { g.accessors[2].componentType = 5125; },
    (g) => { g.accessors[2].count = 6; },
    (g) => { g.bufferViews[3].byteOffset += 4; },
    (g) => { g.buffers[0].byteLength += 4; },
    (g) => { g.nodes[0].scale = [1, 1, 1]; },
    (g) => { g.meshes[0].primitives[0].mode = 1; },
    (g) => { g.extensionsUsed = ["KHR_mesh_quantization"]; },
  ];
  for (const [index, mutate] of mutations.entries()) {
    const mutated = rewriteJson(bytes, mutate);
    assert.equal(decodeComponentTessellation(mutated), null, `JSON mutation ${index}`);
    assert.equal(partMeshMatches(mutated, row), false, `JSON mutation ${index} is no record's mesh`);
  }
  assert.ok(decodeComponentTessellation(rewriteJson(bytes, () => {})), "the canonical JSON, re-spelled, reads");
});

test("decode rejects face and edge tables that do not cover their arrays in order", () => {
  const bytes = encodedEntry();
  const faces = (word, value) => rewriteTable(bytes, "cadgen.faces", word, value);
  const edges = (word, value) => rewriteTable(bytes, "cadgen.edges", word, value);
  const mutations = {
    "face ordinal 0": faces(0, 0),
    "face range start": faces(1, 3),
    "face partial triangle": faces(2, 2),
    "face range short of the indices": faces(2, 0),
    "face colour past the palette": faces(3, 2),
    "edge ordinal 0": edges(0, 0),
    "edge points start": edges(1, 1),
    "edge of one point": edges(2, 1),
    "edge class past the names": edges(3, 8),
  };
  for (const [label, mutated] of Object.entries(mutations)) {
    assert.equal(decodeComponentTessellation(mutated), null, label);
  }
  const twoFaces = encodeMeshFixture({
    ...componentFixture(),
    positions: new Float32Array([0, 0, 0, 2, 0, 0, 0, 3, 0, 0, 0, 1]),
    normals: new Float32Array(12).fill(0.5),
    indices: new Uint32Array([0, 1, 2, 0, 1, 3]),
    faceRanges: [{ ord: 1, indexStart: 0, indexCount: 3 }, { ord: 2, indexStart: 3, indexCount: 3 }],
    bounds: { min: [0, 0, 0], max: [2, 3, 1] },
  }, { part: P });
  assert.ok(decodeComponentTessellation(twoFaces));
  assert.equal(decodeComponentTessellation(rewriteTable(twoFaces, "cadgen.faces", 4, 1)), null,
    "face ordinals rise");
});

test("a body that leaves a face no mesher covered names it, and its record counts it", () => {
  const leftOut = encodeMeshFixture({
    ...componentFixture(),
    faceRanges: [{ ord: 1, indexStart: 0, indexCount: 3 }, { ord: 2, indexStart: 3, indexCount: 0 }],
    unmeshedFaces: [2],
  }, { part: P });
  const decoded = decodeComponentTessellation(leftOut);
  assert.ok(decoded);
  assert.deepEqual([...decoded.component.unmeshedFaces], [2]);
  assert.deepEqual([...solidComponent().unmeshedFaces], [], "a whole body names none");
  // The render data carries them, for the viewer to name the part drawn without them.
  assert.deepEqual(buildComponentMeshData(decoded.component).unmeshedFaces, [2]);
  const row = partRowFor(leftOut);
  assert.equal(row.glb.unmeshedFaceCount, 1);
  assert.deepEqual(partRow(P, row), row);
  assert.ok(partMeshMatches(leftOut, row));
  const { unmeshedFaceCount: _count, ...uncounted } = row.glb;
  assert.equal(partMeshMatches(leftOut, { ...row, glb: uncounted }), false, "a record that does not count them is another mesh's");
  assert.equal("unmeshedFaceCount" in partRowFor(encodedEntry()).glb, false, "a whole body's record counts none");
  for (const count of [0, 3, 1.5]) {
    assert.equal(partRow(P, { ...row, glb: { ...row.glb, unmeshedFaceCount: count } }), null, `count ${count}`);
  }
  // A named face is one of the table's empty faces, named once, in order.
  for (const [label, value] of Object.entries({
    "a face with triangles": [1], "an empty list": [], "out of order": [2, 2], "not a face": [3],
  })) {
    assert.equal(decodeComponentTessellation(rewriteJson(leftOut, (gltf) => {
      gltf.extras.cadgen.unmeshedFaces = value;
    })), null, label);
  }
});
