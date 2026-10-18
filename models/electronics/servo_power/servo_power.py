"""Servo power: USB-C 5 V through a 2 A polyfuse to three servo headers, and 3.3 V for the logic.

A 50 x 35 mm two-layer board for JLCPCB's standard service. USB-C comes in at the left edge (a
sink: 5.1k pull-downs on CC1 and CC2, D+ and D- unused); its two VBUS pairs run either side of
the pull-downs into a 2 A polyfuse, whose +5V feeds an AMS1117-3.3 (10 uF in, 22 uF out) and,
along the bottom, the +5V column of three servo headers at the right edge (pin 1 signal, 2 +5V,
3 GND, ground outermost). The logic plugs into the 1x5 header on the top edge: S1-S3, +3V3 and
GND. The three servo signals run from it as one bus at a 3.59 mm pitch -- the servo pins' 5.08 mm
seen across a 45 degree run -- fanned out of the header's 2.54 mm pins and into the servo pins
with 45 degree jogs. A green LED on +3V3 shows the board is powered.

Everything is routed on top; the bottom is one ground pour, pierced only by vias and pins, and
the top's pour between the tracks is ground too, a via beside every surface-mount ground pad.
VBUS and +5V carry 2 A: 0.8 mm tracks (IPC-2221, 1 oz outer copper, 10 C rise: 0.78 mm), held
to it by `servo_power.kicad_dru`, which also lets the receptacle's 0.6 mm pads keep their own
width inside its courtyard, joins the receptacle's ground solid to the pours and allows GCT's
land pattern its 0.17 mm between pads and locating pegs. The design rules are JLCPCB's
standard 2-layer limits; the drill/place origin is the outline's top-left corner.

DRC leaves one warning, lib_footprint_mismatch on U1: the 2026-10-13 nightly reports KiCad's own
SOT-223-3_TabPin2 as differing from its library copy right after placing it from that library
(the board's other stock footprints do not). It is the nightly's, not the board's.
"""
import math

from cadgen import pcb
from cadgen.kipy.board_types import BoardArc, BoardLayer, BoardSegment, BoardText, Net, Track, Via, Zone
from cadgen.kipy.geometry import Angle, PolygonWithHoles, PolyLineNode, Vector2
from cadgen.kipy.proto.board.board_commands_pb2 import BoardOriginType
from cadgen.kipy.proto.board.board_types_pb2 import ZoneConnectionStyle, ZoneType

WIDTH, HEIGHT, CORNER = 50.0, 35.0, 3.0  # the outline, its top-left corner on KiCad's origin (y down)
HOLES = [(3.5, 3.5), (46.5, 3.5), (3.5, 31.5), (46.5, 31.5)]  # M3, 3.5 mm in from each corner

USB_C = "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal"
R_0603 = "Resistor_SMD:R_0603_1608Metric"
C_0805 = "Capacitor_SMD:C_0805_2012Metric"
HEADER_1X3 = "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical"
HEADER_1X5 = "Connector_PinHeader_2.54mm:PinHeader_1x05_P2.54mm_Vertical"
HOLE = "MountingHole:MountingHole_3.2mm_M3"

