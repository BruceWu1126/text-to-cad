"""Full watch assembly: case + dial/hands + movement + bracelet, one document.

Composed by FUNCTION in this one body: the case, the dial and the bracelet are
plain functions (`lib/watch.py`, what `case.py`, `dial.py` and `bracelet.py`
declare), the movement the three `@memo` layers `movement.py` composes. Each
authors its parts in the WATCH frame (see `lib/spec.py`), so placements are
identity -- except the movement, which is cased by the documented flip about
X + MOVT_Z_OFFSET lift.

Articulation is build123d's joints, placed where the parts stand and paired by
label (no `connect_to`, which cannot place a part in a nested assembly), each rigid
half where its moving joint holds it at 0 (`_held`):

- the watch's real degrees of freedom, the old `KINEMATICS` mates: the hands
  on the dial, the going train, escapement and chronograph in the movement,
  the crown (turn and travel) and the two pushers on the case middle, each
  wheel's pinion and each hand's lume and hub fastened by rigid-joint pairs;
- its gear ratios, the old couplings, as `couple`: `running` (seconds of time)
  and `chrono` (seconds of chronograph running), and its poses as
  `configuration`s;
- the joints its exploded clips move: each part or group the old `ANIMATION`
  translated slides on a prismatic joint along its own direction, the
  movement rises on one and flips on a revolute about X, and the movement's
  tiers fan on prismatic joints in its own frame (a train wheel's tier slide
  and spin are one cylindrical joint).

The four old clips are motions, functions of time over those joints, which the
declared GLB bakes into clips: `running` (the escapement's 8 beats a loop),
`reveal`, `showcase` and `grand_tour`. A joint carries one direction, so where
the old showcase moved the face stack sideways and the reveal lifted it, both
lift it now (the showcase higher, clear of the rising movement), and the crown
and pushers, whose joints are their own travel, stay in the case.
"""

import math

import build123d as bd
from cadgen.engine import configuration, couple, glb, motion, xbf

import chrono_works
import keyless_works
import movement_base
from lib import materials as M
from lib import spec as S
from lib import watch


# ---------------------------------------------------------------------------
# Frames
# ---------------------------------------------------------------------------
# The dial and the case are in the WATCH frame: +Z through the crystal, crown at
# +X. The movement's joints are authored in its own frame (bridge side up, see
# `lib/spec.py`) about local -Z, which the casing flip turns to watch +Z, so a
# positive angle is anticlockwise seen dial-up in both and the ratios read as
# the watch's.

_UP = (0.0, 0.0, 1.0)
_DOWN = (0.0, 0.0, -1.0)
_SUB_SECONDS = (-S.SUBDIAL_RADIUS, 0.0, 0.0)    # small seconds at 9 o'clock
_SUB_MINUTES = (S.SUBDIAL_RADIUS, 0.0, 0.0)     # 30-minute recorder at 3
_SUB_HOURS = (0.0, -S.SUBDIAL_RADIUS, 0.0)      # 12-hour recorder at 6
_PUSHER_AXIS_ORIGIN = (0.0, 0.0, S.PUSHER_Z)    # the pushers' axes cross the case axis here
_MOVEMENT_CENTER = (0.0, 0.0, 1.7)              # the cased movement's flip centre (watch frame)


def _local(pos) -> tuple[float, float, float]:
    return (float(pos[0]), float(pos[1]), 0.0)


def _pusher_direction(angle_deg: float) -> tuple[float, float, float]:
    """Inward along a pusher's axis, which sits ``angle_deg`` from +X."""
    radians = math.radians(angle_deg)
    return (-math.cos(radians), -math.sin(radians), 0.0)


# ---------------------------------------------------------------------------
# Finding parts by label
# ---------------------------------------------------------------------------

def _labelled(root) -> dict[str, object]:
    """Every node below ``root`` by its label (each label the watch uses is unique)."""
    found = {}
    stack = [root]
    while stack:
        node = stack.pop()
        if node.label:
            found.setdefault(node.label, node)
        stack.extend(getattr(node, "children", ()) or ())
    return found


