"""``--no-daemon``: a build in the command's own process, where no daemon can run. It writes what
a daemon build writes, finishes its files before it answers, and asks no daemon anything; without
it, a daemon that cannot take the request is the failure it always was."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support.paths import REPO_ROOT, add_repo_path
from tests.python.support.tmp_root import generated_cad_directory

add_repo_path("packages/cadgen/src")

SOURCE = '''from pathlib import Path
from cadgen import read_step, step, stl
from cadgen import build123d as bd

HERE = Path(__file__).resolve().parent


@step(out="pin.step", materials={"definitions": {"paint": {"baseColor": "#123456", "roughness": 0.2}},
                                 "assignments": [{"targets": ["#pin"], "material": "paint"}]})
def pin():
    shape = bd.Cylinder(1, 6)
    shape.label = "pin"
    return shape


@stl(out="plate.stl")
@step(out="plate.step")
def plate():
    (HERE / "body-ran").write_text("ran")
    body = bd.Box(12, 8, 2) - bd.Cylinder(1.2, 5)
    body.label = "body"
    bought = read_step(HERE / "bought.step")
    bought.label = "bought"
    return bd.Compound([body, bd.Pos(4, 0, 3) * pin(), bd.Pos(-4, 0, 2) * bought])


if __name__ == "__main__":
    plate()
'''
OUTPUTS = ("pin.step", "pin.step.json", "plate.step", "plate.stl")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class NoDaemon(unittest.TestCase):
    def setUp(self):
        temporary = generated_cad_directory(prefix="no-daemon-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / "plate.py").write_text(SOURCE, encoding="utf-8")
        from build123d import Box, export_step

        export_step(Box(3, 3, 3), str(self.root / "bought.step"))  # a file no build of these stores wrote
        self.env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "packages/cadgen/src")}
        self.env.pop("CADGEN_DAEMON_CHILD", None)
        # Where no daemon can run: its state directory is a file.
        (self.root / "no-sockets").write_text("", encoding="utf-8")
        self.nowhere = {**self.env, "CADGEN_DAEMON_STATE_DIR": str(self.root / "no-sockets")}
        self.nowhere.pop("CADGEN_DAEMON_SOCKET", None)

    def run_in(self, env, store, *argv):
        env = {**env, "CADGEN_CACHE_DIR": str(self.root / store)}
        return subprocess.run([sys.executable, *argv], cwd=self.root, env=env, capture_output=True, text=True,
                              timeout=300)

    def stored(self, store):
        """Every record (its build's duration aside), the tree and components each names, and the files."""
        found = {}
        for entry in sorted((self.root / store / "index" / "model").iterdir()):
            record = json.loads(entry.read_text(encoding="utf-8"))
            record.pop("modelSeconds", None)
            tree = record["tree"]
            body = (self.root / store / "objects" / tree[:2] / tree[2:]).read_bytes()
            found[record["model"]] = (record, hashlib.sha256(body).hexdigest(), json.loads(body)["components"])
        return found, {name: digest(self.root / name) for name in OUTPUTS}

    def test_a_build_without_the_daemon_writes_what_a_daemon_build_writes(self):
        built = self.run_in(self.env, "daemon-store", "plate.py", "--json")
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        with mock.patch.dict(os.environ, {"CADGEN_CACHE_DIR": str(self.root / "daemon-store")}):
            from cadgen._internal.doors import await_writes

            for path in json.loads(built.stdout.splitlines()[-1])["files"]:
                await_writes(path)
        expected = self.stored("daemon-store")
        for name in OUTPUTS:
            (self.root / name).unlink()

        alone = self.run_in(self.nowhere, "own-store", "plate.py", "--json", "--no-daemon")
        self.assertEqual(alone.returncode, 0, alone.stdout + alone.stderr)
        answer = json.loads(alone.stdout.splitlines()[-1])
        self.assertEqual(set(answer["files"].values()), {"saved"}, "its files are written before it answers")
        self.assertIn("built plate.step in", alone.stderr)
        self.assertIn(": daemonless, model code", alone.stderr)
        self.assertEqual(self.stored("own-store"), expected)

    def test_export_without_the_daemon_finishes_a_pending_file_from_its_record(self):
        built = self.run_in(self.nowhere, "store", "plate.py", "--no-daemon")
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        with mock.patch.dict(os.environ, {"CADGEN_CACHE_DIR": str(self.root / "store")}):
            from cadgen.store.records import read_record

            outputs = read_record(f"{self.root / 'plate.py'}::plate")["outputs"]
        for name in ("plate.step", "plate.stl", "body-ran"):
            (self.root / name).unlink()

        done = self.run_in(self.nowhere, "store", "-m", "cadgen.cli", "export", "plate.stl", "--no-daemon")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("ready plate.stl", done.stdout)
        for name in ("plate.step", "plate.stl"):  # the mesh's STEP first
            self.assertEqual(digest(self.root / name), outputs[str(self.root / name)]["sha256"])
        self.assertFalse((self.root / "body-ran").exists(), "an export never runs the model")

    def test_without_it_a_daemon_that_cannot_run_fails_the_build(self):
        refused = self.run_in(self.nowhere, "store", "plate.py")
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn("cadgen-daemon: The build daemon keeps its key, locks and log in", refused.stderr)
        self.assertFalse((self.root / "body-ran").exists())
        self.assertFalse((self.root / "plate.step").exists())
        self.assertFalse((self.root / "store" / "index" / "model").exists())

    def test_the_viewer_and_the_cad_app_refuse_it(self):
        from cadgen import cli

        for command, what in (("viewer", "the CAD Viewer"), ("mcp", "the CAD app")):
            with self.subTest(command=command):
                said = io.StringIO()
                with contextlib.redirect_stderr(said), self.assertRaises(SystemExit) as ended:
                    cli.main([command, "--no-daemon"])
                self.assertEqual(ended.exception.code, 2)
                self.assertEqual(said.getvalue(),
                                 f"cadgen {command}: {what} needs the build daemon, so it cannot run with --no-daemon.\n")


if __name__ == "__main__":
    unittest.main()
