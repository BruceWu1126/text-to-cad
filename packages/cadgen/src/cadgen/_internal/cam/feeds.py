"""Feeds and speeds: how fast the spindle turns and the tool moves, from the material, the tool and the machine.

The arithmetic, for a milling cutter of diameter ``D`` mm with ``z`` flutes:

- ``rpm = 1000 x Vc / (pi x D)``, with ``Vc`` the material's surface speed in m/min (x 0.3 for an HSS tool:
  HSS runs at roughly a third of carbide's surface speed in the published tables).
- ``feed = rpm x z x fz``, with ``fz`` the chip load (mm per tooth) at the tool's diameter.
- Adaptive clearing at a radial engagement under half the diameter thins the chip, so the chip load is raised by
  ``1 / (2 sqrt(e (1 - e)))``, ``e`` the engagement as a fraction of ``D`` (the radial chip thinning factor); it is
  capped at 3, because below about 3 % engagement the edge's hone and runout decide the chip, not the geometry.
- Plunging (straight down along the axis) runs at 0.3 x the feed worked out from the plain chip load: chip thinning
  is a side cut's, and a plunge takes a full-width chip whatever the radial engagement.
- The spindle speed is a whole number of rpm (a controller's S word takes no fraction), rounded before anything is
  worked out from it, so the feed and the surface speed reported are the ones the machine runs.
- A cutter whose job leaves ``flutes`` out has 2, a face mill 4 (inserted face mills carry several; 4 is the
  smallest common one).

Hole makers move along their axis, so their feed is per revolution and their plunge is their feed:

- A drill, spot drill or counterbore runs at 0.7 x ``Vc``, feeding ``3 x fz`` mm per rev (two lips, each taking
  about 1.5 x an end mill's chip of the same diameter).
- A reamer runs at two thirds of the drill's speed and twice its feed per rev (the handbook's rule of thumb).
- A boring bar or back-bore is a single point at 0.7 x ``Vc``, ``1.5 x fz`` per rev.
- A tap runs at 0.08 x ``Vc`` and feeds exactly ``rpm x pitch``: anything else strips the thread or breaks the
  tap. When the machine's feed limit is under that, the spindle slows instead (to a whole rpm under the limit), so
  the two still agree. The 0.08 is already a tap's own speed: tapping charts (Machinery's Handbook) give HSS taps,
  the common kind, about 15 to 30 m/min in aluminium, which is 0.08 x 400. So a tap is never derated again for
  being HSS; a carbide tap runs at the same speed, which is safe, not optimised.
- A thread mill mills at half the chip load (its whole thread form is in the cut).

The chip load comes from the material's table of diameters, interpolated linearly between the two bands either
side of the tool (a 4.5 mm cutter gets a chip load between the 3 mm and 6 mm ones, not a jump at a band edge:
published chip loads grow roughly in proportion to diameter, so the line is closer than either band). Under the
smallest band it scales down in proportion to diameter; over the largest it holds the largest band's value
rather than extrapolating.

An operation's ``rpm``, ``feed_mm_min`` and ``plunge_mm_min`` win over the computed ones, and are still held to
the machine's ``max_rpm`` and ``max_feed_mm_min``. Every clamp is a finding (``rpm_clamped``, ``feed_clamped``),
never silent.

The material table is carbide surface speed (m/min) and chip load (mm per tooth) by tool diameter (mm). The
numbers sit inside the published ranges (tool makers' charts such as Harvey Tool's and Kennametal's, and
Machinery's Handbook): 6061 at 400 m/min and 0.04 mm per tooth at 6 mm, against the 300 to 600 m/min and 0.03 to
0.06 mm the charts give for a coated carbide end mill. They are starting points for a prove-out, not optimised.
Wrought aluminium alloys share a row (the charts group 6061 and 7075). A custom material states its own surface
speed and chip load; a table material may override either.

Stdlib only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cadgen._internal.cam.operations.base import finding

if TYPE_CHECKING:
    from cadgen._internal.cam.job import Machine, Material, Operation, Tool

__all__ = ["CHIP_THINNING_CAP", "HSS_FACTOR", "MATERIAL_TABLE", "PLUNGE_FRACTION", "Feeds", "chip_thinning",
           "chipload_at", "feeds_for", "feeds_for_operation", "rpm_for"]

#: Carbide surface speed (m/min) and chip load (mm per tooth) at tool diameters (mm), smallest first.
MATERIAL_TABLE: dict[str, tuple[float, tuple[tuple[float, float], ...]]] = {
    "aluminum-6061": (400.0, ((1.0, 0.008), (3.0, 0.025), (6.0, 0.040), (10.0, 0.060), (12.0, 0.070), (20.0, 0.100))),
    "aluminum-7075": (400.0, ((1.0, 0.008), (3.0, 0.025), (6.0, 0.040), (10.0, 0.060), (12.0, 0.070), (20.0, 0.100))),
    "mild-steel": (120.0, ((1.0, 0.005), (3.0, 0.015), (6.0, 0.030), (10.0, 0.045), (12.0, 0.050), (20.0, 0.080))),
    "stainless-304": (75.0, ((1.0, 0.004), (3.0, 0.012), (6.0, 0.025), (10.0, 0.040), (12.0, 0.045), (20.0, 0.065))),
    "brass": (250.0, ((1.0, 0.008), (3.0, 0.020), (6.0, 0.035), (10.0, 0.050), (12.0, 0.060), (20.0, 0.090))),
    "acetal": (250.0, ((1.0, 0.015), (3.0, 0.035), (6.0, 0.060), (10.0, 0.090), (12.0, 0.100), (20.0, 0.150))),
    "hdpe": (250.0, ((1.0, 0.015), (3.0, 0.040), (6.0, 0.070), (10.0, 0.100), (12.0, 0.120), (20.0, 0.170))),
    "acrylic": (200.0, ((1.0, 0.012), (3.0, 0.030), (6.0, 0.050), (10.0, 0.080), (12.0, 0.090), (20.0, 0.130))),
    "mdf": (400.0, ((1.0, 0.020), (3.0, 0.050), (6.0, 0.120), (10.0, 0.200), (12.0, 0.250), (20.0, 0.350))),
    "plywood": (400.0, ((1.0, 0.020), (3.0, 0.045), (6.0, 0.100), (10.0, 0.170), (12.0, 0.200), (20.0, 0.300))),
    "hardwood": (400.0, ((1.0, 0.015), (3.0, 0.040), (6.0, 0.090), (10.0, 0.150), (12.0, 0.180), (20.0, 0.260))),
}
HSS_FACTOR = 0.3
PLUNGE_FRACTION = 0.3
#: The radial chip thinning factor's cap (see the module's docstring).
CHIP_THINNING_CAP = 3.0

_DRILLS = ("drill", "spot_drill", "counterbore")
_BORING = ("boring_bar", "back_bore")
_SPEED_FACTOR = {"drill": 0.7, "spot_drill": 0.7, "counterbore": 0.7, "reamer": 0.7 * 2 / 3, "boring_bar": 0.7,
                 "back_bore": 0.7, "tap": 0.08}
_TAP_AGREE = 1e-3


@dataclass(frozen=True)
class Feeds:
    """What one operation runs at, after overrides and clamps."""

    rpm: float
    feed_mm_min: float
    plunge_mm_min: float
    #: The chip load the feed was worked out from (mm per tooth, or per rev for a hole maker); ``None`` when the
    #: operation stated its feed.
    chipload_mm: float | None
    #: The surface speed the tool actually runs at, m/min (after any clamp).
    surface_m_min: float
    #: ``rpm_clamped`` / ``feed_clamped`` warnings, in the findings' shape.
    findings: tuple[dict, ...] = ()


def rpm_for(surface_m_min: float, diameter_mm: float) -> float:
    """Spindle speed for a surface speed (m/min) at a diameter (mm)."""
    return 1000.0 * surface_m_min / (math.pi * diameter_mm)


def chipload_at(bands: float | tuple[tuple[float, float], ...], diameter_mm: float) -> float:
    """The chip load for a tool diameter, interpolated between the bands (see the module's docstring)."""
    if isinstance(bands, (int, float)):
        return float(bands)
    first_d, first_c = bands[0]
    if diameter_mm <= first_d:
        return first_c * diameter_mm / first_d
    for (d0, c0), (d1, c1) in zip(bands, bands[1:]):
        if diameter_mm <= d1:
            return c0 + (c1 - c0) * (diameter_mm - d0) / (d1 - d0)
    return bands[-1][1]


def chip_thinning(engagement: float) -> float:
    """The radial chip thinning factor for a radial engagement (fraction of the diameter): 1 at half or more,
    ``1 / (2 sqrt(e (1 - e)))`` under it, capped at :data:`CHIP_THINNING_CAP`."""
    if not 0 < engagement <= 1:
        raise ValueError(f"radial engagement is a fraction of the tool diameter over 0 and up to 1, got {engagement:g}")
    if engagement >= 0.5:
        return 1.0
    return min(1.0 / (2.0 * math.sqrt(engagement * (1.0 - engagement))), CHIP_THINNING_CAP)


def _material_numbers(material: "Material") -> tuple[float, float | tuple[tuple[float, float], ...]]:
    surface, bands = MATERIAL_TABLE.get(material.name, (None, None))
    if material.surface_m_min is not None:
        surface = material.surface_m_min
    if material.chipload_mm is not None:
        bands = material.chipload_mm
    if surface is None or bands is None:
        raise ValueError(f"material: {material.name} is not in the table, so it needs surface_m_min and chipload_mm")
    return surface, bands


def _mm(value: float) -> str:
    return f"{value:,.0f}"


def feeds_for(tool: "Tool", material: "Material", machine: "Machine", *, where: str = "", rpm: float | None = None,
              feed_mm_min: float | None = None, plunge_mm_min: float | None = None,
              radial_engagement: float | None = None) -> Feeds:
    """Feeds and speeds for ``tool`` cutting ``material`` on ``machine``.

    ``where`` names the operation in findings (``operations[2] (pocket_2d)``). ``rpm``, ``feed_mm_min`` and
    ``plunge_mm_min`` are the operation's overrides. ``radial_engagement`` (a fraction of the diameter) is passed by
    adaptive clearing, whose engagement is held constant; under 0.5 it raises the chip load (chip thinning).
    """
    if tool.diameter_mm is None:
        raise ValueError(f"{tool.id}: a {tool.type} has no diameter to work out a spindle speed from")
    diameter = float(tool.diameter_mm)
    surface, bands = _material_numbers(material)
    hss = HSS_FACTOR if tool.material == "hss" and tool.type != "tap" else 1.0   # a tap's factor is its own speed
    surface *= _SPEED_FACTOR.get(tool.type, 1.0) * hss
    base_chip = chipload_at(bands, diameter)
    label = f"{where}, " if where else ""
    item = [{"text": f"{where} {tool.id}".strip(), "tool": tool.id, **({"operation": where} if where else {})}]
    found: list[dict] = []

    # Per revolution (hole makers) or per tooth x teeth (milling): the mm the tool advances each turn.
    if tool.type == "tap":
        pitch = float(tool.size["pitch_mm"])
        per_rev, chip = pitch, None
    elif tool.type in _DRILLS:
        per_rev = chip = 3.0 * base_chip
    elif tool.type == "reamer":
        per_rev = chip = 2.0 * 3.0 * base_chip
    elif tool.type in _BORING:
        per_rev = chip = 1.5 * base_chip
    else:
        chip = base_chip * (0.5 if tool.type == "thread_mill" else 1.0)
        teeth = tool.flutes or (4 if tool.type == "face_mill" else 2)
        plunge_per_rev = chip * teeth          # a plunge's chip is never thinned
        if radial_engagement is not None:
            chip *= chip_thinning(radial_engagement)
        per_rev = chip * teeth

    want_rpm = float(round(rpm if rpm is not None else rpm_for(surface, diameter)))
    spindle = want_rpm
    if machine.max_rpm is not None and spindle > machine.max_rpm:
        spindle = float(math.floor(machine.max_rpm))
        source = "the operation asks for" if rpm is not None else "the material and tool call for"
        found.append(finding(
            "warning", "rpm_clamped",
            f"{label}{tool.id} runs at the machine's top speed of {_mm(spindle)} rpm, under the {_mm(want_rpm)} rpm "
            f"{source}",
            "the feed follows the slower spindle, so the chip load per tooth is kept; the cut takes longer"
            if feed_mm_min is None or tool.type == "tap" else
            "the operation's feed is kept, so each tooth takes a bigger chip than planned; check the feed",
            item))

    if tool.type == "tap":
        feed = spindle * per_rev
        if feed_mm_min is not None and abs(feed_mm_min - feed) > _TAP_AGREE * feed:
            wanted = want_rpm * per_rev
            if spindle < want_rpm and abs(feed_mm_min - wanted) <= _TAP_AGREE * wanted:
                # The stated feed was rpm x pitch at the speed asked for; the spindle was clamped, so it follows.
                found.append(finding(
                    "warning", "feed_clamped",
                    f"{label}{tool.id} taps at {feed:g} mm/min, not the {feed_mm_min:g} mm/min the operation asks "
                    f"for: the spindle runs at {_mm(spindle)} rpm, not {_mm(want_rpm)}",
                    "a tap's feed is exactly rpm x pitch, so the feed slows with the spindle and the two still agree",
                    item))
            else:
                raise ValueError(f"{where or tool.id}.feed_mm_min: a tap feeds exactly rpm x pitch, {_mm(spindle)} x "
                                 f"{per_rev:g} = {feed:g} mm/min, not {feed_mm_min:g}; leave feed_mm_min out or set "
                                 "rpm")
        if machine.max_feed_mm_min is not None and feed > machine.max_feed_mm_min:
            slower = float(math.floor(machine.max_feed_mm_min / per_rev))
            found.append(finding(
                "warning", "feed_clamped",
                f"{label}{tool.id} taps at {_mm(slower)} rpm, not {_mm(spindle)} rpm: rpm x pitch must stay under the "
                f"machine's {_mm(machine.max_feed_mm_min)} mm/min feed limit",
                "a tap's feed is exactly rpm x pitch, so the spindle slows with the feed and the two still agree",
                item))
            spindle, feed = slower, slower * per_rev
        plunge = feed
    else:
        feed = feed_mm_min if feed_mm_min is not None else spindle * per_rev
        if tool.type in (*_DRILLS, "reamer", *_BORING):
            plunge = plunge_mm_min if plunge_mm_min is not None else feed
        elif plunge_mm_min is not None:
            plunge = plunge_mm_min
        elif feed_mm_min is not None:
            plunge = PLUNGE_FRACTION * feed
        else:
            plunge = PLUNGE_FRACTION * spindle * plunge_per_rev
        limit = machine.max_feed_mm_min
        if limit is not None and (feed > limit or plunge > limit):
            over = [f"feed {_mm(feed)}" if feed > limit else "", f"plunge {_mm(plunge)}" if plunge > limit else ""]
            found.append(finding(
                "warning", "feed_clamped",
                f"{label}{tool.id} feeds at the machine's limit of {_mm(limit)} mm/min, under the "
                f"{' and '.join(word for word in over if word)} mm/min "
                f"{'the operation asks for' if feed_mm_min is not None or plunge_mm_min is not None else 'worked out'}",
                "each tooth takes a smaller chip than planned and the cut takes longer; a chip that is too thin rubs "
                "and wears the tool, so a smaller or fewer-fluted tool may suit this machine better",
                item))
            feed, plunge = min(feed, limit), min(plunge, limit)

    chip_out = None if (feed_mm_min is not None or tool.type == "tap") else chip
    return Feeds(spindle, feed, plunge, chip_out, math.pi * diameter * spindle / 1000.0, tuple(found))


def feeds_for_operation(operation: "Operation", tool: "Tool", material: "Material", machine: "Machine", *,
                        where: str = "", radial_engagement: float | None = None) -> Feeds:
    """:func:`feeds_for` with the operation's overrides."""
    return feeds_for(tool, material, machine, where=where or operation.op, rpm=operation.rpm,
                     feed_mm_min=operation.feed_mm_min, plunge_mm_min=operation.plunge_mm_min,
                     radial_engagement=radial_engagement)
