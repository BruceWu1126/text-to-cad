"""A build: a script's models run, their results made documents, and the files they
declare written -- in this process, with the memo this process holds.

``build(script, functions)`` loads the script afresh (its own first-party modules
too: an edit anywhere in the model's code is read), runs each model named, or every
model the script defines, in one memo run, and then, outside the run, makes each
result a document (``document.collect``), names its parts' elements from the run's
history, writes the xbf when its content changed, and writes the exports in order:
those files and nothing else. Every model that ran writes its files: each target, and
then each child model its run ran, from the child's own document (its result as its body
returned it, its closure its own script's), once a build, its report under the target's
(``child_models``). A model whose body raises reports the error with the model's own
frames and leaves its files as they were.

What this process remembers between builds is in memory only: per part ``TShape``,
its element names and its geometry key; per model, the document it last wrote, kept
(``document.Kept``). A part a warm build hands back unchanged (the memo's hit is the same
object) is neither named nor keyed again; a document whose every part, placement, name
and colour is what the file already holds is not touched or rewritten; and any other
document is the kept one patched, only its changed prototypes made again.

Before a model's body runs, unless the memo replays it, its xbf is asked (``previous``):
an xbf holding the key the model has now is what the body would make, and the body is
not run, nor any file written.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import json
import os
import sys
import time
import traceback
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

__all__ = ["BuildError", "build", "load_script"]


class BuildError(Exception):
    """A request the build cannot run: no such script, no such model."""


@dataclass
class _Written:
    signature: tuple
    stats: dict[str, tuple | None]
    # The document as written, kept in memory: the next build patches it (``document.Kept``).
    kept: Any = None
    # The build closure the file holds (``previous.manifest``, as text), or None.
    record: str | None = None
    # Per derived file this process wrote: the document's signature and the kind it was
    # written with, and its stat as written. The same three again: the file is kept.
    files: dict[str, tuple] | None = None
    # The tree the kept document was made from: what a derived file is translated from.
    tree: Any = None


@dataclass
class _Named:
    """What a build made of one part ``TShape``: its element names, its geometry key, the
    shape the document holds of it; the functions its names come from, each with its
    skeleton then (``sources.skeleton``); a number that changes whenever its names do;
    and the provenance of each feature its names hold (``made_by``)."""

    names: Any
    key: str
    shape: Any
    refs: dict[tuple[str, str], str | None]
    token: int
    features: dict[str, dict[str, Any]]


# Per part TShape, what a build made of it (``_Named``), the most recently used last.
_PARTS: "OrderedDict[Any, _Named]" = OrderedDict()
_PARTS_HELD = 1 << 16
_TOKENS = __import__("itertools").count(1)
# Per model: the document this process last wrote, kept to be patched by the next build,
# and each declared file as it left it.
_WRITTEN: dict[str, _Written] = {}
# Per xbf this process last built (its path, ``os.path.normcase``d): per part key, what
# the build made of the part -- what ``made_by`` answers from. In memory only: provenance
# is served while this process lives, never written.
_BUILT: dict[str, dict[str, _Named]] = {}
# Per model, the functions its last run in this process ran or replayed, apart from their
# arguments: a model calling one a verification found impure is not taken from its file.
_FAMILIES: dict[str, frozenset[str]] = {}


def _first_party_modules() -> list[str]:
    from cadgen.engine.kernel.trace import first_party_file

    return [name for name, module in list(sys.modules.items())
            if name != "__main__" and first_party_file(getattr(module, "__file__", None) or "")]


class _SourceOnlyLoader(importlib.machinery.SourceFileLoader):
    """A first-party module compiled from the bytes on disk, never from a ``.pyc``: CPython
    takes a ``__pycache__`` file as current by the source's whole-second mtime and its size,
    so two same-length edits inside one second -- an agent's edit loop -- would run the first
    edit's bytecode while the closure keys the second's source."""

    def get_code(self, fullname: str) -> Any:
        path = self.get_filename(fullname)
        return compile(self.get_data(path), path, "exec", dont_inherit=True)


class _FirstPartyFromSource(importlib.abc.MetaPathFinder):
    """Every first-party module (``trace.first_party_file``) through ``_SourceOnlyLoader``.
    It asks the path finder itself, never the rest of ``sys.meta_path`` (two finders that
    each ask the other would hand one lookup back and forth), and answers nothing else."""

    def find_spec(self, fullname: str, path: Any, target: Any = None) -> Any:
        from cadgen.engine.kernel.trace import first_party_file

        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        if (spec is None or type(spec.loader) is not importlib.machinery.SourceFileLoader or not spec.origin
                or not first_party_file(spec.origin)):
            return None
        spec.loader = _SourceOnlyLoader(spec.loader.name, spec.loader.path)
        return spec


def _from_source() -> None:
    """The model's code runs from its source: no bytecode written for anything a build
    imports, none read for a first-party module. Once per process, first on the meta path."""
    sys.dont_write_bytecode = True
    if not any(isinstance(finder, _FirstPartyFromSource) for finder in sys.meta_path):
        sys.meta_path.insert(0, _FirstPartyFromSource())


def load_script(script: Path) -> Any:
    """The script as a fresh module, after every first-party module a previous build loaded is
    forgotten: the model's code as it is on disk now, imported as Python imports it. Its
    folder comes first on the module search path (``model.seat``). A script inside a package
    (``model.module_of``) is imported by its dotted name (``proj.parts.model``), as ``python -m``
    imports it: its packages first, from their root, which the search path holds next, so a
    relative import, a module beside it by its own name and the package by its name all
    resolve. A loose script is the module its stem names. Every module of the model's own is
    compiled from its source on disk (``_from_source``): no bytecode is written or read."""
    from cadgen.engine.model import module_of, seat

    _from_source()
    for name in _first_party_modules():
        sys.modules.pop(name, None)
    importlib.invalidate_caches()
    name, package, root = module_of(script)
    seat(script, root)
    if package is not None:
        module = importlib.import_module(name)
        found = getattr(module, "__file__", None)
        if not found or Path(found).resolve() != Path(script).resolve():
            raise BuildError(f"{script} is module {name} of its package, but importing {name} finds {found}")
        return module
    spec = importlib.util.spec_from_file_location(name, script, loader=_SourceOnlyLoader(name, str(script)))
    if spec is None or spec.loader is None:
        raise BuildError(f"cannot load {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def _model_traceback(error: BaseException) -> str:
    """The error with the model's own frames: those of its first-party files."""
    from cadgen.engine.kernel.trace import first_party_file

    frames = [frame for frame in traceback.extract_tb(error.__traceback__) if first_party_file(frame.filename)]
    lines = ["Traceback (the model's frames):\n"] + traceback.format_list(frames) if frames else []
    lines += traceback.format_exception_only(type(error), error)
    return "".join(lines).rstrip()


