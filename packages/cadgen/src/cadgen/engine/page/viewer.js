// A minimal viewer of one engine document: its structure as JSON, each part as one
// binary buffer read with typed-array views (no parsing), drawn once per instance at
// its placement. Hovering shows the element under the pointer and its name: a vertex or
// an edge within a few pixels of it (found in the hovered part's vertex and edge tables,
// on screen), else the face, whose triangle's index range is found in the face table.
// Clicking asks the server what the kernel measures of that element.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const MAGIC = 0x324d4258; // "XBM2", little-endian
const HEADER_BYTES = 80;
const CLASSES = { 1: "feature", 2: "tangent", 3: "seam", 5: "boundary", 6: "non-manifold" };
const EDGE_PIXELS = 5;
const VERTEX_PIXELS = 7;
const params = new URLSearchParams(location.search);
const view = document.getElementById("view");
const statusLine = document.getElementById("status");
const titleLine = document.getElementById("title");
const hoverBox = document.getElementById("hover");

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
renderer.setSize(window.innerWidth, window.innerHeight);
view.appendChild(renderer.domElement);
const scene = new THREE.Scene();
scene.background = new THREE.Color(getComputedStyle(document.documentElement).getPropertyValue("--bg").trim() || "#f4f4f2");
const camera = new THREE.PerspectiveCamera(40, window.innerWidth / window.innerHeight, 0.1, 1e7);
camera.up.set(0, 0, 1);
const controls = new OrbitControls(camera, renderer.domElement);
scene.add(new THREE.HemisphereLight(0xffffff, 0x606070, 2.2));
const sun = new THREE.DirectionalLight(0xffffff, 1.6);
scene.add(sun);
const root = new THREE.Group();
scene.add(root);
const overlay = new THREE.Group();
scene.add(overlay);
const highlight = new THREE.MeshBasicMaterial({ color: 0xff9a1f, depthTest: true, polygonOffset: true,
                                                polygonOffsetFactor: -1, polygonOffsetUnits: -4 });
const edgeMaterial = new THREE.LineBasicMaterial({ color: 0x1a1a1a, transparent: true, opacity: 0.55 });
const softEdgeMaterial = new THREE.LineBasicMaterial({ color: 0x1a1a1a, transparent: true, opacity: 0.16 });
const edgeHighlight = new THREE.LineBasicMaterial({ color: 0xff5a00, depthTest: false });
const vertexHighlight = new THREE.PointsMaterial({ color: 0xff5a00, size: 9, sizeAttenuation: false, depthTest: false });

/** One part buffer as typed-array views over it: no copy, no parse. */
export function readPart(buffer) {
  const head = new DataView(buffer, 0, HEADER_BYTES);
  if (head.getUint32(0, true) !== MAGIC) throw new Error("not a part buffer");
  const [nodes, indices, faces, edges, points, vertices] = [8, 12, 16, 20, 24, 28].map((at) => head.getUint32(at, true));
  const offsets = [32, 36, 40, 44, 48, 52, 56].map((at) => head.getUint32(at, true));
  return {
    positions: new Float32Array(buffer, offsets[0], nodes * 3),
    normals: new Float32Array(buffer, offsets[1], nodes * 3),
    indices: new Uint32Array(buffer, offsets[2], indices),
    faces: new Uint32Array(buffer, offsets[3], faces * 3), // ordinal, first index, index count
    edges: new Uint32Array(buffer, offsets[4], edges * 4), // ordinal, first point, point count, class
    edgePoints: new Float32Array(buffer, offsets[5], points * 3),
    vertices: new Float32Array(buffer, offsets[6], vertices * 4), // ordinal, x, y, z
  };
}

const parts = new Map(); // key -> promise of { geometry, lineGeometry, softGeometry, buffer, ... }
let current = null; // the structure on screen
let docId = null;
let version = null;
let fitted = false;

