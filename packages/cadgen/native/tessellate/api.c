/*
 * Part of tessellate.c's unity build: one component meshed, and the library's interface.
 *
 * The host (cadgen._internal.tessellate) reads the component's geometry through OCCT into flat
 * arrays (`tess_input`), calls `cadgen_tessellate`, and packs what comes back (`tess_result`):
 * each face's float32 positions, normals and triangles, each model edge's polyline, and which
 * faces were too narrow to resolve. A face its geometry defeats comes out with no mesh; anything
 * else that fails fails the component, with the result's `error` set.
 */

#define TESS_ABI 1

/* Column counts of the input tables. */
enum {
  FACE_I = 8, FACE_D = 20, PCURVE_I = 8, PCURVE_D = 2, EDGE_I = 9, EDGE_D = 16, BSURF_I = 10, BSURF_D = 4, POLE_D = 7,
  SWEPT_I = 2, SWEPT_D = 6
};

typedef struct {
  i32 face_count, edge_count, pcurve_count, bsurface_count, swept_count, basis_count;
  double chord, angle;
  /* face: ord, reversed, surface kind, its row in the B-spline or swept surface table, first
   * loop, loops, first pole, poles */
  const i32 *face_i;
  /* face: u0, u1, v0, v1, origin, x, y and z directions, radius, semi-angle, major, minor */
  const double *face_d;
  /* loop: first pcurve, pcurves */
  const i32 *loop_i;
  /* pcurve: edge ordinal, reversed, by the host, poles, degree, rational, knots at, poles at */
  const i32 *pcurve_i;
  /* pcurve: first, last */
  const double *pcurve_d;
  /* edge: kind (0 for no curve), by the host, poles (-1 for none), degree, B-spline poles,
   * rational, knots at, poles at, periodic */
  const i32 *edge_i;
  /* edge: first, last, origin, x, y, direction, first radius, second radius */
  const double *edge_d;
  /* swept surface: kind (extrusion or revolution), its basis curve's row */
  const i32 *swept_i;
  /* swept surface: the axis's origin, the direction */
  const double *swept_d;
  /* basis curves, in the edges' columns */
  const i32 *basis_i;
  const double *basis_d;
  /* B-spline surface: u and v degrees, u and v poles, rational, periodic in u and in v, u knots
   * at, v knots at, poles at (weights follow the poles) */
  const i32 *bsurface_i;
  /* B-spline surface: first and last u, first and last v */
  const double *bsurface_d;
  /* face pole: axis, at, lo, hi, point */
  const double *pole_d;
  const double *reals;
  tess_callback callback;
  void *user;
} tess_input;

enum { FACE_NONE = 0, FACE_MESHED = 1, FACE_UNRESOLVED = 2 };

typedef struct {
  i32 abi;
  i32 error;
  char message[160];
  double scale;
  i32 face_count, edge_count;
  i32 *face_state;     /* FACE_* */
  i64 *face_vertices;  /* per face: its vertices (none when it has no triangle) */
  i64 *face_triangles; /* per face: its triangles */
  float *positions, *normals;
  u32 *triangles; /* each face's own indices */
  i64 vertex_total, triangle_total;
  i64 *edge_points; /* per edge: its polyline's points (0 for none) */
  double *points;
  i64 point_total;
} tess_result;

static void *out_alloc(run *r, size_t size) {
  void *p = malloc(size ? size : 1);
  if (!p) fail(r, E_MEMORY);
  return p;
}

static void read_bspline_curve(const i32 *ints, const double *reals, i32 dim, bspline_curve *out) {
  out->count = ints[0];
  out->degree = ints[1];
  out->rational = ints[2];
  out->dim = dim;
  out->periodic = 0;
  out->knots = reals + ints[3];
  out->poles = reals + ints[4];
  out->weights = ints[2] ? reals + ints[4] + (i64)ints[0] * dim : NULL;
}

/* A curve row (an edge's, or a swept surface's basis): its kind, range and geometry. */
static void read_curve(run *r, const i32 *ii, const double *dd, const double *reals, i32 index, curve *c) {
  c->kind = ii[0];
  c->host = ii[1];
  c->n = ii[2];
  c->index = index;
  c->first = dd[0];
  c->last = dd[1];
  for (int d = 0; d < 3; d++) {
    c->o[d] = dd[2 + d];
    c->x[d] = dd[5 + d];
    c->y[d] = dd[8 + d];
    c->d[d] = dd[11 + d];
  }
  c->r1 = dd[14];
  c->r2 = dd[15];
  if (c->kind == C_BSPLINE && !c->host) {
    i32 spec[5] = {ii[4], ii[3], ii[5], ii[6], ii[7]};
    read_bspline_curve(spec, reals, 3, &c->bs);
    c->bs.periodic = ii[8];
    if (c->bs.degree > MAX_DEGREE || c->bs.count <= c->bs.degree) fail(r, E_ERROR);
  }
}

