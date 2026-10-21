"""The engine daemon: one per installation, started on demand, gone after an idle hour.

It holds a warm worker per model script (``worker``) and hands each build to its
script's worker, one build at a time per worker, builds of different scripts at once.
It also serves documents over HTTP (``serve``), holding each loaded document and its
parts' tessellations in its memory.

Its only files are the operating system's coordination: a Unix socket and a lock in
``tempfile.gettempdir()/cadgen-engine`` (on Windows, a localhost TCP port and a token,
in a port file there). Requests are one JSON object per line on a connection; answers
are JSON lines, the last of which carries ``done``, ``failed`` or ``ok``.
"""

from __future__ import annotations

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
from pathlib import Path
from typing import Any, Callable

__all__ = ["IDLE_SECONDS", "lock", "lock_path", "main", "socket_path", "state_dir"]

IDLE_SECONDS = 3600.0
PROTOCOL = 1
WINDOWS = os.name == "nt"


def state_dir() -> Path:
    """Where the daemon's socket and lock live: ``tempfile.gettempdir()/cadgen-engine``,
    or ``CADGEN_ENGINE_DIR``."""
    override = os.environ.get("CADGEN_ENGINE_DIR")
    return Path(override) if override else Path(tempfile.gettempdir()) / "cadgen-engine"


def _tag() -> str:
    """One daemon per installation: the interpreter and the engine it runs."""
    material = f"{sys.executable}|{Path(__file__).resolve().parent}|{PROTOCOL}"
    return hashlib.sha256(material.encode()).hexdigest()[:12]


def socket_path() -> Path:
    return state_dir() / f"{_tag()}.sock"


def lock_path() -> Path:
    return state_dir() / f"{_tag()}.lock"


def port_path() -> Path:
    return state_dir() / f"{_tag()}.port"


def engine_root() -> str:
    """The folder ``cadgen`` is imported from: a worker this daemon starts imports the same."""
    return str(Path(__file__).resolve().parent.parent.parent)


# --- workers -----------------------------------------------------------------------------


class WorkerGone(Exception):
    pass


