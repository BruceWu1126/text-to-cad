"""The engine's verbs as functions, one per command; ``cadgen xbf`` is a thin argparse over them.

    build(code, *, name=None, xbf=None, step=None, glb=None, stl=None, threemf=None) -> Built
    export(source, *destinations, pose=None, part=None) -> list[Written]
    snapshot(source, out, *, display=None, camera=None, grid=False, preview=False, pose=None,
             width=None, height=None, annotation=None, measurements=None) -> Drawn
    serve(source, port=0) -> Served
    where(source, ref=None) -> Facts (the build closure without a ref)
    status() / stop() / daemon()

Every one goes through the daemon (``client``), so the warm worker of the script, of the code
(by its ``name``) or of the document does the work and keeps what it made in memory for the
next call. ``build`` takes code only (law 12): code declares nothing, so its file flags (``xbf=``,
``step=``, ...) are its declarations, ``True`` for the default path or a path. A script's models
build as their decorators declare: by running the script (``python model.py``), or from Python
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
           "observers", "serve", "snapshot", "status", "stop", "where"]

KINDS = ("xbf", "step", "glb", "stl", "threemf")


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

    def export(self, *destinations: Any, pose: Any = None, part: str | None = None) -> list[Written]:
        return export(self, *destinations, pose=pose, part=part)


# --- reaching the daemon ---------------------------------------------------------------------


def _ask(message: dict[str, Any], on_log: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    from cadgen.engine.client import request

    answer = request({**message, "env": dict(os.environ)}, on_log or (lambda _message: None))
    if answer is None:
        raise EngineError("no engine daemon")
    _heard("worker", answer.get("worker"))
    if "failed" in answer:
        raise EngineError(str(answer["failed"]))
    return answer


def _target(target: Any) -> tuple[Path, list[str] | None]:
    """An export's or a snapshot's script source: ``script.py`` or ``script.py::model``."""
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
           outputs: dict[str, dict[str, Any]] | None = None, verify: bool = False,
           log: Callable[[str], None] | None = None, daemon: bool = True) -> tuple[list[dict[str, Any]], Any, float]:
    """One build -- of code, or of a script's models -- in the warm worker that holds it, or in
    this process: its reports, whether the worker was started for it, and its wall time."""
    from cadgen.engine.build import BuildError

    shown = log or (lambda _text: None)
    cwd = Path.cwd()
    started = time.perf_counter()
    if not daemon:
        from cadgen.engine import build as engine_build

        try:
            reports = engine_build.build(script, functions, log=shown, cwd=cwd, outputs=outputs, code=code,
                                         verify=verify)
        except BuildError as error:
            raise EngineError(str(error)) from None
        fresh = None
    else:
        def relay(message: dict[str, Any]) -> None:
            shown(message["log"] if message.get("stream") == "model" else f"  {message['log']}")

        answer = _ask({"op": "build", "script": str(script), "functions": functions, "cwd": str(cwd),
                       "outputs": outputs, "code": code, "name": name, "verify": verify}, relay)
        reports, fresh = answer["done"], answer.get("worker", {}).get("fresh")
    return reports, fresh, time.perf_counter() - started


def build(code: str, *, name: str | None = None, xbf: Any = None, step: Any = None, glb: Any = None,
          stl: Any = None, threemf: Any = None, verify: bool = False, log: Callable[[str], None] | None = None,
          daemon: bool = True) -> Built:
    """Build source text, ``cadgen build SOURCE`` (law 12): ``code`` assigns its final shape to
    ``result``, and is built as ``name`` (``model`` by default) in the warm worker of that name.
    Code declares nothing, so its file flags are its declarations, additive among themselves:
    ``True`` writes the kind where a decorator with no path puts it, in the working folder
    (``XBF/<name>.xbf``, ``<name>.step``, ...), a path writes it there; no flag, no file. A child
    model the code calls writes the files it declares (law 10). ``verify`` runs every body the
    memo would replay and compares it with the logged one (``build.build``); ``daemon=False``
    builds in this process. Returns once every file is on disk. A script's models build by
    running the script, or by ``Model.build()``."""
    from cadgen.engine.build import code_script

    outputs = _outputs({"xbf": xbf, "step": step, "glb": glb, "stl": stl, "threemf": threemf})
    name = name or "model"
    cwd = Path.cwd()
    script = code_script(name, cwd)
    reports, _fresh, wall = _built(script, [name], code=code, name=name, outputs=outputs, verify=verify, log=log,
                                   daemon=daemon)
    return Built(name, script, {**reports[0], "wall": round(wall, 4)}, cwd)


def build_script(script: Path, functions: list[str] | None = None, *, verify: bool = False,
                 log: Callable[[str], None] | None = None, daemon: bool = True) -> Report:
    """A script's models -- ``functions``, or every one it defines -- each built as its
    decorators declare, and every child model each calls as its own declare (law 10): what
    ``python script.py`` (every model) and ``Model.build()`` (one) ask. A script takes no file
    flags: its decorators are the whole definition of what it writes (law 11)."""
    reports, fresh, wall = _built(Path(script).resolve(), functions, verify=verify, log=log, daemon=daemon)
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


def export(source: Any, *destinations: Any, pose: Any = None, part: str | None = None, inputs: bool = True,
           log: Callable[[str], None] | None = None, daemon: bool = True) -> list[Written]:
    """Write a document to each destination, its kind from its suffix (``.xbf``, ``.step``,
    ``.glb``, ``.stl``, ``.3mf``): the document of an ``.xbf``, of a ``.step`` read into one,
    of a script's model (``script.py::model`` or a ``Model``, built first) or of built code
    (a ``Built``). Several destinations read the document once. ``daemon=False`` does it in
    this process, cold. A GLB carries every motion the document defines, as a clip.

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
    done = _exported(order, spec, shown, daemon)
    return written + [Written(Path(row["path"]), row["kind"], row["bytes"], row["seconds"]) for row in done["files"]]


