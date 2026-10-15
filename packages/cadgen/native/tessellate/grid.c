/*
 * Part of tessellate.c's unity build: a face's base triangulation in its parameters.
 *
 * A grid sized by the surface's curvature covers the face's trim loops: a cell no trim crosses
 * is two triangles or nothing (its centre says which), a cell a trim crosses is clipped to it and
 * earcut (cut again from the loops where a hole crossing the cell's side defeats the clip). Then
 * earcut's slivers are flipped away and edges along a line of constant u or v are split at the
 * vertices lying inside them.
 */

/* A polygon's signed area. */
static double polygon_area(const double *p, i64 count) {
  double area = 0.0;
  for (i64 i = 0; i < count; i++) {
    i64 j = (i + 1) % count;
    area += p[2 * i] * p[2 * j + 1] - p[2 * j] * p[2 * i + 1];
  }
  return area / 2;
}

/* Whether (u, v) lies inside the loops by the even-odd rule. */
static int point_in_loops(const poly *loops, i64 count, double u, double v) {
  int inside = 0;
  for (i64 l = 0; l < count; l++) {
    const double *p = loops[l].p;
    i64 n = loops[l].n;
    for (i64 i = 0; i < n; i++) {
      i64 j = (i + 1) % n;
      double x0 = p[2 * i], y0 = p[2 * i + 1], x1 = p[2 * j], y1 = p[2 * j + 1];
      if ((y0 > v) != (y1 > v) && u < ((x1 - x0) * (v - y0)) / (y1 - y0) + x0) inside = !inside;
    }
  }
  return inside;
}

static int point_in_poly(const poly *ring, double u, double v) { return point_in_loops(ring, 1, u, v); }

/* Sutherland-Hodgman against the axis-aligned cell; a piece of fewer than three points is none. */
static poly clip_to_cell(arena *S, poly points, double cu0, double cu1, double cv0, double cv1) {
  poly output = points;
  const double bounds[4] = {cu0, cu1, cv0, cv1};
  const int axes[4] = {0, 0, 1, 1}, below[4] = {0, 1, 0, 1};
  for (int pass = 0; pass < 4; pass++) {
    poly source = output;
    i64 count = source.n;
    poly empty = {NULL, 0};
    if (count == 0) return empty;
    output.p = (double *)arena_alloc(S, sizeof(double) * (size_t)(4 * count + 2));
    output.n = 0;
    int axis = axes[pass], keep_below = below[pass];
    double bound = bounds[pass];
    const double *previous = source.p + 2 * (count - 1);
    int previous_in = keep_below ? previous[axis] <= bound : previous[axis] >= bound;
    for (i64 i = 0; i < count; i++) {
      const double *current = source.p + 2 * i;
      int current_in = keep_below ? current[axis] <= bound : current[axis] >= bound;
      if (current_in != previous_in) {
        double t = (bound - previous[axis]) / (current[axis] - previous[axis]);
        output.p[2 * output.n] = previous[0] + t * (current[0] - previous[0]);
        output.p[2 * output.n + 1] = previous[1] + t * (current[1] - previous[1]);
        output.n++;
      }
      if (current_in) {
        output.p[2 * output.n] = current[0];
        output.p[2 * output.n + 1] = current[1];
        output.n++;
      }
      previous = current;
      previous_in = current_in;
    }
    if (output.n < 3) return empty;
  }
  return output;
}

/* The area earcut's triangles over `points` cover. */
static double covered(const double *points, const vi *triangles) {
  double area = 0.0;
  for (i64 t = 0; t + 2 < triangles->n; t += 3) {
    const double *a = points + 2 * triangles->v[t], *b = points + 2 * triangles->v[t + 1],
                 *c = points + 2 * triangles->v[t + 2];
    area += fabs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1]));
  }
  return area / 2;
}

/* The polygon without a point repeating the one before it (the last against the first). */
static poly deduplicated(arena *S, poly points) {
  if (points.n <= 1) return points;
  poly kept = {(double *)arena_alloc(S, sizeof(double) * (size_t)(2 * points.n)), 0};
  for (i64 i = 0; i < points.n; i++) {
    i64 before = i ? i - 1 : points.n - 1;
    if (points.p[2 * i] == points.p[2 * before] && points.p[2 * i + 1] == points.p[2 * before + 1]) continue;
    kept.p[2 * kept.n] = points.p[2 * i];
    kept.p[2 * kept.n + 1] = points.p[2 * i + 1];
    kept.n++;
  }
  if (!kept.n) {
    kept.p[0] = points.p[0];
    kept.p[1] = points.p[1];
    kept.n = 1;
  }
  return kept;
}

/* Python's x % m for a float x and a positive m. */
static inline double py_mod(double x, double m) {
  double mod = fmod(x, m);
  if (mod != 0.0) {
    if (mod < 0) mod += m;
  } else {
    mod = 0.0;
  }
  return mod;
}

static inline i64 py_imod(i64 x, i64 m) {
  i64 mod = x % m;
  return mod < 0 ? mod + m : mod;
}

/* A region of a cell: an outline and its holes. */
typedef struct {
  poly outline;
  VEC(poly) holes;
} region;

typedef VEC(region) regions;

typedef struct {
  double place;
  i32 run, which;
} cell_end;

static int less_end(const void *a, const void *b, void *ctx) {
  (void)ctx;
  const cell_end *x = (const cell_end *)a, *y = (const cell_end *)b;
  if (x->place != y->place) return x->place < y->place;
  if (x->run != y->run) return x->run < y->run;
  return x->which < y->which;
}

typedef struct {
  poly ring;
  i64 depth;
  i64 order;
} nested;

static int less_nested(const void *a, const void *b, void *ctx) {
  (void)ctx;
  return ((const nested *)a)->depth < ((const nested *)b)->depth;
}

/* The part of the face inside the cell as regions, or 0 where the loops cannot be followed through
 * it. Each loop is cut at the cell's sides into the runs inside it, and the runs are joined end to
 * end by the stretches of the sides that lie inside the face, so a hole crossing a side is a bite
 * out of the outline. Loops wholly inside the cell nest by the even-odd rule. */
