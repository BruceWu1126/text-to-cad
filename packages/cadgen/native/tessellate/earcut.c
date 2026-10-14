/*
 * Part of tessellate.c's unity build: polygon triangulation, mapbox/earcut 3.0.2 as three.js
 * ships it (three/src/extras/lib/earcut.js), line for line, so a face's cells triangulate
 * exactly as the earlier tessellators triangulated them.
 *
 * `triangulate_shape` is three.js's ShapeUtils.triangulateShape: a contour and its holes in,
 * triangles over the contour's points then the holes' out, a closing repeat of a ring's first
 * point dropped first. A recursion past 900 levels is a failure (Python's stack, in the port this
 * was proven against).
 */

typedef struct enode {
  i32 i;
  double x, y;
  struct enode *prev, *next;
  i32 z;
  struct enode *prev_z, *next_z;
  int steiner;
} enode;

typedef struct {
  run *r;
  arena *a;
  vi *triangles;
  int depth;
} earcut_state;

#define EARCUT_MAX_DEPTH 900

static enode *e_node(earcut_state *st, i32 i, double x, double y) {
  enode *p = (enode *)arena_alloc(st->a, sizeof(enode));
  p->i = i;
  p->x = x;
  p->y = y;
  p->prev = p->next = NULL;
  p->z = 0;
  p->prev_z = p->next_z = NULL;
  p->steiner = 0;
  return p;
}

static inline double e_area(const enode *p, const enode *q, const enode *r) {
  return (q->y - p->y) * (r->x - q->x) - (q->x - p->x) * (r->y - q->y);
}

static inline int e_equals(const enode *a, const enode *b) { return a->x == b->x && a->y == b->y; }

static enode *e_insert(earcut_state *st, i32 i, double x, double y, enode *last) {
  enode *p = e_node(st, i, x, y);
  if (!last) {
    p->prev = p;
    p->next = p;
  } else {
    p->next = last->next;
    p->prev = last;
    last->next->prev = p;
    last->next = p;
  }
  return p;
}

static void e_remove(enode *p) {
  p->next->prev = p->prev;
  p->prev->next = p->next;
  if (p->prev_z) p->prev_z->next_z = p->next_z;
  if (p->next_z) p->next_z->prev_z = p->prev_z;
}

static double e_signed_area(const double *data, i64 start, i64 end) {
  double total = 0.0;
  i64 j = end - 2;
  for (i64 i = start; i < end; i += 2) {
    total += (data[j] - data[i]) * (data[i + 1] + data[j + 1]);
    j = i;
  }
  return total;
}

static enode *e_linked_list(earcut_state *st, const double *data, i64 start, i64 end, int clockwise) {
  enode *last = NULL;
  if (clockwise == (e_signed_area(data, start, end) > 0)) {
    for (i64 i = start; i < end; i += 2) last = e_insert(st, (i32)(i / 2), data[i], data[i + 1], last);
  } else {
    for (i64 i = end - 2; i >= start; i -= 2) last = e_insert(st, (i32)(i / 2), data[i], data[i + 1], last);
  }
  if (last && e_equals(last, last->next)) {
    e_remove(last);
    last = last->next;
  }
  return last;
}

static enode *e_filter_points(enode *start, enode *end) {
  if (!start) return start;
  if (!end) end = start;
  enode *p = start;
  for (;;) {
    int again = 0;
    if (!p->steiner && (e_equals(p, p->next) || e_area(p->prev, p, p->next) == 0)) {
      e_remove(p);
      p = end = p->prev;
      if (p == p->next) break;
      again = 1;
    } else {
      p = p->next;
    }
    if (!(again || p != end)) break;
  }
  return end;
}

static inline i32 js_int32(double value) {
  if (!isfinite(value)) return 0;
  double whole = trunc(value);
  double wrapped = fmod(whole, 4294967296.0);
  if (wrapped < 0) wrapped += 4294967296.0;
  return (i32)(u32)wrapped;
}

static i32 e_z_order(double x, double y, double min_x, double min_y, double inv_size) {
  u32 ux = (u32)js_int32((x - min_x) * inv_size);
  u32 uy = (u32)js_int32((y - min_y) * inv_size);
  ux = (ux | (ux << 8)) & 0x00FF00FFu;
  ux = (ux | (ux << 4)) & 0x0F0F0F0Fu;
  ux = (ux | (ux << 2)) & 0x33333333u;
  ux = (ux | (ux << 1)) & 0x55555555u;
  uy = (uy | (uy << 8)) & 0x00FF00FFu;
  uy = (uy | (uy << 4)) & 0x0F0F0F0Fu;
  uy = (uy | (uy << 2)) & 0x33333333u;
  uy = (uy | (uy << 1)) & 0x55555555u;
  return (i32)(ux | (uy << 1));
}

