/*
 * Part of tessellate.c's unity build: a component's geometry, evaluated.
 *
 * An analytic surface (plane, cylinder, cone, sphere, torus) by its formula, written operation
 * for operation as the tessellator it ports wrote it; its normal analytic, so a pole or an apex
 * keeps its limiting direction. A B-spline surface, curve or trim by its poles and knots; a
 * surface of linear extrusion or of revolution by its basis curve (a conic or a B-spline) swept
 * as OCCT sweeps it; the normal of each the cross product of its derivatives. Anything else --
 * and, where the host asks (a test of the library's own evaluation against OCCT's), everything
 * but the analytic surfaces -- by asking the host (`tess_callback`), which evaluates through
 * OCCT.
 */

#define MAX_DEGREE 25

/* What the host is asked to evaluate. */
enum { ASK_PCURVE = 0, ASK_CURVE = 1, ASK_SURFACE = 2, ASK_NORMAL = 3 };

typedef int (*tess_callback)(void *user, i32 what, i32 index, double a, double b, double *out);

enum {
  S_NONE = 0, S_PLANE = 1, S_CYLINDER = 2, S_CONE = 3, S_SPHERE = 4, S_TORUS = 5, S_BSPLINE = 6, S_HOST = 7,
  S_EXTRUSION = 8, S_REVOLUTION = 9
};
enum { C_NONE = 0, C_LINE = 1, C_CIRCLE = 2, C_ELLIPSE = 3, C_BSPLINE = 4, C_HYPERBOLA = 5, C_PARABOLA = 6 };

typedef struct {
  i32 degree, count, rational, dim;
  i32 periodic;          /* a parameter past [knots[degree], knots[count]] wraps */
  const double *knots;   /* count + degree + 1, multiplicities written out */
  const double *poles;   /* count * dim */
  const double *weights; /* count, or NULL */
} bspline_curve;

typedef struct {
  i32 degree_u, degree_v, count_u, count_v, rational, periodic_u, periodic_v;
  const double *knots_u, *knots_v;
  const double *poles; /* (i * count_v + j) * 3 */
  const double *weights;
  double first_u, last_u, first_v, last_v;
} bspline_surface;

typedef struct {
  i32 kind;     /* C_*: what the edge is, which its sampling reads */
  i32 host;     /* evaluated by the host */
  i32 index;
  i32 n;        /* its B-spline's poles, or -1 for none */
  double first, last;
  /* a line's origin and direction; a conic's centre, x and y; r1 and r2 its radii (a parabola's
   * focal length r1) */
  double o[3], x[3], y[3], d[3], r1, r2;
  bspline_curve bs;
} curve;

/* A surface of linear extrusion (its basis curve moved along `direction`) or of revolution (its
 * basis curve turned about the axis through `origin` along `direction`). */
typedef struct {
  curve basis;
  double origin[3], direction[3];
} swept_surface;

typedef struct {
  i32 kind; /* S_* */
  i32 index; /* the face's, for the host */
  double o[3], x[3], y[3], z[3];
  double radius, semi_angle, sin_a, cos_a, major, minor, handedness;
  double cos_lat, sin_lat; /* a cylinder's or cone's normal off its axis plane: cos and sin of 0 or -semi_angle */
  const bspline_surface *bs;
  const swept_surface *sw;
} surface;

typedef struct {
  i32 edge_ord, reversed, host, index;
  i32 n; /* poles */
  double first, last;
  bspline_curve bs;
  /* Along a fixed edge (OCCT's polyline): the face's mesh vertices at its knots, in the edge's
   * order, where OCCT meshed the face (`knot_nodes`), and each knot in this face's parameters,
   * as the host evaluated the pcurve there (`knot_uv`); NULL where there is none. */
  const i32 *knot_nodes;
  const double *knot_uv;
  i32 knot_count;
} pcurve;

/* --- B-splines --------------------------------------------------------------------------- */

/* The knot span holding t: knots[span] <= t < knots[span + 1] within [degree, count - 1],
 * the first or last span beyond the ends (whose polynomial continues there). */
