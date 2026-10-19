"""Stdlib-only client for the warm CAD CLI daemon.

The front door (``cadgen.cli``) and a model script's top-level call import this module
BEFORE any heavy import, so it must stay dependency-free and cheap to import. The daemon
is where every job runs: a build, a compile, a display derivation, an export. A daemon
that cannot be reached or started is an error that names why (:class:`DaemonUnavailable`),
never a slower path, and a request the daemon lost is reported, never replayed. Only a
command its caller ran with ``--no-daemon`` (:data:`NO_DAEMON_FLAG`) builds without one:
that process runs every job itself and never asks a daemon anything.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

from cadgen.cli import NO_DAEMON_FLAG
from cadgen.daemon import transport

# The installed cadgen package directory. Everything the daemon holds resident lives under
# it, so it is both the identity of "which cadgen is this" and the thing to watch for edits.
# It resolves correctly either way: an editable install points at a checkout's source, a
# wheel install at site-packages.
CADGEN_DIR = Path(__file__).resolve().parents[1]

SPAWN_WAIT_SECONDS = 30.0  # how long a started daemon has to answer at its address
# A daemon still finishing the jobs it was running when its code changed keeps its address
# and answers every new request "restart" until they end. A request asks again this often
# until the successor takes it.
RESTART_POLL_SECONDS = 0.5

# How long a request waits on a daemon that sends it nothing at all. A job that runs is
# never silent -- the daemon probes its client every half second meanwhile
# (``server._watch_client``) -- so this is a daemon that stopped answering, and the request
# fails rather than hanging. Generous, because a request waiting to be admitted, or for
# its predecessor's drain, hears nothing meanwhile.
DEFAULT_REQUEST_TIMEOUT_SECONDS = 600.0

_RESTART = object()
# A frame did not arrive in time, as distinct from the channel closing.
_TIMED_OUT = object()


class DaemonUnavailable(RuntimeError):
    """No build daemon could take a request. The message names why, and what to do."""


# The one opt-in out of the daemon, for a place it cannot run (a sandbox without local
# sockets, a CI runner): a command given it builds in its own process, and that process
# never connects to, starts or asks a daemon. It is never a fallback: a command without it
# whose daemon cannot take its request fails, saying why (``DaemonUnavailable``). Its doors:
# a model script (``cli._run_model``), and every door the front door would hand over
# (``cli._WARM_COMMANDS``); the CAD Viewer and the CAD app need the daemon and refuse it
# (:func:`refuse_no_daemon`). The flag is spelled once, by the front door (``cli``). Set for
# the rest of the process: nothing it does later -- another model, a drawing's counts, a
# crash report -- asks a daemon either.
_NO_DAEMON = [False]


def no_daemon_active() -> bool:
    """Whether this process builds without the daemon (:data:`NO_DAEMON_FLAG`): every pool job
    it owes runs here, inline (``artifacts.submit_artifact``, ``executors.submit_compile``)."""
    return _NO_DAEMON[0]


def no_daemon_from_now() -> None:
    """Build without the daemon for the rest of this process: a command run with
    :data:`NO_DAEMON_FLAG`."""
    _NO_DAEMON[0] = True


def refuse_no_daemon(argv, *, prog: str, what: str) -> None:
    """End a command that needs the daemon when it was given :data:`NO_DAEMON_FLAG`, in one
    sentence (exit 2, as a refused argument)."""
    if NO_DAEMON_FLAG in list(argv or ()):
        sys.stderr.write(f"{prog}: {what} needs the build daemon, so it cannot run with {NO_DAEMON_FLAG}.\n")
        raise SystemExit(2)


# A job runs in its CALLER's environment, never the daemon's. A worker inherits the
# environment of whichever build spawned the daemon, so a model that read
# ``os.environ["SIZE"]`` saw that build's SIZE, not its own caller's, and the
# first build's cache root silently became every later build's, across projects. So
# the caller's whole environment travels with every request and the worker applies it
# per JOB: a name the caller has is set, a name it lacks is unset, which also clears
# what a previous job's model code exported (worker._apply_request_env). That is
# also how the per-client choices reach a job: the cache root (CADGEN_CACHE_DIR,
# XDG_CACHE_HOME, LOCALAPPDATA), the import roots (PYTHONPATH, absolutized against the
# client's cwd because the worker runs elsewhere), the cap the daemon's idle housekeeping
# holds the client's store to (CADGEN_STORE_MAX, STORE.md §8), and the telemetry switches
# (``cadgen.analytics.ENVIRONMENT``): a client whose environment turns telemetry off has
# none of its builds counted, whatever the environment of the build that started the
# daemon said (``cadgen.daemon.telemetry``). CADGEN_FFMPEG is resolved here, below.
#
# Except the names the daemon, its pool and a job's own machinery set for themselves:
# a caller never sends them, and applying a job's environment leaves them as they are.
INTERNAL_ENV_VARS = frozenset({
    # A daemon worker's own: that it serves a daemon, and that daemon's address and
    # key, which live in its own state directory (pool.Worker, worker.serve).
    "CADGEN_DAEMON_CHILD",
    "CADGEN_DAEMON_SOCKET",
    "CADGEN_DAEMON_STATE_DIR",
    # A request's own field (worker._apply_request_env).
    "CADGEN_JOB_ID",
    # The daemon's processes report their installation's channel, whoever started
    # them (_spawn_daemon), and a Windows worker names its virtual environment so
    # (pool.interpreter).
    "CADGEN_INSTALL_CHANNEL",
    "__PYVENV_LAUNCHER__",
})

# The client's own ffmpeg, looked up once per process. Resolved HERE rather than
# in the worker because a PATH lookup only answers for the process that does it:
# a tool dispatched to a resident worker sees the PATH belonging to
# whatever first spawned the daemon -- an editor, an agent session, a cron run --
# so an ffmpeg the caller can run was reported as not installed, and installing
# one after the daemon started never helped. Whenever the caller has one, the
# ABSOLUTE path travels and the worker looks nothing up; the worker keeps its own
# PATH fallback for the callers that never came through here.
_ffmpeg_on_path: str | None = None


def _client_ffmpeg() -> str:
    global _ffmpeg_on_path
    if _ffmpeg_on_path is None:
        import shutil

        _ffmpeg_on_path = shutil.which("ffmpeg") or ""
    return _ffmpeg_on_path


def forwarded_env() -> dict[str, str]:
    """The requesting process's environment, which a job runs in, for the payload:
    all of it but the daemon's own names (``INTERNAL_ENV_VARS``)."""
    env = {name: value for name, value in os.environ.items() if name not in INTERNAL_ENV_VARS}
    if "PYTHONPATH" in env:
        entries = [os.path.abspath(e) for e in env["PYTHONPATH"].split(os.pathsep) if e]
        env["PYTHONPATH"] = os.pathsep.join(entries)
    # An explicit CADGEN_FFMPEG is the caller's answer and stands; otherwise the
    # caller's PATH is asked here, where it is the caller's.
    if not env.get("CADGEN_FFMPEG"):
        found = _client_ffmpeg()
        if found:
            env["CADGEN_FFMPEG"] = found
    return env


