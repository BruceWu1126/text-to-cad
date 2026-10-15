/*
 * Part of tessellate.c's unity build: every face of the component verified against its exact
 * geometry, and repaired where it fails.
 *
 * A face's mesh is a claim -- OCCT's or the library's -- and this is where it is proven, with no
 * clock deciding what is checked. Four clauses, each a function here:
 *
 * - ON ITS SURFACE (`surface_distance`, `repair_surface`): every vertex, and of every triangle
 *   its centroid, its three edge midpoints and an asymmetric interior point, within the chord
 *   tolerance of the exact surface. A plane, cylinder, cone, sphere or torus is measured by its
 *   formula; any other surface at the sample's parameters, and -- where that misses by more than
 *   the tolerance -- along the surface's normal there, so a parameterization that runs faster on
 *   one side of a triangle than the other is not mistaken for a triangle off the surface. A
 *   triangle that strays is refined IN PLACE: its interior edges split at their midpoints onto the
 *   surface, or, where it has none the split could use, the triangle itself cut into three about
 *   its centroid; a boundary edge is never split and no vertex on one ever moves, so the faces
 *   stay met across their shared edges. The rounds are capped, as is the growth of the face; what
 *   is still beyond the tolerance after them is reported, and the host names the face.
 * - NO FOLDED TRIANGLE (`unfold`): a triangle whose facet points against the normal it is drawn
 *   with -- cheaply, its corners' normals; exactly, the surface's normal at its centroid -- is
 *   unfolded by flipping an edge it shares with a neighbour, where the flip leaves both halves
 *   facing their normals and their parameters' winding whole (the Delaunay needle OCCT folds
 *   across a B-spline). A boundary edge is never flipped.
 * - CLOSED (`check_closure`, `fill_holes`): over the whole component, its faces welded where
 *   their float32 positions are one point (what the body stores), every mesh edge is used by an
 *   even number of triangles. An odd one on a model edge is a BREP's own free edge (excepted), a
 *   neighbour the component draws without (excepted: that neighbour is named), or a crack, which
 *   conformity (`conform.c`) has closed unless the two faces disagree about the edge's points. An
 *   odd one on no model edge is a hole inside the face (the gap OCCT leaves at a pole): the holes'
 *   loops are ear-cut in the face's parameters and the new triangles verified like any other.
 * - COVERS ITS TRIM (`area_check`): the surface's area over the triangles' footprint in the face's
 *   parameters (each triangle's parameter area at the Jacobian of its centroid) against the face's
 *   exact area, the surface integrated over the trim its pcurves bound (Green's theorem along the
 *   loops, by Gauss quadrature knot span by knot span), within one per cent of the face or the
 *   area a chord error of one tolerance along the whole perimeter moves, whichever is more. The
 *   difference is area of the face the triangles miss or cover twice, wherever the parameters put
 *   it: a fan at a pole covers a parameter strip of no area, and a sliver that straddles a seam is
 *   unwrapped before it is read. The chords' own loss of area is not in it, since both sides are
 *   areas of the surface, and the host's exact area is not asked for: OCCT's quadrature of a face
 *   with a multi-span B-spline edge or basis curve is off by whole per cents.
 */

#define SURFACE_SAMPLES 5
#define REPAIR_ROUNDS 7
/* A repair stops growing a face past this many times the triangles it started with, plus a
 * floor for small faces: what cannot be brought right within it is named, not pursued. */
#define REPAIR_GROWTH 6
#define REPAIR_GROWTH_FLOOR 20000
#define FOLDED_COSINE (-1e-3)

/* What verification found and did for one face. */
typedef struct {
  double worst;       /* the farthest any sample lies from the surface, in model units */
  i64 rounds;         /* refinement rounds the repair took */
  i64 splits;         /* vertices the repair added */
  i64 flips;          /* folded triangles unfolded */
  i64 folded;         /* folded triangles left */
  i64 degenerate;     /* triangles of no area left (dropped before the body is written) */
  i64 holes_filled, hole_edges_left;
  i64 open_edges;     /* odd mesh edges on a shared model edge no neighbour explains: cracks */
  i64 free_edges;     /* odd mesh edges on the BREP's own free edges, or beside an unmeshed face */
  double area;         /* the triangles' area, in model units squared */
  double area_covered; /* the surface's area over the triangles' footprint in the face's parameters */
  double area_exact;   /* the surface's area over the whole trim */
  double area_slack;
  int area_ok;
} face_report;

/* The barycentric weights of the points a triangle is measured at: its centroid, its three edge
 * midpoints, and an asymmetric interior point (a bilinear patch's centre and midpoints can lie
 * on the surface while its facet does not). */
static const double SAMPLE_WEIGHTS[SURFACE_SAMPLES][3] = {
    {1.0 / 3, 1.0 / 3, 1.0 / 3}, {0.5, 0.5, 0.0}, {0.0, 0.5, 0.5}, {0.5, 0.0, 0.5}, {0.6, 0.25, 0.15}};

static inline int same_point(const double *a, const double *b) { return a[0] == b[0] && a[1] == b[1] && a[2] == b[2]; }

/* The distance from `p` to the surface's point at (u, v), measured as the clause says: by the
 * formula of an analytic surface; for any other, at the parameters, then along the normal when the
 * parametric miss exceeds `quick` (a parametrization running unevenly across a triangle displaces
 * the parametric point along the surface, not off it). */
