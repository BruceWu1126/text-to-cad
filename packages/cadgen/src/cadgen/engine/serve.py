"""Serving documents over HTTP: the structure as JSON, each part as one binary buffer.

``GET /api/docs`` lists the documents served; ``GET /api/doc/<id>`` is one document's
structure (``document.Loaded.structure``): its parts with their names, keys, colours
and element names, its instances with world matrices, its tree, and its version, and
for each part already meshed the faces its mesher left without triangles
(``unmeshed``) with ``BRepCheck``'s verdict on each (``unmeshedCheck``);
``GET /api/doc/<id>/part/<key>`` is one part's tessellation (``mesh.Mesh.to_bytes``),
meshed when first asked and held in memory by the part's geometry key, so a part that
two documents, two versions of one document or ten instances share is meshed once;
``GET /api/doc/<id>/part/<key>/face|edge|vertex/<ordinal>`` is what the kernel measures
of that element, now (``mesh.Topology``); ``POST /api/doc/<id>/mesh`` meshes every part
not held yet, in parallel, and answers how long that took. A document is read again
when its file changes: the page polls the version. ``/`` is a page that shows one
(``page/``), with three.js served from a ``node_modules`` folder (``--three``, or the
nearest one above the working directory).

Meshing runs off the request's thread (``Mesher``): by default on a pool of processes
started with the server and kept warm, each handed one part's shape as bytes; or
``inline`` (``CADGEN_ENGINE_MESH=inline``, or ``?mode=inline``), one part after another on
one thread, each spread over the kernel's own threads. Measured on the three benchmark
documents, the pool meshed a whole document 2.8-3.8x faster than inline.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import mimetypes
import os
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

from cadgen.engine.mesh import shape_bytes

__all__ = ["MeshCache", "Server", "find_three"]

PAGE = Path(__file__).resolve().parent / "page"


def find_three(start: Path | None = None) -> Path | None:
    """The ``three`` package folder: ``CADGEN_ENGINE_THREE``, else the nearest
    ``node_modules/three`` above ``start`` (the working directory) or above this file."""
    override = os.environ.get("CADGEN_ENGINE_THREE")
    if override:
        return Path(override)
    for base in (Path(start or os.getcwd()).resolve(), Path(__file__).resolve()):
        for folder in (base, *base.parents):
            candidate = folder / "node_modules" / "three"
            if (candidate / "build" / "three.module.js").is_file():
                return candidate
    return None


class MeshCache:
    """Part tessellations in memory, by geometry key, least recently used dropped past
    ``cap`` bytes; with each, the faces its mesher left without triangles and why."""

    def __init__(self, cap: int = 2 << 30) -> None:
        self.cap = cap
        self.items: "OrderedDict[str, bytes]" = OrderedDict()
        self.notes: dict[str, dict[int, str]] = {}
        self.size = 0
        self.lock = threading.Lock()
        self.pending: dict[str, concurrent.futures.Future] = {}

    def get(self, key: str) -> bytes | None:
        with self.lock:
            found = self.items.get(key)
            if found is not None:
                self.items.move_to_end(key)
            return found

    def unmeshed(self, key: str) -> dict[int, str] | None:
        """The faces of a held part its mesher left without triangles, by ordinal: their
        ``BRepCheck`` verdicts. None for a part not held."""
        with self.lock:
            return self.notes.get(key) if key in self.items else None

    def drop(self, keys: Any) -> None:
        """Forget these parts' tessellations (``POST .../mesh?drop=1``: mesh them again)."""
        with self.lock:
            for key in keys:
                found = self.items.pop(key, None)
                self.notes.pop(key, None)
                if found is not None:
                    self.size -= len(found)

    def put(self, key: str, data: bytes, unmeshed: dict[int, str] | None = None) -> None:
        with self.lock:
            if key in self.items:
                return
            self.items[key] = data
            self.notes[key] = dict(unmeshed or {})
            self.size += len(data)
            while self.size > self.cap and len(self.items) > 1:
                old, dropped = self.items.popitem(last=False)
                self.notes.pop(old, None)
                self.size -= len(dropped)


