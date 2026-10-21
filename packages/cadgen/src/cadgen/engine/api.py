"""The engine's verbs as functions, one per command; ``cadgen xbf`` is a thin argparse over them.

    build(target=None, *, code=None, name=None, xbf=None, step=None, glb=None, stl=None,
          threemf=None, snapshot=None, pose=None, part=None) -> Report | Built
    export(source, *destinations, pose=None, part=None, animate=None) -> list[Written]
    snapshot(source, out, *, display=None, camera=None, grid=False, preview=False, pose=None,
             width=None, height=None) -> Drawn
    serve(source, port=0) -> Served
    where(source, ref) -> Facts
    status() / stop() / daemon()

Every one goes through the daemon (``client``), so the warm worker of the script, of the code
(by its ``name``) or of the document does the work and keeps what it made in memory for the
next call. A file flag (``xbf=``, ``step=``, ...) is ``True`` for the model's declared (or
default) path, a path, or for ``snapshot`` a dict of the path (``out``) and its options.
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

__all__ = ["Built", "Drawn", "EngineError", "Facts", "Report", "Served", "Written", "build", "daemon", "export",
           "serve", "snapshot", "status", "stop", "where"]

KINDS = ("xbf", "step", "glb", "stl", "threemf", "snapshot")


class EngineError(RuntimeError):
    """A request the engine refused or could not run: its words."""


@dataclass
class Report:
    """A build of one script: each model's report (``models``), in order."""

    models: list[dict[str, Any]]
    wall: float = 0.0
    fresh: bool | None = None  # whether the worker was started for it; None built in this process

    @property
    def ok(self) -> bool:
        return all(report.get("ok") for report in self.models)

    @property
    def files(self) -> dict[str, dict[str, Any]]:
        return {path: row for report in self.models for path, row in (report.get("files") or {}).items()}

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self.models)

    def __len__(self) -> int:
        return len(self.models)


@dataclass
class Written:
    """One file an export wrote."""

    path: Path
    kind: str
    bytes: int
    seconds: float


@dataclass
class Drawn:
    """A snapshot: the PNG and what the page drew (``report``)."""

    path: Path
    width: int
    height: int
    report: dict[str, Any]


@dataclass
class Served:
    url: str
    port: int


Facts = dict


@dataclass
class Built:
    """Code built from source text (``build(code=...)``): its report, its files, and -- asked
    of the worker that built it, which holds them -- its document and its shape."""

    name: str
    script: Path
    report: dict[str, Any]
    cwd: Path
    _document: Any = field(default=None, repr=False)

    @property
    def ok(self) -> bool:
        return bool(self.report.get("ok"))

    @property
    def files(self) -> dict[str, dict[str, Any]]:
        return dict(self.report.get("files") or {})

    @property
    def document(self) -> Any:
        """The built document, read-only: an ``export.Source`` -- its ``structure`` (names,
        colours, materials, element names, placements, mechanism) and each part's shape --
        as the worker that built it holds it. ``export(...)`` writes it."""
        if self._document is None:
            from cadgen.engine.export import Source

            answer = _ask({"op": "result", "script": str(self.script), "name": self.name, "cwd": str(self.cwd)})
            payload = base64.b64decode(answer.get("payload") or "")
            done = answer["done"]
            shapes, at = {}, 0
            for key, size in done["shapes"]:
                shapes[key] = payload[at:at + size]
                at += size
            self._document = Source(done["structure"], shapes, live=False)
        return self._document

    @property
    def shape(self) -> Any:
        """The built result as a build123d compound tree: every assembly a ``Compound`` of
        its components, every part placed, named, coloured and given its material as the
        document has them."""
        return self.document.shape()

    def export(self, *destinations: Any, pose: Any = None, part: str | None = None, animate: Any = None) -> list[Written]:
        return export(self, *destinations, pose=pose, part=part, animate=animate)


# --- reaching the daemon ---------------------------------------------------------------------


