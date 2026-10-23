"""The installation's HTTP application: document routes and per-launch viewer sessions."""
from __future__ import annotations

import io
import os
import secrets
import threading
import time
from http.cookies import SimpleCookie
from urllib.parse import urlencode

from .documents import EngineDocuments
from .http_app import CadApp, RAW_FILE_HEADERS, host_is_allowed


class DocumentResponse:
    """Adapt the engine's socket-shaped writer to the application's response, in process."""
    def __init__(self, request):
        self.command = request.method
        self.path = request.path.removeprefix('/__cad')
        if request.query:
            self.path += '?' + urlencode(list(request.query))
        self.headers = request.headers
        self.rfile = io.BytesIO(request.body() if request.method == 'POST' else b'')
        self.wfile = io.BytesIO()
        self.status = 200
        self.response_headers = {}

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.response_headers[name.lower()] = str(value)

    def end_headers(self):
        pass


def document_route(server, request, response):
    if request.method == 'POST' and not request.header('x-cadgen-viewer'):
        response.send_json(403, {'error': 'missing x-cadgen-viewer header'})
        return
    handler = DocumentResponse(request)
    server.route(handler, request.method)
    # Deliberately no CORS headers: all application reads are same-origin or authenticated MCP.
    response.send_bytes(handler.status, handler.wfile.getvalue(),
                        handler.response_headers.get('content-type', 'application/octet-stream'), RAW_FILE_HEADERS)


class SessionApp(CadApp):
    def __init__(self, server, **kwargs):
        super().__init__(documents=EngineDocuments(server=server), **kwargs)
        self.server = server
        self.touched = time.monotonic()

    def handle(self, request, response):
        self.touched = time.monotonic()
        self.server.touch()
        if not host_is_allowed(request.header('host'), self.host):
            return response.send_json(403, {'error': 'Host is not a local name'})
        if request.path.startswith('/__cad/api/') or request.path.startswith('/api/'):
            return document_route(self.server, request, response)
        return super().handle(request, response)


class EngineApp:
    def __init__(self, server, host, *, session_factory=None):
        self.server, self.host = server, host
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.pages = {}
        self.active_requests = 0
        self._default = None
        self._mcp = None
        self.session_factory = session_factory
        from cadgen.analytics import Recorder
        self.analytics = Recorder()
        self.analytics.start()

    def active(self):
        with self.lock:
            apps = [*self.pages.values(), *([self._default] if self._default else [])]
            return self.active_requests > 0 or any(app._exports and app._exports.keeps_alive() for app in apps)

    def close(self):
        if self._mcp is not None:
            self._mcp.close()
        self.analytics.close()

    def endpoint(self):
        return {'ok': True, 'url': self.server.url, 'port': self.server.port, 'token': self.token}

    def launch(self, options):
        from .main import resolve_dist_dir
        start = os.path.abspath(options.get('start') or os.path.expanduser('~'))
        dist = '' if options.get('api_only') else resolve_dist_dir(options.get('dist') or '')
        if not options.get('api_only') and not dist:
            return {'failed': 'CAD Viewer client is not built; build apps/web or install the cadgen wheel'}
        with self.lock:
            found = None if options.get('new') else next((key for key, app in self.pages.items()
                      if os.path.normcase(os.path.normpath(app.start)) == os.path.normcase(start)
                      and os.path.normcase(app.dist_dir) == os.path.normcase(dist)), None)
            ident = found or secrets.token_urlsafe(18)
            if found is None:
                app = SessionApp(self.server, host=self.host, port=self.server.port, dist_dir=dist, start=start)
                app.analytics = self.analytics
                self.pages[ident] = app
            self.pages[ident].touched = time.monotonic()
        url = f'{self.server.url}/s/{ident}/?token={self.token}'
        return {'ok': True, 'url': url, 'apiUrl': f'{self.server.url}/s/{ident}', 'token': self.token,
                'port': self.server.port, 'pid': os.getpid(), 'session': ident,
                'action': 'reused' if found else 'started'}

    def sessions(self):
        with self.lock:
            now = time.monotonic()
            self.pages = {key: app for key, app in self.pages.items() if now - app.touched < 3600 or (app._exports and app._exports.keeps_alive())}
            pages = [{'id': key, 'kind': 'viewer', 'root': app.start,
                      'idle': round(time.monotonic()-app.touched, 1)} for key, app in self.pages.items()]
        return pages + ([] if self._mcp is None else self._mcp.listing())

    def default_app(self):
        with self.lock:
            if self._default is None:
                from .main import resolve_dist_dir
                self._default = SessionApp(self.server, host=self.host, port=self.server.port,
                                           dist_dir=resolve_dist_dir(''))
                self._default.analytics = self.analytics
            return self._default

    def handle(self, request, response):
        with self.lock:
            self.active_requests += 1
        try:
            return self._handle(request, response)
        finally:
            with self.lock:
                self.active_requests -= 1
            self.server.touch()

    def _handle(self, request, response):
        self.server.touch()
        if not host_is_allowed(request.header('host'), self.host):
            return response.send_json(403, {'error': 'Host is not a local name'})
        bearer = request.header('authorization').removeprefix('Bearer ')
        cookie = SimpleCookie()
        try:
            cookie.load(request.header('cookie'))
        except Exception:
            pass
        cookie_name = f'cadgen-engine-{self.server.port}'
        saved = cookie.get(cookie_name)
        supplied = bearer or (saved.value if saved else '') or request.query.get('token', '')
        if not secrets.compare_digest(str(supplied), self.token):
            return response.send_json(403, {'error': 'engine token required'})
        if request.query.get('token') == self.token and request.method == 'GET':
            query = {k: v for k, v in request.query if k != 'token'}
            location = request.path + ('?' + urlencode(query) if query else '')
            return response.send_bytes(302, b'', '', [('location', location),
                ('set-cookie', f'{cookie_name}={self.token}; Path=/; HttpOnly; SameSite=Strict'),
                ('referrer-policy', 'no-referrer')])
        if request.path == '/mcp' and self.session_factory is not None:
            return self.mcp().handle(request, response)
        if request.path == '/' and (request.query.get('doc') in self.server.served
                                    or request.query.get('doc') in self.server.model_loaders):
            from cadgen.engine.addresses import parse

            # The landing link selects an address without meshing it on the HTTP thread.
            path = next(path for path, ident in self.server.idents.items() if ident == request.query.get('doc'))
            address = parse(path)
            start = str(address.script.parent) if address and address.script else os.getcwd() if address else str(path.parent)
            launch = self.launch({'start': start})
            if 'failed' in launch:
                return response.send_json(503, launch)
            return response.send_bytes(302, b'', '', [('location', launch['apiUrl']+'/?'+urlencode({'file': str(path)}))])
        if request.path.startswith('/s/'):
            parts = request.path.split('/', 3)
            with self.lock:
                app = self.pages.get(parts[2])
            if app is None:
                return response.send_json(404, {'error': 'viewer session expired; run cadgen viewer again'})
            request.path = '/' + (parts[3] if len(parts) > 3 else '')
        else:
            app = self.default_app()
        return app.handle(request, response)

    def mcp(self):
        with self.lock:
            if self._mcp is None:
                self._mcp = self.session_factory(self)
            return self._mcp
