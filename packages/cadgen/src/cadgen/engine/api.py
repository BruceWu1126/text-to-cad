"""The engine's verbs as functions, one per command; ``cli`` is a thin argparse over them.

    build(code, *, model=None, name=None, xbf=None, step=None, glb=None, stl=None, threemf=None, dxf=None) -> Built
    export(source, *destinations, pose=None, part=None) -> list[Written]
    snapshot(source, out, *, display=None, camera=None, grid=False, preview=False, pose=None,
             width=None, height=None, annotation=None, measurements=None) -> Drawn
    snapshot_job(job, source=None, *, the snapshot's flags) -> list[Drawn]
    serve(source, port=0) -> Served
    inspect(source, ref=None) -> Facts (the build closure without a ref)
    status() / stop() / daemon()

Every one goes through the daemon (``client``), so the warm worker of the script, of the code
(by its ``name``) or of the document does the work and keeps what it made in memory for the
next call. ``build`` takes source text or a build123d shape (the CLI takes code only) and requires an explicit destination:
``model=NAME`` holds it in memory; file flags (``xbf=``, ``step=``, ...) write to the default
path with ``True`` or to a supplied path. A script's models build when called by the script
(``python model.py``), or from Python
by ``Model.build()`` (``build_script``), a ``Report``. A build writes the model as its joints place
it; a pose, a part and a picture are the ``export`` and ``snapshot`` verbs', never cached.

The engine counts nothing. After each ``build``, ``export`` and ``snapshot`` it calls every
function in ``observers``, in the verb's thread, with one plain dict of what the verb did
(``_facts``): kinds, counts, times, the warm worker's part, and an error's type and frames --
never a name, a path or a message of the person's but the frames' files as Python has them. An
observer that raises is passed over, and the verb returns or raises as it would have. cadgen's
telemetry is one, appended by cadgen's own ``__init__`` (law 3: nothing here imports it).
"""

from __future__ import annotations

import base64
import functools
import json
import os
import re
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar, cast

__all__ = ["Built", "Drawn", "EngineError", "Facts", "Report", "Served", "Written", "build", "daemon", "export",
           "observers", "serve", "snapshot", "snapshot_job", "status", "stop", "inspect"]

KINDS = ("xbf", "step", "glb", "stl", "threemf", "dxf")


class EngineError(RuntimeError):
    """A request the engine refused or could not run: its words."""


