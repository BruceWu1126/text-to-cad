"""USB-C to UART: a WCH CH343P on a 40 x 20 mm two-layer board, with an FTDI-order header.

USB-C at the left edge is a sink (5.1k on CC1 and CC2); D+ and D- run as a pair, 0.15 mm tracks
0.25 mm apart, straight to the CH343P, passing under a USBLC6-2SC6 that clamps them to VBUS and
ground. The bridge runs from VBUS (VDD5 with its 100 nF; V3, its own 3.3 V regulator, on 1 uF) and
its VIO is tied to VDD5, so the header is 5 V logic, as on FTDI's TTL-232R-5V cable: pin 1 at the
bottom, GND, CTS, VCC (VBUS), TXD, RXD, DTR. The bridge's other modem pins (RTS, DSR, RI, DCD) and
ACT are left open, the inputs on the chip's own pull-ups. A green LED on VBUS shows the port is up;
10 uF bulk sits on VBUS at the clamp.

Everything is routed on top over two ground pours, with four short hops on the bottom where the
parts' pin orders cross: the receptacle repeats D+ and D- for the plug's two orientations, interleaved,
so B7 joins A7 under the receptacle and B6 hops to D+ through two vias; the two VBUS pairs, on
either side of the data pins, are joined under the receptacle; VBUS reaches VDD5 and the bridge's
VBUS sense pin under RXD and TXD; and DTR, whose pin sits on the far side of the chip from its
header pin, hops the four tracks of the fan. `usb_uart.kicad_dru` holds the pair to its gap,
uncoupled length and skew, joins the receptacle's ground solid to the pours and allows GCT's land
pattern its 0.17 mm between pads and locating pegs. The design rules are JLCPCB's standard 2-layer
limits; the drill/place origin is the outline's top-left corner.
"""
import math

from cadgen import pcb
from cadgen.kipy.board_types import BoardArc, BoardLayer, BoardSegment, BoardText, Net, Track, Via, Zone
from cadgen.kipy.geometry import Angle, PolygonWithHoles, PolyLineNode, Vector2
from cadgen.kipy.proto.board.board_commands_pb2 import BoardOriginType
from cadgen.kipy.proto.board.board_types_pb2 import ZoneConnectionStyle, ZoneType

WIDTH, HEIGHT, CORNER = 40.0, 20.0, 2.0  # the outline, its top-left corner on KiCad's origin (y down)
HOLES = [(12.0, 3.0), (31.0, 16.8)]  # M2.5

USB_C = "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal"
R_0603 = "Resistor_SMD:R_0603_1608Metric"
C_0402 = "Capacitor_SMD:C_0402_1005Metric"
C_0603 = "Capacitor_SMD:C_0603_1608Metric"
C_0805 = "Capacitor_SMD:C_0805_2012Metric"

# ref: footprint, value, (x, y) mm, rotation, pad -> net. Placed in this order, numbered in it.
PARTS = {
    "J1": (USB_C, "USB4105-GF-A", (3.675, 10.0), -90,
           {"A1": "GND", "B12": "GND", "A12": "GND", "B1": "GND", "SH": "GND", "A4": "VBUS", "B9": "VBUS",
            "A9": "VBUS", "B4": "VBUS", "A5": "CC1", "B5": "CC2", "A6": "D+", "B6": "D+", "A7": "D-", "B7": "D-"}),
    "R1": (R_0603, "5.1k", (10.3, 8.25), 0, {"1": "CC1", "2": "GND"}),
    "R2": (R_0603, "5.1k", (10.75, 11.75), 0, {"1": "CC2", "2": "GND"}),
    "C1": (C_0805, "10uF", (16.0, 5.85), 90, {"1": "VBUS", "2": "GND"}),
    # CH343P, QFN-16: the USB pins face the receptacle; VIO (1) is tied to VDD5 (3), 5 V logic.
    "U1": ("Package_DFN_QFN:WCH_QFN-16-1EP_3x3mm_P0.5mm_EP1.8x1.8mm", "CH343P", (21.0, 9.5), -90,
           {"1": "VBUS", "2": "GND", "3": "VBUS", "4": "TXD", "5": "RXD", "6": "+3V3", "7": "D+", "8": "D-",
            "9": "VBUS", "12": "DTR", "15": "CTS", "17": "GND"}),
    "C2": (C_0402, "100nF", (23.7, 7.12), 90, {"1": "VBUS", "2": "GND"}),
    "C3": (C_0603, "1uF", (17.35, 8.95), 180, {"1": "+3V3", "2": "GND"}),
    # USBLC6-2SC6 across the pair: D+ on I/O1 (pin 6), D- on I/O2 (pin 3), the pair under its body.
    "U2": ("Package_TO_SOT_SMD:SOT-23-6", "USBLC6-2SC6", (14.0, 10.0), 90, {"6": "D+", "2": "GND", "3": "D-", "5": "VBUS"}),
    "R3": (R_0603, "1k", (11.5, 14.5), 0, {"1": "VBUS", "2": "LED"}),
    "D1": ("LED_SMD:LED_0603_1608Metric", "green", (14.5, 14.5), 180, {"1": "GND", "2": "LED"}),
    # FTDI order from pin 1 at the bottom: GND, CTS, VCC (5 V), TXD, RXD, DTR.
    "J2": ("Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical", "FTDI", (36.0, 16.35), 180,
           {"1": "GND", "2": "CTS", "3": "VBUS", "4": "TXD", "5": "RXD", "6": "DTR"}),
    **{f"H{i + 1}": ("MountingHole:MountingHole_2.7mm_M2.5", "M2.5", at, 0, {}) for i, at in enumerate(HOLES)},
}

