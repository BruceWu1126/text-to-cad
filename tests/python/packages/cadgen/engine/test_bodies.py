"""Body retention: a model body whose bytes are what they were is not run; its last run is
replayed, its result the same objects and its trace the one a run leaves -- so names, the
file and the caller's own calls are what they would be had it run."""

from __future__ import annotations

import os
import shutil
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

import build123d as bd

from cadgen.engine import build as engine_build
from cadgen.engine import document
from cadgen.engine.kernel import memo

SHARED = """
WIDTH = {width}
"""

LEFT = """
import build123d as bd
from cadgen.engine import xbf
from shared import WIDTH


@xbf
def left():
    plate = bd.fillet(bd.Box(WIDTH, 8, 3).edges().filter_by(bd.Axis.Z), 1)
    plate = plate - bd.Cylinder(1.5, 10)
    plate.label = "left"
    return plate
"""

RIGHT = """
import build123d as bd
from cadgen.engine import xbf
from shared import WIDTH


@xbf
def right():
    return bd.Box(WIDTH, 4, 4) - bd.Pos(1, 0, 0) * bd.Box(2, 2, 10)
"""

BOUGHT = """
from pathlib import Path

import build123d as bd
from cadgen.engine import xbf, read_step


@xbf
def bought():
    return read_step(Path(__file__).parent / "bought.step")
"""

ROOT = """
import build123d as bd
from cadgen.engine import xbf
from left import left
from right import right
from bought import bought

HOLE = {hole}


@xbf
def root():
    before = bd.Box(1, 1, 1)
    one = left()
    two = left().moved(bd.Location((0, 20, 0)))
    two.label = "two"
    plate = left().moved(bd.Location((0, 40, 0))) - bd.Pos(3, 40, 0) * bd.Cylinder(HOLE, 10)
    plate.label = "plate"
    after = bd.Box(1, 1, 1).moved(bd.Location((0, -20, 0)))
    after.label = "after"
    before.label = "before"
    return bd.Compound(children=[one, two, plate, right().moved(bd.Location((40, 0, 0))),
                                 bought().moved(bd.Location((-40, 0, 0))), before, after], label="root")
"""


def _counts(report: dict) -> tuple[int, int]:
    return report["memo"]["bodies_run"], report["memo"]["bodies_skipped"]


class BodiesCase(unittest.TestCase):
    def setUp(self) -> None:
        memo.clear()
        engine_build._WRITTEN.clear()
        engine_build._PARTS.clear()
        self.addCleanup(memo.clear)
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        os.environ.pop("CADGEN_ENGINE_BODIES", None)
        os.environ.pop("CADGEN_ENGINE_MEMO", None)
        self.folder = Path(tempfile.mkdtemp(prefix="engine-bodies-")).resolve()
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.write("shared.py", SHARED.format(width=10))
        self.write("left.py", LEFT)
        self.write("right.py", RIGHT)
        self.write("bought.py", BOUGHT)
        self.write("root.py", ROOT.format(hole=1.0))
        self.stock(3)

    def write(self, name: str, text: str) -> None:
        path = self.folder / name
        path.write_text(textwrap.dedent(text), encoding="utf-8")
        os.utime(path, ns=(time.time_ns(), time.time_ns()))

    def stock(self, size: float) -> None:
        path = self.folder / "bought.step"
        bd.export_step(bd.Compound(children=[bd.Solid.make_box(size, 2, 2)]), str(path))
        os.utime(path, ns=(time.time_ns(), time.time_ns()))

    def build(self) -> dict:
        report = engine_build.build(self.folder / "root.py", ["root"], cwd=self.folder)[-1]
        self.assertTrue(report["ok"], report)
        return report

    def document(self) -> tuple[bytes, dict]:
        path = self.folder / "root.xbf"
        loaded = document.load(path)
        return path.read_bytes(), {p.name: (dict(p.face_names), dict(p.edge_names)) for p in loaded.prototypes}


