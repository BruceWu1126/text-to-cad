"""Kinematics: an assembly's mechanism, as the document holds it, and its poses.

Authoring is build123d's joints and nothing else. A model puts ``RevoluteJoint``,
``LinearJoint``, ``CylindricalJoint``, ``BallJoint`` and ``RigidJoint`` on its parts and
places them with ``connect_to(..., angle=, position=, angles=)``; the values it connects
them at are the **as-built configuration**. ``configuration(assembly, name, **values)``
names another one. At document time the engine harvests every joint on every part of the
result (``harvest``) and pairs them two ways: by ``connect_to``, where the author used it; and,
for every joint no ``connect_to`` joined, by label -- a moving joint and a ``RigidJoint`` of the
same label on another part are a pair (the nearest in the tree, a tie refused), and so are two
rigid joints of one label each the other's nearest. ``connect_to`` wins: a joint it joined is
never matched by label. build123d's ``connect_to`` composes locations in each part's own
compound, so in a nested assembly it cannot place a part; there a model authors both joints
where the parts stand and lets the label pair them. Either way the document places nothing:
every value, the as-built one included, is computed from where the two frames stand in the
built tree. The pairs make a mechanism in ISO 10303-105's terms, the kinematics of STEP AP242:

- **Links** are sets of occurrences moving as one rigid body: two parts whose rigid joints
  are connected are one link. The **ground** link is every link no ``connect_to`` moved
  (the joint whose ``connect_to`` was called stays put; the other's part moves), with every
  part placed by no joint; a part below a jointed sub-assembly moves with it.
- **Pairs** join two links: a revolute joint is a ``REVOLUTE_PAIR``, a linear one a
  ``PRISMATIC_PAIR``, a cylindrical one a ``CYLINDRICAL_PAIR``, a ball a ``SPHERICAL_PAIR``,
  each ``_WITH_RANGE`` where the joint has finite limits. A pair is named by its moving
  joint's label (a label two pairs share is qualified with the path of the part that holds
  it: ``hand/left:knuckle``). Its two frames (``transform_item_1`` on link 1, the part
  holding the moving joint, ``transform_item_2`` on link 2, the part holding the rigid
  one) are AXIS2_PLACEMENT_3Ds in their links' frames, and coincide at value 0. Link 2's
  placement at value ``v`` is ``link_1 * item_1 * M(v) * item_2^-1``, with ``M`` a turn
  about Z (revolute), a slide along Z (prismatic), both (cylindrical: angle, then
  distance) or build123d's intrinsic X-Y-Z rotation (spherical: three angles). Angles are
  degrees, distances millimetres.
- A **gear pair** couples two pairs: the second's value is ``gear_ratio`` times the
  first's. build123d's joints cannot say one, so no model authors one; the document,
  its readers and every pose honour it.
- A **coupling** (``couple(assembly, name, *, range=(0, 1), lock=False, **joints)``) is one
  control driving several pairs by ratio: at value ``c`` each joint it names stands at its
  as-built value plus its ratio times ``c`` (a pair of several values takes a ratio per
  value), so ``c = 0`` is as built and a coupling never moves the build. A control, not a
  constraint: each joint still poses alone, unless the coupling is locked (a gear train),
  which makes it its joints' only control. Its name poses wherever a joint's does --
  configurations, motions and ``--pose`` -- and configurations and motions resolve it to pair
  values at document time; couplings sharing a joint add. A gear pair cannot say one: it
  couples single values with no offset, so a door's turn and travel, or a 0-1 control, are
  out of its reach. AP242 has no such entity: it is the engine's own (``cadgen_coupling``).

A link's frame is its first member occurrence's placement as built (the ground's, the
world's when it has none).

**Motions** (``motion(assembly, name, ...)``) are definitions, kept beside the configurations:
waypoints -- each a time and a configuration's name or joint values, the rest as built -- an
ease between them (``cubic``: each step eased in and out, resting at every waypoint;
``linear``: constant speed; ``spline``: smooth through every waypoint) and a loop (none,
``loop``, ``pingpong``). A sweep is two waypoints. A function of time is stored as its
samples (``spline``), at times chosen until the samples' spline stays within the tolerance of
the function at every check point between them: 1e-3 radian for an angle, 1e-3 of the
model's size (its bounding box's diagonal, from the B-rep's geometry) for a distance. So the document holds poses and motion definitions, never baked frames or clips; a
GLB export bakes each motion into a clip (``export/motion.py``).

In the document the mechanism is a subtree under ``Main():100``, marked by a
``TDataStd_UAttribute`` of ``GUID`` and read by that GUID, of standard attributes only
(``TDataStd_Name``, ``TDataStd_NamedData`` for an entity's AP242 name and attributes,
``TDataStd_ReferenceList``, ``TDataStd_RealArray``, ``TDataStd_IntegerArray``,
``XCAFDoc_AssemblyItemRef`` for an occurrence):

    0:1:100 "Kinematics"       UAttribute(GUID), NamedData{schema, angle_unit, length_unit}
      :1 <name>                NamedData{entity: MECHANISM_REPRESENTATION}, ReferenceList[ground link]
        :1 "links"
          :k <name>            NamedData{entity: KINEMATIC_LINK}
            :i                 AssemblyItemRef(occurrence: its component labels from the root)
        :2 "pairs"
          :j <label>           NamedData{entity, transform_item_1[9], transform_item_2[9],
                                         lower_/upper_limit_..., gear_ratio}, ReferenceList[link 1, link 2]
        :3 "as built"          NamedData{entity: MECHANISM_STATE_REPRESENTATION},
                               ReferenceList[pair per value], RealArray[values], IntegerArray[dof per value]
        :4 "configurations"
          :s <name>            as "as built"
        :5 "motions"
          :m <name>            NamedData{entity: INTERPOLATED_CONFIGURATION_SEQUENCE, cadgen_ease, cadgen_loop}
            :k                 NamedData{entity: INTERPOLATED_CONFIGURATION_SEGMENT, t_parameter}, and its state:
                               ReferenceList[the configuration it names], or as "as built"
        :6 "couplings"
          :c <name>            NamedData{entity: cadgen_coupling, lower_limit, upper_limit, lock (1 when locked)},
                               ReferenceList[pair per term], RealArray[ratio per term], IntegerArray[value index per term]

STEP carries none of it: OCCT reads AP242 kinematics but cannot write them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

__all__ = ["CONFIGURATIONS", "COUPLINGS", "GUID", "MOTIONS", "Coupling", "KinematicsError", "Link", "Mechanism",
           "Motion", "Pair", "Timeline", "block", "configuration", "couple", "harvest", "motion", "posed_structure",
           "read", "resolve", "write"]

GUID = "5b0e7a64-3c1d-4f8e-a2b9-6d4c1e7f9a20"
TAG = 100  # Main():100, clear of XCAFDoc_DocumentTool's tool labels (1..10)
SCHEMA = "cadgen.kinematics/1"
# The attribute ``configuration`` keeps a shape's named configurations in, ``motion`` its motions
# and ``couple`` its couplings.
CONFIGURATIONS = "xbf_configurations"
MOTIONS = "xbf_motions"
COUPLINGS = "xbf_couplings"
# A coupling's entity: the engine's own, AP242 having no virtual degree of freedom.
COUPLING = "cadgen_coupling"
EASES = ("cubic", "linear", "spline")
LOOPS = (None, "loop", "pingpong")
# How near a motion's stored samples (a function's) and a GLB's baked curves keep to the truth:
# an angle within ANGULAR_TOLERANCE radians, a distance within LINEAR_TOLERANCE of the size.
ANGULAR_TOLERANCE = 1e-3
LINEAR_TOLERANCE = 1e-3
# At most this many samples of a function, or keys of a baked clip.
MOST_KEYS = 4097

ROTATION = ("deg", "lower_limit_actual_rotation", "upper_limit_actual_rotation")
TRANSLATION = ("mm", "lower_limit_actual_translation", "upper_limit_actual_translation")
# A pair's kind: its AP242 entity and its values, each with its unit and limit names.
KINDS: dict[str, tuple[str, tuple]] = {
    "revolute": ("REVOLUTE_PAIR", (ROTATION,)),
    "prismatic": ("PRISMATIC_PAIR", (TRANSLATION,)),
    "cylindrical": ("CYLINDRICAL_PAIR", (ROTATION, TRANSLATION)),
    "spherical": ("SPHERICAL_PAIR", (("deg", "lower_limit_roll", "upper_limit_roll"),
                                     ("deg", "lower_limit_pitch", "upper_limit_pitch"),
                                     ("deg", "lower_limit_yaw", "upper_limit_yaw"))),
    "gear": ("GEAR_PAIR", ()),
}
_BY_ENTITY = {entity: kind for kind, (entity, _dofs) in KINDS.items()}
_STRING_KEYS = ("schema", "angle_unit", "length_unit", "entity")


class KinematicsError(ValueError):
    """A mechanism the engine will not keep or pose, said by name."""


@dataclass
class Link:
    name: str
    members: list[tuple[int, ...]]  # occurrences, as component indices from the root


@dataclass
class Pair:
    label: str
    kind: str
    links: tuple[int, int]
    frames: tuple[tuple[float, ...], tuple[float, ...]] | None  # two AXIS2_PLACEMENT_3Ds: origin, Z, X
    limits: tuple[tuple[float | None, float | None], ...]
    ratio: float | None = None  # a gear pair's
    # Why the pair has no value, where the built tree contradicts it: kept, warned, never posed.
    inconsistent: str | None = None

    @property
    def dofs(self) -> int:
        return len(KINDS[self.kind][1])


@dataclass
class Motion:
    """A motion's definition: its keys, each a time (seconds, the first 0, increasing) and a
    configuration's name or pair values (by pair label, the rest as built), the ease between
    them and the loop."""

    name: str
    keys: list[tuple[float, str | None, dict[str, tuple[float, ...]]]]
    ease: str = "cubic"
    loop: str | None = None

    @property
    def duration(self) -> float:
        return self.keys[-1][0]

    def labels(self) -> set[str]:
        return {label for _t, _name, values in self.keys for label in values}

    def signature(self) -> tuple:
        return (self.name, tuple((t, name, tuple(sorted(values.items()))) for t, name, values in self.keys),
                self.ease, self.loop)


@dataclass
class Coupling:
    """One control driving several pairs: at value ``c`` each pair it names stands at its
    as-built values plus ``ratios[pair]`` (one per value of the pair) times ``c``; ``range`` is
    the control's, holding 0 (as built); a locked coupling is its pairs' only control."""

    name: str
    ratios: dict[str, tuple[float, ...]]
    range: tuple[float, float]
    lock: bool = False

    def signature(self) -> tuple:
        return (self.name, tuple(sorted(self.ratios.items())), tuple(self.range), self.lock)


@dataclass
class Mechanism:
    name: str
    links: list[Link]
    ground: int
    pairs: list[Pair]
    as_built: dict[str, tuple[float, ...]]
    configurations: dict[str, dict[str, tuple[float, ...]]] = field(default_factory=dict)
    # What the harvest found inconsistent, each said by name: the document is written all the same.
    warnings: list[str] = field(default_factory=list)
    motions: dict[str, Motion] = field(default_factory=dict)
    couplings: dict[str, Coupling] = field(default_factory=dict)

    def signature(self) -> tuple:
        return (self.name, tuple((link.name, tuple(link.members)) for link in self.links), self.ground,
                tuple((p.label, p.kind, p.links, p.frames, p.limits, p.ratio, p.inconsistent) for p in self.pairs),
                tuple(sorted(self.as_built.items())),
                tuple((name, tuple(sorted(values.items()))) for name, values in sorted(self.configurations.items())),
                tuple(self.warnings), tuple(motion.signature() for _name, motion in sorted(self.motions.items())),
                tuple(coupling.signature() for _name, coupling in sorted(self.couplings.items())))


# --- matrices ------------------------------------------------------------------------------


def _numpy(location: Any) -> np.ndarray:
    """A TopLoc_Location, a gp_Trsf or a build123d Location as a 4x4 matrix."""
    if hasattr(location, "wrapped"):
        location = location.wrapped
    trsf = location.Transformation() if hasattr(location, "Transformation") else location
    found = np.eye(4)
    for row in range(3):
        for column in range(4):
            found[row, column] = trsf.Value(row + 1, column + 1)
    return found


def _ax2(matrix: np.ndarray) -> tuple[float, ...]:
    """An AXIS2_PLACEMENT_3D of a rigid placement: location, axis (Z), ref_direction (X)."""
    return tuple(float(value) for value in (*matrix[:3, 3], *matrix[:3, 2], *matrix[:3, 0]))


def frame_matrix(ax2: tuple[float, ...] | list[float]) -> np.ndarray:
    """The placement an AXIS2_PLACEMENT_3D stands for, as AP242 builds it: Z its axis, X its
    ref_direction made normal to Z, Y their cross product."""
    origin, z, x = np.array(ax2[0:3], float), np.array(ax2[3:6], float), np.array(ax2[6:9], float)
    z = z / np.linalg.norm(z)
    x = x - np.dot(x, z) * z
    x = x / np.linalg.norm(x)
    found = np.eye(4)
    found[:3, 0], found[:3, 1], found[:3, 2], found[:3, 3] = x, np.cross(z, x), z, origin
    return found


def _rotation(axis: int, degrees: float) -> np.ndarray:
    angle = math.radians(degrees)
    c, s = math.cos(angle), math.sin(angle)
    found = np.eye(4)
    i, j = [(1, 2), (2, 0), (0, 1)][axis]
    found[i, i], found[i, j], found[j, i], found[j, j] = c, -s, s, c
    return found


def pair_motion(kind: str, values: tuple[float, ...] | list[float]) -> np.ndarray:
    """``M(v)``: what a pair's value does between its two frames."""
    if kind == "revolute":
        return _rotation(2, values[0])
    if kind == "prismatic":
        found = np.eye(4)
        found[2, 3] = values[0]
        return found
    if kind == "cylindrical":
        found = _rotation(2, values[0])
        found[2, 3] = values[1]
        return found
    if kind == "spherical":
        return _rotation(0, values[0]) @ _rotation(1, values[1]) @ _rotation(2, values[2])
    raise KinematicsError(f"a {kind} pair has no value")


