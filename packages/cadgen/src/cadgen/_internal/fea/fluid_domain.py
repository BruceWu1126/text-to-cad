"""The fluid a flow study solves in: an OCP boolean around (or through) the part, and its mesh.

Internal flow (through a pipe, a manifold, a channel): the part's bounding box,
exact on the opening planes the study names and a little larger on its other
sides, minus the part. What is left is the passage the flow runs through, plus
the air around the part. The passage is the solid that opens on every named
opening plane and on no other side of the box: the air around the part also
touches the box's other sides. A study whose openings name a side the part has
no opening on is a study error that names the sides it does have.

External flow (around a body): a box reaching 2 L upstream, 5 L downstream and
2 L to the sides (L the part's largest extent), minus the part. Each box side
the free stream enters through is an inlet, each it leaves through an outlet,
each it runs along a slip wall.

Each face of the fluid is classified: a wall (an image of one of the part's
faces through the boolean's history, so it carries the part's ``#o1.fN`` ref
and the wall pressure lands on the face the viewer shows), an inlet, an outlet
or a slip side. The mesh is netgen's, through the same BREP hand-off and the
same fingerprint matcher as the part's own mesh (:mod:`.mesh`, read only), and
its local sizes are the fluid size field: fine at walls and openings, coarser
in the free stream when the ladder's ``fluid_coarsen`` asks for it.

Numeric imports live inside the functions.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cadgen._internal.fea.mesh import VolumeMesh

__all__ = ["OPENINGS", "FluidDomain", "FluidFace", "build_domain", "fine_faces", "mesh_fluid", "opening_words"]

#: The sides of the part's bounding box an opening can name, in axis order.
OPENINGS = ("x_min", "x_max", "y_min", "y_max", "z_min", "z_max")
#: External flow: the box reaches this many part lengths upstream, downstream and to the sides.
UPSTREAM, DOWNSTREAM, SIDES = 2.0, 5.0, 2.0
#: Internal flow: the box's sides that are not openings stand off the part by this share of its diagonal.
STANDOFF = 0.1
_CUT_FAILED = "the boolean that makes the fluid region from the part failed"


def opening_words(opening: str) -> str:
    """"x_min" -> "the low X side"."""
    axis, side = opening.split("_")
    return f"the {'low' if side == 'min' else 'high'} {axis.upper()} side"


def _axis_side(opening: str) -> tuple[int, int]:
    axis, side = opening.split("_")
    return "xyz".index(axis), 0 if side == "min" else 1


@dataclass
class FluidFace:
    """One face of the fluid region and what the flow does there."""

    #: 1-based position in the fluid shape's face map (netgen's face index + 1 once matched).
    index: int
    #: "wall" (the part's surface, no slip), "inlet", "outlet" or "side" (an external box side, slip).
    kind: str
    area: float
    perimeter: float
    centre: tuple[float, float, float]
    #: A wall's part face: its cadgen ordinal and ref (0 and None when it is not one of the part's faces).
    ordinal: int = 0
    ref: str | None = None
    #: The box side an opening or a slip side lies on ("x_min").
    opening: str | None = None


@dataclass
class FluidDomain:
    """The fluid region of a flow study: the solid(s) netgen meshes and what each face is."""

    shape: Any
    kind: str                                  # "internal" or "external"
    faces: list[FluidFace]
    volume_mm3: float
    #: The part's bounding box (low, high), mm.
    part_box: tuple[tuple[float, float, float], tuple[float, float, float]]
    #: The fluid box (low, high), mm.
    box: tuple[tuple[float, float, float], tuple[float, float, float]]
    #: The part's largest extent, mm: an external flow's length scale.
    length_mm: float
    seconds: float = 0.0
    #: Part ordinals whose faces the fluid wets.
    wetted: set[int] = field(default_factory=set)

    @property
    def wall_area_mm2(self) -> float:
        return sum(face.area for face in self.faces if face.kind == "wall")


# -- geometry helpers (shared by the cavity and the acoustic air) --------------------------------------


def bounds(shape) -> tuple[list[float], list[float]]:
    """The exact bounding box (no tolerance gap), mm."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    return [xmin, ymin, zmin], [xmax, ymax, zmax]


def shapes_of(shape, kind) -> list:
    """The sub-shapes of an OCP ``TopAbs`` kind, in the shape's own map order."""
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape

    found = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, kind, found)
    return [found.FindKey(k) for k in range(1, found.Extent() + 1)]


def faces_of(shape) -> list:
    from OCP.TopAbs import TopAbs_FACE

    return shapes_of(shape, TopAbs_FACE)


