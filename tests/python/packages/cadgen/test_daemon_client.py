"""What the client says when the warm worker running its job dies.

The defect this pins: a 2,700-occurrence validate ran 35 minutes on a warm worker,
the worker was killed (out of memory, most likely), and the caller saw one line --
``cadgen-daemon: worker closed the connection`` -- and exit 1. Nothing said the
worker had died, nothing named the job, nothing said where to read what the worker
printed as it died. The fix is the MESSAGE, deliberately not a silent retry: a
half-hour job re-running unannounced is worse than the failure it hides.

Driven through ``_run_request`` with a scripted channel, so the wording is pinned at
the one place it is composed and no daemon has to be started.
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from cadgen.daemon import client, transport  # noqa: E402
from cadgen.daemon import pool as pool_mod  # noqa: E402
from cadgen.daemon import server  # noqa: E402
from cadgen.daemon.jobs import JobLedger


class _ScriptedChannel:
    """A transport channel that answers a request with a fixed sequence of frames."""

    def __init__(self, frames: list[dict]) -> None:
        self.sent: list[dict] = []
        self._frames = [json.dumps(frame).encode("utf-8") for frame in frames]

    def send(self, raw: bytes) -> None:
        self.sent.append(json.loads(raw.decode("utf-8")))

    def recv(self, timeout):
        if not self._frames:
            return b""  # closed
        return self._frames.pop(0)

    def close(self) -> None:
        pass


PAYLOAD = {
    "tool": "step-compile",
    "prog": "cadgen step compile",
    "argv": ["tmp/noexh/noexh.step", "--force"],
    "cwd": "/work",
    "env": {},
    "token": "t",
}


class DeadWorkerMessage(unittest.TestCase):
    def _run(self, frames: list[dict]) -> tuple[object, str, str]:
        channel = _ScriptedChannel(frames)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            outcome = client._run_request(channel, PAYLOAD)
        return outcome, out.getvalue(), err.getvalue()

    def test_the_death_is_explained_and_the_exit_code_kept(self):
        outcome, out, err = self._run([
            {"stream": "stdout", "data": ""},
            {"workerDied": {"pid": 4242, "detail": "worker 4242 was killed by SIGKILL (signal 9)",
                            "exitStatus": -9}},
            {"exit": 1},
        ])
        self.assertEqual(outcome, 1)
        self.assertEqual(out, "")
        # Every clause the caller needed and did not have.
        self.assertIn("the warm worker running", err)
        self.assertIn("died mid-job", err)
        self.assertIn("killed by SIGKILL (signal 9)", err)
        self.assertIn("out of memory", err)
        self.assertIn("`cadgen step compile tmp/noexh/noexh.step --force`", err)
        self.assertIn("NOT retried", err)
        # Where the worker's own last words are: the daemon's log, which its stderr goes to.
        self.assertIn(f"build daemon's log: {client.log_path(client.daemon_address())}", err)
        self.assertNotIn("worker closed the connection", err)

    def test_a_death_the_daemon_never_follows_up_fails_the_job_and_is_told_once(self):
        """No exit frame after the death: the daemon itself is gone too. The job fails;
        the death is the one thing said, never a second notice that the service was lost."""
        outcome, out, err = self._run([
            {"workerDied": {"pid": 4242, "detail": "worker 4242 was killed by SIGKILL (signal 9)",
                            "exitStatus": -9}},
        ])  # the connection closes without an exit frame

        self.assertEqual(outcome, 1)
        self.assertEqual(out, "")
        self.assertEqual(err.count("died mid-job"), 1, err)
        self.assertIn("killed by SIGKILL (signal 9)", err)
        self.assertIn("NOT retried", err)
        self.assertNotIn("lost the build service", err)

    def test_a_daemon_lost_mid_request_fails_the_job_and_says_so(self):
        """The daemon itself killed mid-build: the job fails, named, and is never run again
        somewhere else -- a rerun from the start would read as a slow build."""
        outcome, out, err = self._run([{"stream": "stdout", "data": ""}])  # then the connection closes

        self.assertEqual(outcome, 1)
        self.assertEqual(out, "")
        self.assertIn("lost the build service running `cadgen step compile tmp/noexh/noexh.step --force`", err)
        self.assertIn("closed the connection", err)
        self.assertIn("NOT retried", err)

    def test_a_daemon_that_falls_silent_fails_the_job(self):
        with mock.patch.object(client, "request_timeout", return_value=0.01), \
                mock.patch.object(client, "_recv_json", return_value=client._TIMED_OUT):
            outcome, _out, err = self._run([])
        self.assertEqual(outcome, 1)
        self.assertIn("stopped responding", err)
        self.assertIn("NOT retried", err)

    def test_ctrl_c_while_waiting_on_the_daemon_is_a_plain_interrupt(self):
        with mock.patch.object(client, "_run_with_retry", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt) as stop:
                client.run_via_daemon("run", ["/w/box.py"], "/w", prog="python box.py")
        self.assertTrue(stop.exception.__suppress_context__, "no traceback through the wait")

    def test_a_job_with_no_prog_is_named_by_its_tool(self):
        payload = {**PAYLOAD, "tool": "probe", "prog": None, "argv": ["a b.step"]}
        text = client.worker_died_message(payload, {"detail": "worker 1 exited with code 139"})
        self.assertIn("`cadgen probe a b.step`", text)
        self.assertIn("exited with code 139", text)

    def test_a_model_script_run_is_named_as_python(self):
        # The decorator's warm handoff: prog `python <name>`, argv `[<path>, *args]`.
        payload = {"tool": "run", "prog": "python box.py", "argv": ["/work/src/box.py", "--force"]}
        text = client.worker_died_message(payload, {"detail": "worker 3 was killed by SIGKILL (signal 9)"})
        self.assertIn("`python box.py --force` died mid-job", text)
        self.assertNotIn("box.py /work/src/box.py", text)

    def test_an_ordinary_stderr_frame_is_still_just_relayed(self):
        outcome, _out, err = self._run([{"stream": "stderr", "data": "hello\n"}, {"exit": 0}])
        self.assertEqual(outcome, 0)
        self.assertEqual(err, "hello\n")

    def test_a_released_frame_ends_the_request_with_its_code_before_the_exit(self):
        """the worker releases its client once the command's answer is
        complete and writes the files after; the client returns on the frame and reads
        nothing more (the exit frame reaches nobody)."""
        outcome, out, _err = self._run([{"stream": "stdout", "data": "current part.step\n"}, {"released": 0}])
        self.assertEqual((outcome, out), (0, "current part.step\n"))


class ResidentProcessLifecycle(unittest.TestCase):
    def test_the_daemon_starts_outside_the_callers_project_directory(self):
        spawned = mock.Mock(pid=1234)
        with tempfile.TemporaryDirectory(prefix="cadgen-daemon-launch-") as tmp, \
                mock.patch.object(client.transport, "ensure_authkey") as ensure, \
                mock.patch.object(client, "daemon_identity", return_value="test"), \
                mock.patch.object(client, "log_path", return_value=pathlib.Path(tmp) / "daemon.log"), \
                mock.patch.object(client.subprocess, "Popen", return_value=spawned) as popen:
            # worker_env is imported inside the function, so patch its source.
            from cadgen.daemon import executors

            with mock.patch.object(executors, "worker_env", return_value={}):
                self.assertIs(client._spawn_daemon("test-address"), spawned)

        self.assertEqual(popen.call_args.kwargs["cwd"], tempfile.gettempdir())
        ensure.assert_not_called()

    def test_the_daemon_takes_no_telemetry_switch_or_channel_from_the_client_that_started_it(self):
        # It serves every client; each client's switch travels with its own builds instead, and the channel it
        # reports is the one its installation's plugin wrote down (`cadgen/_internal/channel.py`).
        spawned = mock.Mock(pid=1234)
        started_from = {"PATH": "/bin", "DO_NOT_TRACK": "1", "CADGEN_TELEMETRY": "0",
                        "CADGEN_INSTALL_CHANNEL": "claude-github"}
        with tempfile.TemporaryDirectory(prefix="cadgen-daemon-launch-") as tmp, \
                mock.patch.object(client.transport, "ensure_authkey"), \
                mock.patch.object(client, "daemon_identity", return_value="test"), \
                mock.patch.object(client, "log_path", return_value=pathlib.Path(tmp) / "daemon.log"), \
                mock.patch.object(client.subprocess, "Popen", return_value=spawned) as popen:
            from cadgen.daemon import executors

            with mock.patch.object(executors, "worker_env", return_value=started_from):
                self.assertIs(client._spawn_daemon("test-address"), spawned)

        env = popen.call_args.kwargs["env"]
        self.assertEqual(env["PATH"], "/bin")
        for name in ("DO_NOT_TRACK", "CADGEN_TELEMETRY", "CADGEN_INSTALL_CHANNEL"):
            self.assertNotIn(name, env)

    def test_replaced_key_is_retried_only_after_the_live_owner_republishes(self):
        channel = mock.Mock()
        with mock.patch.object(client.transport, "read_authkey", side_effect=[b"stale", b"owned"]), \
                mock.patch.object(
                    client.transport,
                    "connect",
                    side_effect=[transport.AuthenticationError("rejected"), channel],
                ) as connect:
            self.assertIs(client._connect("private-address"), channel)
        self.assertEqual(
            connect.call_args_list,
            [mock.call("private-address", b"stale"), mock.call("private-address", b"owned")],
        )

    def test_an_idle_spare_starts_outside_the_daemons_project_directory(self):
        with io.StringIO('{"ready": 1234}\n') as stdout:
            process = mock.Mock(stdout=stdout)
            with mock.patch.object(pool_mod.subprocess, "Popen", return_value=process) as popen, \
                    mock.patch.object(client, "daemon_address", return_value="test-address"):
                worker = pool_mod.Worker()
                worker._reader.join(timeout=1)
                self.assertFalse(worker._reader.is_alive())
        self.assertEqual(worker.pid, 1234)
        self.assertEqual(popen.call_args.kwargs["cwd"], tempfile.gettempdir())

    def test_prewarm_starts_nothing_in_a_process_the_daemon_started(self):
        # A worker never warms the daemon it belongs to.
        with mock.patch.dict("os.environ", {"CADGEN_DAEMON_CHILD": "1"}), \
                mock.patch.object(client, "_connect_or_spawn", side_effect=AssertionError("a daemon was started")):
            self.assertFalse(client.prewarm())

    def test_prewarm_replaces_a_daemon_left_by_older_code(self):
        stale, current = _ScriptedChannel([{"restart": True}]), _ScriptedChannel([{"status": {}}])
        with mock.patch.dict("os.environ", {}), \
                mock.patch.object(client, "daemon_address", return_value="test-address"), \
                mock.patch.object(client, "_connect_or_spawn", side_effect=[stale, current]) as connect:
            os.environ.pop("CADGEN_DAEMON_CHILD", None)
            self.assertTrue(client.prewarm())
        self.assertEqual(connect.call_count, 2)
        self.assertEqual([frame["kind"] for frame in stale.sent + current.sent], ["status", "status"])

    def test_a_request_built_before_an_edit_is_asked_again_as_the_code_is_now(self):
        # A build in flight when cadgen's code changed was resent with the token it was built
        # under, so every daemon spawned to take over exited on it: 698 in ten minutes.
        tokens = []

        class Successor(_ScriptedChannel):
            """A daemon started after the edit: it serves the new code's token and no other."""

            def send(self, raw):
                super().send(raw)
                tokens.append(self.sent[-1]["token"])
                frame = {"exit": 0} if tokens[-1] == "after" else {"restart": True}
                self._frames = [json.dumps(frame).encode("utf-8")]

        with mock.patch.object(client, "compute_version_token", return_value="after"), \
                mock.patch.object(client, "daemon_address", return_value="test-address"), \
                mock.patch.object(client, "_connect_or_spawn", side_effect=lambda address: Successor([])), \
                mock.patch.object(client, "request_timeout", return_value=0.0):
            code = client._run_with_retry({**PAYLOAD, "token": "before"}, strict=True, on_stream=lambda text: None)
        self.assertEqual(code, 0)
        self.assertEqual(tokens, ["before", "after"])

    def test_a_job_runs_in_its_callers_environment_and_the_daemon_keeps_its_own_names(self):
        # A model reading os.environ, and a client's own choice such as CADGEN_STORE_MAX
        # (STORE.md §8), see the caller's environment: what the caller has is set, what it
        # lacks is unset -- even in a daemon started with it -- and the names the daemon's
        # machinery sets for itself are neither sent nor replaced.
        import os

        from cadgen.daemon import worker

        with mock.patch.dict("os.environ", {"SIZE": "10", "CADGEN_STORE_MAX": "1G",
                                            "CADGEN_DAEMON_CHILD": "1", "CADGEN_DAEMON_SOCKET": "/daemon.sock"}):
            caller = client.forwarded_env()
            self.assertEqual((caller["SIZE"], caller["CADGEN_STORE_MAX"]), ("10", "1G"))
            self.assertFalse(set(caller) & client.INTERNAL_ENV_VARS)
            caller = {name: value for name, value in caller.items() if name != "CADGEN_STORE_MAX"}
            worker._apply_request_env({"env": {**caller, "SIZE": "20", "CADGEN_DAEMON_SOCKET": "/elsewhere.sock"}})
            self.assertEqual(os.environ["SIZE"], "20")
            self.assertNotIn("CADGEN_STORE_MAX", os.environ)
            self.assertEqual((os.environ["CADGEN_DAEMON_CHILD"], os.environ["CADGEN_DAEMON_SOCKET"]),
                             ("1", "/daemon.sock"))

    def test_a_builds_file_trace_never_sees_what_the_version_token_reads(self):
        # A model body's compile or artifact job asks for the token on the build's thread:
        # cadgen's own files are never the model's input.
        from cadgen._internal import filetrace

        with filetrace.capture() as trace:
            client.compute_version_token()
        files, folders = trace.inputs()
        self.assertEqual((list(files), list(folders)), ([], []))

    def test_the_daemon_popen_is_retained_by_an_owned_reaper(self):
        process = mock.Mock(pid=4321)
        finished = threading.Event()
        process.wait.side_effect = lambda: finished.set()
        client._reap_detached(process)
        self.assertTrue(finished.wait(1.0), "the detached process was not handed to its reaper")
        process.wait.assert_called_once_with()


