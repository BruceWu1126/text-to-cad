"""The sweep of cadgen 0.7's store: removed, in the background, when an engine daemon starts.

cadgen 0.7 kept a content-addressed store -- ``objects/`` and ``index/`` -- under one root:
``CADGEN_CACHE_DIR``, else ``$XDG_CACHE_HOME/cadgen`` (POSIX) or ``%LOCALAPPDATA%\\cadgen``
(Windows), else ``~/.cache/cadgen`` (macOS too). Nothing else lived under that root, and 0.8
reads and writes nothing there: the person's settings (language, telemetry answer and
installation id), the recents and their thumbnails are in the state directory
(``cadgen.viewer.recents.state_dir``), the engine's socket and lock in its own
(``cadgen.engine.daemon.state_dir``). So the sweep removes exactly those two folders.

Every start looks: one directory read of the root, nothing more when the root is absent or
holds neither folder. A folder is removed only when it is a real directory (never a link)
shaped as 0.7 left it -- ``objects/`` holds only two-hex-digit shards, ``index/`` only 0.7's
kinds -- and holds neither state directory. It is first renamed beside itself,
``<name>.removing-<pid>``, so a crash leaves no half-deleted store in place and a second engine
cannot claim the same folder; then the renamed folder is deleted on a thread, links unlinked
and never followed. What cannot go now -- a file Windows holds open -- stays in the renamed
folder, which the next start removes. Anything else under the root is left alone, and so is
the root.
"""
from __future__ import annotations

import os
import re
import stat
import sys
import threading
from pathlib import Path

__all__ = ["KINDS", "NAMES", "claim", "remove", "root", "start", "sweep"]

NAMES = ("objects", "index")
# Every kind an index/ of cadgen 0.5 to 0.7.26 held ("op" until 0.7.4).
KINDS = frozenset({"model", "document", "output", "component", "surface", "bounds", "mesh", "drawing", "op"})
_SHARD = re.compile(r"[0-9a-f]{2}")
_CLAIMED = re.compile(r"(?:objects|index)\.removing-[0-9]+")

# The last sweep's outcome in this process, for a test or a debugger.
LAST: dict = {}


def root() -> Path | None:
    """0.7's store root, by 0.7's rule (0.7.26's ``cadgen.store.paths.store_root``); ``None``
    for a relative ``CADGEN_CACHE_DIR``, which named a folder relative to a process long gone."""
    override = os.environ.get("CADGEN_CACHE_DIR", "").strip()
    if override:
        path = Path(override).expanduser()
        return path if path.is_absolute() else None
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "").strip()
        if local:
            return Path(local) / "cadgen"
    else:
        xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
        if xdg:
            return Path(xdg) / "cadgen"
    return Path.home() / ".cache" / "cadgen"


def _kept() -> list[Path]:
    """The folders 0.8 reads or writes: never inside what the sweep removes."""
    from cadgen.engine.daemon import state_dir as engine_dir
    from cadgen.viewer.recents import state_dir

    return [Path(os.path.abspath(path)) for path in (state_dir(), engine_dir())]


def _link(entry: os.DirEntry) -> bool:
    return entry.is_symlink() or bool(getattr(entry, "is_junction", lambda: False)())


def _shaped(path: Path, name: str) -> bool:
    """Whether a folder holds only what 0.7 put in it: its top level, read once."""
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.name.startswith("."):  # an interrupted atomic write
                    continue
                if _link(entry) or not entry.is_dir(follow_symlinks=False):
                    return False
                if not (_SHARD.fullmatch(entry.name) if name == "objects" else entry.name in KINDS):
                    return False
    except OSError:
        return False
    return True


