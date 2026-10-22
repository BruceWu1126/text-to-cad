from __future__ import annotations
from cadgen.engine import configuration, couple, glb, motion, xbf
from math import cos, pi, sin, tau

import build123d as bd


# Units: millimeters.
# Origin: sun/ring center on the carrier axis.
# XY: gear plane. +Z: gear and pin axes.

GEAR_THICKNESS = 8.0
GEAR_BOTTOM_Z = 0.0

SUN_TEETH = 24
SUN_PITCH_DIAMETER = 48.0
SUN_ROOT_DIAMETER = 42.0
SUN_OUTSIDE_DIAMETER = 54.0
SUN_BORE_DIAMETER = 10.0

PLANET_TEETH = 18
PLANET_PITCH_DIAMETER = 36.0
PLANET_ROOT_DIAMETER = 31.0
PLANET_OUTSIDE_DIAMETER = 41.0
PLANET_CENTER_RADIUS = 42.0
PLANET_COUNT = 3
PLANET_BORE_DIAMETER = 6.8

EXTERNAL_TOOTH_ROOT_SPAN_FRACTION = 0.42
EXTERNAL_TOOTH_TIP_SPAN_FRACTION = 0.18


def _srgb_channel_to_linear(channel: int) -> float:
    value = channel / 255.0
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def _srgb_color(hex_color: str) -> tuple[float, float, float, float]:
    """Linear RGBA for an ``#rrggbb`` sRGB hex: build123d's ``Shape.color``
    setter builds the Color on assignment."""
    value = hex_color.removeprefix("#")
    if len(value) != 6:
        raise ValueError(f"Expected #rrggbb color, got {hex_color!r}")
    return (
        _srgb_channel_to_linear(int(value[0:2], 16)),
        _srgb_channel_to_linear(int(value[2:4], 16)),
        _srgb_channel_to_linear(int(value[4:6], 16)),
        1.0,
    )


# Match the docs app's dark-mode hero render source colors.
PLANET_COLORS = (
    _srgb_color("#61d4f6"),
    _srgb_color("#a6ea90"),
    _srgb_color("#ed9ee5"),
)

RING_TEETH = 60
RING_INTERNAL_PITCH_DIAMETER = 120.0
RING_INTERNAL_ROOT_DIAMETER = 126.0
RING_INTERNAL_TIP_DIAMETER = 115.0
RING_OUTSIDE_DIAMETER = 140.0
RING_TOOTH_ROOT_SPAN_FRACTION = 0.68
RING_TOOTH_TIP_SPAN_FRACTION = 0.34
RING_COLOR = _srgb_color("#c5adef")
SUN_COLOR = _srgb_color("#fdce76")
CARRIER_COLOR = _srgb_color("#bfcec8")
PIN_COLOR = _srgb_color("#818993")

CARRIER_DIAMETER = 105.0
CARRIER_BOTTOM_Z = -5.0
CARRIER_TOP_Z = -1.0
CARRIER_THICKNESS = CARRIER_TOP_Z - CARRIER_BOTTOM_Z

PIN_DIAMETER = 6.0
PIN_HEIGHT = 14.0
PIN_BOTTOM_Z = -5.0
PIN_CARRIER_CLEARANCE_DIAMETER = 6.4


def _polar_point(radius: float, angle: float) -> tuple[float, float]:
    return (radius * cos(angle), radius * sin(angle))


def _trapezoid_tooth_profile(
    *,
    teeth: int,
    root_radius: float,
    tip_radius: float,
    phase: float,
    root_span_fraction: float = 0.72,
    tip_span_fraction: float = 0.38,
) -> list[tuple[float, float]]:
    """Return a closed-profile point loop with straight-sided schematic teeth."""
    pitch_angle = tau / teeth
    points: list[tuple[float, float]] = []

    for tooth_index in range(teeth):
        center_angle = phase + tooth_index * pitch_angle
        points.extend(
            (
                _polar_point(root_radius, center_angle - root_span_fraction * pitch_angle / 2.0),
                _polar_point(tip_radius, center_angle - tip_span_fraction * pitch_angle / 2.0),
                _polar_point(tip_radius, center_angle + tip_span_fraction * pitch_angle / 2.0),
                _polar_point(root_radius, center_angle + root_span_fraction * pitch_angle / 2.0),
            )
        )

    return points


