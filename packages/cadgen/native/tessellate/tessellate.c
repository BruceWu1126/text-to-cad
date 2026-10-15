/*
 * tessellate: cadgen's mesh library -- the verifier, repairer and completer of a component's
 * display mesh (cadgen._internal.tessellate describes the pipeline it is one half of). One unity
 * build of the parts beside it, cross-compiled for every platform by zig into cadgen's native
 * runtime directory, loaded with ctypes, and given nothing but numbers: the host meshes the
 * component with OCCT, reads the BREP and OCCT's triangulation through OCCT, and evaluates
 * anything here has no formula for.
 *
 * Its arithmetic is written operation for operation and must be built without floating-point
 * contraction (-ffp-contract=off) or fast-math: a fused multiply-add rounds differently, and the
 * same arithmetic would not run the same on every platform.
 */

#include "base.c"
#include "geometry.c"
#include "earcut.c"
#include "sampling.c"
#include "grid.c"
#include "refine.c"
#include "conform.c"
#include "verify.c"
#include "api.c"
