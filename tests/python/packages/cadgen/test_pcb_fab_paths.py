"""Where a board's manufacturing files land: the project's build/ folder, as Backplane and KiStack keep them."""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.python.support.paths import add_repo_path

add_repo_path("packages/cadgen/src")

from cadgen._internal.fab_door import _destination  # noqa: E402
from cadgen.metadata import FabExportDecl, fab_build_path, fab_output_path  # noqa: E402

BOARD = Path("/work/PCB/servo/servo.kicad_pcb")
SCRIPT = Path("/work/src/servo.py")


class FabPathsTest(unittest.TestCase):
    def test_a_boards_files_land_in_its_build_folder(self) -> None:
        for fmt, name in (("gerber", "servo.gerbers.zip"), ("bom", "servo.bom.csv"), ("pos", "servo.pos.csv")):
            self.assertEqual(fab_build_path(BOARD, fmt), Path("/work/PCB/servo/build") / name)
            self.assertEqual(fab_output_path(SCRIPT, FabExportDecl(fmt), BOARD), Path("/work/PCB/servo/build") / name)

    def test_out_still_wins_resolved_against_the_script(self) -> None:
        decl = FabExportDecl("gerber", out="../fab/servo.zip")
        self.assertEqual(fab_output_path(SCRIPT, decl, BOARD), Path("/work/fab/servo.zip").resolve())

    def test_a_harness_bom_stays_beside_its_document(self) -> None:
        cable = Path("/work/HARNESS/cable.harness.yml")
        self.assertEqual(fab_output_path(SCRIPT, FabExportDecl("bom"), cable), Path("/work/HARNESS/cable.bom.csv").resolve())
        self.assertEqual(_destination(cable, "bom", None), Path("/work/HARNESS/cable.bom.csv"))

    def test_the_door_writes_where_the_decorator_does(self) -> None:
        self.assertEqual(_destination(BOARD, "gerber", None), Path("/work/PCB/servo/build/servo.gerbers.zip"))


if __name__ == "__main__":
    unittest.main()
