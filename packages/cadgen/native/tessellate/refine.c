/*
 * Part of tessellate.c's unity build: a face's raw mesh -- its base triangulation refined to
 * the surface, its boundary vertices pinned to the model edges they lie on.
 *
 * Refinement runs in rounds, two phases each: MARK the edges whose 3D midpoint strays past the
 * tolerance or whose ends' normals part past the angle, and a triangle's longest uv edge where its
 * centre strays or its facet tilts off the surface; then SUBDIVIDE by the marks, each midpoint
 * made once for the triangles either side. A triangle a round leaves whole marks nothing in the
 * rounds after, so only the triangles the last split made are looked at.
 */

typedef struct {
  i32 ord;
  double f;
} label;

typedef VEC(label) labels;

typedef struct {
  i32 vertex;
  i32 alive;
  labels l;
} bentry;

/* A face's boundary vertices, each with the model edges it lies on (and where along them), in the
 * order they were added, vertex -> labels. */
typedef struct {
  i32 *slot; /* vertex -> its entry, or -1 */
  i64 slot_cap;
  VEC(bentry) entries;
} bmap;

static void bmap_reserve(arena *A, bmap *b, i64 vertices) {
  if (vertices <= b->slot_cap) return;
  i64 cap = b->slot_cap ? b->slot_cap : 64;
  while (cap < vertices) cap *= 2;
  i32 *slot = (i32 *)arena_alloc(A, sizeof(i32) * (size_t)cap);
  if (b->slot_cap) memcpy(slot, b->slot, sizeof(i32) * (size_t)b->slot_cap);
  for (i64 i = b->slot_cap; i < cap; i++) slot[i] = -1;
  b->slot = slot;
  b->slot_cap = cap;
}

static inline labels *bmap_get(const bmap *b, i64 vertex) {
  if (vertex < 0 || vertex >= b->slot_cap || b->slot[vertex] < 0) return NULL;
  return &b->entries.v[b->slot[vertex]].l;
}

static inline int bmap_has(const bmap *b, i64 vertex) { return bmap_get(b, vertex) != NULL; }

static void bmap_set(arena *A, bmap *b, i64 vertex, labels l) {
  bmap_reserve(A, b, vertex + 1);
  if (b->slot[vertex] >= 0) {
    b->entries.v[b->slot[vertex]].l = l;
    return;
  }
  bentry e = {(i32)vertex, 1, l};
  b->slot[vertex] = (i32)b->entries.n;
  PUSH(A, b->entries, e);
}

static void bmap_pop(bmap *b, i64 vertex) {
  if (vertex < 0 || vertex >= b->slot_cap || b->slot[vertex] < 0) return;
  b->entries.v[b->slot[vertex]].alive = 0;
  b->slot[vertex] = -1;
}

static void bmap_clear(bmap *b) {
  for (i64 k = 0; k < b->entries.n; k++)
    if (b->entries.v[k].alive) b->slot[b->entries.v[k].vertex] = -1;
  b->entries.n = 0;
}

/* Who made a face's triangles. */
enum { SOURCE_LIBRARY = 0, SOURCE_OCCT = 1 };

/* One face's mesh before the faces are made to meet: its vertices in the face's parameters, in
 * space and their normals (the surface's own, whichever way the face is oriented), its triangles
 * wound positively in the parameters, and the model edges its boundary vertices lie on. */
typedef struct {
  int source;
  vd uv, xyz, nrm;
  vi tri;
  bmap boundary;
  ring *loops;     /* a library face's sampled trim loops; an OCCT face has none */
  i64 loop_count;
  int singular_grid;
  vb minted; /* per vertex: made by conformity */
  i64 minted_count;
} raw_face;

/* Whether the mesh edge (p, q) runs along a fixed model edge (OCCT's polyline), which a face the
 * library meshes never splits: the face OCCT meshed across it has exactly the polyline's knots. */
static int on_fixed_edge(const bmap *boundary, const shared *edges, i32 edge_count, i32 p, i32 q) {
  const labels *lp = bmap_get(boundary, p), *lq = bmap_get(boundary, q);
  if (!lp || !lq) return 0;
  for (i64 i = 0; i < lp->n; i++) {
    i32 ord = lp->v[i].ord;
    if (ord <= 0 || ord > edge_count || !edges[ord - 1].present || !edges[ord - 1].fixed) continue;
    for (i64 j = 0; j < lq->n; j++)
      if (lq->v[j].ord == ord) return 1;
  }
  return 0;
}

