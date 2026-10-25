"""``cadgen doctor`` — set cadgen up and report it: register the installation, write the ``cadgen``
command, start the engine, check a skill's pin.

The skills' one-time step (``uvx ... --from cadgen==<version> cadgen doctor``), and the repair
command. It registers this installation (``<state>/installations/<version>.json``) and writes the
proxy, ``~/.local/bin/cadgen`` (``cadgen._internal.installations``), through which the skills run
``cadgen +<version>``, and says what it found: each written, already right, or a folder it cannot
write (a sandbox's), the records of installations whose interpreter is gone, and a file in the
proxy's place that is not cadgen's. ``--prune`` removes those records, and the per-version
launchers (``cadgen-<version>``, ``cadgen-python-<version>``) earlier builds published beside the
proxy (``installations.prune_legacy``). Then it starts the engine
when none runs (``cadgen.engine.client``): in a sandbox that cannot start one it says so in its usual
line. ``--no-server`` does all of it but start the engine, saying whether one runs: a CI job's
smoke check, which must leave no engine behind. Run pointed at a skill (its directory or SKILL.md), it also says whether this is the cadgen the
skill's docs were written against: the version its install command pins.

It also proves the CAD kernel loads. ``OCP`` is imported in a fresh interpreter
(never in this one, so the report itself stays stdlib-only and fast to reach),
and a refused load is named for what it is: on Windows 11 that is Smart App
Control blocking the unsigned ``.pyd``, which the bare ``ImportError`` never
says (``cadgen._internal.kernel_load_hint``). And it says where the engine's server,
where every build runs, keeps its door, and whether it was running or was started.

Exit codes: 0 = installed cadgen matches the pin (or nothing claims a pin);
3 = pin mismatch; 4 = the kernel is installed but cannot be loaded (the report
says why when it can tell). A kernel that is simply not installed is reported,
not failed: a no-deps install of the wheel (what the release workflow
smoke-tests) has no OCP and is still a correct install, and the launch command
is what brings the kernel.

A mismatch has TWO fixes and they are not interchangeable, so the report picks
one by asking the install what KIND it is (:func:`_editable_source`). An
ordinary install is simply not the skill's, and the skill's launch command runs
the pinned release. An EDITABLE install records its version once, when it is
installed, and never re-reads the project's metadata — so its code can be
current while the version it reports is not, and running the release instead
would swap the working tree being developed for a published one. The fix there
is to re-install it in place, which re-records the version.

stdlib-only on purpose: this must work when the heavy dependency set is broken.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _resolve_skill(target: str | None) -> Path | None:
    """The SKILL.md to check: an explicit file, a directory containing one, or the
    working directory's — ``None`` when nothing exists to check."""
    base = Path(target).expanduser() if target else Path.cwd()
    if base.is_file():
        return base
    candidate = base / "SKILL.md"
    return candidate if candidate.is_file() else None


def _server(start: bool = True) -> str:
    """The engine server this installation shares (``cadgen.engine.daemon``): where its door is,
    and whether one is running -- started when none is and ``start``, through the client's own start,
    which in a sandbox that cannot start one says so."""
    try:
        from cadgen.engine import daemon as engine_daemon
        from cadgen.engine.client import request

        where = engine_daemon.address()
        answer = request({"op": "status"}, start=False)
        started = False
        if not isinstance(answer, dict) and start:
            try:
                answer = request({"op": "status"}, start=True)
            except ConnectionError as error:
                return f"not running ({where}): {error}"
            started = True
    except Exception as error:  # noqa: BLE001 - a report line, never a reason for the report to fail
        return f"unknown ({type(error).__name__}: {error})"
    if not isinstance(answer, dict):
        return f"not running ({where}); the first build starts it"
    workers = len(answer.get("workers") or [])
    return (f"{'started' if started else 'running'}, pid {answer.get('pid')}, "
            f"{workers} worker{'' if workers == 1 else 's'} ({where})")


def _sharing() -> str | None:
    """``on`` or ``off`` for the usage stats CAD sends, and why, or ``None`` if it cannot tell."""
    try:
        from cadgen.cli.telemetry import describe

        return describe()
    except Exception:  # noqa: BLE001 - a report line, never a reason for the report to fail
        return None


