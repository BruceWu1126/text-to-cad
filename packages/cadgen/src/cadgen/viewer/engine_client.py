"""HTTP access for a client of the installation's engine (never a document relay)."""
from __future__ import annotations

import http.client
import os
import threading
from urllib.parse import urlsplit

from cadgen.engine import daemon
from cadgen.engine.client import _answering, request


def _door(address=None):
    """Which engine the door at ``address`` (by default this installation's) names now: the identity
    of the door a daemon reads (on Windows, of its port file, made anew at each start), or None when
    no engine answers there. A stat and an open of the door that writes nothing: some 15 us."""
    address = address or daemon.address()
    try:
        found = os.stat(address)
    except OSError:
        return None
    if daemon.WINDOWS:
        return found.st_ino, found.st_mtime_ns, found.st_size
    return (found.st_dev, found.st_ino) if _answering(address) else None


class EngineClient:
    """The engine's loopback endpoint as its door last named it. Only the engine can own that
    door (``daemon.folder_refusal``), while a loopback port is anyone's once the engine has let it
    go, and every call carries the user's page token, which outlives the engine. So the endpoint
    held is used only while the door that named it still answers (``_door``), and it is read from
    the door again whenever it may be stale: an engine gone or replaced, after any answer that is
    not a success, after a connection error, and for every call that asks for a ``fresh``
    endpoint (an MCP ``initialize``)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.endpoint = None
        self.door = None  # the identity of the door that named ``endpoint`` (``_door``)
        self.address = None  # where that door is

    def connect(self, *, fresh=False, start=True):
        """The endpoint held, or -- when there is none, the engine that named it is gone, or
        ``fresh`` -- the one the engine's door names now: an engine started when none answers,
        unless ``start`` is false (``ConnectionError``)."""
        with self.lock:
            if self.endpoint is not None and not fresh and (self.door is None or _door(self.address) != self.door):
                self.endpoint = None  # its engine is gone: its port, and the token sent there, anyone's
            if self.endpoint is None or fresh:
                self.endpoint = None
                answer = request({'op': 'http'}, start=start)
                if not answer or 'failed' in answer:
                    raise ConnectionError((answer or {}).get('failed', 'engine did not answer'))
                self.address = daemon.address()
                self.endpoint, self.door = answer, _door(self.address)
            return self.endpoint

    def forget(self):
        """Ask the engine where it listens again on the next call."""
        with self.lock:
            self.endpoint = None

    def _forget(self, endpoint):
        with self.lock:
            if self.endpoint is endpoint:
                self.endpoint = None

    def call(self, method, path, body=b'', headers=None, *, fresh=False, start=True):
        # An engine restarted since the endpoint was read refuses its old token (same --port) or
        # its old port: either is refused before any route runs, so it is sent once more, to the
        # endpoint the door names now. ``start``: whether a call may start an engine to answer it.
        endpoint = self.connect(fresh=fresh, start=start)
        for attempt in (1, 2):
            parsed = urlsplit(endpoint['url'])
            conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=600)
            try:
                conn.request(method, path, body=body, headers={**(headers or {}),
                             'Authorization': 'Bearer ' + endpoint['token']})
                response = conn.getresponse()
                answer = response.status, dict(response.getheaders()), response.read()
            except ConnectionRefusedError:
                self._forget(endpoint)
                if attempt == 2:
                    raise
                endpoint = self.connect(fresh=True, start=start)
                continue
            except (OSError, http.client.HTTPException):
                self._forget(endpoint)
                raise
            finally:
                conn.close()
            if not 200 <= answer[0] < 300:
                self._forget(endpoint)  # the next call asks the door where the engine listens
                if attempt == 1 and answer[0] == 403 and b'engine token required' in answer[2]:
                    endpoint = self.connect(fresh=True, start=start)
                    continue
            return answer
