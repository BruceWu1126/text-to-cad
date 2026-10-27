"""Browser snapshot dispatch, authored-time bounds and atomic delivery without a GPU."""
import asyncio
import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from cadgen.engine import snapshot
from cadgen._internal import snapshot_browser as browser


class BrowserSnapshotContractTest(unittest.TestCase):
    def test_animation_frames_are_authored_seconds_and_selection_is_explicit(self):
        routines = [{"id": "glb:0", "name": "swing", "duration": 1.25},
                    {"id": "glb:1", "name": "slide", "duration": 2}]
        selected = browser.select_routine(routines, "swing", 6)
        self.assertEqual(selected["frames"], 8)
        self.assertEqual(selected["times"], [i / 6 for i in range(8)])
        self.assertEqual(browser.animation_request(True, 30), {"fps": 30})
        for routine in (None, "missing"):
            with self.assertRaisesRegex(snapshot.SnapshotRefused, "Available routines"):
                browser.select_routine(routines, routine, 6)
        with self.assertRaisesRegex(snapshot.SnapshotRefused, "Available routines"):
            browser.select_routine([routines[0], {**routines[0], "id": "glb:2"}], "swing", 6)
        with self.assertRaisesRegex(snapshot.SnapshotRefused, "3600 frames"):
            browser.select_routine([{**routines[0], "duration": 1000}], None, 60)

    def test_bad_options_are_refused_before_rendering(self):
        for animation, fps in ((True, 0), (True, 61), (True, True), ("", 30), ([], 30), (None, 12)):
            with self.subTest(animation=animation, fps=fps), self.assertRaises(snapshot.SnapshotRefused):
                snapshot.validate_animation(animation, fps)
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "mesh.glb"
            source.write_bytes(b"source")
            with self.assertRaisesRegex(snapshot.SnapshotRefused, "combine with pose"):
                snapshot.snapshot(source, Path(folder) / "movie.mp4", animation=True, pose={})
            with self.assertRaisesRegex(snapshot.SnapshotRefused, r"\.mp4"):
                snapshot.snapshot(source, Path(folder) / "movie.png", animation=True)

    def test_dispatch_preserves_full_preview_configuration_and_animation_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "mesh.glb"
            source.write_bytes(b"source")
            for preview in ({}, {"lighting": {"quality": "final", "rotation": 45}}):
                def render(document, out, still, **kwargs):
                    self.assertEqual(still.preview, preview)
                    self.assertEqual((still.animation, still.fps), ("swing", 6))
                    self.assertEqual(still.size, (1280, 720))
                    return {"width": 1280, "height": 720}
                with patch.object(snapshot, "BROWSER_RENDER", render):
                    snapshot.snapshot(source, Path(folder) / "movie.mp4", preview=preview, animation="swing", fps=6)
            restored = snapshot.Still.from_json(snapshot.Still(preview={}, animation=True, fps=6).as_json())
            self.assertEqual(restored.preview, {})
            self.assertEqual((restored.animation, restored.fps), (True, 6))

    def test_browser_failure_keeps_existing_output_and_removes_only_its_stage(self):
        from cadgen import assets

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runtime = root / "runtime"
            runtime.mkdir()
            for name in ("render.html", "snapshot-render.js"):
                (runtime / name).write_text("fixture", encoding="utf-8")
            source, out = root / "source.glb", root / "picture.png"
            source.write_bytes(b"source")
            out.write_bytes(b"earlier")
            released = []
            @contextlib.contextmanager
            def served(*args):
                try:
                    yield {"kind": "glb", "url": "http://127.0.0.1/input/source.glb"}, None
                finally:
                    released.append(True)
            async def fail(job, runtime, document, engine_origin, stage):
                self.assertTrue(job["preview"], "an empty preview mapping still enables Preview")
                stage.write_bytes(b"partial")
                failures = browser._BrowserFailures()
                route = SimpleNamespace(request=SimpleNamespace(url=browser.PAGE_ORIGIN + "/input/missing_texture.png"), fulfill=AsyncMock())
                await browser._route_resource(route, runtime, document, engine_origin, {}, failures)
                route.fulfill.assert_awaited_once_with(status=404, body="snapshot resource not found")
                failures.check()
            with patch.object(assets, "snapshot_dist_dir", return_value=runtime), patch.object(browser, "_source", served), patch.object(browser, "_render", fail):
                with self.assertRaisesRegex(RuntimeError, "Missing required resource.*missing_texture"):
                    browser.render(source, out, snapshot.Still(preview={}))
            self.assertEqual(out.read_bytes(), b"earlier")
            self.assertEqual(released, [True], "the private source is released after a resource failure")
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["picture.png", "runtime", "source.glb"])

    def test_packet_keeps_view_and_override_preview_objects_including_empty_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "mesh.glb"
            source.write_bytes(b"source")
            packet = {"input": str(source), "jobs": [{"preview": {"lighting": {"quality": "final"}},
                       "outputs": [{"path": str(Path(folder) / "view.png")}]}]}
            found = snapshot.job_pictures(packet)
            self.assertEqual(found[0][2].preview, {"lighting": {"quality": "final"}})
            found = snapshot.job_pictures(packet, preview={})
            self.assertEqual(found[0][2].preview, {})
            self.assertEqual(found[0][2].display.get("camera", {}).get("projection", "perspective"), "perspective")

    def test_robot_source_reuses_viewer_payload_and_owns_primitive_routes(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "robot.urdf"
            source.write_text('<robot name="fixture"><link name="body"><visual><geometry>'
                              '<box size="0.1 0.2 0.3"/></geometry></visual></link></robot>', encoding="utf-8")
            with browser._source(source, None) as (resolved, engine):
                self.assertIsNone(engine)
                self.assertEqual(resolved["controls"], {})
                visual = resolved["robot"]["visuals"][0]
                url = visual["mesh"]["url"]
                self.assertTrue(url.startswith(browser.PAGE_ORIGIN + "/model-assets/objects/"))
                data = resolved["_assets"][url.removeprefix(browser.PAGE_ORIGIN)]
                self.assertEqual(data[:4], b"glTF")

    def test_video_flattens_fractional_alpha_over_configured_matte(self):
        from PIL import Image

        picture = Image.new("RGBA", (2, 1))
        picture.putdata([(255, 0, 0, 128), (0, 255, 0, 0)])
        flattened = browser._video_rgb(picture, {"previewSettings": {"background": {"color": "#0000ff"}}})
        self.assertEqual([flattened.getpixel((x, 0)) for x in range(2)], [(128, 0, 127), (0, 0, 255)])
        self.assertEqual(browser._video_rgb(picture, {"display": {"appearance": "dark"}}).getpixel((1, 0)), (32, 34, 38))

    def test_cli_accepts_auto_or_named_animation_and_full_preview_settings(self):
        parser = snapshot.build_parser()
        auto = parser.parse_args(["part.xbf", "part.mp4", "--animation", "--fps", "6"])
        self.assertIs(auto.animation, True)
        self.assertEqual(auto.fps, 6)
        named = parser.parse_args(["part.glb", "part.mp4", "--animation", "slide", "--preview", '{"lighting":{"quality":"final"}}'])
        self.assertEqual(named.animation, "slide")
        self.assertEqual(named.preview, {"lighting": {"quality": "final"}})


class BrowserResourceContractTest(unittest.IsolatedAsyncioTestCase):
    async def test_external_model_resources_fail_without_fetching_and_favicon_is_optional(self):
        with tempfile.TemporaryDirectory() as folder:
            failures = browser._BrowserFailures()
            route = SimpleNamespace(request=SimpleNamespace(url="https://example.invalid/texture.png"), abort=AsyncMock(), fetch=AsyncMock())
            await browser._route_resource(route, Path(folder), Path(folder) / "source.glb", None, {}, failures)
            route.fetch.assert_not_awaited()
            route.abort.assert_awaited_once_with("blockedbyclient")
            with self.assertRaisesRegex(RuntimeError, "Blocked external resource.*texture.png"):
                failures.check()
            harmless = browser._BrowserFailures()
            favicon = SimpleNamespace(request=SimpleNamespace(url=browser.PAGE_ORIGIN + "/favicon.ico"), fulfill=AsyncMock())
            await browser._route_resource(favicon, Path(folder), Path(folder) / "source.glb", None, {}, harmless)
            favicon.fulfill.assert_awaited_once_with(status=204, body="")
            harmless.resource_failure(favicon.request.url, "not found")
            harmless.check()

    async def test_page_failure_cancels_a_waiting_render_and_bounds_diagnostics(self):
        failures = browser._BrowserFailures()
        cancelled = []
        async def pending():
            try:
                failures.add("Page error", "texture decode failed")
                await asyncio.Future()
            finally:
                cancelled.append(True)
        with self.assertRaisesRegex(RuntimeError, "Page error: texture decode failed"):
            await failures.wait(pending(), 300)
        self.assertEqual(cancelled, [True])
        for i in range(100):
            failures.add("Browser error", str(i) + "x" * 5000)
        self.assertEqual(len(failures.messages), 12)
        self.assertLessEqual(max(map(len, failures.messages)), 1024)


if __name__ == "__main__":
    unittest.main()