LABELS = {  # where a reference sits when the footprint's own place would crowd it: (x, y) mm, angle
    "J1": (4.2, 3.4, 0), "R2": (10.75, 13.2, 0), "C2": (24.75, 8.6, 0), "C3": (17.6, 7.65, 0),
    "U1": (22.4, 12.75, 0), "U2": (15.7, 12.6, 0), "R3": (11.5, 16.1, 0), "D1": (14.5, 16.1, 0),
}

SIGNAL, PAIR, POWER = 0.2, 0.15, 0.4  # mm
VIA, DRILL = 0.6, 0.3


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


def hole_keepout(x, y, radius=2.5):
    """No copper under an M2.5 screw head."""
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


def jump(net, width, a, b, *between):
    """A short hop on the bottom layer: a via at each end, the track between them on B.Cu."""
    return [via(net, a), *track(net, width, a, *between, b, layer=BoardLayer.BL_B_Cu), via(net, b)]


def usb_input(p):
    """The receptacle: ground pads to vias, CC pulled down, and its two VBUS pairs joined under its body.

    VBUS comes out on both sides of the data pins; the pairs are joined on the bottom layer under the
    receptacle, west of its pad row, where no signal runs above.
    """
    items = []
    for pad, dy in (("J1.A1", -1), ("J1.A12", 1)):  # each ground pair: a stub and a via beside it
        start = p[pad]
        items += [*track("GND", POWER, start, off(start, 0.395), off(start, 0.945, 0.55 * dy), off(start, 0.945, 0.95 * dy)),
                  via("GND", off(start, 0.945, 0.95 * dy))]
    top, bottom = p["J1.A4"], p["J1.A9"]
    # Out of each pad between its locating peg and the SBU pad beside it: 0.2 mm is what fits.
    west_top, west_bottom = off(top, -1.805, 0.19), off(bottom, -1.805, -0.19)  # a drill clear of the pegs
    items += track("VBUS", SIGNAL, top, off(top, -0.19, 0.19), west_top)
    items += track("VBUS", SIGNAL, bottom, off(bottom, -0.19, -0.19), west_bottom)
    items += jump("VBUS", SIGNAL, west_top, west_bottom)
    items += track("CC1", SIGNAL, p["J1.A5"], off(p["J1.A5"], 0.745), off(p["J1.A5"], 1.245, -0.5), p["R1.1"])
    items += track("CC2", SIGNAL, p["J1.B5"], p["R2.1"])
    for pad, dx, dy in (("R1.2", 1.075, 0), ("R2.2", 0, 1.0)):
        hole = off(p[pad], dx, dy)
        items += [*track("GND", 0.3, p[pad], hole), via("GND", hole)]
    return items, top, bottom