static i32 find_span(i32 count, i32 degree, double t, const double *knots) {
  i32 span;
  if (!(t < knots[count])) {
    span = count - 1;
    while (span > degree && !(knots[span] < knots[span + 1])) span--;
    return span;
  }
  if (!(t > knots[degree])) {
    span = degree;
    while (span < count - 1 && !(knots[span] < knots[span + 1])) span++;
    return span;
  }
  i32 low = degree, high = count;
  while (high - low > 1) {
    i32 mid = (low + high) >> 1;
    if (t < knots[mid]) high = mid;
    else low = mid;
  }
  return low;
}

/* A periodic direction's parameter brought into its one period, as OCCT normalizes it. */
static double in_period(double t, double first, double last) {
  double period = last - first;
  if (!(period > 0)) return t;
  if (t < first || t > last) t -= floor((t - first) / period) * period;
  return t;
}

/* De Boor's algorithm over the `degree + 1` homogeneous points of a span (`width` numbers each):
 * the point at t, and in `slope` its first derivative -- the span's degree times the difference
 * of the two points the last level blends, over the span's length. Each level is a blend whose
 * last column's weight is exactly 1 at the span's end, so a clamped curve's ends are its end poles
 * exactly, as OCCT's own evaluation leaves them. */
static void de_boor(double *d, i32 width, i32 degree, i32 span, double t, const double *knots, double *slope) {
  for (i32 r = 1; r <= degree; r++) {
    if (r == degree && slope) {
      double scale = degree / (knots[span + 1] - knots[span]);
      for (i32 k = 0; k < width; k++) slope[k] = scale * (d[degree * width + k] - d[(degree - 1) * width + k]);
    }
    for (i32 j = degree; j >= r; j--) {
      i32 i = span - degree + j;
      double denominator = knots[i + degree + 1 - r] - knots[i];
      double alpha = denominator != 0.0 ? (t - knots[i]) / denominator : 0.0;
      for (i32 k = 0; k < width; k++) d[j * width + k] = (1.0 - alpha) * d[(j - 1) * width + k] + alpha * d[j * width + k];
    }
  }
}

/* De Boor's blends for one span and parameter, in the order `de_boor` makes them, for the rows of
 * a surface that all take the same: with `blend_rows`, the same arithmetic as `de_boor` per row. */
typedef struct {
  i32 degree;
  double scale; /* the slope's: degree over the span's length */
  double alpha[(MAX_DEGREE * (MAX_DEGREE + 1)) / 2];
} blends;

static void make_blends(i32 degree, i32 span, double t, const double *knots, blends *b) {
  b->degree = degree;
  b->scale = degree / (knots[span + 1] - knots[span]);
  i32 n = 0;
  for (i32 r = 1; r <= degree; r++)
    for (i32 j = degree; j >= r; j--) {
      i32 i = span - degree + j;
      double denominator = knots[i + degree + 1 - r] - knots[i];
      b->alpha[n++] = denominator != 0.0 ? (t - knots[i]) / denominator : 0.0;
    }
}

#define BLEND_ROW(WIDTH)                                                                              \
  static void blend_row_##WIDTH(double *d, const blends *b, double *slope) {                           \
    i32 degree = b->degree, n = 0;                                                                     \
    for (i32 r = 1; r <= degree; r++) {                                                                \
      if (r == degree && slope)                                                                        \
        for (i32 k = 0; k < WIDTH; k++) slope[k] = b->scale * (d[degree * WIDTH + k] - d[(degree - 1) * WIDTH + k]); \
      for (i32 j = degree; j >= r; j--) {                                                              \
        double alpha = b->alpha[n++];                                                                  \
        for (i32 k = 0; k < WIDTH; k++)                                                                \
          d[j * WIDTH + k] = (1.0 - alpha) * d[(j - 1) * WIDTH + k] + alpha * d[j * WIDTH + k];         \
      }                                                                                                \
    }                                                                                                  \
  }
BLEND_ROW(3)
BLEND_ROW(4)
#undef BLEND_ROW

static inline void blend_row(double *d, i32 width, const blends *b, double *slope) {
  if (width == 3) blend_row_3(d, b, slope);
  else blend_row_4(d, b, slope);
}

