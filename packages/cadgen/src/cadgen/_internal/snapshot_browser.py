"""Packaged Viewer stills and authored-time video, injected into the standalone engine.

The page, workers, weights and encoder are installed assets. No npm, system ffmpeg or
browser download is attempted. An in-process holder borrows a document and its meshes;
only the explicitly requested picture or movie is committed, after rendering succeeds.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import math
import mimetypes
import sys
import time
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

PAGE_ORIGIN = "http://127.0.0.1:9747"
FRAME_SECONDS = 300
TOTAL_SECONDS = 3600
MAX_FRAMES = 3600
MAX_VIDEO_SIZE = (1920, 1080)


def animation_request(value, fps):
    from cadgen.engine.snapshot import SnapshotRefused

    if value is None or value is False:
        if fps != 30:
            raise SnapshotRefused("fps requires animation")
        return None
    if value is not True and (not isinstance(value, str) or not value.strip()):
        raise SnapshotRefused("animation is true (the only routine) or an authored routine's name")
    if isinstance(fps, bool) or not isinstance(fps, int) or not 1 <= fps <= 60:
        raise SnapshotRefused("fps is an integer from 1 to 60")
    return {"fps": fps, **({"routine": value.strip()} if isinstance(value, str) else {})}


def select_routine(routines, requested, fps):
    from cadgen.engine.snapshot import SnapshotRefused

    selected = [r for r in routines if r.get("name") == requested or r.get("id") == requested] if requested else routines
    if len(selected) != 1:
        names = ", ".join(str(r.get("name") or r.get("id")) for r in routines) or "none"
        raise SnapshotRefused(f"animation requires one authored routine; select its name. Available routines: {names}")
    routine = selected[0]
    duration = routine.get("duration")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        raise SnapshotRefused("the authored animation has no finite positive duration")
    count = math.ceil(duration * fps)
    if count > MAX_FRAMES:
        raise SnapshotRefused(f"animation exceeds {MAX_FRAMES} frames; choose a lower fps")
    return {"id": routine["id"], "name": routine["name"], "duration": duration, "fps": fps,
            "frames": count, "times": [i / fps for i in range(count)]}


def _video_rgb(picture, job):
    """H.264 is opaque: composite straight RGBA over the chosen studio matte."""
    from PIL import Image, ImageColor

    settings = job.get("previewSettings") or {}
    appearance = settings.get("appearance", settings.get("studio", job.get("display", {}).get("appearance", "light")))
    color = (settings.get("background") or {}).get("color") or ("#202226" if appearance == "dark" else "#ffffff")
    matte = Image.new("RGBA", picture.size, (*ImageColor.getrgb(color), 255))
    return Image.alpha_composite(matte, picture.convert("RGBA")).convert("RGB")


def _encoder():
    import imageio_ffmpeg

    folder = Path(imageio_ffmpeg.__file__).resolve().parent / "binaries"
    candidates = [p for p in folder.glob("ffmpeg-*") if p.is_file()]
    if len(candidates) != 1:
        raise RuntimeError("cadgen's bundled FFmpeg is missing; reinstall imageio-ffmpeg")
    return str(candidates[0])


def _native_hidden_parts(structure, display, name):
    from cadgen.engine.snapshot import hidden_instances

    return [f"i{index}" for index in sorted(hidden_instances(structure, display, name))]


def _robot_hidden_visuals(payload, display):
    """Robot visual IDs are the existing scene contract, not inferred mesh indices."""
    from cadgen.engine.snapshot import SnapshotRefused

    every = {visual["id"] for visual in payload.get("visuals", [])}
    for field in ("hidden", "isolated"):
        unknown = set(display.get(field) or []) - every
        if unknown:
            raise SnapshotRefused(f"display.{field}: unknown robot visual IDs {', '.join(sorted(unknown))}; "
                                  f"available IDs: {', '.join(sorted(every))}")
    shown = (set(display["isolated"]) if display.get("isolated") is not None else every) - set(display.get("hidden") or [])
    if not shown:
        raise SnapshotRefused("display.hidden and display.isolated leave no robot visuals to draw")
    return sorted(every - shown)


class _NoListener:
    """Only the Server lifecycle shape; no socket, listener thread or HTTP app."""
    server_address = ("127.0.0.1", 0)

    def server_close(self):
        pass


class _NativeDocument:
    def __init__(self, server, path):
        self.server = server
        self.path = path

    def allows(self, path):
        return path in ("/api/look", "/api/look/environment", self.path) or path.startswith(self.path + "/")


@contextlib.contextmanager
def _source(document, source, pose=None, display=None):
    display = display or {}
    """An in-memory native document, or a mesh read unchanged by its viewer loader."""
    if source is None and document.suffix.lower() not in (".xbf", ".step", ".stp"):
        resolved = {"kind": document.suffix.lower().lstrip("."), "url": PAGE_ORIGIN + "/input/" + quote(document.name),
                    "inputPath": str(document)}
        if (display.get("hidden") or display.get("isolated") is not None) and document.suffix.lower() not in (".urdf", ".sdf", ".srdf"):
            from cadgen.engine.snapshot import SnapshotRefused
            raise SnapshotRefused(f"browser snapshots cannot resolve hidden/isolated references for {document.suffix}; "
                                  "use a native document or robot visual IDs")
        if document.suffix.lower() in (".urdf", ".sdf", ".srdf"):
            from cadgen.robot_payload import (locate_robot_payload, primitive_glb_bytes, robot_control_values,
                                              robot_payload_bytes, RobotReadError)
            from cadgen.engine.snapshot import SnapshotRefused
            assets = {}
            def file_url(path):
                url = "/model-assets/" + str(len(assets)) + "/" + quote(Path(path).name)
                assets[url] = Path(path)
                return PAGE_ORIGIN + url
            def object_url(digest):
                data = primitive_glb_bytes(digest)
                if data is None:
                    raise SnapshotRefused("a robot primitive mesh is unavailable")
                url = "/model-assets/objects/" + digest + ".glb"
                assets[url] = data
                return PAGE_ORIGIN + url
            try:
                payload = json.loads(robot_payload_bytes(document))
                resolved["controls"] = robot_control_values(payload, pose)
                if display.get("hidden") or display.get("isolated") is not None:
                    resolved["selection"] = {"hide": _robot_hidden_visuals(payload, display)}
                resolved["robot"] = locate_robot_payload(payload, file_url=file_url, object_url=object_url)
                resolved["_assets"] = assets
            except RobotReadError as error:
                raise SnapshotRefused(str(error)) from None
        yield resolved, None
        return
    from cadgen.engine.serve import MESHES, Server

    server = Server(port=0, mesh_mode="inline", cache=MESHES, http_factory=lambda _server, _host, _port: _NoListener())
    try:
        if source is not None:
            ident = server.put(document, source.structure,
                               {p["key"]: source.shape_data(p["key"]) for p in source.structure["prototypes"]})
        else:
            ident = server.add(document)
        doc = server.document(ident)
        structure = server.structure(doc)
        hidden = _native_hidden_parts(structure, display, document.name)
        values = None
        if pose is not None:
            from cadgen.engine import mechanism
            from cadgen.engine.snapshot import SnapshotRefused
            try:
                values = mechanism.resolve(structure.get("kinematics") or {}, pose)[0]
            except mechanism.KinematicsError as error:
                raise SnapshotRefused(str(error)) from None
        server.mesh_all(doc, None)
        path = "/api/doc/" + ident
        yield {"kind": "xbf", "url": PAGE_ORIGIN + path, "inputPath": str(document), "hiddenParts": hidden,
               **({"poseValues": values} if values is not None else {})}, _NativeDocument(server, path)
    finally:
        server.stop()


def render(document, out, still, *, source=None, progress=None):
    """The engine hook: render one PNG or MP4 with atomic output and bounded work."""
    from cadgen.assets import snapshot_dist_dir, runtime_build_hint
    from cadgen.engine.atomic_replace import staging, replace_atomic
    from cadgen.engine.progress import say
    from cadgen.engine.snapshot import SnapshotRefused

    animation = animation_request(still.animation, still.fps)
    if animation and (still.pose is not None or still.annotation or still.measurements or still.transparent):
        raise SnapshotRefused("animation cannot combine pose, annotation, measurements or transparent output")
    width, height = still.drawn
    if animation and (width > MAX_VIDEO_SIZE[0] or height > MAX_VIDEO_SIZE[1] or width % 2 or height % 2):
        raise SnapshotRefused("MP4 dimensions must be even and at most 1920 by 1080")
    runtime = snapshot_dist_dir()
    if not (runtime / "render.html").is_file() or not (runtime / "snapshot-render.js").is_file():
        raise RuntimeError(runtime_build_hint(runtime))
    started = time.monotonic()
    say("scene", detail=document.name)
    with _source(document, source, still.pose, still.display) as (resolved, native):
        pose = resolved.pop("poseValues", still.pose)
        job = {"kind": resolved["kind"], "mode": "view", "display": {key: value for key, value in still.display.items() if key not in ("hidden", "isolated")},
               "camera": still.camera, "outputs": [{"path": str(out), "width": width, "height": height,
                                                     **({"camera": still.camera} if still.camera else {})}],
               "resolved": resolved, "preview": still.preview is True or isinstance(still.preview, dict),
               "output": {"padding": still.padding, "tightFrame": still.tight, "transparent": still.transparent},
               **({"previewSettings": still.preview} if isinstance(still.preview, dict) else {}),
               **({"jointValues": pose} if pose is not None else {}),
               **({"annotation": still.annotation} if still.annotation else {}),
               **({"measurements": still.measurements} if still.measurements else {}),
               **({"animation": animation} if animation else {})}
        with staging(out) as stage:
            report = asyncio.run(asyncio.wait_for(_render(job, runtime, document, native, stage), TOTAL_SECONDS))
            if not stage.is_file() or stage.stat().st_size == 0:
                raise RuntimeError("the snapshot renderer wrote no output")
            replace_atomic(stage, out)
    report.update({"output": str(out), "document": str(document), "bytes": out.stat().st_size,
                   "width": width, "height": height,
                   "displayMode": still.display.get("mode", "solid")})
    report.setdefault("preview", job["preview"])
    report.setdefault("timings", {})["totalMs"] = round((time.monotonic() - started) * 1000)
    return report


async def _launch(playwright, *, preview=False):
    args = []
    if sys.platform == "darwin":
        args.append("--use-angle=metal")
    elif sys.platform.startswith("linux") and not preview:
        args += ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
    # Playwright may have only its headless shell installed; executable_path points
    # to the full browser, so let launch resolve the appropriate managed binary.
    try:
        return await playwright.chromium.launch(headless=True, args=args, timeout=45000)
    except Exception as error:
        if "Executable doesn't exist" not in str(error):
            raise
        failures = [str(error).splitlines()[0]]
    for channel in ("chrome", "msedge"):
        try:
            return await playwright.chromium.launch(channel=channel, headless=True, args=args, timeout=45000)
        except Exception as error:
            if "not found" not in str(error) and "Executable doesn't exist" not in str(error):
                raise
            failures.append(str(error).splitlines()[0])
    raise RuntimeError("Snapshot needs an installed Chromium browser (Playwright Chromium, Chrome or Edge); "
                       "run cadgen doctor --install-browser. No browser is downloaded during rendering. " + "; ".join(failures))


def _favicon(url):
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}" == PAGE_ORIGIN and parsed.path == "/favicon.ico"


class _BrowserFailures:
    """Bound diagnostics and wake a render whose loader swallowed an asset error."""
    def __init__(self):
        self.messages = []
        self.changed = asyncio.Event()

    def add(self, kind, detail):
        message = f"{kind}: {detail}"[:1024]
        if len(self.messages) < 12 and message not in self.messages:
            self.messages.append(message)
        self.changed.set()

    def resource_failure(self, url, reason):
        if not _favicon(url):
            self.add("Required resource failed", f"{url}: {reason}")

    def check(self):
        if self.messages:
            raise RuntimeError("Snapshot browser failed: " + "; ".join(self.messages))

    async def wait(self, awaitable, timeout):
        task = asyncio.ensure_future(awaitable)
        failed = asyncio.create_task(self.changed.wait())
        try:
            done, _pending = await asyncio.wait((task, failed), timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            self.check()
            if task not in done:
                raise TimeoutError(f"snapshot browser exceeded {timeout} seconds")
            return await task
        finally:
            for pending in (task, failed):
                if not pending.done():
                    pending.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await pending


async def _route_resource(handle, runtime, document, native, owned_assets, failures):
    parsed = urlparse(handle.request.url)
    if _favicon(handle.request.url):
        await handle.fulfill(status=204, body="")
        return
    if f"{parsed.scheme}://{parsed.netloc}" != PAGE_ORIGIN:
        failures.add("Blocked external resource", handle.request.url)
        await handle.abort("blockedbyclient")
        return
    if parsed.path.startswith("/api/"):
        if native is None or getattr(handle.request, "method", "GET") != "GET" or not native.allows(parsed.path):
            failures.add("Blocked native document request", handle.request.url)
            await handle.fulfill(status=403, body="snapshot document route is not admitted")
            return
        status, data = await asyncio.to_thread(native.server.read, parsed.path + ("?" + parsed.query if parsed.query else ""))
        if status >= 400:
            failures.resource_failure(handle.request.url, f"HTTP {status}")
        part = parsed.path.removeprefix(native.path + "/part/")
        content_type = ("image/vnd.radiance" if parsed.path == "/api/look/environment" else
                        "application/octet-stream" if parsed.path.startswith(native.path + "/part/") and "/" not in part else
                        "application/json")
        await handle.fulfill(status=status, body=data, content_type=content_type)
        return
    asset = owned_assets.get(parsed.path)
    if isinstance(asset, bytes):
        await handle.fulfill(body=asset, content_type="model/gltf-binary")
        return
    if isinstance(asset, Path):
        await handle.fulfill(path=str(asset), content_type=mimetypes.guess_type(asset)[0] or "application/octet-stream")
        return
    for url, candidate in owned_assets.items():
        if not isinstance(candidate, Path):
            continue
        prefix = url.rsplit("/", 1)[0] + "/"
        if parsed.path.startswith(prefix):
            dependency = (candidate.parent / unquote(parsed.path[len(prefix):])).resolve()
            if dependency.is_relative_to(candidate.parent.resolve()) and dependency.is_file():
                await handle.fulfill(path=str(dependency), content_type=mimetypes.guess_type(dependency)[0] or "application/octet-stream")
                return
    name = unquote(parsed.path).lstrip("/")
    folder = document.parent if name.startswith("input/") else runtime
    relative = name.removeprefix("input/") if name.startswith("input/") else name
    found = (folder / relative).resolve()
    if not found.is_relative_to(folder.resolve()) or not found.is_file():
        failures.add("Missing required resource", handle.request.url)
        await handle.fulfill(status=404, body="snapshot resource not found")
        return
    await handle.fulfill(path=str(found), content_type=mimetypes.guess_type(found)[0] or "application/octet-stream")

async def _render(job, runtime, document, native, stage):
    from playwright.async_api import async_playwright
    from cadgen.engine.progress import say
    from PIL import Image

    size = job["outputs"][0]
    encoder = None
    errors = bytearray()
    owned_assets = job["resolved"].pop("_assets", {})
    async with async_playwright() as playwright:
        browser = await _launch(playwright, preview=job["preview"])
        try:
            context = await browser.new_context(viewport={"width": size["width"], "height": size["height"]}, device_scale_factor=1)
            page = await context.new_page()
            failures = _BrowserFailures()
            context.on("requestfailed", lambda request: failures.resource_failure(request.url, request.failure or "request failed"))
            context.on("response", lambda response: failures.resource_failure(response.url, f"HTTP {response.status}") if response.status >= 400 else None)
            page.on("pageerror", lambda error: failures.add("Page error", str(error)))
            page.on("console", lambda message: failures.add("Browser error", message.text) if message.type == "error" and not _favicon(message.location.get("url", "")) else None)
            async def route(handle):
                try:
                    await _route_resource(handle, runtime, document, native, owned_assets, failures)
                except Exception as error:
                    failures.add("Resource delivery failed", f"{handle.request.url}: {error}")
                    with contextlib.suppress(Exception):
                        await handle.abort("failed")
            await context.route("**/*", route)
            await failures.wait(page.goto(PAGE_ORIGIN + "/render.html", wait_until="load", timeout=FRAME_SECONDS * 1000), FRAME_SECONDS)
            await failures.wait(page.wait_for_function("typeof window.__snapshotSession?.open === 'function'", timeout=FRAME_SECONDS * 1000), FRAME_SECONDS)
            opened = await failures.wait(page.evaluate("job => window.__snapshotSession.open(job)", job), FRAME_SECONDS)
            movie = select_routine(opened.get("routines", []), job["animation"].get("routine"), job["animation"]["fps"]) if job.get("animation") else None
            if movie:
                encoder = await asyncio.create_subprocess_exec(_encoder(), "-y", "-loglevel", "error", "-f", "rawvideo", "-vcodec", "rawvideo",
                    "-s", f"{size['width']}x{size['height']}", "-pix_fmt", "rgb24", "-r", str(movie["fps"]), "-i", "pipe:0", "-an",
                    "-vcodec", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(stage),
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
                async def stderr():
                    while chunk := await encoder.stderr.read(8192):
                        errors.extend(chunk)
                        if len(errors) > 65536:
                            del errors[:-65536]
                stderr_task = asyncio.create_task(stderr())
            report = {}
            for index, at in enumerate(movie["times"] if movie else [0]):
                say("draw", detail=f"{movie['name']} at {at:g}s" if movie else "", count=[index, movie["frames"]] if movie else None)
                frame = await failures.wait(page.evaluate("frame => window.__snapshotSession.renderFrame(frame)",
                    {"routine": movie["id"] if movie else None, "time": at, "outputIndex": 0}), FRAME_SECONDS)
                image = frame.get("image", "")
                if not isinstance(image, str) or not image.startswith("data:image/png;base64,") or len(image) > 128 * 1024 * 1024:
                    raise RuntimeError("the snapshot renderer returned no bounded PNG frame")
                data = base64.b64decode(image.partition(",")[2], validate=True)
                with Image.open(io.BytesIO(data)) as picture:
                    if picture.format != "PNG" or picture.size != (size["width"], size["height"]):
                        raise RuntimeError("snapshot frame dimensions or format changed")
                    if movie:
                        encoder.stdin.write(_video_rgb(picture, job).tobytes())
                    else:
                        picture.verify()
                        stage.write_bytes(data)
                if movie:
                    await asyncio.wait_for(encoder.stdin.drain(), FRAME_SECONDS)
                if not report:
                    report = dict(frame.get("report") or {})
            if encoder:
                encoder.stdin.close()
                if await asyncio.wait_for(encoder.wait(), 60) != 0:
                    raise RuntimeError("MP4 encoder failed: " + errors.decode("utf-8", "replace"))
                await stderr_task
                report["animation"] = movie
            failures.check()
            say("write", detail=stage.name)
            await failures.wait(page.evaluate("() => window.__snapshotSession.close()"), 10)
            failures.check()
            return report
        finally:
            if encoder and encoder.returncode is None:
                encoder.kill()
                await encoder.wait()
            await asyncio.wait_for(browser.close(), 10)
