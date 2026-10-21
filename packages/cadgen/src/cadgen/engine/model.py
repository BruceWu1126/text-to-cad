"""Models: the decorators, a model's identity, and what calling one does.

A model is a parameterless function that returns a build123d shape, decorated with
``@xbf`` and any of the derived-file decorators ``@step``, ``@glb``, ``@stl``,
``@threemf`` and ``@snapshot``. Its identity is ``script.py::function``. ``@xbf(out=None)``
makes the function a model, its document at ``out`` (``XBF/<function>.xbf`` beside the
script by default): its only argument. A derived-file decorator IS its verb, run on the
built document: it takes ``out`` (``<function><suffix>`` beside the script by default) and
exactly the options the verb takes -- ``@step(out, pose=None, part=None)``, ``@glb(out,
pose=None, part=None, animate=None)``, ``@stl``/``@threemf`` as ``@step``, and
``@snapshot(out, display=, camera=, grid=, preview=, pose=, width=, height=)``. A model's
material, colours, joints and configurations are written in its body, never in a
decorator. The outputs are kept in the order the decorators are written, top to bottom,
and the xbf, when declared, is always written first.

The decorated function is a ``Model``: ``.build()``, ``.export(*destinations)``,
``.snapshot(out)`` and ``.document`` run the engine's verbs on it, through the daemon.
Run as a program (``python model.py [flags]``), a model script asks the daemon to build
it, as ``cadgen xbf build model.py [flags]`` does: the script's own process builds
nothing, and exits with the build's status.

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
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

__all__ = ["KINDS", "Model", "Output", "Target", "configuration", "glb", "paint", "read_step", "snapshot", "step",
           "stl", "threemf", "xbf", "models_in"]

# The suffix each output kind writes, and the default file name's.
SUFFIXES = {"xbf": ".xbf", "step": ".step", "glb": ".glb", "stl": ".stl", "threemf": ".3mf", "snapshot": ".png"}
_ACCEPTED = {"xbf": (".xbf",), "step": (".step", ".stp"), "glb": (".glb",), "stl": (".stl",), "threemf": (".3mf",),
             "snapshot": (".png",)}
# The options each decorator takes: exactly its verb's. The xbf takes none but ``out``.
OPTIONS: dict[str, tuple[str, ...]] = {
    "xbf": (), "step": ("pose", "part"), "glb": ("pose", "part", "animate"), "stl": ("pose", "part"),
    "threemf": ("pose", "part"),
    "snapshot": ("display", "camera", "grid", "preview", "pose", "width", "height"),
}
KINDS = tuple(OPTIONS)
# Where a model's xbf is when its decorator names none: this folder beside the script.
XBF_FOLDER = "XBF"

_CONTEXT = threading.local()


@dataclass(frozen=True)
class Output:
    """One file a model declares: its kind, the path its decorator gave, if any, and the
    options it gave (``(name, value)`` pairs, the verb's)."""

    kind: str
    out: str | None
    options: tuple[tuple[str, Any], ...] = ()


@dataclass(frozen=True)
class Target:
    """One file a build writes: its kind, its path, and the options its writer runs with."""

    kind: str
    path: Path
    options: dict[str, Any]


def check_output(kind: str, out: Any, where: str) -> None:
    """A path a decorator or a flag gives for ``kind``: a string ending in the kind's suffix."""
    if out is None:
        return
    if not isinstance(out, (str, os.PathLike)) or not os.fspath(out):
        raise TypeError(f"{where} takes a path")
    if Path(out).suffix.lower() not in _ACCEPTED[kind]:
        raise ValueError(f"{where}: the file must end in {' or '.join(_ACCEPTED[kind])}, not {os.fspath(out)!r}")


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
        if getattr(function, "__module__", None) == "__main__":
            _run_as_program(self.script)

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

    def _path(self, kind: str, out: Any) -> Path:
        if out is None:
            out = Path(XBF_FOLDER) / f"{self.name}.xbf" if kind == "xbf" else Path(self.name + SUFFIXES[kind])
        path = Path(out)
        if not path.is_absolute():
            path = self.script.parent / path
        return Path(_normalized(path))

    def targets(self) -> list[tuple[str, Path]]:
        """Each declared output as ``(kind, path)``, the xbf first, then the derived files
        in the order the decorators are written; a relative path is relative to the
        script's folder, and an output with no path is its default (``XBF/<function>.xbf``,
        ``<function><suffix>``) beside the script."""
        return [(target.kind, target.path) for target in self.plan()]

    def plan(self, outputs: dict[str, dict[str, Any]] | None = None, *, pose: Any = None,
             part: str | None = None, cwd: Path | None = None) -> list[Target]:
        """The files a build of this model writes, the xbf first. ``outputs`` (``{kind:
        {"out": path or None, option: value}}``) selects and overrides: only the kinds it
        names are written, each at its path (relative to ``cwd``), else the declared one,
        else the default, with the declared options under the ones it gives; a kind given
        None is written only where the model declares it, and ``{}`` writes nothing.
        ``outputs`` None is what the decorators declare. ``pose`` and ``part`` apply to every
        file whose verb takes them."""
        declared = {output.kind: output for output in self.outputs}
        if outputs is not None:
            unknown = sorted(set(outputs) - set(KINDS))
            if unknown:
                raise ValueError(f"no output kind {', '.join(unknown)}; the kinds are {', '.join(KINDS)}")
            # A kind given None is written only where the model declares it.
            order = [output.kind for output in self.outputs if output.kind in outputs]
            order += [kind for kind in KINDS if outputs.get(kind) is not None and kind not in order]
        else:
            order = [output.kind for output in self.outputs]
        found = []
        for kind in sorted(order, key=lambda kind: kind != "xbf"):
            output = declared.get(kind)
            options = dict(output.options) if output is not None else {}
            given = dict((outputs or {}).get(kind) or {})
            out = given.pop("out", None)
            unknown = sorted(set(given) - set(OPTIONS[kind]))
            if unknown:
                raise ValueError(f"{kind} takes no {', '.join(unknown)}; it takes out"
                                 + "".join(f", {name}" for name in OPTIONS[kind]))
            options.update({name: value for name, value in given.items() if value is not None})
            for name, value in (("pose", pose), ("part", part)):
                if value is not None and name in OPTIONS[kind]:
                    options[name] = value
            if out is not None:
                check_output(kind, out, f"--{kind}")
                path = Path(out)
                path = Path(_normalized(path if path.is_absolute() else (cwd or Path.cwd()) / path))
            else:
                path = self._path(kind, output.out if output is not None else None)
            found.append(Target(kind, path, options))
        return found

    # --- the verbs, through the daemon (``cadgen.engine.api``) -----------------------------

    def build(self, **flags: Any) -> Any:
        """Build this model (``cadgen xbf build script.py::model``); ``flags`` are the
        build's (``xbf=``, ``step=``, ..., ``pose=``, ``part=``)."""
        from cadgen.engine import api

        return api.build(self, **flags)

    def export(self, *destinations: Any, pose: Any = None, part: str | None = None, animate: Any = None) -> Any:
        """Build this model, then write its document to each destination (by suffix)."""
        from cadgen.engine import api

        return api.export(self, *destinations, pose=pose, part=part, animate=animate)

    def snapshot(self, out: Any, **options: Any) -> Any:
        """Build this model, then draw its document into ``out`` (a ``.png``) as the viewer does."""
        from cadgen.engine import api

        return api.snapshot(self, out, **options)

    @property
    def document(self) -> Any:
        """The model's built document, read-only (``document.Loaded``): its xbf as built
        last. A model that declares no xbf has no document on disk to read."""
        from cadgen.engine import document

        found = next((target.path for target in self.plan() if target.kind == "xbf"), None)
        if found is None:
            raise AttributeError(f"model {self.identity} declares no @xbf: it has no document to read")
        if not found.is_file():
            raise FileNotFoundError(f"model {self.identity} is not built yet: {found} is missing (.build() it)")
        return document.load(found)


def _normalized(path: Path) -> str:
    return os.path.normpath(os.path.abspath(path))


def _run_as_program(script: Path) -> None:
    """``python model.py [flags]``: the daemon builds the script, as ``cadgen xbf build
    model.py [flags]`` does (``::name`` in the flags selects a model), and this process
    exits with the build's status, building nothing itself."""
    if getattr(_run_as_program, "running", False) or os.environ.get("CADGEN_ENGINE_WORKER") == "1":
        return
    import sys

    argv0 = Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0] else None
    if argv0 != Path(script).resolve():
        return  # the script was not run as a program (``python -c``, an embedding)
    _run_as_program.running = True  # type: ignore[attr-defined]
    from cadgen.engine.cli import main

    args = [f"{script}{arg}" if arg.startswith("::") else arg for arg in sys.argv[1:]]
    targets = [arg for arg in args if arg.startswith(f"{script}::")]
    rest = [arg for arg in args if arg not in targets]
    raise SystemExit(main(["build", *(targets or [str(script)]), *rest], prog=f"python {Path(script).name}"))


