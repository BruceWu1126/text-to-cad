"""Reaching the daemon: connect to its socket, starting it first when none answers."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from typing import Any, Callable

from cadgen.engine import daemon as _daemon

__all__ = ["request"]


def _connect() -> socket.socket | None:
    try:
        if _daemon.WINDOWS:
            port, _token = _daemon.port_path().read_text(encoding="utf-8").split()
            return socket.create_connection(("127.0.0.1", int(port)), timeout=5)
        path = _daemon.socket_path()
        if not path.exists():
            return None
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        if len(str(path).encode()) < 100:
            client.connect(str(path))
        else:
            previous = os.getcwd()
            os.chdir(path.parent)
            try:
                client.connect(path.name)
            finally:
                os.chdir(previous)
        return client
    except (OSError, ValueError):
        return None


def _start() -> None:
    env = dict(os.environ)
    root = _daemon.engine_root()
    path = env.get("PYTHONPATH", "")
    if root not in path.split(os.pathsep):
        env["PYTHONPATH"] = os.pathsep.join(item for item in (root, path) if item)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    options: dict[str, Any] = {}
    if _daemon.WINDOWS:
        options["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        options["start_new_session"] = True
    subprocess.Popen([sys.executable, "-m", "cadgen.engine.daemon"], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, cwd=str(_daemon.state_dir().parent)
                     if _daemon.state_dir().parent.exists() else None, **options)


def connect(*, start: bool = True, timeout: float = 60.0) -> socket.socket | None:
    """A connection to the daemon, started when ``start`` and none answers."""
    found = _connect()
    if found is not None or not start:
        return found
    _daemon.state_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
    _start()
    started = time.monotonic()
    deadline = started + timeout
    while time.monotonic() < deadline:
        found = _connect()
        if found is not None:
            return found
        time.sleep(0.05)
        if time.monotonic() - started > 1.0:
            # A daemon that is stopping still holds the lock, and the one just started
            # left at once: start another once the lock is free.
            _start()
            started = time.monotonic()
    raise ConnectionError("the engine daemon did not start")


def wait_gone(timeout: float = 60.0) -> bool:
    """Wait until the daemon is gone: its socket no longer connects and its lock, held
    until its workers have stopped, is free."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = _connect()
        if found is None:
            held = _daemon.lock()
            if held is not None:
                held.close()
                return True
        else:
            found.close()
        time.sleep(0.05)
    return False


def request(message: dict[str, Any], on_log: Callable[[dict[str, Any]], None] = lambda _m: None, *,
            start: bool = True) -> dict[str, Any] | None:
    """Send one request; every log line to ``on_log``; the final answer returned (None
    when no daemon is up and ``start`` is false)."""
    connection = connect(start=start)
    if connection is None:
        return None
    if _daemon.WINDOWS:
        message = {**message, "token": _daemon.port_path().read_text(encoding="utf-8").split()[1]}
    with connection:
        connection.settimeout(None)
        stream = connection.makefile("rwb")
        stream.write((json.dumps(message) + "\n").encode())
        stream.flush()
        for line in stream:
            answer = json.loads(line)
            if "log" in answer:
                on_log(answer)
                continue
            return answer
    raise ConnectionError("the engine daemon closed the connection")
