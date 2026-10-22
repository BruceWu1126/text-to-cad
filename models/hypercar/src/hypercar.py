"""Mid-engine hypercar -- full assembly.

Body panels are cut from one master surface (see ``lib/surfaces.py``), so
highlight lines cross every shutline without a kink and every panel gap is a
real constant-width gap.

The assembly tree is grouped BY SYSTEM -- thirteen sibling stages under
``src/``, each a ``@memo`` function composed here by CALLING it, in this order
(the old occurrence ids the clips below were written against):

    o1.1  body              painted panels, pillars, aero skins
    o1.2  glazing           DLO glass + lamp lenses
    o1.3  lighting          lamp internals + light signature
    o1.4  chassis           monocoque tub, subframes, crash structures
    o1.5  suspension_front  wishbones, uprights, pushrods, rockers, coilovers
    o1.6  suspension_rear   ditto, rear
    o1.7  wheels            rims, tyres
    o1.8  brakes            discs, calipers, hubs
    o1.9  powertrain        engine, intake, exhaust, transaxle, driveshafts
    o1.10 interior          seats, wheel, dash, console, pedals, door cards
    o1.11 aero              splitter, diffuser, wing
    o1.12 hinge             dihedral synchro-helix door mechanism
    o1.13 details           mirrors, badges, filler, vents, fasteners

The car is one xbf (``XBF/hypercar.xbf``) and one GLB (``XBF/hypercar.glb``),
whose clips are the three motions below: ``doors``, ``showcase`` and ``explode``.
"""

from __future__ import annotations

import math

from cadgen.engine import configuration, glb, motion, xbf

import build123d as bd

from aero import aero
from body import body
from brakes import brakes
from chassis import chassis
from details import details
from glazing import glazing
from hinge import hinge
from interior import interior
from lighting import lighting
from powertrain import powertrain
from suspension_front import suspension_front
from suspension_rear import suspension_rear
from wheels import wheels

from lib import hinge as hinge_lib

# Order here IS the occurrence order, and the explode groups below name parts by
# it (``o1.3.49`` is lighting's 49th child): do not reorder without them.
SYSTEMS = [
    body,
    glazing,
    lighting,
    chassis,
    suspension_front,
    suspension_rear,
    wheels,
    brakes,
    powertrain,
    interior,
    aero,
    hinge,
    details,
]

SIDES = ("left", "right")


# ---------------------------------------------------------------------------
# Finding parts, and joints in the holder's parent frame
# ---------------------------------------------------------------------------

def _part(car, label):
    """The one node of the car labelled ``label``."""
    found = [node for node in car.descendants if node.label == label]
    if len(found) != 1:
        raise LookupError(f"{len(found)} nodes of the car are labelled {label!r}")
    return found[0]


def _old(car, ref):
    """The node an old occurrence id names: ``o1.3.49`` is the car's 3rd child's 49th."""
    node = car
    for index in ref.split(".")[1:]:
        node = node.children[int(index) - 1]
    return node


def _old_range(system, first, last):
    """``o1.<system>.<first>`` .. ``o1.<system>.<last>``, the old ``ids()``."""
    return [f"o1.{system}.{i}" for i in range(first, last + 1)]


def _parent_world(node):
    parent = node.parent
    return parent.global_location if parent is not None else bd.Location()


def _turn_only(location):
    """``location``'s rotation, about the origin."""
    return bd.Location(location.position).inverse() * location


def _axis_for(holder, origin, direction):
    """A world axis as a joint on ``holder`` takes it: in the holder's parent frame."""
    world = bd.Plane(origin=origin, z_dir=direction).location
    plane = bd.Plane(_parent_world(holder).inverse() * world)
    return bd.Axis(plane.origin, plane.z_dir)


def _rides(label, carrier, rider):
    """``rider`` fastened to ``carrier``: a pair of rigid joints of one label where they stand."""
    bd.RigidJoint(label, carrier, bd.Location())
    bd.RigidJoint(label, rider, bd.Location())


