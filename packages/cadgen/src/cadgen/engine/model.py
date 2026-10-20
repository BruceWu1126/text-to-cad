"""Models: the decorators, a model's identity, and what calling one does.

A model is a parameterless function that returns a build123d shape, decorated with
``@xbf`` and any of ``@step``, ``@glb``, ``@stl``, ``@threemf``. Its identity is
``script.py::function``. Each decorator names one output the model declares; the
outputs are kept in the order the decorators are written, top to bottom, and the
xbf, when declared, is always written first.

Called inside a build, a model is a child: its body runs in the same run, with the
same memo, and its result is a sub-assembly of whatever the caller makes of it,
labelled with the child's function name when its body gave it no label. A model is
a pure function of its source, so a second call in one build hands back a new
placement of the same result -- new objects sharing the first call's geometry --
rather than running the body again: a child used four times is one prototype placed
four times. Called outside a build, a model is its function.
"""

from __future__ import annotations

import functools
import inspect
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

__all__ = ["Model", "Output", "glb", "paint", "read_step", "step", "stl", "threemf", "xbf", "models_in"]

# The suffix each output kind writes, and the default file name's.
SUFFIXES = {"xbf": ".xbf", "step": ".step", "glb": ".glb", "stl": ".stl", "threemf": ".3mf"}
_ACCEPTED = {"xbf": (".xbf",), "step": (".step", ".stp"), "glb": (".glb",), "stl": (".stl",), "threemf": (".3mf",)}

_CONTEXT = threading.local()


@dataclass(frozen=True)
class Output:
    """One file a model declares: its kind and the path its decorator gave, if any."""

    kind: str
    out: str | None


class Model:
    """A decorated function: the body, the script it lives in, the outputs it declares."""

    def __init__(self, function: Callable[[], Any]) -> None:
        if not callable(function) or not inspect.isfunction(function):
            raise TypeError("a model decorator takes a function")
        parameters = [p for p in inspect.signature(function).parameters.values()
                      if p.default is inspect.Parameter.empty and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)]
        if parameters:
            raise TypeError(f"model {function.__name__!r} takes parameters; a model takes none")
        functools.update_wrapper(self, function)
        self.function = function
        self.name = function.__name__
        source = inspect.getsourcefile(function) or inspect.getfile(function)
        self.script = Path(source).resolve()
        self.outputs: list[Output] = []
        self.__xbf_model__ = True

    @property
    def identity(self) -> str:
        return f"{self.script.name}::{self.name}"

    def __repr__(self) -> str:
        return f"<model {self.identity}>"

    def __call__(self) -> Any:
        build = getattr(_CONTEXT, "build", None)
        if build is None:
            return self.function()
        return build.child(self)

    def targets(self) -> list[tuple[str, Path]]:
        """Each declared output as ``(kind, path)``, the xbf first, then the exports in the
        order the decorators are written; a relative path is relative to the script's
        folder, and an output with no path is ``<function><suffix>`` beside the script."""
        found = []
        for output in sorted(self.outputs, key=lambda output: output.kind != "xbf"):
            path = Path(output.out) if output.out is not None else Path(self.name + SUFFIXES[output.kind])
            if not path.is_absolute():
                path = self.script.parent / path
            found.append((output.kind, Path(_normalized(path))))
        return found


def _normalized(path: Path) -> str:
    import os

    return os.path.normpath(os.path.abspath(path))


def _decorator(kind: str) -> Callable[..., Any]:
    def decorate(function: Any = None, *, out: str | None = None) -> Any:
        if out is not None:
            if not isinstance(out, str) or not out:
                raise TypeError(f"@{kind}(out=...) takes a path")
            if Path(out).suffix.lower() not in _ACCEPTED[kind]:
                raise ValueError(f"@{kind}(out={out!r}): the file must end in {' or '.join(_ACCEPTED[kind])}")

        def apply(target: Any) -> Model:
            model = target if isinstance(target, Model) else Model(target)
            if any(output.kind == kind for output in model.outputs):
                raise TypeError(f"model {model.name!r} declares @{kind} twice")
            # Decorators apply bottom-up: inserting at the front keeps source order.
            model.outputs.insert(0, Output(kind, out))
            return model

        if function is None:
            return apply
        if callable(function):
            return apply(function)
        raise TypeError(f"@{kind} takes a function, or out=")

    decorate.__name__ = kind
    decorate.__qualname__ = kind
    decorate.__doc__ = (f"Declare a model's {kind} output: ``@{kind}`` or ``@{kind}(out=\"path{SUFFIXES[kind]}\")``. "
                        "The function takes no parameters and returns a build123d shape.")
    return decorate


xbf = _decorator("xbf")
step = _decorator("step")
glb = _decorator("glb")
stl = _decorator("stl")
threemf = _decorator("threemf")


def models_in(namespace: dict[str, Any], script: Path) -> list[Model]:
    """The models a script's module defines, in definition order: those whose function
    lives in the script itself (a model it imports is a child, not one of its own)."""
    script = Path(script).resolve()
    found = []
    for value in namespace.values():
        if isinstance(value, Model) and value.script == script and value not in found:
            found.append(value)
    return found


# --- what a model's code may call --------------------------------------------------------


