"""Where a build is: the author's ``report`` and ``track``, and the engine's stages, one frame each.

    from cadgen.engine import report, track, xbf

    @xbf
    def hand():
        fingers = [finger(name) for name in track(FINGERS)]  # "Building geometry  2/5  middle"
        report("palm loft")                                   # "Building geometry  palm loft"
        return bd.Compound(children=[palm(), *fingers], label="hand")

``track(items, label=)`` iterates ``items`` and counts them: the count moves when an item's work
is done, and the detail names the item in flight (``label(item)``, or the item itself when it is a
string). A sized iterable gives the count its total; a lazy one is passed through lazily, counted
without one. A ``track`` inside another counts on its own while it runs. ``report(detail)`` names
what the build is doing now, in place of whatever was showing. Outside a build both do nothing:
``track`` yields the items, ``report`` returns. A body the memo replays is not run, so it says
nothing: it takes no time.

A build's progress is a sequence of frames, each the whole of where the build is::

    {"model": "hand.py::hand", "outputs": ["/abs/XBF/hand.xbf", ...], "phase": "body",
     "label": "Building geometry", "detail": "middle", "done": 2, "total": 5, "determinate": true,
     "elapsedMs": 4120}

``model`` names the model the stage is of as its report does (``script::function``, the script
relative to the build's folder; the script alone while it loads); ``outputs`` are the absolute
paths of the files that model's build writes; ``phase`` is one of ``PHASES``, ``label`` its words;
``done`` of ``total`` is the innermost running ``track``'s count, or the files written of the
model's files while they are written (``total`` null and ``determinate`` false for a lazy
``track``, or none); ``elapsedMs`` counts from the build's start. A phase opens with a frame of its
own. The author's frames are at least ``INTERVAL`` apart: one that comes sooner is held, and the
latest held is sent when the interval is up (or with the next frame), so a count is never left
stale behind a long item and a build's last state is always its last frame.

The stages are the engine's own steps, said where the build reaches them (``build.py``, through
``current``): the files each model writes, planned, give its frames their ``outputs``; a model's
body opens ``body``; its result collected and its elements named ``names``; its XCAF document made
``document``; and each file written ``write``, the file's name its detail.

Whoever runs the build takes the frames (``building``): a worker sends them over its pipe and its
daemon passes them on (``daemon``); a terminal draws them as a bar (``Bar``); the build's
``--json`` report holds each model's last (``Progress.attach``). Nothing is written to disk.
"""

from __future__ import annotations

import contextlib
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sized, TextIO

__all__ = ["INTERVAL", "PHASES", "Bar", "Progress", "building", "current", "describe", "report", "track"]

# Each phase, in the order a build passes them, and its words.
PHASES = {"load": "Loading the script", "body": "Building geometry", "names": "Naming elements",
          "document": "Making the document", "write": "Writing files"}
# The least time between two of the author's frames, in seconds; a phase's own frame is never held.
INTERVAL = 0.1


