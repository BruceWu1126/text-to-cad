"""References to geometry: ``select`` and ``cadgen where`` resolve a ref -- a part's label or an
occurrence's path (``hand/finger.2``), and an element's name or ordinal -- by name in the latest
build, into build123d shapes in world coordinates; a model never reads a file its own build
writes."""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

import build123d as bd

from cadgen.engine import build as engine_build
from cadgen.engine import previous, read_xbf, select
from cadgen.engine.refs import RefError

SRC = Path(__file__).resolve().parents[5] / "packages" / "cadgen" / "src"

PLATE = """
import build123d as bd
from cadgen.engine import xbf


@xbf
def plate():
    body = bd.Box(40, 30, 6)
    relief = {relief}
    return relief
"""

HAND = """
import build123d as bd
from cadgen.engine import xbf


@xbf
def hand():
    palm = bd.Box(30, 20, 5)
    palm.label = "palm"
    finger = bd.Box(4, 4, 20) - bd.Pos(0, 0, 8) * bd.Cylinder(1, 10)
    finger.label = "finger"
    fingers = [finger.moved(bd.Location((x, 0, 12))) for x in (-10, 0, 10)]
    return bd.Compound(children=[palm, *fingers], label="hand")
"""

ROUND = "body - bd.Pos(0, 0, 0) * bd.Cylinder(5, 10)"
WALL = "plate/f:plate.relief_make_cylinder1.1;M:plate.relief"
TOP = "plate/f:plate.body.6;M:plate.relief"


def centre(shape: bd.Shape) -> tuple[float, ...]:
    point = shape.center() if isinstance(shape, bd.Vertex) else shape.center(bd.CenterOf.MASS)
    return tuple(round(value, 6) for value in point)


class _Folder(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="engine-select-")).resolve()
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.restart()
        self.addCleanup(self.restart)

    def restart(self) -> None:
        """What a new worker holds: nothing."""
        from cadgen.engine.kernel import memo

        memo.clear()
        engine_build._WRITTEN.clear()
        engine_build._PARTS.clear()
        engine_build._BUILT.clear()

    def write(self, name: str, text: str) -> Path:
        script = self.folder / name
        script.write_text(textwrap.dedent(text), encoding="utf-8")
        os.utime(script, ns=(time.time_ns(), time.time_ns()))
        return script

    def build(self, name: str, text: str | None = None) -> list[dict]:
        script = self.write(name, text) if text is not None else self.folder / name
        return engine_build.build(script, cwd=self.folder)

    def built(self, name: str, text: str | None = None) -> dict:
        (report,) = self.build(name, text)
        self.assertTrue(report["ok"], report.get("error"))
        return report


class GrammarTest(unittest.TestCase):
    """The grammar on a plain build123d tree, outside any build: its elements have no names there."""

    def tree(self) -> bd.Compound:
        boxes = []
        for at, label in enumerate(("a", "a", "a.2", "M3/M4 screw")):
            box = bd.Pos(10 * at, 0, 0) * bd.Box(1, 1, 1)
            box.label = label
            boxes.append(box)
        return bd.Compound(children=boxes, label="kit")

    def test_a_label_reads_whole_first_and_is_quoted_where_it_would_misread(self) -> None:
        tree = self.tree()
        refs = {select(tree, ref)[0].label: round(select(tree, ref)[0].center().X, 6) + 0.0
                for ref in ("kit/a.1", '"a".2', "a.2", '"M3/M4 screw"')}
        self.assertEqual(refs, {"kit/a.1": 0.0, 'kit/"a".2': 10.0, "a.2": 20.0, '"M3/M4 screw"': 30.0})
        self.assertEqual(select(tree, 'kit/"M3/M4 screw"/face 1')[0].label, '"M3/M4 screw"/face 1')
        with self.assertRaisesRegex(RefError, r"have no names here"):
            select(tree, "kit/a.1/f:kit.box.1")
        for bad, said in (("kit//face 1", "an empty segment"), ("kit/", "ends in '/'"),
                          ('kit/"a', "a quoted segment that does not close")):
            with self.assertRaisesRegex(RefError, said):
                select(tree, bad)


