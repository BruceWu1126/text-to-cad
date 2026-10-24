"""The registry: one self-describing record per document the engine knows, in the user's state folder.

``<state>/documents/<id>/document.json`` is a document's record: a model built from a script
(``kind: "model"``, ``store: "pointer"`` -- the script and the function it points at -- or
``"copy"`` -- its own sources and the files its build read, in the record's folder: a bundle), or
an on-disk CAD file the person opened (``kind: "file"``). The registry POINTS: no geometry, no
mesh, no xbf, no memo is ever written here. A ``memory`` document has no record. Ids are the
documents' own (``ids``: derived from the canonical path and the function, or given with
``@model(id=...)`` for a copy); the registry is their reverse map, never their authority.

A record is relocatable: ``root`` is the one absolute folder in it (``"."`` for a bundle: the
record's own folder), and ``home`` (the script, or the file), ``run`` (the command that builds it
again, ``["python", program]``) and every copied file are relative to it.

The interface is the ``Registry``'s: ``get``, ``put``, ``list``, ``delete`` (records in, records
out), ``update`` (a read and a write under one lock), and a bundle's files (``write_bundle``,
``add_input``, ``folder``). The filesystem implementation here is the only one; it is the seam a
hosted store would implement. Every byte written under the state folder by this module goes
through ``Folder``, and every write commits through ``atomic_replace.replace_atomic``. A record is
written synchronously, and only when a field but ``changed`` differs from the one on disk; then
each of ``OBSERVERS`` is told (a mirror uploads in the background: nothing waits on the network).

The engine imports nothing outside itself (law 3): where the state folder is is the
distribution's to say, through ``FACTORY`` (``cadgen._internal.engine_host``). With none, the
engine records nothing and copies nothing (``current`` is None).
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable

from cadgen.engine.atomic_replace import replace_atomic
from cadgen.engine.ids import canonical, is_id

__all__ = ["DOCUMENTS", "FACTORY", "Folder", "OBSERVERS", "RECORD", "Registry", "SCHEMA", "command", "current",
           "detached", "home", "program", "relative"]

SCHEMA = 1
DOCUMENTS = "documents"
RECORD = "document.json"
# A bundle's own sources, which ``write_bundle`` replaces; ``inputs/<digest>/<name>`` holds what its
# build read (``add_input``).
SOURCES = ("model.py", "modules")
INPUTS = "inputs"

# Told of every record written (one that changed), after it is on disk; never waited on for more.
OBSERVERS: list[Callable[[dict[str, Any]], None]] = []
# The distribution's: makes the registry of the user's state folder (``cadgen._internal.engine_host``).
FACTORY: Callable[[], "Registry"] | None = None

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_LOCK = threading.Lock()


def current() -> "Registry | None":
    """The registry of the user's state folder, as the distribution makes it; None with none."""
    return FACTORY() if FACTORY is not None else None


class Folder:
    """The user's state folder: the one owner of every byte written under it. Each write lands
    whole (beside its target, then ``replace_atomic``), so a reader never meets half a file."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def path(self, inside: str) -> Path:
        if Path(inside).is_absolute() or ".." in Path(inside).parts:
            raise ValueError(f"{inside} is not a path inside the state folder")
        return self.root / inside

    def write(self, inside: str, data: bytes) -> Path:
        """``data`` at ``inside``, landed whole."""
        return self._land(inside, lambda temporary: temporary.write_bytes(data))

    def copy_in(self, inside: str, source: Path | str) -> Path:
        """``source``'s bytes at ``inside``, landed whole."""
        return self._land(inside, lambda temporary: shutil.copyfile(source, temporary))

    def _land(self, inside: str, fill: Callable[[Path], Any]) -> Path:
        target = self.path(inside)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f"{target.name}.{os.getpid()}.{os.urandom(4).hex()}.tmp")
        try:
            fill(temporary)
            replace_atomic(temporary, target)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # on Windows the handle that blocked the rename blocks this too: the rename's error is the one
        return target

    def read(self, inside: str) -> bytes | None:
        try:
            return self.path(inside).read_bytes()
        except OSError:
            return None

    def remove(self, inside: str) -> None:
        """A file or a folder, whole; nothing when it is not there."""
        target = self.path(inside)
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target, ignore_errors=True)
            return
        try:
            target.unlink()
        except FileNotFoundError:
            pass

    def names(self, inside: str) -> list[str]:
        try:
            return sorted(os.listdir(self.path(inside)))
        except OSError:
            return []


