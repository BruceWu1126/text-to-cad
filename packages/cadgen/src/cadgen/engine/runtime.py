"""The Python environment a model asks the shared engine to execute in.

Executable paths stay lexical: resolving a venv's Python symlink would silently run its base
interpreter. Distribution records and editable sources are checked on every call, so a warm
process never conceals an installed dependency changed since its last run.
"""
from __future__ import annotations

import functools
import hashlib
import importlib.metadata as metadata
import importlib.util
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import url2pathname

STARTUP = ("PYTHONPATH", "PYTHONHOME", "PYTHONSAFEPATH", "PYTHONNOUSERSITE", "PYTHONUSERBASE", "PYTHONPLATLIBDIR")
_SOURCE = frozenset({".py", ".pyi", ".so", ".pyd", ".dll", ".dylib", ".pth"})
_SKIP = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", ".tox", "build", "dist"})
_current: dict[str, Any] | None = None
_records: dict[str, tuple[Any, str, str, list[Path], Path | None]] = {}


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

    def refused(path: Path) -> OSError:
        why = ctypes.WinError(ctypes.get_last_error())
        return OSError(f"cannot verify Windows dependency change time for {path}: {why}; "
                       "check file permissions and filesystem support")

    return opened, query, close, Basic, refused


def _windows_change(path: Path) -> int:
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


def _stamp(path: Path) -> list[Any]:
    try:
        found = path.stat()
    except OSError:
        return [str(path), None]
    changed = _windows_change(path) if os.name == "nt" else found.st_ctime_ns
    return [str(path), found.st_size, found.st_mtime_ns, changed]


def _editable(root: Path) -> list[list[Any]]:
    """Only source in an editable project's tree; never generated geometry or environments."""
    rows = []
    for folder, directories, names in os.walk(root):
        directories[:] = sorted(name for name in directories if name not in _SKIP and not name.startswith('.'))
        for name in sorted(names):
            if Path(name).suffix.lower() in _SOURCE:
                rows.append(_stamp(Path(folder) / name))
    return rows


def dependencies() -> str:
    from cadgen.engine.previous import version

    # Editable import hooks and an explicit source root can discover different cadgen metadata
    # for the same imported code. Its actual source/release is authoritative in either layout.
    rows = [["cadgen", version(), sorted(_editable(Path(__file__).resolve().parent.parent))]]
    seen = set()
    names = {"cadgen"}
    for dist in metadata.distributions():
        # Reading/parsing every RECORD repeatedly costs more than checking its files. Cache that
        # listing while the metadata's stat stands; package and editable source stats stay live.
        location = getattr(dist, "_path", None)
        token = str(Path(location).resolve()) if location is not None else str(dist.locate_file("")) + dist.name
        if token in seen:
            continue
        seen.add(token)
        changed = [_stamp(Path(token) / name) for name in ("METADATA", "RECORD", "direct_url.json")] if location is not None else None
        held = _records.get(token)
        if held is None or held[0] != changed:
            name = str(dist.metadata.get("Name") or "").lower().replace("_", "-")
            base = Path(dist.locate_file("")).resolve()
            files = [Path(os.path.normpath(os.path.join(base, str(entry)))) for entry in dist.files or ()
                     if Path(entry).suffix.lower() in _SOURCE or Path(entry).name in ("METADATA", "RECORD", "direct_url.json")]
            try:
                direct = json.loads(dist.read_text("direct_url.json") or "{}")
            except (ValueError, TypeError):
                direct = {}
            editable = None
            if direct.get("dir_info", {}).get("editable") and urlparse(direct.get("url", "")).scheme == "file":
                editable = Path(url2pathname(urlparse(direct["url"]).path)).resolve()
                if name == "cadgen":
                    editable = Path(__file__).resolve().parent.parent
            held = _records[token] = changed, name, dist.version, files, editable
        _, name, version, files, editable = held
        if name in names:
            continue
        names.add(name)
        stamps = [_stamp(path) for path in files]
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


NATIVE_SCHEMA = "cadgen.engine native/1"
NATIVE_GUID = "457ceaca-c5ba-4c7b-a7ae-9d5b735bf385"
READABLE_FORMATS = frozenset({"xbf/7", "xbf/8", "xbf/9"})


class IncompatibleNative(ValueError):
    """A last-built document that this native toolchain cannot safely decode."""


def stamp_native(doc: Any) -> None:
    """Shape hand-overs have no model closure: keep their producer in the raw-readable head."""
    from OCP.Standard import Standard_GUID
    from OCP.TCollection import TCollection_AsciiString
    from OCP.TDataStd import TDataStd_AsciiString

    text = json.dumps({"schema": NATIVE_SCHEMA, "compatibility": compatibility()}, separators=(",", ":"))
    TDataStd_AsciiString.Set_s(doc.Main(), Standard_GUID(NATIVE_GUID), TCollection_AsciiString(text))


def check_producer(data: bytes) -> None:
    """Check the original writer before native decoding, scanning ASCII attributes only.

    Models carry their producer in the closure. A shape's native stamp serves the same purpose.
    Known older model formats stay readable; missing legacy platform fields are not invented.
    """
    from cadgen.engine import previous

    head = data[:previous._HEAD_BYTES]
    writer, closure = previous._head(head)
    import re

    matched = re.fullmatch(r"cadgen\.engine (xbf/[0-9]+) \(cadgen .+\)", writer or "")
    written = matched.group(1) if matched else None
    found = None
    prefix = ('{"schema":"' + NATIVE_SCHEMA + '",').encode()
    at = head.find(prefix)
    if at >= 0:
        try:
            stamp, _ = json.JSONDecoder().raw_decode(head[at:].decode("ascii", "replace"))
            found = stamp.get("compatibility")
        except (ValueError, AttributeError):
            pass
    if found is None and closure is not None:
        record = json.loads(closure)
        found = {"format": (record.get("engine") or {}).get("format"),
                 "OCP": (record.get("libs") or {}).get("OCP"), **(record.get("platform") or {})}
    current = compatibility()
    if not isinstance(found, dict) or not found.get("OCP"):
        raise IncompatibleNative("the last-built document has no producer native compatibility metadata; "
                                 "run its model or hand the shape over again")
    if (written not in READABLE_FORMATS or found.get("format") != written
            or found.get("OCP") != current["OCP"]
            or any(found.get(name) is not None and found[name] != current[name] for name in ("system", "arch"))):
        raise IncompatibleNative("the last-built document has incompatible producer native kernel/document formats; "
                                 "use a compatible server, or run its model or hand the shape over again")


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