static int cell_regions(run *r, arena *S, const poly *loops, i64 loop_count, double cu0, double cu1, double cv0,
                        double cv1, regions *out) {
  double width = cu1 - cu0, height = cv1 - cv0;
  out->v = NULL;
  out->n = out->cap = 0;
  if (!(width > 0) || !(height > 0)) return 0;
  VEC(vd) runs = {NULL, 0, 0};
  VEC(poly) rings = {NULL, 0, 0};
  for (i64 l = 0; l < loop_count; l++) {
    const double *points = loops[l].p;
    i64 count = loops[l].n;
    i64 start = -1;
    for (i64 i = 0; i < count; i++) {
      double x = points[2 * i], y = points[2 * i + 1];
      if (!(cu0 < x && x < cu1 && cv0 < y && y < cv1)) {
        start = i;
        break;
      }
    }
    if (start < 0) {
      poly copy = {(double *)arena_alloc(S, sizeof(double) * (size_t)(2 * (count ? count : 1))), count};
      memcpy(copy.p, points, sizeof(double) * (size_t)(2 * count));
      PUSH(S, rings, copy);
      continue;
    }
    double *t0s = (double *)arena_alloc(S, sizeof(double) * (size_t)(2 * count));
    uint8_t *some = (uint8_t *)arena_alloc(S, (size_t)count);
    for (i64 k = 0; k < count; k++) {
      const double *p = points + 2 * ((start + k) % count), *q = points + 2 * ((start + k + 1) % count);
      double dx = q[0] - p[0], dy = q[1] - p[1];
      double t0 = 0.0, t1 = 1.0;
      double pks[4] = {-dx, dx, -dy, dy}, qks[4] = {p[0] - cu0, cu1 - p[0], p[1] - cv0, cv1 - p[1]};
      for (int e = 0; e < 4; e++) {
        double pk = pks[e], qk = qks[e];
        if (pk == 0) {
          if (qk <= 0) {
            t0 = 1.0;
            t1 = 0.0;
            break;
          }
        } else if (pk < 0) {
          t0 = py_max(t0, qk / pk);
        } else {
          t1 = py_min(t1, qk / pk);
        }
      }
      some[k] = t1 > t0;
      t0s[2 * k] = t0;
      t0s[2 * k + 1] = t1;
    }
    vd current = {NULL, 0, 0};
    int open = 0;
    for (i64 k = 0; k < count; k++) {
      const double *p = points + 2 * ((start + k) % count), *q = points + 2 * ((start + k + 1) % count);
      if (!some[k]) {
        if (open) {
          PUSH(S, runs, current);
          open = 0;
        }
        continue;
      }
      double t0 = t0s[2 * k], t1 = t0s[2 * k + 1];
      double enter[2], leave[2];
      if (t0 > 0) {
        enter[0] = p[0] + t0 * (q[0] - p[0]);
        enter[1] = p[1] + t0 * (q[1] - p[1]);
      } else {
        enter[0] = p[0];
        enter[1] = p[1];
      }
      if (t1 < 1) {
        leave[0] = p[0] + t1 * (q[0] - p[0]);
        leave[1] = p[1] + t1 * (q[1] - p[1]);
      } else {
        leave[0] = q[0];
        leave[1] = q[1];
      }
      if (!open) {
        current.v = NULL;
        current.n = current.cap = 0;
        PUSH2(S, current, enter[0], enter[1]);
        open = 1;
      } else if (t0 > 0) {
        /* Out through a vertex on a side and back in at once: two runs. */
        PUSH(S, runs, current);
        current.v = NULL;
        current.n = current.cap = 0;
        PUSH2(S, current, enter[0], enter[1]);
      }
      PUSH2(S, current, leave[0], leave[1]);
      if (t1 < 1) {
        PUSH(S, runs, current);
        open = 0;
      }
    }
    if (open) PUSH(S, runs, current);
  }

  const double corners[4][2] = {{cu0, cv0}, {cu1, cv0}, {cu1, cv1}, {cu0, cv1}};
  VEC(poly) polygons = {NULL, 0, 0};
#define INWARD(s_, out_)                                    \
  do {                                                      \
    double s__ = py_mod((s_), 4);                           \
    double nudge_u = width * 1e-7, nudge_v = height * 1e-7; \
    if (s__ < 1) {                                          \
      (out_)[0] = cu0 + s__ * width;                        \
      (out_)[1] = cv0 + nudge_v;                            \
    } else if (s__ < 2) {                                   \
      (out_)[0] = cu1 - nudge_u;                            \
      (out_)[1] = cv0 + (s__ - 1) * height;                 \
    } else if (s__ < 3) {                                   \
      (out_)[0] = cu1 - (s__ - 2) * width;                  \
      (out_)[1] = cv1 - nudge_v;                            \
    } else {                                                \
      (out_)[0] = cu0 + nudge_u;                            \
      (out_)[1] = cv1 - (s__ - 3) * height;                 \
    }                                                       \
  } while (0)
  if (runs.n) {
    i64 n = 2 * runs.n;
    cell_end *ends = (cell_end *)arena_alloc(S, sizeof(cell_end) * (size_t)n);
    for (i64 rr = 0; rr < runs.n; rr++) {
      vd *run_ = &runs.v[rr];
      for (int which = 0; which < 2; which++) {
        double *point = which ? run_->v + run_->n - 2 : run_->v;
        double u = py_min(py_max(point[0], cu0), cu1), v = py_min(py_max(point[1], cv0), cv1);
        /* The nearest side: min((v - cv0, 0), (cu1 - u, 1), (cv1 - v, 2), (u - cu0, 3)). */
        double best = v - cv0;
        int side = 0;
        double candidates[3] = {cu1 - u, cv1 - v, u - cu0};
        for (int k = 0; k < 3; k++) {
          double value = candidates[k];
          int lt = value == best ? (k + 1) < side : value < best;
          if (lt) {
            best = value;
            side = k + 1;
          }
        }
        double place;
        if (side == 0) {
          place = (u - cu0) / width;
          point[0] = u;
          point[1] = cv0;
        } else if (side == 1) {
          place = 1 + (v - cv0) / height;
          point[0] = cu1;
          point[1] = v;
        } else if (side == 2) {
          place = 2 + (cu1 - u) / width;
          point[0] = u;
          point[1] = cv1;
        } else {
          place = 3 + (cv1 - v) / height;
          point[0] = cu0;
          point[1] = v;
        }
        ends[2 * rr + which].place = place;
        ends[2 * rr + which].run = (i32)rr;
        ends[2 * rr + which].which = which;
      }
    }
    merge_sort(S, ends, n, sizeof(cell_end), less_end, NULL);
    double *length = (double *)arena_alloc(S, sizeof(double) * (size_t)n);
    for (i64 k = 0; k + 1 < n; k++) length[k] = ends[k + 1].place - ends[k].place;
    length[n - 1] = ends[0].place + 4 - ends[n - 1].place;
    int8_t *inside = (int8_t *)arena_alloc(S, (size_t)n);
    int any = 0;
    for (i64 k = 0; k < n; k++) {
      inside[k] = -1;
      if (length[k] > 0) {
        double at[2];
        INWARD(ends[k].place + length[k] / 2, at);
        inside[k] = (int8_t)point_in_loops(loops, loop_count, at[0], at[1]);
        any = 1;
      }
    }
    if (!any) return 0;
    for (int pass = 0; pass < 2; pass++) {
      for (i64 k = 0; k < n; k++) {
        i64 before = k ? k - 1 : n - 1;
        if (inside[k] < 0 && inside[before] >= 0) inside[k] = (int8_t)!inside[before];
      }
    }
    i64 *slot = (i64 *)arena_alloc(S, sizeof(i64) * (size_t)n);
    for (i64 k = 0; k < n; k++) slot[2 * ends[k].run + ends[k].which] = k;
    uint8_t *used = (uint8_t *)arena_zalloc(S, (size_t)runs.n);
    for (i64 first = 0; first < runs.n; first++) {
      if (used[first]) continue;
      vd polygon = {NULL, 0, 0};
      i64 rr = first;
      int forward = 1, closed = 0;
      for (i64 step = 0; step < runs.n + 1; step++) {
        if (used[rr]) return 0;
        used[rr] = 1;
        vd *run_ = &runs.v[rr];
        i64 points = run_->n / 2;
        for (i64 i = 0; i < points; i++) {
          i64 at = forward ? i : points - 1 - i;
          PUSH2(S, polygon, run_->v[2 * at], run_->v[2 * at + 1]);
        }
        i64 k = slot[2 * rr + (forward ? 1 : 0)];
        int ahead = inside[k], behind = inside[k ? k - 1 : n - 1];
        if (ahead == behind) return 0;
        double s_from = ends[k].place;
        i64 following;
        if (ahead) {
          following = (k + 1) % n;
          double reach = length[k];
          i64 c = py_floor(r, s_from) + 1;
          while ((double)c < s_from + reach) {
            const double *corner = corners[py_imod(c, 4)];
            PUSH2(S, polygon, corner[0], corner[1]);
            c += 1;
          }
        } else {
          following = py_imod(k - 1, n);
          double reach = length[k ? k - 1 : n - 1];
          i64 c = py_ceil(r, s_from) - 1;
          while ((double)c > s_from - reach) {
            const double *corner = corners[py_imod(c, 4)];
            PUSH2(S, polygon, corner[0], corner[1]);
            c -= 1;
          }
        }
        rr = ends[following].run;
        forward = ends[following].which == 0;
        if (rr == first) {
          if (!forward) return 0;
          closed = 1;
          break;
        }
      }
      if (!closed) return 0;
      poly made = {polygon.v, polygon.n / 2};
      PUSH(S, polygons, made);
    }
  } else {
    int count = 0;
    static const double places[4] = {0.5, 1.5, 2.5, 3.5};
    for (int k = 0; k < 4; k++) {
      double at[2];
      INWARD(places[k], at);
      count += point_in_loops(loops, loop_count, at[0], at[1]);
    }
    if (count >= 3) {
      poly square = {(double *)arena_alloc(S, sizeof(double) * 8), 4};
      for (int k = 0; k < 4; k++) {
        square.p[2 * k] = corners[k][0];
        square.p[2 * k + 1] = corners[k][1];
      }
      PUSH(S, polygons, square);
    }
  }
#undef INWARD
  /* Loops wholly inside the cell: each lies in as many of the others as its even-odd depth. */
  regions contours = {NULL, 0, 0};
  for (i64 k = 0; k < polygons.n; k++) {
    region made;
    memset(&made, 0, sizeof made);
    made.outline = polygons.v[k];
    PUSH(S, contours, made);
  }
  nested *order = (nested *)arena_alloc(S, sizeof(nested) * (size_t)(rings.n ? rings.n : 1));
  for (i64 i = 0; i < rings.n; i++) {
    double u = rings.v[i].p[0], v = rings.v[i].p[1];
    i64 depth = 0;
    for (i64 k = 0; k < polygons.n; k++) depth += point_in_poly(&polygons.v[k], u, v);
    for (i64 k = 0; k < rings.n; k++)
      if (k != i) depth += point_in_poly(&rings.v[k], u, v);
    order[i].ring = rings.v[i];
    order[i].depth = depth;
    order[i].order = i;
  }
  merge_sort(S, order, rings.n, sizeof(nested), less_nested, NULL);
  for (i64 i = 0; i < rings.n; i++) {
    if (order[i].depth % 2 == 0) {
      region made;
      memset(&made, 0, sizeof made);
      made.outline = order[i].ring;
      PUSH(S, contours, made);
      continue;
    }
    double u = order[i].ring.p[0], v = order[i].ring.p[1];
    i64 holder = -1;
    for (i64 k = contours.n - 1; k >= 0; k--) {
      if (point_in_poly(&contours.v[k].outline, u, v)) {
        holder = k;
        break;
      }
    }
    if (holder < 0) return 0;
    PUSH(S, contours.v[holder].holes, order[i].ring);
  }
  for (i64 k = 0; k < contours.n; k++) {
    contours.v[k].outline = deduplicated(S, contours.v[k].outline);
    for (i64 h = 0; h < contours.v[k].holes.n; h++) contours.v[k].holes.v[h] = deduplicated(S, contours.v[k].holes.v[h]);
  }
  *out = contours;
  return 1;
}

