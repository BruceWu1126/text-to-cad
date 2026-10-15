/*
 * Part of tessellate.c's unity build: trims and model edges, sampled.
 *
 * Each model edge has ONE polyline every face beside it conforms to, and it is the display edge:
 * OCCT's discretization of the edge where OCCT meshed a face beside it (`fixed_shared_edge`: the
 * polygon on triangulation the host read, taken as it is, a point between two of its knots lying
 * on the chord between them), else the exact curve sampled here (`sample_shared_edge`). A face
 * the library meshes samples its trim loops from their pcurves (`sample_loop`); a stretch along a
 * fixed edge takes that edge's knots verbatim, in this face's parameters as the host evaluated
 * them, and a stretch along a sampled edge is taken from the polyline by arc fraction where the
 * mapping holds, so the faces either side start from the same points.
 */

#define DEFAULT_CHORD 1.5e-3
#define LOOP_TOLERANCE 5e-4
#define MAX_REFINE_DEPTH 7
#define MIN_LOOP_SEGMENTS 8
#define SCALE_FLOOR 1e-6

/* A loop segment along a shared edge polyline: its edge and its ends' fractions. */
typedef struct {
  i32 has; /* 0: the segment runs along no shared edge */
  i32 ord;
  double f0, f1;
} meta;

typedef VEC(meta) vmeta;

/* A sampled loop: points (u, v); segment i runs from point i to i + 1 (the last back to the
 * first), along model edge ords[i], with metas[i]. */
typedef struct {
  vd uv;
  vi ords;
  vmeta metas;
} ring;

typedef struct {
  i32 axis;
  double at, lo, hi;
  double point[3];
} face_pole;

typedef struct {
  i32 ord, reversed;
  surface s;
  double uv[4];
  double period_u, period_v; /* the surface's periods, 0 where it has none */
  /* TODO (next agent): int singular_side[4], whether the box's side u0, u1, v0, v1 is one point
   * of the surface (singular_u at v0/v1; a B-spline's degenerate column at u0/u1), set in
   * mesh_rest after read_geometry, for spread_pole_corners -- see the WIP commit message. */
  i32 loop_count;
  const i32 *loop_start;  /* each loop's first pcurve, into the run's pcurves */
  const i32 *loop_size;
  i32 pole_count;
  face_pole *poles;
} face;

/* One model edge's polyline: the one its faces conform to, and the display edge. */
typedef struct {
  const curve *c;
  i32 present;
  i32 fixed;     /* OCCT's discretization, taken as it is: a point between knots lies on their chord */
  vd params;
  vd points;     /* 3 per point */
  vd fractions;
  i32 closed;
  double length;
  vi boundary_subset;
  i32 corner_start, corner_end; /* the canonical corner each end is (`weld_corners`), or -1 */
} shared;

/* THE position of arc fraction `fraction` on a shared edge: every face computes boundary
 * positions through this one function, so a point is the same floats on both sides of its edge.
 * Between polyline knots it evaluates the exact curve at an interpolated parameter -- or, on a
 * fixed polyline, interpolates the chord, which is where the faces OCCT meshed along it are. */
static void edge_point_at(run *r, const host *h, const shared *sh, double fraction, double *out) {
  const double *fractions = sh->fractions.v;
  i64 last = sh->fractions.n - 1;
  if (fraction <= fractions[0]) {
    memcpy(out, sh->points.v, sizeof(double) * 3);
    return;
  }
  if (fraction >= fractions[last]) {
    memcpy(out, sh->points.v + 3 * last, sizeof(double) * 3);
    return;
  }
  i64 lo = 0, hi = last;
  while (lo + 1 < hi) {
    i64 mid = (lo + hi) >> 1;
    if (fractions[mid] <= fraction) lo = mid;
    else hi = mid;
  }
  double f0 = fractions[lo], f1 = fractions[lo + 1];
  if (fraction == f0) {
    memcpy(out, sh->points.v + 3 * lo, sizeof(double) * 3);
    return;
  }
  if (fraction == f1) {
    memcpy(out, sh->points.v + 3 * (lo + 1), sizeof(double) * 3);
    return;
  }
  double w = f1 > f0 ? (fraction - f0) / (f1 - f0) : 0;
  if (sh->fixed) {
    const double *a = sh->points.v + 3 * lo, *b = a + 3;
    for (int d = 0; d < 3; d++) out[d] = a[d] + w * (b[d] - a[d]);
    return;
  }
  double t = sh->params.v[lo] + w * (sh->params.v[lo + 1] - sh->params.v[lo]);
  curve_value(r, h, sh->c, t, out);
}