function linearColor(rgba, fallback) {
  if (!rgba) return new THREE.Color(fallback);
  return new THREE.Color().setRGB(rgba[0], rgba[1], rgba[2], THREE.LinearSRGBColorSpace);
}

async function part(doc, key) {
  if (parts.has(key)) return parts.get(key);
  const pending = (async () => {
    const response = await fetch(`/api/doc/${doc}/part/${key}?format=2`);
    if (!response.ok) throw new Error(`part ${key}: ${response.status}`);
    const buffer = readPart(await response.arrayBuffer());
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(buffer.positions, 3));
    geometry.setAttribute("normal", new THREE.BufferAttribute(buffer.normals, 3));
    geometry.setIndex(new THREE.BufferAttribute(buffer.indices, 1));
    geometry.computeBoundingSphere();
    geometry.computeBoundingBox();
    // Every edge's segments, in edge-table order: edge row r is segments [starts[r], starts[r + 1]).
    const rows = buffer.edges.length / 4;
    const starts = new Uint32Array(rows + 1);
    const all = [], hard = [], soft = [];
    for (let row = 0; row < rows; row++) {
      starts[row] = all.length;
      const first = buffer.edges[row * 4 + 1], count = buffer.edges[row * 4 + 2], kind = buffer.edges[row * 4 + 3];
      const drawn = kind === 2 ? soft : kind === 3 ? null : hard; // tangent edges faint, seams not drawn
      for (let at = first; at < first + count - 1; at++) {
        all.push(at, at + 1);
        if (drawn) drawn.push(at, at + 1);
      }
    }
    starts[rows] = all.length;
    const points = new THREE.BufferAttribute(buffer.edgePoints, 3);
    const lines = (index) => {
      const made = new THREE.BufferGeometry();
      made.setAttribute("position", points);
      made.setIndex(index);
      return made;
    };
    let unmeshed = 0;
    for (let row = 0; row < buffer.faces.length; row += 3) if (buffer.faces[row + 2] === 0) unmeshed++;
    return { geometry, lineGeometry: lines(hard), softGeometry: lines(soft), allLines: lines(all), starts, buffer,
             unmeshed };
  })();
  parts.set(key, pending);
  pending.catch(() => parts.delete(key));
  return pending;
}

function dispose(key, held) {
  for (const geometry of [held.geometry, held.lineGeometry, held.softGeometry, held.allLines]) geometry.dispose();
  for (const material of held.materials || []) material.dispose();
  parts.delete(key);
}

/** The face table row whose index range holds index position ``at``. */
function faceAt(faces, at) {
  let low = 0, high = faces.length / 3 - 1;
  while (low <= high) {
    const middle = (low + high) >> 1;
    const first = faces[middle * 3 + 1], count = faces[middle * 3 + 2];
    if (at < first) high = middle - 1;
    else if (at >= first + count) low = middle + 1;
    else return middle;
  }
  return -1;
}

