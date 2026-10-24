"""Copy mode: a model whose source is in no saved file (a notebook cell), kept as a bundle.

A copied document's registry folder (``registry.Registry.folder``) is self-contained, and exactly
what a hosted engine would be handed:

- ``model.py``: one file ``python model.py`` runs alone, from the bundle's folder -- cadgen, the
  imports of every module the model's code reaches, the source of every function and class of the
  cell's own it reaches (``inspect.getsource``), every literal value it reaches as an assignment,
  and the model function under ``@cadgen.model(id="<id>")``, its declared files at absolute paths;
- ``modules/<path below its search-path folder>``: every first-party module the code reaches, and
  each first-party module those import (``sources.closure_files``);
- ``inputs/<digest>/<name>``: every file the build read -- data, CAD files through the engine's
  readers, files read by motion functions, fonts -- content-addressed, copied after the build
  from the digests its closure holds (``copy_inputs``).

The first build already runs from the copy, in a worker (``build.load_script``), never in the
caller, so what is copied is what is proven to build. A value the code reaches that is none of the
above is left out, and the build fails with Python's own ``NameError``: a cell must be
self-contained, or saved as a file. A rerun of ``model.py`` reads each copied input in place of
its original (``serving``: ``open`` and the engine's readers); a read through a C extension of an
original's absolute path is not redirected, and the digest check after the build fails it loudly
when that file is no longer what was copied (``copy_inputs``).

Nothing here imports the kernel.
"""
from __future__ import annotations

import ast
import builtins
import contextlib
import hashlib
import inspect
import io
import os
import sys
import textwrap
import types
from pathlib import Path
from typing import Any, Iterator

from cadgen.engine.ids import canonical

__all__ = ["CopyFailed", "bundle_sources", "copy_inputs", "serving"]

# What a copy reconstructs as an assignment: a literal, or a container of literals.
_LITERALS = (int, float, complex, str, bytes, bool, type(None))


class CopyFailed(ValueError):
    """A copy that cannot be made, or whose inputs moved while they were copied."""


# --- the sources, made in the caller ---------------------------------------------------------


def _literal(value: Any, depth: int = 0) -> bool:
    if depth > 20:
        return False
    if isinstance(value, _LITERALS):
        return True
    if type(value) in (tuple, list, set, frozenset):
        return all(_literal(item, depth + 1) for item in value)
    if type(value) is dict:
        return all(_literal(key, depth + 1) and _literal(item, depth + 1) for key, item in value.items())
    return False


def _names(text: str) -> list[str]:
    """Every name a source loads, in order: what it may reach in its namespace."""
    found: list[str] = []
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Name) and node.id not in found:
            found.append(node.id)
    return found


def _importable(name: str) -> bool:
    """Whether module ``name`` is one ``import name`` finds again: loaded, from a file, not ``__main__``."""
    module = sys.modules.get(name)
    file = getattr(module, "__file__", None)
    return name != "__main__" and module is not None and isinstance(file, str) and os.path.isfile(file)


def _first_party(module: types.ModuleType) -> bool:
    from cadgen.engine.kernel.trace import first_party_file

    return first_party_file(getattr(module, "__file__", None) or "")


def _placed(file: Path) -> str | None:
    """A first-party module's file, below the search-path folder it is imported from (``a/b.py``)."""
    for entry in sys.path:
        folder = Path(entry or os.getcwd()).resolve()
        try:
            inside = file.resolve().relative_to(folder)
        except ValueError:
            continue
        if inside.parts and ".." not in inside.parts:
            return inside.as_posix()
    return None


def _model_body(function: Any) -> str:
    """The model function's source without its decorators: the copy writes its own."""
    text = textwrap.dedent(inspect.getsource(function))
    tree = ast.parse(text)
    node = tree.body[0] if tree.body else None
    if not isinstance(node, ast.FunctionDef) or node.name != function.__name__:
        raise CopyFailed(f"the source of model {function.__name__!r} is not its def: save this as a file")
    return "\n".join(text.splitlines()[node.lineno - 1:]) + "\n"


