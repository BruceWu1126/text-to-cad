"""What cadgen meshes a STEP model's components at for display: the one tessellation policy.

Every display mesh is cadgen's (its tessellator on the exact BREP, ``cadgen.store.meshes``),
and so is the choice of tolerances it is made at:

- **The display tolerance** (:data:`DISPLAY_TESSELLATION`): the one pair every
  display mesh is made at -- the CAD Viewer's, the CAD app's, a snapshot's, a
  robot primitive's -- and the pair a mesh export that names none is written at,
  so that export is served from the very mesh the viewer draws. Chord tolerance
  is RELATIVE to each component's bounding diagonal, angle tolerance is radians.
  It is this module's alone, configurable nowhere, and no page knows it: a page
  reads each part's one display mesh by the part's key (``cadgen.store.parts``),
  which names no tolerance.
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
    "tolerance_refusal",
]

# The one pair every display mesh is made at: a part 1,000 px across strays half a
# pixel from its exact surface, and a round face turns at most 0.35 rad (20 degrees)
# from one vertex's normal to its neighbour's.
DISPLAY_TESSELLATION: dict[str, float] = {"chordTolerance": 5e-4, "angleTolerance": 0.35}

# The finest tolerances anything may ask to have meshed: past any display or print
# need (a sphere 2,000 px across strays a third of a pixel at either), and the
# finest at which a face curved all the way round both ways -- a torus, a sphere, a
# closed freeform loft, the faces with the most triangles -- still meshes in about a
# second. Measured on such faces (cadgen's tessellator, one core): at 0.05 rad a
# torus is 95k triangles in 1.0 s of CPU, a sphere 31k in 0.2 s and a closed loft
# 29k in 0.4 s; at a chord of 5e-5 a torus is 135k triangles in 1.5 s, a sphere 92k
# in 0.6 s and a loft 50k in 0.8 s. Finer, the triangles grow as the inverse square
# of the tolerance: at 0.005 rad a model of four primitives is 12.9 million
# triangles in 80 s, a mesh no viewer needs.
TESSELLATION_FLOORS: dict[str, float] = {"chordTolerance": 5e-5, "angleTolerance": 0.05}
# The coarsest: past a twentieth of the bounding diagonal, or a quarter turn
# between neighbouring facets (a circle as a square), a mesh no longer follows the
# part. A chord tolerance above it is, in practice, an absolute millimetre
# deflection carried over from a mesher that took one.
TESSELLATION_CEILINGS: dict[str, float] = {"chordTolerance": 0.05, "angleTolerance": math.pi / 2}
_UNITS = {"chordTolerance": "of each component's bounding diagonal", "angleTolerance": "radians"}


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