# ref: footprint, value, (x, y) mm, rotation, pad -> net. Placed in this order, numbered in it.
PARTS = {
    # The receptacle's mating face on the left edge: its PCB-edge line (local y 3.675) on x = 0.
    "J1": (USB_C, "USB4105-GF-A", (3.675, 17.5), -90,
           {"A1": "GND", "B12": "GND", "A12": "GND", "B1": "GND", "SH": "GND",
            "A4": "VBUS", "B9": "VBUS", "A9": "VBUS", "B4": "VBUS", "A5": "CC1", "B5": "CC2"}),
    "R1": (R_0603, "5.1k", (9.95, 16.25), 0, {"1": "CC1", "2": "GND"}),
    "R2": (R_0603, "5.1k", (9.95, 18.75), 0, {"1": "CC2", "2": "GND"}),
    "F1": ("Fuse:Fuse_1206_3216Metric", "2A polyfuse", (15.2, 17.5), 0, {"1": "VBUS", "2": "+5V"}),
    "C1": (C_0805, "10uF", (21.85, 13.55), 0, {"1": "+5V", "2": "GND"}),
    # AMS1117-3.3: pins 1 GND, 2 VO, 3 VI along the bottom, the tab (VO) at the top.
    "U1": ("Package_TO_SOT_SMD:SOT-223-3_TabPin2", "AMS1117-3.3", (16.5, 10.4), 90, {"1": "GND", "2": "+3V3", "3": "+5V"}),
    "C2": (C_0805, "22uF", (15.55, 4.6), 180, {"1": "+3V3", "2": "GND"}),
    "R3": (R_0603, "1k", (11.2, 7.25), 180, {"1": "+3V3", "2": "LED"}),
    "D1": ("LED_SMD:LED_0603_1608Metric", "green", (10.375, 9.6), 90, {"1": "GND", "2": "LED"}),
    # Servo headers, pins left to right: 1 signal, 2 +5V, 3 GND at the edge; 5.08 mm apart.
    "J2": (HEADER_1X3, "servo 1", (41.32, 12.42), 90, {"1": "S1", "2": "+5V", "3": "GND"}),
    "J3": (HEADER_1X3, "servo 2", (41.32, 17.5), 90, {"1": "S2", "2": "+5V", "3": "GND"}),
    "J4": (HEADER_1X3, "servo 3", (41.32, 22.58), 90, {"1": "S3", "2": "+5V", "3": "GND"}),
    # The logic's header on the top edge, pin 1 at its right: S1, S2, S3, +3V3, GND.
    "J5": (HEADER_1X5, "logic", (32.0, 4.2), -90, {"1": "S1", "2": "S2", "3": "S3", "4": "+3V3", "5": "GND"}),
    **{f"H{i + 1}": (HOLE, "M3", at, 0, {}) for i, at in enumerate(HOLES)},
}

# Where a reference sits when the footprint's own place would crowd it: (x, y) mm, angle.
LABELS = {
    "J1": (4.2, 10.6, 0), "R1": (10.0, 13.6, 0), "R2": (10.0, 21.4, 0), "F1": (15.2, 15.6, 0),
    "C1": (21.85, 11.8, 0), "U1": (12.0, 10.4, 90), "C2": (15.55, 2.5, 0), "R3": (11.2, 5.8, 0),
    "D1": (8.5, 9.6, 0), "J2": (49.0, 12.42, 90), "J3": (49.0, 17.5, 90), "J4": (49.0, 22.58, 90),
    "J5": (35.2, 4.2, 0),
}

TRACE, POWER, THREE_V3 = 0.25, 0.8, 0.5  # mm: signals, the 2 A nets (0.78 mm needed), +3V3
STUB = 0.6  # the receptacle's VBUS and GND pads are 0.6 mm wide: their tracks leave at that width
VIA, DRILL = 0.6, 0.3
BUS = ["S1", "S2", "S3"]


def nm(mm):
    """Millimetres to KiCad's nanometres, rounded (kipy's from_mm truncates)."""
    return round(mm * 1_000_000)


def at(x, y):
    return Vector2.from_xy(nm(x), nm(y))


def off(point, dx=0.0, dy=0.0):
    """`point` moved by (dx, dy) mm."""
    return Vector2.from_xy(point.x + nm(dx), point.y + nm(dy))


def track(net, width, *points, layer=BoardLayer.BL_F_Cu):
    """Straight segments through `points` (Vector2s), one Track each, at 0, 45 or 90 degrees only."""
    for a, b in zip(points, points[1:]):
        dx, dy = abs(b.x - a.x), abs(b.y - a.y)
        if dx and dy and dx != dy:
            raise ValueError(f"{net}: ({a.x}, {a.y}) to ({b.x}, {b.y}) nm is not octolinear")
        t = Track()
        t.start, t.end, t.width, t.layer, t.net = a, b, nm(width), layer, Net(name=net)
        yield t


