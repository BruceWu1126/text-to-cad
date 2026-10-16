"""``/__cad/prompt-files``: the files a Quick Edit prompt names, rendered off the request.

A copied Quick Edit is text, and text cannot carry a picture or a 3D annotation. So
the views the person looked at, and the ink they drew over them, are written
here as files and the prompt names them: anything that can read a file can read
them, and an agent can render the same views again -- after an edit, to check
it -- with ``cadgen snapshot --job <the job file>``.

One prompt writes, in this process's directory under the system's temporary
directory (which the system clears; they outlive the viewer, so a prompt pasted
later still finds them):

* ``<stem>-<n>.annotation.json`` -- the annotation, when the prompt has one: the format
  ``cadgen snapshot --annotation`` reads (SNAPSHOTS.md, "Annotations").
* ``<stem>-<n>.job.json`` -- ``{"jobs": [...]}``, one job per view: exactly what
  ``cadgen snapshot --job <file>`` renders, the annotation named by its path.
* ``<stem>-<n>-<label>.png`` -- each view, rendered from that job file by
  snapshot's own code (:func:`cadgen.snapshot_cli.run_snapshot_async`).

Routes (every one carries ``x-cadgen-viewer: 1``, GETs included: these files are
the person's, and the header keeps another site's page from reading them):

* ``POST /__cad/prompt-files``, body ``{"name", "input", "views": [{"label",
  "camera", "display"?, "kinematics"?, "jointValues"?, "animation"?, "selection"?,
  "width"?, "height"?}], "annotation": {...} | null, "measurements"?: [{"start", "end",
  "label", "color"}]}`` (Measure's results, drawn over every view) -> ``202 {"ok": true, "id", "retryMs"}``
  at once, the request accepted (its shape, the annotation's outer shape and the job
  as snapshot prepares it); ``400 {"ok": false, "error"}`` when it is not.
* ``POST /__cad/prompt-files/save``, body ``{"name", "kind": "annotation" | "measurement",
  "data"}`` -> ``200 {"ok": true, "path"}``: a Quick Edit chip's copy as a file, written at
  once (``PromptFiles.save``); ``400`` when it is not one.
* ``GET /__cad/prompt-files?id=<id>`` -> ``202 {"ok": true, "id", "retryMs"}``
  while it renders; ``200 {"ok": true, "job", "annotation": <path> | null,
  "images": [{"label", "path"}]}`` (the views' order) when it is done; ``500
  {"ok": false, "error"}`` when the render failed; ``404 {"ok": false, "error"}``
  for an id this process does not know, or no longer keeps (``KEEP_SECONDS``).
* ``GET /__cad/prompt-files/<file name>`` -> that file's bytes (``image/png`` or
  ``application/json``), from this directory only; anything else is a 404.

Renders run one at a time on a thread of their own, never on a request: a
render resolves its STEP's tree (a read of the store; a tree the store lacks is
the build pool's to compile) and draws in a headless browser. That browser is
kept open between prompts -- a cold one costs a second or two before the first
pixel -- and closed after ``IDLE_SECONDS`` without a prompt, after a render that
failed (a wedged page is never reused), and when the viewer stops.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import tempfile
import threading
import time
from pathlib import Path

from cadgen._internal.atomic_replace import write_bytes_atomic

__all__ = ["PROMPT_FILES_ROUTE", "PromptFiles", "WarmSnapshotSession"]

PROMPT_FILES_ROUTE = "/__cad/prompt-files"
# How long a request carries: an annotation is points, and a long session of annotating is megabytes.
MAX_BODY_BYTES = 32 * 1024 * 1024
MAX_VIEWS = 16
# When the page asks again while a prompt renders.
RETRY_MS = 200
# How long a finished render's answer is kept for the page to collect.
KEEP_SECONDS = 600.0
# How long the browser stays open without a prompt to render.
IDLE_SECONDS = 300.0
_REQUEST_KEYS = frozenset({"name", "input", "views", "annotation", "measurements"})
_VIEW_KEYS = frozenset({"label", "camera", "display", "kinematics", "jointValues", "animation", "selection", "width", "height"})
_ANNOTATION_KEYS = frozenset({"units", "parts", "marks"})
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_FILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.(png|json)")


def _slug(text: str, fallback: str) -> str:
    return _UNSAFE.sub("-", text).strip("-.")[:48] or fallback


def _stem(name: str) -> str:
    base = os.path.basename(str(name or "").replace("\\", "/"))
    for suffix in (".step", ".stp", ".stl", ".3mf", ".glb", ".urdf", ".srdf", ".sdf", ".dxf"):
        if base.lower().endswith(suffix):
            base = base[: -len(suffix)]
            break
    return _slug(base, "model")


class WarmSnapshotSession:
    """One headless snapshot browser kept open across prompts, on an event loop of its own.

    ``render(job_path)`` runs ``cadgen snapshot --job <job_path>`` -- the same function the
    CLI runs -- in that browser and answers its ``SnapshotResult``. Callers are serialised: a
    page renders one job at a time.
    """

    def __init__(self, *, idle_seconds: float = IDLE_SECONDS) -> None:
        self.idle_seconds = idle_seconds
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._renderer = None
        self._idle = None

    def _event_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=loop.run_forever, name="cadgen-prompt-browser", daemon=True)
            self._thread.start()
            self._loop = loop
        return self._loop

    def render(self, job_path: Path):
        with self._lock:
            return asyncio.run_coroutine_threadsafe(self._render(job_path), self._event_loop()).result()

    async def _render(self, job_path: Path):
        from cadgen._internal.snapshot_door import ALL_KINDS
        from cadgen.assets import browser_runtime_dir
        from cadgen.snapshot_cli import SnapshotOptions, run_snapshot_async
        from cadgen.snapshot_core import BatchSnapshotRenderer

        if self._idle is not None:
            self._idle.cancel()
            self._idle = None
        if self._renderer is None:
            self._renderer = BatchSnapshotRenderer(browser_runtime_dir())
        try:
            result = await run_snapshot_async(
                SnapshotOptions(job=str(job_path)), kinds=ALL_KINDS, cwd=job_path.parent,
                renderer=self._renderer, report=False,
            )
        except BaseException:
            # A page that failed may be wedged on the job it was given: never reuse it.
            await self._close_renderer()
            raise
        self._idle = asyncio.get_running_loop().call_later(
            self.idle_seconds, lambda: asyncio.ensure_future(self._close_renderer()))
        return result

    async def _close_renderer(self) -> None:
        renderer, self._renderer = self._renderer, None
        if renderer is not None:
            await renderer.close()

    def close(self) -> None:
        """Close the browser and stop the loop, at exit. A render still running fails."""
        loop, self._loop = self._loop, None
        if loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._close_renderer(), loop).result(10)
        except Exception:  # noqa: BLE001 - best-effort teardown at exit
            pass
        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(5)


class _Render:
    __slots__ = ("done", "status", "body", "finished_at")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.status = 500
        self.body: dict = {"ok": False, "error": "the prompt's render stopped before it finished"}
        self.finished_at = 0.0


def _directory() -> Path:
    return Path(tempfile.mkdtemp(prefix="cadgen-prompt-files-"))


class PromptFiles:
    """The route's state: this process's directory, its numbering, and its renders.

    ``render`` renders a job file and answers a ``SnapshotResult`` (the warm session's by
    default; a test hands in its own). ``on_crash`` hears a render that failed with
    something other than a snapshot's own error, once.
    """

    def __init__(self, *, render=None, directory=None, retry_ms: int = RETRY_MS, on_crash=None,
                 clock=time.monotonic) -> None:
        self._session = None if render is not None else WarmSnapshotSession()
        self._render_job = render if render is not None else self._session.render
        self._directory_factory = (lambda: Path(directory)) if directory is not None else _directory
        self._directory: Path | None = None
        self.retry_ms = retry_ms
        self._on_crash = on_crash
        self._clock = clock
        self._lock = threading.Lock()
        self._count = 0
        self._renders: dict[str, _Render] = {}

    @property
    def directory(self) -> Path:
        with self._lock:
            if self._directory is None:
                self._directory = self._directory_factory()
                self._directory.mkdir(parents=True, exist_ok=True)
            return self._directory

    def any_in_flight(self) -> bool:
        with self._lock:
            return any(not render.done.is_set() for render in self._renders.values())

    def close(self) -> None:
        if self._session is not None:
            self._session.close()

    # --- POST ----------------------------------------------------------------

    def save(self, body: bytes) -> tuple[int, dict]:
        """A Quick Edit chip's copy as a file, written at once: ``200 {"ok": true, "path"}``, or ``400``.

        ``{"name", "kind": "annotation", "data": <an annotation>}`` writes ``<name>-<n>.annotation.json``
        (``cadgen snapshot --annotation`` reads it); ``{"name", "kind": "measurement", "data":
        {start, end, label, color}}`` writes ``<name>-<n>.measurement.json``, the list of that one
        (``--measurements`` reads it). Nothing renders.
        """
        try:
            request = json.loads(body or b"null")
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"a chip's file is requested as JSON: {error}") from None
        if type(request) is not dict or not set(request) <= {"name", "kind", "data"}:
            raise ValueError("a chip's file is requested as {name, kind, data}")
        kind, data = request.get("kind"), request.get("data")
        if kind == "annotation":
            if type(data) is not dict or not set(data) <= _ANNOTATION_KEYS or type(data.get("marks")) is not list:
                raise ValueError("an annotation is an object of units, parts and marks (a list)")
            content, suffix = data, "annotation"
        elif kind == "measurement":
            from cadgen.snapshot_cli import normalize_measurements
            from cadgen.snapshot_core import SnapshotError

            try:
                content = normalize_measurements([data])
            except SnapshotError as error:
                raise ValueError(str(error)) from None
            suffix = "measurement"
        else:
            raise ValueError("kind is annotation or measurement")
        name = request.get("name")
        directory = self.directory
        with self._lock:
            self._count += 1
            path = directory / f"{_stem(name if type(name) is str and name else kind)}-{self._count}.{suffix}.json"
        path.write_text(json.dumps(content, indent=2), encoding="utf-8")
        return 200, {"ok": True, "path": str(path)}

    def start(self, body: bytes) -> tuple[int, dict]:
        """Accept a prompt's request and start its render: ``202`` with its id, or ``400``."""
        try:
            request = json.loads(body or b"null")
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"a prompt's files are requested as JSON: {error}") from None
        name, input_path, views, annotation, measurements = _checked(request)
        directory = self.directory
        with self._lock:
            self._count += 1
            prefix = f"{_stem(name or input_path)}-{self._count}"
        annotation_path = directory / f"{prefix}.annotation.json" if annotation is not None else None
        labels: set[str] = set()
        jobs, images = [], []
        for index, view in enumerate(views):
            slug = _slug(view["label"].lower(), f"view-{index + 1}")
            while slug in labels:
                slug = f"{slug}-{index + 1}"
            labels.add(slug)
            image = directory / f"{prefix}-{slug}.png"
            output = {"path": str(image), "label": view["label"]}
            for key in ("width", "height"):
                if key in view:
                    output[key] = view[key]
            job: dict[str, object] = {"input": input_path}
            if annotation_path is not None:
                job["annotation"] = str(annotation_path)
            job["camera"] = view["camera"]
            for key in ("display", "kinematics", "jointValues", "animation", "selection"):
                if view.get(key) is not None:
                    job[key] = view[key]
            # Measure's results, drawn over every view as the viewer drew them.
            if measurements:
                job["measurements"] = measurements
            job["output"] = {"padding": 0, "tightFrame": False}
            job["outputs"] = [output]
            jobs.append(job)
            images.append({"label": view["label"], "path": str(image)})
        job_path = directory / f"{prefix}.job.json"
        written = [job_path] + ([annotation_path] if annotation_path is not None else [])
        if annotation_path is not None:
            write_bytes_atomic(annotation_path, (json.dumps(annotation, indent=2) + "\n").encode("utf-8"))
        write_bytes_atomic(job_path, (json.dumps({"jobs": jobs}, indent=2) + "\n").encode("utf-8"))
        try:
            _accepted(job_path)
        except ValueError:
            for path in written:
                path.unlink(missing_ok=True)
            raise
        answer = {"ok": True, "job": str(job_path), "annotation": str(annotation_path) if annotation_path else None,
                  "images": images}
        render_id = secrets.token_hex(12)
        render = _Render()
        with self._lock:
            self._prune()
            self._renders[render_id] = render
        threading.Thread(target=self._run, args=(render, job_path, answer), name="cadgen-prompt-files",
                         daemon=True).start()
        return 202, {"ok": True, "id": render_id, "retryMs": self.retry_ms}

    def _run(self, render: _Render, job_path: Path, answer: dict) -> None:
        from cadgen.snapshot_core import SnapshotError

        try:
            result = self._render_job(job_path)
            if getattr(result, "ok", False):
                render.status, render.body = 200, answer
            else:
                warnings = "; ".join(getattr(result, "warnings", ()) or ())
                render.body = {"ok": False, "error": f"the views did not render{': ' + warnings if warnings else ''}"}
        except SnapshotError as error:
            render.body = {"ok": False, "error": str(error)}
        except Exception as error:  # noqa: BLE001 - answered as a failed render, and counted as a crash
            if self._on_crash is not None:
                self._on_crash(error)
            render.body = {"ok": False, "error": str(error) or type(error).__name__}
        finally:
            render.finished_at = self._clock()
            render.done.set()

    # --- GET -----------------------------------------------------------------

    def status(self, render_id) -> tuple[int, dict]:
        """A render's answer: ``202`` while it runs, then its ``200`` or ``500``; ``404`` unknown."""
        with self._lock:
            self._prune()
            render = self._renders.get(str(render_id or ""))
        if render is None:
            return 404, {"ok": False, "error": "no prompt render by that id: it finished too long ago, or never began"}
        if not render.done.is_set():
            return 202, {"ok": True, "id": render_id, "retryMs": self.retry_ms}
        return render.status, render.body

    def file(self, name: str) -> tuple[bytes, str] | None:
        """A file of this directory, by its bare name, and its content type; ``None`` for any other."""
        if not _FILE_NAME.fullmatch(name or ""):
            return None
        with self._lock:
            directory = self._directory
        if directory is None:
            return None
        path = directory / name
        try:
            if path.is_symlink() or not path.is_file():
                return None
            data = path.read_bytes()
        except OSError:
            return None
        return data, "image/png" if name.endswith(".png") else "application/json; charset=utf-8"

    def _prune(self) -> None:
        now = self._clock()
        for render_id, render in list(self._renders.items()):
            if render.done.is_set() and now - render.finished_at > KEEP_SECONDS:
                del self._renders[render_id]


