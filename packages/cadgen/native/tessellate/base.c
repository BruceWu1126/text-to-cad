/*
 * Part of tessellate.c's unity build: what every other part stands on.
 *
 * Memory comes from arenas, freed whole: a run's own (what outlives a face) and a scratch one each
 * face's working state comes from, reset after it. A failure unwinds to the nearest guard
 * (`ENTER`, `setjmp`) by longjmp, carrying its kind: a face too narrow to resolve, or any other
 * error, which leaves the face out or fails the component.
 *
 * The arithmetic is Python's, operation for operation, as the Python port of the browser's
 * tessellator that this library was proven against byte for byte had it: `py_min`/`py_max` are
 * its builtins (the first argument kept unless the second is strictly less or greater, so a NaN
 * never wins), `py_mod1` its `x % 1`, `py_floor`/`py_ceil` its math.floor/ceil (which fail on a
 * NaN or an infinity), and sorts are stable, as its sort is.
 */

#include <float.h>
#include <math.h>
#include <setjmp.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif

typedef int32_t i32;
typedef int64_t i64;
typedef uint32_t u32;
typedef uint64_t u64;

#define PY_EPSILON DBL_EPSILON

/* --- failures ---------------------------------------------------------------------------- */

enum {
  E_NONE = 0,
  E_UNRESOLVED = 1, /* a face narrower than its tolerance: drawn as nothing, not named */
  E_ERROR = 2,      /* anything else that cannot be meshed */
  E_MEMORY = 3,
  E_CALLBACK = 4,   /* an evaluation the host was asked for failed */
};

typedef struct run run;

typedef struct guard {
  jmp_buf jump;
  struct guard *outer;
  int code; /* the failure that unwound to it */
} guard;

/* --- arenas ------------------------------------------------------------------------------ */

typedef struct block {
  struct block *next;
  size_t used, cap;
  /* data follows, 16-aligned */
} block;

typedef struct {
  block *head;
  run *owner;
} arena;

#define BLOCK_HEADER ((sizeof(block) + 15) & ~(size_t)15)

/* sin and cos of an angle, as libm gives them: a direct-mapped table of the angles a run has
 * turned, since a vertex's point and normal, a grid column and a split's midpoint keep coming
 * back to the same ones. */
#define TRIG_BITS 10
typedef struct {
  u64 bits;
  double s, c;
} trig_entry;

/* One component's meshing: its guards and its arenas -- A for what outlives a face, F for a
 * face's own working state, T for temporaries a step releases. */
struct run {
  guard *guard;
  arena A, F, T;
  trig_entry trig[1 << TRIG_BITS];
};

static void run_init(run *R) {
  memset(R, 0, sizeof *R);
  R->A.owner = R->F.owner = R->T.owner = R;
  for (int k = 0; k < (1 << TRIG_BITS); k++) R->trig[k].c = 1.0; /* every entry +0.0's: sin 0, cos 1 */
}

static void fail(run *r, int code) {
  r->guard->code = code;
  longjmp(r->guard->jump, 1);
}

/* A guard: `ENTER(r, g)`, then `setjmp(g.jump)` -- 0 on the way in, the failure's code when one
 * unwinds to it -- and either way `LEAVE(r, g)` before the next failure can be raised past it. */
#define ENTER(r, g) ((g).outer = (r)->guard, (r)->guard = &(g))
#define LEAVE(r, g) ((r)->guard = (g).outer)

static void *arena_alloc(arena *a, size_t size) {
  size = (size + 15) & ~(size_t)15;
  block *b = a->head;
  if (!b || b->used + size > b->cap) {
    size_t cap = b ? b->cap * 2 : (size_t)1 << 16;
    if (cap > ((size_t)1 << 26)) cap = (size_t)1 << 26;
    if (cap < size) cap = size;
    block *fresh = (block *)malloc(BLOCK_HEADER + cap);
    if (!fresh) fail(a->owner, E_MEMORY);
    fresh->next = b;
    fresh->used = 0;
    fresh->cap = cap;
    a->head = b = fresh;
  }
  void *out = (char *)b + BLOCK_HEADER + b->used;
  b->used += size;
  return out;
}

