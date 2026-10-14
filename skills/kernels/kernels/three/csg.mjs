// three.js kernel: the recorded CSG program built from three.js primitive
// geometries and combined with three-bvh-csg. Everything is faceted
// triangles, so there is no exact geometry and no STEP export. Circles are
// sized to their true area. Fillets and chamfers come from the shared
// lowering (rounded profile corners, RoundedBoxGeometry, or the edge tools
// the recorder resolved); lofts and sweeps from the recorder's meshes.
// three-bvh-csg needs help on coplanar faces: see unflushed() and cleanup.mjs.
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";
import { ADDITION, Brush, Evaluator, INTERSECTION, SUBTRACTION } from "three-bvh-csg";
import { entityOutline, prepareProgram, prismRange, revolveFrame, transformPoint, dot, sub, length, scale } from "../_shared/program.mjs";
import { buildKernelMeshData, meshVolume } from "../_shared/meshData.mjs";
import { cleanupGeometry } from "./cleanup.mjs";

const SEGMENTS = 64;
// Radius scale that gives a regular SEGMENTS-gon its circle's area.
const AREA = Math.sqrt((2 * Math.PI) / (SEGMENTS * Math.sin((2 * Math.PI) / SEGMENTS)));
// Subtraction tools are lengthened by this much past coplanar caps so the
// CSG never has to resolve two coincident faces.
const CLEARANCE = 0.01;
// How far a boolean operand is moved off a plane it shares with the other.
const NUDGE = 2e-4; // mm
// Subtraction tools cut together (see batches()).
const BATCH = 10;

function matrix4(m) {
  return new THREE.Matrix4().set(...m);
}

function prepared(geometry) {
  const g = geometry.index ? geometry.toNonIndexed() : geometry;
  for (const name of Object.keys(g.attributes)) {
    if (name !== "position" && name !== "normal") g.deleteAttribute(name);
  }
  g.clearGroups();
  if (!g.getAttribute("normal")) g.computeVertexNormals();
  return g;
}

function signedVolume(geometry) {
  return meshVolume(geometry.getAttribute("position").array, null);
}

function extrudedEntity(entity, z0, z1) {
  const sized = entity.type === "circle" ? { ...entity, radius: entity.radius * AREA } : entity;
  const shape = new THREE.Shape(entityOutline(sized).map(([x, y]) => new THREE.Vector2(x, y)));
  const geometry = new THREE.ExtrudeGeometry(shape, { depth: z1 - z0, bevelEnabled: false, curveSegments: SEGMENTS });
  geometry.translate(0, 0, z0);
  return geometry;
}

// Revolve one closed sketch outline about the revolve axis by node.angle,
// starting at the frame's radial direction and turning counter-clockwise
// about the axis; a partial revolve gets its two end caps.
function revolvedEntity(entity, node, frame) {
  let ring = entityOutline(entity).map(([x, y]) => {
    const p = transformPoint(node.matrix, [x, y, 0]);
    const rel = sub(p, frame.origin);
    const t = dot(rel, frame.dir);
    return [Math.max(length(sub(rel, scale(frame.dir, t))), 0), t];
  });
  let area = 0;
  ring.forEach(([x0, y0], i) => {
    const [x1, y1] = ring[(i + 1) % ring.length];
    area += x0 * y1 - x1 * y0;
  });
  if (area < 0) ring = ring.reverse();
  const sweep = (Math.min(Math.abs(node.angle), 360) * Math.PI) / 180;
  const full = sweep >= 2 * Math.PI - 1e-9;
  const rMax = Math.max(...ring.map(([r]) => r));
  const steps = Math.max(3, Math.ceil((SEGMENTS * sweep) / (2 * Math.PI)), rMax > 0 ? Math.ceil(sweep / (2 * Math.acos(Math.max(-1, 1 - 0.005 / rMax)))) : 3);
  const sign = node.angle < 0 ? -1 : 1;
  const rings = full ? steps : steps + 1;
  const at = (i, [r, t]) => {
    const phi = (sign * sweep * i) / steps;
    return [r * Math.cos(phi), t, -r * Math.sin(phi)];
  };
  const out = [];
  const tri = (a, b, c) => out.push(...a, ...b, ...c);
  const n = ring.length;
  for (let i = 0; i < steps; i += 1) {
    const i1 = full ? (i + 1) % rings : i + 1;
    for (let k = 0; k < n; k += 1) {
      const a = at(i, ring[k]), b = at(i, ring[(k + 1) % n]), c = at(i1, ring[(k + 1) % n]), d = at(i1, ring[k]);
      tri(a, b, c);
      tri(a, c, d);
    }
  }
  if (!full) {
    const faces = THREE.ShapeUtils.triangulateShape(ring.map(([x, y]) => new THREE.Vector2(x, y)), []);
    for (const [a, b, c] of faces) {
      tri(at(0, ring[a]), at(0, ring[c]), at(0, ring[b]));
      tri(at(steps, ring[a]), at(steps, ring[b]), at(steps, ring[c]));
    }
  }
  let geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(out), 3));
  if (signedVolume(geometry) < 0) {
    const p = geometry.getAttribute("position").array;
    for (let i = 0; i < p.length; i += 9) {
      for (let k = 0; k < 3; k += 1) [p[i + 3 + k], p[i + 6 + k]] = [p[i + 6 + k], p[i + 3 + k]];
    }
  }
  geometry.computeVertexNormals();
  // Local X is the radial direction, Y the axis; Z = X x Y, so turning from
  // X toward -Z is counter-clockwise about the axis.
  const z = [
    frame.radial[1] * frame.dir[2] - frame.radial[2] * frame.dir[1],
    frame.radial[2] * frame.dir[0] - frame.radial[0] * frame.dir[2],
    frame.radial[0] * frame.dir[1] - frame.radial[1] * frame.dir[0]
  ];
  const [o, d, r] = [frame.origin, frame.dir, frame.radial];
  geometry.applyMatrix4(matrix4([r[0], d[0], z[0], o[0], r[1], d[1], z[1], o[1], r[2], d[2], z[2], o[2], 0, 0, 0, 1]));
  return geometry;
}

