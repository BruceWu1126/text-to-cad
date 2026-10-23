"""A warm worker: one process per model script, the memo living in it for its life.

The daemon starts one per script and talks to it over the worker's stdin and its
original stdout. A request is one JSON object per line: ``{"id", "op": "build", "script",
"functions", "cwd"}``, and, when the daemon serves documents, ``served`` (each served
path and the version served of it), ``served_keys`` (each served path and the keys of the
parts its served document holds), ``held`` (the geometry keys it holds a mesh for) and
``shapes`` (the keys it holds a shape for). The answers are frames: a JSON object on one
line and, when it has ``bytes``, exactly that many bytes after it. A build is answered by
``{"id", "log"}`` frames while it builds and ``{"id", "progress"}`` frames saying where it is
(``progress.py``: each stage, ``report`` and ``track``), a ``{"id", "patch", "bytes"}`` frame (below) for
each served xbf the moment the build has put it in place -- before the build goes on to
its exports -- one ``{"id", "done", "rssMb"}`` (the build's reports, and the worker's resident
memory after it, in MiB) or ``{"id", "failed"}``, and last ``{"id", "settled"}``. What the
model prints goes to the worker's stderr, which the daemon passes on. The worker exits when
its stdin closes: the daemon's pool closes it to retire the worker (``daemon.Pool``).

A ``{"id", "op": "where", "path", "key", "kind", "ordinal"}`` request asks what made one
element of a document this worker built (``build.made_by``): it is answered at once,
``{"id", "where": {...} | null}``, by the thread that reads the requests -- during a
build too, from the last build that named the document -- so a page asking about an
element never waits on a build.

A patch is the document as the daemon serves it, made in this process from the tree the
build saved (``document.tree_structure``), so the daemon never reads the file it was
saved as: ``patch.path``, ``patch.structure``, ``patch.meshes`` -- ``[key, size,
unmeshed]`` for each part the build changed: one the served document does not have and
whose mesh the daemon does not hold, meshed here (``mesh.mesh_shape``, the kernel's face parallelism on) --
and ``patch.shapes`` -- ``[key, size]`` for each part whose shape the daemon does not
hold, as ``mesh.shape_bytes``; the payload is the meshes' buffers, then the shapes' bytes,
in that order. A part is meshed from the very bytes that are sent, so a mesh the daemon
makes again of it is the same; and they are the part's bytes as its xbf reads back
(``mesh.shape_bytes``), so a daemon that only read the file meshes the key alike. Only what the build changed is meshed here, and only for a
document the daemon serves: a build of a document nobody serves meshes nothing, and the
parts of a served one the build did not change are meshed by the daemon's pool, when they
are asked for -- the build's answer never waits on a whole document's mesh. Each report
says how many parts the build meshed (``meshed``).

A worker whose resident memory is over its cap after a build (``CADGEN_ENGINE_WORKER_RSS_MB``,
by default a quarter of the machine's memory) is retired: its reports say so (``retired``),
and it exits once it has answered. The daemon starts a fresh one for the next build, a
restart: a model whose xbf holds its closure unchanged is taken in seconds.

A build runs with the cyclic collector off, and the worker collects once after it has
answered: the memo keeps every live result of the last builds, millions of objects, and a
full collection walks all of them -- a second on a large model, paid inside whichever call
allocates when it falls due. Off the build's path it is paid while the worker is idle.
"""

from __future__ import annotations

import gc
import json
import os
import queue
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable

# How long a build waits for the daemon to say it holds a patch before it writes its
# derived files: the viewer is woken first, unless the daemon is too slow to say so.
PATCH_WAIT = 30.0
# The daemon's word that it holds the patch of a path (``{"op": "patched", "path"}``).
_PATCHED: dict[str, threading.Event] = {}


