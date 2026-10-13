"""Setups: where the part sits on the machine, and the work coordinates (WCS) a setup's G-code is written in.

Each setup gets a :class:`SetupFrame`: a rotation and a WCS origin (:class:`Frame`), the stock and part bounds in that
frame, the absolute heights the tool moves at, and the fixture's envelope.

The frame
    The setup's Z axis is its ``direction``: the tool axis in the part's frame, pointing from the part to the spindle
    (``+Z``: the tool comes down from above; ``-Z``: the part is turned over). A face selector (``#o1.f4``) points it
    along that face's outward normal, read through the ``face_normal`` the caller passes (features belong to another
    module). The rotation is the smallest one that takes the part's +Z to the direction; for ``-Z`` it is the half turn
    about the part's X axis (X stays, Y and Z flip). So ``+Z`` keeps the part's axes, ``+X`` maps the part's X, Y, Z to
    the setup's Z, Y, -X, and so on.

The origin
    A keyword places the WCS zero on the stock's or the part's bounding box in the setup's frame: ``*_top_*`` at its
    highest Z, ``*_bottom_*`` its lowest, ``*_center`` the middle of X and Y (``part_bbox_center`` the middle of all
    three), ``*_corner`` the min-X, min-Y corner (front left, looking down the tool). A point ``[x, y, z]`` is in the
    part's frame.

Heights
    ``clearance_mm`` and ``retract_mm`` are above the stock top; :attr:`SetupFrame.clearance_z` and
    :attr:`SetupFrame.retract_z` are those heights as Z in the setup's frame, and :meth:`SetupFrame.above_stock` gives
    one for an operation that overrides them.

The fixture
    Boxes and cylinders in the setup's frame, for the collision checks (:class:`FixtureEnvelope`). A size the job does
    not state takes the default below and is listed in ``assumed``, so a finding can say what it assumed.

    - vise: modelled as it stands on the table. The stock sits on two parallels ``parallels_mm`` tall (0: on the
      vise's floor), so the floor is ``parallels_mm`` under the stock's bottom. Two jaws ``jaw_height_mm`` tall stand
      on the floor and close along the setup's Y on the stock's front and back faces, ``jaw_width_mm`` wide in X
      about the stock's centre. ``grip_mm`` is not a size of its own: it is the height of the jaws' top above the
      stock's bottom, ``jaw_height_mm - parallels_mm``. So any two of the three give the third; a job that states
      all three must state them agreeing within 0.01 mm, else a ValueError names them. With ``grip_mm`` alone the
      jaws are the default height (or ``grip_mm``, when taller) and the parallels make up the rest. A grip of 0 or
      less (the parallels as tall as the jaws) holds nothing and is a ValueError. Jaws taller than the stock
      stand proud of its top; that is real, so it is no error: the jaws are in the envelope at their full height
      and ``notes`` says how far they stand above the stock top (``sizes["jaws_above_stock_mm"]`` holds the
      number), for a finding to word. The floor (a slab :data:`VISE_FLOOR_MM` thick, as wide as the jaws and
      reaching across both) and any parallels are in the envelope too, so a cut through the stock sees what is
      under it.
    - plate: a plate ``thickness_mm`` thick under the stock, wider than it by :data:`PLATE_BORDER_MM` all round.
    - chuck: a cylinder about the setup's Z axis through the stock's centre, ``diameter_mm`` across, its jaw face
      ``grip_mm`` above the stock's bottom and ``jaw_length_mm`` long below it.
    - collet: a cylinder ``diameter_mm`` across holding :data:`COLLET_GRIP_MM` of the stock's bottom,
      :data:`COLLET_LENGTH_MM` long.
    - sheet, none: nothing to collide with.

    ``assumed`` lists, by key, every size the fixture used that the job did not state (the ones a job cannot state,
    such as a vise's ``jaw_thickness_mm`` or a plate's ``border_mm``, always). ``notes`` says in plain words what
    the model adjusted or what stands out (a default grip cut to a short stock, jaws proud of the stock).

Reach
    :func:`reachable` says whether a 3-axis setup's tool can come at a surface from the direction it needs.

numpy and OCP are imported inside the functions; this module is stdlib-only at import.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from cadgen._internal.cam.stock import Bounds, StockSolid, bounds_of, occ_shape

__all__ = [
    "COLLET_GRIP_MM", "Cylinder", "Envelope", "FixtureEnvelope", "Frame", "PLATE_BORDER_MM", "SetupFrame",
    "VISE_DEFAULTS", "VISE_FLOOR_MM", "BoxVolume", "direction_vector", "fixture_envelope", "reachable", "rotation_to",
    "setup_frame", "setup_frames",
]

#: Digits coordinates are rounded to: 6 in mm is a nanometre, as in the toolpath.
_DIGITS = 6
_UNIT_DIGITS = 12

#: A vise's sizes when the job does not state them, mm.
VISE_DEFAULTS = {"jaw_height_mm": 40.0, "jaw_width_mm": 150.0, "parallels_mm": 0.0, "jaw_thickness_mm": 20.0,
                 "parallel_width_mm": 6.0, "floor_thickness_mm": 10.0}
VISE_FLOOR_MM = VISE_DEFAULTS["floor_thickness_mm"]
#: How closely a stated ``grip_mm`` must agree with ``jaw_height_mm - parallels_mm``, mm.
VISE_AGREE_MM = 0.01
#: How far a plate reaches past the stock on every side, mm, and its thickness when not stated.
PLATE_BORDER_MM = 25.0
PLATE_THICKNESS_MM = 12.0
#: A chuck's sizes when not stated: its diameter beyond the stock's on each side, its jaws' length, its grip, mm.
CHUCK_RIM_MM = 20.0
CHUCK_JAW_LENGTH_MM = 30.0
CHUCK_GRIP_MM = 10.0
#: How much of the stock a collet holds, and its rim beyond the stock when its diameter is not stated, mm.
COLLET_GRIP_MM = 15.0
COLLET_RIM_MM = 5.0
COLLET_LENGTH_MM = 40.0

#: The angle within which a surface's direction counts as the tool's, degrees (kernel noise, not a tolerance).
REACH_TOLERANCE_DEG = 0.01

_AXES = {"+X": (1, 0, 0), "-X": (-1, 0, 0), "+Y": (0, 1, 0), "-Y": (0, -1, 0), "+Z": (0, 0, 1), "-Z": (0, 0, -1)}


def _clean(value: float, digits: int = _DIGITS) -> float:
    value = round(float(value), digits)
    return 0.0 if value == 0 else value


def _vec(values, digits: int = _DIGITS) -> tuple[float, float, float]:
    return tuple(_clean(value, digits) for value in values)


def _unit(values) -> tuple[float, float, float]:
    """A direction or a rotation row: unitless, so rounded finer than a coordinate (6 digits would bend a
    rotation by a micrometre per metre)."""
    return _vec(values, _UNIT_DIGITS)


# --- frames --------------------------------------------------------------------------------------------

def direction_vector(direction: str, face_normal: Callable[[str], Sequence[float]] | None = None
                     ) -> tuple[float, float, float]:
    """The unit tool axis, in the part's frame, of a setup's ``direction``: an axis word or a face selector."""
    if direction in _AXES:
        return tuple(float(value) for value in _AXES[direction])
    if face_normal is None:
        raise ValueError(f"setup direction {direction}: a face selector needs the part's faces to read its normal")
    normal = [float(value) for value in face_normal(direction)]
    length = math.sqrt(sum(value * value for value in normal))
    if len(normal) != 3 or not length > 1e-12:
        raise ValueError(f"setup direction {direction}: the face has no normal to point the tool along (is it planar?)")
    return _unit(value / length for value in normal)


def rotation_to(direction: Sequence[float]) -> tuple[tuple[float, float, float], ...]:
    """The part-to-setup rotation for a tool axis: rows are the setup's X, Y, Z axes in the part's frame.

    The smallest rotation taking +Z to ``direction``; for -Z, the half turn about X.
    """
    import numpy as np

    d = np.asarray(direction, dtype=float)
    d = d / np.linalg.norm(d)
    z = np.array([0.0, 0.0, 1.0])
    c = float(d @ z)
    if c > 1 - 1e-15:
        setup_to_part = np.eye(3)
    elif c < -1 + 1e-15:
        setup_to_part = np.diag([1.0, -1.0, -1.0])
    else:
        v = np.cross(z, d)
        k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
        setup_to_part = np.eye(3) + k + k @ k / (1 + c)
    # setup_to_part's columns are the setup's axes in the part's frame; its transpose maps part to setup.
    return tuple(_unit(row) for row in setup_to_part.T)


@dataclass(frozen=True)
class Frame:
    """A setup's work coordinates: ``p_setup = rotation @ (p_part - origin)``."""

    #: Rows: the setup's X, Y, Z axes in the part's frame.
    rotation: tuple[tuple[float, float, float], ...]
    #: The WCS zero, in the part's frame, mm.
    origin: tuple[float, float, float]

    def _r(self):
        import numpy as np

        return np.asarray(self.rotation, dtype=float)

    def point_to_setup(self, points):
        """Points (one ``(3,)`` or many ``(n, 3)``) from the part's frame to the setup's, as a numpy array."""
        import numpy as np

        return (np.asarray(points, dtype=float) - np.asarray(self.origin)) @ self._r().T

    def point_to_part(self, points):
        import numpy as np

        return np.asarray(points, dtype=float) @ self._r() + np.asarray(self.origin)

    def vector_to_setup(self, vectors):
        """Directions (no translation) from the part's frame to the setup's."""
        import numpy as np

        return np.asarray(vectors, dtype=float) @ self._r().T

    def vector_to_part(self, vectors):
        import numpy as np

        return np.asarray(vectors, dtype=float) @ self._r()

    def trsf(self, *, to_setup: bool = True):
        """The OCP ``gp_Trsf`` from the part's frame to the setup's (or back)."""
        from OCP.gp import gp_Trsf

        r = self.rotation
        t = [-sum(r[row][col] * self.origin[col] for col in range(3)) for row in range(3)]
        trsf = gp_Trsf()
        trsf.SetValues(r[0][0], r[0][1], r[0][2], t[0],
                       r[1][0], r[1][1], r[1][2], t[1],
                       r[2][0], r[2][1], r[2][2], t[2])
        return trsf if to_setup else trsf.Inverted()

    def shape_to_setup(self, shape: Any) -> Any:
        """A copy of an OCP or build123d shape moved into the setup's frame, as an OCP shape."""
        return _moved(shape, self.trsf())

    def shape_to_part(self, shape: Any) -> Any:
        return _moved(shape, self.trsf(to_setup=False))