/* The curve's point at t and, when `tangent` is set, its first derivative. */
static void bspline_curve_d1(const bspline_curve *c, double t, double *out, double *tangent) {
  if (c->periodic) t = in_period(t, c->knots[c->degree], c->knots[c->count]);
  i32 span = find_span(c->count, c->degree, t, c->knots);
  i32 dim = c->dim, width = c->weights ? dim + 1 : dim, degree = c->degree;
  if (degree == 1 && !c->weights) {
    /* A polyline's span: de Boor's one level, written out. */
    const double *p0 = c->poles + (span - 1) * dim, *p1 = c->poles + span * dim;
    double denominator = c->knots[span + 1] - c->knots[span];
    double alpha = denominator != 0.0 ? (t - c->knots[span]) / denominator : 0.0;
    for (i32 k = 0; k < dim; k++) out[k] = (1.0 - alpha) * p0[k] + alpha * p1[k];
    if (tangent)
      for (i32 k = 0; k < dim; k++) tangent[k] = (p1[k] - p0[k]) / denominator;
    return;
  }
  double d[(MAX_DEGREE + 1) * 4], before[4];
  for (i32 j = 0; j <= degree; j++) {
    i32 at = span - degree + j;
    double w = c->weights ? c->weights[at] : 1.0;
    for (i32 k = 0; k < dim; k++) d[j * width + k] = c->weights ? c->poles[at * dim + k] * w : c->poles[at * dim + k];
    if (c->weights) d[j * width + dim] = w;
  }
  if (degree == 0) {
    for (i32 k = 0; k < dim; k++) out[k] = c->weights ? d[k] / d[dim] : d[k];
    if (tangent)
      for (i32 k = 0; k < dim; k++) tangent[k] = 0.0;
    return;
  }
  de_boor(d, width, degree, span, t, c->knots, tangent ? before : NULL);
  const double *p = d + degree * width;
  for (i32 k = 0; k < dim; k++) out[k] = c->weights ? p[k] / p[dim] : p[k];
  if (!tangent) return;
  for (i32 k = 0; k < dim; k++) tangent[k] = c->weights ? (before[k] - before[dim] * out[k]) / p[dim] : before[k];
}

static void bspline_curve_value(const bspline_curve *c, double t, double *out) { bspline_curve_d1(c, t, out, NULL); }

/* The surface's point and, when `du` is set, its first derivatives: de Boor along one direction on
 * each of the span's lines of poles, then along the other over what those give -- along the
 * direction of the higher degree first, the one that blends more on each line. On a boundary the
 * point is the boundary curve's either way, to the bit: a clamped end's blends give its end line
 * exactly. */