async function show(doc) {
  const response = await fetch(`/api/doc/${doc}`);
  const structure = await response.json();
  if (structure.error) throw new Error(structure.error);
  titleLine.textContent = structure.name;
  const started = performance.now();
  const loaded = await Promise.all(structure.prototypes.map((p) => part(doc, p.key)));
  const next = new THREE.Group();
  for (const instance of structure.instances) {
    const prototype = structure.prototypes[instance.prototype];
    const held = loaded[instance.prototype];
    const base = new THREE.MeshStandardMaterial({ color: linearColor(instance.color || prototype.color, 0xb8bcc4),
                                                  roughness: 0.55, metalness: 0.05 });
    let material = base;
    const painted = Object.entries(prototype.faceColors || {});
    if (painted.length && held.geometry.groups.length === 0) {
      const colors = new Map();
      const materials = [base];
      const byOrdinal = new Map(painted);
      for (let row = 0; row < held.buffer.faces.length; row += 3) {
        const rgba = byOrdinal.get(String(held.buffer.faces[row]));
        let index = 0;
        if (rgba) {
          const name = rgba.join(",");
          if (!colors.has(name)) {
            colors.set(name, materials.length);
            materials.push(new THREE.MeshStandardMaterial({ color: linearColor(rgba), roughness: 0.55 }));
          }
          index = colors.get(name);
        }
        held.geometry.addGroup(held.buffer.faces[row + 1], held.buffer.faces[row + 2], index);
      }
      held.materials = materials;
    }
    if (held.materials) material = held.materials;
    const mesh = new THREE.Mesh(held.geometry, material);
    mesh.matrixAutoUpdate = false;
    mesh.matrix.fromArray(instance.matrix);
    mesh.matrixWorldNeedsUpdate = true;
    mesh.userData = { instance, prototype, held };
    next.add(mesh);
    for (const [geometry, lineMaterial] of [[held.lineGeometry, edgeMaterial], [held.softGeometry, softEdgeMaterial]]) {
      const lines = new THREE.LineSegments(geometry, lineMaterial);
      lines.matrixAutoUpdate = false;
      lines.matrix.fromArray(instance.matrix);
      lines.raycast = () => {};
      next.add(lines);
    }
  }
  clearHover();
  root.clear();
  root.add(next);
  // Parts no longer in the document are let go.
  const wanted = new Set(structure.prototypes.map((p) => p.key));
  for (const [key, pending] of [...parts]) {
    if (!wanted.has(key)) pending.then((held) => dispose(key, held), () => {});
  }
  current = structure;
  version = structure.version;
  const triangles = loaded.reduce((sum, held) => sum + held.geometry.index.count / 3, 0);
  const unmeshed = loaded.reduce((sum, held) => sum + held.unmeshed, 0);
  statusLine.textContent = `${structure.prototypes.length} parts, ${structure.instances.length} instances, ` +
    `${Math.round(triangles).toLocaleString()} triangles` +
    (unmeshed ? ` · ${unmeshed} unmeshed face${unmeshed === 1 ? "" : "s"}` : "") +
    ` · ${Math.round(performance.now() - started)} ms`;
  if (!fitted) {
    fit();
    fitted = true;
  }
}

function fit() {
  const box = new THREE.Box3().setFromObject(root);
  if (box.isEmpty()) return;
  const center = box.getCenter(new THREE.Vector3());
  const radius = box.getSize(new THREE.Vector3()).length() / 2 || 1;
  camera.position.copy(center).add(new THREE.Vector3(1.4, -1.8, 1.1).normalize().multiplyScalar(radius * 2.6));
  camera.near = radius / 1000;
  camera.far = radius * 100;
  camera.updateProjectionMatrix();
  controls.target.copy(center);
  controls.update();
}

// --- hover ---------------------------------------------------------------------------

const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
let hovered = null; // { kind, ordinal, key }

function clearHover() {
  overlay.clear();
  hovered = null;
}

/** The nearest vertex and edge of one placed part to the pointer, on screen, that are
 * not behind the surface the pointer is on. */
