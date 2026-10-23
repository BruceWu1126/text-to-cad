"""The engine daemon: one per installation, started on demand, gone after an idle hour.

It holds a pool of warm workers, one per model script (``Pool``), and hands each build to its
script's worker: one build at a time per worker, builds of different scripts at once, and at
most ``CADGEN_ENGINE_WORKERS`` workers alive (by default, the machine's cores). A build for a
script with a live worker waits for it while it is busy. One for a script with none starts one
when fewer than the cap are alive; at the cap the least recently used idle worker no page
watches is retired first (its stdin closed: it exits), and when every idle worker is watched the
new one starts past the cap; with every worker busy, the build waits, first come first served,
for the first to come free. Nothing is refused, and nothing is predicted: no admission. A
worker idle ``CADGEN_ENGINE_WORKER_IDLE`` seconds (by default the daemon's own idle time, an
hour) is retired, and so is one over its memory cap after a build (``worker.retirement``).
Before a worker is stopped, every ``pointer`` document of it the server does not hold is taken
from it (``models.Models.drain``): a document outlives its worker. A retired script's next build
is a fresh worker's, a restart.

It also serves documents over HTTP (``serve``), holding each loaded document and its
parts' tessellations in its memory under one budget (``serve.memory_budget``,
``CADGEN_ENGINE_MEMORY_MB``; past it, or on the system's memory-pressure signal, what is held
is evicted: ``serve.Server.trim``), and draws them (``draw``): each served document a
snapshot asks for gets a render process of its own (``render/service.py``), started on its
first picture, sent every version the document is patched to, gone with the daemon or after
the workers' idle time, and held to their memory cap; render processes are not workers, and
do not count toward the cap.

Its only files are the operating system's coordination: a Unix socket and a lock in
``tempfile.gettempdir()/cadgen-engine`` (a socket path past the Unix limit: in ``/tmp/cadgen-<uid>``,
``socket_path``; on Windows, a localhost TCP port and a token, in a port file there). Requests are one JSON object per line on a connection; answers
are JSON lines, the last of which carries ``done``, ``failed`` or ``ok``. Before it, a build
sends its caller its ``{"log"}`` lines and its ``{"progress"}`` frames as its worker sends them
(``progress.py``); the daemon keeps each busy worker's last frame for ``status``, and wakes the
pages waiting on a served document the frame's build writes (``serve.Server.building``).
"""

from __future__ import annotations

import collections
import hashlib
import itertools
import json
import os
import queue
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from cadgen.engine.atomic_replace import replace_atomic
from cadgen.engine.ids import canonical, file_id, is_id

__all__ = ["IDLE_SECONDS", "Endpoint", "Pool", "endpoints", "lock", "lock_path", "main", "socket_path", "state_dir",
           "worker_cap", "worker_idle"]

HTTP_FACTORY = None  # optional application supplied by the enclosing distribution

IDLE_SECONDS = 3600.0
PROTOCOL = 2
WINDOWS = os.name == "nt"
# The retirements ``status`` lists, the latest first.
RETIRED_KEPT = 12
# What a worker's environment must share with a request's for the request to use it.
_RELEVANT = ("PYTHONPATH", "PATH", "VIRTUAL_ENV")


def worker_cap() -> int:
    """The live workers' cap: ``CADGEN_ENGINE_WORKERS``, else the machine's cores."""
    try:
        given = int(os.environ.get("CADGEN_ENGINE_WORKERS", "").strip() or 0)
    except ValueError:
        given = 0
    return given if given > 0 else (os.cpu_count() or 1)


def worker_idle(idle: float) -> float:
    """How long a worker may sit idle before it is retired: ``CADGEN_ENGINE_WORKER_IDLE``
    seconds, else the daemon's own idle time (``idle``)."""
    try:
        given = float(os.environ.get("CADGEN_ENGINE_WORKER_IDLE", "").strip() or -1)
    except ValueError:
        given = -1.0
    return given if given > 0 else idle


def state_dir() -> Path:
    """Where the daemon's socket and lock live: ``tempfile.gettempdir()/cadgen-engine``,
    or ``CADGEN_ENGINE_DIR``."""
    override = os.environ.get("CADGEN_ENGINE_DIR")
    return Path(override) if override else Path(tempfile.gettempdir()) / "cadgen-engine"


def _tag(protocol: int | None = None) -> str:
    """One daemon per installation: its environment, its interpreter's version and the engine it
    runs -- never the name the interpreter was started by (``python``, ``python3``, ``python3.12``
    of one environment are one installation) -- and the protocol it speaks (``PROTOCOL``, else
    ``protocol``: an older one's daemon, which ``stop`` still finds)."""
    prefix = os.path.normcase(os.path.realpath(sys.prefix))
    material = (f"{prefix}|{sys.implementation.cache_tag}|{Path(__file__).resolve().parent}|"
                f"{PROTOCOL if protocol is None else protocol}")
    return hashlib.sha256(material.encode()).hexdigest()[:12]


# A Unix socket's path is at most 104 bytes on macOS and 108 on Linux, its terminator included.
SOCKET_PATH_LIMIT = 100


def socket_path(protocol: int | None = None) -> Path:
    """The daemon's socket: beside its lock, or -- when that path is past ``SOCKET_PATH_LIMIT``
    (a long temporary folder or ``CADGEN_ENGINE_DIR``) -- named by its digest in the user's own
    ``/tmp/cadgen-<uid>`` (``private_folder``). Nothing binds or connects by changing folder: the
    daemon's threads share one working folder with every request they serve."""
    path = state_dir() / f"{_tag(protocol)}.sock"
    if WINDOWS or len(os.fsencode(path)) < SOCKET_PATH_LIMIT:
        return path
    return Path("/tmp") / f"cadgen-{os.getuid()}" / f"{hashlib.sha256(os.fsencode(path)).hexdigest()[:16]}.sock"


def socket_folder_ok(path: Path) -> bool:
    """Whether a socket at ``path`` may be bound or connected: one beside the lock is; one in the
    shared ``/tmp`` only in a folder that is this user's alone (``private_folder``)."""
    return path.parent == state_dir() or private_folder(path.parent)


