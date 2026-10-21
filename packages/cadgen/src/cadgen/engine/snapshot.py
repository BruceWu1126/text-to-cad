"""``cadgen xbf snapshot SRC OUT.png``: a picture of a document or a mesh file, drawn as the CAD
Viewer draws it. SRC is an ``.xbf`` or a ``.step`` (a document), or a ``.glb``, ``.stl`` or
``.3mf`` (a mesh file).

A document is served by the engine (the daemon, so its meshes are the ones the viewer and
every build share, or ``--no-daemon`` a server in this process; a build's own document, which
``@snapshot`` and ``--snapshot`` draw, from the worker's memory over its one mesh cache) and
meshed there. A mesh file is meshed by nobody: the page reads its bytes and draws them with the
mesh renderers the viewer's GLB and mesh tabs call (``headlessScene.js``'s mesh families). A headless
browser opens the snapshot page of cadgen's browser runtime -- ``render.html`` and
``snapshot-render.js`` in the package's built ``_runtime/browser``, read as files, never
imported -- and hands it one render job for the ``xbf`` family. The page reads the structure and
every part buffer from the engine itself and draws them with the scene builder the viewer's xbf
renderer calls (``@text-to-cad/core`` ``lib/xbf/xbfScene.js``, routed by ``headlessScene.js``),
dressed in the look the viewer's viewport resolves for the same display settings. So a snapshot
cannot show a document differently from the viewer: only the camera fit, the size and the PNG
encoding are its own.

The request is the snapshot contract's where it applies (``--display`` mode or JSON, ``--grid``,
``--camera``, ``--preview``, ``clip``, the sizes, ``--json``), and what an engine document's view
does not offer is refused by name before anything renders: ``display.exploded`` (a document's
parts stand where it places them) and ``parts`` for a document that places a single part. Nothing
is written but OUT.

``--pose`` draws the document's mechanism at a configuration (its name) or at joint values (a JSON
object, the rest as built): the engine serves the document at that pose, each link placed by its
pairs from the ground, and the page draws what it is served. An unknown configuration or joint, a
value out of range and a mechanism that is not a tree are refused by name before anything renders.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen

__all__ = ["SnapshotRefused", "add_parser", "draw", "options", "parse_pose", "run", "snapshot"]

PAGE_ORIGIN = "http://localhost"
PAGE_URL = f"{PAGE_ORIGIN}/render.html"
RUNTIME = Path(__file__).resolve().parent.parent / "_runtime" / "browser"
DISPLAY_MODES = ("solid", "shaded", "parts", "xray", "hidden-line", "wireframe")
DISPLAY_KEYS = frozenset({"mode", "grid", "appearance", "camera", "clip"})
CAMERA_PRESETS = frozenset({"front", "back", "right", "left", "top", "bottom", "iso"})
CAMERA_KEYS = frozenset({"preset", "name", "position", "target", "up", "direction", "zoom", "orthographicHalfHeight"})
# The snapshot contract's size profiles; a request names one or a width and a height.
SIZE_PROFILES = {
    "simple": (1200, 900), "simple-square": (1024, 1024), "diagnostic": (1600, 1200), "labeled": (1600, 1200),
    "assembly": (1800, 1200), "assembly-large": (1920, 1440), "presentation": (2400, 1600),
    "presentation-large": (2800, 1800), "contact-sheet": (2400, 1600),
}
DEFAULT_SIZE = SIZE_PROFILES["diagnostic"]
MAX_SIDE = 8192
TIMEOUT_MS = 300_000


class SnapshotRefused(ValueError):
    """A request the snapshot will not draw, said before anything renders."""


# --- the request ---------------------------------------------------------------------------


def _json_or_file(text: str, label: str) -> Any:
    candidate = Path(text)
    if not text.lstrip().startswith(("{", "[")) and candidate.suffix == ".json" and candidate.is_file():
        text = candidate.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise SnapshotRefused(f"{label} is not JSON: {error}") from None


def parse_display(value: str | None, grid: bool) -> dict[str, Any]:
    """``--display``: a mode, or the viewer's display object (JSON, or a JSON file)."""
    display: dict[str, Any] = {}
    if value:
        text = value.strip()
        if text in DISPLAY_MODES:
            display = {"mode": text}
        elif text.startswith("{") or text.endswith(".json"):
            parsed = _json_or_file(text, "--display")
            if not isinstance(parsed, dict):
                raise SnapshotRefused("--display is a mode or a JSON object")
            display = dict(parsed)
        else:
            raise SnapshotRefused(f"--display {text!r} is no display mode; the modes are {', '.join(DISPLAY_MODES)}")
    if "exploded" in display:
        raise SnapshotRefused("display.exploded moves a model's parts apart; an engine document's parts stand where the "
                              "document places them, and its view has no Explode. Remove display.exploded.")
    unknown = sorted(set(display) - DISPLAY_KEYS)
    if unknown:
        raise SnapshotRefused(f"display has no {', '.join(unknown)}; an engine document's display takes "
                              f"{', '.join(sorted(DISPLAY_KEYS))}")
    if "mode" in display and display["mode"] not in DISPLAY_MODES:
        raise SnapshotRefused(f"display.mode {display['mode']!r} is no display mode; the modes are {', '.join(DISPLAY_MODES)}")
    if "grid" in display and not isinstance(display["grid"], bool):
        raise SnapshotRefused("display.grid is true or false")
    if grid:
        display["grid"] = True
    return display


