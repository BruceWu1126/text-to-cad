"""The host's stdio transport: forward JSON-RPC to this installation's engine.

Only connection metadata lives here. Tools, views, pages and document state belong
in the engine. Host capability corrections are made at this boundary: what the person
said (``CADGEN_MCP_PRESENTATION``), and which host launched this process, read from its
parent processes (``host.launcher``), which only this process can see.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import sys
import threading

from cadgen.viewer.engine_client import EngineClient
from .host import MIME, TABS_EXTENSION, UI_EXTENSION
from .protocol import Connection, RequestContext, RpcError, claim_stdin, claim_stdout

LOG = logging.getLogger('cadgen.mcp')


def initialize_params(params, launcher=None):
    """The host's initialize as the engine is sent it: with what the person said of the host
    (``CADGEN_MCP_PRESENTATION``) as a declaration, and ``launcher``, the host that started this
    process (``host.launcher``), for the server to weigh (``host.presentation``)."""
    params = copy.deepcopy(params)
    offered = params.setdefault('capabilities', {})
    extensions = offered.setdefault('extensions', {})
    told = os.environ.get('CADGEN_MCP_PRESENTATION')
    if told == 'inline':
        extensions[UI_EXTENSION] = {'mimeTypes': [MIME]}
    elif told == 'text':
        extensions.pop(UI_EXTENSION, None)
        extensions.pop(TABS_EXTENSION, None)
        launcher = {'host': 'CADGEN_MCP_PRESENTATION=text', 'renders': False}
    params.setdefault('_meta', {})['dev.texttocad/transport'] = {
        'appDir': os.environ.get('CADGEN_MCP_APP_DIR', ''), 'cwd': os.getcwd(), 'launcher': launcher,
    }
    return params


class Holder:
    """This server's hold on the engine (``cadgen.engine.client.hold``): taken as the server starts,
    starting the engine when none runs, and kept while the host runs, so a shell the host runs in a
    sandbox that cannot start an engine always finds one. The engine still leaves on its idle
    timeout once the host has quit and this process with it. An engine that goes while held (``cadgen
    stop``, a crash) is held again at the host's next request, never at once: a stop is obeyed."""

    def __init__(self):
        self.wanted = threading.Event()
        self.tried = threading.Event()  # the first hold taken, or failed
        self.holding = False
        self.thread = threading.Thread(target=self._run, name='cadgen-hold', daemon=True)

    def start(self):
        self.wanted.set()
        self.thread.start()

    def want(self):
        if not self.holding:
            self.wanted.set()

    def _run(self):
        from cadgen.engine.client import hold
        while True:
            self.wanted.wait()
            self.wanted.clear()
            try:
                connection = hold()
            except (ConnectionError, OSError, ValueError) as error:
                LOG.info('could not hold the engine: %s', error)
                self.tried.set()
                continue
            self.holding = True
            self.tried.set()
            try:
                with connection:
                    while connection.reader.read(4096):
                        pass  # until the engine goes
            except OSError:
                pass
            finally:
                self.holding = False
            LOG.info('the engine this server held has stopped')


