// A minimal viewer of one engine document: its structure as JSON, each part as one
// binary buffer read with typed-array views (no parsing), drawn once per instance at
// its placement. Hovering a face shows its element name: the hit triangle's index
// range is found in the part's face table.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const MAGIC = 0x314d4258; // "XBM1", little-endian
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
const highlight = new THREE.MeshBasicMaterial({ color: 0xff9a1f, depthTest: true, polygonOffset: true,
                                                polygonOffsetFactor: -1, polygonOffsetUnits: -4 });
const edgeMaterial = new THREE.LineBasicMaterial({ color: 0x1a1a1a, transparent: true, opacity: 0.55 });

/** One part buffer as typed-array views over it: no copy, no parse. */
export function readPart(buffer) {
  const head = new DataView(buffer, 0, 64);
  if (head.getUint32(0, true) !== MAGIC) throw new Error("not a part buffer");
  const [vertices, indices, faces, edges, points] = [8, 12, 16, 20, 24].map((at) => head.getUint32(at, true));
  const offsets = [28, 32, 36, 40, 44, 48].map((at) => head.getUint32(at, true));
  return {
    positions: new Float32Array(buffer, offsets[0], vertices * 3),
    normals: new Float32Array(buffer, offsets[1], vertices * 3),
    indices: new Uint32Array(buffer, offsets[2], indices),
    faces: new Uint32Array(buffer, offsets[3], faces * 3),
    edges: new Uint32Array(buffer, offsets[4], edges * 3),
    edgePoints: new Float32Array(buffer, offsets[5], points * 3),
  };
}

const parts = new Map(); // key -> { geometry, lines, faces }
let current = null; // the structure on screen
let version = null;
let fitted = false;

function linearColor(rgba, fallback) {
  if (!rgba) return new THREE.Color(fallback);
  return new THREE.Color().setRGB(rgba[0], rgba[1], rgba[2], THREE.LinearSRGBColorSpace);
}

async function part(doc, key) {
  if (parts.has(key)) return parts.get(key);
  const pending = (async () => {
    const response = await fetch(`/api/doc/${doc}/part/${key}`);
    if (!response.ok) throw new Error(`part ${key}: ${response.status}`);
    const buffer = readPart(await response.arrayBuffer());
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(buffer.positions, 3));
    geometry.setAttribute("normal", new THREE.BufferAttribute(buffer.normals, 3));
    geometry.setIndex(new THREE.BufferAttribute(buffer.indices, 1));
    geometry.computeBoundingSphere();
    geometry.computeBoundingBox();
    const segments = [];
    for (let row = 0; row < buffer.edges.length; row += 3) {
      const first = buffer.edges[row + 1], count = buffer.edges[row + 2];
      for (let at = first; at < first + count - 1; at++) segments.push(at, at + 1);
    }
    const lineGeometry = new THREE.BufferGeometry();
    lineGeometry.setAttribute("position", new THREE.BufferAttribute(buffer.edgePoints, 3));
    lineGeometry.setIndex(segments);
    return { geometry, lineGeometry, faces: buffer.faces };
  })();
  parts.set(key, pending);
  return pending;
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
      for (let row = 0; row < held.faces.length; row += 3) {
        const rgba = byOrdinal.get(String(held.faces[row]));
        let index = 0;
        if (rgba) {
          const name = rgba.join(",");
          if (!colors.has(name)) {
            colors.set(name, materials.length);
            materials.push(new THREE.MeshStandardMaterial({ color: linearColor(rgba), roughness: 0.55 }));
          }
          index = colors.get(name);
        }
        held.geometry.addGroup(held.faces[row + 1], held.faces[row + 2], index);
      }
      held.materials = materials;
    }
    if (held.materials) material = held.materials;
    const mesh = new THREE.Mesh(held.geometry, material);
    mesh.matrixAutoUpdate = false;
    mesh.matrix.fromArray(instance.matrix);
    mesh.userData = { instance, prototype, faces: held.faces };
    next.add(mesh);
    const lines = new THREE.LineSegments(held.lineGeometry, edgeMaterial);
    lines.matrixAutoUpdate = false;
    lines.matrix.fromArray(instance.matrix);
    lines.raycast = () => {};
    next.add(lines);
  }
  root.clear();
  root.add(next);
  current = structure;
  version = structure.version;
  const triangles = loaded.reduce((sum, held) => sum + held.geometry.index.count / 3, 0);
  statusLine.textContent = `${structure.prototypes.length} parts, ${structure.instances.length} instances, ` +
    `${Math.round(triangles).toLocaleString()} triangles · ${Math.round(performance.now() - started)} ms`;
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

const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
let hovered = null;
renderer.domElement.addEventListener("pointermove", (event) => {
  pointer.x = (event.clientX / window.innerWidth) * 2 - 1;
  pointer.y = -(event.clientY / window.innerHeight) * 2 + 1;
  raycaster.setFromCamera(pointer, camera);
  const hit = raycaster.intersectObjects(root.children, true).find((found) => found.object.isMesh);
  if (hovered) {
    hovered.parent?.remove(hovered);
    hovered = null;
  }
  if (!hit) {
    hoverBox.style.display = "none";
    return;
  }
  const { instance, prototype, faces } = hit.object.userData;
  const row = faceAt(faces, hit.faceIndex * 3);
  if (row < 0) return;
  const ordinal = faces[row * 3];
  hovered = new THREE.Mesh(hit.object.geometry, highlight);
  hovered.geometry.setDrawRange(0, Infinity);
  hovered.matrixAutoUpdate = false;
  hovered.matrix.copy(hit.object.matrix);
  hovered.onBeforeRender = (r, s, c, geometry) => geometry.setDrawRange(faces[row * 3 + 1], faces[row * 3 + 2]);
  hovered.onAfterRender = (r, s, c, geometry) => geometry.setDrawRange(0, Infinity);
  hit.object.parent.add(hovered);
  const name = (prototype.faceNames || {})[String(ordinal)];
  hoverBox.style.display = "block";
  hoverBox.innerHTML = "";
  const head = document.createElement("b");
  head.textContent = `${instance.path} · face ${ordinal}`;
  const body = document.createElement("div");
  body.textContent = name || "(no element name)";
  hoverBox.append(head, body);
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
  await show(doc);
  window.__xbf = { scene, camera, current: () => current, readPart };
  requestAnimationFrame(frame);
  setTimeout(() => poll(doc), 1000);
}

start().catch((error) => { statusLine.textContent = String(error); });