/* The arc fraction of the point on a polyline nearest `point`, and how far the point is from it. */
static double edge_fraction_of(const shared *sh, const double *point, double *distance) {
  i64 n = sh->points.n / 3;
  double best = INFINITY, fraction = 0.0;
  for (i64 i = 0; i + 1 < n; i++) {
    const double *a = sh->points.v + 3 * i, *b = a + 3;
    double abx = b[0] - a[0], aby = b[1] - a[1], abz = b[2] - a[2];
    double len2 = abx * abx + aby * aby + abz * abz;
    double t = len2 > 0 ? ((point[0] - a[0]) * abx + (point[1] - a[1]) * aby + (point[2] - a[2]) * abz) / len2 : 0.0;
    t = t < 0 ? 0 : t > 1 ? 1 : t;
    double q[3] = {a[0] + t * abx, a[1] + t * aby, a[2] + t * abz};
    double d = dist3(q, point);
    if (d < best) {
      best = d;
      fraction = sh->fractions.v[i] + t * (sh->fractions.v[i + 1] - sh->fractions.v[i]);
    }
  }
  if (n == 1) {
    best = dist3(sh->points.v, point);
    fraction = 0.0;
  }
  if (distance) *distance = best;
  return fraction;
}

/* The adaptive refinement both samplings make: intervals split at their middle where the 3D
 * midpoint strays past `tolerance` from the chord, up to MAX_REFINE_DEPTH rounds. `at` evaluates a
 * parameter: `width` numbers, its point first (a trim adds its (u, v)). An interval that passed a
 * round passes every round after (its ends and middle do not change), so only the ones a split
 * made are looked at again. A middle equal to an end is that end, as a cache of points by
 * parameter has it; `evaluated` keeps the parameter each entry was evaluated at, which a caller
 * compares to the parameter's own bits before reusing what was evaluated. */
typedef void (*point_at_fn)(run *r, void *ctx, double t, double *out);

typedef struct {
  vd params, data, evaluated;
  int width;
} samples;

static void sample_push(arena *S, samples *s, double t, const double *data, double evaluated) {
  PUSH(S, s->params, t);
  RESERVE(S, s->data, s->data.n + s->width);
  memcpy(s->data.v + s->data.n, data, sizeof(double) * (size_t)s->width);
  s->data.n += s->width;
  PUSH(S, s->evaluated, evaluated);
}

