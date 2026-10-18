"""Pi servo HAT: serial bus servos (Feetech/Waveshare STS and SCS) driven from a Raspberry Pi's UART.

A 65 x 56 mm two-layer board on the Raspberry Pi HAT outline (KiCad's own template: its M2.5 holes,
and the 2x20 socket on the bottom, so the board stacks on the Pi). The Pi's UART (pin 8 TXD, pin 10
RXD, 3.3 V from pins 1 and 17) becomes one half-duplex DATA line, the way Waveshare's bus servo
adapter does it: while TXD is low a PNP (Q1) pulls TXEN up, so the 74LVC1G126 drives DATA low and the
74LVC1G125 stops listening; when TXD goes high the PNP lets go, R2 pulls TXEN down, R3 pulls DATA up
and the '125 passes DATA back to RXD, which R4 holds high while the '125 is off. Four servo ports run
along the bottom edge, pin 1 DATA, 2 VIN, 3 GND, so servos daisy-chain from any of them.

The servos' supply comes in on the screw terminal (6-12 V, the servos' own rating), clamped by an
SMAJ15CA TVS and held up by 220 uF, and goes straight to the ports: the Pi is powered as usual,
from its own USB-C, and nothing joins the two supplies but ground. VIN is a 3 mm bus on top (IPC-2221,
1 oz outer copper, 10 C rise: 5 A) with 1.5 mm stubs into each port's pin 2 (2.3 A, a servo's stall).
DATA and TXEN cross the logic on the bottom layer, which is otherwise one ground pour; the top's pour
between the tracks is ground too, a via beside every surface-mount ground pad. `pi_servo_hat.kicad_dru`
holds VIN to its widths. No ID EEPROM: a board that should enumerate as a HAT adds the 24C32 on
pins 27 and 28.
"""
import math

from cadgen import pcb
from cadgen.kipy.board_types import BoardArc, BoardLayer, BoardSegment, BoardText, Net, Track, Via, Zone
from cadgen.kipy.geometry import Angle, PolygonWithHoles, PolyLineNode, Vector2
from cadgen.kipy.proto.board.board_commands_pb2 import BoardOriginType
from cadgen.kipy.proto.board.board_types_pb2 import ZoneConnectionStyle, ZoneType

WIDTH, HEIGHT, CORNER = 65.0, 56.0, 3.0  # the HAT outline, its top-left corner on KiCad's origin (y down)
HOLES = [(3.5, 3.5), (61.5, 3.5), (3.5, 52.5), (61.5, 52.5)]  # M2.5, the HAT spec's

SOCKET = "Connector_PinSocket_2.54mm:PinSocket_2x20_P2.54mm_Vertical"
PORT = "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical"
TERMINAL = "TerminalBlock_Phoenix:TerminalBlock_Phoenix_PT-1,5-2-3.5-H_1x02_P3.50mm_Horizontal"
SOT5 = "Package_TO_SOT_SMD:SOT-23-5"
R_0603 = "Resistor_SMD:R_0603_1608Metric"
C_0603 = "Capacitor_SMD:C_0603_1608Metric"
HOLE = "MountingHole:MountingHole_2.7mm_M2.5"
PI_GND = ("6", "9", "14", "20", "25", "30", "34", "39")
PORTS = 4