def code_script(name: str, cwd: Path) -> Path:
    """The path code built from source text is known by (``cadgen build SOURCE``): never a
    file on disk, a name in the working folder's ``(code)`` folder, ``<name>.py``."""
    return Path(cwd).resolve() / CODE_FOLDER / f"{name}.py"


CODE_FOLDER = "(code)"
_CODE_NAME = __import__("re").compile(r"[A-Za-z_][A-Za-z0-9_]*")


def code_source(name: str, code: str) -> str:
    """Code's source as the module it is run as: a function ``name`` whose body is the code,
    ending in ``return result``. Every line is indented once, but the lines inside a string
    that spans lines, whose text the code's own. The code assigns its final shape to
    ``result``; code that does not is refused by name."""
    import ast
    import io
    import tokenize

    if not _CODE_NAME.fullmatch(name or ""):
        raise BuildError(f"--name {name!r} is no Python identifier")
    try:
        tree = ast.parse(code, filename="<code>")
    except SyntaxError as error:
        raise BuildError(f"the code does not parse: {error}") from None
    assigned = any(isinstance(node, ast.Name) and node.id == "result" and isinstance(node.ctx, ast.Store)
                   for node in ast.walk(tree))
    if not assigned:
        raise BuildError("the code assigns no `result`: code assigns its final shape to `result`")
    inside: set[int] = set()
    try:
        for token in tokenize.generate_tokens(io.StringIO(code).readline):
            if token.type == tokenize.STRING and token.end[0] > token.start[0]:
                inside.update(range(token.start[0] + 1, token.end[0] + 1))
    except (tokenize.TokenError, IndentationError):
        pass
    lines = code.splitlines()
    body = [line if number in inside or not line.strip() else "    " + line
            for number, line in enumerate(lines, start=1)]
    return f"def {name}():\n" + "\n".join(body) + "\n    return result\n"


