"""The engine's document cache: a model document idle a moment is saved once per revision in the
cache folder (its record, in the state folder, says so), so a restarted engine answers every verb
of it -- page, export, snapshot, inspect, cad_show -- with nothing run; a document whose code
changed since is answered as its last run, and nothing reruns; the cache keeps every document until a person deletes it,
with no cap and nothing evicted, and touches nothing in its folder but ``documents/``; and an engine
whose cache folder is read-only, or not writable at all, saves nothing and does everything else as
ever."""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import tempfile
import textwrap
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from tests.python.support import engine_daemon, opengl

SRC = Path(__file__).resolve().parents[5] / "packages" / "cadgen" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cadgen.engine.ids import file_id, model_id  # noqa: E402
from cadgen.engine.registry import Registry  # noqa: E402

IDLE = 0.3  # the cache's idle seam (``CADGEN_ENGINE_CACHE_IDLE``), in seconds

SCRIPT = """
import pathlib
import build123d as bd
from cadgen.engine import model

pathlib.Path(__file__).with_suffix(".ran").write_text("ran", encoding="utf-8")
WIDTH = {width}


@model
def {name}():
    return bd.Box(WIDTH, 2, 1)


if __name__ == "__main__":
    {name}()
"""

# A model whose body says on stderr that it ran: the worker's output reaches the run's stderr.
SAID = """
import sys
import build123d as bd
from cadgen.engine import model

WIDTH = {width}


@model
def {name}():
{body}


if __name__ == "__main__":
    {name}()
"""
GOOD = '    print("the body ran", file=sys.stderr)\n    return bd.Box(WIDTH, 2, 1)'

# A model declaring a STEP and no xbf, whose body says on stderr that it ran.
STEPPED = """
import sys
import build123d as bd
from cadgen.engine import step


@step("plate.step")
def plate():
    print("the body ran", file=sys.stderr)
    return bd.Box({width}, 3, 1)


if __name__ == "__main__":
    plate()
"""

DECLARED = """
import build123d as bd
from cadgen.engine import xbf


@xbf("XBF/plate.xbf")
def plate():
    return bd.Box(4, 3, 1)


if __name__ == "__main__":
    plate()
"""


def await_(condition, what: str, seconds: float = 60.0):
    deadline = time.monotonic() + seconds
    while True:
        found = condition()
        if found:
            return found
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.02)


def get(url: str, token: str) -> tuple[int, dict]:
    request = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


