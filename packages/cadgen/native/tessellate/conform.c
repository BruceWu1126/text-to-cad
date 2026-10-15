/*
 * Part of tessellate.c's unity build: the faces made to meet.
 *
 * Every face's boundary mesh edges are fan-split to the UNION of the boundary fractions all faces
 * hold on each model edge: a vertex on one side of an edge is on the other side too, at the same
 * floats, so no T-junction is left -- between two faces OCCT meshed (whose boundary vertices are
 * its polygons' knots), between two the library meshed, and between one of each, where the
 * library's face adds the points its grid put between the knots and the face OCCT meshed takes
 * them. A planar face the library meshed is triangulated again from its final exact trim points
 * instead. Then each face is welded where two boundary vertices came to one point, refined once
 * more over the fans conformity made, its poles put at their one point, and anything that is one
 * point in float32 dropped.
 */

typedef struct {
  const shared *edges;
  i32 edge_count;
  double merge_tolerance;
  vd *unions;        /* per edge ordinal - 1 */
  uint8_t *has_union;
  uint8_t *ordered;  /* per edge ordinal - 1: its union is `orderly` */
} conformity;

static double fraction_eps(const conformity *c, i32 ord) {
  const shared *sh = (ord > 0 && ord <= c->edge_count && c->edges[ord - 1].present) ? &c->edges[ord - 1] : NULL;
  double spatial = (sh && sh->length != 0.0) ? (c->merge_tolerance * 0.5) / sh->length : 0;
  return py_min(0.25, py_max(1e-9, spatial));
}

static const shared *shared_of(const conformity *c, i32 ord) {
  return (ord > 0 && ord <= c->edge_count && c->edges[ord - 1].present) ? &c->edges[ord - 1] : NULL;
}

/* A closed edge wraps: within eps of 1 is its seam, 0. */
static double canonical_fraction(const conformity *c, i32 ord, double f) {
  double eps = fraction_eps(c, ord);
  if (f <= eps) return 0;
  if (f >= 1 - eps) {
    const shared *sh = shared_of(c, ord);
    return (sh && sh->closed) ? 0 : 1;
  }
  return f;
}

/* Fractions bisection can search: finite, ascending, and on a closed edge within [0, 1). */
static int orderly(const vd *values, int closed) {
  for (i64 k = 0; k < values->n; k++) {
    double x = values->v[k];
    if (!isfinite(x) || (k && x < values->v[k - 1]) || (closed && (x < 0 || x >= 1))) return 0;
  }
  return 1;
}

/* The runs of `values` that can hold a fraction between a and b, ascending: on an open edge
 * exactly those in (a, b); on a closed edge, where (a, b) is an arc read mod 1, a superset the
 * caller's own test narrows. Fractions that are not `orderly` are one run, all of them. */
static int fraction_runs(const vd *values, int ordered, int closed, double a, double b, i64 *lo, i64 *hi) {
  if (!ordered) {
    lo[0] = 0;
    hi[0] = values->n;
    return 1;
  }
  if (!closed) {
    lo[0] = bisect_right_d(values->v, values->n, a);
    hi[0] = bisect_left_d(values->v, values->n, b);
    return lo[0] < hi[0];
  }
  /* The arc's ends move by rounding far less than this before the caller's test reads them. */
  const double margin = 1e-12;
  a -= margin;
  b += margin;
  if (!(b > a)) return 0;
  if (!(b - a < 1)) {
    lo[0] = 0;
    hi[0] = values->n;
    return 1;
  }
  double turns = floor(a);
  a -= turns;
  b -= turns;
  if (b < 1) {
    lo[0] = bisect_left_d(values->v, values->n, a);
    hi[0] = bisect_right_d(values->v, values->n, b);
    return 1;
  }
  lo[0] = 0;
  hi[0] = bisect_right_d(values->v, values->n, b - 1);
  lo[1] = bisect_left_d(values->v, values->n, a);
  hi[1] = values->n;
  return 2;
}

static const vd *union_of(const conformity *c, i32 ord) {
  return (ord > 0 && ord <= c->edge_count && c->has_union[ord - 1]) ? &c->unions[ord - 1] : NULL;
}

static double representative_of(const conformity *c, i32 ord, double f) {
  const vd *u = union_of(c, ord);
  if (!u) return f;
  i64 lo = bisect_left_d(u->v, u->n, f);
  lo = imin(lo, u->n - 1);
  double below = lo > 0 ? u->v[lo - 1] : u->v[lo];
  return fabs(u->v[lo] - f) <= fabs(below - f) ? u->v[lo] : below;
}

/* A planar face's vertex on its trim: its model point and the edges it lies on. */
typedef struct {
  double xyz[3];
  labels l;
} planar_vertex;

typedef struct {
  ring *loops;
  i64 loop_count;
  map index; /* (u, v) -> vertex */
  VEC(planar_vertex) vertices;
} planar;

/* `planar_loops`: a planar face's trim loops through every union point of its edges, in the
 * plane's own coordinates, with each vertex's model point and labels. */
