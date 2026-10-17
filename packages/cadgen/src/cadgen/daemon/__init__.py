"""Warm-process daemon for the CAD build CLIs: where every build, compile and artifact job runs.

Holds OCP, build123d and the parser modules resident so repeated builds skip the import
cost. Every front door — the ``cadgen`` CLI's doors and a model script's top-level call —
hands its job to it. The daemon sets ``CADGEN_DAEMON_CHILD`` in the process it serves from
so a tool invoked through it cannot recurse back into the client.
"""

from __future__ import annotations


def main(argv: list[str] | None = None) -> int:
    """Start the daemon without importing its server during package discovery."""
    from cadgen.daemon.server import main as server_main

    return server_main(argv)

__all__ = ["main"]