def load_code(script: Path, name: str, code: str, outputs: dict[str, dict[str, Any]] | None) -> Any:
    """Code built from source text, as a fresh module held in memory only: its function
    ``name`` a model whose outputs are the flags' (``outputs``). Its source is kept where the
    engine's readers find a file's (``sources.MEMORY``, ``linecache``), never on disk."""
    import linecache
    import types

    from cadgen.engine import sources
    from cadgen.engine.model import Model, Output

    text = code_source(name, code)
    _from_source()
    for module_name in _first_party_modules():
        sys.modules.pop(module_name, None)
    importlib.invalidate_caches()
    for folder in (str(script.parent.parent),):
        if folder in sys.path:
            sys.path.remove(folder)
        sys.path.insert(0, folder)
    sources.MEMORY[str(script)] = text.encode("utf-8")
    linecache.cache[str(script)] = (len(text), None, text.splitlines(True), str(script))
    module_name = f"_cadgen_code_{name}"
    module = types.ModuleType(module_name)
    module.__file__ = str(script)
    sys.modules[module_name] = module
    try:
        exec(compile(text, str(script), "exec"), module.__dict__)  # noqa: S102 - the author's code, as a script runs
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    model = Model(module.__dict__[name])
    model.outputs = [Output(kind, given["out"]) for kind, given in (outputs or {}).items()]
    module.__dict__[name] = model
    return module


def _declared(name: str, outputs: dict[str, dict[str, Any]] | None, base: Path) -> dict[str, dict[str, Any]]:
    """Code's flags as its declarations (law 12): each kind at its path from ``base``, the
    working folder, or where a decorator with no path puts it there."""
    from cadgen.engine.model import KINDS, SUFFIXES, XBF_FOLDER, check_output

    declared = {}
    for kind, given in (outputs or {}).items():
        if kind not in KINDS:
            raise BuildError(f"no output kind {kind}; the kinds are {', '.join(KINDS)}")
        out = (given or {}).get("out")
        try:
            check_output(kind, out, f"--{kind}")
        except (TypeError, ValueError) as error:
            raise BuildError(str(error)) from None
        default = Path(XBF_FOLDER) / f"{name}.xbf" if kind == "xbf" else Path(name + SUFFIXES[kind])
        declared[kind] = {"out": os.path.normpath(os.path.abspath(base / (out or default)))}
    return declared


