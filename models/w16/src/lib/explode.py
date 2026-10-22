"""W16 explode: the joints the old `explode` and `running_reveal` clips move, and both motions.

The original's choreography (`lib/animgen.py`'s `ANIMATION_JS`, embedded in `w16.py`) moved
whole systems and per-bank sets of parts by straight, eased translations while the crank
kept turning. Here every one of those moves is a prismatic joint along the old direction,
standing at 0 as built, and both clips are motions of time that set them on top of the
`crank` cycle (`kinematics.pose`), the engine turning throughout:

- `floor_lift`, held by the engine's root (the world): the block's group, and with it the
  whole engine, rises 1,150 mm at the start of either clip so nothing that separates
  downward sinks below where the engine stood (the old floor lift).
- On the block's group: the ancillaries (down), the oil system (down), the induction (up),
  the cam drive (forward along X) and the crank system (down), each its system's group;
  per bank, the cam covers and the heads (out along the bank's centreline), the exhaust
  (outboard and down), the turbos with their downpipes (outboard and down), the cam caps,
  their bolts and the valve train's guides, spring cups and lash adjusters (out along the
  bank's centreline): each set a first part on the joint and the rest fastened to it.
- On the crank system's group: the main bearing shells, caps and bolts drop below the
  crank as it drops.
- The pistons leave their bores on their own joints (`piston_N`, PISTON_OUT past the stroke).

What turns rides what the old clips carried it with (`lib/kinematics.py`): the cams their
caps, the valves their guides, the followers their lash adjusters, the sprockets the cam
drive, the oil-pump drive the oil system, the pulleys the ancillaries, the crankshaft its
system. A joint carries one direction and one part rides one link, so three moves are the
nearest faithful ones (`src/README.md`, "Not ported"): the cams go out with their caps
(1,000 mm, not 950), the followers with the valve train (750 mm, not 850), and in
`running_reveal` the turbos leave along the explode's line (449 mm, 2 mm off the old end).
The old reveal also faded the crankcase casting and its face skins to 8 % opacity; a motion
cannot hide a part, so the block stays as it is.
"""

from __future__ import annotations

import math

import build123d as bd

from lib import kin, kinematics as K, spec as S
from lib.valvetrain import valve_tag

EXPLODE_SECONDS = 12.0              # the staged explode, 0..1, two crank cycles
REVEAL_SECONDS = 24.0               # off, held, back on: four crank cycles, so it loops exactly
REVEAL_OUT, REVEAL_HOLD = 8.0, 16.0
FLOOR_LIFT = 1150.0

_R = math.sqrt(0.5)
BANK_UP = {1: (0.0, _R, _R), 2: (0.0, -_R, _R)}          # each bank's centreline, out of the vee
EXHAUST_OUT = {1: (0.0, _R, -_R), 2: (0.0, -_R, -_R)}    # along each head's exhaust face, outboard and down
# The turbos leave outboard and down: the explode's vector (the reveal's, (+-160, -420), is 0.4 deg off it).
_TURBO = {1: (0.0, 150.0, -400.0), 2: (0.0, -150.0, -400.0)}
TURBO_EXPLODE = math.hypot(150.0, 400.0)
TURBO_REVEAL = math.hypot(160.0, 420.0)
_DOWN, _UP, _FORWARD = (0.0, 0.0, -1.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)

# Whole systems, each its group on a joint held by the block's group: label, system,
# direction, the farthest either clip takes it.
_SYSTEMS = (
    ("explode_ancillaries", "ancillaries", _DOWN, 700.0),
    ("explode_oil_system", "oil_system", _DOWN, 560.0),
    ("explode_induction", "induction", _UP, 520.0),
    ("explode_camdrive", "camdrive", _FORWARD, 300.0),
    ("explode_crank", "crank", _DOWN, 420.0),
)
# The bottom end's bearing parts, below the crank as it drops (in the crank system's group).
_MAINS = (
    ("explode_main_shells_lower", tuple(f"main_shell:{k}_lower" for k in range(1, 6)), 470.0),
    ("explode_main_shells_upper", tuple(f"main_shell:{k}_upper" for k in range(1, 6)), 80.0),
    ("explode_main_caps", tuple(f"main_cap:{k}" for k in range(2, 6)), 520.0),
    ("explode_main_bolts", tuple(f"main_bolt:{k}_{side}" for k in range(2, 6) for side in ("l", "r")), 600.0),
)
_TURBO_ROTOR = ("turbine_wheel:", "compressor_wheel:", "compressor_shaft_nut:")


def _unit(v):
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v)


def _bank_of_label(label: str) -> int | None:
    """The bank a part of the heads, covers, turbos or exhaust belongs to, by its label: a
    tag `1` or `2`, or one starting `1_` or `2_` (`head:2`, `turbine_wheel:1_rear`,
    `head_bolt:2_7`, `cam_cover_bolt:2_inner_3`), names its bank; any other number is a
    cylinder's (1-8 bank 1, 9-16 bank 2). The original's rule took a bank from `B_...`
    only before front, rear, intake or exhaust, so it sent bank 2's head bolts, head face
    skins and cam-cover bolts out along bank 1's centreline: they go with their bank now."""
    tag = label.split(":", 1)[1] if ":" in label else ""
    head = tag.split("_")[0]
    if head in ("1", "2"):
        return int(head)
    if head.isdigit():
        return S.bank_of(int(head))
    return None