static double surface_distance(run *r, const host *h, const surface *s, const double *box, double u, double v,
                               const double *p, double quick) {
  double rel[3] = {p[0] - s->o[0], p[1] - s->o[1], p[2] - s->o[2]};
  switch (s->kind) {
    case S_PLANE:
      return fabs(rel[0] * s->z[0] + rel[1] * s->z[1] + rel[2] * s->z[2]);
    case S_SPHERE:
      return fabs(sqrt(rel[0] * rel[0] + rel[1] * rel[1] + rel[2] * rel[2]) - s->radius);
    case S_CYLINDER:
    case S_CONE:
    case S_TORUS: {
      double height = rel[0] * s->z[0] + rel[1] * s->z[1] + rel[2] * s->z[2];
      double rx = rel[0] - height * s->z[0], ry = rel[1] - height * s->z[1], rz = rel[2] - height * s->z[2];
      double radial = sqrt(rx * rx + ry * ry + rz * rz);
      if (s->kind == S_CYLINDER) return fabs(radial - s->radius);
      if (s->kind == S_TORUS) return fabs(hypot(radial - s->major, height) - s->minor);
      /* In the point's plane through the axis, its distance from the generatrix rho = R + h tan a. */
      return fabs(radial * s->cos_a - height * s->sin_a - s->radius * s->cos_a);
    }
    default: {
      double at[3];
      surface_value(r, h, s, u, v, at);
      double dx = p[0] - at[0], dy = p[1] - at[1], dz = p[2] - at[2];
      double plain = sqrt(dx * dx + dy * dy + dz * dz);
      if (!(plain > quick)) return plain;
      double n[3];
      surface_normal(r, h, s, u, v, box, n);
      double length = n[0] * n[0] + n[1] * n[1] + n[2] * n[2];
      if (!(length > 0.5)) return plain; /* no normal here (a pole): the parametric miss stands */
      return fabs(dx * n[0] + dy * n[1] + dz * n[2]);
    }
  }
}

/* The farthest the triangle (a, b, c) lies from the surface over its samples, and whether that is
 * past `limit`. */
static double triangle_strays(run *r, const host *h, const surface *s, const double *box, const raw_face *raw, i32 a,
                              i32 b, i32 c, double limit) {
  const double *ua = raw->uv.v + 2 * a, *ub = raw->uv.v + 2 * b, *uc = raw->uv.v + 2 * c;
  const double *pa = raw->xyz.v + 3 * a, *pb = raw->xyz.v + 3 * b, *pc = raw->xyz.v + 3 * c;
  /* A triangle two of whose corners are one point (the parameters of a pole, kept for the face's
   * boundary) is never drawn, and is not measured. */
  if (same_point(pa, pb) || same_point(pb, pc) || same_point(pc, pa)) return 0.0;
  double worst = 0.0;
  for (int k = 0; k < SURFACE_SAMPLES; k++) {
    const double *w = SAMPLE_WEIGHTS[k];
    double u = w[0] * ua[0] + w[1] * ub[0] + w[2] * uc[0], v = w[0] * ua[1] + w[1] * ub[1] + w[2] * uc[1];
    double p[3] = {w[0] * pa[0] + w[1] * pb[0] + w[2] * pc[0], w[0] * pa[1] + w[1] * pb[1] + w[2] * pc[1],
                   w[0] * pa[2] + w[1] * pb[2] + w[2] * pc[2]};
    double distance = surface_distance(r, h, s, box, u, v, p, limit);
    if (distance > worst) worst = distance;
  }
  return worst;
}

/* The cosine between a triangle's facet and `normal` (a corner normal or the surface's), or 2 for
 * a triangle of no area. */
static double facet_cosine(const double *pa, const double *pb, const double *pc, const double *normal) {
  double e1[3] = {pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]}, e2[3] = {pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]};
  double f[3] = {e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0]};
  double facet = sqrt(f[0] * f[0] + f[1] * f[1] + f[2] * f[2]);
  double length = sqrt(normal[0] * normal[0] + normal[1] * normal[1] + normal[2] * normal[2]);
  if (!(facet > 0.0) || !(length > 0.0)) return 2.0;
  return (f[0] * normal[0] + f[1] * normal[1] + f[2] * normal[2]) / (facet * length);
}

/* Whether the triangle is folded: its facet against the surface's normal at its centroid (the
 * corners' normals first, which agree with it everywhere but at a singularity). */
static int is_folded(run *r, const host *h, const face *f, const raw_face *raw, i32 a, i32 b, i32 c) {
  const double *pa = raw->xyz.v + 3 * a, *pb = raw->xyz.v + 3 * b, *pc = raw->xyz.v + 3 * c;
  const double *na = raw->nrm.v + 3 * a, *nb = raw->nrm.v + 3 * b, *nc = raw->nrm.v + 3 * c;
  double sum[3] = {na[0] + nb[0] + nc[0], na[1] + nb[1] + nc[1], na[2] + nb[2] + nc[2]};
  double cheap = facet_cosine(pa, pb, pc, sum);
  if (cheap == 2.0) return 0; /* no area: not folded, dropped later */
  if (cheap > 0.2) return 0;
  double n[3];
  const double *ua = raw->uv.v + 2 * a, *ub = raw->uv.v + 2 * b, *uc = raw->uv.v + 2 * c;
  surface_normal(r, h, &f->s, (ua[0] + ub[0] + uc[0]) / 3, (ua[1] + ub[1] + uc[1]) / 3, f->uv, n);
  if (!(n[0] * n[0] + n[1] * n[1] + n[2] * n[2] > 0.5)) return cheap < FOLDED_COSINE;
  int folded = facet_cosine(pa, pb, pc, n) < FOLDED_COSINE;
  if (folded && getenv("CADGEN_TESS_DEBUG"))
    fprintf(stderr, "DEBUG fold face %d: cheap %.3f exact %.3f uv (%.5g,%.5g) (%.5g,%.5g) (%.5g,%.5g) n (%.3f,%.3f,%.3f) na (%.3f,%.3f,%.3f)\n",
            f->ord, cheap, facet_cosine(pa, pb, pc, n), ua[0], ua[1], ub[0], ub[1], uc[0], uc[1], n[0], n[1], n[2], na[0], na[1], na[2]);
  return folded;
}

/* Each mesh edge of the face and how many of its triangles use it. */
static void edge_uses(arena *T, const vi *tri, emap *uses) {
  emap_init(uses, T, tri->n);
  for (i64 t = 0; t < tri->n; t += 3)
    for (int k = 0; k < 3; k++) *emap_ref(uses, edge_key(tri->v[t + k], tri->v[t + (k + 1) % 3]), 0, NULL) += 1;
}

static void add_vertex(arena *A, run *r, const host *h, const face *f, raw_face *raw, double u, double v) {
  double p[3], n[3];
  surface_value(r, h, &f->s, u, v, p);
  surface_normal(r, h, &f->s, u, v, f->uv, n);
  PUSH2(A, raw->uv, u, v);
  PUSH3(A, raw->xyz, p[0], p[1], p[2]);
  PUSH3(A, raw->nrm, n[0], n[1], n[2]);
  PUSH(A, raw->minted, 0);
}

