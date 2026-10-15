/*
 * Part of tessellate.c's unity build: one component's mesh verified and completed, and the
 * library's interface.
 *
 * The host (cadgen._internal.tessellate) meshes the component with OCCT, reads the component's
 * geometry and OCCT's triangulation into flat arrays (`tess_input`) and calls `cadgen_tessellate`.
 * Here every face OCCT meshed is verified against the exact geometry and repaired where it fails
 * (verify.c), the faces OCCT refused or the host kept from it (the cost guard) are meshed by the
 * library's own tessellator over the same shared edge polylines (grid.c, refine.c), every face is
 * made to meet its neighbours (conform.c), the whole is proven closed and covering, and what comes
 * back (`tess_result`) is each face's float32 positions, normals and triangles with its state --
 * verified, left out and to be named, drawn off its surface and to be named -- each model edge's
 * polyline, and what was found and done per face. A face its geometry defeats comes out with no
 * mesh; anything else that fails fails the component, with the result's `error` set.
 */

#define TESS_ABI 2

/* Column counts of the input tables. */
enum {
  FACE_I = 8, FACE_D = 22, PCURVE_I = 12, PCURVE_D = 2, EDGE_I = 11, EDGE_D = 16, BSURF_I = 10, BSURF_D = 4,
  POLE_D = 7, SWEPT_I = 2, SWEPT_D = 6, MESH_I = 5, REPORT_N = 10
};

/* Where a face's triangles come from, as the host says (`mesh_i` column 0). */
enum { MESH_NONE = 0, MESH_OCCT = 1, MESH_GUARDED = 2 };