def parse_camera(value: str | None) -> Any:
    """``--camera``: a preset, an ``azimuth:elevation`` pair in degrees, or a camera JSON object."""
    if not value:
        return None
    text = value.strip()
    if text.startswith("{"):
        parsed = _json_or_file(text, "--camera")
        if not isinstance(parsed, dict):
            raise SnapshotRefused("--camera is a preset, an azimuth:elevation pair or a JSON object")
        unknown = sorted(set(parsed) - CAMERA_KEYS)
        if unknown:
            raise SnapshotRefused(f"camera has no {', '.join(unknown)}; its keys are {', '.join(sorted(CAMERA_KEYS))}")
        moved = [key for key in ("projection", "focalLength") if key in parsed]
        if moved:
            raise SnapshotRefused(f"camera {', '.join(moved)} belongs to display.camera")
        return parsed
    if text.lower() in CAMERA_PRESETS:
        return text.lower()
    pair = text.split(":")
    try:
        if len(pair) == 2 and all(math.isfinite(float(part)) for part in pair):
            return text
    except ValueError:
        pass
    raise SnapshotRefused(f"camera {text!r} is no preset ({', '.join(sorted(CAMERA_PRESETS))}), "
                          "azimuth:elevation pair or JSON object")


def parse_pose(value: str | None) -> Any:
    """``--pose``: a configuration's name, or ``{"<joint>": value}`` (JSON, or a JSON file of it)."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        raise SnapshotRefused("--pose names a configuration, or gives joint values as a JSON object")
    if text.startswith("{") or (text.endswith(".json") and Path(text).is_file()):
        parsed = _json_or_file(text, "--pose")
        if not isinstance(parsed, dict):
            raise SnapshotRefused("--pose is a configuration's name or a JSON object of joint values")
        return parsed
    return text


def parse_size(width: int | None, height: int | None, profile: str | None) -> tuple[int, int]:
    if profile and (width or height):
        raise SnapshotRefused("--size-profile names a size; give it, or --width and --height, not both")
    if profile:
        if profile not in SIZE_PROFILES:
            raise SnapshotRefused(f"--size-profile {profile!r} is not one of {', '.join(SIZE_PROFILES)}")
        return SIZE_PROFILES[profile]
    size = (width or DEFAULT_SIZE[0], height or DEFAULT_SIZE[1])
    for side in size:
        if not 16 <= side <= MAX_SIDE:
            raise SnapshotRefused(f"an image side is 16 to {MAX_SIDE} pixels, not {side}")
    return size


# --- the engine ----------------------------------------------------------------------------


def _get_json(url: str, *, method: str = "GET", timeout: float = 600.0) -> Any:
    with urlopen(Request(url, method=method), timeout=timeout) as answer:  # noqa: S310 - the engine, on loopback
        return json.loads(answer.read())


def _posed(served: "_Served", pose: Any) -> tuple[str, dict[str, Any]]:
    """The served document at ``pose``: its URL and the pose's values. What the pose names
    wrong is refused by name, in the engine's words, before anything renders."""
    from urllib.error import HTTPError

    body = json.dumps({"pose": pose}).encode()
    request = Request(f"{served.url}/pose", data=body, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=600.0) as answer:  # noqa: S310 - the engine, on loopback
            found = json.loads(answer.read())
    except HTTPError as error:
        if error.code != 400:
            raise
        raise SnapshotRefused(f"--pose: {json.loads(error.read() or b'{}').get('error') or error}") from None
    return f"{served.origin}/api/doc/{found['id']}", {"values": found["values"], "configuration": found["configuration"]}