class SelectTest(_Folder):
    def test_a_part_s_face_edge_and_vertex_by_number_and_by_name(self) -> None:
        self.built("plate.py", PLATE.format(relief=ROUND))
        xbf = self.folder / "XBF/plate.xbf"
        tree = read_xbf(xbf)  # one read: the same elements, select after select
        for kind, ordinal in (("face", 3), ("edge", 2), ("vertex", 1)):
            (by_number,) = select(tree, f"plate/{kind} {ordinal}")
            name = by_number.label.partition("/")[2]
            self.assertRegex(name, rf"^{kind[0]}:plate\.", "an element is labelled by its name, never its ordinal")
            (by_name,) = select(tree, f"plate/{name}")
            self.assertTrue(by_name.is_same(by_number), kind)
            self.assertEqual(centre(select(xbf, f"plate/{name}")[0]), centre(by_number), "a document's path reads it")
            self.assertEqual(by_name.label, by_number.label)
            self.assertIsInstance(by_name, {"face": bd.Face, "edge": bd.Edge, "vertex": bd.Vertex}[kind])
        # A document of one part: its elements by the document's name, or with no owner at all.
        self.assertEqual(select(xbf, "face 3")[0].label, TOP)
        self.assertEqual(select(xbf, "f:plate.body.6;M:plate.relief")[0].label, TOP)
        self.assertEqual(select(xbf, "plate/part1/face 3")[0].label, TOP, "its path in full, too")
        (wall,) = select(xbf, WALL)
        self.assertAlmostEqual(wall.area, 2 * math.pi * 5 * 6, places=6)
        # The line the viewer copies: the file, then the ref.
        (copied,) = select(xbf, f"{xbf}#{TOP}")
        self.assertEqual((copied.label, centre(copied)), (TOP, centre(select(tree, TOP)[0])))
        with self.assertRaisesRegex(RefError, r"the ref names other\.xbf, not plate\.xbf"):
            select(xbf, f"other.xbf#{TOP}")
        with self.assertRaisesRegex(RefError, r"plate has no face 99 \(it has 7\)"):
            select(xbf, "plate/face 99")
        # A part without an element is its shape, placed; several selectors give one each.
        (part,) = select(xbf, "plate")
        self.assertEqual(part.label, "plate")
        self.assertAlmostEqual(part.volume, 40 * 30 * 6 - math.pi * 25 * 6, places=3)
        self.assertEqual([shape.label for shape in select(xbf, "plate/face 3,face 7")],
                         [TOP, WALL])

    def test_a_repeated_part_goes_by_its_occurrence_path(self) -> None:
        self.built("hand.py", HAND)
        xbf = self.folder / "XBF/hand.xbf"
        tree = read_xbf(xbf)
        faces = [select(tree, f"hand/finger.{n}/face 1")[0] for n in (1, 2, 3)]
        self.assertEqual([centre(face)[0] for face in faces], [-12.0, -2.0, 8.0], "each in world coordinates")
        name = faces[1].label.rpartition("/")[2]
        self.assertEqual([face.label for face in faces], [f"hand/finger.{n}/{name}" for n in (1, 2, 3)])
        # The same element by its name under the occurrence, and the path's last segments alone.
        self.assertTrue(select(tree, f"hand/finger.2/{name}")[0].is_same(faces[1]))
        self.assertTrue(select(tree, f"finger.2/{name}")[0].is_same(faces[1]))
        self.assertEqual(select(xbf, f"finger.2/{name}")[0].label, faces[1].label)
        (finger,) = select(xbf, "hand/finger.3")
        self.assertEqual((finger.label, round(finger.bounding_box().center().X, 6)), ("hand/finger.3", 10.0))
        # A label its occurrence alone bears needs no path.
        self.assertEqual(select(xbf, "palm/face 6")[0].label.partition("/")[0], "palm")
        (whole,) = select(xbf, "hand")
        self.assertEqual([child.label for child in whole.children], ["palm", "finger", "finger", "finger"])
        # A label naming several occurrences, and a name several of them hold, are refused with the paths.
        with self.assertRaisesRegex(RefError, r"finger names 3 occurrences in hand\.xbf: hand/finger\.1, "
                                              r"hand/finger\.2, hand/finger\.3"):
            select(xbf, f"finger/{name}")
        with self.assertRaisesRegex(RefError, r"names 3 faces in hand\.xbf: hand/finger\.1/"):
            select(xbf, f"hand/{name}")
        with self.assertRaisesRegex(RefError, r"a face is numbered within its part, and hand holds 4"):
            select(xbf, "hand/face 1")
        # Numbers across the document and occurrence tokens are no part of the grammar: refused by name.
        for old in ("finger_2/face 1", "o1.2.f7", "#o:4b1f9e2a7c03/f:hand.finger.1"):
            owner = old.lstrip("#").split("/")[0]
            with self.assertRaisesRegex(RefError, rf"hand\.xbf has no part or assembly {owner}; nearest: "):
                select(xbf, old)

    def test_a_moved_element_is_its_moved_self_and_a_deleted_one_fails_naming_it(self) -> None:
        self.built("plate.py", PLATE.format(relief=ROUND))
        xbf = self.folder / "XBF/plate.xbf"
        self.assertEqual(centre(select(xbf, WALL)[0]), (0.0, 0.0, 0.0))
        # The hole moved and widened: the same ref, the latest build, its moved self.
        self.built("plate.py", PLATE.format(relief="body - bd.Pos(10, 5, 0) * bd.Cylinder(8, 10)"))
        (wall,) = select(xbf, WALL)
        self.assertEqual(centre(wall), (10.0, 5.0, 0.0))
        self.assertAlmostEqual(wall.area, 301.592894745, places=6)
        self.assertEqual(select(xbf, TOP)[0].label, TOP)
        # The hole deleted: its wall fails by name; the top, whose making changed, fails with its name now.
        self.built("plate.py", PLATE.format(relief="body"))
        with self.assertRaisesRegex(RefError, r"plate\.xbf has no face f:plate\.relief_make_cylinder1\.1;"
                                              r"M:plate\.relief in plate: no face is made of "
                                              r"plate\.relief_make_cylinder1\.1 now"):
            select(xbf, WALL)
        with self.assertRaisesRegex(RefError, r"has no face f:plate\.body\.6;M:plate\.relief in plate: "
                                              r"plate\.body\.6 is now f:plate\.body\.6$"):
            select(xbf, TOP)

    def test_kind_gives_every_element_under_the_ref(self) -> None:
        self.built("plate.py", PLATE.format(relief=ROUND))
        self.built("hand.py", HAND)
        plate, hand = self.folder / "XBF/plate.xbf", self.folder / "XBF/hand.xbf"
        faces = select(plate, "plate", kind="face")
        self.assertEqual(len(faces), 7)
        self.assertEqual(len({face.label for face in faces}), 7)
        self.assertTrue(all(face.label.startswith("plate/f:") for face in faces))
        tree = read_xbf(plate)
        (top,) = select(tree, TOP)
        edges = select(tree, TOP, kind="edge")
        self.assertEqual(len(edges), len(top.edges()))
        self.assertTrue(all(any(edge.is_same(mine) for mine in top.edges()) for edge in edges))
        self.assertEqual(len(select(plate, "plate/edge 1", kind="vertex")), 2)
        self.assertEqual([face.label for face in select(plate, TOP, kind="face")], [TOP])
        with self.assertRaisesRegex(RefError, r"is an edge: it holds no faces"):
            select(plate, "plate/edge 1", kind="face")
        with self.assertRaisesRegex(ValueError, r"kind is face, edge or vertex, not 'solid'"):
            select(plate, "plate", kind="solid")
        # Under an assembly: every face of every part below, each under its occurrence.
        every = select(hand, "hand", kind="face")
        self.assertEqual(len(every), 6 + 3 * 8)
        self.assertEqual(sum(face.label.startswith("hand/finger.2/") for face in every), 8)


