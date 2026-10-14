// Planar cleanup between booleans. three-bvh-csg splits coplanar contact (a
// boss standing on a plate) into thousands of slivers with near-duplicate
// vertices, and every later boolean then works on all of them: the mount
// grew to 26,000 triangles where 270 describe it, and never finished.
//
// A flat face's outline is the set of edges its neighbours (the walls around
// it) have in its plane, and those walls are clean. So each set of coplanar
// triangles facing one way is replaced by a triangulation of the outline the
// walls give it, using the walls' own vertices: edges still meet vertex to
// vertex and the mesh stays closed. A face whose outline can't be traced, or
// whose area would change, is kept as it was.
import * as THREE from "three";

const WELD = 1e-4; // mm: vertices this close are one (positions are float32)
const PLANE = 1e-4; // mm and radians: same plane
const AREA = 1e-4; // relative: a redrawn face keeps its area

function weld(positions) {
  const index = new Map();
  const points = [];
  const ids = new Uint32Array(positions.length / 3);
  for (let i = 0; i < ids.length; i += 1) {
    const x = positions[3 * i], y = positions[3 * i + 1], z = positions[3 * i + 2];
    const key = `${Math.round(x / WELD)},${Math.round(y / WELD)},${Math.round(z / WELD)}`;
    let id = index.get(key);
    if (id === undefined) {
      id = points.length;
      index.set(key, id);
      points.push([x, y, z]);
    }
    ids[i] = id;
  }
  return { points, ids };
}

function planeOf(a, b, c) {
  const u = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
  const v = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
  const n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
  const len = Math.hypot(...n);
  if (len < 1e-9) return null;
  const unit = n.map((x) => x / len);
  return { n: unit, d: unit[0] * a[0] + unit[1] * a[1] + unit[2] * a[2], area: len / 2 };
}

function area2d(pts) {
  let s = 0;
  for (let i = 0; i < pts.length; i += 1) {
    const [x0, y0] = pts[i];
    const [x1, y1] = pts[(i + 1) % pts.length];
    s += x0 * y1 - x1 * y0;
  }
  return s / 2;
}

function inside2d([x, y], poly) {
  let odd = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i, i += 1) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) odd = !odd;
  }
  return odd;
}

function inTriangle([x, y], [a, b, c]) {
  const s = (p, q) => (q[0] - p[0]) * (y - p[1]) - (q[1] - p[1]) * (x - p[0]);
  const d1 = s(a, b), d2 = s(b, c), d3 = s(c, a);
  return !((d1 < 0 || d2 < 0 || d3 < 0) && (d1 > 0 || d2 > 0 || d3 > 0));
}

// 2D frame on a plane with normal n: (u, v, n) right-handed, so a face
// facing +n is counter-clockwise in (u, v).
function frame(n) {
  const h = Math.abs(n[0]) < 0.9 ? [1, 0, 0] : [0, 1, 0];
  let u = [n[1] * h[2] - n[2] * h[1], n[2] * h[0] - n[0] * h[2], n[0] * h[1] - n[1] * h[0]];
  const l = Math.hypot(...u);
  u = u.map((x) => x / l);
  const v = [n[1] * u[2] - n[2] * u[1], n[2] * u[0] - n[0] * u[2], n[0] * u[1] - n[1] * u[0]];
  return (p) => [p[0] * u[0] + p[1] * u[1] + p[2] * u[2], p[0] * v[0] + p[1] * v[1] + p[2] * v[2]];
}

// Chain directed edges into closed loops. Where loops touch at a vertex,
// take the sharpest right turn, which keeps each loop simple.
function chain(edges, flat) {
  const out = new Map();
  for (const [p, q] of edges) {
    if (!out.has(p)) out.set(p, []);
    out.get(p).push(q);
  }
  const turn = (a, b, c) => {
    const [ax, ay] = flat(a), [bx, by] = flat(b), [cx, cy] = flat(c);
    const ux = bx - ax, uy = by - ay, vx = cx - bx, vy = cy - by;
    return Math.atan2(ux * vy - uy * vx, ux * vx + uy * vy);
  };
  const loops = [];
  for (const [first, targets] of out) {
    while (targets.length) {
      const loop = [first];
      let prev = first;
      let v = targets.pop();
      while (v !== first) {
        const ways = out.get(v);
        if (!ways || !ways.length || loop.length > edges.length) return null;
        let pick = 0;
        if (ways.length > 1) {
          let best = Infinity;
          ways.forEach((w, i) => {
            const t = turn(prev, v, w);
            if (t < best) { best = t; pick = i; }
          });
        }
        loop.push(v);
        prev = v;
        v = ways.splice(pick, 1)[0];
      }
      loops.push(loop);
    }
  }
  return loops;
}