function nearest(mesh, hitDistance, clientX, clientY) {
  const { held } = mesh.userData;
  const buffer = held.buffer;
  const matrix = new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse)
    .multiply(mesh.matrixWorld);
  const m = matrix.elements;
  const world = mesh.matrixWorld.elements;
  const width = window.innerWidth, height = window.innerHeight;
  const eye = camera.position;
  const slack = hitDistance * 0.02;
  const screen = (x, y, z, out) => {
    const w = m[3] * x + m[7] * y + m[11] * z + m[15];
    out[0] = ((m[0] * x + m[4] * y + m[8] * z + m[12]) / w * 0.5 + 0.5) * width;
    out[1] = (-(m[1] * x + m[5] * y + m[9] * z + m[13]) / w * 0.5 + 0.5) * height;
    return w > 0;
  };
  const visible = (x, y, z) => {
    const wx = world[0] * x + world[4] * y + world[8] * z + world[12] - eye.x;
    const wy = world[1] * x + world[5] * y + world[9] * z + world[13] - eye.y;
    const wz = world[2] * x + world[6] * y + world[10] * z + world[14] - eye.z;
    return Math.sqrt(wx * wx + wy * wy + wz * wz) <= hitDistance + slack;
  };
  const a = [0, 0], b = [0, 0];
  let vertex = null;
  let best = VERTEX_PIXELS;
  const v = buffer.vertices;
  for (let at = 0; at < v.length; at += 4) {
    if (!screen(v[at + 1], v[at + 2], v[at + 3], a)) continue;
    const d = Math.hypot(a[0] - clientX, a[1] - clientY);
    if (d < best && visible(v[at + 1], v[at + 2], v[at + 3])) {
      best = d;
      vertex = { ordinal: v[at], position: [v[at + 1], v[at + 2], v[at + 3]] };
    }
  }
  if (vertex) return { vertex };
  let edge = null;
  best = EDGE_PIXELS;
  const p = buffer.edgePoints;
  const e = buffer.edges;
  for (let row = 0; row < e.length / 4; row++) {
    const first = e[row * 4 + 1], count = e[row * 4 + 2];
    for (let at = first; at < first + count - 1; at++) {
      if (!screen(p[at * 3], p[at * 3 + 1], p[at * 3 + 2], a) || !screen(p[at * 3 + 3], p[at * 3 + 4], p[at * 3 + 5], b)) continue;
      const dx = b[0] - a[0], dy = b[1] - a[1];
      const t = Math.max(0, Math.min(1, ((clientX - a[0]) * dx + (clientY - a[1]) * dy) / (dx * dx + dy * dy || 1)));
      const d = Math.hypot(a[0] + t * dx - clientX, a[1] + t * dy - clientY);
      if (d < best) {
        const x = p[at * 3] + t * (p[at * 3 + 3] - p[at * 3]);
        const y = p[at * 3 + 1] + t * (p[at * 3 + 4] - p[at * 3 + 1]);
        const z = p[at * 3 + 2] + t * (p[at * 3 + 5] - p[at * 3 + 2]);
        if (!visible(x, y, z)) continue;
        best = d;
        edge = { row, ordinal: e[row * 4], kind: e[row * 4 + 3] };
      }
    }
  }
  return edge ? { edge } : {};
}

function describe(head, lines) {
  hoverBox.style.display = "block";
  hoverBox.innerHTML = "";
  const title = document.createElement("b");
  title.textContent = head;
  hoverBox.append(title);
  for (const text of lines) {
    const line = document.createElement("div");
    line.textContent = text;
    hoverBox.append(line);
  }
}

/** The element under the pointer: a vertex or an edge within a few pixels of it, else the
 * face; with what to draw over it and what to say of it. */