# ref: footprint, value, (x, y) mm, rotation, pad -> net, layer. Placed in this order, numbered in it.
PARTS = {
    # The Pi's 40 pins on the bottom, where KiCad's HAT template puts them: odd pins on y 4.77.
    "J1": (SOCKET, "Pi GPIO", (8.37, 4.77), 270,
           {"1": "+3V3", "17": "+3V3", "8": "TXD", "10": "RXD", **{n: "GND" for n in PI_GND}}, BoardLayer.BL_B_Cu),
    # Its wire entry faces the right edge.
    "J2": (TERMINAL, "VIN 6-12V", (59.5, 30.0), 90, {"1": "VIN", "2": "GND"}, None),
    **{f"J{k + 3}": (PORT, f"servo {k + 1}", (12.0 + 11.0 * k, 50.0), 90, {"1": "DATA", "2": "VIN", "3": "GND"}, None)
       for k in range(PORTS)},
    "C1": ("Capacitor_SMD:CP_Elec_8x10", "220uF 25V", (39.79, 37.0), 0, {"1": "VIN", "2": "GND"}, None),
    "D1": ("Diode_SMD:D_SMA", "SMAJ15CA", (52.0, 38.0), 0, {"1": "VIN", "2": "GND"}, None),
    "U1": (SOT5, "74LVC1G126", (12.0, 16.0), 180, {"1": "TXEN", "2": "TXD", "3": "GND", "4": "DATA", "5": "+3V3"}, None),
    "U2": (SOT5, "74LVC1G125", (26.0, 16.0), 180, {"1": "TXEN", "2": "DATA", "3": "GND", "4": "RXD", "5": "+3V3"}, None),
    "Q1": ("Package_TO_SOT_SMD:SOT-23", "MMBT3906", (12.0, 22.0), 0, {"1": "BASE", "2": "+3V3", "3": "TXEN"}, None),
    "R1": (R_0603, "10k", (15.2, 19.5), 0, {"1": "BASE", "2": "TXD"}, None),
    "R2": (R_0603, "20k", (15.6, 23.8), 0, {"1": "TXEN", "2": "GND"}, None),
    "R3": (R_0603, "10k", (27.6, 23.3), 0, {"1": "DATA", "2": "+3V3"}, None),
    "R4": (R_0603, "10k", (22.5, 8.5), 0, {"1": "RXD", "2": "+3V3"}, None),
    "C2": (C_0603, "100nF", (9.6, 18.6), 0, {"1": "+3V3", "2": "GND"}, None),
    "C3": (C_0603, "100nF", (22.9, 18.6), 180, {"1": "+3V3", "2": "GND"}, None),
    "R5": (R_0603, "1k", (6.2, 11.0), 0, {"1": "LED", "2": "+3V3"}, None),
    "D2": ("LED_SMD:LED_0603_1608Metric", "green", (6.1625, 13.5), 180, {"1": "GND", "2": "LED"}, None),
    **{f"H{i + 1}": (HOLE, "M2.5", xy, 0, {}, None) for i, xy in enumerate(HOLES)},
}

# Where a reference sits when the footprint's own place would crowd it: (x, y) mm, upright.
LABELS = {"U1": (12.0, 13.4), "Q1": (11.0, 25.3), "C2": (6.6, 18.6), "D2": (3.6, 13.5), "U2": (26.0, 13.4), "C3": (20.4, 18.6)}

SIGNAL, LOGIC = 0.2, 0.3  # mm: signals, +3V3
VIN_BUS, VIN_STUB, VIN_FEED = 3.0, 1.5, 2.0  # the 5 A bus, a port's stub, the capacitor's and the TVS's
VIA, DRILL = 0.6, 0.3
BUS_Y, DATA_Y = 44.5, 47.0  # VIN's bus on top, DATA's on the bottom, both above the ports


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


def bottom(net, width, *points):
    return track(net, width, *points, layer=BoardLayer.BL_B_Cu)


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
    k = r * (1 - math.sqrt(0.5))
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


def hole_keepout(x, y, radius=2.9):
    """No copper under an M2.5 screw head and washer."""
    zone = Zone()
    zone.type = ZoneType.ZT_RULE_AREA
    zone.outline = polygon([(x + radius * math.cos(math.pi * i / 8), y + radius * math.sin(math.pi * i / 8)) for i in range(16)])
    zone.layers, zone.name = [BoardLayer.BL_F_Cu, BoardLayer.BL_B_Cu], f"hole {x:g},{y:g}"
    s = zone.rule_area_settings
    s.keepout_copper = s.keepout_tracks = s.keepout_vias = True
    return zone


def text(value, x, y, size=1.0, thickness=0.15):
    label = BoardText()
    label.value, label.position, label.layer = value, at(x, y), BoardLayer.BL_F_SilkS
    label.attributes.size = Vector2.from_xy(nm(size), nm(size))
    label.attributes.stroke_width = nm(thickness)
    return label


def gnd_via(p, pad, *path):
    """A ground pad's short track to its via, the via at the path's end (offsets in mm)."""
    points = [p[pad]]
    for dx, dy in path:
        points.append(off(points[-1], dx, dy))
    return [*track("GND", 0.3, *points), via("GND", points[-1])]


def uart(p):
    """TXD and RXD down from the Pi's pins, between the odd row's pins, to the two buffers."""
    items = []
    txd = off(p["J1.8"], 1.27, 1.27)  # between pins 7 and 9
    tx_branch, tx_end = Vector2.from_xy(txd.x, p["U1.2"].y), Vector2.from_xy(txd.x, p["R1.2"].y)
    items += track("TXD", SIGNAL, p["J1.8"], txd, tx_branch, tx_end, p["R1.2"])
    items += track("TXD", SIGNAL, tx_branch, p["U1.2"])
    rxd = off(p["J1.10"], 1.27, 1.27)  # between pins 9 and 11
    out = p["U2.4"]
    rx_branch, rx_turn = Vector2.from_xy(rxd.x, p["R4.1"].y), Vector2.from_xy(rxd.x, out.y - (out.x - rxd.x))
    items += track("RXD", SIGNAL, p["J1.10"], rxd, rx_branch, rx_turn, out)  # 45 degrees into U2's output
    items += track("RXD", SIGNAL, rx_branch, p["R4.1"])
    return items