static void planar_loops(run *r, arena *A, const host *h, const conformity *c, const face *f, const raw_face *raw,
                         planar *out) {
  memset(out, 0, sizeof *out);
  map_init(&out->index, A, 2, 64);
  out->loops = (ring *)arena_zalloc(A, sizeof(ring) * (size_t)(raw->loop_count ? raw->loop_count : 1));
  out->loop_count = raw->loop_count;
  const double *origin = f->s.o, *xdir = f->s.x, *ydir = f->s.y;
  vd fractions = {NULL, 0, 0};
  for (i64 l = 0; l < raw->loop_count; l++) {
    const ring *loop = &raw->loops[l];
    ring *points = &out->loops[l];
    for (i64 i = 0; i < loop->uv.n / 2; i++) {
      meta m = loop->metas.v[i];
      const shared *sh = m.has ? shared_of(c, m.ord) : NULL;
      if (!sh) {
        PUSH2(A, points->uv, loop->uv.v[2 * i], loop->uv.v[2 * i + 1]);
        PUSH(A, points->ords, loop->ords.v[i]);
        meta none = {0, 0, 0.0, 0.0};
        PUSH(A, points->metas, none);
        continue;
      }
      double start = representative_of(c, m.ord, canonical_fraction(c, m.ord, m.f0));
      if (sh->closed && start == 0 && m.f0 > 0.5) start = 1;
      double end = representative_of(c, m.ord, canonical_fraction(c, m.ord, m.f1));
      if (sh->closed && end == 0 && m.f1 > 0.5) end = 1;
      double eps = fraction_eps(c, m.ord);
      fractions.n = 0;
      PUSH(A, fractions, start);
      const vd *u = union_of(c, m.ord);
      i64 first_between = fractions.n;
      if (u) {
        double lo = py_min(start, end) + eps, hi = py_max(start, end) - eps;
        for (i64 k = 0; k < u->n; k++)
          if (lo < u->v[k] && u->v[k] < hi) PUSH(A, fractions, u->v[k]);
      }
      if (end < start) {
        for (i64 a = first_between, b = fractions.n - 1; a < b; a++, b--) {
          double swap = fractions.v[a];
          fractions.v[a] = fractions.v[b];
          fractions.v[b] = swap;
        }
      }
      PUSH(A, fractions, end);
      for (i64 j = 0; j < fractions.n; j++) {
        if (j + 1 < fractions.n && fabs(fractions.v[j + 1] - fractions.v[j]) <= eps) continue;
        double fr = canonical_fraction(c, m.ord, fractions.v[j]);
        double xyz[3];
        edge_point_at(r, h, sh, fr, xyz);
        double delta[3] = {xyz[0] - origin[0], xyz[1] - origin[1], xyz[2] - origin[2]};
        double uv[2] = {delta[0] * xdir[0] + delta[1] * xdir[1] + delta[2] * xdir[2],
                        delta[0] * ydir[0] + delta[1] * ydir[1] + delta[2] * ydir[2]};
        if (j + 1 < fractions.n) {
          PUSH2(A, points->uv, uv[0], uv[1]);
          PUSH(A, points->ords, m.ord);
          meta made = {1, m.ord, fractions.v[j], fractions.v[j + 1]};
          PUSH(A, points->metas, made);
        }
        u64 key[2] = {key_bits(uv[0]), key_bits(uv[1])};
        int fresh;
        i64 *slot = map_ref(&out->index, key, -1, &fresh);
        label made_label = {m.ord, fr};
        if (fresh) {
          planar_vertex v;
          memset(&v, 0, sizeof v);
          memcpy(v.xyz, xyz, sizeof xyz);
          *slot = out->vertices.n;
          PUSH(A, out->vertices, v);
        }
        i64 at = map_get(&out->index, key, -1);
        PUSH(A, out->vertices.v[at].l, made_label);
      }
    }
  }
}

/* The shared-edge segments of closed loops that properly cross another of the loops' segments, in
 * the order they are found, each once. */
typedef struct {
  double lo, hi;
  const double *p, *q;
  const meta *m;
  i64 id;
} xsegment;

static int less_xsegment(const void *a, const void *b, void *ctx) {
  (void)ctx;
  return ((const xsegment *)a)->lo < ((const xsegment *)b)->lo;
}

static inline double side_of(const double *a, const double *b, const double *c) {
  return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
}

static void crossing_segments(arena *T, const ring *loops, i64 loop_count, vmeta *out) {
  VEC(xsegment) segments = {NULL, 0, 0};
  for (i64 l = 0; l < loop_count; l++) {
    i64 count = loops[l].uv.n / 2;
    for (i64 i = 0; i < count; i++) {
      const double *p = loops[l].uv.v + 2 * i, *q = loops[l].uv.v + 2 * ((i + 1) % count);
      xsegment s = {py_min(p[0], q[0]), py_max(p[0], q[0]), p, q, &loops[l].metas.v[i], segments.n};
      PUSH(T, segments, s);
    }
  }
  merge_sort(T, segments.v, segments.n, sizeof(xsegment), less_xsegment, NULL);
  uint8_t *seen = (uint8_t *)arena_zalloc(T, (size_t)(segments.n ? segments.n : 1));
  out->n = 0;
  for (i64 i = 0; i < segments.n; i++) {
    const xsegment *s = &segments.v[i];
    for (i64 j = i + 1; j < segments.n && segments.v[j].lo <= s->hi; j++) {
      const xsegment *t = &segments.v[j];
      if (side_of(s->p, s->q, t->p) * side_of(s->p, s->q, t->q) < 0 &&
          side_of(t->p, t->q, s->p) * side_of(t->p, t->q, s->q) < 0) {
        const xsegment *both[2] = {s, t};
        for (int k = 0; k < 2; k++) {
          if (!both[k]->m->has || seen[both[k]->id]) continue;
          seen[both[k]->id] = 1;
          PUSH(T, *out, *both[k]->m);
        }
      }
    }
  }
}

