"""`cadgen pcb snapshot --job` replays a board view as a Quick Edit saved it: its window, its Display, its ink.

A Quick Edit on a board writes a job packet (`cadgen.viewer.prompt_files`): each view a plan-view
camera (the window the board view showed, in the plot's plan frame, y up) and the board's Display,
the annotation once, its marks on the board in the sheet's millimetres (y down), and Measure's
results in the same frame. This renders such a packet through the real Python -> browser path and
reads the PIXELS back: the window frames what the camera names, and the ink lands on the board
where it was drawn — from below, at its mirrored place, as the view drew it.

The board is the test board (`tests/python/support/kicad_board.py`): no KiCad, no corpus model.
One browser, one packet.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from tests.python.support.cad_test_roots import ClassCadRoots
from tests.python.support.kicad_board import write_board
from tests.python.support.paths import add_repo_path
from tests.python.support.png import read_png

add_repo_path("packages/cadgen/src")

SIZE = (400, 300)
# The whole 40 x 30 mm sheet in a 400 x 300 picture: the plan view centred on the sheet's middle
# (20, -15: y up), 16 mm above and below. 9.375 px per mm, the sheet's corner at (12.5, 9.375).
PLAN = {"target": [20, -15, 0], "position": [20, -15, 60], "up": [0, 1, 0], "orthographicHalfHeight": 16}
SCALE, OFFSET_X, OFFSET_Y = 300 / 32, 200 - 20 * 300 / 32, 150 - 15 * 300 / 32
MAGENTA = (255, 0, 255)


def on_screen(x: float, y: float) -> tuple[int, int]:
    """A sheet point (y down) where the plan view above draws it, seen from the top."""
    return round(x * SCALE + OFFSET_X), round(y * SCALE + OFFSET_Y)


def near(pixel, other, tolerance=60) -> bool:
    return sum(abs(a - b) for a, b in zip(pixel, other)) <= tolerance


class BoardJobReplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._roots = ClassCadRoots(prefix="pcb-snapshot-")
        workspace = cls._roots.cad_root
        board = write_board(workspace)
        # The ink a person drew across the board's top-left, on the sheet; a dimension along VBUS.
        annotation = workspace / "board.annotation.json"
        annotation.write_text(json.dumps({"units": "mm", "parts": {}, "marks": [
            {"id": "1", "kind": "stroke", "part": None, "points": [3, 3, 0, 11, 3, 0], "color": "#ff00ff", "width": 14}]}), encoding="utf-8")
        measurement = {"start": [5, 15, 0], "end": [34.175, 15, 0], "label": "29.18 mm", "color": "#00ff00"}

        def job(name: str, display: dict) -> dict:
            return {"input": str(board), "camera": PLAN, "display": display,
                    "outputs": [{"path": f"{name}.png", "width": SIZE[0], "height": SIZE[1]}]}

        packet = workspace / "board.snapshot.json"
        packet.write_text(json.dumps({"annotation": str(annotation), "measurements": [measurement], "jobs": [
            job("top", {"mode": "board", "side": "top", "poured": True}),
            job("below", {"mode": "copper", "side": "bottom", "poured": False}),
        ]}), encoding="utf-8")

        from cadgen.pcb import snapshot

        cls.result = snapshot(job=packet)
        cls.images = {Path(file.path).stem: read_png((workspace / Path(file.path).name).read_bytes()) for file in cls.result.files}

    @classmethod
    def tearDownClass(cls) -> None:
        cls._roots.cleanup()

    def test_each_view_is_written_at_its_size(self) -> None:
        self.assertTrue(self.result.ok)
        self.assertEqual(["below", "top"], sorted(self.images))
        for name, image in self.images.items():
            self.assertEqual(SIZE, (image.width, image.height), name)

    def test_the_ink_lands_on_the_board_where_it_was_drawn_and_mirrored_from_below(self) -> None:
        top, below = self.images["top"], self.images["below"]
        x, y = on_screen(7, 3)
        mirrored = round((40 - 7) * SCALE + OFFSET_X)
        self.assertTrue(near(top.pixel(x, y), MAGENTA), f"the stroke where it was drawn: {top.pixel(x, y)}")
        self.assertFalse(near(top.pixel(mirrored, y), MAGENTA), f"and nowhere else: {top.pixel(mirrored, y)}")
        self.assertTrue(near(below.pixel(mirrored, y), MAGENTA), f"from below, at its mirrored place: {below.pixel(mirrored, y)}")
        self.assertFalse(near(below.pixel(x, y), MAGENTA), f"not where the top view had it: {below.pixel(x, y)}")

    def test_the_window_is_the_camera_s_and_the_measurement_is_drawn_over_it(self) -> None:
        top = self.images["top"]
        # The plan view frames the sheet with a millimetre to spare above and below: the corners are the surround.
        self.assertTrue(near(top.pixel(2, 2), (255, 255, 255), 30), f"the light surround: {top.pixel(2, 2)}")
        green = sum(1 for yy in range(top.height) for xx in range(top.width) if near(top.pixel(xx, yy), (0, 255, 0), 90))
        self.assertGreater(green, 40, "the dimension along VBUS")