def logic_supply(p):
    """+3V3 from pin 1 down the left group and from pin 17 down the right; joined on the bottom."""
    items = []
    left = p["J1.1"]
    stops = [Vector2.from_xy(left.x, p[pad].y) for pad in ("R5.2", "U1.5", "C2.1", "Q1.2")]
    items += track("+3V3", LOGIC, left, *stops)
    for stop, pad in zip(stops, ("R5.2", "U1.5", "C2.1", "Q1.2")):
        items += track("+3V3", LOGIC, stop, p[pad])
    right = p["J1.17"]
    r4 = Vector2.from_xy(right.x, p["R4.2"].y)
    edge = nm(30.5)  # the right group's +3V3 runs down past U2's vias
    turn = Vector2.from_xy(edge, r4.y + (edge - r4.x))
    under = Vector2.from_xy(edge, p["C3.1"].y)
    r3 = Vector2.from_xy(edge, p["R3.2"].y)
    items += track("+3V3", LOGIC, right, r4, turn, under, r3, p["R3.2"])
    items += track("+3V3", LOGIC, r4, p["R4.2"])
    corner = Vector2.from_xy(p["U2.5"].x, under.y)
    items += track("+3V3", LOGIC, under, corner, p["C3.1"])
    items += track("+3V3", LOGIC, corner, p["U2.5"])
    link = 6.9 - left.y / 1e6  # under the socket's odd row
    items += bottom("+3V3", LOGIC, left, off(left, 0, link), off(right, 0, link), right)
    items += track("LED", SIGNAL, p["R5.1"], p["D2.2"])
    return items


def half_duplex(p):
    """The buffers, the PNP and their resistors; TXEN and DATA cross to the other side on the bottom."""
    items = []
    # TXEN: Q1's collector and R2 on the left, U1's OE above them, U2's OE# across the UART on the bottom.
    oe1 = off(p["U1.1"], 14.6 - p["U1.1"].x / 1e6)
    v_oe1 = off(oe1, 0, 0.8)
    v_q1 = off(p["Q1.3"], 14.3 - p["Q1.3"].x / 1e6)
    items += [*track("TXEN", SIGNAL, p["U1.1"], oe1, v_oe1), via("TXEN", v_oe1)]
    items += [*track("TXEN", SIGNAL, p["Q1.3"], v_q1), via("TXEN", v_q1)]
    items += track("TXEN", SIGNAL, p["R2.1"], off(p["R2.1"], -0.475, -0.475), v_q1)
    oe2_turn = off(p["U2.1"], 28.1 - p["U2.1"].x / 1e6)
    v_oe2 = Vector2.from_xy(oe2_turn.x, v_oe1.y)
    items += [*track("TXEN", SIGNAL, p["U2.1"], oe2_turn, v_oe2), via("TXEN", v_oe2)]
    items += bottom("TXEN", SIGNAL, v_oe1, v_oe2)  # under the UART, on the bottom
    items += bottom("TXEN", SIGNAL, v_oe1, Vector2.from_xy(v_oe1.x, v_q1.y - (v_oe1.x - v_q1.x)), v_q1)
    # BASE: R1 round the left of U1's capacitor's via into Q1's base.
    base = p["Q1.1"]
    items += track("BASE", SIGNAL, p["R1.1"], Vector2.from_xy(base.x + (base.y - p["R1.1"].y), p["R1.1"].y), base)
    # DATA: U1's output and U2's input each to a via; joined, and run to the ports, on the bottom.
    d1_turn = Vector2.from_xy(nm(9.6), p["U1.4"].y)
    v_d1 = Vector2.from_xy(d1_turn.x, nm(14.2))
    items += [*track("DATA", SIGNAL, p["U1.4"], d1_turn, v_d1), via("DATA", v_d1)]
    v_d2 = off(p["U2.2"], 28.4 - p["U2.2"].x / 1e6)
    items += [*track("DATA", SIGNAL, p["U2.2"], v_d2), via("DATA", v_d2)]
    level = nm(15.4)  # the buffers' link, above TXEN's on the bottom
    items += bottom("DATA", SIGNAL, v_d1, Vector2.from_xy(v_d1.x, level),
                    Vector2.from_xy(v_d2.x - (v_d2.y - level), level), v_d2)
    down = off(v_d2, 1.0, 1.0)
    v_r3 = off(p["R3.1"], 0, 1.2)
    tee = Vector2.from_xy(down.x, v_r3.y)
    items += [*track("DATA", SIGNAL, p["R3.1"], v_r3), via("DATA", v_r3)]
    items += bottom("DATA", SIGNAL, v_d2, down, tee)
    items += bottom("DATA", SIGNAL, v_r3, tee)
    join = tee
    # Ground vias beside every surface-mount ground pad.
    items += gnd_via(p, "U1.3", (0.8, 0), (0.4, -0.4))
    items += gnd_via(p, "U2.3", (0.6, 0), (0.4, -0.4))
    items += gnd_via(p, "C2.2", (0, 1.05))
    items += gnd_via(p, "C3.2", (0, 1.1))
    items += gnd_via(p, "R2.2", (1.075, 0))
    items += gnd_via(p, "D2.1", (0, 1.2))
    return items, join