def _moved(shape: Any, trsf) -> Any:
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform

    return BRepBuilderAPI_Transform(occ_shape(shape), trsf, True).Shape()


# --- fixtures ------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class BoxVolume:
    """An axis-aligned box in the setup's frame."""

    name: str
    min: tuple[float, float, float]
    max: tuple[float, float, float]

    def shape(self):
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.gp import gp_Pnt

        return BRepPrimAPI_MakeBox(gp_Pnt(*self.min), gp_Pnt(*self.max)).Shape()


@dataclass(frozen=True)
class Cylinder:
    """A cylinder about the setup's Z axis direction: its axis passes through ``center`` (x, y), from ``z_min`` up to
    ``z_max``."""

    name: str
    center: tuple[float, float]
    radius: float
    z_min: float
    z_max: float

    def shape(self):
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        axis = gp_Ax2(gp_Pnt(self.center[0], self.center[1], self.z_min), gp_Dir(0, 0, 1))
        return BRepPrimAPI_MakeCylinder(axis, self.radius, self.z_max - self.z_min).Shape()


Envelope = BoxVolume | Cylinder


@dataclass(frozen=True)
class FixtureEnvelope:
    """What holds the stock, as simple volumes in the setup's frame."""

    type: str
    volumes: tuple[Envelope, ...] = ()
    #: The sizes it used, mm (stated or assumed), and the keys it assumed because the job did not state them.
    sizes: dict = field(default_factory=dict)
    assumed: tuple[str, ...] = ()
    #: Plain sentences about the model: a default cut to fit, jaws standing proud of the stock.
    notes: tuple[str, ...] = ()

    def shapes(self) -> list:
        return [volume.shape() for volume in self.volumes]


