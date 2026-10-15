"""What cadgen meshes a STEP model's components at for display: the one tessellation policy.

Every display mesh is cadgen's -- OCCT's ``BRepMesh`` triangulates each component and cadgen's
native mesh library verifies every face of the result against the exact geometry and repairs or
replaces what fails (``cadgen._internal.tessellate``, ``cadgen.store.meshes``) -- and so are the
judgements the pipeline is driven by:

- **The display tolerance** (:data:`DISPLAY_TESSELLATION`): the one pair every
  display mesh is made at -- the CAD Viewer's, the CAD app's, a snapshot's, a
  robot primitive's -- and the pair a mesh export that names none is written at,
  so that export is served from the very mesh the viewer draws. Chord tolerance
  is RELATIVE to each component's bounding diagonal, angle tolerance is radians.
  It is this module's alone, configurable nowhere, and no page knows it: a page
  reads each part's one display mesh by the part's key (``cadgen.store.parts``),
  which names no tolerance.
- **The verification bounds** (:data:`VERIFICATION`): how far, in deflections, a
  sample of a triangle may lie from the exact surface before the library repairs
  the triangle, and how far a face may still lie after the repair's rounds before
  it is drawn as unverified and named.
- **The cost guard** (:func:`cost_guard`): the one class of surface OCCT's
  deflection control does not survive, read off the surface alone, which the
  library meshes from the start.
- **The bounds** (:data:`TESSELLATION_FLOORS`, :data:`TESSELLATION_CEILINGS`):
  the finest and the coarsest a mesh export may ask to have meshed. One rule
  (:func:`tolerance_refusal`) holds them at every door a tolerance enters -- a
  mesh decorator's argument or a mesh door's flag (``cadgen.metadata``), the mesh
  export they reach (``cadgen._internal.mesh_export``) -- so a request outside
  them is refused where it enters, in words naming the bound, and never meshed.
"""

from __future__ import annotations

import math

__all__ = [
    "DISPLAY_TESSELLATION",
    "TESSELLATION_CEILINGS",
    "TESSELLATION_FLOORS",
    "VERIFICATION",
    "cost_guard",
    "tolerance_refusal",
]

# The one pair every display mesh is made at: a part 1,000 px across strays half a
# pixel from its exact surface, and a round face turns at most 0.35 rad (20 degrees)
# from one vertex's normal to its neighbour's.
DISPLAY_TESSELLATION: dict[str, float] = {"chordTolerance": 5e-4, "angleTolerance": 0.35}

# The library measures every vertex, and every triangle's centroid, edge midpoints and an
# interior point, against the exact surface. A sample past ``repairAt`` deflections off it has
# its triangle refined in place; a face any sample of which is still past ``verifiedAt``
# deflections off after the repair's rounds is drawn and named as unverified
# (``unverifiedFaces``). OCCT's own control holds a triangle to the deflection at the points it
# measures, so at ``repairAt`` 1 every triangle it left a hair over is split once; a bound of
# twice the deflection is what the contract allows a drawn triangle.
VERIFICATION: dict[str, float] = {"repairAt": 1.0, "verifiedAt": 2.0}

# The finest tolerances anything may ask to have meshed: past any display or print
# need (a sphere 2,000 px across strays a third of a pixel at either), and the
# finest at which a face curved all the way round both ways -- a torus, a sphere, a
# closed freeform loft, the faces with the most triangles -- still meshes in well under
# a second. Finer, the triangles grow as the inverse square of the tolerance: at
# 0.005 rad a model of four primitives is 12.9 million triangles, a mesh no viewer needs.
TESSELLATION_FLOORS: dict[str, float] = {"chordTolerance": 5e-5, "angleTolerance": 0.05}
# The coarsest: past a twentieth of the bounding diagonal, or a quarter turn
# between neighbouring facets (a circle as a square), a mesh no longer follows the
# part. A chord tolerance above it is, in practice, an absolute millimetre
# deflection carried over from a mesher that took one.
TESSELLATION_CEILINGS: dict[str, float] = {"chordTolerance": 0.05, "angleTolerance": math.pi / 2}
_UNITS = {"chordTolerance": "of each component's bounding diagonal", "angleTolerance": "radians"}

# The cost guard's thresholds (``cost_guard``): the degree a span must have, and how many spans
# the direction must run, for a rational single-span closed B-spline surface to go to the library.
GUARD_SPAN_DEGREE = 8
GUARD_SPANS = 32


def cost_guard(surface: dict) -> bool:
    """Whether a B-spline surface is one OCCT's deflection control does not survive, so the
    library meshes its faces from the start.

    ``surface`` is the surface's signature: ``degree`` and ``poles`` as ``(u, v)`` pairs,
    ``spans`` the knot spans ``(u, v)``, ``rational`` and ``periodic`` as ``(u, v)``. The class is
    the swept tube whose section is a full circle written as ONE rational span of degree six
    (poles no more than the degree plus one, closed on itself) and whose path is a chain of dozens
    of high-degree spans: the section's parameter runs many times faster on one side than the
    other, and OCCT, measuring its deflection in parameters, splits each of the hundreds of rows
    the path's spans give it again and again. On the sample corpus such faces cost it 6 to 218
    seconds and 150,000 to 610,000 triangles each, still eight deflections off their surface, where
    every other B-spline face it meets meshes at its normal rate; no face it meshes in under three
    seconds matches the class. The library meshes them in a fraction of a second and the verifier
    names what stays off tolerance, which no mesher brings on.
    """
    if not surface.get("rational"):
        return False
    degree, poles, spans, periodic = surface["degree"], surface["poles"], surface["spans"], surface["periodic"]
    for closed, along in ((0, 1), (1, 0)):
        if (periodic[closed] and poles[closed] <= degree[closed] + 1 and degree[along] >= GUARD_SPAN_DEGREE
                and spans[along] >= GUARD_SPANS):
            return True
    return False


def tolerance_refusal(key: str, value: float, *, name: str) -> str | None:
    """Why ``value`` is no ``key`` (``chordTolerance`` or ``angleTolerance``) anything
    may be meshed at, in words that call it ``name`` -- what the door it came through
    calls it -- and name the bound it crosses; ``None`` when it is one.

    ``value`` is a finite positive number: each door refuses anything else first."""
    floor, ceiling, unit = TESSELLATION_FLOORS[key], TESSELLATION_CEILINGS[key], _UNITS[key]
    default = DISPLAY_TESSELLATION[key]
    if value < floor:
        return (f"{name} {value:g} is finer than cadgen meshes: it must be at least {floor:g} {unit} "
                f"(default {default:g})")
    if value <= ceiling:
        return None
    if key == "chordTolerance":
        return (f"{name} {value:g} is too large: the value is RELATIVE to each component's bounding "
                f"diagonal, not millimetres, so it must be at most {ceiling:g} (default {default:g}). For an "
                f"absolute chord deviation of X mm on a part whose bounding diagonal is D mm, pass X/D -- "
                f"{value:g} mm on a 200 mm part is {value / 200.0:g}")
    return (f"{name} {value:g} is too large: it must be at most {ceiling:g} {unit}, a quarter turn between "
            f"neighbouring facets (default {default:g})")