/* Every triangle of the face measured, the ones that stray refined in place (the file's header),
 * round after round; `rep` gets the worst distance left and what the repair did. `repair_at` is
 * the distance past which a sample strays; a face the library meshed is measured and not refined
 * again (its own refinement was the repair). */
static void repair_surface(run *r, arenas M, const host *h, const face *f, raw_face *raw, double repair_at,
                           int may_refine, face_report *rep) {
  const surface *s = &f->s;
  i64 initial = raw->tri.n / 3;
  i64 budget = imax(initial * REPAIR_GROWTH, REPAIR_GROWTH_FLOOR);
  /* Which triangles a round looks at: all of them first, then the ones the last round made. The
   * flags outlive a round's scratch (arena F); the round's maps do not (arena T). */
  vb fresh = {NULL, 0, 0};
  RESERVE(M.F, fresh, raw->tri.n / 3);
  for (i64 t = 0; t < raw->tri.n / 3; t++) fresh.v[t] = 1;
  fresh.n = raw->tri.n / 3;
  /* The vertices themselves, once: a vertex off its surface is a wrong parameter, not a chord. */
  for (i64 i = 0; i < raw->uv.n / 2; i++) {
    double distance = surface_distance(r, h, s, f->uv, raw->uv.v[2 * i], raw->uv.v[2 * i + 1], raw->xyz.v + 3 * i, repair_at);
    if (distance > rep->worst) rep->worst = distance;
  }
  for (int round = 0; round <= REPAIR_ROUNDS; round++) {
    arena_mark mark = arena_save(M.T);
    emap uses;
    edge_uses(M.T, &raw->tri, &uses);
    emap judged; /* edge key -> 1 when its midpoint strays */
    emap_init(&judged, M.T, raw->tri.n);
    vu marks = {NULL, 0, 0};
    vl centroids = {NULL, 0, 0};
    double worst = 0.0;
    for (i64 t = 0; t < raw->tri.n / 3; t++) {
      if (!fresh.v[t]) continue;
      i32 corners[3] = {raw->tri.v[3 * t], raw->tri.v[3 * t + 1], raw->tri.v[3 * t + 2]};
      double distance = triangle_strays(r, h, s, f->uv, raw, corners[0], corners[1], corners[2], repair_at);
      if (distance > worst) worst = distance;
      if (getenv("CADGEN_TESS_DEBUG") && distance > 2 * repair_at) {
        fprintf(stderr, "DEBUG face %d tri %lld strays %g (repair_at %g) may_refine %d round %d\n", f->ord, (long long)t,
                distance, repair_at, may_refine, round);
        for (int k = 0; k < 3; k++)
          fprintf(stderr, "   v%d uv (%.9g, %.9g) xyz (%.9g, %.9g, %.9g) boundary %d\n", corners[k],
                  raw->uv.v[2 * corners[k]], raw->uv.v[2 * corners[k] + 1], raw->xyz.v[3 * corners[k]],
                  raw->xyz.v[3 * corners[k] + 1], raw->xyz.v[3 * corners[k] + 2], bmap_has(&raw->boundary, corners[k]));
      }
      if (!(distance > repair_at) || !may_refine || round == REPAIR_ROUNDS) continue;
      /* The interior edges whose own midpoint strays are split; failing one, the longest interior
       * edge; failing any, the triangle is cut about its centroid. */
      int marked = 0;
      i32 lp = -1, lq = -1;
      double longest = -1.0;
      for (int k = 0; k < 3; k++) {
        i32 p = corners[k], q = corners[(k + 1) % 3];
        u64 key = edge_key(p, q);
        if (emap_get(&uses, key, 0) != 2) continue;
        int made;
        i64 *slot = emap_ref(&judged, key, 0, &made);
        if (made) {
          const double *up = raw->uv.v + 2 * p, *uq = raw->uv.v + 2 * q;
          const double *xp = raw->xyz.v + 3 * p, *xq = raw->xyz.v + 3 * q;
          double mid[3] = {(xp[0] + xq[0]) / 2, (xp[1] + xq[1]) / 2, (xp[2] + xq[2]) / 2};
          *slot = surface_distance(r, h, s, f->uv, (up[0] + uq[0]) / 2, (up[1] + uq[1]) / 2, mid, repair_at) > repair_at;
        }
        if (*slot) {
          PUSH(M.T, marks, key);
          marked = 1;
        }
        double length = dist3(raw->xyz.v + 3 * p, raw->xyz.v + 3 * q);
        if (length > longest) {
          longest = length;
          lp = p;
          lq = q;
        }
      }
      if (marked) continue;
      if (lp >= 0) PUSH(M.T, marks, edge_key(lp, lq));
      else PUSH(M.T, centroids, t);
    }
    if (worst > rep->worst) rep->worst = worst;
    if ((!marks.n && !centroids.n) || raw->tri.n / 3 >= budget) {
      arena_reset(M.T, mark);
      break;
    }
    rep->rounds++;
    /* Centroid splits first: the marked edges of a cut triangle survive into its children. */
    for (i64 k = 0; k < centroids.n; k++) {
      i64 t = centroids.v[k];
      i32 a = raw->tri.v[3 * t], b = raw->tri.v[3 * t + 1], c = raw->tri.v[3 * t + 2];
      const double *ua = raw->uv.v + 2 * a, *ub = raw->uv.v + 2 * b, *uc = raw->uv.v + 2 * c;
      i32 m = (i32)(raw->uv.n / 2);
      add_vertex(M.A, r, h, f, raw, (ua[0] + ub[0] + uc[0]) / 3, (ua[1] + ub[1] + uc[1]) / 3);
      raw->tri.v[3 * t + 2] = m;
      PUSH3(M.A, raw->tri, b, c, m);
      PUSH3(M.A, raw->tri, c, a, m);
      rep->splits++;
    }
    /* A centroid cut's children are fresh; a triangle a mark splits is fresh in every piece;
     * one untouched keeps its flag. split_marked keeps the triangles in order, each in place of
     * its parent. */
    vb grown = {NULL, 0, 0};
    RESERVE(M.F, grown, raw->tri.n / 3);
    for (i64 t = 0; t < raw->tri.n / 3; t++) grown.v[t] = (uint8_t)(t < fresh.n ? fresh.v[t] : 1);
    grown.n = raw->tri.n / 3;
    for (i64 k = 0; k < centroids.n; k++) grown.v[centroids.v[k]] = 1;
    if (marks.n) {
      sort_u64(M.T, marks.v, marks.n);
      i64 unique = 0;
      for (i64 k = 0; k < marks.n; k++)
        if (!unique || marks.v[k] != marks.v[unique - 1]) marks.v[unique++] = marks.v[k];
      vi split_tri;
      vb split_fresh;
      vu midpoints = {NULL, 0, 0};
      split_marked(M.A, M.T, &raw->tri, marks.v, unique, raw->uv.n / 2, &split_tri, &split_fresh, &midpoints);
      for (i64 k = 0; k < midpoints.n; k++) {
        i64 p = (i64)(midpoints.v[k] >> 32), q = (i64)(midpoints.v[k] & 0xFFFFFFFFu);
        add_vertex(M.A, r, h, f, raw, (raw->uv.v[2 * p] + raw->uv.v[2 * q]) / 2,
                   (raw->uv.v[2 * p + 1] + raw->uv.v[2 * q + 1]) / 2);
        rep->splits++;
      }
      vb next = {NULL, 0, 0};
      RESERVE(M.F, next, split_fresh.n);
      for (i64 t = 0; t < raw->tri.n / 3; t++) {
        i32 a = raw->tri.v[3 * t], b = raw->tri.v[3 * t + 1], c = raw->tri.v[3 * t + 2];
        int pieces = 1;
        for (int k = 0; k < 3; k++) {
          u64 key = edge_key(k == 0 ? a : k == 1 ? b : c, k == 0 ? b : k == 1 ? c : a);
          i64 lo = 0, hi = unique;
          while (lo < hi) {
            i64 m = (lo + hi) >> 1;
            if (marks.v[m] < key) lo = m + 1;
            else hi = m;
          }
          if (lo < unique && marks.v[lo] == key) pieces++;
        }
        for (int piece = 0; piece < pieces; piece++) next.v[next.n++] = (uint8_t)(pieces > 1 ? 1 : grown.v[t]);
      }
      raw->tri = split_tri;
      fresh = next;
    } else {
      fresh = grown;
    }
    arena_reset(M.T, mark);
  }
}