static void refine_params(run *r, arena *S, samples *s, double tolerance, point_at_fn at, void *ctx) {
  int w = s->width;
  vb settled = {NULL, 0, 0};
  RESERVE(S, settled, s->params.n);
  for (i64 i = 0; i + 1 < s->params.n; i++) settled.v[settled.n++] = 0;
  for (int depth = 0; depth < MAX_REFINE_DEPTH; depth++) {
    samples next = {{NULL, 0, 0}, {NULL, 0, 0}, {NULL, 0, 0}, w};
    vb next_settled = {NULL, 0, 0};
    int split = 0;
    i64 n = s->params.n;
    RESERVE(S, next.params, 2 * n);
    RESERVE(S, next.data, 2 * n * w);
    RESERVE(S, next.evaluated, 2 * n);
    RESERVE(S, next_settled, 2 * n);
    sample_push(S, &next, s->params.v[0], s->data.v, s->evaluated.v[0]);
    for (i64 i = 0; i + 1 < n; i++) {
      double a = s->params.v[i], b = s->params.v[i + 1];
      const double *pa = s->data.v + w * i, *pb = s->data.v + w * (i + 1);
      int keep = settled.v[i];
      if (!keep) {
        double mid = (a + b) / 2, pm[8], evaluated = mid;
        if (mid == a) {
          memcpy(pm, pa, sizeof(double) * (size_t)w);
          evaluated = s->evaluated.v[i];
        } else if (mid == b) {
          memcpy(pm, pb, sizeof(double) * (size_t)w);
          evaluated = s->evaluated.v[i + 1];
        } else {
          at(r, ctx, mid, pm);
        }
        double dx = pm[0] - (pa[0] + pb[0]) / 2;
        double dy = pm[1] - (pa[1] + pb[1]) / 2;
        double dz = pm[2] - (pa[2] + pb[2]) / 2;
        if (sqrt(dx * dx + dy * dy + dz * dz) > tolerance) {
          sample_push(S, &next, mid, pm, evaluated);
          next_settled.v[next_settled.n++] = 0;
          next_settled.v[next_settled.n++] = 0;
          split = 1;
        } else {
          keep = 1;
        }
      }
      if (keep) next_settled.v[next_settled.n++] = 1;
      sample_push(S, &next, b, pb, s->evaluated.v[i + 1]);
    }
    *s = next;
    settled = next_settled;
    if (!split) break;
  }
}

static inline int same_bits(double a, double b) { return memcmp(&a, &b, sizeof a) == 0; }

typedef struct {
  const host *h;
  const surface *s;
  const pcurve *pc;
} pcurve_ctx;

/* A trim's point on its surface, then its (u, v). */
static void pcurve_point_at(run *r, void *ctx, double t, double *out) {
  pcurve_ctx *c = (pcurve_ctx *)ctx;
  pcurve_value(r, c->h, c->pc, t, out + 3);
  surface_value(r, c->h, c->s, out[3], out[4], out);
}

/* The trim's parameters, its points (u, v) at them, and -- for a caller that measures along the
 * trim -- the surface's point at each (u, v). */
static void sample_pcurve(run *r, arena *S, const host *h, const face *f, const pcurve *pc, double tolerance,
                          vd *params, vd *uvs, vd *points) {
  pcurve_ctx ctx = {h, &f->s, pc};
  double t0 = pc->first, t1 = pc->last;
  double start[5], end[5];
  pcurve_point_at(r, &ctx, t0, start);
  double end_at = t1;
  if (t1 == t0) {
    memcpy(end, start, sizeof end);
    end_at = t0;
  } else {
    pcurve_point_at(r, &ctx, t1, end);
  }
  int closed = dist3(start, end) <= tolerance;
  i64 initial = imax(closed ? MIN_LOOP_SEGMENTS : 2, pc->n >= 0 ? pc->n : 2);
  samples s = {{NULL, 0, 0}, {NULL, 0, 0}, {NULL, 0, 0}, 5};
  RESERVE(S, s.params, initial + 1);
  RESERVE(S, s.data, 5 * (initial + 1));
  RESERVE(S, s.evaluated, initial + 1);
  for (i64 i = 0; i <= initial; i++) {
    double t = t0 + ((t1 - t0) * (double)i) / (double)initial;
    double p[5];
    if (t == t0) sample_push(S, &s, t, start, t0);
    else if (t == t1) sample_push(S, &s, t, end, end_at);
    else {
      pcurve_point_at(r, &ctx, t, p);
      sample_push(S, &s, t, p, t);
    }
  }
  refine_params(r, S, &s, tolerance, pcurve_point_at, &ctx);
  i64 n = s.params.n;
  *params = s.params;
  uvs->v = NULL;
  uvs->n = uvs->cap = 0;
  RESERVE(S, *uvs, 2 * n);
  if (points) {
    points->v = NULL;
    points->n = points->cap = 0;
    RESERVE(S, *points, 3 * n);
  }
  for (i64 i = 0; i < n; i++) {
    const double *d = s.data.v + 5 * i;
    double t = s.params.v[i];
    /* What was evaluated at this parameter's own bits stands; a parameter that met its cache
     * under another zero is evaluated again, as each final (u, v) was. */
    if (same_bits(s.evaluated.v[i], t)) {
      PUSH2(S, *uvs, d[3], d[4]);
      if (points) PUSH3(S, *points, d[0], d[1], d[2]);
    } else {
      double uv[2], p[3];
      pcurve_value(r, h, pc, t, uv);
      PUSH2(S, *uvs, uv[0], uv[1]);
      if (points) {
        surface_value(r, h, &f->s, uv[0], uv[1], p);
        PUSH3(S, *points, p[0], p[1], p[2]);
      }
    }
  }
}

