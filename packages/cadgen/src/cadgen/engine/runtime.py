"""The Python environment a model asks the shared engine to execute in.

Executable paths stay lexical: resolving a venv's Python symlink would silently run its base
interpreter. Distribution records and editable sources are checked on every call, so a warm
process never conceals an installed dependency changed since its last run.
"""
from __future__ import annotations

import csv
import functools
import hashlib
import importlib.metadata as metadata
import importlib.util
import io
import json
import os
import platform
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import url2pathname

STARTUP = ("PYTHONPATH", "PYTHONHOME", "PYTHONSAFEPATH", "PYTHONNOUSERSITE", "PYTHONUSERBASE", "PYTHONPLATLIBDIR")
# Type stubs do not execute; explicit data reads are covered by the model's build closure.
_SOURCE = frozenset({".py", ".so", ".pyd", ".dll", ".dylib", ".pth"})
_SKIP = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", ".tox", "build", "dist"})
_current: dict[str, Any] | None = None
_records: dict[str, tuple[Any, str, str, list[str], Path | None, Path | None]] = {}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@functools.cache
def _windows_files() -> tuple[Any, Any, Any, Any, Any]:
    """Read-only Win32 bindings; Python's Windows ctime is creation time, not mutation time."""
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("CreationTime", ctypes.c_longlong), ("LastAccessTime", ctypes.c_longlong),
                    ("LastWriteTime", ctypes.c_longlong), ("ChangeTime", ctypes.c_longlong),
                    ("FileAttributes", wintypes.DWORD)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    opened = kernel.CreateFileW
    opened.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    opened.restype = wintypes.HANDLE
    query = kernel.GetFileInformationByHandleEx
    query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    query.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL

    def refused(path: Path | str) -> OSError:
        why = ctypes.WinError(ctypes.get_last_error())
        return OSError(f"cannot verify Windows dependency change time for {path}: {why}; "
                       "check file permissions and filesystem support")

    return opened, query, close, Basic, refused


def _windows_change(path: Path | str) -> int:
    """FILE_BASIC_INFO.ChangeTime survives a last-write timestamp restored with os.utime."""
    import ctypes

    opened, query, close, basic, refused = _windows_files()
    name = os.path.abspath(path)
    if not name.startswith("\\\\?\\"):
        name = "\\\\?\\UNC\\" + name[2:] if name.startswith("\\\\") else "\\\\?\\" + name
    # FILE_READ_ATTRIBUTES, all sharing modes, OPEN_EXISTING: no data read and no write access.
    handle = opened(name, 0x80, 7, None, 3, 0, None)
    if handle is None or handle == ctypes.c_void_p(-1).value:
        raise refused(path)
    try:
        info = basic()
        if not query(handle, 0, ctypes.byref(info), ctypes.sizeof(info)):  # FileBasicInfo
            raise refused(path)
        return int(info.ChangeTime) * 100  # FILETIME units are 100 ns; its epoch is immaterial here
    finally:
        if not close(handle):
            raise refused(path)


def _stamp(path: Path | str) -> list[Any]:
    path = os.fspath(path)
    try:
        found = os.stat(path)
    except OSError:
        return [path, None]
    changed = _windows_change(path) if os.name == "nt" else found.st_ctime_ns
    return [path, found.st_size, found.st_mtime_ns, changed]


def _editable(root: Path) -> list[list[Any]]:
    """Only source in an editable project's tree; never generated geometry or environments."""
    rows = []
    for folder, directories, names in os.walk(root):
        directories[:] = sorted(name for name in directories if name not in _SKIP and not name.startswith('.'))
        for name in sorted(names):
            if os.path.splitext(name)[1].lower() in _SOURCE:
                rows.append(_stamp(os.path.join(folder, name)))
    return rows


