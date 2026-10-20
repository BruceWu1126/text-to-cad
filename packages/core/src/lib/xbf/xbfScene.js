// A cadgen engine document's scene (`../viewer/sceneContract.js`): the ONE builder of an
// `.xbf`'s scene. The viewer's xbf renderer (`@text-to-cad/ui` renderers/xbf) and the snapshot
// CLI's headless stage (`common/headlessScene.js`) both call it, so neither can draw a document
// the other does not.
//
// It is drawn from what the engine serves and nothing else (`xbfDocument.js`):
//
// - One `BufferGeometry` per prototype, its attributes the part buffer's own typed-array views
//   (positions, the surface's normals, the triangle indices), built once and kept on the part,
//   so every instance, every version that keeps the part and every sibling scene draws the one
//   upload (`sharedGeometry.js`). A prototype with painted faces wears its paints as groups of a
//   geometry over those same buffers.
// - One placed group per instance at its world matrix, holding a `Mesh` over the prototype's
//   geometry and its CAD edges.
// - CAD edges from the edge table and its polylines, as line segments grouped by class: feature
//   (boundary and non-manifold edges drawn as feature edges), tangent and seam, each a line of
//   its own weight (`cadInk.js`'s palette), never the tessellation's triangles.
// - Faces by the face table's index ranges, edges by the edge table's polylines; every element
//   named by its ordinal, with the element name the document gives it. A pick takes an edge
//   within a few pixels of the pointer, else the face under it, as a STEP's Select does.
// - A face the mesher left without triangles is not drawn and is reported (`unmeshed`).
// - Each instance's placed group hangs in a carrier of its own, which Explode moves
//   (`sceneExplode.js`, a STEP's layout over the document's tree).
//
// The display modes are the shared view settings' (`common/viewSettings.js`), resolved by the
// host as for any family: the surface look (`setSurfaceLook`: Solid, Shaded, Parts, X-ray; Hidden
// line's depth-only surfaces and Wireframe's none) and the edges the mode draws (`setEdgeLook`).
// Selection is drawn as a STEP's is: a lit part blended toward the highlight with its edges in
// it (`setHighlight`), a picked face filled and outlined and a picked edge drawn heavy in the
// highlight colour (`setElementHighlight`), an isolated part's neighbours dimmed (`setFocusedParts`).

import { LineMaterial } from "three/examples/jsm/lines/LineMaterial.js";
import { LineSegments2 } from "three/examples/jsm/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/examples/jsm/lines/LineSegmentsGeometry.js";
import { resolveCadEdgeSettings } from "../../common/cadInk.js";
import { createScreenSpaceLineSegments, topologyLineDepthBiasForWidth } from "../../common/renderEdges.js";
import { syncMaterialClipPlanes } from "../viewer/clipPlane.js";
import { FOCUSED_DIMMED_SURFACE_OPACITY } from "../viewer/partVisualState.js";
import { scheduleRuntimeRaycastBvh } from "../viewer/raycastBvh.js";
import { REFERENCE_HOVER_COLOR } from "../viewer/referenceGeometry.js";
import { createSceneExplode } from "../viewer/sceneExplode.js";
import { createSceneHighlight } from "../viewer/sceneHighlight.js";
import { createSurfaceLook } from "../viewer/surfaceLook.js";
import { releaseGeometry, shareGeometry } from "../render/sharedGeometry.js";
import { XBF_EDGE_CLASS_NAMES, xbfEdgeLine, xbmFaceRow, xbmEdgeRow, xbmTriangleFaceRows } from "./xbm.js";
import { xbfKinematics, xbfPosedMatrices } from "./xbfKinematics.js";

export const XBF_SCENE_UNITS = Object.freeze({ unit: "mm", perWorld: 1 });
export const XBF_EDGE_LINES = Object.freeze(["feature", "tangent", "seam"]);
// How near the pointer, in CSS pixels, an edge is picked before the face under it: a STEP's
// Select's, a press's and a resting pointer's (`useStepPicking.js`).
export const XBF_PICK_PIXELS = Object.freeze({ edge: 10, edgeHover: 6 });
const DEFAULT_SURFACE = 0xb8bcc4;
const HIGHLIGHT_ORDER = 30;
// A STEP's reference highlight (`cadInk.js`): one colour, opaque, its lines this many
// drawing-buffer pixels wide, whether the element is picked or under the pointer.
const INK = resolveCadEdgeSettings();
const EDGE_HIGHLIGHT_PIXELS = INK.highlightThickness;

/** An element's id: its instance, its kind and its ordinal (`i3/face/12`). An instance's is `i3`. */
export function xbfElementId(instance, kind, ordinal) {
  return kind ? `i${instance}/${kind}/${ordinal}` : `i${instance}`;
}

/** The parts of an element or instance id, or null. */
export function parseXbfElementId(id) {
  const match = /^i(\d+)(?:\/(face|edge|vertex)\/(\d+))?$/.exec(String(id || ""));
  if (!match) return null;
  return { instance: Number(match[1]), kind: match[2] || null, ordinal: match[3] ? Number(match[3]) : null };
}

function linearColor(THREE, rgba) {
  return new THREE.Color().setRGB(Number(rgba[0]) || 0, Number(rgba[1]) || 0, Number(rgba[2]) || 0, THREE.LinearSRGBColorSpace);
}

/**
 * The geometry of one part, made once over its buffer's views and kept on the part
 * (`part.hold`): the surface, the edges' line geometries by class over one shared point
 * attribute, and the part's own box.
 */