# --- the pools ---------------------------------------------------------------------------
#
# The kernel's mesher holds the interpreter's lock for the whole of a call (measured: ten
# threads mesh no faster than one), so parts are meshed in parallel two ways: ``inline``,
# one part after another, each call spreading that part's faces over the kernel's own
# threads; or ``processes``, a pool started with the server and kept warm, each job one
# part's shape handed over as the kernel's binary shape bytes -- no process reads a
# document, so a large document costs its parts' bytes, not one read per process.


def _timed(shape: Any, parallel: bool) -> tuple[bytes, dict[int, str], float]:
    """One part meshed (a shape, or its ``mesh.shape_bytes``): its buffer, its unmeshed
    faces, and the seconds it took. A pool process's job."""
    from cadgen.engine.mesh import mesh_shape

    started = time.perf_counter()
    found = mesh_shape(shape, parallel=parallel)
    return found.to_bytes(), found.unmeshed, time.perf_counter() - started


def _ready() -> bool:
    """A pool process's first job: import the kernel, so the first part waits on nothing."""
    import cadgen.engine.mesh  # noqa: F401

    return True


MODES = ("inline", "processes")


class Mesher:
    def __init__(self, mode: str | None = None, workers: int | None = None) -> None:
        self.mode = mode or os.environ.get("CADGEN_ENGINE_MESH", "processes")
        if self.mode not in MODES:
            raise ValueError(f"CADGEN_ENGINE_MESH is one of {', '.join(MODES)}")
        self.workers = workers or max(1, (os.cpu_count() or 2))
        # One thread runs inline meshing: the kernel's threads are its parallelism.
        self._inline = concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="mesh")
        self._processes: concurrent.futures.ProcessPoolExecutor | None = None
        self.lock = threading.Lock()
        if self.mode == "processes":
            self.processes()

    def processes(self) -> concurrent.futures.ProcessPoolExecutor:
        with self.lock:
            if self._processes is None:
                import multiprocessing

                self._processes = concurrent.futures.ProcessPoolExecutor(
                    self.workers, mp_context=multiprocessing.get_context("spawn"))
                for _ in range(self.workers):
                    self._processes.submit(_ready)
            return self._processes

    def submit(self, shape: Any, mode: str | None = None) -> concurrent.futures.Future:
        """One part meshed: a future of its buffer, its unmeshed faces and the seconds it
        took. ``shape`` is a live shape or its ``shape_bytes``."""
        if (mode or self.mode) == "processes":
            data = shape if isinstance(shape, (bytes, bytearray)) else shape_bytes(shape)
            return self.processes().submit(_timed, bytes(data), False)
        return self._inline.submit(_timed, shape, True)

    def stop(self) -> None:
        self._inline.shutdown(wait=False, cancel_futures=True)
        if self._processes is not None:
            self._processes.shutdown(wait=False, cancel_futures=True)


def _triangles(data: bytes) -> int:
    from cadgen.engine.mesh import HEADER

    return HEADER.unpack_from(data)[3] // 3


# --- the server --------------------------------------------------------------------------


