"""Document ids: one id per document, derived from the canonical path (and function), the same in
every process, and the one key the engine's server and the viewer agree on."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path

SRC = Path(__file__).resolve().parents[5] / "packages" / "cadgen" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cadgen.engine.addresses import document_id  # noqa: E402
from cadgen.engine.ids import canonical, file_id, is_id, model_id  # noqa: E402

SCRIPT = """
from cadgen.engine import model

@model
def part():
    return None
"""


def _case_insensitive(folder: Path) -> bool:
    probe = folder / "Probe.txt"
    probe.write_text("x", encoding="utf-8")
    try:
        return (folder / "probe.TXT").exists()
    finally:
        probe.unlink()


class IdsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="engine-ids-"))  # as given: not canonical on macOS
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.script = self.folder / "parts.py"
        self.script.write_text(textwrap.dedent(SCRIPT), encoding="utf-8")

    def link(self, name: str, target: Path) -> Path:
        link = self.folder / name
        try:
            link.symlink_to(target)
        except OSError as error:  # Windows without the symlink privilege
            self.skipTest(f"no symlinks here: {error}")
        return link

    def test_a_model_s_id_is_the_same_in_another_process_and_asks_no_engine(self) -> None:
        probe = ("import json, sys; sys.path.insert(0, sys.argv[1]); import parts; "
                 "print(json.dumps(parts.part.id))")
        env = {**os.environ, "PYTHONPATH": str(SRC), "CADGEN_ENGINE_DIR": str(self.folder / "no-engine")}
        done = subprocess.run([sys.executable, "-c", probe, str(self.folder)], capture_output=True, text=True,
                              env=env, cwd=self.folder, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout.strip().splitlines()[-1]), model_id(self.script, "part"))
        self.assertFalse((self.folder / "no-engine").exists(), "no engine was asked or started")

    def test_two_scripts_with_one_function_name_are_two_models(self) -> None:
        other = self.folder / "other.py"
        other.write_text(textwrap.dedent(SCRIPT), encoding="utf-8")
        self.assertNotEqual(model_id(self.script, "part"), model_id(other, "part"))
        self.assertNotEqual(model_id(self.script, "part"), model_id(self.script, "other"))
        self.assertNotEqual(model_id(self.script, "part"), file_id(self.script), "a model is not its script's file")

    def test_a_symlinked_or_differently_spelled_script_is_one_model(self) -> None:
        spellings = [self.link("alias.py", self.script), self.folder / "sub" / ".." / "parts.py"]
        if _case_insensitive(self.folder):
            spellings.append(self.folder / "PARTS.py")
        for spelling in spellings:
            with self.subTest(spelling=str(spelling)):
                self.assertEqual(model_id(spelling, "part"), model_id(self.script, "part"))
                # A script's name never names its model: `parts.py::part` is a file name like any other.
                self.assertNotEqual(document_id(f"{spelling}::part"), model_id(self.script, "part"))

    def test_a_symlinked_or_case_variant_file_is_one_file(self) -> None:
        target = self.folder / "Part.xbf"
        target.write_bytes(b"x")
        spellings = [self.link("alias.xbf", target)]
        if _case_insensitive(self.folder):
            spellings.append(self.folder / "PART.XBF")
        for spelling in spellings:
            with self.subTest(spelling=str(spelling)):
                self.assertEqual(canonical(spelling), canonical(target))
                self.assertEqual(file_id(spelling), file_id(target))
        self.assertEqual(Path(canonical(target)).name, "Part.xbf", "the name as the file system spells it")
        # A file not written yet keeps its name below the folder that exists, and keeps its id once written.
        later = self.folder / "later.xbf"
        before = file_id(later)
        later.write_bytes(b"x")
        self.assertEqual(file_id(later), before)

    def test_derived_ids_are_version_5_uuids(self) -> None:
        for ident in (model_id(self.script, "part"), file_id(self.script)):
            self.assertTrue(is_id(ident))
            self.assertEqual(uuid.UUID(ident).version, 5)
            self.assertEqual(document_id(ident), ident, "an id names itself")
        self.assertFalse(is_id(str(uuid.uuid4()).upper()))
        self.assertFalse(is_id(str(self.script)))
        self.assertTrue(is_id(str(uuid.uuid4())), "the version is never a rule")


class ServedByIdTest(unittest.TestCase):
    def setUp(self) -> None:
        from cadgen.engine.serve import Server

        self.folder = Path(tempfile.mkdtemp(prefix="engine-ids-"))
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.server = Server(port=0, mesh_mode="inline")
        self.addCleanup(self.server.stop)

    def test_the_page_s_document_route_serves_by_id(self) -> None:
        path = self.folder / "part.xbf"
        ident = self.server.put(path, {"version": "one", "prototypes": [], "name": "part"}, {})
        self.assertEqual(ident, file_id(path))
        self.server.start()
        with urllib.request.urlopen(f"{self.server.url}/api/doc/{ident}", timeout=30) as response:  # noqa: S310
            self.assertEqual(json.loads(response.read())["version"], "one")
        self.assertEqual(self.server.listing(), [{"id": ident, "path": canonical(path)}])
        with self.assertRaises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(f"{self.server.url}/api/doc/{file_id(self.folder / 'other.xbf')}", timeout=30)  # noqa: S310
        self.assertEqual(refused.exception.code, 404)

    def test_a_symlinked_file_opened_two_ways_is_one_served_document(self) -> None:
        path = self.folder / "part.xbf"
        link = self.folder / "alias.xbf"
        try:
            link.symlink_to(path)
        except OSError as error:
            self.skipTest(f"no symlinks here: {error}")
        ident = self.server.put(path, {"version": "one", "prototypes": []}, {})
        self.assertEqual(self.server.put(link, {"version": "two", "prototypes": []}, {}), ident)
        self.assertEqual([row["id"] for row in self.server.listing()], [ident])
        held = self.server.hold([str(link)])
        self.assertEqual(held, [ident], "a build writing the link holds the one document")
        self.server.release(held)


if __name__ == "__main__":
    unittest.main()
