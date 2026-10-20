"""One finish library for the whole arm: sheet colours, hardware, and the
materials the parts carry.

Two layers, because they are consumed by different things:

* a **colour** per leaf (`srgb()` hex) — what normal-CAD snapshots, the viewer
  and the STEP's XCAF labels show;
* a **material** per part (`cadgen.engine.Material`: name, density, PBR) — set on
  the shapes by `apply_materials` at the end of the build, which colour alone
  cannot express.

Colour is authored through srgb() rather than as raw channels, which is not a
style preference: the render pipeline treats a build123d Color's channels as
LINEAR and converts to sRGB on the way to the screen, so a hex pasted in as raw
channels displays far too pale. srgb() takes the hex you actually want to SEE.
"""
from __future__ import annotations


from lib.color import srgb


# --- 5052-H32 sheet finishes ---------------------------------------------------------------------
# Every sheet part is cut from 5052-H32 by SendCutSend, so they all take one finish from here rather
# than each naming its own. SendCutSend anodises aluminium in exactly five colours - red, gold, blue,
# black and clear (https://sendcutsend.com/faq/what-metal-colors-do-you-offer/) - and bare mill stock
# is the sixth option, the one you get by ordering no finish at all.
#
# Anodising is a conversion of the surface, not a coating: it keeps the metal's specular response, so
# these read as metal with a tint rather than as paint. Black is the deepest and most matte, clear
# barely shifts the mill colour while evening it out, and the dyed colours stay fairly reflective.
# Gloss cannot be set per part in the viewer (metalness and roughness are cadjs THEME settings), so
# the hexes below are picked to read correctly under the default theme; the material definitions
# carry the channels that Render and GLB do honour.
SHEET_FINISHES = {
    'black': {
        'hex': '#26262A',
        'material': {'name': 'Anodized black 5052', 'roughness': 0.52, 'metalness': 0.62},
    },
    'clear': {
        'hex': '#B4B8BC',
        'material': {'name': 'Anodized clear 5052', 'roughness': 0.34, 'metalness': 0.80},
    },
    'red': {
        'hex': '#8E2733',
        'material': {'name': 'Anodized red 5052', 'roughness': 0.38, 'metalness': 0.70},
    },
    'gold': {
        'hex': '#B08A3C',
        'material': {'name': 'Anodized gold 5052', 'roughness': 0.38, 'metalness': 0.72},
    },
    'blue': {
        'hex': '#26527E',
        'material': {'name': 'Anodized blue 5052', 'roughness': 0.38, 'metalness': 0.70},
    },
    # Bare stock: a mid silver-grey with a faint cool cast (the oxide skin), noticeably darker than
    # the anodised or polished look.
    'mill': {
        'hex': '#A7ACB0',
        'material': {'name': '5052-H32 mill finish', 'roughness': 0.45, 'metalness': 0.85},
    },
}

# The ordered finish. Changing this one name re-colours every sheet part in the arm, because they all
# read ALUMINUM_5052_COLOR, and re-labels the material Render and GLB resolve.
SHEET_FINISH = 'mill'  # Gray aluminum for the demo; production order finish is black.

ALUMINUM_5052_HEX = SHEET_FINISHES[SHEET_FINISH]['hex']
ALUMINUM_5052_COLOR = srgb(ALUMINUM_5052_HEX)

# --- hardware ------------------------------------------------------------------------------------
# Three materials, because the box holds three. The M2 kit is "gold and silver": its posts are
# brass, its nuts are 304 stainless. Every screw is a 12.9 socket cap with a black oxide finish.
HARDWARE_FINISHES = {
    'brass': {
        'hex': '#B08A4A',
        'material': {'name': 'Brass', 'roughness': 0.34, 'metalness': 0.90},
    },
    'black_oxide': {
        'hex': '#3A3A3E',
        'material': {'name': 'Black oxide alloy steel', 'roughness': 0.60, 'metalness': 0.72},
    },
    'stainless': {
        'hex': '#9DA1A6',
        'material': {'name': '304 stainless', 'roughness': 0.30, 'metalness': 0.92},
    },
}
STANDOFF_FINISH = 'brass'      # the M2 kit's posts
NUT_FINISH = 'stainless'       # the M2 kit's nuts, its "silver" half
SCREW_FINISH = 'black_oxide'   # the 12.9 socket caps