class _Served:
    """The document as an engine serves it: its API URL, and how to let go of a server this
    process started."""

    def __init__(self, path: Path, *, daemon: bool, source: Any = None) -> None:
        self.server = None
        if source is not None:
            # A build's document, held in this process: served from memory by a server of its
            # own over the process's one mesh cache, so what the build meshed is drawn as it is.
            from cadgen.engine.serve import MESHES, Server

            self.server = Server(port=0, mesh_mode="inline", cache=MESHES)
            self.server.start()
            shapes = {prototype["key"]: source.shape_data(prototype["key"])
                      for prototype in source.structure["prototypes"]}
            origin, ident = self.server.url, self.server.put(path, source.structure, shapes)
        elif daemon:
            from cadgen.engine.client import request

            answer = request({"op": "serve", "path": str(path)})
            if not answer or "failed" in answer:
                raise RuntimeError(f"the engine would not serve {path.name}: {(answer or {}).get('failed', 'no daemon')}")
            parsed = urlparse(answer["url"])
            origin, ident = f"{parsed.scheme}://{parsed.netloc}", parse_qs(parsed.query)["doc"][0]
        else:
            from cadgen.engine.serve import Server

            self.server = Server(port=0)
            self.server.start()
            origin, ident = self.server.url, self.server.add(path)
        self.origin = origin
        self.url = f"{origin}/api/doc/{ident}"

    def close(self) -> None:
        if self.server is not None:
            self.server.stop()


# --- the page ------------------------------------------------------------------------------


def _runtime() -> Path:
    override = os.environ.get("CADGEN_BROWSER_RUNTIME_DIR", "").strip()
    folder = Path(override).expanduser().resolve() if override else RUNTIME
    missing = [name for name in ("render.html", "snapshot-render.js") if not (folder / name).is_file()]
    if missing:
        raise RuntimeError(f"cadgen's snapshot browser runtime in {folder} lacks {', '.join(missing)}: "
                           "build it with scripts/bundle/bundle.sh (a wheel carries it)")
    return folder


def _launch(playwright: Any) -> Any:
    args = [
        # The page (http://localhost, routed to the runtime's files) reads the engine on another
        # loopback origin: keep Private Network Access's checks off, as cadgen's snapshot does.
        "--disable-features=BlockInsecurePrivateNetworkRequests,PrivateNetworkAccessSendPreflights,"
        "PrivateNetworkAccessRespectPreflightResults,PrivateNetworkAccessChecks,LocalNetworkAccessChecks",
        # The real GPU on macOS; elsewhere the platform's default.
        *(["--use-angle=metal"] if sys.platform == "darwin" else []),
    ]
    try:
        return playwright.chromium.launch(headless=True, args=args)
    except Exception as error:  # noqa: BLE001 - Playwright raises its own Error for a missing browser
        if "Executable doesn't exist" not in str(error):
            raise
    import subprocess

    print("cadgen: downloading the snapshot browser (once)...", file=sys.stderr)
    subprocess.run([sys.executable, "-m", "playwright", "install", "--only-shell", "chromium"], check=True,
                   stdout=sys.stderr)
    return playwright.chromium.launch(headless=True, args=args)