/* A loop segment, bucketed by the cells its box touches. */
typedef struct {
  double x0, y0, x1, y1;
  i32 ord;
  meta m;
} gsegment;

typedef struct {
  vd uv;  /* the vertices (u, v) */
  vi tri; /* the triangles, flat */
  VEC(gsegment) segments;
  vl *by_cell; /* steps_u * steps_v lists of segment indices */
  double min_u, min_v, max_u, max_v, du, dv;
  i64 steps_u, steps_v;
  double snap_u, snap_v;
  map vertex_ids; /* (u, v) as found -> vertex */
} grid;

static inline void cell_of(run *r, const grid *g, double u, double v, i64 *cu, i64 *cv) {
  *cu = imin(g->steps_u - 1, imax(0, py_floor(r, (u - g->min_u) / g->du)));
  *cv = imin(g->steps_v - 1, imax(0, py_floor(r, (v - g->min_v) / g->dv)));
}

static i64 grid_steps_for_direction(run *r, const host *h, const face *f, const double *box, double chord_limit,
                                    int direction) {
  double u0 = box[0], u1 = box[1], v0 = box[2], v1 = box[3];
  double span = direction == 0 ? u1 - u0 : v1 - v0;
  if (span <= 0) return 1;
  /* Enough probe intervals that the curvature of one stretch of the surface (a bell's lip, a
   * spline's bend) is met by one of them rather than averaged over a quarter of the span. */
  const int probes = 16, lines = 8;
  double worst = 0.0;
  for (int line = 0; line <= lines; line++) {
    double across = direction == 0 ? v0 + ((v1 - v0) * line) / lines : u0 + ((u1 - u0) * line) / lines;
    for (int i = 0; i < probes; i++) {
      double t0 = (direction == 0 ? u0 : v0) + (span * i) / probes;
      double t1 = t0 + span / probes;
      double tm = (t0 + t1) / 2;
      double pa[3], pb[3], pm[3];
      if (direction == 0) {
        surface_value(r, h, &f->s, t0, across, pa);
        surface_value(r, h, &f->s, t1, across, pb);
        surface_value(r, h, &f->s, tm, across, pm);
      } else {
        surface_value(r, h, &f->s, across, t0, pa);
        surface_value(r, h, &f->s, across, t1, pb);
        surface_value(r, h, &f->s, across, tm, pm);
      }
      double dx = pm[0] - (pa[0] + pb[0]) / 2;
      double dy = pm[1] - (pa[1] + pb[1]) / 2;
      double dz = pm[2] - (pa[2] + pb[2]) / 2;
      worst = py_max(worst, sqrt(dx * dx + dy * dy + dz * dz));
    }
  }
  if (worst <= chord_limit) return 1;
  /* Chord error grows as the square of a step: the steps each probe interval needs. */
  double per_interval = sqrt(worst / chord_limit);
  return imin(256, imax(1, py_ceil(r, probes * per_interval)));
}

