"""What the operation families share when they read their keys and start to plan.

Reading (``parse``, stdlib): a length in mm, a fraction of the tool's diameter, the ramp angle, the floor keys
(``depth_mm`` or ``z_mm``), the tool's diameter. Planning: a ``region`` as a polygon, the floor an operation cuts
to (refused when it goes under the part's floor it may not pass), and the feeds' findings every plan starts from.

Stdlib only at import, like every module under ``operations/``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cadgen._internal.cam.values import number

if TYPE_CHECKING:
    from cadgen._internal.cam.operations.base import ParseContext
    from cadgen._internal.cam.operations.context import PlanContext

__all__ = ["feed_findings", "floor_from", "floor_keys", "fraction", "mm", "ramp_angle", "region_polygon",
           "tool_diameter"]


def mm(raw: dict, key: str, where: str, default: float | None = None, *, zero: bool = False) -> float | None:
    """``raw[key]`` in mm, over 0 (at least 0 with ``zero``), else ``default``; ``where`` names the operation."""
    if key not in raw:
        return default
    if zero:
        return number(raw[key], where=f"{where}.{key}", minimum=0)
    return number(raw[key], where=f"{where}.{key}", positive=True)


def fraction(raw: dict, key: str, where: str, default: float) -> float:
    """A fraction of the tool's diameter, over 0 and up to 1."""
    if key not in raw:
        return default
    value = number(raw[key], where=f"{where}.{key}", positive=True)
    if value > 1:
        raise ValueError(f"{where}.{key}: a fraction of the tool's diameter, over 0 and up to 1, got {value:g}")
    return value


def floor_keys(raw: dict, where: str) -> tuple[float | None, float | None]:
    """``depth_mm`` (below the material top) or ``z_mm`` (setup frame), at most one."""
    if "depth_mm" in raw and "z_mm" in raw:
        raise ValueError(f"{where}: give depth_mm (below the top of the material) or z_mm (in the setup's frame), "
                         "not both")
    depth = mm(raw, "depth_mm", where)
    z = number(raw["z_mm"], where=f"{where}.z_mm") if "z_mm" in raw else None
    return depth, z


def ramp_angle(raw: dict, where: str) -> float:
    """``ramp_angle_deg``: the steepest the tool goes down into material, degrees over the horizontal."""
    from cadgen._internal.cam.operations.emit import DEFAULT_RAMP_DEG

    if "ramp_angle_deg" not in raw:
        return DEFAULT_RAMP_DEG
    return number(raw["ramp_angle_deg"], where=f"{where}.ramp_angle_deg", positive=True, below=90)


def tool_diameter(ctx: "ParseContext") -> float:
    return float(ctx.tool.diameter_mm)


def region_polygon(ctx: "PlanContext"):
    """The operation's ``region`` as a shapely Polygon; a ValueError when its outline crosses itself."""
    from shapely.geometry import Polygon

    poly = Polygon(ctx.operation.region)
    if not poly.is_valid:
        raise ValueError(f"{ctx.where}.region: the outline crosses itself")
    return poly


def floor_from(ctx: "PlanContext", depth: float | None, z: float | None, default: float | None, *,
               deepest: float | None, what: str) -> float:
    """The floor z: ``z_mm``, ``depth_mm`` below the material top, else ``default``; never under ``deepest`` (a
    ``PlanError``, ``too_deep``). No default and neither key (a region, which has no depth of its own) is a
    ValueError naming the keys."""
    if z is not None:
        floor = z
    elif depth is not None:
        floor = ctx.top_z - depth
    elif default is not None:
        floor = default
    else:
        raise ValueError(f"{ctx.where}: a region has no depth of its own; give depth_mm (below the top of the "
                         "material) or z_mm (in the setup's frame)")
    if deepest is not None and floor < deepest - 1e-6:
        from cadgen._internal.cam.operations.base import PlanError

        raise PlanError("too_deep", f"{ctx.where}: cutting to z {floor:g} goes {deepest - floor:g} mm under {what} at "
                        f"z {deepest:g}, into the part")
    return floor


def feed_findings(ctx: "PlanContext") -> tuple[dict, ...]:
    """The findings the feeds made (their clamps), which every plan's findings start with."""
    return tuple(ctx.feeds.findings)
