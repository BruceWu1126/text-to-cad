// Implicit kernel: the recorded CSG program becomes one signed distance
// function (negative inside), meshed with surface nets on a uniform grid.
// Booleans are min/max, fillets on boxes and prisms are exact rounded SDFs,
// and everything is a sampled approximation: no exact faces, no STEP.
//
// Primitive distance functions follow Inigo Quilez's catalogue
// (https://iquilezles.org/articles/distfunctions/).
import {
  entityOutline,
  prepareProgram,
  prismRange,
  revolveFrame,
  rigidInverse
} from "../_shared/program.mjs";
import { buildKernelMeshData, meshVolume } from "../_shared/meshData.mjs";

function localized(matrix, fn) {
  const m = rigidInverse(matrix);
  return (x, y, z) => fn(
    m[0] * x + m[1] * y + m[2] * z + m[3],
    m[4] * x + m[5] * y + m[6] * z + m[7],
    m[8] * x + m[9] * y + m[10] * z + m[11]
  );
}

// Distance from a 2D box (half sizes hx, hy) with corner radius r.
function roundedRect2d(x, y, hx, hy, r) {
  const qx = Math.abs(x) - hx + r;
  const qy = Math.abs(y) - hy + r;
  return Math.hypot(Math.max(qx, 0), Math.max(qy, 0)) + Math.min(Math.max(qx, qy), 0) - r;
}

function polygon2d(points) {
  return (x, y) => {
    let d = (x - points[0][0]) ** 2 + (y - points[0][1]) ** 2;
    let s = 1;
    for (let i = 0, j = points.length - 1; i < points.length; j = i, i += 1) {
      const [vix, viy] = points[i];
      const [vjx, vjy] = points[j];
      const ex = vjx - vix, ey = vjy - viy;
      const wx = x - vix, wy = y - viy;
      const t = Math.min(Math.max((wx * ex + wy * ey) / (ex * ex + ey * ey), 0), 1);
      const bx = wx - ex * t, by = wy - ey * t;
      d = Math.min(d, bx * bx + by * by);
      const c1 = y >= viy, c2 = y < vjy, c3 = ex * wy > ey * wx;
      if ((c1 && c2 && c3) || (!c1 && !c2 && !c3)) s = -s;
    }
    return s * Math.sqrt(d);
  };
}

function entity2d(entity, extraCorner = 0) {
  const [cx, cy] = entity.center || [0, 0];
  const rot = ((entity.rotation || 0) * Math.PI) / 180;
  const cos = Math.cos(rot), sin = Math.sin(rot);
  const local = (fn) => (x, y) => {
    const dx = x - cx, dy = y - cy;
    return fn(cos * dx + sin * dy, -sin * dx + cos * dy);
  };
  if (entity.type === "circle") {
    return (x, y) => Math.hypot(x - cx, y - cy) - entity.radius;
  }
  if (entity.type === "rect") {
    const hx = entity.width / 2, hy = entity.height / 2;
    const r = Math.min(Math.max(entity.cornerRadius || 0, extraCorner), hx, hy);
    return local((x, y) => roundedRect2d(x, y, hx, hy, r));
  }
  if (entity.type === "polygon") {
    return local(polygon2d(entity.points));
  }
  // Unknown entity: fall back to its outline polygon.
  return polygon2d(entityOutline(entity));
}

function profile2d(profile, extraCorner = 0) {
  const parts = profile.entities.map((e) => ({ fn: entity2d(e, e.mode === "subtract" ? 0 : extraCorner), sub: e.mode === "subtract" }));
  return (x, y) => {
    let d = Infinity;
    for (const { fn, sub } of parts) {
      d = sub ? Math.max(d, -fn(x, y)) : Math.min(d, fn(x, y));
    }
    return d;
  };
}

// Finite cylinder along local Z between z0 and z1.
function cappedCylinder(r, z0, z1) {
  const zc = (z0 + z1) / 2, hz = (z1 - z0) / 2;
  return (x, y, z) => {
    const dx = Math.hypot(x, y) - r;
    const dz = Math.abs(z - zc) - hz;
    return Math.min(Math.max(dx, dz), 0) + Math.hypot(Math.max(dx, 0), Math.max(dz, 0));
  };
}

function cappedCone(h, r1, r2) {
  // Centered, bottom radius r1 at z = -h, top radius r2 at z = +h.
  const k2x = r2 - r1, k2y = 2 * h;
  const k2len2 = k2x * k2x + k2y * k2y;
  return (x, y, z) => {
    const qx = Math.hypot(x, y), qy = z;
    const cax = qx - Math.min(qx, qy < 0 ? r1 : r2);
    const cay = Math.abs(qy) - h;
    const t = Math.min(Math.max(((r2 - qx) * k2x + (h - qy) * k2y) / k2len2, 0), 1);
    const cbx = qx - r2 + k2x * t;
    const cby = qy - h + k2y * t;
    const s = cbx < 0 && cay < 0 ? -1 : 1;
    return s * Math.sqrt(Math.min(cax * cax + cay * cay, cbx * cbx + cby * cby));
  };
}