def _render(job: dict[str, Any], runtime: Path, size: tuple[int, int],
            extra: dict[str, Path] | None = None) -> tuple[dict[str, Any], dict[str, int]]:
    from playwright.sync_api import sync_playwright

    timings: dict[str, int] = {}
    started = time.perf_counter()
    files = {"/render.html": runtime / "render.html", "/snapshot-render.js": runtime / "snapshot-render.js",
             **(extra or {})}

    def route(handle: Any) -> None:
        path = urlparse(handle.request.url).path
        found = files.get(path)
        if found is None:
            handle.fulfill(status=404, content_type="text/plain", body="not found")
            return
        kind = ("text/html" if path.endswith(".html") else "text/javascript" if path.endswith(".js")
                else "application/octet-stream")
        handle.fulfill(status=200, content_type=kind, body=found.read_bytes(), headers={"cache-control": "no-store"})

    with sync_playwright() as playwright:
        browser = _launch(playwright)
        try:
            context = browser.new_context(viewport={"width": size[0], "height": size[1]}, device_scale_factor=1)
            page = context.new_page()
            page.route(f"{PAGE_ORIGIN}/**", route)
            page.goto(PAGE_URL, wait_until="load", timeout=TIMEOUT_MS)
            page.wait_for_function("typeof window.__snapshotRender === 'function'", timeout=TIMEOUT_MS)
            timings["browserMs"] = round((time.perf_counter() - started) * 1000)
            clock = time.perf_counter()
            result = page.evaluate("(job) => window.__snapshotRender(job)", job)
            timings["pageMs"] = round((time.perf_counter() - clock) * 1000)
        finally:
            browser.close()
    if not isinstance(result, dict) or not result.get("ok"):
        message = result.get("error") if isinstance(result, dict) else ""
        raise RuntimeError(str(message or "the snapshot page drew nothing"))
    return result, timings


# --- the verb ------------------------------------------------------------------------------


DOCUMENTS = (".xbf", ".step", ".stp")
# A mesh file and the family the snapshot page draws it with (``headlessScene.js``).
MESH_KINDS = {".glb": "glb", ".stl": "stl", ".3mf": "3mf"}


def _mesh_file(document: Path, out: Path, display: dict[str, Any], camera: Any, preview: bool,
               size: tuple[int, int], started: float) -> dict[str, Any]:
    """A GLB, STL or 3MF drawn by the shared mesh renderers of the snapshot page: the page
    reads the file's bytes as the viewer's mesh renderer does, no engine in between."""
    kind = MESH_KINDS[document.suffix.lower()]
    route = f"/file/{quote(document.name)}"
    output: dict[str, Any] = {"path": str(out), "width": size[0], "height": size[1]}
    if camera is not None:
        output["camera"] = camera
    job: dict[str, Any] = {"kind": kind, "mode": "view", "display": display, "outputs": [output],
                           "resolved": {"kind": kind, "url": route, "inputPath": str(document)}}
    if preview:
        job["preview"] = True
    result, timings = _render(job, _runtime(), size, {route: document})
    drawn = _png(result, out, timings)
    timings["totalMs"] = round((time.perf_counter() - started) * 1000)
    return {"ok": True, "document": str(document), "output": str(out), "width": drawn.get("width"),
            "height": drawn.get("height"), "camera": drawn.get("camera"), "projection": drawn.get("projection"),
            "displayMode": display.get("mode", "solid"), "preview": preview, "pose": None,
            "bytes": out.stat().st_size, "timings": timings}


def _png(result: dict[str, Any], out: Path, timings: dict[str, Any]) -> dict[str, Any]:
    """The page's picture written to ``out``: beside it first, then moved over it."""
    stages = result.get("stageTimings") or {}
    for name in ("loadSourceMs", "buildModelMs", "prepareViewportMs", "captureMs"):
        if name in stages:
            timings[name] = stages[name]
    drawn = (result.get("outputs") or [{}])[0]
    head, _sep, data = str(drawn.get("dataUrl") or "").partition(",")
    if not head.startswith("data:image/png;base64"):
        raise RuntimeError("the snapshot page returned no PNG")
    temporary = out.with_name(f".{out.stem}.{os.getpid()}.tmp.png")
    temporary.write_bytes(base64.b64decode(data))
    os.replace(temporary, out)
    return drawn


