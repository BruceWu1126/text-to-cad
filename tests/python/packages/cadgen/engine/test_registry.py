"""The registry: a record per document the engine built (or a page opened), in the user's state
folder, pointing and never caching; read again after a restart, so an id is known with the command
that builds it again and nothing is run; a moved script's record detached, listed, and removable; a
``memory`` document with none."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support import engine_daemon

SRC = Path(__file__).resolve().parents[5] / "packages" / "cadgen" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cadgen.engine import registry  # noqa: E402
from cadgen.engine.ids import model_id  # noqa: E402
from cadgen.engine.registry import Registry  # noqa: E402


def _record(ident: str, root: Path, **extra) -> dict:
    return {"id": ident, "kind": "model", "store": "pointer", "root": str(root), "home": "parts.py", "entry": "cube",
            "label": "cube", "run": ["python", "caller.py"], **extra}


class RegistryTest(unittest.TestCase):
    """The filesystem registry: records in, records out."""

    def setUp(self) -> None:
        self.state = Path(os.path.realpath(tempfile.mkdtemp(prefix="registry-")))
        self.addCleanup(shutil.rmtree, self.state, True)
        self.registry = Registry(self.state)
        self.ident = model_id(self.state / "parts.py", "cube")

    def test_a_record_is_written_once_until_it_changes_and_its_observers_hear_each_write(self) -> None:
        heard: list[dict] = []
        self.enterContext(mock.patch.object(registry, "OBSERVERS", [heard.append]))
        self.assertTrue(self.registry.put(_record(self.ident, self.state, revision="a")))
        written = self.state / "documents" / self.ident / "document.json"
        stamp = written.stat().st_mtime_ns
        self.assertFalse(self.registry.put(_record(self.ident, self.state, revision="a")), "unchanged: nothing written")
        self.assertEqual(written.stat().st_mtime_ns, stamp)
        self.assertTrue(self.registry.put(_record(self.ident, self.state, revision="b")))
        self.assertEqual([record["revision"] for record in heard], ["a", "b"])
        self.assertEqual([record["id"] for record in self.registry.list()], [self.ident])
        self.assertEqual(registry.command(self.registry, self.registry.get(self.ident)),
                         f"python {self.state / 'caller.py'}")
        self.assertTrue(self.registry.delete(self.ident))
        self.assertEqual((self.registry.get(self.ident), self.registry.list()), (None, []))

    def test_a_record_is_relocated_by_its_root_alone_and_a_bundle_s_is_its_own_folder(self) -> None:
        moved = self.state / "elsewhere"
        self.registry.put(_record(self.ident, moved))
        self.assertEqual(registry.home(self.registry, self.registry.get(self.ident)), moved / "parts.py")
        bundle = self.registry.write_bundle(self.ident, {"model.py": b"# a copy\n"})
        self.registry.put({**_record(self.ident, self.state), "root": ".", "home": "model.py", "store": "copy",
                           "run": ["python", "model.py"]})
        record = self.registry.get(self.ident)
        self.assertEqual(registry.home(self.registry, record), bundle / "model.py")
        self.assertEqual(self.registry.bundle_of(bundle / "model.py"), self.ident)
        self.assertIsNone(self.registry.bundle_of(self.state / "parts.py"))


PARTS = """
import build123d as bd
from cadgen import model


@model
def cube():
    return bd.Box(3, 2, 1)


@model(store="memory")
def scratch():
    return bd.Box(1, 1, 1)