def usb_pair(p):
    """D+ and D- from the receptacle to the bridge, coupled at 0.4 mm, through the ESD diode's body.

    The receptacle repeats each data pin for the plug's two orientations, interleaved D-, D+, D-, D+:
    B7 (D-) joins A7 under the receptacle, west of the pad row; B6 (D+) hops to D+ through two vias.
    """
    items = []
    dp, dn = p["J1.A6"], p["J1.A7"]
    tap_p, tap_n = Vector2.from_xy(p["U2.6"].x, dp.y + nm(0.05)), Vector2.from_xy(p["U2.3"].x, dn.y - nm(0.05))
    end_p, end_n = p["U1.7"], p["U1.8"]
    near_p, near_n = Vector2.from_xy(end_p.x - nm(0.512), end_p.y + nm(0.05)), Vector2.from_xy(end_n.x - nm(0.512), end_n.y - nm(0.05))
    hop = Vector2.from_xy(nm(8.7), dp.y + nm(0.05))  # where B6's hop lands on D+
    items += track("D+", PAIR, dp, off(dp, 0.595), off(dp, 0.645, 0.05), hop, tap_p, off(near_p, -0.05), near_p, off(near_p, 0.05, -0.05), end_p)
    items += track("D-", PAIR, dn, off(dn, 0.595), off(dn, 0.645, -0.05), tap_n, off(near_n, -0.05), near_n, off(near_n, 0.05, 0.05), end_n)
    items += track("D+", PAIR, tap_p, p["U2.6"])
    items += track("D-", PAIR, tap_n, p["U2.3"])
    b7 = p["J1.B7"]
    items += track("D-", PAIR, b7, off(b7, -0.955), off(dn, -0.955), dn)  # under the receptacle
    b6 = p["J1.B6"]
    low, high = Vector2.from_xy(hop.x, b6.y + nm(0.1)), Vector2.from_xy(hop.x, nm(9.225))
    items += track("D+", PAIR, b6, Vector2.from_xy(hop.x - nm(0.1), b6.y), low)
    items += jump("D+", PAIR, low, high)
    items += track("D+", PAIR, high, hop)
    hole = off(p["U2.2"], 0, 1.013)
    items += [*track("GND", 0.3, p["U2.2"], hole), via("GND", hole)]
    return items


def bridge_supply(p, strap_top, strap_bottom):
    """VBUS along the top to the ESD diode, its 10 uF and on under RXD and TXD to VDD5 and VIO; 3.3 V decoupled."""
    items = []
    run = p["C1.1"].y
    rise = strap_top.y - run
    esd = Vector2.from_xy(p["U2.5"].x, run)
    hop_from = Vector2.from_xy(nm(18.4), run)
    hop_to = Vector2.from_xy(p["U1.2"].x, run)
    items += track("VBUS", POWER, strap_top, off(strap_top, 0.595), off(strap_top, 0.595 + rise / 1e6, -rise / 1e6), esd, p["C1.1"], hop_from)
    items += track("VBUS", POWER, esd, p["U2.5"])
    items += jump("VBUS", POWER, hop_from, hop_to)  # under RXD and TXD
    comb = nm(7.3)
    vdd5, vio = p["U1.3"], p["U1.1"]
    items += track("VBUS", SIGNAL, hop_to, Vector2.from_xy(hop_to.x, comb))
    items += track("VBUS", SIGNAL, vdd5, Vector2.from_xy(vdd5.x, comb), Vector2.from_xy(vio.x, comb))
    c2 = p["C2.1"]
    items += track("VBUS", SIGNAL, vio, Vector2.from_xy(vio.x, comb), Vector2.from_xy(c2.x - (c2.y - comb), comb), c2)
    # VBUS on to the sense pin below the chip: the bottom hop turns south under the chip's left edge.
    sense = p["U1.9"]
    under = Vector2.from_xy(sense.x, nm(11.8))
    items += track("VBUS", 0.3, Vector2.from_xy(sense.x, run), under, layer=BoardLayer.BL_B_Cu)
    items += [via("VBUS", under), *track("VBUS", 0.3, under, sense)]
    # The LED, off the lower VBUS pair.
    r3 = p["R3.1"]
    turn = Vector2.from_xy(r3.x - nm(0.375) - (r3.y - strap_bottom.y), strap_bottom.y)
    items += track("VBUS", 0.3, strap_bottom, turn, off(r3, -0.375), r3)
    items += track("LED", SIGNAL, p["R3.2"], p["D1.2"])
    # V3, the bridge's own 3.3 V, to its 1 uF.
    v3 = p["U1.6"]
    items += track("+3V3", 0.3, v3, Vector2.from_xy(p["C3.1"].x + (v3.y - p["C3.1"].y), v3.y), p["C3.1"])
    for pad, dx, dy in (("C1.2", 1.1, 0), ("C2.2", -1.0, 0), ("C3.2", 0, -1.0), ("D1.1", 1.1, 0)):
        hole = off(p[pad], dx, dy)
        items += [*track("GND", 0.3, p[pad], hole), via("GND", hole)]
    items += track("GND", 0.25, p["U1.2"], Vector2.from_xy(p["U1.2"].x, nm(8.75)))  # into the exposed pad
    items.append(via("GND", Vector2.from_xy(p["U1.17"].x, p["U1.17"].y - nm(0.15))))
    return items


