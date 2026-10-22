"""The engine's rename waits out a Windows handle about to close and nothing else, as cadgen's
own does (``tests/python/packages/cadgen/test_atomic_replace.py`` holds that policy)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cadgen.engine import atomic_replace


def refusal(winerror: int) -> PermissionError:
    error = PermissionError(13, "refused")
    error.winerror = winerror
    return error


class ReplaceAtomicTest(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory(prefix="cadgen-engine-rename-")
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.source = self.folder / ".part.tmp.xbf"
        self.source.write_bytes(b"new")
        self.target = self.folder / "part.xbf"

    def test_a_sharing_violation_is_waited_out(self) -> None:
        real, attempts = os.replace, []

        def flaky(source, target):
            attempts.append(source)
            if len(attempts) < 3:
                raise refusal(atomic_replace.WINDOWS_SHARING_VIOLATION)
            real(source, target)

        with mock.patch.object(atomic_replace.os, "replace", side_effect=flaky), \
                mock.patch.object(atomic_replace.time, "sleep") as sleep:
            atomic_replace.replace_atomic(self.source, self.target)
        self.assertEqual(b"new", self.target.read_bytes())
        self.assertEqual([(0.05,), (0.1,)], [call.args for call in sleep.call_args_list])

    def test_a_held_destination_is_waited_out_and_a_denial_with_no_file_there_is_not(self) -> None:
        real, attempts = os.replace, []

        def held_once(source, target):
            attempts.append(source)
            if len(attempts) < 2:
                raise refusal(atomic_replace.WINDOWS_ACCESS_DENIED)
            real(source, target)

        self.target.write_bytes(b"old")
        with mock.patch.object(atomic_replace.os, "replace", side_effect=held_once), \
                mock.patch.object(atomic_replace.time, "sleep"):
            atomic_replace.replace_atomic(self.source, self.target)
        self.assertEqual(b"new", self.target.read_bytes())

        error = refusal(atomic_replace.WINDOWS_ACCESS_DENIED)
        with mock.patch.object(atomic_replace.os, "replace", side_effect=error), \
                mock.patch.object(atomic_replace.time, "sleep") as sleep:
            with self.assertRaises(PermissionError):
                atomic_replace.replace_atomic(self.folder / "absent.tmp", self.folder / "absent")
        sleep.assert_not_called()

    def test_an_exhausted_ladder_retries_from_a_fresh_copy_then_gives_up(self) -> None:
        real, seen = os.replace, []

        def blocked_until_a_new_file(source, target):
            seen.append(Path(source).name)
            if Path(source) == self.source:
                raise refusal(atomic_replace.WINDOWS_SHARING_VIOLATION)
            real(source, target)

        with mock.patch.object(atomic_replace.os, "replace", side_effect=blocked_until_a_new_file), \
                mock.patch.object(atomic_replace.time, "sleep"):
            atomic_replace.replace_atomic(self.source, self.target)
        self.assertEqual(b"new", self.target.read_bytes())
        self.assertNotEqual(self.source.name, seen[-1])
        self.assertEqual([self.target.name], [entry.name for entry in self.folder.iterdir()])

        self.source.write_bytes(b"newer")
        error = refusal(atomic_replace.WINDOWS_SHARING_VIOLATION)
        with mock.patch.object(atomic_replace.os, "replace", side_effect=error) as replace, \
                mock.patch.object(atomic_replace.time, "sleep"):
            with self.assertRaises(PermissionError) as raised:
                atomic_replace.replace_atomic(self.source, self.target)
        self.assertIs(error, raised.exception)
        self.assertEqual(2 * (len(atomic_replace.RETRY_DELAYS_SECONDS) + 1), replace.call_count)


if __name__ == "__main__":
    unittest.main()
