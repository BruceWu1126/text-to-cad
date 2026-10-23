"""Document lifetime: a document outlives its worker (drained before the worker goes), everything
the daemon holds lives under one memory budget -- files the engine reads again itself evicted
before every other document, least recently used first, a watched document never -- the system's
memory-pressure signal evicts down to the low-water mark, and a document no longer held names the
command that builds it again, which nothing runs."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import textwrap
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from tests.python.support import engine_daemon

SRC = Path(__file__).resolve().parents[5] / "packages" / "cadgen" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cadgen.engine.ids import model_id  # noqa: E402
from cadgen.engine.serve import LOW_WATER, Server  # noqa: E402

MIB = 1 << 20


def await_(condition, what: str, seconds: float = 60.0):
    deadline = time.monotonic() + seconds
    while True:
        found = condition()
        if found:
            return found
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.02)


def _document(name: str) -> dict:
    return {"name": name, "version": "1-0", "prototypes": [{"key": f"{name}-key", "name": name}],
            "instances": [], "tree": {"name": name}}


class BudgetTest(unittest.TestCase):
    """The budget over a server's documents and tessellations, its tiers, and the pressure signal."""

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="engine-lifetime-")).resolve()
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.server = Server(port=0, mesh_mode="inline", budget=64 * MIB)
        self.addCleanup(self.server.stop)

    def file(self, name: str, size: int) -> str:
        """A document of a file on disk (one the engine could read again), ``size`` MiB of shape bytes."""
        path = self.folder / f"{name}.xbf"
        path.write_bytes(b"")
        return self.server.put(path, _document(name), {f"{name}-key": b"f" * (size * MIB)})

    def model(self, name: str, size: int) -> str:
        """A model's document (its address: nothing on disk reads it back), ``size`` MiB of shape bytes."""
        return self.server.put(f"{self.folder / name}.py::{name}", _document(name), {f"{name}-key": b"m" * (size * MIB)})

    def test_files_go_before_other_documents_least_recently_used_first_and_a_watched_one_never(self) -> None:
        server = self.server
        server.cache.put("mesh", b"x" * (4 * MIB))
        older = self.model("older", 20)
        first = self.file("first", 20)
        newer = self.model("newer", 16)
        self.assertEqual(len(server.served), 3, "under the budget nothing goes")
        second = self.file("second", 20)  # 80 MiB: past the budget, trimmed to 80 % of it
        self.assertNotIn("mesh", server.cache.items, "a tessellation goes first: it is remade from the shape")
        self.assertEqual(set(server.served), {newer, second},
                         "the other file first, then the least recently used model; the document put stays")
        self.assertLessEqual(server.holding(), int(64 * MIB * 0.8))
        self.assertNotIn(older, server.served)
        self.assertNotIn(first, server.served)
        server.sync(newer)  # a page watches it
        third = self.model("third", 30)
        self.assertEqual(set(server.served), {newer, third}, "the file goes before any model")
        fourth = self.model("fourth", 30)
        self.assertEqual(set(server.served), {newer, fourth}, "a watched document is never evicted")
        self.assertEqual(server.memory()["evictions"], 4)

    def test_a_pressure_signal_evicts_down_to_the_low_water_mark(self) -> None:
        server = self.server
        server.start()
        watched = self.model("watched", 10)
        for name in ("a", "b", "c", "d", "e"):
            self.model(name, 10)
        self.assertEqual(len(server.served), 6, "60 MiB: under the budget")
        server.sync(watched)
        server.pressure.simulate(True)
        await_(lambda: server.holding() <= int(64 * MIB * LOW_WATER), "the low-water mark")
        server.pressure.simulate(None)
        self.assertIn(watched, server.served, "pressure never evicts a watched document either")
        self.assertGreater(server.holding(), int(64 * MIB * LOW_WATER) - 10 * MIB, "no further than the mark")

    def test_an_evicted_file_is_read_again_under_its_id(self) -> None:
        from cadgen.engine import build as engine_build

        script = self.folder / "plate.py"
        script.write_text(textwrap.dedent("""
            import build123d as bd
            from cadgen.engine import xbf


            @xbf("XBF/plate.xbf")
            def plate():
                return bd.Box(4, 3, 1)
        """), encoding="utf-8")
        self.assertTrue(engine_build.build(script, ["plate"], cwd=self.folder)[-1]["ok"])
        ident = self.server.add(self.folder / "XBF/plate.xbf")
        version = self.server.document(ident).version
        self.assertEqual(self.server.trim(0), [ident])
        self.assertNotIn(ident, self.server.served)
        self.assertTrue(self.server.holds(ident), "known by its id still")
        self.assertEqual(self.server.document(ident).version, version, "read again from its file, the same document")
        self.assertIn(ident, self.server.served)


SCRIPT = """
import pathlib
import build123d as bd
from cadgen.engine import model

pathlib.Path(__file__).with_suffix(".ran").write_text("ran", encoding="utf-8")


@model{store}
def {name}():
    return bd.Box(3, 2, 1)
"""