def ports(p, join):
    """VIN's bus on top and DATA's on the bottom, above the ports, with a stub down to each pin."""
    items = []
    pin2 = [p[f"J{k + 3}.2"] for k in range(PORTS)]
    pin1 = [p[f"J{k + 3}.1"] for k in range(PORTS)]
    terminal = p["J2.1"]
    stops = sorted({q.x for q in pin2} | {p["D1.1"].x, terminal.x})
    bus = [Vector2.from_xy(x, nm(BUS_Y)) for x in stops]
    items += track("VIN", VIN_BUS, *bus, terminal)
    for q in pin2:
        width = VIN_FEED if q.x == p["C1.1"].x else VIN_STUB
        items += track("VIN", width, Vector2.from_xy(q.x, nm(BUS_Y)), q)
    items += track("VIN", VIN_FEED, p["C1.1"], Vector2.from_xy(p["C1.1"].x, nm(BUS_Y)))
    items += track("VIN", VIN_FEED, p["D1.1"], Vector2.from_xy(p["D1.1"].x, nm(BUS_Y)))
    stops = sorted({q.x for q in pin1} | {join.x})
    items += bottom("DATA", SIGNAL, *[Vector2.from_xy(x, nm(DATA_Y)) for x in stops])
    items += bottom("DATA", SIGNAL, join, Vector2.from_xy(join.x, nm(DATA_Y)))
    for q in pin1:
        items += bottom("DATA", SIGNAL, Vector2.from_xy(q.x, nm(DATA_Y)), q)
    # The capacitor's and the TVS's ground pads: vias in the pads, into both pours.
    for pad, dx in (("C1.2", 0.9), ("D1.2", 0.6)):
        items += [via("GND", off(p[pad], -dx)), via("GND", off(p[pad], dx))]
    return items


@pcb
def pi_servo_hat(board):
    pads, placed = {}, []
    for ref, (footprint, value, (x, y), rotation, nets, layer) in PARTS.items():
        args = {"layer": layer} if layer is not None else {}
        fp = board.place_footprint_from_library(footprint, at(x, y), Angle.from_degrees(rotation), **args)
        fp.reference_field.text.value = ref
        fp.value_field.text.value = value
        if ref in LABELS:
            fp.reference_field.text.position = at(*LABELS[ref])
            fp.reference_field.text.attributes.angle = 0
        if ref.startswith("H"):
            fp.reference_field.visible = False
        for pad in fp.definition.pads:
            if pad.number in nets:
                pad.net = Net(name=nets[pad.number])
            pads.setdefault(f"{ref}.{pad.number}", pad.position)  # absolute, nm: route from these
        placed.append(fp)
    board.update_items(placed)

    logic, join = half_duplex(pads)
    copper = [*uart(pads), *logic_supply(pads), *logic, *ports(pads, join)]
    labels = [text("BUS SERVO HAT", 46.0, 22.0, size=1.5, thickness=0.2), text("VIN 6-12V", 50.5, 30.0),
              *(text(f"S{k + 1}", pads[f"J{k + 3}.2"].x / 1e6, 53.2) for k in range(PORTS))]
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
    c.hole_clearance = nm(0.25)
    c.silk_clearance, c.min_silk_text_height, c.min_silk_text_thickness = nm(0.15), nm(1.0), nm(0.15)
    board.set_design_rules(rules)


if __name__ == "__main__":
    pi_servo_hat()
