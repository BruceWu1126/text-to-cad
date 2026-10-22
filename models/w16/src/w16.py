"""Quad-turbo 8.0 L W16 — full sectioned assembly.

Thirteen systems, each a `@memo` stage in its own file under `src/`, composed
here by CALLING them (occurrence order = the original's explode order). The
museum section is one flag, `lib/spec.py:SECTIONED`, read by every system: the
cutaway is the model's own geometry. The engine is one xbf (`XBF/w16.xbf`) and
one GLB (`XBF/w16.glb`), whose `crank` clip is the `crank` motion below.
    block          block + main caps + shells
    crank          crankshaft, damper, flywheel
    pistons        16 x piston/rings/pin/circlips/rod/cap/bolts/shells
    heads          two heads
    valvetrain     64 valves + springs + retainers + followers + HLAs
    cams           four camshafts + caps
    camdrive       sprockets, chains, guides, tensioners
    covers         cam covers, coils, plug wells, breathers
    oil_system     dry-sump pan, windage tray, pumps, filter
    turbos         four turbochargers
    exhaust        16 primaries, collectors, downpipes, shields
    induction      intercoolers, plenums, throttles, charge pipes, fuel rails
    ancillaries    alternator, water pumps, belt, coolant manifolds, bell housing
"""

from __future__ import annotations

from cadgen.engine import glb, motion, track, xbf

import build123d as bd

from ancillaries import ancillaries
from block import block
from camdrive import camdrive
from cams import cams
from covers import covers
from crank import crank
from exhaust import exhaust
from heads import heads
from induction import induction
from oil_system import oil_system
from pistons import pistons
from turbos import turbos
from valvetrain import valvetrain

from lib import kinematics
from lib.palette import apply_finishes

# Order here IS the occurrence order.
SYSTEMS = [
    block,
    crank,
    pistons,
    heads,
    valvetrain,
    cams,
    camdrive,
    covers,
    oil_system,
    turbos,
    exhaust,
    induction,
    ancillaries,
]


@xbf(out="../XBF/w16.xbf")
@glb(out="../XBF/w16.glb")
def w16():
    systems = [system() for system in track(SYSTEMS, label=lambda system: system.__name__)]
    engine = bd.Compound(children=systems, label="w16")
    apply_finishes(engine)
    kinematics.author_joints(engine)
    # One full four-stroke cycle, 720 deg in 6 s, looping: the pistons and rods in
    # the firing order, the cams at half speed, 64 valves and their followers, the
    # oil pump, the turbo rotors and every accessory pulley.
    cycle = kinematics.crank_cycle(kinematics.turbos(engine))
    return motion(engine, "crank", cycle, duration=kinematics.CRANK_SECONDS, loop="loop")
