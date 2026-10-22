"""juno — compact humanoid robotics platform concept.

A sleek research humanoid with Unitree-G1-like proportions: ~1.40 m tall,
athletic stance, exposed cylindrical actuator modules at every joint,
warm-porcelain composite shells over graphite structure with machined
aluminum joint rims, coral accents on repeated functional details, a gloss
midnight-blue sensor visor with cyan pixel-grid eyes, and dexterous
five-digit hands. No logos.

Degrees of freedom (27 body DOF, one build123d revolute joint each):
  - each leg (x2): hip yaw, hip roll, hip pitch, knee pitch,
    ankle pitch, ankle roll                                   -> 12
  - each arm (x2): shoulder pitch, shoulder roll, shoulder yaw,
    elbow pitch, wrist roll, wrist pitch                      -> 12
  - waist yaw                                                 -> 1
  - neck yaw, neck pitch                                      -> 2
  Hands add cosmetic posed finger articulation (not counted).

Coordinates: pelvis waist-yaw joint center = world origin, +X forward,
+Y robot-left, +Z up. Soles rest near z = -876 in the athletic stance.

Chain offsets, travel and pose angles live in lib/chain.py, which
../juno.urdf and ../juno.srdf mirror.

The models of this script:

- `juno` (XBF/juno.xbf): the robot. Its 28 links are placed by their joints,
  connected at the athletic ready stance: that stance is the as-built
  configuration, every joint's value its absolute angle, every range the
  URDF's travel (the ankle pitch's widened for the run and the jump), and
  the SRDF's five group states are configurations. The seven old clips are
  motions (functions of time over the joints, lib/gaits.py), the pelvis
  riding `pelvis_lift`, a vertical slide between the world and the pelvis.
- one model per URDF link (`pelvis` ... `hand_right`): the link alone in its
  part-local frame, whose 3MF ../juno.urdf references (XBF/<link>.3mf).

Every link is made once a build. `links()` is the one `@memo` stage: all 28
links, ~17 s of kernel work, which the robot and each link model call. The
first call in a build runs it (or replays it, when no edit reaches a builder)
and every other call is a new placement of its result.
"""

from __future__ import annotations

from cadgen.engine import configuration, memo, motion, threemf, xbf

import build123d as bd

from lib import chain, gaits
from lib.arms import build_bicep, build_forearm
from lib.hand import build_hand
from lib.head import build_head
from lib.joints import (
    build_ankle_link,
    build_hip_bracket,
    build_hip_carrier,
    build_neck_collar,
    build_shoulder_pod,
    build_wrist_carrier,
    build_yaw_housing,
)
from lib.juno_lib import revolute_attach
from lib.legs import build_foot, build_shin, build_thigh
from lib.pelvis import build_pelvis
from lib.torso import build_torso

# The builder of each sided link, by its URDF name without the side.
SIDED = {
    "hip_bracket": build_hip_bracket,
    "hip_carrier": build_hip_carrier,
    "thigh": build_thigh,
    "shin": build_shin,
    "ankle_link": build_ankle_link,
    "foot": build_foot,
    "shoulder_pod": build_shoulder_pod,
    "yaw_housing": build_yaw_housing,
    "bicep": build_bicep,
    "forearm": build_forearm,
    "wrist_carrier": build_wrist_carrier,
    "hand": build_hand,
}

# Every joint's travel (degrees, absolute), as the URDF has it, but the ankle pitch's:
# the run's heel recovery and the jump's crouch dorsiflex the ankle to -51.6 deg, past
# the URDF's assumed -30 (the old clips played joint values with no limit), so the
# xbf's travel reaches -55.
RANGES = {joint["name"]: joint["range_deg"] for joint in chain.all_joints()}
ANKLE_PITCH_RANGE_DEG = (-55.0, chain.ANKLE_PITCH_RANGE_DEG[1])
for _side in chain.SIDES:
    RANGES[f"ankle_pitch_{_side}"] = ANKLE_PITCH_RANGE_DEG

