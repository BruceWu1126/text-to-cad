"""The launchers an installation publishes for the skills (``cadgen._internal.launchers``).

Each test publishes into a temporary folder from a fake environment; none touches ~/.local/bin.
The POSIX shape is symlinks, which Windows grants only with a privilege, so those tests run on
POSIX; the Windows shape (a copied launcher and a ``.cmd``) is plain files and runs everywhere.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from tests.python.support.paths import add_repo_path

add_repo_path("packages/cadgen/src")

from cadgen._internal import launchers  # noqa: E402
from cadgen._internal.launchers import DANGLING, MISSING, OK, OTHER  # noqa: E402

POSIX = unittest.skipIf(os.name == "nt", "symlinks need a privilege on Windows")


class LauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.bin = self.root / "bin"

    def environment(self, name: str, windows: bool = False) -> tuple[str, str]:
        """A fake environment's console script and python."""
        scripts = self.root / name / ("Scripts" if windows else "bin")
        scripts.mkdir(parents=True)
        console, python = scripts / ("cadgen.exe" if windows else "cadgen"), scripts / ("python.exe" if windows else "python")
        console.write_bytes(f"launcher of {name}".encode())
        python.write_text("", encoding="utf-8")
        return str(console), str(python)

    def states(self, found: tuple[str, str], version: str = "1.2.3", windows: bool = False) -> list[str]:
        return [status for _, status in launchers.inspect(version, str(self.bin), found, windows)]

    @POSIX
    def test_publishing_creates_both_launchers(self) -> None:
        found = self.environment("env")
        self.assertEqual([status for _, status, _ in launchers.publish("1.2.3", str(self.bin), found, False)], [MISSING, MISSING])
        self.assertEqual(os.readlink(self.bin / "cadgen-1.2.3"), found[0])
        self.assertEqual((self.bin / "cadgen-python-1.2.3").read_text(encoding="utf-8"), launchers.python_script(found[1], False))
        self.assertTrue(os.access(self.bin / "cadgen-python-1.2.3", os.X_OK))
        self.assertEqual(self.states(found), [OK, OK])

    @POSIX
    def test_a_dangling_or_anothers_launcher_is_repaired(self) -> None:
        gone, current = self.environment("gone"), self.environment("current")
        launchers.publish("1.2.3", str(self.bin), gone, False)
        for path in gone:
            os.unlink(path)
        self.assertEqual(self.states(current), [DANGLING, DANGLING])
        launchers.publish("1.2.3", str(self.bin), current, False)
        self.assertEqual(self.states(current), [OK, OK])
        other = self.environment("other")
        self.assertEqual(self.states(other), [OTHER, OTHER])
        launchers.publish("1.2.3", str(self.bin), other, False)
        self.assertEqual(os.readlink(self.bin / "cadgen-1.2.3"), other[0])

    @POSIX
    def test_another_versions_launchers_are_left_alone(self) -> None:
        launchers.publish("9.9.9", str(self.bin), self.environment("old"), False)
        before = {path.name: os.readlink(path) if path.is_symlink() else path.read_text(encoding="utf-8")
                  for path in self.bin.iterdir()}
        launchers.publish("1.2.3", str(self.bin), self.environment("new"), False)
        for name, content in before.items():
            path = self.bin / name
            self.assertEqual(os.readlink(path) if path.is_symlink() else path.read_text(encoding="utf-8"), content)

    def installed(self, where: Path):
        """This process as an installation named 1.2.3, publishing into ``where``."""
        found = self.environment("env", windows=launchers.WINDOWS)
        return found, mock.patch.dict("os.environ", {launchers.DIR_ENV: str(where), launchers.NAME_ENV: "1.2.3"}), \
            mock.patch.object(launchers, "targets", return_value=found)

    def test_only_the_cad_apps_server_and_doctor_publish(self) -> None:
        from cadgen import cli
        from cadgen.cli import doctor
        from cadgen.mcp import shim

        found, environment, targets = self.installed(self.bin)
        with environment, targets:
            # An ordinary command leaves a missing launcher missing.
            with mock.patch.object(cli, "_run", return_value=0), mock.patch.object(cli, "_tell"):
                self.assertEqual(cli._main(["status"]), 0)
            self.assertFalse(self.bin.exists())
            # The server's start publishes them; doctor repairs one gone dangling, and says so.
            shim.start()
            self.assertEqual(self.states(found, windows=launchers.WINDOWS), [OK, OK])
            console = self.bin / launchers.names("1.2.3")[0]
            console.unlink()
            lines = doctor._launchers()
            self.assertIn(f"{console} published (was missing)", lines)
            self.assertEqual(self.states(found, windows=launchers.WINDOWS), [OK, OK])

    def test_a_folder_it_cannot_write_is_skipped_silently(self) -> None:
        blocked = self.root / "a-file"
        blocked.write_text("", encoding="utf-8")
        results = launchers.publish("1.2.3", str(blocked / "bin"), self.environment("other"), False)
        self.assertTrue(all(error for _, _, error in results))
        from cadgen.cli import doctor
        from cadgen.mcp import shim

        _, environment, targets = self.installed(blocked / "bin")
        with environment, targets:
            shim.start()  # the server says nothing and raises nothing
            lines = doctor._launchers()  # doctor says why, and goes on
        self.assertTrue(all("cannot write it here" in line for line in lines), lines)
        self.assertFalse((blocked / "bin").exists())

    @POSIX
    def test_prune_removes_only_launchers_whose_environment_is_gone(self) -> None:
        gone = self.environment("gone")
        launchers.publish("1.0.0", str(self.bin), gone, False)
        launchers.publish("2.0.0", str(self.bin), self.environment("live"), False)
        for path in gone:
            os.unlink(path)
        os.symlink(gone[0], self.bin / "cadgen")  # a person's own link: not a launcher of cadgen's
        (self.bin / "cadgen-python-3.0.0").write_text("#!/bin/sh\nexec /nowhere\n", encoding="utf-8")
        self.assertEqual(sorted(Path(path).name for path in launchers.prune(str(self.bin))),
                         ["cadgen-1.0.0", "cadgen-python-1.0.0"])
        self.assertEqual(sorted(path.name for path in self.bin.iterdir()),
                         ["cadgen", "cadgen-2.0.0", "cadgen-python-2.0.0", "cadgen-python-3.0.0"])

    def test_the_windows_shape_is_a_copied_launcher_and_a_cmd(self) -> None:
        found = self.environment("env", windows=True)
        launchers.publish("1.2.3", str(self.bin), found, True)
        self.assertEqual((self.bin / "cadgen-1.2.3.exe").read_bytes(), Path(found[0]).read_bytes())
        self.assertIn(f'@"{found[1]}" %*\r\n', (self.bin / "cadgen-python-1.2.3.cmd").read_bytes().decode())
        self.assertEqual(self.states(found, windows=True), [OK, OK])
        os.unlink(found[1])
        self.assertEqual(sorted(Path(path).name for path in launchers.prune(str(self.bin), windows=True)),
                         ["cadgen-1.2.3.exe", "cadgen-python-1.2.3.cmd"])

    def test_a_source_tree_publishes_nothing_unless_named(self) -> None:
        # These tests import cadgen from the checkout: the release's version is not theirs to publish.
        with mock.patch.dict("os.environ", {launchers.NAME_ENV: ""}):
            self.assertIsNone(launchers.own_version())
        with mock.patch.dict("os.environ", {launchers.NAME_ENV: "1.2.3-dev.4"}):
            self.assertEqual(launchers.own_version(), "1.2.3-dev.4")


if __name__ == "__main__":
    unittest.main()
