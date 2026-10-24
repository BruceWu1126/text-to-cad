"""A file the stills or an export read is read in work bounded by the file, or refused by name.
A DXF block is drawn once for every insert of it and every cell of a MINSERT's grid, so a few
kilobytes can name an exponential drawing; each reader counts what it expands and refuses a file
past its bound before it holds what it would draw."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[5] / "packages" / "cadgen" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cadgen.engine.formats import dxf  # noqa: E402


class FolderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="format-bounds-")).resolve()
        self.addCleanup(shutil.rmtree, self.folder, True)


def nested_blocks(path: Path, fanout: int, levels: int) -> Path:
    """A DXF of one line in block B0 and ``levels`` blocks, each inserting the one below
    ``fanout`` times; modelspace inserts the top one (``fanout**levels`` lines)."""
    import ezdxf

    document = ezdxf.new()
    document.blocks.new("B0").add_line((0, 0), (1, 0))
    for level in range(1, levels + 1):
        block = document.blocks.new(f"B{level}")
        for at in range(fanout):
            block.add_blockref(f"B{level - 1}", (2 * at, 0))
    document.modelspace().add_blockref(f"B{levels}", (0, 0))
    document.saveas(path)
    return path


def grid(path: Path, columns: int, rows: int) -> Path:
    """A DXF of one MINSERT of a one-line block in a ``columns`` x ``rows`` grid."""
    import ezdxf

    document = ezdxf.new()
    document.blocks.new("B0").add_line((0, 0), (1, 0))
    insert = document.modelspace().add_blockref("B0", (0, 0))
    insert.dxf.column_count, insert.dxf.row_count = columns, rows
    insert.dxf.column_spacing, insert.dxf.row_spacing = 2.0, 2.0
    document.saveas(path)
    return path


class DxfExpansionTest(FolderTest):
    def test_shared_blocks_within_the_bound_draw_every_placement(self) -> None:
        drawing = dxf.read_dxf(nested_blocks(self.folder / "small.dxf", 2, 3))
        self.assertEqual(len(drawing.lines), 8)
        drawing = dxf.read_dxf(grid(self.folder / "grid.dxf", 3, 4))
        self.assertEqual(len(drawing.lines), 12)

    def test_nested_inserts_past_the_bound_are_refused(self) -> None:
        path = nested_blocks(self.folder / "fanout.dxf", 10, 4)  # 10,000 lines
        with mock.patch.object(dxf, "MAX_EXPANDED", 1000):
            with self.assertRaisesRegex(dxf.DxfError, "draw more than 1,000 entities"):
                dxf.read_dxf(path)

    def test_a_minsert_grid_past_the_bound_is_refused_before_its_cells_are_placed(self) -> None:
        path = grid(self.folder / "grid.dxf", 30_000, 30_000)  # 9e8 cells
        with mock.patch.object(dxf.np, "eye", wraps=dxf.np.eye) as eye:
            with self.assertRaisesRegex(dxf.DxfError, "draw more than"):
                dxf.read_dxf(path)
        self.assertLess(eye.call_count, 10)

    def test_a_minsert_grid_of_a_count_that_is_no_number_is_refused(self) -> None:
        path = grid(self.folder / "grid.dxf", 2, 2)
        text = path.read_text(encoding="utf-8")
        # Group code 70 (the column count) of the one MINSERT, written as an overflowing float.
        head, insert, tail = text.partition("\nINSERT\n")
        cut = tail.index("\n 70\n2\n")
        path.write_text(head + insert + tail[:cut] + "\n 70\n1e400\n" + tail[cut + len("\n 70\n2\n"):], encoding="utf-8")
        with self.assertRaisesRegex(dxf.DxfError, "draw more than"):
            dxf.read_dxf(path)


if __name__ == "__main__":
    unittest.main()
