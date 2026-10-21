"""A model's previous document is its cache: a restart with nothing changed runs no
body and writes nothing; anything else builds, and writes the cold build's bytes."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from cadgen.engine import build as engine_build
from cadgen.engine import document, previous

KIT = """
import json
from pathlib import Path

import build123d as bd
from cadgen.engine import read_step, xbf

HERE = Path(__file__).resolve().parent


@xbf
def peg():
    size = json.loads((HERE / "peg.json").read_text())["size"]
    peg = bd.Box(size, size, {peg})
    peg.label = "peg"
    return peg


@xbf
def cap():
    cap = read_step(HERE / "inputs" / "cap.step")
    cap.label = "cap"
    return cap


@xbf
def kit():
    left = peg().moved(bd.Location((10, 0, 0)))
    right = peg().moved(bd.Location((-10, 0, 0)))
    plate = bd.Box({plate}, 30, 2)
    plate.label = "plate"
    return bd.Compound(children=[left, right, cap().moved(bd.Location((0, 0, 8))), plate], label="kit")
"""


class PreviousTest(unittest.TestCase):
    def setUp(self) -> None:
        import build123d as bd

        from cadgen.engine.kernel import memo

        self.folder = Path(tempfile.mkdtemp(prefix="engine-previous-")).resolve()
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.script = self.folder / "kit.py"
        self.xbf = self.folder / "kit.xbf"
        (self.folder / "inputs").mkdir()
        bd.export_step(bd.Cylinder(3, 4), str(self.folder / "inputs" / "cap.step"))
        (self.folder / "peg.json").write_text(json.dumps({"size": 4}), encoding="utf-8")
        self.restart()
        self.addCleanup(self.restart)
        memo.install()

    def restart(self) -> None:
        """What a new worker holds: nothing."""
        from cadgen.engine.kernel import memo

        memo.clear()
        engine_build._WRITTEN.clear()
        engine_build._PARTS.clear()
        engine_build._BUILT.clear()

    def source(self, peg: float = 10, plate: float = 40) -> None:
        self.script.write_text(textwrap.dedent(KIT.format(peg=peg, plate=plate)), encoding="utf-8")
        os.utime(self.script, ns=(time.time_ns(), time.time_ns()))

    def kit(self) -> dict:
        (report,) = engine_build.build(self.script, ["kit"], cwd=self.folder)
        self.assertTrue(report["ok"], report)
        return report

    def cold(self, **values) -> bytes:
        """The kit as a process that never built it, with no file, builds it."""
        self.restart()
        self.xbf.unlink(missing_ok=True)
        self.source(**values)
        report = self.kit()
        self.assertEqual(report["cache"]["reason"], "no file yet")
        return self.xbf.read_bytes()

    def test_a_restart_with_nothing_changed_runs_no_body_and_writes_nothing(self) -> None:
        first = self.cold()
        written = self.xbf.stat().st_mtime_ns
        self.restart()
        report = self.kit()
        self.assertEqual(report["cache"], {"used": True, "from": "file"})
        self.assertEqual(report["memo"]["bodies_run"], 0)
        self.assertEqual(report["memo"]["bodies_loaded"], 3, "kit, peg and cap")
        self.assertEqual(report["bodies"], {"kit.py::kit": "loaded"})
        self.assertEqual([row["written"] for row in report["files"].values()], [False])
        self.assertEqual(self.xbf.read_bytes(), first)
        self.assertEqual(self.xbf.stat().st_mtime_ns, written)
        # The next build in the same worker: the key it took, in memory, and no read.
        again = self.kit()
        self.assertEqual(again["cache"], {"used": True, "from": "memory"})
        self.assertNotIn("read", again["timings"])
        self.assertEqual(self.xbf.stat().st_mtime_ns, written)

    def test_an_edit_after_a_restart_writes_the_cold_bytes(self) -> None:
        self.cold()
        self.restart()
        self.source(peg=12)
        report = self.kit()
        self.assertEqual(report["cache"]["reason"], "its code changed")
        self.assertEqual(report["bodies"], {"kit.py::kit": "run", "kit.py::peg": "run", "kit.py::cap": "run"})
        edited = self.xbf.read_bytes()
        self.assertEqual(edited, self.cold(peg=12))
        # Taken, then edited in the same worker: built, the cold bytes again.
        self.restart()
        self.assertTrue(self.kit()["cache"]["used"])
        self.source(peg=12, plate=44)
        report = self.kit()
        self.assertFalse(report["cache"]["used"])
        self.assertEqual(report["memo"]["bodies_run"], 3)
        self.assertEqual(self.xbf.read_bytes(), self.cold(peg=12, plate=44))

    def test_a_warm_build_the_memo_replays_is_replayed(self) -> None:
        self.cold()
        report = self.kit()
        self.assertEqual(report["cache"]["reason"], "replayed from memory")
        self.assertEqual(set(report["bodies"].values()), {"replayed"})

    def test_a_changed_input_file_builds(self) -> None:
        self.cold()
        for change in ("json", "step"):
            self.restart()
            if change == "json":
                (self.folder / "peg.json").write_text(json.dumps({"size": 5}), encoding="utf-8")
            else:
                import build123d as bd

                bd.export_step(bd.Cylinder(3, 5), str(self.folder / "inputs" / "cap.step"))
            report = self.kit()
            self.assertEqual(report["cache"]["reason"], "a file it reads changed", change)
            self.assertTrue(report["files"][str(self.xbf)]["written"])
        gone = self.folder / "peg.json"
        gone.unlink()
        self.restart()
        engine_build.build(self.script, ["kit"], cwd=self.folder)  # the body raises: no json
        self.assertTrue(self.xbf.exists(), "a failed build leaves the file")

    def test_the_file_names_its_inputs_by_relative_path_and_holds_no_script_path(self) -> None:
        self.cold()
        text = self.xbf.read_bytes()
        writer, found = previous.scan(self.xbf)
        record = json.loads(found)
        self.assertEqual(record["inputs"], [["read", "peg.json"], ["file", "inputs/cap.step"]])
        self.assertNotIn(str(self.folder).encode(), text)
        self.assertNotIn(b"kit.py", text)
        self.assertEqual(writer, document._writer())

    def test_another_engines_file_is_built_again(self) -> None:
        self.cold()
        writer = document._writer().encode()
        other = writer.replace(b"(cadgen ", b"(cadgem ")
        self.xbf.write_bytes(self.xbf.read_bytes().replace(writer, other))
        self.restart()
        report = self.kit()
        self.assertEqual(report["cache"]["reason"], f"written by {other.decode()}"
                         if b"cadgen.engine xbf/1 (cadgen " in other else "written by another program")
        self.assertTrue(report["files"][str(self.xbf)]["written"])

    def test_a_missing_or_broken_file_builds(self) -> None:
        first = self.cold()
        self.restart()
        self.xbf.unlink()
        self.assertEqual(self.kit()["cache"]["reason"], "no file yet")
        self.assertEqual(self.xbf.read_bytes(), first)
        # Its head whole, the rest gone: the record shows, the document does not read.
        head = first[: len(first) // 2]
        self.xbf.write_bytes(head)
        self.restart()
        report = self.kit()
        self.assertTrue(report["cache"]["reason"].startswith("the file does not read"), report["cache"])
        self.assertEqual(self.xbf.read_bytes(), first)

    def export(self, name: str) -> bytes:
        """The kit's STEP as ``cadgen xbf export`` writes it, in a process of its own."""
        import subprocess
        import sys

        out = self.folder / name
        done = subprocess.run([sys.executable, "-m", "cadgen.engine", "export", str(self.xbf), "--step", str(out)],
                              cwd=self.folder, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        # The STEP header's time stamp is the second it was written: masked.
        return re.sub(rb"'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d'", b"'T'", out.read_bytes(), count=1)

    def test_a_taken_document_serves_and_exports_as_the_cold_one(self) -> None:
        self.cold()
        structure = document.Loaded(self.xbf).structure()
        step = self.export("cold.step")
        self.restart()
        self.assertTrue(self.kit()["cache"]["used"])
        again = document.Loaded(self.xbf)
        self.assertEqual(json.loads(json.dumps(again.structure())), json.loads(json.dumps(structure)))
        self.assertEqual(self.export("taken.step"), step)
        # Provenance is the worker's that built it: a taken document has none to give.
        key = again.prototypes[0].key
        self.assertIsNone(engine_build.made_by(str(self.xbf), key, "face", 1))


if __name__ == "__main__":
    unittest.main()