# The pelvis's one degree of freedom in the world: a vertical slide (mm), held by the
# robot compound, 0 as built. The old clips moved the pelvis (bounce, crouch, dip);
# one joint between the world and the pelvis keeps the vertical part of that.
PELVIS_LIFT = "pelvis_lift"
PELVIS_LIFT_RANGE_MM = (-200.0, 200.0)
# The clips whose pelvis moves vertically: their motions set pelvis_lift.
LIFTED = {"runLoop", "jumpLoop", "kickLoop", "danceLoop"}
# The clips that shifted the pelvis sideways over a planted leg: solved with no shift.
UNSHIFTED = {"kickLoop", "danceLoop"}

X = chain.X_AXIS
Y = chain.Y_AXIS
Z = chain.Z_AXIS
_s = chain.side_sign


@memo
def links():
    """All 28 links, each in its own part-local frame, keyed by its URDF name."""
    made = {
        "pelvis": build_pelvis(),
        "torso": build_torso(),
        "neck_collar": build_neck_collar(),
        "head": build_head(),
    }
    for side in chain.SIDES:
        for part, build in SIDED.items():
            made[f"{part}_{side}"] = build(side)
    return made


def _over_time(name, gait, duration):
    """A clip's function of time: the gait at its phase ``t / duration`` (wrapped, as the
    old clips were), its 27 joint angles and, for the clips that lift the pelvis, the
    pelvis's height on ``pelvis_lift``. The torso extras and the root's lateral shift
    and pitch have no joint (src/README.md, "Motions")."""

    def at(t):
        phase = (t / duration) % 1.0
        angles, _extras, root_offset, _root_pitch = (
            gait(phase, pelvis_dy=0.0) if name in UNSHIFTED else gait(phase))
        values = dict(angles)
        if name in LIFTED:
            values[PELVIS_LIFT] = root_offset[2]
        return values

    return at


def _attach(parent, child, name, origin, axis, xref, angle):
    """The joint ``name`` about ``axis`` at ``origin`` (parent-local), the child's
    own frame on it, connected at ``angle``: its as-built value."""
    revolute_attach(parent, child, name, origin, axis, xref, (0, 0, 0), axis, xref, angle, RANGES[name])