static void *arena_zalloc(arena *a, size_t size) {
  void *out = arena_alloc(a, size);
  memset(out, 0, size);
  return out;
}

static void arena_free(arena *a) {
  block *b = a->head;
  while (b) {
    block *next = b->next;
    free(b);
    b = next;
  }
  a->head = NULL;
}

/* A point to return to: everything allocated after it is released by `arena_reset`. */
typedef struct {
  block *head;
  size_t used;
} arena_mark;

static arena_mark arena_save(arena *a) {
  arena_mark m = {a->head, a->head ? a->head->used : 0};
  return m;
}

static void arena_reset(arena *a, arena_mark m) {
  while (a->head && a->head != m.head) {
    block *next = a->head->next;
    free(a->head);
    a->head = next;
  }
  if (a->head) a->head->used = m.used;
}

/* --- growable arrays --------------------------------------------------------------------- */

static void *grow_to(arena *a, void *data, i64 len, i64 *cap, size_t elem, i64 need) {
  if (need <= *cap) return data;
  i64 fresh = *cap ? *cap * 2 : 8;
  while (fresh < need) fresh *= 2;
  void *out = arena_alloc(a, (size_t)fresh * elem);
  if (len) memcpy(out, data, (size_t)len * elem);
  *cap = fresh;
  return out;
}

#define VEC(T) \
  struct {     \
    T *v;      \
    i64 n, cap; \
  }

typedef VEC(double) vd;
typedef VEC(i32) vi;
typedef VEC(i64) vl;
typedef VEC(u64) vu;
typedef VEC(uint8_t) vb;

#define RESERVE(A, vec, need) \
  ((vec).v = grow_to((A), (vec).v, (vec).n, &(vec).cap, sizeof(*(vec).v), (need)))
#define PUSH(A, vec, x)                                           \
  do {                                                            \
    if ((vec).n == (vec).cap) RESERVE((A), (vec), (vec).n + 1);    \
    (vec).v[(vec).n++] = (x);                                     \
  } while (0)
#define PUSH2(A, vec, a_, b_) \
  do {                        \
    RESERVE((A), (vec), (vec).n + 2); \
    (vec).v[(vec).n++] = (a_); \
    (vec).v[(vec).n++] = (b_); \
  } while (0)
#define PUSH3(A, vec, a_, b_, c_) \
  do {                            \
    RESERVE((A), (vec), (vec).n + 3); \
    (vec).v[(vec).n++] = (a_);     \
    (vec).v[(vec).n++] = (b_);     \
    (vec).v[(vec).n++] = (c_);     \
  } while (0)

/* --- Python's arithmetic ----------------------------------------------------------------- */

static inline double py_min(double a, double b) { return b < a ? b : a; }
static inline double py_max(double a, double b) { return b > a ? b : a; }
static inline double py_min3(double a, double b, double c) { return py_min(py_min(a, b), c); }
static inline double py_max3(double a, double b, double c) { return py_max(py_max(a, b), c); }
static inline i64 imin(i64 a, i64 b) { return b < a ? b : a; }
static inline i64 imax(i64 a, i64 b) { return b > a ? b : a; }

/* x % 1.0, as Python computes a float's remainder: the sign of the divisor, never -0. */
static inline double py_mod1(double x) {
  double mod = fmod(x, 1.0);
  if (mod != 0.0) {
    if (mod < 0) mod += 1.0;
  } else {
    mod = 0.0;
  }
  return mod;
}

/* math.floor/math.ceil: a NaN or an infinity raises. The values here are far inside an i64. */
static i64 py_floor(run *r, double x) {
  if (!isfinite(x)) fail(r, E_ERROR);
  return (i64)floor(x);
}