def _decorator(kind: str) -> Callable[..., Any]:
    names = OPTIONS[kind]

    def decorate(function: Any = None, out: Any = None, **options: Any) -> Any:
        if isinstance(function, (str, os.PathLike)):
            function, out = None, function  # ``@step("arm.step")``
        unknown = sorted(set(options) - set(names))
        if unknown:
            raise TypeError(f"@{kind} takes no {', '.join(unknown)}; it takes out"
                            + "".join(f", {name}" for name in names)
                            + (". A model's material, colours and joints are written in its body." if kind != "xbf"
                               else " alone. A model's material, colours and joints are written in its body."))
        check_output(kind, out, f"@{kind}(out=...)")
        if options.get("animate") is not None:
            raise NotImplementedError("@glb(animate=...): GLB animation is not built yet")
        given = tuple((name, options[name]) for name in names if options.get(name) is not None)

        def apply(target: Any) -> Model:
            model = target if isinstance(target, Model) else Model(target)
            if any(output.kind == kind for output in model.outputs):
                raise TypeError(f"model {model.name!r} declares @{kind} twice")
            # Decorators apply bottom-up: inserting at the front keeps source order.
            model.outputs.insert(0, Output(kind, None if out is None else os.fspath(out), given))
            return model

        if function is None:
            return apply
        if callable(function):
            return apply(function)
        raise TypeError(f"@{kind} takes a function, or out=")

    decorate.__name__ = kind
    decorate.__qualname__ = kind
    if kind == "xbf":
        decorate.__doc__ = ("Make a function a model: ``@xbf`` or ``@xbf(out=\"path.xbf\")`` (default "
                            "``XBF/<function>.xbf`` beside the script). The function takes no parameters and "
                            "returns a build123d shape.")
    else:
        decorate.__doc__ = (f"Declare a file derived from a model's document: ``@{kind}``, ``@{kind}(\"path"
                            f"{SUFFIXES[kind]}\")`` or ``@{kind}(out=..., {', '.join(f'{n}=...' for n in names)})``"
                            f" -- the {kind} verb run on the built document, with its options.")
    return decorate


xbf = _decorator("xbf")
step = _decorator("step")
glb = _decorator("glb")
stl = _decorator("stl")
threemf = _decorator("threemf")
snapshot = _decorator("snapshot")


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


def check_result(model: Model, result: Any) -> None:
    import build123d as bd

    if not isinstance(result, bd.Shape):
        raise TypeError(f"model {model.identity} returned {type(result).__name__}; a model returns a build123d shape")
    if result.__dict__.get("_wrapped") is None:
        raise ValueError(f"model {model.identity} returned an empty shape")