def _slide(label, holder, lead, direction, limits, riders=()):
    """``lead`` on a prismatic joint of ``holder`` along the world ``direction`` (its axis
    through the world origin), ``riders`` fastened to ``lead``. The rigid half stands where
    the joint holds it at 0: the holder's axes, at the axis origin (in ``lead``'s parent
    frame)."""
    origin = (0.0, 0.0, 0.0)
    bd.LinearJoint(label, holder, axis=_axis_for(holder, origin, direction), linear_range=limits)
    held = bd.Location(origin) * _turn_only(holder.global_location)
    bd.RigidJoint(label, lead, _parent_world(lead).inverse() * held)
    for index, rider in enumerate(riders):
        _rides(f"{label}_{index}", lead, rider)


def _norm(v):
    return math.sqrt(sum(c * c for c in v))


def _unit(v):
    m = _norm(v)
    return tuple(c / m for c in v)


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


# ---------------------------------------------------------------------------
# Kinematics: the dihedral synchro-helix doors.
#
# One CYLINDRICAL joint per door -- rotation and axial travel about the SAME
# tower axis is exactly what a cylindrical joint is -- paired by its label with
# a rigid joint on the door skin where the door stands shut. Open is the
# mechanism's own lead: 62 deg of rotation while sweeping 310 mm along the axis
# (299 up, 80 forward), each door's turn and travel set together (``_open``),
# so a motion between shut and open keeps them in step. The numbers come
# straight from lib/hinge.py, so changing the tower changes the joints with it.
#
# The joint is held by the door's FLANK (the front fender, which leads the
# flank's explode group below), not by the chassis: the explodes carry the
# door off with its flank, and at the flank's rest -- every pose but the
# explodes' -- the flank stands where it was built, so the door turns about the
# same tower axis it did on the chassis.
#
# Everything that rides the door -- glass, trim, mirror, and the two mechanism
# parts bolted to it -- lives in another system's group, so it does not ride for
# free: each set hangs on a prismatic joint the door holds (``RIDERS`` below),
# which stands at 0 in every pose but the explodes', so it rides the door.
# ---------------------------------------------------------------------------

def _sweep(side):
    """The door's signed turn at full open, degrees: the two sides are opposite-hand screws."""
    return hinge_lib.DOOR_SWEEP_DEG * hinge_lib.DOOR_SWEEP_SIGN[side]


def _open(side):
    """The door's (turn, travel) fully open: 62 deg about the tower axis while travelling
    310 mm along it."""
    return (_sweep(side), hinge_lib.CARRIER_TRAVEL)


def _door_joints(car):
    """Each door's cylindrical joint on its flank's lead panel."""
    for side in SIDES:
        name, sweep = f"door_{side}", _sweep(side)
        holder = _part(car, f"front_fender:{side}")
        origin, direction = hinge_lib.HELIX_AXIS_ORIGIN[side], hinge_lib.HELIX_AXIS_DIR[side]
        bd.CylindricalJoint(name, holder, axis=_axis_for(holder, origin, direction),
                            angular_range=(min(0.0, sweep), max(0.0, sweep)),
                            linear_range=(0.0, hinge_lib.CARRIER_TRAVEL))
        door = _part(car, f"door:{side}")
        frame = bd.Plane(origin=origin, z_dir=direction).location
        bd.RigidJoint(name, door, _parent_world(door).inverse() * frame)   # shut: as built


# ---------------------------------------------------------------------------
# The explodes: the old ``showcase`` and ``explode`` clips
# ---------------------------------------------------------------------------
#
# Each old explode group moved by one fixed vector times a 0..1 factor ``k``:
#
#   radial   away from the car's long axis: unit [0, cy, cz - CAR_AXIS_Z] times
#            the gain, ``c`` the group's pinned rest centre
#   lateral  straight out in +/-Y by the side its centre sits on: [0, +-gain, 0]
#   fixed    along one declared vector
#
# so each group slides on ONE prismatic joint the chassis holds (the chassis is
# the ground: it appears in no group), along its vector, at ``k`` times its
# length. The group's first part leads (holds the joint's rigid half), the rest
# are fastened to it; where a group is a whole sub-assembly (a corner, the
# powertrain, the interior), the sub-assembly itself leads.
#
# ``tour`` is the window the group occupies in the 48 s showcase. The order --
# skin, then glass and trim, then interior, then powertrain, then running gear
# -- is a strip-down: you always remove what is on top of the thing you want to
# see next, so the car never hides the part being shown.