static void bspline_surface_d1(const bspline_surface *s, double u, double v, double *point, double *du, double *dv) {
  if (s->periodic_u) u = in_period(u, s->first_u, s->last_u);
  if (s->periodic_v) v = in_period(v, s->first_v, s->last_v);
  i32 su = find_span(s->count_u, s->degree_u, u, s->knots_u);
  i32 sv = find_span(s->count_v, s->degree_v, v, s->knots_v);
  i32 p = s->degree_u, q = s->degree_v;
  i32 width = s->weights ? 4 : 3;
  /* Lines along u (one per v) cost (q + 1) * p(p + 1) / 2 blends and then q(q + 1) / 2; along v,
   * the transpose. */
  int along_v_first = (q + 1) * p * (p + 1) + q * (q + 1) > (p + 1) * q * (q + 1) + p * (p + 1);
  /* `a` is the direction blended on each line, `b` the one across the lines. */
  i32 da = along_v_first ? q : p, db = along_v_first ? p : q;
  i32 sa = along_v_first ? sv : su, sb = along_v_first ? su : sv;
  blends along_a, along_b;
  if (da) make_blends(da, sa, along_v_first ? v : u, along_v_first ? s->knots_v : s->knots_u, &along_a);
  if (db) make_blends(db, sb, along_v_first ? u : v, along_v_first ? s->knots_u : s->knots_v, &along_b);
  double lines[(MAX_DEGREE + 1) * 4], lines_a[(MAX_DEGREE + 1) * 4];
  double d[(MAX_DEGREE + 1) * 4];
  /* Poles go by u then v: a step along a line and from line to line. */
  i32 base = (su - p) * s->count_v + (sv - q);
  i32 step_a = along_v_first ? 1 : s->count_v, step_b = along_v_first ? s->count_v : 1;
  for (i32 l = 0; l <= db; l++) {
    if (da == 1 && !s->weights) {
      /* A line of two poles: `blend_row`'s one level, written out. */
      const double *p0 = s->poles + 3 * (base + l * step_b), *p1 = p0 + 3 * step_a;
      double alpha = along_a.alpha[0];
      if (du)
        for (i32 k = 0; k < 3; k++) lines_a[l * 3 + k] = along_a.scale * (p1[k] - p0[k]);
      for (i32 k = 0; k < 3; k++) lines[l * 3 + k] = (1.0 - alpha) * p0[k] + alpha * p1[k];
      continue;
    }
    for (i32 m = 0; m <= da; m++) {
      i32 at = base + l * step_b + m * step_a;
      double w = s->weights ? s->weights[at] : 1.0;
      for (i32 k = 0; k < 3; k++) d[m * width + k] = s->weights ? s->poles[at * 3 + k] * w : s->poles[at * 3 + k];
      if (s->weights) d[m * width + 3] = w;
    }
    if (da == 0) {
      for (i32 k = 0; k < width; k++) lines[l * width + k] = d[k];
      if (du)
        for (i32 k = 0; k < width; k++) lines_a[l * width + k] = 0.0;
      continue;
    }
    blend_row(d, width, &along_a, du ? lines_a + l * width : NULL);
    for (i32 k = 0; k < width; k++) lines[l * width + k] = d[da * width + k];
  }
  /* h the point; ha its derivative along a, hb along b. */
  double h[4], ha[4] = {0, 0, 0, 0}, hb[4] = {0, 0, 0, 0};
  if (db == 0) {
    for (i32 k = 0; k < width; k++) h[k] = lines[k];
    if (du)
      for (i32 k = 0; k < width; k++) ha[k] = lines_a[k];
  } else {
    if (du) {
      blend_row(lines_a, width, &along_b, NULL);
      for (i32 k = 0; k < width; k++) ha[k] = lines_a[db * width + k];
    }
    blend_row(lines, width, &along_b, du ? hb : NULL);
    for (i32 k = 0; k < width; k++) h[k] = lines[db * width + k];
  }
  for (int k = 0; k < 3; k++) point[k] = s->weights ? h[k] / h[3] : h[k];
  if (!du) return;
  const double *hu = along_v_first ? hb : ha, *hv = along_v_first ? ha : hb;
  for (int k = 0; k < 3; k++) {
    if (s->weights) {
      du[k] = (hu[k] - hu[3] * point[k]) / h[3];
      dv[k] = (hv[k] - hv[3] * point[k]) / h[3];
    } else {
      du[k] = hu[k];
      dv[k] = hv[k];
    }
  }
}

/* --- evaluation -------------------------------------------------------------------------- */

typedef struct {
  tess_callback callback;
  void *user;
} host;

static inline void sin_cos(run *r, double x, double *s, double *c) {
  u64 bits;
  memcpy(&bits, &x, sizeof bits);
  trig_entry *e = &r->trig[(bits * 0x9e3779b97f4a7c15ULL) >> (64 - TRIG_BITS)];
  if (e->bits != bits) {
    e->bits = bits;
    e->s = sin(x);
    e->c = cos(x);
  }
  *s = e->s;
  *c = e->c;
}

static void ask(run *r, const host *h, i32 what, i32 index, double a, double b, double *out) {
  if (!h->callback || h->callback(h->user, what, index, a, b, out) != 0) fail(r, E_CALLBACK);
}

static void swept_d1(run *r, const swept_surface *s, int revolution, double u, double v, double *out, double *du,
                     double *dv);

static inline void mix(const surface *s, double a, double b, double c, double *out) {
  out[0] = s->o[0] + a * s->x[0] + b * s->y[0] + c * s->z[0];
  out[1] = s->o[1] + a * s->x[1] + b * s->y[1] + c * s->z[1];
  out[2] = s->o[2] + a * s->x[2] + b * s->y[2] + c * s->z[2];
}