def fixture_envelope(fixture, stock_bounds: Bounds, *, where: str = "fixture") -> FixtureEnvelope:
    """The fixture's volumes around stock with ``stock_bounds`` (the setup's frame)."""
    (x0, y0, z0), (x1, y1, z1) = stock_bounds
    height = z1 - z0
    stated = dict(fixture.size)
    kind = fixture.type
    if kind in ("none", "sheet"):
        return FixtureEnvelope(kind)
    if kind == "vise":
        return _vise(stated, stock_bounds, where)
    if kind == "plate":
        thickness = stated.get("thickness_mm", PLATE_THICKNESS_MM)
        border = PLATE_BORDER_MM
        box = BoxVolume("plate", _vec((x0 - border, y0 - border, z0 - thickness)), _vec((x1 + border, y1 + border, z0)))
        assumed = (*(() if "thickness_mm" in stated else ("thickness_mm",)), "border_mm")
        return FixtureEnvelope(kind, (box,), {"thickness_mm": thickness, "border_mm": border}, assumed)
    center = ((x0 + x1) / 2, (y0 + y1) / 2)
    stock_radius = math.hypot(x1 - x0, y1 - y0) / 2
    if kind in ("chuck", "collet"):
        chuck = kind == "chuck"
        rim = CHUCK_RIM_MM if chuck else COLLET_RIM_MM
        default_grip = CHUCK_GRIP_MM if chuck else COLLET_GRIP_MM
        diameter = stated.get("diameter_mm", 2 * (stock_radius + rim))
        length_key = "jaw_length_mm" if chuck else "length_mm"
        length = stated.get(length_key, CHUCK_JAW_LENGTH_MM if chuck else COLLET_LENGTH_MM)
        notes: list[str] = []
        if "grip_mm" in stated:
            grip = stated["grip_mm"]
            if grip > height + 1e-9:
                raise ValueError(f"{where}.grip_mm: the jaws hold {grip:g} mm of stock that is {height:g} mm long; "
                                 "they would stand past its end")
        else:
            grip = min(default_grip, height)
            if grip < default_grip:
                notes.append(f"the {kind} holds all {grip:g} mm of the stock: its default grip of "
                             f"{default_grip:g} mm is more than the stock is long")
        top = z0 + grip
        volume = Cylinder(kind, _vec(center)[:2], _clean(diameter / 2), _clean(top - length), _clean(top))
        assumed = [key for key in ("diameter_mm", length_key, "grip_mm") if key not in stated]
        if "diameter_mm" not in stated:
            assumed.append("rim_mm")
        sizes = {"diameter_mm": diameter, length_key: length, "grip_mm": grip, "rim_mm": rim}
        return FixtureEnvelope(kind, (volume,), sizes, tuple(assumed), tuple(notes))
    raise ValueError(f"{where}.type: {kind!r} is not a fixture cadgen knows")