def _column(matrix: np.ndarray) -> list[float]:
    return [float(value) for value in matrix.T.ravel()]


def _from_column(values: list[float]) -> np.ndarray:
    return np.array(values, dtype=float).reshape(4, 4).T


# --- authoring ---------------------------------------------------------------------------


def _number_values(values: Any) -> tuple[float, ...] | None:
    if isinstance(values, bool):
        return None
    if isinstance(values, (int, float)):
        return (float(values),)
    try:
        found = tuple(values)
    except TypeError:
        return None
    if not found or any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in found):
        return None
    return tuple(float(value) for value in found)


def configuration(assembly: Any, name: str, **values: Any) -> Any:
    """A named configuration of ``assembly``'s mechanism: a value for each joint it names,
    by the joint's label -- a number, or for a joint of several values a sequence of them
    (a cylindrical joint's angle and position, a ball joint's three angles). A joint it
    does not name stays as built. Kept in the document beside the as-built configuration,
    offered by the viewer as a preset and by ``--pose NAME``. A label names the joint in
    each placement of a sub-assembly placed several times. Returns ``assembly``."""
    import build123d as bd

    if not isinstance(assembly, bd.Shape):
        raise TypeError(f"configuration() takes the assembly whose joints it sets, not {type(assembly).__name__}")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("a configuration's name is a non-empty string")
    checked = {}
    for label, value in values.items():
        found = _number_values(value)
        if found is None or not all(math.isfinite(v) for v in found):
            raise TypeError(f"configuration {name!r}: joint {label!r} takes a number, or a sequence of numbers "
                            f"for a joint of several values, not {value!r}")
        checked[label] = found
    if not checked:
        raise ValueError(f"configuration {name!r} sets no joint")
    held = dict(assembly.__dict__.get(CONFIGURATIONS) or {})
    held[name] = checked
    assembly.__dict__[CONFIGURATIONS] = held
    return assembly


def couple(assembly: Any, name: str, *, range: Any = (0, 1), lock: bool = False, **joints: Any) -> Any:  # noqa: A002
    """A coupling of ``assembly``'s mechanism: one control, ``name``, driving several joints by
    ratio, each by the joint's label -- a number, or for a joint of several values a sequence of
    them (a cylindrical joint's turn and travel, a ball joint's three angles). At value ``c``
    each joint stands at its as-built value plus its ratio times ``c``: ``c = 0`` is as built,
    so a coupling never moves the build. ``range`` is the control's ``(lower, upper)``, which
    holds 0. Each joint still poses alone, unless ``lock=True``: then the coupling is its joints'
    only control (a gear train), the viewer shows no slider of theirs, and a pose sets them
    through it alone. Its name poses wherever a joint's does: ``configuration(..., curl=0.5)``, a
    motion's waypoints and sweeps, and ``--pose``. Kept in the document beside the
    configurations; a label names the joint in each placement of a sub-assembly placed several
    times. Returns ``assembly``."""
    import build123d as bd

    if not isinstance(assembly, bd.Shape):
        raise TypeError(f"couple() takes the assembly whose joints it drives, not {type(assembly).__name__}")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("a coupling's name is a non-empty string")
    where = f"coupling {name!r}"
    bounds = _number_values(range)
    if bounds is None or len(bounds) != 2:
        raise TypeError(f"{where}: range is (lower, upper), two numbers, not {range!r}")
    if not all(math.isfinite(value) for value in bounds) or not bounds[0] < bounds[1]:
        raise ValueError(f"{where}: range is (lower, upper), finite, lower below upper, not {range!r}")
    if not bounds[0] <= 0.0 <= bounds[1]:
        raise ValueError(f"{where}: its range holds 0, the as-built pose, not {range!r}")
    if not isinstance(lock, bool):
        raise TypeError(f"{where}: lock is True or False, not {lock!r}")
    ratios = {}
    for label, value in joints.items():
        found = _number_values(value)
        if found is None or not all(math.isfinite(v) for v in found):
            raise TypeError(f"{where}: joint {label!r} takes a ratio, or a sequence of them for a joint of "
                            f"several values, not {value!r}")
        ratios[label] = found
    if not ratios:
        raise ValueError(f"{where} drives no joint")
    held = dict(assembly.__dict__.get(COUPLINGS) or {})
    held[name] = (ratios, (bounds[0], bounds[1]), lock)
    assembly.__dict__[COUPLINGS] = held
    return assembly


def motion(assembly: Any, name: str, waypoints_or_fn: Any = None, *, sweep: Any = None, duration: Any = None,
           ease: str = "cubic", loop: str | None = None) -> Any:
    """A named motion of ``assembly``'s mechanism, one of:

    - waypoints, ``[(t, pose), ...]``: ``t`` in seconds (the first 0, then increasing), ``pose``
      a configuration's name or ``{joint: value}`` (the rest as built); the duration is the
      last waypoint's time;
    - ``sweep={joint: (from, to)}`` with ``duration``: the two waypoints ``(0, from)`` and
      ``(duration, to)``;
    - a function of time, ``t -> {joint: value}``, with ``duration``: stored as its samples
      (see the module docstring); ``ease`` does not apply, the function is its own timing.

    ``ease`` is ``cubic`` (each step eased in and out, resting at every waypoint), ``linear``
    or ``spline`` (smooth through every waypoint); ``loop`` is None, ``loop`` or
    ``pingpong``. Kept in the document beside the configurations, as a definition; a GLB
    export bakes it into a clip of the same name. Returns ``assembly``."""
    import build123d as bd

    if not isinstance(assembly, bd.Shape):
        raise TypeError(f"motion() takes the assembly whose joints it moves, not {type(assembly).__name__}")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("a motion's name is a non-empty string")
    where = f"motion {name!r}"
    if ease not in EASES:
        raise ValueError(f"{where}: ease is one of {', '.join(EASES)}, not {ease!r}")
    if loop not in LOOPS:
        raise ValueError(f"{where}: loop is None, 'loop' or 'pingpong', not {loop!r}")
    if (waypoints_or_fn is None) == (sweep is None):
        raise TypeError(f"{where} takes waypoints, a function of time, or sweep=: one of them")

    def seconds(value: Any) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{where}: duration is a number of seconds above 0, not {value!r}")
        return float(value)

    def joints(values: Any, at: str) -> dict[str, tuple[float, ...]]:
        if not isinstance(values, dict) or not values:
            raise TypeError(f"{where}: {at} is a configuration's name or a dict of joint values, not {values!r}")
        found = {}
        for label, value in values.items():
            numbers = _number_values(value)
            if not isinstance(label, str) or numbers is None or not all(math.isfinite(v) for v in numbers):
                raise TypeError(f"{where}: {at}: joint {label!r} takes a number, or a sequence of numbers for a "
                                f"joint of several values, not {value!r}")
            found[label] = numbers
        return found

    if callable(waypoints_or_fn):
        if duration is None:
            raise TypeError(f"{where}: a function of time takes duration=")
        held: Any = ("function", waypoints_or_fn, seconds(duration))
    elif sweep is not None:
        if duration is None:
            raise TypeError(f"{where}: sweep= takes duration=")
        if not isinstance(sweep, dict) or not sweep:
            raise TypeError(f"{where}: sweep is {{joint: (from, to)}}, not {sweep!r}")
        ends: list[dict[str, Any]] = [{}, {}]
        for label, span in sweep.items():
            if not isinstance(span, (tuple, list)) or len(span) != 2:
                raise TypeError(f"{where}: sweep joint {label!r} takes (from, to), not {span!r}")
            ends[0][label], ends[1][label] = span
        held = ("keys", [(0.0, None, joints(ends[0], "the sweep's start")),
                         (seconds(duration), None, joints(ends[1], "the sweep's end"))])
    else:
        if duration is not None:
            raise TypeError(f"{where}: waypoints give their own times; duration= is a sweep's or a function's")
        try:
            rows = list(waypoints_or_fn)
        except TypeError:
            raise TypeError(f"{where}: waypoints are [(t, pose), ...], not {waypoints_or_fn!r}") from None
        if len(rows) < 2:
            raise ValueError(f"{where} takes at least two waypoints")
        keys = []
        for at, row in enumerate(rows):
            if not isinstance(row, (tuple, list)) or len(row) != 2:
                raise TypeError(f"{where}: waypoint {at} is (t, pose), not {row!r}")
            t, pose = row
            if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t):
                raise TypeError(f"{where}: waypoint {at}'s time is a number of seconds, not {t!r}")
            if at == 0 and t != 0:
                raise ValueError(f"{where}: the first waypoint is at 0 s, not {t!r}")
            if at and t <= keys[-1][0]:
                raise ValueError(f"{where}: waypoint {at} at {t!r} s is not after the one before it")
            if isinstance(pose, str):
                keys.append((float(t), pose, {}))
            else:
                keys.append((float(t), None, joints(pose, f"waypoint {at}")))
        held = ("keys", keys)
    motions = dict(assembly.__dict__.get(MOTIONS) or {})
    motions[name] = (*held, ease, loop)
    assembly.__dict__[MOTIONS] = motions
    return assembly