class _Parts:
    def __init__(self, root):
        self.by_label = _labelled(root)

    def __getitem__(self, label: str):
        try:
            return self.by_label[label.lstrip("#")]
        except KeyError:
            raise KeyError(f"no part of the watch is labelled {label!r}") from None


def _rides(label: str, carrier, rider) -> None:
    """``rider`` fastened to ``carrier``: a pair of rigid joints of one label where they stand."""
    here = bd.Location()
    bd.RigidJoint(label, carrier, here)
    bd.RigidJoint(label, rider, here)


def _held(base, joint) -> bd.Location:
    """Where the rigid half of ``joint`` (on ``base``) stands at value 0, as the engine reads a
    pair: a revolute's or cylindrical's frame is its axis's; a linear joint slides a frame that
    keeps the part's axes, at the axis's origin."""
    if isinstance(joint, bd.LinearJoint):
        return base.location * bd.Location(joint.relative_axis.position)
    return joint.location


def _group(label: str, parts: list, lead) -> None:
    """Every part of ``parts`` but ``lead`` fastened to it, one rigid pair each."""
    for index, part in enumerate(p for p in parts if p is not lead):
        _rides(f"{label}_{index}", lead, part)


# ---------------------------------------------------------------------------
# The mates: hands, going train, escapement, chronograph, crown and pushers
# ---------------------------------------------------------------------------

_HANDS = (
    # label, hand, axis origin (watch frame), range in degrees
    ("hour", "#hand:hour", (0.0, 0.0, 0.0), (-720.0, 720.0)),
    ("minute", "#hand:minute", (0.0, 0.0, 0.0), (-4320.0, 4320.0)),
    ("chrono_seconds", "#hand:chrono_seconds", (0.0, 0.0, 0.0), (-10800.0, 10800.0)),
    ("sub_seconds", "#hand:sub_seconds", _SUB_SECONDS, (-21600.0, 21600.0)),
    ("chrono_minutes", "#hand:sub_minutes30", _SUB_MINUTES, (-360.0, 360.0)),
    ("chrono_hours", "#hand:sub_hours12", _SUB_HOURS, (-360.0, 360.0)),
)
_HAND_RIDERS = (
    ("hour_lume_rides", "#hand:hour", "#hand:hour_lume"),
    ("hour_hub_rides", "#hand:hour", "#hub:hour"),
    ("minute_lume_rides", "#hand:minute", "#hand:minute_lume"),
    ("minute_hub_rides", "#hand:minute", "#hub:minute"),
    ("chrono_cap_rides", "#hand:chrono_seconds", "#hand:chrono_cap"),
)

# The movement's turning parts that also fan with their tier: one cylindrical joint each,
# its turn about the arbor and its slide along it (local -Z: + is toward the bridges' side
# lifting off after the flip). label, part, position, range, tier slide.
_WHEELS = (
    ("center", "#center_wheel", S.CENTER_WHEEL_POS, (-4320.0, 4320.0)),
    ("third", "#third_wheel", S.THIRD_WHEEL_POS, (-3600.0, 3600.0)),
    ("fourth", "#fourth_wheel", S.FOURTH_WHEEL_POS, (-21600.0, 21600.0)),
    ("escape", "#escape_wheel", S.ESCAPE_WHEEL_POS, (-216000.0, 216000.0)),
    ("pallet", "#pallet_fork", S.PALLET_FORK_POS, (-12.0, 12.0)),
    ("balance", "#balance_wheel", S.BALANCE_POS, (-330.0, 330.0)),
)
_WHEEL_RIDERS = (
    ("center_pinion_rides", "#center_wheel", "#center_pinion"),
    ("third_pinion_rides", "#third_wheel", "#third_pinion"),
    ("fourth_pinion_rides", "#fourth_wheel", "#fourth_pinion"),
    ("escape_pinion_rides", "#escape_wheel", "#escape_pinion"),
    ("pallet_entry_rides", "#pallet_fork", "#pallet_stone:entry"),
    ("pallet_exit_rides", "#pallet_fork", "#pallet_stone:exit"),
    ("pallet_arbor_rides", "#pallet_fork", "#pallet_arbor"),
    ("balance_staff_rides", "#balance_wheel", "#balance_staff"),
    ("impulse_jewel_rides", "#balance_wheel", "#impulse_jewel"),
    *((f"timing_screw_{i}_rides", "#balance_wheel", f"#timing_screw:{i}") for i in range(16)),
)
# The chronograph's wheels turn in the chronograph works, which fan as one tier.
_CHRONO_WHEELS = (
    ("chrono_runner", "#chrono_runner_wheel", S.CENTER_WHEEL_POS, (-10800.0, 10800.0)),
    ("coupling", "#coupling_wheel", S.COUPLING_WHEEL_POS, (-10800.0, 10800.0)),
)
_CHRONO_RIDERS = (
    ("runner_heart_rides", "#chrono_runner_wheel", "#chrono_runner_heart_cam"),
    ("runner_arbor_rides", "#chrono_runner_wheel", "#chrono_runner_arbor"),
)

