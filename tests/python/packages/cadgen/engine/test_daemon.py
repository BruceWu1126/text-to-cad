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


@xbf("XBF/block.xbf")
def block():
    return bd.fillet(bd.Box(SIZE, 4, 2).edges().filter_by(bd.Axis.Z), 0.5)

if __name__ == "__main__":
    block()
"""


PAIR = """
import build123d as bd
from cadgen.engine import xbf

SIZE = {size}


@xbf("XBF/pair.xbf")
def pair():
    block = bd.Box(SIZE, 4, 2)
    block.label = "block"
    peg = bd.Pos(20, 0, 0) * bd.Cylinder(2, 5)
    peg.label = "peg"
    return bd.Compound(children=[block, peg], label="pair")

if __name__ == "__main__":
    pair()
"""


CUT = """
import build123d as bd
from cadgen.engine import xbf

SIZE = {size}


@xbf("XBF/cut.xbf")
def cut():
    peg = bd.Cylinder(2, 10)
    plate = bd.Box(10, 10, 4) - bd.Pos(SIZE / 10, 0, 0) * peg
    plate.label = "plate"
    peg.label = "peg"
    other = bd.Pos(20, 0, 0) * (bd.Box(6, 6, 6) + bd.Sphere(3.5))
    other.label = "other"
    return bd.Compound(children=[plate, bd.Pos(0, 20, 0) * peg, other], label="cut")

if __name__ == "__main__":
    cut()
"""


def get(url: str, token: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": "Bearer "+token}), timeout=120) as response:
        return response.read()


def get_json(url: str, token: str) -> dict:
    return json.loads(get(url, token))


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


class ConnectTest(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "a stale socket file is a Unix daemon's")
    def test_a_connect_to_a_stale_socket_fails_and_leaves_no_socket_open(self) -> None:
        import gc
        import socket
        import warnings
        from unittest import mock

        from cadgen.engine import client

        engine = tempfile.mkdtemp(prefix="eng-", dir="/tmp")  # a short socket path
        self.addCleanup(shutil.rmtree, engine, True)
        with mock.patch.dict(os.environ, {"CADGEN_ENGINE_DIR": engine}):
            path = client._daemon.socket_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            stale.bind(str(path))
            stale.close()  # the file stays, and nothing listens on it
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", ResourceWarning)
                self.assertIsNone(client._connect())
                gc.collect()
        self.assertEqual([w for w in caught if issubclass(w.category, ResourceWarning)], [])


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

    def test_named_memory_model_updates_one_address_and_is_gone_after_restart(self) -> None:
        from urllib.parse import urlparse, parse_qs

        def build(size):
            done = self.cli("build", f"import build123d as bd; result = bd.Box({size}, 2, 3)", "--model", "bracket", "--json")
            self.assertEqual(done.returncode, 0, done.stderr)
            return json.loads(done.stdout.splitlines()[-1])

        first = build(1)
        self.assertEqual(first["files"], {})
        self.assertEqual(first["address"], "code::bracket")
        self.assertEqual(first["meshed"], 0)
        url = urlparse(first["url"])
        doc = parse_qs(url.query)["doc"][0]
        endpoint = f"{url.scheme}://{url.netloc}/api/doc/{doc}"
        token = parse_qs(url.query)["token"][0]
        before = get_json(endpoint, token)
        second = build(2)
        self.assertEqual(second["url"], first["url"])
        after = get_json(endpoint, token)
        self.assertNotEqual(before["version"], after["version"])
        measured = self.cli("where", "code::bracket#face 1")
        self.assertEqual(measured.returncode, 0, measured.stderr)
        self.assertEqual(json.loads(measured.stdout)["kind"], "face")
        models = json.loads(self.cli("status", "--json").stdout)["models"]
        self.assertEqual([row["address"] for row in models], ["code::bracket"])
        self.assertEqual(list(self.folder.iterdir()), [], "no source, cache, document, or thumbnail on disk")
        self._stop_daemon()
        missing = self.cli("serve", "code::bracket")
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("no live model", missing.stderr)
        self.assertEqual(json.loads(self.cli("status", "--json").stdout)["models"], [])

    def test_snapshot_job_draws_a_memory_model_without_an_intermediate_document(self) -> None:
        from tests.python.packages.cadgen.engine.test_snapshot import gl

        gl()
        built = self.cli("build", "import build123d as bd; result = bd.Box(1, 2, 3)", "--model", "bracket")
        self.assertEqual(built.returncode, 0, built.stderr)
        packet = {"input": "code::bracket", "jobs": [{"outputs": [
            {"path": str(self.folder / "bracket.png"), "width": 200, "height": 150}]}]}
        drawn = self.cli("snapshot", "--job", json.dumps(packet))
        self.assertEqual(drawn.returncode, 0, drawn.stderr)
        from PIL import Image

        with Image.open(self.folder / "bracket.png") as picture:
            self.assertEqual(picture.size, (200, 150))
        self.assertEqual([path.name for path in self.folder.iterdir()], ["bracket.png"])

    def test_script_address_builds_only_the_selected_declaration(self) -> None:
        self.script.write_text("""from cadgen import model