def solids_of(shape) -> list:
    from OCP.TopAbs import TopAbs_SOLID

    return shapes_of(shape, TopAbs_SOLID)


def volume_of(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return float(props.Mass())


def _area_centre_perimeter(face) -> tuple[float, tuple[float, float, float], float]:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    from cadgen._internal.fea.mesh import area_center

    area, centre = area_center(face)
    line = GProp_GProps()
    BRepGProp.LinearProperties_s(face, line)
    return area, centre, float(line.Mass())


def plane_of(face, box_low, box_high, tol: float) -> str | None:
    """The box side a planar face lies flat on ("x_min"), or None."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.TopoDS import TopoDS

    if BRepAdaptor_Surface(TopoDS.Face_s(face)).GetType() != GeomAbs_Plane:
        return None
    low, high = bounds(face)
    for axis in range(3):
        if high[axis] - low[axis] > tol:
            continue
        middle = 0.5 * (low[axis] + high[axis])
        if abs(middle - box_low[axis]) <= tol:
            return OPENINGS[2 * axis]
        if abs(middle - box_high[axis]) <= tol:
            return OPENINGS[2 * axis + 1]
    return None


def sides_of(solid, box_low, box_high, tol: float) -> set[str]:
    return {side for face in faces_of(solid) if (side := plane_of(face, box_low, box_high, tol)) is not None}


def opening_sides(part, low, high, tol: float) -> set[str]:
    """The box sides the part opens on: those where a face of the part lies flat with a hole in it."""
    from OCP.TopAbs import TopAbs_WIRE

    sides = set()
    for face in faces_of(part):
        side = plane_of(face, low, high, tol)
        if side is not None and side not in sides and len(shapes_of(face, TopAbs_WIRE)) > 1:
            sides.add(side)
    return sides


def standoff_box(low, high, exact: set[str], standoff: float) -> tuple[list[float], list[float]]:
    """The bounding box, exact on the ``exact`` sides and ``standoff`` past the part on the others."""
    box_low, box_high = list(low), list(high)
    for axis in range(3):
        if OPENINGS[2 * axis] not in exact:
            box_low[axis] -= standoff
        if OPENINGS[2 * axis + 1] not in exact:
            box_high[axis] += standoff
    return box_low, box_high


def box_cut(box_low, box_high, part, failed: str):
    """The OCP boolean box minus part; RuntimeError ``failed`` when it does not complete."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    cut = BRepAlgoAPI_Cut(BRepPrimAPI_MakeBox(gp_Pnt(*box_low), gp_Pnt(*box_high)).Shape(), part)
    if not cut.IsDone():
        raise RuntimeError(failed)
    return cut


def compound(solids):
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    if len(solids) == 1:
        return solids[0]
    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    for solid in solids:
        builder.Add(compound, solid)
    return compound


def classify_faces(shape, cut, part, refs: dict[int, str], role_of) -> list[FluidFace]:
    """Each face of ``shape``: a wall where it is an image of a part face through ``cut``'s history (its
    ordinal and ref), else ``role_of(face)`` -> (kind, box side) or None for a wall no part face traces to."""
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape

    from cadgen._internal.entity_ordinals import entity_map

    face_map = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_FACE, face_map)
    part_faces = entity_map(part, "face")
    wall_of: dict[int, int] = {}
    for ordinal in range(1, part_faces.Extent() + 1):
        face = part_faces.FindKey(ordinal)
        for image in list(cut.Modified(face)) or ([] if cut.IsDeleted(face) else [face]):
            index = face_map.FindIndex(image)
            if index > 0:
                wall_of[index] = ordinal
    faces: list[FluidFace] = []
    for index in range(1, face_map.Extent() + 1):
        face = face_map.FindKey(index)
        area, centre, perimeter = _area_centre_perimeter(face)
        if index in wall_of:
            ordinal = wall_of[index]
            faces.append(FluidFace(index, "wall", area, perimeter, centre, ordinal, refs.get(ordinal)))
        elif (role := role_of(face)) is not None:
            faces.append(FluidFace(index, role[0], area, perimeter, centre, opening=role[1]))
        else:
            faces.append(FluidFace(index, "wall", area, perimeter, centre))
    return faces


# -- the fluid region ---------------------------------------------------------------------------------