def dependencies() -> str:
    from cadgen.engine.previous import version

    # Editable import hooks and an explicit source root can discover different cadgen metadata
    # for the same imported code. Its actual source/release is authoritative in either layout.
    rows = [["cadgen", version(), sorted(_editable(Path(__file__).resolve().parent.parent))]]
    seen = set()
    records = []
    editables: dict[tuple[str, str], list[Any]] = {}
    for dist in metadata.distributions():
        # Reading/parsing every RECORD repeatedly costs more than checking its files. Cache that
        # listing while the metadata's stat stands; package and editable source stats stay live.
        location = getattr(dist, "_path", None)
        token = str(Path(location).resolve()) if location is not None else str(dist.locate_file("")) + dist.name
        if token in seen:
            continue
        seen.add(token)
        inventory = ("METADATA", "RECORD", "direct_url.json")
        if token.endswith(".egg-info"):
            inventory += ("PKG-INFO", "SOURCES.txt", "installed-files.txt")
        changed = [_stamp(os.path.join(token, name)) for name in inventory] if location is not None else None
        held = _records.get(token)
        if held is None or held[0] != changed:
            name = re.sub(r"[-_.]+", "-", str(dist.metadata.get("Name") or "").lower())
            base = Path(dist.locate_file("")).resolve()
            record = dist.read_text("RECORD")
            # metadata.files stats every wheel entry before we can filter it. Read the same
            # inventory directly; the stamps below check the runtime files once. Legacy/custom
            # distributions without RECORD keep importlib.metadata's own inventory semantics.
            entries = (row[0] for row in csv.reader(io.StringIO(record)) if row) if record else (
                str(entry) for entry in dist.files or ())
            files = [os.path.normpath(os.path.join(base, entry)) for entry in entries
                     if os.path.splitext(entry)[1].lower() in _SOURCE
                     or os.path.basename(entry) in ("METADATA", "RECORD", "direct_url.json")]
            try:
                direct = json.loads(dist.read_text("direct_url.json") or "{}")
            except (ValueError, TypeError):
                direct = {}
            editable = None
            if direct.get("dir_info", {}).get("editable") and urlparse(direct.get("url", "")).scheme == "file":
                editable = Path(url2pathname(urlparse(direct["url"]).path)).resolve()
                if name == "cadgen":
                    editable = Path(__file__).resolve().parent.parent
            source = Path(token) if (token.endswith(".egg-info") and dist.read_text("SOURCES.txt") is not None
                                    and dist.read_text("installed-files.txt") is None) else None
            held = _records[token] = changed, name, dist.version, files, editable, source
        records.append(held)
        if held[4] is not None and token.endswith(".dist-info"):
            editables.setdefault((held[1], held[2]), []).append(held)
    names = {"cadgen"}
    for held in records:
        _, name, version, files, editable, source = held
        if source is not None:
            # A plain script sees its source egg-info before the installed editable wheel;
            # a -P worker may see only the wheel. Use that same inventory for the equivalent
            # project, in the egg's original precedence slot. Other installs keep their order.
            for candidate in editables.get((name, version), ()):
                root = candidate[4]
                if source.is_relative_to(root) and not any(
                        part in _SKIP or part in {"site-packages", "dist-packages"}
                        for part in source.relative_to(root).parts[:-1]):
                    _, name, version, files, editable, _ = candidate
                    break
        if name in names:
            continue
        names.add(name)
        stamps = [found for path in files if len(found := _stamp(path)) > 2]
        if editable is not None:
            stamps.extend(_editable(editable))
        rows.append([name, version, sorted(stamps)])
    return digest(sorted(rows))


def compatibility() -> dict[str, Any]:
    """Native document interchange is independent of cadgen's Python release."""
    from cadgen.engine.previous import FORMAT

    ocp = None
    spec = importlib.util.find_spec("OCP")
    origin = Path(spec.origin).resolve() if spec is not None and spec.origin else None
    for provider in ("cadquery-ocp", "cadquery-ocp-novtk"):
        try:
            dist = metadata.distribution(provider)
            if origin == Path(dist.locate_file("OCP/__init__.py")).resolve():
                ocp = dist.version
                break
        except metadata.PackageNotFoundError:
            pass
    return {"format": FORMAT, "system": platform.system(), "arch": platform.machine(), "OCP": ocp}


def check_native(found: Any) -> None:
    """Refuse native bytes before reading them, including a kernel already loaded in this client."""
    if found != compatibility():
        raise ValueError("the Python client and document have incompatible native kernel/document formats")
    loaded = sys.modules.get("OCP")
    if loaded is not None:
        actual, installed = getattr(loaded, "__version__", None), found.get("OCP")
        if not isinstance(actual, str) or not isinstance(installed, str) or not (
                installed == actual or installed.startswith(actual + ".")):
            raise ValueError("the imported native kernel differs from the installed OCP distribution")



def describe(environment: dict[str, str] | None = None) -> dict[str, Any]:
    from cadgen.engine.previous import version

    env = os.environ if environment is None else environment
    path = env.get("PYTHONPATH")
    startup = {name: env.get(name, "") for name in STARTUP}
    if path is not None:
        startup["PYTHONPATH"] = os.pathsep.join(os.path.abspath(item or os.curdir) for item in path.split(os.pathsep))
    found = {"python": os.path.abspath(sys.executable), "prefix": os.path.abspath(sys.prefix),
             "root": str(Path(__file__).resolve().parents[2]), "release": version(),
             "checkout": (Path(__file__).resolve().parents[3] / "pyproject.toml").is_file(),
             "startup": startup, "dependencies": dependencies(), "compatibility": compatibility()}
    found["fingerprint"] = digest(found)
    return found


def validate(found: Any, *, server: bool = True) -> dict[str, Any]:
    from cadgen.engine.previous import version

    if not isinstance(found, dict) or set(found) != {
            "python", "prefix", "root", "release", "checkout", "startup", "dependencies", "compatibility", "fingerprint"}:
        raise ValueError("a build requires its Python execution environment")
    if any(not isinstance(found[name], str) or not os.path.isabs(found[name]) for name in ("python", "prefix", "root")):
        raise ValueError("the model's Python, prefix and cadgen import root must be absolute paths")
    if found["release"] != version():
        raise ValueError(f"model Python runs cadgen {found['release']}; server runs cadgen {version()}: install the same release")
    if found["fingerprint"] != digest({name: value for name, value in found.items() if name != "fingerprint"}):
        raise ValueError("the model's Python execution fingerprint is invalid")
    if server and found["compatibility"] != compatibility():
        raise ValueError("the model's Python and server have incompatible native kernel/document formats")
    return found


def family(found: dict[str, Any]) -> str:
    """One interpreter/import setup, independently of mutable installed dependencies."""
    return digest({name: found[name] for name in ("python", "prefix", "root")})


def key(script: str, execution: dict[str, Any] | None) -> str:
    return os.path.normcase(script) + ("::" + execution["fingerprint"] if execution is not None else "")


def activate(found: dict[str, Any]) -> None:
    global _current
    _current = found


def closure() -> dict[str, Any]:
    """The current build's environment in its closure, also for explicit offline builds."""
    found = _current or describe()
    return {"dependencies": found["dependencies"], "python": platform.python_version()}