/* Two vertices at one position that are the two images of a periodic face's seam, which must both
 * survive: their parameters a period apart (OCCT's seam nodes land a few ulps short of it, and a
 * vertex the unwrap of a straddling triangle duplicated lands on it), or the face's whole span
 * where the surface's period is unknown. */
static int is_seam_image(const face *f, const vd *uv, i64 i, i64 j) {
  for (int d = 0; d < 2; d++) {
    double delta = fabs(uv->v[2 * i + d] - uv->v[2 * j + d]);
    double period = d ? f->period_v : f->period_u, span = f->uv[2 * d + 1] - f->uv[2 * d];
    if (period > 0.0 && fabs(delta - period) <= period * 1e-6) return 1;
    if (span > 0.0 && delta >= span * (1 - 1e-9)) return 1;
  }
  return 0;
}

/* The face's vertices on one of its poles, each with the pole's point, in the order each was first
 * met. */
static void on_poles(arena *T, const face *f, const raw_face *raw, double tolerance, vl *indices, vd *points) {
  indices->n = 0;
  points->n = 0;
  if (!f->pole_count) return;
  double eps[2];
  for (int d = 0; d < 2; d++)
    eps[d] = py_max(py_max(py_max(fabs(f->uv[d * 2]), fabs(f->uv[d * 2 + 1])), f->uv[d * 2 + 1] - f->uv[d * 2]), 1e-12) *
             ldexp(1.0, -23);
  i64 count = raw->uv.n / 2;
  i64 *slot = NULL;
  for (i32 k = 0; k < f->pole_count; k++) {
    const face_pole *pole = &f->poles[k];
    int axis = pole->axis;
    for (i64 index = 0; index < count; index++) {
      double a = raw->uv.v[2 * index + axis], b = raw->uv.v[2 * index + 1 - axis];
      if (fabs(a - pole->at) <= eps[axis] && pole->lo - eps[1 - axis] <= b && b <= pole->hi + eps[1 - axis] &&
          dist3(raw->xyz.v + 3 * index, pole->point) <= tolerance) {
        if (!slot) {
          slot = (i64 *)arena_alloc(T, sizeof(i64) * (size_t)count);
          for (i64 i = 0; i < count; i++) slot[i] = -1;
        }
        if (slot[index] < 0) {
          slot[index] = indices->n;
          PUSH(T, *indices, index);
          PUSH3(T, *points, pole->point[0], pole->point[1], pole->point[2]);
        } else {
          memcpy(points->v + 3 * slot[index], pole->point, sizeof(double) * 3);
        }
      }
    }
  }
}

/* The weld's remap applied, each triangle two of whose corners are one point dropped -- the same
 * index, or the same exact position -- unless both are `apart`. */
static void compact_collapsed(arena *A, arena *T, vi *tri, const vd *xyz, const i32 *remap, const vl *apart) {
  if (!tri->n) return;
  i64 count = xyz->n / 3;
  uint8_t *held = NULL;
  if (apart && apart->n) {
    held = (uint8_t *)arena_zalloc(T, (size_t)count);
    for (i64 k = 0; k < apart->n; k++) held[apart->v[k]] = 1;
  }
  vi out = {NULL, 0, 0};
  RESERVE(A, out, tri->n);
  for (i64 t = 0; t < tri->n; t += 3) {
    i32 a = tri->v[t], b = tri->v[t + 1], c = tri->v[t + 2];
    if (remap) {
      a = remap[a];
      b = remap[b];
      c = remap[c];
    }
    const double *pa = xyz->v + 3 * a, *pb = xyz->v + 3 * b, *pc = xyz->v + 3 * c;
    int same_ab = pa[0] == pb[0] && pa[1] == pb[1] && pa[2] == pb[2];
    int same_bc = pb[0] == pc[0] && pb[1] == pc[1] && pb[2] == pc[2];
    int same_ca = pc[0] == pa[0] && pc[1] == pa[1] && pc[2] == pa[2];
    if (held) {
      same_ab = same_ab && !(held[a] && held[b]);
      same_bc = same_bc && !(held[b] && held[c]);
      same_ca = same_ca && !(held[c] && held[a]);
    }
    if (a == b || b == c || c == a || same_ab || same_bc || same_ca) continue;
    PUSH3(A, out, a, b, c);
  }
  *tri = out;
}

/* A face's boundary vertices at one exact position are one vertex, a seam's two images apart. A
 * triangle two of whose corners are vertices on a pole is kept, though they are one point: it
 * carries the face's boundary to conformity. */