/* Each triangle split by its marked edges into four, three or two, in order; which of them are new;
 * and each new midpoint's edge, numbered from `first_index` in the order the triangles first reach
 * them. */
static void split_marked(arena *A, arena *T, const vi *tri, const u64 *marked, i64 marked_n, i64 first_index, vi *out,
                         vb *fresh, vu *midpoints) {
  emap numbers;
  emap_init(&numbers, T, marked_n);
  midpoints->n = 0;
  vi result = {NULL, 0, 0};
  RESERVE(A, result, tri->n * 2);
  vb flags = {NULL, 0, 0};
  RESERVE(T, flags, tri->n);
  for (i64 t = 0; t < tri->n; t += 3) {
    i32 a = tri->v[t], b = tri->v[t + 1], c = tri->v[t + 2];
    i32 corners[3] = {a, b, c};
    i64 mid[3];
    int splits = 0;
    for (int k = 0; k < 3; k++) {
      u64 key = edge_key(corners[k], corners[(k + 1) % 3]);
      i64 lo = 0, hi = marked_n;
      while (lo < hi) {
        i64 m = (lo + hi) >> 1;
        if (marked[m] < key) lo = m + 1;
        else hi = m;
      }
      if (lo < marked_n && marked[lo] == key) {
        int made;
        i64 *slot = emap_ref(&numbers, key, -1, &made);
        if (made) *slot = first_index + midpoints->n;
        mid[k] = *slot;
        if (made) PUSH(T, *midpoints, key);
        splits++;
      } else {
        mid[k] = -1;
      }
    }
    i32 m1 = (i32)mid[0], m2 = (i32)mid[1], m3 = (i32)mid[2];
    int sab = mid[0] >= 0, sbc = mid[1] >= 0, sca = mid[2] >= 0;
    if (splits == 0) {
      PUSH3(A, result, a, b, c);
      PUSH(T, flags, 0);
    } else if (splits == 3) {
      PUSH3(A, result, a, m1, m3);
      PUSH3(A, result, m1, b, m2);
      PUSH3(A, result, m3, m2, c);
      PUSH3(A, result, m1, m2, m3);
      for (int k = 0; k < 4; k++) PUSH(T, flags, 1);
    } else if (splits == 2) {
      /* Turned so the edge left whole is (c, a). */
      i32 va, vb, vc, x1, x2;
      if (sab && sbc) {
        va = a; vb = b; vc = c; x1 = m1; x2 = m2;
      } else if (sbc && sca) {
        va = b; vb = c; vc = a; x1 = m2; x2 = m3;
      } else {
        va = c; vb = a; vc = b; x1 = m3; x2 = m1;
      }
      PUSH3(A, result, va, x1, x2);
      PUSH3(A, result, va, x2, vc);
      PUSH3(A, result, x1, vb, x2);
      for (int k = 0; k < 3; k++) PUSH(T, flags, 1);
    } else {
      /* Turned so the split edge is (a, b). */
      i32 va, vb, vc, m;
      if (sab) {
        va = a; vb = b; vc = c; m = m1;
      } else if (sbc) {
        va = b; vb = c; vc = a; m = m2;
      } else {
        va = c; vb = a; vc = b; m = m3;
      }
      PUSH3(A, result, va, m, vc);
      PUSH3(A, result, m, vb, vc);
      PUSH2(T, flags, 1, 1);
    }
  }
  *out = result;
  fresh->v = flags.v;
  fresh->n = flags.n;
  fresh->cap = flags.cap;
}

/* The key of the edge a triangle with no edge past the tolerance marks -- its longest uv edge not
 * along a fixed model edge, where its centre strays from the surface or its facet tilts off the
 * surface's normal -- or 0 for none. */