def build(script: Path, functions: list[str] | None = None, *, log: Callable[[str], None] = lambda _line: None,
          cwd: Path | None = None, outputs: dict[str, dict[str, Any]] | None = None, code: str | None = None,
          verify: bool = False) -> list[dict[str, Any]]:
    """Build the script's models (``functions``, or every model it defines); one report
    per model, holding those of the child models its run ran, each of which wrote its own
    files (``child_models``). Raises BuildError for a script or function that is not there;
    a body that raises is its model's report (``ok`` false, ``error`` the model's traceback).

    A script's models write what their decorators declare (``Model.plan``) and nothing else:
    a script takes no file flags (law 11). ``code`` builds source text instead: ``script`` is
    then ``code_script(name, cwd)`` and ``functions`` the one name; code declares nothing, so
    its files are its flags, ``outputs`` (law 12): ``{kind: {"out": path or None}}``, the kinds
    ``xbf``, ``step``, ``glb``, ``stl``, ``threemf``, each at its path from the working folder or,
    with none, where a decorator with no path puts it there (``XBF/<name>.xbf``,
    ``<name><suffix>``). Every file shows the model as its joints place it: a pose or a part is
    the ``export`` verb's, never a build's.

    ``verify``: every body the memo would replay -- a model's, a ``@memo`` call's -- runs
    instead, and its fresh result is compared with the logged one; the report's
    ``verify`` names each body checked and how it went, and a function whose result
    differs runs on every call for the worker's life (``memo.always``)."""
    from cadgen.engine import previous
    from cadgen.engine.kernel import determinism, memo
    from cadgen.engine.model import _MODELS, BuildContext, models_in

    script = Path(script).resolve() if code is None else Path(script)
    if code is None and not script.is_file():
        raise BuildError(f"no script {script}")
    if code is None and outputs:
        raise BuildError("file flags declare code's files; a script's models write what their decorators declare")
    if code is not None:
        if not functions or len(functions) != 1:
            raise BuildError("code is one model: name it with --name")
        outputs = _declared(functions[0], outputs, Path(cwd or Path.cwd()))
    determinism.install()
    memo.install()
    shown = _shown(script, cwd) if code is None else "<code>"
    reports: list[dict[str, Any]] = []
    started = time.perf_counter()
    # Per target: the model, its result (None: it wrote nothing), its body's seconds, and the
    # models that ran as its children whose files the build writes, each with its result.
    results: list[tuple[Any, Any, float, list]] = []
    from cadgen.engine import reader

    # One reader process for the build's STEP reads (``reader``), gone when the build is.
    with memo.run(script) as run, BuildContext() as context, reader.session():
        run.verify = verify
        _MODELS.clear()  # the models this build defines, known by name to what its replays ran
        try:
            module = load_script(script) if code is None else load_code(script, functions[0], code, outputs)
        except BuildError:
            raise
        except Exception as error:  # noqa: BLE001 - the model's own error, reported
            return [{"ok": False, "model": f"{shown}", "error": _model_traceback(error)}]
        loaded = time.perf_counter()
        context.loaded = set(_first_party_modules())
        context.base = script.parent if code is None else Path(cwd or Path.cwd())
        context.script = script
        models = models_in(vars(module), script)
        if functions:
            by_name = {model.name: model for model in models}
            missing = [name for name in functions if name not in by_name]
            if missing:
                raise BuildError(f"{shown} defines no model {', '.join(missing)}"
                                 + (f"; it defines {', '.join(by_name)}" if by_name else "; it defines none"))
            models = [by_name[name] for name in functions]
        elif not models:
            from cadgen.engine.model import Memo

            marked = [value.name for value in vars(module).values()
                      if isinstance(value, Memo) and value.script == script]
            raise BuildError(f"{shown} defines no model" + (
                f"; {', '.join(marked)} {'is a @memo function' if len(marked) == 1 else 'are @memo functions'}: "
                "a model calls it, and it writes no file" if marked else ""))
        # Every model that runs writes its files once a build: a target its own, and a child
        # those of the first target whose run ran it.
        written = {f"{model.script}::{model.name}" for model in models}

        def ran(model: Any, before: set[str]) -> list:
            found = []
            for child, made in context.children(model, run, before):
                if f"{child.script}::{child.name}" not in written:
                    written.add(f"{child.script}::{child.name}")
                    found.append((child, made))
            return found

        for model in models:
            body_started = time.perf_counter()
            tally = dict(run.tally)
            keys_before = context.key_seconds
            run.reads.clear()
            run.unread.clear()
            try:
                if verify:
                    verdict, previous_report = {"used": False, "reason": "verifying"}, None
                else:
                    verdict, previous_report = _previous(model, context, shown, log)
            except Exception as error:  # noqa: BLE001 - the cache never fails a build: the body runs
                verdict, previous_report = {"used": False, "reason": f"the cache failed: {error}"}, None
            if previous_report is not None:
                timings = previous_report["timings"]
                timings["load"] = round(loaded - started, 4)
                timings["total"] = round(sum(value for name, value in timings.items() if name != "total"), 4)
                reports.append(previous_report)
                results.append((model, None, 0.0, []))
                continue
            verdict["seconds"] = round(time.perf_counter() - body_started, 4)
            body_started = time.perf_counter()
            before = set(run.statuses)
            context.called = {}
            try:
                result = context.root(model)
            except Exception as error:  # noqa: BLE001 - the model's own error, reported
                reports.append({"ok": False, "model": f"{shown}::{model.name}", "error": _model_traceback(error)})
                # A child that ran before the error writes its files all the same.
                results.append((model, None, 0.0, ran(model, before)))
                continue
            body = time.perf_counter() - body_started
            calls = {name: run.tally[name] - tally.get(name, 0) for name in run.tally}
            results.append((model, result, body, ran(model, before)))
            # What the bodies' keys cost (inside ``body``) and the modules each body's key
            # took by their bytes, with why.
            retention = {"keys": round(context.key_seconds - keys_before, 4),
                         "fallbacks": {name: list(reasons) for name, reasons in context.fallbacks.items()
                                       if name == model.identity or name in context.timings}}
            # The files each body this model ran or replayed read itself, its inputs by their
            # bytes; and one it read that is no regular file, which keeps it running: say which.
            retention["reads"] = {}
            for table, into in ((run.reads, None), (run.unread, "fallbacks")):
                for body_name, paths in table.items():
                    body_script, _, function = body_name.rpartition("::")
                    identity = f"{Path(body_script).name}::{function}"
                    shown_paths = [_shown(Path(path), Path(body_script).parent) for path in paths]
                    if into is None:
                        retention["reads"][identity] = shown_paths
                    else:
                        retention["fallbacks"].setdefault(identity, []).extend(
                            f"reads {path}, not a regular file: it runs on every build" for path in shown_paths)
            reports.append({"ok": True, "model": f"{shown}::{model.name}", "memo": calls, "retention": retention,
                            "children": {name: round(seconds, 4) for name, seconds in context.timings.items()
                                         if name != model.identity},
                            "bodies": _statuses(run, f"{model.script}::{model.name}"), "cache": verdict})
            if verify:
                reports[-1]["verify"] = _verified(run, f"{model.script}::{model.name}")
            _FAMILIES[f"{model.script}::{model.name}"] = frozenset(
                _family(body) for body in previous.closure(run.keys, f"{model.script}::{model.name}"))
            context.timings.clear()
    load_seconds = loaded - started
    for report, (model, result, body, children) in zip(reports, results):
        if result is not None:
            report["timings"] = {"load": round(load_seconds, 4), "body": round(body, 4)}
            _written(model, result, run, report, log, model.plan(), context)
        # Each child that ran writes the files it declares, from its own document, after its
        # target's: its report is the target's, under its identity (``child_models``).
        for child, made in children:
            identity = f"{_shown(child.script, cwd)}::{child.name}"
            row = report.setdefault("child_models", {})[identity] = {"ok": True, "timings": {}}
            _written(child, made, run, row, log, child.plan(), context)
            if not row["ok"]:
                report["ok"] = False
                report.setdefault("error", f"{identity}: {row['error']}")
    return reports


