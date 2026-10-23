"""cadgen 0.7's store goes when a 0.8 engine starts: its two folders only, links never followed."""
import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cadgen._internal import engine_host, legacy_store


def _write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class LegacyStoreSweepTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "cache" / "cadgen"
        # 0.7.26's layout: sharded objects, the index kinds, an interrupted write.
        _write(self.root / "objects" / "ab" / "cdef01", "0123456789")
        _write(self.root / "objects" / "ff" / ".cdef.tmp")
        _write(self.root / "index" / "model" / "k1", "{}")
        _write(self.root / "index" / "mesh" / "k2", "{}")
        _write(self.root / "index" / "op" / "k3", "{}")
        # What must survive: the state directory, the engine's, an unknown folder and file under the root.
        self.state = self.tmp / "state"
        self.settings = _write(self.state / "settings.json", '{"analytics": {"choice": "on"}}')
        self.channel = _write(self.state / "channel.json", '{"installation": "id"}')
        self.engine = self.tmp / "engine"
        self.unknown = _write(self.root / "notes" / "mine.txt")
        self.stray = _write(self.root / "objects.txt")
        environment = {"CADGEN_CACHE_DIR": str(self.root), "CADGEN_STATE_DIR": str(self.state),
                       "CADGEN_ENGINE_DIR": str(self.engine)}
        patch = mock.patch.dict(os.environ, environment)
        patch.start()
        self.addCleanup(patch.stop)
        quiet = mock.patch("sys.stderr", io.StringIO())  # the sweep's one line
        quiet.start()
        self.addCleanup(quiet.stop)

    def test_the_engine_start_removes_the_store_once_and_keeps_everything_else(self) -> None:
        import cadgen.engine.daemon as daemon

        self.assertIs(daemon.ON_START, engine_host.on_start)  # the daemon's start runs the sweep
        thread = engine_host.on_start()
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ["notes", "objects.txt"])
        self.assertEqual(sorted(legacy_store.LAST["removed"]), [f"index.removing-{os.getpid()}",
                                                               f"objects.removing-{os.getpid()}"])
        self.assertEqual((legacy_store.LAST["files"], legacy_store.LAST["unfinished"]), (5, []))
        self.assertTrue(self.settings.is_file() and self.channel.is_file() and self.unknown.is_file())
        self.assertTrue(self.stray.is_file())

        again = legacy_store.sweep()  # the next start: nothing to do
        self.assertEqual((again["removed"], again["unfinished"], again["files"]), ([], [], 0))

    def test_a_link_inside_the_store_is_unlinked_never_followed(self) -> None:
        outside = _write(self.tmp / "elsewhere" / "precious.step")
        try:
            os.symlink(outside.parent, self.root / "objects" / "ab" / "link", target_is_directory=True)
            os.symlink(outside, self.root / "index" / "model" / "file-link")
        except (OSError, NotImplementedError):
            self.skipTest("this filesystem makes no symbolic links")
        legacy_store.sweep()
        self.assertFalse((self.root / "objects").exists() or (self.root / "index").exists())
        self.assertEqual(outside.read_text(encoding="utf-8"), "x")

    def test_a_linked_or_unfamiliar_folder_is_left_alone(self) -> None:
        shutil.rmtree(self.root / "objects")
        mine = _write(self.tmp / "mine" / "ab" / "keep")
        try:
            os.symlink(mine.parent.parent, self.root / "objects", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("this filesystem makes no symbolic links")
        _write(self.root / "index" / "photos" / "holiday.jpg")  # not a 0.7 kind: not 0.7's index
        outcome = legacy_store.sweep()
        self.assertEqual((outcome["removed"], outcome["files"]), ([], 0))
        self.assertTrue(mine.is_file() and (self.root / "index" / "photos" / "holiday.jpg").is_file())

    def test_a_state_directory_inside_the_store_keeps_it(self) -> None:
        os.environ["CADGEN_STATE_DIR"] = str(self.root / "index" / "model")
        outcome = legacy_store.sweep()
        self.assertEqual(outcome["removed"], [f"objects.removing-{os.getpid()}"])
        self.assertTrue((self.root / "index" / "model" / "k1").is_file())

    def test_an_unfinished_sweep_is_finished_by_the_next_start(self) -> None:
        (self.root / "objects").rename(self.root / "objects.removing-1")  # a crashed engine's claim
        outcome = legacy_store.sweep()
        self.assertIn("objects.removing-1", outcome["removed"])
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ["notes", "objects.txt"])

    def test_no_root_costs_nothing(self) -> None:
        shutil.rmtree(self.root)
        self.assertEqual(legacy_store.sweep()["removed"], [])
        self.assertFalse(self.root.exists())


class LegacyRootTest(unittest.TestCase):
    def test_the_root_is_where_0_7_put_it(self) -> None:
        home = Path.home()
        with mock.patch.dict(os.environ, {"CADGEN_CACHE_DIR": "", "XDG_CACHE_HOME": "", "LOCALAPPDATA": ""}):
            self.assertEqual(legacy_store.root(), home / ".cache" / "cadgen")
        with mock.patch.dict(os.environ, {"CADGEN_CACHE_DIR": "relative/store"}):
            self.assertIsNone(legacy_store.root())
        if os.name != "nt":
            with mock.patch.dict(os.environ, {"CADGEN_CACHE_DIR": "", "XDG_CACHE_HOME": "/x/cache"}):
                self.assertEqual(legacy_store.root(), Path("/x/cache/cadgen"))


if __name__ == "__main__":
    unittest.main()
