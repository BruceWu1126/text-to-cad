"""The stock: the solid the machine starts from, in the part's frame.

:func:`make_stock` turns the job's :class:`~cadgen._internal.cam.job.Stock` and the part into a
:class:`StockSolid`:

- ``box``: the part's bounding box grown by ``margin_mm`` ``[x, y, z_top, z_bottom]``: x and y on each side, z_top
  above the part and z_bottom below it.
- ``cylinder``: a bar along ``axis`` (X, Y or Z of the part's frame), centred on the part's bounding box across that
  axis. Its diameter is the one stated, or the smallest bar about that centre line that holds the part plus the radial
  margin on each side; its length is the part's along the axis plus the axial margin at each end.
- ``sheet``: the part's outline in X and Y grown by ``margin_mm`` ``[x, y]`` on each side, from the part's bottom up
  by ``thickness_mm`` (the part's height when not stated).
- ``from``: the blank read from a STEP, as it sits in the part's frame. It holds the part when its bounding box
  does and the part less the blank (a boolean cut) leaves no volume: a blank with a pocket where the part stands
  out is refused, though its box holds the part's.

A stock that does not hold the part (a stated bar too thin, a blank beside the part) is a ValueError: a plan cut
from it would be plausible and wrong (README law 10).

Numerics and the kernel are imported inside the functions; this module is stdlib-only at import.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cadgen._internal.cam.values import an

__all__ = ["Bounds", "StockSolid", "bounds_of", "make_stock", "occ_shape"]

#: ``((x_min, y_min, z_min), (x_max, y_max, z_max))``, mm.
Bounds = tuple[tuple[float, float, float], tuple[float, float, float]]

#: Digits the bounds are rounded to: 6 in mm is a nanometre (as in the toolpath), so kernel noise never reaches a plan.
_DIGITS = 6
#: How far a part may stand outside its stock, mm, before the stock is refused (the kernel's own slop).
_CONTAIN_MM = 1e-4
#: The chord deflection the part is meshed at to find its radius about a bar's centre line, mm. A face not turned
#: about that line can stand out of the mesh by up to this, so it is the containment slop: a bar the check passes is
#: never thinner than the part by more than the check allows anywhere else.
_RADIUS_DEFLECTION_MM = _CONTAIN_MM
#: The part's volume a ``from`` blank may leave outside it, as a fraction of the part's volume (boolean noise).
_OUTSIDE_FRACTION = 1e-6


@dataclass(frozen=True)
class StockSolid:
    """The stock in the part's frame."""

    type: str                                   # box | cylinder | sheet | from
    #: The solid, an OCP ``TopoDS_Shape`` in the part's frame.
    shape: Any
    bounds: Bounds
    #: A cylinder's axis in the part's frame (``"X"``, ``"Y"``, ``"Z"``), its diameter and length, mm.
    axis: str | None = None
    diameter_mm: float | None = None
    length_mm: float | None = None

    @property
    def size(self) -> tuple[float, float, float]:
        (x0, y0, z0), (x1, y1, z1) = self.bounds
        return (x1 - x0, y1 - y0, z1 - z0)


def occ_shape(shape: Any) -> Any:
    """The OCP ``TopoDS_Shape`` of a build123d shape, or the shape itself when it is one already."""
    return getattr(shape, "wrapped", shape)


def _clean(value: float) -> float:
    value = round(float(value), _DIGITS)
    return 0.0 if value == 0 else value  # no -0.0


def bounds_of(shape: Any) -> Bounds:
    """The tight axis-aligned bounds of a shape, mm, rounded to a nanometre."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(occ_shape(shape), box, False, False)
    if box.IsVoid():
        raise ValueError("the part has no geometry to make stock around")
    x0, y0, z0, x1, y1, z1 = (_clean(value) for value in box.Get())
    return (x0, y0, z0), (x1, y1, z1)


def _box_solid(low, high) -> Any:
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    return BRepPrimAPI_MakeBox(gp_Pnt(*low), gp_Pnt(*high)).Shape()


_AXIS_INDEX = {"X": 0, "Y": 1, "Z": 2}


def _cylinder_solid(axis: str, center: tuple[float, float, float], radius: float, length: float) -> Any:
    """A cylinder along ``axis`` whose base centre is ``center``."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    direction = [0.0, 0.0, 0.0]
    direction[_AXIS_INDEX[axis]] = 1.0
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(*center), gp_Dir(*direction)), radius, length).Shape()


def _radius_about(shape: Any, axis: str, center: tuple[float, float]) -> float:
    """The part's largest distance from the centre line along ``axis`` through ``center`` (the two other coordinates).

    Read from the part's vertices and the nodes of a fine mesh: the nodes lie on the surfaces, so a face turned about
    that line gives its radius exactly, and any other face is within the mesh's chord deflection of its own.
    """
    import numpy as np
    from OCP.BRep import BRep_Tool
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopAbs import TopAbs_FACE, TopAbs_VERTEX
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    copy = BRepBuilderAPI_Copy(occ_shape(shape)).Shape()  # meshing writes into the shape; never into the caller's
    BRepMesh_IncrementalMesh(copy, _RADIUS_DEFLECTION_MM, False, 0.1, False)
    points: list[tuple[float, float, float]] = []
    explorer = TopExp_Explorer(copy, TopAbs_VERTEX)
    while explorer.More():
        point = BRep_Tool.Pnt_s(TopoDS.Vertex_s(explorer.Current()))
        points.append((point.X(), point.Y(), point.Z()))
        explorer.Next()
    explorer = TopExp_Explorer(copy, TopAbs_FACE)
    while explorer.More():
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(TopoDS.Face_s(explorer.Current()), location)
        if triangulation is not None:
            trsf = location.Transformation()
            for index in range(1, triangulation.NbNodes() + 1):
                point = triangulation.Node(index).Transformed(trsf)
                points.append((point.X(), point.Y(), point.Z()))
        explorer.Next()
    if not points:
        raise ValueError("the part has no geometry to make stock around")
    across = [index for index in range(3) if index != _AXIS_INDEX[axis]]
    array = np.asarray(points, dtype=float)[:, across] - np.asarray(center, dtype=float)
    return float(np.sqrt((array * array).sum(axis=1)).max())


