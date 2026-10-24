"""``cadgen inspect DOC`` without a ref: what the document holds, on one screen, before its build
closure -- its state, parts, bounds, volume, materials, joints, states, sequences and declared
files -- read from the document the engine holds, and nothing built; ``--json`` the same as data."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from tests.python.support import engine_daemon

SRC = Path(__file__).resolve().parents[5] / "packages" / "cadgen" / "src"

HINGE = """
import pathlib
import build123d as bd
from cadgen import model, materials
from cadgen.ap242 import configuration_sequence, mechanism_state

pathlib.Path(__file__).with_suffix(".ran").write_text("ran", encoding="utf-8")


@model
def hinge():
    base = bd.Box(100, 60, 20, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
    arm = bd.Pos(40, 0, 20) * bd.Box(120, 20, 10, align=(bd.Align.MIN, bd.Align.CENTER, bd.Align.MIN))
    base.label, arm.label = "base", "arm"
    base.material = materials.aluminium_6061
    elbow = bd.Axis((40, 0, 20), (0, -1, 0))
    bd.RevoluteJoint("elbow", base, axis=elbow, angular_range=(-10, 120))
    bd.RigidJoint("elbow", arm, bd.Plane(origin=elbow.position, z_dir=elbow.direction).location)
    base.joints["elbow"].connect_to(arm.joints["elbow"], angle=0)
    assembly = bd.Compound(children=[base, arm], label="hinge")
    mechanism_state(assembly, "open", elbow=90)
    return configuration_sequence(assembly, "cycle", [(0, "open"), (2.5, "open")])


if __name__ == "__main__":
    hinge()
"""


class SummaryTest(unittest.TestCase):
    """Through a real daemon, in an engine, a state and a cache folder of the test's own."""

    def setUp(self) -> None:
        self.folder = Path(os.path.realpath(tempfile.mkdtemp(prefix="engine-inspect-")))
        self.addCleanup(shutil.rmtree, self.folder, True)
        engine = Path(tempfile.mkdtemp(prefix="eng-", dir="/tmp" if os.name != "nt" else None)).resolve()
        self.addCleanup(shutil.rmtree, engine, True)
        self.env = engine_daemon.watched({**os.environ, "PYTHONPATH": str(SRC), "CADGEN_ENGINE_DIR": str(engine),
                                          "CADGEN_STATE_DIR": str(self.folder / "state"),
                                          "CADGEN_CACHE_DIR": str(self.folder / "cache"),
                                          "CADGEN_ENGINE_MESH": "inline"}, self.addCleanup)
        self.addCleanup(self.run_, "-m", "cadgen.engine", "stop")

    def run_(self, *args: str):
        return engine_daemon.run([sys.executable, *args], cwd=self.folder, env=self.env)

    def test_a_hinge_with_one_joint_and_one_sequence_is_summarised_before_its_closure(self) -> None:
        (self.folder / "hinge.py").write_text(textwrap.dedent(HINGE), encoding="utf-8")
        built = self.run_("hinge.py")
        self.assertEqual(built.returncode, 0, built.stderr)
        ident = next(line.split()[1] for line in built.stdout.splitlines() if line.strip().startswith("id:"))
        (self.folder / "hinge.ran").unlink()

        shown = self.run_("-m", "cadgen.engine", "inspect", ident)
        self.assertEqual(shown.returncode, 0, shown.stderr)
        lines = shown.stdout.splitlines()
        self.assertEqual(lines[0], f"hinge  {ident}")
        self.assertTrue(lines[1].startswith("state       live, "), lines[1])
        self.assertEqual(lines[2:17], [
            "parts       2 in hinge",
            "bounds      210 x 60 x 30 mm, from (-50, -30, 0)",
            "volume      144,000 mm^3",
            "materials   0.324 kg in all",
            "  Aluminum 6061-T6: 0.324 kg, hinge/base",
            "  no material: hinge/arm",
            "joints",
            "  elbow  revolute  -10 to 120 deg, as built 0",
            "states",
            "  open  elbow 90",
            "sequences",
            "  cycle  2.5 s",
            "animations  none",
            "files       none",
            "closure",
        ])
        self.assertEqual(json.loads("\n".join(lines[17:]))["source"]["function"], "hinge")

        data = self.run_("-m", "cadgen.engine", "inspect", ident, "--json")
        self.assertEqual(data.returncode, 0, data.stderr)
        found = json.loads(data.stdout)
        summary = found["summary"]
        self.assertEqual(found["closure"]["source"]["function"], "hinge")
        self.assertEqual(summary["joints"], [{"name": "elbow", "type": "revolute", "range": [[-10.0, 120.0]],
                                              "units": ["deg"], "asBuilt": 0.0}])
        self.assertEqual((summary["states"], summary["sequences"]),
                         ([{"name": "open", "values": {"elbow": 90.0}}], [{"name": "cycle", "duration": 2.5}]))
        self.assertEqual((summary["parts"], summary["volume"], summary["mass"]),
                         ({"occurrences": 2, "distinct": 2, "root": "hinge"}, 144000.0, 0.324))
        self.assertFalse((self.folder / "hinge.ran").exists(), "nothing ran the script")


if __name__ == "__main__":
    unittest.main()