/* The shared polyline in this face's parameters, by arc fraction -- (uvs, fractions) in
 * pcurve-forward order, each fraction in the polyline's own direction -- or 0 where the mapping
 * cannot be trusted, with the trim's own sampling (`own_uvs`) to fall back on. */
static int map_shared_edge(run *r, arena *S, const host *h, const face *f, const pcurve *pc, double tolerance,
                           const shared *sh, vd *out_uvs, vd *out_fractions, vd *own_uvs_out) {
  vd own_params, own_uvs, own_points;
  sample_pcurve(r, S, h, f, pc, tolerance, &own_params, &own_uvs, &own_points);
  *own_uvs_out = own_uvs;
  i64 count_own = own_params.n;
  if (count_own < 2) return 0;
  const double *own3 = own_points.v;
  double *cumulative = (double *)arena_alloc(S, sizeof(double) * (size_t)count_own);
  cumulative[0] = 0.0;
  for (i64 i = 1; i < count_own; i++) cumulative[i] = cumulative[i - 1] + dist3(own3 + 3 * i, own3 + 3 * (i - 1));
  double total = cumulative[count_own - 1];
  if (!(total > 0)) return 0;
  for (i64 i = 0; i < count_own; i++) cumulative[i] = cumulative[i] / total;
  /* Does the shared polyline run with or against the pcurve? */
  i64 last_shared = sh->points.n / 3 - 1;
  const double *shared_start = sh->points.v, *shared_end = sh->points.v + 3 * last_shared;
  double d_same = dist3(shared_start, own3) + dist3(shared_end, own3 + 3 * (count_own - 1));
  double d_flip = dist3(shared_start, own3 + 3 * (count_own - 1)) + dist3(shared_end, own3);
  int flip = d_flip < d_same;

  i64 count = sh->boundary_subset.n;
  vd uvs = {NULL, 0, 0}, fractions = {NULL, 0, 0}, params = {NULL, 0, 0}, points3 = {NULL, 0, 0};
  RESERVE(S, uvs, 2 * count);
  RESERVE(S, fractions, count);
  RESERVE(S, params, count);
  RESERVE(S, points3, 3 * count);
  i64 cursor = 0;
  for (i64 step = 0; step < count; step++) {
    i32 shared_index = sh->boundary_subset.v[flip ? count - 1 - step : step];
    double fr = sh->fractions.v[shared_index];
    double g = flip ? 1 - fr : fr;
    while (cursor + 1 < count_own - 1 && cumulative[cursor + 1] < g) cursor++;
    double t;
    if (step == 0) {
      t = own_params.v[0];
    } else if (step == count - 1) {
      t = own_params.v[count_own - 1];
    } else {
      double c0 = cumulative[cursor], c1 = cumulative[cursor + 1];
      double w = c1 > c0 ? (g - c0) / (c1 - c0) : 0;
      t = own_params.v[cursor] + w * (own_params.v[cursor + 1] - own_params.v[cursor]);
    }
    double uv[2], p[3];
    pcurve_value(r, h, pc, t, uv);
    surface_value(r, h, &f->s, uv[0], uv[1], p);
    if (dist3(p, sh->points.v + 3 * shared_index) > tolerance * 2) return 0;
    PUSH2(S, uvs, uv[0], uv[1]);
    PUSH(S, fractions, fr);
    PUSH(S, params, t);
    PUSH3(S, points3, p[0], p[1], p[2]);
  }
  /* The polyline is sampled to the curve's chord error, but the surface can bulge between its
   * points: densified to the surface's chord error. Planes cannot bulge. An interval that passed
   * a round passes every round after. */
  if (f->s.kind != S_PLANE) {
    vb settled = {NULL, 0, 0};
    RESERVE(S, settled, params.n);
    for (i64 i = 0; i + 1 < params.n; i++) settled.v[settled.n++] = 0;
    for (int depth = 0; depth < 4; depth++) {
      int split = 0;
      i64 n = params.n;
      vd next_uvs = {NULL, 0, 0}, next_fractions = {NULL, 0, 0}, next_params = {NULL, 0, 0}, next_points3 = {NULL, 0, 0};
      vb next_settled = {NULL, 0, 0};
      RESERVE(S, next_uvs, 4 * n);
      RESERVE(S, next_fractions, 2 * n);
      RESERVE(S, next_params, 2 * n);
      RESERVE(S, next_points3, 6 * n);
      RESERVE(S, next_settled, 2 * n);
      PUSH2(S, next_uvs, uvs.v[0], uvs.v[1]);
      PUSH(S, next_fractions, fractions.v[0]);
      PUSH(S, next_params, params.v[0]);
      PUSH3(S, next_points3, points3.v[0], points3.v[1], points3.v[2]);
      for (i64 i = 0; i + 1 < n; i++) {
        int keep = settled.v[i];
        if (!keep) {
          const double *pa = points3.v + 3 * i, *pb = points3.v + 3 * (i + 1);
          double t_mid = (params.v[i] + params.v[i + 1]) / 2;
          double uv_mid[2], pm[3];
          pcurve_value(r, h, pc, t_mid, uv_mid);
          surface_value(r, h, &f->s, uv_mid[0], uv_mid[1], pm);
          double dx = pm[0] - (pa[0] + pb[0]) / 2;
          double dy = pm[1] - (pa[1] + pb[1]) / 2;
          double dz = pm[2] - (pa[2] + pb[2]) / 2;
          if (sqrt(dx * dx + dy * dy + dz * dz) > tolerance) {
            PUSH2(S, next_uvs, uv_mid[0], uv_mid[1]);
            PUSH(S, next_fractions, (fractions.v[i] + fractions.v[i + 1]) / 2);
            PUSH(S, next_params, t_mid);
            PUSH3(S, next_points3, pm[0], pm[1], pm[2]);
            next_settled.v[next_settled.n++] = 0;
            next_settled.v[next_settled.n++] = 0;
            split = 1;
          } else {
            keep = 1;
          }
        }
        if (keep) next_settled.v[next_settled.n++] = 1;
        PUSH2(S, next_uvs, uvs.v[2 * (i + 1)], uvs.v[2 * (i + 1) + 1]);
        PUSH(S, next_fractions, fractions.v[i + 1]);
        PUSH(S, next_params, params.v[i + 1]);
        PUSH3(S, next_points3, points3.v[3 * (i + 1)], points3.v[3 * (i + 1) + 1], points3.v[3 * (i + 1) + 2]);
      }
      uvs = next_uvs;
      fractions = next_fractions;
      params = next_params;
      points3 = next_points3;
      settled = next_settled;
      if (!split) break;
    }
  }
  *out_uvs = uvs;
  *out_fractions = fractions;
  return 1;
}