static i64 py_ceil(run *r, double x) {
  if (!isfinite(x)) fail(r, E_ERROR);
  return (i64)ceil(x);
}

/* A float division whose divisor Python would refuse at zero. */
static inline double py_div(run *r, double a, double b) {
  if (b == 0.0) fail(r, E_ERROR);
  return a / b;
}

/* JavaScript's Math.round, exactly: the nearest integer, a half toward positive infinity. */
static i64 round_half_up(run *r, double value) {
  i64 whole = py_floor(r, value);
  return value - (double)whole >= 0.5 ? whole + 1 : whole;
}

static inline double dist3(const double *a, const double *b) {
  double dx = a[0] - b[0], dy = a[1] - b[1], dz = a[2] - b[2];
  return sqrt(dx * dx + dy * dy + dz * dz);
}

/* An edge of a mesh as one key, its lower index first. */
static inline u64 edge_key(i64 p, i64 q) {
  return p < q ? ((u64)p << 32 | (u64)q) : ((u64)q << 32 | (u64)p);
}

/* A float as a hash key's bits, its two zeros one key (Python's -0.0 == 0.0). */
static inline u64 key_bits(double x) {
  if (x == 0.0) x = 0.0;
  u64 bits;
  memcpy(&bits, &x, sizeof bits);
  return bits;
}

/* Python's f"{f:.12f}", as the key it is: the sign it prints and the number of its last
 * place, round half even on the exact binary value. */
typedef struct {
  i64 units;
  i32 negative;
} decimal12;

static decimal12 decimal12_of(double f) {
  decimal12 out = {0, signbit(f) ? 1 : 0};
  double magnitude = fabs(f);
  if (magnitude == 0.0 || !isfinite(magnitude)) {
    out.units = isfinite(magnitude) ? 0 : INT64_MAX;
    return out;
  }
  int exponent;
  double fraction = frexp(magnitude, &exponent); /* magnitude = fraction * 2^exponent */
  u64 mantissa = (u64)ldexp(fraction, 53);         /* exact */
  int shift = exponent - 53;                        /* magnitude = mantissa * 2^shift */
  unsigned __int128 product = (unsigned __int128)mantissa * (unsigned __int128)1000000000000ULL;
  if (shift >= 0) {
    if (shift > 20) {
      out.units = INT64_MAX;
      return out;
    }
    out.units = (i64)(product << shift);
    return out;
  }
  int right = -shift;
  if (right > 120) return out;
  unsigned __int128 quotient = product >> right;
  unsigned __int128 remainder = product - (quotient << right);
  unsigned __int128 half = (unsigned __int128)1 << (right - 1);
  if (remainder > half || (remainder == half && (quotient & 1))) quotient += 1;
  out.units = (i64)quotient;
  return out;
}

/* --- stable sort ------------------------------------------------------------------------- */

/* Merge sort, `less(a, b)` Python's `<`: equal elements keep their order. */
typedef int (*less_fn)(const void *a, const void *b, void *ctx);

