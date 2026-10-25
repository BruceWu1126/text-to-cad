"""The launchers: an installation's ``cadgen`` and ``python`` at a path a skill can name.

A skill runs cadgen in the agent's shell, and that shell is often a sandbox that lets a command
read and execute anything but write only to its working folder and the temporary folder. ``uvx``
cannot start in one: uv writes its cache before it does anything else. The environment uv built
starts fine there. So the installation publishes two launchers into uv's user bin folder, named by
version, and the skills run them:

- ``~/.local/bin/cadgen-<version>``: this installation's ``cadgen`` console script;
- ``~/.local/bin/cadgen-python-<version>``: its ``python``.

On POSIX ``cadgen-<version>`` is a symlink to the console script, as ``uv tool install`` links its
tools, and ``cadgen-python-<version>`` is a two-line ``sh`` script that ``exec``s the environment's
python by its path. A symlink cannot be the python: an interpreter looks for its environment's
``pyvenv.cfg`` beside the path it was started by, so a link elsewhere starts the bare base
interpreter, without cadgen. On Windows, where a symlink needs a privilege, ``cadgen-<version>.exe``
is a copy of the console script's launcher (it names its python by absolute path, which is why uv's
tool install can copy it too) and ``cadgen-python-<version>.cmd`` runs the environment's
``python.exe``.

Launchers are written at install moments only, in two places. The CAD app's server
(``cadgen mcp``) publishes them as it starts (:func:`startup`): the host starts it outside any
sandbox, at the moment it installs or updates the plugin. ``cadgen doctor``, the skills' one-time
step and the repair command, publishes them and says what it found. Either checks both launchers,
an ``lstat`` and a compare, and writes one only when it is missing, dangling or another
installation's; a folder it cannot write (a sandbox's) is skipped, silently by the server and said
by ``doctor``. No other command publishes or repairs anything: a dangling launcher is ``doctor``'s
to report and fix. A launcher of another version is never touched; ``cadgen doctor
--prune-launchers`` removes those whose environment is gone.

An editable install (a checkout's environment) publishes nothing by itself: it reports the
release's version, and its launchers would take a user's place. A development install names its
own (``CADGEN_LAUNCHER_VERSION``). ``CADGEN_LAUNCHER_DIR`` moves the folder.
"""

from __future__ import annotations

import os
import re
import sys

DIR_ENV = "CADGEN_LAUNCHER_DIR"
NAME_ENV = "CADGEN_LAUNCHER_VERSION"
WINDOWS = os.name == "nt"
# The line that makes a python launcher cadgen's, and names the python it runs.
MARK = "cadgen launcher: "
_NAME = re.compile(r"[0-9][0-9A-Za-z.+_-]*")

OK = "ok"
MISSING = "missing"
DANGLING = "dangling"  # its environment is gone
OTHER = "other"  # another installation of this version
FOREIGN = "foreign"  # a file of that name that cadgen did not write: left alone
REPAIRABLE = (MISSING, DANGLING, OTHER)


def folder() -> str:
    """uv's user bin folder, ``~/.local/bin`` (``%USERPROFILE%\\.local\\bin`` on Windows)."""
    return os.environ.get(DIR_ENV) or os.path.join(os.path.expanduser("~"), ".local", "bin")


def names(version: str, windows: bool = WINDOWS) -> tuple[str, str]:
    """The two launchers' file names for ``version``."""
    if windows:
        return f"cadgen-{version}.exe", f"cadgen-python-{version}.cmd"
    return f"cadgen-{version}", f"cadgen-python-{version}"


def targets() -> tuple[str, str] | None:
    """This installation's console script and python, or ``None`` without a console script.

    The python is the environment's own path, never resolved: resolving it reaches the base
    interpreter, which is not this environment.
    """
    if sys.prefix != sys.base_prefix:
        scripts = os.path.join(sys.prefix, "Scripts" if WINDOWS else "bin")
        python = os.path.join(scripts, "python.exe" if WINDOWS else "python")
        if not os.path.isfile(python):
            python = sys.executable
    else:  # not a virtual environment: rare, and sysconfig costs a few milliseconds
        import sysconfig

        scripts = sysconfig.get_path("scripts")
        python = sys.executable
    console = os.path.join(scripts, "cadgen.exe" if WINDOWS else "cadgen")
    if not (python and os.path.isfile(console)):
        return None
    return os.path.abspath(console), os.path.abspath(python)


def python_script(python: str, windows: bool = WINDOWS) -> str:
    """The python launcher's text: it names ``python`` (``MARK``) and runs it with every argument."""
    if windows:
        return f"@REM {MARK}{python}\r\n@\"{python}\" %*\r\n"
    quoted = "'" + python.replace("'", "'\\''") + "'"
    return f"#!/bin/sh\n# {MARK}{python}\nexec {quoted} \"$@\"\n"


def _recorded(path: str) -> str | None:
    """The python a launcher of cadgen's names, or ``None`` when ``path`` is not one."""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            head = handle.read(4096)
    except (OSError, ValueError):
        return None
    for line in head.splitlines()[:3]:
        at = line.find(MARK)
        if at >= 0:
            return line[at + len(MARK):].strip()
    return None