def _editable_source() -> str | None:
    """The directory this cadgen was EDITABLE-installed from, or ``None``
    (``cadgen._internal.editable``).

    ``pip install -e <dir>`` writes the project's version into the distribution's
    metadata at install time, and the import path then points at the live
    directory, but the recorded version is a SNAPSHOT: nothing re-reads the
    project's metadata afterwards, so a source tree whose version has since been
    bumped keeps reporting the version it was installed at. That is not a wrong
    reading of the version -- it is what this interpreter really has installed --
    and it is why the mismatch above it may need a different command.
    """
    from cadgen._internal.editable import editable_source

    return editable_source()


KERNEL_OK = "ok"
KERNEL_MISSING = "missing"
KERNEL_FAILED = "failed"


def _probe_kernel() -> tuple[str, str]:
    """Import OCP in a fresh interpreter: ``(state, detail)``.

    ``state`` is ``KERNEL_OK`` (``detail`` is the module's path),
    ``KERNEL_MISSING`` (no OCP installed: ``ModuleNotFoundError``), or
    ``KERNEL_FAILED`` (installed but would not load; ``detail`` is the last
    non-empty stderr line, the exception). A subprocess, so a kernel that
    crashes the interpreter or takes the ~2.5 s import cannot take the report
    down with it, and so this process never carries the kernel itself.
    """
    import os
    import subprocess

    try:
        # Both ends name utf-8: the child's stdio would otherwise be the ANSI code page
        # on Windows, which cannot carry every path or every localized loader error.
        # ``-P``, as every child cadgen starts: the kernel the installation imports, never a
        # module of that name in the folder the command was run from.
        result = subprocess.run(
            [sys.executable, "-P", "-c", "import OCP; print(OCP.__file__)"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="backslashreplace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return KERNEL_FAILED, f"{type(error).__name__}: {error}"
    if result.returncode == 0:
        lines = [text for text in result.stdout.splitlines() if text.strip()]
        return KERNEL_OK, lines[-1].strip() if lines else ""
    lines = [text for text in result.stderr.splitlines() if text.strip()]
    detail = lines[-1].strip() if lines else f"exit status {result.returncode}"
    if detail.startswith("ModuleNotFoundError:"):
        return KERNEL_MISSING, detail
    return KERNEL_FAILED, detail


def _setup() -> list[str]:
    """The report lines of the setup: this installation registered and the proxy written when this
    installation may (a source tree registers only when named), the records whose interpreter is
    gone, and why a write failed, which in a sandbox is the folder being out of reach."""
    from cadgen._internal import installations as setup

    lines = []
    try:
        version = setup.own_version()
        if version:
            registered, proxy = setup.setup(version)
        else:
            registered, proxy = None, (setup.proxy_path(), setup.proxy_status(), None)
        if registered is None:
            lines.append(f"registry not registered: a source tree registers only as a version it is named "
                         f"({setup.NAME_ENV})")
        else:
            path, status, error = registered
            lines.append(f"registry {path} " + (f"cannot be written here ({error}): run this outside any sandbox" if error
                                                else "registered" if status == setup.REGISTERED else "OK"))
        path, status, error = proxy
        said = {setup.OK: "OK", setup.NEWER: "OK (a later cadgen's)",
                setup.FOREIGN: "is not cadgen's (left alone): remove it to run cadgen +<version> by it",
                setup.MISSING: "missing", setup.STALE: "stale"}[status]
        if error:
            lines.append(f"command  {path} {said}; cannot write it here ({error}): run this outside any sandbox")
        elif status in (setup.MISSING, setup.STALE) and version:
            lines.append(f"command  {path} written (was {said})")
        else:
            lines.append(f"command  {path} {said}")
        for record in setup.records():
            if not setup.alive(record):
                lines.append(f"registry {record['path']} dangling: its interpreter {record['python']} is gone "
                             "(cadgen doctor --prune removes it)")
    except Exception as error:  # noqa: BLE001 - a report line, never a reason for the report to fail
        lines.append(f"registry unknown ({type(error).__name__}: {error})")
    return lines


def main(argv: list[str] | None = None, prog: str = "cadgen doctor") -> int:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Set cadgen up (register it, write the cadgen command, start the engine), report it, "
                    "and verify a skill's cadgen version pin.",
    )
    parser.add_argument(
        "skill",
        nargs="?",
        help="A skill's SKILL.md, or a directory containing one (default: the working directory).",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Remove the record of every registered installation whose interpreter is gone, and the "
             "per-version launchers earlier builds published beside the cadgen command, and exit.",
    )
    parser.add_argument(
        "--no-server",
        action="store_true",
        help="Do everything but start the engine: say only whether one is running.",
    )
    args = parser.parse_args(argv)

    if args.prune:
        from cadgen._internal import installations

        removed = installations.prune()
        for path in removed:
            sys.stdout.write(f"removed {path}\n")
        if not removed:
            sys.stdout.write("no registered installation has lost its interpreter\n")
        # And the per-version launchers earlier 0.8 builds published beside the cadgen command.
        for path in installations.prune_legacy():
            sys.stdout.write(f"removed {path}, a launcher the cadgen command replaces\n")
        return 0

    import cadgen
    from cadgen._internal.launch import launch_command
    from cadgen.cli import read_skill_pin

    installed = getattr(cadgen, "__version__", "unknown")
    location = Path(cadgen.__file__).resolve().parent
    sys.stdout.write(f"cadgen {installed}\n")
    sys.stdout.write(f"  python   {sys.version.split()[0]} ({sys.executable})\n")
    sys.stdout.write(f"  install  {location}\n")
    # Its record and the command the skills run it by (`cadgen._internal.installations`).
    for line in _setup():
        sys.stdout.write(f"  {line}\n")
    # One per installation: the same door means the same warm daemon. Started here when none runs.
    sys.stdout.write(f"  server   {_server(start=not args.no_server)}\n")
    sharing = _sharing()
    if sharing:
        # Whether CAD's apps send usage stats, as `cadgen telemetry` says it.
        sys.stdout.write(f"  sharing  {sharing}\n")

    from cadgen._internal.kernel_load_hint import kernel_load_hint

    kernel_state, detail = _probe_kernel()
    kernel_loaded = kernel_state != KERNEL_FAILED
    status = 0 if kernel_loaded else 4
    if kernel_state == KERNEL_OK:
        sys.stdout.write(f"  kernel   OK — OCP at {detail}\n")
    elif kernel_state == KERNEL_MISSING:
        # Not a failure: the launch command brings the kernel, and a wheel
        # installed --no-deps (the release smoke test) legitimately has none.
        sys.stdout.write(
            "  kernel   not installed — OCP is absent from this interpreter "
            f"(run cadgen as the skills do, {launch_command()}, and it is there)\n"
        )
    else:
        sys.stderr.write(f"  kernel   FAILED — {detail}\n")
        for hint in kernel_load_hint(detail) or ():
            sys.stderr.write(f"           {hint}\n")

    skill = _resolve_skill(args.skill)
    if skill is None:
        sys.stdout.write("  pin      none found (no SKILL.md to check)\n")
        return status

    pin = read_skill_pin(skill)
    if pin is None:
        # No pinned launch command (a development install points it at a checkout): nothing to enforce.
        sys.stdout.write(f"  pin      unpinned in {skill}\n")
        return status
    if pin == installed:
        sys.stdout.write(f"  pin      OK — cadgen=={pin} ({skill})\n")
        return status
    from cadgen._internal.installations import VERSION

    if not VERSION.fullmatch(pin):
        # A SKILL.md in a downloaded project is anyone's: a "pin" that is no version (a shell
        # command, a uv option) is said as data, never built into the command printed below.
        sys.stderr.write(f"  pin      MISMATCH — {skill} pins no cadgen version: {ascii(pin[:64])}\n")
        return 3
    sys.stderr.write(
        f"  pin      MISMATCH — {skill} pins cadgen=={pin}, "
        f"but cadgen {installed} is installed.\n"
    )
    editable = _editable_source()
    if editable is None:
        sys.stderr.write(
            "This is not the installation the skill uses. Run cadgen with its launch command:\n"
            f"  {launch_command(version=pin)} ...\n"
        )
    else:
        # The install is a live source tree, so the code may already BE the
        # pinned version: what is stale is the version this install recorded
        # when it was made. Naming the other command here would exchange that
        # source tree for a published release, which is not the fix.
        sys.stderr.write(
            f"This cadgen is an EDITABLE install of {editable}. Its version was recorded when\n"
            "it was installed and is never re-read, so the code can be current while the\n"
            "version it reports is not. Re-install it in place to re-record it:\n"
            f"  uv pip install -e {editable}\n"
            "(the skill's launch command would run the published release instead of that source\n"
            "tree.)\n"
        )
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