static void weld_by_position(arena *A, arena *T, const face *f, raw_face *raw, double tolerance) {
  i64 count = raw->uv.n / 2;
  i32 *remap = (i32 *)arena_alloc(T, sizeof(i32) * (size_t)(count ? count : 1));
  for (i64 i = 0; i < count; i++) remap[i] = (i32)i;
  vl remapped = {NULL, 0, 0}; /* in the order the weld found them */
  multimap by_position;
  multimap_init(&by_position, T, 3, raw->boundary.entries.n);
  for (i64 k = 0; k < raw->boundary.entries.n; k++) {
    if (!raw->boundary.entries.v[k].alive) continue;
    i32 vertex = raw->boundary.entries.v[k].vertex;
    const double *p = raw->xyz.v + 3 * vertex;
    u64 key[3] = {key_bits(p[0]), key_bits(p[1]), key_bits(p[2])};
    i64 list = multimap_find(&by_position, key);
    if (list < 0) {
      multimap_add(&by_position, key, vertex);
      continue;
    }
    vl *bucket = &by_position.lists.v[list];
    i64 target = -1;
    for (i64 j = 0; j < bucket->n; j++) {
      if (!is_seam_image(f, &raw->uv, bucket->v[j], vertex)) {
        target = bucket->v[j];
        break;
      }
    }
    if (target >= 0) {
      remap[vertex] = (i32)target;
      PUSH(T, remapped, vertex);
    } else {
      PUSH(T, *bucket, vertex);
    }
  }
  vl pole_indices = {NULL, 0, 0};
  vd pole_points = {NULL, 0, 0};
  on_poles(T, f, raw, tolerance, &pole_indices, &pole_points);
  compact_collapsed(A, T, &raw->tri, &raw->xyz, remapped.n ? remap : NULL, &pole_indices);
  /* A welded vertex is the point it folds into: the survivor inherits every model edge it lay on. */
  for (i64 k = 0; k < remapped.n; k++) {
    i64 vertex = remapped.v[k], target = remap[vertex];
    labels *l = bmap_get(&raw->boundary, vertex), *into = bmap_get(&raw->boundary, target);
    if (l && into) {
      for (i64 i = 0; i < l->n; i++) {
        int present = 0;
        for (i64 j = 0; j < into->n && !present; j++)
          if (into->v[j].ord == l->v[i].ord && into->v[j].f == l->v[i].f) present = 1;
        if (!present) PUSH(A, *into, l->v[i]);
      }
    }
    bmap_pop(&raw->boundary, vertex);
  }
}

static int less_label(const void *a, const void *b, void *ctx) {
  (void)ctx;
  const label *x = (const label *)a, *y = (const label *)b;
  if (x->ord != y->ord) return x->ord < y->ord;
  return x->f < y->f;
}

typedef struct {
  double chord_param, f;
} insert;

static int less_insert(const void *a, const void *b, void *ctx) {
  (void)ctx;
  return ((const insert *)a)->chord_param < ((const insert *)b)->chord_param;
}

/* The fan split of one face: its state while its triangles are emitted. */
typedef struct {
  run *r;
  arena *A, *T;
  const host *h;
  const conformity *c;
  const face *f;
  raw_face *raw;
  emap edge_use;    /* (p, q) -> triangles using it */
  map minted_ids;   /* (ord, f to 12 places, p, q) -> vertex */
  emap own_index;   /* ord -> list */
  VEC(vd) own;      /* each sorted: only ever asked whether it holds a fraction in a range */
  uint8_t *own_ordered;
  vi out;
  VEC(insert) between;
} fan;

static int holds_vertex_between(const fan *F, i32 ord, double fp, double fq) {
  i64 list = emap_get(&F->own_index, (u64)ord, -1);
  if (list < 0) return 0;
  const vd *own = &F->own.v[list];
  double eps = fraction_eps(F->c, ord);
  const shared *sh = shared_of(F->c, ord);
  i64 lo_k[2], hi_k[2];
  if (sh && sh->closed) {
    double forward = py_mod1(fq - fp + 1);
    double start = forward <= 0.5 ? fp : fq;
    double arc = forward <= 0.5 ? forward : py_mod1(fp - fq + 1);
    int runs = fraction_runs(own, F->own_ordered[list], 1, start + eps, start + arc - eps, lo_k, hi_k);
    for (int w = 0; w < runs; w++)
      for (i64 k = lo_k[w]; k < hi_k[w]; k++) {
        double d = py_mod1(own->v[k] - start + 1);
        if (eps < d && d < arc - eps) return 1;
      }
    return 0;
  }
  double lo = py_min(fp, fq) + eps, hi = py_max(fp, fq) - eps;
  int runs = fraction_runs(own, F->own_ordered[list], 0, lo, hi, lo_k, hi_k);
  for (int w = 0; w < runs; w++)
    for (i64 k = lo_k[w]; k < hi_k[w]; k++)
      if (lo < own->v[k] && own->v[k] < hi) return 1;
  return 0;
}

/* `inserts_for`: the union points to put along mesh edge (p, q) -- an edge one triangle uses,
 * both of whose ends lie on one model edge -- as (chord parameter, fraction), or 0. */
