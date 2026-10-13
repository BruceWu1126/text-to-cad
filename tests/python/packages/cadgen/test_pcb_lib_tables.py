"""A generated project's lib tables: KiCad finds the board's project libraries when a person opens it."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.python.support.paths import add_repo_path

add_repo_path("packages/cadgen/src")

from cadgen.kicad.lib_tables import lib_tables, write_lib_tables  # noqa: E402


class LibTablesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.parts = self.root / "src" / "parts"
        (self.parts / "parts.pretty").mkdir(parents=True)
        (self.parts / "parts.kicad_sym").write_text("(kicad_symbol_lib)\n", encoding="utf-8")
        (self.parts / "connectors.kicad_symdir").mkdir()
        (self.parts / "notes.txt").write_text("not a library\n", encoding="utf-8")
        self.project = self.root / "PCB" / "servo"

    def test_each_project_library_is_a_row_relative_to_the_project(self) -> None:
        tables = lib_tables([self.parts], self.project)
        self.assertEqual(sorted(tables), ["fp-lib-table", "sym-lib-table"])
        self.assertEqual(
            tables["sym-lib-table"],
            "(sym_lib_table\n"
            "  (version 7)\n"
            '  (lib (name "connectors")(type "KiCad")(uri "${KIPRJMOD}/../../src/parts/connectors.kicad_symdir")(options "")(descr "project library"))\n'
            '  (lib (name "parts")(type "KiCad")(uri "${KIPRJMOD}/../../src/parts/parts.kicad_sym")(options "")(descr "project library"))\n'
            ")\n",
        )
        self.assertEqual(
            tables["fp-lib-table"],
            "(fp_lib_table\n"
            "  (version 7)\n"
            '  (lib (name "parts")(type "KiCad")(uri "${KIPRJMOD}/../../src/parts/parts.pretty")(options "")(descr "project library"))\n'
            ")\n",
        )

    def test_no_project_libraries_means_no_tables(self) -> None:
        self.assertEqual(lib_tables([], self.project), {})
        empty = self.root / "empty"
        empty.mkdir()
        self.assertEqual(lib_tables([empty], self.project), {})

    def test_only_the_kind_a_folder_has_is_written(self) -> None:
        footprints = self.root / "fp"
        (footprints / "mine.pretty").mkdir(parents=True)
        self.assertEqual(sorted(lib_tables([footprints], self.project)), ["fp-lib-table"])

    def test_a_missing_folder_is_skipped(self) -> None:
        self.assertEqual(lib_tables([self.root / "nowhere"], self.project), {})

    def test_a_board_alone_in_its_folder_writes_and_later_removes_its_tables(self) -> None:
        self.project.mkdir(parents=True)
        (self.project / "servo.kicad_pro").write_text("{}", encoding="utf-8")
        written, note = write_lib_tables(self.project, "servo", lib_tables([self.parts], self.project))
        self.assertEqual(([path.name for path in written], note), (["sym-lib-table", "fp-lib-table"], None))
        written, note = write_lib_tables(self.project, "servo", {})
        self.assertEqual((written, note), ([], None))
        self.assertFalse((self.project / "sym-lib-table").exists())

    def test_a_folder_shared_with_another_board_is_left_alone(self) -> None:
        # The tables are the folder's: one board's build must not write or remove another's.
        self.project.mkdir(parents=True)
        for stem in ("servo", "hat"):
            (self.project / f"{stem}.kicad_pro").write_text("{}", encoding="utf-8")
        (self.project / "sym-lib-table").write_text('(sym_lib_table (lib (descr "project library")))\n', encoding="utf-8")
        written, note = write_lib_tables(self.project, "servo", {})
        self.assertEqual(written, [])
        self.assertTrue((self.project / "sym-lib-table").exists())
        self.assertIn("hat.kicad_pro", note)
        written, note = write_lib_tables(self.project, "servo", lib_tables([self.parts], self.project))
        self.assertEqual(written, [])
        self.assertIn("give each board its own folder", note)


if __name__ == "__main__":
    unittest.main()