class ADaemonThatCannotTakeTheRequest(unittest.TestCase):
    """Where no daemon takes a request, the request fails, saying why: nothing runs it
    anywhere else."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="cadgen-daemon-start-")
        self.addCleanup(tmp.cleanup)
        self.log = pathlib.Path(tmp.name) / "daemon.log"
        for patcher in (
            mock.patch.object(client, "daemon_address", return_value="test-address"),
            mock.patch.object(client, "log_path", return_value=self.log),
            mock.patch.object(client, "_connect", side_effect=FileNotFoundError("no daemon listens there")),
            mock.patch.object(client, "unavailable", return_value=None),
            mock.patch.object(client.transport, "spawn_lock", return_value=mock.Mock(acquire=lambda: True)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _command(self) -> tuple[int | None, str]:
        err = io.StringIO()
        with redirect_stderr(err):
            code = client.run_via_daemon("step-build", ["in.step", "out.step"], "/work", prog="cadgen step build")
        return code, err.getvalue()

    def test_where_none_can_start_the_command_fails_naming_why_and_starts_none(self):
        with mock.patch.object(client, "unavailable", return_value="no local IPC family here"), \
                mock.patch.object(client, "_spawn_daemon", side_effect=AssertionError("a daemon was started")):
            code, err = self._command()
        self.assertEqual(code, 1)
        self.assertIn("cadgen-daemon: no local IPC family here", err)

    def test_a_start_that_fails_names_why(self):
        from cadgen.daemon import executors

        with mock.patch.object(executors, "worker_env", return_value={}), \
                mock.patch.object(client.subprocess, "Popen", side_effect=FileNotFoundError(2, "No such file or directory")):
            code, err = self._command()
        self.assertEqual(code, 1)
        self.assertIn("could not be started", err)
        self.assertIn("No such file or directory", err)

    def test_a_daemon_that_exits_without_answering_is_named_with_its_log(self):
        exited = mock.Mock(returncode=1)
        exited.poll.return_value = 1
        with mock.patch.object(client, "_spawn_daemon", return_value=exited), \
                mock.patch.object(client, "_address_held", return_value=False):
            code, err = self._command()
        self.assertEqual(code, 1)
        self.assertIn("exited (status 1) before it answered", err)
        self.assertIn(str(self.log), err)

    def test_one_that_stood_down_for_a_predecessor_is_started_again_once_it_lets_go(self):
        stood_down, serving = mock.Mock(returncode=0), mock.Mock()
        stood_down.poll.return_value = 0
        serving.poll.return_value = None
        channel = _ScriptedChannel([])
        spawned = []

        def spawn(address):
            spawned.append(address)
            return stood_down if len(spawned) == 1 else serving

        def connect(address):
            if len(spawned) < 2:
                raise FileNotFoundError("no daemon listens there yet")
            return channel

        with mock.patch.object(client, "_spawn_daemon", side_effect=spawn), \
                mock.patch.object(client, "_connect", side_effect=connect), \
                mock.patch.object(client, "_address_held", return_value=True):
            self.assertIs(client._connect_or_spawn("test-address"), channel)
        self.assertEqual(spawned, ["test-address", "test-address"])

    def test_a_nested_job_fails_with_the_reason_on_its_own_stream(self):
        said = []
        with mock.patch.object(client, "unavailable", return_value="the state directory is not writable"):
            code = client.run_nested("step-compile", ["doc.step"], "/work", prog="cadgen step compile",
                                     on_stream=said.append)
        self.assertEqual(code, 1)
        self.assertEqual(said, ["cadgen-daemon: the state directory is not writable\n"])

    def test_a_request_waits_out_a_daemon_finishing_its_builds_then_fails_naming_it(self):
        with mock.patch.object(client, "_connect_or_spawn", side_effect=lambda address: _ScriptedChannel([{"restart": True}])), \
                mock.patch.object(client, "request_timeout", return_value=0.0):
            code, err = self._command()
        self.assertEqual(code, 1)
        self.assertIn("finishing its builds before it updates", err)
        self.assertIn("updating while existing builds finish", err)


class ServerRelaysTheDeath(unittest.TestCase):
    """The supervisor turns WorkerGone into its own frame, before the exit frame,
    and logs it -- ``cadgen daemon status`` cannot show a worker that is gone."""

    class _DyingWorker:
        pid = 777
        extra = False

        def __init__(self, exit_status: int = -9) -> None:
            self.sent: list[dict] = []
            self.exit_status = exit_status

        def send(self, request: dict) -> None:
            self.sent.append(request)

        def frames(self, **_kwargs):
            yield {"stream": "stdout", "data": "partial "}
            raise pool_mod.WorkerGone(
                f"worker 777 {pool_mod.describe_exit(self.exit_status)}", exit_status=self.exit_status
            )

        def alive(self) -> bool:
            return False

    class _Conn:
        def __init__(self) -> None:
            self.frames: list[dict] = []

        def send(self, raw: bytes) -> None:
            self.frames.append(json.loads(raw.decode("utf-8")))

    def test_worker_died_frame_precedes_the_exit_frame(self):
        worker = self._DyingWorker()
        pool = mock.Mock()
        pool.acquire.return_value = worker
        conn = self._Conn()
        request = {"tool": "step-compile", "argv": ["x.step"], "cwd": "/w", "prog": "cadgen step compile"}
        logged: list[str] = []
        jobs = JobLedger()
        with mock.patch.object(server, "_JOBS", jobs), \
                mock.patch.object(server, "_POOL", pool), \
                mock.patch.object(server, "_log", logged.append), \
                mock.patch.object(server, "CLIENT_LIVENESS_INTERVAL_SECONDS", 60.0):
            server._handle_request(conn, request)
        kinds = [next(iter(frame)) for frame in conn.frames if frame != {"stream": "stdout", "data": ""}]
        self.assertEqual(kinds, ["stream", "workerDied", "exit"])
        died = next(frame["workerDied"] for frame in conn.frames if "workerDied" in frame)
        self.assertEqual(jobs.snapshot()[0]["error"], died["detail"])
        self.assertEqual(died["pid"], 777)
        self.assertEqual(died["exitStatus"], -9)
        self.assertIn(pool_mod.describe_exit(-9), died["detail"])  # the worker's own words, as this platform names the signal
        self.assertEqual(conn.frames[-1], {"exit": 1})
        pool.release.assert_called_once_with(worker, healthy=False, cancelled=False)
        self.assertTrue(any("died mid-job" in line for line in logged), logged)

    @unittest.skipIf(os.name == "nt", "a Windows worker's exit status is a code, never a signal")
    def test_a_worker_someone_stopped_is_no_crash_and_one_that_faulted_still_is(self):
        import signal

        # SIGTERM is someone stopping it (a person, a logout, a shutdown): the build was stopped, and the
        # client still hears how its worker went. A fault, or the kernel's kill for memory, is a crash.
        for status, ended in ((-signal.SIGTERM, "cancelled"), (-signal.SIGINT, "cancelled"), (-signal.SIGHUP, "cancelled"),
                              (-signal.SIGSEGV, "crashed"), (-signal.SIGKILL, "crashed"), (-signal.SIGABRT, "crashed")):
            with self.subTest(status=status):
                pool = mock.Mock()
                pool.acquire.return_value = self._DyingWorker(status)
                conn = self._Conn()
                build = mock.Mock()
                with mock.patch.object(server, "_JOBS", JobLedger()), mock.patch.object(server, "_POOL", pool), \
                        mock.patch.object(server, "_log"), mock.patch.object(server, "CLIENT_LIVENESS_INTERVAL_SECONDS", 60.0), \
                        mock.patch.object(server.telemetry, "build", return_value=build), \
                        mock.patch.object(server.telemetry, "worker_died") as worker_died:
                    server._handle_request(conn, {"tool": "step-compile", "argv": ["x.step"], "cwd": "/w",
                                                  "prog": "cadgen step compile"})
                self.assertEqual(build.finish.call_args.args[1], ended)
                self.assertEqual(worker_died.call_args_list, [mock.call(status)] if ended == "crashed" else [])
                # The pool's crash count (`cadgen daemon status`) agrees: a stopped job is no crash.
                self.assertEqual(pool.release.call_args.kwargs["cancelled"], ended == "cancelled")
                self.assertEqual(next(frame["workerDied"]["exitStatus"] for frame in conn.frames if "workerDied" in frame), status)

    def test_a_job_that_released_its_client_finishes_after_the_client_left(self):
        """a worker that released its client goes on writing the files; the
        client leaving after that is no loss -- the worker is not killed, its job ends with
        its exit frame as done, not cancelled, and the ledger closes it then."""
        class Releasing:
            pid = 778
            extra = False

            def __init__(self) -> None:
                self.sent: list[dict] = []

            def send(self, request: dict) -> None:
                self.sent.append(request)

            def frames(self, **_kwargs):
                yield {"stream": "stdout", "data": "built part.step\n"}
                yield {"released": 0}
                yield {"event": {"model": "/w/part.py", "state": "building", "phase": "STEP saved",
                                 "saved": {"output": "/w/part.step", "tree": "t" * 64, "documentHash": "d" * 64}}}
                yield {"exit": 0}

            def alive(self) -> bool:
                return True

            def kill(self) -> None:
                raise AssertionError("the worker was killed after releasing its client")

        class Leaves(self._Conn):
            """A client that returns on the released frame: every send after it fails."""

            gone = False

            def send(self, raw: bytes) -> None:
                if self.gone:
                    raise BrokenPipeError("the client left")
                super().send(raw)
                self.gone = "released" in self.frames[-1]

        pool = mock.Mock()
        worker = Releasing()
        pool.acquire.return_value = worker
        conn = Leaves()
        jobs = JobLedger()
        with mock.patch.object(server, "_JOBS", jobs), mock.patch.object(server, "_POOL", pool), \
                mock.patch.object(server, "_log"), mock.patch.object(server, "CLIENT_LIVENESS_INTERVAL_SECONDS", 60.0):
            server._handle_request(conn, {"tool": "run", "argv": ["/w/part.py"], "cwd": "/w",
                                          "prog": "python part.py"})
        self.assertEqual([next(iter(frame)) for frame in conn.frames if frame != {"stream": "stdout", "data": ""}],
                         ["stream", "released"], "the client heard its release and nothing after")
        pool.release.assert_called_once_with(worker, healthy=True, cancelled=False)
        (job,) = jobs.snapshot()
        self.assertEqual((job["state"], job["exit"]), ("done", 0))
        self.assertEqual(job["savedResults"][os.path.realpath("/w/part.step")]["documentHash"], "d" * 64,
                         "the save landed after the release")

    def test_a_job_whose_caller_left_is_released_as_cancelled_not_crashed(self):
        class Gone(self._Conn):
            def send(self, raw: bytes) -> None:
                raise BrokenPipeError("the client left")

        pool = mock.Mock()
        worker = self._DyingWorker()
        pool.acquire.return_value = worker
        with mock.patch.object(server, "_JOBS", JobLedger()), mock.patch.object(server, "_POOL", pool), \
                mock.patch.object(server, "_log"), mock.patch.object(server, "CLIENT_LIVENESS_INTERVAL_SECONDS", 60.0):
            server._handle_request(Gone(), {"tool": "step-compile", "argv": ["x.step"], "cwd": "/w",
                                            "prog": "cadgen step compile"})
        pool.release.assert_called_once_with(worker, healthy=False, cancelled=True)


class AWorkerThatCouldNotStart(unittest.TestCase):
    """A worker that could not start is a crash, unless the daemon's installation was removed under it:
    then no worker can start, and the daemon retires so its clients start the next from their own."""

    def handle(self, gone: bool):
        pool = mock.Mock()
        pool.acquire.side_effect = pool_mod.WorkerGone("worker 777 exited with code 106 before announcing itself",
                                                       exit_status=106)
        conn = ServerRelaysTheDeath._Conn()
        self.addCleanup(server._INSTALLATION_GONE.clear)
        with mock.patch.object(server, "_JOBS", JobLedger()), mock.patch.object(server, "_POOL", pool), \
                mock.patch.object(server, "_log"), \
                mock.patch.object(server.pool_mod, "installation_gone", return_value=gone), \
                mock.patch.object(server.telemetry, "worker_died") as worker_died:
            server._handle_request(conn, {"tool": "step-compile", "argv": ["x.step"], "cwd": "/w",
                                          "prog": "cadgen step compile"})
        self.assertEqual(conn.frames[-1], {"exit": 1})
        return worker_died

    def test_a_start_that_failed_is_a_crash(self):
        self.assertEqual(self.handle(gone=False).call_args_list, [mock.call(106)])
        self.assertFalse(server._INSTALLATION_GONE.is_set())

    def test_a_start_the_removed_installation_failed_is_none_and_retires_the_daemon(self):
        self.assertEqual(self.handle(gone=True).call_args_list, [])
        self.assertTrue(server._INSTALLATION_GONE.is_set())


class ServerStatusIdentity(unittest.TestCase):
    def test_status_keeps_the_loaded_startup_token_when_disk_code_changes(self):
        pool = mock.Mock()
        pool.snapshot.return_value = {"workers": []}
        broker = mock.Mock()
        broker.snapshot.return_value = {}
        jobs = mock.Mock()
        jobs.snapshot.return_value = []
        with mock.patch.object(server, "_POOL", pool), \
                mock.patch.object(server, "_BROKER", broker), \
                mock.patch.object(server, "_JOBS", jobs), \
                mock.patch.object(
                    server,
                    "compute_version_token",
                    side_effect=AssertionError("status reread the changed source tree"),
                ):
            status = server._status_payload("loaded-at-startup")
        self.assertEqual(status["token"], "loaded-at-startup")


class DescribeExit(unittest.TestCase):
    def test_signal_code_and_open_pipe_are_told_apart(self):
        import signal

        expected = "was killed by SIGKILL (signal 9)" if hasattr(signal, "SIGKILL") else "was killed by signal 9"
        self.assertEqual(pool_mod.describe_exit(-9), expected)
        self.assertEqual(pool_mod.describe_exit(2), "exited with code 2")
        self.assertEqual(pool_mod.describe_exit(None), "closed its output while still running")

    def test_an_unnamed_signal_is_not_described_twice(self):
        """99 is not a signal on any host, so this is the Windows wording for 9 --
        `was killed by signal 9 (signal 9)` was the stutter it used to produce."""
        self.assertEqual(pool_mod.describe_exit(-99), "was killed by signal 99")

    def test_a_windows_exit_status_is_a_code_not_a_signal(self):
        """Windows' Popen.wait returns a non-negative DWORD: a TerminateProcess
        kill comes back as the exit CODE, never as a negative signal."""
        self.assertEqual(pool_mod.describe_exit(9), "exited with code 9")


if __name__ == "__main__":
    unittest.main()