def options(display: Any = None, camera: Any = None, grid: bool = False, width: int | None = None,
            height: int | None = None, size_profile: str | None = None) -> tuple[dict[str, Any], Any, tuple[int, int]]:
    """A snapshot's options as a decorator or a function gives them -- ``display`` a mode, a
    JSON text or the display object -- checked as the verb checks its flags."""
    if isinstance(display, dict):
        display = json.dumps(display)
    return (parse_display(display, bool(grid)), parse_camera(camera if camera is None or isinstance(camera, str)
                                                              else json.dumps(camera)),
            parse_size(width, height, size_profile))


def draw(source: Any, out: Path, *, display: Any = None, camera: Any = None, grid: bool = False,
         preview: bool = False, pose: Any = None, width: int | None = None, height: int | None = None) -> dict[str, Any]:
    """A build's document (an ``export.Source``, in this process's memory) drawn into ``out``:
    the ``@snapshot`` file and ``--snapshot``. Served from memory over the process's mesh
    cache, so nothing the build meshed is meshed again."""
    shown, framed, size = options(display, camera, grid, width, height)
    path = source.path or Path(out).with_suffix(".xbf")
    return snapshot(path, Path(out), display=shown, camera=framed, preview=preview, size=size, pose=pose,
                    source=source)


def snapshot(document: Path, out: Path, *, display: dict[str, Any] | None = None, camera: Any = None,
             preview: bool = False, size: tuple[int, int] = DEFAULT_SIZE, daemon: bool = True,
             pose: Any = None, source: Any = None) -> dict[str, Any]:
    """Draw ``document`` -- an ``.xbf`` or a ``.step`` the engine serves and meshes, or a mesh
    file (``.glb``, ``.stl``, ``.3mf``) the page reads itself -- into ``out`` (a ``.png``);
    returns what was drawn and how long each stage took. ``pose``: a configuration's name or
    ``{joint: value}``, the engine placing each link by its pairs from the ground; None draws
    the document as built; a mesh has no mechanism to pose. ``source``: the document is a
    build's, held in this process (``draw``). Raises ``SnapshotRefused`` for a request it
    will not draw, before rendering."""
    started = time.perf_counter()
    document, out = Path(document).resolve(), Path(out).resolve()
    suffix = document.suffix.lower()
    if suffix not in DOCUMENTS and suffix not in MESH_KINDS:
        raise SnapshotRefused(f"{document.name} is neither a document (.xbf, .step) nor a mesh (.glb, .stl, .3mf)")
    if source is None and not document.is_file():
        raise SnapshotRefused(f"no document {document}")
    if out.suffix.lower() != ".png":
        raise SnapshotRefused(f"a snapshot is a .png; {out.name} is not one")
    if not out.parent.is_dir():
        out.parent.mkdir(parents=True, exist_ok=True)
    display = dict(display or {})
    if suffix in MESH_KINDS:
        if pose is not None:
            raise SnapshotRefused(f"--pose poses a document's mechanism; {document.name} is a mesh and has none")
        return _mesh_file(document, out, display, camera, preview, size, started)
    runtime = _runtime()
    timings: dict[str, Any] = {}
    clock = time.perf_counter()
    served = _Served(document, daemon=daemon, source=source)
    posed = None
    try:
        url = served.url
        if pose is not None:
            url, posed = _posed(served, pose)
        structure = _get_json(url)
        timings["serveMs"] = round((time.perf_counter() - clock) * 1000)
        placed = len(structure.get("instances") or [])
        if display.get("mode") == "parts" and placed < 2 and not preview:
            raise SnapshotRefused(f"display.mode parts colours each placed part apart; {document.name} places "
                                  f"{placed} part{'' if placed == 1 else 's'}. Use solid.")
        # Every part meshed by the engine before the page asks, so the page's load is the bytes alone.
        clock = time.perf_counter()
        meshed = _get_json(f"{url}/mesh", method="POST")
        timings["meshMs"] = round((time.perf_counter() - clock) * 1000)
        unmeshed = {prototype.get("name") or prototype["key"]: prototype["unmeshed"]
                    for prototype in _get_json(url)["prototypes"] if prototype.get("unmeshed")}
        for name, faces in unmeshed.items():
            print(f"warning: {name}: the mesher left face{'s' if len(faces) > 1 else ''} "
                  f"{', '.join(map(str, faces))} without triangles, so the picture does not draw "
                  f"{'them' if len(faces) > 1 else 'it'}", file=sys.stderr)
        output: dict[str, Any] = {"path": str(out), "width": size[0], "height": size[1]}
        if camera is not None:
            output["camera"] = camera
        job: dict[str, Any] = {
            "kind": "xbf", "mode": "view", "display": display, "outputs": [output],
            "resolved": {"kind": "xbf", "url": url, "inputPath": str(document)},
        }
        if preview:
            job["preview"] = True
        result, page = _render(job, runtime, size)
    finally:
        served.close()
    timings.update(page)
    drawn = _png(result, out, timings)
    timings["totalMs"] = round((time.perf_counter() - started) * 1000)
    return {"ok": True, "document": str(document), "output": str(out), "width": drawn.get("width"),
            "height": drawn.get("height"), "camera": drawn.get("camera"), "projection": drawn.get("projection"),
            "displayMode": display.get("mode", "solid"), "preview": preview, "parts": len(structure["prototypes"]),
            "instances": placed, "unmeshed": unmeshed, "pose": posed,
            # The engine's account of the meshing this snapshot asked for: none where it held every part.
            "mesh": {key: meshed.get(key) for key in ("parts", "meshed", "seconds", "triangles", "mode")},
            "bytes": out.stat().st_size, "timings": timings}