static int triangle_mark(run *r, const host *h, const surface *s, i32 a, i32 b, i32 c, const double *uv,
                         const double *xyz, const double *nrm, double chord_limit, double angle_cos,
                         const bmap *boundary, const shared *edges, i32 edge_count, u64 *key) {
  double ua = uv[2 * a], va = uv[2 * a + 1], ub = uv[2 * b], vb = uv[2 * b + 1], uc = uv[2 * c], vc = uv[2 * c + 1];
  double sc[3];
  surface_value(r, h, s, (ua + ub + uc) / 3, (va + vb + vc) / 3, sc);
  const double *pa = xyz + 3 * a, *pb = xyz + 3 * b, *pc = xyz + 3 * c;
  double fx = (pa[0] + pb[0] + pc[0]) / 3, fy = (pa[1] + pb[1] + pc[1]) / 3, fz = (pa[2] + pb[2] + pc[2]) / 3;
  double e1x = pb[0] - pa[0], e1y = pb[1] - pa[1], e1z = pb[2] - pa[2];
  double e2x = pc[0] - pa[0], e2y = pc[1] - pa[1], e2z = pc[2] - pa[2];
  double f0 = e1y * e2z - e1z * e2y, f1 = e1z * e2x - e1x * e2z, f2 = e1x * e2y - e1y * e2x;
  double facet = sqrt(f0 * f0 + f1 * f1 + f2 * f2);
  const double *n = nrm + 3 * a;
  int misaligned = facet > 1e-30 && fabs((f0 * n[0] + f1 * n[1] + f2 * n[2]) / facet) < angle_cos;
  double dx = sc[0] - fx, dy = sc[1] - fy, dz = sc[2] - fz;
  if (!misaligned && sqrt(dx * dx + dy * dy + dz * dz) <= chord_limit) return 0;
  i32 corners[3] = {a, b, c};
  i32 lp = a, lq = b;
  double longest = -1.0;
  for (int k = 0; k < 3; k++) {
    i32 p = corners[k], q = corners[(k + 1) % 3];
    if (on_fixed_edge(boundary, edges, edge_count, p, q)) continue;
    double du = uv[2 * p] - uv[2 * q], dv = uv[2 * p + 1] - uv[2 * q + 1];
    double sq = du * du + dv * dv;
    if (sq > longest) {
      longest = sq;
      lp = p;
      lq = q;
    }
  }
  if (longest < 0) return 0;
  *key = edge_key(lp, lq);
  return 1;
}

/* Every vertex on a trim segment of a shared edge labelled with the edge and its fraction there,
 * and put at that fraction's point. Each vertex is held against the segments of its grid cell and
 * of every cell the grid's snap can have moved it out of.
 *
 * Each vertex settles its (vertex, segment) pairs in turn: its own cell's, then each moved-from
 * cell's in key order. Holding a pair twice changes nothing (a match only ever replaces a farther
 * one), so none is deduplicated. The labels land in a fixed order: the vertices an own cell
 * matched, then those only a moved-from cell did. A vertex outside a segment's box grown past the
 * farthest a match can sit fails the segment without the test. */
typedef struct {
  i32 ord;
  double distance, f;
} choice;

typedef struct {
  i32 vertex, ord;
  double fraction;
  labels l;
} pinned;

typedef VEC(choice) vchoice;
typedef VEC(pinned) vpinned;

typedef struct {
  double lo_x, hi_x, lo_y, hi_y;
} reach_box;

/* The cell along one direction: the floor, clipped to the grid's cells, a NaN left NaN. */
static inline double cell_along(double x, double min, double step, i64 steps) {
  double f = floor((x - min) / step);
  f = 0.0 >= f ? 0.0 : f;
  return (double)(steps - 1) <= f ? (double)(steps - 1) : f;
}

static inline i64 cell_key(const grid *g, double fu, double fv) {
  return (fu == fu && fv == fv) ? (i64)fu * g->steps_v + (i64)fv : -1;
}

/* One (vertex, segment) pair, folded into the vertex's best match per edge. */
static inline void pin_against(arena *T, const gsegment *s, const reach_box *box, double px, double py,
                               double on_eps_sq, double corner_eps_sq, vchoice *choices) {
  if (!s->m.has || px < box->lo_x || px > box->hi_x || py < box->lo_y || py > box->hi_y) return;
  double dx = s->x1 - s->x0, dy = s->y1 - s->y0;
  double length_sq = dx * dx + dy * dy;
  double t = length_sq > 0 ? ((px - s->x0) * dx + (py - s->y0) * dy) / length_sq : 0.0;
  t = 1.0 <= t ? 1.0 : t;
  t = 0.0 >= t ? 0.0 : t;
  double qx = s->x0 + t * dx, qy = s->y0 + t * dy;
  double dist_sq = (px - qx) * (px - qx) + (py - qy) * (py - qy);
  int on_segment = dist_sq < on_eps_sq;
  double d0 = (px - s->x0) * (px - s->x0) + (py - s->y0) * (py - s->y0);
  double d1 = (px - s->x1) * (px - s->x1) + (py - s->y1) * (py - s->y1);
  int at_start = !on_segment && d0 < corner_eps_sq;
  int at_end = !on_segment && !at_start && d1 < corner_eps_sq;
  if (!(on_segment || at_start || at_end)) return;
  double distance = on_segment ? dist_sq : at_start ? d0 : d1;
  double along = on_segment ? t : at_start ? 0.0 : 1.0;
  i64 existing = -1;
  for (i64 j = 0; j < choices->n; j++)
    if (choices->v[j].ord == s->ord) existing = j;
  if (existing >= 0 && choices->v[existing].distance <= distance) return;
  /* A segment's ends snap to their exact fractions, so a corner dedups across faces. */
  along = along < 1e-9 ? 0 : along > 1 - 1e-9 ? 1 : along;
  choice c = {s->ord, distance, s->m.f0 + along * (s->m.f1 - s->m.f0)};
  if (existing >= 0) choices->v[existing] = c;
  else PUSH(T, *choices, c);
}