/* Whether each uncrossed cell's centre lies inside the loops. */
static uint8_t *inside_centres(arena *S, const poly *loops, i64 loop_count, const double *grid_us, i64 steps_u,
                               const double *grid_vs, i64 steps_v) {
  i64 segments = 0;
  for (i64 l = 0; l < loop_count; l++) segments += loops[l].n;
  double *x0 = (double *)arena_alloc(S, sizeof(double) * (size_t)(4 * (segments ? segments : 1)));
  double *y0 = x0 + segments, *x1 = y0 + segments, *y1 = x1 + segments;
  i64 at = 0;
  for (i64 l = 0; l < loop_count; l++) {
    const double *p = loops[l].p;
    i64 n = loops[l].n;
    for (i64 i = 0; i < n; i++, at++) {
      i64 j = (i + 1) % n;
      x0[at] = p[2 * i];
      y0[at] = p[2 * i + 1];
      x1[at] = p[2 * j];
      y1[at] = p[2 * j + 1];
    }
  }
  uint8_t *inside = (uint8_t *)arena_zalloc(S, (size_t)(steps_u * steps_v));
  double *centres = (double *)arena_alloc(S, sizeof(double) * (size_t)steps_u);
  for (i64 i = 0; i < steps_u; i++) centres[i] = (grid_us[i] + grid_us[i + 1]) / 2;
  double *crossings = (double *)arena_alloc(S, sizeof(double) * (size_t)(segments ? segments : 1));
  for (i64 j = 0; j < steps_v; j++) {
    double v = (grid_vs[j] + grid_vs[j + 1]) / 2;
    i64 count = 0, nans = 0;
    for (i64 k = 0; k < segments; k++) {
      if ((y0[k] > v) != (y1[k] > v)) {
        double c = ((x1[k] - x0[k]) * (v - y0[k])) / (y1[k] - y0[k]) + x0[k];
        if (c != c) nans++;
        else crossings[count++] = c;
      }
    }
    if (!count && !nans) continue;
    merge_sort(S, crossings, count, sizeof(double), less_double, NULL);
    for (i64 i = 0; i < steps_u; i++) {
      i64 greater = count - bisect_right_d(crossings, count, centres[i]) + nans;
      inside[i * steps_v + j] = (uint8_t)(greater % 2 == 1);
    }
  }
  return inside;
}

/* The grid's vertex at (u, v): a corner within one source ULP of a grid line on a curved face
 * is moved onto it, and two vertices within one source ULP on both axes are one. Vertices go
 * to arena A, the lookup tables to F. */
typedef struct {
  run *r;
  arena *A, *F;
  grid *g;
  int curved;
  double eps_u, eps_v;
  map nearby_index; /* (cu, cv) -> list */
  VEC(vl) nearby;
} vertex_maker;

static i32 vertex_id(vertex_maker *vm, double u, double v) {
  run *r = vm->r;
  grid *g = vm->g;
  i64 iu = round_half_up(r, (u - g->min_u) / g->du);
  i64 iv = round_half_up(r, (v - g->min_v) / g->dv);
  double grid_u = iu == g->steps_u ? g->max_u : g->min_u + (double)iu * g->du;
  double grid_v = iv == g->steps_v ? g->max_v : g->min_v + (double)iv * g->dv;
  if (vm->curved) {
    if (fabs(u - grid_u) <= vm->eps_u) u = grid_u;
    if (fabs(v - grid_v) <= vm->eps_v) v = grid_v;
  }
  u64 key[2] = {key_bits(u), key_bits(v)};
  i64 found = map_get(&g->vertex_ids, key, -1);
  if (found >= 0) return (i32)found;
  i64 cu = py_floor(r, u / vm->eps_u), cv = py_floor(r, v / vm->eps_v);
  if (vm->curved) {
    for (i64 a = -1; a <= 1 && found < 0; a++) {
      for (i64 b = -1; b <= 1 && found < 0; b++) {
        u64 cell[2] = {(u64)(cu + a), (u64)(cv + b)};
        i64 list = map_get(&vm->nearby_index, cell, -1);
        if (list < 0) continue;
        vl *candidates = &vm->nearby.v[list];
        for (i64 k = 0; k < candidates->n; k++) {
          i64 candidate = candidates->v[k];
          double x = g->uv.v[2 * candidate], y = g->uv.v[2 * candidate + 1];
          if (fabs(x - u) <= vm->eps_u && fabs(y - v) <= vm->eps_v) {
            found = candidate;
            break;
          }
        }
      }
    }
  }
  if (found < 0) {
    found = g->uv.n / 2;
    PUSH2(vm->A, g->uv, u, v);
    if (vm->curved) {
      u64 cell[2] = {(u64)cu, (u64)cv};
      int made;
      i64 *slot = map_ref(&vm->nearby_index, cell, -1, &made);
      if (made) {
        vl empty = {NULL, 0, 0};
        *slot = vm->nearby.n;
        PUSH(vm->F, vm->nearby, empty);
      }
      i64 list = map_get(&vm->nearby_index, cell, -1);
      PUSH(vm->F, vm->nearby.v[list], found);
    }
  }
  map_put(&g->vertex_ids, key, found);
  return (i32)found;
}

static void flip_slivers(run *r, arena *T, const host *h, const face *f, const vd *uv, vi *tri, double degenerate_limit,
                         uint8_t *collinear);
static void split_iso_junctions(run *r, arena *A, arena *T, const vd *uv, vi *tri);

/* Copies of a cut's rings for earcut, which may shorten them: the points indexed are the originals. */
static poly copy_poly(arena *T, poly from) {
  poly to = {(double *)arena_alloc(T, sizeof(double) * (size_t)(2 * (from.n ? from.n : 1))), from.n};
  memcpy(to.p, from.p, sizeof(double) * (size_t)(2 * from.n));
  return to;
}

/* One cut of a cell: its points (an outline's, then its holes') and earcut's triangles over them. */
typedef struct {
  poly points;
  vi faces;
} cell_cut;

typedef VEC(cell_cut) cell_cuts;

/* earcut over the rings themselves, which it may shorten (a closing repeat dropped): 0 where it
 * fails, which skips the cell, as there. */
static int try_triangulate(run *r, arena *T, poly *contour, poly *holes, i64 hole_count, vi *out) {
  guard g;
  ENTER(r, g);
  if (setjmp(g.jump) != 0) {
    LEAVE(r, g);
    if (g.code == E_MEMORY) fail(r, E_MEMORY);
    return 0;
  }
  triangulate_shape(r, T, contour, holes, hole_count, out);
  LEAVE(r, g);
  return 1;
}

/* Each region of a cell cut again from its loops earcut, given copies of its rings; 0 where any
 * of them fails, which leaves the first cut. */