def _written(model: Any, result: Any, run: Any, report: dict[str, Any], log: Callable[[str], None], plan: list,
             context: Any) -> None:
    """``_finish``, a file that cannot be written its model's error."""
    try:
        _finish(model, result, run, report, log, plan, context)
    except Exception as error:  # noqa: BLE001 - a file that cannot be written is the build's error
        report["ok"] = False
        report["error"] = "".join(traceback.format_exception_only(type(error), error)).rstrip()
        report["trace"] = traceback.format_exc()


def _shown(script: Path, cwd: Path | None) -> str:
    try:
        return os.path.relpath(script, cwd or Path.cwd())
    except ValueError:
        return str(script)


def _finish(model: Any, result: Any, run: Any, report: dict[str, Any], log: Callable[[str], None],
            plan: list | None = None, context: Any = None) -> None:
    from cadgen.engine import document

    timings = report["timings"]
    plan = plan if plan is not None else model.plan()
    targets = [(target.kind, target.path) for target in plan]
    clock = time.perf_counter()
    tree = document.collect(result, model.name)
    xbf = next((path for kind, path in targets if kind == "xbf"), None)
    if xbf is not None and context is not None:
        from cadgen.engine import previous

        modules, unkeyable = context.modules(model)
        tree.closure = previous.text(previous.manifest(run.keys, f"{model.script}::{model.name}", Path(xbf).parent,
                                                       model, modules, unkeyable))
    timings["collect"] = round(time.perf_counter() - clock, 4)
    # What the joints' harvest found inconsistent: said, and the document written all the same.
    if tree.mechanism is not None and tree.mechanism.warnings:
        report["kinematics"] = {"warnings": list(tree.mechanism.warnings)}
    clock = time.perf_counter()
    named = 0
    skeletons: dict[str, dict[str, str]] = {}
    tokens = []
    built: dict[str, _Named] = {}
    for part in tree.parts:
        tshape = part.shape.TShape()
        cached = _PARTS.get(tshape)
        if cached is None:
            names, features, refs = _names(run, part)
            written_shape = document.direct(part.shape)
            cached = _PARTS[tshape] = _Named(names, document.geometry_key(written_shape), written_shape, refs,
                                              next(_TOKENS), features)
            named += 1
            while len(_PARTS) > _PARTS_HELD:
                _PARTS.popitem(last=False)
        else:
            _PARTS.move_to_end(tshape)
            if not _current(cached.refs, skeletons):
                # The same shape, but the code its names come from now names it otherwise
                # (a variable or a function renamed, an assignment added): named again.
                names, features, refs = _names(run, part)
                if names != cached.names:
                    cached.names, cached.token = names, next(_TOKENS)
                cached.refs, cached.features = refs, features
                named += 1
        part.names, part.key, part.shape = cached.names, cached.key, cached.shape
        tokens.append(cached.token)
        built[cached.key] = cached
    document.merge(tree)
    signature = (_signature(tree), tuple(tokens), tree.closure)
    timings["names"] = round(time.perf_counter() - clock, 4)
    report["parts"] = len(tree.parts)
    report["named"] = named
    for kind, path in targets:
        if kind == "xbf":
            _BUILT[os.path.normcase(str(path))] = built
    _write(model, plan, tree, signature, report, log)
    timings["total"] = round(sum(value for name, value in timings.items() if name != "total"), 4)