function triangulateLoops(loops, flat) {
  const rings = loops.map((ids) => {
    const pts = ids.map(flat);
    return { ids, pts, area: area2d(pts) };
  }).filter((r) => Math.abs(r.area) > 1e-12);
  const outers = rings.filter((r) => r.area > 0).sort((a, b) => a.area - b.area);
  if (!outers.length) return null;
  const groups = outers.map((outer) => ({ outer, holes: [] }));
  for (const hole of rings.filter((r) => r.area < 0)) {
    const owner = groups.find((g) => inside2d(hole.pts[0], g.outer.pts));
    if (!owner) return null;
    owner.holes.push(hole);
  }
  const tris = [];
  let area = 0;
  for (const { outer, holes } of groups) {
    const ids = [...outer.ids, ...holes.flatMap((h) => h.ids)];
    const faces = THREE.ShapeUtils.triangulateShape(
      outer.pts.map(([x, y]) => new THREE.Vector2(x, y)),
      holes.map((h) => h.pts.map(([x, y]) => new THREE.Vector2(x, y)))
    );
    for (const [a, b, c] of faces) tris.push([ids[a], ids[b], ids[c]]);
    area += outer.area + holes.reduce((s, h) => s + h.area, 0);
  }
  return { tris, area };
}

export function cleanupGeometry(geometry) {
  const { points, ids } = weld(geometry.getAttribute("position").array);
  const tris = [];
  const groups = new Map();
  const incident = new Map(); // vertex -> triangles using it
  const planesAt = new Map(); // vertex -> groups (planes) it touches
  // Well-shaped triangles define the planes. Slivers (their normals are
  // noise at float32 precision) join a neighbouring plane that holds them.
  const slivers = [];
  const add = (tri, group, area) => {
    group.tris.push(tri);
    group.area += area;
    const entry = { tri, group };
    tris.push(entry);
    for (const id of tri) {
      if (!incident.has(id)) incident.set(id, []);
      incident.get(id).push(entry);
      if (!planesAt.has(id)) planesAt.set(id, new Set());
      planesAt.get(id).add(group);
    }
  };
  const groupFor = (plane) => {
    const key = [...plane.n.map((x) => Math.round(x / PLANE)), Math.round(plane.d / PLANE)].join(",");
    let group = groups.get(key);
    if (!group) groups.set(key, (group = { plane, tris: [], area: 0 }));
    return group;
  };
  for (let t = 0; t < ids.length; t += 3) {
    const tri = [ids[t], ids[t + 1], ids[t + 2]];
    if (tri[0] === tri[1] || tri[1] === tri[2] || tri[0] === tri[2]) continue;
    const [a, b, c] = tri.map((id) => points[id]);
    const plane = planeOf(a, b, c);
    if (!plane) continue;
    const longest = Math.max(...[[a, b], [b, c], [c, a]].map(([p, q]) => Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2])));
    if (plane.area < 1e-3 * longest * longest) slivers.push({ tri, plane });
    else add(tri, groupFor(plane), plane.area);
  }
  for (const { tri, plane } of slivers) {
    const holds = (g) => g.plane.n[0] * plane.n[0] + g.plane.n[1] * plane.n[1] + g.plane.n[2] * plane.n[2] > 0 &&
      tri.every((id) => Math.abs(g.plane.n[0] * points[id][0] + g.plane.n[1] * points[id][1] + g.plane.n[2] * points[id][2] - g.plane.d) < PLANE * 10);
    let home = null;
    for (const id of tri) for (const e of incident.get(id) || []) if (!home && holds(e.group)) home = e.group;
    add(tri, home || groupFor(plane), plane.area);
  }
  const redrawn = new Map();
  for (const group of groups.values()) {
    if (group.tris.length < 3) continue; // two triangles: already as simple as it gets
    const { n, d } = group.plane;
    const onPlane = (id) => Math.abs(n[0] * points[id][0] + n[1] * points[id][1] + n[2] * points[id][2] - d) < PLANE * 10;
    const flat = frame(n);
    const flatId = (id) => flat(points[id]);
    // Does this face run along q -> p? Usually one of its own edges at q or p
    // lies on that segment in that direction.
    const ownEdge = (q, p) => {
      const a = points[q], b = points[p];
      const d = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
      const len2 = d[0] * d[0] + d[1] * d[1] + d[2] * d[2];
      const along = (id) => {
        const c = points[id];
        const w = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
        const t = (w[0] * d[0] + w[1] * d[1] + w[2] * d[2]) / len2;
        const off = Math.hypot(w[0] - t * d[0], w[1] - t * d[1], w[2] - t * d[2]);
        return off < WELD ? t : null;
      };
      for (const id of [q, p]) {
        for (const e of incident.get(id) || []) {
          if (e.group !== group) continue;
          for (let k = 0; k < 3; k += 1) {
            const t0 = along(e.tri[k]), t1 = along(e.tri[(k + 1) % 3]);
            if (t0 === null || t1 === null || t1 <= t0) continue;
            if (Math.min(t1, 1) - Math.max(t0, 0) > 1e-9) return true;
          }
        }
      }
      return null;
    };
    // Failing that, is the point just left of the segment inside one of this
    // face's triangles at q or p? An edge neither test confirms is left out,
    // and the face then keeps its own triangles.
    const covered = (q, p, pt) => {
      for (const id of [q, p]) {
        for (const e of incident.get(id) || []) {
          if (e.group === group && inTriangle(pt, e.tri.map(flatId))) return true;
        }
      }
      return false;
    };
    // Nearby triangles that aren't part of this face: two rings out through
    // shared vertices, since a wall edge can run between two T-junctions.
    const neighbours = new Set();
    const ring = new Set(group.tris.flat());
    for (let pass = 0; pass < 2; pass += 1) {
      for (const id of [...ring]) {
        for (const e of incident.get(id)) {
          if (e.group === group || neighbours.has(e)) continue;
          if (e.tri.filter(onPlane).length < 2) continue;
          neighbours.add(e);
          for (const v of e.tri) if (onPlane(v)) ring.add(v);
        }
      }
    }
    // Their edges lying in this plane, reversed: the face runs along them the other way.
    const edges = [];
    const seen = new Set();
    for (const { tri } of neighbours) {
      const on = tri.map(onPlane);
      if (on.every(Boolean)) continue; // another face in this plane (facing the other way)
      for (let k = 0; k < 3; k += 1) {
        const p = tri[k], q = tri[(k + 1) % 3];
        if (!on[k] || !on[(k + 1) % 3] || seen.has(`${q},${p}`)) continue;
        const a = flatId(q), b = flatId(p);
        const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
        if (len < 1e-9) continue;
        const eps = Math.min(len * 0.25, WELD * 20);
        const mid = [(a[0] + b[0]) / 2 - ((b[1] - a[1]) / len) * eps, (a[1] + b[1]) / 2 + ((b[0] - a[0]) / len) * eps];
        if (ownEdge(q, p) || covered(q, p, mid)) {
          seen.add(`${q},${p}`);
          edges.push([q, p]);
        }
      }
    }
    let loops = edges.length ? chain(edges, flatId) : null;
    // A vertex on just two planes, in line with its neighbours, is only a split
    // in a straight edge between two faces: both faces drop it.
    if (loops) {
      loops = loops.map((loop) => loop.filter((v, i) => {
        if (planesAt.get(v)?.size !== 2) return true;
        const a = flatId(loop[(i + loop.length - 1) % loop.length]), b = flatId(v), c = flatId(loop[(i + 1) % loop.length]);
        const len = Math.hypot(c[0] - a[0], c[1] - a[1]);
        return len < 1e-9 || Math.abs((c[0] - a[0]) * (b[1] - a[1]) - (c[1] - a[1]) * (b[0] - a[0])) / len > WELD;
      })).filter((loop) => loop.length >= 3);
    }
    const result = loops && triangulateLoops(loops, flatId);
    if (result && Math.abs(result.area - group.area) <= AREA * Math.max(group.area, 1e-6)) {
      redrawn.set(group, result.tris);
    }
  }
  const kept = [];
  for (const { tri, group } of tris) if (!redrawn.has(group)) kept.push(tri);
  for (const faces of redrawn.values()) kept.push(...faces);
  const out = new Float32Array(kept.length * 9);
  kept.forEach((tri, i) => tri.forEach((id, k) => out.set(points[id], 9 * i + 3 * k)));
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(out, 3));
  g.computeVertexNormals();
  return g;
}