class BuildSelectTest(_Folder):
    def test_a_live_shape_s_elements_go_by_this_build_s_names(self) -> None:
        """In a body ``select`` names a live shape's elements as its document will, and what a selected
        element makes is named as what ``faces()`` gives makes: it carries no label to pin a name."""
        made = {}
        for how in ("base.faces().sort_by(bd.Axis.Z)[-1]", 'select(base, "f:part.base.6")[0]'):
            folder = self.folder / ("faces" if "faces" in how else "select")
            folder.mkdir()
            (folder / "part.py").write_text(textwrap.dedent(f"""
                import build123d as bd
                from cadgen.engine import select, xbf


                @xbf
                def part():
                    base = bd.Box(40, 30, 6)
                    top = {how}
                    boss = bd.extrude(top, 5)
                    rim = select(base, "f:part.base.6", kind="edge")
                    assert len(rim) == 4 and not any(edge.label for edge in rim)
                    return bd.fillet(rim, 1) + boss
            """), encoding="utf-8")
            (report,) = engine_build.build(folder / "part.py", cwd=folder)
            self.assertTrue(report["ok"], report.get("error"))
            (prototype,) = __import__("cadgen.engine.document", fromlist=["Loaded"]).Loaded(
                folder / "XBF/part.xbf").structure()["prototypes"]
            made[folder.name] = (prototype["faceNames"], prototype["edgeNames"])
        self.assertEqual(made["select"], made["faces"])

    def test_a_model_selecting_from_another_model_s_xbf_is_rebuilt_when_it_changes(self) -> None:
        self.built("plate.py", PLATE.format(relief=ROUND))
        clear = f"""
            from pathlib import Path
            import build123d as bd
            from cadgen.engine import select, xbf

            HERE = Path(__file__).resolve().parent


            @xbf
            def clear():
                wall = select(HERE / "XBF/plate.xbf", "{WALL}")[0]
                return bd.Pos(*wall.center(bd.CenterOf.MASS)) * bd.Box(2, 2, 2)
        """
        first = self.built("clear.py", clear)
        record = previous.of(self.folder / "XBF/clear.xbf")
        self.assertEqual([row[:2] for row in record["inputs"]], [["file", "plate.xbf"]], "the xbf is an input")
        again = self.built("clear.py")
        self.assertEqual(again["memo"]["bodies_run"], 0)
        self.assertEqual(first["memo"]["bodies_run"], 1)
        # The other model moves the hole: the reading model runs again, and follows it.
        self.built("plate.py", PLATE.format(relief="body - bd.Pos(10, 5, 0) * bd.Cylinder(8, 10)"))
        moved = self.built("clear.py")
        self.assertEqual(moved["memo"]["bodies_run"], 1)
        self.assertEqual(moved["cache"]["changed"], ["plate.xbf changed"])
        self.assertEqual(centre(select(self.folder / "XBF/clear.xbf", "clear")[0]), (10.0, 5.0, 0.0))
        # A restart compares the file's digest too: unchanged, the model is taken from its file.
        self.restart()
        self.assertEqual(self.built("clear.py")["cache"], {"used": True, "from": "file"})

    def test_a_model_never_reads_a_file_its_own_build_writes(self) -> None:
        own = """
            from pathlib import Path
            import build123d as bd
            from cadgen.engine import select, xbf

            HERE = Path(__file__).resolve().parent


            @xbf
            def own():
                body = bd.Box(10, 10, 10)
                top = {top}
                return body + bd.Pos(*top.center(bd.CenterOf.MASS)) * bd.Cylinder(2, 4)
        """
        xbf = self.folder / "XBF/own.xbf"
        # Its last build's file is there: read, it would be an input the build rewrites.
        self.built("own.py", own.format(top='bd.Box(10, 10, 10).faces().sort_by(bd.Axis.Z)[-1]'))
        written = xbf.read_bytes()
        (refused,) = self.build("own.py", own.format(top='select(HERE / "XBF/own.xbf", "own/face 6")[0]'))
        self.assertFalse(refused["ok"])
        self.assertIn(f"select {xbf}: this build writes it (own.py::own), and a model reads no file its own "
                      "build writes; select from the live shape instead", refused["error"])
        self.assertEqual(xbf.read_bytes(), written, "the file was neither read into the model nor written")
        # A sibling's file is the build's too; the sibling itself, a model, is selected live.
        pair = """
            from pathlib import Path
            import build123d as bd
            from cadgen.engine import select, xbf

            HERE = Path(__file__).resolve().parent


            @xbf
            def plate():
                body = bd.Box(40, 30, 6)
                relief = body - bd.Cylinder(5, 10)
                return relief


            @xbf
            def peg():
                wall = select({source}, "{wall}")[0]
                return bd.Pos(*wall.center(bd.CenterOf.MASS)) * bd.Cylinder(4, 20)
        """
        refused = self.build("pair.py", pair.format(source='HERE / "XBF/plate.xbf"', wall=WALL))
        self.assertEqual([report["ok"] for report in refused], [True, False])
        self.assertIn("this build writes it (pair.py::plate)", refused[1]["error"])
        self.assertTrue(all(report["ok"] for report in self.build("pair.py", pair.format(source="plate", wall=WALL))))
        # From the live shape: by this build's names, so its own output never invalidates it.
        self.built("own.py", own.format(top='select(body, "face 6")[0]'))
        record = previous.of(xbf)
        self.assertEqual(record["inputs"], [])
        self.assertEqual(self.built("own.py")["memo"]["bodies_run"], 0, "its own xbf, rewritten, changed nothing")
        self.restart()
        self.assertEqual(self.built("own.py")["cache"], {"used": True, "from": "file"})


