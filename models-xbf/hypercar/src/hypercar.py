"""Mid-engine hypercar -- full assembly.

Body panels are cut from one master surface (see ``lib/surfaces.py``), so
highlight lines cross every shutline without a kink and every panel gap is a
real constant-width gap.

The assembly tree is grouped BY SYSTEM -- thirteen sibling stages under
``src/``, each a ``@memo`` function composed here by CALLING it, in this order:

    body              painted panels, pillars, aero skins
    glazing           DLO glass + lamp lenses
    lighting          lamp internals + light signature
    chassis           monocoque tub, subframes, crash structures
    suspension_front  wishbones, uprights, pushrods, rockers, coilovers
    suspension_rear   ditto, rear
    wheels            rims, tyres
    brakes            discs, calipers, hubs
    powertrain        engine, intake, exhaust, transaxle, driveshafts
    interior          seats, wheel, dash, console, pedals, door cards
    aero              splitter, diffuser, wing
    hinge             dihedral synchro-helix door mechanism
    details           mirrors, badges, filler, vents, fasteners

The car is one xbf (``XBF/hypercar.xbf``) and one GLB (``XBF/hypercar.glb``),
whose ``doors`` clip is the ``doors`` motion below.
"""

from __future__ import annotations

from cadgen.engine import configuration, couple, glb, motion, xbf

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

# Order here IS the occurrence order.
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


# ---------------------------------------------------------------------------
# Kinematics: the dihedral synchro-helix doors.
#
# One CYLINDRICAL joint per door -- rotation and axial travel about the SAME
# tower axis is exactly what a cylindrical joint is -- on the chassis, paired
# by its label with a rigid joint on the door skin where the door stands shut.
# The ``doors`` coupling drives both sides from one 0..1 control through the
# mechanism's own lead: 62 deg of rotation while sweeping 310 mm along the axis
# (299 up, 80 forward). The numbers come straight from lib/hinge.py, so
# changing the tower changes the joints with it.
#
# Everything that rides the door -- glass, trim, mirror, and the two mechanism
# parts bolted to it -- lives in another system's group, so it does not ride for
# free: each is fastened to the door by a pair of rigid joints of one label,
# which makes it part of the door's link.
# ---------------------------------------------------------------------------

DOOR_RIDERS = [
    "side_glass",
    "door_card_upper",
    "door_card_lower",
    "door_pull",
    "mirror_housing",
    "mirror_bezel",
    "mirror_glass",
    "mirror_stalk",
    "mirror_base",
    "door_bracket",
    "door_lug_lower",
]

SIDES = ("left", "right")


def _sweep(side):
    """The door's signed turn at full open, degrees: the two sides are opposite-hand screws."""
    return hinge_lib.DOOR_SWEEP_DEG * hinge_lib.DOOR_SWEEP_SIGN[side]


def _part(car, label):
    """The one node of the car labelled ``label``."""
    found = [node for node in car.descendants if node.label == label]
    if len(found) != 1:
        raise LookupError(f"{len(found)} nodes of the car are labelled {label!r}")
    return found[0]


def _door_joints(car):
    """Each door's cylindrical joint on the chassis, and its riders fastened to it."""
    tub = _part(car, "chassis")
    for side in SIDES:
        name, sweep = f"door_{side}", _sweep(side)
        frame = bd.Plane(origin=hinge_lib.HELIX_AXIS_ORIGIN[side], z_dir=hinge_lib.HELIX_AXIS_DIR[side])
        bd.CylindricalJoint(name, tub, axis=bd.Axis(frame.origin, frame.z_dir),
                            angular_range=(min(0.0, sweep), max(0.0, sweep)),
                            linear_range=(0.0, hinge_lib.CARRIER_TRAVEL))
        door = _part(car, f"door:{side}")
        bd.RigidJoint(name, door, frame.location)            # shut: as built
        for rider in DOOR_RIDERS:
            fastening = f"{name}_{rider}"
            bd.RigidJoint(fastening, door, frame.location)
            bd.RigidJoint(fastening, _part(car, f"{rider}:{side}"), frame.location)


@xbf(out="../XBF/hypercar.xbf")
@glb(out="../XBF/hypercar.glb")
def hypercar():
    groups = [system() for system in SYSTEMS]
    car = bd.Compound(children=groups, label="mid_engine_hypercar")
    _door_joints(car)
    couple(car, "doors", **{f"door_{side}": (_sweep(side), hinge_lib.CARRIER_TRAVEL) for side in SIDES})
    configuration(car, "open", doors=1)                     # shut is as built
    # Both doors through the full synchro-helix and shut again, 7 s a loop.
    return motion(car, "doors", sweep={"doors": (0, 1)}, duration=3.5, loop="pingpong")