function compile(node) {
  switch (node.type) {
    case "union": {
      const fns = node.children.map(compile);
      return (x, y, z) => {
        let d = Infinity;
        for (const fn of fns) d = Math.min(d, fn(x, y, z));
        return d;
      };
    }
    case "intersect": {
      const fns = node.children.map(compile);
      return (x, y, z) => {
        let d = -Infinity;
        for (const fn of fns) d = Math.max(d, fn(x, y, z));
        return d;
      };
    }
    case "subtract": {
      const base = compile(node.base);
      const tool = compile(node.tool);
      return (x, y, z) => Math.max(base(x, y, z), -tool(x, y, z));
    }
    case "box": {
      const [hx, hy, hz] = node.size.map((v) => v / 2);
      const r = Math.min(node.edgeFillet || 0, hx, hy, hz);
      return localized(node.matrix, (x, y, z) => {
        const qx = Math.abs(x) - hx + r, qy = Math.abs(y) - hy + r, qz = Math.abs(z) - hz + r;
        return Math.hypot(Math.max(qx, 0), Math.max(qy, 0), Math.max(qz, 0)) + Math.min(Math.max(qx, qy, qz), 0) - r;
      });
    }
    case "cylinder":
      return localized(node.matrix, cappedCylinder(node.radius, -node.height / 2, node.height / 2));
    case "hole":
      return localized(node.matrix, cappedCylinder(node.radius, -node.depth, node.depth));
    case "cone":
      return localized(node.matrix, cappedCone(node.height / 2, node.bottomRadius, node.topRadius));
    case "sphere":
      return localized(node.matrix, (x, y, z) => Math.hypot(x, y, z) - node.radius);
    case "prism": {
      const r = node.edgeFillet || 0;
      const [z0, z1] = prismRange(node);
      const zc = (z0 + z1) / 2, hz = (z1 - z0) / 2;
      const d2 = profile2d(node.profile, r);
      return localized(node.matrix, (x, y, z) => {
        const wx = d2(x, y) + r;
        const wz = Math.abs(z - zc) - hz + r;
        return Math.min(Math.max(wx, wz), 0) + Math.hypot(Math.max(wx, 0), Math.max(wz, 0)) - r;
      });
    }
    case "revolve": {
      const frame = revolveFrame(node);
      const d2 = profile2d(node.profile);
      const [ox, oy, oz] = frame.origin;
      const [dx, dy, dz] = frame.dir;
      const [rx, ry, rz] = frame.radial;
      // Third axis of the revolve frame: direction of positive rotation.
      const bx = dy * rz - dz * ry, by = dz * rx - dx * rz, bz = dx * ry - dy * rx;
      const sweep = (Math.min(Math.abs(node.angle), 360) * Math.PI) / 180;
      const full = sweep >= 2 * Math.PI - 1e-9;
      return (x, y, z) => {
        const px = x - ox, py = y - oy, pz = z - oz;
        const t = px * dx + py * dy + pz * dz;
        const qx = px - t * dx, qy = py - t * dy, qz = pz - t * dz;
        const r = Math.hypot(qx, qy, qz);
        const u = frame.o2[0] + t * frame.d2[0] + r * frame.r2[0];
        const v = frame.o2[1] + t * frame.d2[1] + r * frame.r2[1];
        const d = d2(u, v);
        if (full) return d;
        let phi = Math.atan2(qx * bx + qy * by + qz * bz, qx * rx + qy * ry + qz * rz);
        if (node.angle < 0) phi = -phi;
        if (phi < 0) phi += 2 * Math.PI;
        if (phi <= sweep) return d;
        const gap = Math.min(phi - sweep, 2 * Math.PI - phi);
        return Math.max(d, r * Math.sin(Math.min(gap, Math.PI / 2)));
      };
    }
    default:
      throw new Error(`implicit kernel cannot evaluate ${node.type}`);
  }
}

export function compileSdf(root) {
  return compile(root);
}