static enode *e_sort_linked(enode *list) {
  i64 in_size = 1;
  for (;;) {
    enode *p = list, *tail = NULL, *e, *q;
    list = NULL;
    i64 num_merges = 0;
    while (p) {
      num_merges++;
      q = p;
      i64 p_size = 0;
      for (i64 k = 0; k < in_size; k++) {
        p_size++;
        q = q->next_z;
        if (!q) break;
      }
      i64 q_size = in_size;
      while (p_size > 0 || (q_size > 0 && q)) {
        if (p_size != 0 && (q_size == 0 || !q || p->z <= q->z)) {
          e = p;
          p = p->next_z;
          p_size--;
        } else {
          e = q;
          q = q->next_z;
          q_size--;
        }
        if (tail) tail->next_z = e;
        else list = e;
        e->prev_z = tail;
        tail = e;
      }
      p = q;
    }
    tail->next_z = NULL;
    in_size *= 2;
    if (num_merges <= 1) break;
  }
  return list;
}

static void e_index_curve(enode *start, double min_x, double min_y, double inv_size) {
  enode *p = start;
  for (;;) {
    if (p->z == 0) p->z = e_z_order(p->x, p->y, min_x, min_y, inv_size);
    p->prev_z = p->prev;
    p->next_z = p->next;
    p = p->next;
    if (p == start) break;
  }
  p->prev_z->next_z = NULL;
  p->prev_z = NULL;
  e_sort_linked(p);
}

static inline int e_point_in_triangle(double ax, double ay, double bx, double by, double cx, double cy, double px,
                                      double py) {
  return (cx - px) * (ay - py) >= (ax - px) * (cy - py) && (ax - px) * (by - py) >= (bx - px) * (ay - py) &&
         (bx - px) * (cy - py) >= (cx - px) * (by - py);
}

static inline int e_point_in_triangle_except_first(double ax, double ay, double bx, double by, double cx, double cy,
                                                   double px, double py) {
  return !(ax == px && ay == py) && e_point_in_triangle(ax, ay, bx, by, cx, cy, px, py);
}

static int e_is_ear(enode *ear) {
  enode *a = ear->prev, *b = ear, *c = ear->next;
  if (e_area(a, b, c) >= 0) return 0;
  double ax = a->x, bx = b->x, cx = c->x, ay = a->y, by = b->y, cy = c->y;
  double x0 = py_min3(ax, bx, cx), y0 = py_min3(ay, by, cy);
  double x1 = py_max3(ax, bx, cx), y1 = py_max3(ay, by, cy);
  enode *p = c->next;
  while (p != a) {
    if (x0 <= p->x && p->x <= x1 && y0 <= p->y && p->y <= y1 &&
        e_point_in_triangle_except_first(ax, ay, bx, by, cx, cy, p->x, p->y) && e_area(p->prev, p, p->next) >= 0)
      return 0;
    p = p->next;
  }
  return 1;
}

static int e_is_ear_hashed(enode *ear, double min_x, double min_y, double inv_size) {
  enode *a = ear->prev, *b = ear, *c = ear->next;
  if (e_area(a, b, c) >= 0) return 0;
  double ax = a->x, bx = b->x, cx = c->x, ay = a->y, by = b->y, cy = c->y;
  double x0 = py_min3(ax, bx, cx), y0 = py_min3(ay, by, cy);
  double x1 = py_max3(ax, bx, cx), y1 = py_max3(ay, by, cy);
  i32 min_z = e_z_order(x0, y0, min_x, min_y, inv_size);
  i32 max_z = e_z_order(x1, y1, min_x, min_y, inv_size);
  enode *p = ear->prev_z, *n = ear->next_z;
#define E_INSIDE(q)                                                                                       \
  (x0 <= (q)->x && (q)->x <= x1 && y0 <= (q)->y && (q)->y <= y1 && (q) != a && (q) != c &&               \
   e_point_in_triangle_except_first(ax, ay, bx, by, cx, cy, (q)->x, (q)->y) && e_area((q)->prev, (q), (q)->next) >= 0)
  while (p && p->z >= min_z && n && n->z <= max_z) {
    if (E_INSIDE(p)) return 0;
    p = p->prev_z;
    if (E_INSIDE(n)) return 0;
    n = n->next_z;
  }
  while (p && p->z >= min_z) {
    if (E_INSIDE(p)) return 0;
    p = p->prev_z;
  }
  while (n && n->z <= max_z) {
    if (E_INSIDE(n)) return 0;
    n = n->next_z;
  }
#undef E_INSIDE
  return 1;
}

