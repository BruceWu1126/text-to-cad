"""EXPERIMENTAL: lazy, transparent re-export of NVIDIA's usd-brep (SMLib) kernel.

    from cadgen import usd_brep as ub
    from cadgen.usd_brep_bridge import to_build123d

The same PEP 562 proxy as ``cadgen.build123d``: the first attribute touch does
the real ``import usd_brep`` and returns the GENUINE object, so ``ub.create_box``
IS usd-brep's function. Nothing is wrapped or renamed. A model builds its
geometry with usd-brep and hands the result to ``to_build123d`` at the return,
so everything downstream of the model function (the store, the STEP writer,
SURF extraction, meshes, the viewer) is the OpenCascade pipeline it always was.

usd-brep is NOT a cadgen dependency: its wheel is CPython 3.12 only, and Linux
and Windows only (no macOS), while cadgen launches on 3.13 everywhere. Run an
experiment in an environment that has both, for example::

    uv run --python 3.12 --with usd-brep==1.0.0 --with-editable packages/cadgen python model.py
"""

from __future__ import annotations

from typing import Any

_REAL = None


def _real_module():
    global _REAL
    if _REAL is None:
        try:
            import usd_brep as _usd_brep
        except ImportError as exc:
            raise ImportError(
                "cadgen.usd_brep needs NVIDIA's usd-brep in this environment "
                "(pip install usd-brep; CPython 3.12 on Linux or Windows only)"
            ) from exc
        _REAL = _usd_brep
    return _REAL


def __getattr__(name: str) -> Any:
    if name.startswith("__") and name.endswith("__"):
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    real = _real_module()
    try:
        value = getattr(real, name)
    except AttributeError:
        raise AttributeError(f"module 'usd_brep' has no attribute {name!r}", name=name, obj=real) from None
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_real_module())))