CAR_AXIS_Z = 560.0

SKIN_TOUR = (0.1, 0.22, 0.86, 0.97)
GLASS_TOUR = (0.11, 0.23, 0.86, 0.97)
TRIM_TOUR = (0.12, 0.24, 0.86, 0.97)
INTERIOR_TOUR = (0.24, 0.36, 0.86, 0.97)
POWERTRAIN_TOUR = (0.38, 0.5, 0.86, 0.97)
SUSPENSION_TOUR = (0.66, 0.78, 0.84, 0.95)
WHEEL_TOUR = (0.68, 0.8, 0.84, 0.95)
BRAKE_TOUR = (0.7, 0.81, 0.84, 0.95)


def _radial(centre, gain):
    return tuple(c * gain for c in _unit((0.0, centre[1], centre[2] - CAR_AXIS_Z)))


def _lateral(centre, gain):
    return (0.0, gain if centre[1] >= 0 else -gain, 0.0)


# (joint, old occurrence ids, vector, tour). Doors and their riders, which the old
# groups moved too, are taken out of their groups below (``RIDERS``).
GROUPS = [
    # body: the flanks swing out sideways, the upper skins lift, the ends draw
    # fore and aft, the floor drops away
    ("explode_flank_left", ["o1.1.3", "o1.1.4", "o1.1.5", "o1.1.6", "o1.1.18", "o1.1.20", "o1.1.22"],
     _radial((-82.5, 772.8, 567.1), 1500), SKIN_TOUR),
    ("explode_flank_right", ["o1.1.7", "o1.1.8", "o1.1.9", "o1.1.10", "o1.1.19", "o1.1.21", "o1.1.23"],
     _radial((-82.5, -772.8, 567.1), 1500), SKIN_TOUR),
    ("explode_upper_skins", ["o1.1.2", "o1.1.11", "o1.1.13", "o1.1.14", "o1.1.15", "o1.1.17"],
     _radial((-82.5, 0.0, 849.1), 1500), SKIN_TOUR),
    ("explode_nose", ["o1.1.1", "o1.1.24", "o1.1.25", "o1.1.26"], (1500.0, 0.0, 150.0), SKIN_TOUR),
    ("explode_rear_fascia", ["o1.1.16"], (-1500.0, 0.0, 150.0), SKIN_TOUR),
    ("explode_underbody", ["o1.1.12"], (0.0, 0.0, -900.0), SKIN_TOUR),

    # glazing: screens lift, lenses leave with the lamps they cover (the side
    # glass follows its door's flank: a rider, below)
    ("explode_screens", ["o1.2.1", "o1.2.4"], _radial((-230.0, 0.0, 947.9), 2150), GLASS_TOUR),
    ("explode_headlight_lenses", ["o1.2.5", "o1.2.6"], (1750.0, 0.0, 300.0), GLASS_TOUR),
    ("explode_taillight_lens", ["o1.2.7"], (-1750.0, 0.0, 300.0), GLASS_TOUR),

    # lighting: head and tail units leave out of their own ends
    ("explode_head_lamps", _old_range(3, 1, 48), (1750.0, 0.0, 560.0), TRIM_TOUR),
    ("explode_tail_lamps", _old_range(3, 49, 92), (-1750.0, 0.0, 560.0), TRIM_TOUR),

    # chassis holds still -- no entry.

    # suspension: each corner straight out, the steering rack and ARBs along the
    # car so they stay legible between the corners
    ("explode_suspension_front_left", ["o1.5.1"], _lateral((1242.1, 437.2, 448.9), 1150), SUSPENSION_TOUR),
    ("explode_suspension_front_right", ["o1.5.2"], _lateral((1242.1, -437.2, 448.9), 1150), SUSPENSION_TOUR),
    ("explode_steering", ["o1.5.3", "o1.5.4"], (900.0, 0.0, 700.0), SUSPENSION_TOUR),
    ("explode_suspension_rear_left", _old_range(6, 1, 48), _lateral((-1453.2, 546.9, 490.3), 1150),
     SUSPENSION_TOUR),
    ("explode_suspension_rear_right", _old_range(6, 49, 96), _lateral((-1453.2, -546.9, 490.0), 1150),
     SUSPENSION_TOUR),
    ("explode_rear_arb", _old_range(6, 97, 101), (-900.0, 0.0, 700.0), SUSPENSION_TOUR),

    # wheels and brakes: off their own hubs, one corner at a time in space
    ("explode_wheel_front_left", ["o1.7.1", "o1.7.5"], _lateral((1326.9, 855.3, 403.1), 2400), WHEEL_TOUR),
    ("explode_wheel_front_right", ["o1.7.2", "o1.7.6"], _lateral((1326.9, -855.3, 403.1), 2400), WHEEL_TOUR),
    ("explode_wheel_rear_left", ["o1.7.3", "o1.7.7"], _lateral((-1351.6, 835.2, 424.4), 2400), WHEEL_TOUR),
    ("explode_wheel_rear_right", ["o1.7.4", "o1.7.8"], _lateral((-1351.6, -835.2, 424.4), 2400), WHEEL_TOUR),
    ("explode_brake_front_left", ["o1.8.1", "o1.8.2"], _lateral((1350.0, 852.7, 397.0), 1750), BRAKE_TOUR),
    ("explode_brake_front_right", ["o1.8.3", "o1.8.4"], _lateral((1347.7, -847.3, 388.1), 1750), BRAKE_TOUR),
    ("explode_brake_rear_left", ["o1.8.5", "o1.8.6"], _lateral((-1350.0, 830.0, 407.2), 1750), BRAKE_TOUR),
    ("explode_brake_rear_right", ["o1.8.7", "o1.8.8"], _lateral((-1350.0, -830.0, 417.4), 1750), BRAKE_TOUR),

    # powertrain out of the back as one unit; it comes apart later, on its own
    ("explode_powertrain", ["o1.9"], (-2350.0, 0.0, 320.0), POWERTRAIN_TOUR),

    # interior lifts straight out of the tub
    ("explode_interior", ["o1.10"], (140.0, 0.0, 1950.0), INTERIOR_TOUR),

    # aero: front furniture forward and down, floor and diffuser back and down,
    # wing back and up -- each leaves the way it was fitted
    ("explode_front_aero", _old_range(11, 1, 11), (1900.0, 0.0, -200.0), TRIM_TOUR),
    ("explode_diffuser", _old_range(11, 12, 18), (-1400.0, 0.0, -800.0), TRIM_TOUR),
    ("explode_wing", _old_range(11, 19, 24), (-1200.0, 0.0, 900.0), TRIM_TOUR),

    # door mechanism: outboard with its own flank
    ("explode_hinge_left", ["o1.12.1"], _lateral((798.5, 821.4, 529.0), 1400), TRIM_TOUR),
    ("explode_hinge_right", ["o1.12.2"], _lateral((798.5, -821.4, 529.0), 1400), TRIM_TOUR),

    # details: side jewellery out with its flank, badges and filler straight up
    ("explode_details_left", _old_range(13, 1, 37), _radial((-64.7, 652.5, 691.5), 2500), SKIN_TOUR),
    ("explode_details_right", _old_range(13, 38, 74), _radial((-64.7, -652.5, 691.5), 2500), SKIN_TOUR),
    ("explode_badges", _old_range(13, 75, 84), (0.0, 0.0, 1400.0), SKIN_TOUR),
]

