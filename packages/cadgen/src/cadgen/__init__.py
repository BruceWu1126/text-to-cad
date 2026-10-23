"""cadgen: CAD models as Python, built by its engine (``cadgen.engine``) into documents (``.xbf``)
and the files they declare.

The root exports the engine's surface -- the decorators (``xbf``, ``step``, ``glb``, ``stl``,
``threemf``, ``dxf``), ``memo``, materials (``Material``, ``materials``, ``paint``), the mechanism
(``configuration``, ``motion``, ``couple``), the readers (``read_step``, ``read_xbf``,
``read_mesh``), ``select``, progress (``report``, ``track``) -- and its verbs as functions
(``export``, ``snapshot``, ``serve``, ``inspect``, ``status``, ``stop``, ``daemon``),
every name loaded on first use: ``import cadgen`` imports no CAD kernel.
"""

from functools import cache as _cache
from typing import TYPE_CHECKING

# Before anything imports build123d, which every cadgen entry point eventually does:
# build123d parses EVERY font in the system font folders at import time, with no
# per-file guard, so one malformed file aborts the import and every cadgen command
# with it (issue #322, upstream in build123d's register_folder), as does a font with
# no family name, whose empty name OCCT refuses (#636). This hides the fonts build123d
# cannot register from that one listing.
#
# It belongs here rather than in a launcher because the `cadgen` front door, the engine's
# warm workers and `python -m cadgen.X` children all reach cadgen by different routes, and a
# fix that covered only one of them would leave the builds it spawns still broken.
#
# Cost where nothing is wrong: one str.endswith per glob call. CADGEN_FONT_GUARD=0
# opts out.
from cadgen._internal.font_scan import install_font_guard as _install_font_guard
from cadgen._internal.kernel_import_site import install as _install_kernel_import_site
from cadgen._internal.engine_telemetry import install as _install_engine_telemetry

_install_kernel_import_site()
del _install_kernel_import_site

_install_font_guard()
del _install_font_guard

# The engine counts nothing (its law 3: it imports nothing of cadgen): it tells what each of its verbs did to
# the functions in cadgen.engine.api.observers. cadgen's telemetry is one, appended here for the reason the
# font guard is installed here: every route into the engine -- `python model.py`, a `cadgen export` or
# `snapshot`, `cadgen.export()` -- comes through this package first. Appended as the engine's verbs are
# imported, never sooner: a process that uses no engine imports none of it.
_install_engine_telemetry()
del _install_engine_telemetry

from cadgen._internal.engine_host import install as _install_engine_host

_install_engine_host()
del _install_engine_host

# The engine's surface (``cadgen.engine.__all__``) and its verbs as functions (``cadgen.engine.api``).
_ENGINE = ("model", "xbf", "memo", "step", "glb", "stl", "threemf", "dxf", "Material", "materials", "paint", "configuration",
           "motion", "couple", "read_step", "read_xbf", "read_mesh", "select", "report", "track")
_VERBS = ("export", "snapshot", "serve", "inspect", "status", "stop", "daemon")

__all__ = [
    "__version__",
    *_ENGINE,
    *_VERBS,
    "build123d",
    "srgb",
]


def __getattr__(name: str):
    if name in _ENGINE:
        import cadgen.engine as _engine

        return getattr(_engine, name)
    if name in _VERBS:
        from cadgen.engine import api

        return getattr(api, name)
    if name == "build123d":
        # `from cadgen import build123d as bd` -- the lazy transparent re-export.
        # Importing the submodule is cheap; the real build123d import happens on
        # first attribute touch inside cadgen.build123d.
        import cadgen.build123d as _bd

        return _bd
    if name in {"srgb", "srgb_to_linear", "linear_to_srgb"}:
        from cadgen import color

        return getattr(color, name)
    if name == "__version__":
        return _resolve_version()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if TYPE_CHECKING:
    # Mirrors the lazy __getattr__ above so type checkers and editors see the root exports
    # without importing OCP. Every name here must have a branch there, and comes from the
    # module that defines it.
    from cadgen import build123d
    from cadgen.color import (
        srgb,
        # __getattr__ serves these two without advertising them in __all__;
        # the alias is how the mirror says "re-export" rather than "unused".
        linear_to_srgb as linear_to_srgb,
        srgb_to_linear as srgb_to_linear,
    )
    from cadgen.engine import materials
    from cadgen.engine.api import daemon, export, serve, snapshot, status, stop, inspect
    from cadgen.engine.materials import Material
    from cadgen.engine.mechanism import couple
    from cadgen.engine.model import (
        configuration,
        dxf,
        glb,
        memo,
        model,
        motion,
        paint,
        read_step,
        read_xbf,
        step,
        stl,
        threemf,
        xbf,
    )
    from cadgen.engine.progress import report, track
    from cadgen.engine.reader import read_mesh
    from cadgen.engine.refs import select


@_cache
def _resolve_version() -> str:
    """The version declared beside this very package (``_internal.version.declared_version``):
    the source tree's pyproject for a checkout, the dist-info beside it for an install.
    Never the first ``cadgen`` metadata on ``sys.path``, which can be another install's.
    Resolved once per process: the code that answers cannot change under a running one."""
    import pathlib

    from cadgen._internal.version import declared_version

    return declared_version(pathlib.Path(__file__).resolve().parent)