/* The loop's points; segment i lies on model edge ords[i], and metas[i] names its shared edge and
 * fractions where it runs along one (in that polyline's direction, whichever way the loop runs).
 * `rings` come from arena A: what is sampled outlives the face. */
static void sample_loop(run *r, arena *A, arena *S, const host *h, const face *f, const pcurve *pcurves, i32 loop,
                        double tolerance, const shared *edges, i32 edge_count, ring *out) {
  memset(out, 0, sizeof *out);
  double snaps[2][3];
  int snap_count = 0;
  if (f->s.kind != S_PLANE) {
    for (int d = 0; d < 2; d++) {
      double lo = f->uv[d * 2], hi = f->uv[d * 2 + 1];
      snaps[d][0] = lo;
      snaps[d][1] = hi;
      snaps[d][2] = py_max(py_max(py_max(fabs(lo), fabs(hi)), hi - lo), 1e-12) * ldexp(1.0, -23);
    }
    snap_count = 2;
  }
  i32 first = f->loop_start[loop], size = f->loop_size[loop];
  for (i32 k = 0; k < size; k++) {
    const pcurve *pc = &pcurves[first + k];
    int forward = !pc->reversed;
    const shared *sh = (pc->edge_ord > 0 && pc->edge_ord <= edge_count && edges[pc->edge_ord - 1].present)
                           ? &edges[pc->edge_ord - 1] : NULL;
    vd segment = {NULL, 0, 0}, fractions = {NULL, 0, 0}, own = {NULL, 0, 0};
    int mapped = 0, sampled = 0;
    if (sh && sh->fixed && pc->knot_uv && pc->knot_count == sh->points.n / 3 && pc->knot_count >= 2) {
      /* OCCT's knots, verbatim: the faces OCCT meshed along this edge have exactly these. */
      RESERVE(S, segment, 2 * pc->knot_count);
      RESERVE(S, fractions, pc->knot_count);
      for (i32 i = 0; i < pc->knot_count; i++) {
        PUSH2(S, segment, pc->knot_uv[2 * i], pc->knot_uv[2 * i + 1]);
        PUSH(S, fractions, sh->fractions.v[i]);
      }
      mapped = 1;
    } else if (sh && sh->points.n / 3 >= 2) {
      mapped = map_shared_edge(r, S, h, f, pc, tolerance, sh, &segment, &fractions, &own);
      sampled = 1;
    }
    if (!mapped) {
      /* The trim's own sampling: the one the mapping made, which sampling again would repeat. */
      if (sampled) {
        segment = own;
      } else {
        vd params;
        sample_pcurve(r, S, h, f, pc, tolerance, &params, &segment, NULL);
      }
    }
    i64 n = segment.n / 2;
    /* A bound's representation error snapped: past a sphere's pole or a cone's apex by one ULP is
     * across the singularity. */
    for (i64 i = 0; i < n; i++) {
      for (int d = 0; d < snap_count; d++) {
        double *value = &segment.v[2 * i + d];
        if (fabs(*value - snaps[d][0]) <= snaps[d][2]) *value = snaps[d][0];
        else if (fabs(*value - snaps[d][1]) <= snaps[d][2]) *value = snaps[d][1];
      }
    }
    /* An edge that is one point in this face's parameters adds no segment. */
    int one_point = 1;
    for (i64 i = 0; i < n && one_point; i++)
      if (!(segment.v[2 * i] == segment.v[0] && segment.v[2 * i + 1] == segment.v[1])) one_point = 0;
    if (one_point) continue;
    if (!forward) {
      for (i64 i = 0, j = n - 1; i < j; i++, j--) {
        double u = segment.v[2 * i], v = segment.v[2 * i + 1];
        segment.v[2 * i] = segment.v[2 * j];
        segment.v[2 * i + 1] = segment.v[2 * j + 1];
        segment.v[2 * j] = u;
        segment.v[2 * j + 1] = v;
      }
      if (mapped) {
        for (i64 i = 0, j = fractions.n - 1; i < j; i++, j--) {
          double swap = fractions.v[i];
          fractions.v[i] = fractions.v[j];
          fractions.v[j] = swap;
        }
      }
    }
    /* Each segment's last point is the next pcurve's first. */
    i32 ord = pc->edge_ord > 0 ? pc->edge_ord : 0;
    for (i64 i = 0; i + 1 < n; i++) {
      PUSH2(A, out->uv, segment.v[2 * i], segment.v[2 * i + 1]);
      PUSH(A, out->ords, ord);
      meta m = {0, 0, 0.0, 0.0};
      if (mapped) {
        m.has = 1;
        m.ord = pc->edge_ord;
        m.f0 = fractions.v[i];
        m.f1 = fractions.v[i + 1];
      }
      PUSH(A, out->metas, m);
    }
  }
}

