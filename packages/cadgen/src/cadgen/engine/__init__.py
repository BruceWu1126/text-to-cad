"""cadgen's engine: ``@xbf`` models built in a warm worker, kept as XCAF documents.

A model is a parameterless function returning a build123d shape::

    from cadgen.engine import xbf, step

    @xbf
    @step("arm.step")
    def arm():
        ...

``python arm.py`` runs it, and every other model the script defines, in a warm worker whose
kernel memo makes an edit cost what it changed; the document is written to
``XBF/arm.xbf`` beside the script, and the derived files it declares after it, each a
translation of that document (``cadgen xbf export``). ``cadgen xbf serve XBF/arm.xbf``
shows it. Nothing but those files is written: every cache lives in a process's memory.
The verbs as functions are ``cadgen.engine.api`` (``cadgen.build``, ``cadgen.export``, ...).

A function worth replaying on a later build -- an assembly's part, a derivation's stage --
is marked ``@memo``: it writes no file, and a build whose edit does not reach it replays it
instead of running it. A part's material is a value set on the shape: ``part.material = materials.aluminium``
(``Material`` for any other). An assembly's mechanism is build123d's joints, placed with
``connect_to``, as built where the joints place the parts; ``configuration(assembly, name,
**values)`` names a configuration of it and ``motion(assembly, name, ...)`` a motion, which a
GLB export bakes into a clip. ``read_step(path)`` and ``read_xbf(path)`` copy a file's geometry into
the model, named, coloured and given its materials as the file has them. ``select(source, ref,
kind=None)`` is the geometry a ref names -- ``hand/finger.2/f:finger.tip.1``, ``base/face 3`` --
as build123d shapes in world coordinates (``refs``).
Importing this package imports no kernel: the names below load on first use. Imported by a
model script run as a program, it asks the daemon to build the script there and then
(``model.run_at_import``): a script imports the engine before build123d, so the asking process
never loads it. Imported by a
script run by its path from inside a package (``python proj/parts/model.py``), it gives the
script the package ``python -m proj.parts.model`` would (``model.as_package_module``).
"""

from __future__ import annotations

from typing import Any

__all__ = ["Material", "configuration", "dxf", "glb", "materials", "memo", "motion", "paint", "read_step", "read_xbf",
           "report", "select", "step", "stl", "threemf", "track", "xbf"]


def _main_in_a_package() -> None:
    """A ``__main__`` run by its path (no spec, no package) from a folder holding an
    ``__init__.py`` takes its package now, before its own relative imports run."""
    import os
    import sys

    main = sys.modules.get("__main__")
    file = getattr(main, "__file__", None)
    if getattr(main, "__spec__", True) is not None or getattr(main, "__package__", None) or \
            not isinstance(file, str) or not file.endswith(".py") or \
            not os.path.isfile(os.path.join(os.path.dirname(os.path.abspath(file)), "__init__.py")):
        return
    from cadgen.engine.model import as_package_module

    as_package_module(main)


_main_in_a_package()


def __getattr__(name: str) -> Any:
    if name == "materials":
        import importlib

        return importlib.import_module("cadgen.engine.materials")
    if name == "Material":
        from cadgen.engine.materials import Material

        return Material
    if name in ("report", "track"):
        from cadgen.engine import progress

        return getattr(progress, name)
    if name == "select":
        from cadgen.engine.refs import select

        return select
    if name in __all__:
        from cadgen.engine import model

        return getattr(model, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _run_main_as_program() -> None:
    """``python model.py``: a model script asks the daemon to build it the moment it imports the
    engine, so this process never loads build123d (``model.run_at_import``). Last, once every
    name above can load."""
    import sys

    file = getattr(sys.modules.get("__main__"), "__file__", None)
    if isinstance(file, str) and file.endswith(".py"):
        from cadgen.engine.model import run_at_import

        run_at_import(file)


_run_main_as_program()