def claim(base: Path, kept: list[Path] | None = None) -> list[Path]:
    """Rename 0.7's folders under ``base`` aside; every folder to delete: those, and those an
    earlier start renamed and did not finish."""
    try:
        with os.scandir(base) as entries:
            found = {entry.name: entry for entry in entries}
    except OSError:  # no root: nothing to do
        return []
    claimed = [base / name for name, entry in sorted(found.items())
               if _CLAIMED.fullmatch(name) and not _link(entry) and entry.is_dir(follow_symlinks=False)]
    candidates = [name for name in NAMES if name in found and not _link(found[name])
                  and found[name].is_dir(follow_symlinks=False)]
    if not candidates:
        return claimed
    kept = _kept() if kept is None else [Path(os.path.abspath(path)) for path in kept]
    for name in candidates:
        folder = Path(os.path.abspath(base / name))
        if any(path == folder or folder in path.parents for path in kept) or not _shaped(folder, name):
            continue
        target = base / f"{name}.removing-{os.getpid()}"
        try:
            os.rename(base / name, target)
        except OSError:  # gone already (another engine), or held open (Windows): the next start retries
            continue
        if target not in claimed:
            claimed.append(target)
    return claimed


def _unlink(path: str, junction: bool) -> None:
    if junction:
        os.rmdir(path)  # removes the junction, not what it points at
        return
    try:
        os.unlink(path)
    except PermissionError:
        if os.name != "nt":
            raise
        os.chmod(path, stat.S_IWRITE)  # a read-only file
        os.unlink(path)


def remove(folder: Path) -> tuple[int, int, int]:
    """Delete ``folder`` bottom-up without following a link: ``(files, bytes, failures)``."""
    files = size = failures = 0
    stack: list[tuple[str, bool]] = [(str(folder), False)]
    while stack:
        path, emptied = stack.pop()
        if emptied:
            try:
                os.rmdir(path)
            except FileNotFoundError:
                pass
            except OSError:
                failures += 1
            continue
        stack.append((path, True))
        try:
            with os.scandir(path) as listing:
                entries = list(listing)
        except FileNotFoundError:
            continue
        except OSError:
            failures += 1
            continue
        for entry in entries:
            try:
                junction = bool(getattr(entry, "is_junction", lambda: False)())
                if not junction and not entry.is_symlink() and entry.is_dir(follow_symlinks=False):
                    stack.append((entry.path, False))
                    continue
                length = entry.stat(follow_symlinks=False).st_size
                _unlink(entry.path, junction)
            except FileNotFoundError:
                continue
            except OSError:
                failures += 1
                continue
            files += 1
            size += length
    return files, size, failures


def _amount(size: float) -> str:
    for unit in ("bytes", "KB", "MB"):
        if size < 1000:
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1000
    return f"{size:.1f} GB"


def sweep(base: Path | None = None, kept: list[Path] | None = None) -> dict:
    """Remove 0.7's store under ``base`` (by default, 0.7's root): what was removed, and what kept."""
    base = root() if base is None else Path(base)
    outcome: dict = {"root": str(base) if base else None, "removed": [], "unfinished": [], "files": 0, "bytes": 0}
    if base is not None:
        for folder in claim(base, kept):
            files, size, failures = remove(folder)
            outcome["files"] += files
            outcome["bytes"] += size
            (outcome["unfinished"] if failures else outcome["removed"]).append(folder.name)
    if outcome["removed"] or outcome["unfinished"]:
        line = (f"cadgen: removed cadgen 0.7's store at {base}: {outcome['files']} files, "
                f"{_amount(outcome['bytes'])}")
        if outcome["unfinished"]:
            line += f"; {', '.join(outcome['unfinished'])} not empty yet, removed at the next start"
        try:
            print(line, file=sys.stderr, flush=True)
        except (OSError, ValueError):
            pass
    LAST.clear()
    LAST.update(outcome)
    return outcome


def _run() -> None:
    try:
        sweep()
    except Exception:  # noqa: BLE001 -- never the daemon's concern
        pass


def start() -> threading.Thread:
    """Sweep on a thread of its own: the daemon's start and first build never wait for it."""
    thread = threading.Thread(target=_run, name="cadgen-0.7-store-sweep", daemon=True)
    thread.start()
    return thread
