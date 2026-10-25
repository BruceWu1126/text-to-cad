"""``cadgen``, the command (``~/.local/bin/cadgen``): which installation runs, and running it.

The proxy's ``sh`` runs this module with the interpreter registered last
(``cadgen._internal.installations``); it picks one installation's record, first match wins:

a. ``+<version>`` as the first argument (``cadgen +0.8.0 export ...``: what the skills run);
b. ``CADGEN_VERSION``;
c. ``version = "<version>"`` in the first ``cadgen.toml``, from the current folder up, that says one;
d. the installation whose engine runs and is held by a CAD app server of the host this shell is
   (``engine_host.shell``: its variables; the hold names its host, ``cadgen.mcp.host.family``);
e. any installation whose engine runs, the newest version first;
f. the newest installation registered.

A version named (a-c) that is not registered, or whose interpreter is gone, fails in one line that
names the command that installs it. So only a version (``installations.VERSION``: a digit, then
letters, digits and ``.+_-``) is named: anything else -- a ``cadgen.toml`` of a downloaded project
whose ``version`` carries spaces, shell or uv options -- is refused without building a command of
it. A ``cadgen.toml`` another user owns, or that everyone may write (one in a shared folder above
this one), names nothing: it is passed over. The engines are found by their doors in the engine folder,
each asked its ``status`` at once and waited for ``ASK_SECONDS`` at most: only (d) and (e) ask.

Then ``cadgen [+V] run ARGS`` runs that installation's ``python ARGS`` (a model script, ``-m``,
``-c``), and anything else its ``cadgen`` console script with the arguments: by ``exec``, so the
proxy adds no process and no output of its own -- or, when the installation chosen is the one this
interpreter is (the usual case: one installation), as its console script would, in this process.
A development or source registration cannot know the version it is registered as (it reports its
release's), so it runs with ``CADGEN_INSTALLATION_VERSION`` set to its record's
(``installations.named``): its ``doctor``, its records and its engine name it rightly, and the
processes it starts, the same installation, inherit it.
"""

from __future__ import annotations

import os
import sys

ASK_SECONDS = 0.5
CONFIG = "cadgen.toml"
NOT_INSTALLED = ("cadgen {version} is not installed: run `uvx --no-config --managed-python --python 3.13 "
                 "--from cadgen=={version} cadgen doctor` once, outside any sandbox or with its permission")
# The ``cadgen`` command's own words (``installations._SH``) when no installation is registered.
NONE_INSTALLED = ("cadgen is not installed: run `uvx --no-config --managed-python --python 3.13 --from cadgen "
                  "cadgen doctor` once, outside any sandbox or with its permission")


class Refused(Exception):
    """What the proxy says instead of running anything: one line."""


def version_key(version: str) -> tuple:
    """Newest last, in the order an engine retires older installations' engines by
    (``cadgen.engine.daemon.rank``, ``packaging.version``'s): a release above its own pre-releases
    and development builds (``0.8.0-dev.<time>``), which sort by their time; a version that is no
    PEP 440 version below every one that is."""
    from cadgen.engine.daemon import rank

    ranked = rank(version)
    return (ranked is not None, ranked or ())


def named(args: list[str], environ, cwd: str | None) -> tuple[str | None, list[str]]:
    """The version the command names (a-c), and the arguments left to run. ``Refused`` for one
    that is no version: never echoed into the command the refusal names. ``cwd`` None: a shell
    whose folder was removed, where no ``cadgen.toml`` is looked for."""
    if args and args[0].startswith("+") and len(args[0]) > 1:
        return _version(args[0][1:], "its first argument"), args[1:]
    if environ.get("CADGEN_VERSION"):
        return _version(environ["CADGEN_VERSION"], "CADGEN_VERSION"), args
    if cwd is None:
        return None, args
    folder = os.path.abspath(cwd)
    while True:
        config = os.path.join(folder, CONFIG)
        if os.path.isfile(config) and _users_own(config):
            import tomllib

            try:
                with open(config, "rb") as handle:
                    version = tomllib.load(handle).get("version")
            except (OSError, ValueError, RecursionError) as error:
                raise Refused(f"cadgen: {config}: {error}") from None
            if isinstance(version, str) and version:
                return _version(version, config), args
        parent = os.path.dirname(folder)
        if parent == folder:
            return None, args
        folder = parent