def unavailable() -> str | None:
    """Why no build daemon can start for this installation here, or None when one can.

    Answered without starting one, from what a start needs: a local IPC family (the
    transport's: AF_UNIX on POSIX, AF_PIPE on Windows), a socket folder of this user's own
    (``transport.claim_folder``), and a state directory this user can write, where the key,
    the locks and the log go. ``cadgen doctor`` reports it, the CAD Viewer refuses to start
    on it, and a request that finds no daemon raises it before spawning one.
    """
    if not transport.supported():
        return ("This Python offers no local IPC family for the build daemon (AF_UNIX on POSIX, "
                "AF_PIPE on Windows), so cadgen cannot build with it; use an interpreter that has one.")
    try:
        transport.claim_folder(daemon_address(), create=False)
    except transport.AddressUnusable as error:
        return str(error)
    folder = transport.state_dir()
    existing = folder
    while not existing.exists() and existing.parent != existing:
        existing = existing.parent
    if not existing.is_dir() or not os.access(existing, os.W_OK | os.X_OK):
        return (f"The build daemon keeps its key, locks and log in {folder}, which this user cannot "
                "write; set CADGEN_DAEMON_STATE_DIR to a folder this user can write.")
    return None


def daemon_identity() -> str:
    """The short name this checkout's daemon is known by."""
    return transport.identity_digest(str(CADGEN_DIR))


def daemon_address() -> str:
    """Where this checkout's daemon listens.

    CADGEN_DAEMON_SOCKET still overrides it, and still means "the address" -- a filesystem
    path on POSIX, a pipe name on Windows. The default carries a protocol version, so a
    client never reaches a daemon speaking the older newline-JSON format.
    """
    override = os.environ.get("CADGEN_DAEMON_SOCKET")
    if override:
        return str(override)
    return transport.address_for(daemon_identity())


