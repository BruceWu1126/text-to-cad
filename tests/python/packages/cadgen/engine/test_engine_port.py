"""An installation's page links outlive its engine: one HTTP port (3245, else the one it took and
remembers) and one page token, the same at every start until ``cadgen server --rotate-token``."""

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
                                          "CADGEN_CACHE_DIR": str(self.root / "cache"), "CADGEN_ENGINE_IDLE": "120"},
                                         self.addCleanup)
        self.addCleanup(self.stop)

    def cli(self, *args: str, **extra: str) -> subprocess.CompletedProcess:
        return engine_daemon.run([sys.executable, "-m", "cadgen.engine", *args], env={**self.env, **extra})

    def stop(self) -> None:
        done = self.cli("stop")
        self.assertEqual(done.returncode, 0, done.stderr)

    def launch(self, **extra: str) -> dict:
        """The engine's page, started (or reused) by the viewer's launcher: its port and token."""
        done = engine_daemon.run([sys.executable, "-m", "cadgen.viewer", "--api-only", "--json"],
                                 env={**self.env, **extra}, cwd=self.root)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

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

    def test_a_held_default_moves_the_engine_once_and_its_port_and_token_stay(self) -> None:
        holder = socket.socket()
        self.addCleanup(holder.close)
        if not listening(3245):
            holder.bind(("127.0.0.1", 3245))  # a plain listener: another program's
            holder.listen(1)
        first = self.launch()
        self.assertGreater(first["port"], 3245)
        self.assertEqual(self.record()["port"], first["port"])
        self.assertIn(f":{first['port']} (not the default 3245", self.cli("status").stdout)
        self.stop()
        holder.close()  # 3245 free again (unless another engine's): the remembered port is kept
        again = self.launch()
        self.assertEqual((again["port"], again["token"]), (first["port"], first["token"]))
        self.assertNotEqual(again["pid"], first["pid"])

    def test_a_named_port_wins_and_rotate_token_replaces_the_token(self) -> None:
        named = free_port()
        first = self.launch(CADGEN_ENGINE_PORT=str(named))
        self.assertEqual(first["port"], named)
        self.assertNotIn("port", self.record(), "a named port is not remembered")
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
