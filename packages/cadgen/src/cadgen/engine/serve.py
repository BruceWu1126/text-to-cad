"""Serving documents over HTTP: the structure as JSON, each part as one binary buffer.

``GET /api/docs`` lists the documents served; ``GET /api/doc/<id>`` is one document's
structure (``document.Loaded.structure``): its parts with their names, keys, colours
and element names, its instances with world matrices, its tree, and its version, and
for each part already meshed the faces its mesher left without triangles
(``unmeshed``) with ``BRepCheck``'s verdict on each (``unmeshedCheck``);
``GET /api/doc/<id>/version?after=V`` answers the version once it is not ``V`` (or after
``wait`` seconds); ``GET /api/doc/<id>/part/<key>`` is one part's tessellation
(``mesh.Mesh.to_bytes``), held in memory by the part's geometry key, so a part that two
documents, two versions of one document or ten instances share is meshed once;
``GET /api/doc/<id>/part/<key>/face|edge|vertex/<ordinal>`` is what the kernel measures
of that element, now (``mesh.Topology``); ``POST /api/doc/<id>/mesh`` meshes every part
not held yet, in parallel, and answers how long that took. ``/`` is a page that shows
one (``page/``), with three.js served from a ``node_modules`` folder (``--three``, or the
nearest one above the working directory).

**The document stays in memory.** A document is read from its file once, when it is first
served. After that a model's worker hands the daemon each build's document as a patch
(``patch``): its structure, the buffers of the parts the worker meshed, and the shape
bytes of parts the daemon holds no shape for; the document is replaced in memory and the
page, waiting on the version, is answered at once. The file is read again only when some
other process rewrote it: its modification time and size are compared when the version
is asked for, except while a build is writing it (``hold``).

Meshing runs off the request's thread (``Mesher``): by default on a pool of processes
started with the server and kept warm, each handed one part's shape bytes; or ``inline``
(``CADGEN_ENGINE_MESH=inline``, or ``?mode=inline``), one part after another on one thread,
each spread over the kernel's own threads. A document's parts go to the pool largest
first, by their shape bytes (of face count, bounding diagonal and bytes, the bytes
predicted a part's mesh time best), and a part of ``PARALLEL_BYTES`` or more is meshed
with the kernel's face parallelism inside its pool process. When a page asks for a
document's structure, every part not held is sent to the pool at once, so the page's part
requests join meshes already running.
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
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

from cadgen.engine.access import allow_origin
from cadgen.engine.mesh import shape_bytes

__all__ = ["MeshCache", "PARALLEL_BYTES", "Served", "Server", "find_three"]

PAGE = Path(__file__).resolve().parent / "page"
# A part whose shape bytes reach this is meshed with the kernel's face parallelism inside
# its pool process: measured one part at a time on two large documents, parts above it
# meshed 1.1-1.5x faster with it and parts below it no faster, some slower. (A whole
# document on the pool moved within the noise of a loaded machine either way: what sets
# its wall is its largest part, often one face the kernel cannot split.)
PARALLEL_BYTES = 500_000
# How long a version request waits for a new version at most, in seconds.
WAIT_LIMIT = 60.0


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

    def keys(self) -> list[str]:
        with self.lock:
            return list(self.items)

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
                self.items.move_to_end(key)
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

    def submit(self, data: bytes, mode: str | None = None) -> concurrent.futures.Future:
        """One part meshed from its ``shape_bytes``: a future of its buffer, its unmeshed
        faces and the seconds it took."""
        if (mode or self.mode) == "processes":
            return self.processes().submit(_timed, data, len(data) >= PARALLEL_BYTES)
        return self._inline.submit(_timed, data, True)

    def stop(self) -> None:
        self._inline.shutdown(wait=False, cancel_futures=True)
        if self._processes is not None:
            self._processes.shutdown(wait=False, cancel_futures=True)


def _triangles(data: bytes) -> int:
    from cadgen.engine.mesh import HEADER

    return HEADER.unpack_from(data)[3] // 3


# --- the documents -----------------------------------------------------------------------


@dataclass
class Served:
    """One document as the server holds it: its structure (no meshing facts), its
    version, the file state it reflects, and each part's shape by key -- a live shape when
    the document was read from its file, the shape's bytes when a worker sent it."""

    ident: str
    path: Path
    structure: dict[str, Any]
    stat: tuple | None
    shapes: dict[str, Any]
    names: dict[str, str]
    patch: dict[str, Any] | None = None
    data: dict[str, bytes] = field(default_factory=dict)  # shape bytes made from live shapes

    @property
    def version(self) -> str:
        return self.structure["version"]

    def shape_data(self, key: str) -> bytes | None:
        """A part's shape bytes: made once from a live shape."""
        found = self.shapes.get(key)
        if found is None or isinstance(found, (bytes, bytearray)):
            return found
        made = self.data.get(key)
        if made is None:
            made = self.data[key] = shape_bytes(found)
        return made


