// three.js kernel: the recorded CSG program built from three.js primitive
// geometries and combined with three-bvh-csg. Everything is faceted
// triangles, so there is no exact geometry and no STEP export. It shows
// fillets only where three.js has a shape for them: rounded rectangle
// corners in a profile, and a box filleted on every edge (RoundedBoxGeometry).
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";
import { ADDITION, Brush, Evaluator, INTERSECTION, SUBTRACTION } from "three-bvh-csg";
import { entityOutline, prepareProgram, prismRange, revolveFrame, transformPoint, dot, sub, length, scale } from "../_shared/program.mjs";
import { buildKernelMeshData, meshVolume } from "../_shared/meshData.mjs";

const SEGMENTS = 64;
// Subtraction tools are lengthened by this much past coplanar caps so the
// CSG never has to resolve two coincident faces.
const CLEARANCE = 0.01;

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
  const shape = new THREE.Shape(entityOutline(entity).map(([x, y]) => new THREE.Vector2(x, y)));
  const geometry = new THREE.ExtrudeGeometry(shape, { depth: z1 - z0, bevelEnabled: false, curveSegments: SEGMENTS });
  geometry.translate(0, 0, z0);
  return geometry;
}

// Lathe one closed sketch outline around the revolve axis. three's lathe
// spins (x, y) points around its Y axis, so the outline is expressed as
// (radius, axial) and the result is moved onto the real axis.
function lathedEntity(entity, node, frame) {
  const points = entityOutline(entity).map(([x, y]) => {
    const p = transformPoint(node.matrix, [x, y, 0]);
    const rel = sub(p, frame.origin);
    const t = dot(rel, frame.dir);
    return new THREE.Vector2(Math.max(length(sub(rel, scale(frame.dir, t))), 0), t);
  });
  points.push(points[0].clone());
  let geometry = new THREE.LatheGeometry(points, SEGMENTS);
  if (signedVolume(prepared(geometry.clone())) < 0) {
    geometry = new THREE.LatheGeometry([...points].reverse(), SEGMENTS);
  }
  // Lathe Y goes onto the axis; X and Z span the radial plane (only full
  // revolves get here, so where phi starts doesn't matter).
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
    const out = this.evaluator.evaluate(a, b, op);
    out.updateMatrixWorld();
    return out;
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
      case "subtract":
        return this.combine(this.build(node.base), this.build(node.tool), SUBTRACTION);
      case "box":
        return this.brush(
          node.edgeFillet
            ? new RoundedBoxGeometry(...node.size, 8, Math.min(node.edgeFillet, ...node.size.map((v) => v / 2)))
            : new THREE.BoxGeometry(...node.size),
          node.matrix
        );
      case "cylinder":
        return this.brush(new THREE.CylinderGeometry(node.radius, node.radius, node.height, SEGMENTS).rotateX(Math.PI / 2), node.matrix);
      case "cone":
        return this.brush(
          new THREE.CylinderGeometry(node.topRadius, node.bottomRadius, node.height, SEGMENTS).rotateX(Math.PI / 2),
          node.matrix
        );
      case "sphere":
        return this.brush(new THREE.SphereGeometry(node.radius, SEGMENTS, SEGMENTS / 2), node.matrix);
      case "hole":
        return this.brush(
          new THREE.CylinderGeometry(node.radius, node.radius, 2 * node.depth, SEGMENTS).rotateX(Math.PI / 2),
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
        if (Math.abs(Math.abs(node.angle) - 360) > 1e-9) {
          throw new Error(`three.js kernel can't revolve a partial ${node.angle} degree arc yet`);
        }
        const frame = revolveFrame(node);
        return this.profileBrush(node.profile, (entity) => this.brush(lathedEntity(entity, node, frame)));
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