static int inserts_for(fan *F, i32 p, i32 q, i32 *ord_out) {
  labels *lp_all = bmap_get(&F->raw->boundary, p), *lq_all = bmap_get(&F->raw->boundary, q);
  if (!lp_all || !lq_all) return 0;
  if (emap_get(&F->edge_use, edge_key(p, q), 0) != 1) return 0;
  /* The model edges both ends lie on: each of p's labels with q's first on the same edge. */
  label bp = {0, 0}, bq = {0, 0};
  i64 commons = 0;
  int chosen = 0;
  for (i64 i = 0; i < lp_all->n; i++) {
    const label *lp = &lp_all->v[i], *lq = NULL;
    for (i64 j = 0; j < lq_all->n && !lq; j++)
      if (lq_all->v[j].ord == lp->ord) lq = &lq_all->v[j];
    if (!lq) continue;
    if (!commons) {
      bp = *lp;
      bq = *lq;
    }
    commons++;
  }
  if (!commons) return 0;
  /* A face bounded by two model edges (an arc and its chord) has both corners on both: the mesh
   * edge runs along the one this face has no point between them on, else the first. */
  if (commons > 1) {
    for (i64 i = 0; i < lp_all->n && !chosen; i++) {
      const label *lp = &lp_all->v[i], *lq = NULL;
      for (i64 j = 0; j < lq_all->n && !lq; j++)
        if (lq_all->v[j].ord == lp->ord) lq = &lq_all->v[j];
      if (!lq) continue;
      if (!holds_vertex_between(F, lp->ord, lp->f, lq->f)) {
        bp = *lp;
        bq = *lq;
        chosen = 1;
      }
    }
  }
  const vd *u = union_of(F->c, bp.ord);
  if (!u) return 0;
  const shared *sh = shared_of(F->c, bp.ord);
  double eps = fraction_eps(F->c, bp.ord);
  int ordered = F->c->ordered[bp.ord - 1];
  i64 lo_k[2], hi_k[2];
  F->between.n = 0;
  if (sh && sh->closed) {
    /* Fractions wrap on a closed edge: the SHORT arc from p to q. */
    double forward = py_mod1(bq.f - bp.f + 1);
    int use_forward = forward <= 0.5;
    double start = use_forward ? bp.f : bq.f;
    double arc = use_forward ? forward : py_mod1(bp.f - bq.f + 1);
    if (arc <= eps * 2) return 0;
    int runs = fraction_runs(u, ordered, 1, start + eps, start + arc - eps, lo_k, hi_k);
    for (int w = 0; w < runs; w++)
      for (i64 k = lo_k[w]; k < hi_k[w]; k++) {
        double d = py_mod1(u->v[k] - start + 1);
        if (eps < d && d < arc - eps) {
          insert in = {use_forward ? d / arc : 1 - d / arc, u->v[k]};
          PUSH(F->T, F->between, in);
        }
      }
  } else {
    double lo = py_min(bp.f, bq.f), hi = py_max(bp.f, bq.f);
    if (hi - lo <= eps * 2) return 0;
    int runs = fraction_runs(u, ordered, 0, lo + eps, hi - eps, lo_k, hi_k);
    for (int w = 0; w < runs; w++)
      for (i64 k = lo_k[w]; k < hi_k[w]; k++) {
        double fr = u->v[k];
        if (lo + eps < fr && fr < hi - eps) {
          insert in = {(fr - bp.f) / (bq.f - bp.f), fr};
          PUSH(F->T, F->between, in);
        }
      }
  }
  if (!F->between.n) return 0;
  merge_sort(F->T, F->between.v, F->between.n, sizeof(insert), less_insert, NULL);
  *ord_out = bp.ord;
  return 1;
}

static i32 vertex_at(fan *F, i32 ord, double fr, i32 a, i32 b, double w) {
  decimal12 key12 = decimal12_of(fr);
  u64 key[4] = {(u64)(u32)ord | ((u64)(u32)key12.negative << 32), (u64)key12.units, (u64)(i64)(a < b ? a : b),
                (u64)(i64)(a < b ? b : a)};
  i64 found = map_get(&F->minted_ids, key, -1);
  if (found >= 0) return (i32)found;
  raw_face *raw = F->raw;
  const surface *s = &F->f->s;
  /* Between a's and b's parameters, a corner on a pole taken on b's meridian (`sample_params`). */
  i32 ends[2] = {a, b};
  double ab[4];
  sample_params(F->f, raw->uv.v, ends, 2, ab, NULL);
  double uv[2] = {ab[0] + w * (ab[2] - ab[0]), ab[1] + w * (ab[3] - ab[1])};
  found = raw->uv.n / 2;
  PUSH2(F->A, raw->uv, uv[0], uv[1]);
  double xyz[3], n[3];
  edge_point_at(F->r, F->h, shared_of(F->c, ord), fr, xyz);
  surface_normal(F->r, F->h, s, uv[0], uv[1], F->f->uv, n);
  PUSH3(F->A, raw->xyz, xyz[0], xyz[1], xyz[2]);
  PUSH3(F->A, raw->nrm, n[0], n[1], n[2]);
  labels l = {NULL, 0, 0};
  label made = {ord, fr};
  PUSH(F->A, l, made);
  bmap_set(F->A, &raw->boundary, found, l);
  PUSH(F->A, raw->minted, 1);
  raw->minted_count++;
  map_put(&F->minted_ids, key, found);
  return (i32)found;
}

static void emit(fan *F, i32 a, i32 b, i32 c, int depth) {
  if (depth > 24) {
    PUSH3(F->A, F->out, a, b, c);
    return;
  }
  i32 rotations[3][3] = {{a, b, c}, {b, c, a}, {c, a, b}};
  for (int k = 0; k < 3; k++) {
    i32 p = rotations[k][0], q = rotations[k][1], r = rotations[k][2];
    i32 ord;
    if (!inserts_for(F, p, q, &ord)) continue;
    /* The inserts are this call's: copy them before recursing reuses the buffer. */
    i64 count = F->between.n;
    insert *between = (insert *)arena_alloc(F->T, sizeof(insert) * (size_t)count);
    memcpy(between, F->between.v, sizeof(insert) * (size_t)count);
    i32 previous = p;
    for (i64 i = 0; i < count; i++) {
      i32 m = vertex_at(F, ord, between[i].f, p, q, between[i].chord_param);
      emit(F, previous, m, r, depth + 1);
      previous = m;
    }
    emit(F, previous, q, r, depth + 1);
    return;
  }
  PUSH3(F->A, F->out, a, b, c);
}