def log_path(address: str | None = None) -> Path:
    """Where the daemon's lifecycle and OCP noise go.

    Derived from the identity rather than from the address: a pipe name is not a path, so
    there is nothing to hang a sibling .log off on Windows. On POSIX it is the socket's
    sibling, kept in the state directory when the socket had to move out of it
    (``transport.beside``).
    """
    if address and os.name != "nt":
        return transport.beside(address, Path(address).with_suffix(".log").name)
    return transport.state_dir() / f"cadgen-daemon-{daemon_identity()}.log"


def request_timeout() -> float:
    """Seconds a request waits on a daemon that sends it nothing before it fails."""
    return DEFAULT_REQUEST_TIMEOUT_SECONDS


def _declared_version(base: Path) -> str:
    """The version declared beside the code under ``base`` (``_internal.version``): the one
    ``cadgen.__version__`` answers for the running package, computed for any package dir."""
    from cadgen._internal.version import declared_version

    return declared_version(base)


def compute_version_token(root: Path | None = None) -> str:
    """The running daemon's identity: the version declared beside its code plus the newest
    ``.py`` mtime under it. Client and server compute this identically from the same
    files; inequality means restart. The version alone is not enough in a checkout, where
    an editable install keeps one version across every edit; mtimes alone are not enough
    for a wheel, where they are fixed at install time. Skill code is not watched: the
    daemon never imports it.

    Read unseen by a build's file trace (``filetrace.paused``): a model body's compile or
    artifact job asks for it on the build's thread, and cadgen's own files are never a
    model's input."""
    from cadgen._internal.filetrace import paused

    base = Path(root) if root is not None else CADGEN_DIR
    newest = 0
    with paused():
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [name for name in dirnames if name != "__pycache__"]
            for filename in filenames:
                if not filename.endswith(".py"):
                    continue
                try:
                    mtime = os.stat(os.path.join(dirpath, filename)).st_mtime_ns
                except OSError:
                    continue
                newest = max(newest, mtime)
        return f"{_declared_version(base)}:{newest}"


def _request_payload(
    tool: str,
    argv: list[str],
    cwd: str | None,
    prog: str | None,
    *,
    store_root: str | None = None,
    closure: str | None = None,
    coalesce: bool = False,
    dependency: bool = False,
) -> dict:
    from cadgen.store.paths import store_root as default_store_root

    return {
        "tool": str(tool),
        # The name the caller is known by. Without it the daemon invented
        # "scripts/<tool>", so `cadgen step build --help` printed a usage line naming
        # a command nobody typed.
        "prog": str(prog) if prog else None,
        "argv": [str(arg) for arg in argv],
        "cwd": str(cwd) if cwd else os.getcwd(),
        "env": forwarded_env(),
        # The store this job reads and writes: an explicit request field, never
        # the worker's ambient environment, so one daemon serves isolated stores.
        "store_root": str(store_root) if store_root else str(default_store_root()),
        # In-flight coalescing (cadgen.daemon.broker): a compile carries its document's
        # digest and may join a job already compiling the same bytes. A top-level request
        # never does -- the model the user asked for runs.
        "closure": str(closure) if closure else None,
        "coalesce": bool(coalesce and closure),
        # A nested request may consume the reserved dependency-progress
        # headroom. This is independent of whether it can coalesce.
        "dependency": bool(dependency),
        "token": compute_version_token(),
    }


def run_via_daemon(tool: str, argv: list[str], cwd: str | None = None, prog: str | None = None) -> int:
    """Run one CLI invocation on the warm daemon and return its exit code: a daemon that
    cannot take it fails it (exit 1), saying why on stderr. Never called in a daemon worker,
    which is where a job runs (``CADGEN_DAEMON_CHILD``): its callers run the job there."""
    from cadgen.daemon.executors import emit_event

    payload = _request_payload(tool, [str(arg) for arg in argv], cwd, prog)
    try:
        return _run_with_retry(payload, on_event=emit_event)
    except DaemonUnavailable as error:
        _say(f"cadgen-daemon: {error}\n")
        return 1
    except KeyboardInterrupt:
        # Ctrl-C while this process only waits on the daemon: the build service stops the
        # job when its caller leaves. Raised fresh, so a script's own handler catches the
        # interrupt without the transport's wait in its traceback.
        raise KeyboardInterrupt from None