@xbf(out="../XBF/juno.xbf")
def juno():
    link = links()
    for name, shape in link.items():
        shape.label = name

    order = ["pelvis", "torso", "neck_collar", "head"]
    _attach(link["pelvis"], link["torso"], "waist_yaw", chain.WAIST_YAW_ORIGIN_MM, Z, X, chain.WAIST_YAW_DEG)
    _attach(link["torso"], link["neck_collar"], "neck_yaw", chain.NECK_YAW_ORIGIN_MM, Z, X, chain.NECK_YAW_DEG)
    _attach(link["neck_collar"], link["head"], "neck_pitch", chain.NECK_PITCH_ORIGIN_MM, Y, X,
            chain.NECK_PITCH_DEG)

    for side in chain.SIDES:
        s = _s(side)

        def part(name):
            return link[f"{name}_{side}"]

        # ---- leg chain (6 DOF)
        _attach(link["pelvis"], part("hip_bracket"), f"hip_yaw_{side}",
                (0, s * chain.HIP_Y_MM, chain.HIP_YAW_DROP_Z_MM), Z, X, chain.HIP_YAW_DEG)
        _attach(part("hip_bracket"), part("hip_carrier"), f"hip_roll_{side}",
                chain.HIP_ROLL_ORIGIN_MM, X, Y, s * chain.HIP_ROLL_ABDUCT_DEG)
        _attach(part("hip_carrier"), part("thigh"), f"hip_pitch_{side}",
                chain.HIP_PITCH_ORIGIN_MM, Y, X, chain.HIP_PITCH_DEG)
        _attach(part("thigh"), part("shin"), f"knee_{side}", chain.KNEE_ORIGIN_MM, Y, X, chain.KNEE_DEG)
        _attach(part("shin"), part("ankle_link"), f"ankle_pitch_{side}",
                chain.ANKLE_PITCH_ORIGIN_MM, Y, X, chain.ANKLE_PITCH_DEG)
        _attach(part("ankle_link"), part("foot"), f"ankle_roll_{side}",
                chain.ANKLE_ROLL_ORIGIN_MM, X, Y, -s * chain.HIP_ROLL_ABDUCT_DEG)

        # ---- arm chain (6 DOF)
        _attach(link["torso"], part("shoulder_pod"), f"shoulder_pitch_{side}",
                (0, s * chain.SHOULDER_Y_MM, chain.SHOULDER_PITCH_RAISE_Z_MM), Y, X, chain.SHOULDER_PITCH_DEG)
        _attach(part("shoulder_pod"), part("yaw_housing"), f"shoulder_roll_{side}",
                (0, s * chain.SHOULDER_ROLL_Y_MM, chain.SHOULDER_ROLL_Z_MM), X, Y,
                s * chain.SHOULDER_ROLL_ABDUCT_DEG)
        _attach(part("yaw_housing"), part("bicep"), f"shoulder_yaw_{side}",
                chain.SHOULDER_YAW_ORIGIN_MM, Z, X, -s * chain.SHOULDER_YAW_INTERNAL_DEG)
        _attach(part("bicep"), part("forearm"), f"elbow_{side}", chain.ELBOW_ORIGIN_MM, Y, X, chain.ELBOW_DEG)
        _attach(part("forearm"), part("wrist_carrier"), f"wrist_roll_{side}",
                chain.WRIST_ROLL_ORIGIN_MM, Z, X, chain.WRIST_ROLL_DEG)
        _attach(part("wrist_carrier"), part("hand"), f"wrist_pitch_{side}",
                chain.WRIST_PITCH_ORIGIN_MM, Y, X, chain.WRIST_PITCH_DEG)

        order += [f"{name}_{side}" for name in SIDED]

    robot = bd.Compound(children=[link[name] for name in order], label="juno")
    # The robot compound holds the pelvis's slide: the compound is the world (the
    # ground link), the pelvis rides on it, and every other link on the pelvis.
    bd.LinearJoint(PELVIS_LIFT, robot, axis=bd.Axis((0, 0, 0), Z), linear_range=PELVIS_LIFT_RANGE_MM)
    bd.RigidJoint(PELVIS_LIFT, link["pelvis"], bd.Location((0, 0, 0)))
    for name, pose in chain.named_poses_deg().items():
        configuration(robot, name, **pose)
    for name, (gait, duration) in gaits.CLIPS.items():
        motion(robot, name, _over_time(name, gait, duration), duration=duration)
    return robot


# --- the URDF's link meshes: each link alone, in its part-local frame -------------------------


@xbf(out="../XBF/pelvis.xbf")
@threemf(out="../XBF/pelvis.3mf")
def pelvis():
    return links()["pelvis"]


@xbf(out="../XBF/torso.xbf")
@threemf(out="../XBF/torso.3mf")
def torso():
    return links()["torso"]


@xbf(out="../XBF/neck_collar.xbf")
@threemf(out="../XBF/neck_collar.3mf")
def neck_collar():
    return links()["neck_collar"]


@xbf(out="../XBF/head.xbf")
@threemf(out="../XBF/head.3mf")
def head():
    return links()["head"]


@xbf(out="../XBF/hip_bracket_left.xbf")
@threemf(out="../XBF/hip_bracket_left.3mf")
def hip_bracket_left():
    return links()["hip_bracket_left"]


@xbf(out="../XBF/hip_carrier_left.xbf")
@threemf(out="../XBF/hip_carrier_left.3mf")
def hip_carrier_left():
    return links()["hip_carrier_left"]


