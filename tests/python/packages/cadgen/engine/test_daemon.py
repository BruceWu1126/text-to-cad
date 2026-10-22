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
from tests.python.support import engine_daemon

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
        return document.saved(self.folder / "XBF/pair.xbf")

    def patch(self, request: dict, tree) -> list:
        from cadgen.engine.worker import Patcher

        sent: list = []
        patcher = Patcher(request, lambda message, payload: sent.append(message))
        patcher.saved(self.folder / "XBF/pair.xbf", tree)
        return sent

    def test_a_document_nobody_serves_is_meshed_by_no_build(self) -> None:
        tree = self.build(10)
        self.assertEqual(self.patch({}, tree), [])

    def test_a_served_document_s_unchanged_parts_are_not_meshed_by_the_build(self) -> None:
        first = self.build(10)
        served = sorted({part.key for part in first.parts})
        edited = self.build(12)
        path = str(self.folder / "XBF/pair.xbf")
        # The daemon serves the first build's document and holds no mesh yet (a cold daemon).
        sent = self.patch({"served": {path: "0-0"}, "served_keys": {path: served}, "held": [], "shapes": []}, edited)
        self.assertEqual(len(sent), 1)
        self.assertEqual(len(sent[0]["patch"]["meshes"]), 1, "the changed part alone")