def run_nested(
    tool: str,
    argv: list[str],
    cwd: str | None,
    *,
    prog: str | None = None,
    store_root: str | None = None,
    closure: str | None = None,
    on_stream: Callable[[str], None] | None = None,
    on_event: Callable[[dict], None] | None = None,
) -> int:
    """A job submitted from INSIDE a build or a door (a worker or a client): a
    document's compile, coalesced by ``closure`` (the document's bytes).

    Unlike :func:`run_via_daemon` this ignores ``CADGEN_DAEMON_CHILD``: a worker
    submitting a compile is the pool's nesting, not recursion into itself. Stream
    frames go to ``on_stream`` (captured for the error path), events to
    ``on_event``. A daemon that cannot take the job fails it (exit 1), saying why
    on ``on_stream``.
    """
    payload = _request_payload(
        tool, argv, cwd, prog, store_root=store_root, closure=closure, coalesce=True, dependency=True,
    )
    try:
        return _run_with_retry(payload, on_stream=on_stream, on_event=on_event)
    except DaemonUnavailable as error:
        _say(f"cadgen-daemon: {error}\n", on_stream)
        return 1


def _say(text: str, on_stream: Callable[[str], None] | None = None) -> None:
    """A line about the request itself, where its stream goes: ``on_stream``, or stderr."""
    if on_stream is not None:
        on_stream(text)
        return
    sys.stderr.write(text)
    sys.stderr.flush()


def artifact_payload(request: dict, *, store_root: str, dependency: bool = False) -> dict:
    """Capture a structured source-free request on the calling thread."""
    from cadgen.daemon.artifacts import normalize_request, store_path

    payload = _request_payload("artifact", [], None, None, store_root=store_path(store_root), dependency=dependency)
    payload["artifact"] = normalize_request(request)
    return payload


def run_artifact(payload: dict, *, subscriber=None):
    """Run exactly this artifact request; a failure is reported, never replayed."""
    from cadgen.daemon.artifacts import ArtifactJobError, validate_result

    results, chunks = [], []

    def receive(value):
        if results:
            raise ArtifactJobError("artifact worker returned more than one result")
        results.append(validate_result(payload["artifact"], value))

    def connected(conn):
        if subscriber is not None:
            subscriber._bind_detach(conn.close if conn is not None else None)

    try:
        code = _run_with_retry(payload, on_stream=chunks.append, on_artifact_result=receive, strict=True,
                               on_connection=connected, cancelled=(lambda: subscriber.detached) if subscriber is not None else None)
    except DaemonUnavailable as error:
        raise ArtifactJobError(f"artifact request failed: {error}") from error
    if code != 0 or not results:
        detail = "".join(chunks).strip()
        if not detail:
            detail = ("The geometry service completed without returning the requested geometry."
                      if code == 0 else f"The geometry service failed (exit {code}).")
        raise ArtifactJobError(f"artifact request failed: {detail}")
    return results[0]


def _run_with_retry(payload: dict, *, on_stream=None, on_event=None,
                    on_artifact_result=None, strict: bool = False, on_connection=None, cancelled=None) -> int:
    """Send ``payload`` to this installation's daemon, starting it when none answers, and
    return the job's exit code. Raises :class:`DaemonUnavailable` when no daemon takes it:
    none could start, or one updating while its builds finish did not hand over in time."""
    address = daemon_address()
    restarted, deadline = False, None
    while True:
        if cancelled is not None and cancelled():
            return 1  # whoever asked has left: no one waits for this answer
        try:
            conn = _connect_or_spawn(address)
        except (transport.AuthenticationError, transport.AddressUnusable) as error:
            raise DaemonUnavailable(str(error)) from error
        try:
            if on_connection is not None:
                on_connection(conn)
            kwargs = {"on_stream": on_stream, "on_event": on_event}
            if strict or on_artifact_result is not None:
                kwargs.update(on_artifact_result=on_artifact_result, strict=strict, cancelled=cancelled)
            outcome = _run_request(conn, payload, **kwargs)
        finally:
            try:
                conn.close()
            except OSError:
                pass
            if on_connection is not None:
                on_connection(None)
        if outcome is not _RESTART:
            return outcome
        # Ask again as the code on disk is now. The token was stamped when the request was
        # built, and a successor takes its own at start: a request built before an edit and
        # resent as it was made every successor exit on it, until the deadline.
        payload = {**payload, "token": compute_version_token()}
        if not restarted:
            restarted = True
            continue  # the stale daemon is going; an idle one already released its address
        # Told to restart again: the stale daemon is finishing jobs and keeps its address
        # until they end. Wait for its successor as long as a silent daemon is waited on.
        if deadline is None:
            deadline = time.monotonic() + request_timeout()
            _say("cadgen-daemon: the build daemon is finishing its builds before it updates; waiting for it.\n",
                 on_stream)
        if time.monotonic() >= deadline:
            raise DaemonUnavailable("The build daemon is updating while existing builds finish. "
                                    "Retry after those builds finish.")
        time.sleep(RESTART_POLL_SECONDS)