def _stat(path: Path) -> tuple | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


# --- the server --------------------------------------------------------------------------


class Server:
    def __init__(self, port: int = 0, three: str | None = None, touch: Callable[[], None] = lambda: None,
                 host: str = "127.0.0.1", mesh_mode: str | None = None) -> None:
        self.served: dict[str, Served] = {}
        self.idents: dict[Path, str] = {}
        self.cache = MeshCache()
        self.mesher = Mesher(mesh_mode)
        self.three = Path(three) if three else find_three()
        self.touch = touch
        self.load_lock = threading.RLock()
        self.held: dict[Path, int] = {}
        self.changed = threading.Condition()
        self.generation = 0
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
        if self.thread is not None:  # shutdown waits for a loop that runs
            self.http.shutdown()
        self.http.server_close()
        self.mesher.stop()

    # --- documents -------------------------------------------------------------------

    def add(self, path: Path) -> str:
        path = Path(path).resolve()
        ident = hashlib.blake2b(str(path).encode(), digest_size=6).hexdigest()
        with self.load_lock:
            if ident not in self.served:
                self.served[ident] = self._read(ident, path)  # a document that cannot be read is the caller's error
                self.idents[path] = ident
        return ident

    def listing(self) -> list[dict[str, str]]:
        return [{"id": ident, "path": str(doc.path)} for ident, doc in self.served.items()]

    def versions(self) -> dict[str, str]:
        """Every served path and the version served of it."""
        with self.load_lock:
            return {str(doc.path): doc.version for doc in self.served.values()}

    def served_keys(self) -> dict[str, list[str]]:
        """Every served path and the keys of the parts the document served of it holds:
        what a build of it changed is what these lack."""
        with self.load_lock:
            return {str(doc.path): sorted(doc.shapes) for doc in self.served.values()}

    def shape_keys(self) -> list[str]:
        """The keys of every part whose shape a served document holds."""
        with self.load_lock:
            return sorted({key for doc in self.served.values() for key, shape in doc.shapes.items() if shape is not None})

    def _read(self, ident: str, path: Path) -> Served:
        from cadgen.engine.document import Loaded

        loaded = Loaded(path)
        return Served(ident, path, loaded.structure(), loaded.version, {p.key: p.shape for p in loaded.prototypes},
                      {p.key: p.name for p in loaded.prototypes})

    def document(self, ident: str) -> Served:
        """The document as the server holds it, read from its file again only when some
        other process rewrote the file (and no build is writing it now)."""
        with self.load_lock:
            doc = self.served[ident]
            if self.held.get(doc.path):
                return doc
            stat = _stat(doc.path)
            if stat is None or stat == doc.stat:
                return doc
            doc = self.served[ident] = self._read(ident, doc.path)
        self._changed()
        return doc

    def hold(self, paths: list[str]) -> list[Path]:
        """Paths a build is writing: their files are not read again until ``release``."""
        held = []
        with self.load_lock:
            for text in paths:
                path = Path(text).resolve()
                if path in self.idents:
                    self.held[path] = self.held.get(path, 0) + 1
                    held.append(path)
        return held

    def release(self, paths: list[Path]) -> None:
        with self.load_lock:
            for path in paths:
                left = self.held.get(path, 0) - 1
                if left > 0:
                    self.held[path] = left
                else:
                    self.held.pop(path, None)
        self._changed()

    def _changed(self) -> None:
        with self.changed:
            self.generation += 1
            self.changed.notify_all()

    def patch(self, header: dict[str, Any], payload: bytes, arrived: float) -> dict[str, Any] | None:
        """A worker's document for one served path (see the module docstring): the new
        structure in place of the old, the buffers it brought held, its shapes kept; every
        part neither held nor brought is sent to the pool. Returns what it did, or None for
        a path not served."""
        started = time.perf_counter()
        path = Path(header["path"]).resolve()
        ident = self.idents.get(path)
        if ident is None:
            return None
        view = memoryview(payload)
        at = 0
        for key, size, unmeshed in header.get("meshes") or []:
            self.cache.put(key, bytes(view[at:at + size]), {int(o): text for o, text in unmeshed.items()})
            at += size
        brought: dict[str, bytes] = {}
        for key, size in header.get("shapes") or []:
            brought[key] = bytes(view[at:at + size])
            at += size
        structure = header["structure"]
        mtime, _sep, size = structure["version"].partition("-")
        with self.load_lock:
            old = self.served[ident]
            shapes: dict[str, Any] = {}
            names: dict[str, str] = {}
            for prototype in structure["prototypes"]:
                key = prototype["key"]
                shapes[key] = brought.get(key) or old.shapes.get(key) or self._shape(key)
                names[key] = prototype["name"]
            data = {key: made for key, made in old.data.items() if key in shapes}
            doc = self.served[ident] = Served(ident, path, structure, (int(mtime), int(size)), shapes, names, None, data)
            applied = time.perf_counter()
            doc.patch = {"worker": header.get("timings") or {}, "arrived": arrived, "applied": time.time(),
                         "seconds": round(applied - started, 4), "meshes": len(header.get("meshes") or []),
                         "shapes": len(brought), "bytes": len(payload)}
        self._changed()
        self.prefetch(doc)
        return doc.patch

    def _shape(self, key: str) -> Any:
        for doc in self.served.values():
            found = doc.shapes.get(key)
            if found is not None:
                return found
        return None

    def shape_of(self, doc: Served, key: str) -> Any:
        """A part's shape bytes, from the document, or -- for one no process sent -- from
        its file, read once to fill every shape it lacks."""
        found = doc.shape_data(key)
        if found is None:
            with self.load_lock:
                read = self._read(doc.ident, doc.path)
                for missing, shape in read.shapes.items():
                    doc.shapes.setdefault(missing, shape)
                    if doc.shapes[missing] is None:
                        doc.shapes[missing] = shape
            found = doc.shape_data(key)
        if found is None:
            raise KeyError(f"no shape for part {key}")
        return found

    def structure(self, doc: Served) -> dict[str, Any]:
        """The document's structure, each part held with the faces its mesher left
        without triangles (``unmeshed``, ordinals) and ``BRepCheck``'s verdict on each."""
        found = dict(doc.structure)
        found["prototypes"] = prototypes = []
        for prototype in doc.structure["prototypes"]:
            unmeshed = self.cache.unmeshed(prototype["key"])
            if unmeshed is not None:
                prototype = {**prototype, "unmeshed": sorted(unmeshed),
                             "unmeshedCheck": {str(ordinal): text for ordinal, text in sorted(unmeshed.items())}}
            prototypes.append(prototype)
        return found

    def wait(self, ident: str, after: str | None, seconds: float) -> Served:
        """The document once its version is not ``after``, or after ``seconds``."""
        deadline = time.monotonic() + seconds
        while True:
            generation = self.generation
            doc = self.document(ident)
            left = deadline - time.monotonic()
            if after is None or doc.version != after or left <= 0:
                return doc
            with self.changed:
                # Woken by a patch or a release; the file is looked at twice a second.
                self.changed.wait_for(lambda: self.generation != generation, timeout=min(left, 0.5))

    def topology(self, key: str, data: bytes) -> Any:
        """A part's ``mesh.Topology``, for its facts: a few held, for the process's convenience."""
        from cadgen.engine.mesh import Topology

        with self.topology_lock:
            found = self.topologies.get(key)
            if found is not None:
                self.topologies.move_to_end(key)
                return found
        found = Topology(data)
        with self.topology_lock:
            self.topologies[key] = found
            while len(self.topologies) > 32:
                self.topologies.popitem(last=False)
        return found

    # --- meshing ---------------------------------------------------------------------

    def _submit(self, key: str, data: bytes, mode: str | None) -> concurrent.futures.Future:
        """The part's mesh, running or started: a future of its buffer, unmeshed faces and
        seconds, whose result is held as soon as it is ready."""
        with self.cache.lock:
            future = self.cache.pending.get(key)
            if future is not None:
                return future
            future = self.cache.pending[key] = self.mesher.submit(data, mode)

        def settle(done: concurrent.futures.Future) -> None:
            if not done.cancelled() and done.exception() is None:
                buffer, unmeshed, _seconds = done.result()
                self.cache.put(key, buffer, unmeshed)
            with self.cache.lock:
                if self.cache.pending.get(key) is done:
                    del self.cache.pending[key]

        future.add_done_callback(settle)
        return future

    def _jobs(self, doc: Served) -> list[tuple[str, bytes]]:
        """The document's parts not held, each once, largest first."""
        jobs = {}
        for key in doc.shapes:
            if key not in jobs and self.cache.get(key) is None:
                jobs[key] = self.shape_of(doc, key)
        return sorted(jobs.items(), key=lambda item: -len(item[1]))

    def prefetch(self, doc: Served) -> None:
        """Send every part of the document not held to the mesher, largest first, now."""
        for key, data in self._jobs(doc):
            self._submit(key, data, None)

    def mesh_all(self, doc: Served, mode: str | None) -> dict[str, Any]:
        """Mesh every part of a document not held yet, all at once, largest first."""
        started = time.perf_counter()
        jobs = self._jobs(doc)
        futures = {key: self._submit(key, data, mode) for key, data in jobs}
        timings = []
        total_bytes = triangles = unmeshed_faces = 0
        for key, future in futures.items():
            data, unmeshed, seconds = future.result()
            total_bytes += len(data)
            triangles += _triangles(data)
            unmeshed_faces += len(unmeshed)
            timings.append((seconds, doc.names.get(key, key)))
        timings.sort(reverse=True)
        each = [seconds for seconds, _name in timings]
        return {"parts": len(doc.shapes), "meshed": len(futures), "seconds": round(time.perf_counter() - started, 4),
                "bytes": total_bytes, "triangles": triangles, "unmeshed": unmeshed_faces,
                "mode": mode or self.mesher.mode, "workers": self.mesher.workers,
                "per_part": {"sum": round(sum(each), 4), "max": round(each[0], 4) if each else 0.0,
                             "median": round(each[len(each) // 2], 4) if each else 0.0,
                             "slowest": [[name, round(seconds, 4)] for seconds, name in timings[:5]]}}

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
            if parts[:2] == ["api", "doc"] and len(parts) >= 3 and parts[2] in self.served:
                if len(parts) == 4 and parts[3] == "version":
                    after = (query.get("after") or [None])[0]
                    seconds = min(float((query.get("wait") or ["0"])[0] or 0), WAIT_LIMIT)
                    doc = self.wait(parts[2], after, seconds)
                    return self.json(handler, {"version": doc.version, "patch": doc.patch})
                doc = self.document(parts[2])
                if len(parts) == 3:
                    self.prefetch(doc)  # a page is about to ask for every part
                    return self.json(handler, self.structure(doc))
                if len(parts) >= 5 and parts[3] == "part":
                    key = parts[4]
                    if key not in doc.shapes:
                        return self.error(handler, 404, "no such part")
                    if len(parts) == 5:
                        return self.part(handler, doc, key)
                    if len(parts) == 7 and parts[5] in ("face", "edge", "vertex") and parts[6].isdigit():
                        return self.fact(handler, doc, key, parts[5], int(parts[6]))
                if len(parts) == 4 and parts[3] == "mesh" and method == "POST":
                    mode = (query.get("mode") or [None])[0]
                    if mode is not None and mode not in MODES:
                        return self.error(handler, 400, f"mode is one of {', '.join(MODES)}")
                    if (query.get("drop") or ["0"])[0] == "1":
                        self.cache.drop(doc.shapes)
                    return self.json(handler, self.mesh_all(doc, mode))
            self.error(handler, 404, "not found")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:  # noqa: BLE001 - a request's failure is its answer
            self.error(handler, 500, f"{type(error).__name__}: {error}")

    def part(self, handler: BaseHTTPRequestHandler, doc: Served, key: str) -> None:
        data = self.cache.get(key)
        if data is None:
            data = self._submit(key, self.shape_of(doc, key), None).result()[0]
        self.send(handler, 200, data, "application/octet-stream", cache=True)

    def fact(self, handler: BaseHTTPRequestHandler, doc: Served, key: str, kind: str, ordinal: int) -> None:
        """What the kernel measures of one face, edge or vertex of a part, now."""
        topology = self.topology(key, self.shape_of(doc, key))
        try:
            found = getattr(topology, kind)(ordinal)
        except KeyError as error:
            return self.error(handler, 404, str(error.args[0]))
        if kind == "face":
            unmeshed = self.cache.unmeshed(key)
            found["meshed"] = None if unmeshed is None else ordinal not in unmeshed
        self.json(handler, found)

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
        allow_origin(handler)  # a page on this machine reads documents from here directly (access.py)
        handler.end_headers()
        if handler.command != "HEAD":
            handler.wfile.write(data)
