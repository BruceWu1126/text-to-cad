"""``cadgen where DOC [REF]``: the engine's ``where`` (``cadgen.engine.cli.where_main``), the
first of its verbs at the top level of ``cadgen``. Without REF it prints the build closure an
xbf holds; with one, what made that element."""

from __future__ import annotations

import sys


def build_parser(prog: str = "cadgen where"):
    from cadgen.engine.cli import where_parser

    return where_parser(prog)


def main(argv: list[str] | None = None, prog: str = "cadgen where") -> int:
    from cadgen.engine.cli import where_main

    return where_main(argv, prog)


if __name__ == "__main__":
    sys.exit(main())