/* The surface's point at (u, v). */
static void surface_value(run *r, const host *h, const surface *s, double u, double v, double *out) {
  double su, cu, sv, cv;
  switch (s->kind) {
    case S_PLANE:
      mix(s, u, v, 0.0, out);
      return;
    case S_CYLINDER:
      sin_cos(r, u, &su, &cu);
      mix(s, s->radius * cu, s->radius * su, v, out);
      return;
    case S_CONE: {
      double radius = s->radius + v * s->sin_a;
      if (fabs(radius) < (fabs(s->radius) + fabs(v)) * PY_EPSILON * 4) radius = 0.0;
      sin_cos(r, u, &su, &cu);
      mix(s, radius * cu, radius * su, v * s->cos_a, out);
      return;
    }
    case S_SPHERE: {
      sin_cos(r, v, &sv, &cv);
      sin_cos(r, u, &su, &cu);
      double flat = fabs(cv) < PY_EPSILON * 4 ? 0.0 : cv;
      mix(s, s->radius * flat * cu, s->radius * flat * su, s->radius * sv, out);
      return;
    }
    case S_TORUS: {
      sin_cos(r, v, &sv, &cv);
      sin_cos(r, u, &su, &cu);
      double ring = s->major + s->minor * cv;
      mix(s, ring * cu, ring * su, s->minor * sv, out);
      return;
    }
    case S_BSPLINE:
      bspline_surface_d1(s->bs, u, v, out, NULL, NULL);
      return;
    case S_EXTRUSION:
    case S_REVOLUTION:
      swept_d1(r, s->sw, s->kind == S_REVOLUTION, u, v, out, NULL, NULL);
      return;
    case S_HOST:
      ask(r, h, ASK_SURFACE, s->index, u, v, out);
      return;
    default:
      fail(r, E_ERROR);
  }
}

/* The surface's normal at (u, v): analytic for the analytic kinds; else the cross product of
 * the derivatives, and where one vanishes -- a pole, a collapsed edge -- the surface's just
 * inside the face, toward the middle of `box` (u0, u1, v0, v1). */
static void surface_normal(run *r, const host *h, const surface *s, double u, double v, const double *box,
                           double *out) {
  if (s->kind >= S_PLANE && s->kind <= S_TORUS) {
    double nx, ny, nz;
    if (s->kind == S_PLANE) {
      nx = 0.0;
      ny = 0.0;
      nz = 1.0;
    } else {
      /* The latitude: a cylinder's 0, a cone's -semi_angle, a sphere's or torus's v. */
      double su, cu, sl = s->sin_lat, cl = s->cos_lat;
      if (s->kind == S_SPHERE || s->kind == S_TORUS) sin_cos(r, v, &sl, &cl);
      sin_cos(r, u, &su, &cu);
      nx = cu * cl;
      ny = su * cl;
      nz = sl;
    }
    double k = s->handedness;
    out[0] = k * (nx * s->x[0] + ny * s->y[0] + nz * s->z[0]);
    out[1] = k * (nx * s->x[1] + ny * s->y[1] + nz * s->z[1]);
    out[2] = k * (nx * s->x[2] + ny * s->y[2] + nz * s->z[2]);
    return;
  }
  if (s->kind == S_HOST) {
    ask(r, h, ASK_NORMAL, s->index, u, v, out);
    return;
  }
  if (s->kind != S_BSPLINE && s->kind != S_EXTRUSION && s->kind != S_REVOLUTION) fail(r, E_ERROR);
  static const double nudges[4] = {0.0, 1e-7, 1e-5, 1e-3};
  for (int k = 0; k < 4; k++) {
    double point[3], du[3], dv[3];
    double at_u = u, at_v = v;
    if (nudges[k] != 0.0) {
      double cu = (box[0] + box[1]) / 2, cv = (box[2] + box[3]) / 2;
      at_u = u + (cu - u) * nudges[k];
      at_v = v + (cv - v) * nudges[k];
    }
    if (s->kind == S_BSPLINE) bspline_surface_d1(s->bs, at_u, at_v, point, du, dv);
    else swept_d1(r, s->sw, s->kind == S_REVOLUTION, at_u, at_v, point, du, dv);
    double nx = du[1] * dv[2] - du[2] * dv[1];
    double ny = du[2] * dv[0] - du[0] * dv[2];
    double nz = du[0] * dv[1] - du[1] * dv[0];
    double length = sqrt(nx * nx + ny * ny + nz * nz);
    double su = du[0] * du[0] + du[1] * du[1] + du[2] * du[2];
    double sv = dv[0] * dv[0] + dv[1] * dv[1] + dv[2] * dv[2];
    if (length > py_max(su, sv) * 1e-12) {
      out[0] = nx / length;
      out[1] = ny / length;
      out[2] = nz / length;
      return;
    }
  }
  out[0] = out[1] = out[2] = 0.0;
}