typedef struct {
  i32 face_count, edge_count, pcurve_count, bsurface_count, swept_count, basis_count;
  /* The chord tolerance relative to the component's extent and the angle, which the library's
   * own faces are meshed to; the absolute deflection OCCT meshed at, which every face is verified
   * to; a sample past `repair_at` deflections off its surface is repaired, a face left past
   * `verified_at` is unverified. */
  double chord, angle, deflection, repair_at, verified_at;
  /* face: ord, reversed, surface kind, its row in the B-spline or swept surface table, first
   * loop, loops, first pole, poles */
  const i32 *face_i;
  /* face: u0, u1, v0, v1, origin, x, y and z directions, radius, semi-angle, major, minor, the
   * surface's u and v periods (0 for none) */
  const double *face_d;
  /* face: mesh source (MESH_*), first vertex, vertices, first triangle, triangles -- OCCT's mesh
   * of the face in the mesh arrays below */
  const i32 *mesh_i;
  const double *mesh_xyz, *mesh_nrm, *mesh_uv; /* OCCT's vertices: located positions, outward
                                                  normals, the face's own parameters */
  const i32 *mesh_tri;                          /* OCCT's triangles, wound outward, local indices */
  /* loop: first pcurve, pcurves */
  const i32 *loop_i;
  /* pcurve: edge ordinal, reversed, by the host, poles, degree, rational, knots at, poles at,
   * first knot node, knot nodes, first knot uv, knot uvs -- the knots of the edge's fixed polyline
   * as this face's OCCT mesh vertices (`knot_nodes`) and in this face's parameters (`knot_uv`) */
  const i32 *pcurve_i;
  /* pcurve: first, last */
  const double *pcurve_d;
  const i32 *knot_nodes;
  const double *knot_uv;
  /* edge: kind (0 for no curve), by the host, poles (-1 for none), degree, B-spline poles,
   * rational, knots at, poles at, periodic, first polyline point, polyline points -- OCCT's
   * discretization of the edge where a face OCCT meshed lies along it (`edge_poly_*`) */
  const i32 *edge_i;
  /* edge: first, last, origin, x, y, direction, first radius, second radius */
  const double *edge_d;
  const double *edge_poly_xyz, *edge_poly_t;
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

/* A face's state in the result: no triangles; verified; too narrow to resolve (drawn as nothing,
 * not named); its triangles taken away because no mesh of it could be brought right (named);
 * drawn, though still off its surface past `verified_at` (named). */
enum { FACE_NONE = 0, FACE_MESHED = 1, FACE_UNRESOLVED = 2, FACE_UNMESHED = 3, FACE_UNVERIFIED = 4 };

/* Who made a face's triangles in the result. */
enum { MADE_BY_NONE = 0, MADE_BY_OCCT = 1, MADE_BY_LIBRARY = 2 };

typedef struct {
  i32 abi;
  i32 error;
  char message[160];
  double scale;
  i32 face_count, edge_count;
  i32 *face_state;     /* FACE_* */
  i32 *face_source;    /* MADE_BY_* */
  double *face_worst;  /* per face: the farthest sample off its surface, in deflections */
  i64 *face_report;    /* per face, REPORT_N numbers: rounds, splits, flips, folded, holes filled,
                          hole edges left, open edges, free edges, library fallbacks, area ok */
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

/* One face's raw mesh by the library's tessellator: 1 made, 0 none (no mesh, or a face its geometry
 * defeats, which is left out), -1 a face too narrow to resolve. Running out of memory fails the
 * component. */
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

static void read_geometry(run *r, arenas M, const tess_input *in, geometry *out) {
  i32 F = in->face_count, E = in->edge_count;
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
    p->knot_nodes = ii[9] > 0 && in->knot_nodes ? in->knot_nodes + ii[8] : NULL;
    p->knot_uv = ii[11] > 0 && in->knot_uv ? in->knot_uv + 2 * (i64)ii[10] : NULL;
    p->knot_count = ii[11] > 0 ? ii[11] : ii[9];
    if (ii[9] > 0 && ii[11] > 0 && ii[9] != ii[11]) fail(r, E_ERROR);
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
    f->period_u = dd[20];
    f->period_v = dd[21];
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

/* On a periodic surface OCCT's triangles can straddle the period: a seam node at u = P beside
 * nodes at u ~ 0, a sliver on the surface that is a period wide in its parameters, whose
 * parametric samples, midpoints, winding and hole loops would all be read a period away. Each
 * corner is brought within half a period of the triangle's first, and the triangle then into the
 * face's parameter box, on a duplicate of its vertex (the same point, normal and labels) where
 * that changes the vertex's parameters. */
static void unwrap_periods(arenas M, const face *f, raw_face *raw) {
  double period[2] = {f->period_u > 0.0 ? f->period_u : 0.0, f->period_v > 0.0 ? f->period_v : 0.0};
  if (period[0] == 0.0 && period[1] == 0.0) return;
  i64 vn = raw->uv.n / 2;
  arena_mark mark = arena_save(M.T);
  i32 *head = (i32 *)arena_alloc(M.T, sizeof(i32) * (size_t)(vn ? vn : 1));
  for (i64 i = 0; i < vn; i++) head[i] = -1;
  vi dup_code = {NULL, 0, 0}, dup_index = {NULL, 0, 0}, dup_next = {NULL, 0, 0};
  for (i64 t = 0; t < raw->tri.n / 3; t++) {
    i32 corners[3] = {raw->tri.v[3 * t], raw->tri.v[3 * t + 1], raw->tri.v[3 * t + 2]};
    /* A triangle two of whose corners are one point spans the parameters a pole collapses: it is
     * the face's parameter cover there, not a sliver, and stays as OCCT drew it. */
    const double *p0 = raw->xyz.v + 3 * corners[0], *p1 = raw->xyz.v + 3 * corners[1], *p2 = raw->xyz.v + 3 * corners[2];
    if (same_point(p0, p1) || same_point(p1, p2) || same_point(p2, p0)) continue;
    double uv[3][2];
    for (int c = 0; c < 3; c++) {
      uv[c][0] = raw->uv.v[2 * corners[c]];
      uv[c][1] = raw->uv.v[2 * corners[c] + 1];
    }
    long shift[3][2] = {{0, 0}, {0, 0}, {0, 0}};
    int wrapped = 0;
    for (int d = 0; d < 2; d++) {
      if (period[d] == 0.0) continue;
      for (int c = 1; c < 3; c++) {
        double turns = (uv[c][d] - uv[0][d]) / period[d];
        if (!(fabs(turns) < 1e6)) continue;
        shift[c][d] = lround(turns);
        if (shift[c][d]) wrapped = 1;
      }
      if (!wrapped) continue;
      /* The unwrapped triangle into the face's box, where its centroid left it. */
      double centroid = 0.0;
      for (int c = 0; c < 3; c++) centroid += (uv[c][d] - shift[c][d] * period[d]) / 3;
      double middle = (f->uv[2 * d] + f->uv[2 * d + 1]) / 2;
      if (isfinite(middle) && fabs(middle - centroid) > period[d] / 2) {
        long whole = lround((centroid - middle) / period[d]);
        for (int c = 0; c < 3; c++) shift[c][d] += whole;
      }
    }
    if (!wrapped) continue;
    for (int c = 0; c < 3; c++) {
      if (!shift[c][0] && !shift[c][1]) continue;
      i32 v = corners[c];
      i32 code = (i32)(shift[c][0] * 4096 + shift[c][1]);
      i32 found = -1;
      for (i32 d = head[v]; d >= 0 && found < 0; d = dup_next.v[d])
        if (dup_code.v[d] == code) found = dup_index.v[d];
      if (found < 0) {
        found = (i32)(raw->uv.n / 2);
        double u = uv[c][0] - shift[c][0] * period[0], w = uv[c][1] - shift[c][1] * period[1];
        double p[3] = {raw->xyz.v[3 * v], raw->xyz.v[3 * v + 1], raw->xyz.v[3 * v + 2]};
        double n[3] = {raw->nrm.v[3 * v], raw->nrm.v[3 * v + 1], raw->nrm.v[3 * v + 2]};
        PUSH2(M.A, raw->uv, u, w);
        PUSH3(M.A, raw->xyz, p[0], p[1], p[2]);
        PUSH3(M.A, raw->nrm, n[0], n[1], n[2]);
        PUSH(M.A, raw->minted, raw->minted.v[v]);
        const labels *have = bmap_get(&raw->boundary, v);
        if (have) {
          labels copy = {NULL, 0, 0};
          for (i64 q = 0; q < have->n; q++) PUSH(M.A, copy, have->v[q]);
          bmap_set(M.A, &raw->boundary, found, copy);
        }
        PUSH(M.T, dup_code, code);
        PUSH(M.T, dup_index, found);
        PUSH(M.T, dup_next, head[v]);
        head[v] = (i32)(dup_code.n - 1);
      }
      raw->tri.v[3 * t + c] = found;
    }
  }
  arena_reset(M.T, mark);
}

/* The face OCCT meshed, as a raw face: its vertices (positions in double, normals and winding
 * turned to the surface's own orientation, which the output turns back), and each polygon-on-
 * triangulation node labelled with the model edge and the fraction of that edge's polyline it is
 * -- by its place in the polygon where the polygon is the polyline, else by the nearest point of
 * the polyline, which is where conformity then puts it. */
static raw_face *load_occt_face(run *r, arenas M, const tess_input *in, i32 k, const face *f, const pcurve *pcurves,
                                const shared *edges, i32 E) {
  const i32 *mi = in->mesh_i + (i64)k * MESH_I;
  i64 v0 = mi[1], vn = mi[2], t0 = mi[3], tn = mi[4];
  if (tn <= 0 || vn <= 0) return NULL;
  raw_face *raw = (raw_face *)arena_zalloc(M.A, sizeof(raw_face));
  raw->source = SOURCE_OCCT;
  RESERVE(M.A, raw->uv, 2 * vn);
  RESERVE(M.A, raw->xyz, 3 * vn);
  RESERVE(M.A, raw->nrm, 3 * vn);
  RESERVE(M.A, raw->minted, vn);
  double flip = f->reversed ? -1.0 : 1.0;
  for (i64 i = 0; i < vn; i++) {
    const double *p = in->mesh_xyz + 3 * (v0 + i), *n = in->mesh_nrm + 3 * (v0 + i), *uv = in->mesh_uv + 2 * (v0 + i);
    PUSH2(M.A, raw->uv, uv[0], uv[1]);
    PUSH3(M.A, raw->xyz, p[0], p[1], p[2]);
    PUSH3(M.A, raw->nrm, flip * n[0], flip * n[1], flip * n[2]);
    PUSH(M.A, raw->minted, 0);
  }
  RESERVE(M.A, raw->tri, 3 * tn);
  for (i64 t = 0; t < tn; t++) {
    const i32 *tri = in->mesh_tri + 3 * (t0 + t);
    if (tri[0] < 0 || tri[0] >= vn || tri[1] < 0 || tri[1] >= vn || tri[2] < 0 || tri[2] >= vn) fail(r, E_ERROR);
    if (f->reversed) PUSH3(M.A, raw->tri, tri[0], tri[2], tri[1]);
    else PUSH3(M.A, raw->tri, tri[0], tri[1], tri[2]);
  }
  bmap_reserve(M.A, &raw->boundary, vn);
  for (i32 l = 0; l < f->loop_count; l++) {
    for (i32 j = 0; j < f->loop_size[l]; j++) {
      const pcurve *pc = &pcurves[f->loop_start[l] + j];
      if (!pc->knot_nodes || pc->knot_count < 1 || pc->edge_ord <= 0 || pc->edge_ord > E) continue;
      const shared *sh = &edges[pc->edge_ord - 1];
      if (!sh->present) continue;
      int by_place = sh->fixed && sh->points.n / 3 == pc->knot_count;
      for (i32 i = 0; i < pc->knot_count && by_place; i++) {
        i32 node = pc->knot_nodes[i];
        if (node < 0 || node >= vn) fail(r, E_ERROR);
        if (dist3(raw->xyz.v + 3 * node, sh->points.v + 3 * i) > 0.0) by_place = 0;
      }
      for (i32 i = 0; i < pc->knot_count; i++) {
        i32 node = pc->knot_nodes[i];
        if (node < 0 || node >= vn) fail(r, E_ERROR);
        double fraction = by_place ? sh->fractions.v[i] : edge_fraction_of(sh, raw->xyz.v + 3 * node, NULL);
        label made = {pc->edge_ord, fraction};
        labels *have = bmap_get(&raw->boundary, node);
        if (have) {
          int present = 0;
          for (i64 q = 0; q < have->n && !present; q++)
            if (have->v[q].ord == made.ord && have->v[q].f == made.f) present = 1;
          if (!present) PUSH(M.A, *have, made);
        } else {
          labels l = {NULL, 0, 0};
          PUSH(M.A, l, made);
          bmap_set(M.A, &raw->boundary, node, l);
        }
      }
    }
  }
  unwrap_periods(M, f, raw);
  return raw;
}

/* How many pcurves lie on each model edge: one on a free edge, two on a shared edge or a seam. */
static i32 *edge_sides(arena *A, const pcurve *pcurves, i32 pcurve_count, i32 E) {
  i32 *sides = (i32 *)arena_zalloc(A, sizeof(i32) * (size_t)(E ? E : 1));
  for (i32 k = 0; k < pcurve_count; k++)
    if (pcurves[k].edge_ord > 0 && pcurves[k].edge_ord <= E) sides[pcurves[k].edge_ord - 1]++;
  return sides;
}

/* How many pcurves on each model edge belong to a face with triangles. */
static i32 *meshed_sides(arena *T, const face *faces, raw_face **raws, i32 F, const pcurve *pcurves, i32 E) {
  i32 *sides = (i32 *)arena_zalloc(T, sizeof(i32) * (size_t)(E ? E : 1));
  for (i32 k = 0; k < F; k++) {
    if (!raws[k] || !raws[k]->tri.n) continue;
    for (i32 l = 0; l < faces[k].loop_count; l++)
      for (i32 j = 0; j < faces[k].loop_size[l]; j++) {
        i32 ord = pcurves[faces[k].loop_start[l] + j].edge_ord;
        if (ord > 0 && ord <= E) sides[ord - 1]++;
      }
  }
  return sides;
}

/* The component's odd edges attributed: per face, the ones on a model edge no neighbour explains
 * (cracks, their model edges listed in `cracked[k]`), the ones a free edge or an unmeshed
 * neighbour explains, and the ones inside the face (holes), which `holes` gets for `fill_holes`. */
static void classify_open_edges(run *r, arenas M, const face *faces, raw_face **raws, i32 F, const pcurve *pcurves,
                                i32 E, const i32 *sides, face_report *reports, vopen *holes, i64 *hole_start,
                                i64 *hole_count, vi *cracked) {
  vopen open = {NULL, 0, 0};
  odd_edges(r, M, raws, F, &open);
  i32 *meshed = meshed_sides(M.T, faces, raws, F, pcurves, E);
  for (i32 k = 0; k < F; k++) {
    hole_start[k] = holes->n;
    hole_count[k] = 0;
    cracked[k].n = 0;
  }
  /* Grouped by face, in the order the faces come: holes are filled face by face. A face with no
   * triangles keeps the counts that took them away. */
  for (i32 k = 0; k < F; k++) {
    if (!raws[k]) continue;
    reports[k].open_edges = reports[k].free_edges = 0;
    hole_start[k] = holes->n;
    for (i64 i = 0; i < open.n; i++) {
      const open_edge *o = &open.v[i];
      if (o->face != k) continue;
      i32 ord = common_edge(&raws[k]->boundary, o->p, o->q);
      if (ord > 0) {
        /* A free edge of the BREP, or a shared edge whose other face has no triangles. */
        if (sides[ord - 1] <= 1 || meshed[ord - 1] < sides[ord - 1]) reports[k].free_edges++;
        else {
          reports[k].open_edges++;
          PUSH(M.T, cracked[k], ord);
        }
      } else {
        if (getenv("CADGEN_TESS_DEBUG")) {
          const labels *lp = bmap_get(&raws[k]->boundary, o->p), *lq = bmap_get(&raws[k]->boundary, o->q);
          fprintf(stderr, "DEBUG hole face %d: v%d (%.9g,%.9g,%.9g) uv (%.6g,%.6g) labels", faces[k].ord, o->p,
                  raws[k]->xyz.v[3 * o->p], raws[k]->xyz.v[3 * o->p + 1], raws[k]->xyz.v[3 * o->p + 2],
                  raws[k]->uv.v[2 * o->p], raws[k]->uv.v[2 * o->p + 1]);
          for (i64 q = 0; lp && q < lp->n; q++) fprintf(stderr, " e%d/%.4g", lp->v[q].ord, lp->v[q].f);
          fprintf(stderr, " -- v%d (%.9g,%.9g,%.9g) uv (%.6g,%.6g) labels", o->q, raws[k]->xyz.v[3 * o->q],
                  raws[k]->xyz.v[3 * o->q + 1], raws[k]->xyz.v[3 * o->q + 2], raws[k]->uv.v[2 * o->q], raws[k]->uv.v[2 * o->q + 1]);
          for (i64 q = 0; lq && q < lq->n; q++) fprintf(stderr, " e%d/%.4g", lq->v[q].ord, lq->v[q].f);
          fprintf(stderr, "\n");
        }
        PUSH(M.T, *holes, *o);
        hole_count[k]++;
      }
    }
  }
}

/* TEMPORARY: a face's raw mesh on stderr, when CADGEN_TESS_DUMP names its ordinal. */
static void debug_dump(const char *stage, const face *f, const raw_face *raw) {
  const char *want = getenv("CADGEN_TESS_DUMP");
  if (!want || atoi(want) != f->ord) return;
  fprintf(stderr, "DUMP %s face %d source %d: %lld vertices %lld triangles; box u [%.9g, %.9g] v [%.9g, %.9g]\n", stage, f->ord,
          raw->source, (long long)(raw->uv.n / 2), (long long)(raw->tri.n / 3), f->uv[0], f->uv[1], f->uv[2], f->uv[3]);
  for (i64 i = 0; i < raw->uv.n / 2; i++) {
    const labels *l = bmap_get(&raw->boundary, i);
    fprintf(stderr, "DUMP v%lld uv (%.9g, %.9g) xyz (%.9g, %.9g, %.9g) nrm (%.6g, %.6g, %.6g)", (long long)i, raw->uv.v[2 * i],
            raw->uv.v[2 * i + 1], raw->xyz.v[3 * i], raw->xyz.v[3 * i + 1], raw->xyz.v[3 * i + 2], raw->nrm.v[3 * i],
            raw->nrm.v[3 * i + 1], raw->nrm.v[3 * i + 2]);
    if (l)
      for (i64 q = 0; q < l->n; q++) fprintf(stderr, " [e%d f%.6g]", l->v[q].ord, l->v[q].f);
    fprintf(stderr, "\n");
  }
  for (i64 t = 0; t < raw->tri.n / 3; t++)
    fprintf(stderr, "DUMP t%lld %d %d %d\n", (long long)t, raw->tri.v[3 * t], raw->tri.v[3 * t + 1], raw->tri.v[3 * t + 2]);
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
  double deflection = in->deflection > 0 ? in->deflection : in->chord * scale;
  double repair_at = deflection * (in->repair_at > 0 ? in->repair_at : 1.0);
  double verified_at = deflection * (in->verified_at > 0 ? in->verified_at : 2.0);

  /* One polyline per model edge: OCCT's where it discretized the edge, else the exact curve
   * sampled at the chord tolerance. */
  shared *edges = (shared *)arena_zalloc(M.A, sizeof(shared) * (size_t)(E + 1));
  for (i32 k = 0; k < E; k++) {
    if (curves[k].kind == C_NONE) continue;
    const i32 *ii = in->edge_i + (i64)k * EDGE_I;
    if (ii[10] >= 2 && in->edge_poly_xyz && in->edge_poly_t) {
      fixed_shared_edge(M.A, &curves[k], in->edge_poly_xyz + 3 * (i64)ii[9], in->edge_poly_t + ii[9], ii[10], &edges[k]);
      continue;
    }
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
  i32 *sides = edge_sides(M.A, pcurves, in->pcurve_count, E);

  /* Every face's triangles: OCCT's where it meshed the face, the library's where OCCT refused it
   * or the host kept it from OCCT. */
  raw_face **raws = (raw_face **)arena_zalloc(M.A, sizeof(raw_face *) * (size_t)(F + 1));
  res->face_state = (i32 *)out_alloc(r, sizeof(i32) * (size_t)(F ? F : 1));
  res->face_source = (i32 *)out_alloc(r, sizeof(i32) * (size_t)(F ? F : 1));
  res->face_worst = (double *)out_alloc(r, sizeof(double) * (size_t)(F ? F : 1));
  res->face_report = (i64 *)out_alloc(r, sizeof(i64) * (size_t)(F ? F * REPORT_N : 1));
  face_report *reports = (face_report *)arena_zalloc(M.A, sizeof(face_report) * (size_t)(F + 1));
  i64 *fallbacks = (i64 *)arena_zalloc(M.A, sizeof(i64) * (size_t)(F + 1));
  for (i32 k = 0; k < F; k++) {
    res->face_state[k] = FACE_NONE;
    res->face_source[k] = MADE_BY_NONE;
    const i32 *mi = in->mesh_i + (i64)k * MESH_I;
    if (mi[0] == MESH_OCCT && mi[4] > 0) {
      raws[k] = load_occt_face(r, M, in, k, &faces[k], pcurves, edges, E);
      continue;
    }
    arena_mark face_mark = arena_save(M.F), temp_mark = arena_save(M.T);
    raw_face *raw = (raw_face *)arena_zalloc(M.A, sizeof(raw_face));
    int made = try_face(r, M, &h, &faces[k], pcurves, scale, in, edges, E, raw);
    if (made > 0) raws[k] = raw;
    else if (made < 0) res->face_state[k] = FACE_UNRESOLVED;
    arena_reset(M.F, face_mark);
    arena_reset(M.T, temp_mark);
  }

  /* Every vertex on a pole set to the pole's one point before anything welds by position: the
   * evaluations that put them there differ in their last bits. */
  for (i32 k = 0; k < F; k++)
    if (raws[k] && faces[k].pole_count) snap_poles(M.T, &faces[k], raws[k], weld_tolerance);
  for (i32 k = 0; k < F; k++)
    if (raws[k]) debug_dump("loaded", &faces[k], raws[k]);

  /* On its surface, no folded triangle: each face measured, OCCT's refined where they stray. */
  for (i32 k = 0; k < F; k++) {
    if (!raws[k]) continue;
    arena_mark face_mark = arena_save(M.F), temp_mark = arena_save(M.T);
    repair_surface(r, M, &h, &faces[k], raws[k], repair_at, raws[k]->source == SOURCE_OCCT, &reports[k]);
    unfold(r, M, &h, &faces[k], raws[k], &reports[k]);
    arena_reset(M.F, face_mark);
    arena_reset(M.T, temp_mark);
  }

  /* The faces on each model edge: two, or one on a free edge, or one twice on a seam. */
  i32 *edge_face = (i32 *)arena_alloc(M.A, sizeof(i32) * 2 * (size_t)(E + 1));
  for (i64 i = 0; i < 2 * (i64)(E + 1); i++) edge_face[i] = -1;
  for (i32 k = 0; k < F; k++)
    for (i32 l = 0; l < faces[k].loop_count; l++)
      for (i32 j = 0; j < faces[k].loop_size[l]; j++) {
        i32 ord = pcurves[faces[k].loop_start[l] + j].edge_ord;
        if (ord <= 0 || ord > E) continue;
        i32 *slots = edge_face + 2 * (ord - 1);
        if (slots[0] < 0) slots[0] = k;
        else if (slots[1] < 0) slots[1] = k;
      }

  /* Met across their shared edges, then proven closed and covering; a face that is neither has
   * one more chance, the library's tessellation over the same polylines, and is left out after. */
  for (int attempt = 0; attempt < 2; attempt++) {
    conform_boundaries(r, M, &h, faces, raws, F, edges, E, weld_tolerance);
    for (i32 k = 0; k < F; k++)
      if (raws[k]) debug_dump("conformed", &faces[k], raws[k]);
    arena_mark temp_mark = arena_save(M.T);
    vopen holes = {NULL, 0, 0};
    i64 *hole_start = (i64 *)arena_zalloc(M.T, sizeof(i64) * (size_t)(F + 1));
    i64 *hole_count = (i64 *)arena_zalloc(M.T, sizeof(i64) * (size_t)(F + 1));
    vi *cracked = (vi *)arena_zalloc(M.T, sizeof(vi) * (size_t)(F + 1));
    classify_open_edges(r, M, faces, raws, F, pcurves, E, sides, reports, &holes, hole_start, hole_count, cracked);
    int any_filled = 0;
    for (i32 k = 0; k < F; k++) {
      if (!raws[k] || !hole_count[k]) continue;
      i64 filled = reports[k].holes_filled;
      fill_holes(r, M, raws[k], holes.v + hole_start[k], hole_count[k], &reports[k]);
      if (reports[k].holes_filled > filled) {
        any_filled = 1;
        arena_mark face_mark = arena_save(M.F);
        repair_surface(r, M, &h, &faces[k], raws[k], repair_at, raws[k]->source == SOURCE_OCCT, &reports[k]);
        arena_reset(M.F, face_mark);
      }
    }
    if (any_filled) {
      /* The holes filled, the odd edges are counted again: what a fill closed is closed. */
      holes.n = 0;
      classify_open_edges(r, M, faces, raws, F, pcurves, E, sides, reports, &holes, hole_start, hole_count, cracked);
    }
    for (i32 k = 0; k < F; k++)
      if (raws[k]) reports[k].hole_edges_left = hole_count[k];
    for (i32 k = 0; k < F; k++)
      if (raws[k]) area_check(r, &h, &faces[k], pcurves, edges, E, raws[k], deflection, &reports[k]);
    /* What each face failed on its own -- holes, cover, folds -- as against cracks, which take
     * two faces: a face whose only fault is cracks against neighbours OCCT meshed that failed on
     * their own is not remade itself, since they are, and the cracks are read again after. */
    uint8_t *own = (uint8_t *)arena_zalloc(M.T, (size_t)(F + 1)), *remade = (uint8_t *)arena_zalloc(M.T, (size_t)(F + 1));
    for (i32 k = 0; k < F; k++) {
      if (!raws[k]) continue;
      own[k] = reports[k].hole_edges_left > 0 || !reports[k].area_ok || reports[k].folded > 0;
      remade[k] = own[k] && attempt == 0 && raws[k]->source == SOURCE_OCCT;
    }
    int retry = 0;
    for (i32 k = 0; k < F; k++) {
      if (!raws[k]) continue;
      int failed = own[k] || reports[k].open_edges > 0;
      if (!failed) continue;
      if (!own[k] && attempt == 0) {
        int explained = cracked[k].n > 0;
        for (i64 i = 0; i < cracked[k].n && explained; i++) {
          const i32 *slots = edge_face + 2 * (cracked[k].v[i] - 1);
          i32 other = slots[0] == k ? slots[1] : slots[0];
          explained = other >= 0 && other != k && remade[other];
        }
        if (explained) continue;
      }
      if (attempt == 0 && raws[k]->source == SOURCE_OCCT) {
        /* OCCT's mesh of the face taken away; the library's tessellation takes its place where
         * it can be made, and is proven like the rest on the second pass. */
        arena_mark face_mark = arena_save(M.F), temp2 = arena_save(M.T);
        raw_face *raw = (raw_face *)arena_zalloc(M.A, sizeof(raw_face));
        int made = try_face(r, M, &h, &faces[k], pcurves, scale, in, edges, E, raw);
        arena_reset(M.F, face_mark);
        arena_reset(M.T, temp2);
        fallbacks[k]++;
        if (made > 0) {
          raws[k] = raw;
          face_report fresh = {0};
          reports[k] = fresh;
          if (faces[k].pole_count) snap_poles(M.T, &faces[k], raw, weld_tolerance);
          debug_dump("fallback", &faces[k], raw);
          arena_mark fm = arena_save(M.F), tm = arena_save(M.T);
          repair_surface(r, M, &h, &faces[k], raws[k], repair_at, 0, &reports[k]);
          unfold(r, M, &h, &faces[k], raws[k], &reports[k]);
          arena_reset(M.F, fm);
          arena_reset(M.T, tm);
          retry = 1;
          continue;
        }
        raws[k] = NULL;
        res->face_state[k] = made < 0 ? FACE_UNRESOLVED : FACE_UNMESHED;
        retry = 1;
        continue;
      }
      /* The library's own face, or a fallback that is no better: left out and named. */
      raws[k] = NULL;
      res->face_state[k] = FACE_UNMESHED;
      retry = 1;
    }
    if (retry && attempt == 1) {
      /* The faces left out opened their neighbours' edges, which the final count explains. */
      vopen again = {NULL, 0, 0};
      i64 *hs = (i64 *)arena_zalloc(M.T, sizeof(i64) * (size_t)(F + 1));
      i64 *hc = (i64 *)arena_zalloc(M.T, sizeof(i64) * (size_t)(F + 1));
      vi *cr = (vi *)arena_zalloc(M.T, sizeof(vi) * (size_t)(F + 1));
      classify_open_edges(r, M, faces, raws, F, pcurves, E, sides, reports, &again, hs, hc, cr);
    }
    arena_reset(M.T, temp_mark);
    if (!retry) break;
  }

  res->face_vertices = (i64 *)out_alloc(r, sizeof(i64) * (size_t)(F ? F : 1));
  res->face_triangles = (i64 *)out_alloc(r, sizeof(i64) * (size_t)(F ? F : 1));
  i64 vertex_total = 0, triangle_total = 0;
  for (i32 k = 0; k < F; k++) {
    res->face_vertices[k] = res->face_triangles[k] = 0;
    res->face_worst[k] = 0.0;
    face_report *rep = &reports[k];
    raw_face *raw = raws[k];
    if (!raw) {
      i64 *row = res->face_report + (i64)k * REPORT_N;
      i64 values[REPORT_N] = {rep->rounds, rep->splits, rep->flips, rep->folded, rep->holes_filled,
                              rep->hole_edges_left, rep->open_edges, rep->free_edges, fallbacks[k], rep->area_ok};
      memcpy(row, values, sizeof values);
      continue;
    }
    arena_mark temp_mark = arena_save(M.T), face_mark = arena_save(M.F);
    refine_post_conform(r, M.A, M.T, &h, &faces[k], raw, in->chord);
    if (faces[k].pole_count) snap_poles(M.T, &faces[k], raw, weld_tolerance);
    drop_float32_collapsed(M.A, &raw->tri, &raw->xyz);
    /* The final word on the surface clause, over what is drawn. */
    face_report final = {0};
    repair_surface(r, M, &h, &faces[k], raw, repair_at, 0, &final);
    rep->worst = final.worst;
    arena_reset(M.T, temp_mark);
    arena_reset(M.F, face_mark);
    res->face_state[k] = rep->worst > verified_at ? FACE_UNVERIFIED : FACE_MESHED;
    res->face_source[k] = raw->source == SOURCE_OCCT ? MADE_BY_OCCT : MADE_BY_LIBRARY;
    res->face_worst[k] = deflection > 0 ? rep->worst / deflection : 0.0;
    i64 *row = res->face_report + (i64)k * REPORT_N;
    i64 values[REPORT_N] = {rep->rounds, rep->splits, rep->flips, rep->folded, rep->holes_filled,
                            rep->hole_edges_left, rep->open_edges, rep->free_edges, fallbacks[k], rep->area_ok};
    memcpy(row, values, sizeof values);
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

static void mesh_component(run *r, const tess_input *in, tess_result *res) {
  arenas M = {&r->A, &r->F, &r->T};
  host h = {in->callback, in->user};
  geometry geo;
  read_geometry(r, M, in, &geo);
  mesh_rest(r, M, &h, in, res, geo.faces, geo.pcurves, geo.curves, in->face_count, in->edge_count);
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

static void free_result_arrays(tess_result *res) {
  free(res->face_state);
  free(res->face_source);
  free(res->face_worst);
  free(res->face_report);
  free(res->face_vertices);
  free(res->face_triangles);
  free(res->positions);
  free(res->normals);
  free(res->triangles);
  free(res->edge_points);
  free(res->points);
  res->face_state = res->face_source = NULL;
  res->face_worst = NULL;
  res->face_report = res->face_vertices = res->face_triangles = NULL;
  res->positions = res->normals = NULL;
  res->triangles = NULL;
  res->edge_points = NULL;
  res->points = NULL;
}

EXPORT void cadgen_tessellate_free(tess_result *res) {
  if (!res) return;
  free_result_arrays(res);
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
    free_result_arrays(res);
  }
  arena_free(&R.A);
  arena_free(&R.F);
  arena_free(&R.T);
  return res;
}
