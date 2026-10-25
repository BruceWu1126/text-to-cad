"""An installation's page links outlive its engine: one HTTP port (``--port``, else
``CADGEN_ENGINE_PORT``, else 3245 or the port it rolled to when 3245 was another's, remembered) and
the user's one page token, every installation's, the same at every start until ``cadgen server
--rotate-token``. A named port another process holds is refused by name; another installation's
engine that nothing uses is retired."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support import engine_daemon

SRC = Path(__file__).resolve().parents[5] / "packages" / "cadgen" / "src"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def held_port(test: unittest.TestCase) -> int:
    """A free port, then held by a plain listener of this process's: another program's."""
    holder = socket.socket()
    test.addCleanup(holder.close)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    return holder.getsockname()[1]


def other_installation(root: Path) -> str:
    """The interpreter of a second installation: a virtual environment of its own (its own
    ``sys.prefix``, so its own engine, ``daemon._tag``) that imports what this one does."""
    import sysconfig

    folder = root / "other"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(folder)], check=True)
    (site,) = (folder / "Lib" / "site-packages",) if os.name == "nt" else folder.glob("lib/python*/site-packages")
    (site / "inherit.pth").write_text(f"import site; site.addsitedir({sysconfig.get_paths()['purelib']!r})\n",
                                      encoding="utf-8")
    return str(folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))


class Listener:
    """What ``daemon.bind``'s ``listen`` makes, in process: a plain listener on ``port``."""

    def __init__(self, port: int, token: str) -> None:
        self.socket = socket.socket()
        try:
            self.socket.bind(("127.0.0.1", port))
        except OSError:
            self.socket.close()
            raise
        self.socket.listen(1)
        self.port, self.token = self.socket.getsockname()[1], token