def uart(p):
    """RXD, TXD, VCC and CTS to the header as a fan, DTR hopping under them to the header's far end."""
    items = []
    j = {n: p[f"J2.{n}"] for n in range(1, 7)}
    rxd, txd, cts, dtr = p["U1.5"], p["U1.4"], p["U1.15"], p["U1.12"]
    lane = {"RXD": nm(5.45), "TXD": nm(6.0), "VBUS": nm(6.55), "CTS": nm(7.1)}
    # Each track leaves the bundle at 45 degrees and lands on its pin's centre.
    def into(pin, y):
        return Vector2.from_xy(pin.x - abs(pin.y - y), y)
    corner = off(rxd, 0.2375, -0.2375)  # out of the pad's corner, clear of TXD's pad
    items += track("RXD", SIGNAL, rxd, corner, Vector2.from_xy(corner.x, lane["RXD"] + nm(0.6)),
                   Vector2.from_xy(corner.x + nm(0.6), lane["RXD"]), into(j[5], lane["RXD"]), j[5])
    items += track("TXD", SIGNAL, txd, Vector2.from_xy(txd.x, lane["TXD"] + nm(0.6)), Vector2.from_xy(txd.x + nm(0.6), lane["TXD"]),
                   into(j[4], lane["TXD"]), j[4])
    c2 = p["C2.1"]
    items += track("VBUS", POWER, c2, Vector2.from_xy(nm(24.3), c2.y), Vector2.from_xy(nm(24.3) + (c2.y - lane["VBUS"]), lane["VBUS"]),
                   into(j[3], lane["VBUS"]), j[3])
    rise = Vector2.from_xy(nm(25.0), cts.y)
    items += track("CTS", SIGNAL, cts, rise, Vector2.from_xy(rise.x + (cts.y - lane["CTS"]), lane["CTS"]), into(j[2], lane["CTS"]), j[2])
    below, above = Vector2.from_xy(nm(28.0), nm(7.7)), Vector2.from_xy(nm(28.0), nm(4.85))
    drop = Vector2.from_xy(dtr.x, nm(11.4))
    items += track("DTR", SIGNAL, dtr, drop, Vector2.from_xy(below.x - (drop.y - below.y), drop.y), below)
    items += jump("DTR", SIGNAL, below, above)  # under RXD, TXD, VCC and CTS
    items += track("DTR", SIGNAL, above, Vector2.from_xy(above.x + (above.y - j[6].y), j[6].y), j[6])
    return items


@pcb
def usb_uart(board):
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
            pads.setdefault(f"{ref}.{pad.number}", pad.position)
        placed.append(fp)
    board.update_items(placed)

    inputs, strap_top, strap_bottom = usb_input(pads)
    copper = [*inputs, *usb_pair(pads), *bridge_supply(pads, strap_top, strap_bottom), *uart(pads)]
    labels = [text("USB-UART", 22.5, 17.6, size=1.2, thickness=0.18),
              *(text(name, 38.6, pads[f"J2.{i + 1}"].y / 1e6, width=0.7) for i, name in enumerate(["GND", "CTS", "5V", "TXD", "RXD", "DTR"]))]
    board.create_items([
        *outline(WIDTH, HEIGHT, CORNER),
        *copper,
        ground_pour(BoardLayer.BL_F_Cu),
        ground_pour(BoardLayer.BL_B_Cu),
        *(hole_keepout(x, y) for x, y in HOLES),
        *labels,
    ])
    board.set_origin(BoardOriginType.BOT_DRILL, at(0, 0))

    rules = board.get_design_rules().rules
    c = rules.constraints
    c.min_clearance = c.min_track_width = nm(0.1)
    c.min_via_size, c.min_through_drill, c.min_via_annular_width = nm(0.5), nm(0.3), nm(0.13)
    c.copper_edge_clearance, c.hole_to_hole_min = nm(0.2), nm(0.5)
    c.hole_clearance = nm(0.15)
    c.silk_clearance, c.min_silk_text_height, c.min_silk_text_thickness = nm(0.15), nm(1.0), nm(0.15)
    board.set_design_rules(rules)


if __name__ == "__main__":
    usb_uart()