def bundle_sources(model: Any, ident: str, cwd: Path) -> dict[str, bytes]:
    """The bundle's sources for ``model`` (a ``Model`` whose store is ``copy``), under id ``ident``:
    ``model.py`` and ``modules/...`` (see the module), as bytes by their path in the bundle. Its
    declared files are written where the original declares them: a relative path against the
    folder of the model's file where that is a file, else against ``cwd``."""
    from cadgen.engine.model import Memo, Model

    function = model.function
    namespace = function.__globals__
    home = function.__module__
    local = not _importable(home)  # a cell's namespace: its functions are copied as source
    imports: list[str] = []
    values: list[str] = []
    definitions: list[str] = []
    reached: list[types.ModuleType] = []
    done: set[str] = set()

    def reach(text: str) -> None:
        for name in _names(text):
            if name in done or name not in namespace:
                continue
            done.add(name)
            value = namespace[name]
            target = value.function if isinstance(value, (Memo, Model)) else value
            if isinstance(value, types.ModuleType):
                imports.append(f"import {value.__name__} as {name}" if value.__name__ != name else f"import {name}")
                if _first_party(value):
                    reached.append(value)
            elif (inspect.isfunction(target) or inspect.isclass(target)) and target.__module__ == home and local:
                try:
                    source = textwrap.dedent(inspect.getsource(target))
                except (OSError, TypeError):
                    continue  # no source to copy: the build's NameError says so
                definitions.append(source)
                reach(source)
            elif inspect.isfunction(target) or inspect.isclass(target) or isinstance(value, (Memo, Model)):
                module = target.__module__
                if _importable(module) and "<" not in target.__qualname__ and "." not in target.__qualname__:
                    imports.append(f"from {module} import {target.__qualname__} as {name}")
                    if _first_party(sys.modules[module]):
                        reached.append(sys.modules[module])
            elif _literal(value):
                values.append(f"{name} = {value!r}")

    body = _model_body(function)
    reach(body)
    files: dict[str, bytes] = {}
    from cadgen.engine.sources import closure_files

    for module in reached:
        name = module.__name__
        # Its packages' ``__init__`` too, from the top: ``import lib.geom`` runs ``lib/__init__.py`` first.
        parents = [sys.modules.get(".".join(name.split(".")[:depth])) for depth in range(1, name.count(".") + 1)]
        owned = [Path(parent.__file__) for parent in parents if parent is not None and _first_party(parent)]
        for file in [*owned, *closure_files(Path(module.__file__), name)]:
            placed = _placed(file)
            if placed is not None:
                files[f"modules/{placed}"] = file.read_bytes()
    origin = Path(function.__code__.co_filename)
    folder = origin.parent if origin.is_file() else cwd
    decorators = []
    for output in model.outputs:
        path = Path(output.out)
        decorators.append(f"@_cadgen.{output.kind}({os.path.normpath(path if path.is_absolute() else folder / path)!r})")
    decorators.append(f"@_cadgen.model(id={ident!r})")
    text = [f'"""Model {function.__name__!r}, copied by cadgen from {function.__code__.co_filename}: '
            f'`python model.py` builds it again, as document {ident}."""',
            "import cadgen as _cadgen"]
    if any(name.startswith("modules/") for name in files):
        text += ["import pathlib as _cadgen_pathlib", "import sys as _cadgen_sys",
                 "_cadgen_modules = str(_cadgen_pathlib.Path(__file__).resolve().parent / 'modules')",
                 "if _cadgen_modules not in _cadgen_sys.path:",
                 "    _cadgen_sys.path.insert(0, _cadgen_modules)"]
    text += [*imports, *values, ""]
    for source in reversed(definitions):  # what a definition reaches is defined before it
        text += ["", source.rstrip(), ""]
    text += ["", *decorators, body.rstrip(), "", "", 'if __name__ == "__main__":', f"    {function.__name__}()", ""]
    files["model.py"] = "\n".join(text).encode("utf-8")
    return files