def listening(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
    except OSError:
        return False
    return True


class EnginePortTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="eport-", dir="/tmp" if os.name != "nt" else None)).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)
        environment = {key: value for key, value in os.environ.items() if key != "CADGEN_ENGINE_PORT"}
        self.env = engine_daemon.watched({**environment, "PYTHONPATH": str(SRC),
                                          "CADGEN_ENGINE_DIR": str(self.root / "engine"),
                                          "CADGEN_STATE_DIR": str(self.root / "state"),
                                          "CADGEN_CACHE_DIR": str(self.root / "cache")},
                                         self.addCleanup)
        self.addCleanup(self.stop)

    def cli(self, *args: str, python: str = sys.executable, **extra: str) -> subprocess.CompletedProcess:
        return engine_daemon.run([python, "-m", "cadgen.engine", *args], env={**self.env, **extra})

    def stop(self, python: str = sys.executable) -> None:
        done = self.cli("stop", python=python)
        self.assertEqual(done.returncode, 0, done.stderr)

    def started(self, python: str = sys.executable, **extra: str) -> subprocess.CompletedProcess:
        """The viewer's launcher, run: it starts (or reuses) its installation's engine and its page."""
        return engine_daemon.run([python, "-m", "cadgen.viewer", "--api-only", "--json"],
                                 env={**self.env, **extra}, cwd=self.root)

    def launch(self, python: str = sys.executable, **extra: str) -> dict:
        """The engine's page, started (or reused) by the viewer's launcher: its port, token and pid."""
        done = self.started(python, **extra)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def answering(self, *expected: Path) -> None:
        """Await the engines whose doors answer in the test's engine folder being ``expected``."""
        from cadgen.engine import client

        deadline = time.monotonic() + 30
        while {door for door in (self.root / "engine").glob("*.port" if os.name == "nt" else "*.door")
               if client._answering(door)} != set(expected):
            self.assertLess(time.monotonic(), deadline, f"only {expected} answer")
            time.sleep(0.05)

    def ours(self) -> Path:
        from cadgen.engine import daemon

        with mock.patch.dict(os.environ, {"CADGEN_ENGINE_DIR": self.env["CADGEN_ENGINE_DIR"]}):
            return daemon.address()

    def other(self) -> tuple[str, dict, Path]:
        """A second installation's engine on a port of this test's, its page launched: its
        interpreter, the launch (``pid``, ``port``) and its door, stopped at the end."""
        python = other_installation(self.root)
        self.addCleanup(self.stop, python)
        launched = self.launch(python, CADGEN_ENGINE_PORT=str(free_port()))
        (door,) = (self.root / "engine").glob("*.port" if os.name == "nt" else "*.door")
        self.assertNotEqual(door, self.ours(), "another installation's engine")
        return python, launched, door

    def server(self, *args: str) -> None:
        """``cadgen server ARGS`` in the background, once its door answers."""
        from cadgen.engine import client, daemon

        process = subprocess.Popen([sys.executable, "-m", "cadgen.engine", "server", *args], env=self.env,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(engine_daemon.reap, process)
        with mock.patch.dict(os.environ, {"CADGEN_ENGINE_DIR": self.env["CADGEN_ENGINE_DIR"]}):
            deadline = time.monotonic() + 60
            while (found := client._connect(daemon.address())) is None:
                self.assertIsNone(process.poll(), "the server exited before its door answered")
                self.assertLess(time.monotonic(), deadline, "the server's door answered")
                time.sleep(0.05)
            found.close()

    def record(self) -> dict:
        """This installation's engine record, or ``{}`` with none."""
        found = [path for path in (self.root / "state" / "engines").glob("*.json")]
        if not found:
            return {}
        (found,) = found
        if os.name != "nt":
            self.assertEqual(found.stat().st_mode & 0o777, 0o600)
        return json.loads(found.read_text(encoding="utf-8"))

    def token(self) -> str:
        """The user's page token, as its file holds it."""
        found = self.root / "state" / "engines" / "token"
        if os.name != "nt":
            self.assertEqual(found.stat().st_mode & 0o777, 0o600)
        return found.read_text(encoding="utf-8").strip()

    def bound(self, default: int) -> tuple[Listener, dict]:
        """``daemon.bind`` in this process, nothing named, its default ``default``: its listener
        (closed at the end) and where its port came from."""
        from cadgen.engine import daemon, registry

        environment = {key: value for key, value in os.environ.items() if key != "CADGEN_ENGINE_PORT"}
        with mock.patch.dict(os.environ, {**environment, "CADGEN_ENGINE_DIR": self.env["CADGEN_ENGINE_DIR"]},
                             clear=True), mock.patch.object(daemon, "DEFAULT_PORT", default), \
                mock.patch.object(registry, "FACTORY", lambda: registry.Registry(self.root / "state")):
            server, where = daemon.bind("127.0.0.1", None, Listener)
        self.addCleanup(server.socket.close)
        return server, where

    def remembered(self) -> int | None:
        from cadgen.engine import daemon, registry

        return registry.Registry(self.root / "state").engine(daemon._tag()).get("port")

    def test_the_default_port_is_3245(self) -> None:
        from cadgen.engine.daemon import DEFAULT_PORT

        self.assertEqual(DEFAULT_PORT, 3245)
        if listening(DEFAULT_PORT):
            self.skipTest("3245 is another engine's on this machine")
        self.assertEqual(self.launch()["port"], 3245)
        self.assertIn("serving http://127.0.0.1:3245 (the default port)", self.cli("status").stdout)

    def test_a_port_another_program_holds_is_refused_by_name_and_leaves_no_engine(self) -> None:
        port = held_port(self)
        done = self.started(CADGEN_ENGINE_PORT=str(port))
        self.assertEqual(done.returncode, 1, done.stdout)
        (said,) = done.stderr.strip().splitlines()[-1:]
        self.assertRegex(said, rf"^CAD Viewer: port {port} is held by (.+ \(pid (\d+)\)|another process): "
                               r"stop it, or start cadgen with another CADGEN_ENGINE_PORT$")
        if os.name != "nt":  # the system names the holder: this process
            self.assertIn(f" (pid {os.getpid()}): ", said)
        self.answering()

    def test_cadgen_server_on_a_held_port_fails_at_once_and_leaves_no_engine(self) -> None:
        port = held_port(self)
        done = self.cli("server", "--port", str(port))  # no request is ever sent: it binds at its start
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertRegex(done.stderr.strip().splitlines()[-1],
                         rf"^cadgen: port {port} is held by .+: stop it, or pass another --port$")
        self.answering()

    def test_a_held_default_rolls_to_the_next_free_port_says_so_once_and_keeps_it(self) -> None:
        mine = not listening(3245)
        if mine:  # else another program's already: the same roll
            holder = socket.socket()
            self.addCleanup(holder.close)
            holder.bind(("127.0.0.1", 3245))
            holder.listen(1)
        done = self.started()
        self.assertEqual(done.returncode, 0, done.stderr)
        port = json.loads(done.stdout)["port"]
        self.assertTrue(3245 < port <= 3245 + 64, port)
        pid = str(os.getpid()) if mine and os.name != "nt" else r"\d+"  # the system names the holder
        said = rf"3245 is held by (.+pid {pid}\)|another process); this installation serves on {port} and keeps it"
        self.assertRegex(done.stderr, rf"(?m)^cadgen: {said}$")
        self.assertEqual(self.record()["port"], port)
        status = self.cli("status").stdout
        self.assertRegex(status, rf"serving http://127\.0\.0\.1:{port} \(rolled from 3245 because .+ holds it\)")
        self.assertRegex(status, rf"(?m)^  {said}$")
        self.stop()
        again = self.started()  # the remembered port: the same links, nothing said
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(json.loads(again.stdout)["port"], port)
        self.assertNotIn("3245 is held", again.stderr + self.cli("status").stdout)

    def test_the_installation_port_returns_to_a_free_default_and_rolls_from_a_held_remembered_one(self) -> None:
        from cadgen.engine import daemon, registry

        default, remembered = held_port(self), free_port()
        registry.Registry(self.root / "state").put_token("t" * 43)
        registry.Registry(self.root / "state").put_engine(daemon._tag(), {"port": remembered})
        server, where = self.bound(default)  # the default held, the remembered port free: used, nothing said
        self.assertEqual((server.port, where["source"], "notice" in where), (remembered, "rolled", False))
        self.assertEqual(server.token, "t" * 43)
        server.socket.close()
        held = Listener(remembered, "")  # the remembered port held too: the roll starts from it
        self.addCleanup(held.socket.close)
        server, where = self.bound(default)
        self.assertGreater(server.port, remembered)
        self.assertRegex(where["notice"], rf"^{remembered} is held by .+; this installation serves on {server.port} "
                                          "and keeps it$")
        self.assertEqual(self.remembered(), server.port)
        server.socket.close()
        default = free_port()  # the default free again: taken back, the rolled port forgotten
        rolled = self.remembered()
        server, where = self.bound(default)
        self.assertEqual((server.port, where["source"]), (default, "default"))
        self.assertEqual(where["notice"], f"back on the default port {default}: links made on {rolled} no longer work")
        self.assertIsNone(self.remembered())
        self.assertEqual(server.token, "t" * 43)

    def test_an_unused_engine_of_another_installation_is_retired_and_its_port_taken(self) -> None:
        _python, theirs, _door = self.other()
        ours = self.launch(CADGEN_ENGINE_PORT=str(theirs["port"]))
        self.assertEqual(ours["port"], theirs["port"])
        self.assertNotEqual(ours["pid"], theirs["pid"])
        # One token for every installation's engine: a link outlives an upgrade.
        self.assertEqual((ours["token"], theirs["token"]), (self.token(), self.token()))
        self.answering(self.ours())

    def test_an_engine_of_another_installation_in_use_is_refused_when_named_and_else_rolled_from(self) -> None:
        from cadgen.engine import client, previous

        python, theirs, door = self.other()
        held = client._connect(door)  # an app server's hold on it, as ``client.hold`` asks one
        self.addCleanup(held.close)
        message = {"op": "hold", "host": "codex"}  # held for Codex: the `cadgen` command in its shells runs it
        if os.name == "nt":
            message = {"token": door.read_text(encoding="utf-8").split()[1], **message}
        held.writer.write((json.dumps(message) + "\n").encode())
        held.writer.flush()
        self.assertEqual(json.loads(held.reader.readline()), {"ok": True})
        from cadgen.engine import daemon

        status = daemon._ask(door, {"op": "status"})
        self.assertEqual((status["holders"], status["hosts"]), (1, {"codex": 1}))
        self.assertEqual(os.path.realpath(status["prefix"]), os.path.realpath(str(Path(python).parent.parent)))
        done = self.started(CADGEN_ENGINE_PORT=str(theirs["port"]))
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertEqual(done.stderr.strip().splitlines()[-1],
                         f"CAD Viewer: port {theirs['port']} is held by cadgen {previous.version()}'s engine "
                         f"(pid {theirs['pid']}), still in use by 1 app server: quit that app or run `cadgen stop` "
                         "with that version")
        self.answering(door)
        server, where = self.bound(theirs["port"])  # its port the default: rolled from, never retired
        self.assertGreater(server.port, theirs["port"])
        self.assertEqual(where["notice"], f"{theirs['port']} is held by cadgen {previous.version()}'s engine (pid "
                                          f"{theirs['pid']}); this installation serves on {server.port} and keeps it")
        self.assertEqual(self.launch(python)["pid"], theirs["pid"], "theirs runs on")
        # It serves with the token it started with: a new one is refused while it runs.
        refused = self.cli("server", "--rotate-token")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("another installation's engine server is running", refused.stderr)

    def test_a_named_port_wins_and_rotate_token_replaces_the_token(self) -> None:
        named = free_port()
        first = self.launch(CADGEN_ENGINE_PORT=str(named))
        self.assertEqual(first["port"], named)
        self.assertNotIn("port", self.record(), "a named port is never remembered")
        self.assertEqual(first["token"], self.token())
        self.stop()
        # --port on ``cadgen server`` outranks CADGEN_ENGINE_PORT; --rotate-token retires every link.
        other = free_port()
        self.env["CADGEN_ENGINE_PORT"] = str(named)
        self.server("--port", str(other), "--rotate-token")
        rotated = self.launch()
        self.assertEqual(rotated["port"], other)
        self.assertNotEqual(rotated["token"], first["token"])
        self.assertEqual(self.token(), rotated["token"])
        # Refused while an engine runs, which keeps the token it started with.
        refused = self.cli("server", "--rotate-token")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("run cadgen stop first", refused.stderr)
        self.assertEqual(self.token(), rotated["token"])


class BuildBoundNoticeTest(unittest.TestCase):
    def test_a_roll_a_builds_page_made_is_said_by_the_next_build_once_and_by_no_status(self) -> None:
        """A build's page binds the port with no client's request (``models.register_page``): the
        next build's answer says the roll, once; a status asked meanwhile does not take it."""
        import io

        from cadgen.engine import daemon

        said = "3245 is held by another process; this installation serves on 3246 and keeps it"

        class Engine:
            refusal, token, unsaid = None, "", said

            def handle(self, request: dict, send) -> None:
                send({"log": "building"})
                send({"ok": True})

        engine = Engine()

        def asked(op: str) -> list[dict]:
            out = io.BytesIO()
            daemon._serve(engine, io.BytesIO((json.dumps({"op": op}) + "\n").encode()), out)
            return [json.loads(line) for line in out.getvalue().splitlines()]

        self.assertEqual(asked("status"), [{"log": "building"}, {"ok": True}])
        self.assertEqual(asked("build"), [{"log": "building"}, {"ok": True, "notice": said}])
        self.assertEqual(asked("build"), [{"log": "building"}, {"ok": True}])


if __name__ == "__main__":
    unittest.main()