def _write(model: Any, plan: list, tree: Any, signature: tuple, report: dict[str, Any],
           log: Callable[[str], None]) -> None:
    """The model's files: the xbf first -- saved, and the viewer woken with it, before any
    derived file is written -- then each derived file, translated from the document
    (``export.translate``). A file whose document and stat are what this process left is
    kept, not rewritten; the xbf is not rewritten when its bytes are the file's."""
    from cadgen.engine import document

    timings = report["timings"]
    key = f"{model.script}::{model.name}"
    remembered = _WRITTEN.get(key)
    stats = {str(target.path): _stat(target.path) for target in plan}
    files = dict((remembered.files or {}) if remembered is not None else {})
    same = remembered is not None and remembered.signature == signature

    def current(target: Any) -> bool:
        stat = stats[str(target.path)]
        if not same or stat is None:
            return False
        if target.kind == "xbf":
            return remembered.stats.get(str(target.path)) == stat
        return files.get(str(target.path)) == (signature, target.kind, stat)

    stale = [target for target in plan if not current(target)]
    written: dict[str, Any] = {}
    for target in plan:
        if target not in stale:
            written[str(target.path)] = {"kind": target.kind, "written": False, "seconds": 0.0,
                                         "bytes": stats[str(target.path)][1]}
    kept = remembered.kept if remembered is not None and remembered.kept is not None else None
    # The document is made again when it changed, when its xbf is written, or when none is
    # held: a build always leaves its document in memory, for the exports asked of it later.
    if not same or kept is None or kept.doc is None or any(target.kind == "xbf" for target in stale):
        clock = time.perf_counter()
        kept = kept if kept is not None else document.Kept()
        doc = document.to_xcaf(tree, kept)
        timings["document"] = round(time.perf_counter() - clock, 4)
        report["converted"] = {"prototypes": len(tree.order), "converted": kept.converted, "patched": kept.patched}
    else:
        doc = kept.doc
    if stale:
        xbf = next((target.path for target in plan if target.kind == "xbf"), None)
        source = None
        for target in stale:
            clock = time.perf_counter()
            if target.kind == "xbf":
                # A file this process did not write as it stands -- in a fresh process, one it
                # took, one another worker wrote (a child's, which its parent's build writes
                # too) -- is compared with what it made: the same bytes are not written again.
                # A save tells the viewer before the build goes on (``worker``).
                theirs = remembered is None or remembered.kept is None or \
                    remembered.stats.get(str(target.path)) != stats[str(target.path)]
                fresh, size = document.save(doc, target.path, compare=theirs)
            else:
                from cadgen.engine import export

                if source is None:
                    source = export.Source.built(doc, tree, xbf)
                size = export.translate(source, target.kind, target.path)
                fresh = True
                files[str(target.path)] = (signature, target.kind, _stat(target.path))
            seconds = time.perf_counter() - clock
            timings[target.kind] = round(seconds, 4)
            written[str(target.path)] = {"kind": target.kind, "written": fresh, "seconds": round(seconds, 4),
                                         "bytes": size}
            log(f"{'wrote' if fresh else 'kept'} {os.path.relpath(target.path)} ({size / 1e6:.1f} MB) in {seconds:.2f}s")
    _WRITTEN[key] = _Written(signature, {str(target.path): _stat(target.path) for target in plan}, kept, tree.closure,
                             files, tree)
    report["files"] = {str(target.path): written[str(target.path)] for target in plan}


def held(script: Path | None = None, name: str | None = None, *, path: Path | None = None) -> Any:
    """The document this process last built, as an ``export.Source``: model ``name`` of
    ``script`` (any of its models when ``name`` is None and it has one), or the model whose
    xbf is ``path`` while the file is as the build left it. None when it holds none."""
    from cadgen.engine import export

    found = []
    for key, written in _WRITTEN.items():
        if written.kept is None or written.kept.doc is None or written.tree is None:
            continue
        model_script, _sep, function = key.rpartition("::")
        xbf = next((Path(target) for target in written.stats if target.endswith(".xbf")), None)
        if path is not None:
            if xbf is None or os.path.normcase(str(xbf)) != os.path.normcase(str(Path(path).resolve())):
                continue
            if _stat(xbf) != written.stats.get(str(xbf)):
                continue
        elif script is not None and (Path(model_script) != Path(script) or (name is not None and function != name)):
            continue
        found.append((written, xbf))
    if len(found) != 1:
        return None
    written, xbf = found[0]
    return export.Source.built(written.kept.doc, written.tree, xbf)


def _statuses(run: Any, name: str) -> dict[str, str]:
    """How each body of a model went in this build, by its identity: ``run``, ``replayed``."""
    from cadgen.engine import previous

    return {_identity(body): run.statuses.get(body, "run") for body in previous.closure(run.keys, name)}


def _verified(run: Any, name: str) -> dict[str, Any]:
    """A verifying build's finding for one model: each body of it the memo would have
    replayed, ``same``, ``differs: why``, or ``follows`` the bodies it called that differ;
    the mismatches (those that differ themselves); every function marked to run always."""
    from cadgen.engine import previous
    from cadgen.engine.kernel import memo

    checked = {_identity(body): run.verified[body] for body in previous.closure(run.keys, name)
               if body in run.verified}
    return {"checked": checked,
            "mismatches": sorted(body for body, found in checked.items() if found.startswith("differs")),
            "always_run": sorted(_identity(body) for body in memo.always())}