/* Edge ends that coincide are one corner of the model, each cluster one point; the first corner
 * within the tolerance wins. */
typedef struct {
  run *r;
  arena *A;
  double tolerance, size;
  vd corners;
  multimap cells;
} corner_welder;

static i32 canonical_corner(corner_welder *w, const double *point) {
  i64 cx = py_floor(w->r, point[0] / w->size), cy = py_floor(w->r, point[1] / w->size),
      cz = py_floor(w->r, point[2] / w->size);
  i64 best = -1;
  for (i64 dx = -1; dx <= 1; dx++) {
    for (i64 dy = -1; dy <= 1; dy++) {
      for (i64 dz = -1; dz <= 1; dz++) {
        u64 key[3] = {(u64)(cx + dx), (u64)(cy + dy), (u64)(cz + dz)};
        i64 list = multimap_find(&w->cells, key);
        if (list < 0) continue;
        vl *indices = &w->cells.lists.v[list];
        for (i64 k = 0; k < indices->n; k++) {
          i64 index = indices->v[k];
          if ((best < 0 || index < best) && dist3(w->corners.v + 3 * index, point) <= w->tolerance) best = index;
        }
      }
    }
  }
  if (best >= 0) return (i32)best;
  u64 key[3] = {(u64)cx, (u64)cy, (u64)cz};
  i64 index = w->corners.n / 3;
  multimap_add(&w->cells, key, index);
  PUSH3(w->A, w->corners, point[0], point[1], point[2]);
  return (i32)index;
}

static void weld_corners(run *r, arena *A, shared *edges, i32 edge_count, double tolerance, corner_welder *w) {
  memset(w, 0, sizeof *w);
  w->r = r;
  w->A = A;
  w->tolerance = tolerance;
  w->size = tolerance > 0 ? tolerance : 1.0;
  multimap_init(&w->cells, A, 3, 64);
  for (i32 e = 0; e < edge_count; e++) {
    shared *sh = &edges[e];
    if (!sh->present) continue;
    i64 last = sh->points.n / 3 - 1;
    double *points = sh->points.v;
    if (sh->closed) {
      /* A closed edge's seam can meet other edges' corners (a cylinder's seam and a line). */
      i32 corner = canonical_corner(w, points);
      memcpy(points, w->corners.v + 3 * corner, sizeof(double) * 3);
      memcpy(points + 3 * last, w->corners.v + 3 * corner, sizeof(double) * 3);
      sh->corner_start = sh->corner_end = corner;
      continue;
    }
    i32 start = canonical_corner(w, points);
    memcpy(points, w->corners.v + 3 * start, sizeof(double) * 3);
    i32 end = canonical_corner(w, points + 3 * last);
    memcpy(points + 3 * last, w->corners.v + 3 * end, sizeof(double) * 3);
    sh->corner_start = start;
    sh->corner_end = end;
    /* Ends that weld into one corner make the edge closed, though its curve's ends miss by more
     * than the closure test allowed; a curve no longer than the weld distance stays open. */
    if (sh->c->kind != C_LINE && start == end) {
      int long_enough = 0;
      for (i64 i = 0; i <= last && !long_enough; i++)
        if (dist3(points + 3 * i, points) > tolerance * ldexp(1.0, 10)) long_enough = 1;
      if (long_enough) sh->closed = 1;
    }
  }
}

static void snap_poles(arena *T, const face *f, raw_face *raw, double tolerance) {
  vl indices = {NULL, 0, 0};
  vd points = {NULL, 0, 0};
  on_poles(T, f, raw, tolerance, &indices, &points);
  for (i64 k = 0; k < indices.n; k++) memcpy(raw->xyz.v + 3 * indices.v[k], points.v + 3 * k, sizeof(double) * 3);
}

/* One face's raw mesh: 1 made, 0 none (no mesh, or a face its geometry defeats, which is left
 * out), -1 a face too narrow to resolve. Running out of memory fails the component. */
