"""A build: a script's models run, their results made documents, and the files they
declare written -- in this process, with the memo this process holds.

``build(script, functions)`` loads the script afresh (its own first-party modules
too: an edit anywhere in the model's code is read), runs each model named, or every
model the script defines, in one memo run, and then, outside the run, makes each
result a document (``document.collect``), names its parts' elements from the run's
history, writes the xbf when its content changed, writes the exports in order, and
writes the sidecar. A model whose body raises reports the error with the model's own
frames and leaves its files as they were.

What this process remembers between builds is in memory only: per part ``TShape``,
its element names and its geometry key; per output path, the document it last wrote.
A part a warm build hands back unchanged (the memo's hit is the same object) is
neither named nor keyed again, and a document whose every part, placement, name and
colour is what the file already holds is not rebuilt or rewritten.
"""

from __future__ import annotations

import importlib
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


@dataclass
class _Named:
    """What a build made of one part ``TShape``: its element names and the features they
    stand for, its geometry key, the shape the document holds of it; the functions its
    names come from, each with its skeleton then (``sources.skeleton``), and a number
    that changes whenever its names do."""

    names: Any
    key: str
    shape: Any
    refs: dict[tuple[str, str], str | None]
    token: int


# Per part TShape, what a build made of it (``_Named``), the most recently used last.
_PARTS: "OrderedDict[Any, _Named]" = OrderedDict()
_PARTS_HELD = 1 << 16
_TOKENS = __import__("itertools").count(1)
# Per model: the document this process last wrote, and each declared file as it left it.
_WRITTEN: dict[str, _Written] = {}


def _first_party_modules() -> list[str]:
    from cadgen.engine.kernel.trace import first_party_file

    return [name for name, module in list(sys.modules.items())
            if name != "__main__" and first_party_file(getattr(module, "__file__", None) or "")]


def load_script(script: Path) -> Any:
    """The script as a fresh module named by its stem, after every first-party module a
    previous build loaded is forgotten: the model's code as it is on disk now. No
    bytecode is written."""
    sys.dont_write_bytecode = True
    for name in _first_party_modules():
        sys.modules.pop(name, None)
    importlib.invalidate_caches()
    folder = str(script.parent)
    if folder in sys.path:
        sys.path.remove(folder)
    sys.path.insert(0, folder)
    name = script.stem
    spec = importlib.util.spec_from_file_location(name, script)
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


def build(script: Path, functions: list[str] | None = None, *, log: Callable[[str], None] = lambda _line: None,
          cwd: Path | None = None) -> list[dict[str, Any]]:
    """Build the script's models (``functions``, or every model it defines); one report
    per model. Raises BuildError for a script or function that is not there; a body that
    raises is its model's report (``ok`` false, ``error`` the model's traceback)."""
    from cadgen.engine.kernel import determinism, memo
    from cadgen.engine.model import BuildContext, models_in

    script = Path(script).resolve()
    if not script.is_file():
        raise BuildError(f"no script {script}")
    determinism.install()
    memo.install()
    shown = _shown(script, cwd)
    reports: list[dict[str, Any]] = []
    started = time.perf_counter()
    results: list[tuple[Any, Any, float]] = []
    with memo.run(script) as run, BuildContext() as context:
        try:
            module = load_script(script)
        except Exception as error:  # noqa: BLE001 - the model's own error, reported
            return [{"ok": False, "model": f"{shown}", "error": _model_traceback(error)}]
        loaded = time.perf_counter()
        context.loaded = set(_first_party_modules())
        models = models_in(vars(module), script)
        if functions:
            by_name = {model.name: model for model in models}
            missing = [name for name in functions if name not in by_name]
            if missing:
                raise BuildError(f"{shown} defines no model {', '.join(missing)}"
                                 + (f"; it defines {', '.join(by_name)}" if by_name else "; it defines none"))
            models = [by_name[name] for name in functions]
        elif not models:
            raise BuildError(f"{shown} defines no model")
        for model in models:
            body_started = time.perf_counter()
            tally = dict(run.tally)
            keys_before = context.key_seconds
            try:
                result = context.root(model)
            except Exception as error:  # noqa: BLE001 - the model's own error, reported
                reports.append({"ok": False, "model": f"{shown}::{model.name}", "error": _model_traceback(error)})
                results.append((model, None, 0.0))
                continue
            body = time.perf_counter() - body_started
            calls = {name: run.tally[name] - tally.get(name, 0) for name in run.tally}
            results.append((model, result, body))
            # What the bodies' keys cost (inside ``body``) and the modules each body's key
            # took by their bytes, with why.
            retention = {"keys": round(context.key_seconds - keys_before, 4),
                         "fallbacks": {name: reasons for name, reasons in context.fallbacks.items()
                                       if name == model.identity or name in context.timings}}
            reports.append({"ok": True, "model": f"{shown}::{model.name}", "memo": calls, "retention": retention,
                            "children": {name: round(seconds, 4) for name, seconds in context.timings.items()
                                         if name != model.identity}})
            context.timings.clear()
    load_seconds = loaded - started
    for report, (model, result, body) in zip(reports, results):
        if result is None:
            continue
        report["timings"] = {"load": round(load_seconds, 4), "body": round(body, 4)}
        try:
            _finish(model, result, run, report, log, shown)
        except Exception as error:  # noqa: BLE001 - a file that cannot be written is the build's error
            report["ok"] = False
            report["error"] = "".join(traceback.format_exception_only(type(error), error)).rstrip()
            report["trace"] = traceback.format_exc()
    return reports