def private_folder(folder: Path) -> bool:
    """Whether ``folder`` (made when missing) is this user's alone: a real folder, not a link, owned
    by this user and closed to everyone else -- a socket in it can only be this user's daemon."""
    try:
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        found = os.lstat(folder)
    except OSError:
        return False
    import stat

    return stat.S_ISDIR(found.st_mode) and found.st_uid == os.getuid() and not found.st_mode & 0o077


def lock_path(protocol: int | None = None) -> Path:
    return state_dir() / f"{_tag(protocol)}.lock"


def port_path(protocol: int | None = None) -> Path:
    return state_dir() / f"{_tag(protocol)}.port"


@dataclass(frozen=True)
class Endpoint:
    """Where one protocol's daemon of this installation listens and locks: its socket (on Windows,
    its port file) and its lock."""

    protocol: int
    address: Path
    lock: Path


def endpoints() -> list[Endpoint]:
    """Every daemon this installation may have running, this protocol's first and then each older
    one's (``_tag``): the files ``stop`` looks at, in the engine folder and, for a socket past the
    path limit, in ``/tmp/cadgen-<uid>``. A protocol bump never strands the daemon that runs."""
    return [Endpoint(protocol, port_path(protocol) if WINDOWS else socket_path(protocol), lock_path(protocol))
            for protocol in range(PROTOCOL, 0, -1)]


def engine_root() -> str:
    """The folder ``cadgen`` is imported from: a worker this daemon starts imports the same."""
    return str(Path(__file__).resolve().parent.parent.parent)


# Windows' process creation flags, by number: ``subprocess`` names them on Windows alone.
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)


def windowless() -> dict[str, Any]:
    """How the engine starts a process -- a worker, a reader, a render process -- on Windows: with
    a console of its own and no window (``CREATE_NO_WINDOW``); elsewhere as it is. A console program
    started with no console gets a new one, with a window (#597): a window per process, open as long
    as it lives, and closing one ends it."""
    return {"creationflags": CREATE_NO_WINDOW} if os.name == "nt" else {}