def build_domain(part, kind: str, *, openings: dict[str, str] | None = None,
                 velocity: tuple[float, float, float] | None = None, refs: dict[int, str] | None = None) -> FluidDomain:
    """The fluid region around ``part`` (an OCP shape, placed) and what each of its faces is.

    Internal: ``openings`` maps each named side ("x_min") to "inlet" or "outlet". External:
    ``velocity`` is the free stream's direction (any length). ``refs`` names the part's faces by
    cadgen ordinal (``{3: "#o1.f3"}``). ValueError for an opening the part does not have there.
    """
    started = time.perf_counter()
    low, high = bounds(part)
    extent = [h - l for l, h in zip(low, high)]
    diagonal = sum(e * e for e in extent) ** 0.5
    if not diagonal > 0:
        raise RuntimeError("the part has no volume to put in a flow")
    length = max(extent)
    tol = 1e-6 * max(diagonal, 1.0)
    if kind == "internal":
        solids, box_low, box_high, cut = _internal(part, openings or {}, low, high, diagonal, tol)
        roles = dict(openings or {})

        def role_of(face):
            side = plane_of(face, box_low, box_high, tol)
            return (roles[side], side) if side in roles else None
    else:
        solids, box_low, box_high, cut = _external(part, velocity, low, high, length, tol)
        norm = sum(c * c for c in velocity) ** 0.5
        direction = [c / norm for c in velocity]

        def role_of(face):
            side = plane_of(face, box_low, box_high, tol)
            if side is None:
                return None
            axis, high_side = _axis_side(side)
            along = direction[axis] * (1.0 if high_side else -1.0)
            return ("outlet" if along > 1e-9 else "inlet" if along < -1e-9 else "side"), side
    shape = compound(solids)
    faces = classify_faces(shape, cut, part, refs or {}, role_of)
    return FluidDomain(
        shape=shape, kind=kind, faces=faces, volume_mm3=sum(volume_of(solid) for solid in solids),
        part_box=(tuple(low), tuple(high)), box=(tuple(box_low), tuple(box_high)), length_mm=length,
        seconds=time.perf_counter() - started, wetted={face.ordinal for face in faces if face.ordinal},
    )


def _internal(part, openings: dict[str, str], low, high, diagonal: float, tol: float):
    """The passage(s) through the part that open on exactly the named sides.

    The box is exact on every side the part opens on (a face there with a hole) and on every named
    side, and stands off the part elsewhere, so the air around the part touches a side no passage
    does. A passage is a solid of the cut that touches only exact sides.
    """
    named = set(openings)
    available = opening_sides(part, low, high, tol)
    order = OPENINGS.index
    if missing := sorted(named - available, key=order):
        where = (f"its openings are on {', '.join(sorted(available, key=order))}" if available
                 else "it has no face on its bounding box with a hole in it")
        raise ValueError(
            f"flow: the part has no opening on {', '.join(f'{opening_words(m)} ({m})' for m in missing)}; {where}; "
            "name those sides as the inlets and outlets (or use kind \"external\" for flow around it)")
    exact = named | available
    box_low, box_high = standoff_box(low, high, exact, STANDOFF * diagonal)
    cut = box_cut(box_low, box_high, part, _CUT_FAILED)
    passages = []
    for solid in solids_of(cut.Shape()):
        sides = sides_of(solid, box_low, box_high, tol)
        if sides and sides <= exact:
            passages.append((solid, sides))
    keep = [solid for solid, sides in passages if sides == named]
    if keep:
        return keep, box_low, box_high, cut
    words = " and ".join(sorted(named, key=order))
    wider = [sides - named for _, sides in passages if named <= sides]
    if wider:
        extra = min(wider, key=len)
        raise ValueError(f"flow: the passage from {words} also opens on {', '.join(sorted(extra, key=order))}; "
                         "name every opening of the passage as an inlet or an outlet")
    found = "; ".join(", ".join(sorted(sides, key=order)) for _, sides in passages)
    raise ValueError(f"flow: no single passage through the part joins {words}"
                     + (f"; its passages open on {found}" if found else ""))


def _external(part, velocity, low, high, length: float, tol: float):
    """The box around the part, 2 L upstream, 5 L downstream and 2 L to the sides, minus the part."""
    dominant = max(range(3), key=lambda axis: abs(velocity[axis]))
    box_low, box_high = [], []
    for axis in range(3):
        before, after = SIDES * length, SIDES * length
        if axis == dominant:
            before, after = (UPSTREAM * length, DOWNSTREAM * length) if velocity[axis] > 0 else (DOWNSTREAM * length, UPSTREAM * length)
        box_low.append(low[axis] - before)
        box_high.append(high[axis] + after)
    cut = box_cut(box_low, box_high, part, _CUT_FAILED)
    # A closed cavity inside the part is not in the flow: keep what touches the box.
    solids = [solid for solid in solids_of(cut.Shape()) if sides_of(solid, box_low, box_high, tol)]
    if not solids:
        raise RuntimeError("the boolean that makes the fluid region around the part left no fluid")
    return solids, box_low, box_high, cut