def _family(name: str) -> str:
    """A body's function, apart from the arguments a ``@memo`` call's name holds."""
    script, _, function = name.rpartition("::")
    return f"{script}::{function.partition('(')[0]}"


def _identity(name: str) -> str:
    script, _, function = name.rpartition("::")
    return f"{Path(script).name}::{function}"


def _previous(model: Any, context: Any, shown: str, log: Callable[[str], None]) -> tuple[dict, dict | None]:
    """The model's previous document asked before its body runs (``previous``): the
    verdict, and the model's report where the document is what the body would make -- the
    key the model has now is the one the document holds -- so the body is not run and the
    files are left as they are. A body the memo replays is replayed, as ever."""
    from cadgen.engine import document, previous
    from cadgen.engine.kernel import memo

    targets = model.targets()
    xbf = next((Path(path) for kind, path in targets if kind == "xbf"), None)
    if xbf is None:
        return {"used": False, "reason": "the model declares no xbf"}, None
    if not memo.enabled() or os.environ.get("CADGEN_ENGINE_BODIES", "1").strip() == "0":
        return {"used": False, "reason": "retention is off"}, None
    name = f"{model.script}::{model.name}"
    clock = time.perf_counter()
    source = context.source(model)
    if memo.replayable(name, source):
        return {"used": False, "reason": "replayed from memory"}, None
    always = memo.always()
    impure = sorted(_identity(family) for family in _FAMILIES.get(name, ()) if family in always)
    if impure:
        return {"used": False, "reason": f"it calls {', '.join(impure)}, which a verification found impure"}, None
    stats = {str(path): _stat(path) for _kind, path in targets}
    remembered = _WRITTEN.get(name)
    where = "memory"
    if remembered is not None and remembered.stats == stats:
        # The file this process last wrote or took, as it left it: its record is in memory.
        text = remembered.record
        if text is None:
            return {"used": False, "reason": "its last build wrote no key"}, None
    else:
        where = "file"
        if stats[str(xbf)] is None:
            return {"used": False, "reason": "no file yet"}, None
        missing = [path for kind, path in targets if kind != "xbf" and stats[str(path)] is None]
        if missing:
            return {"used": False, "reason": f"{_shown(Path(missing[0]), xbf.parent)} is missing"}, None
        writer, text = previous.scan(xbf)
        if writer != document._writer():
            return {"used": False, "reason": f"written by {writer or 'another program'}"}, None
        if text is None:
            return {"used": False, "reason": "the file holds no key"}, None
    found = json.loads(text)
    if found.get("key") is None:
        return {"used": False, "reason": "the file holds no key"}, None
    modules, _unkeyable = context.modules(model)
    now, changes = previous.again(found, model, xbf.parent, modules)
    keyed = time.perf_counter()
    if now != found["key"]:
        changes = changes or ["its closure changed"]
        shown_changes = changes[:3] + ([f"{len(changes) - 3} more"] if len(changes) > 3 else [])
        return {"used": False, "reason": ", ".join(shown_changes), "changed": changes}, None
    timings = {"key": round(keyed - clock, 4)}
    if where == "file":
        # Taken only once it reads whole, written by this engine, holding the record its head showed.
        try:
            doc = document._read(xbf)
            held = previous.read_record(doc)
        except Exception as error:  # noqa: BLE001 - a document that does not read is built again
            return {"used": False, "reason": f"the file does not read: {error}"}, None
        del doc
        if held != (document._writer(), text):
            return {"used": False, "reason": "the file does not hold the closure its head shows"}, None
        timings["read"] = round(time.perf_counter() - keyed, 4)
        _WRITTEN[name] = _Written(("loaded", found["key"]), stats, None, text)
    log(f"took {os.path.relpath(xbf)}: its key is the model's ({found['bodies']} bodies), nothing ran")
    report = {"ok": True, "model": f"{shown}::{model.name}",
              "memo": {"hits": 0, "misses": 0, "uncached": 0, "bodies_run": 0, "bodies_skipped": 0,
                       "bodies_loaded": found["bodies"]},
              "retention": {"keys": round(context.key_seconds, 4),
                            "fallbacks": {key: list(reasons) for key, reasons in context.fallbacks.items()
                                          if key == model.identity},
                            "reads": {}},
              "children": {}, "bodies": {model.identity: "loaded"}, "cache": {"used": True, "from": where},
              "timings": timings,
              # Each file's row as a build's has it, its kind too: what reads the report for the
              # document (an export, a snapshot) finds the taken xbf by it and reads that file.
              "files": {str(path): {"kind": kind, "written": False, "seconds": 0.0, "bytes": stats[str(path)][1]}
                        for kind, path in targets}}
    return report["cache"], report


