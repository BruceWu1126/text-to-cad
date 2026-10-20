"""Building a model: the decorators, the document it writes, what a warm build does
again, and the law that nothing but the declared files is written."""

from __future__ import annotations

import json
import os
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from cadgen.engine import build as engine_build
from cadgen.engine import document
from cadgen.engine.model import Model, step, stl, xbf

MODEL = """
import build123d as bd
from cadgen.engine import xbf, step, paint

WIDTH = {width}


@xbf
def bracket():
    plate = bd.Box(WIDTH, 10, 2)
    plate = bd.fillet(plate.edges().filter_by(bd.Axis.Z), 1)
    plate = plate - bd.Cylinder(1.5, 5)
    plate.color = bd.Color(0.1, 0.5, 0.9)
    return plate


@xbf(out="out/frame.xbf")
@step(out="out/frame.step")
def frame():
    left = bracket()
    right = bracket().moved(bd.Location((0, 20, 0)))
    right.label = "right"
    rail = bd.Box(4, 40, 4).moved(bd.Location((WIDTH, 10, 0)))
    rail = paint(rail, rail.faces().sort_by(bd.Axis.Z)[-1], (1, 0, 0))
    rail.label = "rail"
    return bd.Compound(children=[left, right, rail], label="frame")
"""


def _files(folder: Path) -> set[str]:
    return {str(path.relative_to(folder)) for path in folder.rglob("*") if path.is_file()}


class EngineCase(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="engine-build-")).resolve()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.folder, ignore_errors=True))
        self.script = self.folder / "frame.py"

    def write(self, width: int = 30, text: str = MODEL) -> None:
        self.script.write_text(textwrap.dedent(text.format(width=width)), encoding="utf-8")
        # A rewrite within the same clock tick still reads as a new file.
        os.utime(self.script, ns=(time.time_ns(), time.time_ns()))

    def build(self, *functions: str) -> dict[str, dict]:
        reports = engine_build.build(self.script, list(functions) or None, cwd=self.folder)
        return {report["model"].rsplit("::", 1)[-1]: report for report in reports}


class DecoratorTest(unittest.TestCase):
    def test_bare_and_with_out_and_stacked_in_source_order(self) -> None:
        @xbf
        def one():
            return None

        @xbf(out="a/b.xbf")
        @stl(out="b.stl")
        @step
        def two():
            return None

        self.assertIsInstance(one, Model)
        here = Path(__file__).resolve().parent
        self.assertEqual(one.targets(), [("xbf", here / "one.xbf")])
        self.assertEqual(two.targets(), [("xbf", here / "a/b.xbf"), ("stl", here / "b.stl"), ("step", here / "two.step")])
        self.assertEqual(two.identity, "test_build.py::two")
        with self.assertRaises(ValueError):
            xbf(out="x.step")
        with self.assertRaises(TypeError):
            @xbf
            def takes(value):
                return value

    def test_a_model_called_outside_a_build_is_its_function(self) -> None:
        @xbf
        def plain():
            return 42

        self.assertEqual(plain(), 42)


