// Manifold kernel: the recorded CSG program built with Manifold
// (github.com/elalish/manifold), the mesh-boolean library OpenSCAD and
// Blender use. Its booleans are exact on coincident faces, so solids that
// touch (a boss standing on a plate) don't stall it the way three-bvh-csg does.
//
// Every curve is faceted, but each circle's polygon is sized to the circle's
// area (and revolves get the same radial correction), so volumes match the
// exact part closely. Fillets show where the shared lowering can express them
// (profile corners), and a box filleted on every edge is the exact hull of
// eight spheres.
import Module from "manifold-3d";
import { prepareProgram, prismRange, revolveFrame, transformPoint, dot, sub } from "../_shared/program.mjs";
import { buildKernelMeshData } from "../_shared/meshData.mjs";

// Facets are fine enough that a chord sags at most this far from its circle.
const SAG = 0.002; // mm
const MIN_SEGMENTS = 48;
const MAX_SEGMENTS = 720;

const wasm = await Module();
wasm.setup();
const { Manifold, CrossSection } = wasm;

function segmentsFor(radius) {
  if (radius <= SAG) return MIN_SEGMENTS;
  const n = Math.ceil(Math.PI / Math.acos(1 - SAG / radius));
  return Math.min(MAX_SEGMENTS, Math.max(MIN_SEGMENTS, n + (n % 4 ? 4 - (n % 4) : 0)));
}

// Scale that gives a regular n-gon the area of its circle.
function areaScale(n) {
  return Math.sqrt((2 * Math.PI) / (n * Math.sin((2 * Math.PI) / n)));
}

// Program matrices are row-major; Manifold takes column-major.
function columnMajor(m) {
  return [m[0], m[4], m[8], m[12], m[1], m[5], m[9], m[13], m[2], m[6], m[10], m[14], m[3], m[7], m[11], m[15]];
}

function ccw(points) {
  let area = 0;
  for (let i = 0; i < points.length; i += 1) {
    const [x0, y0] = points[i];
    const [x1, y1] = points[(i + 1) % points.length];
    area += x0 * y1 - x1 * y0;
  }
  return area >= 0 ? points : [...points].reverse();
}

function arc(cx, cy, r, startDeg, sweepDeg) {
  const n = Math.max(2, Math.ceil((segmentsFor(r) * Math.abs(sweepDeg)) / 360));
  const k = areaScale(segmentsFor(r));
  return Array.from({ length: n + 1 }, (_, i) => {
    const a = ((startDeg + (sweepDeg * i) / n) * Math.PI) / 180;
    return [cx + k * r * Math.cos(a), cy + k * r * Math.sin(a)];
  });
}

// One sketch entity's outline, counter-clockwise in sketch XY.
function outline(entity) {
  const [cx, cy] = entity.center || [0, 0];
  const rot = ((entity.rotation || 0) * Math.PI) / 180;
  const place = ([x, y]) => [cx + x * Math.cos(rot) - y * Math.sin(rot), cy + x * Math.sin(rot) + y * Math.cos(rot)];
  if (entity.type === "circle") {
    return arc(0, 0, entity.radius, 0, 360).slice(0, -1).map(place);
  }
  if (entity.type === "polygon") {
    return ccw(entity.points.map(place));
  }
  if (entity.type === "rect") {
    const hx = entity.width / 2;
    const hy = entity.height / 2;
    const r = Math.min(entity.cornerRadius || 0, hx, hy);
    if (r <= 0) return [[-hx, -hy], [hx, -hy], [hx, hy], [-hx, hy]].map(place);
    return [[hx - r, -hy + r, -90], [hx - r, hy - r, 0], [-hx + r, hy - r, 90], [-hx + r, -hy + r, 180]]
      .flatMap(([x, y, start]) => arc(x, y, r, start, 90)).map(place);
  }
  throw new Error(`Manifold kernel can't draw a ${entity.type} sketch entity yet`);
}

function profile(prof, map = (p) => p) {
  let acc = null;
  for (const entity of prof.entities) {
    const section = new CrossSection([ccw(outline(entity).map(map))], "Positive");
    if (entity.mode === "subtract") {
      if (acc) acc = acc.subtract(section);
    } else {
      acc = acc ? acc.add(section) : section;
    }
  }
  if (!acc) throw new Error("sketch profile has no material");
  return acc;
}

class ManifoldBuilder {
  constructor() {
    this.warnings = [];
  }

  placed(solid, matrix) {
    return matrix ? solid.transform(columnMajor(matrix)) : solid;
  }