# -- the mesh -----------------------------------------------------------------------------------------


def fine_faces(domain: FluidDomain) -> list[FluidFace]:
    """The faces kept at the wall size: the part's walls, and an internal flow's openings (an external
    box's sides are far field)."""
    kinds = ("wall",) if domain.kind == "external" else ("wall", "inlet", "outlet")
    return [face for face in domain.faces if face.kind in kinds]


def mesh_fluid(domain: FluidDomain, wall_mm: float, far_mm: float | None = None, *, order: int = 2) -> "VolumeMesh":
    """netgen's tetrahedra of the fluid: ``wall_mm`` at walls and openings, up to ``far_mm`` away from them.

    The mesh's ``boundary_ordinal`` is each boundary triangle's fluid face index (``domain.faces``),
    matched by the part mesher's fingerprint matcher; ``faces`` holds a fingerprint per fluid face.
    """
    far = float(far_mm) if far_mm and far_mm > wall_mm else float(wall_mm)
    size_field = None
    if far > wall_mm:
        size_field = {"faces": {face.index: float(wall_mm) for face in fine_faces(domain)}}
    return mesh_domain(domain, "fluid", far, order, size_field, "the mesher produced no fluid elements")


def mesh_domain(domain: FluidDomain, label: str, size: float, order: int, size_field: dict | None, empty: str, *,
                check: bool = True) -> "VolumeMesh":
    """netgen's mesh of ``domain.shape`` at ``size`` (:func:`netgen_mesh`); ``boundary_ordinal`` is each boundary
    triangle's face index."""
    import numpy as np

    from cadgen._internal.fea.mesh import FaceFingerprint, VolumeMesh, require_fea_stack

    require_fea_stack()
    prints = [FaceFingerprint(face.ref or f"{label} face {face.index}", face.index, face.area, face.centre) for face in domain.faces]
    low, high = (np.array(corner) for corner in domain.box)
    diagonal = float(np.linalg.norm(high - low))
    started = time.perf_counter()
    coordinates, e3, e2, mapping = netgen_mesh(domain.shape, prints, diagonal, size, order, size_field, label, empty,
                                               check=check)
    width = 10 if order == 2 else 4
    index_of = np.zeros(int(e2["index"].max()) + 1, dtype=np.int64)
    for index, ordinal in mapping.items():
        index_of[index + 1] = ordinal
    return VolumeMesh(
        nodes=coordinates,
        tets=np.ascontiguousarray(e3["nodes"][:, :width].astype(np.int64) - 1),
        boundary=np.ascontiguousarray(e2["nodes"][:, :6 if order == 2 else 3].astype(np.int64) - 1),
        boundary_ordinal=index_of[e2["index"].astype(np.int64)],
        faces={fp.ordinal: fp for fp in prints},
        max_h=size,
        bbox_diagonal=diagonal,
        seconds=time.perf_counter() - started,
    )


def netgen_mesh(shape, prints: list, diagonal: float, size: float, order: int, size_field: dict | None, label: str,
                empty: str, *, check: bool = True):
    """netgen's mesh of an OCP shape through the part mesher's BREP hand-off, its faces matched to ``prints`` by
    the part mesher's fingerprint matcher: (node coordinates, 3D elements, 2D elements, netgen face index ->
    fingerprint ordinal). RuntimeError ``empty`` for no elements, and (``check``) for elements not of ``order``."""
    import tempfile
    from pathlib import Path

    import numpy as np

    from cadgen._internal.fea import mesh as part_mesh
    from cadgen._internal.step_scene_loader import kernel_messages_on_stderr

    import netgen.meshing as ngmesh
    import netgen.occ as ngocc

    with tempfile.TemporaryDirectory(prefix=f"cadgen-{label}-") as tmp:
        brep = Path(tmp) / f"{label}.brep"
        part_mesh.write_brep(shape, brep)
        with kernel_messages_on_stderr():
            ngmesh.SetMessageImportance(0)
            geometry = ngocc.OCCGeometry(str(brep))
            mapping = part_mesh.match_faces(prints, list(geometry.faces), diagonal)
            keys = {index: [ordinal] for index, ordinal in mapping.items()}
            mesh = part_mesh.generate_mesh(geometry, ngocc, size, 1.0, order, size_field, keys)
            coordinates = np.array(mesh.Coordinates(), dtype=float, copy=True)
            e3 = mesh.Elements3D().NumPy().copy()
            e2 = mesh.Elements2D().NumPy().copy()
            del mesh, geometry
    if len(e3) == 0:
        raise RuntimeError(empty)
    if check:
        part_mesh.require_elements(e3, order)
    return coordinates, e3, e2, mapping