class Progress:
    """One build's progress (see the module): its state, and each frame of it sent to ``send``.

    ``script`` and ``cwd`` are the build's, which name its models as their reports do; ``code``
    says the build is of source text, whose script is named ``<code>``."""

    def __init__(self, send: Callable[[dict[str, Any]], None], *, script: Path | str | None = None,
                 cwd: Path | str | None = None, code: bool = False) -> None:
        self.send = send
        self.script = Path(script) if script is not None else None
        self.cwd = Path(cwd) if cwd is not None else None
        self.code = code
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.model = ""
        self.outputs: list[str] = []
        self.phase = "load"
        self.detail = ""
        self.count: list[Any] | None = None  # the phase's own [done, total]: the files written
        self.tracks: list[list[Any]] = []  # each running ``track``'s [done, total], the innermost last
        # Per model, by its script and function: its name in a frame and the files its build writes.
        self.planned: dict[tuple[str, str], tuple[str, list[str]]] = {}
        self.sent = float("-inf")
        self.held = False
        self.timer: threading.Timer | None = None
        self.closed = False
        self.last: dict[str, Any] | None = None
        self.frames: dict[str, dict[str, Any]] = {}  # each model's last frame

    # --- the frames --------------------------------------------------------------------------

    def frame(self) -> dict[str, Any]:
        """Where the build is now."""
        count = self.tracks[-1] if self.tracks else self.count
        done, total = (count[0], count[1]) if count is not None else (0, None)
        return {"model": self.model, "outputs": list(self.outputs), "phase": self.phase,
                "label": PHASES.get(self.phase, self.phase), "detail": self.detail, "done": done, "total": total,
                "determinate": bool(total), "elapsedMs": round((time.monotonic() - self.started) * 1000)}

    def _emit(self, force: bool = False) -> None:
        """Send where the build is; the author's frames (not ``force``) ``INTERVAL`` apart, a held
        one sent when the interval is up. Call with ``lock`` held."""
        if self.closed:
            return
        now = time.monotonic()
        if not force and now - self.sent < INTERVAL:
            self.held = True
            if self.timer is None:
                self.timer = threading.Timer(INTERVAL - (now - self.sent), self._flush)
                self.timer.daemon = True
                self.timer.start()
            return
        self._send(now)

    def _send(self, now: float) -> None:
        self.held = False
        self.sent = now
        found = self.frame()
        self.last = found
        if found["model"]:
            self.frames[found["model"]] = found
        try:
            self.send(found)
        except Exception:  # noqa: BLE001 - saying where a build is never fails it
            pass

    def _flush(self) -> None:
        with self.lock:
            self.timer = None
            if self.held and not self.closed:
                self._send(time.monotonic())

    def close(self) -> None:
        """The build is over: a held frame is sent now, and no other after it."""
        with self.lock:
            if self.timer is not None:
                self.timer.cancel()
                self.timer = None
            if self.held and not self.closed:
                self._send(time.monotonic())
            self.closed = True

    def attach(self, reports: Iterable[dict[str, Any]]) -> None:
        """Each report's ``progress``: its model's last frame, else the build's (a script that
        failed to load: where it was)."""
        for report in reports:
            if isinstance(report, dict):
                report["progress"] = self.frames.get(str(report.get("model")), self.last)

    # --- the engine's stages -----------------------------------------------------------------

    def name(self, script: Path | str | None, function: str | None = None) -> str:
        """A model as its report names it: its script relative to the build's folder (``<code>``
        for source text), and its function."""
        if script is None:
            shown = ""
        elif self.code and self.script is not None and Path(script).resolve() == self.script.resolve():
            shown = "<code>"
        else:
            try:
                shown = os.path.relpath(script, self.cwd or Path.cwd())
            except ValueError:  # another drive
                shown = str(script)
        return f"{shown}::{function}" if function else shown

    def stage(self, phase: str, *, model: str | None = None, outputs: list[str] | None = None, detail: str = "",
              count: list[Any] | None = None) -> None:
        """A phase begins: its own frame, sent at once."""
        with self.lock:
            self.phase = phase
            if model is not None:
                self.model = model
            if outputs is not None:
                self.outputs = list(outputs)
            self.detail = detail
            self.count = count
            self.tracks = []
            self._emit(force=True)

    def load(self) -> None:
        """The build has begun: its script loads."""
        self.stage("load", model=self.name(self.script), outputs=[])

    def plan(self, model: Any, paths: list[str]) -> None:
        with self.lock:
            self.planned[(str(model.script), model.name)] = (self.name(model.script, model.name), paths)

    def _model(self, script: str | None, function: str) -> tuple[str, list[str]] | None:
        found = self.planned.get((script, function)) if script is not None else None
        if found is None:  # by its function alone: the last planned of that name
            found = next((value for (_script, name), value in reversed(self.planned.items()) if name == function),
                         None)
        return found

    def body(self, model: Any) -> None:
        found = self._model(str(model.script), model.name)
        name, outputs = found if found is not None else (self.name(model.script, model.name), [])
        self.stage("body", model=name, outputs=outputs)

    def named(self, function: str) -> None:
        found = self._model(None, function)
        if found is None:
            self.stage("names")
        else:
            self.stage("names", model=found[0], outputs=found[1])

    def writing(self, path: Any) -> None:
        """A file is written: of the model whose files it is, the one being written now."""
        text = str(Path(os.fspath(path)))
        owner = next(((name, paths) for name, paths in reversed(list(self.planned.values())) if text in paths), None)
        if owner is None:
            self.stage("write", detail=Path(text).name)
        else:
            name, paths = owner
            self.stage("write", model=name, outputs=paths, detail=Path(text).name,
                       count=[paths.index(text), len(paths)])

    # --- the author's frames -----------------------------------------------------------------

    def say(self, detail: str) -> None:
        with self.lock:
            self.detail = detail
            self._emit()

    def counting(self, total: int | None) -> list[Any]:
        with self.lock:
            count = [0, total]
            self.tracks.append(count)
            self._emit()
            return count

    def item(self, detail: str) -> None:
        with self.lock:
            self.detail = detail
            self._emit()

    def advance(self, count: list[Any]) -> None:
        with self.lock:
            count[0] += 1
            self._emit()

    def counted(self, count: list[Any]) -> None:
        """A ``track`` is through: its last count is sent if it was held, and it counts no more."""
        with self.lock:
            if self.held and not self.closed:
                self._send(time.monotonic())
            self.tracks = [held for held in self.tracks if held is not count]


# --- the build of this process -----------------------------------------------------------------

_BUILDS: list[Progress] = []  # the builds running in this process, the newest last
_LOCK = threading.Lock()


def current() -> Progress | None:
    """This process's build (``building``), whose frames the engine's stages are; None outside one."""
    return _BUILDS[-1] if _BUILDS else None


_current = current


@contextlib.contextmanager
def building(progress: Progress) -> Iterator[Progress]:
    """Inside it, ``progress`` is this process's build: the author's calls and the engine's stages
    are its frames. It is closed after (``Progress.close``)."""
    with _LOCK:
        _BUILDS.append(progress)
    try:
        yield progress
    finally:
        with _LOCK:
            _BUILDS.remove(progress)
        progress.close()