def _make_external_gear(
    *,
    label: str,
    teeth: int,
    root_diameter: float,
    outside_diameter: float,
    phase: float,
    center: tuple[float, float] = (0.0, 0.0),
    bore_diameter: float | None = None,
    color: bd.Color | None = None,
):
    with bd.BuildPart() as gear:
        with bd.BuildSketch(bd.Plane.XY):
            bd.Polygon(
                _trapezoid_tooth_profile(
                    teeth=teeth,
                    root_radius=root_diameter / 2.0,
                    tip_radius=outside_diameter / 2.0,
                    phase=phase,
                    root_span_fraction=EXTERNAL_TOOTH_ROOT_SPAN_FRACTION,
                    tip_span_fraction=EXTERNAL_TOOTH_TIP_SPAN_FRACTION,
                ),
                align=None,
            )
        bd.extrude(amount=GEAR_THICKNESS)

        if bore_diameter is not None:
            with bd.Locations(bd.Location((0.0, 0.0, -0.5))):
                bd.Cylinder(
                    radius=bore_diameter / 2.0,
                    height=GEAR_THICKNESS + 1.0,
                    align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN),
                    mode=bd.Mode.SUBTRACT,
                )

    part = gear.part.moved(bd.Location((center[0], center[1], GEAR_BOTTOM_Z)))
    part.label = label
    part.color = color
    return part


def _make_internal_ring_gear(*, label: str, phase: float, color: bd.Color | None = None):
    with bd.BuildPart() as ring:
        bd.Cylinder(
            radius=RING_OUTSIDE_DIAMETER / 2.0,
            height=GEAR_THICKNESS,
            align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN),
        )

        with bd.BuildSketch(bd.Plane.XY):
            bd.Polygon(
                _trapezoid_tooth_profile(
                    teeth=RING_TEETH,
                    root_radius=RING_INTERNAL_ROOT_DIAMETER / 2.0,
                    tip_radius=RING_INTERNAL_TIP_DIAMETER / 2.0,
                    phase=phase,
                    root_span_fraction=RING_TOOTH_ROOT_SPAN_FRACTION,
                    tip_span_fraction=RING_TOOTH_TIP_SPAN_FRACTION,
                ),
                align=None,
            )
        bd.extrude(amount=GEAR_THICKNESS, mode=bd.Mode.SUBTRACT)

    part = ring.part
    part.label = label
    part.color = color
    return part


def _make_carrier_plate():
    with bd.BuildPart() as carrier:
        with bd.Locations(bd.Location((0.0, 0.0, CARRIER_BOTTOM_Z))):
            bd.Cylinder(
                radius=CARRIER_DIAMETER / 2.0,
                height=CARRIER_THICKNESS,
                align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN),
            )
        for index in range(PLANET_COUNT):
            center = _polar_point(PLANET_CENTER_RADIUS, tau * index / PLANET_COUNT)
            with bd.Locations(bd.Location((center[0], center[1], CARRIER_BOTTOM_Z - 0.1))):
                bd.Cylinder(
                    radius=PIN_CARRIER_CLEARANCE_DIAMETER / 2.0,
                    height=CARRIER_THICKNESS + 0.2,
                    align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN),
                    mode=bd.Mode.SUBTRACT,
                )

    part = carrier.part
    part.label = "carrier_plate"
    part.color = CARRIER_COLOR
    return part


def _make_planet_pin(*, label: str, center: tuple[float, float]):
    with bd.BuildPart() as pin:
        with bd.Locations(bd.Location((center[0], center[1], PIN_BOTTOM_Z))):
            bd.Cylinder(
                radius=PIN_DIAMETER / 2.0,
                height=PIN_HEIGHT,
                align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN),
            )

    part = pin.part
    part.label = label
    part.color = PIN_COLOR
    return part


def _planet_center(index: int) -> tuple[float, float]:
    angle = tau * index / PLANET_COUNT
    return _polar_point(PLANET_CENTER_RADIUS, angle)


# ---------------------------------------------------------------------------
# Kinematics: build123d joints, connected at 0 where the parts stand. The ring
# is the ground; the sun and the carrier turn on it, each planet turns on the
# carrier, and each pin is fastened to the carrier. The gear train is one
# locked coupling, `drive` (the sun's angle), over the exact fixed-ring
# planetary ratios: 3.5 sun revolutions (1260 deg) return the carrier to 360
# deg and every gear to an equivalent tooth phase, so `meshCycle`, the drive
# swept once over 6 s and looped, plays without a seam.
# ---------------------------------------------------------------------------