class ReadAndPlaceTest(_Folder):
    def test_a_read_of_a_file_the_build_writes_is_refused_when_the_memo_holds_it(self) -> None:
        """The refusal comes before the memo: a read the memo holds from a build that did not
        write the file is refused once the build writes it. (Each body is edited so that it runs:
        an unchanged one is replayed, and reads nothing.)"""
        writer = """
            import build123d as bd
            from cadgen.engine import step, xbf


            @xbf
            @step
            def plate():
                return bd.Box(30, 20, 4)
        """
        reads = """
            from pathlib import Path
            import build123d as bd
            from cadgen.engine import read_step, read_xbf, step, xbf

            HERE = Path(__file__).resolve().parent
            {plate}

            @xbf
            def from_xbf():
                return read_xbf(HERE / "XBF/plate.xbf"){tail}


            @xbf
            def from_step():
                return read_step(HERE / "plate.step"){tail}
        """
        self.built("writer.py", writer)
        self.assertTrue(all(report["ok"] for report in self.build("reads.py", reads.format(plate="", tail=""))),
                        "another script's files: read, and held by the memo")
        plate = "\n            ".join(textwrap.dedent(writer).split("\n\n\n", 1)[1].strip().splitlines())
        refused = self.build("reads.py", reads.format(plate=plate, tail=".moved(bd.Location((0, 0, 1)))"))
        self.assertEqual([report["ok"] for report in refused], [True, False, False])
        self.assertIn("read_xbf", refused[1]["error"])
        self.assertIn("read_step", refused[2]["error"])
        for report in refused[1:]:
            self.assertIn("this build writes it (reads.py::plate)", report["error"])

    def test_a_child_model_s_second_placement_measures_as_its_first(self) -> None:
        reports = self.build("pegs.py", """
            import build123d as bd
            from cadgen.engine import xbf


            @xbf
            def peg():
                return bd.Cylinder(2, 10)


            @xbf
            def rack():
                first, second = peg(), peg().moved(bd.Location((10, 0, 0)))
                if abs(second.volume - first.volume) > 1e-6:
                    raise ValueError(f"the second placement measures {second.volume}, not {first.volume}")
                return bd.Compound(children=[first, second], label="rack")
        """)
        self.assertEqual([report["ok"] for report in reports], [True, True], [r.get("error") for r in reports])