class CacheTest(unittest.TestCase):
    """Through a real daemon, in an engine, a state and a cache folder of the test's own."""

    def setUp(self) -> None:
        self.folder = Path(os.path.realpath(tempfile.mkdtemp(prefix="engine-cache-")))
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.project = self.folder / "project"
        self.project.mkdir()
        self.use(self.folder / "state", self.folder / "cache")

    def use(self, state: Path, cache: Path, **extra: str) -> None:
        """The engine the test talks to from now on: a folder of its own, and ``state`` and ``cache``."""
        self.state, self.cache = state, cache
        self.engine = Path(tempfile.mkdtemp(prefix="eng-", dir="/tmp" if os.name != "nt" else None)).resolve()
        self.addCleanup(shutil.rmtree, self.engine, True)
        self.env = engine_daemon.watched({**os.environ, "PYTHONPATH": str(SRC), "CADGEN_ENGINE_DIR": str(self.engine),
                                          "CADGEN_STATE_DIR": str(state), "CADGEN_CACHE_DIR": str(cache),
                                          "CADGEN_ENGINE_MESH": "inline",
                                          "CADGEN_ENGINE_CACHE_IDLE": str(IDLE), **extra}, self.addCleanup)
        self.addCleanup(self.cli, "stop", env=dict(self.env))

    def cli(self, *args: str, env: dict | None = None):
        return engine_daemon.run([sys.executable, "-m", "cadgen.engine", *args], cwd=self.project, env=env or self.env)

    def run_script(self, name: str):
        done = engine_daemon.run([sys.executable, f"{name}.py"], cwd=self.project, env=self.env)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done

    def write(self, name: str, width: float = 3.0, text: str = SCRIPT) -> str:
        (self.project / f"{name}.py").write_text(textwrap.dedent(text.format(name=name, width=width)), encoding="utf-8")
        return model_id(self.project / f"{name}.py", name)

    def ask(self, message: dict) -> dict:
        from cadgen.engine.client import request

        with mock.patch.dict(os.environ, {"CADGEN_ENGINE_DIR": str(self.engine)}):
            return request(message, start=False) or {}

    def record(self, ident: str) -> dict:
        return Registry(self.state).get(ident) or {}

    def file(self, ident: str) -> Path:
        return self.cache / "documents" / ident / "document.xbf"

    def cached(self, ident: str, revision: str | None = None) -> dict:
        """The record's ``cache`` field, once its file is there (at ``revision``, when given)."""
        def found():
            meta = self.record(ident).get("cache")
            ok = meta and self.file(ident).is_file() and (revision is None or meta["revision"] == revision)
            return meta if ok else None

        return await_(found, f"{ident}'s cache file")

    def page(self, ident: str) -> tuple[int, dict]:
        serving = self.ask({"op": "status"})["serving"]
        return get(f"{serving['url']}/api/doc/{ident}", self.ask({"op": "http"}).get("token", ""))

    def ran(self, name: str) -> bool:
        return (self.project / f"{name}.ran").exists()

    # --- the tests ----------------------------------------------------------------------

    def test_an_idle_build_is_saved_once_and_an_unchanged_run_rewrites_nothing(self) -> None:
        ident = self.write("cube")
        self.run_script("cube")
        meta = self.cached(ident)
        record = self.record(ident)
        self.assertEqual(set(record), {"schema", "id", "kind", "root", "home", "entry", "revision", "built", "facts",
                                       "cache"}, "a build's record, once its document is cached")
        self.assertEqual(set(meta), {"revision", "bytes"})
        self.assertEqual((meta["revision"], meta["bytes"]), (record["revision"], self.file(ident).stat().st_size))
        self.assertEqual(sorted(os.listdir(self.state / "documents" / ident)), ["document.json"],
                         "the bytes are the cache folder's, never the state folder's")
        compiled = list((self.cache / "pycache").glob("*/**/build123d/__init__.*.pyc"))  # the prefix mirrors its path
        self.assertEqual(len(compiled), 1, "the worker's libraries' bytecode, in the engine's folder")
        self.assertEqual(list(self.project.rglob("*.pyc")), [], "none beside the model")
        stamps = (self.file(ident).stat().st_mtime_ns, (self.state / "documents" / ident / "document.json").stat().st_mtime_ns)
        self.run_script("cube")
        time.sleep(IDLE * 4)  # its idle window passed again
        self.assertEqual((self.file(ident).stat().st_mtime_ns,
                          (self.state / "documents" / ident / "document.json").stat().st_mtime_ns), stamps,
                         "an unchanged document is never rewritten")
        self.assertEqual(self.ask({"op": "status"})["cache"]["written"], 1)

    def home(self, action: str, path: str) -> tuple[int, dict]:
        """A change to the home's library, as the engine's page posts it (``/__cad/recents``)."""
        serving = self.ask({"op": "status"})["serving"]
        request = urllib.request.Request(f"{serving['url']}/__cad/recents", method="POST",
                                         data=json.dumps({"action": action, "path": path}).encode(),
                                         headers={"Authorization": "Bearer " + self.ask({"op": "http"})["token"],
                                                  "x-cadgen-viewer": "1", "content-type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")

    def test_delete_is_one_function_from_the_shell_and_from_the_home(self) -> None:
        cube, other = self.write("cube"), self.write("other")
        for name, ident in (("cube", cube), ("other", other)):
            self.run_script(name)
            self.cached(ident)
        # The shell refuses a document it cannot name, before anything is deleted.
        refused = self.cli("delete", cube, str(self.project / "nothing.py"))
        self.assertEqual(refused.returncode, 2, refused.stderr)
        self.assertIn(f"no document is recorded for {self.project / 'nothing.py'}", refused.stderr)
        self.assertTrue(self.record(cube) and self.file(cube).is_file(), "nothing was deleted")
        # Several at once, an id and a model's script: a line each; the record, the cached document and
        # the engine's copy go; the script stays.
        done = self.cli("delete", cube, str(self.project / "other.py"))
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, f"deleted cube {cube}\ndeleted other {other}\n", ""))
        for ident in (cube, other):
            self.assertEqual(self.record(ident), {})
            self.assertFalse(self.file(ident).parent.exists())
        self.assertEqual([row["id"] for row in self.ask({"op": "status"})["models"]], [])
        self.assertEqual(self.page(cube)[0], 404, "the engine holds it no more")
        self.assertTrue((self.project / "cube.py").is_file(), "never a file of the person's")
        # The next run registers it again, and the cache keeps it again.
        self.run_script("cube")
        self.cached(cube)
        # The home's Delete is the same deletion; Remove is a file's alone.
        self.assertEqual(self.home("remove", cube)[0], 400, "a model's row is deleted, never removed")
        status, body = self.home("delete", cube)
        self.assertEqual(status, 200, body)
        self.assertNotIn(cube, [row.get("id") for row in body["recents"]])
        self.assertEqual(self.record(cube), {})
        self.assertFalse(self.file(cube).parent.exists())
        self.assertEqual(self.page(cube)[0], 404)
        # With no engine running, the shell deletes the record and its file, and starts none.
        self.run_script("other")
        self.cached(other)
        self.assertEqual(self.cli("stop").returncode, 0)
        done = self.cli("delete", other, "--json")
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual(json.loads(done.stdout), {"ok": True, "deleted": [{"id": other, "name": "other", "kind": "model"}]})
        self.assertEqual((self.record(other), self.file(other).parent.exists()), ({}, False))
        self.assertIn("no engine server is running", self.cli("status").stdout)

    def test_a_restart_answers_every_verb_from_the_cache_and_an_edit_reruns_nothing(self) -> None:
        ident = self.write("cube")
        self.run_script("cube")
        live = {kind: self.folder / f"live.{kind}" for kind in ("xbf", "glb")}
        self.assertEqual(self.cli("export", ident, *map(str, live.values())).returncode, 0)
        self.cached(ident)
        self.assertEqual(self.cli("stop").returncode, 0)
        (self.project / "cube.ran").unlink()

        inspected = self.cli("inspect", ident, "--json")  # starts the engine afresh
        self.assertEqual(inspected.returncode, 0, inspected.stderr)
        self.assertEqual(json.loads(inspected.stdout)["closure"]["source"]["function"], "cube")
        self.assertEqual(json.loads(inspected.stdout)["summary"]["state"]["state"], "held")
        cold = {kind: self.folder / f"cold.{kind}" for kind in ("xbf", "glb")}
        exported = self.cli("export", ident, *map(str, cold.values()))
        self.assertEqual(exported.returncode, 0, exported.stderr)
        for kind in cold:
            self.assertEqual(cold[kind].read_bytes(), live[kind].read_bytes(), f"the {kind} is the live export's")
        from cadgen.engine.render import context

        if opengl.draws(context.available(), "the cold snapshot is drawn by OCCT"):
            snapped = self.cli("snapshot", ident, str(self.folder / "cold.png"))
            self.assertEqual(snapped.returncode, 0, snapped.stderr)
            self.assertGreater((self.folder / "cold.png").stat().st_size, 0)
        status, structure = self.page(ident)
        self.assertEqual(status, 200)
        self.assertEqual(structure["closure"]["source"]["function"], "cube")
        listed = self.ask({"op": "status"})
        (row,) = [row for row in listed["models"] if row["id"] == ident]
        self.assertEqual((row["state"], row["held"]), ("held", "memory and disk"))
        self.assertEqual([Path(worker["script"]).name for worker in listed["workers"]], ["documents"])
        from cadgen.mcp.server import _say_not_held

        with mock.patch.dict(os.environ, {"CADGEN_ENGINE_DIR": str(self.engine)}):
            self.assertIsNone(_say_not_held(ident), "cad_show shows it, with nothing to add")
        self.assertFalse(self.ran("cube"), "nothing ran the script")

        # The code changes: every surface answers the document of its last run, as it did, and says
        # nothing of the edit -- when to run the code again is the agent's call; nothing reruns.
        self.write("cube", width=5.0)
        (self.project / "cube.ran").unlink(missing_ok=True)
        self.assertEqual(self.page(ident), (status, structure), "the page reads the last run, as before")
        inspected = self.cli("inspect", ident)
        self.assertEqual((inspected.returncode, inspected.stderr), (0, ""))
        self.assertTrue(inspected.stdout.splitlines()[1].startswith("state       held (in memory and disk), cached, "
                                                                    "built "), inspected.stdout)
        with mock.patch.dict(os.environ, {"CADGEN_ENGINE_DIR": str(self.engine)}):
            self.assertIsNone(_say_not_held(ident))
        self.assertFalse(self.ran("cube"), "nothing reran it")

    def said(self, body: str = GOOD, width: float = 3.0) -> str:
        """``cube.py``, its model's body ``body``: the model's id."""
        (self.project / "cube.py").write_text(SAID.format(name="cube", width=width, body=body), encoding="utf-8")
        return model_id(self.project / "cube.py", "cube")

    def assert_taken(self) -> None:
        """A run of ``cube.py`` that took its document from the engine's cache: no body ran."""
        done = self.run_script("cube")
        self.assertIn("took cube.py::cube cold in", done.stdout)
        self.assertIn("the engine's cache holds the model's key, 1 bodies loaded, nothing ran", done.stdout)
        self.assertNotIn("the body ran", done.stderr)

    def test_an_unchanged_warm_run_is_taken_from_memory_and_an_edit_rebuilds(self) -> None:
        self.said()
        self.assertIn("the body ran", self.run_script("cube").stderr)
        warm = self.run_script("cube")
        self.assertNotIn("the body ran", warm.stderr)
        lines = warm.stdout.splitlines()
        self.assertRegex(lines[0], r"^took cube\.py::cube warm in \d+\.\d\ds: its document in memory holds the "
                                   r"model's key, 1 bodies loaded, nothing ran$")
        self.assertEqual(lines[1].replace("×", "x").replace("·", "|"),
                         "  parts 1 | bounds 3 x 2 x 1 mm | joints 0 | sequences 0", "the delta: nothing changed")
        self.said(width=5.0)
        rebuilt = self.run_script("cube")
        self.assertIn("the body ran", rebuilt.stderr)
        self.assertIn("rebuilt: cube.py: WIDTH changed", rebuilt.stdout)
        self.assertIn("bounds 5 x 2 x 1 mm (was 3 x 2 x 1)", rebuilt.stdout.replace("×", "x"))

    def test_after_a_restart_an_unchanged_run_is_taken_from_the_cache_and_an_edit_rebuilds(self) -> None:
        ident = self.said()
        self.assertIn("the body ran", self.run_script("cube").stderr)
        self.cached(ident)
        self.assertEqual(self.cli("stop").returncode, 0)
        self.assert_taken()
        exported = self.cli("export", ident, str(self.folder / "taken.step"))
        self.assertEqual(exported.returncode, 0, exported.stderr)
        self.said(width=5.0)
        rebuilt = self.run_script("cube")
        self.assertIn("built cube.py::cube warm", rebuilt.stdout)
        self.assertIn("the body ran", rebuilt.stderr)

    def corrupt(self, ident: str) -> None:
        """The document's cache file broken where its bytes are read, its size and its head (the writer
        and the closure a build compares) as they were: what an earlier test of this left behind."""
        path = self.file(ident)
        data = bytearray(path.read_bytes())
        from cadgen.engine import previous

        kept = data.find(previous._PREFIX)
        kept = data.find(b"\x00", kept) if kept >= 0 else 0
        self.assertLess(kept + 64, len(data) - 1024, "room past the head to break")
        data[-1024:] = bytes(1024)
        path.write_bytes(bytes(data))

    def test_a_cache_file_that_does_not_read_is_dropped_and_written_again(self) -> None:
        ident = self.said()
        self.assertIn("the body ran", self.run_script("cube").stderr)
        meta = self.cached(ident)
        whole = self.file(ident).read_bytes()
        self.assertEqual(self.cli("stop").returncode, 0)
        self.corrupt(ident)
        # After a restart, a verb that reads it fails once, says why and what builds it again, and drops
        # the file: it is not offered again, broken, after every restart.
        out = self.folder / "cube.step"
        exported = self.cli("export", ident, str(out))
        self.assertEqual(exported.returncode, 2, exported.stderr)
        self.assertIn(f"the engine's cache of {ident} does not read", exported.stderr)
        self.assertIn(f"run `cadgen run {self.project / 'cube.py'}` to build it again", exported.stderr)
        self.assertFalse(out.exists())
        self.assertNotIn("cache", self.record(ident))
        self.assertFalse(self.file(ident).exists())
        self.assertIn("the body ran", self.run_script("cube").stderr)
        self.assertEqual(self.cached(ident, meta["revision"])["bytes"], len(whole), "written again, whole")
        # A build that finds it broken (no verb read it first) runs the body, and the file is written again.
        self.assertEqual(self.cli("stop").returncode, 0)
        self.corrupt(ident)
        self.assertIn("the body ran", self.run_script("cube").stderr)
        self.cached(ident, meta["revision"])
        self.assertEqual(self.cli("stop").returncode, 0)
        self.assertEqual(self.cli("export", ident, str(out)).returncode, 0, "the next restart reads it")
        self.assertGreater(out.stat().st_size, 0)

    def test_failed_revisions_before_a_restart_leave_the_cache_to_take(self) -> None:
        self.use(self.state, self.cache, CADGEN_ENGINE_CACHE_IDLE="600")  # saved at the stop, as a person's is
        self.said()
        self.run_script("cube")
        self.run_script("cube")
        for broken in ("    return bd.Box(WIDTH, 2, 1", "    assert WIDTH < 0, 'too wide'\n    return bd.Box(WIDTH, 2, 1)",
                       "    return bd.fillet(bd.Box(WIDTH, 2, 1).edges(), radius=50)"):
            self.said(broken)
            failed = engine_daemon.run([sys.executable, "cube.py"], cwd=self.project, env=self.env)
            self.assertNotEqual(failed.returncode, 0, broken)
        self.said()
        self.run_script("cube")
        self.assertEqual(self.cli("stop").returncode, 0)
        self.assert_taken()

    def test_a_rebuild_replaces_the_cache_at_its_new_revision(self) -> None:
        ident = self.write("cube")
        self.run_script("cube")
        first = self.cached(ident)["revision"]
        before = self.file(ident).read_bytes()
        self.write("cube", width=6.0)
        self.run_script("cube")
        revision = self.record(ident)["revision"]
        self.assertNotEqual(revision, first)
        self.assertEqual(self.cached(ident, revision)["revision"], revision)
        self.assertNotEqual(self.file(ident).read_bytes(), before, "the new build's document")

    def test_a_build_arriving_during_a_save_waits_for_it_and_succeeds(self) -> None:
        from cadgen.engine import daemon, document

        ident = self.write("cube")
        script = str(self.project / "cube.py")
        saved: list[tuple[dict, bytes]] = []
        worker = daemon.Worker(script, {**os.environ, "PYTHONPATH": str(SRC)},
                               on_saved=lambda _worker, header, payload: saved.append((header, payload)))
        self.addCleanup(worker.stop)
        order = {"id": 1, "op": "build", "script": script, "functions": None, "cwd": str(self.project)}
        self.assertIn("done", worker.build(order, lambda _message: None))
        worker.save(ident, script, "cube", "g1")
        answer = worker.build({**order, "id": 2}, lambda _message: None)  # queued behind the save
        self.assertTrue(all(report["ok"] for report in answer["done"]), answer)
        (header, payload), = await_(lambda: saved, "the save's answer")
        self.assertEqual((header["ident"], header["generation"], header.get("error")), (ident, "g1", None))
        self.assertTrue(document.read_bytes(payload), "the whole document's bytes")

    def test_a_stop_saves_a_document_built_in_its_last_idle_window(self) -> None:
        self.use(self.state, self.cache, CADGEN_ENGINE_CACHE_IDLE="600")
        ident = self.write("cube")
        self.run_script("cube")
        self.assertFalse(self.file(ident).exists(), "not idle yet")
        self.assertEqual(self.cli("stop").returncode, 0)
        self.assertEqual(self.record(ident)["cache"]["revision"], self.record(ident)["revision"])
        self.assertTrue(self.file(ident).is_file())
        # The next engine's viewer opens it by its id, as it held it.
        launched = engine_daemon.run([sys.executable, "-m", "cadgen.viewer", "--json", "--api-only", ident],
                                     cwd=self.project, env=self.env)
        self.assertEqual(launched.returncode, 0, launched.stderr)
        self.assertEqual([row["id"] for row in json.loads(launched.stdout)["documents"]], [ident])

    def test_every_document_stays_until_deleted_and_nothing_else_in_the_folder_is_touched(self) -> None:
        foreign = self.cache / "objects" / "ab" / "0.7-object"
        foreign.parent.mkdir(parents=True)
        foreign.write_bytes(b"a 0.7 store object")
        beside = self.cache / "index.db"
        beside.write_bytes(b"0.7")
        stamps = {path: path.stat().st_mtime_ns for path in (foreign, beside)}
        # Its workers go after each build, over a memory cap of 1 MB, and its memory budget is past at
        # once: every document leaves memory, and the cache keeps them all.
        self.use(self.state, self.cache, CADGEN_ENGINE_WORKER_RSS_MB="1", CADGEN_ENGINE_MEMORY_MB="0.001")
        idents = [self.write(name) for name in ("first", "second", "third")]
        for name, ident in zip(("first", "second", "third"), idents):
            self.run_script(name)
            self.cached(ident)
        await_(lambda: self.ask({"op": "status"})["workers"] == [], "the workers to retire")
        time.sleep(IDLE * 4)  # idle windows past: nothing more happens to the files
        self.assertTrue(all(self.file(ident).is_file() for ident in idents), "nothing evicts a document")
        cache = self.ask({"op": "status"})["cache"]
        self.assertEqual(sorted(cache), ["bytes", "documents", "enabled", "failures", "idleSeconds", "root", "written"],
                         "no cap, no evictions")
        self.assertEqual((cache["documents"], cache["root"]), (3, str(self.cache)))
        total = sum(self.file(ident).stat().st_size for ident in idents)
        from cadgen.engine.cli import _bytes

        self.assertIn(f"cache {self.cache}: 3 documents · {_bytes(total)}, kept until deleted\n", self.cli("status").stdout)
        self.assertEqual([(row["state"], row.get("held")) for row in self.ask({"op": "status"})["models"]],
                         [("held", "disk")] * 3, "out of memory, each read again from the cache")
        exported = self.cli("export", idents[0], str(self.folder / "first.step"))
        self.assertEqual(exported.returncode, 0, exported.stderr)
        self.assertEqual({path: path.stat().st_mtime_ns for path in stamps}, stamps, "the rest of the folder is not ours")
        self.assertEqual(foreign.read_bytes(), b"a 0.7 store object")

    def test_a_declared_xbf_model_is_cached_its_file_untouched_and_a_file_document_gets_none(self) -> None:
        (self.project / "plate.py").write_text(textwrap.dedent(DECLARED), encoding="utf-8")
        self.run_script("plate")
        declared = self.project / "XBF" / "plate.xbf"
        written = (declared.read_bytes(), declared.stat().st_mtime_ns)
        ident = model_id(self.project / "plate.py", "plate")
        meta = self.cached(ident)
        self.assertNotIn(str(declared), json.dumps(self.record(ident)), "the cache never points at the author's file")
        served = self.ask({"op": "serve", "path": str(declared)})
        self.assertEqual(served.get("doc"), file_id(declared))
        time.sleep(IDLE * 4)
        self.assertEqual((declared.read_bytes(), declared.stat().st_mtime_ns), written, "the declared file is the author's")
        self.assertEqual(sorted(os.listdir(self.cache / "documents")), [ident], "a file's document is its own file")
        self.assertEqual(meta["bytes"], self.file(ident).stat().st_size)

    def test_a_model_declaring_a_step_is_taken_by_its_key_after_a_restart_and_its_file_written_from_it(self) -> None:
        (self.project / "plate.py").write_text(textwrap.dedent(STEPPED.format(width=4)), encoding="utf-8")
        ident = model_id(self.project / "plate.py", "plate")
        self.assertIn("the body ran", self.run_script("plate").stderr)
        declared = self.project / "plate.step"
        first = declared.read_bytes()
        self.cached(ident)
        self.assertNotIn("files", self.record(ident), "nothing records which files a model wrote")
        self.assertEqual(self.cli("stop").returncode, 0)
        # Unchanged after a restart: taken by its key, nothing run; its STEP is the document's export, as
        # `cadgen export` writes it of the document taken (OCCT's xbf read gives a -0. back for a 0.:
        # the first run after a restart may write it once, the same geometry).
        done = self.run_script("plate")
        self.assertIn("took plate.py::plate cold in", done.stdout)
        self.assertIn("the engine's cache holds the model's key", done.stdout)
        self.assertNotIn("the body ran", done.stderr)
        exported = self.folder / "exported.step"
        self.assertEqual(self.cli("export", ident, str(exported)).returncode, 0)
        taken = declared.read_bytes()
        self.assertEqual(taken, exported.read_bytes(), "exactly an export of the document")
        # Unchanged, it is compared and left as it is: its bytes and its time.
        stamp = declared.stat().st_mtime_ns
        done = self.run_script("plate")
        self.assertIn("took plate.py::plate warm in", done.stdout)
        self.assertEqual((declared.read_bytes(), declared.stat().st_mtime_ns), (taken, stamp))
        # Deleted: the next run writes it again from the document, and still runs nothing.
        declared.unlink()
        done = self.run_script("plate")
        self.assertIn("took plate.py::plate warm in", done.stdout)
        self.assertNotIn("the body ran", done.stderr)
        self.assertEqual(declared.read_bytes(), taken)
        # A key change runs the body.
        (self.project / "plate.py").write_text(textwrap.dedent(STEPPED.format(width=5)), encoding="utf-8")
        done = self.run_script("plate")
        self.assertIn("the body ran", done.stderr)
        self.assertNotEqual(declared.read_bytes(), first)

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                     "a read-only folder is POSIX's, and refuses no one running as root")
    def test_with_its_cache_folder_read_only_the_engine_saves_nothing_and_does_everything_else(self) -> None:
        def scenario(tag: str, **extra: str) -> dict:
            if tag == "read-only":
                self.use(self.folder / f"state-{tag}", self.folder / f"cache-{tag}", **extra)
                self.cache.mkdir()
                self.cache.chmod(stat.S_IRUSR | stat.S_IXUSR)
                self.addCleanup(self.cache.chmod, stat.S_IRWXU)
            else:  # no folder can be made there at all: a file is in its place
                self.use(self.folder / f"state-{tag}", Path(engine_daemon.unwritable(self.folder / tag)), **extra)
            ident = self.write("cube")
            seen: dict = {"build": [line for line in self.run_script("cube").stdout.splitlines() if "id:" in line]}
            time.sleep(IDLE * 4)  # idle: a save is tried
            status, structure = self.page(ident)
            seen["page"] = (status, sorted(structure))
            seen["state"] = self.ask({"op": "state", "id": ident})
            out = self.folder / f"{tag}.xbf"
            seen["export"] = (self.cli("export", ident, str(out)).returncode, out.read_bytes())
            seen["inspect"] = self.cli("inspect", ident, "--query", "closure").stdout
            listed = self.ask({"op": "status"})
            seen["models"] = [(row["state"], row.get("held")) for row in listed["models"]]
            failures = listed["cache"]["failures"]
            self.assertEqual(self.cli("stop").returncode, 0)
            (self.project / "cube.ran").unlink()
            again = self.cli("inspect", ident)
            seen["restart"] = (again.returncode, again.stderr)
            seen["restart state"] = self.ask({"op": "state", "id": ident})
            seen["restart page"] = self.page(ident)
            seen["ran"] = self.ran("cube")
            self.assertEqual(self.cli("stop").returncode, 0)
            seen = json.loads(json.dumps(seen, default=lambda value: value.hex()).replace(str(self.state), "STATE"))
            return seen, failures, list(self.cache.iterdir()) if self.cache.is_dir() else []

        read_only, failures, left = scenario("read-only")
        self.assertTrue(failures, "the failed save is listed")
        self.assertEqual(left, [], "and nothing is cached")
        unwritable, failures, _left = scenario("unwritable")
        self.assertTrue(failures, "the failed save is listed there too")
        self.assertEqual(read_only, unwritable, "every verb, every state, every message alike: nothing saved")
        self.assertEqual(read_only["restart"][0], 2, "after a restart it is not held: its command is said")