def _console_status(path: str, target: str, windows: bool) -> str:
    try:
        found = os.lstat(path)
    except OSError:
        return MISSING
    if windows:
        # A copy: the size and modification time of the console script it was copied from.
        try:
            source = os.stat(target)
        except OSError:
            return OTHER
        return OK if (found.st_size, found.st_mtime_ns) == (source.st_size, source.st_mtime_ns) else OTHER
    import stat

    if not stat.S_ISLNK(found.st_mode):
        return FOREIGN
    if not os.path.exists(path):
        return DANGLING
    try:
        return OK if os.readlink(path) == target else OTHER
    except OSError:
        return OTHER


def _python_status(path: str, python: str) -> str:
    if not os.path.lexists(path):
        return MISSING
    recorded = _recorded(path)
    if recorded is None:
        return FOREIGN
    if not os.path.exists(recorded):
        return DANGLING
    return OK if recorded == python else OTHER


def inspect(version: str, where: str | None = None, found: tuple[str, str] | None = None,
            windows: bool = WINDOWS) -> list[tuple[str, str]]:
    """``[(path, status), ...]`` for the two launchers of ``version``, without writing anything."""
    where = where or folder()
    found = found or targets()
    console_name, python_name = names(version, windows)
    console, python = os.path.join(where, console_name), os.path.join(where, python_name)
    if found is None:  # nothing of this installation's to compare with
        return [(path, MISSING if not os.path.lexists(path) else OTHER) for path in (console, python)]
    return [(console, _console_status(console, found[0], windows)), (python, _python_status(python, found[1]))]


def _replace(path: str, write) -> None:
    """Write ``path`` whole, beside it first, so a reader never finds half a launcher."""
    from cadgen._internal.atomic_replace import replace_atomic, temp_suffix

    temporary = f"{path}{temp_suffix()}"
    try:
        write(temporary)
        replace_atomic(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _writer(kind: str, target: str, windows: bool):
    if kind == "python":
        text = python_script(target, windows)

        def write(temporary: str) -> None:
            with open(temporary, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
            os.chmod(temporary, 0o755)
    elif windows:
        def write(temporary: str) -> None:
            import shutil

            shutil.copy2(target, temporary)
    else:
        def write(temporary: str) -> None:
            os.symlink(target, temporary)
    return write


def publish(version: str, where: str | None = None, found: tuple[str, str] | None = None,
            windows: bool = WINDOWS) -> list[tuple[str, str, str | None]]:
    """Create or repair the launchers of ``version`` that are missing, dangling or another
    installation's: ``[(path, status before, error or None), ...]``. A folder it cannot write is
    not raised but returned as the error, for ``cadgen doctor`` to say."""
    if not _NAME.fullmatch(version or ""):
        raise ValueError(f"not a launcher version: {version!r}")
    where = where or folder()
    found = found or targets()
    states = inspect(version, where, found, windows)
    if found is None or all(status not in REPAIRABLE for _, status in states):
        return [(path, status, None) for path, status in states]
    results: list[tuple[str, str, str | None]] = []
    for (path, status), target, kind in zip(states, found, ("console", "python")):
        error = None
        if status in REPAIRABLE:
            try:
                os.makedirs(where, exist_ok=True)
                _replace(path, _writer(kind, target, windows))
            except OSError as failure:
                error = f"{type(failure).__name__}: {failure.strerror or failure}"
        results.append((path, status, error))
    return results


def prune(where: str | None = None, windows: bool = WINDOWS) -> list[str]:
    """Remove every version's launchers whose environment is gone; return their paths.

    Only cadgen's own: a symlink named ``cadgen-<version>`` that points nowhere, and a python
    launcher whose python is gone (on Windows, with the ``.exe`` of its version beside it).
    """
    where = where or folder()
    try:
        entries = sorted(os.listdir(where))
    except OSError:
        return []
    removed = []
    for entry in entries:
        path = os.path.join(where, entry)
        doomed: list[str] = []
        if entry.startswith("cadgen-python-"):
            version = entry[len("cadgen-python-"):]
            if windows:
                version = version[:-4] if version.endswith(".cmd") else ""
            recorded = _recorded(path) if _NAME.fullmatch(version) else None
            if recorded is not None and not os.path.exists(recorded):
                doomed = [path, os.path.join(where, names(version, True)[0])] if windows else [path]
        elif not windows and _NAME.fullmatch(entry[len("cadgen-"):]) and entry.startswith("cadgen-"):
            if os.path.islink(path) and not os.path.exists(path):
                doomed = [path]
        for gone in doomed:
            try:
                os.unlink(gone)
                removed.append(gone)
            except FileNotFoundError:
                pass
            except OSError:
                break
    return removed


def editable() -> bool:
    """Whether this cadgen is imported from outside its environment: a checkout's source tree
    (an editable install, or a path on ``PYTHONPATH``). A string compare, no metadata read."""
    import cadgen

    here = os.path.normcase(os.path.abspath(os.path.dirname(cadgen.__file__)))
    prefix = os.path.normcase(os.path.abspath(sys.prefix))
    return not here.startswith(prefix.rstrip(os.sep) + os.sep)


def own_version() -> str | None:
    """The version this process publishes launchers as: a development install's own name, else
    the installed version -- or ``None`` for a source tree, which publishes nothing by itself."""
    named = os.environ.get(NAME_ENV)
    if named:
        return named if _NAME.fullmatch(named) else None
    if editable():
        return None
    from cadgen import __version__

    return __version__


def startup() -> None:
    """As the CAD app's server starts: publish this installation's launchers if they need it,
    silently."""
    try:
        version = own_version()
        if version:
            publish(version)
    except Exception:  # noqa: BLE001 - a launcher never fails the server it rides on
        pass
