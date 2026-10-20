"""cadgen's engine: ``@xbf`` models built in a warm worker, kept as XCAF documents.

A model is a parameterless function returning a build123d shape::

    from cadgen.engine import xbf, step

    @xbf
    @step(out="arm.step")
    def arm():
        ...

``cadgen xbf build arm.py::arm`` runs it in a warm worker whose kernel memo makes an
edit cost what it changed; the document is written to ``arm.xbf`` beside the script,
and the declared exports after it. ``cadgen xbf serve arm.xbf`` shows it. Nothing but
those files is written: every cache lives in a process's memory.

Importing this package imports no kernel: the names below load on first use.
"""

from __future__ import annotations

from typing import Any

__all__ = ["glb", "paint", "read_step", "step", "stl", "threemf", "xbf"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from cadgen.engine import model

        return getattr(model, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