# --- the inputs, copied after the build --------------------------------------------------------


def _digest(kind: str, path: str) -> str:
    """A file's digest as the closure spells it: ``read`` blake2b's (a body's own read), else sha256's."""
    hasher = hashlib.blake2b(digest_size=32) if kind == "read" else hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 22), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _rows(closure: dict[str, Any]) -> Iterator[tuple[str, str, str]]:
    """Each input of a closure (``build._closure``): its kind, its absolute path, its digest."""
    for row in closure.get("inputs") or []:
        kind, path, digest = row[0], (row[1] if row[0] in ("file", "read") else row[2]), row[-1]
        if path is None or digest is None:
            continue  # a file it found missing, or a font with no file: nothing to copy
        if kind == "font":
            digest = str(digest).rpartition(":")[2]
        yield kind, path, digest


def copy_inputs(registry: Any, ident: str, closure: dict[str, Any] | None, copied: dict[str, str]
                ) -> dict[str, str]:
    """Every file a copied document's build read, copied into its bundle (``inputs/<digest>/<name>``),
    each checked against the digest its closure holds: the ``copied`` map (original -> its copy, in
    the bundle) as it is now. A file that moved since the build read it, and an original read in
    place of its copy (a C extension's read of the absolute path) that is no longer what was
    copied, fail loudly (``CopyFailed``)."""
    found: dict[str, str] = {}
    folder = Path(registry.folder(ident))
    # Every path is keyed by its canonical spelling (``ids.canonical``), the engine's one.
    copies = {canonical(folder / inside): original for original, inside in copied.items()}
    for kind, path, digest in _rows(closure or {}):
        path = canonical(path)
        if path in copies:  # read through its copy (``serving``): the copy is the input
            found[copies[path]] = copied[copies[path]]
            continue
        if registry.bundle_of(path) == ident:
            continue  # a module or a source of the bundle's own
        if path in copied and Path(copied[path]).parts[1] != digest:
            raise CopyFailed(f"{path} was read in place of its copy, and it is no longer what was copied: "
                             "a read through a C extension is not redirected (copy mode reads files through "
                             "open() and the engine's readers)")
        try:
            now = _digest(kind, path)
        except OSError as error:
            raise CopyFailed(f"{path}, which the build read, cannot be copied: {error}") from None
        if now != digest:
            raise CopyFailed(f"{path} changed after the build read it: build again to copy what it reads")
        found[path] = registry.add_input(ident, path, digest)
    return found


# --- a copy's rerun, in the worker -------------------------------------------------------------

_COPIED: dict[str, str] = {}
_ORIGIN: list[str | None] = [None]


def copied_path(path: Any) -> Any:
    """``path``, or the copy a copied document's rerun reads in its place (``serving``)."""
    if not _COPIED or isinstance(path, int):
        return path
    try:
        text = os.fspath(path)
    except TypeError:
        return path
    if isinstance(text, bytes):
        return path
    origin = _ORIGIN[0]
    found = canonical(text if os.path.isabs(text) else os.path.join(origin or os.getcwd(), text))
    return _COPIED.get(found, path)


@contextlib.contextmanager
def serving(copied: dict[str, str] | None, origin: str | None) -> Iterator[None]:
    """A rerun of a copied document's ``model.py``: each original it read (``copied``: original ->
    its copy, absolute) is read from its copy -- through ``open`` and the engine's readers, a relative
    path resolved against the folder the first build ran in (``origin``). Nothing outside it."""
    if not copied:
        yield
        return
    real_open, real_io_open = builtins.open, io.open

    def opened(file: Any, *args: Any, **kwargs: Any) -> Any:
        return real_open(copied_path(file), *args, **kwargs)

    _COPIED.update({canonical(original): copy for original, copy in copied.items()})
    _ORIGIN[0] = origin
    builtins.open = io.open = opened
    try:
        yield
    finally:
        builtins.open, io.open = real_open, real_io_open
        _COPIED.clear()
        _ORIGIN[0] = None
