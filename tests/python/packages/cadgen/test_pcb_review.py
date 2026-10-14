"""The review: what a person reviewing a board checks that KiCad's checks do not."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.python.support.paths import add_repo_path

add_repo_path("packages/cadgen/src")

from cadgen.kicad.board_index import BoardIndex, Pad, Part, Track  # noqa: E402
from cadgen.kicad.review import is_ground, net_currents, required_width, review  # noqa: E402


def pad(part, number, net, at, type="passive", name=None):
    return Pad(part=part, number=number, name=name, net=net, type=type, side="top", at=at, polygon=())


def part(ref, pads, dnp=False, fields=None, footprint=""):
    fields = {"LCSC": f"C-{ref}"} if fields is None else fields  # sourced unless a test says otherwise
    return Part(ref=ref, value="", footprint=footprint, side="top", at=pads[0].at, rotation=0.0, fields=fields, dnp=dnp,
                outline=(), pads=tuple(pads))


def resistor(ref, fields, footprint="Resistor_SMD:R_0603_1608Metric", dnp=False):
    return part(ref, [pad(ref, "1", "A", (0.0, 0.0)), pad(ref, "2", "B", (1.0, 0.0))], dnp=dnp, fields=fields, footprint=footprint)


def ic(at=(0.0, 0.0)):
    x, y = at
    return part("U2", [pad("U2", "7", "VDD", (x, y), "power_in", "VDD"), pad("U2", "8", "GND", (x + 1, y), "power_in", "GND"),
                       pad("U2", "1", "SDA", (x + 2, y), "bidirectional")])


def cap(ref, at, net="VDD", dnp=False):
    return part(ref, [pad(ref, "1", net, at), pad(ref, "2", "GND", (at[0] + 1, at[1]))], dnp=dnp)


def board(*parts, tracks=()):
    return BoardIndex(parts=tuple(parts), tracks=tuple(tracks), vias=(), zones=(), holes=(), outline=(), nets=(),
                      origin=(0.0, 0.0))


class ReviewTest(unittest.TestCase):
    def test_ground_names(self):
        for name in ("GND", "gnd", "AGND", "PGND", "GNDA", "GND_ISO", "VSS", "/Power/GND", "0V"):
            self.assertTrue(is_ground(name), name)
        for name in ("VDD", "VBUS", "SIGNAL_GND_SENSE", None, ""):
            self.assertFalse(is_ground(name), name)

    def test_a_close_capacitor_is_fine(self):
        self.assertEqual(review(board(ic(), cap("C4", (2.0, 0.0)))), ())

    def test_a_far_capacitor_is_named_with_its_distance(self):
        (found,) = review(board(ic(), cap("C4", (9.1, 0.0))))
        self.assertEqual((found.check, found.severity, found.type), ("review", "warning", "decoupling_far"))
        self.assertEqual(found.summary, "U2's VDD: the nearest decoupling capacitor, C4, is 9.1 mm away (aim for under 3 mm)")
        self.assertEqual([item.ref for item in found.items], ["#U2.7", "#C4.1"])

    def test_no_capacitor_on_a_power_input(self):
        (found,) = review(board(ic()))
        self.assertEqual(found.type, "decoupling_missing")
        self.assertEqual(found.summary, "U2's VDD has no decoupling capacitor")

    def test_ground_pins_are_never_reported(self):
        self.assertTrue(all("GND" not in found.summary for found in review(board(ic()))))

    def test_a_dnp_capacitor_decouples_nothing(self):
        (found,) = review(board(ic(), cap("C4", (2.0, 0.0), dnp=True)))
        self.assertEqual(found.type, "decoupling_missing")

    def test_a_connector_or_an_unfitted_part_is_not_reviewed(self):
        connector = part("J1", [pad("J1", "1", "+5V", (0.0, 0.0), "power_in"), pad("J1", "2", "3V3", (2.5, 0.0), "power_in")])
        self.assertEqual(review(board(connector)), ())
        unfitted = part("U2", [pad("U2", "7", "VDD", (0.0, 0.0), "power_in")], dnp=True)
        self.assertEqual(review(board(unfitted)), ())

    def test_a_board_without_power_inputs_has_nothing_to_say(self):
        connector = part("J1", [pad("J1", "1", "VBUS", (0.0, 0.0)), pad("J1", "2", "GND", (2.5, 0.0))])
        self.assertEqual(review(board(connector)), ())

    def test_a_thin_track_for_its_current(self):
        thin = Track(net="VBUS", layer="F.Cu", width=0.25, points=((0.0, 0.0), (10.0, 0.0)))
        wide = Track(net="VBUS", layer="F.Cu", width=1.0, points=((10.0, 0.0), (20.0, 0.0)))
        (found,) = review(board(tracks=(thin, wide)), {"VBUS": 2.0})
        self.assertEqual(found.type, "track_current")
        self.assertEqual(found.summary, "VBUS carries 2 A; its narrowest track is 0.25 mm, it needs about 0.78 mm")
        self.assertEqual(found.items[0].ref, "#net:VBUS")
        self.assertEqual(review(board(tracks=(wide,)), {"VBUS": 2.0}), ())
        self.assertEqual(review(board(tracks=(thin,))), ())

    def test_the_needed_width_never_reads_as_the_narrowest(self):
        thin = Track(net="VBUS", layer="F.Cu", width=0.1, points=((0.0, 0.0), (10.0, 0.0)))
        (found,) = review(board(tracks=(thin,)), {"VBUS": 0.5})
        self.assertEqual(found.summary, "VBUS carries 0.5 A; its narrowest track is 0.1 mm, it needs about 0.12 mm")

    def test_ipc_2221_width(self):
        self.assertAlmostEqual(required_width(2.0), 0.78, delta=0.02)
        self.assertAlmostEqual(required_width(1.0), 0.30, delta=0.02)

    def test_currents_are_read_from_the_project(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / "t.kicad_pro"
            project.write_text(json.dumps({"cadgen": {"net_currents": {"VBUS": 2.0, "BAD": -1}}}), encoding="utf-8")
            self.assertEqual(net_currents(project), {"VBUS": 2.0})
            for text in ('{"cadgen": []}', '{"cadgen": {"net_currents": [1]}}', '{"cadgen": {"net_currents": {"V": Infinity}}}'):
                project.write_text(text, encoding="utf-8")
                self.assertEqual(net_currents(project), {}, text)
            project.write_text("{not json", encoding="utf-8")
            self.assertEqual(net_currents(project), {})
            self.assertEqual(net_currents(Path(folder) / "missing.kicad_pro"), {})
            self.assertEqual(net_currents(None), {})

    def test_a_part_with_nothing_to_order_it_by(self):
        (found,) = review(board(resistor("R1", {"Script": "board.py:12"})))
        self.assertEqual((found.check, found.severity, found.type), ("review", "warning", "part_unsourced"))
        self.assertEqual(found.summary, "R1 has no LCSC or MPN to order it by")
        self.assertEqual([item.ref for item in found.items], ["#R1"])

    def test_an_lcsc_or_an_mpn_alone_is_enough(self):
        for fields in ({"LCSC": "C21190"}, {"MPN": "RC0603FR-071KL"}, {"lcsc": "C1"}, {" mpn ": "X"}):
            self.assertEqual(review(board(resistor("R1", fields))), (), fields)

    def test_a_number_in_a_field_the_bom_never_reads_is_no_number(self):
        # The BOM's columns are the LCSC and MPN fields: a part numbered only elsewhere has an empty row.
        for fields in ({"Mfr PN": "X"}, {"Manufacturer Part Number": "X"}, {"LCSC Part #": "C1"}):
            (found,) = review(board(resistor("R1", fields)))
            self.assertEqual(found.type, "part_unsourced", fields)
        self.assertEqual(len(review(board(resistor("R1", {"LCSC": "  "})))), 1)  # blank is nothing

    def test_every_part_without_a_number_is_one_finding(self):
        parts = [resistor(f"R{n}", {}) for n in (10, 2, 1)] + [resistor("C1", {"LCSC": "C1"})]
        (found,) = review(board(*parts))
        self.assertEqual(found.summary, "3 parts have no LCSC or MPN to order them by: R1, R2, R10")
        self.assertEqual([item.ref for item in found.items], ["#R1", "#R2", "#R10"])
        (many,) = review(board(*[resistor(f"R{n}", {}) for n in range(1, 13)]))
        self.assertEqual(many.summary, "12 parts have no LCSC or MPN to order them by: R1, R2, R3, R4, R5, R6, R7, R8 and 4 more")
        self.assertEqual(len(many.items), 12)

    def test_holes_test_points_fiducials_and_unfitted_parts_are_not_ordered(self):
        for ref in ("H1", "MH2", "TP3", "FID1", "LOGO1"):
            self.assertEqual(review(board(resistor(ref, {}))), (), ref)
        self.assertEqual(review(board(resistor("R1", {}, dnp=True))), ())
        padless = Part(ref="G1", value="", footprint="", side="top", at=(0.0, 0.0), rotation=0.0, fields={}, dnp=False, outline=(), pads=())
        self.assertEqual(review(board(padless)), ())

    def test_a_part_the_bom_leaves_out_is_not_ordered(self):
        from cadgen import build123d as bd
        from cadgen.kicad import sexpr
        from cadgen.kicad.board_index import read_index
        from cadgen.kicad.design import Board
        from cadgen.kicad.project import project_texts
        from tests.python.support.kicad_library import write_test_library

        with tempfile.TemporaryDirectory() as folder:
            with bd.BuildSketch() as outline:
                bd.Rectangle(20, 20)
            pcb = Board(outline=outline.sketch, libraries=[write_test_library(Path(folder))])
            jumper = pcb.part("Test:FIDUCIAL", footprint="Test:ONE_PAD", ref="JP1")  # (in_bom no), a ref the rule can't skip
            resistor_ = pcb.part("Test:R", footprint="Test:R_0603", ref="R1")
            pcb.no_connect(jumper[1], resistor_[1], resistor_[2])
            pcb.place(jumper, at=(-5, 0))
            pcb.place(resistor_, at=(5, 0))
            index = read_index(sexpr.parse(project_texts(pcb, name="b").pcb), project=None)
        self.assertEqual([(part.ref, part.in_bom) for part in index.parts], [("JP1", False), ("R1", True)])
        (found,) = review(index)
        self.assertEqual(found.summary, "R1 has no LCSC or MPN to order it by")

    def test_one_part_number_on_two_footprints(self):
        (found,) = review(board(resistor("R1", {"LCSC": "C21190"}), resistor("R4", {"LCSC": "C21190"}, footprint="Resistor_SMD:R_0402_1005Metric"),
                                resistor("R2", {"LCSC": "C21190"})))
        self.assertEqual(found.type, "mpn_footprints")
        self.assertEqual(found.summary, "C21190 is on R1, R2 (R_0603_1608Metric) and R4 (R_0402_1005Metric)")
        self.assertEqual([item.ref for item in found.items], ["#R1", "#R2", "#R4"])
        self.assertEqual(review(board(resistor("R1", {"LCSC": "C21190"}), resistor("R2", {"LCSC": "C21190"}))), ())

    def test_pcb_review_is_advice_never_a_gate(self):
        from unittest import mock

        from cadgen import pcb
        from cadgen.kicad.phrasing import Finding, FindingItem

        for severity in ("warning", "error"):
            found = Finding(check="review", severity=severity, type="decoupling_far", description="far",
                            items=(FindingItem(text="C1", at=(1.0, 2.5)),), summary="C1 is far")
            with tempfile.TemporaryDirectory() as folder, mock.patch("cadgen.kicad.check.reviewed", return_value=[found]):
                board = Path(folder) / "b.kicad_pcb"
                board.write_text("(kicad_pcb)", encoding="utf-8")
                result = pcb.review(board)
                self.assertTrue(result.ok, (severity, result.issues))
                self.assertEqual([(issue.code, issue.severity, issue.message) for issue in result.issues],
                                 [("review.decoupling_far", severity, "C1 is far: C1 at (1, 2.5)")])

    def test_pcb_review_takes_only_a_board(self):
        from cadgen import pcb

        with tempfile.TemporaryDirectory() as folder:
            schematic = Path(folder) / "b.kicad_sch"
            schematic.write_text("(kicad_sch)", encoding="utf-8")
            self.assertFalse(pcb.review(schematic).ok)
            self.assertFalse(pcb.review(Path(folder) / "missing.kicad_pcb").ok)

if __name__ == "__main__":
    unittest.main()