class DaemonTest(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="engine-daemon-")).resolve()
        self.state = Path(tempfile.mkdtemp(prefix="eng-", dir="/tmp" if os.name != "nt" else None)).resolve()
        self.env = engine_daemon.watched({**os.environ, "PYTHONPATH": str(SRC), "CADGEN_ENGINE_DIR": str(self.state),
                                          "CADGEN_ENGINE_IDLE": "60", "CADGEN_ENGINE_MESH": "inline"}, self.addCleanup)
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
        return engine_daemon.run([sys.executable, "-m", "cadgen.engine", *args], cwd=self.folder, env=env or self.env)

    def run_script(self, name: str, env: dict | None = None) -> subprocess.CompletedProcess:
        """``python NAME``: the script asks the daemon to build its models."""
        return engine_daemon.run([sys.executable, name], cwd=self.folder, env=env or self.env)

    def built(self, name: str, env: dict | None = None) -> dict:
        """Model ``name`` of ``name.py`` built through the daemon by ``Model.build()``: its report."""
        probe = f"import json, {name}; print(json.dumps({name}.{name}.build().models[0]))"
        done = engine_daemon.run([sys.executable, "-B", "-c", probe], cwd=self.folder, env=env or self.env)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout.strip().splitlines()[-1])

    def build(self, size: int) -> dict:
        self.script.write_text(textwrap.dedent(MODEL.format(size=size)), encoding="utf-8")
        return self.built("block")

    def test_a_warm_worker_keeps_its_memo_between_builds(self) -> None:
        cold = self.build(10)
        self.assertGreater(cold["memo"]["misses"], 0)
        self.assertEqual(cold["memo"]["hits"], 0)
        warm = self.build(10)
        self.assertEqual(warm["memo"]["misses"], 0)
        self.assertEqual((warm["memo"]["bodies_run"], warm["memo"]["bodies_skipped"]), (0, 1))
        edited = self.build(12)
        self.assertGreater(edited["memo"]["misses"], 0)
        self.assertTrue(edited["files"][str(self.folder / "XBF/block.xbf")]["written"])
        status = json.loads(self.cli("status", "--json").stdout)
        self.assertEqual(len(status["workers"]), 1)
        self.assertEqual(status["workers"][0]["builds"], 3)
        # The daemon's only files are its socket and lock; the build's, its outputs.
        self.assertEqual({path.suffix for path in self.state.iterdir()}, {".sock", ".lock"})
        self.assertEqual(sorted(path.relative_to(self.folder).as_posix() for path in self.folder.rglob("*") if path.is_file()),
                         ["XBF/block.xbf", "block.py"])

    def test_a_warm_build_writes_the_bytes_a_cold_one_does(self) -> None:
        """A warm worker's faces carry whichever ``Checked`` flag the memo's replay left
        them; the document writes them all cleared. So a fresh worker, building the file
        afresh, writes the bytes the warm one did."""
        self.script = self.folder / "cut.py"
        xbf = self.folder / "XBF/cut.xbf"

        def build(size: int) -> dict:
            self.script.write_text(textwrap.dedent(CUT.format(size=size)), encoding="utf-8")
            return self.built("cut")

        build(10)
        cold = xbf.read_bytes()
        build(12)
        self.assertTrue(build(10)["files"][str(xbf)]["written"], "the edit's file is replaced")
        self.assertEqual(xbf.read_bytes(), cold, "the warm worker wrote the cold one's bytes")
        self._stop_daemon()
        self.assertTrue(build(10)["cache"]["used"], "a fresh worker takes the file its key is in")
        xbf.unlink()
        self._stop_daemon()
        self.assertTrue(build(10)["files"][str(xbf)]["written"])
        self.assertEqual(xbf.read_bytes(), cold, "a fresh worker writes the same bytes")

    def test_a_served_document_follows_the_build_without_its_file_being_read(self) -> None:
        self.script = self.folder / "pair.py"
        self.script.write_text(textwrap.dedent(PAIR.format(size=10)), encoding="utf-8")
        self.assertEqual(self.run_script("pair.py").returncode, 0)
        served = self.cli("serve", "XBF/pair.xbf")
        self.assertEqual(served.returncode, 0, served.stderr)
        base, ident = served.stdout.strip().split("/?doc=")
        api = f"{base}/api/doc/{ident}"
        before = get_json(api)
        for prototype in before["prototypes"]:  # the page has every part
            self.assertEqual(get(f"{api}/part/{prototype['key']}")[:4], b"XBM2")
        self.script.write_text(textwrap.dedent(PAIR.format(size=12)), encoding="utf-8")
        self.assertEqual(self.run_script("pair.py").returncode, 0)
        answer = get_json(f"{api}/version?after={before['version']}&wait=60")
        stat = (self.folder / "XBF/pair.xbf").stat()
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
        self.assertEqual(self.built("pair")["meshed"], 0, "nobody serves it: nothing meshed")
        # A fresh daemon serves the file: its worker has built nothing yet.
        self.cli("stop")
        served = self.cli("serve", "XBF/pair.xbf")
        self.assertEqual(served.returncode, 0, served.stderr)
        base, ident = served.stdout.strip().split("/?doc=")
        before = get_json(f"{base}/api/doc/{ident}")
        self.script.write_text(textwrap.dedent(PAIR.format(size=12)), encoding="utf-8")
        self.assertEqual(self.built("pair")["meshed"], 1, "the changed part alone, not the whole document")
        answer = get_json(f"{base}/api/doc/{ident}/version?after={before['version']}&wait=60")
        self.assertEqual(answer["patch"]["meshes"], 1)
        self.assertEqual(self.built("pair")["meshed"], 0)

    def where(self, ref: str) -> dict:
        found = engine_daemon.run([sys.executable, "-m", "cadgen.cli", "where", "XBF/block.xbf", ref], cwd=self.folder,
                                  env=self.env)
        self.assertEqual(found.returncode, 0, found.stderr)
        return json.loads(found.stdout)

    def test_an_element_answers_what_made_it_from_the_worker_that_built_it(self) -> None:
        self.build(10)
        face = self.where("block/face 1")
        made = face["made_by"]
        line = next(n for n, text in enumerate(self.script.read_text(encoding="utf-8").splitlines(), 1)
                    if "bd.fillet" in text)
        self.assertEqual((made["function"], made["file"], made["line"]), ("block", str(self.script), line))
        self.assertEqual(made["name"], face["name"])
        self.assertTrue(made["feature"].startswith("block.") and made["operation"])
        self.assertGreater(face["area"], 0)
        # By its element name, the same element.
        self.assertEqual(self.where(face["name"])["ordinal"], 1)
        # A daemon started again with no build serves the file cold: no worker holds a
        # trace of it, so ``made_by`` is null -- and every fact the kernel measures is
        # what it was. Nothing of the provenance was written anywhere.
        self._stop_daemon()
        cold = self.where("block/face 1")
        self.assertIsNone(cold.pop("made_by"))
        drop = ("made_by", "meshed")
        self.assertEqual({k: v for k, v in cold.items() if k not in drop}, {k: v for k, v in face.items() if k not in drop})
        self.assertEqual(sorted(path.relative_to(self.folder).as_posix() for path in self.folder.rglob("*") if path.is_file()),
                         ["XBF/block.xbf", "block.py"])

    def test_a_worker_over_its_memory_cap_is_retired_and_the_next_build_restarts(self) -> None:
        capped = {**self.env, "CADGEN_ENGINE_WORKER_RSS_MB": "1"}
        self.script.write_text(textwrap.dedent(MODEL.format(size=10)), encoding="utf-8")
        report = self.built("block", env=capped)
        self.assertIn("over its 1 MB cap", report["retired"])
        # The next build is a fresh worker's: a restart, which takes the file it wrote.
        again = self.run_script("block.py", env=capped)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertTrue(again.stdout.startswith("took block.py::block cold"), again.stdout)
        self.assertIn("retired: the worker held", again.stdout)
        status = json.loads(self.cli("status", "--json").stdout)
        self.assertEqual(status["workers"], [], "each worker left the pool when it was retired")
        self.assertEqual([row["reason"] for row in status["retired"]], ["RSS", "RSS"])

    def test_a_failing_body_answers_with_its_error(self) -> None:
        self.script.write_text("from cadgen.engine import xbf\n\n@xbf\ndef block():\n    raise RuntimeError('no')\n", encoding="utf-8")
        done = self.run_script("block.py")
        self.assertEqual(done.returncode, 1)
        self.assertIn("RuntimeError: no", done.stderr)

    def test_running_a_model_script_asks_the_daemon_to_build_it(self) -> None:
        # A script runs like Python and takes no options (law 11): every model it defines writes
        # what it declares, whatever its argv holds.
        self.script.write_text(textwrap.dedent(MODEL.format(size=3)) + "\n\n@stl\ndef peg():\n"
                               "    return bd.Cylinder(1, 3)\n", encoding="utf-8")
        self.script.write_text(self.script.read_text(encoding="utf-8").replace("import xbf", "import stl, xbf"),
                               encoding="utf-8")
        done = self.run_script("block.py")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual([line.split(" in ")[0] for line in done.stdout.splitlines() if line.startswith("built")],
                         ["built block.py::block cold", "built block.py::peg cold"])
        workers = json.loads(self.cli("status", "--json").stdout)["workers"]
        self.assertEqual([(Path(w["script"]).name, w["builds"]) for w in workers], [("block.py", 1)],
                         "the daemon's worker built it, not the script's own process")
        done = engine_daemon.run([sys.executable, "block.py", "--step", "b.step", "--json"], cwd=self.folder,
                                 env=self.env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(sorted(path.relative_to(self.folder).as_posix() for path in self.folder.rglob("*") if path.is_file()),
                         ["XBF/block.xbf", "block.py", "peg.stl"])

    def test_a_model_script_asks_at_its_import_of_the_engine(self) -> None:
        # The asking process runs nothing past the engine's import -- not build123d, not the body
        # of the script -- and the worker runs it all; a script that shows no model is plain Python.
        self.script.write_text(textwrap.dedent("""
            from cadgen.engine import xbf
            import os, sys
            from pathlib import Path
            with Path(__file__).with_name("ran.txt").open("a") as ran:
                ran.write(f"{os.environ.get('CADGEN_ENGINE_WORKER', 'asker')} {'build123d' in sys.modules}\\n")
            import build123d as bd

            @xbf
            def block():
                return bd.Box(1, 2, 3)
            """), encoding="utf-8")
        done = self.run_script("block.py")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(set((self.folder / "ran.txt").read_text(encoding="utf-8").splitlines()), {"1 True"},
                         "the script ran in the worker alone, past the engine's import")
        self.assertTrue((self.folder / "XBF" / "block.xbf").is_file())
        (self.folder / "tool.py").write_text("from cadgen.engine import select\nimport sys\n"
                                             "print('ran', 'build123d' in sys.modules)\n", encoding="utf-8")
        done = self.run_script("tool.py")
        self.assertEqual((done.returncode, done.stdout.strip()), (0, "ran False"), done.stderr)


    def test_the_viewer_moves_before_a_derived_file_is_written(self) -> None:
        # A deliberately slow STL writer, put in place in the worker by the model's own module.
        slow = textwrap.dedent("""
            import time
            from cadgen.engine import export

            if not getattr(export, "slow", False):
                class Slow(type(export.KINDS["stl"])):
                    def write(self, view, path):
                        time.sleep(3)
                        super().write(view, path)

                given = export.KINDS["stl"]
                export.KINDS["stl"] = Slow(given.name, given.suffixes, given.writer, given.atomic)
                export.slow = True
        """) + textwrap.dedent(PAIR).replace("@xbf\n", "@xbf\n@stl\n").replace(
            "import xbf", "import stl, xbf")
        self.script = self.folder / "pair.py"
        self.script.write_text(slow.format(size=10), encoding="utf-8")
        self.assertEqual(self.run_script("pair.py").returncode, 0)
        served = self.cli("serve", "XBF/pair.xbf")
        base, ident = served.stdout.strip().split("/?doc=")
        before = get_json(f"{base}/api/doc/{ident}")
        stl = self.folder / "pair.stl"
        written = stl.stat().st_mtime_ns
        self.script.write_text(slow.format(size=12), encoding="utf-8")
        building = subprocess.Popen([sys.executable, "pair.py"], cwd=self.folder, env=self.env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(building.wait)
        answer = get_json(f"{base}/api/doc/{ident}/version?after={before['version']}&wait=60")
        self.assertNotEqual(answer["version"], before["version"])
        self.assertEqual(stl.stat().st_mtime_ns, written, "the viewer moved before the STL was written")
        self.assertEqual(building.wait(timeout=300), 0)
        self.assertNotEqual(stl.stat().st_mtime_ns, written)

    def test_exports_and_code_through_the_daemon(self) -> None:
        self.script = self.folder / "pair.py"
        self.script.write_text(textwrap.dedent(PAIR.format(size=10)), encoding="utf-8")
        self.assertEqual(self.run_script("pair.py").returncode, 0)
        done = self.cli("export", "XBF/pair.xbf", "pair.glb", "pair.step")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(len(json.loads(self.cli("status", "--json").stdout)["workers"]), 1,
                         "the worker that built the document wrote its exports")
        done = self.cli("export", "pair.step", "back.xbf")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue((self.folder / "back.xbf").is_file())
        done = self.cli("export", "XBF/pair.xbf", "made.stl", "--part", "peg")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.cli("export", "pair.glb", "x.step").returncode, 2, "a mesh is no source")
        self.assertEqual(self.cli("export", "pair.py::pair", "x.step").returncode, 2, "nor is a script")
        code = "import build123d as bd\nresult = bd.Box(2, 2, 2) - bd.Cylinder(0.5, 3)\n"
        built = engine_daemon.run([sys.executable, "-m", "cadgen.engine", "build", "-", "--name", "plug", "--stl",
                                   "plug.stl", "--json"], input=code, cwd=self.folder, env=self.env)
        self.assertEqual(built.returncode, 0, built.stderr)
        self.assertTrue((self.folder / "plug.stl").is_file())
        # From Python: the code's document and shape, asked of the worker that holds them.
        probe = textwrap.dedent(f"""
            import json, cadgen
            built = cadgen.build({code!r}, name="plug", stl="plug.stl")
            shape = built.shape
            print(json.dumps({{"bodies": built.report["bodies"], "name": built.document.name,
                               "volume": round(shape.volume, 3)}}))
        """)
        done = engine_daemon.run([sys.executable, "-c", probe], cwd=self.folder, env=self.env)
        self.assertEqual(done.returncode, 0, done.stderr)
        found = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertEqual(found["bodies"], {"plug.py::plug": "replayed"}, "the code's warm worker")
        self.assertEqual(found["name"], "plug")
        self.assertAlmostEqual(found["volume"], 8 - 3.14159265 * 0.25 * 2, places=2)

    def test_an_idle_daemon_exits(self) -> None:
        env = {**self.env, "CADGEN_ENGINE_IDLE": "0.5"}
        self.script.write_text(textwrap.dedent(MODEL.format(size=3)), encoding="utf-8")
        self.assertEqual(self.run_script("block.py", env=env).returncode, 0)
        deadline = time.monotonic() + 30
        while any(self.state.glob("*.sock")) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(any(self.state.glob("*.sock")), "the idle daemon left")


class DeclaresModelsTest(unittest.TestCase):
    """Which sources hand ``python script.py`` to the daemon at their import of the engine."""

    def declares(self, source: str) -> bool:
        if str(SRC) not in sys.path:
            sys.path.insert(0, str(SRC))
        from cadgen.engine.model import declares_models

        folder = Path(tempfile.mkdtemp(prefix="engine-declares-"))
        self.addCleanup(shutil.rmtree, folder, True)
        script = folder / "model.py"
        script.write_text(textwrap.dedent(source), encoding="utf-8")
        return declares_models(script)

    def test_an_engine_decorator_by_name_or_through_the_module(self) -> None:
        self.assertTrue(self.declares("from cadgen.engine import xbf\n@xbf\ndef a():\n    pass\n"))
        self.assertTrue(self.declares("from cadgen.engine import step as S\n@S('a.step')\ndef a():\n    pass\n"))
        self.assertTrue(self.declares("import cadgen.engine as ce\n@ce.dxf(out='a.dxf')\ndef a():\n    pass\n"))
        self.assertTrue(self.declares("import cadgen.engine\n@cadgen.engine.glb\ndef a():\n    pass\n"))
        self.assertTrue(self.declares("from cadgen import engine\n@engine.xbf\ndef a():\n    pass\n"))
        # cadgen exports the engine's names at its root.
        self.assertTrue(self.declares("from cadgen import xbf, step\n@xbf\n@step\ndef a():\n    pass\n"))
        self.assertTrue(self.declares("import cadgen\n@cadgen.stl\ndef a():\n    pass\n"))

    def test_no_engine_decorator_is_no_model(self) -> None:
        self.assertFalse(self.declares("from cadgen.engine import select\nselect('a.xbf', 'b')\n"))
        self.assertFalse(self.declares("from cadgen.engine import memo\nfrom other import step\n"
                                       "@step\ndef a():\n    pass\n@memo\ndef b():\n    pass\n"))
        self.assertFalse(self.declares("import other as ce\nimport cadgen.engine\n@ce.xbf\ndef a():\n    pass\n"))


if __name__ == "__main__":
    unittest.main()