def _vise(stated: dict, stock_bounds: Bounds, where: str) -> FixtureEnvelope:
    """A vise as it stands: floor, parallels, jaws (see the module's docstring)."""
    (x0, y0, z0), (x1, y1, z1) = stock_bounds
    given = {key for key in ("jaw_height_mm", "parallels_mm", "grip_mm") if key in stated}
    jaw_height = stated.get("jaw_height_mm")
    parallels = stated.get("parallels_mm")
    grip = stated.get("grip_mm")
    if grip is None:
        jaw_height = VISE_DEFAULTS["jaw_height_mm"] if jaw_height is None else jaw_height
        parallels = VISE_DEFAULTS["parallels_mm"] if parallels is None else parallels
        grip = jaw_height - parallels
    elif jaw_height is not None and parallels is not None:
        if abs(jaw_height - parallels - grip) > VISE_AGREE_MM:
            raise ValueError(f"{where}: grip_mm ({grip:g}) must be jaw_height_mm ({jaw_height:g}) minus parallels_mm "
                             f"({parallels:g}), {jaw_height - parallels:g}: the jaws' top stands that far above the "
                             "stock's bottom; state two of the three")
    elif jaw_height is not None:
        parallels = jaw_height - grip
        if parallels < 0:
            raise ValueError(f"{where}: grip_mm ({grip:g}) is more than jaw_height_mm ({jaw_height:g}): the jaws' top "
                             "stands grip_mm above the stock's bottom, which rests on the floor at the lowest")
    elif parallels is not None:
        jaw_height = grip + parallels
    else:
        jaw_height = max(VISE_DEFAULTS["jaw_height_mm"], grip)
        parallels = jaw_height - grip
    if grip <= 0:
        raise ValueError(f"{where}: the jaws ({jaw_height:g} mm) are no taller than the parallels ({parallels:g} mm), "
                         "so they hold none of the stock")
    sizes = {"jaw_height_mm": jaw_height, "parallels_mm": parallels, "grip_mm": grip}
    for key in ("jaw_width_mm", "jaw_thickness_mm", "parallel_width_mm", "floor_thickness_mm"):
        sizes[key] = stated.get(key, VISE_DEFAULTS[key])
    assumed = tuple(key for key in ("jaw_height_mm", "parallels_mm", "grip_mm") if key not in given) + tuple(
        key for key in ("jaw_width_mm", "jaw_thickness_mm", "parallel_width_mm", "floor_thickness_mm")
        if key not in stated)
    floor = z0 - parallels
    top = floor + jaw_height
    cx = (x0 + x1) / 2
    half = sizes["jaw_width_mm"] / 2
    jaw = sizes["jaw_thickness_mm"]
    volumes: list[Envelope] = [
        BoxVolume("front jaw", _vec((cx - half, y0 - jaw, floor)), _vec((cx + half, y0, top))),
        BoxVolume("back jaw", _vec((cx - half, y1, floor)), _vec((cx + half, y1 + jaw, top))),
        BoxVolume("vise floor", _vec((cx - half, y0 - jaw, floor - sizes["floor_thickness_mm"])),
                  _vec((cx + half, y1 + jaw, floor))),
    ]
    if parallels > 0:
        width = min(sizes["parallel_width_mm"], (y1 - y0) / 4)
        volumes += [
            BoxVolume("front parallel", _vec((x0, y0, floor)), _vec((x1, y0 + width, z0))),
            BoxVolume("back parallel", _vec((x0, y1 - width, floor)), _vec((x1, y1, z0))),
        ]
    notes: list[str] = []
    proud = _clean(top - z1)
    sizes["jaws_above_stock_mm"] = max(proud, 0.0)
    if proud > 0:
        notes.append(f"the vise jaws stand {proud:g} mm above the stock top")
    return FixtureEnvelope("vise", tuple(volumes), sizes, assumed, tuple(notes))