class WhereTest(unittest.TestCase):
    """``cadgen where`` resolves the same grammar, through a daemon of the test's own."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.folder = Path(tempfile.mkdtemp(prefix="engine-where-")).resolve()
        cls.state = Path(tempfile.mkdtemp(prefix="eng-", dir="/tmp" if os.name != "nt" else None)).resolve()
        cls.env = {**os.environ, "PYTHONPATH": str(SRC), "CADGEN_ENGINE_DIR": str(cls.state),
                   "CADGEN_ENGINE_IDLE": "60", "CADGEN_ENGINE_MESH": "inline"}
        (cls.folder / "hand.py").write_text(textwrap.dedent(HAND), encoding="utf-8")
        (report,) = engine_build.build(cls.folder / "hand.py", cwd=cls.folder)
        assert report["ok"], report

    @classmethod
    def tearDownClass(cls) -> None:
        subprocess.run([sys.executable, "-m", "cadgen.engine", "stop"], cwd=cls.folder, env=cls.env,
                       capture_output=True, timeout=120)
        shutil.rmtree(cls.folder, ignore_errors=True)
        shutil.rmtree(cls.state, ignore_errors=True)

    def where(self, ref: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "cadgen.cli", "where", "XBF/hand.xbf", ref], cwd=self.folder,
                              env=self.env, capture_output=True, text=True, timeout=300)

    def test_where_takes_an_occurrence_path(self) -> None:
        done = self.where("hand/finger.2/face 1")
        self.assertEqual(done.returncode, 0, done.stderr)
        face = json.loads(done.stdout)
        self.assertEqual((face["path"], face["kind"], face["ordinal"], face["part"]), ("hand/finger.2", "face", 1, "finger"))
        self.assertEqual(face["ref"], f"hand/finger.2/{face['name']}")
        self.assertEqual([round(value, 6) for value in face["centroid"]], [-2.0, 0.0, 12.0], "where finger 2 stands")
        # The line the viewer copies -- the file, its canonical ref -- names the same element.
        copied = self.where(f"{self.folder / 'XBF/hand.xbf'}#{face['ref']}")
        self.assertEqual(copied.returncode, 0, copied.stderr)
        self.assertEqual({k: v for k, v in json.loads(copied.stdout).items() if k != "made_by"},
                         {k: v for k, v in face.items() if k != "made_by"})
        weighed = json.loads(self.where("hand/finger.3").stdout)
        self.assertEqual((weighed["ref"], weighed["parts"], weighed["path"]), ("hand/finger.3", 1, "hand/finger.3"))
        self.assertEqual(json.loads(self.where("hand").stdout)["unweighed"],
                         ["hand/palm", "hand/finger.1", "hand/finger.2", "hand/finger.3"])
        for ref, said in (("finger", "finger names 3 occurrences in hand.xbf: hand/finger.1, hand/finger.2, "
                                     "hand/finger.3"),
                          ("finger_2/face 1", "hand.xbf has no part or assembly finger_2"),
                          ("hand/finger.2/face 99", "hand/finger.2 has no face 99 (it has 8)")):
            refused = self.where(ref)
            self.assertEqual(refused.returncode, 2, refused.stdout)
            self.assertIn(said, refused.stderr)

    def test_a_model_is_built_through_the_daemon_and_selected_from_its_document(self) -> None:
        probe = textwrap.dedent("""
            import json, sys
            sys.path.insert(0, sys.argv[1])
            from hand import hand
            from cadgen.engine import select
            (face,) = select(hand, "hand/finger.3/face 1")
            print(json.dumps([face.label, [round(v, 6) for v in face.center(__import__("build123d").CenterOf.MASS)]]))
        """)
        done = subprocess.run([sys.executable, "-c", probe, str(self.folder)], cwd=self.folder, env=self.env,
                              capture_output=True, text=True, timeout=300)
        self.assertEqual(done.returncode, 0, done.stderr)
        label, point = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertTrue(label.startswith("hand/finger.3/f:"))
        self.assertEqual(point, [8.0, 0.0, 12.0])
        status = json.loads(subprocess.run([sys.executable, "-m", "cadgen.engine", "status"], cwd=self.folder,
                                           env=self.env, capture_output=True, text=True, timeout=120).stdout)
        self.assertIn("hand.py", [Path(worker["script"]).name for worker in status["workers"]])


if __name__ == "__main__":
    unittest.main()