static int try_cut_regions(run *r, arena *T, const regions *rebuilt, cell_cuts *out) {
  guard g;
  ENTER(r, g);
  if (setjmp(g.jump) != 0) {
    LEAVE(r, g);
    if (g.code == E_MEMORY) fail(r, E_MEMORY);
    return 0;
  }
  for (i64 k = 0; k < rebuilt->n; k++) {
    const region *rg = &rebuilt->v[k];
    if (rg->outline.n < 3) continue;
    i64 total = rg->outline.n;
    for (i64 hh = 0; hh < rg->holes.n; hh++) total += rg->holes.v[hh].n;
    cell_cut cut;
    cut.points.p = (double *)arena_alloc(T, sizeof(double) * (size_t)(2 * total));
    cut.points.n = total;
    memcpy(cut.points.p, rg->outline.p, sizeof(double) * (size_t)(2 * rg->outline.n));
    i64 at = rg->outline.n;
    for (i64 hh = 0; hh < rg->holes.n; hh++) {
      memcpy(cut.points.p + 2 * at, rg->holes.v[hh].p, sizeof(double) * (size_t)(2 * rg->holes.v[hh].n));
      at += rg->holes.v[hh].n;
    }
    poly outline = copy_poly(T, rg->outline);
    poly *inner = (poly *)arena_alloc(T, sizeof(poly) * (size_t)(rg->holes.n ? rg->holes.n : 1));
    for (i64 hh = 0; hh < rg->holes.n; hh++) inner[hh] = copy_poly(T, rg->holes.v[hh]);
    cut.faces.v = NULL;
    cut.faces.n = cut.faces.cap = 0;
    triangulate_shape(r, T, &outline, inner, rg->holes.n, &cut.faces);
    PUSH(T, *out, cut);
  }
  LEAVE(r, g);
  return 1;
}

/* The face's grid, triangulated: NULL where the loops span nothing in either parameter. The grid's
 * vertices and triangles come from A, its tables from F, each cell's working state from T. */