  // A cylinder or cone, centered on its origin along Z.
  round(lowRadius, highRadius, height) {
    const n = segmentsFor(Math.max(lowRadius, highRadius));
    const k = areaScale(n);
    return Manifold.cylinder(height, k * lowRadius, k * highRadius, n, true);
  }

  sphere(radius) {
    const raw = Manifold.sphere(radius, segmentsFor(radius));
    return raw.scale(Math.cbrt(((4 / 3) * Math.PI * radius ** 3) / raw.volume()));
  }

  build(node) {
    switch (node.type) {
      case "union":
        return Manifold.union(node.children.map((c) => this.build(c)));
      case "intersect":
        return node.children.map((c) => this.build(c)).reduce((a, b) => a.intersect(b));
      case "subtract":
        return this.build(node.base).subtract(this.build(node.tool));
      case "box": {
        const [l, w, h] = node.size;
        if (!node.edgeFillet) return this.placed(Manifold.cube([l, w, h], true), node.matrix);
        const r = Math.min(node.edgeFillet, l / 2, w / 2, h / 2);
        const ball = this.sphere(r);
        const corners = [];
        for (const x of [-1, 1]) for (const y of [-1, 1]) for (const z of [-1, 1]) {
          corners.push(ball.translate([x * (l / 2 - r), y * (w / 2 - r), z * (h / 2 - r)]));
        }
        return this.placed(Manifold.hull(corners), node.matrix);
      }
      case "cylinder":
        return this.placed(this.round(node.radius, node.radius, node.height), node.matrix);
      case "cone":
        return this.placed(this.round(node.bottomRadius, node.topRadius, node.height), node.matrix);
      case "sphere":
        return this.placed(this.sphere(node.radius), node.matrix);
      case "hole":
        return this.placed(this.round(node.radius, node.radius, 2 * node.depth), node.matrix);
      case "prism": {
        if (node.edgeFillet) this.warnings.push(`fillet ${node.edgeFillet} mm on every edge of an extrusion is shown sharp`);
        const [z0, z1] = prismRange(node);
        return this.placed(profile(node.profile).extrude(z1 - z0).translate([0, 0, z0]), node.matrix);
      }
      case "revolve": {
        const frame = revolveFrame(node);
        // Sketch XY -> (radial, axial): a rigid 2D map, since the axis lies in the sketch plane.
        const toRT = ([x, y]) => {
          const rel = [x - frame.o2[0], y - frame.o2[1]];
          return [rel[0] * frame.r2[0] + rel[1] * frame.r2[1], rel[0] * frame.d2[0] + rel[1] * frame.d2[1]];
        };
        const section = profile(node.profile, toRT);
        const radius = section.bounds().max[0];
        const n = segmentsFor(radius);
        const solid = section.scale([areaScale(n), 1]).revolve(n, node.angle);
        // Revolve output: X radial, Z the axis; Y completes a right-handed frame.
        const { origin: o, dir: d, radial: r } = frame;
        const y = [d[1] * r[2] - d[2] * r[1], d[2] * r[0] - d[0] * r[2], d[0] * r[1] - d[1] * r[0]];
        return solid.transform([r[0], r[1], r[2], 0, y[0], y[1], y[2], 0, d[0], d[1], d[2], 0, o[0], o[1], o[2], 1]);
      }
      default:
        throw new Error(`Manifold kernel cannot build ${node.type}`);
    }
  }
}

export function buildManifoldMesh(program) {
  const { root, warnings } = prepareProgram(program.root);
  const builder = new ManifoldBuilder();
  const solid = builder.build(root);
  const status = solid.status();
  if (status !== "NoError" && status?.value !== 0) {
    throw new Error(`Manifold reported ${typeof status === "string" ? status : JSON.stringify(status)}`);
  }
  const mesh = solid.getMesh();
  const stride = mesh.numProp;
  const positions = new Float32Array((mesh.vertProperties.length / stride) * 3);
  for (let i = 0, j = 0; i < mesh.vertProperties.length; i += stride, j += 3) {
    positions[j] = mesh.vertProperties[i];
    positions[j + 1] = mesh.vertProperties[i + 1];
    positions[j + 2] = mesh.vertProperties[i + 2];
  }
  const segments = segmentsFor(1);
  return {
    meshData: buildKernelMeshData({ positions, indices: mesh.triVerts }),
    volume: solid.volume(),
    triangles: mesh.triVerts.length / 3,
    warnings: [...warnings, ...builder.warnings, `circles faceted to ${SAG} mm sag (${segments} segments at 1 mm radius), sized to their true area`]
  };
}
