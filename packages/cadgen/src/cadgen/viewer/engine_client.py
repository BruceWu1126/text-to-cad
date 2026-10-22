"""HTTP access for a client of the installation's engine (never a document relay)."""
from __future__ import annotations

import http.client
import json
import os
import threading
from urllib.parse import urlencode, urlsplit

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


class RemoteApp:
    """Transitional MCP client of the engine's complete HTTP application."""
    def __init__(self):
        self.client = EngineClient()

    def read_catalog(self, file):
        status, _, data = self.client.call('GET', '/__cad/catalog?' + urlencode({'file': file}))
        if status != 200:
            raise ValueError('engine catalog read failed')
        return json.loads(data)

    def handle(self, request, response):
        route = request.path + ('?' + urlencode(list(request.query)) if request.query else '')
        status, headers, data = self.client.call(request.raw_method, route,
                                request.body() if request.method == 'POST' else b'', request.headers)
        response.send_bytes(status, data, headers.get('Content-Type', headers.get('content-type', '')),
                            [(k, v) for k, v in headers.items() if k.lower() not in
                             {'date', 'server', 'content-length', 'content-type', 'cache-control'}])