static int try_face(run *r, arenas M, const host *h, const face *f, const pcurve *pcurves, double scale,
                    const tess_input *in, const shared *edges, i32 E, raw_face *raw) {
  guard g;
  ENTER(r, g);
  if (setjmp(g.jump) != 0) {
    LEAVE(r, g);
    if (g.code == E_MEMORY) fail(r, E_MEMORY);
    return g.code == E_UNRESOLVED ? -1 : 0;
  }
  int made = tessellate_face_raw(r, M, h, f, pcurves, scale, in->chord, in->angle, edges, E, raw);
  LEAVE(r, g);
  return made;
}

/* The component's geometry as the library evaluates it. */
typedef struct {
  face *faces;
  pcurve *pcurves;
  curve *curves;
} geometry;

static void read_geometry(run *r, arenas M, const tess_input *in, geometry *out);
static void mesh_rest(run *r, arenas M, const host *hp, const tess_input *in, tess_result *res, face *faces,
                      pcurve *pcurves, curve *curves, i32 F, i32 E);

static void mesh_component(run *r, const tess_input *in, tess_result *res) {
  arenas M = {&r->A, &r->F, &r->T};
  host h = {in->callback, in->user};
  i32 F = in->face_count, E = in->edge_count;
  geometry geo;
  read_geometry(r, M, in, &geo);
  face *faces = geo.faces;
  pcurve *pcurves = geo.pcurves;
  curve *curves = geo.curves;
  mesh_rest(r, M, &h, in, res, faces, pcurves, curves, F, E);
}