# The tier slide each turning part takes in the showcase fan (local z, from the old tiers).
_WHEEL_TIER = {"center": -5.0, "third": -5.0, "fourth": -5.0, "escape": -5.0, "pallet": -5.0, "balance": -24.0}


def _hands(parts: _Parts, dial) -> None:
    for label, hand, origin, limits in _HANDS:
        axis = bd.Axis(origin, _UP)
        joint = bd.RevoluteJoint(label, dial, axis=axis, angular_range=limits)
        bd.RigidJoint(label, parts[hand], joint.location)
    for label, carrier, rider in _HAND_RIDERS:
        _rides(label, parts[carrier], parts[rider])


def _train(parts: _Parts, caliber, chrono) -> None:
    for label, wheel, pos, limits in _WHEELS:
        axis = bd.Axis(_local(pos), _DOWN)
        joint = bd.CylindricalJoint(label, caliber, axis=axis, angular_range=limits, linear_range=(0.0, 30.0))
        bd.RigidJoint(label, parts[wheel], joint.location)
    for label, carrier, rider in _WHEEL_RIDERS:
        _rides(label, parts[carrier], parts[rider])
    for label, wheel, pos, limits in _CHRONO_WHEELS:
        axis = bd.Axis(_local(pos), _DOWN)
        joint = bd.RevoluteJoint(label, chrono, axis=axis, angular_range=limits)
        bd.RigidJoint(label, parts[wheel], joint.location)
    for label, carrier, rider in _CHRONO_RIDERS:
        _rides(label, parts[carrier], parts[rider])


def _crown_and_pushers(parts: _Parts) -> None:
    middle = parts["#case_middle"]
    # Winding turns the crown; setting pulls it out: a cylindrical joint, turn then travel.
    crown = bd.Axis((0.0, 0.0, S.CROWN_Z), (1.0, 0.0, 0.0))
    joint = bd.CylindricalJoint("crown", middle, axis=crown, angular_range=(-3600.0, 3600.0), linear_range=(0.0, 1.6))
    bd.RigidJoint("crown", parts["#crown"], joint.location)
    for label, cap, angle in (("pusher_start", "#pusher_cap:2oclock", S.PUSHER_ANGLES[0]),
                              ("pusher_reset", "#pusher_cap:4oclock", S.PUSHER_ANGLES[1])):
        axis = bd.Axis(_PUSHER_AXIS_ORIGIN, _pusher_direction(angle))
        joint = bd.LinearJoint(label, middle, axis=axis, linear_range=(0.0, 1.1))
        bd.RigidJoint(label, parts[cap], _held(middle, joint))


