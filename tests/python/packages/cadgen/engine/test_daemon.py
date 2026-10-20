"""The warm loop through the daemon: started on demand, one worker per script that keeps
its memo between builds, its socket and lock its only files, gone when idle."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
import urllib.request
from pathlib import Path

SRC = Path(__file__).resolve().parents[5] / "packages" / "cadgen" / "src"

MODEL = """
import build123d as bd
from cadgen.engine import xbf

SIZE = {size}


@xbf
def block():
    return bd.fillet(bd.Box(SIZE, 4, 2).edges().filter_by(bd.Axis.Z), 0.5)
"""


PAIR = """
import build123d as bd
from cadgen.engine import xbf

SIZE = {size}


@xbf
def pair():
    block = bd.Box(SIZE, 4, 2)
    block.label = "block"
    peg = bd.Pos(20, 0, 0) * bd.Cylinder(2, 5)
    peg.label = "peg"
    return bd.Compound(children=[block, peg], label="pair")
"""


CUT = """
import build123d as bd
from cadgen.engine import xbf

SIZE = {size}


@xbf
def cut():
    peg = bd.Cylinder(2, 10)
    plate = bd.Box(10, 10, 4) - bd.Pos(SIZE / 10, 0, 0) * peg
    plate.label = "plate"
    peg.label = "peg"
    other = bd.Pos(20, 0, 0) * (bd.Box(6, 6, 6) + bd.Sphere(3.5))
    other.label = "other"
    return bd.Compound(children=[plate, bd.Pos(0, 20, 0) * peg, other], label="cut")