static inline int e_sign(double num) { return num > 0 ? 1 : num < 0 ? -1 : 0; }

static inline int e_on_segment(const enode *p, const enode *q, const enode *r) {
  return py_max(p->x, r->x) >= q->x && q->x >= py_min(p->x, r->x) && py_max(p->y, r->y) >= q->y &&
         q->y >= py_min(p->y, r->y);
}

static int e_intersects(const enode *p1, const enode *q1, const enode *p2, const enode *q2) {
  int o1 = e_sign(e_area(p1, q1, p2));
  int o2 = e_sign(e_area(p1, q1, q2));
  int o3 = e_sign(e_area(p2, q2, p1));
  int o4 = e_sign(e_area(p2, q2, q1));
  if (o1 != o2 && o3 != o4) return 1;
  if (o1 == 0 && e_on_segment(p1, p2, q1)) return 1;
  if (o2 == 0 && e_on_segment(p1, q2, q1)) return 1;
  if (o3 == 0 && e_on_segment(p2, p1, q2)) return 1;
  if (o4 == 0 && e_on_segment(p2, q1, q2)) return 1;
  return 0;
}

static int e_locally_inside(const enode *a, const enode *b) {
  if (e_area(a->prev, a, a->next) < 0) return e_area(a, b, a->next) >= 0 && e_area(a, a->prev, b) >= 0;
  return e_area(a, b, a->prev) < 0 || e_area(a, a->next, b) < 0;
}

static enode *e_cure_local_intersections(earcut_state *st, enode *start) {
  enode *p = start;
  for (;;) {
    enode *a = p->prev, *b = p->next->next;
    if (!e_equals(a, b) && e_intersects(a, p, p->next, b) && e_locally_inside(a, b) && e_locally_inside(b, a)) {
      PUSH3(st->a, *st->triangles, a->i, p->i, b->i);
      e_remove(p);
      e_remove(p->next);
      p = start = b;
    }
    p = p->next;
    if (p == start) break;
  }
  return e_filter_points(p, NULL);
}

static int e_intersects_polygon(const enode *a, const enode *b) {
  const enode *p = a;
  for (;;) {
    if (p->i != a->i && p->next->i != a->i && p->i != b->i && p->next->i != b->i && e_intersects(p, p->next, a, b))
      return 1;
    p = p->next;
    if (p == a) break;
  }
  return 0;
}

static int e_middle_inside(const enode *a, const enode *b) {
  const enode *p = a;
  int inside = 0;
  double px = (a->x + b->x) / 2, py = (a->y + b->y) / 2;
  for (;;) {
    if (((p->y > py) != (p->next->y > py)) && p->next->y != p->y &&
        (px < (p->next->x - p->x) * (py - p->y) / (p->next->y - p->y) + p->x))
      inside = !inside;
    p = p->next;
    if (p == a) break;
  }
  return inside;
}

static int e_is_valid_diagonal(const enode *a, const enode *b) {
  return a->next->i != b->i && a->prev->i != b->i && !e_intersects_polygon(a, b) &&
         ((e_locally_inside(a, b) && e_locally_inside(b, a) && e_middle_inside(a, b) &&
           (e_area(a->prev, a, b->prev) != 0 || e_area(a, b->prev, b) != 0)) ||
          (e_equals(a, b) && e_area(a->prev, a, a->next) > 0 && e_area(b->prev, b, b->next) > 0));
}

static enode *e_split_polygon(earcut_state *st, enode *a, enode *b) {
  enode *a2 = e_node(st, a->i, a->x, a->y);
  enode *b2 = e_node(st, b->i, b->x, b->y);
  enode *an = a->next, *bp = b->prev;
  a->next = b;
  b->prev = a;
  a2->next = an;
  an->prev = a2;
  b2->next = a2;
  a2->prev = b2;
  bp->next = b2;
  b2->prev = bp;
  return b2;
}

static void e_earcut_linked(earcut_state *st, enode *ear, double min_x, double min_y, double inv_size, int pass);