HINGE = """
import pathlib
import build123d as bd
from cadgen.engine import mechanism_state, model

pathlib.Path(__file__).with_suffix(".ran").write_text("ran", encoding="utf-8")


@model
def {name}():
    base = bd.Box(100, 60, 20)
    base.label = "base"
    arm = bd.Box(120, 20, 10, align=(bd.Align.MIN, bd.Align.CENTER, bd.Align.MIN))
    arm.label = "arm"
    bd.RevoluteJoint("elbow", base, axis=bd.Axis((40, 0, 20), (0, 1, 0)), angular_range=(-10, 120))
    bd.RigidJoint("mount", arm, bd.Location())
    base.joints["elbow"].connect_to(arm.joints["mount"], angle=30)
    root = bd.Compound(children=[base, arm], label="hinge")
    mechanism_state(root, "raised", elbow=90)
    return root
"""

CALLER = """
import {name}

{name}.{name}()
"""


def get(url: str, token: str) -> tuple[int, dict]:
    request = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


class LifetimeDaemonTest(unittest.TestCase):
    """Through a daemon, its workers real processes retired after a second idle."""

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="engine-lifetime-")).resolve()
        self.state = Path(tempfile.mkdtemp(prefix="eng-", dir="/tmp" if os.name != "nt" else None)).resolve()
        self.env = engine_daemon.watched({**os.environ, "PYTHONPATH": str(SRC), "CADGEN_ENGINE_DIR": str(self.state),
                                          "CADGEN_ENGINE_IDLE": "120", "CADGEN_ENGINE_MESH": "inline",
                                          "CADGEN_ENGINE_WORKER_IDLE": "1"}, self.addCleanup)
        # This process asks the test's daemon (``status``, ``export``), never starting one.
        self.enterContext(mock.patch.dict(os.environ, {"CADGEN_ENGINE_DIR": str(self.state)}))
        self.addCleanup(self._stop)

    def _stop(self) -> None:
        engine_daemon.run([sys.executable, "-m", "cadgen.engine", "stop"], cwd=self.folder, env=self.env)
        shutil.rmtree(self.folder, ignore_errors=True)
        shutil.rmtree(self.state, ignore_errors=True)

    def ask(self, message: dict) -> dict:
        from cadgen.engine.client import request

        return request(message, start=False) or {}

    def row(self, ident: str, state: str | None = None) -> dict:
        """The status row of document ``ident``; with ``state``, once it is in that state."""
        def found() -> dict:
            return next((row for row in self.ask({"op": "status"}).get("models") or [] if row["id"] == ident), {})

        return await_(lambda: (lambda row: row if row.get("state") == state else None)(found()),
                      f"{ident} {state}") if state else found()

    def built(self, name: str, store: str = "", env: dict | None = None, script: str = SCRIPT) -> str:
        """``python caller_NAME.py`` builds model ``name`` of ``NAME.py``: its id, once its worker retired."""
        (self.folder / f"{name}.py").write_text(textwrap.dedent(script.format(name=name, store=store)), encoding="utf-8")
        caller = self.folder / f"caller_{name}.py"
        caller.write_text(textwrap.dedent(CALLER.format(name=name)), encoding="utf-8")
        done = engine_daemon.run([sys.executable, caller.name], cwd=self.folder, env=env or self.env)
        self.assertEqual(done.returncode, 0, done.stderr)
        await_(lambda: not any(Path(w["script"]).name == f"{name}.py" for w in self.ask({"op": "status"})["workers"]),
               f"{name}'s worker to retire")
        (self.folder / f"{name}.ran").unlink()
        return model_id(self.folder / f"{name}.py", name)

    def page(self, ident: str) -> tuple[int, dict]:
        serving = self.ask({"op": "status"})["serving"]
        token = self.ask({"op": "http"}).get("token", "")
        return get(f"{serving['url']}/api/doc/{ident}", token)

    def test_a_held_document_exports_by_id_with_no_worker_of_its_script(self) -> None:
        ident = self.built("bracket")
        self.row(ident, "held")  # its worker gone, its document drained first
        out = self.folder / "bracket.stl"
        answer = self.ask({"op": "export", "source": {"id": ident}, "destinations": [str(out), str(out.with_suffix(".step"))],
                           "cwd": str(self.folder), "env": dict(os.environ)})
        self.assertIn("done", answer, answer)
        self.assertGreater(out.stat().st_size, 0)
        self.assertGreater(out.with_suffix(".step").stat().st_size, 0)
        inspected = engine_daemon.run([sys.executable, "-m", "cadgen.engine", "inspect", f"{self.folder / 'bracket.py'}::bracket"],
                                      cwd=self.folder, env=self.env)
        self.assertEqual(inspected.returncode, 0, inspected.stderr)
        self.assertIn("source", json.loads(inspected.stdout), "its closure, from the held document")
        self.assertEqual([Path(w["script"]).name for w in self.ask({"op": "status"})["workers"]], ["documents"],
                         "the documents worker read the held bytes: no worker of its script started")
        self.assertFalse((self.folder / "bracket.ran").exists(), "nothing ran the script")
        # Its author's run builds it again: the same id, a new version.
        again = engine_daemon.run([sys.executable, "caller_bracket.py"], cwd=self.folder, env=self.env)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual((self.row(ident)["version"], self.row(ident)["id"]), (2, ident))

    def test_a_held_mechanism_exports_whole_from_the_hold(self) -> None:
        from cadgen.engine import document
        from tests.python.packages.cadgen.engine.test_kinematics import _arm_tip, _has_point, _vertices

        ident = self.built("hinge", script=HINGE)
        self.row(ident, "held")
        out = {kind: self.folder / f"held.{kind}" for kind in ("xbf", "step")}

        def export(destinations: list[Path], pose: str | None = None) -> None:
            answer = self.ask({"op": "export", "source": {"id": ident}, "destinations": [str(p) for p in destinations],
                               "pose": pose, "cwd": str(self.folder), "env": dict(os.environ)})
            self.assertIn("done", answer, answer)

        export(list(out.values()))
        posed = self.folder / "raised.step"
        export([posed], "raised")
        held = document.Loaded(out["xbf"])
        (pair,) = held.mechanism.pairs
        self.assertEqual((pair.label, pair.kind, pair.limits), ("elbow", "revolute", ((-10.0, 120.0),)))
        self.assertEqual((held.mechanism.as_built, held.mechanism.configurations),
                         ({"elbow": (30.0,)}, {"raised": {"elbow": (90.0,)}}), "the xbf carries the mechanism")
        self.assertTrue(_has_point(_vertices(document.Loaded(out["step"]), "arm"), _arm_tip(30)),
                        "the STEP places the arm as built")
        self.assertTrue(_has_point(_vertices(document.Loaded(posed), "arm"), _arm_tip(90)),
                        "a pose of the held document is its mechanism's")
        # The viewer's export of the page's revision reads the hold too.
        _status, shown = self.page(ident)
        viewed = self.folder / "viewed.xbf"
        answer = self.ask({"op": "export", "source": {"script": str(self.folder / "hinge.py"), "function": "hinge"},
                           "expected": {"address": f"{self.folder / 'hinge.py'}::hinge", "digest": shown["exportRevision"],
                                        "modelRevision": shown["version"]}, "destinations": [str(viewed)]})
        self.assertIn("done", answer, answer)
        self.assertIsNotNone(document.Loaded(viewed).mechanism)
        self.assertEqual([Path(w["script"]).name for w in self.ask({"op": "status"})["workers"]], ["documents"])
        self.assertFalse((self.folder / "hinge.ran").exists(), "nothing ran the script")

    def test_an_evicted_and_an_expired_document_name_their_rebuild_and_nothing_runs_it(self) -> None:
        tiny = {**self.env, "CADGEN_ENGINE_MEMORY_MB": "0.001"}  # every document is evicted once drained
        evicted = self.built("arm", env=tiny)
        expired = self.built("cell", store='(store="memory")', env=tiny)
        command = f"python {self.folder / 'caller_arm.py'}"
        row = self.row(evicted, "evicted")
        self.assertEqual(row["rebuild"], command, "the program that built it, not its script")
        status, body = self.page(evicted)
        self.assertEqual((status, body["state"], body["rebuild"]), (404, "evicted", command))
        inspected = engine_daemon.run([sys.executable, "-m", "cadgen.engine", "inspect", f"{self.folder / 'arm.py'}::arm"],
                                      cwd=self.folder, env=self.env)
        self.assertNotEqual(inspected.returncode, 0)
        self.assertIn(f"run `{command}`", inspected.stderr)
        row = self.row(expired, "expired")
        self.assertEqual(row["rebuild"], f"python {self.folder / 'caller_cell.py'}")
        status, body = self.page(expired)
        self.assertEqual((status, body["state"]), (404, "expired"))
        self.assertIn("it is kept in memory only", body["error"])
        listed = engine_daemon.run([sys.executable, "-m", "cadgen.engine", "status"], cwd=self.folder, env=self.env)
        self.assertIn(f"evicted   arm   {evicted}  run `{command}`", listed.stdout)
        self.assertIn(f"expired   cell  {expired}", listed.stdout)
        from cadgen.mcp.server import ToolFailed, _say_not_held

        with self.assertRaisesRegex(ToolFailed, "kept in memory only"):
            _say_not_held(expired)
        self.assertFalse((self.folder / "arm.ran").exists(), "nothing ran the script to restore it")
        self.assertFalse((self.folder / "cell.ran").exists())


if __name__ == "__main__":
    unittest.main()
