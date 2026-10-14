// Implicit kernel: the recorded CSG program becomes one signed distance
// function (negative inside), meshed with surface nets on a uniform grid.
// Booleans are min/max (culled through a bounding-volume tree), fillets on
// boxes and prisms are exact rounded SDFs, other fillets and chamfers are the
// recorder's edge tools, and lofts and sweeps are distance fields of the
// recorder's meshes. The grid is as fine as a sample budget allows, sampled
// exactly only near the surface. Everything is a sampled approximation: no
// exact faces, no STEP.
//
// Primitive distance functions follow Inigo Quilez's catalogue
// (https://iquilezles.org/articles/distfunctions/).
import {
  entityOutline,
  nodeBounds,
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

// Distance from point p to triangle abc (Ericson, Real-Time Collision Detection 5.1.5).
function triangleDistance(px, py, pz, a, b, c) {
  const abx = b[0] - a[0], aby = b[1] - a[1], abz = b[2] - a[2];
  const acx = c[0] - a[0], acy = c[1] - a[1], acz = c[2] - a[2];
  const apx = px - a[0], apy = py - a[1], apz = pz - a[2];
  const d1 = abx * apx + aby * apy + abz * apz, d2 = acx * apx + acy * apy + acz * apz;
  const at = (x, y, z) => Math.hypot(px - x, py - y, pz - z);
  if (d1 <= 0 && d2 <= 0) return at(...a);
  const bpx = px - b[0], bpy = py - b[1], bpz = pz - b[2];
  const d3 = abx * bpx + aby * bpy + abz * bpz, d4 = acx * bpx + acy * bpy + acz * bpz;
  if (d3 >= 0 && d4 <= d3) return at(...b);
  const vc = d1 * d4 - d3 * d2;
  if (vc <= 0 && d1 >= 0 && d3 <= 0) {
    const v = d1 / (d1 - d3);
    return at(a[0] + v * abx, a[1] + v * aby, a[2] + v * abz);
  }
  const cpx = px - c[0], cpy = py - c[1], cpz = pz - c[2];
  const d5 = abx * cpx + aby * cpy + abz * cpz, d6 = acx * cpx + acy * cpy + acz * cpz;
  if (d6 >= 0 && d5 <= d6) return at(...c);
  const vb = d5 * d2 - d1 * d6;
  if (vb <= 0 && d2 >= 0 && d6 <= 0) {
    const w = d2 / (d2 - d6);
    return at(a[0] + w * acx, a[1] + w * acy, a[2] + w * acz);
  }
  const va = d3 * d6 - d5 * d4;
  if (va <= 0 && d4 - d3 >= 0 && d5 - d6 >= 0) {
    const w = (d4 - d3) / (d4 - d3 + (d5 - d6));
    return at(b[0] + w * (c[0] - b[0]), b[1] + w * (c[1] - b[1]), b[2] + w * (c[2] - b[2]));
  }
  const denom = 1 / (va + vb + vc);
  const v = vb * denom, w = vc * denom;
  return at(a[0] + abx * v + acx * w, a[1] + aby * v + acy * w, a[2] + abz * v + acz * w);
}

// Signed distance to a closed triangle mesh (a loft or sweep the recorder
// built), sampled on the kernel's lattice: inside or outside by counting
// crossings along X, exact distances within two cells of the surface (all
// surface nets reads), clamped beyond. Off-lattice points interpolate.
function meshField(mesh, lattice) {
  const { h, origin, dims } = lattice;
  const [nx, ny, nz] = dims;
  const v = mesh.vertices, t = mesh.triangles;
  const tri = (k) => [0, 1, 2].map((c) => [v[3 * t[3 * k + c]], v[3 * t[3 * k + c] + 1], v[3 * t[3 * k + c] + 2]]);
  const band = 2 * h;
  const field = new Float32Array(nx * ny * nz).fill(band);
  // crossings of each X row (j, k) with the surface
  const rows = new Map();
  const nudge = 1e-7 * h; // keep rows off shared triangle edges
  for (let k = 0; k < t.length / 3; k += 1) {
    const [a, b, c] = tri(k);
    const y0 = Math.min(a[1], b[1], c[1]), y1 = Math.max(a[1], b[1], c[1]);
    const z0 = Math.min(a[2], b[2], c[2]), z1 = Math.max(a[2], b[2], c[2]);
    const den = (b[1] - a[1]) * (c[2] - a[2]) - (c[1] - a[1]) * (b[2] - a[2]);
    if (Math.abs(den) < 1e-18) continue;
    for (let j = Math.max(0, Math.ceil((y0 - origin[1]) / h)); j <= Math.min(ny - 1, Math.floor((y1 - origin[1]) / h)); j += 1) {
      const y = origin[1] + j * h + nudge;
      for (let q = Math.max(0, Math.ceil((z0 - origin[2]) / h)); q <= Math.min(nz - 1, Math.floor((z1 - origin[2]) / h)); q += 1) {
        const z = origin[2] + q * h + nudge * 0.37;
        const u = ((y - a[1]) * (c[2] - a[2]) - (c[1] - a[1]) * (z - a[2])) / den;
        const w = ((b[1] - a[1]) * (z - a[2]) - (y - a[1]) * (b[2] - a[2])) / den;
        if (u < 0 || w < 0 || u + w > 1) continue;
        const key = j + ny * q;
        if (!rows.has(key)) rows.set(key, []);
        rows.get(key).push(a[0] + u * (b[0] - a[0]) + w * (c[0] - a[0]));
      }
    }
  }
  for (const [key, xs] of rows) {
    xs.sort((m, n) => m - n);
    const j = key % ny, q = Math.floor(key / ny);
    for (let r = 0; r + 1 < xs.length; r += 2) {
      for (let i = Math.max(0, Math.ceil((xs[r] - origin[0]) / h)); i <= Math.min(nx - 1, Math.floor((xs[r + 1] - origin[0]) / h)); i += 1) {
        field[i + nx * (j + ny * q)] = -band;
      }
    }
  }
  // exact distances near each triangle
  for (let k = 0; k < t.length / 3; k += 1) {
    const [a, b, c] = tri(k);
    const nrm = [
      (b[1] - a[1]) * (c[2] - a[2]) - (b[2] - a[2]) * (c[1] - a[1]),
      (b[2] - a[2]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[2] - a[2]),
      (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    ];
    const nl = Math.hypot(...nrm) || 1;
    const lo = [0, 1, 2].map((d) => Math.max(0, Math.ceil((Math.min(a[d], b[d], c[d]) - band - origin[d]) / h)));
    const hi = [0, 1, 2].map((d) => Math.min(dims[d] - 1, Math.floor((Math.max(a[d], b[d], c[d]) + band - origin[d]) / h)));
    for (let q = lo[2]; q <= hi[2]; q += 1) {
      const z = origin[2] + q * h;
      for (let j = lo[1]; j <= hi[1]; j += 1) {
        const y = origin[1] + j * h;
        for (let i = lo[0]; i <= hi[0]; i += 1) {
          const x = origin[0] + i * h;
          const plane = Math.abs(nrm[0] * (x - a[0]) + nrm[1] * (y - a[1]) + nrm[2] * (z - a[2])) / nl;
          if (plane >= band) continue;
          const idx = i + nx * (j + ny * q);
          const cur = field[idx];
          if (plane >= Math.abs(cur)) continue;
          const d = triangleDistance(x, y, z, a, b, c);
          if (d < Math.abs(cur)) field[idx] = cur < 0 ? -d : d;
        }
      }
    }
  }
  return (x, y, z) => {
    const fx = (x - origin[0]) / h, fy = (y - origin[1]) / h, fz = (z - origin[2]) / h;
    const i = Math.floor(fx), j = Math.floor(fy), q = Math.floor(fz);
    if (i < 0 || j < 0 || q < 0 || i >= nx - 1 || j >= ny - 1 || q >= nz - 1) return band;
    const tx = fx - i, ty = fy - j, tz = fz - q;
    const at = (di, dj, dq) => field[i + di + nx * (j + dj + ny * (q + dq))];
    const lerp = (m, n, s) => m + (n - m) * s;
    return lerp(
      lerp(lerp(at(0, 0, 0), at(1, 0, 0), tx), lerp(at(0, 1, 0), at(1, 1, 0), tx), ty),
      lerp(lerp(at(0, 0, 1), at(1, 0, 1), tx), lerp(at(0, 1, 1), at(1, 1, 1), tx), ty),
      tz
    );
  };
}

// -- culling ------------------------------------------------------------------
// Unions and cutting sets skip members whose bounding box is already farther
// than the best distance found, through a bounding-volume tree. Box distance
// never exceeds a member's own value outside its box (each distance function
// here is exact or an underestimate inside, exact or clamped outside), so
// skipping one never changes a sign.

function flatUnion(node) {
  return node.type === "union" ? node.children.flatMap(flatUnion) : [node];
}

function boxOf(points) {
  const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
  for (const p of points) for (let k = 0; k < 3; k += 1) {
    min[k] = Math.min(min[k], p[k]);
    max[k] = Math.max(max[k], p[k]);
  }
  return { min, max };
}

function placedBox(matrix, lo, hi) {
  const corners = [];
  for (const x of [lo[0], hi[0]]) for (const y of [lo[1], hi[1]]) for (const z of [lo[2], hi[2]]) {
    corners.push([0, 1, 2].map((r) => matrix[4 * r] * x + matrix[4 * r + 1] * y + matrix[4 * r + 2] * z + matrix[4 * r + 3]));
  }
  return boxOf(corners);
}

// The region where a node's value can be negative (null: anywhere).
function influence(node) {
  switch (node.type) {
    case "hole":
      return placedBox(node.matrix, [-node.radius, -node.radius, -node.depth], [node.radius, node.radius, node.depth]);
    case "union": {
      const boxes = node.children.map(influence);
      return boxes.some((b) => !b) ? null : boxOf(boxes.flatMap((b) => [b.min, b.max]));
    }
    case "intersect": {
      const boxes = node.children.map(influence).filter(Boolean);
      if (!boxes.length) return null;
      return { min: [0, 1, 2].map((k) => Math.max(...boxes.map((b) => b.min[k]))), max: [0, 1, 2].map((k) => Math.min(...boxes.map((b) => b.max[k]))) };
    }
    case "subtract":
      return influence(node.base);
    default:
      return nodeBounds(node);
  }
}

// Squared distance from a point to a box (0 inside it).
function boxDistance2(box, x, y, z) {
  const dx = Math.max(box.min[0] - x, 0, x - box.max[0]);
  const dy = Math.max(box.min[1] - y, 0, y - box.max[1]);
  const dz = Math.max(box.min[2] - z, 0, z - box.max[2]);
  return dx * dx + dy * dy + dz * dz;
}

// A member in this box can't go below best: it is farther than a positive
// best, or (best already inside something) the point is outside its box.
function beyond(box, x, y, z, best) {
  if (!box) return false;
  const d2 = boxDistance2(box, x, y, z);
  return best > 0 ? d2 >= best * best : d2 > 0;
}

function bvh(items) {
  const box = items.some((i) => !i.box) ? null : boxOf(items.flatMap((i) => [i.box.min, i.box.max]));
  if (items.length <= 4 || !box) return { box, items };
  const ext = [0, 1, 2].map((k) => box.max[k] - box.min[k]);
  const axis = ext.indexOf(Math.max(...ext));
  const sorted = [...items].sort((a, b) => a.box.min[axis] + a.box.max[axis] - b.box.min[axis] - b.box.max[axis]);
  const half = sorted.length >> 1;
  return { box, left: bvh(sorted.slice(0, half)), right: bvh(sorted.slice(half)) };
}

// min over members, or limit when no member is below it
function unionWithin(items) {
  if (items.length === 1 && !items[0].box) return items[0].fn;
  const root = bvh(items);
  const visit = (node, x, y, z, best) => {
    if (beyond(node.box, x, y, z, best)) return best;
    if (node.items) {
      for (const item of node.items) {
        if (beyond(item.box, x, y, z, best)) continue;
        const d = item.fn(x, y, z);
        if (d < best) best = d;
      }
      return best;
    }
    const dl = node.left.box ? boxDistance2(node.left.box, x, y, z) : 0;
    const dr = node.right.box ? boxDistance2(node.right.box, x, y, z) : 0;
    if (dl <= dr) {
      best = visit(node.left, x, y, z, best);
      return visit(node.right, x, y, z, best);
    }
    best = visit(node.right, x, y, z, best);
    return visit(node.left, x, y, z, best);
  };
  return (x, y, z, limit = Infinity) => visit(root, x, y, z, limit);
}

function compile(node, lattice) {
  switch (node.type) {
    case "union":
      return unionWithin(flatUnion(node).map((c) => ({ fn: compile(c, lattice), box: influence(c) })));
    case "intersect": {
      const fns = node.children.map((c) => compile(c, lattice));
      return (x, y, z) => {
        let d = -Infinity;
        for (const fn of fns) d = Math.max(d, fn(x, y, z));
        return d;
      };
    }
    case "subtract": {
      // A tool only matters where it is nearer than -base: inside the base by
      // more than the tool's distance, or (outside the base) only within the tool.
      const base = compile(node.base, lattice);
      const tool = unionWithin(flatUnion(node.tool).map((c) => ({ fn: compile(c, lattice), box: influence(c) })));
      return (x, y, z) => {
        const b = base(x, y, z);
        const t = tool(x, y, z, b > 0 ? 0 : -b);
        return Math.max(b, -t);
      };
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
    case "loft":
    case "sweep":
      return meshField(node.mesh, lattice);
    default:
      throw new Error(`implicit kernel cannot evaluate ${node.type}`);
  }
}

export function compileSdf(root, lattice) {
  return compile(root, lattice);
}

// Surface nets: one vertex per sign-changing cell (the mean of its edge
// crossings), one quad per sign-changing grid edge.
// The sampling lattice: cell size h, the first sample, samples per axis.
// Cells are as small as the sample budget allows (two-level sampling only
// evaluates the ones near the surface), down to resolution cells across.
export function latticeFor(bounds, { resolution = 640, maxSamples = 2_000_000 } = {}) {
  const ext = [0, 1, 2].map((i) => bounds.max[i] - bounds.min[i]);
  let h = Math.max(Math.max(...ext) / resolution, Math.cbrt((ext[0] * ext[1] * ext[2]) / maxSamples));
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
  return { h, origin, dims };
}

// Samples on the lattice, evaluated exactly only where the surface can be.
// A coarse pass every COARSE cells comes first; a coarse cell whose corners
// all lie farther from the surface than the cell is wide can't hold any of
// it (the distance functions here change no faster than distance does), so
// its fine samples are interpolated from the corners, whose sign they share.
const COARSE = 4;

function sampleTwoLevel(sdf, { h, origin, dims }) {
  const [nx, ny, nz] = dims;
  const values = new Float32Array(nx * ny * nz);
  const coarse = dims.map((n) => Math.ceil((n - 1) / COARSE) + 1);
  const fineOf = (c, n) => Math.min(c * COARSE, n - 1);
  const cv = new Float32Array(coarse[0] * coarse[1] * coarse[2]);
  for (let k = 0; k < coarse[2]; k += 1) {
    const z = origin[2] + fineOf(k, nz) * h;
    for (let j = 0; j < coarse[1]; j += 1) {
      const y = origin[1] + fineOf(j, ny) * h;
      for (let i = 0; i < coarse[0]; i += 1) {
        cv[i + coarse[0] * (j + coarse[1] * k)] = sdf(origin[0] + fineOf(i, nx) * h, y, z);
      }
    }
  }
  const reach = COARSE * h * Math.sqrt(3) * 1.01;
  const c8 = new Float64Array(8);
  for (let k = 0; k + 1 < coarse[2]; k += 1) {
    const k0 = fineOf(k, nz), k1 = fineOf(k + 1, nz);
    for (let j = 0; j + 1 < coarse[1]; j += 1) {
      const j0 = fineOf(j, ny), j1 = fineOf(j + 1, ny);
      for (let i = 0; i + 1 < coarse[0]; i += 1) {
        const i0 = fineOf(i, nx), i1 = fineOf(i + 1, nx);
        let near = false;
        for (let c = 0; c < 8; c += 1) {
          c8[c] = cv[(i + (c & 1)) + coarse[0] * ((j + ((c >> 1) & 1)) + coarse[1] * (k + (c >> 2)))];
          if (Math.abs(c8[c]) < reach) near = true;
        }
        for (let q = k0; q <= k1; q += 1) {
          const tz = k1 > k0 ? (q - k0) / (k1 - k0) : 0;
          const z = origin[2] + q * h;
          for (let r = j0; r <= j1; r += 1) {
            const ty = j1 > j0 ? (r - j0) / (j1 - j0) : 0;
            const y = origin[1] + r * h;
            let idx = i0 + nx * (r + ny * q);
            for (let p = i0; p <= i1; p += 1, idx += 1) {
              if (near) {
                values[idx] = sdf(origin[0] + p * h, y, z);
                continue;
              }
              const tx = i1 > i0 ? (p - i0) / (i1 - i0) : 0;
              const x00 = c8[0] + (c8[1] - c8[0]) * tx, x10 = c8[2] + (c8[3] - c8[2]) * tx;
              const x01 = c8[4] + (c8[5] - c8[4]) * tx, x11 = c8[6] + (c8[7] - c8[6]) * tx;
              const y0 = x00 + (x10 - x00) * ty, y1 = x01 + (x11 - x01) * ty;
              values[idx] = y0 + (y1 - y0) * tz;
            }
          }
        }
      }
    }
  }
  return values;
}

export function meshSdf(sdf, lattice) {
  const { h, origin, dims } = lattice;
  const [nx, ny, nz] = dims;
  const values = sampleTwoLevel(sdf, lattice);
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

export function buildImplicitMesh(program, { resolution = 640 } = {}) {
  const { root, warnings, bounds } = prepareProgram(program.root);
  if (!bounds) {
    throw new Error("program has no solid material");
  }
  const lattice = latticeFor(bounds, { resolution });
  const sdf = compileSdf(root, lattice);
  const mesh = meshSdf(sdf, lattice);
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