STANDOFF_COLOR = srgb(HARDWARE_FINISHES[STANDOFF_FINISH]['hex'])
NUT_COLOR = srgb(HARDWARE_FINISHES[NUT_FINISH]['hex'])
SCREW_COLOR = srgb(HARDWARE_FINISHES[SCREW_FINISH]['hex'])


# --- the off-the-shelf boards ---------------------------------------------------------------------
# Raspberry Pi publishes the Pi 5 as a mechanical reference (RP-004882-DD Iss1) and it carries no
# colour at all, so the board renders as bare grey. These finishes give the vendor solids their real
# appearance without touching the geometry: the populated board green, the shells steel, the headers
# and connector bodies black.
BOARD_FINISHES = {
    'pcb_green': {
        'hex': '#1F5E3A',
        'material': {'name': 'FR-4 solder mask', 'roughness': 0.55, 'metalness': 0.05},
    },
    'connector_steel': {
        'hex': '#B9BDC2',
        'material': {'name': 'Plated connector shell', 'roughness': 0.35, 'metalness': 0.85},
    },
    'connector_black': {
        'hex': '#232326',
        'material': {'name': 'Connector body', 'roughness': 0.70, 'metalness': 0.05},
    },
}


# --- materials on the shapes ---------------------------------------------------------------------
# The `materials=` declaration this module used to build is now a `Material` set on each part (the
# engine has no decorator argument). Each finish keeps the catalogue's name and density where the
# alloy is a catalogue one, its own metallic/roughness, and its finish name as the description;
# `color=None`, so every part keeps the srgb() colour its builder gave it.
from dataclasses import replace as _replace

from cadgen.engine import Material, materials as _catalogue


def _finish(base, entry):
    pbr = entry['material']
    return _replace(base, color=None, metallic=pbr['metalness'], roughness=pbr['roughness'],
                    description=pbr['name'])


# 5052-H32 (2680 kg/m^3) has no catalogue card of its own: the generic aluminium's name and density.
SHEET_MATERIAL = _finish(_catalogue.aluminium, SHEET_FINISHES[SHEET_FINISH])
HARDWARE_MATERIALS = {
    'brass': _finish(_catalogue.brass, HARDWARE_FINISHES['brass']),
    'black_oxide': _finish(_catalogue.steel, HARDWARE_FINISHES['black_oxide']),
    'stainless': _finish(_catalogue.stainless_steel, HARDWARE_FINISHES['stainless']),  # 304 = X5CrNi18-10
}
BOARD_MATERIALS = {
    'pcb_green': Material('FR4', 1850.0, metallic=0.05, roughness=0.55, description='FR-4 solder mask'),
    'connector_steel': _finish(_catalogue.steel, BOARD_FINISHES['connector_steel']),
    'connector_black': _finish(_catalogue.nylon, BOARD_FINISHES['connector_black']),
}


def apply_materials(assembly):
    """Every leaf of ``assembly`` in one of the selected finishes' colours takes that finish's
    material: the colour IS the assignment here, since every sheet part reads ALUMINUM_5052_COLOR
    and every M2/M3 part its hardware colour. Returns the count per material description."""
    by_color = [(ALUMINUM_5052_COLOR, SHEET_MATERIAL),
                (STANDOFF_COLOR, HARDWARE_MATERIALS[STANDOFF_FINISH]),
                (NUT_COLOR, HARDWARE_MATERIALS[NUT_FINISH]),
                (SCREW_COLOR, HARDWARE_MATERIALS[SCREW_FINISH])]
    counts = {}

    def walk(node):
        children = list(node.children or ())
        if children:
            for child in children:
                walk(child)
            return
        rgba = node.__dict__.get('_color')
        if rgba is None:
            return
        rgba = tuple(rgba)
        for color, material in by_color:
            if all(abs(a - b) < 1e-4 for a, b in zip(rgba, color)):
                node.material = material
                counts[material.description] = counts.get(material.description, 0) + 1
                return

    walk(assembly)
    return counts