def _names(run: Any, part: Any) -> tuple[Any, dict[str, dict[str, Any]], dict[tuple[str, str], str | None]]:
    """A part's element names, from the run's history (None for a part no feature named);
    per feature name they hold, its operation and the function, file and line of its
    call; and the functions those names come from, each with its skeleton now. A part
    read from a file is named by its own product's elements."""
    try:
        lineage, features = run.account(part.shape)
    except Exception:  # noqa: BLE001 - naming never fails a build
        return None, {}, {}
    if lineage is None:
        return None, {}, {}
    trace = run.trace
    provenance = {}
    refs: dict[tuple[str, str], str | None] = {}
    skeletons: dict[str, dict[str, str]] = {}
    for name, index in features.items():
        feature = trace.features[index]
        ref = (trace.path_of(feature.file), feature.function)
        provenance[name] = {"operation": feature.op, "function": feature.function, "file": ref[0],
                            "line": feature.line}
        if ref not in refs:
            refs[ref] = _skeleton(ref, skeletons)
        if feature.assigner is not None:
            # Named by its caller's assignment (a helper returned it): that function too.
            caller = (trace.path_of(feature.assigner[0]), feature.assigner[1])
            if caller not in refs:
                refs[caller] = _skeleton(caller, skeletons)
    return {"faces": lineage.get("faces") or [], "edges": lineage.get("edges") or [],
            "vertices": lineage.get("vertices") or [], "table": lineage.get("table") or {}}, provenance, refs


_KINDS = {"face": "faces", "edge": "edges", "vertex": "vertices"}


def made_by(path: str, key: str, kind: str, ordinal: int) -> dict[str, Any] | None:
    """What made element ``ordinal`` (1-based) of kind ``kind`` (face, edge, vertex) of
    part ``key`` of the xbf at ``path``, as this process's last build of that xbf named
    it: the element's name, the feature that made it (its last step's), that feature's
    operation and the function, file and line of its call, and, where the name folds
    part of its derivation, the derivation spelled out. None for a document this process
    did not build, or an element its build gave no name."""
    named = _BUILT.get(os.path.normcase(str(path)), {}).get(key)
    if named is None or not named.names:
        return None
    texts = named.names.get(_KINDS[kind]) or []
    name = texts[ordinal - 1] if 0 < ordinal <= len(texts) else None
    if name is None:
        return None
    feature = _last_feature(name)
    found = named.features.get(feature) or {}
    answer = {"name": name, "feature": feature, "operation": found.get("operation"),
              "function": found.get("function"), "file": found.get("file"), "line": found.get("line")}
    spelled = _spelled(name, named.names.get("table") or {})
    if spelled != name:
        answer["derivation"] = spelled
    return answer


def _spelled(text: str, table: dict[str, str]) -> str:
    """A name with every folded atom it holds spelled out, from its part's table."""
    from cadgen.engine.kernel.trace import folded_atoms

    for atom in folded_atoms(text):
        if atom in table:
            text = text.replace(atom, _spelled(table[atom], table))
    return text


def _skeleton(ref: tuple[str, str], skeletons: dict[str, dict[str, str]]) -> str | None:
    """The skeleton (``sources.skeleton``) of function ``ref[1]`` of file ``ref[0]`` now,
    each file read once a build (``skeletons``); None for a file or function not there."""
    from cadgen.engine.sources import skeleton

    path, function = ref
    found = skeletons.get(path)
    if found is None:
        try:
            found = skeletons[path] = skeleton(Path(path))
        except OSError:
            found = skeletons[path] = {}
    return found.get(function)


def _current(refs: dict[tuple[str, str], str | None], skeletons: dict[str, dict[str, str]]) -> bool:
    """Whether every function a part's names come from has the skeleton it had then."""
    return all(_skeleton(ref, skeletons) == digest for ref, digest in refs.items())


def _signature(tree: Any) -> tuple:
    return (tree.signature, tuple((part.shape.TShape(), part.name, part.color, part.key) for part in tree.parts))


def _stat(path: Path) -> tuple | None:
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _last_feature(name: str) -> str:
    from cadgen.engine.kernel.trace import _features

    text = name.split(":", 1)[1] if ":" in name[:2] else name
    found = _features(text)
    return found[-1] if found else ""