function partGeometry(THREE, part) {
  return part.hold("three:geometry", () => {
    const mesh = part.mesh;
    const surface = new THREE.BufferGeometry();
    surface.name = part.key;
    surface.setAttribute("position", new THREE.BufferAttribute(mesh.positions, 3));
    surface.setAttribute("normal", new THREE.BufferAttribute(mesh.normals, 3));
    surface.setIndex(new THREE.BufferAttribute(mesh.indices, 1));
    const points = new THREE.BufferAttribute(mesh.edgePoints, 3);
    const segments = { feature: [], tangent: [], seam: [] };
    for (let row = 0; row < mesh.edges.length / 4; row += 1) {
      const first = mesh.edges[row * 4 + 1], count = mesh.edges[row * 4 + 2];
      const into = segments[xbfEdgeLine(mesh.edges[row * 4 + 3])];
      for (let at = first; at + 1 < first + count; at += 1) into.push(at, at + 1);
    }
    const lines = {};
    for (const line of XBF_EDGE_LINES) {
      if (!segments[line].length) continue;
      const geometry = new THREE.BufferGeometry();
      geometry.name = `${part.key}:${line}`;
      geometry.setAttribute("position", points);
      geometry.setIndex(new THREE.BufferAttribute(new Uint32Array(segments[line]), 1));
      lines[line] = geometry;
    }
    const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
    const grow = (array, stride, offset) => {
      for (let at = offset; at + 2 < array.length; at += stride) {
        for (let axis = 0; axis < 3; axis += 1) {
          const value = array[at + axis];
          if (value < min[axis]) min[axis] = value;
          if (value > max[axis]) max[axis] = value;
        }
      }
    };
    grow(mesh.positions, 3, 0);
    grow(mesh.edgePoints, 3, 0);
    grow(mesh.vertices, 4, 1);
    const box = new THREE.Box3();
    if (min[0] <= max[0]) box.set(new THREE.Vector3(...min), new THREE.Vector3(...max));
    if (!box.isEmpty()) {
      surface.boundingBox = box.clone();
      surface.boundingSphere = box.getBoundingSphere(new THREE.Sphere());
      for (const geometry of Object.values(lines)) {
        geometry.boundingBox = box.clone();
        geometry.boundingSphere = surface.boundingSphere.clone();
      }
    }
    return { surface, lines, points, box, triangleFaces: null };
  }, held => {
    held.surface.dispose();
    for (const geometry of Object.values(held.lines)) geometry.dispose();
  });
}

function triangleFaces(held, part) {
  held.triangleFaces ||= xbmTriangleFaceRows(part.mesh);
  return held.triangleFaces;
}

/**
 * A face's outline, as segment pairs in the part's frame: the sides of its triangles that no
 * other of its triangles shares, read by where their ends are (so a seam the mesher split into
 * two rows of nodes is outlined as a STEP's face boundary is, along the seam too).
 * @returns {Float32Array}
 */
export function xbfFaceOutline(mesh, ordinal) {
  const row = xbmFaceRow(mesh, ordinal);
  if (row < 0 || !mesh.faces[row * 3 + 2]) return new Float32Array(0);
  const first = mesh.faces[row * 3 + 1], count = mesh.faces[row * 3 + 2];
  const p = mesh.positions;
  const key = node => `${p[node * 3]},${p[node * 3 + 1]},${p[node * 3 + 2]}`;
  const sides = new Map();
  for (let at = first; at < first + count; at += 3) {
    const corners = [mesh.indices[at], mesh.indices[at + 1], mesh.indices[at + 2]];
    for (let side = 0; side < 3; side += 1) {
      const a = corners[side], b = corners[(side + 1) % 3];
      const ka = key(a), kb = key(b);
      if (ka === kb) continue;
      const name = ka < kb ? `${ka}|${kb}` : `${kb}|${ka}`;
      const found = sides.get(name);
      if (found) found.uses += 1; else sides.set(name, { a, b, uses: 1 });
    }
  }
  const out = [];
  for (const { a, b, uses } of sides.values()) {
    if (uses !== 1) continue;
    out.push(p[a * 3], p[a * 3 + 1], p[a * 3 + 2], p[b * 3], p[b * 3 + 1], p[b * 3 + 2]);
  }
  return new Float32Array(out);
}

/** A part's volume from its triangles (the divergence theorem over the closed mesh): approximate, as a STEP's card says. */
export function xbfMeshVolume(mesh) {
  const p = mesh.positions, index = mesh.indices;
  let sum = 0;
  for (let at = 0; at + 2 < index.length; at += 3) {
    const a = index[at] * 3, b = index[at + 1] * 3, c = index[at + 2] * 3;
    sum += p[a] * (p[b + 1] * p[c + 2] - p[b + 2] * p[c + 1])
      - p[a + 1] * (p[b] * p[c + 2] - p[b + 2] * p[c])
      + p[a + 2] * (p[b] * p[c + 1] - p[b + 1] * p[c]);
  }
  return Math.abs(sum / 6);
}

/**
 * @typedef {object} XbfHit
 * @property {string} id  The element's id (`xbfElementId`), or the instance's for `pick`.
 * @property {"face" | "edge" | null} kind  Null: the instance itself (`pickElement`'s `parts`).
 * @property {number} instance  The instance's index in the structure.
 * @property {string} key  Its prototype's geometry key: what the engine's facts are asked of.
 * @property {number | null} ordinal
 * @property {string} name  The element's name in the document, or "".
 * @property {number[]} point  Where, in world coordinates.
 */

/**
 * The scene of a loaded engine document (`loadXbfDocument`).
 *
 * @param {typeof import("three")} THREE
 * @param {import("./xbfDocument.js").XbfDocument} document  Its parts' geometry is built over
 *   their buffers once and kept on the parts; the scene owns what it adds (materials, painted
 *   groups, lines' materials, highlights) and the document's holder releases the rest.
 * @returns {import("../viewer/sceneContract.js").KitScene & object}
 */