# --- the doors' riders ------------------------------------------------------
#
# The old explodes moved each door with its flank and each rider with its own
# group, the doors clip moved them all together. A rider set now hangs on a
# prismatic joint its door holds, along its old displacement RELATIVE to the
# door, ``v_rider - v_door``: where the two share a window (every part of the
# explode clip; the mirror's five parts in the showcase too) that is the old
# move exactly. Where the showcase's windows differ (side glass, door cards,
# bracket and lug rise a little or a lot after the door), the joint stands at
# the old relative move's projection on that line -- the nearest point a single
# slide can reach; the holds and the fall back, which the windows share, are
# exact. (side, joint, the rider set's labels, the old group it flew with.)

RIDERS = [
    ("side_glass", ["side_glass"], _radial((-237.9, 513.6, 943.9), 2150), GLASS_TOUR),
    ("door_card", ["door_card_upper", "door_card_lower", "door_pull"], (140.0, 0.0, 1950.0), INTERIOR_TOUR),
    ("mirror", ["mirror_housing", "mirror_bezel", "mirror_glass", "mirror_stalk", "mirror_base"],
     _radial((-64.7, 652.5, 691.5), 2500), SKIN_TOUR),
    ("door_hardware", ["door_bracket", "door_lug_lower"], _lateral((798.5, 821.4, 529.0), 1400), TRIM_TOUR),
]


