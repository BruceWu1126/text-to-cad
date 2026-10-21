"""What a model imports: ``read_step`` and ``read_xbf`` copy a file's geometry with its names,
colours and materials (an xbf's mechanism stays behind)."""

from __future__ import annotations

import json
import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path

from cadgen.engine import build as engine_build
from cadgen.engine import document

KIT = """
import build123d as bd
from cadgen.engine import configuration, materials, paint, step, xbf


@xbf
@step("kit.step")
def kit():
    base = bd.Box(10, 10, 5)
    base.label, base.color, base.material = "base", bd.Color(0.2, 0.4, 0.6), materials.aluminium
    peg = bd.Pos(20, 0, 0) * bd.Cylinder(3, 8)
    peg.label, peg.color = "peg", bd.Color(0.9, 0.1, 0.1)
    other = peg.moved(bd.Location((0, 20, 0)))
    other.label = "other"
    return bd.Compound(children=[base, peg, other], label="kit")


@xbf
def hinge():
    base, arm = bd.Box(100, 60, 20), bd.Box(120, 20, 10, align=(bd.Align.MIN, bd.Align.CENTER, bd.Align.MIN))
    base.label, arm.label = "base", "arm"
    paint(base, base.faces().sort_by(bd.Axis.Z)[-1], (0.1, 0.8, 0.1))
    bd.RevoluteJoint("elbow", base, axis=bd.Axis((40, 0, 20), (0, 1, 0)), angular_range=(-10, 120))
    bd.RigidJoint("mount", arm)
    base.joints["elbow"].connect_to(arm.joints["mount"], angle=30)
    return configuration(bd.Compound(children=[base, arm], label="hinge"), "raised", elbow=90)
"""

USE = """
from pathlib import Path

from cadgen.engine import read_step, read_xbf, xbf

HERE = Path(__file__).resolve().parent


@xbf
def from_step():
    return read_step(HERE / "kit.step")


@xbf
def from_xbf():
    return read_xbf(HERE / "XBF/converted.xbf")


@xbf
def twice():
    first = read_step(HERE / "kit.step")
    second = read_step(HERE / "kit.step").moved(__import__("build123d").Location((0, 0, 50)))
    third = read_step(HERE / "again.step")
    return __import__("build123d").Compound(children=[first, second, third], label="twice")


@xbf
def hinge_copy():
    return read_xbf(HERE / "XBF/hinge.xbf")
"""


class ImportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from cadgen.engine import api

        cls.folder = Path(tempfile.mkdtemp(prefix="engine-imports-")).resolve()
        (cls.folder / "kit.py").write_text(textwrap.dedent(KIT), encoding="utf-8")
        (cls.folder / "use.py").write_text(textwrap.dedent(USE), encoding="utf-8")
        for report in engine_build.build(cls.folder / "kit.py", cwd=cls.folder):
            assert report["ok"], report
        shutil.copy(cls.folder / "kit.step", cls.folder / "again.step")
        api.export(cls.folder / "kit.step", cls.folder / "XBF/converted.xbf", daemon=False)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.folder, ignore_errors=True)

    def built(self, name: str) -> dict:
        (report,) = engine_build.build(self.folder / "use.py", [name], cwd=self.folder)
        self.assertTrue(report["ok"], report)
        return document.Loaded(self.folder / f"XBF/{name}.xbf").structure()

    def test_read_xbf_of_a_converted_step_is_read_step_of_the_step(self) -> None:
        from_step, from_xbf = self.built("from_step"), self.built("from_xbf")
        for field in ("prototypes", "instances", "tree"):
            self.assertEqual(json.dumps(from_step[field], sort_keys=True), json.dumps(from_xbf[field], sort_keys=True),
                             field)
        # The same geometry (each part's key, above); names, colours and materials as the model made them.
        built = document.Loaded(self.folder / "XBF/kit.xbf").structure()
        self.assertEqual([(p["name"], p["color"], p["faceNames"]) for p in from_xbf["prototypes"]],
                         [(p["name"], p["color"], p["faceNames"]) for p in built["prototypes"]])
        self.assertEqual(from_xbf["prototypes"][0]["material"]["name"], built["prototypes"][0]["material"]["name"])
        self.assertEqual([i["path"].split("/")[-1] for i in from_xbf["instances"]], ["base", "peg", "other"])

    def test_read_xbf_copies_parts_and_paints_but_no_mechanism(self) -> None:
        copy = self.built("hinge_copy")
        hinge = document.Loaded(self.folder / "XBF/hinge.xbf").structure()
        self.assertIn("kinematics", hinge)
        self.assertNotIn("kinematics", copy, "a part is a part: joints and configurations stay behind")
        for mine, theirs in zip(copy["prototypes"], hinge["prototypes"]):
            self.assertEqual((mine["name"], mine["faceColors"], mine["faceNames"]),
                             (theirs["name"], theirs["faceColors"], theirs["faceNames"]))
        self.assertTrue(copy["prototypes"][0]["faceColors"], "the painted face")


if __name__ == "__main__":
    unittest.main()