# ---------------------------------------------------------------------------
# The exploded clips' joints
# ---------------------------------------------------------------------------
# Each part or group the old clips translated slides on a prismatic joint from the
# case middle along its one direction (label, target, direction).
_LIFTS = (
    ("lift_caseback_o_ring", "#caseback_o_ring", _DOWN),
    ("lift_caseback_retaining_ring", "#caseback_retaining_ring", _DOWN),
    ("lift_caseback", "#caseback", _DOWN),
    ("lift_caseback_sapphire", "#caseback_sapphire", _DOWN),
    ("lift_movement_ring", "#movement_ring", _DOWN),
    ("lift_bezel_ring", "#bezel_ring", _UP),
    ("lift_bezel_polish", "#case_polish:bezel", _UP),
    ("lift_bezel_insert", "#bezel_insert", _UP),
    ("lift_tachymeter_scale", "#tachymeter_scale", _UP),
    ("lift_crystal", "#crystal", _UP),
    ("lift_crystal_gasket", "#crystal_gasket", _UP),
    ("lift_dial", "#dial_and_hands", _UP),
    ("lift_strap_12", "#strap_12", (0.0, 1.0, 0.0)),
    ("lift_strap_6", "#strap_6", (0.0, -1.0, 0.0)),
    ("lift_clasp", "#clasp", (0.0, -1.0, 0.0)),
    ("lift_spring_bar_12", "#spring_bar:12", (0.0, 1.0, 0.0)),
    ("lift_spring_bar_6", "#spring_bar:6", (0.0, -1.0, 0.0)),
)

# Movement tiers, as the old showcase fanned them (local z; negative is the bridge side,
# which the flip turns upward): each group slides on one prismatic joint, its first part
# the lead and the rest fastened to it. The turning parts fan on their own joints
# (`_WHEEL_TIER`); a fastened rider goes with its wheel.
_PALLET_BRIDGE = ("#pallet_bridge", "#pallet_bridge:stripe_shadow:0", "#pallet_bridge:stripe_shadow:1",
                  "#pallet_bridge:anglage", "#bridge_jewel:pallet", "#chaton:pallet", "#screw:pallet_bridge")
_TRAIN_BRIDGE = (
    "#train_bridge", *(f"#train_bridge:stripe_shadow:{i}" for i in range(3)),
    *(f"#train_bridge:anglage:{i}" for i in range(2)),
    *(f"#{kind}:{wheel}" for wheel in ("center", "third", "fourth", "escape") for kind in ("bridge_jewel", "chaton")),
    "#screw:train_bridge:foot", "#screw:train_bridge:fourth", "#screw:train_bridge:center",
)
_BARREL_BRIDGE = (
    "#barrel_bridge", *(f"#barrel_bridge:stripe_shadow:{i}" for i in range(6)),
    *(f"#barrel_bridge:anglage:{i}" for i in range(5)), "#bridge_jewel:barrel",
    "#screw:barrel_bridge:left", "#screw:barrel_bridge:right", "#screw:barrel_bridge:top",
)
_TIERS = (
    ("tier_keyless", ("#keyless_works",), 7.0),
    ("tier_barrel", ("#barrel_drum", "#barrel_lid", "#mainspring", "#barrel_arbor"), -8.0),
    ("tier_pallet_bridge", _PALLET_BRIDGE, -11.5),
    ("tier_train_bridge", _TRAIN_BRIDGE, -15.5),
    ("tier_barrel_bridge", _BARREL_BRIDGE, -15.5),
    ("tier_winding", ("#ratchet_wheel", "#screw:ratchet", "#crown_wheel", "#crown_wheel_core", "#screw:crown_wheel",
                      "#click", "#screw:click", "#click_spring", "#screw:click_spring"), -19.0),
    ("tier_balance_cock", ("#balance_cock", "#hairspring", "#hairspring_stud", "#balance_cock:stripe_shadow:0",
                           "#balance_cock:stripe_shadow:1", "#screw:balance_cock", "#stud_holder",
                           "#screw:stud_holder", "#shock_bezel", "#shock_chaton", "#shock_cap_jewel",
                           "#shock_lyre_spring"), -24.0),
    ("tier_chrono", ("#chronograph_works",), -30.0),
)