def via(net, point):
    v = Via()
    v.position, v.net, v.diameter, v.drill_diameter = point, Net(name=net), nm(VIA), nm(DRILL)
    return v


def polygon(points):
    poly = PolygonWithHoles()
    for x, y in points:
        poly.outline.append(PolyLineNode.from_xy(nm(x), nm(y)))
    poly.outline.closed = True
    return poly


def outline(w, h, r):
    """The board's edge on Edge.Cuts: four lines and four quarter-circle corners."""
    k = r * (1 - math.sqrt(0.5))  # a corner arc's midpoint, in from each edge
    lines = [((r, 0), (w - r, 0)), ((w, r), (w, h - r)), ((w - r, h), (r, h)), ((0, h - r), (0, r))]
    arcs = [((0, r), (k, k), (r, 0)), ((w - r, 0), (w - k, k), (w, r)),
            ((w, h - r), (w - k, h - k), (w - r, h)), ((r, h), (k, h - k), (0, h - r))]
    items = []
    for start, end in lines:
        s = BoardSegment()
        s.start, s.end = at(*start), at(*end)
        items.append(s)
    for start, mid, end in arcs:
        a = BoardArc()
        a.start, a.mid, a.end = at(*start), at(*mid), at(*end)
        items.append(a)
    for item in items:
        item.layer, item.attributes.stroke.width = BoardLayer.BL_Edge_Cuts, nm(0.1)
    return items


def ground_pour(layer):
    zone = Zone()
    zone.outline = polygon([(0.5, 0.5), (WIDTH - 0.5, 0.5), (WIDTH - 0.5, HEIGHT - 0.5), (0.5, HEIGHT - 0.5)])
    zone.layers, zone.net, zone.name = [layer], Net(name="GND"), f"GND {BoardLayer.Name(layer)[3:]}"
    zone.clearance = nm(0.3)
    zone.connection.zone_connection = ZoneConnectionStyle.ZCS_PTH_THERMAL  # pins on spokes, SMD pads solid
    zone.connection.thermal_spokes.width, zone.connection.thermal_spokes.gap = nm(0.5), nm(0.3)
    return zone


def hole_keepout(x, y, radius=3.0):
    """No copper under an M3 screw head and washer."""
    zone = Zone()
    zone.type = ZoneType.ZT_RULE_AREA
    zone.outline = polygon([(x + radius * math.cos(math.pi * i / 8), y + radius * math.sin(math.pi * i / 8)) for i in range(16)])
    zone.layers, zone.name = [BoardLayer.BL_F_Cu, BoardLayer.BL_B_Cu], f"hole {x:g},{y:g}"
    s = zone.rule_area_settings
    s.keepout_copper = s.keepout_tracks = s.keepout_vias = True
    return zone


def text(value, x, y, size=1.0, thickness=0.15, width=None):
    label = BoardText()
    label.value, label.position, label.layer = value, at(x, y), BoardLayer.BL_F_SilkS
    label.attributes.size = Vector2.from_xy(nm(width or size), nm(size))
    label.attributes.stroke_width = nm(thickness)
    return label


