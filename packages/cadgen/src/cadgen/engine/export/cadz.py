"""A ``.cadz``: an xbf delivered with what builds it -- the xbf, its build closure's source
files (the script and every module the closure lists) and, unless left out, its inputs --
zipped at their places relative to one another.

Self-contained delivery is an export, never a property of the document: the xbf carries
its closure as pointers and digests (``previous``), and this follows the pointers. Every
file is stored under its path relative to the folder that holds them all, so extracting
the archive and building the script there writes the xbf where the archive has it; a
drift-free model rebuilds its bytes. The archive itself is a function of those files:
sorted entries, one fixed date, no extra attributes.
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path
from typing import Any

from cadgen.engine.atomic_replace import replace_atomic
from cadgen.engine.export import ZIP_DATE

__all__ = ["SUFFIX", "files", "write"]

SUFFIX = ".cadz"


def _shown(path: Any, folder: Path) -> str:
    """``path`` relative to ``folder``, or whole on another drive."""
    try:
        return os.path.relpath(path, folder)
    except ValueError:
        return str(path)


def files(xbf: Path, *, inputs: bool = True) -> list[Path]:
    """The files a ``.cadz`` of ``xbf`` holds: the xbf, the closure's script and modules,
    and its inputs (a font found by name stays the system's). Refused by name where the
    xbf holds no closure or a file it names is gone."""
    from cadgen.engine import previous
    from cadgen.engine.export import ExportRefused

    xbf = Path(xbf).resolve()
    record = previous.of(xbf)
    if record is None:
        raise ExportRefused(f"{xbf.name} holds no build closure: a .cadz is made from an xbf this engine built")
    if record["source"].get("kind") != "script":
        raise ExportRefused(f"{xbf.name} was built from code text, which no file holds: it has no sources to package")
    folder = xbf.parent
    found = [xbf, Path(previous.absolute(record["source"]["path"], folder))]
    found += [Path(previous.absolute(path, folder)) for path, _digest in record["modules"]]
    if inputs:
        for row in record["inputs"] or []:
            path = row[1] if row[0] in ("file", "read") else row[2]
            if path is not None and (row[0] != "read" or row[-1] is not None):
                found.append(Path(previous.absolute(path, folder)))
    unique = list(dict.fromkeys(found))
    missing = [path for path in unique if not path.is_file()]
    if missing:
        raise ExportRefused(f"cannot package {xbf.name}: {_shown(missing[0], folder)} is missing")
    return unique


def write(xbf: Path, out: Path, *, inputs: bool = True) -> int:
    """Write the ``.cadz`` of ``xbf`` at ``out`` (beside it, then moved over it); its size."""
    found = files(xbf, inputs=inputs)
    root = Path(os.path.commonpath([str(path.parent) for path in found]))
    out = Path(out).resolve()
    temporary = out.with_name(f".{out.name}.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(found, key=lambda item: item.relative_to(root).as_posix()):
                entry = zipfile.ZipInfo(path.relative_to(root).as_posix(), ZIP_DATE)
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.external_attr = 0o644 << 16
                archive.writestr(entry, path.read_bytes())
        replace_atomic(temporary, out)
    finally:
        temporary.unlink(missing_ok=True)
    return out.stat().st_size