static void pin_boundary(run *r, arena *A, arena *T, const host *h, const grid *g, const vd *uv, vd *xyz, bmap *boundary,
                         const shared *edges, i32 edge_count) {
  i64 count = uv->n / 2;
  if (!count) return;
  double max_u = 0.0, max_v = 0.0;
  int nan_u = 0, nan_v = 0;
  for (i64 i = 0; i < count; i++) {
    double au = fabs(uv->v[2 * i]), av = fabs(uv->v[2 * i + 1]);
    if (au != au) nan_u = 1;
    else if (au > max_u || i == 0) max_u = au;
    if (av != av) nan_v = 1;
    else if (av > max_v || i == 0) max_v = av;
  }
  if (nan_u) max_u = NAN;
  if (nan_v) max_v = NAN;
  double eps = py_max(py_max(max_u, max_v), 1) * 1e-7;
  double eps_sq = eps * eps, corner_eps_sq = eps_sq * 16;
  double snap_u = g->snap_u, snap_v = g->snap_v;
  double on_eps_sq = py_max(eps_sq, snap_u * snap_u + snap_v * snap_v);
  i64 cells = g->steps_u * g->steps_v;
  int any_cell = 0;
  for (i64 k = 0; k < cells && !any_cell; k++)
    if (g->by_cell[k].n) any_cell = 1;
  if (!any_cell) return;
  /* Each segment's box, grown past the farthest a match can sit by more than rounding moves it. */
  double reach = sqrt(on_eps_sq > corner_eps_sq ? on_eps_sq : corner_eps_sq) * (1 + 1e-6);
  reach_box *boxes = (reach_box *)arena_alloc(T, sizeof(reach_box) * (size_t)(g->segments.n ? g->segments.n : 1));
  for (i64 k = 0; k < g->segments.n; k++) {
    const gsegment *s = &g->segments.v[k];
    reach_box *b = &boxes[k];
    if (s->x0 != s->x0 || s->x1 != s->x1 || s->y0 != s->y0 || s->y1 != s->y1) {
      b->lo_x = b->lo_y = -INFINITY;
      b->hi_x = b->hi_y = INFINITY;
      continue;
    }
    double size = fabs(s->x0);
    size = fabs(s->x1) > size ? fabs(s->x1) : size;
    size = fabs(s->y0) > size ? fabs(s->y0) : size;
    size = fabs(s->y1) > size ? fabs(s->y1) : size;
    double grow = reach + size * 1e-12;
    b->lo_x = (s->x0 < s->x1 ? s->x0 : s->x1) - grow;
    b->hi_x = (s->x0 < s->x1 ? s->x1 : s->x0) + grow;
    b->lo_y = (s->y0 < s->y1 ? s->y0 : s->y1) - grow;
    b->hi_y = (s->y0 < s->y1 ? s->y1 : s->y0) + grow;
  }
  int shifted = snap_u != 0.0 || snap_v != 0.0;
  vchoice choices = {NULL, 0, 0};
  vpinned early = {NULL, 0, 0}, late = {NULL, 0, 0};
  int any_pair = 0;
  for (i64 i = 0; i < count; i++) {
    double u = uv->v[2 * i], v = uv->v[2 * i + 1];
    double fu = cell_along(u, g->min_u, g->du, g->steps_u), fv = cell_along(v, g->min_v, g->dv, g->steps_v);
    i64 own = cell_key(g, fu, fv);
    choices.n = 0;
    if (own >= 0 && own < cells) {
      const vl *bucket = &g->by_cell[own];
      if (bucket->n) any_pair = 1;
      for (i64 k = 0; k < bucket->n; k++)
        pin_against(T, &g->segments.v[bucket->v[k]], &boxes[bucket->v[k]], u, v, on_eps_sq, corner_eps_sq, &choices);
    }
    int matched_own = choices.n > 0;
    if (shifted) {
      double um = cell_along(u - snap_u, g->min_u, g->du, g->steps_u);
      double up = cell_along(u + snap_u, g->min_u, g->du, g->steps_u);
      double vm = cell_along(v - snap_v, g->min_v, g->dv, g->steps_v);
      double vp = cell_along(v + snap_v, g->min_v, g->dv, g->steps_v);
      if (!(um == fu && up == fu && vm == fv && vp == fv)) {
        i64 keys[4] = {cell_key(g, um, vm), cell_key(g, um, vp), cell_key(g, up, vm), cell_key(g, up, vp)};
        for (int a = 1; a < 4; a++)
          for (int b = a; b > 0 && keys[b] < keys[b - 1]; b--) {
            i64 swap = keys[b];
            keys[b] = keys[b - 1];
            keys[b - 1] = swap;
          }
        for (int k = 0; k < 4; k++) {
          i64 key = keys[k];
          if ((k && key == keys[k - 1]) || key == own || key < 0 || key >= cells) continue;
          const vl *bucket = &g->by_cell[key];
          if (bucket->n) any_pair = 1;
          for (i64 j = 0; j < bucket->n; j++)
            pin_against(T, &g->segments.v[bucket->v[j]], &boxes[bucket->v[j]], u, v, on_eps_sq, corner_eps_sq,
                        &choices);
        }
      }
    }
    if (!choices.n) continue;
    /* The labels, the one the vertex is pinned through first (a stable sort on that alone). */
    i64 pin = -1, valid = 0;
    double pin_distance = 0.0, pin_fraction = 0.0;
    i32 pin_ord = 0;
    for (i64 j = 0; j < choices.n; j++) {
      choice *c = &choices.v[j];
      if (c->ord <= 0 || c->ord > edge_count || !edges[c->ord - 1].present) continue;
      double fraction = (edges[c->ord - 1].closed && c->f >= 1 - 1e-12) ? 0 : c->f;
      valid++;
      if (pin < 0 || c->distance < pin_distance) {
        pin = j;
        pin_distance = c->distance;
        pin_ord = c->ord;
        pin_fraction = fraction;
      }
    }
    if (!valid) continue;
    pinned p = {(i32)i, pin_ord, pin_fraction, {NULL, 0, 0}};
    RESERVE(A, p.l, valid);
    for (int first = 1; first >= 0; first--)
      for (i64 j = 0; j < choices.n; j++) {
        choice *c = &choices.v[j];
        if (c->ord <= 0 || c->ord > edge_count || !edges[c->ord - 1].present) continue;
        label made = {c->ord, (edges[c->ord - 1].closed && c->f >= 1 - 1e-12) ? 0 : c->f};
        if ((made.ord == pin_ord && made.f == pin_fraction) == first) p.l.v[p.l.n++] = made;
      }
    if (matched_own) PUSH(T, early, p);
    else PUSH(T, late, p);
  }
  if (!any_pair) return;
  bmap_reserve(A, boundary, count);
  for (int pass = 0; pass < 2; pass++) {
    const vpinned *list = pass ? &late : &early;
    for (i64 k = 0; k < list->n; k++) {
      const pinned *p = &list->v[k];
      bmap_set(A, boundary, p->vertex, p->l);
      edge_point_at(r, h, &edges[p->ord - 1], p->fraction, xyz->v + 3 * p->vertex);
    }
  }
}