def _connect(address: str) -> transport.Channel:
    if _NO_DAEMON[0]:
        # Nothing to ask: a reader of the ledger reads at once, counts are kept for the next sender.
        raise ConnectionRefusedError(f"this process builds without the daemon ({NO_DAEMON_FLAG})")
    key = transport.read_authkey(address) or b""
    try:
        return transport.connect(address, key)
    except transport.AuthenticationError:
        # A live lock owner repairs a replaced key after rejecting this handshake.
        # Its handshake thread and this client observe the rejection concurrently, so
        # give the owner a bounded window to finish the atomic publication. An empty
        # key is a recovery probe when external cleanup removed the file entirely.
        # Windows replacement can spend two 750 ms sharing-violation ladders,
        # including the unseen-copy retry. Leave a little scheduling headroom.
        deadline = time.monotonic() + 2.0
        while True:
            repaired = transport.read_authkey(address)
            if repaired and not transport.keys_match(key, repaired):
                return transport.connect(address, repaired)
            if time.monotonic() >= deadline:
                break
            time.sleep(0.005)
        raise


# Connect failures no spawn can fix: a live peer that rejects the key, or an address no
# daemon could listen at. The rest mean "nothing is listening yet".
_FINAL = (transport.AuthenticationError, transport.AddressUnusable)


def _connect_or_spawn(address: str) -> transport.Channel:
    """A channel to the daemon at ``address``, started here when none answers. Raises
    :class:`DaemonUnavailable` naming why there is none: what :func:`unavailable` finds,
    a start that failed, or a daemon that exited or stayed silent instead of answering."""
    if _NO_DAEMON[0]:
        # Every job of such a process runs in it; a request reaching here is cadgen's bug.
        raise DaemonUnavailable(f"a request for the build daemon was made by a process given {NO_DAEMON_FLAG}, "
                                "which runs every job itself")
    try:
        return _connect(address)
    except _FINAL:
        raise
    except OSError:
        pass
    reason = unavailable()
    if reason is not None:
        raise DaemonUnavailable(reason)
    # Never unlink the address here. Only the daemon that holds the singleton lock
    # may remove a leftover socket (server._bind). And only ONE client spawns: the
    # first to take the spawn lock starts the daemon; the others just wait for the
    # address to answer. Twenty concurrent clients used to start twenty daemons.
    election = transport.spawn_lock(address)
    spawner = election.acquire()
    process = None
    try:
        if spawner:
            # Double-check under the lock: a client whose first connect predates the
            # daemon's bind can take the election just after the previous spawner
            # released it. If the daemon answers now, there is nothing to spawn.
            try:
                return _connect(address)
            except _FINAL:
                raise
            except OSError:
                pass
            process = _spawn_daemon(address)
            _reap_detached(process)
        deadline = time.monotonic() + SPAWN_WAIT_SECONDS
        while True:
            try:
                return _connect(address)
            except _FINAL:
                raise
            except OSError:
                pass
            if process is not None and process.poll() is not None:
                # Our daemon exited without answering. It stood down, because the address's
                # lock was held -- by a daemon serving it now (one more connect finds it),
                # or by one still letting go of it, whose successor this client starts
                # again -- or it failed, and its log says why.
                try:
                    return _connect(address)
                except _FINAL:
                    raise
                except OSError:
                    pass
                if not _address_held(address):
                    raise DaemonUnavailable(
                        f"The build daemon exited (status {process.returncode}) before it answered at "
                        f"{address}; its log says why: {log_path(address)}")
                process = _spawn_daemon(address)
                _reap_detached(process)
            if time.monotonic() >= deadline:
                raise DaemonUnavailable(f"No build daemon answered at {address} within "
                                        f"{SPAWN_WAIT_SECONDS:.0f} s; its log says why: {log_path(address)}")
            time.sleep(0.05)
    finally:
        if spawner:
            election.release()


