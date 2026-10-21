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

__all__ = ["Memo", "Model", "Output", "configuration", "glb", "memo", "paint", "read_step", "step", "stl", "threemf",
           "xbf", "models_in"]

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


# --- @memo: a function worth replaying -------------------------------------------------


class Memo:
    """A function marked ``@memo``: one the engine may replay instead of executing.

    Called inside a build, a call is a body (``kernel/memo.py``, ``body``): keyed by the
    code it reaches (``sources.Symbols``, as a model's body is), the files it reads, and
    its arguments by value (``memo.call_key``), its kernel calls logged, and on a later
    build with the same key replayed without running -- the trace, so every element name,
    is what a run leaves. A second call with the same arguments in one build is a new
    placement of the first's result, as a child model's is. A call whose arguments cannot
    be keyed, or that returns no geometry, runs every time, and the build's report says
    which function and why (``retention.fallbacks``). Called outside a build, it is its
    function. It writes no file and declares none."""

    def __init__(self, function: Callable[..., Any]) -> None:
        if not inspect.isfunction(function):
            raise TypeError("@memo takes a function")
        parameters = list(inspect.signature(function).parameters)
        if parameters and parameters[0] in ("self", "cls"):
            raise TypeError(f"@memo on {function.__qualname__!r}: a method is not markable (its first parameter "
                            f"is {parameters[0]}); mark a function of the values it needs")
        functools.update_wrapper(self, function)
        self.function = function
        self.name = function.__qualname__
        source = inspect.getsourcefile(function) or inspect.getfile(function)
        self.script = Path(source).resolve()
        self.__xbf_memo__ = True

    @property
    def identity(self) -> str:
        return f"{self.script.name}::{self.name}"

    def __repr__(self) -> str:
        return f"<memo {self.identity}>"

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        build = getattr(_CONTEXT, "build", None)
        if build is None:
            return self.function(*args, **kwargs)
        return build.memo(self, args, kwargs)


def memo(function: Any = None, /, **options: Any) -> Any:
    """Mark a function worth replaying: ``@memo``, bare. Its arguments are keyed by value
    (plain values, build123d's value types, materials, shapes the engine made); it returns
    geometry (a shape, or a tuple, list or dict of shapes); it writes nothing outside
    itself. ``cadgen xbf build --verify`` runs every call the engine would replay and
    compares what it makes with what was logged."""
    if options or function is None:
        raise TypeError("@memo takes no arguments: write @memo")
    if isinstance(function, Model):
        raise TypeError(f"@memo on model {function.name!r}: a model is already replayed; @memo marks a function")
    return Memo(function)


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
    """Colour faces of a part, or give them a material: ``faces`` (a face, or several) of
    ``shape`` take ``color`` -- anything ``build123d.Color`` takes, or a ``Material`` --
    in the document, by their ordinal in the part. A face holds one paint: a colour
    replaces its material, a material its colour. Returns ``shape``, whose paints the
    memo keys like its label.

    A part's material is set as its colour is, on the shape: ``part.material =
    materials.steel``; on an assembly, it is every part's below it that has none. Both
    are attributes of the object, and travel as build123d moves them: a copy, ``moved``
    and ``located`` keep them, and a boolean's result takes those of its first operand
    where its class holds the field (a ``Part`` does, a bare ``Solid`` has no
    ``material``). Paint and materials are for finished parts: set them after the last
    operation, so an edit of one is no kernel call (the memo keys them with the shape,
    and a compound does not hold its children's materials in its key)."""
    import build123d as bd
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape

    from cadgen.engine.kernel.memo import FACE_COLORS, FACE_MATERIALS
    from cadgen.engine.materials import Material

    material = color if isinstance(color, Material) else None
    rgba = None if material is not None else tuple(
        float(value) for value in (color if isinstance(color, bd.Color) else bd.Color(color)))
    mapped = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape.wrapped, TopAbs_FACE, mapped)
    painted = dict(shape.__dict__.get(FACE_COLORS) or {})
    given = dict(shape.__dict__.get(FACE_MATERIALS) or {})
    for face in ([faces] if isinstance(faces, bd.Shape) else list(faces)):
        ordinal = mapped.FindIndex(face.wrapped)
        if not ordinal:
            raise ValueError("paint: a face that is not one of the shape's")
        if material is not None:
            given[ordinal] = material
            painted.pop(ordinal, None)
        else:
            painted[ordinal] = rgba
            given.pop(ordinal, None)
    shape.__dict__[FACE_COLORS] = painted
    if given:
        shape.__dict__[FACE_MATERIALS] = given
    else:
        shape.__dict__.pop(FACE_MATERIALS, None)
    return shape