export function createXbfScene(THREE, document) {
  const { structure } = document;
  const prototypes = structure.prototypes;
  const root = new THREE.Group();
  root.name = "xbf-document";
  const ownedGeometries = [];
  const materials = new Map();
  const material = (rgba) => {
    const name = rgba ? rgba.join(",") : "";
    let found = materials.get(name);
    if (!found) {
      const alpha = rgba ? Math.min(Math.max(Number(rgba[3] ?? 1), 0), 1) : 1;
      found = new THREE.MeshPhysicalMaterial({
        color: rgba ? linearColor(THREE, rgba) : DEFAULT_SURFACE, side: THREE.DoubleSide,
        transparent: alpha < 1, opacity: alpha, depthWrite: alpha >= 1,
        // The edges lie on the surfaces: the surfaces step back a hair so the edges win the depth test.
        polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1
      });
      found.userData.cadSourceColor = Boolean(rgba);
      materials.set(name, found);
    }
    return found;
  };
  // Edges draw after the surfaces, in the transparent pass (a see-through X-ray surface would
  // otherwise wash out the edges under it), as a STEP's edge pass does.
  const lineMaterials = Object.fromEntries(XBF_EDGE_LINES.map(line => [line, new THREE.LineBasicMaterial({ color: 0x253443, transparent: true })]));
  const hiddenDepth = new THREE.MeshBasicMaterial({ colorWrite: false, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 });

  // A prototype's surface geometry: the part's, or for painted faces one over its buffers with
  // a group per face and a material per paint.
  const surfaces = prototypes.map((prototype) => {
    const part = document.parts.get(String(prototype.key));
    if (!part) throw new Error(`the document names part ${prototype.key} and the engine sent no buffer for it`);
    const held = partGeometry(THREE, part);
    const painted = Object.entries(prototype.faceColors || {});
    if (!painted.length) return { part, held, geometry: held.surface, paints: [] };
    const geometry = shareGeometry(THREE, held.surface);
    ownedGeometries.push(geometry);
    const byOrdinal = new Map(painted);
    const paints = [];
    const slot = new Map();
    const faces = part.mesh.faces;
    for (let row = 0; row < faces.length; row += 3) {
      const paint = byOrdinal.get(String(faces[row]));
      let index = 0;
      if (paint) {
        const name = paint.join(",");
        if (!slot.has(name)) { slot.set(name, paints.length + 1); paints.push(paint); }
        index = slot.get(name);
      }
      if (faces[row + 2]) geometry.addGroup(faces[row + 1], faces[row + 2], index);
    }
    return { part, held, geometry, paints };
  });

  // Each instance's place in the document's tree ("o1.2.3", the root "o1"): what Explode's layout
  // groups its levels by, as a STEP's occurrence paths are.
  const treePaths = new Map();
  const walk = (node, path) => {
    if (Number.isInteger(node?.instance)) treePaths.set(node.instance, path);
    (Array.isArray(node?.children) ? node.children : []).forEach((child, at) => walk(child, `${path}.${at + 1}`));
  };
  if (structure.tree) walk(structure.tree, "o1");

  const instances = structure.instances.map((instance, index) => {
    const prototype = prototypes[instance.prototype];
    const surface = surfaces[instance.prototype];
    const rgba = instance.color || prototype.color || null;
    // The carrier is Explode's to move; the placed group under it stands where the structure (or
    // the pose) places the instance.
    const carrier = new THREE.Group();
    carrier.name = `${instance.path} carrier`;
    const group = new THREE.Group();
    group.name = instance.path;
    group.matrixAutoUpdate = false;
    group.matrix.fromArray(instance.matrix);
    group.userData.xbfInstance = index;
    const mesh = new THREE.Mesh(surface.geometry, surface.paints.length ? [material(rgba), ...surface.paints.map(material)] : material(rgba));
    mesh.name = prototype.name || instance.path;
    mesh.castShadow = true;
    // What Parts colours it by, and what a highlight and an annotation name it by.
    mesh.userData.cadFillIndex = index;
    mesh.userData.partId = xbfElementId(index);
    mesh.userData.xbfInstance = index;
    group.add(mesh);
    const lines = {};
    for (const line of XBF_EDGE_LINES) {
      const geometry = surface.held.lines[line];
      if (!geometry) continue;
      const object = new THREE.LineSegments(geometry, lineMaterials[line]);
      object.name = `${line} edges`;
      object.raycast = () => {};
      object.renderOrder = 1;
      lines[line] = object;
      group.add(object);
    }
    carrier.add(group);
    root.add(carrier);
    // `matrix`: where the instance stands now, the structure's placement or the pose's.
    return { index, instance, prototype, surface, carrier, group, mesh, lines, record: { mesh }, matrix: instance.matrix };
  });
  root.updateMatrixWorld(true);

  const instanceBox = entry => (entry.surface.held.box.isEmpty() ? null : entry.surface.held.box.clone().applyMatrix4(entry.group.matrixWorld));
  const measureBounds = () => {
    const box = new THREE.Box3();
    for (const entry of instances) {
      const placed = instanceBox(entry);
      if (placed) box.union(placed);
    }
    return box.isEmpty() ? { min: [0, 0, 0], max: [0, 0, 0] } : { min: box.min.toArray(), max: box.max.toArray() };
  };
  const bounds = measureBounds();
  let posedBounds = bounds;
  // Explode moves the carriers, by the document's tree (`sceneExplode.js`), every instance a part.
  const explode = createSceneExplode(THREE, root, instances
    .filter(entry => treePaths.has(entry.index))
    .sort((left, right) => treePaths.get(left.index).localeCompare(treePaths.get(right.index), "en", { numeric: true }))
    .map(entry => ({ object: entry.carrier, path: treePaths.get(entry.index), meshes: [entry.mesh] })), bounds);
  let explodedBounds = null;
  let explodeAmount = 0;
  const kinematics = xbfKinematics(structure);
  const records = instances.map(entry => entry.record);
  if (records.length) scheduleRuntimeRaycastBvh({ displayRecords: records }, { deferUntilRaycast: true });

  const look = createSurfaceLook(THREE, root);
  const ink = createSceneHighlight(THREE, look);
  let lastLook = null;
  let surfaceStyle = "shaded";
  let edgeLook = { enabled: true, visibility: "visible", color: null };
  let clipPlanes = [];
  let partInk = { hovered: [], selected: [] };
  const hidden = new Set();
  // Isolation: the instances isolated; every other is dimmed and out of reach, as a STEP's.
  const focused = new Set();
  const dimmed = index => focused.size > 0 && !focused.has(index);
  let disposed = false;
  const overlay = { hovered: null, selected: [] };
  const overlayObjects = new Map();
  // A lit part's edges, as a STEP part's: in the selection's colour, or the hover's.
  const partLineMaterials = {
    selected: new THREE.LineBasicMaterial({ color: INK.highlightColor, transparent: true }),
    hovered: new THREE.LineBasicMaterial({ color: REFERENCE_HOVER_COLOR, transparent: true })
  };

  const applyVisibility = () => {
    for (const entry of instances) {
      const shown = !hidden.has(entry.index);
      entry.group.visible = shown;
      entry.mesh.visible = shown && surfaceStyle !== "off";
      // An isolated part's neighbours keep no edges, as a STEP's do not.
      for (const object of Object.values(entry.lines)) object.visible = shown && edgeLook.enabled && !dimmed(entry.index);
    }
  };
  const applyEdgeLook = () => {
    const palette = resolveCadEdgeSettings(edgeLook.color ? { color: edgeLook.color } : null);
    const onTop = edgeLook.visibility === "all";
    for (const target of [...XBF_EDGE_LINES.map(line => lineMaterials[line]), ...Object.values(partLineMaterials)]) {
      // Hidden edges, where the mode shows them, are drawn through the surfaces.
      if (target.depthTest === onTop) { target.depthTest = !onTop; target.needsUpdate = true; }
      syncMaterialClipPlanes(target, clipPlanes);
    }
    for (const line of XBF_EDGE_LINES) lineMaterials[line].color.set(palette.classes[line]?.color || palette.color);
    applyVisibility();
  };
  const recordsFor = ids => ids.map(id => instances[parseXbfElementId(id)?.instance]?.record).filter(Boolean);
  const indicesOf = ids => new Set(ids.map(id => parseXbfElementId(id)?.instance).filter(index => instances[index]));
  const applySurfaces = () => {
    const visible = ids => ids.filter(id => !dimmed(parseXbfElementId(id)?.instance));
    ink.apply(lastLook, { hovered: recordsFor(visible(partInk.hovered)), selected: recordsFor(visible(partInk.selected)) }, clipPlanes);
    // Hidden line: the surfaces only hide what is behind them, in no colour of their own.
    if (surfaceStyle === "hidden") {
      for (const entry of instances) entry.mesh.material = Array.isArray(entry.mesh.material) ? entry.mesh.material.map(() => hiddenDepth) : hiddenDepth;
    } else if (focused.size) {
      // An isolated part's neighbours: all but gone, as a STEP's are.
      for (const entry of instances) {
        if (!dimmed(entry.index)) continue;
        for (const paint of Array.isArray(entry.mesh.material) ? entry.mesh.material : [entry.mesh.material]) {
          if (!paint.transparent || paint.depthWrite) { paint.transparent = true; paint.depthWrite = false; paint.needsUpdate = true; }
          paint.opacity = Math.min(paint.opacity, FOCUSED_DIMMED_SURFACE_OPACITY);
        }
      }
    }
    syncMaterialClipPlanes(hiddenDepth, clipPlanes);
    // A lit part's edges in its ink; the selection's over the hover's.
    const selected = indicesOf(visible(partInk.selected)), hovered = indicesOf(visible(partInk.hovered));
    for (const entry of instances) {
      const state = selected.has(entry.index) ? "selected" : hovered.has(entry.index) ? "hovered" : null;
      for (const [line, object] of Object.entries(entry.lines)) {
        object.material = state ? partLineMaterials[state] : lineMaterials[line];
        // Over the lit surface, which is drawn after its neighbours (`sceneHighlight.js`).
        object.renderOrder = state ? 24 : 1;
      }
    }
    applyVisibility();
  };

  // --- picking -------------------------------------------------------------------------

  const raycaster = new THREE.Raycaster();
  const kept = point => clipPlanes.every(plane => plane.distanceToPoint(point) >= 0);
  // What a pick can reach: a shown instance, not dimmed by an isolation (a STEP's isolation keeps
  // its neighbours out of reach), with surfaces drawn.
  const reachable = entry => !hidden.has(entry.index) && !dimmed(entry.index);
  const visibleMeshes = () => instances.filter(entry => reachable(entry) && surfaceStyle !== "off").map(entry => entry.mesh);
  const surfaceHit = (ray) => {
    raycaster.ray.copy(ray);
    return raycaster.intersectObjects(visibleMeshes(), false).find(hit => kept(hit.point)) || null;
  };
  const faceOf = (entry, hit) => {
    const rows = triangleFaces(entry.surface.held, entry.surface.part);
    const row = rows[hit.faceIndex] ?? -1;
    return row < 0 ? null : entry.surface.part.mesh.faces[row * 3];
  };
  const elementHit = (entry, kind, ordinal, point) => ({
    id: xbfElementId(entry.index, kind, ordinal), kind, instance: entry.index, key: String(entry.prototype.key), ordinal,
    name: kind === "face" ? (entry.prototype.faceNames || {})[String(ordinal)] || ""
      : kind === "edge" ? (entry.prototype.edgeNames || {})[String(ordinal)] || "" : "",
    point: point.toArray(), path: entry.instance.path, partName: entry.prototype.name || ""
  });

  /**
   * The edge of `entry` nearest the pointer on screen, within `pixels`, and not behind the surface
   * the pointer is on (`limit`: the hit's distance along the ray).
   */
  function nearestEdgeOnScreen(entry, ray, view, limit, pixels) {
    const { camera, width, height } = view;
    const matrix = new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse).multiply(entry.group.matrixWorld);
    const m = matrix.elements;
    const world = entry.group.matrixWorld.elements;
    const pointer = ray.at(Math.max(camera.near || 0, 1e-3) * 2, new THREE.Vector3()).applyMatrix4(
      new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse));
    const px = (pointer.x * 0.5 + 0.5) * width, py = (-pointer.y * 0.5 + 0.5) * height;
    const project = (x, y, z, out) => {
      const w = m[3] * x + m[7] * y + m[11] * z + m[15];
      if (!(w > 0)) return false;
      out[0] = ((m[0] * x + m[4] * y + m[8] * z + m[12]) / w * 0.5 + 0.5) * width;
      out[1] = (-(m[1] * x + m[5] * y + m[9] * z + m[13]) / w * 0.5 + 0.5) * height;
      return true;
    };
    const toWorld = (x, y, z) => new THREE.Vector3(
      world[0] * x + world[4] * y + world[8] * z + world[12],
      world[1] * x + world[5] * y + world[9] * z + world[13],
      world[2] * x + world[6] * y + world[10] * z + world[14]);
    const slack = Math.max(limit === Infinity ? 0 : limit * 0.02, 1e-6);
    const unhidden = (point) => kept(point) && (limit === Infinity || point.clone().sub(ray.origin).dot(ray.direction) <= limit + slack);
    const mesh = entry.surface.part.mesh;
    const a = [0, 0], b = [0, 0];
    let best = pixels;
    let found = null;
    const p = mesh.edgePoints, e = mesh.edges;
    for (let row = 0; row < e.length / 4; row += 1) {
      const first = e[row * 4 + 1], count = e[row * 4 + 2];
      for (let at = first; at + 1 < first + count; at += 1) {
        if (!project(p[at * 3], p[at * 3 + 1], p[at * 3 + 2], a) || !project(p[at * 3 + 3], p[at * 3 + 4], p[at * 3 + 5], b)) continue;
        const dx = b[0] - a[0], dy = b[1] - a[1];
        const t = Math.max(0, Math.min(1, ((px - a[0]) * dx + (py - a[1]) * dy) / (dx * dx + dy * dy || 1)));
        const d = Math.hypot(a[0] + t * dx - px, a[1] + t * dy - py);
        if (d >= best) continue;
        const point = toWorld(p[at * 3] + t * (p[at * 3 + 3] - p[at * 3]), p[at * 3 + 1] + t * (p[at * 3 + 4] - p[at * 3 + 1]),
          p[at * 3 + 2] + t * (p[at * 3 + 5] - p[at * 3 + 2]));
        if (!unhidden(point)) continue;
        best = d;
        found = { ...elementHit(entry, "edge", e[row * 4], point), pixels: d };
      }
    }
    return found;
  }

  // --- element highlights --------------------------------------------------------------

  // A STEP's reference highlight (`useStepHighlights.js`): a face filled in the highlight colour,
  // opaque, and outlined in it; an edge drawn in it, heavy and over everything -- its lines the
  // screen-space lines a STEP's are (`renderEdges.js`), so they are as wide at every pixel ratio.
  // A face or edge under the pointer is drawn as a picked one is.
  const fillMaterial = new THREE.MeshBasicMaterial({ color: INK.highlightColor, transparent: INK.highlightOpacity < 0.999, opacity: INK.highlightOpacity,
    depthWrite: false, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2, side: THREE.DoubleSide, toneMapped: false });
  // Every screen-space line material the scene has made, registered with the viewport that shows
  // it (`setLineRegistry`), as a STEP scene's are: the viewport keeps their `resolution` at its
  // drawing buffer's size across resizes and pixel-ratio changes, and keeps its pixel ratio while
  // the camera moves so their widths do not jump.
  const screenSpaceLineMaterials = new Set();
  let lineRegistry = null;
  const lineContext = {
    LineSegments2, LineSegmentsGeometry, LineMaterial,
    registerScreenSpaceLineMaterial: material => lineRegistry?.registerScreenSpaceLineMaterial?.(material),
    unregisterScreenSpaceLineMaterial: material => lineRegistry?.unregisterScreenSpaceLineMaterial?.(material)
  };
  const highlightLine = (positions, onTop) => {
    const line = createScreenSpaceLineSegments(lineContext, positions, {
      color: INK.highlightColor, opacity: INK.highlightOpacity, lineWidth: EDGE_HIGHLIGHT_PIXELS, renderOrder: HIGHLIGHT_ORDER + 1,
      depthTest: !onTop, depthWrite: false, depthBias: topologyLineDepthBiasForWidth(EDGE_HIGHLIGHT_PIXELS)
    }, screenSpaceLineMaterials);
    if (!line) return null;
    line.raycast = () => {};
    syncMaterialClipPlanes(line.material, clipPlanes);
    return line;
  };
  const outlines = new Map();
  const outlineOf = (entry, ordinal) => {
    const key = `${entry.prototype.key}:${ordinal}`;
    if (!outlines.has(key)) outlines.set(key, xbfFaceOutline(entry.surface.part.mesh, ordinal));
    return outlines.get(key);
  };
  function overlayFor(id, state) {
    const parsed = parseXbfElementId(id);
    const entry = parsed && instances[parsed.instance];
    if (!entry || !parsed.kind) return null;
    const mesh = entry.surface.part.mesh;
    const object = new THREE.Group();
    if (parsed.kind === "face") {
      const row = xbmFaceRow(mesh, parsed.ordinal);
      if (row < 0 || !mesh.faces[row * 3 + 2]) return null;
      // The part's own positions, and its index buffer's range for this face: a view, no copy.
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", entry.surface.held.surface.getAttribute("position"));
      const first = mesh.faces[row * 3 + 1], count = mesh.faces[row * 3 + 2];
      geometry.setIndex(new THREE.BufferAttribute(mesh.indices.subarray(first, first + count), 1));
      geometry.userData.xbfSharedPosition = true;
      const fill = new THREE.Mesh(geometry, fillMaterial);
      fill.renderOrder = HIGHLIGHT_ORDER;
      fill.raycast = () => {};
      object.add(fill);
      const outline = outlineOf(entry, parsed.ordinal);
      const line = outline.length ? highlightLine(outline, false) : null;
      if (line) object.add(line);
    } else if (parsed.kind === "edge") {
      const row = xbmEdgeRow(mesh, parsed.ordinal);
      if (row < 0) return null;
      const first = mesh.edges[row * 4 + 1], count = mesh.edges[row * 4 + 2];
      const points = mesh.edgePoints;
      const pairs = new Float32Array(Math.max(count - 1, 0) * 6);
      for (let at = 0; at + 1 < count; at += 1) pairs.set(points.subarray((first + at) * 3, (first + at + 2) * 3), at * 6);
      const line = highlightLine(pairs, true);
      if (line) object.add(line);
    } else {
      return null;
    }
    object.name = `${state} ${parsed.kind}`;
    entry.group.add(object);
    return object;
  }
  function dropOverlay(object) {
    object.removeFromParent();
    for (const child of object.children) {
      const geometry = child.geometry;
      // A face's fill draws the part's own positions: a renderer frees the buffers of every
      // attribute a disposed geometry holds, so that one is let go of first.
      if (geometry.userData.xbfSharedPosition) geometry.deleteAttribute("position");
      geometry.dispose();
      // A line's material is its own, registered with the viewport until now; the fill's is shared.
      child.userData.beforeDispose?.();
      if (child.material !== fillMaterial) child.material.dispose();
    }
  }
  function applyOverlays() {
    const wanted = new Map();
    for (const id of overlay.selected) wanted.set(id, "selected");
    // One drawing per element: picked and under the pointer, it is drawn once.
    if (overlay.hovered && !wanted.has(overlay.hovered)) wanted.set(overlay.hovered, "hovered");
    for (const [id, object] of overlayObjects) {
      if (wanted.get(id) !== object.userData.state) { dropOverlay(object); overlayObjects.delete(id); }
    }
    for (const [id, state] of wanted) {
      if (overlayObjects.has(id)) continue;
      const object = overlayFor(id, state);
      if (object) { object.userData.state = state; overlayObjects.set(id, object); }
    }
    syncOverlayClipPlanes();
  }
  function syncOverlayClipPlanes() {
    syncMaterialClipPlanes(fillMaterial, clipPlanes);
    for (const object of overlayObjects.values()) for (const child of object.children) syncMaterialClipPlanes(child.material, clipPlanes);
  }

  const unmeshed = surfaces.flatMap((surface, index) => surface.part.unmeshed.length
    ? [{ prototype: index, key: String(prototypes[index].key), name: prototypes[index].name || "", faces: surface.part.unmeshed }] : []);
  const triangleCount = surfaces.reduce((sum, surface) => sum + Math.floor(surface.part.mesh.indices.length / 3), 0);

  const scene = {
    object3D: root,
    /** Where the instances stand now: posed, or exploded. */
    get bounds() { return explodedBounds || posedBounds; },
    restBounds: bounds,
    /** The document's mechanism (`xbfKinematics.js`), or null: what Position poses. */
    kinematics,
    /**
     * Pose the mechanism: each instance's placed group moved to where the mechanism stands at
     * `values` (pair label → its values; null puts every instance back where the structure places
     * it). Nothing is fetched or rebuilt; picks and highlights follow the groups. Throws, by
     * name, for a mechanism that is not a tree.
     */
    setPose(values) {
      if (disposed || !kinematics) return;
      const matrices = values ? xbfPosedMatrices(structure, values) : structure.instances.map(instance => instance.matrix);
      for (const entry of instances) {
        entry.matrix = matrices[entry.index];
        entry.group.matrix.fromArray(entry.matrix);
      }
      root.updateMatrixWorld(true);
      posedBounds = measureBounds();
    },
    /** How many parts Explode moves apart: none for a document of one instance. */
    explodeParts: explode.parts,
    /** Explode's amount (0..1); true when it moved anything. */
    setExplode(amount) {
      if (disposed || !explode.parts) return false;
      const value = Math.min(Math.max(Number(amount) || 0, 0), 1);
      if (value === explodeAmount) return false;
      explodeAmount = value;
      explodedBounds = value > 0 ? explode.set(value) : (explode.set(0), null);
      return true;
    },
    units: XBF_SCENE_UNITS,
    /** How many parts Parts colours: every placed instance its own. */
    partCount: instances.length,
    document,
    structure,
    version: document.version,
    /** Faces the mesher left without triangles, by prototype: reported, not drawn. */
    unmeshed,
    triangleCount,
    // A document authors colours and no finish: Render wears the studio's surface over them.
    setSurfaceLook(next) {
      if (disposed) return;
      lastLook = next ? { ...next, authored: false } : null;
      surfaceStyle = next?.surface?.style === "hidden" || next?.surface?.style === "off" ? next.surface.style : "shaded";
      applySurfaces();
    },
    /** The edges the display mode draws (`resolveViewSettings`'s `edges`): on or off, through the surfaces or not, their ink. */
    setEdgeLook(next) {
      if (disposed) return;
      edgeLook = { enabled: next?.enabled !== false, visibility: next?.visibility === "all" ? "all" : "visible", color: next?.color || null };
      applyEdgeLook();
    },
    setClipPlanes(planes) {
      if (disposed) return;
      clipPlanes = Array.isArray(planes) ? planes : [];
      look.setClipPlanes(clipPlanes);
      syncMaterialClipPlanes(hiddenDepth, clipPlanes);
      for (const line of XBF_EDGE_LINES) syncMaterialClipPlanes(lineMaterials[line], clipPlanes);
      for (const paint of Object.values(partLineMaterials)) syncMaterialClipPlanes(paint, clipPlanes);
      syncOverlayClipPlanes();
    },
    /** Whether `id` names an instance (`i3`) of this document. */
    hasPart: id => Boolean(instances[parseXbfElementId(id)?.instance]) && !parseXbfElementId(id)?.kind,
    /** An instance as a chip names it. */
    describePart(id) {
      const entry = instances[parseXbfElementId(id)?.instance];
      if (!entry) return null;
      return { name: entry.prototype.name || entry.instance.path, path: entry.instance.path, prototype: entry.instance.prototype };
    },
    /**
     * What a chip's card measures of instances (ids `i3`): the box they make where they stand
     * now, and their volume from their triangles (approximate, as a STEP's is). Null for none.
     */
    measureParts(ids) {
      const box = new THREE.Box3();
      let volume = 0;
      let found = false;
      for (const index of indicesOf(ids)) {
        const entry = instances[index];
        const placed = instanceBox(entry);
        if (placed) box.union(placed);
        volume += entry.surface.held.volume ??= xbfMeshVolume(entry.surface.part.mesh);
        found = true;
      }
      if (!found) return null;
      return { size: box.isEmpty() ? null : box.getSize(new THREE.Vector3()).toArray(), volume };
    },
    /** An element as a chip names it: its kind and ordinal, its class, its name, its part. */
    describeElement(id) {
      const parsed = parseXbfElementId(id);
      const entry = parsed && instances[parsed.instance];
      if (!entry || !parsed.kind || parsed.kind === "vertex") return null;
      const mesh = entry.surface.part.mesh;
      let edgeClass = "";
      if (parsed.kind === "edge") {
        const row = xbmEdgeRow(mesh, parsed.ordinal);
        if (row < 0) return null;
        edgeClass = XBF_EDGE_CLASS_NAMES[mesh.edges[row * 4 + 3]] || "";
      } else if (xbmFaceRow(mesh, parsed.ordinal) < 0) {
        return null;
      }
      const names = parsed.kind === "face" ? entry.prototype.faceNames : entry.prototype.edgeNames;
      return { id, kind: parsed.kind, ordinal: parsed.ordinal, instance: parsed.instance, key: String(entry.prototype.key),
        name: (names || {})[String(parsed.ordinal)] || "", edgeClass, path: entry.instance.path, partName: entry.prototype.name || "",
        matrix: entry.matrix };
    },
    /** The box, in the scene's coordinates where they stand now, of instances and elements (ids `i3`, `i3/face/12`): what Zoom to selection frames. */
    selectionBounds(ids) {
      const box = new THREE.Box3();
      for (const id of ids) {
        const parsed = parseXbfElementId(id);
        const entry = parsed && instances[parsed.instance];
        if (!entry) continue;
        if (!parsed.kind) { const placed = instanceBox(entry); if (placed) box.union(placed); continue; }
        const mesh = entry.surface.part.mesh;
        const local = new THREE.Box3();
        if (parsed.kind === "face") {
          const row = xbmFaceRow(mesh, parsed.ordinal);
          if (row < 0) continue;
          const first = mesh.faces[row * 3 + 1], count = mesh.faces[row * 3 + 2];
          for (let at = first; at < first + count; at += 1) {
            const node = mesh.indices[at];
            local.expandByPoint(new THREE.Vector3(mesh.positions[node * 3], mesh.positions[node * 3 + 1], mesh.positions[node * 3 + 2]));
          }
        } else {
          const row = xbmEdgeRow(mesh, parsed.ordinal);
          if (row < 0) continue;
          const first = mesh.edges[row * 4 + 1], count = mesh.edges[row * 4 + 2];
          for (let at = first; at < first + count; at += 1) {
            local.expandByPoint(new THREE.Vector3(mesh.edgePoints[at * 3], mesh.edgePoints[at * 3 + 1], mesh.edgePoints[at * 3 + 2]));
          }
        }
        if (!local.isEmpty()) box.union(local.applyMatrix4(entry.group.matrixWorld));
      }
      return box.isEmpty() ? null : { min: box.min.toArray(), max: box.max.toArray() };
    },
    /** Light instances (ids `i3`): the part under the pointer and the parts picked, as a STEP lights its parts. */
    setHighlight({ hovered = [], selected = [] } = {}) {
      if (disposed) return;
      partInk = { hovered: hovered.filter(id => scene.hasPart(id)), selected: selected.filter(id => scene.hasPart(id)) };
      applySurfaces();
    },
    /**
     * The viewport the scene is shown in, as its screen-space lines' registry
     * (`registerScreenSpaceLineMaterial` / `unregisterScreenSpaceLineMaterial`, a viewer runtime's),
     * or null: every line material the scene holds moves to it, and every one it makes later is
     * registered with it, as a STEP scene's are.
     */
    setLineRegistry(registry) {
      const next = disposed ? null : registry || null;
      if (next === lineRegistry) return;
      for (const found of screenSpaceLineMaterials) lineRegistry?.unregisterScreenSpaceLineMaterial?.(found);
      lineRegistry = next;
      for (const found of screenSpaceLineMaterials) lineRegistry?.registerScreenSpaceLineMaterial?.(found);
    },
    /** How many screen-space line materials the scene holds now. */
    get screenSpaceLineMaterialCount() { return screenSpaceLineMaterials.size; },
    /** Light elements (ids `i3/face/12`): the one under the pointer and those picked. */
    setElementHighlight({ hovered = null, selected = [] } = {}) {
      if (disposed) return;
      overlay.hovered = hovered || null;
      overlay.selected = [...selected];
      applyOverlays();
    },
    /** Hide instances by id; every other is shown. */
    setHiddenParts(ids = []) {
      if (disposed) return;
      hidden.clear();
      for (const index of indicesOf(ids)) hidden.add(index);
      applyVisibility();
    },
    hiddenParts: () => [...hidden].map(index => xbfElementId(index)),
    /** Isolate instances by id (none: no isolation): every other is dimmed and out of reach. */
    setFocusedParts(ids = []) {
      if (disposed) return;
      focused.clear();
      for (const index of indicesOf(ids)) focused.add(index);
      applySurfaces();
    },
    /** The instance under the ray (the scene contract's pick): `{ id: "i3", point }`. */
    pick(ray) {
      if (disposed) return null;
      const hit = surfaceHit(ray);
      if (!hit) return null;
      const index = hit.object.userData.xbfInstance;
      return { id: xbfElementId(index), name: instances[index].prototype.name || instances[index].instance.path, point: hit.point.toArray() };
    },
    /**
     * What a STEP's Select takes under the pointer: an edge within `XBF_PICK_PIXELS` of it (a
     * press's or, `hover`, a resting pointer's), else the face under it -- each only where
     * `faces` / `edges` say so and only on an instance whose elements can be picked (`elements`:
     * a set of instance indices, or null for every one) -- else, where `parts` says so, the
     * instance under it (`{ id: "i3", kind: null }`). `view` is the camera and the viewport's
     * size in CSS pixels, what "near on screen" is measured in.
     * @param {{ faces?: boolean, edges?: boolean, parts?: boolean, elements?: Set<number> | null, hover?: boolean }} [options]
     * @returns {XbfHit | null}
     */
    pickElement(ray, view, { faces = true, edges = true, parts = false, elements = null, hover = false } = {}) {
      if (disposed || !view?.camera) return null;
      const hit = surfaceHit(ray);
      const open = entry => !elements || elements.has(entry.index);
      // An edge near the pointer and in front of the surface under it, of any part the ray passes
      // near (a peg's rim over the plate behind it, a silhouette, every edge in Wireframe).
      if (edges) {
        const limit = hit ? hit.distance : Infinity;
        let best = null;
        const sphere = new THREE.Sphere();
        for (const entry of instances) {
          if (!reachable(entry) || !open(entry) || entry.surface.held.box.isEmpty()) continue;
          entry.surface.held.box.getBoundingSphere(sphere).applyMatrix4(entry.group.matrixWorld);
          sphere.radius = sphere.radius * 1.05 + 1e-6;
          if (!ray.intersectsSphere(sphere)) continue;
          const near = nearestEdgeOnScreen(entry, ray, view, limit, hover ? XBF_PICK_PIXELS.edgeHover : XBF_PICK_PIXELS.edge);
          if (near && (!best || near.pixels < best.pixels)) best = near;
        }
        if (best) {
          const { pixels: _pixels, ...found } = best;
          return found;
        }
      }
      if (!hit) return null;
      const entry = instances[hit.object.userData.xbfInstance];
      if (faces && open(entry)) {
        const ordinal = faceOf(entry, hit);
        if (ordinal != null) return elementHit(entry, "face", ordinal, hit.point);
      }
      if (!parts) return null;
      return { id: xbfElementId(entry.index), kind: null, instance: entry.index, key: String(entry.prototype.key), ordinal: null,
        name: entry.prototype.name || entry.instance.path, point: hit.point.toArray(), path: entry.instance.path, partName: entry.prototype.name || "" };
    },
    /**
     * A second scene of the same document (the viewer's Preview beside its editor): its own
     * objects and materials over the same part geometry, which the document keeps.
     */
    createSibling() {
      const sibling = createXbfScene(THREE, document);
      sibling.setHiddenParts(scene.hiddenParts());
      return sibling;
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      root.removeFromParent();
      for (const object of overlayObjects.values()) dropOverlay(object);
      overlayObjects.clear();
      for (const found of screenSpaceLineMaterials) lineRegistry?.unregisterScreenSpaceLineMaterial?.(found);
      screenSpaceLineMaterials.clear();
      lineRegistry = null;
      ink.dispose();
      look.dispose();
      for (const found of materials.values()) found.dispose();
      for (const found of Object.values(lineMaterials)) found.dispose();
      for (const found of Object.values(partLineMaterials)) found.dispose();
      hiddenDepth.dispose();
      fillMaterial.dispose();
      for (const geometry of ownedGeometries) releaseGeometry(geometry);
    }
  };
  applyEdgeLook();
  return scene;
}