class Proxy:
    """One host connection's engine session. The host initializes once, but the engine's session
    can end under it (the engine restarted, or the session's idle hour passed): the shim then opens
    a new one with the host's own initialize and sends the request again, so the host never has to
    reconnect. Only a request the engine never took is sent again: one it refused for an unknown
    session, or one whose connection was refused."""

    def __init__(self, client=None, holder=None, launcher=lambda: None):
        self.client = client or EngineClient()
        self.holder = holder
        self.launcher = launcher  # the host that started this process, read once (``serve``)
        self.session = None
        self.protocol = None
        self.initialize = None  # the host's initialize, as the engine was sent it
        self.initialized = False  # whether the host said notifications/initialized
        self.lock = threading.Lock()

    def _send(self, message, session, *, fresh=False):
        if self.holder is not None:
            self.holder.want()
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'}
        if session:
            headers['Mcp-Session-Id'], headers['MCP-Protocol-Version'] = session
        return self.client.call('POST', '/mcp', json.dumps(message).encode('utf-8'), headers, fresh=fresh)

    def _open(self, request_id, params):
        # The host's initialize goes only to the endpoint the engine's door names now, never to
        # one held from an earlier call, which another process may have taken since.
        status, reply_headers, data = self._send({'jsonrpc': '2.0', 'id': request_id, 'method': 'initialize',
                                                  'params': params}, None, fresh=True)
        answer = self._answer(status, data)
        headers = {name.lower(): value for name, value in reply_headers.items()}
        session = (headers['mcp-session-id'], answer['result']['protocolVersion'])
        with self.lock:
            self.session, self.protocol = session
        return answer['result'], session

    def _reopen(self, stale):
        """A new engine session for the host's, once per ended one however many requests find it ended."""
        with self.lock:
            if self.initialize is None:
                raise RpcError(-32000, 'The engine session ended before this server was initialized.')
            current = (self.session, self.protocol) if self.session else None
            if current is not None and current != stale:
                return current  # another request opened it already
            params, initialized = self.initialize, self.initialized
        LOG.info('the engine session ended; opening a new one')
        _result, session = self._open('cadgen-reopen', params)
        if initialized:
            self._send({'jsonrpc': '2.0', 'method': 'notifications/initialized', 'params': {}}, session)
        return session

    @staticmethod
    def _answer(status, data):
        if status not in (200, 202):
            raise RpcError(-32000, f'Engine MCP endpoint returned HTTP {status}.')
        if status == 202:
            return {}
        answer = json.loads(data)
        if 'error' in answer:
            error = answer['error']
            raise RpcError(error['code'], error['message'], error.get('data'))
        return answer

    def _post(self, message):
        with self.lock:
            session = (self.session, self.protocol) if self.session else None
        for attempt in (1, 2):
            try:
                status, _headers, data = self._send(message, session)
            except ConnectionRefusedError:
                # No engine listens where the last one did: the request reached nothing.
                if attempt == 2 or session is None:
                    raise
                status = 404
            if status == 403 and session is not None and attempt == 1:
                # Another engine took the port: its token is not the one this shim holds.
                self.client.forget()
                status = 404
            if status != 404 or session is None or attempt == 2:
                break
            session = self._reopen(session)
        if status == 404:
            raise RpcError(-32000, 'The engine session ended and a new one could not be opened.')
        answer = self._answer(status, data)
        return answer.get('result', {}) if answer else {}

    def handle(self, method, params, context: RequestContext):
        if method == 'initialize':
            params = initialize_params(params, self.launcher())
            result, _session = self._open(context.request_id, params)
            with self.lock:
                self.initialize = params
            return result
        return self._post({'jsonrpc': '2.0', 'id': context.request_id, 'method': method, 'params': params})

    def notify(self, method, params):
        if method == 'notifications/initialized':
            with self.lock:
                self.initialized = True
        self._post({'jsonrpc': '2.0', 'method': method, 'params': params})

    def close(self):
        with self.lock:
            ident = self.session
        if ident:
            # The session's end, told to the engine that holds it: never an engine started to hear it
            # (a host closing after `cadgen stop` would start one up for nothing).
            try:
                self.client.call('DELETE', '/mcp', headers={'Mcp-Session-Id': ident}, start=False)
            except OSError:
                pass


def serve(argv=None):
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format='cadgen mcp: %(message)s')
    output = claim_stdout()
    requests = claim_stdin()
    from cadgen._internal.channel import remember
    remembering = threading.Thread(target=remember, daemon=True)
    remembering.start()
    holder = Holder()
    holder.start()  # the engine up and held from the start, not from the first card
    # Which host started this process, read from its parents while the host starts to greet it.
    from .host import launcher
    found = {}
    reading = threading.Thread(target=lambda: found.update(host=launcher()), name='cadgen-host', daemon=True)
    reading.start()

    def launched():
        reading.join(5)
        return found.get('host')

    proxy = Proxy(holder=holder, launcher=launched)
    connection = Connection(requests, output, proxy.handle, workers=64, notification=proxy.notify)
    try:
        connection.serve()
    finally:
        proxy.close()
        remembering.join(1)
        # An engine this server is starting is up before it leaves, never half started after it.
        holder.tried.wait(60)
    return 0