def configuration(assembly: Any, name: str, **values: Any) -> Any:
    """Name a configuration of an assembly's mechanism: ``configuration(arm, "raised",
    elbow=90)`` sets the joint labelled ``elbow`` to 90 degrees in the configuration
    ``raised`` (a number per joint, or for a joint of several values a sequence: a
    cylindrical joint's angle and position, a ball joint's three angles). The joints are
    build123d's, placed with ``connect_to``; the values they were connected at are the
    as-built configuration, and a joint a configuration does not name stays as built. The
    document keeps every configuration (AP242's mechanism states); the viewer offers them
    as presets, ``--pose NAME`` poses a snapshot or an export at one. Returns ``assembly``."""
    from cadgen.engine import mechanism

    return mechanism.configuration(assembly, name, **values)


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
    call. What a second call of a child model in one build hands back.

    What a model may change in place is each placement's own: every dict and list it holds
    is copied (a joint added to one placement is not on another), and its joints are new
    joints on the new objects, a joint connected to one in the same placement connected to
    that one's copy."""
    copied: dict[int, Any] = {}
    made = _instance(shape, copied)
    for joint in copied.values():
        other = joint.connected_to
        if other is not None and id(other) in copied:
            joint.connected_to = copied[id(other)]
    return made


def _instance(shape: Any, copied: dict[int, Any]) -> Any:
    import copy

    cls = type(shape)
    made = cls.__new__(cls)
    attrs = dict(shape.__dict__)
    children = attrs.pop("_NodeMixin__children", None)
    attrs.pop("_NodeMixin__parent", None)
    wrapped = attrs.get("_wrapped")
    if wrapped is not None:
        attrs["_wrapped"] = wrapped.Located(wrapped.Location())
    attrs = {name: dict(value) if type(value) is dict else list(value) if type(value) is list else value
             for name, value in attrs.items()}
    joints = attrs.get("joints")
    if joints:
        from build123d import Joint

        for label, joint in joints.items():
            if isinstance(joint, Joint):
                joints[label] = copied[id(joint)] = copy.copy(joint)
                joints[label].parent = made
    made.__dict__.update(attrs)
    if children:
        copies = [_instance(child, copied) for child in children]
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
        self.resolved: dict[str, list] = {}
        # The first-party modules the script's load imported (``build`` sets it), the
        # build's symbol resolution, the time keys took and each body's fallbacks.
        self.loaded: set[str] | None = None
        self.symbols: Any = None
        self.key_seconds = 0.0
        self.fallbacks: dict[str, list[str]] = {}

    def source(self, model: Model) -> str:
        """What a model's body is made of: the symbols its function reaches, a module's
        bytes where one cannot be followed (``sources.Symbols``), and the engine's own
        code; worked out once a build."""
        key = str(model.script) + "::" + model.name
        found = self.sources.get(key)
        if found is None:
            import time

            from cadgen.engine.sources import Symbols

            started = time.perf_counter()
            if self.symbols is None:
                self.symbols = Symbols(self.resolved, self.loaded)
            digest, reasons = self.symbols.key(model.function)
            if reasons:
                self.fallbacks[model.identity] = reasons
            found = self.sources[key] = digest + _engine_version()
            self.key_seconds += time.perf_counter() - started
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
                result = memo.body(key, lambda: self.source(model), model.function)
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

    def memo(self, marked: Memo, args: tuple, kwargs: dict) -> Any:
        """A ``@memo`` call: a body named by the function and its arguments' digest, run or
        replayed (``memo.body``); the same call again in this build a new placement of its
        result. Arguments the memo cannot key, or a result that is no geometry, run it on
        every call, and the report's fallbacks say why."""
        import time

        from cadgen.engine.kernel import memo as kernel

        family = str(marked.script) + "::" + marked.name
        try:
            digest, inputs = kernel.call_key(args, kwargs)
        except kernel.Unkeyable as error:
            self._fallback(marked, f"runs on every call: {error}")
            return marked.function(*args, **kwargs)
        name = f"{family}({digest})" if args or kwargs else family
        found = self.results.get(name)
        if found is not None:
            kernel.used(name)  # the calling body's replay needs this one's run
            return _placement(found[1])
        if name in self.stack:
            raise RecursionError(f"{marked.identity} calls itself with the same arguments")
        self.stack.append(name)
        started = time.perf_counter()
        try:
            with kernel.scope(name):
                result = kernel.body(name, lambda: self.source(marked), lambda: marked.function(*args, **kwargs),
                                     inputs=inputs, family=family)
        finally:
            self.stack.pop()
        self.timings[marked.identity] = self.timings.get(marked.identity, 0.0) + time.perf_counter() - started
        if not kernel.geometric(result):
            kernel.direct(name)
            self._fallback(marked, f"runs on every call: it returned {type(result).__name__}, not geometry "
                                   "(a shape, or a tuple, list or dict of shapes)")
            return result
        self.results[name] = (result, _placement(result))
        return result

    def _fallback(self, marked: Memo, reason: str) -> None:
        reasons = self.fallbacks.setdefault(marked.identity, [])
        if reason not in reasons:
            reasons.append(reason)
        self.timings.setdefault(marked.identity, 0.0)


def _placement(value: Any) -> Any:
    """A new placement of a ``@memo`` result: ``instance`` of each shape it holds."""
    if isinstance(value, (list, tuple)):
        return type(value)(_placement(item) for item in value)
    if isinstance(value, dict):
        return {name: _placement(item) for name, item in value.items()}
    return instance(value)


def check_result(model: Model, result: Any) -> None:
    import build123d as bd

    if not isinstance(result, bd.Shape):
        raise TypeError(f"model {model.identity} returned {type(result).__name__}; a model returns a build123d shape")
    if result.__dict__.get("_wrapped") is None:
        raise ValueError(f"model {model.identity} returned an empty shape")
