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
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

__all__ = ["IDLE_SECONDS", "lock_path", "main", "socket_path", "state_dir"]

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
    """One warm worker process and the script it builds."""

    def __init__(self, script: str, environment: dict[str, str]) -> None:
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
                                        cwd=str(Path(script).parent), text=True, encoding="utf-8",
                                        errors="replace", bufsize=1)
        self.lock = threading.Lock()
        self.sink: Callable[[str], None] | None = None
        self.started = time.time()
        self.builds = 0
        threading.Thread(target=self._stderr, daemon=True).start()
        ready = self._read()
        if ready is None or "ready" not in ready:
            raise WorkerGone(f"the worker for {script} did not start")
        self.pid = ready["ready"]

    def _stderr(self) -> None:
        assert self.process.stderr is not None
        for line in self.process.stderr:
            sink = self.sink
            if sink is not None:
                sink(line.rstrip("\n"))

    def _read(self) -> dict[str, Any] | None:
        assert self.process.stdout is not None
        while True:
            line = self.process.stdout.readline()
            if not line:
                return None
            line = line.strip()
            if line.startswith("{"):
                return json.loads(line)

    def alive(self) -> bool:
        return self.process.poll() is None

    def build(self, request: dict[str, Any], send: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        with self.lock:
            self.sink = lambda text: send({"log": text, "stream": "model"})
            try:
                assert self.process.stdin is not None
                self.process.stdin.write(json.dumps(request) + "\n")
                self.process.stdin.flush()
                while True:
                    message = self._read()
                    if message is None:
                        code = self.process.wait()
                        raise WorkerGone(f"the worker for {Path(self.script).name} exited ({code}) during the build")
                    if "log" in message:
                        send({"log": message["log"]})
                        continue
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
            found = self.workers[key] = Worker(script, environment)
            return found

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
                        answer = worker.build({"id": 1, "op": "build", "script": script,
                                               "functions": request.get("functions"), "cwd": request.get("cwd")}, send)
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
                self.server = Server(port=int(request.get("port") or 0), three=request.get("three"), touch=self.touch)
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


def _lock() -> Any:
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
    held = _lock()
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