def _leaves(group) -> list:
    return [node for node in group.descendants if not node.children]


def explode_sets(systems) -> dict[str, list]:
    """{heads_B, covers_B, turbos_B, exhaust_B}: each system's parts by bank, the downpipes
    riding with their turbo (the original's `EXPL` table)."""
    found: dict[str, list] = {f"{name}_{bank}": [] for name in ("heads", "covers", "turbos", "exhaust")
                              for bank in (1, 2)}
    for name in ("heads", "covers", "turbos", "exhaust"):
        for part in _leaves(systems[name]):
            bank = _bank_of_label(part.label)
            if bank is None:
                raise LookupError(f"cannot place {part.label!r} ({name}) on a bank")
            target = "turbos" if name == "exhaust" and part.label.split(":")[0] in ("downpipe", "downpipe_vband") \
                else name
            found[f"{target}_{bank}"].append(part)
    return found


def _slide(label: str, holder, moved, direction, reach: float) -> None:
    """``moved`` slides along ``direction`` on ``holder``, 0 as built, as far as ``reach``.
    Every group stands at the identity and every part in its group, so the rigid half,
    a pure translation at the axis's origin, is the identity in the part's group."""
    bd.LinearJoint(label, holder, axis=bd.Axis((0.0, 0.0, 0.0), direction), linear_range=(0.0, reach))
    bd.RigidJoint(label, moved, bd.Location())


def _group(label: str, holder, parts: list, direction, reach: float) -> None:
    """``parts`` slide as one: the first on a joint, every other fastened to it."""
    lead, *rest = parts
    _slide(label, holder, lead, direction, reach)
    for at, part in enumerate(rest):
        here = bd.Location()
        bd.RigidJoint(f"{label}_rides_{at}", lead, here)
        bd.RigidJoint(f"{label}_rides_{at}", part, here)


def author(engine) -> None:
    """The explode's joints on the engine's groups and parts (after `kinematics.author_joints`)."""
    systems = K.systems_of(engine)
    by_label = {part.label: part for part in _leaves(engine)}
    block, crank = systems["block"], systems["crank"]

    def parts(*labels: str) -> list:
        return [by_label[label] for label in labels if label in by_label]

    _slide("floor_lift", engine, block, _UP, FLOOR_LIFT)
    for label, system, direction, reach in _SYSTEMS:
        _slide(label, block, systems[system], direction, reach)
    sets = explode_sets(systems)
    for bank in (1, 2):
        up = BANK_UP[bank]
        heads = sets[f"heads_{bank}"]
        heads.sort(key=lambda part: part.label != f"head:{bank}")          # the casting leads
        _group(f"explode_heads_{bank}", block, heads, up, 560.0)
        _group(f"explode_covers_{bank}", block, sets[f"covers_{bank}"], up, 1200.0)
        _group(f"explode_exhaust_{bank}", block, sets[f"exhaust_{bank}"], EXHAUST_OUT[bank], 300.0)
        # the rotors turn on their centre housings, which the set carries
        statics = [part for part in sets[f"turbos_{bank}"] if not part.label.startswith(_TURBO_ROTOR)]
        _group(f"explode_turbos_{bank}", block, statics, _unit(_TURBO[bank]), TURBO_REVEAL)
        kinds = ("intake", "exhaust")
        _group(f"explode_cam_caps_{bank}", block,
               parts(*(f"cam_cap:{bank}_{kind}_{k}" for kind in kinds for k in range(1, 6))), up, 1000.0)
        _group(f"explode_cam_bolts_{bank}", block,
               parts(*(f"cam_cap_bolt:{bank}_{kind}_{k}_{side}" for kind in kinds for k in range(1, 6)
                       for side in ("a", "b"))), up, 1050.0)
        tags = [valve_tag(g) for g in kin.all_valves() if S.bank_of(g.cyl) == bank]
        _group(f"explode_valvetrain_{bank}", block,
               parts(*(f"{kind}:{tag}" for kind in ("valve_guide", "spring_cup", "lash_adjuster") for tag in tags)),
               up, 750.0)
    for label, labels, reach in _MAINS:
        _group(label, crank, parts(*labels), _DOWN, reach)


# --- the choreography ---------------------------------------------------------------------

def _smooth(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3.0 - 2.0 * x)


def _stage(p: float, a: float, b: float) -> float:
    """Eased 0 before ``a``, 1 after ``b``."""
    return _smooth((p - a) / (b - a))