static grid *grid_triangulate(run *r, arena *A, arena *F, arena *T, const host *h, const face *f, const ring *loops,
                              i64 loop_count, double chord_limit, double angle_limit, int flip) {
  double min_u = INFINITY, min_v = INFINITY, max_u = -INFINITY, max_v = -INFINITY;
  poly *views = (poly *)arena_alloc(F, sizeof(poly) * (size_t)(loop_count ? loop_count : 1));
  for (i64 l = 0; l < loop_count; l++) {
    views[l].p = loops[l].uv.v;
    views[l].n = loops[l].uv.n / 2;
    for (i64 i = 0; i < views[l].n; i++) {
      double u = views[l].p[2 * i], v = views[l].p[2 * i + 1];
      if (u < min_u) min_u = u;
      if (u > max_u) max_u = u;
      if (v < min_v) min_v = v;
      if (v > max_v) max_v = v;
    }
  }
  if (!(max_u > min_u) || !(max_v > min_v)) return NULL;
  double box[4] = {min_u, max_u, min_v, max_v};
  i64 steps_u = imin(256, imax(grid_steps_for_direction(r, h, f, box, chord_limit, 0),
                               py_ceil(r, (max_u - min_u) / angle_limit)));
  i64 steps_v = imin(256, imax(grid_steps_for_direction(r, h, f, box, chord_limit, 1),
                               f->s.kind == S_SPHERE ? py_ceil(r, (max_v - min_v) / angle_limit) : 1));
  grid *g = (grid *)arena_zalloc(A, sizeof(grid));
  g->min_u = min_u;
  g->min_v = min_v;
  g->max_u = max_u;
  g->max_v = max_v;
  g->steps_u = steps_u;
  g->steps_v = steps_v;
  g->du = (max_u - min_u) / (double)steps_u;
  g->dv = (max_v - min_v) / (double)steps_v;
  if (g->du == 0.0 || g->dv == 0.0) fail(r, E_ERROR);
  i64 cells = steps_u * steps_v;
  g->by_cell = (vl *)arena_zalloc(F, sizeof(vl) * (size_t)cells);
  uint8_t *crossed = (uint8_t *)arena_zalloc(F, (size_t)cells);
  /* Loop segments bucketed by the cells their boxes touch: which cells a trim crosses here, and
   * which segment a boundary vertex lies on when it is pinned. */
  for (i64 l = 0; l < loop_count; l++) {
    const ring *ring_ = &loops[l];
    i64 count = views[l].n;
    for (i64 i = 0; i < count; i++) {
      i64 j = (i + 1) % count;
      gsegment s;
      s.x0 = ring_->uv.v[2 * i];
      s.y0 = ring_->uv.v[2 * i + 1];
      s.x1 = ring_->uv.v[2 * j];
      s.y1 = ring_->uv.v[2 * j + 1];
      s.ord = ring_->ords.v[i];
      s.m = ring_->metas.v[i];
      i64 index = g->segments.n;
      PUSH(F, g->segments, s);
      i64 ca0, cb0, ca1, cb1;
      cell_of(r, g, py_min(s.x0, s.x1), py_min(s.y0, s.y1), &ca0, &cb0);
      cell_of(r, g, py_max(s.x0, s.x1), py_max(s.y0, s.y1), &ca1, &cb1);
      for (i64 cu = ca0; cu <= ca1; cu++) {
        for (i64 cv = cb0; cv <= cb1; cv++) {
          i64 key = cu * steps_v + cv;
          crossed[key] = 1;
          PUSH(F, g->by_cell[key], index);
        }
      }
    }
  }
  vertex_maker vm;
  memset(&vm, 0, sizeof vm);
  vm.r = r;
  vm.A = A;
  vm.F = F;
  vm.g = g;
  /* Two vertices within one source ULP on both axes are one vertex: a grid line and a trim, or
   * two trims meeting at a corner, a hair apart. Planes keep exact keys. */
  vm.curved = f->s.kind != S_PLANE;
  vm.eps_u = py_max(py_max(py_max(fabs(min_u), fabs(max_u)), max_u - min_u), 1e-12) * ldexp(1.0, -23);
  vm.eps_v = py_max(py_max(py_max(fabs(min_v), fabs(max_v)), max_v - min_v), 1e-12) * ldexp(1.0, -23);
  map_init(&vm.nearby_index, F, 2, 64);
  map_init(&g->vertex_ids, F, 2, 256);
  g->snap_u = vm.curved ? vm.eps_u : 0.0;
  g->snap_v = vm.curved ? vm.eps_v : 0.0;

  /* Earcut's zero-area triangles over a straight run of trim are flipped away, not dropped: each
   * triangle's flag says it is one, until a flip makes it stand. */
  vb collinear = {NULL, 0, 0};
  double degenerate_limit = fabs((max_u - min_u) * (max_v - min_v)) * 1e-12;
  if (degenerate_limit == 0.0) degenerate_limit = 1e-30;
  /* Grid lines' coordinates are canonical: adjacent cells address their border with the same
   * floats. */
  double *grid_us = (double *)arena_alloc(F, sizeof(double) * (size_t)(steps_u + 1));
  double *grid_vs = (double *)arena_alloc(F, sizeof(double) * (size_t)(steps_v + 1));
  for (i64 iu = 0; iu <= steps_u; iu++) grid_us[iu] = iu == steps_u ? max_u : min_u + (double)iu * g->du;
  for (i64 iv = 0; iv <= steps_v; iv++) grid_vs[iv] = iv == steps_v ? max_v : min_v + (double)iv * g->dv;
  uint8_t *inside = NULL;
  for (i64 iu = 0; iu < steps_u; iu++) {
    double cu0 = grid_us[iu], cu1 = grid_us[iu + 1];
    for (i64 iv = 0; iv < steps_v; iv++) {
      double cv0 = grid_vs[iv], cv1 = grid_vs[iv + 1];
      if (!crossed[iu * steps_v + iv]) {
        /* An uncrossed cell is wholly inside or outside: its centre says which. */
        if (!inside) inside = inside_centres(F, views, loop_count, grid_us, steps_u, grid_vs, steps_v);
        if (!inside[iu * steps_v + iv]) continue;
        i32 a = vertex_id(&vm, cu0, cv0), b = vertex_id(&vm, cu1, cv0), c = vertex_id(&vm, cu1, cv1),
            d = vertex_id(&vm, cu0, cv1);
        PUSH3(A, g->tri, a, b, c);
        PUSH3(A, g->tri, a, c, d);
        PUSH2(F, collinear, 0, 0);
        continue;
      }
      arena_mark mark = arena_save(T);
      /* A cell a trim crosses: every loop clipped to it, the piece earcut. */
      poly *clipped = (poly *)arena_alloc(T, sizeof(poly) * (size_t)(loop_count ? loop_count : 1));
      i64 pieces = 0;
      for (i64 l = 0; l < loop_count; l++) {
        poly piece = clip_to_cell(T, views[l], cu0, cu1, cv0, cv1);
        if (piece.n >= 3) clipped[pieces++] = piece;
      }
      i64 outer_index = 0;
      double outer_abs_area = 0.0;
      for (i64 i = 0; i < pieces; i++) {
        double abs_area = fabs(polygon_area(clipped[i].p, clipped[i].n));
        if (abs_area > outer_abs_area) {
          outer_abs_area = abs_area;
          outer_index = i;
        }
      }
      if (!pieces || outer_abs_area <= degenerate_limit) {
        arena_reset(T, mark);
        continue;
      }
      poly contour = copy_poly(T, clipped[outer_index]);
      poly *holes = (poly *)arena_alloc(T, sizeof(poly) * (size_t)pieces);
      i64 hole_count = 0;
      for (i64 i = 0; i < pieces; i++)
        if (i != outer_index) holes[hole_count++] = copy_poly(T, clipped[i]);
      /* earcut over the rings themselves, which it may shorten (a closing repeat dropped); a
       * failure in it skips the cell, as there. */
      vi cell_faces = {NULL, 0, 0};
      if (!try_triangulate(r, T, &contour, holes, hole_count, &cell_faces)) {
        arena_reset(T, mark);
        continue;
      }
      cell_cuts cuts = {NULL, 0, 0};
      {
        i64 total = contour.n;
        for (i64 k = 0; k < hole_count; k++) total += holes[k].n;
        cell_cut first;
        first.points.p = (double *)arena_alloc(T, sizeof(double) * (size_t)(2 * (total ? total : 1)));
        first.points.n = total;
        memcpy(first.points.p, contour.p, sizeof(double) * (size_t)(2 * contour.n));
        i64 at = contour.n;
        for (i64 k = 0; k < hole_count; k++) {
          memcpy(first.points.p + 2 * at, holes[k].p, sizeof(double) * (size_t)(2 * holes[k].n));
          at += holes[k].n;
        }
        first.faces = cell_faces;
        PUSH(T, cuts, first);
      }
      /* What earcut made must cover what the loops leave of the cell. Where it does not -- a hole
       * crossing a side of the cell, which the clip hands earcut as a hole touching its outline --
       * the cell is cut again from its loops as a whole, and that cut is taken where it covers
       * what it must. */
      double holes_area = 0.0;
      for (i64 i = 0; i < pieces; i++)
        if (i != outer_index) holes_area += fabs(polygon_area(clipped[i].p, clipped[i].n));
      double region_area = outer_abs_area - holes_area;
      double slack = (cu1 - cu0) * (cv1 - cv0) * 1e-7;
      if (fabs(covered(cuts.v[0].points.p, &cuts.v[0].faces) - region_area) > slack) {
        regions rebuilt;
        if (cell_regions(r, T, views, loop_count, cu0, cu1, cv0, cv1, &rebuilt)) {
          cell_cuts again = {NULL, 0, 0};
          if (try_cut_regions(r, T, &rebuilt, &again)) {
            double sum = 0.0;
            for (i64 k = 0; k < again.n; k++) sum += covered(again.v[k].points.p, &again.v[k].faces);
            if (fabs(sum - region_area) <= slack) cuts = again;
          }
        }
      }
      for (i64 k = 0; k < cuts.n; k++) {
        poly local = cuts.v[k].points;
        const vi *faces_ = &cuts.v[k].faces;
        i32 *local_ids = (i32 *)arena_alloc(T, sizeof(i32) * (size_t)(local.n ? local.n : 1));
        for (i64 i = 0; i < local.n; i++) local_ids[i] = vertex_id(&vm, local.p[2 * i], local.p[2 * i + 1]);
        for (i64 t = 0; t + 2 < faces_->n; t += 3) {
          i32 a = faces_->v[t], b = faces_->v[t + 1], c = faces_->v[t + 2];
          const double *pa = local.p + 2 * a, *pb = local.p + 2 * b, *pc = local.p + 2 * c;
          double cross = (pb[0] - pa[0]) * (pc[1] - pa[1]) - (pc[0] - pa[0]) * (pb[1] - pa[1]);
          if (local_ids[a] == local_ids[b] || local_ids[b] == local_ids[c] || local_ids[c] == local_ids[a]) continue;
          uint8_t sliver = 0;
          if (fabs(cross) / 2 <= degenerate_limit) {
            if (!flip) continue;
            sliver = 1;
          }
          PUSH3(A, g->tri, local_ids[a], local_ids[b], local_ids[c]);
          PUSH(F, collinear, sliver);
        }
      }
      arena_reset(T, mark);
    }
  }
  arena_mark mark = arena_save(T);
  if (flip) flip_slivers(r, T, h, f, &g->uv, &g->tri, degenerate_limit, collinear.v);
  arena_reset(T, mark);
  int any = 0;
  for (i64 t = 0; t < collinear.n && !any; t++)
    if (collinear.v[t]) any = 1;
  if (any) {
    i64 kept = 0;
    for (i64 t = 0; t < collinear.n; t++) {
      if (collinear.v[t]) continue;
      g->tri.v[3 * kept] = g->tri.v[3 * t];
      g->tri.v[3 * kept + 1] = g->tri.v[3 * t + 1];
      g->tri.v[3 * kept + 2] = g->tri.v[3 * t + 2];
      kept++;
    }
    g->tri.n = 3 * kept;
  }
  split_iso_junctions(r, A, T, &g->uv, &g->tri);
  arena_reset(T, mark);
  return g;
}

/* Each triangle fanned to the vertices lying inside one of its edges along a line of constant u or
 * v. */
typedef struct {
  double key;
  vd values;
} iso_line;