class Registry:
    """The documents' records under ``<state>/documents`` (see the module)."""

    def __init__(self, state: Folder | Path | str) -> None:
        self.state = state if isinstance(state, Folder) else Folder(state)

    # --- records ----------------------------------------------------------------------------

    def get(self, ident: str) -> dict[str, Any] | None:
        """The record of document ``ident``, or None (none, or a file this schema does not read)."""
        if not is_id(ident):
            return None
        data = self.state.read(f"{DOCUMENTS}/{ident}/{RECORD}")
        if data is None:
            return None
        try:
            record = json.loads(data)
        except ValueError:
            return None
        if not isinstance(record, dict) or record.get("schema") != SCHEMA or record.get("id") != ident:
            return None
        return record

    def put(self, record: dict[str, Any]) -> bool:
        """Write ``record`` (its ``id`` names it); False, and nothing written, when every field but
        ``changed`` is what is on disk. ``created`` is kept from the record it replaces."""
        ident = record["id"]
        if not is_id(ident):
            raise ValueError(f"{ident!r} is not a document id")
        with self._lock(ident):
            old = self.get(ident)
            now = time.time()
            record = {**record, "schema": SCHEMA,
                      "created": (old or {}).get("created") or record.get("created") or now}
            if old is not None and _same(old, record):
                return False
            record["changed"] = now
            text = json.dumps(record, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
            self.state.write(f"{DOCUMENTS}/{ident}/{RECORD}", text.encode("utf-8"))
        for observer in list(OBSERVERS):
            try:
                observer(record)
            except Exception:  # noqa: BLE001 - an observer never fails the write it hears
                pass
        return True

    def update(self, ident: str, change: Callable[[dict[str, Any] | None], dict[str, Any] | None]
               ) -> dict[str, Any] | None:
        """``change(the record, or None)`` gives the record to write (None: nothing is), the read and
        the write under one lock; the record as it is then."""
        with self._lock(ident):
            found = change(self.get(ident))
            if found is not None:
                self.put(found)
            return self.get(ident)

    def list(self) -> list[dict[str, Any]]:
        """Every record the registry holds, in no order."""
        return [record for record in (self.get(name) for name in self.state.names(DOCUMENTS)) if record is not None]

    def delete(self, ident: str) -> bool:
        """Forget document ``ident``: its folder, whole (a copy's sources and inputs with it)."""
        if not is_id(ident):
            return False
        with self._lock(ident):
            there = self.state.path(f"{DOCUMENTS}/{ident}").exists()
            self.state.remove(f"{DOCUMENTS}/{ident}")
        return there

    # --- a document's folder: a copy's bundle ------------------------------------------------

    def folder(self, ident: str) -> Path:
        """Where document ``ident``'s record is on this machine: a copy's bundle."""
        if not is_id(ident):
            raise ValueError(f"{ident!r} is not a document id")
        return self.state.path(f"{DOCUMENTS}/{ident}")

    def write_bundle(self, ident: str, files: dict[str, bytes]) -> Path:
        """A copy's sources (``model.py``, ``modules/...``) in place of the ones it had. Its inputs
        stay until the registration after its build names the ones it read (``prune_inputs``)."""
        with self._lock(ident):
            for name in SOURCES:
                self.state.remove(f"{DOCUMENTS}/{ident}/{name}")
            for inside, data in files.items():
                if Path(inside).parts[0] not in SOURCES:
                    raise ValueError(f"{inside} is not a bundle's source")
                self.state.write(f"{DOCUMENTS}/{ident}/{inside}", data)
        return self.folder(ident)

    def add_input(self, ident: str, source: Path | str, digest: str) -> str:
        """``source`` copied into the bundle at ``inputs/<digest>/<its name>``, once (the digest is
        its content's); that path, relative to the bundle."""
        inside = f"{INPUTS}/{digest}/{Path(source).name}"
        if not self.state.path(f"{DOCUMENTS}/{ident}/{inside}").is_file():
            self.state.copy_in(f"{DOCUMENTS}/{ident}/{inside}", source)
        return inside

    def prune_inputs(self, ident: str, kept: set[str]) -> None:
        """Every input folder of the bundle not in ``kept`` (by digest) goes."""
        for digest in self.state.names(f"{DOCUMENTS}/{ident}/{INPUTS}"):
            if digest not in kept:
                self.state.remove(f"{DOCUMENTS}/{ident}/{INPUTS}/{digest}")

    def bundle_of(self, path: Path | str) -> str | None:
        """The id of the document whose folder holds ``path`` (a copy's ``model.py``, a module or an
        input of it), else None: a fact about the folder, never a detection."""
        documents = canonical(self.state.path(DOCUMENTS))
        found = canonical(path)
        if not found.startswith(documents + os.sep):
            return None
        ident = found[len(documents) + 1:].split(os.sep, 1)[0]
        return ident if is_id(ident) else None

    def root(self, record: dict[str, Any]) -> Path:
        """The folder a record's paths are relative to: its ``root``, or its own folder (``"."``)."""
        root = record.get("root") or "."
        return self.folder(record["id"]) if root == "." else Path(root)

    def _lock(self, ident: str) -> threading.RLock:
        with _LOCKS_LOCK:
            return _LOCKS.setdefault(f"{self.state.root}|{ident}", threading.RLock())


def _same(old: dict[str, Any], new: dict[str, Any]) -> bool:
    return ({key: value for key, value in old.items() if key != "changed"}
            == {key: value for key, value in new.items() if key != "changed"})


def relative(path: Path | str, root: Path | str) -> str:
    """``path`` as a record keeps it: relative to ``root``, with ``/``; absolute where it cannot be
    (another drive)."""
    try:
        return Path(os.path.relpath(os.fspath(path), os.fspath(root))).as_posix()
    except ValueError:
        return Path(path).as_posix()


def home(registry: Registry, record: dict[str, Any]) -> Path:
    """A record's script (a model's) or file (a file's), absolute."""
    return Path(os.path.normpath(registry.root(record) / record["home"]))


def program(registry: Registry, record: dict[str, Any]) -> Path | None:
    """The program whose run builds a model's document again, absolute; None for a file's."""
    run = record.get("run") or []
    return Path(os.path.normpath(registry.root(record) / run[-1])) if run else None


def command(registry: Registry, record: dict[str, Any]) -> str | None:
    """The command that builds a model's document again, as a person types it: ``python`` and the
    program, absolute. The engine never runs it."""
    found = program(registry, record)
    return None if found is None else f"python {shlex.quote(str(found))}"


def detached(registry: Registry, record: dict[str, Any]) -> bool:
    """Whether the script or the file a record points at is gone (moved, deleted)."""
    return not home(registry, record).is_file()