class Worker:
    """One warm worker process and the script it builds.

    The worker's protocol pipe (its original stdout) carries frames: a JSON object on one
    line and, when the object has ``bytes``, exactly that many bytes after the line. A
    reader thread takes every frame as it comes: a build's ``log`` lines go to that
    build's caller, each served document it saves -- a ``patch`` frame, sent the moment
    the xbf is in place, before the exports -- to ``on_patch``, its ``done``/``failed``
    answer to the waiting ``build`` call, and its closing ``settled`` to ``on_settled``,
    which lets the server look at those files again. ``where`` asks the worker what made
one element of a document it built (``worker``): a request of its own, answered on the
same pipe, even while a build runs."""

    def __init__(self, script: str, environment: dict[str, str],
                 on_patch: Callable[[dict[str, Any], bytes, float], None] = lambda *_args: None,
                 on_settled: Callable[..., None] = lambda _worker, _every=False: None) -> None:
        self.script = script
        self.environment = environment
        env = dict(environment)
        path = env.get("PYTHONPATH", "")
        root = engine_root()
        if root not in path.split(os.pathsep):
            env["PYTHONPATH"] = os.pathsep.join(item for item in (root, path) if item)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        self.process = subprocess.Popen([sys.executable, "-m", "cadgen.engine.worker"], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                                        cwd=str(Path(script).parent))
        self.lock = threading.Lock()
        self.writing = threading.Lock()  # one request line at a time on the worker's stdin
        self.asked: dict[str, "queue.Queue[Any]"] = {}
        self.questions = itertools.count(1)
        self.sink: Callable[[dict[str, Any]], None] | None = None
        self.on_patch = on_patch
        self.on_settled = on_settled
        self.answers: "queue.Queue[dict[str, Any] | None]" = queue.Queue()
        self.started = time.time()
        self.builds = 0
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
                elif "patch" in message:
                    try:
                        self.on_patch(message["patch"], payload, arrived)
                    except Exception:  # noqa: BLE001 - a document that cannot be patched is read from its file
                        pass
                elif "settled" in message:
                    self.on_settled(self)
                elif "where" in message:
                    waiting = self.asked.pop(str(message.get("id")), None)
                    if waiting is not None:
                        waiting.put(message["where"])
                else:
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

    def _write(self, request: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        with self.writing:
            self.process.stdin.write((json.dumps(request) + "\n").encode())
            self.process.stdin.flush()

    def where(self, path: str, key: str, kind: str, ordinal: int, timeout: float = 10.0) -> dict[str, Any] | None:
        """What made one element of a document this worker built, as its last build of it
        named the element (``build.made_by``); None when it did not build it, or is gone."""
        ident = f"where{next(self.questions)}"
        waiting: "queue.Queue[Any]" = queue.Queue(1)
        self.asked[ident] = waiting
        try:
            self._write({"id": ident, "op": "where", "path": path, "key": key, "kind": kind, "ordinal": ordinal})
            return waiting.get(timeout=timeout)
        except (OSError, ValueError, queue.Empty):
            return None
        finally:
            self.asked.pop(ident, None)

    def build(self, request: dict[str, Any], send: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        with self.lock:
            self.sink = send
            try:
                self._write(request)
                message = self.answers.get()
                if message is None:
                    code = self.process.wait()
                    raise WorkerGone(f"the worker for {Path(self.script).name} exited ({code}) during the build")
                self.builds += 1
                return message
            except (BrokenPipeError, OSError) as error:
                raise WorkerGone(str(error)) from error
            finally:
                self.sink = None

    def stop(self) -> None:
        try:
            if self.process.stdin is not None:
                self.process.stdin.close()
            self.process.wait(timeout=5)
        except Exception:  # noqa: BLE001
            self.process.kill()


def _read_exactly(stream: Any, size: int) -> bytes:
    chunks = []
    while size:
        chunk = stream.read(size)
        if not chunk:
            raise EOFError("the worker closed its pipe inside a frame")
        chunks.append(chunk)
        size -= len(chunk)
    return b"".join(chunks)


# --- the daemon ---------------------------------------------------------------------------


class Daemon:
    def __init__(self, idle: float) -> None:
        self.idle = idle
        self.workers: dict[str, Worker] = {}
        self.workers_lock = threading.Lock()
        self.last = time.monotonic()
        self.busy = 0
        self.busy_lock = threading.Lock()
        self.stopping = threading.Event()
        self.server = None
        self.token = secrets.token_hex(16) if WINDOWS else ""
        # Per xbf a build wrote (its path, ``os.path.normcase``d), the worker that built
        # it last: the one that holds its provenance (``provenance``).
        self.builders: dict[str, Worker] = {}

    def touch(self) -> None:
        self.last = time.monotonic()

    def worker(self, script: str, environment: dict[str, str]) -> Worker:
        key = os.path.normcase(script)
        relevant = {name: environment.get(name, "") for name in ("PYTHONPATH", "PATH", "VIRTUAL_ENV")}
        with self.workers_lock:
            found = self.workers.get(key)
            if found is not None and found.alive() and {name: found.environment.get(name, "") for name in relevant} == relevant:
                return found
            if found is not None:
                found.stop()
            found = self.workers[key] = Worker(script, environment, self.patched, self.settled)
            return found

    def provenance(self, path: str, key: str, kind: str, ordinal: int) -> dict[str, Any] | None:
        """What made one element of a served document, asked of the worker that built it
        (``Worker.where``): None for a document no live worker built -- one served cold."""
        resolved = str(Path(path).resolve())
        worker = self.builders.get(os.path.normcase(resolved))
        if worker is None or not worker.alive():
            return None
        return worker.where(resolved, key, kind, ordinal)

    def patched(self, header: dict[str, Any], payload: bytes, arrived: float) -> None:
        """A worker's document for a served path, put in place of the one served."""
        server = self.server
        if server is not None:
            server.patch(header, payload, arrived)

    def settled(self, worker: Worker, every: bool = False) -> None:
        """A worker is done with a build's documents (``every``: it is gone): their files
        may be looked at again."""
        with self.workers_lock:
            held = [paths for paths in worker.held] if every else worker.held[:1]
            del worker.held[:len(held)]
        if self.server is not None:
            for paths in held:
                self.server.release(paths)

    def handle(self, request: dict[str, Any], send: Callable[[dict[str, Any]], None]) -> None:
        self.touch()
        op = request.get("op")
        if op == "build":
            with self.busy_lock:
                self.busy += 1
            try:
                script = request["script"]
                for attempt in (1, 2):
                    try:
                        worker = self.worker(script, request.get("env") or {})
                        fresh = worker.builds == 0
                        order = {"id": 1, "op": "build", "script": script, "functions": request.get("functions"),
                                 "cwd": request.get("cwd"), "verify": bool(request.get("verify"))}
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
                            for path in report.get("files") or {}:
                                if path.endswith(".xbf"):
                                    self.builders[os.path.normcase(path)] = worker
                        answer["worker"] = {"pid": worker.pid, "fresh": fresh}
                        send(answer)
                        return
                    except WorkerGone as error:
                        with self.workers_lock:
                            self.workers.pop(os.path.normcase(script), None)
                        if attempt == 2 or "during the build" in str(error):
                            send({"failed": str(error)})
                            return
            finally:
                with self.busy_lock:
                    self.busy -= 1
                self.touch()
        elif op == "serve":
            from cadgen.engine.serve import Server

            if self.server is None:
                self.server = Server(port=int(request.get("port") or 0), three=request.get("three"), touch=self.touch,
                                     provenance=self.provenance)
                self.server.start()
            try:
                ident = self.server.add(Path(request["path"]))
            except Exception as error:  # noqa: BLE001
                send({"failed": f"cannot serve {request['path']}: {error}"})
                return
            send({"ok": True, "url": f"{self.server.url}/?doc={ident}", "port": self.server.port})
        elif op == "status":
            with self.workers_lock:
                workers = [{"script": w.script, "pid": w.pid, "builds": w.builds, "alive": w.alive(),
                            "busy": w.lock.locked()} for w in self.workers.values()]
            send({"ok": True, "pid": os.getpid(), "workers": workers, "idle": round(time.monotonic() - self.last, 1),
                  "serving": None if self.server is None else {"url": self.server.url,
                                                               "documents": self.server.listing()}})
        elif op == "stop":
            send({"ok": True})
            self.stopping.set()
        else:
            send({"failed": f"unknown request {op!r}"})

    def watch(self) -> None:
        while not self.stopping.wait(min(30.0, max(self.idle / 10, 0.2))):
            if self.busy == 0 and time.monotonic() - self.last > self.idle:
                self.stopping.set()

    def shutdown(self) -> None:
        with self.workers_lock:
            for worker in self.workers.values():
                worker.stop()
            self.workers.clear()
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

            daemon.handle(request, send)
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
    path.unlink(missing_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    if len(str(path).encode()) < 100:
        server.bind(str(path))
    else:  # a path past the socket name limit: bound by its name, from its folder
        previous = os.getcwd()
        os.chdir(path.parent)
        try:
            server.bind(path.name)
        finally:
            os.chdir(previous)
    os.chmod(path, 0o600)
    return server


def lock() -> Any:
    """The daemon's lock, held for its life; None when another daemon holds it."""
    state_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = open(lock_path(), "a+")
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
    idle = float(os.environ.get("CADGEN_ENGINE_IDLE", IDLE_SECONDS))
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
        os.replace(temporary, port_path())
    server.settimeout(0.5)
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