static void read_geometry(run *r, arenas M, const tess_input *in, geometry *out) {
  i32 F = in->face_count, E = in->edge_count;
  /* The geometry. */
  bspline_surface *bsurfaces = (bspline_surface *)arena_zalloc(M.A, sizeof(bspline_surface) * (size_t)(in->bsurface_count + 1));
  for (i32 k = 0; k < in->bsurface_count; k++) {
    const i32 *ii = in->bsurface_i + (i64)k * BSURF_I;
    const double *dd = in->bsurface_d + (i64)k * BSURF_D;
    bspline_surface *s = &bsurfaces[k];
    s->degree_u = ii[0];
    s->degree_v = ii[1];
    s->count_u = ii[2];
    s->count_v = ii[3];
    s->rational = ii[4];
    s->periodic_u = ii[5];
    s->periodic_v = ii[6];
    s->knots_u = in->reals + ii[7];
    s->knots_v = in->reals + ii[8];
    s->poles = in->reals + ii[9];
    s->weights = ii[4] ? in->reals + ii[9] + (i64)ii[2] * ii[3] * 3 : NULL;
    s->first_u = dd[0];
    s->last_u = dd[1];
    s->first_v = dd[2];
    s->last_v = dd[3];
    if (s->degree_u > MAX_DEGREE || s->degree_v > MAX_DEGREE || s->count_u <= s->degree_u || s->count_v <= s->degree_v)
      fail(r, E_ERROR);
  }
  pcurve *pcurves = (pcurve *)arena_zalloc(M.A, sizeof(pcurve) * (size_t)(in->pcurve_count + 1));
  for (i32 k = 0; k < in->pcurve_count; k++) {
    const i32 *ii = in->pcurve_i + (i64)k * PCURVE_I;
    pcurve *p = &pcurves[k];
    p->edge_ord = ii[0];
    p->reversed = ii[1];
    p->host = ii[2];
    p->index = k;
    p->n = ii[3];
    p->first = in->pcurve_d[2 * (i64)k];
    p->last = in->pcurve_d[2 * (i64)k + 1];
    if (!p->host) {
      i32 spec[5] = {ii[3], ii[4], ii[5], ii[6], ii[7]};
      read_bspline_curve(spec, in->reals, 2, &p->bs);
      if (p->bs.degree > MAX_DEGREE || p->bs.count <= p->bs.degree) fail(r, E_ERROR);
    }
  }
  curve *curves = (curve *)arena_zalloc(M.A, sizeof(curve) * (size_t)(E + 1));
  for (i32 k = 0; k < E; k++)
    read_curve(r, in->edge_i + (i64)k * EDGE_I, in->edge_d + (i64)k * EDGE_D, in->reals, k, &curves[k]);
  swept_surface *swept = (swept_surface *)arena_zalloc(M.A, sizeof(swept_surface) * (size_t)(in->swept_count + 1));
  for (i32 k = 0; k < in->swept_count; k++) {
    const i32 *ii = in->swept_i + (i64)k * SWEPT_I;
    const double *dd = in->swept_d + (i64)k * SWEPT_D;
    if (ii[1] < 0 || ii[1] >= in->basis_count) fail(r, E_ERROR);
    read_curve(r, in->basis_i + (i64)ii[1] * EDGE_I, in->basis_d + (i64)ii[1] * EDGE_D, in->reals, ii[1],
               &swept[k].basis);
    if (swept[k].basis.host || swept[k].basis.kind == C_NONE) fail(r, E_ERROR);
    memcpy(swept[k].origin, dd, sizeof(double) * 3);
    memcpy(swept[k].direction, dd + 3, sizeof(double) * 3);
  }
  face *faces = (face *)arena_zalloc(M.A, sizeof(face) * (size_t)(F + 1));
  for (i32 k = 0; k < F; k++) {
    const i32 *ii = in->face_i + (i64)k * FACE_I;
    const double *dd = in->face_d + (i64)k * FACE_D;
    face *f = &faces[k];
    f->ord = ii[0];
    f->reversed = ii[1];
    surface *s = &f->s;
    s->kind = ii[2];
    s->index = k;
    if (s->kind == S_BSPLINE) {
      if (ii[3] < 0 || ii[3] >= in->bsurface_count) fail(r, E_ERROR);
      s->bs = &bsurfaces[ii[3]];
    } else if (s->kind == S_EXTRUSION || s->kind == S_REVOLUTION) {
      if (ii[3] < 0 || ii[3] >= in->swept_count) fail(r, E_ERROR);
      s->sw = &swept[ii[3]];
    }
    for (int d = 0; d < 4; d++) f->uv[d] = dd[d];
    for (int d = 0; d < 3; d++) {
      s->o[d] = dd[4 + d];
      s->x[d] = dd[7 + d];
      s->y[d] = dd[10 + d];
      s->z[d] = dd[13 + d];
    }
    s->radius = dd[16];
    s->semi_angle = dd[17];
    s->major = dd[18];
    s->minor = dd[19];
    s->sin_a = sin(s->semi_angle);
    s->cos_a = cos(s->semi_angle);
    double latitude = s->kind == S_CONE ? -s->semi_angle : 0.0;
    s->cos_lat = cos(latitude);
    s->sin_lat = sin(latitude);
    double cross[3] = {s->x[1] * s->y[2] - s->x[2] * s->y[1], s->x[2] * s->y[0] - s->x[0] * s->y[2],
                       s->x[0] * s->y[1] - s->x[1] * s->y[0]};
    s->handedness = cross[0] * s->z[0] + cross[1] * s->z[1] + cross[2] * s->z[2] < 0 ? -1.0 : 1.0;
    i32 first_loop = ii[4], loops = ii[5];
    f->loop_count = loops;
    i32 *starts = (i32 *)arena_alloc(M.A, sizeof(i32) * (size_t)(loops ? 2 * loops : 1));
    for (i32 l = 0; l < loops; l++) {
      starts[l] = in->loop_i[2 * (i64)(first_loop + l)];
      starts[loops + l] = in->loop_i[2 * (i64)(first_loop + l) + 1];
    }
    f->loop_start = starts;
    f->loop_size = starts + loops;
    f->pole_count = ii[7];
    f->poles = (face_pole *)arena_alloc(M.A, sizeof(face_pole) * (size_t)(ii[7] ? ii[7] : 1));
    for (i32 p = 0; p < ii[7]; p++) {
      const double *pd = in->pole_d + (i64)(ii[6] + p) * POLE_D;
      f->poles[p].axis = (i32)pd[0];
      f->poles[p].at = pd[1];
      f->poles[p].lo = pd[2];
      f->poles[p].hi = pd[3];
      memcpy(f->poles[p].point, pd + 4, sizeof(double) * 3);
    }
  }

  out->faces = faces;
  out->pcurves = pcurves;
  out->curves = curves;
}