import build123d as bd
@model
def unused():
    raise AssertionError('not called')
@model
def block():
    return bd.Box(1, 2, 3)
""", encoding="utf-8")
        served = self.cli("serve", f"{self.script}::block")
        self.assertEqual(served.returncode, 0, served.stderr)
        self.assertEqual([path.name for path in self.folder.iterdir()], ["block.py"])
        exported = self.cli("export", f"{self.script}::block", "block.step")
        self.assertEqual(exported.returncode, 0, exported.stderr)
        self.assertTrue((self.folder / 'block.step').is_file())

    def test_main_calls_bare_and_file_models_in_order_and_keeps_its_arguments(self) -> None:
        self.script.write_text("""import sys
from cadgen import model, step, xbf
import build123d as bd
@model
def bare(): return bd.Box(1, 2, 3)
@step('saved.step')
def saved(): return bd.Box(2, 2, 3)
@xbf("XBF/document.xbf")
def document(): return bd.Box(3, 2, 3)
if __name__ == '__main__':
    print('before', sys.argv[1:])
    value = bare()
    print('volume', round(value.volume))
    saved()
    document()
    print('after')
""", encoding="utf-8")
        done = engine_daemon.run([sys.executable, str(self.script), '--mine'], cwd=self.folder, env=self.env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("before ['--mine']", done.stdout)
        self.assertIn('volume 6', done.stdout)
        self.assertTrue(done.stdout.rstrip().endswith('after'))
        self.assertTrue((self.folder / 'saved.step').is_file())
        self.assertTrue((self.folder / 'XBF/document.xbf').is_file())
        self.assertFalse((self.folder / 'XBF/bare.xbf').exists())

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
        # The daemon's socket (on Windows the port file standing in for it) and its lock, nothing else.
        self.assertEqual({path.suffix for path in self.state.iterdir()}, {".port" if os.name == "nt" else ".sock", ".lock"})
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
        from urllib.parse import urlsplit, parse_qs
        url = urlsplit(served.stdout.strip())
        base, query = f"{url.scheme}://{url.netloc}", parse_qs(url.query)
        ident, token = query["doc"][0], query["token"][0]
        api = f"{base}/api/doc/{ident}"
        before = get_json(api, token)
        for prototype in before["prototypes"]:  # the page has every part
            self.assertEqual(get(f"{api}/part/{prototype['key']}", token)[:4], b"XBM2")
        self.script.write_text(textwrap.dedent(PAIR.format(size=12)), encoding="utf-8")
        self.assertEqual(self.run_script("pair.py").returncode, 0)
        answer = get_json(f"{api}/version?after={before['version']}&wait=60", token)
        stat = (self.folder / "XBF/pair.xbf").stat()
        self.assertEqual(answer["version"], f"{stat.st_mtime_ns}-{stat.st_size}")
        self.assertIsNotNone(answer["patch"], "the document came from the worker, not from its file")
        self.assertEqual(answer["patch"]["meshes"], 1, "the worker meshed the changed part alone")
        after = get_json(api, token)
        keys = lambda s: {p["name"]: p["key"] for p in s["prototypes"]}  # noqa: E731
        self.assertNotEqual(keys(after)["block"], keys(before)["block"])
        self.assertEqual(keys(after)["peg"], keys(before)["peg"])
        self.assertEqual(get(f"{api}/part/{keys(after)['block']}", token)[:4], b"XBM2")

    def test_a_build_meshes_only_what_it_changed_of_a_served_document(self) -> None:
        self.script = self.folder / "pair.py"
        self.script.write_text(textwrap.dedent(PAIR.format(size=10)), encoding="utf-8")
        self.assertEqual(self.built("pair")["meshed"], 0, "nobody serves it: nothing meshed")
        # A fresh daemon serves the file: its worker has built nothing yet.
        self.cli("stop")
        served = self.cli("serve", "XBF/pair.xbf")
        self.assertEqual(served.returncode, 0, served.stderr)
        from urllib.parse import urlsplit, parse_qs
        url = urlsplit(served.stdout.strip())
        base, query = f"{url.scheme}://{url.netloc}", parse_qs(url.query)
        ident, token = query["doc"][0], query["token"][0]
        before = get_json(f"{base}/api/doc/{ident}", token)
        self.script.write_text(textwrap.dedent(PAIR.format(size=12)), encoding="utf-8")
        self.assertEqual(self.built("pair")["meshed"], 1, "the changed part alone, not the whole document")
        answer = get_json(f"{base}/api/doc/{ident}/version?after={before['version']}&wait=60", token)
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
        self.script.write_text("from cadgen.engine import xbf\n\n@xbf('XBF/block.xbf')\ndef block():\n    raise RuntimeError('no')\nif __name__ == '__main__':\n    block()\n", encoding="utf-8")
        done = self.run_script("block.py")
        self.assertEqual(done.returncode, 1)
        self.assertIn("RuntimeError: no", done.stderr)

    def test_running_a_model_script_asks_the_daemon_to_build_it(self) -> None:
        # A script runs like Python and takes no options (law 11): every model it defines writes
        # what it declares, whatever its argv holds.
        self.script.write_text(textwrap.dedent(MODEL.format(size=3)) + "\n\n@stl('peg.stl')\ndef peg():\n"
                               "    return bd.Cylinder(1, 3)\nif __name__ == '__main__':\n    peg()\n", encoding="utf-8")
        self.script.write_text(self.script.read_text(encoding="utf-8").replace("import xbf", "import stl, xbf"),
                               encoding="utf-8")
        done = self.run_script("block.py")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual([line.split(" in ")[0] for line in done.stdout.splitlines() if line.startswith("built")],
                         ["built block.py::block cold", "built block.py::peg warm"])
        self.assertEqual(sum(line.startswith("  view: http://127.0.0.1:") for line in done.stdout.splitlines()), 2,
                         "each model reports its viewer link, including file-output builds")
        workers = json.loads(self.cli("status", "--json").stdout)["workers"]
        self.assertEqual([(Path(w["script"]).name, w["builds"]) for w in workers], [("block.py", 2)],
                         "the daemon's worker built it, not the script's own process")
        done = engine_daemon.run([sys.executable, "block.py", "--step", "b.step", "--json"], cwd=self.folder,
                                 env=self.env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(sorted(path.relative_to(self.folder).as_posix() for path in self.folder.rglob("*") if path.is_file()),
                         ["XBF/block.xbf", "block.py", "peg.stl"])

    def test_decoration_only_declares_and_main_calls_only_its_selected_model(self) -> None:
        self.script.write_text(textwrap.dedent("""
            from cadgen import model, xbf

            @model
            def unused():
                raise AssertionError("uncalled model ran")

            @xbf("XBF/block.xbf")
            def block():
                import build123d as bd
                return bd.Box(1, 2, 3)

            if __name__ == "__main__":
                import sys
                print("after definitions", "build123d" in sys.modules)
                if "call" in sys.argv:
                    block()
                    print("after call")
            """), encoding="utf-8")
        declared = self.run_script("block.py")
        self.assertEqual((declared.returncode, declared.stdout.strip()), (0, "after definitions False"), declared.stderr)
        self.assertFalse((self.folder / "XBF").exists())
        self.assertFalse(self.state.exists() and any(self.state.iterdir()), "importing starts no daemon")
        called = engine_daemon.run([sys.executable, "block.py", "call"], cwd=self.folder, env=self.env)
        self.assertEqual(called.returncode, 0, called.stderr)
        self.assertIn("after call", called.stdout)
        self.assertTrue((self.folder / "XBF/block.xbf").is_file())


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
        """) + textwrap.dedent(PAIR).replace('@xbf("XBF/pair.xbf")', '@xbf("XBF/pair.xbf")\n@stl("pair.stl")').replace(
            "import xbf", "import stl, xbf")
        self.script = self.folder / "pair.py"
        self.script.write_text(slow.format(size=10), encoding="utf-8")
        self.assertEqual(self.run_script("pair.py").returncode, 0)
        served = self.cli("serve", "XBF/pair.xbf")
        from urllib.parse import urlsplit, parse_qs
        url = urlsplit(served.stdout.strip())
        base, query = f"{url.scheme}://{url.netloc}", parse_qs(url.query)
        ident, token = query["doc"][0], query["token"][0]
        before = get_json(f"{base}/api/doc/{ident}", token)
        stl = self.folder / "pair.stl"
        written = stl.stat().st_mtime_ns
        self.script.write_text(slow.format(size=12), encoding="utf-8")
        building = subprocess.Popen([sys.executable, "pair.py"], cwd=self.folder, env=self.env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(building.wait)
        answer = get_json(f"{base}/api/doc/{ident}/version?after={before['version']}&wait=60", token)
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
        self.assertEqual(self.cli("export", "pair.py::pair", "x.step").returncode, 0, "a model address selects its function")
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

    def test_warming_starts_the_server_before_any_document(self) -> None:
        """What the viewer asks at its launch (``{"op": "warm"}``): the daemon, its server and the
        processes a first document waits on, started with no document served; asked again, nothing more."""
        probe = ("import json; from cadgen.engine.client import request; "
                 "print(json.dumps([request({'op': 'warm'}), request({'op': 'warm'}), request({'op': 'status'})]))")
        done = engine_daemon.run([sys.executable, "-c", probe], cwd=self.folder, env=self.env)
        self.assertEqual(done.returncode, 0, done.stderr)
        first, again, status = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertEqual((first, again), ({"ok": True}, {"ok": True}))
        self.assertTrue(status["serving"]["url"].startswith("http://127.0.0.1:"))
        self.assertEqual(status["serving"]["documents"], [])

    def test_an_idle_daemon_exits(self) -> None:
        env = {**self.env, "CADGEN_ENGINE_IDLE": "0.5"}
        self.script.write_text(textwrap.dedent(MODEL.format(size=3)), encoding="utf-8")
        self.assertEqual(self.run_script("block.py", env=env).returncode, 0)
        deadline = time.monotonic() + 30
        while any(self.state.glob("*.sock")) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(any(self.state.glob("*.sock")), "the idle daemon left")


if __name__ == "__main__":
    unittest.main()