class RetentionTest(BodiesCase):
    def test_names_and_the_file_are_the_same_whether_a_child_ran_or_was_replayed(self) -> None:
        self.build()
        self.write("root.py", ROOT.format(hole=1.25))
        report = self.build()
        self.assertEqual(_counts(report), (1, 3), "the root ran, its three children were replayed")
        replayed = self.document()
        # The same sources built by a process that has run nothing: every body runs.
        memo.clear()
        engine_build._WRITTEN.clear()
        engine_build._PARTS.clear()
        (self.folder / "root.xbf").unlink()
        self.assertEqual(_counts(self.build()), (4, 0))
        ran = self.document()
        self.assertEqual(replayed[1], ran[1], "every element name")
        self.assertTrue(any(names[0] for names in ran[1].values()))
        self.assertEqual(replayed[0], ran[0], "the xbf's bytes")

    def test_an_edit_in_a_module_two_bodies_import_reruns_both(self) -> None:
        self.build()
        self.write("shared.py", SHARED.format(width=12))
        self.assertEqual(_counts(self.build()), (3, 1), "left, right and the root ran; bought was replayed")

    def test_a_changed_file_input_reruns_the_body_that_reads_it(self) -> None:
        self.build()
        self.stock(5)
        report = self.build()
        # The reader ran, and the root whose result holds it; left and right were replayed.
        self.assertEqual(_counts(report), (2, 2))
        self.assertGreater(report["memo"]["misses"], 0)

    def test_an_edit_in_the_root_reruns_the_root_alone(self) -> None:
        self.build()
        self.write("root.py", ROOT.format(hole=1.5))
        report = self.build()
        self.assertEqual(_counts(report), (1, 3))
        self.assertEqual(_counts(self.build()), (0, 4), "nothing changed: the root and its children are replayed")

    def test_a_replayed_child_placed_twice_is_still_one_prototype(self) -> None:
        self.build()
        self.write("root.py", ROOT.format(hole=1.5))
        report = self.build()
        self.assertEqual(_counts(report), (1, 3))
        self.assertEqual(report["named"], 1, "only the newly cut plate: the replayed child's part is the run's own")
        structure = document.load(self.folder / "root.xbf").structure()
        by_path = {i["path"]: i["prototype"] for i in structure["instances"]}
        self.assertEqual(by_path["root/left"], by_path["root/two"])
        self.assertNotEqual(by_path["root/left"], by_path["root/plate"])

    def test_the_report_and_the_stats_count_bodies_run_and_skipped(self) -> None:
        before = memo.stats()
        self.assertEqual(_counts(self.build()), (4, 0))
        self.assertEqual(_counts(self.build()), (0, 4))
        after = memo.stats()
        self.assertEqual((after["bodies_run"] - before["bodies_run"], after["bodies_skipped"] - before["bodies_skipped"]),
                         (4, 4))


class OrdinalTest(BodiesCase):
    def test_a_caller_s_calls_are_keyed_alike_whether_its_child_ran_or_was_replayed(self) -> None:
        # The root makes the same box before and after calling its children: a replayed
        # child makes no call, so the root's ordinals -- and what it hits -- are the same.
        self.build()
        self.write("root.py", ROOT.format(hole=1.5))
        replayed = self.build()["memo"]
        memo.clear()
        self.write("root.py", ROOT.format(hole=1.0))
        os.environ["CADGEN_ENGINE_BODIES"] = "0"
        self.build()
        self.write("root.py", ROOT.format(hole=1.5))
        ran = self.build()["memo"]
        self.assertEqual(_counts({"memo": ran}), (4, 0))
        self.assertEqual(replayed["misses"], ran["misses"], "the root missed the same calls")
        root_calls = replayed["hits"] + replayed["misses"]
        self.assertLess(replayed["misses"], root_calls, "its unchanged calls hit")


DIMENSIONS = '''
"""Dimensions the parts share."""
import build123d as bd

WIDTH = 10.0
HEIGHT = {height}
SPARE = 1.0


def plate(width):
    """A plate."""
    return bd.Box(width, 8, {thickness})


def {spare}():
    return SPARE
'''

HELPERS = """
import build123d as bd
from dimensions import plate


def holed(width):
    return plate(width) - bd.Cylinder(1, 10)
"""

BRACKET = """
from cadgen.engine import xbf
from dimensions import WIDTH
from helpers import holed


@xbf
def bracket():
    return holed(WIDTH)
"""

SPACER = """
import build123d as bd
from cadgen.engine import xbf
import dimensions


@xbf
def spacer():
    return bd.Box(dimensions.HEIGHT, 2, 2)
"""

PROBE = """
import build123d as bd
from cadgen.engine import xbf
import dimensions

WHICH = "HEI" + "GHT"


@xbf
def probe():
    return bd.Box(getattr(dimensions, WHICH), 3, 3)
"""

