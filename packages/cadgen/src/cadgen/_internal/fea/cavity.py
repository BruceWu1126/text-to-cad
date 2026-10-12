"""The inside of a container: the space a liquid fills in a tank, a cup or a channel, and its mesh.

The cavity is an OCP boolean: the part's bounding box, exact on every side the
part opens on (a face lying flat on that side with a hole in it: an open top)
and standing off the part on the others, minus the part. The air around the
part touches a stand-off side; the inside does not. So the cavity is the
largest solid of the cut that touches no side but the part's own openings: a
sealed tank's inside touches none, an open tank's touches its open top.

Each face of the cavity is a wall (an image of one of the part's faces through
the boolean's history, carrying the part's ``#o1.fN`` ref, so the pressure
lands on the face the viewer shows) or an opening (flat on one of the part's
open sides, ``x_min`` ... ``z_max``). The mesh is netgen's linear tetrahedra,
through the fluid region's mesher (:mod:`.fluid_domain`), optionally finer in a
band (the sweep of a free surface).

Numeric imports live inside the functions.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from cadgen._internal.fea.fluid_domain import OPENINGS

if TYPE_CHECKING:
    from cadgen._internal.fea.fluid_domain import FluidDomain
    from cadgen._internal.fea.mesh import VolumeMesh

__all__ = ["OPENINGS", "build_cavity", "mesh_cavity", "skfem_mesh"]

#: The box's stand-off from the part on its closed sides, as a share of the part's diagonal.
STANDOFF = 0.1


def build_cavity(part, *, refs: dict[int, str] | None = None) -> "FluidDomain":
    """The space inside ``part`` (an OCP shape, placed) and what each of its faces is.

    The domain's ``kind`` is "cavity"; its faces are "wall" or "opening" (with the side, ``opening``).
    ValueError, in words, when the part has no inside a liquid could fill.
    """
    from cadgen._internal.fea import fluid_domain as fd

    started = time.perf_counter()
    low, high = fd.bounds(part)
    extent = [h - l for l, h in zip(low, high)]
    diagonal = sum(e * e for e in extent) ** 0.5
    if not diagonal > 0:
        raise RuntimeError("the part has no volume to hold a liquid")
    tol = 1e-6 * max(diagonal, 1.0)
    open_sides = fd.opening_sides(part, low, high, tol)
    box_low, box_high = fd.standoff_box(low, high, open_sides, STANDOFF * diagonal)
    cut = fd.box_cut(box_low, box_high, part, "the boolean that finds the inside of the part failed")
    inside = []
    for solid in fd.solids_of(cut.Shape()):
        sides = fd.sides_of(solid, box_low, box_high, tol)
        if sides <= open_sides:
            inside.append((fd.volume_of(solid), solid, sides))
    if not inside:
        raise ValueError("multiphase: the part has no inside to hold a liquid; it needs a cavity (a tank, a cup, a "
                         "channel closed at the bottom), open at most on a side of its bounding box")
    volume, solid, sides = max(inside, key=lambda item: item[0])

    def role_of(face):
        side = fd.plane_of(face, box_low, box_high, tol)
        return ("opening", side) if side in sides else None

    faces = fd.classify_faces(solid, cut, part, refs or {}, role_of)
    clow, chigh = fd.bounds(solid)
    return fd.FluidDomain(
        shape=solid, kind="cavity", faces=faces, volume_mm3=volume,
        part_box=(tuple(low), tuple(high)), box=(tuple(clow), tuple(chigh)),
        length_mm=max(h - l for l, h in zip(clow, chigh)),
        seconds=time.perf_counter() - started, wetted={face.ordinal for face in faces if face.ordinal},
    )


def mesh_cavity(domain: "FluidDomain", size_mm: float, *, band: dict | None = None) -> "VolumeMesh":
    """netgen's linear tetrahedra of the cavity at ``size_mm``; ``band`` ``{"points": [[x, y, z, h], ...],
    "radius_mm": r}`` meshes finer around its points. ``boundary_ordinal`` is each boundary triangle's cavity face index."""
    from cadgen._internal.fea.fluid_domain import mesh_domain

    return mesh_domain(domain, "cavity", float(size_mm), 1, band, "the mesher produced no elements inside the part",
                       check=False)


def skfem_mesh(volume: "VolumeMesh") -> tuple[Any, Any]:
    """The cavity's mesh as skfem's (corners only, metres), and each skfem boundary facet's cavity face index."""
    import numpy as np
    from skfem import MeshTet

    used = np.unique(volume.tets)
    renumber = np.full(len(volume.nodes), -1, dtype=np.int64)
    renumber[used] = np.arange(len(used))
    mesh = MeshTet(np.ascontiguousarray(volume.nodes[used].T * 1e-3), np.ascontiguousarray(renumber[volume.tets].T))
    face_of = {tuple(sorted(renumber[tri])): int(o) for tri, o in zip(volume.boundary, volume.boundary_ordinal)}
    boundary = mesh.boundary_facets()
    keys = np.sort(mesh.facets[:, boundary], axis=0).T
    facet_face = np.array([face_of.get(tuple(int(v) for v in key), 0) for key in keys], dtype=np.int64)
    return mesh, (boundary, facet_face)