/* Each folded triangle of the face unfolded by flipping an interior edge it shares, where both
 * halves then face their normals and keep the face's winding in its parameters. A pass reads the
 * triangles' neighbours once and leaves a triangle a flip touched for the next pass. */
static void unfold(run *r, arenas M, const host *h, const face *f, raw_face *raw, face_report *rep) {
  const double *U = raw->uv.v;
#define ORIENT2(a, b, c) \
  ((U[2 * (b)] - U[2 * (a)]) * (U[2 * (c) + 1] - U[2 * (a) + 1]) - (U[2 * (c)] - U[2 * (a)]) * (U[2 * (b) + 1] - U[2 * (a) + 1]))
  for (int pass = 0; pass < 4; pass++) {
    arena_mark mark = arena_save(M.T);
    i64 count = raw->tri.n / 3;
    /* Each edge's first two triangles (the second plus one in the high word), -2 past two. */
    emap owners;
    emap_init(&owners, M.T, raw->tri.n);
    for (i64 t = 0; t < count; t++)
      for (int k = 0; k < 3; k++) {
        u64 key = edge_key(raw->tri.v[3 * t + k], raw->tri.v[3 * t + (k + 1) % 3]);
        int made;
        i64 *slot = emap_ref(&owners, key, -1, &made);
        if (made) *slot = t;
        else if (*slot >= 0 && (*slot >> 32) == 0) *slot = *slot | ((t + 1) << 32);
        else *slot = -2;
      }
    uint8_t *changed = (uint8_t *)arena_zalloc(M.T, (size_t)(count ? count : 1));
    i64 flips = 0, folded = 0;
    for (i64 t = 0; t < count; t++) {
      if (changed[t]) continue;
      i32 corners[3] = {raw->tri.v[3 * t], raw->tri.v[3 * t + 1], raw->tri.v[3 * t + 2]};
      if (!is_folded(r, h, f, raw, corners[0], corners[1], corners[2])) continue;
      double sign = ORIENT2(corners[0], corners[1], corners[2]) < 0 ? -1.0 : 1.0;
      /* Its edges, longest first. */
      int order[3] = {0, 1, 2};
      double lengths[3];
      for (int k = 0; k < 3; k++) lengths[k] = dist3(raw->xyz.v + 3 * corners[k], raw->xyz.v + 3 * corners[(k + 1) % 3]);
      for (int i = 0; i < 3; i++)
        for (int j = i + 1; j < 3; j++)
          if (lengths[order[j]] > lengths[order[i]]) {
            int swap = order[i];
            order[i] = order[j];
            order[j] = swap;
          }
      int flipped = 0;
      for (int o = 0; o < 3 && !flipped; o++) {
        int k = order[o];
        i32 a = corners[k], b = corners[(k + 1) % 3], c = corners[(k + 2) % 3];
        i64 slot = emap_get(&owners, edge_key(a, b), -1);
        if (slot < 0 || (slot >> 32) == 0) continue;
        i64 t1 = slot & 0xFFFFFFFF, t2 = (slot >> 32) - 1;
        i64 other = t1 == t ? t2 : t1;
        if (other == t || changed[other]) continue;
        i32 d = -1;
        for (int m = 0; m < 3; m++) {
          i32 p = raw->tri.v[3 * other + m];
          if (p != a && p != b) d = p;
        }
        if (d < 0 || d == c) continue;
        /* The quad a, d, b, c must be convex in the parameters for the flipped diagonal to lie
         * inside it, and both halves must face the surface. */
        if (sign * ORIENT2(a, d, c) <= 0 || sign * ORIENT2(d, b, c) <= 0) continue;
        if (is_folded(r, h, f, raw, a, d, c) || is_folded(r, h, f, raw, d, b, c)) continue;
        raw->tri.v[3 * t] = a;
        raw->tri.v[3 * t + 1] = d;
        raw->tri.v[3 * t + 2] = c;
        raw->tri.v[3 * other] = d;
        raw->tri.v[3 * other + 1] = b;
        raw->tri.v[3 * other + 2] = c;
        changed[t] = changed[other] = 1;
        flipped = 1;
        flips++;
      }
      if (!flipped) folded++;
    }
    arena_reset(M.T, mark);
    rep->flips += flips;
    if (!flips) {
      rep->folded = folded;
      break;
    }
    rep->folded = folded; /* the count stands if the next pass flips none */
  }
#undef ORIENT2
}