static void merge_sort(arena *scratch, void *base, i64 count, size_t size, less_fn less, void *ctx) {
  if (count < 2) return;
  char *data = (char *)base;
  /* Binary insertion for short runs, then bottom-up merges. */
  const i64 run_length = 16;
  char *tmp = (char *)arena_alloc(scratch, size * 1);
  for (i64 start = 0; start < count; start += run_length) {
    i64 end = imin(start + run_length, count);
    for (i64 i = start + 1; i < end; i++) {
      memcpy(tmp, data + i * size, size);
      i64 lo = start, hi = i;
      while (lo < hi) {
        i64 mid = (lo + hi) >> 1;
        if (less(tmp, data + mid * size, ctx)) hi = mid;
        else lo = mid + 1;
      }
      if (lo < i) {
        memmove(data + (lo + 1) * size, data + lo * size, (size_t)(i - lo) * size);
        memcpy(data + lo * size, tmp, size);
      }
    }
  }
  if (count <= run_length) return;
  char *buffer = (char *)arena_alloc(scratch, (size_t)count * size);
  char *from = data, *to = buffer;
  for (i64 width = run_length; width < count; width *= 2) {
    for (i64 left = 0; left < count; left += 2 * width) {
      i64 mid = imin(left + width, count), right = imin(left + 2 * width, count);
      i64 i = left, j = mid, k = left;
      while (i < mid && j < right) {
        if (less(from + j * size, from + i * size, ctx)) {
          memcpy(to + k * size, from + j * size, size);
          j++;
        } else {
          memcpy(to + k * size, from + i * size, size);
          i++;
        }
        k++;
      }
      if (i < mid) memcpy(to + k * size, from + i * size, (size_t)(mid - i) * size), k += mid - i;
      if (j < right) memcpy(to + k * size, from + j * size, (size_t)(right - j) * size);
    }
    char *swap = from;
    from = to;
    to = swap;
  }
  if (from != data) memcpy(data, from, (size_t)count * size);
}

/* Numbers sorted where their order is all there is to keep (equal ones are the same bits):
 * insertion for a few, else least-significant-digit radix a byte a pass, a pass over a byte
 * every key shares skipped. */
static void sort_u64(arena *scratch, u64 *v, i64 n) {
  if (n < 64) {
    for (i64 i = 1; i < n; i++) {
      u64 x = v[i];
      i64 j = i - 1;
      while (j >= 0 && v[j] > x) {
        v[j + 1] = v[j];
        j--;
      }
      v[j + 1] = x;
    }
    return;
  }
  u64 all_and = ~(u64)0, all_or = 0;
  for (i64 i = 0; i < n; i++) {
    all_and &= v[i];
    all_or |= v[i];
  }
  u64 varies = all_and ^ all_or; /* the bits not every key shares */
  u64 *buffer = (u64 *)arena_alloc(scratch, sizeof(u64) * (size_t)n);
  u64 *from = v, *to = buffer;
  i64 count[256];
  for (int shift = 0; shift < 64; shift += 8) {
    if (!((varies >> shift) & 0xFF)) continue;
    memset(count, 0, sizeof count);
    for (i64 i = 0; i < n; i++) count[(from[i] >> shift) & 0xFF]++;
    i64 total = 0;
    for (int d = 0; d < 256; d++) {
      i64 c = count[d];
      count[d] = total;
      total += c;
    }
    for (i64 i = 0; i < n; i++) to[count[(from[i] >> shift) & 0xFF]++] = from[i];
    u64 *swap = from;
    from = to;
    to = swap;
  }
  if (from != v) memcpy(v, from, sizeof(u64) * (size_t)n);
}

static int less_double(const void *a, const void *b, void *ctx) {
  (void)ctx;
  return *(const double *)a < *(const double *)b;
}