static void e_split_earcut(earcut_state *st, enode *start, double min_x, double min_y, double inv_size) {
  enode *a = start;
  for (;;) {
    enode *b = a->next->next;
    while (b != a->prev) {
      if (a->i != b->i && e_is_valid_diagonal(a, b)) {
        enode *c = e_split_polygon(st, a, b);
        a = e_filter_points(a, a->next);
        c = e_filter_points(c, c->next);
        e_earcut_linked(st, a, min_x, min_y, inv_size, 0);
        e_earcut_linked(st, c, min_x, min_y, inv_size, 0);
        return;
      }
      b = b->next;
    }
    a = a->next;
    if (a == start) break;
  }
}

static void e_earcut_linked(earcut_state *st, enode *ear, double min_x, double min_y, double inv_size, int pass) {
  if (!ear) return;
  if (++st->depth > EARCUT_MAX_DEPTH) fail(st->r, E_ERROR);
  if (!pass && inv_size != 0.0) e_index_curve(ear, min_x, min_y, inv_size);
  enode *stop = ear;
  while (ear->prev != ear->next) {
    enode *prev = ear->prev, *next = ear->next;
    if (inv_size != 0.0 ? e_is_ear_hashed(ear, min_x, min_y, inv_size) : e_is_ear(ear)) {
      PUSH3(st->a, *st->triangles, prev->i, ear->i, next->i);
      e_remove(ear);
      ear = next->next;
      stop = next->next;
      continue;
    }
    ear = next;
    if (ear == stop) {
      if (!pass) {
        e_earcut_linked(st, e_filter_points(ear, NULL), min_x, min_y, inv_size, 1);
      } else if (pass == 1) {
        ear = e_cure_local_intersections(st, e_filter_points(ear, NULL));
        e_earcut_linked(st, ear, min_x, min_y, inv_size, 2);
      } else if (pass == 2) {
        e_split_earcut(st, ear, min_x, min_y, inv_size);
      }
      break;
    }
  }
  st->depth--;
}

static enode *e_get_leftmost(enode *start) {
  enode *p = start, *leftmost = start;
  for (;;) {
    if (p->x < leftmost->x || (p->x == leftmost->x && p->y < leftmost->y)) leftmost = p;
    p = p->next;
    if (p == start) break;
  }
  return leftmost;
}

static double e_compare_xy_slope(const enode *a, const enode *b) {
  double result = a->x - b->x;
  if (result == 0) {
    result = a->y - b->y;
    if (result == 0) {
      /* JavaScript's division: by zero an infinity or NaN, as IEEE division is. */
      double a_slope = (a->next->y - a->y) / (a->next->x - a->x);
      double b_slope = (b->next->y - b->y) / (b->next->x - b->x);
      result = a_slope - b_slope;
    }
  }
  return result;
}

static int e_less_xy_slope(const void *a, const void *b, void *ctx) {
  (void)ctx;
  return e_compare_xy_slope(*(enode *const *)a, *(enode *const *)b) < 0;
}

static int e_sector_contains_sector(const enode *m, const enode *p) {
  return e_area(m->prev, m, p->prev) < 0 && e_area(p->next, m, m->next) < 0;
}

static enode *e_find_hole_bridge(enode *hole, enode *outer) {
  enode *p = outer;
  double hx = hole->x, hy = hole->y, qx = -INFINITY;
  enode *m = NULL;
  if (e_equals(hole, p)) return p;
  for (;;) {
    if (e_equals(hole, p->next)) return p->next;
    if (hy <= p->y && hy >= p->next->y && p->next->y != p->y) {
      double x = p->x + (hy - p->y) * (p->next->x - p->x) / (p->next->y - p->y);
      if (x <= hx && x > qx) {
        qx = x;
        m = p->x < p->next->x ? p : p->next;
        if (x == hx) return m;
      }
    }
    p = p->next;
    if (p == outer) break;
  }
  if (!m) return NULL;
  enode *stop = m;
  double mx = m->x, my = m->y, tan_min = INFINITY;
  p = m;
  for (;;) {
    if (hx >= p->x && p->x >= mx && hx != p->x &&
        e_point_in_triangle(hy < my ? hx : qx, hy, mx, my, hy < my ? qx : hx, hy, p->x, p->y)) {
      double tan = fabs(hy - p->y) / (hx - p->x);
      if (e_locally_inside(p, hole) &&
          (tan < tan_min || (tan == tan_min && (p->x > m->x || (p->x == m->x && e_sector_contains_sector(m, p)))))) {
        m = p;
        tan_min = tan;
      }
    }
    p = p->next;
    if (p == stop) break;
  }
  return m;
}

