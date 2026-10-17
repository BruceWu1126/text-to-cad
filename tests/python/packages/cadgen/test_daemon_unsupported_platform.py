"""Where no daemon can run, cadgen says so: a request fails naming why, and starts nothing.

This guard was written for Windows, which had no transport at all: the daemon spoke
AF_UNIX and CPython does not provide it there. That is no longer the situation --
multiprocessing.connection carries AF_PIPE on Windows and AF_UNIX everywhere else, so
every platform this project supports can now run a daemon.

The guard stays anyway, because "can this platform carry a daemon" is still a question with
a real answer, and the daemon is where every build runs: on a platform without a family the
command must fail with a reason a person can act on, never with an exception from deep in
the transport, and never by building somewhere slower.

The platform is simulated rather than skipped off, so this holds on machines where it
cannot be reproduced.
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr
from unittest import mock

from tests.python.support.paths import add_repo_path

add_repo_path("packages", "cadgen", "src")

from cadgen.daemon import client, transport  # noqa: E402


def no_transport():
    """Stand in for a platform offering neither AF_UNIX nor AF_PIPE."""
    return mock.patch.object(transport, "supported", return_value=False)


class NoTransport(unittest.TestCase):
    def test_the_reason_names_the_missing_family(self):
        with no_transport():
            reason = client.unavailable()
        self.assertIn("AF_UNIX", reason)
        self.assertIn("AF_PIPE", reason)

    def test_a_command_fails_naming_it_and_starts_no_daemon(self):
        err = io.StringIO()
        with no_transport(), redirect_stderr(err), \
                mock.patch.dict("os.environ", {"CADGEN_DAEMON": "1"}), \
                mock.patch.object(client, "_connect", side_effect=OSError("no daemon listens there")), \
                mock.patch.object(client, "_spawn_daemon", side_effect=AssertionError("a daemon was started")):
            import os

            os.environ.pop("CADGEN_DAEMON_CHILD", None)
            code = client.run_via_daemon("step-build", ["in.step", "out.step"], "/work", prog="cadgen step build")
        self.assertEqual(code, 1)
        self.assertIn("no local IPC family", err.getvalue())

    def test_every_platform_we_ship_on_has_a_family(self):
        # AF_UNIX on POSIX, AF_PIPE on Windows. If this ever fails, the daemon has lost
        # its transport on the machine running the suite.
        self.assertTrue(transport.supported())
        self.assertIsNone(client.unavailable())


if __name__ == "__main__":
    unittest.main()