def add_parser(verbs: Any) -> argparse.ArgumentParser:
    """The ``snapshot`` verb of ``cadgen xbf``."""
    parser = verbs.add_parser("snapshot", help="draw a document as the CAD Viewer does, into a PNG")
    parser.add_argument("document", metavar="SRC", help="a document (.xbf, .step) or a mesh file (.glb, .stl, .3mf)")
    parser.add_argument("out", metavar="OUT.png", help="the .png to write")
    parser.add_argument("--display", help="a mode (solid, shaded, parts, xray, hidden-line, wireframe) or the display "
                                          "JSON object (mode, grid, appearance, camera, clip), or a JSON file of it")
    parser.add_argument("--grid", action="store_true", help="draw the measuring grid")
    parser.add_argument("--camera", help="a preset (iso, front, back, left, right, top, bottom), azimuth:elevation, "
                                         "or a camera JSON object")
    parser.add_argument("--preview", action="store_true", help="Preview's photographic look instead of the mode")
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--size-profile", choices=sorted(SIZE_PROFILES))
    parser.add_argument("--pose", help="a configuration's name, or joint values as JSON ('{\"elbow\": 45}'): the "
                                       "mechanism posed there; as built without it")
    parser.add_argument("--no-daemon", action="store_true", help="serve the document from this process")
    parser.add_argument("--json", action="store_true", help="print what was drawn and its timings as JSON")
    return parser


def run(args: argparse.Namespace) -> int:
    try:
        display = parse_display(args.display, args.grid)
        camera = parse_camera(args.camera)
        size = parse_size(args.width, args.height, args.size_profile)
        pose = parse_pose(args.pose)
        report = snapshot(Path(args.document), Path(args.out), display=display, camera=camera, preview=args.preview,
                          size=size, daemon=not args.no_daemon, pose=pose)
    except SnapshotRefused as error:
        print(f"cadgen xbf snapshot: {error}", file=sys.stderr)
        return 2
    except Exception as error:  # noqa: BLE001 - a failed render is the command's answer
        print(f"cadgen xbf snapshot: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report))
    else:
        timings = report["timings"]
        print(f"wrote {args.out} ({report['width']}x{report['height']}, {report['displayMode']}"
              f"{', preview' if report['preview'] else ''}) in {timings['totalMs'] / 1000:.2f}s")
    return 0


class _Verb(type(sys)):
    """This module is also the ``@snapshot`` decorator (``model.snapshot``): imported, its name in
    ``cadgen.engine`` is this module, and a model's ``from cadgen.engine import snapshot`` must
    still declare a snapshot."""

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        from cadgen.engine.model import snapshot as decorate

        return decorate(*args, **kwargs)


sys.modules[__name__].__class__ = _Verb
