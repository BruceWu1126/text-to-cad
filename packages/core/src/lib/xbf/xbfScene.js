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
// - Vertices from the vertex table, picked within a few pixels; faces by the face table's index
//   ranges; every element named by its ordinal, with the element name the document gives it.
// - A face the mesher left without triangles is not drawn and is reported (`unmeshed`).
//
// The display modes are the shared view settings' (`common/viewSettings.js`), resolved by the
// host as for any family: the surface look (`setSurfaceLook`: Solid, Shaded, Parts, X-ray; Hidden
// line's depth-only surfaces and Wireframe's none) and the edges the mode draws (`setEdgeLook`).

import { LineMaterial } from "three/examples/jsm/lines/LineMaterial.js";
import { LineSegments2 } from "three/examples/jsm/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/examples/jsm/lines/LineSegmentsGeometry.js";
import { resolveCadEdgeSettings } from "../../common/cadInk.js";
import { syncMaterialClipPlanes } from "../viewer/clipPlane.js";
import { scheduleRuntimeRaycastBvh } from "../viewer/raycastBvh.js";
import { REFERENCE_HOVER_COLOR, REFERENCE_SELECTED_COLOR } from "../viewer/referenceGeometry.js";
import { createSceneHighlight } from "../viewer/sceneHighlight.js";
import { createSurfaceLook } from "../viewer/surfaceLook.js";
import { releaseGeometry, shareGeometry } from "../render/sharedGeometry.js";
import { XBF_EDGE_CLASS_NAMES, xbfEdgeLine, xbmFaceRow, xbmEdgeRow, xbmTriangleFaceRows, xbmVertexRow } from "./xbm.js";
import { xbfKinematics, xbfPosedMatrices } from "./xbfKinematics.js";

