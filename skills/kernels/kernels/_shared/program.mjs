// Shared helpers for the kernel-neutral CSG program that
// scripts/kernels/record.py records from a build123d generator. The mesh and
// implicit kernels both evaluate it; see record.py for the node schema.
//
// Matrices are row-major 4x4 arrays (16 numbers), lengths are millimeters.

export const IDENTITY = Object.freeze([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);

export function multiply(a, b) {
  const out = new Array(16);
  for (let r = 0; r < 4; r += 1) {
    for (let c = 0; c < 4; c += 1) {
      out[r * 4 + c] = a[r * 4] * b[c] + a[r * 4 + 1] * b[4 + c] + a[r * 4 + 2] * b[8 + c] + a[r * 4 + 3] * b[12 + c];
    }
  }
  return out;
}

export function translation(x = 0, y = 0, z = 0) {
  return [1, 0, 0, x, 0, 1, 0, y, 0, 0, 1, z, 0, 0, 0, 1];
}

export function transformPoint(m, [x, y, z]) {
  return [
    m[0] * x + m[1] * y + m[2] * z + m[3],
    m[4] * x + m[5] * y + m[6] * z + m[7],
    m[8] * x + m[9] * y + m[10] * z + m[11]
  ];
}

// Column k of the rotation part: where local axis k points in world space.
export function axisOf(m, k) {
  return [m[k], m[4 + k], m[8 + k]];
}

// Inverse of a rigid transform (rotation + translation): transpose + -R^T t.
export function rigidInverse(m) {
  const t = [m[3], m[7], m[11]];
  return [
    m[0], m[4], m[8], -(m[0] * t[0] + m[4] * t[1] + m[8] * t[2]),
    m[1], m[5], m[9], -(m[1] * t[0] + m[5] * t[1] + m[9] * t[2]),
    m[2], m[6], m[10], -(m[2] * t[0] + m[6] * t[1] + m[10] * t[2]),
    0, 0, 0, 1
  ];
}

export const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
export const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
export const scale = (a, s) => [a[0] * s, a[1] * s, a[2] * s];
export const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
export const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
export const length = (a) => Math.hypot(a[0], a[1], a[2]);
export const normalize = (a) => {
  const n = length(a);
  return n > 0 ? scale(a, 1 / n) : [0, 0, 0];
};

const WORLD_AXES = { X: [1, 0, 0], Y: [0, 1, 0], Z: [0, 0, 1] };

function parallel(a, b) {
  return Math.abs(Math.abs(dot(normalize(a), normalize(b))) - 1) < 1e-9;
}

// -- 2D profiles ------------------------------------------------------------

const CIRCLE_SEGMENTS = 64;
const CORNER_SEGMENTS = 12;

// Outline of one sketch entity as a counter-clockwise polygon in sketch XY.
export function entityOutline(entity) {
  const [cx, cy] = entity.center || [0, 0];
  const rot = ((entity.rotation || 0) * Math.PI) / 180;
  const cos = Math.cos(rot);
  const sin = Math.sin(rot);
  const place = ([x, y]) => [cx + x * cos - y * sin, cy + x * sin + y * cos];
  if (entity.type === "circle") {
    return Array.from({ length: CIRCLE_SEGMENTS }, (_, i) => {
      const a = (2 * Math.PI * i) / CIRCLE_SEGMENTS;
      return place([entity.radius * Math.cos(a), entity.radius * Math.sin(a)]);
    });
  }
  if (entity.type === "polygon") {
    return entity.points.map(place);
  }
  if (entity.type === "rect") {
    const hx = entity.width / 2;
    const hy = entity.height / 2;
    const r = Math.min(entity.cornerRadius || 0, hx, hy);
    if (r <= 0) {
      return [[-hx, -hy], [hx, -hy], [hx, hy], [-hx, hy]].map(place);
    }
    const points = [];
    const corners = [[hx - r, -hy + r, -90], [hx - r, hy - r, 0], [-hx + r, hy - r, 90], [-hx + r, -hy + r, 180]];
    for (const [x, y, start] of corners) {
      for (let i = 0; i <= CORNER_SEGMENTS; i += 1) {
        const a = ((start + (90 * i) / CORNER_SEGMENTS) * Math.PI) / 180;
        points.push(place([x + r * Math.cos(a), y + r * Math.sin(a)]));
      }
    }
    return points;
  }
  throw new Error(`unknown sketch entity ${entity.type}`);
}

export function profileBounds2d(profile) {
  const min = [Infinity, Infinity];
  const max = [-Infinity, -Infinity];
  for (const entity of profile.entities) {
    if (entity.mode === "subtract") {
      continue;
    }
    for (const [x, y] of entityOutline(entity)) {
      min[0] = Math.min(min[0], x);
      min[1] = Math.min(min[1], y);
      max[0] = Math.max(max[0], x);
      max[1] = Math.max(max[1], y);
    }
  }
  return { min, max };
}

export function prismRange(node) {
  if (node.both) {
    return [-Math.abs(node.amount), Math.abs(node.amount)];
  }
  return node.amount >= 0 ? [0, node.amount] : [node.amount, 0];
}

// Frame for revolving a sketch profile: maps world points to (radial, axial)
// sketch-plane coordinates. Requires the axis to lie in the sketch plane.
export function revolveFrame(node) {
  const origin = node.axis.origin;
  const dir = normalize(node.axis.direction);
  const m = node.matrix;
  const planeNormal = axisOf(m, 2);
  if (Math.abs(dot(planeNormal, dir)) > 1e-6) {
    throw new Error("revolve axis must lie in the sketch plane");
  }
  // In-plane direction perpendicular to the axis, pointing at the profile.
  let radial = normalize(cross(dir, planeNormal));
  const { min, max } = profileBounds2d(node.profile);
  const centroid = transformPoint(m, [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2, 0]);
  if (dot(sub(centroid, origin), radial) < 0) {
    radial = scale(radial, -1);
  }
  // Sketch-local 2D coordinates of the axis origin and the two directions.
  const inv = rigidInverse(m);
  const toLocal = (p) => transformPoint(inv, p);
  const o2 = toLocal(origin);
  const d2 = sub(toLocal(add(origin, dir)), o2);
  const r2 = sub(toLocal(add(origin, radial)), o2);
  return { origin, dir, radial, o2, d2, r2 };
}

// -- bounds -----------------------------------------------------------------

function boxCorners(min, max) {
  const out = [];
  for (const x of [min[0], max[0]]) {
    for (const y of [min[1], max[1]]) {
      for (const z of [min[2], max[2]]) {
        out.push([x, y, z]);
      }
    }
  }
  return out;
}

function leafLocalBox(node) {
  switch (node.type) {
    case "box": {
      const [l, w, h] = node.size;
      return [[-l / 2, -w / 2, -h / 2], [l / 2, w / 2, h / 2]];
    }
    case "cylinder":
      return [[-node.radius, -node.radius, -node.height / 2], [node.radius, node.radius, node.height / 2]];
    case "cone": {
      const r = Math.max(node.bottomRadius, node.topRadius);
      return [[-r, -r, -node.height / 2], [r, r, node.height / 2]];
    }
    case "sphere":
      return [[-node.radius, -node.radius, -node.radius], [node.radius, node.radius, node.radius]];
    case "prism": {
      const { min, max } = profileBounds2d(node.profile);
      const [z0, z1] = prismRange(node);
      return [[min[0], min[1], z0], [max[0], max[1], z1]];
    }
    default:
      return null;
  }
}

function revolveWorldBox(node) {
  const frame = revolveFrame(node);
  let tMin = Infinity;
  let tMax = -Infinity;
  let rMax = 0;
  for (const entity of node.profile.entities) {
    if (entity.mode === "subtract") continue;
    for (const [x, y] of entityOutline(entity)) {
      const p = transformPoint(node.matrix, [x, y, 0]);
      const rel = sub(p, frame.origin);
      const t = dot(rel, frame.dir);
      tMin = Math.min(tMin, t);
      tMax = Math.max(tMax, t);
      rMax = Math.max(rMax, length(sub(rel, scale(frame.dir, t))));
    }
  }
  const a = add(frame.origin, scale(frame.dir, tMin));
  const b = add(frame.origin, scale(frame.dir, tMax));
  return {
    min: [0, 1, 2].map((i) => Math.min(a[i], b[i]) - rMax),
    max: [0, 1, 2].map((i) => Math.max(a[i], b[i]) + rMax)
  };
}

function mergeBox(acc, box) {
  if (!box) return acc;
  if (!acc) return { min: [...box.min], max: [...box.max] };
  return {
    min: acc.min.map((v, i) => Math.min(v, box.min[i])),
    max: acc.max.map((v, i) => Math.max(v, box.max[i]))
  };
}

// World AABB of the material a node can occupy. Holes and subtraction tools
// never add material, so they are skipped.
export function nodeBounds(node) {
  switch (node.type) {
    case "union":
      return node.children.reduce((acc, child) => mergeBox(acc, nodeBounds(child)), null);
    case "intersect":
      return node.children.reduce((acc, child) => mergeBox(acc, nodeBounds(child)), null);
    case "subtract":
      return nodeBounds(node.base);
    case "fillet":
    case "chamfer":
      return nodeBounds(node.child);
    case "hole":
      return null;
    case "revolve":
      return revolveWorldBox(node);
    case "loft":
    case "sweep": {
      const v = node.mesh.vertices;
      let box = null;
      for (let i = 0; i + 2 < v.length; i += 3) box = mergeBox(box, { min: [v[i], v[i + 1], v[i + 2]], max: [v[i], v[i + 1], v[i + 2]] });
      return box;
    }
    default: {
      const local = leafLocalBox(node);
      if (!local) return null;
      return boxCorners(local[0], local[1])
        .map((p) => transformPoint(node.matrix, p))
        .reduce((acc, p) => mergeBox(acc, { min: p, max: p }), null);
    }
  }
}

// -- lowering ---------------------------------------------------------------

// Holes with depth null drill "through everything": give them a concrete
// depth from the program's bounds so every kernel can treat them as
// ordinary finite cylinders (centered, reaching depth each way).
export function resolveHoleDepths(root) {
  const bounds = nodeBounds(root);
  const diagonal = bounds ? length(sub(bounds.max, bounds.min)) : 1;
  const visit = (node) => {
    if (node.type === "hole") {
      return node.depth == null ? { ...node, depth: 2 * diagonal + 1 } : node;
    }
    return mapChildren(node, visit);
  };
  return visit(root);
}

export function mapChildren(node, fn) {
  const out = { ...node };
  if (node.child) out.child = fn(node.child);
  if (node.base) out.base = fn(node.base);
  if (node.tool) out.tool = fn(node.tool);
  if (node.children) out.children = node.children.map(fn);
  return out;
}

// Box -> prism with the extrusion axis on local axis k (0, 1 or 2), so edge
// modifiers along that axis can round or cut the profile's corners.
function boxAsPrism(node, k) {
  const [l, w, h] = node.size;
  // Cyclic permutations keep the frame right-handed.
  const perms = [
    { cols: [1, 2, 0], profile: [w, h], amount: l },
    { cols: [2, 0, 1], profile: [h, l], amount: w },
    { cols: [0, 1, 2], profile: [l, w], amount: h }
  ];
  const { cols, profile, amount } = perms[k];
  const perm = new Array(16).fill(0);
  cols.forEach((src, dst) => {
    perm[src * 4 + dst] = 1;
  });
  perm[15] = 1;
  return {
    type: "prism",
    profile: {
      entities: [{ type: "rect", width: profile[0], height: profile[1], cornerRadius: 0, center: [0, 0], rotation: 0, mode: "add" }]
    },
    amount,
    both: false,
    matrix: multiply(multiply(node.matrix, perm), translation(0, 0, -amount / 2))
  };
}

function chamferedRect(entity, c) {
  const hx = entity.width / 2;
  const hy = entity.height / 2;
  const d = Math.min(c, hx, hy);
  return {
    type: "polygon",
    points: [
      [-hx + d, -hy], [hx - d, -hy], [hx, -hy + d], [hx, hy - d],
      [hx - d, hy], [-hx + d, hy], [-hx, hy - d], [-hx, -hy + d]
    ],
    center: entity.center,
    rotation: entity.rotation,
    mode: entity.mode
  };
}

function applyCornerModifier(prism, kind, size) {
  return {
    ...prism,
    profile: {
      entities: prism.profile.entities.map((entity) => {
        if (entity.type !== "rect") return entity;
        if (kind === "fillet") return { ...entity, cornerRadius: Math.max(entity.cornerRadius || 0, size) };
        return chamferedRect(entity, size);
      })
    }
  };
}

// Lower fillet/chamfer nodes the kernels can express:
//  - {parallel: A} edges on one box or prism extruded along A (optionally
//    with things cut from it) become rounded (fillet) or cut (chamfer)
//    rectangle corners in the profile. Rounding a subtracted rectangle's
//    corners is the fillet of the pocket's concave edges, so box and prism
//    tools get the same treatment. Unions are left sharp: rounding each
//    member would also round corners that disappear inside the union.
//  - {all: true} fillets on boxes and prisms become an `edgeRadius` that the
//    implicit kernel rounds exactly and the mesh kernel reports.
// Anything else uses the node's resolved tools when the recorder found them
// (scripts/kernel_tools/resolve.py), and otherwise stays sharp with a warning.
export function lowerEdgeModifiers(root) {
  const warnings = [];
  const lower = (node) => {
    if (node.type !== "fillet" && node.type !== "chamfer") {
      return mapChildren(node, lower);
    }
    if (!node.tools) return lowerOne(node);
    // The kernel's own shapes first (exact rounded corners and boxes); where
    // they would draw an edge sharp, the tools resolved from the real build:
    // the material a fillet or chamfer removes from convex edges and adds to
    // concave ones.
    const before = warnings.length;
    const own = lowerOne(node);
    if (warnings.length === before) return own;
    warnings.length = before;
    let out = lower(node.child);
    if (node.tools.cut) out = { type: "subtract", base: out, tool: lower(node.tools.cut) };
    if (node.tools.fill) out = { type: "union", children: [out, lower(node.tools.fill)] };
    return out;
  };
  const lowerOne = (node) => {
    const child = lower(node.child);
    const { select, size } = node;
    const label = `${node.type} ${size} mm`;
    if (select?.parallel) {
      const axis = WORLD_AXES[select.parallel];
      let skipped = 0;
      // One box or prism: round its corners if it runs along the axis.
      const round = (n) => {
        if (n.type === "box") {
          const k = [0, 1, 2].find((i) => parallel(axisOf(n.matrix, i), axis));
          if (k !== undefined) return applyCornerModifier(boxAsPrism(n, k), node.type, size);
        } else if (n.type === "prism" && parallel(axisOf(n.matrix, 2), axis)) {
          return applyCornerModifier(n, node.type, size);
        }
        skipped += 1;
        return n;
      };
      // Tools: every box/prism in them; holes and round solids have no
      // straight edges to modify.
      const roundTools = (n) => (n.type === "box" || n.type === "prism") ? round(n)
        : n.type === "union" ? mapChildren(n, roundTools) : n;
      const solid = (n) => n.type === "box" || n.type === "prism" || (n.type === "subtract" && solid(n.base));
      const apply = (n) => n.type === "subtract"
        ? { ...n, base: apply(n.base), tool: roundTools(n.tool) }
        : round(n);
      if (!solid(child)) {
        warnings.push(`${label} on edges of combined shapes is shown sharp`);
        return child;
      }
      const out = apply(child);
      if (skipped) {
        warnings.push(`${label}: edges not along the extrusion axis are shown sharp`);
      }
      return out;
    }
    if (select?.all && node.type === "fillet" && (child.type === "box" || child.type === "prism")) {
      return { ...child, edgeFillet: size };
    }
    warnings.push(`${label} on ${select?.custom || "these edges"} is shown sharp`);
    return child;
  };
  return { root: lower(root), warnings };
}

export function prepareProgram(root) {
  const lowered = lowerEdgeModifiers(resolveHoleDepths(root));
  return { root: lowered.root, warnings: lowered.warnings, bounds: nodeBounds(lowered.root) };
}