def _lifts(parts: _Parts) -> None:
    middle = parts["#case_middle"]
    for label, target, direction in _LIFTS:
        axis = bd.Axis((0.0, 0.0, 0.0), direction)
        joint = bd.LinearJoint(label, middle, axis=axis, linear_range=(0.0, 120.0))
        bd.RigidJoint(label, parts[target], _held(middle, joint))


def _tiers(parts: _Parts, caliber) -> None:
    for label, targets, _dz in _TIERS:
        group = [parts[target] for target in targets]
        axis = bd.Axis((0.0, 0.0, 0.0), _UP)
        joint = bd.LinearJoint(label, caliber, axis=axis, linear_range=(-40.0, 40.0))
        bd.RigidJoint(label, group[0], _held(caliber, joint))
        _group(f"{label}_rides", group, group[0])


# ---------------------------------------------------------------------------
# Gear ratios and poses
# ---------------------------------------------------------------------------
# Gear trains are ratio arithmetic. `running` is SECONDS of elapsed time; `chrono` is
# SECONDS of chronograph running (the clutch engaged).
#
#   18,000 vph / 2 vibrations per tooth / 15 teeth = 600 escape rev/h = 60 deg/s
#   fourth wheel  1 rev/min  =   6 deg/s (carries the small seconds)
#   third wheel   8 rev/h    = 0.8 deg/s (conventional 2310-family ratio)
#   center wheel  1 rev/h    = 0.1 deg/s (the minute arbor)
# Hands run CLOCKWISE seen dial-up, i.e. negative about watch +Z; meshing wheels
# alternate sense. A cylindrical joint's ratio is its turn's, its slide's 0.

def _couplings_and_poses(watch_root) -> None:
    couple(watch_root, "running", range=(0.0, 3600.0),
           minute=-0.1, hour=-1.0 / 120.0, sub_seconds=-6.0,
           center=(-0.1, 0.0), third=(0.8, 0.0), fourth=(-6.0, 0.0), escape=(60.0, 0.0))
    couple(watch_root, "chrono", range=(0.0, 1800.0),
           chrono_seconds=-6.0, chrono_runner=-6.0, chrono_minutes=-0.2, chrono_hours=-1.0 / 120.0, coupling=6.0)
    # ZERO IS THE ARTIFACT AS WRITTEN: the watch as built reads 10:09:38 with the
    # chronograph reset, so every configuration is a departure from that.
    configuration(watch_root, "one_minute", running=60.0)
    configuration(watch_root, "half_hour", running=1800.0)
    configuration(watch_root, "chrono_at_10min", chrono=600.0)
    configuration(watch_root, "start_pressed", pusher_start=1.0)
    configuration(watch_root, "reset_pressed", pusher_reset=1.0)
    configuration(watch_root, "winding", crown=(1080.0, 0.0))
    configuration(watch_root, "setting", crown=(180.0, 1.6))


# ---------------------------------------------------------------------------
# The clips: motions of time over the joints
# ---------------------------------------------------------------------------
#   running    -- one seamless escapement loop, slow-motion macro pacing: 4 balance
#                 oscillations, 8 beats. The escape wheel advances one half-tooth-pitch
#                 step per beat (8 x 12 deg = 96 deg = exactly 4 tooth pitches of the
#                 15-tooth wheel, so the loop is seamless), the pallet fork snaps between
#                 banking positions at each beat, the fourth/third wheels creep
#                 spoke-symmetric increments, and the chronograph runs.
#   reveal     -- staged partial explode: the caseback stack fans downward, the movement
#                 ring drops clear, the crystal and bezel stack lift off, the dial floats
#                 up, the bracelet straps slide apart, and the movement rises into the
#                 case mouth and flips bridge-side-up.
#   showcase   -- full explode: the face stack lifts high, the caseback stack and bracelet
#                 spread away, the movement rises up the middle, flips, and its tiers fan
#                 open; the second half mirrors the first, so the loop closes.
#   grand_tour -- reveal opens and dwells with the escapement running, then the movement's
#                 tiers fan open while the floating face stack lifts for headroom;
#                 everything mirrors closed again.