def _shown(script: Path, cwd: Path | None) -> str:
    try:
        return os.path.relpath(script, cwd or Path.cwd())
    except ValueError:
        return str(script)


def _finish(model: Any, result: Any, run: Any, report: dict[str, Any], log: Callable[[str], None],
            shown: str) -> None:
    from cadgen.engine import document, exports

    timings = report["timings"]
    targets = model.targets()
    clock = time.perf_counter()
    tree = document.collect(result, model.name)
    timings["collect"] = round(time.perf_counter() - clock, 4)
    clock = time.perf_counter()
    named = 0
    skeletons: dict[str, dict[str, str]] = {}
    tokens = []
    for part in tree.parts:
        tshape = part.shape.TShape()
        cached = _PARTS.get(tshape)
        if cached is None:
            names, refs = _names(run, part)
            written_shape = document.direct(part.shape)
            cached = _PARTS[tshape] = _Named(names, document.geometry_key(written_shape), written_shape, refs,
                                              next(_TOKENS))
            named += 1
            while len(_PARTS) > _PARTS_HELD:
                _PARTS.popitem(last=False)
        else:
            _PARTS.move_to_end(tshape)
            if not _current(cached.refs, skeletons):
                # The same shape, but the code its names come from now names it otherwise
                # (a variable or a function renamed, an assignment added): named again.
                names, refs = _names(run, part)
                if names != cached.names:
                    cached.names, cached.token = names, next(_TOKENS)
                cached.refs = refs
                named += 1
        part.names, part.key, part.shape = cached.names, cached.key, cached.shape
        tokens.append(cached.token)
    signature = (_signature(tree), tuple(tokens))
    timings["names"] = round(time.perf_counter() - clock, 4)
    report["parts"] = len(tree.parts)
    report["named"] = named
    xbf_path = next((path for kind, path in targets if kind == "xbf"), None)
    key = f"{model.script}::{model.name}"
    remembered = _WRITTEN.get(key)
    stats = {str(path): _stat(path) for _kind, path in targets}
    written: dict[str, Any] = {}
    if remembered is not None and remembered.signature == signature and remembered.stats == stats:
        for _kind, path in targets:
            written[str(path)] = {"written": False, "seconds": 0.0, "bytes": stats[str(path)][1]}
    else:
        clock = time.perf_counter()
        doc = document.to_xcaf(tree)
        timings["document"] = round(time.perf_counter() - clock, 4)
        for kind, path in targets:
            clock = time.perf_counter()
            if kind == "xbf":
                # A fresh process compares what it made with the file: the same bytes
                # are not written again.
                fresh, size = document.save(doc, path, compare=remembered is None)
            else:
                fresh, size = True, exports.write(kind, doc, tree, path)
            seconds = time.perf_counter() - clock
            timings[kind] = round(seconds, 4)
            written[str(path)] = {"written": fresh, "seconds": round(seconds, 4), "bytes": size}
            log(f"{'wrote' if fresh else 'kept'} {os.path.relpath(path)} ({size / 1e6:.1f} MB) in {seconds:.2f}s")
        _WRITTEN[key] = _Written(signature, {str(path): _stat(path) for _kind, path in targets})
    report["files"] = written
    timings["total"] = round(sum(value for name, value in timings.items() if name != "total"), 4)
    # The sidecar describes the build that wrote the xbf: one that kept it keeps the sidecar.
    if xbf_path is not None and (written[str(xbf_path)]["written"]
                                 or not xbf_path.with_name(xbf_path.name + ".json").is_file()):
        _sidecar(xbf_path, model, report, tree, shown)


def _names(run: Any, part: Any) -> tuple[Any, dict[tuple[str, str], str | None]]:
    """A part's element names and their provenance, from the run's history (None for a
    part no feature named), and the functions those names come from, each with its
    skeleton now. A part read from a file is named by its own product's elements."""
    try:
        lineage, features = run.account(part.shape)
    except Exception:  # noqa: BLE001 - naming never fails a build
        return None, {}
    if lineage is None:
        return None, {}
    trace = run.trace
    provenance = {}
    refs: dict[tuple[str, str], str | None] = {}
    skeletons: dict[str, dict[str, str]] = {}
    for name, index in features.items():
        feature = trace.features[index]
        provenance[name] = [feature.op, f"{feature.file}:{feature.line}"]
        ref = (trace.path_of(feature.file), feature.function)
        if ref not in refs:
            refs[ref] = _skeleton(ref, skeletons)
    return {"faces": lineage.get("faces") or [], "edges": lineage.get("edges") or [],
            "table": lineage.get("table") or {}, "features": provenance}, refs


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


def _sidecar(xbf_path: Path, model: Any, report: dict[str, Any], tree: Any, shown: str) -> None:
    """``<name>.xbf.json``: the model, the build's timings and calls, the files, and per
    part the provenance of each element name -- the feature that made it, and that
    feature's operation and ``file:line``."""
    parts = []
    for part in tree.parts:
        names = part.names or {}
        elements = {}
        for kind in ("faces", "edges"):
            for text in names.get(kind) or []:
                if text is not None:
                    elements[text] = _last_feature(text)
        parts.append({"name": part.name, "key": part.key, "features": names.get("features") or {},
                      "elements": elements, "table": names.get("table") or {}})
    payload = {
        "model": f"{shown}::{model.name}",
        "engine": 1,
        "timings": report.get("timings", {}),
        "memo": report.get("memo", {}),
        "files": {os.path.basename(path): row for path, row in report.get("files", {}).items()},
        "parts": parts,
    }
    target = xbf_path.with_name(xbf_path.name + ".json")
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"))
    os.replace(temporary, target)