def _exported(order: dict[str, Any], spec: dict[str, Any], shown: Callable[[str], None], daemon: bool) -> dict:
    """An ``export`` request answered by the worker that holds the document (``worker._export``)."""
    if daemon:
        done = _ask({**order, "code": spec.get("code")}, lambda message: shown(message["log"]))["done"]
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
    annotation's ink was drawn in, which the packet holds."""
    from cadgen.engine import snapshot as drawing
    from cadgen.engine.model import Model

    out = Path(os.fspath(out)).resolve()
    if isinstance(source, (Model, Built)) or (not isinstance(source, Path) and (
            "::" in os.fspath(source) or os.fspath(source).endswith(".py"))):
        if out.suffix.lower() != ".png":
            raise drawing.SnapshotRefused(f"a snapshot is a .png; {out.name} is not one")
        spec = _source(source)
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


def where(source: Any, ref: str | None = None) -> Facts | list[Facts]:
    """What made one element of a document, and what the kernel measures of it: ``ref`` is
    ``PART/face N`` (``edge``, ``vertex``) or an element name (``f:...``). A part or an assembly
    (its name or path) answers its volume and its mass: each part's volume times its material's
    density, summed (``cli.weighed``). Without ``ref``, the build closure the xbf holds
    (``previous``): its engine, libraries, source, modules, inputs and key, read from the file
    alone."""
    import urllib.request

    from cadgen.engine.cli import elements, placements, weighed

    path = Path(os.fspath(source)).resolve()
    if not path.is_file():
        raise EngineError(f"no document {source}")
    if ref is None:
        from cadgen.engine import previous

        writer, found = previous.scan(path)
        if found is None:
            raise EngineError(f"{path.name} holds no build closure"
                              + (f" (written by {writer})" if writer else ": no engine wrote it"))
        return json.loads(found)
    answer = _ask({"op": "serve", "path": str(path)})
    base, _sep, ident = answer["url"].partition("/?doc=")

    def get(route: str) -> Any:
        with urllib.request.urlopen(f"{base}/api/doc/{ident}{route}", timeout=300) as response:  # noqa: S310
            return json.loads(response.read())

    structure = get("")
    held = placements(structure, ref)
    if held:
        keys = {structure["prototypes"][i["prototype"]]["key"] for i in held}
        return weighed(structure, held, {key: get(f"/part/{key}/volume")["volume"] for key in sorted(keys)}, ref)
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
#     source    export, snapshot: what it read -- "xbf", "step", "glb", "stl", "threemf", "model" (a script's
#               model or built code) or "other"
#     found     snapshot: the source file is there (a model's always is)
#     outputs   export: each destination, {kind, seconds (None for one not written)}
#     pose      export, snapshot: a pose was asked for
#
# The facts of a verb another verb runs are its caller's: only the outermost one tells.

observers: list[Callable[[dict[str, Any]], None]] = []

_HEARD = threading.local()  # what the daemon's answers said while a verb ran in this thread (``_heard``)
_ENGINE = os.path.dirname(os.path.abspath(__file__)) + os.sep
_SUFFIXES = {".xbf": "xbf", ".step": "step", ".stp": "step", ".glb": "glb", ".stl": "stl", ".3mf": "threemf",
             ".cadz": "cadz"}
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
        "seconds": round(seconds, 4), "program": bool(getattr(model._run_as_program, "running", False)),
        "models": [_model(report) for report in reports], "worker": worker,
        "error": _error(error, reports, worker)}
    if verb in ("export", "snapshot"):
        source = args[0] if args else kwargs.get("source")
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