def _mirrored(vector, side):
    """A left-side vector for ``side``: the right is the mirror image in Y."""
    return vector if side == "left" else (vector[0], -vector[1], vector[2])


def _riders():
    """(joint, side, labels, rider vector, rider tour, door vector) for every rider set."""
    door_vector = {side: dict((g[0], g[2]) for g in GROUPS)[f"explode_flank_{side}"] for side in SIDES}
    return [(f"explode_{name}_{side}", side, [f"{label}:{side}" for label in labels], _mirrored(vector, side),
             tour, door_vector[side])
            for side in SIDES for name, labels, vector, tour in RIDERS]


# --- engine sub-explode -----------------------------------------------------
#
# The powertrain gets a second, nested stage: once the whole unit has moved
# clear of the car it comes apart on its own, around the block. Directions are
# derived from each part's rest-pose centre relative to the block, so heads and
# cam covers leave along their real bank angle rather than a hand-picked
# vector; ``vec`` overrides that where a fixed direction reads better (the
# plenum straight up, the transaxle straight back). Each slides on a prismatic
# joint the powertrain holds, so it comes apart where the unit already stands.

ENGINE_ANCHOR = (-1500.0, 0.0, 478.6)    # engine_block:v12
ENGINE_TOUR = (0.52, 0.62, 0.8, 0.9)


def _engine(centre, gain):
    return tuple(c * gain for c in _unit(tuple(a - b for a, b in zip(centre, ENGINE_ANCHOR))))


ENGINE_PARTS = [
    ("explode_cylinder_head_front", ["o1.9.2"], _engine((-1343.7, 0.0, 626.6), 520)),
    ("explode_cylinder_head_rear", ["o1.9.4"], _engine((-1656.3, 0.0, 626.6), 520)),
    ("explode_cam_cover_front", ["o1.9.3"], _engine((-1310.9, 0.0, 685.6), 980)),
    ("explode_cam_cover_rear", ["o1.9.5"], _engine((-1689.1, 0.0, 685.6), 980)),
    ("explode_gear_drive_cover_left", ["o1.9.6"], _engine((-1500.0, 388.0, 468.0), 760)),
    ("explode_gear_drive_cover_right", ["o1.9.8"], _engine((-1500.0, -388.0, 468.0), 760)),
    ("explode_cam_drive_bosses", ["o1.9.7", "o1.9.9"], _engine((-1500.0, 0.0, 884.0), 760)),
    ("explode_intake", ["o1.9.10"], (0.0, 0.0, 1250.0)),
    ("explode_exhaust", ["o1.9.11"], _engine((-1800.2, 0.2, 526.1), 900)),
    ("explode_transaxle", ["o1.9.12"], (-1150.0, 0.0, 60.0)),
]


