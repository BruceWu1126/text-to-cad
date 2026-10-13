"""Which KiCad project a folder means: the one Backplane would open for it."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.python.support.paths import add_repo_path

add_repo_path("packages/cadgen/src")

from cadgen.kicad.project_find import canonical_project, find_projects  # noqa: E402


class ProjectFindTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)

    def project(self, relative: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
        return path

    def test_the_roots_own_project_wins(self) -> None:
        top = self.project("top.kicad_pro")
        self.project("hw/other/other.kicad_pro")
        self.assertEqual(canonical_project(self.root), top)

    def test_a_single_project_below_the_root(self) -> None:
        deep = self.project("hw/main/board/board.kicad_pro")
        self.assertEqual(canonical_project(self.root), deep)

    def test_the_shallowest_project_wins(self) -> None:
        shallow = self.project("a/x/x.kicad_pro")
        self.project("b/c/d/y.kicad_pro")
        self.assertEqual(canonical_project(self.root), shallow)

    def test_two_at_the_same_depth_are_refused_by_name(self) -> None:
        self.project("PCB/servo/servo.kicad_pro")
        self.project("PCB/hat/hat.kicad_pro")
        with self.assertRaises(ValueError) as raised:
            canonical_project(self.root)
        self.assertIn("PCB/hat/hat.kicad_pro", str(raised.exception))
        self.assertIn("PCB/servo/servo.kicad_pro", str(raised.exception))

    def test_backups_builds_hidden_and_dependencies_are_never_projects(self) -> None:
        board = self.project("PCB/servo/servo.kicad_pro")
        for skipped in ("PCB/servo/servo-backups/servo.kicad_pro", "PCB/build/old.kicad_pro", "PCB/.history/h.kicad_pro",
                        "node_modules/kit/k.kicad_pro"):
            self.project(skipped)
        self.assertEqual(find_projects(self.root), [board])
        self.assertEqual(canonical_project(self.root), board)

    def test_deeper_than_four_folders_is_not_searched(self) -> None:
        self.project("a/b/c/d/e/deep.kicad_pro")
        self.assertEqual(find_projects(self.root), [])
        with self.assertRaises(FileNotFoundError):
            canonical_project(self.root)

    def test_a_file_is_its_own_project(self) -> None:
        board = self.root / "x.kicad_pcb"
        board.write_text("", encoding="utf-8")
        self.assertEqual(canonical_project(board), board)


if __name__ == "__main__":
    unittest.main()
