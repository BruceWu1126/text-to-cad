"""``/__cad/xbf``: a document the cadgen engine serves -- an ``.xbf``, or a ``.step``/``.stp`` --
read by the page through this server.

``GET /__cad/xbf?file=<abs>`` asks the engine's daemon to serve the file (``cadgen.engine.client``,
connect-or-start, the ``serve`` op every ``cadgen xbf serve`` sends) and answers the document's id
and where the page reads it: ``/__cad/xbf/api/doc/<id>``, under this server's own origin. The
engine reads the file once and holds it (a STEP in a reader process of its own, held by the file's
bytes: ``cadgen.engine.serve``); a request never waits for that read. The serve runs on a thread
of its own (:class:`EngineDocuments`), and a request waits for it only as long as the app's hold,
then answers 202 ``{"state": "reading", "retryMs"}`` and is asked again -- so a large STEP opens,
and a host that relays requests through a few shared slots (the CAD app's) never has one held for
a parse's length.

``GET /__cad/xbf?file=<abs>&held=1`` answers whether the engine holds a read of the file now,
without asking it for one: the home pictures a STEP only then (a large STEP's read is long).

``GET /__cad/xbf/api/...`` relays the page's reads to that engine (:meth:`EngineDocuments.relay`):
the structure, one buffer per part, an element's facts, a part's groups, the look and its
environment, and the version a page waits on, so an edit reaches the page the moment the engine
has it. These are the engine's read routes and nothing else: no other path, method or query
reaches it. The page never reads the engine's own (random) port: the CAD app's tunnel carries
these reads as it carries every route, a viewer bound to another interface (``--host``) or
reached through a port forward serves them, and the engine still answers loopback pages alone
(``cadgen.engine.access``) -- this server is its one reader. A version wait is held here no
longer than :data:`RELAY_WAIT_SECONDS` (the tunnel's, not at all): a page holds one at a time,
and a browser has six connections for every tab of this origin.

The engine is a process of its own, spoken to over its socket and its loopback HTTP port: nothing
here imports the kernel. A daemon that went (idle for an hour, or stopped) is found again where
it now serves, and a document it no longer serves is served again, once, before a read fails.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import threading
import time
from urllib.parse import parse_qs, quote, urlparse

from .backend import absolute_path
from .scanner import node_basename

__all__ = [
    "ENGINE_DOCUMENTS",
    "EngineDocuments",
    "RELAY_PREFIX",
    "RELAY_WAIT_SECONDS",
    "SERVE_HOLD_SECONDS",
    "engine_document_path",
]

# The documents the engine reads and serves: its own, and a STEP.
ENGINE_DOCUMENTS = (".xbf", ".step", ".stp")
ROUTE_PATH = "/__cad/xbf"
RELAY_PREFIX = "/__cad/xbf/"
# How long a request waits on the engine's serve before it answers 202: a document the engine
# holds, or a small one it reads quickly, is answered by the first request.
SERVE_HOLD_SECONDS = 2.0
# How long a finished serve's answer is kept for the requests that follow it.
FINISHED_SECONDS = 30.0
# How long a relayed version wait is held here at most (the module docstring).
RELAY_WAIT_SECONDS = 1.0
# How long a relayed read may take: a part the engine is still meshing is answered once it is.
RELAY_TIMEOUT_SECONDS = 600.0
# The engine's read routes, below ``/__cad/xbf``: nothing else is relayed.
_RELAYED = re.compile(
    r"/api/(?:look(?:/environment)?|doc/[^/]+(?:/version|/part/[^/]+(?:/(?:face|edge|vertex)/[0-9]+|/groups|/volume)?)?)")
_DOC = re.compile(r"/api/doc/([^/]+)")


def engine_document_path(file_ref) -> str:
    """The absolute path of the engine document ``file_ref`` names; ``ValueError`` (400) for a
    request that names none."""
    if not file_ref:
        raise ValueError(f"GET {ROUTE_PATH} needs ?file=<the absolute path of an .xbf or a .step>")
    path = absolute_path(file_ref)
    if os.path.splitext(path)[1].lower() not in ENGINE_DOCUMENTS:
        raise ValueError(f"{node_basename(path)} is not a document the cadgen engine reads (.xbf, .step, .stp)")
    if not os.path.isfile(path):
        raise ValueError(f"no document {path}")
    return path


class _Serve:
    """One file's serve, which every request for it joins."""

    __slots__ = ("done", "status", "body", "finished_at")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.status = 500
        self.body: dict = {"error": "the engine's serve stopped before it answered"}
        self.finished_at = 0.0