def _files(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Every file a model's build wrote or kept: the model's own, then each child model's
    (``child_models``, the models its run ran, each of which writes its own files)."""
    found = dict(report.get("files") or {})
    for child in (report.get("child_models") or {}).values():
        found.update(child.get("files") or {})
    return found


@dataclass
class Report:
    """A build of one script: each model's report (``models``), in order; each holds the
    reports of the child models its run ran (``child_models``), each of which wrote its own
    files."""

    models: list[dict[str, Any]]
    wall: float = 0.0
    fresh: bool | None = None  # whether the worker was started for it; None built in this process

    @property
    def ok(self) -> bool:
        return all(report.get("ok") for report in self.models)

    @property
    def files(self) -> dict[str, dict[str, Any]]:
        return {path: row for report in self.models for path, row in _files(report).items()}

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
    source_kind: str = "code"
    _document: Any = field(default=None, repr=False)

    @property
    def ok(self) -> bool:
        return bool(self.report.get("ok"))

    @property
    def files(self) -> dict[str, dict[str, Any]]:
        """Every file the build wrote or kept: the code's, then each child model's."""
        return _files(self.report)

    @property
    def document(self) -> Any:
        """The built document, read-only: an ``export.Source`` -- its ``structure`` (names,
        colours, materials, element names, placements, mechanism) and each part's shape --
        as the worker that built it holds it. ``export(...)`` writes it."""
        if self._document is None:
            from cadgen.engine.export import Source

            answer = _ask({"op": "result", "script": str(self.script), "name": self.name, "cwd": str(self.cwd),
                           "source_kind": self.source_kind})
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

    def export(self, *destinations: Any, pose: Any = None, part: str | None = None) -> list[Written]:
        return export(self, *destinations, pose=pose, part=part)


# --- reaching the daemon ---------------------------------------------------------------------


def _ask(message: dict[str, Any], on_log: Callable[[dict[str, Any]], None] | None = None,
         on_progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    from cadgen.engine.client import request

    answer = request({**message, "env": dict(os.environ)}, on_log or (lambda _message: None), on_progress=on_progress)
    if answer is None:
        raise EngineError("no engine daemon")
    _heard("worker", answer.get("worker"))
    if "failed" in answer:
        raise EngineError(str(answer["failed"]))
    return answer


def _target(target: Any) -> tuple[Path, list[str] | None]:
    """A snapshot's script source: ``script.py`` or ``script.py::model``."""
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
        else:
            found[kind] = {"out": os.fspath(value)}
    return found or None


def _built(script: Path, functions: list[str] | None, *, code: str | None = None, name: str | None = None,
           outputs: dict[str, dict[str, Any]] | None = None, verify: bool = False, profile: bool = False,
           log: Callable[[str], None] | None = None, daemon: bool = True,
           progress: Any = None, shape: dict | None = None, cwd: Path | None = None) -> tuple[list[dict[str, Any]], Any, float]:
    """One build -- of code, or of a script's models -- in the warm worker that holds it, or in
    this process: its reports, whether the worker was started for it, and its wall time. Where
    the build is goes, frame by frame, to ``progress`` (``progress.py``): by default a bar on
    stderr when it is a terminal and nowhere when it is not; ``False`` nowhere; a callable takes
    each frame. Each model's report holds its last (``progress``)."""
    from cadgen.engine import progress as progressing
    from cadgen.engine.build import BuildError

    bar = progressing.Bar() if progress is None else None
    frames = bar.frame if bar is not None else (progress or None)
    shown = log or (lambda _text: None)
    if bar is not None:
        shown = bar.around(shown)  # a line of the log goes above the bar
    cwd = cwd or Path.cwd()
    started = time.perf_counter()
    try:
        if not daemon:
            from cadgen.engine import build as engine_build

            said = progressing.Progress(frames or (lambda _frame: None), script=script, cwd=cwd,
                                        code=code is not None)
            try:
                with progressing.building(said):
                    said.load()
                    reports = engine_build.build(script, functions, log=shown, cwd=cwd, outputs=outputs, code=code,
                                                 verify=verify, profile=profile, shape=shape)
            except BuildError as error:
                raise EngineError(str(error)) from None
            said.attach(reports)
            fresh = None
        else:
            def relay(message: dict[str, Any]) -> None:
                shown(message["log"] if message.get("stream") == "model" else f"  {message['log']}")

            answer = _ask({"op": "build", "script": str(script), "functions": functions, "cwd": str(cwd),
                           "outputs": outputs, "code": code, "shape": shape, "name": name, "verify": verify, "profile": profile}, relay, frames)
            reports, fresh = answer["done"], answer.get("worker", {}).get("fresh")
    finally:
        if bar is not None:
            bar.close()
    return reports, fresh, time.perf_counter() - started


def build(code: Any, *, model: str | None = None, name: str | None = None, xbf: Any = None, step: Any = None, glb: Any = None,
          stl: Any = None, threemf: Any = None, dxf: Any = None, verify: bool = False, profile: bool = False,
          log: Callable[[str], None] | None = None, daemon: bool = True, progress: Any = None,
          cwd: Any = None) -> Built:
    """Build source text or a build123d shape. Source text assigns its final shape to
    ``result``, and is built as ``name`` (``model`` by default) in the warm worker of that name.
    An output is required: ``model=NAME`` holds ``code::NAME`` in memory and updates it on
    subsequent builds. File flags are additive declarations:
    ``True`` writes the kind at the CLI default in the working folder
    (``XBF/<name>.xbf``, ``<name>.step``, ...), a path writes it there. A child
    model the code calls writes the files it declares (law 10). ``verify`` runs every body the
    memo would replay and compares it with the logged one (``build.build``); ``daemon=False``
    builds in this process; ``progress`` as ``_built`` has it. Returns once every file is on
    disk. For a supplied shape, its geometry already exists in the caller; metadata
    and geometry travel in memory to the worker's normal document writer. Its prior
    operations cannot be replayed or verified. ``profile=True`` reports the actual worker
    build call, preserving cache hits; for a supplied shape only the remaining worker
    processing is measured. ``cwd`` sets the build working directory without changing
    the caller's process directory. A script's decorated models use ``Model.build()``."""
    from cadgen.engine.build import code_script

    outputs = _outputs({"xbf": xbf, "step": step, "glb": glb, "stl": stl, "threemf": threemf, "dxf": dxf})
    if model is None and not outputs:
        raise EngineError("build requires an output: model=NAME or xbf/step/glb/stl/threemf/dxf")
    if model is not None:
        if not isinstance(model, str) or not model.isidentifier() or not model.isascii() or __import__("keyword").iskeyword(model):
            raise EngineError("model must be a nonempty Python identifier")
        if name is not None and name != model:
            raise EngineError("model and name must name the same model")
    name = model or name or "model"
    cwd = Path(cwd).resolve() if cwd is not None else Path.cwd()
    if not cwd.is_dir():
        raise EngineError(f"no working directory {cwd}")
    script = code_script(name, cwd)
    shape = None
    if not isinstance(code, str):
        if verify:
            raise EngineError("verify requires source code: a supplied shape has already been built")
        from cadgen.engine.build import _CODE_NAME

        # Source text is refused by this rule in its worker (``build.code_source``); a shape
        # has no source, so its name is checked here, before it names a worker or a path.
        if not _CODE_NAME.fullmatch(name):
            raise EngineError(f"name {name!r} is no Python identifier")
        from cadgen.engine.shape_input import pack

        shape = pack(code, name)
        code = ""  # the same named worker/address as a source-code build
    reports, _fresh, wall = _built(script, [name], code=code, name=name, outputs=outputs, verify=verify, log=log,
                                   daemon=daemon, progress=progress, profile=profile, shape=shape, cwd=cwd)
    result = Built(name, script, {**reports[0], "wall": round(wall, 4)}, cwd)
    if not daemon and result.ok:
        from cadgen.engine.build import held

        result._document = held(script, name)
    return result


def build_script(script: Path, functions: list[str] | None = None, *, verify: bool = False, profile: bool = False,
                 log: Callable[[str], None] | None = None, daemon: bool = True, progress: Any = None) -> Report:
    """A script's models -- ``functions``, or every one it defines -- each built as its
    decorators declare, and every child model each calls as its own declare (law 10): what
    explicit ``Model.build()`` requests ask. A script takes no file
    flags: its decorators are the whole definition of what it writes (law 11). ``progress`` as
    ``_built`` has it."""
    reports, fresh, wall = _built(Path(script).resolve(), functions, verify=verify, log=log, daemon=daemon,
                                  progress=progress, profile=profile)
    return Report(reports, round(wall, 4), fresh)


def _source(source: Any, *, scripts: bool = False) -> dict[str, Any]:
    """What an export or a snapshot reads: a ``Model``, built code (a ``Built``), a document's path
    -- or, for a snapshot (``scripts``), a script's model by its path, ``script.py::model``."""
    from cadgen.engine.export import MESHES, SOURCES, ExportRefused
    from cadgen.engine.model import Model

    if isinstance(source, Model):
        return {"script": str(source.script), "function": source.name}
    if isinstance(source, Built):
        return {"code" if source.source_kind == "code" else "function": source.name, "script": str(source.script)}
    from cadgen.engine.addresses import parse

    text = os.fspath(source)
    address = parse(text)
    if address is not None:
        if address.script is not None:
            return {"script": str(address.script), "function": address.name}
        return _ask({"op": "model_source", "address": str(address)})["source"]
    if scripts and ("::" in text or text.endswith(".py")):
        script, functions = _target(text)
        return {"script": str(script), "function": functions[0] if functions else None}
    path = Path(text).resolve()
    if path.suffix.lower() in MESHES:
        raise ExportRefused(f"{path.name} is a mesh: it has no B-rep, and no export is made from one")
    if path.suffix.lower() not in SOURCES:
        raise ExportRefused(f"{path.name} is no document: an export reads an .xbf or a .step")
    if not path.is_file():
        raise ExportRefused(f"no document {path}")
    return {"path": str(path)}


def export(source: Any, *destinations: Any, pose: Any = None, part: str | None = None, inputs: bool = True,
           log: Callable[[str], None] | None = None, daemon: bool = True,
           progress: Callable[[dict], None] | None = None) -> list[Written]:
    """Write a document to each destination, its kind from its suffix (``.xbf``, ``.step``,
    ``.glb``, ``.stl``, ``.3mf``, and ``.dxf`` of a planar document): the document of an
    ``.xbf``, of a ``.step`` read into one, of a ``Model`` (built first) or of built code (a
    ``Built``). Several destinations read the document once. ``part`` is one part or assembly as
    a ref's owner names it (``refs``). ``daemon=False`` does it in this process, cold. A GLB
    carries every motion the document defines, as a clip.

    A ``.cadz`` destination packages an ``.xbf`` with its build closure's source files and,
    unless ``inputs`` is False, its inputs (``export.cadz``), in this process: no document is
    read, only the files the closure names."""
    from cadgen.engine.export import ExportRefused, kind_of
    from cadgen.engine.export import cadz

    if not destinations:
        raise ExportRefused("export names at least one destination")
    resolved = [Path(os.fspath(destination)).resolve() for destination in destinations]
    packages = [path for path in resolved if path.suffix.lower() == cadz.SUFFIX]
    resolved = [path for path in resolved if path.suffix.lower() != cadz.SUFFIX]
    for destination in resolved:
        kind_of(destination)
    written: list[Written] = []
    if packages:
        if pose is not None or part is not None:
            raise ExportRefused("a .cadz packages the document as built: --pose and --part are another export's")
        text = os.fspath(source) if isinstance(source, (str, os.PathLike)) else ""
        if not text.lower().endswith(".xbf"):
            raise ExportRefused("a .cadz is made from an .xbf: export the model's xbf first")
        for package in packages:
            clock = time.perf_counter()
            size = cadz.write(Path(text), package, inputs=inputs)
            written.append(Written(package, "cadz", size, round(time.perf_counter() - clock, 4)))
        if not resolved:
            return written
    spec = _source(source)
    cwd = str(source.cwd) if isinstance(source, Built) else os.getcwd()
    shown = log or (lambda _text: None)
    order = {"op": "export", "source": spec, "destinations": [str(path) for path in resolved], "pose": pose,
             "part": part, "cwd": cwd}
    done = _exported(order, spec, shown, daemon, progress)
    return written + [Written(Path(row["path"]), row["kind"], row["bytes"], row["seconds"]) for row in done["files"]]


def _exported(order: dict[str, Any], spec: dict[str, Any], shown: Callable[[str], None], daemon: bool,
              progress: Callable[[dict], None] | None = None) -> dict:
    """An ``export`` request answered by the worker that holds the document (``worker._export``)."""
    if daemon:
        done = _ask({**order, "code": spec.get("code")}, lambda message: shown(message["log"]), progress)["done"]
    else:
        from cadgen.engine.build import BuildError
        from cadgen.engine.worker import _export

        try:
            done = _export(order, shown)
        except BuildError as error:
            raise EngineError(str(error)) from None
    _heard("reports", done.get("reports"))
    failed = [report for report in done.get("reports") or [] if not report.get("ok")]
    if failed:
        raise EngineError(failed[0].get("error") or f"the build of {failed[0].get('model')} failed")
    return done


def snapshot(source: Any, out: Any, *, display: Any = None, camera: Any = None, grid: bool = False,
             preview: bool = False, pose: Any = None, width: int | None = None, height: int | None = None,
             size_profile: str | None = None, daemon: bool = True, annotation: Any = None,
             measurements: Any = None) -> Drawn:
    """Draw a document (``.xbf``, ``.step``) or a mesh file (``.glb``, ``.stl``, ``.3mf``) into
    ``out`` (a ``.png``) as the viewer draws it. A ``Model`` (or ``script.py::model``) is built
    first and built code (a ``Built``) is drawn as built: the document drawn is the one its
    worker holds in memory, whether or not an xbf of it is on disk. ``annotation`` (the viewer's
    annotation: its file's path, its object, or the Quick Edit packet that carries it) and
    ``measurements`` are drawn over the model; ``camera="authored"`` draws it from the view the
    annotation's ink was drawn in, which the packet holds. ``out`` a folder (a path ending in a
    separator, or one that is there) is given a name inside it, the source's stem and the time
    (``snapshot.picture_path``). A snapshot refused or failed leaves the file at ``out`` as it
    was (``snapshot.write_png``)."""
    from cadgen.engine import snapshot as drawing

    out = drawing.picture_path(out, source)
    return _snapshot(source, out, display=display, camera=camera, grid=grid, preview=preview, pose=pose,
                     width=width, height=height, size_profile=size_profile, daemon=daemon, annotation=annotation,
                     measurements=measurements)


def _snapshot(source: Any, out: Path, *, display: Any, camera: Any, grid: bool, preview: bool, pose: Any,
              width: int | None, height: int | None, size_profile: str | None, daemon: bool, annotation: Any,
              measurements: Any) -> Drawn:
    from cadgen.engine import snapshot as drawing
    from cadgen.engine.model import Model

    if isinstance(source, (Model, Built)) or (not isinstance(source, Path) and (
            "::" in os.fspath(source) or os.fspath(source).endswith(".py"))):
        if out.suffix.lower() != ".png":
            raise drawing.SnapshotRefused(f"a snapshot is a .png; {out.name} is not one")
        spec = _source(source, scripts=True)
        drawing.options(display, camera, grid, width, height, size_profile)  # refused here, before any build
        drawing.parse_annotation(annotation)  # refused here, before any build
        if isinstance(annotation, (str, Path)) and not str(annotation).lstrip().startswith("{"):
            annotation = str(Path(annotation).expanduser().resolve())  # read in the worker, beside its packets
        draw = {"out": str(out), "display": display, "camera": camera, "grid": grid, "preview": preview,
                "pose": drawing.parse_pose(pose) if isinstance(pose, str) else pose, "width": width,
                "height": height, "size_profile": size_profile, "annotation": annotation,
                "measurements": drawing.parse_measurements(measurements)}
        order = {"op": "export", "source": spec, "destinations": [], "draw": draw,
                 "cwd": str(source.cwd) if isinstance(source, Built) else os.getcwd()}
        found = _exported(order, spec, lambda _text: None, daemon)["drawn"]
        return Drawn(out, found["width"], found["height"], found)
    shown, framed, size = drawing.options(display, camera, grid, width, height, size_profile)
    found = drawing.snapshot(Path(os.fspath(source)), out, display=shown, camera=framed, preview=preview,
                             size=None if width is None and height is None and size_profile is None else size,
                             daemon=daemon, pose=drawing.parse_pose(pose) if isinstance(pose, str) else pose,
                             annotation=drawing.parse_annotation(annotation),
                             measurements=drawing.parse_measurements(measurements), annotation_source=annotation)
    return Drawn(out, found["width"], found["height"], found)


def snapshot_job(job: Any, source: Any = None, *, display: Any = None, camera: Any = None, grid: bool = False,
                 preview: bool = False, pose: Any = None, width: int | None = None, height: int | None = None,
                 size_profile: str | None = None, daemon: bool = True, annotation: Any = None,
                 measurements: Any = None) -> list[Drawn]:
    """Every picture a job names (``cadgen snapshot --job``): a Quick Edit packet (the
    ``.snapshot.json`` the viewer writes, its path or its object), a job or a list of jobs, each
    job a view drawn as it was -- its camera, display, hidden and isolated parts, pose, the
    packet's ink and dimensions -- at its output settings, into each of its outputs
    (``snapshot.job_pictures``). ``source`` is drawn in place of every job's input; a flag given
    overrides every job. Every request is checked before anything renders, and a picture that
    fails writes none of the job's, leaving every output as it was."""
    from cadgen.engine import snapshot as drawing

    _heard("source", os.fspath(source) if source is not None else drawing.job_input(job))
    pictures = drawing.job_pictures(job, source, display=display, camera=camera, grid=grid, preview=preview, pose=pose,
                                    width=width, height=height, size_profile=size_profile, annotation=annotation,
                                    measurements=measurements)
    return [Drawn(Path(found["output"]), found["width"], found["height"], found)
            for found in drawing.draw_pictures(pictures, daemon=daemon)]


def serve(source: Any, port: int = 0, three: str | None = None) -> Served:
    """Serve a document (``.xbf``, ``.step``) from the daemon; its page's URL."""
    from cadgen.engine.addresses import key

    path = key(os.fspath(source))
    if isinstance(path, Path) and not path.is_file():
        raise EngineError(f"no document {source}")
    if three is None:
        from cadgen.engine.serve import find_three

        found = find_three()
        three = str(found) if found else None
    answer = _ask({"op": "serve", "path": str(path), "port": port, "three": three})
    return Served(answer["url"], answer["port"])


def inspect(source: Any, ref: str | None = None, *, query: str | None = None) -> Facts | list[Facts]:
    """What a ref names in a document (``refs``: ``hand/finger.2/face 7``, ``base/f:...``, a
    copied ``/abs/kit.xbf#...`` line) and what made it. An element answers what the kernel
    measures of it -- its points placed where its occurrence stands, in the document's frame --
    with its canonical ``ref``, its occurrence's ``path`` and ``made_by``; a part or an assembly
    its volume and its mass: each part's volume times its material's density, summed
    (``cli.weighed``). One answer per selector (a list for several). Without ``ref``, the build
    closure the xbf holds (``previous``): its engine, libraries, source, modules, inputs and
    key, read from the file alone. A ref that names nothing, or several things, is refused by
    name (``refs.RefError``). No query builds a missing model or writes its declared
    outputs. ``query="build"`` returns file metadata/closure or live build metadata;
    ``query="geometry"`` requires a ref, and ``query="closure"`` accepts none."""
    import urllib.error
    import urllib.request

    from cadgen.engine import refs
    from cadgen.engine.cli import weighed

    from cadgen.engine.addresses import key, split

    source, embedded_ref = split(source)
    if ref is not None and embedded_ref is not None and ref != embedded_ref:
        raise EngineError("source reference and ref disagree")
    ref = ref if ref is not None else embedded_ref
    query = query or ("geometry" if ref is not None else "closure")
    if query not in ("closure", "geometry", "build"):
        raise EngineError("query is closure, geometry or build")
    if (query == "geometry") != (ref is not None):
        raise EngineError("only a geometry query requires a reference")
    path = key(source)
    if isinstance(path, Path) and not path.is_file():
        raise EngineError(f"no document {source}")
    if query == "build":
        if not isinstance(path, Path):
            return {"file": None, "live": _ask({"op": "model_info", "address": str(path)})["model"]}
        from cadgen.engine import previous

        stat = path.stat()
        _writer, closure = previous.scan(path)
        return {"file": {"path": str(path), "bytes": stat.st_size, "modifiedAt": stat.st_mtime},
                "live": None, "closure": json.loads(closure) if closure else None}
    if ref is None and isinstance(path, Path):
        from cadgen.engine import previous

        writer, found = previous.scan(path)
        if found is None:
            raise EngineError(f"{path.name} holds no build closure"
                              + (f" (written by {writer})" if writer else ": no engine wrote it"))
        return json.loads(found)
    if ref is not None:
        refs.parse(ref)  # refuse invalid refs before serving
    answer = _ask({"op": "serve", "path": str(path), "build": False})
    base = answer["url"].split("/?", 1)[0]
    ident = answer["doc"]

    def get(route: str) -> Any:
        try:
            with urllib.request.urlopen(urllib.request.Request(f"{base}/api/doc/{ident}{route}",
                    headers={"Authorization": "Bearer " + answer.get("token", "")}), timeout=300) as response:  # noqa: S310
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            try:
                said = json.loads(error.read()).get("error")
            except ValueError:
                said = None
            raise refs.RefError(said or str(error)) from None

    structure = get("")
    if ref is None:
        if not structure.get("closure"):
            raise EngineError(f"{source} holds no build closure")
        return structure["closure"]
    found: list[Facts] = []
    volumes: dict[str, float] = {}
    for hit in refs.resolve_structure(structure, ref, path):
        if hit.kind is None:
            for key in sorted({structure["prototypes"][node.payload["prototype"]]["key"] for node in hit.node.parts()}):
                if key not in volumes:
                    volumes[key] = get(f"/part/{key}/volume")["volume"]
            found.append(weighed(structure, hit, volumes))
            continue
        prototype = structure["prototypes"][hit.node.payload["prototype"]]
        matrix = structure["instances"][hit.node.payload["instance"]]["matrix"]
        try:
            facts = get(f"/part/{prototype['key']}/{hit.kind}/{hit.ordinal}")
        except refs.RefError as error:  # an ordinal past the part's elements
            raise refs.RefError(str(error).replace("the part", hit.path or os.path.basename(str(path)), 1)) from None
        found.append({"ref": hit.ref, "path": hit.path, "part": prototype["name"],
                      "key": prototype["key"], "kind": hit.kind, "ordinal": hit.ordinal, "name": hit.name,
                      **refs.placed_facts(facts, matrix)})
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


# --- what each verb did, told to its observers ------------------------------------------------
#
# ``observers``: functions called after each build, export and snapshot with the verb's facts (``_facts``):
#
#     verb      "build", "export" or "snapshot"
#     ok        it did all it was asked
#     seconds   its wall time here
#     program   ``python model.py`` asked (``model._run_as_program``)
#     models    each model a build reported, or the one an export or a snapshot of a script built first:
#               {ok, cached (its xbf taken whole: ``cache.used``), children (models it called), parts,
#               files (the kinds it wrote or kept)}
#     worker    the warm worker's part: {fresh (started for it; None when no answer said), retired (over
#               its memory cap after it), died (its exit status, when it died under the work; else None)}
#     error     why it failed, or None: {type (the error's class, ``module.Name`` or a builtin's name; None
#               when no error says), frames ([{file, line, function}], innermost last), engine (raised in
#               the engine's own code, or its worker's), refused (the engine refused the request by name),
#               here (raised in this process, as against told by the daemon or a report)}
#     source    export, snapshot: what it read -- "xbf", "step", "glb", "stl", "threemf", "dxf", "urdf",
#               "sdf", "srdf", "model" (a script's model or built code) or "other"
#     found     snapshot: the source file is there (a model's always is)
#     outputs   export: each destination, {kind, seconds (None for one not written)}
#     pose      export, snapshot: a pose was asked for
#
# The facts of a verb another verb runs are its caller's: only the outermost one tells.

observers: list[Callable[[dict[str, Any]], None]] = []

_HEARD = threading.local()  # what the daemon's answers said while a verb ran in this thread (``_heard``)
_ENGINE = os.path.dirname(os.path.abspath(__file__)) + os.sep
_SUFFIXES = {".xbf": "xbf", ".step": "step", ".stp": "step", ".glb": "glb", ".stl": "stl", ".3mf": "threemf",
             ".cadz": "cadz", ".dxf": "dxf", ".urdf": "urdf", ".sdf": "sdf", ".srdf": "srdf"}
_FRAME = re.compile(r'  File "(?P<file>[^"]+)", line (?P<line>\d+), in (?P<function>\S.*)')
_TYPE = re.compile(r"(?P<type>[A-Za-z_][A-Za-z0-9_.]*)(?::|$)")
_DIED = re.compile(r"exited \((?P<status>-?\d+)\) during the build")  # the daemon's word (``daemon.Worker.build``)
_TRACEBACK = "Traceback (most recent call last):"
# The engine's refusals, by module and name: a verb that raised one has imported its module already.
_REFUSALS = (("cadgen.engine.build", "BuildError"), ("cadgen.engine.export", "ExportRefused"),
             ("cadgen.engine.snapshot", "SnapshotRefused"))

_Verb = TypeVar("_Verb", bound=Callable[..., Any])


def _heard(name: str, value: Any) -> None:
    """Something the daemon answered while a verb runs in this thread: kept for its facts."""
    heard = getattr(_HEARD, "facts", None)
    if heard is not None and value is not None:
        heard[name] = value


def _observed(verb: str, function: _Verb) -> _Verb:
    """``function``, telling ``observers`` what it did as it returns or raises."""

    @functools.wraps(function)
    def observed(*args: Any, **kwargs: Any) -> Any:
        if not observers or getattr(_HEARD, "facts", None) is not None:
            return function(*args, **kwargs)
        _HEARD.facts = heard = {}
        started = time.perf_counter()
        try:
            result = function(*args, **kwargs)
        except BaseException as error:
            _HEARD.facts = None
            _tell(verb, args, kwargs, heard, None, error, time.perf_counter() - started)
            raise
        _HEARD.facts = None
        _tell(verb, args, kwargs, heard, result, None, time.perf_counter() - started)
        return result

    return cast(_Verb, observed)


def _tell(verb: str, args: tuple, kwargs: dict[str, Any], heard: dict[str, Any], result: Any,
          error: BaseException | None, seconds: float) -> None:
    try:
        found = _facts(verb, args, kwargs, heard, result, error, seconds)
    except Exception:  # noqa: BLE001 - facts that cannot be made are not told
        return
    for observer in list(observers):
        try:
            observer(found)
        except Exception:  # noqa: BLE001 - an observer never fails the verb it observes
            pass


def _facts(verb: str, args: tuple, kwargs: dict[str, Any], heard: dict[str, Any], result: Any,
           error: BaseException | None, seconds: float) -> dict[str, Any]:
    """What one verb did (the table above), from its arguments, what it returned or raised, and what the
    daemon's answers said meanwhile (``_heard``)."""
    from cadgen.engine import model

    if verb == "build":
        reports = (result.models if isinstance(result, Report) else [result.report] if isinstance(result, Built)
                   else [])
    else:
        reports = list(heard.get("reports") or [])
    worker = {"fresh": (heard.get("worker") or {}).get("fresh"),
              "retired": any(report.get("retired") for report in reports), "died": None}
    found: dict[str, Any] = {
        "verb": verb, "ok": error is None and all(report.get("ok") for report in reports),
        "seconds": round(seconds, 4), "program": bool(getattr(model._CONTEXT, "program", False)),
        "models": [_model(report) for report in reports], "worker": worker,
        "error": _error(error, reports, worker)}
    if verb in ("export", "snapshot"):
        # A job's is the file it draws, which it says as it starts (``snapshot_job``).
        source = heard.get("source") or (args[0] if args else kwargs.get("source"))
        found["source"] = _kind(source)
        found["pose"] = kwargs.get("pose") is not None
    if verb == "export":
        if error is None:
            found["outputs"] = [{"kind": row.kind, "seconds": row.seconds} for row in result]
        else:
            found["outputs"] = [{"kind": _SUFFIXES.get(Path(os.fspath(path)).suffix.lower(), "other"),
                                 "seconds": None} for path in args[1:]]
    if verb == "snapshot":
        found["found"] = found["source"] == "model" or Path(os.fspath(source)).is_file()
    return found


def _kind(source: Any) -> str:
    from cadgen.engine.model import Model

    if isinstance(source, (Model, Built)):
        return "model"
    text = os.fspath(source)
    if "::" in text or text.endswith(".py"):
        return "model"
    return _SUFFIXES.get(Path(text).suffix.lower(), "other")


def _model(report: dict[str, Any]) -> dict[str, Any]:
    return {"ok": bool(report.get("ok")), "cached": bool((report.get("cache") or {}).get("used")),
            "children": len(report.get("children") or {}), "parts": int(report.get("parts") or 0),
            "files": sorted({str(row.get("kind")) for row in (report.get("files") or {}).values() if row.get("kind")})}


def _error(error: BaseException | None, reports: list[dict[str, Any]], worker: dict[str, Any]) -> dict[str, Any] | None:
    """Why a verb failed: the first failed report's error -- a body's (the model's frames, ``build``), or
    a file the engine could not write (``trace``, the engine's) -- else what the verb raised: the worker's
    own error as it answered it, a worker that died under the work (``worker["died"]``), a refusal, or an
    error raised here."""
    told = {"engine": True, "refused": False, "here": False}
    failed = next((report for report in reports if not report.get("ok")), None)
    if failed is not None:
        trace = failed.get("trace")
        return {**_read(trace or failed.get("error") or ""), **told, "engine": bool(trace)}
    if error is None:
        return None
    if isinstance(error, EngineError):
        text = str(error)
        died = _DIED.search(text)
        if died:
            worker["died"] = int(died["status"])
            return {"type": None, "frames": [], **told}
        if _TRACEBACK in text:
            return {**_read(text), **told}
        return {"type": None, "frames": [], **told, "refused": text != "no engine daemon"}
    refusals = tuple(kind for kind in (getattr(sys.modules.get(module), name, None) for module, name in _REFUSALS)
                     if isinstance(kind, type))
    frames = [{"file": frame.f_code.co_filename, "line": int(line or 0), "function": frame.f_code.co_name}
              for frame, line in traceback.walk_tb(error.__traceback__)]
    kind = type(error)
    return {"type": kind.__qualname__ if kind.__module__ == "builtins" else f"{kind.__module__}.{kind.__qualname__}",
            "frames": frames, "engine": bool(frames) and os.path.abspath(frames[-1]["file"]).startswith(_ENGINE),
            "refused": isinstance(error, refusals), "here": True}


def _read(text: str) -> dict[str, Any]:
    """The last error a traceback's text shows (``traceback.format_exception``'s, the model's own frames'
    from ``build``, or the error's line alone): its type's name and its frames, innermost last."""
    kind, frames, expecting = None, [], True
    for line in text.splitlines():
        if line.startswith("Traceback ("):  # a block, or the next one of a chain
            kind, frames, expecting = None, [], True
            continue
        frame = _FRAME.fullmatch(line)
        if frame is not None:
            frames.append({"file": frame["file"], "line": int(frame["line"]), "function": frame["function"].strip()})
            expecting = True
        elif expecting and line and not line[0].isspace():  # the error's own line, after its frames
            named = _TYPE.match(line)
            kind, expecting = named["type"] if named else None, False
    return {"type": kind, "frames": frames}


build = _observed("build", build)
# A script's models build here (law 11: ``python model.py``, ``Model.build()``): a build too.
build_script = _observed("build", build_script)
export = _observed("export", export)
snapshot = _observed("snapshot", snapshot)
snapshot_job = _observed("snapshot", snapshot_job)
