"""HTTP access for a client of the installation's engine (never a document relay)."""
from __future__ import annotations

import http.client
import threading
from urllib.parse import urlsplit

from cadgen.engine.client import request


class EngineClient:
    def __init__(self):
        self.lock = threading.Lock()
        self.endpoint = None

    def connect(self):
        with self.lock:
            if self.endpoint is None:
                answer = request({'op': 'http'})
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

    def call(self, method, path, body=b'', headers=None):
        # An engine restarted since the endpoint was read refuses its old token (same --port) or
        # its old port: either is refused before any route runs, so it is asked again once.
        for attempt in (1, 2):
            endpoint = self.connect()
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
                continue
            except (OSError, http.client.HTTPException):
                self._forget(endpoint)
                raise
            finally:
                conn.close()
            if attempt == 1 and answer[0] == 403 and b'engine token required' in answer[2]:
                self._forget(endpoint)
                continue
            return answer