static int off_boundary(void *ctx, i32 p, i32 q) {
  const bmap *boundary = (const bmap *)ctx;
  return !(bmap_has(boundary, p) && bmap_has(boundary, q));
}

/* One more refinement over the fans conformity made, never splitting a boundary edge (its vertices
 * are conformity's). */
static void refine_post_conform(run *r, arena *A, arena *T, const host *h, const face *f, raw_face *raw, double chord) {
  if (raw->singular_grid || !raw->minted_count) return;
  i64 count = raw->xyz.n / 3;
  const double *origin = raw->xyz.v;
  double scale = 0.0;
  for (i64 i = 0; i < count; i++) scale = py_max(scale, dist3(raw->xyz.v + 3 * i, origin));
  double chord_limit = chord * py_max(scale, 1e-9);
  const surface *s = &f->s;
  vb fresh = {NULL, 0, 0};
  RESERVE(T, fresh, raw->tri.n / 3);
  for (i64 t = 0; t < raw->tri.n / 3; t++) fresh.v[t] = 1;
  fresh.n = raw->tri.n / 3;
  for (int depth = 0; depth < 3; depth++) {
    vu marked = {NULL, 0, 0};
    emap seen;
    emap_init(&seen, T, 64);
    for (i64 t = 0; t < raw->tri.n / 3; t++) {
      if (!fresh.v[t]) continue;
      i32 corners[3] = {raw->tri.v[3 * t], raw->tri.v[3 * t + 1], raw->tri.v[3 * t + 2]};
      /* Only the fans conformity made: the rest was refined by the main pass. */
      int touched = 0;
      for (int k = 0; k < 3; k++)
        if (corners[k] < raw->minted.n && raw->minted.v[corners[k]]) touched = 1;
      if (!touched) continue;
      for (int k = 0; k < 3; k++) {
        i32 p = corners[k], q = corners[(k + 1) % 3];
        if (bmap_has(&raw->boundary, p) && bmap_has(&raw->boundary, q)) continue;
        i32 ends[2] = {p, q};
        double pq[4];
        sample_params(f, raw->uv.v, ends, 2, pq, NULL);
        double mu = (pq[0] + pq[2]) / 2, mv = (pq[1] + pq[3]) / 2;
        double sm[3];
        surface_value(r, h, s, mu, mv, sm);
        if (!isfinite(sm[0])) continue;
        const double *xp = raw->xyz.v + 3 * p, *xq = raw->xyz.v + 3 * q;
        double dx = sm[0] - (xp[0] + xq[0]) / 2, dy = sm[1] - (xp[1] + xq[1]) / 2, dz = sm[2] - (xp[2] + xq[2]) / 2;
        if (sqrt(dx * dx + dy * dy + dz * dz) > chord_limit) {
          u64 key = edge_key(p, q);
          int made;
          emap_ref(&seen, key, 1, &made);
          if (made) PUSH(T, marked, key);
        }
      }
    }
    mark_pole_twins(T, f, raw->uv.v, &raw->tri, &marked, off_boundary, &raw->boundary);
    if (!marked.n) break;
    sort_u64(T, marked.v, marked.n);
    vi split_tri;
    vb split_fresh;
    vu midpoints = {NULL, 0, 0};
    split_marked(A, T, &raw->tri, marked.v, marked.n, raw->uv.n / 2, &split_tri, &split_fresh, &midpoints);
    raw->tri = split_tri;
    fresh = split_fresh;
    for (i64 k = 0; k < midpoints.n; k++) {
      i32 ends[2] = {(i32)(midpoints.v[k] >> 32), (i32)(midpoints.v[k] & 0xFFFFFFFFu)};
      double pq[4];
      sample_params(f, raw->uv.v, ends, 2, pq, NULL);
      double mu = (pq[0] + pq[2]) / 2, mv = (pq[1] + pq[3]) / 2;
      double made[3], turned[3];
      surface_value(r, h, s, mu, mv, made);
      surface_normal(r, h, s, mu, mv, f->uv, turned);
      PUSH2(A, raw->uv, mu, mv);
      PUSH3(A, raw->xyz, made[0], made[1], made[2]);
      PUSH3(A, raw->nrm, turned[0], turned[1], turned[2]);
      PUSH(A, raw->minted, 0);
    }
  }
}

/* A triangle two of whose corners are one float32 point dropped. */
static void drop_float32_collapsed(arena *A, vi *tri, const vd *xyz) {
  vi out = {NULL, 0, 0};
  RESERVE(A, out, tri->n);
  for (i64 t = 0; t < tri->n; t += 3) {
    i32 a = tri->v[t], b = tri->v[t + 1], c = tri->v[t + 2];
    float pa[3], pb[3], pc[3];
    for (int d = 0; d < 3; d++) {
      pa[d] = (float)xyz->v[3 * a + d];
      pb[d] = (float)xyz->v[3 * b + d];
      pc[d] = (float)xyz->v[3 * c + d];
    }
    int same_ab = pa[0] == pb[0] && pa[1] == pb[1] && pa[2] == pb[2];
    int same_bc = pb[0] == pc[0] && pb[1] == pc[1] && pb[2] == pc[2];
    int same_ca = pc[0] == pa[0] && pc[1] == pa[1] && pc[2] == pa[2];
    if (a == b || b == c || c == a || same_ab || same_bc || same_ca) continue;
    PUSH3(A, out, a, b, c);
  }
  *tri = out;
}