# --- setups --------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SetupFrame:
    """One setup, placed: its frame, the stock and part in it, its heights and its fixture."""

    setup: Any                                  # job.Setup
    #: The tool axis in the part's frame, unit, pointing from the part to the spindle.
    direction: tuple[float, float, float]
    frame: Frame
    stock_bounds: Bounds                        # the setup's frame
    part_bounds: Bounds                         # the setup's frame
    fixture: FixtureEnvelope

    @property
    def stock_top_z(self) -> float:
        return self.stock_bounds[1][2]

    @property
    def stock_bottom_z(self) -> float:
        return self.stock_bounds[0][2]

    def above_stock(self, height_mm: float) -> float:
        """Z in the setup's frame of a height above the stock top (an operation's own clearance or retract)."""
        return _clean(self.stock_top_z + height_mm)

    @property
    def clearance_z(self) -> float:
        return self.above_stock(self.setup.clearance_mm)

    @property
    def retract_z(self) -> float:
        return self.above_stock(self.setup.retract_mm)


def _rotated_bounds(shape: Any, rotation) -> Bounds:
    """Bounds of ``shape`` rotated into a frame (no origin yet)."""
    return bounds_of(Frame(rotation, (0.0, 0.0, 0.0)).shape_to_setup(shape))


def _origin_in_rotated(origin: str, stock: Bounds, part: Bounds) -> tuple[float, float, float]:
    box = stock if origin.startswith("stock_") else part
    (x0, y0, z0), (x1, y1, z1) = box
    x, y = ((x0, y0) if origin.endswith("_corner") else ((x0 + x1) / 2, (y0 + y1) / 2))
    if "_top_" in origin:
        z = z1
    elif "_bottom_" in origin:
        z = z0
    else:
        z = (z0 + z1) / 2
    return (x, y, z)