def report(detail: str) -> None:
    """Name what the build is doing now (see the module); nothing outside a build."""
    current = _current()
    if current is not None:
        current.say(str(detail or ""))


def _label_for(item: Any, label: Callable[[Any], str] | None) -> str:
    if label is not None:
        try:
            return str(label(item) or "")
        except Exception:  # noqa: BLE001 - a label never fails a build
            return ""
    return item if isinstance(item, str) else ""


def track(items: Iterable[Any], *, label: Callable[[Any], str] | None = None) -> Iterator[Any]:
    """Iterate ``items``, counting the build's way through them (see the module): the count moves
    when an item's work is done, the detail names the item in flight. ``items`` is measured only
    when it has a length; a lazy iterable is never consumed ahead of the loop."""
    current = _current()
    if current is None:
        yield from items
        return
    count = current.counting(len(items) if isinstance(items, Sized) and len(items) else None)
    try:
        for item in items:
            current.item(_label_for(item, label))
            yield item
            current.advance(count)
    finally:
        current.counted(count)


# --- in words, and on a terminal ---------------------------------------------------------------


def describe(frame: dict[str, Any]) -> str:
    """A frame in words: its phase, its count and what is in flight (``Building geometry · 2/5
    middle``)."""
    count = f"{frame.get('done')}/{frame.get('total')}" if frame.get("determinate") else ""
    tail = " ".join(text for text in (count, str(frame.get("detail") or "")) if text)
    head = str(frame.get("label") or PHASES.get(str(frame.get("phase")), frame.get("phase") or ""))
    return f"{head} · {tail}" if tail else head


def elapsed(seconds: float) -> str:
    """A duration read at a glance: ``259ms``, ``1.1s``, ``10m03s``, ``2h10m``."""
    seconds = max(0.0, float(seconds))
    if seconds < 1.0:
        return f"{int(round(seconds * 1000))}ms"
    if seconds < 60.0:
        return f"{seconds:.1f}s"
    minutes, remainder = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return f"{minutes}m{remainder:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def _drawn(ratio: float, width: int = 18) -> str:
    filled = int(round(min(1.0, max(0.0, ratio)) * width))
    return f"▕{'█' * filled}{'░' * (width - filled)}▏"


class Bar:
    """A build's frames drawn on a terminal, as cadgen's CLI drew its builds: a line per model,
    under it a line per phase it finished with how long that took, and the phase in flight on a
    live line repainted in place -- its count as a bar when it has one, and what is in flight. On a
    stream that is no terminal it draws nothing at all. Lines printed through ``around`` go above
    the live line."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream = stream if stream is not None else sys.stderr
        try:
            self.enabled = bool(self.stream.isatty())
        except (AttributeError, ValueError, OSError):
            self.enabled = False
        self.lock = threading.RLock()
        self.width = 0
        self.model: str | None = None
        self.phase: str | None = None
        self.label = ""
        self.began = 0.0
        self.live = ""

    def _write(self, text: str) -> None:
        try:
            self.stream.write(text)
            self.stream.flush()
        except (OSError, ValueError):
            self.enabled = False

    def _clear(self) -> None:
        if self.width:
            self._write(f"\r{' ' * self.width}\r")
            self.width = 0

    def _commit(self, text: str) -> None:
        padding = max(0, self.width - len(text))
        self._write(f"\r{text}{' ' * padding}\n")
        self.width = 0

    def _repaint(self) -> None:
        if self.live:
            padding = max(0, self.width - len(self.live))
            self._write(f"\r{self.live}{' ' * padding}")
            self.width = len(self.live)

    def _finished(self) -> None:
        if self.phase is not None:
            self._commit(f"  ✓ {self.label.lower():<26}{elapsed(time.monotonic() - self.began):>9}")

    def frame(self, frame: dict[str, Any]) -> None:
        """One frame: a phase that ended leaves its line, the live line shows this one."""
        if not self.enabled:
            return
        with self.lock:
            model = str(frame.get("model") or "")
            if model != self.model or frame.get("phase") != self.phase:
                self._finished()
                if model != self.model and "::" in model:
                    self._commit(model)
                self.model, self.phase = model, frame.get("phase")
                self.label = str(frame.get("label") or self.phase or "")
                self.began = time.monotonic()
            text = self.label.lower()
            if frame.get("determinate"):
                text += f"  {_drawn(frame['done'] / frame['total'])}  {frame['done']}/{frame['total']}"
            if frame.get("detail"):
                text += f"  {frame['detail']}"
            self.live = f"    {text}"
            self._repaint()

    def around(self, write: Callable[[str], None]) -> Callable[[str], None]:
        """``write`` with the live line cleared before it and drawn again after."""
        if not self.enabled:
            return write

        def written(text: str) -> None:
            with self.lock:
                self._clear()
                write(text)
                self._repaint()

        return written

    def close(self) -> None:
        """The build is over: its last phase leaves its line, the live line goes."""
        if not self.enabled:
            return
        with self.lock:
            self._finished()
            self._clear()
            self.phase = self.model = None
            self.live = ""