static void mesh_rest(run *r, arenas M, const host *hp, const tess_input *in, tess_result *res, face *faces,
                      pcurve *pcurves, curve *curves, i32 F, i32 E) {
  host h = *hp;
  /* The component's scale: a box from each pcurve's ends and middle. */
  double low[3] = {INFINITY, INFINITY, INFINITY}, high[3] = {-INFINITY, -INFINITY, -INFINITY};
#define GROW(p_)                                \
  for (int d_ = 0; d_ < 3; d_++) {              \
    if ((p_)[d_] < low[d_]) low[d_] = (p_)[d_]; \
    if ((p_)[d_] > high[d_]) high[d_] = (p_)[d_]; \
  }
  for (i32 k = 0; k < F; k++) {
    face *f = &faces[k];
    for (i32 l = 0; l < f->loop_count; l++) {
      for (i32 j = 0; j < f->loop_size[l]; j++) {
        const pcurve *pc = &pcurves[f->loop_start[l] + j];
        double ts[3] = {pc->first, (pc->first + pc->last) / 2, pc->last};
        for (int t = 0; t < 3; t++) {
          double uv[2], p[3];
          pcurve_value(r, &h, pc, ts[t], uv);
          surface_value(r, &h, &f->s, uv[0], uv[1], p);
          GROW(p);
        }
      }
    }
  }
  if (!(low[0] <= high[0])) {
    for (i32 k = 0; k < E; k++) {
      if (curves[k].kind == C_NONE) continue;
      double ts[3] = {curves[k].first, (curves[k].first + curves[k].last) / 2, curves[k].last};
      for (int t = 0; t < 3; t++) {
        double p[3];
        curve_value(r, &h, &curves[k], ts[t], p);
        GROW(p);
      }
    }
  }
#undef GROW
  double dx = high[0] - low[0], dy = high[1] - low[1], dz = high[2] - low[2];
  double extent = sqrt(dx * dx + dy * dy + dz * dz);
  double scale = isfinite(extent) ? py_max(extent, SCALE_FLOOR) : SCALE_FLOOR;
  res->scale = scale;

  /* One polyline per model edge, sampled from its exact curve at the chord tolerance. */
  shared *edges = (shared *)arena_zalloc(M.A, sizeof(shared) * (size_t)(E + 1));
  for (i32 k = 0; k < E; k++) {
    if (curves[k].kind == C_NONE) continue;
    arena_mark mark = arena_save(M.T);
    sample_shared_edge(r, M.A, M.T, &h, &curves[k], in->chord * scale, &edges[k]);
    arena_reset(M.T, mark);
  }
  /* Two points closer than this are one point of the model evaluated twice. */
  double weld_tolerance = scale * ldexp(1.0, -21);
  corner_welder welder;
  weld_corners(r, M.A, edges, E, weld_tolerance, &welder);
  for (i32 k = 0; k < F; k++) {
    for (i32 p = 0; p < faces[k].pole_count; p++) {
      i32 corner = canonical_corner(&welder, faces[k].poles[p].point);
      memcpy(faces[k].poles[p].point, welder.corners.v + 3 * corner, sizeof(double) * 3);
    }
  }

  raw_face **raws = (raw_face **)arena_zalloc(M.A, sizeof(raw_face *) * (size_t)(F + 1));
  res->face_state = (i32 *)out_alloc(r, sizeof(i32) * (size_t)(F ? F : 1));
  for (i32 k = 0; k < F; k++) {
    res->face_state[k] = FACE_NONE;
    arena_mark face_mark = arena_save(M.F), temp_mark = arena_save(M.T);
    raw_face *raw = (raw_face *)arena_zalloc(M.A, sizeof(raw_face));
    int made = try_face(r, M, &h, &faces[k], pcurves, scale, in, edges, E, raw);
    if (made > 0) raws[k] = raw;
    else if (made < 0) res->face_state[k] = FACE_UNRESOLVED;
    arena_reset(M.F, face_mark);
    arena_reset(M.T, temp_mark);
  }
  conform_boundaries(r, M, &h, faces, raws, F, edges, E, weld_tolerance);
  res->face_vertices = (i64 *)out_alloc(r, sizeof(i64) * (size_t)(F ? F : 1));
  res->face_triangles = (i64 *)out_alloc(r, sizeof(i64) * (size_t)(F ? F : 1));
  i64 vertex_total = 0, triangle_total = 0;
  for (i32 k = 0; k < F; k++) {
    res->face_vertices[k] = res->face_triangles[k] = 0;
    raw_face *raw = raws[k];
    if (!raw) continue;
    arena_mark temp_mark = arena_save(M.T);
    refine_post_conform(r, M.A, M.T, &h, &faces[k], raw, in->chord);
    if (faces[k].pole_count) snap_poles(M.T, &faces[k], raw, weld_tolerance);
    drop_float32_collapsed(M.A, &raw->tri, &raw->xyz);
    arena_reset(M.T, temp_mark);
    res->face_state[k] = FACE_MESHED;
    res->face_triangles[k] = raw->tri.n / 3;
    res->face_vertices[k] = raw->tri.n ? raw->xyz.n / 3 : 0;
    vertex_total += res->face_vertices[k];
    triangle_total += res->face_triangles[k];
  }
  res->positions = (float *)out_alloc(r, sizeof(float) * (size_t)(3 * vertex_total));
  res->normals = (float *)out_alloc(r, sizeof(float) * (size_t)(3 * vertex_total));
  res->triangles = (u32 *)out_alloc(r, sizeof(u32) * (size_t)(3 * triangle_total));
  res->vertex_total = vertex_total;
  res->triangle_total = triangle_total;
  i64 vat = 0, tat = 0;
  for (i32 k = 0; k < F; k++) {
    raw_face *raw = raws[k];
    if (!raw || !raw->tri.n) continue;
    i64 vertices = raw->xyz.n / 3;
    for (i64 i = 0; i < 3 * vertices; i++) {
      res->positions[3 * vat + i] = (float)raw->xyz.v[i];
      double n = raw->nrm.v[i];
      res->normals[3 * vat + i] = (float)(faces[k].reversed ? -n : n);
    }
    for (i64 t = 0; t < raw->tri.n; t += 3) {
      res->triangles[3 * tat + t] = (u32)raw->tri.v[t];
      res->triangles[3 * tat + t + 1] = (u32)raw->tri.v[faces[k].reversed ? t + 2 : t + 1];
      res->triangles[3 * tat + t + 2] = (u32)raw->tri.v[faces[k].reversed ? t + 1 : t + 2];
    }
    vat += vertices;
    tat += raw->tri.n / 3;
  }
  res->edge_points = (i64 *)out_alloc(r, sizeof(i64) * (size_t)(E ? E : 1));
  i64 point_total = 0;
  for (i32 k = 0; k < E; k++) {
    res->edge_points[k] = edges[k].present ? edges[k].points.n / 3 : 0;
    point_total += res->edge_points[k];
  }
  res->points = (double *)out_alloc(r, sizeof(double) * (size_t)(3 * point_total));
  res->point_total = point_total;
  i64 pat = 0;
  for (i32 k = 0; k < E; k++) {
    if (!edges[k].present) continue;
    memcpy(res->points + 3 * pat, edges[k].points.v, sizeof(double) * (size_t)edges[k].points.n);
    pat += edges[k].points.n / 3;
  }
}