_OSCILLATIONS = 4                                    # balance swings per loop
_BEATS = _OSCILLATIONS * 2                           # escape steps per loop
_BALANCE_AMPLITUDE_DEG = 75.0
_ESCAPE_STEP_DEG = 360.0 / S.ESCAPE_WHEEL_TEETH / 2  # half a tooth pitch: 12 deg
_PALLET_BANK_DEG = 8.0
_STEP_FRACTION = 0.25                                # fraction of a beat spent mid-snap


def _smooth(t: float) -> float:
    x = min(1.0, max(0.0, t))
    return x * x * (3.0 - 2.0 * x)


def _stage(t: float, a: float, b: float) -> float:
    """Staged smoothstep: 0 before a, 1 after b."""
    return _smooth((t - a) / (b - a))


def _snap(fraction: float) -> float:
    """Quick snap easing inside one beat: completes in _STEP_FRACTION of it."""
    return _smooth(fraction / _STEP_FRACTION)


# Staged reveal: (joint, distance, start, end). The bezel stack and crystal lift clear
# first, then the dial floats up so the face (hands running) and the movement (flipped
# bridge-side-up in the case mouth, escapement beating) are BOTH visible.
_REVEAL_MOVES = (
    ("lift_caseback_o_ring", 9.0, 0.0, 0.35),
    ("lift_caseback_retaining_ring", 13.0, 0.03, 0.38),
    ("lift_caseback", 18.0, 0.06, 0.42),
    ("lift_caseback_sapphire", 24.0, 0.09, 0.45),
    ("lift_movement_ring", 5.5, 0.0, 0.3),
    ("lift_bezel_ring", 34.0, 0.05, 0.45),
    ("lift_bezel_polish", 34.0, 0.05, 0.45),
    ("lift_bezel_insert", 37.0, 0.05, 0.45),
    ("lift_tachymeter_scale", 40.0, 0.05, 0.45),
    ("lift_crystal", 48.0, 0.05, 0.45),
    ("lift_crystal_gasket", 43.0, 0.05, 0.45),
    ("lift_dial", 26.0, 0.25, 0.6),
    ("lift_strap_12", 9.0, 0.1, 0.5),
    ("lift_strap_6", 9.0, 0.1, 0.5),
    ("lift_clasp", 9.0, 0.1, 0.5),
)
_REVEAL_RISE = 16.0

# Grand tour: extra face-stack lift while the tiers are open.
_TOUR_HEADROOM = 32.0
_TOUR_HEADROOM_JOINTS = ("lift_dial", "lift_crystal", "lift_crystal_gasket", "lift_bezel_ring",
                         "lift_bezel_polish", "lift_bezel_insert", "lift_tachymeter_scale")

# Showcase: the face stack lifts high, clear of the movement rising up the middle (the old
# clip fanned it sideways), and the caseback stack and bracelet spread away.
_SHOWCASE_LIFTS = (
    ("lift_dial", 46.0), ("lift_bezel_ring", 54.0), ("lift_bezel_polish", 54.0), ("lift_bezel_insert", 57.0),
    ("lift_tachymeter_scale", 60.0), ("lift_crystal", 66.0), ("lift_crystal_gasket", 63.0),
)
_SHOWCASE_SPREADS = (
    ("lift_movement_ring", 7.0), ("lift_caseback_o_ring", 12.0), ("lift_caseback_retaining_ring", 17.0),
    ("lift_caseback", 23.0), ("lift_caseback_sapphire", 30.0), ("lift_spring_bar_12", 4.0),
    ("lift_spring_bar_6", 4.0), ("lift_strap_12", 14.0), ("lift_strap_6", 14.0), ("lift_clasp", 21.0),
)
_MOVEMENT_RISE = 30.0
_CLIP_JOINTS = (*(label for label, _target, _direction in _LIFTS), *(label for label, _targets, _dz in _TIERS),
                "movement_rise", "movement_flip")


def _reveal_stages(r: float) -> tuple[float, float]:
    """(rise, flip) of the movement at reveal openness r."""
    return _stage(r, 0.45, 0.85), _stage(r, 0.55, 0.92)


