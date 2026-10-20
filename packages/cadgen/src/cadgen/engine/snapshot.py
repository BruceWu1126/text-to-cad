"""``cadgen xbf snapshot DOC OUT.png``: a picture of an engine document, drawn as the CAD
Viewer draws it.

The document is served by the engine (the daemon, so its meshes are the ones the viewer and
every build share, or ``--no-daemon`` a server in this process) and meshed there. A headless
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
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

__all__ = ["SnapshotRefused", "add_parser", "run", "snapshot"]

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


class _Served:
    """The document as an engine serves it: its API URL, and how to let go of a server this
    process started."""

    def __init__(self, path: Path, *, daemon: bool) -> None:
        self.server = None
        if daemon:
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


def _render(job: dict[str, Any], runtime: Path, size: tuple[int, int]) -> tuple[dict[str, Any], dict[str, int]]:
    from playwright.sync_api import sync_playwright

    timings: dict[str, int] = {}
    started = time.perf_counter()
    files = {"/render.html": runtime / "render.html", "/snapshot-render.js": runtime / "snapshot-render.js"}

    def route(handle: Any) -> None:
        path = urlparse(handle.request.url).path
        found = files.get(path)
        if found is None:
            handle.fulfill(status=404, content_type="text/plain", body="not found")
            return
        kind = "text/html" if path.endswith(".html") else "text/javascript"
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


def snapshot(document: Path, out: Path, *, display: dict[str, Any] | None = None, camera: Any = None,
             preview: bool = False, size: tuple[int, int] = DEFAULT_SIZE, daemon: bool = True) -> dict[str, Any]:
    """Draw ``document`` (an ``.xbf``) into ``out`` (a ``.png``); returns what was drawn and how long
    each stage took. Raises ``SnapshotRefused`` for a request it will not draw, before rendering."""
    started = time.perf_counter()
    document, out = Path(document).resolve(), Path(out).resolve()
    if document.suffix.lower() != ".xbf":
        raise SnapshotRefused(f"{document.name} is not an engine document (.xbf)")
    if not document.is_file():
        raise SnapshotRefused(f"no document {document}")
    if out.suffix.lower() != ".png":
        raise SnapshotRefused(f"a snapshot is a .png; {out.name} is not one")
    if not out.parent.is_dir():
        raise SnapshotRefused(f"no folder {out.parent} to write {out.name} into")
    display = dict(display or {})
    runtime = _runtime()
    timings: dict[str, Any] = {}
    clock = time.perf_counter()
    served = _Served(document, daemon=daemon)
    try:
        structure = _get_json(served.url)
        timings["serveMs"] = round((time.perf_counter() - clock) * 1000)
        placed = len(structure.get("instances") or [])
        if display.get("mode") == "parts" and placed < 2 and not preview:
            raise SnapshotRefused(f"display.mode parts colours each placed part apart; {document.name} places "
                                  f"{placed} part{'' if placed == 1 else 's'}. Use solid.")
        # Every part meshed by the engine before the page asks, so the page's load is the bytes alone.
        clock = time.perf_counter()
        meshed = _get_json(f"{served.url}/mesh", method="POST")
        timings["meshMs"] = round((time.perf_counter() - clock) * 1000)
        unmeshed = {prototype.get("name") or prototype["key"]: prototype["unmeshed"]
                    for prototype in _get_json(served.url)["prototypes"] if prototype.get("unmeshed")}
        for name, faces in unmeshed.items():
            print(f"warning: {name}: the mesher left face{'s' if len(faces) > 1 else ''} "
                  f"{', '.join(map(str, faces))} without triangles, so the picture does not draw "
                  f"{'them' if len(faces) > 1 else 'it'}", file=sys.stderr)
        output: dict[str, Any] = {"path": str(out), "width": size[0], "height": size[1]}
        if camera is not None:
            output["camera"] = camera
        job: dict[str, Any] = {
            "kind": "xbf", "mode": "view", "display": display, "outputs": [output],
            "resolved": {"kind": "xbf", "url": served.url, "inputPath": str(document)},
        }
        if preview:
            job["preview"] = True
        result, page = _render(job, runtime, size)
    finally:
        served.close()
    timings.update(page)
    stages = result.get("stageTimings") or {}
    for name in ("loadSourceMs", "buildModelMs", "prepareViewportMs", "captureMs"):
        if name in stages:
            timings[name] = stages[name]
    drawn = (result.get("outputs") or [{}])[0]
    head, _sep, data = str(drawn.get("dataUrl") or "").partition(",")
    if not head.startswith("data:image/png;base64"):
        raise RuntimeError("the snapshot page returned no PNG")
    out.write_bytes(base64.b64decode(data))
    timings["totalMs"] = round((time.perf_counter() - started) * 1000)
    return {"ok": True, "document": str(document), "output": str(out), "width": drawn.get("width"),
            "height": drawn.get("height"), "camera": drawn.get("camera"), "projection": drawn.get("projection"),
            "displayMode": display.get("mode", "solid"), "preview": preview, "parts": len(structure["prototypes"]),
            "instances": placed, "unmeshed": unmeshed,
            # The engine's account of the meshing this snapshot asked for: none where it held every part.
            "mesh": {key: meshed.get(key) for key in ("parts", "meshed", "seconds", "triangles", "mode")},
            "bytes": out.stat().st_size, "timings": timings}


def add_parser(verbs: Any) -> argparse.ArgumentParser:
    """The ``snapshot`` verb of ``cadgen xbf``."""
    parser = verbs.add_parser("snapshot", help="draw a document as the CAD Viewer does, into a PNG")
    parser.add_argument("document", help="an .xbf")
    parser.add_argument("out", help="the .png to write")
    parser.add_argument("--display", help="a mode (solid, shaded, parts, xray, hidden-line, wireframe) or the display "
                                          "JSON object (mode, grid, appearance, camera, clip), or a JSON file of it")
    parser.add_argument("--grid", action="store_true", help="draw the measuring grid")
    parser.add_argument("--camera", help="a preset (iso, front, back, left, right, top, bottom), azimuth:elevation, "
                                         "or a camera JSON object")
    parser.add_argument("--preview", action="store_true", help="Preview's photographic look instead of the mode")
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--size-profile", choices=sorted(SIZE_PROFILES))
    parser.add_argument("--no-daemon", action="store_true", help="serve the document from this process")
    parser.add_argument("--json", action="store_true", help="print what was drawn and its timings as JSON")
    return parser


def run(args: argparse.Namespace) -> int:
    try:
        display = parse_display(args.display, args.grid)
        camera = parse_camera(args.camera)
        size = parse_size(args.width, args.height, args.size_profile)
        report = snapshot(Path(args.document), Path(args.out), display=display, camera=camera, preview=args.preview,
                          size=size, daemon=not args.no_daemon)
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