/* The component meshed under the run's outermost guard: 0 where it failed, its code in `error`. */
static int run_component(run *R, const tess_input *in, tess_result *res) {
  guard top;
  top.outer = NULL;
  top.code = E_NONE;
  R->guard = &top;
  if (setjmp(top.jump) != 0) {
    res->error = top.code;
    return 0;
  }
  mesh_component(R, in, res);
  return 1;
}

/* The geometry evaluated as the library evaluates it, for a test against OCCT: `what` names a
 * face's surface point (ASK_SURFACE) or normal (ASK_NORMAL), a pcurve's point (ASK_PCURVE) or an
 * edge's (ASK_CURVE), `index` the face, pcurve or edge, and each of the `count` parameter pairs
 * gives a point. 0 where it worked. */
static i32 probe_geometry(run *R, const tess_input *in, i32 what, i32 index, i32 count, const double *params,
                          double *out) {
  guard top;
  top.outer = NULL;
  top.code = E_NONE;
  R->guard = &top;
  if (setjmp(top.jump) != 0) return top.code ? top.code : E_ERROR;
  arenas M = {&R->A, &R->F, &R->T};
  host h = {in->callback, in->user};
  geometry geo;
  read_geometry(R, M, in, &geo);
  for (i32 k = 0; k < count; k++) {
    double a = params[2 * k], b = params[2 * k + 1];
    double *o = out + 3 * (i64)k;
    if (what == ASK_SURFACE) surface_value(R, &h, &geo.faces[index].s, a, b, o);
    else if (what == ASK_NORMAL) surface_normal(R, &h, &geo.faces[index].s, a, b, geo.faces[index].uv, o);
    else if (what == ASK_PCURVE) pcurve_value(R, &h, &geo.pcurves[index], a, o);
    else curve_value(R, &h, &geo.curves[index], a, o);
  }
  return 0;
}