/* The run's arenas, as each stage takes them: A for what outlives the face, F for the face's own
 * working state, T for temporaries a step releases. */
typedef struct {
  arena *A, *F, *T;
} arenas;

static void copy_ring(arena *A, const ring *from, ring *to) {
  memset(to, 0, sizeof *to);
  RESERVE(A, to->uv, from->uv.n);
  memcpy(to->uv.v, from->uv.v, sizeof(double) * (size_t)from->uv.n);
  to->uv.n = from->uv.n;
  RESERVE(A, to->ords, from->ords.n);
  memcpy(to->ords.v, from->ords.v, sizeof(i32) * (size_t)from->ords.n);
  to->ords.n = from->ords.n;
  RESERVE(A, to->metas, from->metas.n);
  memcpy(to->metas.v, from->metas.v, sizeof(meta) * (size_t)from->metas.n);
  to->metas.n = from->metas.n;
}

/* The face's raw mesh, or 0 for none (no loops, or a grid that made no triangle). A face its trims
 * cannot resolve fails E_UNRESOLVED. */
static int tessellate_face_raw(run *r, arenas M, const host *h, const face *f, const pcurve *pcurves, double scale,
                               double chord, double angle, const shared *edges, i32 edge_count, raw_face *out) {
  memset(out, 0, sizeof *out);
  if (!f->loop_count) return 0;
  double rough_limit = LOOP_TOLERANCE * scale;
  ring *loops = (ring *)arena_alloc(M.F, sizeof(ring) * (size_t)f->loop_count);
  i64 loop_count = 0;
  for (i32 l = 0; l < f->loop_count; l++) {
    sample_loop(r, M.F, M.T, h, f, pcurves, l, rough_limit, edges, edge_count, &loops[loop_count]);
    if (loops[loop_count].uv.n / 2 >= 3) loop_count++;
  }
  if (!loop_count) fail(r, E_UNRESOLVED);
  double min_u = INFINITY, min_v = INFINITY, max_u = -INFINITY, max_v = -INFINITY;
  for (i64 l = 0; l < loop_count; l++) {
    for (i64 i = 0; i < loops[l].uv.n / 2; i++) {
      double u = loops[l].uv.v[2 * i], v = loops[l].uv.v[2 * i + 1];
      if (u < min_u) min_u = u;
      if (u > max_u) max_u = u;
      if (v < min_v) min_v = v;
      if (v > max_v) max_v = v;
    }
  }
  double probe_uv[5][2] = {{min_u, min_v}, {max_u, min_v}, {min_u, max_v}, {max_u, max_v},
                           {(min_u + max_u) / 2, (min_v + max_v) / 2}};
  double probes[5][3];
  for (int k = 0; k < 5; k++) surface_value(r, h, &f->s, probe_uv[k][0], probe_uv[k][1], probes[k]);
  double face_scale = 0.0;
  for (int i = 0; i < 5; i++)
    for (int j = i + 1; j < 5; j++) face_scale = py_max(face_scale, dist3(probes[i], probes[j]));
  double local = py_max(py_min(scale, face_scale * 4), 1e-9);
  double chord_limit = chord * local, loop_limit = LOOP_TOLERANCE * local;
  if (loop_limit < rough_limit) {
    loop_count = 0;
    for (i32 l = 0; l < f->loop_count; l++) {
      sample_loop(r, M.F, M.T, h, f, pcurves, l, loop_limit, edges, edge_count, &loops[loop_count]);
      if (loops[loop_count].uv.n / 2 >= 3) loop_count++;
    }
  }
  if (!loop_count) fail(r, E_UNRESOLVED);
  out->loops = (ring *)arena_alloc(M.A, sizeof(ring) * (size_t)loop_count);
  for (i64 l = 0; l < loop_count; l++) copy_ring(M.A, &loops[l], &out->loops[l]);
  out->loop_count = loop_count;

  /* A rectangular sphere or cone patch that ends at a pole: its regular angular grid is built to
   * the chord and angle criteria at once and its pole row collapsed, with no refinement after. */
  double u0 = f->uv[0], u1 = f->uv[1], v0 = f->uv[2], v1 = f->uv[3];
  int singular_grid = 0;
  if (loop_count == 1 && (singular_u(r, &f->s, v0) || singular_u(r, &f->s, v1))) {
    singular_grid = 1;
    for (i64 i = 0; i < loops[0].uv.n / 2 && singular_grid; i++) {
      double u = loops[0].uv.v[2 * i], v = loops[0].uv.v[2 * i + 1];
      if (!(u == u0 || u == u1 || v == v0 || v == v1)) singular_grid = 0;
    }
  }
  grid *g = grid_triangulate(r, M.A, M.F, M.T, h, f, loops, loop_count,
                             singular_grid ? chord_limit / 3 : chord_limit,
                             singular_grid ? angle / sqrt(2) : INFINITY, !singular_grid);
  if (!g) fail(r, E_UNRESOLVED);
  if (!g->tri.n) return 0;
  const surface *s = &f->s;
  i64 count = g->uv.n / 2;
  vd uv = g->uv, xyz = {NULL, 0, 0}, nrm = {NULL, 0, 0};
  RESERVE(M.A, xyz, 3 * count);
  RESERVE(M.A, nrm, 3 * count);
  for (i64 i = 0; i < count; i++) {
    double p[3], n[3];
    surface_value(r, h, s, uv.v[2 * i], uv.v[2 * i + 1], p);
    surface_normal(r, h, s, uv.v[2 * i], uv.v[2 * i + 1], f->uv, n);
    PUSH3(M.A, xyz, p[0], p[1], p[2]);
    PUSH3(M.A, nrm, n[0], n[1], n[2]);
  }
  vi tri = g->tri;
  i32 *remap = NULL;
  if (singular_grid) {
    remap = (i32 *)arena_alloc(M.T, sizeof(i32) * (size_t)count);
    vd pole_keys = {NULL, 0, 0};
    vi pole_index = {NULL, 0, 0};
    for (i64 i = 0; i < count; i++) {
      remap[i] = (i32)i;
      double v = uv.v[2 * i + 1];
      if (!singular_u(r, s, v)) continue;
      i64 found = -1;
      for (i64 k = 0; k < pole_keys.n; k++)
        if (pole_keys.v[k] == v) found = k;
      if (found >= 0) remap[i] = pole_index.v[found];
      else {
        PUSH(M.T, pole_keys, v);
        PUSH(M.T, pole_index, (i32)i);
      }
    }
    vi regular = {NULL, 0, 0};
    double tiny = scale * 1e-12;
    for (i64 t = 0; t < tri.n; t += 3) {
      i32 a = remap[tri.v[t]], b = remap[tri.v[t + 1]], c = remap[tri.v[t + 2]];
      if (dist3(xyz.v + 3 * a, xyz.v + 3 * b) <= tiny || dist3(xyz.v + 3 * b, xyz.v + 3 * c) <= tiny ||
          dist3(xyz.v + 3 * c, xyz.v + 3 * a) <= tiny)
        continue;
      PUSH3(M.A, regular, a, b, c);
    }
    tri = regular;
  }
  /* Boundary vertices pinned onto their shared edges before refinement reads them, so a stretch
   * along a fixed edge is known and never split: the face across it holds exactly those knots. */
  pin_boundary(r, M.A, M.T, h, g, &uv, &xyz, &out->boundary, edges, edge_count);
  i64 grid_count = count;
  double angle_cos = cos(angle);
  /* Each edge's verdict, made once, and the surface's point at its middle that it read: the
   * point a split of the edge makes, and for an edge it fails, which this round splits, the
   * normal there too, while the angle's sin and cos are at hand. */
  typedef struct {
    double mid[3], nrm[3];
    i64 bad;
  } verdict;
  emap judged;
  emap_init(&judged, M.F, tri.n);
  VEC(verdict) verdicts = {NULL, 0, 0};
  RESERVE(M.F, verdicts, tri.n);
  vb fresh = {NULL, 0, 0};
  RESERVE(M.F, fresh, tri.n / 3);
  for (i64 t = 0; t < tri.n / 3; t++) fresh.v[t] = 1;
  fresh.n = tri.n / 3;
  vu marks = {NULL, 0, 0};
  for (int depth = 0; depth < (singular_grid ? 0 : MAX_REFINE_DEPTH); depth++) {
    arena_mark mark = arena_save(M.T);
    marks.v = NULL;
    marks.n = marks.cap = 0;
    for (i64 t = 0; t < tri.n / 3; t++) {
      if (!fresh.v[t]) continue;
      i32 corners[3] = {tri.v[3 * t], tri.v[3 * t + 1], tri.v[3 * t + 2]};
      int any_edge = 0;
      for (int k = 0; k < 3; k++) {
        i32 p = corners[k], q = corners[(k + 1) % 3];
        u64 key = edge_key(p, q);
        int made;
        i64 *slot = emap_ref(&judged, key, verdicts.n, &made);
        i64 at = *slot;
        if (made) {
          verdict v;
          surface_value(r, h, s, (uv.v[2 * p] + uv.v[2 * q]) / 2, (uv.v[2 * p + 1] + uv.v[2 * q + 1]) / 2, v.mid);
          const double *xp = xyz.v + 3 * p, *xq = xyz.v + 3 * q;
          double dx = v.mid[0] - (xp[0] + xq[0]) / 2, dy = v.mid[1] - (xp[1] + xq[1]) / 2,
                 dz = v.mid[2] - (xp[2] + xq[2]) / 2;
          const double *np_ = nrm.v + 3 * p, *nq = nrm.v + 3 * q;
          v.bad = (sqrt(dx * dx + dy * dy + dz * dz) > chord_limit ||
                   np_[0] * nq[0] + np_[1] * nq[1] + np_[2] * nq[2] < angle_cos) &&
                  !on_fixed_edge(&out->boundary, edges, edge_count, p, q);
          if (v.bad)
            surface_normal(r, h, s, (uv.v[2 * p] + uv.v[2 * q]) / 2, (uv.v[2 * p + 1] + uv.v[2 * q + 1]) / 2, f->uv,
                           v.nrm);
          PUSH(M.F, verdicts, v);
        }
        if (verdicts.v[at].bad) {
          PUSH(M.T, marks, key);
          any_edge = 1;
        }
      }
      if (!any_edge) {
        u64 key;
        if (triangle_mark(r, h, s, corners[0], corners[1], corners[2], uv.v, xyz.v, nrm.v, chord_limit, angle_cos,
                          &out->boundary, edges, edge_count, &key))
          PUSH(M.T, marks, key);
      }
    }
    if (!marks.n) {
      arena_reset(M.T, mark);
      break;
    }
    sort_u64(M.T, marks.v, marks.n);
    i64 unique = 0;
    for (i64 k = 0; k < marks.n; k++)
      if (!unique || marks.v[k] != marks.v[unique - 1]) marks.v[unique++] = marks.v[k];
    vi split_tri;
    vb split_fresh;
    vu midpoints = {NULL, 0, 0};
    split_marked(M.A, M.T, &tri, marks.v, unique, count, &split_tri, &split_fresh, &midpoints);
    tri = split_tri;
    fresh.n = 0;
    RESERVE(M.F, fresh, split_fresh.n);
    memcpy(fresh.v, split_fresh.v, (size_t)split_fresh.n);
    fresh.n = split_fresh.n;
    RESERVE(M.A, uv, uv.n + 2 * midpoints.n);
    RESERVE(M.A, xyz, xyz.n + 3 * midpoints.n);
    RESERVE(M.A, nrm, nrm.n + 3 * midpoints.n);
    for (i64 k = 0; k < midpoints.n; k++) {
      i64 p = (i64)(midpoints.v[k] >> 32), q = (i64)(midpoints.v[k] & 0xFFFFFFFFu);
      double mu = (uv.v[2 * p] + uv.v[2 * q]) / 2, mv = (uv.v[2 * p + 1] + uv.v[2 * q + 1]) / 2;
      double made[3], turned[3];
      /* The point the edge's verdict read there: (p + q) / 2 is (q + p) / 2, to the bit. */
      i64 at = emap_get(&judged, midpoints.v[k], -1);
      if (at >= 0) memcpy(made, verdicts.v[at].mid, sizeof made);
      else surface_value(r, h, s, mu, mv, made);
      if (at >= 0 && verdicts.v[at].bad) memcpy(turned, verdicts.v[at].nrm, sizeof turned);
      else surface_normal(r, h, s, mu, mv, f->uv, turned);
      PUSH2(M.A, uv, mu, mv);
      PUSH3(M.A, xyz, made[0], made[1], made[2]);
      PUSH3(M.A, nrm, turned[0], turned[1], turned[2]);
    }
    count = uv.n / 2;
    arena_reset(M.T, mark);
  }
  /* Every boundary vertex pinned onto its shared edge polyline, the ones refinement made too. */
  pin_boundary(r, M.A, M.T, h, g, &uv, &xyz, &out->boundary, edges, edge_count);
  if (remap) {
    /* The pole's one vertex lies on every edge that met the pole: it takes the labels of the
     * vertices folded into it, which no triangle has any more. */
    for (i64 i = 0; i < grid_count; i++) {
      if (remap[i] == i) continue;
      const labels *from = bmap_get(&out->boundary, i);
      if (!from) continue;
      labels moved = *from;
      if (!bmap_get(&out->boundary, remap[i])) {
        labels none = {NULL, 0, 0};
        bmap_set(M.A, &out->boundary, remap[i], none);
      }
      labels *into = bmap_get(&out->boundary, remap[i]);
      for (i64 q = 0; q < moved.n; q++) {
        int present = 0;
        for (i64 j = 0; j < into->n && !present; j++)
          if (into->v[j].ord == moved.v[q].ord && into->v[j].f == moved.v[q].f) present = 1;
        if (!present) PUSH(M.A, *into, moved.v[q]);
      }
      bmap_pop(&out->boundary, i);
    }
  }
  out->source = SOURCE_LIBRARY;
  out->uv = uv;
  out->xyz = xyz;
  out->nrm = nrm;
  out->tri = tri;
  out->singular_grid = singular_grid;
  RESERVE(M.A, out->minted, count);
  memset(out->minted.v, 0, (size_t)count);
  out->minted.n = count;
  return 1;
}
