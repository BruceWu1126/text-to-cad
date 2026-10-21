"""``cadgen xbf snapshot SRC OUT.png``: a picture of a document or a mesh file, drawn as the CAD
Viewer draws it. SRC is an ``.xbf`` or a ``.step`` (a document), or a ``.glb``, ``.stl`` or
``.3mf`` (a mesh file).

A document is served by the engine (the daemon, so its meshes are the ones the viewer and
every build share, or ``--no-daemon`` a server in this process; a model's own document, which
``Model.snapshot`` and ``cadgen.snapshot(model, ...)`` draw, from its worker's memory over its one
mesh cache) and
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
does not offer is refused by name before anything renders: ``parts`` and ``exploded`` for a document
that places a single part. ``--display exploded=0.6`` (or ``display.exploded``) moves the parts
apart as the viewer's Explode does, by its amount (0.5 unless one is given). ``--annotation FILE``
draws the viewer's annotation over the model (its ink on the parts it was drawn on, wherever they
stand): an annotation file, or the Quick Edit packet that carries one (a ``.snapshot.json``).
``--measurements FILE`` draws Measure's dimensions. ``--camera authored`` draws the picture from the
view the ink was drawn in, exactly (no fit): a mark names its view only by label ("view 1"), and
the view -- its camera, size and projection -- is a job of the packet (``authored``). Nothing is
written but OUT.

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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen

__all__ = ["SnapshotRefused", "Still", "add_parser", "authored", "draw", "options", "parse_annotation",
           "parse_measurements", "parse_pose", "run", "snapshot"]

PAGE_ORIGIN = "http://localhost"
PAGE_URL = f"{PAGE_ORIGIN}/render.html"
RUNTIME = Path(__file__).resolve().parent.parent / "_runtime" / "browser"
DISPLAY_MODES = ("solid", "shaded", "parts", "xray", "hidden-line", "wireframe")
DISPLAY_KEYS = frozenset({"mode", "grid", "appearance", "camera", "clip", "exploded"})
EXPLODED_KEYS = frozenset({"enabled", "amount"})
# Explode's amount when a request names none: the viewer's own default.
EXPLODED_AMOUNT = 0.5
CAMERA_PRESETS = frozenset({"front", "back", "right", "left", "top", "bottom", "iso"})
CAMERA_KEYS = frozenset({"preset", "name", "position", "target", "up", "direction", "zoom", "orthographicHalfHeight"})
# ``--camera authored``: the view the annotation's ink was drawn in, exactly (``authored``).
AUTHORED = "authored"
# The Quick Edit packets an annotation file is looked for beside (``cadgen.viewer`` writes them).
PACKETS = "*.snapshot.json"
# The viewer's annotation file (``@text-to-cad/core`` ``common/annotation.js``) and Measure's marks.
ANNOTATION_KEYS = frozenset({"units", "parts", "marks"})
MARK_KINDS = ("stroke", "area", "text")
MEASUREMENT_KEYS = frozenset({"start", "end", "label", "color"})
MAX_MEASUREMENTS = 64
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


def _amount(text: str) -> float:
    try:
        amount = float(text)
    except ValueError:
        amount = math.nan
    if not 0 <= amount <= 1:
        raise SnapshotRefused(f"exploded={text} is no amount; Explode's amount is 0 (together) to 1 (apart)")
    return amount


def _exploded(value: Any) -> dict[str, Any]:
    """``display.exploded``, checked: ``{"enabled", "amount"}``, the amount 0 to 1 (0.5 unless given)."""
    if not isinstance(value, dict):
        raise SnapshotRefused('display.exploded is {"enabled": true, "amount": 0 to 1}')
    unknown = sorted(set(value) - EXPLODED_KEYS)
    if unknown:
        raise SnapshotRefused(f"display.exploded has no {', '.join(unknown)}; it takes enabled and amount")
    enabled = value.get("enabled", True)
    if not isinstance(enabled, bool):
        raise SnapshotRefused("display.exploded.enabled is true or false")
    amount = value.get("amount", EXPLODED_AMOUNT)
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not 0 <= amount <= 1:
        raise SnapshotRefused("display.exploded.amount is a number from 0 (together) to 1 (apart)")
    return {"enabled": enabled, "amount": float(amount)}


def exploded_amount(display: dict[str, Any]) -> float:
    """How far apart the display moves the parts: 0 when it does not explode them."""
    exploded = display.get("exploded")
    return float(exploded["amount"]) if exploded and exploded.get("enabled") else 0.0


def parse_display(value: str | None, grid: bool) -> dict[str, Any]:
    """``--display``: a mode, ``exploded=AMOUNT`` (or both, ``hidden-line,exploded=0.6``), or the
    viewer's display object (JSON, or a JSON file)."""
    display: dict[str, Any] = {}
    if value:
        text = value.strip()
        if text.startswith("{") or text.endswith(".json"):
            parsed = _json_or_file(text, "--display")
            if not isinstance(parsed, dict):
                raise SnapshotRefused("--display is a mode or a JSON object")
            display = dict(parsed)
        else:
            for token in (part.strip() for part in text.split(",")):
                name, equals, amount = token.partition("=")
                if name == "exploded" and "exploded" not in display:
                    display["exploded"] = {"enabled": True, "amount": _amount(amount) if equals else EXPLODED_AMOUNT}
                elif token in DISPLAY_MODES and "mode" not in display:
                    display["mode"] = token
                else:
                    raise SnapshotRefused(f"--display {token!r} is no display mode; the modes are {', '.join(DISPLAY_MODES)}, "
                                          "and exploded=AMOUNT moves the parts apart (one of each, by commas)")
    if "exploded" in display:
        display["exploded"] = _exploded(display["exploded"])
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
    """``--camera``: a preset, an ``azimuth:elevation`` pair in degrees, a camera JSON object, or
    ``authored``: the camera the annotation's ink was drawn in (``--annotation``'s ``view``)."""
    if not value:
        return None
    text = value.strip()
    if text.lower() == AUTHORED:
        return AUTHORED
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
                          "azimuth:elevation pair, JSON object or authored")


