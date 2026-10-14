"""Load a cadgen model program on a kernel that isn't cadgen's.

A model is a plain Python script with a decorated function (``@step``,
``@stl``, ...) returning a build123d shape. On another kernel the script still
runs as the program it is; only two names change underneath it:

- ``build123d`` (and ``cadgen.build123d``) is the kernel's build123d-shaped
  module: a recorder that writes a CSG program, keel's usd-brep layer, ...
- the ``cadgen`` decorators mark the model and hand the function back
  unchanged, so calling it runs the body and returns the shape. Nothing is
  written to cadgen's store.

Anything else a model reaches for in cadgen (``read_step``, assemblies) is
reported as unsupported on alternate kernels, by name.

Stdlib only: runners import this from foreign interpreters (keel's Python,
a kernel's own venv).
"""
from __future__ import annotations

import importlib.abc
import importlib.util
import sys
import types
from dataclasses import dataclass
from pathlib import Path

DECORATORS = {"step": "step", "dxf": "dxf", "stl": "stl", "glb": "glb", "threemf": "3mf"}


class Unsupported(Exception):
    """The model uses something an alternate kernel can't run."""


@dataclass(frozen=True)
class Model:
    script: Path
    function: str
    call: object
    outs: dict  # format -> resolved Path


def srgb_to_linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def linear_to_srgb(channel: float) -> float:
    return channel * 12.92 if channel <= 0.0031308 else 1.055 * channel ** (1 / 2.4) - 0.055


def srgb(value: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    """cadgen.srgb: the linear RGBA channels for an sRGB hex. Palette modules
    call it at import and do arithmetic on the result, so it has to be real."""
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        raise ValueError(f"expected a #rgb or #rrggbb colour, got {value!r}")
    red, green, blue = (int(text[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return srgb_to_linear(red), srgb_to_linear(green), srgb_to_linear(blue), float(alpha)


def _mark(fmt):
    def factory(func=None, *, out=None, **_ignored):
        def apply(fn):
            outs = dict(getattr(fn, "__kernel_model__", {}))
            outs.setdefault(fmt, out)
            fn.__kernel_model__ = outs
            return fn

        return apply(func) if func is not None else apply

    return factory


class _CadgenSubmodules(importlib.abc.MetaPathFinder):
    """``import cadgen.<anything>`` fails with a teaching error, not a bare
    ModuleNotFoundError."""

    def find_spec(self, name, path=None, target=None):
        if name.startswith("cadgen.") and name != "cadgen.build123d":
            raise Unsupported(f"{name} is cadgen's own; alternate kernels run the build123d geometry only")
        return None


def install(build123d_module) -> None:
    """Make ``cadgen`` and ``build123d`` resolve to this kernel's modules."""
    cadgen = types.ModuleType("cadgen")
    cadgen.__path__ = []
    cadgen.build123d = build123d_module
    for name, fmt in DECORATORS.items():
        setattr(cadgen, name, _mark(fmt))
    cadgen.srgb, cadgen.srgb_to_linear, cadgen.linear_to_srgb = srgb, srgb_to_linear, linear_to_srgb

    def missing(name):
        if name.startswith("__"):
            raise AttributeError(name)
        raise Unsupported(f"cadgen.{name} is cadgen's own; alternate kernels run the build123d geometry only")

    cadgen.__getattr__ = missing
    sys.modules["cadgen"] = cadgen
    sys.modules["cadgen.build123d"] = build123d_module
    sys.modules["build123d"] = build123d_module
    if not any(isinstance(finder, _CadgenSubmodules) for finder in sys.meta_path):
        sys.meta_path.insert(0, _CadgenSubmodules())


def load(script, function: str | None = None) -> Model:
    """Import a model script (not as __main__, so it builds nothing on import)
    and pick its model: the one named, else the file's only one."""
    script = Path(script).resolve()
    # The import path is python script.py's: the script's folder first.
    sys.path.insert(0, str(script.parent))
    spec = importlib.util.spec_from_file_location(f"_kernel_model_{script.stem}", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    models = {name: fn for name, fn in vars(module).items()
              if callable(fn) and hasattr(fn, "__kernel_model__") and getattr(fn, "__module__", None) == module.__name__}
    if function:
        if function not in models:
            raise Unsupported(f"{script.name} has no model named {function}; it has {', '.join(sorted(models)) or 'none'}")
        name = function
    elif len(models) == 1:
        (name,) = models
    elif not models:
        raise Unsupported(f"{script.name} declares no model (a function decorated with @step, @stl, ...)")
    else:
        raise Unsupported(f"{script.name} declares {len(models)} models; name one: {script.name}::{sorted(models)[0]}")
    fn = models[name]
    outs = {fmt: (script.parent / out if out else script.parent / f"{script.stem}.{fmt}").resolve()
            for fmt, out in fn.__kernel_model__.items()}
    return Model(script=script, function=name, call=fn, outs=outs)


def shape_of(result):
    """The shape a model returned, unwrapping builder contexts."""
    part = getattr(result, "part", None)
    if part is not None and not callable(part):
        return part
    return result


def split_target(target: str):
    """``model.py::function`` -> (path, function or None)."""
    path, _, function = str(target).partition("::")
    return path, function or None


__all__ = ["Model", "Unsupported", "install", "load", "shape_of", "split_target"]