ASSEMBLY = """
import build123d as bd
from cadgen.engine import xbf
from bracket import bracket
from spacer import spacer


@xbf
def assembly():
    return bd.Compound(children=[bracket(), spacer().moved(bd.Location((30, 0, 0)))], label="assembly")
"""


class SymbolsCase(BodiesCase):
    """A body is keyed by the symbols its code reaches: ``bracket`` reaches ``holed`` and,
    through it, ``plate`` and ``WIDTH``; ``spacer`` reads ``HEIGHT`` off the module;
    ``probe`` reads a name it computes, so its module falls back to its bytes."""

    def setUp(self) -> None:
        super().setUp()
        self.dimensions(height=4.0)
        self.write("helpers.py", HELPERS)
        self.write("bracket.py", BRACKET)
        self.write("spacer.py", SPACER)
        self.write("probe.py", PROBE)
        self.write("assembly.py", ASSEMBLY)

    def dimensions(self, height: float = 4.0, thickness: float = 3.0, spare: str = "spare") -> None:
        self.write("dimensions.py", DIMENSIONS.format(height=height, thickness=thickness, spare=spare))

    def assemble(self) -> tuple[int, int]:
        report = engine_build.build(self.folder / "assembly.py", ["assembly"], cwd=self.folder)[-1]
        self.assertTrue(report["ok"], report)
        return _counts(report)

    def probe(self) -> dict:
        report = engine_build.build(self.folder / "probe.py", ["probe"], cwd=self.folder)[-1]
        self.assertTrue(report["ok"], report)
        return report


class SymbolsTest(SymbolsCase):
    def test_a_constant_edit_runs_the_bodies_that_read_it_and_replays_the_rest(self) -> None:
        self.assertEqual(self.assemble(), (3, 0))
        self.dimensions(height=5.0)
        self.assertEqual(self.assemble(), (2, 1), "spacer reads HEIGHT and the assembly holds it; bracket replayed")

    def test_an_edit_to_a_function_runs_every_body_that_reaches_it(self) -> None:
        self.assemble()
        self.dimensions(thickness=3.5)
        # bracket calls holed, which calls plate: two steps away, still its code.
        self.assertEqual(self.assemble(), (2, 1), "bracket and the assembly ran; spacer replayed")

    def test_comments_blank_lines_and_docstrings_run_nothing(self) -> None:
        self.assemble()
        text = (self.folder / "dimensions.py").read_text(encoding="utf-8")
        text = text.replace('"""Dimensions the parts share."""', '"""What the parts share."""\n\n# in millimetres')
        text = text.replace('    """A plate."""', '    """A plate,\n    drilled by its callers."""\n    # flat')
        self.write("dimensions.py", text)
        self.write("helpers.py", "# helpers\n\n\n" + HELPERS.replace("def holed(width):", 'def holed(width):\n    """Holed."""'))
        self.write("assembly.py", ASSEMBLY.replace("@xbf", "\n# the top\n@xbf"))
        self.assertEqual(self.assemble(), (0, 3))

    def test_a_computed_getattr_falls_back_to_its_module_s_bytes(self) -> None:
        self.probe()
        self.assertEqual(_counts(self.probe()), (0, 1))
        self.dimensions(spare="extra")  # nothing the probe names, but its module's bytes
        report = self.probe()
        self.assertEqual(_counts(report), (1, 0))
        reasons = report["retention"]["fallbacks"]["probe.py::probe"]
        self.assertTrue(any("getattr with a computed name" in reason for reason in reasons), reasons)
        # The assembly's bodies read nothing they cannot follow.
        self.assemble()
        self.dimensions(height=4.0, spare="other")
        self.assertEqual(self.assemble(), (0, 3))

    def test_renaming_runs_the_users_of_what_was_renamed_and_nothing_else(self) -> None:
        self.assemble()
        self.dimensions(spare="renamed")
        self.assertEqual(self.assemble(), (0, 3), "nothing reaches the renamed function")
        self.write("dimensions.py", DIMENSIONS.format(height=4.0, thickness=3.0, spare="renamed")
                   .replace("def plate(", "def slab("))
        self.write("helpers.py", HELPERS.replace("plate", "slab"))
        self.assertEqual(self.assemble(), (2, 1), "bracket reaches it and the assembly holds bracket")


if __name__ == "__main__":
    unittest.main()