class Patcher:
    """One build's patches (see the module docstring): ``saved`` is called the moment the
    build has put an xbf in place, and returns once the daemon holds the patch -- so the
    viewer's version has moved before any derived file is written; ``finish`` remembers
    the build's keys."""

    def __init__(self, request: dict[str, Any], send: Callable[[dict[str, Any], bytes], None], *,
                 acknowledged: bool = False) -> None:
        self.served = {Path(path).resolve(): version for path, version in (request.get("served") or {}).items()}
        self.held = set(request.get("held") or [])
        self.served_keys = {Path(path).resolve(): set(keys) for path, keys in (request.get("served_keys") or {}).items()}
        self.meshed = 0
        self.have = set(request.get("shapes") or [])
        self.send = send
        self.acknowledged = acknowledged

    def saved(self, path: Path, tree: Any) -> None:
        try:
            self._patch(Path(path).resolve(), tree)
        except Exception:  # noqa: BLE001 - without a patch the daemon reads the file
            traceback.print_exc()

    def _patch(self, path: Path, tree: Any) -> None:
        from cadgen.engine.export import structure_of
        from cadgen.engine.mesh import mesh_shape, shape_bytes
        from cadgen.engine.serve import MESHES

        if path not in self.served:
            return
        stat = path.stat()
        if self.served[path] == f"{stat.st_mtime_ns}-{stat.st_size}":
            return  # the daemon serves this very file
        clock = time.perf_counter()
        structure = structure_of(tree, path)
        made = time.perf_counter()
        meshes, buffers, shapes, data = [], [], [], []
        parts = {}
        for part in tree.parts:
            parts.setdefault(part.key, part)
        served = self.served_keys.get(path, set())
        for key, part in parts.items():
            fresh = key not in served and key not in self.held
            if not fresh and key in self.have:
                continue
            shape = shape_bytes(part.shape)
            if fresh:
                # The process's one mesh cache: a part this worker meshed for an export is
                # not meshed again for the viewer, nor the other way round.
                buffer = MESHES.get(key)
                if buffer is None:
                    found = mesh_shape(shape, parallel=True)
                    buffer = found.to_bytes()
                    MESHES.put(key, buffer, found.unmeshed)
                    self.meshed += 1
                buffers.append(buffer)
                meshes.append([key, len(buffer), {str(o): text for o, text in (MESHES.unmeshed(key) or {}).items()}])
                self.held.add(key)
            if key not in self.have:
                data.append(shape)
                shapes.append([key, len(shape)])
                self.have.add(key)
        done = time.perf_counter()
        self.served[path] = structure["version"]
        waiting = _PATCHED.setdefault(str(path), threading.Event()) if self.acknowledged else None
        if waiting is not None:
            waiting.clear()
        self.send({"patch": {"path": str(path), "structure": structure, "meshes": meshes, "shapes": shapes,
                             "timings": {"structure": round(made - clock, 4), "mesh": round(done - made, 4),
                                         "sent": time.time()}}}, b"".join(buffers + data))
        if waiting is not None:
            waiting.wait(PATCH_WAIT)  # the viewer's version moves before a derived file is written

    def finish(self, reports: list[dict[str, Any]]) -> None:
        """Each report says how many parts this build meshed."""
        for report in reports:
            report["meshed"] = self.meshed


# --- the worker's memory cap ----------------------------------------------------------------------


def resident_mb() -> float | None:
    """This process's resident memory now, in MiB; None where the platform will not say."""
    try:
        if sys.platform == "darwin":
            import ctypes

            class Info(ctypes.Structure):  # mach_task_basic_info
                _fields_ = [("virtual_size", ctypes.c_uint64), ("resident_size", ctypes.c_uint64),
                            ("resident_size_max", ctypes.c_uint64), ("user_time", ctypes.c_uint32 * 2),
                            ("system_time", ctypes.c_uint32 * 2), ("policy", ctypes.c_int32),
                            ("suspend_count", ctypes.c_int32)]

            system = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
            info, count = Info(), ctypes.c_uint32(ctypes.sizeof(Info) // 4)
            task = ctypes.c_uint32.in_dll(system, "mach_task_self_")
            if system.task_info(task, 20, ctypes.byref(info), ctypes.byref(count)) != 0:  # MACH_TASK_BASIC_INFO
                return None
            return info.resident_size / 2**20
        if sys.platform.startswith("linux"):
            with open("/proc/self/statm", encoding="ascii") as handle:
                return int(handle.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 2**20
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):  # PROCESS_MEMORY_COUNTERS
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]

            counters = Counters()
            counters.cb = ctypes.sizeof(Counters)
            # Typed: the current process's pseudo-handle is -1, a HANDLE; passed as a C int the call
            # fails, and a Windows worker had no memory cap.
            current = ctypes.windll.kernel32.GetCurrentProcess
            current.restype = wintypes.HANDLE
            info = ctypes.windll.psapi.GetProcessMemoryInfo
            info.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            info.restype = wintypes.BOOL
            if not info(current(), ctypes.byref(counters), counters.cb):
                return None
            return counters.WorkingSetSize / 2**20
    except Exception:  # noqa: BLE001 - a platform that will not say has no cap
        return None
    return None


