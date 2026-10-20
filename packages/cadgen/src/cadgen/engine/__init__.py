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

A part's material is a value set on the shape: ``part.material = materials.aluminium``
(``Material`` for any other). An assembly's mechanism is build123d's joints, placed with
``connect_to``; ``configuration(assembly, name, **values)`` names a configuration of it.
Importing this package imports no kernel: the names below load on first use.
"""

from __future__ import annotations

from typing import Any

__all__ = ["Material", "configuration", "glb", "materials", "paint", "read_bytes", "read_json", "read_step",
           "read_text", "step", "stl", "threemf", "xbf"]

# The files a model reads through the engine, keyed by their bytes (``reads``).
_READS = frozenset({"read_bytes", "read_json", "read_text"})


def __getattr__(name: str) -> Any:
    if name == "materials":
        import importlib

        return importlib.import_module("cadgen.engine.materials")
    if name == "Material":
        from cadgen.engine.materials import Material

        return Material
    if name in _READS:
        from cadgen.engine import reads

        return getattr(reads, name)
    if name in __all__:
        from cadgen.engine import model

        return getattr(model, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