def usb_input(p):
    """J1's two VBUS pairs round the CC pull-downs into the fuse; the ground pads to vias."""
    items = []
    fuse = p["F1.1"]
    for pad in ("J1.A4", "J1.A9"):  # the upper and the lower VBUS pair
        start = p[pad]
        rise = abs(fuse.y - start.y)
        turn = Vector2.from_xy(fuse.x - rise, start.y)  # the 45 degree run into the fuse starts here
        stub = off(start, 1.245)
        items += [*track("VBUS", STUB, start, stub), *track("VBUS", POWER, stub, turn, fuse)]
    items += track("CC1", TRACE, p["J1.A5"], p["R1.1"])
    cc2 = p["J1.B5"]
    items += track("CC2", TRACE, cc2, off(cc2, 0.745), off(cc2, 1.245, -0.5), p["R2.1"])
    middle = Vector2.from_xy(p["R1.2"].x, (p["R1.2"].y + p["R2.2"].y) // 2)
    items += [*track("GND", 0.3, p["R1.2"], middle, p["R2.2"]), via("GND", middle)]
    for pad, side in (("J1.A1", -1), ("J1.A12", 1)):  # the upper and the lower ground pair
        start = p[pad]
        bend, hole = off(start, 0.445), off(start, 1.045, 0.6 * side)
        items += [*track("GND", 0.4, start, bend, hole), via("GND", hole)]
    return items


def regulator(p):
    """+5V from the fuse up to the LDO and its input capacitor; +3V3 from the tab to C2, the LED and J5."""
    vi = p["U1.3"]
    junction = Vector2.from_xy(vi.x, p["F1.2"].y)  # +5V splits here: up to U1, down to the servos
    items = [*track("+5V", POWER, p["F1.2"], junction, vi, p["C1.1"])]
    tab = p["U1.tab"]
    j5 = p["J5.4"]
    rise = tab.y - j5.y
    items += track("+3V3", THREE_V3, p["U1.2"], tab)  # the pin and the tab are one pin: joined under the body
    items += track("+3V3", THREE_V3, tab, p["C2.1"])
    items += track("+3V3", THREE_V3, tab, p["R3.1"])
    items += track("+3V3", THREE_V3, tab, Vector2.from_xy(j5.x - rise, tab.y), j5)
    items += track("LED", TRACE, p["R3.2"], p["D1.2"])
    for pad, dx, dy in (("U1.1", -1.25, 0), ("C1.2", 1.4, 0), ("C2.2", -1.4, 0), ("D1.1", 0, 1.313)):
        hole = off(p[pad], dx, dy)
        items += [*track("GND", 0.4, p[pad], hole), via("GND", hole)]
    return items, junction


def servo_supply(p, junction):
    """+5V down from the junction at 45 degrees, along the bottom and up the servos' pin 2 column."""
    low = p["J4.2"].y + nm(1.92)  # the trunk's y, under the lowest servo
    drop = low - junction.y
    corner = Vector2.from_xy(junction.x + drop, low)
    return list(track("+5V", POWER, junction, corner, Vector2.from_xy(p["J4.2"].x, low), p["J4.2"], p["J3.2"], p["J2.2"]))


def signal_bus(p):
    """S1-S3 from J5 to the servos' pin 1s as one bus, concentric round its 45 degree turn.

    J5's pins are 2.54 mm apart and the servos' 5.08 mm: the bus runs at 5.08 / sqrt(2), so the
    jogs into the lower two servo pins, parallel at 45 degrees, sit at the bus's own pitch. S1,
    J5's rightmost pin, is the turn's inner track and runs straight into J2's pin 1.
    """
    pitch = 5.08 / math.sqrt(2)
    fan = nm(pitch - 2.54)  # J5's outer pins step out to the bus pitch at 45 degrees
    items, runs = [], []
    for i, net in enumerate(BUS):
        start, end = p[f"J5.{i + 1}"], p[f"J{i + 2}.1"]
        side = 1 - i  # S1 steps right, S2 stays on its pin, S3 steps left
        top = Vector2.from_xy(start.x + side * fan, start.y + abs(side) * fan)
        level = p["J2.1"].y + nm(i * pitch)  # this track's horizontal run
        chamfer = nm(4.0 + i * pitch * (2 - math.sqrt(2)))  # concentric: each outer turn wider
        jog = end.y - level  # down into the servo pin at 45 degrees
        bend, turned, jogged = (Vector2.from_xy(top.x, level - chamfer), Vector2.from_xy(top.x + chamfer, level),
                                Vector2.from_xy(end.x - jog, level))
        points = [start, top, bend, turned, jogged, end]
        points = [q for k, q in enumerate(points) if k == 0 or (q.x, q.y) != (points[k - 1].x, points[k - 1].y)]
        items += track(net, TRACE, *points)
        runs.append([(top, bend), (bend, turned), (turned, jogged)])  # its vertical, 45 degree and level runs
    # A ground via midway between neighbours on each run: the pour between them is ground too.
    for first, second in zip(runs, runs[1:]):
        for (a0, a1), (b0, b1) in zip(first, second):
            items.append(via("GND", Vector2.from_xy(round((a0.x + a1.x + b0.x + b1.x) / 4), round((a0.y + a1.y + b0.y + b1.y) / 4))))
    return items


@pcb
def servo_power(board):
    # Place every part, name it and put its pads on their nets.
    pads, placed = {}, []
    for ref, (footprint, value, (x, y), rotation, nets) in PARTS.items():
        fp = board.place_footprint_from_library(footprint, at(x, y), Angle.from_degrees(rotation))
        fp.reference_field.text.value = ref
        fp.value_field.text.value = value
        if ref in LABELS:
            lx, ly, angle = LABELS[ref]
            fp.reference_field.text.position = at(lx, ly)
            fp.reference_field.text.attributes.angle = angle
        if ref.startswith("H"):
            fp.reference_field.visible = False
        for pad in fp.definition.pads:
            if pad.number in nets:
                pad.net = Net(name=nets[pad.number])
            pads.setdefault(f"{ref}.{pad.number}", pad.position)  # absolute, nm: route from these
        placed.append(fp)
    board.update_items(placed)
    # U1's pin 2 is two pads, the pin and the tab: the tab is the one away from pin 1.
    u1 = next(fp for fp in placed if fp.reference_field.text.value == "U1")
    pin1 = pads["U1.1"]
    pads["U1.tab"] = max((pad.position for pad in u1.definition.pads if pad.number == "2"),
                         key=lambda q: (q.x - pin1.x) ** 2 + (q.y - pin1.y) ** 2)

    # Route one group at a time: the USB input, the regulator, the servos' +5V, the signal bus.
    regulated, junction = regulator(pads)
    copper = [*usb_input(pads), *regulated, *servo_supply(pads, junction), *signal_bus(pads)]

    labels = [text("SERVO PWR", 25.0, 29.0, size=1.5, thickness=0.2), text("5V 2A", 25.0, 31.3),
              *(text(name, pads[f"J5.{i + 1}"].x / 1e6, 1.6, width=0.8) for i, name in enumerate(["S1", "S2", "S3", "3V3", "GND"])),
              *(text(name, pads[f"J2.{i + 1}"].x / 1e6, 9.5) for i, name in enumerate(["S", "+", "-"]))]
    board.create_items([
        *outline(WIDTH, HEIGHT, CORNER),
        *copper,
        ground_pour(BoardLayer.BL_F_Cu),
        ground_pour(BoardLayer.BL_B_Cu),
        *(hole_keepout(x, y) for x, y in HOLES),
        *labels,
    ])
    board.set_origin(BoardOriginType.BOT_DRILL, at(0, 0))

    # JLCPCB's standard 2-layer limits: what DRC holds the board to.
    rules = board.get_design_rules().rules
    c = rules.constraints
    c.min_clearance = c.min_track_width = nm(0.1)
    c.min_via_size, c.min_through_drill, c.min_via_annular_width = nm(0.5), nm(0.3), nm(0.13)
    c.copper_edge_clearance, c.hole_to_hole_min = nm(0.2), nm(0.5)
    c.hole_clearance = nm(0.15)  # the floor; servo_power.kicad_dru holds all but J1's land pattern to 0.25
    c.silk_clearance, c.min_silk_text_height, c.min_silk_text_thickness = nm(0.15), nm(1.0), nm(0.15)
    board.set_design_rules(rules)


if __name__ == "__main__":
    servo_power()