static void split_iso_junctions(run *r, arena *A, arena *S, const vd *uv, vi *tri) {
  (void)r;
  i64 vertex_count = uv->n / 2;
  uint8_t *used = (uint8_t *)arena_zalloc(S, (size_t)(vertex_count ? vertex_count : 1));
  for (i64 k = 0; k < tri->n; k++) used[tri->v[k]] = 1;
  /* A line of constant v: the u of each vertex on it; and of constant u: each v. */
  map along_u_index, along_v_index, ids;
  map_init(&along_u_index, S, 1, 64);
  map_init(&along_v_index, S, 1, 64);
  map_init(&ids, S, 2, vertex_count);
  VEC(vd) along_u = {NULL, 0, 0}, along_v = {NULL, 0, 0};
  int crowded = 0;
  for (i64 index = 0; index < vertex_count; index++) {
    if (!used[index]) continue;
    double u = uv->v[2 * index], v = uv->v[2 * index + 1];
    int made;
    i64 *slot = map_ref(&along_u_index, (u64[]){key_bits(v)}, -1, &made);
    if (made) {
      vd empty = {NULL, 0, 0};
      *slot = along_u.n;
      PUSH(S, along_u, empty);
    }
    i64 line = map_get1(&along_u_index, key_bits(v), -1);
    PUSH(S, along_u.v[line], u);
    if (along_u.v[line].n > 2) crowded = 1;
    slot = map_ref(&along_v_index, (u64[]){key_bits(u)}, -1, &made);
    if (made) {
      vd empty = {NULL, 0, 0};
      *slot = along_v.n;
      PUSH(S, along_v, empty);
    }
    line = map_get1(&along_v_index, key_bits(u), -1);
    PUSH(S, along_v.v[line], v);
    if (along_v.v[line].n > 2) crowded = 1;
    u64 key[2] = {key_bits(u), key_bits(v)};
    map_put(&ids, key, index);
  }
  if (!crowded) return;
  for (i64 k = 0; k < along_u.n; k++) merge_sort(S, along_u.v[k].v, along_u.v[k].n, sizeof(double), less_double, NULL);
  for (i64 k = 0; k < along_v.n; k++) merge_sort(S, along_v.v[k].v, along_v.v[k].n, sizeof(double), less_double, NULL);
  vi out = {NULL, 0, 0};
  RESERVE(A, out, tri->n);
  vi pending = {NULL, 0, 0};
  RESERVE(S, pending, tri->n);
  for (i64 t = tri->n - 3; t >= 0; t -= 3) PUSH3(S, pending, tri->v[t], tri->v[t + 1], tri->v[t + 2]);
  vi chain = {NULL, 0, 0};
  while (pending.n) {
    pending.n -= 3;
    i32 a = pending.v[pending.n], b = pending.v[pending.n + 1], c = pending.v[pending.n + 2];
    i32 rotations[3][3] = {{a, b, c}, {b, c, a}, {c, a, b}};
    int split = 0;
    for (int k = 0; k < 3 && !split; k++) {
      i32 p = rotations[k][0], q = rotations[k][1], rr = rotations[k][2];
      double pu = uv->v[2 * p], pv = uv->v[2 * p + 1], qu = uv->v[2 * q], qv = uv->v[2 * q + 1];
      const vd *line;
      double start, end;
      int on_v;
      if (pv == qv) {
        line = &along_u.v[map_get1(&along_u_index, key_bits(pv), -1)];
        start = pu;
        end = qu;
        on_v = 1;
      } else if (pu == qu) {
        line = &along_v.v[map_get1(&along_v_index, key_bits(pu), -1)];
        start = pv;
        end = qv;
        on_v = 0;
      } else {
        continue;
      }
      double lo = start < end ? start : end, hi = start < end ? end : start;
      i64 from = bisect_right_d(line->v, line->n, lo), to = bisect_left_d(line->v, line->n, hi);
      if (from >= to) continue;
      chain.n = 0;
      PUSH(S, chain, p);
      for (i64 i = 0; i < to - from; i++) {
        double x = start < end ? line->v[from + i] : line->v[to - 1 - i];
        u64 key[2] = {on_v ? key_bits(x) : key_bits(pu), on_v ? key_bits(pv) : key_bits(x)};
        PUSH(S, chain, (i32)map_get(&ids, key, -1));
      }
      PUSH(S, chain, q);
      /* A fan from the opposite corner, each piece wound as the triangle was; each goes back on
       * the stack, in order, for its other edges. */
      for (i64 i = chain.n - 2; i >= 0; i--) PUSH3(S, pending, chain.v[i], chain.v[i + 1], rr);
      split = 1;
    }
    if (!split) PUSH3(A, out, a, b, c);
  }
  *tri = out;
}

/* sin(alpha + beta) < 0 for the angles at c and d over edge (a, b), scaled by the four edges'
 * lengths: the Delaunay test, in the metric's plane. */
static int angle_sum_past_pi(const double *a, const double *b, const double *c, const double *d) {
  double cax = a[0] - c[0], cay = a[1] - c[1], cbx = b[0] - c[0], cby = b[1] - c[1];
  double dax = a[0] - d[0], day = a[1] - d[1], dbx = b[0] - d[0], dby = b[1] - d[1];
  double value = fabs(cax * cby - cay * cbx) * (dax * dbx + day * dby) + (cax * cbx + cay * cby) * fabs(dax * dby - day * dbx);
  return value < -1e-9 * sqrt((cax * cax + cay * cay) * (cbx * cbx + cby * cby) * (dax * dax + day * day) *
                              (dbx * dbx + dby * dby));
}

/* Lawson flips over the base triangulation, in the face's parameters mapped through the surface's
 * metric at their box's centre. A flip is made only where both new triangles keep the face's
 * winding. */