/* bisect.bisect_left / bisect_right over a sorted array of floats. */
static i64 bisect_left_d(const double *values, i64 count, double x) {
  i64 lo = 0, hi = count;
  while (lo < hi) {
    i64 mid = (lo + hi) >> 1;
    if (values[mid] < x) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

static i64 bisect_right_d(const double *values, i64 count, double x) {
  i64 lo = 0, hi = count;
  while (lo < hi) {
    i64 mid = (lo + hi) >> 1;
    if (x < values[mid]) hi = mid;
    else lo = mid + 1;
  }
  return lo;
}

/* --- hash maps --------------------------------------------------------------------------- */

/* Open addressing over keys of 1 to 4 words, each slot's value an i64. Keys go in as the
 * caller makes them (floats through `key_bits`), and a map is only ever looked up, never
 * iterated: anything whose order matters keeps that order itself. A slot is its key's words then
 * its value, side by side, so a probe touches one line. */
typedef struct {
  u64 *slots; /* (words + 1) * capacity: each slot's key, then its value */
  uint8_t *used;
  i64 capacity, count;
  int words;
  arena *a;
} map;

static inline u64 mix64(u64 x) {
  x ^= x >> 30;
  x *= 0xbf58476d1ce4e5b9ULL;
  x ^= x >> 27;
  x *= 0x94d049bb133111ebULL;
  x ^= x >> 31;
  return x;
}

static inline u64 hash_words(const u64 *key, int words) {
  u64 h = mix64(key[0] + 0x9e3779b97f4a7c15ULL);
  for (int i = 1; i < words; i++) h = mix64(h ^ (key[i] + 0x9e3779b97f4a7c15ULL * (u64)(i + 1)));
  return h;
}

static inline int same_key(const u64 *a, const u64 *b, int words) {
  switch (words) {
    case 1: return a[0] == b[0];
    case 2: return a[0] == b[0] && a[1] == b[1];
    case 3: return a[0] == b[0] && a[1] == b[1] && a[2] == b[2];
    default: return a[0] == b[0] && a[1] == b[1] && a[2] == b[2] && a[3] == b[3];
  }
}

static void map_init(map *m, arena *a, int words, i64 expected) {
  i64 capacity = 16;
  while (capacity < expected * 2) capacity *= 2;
  m->a = a;
  m->words = words;
  m->capacity = capacity;
  m->count = 0;
  m->slots = (u64 *)arena_alloc(a, sizeof(u64) * (size_t)(capacity * (words + 1)));
  m->used = (uint8_t *)arena_zalloc(a, (size_t)capacity);
}

static inline i64 map_slot(const map *m, const u64 *key) {
  i64 mask = m->capacity - 1, stride = m->words + 1;
  i64 at = (i64)(hash_words(key, m->words) & (u64)mask);
  for (;;) {
    if (!m->used[at] || same_key(m->slots + at * stride, key, m->words)) return at;
    at = (at + 1) & mask;
  }
}

static void map_rehash(map *m) {
  map bigger;
  map_init(&bigger, m->a, m->words, m->capacity);
  i64 stride = m->words + 1;
  for (i64 i = 0; i < m->capacity; i++) {
    if (!m->used[i]) continue;
    i64 at = map_slot(&bigger, m->slots + i * stride);
    bigger.used[at] = 1;
    memcpy(bigger.slots + at * stride, m->slots + i * stride, sizeof(u64) * (size_t)stride);
    bigger.count++;
  }
  *m = bigger;
}

/* The value stored under `key`, or `missing`. */
static inline i64 map_get(const map *m, const u64 *key, i64 missing) {
  i64 at = map_slot(m, key);
  return m->used[at] ? (i64)m->slots[at * (m->words + 1) + m->words] : missing;
}

/* The value's slot, made (holding `initial`) when absent; *made says which. The slot moves when
 * the map next grows: use it before anything else goes in. */
static inline i64 *map_ref(map *m, const u64 *key, i64 initial, int *made) {
  if ((m->count + 1) * 2 > m->capacity) map_rehash(m);
  i64 at = map_slot(m, key), stride = m->words + 1;
  u64 *slot = m->slots + at * stride;
  if (!m->used[at]) {
    m->used[at] = 1;
    for (int i = 0; i < m->words; i++) slot[i] = key[i];
    slot[m->words] = (u64)initial;
    m->count++;
    if (made) *made = 1;
  } else if (made) {
    *made = 0;
  }
  return (i64 *)(slot + m->words);
}

static inline void map_put(map *m, const u64 *key, i64 value) { *map_ref(m, key, value, NULL) = value; }

static inline i64 map_get1(const map *m, u64 key, i64 missing) { return map_get(m, &key, missing); }
static inline void map_put1(map *m, u64 key, i64 value) { map_put(m, &key, value); }

/* A map from one-word keys (a mesh edge's `edge_key`, a vertex, an ordinal) to an i64: the hot
 * lookups of refinement, flips and conformity. No key is all ones, which marks an empty slot,
 * and the slot's index is the key's Fibonacci hash, so a probe is a multiply and a compare. */
#define ALWAYS_INLINE __attribute__((always_inline)) inline
#define EMPTY_KEY (~(u64)0)

typedef struct {
  u64 key;
  i64 value;
} eslot;

typedef struct {
  eslot *slots;
  i64 capacity, count;
  int shift;
  arena *a;
} emap;

static void emap_init(emap *m, arena *a, i64 expected) {
  i64 capacity = 16;
  int bits = 4;
  while (capacity < expected * 2) {
    capacity *= 2;
    bits++;
  }
  m->a = a;
  m->capacity = capacity;
  m->count = 0;
  m->shift = 64 - bits;
  m->slots = (eslot *)arena_alloc(a, sizeof(eslot) * (size_t)capacity);
  for (i64 i = 0; i < capacity; i++) m->slots[i].key = EMPTY_KEY;
}

static ALWAYS_INLINE eslot *emap_find(const emap *m, u64 key) {
  i64 mask = m->capacity - 1;
  i64 at = (i64)((key * 0x9e3779b97f4a7c15ULL) >> m->shift);
  for (;;) {
    eslot *s = &m->slots[at];
    if (s->key == key || s->key == EMPTY_KEY) return s;
    at = (at + 1) & mask;
  }
}

static void emap_grow(emap *m) {
  eslot *old = m->slots;
  i64 old_capacity = m->capacity;
  emap_init(m, m->a, m->count + 1);
  for (i64 i = 0; i < old_capacity; i++) {
    if (old[i].key == EMPTY_KEY) continue;
    *emap_find(m, old[i].key) = old[i];
    m->count++;
  }
}

static ALWAYS_INLINE i64 emap_get(const emap *m, u64 key, i64 missing) {
  const eslot *s = emap_find(m, key);
  return s->key == key ? s->value : missing;
}

/* The value's slot, made (holding `initial`) when absent; *made says which. The slot moves when
 * the map next grows: use it before anything else goes in. */
static ALWAYS_INLINE i64 *emap_ref(emap *m, u64 key, i64 initial, int *made) {
  if ((m->count + 1) * 2 > m->capacity) emap_grow(m);
  eslot *s = emap_find(m, key);
  if (s->key != key) {
    s->key = key;
    s->value = initial;
    m->count++;
    if (made) *made = 1;
  } else if (made) {
    *made = 0;
  }
  return &s->value;
}

static ALWAYS_INLINE void emap_put(emap *m, u64 key, i64 value) { *emap_ref(m, key, value, NULL) = value; }

/* Lists under keys, each list's items in the order they were added: `lists_add` appends,
 * `lists_find` names a key's list (-1 for none), and its items are `items(list)`. */
typedef struct {
  map index;      /* key -> list */
  VEC(vl) lists;  /* the lists, in the order their keys first came */
  arena *a;
} multimap;

static void multimap_init(multimap *mm, arena *a, int words, i64 expected) {
  map_init(&mm->index, a, words, expected);
  mm->lists.v = NULL;
  mm->lists.n = mm->lists.cap = 0;
  mm->a = a;
}

static i64 multimap_list(multimap *mm, const u64 *key) {
  int made;
  i64 *slot = map_ref(&mm->index, key, -1, &made);
  if (made) {
    vl empty = {NULL, 0, 0};
    *slot = mm->lists.n;
    PUSH(mm->a, mm->lists, empty);
  }
  return *slot;
}

static inline void multimap_add(multimap *mm, const u64 *key, i64 item) {
  i64 list = multimap_list(mm, key);
  PUSH(mm->a, mm->lists.v[list], item);
}

static inline i64 multimap_find(const multimap *mm, const u64 *key) { return map_get(&mm->index, key, -1); }
