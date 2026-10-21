"""A warm worker: one process per model script, the memo living in it for its life.

The daemon starts one per script and talks to it over the worker's stdin and its
original stdout. A request is one JSON object per line: ``{"id", "op": "build", "script",
"functions", "cwd"}``, and, when the daemon serves documents, ``served`` (each served
path and the version served of it), ``served_keys`` (each served path and the keys of the
parts its served document holds), ``held`` (the geometry keys it holds a mesh for) and
``shapes`` (the keys it holds a shape for). The answers are frames: a JSON object on one
line and, when it has ``bytes``, exactly that many bytes after it. A build is answered by
``{"id", "log"}`` frames while it builds, a ``{"id", "patch", "bytes"}`` frame (below) for
each served xbf the moment the build has put it in place -- before the build goes on to
its exports -- one ``{"id", "done"}`` (the build's reports) or ``{"id", "failed"}``, and
last ``{"id", "settled"}``. What the model prints goes to the worker's stderr, which the
daemon passes on. The worker exits when its stdin closes.

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


def _export(request: dict[str, Any], log: Callable[[str], None]) -> dict[str, Any]:
    """An ``export`` request: the document -- a script's model built first, code this worker
    built, or a file (the document this worker last built of it while the file is as the
    build left it, else read) -- written to each destination. Answers what was written."""
    from cadgen.engine import build as engine_build
    from cadgen.engine import export

    source_spec = request["source"]
    built: list[dict[str, Any]] = []
    if "script" in source_spec and "code" not in source_spec:
        script = Path(source_spec["script"])
        function = source_spec.get("function")
        # Built with the document it declares and nothing else: the exports are this request's.
        reports = engine_build.build(script, [function] if function else None, outputs={"xbf": None}, log=log,
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
    elif "code" in source_spec:
        source = engine_build.held(Path(source_spec["script"]), source_spec["code"])
        if source is None:
            raise engine_build.BuildError(f"no code named {source_spec['code']!r} is built in this worker")
    else:
        path = Path(source_spec["path"])
        source = engine_build.held(path=path)
        if source is None:
            clock = time.perf_counter()
            source = export.Source.read(path)
            log(f"read {path.name} in {time.perf_counter() - clock:.2f}s")
    files = []
    for destination in request["destinations"]:
        clock = time.perf_counter()
        target = Path(destination)
        size = export.translate(source, None, target, pose=request.get("pose"), part=request.get("part"))
        seconds = time.perf_counter() - clock
        files.append({"path": str(target), "kind": export.kind_of(target), "bytes": size,
                      "seconds": round(seconds, 4)})
        log(f"wrote {target.name} ({size / 1e6:.1f} MB) in {seconds:.2f}s")
    return {"reports": built, "files": files}


def _result(request: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
    """A ``result`` request: the document code this worker built, as its structure and each
    part's shape bytes (``[key, size]`` in ``shapes``, the bytes after the line in order)."""
    from cadgen.engine import build as engine_build

    source = engine_build.held(Path(request["script"]), request["code"])
    if source is None:
        raise engine_build.BuildError(f"no code named {request['code']!r} is built in this worker")
    keys = [prototype["key"] for prototype in source.structure["prototypes"]]
    data = [source.shape_data(key) for key in keys]
    return {"structure": source.structure, "shapes": [[key, len(part)] for key, part in zip(keys, data)]}, \
        b"".join(data)


def main() -> int:
    sys.dont_write_bytecode = True
    os.environ["CADGEN_ENGINE_WORKER"] = "1"
    protocol = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)  # the model's prints go where the daemon reads logs from
    sys.stdout = sys.stderr

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

    requests: "queue.Queue[dict[str, Any] | None]" = queue.Queue()

    def read() -> None:
        """Every request as it comes: a ``where`` answered here and now, the rest queued
        for the build loop."""
        try:
            for line in sys.stdin:
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
            try:
                cwd = request.get("cwd")
                if cwd:
                    os.chdir(cwd)
                if op == "export":
                    send({"id": ident, "done": _export(request, lambda text: send({"id": ident, "log": text}))})
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
                send({"id": ident, "settled": True, "op": op})
            gc.collect()
            continue
        if op != "build":
            send({"id": ident, "failed": f"unknown request {op!r}"})
            send({"id": ident, "settled": True, "op": op})
            continue
        patcher = Patcher(request, lambda message, payload: send({"id": ident, **message}, payload),
                          acknowledged=bool(request.get("acknowledged")))
        document.SAVED_LISTENERS.append(patcher.saved)
        try:
            cwd = request.get("cwd")
            if cwd:
                os.chdir(cwd)
            script = Path(request["script"])
            reports = build(script, request.get("functions") or None,
                            log=lambda text: send({"id": ident, "log": text}), cwd=Path(cwd) if cwd else None,
                            outputs=request.get("outputs"), pose=request.get("pose"), part=request.get("part"),
                            code=request.get("code"))
            patcher.finish(reports)
            send({"id": ident, "done": reports})
        except BuildError as error:
            send({"id": ident, "failed": str(error)})
        except BaseException as error:  # noqa: BLE001 - the worker answers every request
            send({"id": ident, "failed": "".join(traceback.format_exception(type(error), error, error.__traceback__))})
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                return 1
        finally:
            document.SAVED_LISTENERS.remove(patcher.saved)
            send({"id": ident, "settled": True})
        gc.collect()  # answered: the cycles the build left, collected while idle
    return 0


if __name__ == "__main__":
    sys.exit(main())
