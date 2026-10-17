"""``cadgen glb build`` — a GENERATED CLI over :func:`cadgen.glb.build`.

The GLB shell of the three per-format doors; see :mod:`cadgen.cli.stl_build`.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from cadgen._internal.cli_from_function import generated_main, generated_parser

DEFAULT_PROG = "cadgen glb build"
VERB = ("cadgen.glb", "build")


def build_parser(prog: str = DEFAULT_PROG) -> argparse.ArgumentParser:
    return generated_parser(VERB, prog=prog)


def main(argv: Sequence[str] | None = None, *, prog: str = DEFAULT_PROG) -> int:
    return generated_main(VERB, argv, prog=prog)


if __name__ == "__main__":
    import sys

    from cadgen.cli import main as front_door

    # Run as a module, the command is still a door: handed to the daemon as `cadgen` hands it.
    raise SystemExit(front_door([*DEFAULT_PROG.split()[1:], *sys.argv[1:]]))