class KeptTest(unittest.TestCase):
    """The cache on its own: it has no cap, and keeps every file it writes, a shape handed over's as a
    model's; a document whose file is gone says to hand it over again."""

    def test_no_cap_every_file_written_stays(self) -> None:
        from cadgen.engine import cache as engine_cache

        folder = Path(os.path.realpath(tempfile.mkdtemp(prefix="engine-kept-")))
        self.addCleanup(shutil.rmtree, folder, True)
        held = Registry(folder / "state", folder / "cache")

        class Models:
            def registry(self) -> Registry:
                return held

            def row(self, _ident: str) -> None:
                return None

        names = ["a_shape", "a_model", "b_shape", "b_model"]
        idents = {name: model_id(folder / f"{name}.py", name) for name in names}
        for name, ident in idents.items():
            held.put({"id": ident, "kind": "shape" if name.endswith("shape") else "model",
                      "root": str(folder), "home": f"{name}.py", "entry": name, "revision": "r1"})
        self.assertFalse(hasattr(engine_cache, "cap") or hasattr(engine_cache, "CACHE_GB"), "no cap to set")
        first, second = engine_cache.Cache(Models()), engine_cache.Cache(Models())
        for name in names[:3]:
            first._write(idents[name], "r1", b"x" * 1000)
        second._write(idents["b_model"], "r1", b"x" * 1000)  # an engine started after the first three
        kept = {name for name, ident in idents.items() if (folder / "cache" / "documents" / ident / "document.xbf").is_file()}
        self.assertEqual(kept, set(names), "every file stays")
        self.assertTrue(all(held.get(ident)["cache"]["bytes"] == 1000 for ident in idents.values()))
        status = second.status()
        self.assertEqual((status["documents"], status["bytes"]), (4, 4000))
        self.assertNotIn("capBytes", status)
        self.assertNotIn("evictions", status)

    def test_a_shape_taken_up_after_a_restart_and_unsaved_names_the_program_that_handed_it_over(self) -> None:
        from cadgen.engine import registry as registering
        from cadgen.engine.models import Models as Book

        folder = Path(os.path.realpath(tempfile.mkdtemp(prefix="engine-shape-")))
        self.addCleanup(shutil.rmtree, folder, True)
        held = Registry(folder / "state", folder / "cache")
        ident = model_id(folder / "hand.py", "crate")
        words = Book.shown(str(folder / "hand.py"))
        held.put({"id": ident, "kind": "shape", "root": str(folder), "home": "hand.py", "program": "hand.py",
                  "entry": "crate", "revision": "h1", "built": 1.0})
        held.cache.write(f"documents/{ident}/document.xbf", b"x" * 10)
        held.update(ident, lambda found: {**found, "cache": {"revision": "h1", "bytes": 10}})
        daemon = mock.Mock(server=None)
        with mock.patch.object(registering, "FACTORY", lambda: held):
            book = Book(daemon)
            book.adopt(ident)  # a restart: taken up from its record, its cache file at its revision
            self.assertEqual(book.state(ident), "held")
            book.cache.remove(ident)  # its file gone (its bytes did not read)
            found = book.rebuild(ident)
        self.assertEqual((found["state"], found["rebuild"]), ("unsaved", None))
        self.assertIn(words, found["error"], "the words of the program its record names, not a Python session's")


if __name__ == "__main__":
    unittest.main()