def detached() -> dict[str, Any]:
    """How the daemon is started: outliving the command that needed it. On Windows a console of its
    own with no window and a process group of its own, never no console at all (``DETACHED_PROCESS``),
    which would give each console program it starts a window of its own (#597); elsewhere a session
    of its own (``start_new_session`` is POSIX's: Windows takes it as nothing)."""
    if os.name == "nt":
        return {"creationflags": CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


# --- workers -----------------------------------------------------------------------------


class WorkerGone(Exception):
    pass


class Worker:
    """One warm worker process and the script it builds.

    The worker's protocol pipe (its original stdout) carries frames: a JSON object on one
    line and, when the object has ``bytes``, exactly that many bytes after the line. A
    reader thread takes every frame as it comes: a build's ``log`` lines go to that
    build's caller, and so do its ``progress`` frames (``progress.py``), the last of which it
    keeps (``progress``, what ``status`` shows) and hands to ``on_progress``; each served
    document it saves -- a ``patch`` frame, sent the moment
    the xbf is in place, before the exports -- to ``on_patch``, its ``done``/``failed``
    answer to the waiting ``build`` call, and its closing ``settled`` to ``on_settled``,
    which lets the server look at those files again. ``where`` asks the worker what made
one element of a document it built (``worker``): a request of its own, answered on the
same pipe, even while a build runs. Requests reach the worker on its original stdin, which it
reads on a handle of its own; its standard input is the null device, so nothing a model loads
or reads ever touches the request pipe (on Windows a DLL's C runtime would wait on it)."""

    def __init__(self, script: str, environment: dict[str, str],
                 on_patch: Callable[[dict[str, Any], bytes, float], None] = lambda *_args: None,
                 on_settled: Callable[..., None] = lambda _worker, _every=False: None, cwd: str | None = None,
                 on_progress: Callable[["Worker", dict[str, Any]], None] = lambda _worker, _frame: None) -> None:
        self.script = script
        self.environment = environment
        env = dict(environment)
        path = env.get("PYTHONPATH", "")
        root = engine_root()
        if root not in path.split(os.pathsep):
            env["PYTHONPATH"] = os.pathsep.join(item for item in (root, path) if item)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["CADGEN_ENGINE_WORKER"] = "1"
        self.process = subprocess.Popen([sys.executable, "-P", "-m", "cadgen.engine.worker"], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                                        cwd=cwd or str(Path(script).parent), **windowless())
        self.lock = threading.Lock()
        self.writing = threading.Lock()  # one request line at a time on the worker's stdin
        self.asked: dict[str, "queue.Queue[Any]"] = {}
        self.questions = itertools.count(1)
        self.sink: Callable[[dict[str, Any]], None] | None = None
        self.on_patch = on_patch
        self.on_settled = on_settled
        self.on_progress = on_progress
        # The running request's last progress frame, and every file its frames named.
        self.progress: dict[str, Any] | None = None
        self.touched: set[str] = set()
        self.document_read = False
        self.answers: "queue.Queue[dict[str, Any] | None]" = queue.Queue()
        self.started = time.time()
        self.builds = 0
        # Its place in the pool (``Pool``): its key, whether a request holds it, since when, and
        # when it was last let go; its resident memory after its last build, in MiB.
        self.key = os.path.normcase(script)
        self.leased = False
        self.since = 0.0
        self.used = time.monotonic()
        self.rss: float | None = None
        # Over its memory cap after a build (``worker.retirement``): it exits, and is never asked again.
        self.retired = False
        self.held: list[Any] = []  # what the daemon holds for this worker's build until it settles
        threading.Thread(target=self._stderr, daemon=True).start()
        threading.Thread(target=self._reader, daemon=True).start()
        ready = self.answers.get()
        if ready is None or "ready" not in ready:
            raise WorkerGone(f"the worker for {script} did not start")
        self.pid = ready["ready"]

    def _stderr(self) -> None:
        assert self.process.stderr is not None
        for line in iter(self.process.stderr.readline, b""):
            sink = self.sink
            if sink is not None:
                sink({"log": line.decode("utf-8", "replace").rstrip("\n"), "stream": "model"})

    def _reader(self) -> None:
        stream = self.process.stdout
        assert stream is not None
        try:
            for line in iter(stream.readline, b""):
                line = line.strip()
                if not line.startswith(b"{"):
                    continue
                message = json.loads(line)
                arrived = time.time()
                size = int(message.get("bytes") or 0)
                payload = _read_exactly(stream, size) if size else b""
                if "log" in message:
                    sink = self.sink
                    if sink is not None:
                        sink({"log": message["log"]})
                elif "progress" in message:
                    frame = self.progress = message["progress"]
                    self.touched.update(frame.get("outputs") or ())
                    sink = self.sink
                    if sink is not None:
                        sink({"progress": frame})
                    try:
                        self.on_progress(self, frame)
                    except Exception:  # noqa: BLE001 - where a build is never fails it
                        pass
                elif "patch" in message:
                    try:
                        self.on_patch(message["patch"], payload, arrived)
                    except Exception:  # noqa: BLE001 - a document that cannot be patched is read from its file
                        pass
                    try:
                        # Held (or not): the build goes on to its derived files.
                        self._write({"op": "patched", "path": message["patch"].get("path")})
                    except (OSError, ValueError):
                        pass
                elif "settled" in message:
                    if message.get("op", "build") == "build":
                        self.on_settled(self)
                elif "collected" in message:
                    pass  # the worker collected while idle (``worker.py``): nothing waits on it
                elif "where" in message:
                    waiting = self.asked.pop(str(message.get("id")), None)
                    if waiting is not None:
                        waiting.put(message["where"])
                else:
                    if payload:
                        message["payload"] = payload
                    self.answers.put(message)
        except (OSError, ValueError, EOFError):
            pass
        finally:
            self.answers.put(None)  # gone: every build it held settles
            for waiting in list(self.asked.values()):
                waiting.put(None)
            self.on_settled(self, True)

    def alive(self) -> bool:
        return self.process.poll() is None

    def _write(self, request: dict[str, Any], payload: bytes = b"") -> None:
        """One request, its ``payload`` (``bytes`` of them) after its line."""
        assert self.process.stdin is not None
        if payload:
            request = {**request, "bytes": len(payload)}
        with self.writing:
            self.process.stdin.write((json.dumps(request) + "\n").encode())
            if payload:
                self.process.stdin.write(payload)
            self.process.stdin.flush()

    def where(self, doc: str, key: str, kind: str, ordinal: int, timeout: float = 10.0) -> dict[str, Any] | None:
        """What made one element of a document (``doc``, its id) this worker built, as its last
        build of it named the element (``build.made_by``); None when it did not build it, or is gone."""
        ident = f"where{next(self.questions)}"
        waiting: "queue.Queue[Any]" = queue.Queue(1)
        self.asked[ident] = waiting
        try:
            self._write({"id": ident, "op": "where", "doc": doc, "key": key, "kind": kind, "ordinal": ordinal})
            return waiting.get(timeout=timeout)
        except (OSError, ValueError, queue.Empty):
            return None
        finally:
            self.asked.pop(ident, None)

    def build(self, request: dict[str, Any], send: Callable[[dict[str, Any]], None],
              payload: bytes = b"") -> dict[str, Any]:
        with self.lock:
            self.sink = send
            self.document_read = bool(request.get("expected"))
            self.progress, self.touched = None, set()
            try:
                self._write(request, payload)
                message = self.answers.get()
                if message is None:
                    code = self.process.wait()
                    raise WorkerGone(f"the worker for {Path(self.script).name} exited ({code}) during the build")
                self.builds += 1
                self.rss = message.pop("rssMb", self.rss)
                done = message.get("done")
                if isinstance(done, dict):
                    reports = done.get("reports") or []
                else:
                    reports = done if isinstance(done, list) else []
                if any(isinstance(report, dict) and report.get("retired") for report in reports):
                    self.retired = True
                return message
            except (BrokenPipeError, OSError) as error:
                raise WorkerGone(str(error)) from error
            finally:
                self.sink = None
                self.document_read = False

    def stop(self) -> None:
        try:
            if self.process.stdin is not None:
                self.process.stdin.close()
            self.process.wait(timeout=5)
        except Exception:  # noqa: BLE001
            self.process.kill()


def refused(error: Exception) -> dict[str, Any]:
    """A refusal as the daemon answers it: its words, its type (``worker.refusal``), and for a
    document the engine does not hold (``serve.NotHeld``) its state and the command that builds it
    again, so the caller raises it as that type (``api._failed``) and the CLI exits 2."""
    from cadgen.engine.serve import NotHeld
    from cadgen.engine.worker import refusal

    found: dict[str, Any] = {"failed": str(error), **refusal(error)}
    if isinstance(error, NotHeld):
        found.update(state=error.state, rebuild=error.rebuild)
    return found


def _written(report: dict[str, Any]) -> list[str]:
    """Every file a build's report names: its model's, and each child model's
    (``child_models``: the models its run ran, each of which wrote its own)."""
    return [*(report.get("files") or {}),
            *(path for child in (report.get("child_models") or {}).values() for path in child.get("files") or {})]


def _read_exactly(stream: Any, size: int) -> bytes:
    chunks = []
    while size:
        chunk = stream.read(size)
        if not chunk:
            raise EOFError("the worker closed its pipe inside a frame")
        chunks.append(chunk)
        size -= len(chunk)
    return b"".join(chunks)


# --- the pool -----------------------------------------------------------------------------


@dataclass(eq=False)
class _Ticket:
    """A request waiting for a worker: granted the live worker of its key (``worker``), or a
    slot to start one in (``start``), retiring ``victim`` first."""

    key: str
    environment: dict[str, str]
    cwd: str | None
    worker: Any = None
    start: bool = False
    victim: Any = None

    @property
    def granted(self) -> bool:
        return self.worker is not None or self.start


def _shared(environment: dict[str, str]) -> dict[str, str]:
    return {name: environment.get(name, "") for name in _RELEVANT}


class Pool:
    """The daemon's live workers, one per key (a script's path, ``documents``),
    at most ``cap`` of them but for watched ones (see the module): ``lease`` a key's worker for one
    request, and ``release`` it after. Requests are granted first come first served: one whose key
    has a live worker takes it when it is free; one that needs a worker of its own takes a slot
    under the cap, else the slot of the least recently used idle worker no page watches -- one no
    request waits for, if there is one -- which is retired. When every idle worker is watched, the
    new worker starts past the cap: the count cap never retires a watched worker (connected means
    warm); its memory cap may. With every worker busy the request waits, and so does every request
    that needs a slot after it. A worker idle ``idle`` seconds is retired (``retire_idle``), as is one that is
    gone or over its memory cap when let go. ``start(key, environment, cwd)`` starts a worker;
    ``drain(worker)`` is asked of every worker retired, out of the pool and before it is stopped
    (the daemon's: its documents taken from it, ``models.Models.drain``)."""

    def __init__(self, cap: int, idle: float, start: Callable[[str, dict[str, str], str | None], Any],
                 connected: Callable[[Any], bool] = lambda worker: False,
                 drain: Callable[[Any], None] = lambda worker: None) -> None:
        self.cap = max(1, int(cap))
        self.idle = idle
        self.start = start
        self.connected = connected
        self.drain = drain
        self.lock = threading.Condition()
        self.live: dict[str, Any] = {}  # by key
        self.starting: set[str] = set()  # the keys whose worker is being started, each holding a slot
        self.queue: list[_Ticket] = []
        self.retired: collections.deque = collections.deque(maxlen=RETIRED_KEPT)

    def lease(self, script: str, environment: dict[str, str], cwd: str | None = None) -> Any:
        """The worker of ``script`` for one request, waiting its turn (see the class); started
        when it has none. ``release`` it after."""
        ticket = _Ticket(os.path.normcase(script), environment, cwd)
        with self.lock:
            self.queue.append(ticket)
            self._grant()
            while not ticket.granted:
                self.lock.wait()
        if ticket.worker is not None:
            return ticket.worker
        if ticket.victim is not None:
            self._stop(ticket.victim)  # the worker whose slot this is exits before the new one starts
        try:
            worker = self.start(script, environment, cwd)
        except BaseException:
            with self.lock:
                self.starting.discard(ticket.key)
                self._grant()
            raise
        with self.lock:
            self.starting.discard(ticket.key)
            worker.leased, worker.since = True, time.monotonic()
            self.live[ticket.key] = worker
        return worker

    def release(self, worker: Any) -> None:
        """A request is done with its worker: it is free again, or retired when it is gone or
        over its memory cap."""
        gone = False
        with self.lock:
            worker.leased, worker.used = False, time.monotonic()
            if worker.retired or not worker.alive():
                gone = self.live.get(worker.key) is worker
                if gone:
                    del self.live[worker.key]
                    self._note(worker, "RSS" if worker.retired else "exited")
            self._grant()
        if gone:
            self._stop(worker)

    def _stop(self, worker: Any) -> None:
        """A worker retired: drained (``drain``), then stopped. A drain that fails stops it all the same."""
        try:
            self.drain(worker)
        except Exception:  # noqa: BLE001 - what the drain could not take is evicted
            pass
        worker.stop()

    def retire_idle(self, now: float | None = None) -> list[Any]:
        """Retire workers idle ``idle`` seconds with no connected document; the retired."""
        now = time.monotonic() if now is None else now
        with self.lock:
            gone = [worker for worker in self.live.values()
                    if not worker.leased and now - worker.used >= self.idle and not self.connected(worker)]
            for worker in gone:
                del self.live[worker.key]
                self._note(worker, "idle")
            if gone:
                self._grant()
        for worker in gone:
            self._stop(worker)
        return gone

    def holds(self, worker: Any) -> bool:
        """Whether ``worker`` is live in the pool: neither retired nor gone."""
        with self.lock:
            return self.live.get(worker.key) is worker and not worker.retired and worker.alive()

    def workers(self) -> list[Any]:
        with self.lock:
            return list(self.live.values())

    def waiting(self) -> int:
        with self.lock:
            return sum(not ticket.granted for ticket in self.queue)

    def stop(self) -> None:
        with self.lock:
            every = list(self.live.values())
            self.live.clear()
        for worker in every:
            worker.stop()

    def _note(self, worker: Any, reason: str) -> None:
        self.retired.appendleft({"script": worker.script, "pid": getattr(worker, "pid", None),
                                 "builds": getattr(worker, "builds", 0), "reason": reason, "at": time.time()})

    def _grant(self) -> None:
        """Grant what the waiting requests can have, in the order they came (call with the lock
        held): see the class."""
        for key, worker in list(self.live.items()):
            if not worker.leased and not worker.alive():
                del self.live[key]
                self._note(worker, "exited")
        wanted = {ticket.key for ticket in self.queue if ticket.key in self.live}
        taken: set[int] = set()  # the workers this pass granted or retired
        blocked = False  # a request before found no slot: no later one takes one
        granted = False
        for ticket in self.queue:
            worker = self.live.get(ticket.key)
            if worker is not None and _shared(worker.environment) == _shared(ticket.environment):
                if not worker.leased and id(worker) not in taken:
                    worker.leased, worker.since = True, time.monotonic()
                    ticket.worker = worker
                    taken.add(id(worker))
                    granted = True
                continue
            if ticket.key in self.starting:
                continue  # its key's worker is being started: it waits for that one
            if worker is not None:
                # Its script's worker, of another environment: replaced in its slot once it is free.
                if not worker.leased and id(worker) not in taken:
                    del self.live[ticket.key]
                    self._note(worker, "environment")
                    self._slot(ticket, worker)
                    taken.add(id(worker))
                    granted = True
                continue
            if blocked:
                continue
            if len(self.live) + len(self.starting) < self.cap:
                self._slot(ticket, None)
                granted = True
                continue
            idle = [held for held in self.live.values() if not held.leased and id(held) not in taken]
            if not idle:
                blocked = True
                continue
            # Connected means warm: a worker whose document a page watches is never the count
            # cap's victim. With only watched idle workers the cap is exceeded instead, so a
            # build never waits on an open page and never costs one its warm worker.
            unwatched = [held for held in idle if not self.connected(held)]
            if not unwatched:
                self._slot(ticket, None)
                granted = True
                continue
            victim = min(unwatched, key=lambda held: (held.key in wanted, held.used))
            del self.live[victim.key]
            self._note(victim, "LRU")
            self._slot(ticket, victim)
            taken.add(id(victim))
            granted = True
        if granted:
            self.queue = [ticket for ticket in self.queue if not ticket.granted]
            self.lock.notify_all()

    def _slot(self, ticket: _Ticket, victim: Any) -> None:
        ticket.start, ticket.victim = True, victim
        self.starting.add(ticket.key)


# --- the daemon ---------------------------------------------------------------------------


class Daemon:
    def __init__(self, idle: float, cap: int | None = None, retire_after: float | None = None) -> None:
        self.idle = idle
        self.pool = Pool(cap or worker_cap(), retire_after if retire_after is not None else worker_idle(idle),
                         self._start, self.connected, lambda worker: self.models.drain(worker))
        self.workers_lock = threading.Lock()  # each worker's ``held``
        self.born = time.time()
        self.last = time.monotonic()
        self.busy = 0
        self.busy_lock = threading.Lock()
        self.stopping = threading.Event()
        self.server = None
        self.token = secrets.token_hex(16) if WINDOWS else ""
        # Per document a build made (its id: each xbf's ``file_id``, each model's ``model_id``),
        # the worker that built it last: the one that holds its provenance (``provenance``).
        self.builders: dict[str, Worker] = {}
        self.renders = None  # the served documents' render processes (``render.service.Renders``)
        self.serving = threading.Lock()
        from cadgen.engine.models import Models

        self.models = Models(self)

    def touch(self) -> None:
        self.last = time.monotonic()

    def connected(self, worker: Worker) -> bool:
        """A page watching any document this worker built keeps its memo warm."""
        server = self.server
        return server is not None and any(self.builders.get(ident) is worker for ident in server.watched())

    def _start(self, script: str, environment: dict[str, str], cwd: str | None) -> Worker:
        return Worker(script, environment, self.patched, self.settled, cwd=cwd, on_progress=self.progressed)

    def progressed(self, _worker: Worker, frame: dict[str, Any]) -> None:
        """A build's progress frame: the pages waiting on a served document it writes are woken
        with it (``serve.Server.building``)."""
        server = self.server
        if server is not None and not _worker.document_read:
            outputs = [*(frame.get("outputs") or []), *self.models.paths(_worker, frame.get("model"))]
            if outputs:
                server.building({**frame, "outputs": outputs})

    def _built(self, worker: Worker, answer: dict[str, Any]) -> None:
        """A request of ``worker`` answered: each served document its build wrote is built no
        longer, and one whose model failed says what failed, and where (``serve.Server.built``)."""
        self.models.register(worker, answer)
        server = self.server
        if server is None:
            return
        outcomes: dict[str, Any] = {path: None for path in worker.touched}
        done = answer.get("done")
        reports = done.get("reports") if isinstance(done, dict) else done
        for report in reports if isinstance(reports, list) else []:
            frame = report.get("progress") or {}
            failed = None if report.get("ok") else {"model": report.get("model"), "error": report.get("error"),
                                                    "progress": frame}
            for path in [*(frame.get("outputs") or ()), *(report.get("files") or {}),
                         *self.models.paths(worker, report.get("model"))]:
                outcomes[path] = failed
        if "failed" in answer:
            frame = worker.progress or {}
            outcomes = {path: {"model": frame.get("model"), "error": answer["failed"], "progress": frame}
                        for path in [*outcomes, *self.models.paths(worker, frame.get("model"))]}
        if outcomes:
            server.built(outcomes)

    @staticmethod
    def _where(error: Exception, worker: Worker | None) -> str:
        """A worker's failure, and where its build was when it failed."""
        from cadgen.engine.progress import describe

        frame = worker.progress if worker is not None else None
        return f"{error}, at {frame.get('model')}: {describe(frame)}" if frame else str(error)

    def lease(self, script: str, environment: dict[str, str], cwd: str | None = None) -> Worker:
        """The warm worker of a script (by its path) or of the documents no worker built
        (``documents``), for one request: waiting its turn in the
        pool, started when none is alive (``Pool``). ``release`` it after."""
        return self.pool.lease(script, environment, cwd)

    def release(self, worker: Worker) -> None:
        self.pool.release(worker)

    def provenance(self, ident: str, key: str, kind: str, ordinal: int) -> dict[str, Any] | None:
        """What made one element of a served document (by its id), asked of the worker that built
        it (``Worker.where``): None for a document no live worker built -- one served cold."""
        worker = self.builders.get(ident)
        if worker is None or not self.pool.holds(worker):
            return None
        return worker.where(ident, key, kind, ordinal)

    def patched(self, header: dict[str, Any], payload: bytes, arrived: float) -> None:
        """A worker's document for a served path, put in place of the one served, and sent on to
        its render process when it has one."""
        server = self.server
        if server is not None:
            server.patch(header, payload, arrived)
            if self.renders is not None and header.get("path"):
                self.renders.patched(file_id(header["path"]))

    def serve(self, request: dict[str, Any]) -> Any:
        """The daemon's server, started on the first request that needs one."""
        from cadgen.engine.serve import MESHES, Server, memory_budget

        with self.serving:
            if self.server is None:
                port, host = int(request.get("port") or 0), request.get("host") or "127.0.0.1"
                try:
                    self.server = Server(port=port, three=request.get("three"), touch=self.touch,
                                         provenance=self.provenance, cache=MESHES, isolated=True,
                                         host=host, http_factory=HTTP_FACTORY, budget=memory_budget())
                except OSError as error:
                    raise ValueError(f"cannot listen on {host}:{port}: {error.strerror or error}"
                                     + ("; pass another --port" if port else "")) from None
                self.server.lost = self.models.rebuild
                self.server.start()
            elif ((request.get("port") and int(request["port"]) != self.server.port)
                  or (request.get("host") and request["host"] != self.server.http.server_address[0])):
                raise ValueError("the installation's engine already serves another host/port; stop it before changing its bind")
            return self.server

    def settled(self, worker: Worker, every: bool = False) -> None:
        """A worker is done with a build's documents (``every``: it is gone): their files
        may be looked at again."""
        with self.workers_lock:
            held = [paths for paths in worker.held] if every else worker.held[:1]
            del worker.held[:len(held)]
        if self.server is not None:
            for paths in held:
                self.server.release(paths)

    def document(self, ident: str) -> tuple[dict[str, Any], bytes]:
        """What the document ``ident`` is while this engine holds it, as an export reads it, and the
        bytes it carries: a model a live worker holds (``{"script", "function", "held"}``, read from
        that worker, never built); one whose worker is gone but whose whole document the server
        holds (``{"document", "name"}``, its xbf bytes after it: the documents worker reads them as
        an on-disk xbf, and no worker of its script starts); a file this engine serves or served
        (``{"path"}``). Refused (``Models.not_held``), naming its state and the command that
        builds it again, for any other id: the engine runs nothing."""
        self.models.prune()
        record = self.models.held(ident)
        server = self.server
        if record is not None:
            worker = record["worker"]
            if worker is not None and self.pool.holds(worker):
                return {"script": record["source"]["script"], "function": record["source"]["name"], "held": ident}, b""
            held = self.held_document(ident)
            if held is not None:
                return held
        if server is not None and ident in server.served and not isinstance(server.served[ident].path, str):
            return {"path": str(server.served[ident].path)}, b""
        known = server.file_of(ident) if server is not None else None
        if known is not None:
            return {"path": known}, b""
        raise self.models.not_held(ident)

    def held_document(self, ident: str) -> tuple[dict[str, Any], bytes] | None:
        """A model document the server holds whole and no worker does, as an export reads it: its
        source and its xbf bytes. ``ValueError`` for one held for viewing alone (its worker exited
        before it was drained), naming the command that builds it again. None when it is not held."""
        server = self.server
        if server is None:
            return None
        with server.load_lock:
            doc = server.served.get(ident)
        if doc is None:
            return None
        if doc.xbf is None:
            with self.models.lock:
                record = self.models.records.get(ident)
            name = record["source"]["name"] if record else ident
            raise ValueError(f"{name} ({ident}) is held for viewing only: its worker exited before the engine "
                             f"took it whole; run `{self.models.command(ident)}` to build it again")
        source = {"document": ident, "name": doc.structure.get("name") or ident}
        if doc.animations:
            source["animations"] = doc.animations  # the build's, which a GLB of it bakes
        return source, doc.xbf

    def _routed(self, request: dict[str, Any]) -> tuple[str, str | None]:
        """The worker a request goes to (its key) and the folder that worker starts in."""
        source = request.get("source") or {}
        if "document" in source:
            return "documents", request.get("cwd")  # a held document's bytes: no worker of its script
        if "path" in source:
            builder = self.builders.get(file_id(source["path"]))
            if builder is not None and self.pool.holds(builder):
                return builder.script, None
            return "documents", request.get("cwd")
        # A script's worker is keyed by its canonical path: a process key, not a document's id.
        return canonical(request.get("script") or source["script"]), None

    def _call(self, request: dict[str, Any], order: dict[str, Any], send: Callable[[dict[str, Any]], None],
              payload: bytes = b"") -> None:
        """An export or a result asked of the worker the request routes to (with ``payload``, the
        bytes its source carries, after its line); its answer sent."""
        with self.busy_lock:
            self.busy += 1
        try:
            key, cwd = self._routed(request)
            for attempt in (1, 2):
                worker = None
                try:
                    environment = request.get("env") or {}
                    if request.get("expected") or (request.get("source") or {}).get("held"):
                        # Viewer exports and a document named by its id borrow the held document's
                        # worker, including its environment: another caller's environment must not
                        # evict it (a different one starts a fresh worker, which holds nothing).
                        with self.pool.lock:
                            held = self.pool.live.get(os.path.normcase(key))
                            environment = held.environment if held else dict(os.environ)
                        if held is None and "script" in request["source"]:
                            named = request["source"].get("held")
                            send(refused(self.models.not_held(named)) if isinstance(named, str) else
                                 {"failed": "The viewed model is no longer held. Reopen it before exporting."})
                            return
                    worker = self.lease(key, environment, cwd)
                    expected = request.get("expected") or {}
                    if "modelRevision" in expected and "document" not in request["source"]:
                        # The lease excludes a rebuild: validate the generation here,
                        # including a rebuild that produced identical geometry.
                        with self.models.lock:
                            model = self.models.records.get(expected.get("id") or "")
                            matches = (model is not None and model["worker"] is worker
                                       and f"{model['version']}-0" == expected["modelRevision"])
                        if not matches:
                            send({"failed": "The source changed. Open Export again to use the displayed revision."})
                            return
                    answer = worker.build(order, send, payload)
                    returned = answer.pop("payload", b"")
                    if returned:
                        import base64

                        answer["payload"] = base64.b64encode(returned).decode("ascii")
                    done = answer.get("done")
                    # An export from a script built it first: that worker holds the xbf now.
                    for row in (done.get("reports") or []) if isinstance(done, dict) else []:
                        for path in _written(row):
                            if path.endswith(".xbf"):
                                self.builders[file_id(path)] = worker
                    if not request.get("expected"):
                        self._built(worker, answer)
                    send(answer)
                    return
                except WorkerGone as error:
                    if attempt == 2 or "during the build" in str(error):
                        failed = {"failed": self._where(error, worker)}
                        if worker is not None and not request.get("expected"):
                            self._built(worker, failed)
                        send(failed)
                        return
                finally:
                    if worker is not None:
                        self.release(worker)
        finally:
            with self.busy_lock:
                self.busy -= 1
            self.touch()

    def handle(self, request: dict[str, Any], send: Callable[[dict[str, Any]], None]) -> None:
        self.touch()
        op = request.get("op")
        if op == "models":
            send({"ok": True, "models": self.models.listing()})
            return
        if op == "model_info":
            self.models.prune()
            ident = str(request.get("id"))
            with self.models.lock:
                record = self.models.records.get(ident) or self.models.expired.get(ident)
            if record is None:
                send(refused(self.models.not_held(ident)))
            else:
                model = {key: record.get(key) for key in ("id", "source", "program", "builtAt", "version", "files",
                                                          "store")}
                model["state"] = self.models.state(ident)
                lost = self.models.rebuild(ident)
                if lost is not None:
                    model["rebuild"] = lost["rebuild"]
                send({"model": model})
            return
        if op == "document":
            # What an id names while this engine holds it (``document``), asked without building.
            try:
                send({"ok": True, "document": self.document(str(request.get("id")))[0]})
            except ValueError as error:
                send(refused(error))
            return
        if op == "state":
            # What the engine knows of a document by its id: held or not, and what builds it again.
            ident = str(request.get("id"))
            self.models.prune()
            lost = self.models.rebuild(ident)
            send({"ok": True, "state": self.models.state(ident) if lost is None else lost["state"],
                  **({"rebuild": lost["rebuild"], "error": lost["error"]} if lost else {})})
            return
        payload = b""
        if op == "export" and "id" in (request.get("source") or {}):
            ident = str(request["source"]["id"])
            try:
                source, payload = self.document(ident)
            except ValueError as error:
                send(refused(error))
                return
            expected = request.get("expected") or {}
            if "document" in source and "modelRevision" in expected:
                # The viewer's export of a model whose worker is gone reads the whole document the
                # server holds, at the revision the page shows: checked here, as no worker holds it.
                with self.models.lock:
                    record = self.models.records.get(ident)
                served = self.server.served.get(ident) if self.server is not None else None
                if (record is None or served is None or f"{record['version']}-0" != expected["modelRevision"]
                        or served.structure.get("exportRevision") != expected.get("digest")):
                    send({"failed": "The source changed. Open Export again to use the displayed revision."})
                    return
            request = {**request, "source": source}
        if op == "export":
            order = {"id": 1, "op": "export", "source": request["source"], "destinations": request["destinations"],
                     "cwd": request.get("cwd"),
                     "draw": request.get("draw"), "draw_still": request.get("draw_still"),
                     "expected": request.get("expected"), "inspect": request.get("inspect"), "original": request.get("original")}
            return self._call(request, order, send, payload)
        if op == "result":
            order = {"id": 1, "op": "result", "script": request["script"], "name": request["name"],
                     "cwd": request.get("cwd")}
            return self._call(request, order, send)
        if op == "build":
            with self.busy_lock:
                self.busy += 1
            try:
                script = request["script"]
                key, cwd = self._routed(request)
                for attempt in (1, 2):
                    worker = None
                    try:
                        worker = self.lease(key, request.get("env") or {}, cwd)
                        fresh = worker.builds == 0
                        order = {"id": 1, "op": "build", "script": script, "functions": request.get("functions"),
                                 "cwd": request.get("cwd"), "verify": bool(request.get("verify")),
                                 "profile": bool(request.get("profile")), "acknowledged": True,
                                 "run": request.get("run")}
                        server = self.server
                        if server is not None:
                            # The worker sends each served document it builds back as a patch,
                            # with the meshes and shapes the server does not hold; until it has,
                            # the server does not read those files again.
                            order.update(served=server.versions(), held=server.cache.keys(), shapes=server.shape_keys(),
                                         served_keys=server.served_keys())
                            worker.held.append(server.hold(list(order["served"])))
                        answer = worker.build(order, send)
                        for report in answer.get("done") or []:
                            # Its children's too: the worker that built a child's xbf holds its trace.
                            for path in _written(report):
                                if path.endswith(".xbf"):
                                    self.builders[file_id(path)] = worker
                        answer["worker"] = {"pid": worker.pid, "fresh": fresh}
                        # The program that asked: what a document it built names to build it again.
                        answer["program"] = request.get("program")
                        self._built(worker, answer)
                        send(answer)
                        return
                    except WorkerGone as error:
                        if attempt == 2 or "during the build" in str(error):
                            failed = {"failed": self._where(error, worker)}
                            if worker is not None:
                                self._built(worker, failed)
                            send(failed)
                            return
                    finally:
                        if worker is not None:
                            self.release(worker)
            finally:
                with self.busy_lock:
                    self.busy -= 1
                self.touch()
        elif op == "serve":
            server = self.serve(request)
            named = str(request["path"])
            try:
                if not is_id(named):
                    ident = server.add(Path(named))
                elif self.models.held(named) is not None:
                    server, ident = self.models.serve(named)
                else:
                    found, _payload = self.document(named)  # a file this engine knows by its id, or refused
                    ident = server.add(Path(found["path"]))
            except Exception as error:  # noqa: BLE001
                from cadgen.engine.serve import NotHeld

                # A document id the engine does not hold is a refusal: its state and the command
                # that builds it again travel with it, and the caller raises it as its type.
                send(refused(error) if isinstance(error, NotHeld) else {"failed": f"cannot serve {named}: {error}"})
                return
            app = getattr(server.http, "app", None)
            token = app.token if app is not None else ""
            send({"ok": True, "url": self.models.url(server, ident), "doc": ident, "token": token,
                  "port": server.port})
        elif op in ("viewer", "http"):
            server = self.serve(request)
            application = getattr(server.http, "app", None)
            if application is None:
                send({"failed": "this engine has no application host"})
            else:
                send(application.launch(request) if op == "viewer" else application.endpoint())
        elif op == "draw":
            self.draw(request, send)
        elif op == "warm":
            # The viewer at its launch: the server, its meshing processes and a STEP reader started
            # while nobody waits, so a page's first document waits on none of them.
            self.serve(request).warm()
            send({"ok": True})
        elif op == "status":
            send(self.status())
        elif op == "stop":
            send({"ok": True})
            self.stopping.set()
        else:
            send({"failed": f"unknown request {op!r}"})

    def draw(self, request: dict[str, Any], send: Callable[[dict[str, Any]], None]) -> None:
        """A picture of a document (``{"op": "draw", "path", "still"}``, a ``snapshot.Still``):
        served, meshed by the daemon's pool, drawn by its render process; answered with the PNG
        (base64) and what was drawn, or refused by name."""
        from cadgen.engine.render.service import Refused, Renders, answer_png

        with self.busy_lock:
            self.busy += 1
        try:
            server = self.serve(request)
            with self.serving:
                if self.renders is None:
                    self.renders = Renders(server)
            try:
                data, report = self.renders.draw(Path(request["path"]), request["still"])
            except Refused as error:
                send({"refused": str(error)})
                return
            except Exception as error:  # noqa: BLE001 - a picture that could not be drawn is the answer
                send({"failed": f"{type(error).__name__}: {error}"})
                return
            send({"ok": True, "report": report, "png": answer_png(data)})
        finally:
            with self.busy_lock:
                self.busy -= 1
            self.touch()

    def status(self) -> dict[str, Any]:
        """The daemon, its workers (each its script, its builds, its memory after its last build
        in MiB, how long it has been busy or idle, and a busy one's last progress frame), the
        requests waiting for one, the last retirements, and what it serves and draws."""
        from cadgen.engine.worker import rss_cap_mb

        now = time.monotonic()
        workers = [{"script": w.script, "pid": w.pid, "builds": w.builds, "alive": w.alive() and not w.retired,
                    "busy": w.leased, "rssMb": None if w.rss is None else round(w.rss),
                    "seconds": round(now - (w.since if w.leased else w.used), 1),
                    "progress": w.progress if w.leased else None} for w in self.pool.workers()]
        return {"ok": True, "pid": os.getpid(), "up": round(time.time() - self.born, 1), "workers": workers,
                "cap": self.pool.cap, "waiting": self.pool.waiting(), "idleTimeout": self.pool.idle,
                "rssCapMb": rss_cap_mb(), "retired": list(self.pool.retired),
                "idle": round(now - self.last, 1),
                "serving": None if self.server is None else {"url": self.server.url,
                                                             "documents": self.server.listing(every=True),
                                                             "memory": self.server.memory()},
                "models": self.models.listing(every=True),
                "renders": [] if self.renders is None else self.renders.status(),
                "sessions": (self.server.http.app.sessions() if self.server is not None
                             and hasattr(self.server.http, "app") else [])}

    def watch(self) -> None:
        period = min(30.0, max(min(self.idle, self.pool.idle) / 10, 0.2))
        while not self.stopping.wait(period):
            self.pool.retire_idle()
            self.models.prune()
            if self.renders is not None:
                self.renders.retire_idle(self.pool.idle)
            if (self.busy == 0 and time.monotonic() - self.last > self.idle
                    and not (self.server is not None and (self.server.watched() or self.server.clients_active()))):
                self.stopping.set()

    def shutdown(self) -> None:
        self.pool.stop()
        if self.renders is not None:
            self.renders.stop()
        if self.server is not None:
            self.server.stop()


def _serve_connection(daemon: Daemon, connection: socket.socket) -> None:
    with connection:
        stream = connection.makefile("rwb")
        try:
            line = stream.readline()
            if not line:
                return
            request = json.loads(line)
            if WINDOWS and request.get("token") != daemon.token:
                return
            lock = threading.Lock()

            def send(message: dict[str, Any]) -> None:
                with lock:
                    stream.write((json.dumps(message, separators=(",", ":")) + "\n").encode())
                    stream.flush()

            try:
                daemon.handle(request, send)
            except (OSError, ValueError) as error:
                send({"failed": str(error)})
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass


def _bind() -> socket.socket:
    if WINDOWS:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        return server
    path = socket_path()
    if not socket_folder_ok(path):
        raise OSError(f"the engine's socket folder {path.parent} is not this user's alone")
    path.unlink(missing_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    os.chmod(path, 0o600)
    return server


def lock(path: Path | None = None) -> Any:
    """The daemon's lock (or another protocol's, at ``path``), held for its life; None when
    another daemon holds it."""
    state_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = open(path or lock_path(), "a+")
    try:
        if WINDOWS:
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def main(argv: list[str] | None = None) -> int:
    sys.dont_write_bytecode = True
    idle = float(os.environ.get("CADGEN_ENGINE_IDLE") or IDLE_SECONDS)
    held = lock()
    if held is None:
        return 0  # another daemon is up
    daemon = Daemon(idle)
    server = _bind()
    server.listen(64)
    if WINDOWS:
        port = server.getsockname()[1]
        temporary = port_path().with_suffix(".tmp")
        temporary.write_text(f"{port} {daemon.token}\n", encoding="utf-8")
        replace_atomic(temporary, port_path())
    server.settimeout(0.5)
    if not WINDOWS:
        import signal

        # A terminate (a logout, ``kill``) stops the daemon as ``cadgen stop`` does: its workers,
        # pools, socket and listener go with it.
        signal.signal(signal.SIGTERM, lambda _signum, _frame: daemon.stopping.set())
    threading.Thread(target=daemon.watch, daemon=True).start()
    try:
        while not daemon.stopping.is_set():
            try:
                connection, _address = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            connection.settimeout(None)
            threading.Thread(target=_serve_connection, args=(daemon, connection), daemon=True).start()
    finally:
        server.close()
        daemon.shutdown()
        try:
            (port_path() if WINDOWS else socket_path()).unlink(missing_ok=True)
        except OSError:
            pass
        held.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