def _explode_joints(car):
    """Every explode group on a prismatic of the chassis, the engine's parts on prismatics of
    the powertrain, each door's riders on prismatics of the door."""
    tub = _part(car, "chassis")
    on_doors = {f"door:{side}" for side in SIDES} | {label for _j, _s, labels, *_r in _riders() for label in labels}
    for name, refs, vector, _tour in GROUPS:
        members = [node for node in (_old(car, ref) for ref in refs) if node.label not in on_doors]
        length = _norm(vector)
        _slide(name, tub, members[0], _unit(vector), (0.0, math.ceil(length)), members[1:])
    powertrain_group = _old(car, "o1.9")
    for name, refs, vector in ENGINE_PARTS:
        members = [_old(car, ref) for ref in refs]
        _slide(name, powertrain_group, members[0], _unit(vector), (0.0, math.ceil(_norm(vector))), members[1:])
    for name, side, labels, vector, _tour, door_vector in _riders():
        members = [_part(car, label) for label in labels]
        relative = tuple(a - b for a, b in zip(vector, door_vector))
        reach = math.ceil(_norm(vector) + _norm(door_vector))   # holds every projection the showcase takes
        _slide(name, _part(car, f"door:{side}"), members[0], _unit(relative), (-reach, reach), members[1:])


# --- the clips ----------------------------------------------------------------

def _smooth(t):
    x = min(1.0, max(0.0, t))
    return x * x * (3 - 2 * x)


def _win(p, a, b, c, d):
    """Rise a..b, hold b..c, fall c..d, zero outside: every window ends before 1, so the
    clip ends where it starts and repeats seamlessly."""
    if p <= a or p >= d:
        return 0.0
    if p < b:
        return _smooth((p - a) / (b - a))
    if p <= c:
        return 1.0
    return 1.0 - _smooth((p - c) / (d - c))


def _bump(p):
    """Raised cosine: 0 at both ends, 1 in the middle, its slope 0 at the seam."""
    return 0.5 - 0.5 * math.cos(2 * math.pi * min(1.0, max(0.0, p)))


def _explode_values(k_of, engine_k):
    """Every explode joint's value: each group at ``k_of(tour)`` of its vector, the engine's
    parts at ``engine_k`` of theirs, each rider at its old move relative to its door,
    projected on its joint's line."""
    values = {name: _norm(vector) * k_of(tour) for name, _refs, vector, tour in GROUPS}
    for name, _refs, vector in ENGINE_PARTS:
        values[name] = _norm(vector) * engine_k
    door_k = k_of(SKIN_TOUR)
    for name, _side, _labels, vector, tour, door_vector in _riders():
        line = _unit(tuple(a - b for a, b in zip(vector, door_vector)))
        k = k_of(tour)
        values[name] = _dot(line, tuple(r * k - d * door_k for r, d in zip(vector, door_vector)))
    return values


def _showcase(t):
    """The cinematic tour: skin off, interior out, engine back and then apart, running gear
    off, then everything back together. Doors stay shut."""
    p = (t / 48) % 1
    return _explode_values(lambda tour: _win(p, *tour), _win(p, *ENGINE_TOUR))


def _explode(t):
    """The whole car apart and back together, the engine's own sub-explode with it."""
    k = _bump((t / 10) % 1)
    return _explode_values(lambda _tour: k, k)


@xbf(out="../XBF/hypercar.xbf")
@glb(out="../XBF/hypercar.glb")
def hypercar():
    groups = [system() for system in SYSTEMS]
    car = bd.Compound(children=groups, label="mid_engine_hypercar")
    _door_joints(car)
    _explode_joints(car)
    # Shut is as built; open is each door through its full synchro-helix.
    configuration(car, "open", **{f"door_{side}": _open(side) for side in SIDES})
    # Both doors open and shut again, 7 s (a player repeats it).
    shut = {f"door_{side}": (0.0, 0.0) for side in SIDES}
    motion(car, "doors", [(0, shut), (3.5, "open"), (7, shut)], ease="cubic")
    motion(car, "showcase", _showcase, duration=48)
    return motion(car, "explode", _explode, duration=10)