def _tour_stages(t: float) -> tuple[float, float]:
    """(r, f) at tour progress t: r the reveal openness (a trapezoid, open by 0.20 and
    closing from 0.83), f the tier fan (open 0.45..0.55, closed again by 0.83)."""
    return (
        min(_stage(t, 0.0, 0.2), _stage(1 - t, 0.0, 0.17)),
        min(_stage(t, 0.45, 0.55), _stage(1 - t, 0.17, 0.25)),
    )


def _showcase_stages(s: float) -> tuple[float, float, float, float, float]:
    """(lift, spread, rise, flip, fan) at showcase progress s, keyed on min(s, 1 - s) so
    the reassembly half mirrors the expansion half and the loop is seamless."""
    u = min(s, 1 - s)
    return (
        _stage(u, 0.02, 0.17),
        _stage(u, 0.05, 0.25),
        _stage(u, 0.17, 0.34),
        _stage(u, 0.3, 0.38),
        _stage(u, 0.36, 0.42),
    )


def _choreograph(*, run: float = 0.0, reveal: float = 0.0, showcase: float = 0.0, tour: float = 0.0) -> dict:
    """The watch's joint values on its four timelines: ``reveal``, ``showcase`` and ``tour``
    run 0..1, and ``run`` counts escapement loops."""
    s = min(1.0, max(0.0, showcase))
    tour_open, tour_fan = _tour_stages(min(1.0, max(0.0, tour)))
    rv = max(reveal, tour_open)          # the tour reuses the reveal for its open and close
    run = run + 2 * tour                 # three escapement loops per tour, counting on
    lift, spread, rise, flip, fan = _showcase_stages(s)
    fan = max(fan, tour_fan)

    # A function of time sets the same joints at every time: every clip joint, at rest 0.
    values: dict[str, float] = {joint: 0.0 for joint in _CLIP_JOINTS}

    def add(joint: str, amount: float) -> None:
        values[joint] += amount

    for joint, distance, a, b in _REVEAL_MOVES:
        add(joint, distance * _stage(rv, a, b))
    if s > 0:
        for joint, distance in _SHOWCASE_LIFTS:
            add(joint, distance * lift)
        for joint, distance in _SHOWCASE_SPREADS:
            add(joint, distance * spread)
    if tour_fan > 0:
        for joint in _TOUR_HEADROOM_JOINTS:
            add(joint, _TOUR_HEADROOM * tour_fan)
    for joint, _targets, dz in _TIERS:
        add(joint, dz * fan)

    # The movement rises out of the case and flips in place about its own centre.
    reveal_rise, reveal_flip = _reveal_stages(rv)
    add("movement_rise", _MOVEMENT_RISE * rise + _REVEAL_RISE * reveal_rise)
    add("movement_flip", 180.0 * min(1.0, flip + reveal_flip))

    # The escapement: the balance swings sinusoidally, beats on its zero crossings.
    beats = run * _BEATS
    beat = math.floor(beats)
    fraction = beats - beat
    turns = {
        "balance": _BALANCE_AMPLITUDE_DEG * math.sin(math.tau * _OSCILLATIONS * run),
        # the pallet fork snaps between banking positions once per beat
        "pallet": (_PALLET_BANK_DEG if beat % 2 == 0 else -_PALLET_BANK_DEG) * (1 - 2 * _snap(fraction)),
        # the escape wheel: one crisp half-tooth step per beat, released as the pallet snaps
        "escape": _ESCAPE_STEP_DEG * (beat + _snap(fraction)),
        # the going train creeps, spoke-and-tooth-symmetric 144 deg a loop
        "fourth": -144.0 * run,
        "third": 144.0 * run,
        "center": 0.0,
    }
    for joint, angle in turns.items():
        values[joint] = (angle, -_WHEEL_TIER[joint] * fan)   # turn, then the tier's slide
    # The small seconds ride the fourth wheel; the chronograph runs, one turn a loop.
    values["sub_seconds"] = -144.0 * run
    values["chrono_runner"] = -360.0 * run
    values["chrono_seconds"] = -360.0 * run
    values["coupling"] = 360.0 * run
    return values


