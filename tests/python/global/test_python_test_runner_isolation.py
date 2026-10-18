from __future__ import annotations

import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from tests.python.support.paths import REPO_ROOT, add_repo_path
from tests.python.support.daemon_cleanup import retire_owned_daemon

add_repo_path("packages/cadgen/src")

spec = importlib.util.spec_from_file_location("isolated_unittest_runner", REPO_ROOT / "scripts/test/unittest_files.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def _stand_in(calls: list, *, child_error: Exception | None = None):
    """subprocess.run for the runner: the daemon's start binds (writes its key), a file's
    interpreter runs (or raises ``child_error``), the retirement is recorded with which of
    the stores the files ran on still exist then."""
    def run(argv, *, env, **kwargs):
        stores = [Path(file_env["CADGEN_CACHE_DIR"]).is_dir() for file_argv, file_env, _ in calls
                  if file_argv[1] != "-c" and not file_argv[1].endswith("daemon_cleanup.py")]
        calls.append((list(argv), dict(env), stores))
        if argv[1] == "-c":
            Path(env["CADGEN_DAEMON_STATE_DIR"], "d.sock.key").write_text("test key", encoding="utf-8")
        elif not argv[1].endswith("daemon_cleanup.py") and child_error is not None:
            raise child_error
        return subprocess.CompletedProcess(argv, 0, "", "")

    return run


class PythonTestRunnerIsolation(unittest.TestCase):
    def test_files_own_stores_on_one_private_daemon_retired_after_the_last(self):
        calls = []
        ambient = {"CADGEN_DAEMON_STATE_DIR": "developer-state", "CADGEN_DAEMON_SOCKET": "developer-socket"}
        with mock.patch.dict(os.environ, ambient), \
                mock.patch.object(runner.subprocess, "run", side_effect=_stand_in(calls)), \
                mock.patch.object(runner.sys, "stderr", io.StringIO()):
            runner.run_files(["first.py", "second.py"], str(REPO_ROOT), 2, False)
            self.assertEqual(os.environ["CADGEN_DAEMON_SOCKET"], "developer-socket")
            self.assertEqual(os.environ["CADGEN_DAEMON_STATE_DIR"], "developer-state")
        start, *files, retire = calls
        # Started before the first file, in the runner's environment; retired after the last.
        self.assertEqual(start[0][1:3], ["-c", "from cadgen.daemon import client; client.prewarm()"])
        self.assertTrue(retire[0][1].endswith("daemon_cleanup.py"))
        endpoint = {key: start[1][key] for key in ("CADGEN_DAEMON_STATE_DIR", "CADGEN_DAEMON_SOCKET")}
        self.assertNotEqual(endpoint["CADGEN_DAEMON_SOCKET"], "developer-socket")
        self.assertEqual(retire[0][2], endpoint["CADGEN_DAEMON_SOCKET"])
        self.assertEqual(len(files), 2)
        for _argv, env, _stores in files:
            self.assertEqual({key: env[key] for key in endpoint}, endpoint)
        stores = [env["CADGEN_CACHE_DIR"] for _argv, env, _stores in files]
        self.assertNotEqual(stores[0], stores[1])
        # The daemon answers a build's verify into its store: the stores outlive it.
        self.assertEqual(retire[2], [True, True])
        for path in (*stores, endpoint["CADGEN_DAEMON_STATE_DIR"]):
            self.assertFalse(Path(path).exists())

    def test_even_one_file_at_one_job_runs_on_the_runs_daemon(self):
        # A direct run of one file used to run in the runner's own interpreter, on the
        # developer's daemon: every file now runs in an interpreter of its own, on the run's.
        ran = ("only.py", 0, "Ran 0 tests in 0.000s\n\nOK\n", 0.0)
        endpoint = {"CADGEN_DAEMON_SOCKET": "run-socket", "CADGEN_DAEMON_STATE_DIR": "run-state"}
        with mock.patch.object(runner, "run_daemon", return_value=contextlib.nullcontext(endpoint)), \
                mock.patch.object(runner, "_run_one_file", return_value=ran) as one, \
                mock.patch.object(runner.sys, "stderr", io.StringIO()):
            self.assertEqual(runner.main(["--top", str(REPO_ROOT), "only.py"]), 0)
        (path, top, verbose, env), _kwargs = one.call_args
        self.assertEqual((path, top, verbose), ("only.py", os.path.realpath(REPO_ROOT), False))
        self.assertEqual({key: env[key] for key in endpoint}, endpoint)

    def test_a_failed_child_still_retires_the_runs_daemon(self):
        calls = []
        stand_in = _stand_in(calls, child_error=OSError("test child could not run"))
        with mock.patch.object(runner.subprocess, "run", side_effect=stand_in), \
                mock.patch.object(runner.sys, "stderr", io.StringIO()):
            with self.assertRaisesRegex(OSError, "test child could not run"):
                runner.run_files(["failed.py"], str(REPO_ROOT), 1, False)
        start, child, retire = calls
        self.assertTrue(retire[0][1].endswith("daemon_cleanup.py"))
        self.assertEqual(retire[0][2], start[1]["CADGEN_DAEMON_SOCKET"])
        self.assertFalse(Path(start[1]["CADGEN_DAEMON_STATE_DIR"]).exists())
        self.assertFalse(Path(child[1]["CADGEN_CACHE_DIR"]).exists())

    def test_cleanup_uses_authenticated_retirement_without_spawning_or_retrying(self):
        from cadgen.daemon import client, transport

        channel = mock.Mock()
        lock = mock.Mock()
        lock.acquire.side_effect = [False, False, True]  # still draining twice, then gone
        with mock.patch.dict(os.environ, {"CADGEN_DAEMON_SOCKET": "owned"}), \
                mock.patch.object(client, "_connect", return_value=channel) as connect, \
                mock.patch.object(client, "_send_json", return_value=True) as send, \
                mock.patch.object(client, "_recv_json", return_value={"restart": True}), \
                mock.patch.object(transport, "daemon_lock", return_value=lock) as daemon_lock, \
                mock.patch.object(client, "_connect_or_spawn", side_effect=AssertionError("must never spawn")):
            retire_owned_daemon("owned")
            connect.assert_called_once_with("owned")
            send.assert_called_once_with(channel, {"token": "test-owned-daemon-retirement"})
            channel.close.assert_called_once()
            # Done when the daemon lets go of its address, not at its reply: one still
            # draining wakes its own accept through the socket file the caller removes next,
            # and with that file gone it served nothing, forever.
            daemon_lock.assert_called_once_with("owned")
            self.assertEqual(lock.acquire.call_count, 3)
            lock.release.assert_called_once()
            with self.assertRaisesRegex(ValueError, "explicit test-owned address"):
                retire_owned_daemon("developer")
            self.assertEqual(connect.call_count, 1)


class HangGuard(unittest.TestCase):
    def test_a_hung_file_fails_by_name_with_its_stack(self):
        # A hung file once held a CI job silent for 42 minutes, until it was cancelled.
        with tempfile.TemporaryDirectory() as scratch:
            top = os.path.realpath(scratch)
            hung = Path(top, "test_hung_fixture.py")
            hung.write_text(
                "import threading, unittest\n\n\n"
                "class Hung(unittest.TestCase):\n"
                "    def test_waits_forever(self):\n"
                "        threading.Event().wait()\n",
                encoding="utf-8",
            )
            report = io.StringIO()
            with contextlib.chdir(top), mock.patch.object(runner, "FILE_HANG_SECONDS", 1), \
                    mock.patch.object(runner, "run_daemon", return_value=contextlib.nullcontext({})), \
                    mock.patch.object(runner.sys, "stderr", report):
                code = runner.run_files([str(hung)], top, 1, False)
        self.assertEqual(code, 1)
        self.assertIn("in test_waits_forever", report.getvalue())
        self.assertIn(f"failing modules:\n  {hung}", report.getvalue())

    def test_a_windows_childs_summary_still_parses(self):
        # Read from a file, a Windows child's \r\n reaches the parser untranslated:
        # once, every module of a passing Windows run counted as one with no summary.
        with tempfile.TemporaryFile() as stream:
            stream.write(b"..\r\n" + b"-" * 70 + b"\r\nRan 2 tests in 0.010s\r\n\r\nOK (skipped=1)\r\n")
            output = runner._read_all(stream)
        self.assertTrue(runner._RAN.search(output))
        self.assertEqual(runner._counts(output)["tests"], 2)
        self.assertEqual(runner._counts(output)["skipped"], 1)


if __name__ == "__main__":
    unittest.main()