def _address_held(address: str) -> bool:
    """Whether a daemon holds ``address``'s singleton lock: one serving it, or one still
    letting go of it (``server._bind``)."""
    lock = transport.daemon_lock(address)
    if lock.acquire():
        lock.release()
        return False
    return True


def detach_kwargs() -> dict:
    """How to start a process that outlives the command that needed it: the daemon, and
    the CAD Viewer's ``--detach``.

    start_new_session is POSIX-only and Windows does not merely ignore it politely -- it
    is named `unused_start_new_session` in subprocess, so passing it there is silently
    nothing and the process would share its parent's console and die with it.

    On Windows it gets a console of its own with no window (CREATE_NO_WINDOW), never no
    console at all (DETACHED_PROCESS): every console program a process without a console
    starts -- each warm worker, a build, Node, ffmpeg -- gets a NEW console, with a window.
    That was a window per worker, open as long as the worker lived, and closing one ended
    the worker with STATUS_CONTROL_C_EXIT (#597). A windowless console is inherited by
    everything started under it, so nothing below opens a window.
    """
    if os.name == "nt":
        return {
            "creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
        }
    return {"start_new_session": True}


def _reap_detached(process: subprocess.Popen) -> None:
    """Retain and eventually reap the daemon process the client started.

    The daemon deliberately outlives this command, but dropping its ``Popen`` as soon as
    the socket answers makes Python warn that the subprocess is still running.  A daemon
    thread may wait for that detached process without keeping the client alive; it also
    closes the Windows process handle promptly when the daemon eventually exits.
    """
    def wait() -> None:
        with contextlib.suppress(OSError):
            process.wait()

    threading.Thread(
        target=wait,
        name=f"cadgen-daemon-{process.pid}",
        daemon=True,
    ).start()


def _spawn_daemon(address: str) -> subprocess.Popen:
    """Start the daemon for ``address``, its output to its log. Raises
    :class:`DaemonUnavailable` when it cannot be started: its log cannot be written, or the
    interpreter cannot be run."""
    from cadgen.analytics import for_others
    from cadgen.daemon.executors import worker_env

    # The daemon and its workers must import THIS cadgen from whatever directory they
    # run in; a relative PYTHONPATH entry would otherwise pick the installed one. The
    # daemon serves every client, so it takes no telemetry switch from the one that
    # started it: each client's travels with its own builds (forwarded_env).
    env = for_others(worker_env())
    # Nor its install channel: it reports the one its installation's plugin wrote down, whoever started it
    # (``cadgen/_internal/channel.py``).
    env.pop("CADGEN_INSTALL_CHANNEL", None)
    env["CADGEN_DAEMON_CHILD"] = "1"
    env.setdefault("CADGEN_DAEMON_SOCKET", str(address))
    log_file_path = log_path(address)
    try:
        log_file_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(log_file_path, "ab")
    except OSError as error:
        raise DaemonUnavailable(
            f"The build daemon cannot write its log at {log_file_path} ({error.strerror or error}); "
            "set CADGEN_DAEMON_STATE_DIR to a folder this user can write.") from error
    with log_file:
        try:
            process = subprocess.Popen(
                # -P: cadgen's own modules, never the working folder's (STORE.md §9).
                [sys.executable, "-P", "-m", "cadgen.daemon"],
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                # A resident daemon must not retain the project directory of the
                # first model that happened to need it.  Windows refuses to remove a
                # directory while any live process has it as cwd.
                cwd=tempfile.gettempdir(),
                env=env,
                **detach_kwargs(),
            )
        except OSError as error:
            raise DaemonUnavailable(
                f"The build daemon could not be started ({sys.executable} -P -m cadgen.daemon: "
                f"{error.strerror or error}).") from error
    return process