def _running(t: float) -> dict:
    return _choreograph(run=t / 6)


def _reveal(t: float) -> dict:
    progress = t / 12
    # A trapezoid: open, hold with the face and escapement both running in view, close.
    if progress < 0.35:
        r = _smooth(progress / 0.35)
    elif progress < 0.65:
        r = 1.0
    else:
        r = _smooth((1 - progress) / 0.35)
    return _choreograph(reveal=r, run=progress)


def _showcase(t: float) -> dict:
    progress = t / 12
    return _choreograph(showcase=progress, run=progress)


def _grand_tour(t: float) -> dict:
    progress = t / 24
    return _choreograph(tour=progress, run=progress)


# ---------------------------------------------------------------------------
# The watch
# ---------------------------------------------------------------------------

def _movement():
    """The cased movement: its three layers (the `@memo` layers `movement.py` composes) in
    `caliber`, flipped about X and lifted into the case, inside `movement`, which rises."""
    base = movement_base.movement_base()
    base.label = "movement_base"
    keyless = keyless_works.keyless_works()
    keyless.label = "keyless_works"
    chrono = chrono_works.chrono_works()
    chrono.label = "chronograph_works"
    return bd.Compound(children=[base, keyless, chrono], label="caliber"), chrono


def _movement_ring():
    """Fills the annulus between the movement OD and the case interior, so the caseback window
    shows metal, not void. The cut over/under-shoots to avoid coplanar-face booleans; the
    cased movement spans watch z 0.96..7.7."""
    ring = bd.Pos(0, 0, 1.0) * bd.Cylinder(15.6, 6.7, align=(None, None, None)) - bd.Pos(0, 0, 0.9) * bd.Cylinder(
        S.MOVEMENT_DIAMETER / 2 + 0.05, 7.0, align=(None, None, None))
    ring.color = bd.Color(*S.STEEL_DARK)
    ring.label = "movement_ring"
    ring.material = M.FINISHES["satin_steel"]
    return ring


@xbf(out="../XBF/moonwatch.xbf")
@glb(out="../XBF/moonwatch.glb")
def moonwatch():
    case = watch.case()

    dial = watch.dial()
    dial.label = "dial_and_hands"

    bracelet = watch.bracelet()

    caliber, chrono = _movement()
    # The movement's own joints, authored in its frame before it is cased.
    caliber_parts = _Parts(caliber)
    _train(caliber_parts, caliber, chrono)
    _tiers(caliber_parts, caliber)
    # cased: local (x, y, z) -> watch (x, -y, MOVT_Z_OFFSET - z)
    caliber.location = bd.Location((0, 0, S.MOVT_Z_OFFSET), (1, 0, 0), 180)
    movement = bd.Compound(children=[caliber], label="movement")
    flip = bd.Axis(_MOVEMENT_CENTER, (1.0, 0.0, 0.0))
    joint = bd.RevoluteJoint("movement_flip", movement, axis=flip, angular_range=(0.0, 180.0))
    bd.RigidJoint("movement_flip", caliber, joint.location)

    root = bd.Compound(children=[case, dial, bracelet, movement, _movement_ring()], label="moonwatch")
    parts = _Parts(root)
    _hands(parts, dial)
    _crown_and_pushers(parts)
    _lifts(parts)
    rise = bd.Axis((0.0, 0.0, 0.0), _UP)
    joint = bd.LinearJoint("movement_rise", parts["#case_middle"], axis=rise, linear_range=(0.0, 60.0))
    bd.RigidJoint("movement_rise", movement, _held(parts["#case_middle"], joint))

    _couplings_and_poses(root)
    motion(root, "running", _running, duration=6, loop="loop")
    motion(root, "reveal", _reveal, duration=12, loop="loop")
    motion(root, "showcase", _showcase, duration=12, loop="loop")
    motion(root, "grand_tour", _grand_tour, duration=24, loop="loop")
    return root


if __name__ == "__main__":
    moonwatch()