static void flip_slivers(run *r, arena *S, const host *h, const face *f, const vd *uv, vi *tri, double degenerate_limit,
                         uint8_t *collinear) {
  i64 vertex_count = uv->n / 2;
  if (!vertex_count) return;
  double min_u = uv->v[0], max_u = uv->v[0], min_v = uv->v[1], max_v = uv->v[1];
  for (i64 i = 1; i < vertex_count; i++) {
    double u = uv->v[2 * i], v = uv->v[2 * i + 1];
    if (u < min_u) min_u = u;
    if (u > max_u) max_u = u;
    if (v < min_v) min_v = v;
    if (v > max_v) max_v = v;
  }
  if (!(max_u > min_u) || !(max_v > min_v)) return;
  double cu = (min_u + max_u) / 2, cv = (min_v + max_v) / 2;
  double hu = (max_u - min_u) * 1e-4, hv = (max_v - min_v) * 1e-4;
  double pu1[3], pu0[3], pv1[3], pv0[3];
  surface_value(r, h, &f->s, cu + hu, cv, pu1);
  surface_value(r, h, &f->s, cu - hu, cv, pu0);
  surface_value(r, h, &f->s, cu, cv + hv, pv1);
  surface_value(r, h, &f->s, cu, cv - hv, pv0);
  double su[3], sv[3];
  for (int d = 0; d < 3; d++) {
    su[d] = py_div(r, pu1[d] - pu0[d], 2 * hu);
    sv[d] = py_div(r, pv1[d] - pv0[d], 2 * hv);
  }
  double e = su[0] * su[0] + su[1] * su[1] + su[2] * su[2];
  double ff = su[0] * sv[0] + su[1] * sv[1] + su[2] * sv[2];
  double g = sv[0] * sv[0] + sv[1] * sv[1] + sv[2] * sv[2];
  double det = e * g - ff * ff;
  if (!(e > 0) || !(det > e * g * 1e-12) || !isfinite(det)) return;
  double root_e = sqrt(e), shear = ff / root_e, height = sqrt(det) / root_e;
  double *plane = (double *)arena_alloc(S, sizeof(double) * (size_t)(2 * vertex_count));
  for (i64 i = 0; i < vertex_count; i++) {
    double u = uv->v[2 * i], v = uv->v[2 * i + 1];
    plane[2 * i] = root_e * u + shear * v;
    plane[2 * i + 1] = height * v;
  }
  const double *P = plane, *U = uv->v;
#define ANGLE_SUM_PAST_PI(a, b, c, d) angle_sum_past_pi(P + 2 * (a), P + 2 * (b), P + 2 * (c), P + 2 * (d))
#define ORIENT(a, b, c)                                                                              \
  ((U[2 * (b)] - U[2 * (a)]) * (U[2 * (c) + 1] - U[2 * (a) + 1]) - \
   (U[2 * (c)] - U[2 * (a)]) * (U[2 * (b) + 1] - U[2 * (a) + 1]))
  /* Each edge and the triangles that use it (their offsets, in order): `index` names an edge's
   * entry, whose owners are at most a few; and whether it waits on the stack. */
  typedef struct {
    u64 key;
    i64 owners[4]; /* the first four; past four, all of them are in `spill` */
    i32 count, queued;
    vl *spill;
  } edge_entry;
  emap index;
  emap_init(&index, S, tri->n);
  VEC(edge_entry) entries = {NULL, 0, 0};
  RESERVE(S, entries, tri->n);
#define ENTRY_OF(key_, out_)                                    \
  do {                                                          \
    int made_;                                                  \
    i64 *slot_ = emap_ref(&index, (key_), -1, &made_);          \
    if (made_) {                                                \
      edge_entry fresh_;                                        \
      memset(&fresh_, 0, sizeof fresh_);                        \
      fresh_.key = (key_);                                      \
      *slot_ = entries.n;                                       \
      PUSH(S, entries, fresh_);                                 \
    }                                                           \
    (out_) = *slot_;                                            \
  } while (0)
  /* An edge's owners: appended, and removed first place first, as a list of them would be. */
#define OWN(entry_, t_)                                                              \
  do {                                                                               \
    edge_entry *o_ = (entry_);                                                       \
    if (!o_->spill && o_->count == 4) {                                              \
      o_->spill = (vl *)arena_zalloc(S, sizeof(vl));                                 \
      for (int i_ = 0; i_ < 4; i_++) PUSH(S, *o_->spill, o_->owners[i_]);            \
    }                                                                                \
    if (o_->spill) PUSH(S, *o_->spill, (t_));                                        \
    else o_->owners[o_->count] = (t_);                                               \
    o_->count++;                                                                     \
  } while (0)
  for (i64 t = 0; t < tri->n; t += 3) {
    i32 a = tri->v[t], b = tri->v[t + 1], c = tri->v[t + 2];
    i32 pairs[3][2] = {{a, b}, {b, c}, {c, a}};
    for (int k = 0; k < 3; k++) {
      i64 e;
      ENTRY_OF(edge_key(pairs[k][0], pairs[k][1]), e);
      OWN(&entries.v[e], t);
    }
  }
  /* Every edge waits on the stack, the first one met at its bottom. */
  vl pending = {NULL, 0, 0};
  RESERVE(S, pending, entries.n);
  for (i64 e = 0; e < entries.n; e++) {
    pending.v[e] = e;
    entries.v[e].queued = 1;
  }
  pending.n = entries.n;
  i64 budget = tri->n * 4;
  while (pending.n && budget > 0) {
    i64 e = pending.v[--pending.n];
    edge_entry *entry = &entries.v[e];
    entry->queued = 0;
    if (entry->count != 2) continue;
    u64 key = entry->key;
    i64 t1 = entry->spill ? entry->spill->v[0] : entry->owners[0];
    i64 t2 = entry->spill ? entry->spill->v[1] : entry->owners[1];
    i32 a = -1, b = -1, c = -1, d = -1;
    for (int k = 0; k < 3; k++) {
      i32 p = tri->v[t1 + k], q = tri->v[t1 + (k + 1) % 3];
      if (edge_key(p, q) == key) {
        a = p;
        b = q;
        c = tri->v[t1 + (k + 2) % 3];
      }
    }
    for (int k = 0; k < 3; k++) {
      i32 p = tri->v[t2 + k];
      if (p != a && p != b) d = p;
    }
    if (c < 0 || d < 0 || c == d) continue;
    if (!ANGLE_SUM_PAST_PI(a, b, c, d) || ANGLE_SUM_PAST_PI(c, d, b, a)) continue;
    /* The face's winding, read from the larger of the two. */
    double first = ORIENT(a, b, c), second = ORIENT(b, a, d);
    double larger = fabs(first) >= fabs(second) ? first : second;
    int sign = (larger > 0) - (larger < 0);
    if (!sign || sign * ORIENT(a, d, c) <= degenerate_limit || sign * ORIENT(d, b, c) <= degenerate_limit) continue;
    /* Disown t1 and t2: each leaves its edges' owners, its first place in each. */
    i64 ts[2] = {t1, t2};
    for (int which = 0; which < 2; which++) {
      i64 t = ts[which];
      i32 x = tri->v[t], y = tri->v[t + 1], z = tri->v[t + 2];
      i32 pairs[3][2] = {{x, y}, {y, z}, {z, x}};
      for (int k = 0; k < 3; k++) {
        edge_entry *owned = &entries.v[emap_get(&index, edge_key(pairs[k][0], pairs[k][1]), -1)];
        i64 *list = owned->spill ? owned->spill->v : owned->owners;
        for (i32 i = 0; i < owned->count; i++) {
          if (list[i] == t) {
            memmove(list + i, list + i + 1, sizeof(i64) * (size_t)(owned->count - i - 1));
            break;
          }
        }
        owned->count--;
        if (owned->spill) owned->spill->n--;
      }
    }
    collinear[t1 / 3] = 0;
    collinear[t2 / 3] = 0;
    tri->v[t1] = a;
    tri->v[t1 + 1] = d;
    tri->v[t1 + 2] = c;
    tri->v[t2] = d;
    tri->v[t2 + 1] = b;
    tri->v[t2 + 2] = c;
    for (int which = 0; which < 2; which++) {
      i64 t = ts[which];
      i32 x = tri->v[t], y = tri->v[t + 1], z = tri->v[t + 2];
      i32 pairs[3][2] = {{x, y}, {y, z}, {z, x}};
      for (int k = 0; k < 3; k++) {
        i64 at;
        ENTRY_OF(edge_key(pairs[k][0], pairs[k][1]), at);
        OWN(&entries.v[at], t);
      }
    }
    budget -= 1;
    i32 next_pairs[4][2] = {{a, d}, {d, b}, {b, c}, {c, a}};
    for (int k = 0; k < 4; k++) {
      i64 at = emap_get(&index, edge_key(next_pairs[k][0], next_pairs[k][1]), -1);
      if (entries.v[at].queued) continue;
      entries.v[at].queued = 1;
      PUSH(S, pending, at);
    }
  }
#undef ENTRY_OF
#undef OWN
#undef ANGLE_SUM_PAST_PI
#undef ORIENT
}