function pick(event) {
  pointer.x = (event.clientX / window.innerWidth) * 2 - 1;
  pointer.y = -(event.clientY / window.innerHeight) * 2 + 1;
  raycaster.setFromCamera(pointer, camera);
  const hit = raycaster.intersectObjects(root.children, true).find((found) => found.object.isMesh);
  if (!hit) return null;
  const mesh = hit.object;
  const { instance, prototype, held } = mesh.userData;
  const near = nearest(mesh, hit.distance, event.clientX, event.clientY);
  const placed = (object) => {
    object.matrixAutoUpdate = false;
    object.matrix.copy(mesh.matrix);
    object.renderOrder = 2;
    return object;
  };
  const target = { key: prototype.key, instance: instance.path };
  if (near.vertex) {
    return { ...target, kind: "vertex", ordinal: near.vertex.ordinal,
             head: `${instance.path} · vertex ${near.vertex.ordinal}`,
             lines: [near.vertex.position.map((value) => value.toFixed(3)).join(", ")],
             draw: () => {
               const point = new THREE.BufferGeometry();
               point.setAttribute("position", new THREE.Float32BufferAttribute(near.vertex.position, 3));
               return placed(new THREE.Points(point, vertexHighlight));
             } };
  }
  if (near.edge) {
    const { row, ordinal, kind } = near.edge;
    return { ...target, kind: "edge", ordinal, head: `${instance.path} · edge ${ordinal} · ${CLASSES[kind] || "edge"}`,
             lines: [(prototype.edgeNames || {})[String(ordinal)] || "(no element name)"],
             draw: () => {
               const line = placed(new THREE.LineSegments(held.allLines, edgeHighlight));
               line.onBeforeRender = (r, s, c, geometry) =>
                 geometry.setDrawRange(held.starts[row], held.starts[row + 1] - held.starts[row]);
               line.onAfterRender = (r, s, c, geometry) => geometry.setDrawRange(0, Infinity);
               return line;
             } };
  }
  const faces = held.buffer.faces;
  const row = faceAt(faces, hit.faceIndex * 3);
  if (row < 0) return null;
  const ordinal = faces[row * 3];
  return { ...target, kind: "face", ordinal, head: `${instance.path} · face ${ordinal}`,
           lines: [(prototype.faceNames || {})[String(ordinal)] || "(no element name)"],
           draw: () => {
             const face = placed(new THREE.Mesh(mesh.geometry, highlight));
             face.onBeforeRender = (r, s, c, geometry) => geometry.setDrawRange(faces[row * 3 + 1], faces[row * 3 + 2]);
             face.onAfterRender = (r, s, c, geometry) => geometry.setDrawRange(0, Infinity);
             return face;
           } };
}

function hover(event) {
  const target = pick(event);
  const same = (a, b) => a && b && a.key === b.key && a.instance === b.instance && a.kind === b.kind &&
    a.ordinal === b.ordinal;
  if (same(target, hovered)) return; // the same element: what is shown stays, its facts too
  clearHover();
  if (!target) {
    hoverBox.style.display = "none";
    return;
  }
  overlay.add(target.draw());
  hovered = target;
  describe(target.head, target.lines);
}

let queued = null;
renderer.domElement.addEventListener("pointermove", (event) => {
  if (queued === null) setTimeout(() => { hover(queued); queued = null; }, 0);
  queued = event;
});

/** A click asks the server what the kernel measures of the hovered element. */
renderer.domElement.addEventListener("click", async (event) => {
  hover(event);
  const target = hovered;
  if (!target || !docId) return;
  const facts = await (await fetch(`/api/doc/${docId}/part/${target.key}/${target.kind}/${target.ordinal}`)).json();
  if (hovered !== target) return;
  const lines = Object.entries(facts).filter(([name]) => !["kind", "ordinal"].includes(name))
    .map(([name, value]) => `${name}: ${JSON.stringify(value, (_k, v) => typeof v === "number" ? +v.toPrecision(6) : v)}`);
  describe(target.head, [...target.lines, ...lines]);
});

window.addEventListener("resize", () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

function frame() {
  sun.position.copy(camera.position);
  controls.update();
  renderer.render(scene, camera);
  requestAnimationFrame(frame);
}

async function poll(doc) {
  try {
    const response = await fetch(`/api/doc/${doc}/version`);
    const answer = await response.json();
    if (answer.version && answer.version !== version) await show(doc);
  } catch (error) {
    statusLine.textContent = String(error);
  }
  setTimeout(() => poll(doc), 1000);
}

async function start() {
  let doc = params.get("doc");
  if (!doc) {
    const listing = await (await fetch("/api/docs")).json();
    doc = listing.length ? listing[listing.length - 1].id : null;
  }
  if (!doc) {
    statusLine.textContent = "no document is served";
    return;
  }
  docId = doc;
  await show(doc);
  window.__xbf = { scene, camera, controls, current: () => current, readPart, hover, hovered: () => hovered };
  requestAnimationFrame(frame);
  setTimeout(() => poll(doc), 1000);
}

start().catch((error) => { statusLine.textContent = String(error); });
