"""The toolpath format: moves, timing, arc fitting, canned cycles and linking.

Stdlib only: the moves are built by hand.
"""

from __future__ import annotations

import math
import unittest

from tests.python.support.cam_fixtures import heavy_at_import, value_error

from cadgen._internal.cam.toolpath import (
    PLUNGE, Cycle, Heights, Helix, Move, Ramp, Toolpath, expand_cycle, fit_arcs, link, move_seconds, rapids_below,
    walk,
)


def polygon(radius: float, sides: int, z: float = 0.0) -> list[tuple[float, float, float]]:
    """A closed regular polygon round the origin, starting and ending at (radius, 0)."""
    return [(radius * math.cos(2 * math.pi * k / sides), radius * math.sin(2 * math.pi * k / sides), z)
            for k in range(sides + 1)]


class Moves(unittest.TestCase):
    def test_a_move_is_frozen_hashable_and_writes_the_same_repr_for_the_same_place(self):
        a = Move("feed", (0.1 + 0.2, -0.0, 1), feed=600, op=2)
        b = Move("feed", (0.3, 0.0, 1.0), feed=600.0, op=2)
        self.assertEqual(a, b)
        self.assertEqual(hash(a), hash(b))
        self.assertEqual(repr(a), "Move(kind='feed', xyz=(0.3, 0.0, 1.0), feed=600.0, op=2, t=0.0, center=None, "
                                  "cycle=None, dwell_s=0.0)")
        with self.assertRaises(AttributeError):
            a.kind = "rapid"

    def test_a_move_that_cannot_be_run_is_refused_when_made(self):
        self.assertIn("has no feed", value_error(self, Move, "rapid", (0, 0, 0), 500))
        self.assertIn("needs its centre", value_error(self, Move, "arc_cw", (0, 0, 0), 500))
        self.assertIn("needs a peck_mm", value_error(self, Cycle, "peck", 2, -5))
        self.assertIn("under the R plane", value_error(self, Cycle, "drill", 2, 5))

    def test_the_format_is_stdlib_only_at_import(self):
        self.assertEqual(heavy_at_import("cadgen._internal.cam.toolpath"), "")


class Measuring(unittest.TestCase):
    def test_time_length_and_bounds_of_a_known_path_with_a_helix(self):
        helix = math.hypot(math.pi * 10, 1)    # half a turn at r=10, dropping 1 mm
        path = Toolpath(0, "T1", 10000, "flood", (0, 0, 5), [
            Move("rapid", (10, 0, 5)),
            Move("plunge", (10, 0, 0), 100),
            Move("arc_ccw", (-10, 0, -1), 600, center=(0, 0)),
            Move("arc_ccw", (10, 0, -2), 600, center=(0, 0)),
            Move("feed", (10, 10, -2), 600, dwell_s=0.5),
        ])
        seconds = 10 / 6000 * 60 + 5 / 100 * 60 + 2 * helix / 600 * 60 + 10 / 600 * 60 + 0.5
        self.assertAlmostEqual(path.seconds(6000), seconds, places=9)
        timed = path.timed(6000, t0=100)
        self.assertAlmostEqual(timed.moves[0].t, 100.1, places=6)
        self.assertAlmostEqual(timed.moves[-1].t, 100 + seconds, places=5)
        lengths = path.lengths()
        self.assertAlmostEqual(lengths.cut_mm, 5 + 2 * helix + 10, places=9)
        self.assertAlmostEqual(lengths.rapid_mm, 10, places=9)
        self.assertEqual(path.bounds(), ((-10, -10, -2), (10, 10, 5)))

    def test_an_arc_whose_ends_sit_at_different_radii_is_refused(self):
        message = value_error(self, Toolpath, 0, "T1", 1000, "off", (10, 0, 0),
                              [Move("arc_cw", (0, -9, 0), 500, center=(0, 0))])
        self.assertIn("moves[0] (arc_cw): the arc starts 10.0000 mm from its centre and ends 9.0000", message)


class Arcs(unittest.TestCase):
    def test_a_64_gon_fits_back_to_two_half_circle_arcs_within_tol(self):
        points = polygon(20, 64)
        moves = fit_arcs(points, 0.05, feed=800)
        self.assertEqual([(move.kind, move.xyz, move.center) for move in moves],
                         [("arc_ccw", (-20, 0, 0), (0, 0)), ("arc_ccw", (20, 0, 0), (0, 0))])
        # The chord of a 64-gon of radius 20 sags 0.024 mm from the circle: under that tolerance, no arc.
        tight = fit_arcs(points, 0.01, feed=800)
        self.assertEqual({move.kind for move in tight}, {"feed"})
        self.assertEqual(len(tight), 64)

    def test_clockwise_and_helical_arcs_fit_too(self):
        turn = [(10 * math.cos(-math.pi * k / 32), 10 * math.sin(-math.pi * k / 32), -k / 32) for k in range(33)]
        moves = fit_arcs(turn, 0.05, feed=800)
        self.assertEqual([(move.kind, move.xyz) for move in moves], [("arc_cw", (-10, 0, -1))])

    def test_a_square_stays_four_lines_and_a_straight_run_is_one(self):
        square = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0), (0, 0, 0)]
        self.assertEqual([(move.kind, move.xyz) for move in fit_arcs(square, 0.05, feed=800)],
                         [("feed", (10, 0, 0)), ("feed", (10, 10, 0)), ("feed", (0, 10, 0)), ("feed", (0, 0, 0))])
        run = [(x, 0, 0) for x in range(11)]
        self.assertEqual([move.xyz for move in fit_arcs(run, 0.05, feed=800)], [(10, 0, 0)])

    def test_fitting_is_deterministic(self):
        points = polygon(7.3, 50, z=-1.25)
        self.assertEqual(repr(fit_arcs(points, 0.02, feed=500)), repr(fit_arcs(list(points), 0.02, feed=500)))


