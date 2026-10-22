// Test-only access to stored parts: the committed fixture parts (`../fixtures`, written by cadgen's
// part store before the engine replaced it), and the row that store recorded for a mesh. Nothing
// here ships.

import { createHash } from "node:crypto";
import fs from "node:fs";

import { decodeComponentTessellation, partRow } from "../tessellationCache.js";
import { encodeMeshFixture, partMeshFacts, testPartKey } from "../testing.js";

export { encodeMeshFixture, testPartKey };

const FIXTURES = new URL("../fixtures/", import.meta.url);
const MANIFEST = JSON.parse(fs.readFileSync(new URL("fixtures.json", FIXTURES), "utf8"));

export function sha256Hex(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

/** One fixture component's selector table, as cadgen stores it (parsed). */
export function selectorTableFixture(name) {
  return JSON.parse(fs.readFileSync(new URL(`${name}.selectors.json`, FIXTURES), "utf8"));
}

// A selector table's facts for a row: a fixture's own, else a stand-in no test reads.
const NO_TABLE = Object.freeze({ object: "e".repeat(64), byteLength: 2, faceCount: 0, edgeCount: 0, vertexCount: 0 });

/**
 * The row the part store recorded for a valid mesh body: the part it names, its mesh's facts and
 * address, and `selectors` (a selector table's facts, a stand-in when omitted).
 */
export function partRowFor(bytes, selectors = NO_TABLE) {
  const decoded = decodeComponentTessellation(bytes);
  if (!decoded) throw new TypeError("not a valid stored mesh body");
  const row = partRow(decoded.part, { glb: { object: sha256Hex(bytes), ...partMeshFacts(bytes) }, selectors });
  if (!row) throw new TypeError("not a valid part record");
  return row;
}

/** One fixture component's stored display mesh, with its part and its row. */
export function meshFixture(name) {
  const bytes = new Uint8Array(fs.readFileSync(new URL(`${name}.glb`, FIXTURES)));
  const table = fs.readFileSync(new URL(`${name}.selectors.json`, FIXTURES));
  const { stats } = JSON.parse(table.toString("utf8"));
  const selectors = { object: sha256Hex(table), byteLength: table.byteLength, faceCount: stats.faceCount,
    edgeCount: stats.edgeCount, vertexCount: stats.vertexCount };
  return { bytes, part: MANIFEST[name].part, row: partRowFor(bytes, selectors) };
}