static enode *e_eliminate_hole(earcut_state *st, enode *hole, enode *outer) {
  enode *bridge = e_find_hole_bridge(hole, outer);
  if (!bridge) return outer;
  enode *bridge_reverse = e_split_polygon(st, bridge, hole);
  e_filter_points(bridge_reverse, bridge_reverse->next);
  return e_filter_points(bridge, bridge->next);
}

static enode *e_eliminate_holes(earcut_state *st, const double *data, i64 length, const i64 *holes, i64 hole_count,
                                enode *outer) {
  enode **queue = (enode **)arena_alloc(st->a, sizeof(enode *) * (size_t)(hole_count ? hole_count : 1));
  for (i64 i = 0; i < hole_count; i++) {
    i64 start = holes[i] * 2;
    i64 end = i < hole_count - 1 ? holes[i + 1] * 2 : length;
    enode *ring = e_linked_list(st, data, start, end, 0);
    if (!ring) fail(st->r, E_ERROR); /* an empty ring fails, as it raised in the port */
    if (ring == ring->next) ring->steiner = 1;
    queue[i] = e_get_leftmost(ring);
  }
  merge_sort(st->a, queue, hole_count, sizeof(enode *), e_less_xy_slope, NULL);
  for (i64 i = 0; i < hole_count; i++) outer = e_eliminate_hole(st, queue[i], outer);
  return outer;
}

/* earcut(data, hole_indices): flat coordinates, each hole's first point; flat triangles out. */
static void earcut(run *r, arena *a, const double *data, i64 length, const i64 *holes, i64 hole_count, vi *triangles) {
  earcut_state st = {r, a, triangles, 0};
  int has_holes = hole_count > 0;
  i64 outer_len = has_holes ? holes[0] * 2 : length;
  enode *outer = e_linked_list(&st, data, 0, outer_len, 1);
  if (!outer || outer->next == outer->prev) return;
  double min_x = 0.0, min_y = 0.0, inv_size = 0.0;
  if (has_holes) outer = e_eliminate_holes(&st, data, length, holes, hole_count, outer);
  if (length > 80 * 2) {
    min_x = data[0];
    min_y = data[1];
    double max_x = min_x, max_y = min_y;
    for (i64 i = 2; i < outer_len; i += 2) {
      double x = data[i], y = data[i + 1];
      if (x < min_x) min_x = x;
      if (y < min_y) min_y = y;
      if (x > max_x) max_x = x;
      if (y > max_y) max_y = y;
    }
    inv_size = py_max(max_x - min_x, max_y - min_y);
    inv_size = inv_size != 0 ? 32767 / inv_size : 0.0;
  }
  e_earcut_linked(&st, outer, min_x, min_y, inv_size, 0);
}

/* A ring of points: (x, y) pairs. */
typedef struct {
  double *p;
  i64 n;
} poly;

/* three.js's ShapeUtils.triangulateShape. A closing repeat of a ring's first point is dropped
 * from the ring itself (its count lowered), as there. Triangles index the contour's points,
 * then each hole's. */
static void triangulate_shape(run *r, arena *a, poly *contour, poly *holes, i64 hole_count, vi *out) {
#define DROP_REPEAT(ring) \
  if ((ring)->n > 2 && (ring)->p[2 * ((ring)->n - 1)] == (ring)->p[0] && (ring)->p[2 * ((ring)->n - 1) + 1] == (ring)->p[1]) \
    (ring)->n--;
  DROP_REPEAT(contour);
  i64 total = contour->n;
  for (i64 h = 0; h < hole_count; h++) {
    DROP_REPEAT(&holes[h]);
    total += holes[h].n;
  }
#undef DROP_REPEAT
  double *data = (double *)arena_alloc(a, sizeof(double) * (size_t)(2 * (total ? total : 1)));
  i64 *starts = (i64 *)arena_alloc(a, sizeof(i64) * (size_t)(hole_count ? hole_count : 1));
  memcpy(data, contour->p, sizeof(double) * (size_t)(2 * contour->n));
  i64 at = contour->n;
  for (i64 h = 0; h < hole_count; h++) {
    starts[h] = at;
    memcpy(data + 2 * at, holes[h].p, sizeof(double) * (size_t)(2 * holes[h].n));
    at += holes[h].n;
  }
  out->n = 0;
  earcut(r, a, data, 2 * total, starts, hole_count, out);
}
