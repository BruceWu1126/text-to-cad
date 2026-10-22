// A part's stored display mesh, read: the codec of the GLB body cadgen's part store wrote for each
// component, keyed by the part (`<digest>-v<version>`), and the record that described it. The store
// and its writer are gone with the old pipeline (the engine serves a document's parts itself); the
// bodies still read are committed ones -- the docs hero's static package (`common/source.js`'s
// `packageSourceFromBaseUrl`) and the test fixtures (`fixtures/`). This module only reads.
//
// It is BROWSER-PURE: codec and records only, no filesystem and no transport.
//
// A body is glTF 2.0 binary, in CAD units (millimetres, Z up). Its BIN chunk holds, each
// section 4-byte aligned and present only when not empty: POSITION and NORMAL (f32 xyz), the
// indices of the ONE triangle primitive (u16 for at most 65,535 vertices, else u32), the face
// table (u32 rows: ord, indexStart, indexCount, colour — 0 for none, else a palette row plus
// one), the edge table (u32 rows: ord, pointStart, pointCount, class) and every edge's
// polyline (f32 xyz) back to back. Its JSON chunk is the canonical JSON for the values in
// `extras.cadgen` (the part, bounds, face colour palette) and the counts: a node carrying the
// CAD -> glTF frame, one mesh, three accessors, the buffer views and where each table is. A
// reader rebuilds that JSON and requires it, then views every section in place.

// The JSON chunk grows only with the face colour palette, up to the bound cadgen's writer kept.
export const MESH_MAX_JSON_BYTES = 64 * 1024 * 1024;
// The edge table's class codes, in the order cadgen wrote them.
export const MESH_EDGE_CLASSES = Object.freeze([
  "none", "feature", "tangent", "seam", "degenerate", "boundary", "nonManifold", "unknown",
]);
// Both tables are rows of four u32: (ord, start, count, reference).
export const MESH_TABLE_COLUMNS = 4;

const GLB_MAGIC = 0x46546c67; // "glTF" little-endian
const GLB_VERSION = 2;
const JSON_CHUNK = 0x4e4f534a; // "JSON"
const BIN_CHUNK = 0x004e4942; // "BIN\0"
const UNSIGNED_SHORT_VERTEX_LIMIT = 65535;
// Z-up millimetres as glTF's Y-up metres: -90 degrees about X, then 0.001.
const NODE_ROTATION = Object.freeze([-Math.SQRT1_2, 0, 0, Math.SQRT1_2]);
const NODE_SCALE = Object.freeze([0.001, 0.001, 0.001]);
// `extras.cadgen`'s values, in the order cadgen writes them; the class names and the
// table references follow them.
const CAD_VALUES = Object.freeze(["part", "bounds", "faceColors"]);
// The faces no mesher could cover, which the body leaves undrawn: a value only a body with one
// has (`unmeshedFaces`), and a count only its record has.
const UNMESHED = "unmeshedFaces";
const UNMESHED_COUNT = "unmeshedFaceCount";
const COUNT_FIELDS = Object.freeze(["vertexCount", "indexCount", "faceCount", "edgeCount", "edgePointCount"]);
const SHA256_RE = /^[0-9a-f]{64}$/;
// A part's key: a digest and the version of its derivation.
const PART_RE = /^[0-9a-f]{64}-v[0-9]+$/;
const GLB_FIELDS = Object.freeze(["object", "byteLength", "decodedBytes", ...COUNT_FIELDS]);
const SELECTOR_FIELDS = Object.freeze(["object", "byteLength", "faceCount", "edgeCount", "vertexCount"]);
const JSON_TEXT = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });

/** Whether `value` is a part's key as a view names it. */
export function isPartKey(value) {
  return typeof value === "string" && PART_RE.test(value);
}