/* --- Covers its trim ----------------------------------------------------------------------- */

/* Gauss-Legendre on [-1, 1]: four points, exact through degree seven. */
static const double GAUSS_X[4] = {-0.8611363115940526, -0.3399810435848563, 0.3399810435848563, 0.8611363115940526};
static const double GAUSS_W[4] = {0.3478548451374538, 0.6521451548625461, 0.6521451548625461, 0.3478548451374538};
#define GAUSS_N 4
#define HALF_PI 1.5707963267948966
/* An interval with no knots of its own is integrated in this many pieces: a conic's or a host's
 * integrand, smooth but not polynomial over the whole face. */
#define SMOOTH_PIECES 16
#define MAX_PIECES 64

/* A knot vector with its degree and pole count: the domain is [v[degree], v[count]]. */
typedef struct {
  const double *v;
  i32 degree, count;
} bspline_curve_knots;

static inline double cross_norm(const double *a, const double *b) {
  double x = a[1] * b[2] - a[2] * b[1], y = a[2] * b[0] - a[0] * b[2], z = a[0] * b[1] - a[1] * b[0];
  return sqrt(x * x + y * y + z * z);
}

/* |Su x Sv| at (u, v): the surface's area per unit of its parameters there -- by formula for the
 * analytic kinds, from the derivatives of a B-spline or swept surface, and by central differences
 * of the host's points for a surface only the host evaluates. */
static double surface_jacobian(run *r, const host *h, const surface *s, double u, double v, const double *box) {
  double p[3], du[3], dv[3];
  switch (s->kind) {
    case S_PLANE:
      return cross_norm(s->x, s->y);
    case S_CYLINDER:
      return fabs(s->radius);
    case S_CONE:
      return fabs(s->radius + v * s->sin_a);
    case S_SPHERE:
      return s->radius * s->radius * fabs(cos(v));
    case S_TORUS:
      return fabs(s->minor * (s->major + s->minor * cos(v)));
    case S_BSPLINE:
      bspline_surface_d1(s->bs, u, v, p, du, dv);
      return cross_norm(du, dv);
    case S_EXTRUSION:
    case S_REVOLUTION:
      swept_d1(r, s->sw, s->kind == S_REVOLUTION, u, v, p, du, dv);
      return cross_norm(du, dv);
    case S_HOST: {
      double hu = py_max(fabs(box[1] - box[0]), 1e-9) * 1e-6, hv = py_max(fabs(box[3] - box[2]), 1e-9) * 1e-6;
      double a[3], b[3];
      ask(r, h, ASK_SURFACE, s->index, u + hu, v, a);
      ask(r, h, ASK_SURFACE, s->index, u - hu, v, b);
      for (int d = 0; d < 3; d++) du[d] = (a[d] - b[d]) / (2 * hu);
      ask(r, h, ASK_SURFACE, s->index, u, v + hv, a);
      ask(r, h, ASK_SURFACE, s->index, u, v - hv, b);
      for (int d = 0; d < 3; d++) dv[d] = (a[d] - b[d]) / (2 * hv);
      return cross_norm(du, dv);
    }
    default:
      fail(r, E_ERROR);
  }
  return 0.0;
}

/* The distinct knots of a B-spline strictly inside (lo, hi), ascending, at most `cap` of them. */
static i32 knots_between(const bspline_curve_knots knots, double lo, double hi, double *out, i32 cap) {
  i32 n = 0;
  for (i32 k = knots.degree + 1; k < knots.count && n < cap; k++) {
    double t = knots.v[k];
    if (t > lo && t < hi && (n == 0 || t > out[n - 1])) out[n++] = t;
  }
  return n;
}

/* The knots that cut the surface along v (none for the kinds without any). */
static i32 surface_knots_v(const surface *s, double lo, double hi, double *out, i32 cap) {
  if (s->kind == S_BSPLINE) {
    bspline_curve_knots k = {s->bs->knots_v, s->bs->degree_v, s->bs->count_v};
    return knots_between(k, lo, hi, out, cap);
  }
  if (s->kind == S_REVOLUTION && s->sw->basis.kind == C_BSPLINE) {
    bspline_curve_knots k = {s->sw->basis.bs.knots, s->sw->basis.bs.degree, s->sw->basis.bs.count};
    return knots_between(k, lo, hi, out, cap);
  }
  return 0;
}

/* The knots that cut the surface along u. */
static i32 surface_knots_u(const surface *s, double lo, double hi, double *out, i32 cap) {
  if (s->kind == S_BSPLINE) {
    bspline_curve_knots k = {s->bs->knots_u, s->bs->degree_u, s->bs->count_u};
    return knots_between(k, lo, hi, out, cap);
  }
  if (s->kind == S_EXTRUSION && s->sw->basis.kind == C_BSPLINE) {
    bspline_curve_knots k = {s->sw->basis.bs.knots, s->sw->basis.bs.degree, s->sw->basis.bs.count};
    return knots_between(k, lo, hi, out, cap);
  }
  return 0;
}

/* The surface's area between the parameter lines v = v0 and v = v along the line at u, signed by
 * the direction: the Jacobian integrated, in closed form for the analytic kinds and otherwise by
 * Gauss quadrature over the spans the surface's knots cut the line into (a surface of extrusion
 * has one value all along the line). */