@xbf(out="../XBF/thigh_left.xbf")
@threemf(out="../XBF/thigh_left.3mf")
def thigh_left():
    return links()["thigh_left"]


@xbf(out="../XBF/shin_left.xbf")
@threemf(out="../XBF/shin_left.3mf")
def shin_left():
    return links()["shin_left"]


@xbf(out="../XBF/ankle_link_left.xbf")
@threemf(out="../XBF/ankle_link_left.3mf")
def ankle_link_left():
    return links()["ankle_link_left"]


@xbf(out="../XBF/foot_left.xbf")
@threemf(out="../XBF/foot_left.3mf")
def foot_left():
    return links()["foot_left"]


@xbf(out="../XBF/shoulder_pod_left.xbf")
@threemf(out="../XBF/shoulder_pod_left.3mf")
def shoulder_pod_left():
    return links()["shoulder_pod_left"]


@xbf(out="../XBF/yaw_housing_left.xbf")
@threemf(out="../XBF/yaw_housing_left.3mf")
def yaw_housing_left():
    return links()["yaw_housing_left"]


@xbf(out="../XBF/bicep_left.xbf")
@threemf(out="../XBF/bicep_left.3mf")
def bicep_left():
    return links()["bicep_left"]


@xbf(out="../XBF/forearm_left.xbf")
@threemf(out="../XBF/forearm_left.3mf")
def forearm_left():
    return links()["forearm_left"]


@xbf(out="../XBF/wrist_carrier_left.xbf")
@threemf(out="../XBF/wrist_carrier_left.3mf")
def wrist_carrier_left():
    return links()["wrist_carrier_left"]


@xbf(out="../XBF/hand_left.xbf")
@threemf(out="../XBF/hand_left.3mf")
def hand_left():
    return links()["hand_left"]


@xbf(out="../XBF/hip_bracket_right.xbf")
@threemf(out="../XBF/hip_bracket_right.3mf")
def hip_bracket_right():
    return links()["hip_bracket_right"]


@xbf(out="../XBF/hip_carrier_right.xbf")
@threemf(out="../XBF/hip_carrier_right.3mf")
def hip_carrier_right():
    return links()["hip_carrier_right"]


@xbf(out="../XBF/thigh_right.xbf")
@threemf(out="../XBF/thigh_right.3mf")
def thigh_right():
    return links()["thigh_right"]


@xbf(out="../XBF/shin_right.xbf")
@threemf(out="../XBF/shin_right.3mf")
def shin_right():
    return links()["shin_right"]


@xbf(out="../XBF/ankle_link_right.xbf")
@threemf(out="../XBF/ankle_link_right.3mf")
def ankle_link_right():
    return links()["ankle_link_right"]


@xbf(out="../XBF/foot_right.xbf")
@threemf(out="../XBF/foot_right.3mf")
def foot_right():
    return links()["foot_right"]


@xbf(out="../XBF/shoulder_pod_right.xbf")
@threemf(out="../XBF/shoulder_pod_right.3mf")
def shoulder_pod_right():
    return links()["shoulder_pod_right"]


@xbf(out="../XBF/yaw_housing_right.xbf")
@threemf(out="../XBF/yaw_housing_right.3mf")
def yaw_housing_right():
    return links()["yaw_housing_right"]


@xbf(out="../XBF/bicep_right.xbf")
@threemf(out="../XBF/bicep_right.3mf")
def bicep_right():
    return links()["bicep_right"]


@xbf(out="../XBF/forearm_right.xbf")
@threemf(out="../XBF/forearm_right.3mf")
def forearm_right():
    return links()["forearm_right"]


@xbf(out="../XBF/wrist_carrier_right.xbf")
@threemf(out="../XBF/wrist_carrier_right.3mf")
def wrist_carrier_right():
    return links()["wrist_carrier_right"]


@xbf(out="../XBF/hand_right.xbf")
@threemf(out="../XBF/hand_right.3mf")
def hand_right():
    return links()["hand_right"]