static void native_curve_d1(run *r, const curve *c, double t, double *out, double *tangent);

/* Whether a B-spline surface's row of poles at v-index j is one point. */
static int degenerate_row(const bspline_surface *s, i32 j) {
  const double *first = s->poles + (size_t)j * 3;
  double scale = fabs(first[0]) + fabs(first[1]) + fabs(first[2]);
  for (i32 i = 1; i < s->count_u; i++) {
    const double *p = s->poles + ((size_t)i * s->count_v + j) * 3;
    if (fabs(p[0] - first[0]) + fabs(p[1] - first[1]) + fabs(p[2] - first[2]) > scale * 1e-12) return 0;
  }
  return 1;
}

/* Whether the surface's u-line at v is one point: a sphere's pole, a cone's apex, a B-spline's
 * boundary row of one pole, a surface of revolution's basis on its axis. */
static int singular_u(run *r, const surface *s, double v) {
  if (s->kind == S_SPHERE) return fabs(cos(v)) < 1e-12;
  if (s->kind == S_CONE) return fabs(s->radius + v * sin(s->semi_angle)) < (fabs(s->radius) + fabs(v)) * 1e-12;
  if (s->kind == S_BSPLINE) {
    const bspline_surface *b = s->bs;
    double eps = (fabs(b->first_v) + fabs(b->last_v) + (b->last_v - b->first_v)) * 1e-12;
    if (fabs(v - b->first_v) <= eps) return degenerate_row(b, 0);
    if (fabs(v - b->last_v) <= eps) return degenerate_row(b, b->count_v - 1);
    return 0;
  }
  if (s->kind == S_REVOLUTION) {
    double p[3], arm[3];
    native_curve_d1(r, &s->sw->basis, v, p, NULL);
    for (int d = 0; d < 3; d++) arm[d] = p[d] - s->sw->origin[d];
    const double *k = s->sw->direction;
    double off[3] = {k[1] * arm[2] - k[2] * arm[1], k[2] * arm[0] - k[0] * arm[2], k[0] * arm[1] - k[1] * arm[0]};
    double reach = fabs(arm[0]) + fabs(arm[1]) + fabs(arm[2]);
    return fabs(off[0]) + fabs(off[1]) + fabs(off[2]) <= reach * 1e-12;
  }
  return 0;
}

/* Whether a B-spline surface's column of poles at u-index i is one point. */
static int degenerate_column(const bspline_surface *s, i32 i) {
  const double *first = s->poles + (size_t)i * s->count_v * 3;
  double scale = fabs(first[0]) + fabs(first[1]) + fabs(first[2]);
  for (i32 j = 1; j < s->count_v; j++) {
    const double *p = s->poles + ((size_t)i * s->count_v + j) * 3;
    if (fabs(p[0] - first[0]) + fabs(p[1] - first[1]) + fabs(p[2] - first[2]) > scale * 1e-12) return 0;
  }
  return 1;
}

/* Whether the surface's v-line at u is one point: a B-spline's boundary column of one pole. No
 * other surface the library evaluates collapses along v -- a sphere, a cone and a surface of
 * revolution collapse along u (`singular_u`), an extrusion never. */
static int singular_v(run *r, const surface *s, double u) {
  (void)r;
  if (s->kind != S_BSPLINE) return 0;
  const bspline_surface *b = s->bs;
  double eps = (fabs(b->first_u) + fabs(b->last_u) + (b->last_u - b->first_u)) * 1e-12;
  if (fabs(u - b->first_u) <= eps) return degenerate_column(b, 0);
  if (fabs(u - b->last_u) <= eps) return degenerate_column(b, b->count_u - 1);
  return 0;
}

static void pcurve_value(run *r, const host *h, const pcurve *p, double t, double *out) {
  if (p->host) ask(r, h, ASK_PCURVE, p->index, t, 0.0, out);
  else bspline_curve_value(&p->bs, t, out);
}

/* A curve the library evaluates itself: its point at t and, when `tangent` is set, its first
 * derivative -- the conics as OCCT's ElCLib writes them. */