function align4(value) {
  return (value + 3) & ~3;
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function finiteNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function finiteTuple(value, length) {
  return Array.isArray(value) && value.length === length && value.every(finiteNumber);
}

function validCount(value) {
  return Number.isSafeInteger(value) && value >= 0;
}

// JSON equality as cadgen's writer decided it: numbers by value, objects by
// their key sets, arrays element by element.
function sameJson(a, b) {
  if (a === b) return true;
  if (!a || !b || typeof a !== "object" || typeof b !== "object") return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (Array.isArray(a)) {
    if (a.length !== b.length) return false;
    for (let index = 0; index < a.length; index += 1) {
      if (!sameJson(a[index], b[index])) return false;
    }
    return true;
  }
  const keys = Object.keys(a);
  if (keys.length !== Object.keys(b).length) return false;
  for (const key of keys) {
    if (!Object.hasOwn(b, key) || !sameJson(a[key], b[key])) return false;
  }
  return true;
}

/**
 * What decoding a body costs beyond its bytes, as cadgen's writer counted it: the
 * client's mesh data (positions, normals, u32 indices, the edges' points and segment pairs),
 * never less than what `buildComponentMeshData` builds, and an allowance per face and edge
 * for the selector tables.
 */
export function tessellationDecodedBytes(counts) {
  const values = COUNT_FIELDS.map((field) => counts?.[field]);
  if (!values.every(validCount) || counts.edgePointCount < 2 * counts.edgeCount
    || (counts.edgeCount === 0) !== (counts.edgePointCount === 0)) {
    throw new TypeError("invalid tessellation size facts");
  }
  const decodedBytes = 24 * counts.vertexCount + 4 * counts.indexCount + 12 * counts.edgePointCount
    + 8 * (counts.edgePointCount - counts.edgeCount) + 256 * (counts.faceCount + counts.edgeCount);
  if (!Number.isSafeInteger(decodedBytes)) {
    throw new TypeError("tessellation decoded size exceeds the safe integer range");
  }
  return decodedBytes;
}

function meshSections({ vertexCount, indexCount, faceCount, edgeCount, edgePointCount }) {
  const sections = [];
  if (vertexCount) {
    const width = vertexCount <= UNSIGNED_SHORT_VERTEX_LIMIT ? 2 : 4;
    sections.push(["POSITION", 12 * vertexCount, 34962], ["NORMAL", 12 * vertexCount, 34962],
      ["indices", width * indexCount, 34963]);
  }
  if (faceCount) sections.push(["cadgen.faces", 4 * MESH_TABLE_COLUMNS * faceCount, null]);
  if (edgeCount) {
    sections.push(["cadgen.edges", 4 * MESH_TABLE_COLUMNS * edgeCount, null],
      ["cadgen.edgePoints", 12 * edgePointCount, null]);
  }
  return sections;
}

/**
 * The JSON chunk of a body whose `extras.cadgen` values (part, bounds, colours) and counts these
 * are: what cadgen wrote and what a body must hold to be
 * read.
 */
export function canonicalMeshGltf(cad, counts) {
  const views = [];
  const tables = {};
  let offset = 0;
  for (const [name, byteLength, target] of meshSections(counts)) {
    const view = { buffer: 0, byteOffset: offset, byteLength };
    if (target !== null) {
      view.target = target;
    } else {
      view.name = name;
      tables[name.slice("cadgen.".length)] = views.length;
    }
    views.push(view);
    offset += align4(byteLength);
  }
  const extras = { ...cad, edgeClasses: [...MESH_EDGE_CLASSES] };
  for (const [name, count] of [["faces", counts.faceCount], ["edges", counts.edgeCount], ["edgePoints", counts.edgePointCount]]) {
    if (name in tables) extras[name] = { bufferView: tables[name], count };
  }
  const gltf = { asset: { version: "2.0", generator: "cadgen" }, extras: { cadgen: extras } };
  if (counts.vertexCount) {
    Object.assign(gltf, {
      scene: 0,
      scenes: [{ nodes: [0] }],
      nodes: [{ mesh: 0, rotation: [...NODE_ROTATION], scale: [...NODE_SCALE] }],
      meshes: [{ primitives: [{ attributes: { POSITION: 0, NORMAL: 1 }, indices: 2, mode: 4 }] }],
      accessors: [
        { bufferView: 0, componentType: 5126, count: counts.vertexCount, type: "VEC3",
          min: [...cad.bounds.min], max: [...cad.bounds.max] },
        { bufferView: 1, componentType: 5126, count: counts.vertexCount, type: "VEC3" },
        { bufferView: 2, componentType: counts.vertexCount <= UNSIGNED_SHORT_VERTEX_LIMIT ? 5123 : 5125,
          count: counts.indexCount, type: "SCALAR" },
      ],
    });
  }
  if (views.length) {
    gltf.bufferViews = views;
    gltf.buffers = [{ byteLength: offset }];
  }
  return gltf;
}

function validCad(cad) {
  if (!isObject(cad) || !isPartKey(cad.part)) return false;
  const bounds = cad.bounds;
  if (!isObject(bounds) || Object.keys(bounds).length !== 2
    || !finiteTuple(bounds.min, 3) || !finiteTuple(bounds.max, 3)
    || bounds.min.some((value, index) => value > bounds.max[index])) return false;
  if (Object.hasOwn(cad, UNMESHED)) {
    const unmeshed = cad[UNMESHED];
    if (!Array.isArray(unmeshed) || !unmeshed.length
      || unmeshed.some((ord, index) => !Number.isSafeInteger(ord) || ord < 1 || (index && ord <= unmeshed[index - 1]))) {
      return false;
    }
  }
  return Array.isArray(cad.faceColors) && cad.faceColors.every((color) => finiteTuple(color, 4));
}

// A table reference's count: 0 when the table is absent, null when the reference is malformed.
function tableCount(reference) {
  if (reference === undefined || reference === null) return 0;
  return isObject(reference) && Number.isSafeInteger(reference.count) && reference.count > 0
    ? reference.count : null;
}

// The rows rise by ordinal and cover `total` from 0 in order; `valid(count, reference)` holds of
// each row's count and reference.
function validTable(table, total, valid) {
  let previous = 0;
  let cursor = 0;
  for (let row = 0; row < table.length; row += MESH_TABLE_COLUMNS) {
    const ord = table[row];
    const count = table[row + 2];
    if (ord <= previous || table[row + 1] !== cursor || !valid(count, table[row + 3])) return false;
    previous = ord;
    cursor += count;
  }
  return cursor === total;
}

// The validated body: its part, counts and every section viewed in place (copied when the
// bytes do not sit on a 4-byte boundary). `part`, when given, is the part it must be.
function decodeBody(bytes, part) {
  if (!(bytes instanceof Uint8Array) || bytes.length < 20) return null;
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (view.getUint32(0, true) !== GLB_MAGIC || view.getUint32(4, true) !== GLB_VERSION
    || view.getUint32(8, true) !== bytes.length || view.getUint32(16, true) !== JSON_CHUNK) return null;
  const jsonLength = view.getUint32(12, true);
  if (jsonLength === 0 || jsonLength > MESH_MAX_JSON_BYTES || jsonLength % 4 !== 0
    || 20 + jsonLength > bytes.length) return null;
  let binStart = 20 + jsonLength;
  let binLength = 0;
  if (binStart < bytes.length) {
    if (binStart + 8 > bytes.length) return null;
    binLength = view.getUint32(binStart, true);
    if (view.getUint32(binStart + 4, true) !== BIN_CHUNK || binLength === 0 || binLength % 4 !== 0
      || binStart + 8 + binLength !== bytes.length) return null;
    binStart += 8;
  }
  const gltf = JSON.parse(JSON_TEXT.decode(bytes.subarray(20, 20 + jsonLength)));
  const cad = isObject(gltf) && isObject(gltf.extras) ? gltf.extras.cadgen : null;
  if (!validCad(cad) || (part !== undefined && cad.part !== part)) return null;
  let vertexCount = 0;
  let indexCount = 0;
  if (gltf.accessors !== undefined) {
    const accessors = gltf.accessors;
    if (!Array.isArray(accessors) || accessors.length !== 3 || !accessors.every(isObject)
      || !validCount(accessors[0].count) || !validCount(accessors[2].count)) return null;
    vertexCount = accessors[0].count;
    indexCount = accessors[2].count;
    if (!vertexCount || !indexCount || indexCount % 3 !== 0) return null;
  }
  const counts = {
    vertexCount,
    indexCount,
    faceCount: tableCount(cad.faces),
    edgeCount: tableCount(cad.edges),
    edgePointCount: tableCount(cad.edgePoints),
  };
  if (counts.faceCount === null || counts.edgeCount === null || counts.edgePointCount === null
    || (counts.edgeCount === 0) !== (counts.edgePointCount === 0)) return null;
  const values = {};
  for (const name of CAD_VALUES) values[name] = cad[name];
  if (Object.hasOwn(cad, UNMESHED)) values[UNMESHED] = cad[UNMESHED];
  const canonical = canonicalMeshGltf(values, counts);
  if (!sameJson(gltf, canonical) || binLength !== (canonical.buffers?.[0].byteLength ?? 0)) return null;

  const views = canonical.bufferViews || [];
  const viewOf = (name) => views[name === "POSITION" ? 0 : name === "NORMAL" ? 1 : name === "indices" ? 2
    : views.findIndex((entry) => entry.name === name)];
  const take = (name, Ctor) => {
    const entry = viewOf(name);
    const start = bytes.byteOffset + binStart + entry.byteOffset;
    const length = entry.byteLength / Ctor.BYTES_PER_ELEMENT;
    return start % 4 === 0
      ? new Ctor(bytes.buffer, start, length)
      : new Ctor(bytes.buffer.slice(start, start + entry.byteLength));
  };
  const IndexArray = vertexCount <= UNSIGNED_SHORT_VERTEX_LIMIT ? Uint16Array : Uint32Array;
  const component = {
    positions: vertexCount ? take("POSITION", Float32Array) : new Float32Array(0),
    normals: vertexCount ? take("NORMAL", Float32Array) : new Float32Array(0),
    indices: vertexCount ? take("indices", IndexArray) : new Uint32Array(0),
    faceTable: counts.faceCount ? take("cadgen.faces", Uint32Array) : new Uint32Array(0),
    edgeTable: counts.edgeCount ? take("cadgen.edges", Uint32Array) : new Uint32Array(0),
    edgePoints: counts.edgeCount ? take("cadgen.edgePoints", Float32Array) : new Float32Array(0),
    faceColors: cad.faceColors,
    bounds: cad.bounds,
    unmeshedFaces: Object.freeze([...(cad[UNMESHED] || [])]),
  };
  const palette = cad.faceColors.length;
  if (!validTable(component.faceTable, indexCount, (count, color) => count % 3 === 0 && color <= palette)
    || !validTable(component.edgeTable, counts.edgePointCount,
      (count, edgeClass) => count >= 2 && edgeClass < MESH_EDGE_CLASSES.length)) return null;
  if (component.unmeshedFaces.length) {
    // Each one is a face of the table, and an empty one.
    const empty = new Set();
    for (let row = 0; row < component.faceTable.length; row += MESH_TABLE_COLUMNS) {
      if (!component.faceTable[row + 2]) empty.add(component.faceTable[row]);
    }
    if (!component.unmeshedFaces.every((ord) => empty.has(ord))) return null;
  }
  return { component, part: cad.part, counts };
}

// A record's facts about one of its objects, exactly `fields` (and `optional`, when present).
function objectFacts(value, fields, optional = []) {
  if (!isObject(value)) return null;
  const keys = Object.keys(value);
  if (keys.some((key) => !fields.includes(key) && !optional.includes(key)) || fields.some((key) => !Object.hasOwn(value, key))) {
    return null;
  }
  if (!SHA256_RE.test(value.object)) return null;
  const facts = { object: value.object };
  for (const key of [...fields, ...optional]) {
    if (key === "object" || !Object.hasOwn(value, key)) continue;
    if (!validCount(value[key])) return null;
    facts[key] = value[key];
  }
  return facts;
}

/**
 * A part's row: its key and its record, checked and frozen —
 * `{part, glb: {object, byteLength, decodedBytes, <counts>, unmeshedFaceCount?}, selectors:
 * {object, byteLength, faceCount, edgeCount, vertexCount}}`. Null for anything else.
 */
export function partRow(part, record) {
  try {
    if (!isPartKey(part) || !isObject(record)) return null;
    const keys = Object.keys(record).filter((key) => key !== "part");
    if (keys.length !== 2 || !keys.includes("glb") || !keys.includes("selectors")
      || (record.part !== undefined && record.part !== part)) return null;
    const glb = objectFacts(record.glb, GLB_FIELDS, [UNMESHED_COUNT]);
    const selectors = objectFacts(record.selectors, SELECTOR_FIELDS);
    if (!glb || !selectors || glb.byteLength < 20 || glb.byteLength % 4 !== 0 || selectors.byteLength < 1
      || glb.decodedBytes !== tessellationDecodedBytes(glb)) return null;
    if (Object.hasOwn(glb, UNMESHED_COUNT) && (glb[UNMESHED_COUNT] < 1 || glb[UNMESHED_COUNT] > glb.faceCount)) return null;
    return Object.freeze({ part, glb: Object.freeze(glb), selectors: Object.freeze(selectors) });
  } catch {
    return null;
  }
}

/**
 * Whether `bytes` are the display mesh `row` names: its part's body, whose framing and counts are
 * the record's. The content address is the reader's to check.
 */
export function partMeshMatches(bytes, row) {
  try {
    const body = decodeBody(bytes, row?.part);
    if (!body) return false;
    const facts = { byteLength: bytes.byteLength, decodedBytes: tessellationDecodedBytes(body.counts), ...body.counts,
      ...(body.component.unmeshedFaces.length ? { [UNMESHED_COUNT]: body.component.unmeshedFaces.length } : {}) };
    const { object: _object, ...expected } = row.glb;
    return sameJson(facts, expected);
  } catch {
    return false;
  }
}

/**
 * One stored mesh, decoded: `component` holds its sections viewed in place -- `positions` and
 * `normals` (f32 xyz), `indices` (u16 or u32), `faceTable` and `edgeTable` (u32 rows of
 * MESH_TABLE_COLUMNS), `edgePoints` (f32 xyz) -- with its face colour palette and bounds, and
 * `unmeshedFaces`: the faces no mesher could cover, which it does not draw. `meshFaceRanges` and
 * `meshEdgePolylines` read the tables as objects. Null for anything that is not a valid body of
 * `part` (any part when it is not given): a corrupt entry is a miss, never an error.
 */
export function decodeComponentTessellation(bytes, { part } = {}) {
  try {
    const body = decodeBody(bytes, part);
    return body ? { component: body.component, part: body.part } : null;
  } catch {
    return null;
  }
}

/** A decoded component's faces: `[{ord, color, indexStart, indexCount}]`, a colour its palette row or null. */
export function meshFaceRanges(component) {
  const table = component.faceTable;
  const ranges = [];
  for (let row = 0; row < table.length; row += MESH_TABLE_COLUMNS) {
    const color = table[row + 3];
    ranges.push({
      ord: table[row],
      color: color ? component.faceColors[color - 1] : null,
      indexStart: table[row + 1],
      indexCount: table[row + 2],
    });
  }
  return ranges;
}

/** A decoded component's edges: `[{ord, visibilityClass, polyline}]`, each polyline a view of its points. */
export function meshEdgePolylines(component) {
  const table = component.edgeTable;
  const points = component.edgePoints;
  const edges = [];
  for (let row = 0; row < table.length; row += MESH_TABLE_COLUMNS) {
    const start = table[row + 1] * 3;
    edges.push({
      ord: table[row],
      visibilityClass: MESH_EDGE_CLASSES[table[row + 3]],
      polyline: points.subarray(start, start + table[row + 2] * 3),
    });
  }
  return edges;
}
