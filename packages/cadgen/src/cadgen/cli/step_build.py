"""``cadgen step build`` — a GENERATED CLI over :func:`cadgen.step.build`.

There is no parser here on purpose. Everything the command accepts is derived
from the verb function's signature by
:mod:`cadgen._internal.cli_from_function`, so a flag cannot drift from a
parameter: this module only names which function the command is (README law 6).

``IN.step OUT.step``: one document in, a NEW document out, re-emitted in
cadgen's dialect and optionally annotated with ``--kinematics``.
OUT is a REQUIRED positional, which is what tells this apart from the cache
action next door — compile caches a document; build writes a new one.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from cadgen._internal.cli_from_function import generated_main, generated_parser

DEFAULT_PROG = "cadgen step build"
VERB = ("cadgen.step", "build")


def build_parser(prog: str = DEFAULT_PROG) -> argparse.ArgumentParser:
    return generated_parser(VERB, prog=prog)


def main(argv: Sequence[str] | None = None, *, prog: str = DEFAULT_PROG) -> int:
    return generated_main(VERB, argv, prog=prog)


if __name__ == "__main__":
    import sys

    from cadgen.cli import main as front_door

    # Run as a module, the command is still a door: handed to the daemon as `cadgen` hands it.
    raise SystemExit(front_door([*DEFAULT_PROG.split()[1:], *sys.argv[1:]]))