static double area_along_v(run *r, const host *h, const surface *s, const double *box, double u, double v0, double v) {
  if (v == v0) return 0.0;
  double lo = py_min(v0, v), hi = py_max(v0, v), sign = v > v0 ? 1.0 : -1.0;
  switch (s->kind) {
    case S_PLANE:
    case S_CYLINDER:
    case S_EXTRUSION:
      return (v - v0) * surface_jacobian(r, h, s, u, v, box);
    case S_CONE: {
      /* |R + t sin a| integrated: R t + t^2 sin a / 2 on each side of the apex. */
      double R = s->radius, sa = s->sin_a;
#define ANTIDERIVATIVE(t) (R * (t) + (t) * (t) * sa / 2)
      double apex = sa != 0.0 ? -R / sa : INFINITY, total;
      if (apex > lo && apex < hi) total = fabs(ANTIDERIVATIVE(apex) - ANTIDERIVATIVE(lo)) + fabs(ANTIDERIVATIVE(hi) - ANTIDERIVATIVE(apex));
      else total = fabs(ANTIDERIVATIVE(hi) - ANTIDERIVATIVE(lo));
#undef ANTIDERIVATIVE
      return sign * total;
    }
    case S_SPHERE:
      if (lo >= -HALF_PI - 1e-9 && hi <= HALF_PI + 1e-9) return s->radius * s->radius * (sin(v) - sin(v0));
      break;
    case S_TORUS:
      if (s->minor >= 0.0 && s->major >= s->minor) return s->minor * (s->major * (v - v0) + s->minor * (sin(v) - sin(v0)));
      break;
    default:
      break;
  }
  double breaks[MAX_PIECES];
  i32 n = surface_knots_v(s, lo, hi, breaks, MAX_PIECES - 1);
  /* A span is integrated in as many pieces as its degree asks of a four-point rule (the Jacobian
   * is the root of a polynomial of about twice the degree); a conic's or a host's smooth integrand
   * in a fixed number. */
  i32 pieces = SMOOTH_PIECES;
  if (s->kind == S_BSPLINE) pieces = (s->bs->degree_v + GAUSS_N) / GAUSS_N;
  else if (s->kind == S_REVOLUTION && s->sw->basis.kind == C_BSPLINE) pieces = (s->sw->basis.bs.degree + GAUSS_N) / GAUSS_N;
  double total = 0.0, a = lo;
  for (i32 k = 0; k <= n; k++) {
    double b = k < n ? breaks[k] : hi;
    for (i32 piece = 0; piece < pieces; piece++) {
      double pa = a + (b - a) * piece / pieces, pb = a + (b - a) * (piece + 1) / pieces;
      double mid = (pa + pb) / 2, half = (pb - pa) / 2;
      for (int g = 0; g < GAUSS_N; g++) total += GAUSS_W[g] * half * surface_jacobian(r, h, s, u, mid + half * GAUSS_X[g], box);
    }
    a = b;
  }
  return sign * total;
}

/* The degree the surface's area varies with along u: a B-spline's, a swept surface's basis
 * curve's where it is a B-spline, else a conic's stand-in. */
static i32 surface_degree_u(const surface *s) {
  if (s->kind == S_BSPLINE) return s->bs->degree_u;
  if (s->kind == S_EXTRUSION && s->sw->basis.kind == C_BSPLINE) return s->sw->basis.bs.degree;
  return 3;
}

/* The pcurve's point at t and du/dt there: a native pcurve's from its B-spline, a host's by central
 * differences. */
static void pcurve_d1(run *r, const host *h, const pcurve *pc, double t, double *uv, double *du_dt) {
  if (!pc->host) {
    double tangent[2];
    bspline_curve_d1(&pc->bs, t, uv, tangent);
    *du_dt = tangent[0];
    return;
  }
  double step = py_max(fabs(pc->last - pc->first), 1e-9) * 1e-6, a[2], b[2];
  pcurve_value(r, h, pc, t, uv);
  pcurve_value(r, h, pc, t + step, a);
  pcurve_value(r, h, pc, t - step, b);
  *du_dt = (a[0] - b[0]) / (2 * step);
}

/* How many pieces a stretch [a, b] of the pcurve is integrated in: one more than the surface's
 * knots its parameter box crosses, times what the surface's degree along u and the pcurve's own
 * ask of a four-point rule, up to MAX_PIECES. */
static i32 stretch_pieces(run *r, const host *h, const face *f, const pcurve *pc, double a, double b) {
  double lo[2] = {INFINITY, INFINITY}, hi[2] = {-INFINITY, -INFINITY}, ts[3] = {a, (a + b) / 2, b};
  for (int k = 0; k < 3; k++) {
    double uv[2];
    pcurve_value(r, h, pc, ts[k], uv);
    for (int d = 0; d < 2; d++) {
      lo[d] = py_min(lo[d], uv[d]);
      hi[d] = py_max(hi[d], uv[d]);
    }
  }
  double knots[MAX_PIECES];
  i32 crossed = surface_knots_u(&f->s, lo[0], hi[0], knots, MAX_PIECES - 1) +
                surface_knots_v(&f->s, lo[1], hi[1], knots, MAX_PIECES - 1);
  i32 degree = surface_degree_u(&f->s) + (pc->host ? 3 : pc->bs.degree);
  i64 pieces = (i64)(1 + crossed) * ((degree + GAUSS_N) / GAUSS_N);
  return pieces < MAX_PIECES ? (i32)pieces : MAX_PIECES;
}

/* The face's exact area: the surface's area over the trim its loops bound, by Green's theorem in
 * the face's parameters -- the line integral, along the loops, of the area between the box's
 * bottom and the loop (`area_along_v`) against du -- with Gauss quadrature along each pcurve,
 * knot span by knot span, each span cut again where the surface's knots cross it. */