class ThreeBuilder {
  constructor() {
    this.evaluator = new Evaluator();
    this.evaluator.attributes = ["position", "normal"];
    this.evaluator.useGroups = false;
    this.warnings = [];
  }

  brush(geometry, matrix = null) {
    if (matrix) geometry.applyMatrix4(matrix4(matrix));
    const brush = new Brush(prepared(geometry));
    brush.updateMatrixWorld();
    return brush;
  }

  combine(a, b, op) {
    const raw = this.evaluator.evaluate(a, this.unflushed(a, b, op), op);
    const out = new Brush(cleanupGeometry(raw.geometry));
    out.updateMatrixWorld();
    return out;
  }

  // Tools in spatial batches of BATCH; tools in a batch that don't overlap are
  // joined by concatenating their triangles, which needs no boolean.
  batches(brushes) {
    if (brushes.length <= 1) return brushes;
    const boxes = brushes.map((b) => new THREE.Box3().setFromBufferAttribute(b.geometry.getAttribute("position")));
    const centers = boxes.map((box) => box.getCenter(new THREE.Vector3()));
    const order = brushes.map((_, i) => i).sort((i, j) => centers[i].x - centers[j].x || centers[i].y - centers[j].y || centers[i].z - centers[j].z);
    const out = [];
    for (let s = 0; s < order.length; s += BATCH) {
      const group = order.slice(s, s + BATCH);
      const disjoint = group.every((i, a) => group.every((j, b) => b <= a || !boxes[i].intersectsBox(boxes[j])));
      if (!disjoint) {
        out.push(...group.map((i) => brushes[i]));
        continue;
      }
      const arrays = group.map((i) => brushes[i].geometry.getAttribute("position").array);
      const merged = new Float32Array(arrays.reduce((n, a) => n + a.length, 0));
      arrays.reduce((offset, a) => (merged.set(a, offset), offset + a.length), 0);
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.BufferAttribute(merged, 3));
      geometry.computeVertexNormals();
      const brush = new Brush(geometry);
      brush.updateMatrixWorld();
      out.push(brush);
    }
    return out;
  }

  // three-bvh-csg is slow on coplanar faces (a boss standing on a plate, a
  // pocket flush with the top): it splits them into thousands of slivers.
  // Where b has a face in one of a's planes, move b by NUDGE so the faces
  // cross instead of touching, in the direction that keeps the result: into
  // a for a union contact, out past the surface for a flush cut, away from a
  // for a tool resting on it. The shift is far below the faceting error.
  unflushed(a, b, op) {
    if (op === INTERSECTION) return b;
    const planes = new Map();
    const each = (brush, fn) => {
      const p = brush.geometry.getAttribute("position").array;
      for (let i = 0; i + 8 < p.length; i += 9) {
        const u = [p[i + 3] - p[i], p[i + 4] - p[i + 1], p[i + 5] - p[i + 2]];
        const v = [p[i + 6] - p[i], p[i + 7] - p[i + 1], p[i + 8] - p[i + 2]];
        const n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
        const len = Math.hypot(...n);
        if (len < 1e-9) continue;
        const unit = n.map((x) => x / len);
        fn(unit, unit[0] * p[i] + unit[1] * p[i + 1] + unit[2] * p[i + 2]);
      }
    };
    const key = (n, d) => [...n.map((x) => Math.round(x * 1e5)), Math.round(d * 1e4)].join(",");
    each(a, (n, d) => planes.set(key(n, d), true));
    const shift = [0, 0, 0];
    const seen = new Set();
    each(b, (n, d) => {
      const same = planes.has(key(n, d));
      const opposite = planes.has(key(n.map((x) => -x), -d));
      if (!same && !opposite) return;
      const k = key(n, d);
      if (seen.has(k)) return;
      seen.add(k);
      // union contact: push b into a; flush cut: push the tool out; tool resting on a: lift it off
      const sign = op === ADDITION ? (opposite ? 1 : 0) : (same ? 1 : -1);
      for (let i = 0; i < 3; i += 1) shift[i] += sign * n[i];
    });
    const len = Math.hypot(...shift);
    if (len < 1e-9) return b;
    const geometry = b.geometry.clone();
    geometry.translate(...shift.map((x) => (x / len) * NUDGE));
    const moved = new Brush(geometry);
    moved.updateMatrixWorld();
    return moved;
  }

  // Sequential 2D booleans of a profile, each entity built by makeEntity.
  profileBrush(profile, makeEntity) {
    let acc = null;
    for (const entity of profile.entities) {
      const subtract = entity.mode === "subtract";
      const brush = makeEntity(entity, subtract);
      if (!acc) {
        if (!subtract) acc = brush;
        continue;
      }
      acc = this.combine(acc, brush, subtract ? SUBTRACTION : ADDITION);
    }
    if (!acc) throw new Error("sketch profile has no material");
    return acc;
  }

  build(node) {
    if (node.edgeFillet && node.type !== "box") {
      this.warnings.push(`fillet ${node.edgeFillet} mm on all edges is shown sharp (three.js only rounds boxes)`);
    }
    switch (node.type) {
      case "union":
        return node.children.map((c) => this.build(c)).reduce((a, b) => this.combine(a, b, ADDITION));
      case "intersect":
        return node.children.map((c) => this.build(c)).reduce((a, b) => this.combine(a, b, INTERSECTION));
      case "subtract": {
        // A - (B + C) = (A - B) - C. One cut by the union of a hundred holes
        // splits every face by all of them at once (75 s on a 10 x 10 grid);
        // a hundred cuts each redo the whole face. Cut in small batches of
        // neighbouring tools instead.
        const tools = (node.tool.type === "union" ? node.tool.children : [node.tool]).map((t) => this.build(t));
        return this.batches(tools).reduce((acc, tool) => this.combine(acc, tool, SUBTRACTION), this.build(node.base));
      }
      case "box":
        return this.brush(
          node.edgeFillet
            ? new RoundedBoxGeometry(...node.size, 8, Math.min(node.edgeFillet, ...node.size.map((v) => v / 2)))
            : new THREE.BoxGeometry(...node.size),
          node.matrix
        );
      case "cylinder":
        return this.brush(new THREE.CylinderGeometry(node.radius * AREA, node.radius * AREA, node.height, SEGMENTS).rotateX(Math.PI / 2), node.matrix);
      case "cone":
        return this.brush(
          new THREE.CylinderGeometry(node.topRadius * AREA, node.bottomRadius * AREA, node.height, SEGMENTS).rotateX(Math.PI / 2),
          node.matrix
        );
      case "sphere": {
        const geometry = new THREE.SphereGeometry(node.radius, SEGMENTS, SEGMENTS / 2);
        const k = Math.cbrt(((4 / 3) * Math.PI * node.radius ** 3) / signedVolume(prepared(geometry.clone())));
        return this.brush(geometry.scale(k, k, k), node.matrix);
      }
      case "hole":
        return this.brush(
          new THREE.CylinderGeometry(node.radius * AREA, node.radius * AREA, 2 * node.depth, SEGMENTS).rotateX(Math.PI / 2),
          node.matrix
        );
      case "prism": {
        const [z0, z1] = prismRange(node);
        return this.profileBrush(node.profile, (entity, subtract) => {
          const pad = subtract ? CLEARANCE : 0;
          return this.brush(extrudedEntity(entity, z0 - pad, z1 + pad), node.matrix);
        });
      }
      case "revolve": {
        const frame = revolveFrame(node);
        return this.profileBrush(node.profile, (entity) => this.brush(revolvedEntity(entity, node, frame)));
      }
      case "loft":
      case "sweep": {
        // the closed mesh the recorder built through the sections or along the path
        const v = node.mesh.vertices, t = node.mesh.triangles;
        const positions = new Float32Array(t.length * 3);
        t.forEach((id, i) => positions.set([v[3 * id], v[3 * id + 1], v[3 * id + 2]], 3 * i));
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
        geometry.computeVertexNormals();
        return this.brush(geometry);
      }
      default:
        throw new Error(`three.js kernel cannot build ${node.type}`);
    }
  }
}

export function buildThreeMesh(program) {
  const { root, warnings } = prepareProgram(program.root);
  const builder = new ThreeBuilder();
  const brush = builder.build(root);
  const geometry = brush.geometry;
  const positions = new Float32Array(geometry.getAttribute("position").array);
  const normals = new Float32Array(geometry.getAttribute("normal").array);
  return {
    meshData: buildKernelMeshData({ positions, normals }),
    volume: meshVolume(positions, null),
    triangles: positions.length / 9,
    warnings: [...warnings, ...builder.warnings]
  };
}