class Cycles(unittest.TestCase):
    def test_a_peck_cycle_expands_to_the_moves_g83_makes(self):
        cycle = Move("cycle", (5, 5, 2), 100, op=3, cycle=Cycle("peck", r=2, z=-5, peck_mm=2))
        moves = expand_cycle(cycle, (0, 0, 10), peck_clearance_mm=0.5)
        self.assertEqual([(move.kind, move.xyz[2]) for move in moves], [
            ("rapid", 10), ("rapid", 2),
            ("plunge", 0), ("rapid", 2), ("rapid", 0.5),
            ("plunge", -2), ("rapid", 2), ("rapid", -1.5),
            ("plunge", -4), ("rapid", 2), ("rapid", -3.5),
            ("plunge", -5), ("rapid", 2),
        ])
        self.assertEqual({move.xyz[:2] for move in moves}, {(5, 5)})
        self.assertEqual({move.op for move in moves}, {3})
        rapid = 5 * math.sqrt(2) + 8 + (2 + 4 + 6 + 7) + (1.5 + 3.5 + 5.5)
        self.assertAlmostEqual(move_seconds((0, 0, 10), cycle, rapid_mm_min=6000),
                               rapid / 6000 * 60 + (2 + 2.5 + 2.5 + 1.5) / 100 * 60, places=9)

    def test_a_cycle_returns_to_its_r_plane_or_where_it_started(self):
        cycle = Move("cycle", (0, 0, 7), 100, cycle=Cycle("drill", r=2, z=-5))
        self.assertIn("returns to its R plane (2) or to the height it started from (10), not 7",
                      value_error(self, expand_cycle, cycle, (0, 0, 10)))

    def test_a_tap_feeds_at_rpm_times_pitch(self):
        tap = Move("cycle", (0, 0, 2), 900, cycle=Cycle("tap", r=2, z=-8, pitch_mm=1))
        self.assertIn("feeds at rpm x pitch", value_error(self, Toolpath, 0, "T3", 800, "off", (0, 0, 10), [tap]))
        self.assertEqual(Toolpath(0, "T3", 900, "off", (0, 0, 10), [tap]).lengths().cut_mm, 20)


class Linking(unittest.TestCase):
    HEIGHTS = Heights.above(0, clearance_mm=10, retract_mm=3)

    def assert_no_low_rapids(self, start, moves):
        self.assertEqual(rapids_below(moves, start=start, z=self.HEIGHTS.retract), [])

    def test_links_travel_at_the_retract_plane_only_when_told_it_is_clear(self):
        start, end = (0, 0, -4), (30, 0, -4)
        near = link(start, end, heights=self.HEIGHTS, retract_is_clear=True, feed=200, op=1)
        self.assertEqual([(move.kind, move.xyz) for move in near],
                         [("rapid", (0, 0, 3)), ("rapid", (30, 0, 3)), ("plunge", (30, 0, -4))])
        far = link(start, end, heights=self.HEIGHTS, retract_is_clear=False, feed=200, op=1)
        self.assertEqual([move.xyz for move in far], [(0, 0, 10), (30, 0, 10), (30, 0, 3), (30, 0, -4)])
        for moves in (near, far):
            self.assert_no_low_rapids(start, moves)

    def test_a_declared_clear_height_is_reached_straight_down_never_sideways(self):
        start, end = (0, 0, -6), (12, 5, -6)
        moves = link(start, end, heights=self.HEIGHTS, retract_is_clear=True, feed=200, op=0, rapid_to=-3.5)
        self.assertEqual([(move.kind, move.xyz) for move in moves],
                         [("rapid", (0, 0, 3)), ("rapid", (12, 5, 3)), ("rapid", (12, 5, -3.5)), ("plunge", (12, 5, -6))])
        self.assert_no_low_rapids(start, moves)
        self.assertIn("rapid_to (-7) must be between", value_error(
            self, link, start, end, heights=self.HEIGHTS, retract_is_clear=True, feed=200, op=0, rapid_to=-7))
        # The checker itself sees a sideways rapid at depth.
        self.assertEqual(rapids_below([Move("rapid", (12, 5, -6))], start=start, z=3), [0])

    def test_ramp_and_helix_entries_end_at_the_cut_and_stay_under_their_angle(self):
        start, end = (40, 40, 5), (0, 0, -5)
        for entry in (Ramp((10, 0), 5), Helix((-4, 0), 3, cw=True), PLUNGE):
            moves = link(start, end, heights=self.HEIGHTS, retract_is_clear=True, feed=300, op=0, entry=entry)
            self.assertEqual(moves[-1].xyz, end)
            self.assert_no_low_rapids(start, moves)
            Toolpath(0, "T1", 10000, "off", start, moves)    # arcs and all, it runs
            if entry is PLUNGE:
                continue
            for here, move in walk(moves, start):
                if move.kind != "rapid":
                    drop = here[2] - move.xyz[2]
                    across = (abs(math.pi * 4) if move.kind.startswith("arc")
                              else math.hypot(move.xyz[0] - here[0], move.xyz[1] - here[1]))
                    self.assertLessEqual(math.degrees(math.atan2(drop, across)), entry.angle_deg + 1e-6)


if __name__ == "__main__":
    unittest.main()