_CARRIER_RATIO = SUN_TEETH / (SUN_TEETH + RING_TEETH)          # 24/84
_PLANET_RATIO = -(SUN_TEETH / PLANET_TEETH) * (1 - _CARRIER_RATIO)
_FULL_MESH_CYCLE_DEG = 1260


def _revolute(fixed, moving, label: str, center: tuple[float, float], limits: tuple[float, float]) -> None:
    """``moving`` turns about +Z through ``center`` on ``fixed``."""
    point = (center[0], center[1], 0.0)
    bd.RevoluteJoint(label, fixed, axis=bd.Axis(point, (0.0, 0.0, 1.0)), angular_range=limits)
    bd.RigidJoint(label, moving, bd.Plane(origin=point, z_dir=(0.0, 0.0, 1.0)).location)
    fixed.joints[label].connect_to(moving.joints[label], angle=0.0)


def _fastened(fixed, moving, label: str, center: tuple[float, float]) -> None:
    """``moving`` fixed to ``fixed`` where it stands."""
    location = bd.Location((center[0], center[1], 0.0))
    bd.RigidJoint(label, fixed, location)
    bd.RigidJoint(label, moving, location)
    fixed.joints[label].connect_to(moving.joints[label])


@xbf(out="../../XBF/planetary_gear_assembly/planetary_gear_assembly.xbf")
@glb(out="../../XBF/planetary_gear_assembly/planetary_gear_assembly.glb")
def planetary_gear_assembly():
    """Return a labeled simplified planetary gear assembly in millimeters."""
    sun_pitch_angle = tau / SUN_TEETH
    ring_pitch_angle = tau / RING_TEETH

    parts = [
        _make_carrier_plate(),
        _make_internal_ring_gear(
            label="ring_gear_60_internal_teeth",
            phase=-ring_pitch_angle / 2.0,
            color=RING_COLOR,
        ),
        _make_external_gear(
            label="sun_gear_24_teeth",
            teeth=SUN_TEETH,
            root_diameter=SUN_ROOT_DIAMETER,
            outside_diameter=SUN_OUTSIDE_DIAMETER,
            phase=-sun_pitch_angle / 2.0,
            bore_diameter=SUN_BORE_DIAMETER,
            color=SUN_COLOR,
        ),
    ]

    for index in range(PLANET_COUNT):
        planet_angle = tau * index / PLANET_COUNT
        center = _planet_center(index)
        parts.append(
            _make_external_gear(
                label=f"planet_gear_{index + 1}_18_teeth",
                teeth=PLANET_TEETH,
                root_diameter=PLANET_ROOT_DIAMETER,
                outside_diameter=PLANET_OUTSIDE_DIAMETER,
                phase=planet_angle,
                center=center,
                bore_diameter=PLANET_BORE_DIAMETER,
                color=PLANET_COLORS[index],
            )
        )
        parts.append(_make_planet_pin(label=f"planet_pin_{index + 1}", center=center))

    carrier, ring, sun = parts[:3]
    # Each planet and pin on the carrier before the carrier turns on the ring.
    for index in range(PLANET_COUNT):
        planet, pin = parts[3 + 2 * index], parts[4 + 2 * index]
        center = _planet_center(index)
        limits = (-4 * _FULL_MESH_CYCLE_DEG, 4 * _FULL_MESH_CYCLE_DEG)
        _revolute(carrier, planet, f"planet{index + 1}", center, limits)
        _fastened(carrier, pin, f"pin{index + 1}", center)
    _revolute(ring, sun, "sun", (0.0, 0.0), (-_FULL_MESH_CYCLE_DEG, _FULL_MESH_CYCLE_DEG))
    _revolute(ring, carrier, "carrier", (0.0, 0.0), (-360.0, 360.0))

    assembly = bd.Compound(
        obj=parts,
        children=parts,
        label="simplified_planetary_gear_assembly",
    )
    planets = {f"planet{index + 1}": _PLANET_RATIO for index in range(PLANET_COUNT)}
    couple(assembly, "drive", range=(0, _FULL_MESH_CYCLE_DEG), lock=True,
           sun=1.0, carrier=_CARRIER_RATIO, **planets)
    configuration(assembly, "quarter_cycle", drive=_FULL_MESH_CYCLE_DEG / 4)
    configuration(assembly, "half_cycle", drive=_FULL_MESH_CYCLE_DEG / 2)
    return motion(assembly, "meshCycle", sweep={"drive": (0, _FULL_MESH_CYCLE_DEG)}, duration=6,
                  ease="linear", loop="loop")


if __name__ == "__main__":
    planetary_gear_assembly()
