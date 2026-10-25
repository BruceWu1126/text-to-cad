"""The line under the run line, in an engine of the test's own: a model's parts, bounds, joints and
configuration sequences against its build before (``delta``). A first build has nothing to compare;
an edit that adds parts says how many and the bounds they were; a failed build says none; and the
report a build returns carries it as data."""

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

# A row of ``COUNT`` 10 mm cubes, 20 mm apart; ``FAIL`` makes the body raise. cadgen is imported
# first, so its lazy kernel binds ``build123d``.
SCRIPT = """
import json
import sys

from cadgen import model

import build123d as bd

COUNT, FAIL = {count}, {fail}


@model
def row():
    assert not FAIL, "the row needs a cube"
    cubes = [bd.Pos(20 * at, 0, 0) * bd.Box(10, 10, 10) for at in range(COUNT)]
    for at, cube in enumerate(cubes):
        cube.label = f"cube{{at}}"
    return bd.Compound(children=cubes, label="row")


if __name__ == "__main__":
    if sys.argv[1:] == ["--report"]:
        print(json.dumps(row.build().models[0]["delta"]))
    else:
        row()
"""


class DeltaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.folder = Path(os.path.realpath(tempfile.mkdtemp(prefix="engine-delta-")))
        cls.addClassCleanup(shutil.rmtree, cls.folder, True)
        engine = Path(tempfile.mkdtemp(prefix="eng-", dir="/tmp" if os.name != "nt" else None)).resolve()
        cls.addClassCleanup(shutil.rmtree, engine, True)
        cls.env = engine_daemon.watched({**os.environ, "PYTHONPATH": str(SRC), "CADGEN_ENGINE_DIR": str(engine),
                                         "CADGEN_STATE_DIR": str(cls.folder / "state"),
                                         "CADGEN_CACHE_DIR": str(cls.folder / "cache"),
                                         "CADGEN_ENGINE_MESH": "inline", "CADGEN_ENGINE_CACHE_GB": "0"},
                                        cls.addClassCleanup)
        cls.addClassCleanup(cls.run_, "-m", "cadgen.engine", "stop")

    @classmethod
    def run_(cls, *args: str):
        return engine_daemon.run([sys.executable, *args], cwd=cls.folder, env=cls.env)

    def built(self, count: int, *args: str, fail: bool = False):
        (self.folder / "row.py").write_text(textwrap.dedent(SCRIPT.format(count=count, fail=fail)), encoding="utf-8")
        return self.run_("row.py", *args)

    def delta(self, done) -> str:
        lines = done.stdout.splitlines()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(lines[0].startswith("built row.py::row"), done.stdout)
        return lines[1].replace("×", "x").replace("·", "|")

    def test_a_build_says_its_parts_and_bounds_against_the_build_before(self) -> None:
        self.assertEqual(self.delta(self.built(2)), "  parts 2 | bounds 30 x 10 x 10 mm | joints 0 | sequences 0")
        self.assertEqual(self.delta(self.built(4)),
                         "  parts 4 (+2) | bounds 70 x 10 x 10 mm (was 30 x 10 x 10) | joints 0 | sequences 0")
        failed = self.built(4, fail=True)
        self.assertEqual(failed.returncode, 1)
        self.assertNotIn("parts", failed.stdout + failed.stderr)
        self.assertEqual(json.loads(self.built(5, "--report").stdout.splitlines()[-1]),
                         {"first": False, "parts": {"now": 5, "was": 4}, "bounds": {"now": [90.0, 10.0, 10.0],
                                                                                     "was": [70.0, 10.0, 10.0]},
                          "joints": {"now": 0}, "sequences": {"now": 0}, "warnings": {"now": 0}})


if __name__ == "__main__":
    unittest.main()