def paint(shape: Any, faces: Any, color: Any) -> Any:
    """Colour faces of a part: ``faces`` (a face, or several) of ``shape`` take ``color``
    (anything ``build123d.Color`` takes) in the document, by their ordinal in the part.
    Returns ``shape``, whose face colours the memo keys like its label."""
    import build123d as bd
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape

    from cadgen.engine.kernel.memo import FACE_COLORS

    rgba = tuple(float(value) for value in (color if isinstance(color, bd.Color) else bd.Color(color)))
    mapped = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape.wrapped, TopAbs_FACE, mapped)
    painted = dict(shape.__dict__.get(FACE_COLORS) or {})
    for face in ([faces] if isinstance(faces, bd.Shape) else list(faces)):
        ordinal = mapped.FindIndex(face.wrapped)
        if not ordinal:
            raise ValueError("paint: a face that is not one of the shape's")
        painted[ordinal] = rgba
    shape.__dict__[FACE_COLORS] = painted
    return shape


def _read_step(path: Any) -> Any:
    from cadgen.engine.document import read_step_shape

    return read_step_shape(path)


_READ_STEP: Callable[[Any], Any] | None = None


def read_step(path: Any) -> Any:
    """A STEP file as a build123d shape: a compound tree of its assembly, every part and
    group labelled and coloured as the file has them. Parsed once per worker and held:
    a later build's read of the same bytes is the memo's hit, the same objects."""
    global _READ_STEP
    if _READ_STEP is None:
        from cadgen.engine.kernel.memo import wrap_function

        _READ_STEP = wrap_function("read_step", _read_step)
    import os

    return _READ_STEP(os.fspath(path))


# --- a build's context -------------------------------------------------------------------


def instance(shape: Any) -> Any:
    """A new placement of ``shape``: new objects, of the same classes and attributes,
    sharing its geometry -- its TopoDS handle, and its children's -- with no kernel
    call. What a second call of a child model in one build hands back."""
    cls = type(shape)
    made = cls.__new__(cls)
    attrs = dict(shape.__dict__)
    children = attrs.pop("_NodeMixin__children", None)
    attrs.pop("_NodeMixin__parent", None)
    wrapped = attrs.get("_wrapped")
    if wrapped is not None:
        attrs["_wrapped"] = wrapped.Located(wrapped.Location())
    made.__dict__.update(attrs)
    if children:
        copies = [instance(child) for child in children]
        for child in copies:
            child.__dict__["_NodeMixin__parent"] = made
        made.__dict__["_NodeMixin__children"] = copies
    return made


@functools.cache
def _engine_version() -> str:
    """The engine's own code, by its bytes: a body replayed is one this engine ran."""
    from cadgen.engine.sources import digest

    here = Path(__file__).resolve().parent
    return ":" + "".join(digest(path)[:12] for path in sorted([*here.glob("*.py"), *here.glob("kernel/*.py")]))


class BuildContext:
    """The models a build runs on this thread: the children each body calls, built once
    and placed again, and how long each took."""

    def __init__(self) -> None:
        self.results: dict[str, tuple[Any, Any]] = {}
        self.timings: dict[str, float] = {}
        self.stack: list[str] = []
        self.sources: dict[str, str] = {}

    def source(self, model: Model) -> str:
        """What a model's body is made of: its code's bytes (``sources.closure``) and the
        engine's own, read once a build."""
        key = str(model.script) + "::" + model.function.__module__
        found = self.sources.get(key)
        if found is None:
            from cadgen.engine.sources import closure

            found = self.sources[key] = closure(model.script, model.function.__module__) + _engine_version()
        return found

    def __enter__(self) -> "BuildContext":
        self.previous = getattr(_CONTEXT, "build", None)
        _CONTEXT.build = self
        return self

    def __exit__(self, *exc: object) -> None:
        _CONTEXT.build = self.previous

    def run(self, model: Model) -> Any:
        """A model's body, as the build's root or as a child."""
        import time

        key = str(model.script) + "::" + model.name
        if key in self.stack:
            raise RecursionError(f"model {model.identity} calls itself")
        from cadgen.engine.kernel import memo

        self.stack.append(key)
        started = time.perf_counter()
        try:
            with memo.scope(key):
                # One coarse entry above the body's calls: not called when the bytes it is
                # made of are what they were (``memo.body``).
                result = memo.body(key, self.source(model), model.function)
        finally:
            self.stack.pop()
        self.timings[model.identity] = self.timings.get(model.identity, 0.0) + time.perf_counter() - started
        check_result(model, result)
        return result

    def root(self, model: Model) -> Any:
        """A model the build was asked for: its body, kept for a later model of the same
        build that calls it as a child."""
        result = self.run(model)
        self.results.setdefault(str(model.script) + "::" + model.name, (result, instance(result)))
        return result

    def child(self, model: Model) -> Any:
        key = str(model.script) + "::" + model.name
        found = self.results.get(key)
        if found is not None:
            from cadgen.engine.kernel import memo

            memo.used(key)  # the calling body's replay needs this one's run
            made = instance(found[1])
        else:
            made = self.run(model)
            self.results[key] = (made, instance(made))
        if not made.__dict__.get("label"):
            made.label = model.name
        return made


def check_result(model: Model, result: Any) -> None:
    import build123d as bd

    if not isinstance(result, bd.Shape):
        raise TypeError(f"model {model.identity} returned {type(result).__name__}; a model returns a build123d shape")
    if result.__dict__.get("_wrapped") is None:
        raise ValueError(f"model {model.identity} returned an empty shape")
