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


# A bare model: no file, its document the worker's memory alone.
BARE = """
import build123d as bd
from cadgen.engine import model


@model
def bracket():
    return bd.Box({size}, 2, 3)

if __name__ == "__main__":
    bracket()
"""


PLUG = """
import build123d as bd
from cadgen.engine import stl


@stl("plug.stl")
def plug():
    return bd.Box(2, 2, 2) - bd.Cylinder(0.5, 3)

if __name__ == "__main__":
    plug()
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


class LongStatePathTest(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "a socket's path limit is a Unix socket's")
    def test_a_state_folder_past_the_socket_path_limit_works_and_nobody_changes_folder(self) -> None:
        from unittest import mock

        from cadgen.engine import client

        engine = Path(tempfile.mkdtemp(prefix="eng-")) / ("long-" * 24)
        self.addCleanup(shutil.rmtree, engine.parent, True)
        env = engine_daemon.watched({**os.environ, "PYTHONPATH": str(SRC), "CADGEN_ENGINE_DIR": str(engine)},
                                    self.addCleanup)
        self.addCleanup(engine_daemon.run, [sys.executable, "-m", "cadgen.engine", "stop"], env=env)
        probe = "from cadgen.engine.client import request; print(request({'op': 'status'})['ok'])"
        started = engine_daemon.run([sys.executable, "-c", probe], env=env)
        self.assertEqual((started.returncode, started.stdout.split()[-1:]), (0, ["True"]), started.stderr)
        with mock.patch.dict(os.environ, {"CADGEN_ENGINE_DIR": str(engine)}), \
                mock.patch.object(os, "chdir", side_effect=AssertionError("the client changed folder")):
            path = client._daemon.socket_path()
            self.assertLess(len(os.fsencode(path)), 100, "the socket's own path fits the limit")
            self.assertTrue(client.request({"op": "status"}, start=False)["ok"])
        self.assertEqual(os.stat(path.parent).st_mode & 0o077, 0, "the socket's folder is the user's alone")


class InstallationTest(unittest.TestCase):
    def test_every_name_of_the_installation_s_interpreter_reaches_one_engine(self) -> None:
        from unittest import mock

        from cadgen.engine import daemon

        folder = os.path.dirname(sys.executable)
        names = [os.path.join(folder, name) for name in ("python", "python3", f"python3.{sys.version_info[1]}")]
        tags = set()
        for name in names:
            with mock.patch.object(sys, "executable", name):
                tags.add(daemon.socket_path())
        self.assertEqual(len(tags), 1, "`python3 model.py` and `cadgen viewer` share one engine")

    def test_a_relative_and_an_absolute_launch_reach_one_engine(self) -> None:
        # `../../.venv/bin/python model.py` from a model folder, and the absolute path.
        probe = "from cadgen.engine import daemon; print(daemon.socket_path())"
        env = {**os.environ, "PYTHONPATH": str(SRC)}
        with tempfile.TemporaryDirectory() as folder:
            folder = os.path.realpath(folder)
            relative = os.path.relpath(sys.executable, folder) if os.path.splitdrive(sys.executable)[0].lower() \
                == os.path.splitdrive(folder)[0].lower() else sys.executable
            found = {subprocess.run([command, "-c", probe], cwd=folder, env=env, capture_output=True, text=True,
                                    timeout=60).stdout.strip() for command in (relative, sys.executable)}
        self.assertEqual(len(found), 1, found)


class PoolExitTest(unittest.TestCase):
    def test_a_pool_process_exits_when_its_engine_dies_without_cleaning_up(self) -> None:
        probe = ("import os; from cadgen.engine.serve import Mesher; mesher = Mesher('processes', workers=1); "
                 "print(mesher.processes().submit(os.getpid).result(), flush=True); os._exit(0)")
        done = engine_daemon.run([sys.executable, "-c", probe], env={**os.environ, "PYTHONPATH": str(SRC)})
        self.assertEqual(done.returncode, 0, done.stderr)
        orphan = int(done.stdout.split()[-1])
        deadline = time.monotonic() + 30
        while engine_daemon._alive(orphan):
            self.assertLess(time.monotonic(), deadline, "the pool process outlived the process that started it")
            time.sleep(0.05)


class ListenTest(unittest.TestCase):
    def test_a_taken_port_is_refused_by_name_and_leaves_no_pool_or_recorder_behind(self) -> None:
        import socket
        from unittest import mock

        from cadgen._internal import engine_host
        from cadgen.engine import daemon

        taken = socket.socket()
        self.addCleanup(taken.close)
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        engine = daemon.Daemon(60)
        with mock.patch("cadgen.engine.serve.Mesher") as mesher, mock.patch.object(daemon, "HTTP_FACTORY", None):
            with self.assertRaisesRegex(ValueError, f"127.0.0.1:{port}: .*; pass another --port"):
                engine.serve({"port": port})
        mesher.return_value.stop.assert_called_once_with()
        self.assertIsNone(engine.server, "the next request may still choose its port")
        with mock.patch("cadgen.viewer.engine_app.EngineApp") as app, self.assertRaises(OSError):
            engine_host.http_factory(mock.Mock(), "127.0.0.1", port)
        app.return_value.close.assert_called_once_with()


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

    def test_inspection_never_builds_missing_or_stale_script_models(self):
        marker = self.folder / "executed.txt"
        main = self.folder / "main.txt"
        self.script.write_text(textwrap.dedent(MODEL.format(size=3)).replace(
            'if __name__ == "__main__":',
            f'from pathlib import Path\nPath({str(marker)!r}).write_text("imported", encoding="utf-8")\n'
            f'if __name__ == "__main__":\n    Path({str(main)!r}).write_text("main", encoding="utf-8")'), encoding="utf-8")
        address = str(self.script) + "::block"
        for options in ((), ("face 1",), ("--query", "build")):
            missing = self.cli("inspect", address, *options)
            self.assertNotEqual(missing.returncode, 0, missing.stdout)
            self.assertIn("no live model", missing.stderr)
        self.assertFalse(marker.exists())
        self.assertFalse((self.folder / "XBF/block.xbf").exists())
        self.built("block")
        self.assertTrue(marker.exists())
        self.assertFalse(main.exists(), "explicit decorated-model build does not execute __main__")
        saved = (self.folder / "XBF/block.xbf").read_bytes()
        marker.unlink()
        self.script.write_text('raise RuntimeError("inspection ran source")\n', encoding="utf-8")
        for options in ((), ("face 1",), ("--query", "build")):
            inspected = self.cli("inspect", address, *options)
            self.assertEqual(inspected.returncode, 0, inspected.stderr)
        self.assertEqual((self.folder / "XBF/block.xbf").read_bytes(), saved)
        self.assertFalse(marker.exists())
        self._stop_daemon()
        missing = self.cli("inspect", address, "face 1")
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("no live model", missing.stderr)
        inspected = self.cli("inspect", str(self.folder / "XBF/block.xbf"), "--query", "build")
        self.assertEqual(inspected.returncode, 0, inspected.stderr)
        self.assertIsNone(json.loads(inspected.stdout)["live"])

    def _bare(self) -> str:
        """``bracket.py``: one bare ``@model``, a document of memory alone, and its address."""
        script = self.folder / "bracket.py"
        script.write_text(textwrap.dedent(BARE.format(size=1)), encoding="utf-8")
        return f"{script}::bracket"

    def test_named_memory_model_updates_one_address_and_is_gone_after_restart(self) -> None:
        from urllib.parse import urlparse, parse_qs

        address = self._bare()
        first = self.built("bracket")
        self.assertEqual(first["files"], {})
        self.assertEqual(first["address"], address)
        self.assertEqual(first["meshed"], 0)
        url = urlparse(first["url"])
        doc = parse_qs(url.query)["doc"][0]
        endpoint = f"{url.scheme}://{url.netloc}/api/doc/{doc}"
        token = parse_qs(url.query)["token"][0]
        before = get_json(endpoint, token)
        (self.folder / "bracket.py").write_text(textwrap.dedent(BARE.format(size=2)), encoding="utf-8")
        second = self.built("bracket")
        self.assertEqual(second["url"], first["url"])
        after = get_json(endpoint, token)
        self.assertNotEqual(before["version"], after["version"])
        measured = self.cli("inspect", f"{address}#face 1")
        self.assertEqual(measured.returncode, 0, measured.stderr)
        self.assertEqual(json.loads(measured.stdout)["kind"], "face")
        models = json.loads(self.cli("status", "--json").stdout)["models"]
        self.assertEqual([row["address"] for row in models], [address])
        self.assertEqual([path.name for path in self.folder.iterdir()], ["bracket.py"],
                         "no cache, document, or thumbnail on disk")
        self._stop_daemon()
        missing = self.cli("inspect", f"{address}#face 1")
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("no live model", missing.stderr)
        self.assertEqual(json.loads(self.cli("status", "--json").stdout)["models"], [])

    def test_snapshot_job_draws_a_memory_model_without_an_intermediate_document(self) -> None:
        from tests.python.packages.cadgen.engine.test_snapshot import gl

        gl()
        address = self._bare()
        self.built("bracket")
        packet = {"input": address, "jobs": [{"outputs": [
            {"path": str(self.folder / "bracket.png"), "width": 200, "height": 150}]}]}
        drawn = self.cli("snapshot", "--job", json.dumps(packet))
        self.assertEqual(drawn.returncode, 0, drawn.stderr)
        from PIL import Image

        with Image.open(self.folder / "bracket.png") as picture:
            self.assertEqual(picture.size, (200, 150))
        self.assertEqual(sorted(path.name for path in self.folder.iterdir()), ["bracket.png", "bracket.py"])

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

    def test_an_idle_worker_holds_no_project_folder_and_a_build_reads_relative_to_its_script(self) -> None:
        (self.folder / "size.txt").write_text("7", encoding="utf-8")
        self.script.write_text(textwrap.dedent(MODEL.format(size='float(open("size.txt").read())')), encoding="utf-8")
        done = self.run_script("block.py")
        self.assertEqual(done.returncode, 0, done.stderr)
        worker = json.loads(self.cli("status", "--json").stdout)["workers"][0]["pid"]
        if os.name == "nt":  # a folder a process stands in cannot be renamed
            moved = self.folder.with_name(self.folder.name + "-moved")
            os.rename(self.folder, moved)
            os.rename(moved, self.folder)
            return
        if sys.platform.startswith("linux"):
            where = os.readlink(f"/proc/{worker}/cwd")
        else:
            listed = subprocess.run(["lsof", "-a", "-p", str(worker), "-d", "cwd", "-Fn"], capture_output=True,
                                    text=True, timeout=60).stdout
            where = next(line[1:] for line in listed.splitlines() if line.startswith("n"))
        self.assertNotEqual(os.path.realpath(where), os.path.realpath(self.folder), "the idle worker left the folder")

    def test_a_warm_worker_keeps_its_memo_between_builds(self) -> None:
        cold = self.build(10)
        self.assertGreater(cold["memo"]["misses"], 0)
        self.assertEqual(cold["memo"]["hits"], 0)
        warm = self.build(10)
        self.assertEqual(warm["memo"]["misses"], 0)
        # Unchanged: its document holds the model's key, in the worker's memory, and nothing runs.
        self.assertEqual((warm["memo"]["bodies_run"], warm["memo"]["bodies_skipped"], warm["cache"]),
                         (0, 0, {"used": True, "from": "memory"}))
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
        found = engine_daemon.run([sys.executable, "-m", "cadgen.cli", "inspect", "XBF/block.xbf", ref], cwd=self.folder,
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

    def test_exports_and_a_model_s_document_through_the_daemon(self) -> None:
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
        (self.folder / "plug.py").write_text(textwrap.dedent(PLUG), encoding="utf-8")
        built = self.run_script("plug.py")
        self.assertEqual(built.returncode, 0, built.stderr)
        self.assertTrue((self.folder / "plug.stl").is_file())
        # From Python: the model's document and shape, asked of the worker that holds them.
        probe = textwrap.dedent("""
            import json, plug
            report = plug.plug.build().models[0]
            shape = plug.plug()
            print(json.dumps({"bodies": report["bodies"], "name": plug.plug.document.name,
                              "volume": round(shape.volume, 3)}))
        """)
        done = engine_daemon.run([sys.executable, "-c", probe], cwd=self.folder, env=self.env)
        self.assertEqual(done.returncode, 0, done.stderr)
        found = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertEqual(found["bodies"], {"plug.py::plug": "replayed"}, "the script's warm worker")
        self.assertEqual(found["name"], "plug")
        self.assertAlmostEqual(found["volume"], 8 - 3.14159265 * 0.25 * 2, places=2)

    def test_a_build_with_no_page_starts_no_meshing_process(self) -> None:
        """``python model.py`` serves its document, so the daemon starts its server; the meshing
        pool waits for a page, a view or a mesh: the daemon's only child is the worker."""
        env = {name: value for name, value in self.env.items() if name != "CADGEN_ENGINE_MESH"}
        self.script.write_text(textwrap.dedent(MODEL.format(size=3)), encoding="utf-8")
        done = self.run_script("block.py", env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("  view: http://127.0.0.1:", done.stdout, "the build registered its page")
        status = json.loads(self.cli("status", "--json", env=env).stdout)
        children = []
        for path in Path(env[engine_daemon.JOURNAL]).glob("*-*.txt"):
            role, _, pid = path.stem.partition("-")
            first = path.read_text(encoding="utf-8").split("\n", 1)[0]
            if pid.isdigit() and f"parent={status['pid']} " in first and engine_daemon._alive(int(pid)):
                children.append((role, int(pid)))
        self.assertEqual(children, [("worker", status["workers"][0]["pid"])])

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

    @unittest.skipIf(os.name == "nt", "a terminate signal is POSIX's")
    def test_a_terminated_daemon_stops_as_stop_does(self) -> None:
        import signal

        probe = ("import json; from cadgen.engine.client import request; request({'op': 'warm'}); "
                 "print(request({'op': 'status'})['pid'])")
        done = engine_daemon.run([sys.executable, "-c", probe], cwd=self.folder, env=self.env)
        self.assertEqual(done.returncode, 0, done.stderr)
        pid = int(done.stdout.split()[-1])
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 30
        while engine_daemon._alive(pid) or any(self.state.glob("*.sock")):
            self.assertLess(time.monotonic(), deadline, "the daemon shut down and removed its socket")
            time.sleep(0.05)

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