typedef struct {
  const host *h;
  const curve *c;
} curve_ctx;

static void curve_point_at(run *r, void *ctx, double t, double *out) {
  curve_ctx *c = (curve_ctx *)ctx;
  curve_value(r, c->h, c->c, t, out);
}

/* One model edge's polyline as OCCT discretized it: `count` points (3 numbers each) at the edge
 * parameters `params`, in the edge's own order. Its fractions are its cumulative chord length,
 * every point is a boundary point, and it is closed where its ends are one point. */
static void fixed_shared_edge(arena *A, const curve *c, const double *points, const double *params, i64 count,
                              shared *out) {
  memset(out, 0, sizeof *out);
  out->c = c;
  out->present = 1;
  out->fixed = 1;
  out->corner_start = out->corner_end = -1;
  RESERVE(A, out->params, count);
  RESERVE(A, out->points, 3 * count);
  RESERVE(A, out->fractions, count);
  RESERVE(A, out->boundary_subset, count);
  for (i64 i = 0; i < count; i++) {
    PUSH(A, out->params, params[i]);
    PUSH3(A, out->points, points[3 * i], points[3 * i + 1], points[3 * i + 2]);
    PUSH(A, out->boundary_subset, (i32)i);
  }
  PUSH(A, out->fractions, 0.0);
  for (i64 i = 1; i < count; i++)
    PUSH(A, out->fractions, out->fractions.v[i - 1] + dist3(out->points.v + 3 * i, out->points.v + 3 * (i - 1)));
  double total = out->fractions.v[count - 1];
  if (total > 0) {
    for (i64 i = 0; i < count; i++) out->fractions.v[i] = out->fractions.v[i] / total;
    out->fractions.v[count - 1] = 1;
  }
  out->length = total;
  /* Closure as `sample_shared_edge` judges it: the ends together against the polyline's extent. */
  double extent = 0.0;
  for (i64 i = 1; i < count; i++) extent = py_max(extent, dist3(out->points.v, out->points.v + 3 * i));
  out->closed = count > 2 && dist3(out->points.v, out->points.v + 3 * (count - 1)) <= extent * ldexp(1.0, -21);
  if (out->closed) memcpy(out->points.v + 3 * (count - 1), out->points.v, sizeof(double) * 3);
}