class EngineDocuments:
    """The engine documents this server's pages read: each file's serve, off the request and
    joined by every request for it, and the relay of the page's reads to the engine.

    ``hold_seconds`` is how long a request waits on a serve before answering 202, ``retry_ms``
    the wait the 202 asks of the client before it asks again, and ``wait_seconds`` the longest a
    relayed version wait is held. The CAD Viewer holds; the CAD app's tunnel, whose calls share
    a host's few slots, holds briefly and never waits on a version (``cadgen.mcp.tunnel``).
    ``on_crash`` hears a serve that failed for a reason other than its file (a bug), once.
    """

    def __init__(self, *, hold_seconds: float = SERVE_HOLD_SECONDS, retry_ms: int = 0,
                 wait_seconds: float = RELAY_WAIT_SECONDS, on_crash=None, clock=time.monotonic) -> None:
        self.hold_seconds = hold_seconds
        self.retry_ms = retry_ms
        self.wait_seconds = wait_seconds
        self._on_crash = on_crash
        self._clock = clock
        self._lock = threading.Lock()
        self._serves: dict[str, _Serve] = {}
        # Where the engine serves (``host:port``, as its last answer said), and the file each
        # document id it gave is of: what a read is relayed to, and what is served again when the
        # engine restarted.
        self._engine: str | None = None
        self._paths: dict[str, str] = {}

    def any_in_flight(self) -> bool:
        """Whether a serve is running: a page is waiting on it."""
        with self._lock:
            return any(not serve.done.is_set() for serve in self._serves.values())

    # --- the serve -------------------------------------------------------------------------

    def response(self, file_ref) -> tuple[int, dict]:
        """``(status, body)``: ``{"doc", "url"}`` at 200, 202 while the engine reads the file, or
        the engine's refusal at 400. ``ValueError`` for a request that names no engine document."""
        path = engine_document_path(file_ref)
        serve = self._joined(path) or self._started(path)
        if serve.done.wait(self.hold_seconds):
            return serve.status, serve.body
        return 202, {"state": "reading", "retryMs": self.retry_ms}

    def held(self, file_ref) -> dict:
        """``{"held": bool}``: whether the engine serves the document now (its daemon lists it),
        asked without starting the daemon or a read."""
        path = engine_document_path(file_ref)
        from cadgen.engine.client import request

        try:
            answer = request({"op": "status"}, start=False)
        except (OSError, ValueError):
            answer = None
        served = ((answer or {}).get("serving") or {}).get("documents") or []
        real = os.path.realpath(path)
        return {"held": any(os.path.realpath(str(item.get("path") or "")) == real for item in served)}

    def _joined(self, path: str) -> _Serve | None:
        now = self._clock()
        with self._lock:
            for name, serve in list(self._serves.items()):
                if serve.done.is_set() and now - serve.finished_at > FINISHED_SECONDS:
                    del self._serves[name]
            return self._serves.get(path)

    def _started(self, path: str) -> _Serve:
        with self._lock:
            serve = self._serves.get(path)
            if serve is not None:
                return serve
            serve = self._serves[path] = _Serve()
        threading.Thread(target=self._run, args=(serve, path), name="cadgen-engine-serve", daemon=True).start()
        return serve

    def _run(self, serve: _Serve, path: str) -> None:
        try:
            serve.status, serve.body = 200, self.serve(path)
        except (ConnectionError, OSError, ValueError) as error:
            # The engine's own words for a file it will not serve, or a daemon that would not start.
            serve.status, serve.body = 400, {"error": str(error) or type(error).__name__}
        except Exception as error:  # noqa: BLE001 - answered as the GET funnel answers a route's bug
            if self._on_crash is not None:
                self._on_crash(error)
            serve.status, serve.body = 400, {"error": str(error) or type(error).__name__}
        finally:
            serve.finished_at = self._clock()
            serve.done.set()

    def serve(self, path: str) -> dict:
        """Ask the engine to serve ``path`` now: ``{"doc", "url"}``, the url under this server's
        origin. ``ValueError`` with the engine's words when it will not."""
        from cadgen.engine.client import request

        answer = request({"op": "serve", "path": path})
        if not answer or "failed" in answer:
            raise ValueError(str((answer or {}).get("failed") or "the cadgen engine did not answer"))
        served = urlparse(str(answer.get("url") or ""))
        ident = (parse_qs(served.query).get("doc") or [""])[0]
        if not ident or not served.netloc:
            raise ValueError(f"the cadgen engine served {node_basename(path)} without naming it: {answer.get('url')}")
        with self._lock:
            self._engine = served.netloc
            self._paths[ident] = path
        return {"doc": ident, "url": f"{RELAY_PREFIX}api/doc/{quote(ident, safe='')}"}

    # --- the relay -------------------------------------------------------------------------

    def relay(self, pathname: str, query) -> tuple[int, bytes, str]:
        """A page's read of the engine (``/__cad/xbf/api/...``): ``(status, body, content type)``
        as the engine answered it. A path that is not one of the engine's read routes is a 404."""
        rest = pathname[len(ROUTE_PATH):]
        if not _RELAYED.fullmatch(rest):
            return 404, b'{"error":"Not found"}', "application/json"
        target, timeout = rest, RELAY_TIMEOUT_SECONDS
        if rest.endswith("/version"):
            try:
                wait = min(max(float(query.get("wait") or 0), 0.0), self.wait_seconds)
            except ValueError:
                wait = 0.0
            target = f"{rest}?after={quote(query.get('after') or '', safe='')}&wait={wait:g}"
            timeout = wait + 60.0
        found = _DOC.match(rest)
        ident = found.group(1) if found else ""
        for attempt in (1, 2):
            engine = self._engine_address(refresh=attempt == 2)
            try:
                if engine is None:
                    raise ConnectionRefusedError("no cadgen engine serves documents")
                status, body, content_type = _get(engine, target, timeout)
            except (OSError, http.client.HTTPException) as error:  # refused, reset, or cut short
                if attempt == 1 and self._serve_again(ident):
                    continue
                return 502, json.dumps({"error": f"the cadgen engine is not answering: {error}"}).encode(), "application/json"
            if status == 404 and attempt == 1 and ident and self._serve_again(ident):
                continue  # an engine started afresh serves nothing until it is asked again
            return status, body, content_type
        return 502, b'{"error":"the cadgen engine is not answering"}', "application/json"

    def _engine_address(self, *, refresh: bool) -> str | None:
        """Where the engine serves: as its last answer said, or -- when this server has heard
        none, or ``refresh`` -- as its daemon says now, without starting one."""
        with self._lock:
            known = self._engine
        if known is not None and not refresh:
            return known
        from cadgen.engine.client import request

        try:
            answer = request({"op": "status"}, start=False)
        except (OSError, ValueError):
            answer = None
        url = ((answer or {}).get("serving") or {}).get("url")
        found = urlparse(str(url)).netloc if url else None
        with self._lock:
            if found:
                self._engine = found
            return self._engine

    def _serve_again(self, ident: str) -> bool:
        """Serve again the file a document id was given for, starting the engine if it went:
        whether there is such a file."""
        with self._lock:
            path = self._paths.get(ident)
        if not path or not os.path.isfile(path):
            return False
        try:
            self.serve(path)
        except (OSError, ValueError):
            return False
        return True


def _get(engine: str, target: str, timeout: float) -> tuple[int, bytes, str]:
    """One GET of the engine's (``host:port``) loopback HTTP."""
    host, _, port = engine.rpartition(":")
    connection = http.client.HTTPConnection(host.strip("[]"), int(port), timeout=timeout)
    try:
        connection.request("GET", target)
        response = connection.getresponse()
        return response.status, response.read(), response.getheader("Content-Type") or "application/octet-stream"
    finally:
        connection.close()
