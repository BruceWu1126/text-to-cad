"""The host's stdio transport: forward JSON-RPC to this installation's engine.

Only connection metadata lives here. Tools, views, pages and document state belong
in the engine. Host capability corrections are made at this boundary.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import sys
import threading

from cadgen.viewer.engine_client import EngineClient
from .protocol import Connection, RequestContext, RpcError, claim_stdout


def initialize_params(params):
    params = copy.deepcopy(params)
    client = params.get('clientInfo') or {}
    offered = params.setdefault('capabilities', {})
    extensions = offered.setdefault('extensions', {})
    if client.get('name') == 'codex-mcp-client':
        extensions['dev.texttocad/tabs'] = {'entrypoints': ['global', 'thread', 'file']}
    elif os.environ.get('CADGEN_MCP_PRESENTATION') in ('inline', 'text'):
        if os.environ['CADGEN_MCP_PRESENTATION'] == 'inline':
            extensions['io.modelcontextprotocol/ui'] = {'mimeTypes': ['text/html;profile=mcp-app']}
        else:
            extensions.pop('io.modelcontextprotocol/ui', None)
            extensions.pop('dev.texttocad/tabs', None)
    params.setdefault('_meta', {})['dev.texttocad/transport'] = {
        'appDir': os.environ.get('CADGEN_MCP_APP_DIR', ''), 'cwd': os.getcwd(),
    }
    return params


class Proxy:
    def __init__(self, client=None):
        self.client = client or EngineClient()
        self.session = None
        self.protocol = None
        self.lock = threading.Lock()

    def _post(self, message):
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'}
        with self.lock:
            if self.session:
                headers['Mcp-Session-Id'] = self.session
            if self.protocol:
                headers['MCP-Protocol-Version'] = self.protocol
        status, reply_headers, data = self.client.call('POST', '/mcp', json.dumps(message).encode('utf-8'), headers)
        if status == 404:
            with self.lock:
                self.session = self.protocol = None
            raise RpcError(-32000, 'The engine session ended; reconnect this MCP server.')
        if status not in (200, 202):
            raise RpcError(-32000, f'Engine MCP endpoint returned HTTP {status}.')
        if status == 202:
            return {}
        answer = json.loads(data)
        if 'error' in answer:
            error = answer['error']
            raise RpcError(error['code'], error['message'], error.get('data'))
        if message['method'] == 'initialize':
            headers = {name.lower(): value for name, value in reply_headers.items()}
            with self.lock:
                self.session = headers['mcp-session-id']
                self.protocol = answer['result']['protocolVersion']
        return answer['result']

    def handle(self, method, params, context: RequestContext):
        if method == 'initialize':
            params = initialize_params(params)
        return self._post({'jsonrpc': '2.0', 'id': context.request_id, 'method': method, 'params': params})

    def notify(self, method, params):
        self._post({'jsonrpc': '2.0', 'method': method, 'params': params})

    def close(self):
        with self.lock:
            ident = self.session
        if ident:
            try:
                self.client.call('DELETE', '/mcp', headers={'Mcp-Session-Id': ident})
            except OSError:
                pass


def serve(argv=None):
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format='cadgen mcp: %(message)s')
    output = claim_stdout()
    from cadgen._internal.channel import remember
    remembering = threading.Thread(target=remember, daemon=True)
    remembering.start()
    proxy = Proxy()
    connection = Connection(sys.stdin.buffer, output, proxy.handle, workers=64, notification=proxy.notify)
    try:
        connection.serve()
    finally:
        proxy.close()
        remembering.join(1)
    return 0
