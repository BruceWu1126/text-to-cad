"""Serving documents over HTTP: the structure as JSON, each part as one binary buffer.

``GET /api/docs`` lists the documents served; ``GET /api/doc/<id>`` is one document's
structure (``document.Loaded.structure``): its parts with their names, keys, colours,
materials and element names, its instances with world matrices, its tree, and its version, and
for each part already meshed the faces its mesher left without triangles
(``unmeshed``) with ``BRepCheck``'s verdict on each (``unmeshedCheck``);
``GET /api/doc/<id>/version?after=V&progress=N&wait=S`` answers ``{"version", "patch",
"building", "failed", "progress"}`` once the version is not ``V``, or the count ``progress`` is
not ``N`` (without ``progress=``, the version alone is waited on), or after ``S`` seconds:
``building`` is the last progress frame of the build writing the document now (``progress.py``;
null when none is), ``failed`` the last build of it that failed (``{"model", "error",
"progress"}``: its error and where it was; null once one succeeds or another begins), and
``progress`` a count that moves whenever either does (``building``, ``built``), which the page
sends back to wait for the next; ``GET /api/doc/<id>/part/<key>`` is one part's tessellation
(``mesh.Mesh.to_bytes``), held in memory by the part's geometry key, so a part that two
documents, two versions of one document or ten instances share is meshed once;
``GET /api/doc/<id>/part/<key>/face|edge|vertex/<ordinal>`` is what the kernel measures
of that element, now (``mesh.Topology``), and ``made_by``: the element's name, the feature
that made it, its operation, and the function, file and line of that feature's call,
asked of the worker that built the document (``provenance``) -- never read from a file, so
a document served cold, with no live worker that built it, answers null;
``GET /api/doc/<id>/part/<key>/groups`` is how a pick of one face or edge grows (each face's
tangent group, each edge's chain: ``mesh.Topology.groups``); ``.../volume`` the part's volume
in cubic millimetres, what its mass is weighed from (``mesh.Topology.volume``); ``POST /api/doc/<id>/mesh`` meshes every part
not held yet, in parallel, and answers how long that took. ``POST /api/doc/<id>/pose``
with ``{"pose": NAME | {joint: value}}`` serves the document at that pose under an id of
its own (``<id>~<digest>``): its structure's instances where the mechanism places them,
every other route the document's; what the pose names wrong is refused by name (400).
The structure carries the document's mechanism as ``kinematics`` (``mechanism.block``),
from which a page poses it itself. ``GET /api/look`` is the look the regular display modes
draw a document in (``render/look.py``), which a page lights and dresses its scene by, and
``GET /api/look/environment`` the image its environment is. ``/`` is a page that shows
one (``page/``), with three.js served from a ``node_modules`` folder (``--three``, or the
nearest one above the working directory).

**The document stays in memory.** A document is read from its file once, when it is first
served. After that a model's worker hands the daemon each build's document as a patch
(``patch``): its structure, the buffers of the parts the worker meshed, and the shape
bytes of parts the daemon holds no shape for; the document is replaced in memory and the
page, waiting on the version, is answered at once. The file is read again only when some
other process rewrote it: its modification time and size are compared when the version
is asked for, except while a build is writing it (``hold``).

**A STEP is read in a reader process, held by its bytes.** OCCT's XCAF reader works in many
times the memory of what it makes and its allocator keeps most of it, so the server never
parses a ``.step``/``.stp`` itself: a reader process of its own does (``_read_foreign``, one
read per process, the next one started and warm meanwhile) and answers the structure and each
part's shape bytes, which are all the server keeps. The read is held by the file's bytes
(``FOREIGN_HELD`` of them): the same bytes served again -- the file touched, copied, or put
back -- are not parsed again while the server lives, and their parts' keys (and so their
meshes) are the same. A STEP is read outside the server's lock, so every other document is
served meanwhile; one rewritten while it is served is read again in the background, and the
page keeps the old version until the new one is in. Its structure says how long the parse
took and which process made it (``read``).

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
from cadgen.engine.render.look import LOOK

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
# A file this engine did not write, read by a reader process (the module docstring).
FOREIGN = (".step", ".stp")
# How many STEP reads are held by their bytes: a read a served document holds stays in memory
# with it whatever this says; past it, the least recently served read goes.
FOREIGN_HELD = 8


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


# The process's one mesh cache: the daemon's server serves from it, and in a worker the
# build's patch, its exports and a snapshot it draws all take a part's buffer from it.
MESHES = MeshCache()


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


# --- a STEP's reader process ----------------------------------------------------------------


def _reader_ready() -> None:
    """A reader process's start: the kernel and the reader imported, so its read waits on nothing."""
    import cadgen.engine.document  # noqa: F401
    import cadgen.engine.mesh  # noqa: F401