static double exact_area(run *r, const host *h, const face *f, const pcurve *pcurves) {
  double v0 = f->uv[2], total = 0.0;
  for (i32 l = 0; l < f->loop_count; l++) {
    for (i32 j = 0; j < f->loop_size[l]; j++) {
      const pcurve *pc = &pcurves[f->loop_start[l] + j];
      double breaks[MAX_PIECES];
      i32 n = 0;
      if (!pc->host) {
        bspline_curve_knots k = {pc->bs.knots, pc->bs.degree, pc->bs.count};
        n = knots_between(k, pc->first, pc->last, breaks, MAX_PIECES - 1);
      }
      double a = pc->first, sum = 0.0;
      for (i32 k = 0; k <= n; k++) {
        double b = k < n ? breaks[k] : pc->last;
        i32 pieces = stretch_pieces(r, h, f, pc, a, b);
        for (i32 piece = 0; piece < pieces; piece++) {
          double pa = a + (b - a) * piece / pieces, pb = a + (b - a) * (piece + 1) / pieces;
          double mid = (pa + pb) / 2, half = (pb - pa) / 2;
          for (int g = 0; g < GAUSS_N; g++) {
            double t = mid + half * GAUSS_X[g], uv[2], du_dt;
            pcurve_d1(r, h, pc, t, uv, &du_dt);
            sum += GAUSS_W[g] * half * du_dt * area_along_v(r, h, &f->s, f->uv, uv[0], v0, uv[1]);
          }
        }
        a = b;
      }
      total += pc->reversed ? -sum : sum;
    }
  }
  return fabs(total);
}

/* The face's triangles' area, in model units squared, and in `covered` the surface's area over
 * their footprint in the face's parameters: each triangle's signed parameter area at the Jacobian
 * of its centroid, exact where the Jacobian is linear over the triangle and second-order close
 * elsewhere (a triangle at the chord tolerance is small against the surface's features). A
 * triangle wound against the face in its parameters covers nothing twice: it counts negative. */
static double mesh_area(run *r, const host *h, const face *f, const raw_face *raw, double *covered) {
  double area = 0.0, over = 0.0;
  for (i64 t = 0; t < raw->tri.n; t += 3) {
    i32 a = raw->tri.v[t], b = raw->tri.v[t + 1], c = raw->tri.v[t + 2];
    const double *pa = raw->xyz.v + 3 * a, *pb = raw->xyz.v + 3 * b, *pc = raw->xyz.v + 3 * c;
    double e1[3] = {pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]}, e2[3] = {pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]};
    area += cross_norm(e1, e2) / 2;
    const double *ua = raw->uv.v + 2 * a, *ub = raw->uv.v + 2 * b, *uc = raw->uv.v + 2 * c;
    double twice = (ub[0] - ua[0]) * (uc[1] - ua[1]) - (uc[0] - ua[0]) * (ub[1] - ua[1]);
    if (twice == 0.0) continue;
    over += twice / 2 * surface_jacobian(r, h, &f->s, (ua[0] + ub[0] + uc[0]) / 3, (ua[1] + ub[1] + uc[1]) / 3, f->uv);
  }
  *covered = over;
  return area;
}

/* Whether the face's triangles cover its trim (the file's header): the surface's area over their
 * footprint against the face's exact area, within one per cent of it or a chord error of
 * `deflection` along the whole perimeter -- the lengths of the edges the loops run along --
 * whichever is more. */
static int area_check(run *r, const host *h, const face *f, const pcurve *pcurves, const shared *edges, i32 edge_count,
                      const raw_face *raw, double deflection, face_report *rep) {
  double covered = 0.0;
  rep->area = mesh_area(r, h, f, raw, &covered);
  rep->area_covered = fabs(covered);
  rep->area_exact = exact_area(r, h, f, pcurves);
  double perimeter = 0.0;
  for (i32 l = 0; l < f->loop_count; l++)
    for (i32 j = 0; j < f->loop_size[l]; j++) {
      i32 ord = pcurves[f->loop_start[l] + j].edge_ord;
      if (ord > 0 && ord <= edge_count && edges[ord - 1].present) perimeter += edges[ord - 1].length;
    }
  rep->area_slack = py_max(0.01 * rep->area_exact, perimeter * deflection);
  rep->area_ok = fabs(rep->area_covered - rep->area_exact) <= rep->area_slack;
  if (getenv("CADGEN_TESS_DEBUG"))
    fprintf(stderr, "DEBUG area face %d: flat %.9g covered %.9g exact %.9g (%+.4f%%) slack %.3g perimeter %.6g ok %d\n", f->ord,
            rep->area, rep->area_covered, rep->area_exact, 100.0 * (rep->area_covered - rep->area_exact) / rep->area_exact,
            rep->area_slack, perimeter, rep->area_ok);
  return rep->area_ok;
}

/* The float32 position every stored vertex has, as a hash key: the body's own notion of one point. */
static inline void position_key(const double *p, u64 *key) {
  float x = (float)p[0], y = (float)p[1], z = (float)p[2];
  if (x == 0.0f) x = 0.0f;
  if (y == 0.0f) y = 0.0f;
  if (z == 0.0f) z = 0.0f;
  u32 bx, by, bz;
  memcpy(&bx, &x, 4);
  memcpy(&by, &y, 4);
  memcpy(&bz, &z, 4);
  key[0] = (u64)bx | ((u64)by << 32);
  key[1] = (u64)bz;
}

/* One odd mesh edge of the welded component: the face and the local vertices it runs between. */
typedef struct {
  i32 face;
  i32 p, q;
} open_edge;

typedef VEC(open_edge) vopen;

/* The model edges (ordinals) a boundary vertex lies on that another lies on too, or 0. */
static i32 common_edge(const bmap *boundary, i32 p, i32 q) {
  const labels *lp = bmap_get(boundary, p), *lq = bmap_get(boundary, q);
  if (!lp || !lq) return 0;
  for (i64 i = 0; i < lp->n; i++)
    for (i64 j = 0; j < lq->n; j++)
      if (lp->v[i].ord == lq->v[j].ord) return lp->v[i].ord;
  return 0;
}

/* Every odd mesh edge of the component, its faces welded where their float32 positions are one
 * point, each with the face whose triangle runs it. A triangle two of whose corners weld to one
 * point is no triangle here. */
