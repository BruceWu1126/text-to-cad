"""An installation's page links outlive its engine: one HTTP port (``--port``, else
``CADGEN_ENGINE_PORT``, else 3245, never another) and one page token, the same at every start until
``cadgen server --rotate-token``. A port another process holds is refused by name, but for another
installation's engine that nothing uses, which is retired."""

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
        (found,) = (self.root / "state" / "engines").iterdir()
        if os.name != "nt":
            self.assertEqual(found.stat().st_mode & 0o777, 0o600)
        return json.loads(found.read_text(encoding="utf-8"))

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
                               r"stop it, or start cadgen with CADGEN_ENGINE_PORT=<n>$")
        if os.name != "nt":  # the system names the holder: this process
            self.assertIn(f" (pid {os.getpid()}): ", said)
        self.answering()

    def test_an_unused_engine_of_another_installation_is_retired_and_its_port_taken(self) -> None:
        _python, theirs, _door = self.other()
        ours = self.launch(CADGEN_ENGINE_PORT=str(theirs["port"]))
        self.assertEqual(ours["port"], theirs["port"])
        self.assertNotEqual(ours["pid"], theirs["pid"])
        self.answering(self.ours())

    def test_an_engine_of_another_installation_in_use_is_refused_by_name(self) -> None:
        from cadgen.engine import client, previous

        python, theirs, door = self.other()
        held = client._connect(door)  # an app server's hold on it, as ``client.hold`` asks one
        self.addCleanup(held.close)
        message = {"op": "hold"}
        if os.name == "nt":
            message = {"token": door.read_text(encoding="utf-8").split()[1], **message}
        held.writer.write((json.dumps(message) + "\n").encode())
        held.writer.flush()
        self.assertEqual(json.loads(held.reader.readline()), {"ok": True})
        done = self.started(CADGEN_ENGINE_PORT=str(theirs["port"]))
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertEqual(done.stderr.strip().splitlines()[-1],
                         f"CAD Viewer: port {theirs['port']} is held by cadgen {previous.version()}'s engine "
                         f"(pid {theirs['pid']}), still in use by 1 app server: quit that app or run `cadgen stop` "
                         "with that version")
        self.answering(door)
        self.assertEqual(self.launch(python)["pid"], theirs["pid"], "theirs runs on")

    def test_a_named_port_wins_and_rotate_token_replaces_the_token(self) -> None:
        named = free_port()
        first = self.launch(CADGEN_ENGINE_PORT=str(named))
        self.assertEqual(first["port"], named)
        self.assertEqual(set(self.record()), {"schema", "token"}, "the record keeps the token alone")
        self.stop()
        # --port on ``cadgen server`` outranks CADGEN_ENGINE_PORT; --rotate-token retires every link.
        other = free_port()
        self.env["CADGEN_ENGINE_PORT"] = str(named)
        self.server("--port", str(other), "--rotate-token")
        rotated = self.launch()
        self.assertEqual(rotated["port"], other)
        self.assertNotEqual(rotated["token"], first["token"])
        self.assertEqual(self.record()["token"], rotated["token"])
        # Refused while an engine runs, which keeps the token it started with.
        refused = self.cli("server", "--rotate-token")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("run cadgen stop first", refused.stderr)
        self.assertEqual(self.record()["token"], rotated["token"])


if __name__ == "__main__":
    unittest.main()
