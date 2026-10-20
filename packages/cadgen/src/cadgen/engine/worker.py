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
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable

class Patcher:
    """One build's patches (see the module docstring): ``saved`` is called the moment the
    build has put an xbf in place; ``finish`` remembers the build's keys."""

    def __init__(self, request: dict[str, Any], send: Callable[[dict[str, Any], bytes], None]) -> None:
        self.served = {Path(path).resolve(): version for path, version in (request.get("served") or {}).items()}
        self.held = set(request.get("held") or [])
        self.served_keys = {Path(path).resolve(): set(keys) for path, keys in (request.get("served_keys") or {}).items()}
        self.meshed = 0
        self.have = set(request.get("shapes") or [])
        self.send = send

    def saved(self, path: Path, tree: Any) -> None:
        try:
            self._patch(Path(path).resolve(), tree)
        except Exception:  # noqa: BLE001 - without a patch the daemon reads the file
            traceback.print_exc()

    def _patch(self, path: Path, tree: Any) -> None:
        from cadgen.engine import document
        from cadgen.engine.mesh import mesh_shape, shape_bytes

        if path not in self.served:
            return
        stat = path.stat()
        if self.served[path] == f"{stat.st_mtime_ns}-{stat.st_size}":
            return  # the daemon serves this very file
        clock = time.perf_counter()
        structure = document.tree_structure(tree, path)
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
                found = mesh_shape(shape, parallel=True)
                buffers.append(found.to_bytes())
                meshes.append([key, len(buffers[-1]), {str(o): text for o, text in found.unmeshed.items()}])
                self.held.add(key)
                self.meshed += 1
            if key not in self.have:
                data.append(shape)
                shapes.append([key, len(shape)])
                self.have.add(key)
        done = time.perf_counter()
        self.served[path] = structure["version"]
        self.send({"patch": {"path": str(path), "structure": structure, "meshes": meshes, "shapes": shapes,
                             "timings": {"structure": round(made - clock, 4), "mesh": round(done - made, 4),
                                         "sent": time.time()}}}, b"".join(buffers + data))

    def finish(self, reports: list[dict[str, Any]]) -> None:
        """Each report says how many parts this build meshed."""
        for report in reports:
            report["meshed"] = self.meshed


def main() -> int:
    sys.dont_write_bytecode = True
    protocol = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)  # the model's prints go where the daemon reads logs from
    sys.stdout = sys.stderr

    def send(message: dict[str, Any], payload: bytes = b"") -> None:
        if payload:
            message = {**message, "bytes": len(payload)}
        protocol.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
        if payload:
            protocol.write(payload)
        protocol.flush()

    from cadgen.engine import document
    from cadgen.engine.build import BuildError, build

    send({"ready": os.getpid()})
    gc.disable()
    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        ident = request.get("id")
        if request.get("op") != "build":
            send({"id": ident, "failed": f"unknown request {request.get('op')!r}"})
            send({"id": ident, "settled": True})
            continue
        patcher = Patcher(request, lambda message, payload: send({"id": ident, **message}, payload))
        document.SAVED_LISTENERS.append(patcher.saved)
        try:
            cwd = request.get("cwd")
            if cwd:
                os.chdir(cwd)
            reports = build(Path(request["script"]), request.get("functions") or None,
                            log=lambda text: send({"id": ident, "log": text}), cwd=Path(cwd) if cwd else None)
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