# --- harvest -----------------------------------------------------------------------------


def _objects(result: Any, root_path: tuple[int, ...]) -> list[tuple[Any, tuple[int, ...]]]:
    """Every node of a result with its occurrence path, as ``document.collect`` reads the
    tree: a node's children are components where its compound holds as many."""
    from OCP.TopAbs import TopAbs_COMPOUND

    from cadgen.engine.document import _children, _identity

    found = []
    stack = [(result, result.wrapped.Located(_identity()), root_path)]
    while stack:
        obj, topods, path = stack.pop()
        found.append((obj, path))
        state = obj.__dict__
        children = state.get("_NodeMixin__children") or ()
        held = _children(topods) if children and int(topods.ShapeType()) == int(TopAbs_COMPOUND) else []
        if children and len(held) == len(children):
            for at in reversed(range(len(children))):
                stack.append((children[at], held[at].Located(_identity()), path + (at,)))
    return found


def _occurrence(tree: Any, path: tuple[int, ...]) -> tuple[Any, list[str]]:
    """An occurrence's placement in the root's frame (composed as the document's reader
    composes it) and its names from the root down."""
    from cadgen.engine.document import _identity

    node, location, names = tree.root, _identity(), [tree.root.name]
    for at in path:
        component = node.components[at]
        location = location.Multiplied(component.location)
        names.append(component.name or component.prototype.name)
        node = component.prototype
    return location, names