class Server:
    def __init__(self, port: int = 0, three: str | None = None, touch: Callable[[], None] = lambda: None,
                 host: str = "127.0.0.1", mesh_mode: str | None = None) -> None:
        self.documents: dict[str, Path] = {}
        self.cache = MeshCache()
        self.mesher = Mesher(mesh_mode)
        self.three = Path(three) if three else find_three()
        self.touch = touch
        self.load_lock = threading.Lock()
        self.topologies: "OrderedDict[str, Any]" = OrderedDict()
        self.topology_lock = threading.Lock()
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802
                server.touch()
                server.route(self, "GET")

            def do_POST(self) -> None:  # noqa: N802
                server.touch()
                server.route(self, "POST")

        self.http = ThreadingHTTPServer((host, port), Handler)
        self.http.daemon_threads = True
        self.port = self.http.server_address[1]
        self.url = f"http://{host}:{self.port}"
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.http.shutdown()
        self.http.server_close()
        self.mesher.stop()

    def add(self, path: Path) -> str:
        path = Path(path).resolve()
        ident = hashlib.blake2b(str(path).encode(), digest_size=6).hexdigest()
        self.documents[ident] = path
        self.document(ident)  # read now: a document that cannot be read is the caller's error
        return ident

    def listing(self) -> list[dict[str, str]]:
        return [{"id": ident, "path": str(path)} for ident, path in self.documents.items()]

    def document(self, ident: str) -> Any:
        """The document as its file is now: read again when the file changed."""
        from cadgen.engine.document import load

        path = self.documents[ident]
        with self.load_lock:
            return load(path)

    def structure(self, loaded: Any) -> dict[str, Any]:
        """The document's structure, each part held with the faces its mesher left
        without triangles (``unmeshed``, ordinals) and ``BRepCheck``'s verdict on each."""
        found = loaded.structure()
        for prototype in found["prototypes"]:
            unmeshed = self.cache.unmeshed(prototype["key"])
            if unmeshed is not None:
                prototype["unmeshed"] = sorted(unmeshed)
                prototype["unmeshedCheck"] = {str(ordinal): text for ordinal, text in sorted(unmeshed.items())}
        return found

    def topology(self, key: str, shape: Any) -> Any:
        """A part's ``mesh.Topology``, for its facts: a few held, for the process's convenience."""
        from cadgen.engine.mesh import Topology

        with self.topology_lock:
            found = self.topologies.get(key)
            if found is not None:
                self.topologies.move_to_end(key)
                return found
        found = Topology(shape)
        with self.topology_lock:
            self.topologies[key] = found
            while len(self.topologies) > 32:
                self.topologies.popitem(last=False)
        return found

    # --- routes ----------------------------------------------------------------------

    def route(self, handler: BaseHTTPRequestHandler, method: str) -> None:
        parsed = urlparse(handler.path)
        parts = [unquote(part) for part in parsed.path.split("/") if part]
        query = parse_qs(parsed.query)
        try:
            if not parts:
                return self.file(handler, PAGE / "index.html")
            if parts[0] == "page" and len(parts) == 2:
                return self.file(handler, PAGE / parts[1])
            if parts[0] == "three" and self.three is not None:
                target = (self.three / "/".join(parts[1:])).resolve()
                if self.three.resolve() in target.parents:
                    return self.file(handler, target)
            if parts[:2] == ["api", "docs"]:
                return self.json(handler, self.listing())
            if parts[:2] == ["api", "doc"] and len(parts) >= 3 and parts[2] in self.documents:
                loaded = self.document(parts[2])
                if len(parts) == 3:
                    return self.json(handler, self.structure(loaded))
                if len(parts) == 4 and parts[3] == "version":
                    return self.json(handler, {"version": f"{loaded.version[0]}-{loaded.version[1]}"})
                if len(parts) >= 5 and parts[3] == "part":
                    prototype = next((p for p in loaded.prototypes if p.key == parts[4]), None)
                    if prototype is None:
                        return self.error(handler, 404, "no such part")
                    if len(parts) == 5:
                        return self.part(handler, prototype.key, prototype.shape)
                    if len(parts) == 7 and parts[5] in ("face", "edge", "vertex") and parts[6].isdigit():
                        return self.fact(handler, prototype.key, prototype.shape, parts[5], int(parts[6]))
                if len(parts) == 4 and parts[3] == "mesh" and method == "POST":
                    mode = (query.get("mode") or [None])[0]
                    if mode is not None and mode not in MODES:
                        return self.error(handler, 400, f"mode is one of {', '.join(MODES)}")
                    if (query.get("drop") or ["0"])[0] == "1":
                        self.cache.drop(p.key for p in loaded.prototypes)
                    wanted = [(p.key, p.shape, p.name) for p in loaded.prototypes]
                    return self.json(handler, self.mesh_all(wanted, mode))
            self.error(handler, 404, "not found")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:  # noqa: BLE001 - a request's failure is its answer
            self.error(handler, 500, f"{type(error).__name__}: {error}")

    def part(self, handler: BaseHTTPRequestHandler, key: str, shape: Any) -> None:
        data = self.cache.get(key)
        if data is None:
            data = self._mesh(key, shape, None)
        self.send(handler, 200, data, "application/octet-stream", cache=True)

    def fact(self, handler: BaseHTTPRequestHandler, key: str, shape: Any, kind: str, ordinal: int) -> None:
        """What the kernel measures of one face, edge or vertex of a part, now."""
        topology = self.topology(key, shape)
        try:
            found = getattr(topology, kind)(ordinal)
        except KeyError as error:
            return self.error(handler, 404, str(error.args[0]))
        if kind == "face":
            unmeshed = self.cache.unmeshed(key)
            found["meshed"] = None if unmeshed is None else ordinal not in unmeshed
        self.json(handler, found)

    def _mesh(self, key: str, shape: Any, mode: str | None) -> bytes:
        with self.cache.lock:
            future = self.cache.pending.get(key)
            if future is None:
                future = self.cache.pending[key] = self.mesher.submit(shape, mode)
        try:
            data, unmeshed, _seconds = future.result()
        finally:
            with self.cache.lock:
                self.cache.pending.pop(key, None)
        self.cache.put(key, data, unmeshed)
        return data

    def mesh_all(self, prototypes: list[tuple[str, Any, str]], mode: str | None) -> dict[str, Any]:
        """Mesh every part of a document not held yet, all at once on the pool."""
        started = time.perf_counter()
        futures = {}
        for key, shape, name in prototypes:
            if key not in futures and self.cache.get(key) is None:
                futures[key] = (name, self.mesher.submit(shape, mode))
        timings = []
        total_bytes = triangles = unmeshed_faces = 0
        for key, (name, future) in futures.items():
            data, unmeshed, seconds = future.result()
            self.cache.put(key, data, unmeshed)
            total_bytes += len(data)
            triangles += _triangles(data)
            unmeshed_faces += len(unmeshed)
            timings.append((seconds, name))
        timings.sort(reverse=True)
        each = [seconds for seconds, _name in timings]
        return {"parts": len(prototypes), "meshed": len(futures), "seconds": round(time.perf_counter() - started, 4),
                "bytes": total_bytes, "triangles": triangles, "unmeshed": unmeshed_faces,
                "mode": mode or self.mesher.mode, "workers": self.mesher.workers,
                "per_part": {"sum": round(sum(each), 4), "max": round(each[0], 4) if each else 0.0,
                             "median": round(each[len(each) // 2], 4) if each else 0.0,
                             "slowest": [[name, round(seconds, 4)] for seconds, name in timings[:5]]}}

    # --- responses -------------------------------------------------------------------

    def file(self, handler: BaseHTTPRequestHandler, path: Path) -> None:
        if not path.is_file():
            return self.error(handler, 404, "not found")
        kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix == ".js":
            kind = "text/javascript"
        self.send(handler, 200, path.read_bytes(), kind)

    def json(self, handler: BaseHTTPRequestHandler, payload: Any) -> None:
        self.send(handler, 200, json.dumps(payload, separators=(",", ":")).encode(), "application/json")

    def error(self, handler: BaseHTTPRequestHandler, status: int, text: str) -> None:
        self.send(handler, status, json.dumps({"error": text}).encode(), "application/json")

    def send(self, handler: BaseHTTPRequestHandler, status: int, data: bytes, kind: str, *, cache: bool = False) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", kind)
        handler.send_header("Content-Length", str(len(data)))
        handler.send_header("Cache-Control", "max-age=31536000, immutable" if cache else "no-store")
        handler.end_headers()
        if handler.command != "HEAD":
            handler.wfile.write(data)