"""

class RegistryDaemonTest(unittest.TestCase):
    """Through a real daemon in a private engine folder and state folder."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.folder = Path(os.path.realpath(tempfile.mkdtemp(prefix="registry-")))
        cls.engine = Path(tempfile.mkdtemp(prefix="eng-", dir="/tmp" if os.name != "nt" else None)).resolve()
        cls.state = cls.folder / "state"
        cls.addClassCleanup(shutil.rmtree, cls.folder, True)
        cls.addClassCleanup(shutil.rmtree, cls.engine, True)
        cls.env = engine_daemon.watched({**os.environ, "PYTHONPATH": str(SRC), "CADGEN_ENGINE_DIR": str(cls.engine),
                                         "CADGEN_STATE_DIR": str(cls.state), "CADGEN_ENGINE_IDLE": "120",
                                         "CADGEN_ENGINE_MESH": "inline"}, cls.addClassCleanup)
        cls.addClassCleanup(cls.cli, "stop")

    @classmethod
    def cli(cls, *args: str, cwd: Path | None = None):
        return engine_daemon.run([sys.executable, "-m", "cadgen.engine", *args], cwd=cwd or cls.folder, env=cls.env)

    def run_python(self, *args: str, cwd: Path, input: str | None = None):
        return engine_daemon.run([sys.executable, *args], cwd=cwd, env=self.env, input=input)

    def project(self, name: str, files: dict[str, str]) -> Path:
        folder = self.folder / name
        folder.mkdir()
        for file, text in files.items():
            (folder / file).write_text(textwrap.dedent(text), encoding="utf-8")
        return folder

    def ask(self, message: dict) -> dict:
        from cadgen.engine.client import request

        with mock.patch.dict(os.environ, {"CADGEN_ENGINE_DIR": str(self.engine)}):
            return request(message, start=False) or {}

    def records(self) -> dict[str, dict]:
        return {record["id"]: record for record in Registry(self.state).list()}

    def test_a_build_writes_its_record_alone_and_a_restart_knows_it_by_its_command(self) -> None:
        project = self.project("pointer", {"parts.py": PARTS,
                                           "caller.py": "import parts\n\nparts.cube()\nparts.scratch()\n"})
        before = sorted(os.listdir(project))
        done = self.run_python("caller.py", cwd=project)
        self.assertEqual(done.returncode, 0, done.stderr)
        cube, scratch = model_id(project / "parts.py", "cube"), model_id(project / "parts.py", "scratch")
        record = self.records()[cube]
        self.assertEqual((record["store"], record["home"], record["run"], Path(record["root"])),
                         ("pointer", "parts.py", ["python", "caller.py"], project))
        self.assertEqual(sorted(os.listdir(self.state / "documents" / cube)), ["document.json"], "it points: no geometry")
        self.assertNotIn(scratch, self.records(), "a memory document has no record")
        self.assertEqual(sorted(os.listdir(project)), before, "nothing beside the script")
        written = (self.state / "documents" / cube / "document.json").stat().st_mtime_ns
        again = self.run_python("caller.py", cwd=project)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual((self.state / "documents" / cube / "document.json").stat().st_mtime_ns, written,
                         "an unchanged build rewrites nothing")
        # A restart: the id is known, not held, with the command that builds it again; nothing runs it.
        self.assertEqual(self.cli("stop").returncode, 0)
        inspected = self.cli("inspect", cube)
        self.assertEqual(inspected.returncode, 2, inspected.stderr)
        self.assertIn(f"run `python {project / 'caller.py'}`", inspected.stderr)
        answer = self.ask({"op": "state", "id": cube})
        self.assertEqual((answer["state"], answer["rebuild"]), ("evicted", f"python {project / 'caller.py'}"))
        self.assertEqual(self.ask({"op": "state", "id": scratch})["state"], "evicted", "a memory id is unknown")
        from cadgen.viewer.recents import RecentStore

        listed = {entry.path: entry.public() for entry in RecentStore(self.state).list()}
        self.assertEqual((listed[cube]["name"], listed[cube]["missing"]), ("cube", False))
        self.assertNotIn(scratch, listed)
        # The script moved: detached, listed and saying so, until it is removed.
        (project / "parts.py").rename(project / "moved.py")
        self.assertEqual(self.ask({"op": "state", "id": cube})["state"], "detached")
        self.assertTrue({entry.path: entry.public() for entry in RecentStore(self.state).list()}[cube]["missing"])
        RecentStore(self.state).remove(cube)
        self.assertFalse((self.state / "documents" / cube).exists())


if __name__ == "__main__":
    unittest.main()