static void native_curve_d1(run *r, const curve *c, double t, double *out, double *tangent) {
  switch (c->kind) {
    case C_LINE:
      for (int d = 0; d < 3; d++) out[d] = t * c->d[d] + c->o[d];
      if (tangent) memcpy(tangent, c->d, sizeof(double) * 3);
      return;
    case C_CIRCLE:
    case C_ELLIPSE: {
      double ct, st;
      sin_cos(r, t, &st, &ct);
      double r2 = c->kind == C_CIRCLE ? c->r1 : c->r2;
      double a1 = c->r1 * ct, a2 = r2 * st;
      for (int d = 0; d < 3; d++) out[d] = a1 * c->x[d] + a2 * c->y[d] + c->o[d];
      if (tangent)
        for (int d = 0; d < 3; d++) tangent[d] = -c->r1 * st * c->x[d] + r2 * ct * c->y[d];
      return;
    }
    case C_HYPERBOLA: {
      double ch = cosh(t), sh = sinh(t);
      double a1 = c->r1 * ch, a2 = c->r2 * sh;
      for (int d = 0; d < 3; d++) out[d] = a1 * c->x[d] + a2 * c->y[d] + c->o[d];
      if (tangent)
        for (int d = 0; d < 3; d++) tangent[d] = c->r1 * sh * c->x[d] + c->r2 * ch * c->y[d];
      return;
    }
    case C_PARABOLA: {
      double focal = c->r1;
      if (focal == 0.0) {
        for (int d = 0; d < 3; d++) out[d] = t * c->x[d] + c->o[d];
        if (tangent) memcpy(tangent, c->x, sizeof(double) * 3);
        return;
      }
      double a1 = t * t / (4.0 * focal);
      for (int d = 0; d < 3; d++) out[d] = a1 * c->x[d] + t * c->y[d] + c->o[d];
      if (tangent)
        for (int d = 0; d < 3; d++) tangent[d] = t / (2.0 * focal) * c->x[d] + c->y[d];
      return;
    }
    case C_BSPLINE:
      bspline_curve_d1(&c->bs, t, out, tangent);
      return;
    default:
      fail(r, E_ERROR);
  }
}

static void curve_value(run *r, const host *h, const curve *c, double t, double *out) {
  if (c->host) {
    ask(r, h, ASK_CURVE, c->index, t, 0.0, out);
    return;
  }
  native_curve_d1(r, c, t, out, NULL);
}

/* v turned by the angle u about the unit axis k (Rodrigues). */
static inline void rotate(run *r, const double *k, double u, const double *v, double *out) {
  double cu, su;
  sin_cos(r, u, &su, &cu);
  double kv = k[0] * v[0] + k[1] * v[1] + k[2] * v[2];
  double cross[3] = {k[1] * v[2] - k[2] * v[1], k[2] * v[0] - k[0] * v[2], k[0] * v[1] - k[1] * v[0]};
  for (int d = 0; d < 3; d++) out[d] = v[d] * cu + cross[d] * su + k[d] * kv * (1 - cu);
}

/* A swept surface's point and, when `du` is set, its first derivatives. Extrusion: the basis at u
 * moved v along the direction. Revolution: the basis at v turned u about the axis. */
static void swept_d1(run *r, const swept_surface *s, int revolution, double u, double v, double *out, double *du,
                     double *dv) {
  if (!revolution) {
    double p[3], t[3];
    native_curve_d1(r, &s->basis, u, p, du ? t : NULL);
    for (int d = 0; d < 3; d++) out[d] = p[d] + v * s->direction[d];
    if (du) {
      memcpy(du, t, sizeof t);
      memcpy(dv, s->direction, sizeof(double) * 3);
    }
    return;
  }
  double p[3], t[3], arm[3];
  native_curve_d1(r, &s->basis, v, p, du ? t : NULL);
  for (int d = 0; d < 3; d++) arm[d] = p[d] - s->origin[d];
  double turned[3];
  rotate(r, s->direction, u, arm, turned);
  for (int d = 0; d < 3; d++) out[d] = s->origin[d] + turned[d];
  if (!du) return;
  const double *k = s->direction;
  du[0] = k[1] * turned[2] - k[2] * turned[1];
  du[1] = k[2] * turned[0] - k[0] * turned[2];
  du[2] = k[0] * turned[1] - k[1] * turned[0];
  rotate(r, k, u, t, dv);
}
