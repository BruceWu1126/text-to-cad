"""HTTP access for a client of the installation's engine (never a document relay)."""
from __future__ import annotations

import http.client
import threading
from urllib.parse import urlsplit

from cadgen.engine.client import request


class EngineClient:
    """The engine's loopback endpoint as its door last named it. Only the engine can own that
    door (``daemon.folder_refusal``), while a loopback port is anyone's once the engine has let it
    go, so the endpoint is read from the door again whenever the one held may be stale: after any
    answer that is not a success, after a connection error, and for every call that asks for a
    ``fresh`` endpoint (an MCP ``initialize``)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.endpoint = None

    def connect(self, *, fresh=False, start=True):
        """The endpoint held, or -- when there is none, or ``fresh`` -- the one the engine's door
        names now: an engine started when none answers, unless ``start`` is false (``ConnectionError``)."""
        with self.lock:
            if self.endpoint is None or fresh:
                self.endpoint = None
                answer = request({'op': 'http'}, start=start)
                if not answer or 'failed' in answer:
                    raise ConnectionError((answer or {}).get('failed', 'engine did not answer'))
                self.endpoint = answer
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