/* Every face's boundary made to meet its neighbours', as the file's header tells. */
static void conform_boundaries(run *r, arenas M, const host *h, const face *faces, raw_face **raws, i64 count,
                               const shared *edges, i32 edge_count, double merge_tolerance) {
  conformity c = {edges, edge_count, merge_tolerance, NULL, NULL, NULL};
  c.unions = (vd *)arena_zalloc(M.A, sizeof(vd) * (size_t)(edge_count ? edge_count : 1));
  c.has_union = (uint8_t *)arena_zalloc(M.A, (size_t)(edge_count ? edge_count : 1));
  for (i64 k = 0; k < count; k++) {
    if (!raws[k]) continue;
    bmap *b = &raws[k]->boundary;
    for (i64 e = 0; e < b->entries.n; e++) {
      if (!b->entries.v[e].alive) continue;
      labels *l = &b->entries.v[e].l;
      for (i64 i = 0; i < l->n; i++) {
        i32 ord = l->v[i].ord;
        if (ord <= 0 || ord > edge_count) fail(r, E_ERROR);
        c.has_union[ord - 1] = 1;
        PUSH(M.A, c.unions[ord - 1], canonical_fraction(&c, ord, l->v[i].f));
      }
    }
  }
  for (i32 ord = 1; ord <= edge_count; ord++) {
    if (!c.has_union[ord - 1]) continue;
    vd *values = &c.unions[ord - 1];
    double eps = fraction_eps(&c, ord);
    merge_sort(M.T, values->v, values->n, sizeof(double), less_double, NULL);
    i64 unique = 0;
    for (i64 i = 0; i < values->n; i++)
      if (!unique || values->v[i] - values->v[unique - 1] > eps) values->v[unique++] = values->v[i];
    values->n = unique;
    const shared *sh = shared_of(&c, ord);
    if (sh && sh->closed && values->n > 1 && 1 - values->v[values->n - 1] <= eps) values->n--;
  }

  /* A planar face the library meshed has loops that must not cross themselves: each crossing
   * segment is split at its middle, on the exact curve, until none crosses, the new points joining
   * the union first. */
  for (i64 k = 0; k < count; k++) {
    if (!raws[k] || raws[k]->source != SOURCE_LIBRARY || faces[k].s.kind != S_PLANE) continue;
    for (int round = 0; round < MAX_REFINE_DEPTH; round++) {
      arena_mark mark = arena_save(M.T);
      planar p;
      planar_loops(r, M.T, h, &c, &faces[k], raws[k], &p);
      vmeta crossing = {NULL, 0, 0};
      crossing_segments(M.T, p.loops, p.loop_count, &crossing);
      int split = 0;
      for (i64 i = 0; i < crossing.n; i++) {
        meta m = crossing.v[i];
        if (m.ord <= 0 || m.ord > edge_count || !c.has_union[m.ord - 1]) continue;
        vd *u = &c.unions[m.ord - 1];
        double lo = py_min(m.f0, m.f1), hi = py_max(m.f0, m.f1);
        if (hi - lo <= fraction_eps(&c, m.ord) * 4) continue;
        double mid = (lo + hi) / 2;
        i64 at = 0;
        while (at < u->n && u->v[at] < mid) at++;
        RESERVE(M.A, *u, u->n + 1);
        memmove(u->v + at + 1, u->v + at, sizeof(double) * (size_t)(u->n - at));
        u->v[at] = mid;
        u->n++;
        split = 1;
      }
      arena_reset(M.T, mark);
      if (!split) break;
    }
  }
  c.ordered = (uint8_t *)arena_zalloc(M.A, (size_t)(edge_count ? edge_count : 1));
  for (i32 ord = 1; ord <= edge_count; ord++) {
    const shared *sh = shared_of(&c, ord);
    if (c.has_union[ord - 1]) c.ordered[ord - 1] = (uint8_t)orderly(&c.unions[ord - 1], sh && sh->closed);
  }

  for (i64 k = 0; k < count; k++) {
    raw_face *raw = raws[k];
    if (!raw) continue;
    const face *f = &faces[k];
    if (f->s.kind == S_PLANE && raw->source == SOURCE_LIBRARY) {
      /* A new point on a curved trim can cross an old triangle's diagonal: the planar region the
       * library meshed is triangulated again from its final exact trim points. */
      planar p;
      planar_loops(r, M.A, h, &c, f, raw, &p);
      arena_mark mark = arena_save(M.F);
      grid *built = grid_triangulate(r, M.A, M.F, M.T, h, f, p.loops, p.loop_count, INFINITY, INFINITY, 1);
      if (built && built->tri.n) {
        raw->uv = built->uv;
        raw->tri = built->tri;
        raw->loops = p.loops;
        raw->loop_count = p.loop_count;
        bmap_clear(&raw->boundary);
        i64 vertices = built->uv.n / 2;
        vd xyz = {NULL, 0, 0}, nrm = {NULL, 0, 0};
        RESERVE(M.A, xyz, 3 * vertices);
        RESERVE(M.A, nrm, 3 * vertices);
        for (i64 i = 0; i < vertices; i++) {
          double u = built->uv.v[2 * i], v = built->uv.v[2 * i + 1];
          u64 key[2] = {key_bits(u), key_bits(v)};
          i64 at = map_get(&p.index, key, -1);
          if (at >= 0) {
            bmap_set(M.A, &raw->boundary, i, p.vertices.v[at].l);
            PUSH3(M.A, xyz, p.vertices.v[at].xyz[0], p.vertices.v[at].xyz[1], p.vertices.v[at].xyz[2]);
          } else {
            double point[3];
            surface_value(r, h, &f->s, u, v, point);
            PUSH3(M.A, xyz, point[0], point[1], point[2]);
          }
        }
        for (i64 i = 0; i < vertices; i++) {
          double n[3];
          surface_normal(r, h, &f->s, built->uv.v[2 * i], built->uv.v[2 * i + 1], f->uv, n);
          PUSH3(M.A, nrm, n[0], n[1], n[2]);
        }
        raw->xyz = xyz;
        raw->nrm = nrm;
        raw->minted.n = 0;
        RESERVE(M.A, raw->minted, vertices);
        memset(raw->minted.v, 0, (size_t)vertices);
        raw->minted.n = vertices;
        raw->minted_count = 0;
      }
      arena_reset(M.F, mark);
    }
    for (i64 e = 0; e < raw->boundary.entries.n; e++) {
      bentry *entry = &raw->boundary.entries.v[e];
      if (!entry->alive) continue;
      for (i64 i = 0; i < entry->l.n; i++)
        entry->l.v[i].f = representative_of(&c, entry->l.v[i].ord, canonical_fraction(&c, entry->l.v[i].ord, entry->l.v[i].f));
      /* The lowest (ord, f) label places the vertex, so two vertices at one model point take one
       * position whichever edge pinned each. */
      merge_sort(M.T, entry->l.v, entry->l.n, sizeof(label), less_label, NULL);
      const shared *sh = shared_of(&c, entry->l.v[0].ord);
      if (sh) edge_point_at(r, h, sh, entry->l.v[0].f, raw->xyz.v + 3 * entry->vertex);
    }
    /* Snapping can land two boundary vertices on one point: welded, collapsed triangles dropped. */
    weld_by_position(M.A, M.T, f, raw, merge_tolerance);
  }

  for (i64 k = 0; k < count; k++) {
    raw_face *raw = raws[k];
    if (!raw || !raw->boundary.entries.n) continue;
    int any = 0;
    for (i64 e = 0; e < raw->boundary.entries.n && !any; e++)
      if (raw->boundary.entries.v[e].alive) any = 1;
    if (!any) continue;
    arena_mark mark = arena_save(M.T);
    fan F;
    memset(&F, 0, sizeof F);
    F.r = r;
    F.A = M.A;
    F.T = M.T;
    F.h = h;
    F.c = &c;
    F.f = &faces[k];
    F.raw = raw;
    /* Only a region-boundary mesh edge (one triangle uses it) may be split. */
    emap_init(&F.edge_use, M.T, raw->tri.n);
    for (i64 t = 0; t < raw->tri.n; t += 3) {
      i32 corners[3] = {raw->tri.v[t], raw->tri.v[t + 1], raw->tri.v[t + 2]};
      for (int j = 0; j < 3; j++) *emap_ref(&F.edge_use, edge_key(corners[j], corners[(j + 1) % 3]), 0, NULL) += 1;
    }
    map_init(&F.minted_ids, M.T, 4, 64);
    emap_init(&F.own_index, M.T, 64);
    vi own_ords = {NULL, 0, 0};
    for (i64 e = 0; e < raw->boundary.entries.n; e++) {
      bentry *entry = &raw->boundary.entries.v[e];
      if (!entry->alive) continue;
      for (i64 i = 0; i < entry->l.n; i++) {
        int made;
        u64 key = (u64)entry->l.v[i].ord;
        i64 *slot = emap_ref(&F.own_index, key, -1, &made);
        if (made) {
          vd empty = {NULL, 0, 0};
          *slot = F.own.n;
          PUSH(M.T, F.own, empty);
          PUSH(M.T, own_ords, entry->l.v[i].ord);
        }
        PUSH(M.T, F.own.v[emap_get(&F.own_index, key, -1)], entry->l.v[i].f);
      }
    }
    F.own_ordered = (uint8_t *)arena_zalloc(M.T, (size_t)(F.own.n ? F.own.n : 1));
    for (i64 e = 0; e < F.own.n; e++) {
      vd *list = &F.own.v[e];
      const shared *sh = shared_of(&c, own_ords.v[e]);
      merge_sort(M.T, list->v, list->n, sizeof(double), less_double, NULL);
      F.own_ordered[e] = (uint8_t)orderly(list, sh && sh->closed);
    }
    vi triangles = raw->tri;
    RESERVE(M.A, F.out, triangles.n);
    for (i64 t = 0; t < triangles.n; t += 3) {
      i32 a = triangles.v[t], b = triangles.v[t + 1], cc = triangles.v[t + 2];
      /* Only an edge with both ends on the boundary is ever split. */
      if (bmap_has(&raw->boundary, a) + bmap_has(&raw->boundary, b) + bmap_has(&raw->boundary, cc) >= 2)
        emit(&F, a, b, cc, 0);
      else
        PUSH3(M.A, F.out, a, b, cc);
    }
    raw->tri = F.out;
    arena_reset(M.T, mark);
    /* Splitting can mint a vertex where a boundary vertex already is: a second weld. */
    weld_by_position(M.A, M.T, &faces[k], raw, merge_tolerance);
  }
}