def _physical_mb() -> float | None:
    try:
        if sys.platform == "darwin":
            import ctypes

            system = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
            size, length = ctypes.c_uint64(0), ctypes.c_size_t(8)
            if system.sysctlbyname(b"hw.memsize", ctypes.byref(size), ctypes.byref(length), None, 0) == 0:
                return size.value / 2**20
            return None
        if os.name == "nt":  # no sysconf: GlobalMemoryStatusEx's ullTotalPhys
            import ctypes
            from ctypes import wintypes

            class Status(ctypes.Structure):  # MEMORYSTATUSEX
                _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                            *((name, ctypes.c_uint64) for name in (
                                "ullTotalPhys", "ullAvailPhys", "ullTotalPageFile", "ullAvailPageFile",
                                "ullTotalVirtual", "ullAvailVirtual", "ullAvailExtendedVirtual"))]

            status = Status()
            status.dwLength = ctypes.sizeof(Status)
            ask = ctypes.windll.kernel32.GlobalMemoryStatusEx
            ask.argtypes, ask.restype = [ctypes.POINTER(Status)], wintypes.BOOL
            return status.ullTotalPhys / 2**20 if ask(ctypes.byref(status)) else None
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**20
    except (AttributeError, OSError, ValueError):
        return None


def rss_cap_mb() -> float | None:
    """The resident memory a worker may hold after a build: ``CADGEN_ENGINE_WORKER_RSS_MB``
    (``0`` for no cap), else a quarter of the machine's memory, at least 2 GiB. None: no cap."""
    given = os.environ.get("CADGEN_ENGINE_WORKER_RSS_MB", "").strip()
    if given:
        try:
            value = float(given)
        except ValueError:
            value = -1.0
        if value >= 0:
            return value or None
    physical = _physical_mb()
    return None if physical is None else max(physical / 4, 2048.0)


def retirement(reports: list[dict[str, Any]], resident: float | None = None) -> bool:
    """After a build: this worker's resident memory (``resident``, else measured now) against its
    cap. Over it, each report says so (``retired``) and the worker is retired: it exits once it
    has answered, and the next build is a fresh worker's -- a restart, which takes a model's xbf
    when nothing changed."""
    resident, cap = resident if resident is not None else resident_mb(), rss_cap_mb()
    if resident is None or cap is None or resident <= cap:
        return False
    for report in reports:
        report["retired"] = (f"the worker held {resident:.0f} MB after the build, over its {cap:.0f} MB cap "
                             "(CADGEN_ENGINE_WORKER_RSS_MB): the next build starts a fresh one")
    return True


