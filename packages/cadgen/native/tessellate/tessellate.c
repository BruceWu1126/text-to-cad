/*
 * tessellate: a component's display mesh, the tessellator cadgen's CAD Viewer ran in the browser
 * (cadgen._internal.tessellate describes it), compiled. One unity build of the parts beside it,
 * cross-compiled for every platform by zig (scripts/bundle/cadgen-runtime.sh) into
 * cadgen/_runtime/native, loaded with ctypes, and given nothing but numbers: the host reads the
 * BREP through OCCT and evaluates anything here has no formula for.
 *
 * It was proven byte for byte against a Python port of that tessellator, and its arithmetic is
 * written operation for operation, so it must be built without floating-point contraction
 * (-ffp-contract=off) or fast-math: a fused multiply-add rounds differently, and the same
 * arithmetic would not run on every platform.
 */

#include "base.c"
#include "geometry.c"
#include "earcut.c"
#include "sampling.c"
#include "grid.c"
#include "refine.c"
#include "conform.c"
#include "api.c"
