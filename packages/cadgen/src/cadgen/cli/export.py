"""Finish one file from its recorded result; never run a model."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

DEFAULT_PROG = "cadgen export"


def build_parser(*, prog: str = DEFAULT_PROG) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description=__doc__)
    parser.add_argument("path", type=Path, help="the saved file to make available")
    return parser


def main(argv: Sequence[str] | None = None, *, prog: str = DEFAULT_PROG) -> int:
    args = build_parser(prog=prog).parse_args(argv)
    from cadgen._internal.cli_from_function import report_failure
    from cadgen._internal.doors import display_path
    from cadgen._internal.export_saved import export_saved

    try:
        target = export_saved(args.path)
    except Exception as error:
        report_failure(error, prog=prog, as_json=False, verbose_hint=False)
        return 1
    print(f"ready {display_path(target)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