def _checked(request) -> tuple[str, str, list[dict], dict | None, list | None]:
    """The request's fields, each checked; ``ValueError`` names what is wrong."""
    if type(request) is not dict:
        raise ValueError("a prompt's files are requested as an object: {name, input, views, annotation, measurements}")
    unknown = sorted(set(request) - _REQUEST_KEYS)
    if unknown:
        raise ValueError(f"unknown field(s): {', '.join(unknown)}; a request has {', '.join(sorted(_REQUEST_KEYS))}")
    name = request.get("name")
    if name is not None and type(name) is not str:
        raise ValueError("name is the file's name, a string")
    input_path = request.get("input")
    if type(input_path) is not str or not os.path.isabs(input_path):
        raise ValueError("input is the absolute path of the file the views show")
    if not os.path.isfile(input_path):
        raise ValueError(f"no file at {input_path}")
    views = request.get("views")
    if type(views) is not list or not views:
        raise ValueError("views lists the views to render, at least one")
    if len(views) > MAX_VIEWS:
        raise ValueError(f"a prompt renders at most {MAX_VIEWS} views")
    for index, view in enumerate(views):
        at = f"views[{index}]"
        if type(view) is not dict:
            raise ValueError(f"{at} is an object: {{label, camera, ...}}")
        unknown = sorted(set(view) - _VIEW_KEYS)
        if unknown:
            raise ValueError(f"{at} has unknown field(s): {', '.join(unknown)}; a view has {', '.join(sorted(_VIEW_KEYS))}")
        if type(view.get("label")) is not str or not view["label"].strip():
            raise ValueError(f"{at}.label names the view")
        if type(view.get("camera")) not in (dict, str):
            raise ValueError(f"{at}.camera is the view's camera")
        for key in ("width", "height"):
            if key in view and (type(view[key]) is not int or not 1 <= view[key] <= 8192):
                raise ValueError(f"{at}.{key} is a number of pixels, 1 to 8192")
    annotation = request.get("annotation")
    if annotation is not None:
        if type(annotation) is not dict or not set(annotation) <= _ANNOTATION_KEYS or type(annotation.get("marks")) is not list:
            raise ValueError("annotation is null or an object of units, parts and marks (a list)")
        if not all(type(mark) is dict for mark in annotation["marks"]):
            raise ValueError("annotation.marks lists objects")
    # Measure's results: their shape is snapshot's to check, with the job (`_accepted`).
    measurements = request.get("measurements")
    if measurements is not None and type(measurements) is not list:
        raise ValueError("measurements is null or a list of {start, end, label, color}")
    return name or "", input_path, views, annotation, measurements


def _accepted(job_path: Path) -> None:
    """The job file as ``cadgen snapshot --job`` accepts it, before anything renders.

    Snapshot's own preparation (``prepare_render_job_packet``): it refuses what a request
    can be refused for and builds nothing. Its refusal is this request's 400.
    """
    from cadgen._internal.snapshot_door import ALL_KINDS
    from cadgen.snapshot_cli import enabled_kinds, prepare_render_job_packet, read_job_file
    from cadgen.snapshot_core import SnapshotError

    try:
        prepare_render_job_packet(
            read_job_file(str(job_path), cwd=job_path.parent), cwd=job_path.parent, kinds=enabled_kinds(ALL_KINDS)
        )
    except SnapshotError as error:
        raise ValueError(str(error)) from None
