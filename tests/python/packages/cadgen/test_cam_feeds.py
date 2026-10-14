"""Feeds and speeds: textbook numbers, chip thinning, clamps that say so, and a tap's feed exactly rpm x pitch."""

from __future__ import annotations

import math
import unittest

from tests.python.support.cam_fixtures import heavy_at_import, value_error

from cadgen._internal.cam.feeds import MATERIAL_TABLE, chip_thinning, chipload_at, feeds_for
from cadgen._internal.cam.job import MATERIALS, Machine, Material, Tool

MILL = Machine("mill", 3, "grbl", max_rpm=30000, max_feed_mm_min=10000)
AL = Material("aluminum-6061")
ENDMILL = Tool("T1", 1, "flat_endmill", diameter_mm=6, flutes=3)


class FeedsTest(unittest.TestCase):
    def test_the_table_covers_every_material_a_job_can_name(self):
        self.assertEqual(sorted(MATERIAL_TABLE), sorted(MATERIALS))

    def test_a_6mm_three_flute_carbide_endmill_in_6061_gets_textbook_numbers(self):
        # Tool makers' charts: coated carbide in 6061 at about 300 to 600 m/min and 0.03 to 0.05 mm per tooth at
        # 6 mm, so about 21,000 rpm and 2,500 mm/min at their middle (450 m/min would be 23,900 rpm).
        feeds = feeds_for(ENDMILL, AL, MILL)
        self.assertAlmostEqual(feeds.rpm, 21000, delta=2100)
        self.assertAlmostEqual(feeds.feed_mm_min, 2500, delta=250)
        self.assertAlmostEqual(feeds.feed_mm_min, feeds.rpm * 3 * feeds.chipload_mm)
        self.assertAlmostEqual(feeds.plunge_mm_min, 0.3 * feeds.feed_mm_min)
        self.assertEqual(feeds.findings, ())

    def test_chip_load_is_interpolated_between_diameter_bands(self):
        bands = ((3.0, 0.02), (6.0, 0.04))
        self.assertAlmostEqual(chipload_at(bands, 4.5), 0.03)
        self.assertAlmostEqual(chipload_at(bands, 1.5), 0.01)    # under the smallest: in proportion
        self.assertAlmostEqual(chipload_at(bands, 12.0), 0.04)   # over the largest: held

    def test_radial_chip_thinning_raises_the_chip_load_under_half_engagement(self):
        self.assertEqual(chip_thinning(0.5), 1.0)
        self.assertAlmostEqual(chip_thinning(0.1), 1 / (2 * math.sqrt(0.1 * 0.9)))
        self.assertEqual(chip_thinning(0.001), 3.0)
        plain, thinned = feeds_for(ENDMILL, AL, MILL), feeds_for(ENDMILL, AL, MILL, radial_engagement=0.1)
        self.assertAlmostEqual(thinned.feed_mm_min / plain.feed_mm_min, chip_thinning(0.1))

    def test_hss_runs_slower_than_carbide(self):
        hss = feeds_for(Tool("T1", 1, "flat_endmill", "hss", diameter_mm=6, flutes=3), AL, MILL)
        self.assertAlmostEqual(hss.rpm / feeds_for(ENDMILL, AL, MILL).rpm, 0.3, places=4)
        # A tap's speed factor is already a tap's own (HSS) speed: it is not derated twice.
        tap = {"diameter_mm": 6, "size": {"pitch_mm": 1.0}}
        self.assertEqual(feeds_for(Tool("T3", 3, "tap", "hss", **tap), AL, MILL).rpm,
                         feeds_for(Tool("T3", 3, "tap", **tap), AL, MILL).rpm)

    def test_a_counterbore_feeds_half_a_drills_feed_per_rev(self):
        drill = feeds_for(Tool("T2", 2, "drill", diameter_mm=11), AL, MILL)
        cbore = feeds_for(Tool("T5", 5, "counterbore", diameter_mm=11, size={"pilot_diameter_mm": 6.6}), AL, MILL)
        self.assertEqual(cbore.rpm, drill.rpm)
        self.assertAlmostEqual(cbore.feed_mm_min / cbore.rpm, 0.5 * drill.feed_mm_min / drill.rpm)

    def test_a_machine_too_slow_clamps_rpm_and_says_so(self):
        router = Machine("router", 3, "grbl", max_rpm=12000, max_feed_mm_min=10000)
        feeds = feeds_for(ENDMILL, AL, router, where="operations[0]")
        self.assertEqual(feeds.rpm, 12000)
        self.assertAlmostEqual(feeds.feed_mm_min, 12000 * 3 * feeds.chipload_mm)
        (found,) = feeds.findings
        self.assertEqual((found["severity"], found["type"]), ("warning", "rpm_clamped"))
        self.assertEqual(found["summary"], "operations[0]: T1 is capped at the machine's 12,000 rpm; the tool and material "
                                           "call for 21,221 rpm, so the cut runs slower at the same chip load")
        self.assertNotIn("\u2014", found["summary"] + found["description"])
        # A feed over the machine's limit says what it was and what that does to the chip.
        (fed,) = feeds_for(ENDMILL, AL, Machine("mill", 3, "grbl", max_feed_mm_min=1000)).findings
        self.assertEqual(fed["summary"], "T1 is capped at the machine's 1,000 mm/min; the tool and material call for a "
                                         "2,547 mm/min feed, so the cut runs slower with a thinner chip")
        self.assertIn("set a lower rpm on the operation", fed["description"])

    def test_an_override_wins_and_is_still_clamped(self):
        feeds = feeds_for(ENDMILL, AL, MILL, rpm=40000, feed_mm_min=15000)
        self.assertEqual((feeds.rpm, feeds.feed_mm_min), (30000, 10000))
        self.assertEqual([item["type"] for item in feeds.findings], ["rpm_clamped", "feed_clamped"])
        self.assertEqual(feeds_for(ENDMILL, AL, MILL, rpm=9000, plunge_mm_min=100).plunge_mm_min, 100)

    def test_a_tap_feeds_exactly_rpm_times_pitch(self):
        tap = Tool("T3", 3, "tap", diameter_mm=6, size={"pitch_mm": 1.0})
        feeds = feeds_for(tap, AL, MILL)
        self.assertEqual(feeds.feed_mm_min, feeds.rpm * 1.0)
        slow = feeds_for(tap, AL, Machine("mill", 3, "grbl", max_feed_mm_min=500))
        self.assertEqual((slow.rpm, slow.feed_mm_min), (500, 500))
        self.assertEqual([item["type"] for item in slow.findings], ["feed_clamped"])
        self.assertIn("rpm x pitch", value_error(self, feeds_for, tap, AL, MILL, rpm=600, feed_mm_min=700))

    def test_rpm_is_whole_and_a_tap_feeds_exactly_the_rounded_rpm_times_pitch(self):
        tap = Tool("T3", 3, "tap", diameter_mm=8, size={"pitch_mm": 1.25})
        feeds = feeds_for(tap, AL, MILL)
        self.assertEqual(feeds.rpm, int(feeds.rpm))
        self.assertEqual(feeds.feed_mm_min, feeds.rpm * 1.25)
        slow = feeds_for(tap, AL, Machine("mill", 3, "grbl", max_feed_mm_min=1000))
        self.assertEqual((slow.rpm, slow.feed_mm_min), (800, 1000))
        self.assertEqual(feeds_for(ENDMILL, AL, MILL, rpm=9000.4).rpm, 9000)
        # A feed stated as rpm x pitch at a speed the machine cannot reach follows the slower spindle.
        clamped = feeds_for(tap, AL, Machine("mill", 3, "grbl", max_rpm=500), rpm=800, feed_mm_min=1000)
        self.assertEqual((clamped.rpm, clamped.feed_mm_min), (500, 625))
        self.assertEqual([item["type"] for item in clamped.findings], ["rpm_clamped", "feed_clamped"])

    def test_plunge_is_not_raised_by_chip_thinning_and_a_face_mill_has_four_teeth(self):
        plain, thinned = feeds_for(ENDMILL, AL, MILL), feeds_for(ENDMILL, AL, MILL, radial_engagement=0.1)
        self.assertEqual(thinned.plunge_mm_min, plain.plunge_mm_min)
        face = feeds_for(Tool("T5", 5, "face_mill", diameter_mm=50), AL, MILL)
        self.assertAlmostEqual(face.feed_mm_min, face.rpm * 4 * face.chipload_mm)

    def test_a_drill_feeds_per_revolution_and_plunges_at_its_feed(self):
        drill = Tool("T2", 2, "drill", diameter_mm=6)
        feeds = feeds_for(drill, AL, MILL)
        self.assertAlmostEqual(feeds.feed_mm_min, feeds.rpm * feeds.chipload_mm)
        self.assertEqual(feeds.plunge_mm_min, feeds.feed_mm_min)

    def test_a_custom_material_uses_its_own_numbers(self):
        bronze = Material("bronze", surface_m_min=90, chipload_mm=0.03)
        feeds = feeds_for(ENDMILL, bronze, MILL)
        self.assertEqual(feeds.rpm, round(1000 * 90 / (math.pi * 6)))
        self.assertAlmostEqual(feeds.chipload_mm, 0.03)

    def test_feeds_and_tools_are_stdlib_only_at_import(self):
        self.assertEqual(heavy_at_import("cadgen._internal.cam.feeds", "cadgen._internal.cam.tools"), "")


if __name__ == "__main__":
    unittest.main()
