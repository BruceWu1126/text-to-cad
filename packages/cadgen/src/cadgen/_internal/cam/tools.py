"""Tool shapes: what a cutter looks like, for the checks and for the result GLB.

Every rotating tool is a solid of revolution: its tip at the origin, its axis +Z, mm. :func:`profile` turns a
job's :class:`~cadgen._internal.cam.job.Tool` into a :class:`ToolProfile`, whose ``radius_at(h)`` is the radius
of the spinning tool at height ``h`` above its tip (cutter, then shank, then holder), and whose ``mesh()`` is a
closed, deterministic triangle mesh of the same outline.

The outline is built from straight sections and quarter arcs (a ball's end, a bull nose's corner), so
``radius_at`` is exact and the mesh is the only thing sampled. Where the outline steps (a 6 mm cutter under a
32 mm holder), ``radius_at`` gives the larger radius: a check against it is never optimistic.

What a job leaves out is filled in as follows, and only here:

- ``flute_length_mm``: 3 x diameter (a standard-length cutter). For a boring bar or back-bore it is the reach:
  how deep the narrow neck goes before the shank.
- ``shank_diameter_mm``: the diameter; 3 mm for a flat, ball, bull or thread mill under 3 mm (small cutters sit
  on a bigger shank, and a check that assumed a 1 mm shank would pass tools that cannot fit).
- ``stickout_mm``: the flute length plus one diameter.
- ``tip_angle_deg``: 118 for a drill, 90 for a spot drill (a chamfer mill and a V-bit must state theirs).
- A boring bar's or back-bore's head is a quarter of its diameter tall, on a neck 0.6 x its diameter.
- A counterbore's pilot (``pilot_diameter_mm``) is as long as it is wide.
- A holder is drawn and checked only when the job states one.

A thread mill is modelled as a multi-form cutter (full diameter for its flute length), the larger envelope of
the two kinds. A tap is a cylinder of its major diameter; its lead chamfer is not drawn.

Stdlib at import; :meth:`ToolProfile.mesh` imports numpy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cadgen._internal.cam.job import Tool

__all__ = ["MESH_SEGMENTS", "PROFILE_TYPES", "Envelope", "Section", "ToolProfile", "profile"]

#: The tool types this module draws: the rotating cutters and hole makers phase 1 runs.
PROFILE_TYPES = ("flat_endmill", "face_mill", "ball_endmill", "bull_endmill", "chamfer_mill", "v_bit", "thread_mill",
                 "drill", "spot_drill", "tap", "reamer", "boring_bar", "back_bore", "counterbore")
#: Facets around the axis in :meth:`ToolProfile.mesh`, and per quarter arc of a ball or corner radius.
MESH_SEGMENTS = 32
ARC_SEGMENTS = 8

_TIP_ANGLE = {"drill": 118.0, "spot_drill": 90.0}
_SMALL_SHANK_MM = 3.0
_SMALL_SHANK_TYPES = ("flat_endmill", "ball_endmill", "bull_endmill", "thread_mill")
_HEAD_HEIGHT = 0.25       # boring bar and back-bore head height, x diameter
_NECK = 0.6               # their neck diameter, x diameter


@dataclass(frozen=True)
class Section:
    """One piece of the outline between heights ``z0`` and ``z1`` (``z0 <= z1``).

    A line runs from radius ``r0`` at ``z0`` to ``r1`` at ``z1``. An arc (``arc`` set to ``(zc, rc, radius)``) is
    the convex quarter circle about ``(zc, rc)`` in the (z, r) half-plane: ``r = rc + sqrt(radius^2 - (z - zc)^2)``.
    """

    z0: float
    z1: float
    r0: float
    r1: float
    arc: tuple[float, float, float] | None = None

    def radius(self, z: float) -> float:
        if self.arc is not None:
            zc, rc, radius = self.arc
            return rc + math.sqrt(max(radius * radius - (z - zc) ** 2, 0.0))
        if self.z1 == self.z0:
            return max(self.r0, self.r1)
        return self.r0 + (self.r1 - self.r0) * (z - self.z0) / (self.z1 - self.z0)

    def points(self) -> list[tuple[float, float]]:
        """``(r, z)`` from ``z0`` to ``z1``; an arc sampled at :data:`ARC_SEGMENTS`."""
        if self.arc is None:
            return [(self.r0, self.z0), (self.r1, self.z1)]
        steps = ARC_SEGMENTS
        zc, rc, radius = self.arc
        # Sample by angle, not by height, so the points sit evenly along the curve.
        start = math.asin(max(-1.0, min(1.0, (self.z0 - zc) / radius)))
        end = math.asin(max(-1.0, min(1.0, (self.z1 - zc) / radius)))
        out = []
        for i in range(steps + 1):
            angle = start + (end - start) * i / steps
            out.append((rc + radius * math.cos(angle), zc + radius * math.sin(angle)))
        out[0], out[-1] = (self.r0, self.z0), (self.r1, self.z1)
        return out


@dataclass(frozen=True)
class Envelope:
    """A cylinder bounding one part of the tool, for the holder and shank collision checks."""

    part: str           # cutter | shank | holder
    z0: float
    z1: float
    radius: float


@dataclass(frozen=True)
class ToolProfile:
    """A tool's outline, tip at the origin, axis +Z, mm (see the module's docstring for the defaults)."""

    tool_id: str
    type: str
    diameter_mm: float
    #: How far up from the tip it cuts (the flutes; a boring bar's or back-bore's head).
    cutting_length_mm: float
    #: The height at which the tool first reaches its full cutting radius (a drill's point, a ball's radius).
    tip_length_mm: float
    shank_diameter_mm: float
    #: Tip to holder face.
    stickout_mm: float
    holder_diameter_mm: float | None
    holder_length_mm: float | None
    sections: tuple[Section, ...]

    @property
    def length_mm(self) -> float:
        """Tip to the top of the holder (or of the stickout when there is no holder)."""
        return self.sections[-1].z1

    @property
    def reach_mm(self) -> float:
        """How deep it goes into a hole of its own cutting diameter before anything wider follows it in."""
        limit = self.diameter_mm / 2 + 1e-9
        for section in self.sections:
            if section.r0 > limit:
                return section.z0
            if section.r1 > limit:
                if section.arc is not None or section.z1 == section.z0:
                    return section.z0
                return section.z0 + (limit - section.r0) / (section.r1 - section.r0) * (section.z1 - section.z0)
        return self.length_mm

    def radius_at(self, height: float) -> float:
        """The spinning tool's radius ``height`` mm above its tip; 0 below the tip and above the holder. At a step
        in the outline, the larger radius."""
        found = [section.radius(height) for section in self.sections if section.z0 <= height <= section.z1]
        return max(found, default=0.0)

    def cutting_radius_at(self, height: float) -> float:
        """The radius that cuts at ``height``: :meth:`radius_at` within the cutting length, else 0."""
        if height < 0 or height > self.cutting_length_mm:
            return 0.0
        return self.radius_at(height)

    def envelope(self) -> tuple[Envelope, ...]:
        """Bounding cylinders: the cutter up to its cutting length, the shank up to the stickout, the holder."""
        top = self.stickout_mm
        parts = [Envelope("cutter", 0.0, self.cutting_length_mm, self._widest(0.0, self.cutting_length_mm))]
        if top > self.cutting_length_mm:
            parts.append(Envelope("shank", self.cutting_length_mm, top, self._widest(self.cutting_length_mm, top)))
        if self.holder_diameter_mm is not None and self.holder_length_mm is not None:
            parts.append(Envelope("holder", top, top + self.holder_length_mm, self.holder_diameter_mm / 2))
        return tuple(parts)

    def _widest(self, z0: float, z1: float) -> float:
        """The largest radius strictly between ``z0`` and ``z1``: a step at either end belongs to the neighbour."""
        widest = 0.0
        for section in self.sections:
            if section.z0 == section.z1:
                if z0 < section.z0 < z1:
                    widest = max(widest, section.r0, section.r1)
            elif section.z0 < z1 and section.z1 > z0:
                # Lines and the quarter arcs are monotonic, so the widest point is at an end of the overlap.
                widest = max(widest, section.radius(max(section.z0, z0)), section.radius(min(section.z1, z1)))
        return widest

    def outline(self) -> list[tuple[float, float]]:
        """The outline as ``(r, z)`` points from the axis at the tip to the axis at the top, duplicates removed."""
        points: list[tuple[float, float]] = [(0.0, 0.0)]
        for section in self.sections:
            for point in section.points():
                if point != points[-1]:
                    points.append(point)
        top = (0.0, self.length_mm)
        if points[-1] != top:
            points.append(top)
        return points

    def mesh(self, segments: int = MESH_SEGMENTS):
        """``(positions, triangles)``: float32 ``(n, 3)`` mm and uint32 ``(m, 3)``, the outline revolved about +Z.

        Closed (every edge shared by two triangles), wound outward, and the same bytes every call.
        """
        import numpy as np

        outline = self.outline()
        angles = [2 * math.pi * j / segments for j in range(segments)]
        cos = [math.cos(a) for a in angles]
        sin = [math.sin(a) for a in angles]
        positions: list[tuple[float, float, float]] = []
        rings: list[list[int]] = []          # per outline point, its vertex indices (one for a point on the axis)
        for r, z in outline:
            if r == 0.0:
                rings.append([len(positions)] * segments)
                positions.append((0.0, 0.0, z))
            else:
                start = len(positions)
                positions.extend((r * cos[j], r * sin[j], z) for j in range(segments))
                rings.append(list(range(start, start + segments)))
        triangles: list[tuple[int, int, int]] = []
        for k in range(len(outline) - 1):
            low, high = rings[k], rings[k + 1]
            low_axis, high_axis = outline[k][0] == 0.0, outline[k + 1][0] == 0.0
            for j in range(segments):
                a, b = low[j], low[(j + 1) % segments]
                c, d = high[(j + 1) % segments], high[j]
                if not low_axis:
                    triangles.append((a, b, c))
                if not high_axis:
                    triangles.append((a, c, d))
        return np.asarray(positions, dtype=np.float32), np.asarray(triangles, dtype=np.uint32).reshape(-1, 3)


def _line(z0: float, z1: float, r0: float, r1: float) -> Section:
    return Section(z0, z1, r0, r1)


def profile(tool: "Tool") -> ToolProfile:
    """The outline of a job's tool. A type this phase does not draw (lathe inserts, beams, a slitting saw) is a
    ValueError naming it."""
    if tool.type not in PROFILE_TYPES:
        raise ValueError(f"{tool.id}: a {tool.type} has no tool shape yet (built today: {', '.join(PROFILE_TYPES)})")
    diameter = float(tool.diameter_mm)
    radius = diameter / 2
    size = tool.size
    flute = float(tool.flute_length_mm) if tool.flute_length_mm is not None else 3 * diameter
    default_shank = diameter
    if tool.type in _SMALL_SHANK_TYPES and diameter < _SMALL_SHANK_MM:
        default_shank = _SMALL_SHANK_MM
    shank = float(tool.shank_diameter_mm) if tool.shank_diameter_mm is not None else default_shank
    stickout = float(tool.stickout_mm) if tool.stickout_mm is not None else flute + diameter

    sections: list[Section] = []
    cutting = flute
    tip = 0.0
    if tool.type in ("flat_endmill", "face_mill", "thread_mill", "tap", "reamer"):
        sections += [_line(0.0, 0.0, 0.0, radius), _line(0.0, flute, radius, radius)]
    elif tool.type == "ball_endmill":
        tip = radius
        cutting = max(flute, radius)
        sections += [Section(0.0, radius, 0.0, radius, arc=(radius, 0.0, radius)), _line(radius, cutting, radius, radius)]
    elif tool.type == "bull_endmill":
        corner = float(size["corner_radius_mm"])
        tip = corner
        cutting = max(flute, corner)
        sections += [_line(0.0, 0.0, 0.0, radius - corner),
                     Section(0.0, corner, radius - corner, radius, arc=(corner, radius - corner, corner)),
                     _line(corner, cutting, radius, radius)]
    elif tool.type in ("chamfer_mill", "v_bit", "drill", "spot_drill"):
        angle = float(size.get("tip_angle_deg", _TIP_ANGLE.get(tool.type, 0.0)))
        tip_radius = float(size.get("tip_diameter_mm", 0.0)) / 2
        tip = round((radius - tip_radius) / math.tan(math.radians(angle) / 2), 9)   # 3.0, not 3.0000000000000004
        # A chamfer mill or V-bit cuts with its cone unless the job states longer flutes; a drill along its flutes.
        cone_only = tool.type in ("chamfer_mill", "v_bit") and tool.flute_length_mm is None
        cutting = tip if cone_only else max(flute, tip)
        if tip_radius > 0:
            sections.append(_line(0.0, 0.0, 0.0, tip_radius))
        sections.append(_line(0.0, tip, tip_radius, radius))
        if cutting > tip:
            sections.append(_line(tip, cutting, radius, radius))
    elif tool.type in ("boring_bar", "back_bore"):
        cutting = _HEAD_HEIGHT * diameter
        neck = _NECK * radius
        reach = max(flute, cutting)
        sections += [_line(0.0, 0.0, 0.0, radius), _line(0.0, cutting, radius, radius),
                     _line(cutting, cutting, radius, neck), _line(cutting, reach, neck, neck)]
    else:  # counterbore
        pilot = float(size.get("pilot_diameter_mm", 0.0)) / 2
        if pilot > 0:
            tip = 2 * pilot
            sections += [_line(0.0, 0.0, 0.0, pilot), _line(0.0, tip, pilot, pilot), _line(tip, tip, pilot, radius)]
        else:
            sections.append(_line(0.0, 0.0, 0.0, radius))
        cutting = tip + flute
        sections.append(_line(tip, cutting, radius, radius))

    # The shank, from the top of what was drawn up to the holder face.
    body_top, body_r = sections[-1].z1, sections[-1].r1
    stickout = max(stickout, body_top)
    if stickout > body_top:
        if shank / 2 != body_r:
            sections.append(_line(body_top, body_top, body_r, shank / 2))
        sections.append(_line(body_top, stickout, shank / 2, shank / 2))
    holder_d = holder_l = None
    if tool.holder is not None:
        holder_d, holder_l = float(tool.holder.diameter_mm), float(tool.holder.length_mm)
        last = sections[-1].r1
        sections.append(_line(stickout, stickout, last, holder_d / 2))
        sections.append(_line(stickout, stickout + holder_l, holder_d / 2, holder_d / 2))
    return ToolProfile(tool.id, tool.type, diameter, cutting, tip, shank, stickout, holder_d, holder_l, tuple(sections))