class BuildTest(EngineCase):
    def test_the_document_holds_names_colours_instances_and_element_names(self) -> None:
        self.write()
        reports = self.build()
        self.assertTrue(all(report["ok"] for report in reports.values()), reports)
        self.assertEqual(_files(self.folder), {"frame.py", "bracket.xbf", "bracket.xbf.json", "out/frame.xbf",
                                               "out/frame.xbf.json", "out/frame.step"})
        loaded = document.load(self.folder / "out/frame.xbf")
        structure = loaded.structure()
        self.assertEqual(structure["name"], "frame")
        self.assertEqual(structure["units"], "mm")
        self.assertEqual([child["name"] for child in structure["tree"]["children"]], ["bracket", "right", "rail"])
        # A child used twice is one prototype placed twice.
        self.assertEqual([i["prototype"] for i in structure["instances"]], [0, 0, 1])
        self.assertEqual(structure["instances"][1]["matrix"][12:15], [0.0, 20.0, 0.0])
        bracket, rail = loaded.prototypes
        self.assertEqual(bracket.name, "bracket")
        self.assertEqual(bracket.color, (0.1, 0.5, 0.9, 1.0))
        self.assertEqual(list(rail.face_colors.values()), [(1.0, 0.0, 0.0, 1.0)])
        self.assertTrue(bracket.face_names and all(name.startswith("f:bracket.") for name in bracket.face_names.values()))
        self.assertTrue(any(";M:bracket.cut1" in name for name in bracket.face_names.values()))
        self.assertTrue(bracket.edge_names)
        self.assertEqual(len(bracket.key), 32)
        # The sidecar: the model, timings, and provenance per element name.
        sidecar = json.loads((self.folder / "out/frame.xbf.json").read_text(encoding="utf-8"))
        self.assertEqual(sidecar["model"], "frame.py::frame")
        self.assertIn("body", sidecar["timings"])
        part = sidecar["parts"][0]
        cut = next(name for name in part["elements"] if ";M:bracket.cut1" in name)
        self.assertEqual(part["elements"][cut], "bracket.cut1")
        self.assertEqual(part["features"]["bracket.cut1"][0], "cut")
        self.assertRegex(part["features"]["bracket.cut1"][1], r"^frame\.py:\d+$")
        # The STEP is a translation: names, and every face's element name.
        text = (self.folder / "out/frame.step").read_text(encoding="utf-8")
        self.assertIn("PRODUCT('frame'", text)
        for name in bracket.face_names.values():
            self.assertIn(f"ADVANCED_FACE('{name}'", text)
        self.assertNotIn("ADVANCED_FACE(''", text)

    def test_a_step_read_back_names_each_occurrence_as_the_file_does(self) -> None:
        # Two placements of one part are told apart by their own names, not their product's.
        self.write()
        self.build("frame")
        read = document.read_step_shape(self.folder / "out/frame.step")
        self.assertEqual([child.label for child in read.children], ["bracket", "right", "rail"])
        self.assertTrue(read.children[0].wrapped.TShape() == read.children[1].wrapped.TShape())
        # Each placed where the file places it: the second bracket 20 along y.
        offset = read.children[1].center() - read.children[0].center()
        self.assertAlmostEqual(offset.Y, 20.0, places=6)
        self.assertAlmostEqual(abs(offset.X) + abs(offset.Z), 0.0, places=6)

    def test_a_warm_build_hits_and_rewrites_nothing(self) -> None:
        self.write()
        self.build()
        target = self.folder / "out/frame.xbf"
        written = [target, self.folder / "out/frame.step", self.folder / "out/frame.xbf.json"]
        before = [path.stat().st_mtime_ns for path in written]
        reports = self.build()
        frame = reports["frame"]
        self.assertEqual(frame["memo"]["misses"], 0)
        self.assertEqual((frame["memo"]["bodies_run"], frame["memo"]["bodies_skipped"]), (0, 1),
                         "an unchanged body is not run: its last run is replayed")
        self.assertEqual(frame["named"], 0, "an unchanged part is not named again")
        self.assertFalse(any(row["written"] for row in frame["files"].values()))
        self.assertEqual([path.stat().st_mtime_ns for path in written], before, "the sidecar too")

    def test_an_assembly_made_again_around_the_same_parts_rewrites_nothing(self) -> None:
        # A child with joints cannot be keyed: the compound above it is made afresh on
        # every build, around the very parts the memo handed back.
        self.write(text=MODEL.replace('    rail.label = "rail"', '    rail.label = "rail"\n    rail.joints = {{"slide": object()}}'))
        self.build("frame")
        frame = self.build("frame")["frame"]
        self.assertGreater(frame["memo"]["uncached"], 0)
        self.assertFalse(any(row["written"] for row in frame["files"].values()))

    def test_an_edit_misses_from_where_it_changed_and_rewrites(self) -> None:
        self.write(30)
        self.build()
        names_before = document.load(self.folder / "out/frame.xbf").prototypes[0].face_names
        self.write(34)
        frame = self.build("frame")["frame"]
        self.assertGreater(frame["memo"]["misses"], 0)
        self.assertTrue(frame["files"][str(self.folder / "out/frame.xbf")]["written"])
        loaded = document.load(self.folder / "out/frame.xbf")
        self.assertEqual(loaded.prototypes[0].face_names, names_before, "faces keep their names across an edit")
        self.write(30)
        frame = self.build("frame")["frame"]
        self.assertEqual(frame["memo"]["misses"], 0, "a revert is the first build's entries")

    def test_a_fresh_process_keeps_a_file_with_the_same_bytes(self) -> None:
        self.write()
        self.build()
        target = self.folder / "out/frame.xbf"
        stamp = target.stat().st_mtime_ns
        engine_build._WRITTEN.clear()
        engine_build._PARTS.clear()
        frame = self.build("frame")["frame"]
        self.assertFalse(frame["files"][str(target)]["written"])
        self.assertEqual(target.stat().st_mtime_ns, stamp)

    def test_a_body_that_raises_reports_the_model_frame_and_keeps_the_file(self) -> None:
        self.write()
        self.build()
        target = self.folder / "out/frame.xbf"
        stamp = target.stat().st_mtime_ns
        self.write(text=MODEL.replace("    rail.label = \"rail\"", "    raise ValueError('rail too short')"))
        frame = self.build("frame")["frame"]
        self.assertFalse(frame["ok"])
        self.assertIn("rail too short", frame["error"])
        self.assertIn("frame.py", frame["error"])
        self.assertNotIn("build123d", frame["error"])
        self.assertEqual(target.stat().st_mtime_ns, stamp)

    def test_a_file_read_hits_whether_or_not_a_child_reading_it_ran_alone(self) -> None:
        # A child's body runs inline in every build of its parent, so the n-th read of
        # one file in a build is fixed by the code: building the child alone in between
        # shifts no ordinal of the parent's.
        import build123d as bd

        bd.export_step(bd.Compound(children=[bd.Solid.make_box(3, 4, 5)]), str(self.folder / "bought.step"))
        self.write(text="""
            from pathlib import Path

            import build123d as bd
            from cadgen.engine import xbf, read_step

            BOUGHT = Path(__file__).parent / "bought.step"


            @xbf
            def holder():
                return read_step(BOUGHT) - bd.Cylinder(1, 20)


            @xbf
            def stand():
                part = read_step(BOUGHT).moved(bd.Location((10, 0, 0)))
                return bd.Compound(children=[holder(), part])
        """)
        self.assertTrue(self.build("stand")["stand"]["ok"])
        holder = self.build("holder")["holder"]
        self.assertTrue(holder["ok"], holder)
        self.assertEqual(holder["memo"]["misses"], 0)
        stand = self.build("stand")["stand"]
        self.assertTrue(stand["ok"], stand)
        self.assertEqual((stand["memo"]["misses"], stand["memo"]["uncached"]), (0, 0))

    def test_a_missing_function_is_the_request_s_error(self) -> None:
        self.write()
        with self.assertRaises(engine_build.BuildError):
            self.build("nothing")

    def test_no_bytecode_and_no_other_file(self) -> None:
        self.write()
        (self.folder / "helper.py").write_text("WIDTH = 3\n", encoding="utf-8")
        self.script.write_text(self.script.read_text(encoding="utf-8").replace("WIDTH = 30", "from helper import WIDTH"), encoding="utf-8")
        self.build()
        self.assertFalse(any("__pycache__" in name for name in _files(self.folder)))


if __name__ == "__main__":
    unittest.main()