"""


def get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def get_json(url: str) -> dict:
    return json.loads(get(url))


class PatcherTest(unittest.TestCase):
    """What a worker meshes for a build: only parts a served document lacks, never a whole
    document whose meshes the daemon has not made yet."""

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="engine-patcher-")).resolve()
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.script = self.folder / "pair.py"

    def build(self, size: int):
        from cadgen.engine import build as engine_build
        from cadgen.engine import document

        self.script.write_text(textwrap.dedent(PAIR.format(size=size)), encoding="utf-8")
        os.utime(self.script, ns=(time.time_ns(), time.time_ns()))
        self.assertTrue(engine_build.build(self.script, ["pair"], cwd=self.folder)[-1]["ok"])
        return document.saved(self.folder / "pair.xbf")

    def patch(self, request: dict, tree) -> list:
        from cadgen.engine.worker import Patcher

        sent: list = []
        patcher = Patcher(request, lambda message, payload: sent.append(message))
        patcher.saved(self.folder / "pair.xbf", tree)
        return sent

    def test_a_document_nobody_serves_is_meshed_by_no_build(self) -> None:
        tree = self.build(10)
        self.assertEqual(self.patch({}, tree), [])

    def test_a_served_document_s_unchanged_parts_are_not_meshed_by_the_build(self) -> None:
        first = self.build(10)
        served = sorted({part.key for part in first.parts})
        edited = self.build(12)
        path = str(self.folder / "pair.xbf")
        # The daemon serves the first build's document and holds no mesh yet (a cold daemon).
        sent = self.patch({"served": {path: "0-0"}, "served_keys": {path: served}, "held": [], "shapes": []}, edited)
        self.assertEqual(len(sent), 1)
        self.assertEqual(len(sent[0]["patch"]["meshes"]), 1, "the changed part alone")


class DaemonTest(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="engine-daemon-")).resolve()
        self.state = Path(tempfile.mkdtemp(prefix="eng-", dir="/tmp" if os.name != "nt" else None)).resolve()
        self.env = {**os.environ, "PYTHONPATH": str(SRC), "CADGEN_ENGINE_DIR": str(self.state),
                    "CADGEN_ENGINE_IDLE": "60", "CADGEN_ENGINE_MESH": "inline"}
        self.addCleanup(self._stop)
        self.script = self.folder / "block.py"

    def _stop(self) -> None:
        self._stop_daemon()
        shutil.rmtree(self.folder, ignore_errors=True)
        shutil.rmtree(self.state, ignore_errors=True)

    def _stop_daemon(self) -> None:
        self.cli("stop")
        deadline = time.monotonic() + 20
        while any(self.state.glob("*.sock")) and time.monotonic() < deadline:
            time.sleep(0.05)

    def cli(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "cadgen.engine", *args], cwd=self.folder, env=env or self.env,
                              capture_output=True, text=True, timeout=300)

    def build(self, size: int) -> dict:
        self.script.write_text(textwrap.dedent(MODEL.format(size=size)), encoding="utf-8")
        done = self.cli("build", "--json", "block.py::block")
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout.strip().splitlines()[-1])

    def test_a_warm_worker_keeps_its_memo_between_builds(self) -> None:
        cold = self.build(10)
        self.assertGreater(cold["memo"]["misses"], 0)
        self.assertEqual(cold["memo"]["hits"], 0)
        warm = self.build(10)
        self.assertEqual(warm["memo"]["misses"], 0)
        self.assertEqual((warm["memo"]["bodies_run"], warm["memo"]["bodies_skipped"]), (0, 1))
        edited = self.build(12)
        self.assertGreater(edited["memo"]["misses"], 0)
        self.assertTrue(edited["files"][str(self.folder / "block.xbf")]["written"])
        status = json.loads(self.cli("status").stdout)
        self.assertEqual(len(status["workers"]), 1)
        self.assertEqual(status["workers"][0]["builds"], 3)
        # The daemon's only files are its socket and lock; the build's, its outputs.
        self.assertEqual({path.suffix for path in self.state.iterdir()}, {".sock", ".lock"})
        self.assertEqual(sorted(path.name for path in self.folder.iterdir()), ["block.py", "block.xbf"])

    def test_a_warm_build_writes_the_bytes_a_cold_one_does(self) -> None:
        """A warm worker's faces carry whichever ``Checked`` flag the memo's replay left
        them; the document writes them all cleared. So the first build after a restart
        finds the file it would write already there and rewrites nothing."""
        self.script = self.folder / "cut.py"
        xbf = self.folder / "cut.xbf"

        def build(size: int) -> dict:
            self.script.write_text(textwrap.dedent(CUT.format(size=size)), encoding="utf-8")
            done = self.cli("build", "--json", "cut.py")
            self.assertEqual(done.returncode, 0, done.stderr)
            return json.loads(done.stdout.strip().splitlines()[-1])

        build(10)
        cold = xbf.read_bytes()
        build(12)
        self.assertTrue(build(10)["files"][str(xbf)]["written"], "the edit's file is replaced")
        self.assertEqual(xbf.read_bytes(), cold, "the warm worker wrote the cold one's bytes")
        self._stop_daemon()
        self.assertFalse(build(10)["files"][str(xbf)]["written"], "a fresh worker keeps the same bytes")

    def test_a_served_document_follows_the_build_without_its_file_being_read(self) -> None:
        self.script = self.folder / "pair.py"
        self.script.write_text(textwrap.dedent(PAIR.format(size=10)), encoding="utf-8")
        self.assertEqual(self.cli("build", "pair.py").returncode, 0)
        served = self.cli("serve", "pair.xbf")
        self.assertEqual(served.returncode, 0, served.stderr)
        base, ident = served.stdout.strip().split("/?doc=")
        api = f"{base}/api/doc/{ident}"
        before = get_json(api)
        for prototype in before["prototypes"]:  # the page has every part
            self.assertEqual(get(f"{api}/part/{prototype['key']}")[:4], b"XBM2")
        self.script.write_text(textwrap.dedent(PAIR.format(size=12)), encoding="utf-8")
        self.assertEqual(self.cli("build", "pair.py").returncode, 0)
        answer = get_json(f"{api}/version?after={before['version']}&wait=60")
        stat = (self.folder / "pair.xbf").stat()
        self.assertEqual(answer["version"], f"{stat.st_mtime_ns}-{stat.st_size}")
        self.assertIsNotNone(answer["patch"], "the document came from the worker, not from its file")
        self.assertEqual(answer["patch"]["meshes"], 1, "the worker meshed the changed part alone")
        after = get_json(api)
        keys = lambda s: {p["name"]: p["key"] for p in s["prototypes"]}  # noqa: E731
        self.assertNotEqual(keys(after)["block"], keys(before)["block"])
        self.assertEqual(keys(after)["peg"], keys(before)["peg"])
        self.assertEqual(get(f"{api}/part/{keys(after)['block']}")[:4], b"XBM2")

    def test_a_build_meshes_only_what_it_changed_of_a_served_document(self) -> None:
        self.script = self.folder / "pair.py"
        self.script.write_text(textwrap.dedent(PAIR.format(size=10)), encoding="utf-8")
        done = self.cli("build", "--json", "pair.py")
        self.assertEqual(json.loads(done.stdout.strip().splitlines()[-1])["meshed"], 0, "nobody serves it: nothing meshed")
        # A fresh daemon serves the file: its worker has built nothing yet.
        self.cli("stop")
        served = self.cli("serve", "pair.xbf")
        self.assertEqual(served.returncode, 0, served.stderr)
        base, ident = served.stdout.strip().split("/?doc=")
        before = get_json(f"{base}/api/doc/{ident}")
        self.script.write_text(textwrap.dedent(PAIR.format(size=12)), encoding="utf-8")
        done = self.cli("build", "--json", "pair.py")
        self.assertEqual(json.loads(done.stdout.strip().splitlines()[-1])["meshed"], 1,
                         "the changed part alone, not the whole document")
        answer = get_json(f"{base}/api/doc/{ident}/version?after={before['version']}&wait=60")
        self.assertEqual(answer["patch"]["meshes"], 1)
        unchanged = self.cli("build", "--json", "pair.py")
        self.assertEqual(json.loads(unchanged.stdout.strip().splitlines()[-1])["meshed"], 0)

    def test_a_failing_body_answers_with_its_error(self) -> None:
        self.script.write_text("from cadgen.engine import xbf\n\n@xbf\ndef block():\n    raise RuntimeError('no')\n", encoding="utf-8")
        done = self.cli("build", "block.py")
        self.assertEqual(done.returncode, 1)
        self.assertIn("RuntimeError: no", done.stderr)

    def test_an_idle_daemon_exits(self) -> None:
        env = {**self.env, "CADGEN_ENGINE_IDLE": "0.5"}
        self.script.write_text(textwrap.dedent(MODEL.format(size=3)), encoding="utf-8")
        self.assertEqual(self.cli("build", "block.py", env=env).returncode, 0)
        deadline = time.monotonic() + 30
        while any(self.state.glob("*.sock")) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(any(self.state.glob("*.sock")), "the idle daemon left")


if __name__ == "__main__":
    unittest.main()