/* A face's grid and its pinning, for a test of them: the component's face `index` with its trim
 * loops given rather than sampled -- `loop_uv` their points (u, v), `loop_sizes` each loop's
 * count; each point's segment's model edge `loop_ords` and its fractions along the edge's
 * polyline `loop_fractions` (f0, f1; NaN where it runs along no shared edge) -- triangulated at
 * `chord_limit` and `angle_limit` and its vertices pinned to the component's edges, each sampled
 * at the input's chord as an absolute tolerance. Each vertex's (u, v) goes to `uv` and the first
 * two edges it is pinned to to `ords` (0 for none), each triangle to `triangles`, while they fit
 * `capacity` vertices and triangles; `counts` gets how many there are. 0 where it worked. */
static i32 pinned_face(run *R, const tess_input *in, i32 index, const double *loop_uv, const i32 *loop_sizes,
                       i32 loop_count, const i32 *loop_ords, const double *loop_fractions, double chord_limit,
                       double angle_limit, double *uv, i32 *ords, i32 *triangles, i64 capacity, i64 *counts) {
  guard top;
  top.outer = NULL;
  top.code = E_NONE;
  R->guard = &top;
  if (setjmp(top.jump) != 0) return top.code ? top.code : E_ERROR;
  arenas M = {&R->A, &R->F, &R->T};
  host h = {in->callback, in->user};
  geometry geo;
  read_geometry(R, M, in, &geo);
  if (index < 0 || index >= in->face_count || loop_count < 1) fail(R, E_ERROR);
  shared *edges = (shared *)arena_zalloc(M.A, sizeof(shared) * (size_t)(in->edge_count + 1));
  for (i32 k = 0; k < in->edge_count; k++)
    if (geo.curves[k].kind != C_NONE) sample_shared_edge(R, M.A, M.T, &h, &geo.curves[k], in->chord, &edges[k]);
  ring *loops = (ring *)arena_zalloc(M.A, sizeof(ring) * (size_t)loop_count);
  i64 at = 0;
  for (i32 l = 0; l < loop_count; l++) {
    for (i32 i = 0; i < loop_sizes[l]; i++, at++) {
      PUSH2(M.A, loops[l].uv, loop_uv[2 * at], loop_uv[2 * at + 1]);
      PUSH(M.A, loops[l].ords, loop_ords[at]);
      meta m = {0, 0, 0.0, 0.0};
      if (loop_fractions[2 * at] == loop_fractions[2 * at]) {
        m.has = 1;
        m.ord = loop_ords[at];
        m.f0 = loop_fractions[2 * at];
        m.f1 = loop_fractions[2 * at + 1];
      }
      PUSH(M.A, loops[l].metas, m);
    }
  }
  const face *f = &geo.faces[index];
  grid *g = grid_triangulate(R, M.A, M.F, M.T, &h, f, loops, loop_count, chord_limit, angle_limit, 1);
  if (!g) fail(R, E_UNRESOLVED);
  i64 count = g->uv.n / 2, made = g->tri.n / 3;
  vd xyz = {NULL, 0, 0};
  RESERVE(M.A, xyz, 3 * count);
  for (i64 i = 0; i < count; i++) {
    double p[3];
    surface_value(R, &h, &f->s, g->uv.v[2 * i], g->uv.v[2 * i + 1], p);
    PUSH3(M.A, xyz, p[0], p[1], p[2]);
  }
  bmap boundary;
  memset(&boundary, 0, sizeof boundary);
  pin_boundary(R, M.A, M.T, &h, g, &g->uv, &xyz, &boundary, edges, in->edge_count);
  counts[0] = count;
  counts[1] = made;
  if (count > capacity || made > capacity) return 0;
  for (i64 i = 0; i < count; i++) {
    const labels *l = bmap_get(&boundary, i);
    uv[2 * i] = g->uv.v[2 * i];
    uv[2 * i + 1] = g->uv.v[2 * i + 1];
    ords[2 * i] = l && l->n > 0 ? l->v[0].ord : 0;
    ords[2 * i + 1] = l && l->n > 1 ? l->v[1].ord : 0;
  }
  memcpy(triangles, g->tri.v, sizeof(i32) * (size_t)g->tri.n);
  return 0;
}