def _send_json(channel: transport.Channel, payload: dict) -> bool:
    try:
        channel.send(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    except (OSError, ValueError):
        return False
    return True


def _recv_json(channel: transport.Channel, timeout: float | None):
    """One frame: a dict, ``_TIMED_OUT``, or None for a closed or unreadable channel."""
    try:
        raw = channel.recv(timeout)
    except (OSError, EOFError):
        return None
    if raw is None:
        return _TIMED_OUT
    if not raw:
        return None
    try:
        message = json.loads(raw.decode("utf-8"))
    except ValueError:
        return None
    return message if isinstance(message, dict) else None


def _job_words(payload: dict) -> tuple[str, list[str]]:
    """``(how the user spelled the command, its arguments)``.

    The ``run`` tool is the decorator's warm handoff for ``python <script>``: its
    argv carries the SCRIPT PATH first and its prog is ``python <name>``, so the
    job reads ``python <name> <args>``. Every other tool is a ``cadgen`` command
    whose argv is exactly what follows it.
    """
    argv = [str(arg) for arg in payload.get("argv") or []]
    tool = str(payload.get("tool") or "")
    prog = str(payload.get("prog") or f"cadgen {tool}")
    return prog, argv[1:] if tool == "run" and argv else argv


def worker_died_message(payload: dict, death: dict) -> str:
    """What the user reads when the warm worker running THEIR job dies.

    Says that the worker died and how (the pool's evidence: exit code or signal),
    names the job, suspects the likely causes, says the job was not retried -- a
    35-minute job silently re-running is worse than the failure -- and names where
    the worker's own last words are: the daemon's log, where a worker's stderr goes.
    Every clause is load-bearing; the tests pin them.
    """
    prog, args = _job_words(payload)
    detail = str(death.get("detail") or "worker closed the connection")
    return (
        f"cadgen-daemon: the warm worker running `{' '.join([prog, *args])}` died mid-job ({detail}) -- "
        "most likely out of memory, or a crash in the geometry kernel. The job was NOT retried; "
        f"what the worker printed as it died is in the build daemon's log: {log_path(daemon_address())}\n"
    )


def daemon_lost_message(payload: dict, reason: str) -> str:
    """What the user reads when the build service itself goes away mid-request (it was
    killed, or crashed): the job is not run again, and the daemon's log may say why."""
    prog, args = _job_words(payload)
    return (f"cadgen-daemon: lost the build service running `{' '.join([prog, *args])}` (it {reason}). "
            f"The job was NOT retried; the build daemon's log may say why: {log_path(daemon_address())}\n")


def _run_request(
    channel: transport.Channel, payload: dict, *, on_stream=None, on_event=None,
    on_artifact_result=None, strict: bool = False, cancelled=None,
) -> int | object:
    """Send one request and stream the response: the job's exit code, or ``_RESTART``.
    A request the daemon lost -- a protocol fault, a silence past ``request_timeout``, a
    worker that died -- fails (exit 1) and says so; it is never run again.

    Stream frames go to the process's own stdout/stderr unless ``on_stream`` is
    given (a nested child build captures them); ``event`` frames — the build
    tree's model transitions — go to ``on_event``."""
    def protocol_failure(reason):
        if strict:
            _say(f"The geometry service {reason}.\n", on_stream)
        elif not died:  # a worker's death has said what happened already
            _say(daemon_lost_message(payload, reason), on_stream)
        return 1

    died = False
    if cancelled is not None and cancelled():
        return 1
    if not _send_json(channel, payload):
        return protocol_failure("disconnected before the request was sent")
    # Applies per frame, not to the whole request: a daemon that is streaming output keeps
    # resetting it, so only genuine silence trips the deadline.
    timeout = request_timeout() or None
    streams = {"stdout": sys.stdout, "stderr": sys.stderr}
    observed_work = False
    deadline = time.monotonic() + timeout if timeout else None
    while True:
        if cancelled is not None and cancelled():
            return 1
        message = _recv_json(channel, .1 if strict else timeout)
        if message is _TIMED_OUT:
            if strict and (deadline is None or time.monotonic() < deadline):
                continue
            return protocol_failure(f"stopped responding for {timeout:.0f} seconds")
        if message is None:
            return protocol_failure("closed the connection before the request finished")
        deadline = time.monotonic() + timeout if timeout else None
        if message.get("restart"):
            if strict and observed_work:
                # An uncertain completed/partial operation cannot replay.
                return protocol_failure("restarted before confirming the completed request")
            return _RESTART
        if "exit" in message:
            return int(message["exit"])
        if "artifactResult" in message:
            if on_artifact_result is None:
                return protocol_failure("sent a geometry result for a build")
            on_artifact_result(message["artifactResult"])
            observed_work = True
            continue
        if "event" in message:
            if strict:
                return protocol_failure("sent a build update instead of a geometry response")
            if on_event is not None and isinstance(message["event"], dict):
                on_event(message["event"])
            continue
        if "workerDied" in message:
            # The worker running this job is gone. This is the one place the loss is
            # explained, and it is never retried -- the caller's job may have run for half
            # an hour. The supervisor follows with the exit frame.
            observed_work = died = True
            if strict:
                _say(f"artifact worker died: {message['workerDied']}; no retry\n", on_stream)
            else:
                _say(worker_died_message(payload, message["workerDied"] or {}), on_stream)
            continue
        data = message.get("data")
        stream = message.get("stream")
        if stream not in streams or not isinstance(data, str):
            return protocol_failure("sent an invalid response")
        observed_work = observed_work or bool(data)
        if on_stream is not None:
            on_stream(data)
            continue
        target = streams[stream]
        target.write(data)
        target.flush()


def watch_jobs(after: str | None = None, *, output: str | None = None, store_root: str | None = None) -> dict | None:
    """Read changes from the running ledger, with at most a one-second wait.

    Like status(), this never starts or restarts a daemon. No model, source
    path, build request or kernel worker is involved in this read-only request.
    """
    try:
        channel = _connect(daemon_address())
    except OSError:
        return None
    try:
        request = {"kind": "status", "jobsOnly": True, "after": after}
        if output is not None and store_root is not None:
            request.update(output=output, storeRoot=store_root)
        if not _send_json(channel, request):
            return None
        message = _recv_json(channel, 2.0)
        if not isinstance(message, dict):
            return None
        payload = message.get("status")
        if (not isinstance(payload, dict) or not isinstance(payload.get("jobsCursor"), str)
                or not isinstance(payload.get("jobs"), list)):
            return None
        return payload
    finally:
        with contextlib.suppress(OSError):
            channel.close()


def prewarm() -> bool:
    """Start this installation's daemon, and with it its warm workers, if none answers.

    The first build of a session otherwise pays for both: spawning the daemon and
    importing build123d in a worker, seconds before any model code runs. Submits
    nothing: a daemon that answers is only asked its status. True once a daemon
    answers; raises :class:`DaemonUnavailable` naming why none could start.
    """
    if os.environ.get("CADGEN_DAEMON_CHILD"):
        return False
    try:
        channel = _connect_or_spawn(daemon_address())
    except (transport.AuthenticationError, transport.AddressUnusable) as error:
        raise DaemonUnavailable(str(error)) from error
    return _ask_status(channel) is not None


# The most a command waits to hand its counts over (``hand_over``): past it, they are not counted.
HAND_OVER_SECONDS = 0.5


def hand_over(counts: dict) -> None:
    """Hand a command's counts to the running daemon, which sends them with its own telemetry
    (``cadgen.daemon.telemetry.counted``): a command sends nothing itself. Like ``status`` it
    never starts a daemon: with none running to take them they are kept for the next process
    that sends (``analytics.spool``). One that does not answer within ``HAND_OVER_SECONDS``,
    or is gone mid-send, may have taken them, so they are not kept twice. The command goes on
    either way. Never raises."""
    try:
        from cadgen.analytics import refused, spool

        if refused():
            return
    except Exception:  # noqa: BLE001 - telemetry never fails the command it counts
        return

    def send() -> None:
        try:
            channel = _connect(daemon_address())
        except Exception:  # noqa: BLE001 - none running, or one that takes no key: kept for the next sender
            spool(counts)
            return
        try:
            _send_json(channel, {"kind": "count", **counts})
        except Exception:  # noqa: BLE001 - one gone mid-send may have read them: not kept again
            pass
        finally:
            with contextlib.suppress(Exception):
                channel.close()

    thread = threading.Thread(target=send, name="cadgen-hand-over", daemon=True)
    thread.start()
    thread.join(HAND_OVER_SECONDS)


def status() -> dict | None:
    """The running daemon's state, or None if there is none.

    Deliberately does NOT spawn one: "is anything warm?" must be answerable without
    changing the answer.
    """
    try:
        channel = _connect(daemon_address())
    except OSError:
        return None
    return _ask_status(channel)


def _ask_status(channel) -> dict | None:
    """Ask a connected daemon its state, then close the channel: its status, or None when
    it does not answer. A daemon answers status before it compares version tokens
    (``server``), so the request carries none."""
    try:
        if not _send_json(channel, {"kind": "status"}):
            return None
        while True:
            message = _recv_json(channel, 10.0)
            if message is _TIMED_OUT or message is None:
                return None
            if "status" in message:
                return message["status"]
    finally:
        with contextlib.suppress(OSError):
            channel.close()