// Surface nets: one vertex per sign-changing cell (the mean of its edge
// crossings), one quad per sign-changing grid edge.
export function meshSdf(sdf, bounds, { resolution = 128, maxSamples = 6_000_000 } = {}) {
  const ext = [0, 1, 2].map((i) => bounds.max[i] - bounds.min[i]);
  let h = Math.max(...ext) / resolution;
  let dims;
  let origin;
  for (;;) {
    const pad = 2 * h;
    origin = bounds.min.map((v) => v - pad);
    dims = ext.map((e) => Math.ceil((e + 2 * pad) / h) + 1);
    const total = dims[0] * dims[1] * dims[2];
    if (total <= maxSamples) break;
    h *= Math.cbrt(total / maxSamples) * 1.01;
  }
  const [nx, ny, nz] = dims;
  const values = new Float32Array(nx * ny * nz);
  for (let k = 0; k < nz; k += 1) {
    const z = origin[2] + k * h;
    for (let j = 0; j < ny; j += 1) {
      const y = origin[1] + j * h;
      let idx = nx * (j + ny * k);
      for (let i = 0; i < nx; i += 1, idx += 1) {
        values[idx] = sdf(origin[0] + i * h, y, z);
      }
    }
  }
  const at = (i, j, k) => values[i + nx * (j + ny * k)];
  const cx = nx - 1, cy = ny - 1, cz = nz - 1;
  const cellVertex = new Int32Array(cx * cy * cz).fill(-1);
  const positions = [];
  const CORNERS = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0], [0, 0, 1], [1, 0, 1], [0, 1, 1], [1, 1, 1]];
  const EDGES = [[0, 1], [2, 3], [4, 5], [6, 7], [0, 2], [1, 3], [4, 6], [5, 7], [0, 4], [1, 5], [2, 6], [3, 7]];
  const v = new Float64Array(8);
  for (let k = 0; k < cz; k += 1) {
    for (let j = 0; j < cy; j += 1) {
      for (let i = 0; i < cx; i += 1) {
        let inside = 0;
        for (let c = 0; c < 8; c += 1) {
          v[c] = at(i + CORNERS[c][0], j + CORNERS[c][1], k + CORNERS[c][2]);
          if (v[c] < 0) inside += 1;
        }
        if (inside === 0 || inside === 8) continue;
        let sx = 0, sy = 0, sz = 0, n = 0;
        for (const [a, b] of EDGES) {
          if ((v[a] < 0) === (v[b] < 0)) continue;
          const t = v[a] / (v[a] - v[b]);
          sx += CORNERS[a][0] + t * (CORNERS[b][0] - CORNERS[a][0]);
          sy += CORNERS[a][1] + t * (CORNERS[b][1] - CORNERS[a][1]);
          sz += CORNERS[a][2] + t * (CORNERS[b][2] - CORNERS[a][2]);
          n += 1;
        }
        cellVertex[i + cx * (j + cy * k)] = positions.length / 3;
        positions.push(origin[0] + (i + sx / n) * h, origin[1] + (j + sy / n) * h, origin[2] + (k + sz / n) * h);
      }
    }
  }
  const cell = (i, j, k) => cellVertex[i + cx * (j + cy * k)];
  const indices = [];
  const quad = (a, b, c, d, flip) => {
    if (a < 0 || b < 0 || c < 0 || d < 0) return;
    if (flip) indices.push(a, c, b, a, d, c);
    else indices.push(a, b, c, a, c, d);
  };
  // Each sign-changing grid edge gets a quad of the four cells around it,
  // wound counter-clockwise about the edge direction; flipped when the
  // edge runs from outside to inside so normals point out.
  for (let k = 1; k < cz; k += 1) {
    for (let j = 1; j < cy; j += 1) {
      for (let i = 0; i < cx; i += 1) {
        const a = at(i, j, k) < 0, b = at(i + 1, j, k) < 0;
        if (a !== b) quad(cell(i, j - 1, k - 1), cell(i, j, k - 1), cell(i, j, k), cell(i, j - 1, k), !a);
      }
    }
  }
  for (let k = 1; k < cz; k += 1) {
    for (let j = 0; j < cy; j += 1) {
      for (let i = 1; i < cx; i += 1) {
        const a = at(i, j, k) < 0, b = at(i, j + 1, k) < 0;
        if (a !== b) quad(cell(i - 1, j, k - 1), cell(i - 1, j, k), cell(i, j, k), cell(i, j, k - 1), !a);
      }
    }
  }
  for (let k = 0; k < cz; k += 1) {
    for (let j = 1; j < cy; j += 1) {
      for (let i = 1; i < cx; i += 1) {
        const a = at(i, j, k) < 0, b = at(i, j, k + 1) < 0;
        if (a !== b) quad(cell(i - 1, j - 1, k), cell(i, j - 1, k), cell(i, j, k), cell(i - 1, j, k), !a);
      }
    }
  }
  const normals = new Float32Array(positions.length);
  const e = h * 0.5;
  for (let p = 0; p < positions.length; p += 3) {
    const [x, y, z] = [positions[p], positions[p + 1], positions[p + 2]];
    const gx = sdf(x + e, y, z) - sdf(x - e, y, z);
    const gy = sdf(x, y + e, z) - sdf(x, y - e, z);
    const gz = sdf(x, y, z + e) - sdf(x, y, z - e);
    const len = Math.hypot(gx, gy, gz) || 1;
    normals[p] = gx / len;
    normals[p + 1] = gy / len;
    normals[p + 2] = gz / len;
  }
  return { positions: new Float32Array(positions), indices: new Uint32Array(indices), normals, cellSize: h };
}

export function buildImplicitMesh(program, { resolution = 128 } = {}) {
  const { root, warnings, bounds } = prepareProgram(program.root);
  if (!bounds) {
    throw new Error("program has no solid material");
  }
  const sdf = compileSdf(root);
  const mesh = meshSdf(sdf, bounds, { resolution });
  if (!mesh.indices.length) {
    throw new Error("implicit kernel found no surface");
  }
  return {
    meshData: buildKernelMeshData(mesh),
    volume: meshVolume(mesh.positions, mesh.indices),
    triangles: mesh.indices.length / 3,
    warnings: [`sampled at ${mesh.cellSize.toFixed(3)} mm cells, so sharp edges are softened`, ...warnings]
  };
}
