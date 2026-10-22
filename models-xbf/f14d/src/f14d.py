"""Grumman F-14D Super Tomcat -- full assembly.

Wings at 20 degrees, canopy closed, gear down, on the deck.  Clean airframe:
empty pylon stations, no external stores.

The airframe skin is ONE lofted solid (``src/lib/body.py``) built from
full-width blended sections, so the glove flows into the forward fuselage, the
nacelles flow into the pancake tunnel, and the fin roots sit on a continuous
surface.  Nothing in the primary surface is filleted, because nothing there is
joined.

Assembly tree is grouped BY SYSTEM -- ten sibling stages under ``src/``, each
a ``@memo`` function composed here by CALLING it.  THE ``SYSTEMS`` LIST BELOW
IS THE OCCURRENCE ORDER:

    airframe    the one-piece blended skin, cut and detailed
    cockpit     tub, panels, seats, HUD, canopy, windscreen
    wings       panels, slats, flaps, spoilers, tip lights
    inlets      ramps, splitters, bleed slots, ducts
    nozzles     C-D nozzles, petals, seals, actuator rings
    empennage   fins, rudders, stabilators, ventral fins
    aft         speed brakes, beavertail, tailhook, dump mast
    nose_gear   leg, wheels, launch bar, doors, bay
    main_gear   legs, wheels, brakes, doors, bays
    details     antennas, probes, lights, wicks, vents, panels

Three systems the brief names -- glove, engines, markings -- have no stage
yet; add one as ``src/<name>.py`` and insert it here.  A system that fails to
build fails the aircraft.

Which skin cutters the airframe applies is decided in ``src/airframe.py``.

The aircraft declares no kinematics: nothing about it articulates.  It is one
xbf (``XBF/f14d.xbf``); STEP, GLB, STL and 3MF are written from it on demand.
"""

from __future__ import annotations

from cadgen.engine import track, xbf

import build123d as bd

from aft import aft
from airframe import airframe
from cockpit import cockpit
from details import details
from empennage import empennage
from inlets import inlets
from main_gear import main_gear
from nose_gear import nose_gear
from nozzles import nozzles
from wings import wings

# Order here IS the occurrence order.
SYSTEMS = [
    airframe,
    cockpit,
    wings,
    inlets,
    nozzles,
    empennage,
    aft,
    nose_gear,
    main_gear,
    details,
]


@xbf(out="../XBF/f14d.xbf")
def f14d():
    groups = [system() for system in track(SYSTEMS, label=lambda system: system.__name__)]
    aircraft = bd.Compound(children=groups, label="f14d_super_tomcat")
    return aircraft