def setup_frame(setup, part: Any, stock: StockSolid, *, face_normal: Callable[[str], Sequence[float]] | None = None,
                where: str | None = None) -> SetupFrame:
    """Place one setup: ``part`` is the part's shape (build123d or OCP), ``stock`` from :func:`make_stock`.

    ``face_normal(selector)`` gives a face's outward normal in the part's frame; it is needed only when the setup's
    direction is a face selector.
    """
    where = where or f"setup {setup.id}"
    direction = direction_vector(setup.direction, face_normal)
    rotation = rotation_to(direction)
    if isinstance(setup.origin, str):
        stock_r = _rotated_bounds(stock.shape, rotation)
        part_r = _rotated_bounds(part, rotation)
        at = _origin_in_rotated(setup.origin, stock_r, part_r)
        origin = _vec(sum(rotation[row][col] * at[row] for row in range(3)) for col in range(3))
    else:
        origin = _vec(setup.origin)
    frame = Frame(rotation, origin)
    stock_bounds = bounds_of(frame.shape_to_setup(stock.shape))
    part_bounds = bounds_of(frame.shape_to_setup(part))
    fixture = fixture_envelope(setup.fixture, stock_bounds, where=f"{where}: fixture")
    return SetupFrame(setup, direction, frame, stock_bounds, part_bounds, fixture)


def setup_frames(job, part: Any, stock: StockSolid, *,
                 face_normal: Callable[[str], Sequence[float]] | None = None) -> dict[str, SetupFrame]:
    """Every setup of the job, placed, by id in the job's order."""
    return {setup.id: setup_frame(setup, part, stock, face_normal=face_normal, where=f"setups[{index}]")
            for index, setup in enumerate(job.setups)}


def reachable(direction: str | Sequence[float], setup: SetupFrame, *,
              tolerance_deg: float = REACH_TOLERANCE_DEG) -> bool:
    """Whether a 3-axis setup's tool can come at a surface from ``direction``.

    ``direction`` (part's frame, an axis word like ``"+Z"`` or a vector) is the way the tool must approach from: the
    outward normal of the floor or face it cuts, or a hole's axis pointing out of its mouth. It is reachable when it is
    the setup's tool axis, within ``tolerance_deg``.
    """
    import numpy as np

    vector = np.asarray(_AXES[direction] if isinstance(direction, str) else direction, dtype=float)
    length = float(np.linalg.norm(vector))
    if not length > 1e-12:
        raise ValueError("reachable: the direction has no length")
    cosine = float(vector @ np.asarray(setup.direction, dtype=float)) / length
    return cosine >= math.cos(math.radians(tolerance_deg))