def _export(request: dict[str, Any], log: Callable[[str], None]) -> dict[str, Any]:
    """An ``export`` request: the document -- a script's model built first, or a file (the document this worker last built of it while the file is as the
    build left it, else read) -- written to each destination, and drawn into ``draw``'s
    PNG where the request has one (``{"out", option: value}``, the snapshot verb's: a
    model's picture is drawn from the document in this worker's memory). Answers what was
    written."""
    from cadgen.engine import build as engine_build
    from cadgen.engine import export

    source_spec = request["source"]
    built: list[dict[str, Any]] = []
    if request.get("expected") and "script" in source_spec:
        source = engine_build.held(Path(source_spec["script"]), source_spec.get("function"))
        if source is None:
            raise export.ExportRefused("The viewed model is no longer held. Reopen it before exporting.")
    elif "script" in source_spec:
        script = Path(source_spec["script"])
        function = source_spec.get("function")
        # Built as it declares, its children as theirs (law 10); the exports are this request's.
        reports = engine_build.build(script, [function] if function else None, log=log,
                                     cwd=Path(request["cwd"]) if request.get("cwd") else None)
        if len(reports) != 1:
            raise engine_build.BuildError(f"{script.name} defines {len(reports)} models: name one (SCRIPT::MODEL)")
        built = reports
        if not reports[0].get("ok"):
            return {"reports": reports, "files": []}
        function = reports[0]["model"].rpartition("::")[2]
        source = engine_build.held(script.resolve(), function)
        if source is None:
            xbf = next((path for path, row in reports[0]["files"].items() if row.get("kind") == "xbf"), None)
            if xbf is None:
                raise engine_build.BuildError(f"{script.name}::{function}: the build left no document")
            source = export.Source.read(xbf)
    else:
        path = Path(source_spec["path"])
        source = engine_build.held(path=path)
        if source is None:
            clock = time.perf_counter()
            source = export.Source.read(path)
            log(f"read {path.name} in {time.perf_counter() - clock:.2f}s")
    if request.get("expected"):
        from cadgen.engine.export.viewer import check, options
        check(source, request["expected"])
        if request.get("inspect"):
            return {"reports": [], "files": [], "formats": options(source, pose=request.get("pose"), part=request.get("part"), original_allowed="version" in request["expected"])}
    files = []
    for destination in request["destinations"]:
        clock = time.perf_counter()
        target = Path(destination)
        from cadgen.engine.progress import current
        progress = current()
        if progress is not None:
            progress.stage("write", detail=target.name)
        if request.get("original"):
            from cadgen.engine.export.viewer import original
            size = original(source, target, request["expected"])
        else:
            size = export.translate(source, None, target, pose=request.get("pose"), part=request.get("part"))
        seconds = time.perf_counter() - clock
        files.append({"path": str(target), "kind": export.kind_of(target), "bytes": size,
                      "seconds": round(seconds, 4)})
    drawn = None
    if request.get("draw"):
        from cadgen.engine import snapshot

        options = dict(request["draw"])
        out = Path(options.pop("out"))
        clock = time.perf_counter()
        drawn = snapshot.draw(source, out, **options)
        files.append({"path": str(out), "kind": "png", "bytes": drawn["bytes"],
                      "seconds": round(time.perf_counter() - clock, 4)})
    if request.get("draw_still"):
        from cadgen.engine.snapshot import Still, _occt

        options = request["draw_still"]
        out = Path(options["out"])
        drawn = _occt(source.path or out.with_suffix(".xbf"), out, Still.from_json(options["still"]),
                      False, source, time.perf_counter())
    return {"reports": built, "files": files, "drawn": drawn}