export const XBF_SCENE_UNITS = Object.freeze({ unit: "mm", perWorld: 1 });
export const XBF_EDGE_LINES = Object.freeze(["feature", "tangent", "seam"]);
// How near the pointer, in CSS pixels, a vertex and an edge are picked before the face under it.
export const XBF_PICK_PIXELS = Object.freeze({ vertex: 7, edge: 5 });
const DEFAULT_SURFACE = 0xb8bcc4;
const HIGHLIGHT_ORDER = 30;
// A picked edge's width in drawing-buffer pixels: `cadInk.js`'s highlight thickness.
const EDGE_HIGHLIGHT_PIXELS = resolveCadEdgeSettings().highlightThickness;

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
 * @typedef {object} XbfHit
 * @property {string} id  The element's id (`xbfElementId`), or the instance's for `pick`.
 * @property {"face" | "edge" | "vertex" | null} kind
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
  const lineMaterials = Object.fromEntries(XBF_EDGE_LINES.map(line => [line, new THREE.LineBasicMaterial({ color: 0x253443 })]));
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

  const instances = structure.instances.map((instance, index) => {
    const prototype = prototypes[instance.prototype];
    const surface = surfaces[instance.prototype];
    const rgba = instance.color || prototype.color || null;
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
    root.add(group);
    // `matrix`: where the instance stands now, the structure's placement or the pose's.
    return { index, instance, prototype, surface, group, mesh, lines, record: { mesh }, matrix: instance.matrix };
  });
  root.updateMatrixWorld(true);

  const measureBounds = () => {
    const box = new THREE.Box3();
    for (const entry of instances) {
      if (!entry.surface.held.box.isEmpty()) box.union(entry.surface.held.box.clone().applyMatrix4(entry.group.matrixWorld));
    }
    return box.isEmpty() ? { min: [0, 0, 0], max: [0, 0, 0] } : { min: box.min.toArray(), max: box.max.toArray() };
  };
  const bounds = measureBounds();
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
  let disposed = false;
  const overlay = { hovered: null, selected: [] };
  const overlayObjects = new Map();

  const applyVisibility = () => {
    for (const entry of instances) {
      const shown = !hidden.has(entry.index);
      entry.group.visible = shown;
      entry.mesh.visible = shown && surfaceStyle !== "off";
      for (const object of Object.values(entry.lines)) object.visible = shown && edgeLook.enabled;
    }
  };
  const applyEdgeLook = () => {
    const palette = resolveCadEdgeSettings(edgeLook.color ? { color: edgeLook.color } : null);
    const onTop = edgeLook.visibility === "all";
    for (const line of XBF_EDGE_LINES) {
      const target = lineMaterials[line];
      target.color.set(palette.classes[line]?.color || palette.color);
      // Hidden edges, where the mode shows them, are drawn through the surfaces.
      if (target.depthTest === onTop) { target.depthTest = !onTop; target.needsUpdate = true; }
      syncMaterialClipPlanes(target, clipPlanes);
    }
    applyVisibility();
  };
  const recordsFor = ids => ids.map(id => instances[parseXbfElementId(id)?.instance]?.record).filter(Boolean);
  const applySurfaces = () => {
    ink.apply(lastLook, { hovered: recordsFor(partInk.hovered), selected: recordsFor(partInk.selected) }, clipPlanes);
    // Hidden line: the surfaces only hide what is behind them, in no colour of their own.
    if (surfaceStyle === "hidden") {
      for (const entry of instances) entry.mesh.material = Array.isArray(entry.mesh.material) ? entry.mesh.material.map(() => hiddenDepth) : hiddenDepth;
    }
    syncMaterialClipPlanes(hiddenDepth, clipPlanes);
    applyVisibility();
  };

  // --- picking -------------------------------------------------------------------------

  const raycaster = new THREE.Raycaster();
  const kept = point => clipPlanes.every(plane => plane.distanceToPoint(point) >= 0);
  const visibleMeshes = () => instances.filter(entry => !hidden.has(entry.index) && surfaceStyle !== "off").map(entry => entry.mesh);
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
   * The vertex or edge of `entry` nearest the pointer on screen, within its pixels, and not
   * behind the surface the pointer is on (`limit`: the hit's distance along the ray).
   */
  function nearestOnScreen(entry, ray, view, limit) {
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
    let best = XBF_PICK_PIXELS.vertex;
    let found = null;
    const v = mesh.vertices;
    for (let at = 0; at + 3 < v.length; at += 4) {
      if (!project(v[at + 1], v[at + 2], v[at + 3], a)) continue;
      const d = Math.hypot(a[0] - px, a[1] - py);
      if (d >= best) continue;
      const point = toWorld(v[at + 1], v[at + 2], v[at + 3]);
      if (!unhidden(point)) continue;
      best = d;
      found = { ...elementHit(entry, "vertex", v[at], point), pixels: d };
    }
    if (found) return found;
    best = XBF_PICK_PIXELS.edge;
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

  const overlayMaterials = {
    hovered: {
      face: new THREE.MeshBasicMaterial({ color: REFERENCE_HOVER_COLOR, transparent: true, opacity: 0.45, depthWrite: false,
        polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -4, side: THREE.DoubleSide }),
      // A picked edge is drawn as a STEP part's highlighted edge is: a screen-space line of its own width.
      edge: new LineMaterial({ color: REFERENCE_HOVER_COLOR, linewidth: EDGE_HIGHLIGHT_PIXELS, depthTest: false, transparent: true }),
      vertex: new THREE.PointsMaterial({ color: REFERENCE_HOVER_COLOR, size: 9, sizeAttenuation: false, depthTest: false, transparent: true })
    },
    selected: {
      face: new THREE.MeshBasicMaterial({ color: REFERENCE_SELECTED_COLOR, transparent: true, opacity: 0.55, depthWrite: false,
        polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -4, side: THREE.DoubleSide }),
      edge: new LineMaterial({ color: REFERENCE_SELECTED_COLOR, linewidth: EDGE_HIGHLIGHT_PIXELS, depthTest: false, transparent: true }),
      vertex: new THREE.PointsMaterial({ color: REFERENCE_SELECTED_COLOR, size: 10, sizeAttenuation: false, depthTest: false, transparent: true })
    }
  };
  function overlayFor(id, state) {
    const parsed = parseXbfElementId(id);
    const entry = parsed && instances[parsed.instance];
    if (!entry || !parsed.kind) return null;
    const mesh = entry.surface.part.mesh;
    const paint = overlayMaterials[state][parsed.kind];
    let object = null;
    if (parsed.kind === "face") {
      const row = xbmFaceRow(mesh, parsed.ordinal);
      if (row < 0 || !mesh.faces[row * 3 + 2]) return null;
      // The part's own positions, and its index buffer's range for this face: a view, no copy.
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", entry.surface.held.surface.getAttribute("position"));
      const first = mesh.faces[row * 3 + 1], count = mesh.faces[row * 3 + 2];
      geometry.setIndex(new THREE.BufferAttribute(mesh.indices.subarray(first, first + count), 1));
      geometry.userData.xbfSharedPosition = true;
      object = new THREE.Mesh(geometry, paint);
    } else if (parsed.kind === "edge") {
      const row = xbmEdgeRow(mesh, parsed.ordinal);
      if (row < 0) return null;
      const first = mesh.edges[row * 4 + 1], count = mesh.edges[row * 4 + 2];
      const points = mesh.edgePoints;
      const pairs = new Float32Array(Math.max(count - 1, 0) * 6);
      for (let at = 0; at + 1 < count; at += 1) pairs.set(points.subarray((first + at) * 3, (first + at + 2) * 3), at * 6);
      const geometry = new LineSegmentsGeometry().setPositions(pairs);
      object = new LineSegments2(geometry, paint);
    } else {
      const row = xbmVertexRow(mesh, parsed.ordinal);
      if (row < 0) return null;
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.BufferAttribute(mesh.vertices.slice(row * 4 + 1, row * 4 + 4), 3));
      object = new THREE.Points(geometry, paint);
    }
    object.name = `${state} ${parsed.kind}`;
    object.renderOrder = HIGHLIGHT_ORDER + (state === "hovered" ? 1 : 0);
    object.raycast = () => {};
    entry.group.add(object);
    return object;
  }
  function dropOverlay(object) {
    object.removeFromParent();
    const geometry = object.geometry;
    // A face's and an edge's overlay draws the part's own positions: a renderer frees the buffers
    // of every attribute a disposed geometry holds, so that one is let go of first.
    if (geometry.userData.xbfSharedPosition) geometry.deleteAttribute("position");
    geometry.dispose();
  }
  function applyOverlays() {
    const wanted = new Map();
    if (overlay.hovered) wanted.set(`hovered:${overlay.hovered}`, [overlay.hovered, "hovered"]);
    for (const id of overlay.selected) wanted.set(`selected:${id}`, [id, "selected"]);
    for (const [name, object] of overlayObjects) {
      if (!wanted.has(name)) { dropOverlay(object); overlayObjects.delete(name); }
    }
    for (const [name, [id, state]] of wanted) {
      if (overlayObjects.has(name)) continue;
      const object = overlayFor(id, state);
      if (object) overlayObjects.set(name, object);
    }
    for (const paints of Object.values(overlayMaterials)) for (const paint of Object.values(paints)) syncMaterialClipPlanes(paint, clipPlanes);
  }

  const unmeshed = surfaces.flatMap((surface, index) => surface.part.unmeshed.length
    ? [{ prototype: index, key: String(prototypes[index].key), name: prototypes[index].name || "", faces: surface.part.unmeshed }] : []);
  const triangleCount = surfaces.reduce((sum, surface) => sum + Math.floor(surface.part.mesh.indices.length / 3), 0);

  const scene = {
    object3D: root,
    bounds,
    restBounds: bounds,
    /** The document's mechanism (`xbfKinematics.js`), or null: what Position poses. */
    kinematics,
    /**
     * Pose the mechanism: each instance's placed group moved to where the mechanism stands at
     * `values` (pair label → its values; null puts every instance back where the structure places
     * it). Nothing is fetched or rebuilt; picks, highlights and an element's facts follow the
     * groups. Throws, by name, for a mechanism that is not a tree.
     */
    setPose(values) {
      if (disposed || !kinematics) return;
      const matrices = values ? xbfPosedMatrices(structure, values) : structure.instances.map(instance => instance.matrix);
      for (const entry of instances) {
        entry.matrix = matrices[entry.index];
        entry.group.matrix.fromArray(entry.matrix);
      }
      root.updateMatrixWorld(true);
      scene.bounds = measureBounds();
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
      for (const paints of Object.values(overlayMaterials)) for (const paint of Object.values(paints)) syncMaterialClipPlanes(paint, clipPlanes);
    },
    /** Whether `id` names an instance (`i3`) of this document. */
    hasPart: id => Boolean(instances[parseXbfElementId(id)?.instance]) && !parseXbfElementId(id)?.kind,
    /** An instance as a chip and the tree say it. */
    describePart(id) {
      const entry = instances[parseXbfElementId(id)?.instance];
      if (!entry) return null;
      const mesh = entry.surface.part.mesh;
      return { name: entry.prototype.name || entry.instance.path, facts: [
        { label: "Path", value: entry.instance.path },
        { label: "Faces", value: (mesh.faces.length / 3).toLocaleString("en-US") },
        { label: "Edges", value: (mesh.edges.length / 4).toLocaleString("en-US") },
        { label: "Triangles", value: Math.floor(mesh.indices.length / 3).toLocaleString("en-US") }
      ] };
    },
    /** An element as the facts panel heads it: its kind and ordinal, its class, its name. */
    describeElement(id) {
      const parsed = parseXbfElementId(id);
      const entry = parsed && instances[parsed.instance];
      if (!entry || !parsed.kind) return null;
      const mesh = entry.surface.part.mesh;
      let edgeClass = "";
      if (parsed.kind === "edge") {
        const row = xbmEdgeRow(mesh, parsed.ordinal);
        edgeClass = row < 0 ? "" : XBF_EDGE_CLASS_NAMES[mesh.edges[row * 4 + 3]] || "";
      }
      const names = parsed.kind === "face" ? entry.prototype.faceNames : parsed.kind === "edge" ? entry.prototype.edgeNames : null;
      return { id, kind: parsed.kind, ordinal: parsed.ordinal, instance: parsed.instance, key: String(entry.prototype.key),
        name: (names || {})[String(parsed.ordinal)] || "", edgeClass, path: entry.instance.path, partName: entry.prototype.name || "",
        matrix: entry.matrix };
    },
    /** Light instances (ids `i3`): what the tree hovers and selects. */
    setHighlight({ hovered = [], selected = [] } = {}) {
      if (disposed) return;
      partInk = { hovered: hovered.filter(id => scene.hasPart(id)), selected: selected.filter(id => scene.hasPart(id)) };
      applySurfaces();
    },
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
      for (const id of ids) {
        const parsed = parseXbfElementId(id);
        if (parsed && instances[parsed.instance]) hidden.add(parsed.instance);
      }
      applyVisibility();
    },
    hiddenParts: () => [...hidden].map(index => xbfElementId(index)),
    /** The instance under the ray (the scene contract's pick): `{ id: "i3", point }`. */
    pick(ray) {
      if (disposed) return null;
      const hit = surfaceHit(ray);
      if (!hit) return null;
      const index = hit.object.userData.xbfInstance;
      return { id: xbfElementId(index), name: instances[index].prototype.name || instances[index].instance.path, point: hit.point.toArray() };
    },
    /**
     * The element under the pointer: a vertex within `XBF_PICK_PIXELS.vertex` of it, else an edge
     * within `XBF_PICK_PIXELS.edge`, else the face, as FreeCAD preselects. `view` is the camera and
     * the viewport's size in CSS pixels, what "near on screen" is measured in.
     * @returns {XbfHit | null}
     */
    pickElement(ray, view) {
      if (disposed || !view?.camera) return null;
      const hit = surfaceHit(ray);
      // A vertex or an edge near the pointer and in front of the surface under it, of any part
      // the ray passes near (a peg's rim over the plate behind it, a silhouette, every edge in
      // Wireframe): vertices before edges, the nearest on screen first.
      const limit = hit ? hit.distance : Infinity;
      let best = null;
      const sphere = new THREE.Sphere();
      for (const entry of instances) {
        if (hidden.has(entry.index) || entry.surface.held.box.isEmpty()) continue;
        entry.surface.held.box.getBoundingSphere(sphere).applyMatrix4(entry.group.matrixWorld);
        sphere.radius = sphere.radius * 1.05 + 1e-6;
        if (!ray.intersectsSphere(sphere)) continue;
        const near = nearestOnScreen(entry, ray, view, limit);
        if (!near) continue;
        const rank = (near.kind === "vertex" ? 0 : 1) * 1e6 + near.pixels;
        if (!best || rank < best.rank) best = { rank, near };
      }
      if (best) {
        const { pixels: _pixels, ...found } = best.near;
        return found;
      }
      if (!hit) return null;
      const entry = instances[hit.object.userData.xbfInstance];
      const ordinal = faceOf(entry, hit);
      return ordinal == null ? null : elementHit(entry, "face", ordinal, hit.point);
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
      ink.dispose();
      look.dispose();
      for (const found of materials.values()) found.dispose();
      for (const found of Object.values(lineMaterials)) found.dispose();
      hiddenDepth.dispose();
      for (const paints of Object.values(overlayMaterials)) for (const paint of Object.values(paints)) paint.dispose();
      for (const geometry of ownedGeometries) releaseGeometry(geometry);
    }
  };
  applyEdgeLook();
  return scene;
}
