"""``cadgen doctor`` — report the installed cadgen, publish its launchers, check a skill's pin.

The one check a skill teaches: run it pointed at the skill (its directory or SKILL.md), and it
says which installation is running -- its folder, and the engine server it shares -- and whether
that is the cadgen the skill's docs were written against: the version its install command pins
(``uvx ... --from cadgen==<version> cadgen doctor``). Run by that command, it is also how a skill
gets its launchers back: it installs cadgen and publishes ``~/.local/bin/cadgen-<version>`` and
``cadgen-python-<version>`` (``cadgen._internal.launchers``), and says whether each is present
and runs this installation, dangling, or missing. ``--prune-launchers`` removes every version's
launchers whose environment is gone.

It also proves the CAD kernel loads. ``OCP`` is imported in a fresh interpreter
(never in this one, so the report itself stays stdlib-only and fast to reach),
and a refused load is named for what it is: on Windows 11 that is Smart App
Control blocking the unsigned ``.pyd``, which the bare ``ImportError`` never
says (``cadgen._internal.kernel_load_hint``). And it says where the engine's server,
where every build runs, keeps its door and whether one is running, without starting one.

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


def _server() -> str:
    """The engine server this installation shares (``cadgen.engine.daemon``): where its door is,
    and whether one is running -- asked, never started."""
    try:
        from cadgen.engine import daemon as engine_daemon
        from cadgen.engine.client import request

        where = engine_daemon.address()
        answer = request({"op": "status"}, start=False)
    except Exception as error:  # noqa: BLE001 - a report line, never a reason for the report to fail
        return f"unknown ({type(error).__name__}: {error})"
    if not isinstance(answer, dict):
        return f"not running ({where}); the first build starts it"
    workers = len(answer.get("workers") or [])
    return f"running, pid {answer.get('pid')}, {workers} worker{'' if workers == 1 else 's'} ({where})"


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


_SAY = {
    "ok": "OK",
    "missing": "missing",
    "dangling": "dangling — its environment is gone",
    "other": "another installation's",
    "foreign": "not cadgen's (left alone)",
}


def _launchers() -> list[str]:
    """One report line per launcher: published (or repaired) when this installation may, else
    as found -- and why a write failed, which in a sandbox is the folder being out of reach."""
    from cadgen._internal import launchers

    try:
        version = launchers.own_version()
        if version:  # this command's start may have published them already: say so
            done = launchers.last
            fresh = done is not None and done[:2] == (version, launchers.folder())
            results = done[2] if fresh else launchers.publish(version)
        else:  # a source tree: says what the release's launchers are, writes nothing
            from cadgen import __version__

            results = [(path, status, None) for path, status in launchers.inspect(__version__)]
            version = None
    except Exception as error:  # noqa: BLE001 - a report line, never a reason for the report to fail
        return [f"unknown ({type(error).__name__}: {error})"]
    lines = []
    for path, status, error in results:
        if status in launchers.REPAIRABLE and version and not error:
            lines.append(f"{path} published (was {_SAY[status]})")
        elif error:
            lines.append(f"{path} {_SAY[status]}; cannot write it here ({error}): run this outside any sandbox")
        else:
            lines.append(f"{path} {_SAY[status]}")
    return lines


def main(argv: list[str] | None = None, prog: str = "cadgen doctor") -> int:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Print the installed cadgen and verify a skill's cadgen version pin.",
    )
    parser.add_argument(
        "skill",
        nargs="?",
        help="A skill's SKILL.md, or a directory containing one (default: the working directory).",
    )
    parser.add_argument(
        "--prune-launchers",
        action="store_true",
        help="Remove every version's launchers in ~/.local/bin whose environment is gone, and exit.",
    )
    args = parser.parse_args(argv)

    if args.prune_launchers:
        from cadgen._internal import launchers

        removed = launchers.prune()
        for path in removed:
            sys.stdout.write(f"removed {path}\n")
        if not removed:
            sys.stdout.write(f"no launcher in {launchers.folder()} has lost its environment\n")
        return 0

    import cadgen
    from cadgen._internal.launch import launch_command
    from cadgen.cli import read_skill_pin

    installed = getattr(cadgen, "__version__", "unknown")
    location = Path(cadgen.__file__).resolve().parent
    sys.stdout.write(f"cadgen {installed}\n")
    sys.stdout.write(f"  python   {sys.version.split()[0]} ({sys.executable})\n")
    sys.stdout.write(f"  install  {location}\n")
    # One per installation: the same door means the same warm daemon.
    sys.stdout.write(f"  server   {_server()}\n")
    # The paths the skills run this installation by (`cadgen._internal.launchers`).
    for line in _launchers():
        sys.stdout.write(f"  launcher {line}\n")
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