static void odd_edges(run *r, arenas M, raw_face **raws, i32 F, vopen *out) {
  (void)r;
  i64 vertices = 0, triangles = 0;
  for (i32 k = 0; k < F; k++)
    if (raws[k]) {
      vertices += raws[k]->uv.n / 2;
      triangles += raws[k]->tri.n / 3;
    }
  map welded;
  map_init(&welded, M.T, 2, vertices);
  i32 **gids = (i32 **)arena_zalloc(M.T, sizeof(i32 *) * (size_t)(F ? F : 1));
  i64 next = 0;
  for (i32 k = 0; k < F; k++) {
    if (!raws[k]) continue;
    i64 count = raws[k]->uv.n / 2;
    gids[k] = (i32 *)arena_alloc(M.T, sizeof(i32) * (size_t)(count ? count : 1));
    for (i64 i = 0; i < count; i++) {
      u64 key[2];
      position_key(raws[k]->xyz.v + 3 * i, key);
      int made;
      i64 *slot = map_ref(&welded, key, next, &made);
      if (made) next++;
      gids[k][i] = (i32)*slot;
    }
  }
  emap uses;
  emap_init(&uses, M.T, triangles * 3);
  for (i32 k = 0; k < F; k++) {
    if (!raws[k]) continue;
    const vi *tri = &raws[k]->tri;
    for (i64 t = 0; t < tri->n; t += 3) {
      i32 a = gids[k][tri->v[t]], b = gids[k][tri->v[t + 1]], c = gids[k][tri->v[t + 2]];
      if (a == b || b == c || c == a) continue;
      *emap_ref(&uses, edge_key(a, b), 0, NULL) += 1;
      *emap_ref(&uses, edge_key(b, c), 0, NULL) += 1;
      *emap_ref(&uses, edge_key(c, a), 0, NULL) += 1;
    }
  }
  out->n = 0;
  for (i32 k = 0; k < F; k++) {
    if (!raws[k]) continue;
    const vi *tri = &raws[k]->tri;
    for (i64 t = 0; t < tri->n; t += 3) {
      i32 local[3] = {tri->v[t], tri->v[t + 1], tri->v[t + 2]};
      i32 a = gids[k][local[0]], b = gids[k][local[1]], c = gids[k][local[2]];
      if (a == b || b == c || c == a) continue;
      for (int e = 0; e < 3; e++) {
        i32 g1 = e == 0 ? a : e == 1 ? b : c, g2 = e == 0 ? b : e == 1 ? c : a;
        if (emap_get(&uses, edge_key(g1, g2), 0) % 2 == 1) {
          open_edge o = {k, local[e], local[(e + 1) % 3]};
          PUSH(M.T, *out, o);
        }
      }
    }
  }
}

/* The holes inside a face -- its odd mesh edges on no model edge, chained into closed loops --
 * ear-cut in the face's parameters, wound as the face is. A loop that does not close, or has no
 * area in the parameters, is left; the caller counts what is left by looking again. */
static void fill_holes(run *r, arenas M, raw_face *raw, const open_edge *edges, i64 count, face_report *rep) {
  if (!count) return;
  arena_mark mark = arena_save(M.T);
  /* Each open edge's directed pair, its tail indexed: the loop runs tail to head, head to the
   * next edge's tail. The triangles wind one way round, so the loop runs the other. */
  emap by_tail;
  emap_init(&by_tail, M.T, count);
  uint8_t *used = (uint8_t *)arena_zalloc(M.T, (size_t)count);
  for (i64 i = 0; i < count; i++) {
    int made;
    i64 *slot = emap_ref(&by_tail, (u64)(u32)edges[i].p, i, &made);
    if (!made) *slot = -2; /* two open edges leave one vertex: not a simple loop */
  }
  double orientation = 0.0;
  for (i64 t = 0; t < raw->tri.n && orientation == 0.0; t += 3) {
    const double *a = raw->uv.v + 2 * raw->tri.v[t], *b = raw->uv.v + 2 * raw->tri.v[t + 1],
                 *c = raw->uv.v + 2 * raw->tri.v[t + 2];
    orientation = (b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1]);
  }
  for (i64 start = 0; start < count; start++) {
    if (used[start]) continue;
    vi loop = {NULL, 0, 0};
    i64 at = start;
    int closed = 0;
    for (i64 steps = 0; steps <= count; steps++) {
      used[at] = 1;
      PUSH(M.T, loop, edges[at].p);
      i64 next = emap_get(&by_tail, (u64)(u32)edges[at].q, -1);
      if (next < 0 || (next != start && used[next])) break;
      if (next == start) {
        closed = 1;
        break;
      }
      at = next;
    }
    if (!closed || loop.n < 3) continue;
    /* The loop as a polygon in the parameters, wound as the triangles are (it ran against them). */
    poly contour = {(double *)arena_alloc(M.T, sizeof(double) * (size_t)(2 * loop.n)), loop.n};
    for (i64 i = 0; i < loop.n; i++) {
      i32 v = loop.v[loop.n - 1 - i];
      contour.p[2 * i] = raw->uv.v[2 * v];
      contour.p[2 * i + 1] = raw->uv.v[2 * v + 1];
    }
    i32 *points = (i32 *)arena_alloc(M.T, sizeof(i32) * (size_t)loop.n);
    for (i64 i = 0; i < loop.n; i++) points[i] = loop.v[loop.n - 1 - i];
    double area = polygon_area(contour.p, contour.n);
    if (!(fabs(area) > 0.0)) continue;
    vi made = {NULL, 0, 0};
    guard g;
    ENTER(r, g);
    if (setjmp(g.jump) != 0) {
      LEAVE(r, g);
      if (g.code == E_MEMORY) fail(r, E_MEMORY);
      continue;
    }
    triangulate_shape(r, M.T, &contour, NULL, 0, &made);
    LEAVE(r, g);
    i64 before = raw->tri.n;
    for (i64 t = 0; t + 2 < made.n; t += 3) {
      i32 a = points[made.v[t]], b = points[made.v[t + 1]], c = points[made.v[t + 2]];
      const double *ua = raw->uv.v + 2 * a, *ub = raw->uv.v + 2 * b, *uc = raw->uv.v + 2 * c;
      double turn = (ub[0] - ua[0]) * (uc[1] - ua[1]) - (uc[0] - ua[0]) * (ub[1] - ua[1]);
      if (turn == 0.0 || a == b || b == c || c == a) continue;
      if ((turn < 0) != (orientation < 0)) {
        i32 swap = b;
        b = c;
        c = swap;
      }
      PUSH3(M.A, raw->tri, a, b, c);
    }
    if (raw->tri.n > before) rep->holes_filled++;
  }
  arena_reset(M.T, mark);
}