/* One model edge's exact curve sampled, the polyline every face beside it conforms to, and the
 * display edge. */
static void sample_shared_edge(run *r, arena *A, arena *S, const host *h, const curve *c, double tolerance, shared *out) {
  memset(out, 0, sizeof *out);
  out->c = c;
  out->present = 1;
  out->corner_start = out->corner_end = -1;
  double t0 = c->first, t1 = c->last;
  curve_ctx ctx = {h, c};
  double start[3], end[3];
  curve_value(r, h, c, t0, start);
  curve_value(r, h, c, t1, end);
  int closed = 0;
  if (c->kind != C_LINE) {
    double extent = dist3(start, end);
    static const double probes[3] = {0.25, 0.5, 0.75};
    for (int k = 0; k < 3; k++) {
      double p[3];
      curve_value(r, h, c, t0 + probes[k] * (t1 - t0), p);
      extent = py_max(extent, dist3(start, p));
    }
    /* Closure is a geometric fact, not a matter of the display tolerance. */
    closed = dist3(start, end) <= extent * ldexp(1.0, -21);
  }
  i64 initial = c->kind == C_LINE ? 1 : imax(closed ? 8 : 4, c->n >= 0 ? c->n : 2);
  samples s = {{NULL, 0, 0}, {NULL, 0, 0}, {NULL, 0, 0}, 3};
  for (i64 i = 0; i <= initial; i++) {
    double t = t0 + ((t1 - t0) * (double)i) / (double)initial, p[3];
    if (t == t0) sample_push(S, &s, t, start, t0);
    else if (t == t1) sample_push(S, &s, t, end, t1);
    else {
      curve_value(r, h, c, t, p);
      sample_push(S, &s, t, p, t);
    }
  }
  refine_params(r, S, &s, tolerance, curve_point_at, &ctx);
  i64 n = s.params.n;
  RESERVE(A, out->params, n);
  RESERVE(A, out->points, 3 * n);
  for (i64 i = 0; i < n; i++) {
    double p[3], t = s.params.v[i];
    /* Each point the curve's at its parameter: evaluated there already, unless its cache was
     * met under the other zero. */
    if (same_bits(s.evaluated.v[i], t)) memcpy(p, s.data.v + 3 * i, sizeof p);
    else curve_value(r, h, c, t, p);
    PUSH(A, out->params, t);
    PUSH3(A, out->points, p[0], p[1], p[2]);
  }
  if (closed && n > 1) {
    /* The seam is one point: both ends the start's. */
    memcpy(out->points.v + 3 * (n - 1), out->points.v, sizeof(double) * 3);
  }
  out->closed = closed;
  RESERVE(A, out->fractions, n);
  PUSH(A, out->fractions, 0.0);
  for (i64 i = 1; i < n; i++)
    PUSH(A, out->fractions, out->fractions.v[i - 1] + dist3(out->points.v + 3 * i, out->points.v + 3 * (i - 1)));
  double total = out->fractions.v[n - 1];
  if (total > 0) {
    for (i64 i = 0; i < n; i++) out->fractions.v[i] = out->fractions.v[i] / total;
    out->fractions.v[n - 1] = 1;
  }
  out->length = total;
  /* Face boundaries need only the loop tolerance: the subset keeps the points dropping which
   * would stray past it (ends always kept). */
  PUSH(A, out->boundary_subset, 0);
  double boundary_tolerance = tolerance * (LOOP_TOLERANCE / DEFAULT_CHORD);
  i64 anchor = 0;
  const double *points = out->points.v;
  for (i64 i = 1; i < n; i++) {
    if (i == n - 1) {
      PUSH(A, out->boundary_subset, (i32)i);
      break;
    }
    const double *a = points + 3 * anchor, *b = points + 3 * (i + 1);
    double abx = b[0] - a[0], aby = b[1] - a[1], abz = b[2] - a[2];
    double ab_len2 = abx * abx + aby * aby + abz * abz;
    double worst = 0.0;
    for (i64 k = anchor + 1; k <= i; k++) {
      const double *p = points + 3 * k;
      double apx = p[0] - a[0], apy = p[1] - a[1], apz = p[2] - a[2];
      double t = ab_len2 > 0 ? (apx * abx + apy * aby + apz * abz) / ab_len2 : 0;
      double clamped = py_max(0, py_min(1, t));
      double qx = a[0] + clamped * abx, qy = a[1] + clamped * aby, qz = a[2] + clamped * abz;
      double dx = p[0] - qx, dy = p[1] - qy, dz = p[2] - qz;
      worst = py_max(worst, sqrt(dx * dx + dy * dy + dz * dz));
      if (worst > boundary_tolerance) break;
    }
    if (worst > boundary_tolerance) {
      PUSH(A, out->boundary_subset, (i32)i);
      anchor = i;
    }
  }
}
