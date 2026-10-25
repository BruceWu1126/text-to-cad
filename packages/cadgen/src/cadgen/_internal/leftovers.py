"""What cadgen leaves behind that no cadgen running now needs, which ``cadgen doctor`` reports and
``cadgen doctor --prune`` removes. Nothing here removes anything by itself.

Three classes, each found by what it is, never by a guess:

- **Engine records of an older format**: ``<state>/engines/<installation>.json`` that the engine
  does not read (``registry.Registry.engine``: another ``schema``, or not a record), removed; one
  of this schema carrying more than it reads (an earlier build's ``token``) is written again with
  what it reads alone, so the port it rolled to is kept.
- **Bytecode folders of installations no longer registered**: ``<cache>/pycache/<folder>/``, each
  an engine's (``daemon.bytecode``, named by its ``daemon._tag``), that holds no registered
  installation's environment (``installations/<version>.json``'s ``prefix``, under which the
  bytecode of its libraries is filed) and is no running engine's (its lock held, or its door
  answering, in the engine folder).
- **Cache folders with no record**: ``<cache>/documents/<id>/`` whose document has no record
  (``<state>/documents/<id>/document.json``): a document the registry forgot.

Nothing written in the last ``QUIET_DAYS`` days is removed, of any class: a write that recent may
be a cadgen at work.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

QUIET_DAYS = 7
ENGINES = "engines"
PYCACHE = "pycache"
DOCUMENTS = "documents"
RECORD = "document.json"


@dataclass
class Found:
    """One class's leftovers: what may go (``paths``, ``bytes`` of them), and what stays because it
    was written in the last ``QUIET_DAYS`` days (``recent``)."""

    paths: list[Path] = field(default_factory=list)
    bytes: int = 0
    recent: list[Path] = field(default_factory=list)
    # Engine records only: those of this schema to write again without what the engine does not read.
    trimmed: list[Path] = field(default_factory=list)


def _quiet(newest: float | None, now: float) -> bool:
    return newest is None or now - newest >= QUIET_DAYS * 86400


def _walk(path: Path) -> tuple[int, float | None]:
    """The bytes under ``path`` and the newest write there (its own time and every file's and
    folder's in it), following no link."""
    try:
        top = os.lstat(path)
    except OSError:
        return 0, None
    if not os.path.isdir(path) or os.path.islink(path):
        return top.st_size, top.st_mtime
    size, newest = 0, top.st_mtime
    for folder, folders, files in os.walk(path):
        for name in folders + files:
            try:
                found = os.lstat(os.path.join(folder, name))
            except OSError:
                continue
            newest = max(newest, found.st_mtime)
            if name in files:
                size += found.st_size
    return size, newest


def _remove(path: Path) -> bool:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def remove(paths: list[Path]) -> int:
    """``paths`` removed, each whole: how many went."""
    return sum(_remove(path) for path in paths)


# --- engine records ------------------------------------------------------------------------------


def engine_records(state: Path, now: float | None = None) -> Found:
    """The engine records the engine does not read (``paths``), and those it reads that carry more
    (``trimmed``)."""
    from cadgen.engine.registry import ENGINE_FIELDS, ENGINE_SCHEMA

    now = time.time() if now is None else now
    found = Found()
    folder = state / ENGINES
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return found
    for name in names:
        path = folder / name
        if not (name.endswith(".json") and name[:-5].isalnum()) or path.is_symlink() or not path.is_file():
            continue
        try:
            record = json.loads(path.read_bytes())
        except (OSError, ValueError):
            record = None
        current = isinstance(record, dict) and record.get("schema") == ENGINE_SCHEMA
        if current and set(record) <= set(ENGINE_FIELDS):
            continue
        size, newest = _walk(path)
        if not _quiet(newest, now):
            found.recent.append(path)
        elif current:
            found.trimmed.append(path)
        else:
            found.paths.append(path)
            found.bytes += size
    return found


def remove_engine_records(state: Path, found: Found) -> tuple[int, int]:
    """``found``'s records removed, and those carrying more written again with what the engine reads
    (``registry.Registry.put_engine``, so the port each rolled to stays): how many of each."""
    from cadgen.engine.registry import ENGINE_SCHEMA, Folder, Registry

    removed = remove(found.paths)
    trimmed = 0
    held = Registry(Folder(state))
    for path in found.trimmed:
        try:
            record = json.loads(path.read_bytes())
            if isinstance(record, dict) and record.get("schema") == ENGINE_SCHEMA:
                held.put_engine(path.stem, record)
                trimmed += 1
        except (OSError, ValueError):
            continue
    return removed, trimmed


# --- bytecode folders ----------------------------------------------------------------------------


def _filed(folder: Path, prefix: str) -> bool:
    """Whether ``folder`` (a ``PYTHONPYCACHEPREFIX``) holds bytecode of a library under ``prefix``:
    Python files it there by its source's absolute path, the drive dropped."""
    for spelling in {os.path.abspath(prefix), os.path.realpath(prefix)}:
        rest = os.path.splitdrive(spelling)[1].lstrip("\\/")
        if rest and (folder / rest).is_dir():
            return True
    return False


def _running(name: str, engines: Path) -> bool:
    """Whether the engine whose files in the engine folder ``engines`` are named ``name`` runs: its
    lock held, or its door (on Windows, its port) answering."""
    from cadgen.engine import client, daemon

    lock = engines / f"{name}.lock"
    if lock.exists():
        try:
            held = daemon.lock(lock)
        except OSError:
            return True  # a folder this cannot lock in: not this to judge
        if held is None:
            return True
        held.close()
    address = engines / f"{name}.{'port' if daemon.WINDOWS else 'door'}"
    return address.exists() and client._answering(address)


def bytecode_folders(cache: Path, prefixes: list[str], engines: Path, now: float | None = None) -> Found:
    """The bytecode folders under ``<cache>/pycache`` that hold none of the registered installations'
    environments (``prefixes``) and are no running engine's (in the engine folder ``engines``)."""
    now = time.time() if now is None else now
    found = Found()
    root = cache / PYCACHE
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return found
    for name in names:
        folder = root / name
        if folder.is_symlink() or not folder.is_dir():
            continue
        if any(_filed(folder, prefix) for prefix in prefixes) or _running(name, engines):
            continue
        size, newest = _walk(folder)  # each start of its engine marks the folder (``daemon._pycache``)
        if not _quiet(newest, now):
            found.recent.append(folder)
            continue
        found.paths.append(folder)
        found.bytes += size
    return found


# --- cache folders with no record ----------------------------------------------------------------


def unrecorded(state: Path, cache: Path, now: float | None = None) -> Found:
    """The document folders under ``<cache>/documents`` whose document has no record in ``state``."""
    from cadgen.engine.ids import is_id

    now = time.time() if now is None else now
    found = Found()
    try:
        names = sorted(os.listdir(cache / DOCUMENTS))
    except OSError:
        return found
    for name in names:
        folder = cache / DOCUMENTS / name
        if not is_id(name) or folder.is_symlink() or not folder.is_dir():
            continue  # no document's: not the cache's to judge
        if os.path.lexists(state / DOCUMENTS / name / RECORD):
            continue
        size, newest = _walk(folder)
        if not _quiet(newest, now):
            found.recent.append(folder)
            continue
        found.paths.append(folder)
        found.bytes += size
    return found


# --- words ---------------------------------------------------------------------------------------


def size(count: int) -> str:
    """A size at a glance: ``8.6 KB``, ``412.0 MB``, ``1.2 GB``."""
    for unit, scale in (("GB", 2**30), ("MB", 2**20), ("KB", 2**10)):
        if count >= scale:
            return f"{count / scale:.1f} {unit}"
    return f"{count} B"


def count(number: int, one: str, many: str) -> str:
    return f"{number} {one if number == 1 else many}"