EXPORT i32 cadgen_tessellate_pinned(const tess_input *in, i32 index, const double *loop_uv, const i32 *loop_sizes,
                                    i32 loop_count, const i32 *loop_ords, const double *loop_fractions,
                                    double chord_limit, double angle_limit, double *uv, i32 *ords, i32 *triangles,
                                    i64 capacity, i64 *counts) {
  run R;
  run_init(&R);
  i32 status = pinned_face(&R, in, index, loop_uv, loop_sizes, loop_count, loop_ords, loop_fractions, chord_limit,
                           angle_limit, uv, ords, triangles, capacity, counts);
  arena_free(&R.A);
  arena_free(&R.F);
  arena_free(&R.T);
  return status;
}

/* Earcut as the library triangulates with it, for a test against three.js's: a contour then its
 * holes, `count` points (x, y) in all, `starts` each hole's first point; the triangles' point
 * indices go to `out`, room for `capacity`. The number of indices, or -1. */
static i32 earcut_points(run *R, const double *points, i32 count, const i32 *starts, i32 hole_count, i32 *out,
                         i32 capacity) {
  guard top;
  top.outer = NULL;
  top.code = E_NONE;
  R->guard = &top;
  if (setjmp(top.jump) != 0) return -1;
  poly contour = {(double *)points, hole_count ? starts[0] : count};
  poly *holes = (poly *)arena_alloc(&R->T, sizeof(poly) * (size_t)(hole_count ? hole_count : 1));
  for (i32 k = 0; k < hole_count; k++) {
    holes[k].p = (double *)points + 2 * (i64)starts[k];
    holes[k].n = (k + 1 < hole_count ? starts[k + 1] : count) - starts[k];
  }
  vi made = {NULL, 0, 0};
  triangulate_shape(R, &R->T, &contour, holes, hole_count, &made);
  if (made.n > capacity) return -1;
  memcpy(out, made.v, sizeof(i32) * (size_t)made.n);
  return (i32)made.n;
}

EXPORT i32 cadgen_tessellate_earcut(const double *points, i32 count, const i32 *starts, i32 hole_count, i32 *out,
                                    i32 capacity) {
  run R;
  run_init(&R);
  i32 made = earcut_points(&R, points, count, starts, hole_count, out, capacity);
  arena_free(&R.A);
  arena_free(&R.F);
  arena_free(&R.T);
  return made;
}

/* The geometry evaluated as the library evaluates it, for a test against OCCT: `what` names a
 * face's surface point (ASK_SURFACE) or normal (ASK_NORMAL), a pcurve's point (ASK_PCURVE) or an
 * edge's (ASK_CURVE), `index` the face, pcurve or edge, and each of the `count` parameter pairs
 * gives a point, three numbers. 0 where it worked. */
EXPORT i32 cadgen_tessellate_probe(const tess_input *in, i32 what, i32 index, i32 count, const double *params,
                                   double *out) {
  run R;
  run_init(&R);
  i32 status = probe_geometry(&R, in, what, index, count, params, out);
  arena_free(&R.A);
  arena_free(&R.F);
  arena_free(&R.T);
  return status;
}

EXPORT void cadgen_tessellate_free(tess_result *res) {
  if (!res) return;
  free(res->face_state);
  free(res->face_vertices);
  free(res->face_triangles);
  free(res->positions);
  free(res->normals);
  free(res->triangles);
  free(res->edge_points);
  free(res->points);
  free(res);
}

EXPORT i32 cadgen_tessellate_abi(void) { return TESS_ABI; }

EXPORT tess_result *cadgen_tessellate(const tess_input *in) {
  tess_result *res = (tess_result *)calloc(1, sizeof(tess_result));
  if (!res) return NULL;
  res->abi = TESS_ABI;
  res->face_count = in->face_count;
  res->edge_count = in->edge_count;
  run R;
  run_init(&R);
  if (!run_component(&R, in, res)) {
    int code = res->error;
    snprintf(res->message, sizeof res->message, "%s",
             code == E_MEMORY ? "out of memory" : code == E_CALLBACK ? "an evaluation the host was asked for failed"
                                                                     : "the component's geometry could not be meshed");
    free(res->face_state);
    free(res->face_vertices);
    free(res->face_triangles);
    free(res->positions);
    free(res->normals);
    free(res->triangles);
    free(res->edge_points);
    free(res->points);
    res->face_state = NULL;
    res->face_vertices = res->face_triangles = NULL;
    res->positions = res->normals = NULL;
    res->triangles = NULL;
    res->edge_points = NULL;
    res->points = NULL;
  }
  arena_free(&R.A);
  arena_free(&R.F);
  arena_free(&R.T);
  return res;
}