def _load(value: Any, label: str) -> Any:
    """A flag's JSON: the value itself (a function's), JSON text, or the path of a file of it."""
    if not isinstance(value, (str, Path)):
        return value
    text = str(value).strip()
    if text.startswith(("{", "[")):
        try:
            return json.loads(text)
        except json.JSONDecodeError as error:
            raise SnapshotRefused(f"{label} is not JSON: {error}") from None
    path = Path(text).expanduser()
    if not path.is_file():
        raise SnapshotRefused(f"{label} {text}: no such file")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SnapshotRefused(f"{label} {path.name} is not JSON: {error}") from None


def _numbers(value: Any, count: int | None = None) -> bool:
    return isinstance(value, list) and (count is None or len(value) == count) and all(
        isinstance(number, (int, float)) and not isinstance(number, bool) and math.isfinite(number) for number in value)


def _color(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 7 and value.startswith("#") and all(
        digit in "0123456789abcdefABCDEF" for digit in value[1:])


def _packet(found: Any) -> bool:
    """A Quick Edit packet: an annotation and the views it was drawn in -- the ``.snapshot.json``
    the viewer writes (``jobs``), or the request it posts (``views``)."""
    return isinstance(found, dict) and isinstance(found.get("annotation"), dict) and (
        isinstance(found.get("jobs"), list) or isinstance(found.get("views"), list))


def parse_annotation(value: Any) -> dict[str, Any] | None:
    """``--annotation``: the viewer's annotation (its file's path, its JSON, or a Quick Edit packet
    that carries it), checked where a still reads it: its marks and each part's placement. The page
    and the stills draw it; neither refuses it after this."""
    if value is None:
        return None
    found = _load(value, "--annotation")
    if _packet(found):
        found = found["annotation"]
    if not isinstance(found, dict) or not isinstance(found.get("marks"), list):
        raise SnapshotRefused("--annotation is the viewer's annotation file: an object of units, parts and marks")
    unknown = sorted(set(found) - ANNOTATION_KEYS)
    if unknown:
        raise SnapshotRefused(f"--annotation has no {', '.join(unknown)}; an annotation has {', '.join(sorted(ANNOTATION_KEYS))}")
    for name, part in (found.get("parts") or {}).items():
        if not isinstance(part, dict) or not _numbers(part.get("placement"), 16):
            raise SnapshotRefused(f"--annotation: parts.{name}.placement is a 4x4 matrix of 16 numbers")
    for index, mark in enumerate(found["marks"]):
        at = f"--annotation: marks[{index}]"
        if not isinstance(mark, dict) or mark.get("kind") not in MARK_KINDS:
            raise SnapshotRefused(f"{at}.kind is one of {', '.join(MARK_KINDS)}")
        points = mark.get("points")
        if not _numbers(points) or not points or len(points) % 3:
            raise SnapshotRefused(f"{at}.points are x, y, z triples")
        if not _color(mark.get("color")):
            raise SnapshotRefused(f"{at}.color is #rrggbb")
        if mark.get("part") is not None and not isinstance(mark["part"], str):
            raise SnapshotRefused(f"{at}.part is an occurrence id or null")
        if mark["kind"] == "text" and len(points) != 12:
            raise SnapshotRefused(f"{at}: a text mark's points are its four corners")
        if mark.get("view") is not None and not isinstance(mark["view"], str):
            raise SnapshotRefused(f"{at}.view is the label of the view it was drawn in")
    return found


def parse_measurements(value: Any) -> list[dict[str, Any]] | None:
    """``--measurements``: Measure's dimensions, ``[{start, end, label, color}]`` (or one of them,
    as a chip copies it): a file's path, or its JSON."""
    if value is None:
        return None
    found = _load(value, "--measurements")
    found = [found] if isinstance(found, dict) else found
    if not isinstance(found, list) or len(found) > MAX_MEASUREMENTS:
        raise SnapshotRefused(f"--measurements is a list of at most {MAX_MEASUREMENTS} {{start, end, label, color}}")
    marks = []
    for index, mark in enumerate(found):
        at = f"--measurements[{index}]"
        if not isinstance(mark, dict) or set(mark) - MEASUREMENT_KEYS:
            raise SnapshotRefused(f"{at} is {{start, end, label, color}}")
        for key in ("start", "end"):
            if not _numbers(mark.get(key), 3):
                raise SnapshotRefused(f"{at}.{key} is [x, y, z]")
        if not isinstance(mark.get("label"), str):
            raise SnapshotRefused(f"{at}.label is its words")
        if not _color(mark.get("color")):
            raise SnapshotRefused(f"{at}.color is #rrggbb")
        marks.append({"start": list(mark["start"]), "end": list(mark["end"]), "label": mark["label"], "color": mark["color"]})
    return marks


@dataclass
class View:
    """A view an annotation was drawn in, as a Quick Edit packet holds it: its camera (the snapshot
    contract's object: position, target, up, orthographicHalfHeight), its lens (``display.camera``:
    the projection) and its size."""

    camera: Any
    lens: dict[str, Any] | None
    size: tuple[int, int] | None


def _view_of(packet: dict[str, Any], label: str) -> View | None:
    """The view ``label`` names in a packet: a job whose output carries the label (the
    ``.snapshot.json``), or a view of the label (the request the viewer posts)."""
    def size(width: Any, height: Any) -> tuple[int, int] | None:
        return (int(width), int(height)) if isinstance(width, int) and isinstance(height, int) else None

    for job in packet.get("jobs") or []:
        if not isinstance(job, dict):
            continue
        for output in job.get("outputs") or []:
            if isinstance(output, dict) and output.get("label") == label:
                return View(job.get("camera"), (job.get("display") or {}).get("camera"),
                            size(output.get("width"), output.get("height")))
    for view in packet.get("views") or []:
        if isinstance(view, dict) and view.get("label") == label:
            return View(view.get("camera"), (view.get("display") or {}).get("camera"),
                        size(view.get("width"), view.get("height")))
    return None


def authored(camera: Any, annotation: dict[str, Any] | None, source: Any, document: Path) -> tuple[Any, bool, View | None]:
    """The camera a still is drawn with, whether it is drawn exactly so (no fit to the model), and
    the view it came from. ``authored`` is the view the annotation's ink was drawn in: a mark names
    its view by label alone ("view 1", the last mark's), and the view is a job of the Quick Edit
    packet that carried the annotation -- ``source`` itself when it is one (``--annotation`` given a
    ``.snapshot.json``), else the newest packet beside the annotation file that draws ``document``.
    Anything else is itself, fitted. Refused by name when the marks name no view or no packet holds
    it."""
    if camera != AUTHORED:
        return camera, False, None
    if annotation is None:
        raise SnapshotRefused("--camera authored is the view an annotation's ink was drawn in: give --annotation FILE")
    label = next((mark["view"] for mark in reversed(annotation["marks"]) if mark.get("view")), None)
    if label is None:
        raise SnapshotRefused("--camera authored: the annotation's marks name no view they were drawn in")
    given = _load(source, "--annotation") if source is not None else None
    packets: list[dict[str, Any]] = [given] if _packet(given) else []
    beside = None
    if not packets and isinstance(source, (str, Path)) and not str(source).lstrip().startswith("{"):
        beside = Path(source).expanduser().resolve().parent
        for candidate in sorted(beside.glob(PACKETS), key=lambda path: path.stat().st_mtime, reverse=True):
            try:
                found = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if _packet(found) and str(found.get("input") or "") and Path(found["input"]).resolve() == document:
                packets.append(found)
    for packet in packets:
        view = _view_of(packet, label)
        if view is not None and isinstance(view.camera, dict):
            unknown = sorted(set(view.camera) - CAMERA_KEYS)
            if unknown or not all(_numbers(view.camera.get(key), 3) for key in ("position", "target")):
                raise SnapshotRefused(f"--camera authored: {label!r}'s camera is no camera object "
                                      f"({', '.join(unknown) or 'it lacks a position or a target'})")
            return dict(view.camera), True, view
        if view is not None and isinstance(view.camera, str):
            return parse_camera(view.camera), False, view
    where = f"beside {Path(source).name} " if beside is not None else ""
    raise SnapshotRefused(f"--camera authored: the annotation's ink was drawn in {label!r}, and no Quick Edit packet "
                          f"(.snapshot.json) {where}holds that view of {document.name}: give the packet as --annotation")


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


@dataclass
class Still:
    """What a picture is asked to show, checked: the display (mode, grid, clip, Explode, the
    projection), the camera and whether it is drawn exactly so (``exact``: ``--camera authored``,
    no fit), Preview, the size, the pose, the annotation and the measurements drawn over it. The
    one request both renderers draw, and what a renderer process is handed (``as_json``)."""

    display: dict[str, Any] = field(default_factory=dict)
    camera: Any = None
    exact: bool = False
    preview: bool = False
    size: tuple[int, int] = DEFAULT_SIZE
    pose: Any = None
    annotation: dict[str, Any] | None = None
    measurements: list[dict[str, Any]] | None = None

    def as_json(self) -> dict[str, Any]:
        return {"display": self.display, "camera": self.camera, "exact": self.exact, "preview": self.preview,
                "size": list(self.size), "pose": self.pose, "annotation": self.annotation,
                "measurements": self.measurements}

    @staticmethod
    def from_json(found: dict[str, Any]) -> "Still":
        return Still(dict(found.get("display") or {}), found.get("camera"), bool(found.get("exact")),
                     bool(found.get("preview")), tuple(found.get("size") or DEFAULT_SIZE), found.get("pose"),
                     found.get("annotation"), found.get("measurements"))


def refusals(still: Still, structure: dict[str, Any], name: str) -> None:
    """What the document's view does not offer, refused by name before anything renders: Parts and
    Explode colour or move placed parts apart, and a document of one has nothing to part."""
    placed = len(structure.get("instances") or [])
    if placed >= 2 or still.preview:
        return
    count = f"{placed} part{'' if placed == 1 else 's'}"
    if still.display.get("mode") == "parts":
        raise SnapshotRefused(f"display.mode parts colours each placed part apart; {name} places {count}. Use solid.")
    if exploded_amount(still.display) > 0:
        raise SnapshotRefused(f"display.exploded moves a document's parts apart; {name} places {count}. "
                              "Remove display.exploded.")


def _job(kind: str, url: str, document: Path, out: Path, still: Still) -> dict[str, Any]:
    """The snapshot page's render job for ``still``."""
    output: dict[str, Any] = {"path": str(out), "width": still.size[0], "height": still.size[1]}
    if still.camera is not None:
        output["camera"] = still.camera
    job: dict[str, Any] = {"kind": kind, "mode": "view", "display": still.display, "outputs": [output],
                           "resolved": {"kind": kind, "url": url, "inputPath": str(document)}}
    if still.preview:
        job["preview"] = True
    if still.exact:
        job["output"] = {"padding": 0, "tightFrame": False}  # the camera as it was, as a prompt's view replays
    if still.annotation is not None:
        job["annotation"] = still.annotation
    if still.measurements:
        job["measurements"] = still.measurements
    return job


def _mesh_file(document: Path, out: Path, still: Still, started: float) -> dict[str, Any]:
    """A GLB, STL or 3MF drawn by the shared mesh renderers of the snapshot page: the page
    reads the file's bytes as the viewer's mesh renderer does, no engine in between."""
    kind = MESH_KINDS[document.suffix.lower()]
    route = f"/file/{quote(document.name)}"
    result, timings = _render(_job(kind, route, document, out, still), _runtime(), still.size, {route: document})
    drawn = _png(result, out, timings)
    timings["totalMs"] = round((time.perf_counter() - started) * 1000)
    return {"ok": True, "document": str(document), "output": str(out), "width": drawn.get("width"),
            "height": drawn.get("height"), "camera": drawn.get("camera"), "projection": drawn.get("projection"),
            "displayMode": still.display.get("mode", "solid"), "preview": still.preview, "pose": None,
            "exploded": exploded_amount(still.display) or None, "renderer": "chromium",
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
    write_png(out, base64.b64decode(data))
    return drawn


def write_png(out: Path, data: bytes) -> None:
    """The one file a snapshot writes: beside ``out`` first, then moved over it."""
    temporary = out.with_name(f".{out.stem}.{os.getpid()}.tmp.png")
    temporary.write_bytes(data)
    os.replace(temporary, out)


def options(display: Any = None, camera: Any = None, grid: bool = False, width: int | None = None,
            height: int | None = None, size_profile: str | None = None) -> tuple[dict[str, Any], Any, tuple[int, int]]:
    """A snapshot's options as a function gives them -- ``display`` a mode, a
    JSON text or the display object -- checked as the verb checks its flags."""
    if isinstance(display, dict):
        display = json.dumps(display)
    return (parse_display(display, bool(grid)), parse_camera(camera if camera is None or isinstance(camera, str)
                                                              else json.dumps(camera)),
            parse_size(width, height, size_profile))


def draw(source: Any, out: Path, *, display: Any = None, camera: Any = None, grid: bool = False,
         preview: bool = False, pose: Any = None, width: int | None = None, height: int | None = None,
         size_profile: str | None = None, annotation: Any = None, measurements: Any = None) -> dict[str, Any]:
    """A build's document (an ``export.Source``, in this process's memory) drawn into ``out``:
    a model's picture (``Model.snapshot``). Served from memory over the process's mesh
    cache, so nothing the build meshed is meshed again."""
    shown, framed, size = options(display, camera, grid, width, height, size_profile)
    path = source.path or Path(out).with_suffix(".xbf")
    return snapshot(path, Path(out), display=shown, camera=framed, preview=preview,
                    size=None if width is None and height is None and size_profile is None else size, pose=pose,
                    source=source, annotation=parse_annotation(annotation), measurements=parse_measurements(measurements),
                    annotation_source=annotation)


def snapshot(document: Path, out: Path, *, display: dict[str, Any] | None = None, camera: Any = None,
             preview: bool = False, size: tuple[int, int] | None = None, daemon: bool = True,
             pose: Any = None, source: Any = None, annotation: dict[str, Any] | None = None,
             measurements: list[dict[str, Any]] | None = None, annotation_source: Any = None) -> dict[str, Any]:
    """Draw ``document`` -- an ``.xbf`` or a ``.step`` the engine serves and meshes, or a mesh
    file (``.glb``, ``.stl``, ``.3mf``) the page reads itself -- into ``out`` (a ``.png``);
    returns what was drawn and how long each stage took. ``pose``: a configuration's name or
    ``{joint: value}``, the engine placing each link by its pairs from the ground; None draws
    the document as built; a mesh has no mechanism to pose. ``source``: the document is a
    build's, held in this process (``draw``). ``annotation`` (``parse_annotation``'s) and
    ``measurements`` are drawn over the model; ``camera`` ``authored`` is the view the ink was
    drawn in, read from the Quick Edit packet ``annotation_source`` is (or is beside: ``authored``),
    and its size and projection unless ``size`` and ``display.camera`` say otherwise. ``size`` None
    is the default size. Raises ``SnapshotRefused`` for a request it will not draw, before
    rendering."""
    started = time.perf_counter()
    document, out = Path(document).resolve(), Path(out).resolve()
    suffix = document.suffix.lower()
    if suffix not in DOCUMENTS and suffix not in MESH_KINDS:
        raise SnapshotRefused(f"{document.name} is neither a document (.xbf, .step) nor a mesh (.glb, .stl, .3mf)")
    if source is None and not document.is_file():
        raise SnapshotRefused(f"no document {document}")
    if out.suffix.lower() != ".png":
        raise SnapshotRefused(f"a snapshot is a .png; {out.name} is not one")
    framed, exact, view = authored(camera, annotation, annotation_source, document)
    display = dict(display or {})
    if view is not None and view.lens and "camera" not in display:
        display["camera"] = dict(view.lens)  # the view's projection
    size = size or (view.size if view is not None and view.size else DEFAULT_SIZE)
    still = Still(display, framed, exact, preview, tuple(size), pose, annotation, measurements)
    if suffix in MESH_KINDS:
        if pose is not None:
            raise SnapshotRefused(f"--pose poses a document's mechanism; {document.name} is a mesh and has none")
        if exploded_amount(still.display) and suffix != ".glb":
            raise SnapshotRefused(f"display.exploded moves a model's parts apart; {document.name} is one mesh. "
                                  "Remove display.exploded.")
    if not out.parent.is_dir():
        out.parent.mkdir(parents=True, exist_ok=True)
    if suffix in MESH_KINDS:
        return _mesh_file(document, out, still, started)
    return _chromium(document, out, still, daemon, source, started)


def _chromium(document: Path, out: Path, still: Still, daemon: bool, source: Any, started: float) -> dict[str, Any]:
    """A document drawn by the snapshot page: served (and meshed) by the engine, read and drawn by
    the viewer's own scene builder in a headless browser."""
    runtime = _runtime()
    timings: dict[str, Any] = {}
    clock = time.perf_counter()
    served = _Served(document, daemon=daemon, source=source)
    posed = None
    try:
        url = served.url
        if still.pose is not None:
            url, posed = _posed(served, still.pose)
        structure = _get_json(url)
        timings["serveMs"] = round((time.perf_counter() - clock) * 1000)
        refusals(still, structure, document.name)
        # Every part meshed by the engine before the page asks, so the page's load is the bytes alone.
        clock = time.perf_counter()
        meshed = _get_json(f"{url}/mesh", method="POST")
        timings["meshMs"] = round((time.perf_counter() - clock) * 1000)
        unmeshed = _unmeshed(_get_json(url))
        result, page = _render(_job("xbf", url, document, out, still), runtime, still.size)
    finally:
        served.close()
    timings.update(page)
    drawn = _png(result, out, timings)
    timings["totalMs"] = round((time.perf_counter() - started) * 1000)
    return {"ok": True, "document": str(document), "output": str(out), "width": drawn.get("width"),
            "height": drawn.get("height"), "camera": drawn.get("camera"), "projection": drawn.get("projection"),
            "displayMode": still.display.get("mode", "solid"), "preview": still.preview,
            "parts": len(structure["prototypes"]), "instances": len(structure.get("instances") or []),
            "unmeshed": unmeshed, "pose": posed, "exploded": exploded_amount(still.display) or None,
            "renderer": "chromium",
            # The engine's account of the meshing this snapshot asked for: none where it held every part.
            "mesh": {key: meshed.get(key) for key in ("parts", "meshed", "seconds", "triangles", "mode")},
            "bytes": out.stat().st_size, "timings": timings}


def _unmeshed(structure: dict[str, Any]) -> dict[str, list[int]]:
    """The faces the mesher left without triangles, by part, each warned of: the picture lacks them."""
    unmeshed = {prototype.get("name") or prototype["key"]: prototype["unmeshed"]
                for prototype in structure["prototypes"] if prototype.get("unmeshed")}
    for name, faces in unmeshed.items():
        print(f"warning: {name}: the mesher left face{'s' if len(faces) > 1 else ''} "
              f"{', '.join(map(str, faces))} without triangles, so the picture does not draw "
              f"{'them' if len(faces) > 1 else 'it'}", file=sys.stderr)
    return unmeshed


def add_parser(verbs: Any) -> argparse.ArgumentParser:
    """The ``snapshot`` verb of ``cadgen xbf``."""
    parser = verbs.add_parser("snapshot", help="draw a document as the CAD Viewer does, into a PNG")
    parser.add_argument("document", metavar="SRC", help="a document (.xbf, .step), a mesh file (.glb, .stl, .3mf), or "
                                                        "SCRIPT.py::MODEL (built first, drawn from its worker)")
    parser.add_argument("out", metavar="OUT.png", help="the .png to write")
    parser.add_argument("--display", help="a mode (solid, shaded, parts, xray, hidden-line, wireframe), exploded=AMOUNT "
                                          "(the parts moved apart, 0 to 1; with a mode: hidden-line,exploded=0.6), or "
                                          "the display JSON object (mode, grid, appearance, camera, clip, exploded), or "
                                          "a JSON file of it")
    parser.add_argument("--grid", action="store_true", help="draw the measuring grid")
    parser.add_argument("--camera", help="a preset (iso, front, back, left, right, top, bottom), azimuth:elevation, "
                                         "a camera JSON object, or authored: the view --annotation's ink was drawn "
                                         "in, from its Quick Edit packet, exactly")
    parser.add_argument("--preview", action="store_true", help="Preview's photographic look instead of the mode")
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--size-profile", choices=sorted(SIZE_PROFILES))
    parser.add_argument("--pose", help="a configuration's name, or joint values as JSON ('{\"elbow\": 45}'): the "
                                       "mechanism posed there; as built without it")
    parser.add_argument("--annotation", metavar="FILE", help="the viewer's annotation file, or the Quick Edit packet "
                                                              "(.snapshot.json) that carries it: its ink drawn over the "
                                                              "model, on the parts it was drawn on")
    parser.add_argument("--measurements", metavar="FILE", help="Measure's dimensions (a JSON list of {start, end, label, "
                                                                "color}), drawn over the model")
    parser.add_argument("--no-daemon", action="store_true", help="serve the document from this process")
    parser.add_argument("--json", action="store_true", help="print what was drawn and its timings as JSON")
    return parser


def run(args: argparse.Namespace) -> int:
    if "::" in args.document or args.document.endswith(".py"):
        return _run_model(args)
    try:
        display = parse_display(args.display, args.grid)
        camera = parse_camera(args.camera)
        size = parse_size(args.width, args.height, args.size_profile)
        pose = parse_pose(args.pose)
        given = args.width is not None or args.height is not None or args.size_profile is not None
        report = snapshot(Path(args.document), Path(args.out), display=display, camera=camera, preview=args.preview,
                          size=size if given else None, daemon=not args.no_daemon, pose=pose,
                          annotation=parse_annotation(args.annotation), measurements=parse_measurements(args.measurements),
                          annotation_source=args.annotation)
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


def _run_model(args: argparse.Namespace) -> int:
    """``cadgen xbf snapshot SCRIPT.py::MODEL OUT.png``: the model built in its worker, its
    document drawn from that worker's memory (``api.snapshot``)."""
    from cadgen.engine import api

    try:
        drawn = api.snapshot(args.document, args.out, display=args.display, camera=args.camera, grid=args.grid,
                             preview=args.preview, pose=args.pose, width=args.width, height=args.height,
                             size_profile=args.size_profile, daemon=not args.no_daemon, annotation=args.annotation,
                             measurements=args.measurements)
    except (SnapshotRefused, ValueError) as error:
        print(f"cadgen xbf snapshot: {error}", file=sys.stderr)
        return 2
    except api.EngineError as error:
        print(f"cadgen xbf snapshot: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(drawn.report))
    else:
        print(f"wrote {args.out} ({drawn.width}x{drawn.height}) in {drawn.report['timings']['totalMs'] / 1000:.2f}s")
    return 0