def _common(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    count = 0
    for a, b in zip(left, right):
        if a != b:
            break
        count += 1
    return count


def _joint_kind(joint: Any) -> str | None:
    import build123d as bd

    for cls, kind in ((bd.RigidJoint, "rigid"), (bd.RevoluteJoint, "revolute"), (bd.LinearJoint, "prismatic"),
                      (bd.CylindricalJoint, "cylindrical"), (bd.BallJoint, "spherical")):
        if isinstance(joint, cls):
            return kind
    return None


def _local_frames(kind: str, moving: Any, rigid: Any) -> tuple[np.ndarray, np.ndarray, tuple | None]:
    """A pair's two frames in the parts that hold its joints, as build123d's
    ``relative_to`` places one part on the other, and the value the joint holds."""
    import build123d as bd

    other = _numpy(rigid.relative_location)
    if kind == "revolute":
        held = getattr(moving, "_angle", None)
        return _numpy(moving.relative_axis.location), other, None if held is None else (float(held),)
    if kind == "prismatic":
        frame = _numpy(moving.relative_axis.location)
        turn = frame.copy()
        turn[:3, 3] = 0.0
        held = moving.position
        return frame, other @ turn, None if held is None else (float(held),)
    if kind == "cylindrical":
        axis = moving.relative_axis
        frame = _numpy(bd.Location(bd.Plane(origin=axis.position, x_dir=moving.angle_reference, z_dir=axis.direction)))
        held = None if moving.angle is None or moving.position is None else (float(moving.angle), float(moving.position))
        return frame, other, held
    # spherical: the joint's reference plane turns with the angles, on the rigid side.
    return _numpy(moving.relative_location), other @ np.linalg.inv(_numpy(moving.angle_reference.location)), None


def _limits(kind: str, joint: Any) -> tuple[tuple[float | None, float | None], ...]:
    def finite(pair: Any) -> tuple[float | None, float | None]:
        lower, upper = (float(value) for value in pair)
        return (lower if math.isfinite(lower) else None, upper if math.isfinite(upper) else None)

    if kind == "revolute":
        return (finite(joint.angular_range),)
    if kind == "prismatic":
        return (finite(joint.linear_range),)
    if kind == "cylindrical":
        return (finite(joint.angular_range), finite(joint.linear_range))
    return tuple(finite(pair) for pair in joint.angular_range)


def _within(value: float, limits: tuple[float | None, float | None], slack: float = 1e-9) -> bool:
    lower, upper = limits
    return (lower is None or value >= lower - slack) and (upper is None or value <= upper + slack)


def _angle_in(value: float, hint: float | None, limits: tuple[float | None, float | None]) -> float | None:
    """The angle ``value`` stands for (any turn of it): the joint's own when it is one of
    them, else one within the limits, nearest zero."""
    if hint is not None and abs((hint - value + 180.0) % 360.0 - 180.0) < 1e-6:
        return hint
    candidates = sorted((value + 360.0 * turn for turn in range(-3, 4)), key=abs)
    for candidate in candidates:
        if _within(candidate, limits):
            return candidate
    return None


def _extract(pair: Pair, relative: np.ndarray, hint: tuple | None, scale: float) -> tuple[float, ...] | None:
    """The value of a pair whose link 2 stands at ``relative`` from link 1's frame, item
    to item: None where no value of the pair puts it there, or none within its limits."""
    kind, limits = pair.kind, pair.limits
    rotation = relative[:3, :3]
    if kind in ("revolute", "cylindrical"):
        turned = _angle_in(math.degrees(math.atan2(rotation[1, 0], rotation[0, 0])), hint[0] if hint else None,
                           limits[0])
        if turned is None:
            return None
        found = (turned,) if kind == "revolute" else (turned, float(relative[2, 3]))
    elif kind == "prismatic":
        found = (float(relative[2, 3]),)
    else:
        pitch = math.degrees(math.asin(max(-1.0, min(1.0, rotation[0, 2]))))
        roll = math.degrees(math.atan2(-rotation[1, 2], rotation[2, 2]))
        yaw = math.degrees(math.atan2(-rotation[0, 1], rotation[0, 0]))
        found = None
        for branch in ((roll, pitch, yaw), (roll + 180.0, 180.0 - pitch, yaw + 180.0)):
            angles = [_angle_in(angle, None, limit) for angle, limit in zip(branch, limits)]
            if all(angle is not None for angle in angles):
                found = tuple(angles)
                break
        if found is None:
            return None
    if not all(_within(value, limit, 1e-6 * max(1.0, scale)) for value, limit in zip(found, limits)):
        return None
    if np.abs(pair_motion(kind, found) - relative).max() > 1e-6 * max(1.0, scale):
        return None
    return found


def _by_label(jointed: list, used: set[int], tree: Any, warnings: list[str]) -> list[tuple]:
    """The pairings of joints no ``connect_to`` joined, by their labels: a moving joint and a
    ``RigidJoint`` of the same label on another part are its pair (the moving joint's part stays,
    the rigid one's is placed on it), and two rigid joints of one label fasten their parts. Of
    several candidates, the nearest in the tree is taken (what a placement of a sub-assembly
    holds pairs within it); a moving joint with two equally near is no pair, and a warning,
    rigid joints with two equally near are no pair. A joint that matches nothing is no pair."""
    rows: dict[str, list[tuple[tuple[int, ...], Any]]] = {}
    for path, joint in jointed:
        if id(joint) not in used and _joint_kind(joint) is not None:
            rows.setdefault(joint.label, []).append((path, joint))
    found = []

    def nearest(path: tuple[int, ...], label: str, candidates: list, strict: bool = True) -> Any:
        best = max(_common(path, other) for other, _joint in candidates)
        ties = [row for row in candidates if _common(path, row[0]) == best]
        if len(ties) > 1 and not strict:
            return None
        if len(ties) > 1:
            names = " and ".join("/".join(_occurrence(tree, other)[1]) for other, _joint in ties[:2])
            warnings.append(f"joint {label!r} on {'/'.join(_occurrence(tree, path)[1])} matches the rigid joints "
                            f"{label!r} on {names} alike, so it pairs with neither: give each pair a label of its "
                            "own, or connect it with connect_to")
            return None
        return ties[0]

    for label, held in rows.items():
        rigid = [row for row in held if _joint_kind(row[1]) == "rigid"]
        for path, joint in [row for row in held if _joint_kind(row[1]) != "rigid"]:
            candidates = [row for row in rigid if row[0] != path]
            other = nearest(path, label, candidates) if candidates else None
            if other is not None:
                rigid.remove(other)
                found.append((path, joint, other[0], other[1]))
        # Rigid joints alone fasten only where one is nearest: a datum a part carries in each of
        # its placements (three pegs' `base`) is no fastening.
        mates = {}
        for at, (path, _joint) in enumerate(rigid):
            candidates = [row for row in rigid if row[0] != path]
            other = nearest(path, label, candidates, strict=False) if candidates else None
            mates[at] = None if other is None else next(index for index, row in enumerate(rigid) if row is other)
        for at, other in mates.items():
            if other is not None and other > at and mates.get(other) == at:  # each the other's one nearest
                found.append((*rigid[at], *rigid[other]))
    return found


def harvest(result: Any, tree: Any, root_path: tuple[int, ...]) -> Mechanism | None:
    """The mechanism of a model's result (see the module docstring), or None for one with no
    connected joint. ``root_path`` is the result's own occurrence in ``tree`` (``()``, or
    ``(0,)`` where the document holds it in a component of its own)."""
    objects = _objects(result, root_path)
    holders: dict[int, list[tuple[int, ...]]] = {}
    jointed: list[tuple[tuple[int, ...], Any]] = []
    configured: list[tuple[tuple[int, ...], Any, dict]] = []
    motioned: list[tuple[tuple[int, ...], Any, dict]] = []
    coupled: list[tuple[tuple[int, ...], Any, dict]] = []
    for obj, path in objects:
        joints = obj.__dict__.get("joints") or {}
        for joint in list(joints.values()):
            holders.setdefault(id(joint), []).append(path)
            jointed.append((path, joint))
        held = obj.__dict__.get(CONFIGURATIONS)
        if held:
            configured.append((path, obj, held))
        held = obj.__dict__.get(MOTIONS)
        if held:
            motioned.append((path, obj, held))
        held = obj.__dict__.get(COUPLINGS)
        if held:
            coupled.append((path, obj, held))

    # Every connect_to: (the joint called and its part, which stays; the other and its part, which moves).
    seen: set = set()
    pairings = []
    for path, joint in jointed:
        other = getattr(joint, "connected_to", None)
        candidates = holders.get(id(other)) if other is not None else None
        if not candidates:
            continue
        moved = max(candidates, key=lambda candidate: _common(path, candidate))
        key = frozenset([(path, id(joint)), (moved, id(other))])
        if key in seen:
            continue
        seen.add(key)
        pairings.append((path, joint, moved, other))
    warnings: list[str] = []
    pairings.extend(_by_label(jointed, {id(joint) for row in pairings for joint in (row[1], row[3])}, tree, warnings))

    def name_of(path: tuple[int, ...]) -> str:
        return "/".join(_occurrence(tree, path)[1])

    def labels_below(path: tuple[int, ...]) -> set[str]:
        return {joint.label for holder, joint in jointed if holder[:len(path)] == path}

    def controls_below(path: tuple[int, ...]) -> set[str]:
        return {name for holder, _obj, held in coupled if holder[:len(path)] == path for name in held}

    def unknown(key: str, labels: set[str], controls: set[str] = frozenset()) -> bool:
        return not any(key == known or key.endswith(f":{known}") for known in (*labels, *controls))

    def known(labels: set[str], controls: set[str]) -> str:
        found = f"its joints are {', '.join(sorted(labels))}" if labels else "it holds no joint"
        return found + (f"; its couplings are {', '.join(sorted(controls))}" if controls else "")

    # A configuration, a motion or a coupling naming a joint no part of its assembly holds means
    # nothing, nor does a coupling named as a joint is (a pose could not tell them apart): the
    # harvest's errors. Every other inconsistency is a warning, and the document is written.
    for path, _obj, held in configured:
        labels, controls = labels_below(path), controls_below(path)
        for name, values in held.items():
            for key in values:
                if unknown(key, labels, controls):
                    raise KinematicsError(f"configuration {name!r} of {name_of(path)} names no joint {key!r}; "
                                          + known(labels, controls))
    for path, _obj, held in motioned:
        labels, controls = labels_below(path), controls_below(path)
        for name, spec in held.items():
            for key in (label for _t, _name, values in (spec[1] if spec[0] == "keys" else ()) for label in values):
                if unknown(key, labels, controls):
                    raise KinematicsError(f"motion {name!r} of {name_of(path)} names no joint {key!r}; "
                                          + known(labels, controls))
    for path, _obj, held in coupled:
        labels = labels_below(path)
        for name, (ratios, _range, _lock) in held.items():
            if not unknown(name, labels):
                raise KinematicsError(f"coupling {name!r} of {name_of(path)} has a joint's name, which a pose could "
                                      "not tell from it: give it a name of its own")
            for key in ratios:
                if unknown(key, labels):
                    raise KinematicsError(f"coupling {name!r} of {name_of(path)} names no joint {key!r}; "
                                          + known(labels, set()))
    if not pairings:
        if configured or motioned or coupled:
            warnings.append("the configurations, couplings or motions name joints, and no joint is paired with "
                            "another")
        return Mechanism(tree.root.name, [], 0, [], {}, {}, warnings) if warnings else None

    parent: dict[tuple[int, ...], tuple[int, ...]] = {}

    def find(path: tuple[int, ...]) -> tuple[int, ...]:
        parent.setdefault(path, path)
        while parent[path] != path:
            parent[path] = parent[parent[path]]
            path = parent[path]
        return path

    moving_pairs = []
    for stays, joint, moves, other in pairings:
        kinds = (_joint_kind(joint), _joint_kind(other))
        find(stays), find(moves)
        if kinds == ("rigid", "rigid"):
            parent[find(moves)] = find(stays)
            continue
        if None in kinds:
            continue
        if "rigid" not in kinds:
            warnings.append(
                f"joint {joint.label!r} ({type(joint).__name__}) on {name_of(stays)} is connected to {other.label!r} "
                f"({type(other).__name__}) on {name_of(moves)}: a pin in a slot, which no AP242 lower pair is, so it "
                "is no pair. Connect each moving joint to a RigidJoint")
            continue
        if kinds[0] == "rigid":
            moving, held_by, rigid, rigid_by = other, moves, joint, stays
        else:
            moving, held_by, rigid, rigid_by = joint, stays, other, moves
        moving_pairs.append((moving, held_by, rigid, rigid_by, moves))
    if not moving_pairs:
        if configured or motioned or coupled:
            warnings.append("the configurations, couplings or motions name joints, and no moving joint is paired")
        return Mechanism(tree.root.name, [], 0, [], {}, {}, warnings) if warnings else None

    classes: dict[tuple[int, ...], list[tuple[int, ...]]] = {}
    for path in sorted(parent):
        classes.setdefault(find(path), []).append(path)
    # A pair moves the class its moving side is in; one inside a single rigid body moves nothing.
    moved_classes = {find(moves) for _moving, held_by, _rigid, rigid_by, moves in moving_pairs
                     if find(held_by) != find(rigid_by)}
    ground_members = sorted(path for root, members in classes.items() if root not in moved_classes for path in members)
    links = [Link("", ground_members)]
    link_of: dict[tuple[int, ...], int] = {path: 0 for path in ground_members}
    for root, members in sorted(classes.items(), key=lambda item: item[1][0]):
        if root in moved_classes:
            for path in members:
                link_of[path] = len(links)
            links.append(Link("", sorted(members)))
    for link in links:
        link.name = _occurrence(tree, link.members[0])[1][-1] if link.members else tree.root.name
    worlds = {path: _numpy(_occurrence(tree, path)[0]) for path in parent}
    frames = [worlds[link.members[0]] if link.members else np.eye(4) for link in links]
    scale = max([1.0] + [float(np.abs(matrix[:3, 3]).max()) for matrix in worlds.values()])

    # The pairs, named by their moving joints' labels; a label two share takes its part's path.
    counts: dict[str, int] = {}
    for moving, *_rest in moving_pairs:
        counts[moving.label] = counts.get(moving.label, 0) + 1
    pairs: list[Pair] = []
    as_built: dict[str, tuple[float, ...]] = {}
    joint_labels: list[tuple[str, tuple[int, ...]]] = []
    for moving, held_by, rigid, rigid_by, _moves in moving_pairs:
        kind = _joint_kind(moving)
        assert kind is not None and kind != "rigid"
        first, second = link_of[held_by], link_of[rigid_by]
        label = moving.label if counts[moving.label] == 1 else f"{name_of(held_by)}:{moving.label}"
        while any(pair.label == label for pair in pairs):
            label += "'"
        item_1, item_2, held = _local_frames(kind, moving, rigid)
        ax2_1 = _ax2(np.linalg.inv(frames[first]) @ worlds[held_by] @ item_1)
        ax2_2 = _ax2(np.linalg.inv(frames[second]) @ worlds[rigid_by] @ item_2)
        pair = Pair(label, kind, (first, second), (ax2_1, ax2_2), _limits(kind, moving))
        if first == second:
            together = len(links[first].members)
            pair.inconsistent = (f"it joins {name_of(held_by)} to {name_of(rigid_by)}, which rigid joints make one "
                                 f"link{f' with {together - 2} other parts' if together > 2 else ''}"
                                 f"{' (the unmoved ground)' if first == 0 else ''}: the pair cannot move")
        else:
            relative = (np.linalg.inv(frame_matrix(ax2_1)) @ np.linalg.inv(frames[first]) @ frames[second]
                        @ frame_matrix(ax2_2))
            value = _extract(pair, relative, held, scale)
            if value is None:
                pair.inconsistent = (
                    f"{name_of(rigid_by)} is not where it can put it"
                    f"{'' if held is None else f' (it was connected at {_shown(held)})'}: its rigid joint's frame and "
                    f"the {kind} joint's are not one {kind} motion apart within its range (a part moved after "
                    "connect_to, or connected after the compound holding it was made, or a rigid joint off the "
                    "joint's axis)")
            else:
                as_built[label] = value
        if pair.inconsistent:
            warnings.append(f"joint {label!r} is kept with no value and cannot be posed: {pair.inconsistent}")
        pairs.append(pair)
        joint_labels.append((moving.label, held_by))

    couplings, controls = _couplings(coupled, pairs, joint_labels, name_of, warnings)
    found = Mechanism(tree.root.name, links, 0, pairs, as_built, {}, warnings, couplings=couplings)
    found.configurations = _configurations(configured, found, joint_labels, controls, name_of)
    if motioned:
        functions = any(spec[0] == "function" for _path, _obj, held in motioned for spec in held.values())
        found.motions = _motions(motioned, found, joint_labels, controls, name_of, _size(result) if functions else 1.0)
    return found


class _Unfit(KinematicsError):
    """A motion value its pair cannot take: the motion is left out, and warned."""


def _matching(path: tuple[int, ...], key: str, pairs: list[Pair], joint_labels: list) -> list[Pair]:
    """The pairs a joint label set on the assembly at ``path`` names: by the pair's label or
    its moving joint's, among the joints below the assembly."""
    return [pair for pair, (raw, holder) in zip(pairs, joint_labels)
            if holder[:len(path)] == path and key in (pair.label, raw)]


def _couplings(coupled: list, pairs: list[Pair], joint_labels: list, name_of: Callable,
               warnings: list[str]) -> tuple[dict[str, Coupling], list[tuple[tuple[int, ...], str, str]]]:
    """Each coupling, by name, its joints made pairs: a joint label names the pair of every joint
    below its assembly so labelled (each placement's, for a sub-assembly placed several times).
    A name two couplings share takes the path of the assembly holding each (``hand/index:curl``),
    as a label two pairs share does. A joint in no pair, or given as many ratios as its pair has
    no values, is left out of the coupling, and warned; a coupling left with no joint is left out.
    A coupling named as a pair is, and a joint a locked coupling drives that another coupling
    drives too, fail the harvest. Returns the couplings and, for each, its assembly's path, its
    own name and the name it is kept by (what a configuration or a motion may call it)."""
    counts: dict[str, int] = {}
    for _path, _obj, held in coupled:
        for name in held:
            counts[name] = counts.get(name, 0) + 1
    labels = {pair.label for pair in pairs}
    found: dict[str, Coupling] = {}
    controls: list[tuple[tuple[int, ...], str, str]] = []
    for path, _obj, held in coupled:
        for name, (given, bounds, lock) in held.items():
            kept = name if counts[name] == 1 else f"{name_of(path)}:{name}"
            where = f"coupling {name!r} of {name_of(path)}"
            if kept in labels:
                raise KinematicsError(f"{where} is named {kept!r}, as a joint is, which a pose could not tell from it: "
                                      "give it a name of its own")
            ratios: dict[str, tuple[float, ...]] = {}
            for key, value in given.items():
                matches = _matching(path, key, pairs, joint_labels)
                if not matches:
                    warnings.append(f"{where} drives joint {key!r}, which is in no pair: the joint is left out of it")
                for pair in matches:
                    if len(value) != pair.dofs:
                        need = "a ratio" if pair.dofs == 1 else f"{pair.dofs} ratios ({', '.join(_dof_names(pair.kind))})"
                        warnings.append(f"{where}: joint {pair.label!r} ({pair.kind}) takes {need}, not {_shown(value)}: "
                                        "the joint is left out of it")
                        continue
                    ratios[pair.label] = tuple(float(ratio) for ratio in value)
            if not ratios:
                warnings.append(f"{where} drives no pair: it is left out")
                continue
            found[kept] = Coupling(kept, ratios, (float(bounds[0]), float(bounds[1])), bool(lock))
            controls.append((path, name, kept))
    drivers: dict[str, list[str]] = {}
    for kept in sorted(found):
        for label in found[kept].ratios:
            drivers.setdefault(label, []).append(kept)
    for label, names in drivers.items():
        locked = [name for name in names if found[name].lock]
        if locked and len(names) > 1:
            other = next(name for name in names if name != locked[0])
            raise KinematicsError(f"joint {label!r} is driven by the locked coupling {locked[0]!r} and by {other!r}: "
                                  "a locked coupling is its joints' only control")
    return {name: found[name] for name in sorted(found)}, controls


def _coupling_json(coupling: Coupling, pairs: list[Pair]) -> dict[str, Any]:
    """A coupling as the structure's ``kinematics.couplings`` holds it: its range, whether it is
    locked, and its terms, ``[pair, value index, ratio]`` in the pairs' order."""
    return {"range": [float(coupling.range[0]), float(coupling.range[1])], "lock": bool(coupling.lock),
            "terms": [[pair.label, index, float(ratio)] for pair in pairs if pair.label in coupling.ratios
                      for index, ratio in enumerate(coupling.ratios[pair.label])]}


def _locked(couplings: dict[str, Any]) -> dict[str, str]:
    """Each pair a locked coupling drives (``kinematics.couplings``): the coupling's name."""
    return {label: name for name, coupling in couplings.items() if coupling.get("lock")
            for label, _index, _ratio in coupling["terms"]}


def _coupled(given: dict[str, Any], couplings: dict[str, Any], as_built: dict[str, tuple[float, ...]],
             checked: Callable[[str, tuple[float, ...], str], tuple[float, ...]], where: str) -> dict[str, tuple[float, ...]]:
    """The pair values couplings put their joints at: ``given`` is each coupling's value by name,
    ``couplings`` the structure's. Each joint stands at its as-built values plus each coupling's
    ratio times its value (couplings sharing a joint add). A value that is no number or is outside
    its coupling's range, and one putting a joint past its limits (``checked``), are refused by
    name."""
    moved: dict[str, list[float]] = {}
    by: dict[str, dict[str, None]] = {}
    for name, value in given.items():
        coupling = couplings[name]
        found = _number_values(value)
        if found is None or len(found) != 1 or not math.isfinite(found[0]):
            raise KinematicsError(f"{where}: coupling {name!r} takes a number, not {value!r}")
        lower, upper = coupling["range"]
        if not _within(found[0], (lower, upper)):
            raise KinematicsError(f"{where}: coupling {name!r} at {found[0]:g} is outside its range "
                                  f"[{lower:g}, {upper:g}]")
        for label, index, ratio in coupling["terms"]:
            if label not in as_built:
                raise KinematicsError(f"{where}: coupling {name!r} moves joint {label!r}, which is kept with no value")
            row = moved.setdefault(label, list(as_built[label]))
            row[index] += ratio * found[0]
            by.setdefault(label, {})[f"{name!r} at {found[0]:g}"] = None
    return {label: checked(label, tuple(row), f"{where}: coupling{'s' if len(by[label]) > 1 else ''} "
                                              f"{', '.join(by[label])}")
            for label, row in moved.items()}


def _converted(path: tuple[int, ...], values: dict[str, Any], mechanism: Mechanism, joint_labels: list,
               controls: list, where: str) -> tuple[dict[str, tuple[float, ...]], list[str]]:
    """``values`` -- joints by label and couplings by name, as the assembly at ``path`` sets them
    -- as the pair values they put the pairs at, and what could not be taken, each said by name:
    a joint in no pair, a value its pair cannot take, a locked coupling's joint set alone, a
    coupling set with one of its joints, a coupling's value outside its range or putting a joint
    past its limits. What a configuration and a motion name is made pair values here, at
    document time."""
    pairs = mechanism.pairs
    by_label = {pair.label: pair for pair in pairs}
    couplings = {name: _coupling_json(coupling, pairs) for name, coupling in mechanism.couplings.items()}
    locked = _locked(couplings)
    found: dict[str, tuple[float, ...]] = {}
    problems: list[str] = []
    driving: dict[str, Any] = {}
    for key, value in values.items():
        named = [kept for holder, own, kept in controls if holder[:len(path)] == path and key in (own, kept)]
        if named:
            driving.update(dict.fromkeys(named, value))
            continue
        matches = _matching(path, key, pairs, joint_labels)
        if not matches:
            problems.append(f"{where} sets joint {key!r}, which is in no pair")
        for pair in matches:
            if pair.label in locked:
                problems.append(f"{where}: joint {pair.label!r} moves only with the locked coupling "
                                f"{locked[pair.label]!r}; set {locked[pair.label]!r}")
                continue
            try:
                found[pair.label] = _checked(pair, value, where)
            except KinematicsError as error:
                problems.append(str(error))
    as_built = mechanism.as_built
    checked = lambda label, row, at: _checked(by_label[label], row, at)  # noqa: E731
    for name in list(driving):
        clash = [label for label in mechanism.couplings[name].ratios if label in found]
        problem = f"{where} sets coupling {name!r} and its joint {clash[0]!r}: set one of them" if clash else None
        if problem is None:
            try:
                _coupled({name: driving[name]}, couplings, as_built, checked, where)
            except KinematicsError as error:
                problem = str(error)
        if problem is not None:
            problems.append(problem)
            del driving[name]
    if driving:
        try:
            found.update(_coupled(driving, couplings, as_built, checked, where))
        except KinematicsError as error:  # each alone within the limits, together past them
            problems.append(str(error))
    return {pair.label: found[pair.label] for pair in pairs if pair.label in found}, problems


def _size(result: Any) -> float:
    """A model's size: its bounding box's diagonal, from its geometry (never a mesh), at least 1 mm."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    BRepBndLib.Add_s(result.wrapped, box, False)
    if box.IsVoid():
        return 1.0
    low, high = box.CornerMin(), box.CornerMax()
    return max(1.0, math.dist((low.X(), low.Y(), low.Z()), (high.X(), high.Y(), high.Z())))


def _motions(motioned: list, mechanism: Mechanism, joint_labels: list, controls: list, name_of: Callable,
             size: float) -> dict[str, Motion]:
    """Each motion as the document keeps it, by name: its keys in pair labels (a function's
    sampled, a coupling's value made its joints'), a shallower assembly's motion over a deeper
    one's of the same name. A motion naming a configuration the mechanism has not, or a joint
    its assembly has not, is the harvest's error; one setting a value its pair cannot take, or
    moving a pair kept with no value or closing a loop, is left out, and warned."""
    pairs, warnings = mechanism.pairs, mechanism.warnings
    _reached, loop = _tree(mechanism)
    blocked = {pair.label: f"it is kept with no value ({pair.inconsistent})" for pair in pairs if pair.inconsistent}
    blocked.update({label: f"pairs {', '.join(loop)} close a loop" for label in loop or ()})
    tolerances = {pair.label: [math.degrees(ANGULAR_TOLERANCE) if unit == "deg" else LINEAR_TOLERANCE * size
                               for unit, *_names in KINDS[pair.kind][1]] for pair in pairs}
    found: dict[str, Motion] = {}
    for path, _obj, held in sorted(motioned, key=lambda item: -len(item[0])):
        for name, spec in held.items():
            where = f"motion {name!r} of {name_of(path)}"

            def convert(values: dict[str, Any], at: str) -> dict[str, tuple[float, ...]]:
                converted, problems = _converted(path, values, mechanism, joint_labels, controls, f"{where}: {at}")
                if problems:
                    raise _Unfit(problems[0])
                return converted

            try:
                if spec[0] == "function":
                    _kind, function, duration, _ease, loop_mode = spec
                    keys = _sampled(function, duration, convert, tolerances, where, path, joint_labels, controls)
                    ease = "spline"
                else:
                    _kind, rows, ease, loop_mode = spec
                    keys = []
                    for t, configuration, values in rows:
                        if configuration is not None:
                            if configuration not in mechanism.configurations:
                                known = ", ".join(sorted(mechanism.configurations)) or "none"
                                raise KinematicsError(f"{where}: the waypoint at {t:g} s names no configuration "
                                                      f"{configuration!r}; the configurations: {known}")
                            keys.append((t, configuration, {}))
                        else:
                            keys.append((t, None, convert(values, f"the waypoint at {t:g} s")))
                motion = Motion(name, keys, ease, loop_mode)
                stopped = sorted({label for label in motion.labels() | {label for _t, configuration, _v in keys
                                                                        if configuration is not None
                                                                        for label in mechanism.configurations[configuration]}
                                  if label in blocked})
                if stopped:
                    raise _Unfit(f"{where} moves joint {stopped[0]!r}, which cannot be posed: {blocked[stopped[0]]}")
            except _Unfit as error:
                warnings.append(f"{error}: the motion is left out")
                found.pop(name, None)
                continue
            found[name] = motion
    return {name: found[name] for name in sorted(found)}


def _flat(values: dict[str, tuple[float, ...]], labels: list[str]) -> np.ndarray:
    return np.array([value for label in labels for value in values[label]], dtype=float)


def _sampled(function: Callable, duration: float, convert: Callable, tolerances: dict[str, list[float]],
             where: str, path: tuple[int, ...], joint_labels: list, controls: list) -> list:
    """A function of time's samples: eight equal steps over its duration, each step halved
    where the samples' spline strays from the function, at a quarter, half or three quarters
    of it, by more than the tolerance (``ANGULAR_TOLERANCE``, ``LINEAR_TOLERANCE``), until
    none does. A function of the code: the same function samples at the same times."""
    held: dict[float, dict[str, tuple[float, ...]]] = {}
    names = {name for holder, own, kept in controls if holder[:len(path)] == path for name in (own, kept)}

    def at(t: float) -> dict[str, tuple[float, ...]]:
        found = held.get(t)
        if found is None:
            try:
                given = function(t)
            except Exception as error:  # noqa: BLE001 - the model's function, said by name
                raise KinematicsError(f"{where}: the function raised at {t:g} s: {error!r}") from None
            if not isinstance(given, dict) or not given:
                raise KinematicsError(f"{where}: the function returns {{joint: value}}, not {given!r} (at {t:g} s)")
            for key in given:
                if key not in names and not any(key in (raw,) or key.endswith(f":{raw}") for raw, holder in joint_labels
                                                if holder[:len(path)] == path):
                    labels = sorted({raw for raw, holder in joint_labels if holder[:len(path)] == path})
                    raise KinematicsError(f"{where} names no joint {key!r}; its joints are {', '.join(labels)}"
                                          + (f"; its couplings are {', '.join(sorted(names))}" if names else ""))
            found = held[t] = convert(given, f"the function at {t:g} s")
        return found

    first = at(0.0)
    labels = sorted(first)
    tolerance = np.array([value for label in labels for value in tolerances[label]], dtype=float)
    times = [duration * step / 8 for step in range(9)]
    while True:
        rows = []
        for t in times:
            values = at(t)
            if sorted(values) != labels:
                raise KinematicsError(f"{where}: the function sets {', '.join(sorted(values))} at {t:g} s and "
                                      f"{', '.join(labels)} at 0 s: it sets the same joints at every time")
            rows.append(_flat(values, labels))
        grid, table = np.array(times), np.array(rows)
        split = []
        for a, b in zip(times, times[1:]):
            for fraction in (0.25, 0.5, 0.75):
                t = a + (b - a) * fraction
                if np.any(np.abs(_interpolated(grid, table, "spline", t) - _flat(at(t), labels)) > tolerance):
                    split.append((a + b) / 2)
                    break
        if not split:
            return [(t, None, at(t)) for t in times]
        times = sorted(set(times) | set(split))
        if len(times) > MOST_KEYS:
            raise KinematicsError(f"{where}: the function does not settle within the tolerance in {MOST_KEYS} samples")


def _tangent(times: np.ndarray, values: np.ndarray, at: int) -> np.ndarray:
    last = len(times) - 1
    if at == 0:
        return (values[1] - values[0]) / (times[1] - times[0])
    if at == last:
        return (values[last] - values[last - 1]) / (times[last] - times[last - 1])
    return (values[at + 1] - values[at - 1]) / (times[at + 1] - times[at - 1])


def _interpolated(times: np.ndarray, values: np.ndarray, ease: str, t: float) -> np.ndarray:
    """Values at ``t`` between keys: ``linear``, ``cubic`` (each step eased in and out) or
    ``spline`` (a cubic Hermite through the keys, each key's slope its neighbours' secant)."""
    if t <= times[0]:
        return values[0].copy()
    if t >= times[-1]:
        return values[-1].copy()
    at = min(int(np.searchsorted(times, t, side="right")) - 1, len(times) - 2)
    span = times[at + 1] - times[at]
    s = (t - times[at]) / span
    if ease == "linear":
        return values[at] + s * (values[at + 1] - values[at])
    if ease == "cubic":
        return values[at] + s * s * (3.0 - 2.0 * s) * (values[at + 1] - values[at])
    first, second = _tangent(times, values, at), _tangent(times, values, at + 1)
    return ((2 * s ** 3 - 3 * s ** 2 + 1) * values[at] + (s ** 3 - 2 * s ** 2 + s) * span * first
            + (-2 * s ** 3 + 3 * s ** 2) * values[at + 1] + (s ** 3 - s ** 2) * span * second)


def _shown(values: tuple[float, ...] | list[float]) -> str:
    text = [f"{value:g}" for value in values]
    return text[0] if len(text) == 1 else "(" + ", ".join(text) + ")"


def _configurations(configured: list, mechanism: Mechanism, joint_labels: list, controls: list,
                    name_of: Callable) -> dict:
    """Each named configuration as pair values (a coupling's value made its joints'), every
    assembly's that names it merged, a deeper one's values first and a shallower one's over
    them. What a value cannot be taken for (``_converted``) is left out, and warned."""
    merged: dict[str, dict[str, tuple[float, ...]]] = {}
    for path, _obj, held in sorted(configured, key=lambda item: -len(item[0])):
        for name, values in held.items():
            found, problems = _converted(path, values, mechanism, joint_labels, controls,
                                         f"configuration {name!r} of {name_of(path)}")
            mechanism.warnings.extend(f"{problem}: the value is left out" for problem in problems)
            merged.setdefault(name, {}).update(found)
    return {name: merged[name] for name in sorted(merged)}


def _checked(pair: Pair, value: Any, where: str) -> tuple[float, ...]:
    """A value for ``pair``: as many numbers as it has values, each within its limits."""
    found = _number_values(value)
    if found is None or len(found) != pair.dofs or not all(math.isfinite(v) for v in found):
        need = "a number" if pair.dofs == 1 else f"{pair.dofs} numbers ({', '.join(_dof_names(pair.kind))})"
        raise KinematicsError(f"{where}: joint {pair.label!r} ({pair.kind}) takes {need}, not {value!r}")
    for value_at, limits, (unit, *_names) in zip(found, pair.limits, KINDS[pair.kind][1]):
        if not _within(value_at, limits):
            lower = "-inf" if limits[0] is None else f"{limits[0]:g}"
            upper = "inf" if limits[1] is None else f"{limits[1]:g}"
            raise KinematicsError(f"{where}: joint {pair.label!r} at {value_at:g} {unit} is outside its range "
                                  f"[{lower}, {upper}] {unit}")
    return found


def _dof_names(kind: str) -> tuple[str, ...]:
    return {"cylindrical": ("angle", "position"), "spherical": ("x", "y", "z angles")}.get(kind, ("value",))


# --- the document --------------------------------------------------------------------------


def _text(value: str) -> Any:
    from OCP.TCollection import TCollection_ExtendedString

    return TCollection_ExtendedString(value, True)


def _label(parent: Any, tag: int, name: str, entity: str | None = None) -> Any:
    from OCP.TDataStd import TDataStd_NamedData

    from cadgen.engine.document import _name

    label = parent.FindChild(tag, True)
    _name(label, name)
    if entity is not None:
        TDataStd_NamedData.Set_s(label).SetString(_text("entity"), _text(entity))
    return label


def _reals(values: Any) -> Any:
    from OCP.TColStd import TColStd_HArray1OfReal

    array = TColStd_HArray1OfReal(1, len(values))
    for at, value in enumerate(values, 1):
        array.SetValue(at, float(value))
    return array


def _state(label: Any, pair_labels: list, values: dict[str, tuple[float, ...]], pairs: list[Pair]) -> None:
    from OCP.TDataStd import TDataStd_IntegerArray, TDataStd_RealArray, TDataStd_ReferenceList

    rows = [(at, dof, value) for at, pair in enumerate(pairs) if pair.label in values
            for dof, value in enumerate(values[pair.label])]
    if not rows:
        return
    references = TDataStd_ReferenceList.Set_s(label)
    reals = TDataStd_RealArray.Set_s(label, 1, len(rows))
    dofs = TDataStd_IntegerArray.Set_s(label, 1, len(rows))
    for row, (at, dof, value) in enumerate(rows, 1):
        references.Append(pair_labels[at])
        reals.SetValue(row, float(value))
        dofs.SetValue(row, dof)


def write(doc: Any, mechanism: Mechanism, occurrence: Callable[[tuple[int, ...]], list]) -> None:
    """The mechanism's subtree in ``doc`` (see the module docstring). ``occurrence(path)``
    is an occurrence's labels: the root's, then each component's down to it."""
    from OCP.Standard import Standard_GUID
    from OCP.TCollection import TCollection_AsciiString
    from OCP.TColStd import TColStd_ListOfAsciiString
    from OCP.TDataStd import TDataStd_NamedData, TDataStd_ReferenceList, TDataStd_UAttribute
    from OCP.XCAFDoc import XCAFDoc_AssemblyItemId, XCAFDoc_AssemblyItemRef

    from cadgen.engine.document import _entry

    root = _label(doc.Main(), TAG, "Kinematics")
    TDataStd_UAttribute.Set_s(root, Standard_GUID(GUID))
    data = TDataStd_NamedData.Set_s(root)
    data.SetString(_text("schema"), _text(SCHEMA))
    data.SetString(_text("angle_unit"), _text("deg"))
    data.SetString(_text("length_unit"), _text("mm"))
    top = _label(root, 1, mechanism.name, "MECHANISM_REPRESENTATION")
    links_label = _label(top, 1, "links")
    pairs_label = _label(top, 2, "pairs")
    link_labels = []
    for at, link in enumerate(mechanism.links, 1):
        label = _label(links_label, at, link.name, "KINEMATIC_LINK")
        for member_at, path in enumerate(link.members, 1):
            entries = TColStd_ListOfAsciiString()
            for step in occurrence(path):
                entries.Append(TCollection_AsciiString(_entry(step)))
            XCAFDoc_AssemblyItemRef.Set_s(label.FindChild(member_at, True), XCAFDoc_AssemblyItemId(entries))
        link_labels.append(label)
    # MECHANISM_REPRESENTATION's base: the link that does not move.
    if link_labels:
        TDataStd_ReferenceList.Set_s(top).Append(link_labels[mechanism.ground])
    if mechanism.warnings:
        # What the harvest found inconsistent, kept with the mechanism it describes.
        from OCP.TDataStd import TDataStd_ExtStringArray

        found = TDataStd_ExtStringArray.Set_s(top, 1, len(mechanism.warnings))
        for at, text in enumerate(mechanism.warnings, 1):
            found.SetValue(at, _text(text))
    pair_labels = []
    for at, pair in enumerate(mechanism.pairs, 1):
        entity, dofs = KINDS[pair.kind]
        ranged = any(limit is not None for limits in pair.limits for limit in limits)
        label = _label(pairs_label, at, pair.label, entity + ("_WITH_RANGE" if ranged else ""))
        data = TDataStd_NamedData.Set_s(label)
        if pair.frames is not None:
            data.SetArrayOfReals(_text("transform_item_1"), _reals(pair.frames[0]))
            data.SetArrayOfReals(_text("transform_item_2"), _reals(pair.frames[1]))
        for (_unit, lower_name, upper_name), (lower, upper) in zip(dofs, pair.limits):
            if lower is not None:
                data.SetReal(_text(lower_name), float(lower))
            if upper is not None:
                data.SetReal(_text(upper_name), float(upper))
        if pair.ratio is not None:
            data.SetReal(_text("gear_ratio"), float(pair.ratio))
        if pair.inconsistent:
            data.SetString(_text("cadgen_inconsistent"), _text(pair.inconsistent))  # cadgen's, no AP242 attribute
        references = TDataStd_ReferenceList.Set_s(label)
        references.Append(link_labels[pair.links[0]])  # KINEMATIC_JOINT.edge_start: link 1
        references.Append(link_labels[pair.links[1]])  # edge_end: link 2
        pair_labels.append(label)
    _state(_label(top, 3, "as built", "MECHANISM_STATE_REPRESENTATION"), pair_labels, mechanism.as_built,
           mechanism.pairs)
    configurations = _label(top, 4, "configurations")
    configuration_labels = {}
    for at, (name, values) in enumerate(mechanism.configurations.items(), 1):
        label = configuration_labels[name] = _label(configurations, at, name, "MECHANISM_STATE_REPRESENTATION")
        _state(label, pair_labels, values, mechanism.pairs)
    if mechanism.motions:
        # Each motion as AP242's interpolated configuration sequence: its segments' times and states.
        motions = _label(top, 5, "motions")
        for at, (name, motion) in enumerate(mechanism.motions.items(), 1):
            label = _label(motions, at, name, "INTERPOLATED_CONFIGURATION_SEQUENCE")
            data = TDataStd_NamedData.Set_s(label)
            data.SetString(_text("cadgen_ease"), _text(motion.ease))  # cadgen's, no AP242 attribute
            if motion.loop:
                data.SetString(_text("cadgen_loop"), _text(motion.loop))
            for key_at, (t, configuration, values) in enumerate(motion.keys, 1):
                segment = _label(label, key_at, repr(float(t)), "INTERPOLATED_CONFIGURATION_SEGMENT")
                TDataStd_NamedData.Set_s(segment).SetReal(_text("t_parameter"), float(t))
                if configuration is not None:
                    TDataStd_ReferenceList.Set_s(segment).Append(configuration_labels[configuration])
                else:
                    _state(segment, pair_labels, values, mechanism.pairs)
    if mechanism.couplings:
        # Each coupling, the engine's own entity (AP242 has no virtual degree of freedom): its
        # range and lock, and its terms as a state's rows, a ratio where a state holds a value.
        couplings = _label(top, 6, "couplings")
        for at, (name, coupling) in enumerate(mechanism.couplings.items(), 1):
            label = _label(couplings, at, name, COUPLING)
            data = TDataStd_NamedData.Set_s(label)
            data.SetReal(_text("lower_limit"), float(coupling.range[0]))
            data.SetReal(_text("upper_limit"), float(coupling.range[1]))
            if coupling.lock:
                data.SetInteger(_text("lock"), 1)
            _state(label, pair_labels, coupling.ratios, mechanism.pairs)


def _children_of(label: Any) -> list:
    from OCP.TDF import TDF_ChildIterator

    found = []
    iterator = TDF_ChildIterator(label, False)
    while iterator.More():
        found.append(iterator.Value())
        iterator.Next()
    return found


def _named(label: Any) -> Any:
    from OCP.TDataStd import TDataStd_NamedData

    from cadgen.engine.document import _find

    data = TDataStd_NamedData()
    if not _find(label, data, TDataStd_NamedData.GetID_s()):
        return None
    data.LoadDeferredData()
    return data


def _string(data: Any, key: str) -> str | None:
    return data.GetString(_text(key)).ToExtString() if data is not None and data.HasString(_text(key)) else None


def _real(data: Any, key: str) -> float | None:
    return data.GetReal(_text(key)) if data is not None and data.HasReal(_text(key)) else None


def _array(data: Any, key: str) -> tuple[float, ...] | None:
    if data is None or not data.HasArrayOfReals(_text(key)):
        return None
    array = data.GetArrayOfReals(_text(key))
    return tuple(array.Value(at) for at in range(array.Lower(), array.Upper() + 1))


def _references(label: Any) -> list[str]:
    from OCP.TDataStd import TDataStd_ReferenceList

    from cadgen.engine.document import _entry, _find

    found = TDataStd_ReferenceList()
    if not _find(label, found, TDataStd_ReferenceList.GetID_s()):
        return []
    return [_entry(item) for item in found.List()]


def _read_state(label: Any, pair_at: dict[str, int], pairs: list[Pair]) -> dict[str, tuple[float, ...]]:
    from OCP.TDataStd import TDataStd_IntegerArray, TDataStd_RealArray

    from cadgen.engine.document import _find

    references = _references(label)
    reals, dofs = TDataStd_RealArray(), TDataStd_IntegerArray()
    if not references or not _find(label, reals, TDataStd_RealArray.GetID_s()) \
            or not _find(label, dofs, TDataStd_IntegerArray.GetID_s()):
        return {}
    rows: dict[str, list[float]] = {}
    for row, entry in enumerate(references):
        pair = pairs[pair_at[entry]]
        values = rows.setdefault(pair.label, [0.0] * pair.dofs)
        values[dofs.Value(dofs.Lower() + row)] = reals.Value(reals.Lower() + row)
    return {label_: tuple(values) for label_, values in rows.items()}


def read(doc: Any, occurrences: dict[str, tuple[int, ...]]) -> Mechanism | None:
    """The mechanism a document holds, or None. ``occurrences`` maps an occurrence's label
    entries, ``/``-joined from the root, to its component indices."""
    from OCP.Standard import Standard_GUID
    from OCP.XCAFDoc import XCAFDoc_AssemblyItemRef

    from cadgen.engine.document import _entry, _find, _label_name

    marker = Standard_GUID(GUID)
    root = next((label for label in _children_of(doc.Main()) if label.IsAttribute(marker)), None)
    if root is None:
        return None
    top = root.FindChild(1, False)
    links_label, pairs_label = top.FindChild(1, False), top.FindChild(2, False)
    links, link_at = [], {}
    for label in _children_of(links_label):
        members = []
        for member in _children_of(label):
            item = XCAFDoc_AssemblyItemRef()
            if _find(member, item, XCAFDoc_AssemblyItemRef.GetID_s()):
                path = occurrences.get(item.GetItem().ToString().ToCString())
                if path is not None:
                    members.append(path)
        link_at[_entry(label)] = len(links)
        links.append(Link(_label_name(label), members))
    pairs, pair_at = [], {}
    for label in _children_of(pairs_label):
        data = _named(label)
        entity = _string(data, "entity") or ""
        kind = _BY_ENTITY.get(entity.removesuffix("_WITH_RANGE"))
        ends = [link_at.get(entry) for entry in _references(label)]
        if kind is None or len(ends) != 2 or None in ends:
            continue
        first, second = _array(data, "transform_item_1"), _array(data, "transform_item_2")
        limits = tuple((_real(data, lower), _real(data, upper)) for _unit, lower, upper in KINDS[kind][1])
        pair_at[_entry(label)] = len(pairs)
        pairs.append(Pair(_label_name(label), kind, (ends[0], ends[1]),
                          (first, second) if first is not None and second is not None else None, limits,
                          _real(data, "gear_ratio"), _string(data, "cadgen_inconsistent")))
    ground = link_at.get(next(iter(_references(top)), ""), 0)
    as_built = _read_state(top.FindChild(3, False), pair_at, pairs)
    configuration_labels = _children_of(top.FindChild(4, False))
    configurations = {_label_name(label): _read_state(label, pair_at, pairs) for label in configuration_labels}
    named = {_entry(label): _label_name(label) for label in configuration_labels}
    motions = {}
    held_motions = top.FindChild(5, False)
    for label in ([] if held_motions.IsNull() else _children_of(held_motions)):
        data = _named(label)
        keys = []
        for segment in _children_of(label):
            references = _references(segment)
            t = _real(_named(segment), "t_parameter") or 0.0
            if references and references[0] in named:
                keys.append((t, named[references[0]], {}))
            else:
                keys.append((t, None, _read_state(segment, pair_at, pairs)))
        motions[_label_name(label)] = Motion(_label_name(label), keys, _string(data, "cadgen_ease") or "cubic",
                                             _string(data, "cadgen_loop"))
    couplings = {}
    held_couplings = top.FindChild(6, False)
    for label in ([] if held_couplings.IsNull() else _children_of(held_couplings)):
        data = _named(label)
        lower, upper = _real(data, "lower_limit"), _real(data, "upper_limit")
        if _string(data, "entity") != COUPLING or lower is None or upper is None:
            continue
        lock = data.HasInteger(_text("lock")) and data.GetInteger(_text("lock")) == 1
        couplings[_label_name(label)] = Coupling(_label_name(label), _read_state(label, pair_at, pairs),
                                                 (lower, upper), lock)
    from OCP.TDataStd import TDataStd_ExtStringArray

    held = TDataStd_ExtStringArray()
    warnings = ([held.Value(at).ToExtString() for at in range(held.Lower(), held.Upper() + 1)]
                if _find(top, held, TDataStd_ExtStringArray.GetID_s()) else [])
    return Mechanism(_label_name(top), links, ground, pairs, as_built, configurations, warnings, motions, couplings)


# --- what the page reads -------------------------------------------------------------------


def _json_value(values: tuple[float, ...] | list[float]) -> Any:
    return float(values[0]) if len(values) == 1 else [float(value) for value in values]


def _tree(mechanism: Mechanism) -> tuple[dict[int, int], list[str] | None]:
    """Each link reached from the ground, by the pair that reaches it (gears aside), and a
    loop's pairs where the pairs close one."""
    reached: dict[int, int] = {mechanism.ground: -1}
    via: dict[int, int | None] = {mechanism.ground: None}
    queue = [mechanism.ground]
    loop = None
    used: set[int] = set()
    while queue:
        link = queue.pop(0)
        for at, pair in enumerate(mechanism.pairs):
            if pair.kind == "gear" or at in used or link not in pair.links or pair.links[0] == pair.links[1]:
                continue
            used.add(at)
            other = pair.links[1] if pair.links[0] == link else pair.links[0]
            if other in reached:
                if loop is None:
                    loop = _loop(mechanism, via, link, other, at)
                continue
            reached[other] = at
            via[other] = at
            queue.append(other)
    return reached, loop


def _loop(mechanism: Mechanism, via: dict, a: int, b: int, closing: int) -> list[str]:
    def chain(link: int) -> list[int]:
        found = []
        while via.get(link) is not None:
            found.append(via[link])
            pair = mechanism.pairs[via[link]]
            link = pair.links[0] if pair.links[1] == link else pair.links[1]
        return found

    left, right = chain(a), chain(b)
    while left and right and left[-1] == right[-1]:
        left.pop()
        right.pop()
    return [mechanism.pairs[at].label for at in [*left, closing, *reversed(right)]]


def block(mechanism: Mechanism, instance_paths: list[tuple[int, ...]],
          world: Callable[[tuple[int, ...]], list[float]]) -> dict[str, Any]:
    """The structure's ``kinematics``: what a page needs to pose the document's instances.
    ``world(path)`` is an occurrence's placement as built (4x4, column-major)."""
    owner: dict[tuple[int, ...], int] = {path: at for at, link in enumerate(mechanism.links) for path in link.members}
    members: list[list[int]] = [[] for _ in mechanism.links]
    for index, path in enumerate(instance_paths if mechanism.links else ()):
        # The deepest link holding the instance; none, the ground.
        found = mechanism.ground
        for depth in range(len(path), -1, -1):
            if path[:depth] in owner:
                found = owner[path[:depth]]
                break
        members[found].append(index)
    reached, loop = _tree(mechanism)
    child_side = {pair_at: link for link, pair_at in reached.items() if pair_at >= 0}
    driving = {link: pair_at for pair_at, link in child_side.items()}
    pairs = []
    for pair in mechanism.pairs:
        entry: dict[str, Any] = {"label": pair.label, "type": pair.kind, "links": list(pair.links)}
        if pair.kind == "gear":
            first, second = (driving.get(link) for link in pair.links)
            entry["ratio"] = pair.ratio
            entry["drives"] = None if first is None or second is None else [mechanism.pairs[first].label,
                                                                             mechanism.pairs[second].label]
        else:
            entry["frames"] = [_column(frame_matrix(frame)) for frame in pair.frames] if pair.frames else None
            entry["limits"] = [list(limits) for limits in pair.limits]
            entry["units"] = [unit for unit, *_names in KINDS[pair.kind][1]]
        if pair.inconsistent:
            entry["inconsistent"] = pair.inconsistent
        pairs.append(entry)
    links = [{"name": link.name, "instances": members[at],
              "matrix": world(link.members[0]) if link.members else _column(np.eye(4))}
             for at, link in enumerate(mechanism.links)]
    warnings = list(mechanism.warnings)
    if loop:
        warnings.append(f"pairs {', '.join(loop)} close a loop, which has no pose without a solver: posing them is "
                        "refused")
    return {"schema": SCHEMA, "name": mechanism.name, "ground": mechanism.ground, "links": links, "pairs": pairs,
            "asBuilt": {label: _json_value(values) for label, values in mechanism.as_built.items()},
            "configurations": {name: {label: _json_value(values) for label, values in values_.items()}
                               for name, values_ in mechanism.configurations.items()},
            "couplings": {name: _coupling_json(coupling, mechanism.pairs) for name, coupling in mechanism.couplings.items()},
            "motions": {name: _motion_json(motion) for name, motion in mechanism.motions.items()},
            "loop": loop, "warnings": warnings}


def _motion_json(motion: Motion) -> dict[str, Any]:
    keys = [{"t": t, "configuration": configuration} if configuration is not None
            else {"t": t, "values": {label: _json_value(value) for label, value in values.items()}}
            for t, configuration, values in motion.keys]
    return {"duration": motion.duration, "ease": motion.ease, "loop": motion.loop, "keys": keys}



# --- motions over time -----------------------------------------------------------------------


class Timeline:
    """A motion of the structure's ``kinematics`` over time: ``values(t)`` the pair values
    at ``t`` seconds of its clip, every pair the motion does not move as built and each
    pair a gear drives from its driver. A clip of a ``pingpong`` motion is twice its
    duration, out and back; ``breaks`` are the times its keys fall at, where its speed may
    jump."""

    def __init__(self, kinematics: dict[str, Any], name: str) -> None:
        motions = kinematics.get("motions") or {}
        if name not in motions:
            raise KinematicsError(f"the document has no motion {name!r}; its motions: "
                                  f"{', '.join(sorted(motions)) or 'none'}")
        entry = motions[name]
        self.kinematics, self.name = kinematics, name
        self.ease, self.loop = entry["ease"], entry.get("loop")
        keys = entry["keys"]
        self.times = np.array([float(key["t"]) for key in keys])
        self.duration = float(self.times[-1])
        self.period = self.duration * (2 if self.loop == "pingpong" else 1)
        configurations = kinematics.get("configurations") or {}
        driven = _driven(kinematics)
        moving: set[str] = set()
        poses = []
        for key in keys:
            if "configuration" in key:
                moving |= set(configurations[key["configuration"]])
                poses.append(resolve(kinematics, key["configuration"])[0])
            else:
                moving |= set(key["values"])
                poses.append(resolve(kinematics, key["values"], own=True)[0])
        self.labels = sorted(label for label in moving if label not in driven)
        self.sizes = [len(poses[0][label]) for label in self.labels]
        self.table = np.array([[value for label in self.labels for value in pose[label]] for pose in poses],
                              dtype=float).reshape(len(poses), sum(self.sizes))
        pairs = _block_pairs(kinematics)
        limits = [limit for label in self.labels for limit in (pairs[label].get("limits") or ())]
        self.lower = np.array([-math.inf if low is None else low for low, _high in limits], dtype=float)
        self.upper = np.array([math.inf if high is None else high for _low, high in limits], dtype=float)

    def local(self, t: float) -> float:
        """The motion's own time at ``t`` seconds of its clip."""
        return 2 * self.duration - t if self.loop == "pingpong" and t > self.duration else t

    def joints(self, t: float) -> dict[str, tuple[float, ...]]:
        flat = np.clip(_interpolated(self.times, self.table, self.ease, self.local(t)), self.lower, self.upper)
        found, at = {}, 0
        for label, size in zip(self.labels, self.sizes):
            found[label] = tuple(float(value) for value in flat[at:at + size])
            at += size
        return found

    def values(self, t: float) -> dict[str, tuple[float, ...]]:
        return resolve(self.kinematics, self.joints(t), own=True)[0]

    @property
    def breaks(self) -> list[float]:
        found = [float(t) for t in self.times]
        if self.loop == "pingpong":
            found += [2 * self.duration - t for t in reversed(found[:-1])]
        return found


# --- poses -------------------------------------------------------------------------------


def _block_pairs(kinematics: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {pair["label"]: pair for pair in kinematics["pairs"]}


def _driven(kinematics: dict[str, Any]) -> dict[str, tuple[str, float, str]]:
    """Each pair a gear drives: (its driver, the ratio, the gear)."""
    return {pair["drives"][1]: (pair["drives"][0], float(pair["ratio"] or 0.0), pair["label"])
            for pair in kinematics["pairs"] if pair["type"] == "gear" and pair.get("drives")}


def _as_tuple(value: Any) -> tuple[float, ...]:
    return (float(value),) if isinstance(value, (int, float)) else tuple(float(item) for item in value)


def _block_checked(pair: dict[str, Any], value: Any, where: str) -> tuple[float, ...]:
    as_pair = Pair(pair["label"], pair["type"], tuple(pair["links"]), None,
                   tuple(tuple(limit) for limit in pair.get("limits") or ()))
    return _checked(as_pair, value, where)


def resolve(kinematics: dict[str, Any] | None, pose: Any, *,
            own: bool = False) -> tuple[dict[str, tuple[float, ...]], str | None]:
    """The values a pose sets every pair to, and the configuration it names: ``pose`` is
    None (as built), a configuration's name, or ``{joint label or coupling name: value}``
    (the rest as built; a coupling at ``c`` puts each of its joints at its as-built values
    plus its ratio times ``c``, couplings sharing a joint adding). An unknown name or joint,
    a value out of range, a value for a pair a gear drives, a locked coupling's joint set
    alone and a coupling set with one of its joints are refused by name. ``own``: the values
    are the document's own (a motion's keys), which set a locked coupling's joints."""
    if kinematics is None:
        if pose is None:
            return {}, None
        raise KinematicsError("the document has no joints to pose")
    as_built = {label: _as_tuple(value) for label, value in kinematics["asBuilt"].items()}
    values = dict(as_built)
    pairs = _block_pairs(kinematics)
    driven = _driven(kinematics)
    couplings = kinematics.get("couplings") or {}
    locked = _locked(couplings)
    loop = kinematics.get("loop") or ()
    named = None
    if isinstance(pose, str):
        configurations = kinematics.get("configurations") or {}
        if pose not in configurations:
            known = ", ".join(sorted(configurations)) or "none"
            raise KinematicsError(f"the document has no configuration {pose!r}; its configurations: {known}")
        named, given, where = pose, configurations[pose], f"configuration {pose!r}"
    elif isinstance(pose, dict):
        given, where = pose, "the pose"
    elif pose is None:
        given, where = {}, ""
    else:
        raise KinematicsError("a pose is a configuration's name or an object of joint values")

    def unposable(label: str) -> str | None:
        """Why the pair cannot be posed, or None."""
        if pairs[label].get("inconsistent"):
            return pairs[label]["inconsistent"]
        if label in loop:
            return f"pairs {', '.join(loop)} close a loop, which has no pose without a solver"
        return None

    controls = {}
    for label, value in given.items():
        pair = pairs.get(label)
        if pair is None and label in couplings:
            controls[label] = value
            continue
        if pair is None or pair["type"] == "gear":
            movable = ", ".join(sorted(name for name, found in pairs.items() if found["type"] != "gear")) or "none"
            raise KinematicsError(f"{where} names no joint {label!r}; the document's joints: {movable}"
                                  + (f"; its couplings: {', '.join(sorted(couplings))}" if couplings else ""))
        why = unposable(label)
        if why:
            raise KinematicsError(f"{where}: joint {label!r} cannot be posed: {why}")
        if label in driven and not named:
            driver, _ratio, gear = driven[label]
            raise KinematicsError(f"{where}: joint {label!r} is driven by {driver!r} through gear {gear!r}; "
                                  f"set {driver!r}")
        if label in locked and not named and not own:
            raise KinematicsError(f"{where}: joint {label!r} moves only with the locked coupling {locked[label]!r}; "
                                  f"set {locked[label]!r}")
        values[label] = _block_checked(pair, value, where)
    for name in controls:
        for label, _index, _ratio in couplings[name]["terms"]:
            if label in given:
                raise KinematicsError(f"{where} sets coupling {name!r} and its joint {label!r}: set one of them")
            why = unposable(label)
            if why:
                raise KinematicsError(f"{where}: coupling {name!r} cannot be posed: its joint {label!r} cannot be: "
                                      f"{why}")
    values.update(_coupled(controls, couplings, as_built,
                           lambda label, row, at: _block_checked(pairs[label], row, at), where))
    for label, (driver, ratio, gear) in driven.items():
        if driver not in values or label not in values:
            continue
        found = (values[driver][0] * ratio, *values[label][1:])
        values[label] = _block_checked(pairs[label], found, f"gear {gear!r} driving {label!r} from {driver!r}")
    return values, named


def link_worlds(kinematics: dict[str, Any], values: dict[str, tuple[float, ...]]) -> list[np.ndarray]:
    """Each link's placement at ``values``: the ground where it is, every other link by its
    pairs from the ground down (a pair that closes a loop is not followed; a pair with no value,
    inconsistent, holds its links as built)."""
    links = kinematics["links"]
    if not links:
        return []
    found: list[np.ndarray | None] = [None] * len(links)
    found[kinematics["ground"]] = _from_column(links[kinematics["ground"]]["matrix"])
    pending = [pair for pair in kinematics["pairs"] if pair["type"] != "gear" and pair.get("frames")]
    progress = True
    while pending and progress:
        progress = False
        for pair in list(pending):
            first, second = pair["links"]
            if pair.get("inconsistent") or pair["label"] not in values:
                # The two links as they stand: the second's placement as built over the first's.
                item_1 = np.linalg.inv(_from_column(links[first]["matrix"]))
                item_2 = np.linalg.inv(_from_column(links[second]["matrix"]))
                moved = np.eye(4)
            else:
                item_1, item_2 = (_from_column(frame) for frame in pair["frames"])
                moved = pair_motion(pair["type"], values[pair["label"]])
            if found[first] is not None and found[second] is None:
                found[second] = found[first] @ item_1 @ moved @ np.linalg.inv(item_2)
            elif found[second] is not None and found[first] is None:
                found[first] = found[second] @ item_2 @ np.linalg.inv(moved) @ np.linalg.inv(item_1)
            elif found[first] is None:
                continue
            pending.remove(pair)
            progress = True
    return [matrix if matrix is not None else _from_column(links[at]["matrix"]) for at, matrix in enumerate(found)]


def posed_instances(structure: dict[str, Any], values: dict[str, tuple[float, ...]]) -> list[list[float]]:
    """Every instance's world matrix (column-major) with the mechanism at ``values``."""
    kinematics = structure["kinematics"]
    reference = kinematics.get("pose", {}).get("values") or kinematics["asBuilt"]
    at = link_worlds(kinematics, values)
    rest = link_worlds(kinematics, {label: _as_tuple(value) for label, value in reference.items()})
    matrices = [list(instance["matrix"]) for instance in structure["instances"]]
    for link, (now, then) in enumerate(zip(at, rest)):
        delta = now @ np.linalg.inv(then)
        if np.allclose(delta, np.eye(4), rtol=0, atol=1e-12):
            continue
        for index in kinematics["links"][link]["instances"]:
            matrices[index] = _column(delta @ _from_column(matrices[index]))
    return matrices


def posed_structure(structure: dict[str, Any], pose: Any) -> dict[str, Any]:
    """The structure with its instances where ``pose`` places them, its kinematics saying
    which pose that is (``pose``: the values and the configuration named)."""
    kinematics = structure.get("kinematics")
    values, named = resolve(kinematics, pose)
    if kinematics is None:
        return structure
    matrices = posed_instances(structure, values)
    found = dict(structure)
    found["instances"] = [{**instance, "matrix": matrix} for instance, matrix in zip(structure["instances"], matrices)]
    found["kinematics"] = {**kinematics, "pose": {"values": {label: _json_value(value) for label, value in values.items()},
                                                  "configuration": named}}
    return found
