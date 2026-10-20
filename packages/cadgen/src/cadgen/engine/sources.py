"""What a model body is made of: the bytes of its module and of the first-party modules
that module imports, transitively.

``closure(path, module)`` is a digest of every file a body's code can come from: the file
its function is defined in and, followed through their import statements -- every
``import`` and ``from ... import`` anywhere in a file, a function's own included, so a
body that is not run still counts what it would import -- each first-party module and
package they name, as the module search path finds it. A module is first-party as the
trace counts a frame (not the standard library, not an installed package, not cadgen).
Files are read by their bytes, hashed once per version of the file (its stat), and their
imports parsed once per digest: all in this process's memory.
"""

from __future__ import annotations

import ast
import hashlib
import os
import sys
from collections import OrderedDict
from pathlib import Path

__all__ = ["closure", "digest"]

_HELD = 4096
# (device, inode, size, mtime) -> the file's digest; digest -> the imports its source makes.
_DIGESTS: "OrderedDict[tuple, str]" = OrderedDict()
_IMPORTS: "OrderedDict[str, tuple]" = OrderedDict()


def digest(path: Path) -> str:
    """A file's bytes, hashed once per version of the file."""
    stat = os.stat(path)
    version = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    found = _DIGESTS.get(version)
    if found is None:
        found = _DIGESTS[version] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        while len(_DIGESTS) > _HELD:
            _DIGESTS.popitem(last=False)
    return found


def _imports(path: Path, file_digest: str) -> tuple:
    """Each import statement of a file: ``(level, module, names)``."""
    found = _IMPORTS.get(file_digest)
    if found is None:
        rows = []
        try:
            tree = ast.parse(Path(path).read_bytes(), filename=str(path))
        except SyntaxError:
            tree = ast.Module(body=[], type_ignores=[])
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                rows.extend((0, alias.name, ()) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                rows.append((node.level, node.module or "", tuple(alias.name for alias in node.names)))
        found = _IMPORTS[file_digest] = tuple(rows)
        while len(_IMPORTS) > _HELD:
            _IMPORTS.popitem(last=False)
    return found


def _first_party(path: Path) -> bool:
    from cadgen.engine.kernel.trace import first_party_file

    return first_party_file(str(path))


def _files(name: str) -> list[tuple[str, Path]]:
    """The first-party files importing ``name`` runs, each with its module name: every
    package ``__init__`` on the way, then the module -- as the first search-path entry
    holding its top level finds them. Nothing for a module that is not first-party."""
    parts = name.split(".")
    for entry in sys.path:
        root = Path(entry or os.getcwd())
        top = root / parts[0]
        if not (top.with_suffix(".py").is_file() or (top / "__init__.py").is_file()):
            continue
        found = []
        folder = root
        for depth, part in enumerate(parts, 1):
            package = folder / part / "__init__.py"
            module = (folder / part).with_suffix(".py")
            dotted = ".".join(parts[:depth])
            if package.is_file():
                found.append((dotted, package))
                folder = folder / part
            elif module.is_file():
                found.append((dotted, module))
                break
            else:
                break
        return [(dotted, path.resolve()) for dotted, path in found if _first_party(path)]
    return []


def closure(path: Path, module: str, resolved: dict[str, list] | None = None) -> str:
    """The digest of a body's code: the file it is defined in (as module ``module``) and
    every first-party module it imports, transitively, by path and bytes. ``resolved``
    keeps what each module name resolved to, for the bodies of one build to share."""
    resolved = {} if resolved is None else resolved
    seen: dict[Path, str] = {}
    pending = [(module, Path(path).resolve())]
    while pending:
        name, file = pending.pop()
        if file in seen:
            continue
        seen[file] = file_digest = digest(file)
        package = name if file.name == "__init__.py" else name.rpartition(".")[0]
        for level, target, names in _imports(file, file_digest):
            if level:
                base = package.split(".") if package else []
                base = base[:len(base) - (level - 1)] if level > 1 else base
                target = ".".join([*base, target] if target else base)
            if not target:
                continue
            candidates = [target, *(f"{target}.{item}" for item in names if item != "*")]
            for candidate in candidates:
                found = resolved.get(candidate)
                if found is None:
                    found = resolved[candidate] = _files(candidate)
                pending.extend(found)
    hasher = hashlib.sha256()
    for file in sorted(seen):
        hasher.update(f"{file}\0{seen[file]}\n".encode())
    return hasher.hexdigest()