def _volume(shape: Any) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return abs(props.Mass())


def _volume_outside(part: Any, blank: Any) -> float | None:
    """The part's volume outside the blank, mm3, when it is more than boolean noise; else ``None``."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut

    part = occ_shape(part)
    total = _volume(part)
    cut = BRepAlgoAPI_Cut(part, occ_shape(blank))
    if not cut.IsDone():
        raise ValueError("stock.from: the kernel could not compare the blank with the part")
    outside = _volume(cut.Shape())
    return outside if outside > _OUTSIDE_FRACTION * max(total, 1.0) else None


def _contains(outer: Bounds, inner: Bounds) -> bool:
    return all(o <= i + _CONTAIN_MM for o, i in zip(outer[0], inner[0])) and all(
        o >= i - _CONTAIN_MM for o, i in zip(outer[1], inner[1]))


def _fmt(bounds: Bounds) -> str:
    (x0, y0, z0), (x1, y1, z1) = bounds
    return f"x {x0:g}..{x1:g}, y {y0:g}..{y1:g}, z {z0:g}..{z1:g} mm"


def make_stock(stock, part: Any) -> StockSolid:
    """The stock the job states, made around ``part`` (a build123d or OCP shape in the part's frame)."""
    part_bounds = bounds_of(part)
    (px0, py0, pz0), (px1, py1, pz1) = part_bounds
    if stock.type == "box":
        mx, my, top, bottom = stock.margin_mm or (0.0, 0.0, 0.0, 0.0)
        low = (_clean(px0 - mx), _clean(py0 - my), _clean(pz0 - bottom))
        high = (_clean(px1 + mx), _clean(py1 + my), _clean(pz1 + top))
        return StockSolid("box", _box_solid(low, high), (low, high))
    if stock.type == "sheet":
        mx, my = stock.margin_mm or (0.0, 0.0)
        thickness = stock.thickness_mm if stock.thickness_mm is not None else pz1 - pz0
        if thickness < pz1 - pz0 - _CONTAIN_MM:
            raise ValueError(f"stock.thickness_mm: {an(format(thickness, 'g'))} mm sheet is thinner than the part's "
                             f"{pz1 - pz0:g} mm")
        low = (_clean(px0 - mx), _clean(py0 - my), _clean(pz0))
        high = (_clean(px1 + mx), _clean(py1 + my), _clean(pz0 + thickness))
        return StockSolid("sheet", _box_solid(low, high), (low, high))
    if stock.type == "cylinder":
        return _cylinder_stock(stock, part, part_bounds)
    if stock.type == "from":
        from cadgen.step_scene import read_step

        shape = occ_shape(read_step(stock.path))
        found = bounds_of(shape)
        if not _contains(found, part_bounds):
            raise ValueError(f"stock.from: the blank in {stock.path.name} ({_fmt(found)}) does not hold the part "
                             f"({_fmt(part_bounds)}); model the blank where it sits around the part")
        outside = _volume_outside(part, shape)
        if outside is not None:
            raise ValueError(f"stock.from: the blank in {stock.path.name} does not hold the part: {outside:,.4g} mm3 "
                             "of the part stands outside it, though its bounding box holds the part's; model the "
                             "blank where it sits around the part")
        return StockSolid("from", shape, found)
    raise ValueError(f"stock.type: {stock.type!r} is not a stock cadgen makes")


def _cylinder_stock(stock, part: Any, part_bounds: Bounds) -> StockSolid:
    axis = stock.axis
    along = _AXIS_INDEX[axis]
    across = [index for index in range(3) if index != along]
    low, high = part_bounds
    center_2d = tuple((low[index] + high[index]) / 2 for index in across)
    radial, axial = stock.margin_mm or (0.0, 0.0)
    need = _radius_about(part, axis, center_2d)
    if stock.diameter_mm is not None:
        radius = stock.diameter_mm / 2
        if radius < need - _CONTAIN_MM:
            raise ValueError(f"stock.diameter_mm: {an(format(stock.diameter_mm, 'g'))} mm bar does not hold the part, which needs "
                             f"{2 * need:.3f} mm about its centre line along {axis}")
    else:
        radius = need + radial
    radius = _clean(radius)
    start = low[along] - axial
    length = _clean(high[along] + axial - start)
    base = [0.0, 0.0, 0.0]
    base[along] = _clean(start)
    for index, value in zip(across, center_2d):
        base[index] = _clean(value)
    lo = [0.0, 0.0, 0.0]
    hi = [0.0, 0.0, 0.0]
    lo[along], hi[along] = base[along], _clean(start + length)
    for index in across:
        lo[index], hi[index] = _clean(base[index] - radius), _clean(base[index] + radius)
    shape = _cylinder_solid(axis, tuple(base), radius, length)
    return StockSolid("cylinder", shape, (tuple(lo), tuple(hi)), axis=axis, diameter_mm=_clean(2 * radius),
                      length_mm=length)