def _result(request: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
    """A ``result`` request: the document of a model this worker built, as its structure and each
    part's shape bytes (``[key, size]`` in ``shapes``, the bytes after the line in order)."""
    from cadgen.engine import build as engine_build

    source = engine_build.held(Path(request["script"]), request["name"])
    if source is None:
        raise engine_build.BuildError(f"no model named {request['name']!r} is built in this worker")
    keys = [prototype["key"] for prototype in source.structure["prototypes"]]
    data = [source.shape_data(key) for key in keys]
    from cadgen.engine import previous

    _writer, closure = previous.read_record(source.xcaf)
    return {"structure": source.structure, "closure": json.loads(closure) if closure else None,
            "shapes": [[key, len(part)] for key, part in zip(keys, data)]}, \
        b"".join(data)


def main() -> int:
    sys.dont_write_bytecode = True
    os.environ["CADGEN_ENGINE_WORKER"] = "1"
    protocol = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)  # the model's prints go where the daemon reads logs from
    sys.stdout = sys.stderr
    # The daemon's requests come on a handle of the worker's own, and standard input is the null
    # device. On Windows a thread blocked reading a pipe holds every other call on that pipe --
    # GetFileType, which a DLL's C runtime asks of the standard handles as the DLL loads (numpy's
    # OpenBLAS, say) -- so a model's first import would wait for the daemon's next request, which
    # waits for the build: a worker that never builds. And a model reading its input reads none
    # of the protocol.
    asked = os.fdopen(os.dup(0), "rb")
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.close(null)
    sys.stdin = open(os.devnull, encoding="utf-8")  # noqa: SIM115 - standard input, for the worker's life

    lock = threading.Lock()

    def send(message: dict[str, Any], payload: bytes = b"") -> None:
        if payload:
            message = {**message, "bytes": len(payload)}
        with lock:  # a frame is whole: the build's thread and the reader's both send
            protocol.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
            if payload:
                protocol.write(payload)
            protocol.flush()

    from cadgen.engine import document
    from cadgen.engine.build import BuildError, build, made_by
    from cadgen.engine.progress import Progress, building

    def progress(ident: Any, **where: Any) -> Progress:
        """A request's progress, each frame sent as ``{"id", "progress"}`` (``progress.py``)."""
        return Progress(lambda frame: send({"id": ident, "progress": frame}), **where)

    requests: "queue.Queue[dict[str, Any] | None]" = queue.Queue()

    def read() -> None:
        """Every request as it comes: a ``where`` answered here and now, the rest queued
        for the build loop."""
        try:
            for line in asked:
                if not line.strip():
                    continue
                request = json.loads(line)
                if request.get("op") == "patched":
                    _PATCHED.setdefault(str(request.get("path")), threading.Event()).set()
                elif request.get("op") == "where":
                    try:
                        found = made_by(request["path"], request["key"], request["kind"], int(request["ordinal"]))
                    except Exception:  # noqa: BLE001 - a question it cannot answer is answered with nothing
                        found = None
                    send({"id": request.get("id"), "where": found})
                else:
                    requests.put(request)
        finally:
            requests.put(None)

    # A request runs in its folder (its ``cwd``, else the one this worker started in: its script's),
    # so a model's relative reads resolve as before; between requests the worker stands in the
    # temporary folder, so an idle worker holds no project folder open (on Windows, a folder a
    # process stands in cannot be renamed or deleted).
    home, neutral = os.getcwd(), tempfile.gettempdir()
    os.chdir(neutral)

    def leave() -> None:
        try:
            os.chdir(neutral)
        except OSError:
            pass

    send({"ready": os.getpid()})
    gc.disable()
    threading.Thread(target=read, daemon=True).start()
    while True:
        request = requests.get()
        if request is None:
            break
        ident = request.get("id")
        op = request.get("op")
        if op in ("export", "result"):
            retire = False
            try:
                cwd = request.get("cwd")
                os.chdir(cwd or home)
                if op == "export":
                    source = request["source"]
                    said = progress(ident, script=source.get("script"), cwd=cwd)
                    said.model = said.name(source.get("script") or source.get("path"))
                    with building(said):
                        done = _export(request, lambda text: send({"id": ident, "log": text}))
                    said.attach(done.get("reports") or [])
                    resident = resident_mb()
                    retire = retirement(done.get("reports") or [], resident) if done.get("reports") else False
                    send({"id": ident, "done": done, "rssMb": None if resident is None else round(resident)})
                else:
                    answer, payload = _result(request)
                    send({"id": ident, "done": answer}, payload)
            except (BuildError, ValueError, OSError) as error:
                send({"id": ident, "failed": str(error)})
            except BaseException as error:  # noqa: BLE001 - the worker answers every request
                send({"id": ident, "failed": "".join(traceback.format_exception(type(error), error,
                                                                                error.__traceback__))})
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    return 1
            finally:
                leave()
                send({"id": ident, "settled": True, "op": op})
            if retire:
                return 0
            gc.collect()
            continue
        if op != "build":
            send({"id": ident, "failed": f"unknown request {op!r}"})
            send({"id": ident, "settled": True, "op": op})
            continue
        patcher = Patcher(request, lambda message, payload: send({"id": ident, **message}, payload),
                          acknowledged=bool(request.get("acknowledged")))
        document.SAVED_LISTENERS.append(patcher.saved)
        retire = False
        try:
            cwd = request.get("cwd")
            os.chdir(cwd or home)
            script = Path(request["script"])
            # Where the build is, frame by frame; each report holds its model's last.
            said = progress(ident, script=script, cwd=cwd)
            with building(said):
                said.load()
                reports = build(script, request.get("functions") or None,
                                log=lambda text: send({"id": ident, "log": text}), cwd=Path(cwd) if cwd else None,
                                verify=bool(request.get("verify")), profile=bool(request.get("profile")))
            said.attach(reports)
            patcher.finish(reports)
            resident = resident_mb()
            retire = retirement(reports, resident)
            # Its memory now, which the daemon's status shows (``rssMb``).
            send({"id": ident, "done": reports, "rssMb": None if resident is None else round(resident)})
        except BuildError as error:
            send({"id": ident, "failed": str(error)})
        except BaseException as error:  # noqa: BLE001 - the worker answers every request
            send({"id": ident, "failed": "".join(traceback.format_exception(type(error), error, error.__traceback__))})
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                return 1
        finally:
            leave()
            document.SAVED_LISTENERS.remove(patcher.saved)
            send({"id": ident, "settled": True})
        if retire:
            return 0  # over its memory cap: gone, with all it held
        gc.collect()  # answered: the cycles the build left, collected while idle
    return 0


if __name__ == "__main__":
    sys.exit(main())
