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

    def call(self, method, path, body=b'', headers=None):
        endpoint = self.connect()
        parsed = urlsplit(endpoint['url'])
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=600)
        try:
            conn.request(method, path, body=body, headers={**(headers or {}),
                         'Authorization': 'Bearer ' + endpoint['token']})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        except (OSError, http.client.HTTPException):
            with self.lock:
                self.endpoint = None
            raise
        finally:
            conn.close()