def _at_rest() -> dict[str, float]:
    """Every explode joint, as built: a motion sets the same joints at every time."""
    values = {"floor_lift": 0.0}
    values.update({label: 0.0 for label, *_rest in _SYSTEMS})
    values.update({label: 0.0 for label, *_rest in _MAINS})
    for bank in (1, 2):
        for name in ("heads", "covers", "exhaust", "turbos", "cam_caps", "cam_bolts", "valvetrain"):
            values[f"explode_{name}_{bank}"] = 0.0
    return values


def explode_values(p: float) -> dict[str, float]:
    """The staged explode at progress ``p`` (0..1), the original's ``explode(m, p)``:
    the floor lift; the ancillaries and oil system down (0.02-0.16); the induction up, the
    cam drive forward, the covers, exhaust and turbos off (0.14-0.30); the heads, cams and
    valve train out along bank 1's centreline (0.33-0.50), then bank 2's (0.48-0.66); the
    pistons out of their bores (`piston_out`); the crank down with its bearings below it
    (0.84-1.0)."""
    values = _at_rest()
    values["floor_lift"] = FLOOR_LIFT * _stage(p, 0.0, 0.16)
    low = _stage(p, 0.02, 0.16)
    values["explode_ancillaries"] = 700.0 * low
    values["explode_oil_system"] = 550.0 * low
    outer = _stage(p, 0.14, 0.30)
    values["explode_induction"] = 500.0 * outer
    values["explode_camdrive"] = 260.0 * outer
    for bank in (1, 2):
        values[f"explode_covers_{bank}"] = 1200.0 * outer
        values[f"explode_exhaust_{bank}"] = 280.0 * outer
        values[f"explode_turbos_{bank}"] = TURBO_EXPLODE * outer
        top = _stage(p, 0.33, 0.5) if bank == 1 else _stage(p, 0.48, 0.66)
        values[f"explode_heads_{bank}"] = 420.0 * top
        values[f"explode_valvetrain_{bank}"] = 750.0 * top
        values[f"explode_cam_caps_{bank}"] = 1000.0 * top
        values[f"explode_cam_bolts_{bank}"] = 1050.0 * top
    bottom = _stage(p, 0.84, 1.0)
    values["explode_crank"] = 420.0 * bottom
    for label, _labels, reach in _MAINS:
        values[label] = reach * bottom
    return values


def piston_out(p: float) -> float:
    """How far the explode has drawn every piston out of its bore at ``p`` (0.68-0.85)."""
    return K.PISTON_OUT * _stage(p, 0.68, 0.85)


def reveal_values(p: float) -> dict[str, float]:
    """The running cutaway at progress ``p`` (0..1), the original's ``reveal(m, p)``: the
    floor lift, everything outboard of the working parts clear (0.02-0.30), the heads lifted
    off (0.26-0.55), and nothing that moves displaced: the cams, valves and followers keep
    running where they stand."""
    values = _at_rest()
    values["floor_lift"] = FLOOR_LIFT * _stage(p, 0.0, 0.16)
    clear = _stage(p, 0.02, 0.30)
    values["explode_ancillaries"] = 700.0 * clear
    values["explode_oil_system"] = 560.0 * clear
    values["explode_induction"] = 520.0 * clear
    values["explode_camdrive"] = 300.0 * clear
    lift = _stage(p, 0.26, 0.55)
    for bank in (1, 2):
        values[f"explode_covers_{bank}"] = 1200.0 * clear
        values[f"explode_exhaust_{bank}"] = 300.0 * clear
        values[f"explode_turbos_{bank}"] = TURBO_REVEAL * clear
        values[f"explode_heads_{bank}"] = 560.0 * lift
    return values


def reveal_progress(t: float) -> float:
    """Apart over 0..8 s, held to 16 s, back by 24 s: 0 at both ends, so the clip loops."""
    if t <= REVEAL_OUT:
        return _smooth(t / REVEAL_OUT)
    if t <= REVEAL_HOLD:
        return 1.0
    return _smooth((REVEAL_SECONDS - t) / (REVEAL_SECONDS - REVEAL_HOLD))


def _theta(t: float) -> float:
    """The crank never stops: 720 deg every CRANK_SECONDS, run on (no wrap, so every joint's
    value is continuous; a whole number of cycles per clip)."""
    return 720.0 * t / K.CRANK_SECONDS


def explode_clip(turbo_tags):
    """The `explode` motion's function of time: the staged explode over EXPLODE_SECONDS with
    the crank cycle running throughout."""
    def at(t: float) -> dict[str, float]:
        p = min(1.0, max(0.0, t / EXPLODE_SECONDS))
        values = K.pose(_theta(t), turbo_tags)
        out = piston_out(p)
        for c in S.CYLINDERS:
            values[f"piston_{c.number}"] += out
        values.update(explode_values(p))
        return values
    return at


def running_reveal_clip(turbo_tags):
    """The `running_reveal` motion's function of time: the cutaway off, held and back on over
    REVEAL_SECONDS while the crank cycle runs."""
    def at(t: float) -> dict[str, float]:
        values = K.pose(_theta(t), turbo_tags)
        values.update(reveal_values(reveal_progress(t)))
        return values
    return at