def _ask(message: dict[str, Any], on_log: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    from cadgen.engine.client import request

    answer = request({**message, "env": dict(os.environ)}, on_log or (lambda _message: None))
    if answer is None:
        raise EngineError("no engine daemon")
    if "failed" in answer:
        raise EngineError(str(answer["failed"]))
    return answer


def _target(target: Any) -> tuple[Path, list[str] | None]:
    """A build's target: a ``Model``, ``script.py``, ``script.py::model`` or a path."""
    from cadgen.engine.model import Model

    if isinstance(target, Model):
        return target.script, [target.name]
    if isinstance(target, tuple):  # (script, [models] or None): one run of several models
        script, functions = target
        return _target(script)[0], list(functions) if functions else None
    text = os.fspath(target)
    script, _sep, function = text.partition("::")
    path = Path(script).resolve()
    if path.suffix != ".py":
        raise EngineError(f"{script} is not a Python script")
    if not path.is_file():
        raise EngineError(f"no script {script}")
    return path, [function] if function else None


def _outputs(flags: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
    found: dict[str, dict[str, Any]] = {}
    for kind in KINDS:
        value = flags.get(kind)
        if value is None or value is False:
            continue
        if value is True:
            found[kind] = {}
        elif isinstance(value, dict):
            found[kind] = {name: (os.fspath(given) if name == "out" and given is not None else given)
                           for name, given in value.items()}
        else:
            found[kind] = {"out": os.fspath(value)}
    return found or None


def build(target: Any = None, *, code: str | None = None, name: str | None = None, xbf: Any = None,
          step: Any = None, glb: Any = None, stl: Any = None, threemf: Any = None, snapshot: Any = None,
          pose: Any = None, part: str | None = None, verify: bool = False,
          log: Callable[[str], None] | None = None, daemon: bool = True) -> "Report | Built":
    """Build a script's models (``target``: ``script.py``, ``script.py::model`` or a ``Model``)
    or ``code`` (source text assigning its final shape to ``result``, built as ``name``,
    ``model`` by default). With no file flags a script writes what its decorators declare; a
    flag selects and overrides (``xbf=True`` alone writes only the xbf). Code writes only the
    files its flags name, each with its path. ``verify`` runs every body the memo would
    replay and compares it with the logged one (``build.build``). Returns once every file is
    on disk."""
    from cadgen.engine.build import BuildError, code_script

    outputs = _outputs({"xbf": xbf, "step": step, "glb": glb, "stl": stl, "threemf": threemf, "snapshot": snapshot})
    shown = log or (lambda _text: None)
    cwd = Path.cwd()
    if code is not None:
        if target is not None:
            raise EngineError("build takes a target or code, not both")
        name = name or "model"
        script, functions = code_script(name, cwd), [name]
    else:
        if target is None:
            raise EngineError("build takes a target (script.py[::model]) or code")
        if name is not None:
            raise EngineError("name= names code: a script's models are named by their functions")
        script, functions = _target(target)
    started = time.perf_counter()
    if not daemon:
        from cadgen.engine import build as engine_build

        try:
            reports = engine_build.build(script, functions, log=shown, cwd=cwd, outputs=outputs, pose=pose,
                                         part=part, code=code, verify=verify)
        except BuildError as error:
            raise EngineError(str(error)) from None
        fresh = None
    else:
        def relay(message: dict[str, Any]) -> None:
            shown(message["log"] if message.get("stream") == "model" else f"  {message['log']}")

        answer = _ask({"op": "build", "script": str(script), "functions": functions, "cwd": str(cwd),
                       "outputs": outputs, "pose": pose, "part": part, "code": code, "name": name,
                       "verify": verify}, relay)
        reports, fresh = answer["done"], answer.get("worker", {}).get("fresh")
    wall = time.perf_counter() - started
    if code is not None:
        return Built(name or "model", script, {**reports[0], "wall": round(wall, 4)}, cwd)
    return Report(reports, round(wall, 4), fresh)


def _source(source: Any) -> dict[str, Any]:
    from cadgen.engine.export import MESHES, SOURCES, ExportRefused
    from cadgen.engine.model import Model

    if isinstance(source, Model):
        return {"script": str(source.script), "function": source.name}
    if isinstance(source, Built):
        return {"code": source.name, "script": str(source.script)}
    text = os.fspath(source)
    if "::" in text or text.endswith(".py"):
        script, functions = _target(text)
        return {"script": str(script), "function": functions[0] if functions else None}
    path = Path(text).resolve()
    if path.suffix.lower() in MESHES:
        raise ExportRefused(f"{path.name} is a mesh: it has no B-rep, and no export is made from one")
    if path.suffix.lower() not in SOURCES:
        raise ExportRefused(f"{path.name} is no document: an export reads an .xbf, a .step, or a script's model")
    if not path.is_file():
        raise ExportRefused(f"no document {path}")
    return {"path": str(path)}


def export(source: Any, *destinations: Any, pose: Any = None, part: str | None = None, animate: Any = None,
           log: Callable[[str], None] | None = None, daemon: bool = True) -> list[Written]:
    """Write a document to each destination, its kind from its suffix (``.xbf``, ``.step``,
    ``.glb``, ``.stl``, ``.3mf``): the document of an ``.xbf``, of a ``.step`` read into one,
    of a script's model (``script.py::model`` or a ``Model``, built first) or of built code
    (a ``Built``). Several destinations read the document once. ``daemon=False`` does it in
    this process, cold."""
    from cadgen.engine.export import ExportRefused, kind_of

    if animate is not None:
        raise ExportRefused("GLB animation is not built yet: animate takes nothing")
    if not destinations:
        raise ExportRefused("export names at least one destination")
    resolved = [Path(os.fspath(destination)).resolve() for destination in destinations]
    for destination in resolved:
        kind_of(destination)
    spec = _source(source)
    cwd = str(source.cwd) if isinstance(source, Built) else os.getcwd()
    shown = log or (lambda _text: None)
    order = {"op": "export", "source": spec, "destinations": [str(path) for path in resolved], "pose": pose,
             "part": part, "cwd": cwd}
    if daemon:
        done = _ask({**order, "code": spec.get("code")}, lambda message: shown(message["log"]))["done"]
    else:
        from cadgen.engine.build import BuildError
        from cadgen.engine.worker import _export

        try:
            done = _export(order, shown)
        except BuildError as error:
            raise EngineError(str(error)) from None
    failed = [report for report in done.get("reports") or [] if not report.get("ok")]
    if failed:
        raise EngineError(failed[0].get("error") or f"the build of {failed[0].get('model')} failed")
    return [Written(Path(row["path"]), row["kind"], row["bytes"], row["seconds"]) for row in done["files"]]


def snapshot(source: Any, out: Any, *, display: Any = None, camera: Any = None, grid: bool = False,
             preview: bool = False, pose: Any = None, width: int | None = None, height: int | None = None,
             size_profile: str | None = None, daemon: bool = True) -> Drawn:
    """Draw a document (``.xbf``, ``.step``) or a mesh file (``.glb``, ``.stl``, ``.3mf``) into
    ``out`` (a ``.png``) as the viewer draws it; a ``Model`` is built first and its document
    drawn."""
    from cadgen.engine import snapshot as drawing
    from cadgen.engine.model import Model

    out = Path(os.fspath(out)).resolve()
    if isinstance(source, Model):
        xbf = next((target.path for target in source.plan() if target.kind == "xbf"), None)
        if xbf is None:
            report = build(source, snapshot={"out": str(out), "display": display, "camera": camera, "grid": grid,
                                             "preview": preview, "pose": pose, "width": width, "height": height})
            row = report.files.get(str(out))
            if not report.ok or row is None:
                raise EngineError((report.models[0].get("error") if report.models else "") or "the build failed")
            return Drawn(out, width or drawing.DEFAULT_SIZE[0], height or drawing.DEFAULT_SIZE[1], row)
        report = build(source)
        if not report.ok:
            raise EngineError(report.models[0].get("error") or "the build failed")
        source = xbf
    elif isinstance(source, Built):
        xbf = next((path for path, row in source.files.items() if row.get("kind") == "xbf"), None)
        if xbf is None:
            raise drawing.SnapshotRefused("code built without --xbf has no document on disk: build it with "
                                          "--xbf F, or with --snapshot OUT.png")
        source = xbf
    shown, framed, size = drawing.options(display, camera, grid, width, height, size_profile)
    found = drawing.snapshot(Path(os.fspath(source)), out, display=shown, camera=framed, preview=preview, size=size,
                             daemon=daemon, pose=drawing.parse_pose(pose) if isinstance(pose, str) else pose)
    return Drawn(out, found["width"], found["height"], found)


def serve(source: Any, port: int = 0, three: str | None = None) -> Served:
    """Serve a document (``.xbf``, ``.step``) from the daemon; its page's URL."""
    path = Path(os.fspath(source)).resolve()
    if not path.is_file():
        raise EngineError(f"no document {source}")
    if three is None:
        from cadgen.engine.serve import find_three

        found = find_three()
        three = str(found) if found else None
    answer = _ask({"op": "serve", "path": str(path), "port": port, "three": three})
    return Served(answer["url"], answer["port"])


def where(source: Any, ref: str) -> Facts | list[Facts]:
    """What made one element of a document, and what the kernel measures of it: ``ref`` is
    ``PART/face N`` (``edge``, ``vertex``) or an element name (``f:...``)."""
    import urllib.request

    from cadgen.engine.cli import elements

    path = Path(os.fspath(source)).resolve()
    if not path.is_file():
        raise EngineError(f"no document {source}")
    answer = _ask({"op": "serve", "path": str(path)})
    base, _sep, ident = answer["url"].partition("/?doc=")

    def get(route: str) -> Any:
        with urllib.request.urlopen(f"{base}/api/doc/{ident}{route}", timeout=300) as response:  # noqa: S310
            return json.loads(response.read())

    structure = get("")
    found = []
    for prototype, kind, ordinal in elements(structure, ref):
        facts = get(f"/part/{prototype['key']}/{kind}/{ordinal}")
        found.append({"part": prototype["name"], "key": prototype["key"], "kind": kind, "ordinal": ordinal,
                      "name": prototype.get(f"{kind}Names", {}).get(str(ordinal)), **facts})
    if not found:
        raise EngineError(f"{path.name} has no element {ref!r}")
    return found[0] if len(found) == 1 else found


def status() -> dict[str, Any] | None:
    """The daemon's workers and served documents; None when no daemon runs."""
    from cadgen.engine.client import request

    return request({"op": "status"}, start=False)


def stop() -> bool:
    """Stop the daemon, and wait until it is gone. False when none was running."""
    from cadgen.engine.client import request, wait_gone

    if request({"op": "stop"}, start=False) is None:
        return False
    if not wait_gone():
        raise EngineError("the engine daemon did not exit")
    return True


def daemon() -> int:
    """Run the daemon in this process."""
    from cadgen.engine.daemon import main

    return main([])
