"""Internal runner for a directly-executed decorated model script.

NOT a user-facing CLI — the user interface is ``python <model>.py``, whose
``__main__`` calls the decorated model; that top-level call (cadgen.authoring)
hands it to the warm daemon (tool ``"run"``), whose worker runs it here. The argv
shape is ``[script, flags...]`` so the daemon can replay exactly what the call saw.

This module drives the one existing pipeline
(``cadgen.generation.generate_step_targets`` / ``generate_dxf_targets``) —
progress records, the no-op gate, incremental package build, ``.step``
assembly — with zero forked logic.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from cadgen.cli import NO_DAEMON_FLAG


def _build_parser(prog: str, *, internal: bool = False) -> argparse.ArgumentParser:
    """The per-run FLAGS of ``python <model>.py``.

    Flags only: the script is how the user got here, never something they pass,
    so it is not an argument of this parser (the internal argv carries it as its
    first item and :func:`run_model_argv` takes it off). ``--model`` is the
    pipeline's own routing flag -- which model of a file holding several this job
    builds -- and exists only on the ``internal`` parser, the one a worker's job is
    parsed by: a user selects a model by calling it in ``__main__``, and to the
    parser of what the user typed the flag is a name that never existed.
    """
    parser = argparse.ArgumentParser(
        prog=prog,
        description=(
            "Build this CAD model: gate, build, and write every output its decorators "
            "declare. It answers once every model's result is recorded, and the files "
            "follow. Reached by calling the decorated function in __main__."
        ),
        allow_abbrev=False,
    )
    if internal:
        parser.add_argument("--model", metavar="FUNCTION", help=argparse.SUPPRESS)
    parser.add_argument("--force", action="store_true", help="Rebuild even when current.")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed progress and timing on stderr, and a full traceback on failure.",
    )
    parser.add_argument(
        "--profile",
        action="store_true",
        help=(
            "Rebuild this model with its own code profiled (cProfile) and print where "
            "that time went on stderr. Its children are built or reused as usual and are "
            "not profiled. Slows Python-call-heavy code."
        ),
    )
    parser.add_argument("--json", action="store_true", help="One JSON result line on stdout.")
    parser.add_argument(
        NO_DAEMON_FLAG,
        dest="no_daemon",
        action="store_true",
        help=(
            "Build in this process, without the build daemon, for a place it cannot run "
            "(a sandbox without local sockets, a CI runner): the same files, written "
            "before the command answers, with no warm edits and its children one at a time."
        ),
    )
    return parser


def check_user_flags(flags: Sequence[str], *, prog: str) -> list[str]:
    """Parse what the USER typed after ``python <model>.py`` and return the flags
    to forward to the pipeline. Runs in the process the user started, before the
    handoff, so ``--help`` and a usage error end the run here (exit 0 / exit 2)
    instead of becoming a daemon job that "succeeds" without building anything.
    The user's parser knows the user's flags alone: a script builds what its
    ``__main__`` calls, and the pipeline's routing flag is, to the user, an
    unrecognized argument like any other."""
    _build_parser(prog).parse_args(list(flags))
    return list(flags)


def run_model_argv(argv: Sequence[str], *, prog: str = "python <model>.py") -> int:
    # The pipeline's entry outside cadgen.cli.main (a worker's run job, this module run
    # directly), so it needs the same UTF-8 streams.
    from cadgen.cli import _harden_std_stream_errors

    _harden_std_stream_errors()
    parser = _build_parser(prog, internal=True)
    items = list(argv)
    if not items or items[0].startswith("-"):
        parser.error("internal: the model script must be the first argument")
    args = parser.parse_args(items[1:])
    script = Path(items[0]).expanduser().resolve()
    if not script.is_file():
        parser.error(f"model script does not exist: {items[0]}")

    # Where this process's build-tree events go: a daemon worker already relays them
    # as frames; a root renders the tree itself.
    from cadgen.cli_tree import build_tree

    if args.no_daemon:
        # The one opt-in out of the daemon: every pool job the build owes runs here, inline,
        # and this process asks no daemon anything.
        from cadgen.daemon.client import no_daemon_from_now

        no_daemon_from_now()
    with build_tree(json_lines=bool(args.json)):
        return _run(args, script, prog)


def _run(args: argparse.Namespace, script: Path, prog: str) -> int:

    from cadgen.catalog import source_from_path

    try:
        function = getattr(args, "model", None)
        source = source_from_path(script, function=function)
        # The pipeline's target: the script, or ``script::fn`` for one model of a
        # file holding several (cadgen.store.index.model_ref).
        target = f"{script}::{function}" if function else str(script)
        if source is None:
            raise ValueError(
                f"{script.name} declares no CAD model — decorate one function with "
                "@step, @dxf, or a mesh decorator (@stl/@glb/@threemf) from cadgen"
            )
        if source.dxf_path is not None and source.step_path is None:
            from cadgen.generation import generate_dxf_targets

            return generate_dxf_targets(
                [target],
                # A profile needs the body to run: --profile rebuilds a current model.
                force=bool(args.force or args.profile),
                verbose=bool(args.verbose),
                json_output=bool(args.json),
                profile=bool(args.profile),
            )

        from cadgen.generation import generate_step_targets

        # The model's ``out=`` is the ONE spelling of where its document goes:
        # there is no per-run override (the record is keyed by the script, and
        # two documents for one model would be two truths).
        return generate_step_targets(
            [target],
            # A profile needs the body to run: --profile rebuilds a current model.
            force=bool(args.force or args.profile),
            verbose=bool(args.verbose),
            json_output=bool(args.json),
            profile=bool(args.profile),
        )
    except Exception as exc:  # noqa: BLE001 — the CLI boundary: report, do not traceback
        # The ONE failure envelope, shared with every generated door: under
        # --json a failure is `{"ok": false, "error": ...}` on stdout.
        from cadgen._internal.cli_from_function import report_failure

        return report_failure(exc, prog=prog, as_json=bool(args.json), verbose=bool(args.verbose))


def main(argv: Sequence[str] | None = None, *, prog: str = "python <model>.py") -> int:
    import sys

    return run_model_argv(list(argv) if argv is not None else sys.argv[1:], prog=prog)


if __name__ == "__main__":
    raise SystemExit(main())