def _version(version: str, source: str) -> str:
    """``version`` when it is one (``installations.VERSION``), else ``Refused``, saying where it came from."""
    from cadgen._internal.installations import VERSION

    if not VERSION.fullmatch(version):
        raise Refused(f"cadgen: {source} names no cadgen version: {ascii(version[:64])}")
    return version


def _users_own(config: str) -> bool:
    """Whether ``config`` is this user's: on POSIX a file of this user's that not everyone may
    write. A ``cadgen.toml`` in a shared folder above the current one (``/tmp``, say) is another
    user's to plant. Windows: any."""
    if os.name == "nt":
        return True
    try:
        found = os.stat(config)
    except OSError:
        return False
    return found.st_uid == os.getuid() and not found.st_mode & 0o002


def _same(one: str, other: str) -> bool:
    return os.path.normcase(os.path.realpath(one)) == os.path.normcase(os.path.realpath(other))


def choose(version: str | None, records: list[dict], engines, host: str | None) -> dict:
    """The record to run (a-f); ``engines()`` the running engines' statuses, asked only for (d)-(e)."""
    from cadgen._internal.installations import alive

    if version is not None:
        for record in records:
            if record["version"] == version and alive(record):
                return record
        raise Refused(NOT_INSTALLED.format(version=version))
    live = sorted((record for record in records if alive(record)), key=lambda r: version_key(r["version"]), reverse=True)
    if not live:
        raise Refused(NONE_INSTALLED)
    running = engines()
    serving = [record for record in live
               if any(isinstance(status.get("prefix"), str) and _same(status["prefix"], record["prefix"])
                      for status in running)]
    if host is not None:
        for record in serving:
            if any(_same(status["prefix"], record["prefix"]) and (status.get("hosts") or {}).get(host)
                   for status in running if isinstance(status.get("prefix"), str)):
                return record
    return serving[0] if serving else live[0]


def engines() -> list[dict]:
    """The ``status`` of every engine whose door answers in the engine folder, asked at once and
    waited for ``ASK_SECONDS`` at most: an engine that does not answer by then is left out."""
    import threading

    from cadgen.engine import client, daemon

    try:
        doors = sorted(daemon.state_dir().glob("*.port" if daemon.WINDOWS else "*.door"))
    except OSError:
        return []
    answers: list[dict] = []

    def ask(door) -> None:
        try:
            connection = client._connect(door)
            if connection is not None:
                answer = client._exchange(connection, {"op": "status"}, address=door)
                if answer.get("ok"):
                    answers.append(answer)
        except (OSError, ValueError):
            pass

    asking = [threading.Thread(target=ask, args=(door,), daemon=True) for door in doors]
    for thread in asking:
        thread.start()
    import time

    deadline = time.monotonic() + ASK_SECONDS
    for thread in asking:
        thread.join(max(0.0, deadline - time.monotonic()))
    return list(answers)


def command(record: dict, args: list[str]) -> list[str]:
    """What runs: the installation's python for ``run``, else its console script."""
    if args[:1] == ["run"]:
        return [record["python"], *args[1:]]
    return [record["cadgen"], *args]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        from cadgen._internal.installations import records
        from cadgen._internal.engine_host import shell

        try:
            cwd: str | None = os.getcwd()
        except OSError:  # its folder removed under the shell (a pruned worktree)
            cwd = None
        version, args = named(args, os.environ, cwd)
        record = choose(version, records(), engines, shell())
    except Refused as refusal:
        print(refusal, file=sys.stderr)
        return 1
    run = command(record, args)
    from cadgen._internal.installations import NAME_ENV, named as told

    if told(record):  # an installation that cannot know its own name: told it
        os.environ[NAME_ENV] = told(record)
    else:  # a release knows its own, whatever another installation's process that ran this was told
        os.environ.pop(NAME_ENV, None)
    if run[0] == record["cadgen"] and _same(sys.prefix, record["prefix"]):
        # The installation this interpreter is: its console script's work, here, without a second start.
        sys.argv = run
        from cadgen.cli import main as cadgen

        return cadgen(args)
    if os.name == "nt":  # no exec on Windows: a child, and its exit code
        import subprocess

        return subprocess.call(run, env=os.environ)
    try:
        os.execve(run[0], run, os.environ)
    except OSError as error:
        print(f"cadgen: cannot run {run[0]}: {error.strerror or error}", file=sys.stderr)
        return 1
    return 1  # not reached


if __name__ == "__main__":
    sys.exit(main())
