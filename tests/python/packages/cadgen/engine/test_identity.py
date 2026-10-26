"""A run reattaches a moved script by its name and bytes, without rebuilding its document."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from cadgen.engine.identity import fingerprint, reattach
from cadgen.engine.ids import canonical, model_id
from cadgen.engine.registry import Registry
from tests.python.support import engine_daemon

SCRIPT = '''import build123d as bd
from cadgen import model
@model{options}
def part():
    return bd.Box(2, 3, 4)
if __name__ == "__main__":
    part()
'''


class ReattachmentTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(canonical(tempfile.mkdtemp(prefix="identity-")))
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.registry = Registry(self.folder / "state", self.folder / "cache")

    def test_two_missing_candidates_choose_the_latest_and_keep_the_other(self):
        source = self.folder / "new" / "part.py"
        source.parent.mkdir()
        source.write_text(SCRIPT.format(options=""))
        for at, built in enumerate((2, 7)):
            script = self.folder / f"gone{at}" / "part.py"
            ident = model_id(script, "part")
            self.registry.put({"id": ident, "kind": "model", "root": str(script.parent), "home": script.name,
                               "entry": "part", "name": "part", "fingerprint": fingerprint(source), "built": built,
                               "created": 1, "preview": {"thumbnail": "kept.png"}})
            self.registry.cache.write(f"documents/{ident}/document.xbf", f"cache{at}".encode())
        new = model_id(source, "part")
        lines = []
        chosen = reattach(self.registry, new, {"script": str(source), "name": "part",
                                              "fingerprint": fingerprint(source)}, log=lines.append)
        self.assertEqual(chosen, model_id(self.folder / "gone1" / "part.py", "part"))
        self.assertIn("most recently updated of 2 candidates", lines[0])
        self.assertEqual(len(self.registry.list()), 2)
        self.assertEqual(self.registry.cache.read(f"documents/{new}/document.xbf"), b"cache1")
        self.assertEqual(self.registry.get(new)["preview"], {"thumbnail": "kept.png"})
        self.assertEqual(self.registry.get(new)["created"], 1)


class ReattachmentDaemonTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(canonical(tempfile.mkdtemp(prefix="identity-")))
        self.addCleanup(shutil.rmtree, self.folder, True)
        engine = tempfile.mkdtemp(prefix="eng-", dir="/tmp" if os.name != "nt" else None)
        self.addCleanup(shutil.rmtree, engine, True)
        self.env = {**os.environ, "CADGEN_ENGINE_DIR": engine, "CADGEN_STATE_DIR": str(self.folder / "state"),
                    "CADGEN_CACHE_DIR": str(self.folder / "cache"), "CADGEN_LAUNCHER_DIR": str(self.folder / "bin"),
                    "CADGEN_ENGINE_PORT": "0", "CADGEN_ENGINE_STANDBY": "0", "DO_NOT_TRACK": "1"}
        self.addCleanup(self.stop)
        self.registry = Registry(self.env["CADGEN_STATE_DIR"], self.env["CADGEN_CACHE_DIR"])

    def stop(self):
        engine_daemon.run([sys.executable, "-m", "cadgen.engine", "stop"], env=self.env)

    def run_script(self, script, warm=False):
        done = engine_daemon.run([sys.executable, str(script)], env=self.env)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        if warm:
            self.assertIn("nothing ran", done.stdout, done.stdout + done.stderr)
        return done

    def cached(self, ident):
        return hashlib.sha256(self.registry.cache.read(f"documents/{ident}/document.xbf")).hexdigest()

    def test_moves_are_warm_copies_and_function_renames_are_new(self):
        project = self.folder / "project"
        project.mkdir()
        script = project / "part.py"
        script.write_text(SCRIPT.format(options=""))
        self.run_script(script)
        self.stop()
        first = model_id(script, "part")
        digest = self.cached(first)
        self.registry.update(first, lambda row: {**row, "preview": {"thumbnail": "kept.png"}, "opened": 3})
        created = self.registry.get(first)["created"]
        moved = self.folder / "moved"
        project.rename(moved)
        script = moved / "part.py"
        self.run_script(script, warm=True)
        second = model_id(script, "part")
        self.assertIsNone(self.registry.get(first))
        self.assertEqual(self.cached(second), digest)
        self.assertEqual(self.registry.get(second)["created"], created)
        self.assertEqual(self.registry.get(second)["preview"], {"thumbnail": "kept.png"})
        self.assertEqual(self.registry.get(second)["opened"], 3)
        # A second move, with the same daemon and the original cache's paths still in its bytes.
        script.rename(moved / "renamed.py")
        script = moved / "renamed.py"
        self.run_script(script, warm=True)
        third = model_id(script, "part")
        self.assertIsNone(self.registry.get(second))
        self.assertEqual(self.cached(third), digest)
        self.stop()
        before = self.registry.get(third)
        copy = self.folder / "copy"
        shutil.copytree(moved, copy)
        self.run_script(copy / "renamed.py")
        self.assertEqual(self.registry.get(third), before, "copy leaves the extant original untouched")
        copied = model_id(copy / "renamed.py", "part")
        self.assertIsNotNone(self.registry.get(copied))
        script.write_text(script.read_text().replace("part()", "different()"))
        self.run_script(script)
        self.assertIsNotNone(self.registry.get(model_id(script, "different")))
        self.assertEqual(self.registry.get(third), before, "a renamed function leaves its prior document")

    def test_an_explicit_name_survives_a_file_move_with_the_same_id(self):
        script = self.folder / "named.py"
        script.write_text(SCRIPT.format(options='(name="named")'))
        self.run_script(script)
        self.stop()
        ident = model_id(script, "part", "named")
        digest = self.cached(ident)
        script.rename(self.folder / "elsewhere.py")
        self.run_script(self.folder / "elsewhere.py", warm=True)
        self.assertEqual(self.cached(ident), digest)
        self.assertEqual(self.registry.get(ident)["home"], "elsewhere.py")


def setUpModule():
    engine_daemon.isolate(unittest.addModuleCleanup)


if __name__ == "__main__":
    unittest.main()