def _read_foreign(path: str, digest: str) -> dict[str, Any]:
    """A STEP read into a document in a reader process (the module docstring): its structure,
    each part's shape bytes by a key made from the file's bytes (``digest``) and the part's label,
    whether the document is named by the file's stem (several free shapes), the seconds the read
    took and the process that made it. A reader process's job."""
    from OCP.TDF import TDF_LabelSequence
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    from cadgen.engine.document import Loaded

    started = time.perf_counter()
    loaded = Loaded(Path(path))
    keys = {p.key: f"step-{digest[:20]}-{p.entry}" for p in loaded.prototypes}
    structure = loaded.structure()
    structure["prototypes"] = [{**prototype, "key": keys[prototype["key"]]} for prototype in structure["prototypes"]]
    shapes = {keys[p.key]: shape_bytes(p.shape, read=True) for p in loaded.prototypes}
    free = TDF_LabelSequence()
    XCAFDoc_DocumentTool.ShapeTool_s(loaded.doc.Main()).GetFreeShapes(free)
    return {"structure": structure, "shapes": shapes, "stem": free.Length() != 1,
            "seconds": round(time.perf_counter() - started, 4), "process": os.getpid()}


def _digest(path: Path) -> str:
    """The file's bytes, digested: what a STEP's read is held by."""
    found = hashlib.blake2b(digest_size=20)
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            found.update(chunk)
    return found.hexdigest()


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
        """A part's shape bytes: made once from the shape its file read gave."""
        found = self.shapes.get(key)
        if found is None or isinstance(found, (bytes, bytearray)):
            return found
        made = self.data.get(key)
        if made is None:
            made = self.data[key] = shape_bytes(found, read=True)
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
                 host: str = "127.0.0.1", mesh_mode: str | None = None,
                 provenance: Callable[[str, str, str, int], dict[str, Any] | None] | None = None,
                 cache: MeshCache | None = None) -> None:
        self.served: dict[str, Served] = {}
        self.idents: dict[Path, str] = {}
        # The daemon's server and a worker's snapshot serve from the process's one cache
        # (``MESHES``); a server of its own (a test's) holds its own.
        self.cache = cache if cache is not None else MeshCache()
        self.mesher = Mesher(mesh_mode)
        self.three = Path(three) if three else find_three()
        self.touch = touch
        self.provenance = provenance
        self.load_lock = threading.RLock()
        self.held: dict[Path, int] = {}
        self.changed = threading.Condition()
        self.generation = 0
        self.topologies: "OrderedDict[str, Any]" = OrderedDict()
        self.topology_lock = threading.Lock()
        # A document served at a pose (``POST /api/doc/<id>/pose``): its id, the document's and the pose.
        self.poses: dict[str, tuple[str, Any]] = {}
        # STEP reads (the module docstring): held by the file's bytes, each read once however
        # many ask for it at a time, by a reader process started on the first.
        self.foreign: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
        self.foreign_reads: dict[str, concurrent.futures.Future] = {}
        self.foreign_lock = threading.Lock()
        self.reader: concurrent.futures.ProcessPoolExecutor | None = None
        # A served STEP being read again in the background, and one whose file could not be read
        # as it stands (by its state): it is read again once the file changes.
        self.rereading: set[str] = set()
        self.unreadable: dict[str, tuple | None] = {}
        # Per served path, its build: the last progress frame of the build writing it now, the
        # last build of it that failed, and a count that moves with either (``build_of``).
        self.builds: dict[Path, dict[str, Any]] = {}
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
        with self.foreign_lock:
            reader, self.reader = self.reader, None
        if reader is not None:
            reader.shutdown(wait=False, cancel_futures=True)

    # --- documents -------------------------------------------------------------------

    def add(self, path: Path) -> str:
        path = Path(path).resolve()
        ident = hashlib.blake2b(str(path).encode(), digest_size=6).hexdigest()
        if path.suffix.lower() in FOREIGN:
            # Read outside the lock: a STEP's read is long, and every other document is served
            # meanwhile. Two first opens of one file share its one read (``_foreign``).
            with self.load_lock:
                if ident in self.served:
                    return ident
            served = self._read(ident, path)  # a document that cannot be read is the caller's error
            with self.load_lock:
                if ident not in self.served:
                    self.served[ident] = served
                    self.idents[path] = ident
            return ident
        with self.load_lock:
            if ident not in self.served:
                self.served[ident] = self._read(ident, path)  # a document that cannot be read is the caller's error
                self.idents[path] = ident
        return ident

    def put(self, path: Path, structure: dict[str, Any], shapes: dict[str, Any]) -> str:
        """Serve a document held in memory (a build's, which a worker draws): its structure
        and each part's shape (or shape bytes) by key. No file is read for it."""
        path = Path(path).resolve()
        ident = hashlib.blake2b(str(path).encode(), digest_size=6).hexdigest()
        with self.load_lock:
            self.served[ident] = Served(ident, path, structure, _stat(path), dict(shapes),
                                        {p["key"]: p["name"] for p in structure["prototypes"]})
            self.idents[path] = ident
        self._changed()
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
        if path.suffix.lower() in FOREIGN:
            return self._read_step(ident, path)
        from cadgen.engine.document import Loaded

        loaded = Loaded(path)
        return Served(ident, path, loaded.structure(), loaded.version, {p.key: p.shape for p in loaded.prototypes},
                      {p.key: p.name for p in loaded.prototypes})

    def _read_step(self, ident: str, path: Path) -> Served:
        """A STEP served: its read (``_foreign``), named and versioned as this file."""
        read, stat = self._foreign(path)
        structure = {**read["structure"], "path": str(path), "version": f"{stat[0]}-{stat[1]}",
                     "read": {"seconds": read["seconds"], "process": read["process"]}}
        if read["stem"]:  # several free shapes: the document is named by its file
            structure["name"] = path.stem
            structure["tree"] = {**structure["tree"], "name": path.stem}
        return Served(ident, path, structure, stat, dict(read["shapes"]),
                      {prototype["key"]: prototype["name"] for prototype in structure["prototypes"]})

    def _readers(self) -> concurrent.futures.ProcessPoolExecutor:
        """The reader processes: one at a time, each gone after its read (with what the parse
        left in its memory), the next started and warm meanwhile."""
        with self.foreign_lock:
            if self.reader is None:
                import multiprocessing

                self.reader = concurrent.futures.ProcessPoolExecutor(
                    1, mp_context=multiprocessing.get_context("spawn"), initializer=_reader_ready, max_tasks_per_child=1)
            return self.reader

    def _foreign(self, path: Path) -> tuple[dict[str, Any], tuple]:
        """The read of the STEP at ``path`` and the file state it is of: held by the file's
        bytes, else read by a reader process -- once, however many ask for it meanwhile."""
        stat = _stat(path)
        if stat is None:
            raise FileNotFoundError(f"no document {path}")
        digest = _digest(path)
        with self.foreign_lock:
            found = self.foreign.get(digest)
            if found is not None:
                self.foreign.move_to_end(digest)
                return found, stat
            reading = self.foreign_reads.get(digest)
            owner = reading is None
            if owner:
                reading = self.foreign_reads[digest] = concurrent.futures.Future()
        if not owner:
            return reading.result(), stat
        try:
            try:
                read = self._readers().submit(_read_foreign, str(path), digest).result()
            except concurrent.futures.BrokenExecutor:  # the reader died mid-read
                with self.foreign_lock:
                    self.reader = None  # the next read starts a reader afresh
                raise ValueError(f"the STEP reader exited reading {path.name}") from None
        except BaseException as error:
            with self.foreign_lock:
                self.foreign_reads.pop(digest, None)
            reading.set_exception(error)
            raise
        with self.foreign_lock:
            self.foreign_reads.pop(digest, None)
            if _stat(path) == stat:  # held only when the bytes read are the bytes digested
                self.foreign[digest] = read
                while len(self.foreign) > FOREIGN_HELD:
                    self.foreign.popitem(last=False)
        reading.set_result(read)
        return read, stat

    def document(self, ident: str) -> Served:
        """The document as the server holds it, read from its file again only when some
        other process rewrote the file (and no build is writing it now). A STEP is read
        again in the background (the module docstring): until it is in, this is the last."""
        with self.load_lock:
            doc = self.served[ident]
            if self.held.get(doc.path):
                return doc
            stat = _stat(doc.path)
            if stat is None or stat == doc.stat:
                return doc
            if doc.path.suffix.lower() in FOREIGN:
                if ident not in self.rereading and self.unreadable.get(ident, ()) != stat:
                    self.rereading.add(ident)
                    threading.Thread(target=self._reread, args=(ident, doc, stat), daemon=True).start()
                return doc
            doc = self.served[ident] = self._read(ident, doc.path)
        self._changed()
        return doc

    def _reread(self, ident: str, doc: Served, stat: tuple) -> None:
        """A served STEP whose file changed, read again and put in place of ``doc``: the page
        waiting on the version is answered then. A file that cannot be read as it stands (a
        writer midway, garbage) is tried again once it changes."""
        fresh = None
        try:
            fresh = self._read(ident, doc.path)
        except Exception:  # noqa: BLE001 - the page keeps the version it has
            pass
        with self.load_lock:
            self.rereading.discard(ident)
            if fresh is None:
                self.unreadable[ident] = stat
            else:
                self.unreadable.pop(ident, None)
                if self.served.get(ident) is doc:
                    self.served[ident] = fresh
        self._changed()

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

    def building(self, frame: dict[str, Any]) -> None:
        """A build's progress frame (``progress.py``): each served document among its
        ``outputs`` is being built, as the frame says, and its pages are woken."""
        paths = []
        for text in frame.get("outputs") or []:
            try:
                paths.append(Path(text).resolve())
            except (OSError, ValueError):
                continue
        with self.load_lock:
            hit = [path for path in paths if path in self.idents]
            for path in hit:
                state = self.builds.setdefault(path, {"building": None, "failed": None, "progress": 0})
                state.update(building=frame, failed=None, progress=state["progress"] + 1)
        if hit:
            self._changed()

    def built(self, outcomes: dict[str, dict[str, Any] | None]) -> None:
        """Builds that answered: per path they wrote, None when its model built, else what failed
        (``{"model", "error", "progress"}``). Those served are built no longer; their pages are woken."""
        hit = False
        with self.load_lock:
            for text, failed in outcomes.items():
                try:
                    path = Path(text).resolve()
                except (OSError, ValueError):
                    continue
                if path not in self.idents:
                    continue
                state = self.builds.setdefault(path, {"building": None, "failed": None, "progress": 0})
                state.update(building=None, failed=failed, progress=state["progress"] + 1)
                hit = True
        if hit:
            self._changed()

    def build_of(self, path: Path) -> dict[str, Any]:
        """A served document's build: ``building``, ``failed`` and their ``progress`` count."""
        with self.load_lock:
            return dict(self.builds.get(path) or {"building": None, "failed": None, "progress": 0})

    def wait(self, ident: str, after: str | None, seconds: float, progress: int | None = None) -> Served:
        """The document once its version is not ``after``, or its build's ``progress`` count is
        not ``progress`` (when given), or after ``seconds``."""
        deadline = time.monotonic() + seconds
        while True:
            generation = self.generation
            doc = self.document(ident)
            left = deadline - time.monotonic()
            if after is None or doc.version != after or left <= 0 or (
                    progress is not None and self.build_of(doc.path)["progress"] != progress):
                return doc
            with self.changed:
                # Woken by a patch, a release or a build's progress; the file is looked at twice a second.
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
            if parts == ["api", "look"]:
                return self.json(handler, LOOK.json())
            if parts == ["api", "look", "environment"]:
                return self.send(handler, 200, LOOK.environment_path.read_bytes(), "image/vnd.radiance")
            ident, pose = self.poses.get(parts[2], (parts[2], None)) if len(parts) >= 3 else ("", None)
            if parts[:2] == ["api", "doc"] and ident in self.served:
                if len(parts) == 4 and parts[3] == "version":
                    after = (query.get("after") or [None])[0]
                    seconds = min(float((query.get("wait") or ["0"])[0] or 0), WAIT_LIMIT)
                    given = (query.get("progress") or [""])[0]
                    doc = self.wait(ident, after, seconds, int(given) if given.isdigit() else None)
                    build = self.build_of(doc.path)
                    return self.json(handler, {"version": doc.version, "patch": doc.patch, **build})
                doc = self.document(ident)
                if len(parts) == 3:
                    self.prefetch(doc)  # a page is about to ask for every part
                    return self.json(handler, self.posed(doc, pose))
                if len(parts) == 4 and parts[3] == "pose" and method == "POST":
                    return self.pose(handler, doc)
                if len(parts) >= 5 and parts[3] == "part":
                    key = parts[4]
                    if key not in doc.shapes:
                        return self.error(handler, 404, "no such part")
                    if len(parts) == 5:
                        return self.part(handler, doc, key)
                    if len(parts) == 7 and parts[5] in ("face", "edge", "vertex") and parts[6].isdigit():
                        return self.fact(handler, doc, key, parts[5], int(parts[6]))
                    if len(parts) == 6 and parts[5] == "groups":
                        return self.json(handler, self.topology(key, self.shape_of(doc, key)).groups())
                    if len(parts) == 6 and parts[5] == "volume":
                        return self.json(handler, self.topology(key, self.shape_of(doc, key)).volume())
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
        except ValueError as error:  # a pose refused by name (``mechanism.KinematicsError``), a body not JSON
            self.error(handler, 400, str(error))
        except Exception as error:  # noqa: BLE001 - a request's failure is its answer
            self.error(handler, 500, f"{type(error).__name__}: {error}")

    def posed(self, doc: Served, pose: Any) -> dict[str, Any]:
        """The document's structure, at ``pose`` where one is given: its instances where the
        mechanism places them there (``mechanism.posed_structure``)."""
        structure = self.structure(doc)
        if pose is None:
            return structure
        from cadgen.engine.mechanism import posed_structure

        return posed_structure(structure, pose)

    def pose(self, handler: BaseHTTPRequestHandler, doc: Served) -> None:
        """``POST /api/doc/<id>/pose`` with ``{"pose": NAME | {joint: value}}``: the document
        served at that pose under an id of its own, its parts the document's. An unknown
        configuration or joint, a value out of range and a mechanism that is not a tree are
        refused by name (400) before the id exists."""
        length = int(handler.headers.get("Content-Length") or 0)
        try:
            pose = json.loads(handler.rfile.read(length) or b"{}").get("pose")
        except (json.JSONDecodeError, AttributeError):
            raise ValueError("a pose request is {\"pose\": a configuration's name or {joint: value}}") from None
        if not isinstance(pose, (str, dict)):
            raise ValueError("a pose is a configuration's name or an object of joint values")
        posed = self.posed(doc, pose)
        if "kinematics" not in posed:
            raise ValueError("the document has no joints to pose")
        digest = hashlib.blake2b(json.dumps(pose, sort_keys=True).encode(), digest_size=6).hexdigest()
        ident = f"{doc.ident}~{digest}"
        self.poses[ident] = (doc.ident, pose)
        self.json(handler, {"id": ident, "url": f"{self.url}/api/doc/{ident}", **posed["kinematics"]["pose"]})

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
        found["made_by"] = self.made_by(doc, key, kind, ordinal)
        self.json(handler, found)

    def made_by(self, doc: Served, key: str, kind: str, ordinal: int) -> dict[str, Any] | None:
        """What made the element, from the worker that built the document (``provenance``),
        when the name it gives is the one the served document holds: None for a document
        no live worker built, or whose served version that worker's last build is not."""
        if self.provenance is None:
            return None
        try:
            found = self.provenance(str(doc.path), key, kind, ordinal)
        except Exception:  # noqa: BLE001 - provenance never fails the facts
            return None
        prototype = next((p for p in doc.structure["prototypes"] if p["key"] == key), None)
        if not found or prototype is None or prototype.get(f"{kind}Names", {}).get(str(ordinal)) != found.get("name"):
            return None
        return found

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
