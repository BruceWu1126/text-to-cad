"""A Quick Edit prompt's files: its views as a snapshot job, its annotation, and their renders.

The route over a real server, with the render stubbed (snapshot's own rendering is
the snapshot suites'): what is written, the 202 -> 200 flow, the refusals, and that
the files are served from the prompt-files directory and nowhere else.
"""

from __future__ import annotations

import http.client
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from cadgen.snapshot_core import SnapshotError
from cadgen.viewer import handler as handler_module
from cadgen.viewer.http_app import create_cad_app
from cadgen.viewer.prompt_files import PromptFiles, WarmSnapshotSession

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
ANNOTATION = {"units": "mm", "parts": {}, "marks": [
    {"id": "1", "kind": "stroke", "part": None, "points": [0, 0, 0, 1, 1, 1], "color": "#ff2d55", "width": 2}]}
CAMERA = {"position": [10, -10, 10], "target": [0, 0, 0], "up": [0, 0, 1]}


class PromptFilesRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.model = self.tmp / "part.stl"
        self.model.write_text("solid part\nendsolid part\n", encoding="utf-8")
        self.files = self.tmp / "prompt-files"
        self.release = threading.Event()
        self.failure: Exception | None = None
        self.rendered: list[Path] = []
        app = create_cad_app(host="127.0.0.1", port=0)
        app.prompt_files = PromptFiles(render=self.render, directory=self.files, retry_ms=5)
        server = handler_module.serve(app, "127.0.0.1", 0)
        self.port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(self.release.set)

    def render(self, job_path: Path):
        """What snapshot does with the job file: writes each output's PNG."""
        self.release.wait(10)
        self.rendered.append(job_path)
        if self.failure is not None:
            raise self.failure
        for job in json.loads(job_path.read_text(encoding="utf-8"))["jobs"]:
            for output in job["outputs"]:
                Path(output["path"]).write_bytes(PNG)
        return SimpleNamespace(ok=True, warnings=())

    def call(self, method: str, path: str, body=None, *, guard: bool = True) -> tuple[int, bytes, str]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"x-cadgen-viewer": "1", "content-type": "application/json"} if guard else {}
        try:
            connection.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
            response = connection.getresponse()
            return response.status, response.read(), response.getheader("content-type") or ""
        finally:
            connection.close()

    def request(self, **overrides) -> dict:
        return {"name": "part.stl", "input": str(self.model), "annotation": ANNOTATION, "views": [
            {"label": "current", "camera": CAMERA, "display": {"mode": "render"}, "width": 640, "height": 480},
            {"label": "View 1", "camera": "iso"},
        ], **overrides}

    def finished(self, render_id: str) -> tuple[int, dict]:
        while True:
            status, body, _ = self.call("GET", f"/__cad/prompt-files?id={render_id}")
            if status != 202:
                return status, json.loads(body)

    def test_a_prompt_writes_its_sketch_and_a_job_snapshot_renders_then_answers_its_images(self) -> None:
        status, body, _ = self.call("POST", "/__cad/prompt-files", self.request())
        accepted = json.loads(body)
        self.assertEqual((status, set(accepted)), (202, {"ok", "id", "retryMs"}))
        status, body, _ = self.call("GET", f"/__cad/prompt-files?id={accepted['id']}")
        self.assertEqual((status, json.loads(body)), (202, accepted))

        self.release.set()
        status, answer = self.finished(accepted["id"])
        job_path, annotation_path = self.files / "part-1.job.json", self.files / "part-1.annotation.json"
        current, view_1 = self.files / "part-1-current.png", self.files / "part-1-view-1.png"
        self.assertEqual((status, answer), (200, {"ok": True, "job": str(job_path), "annotation": str(annotation_path), "images": [
            {"label": "current", "path": str(current)}, {"label": "View 1", "path": str(view_1)}]}))
        self.assertEqual(self.rendered, [job_path])
        self.assertEqual(json.loads(annotation_path.read_text(encoding="utf-8")), ANNOTATION)
        common = {"input": str(self.model), "annotation": str(annotation_path)}
        settings = {"output": {"padding": 0, "tightFrame": False}}
        self.assertEqual(json.loads(job_path.read_text(encoding="utf-8")), {"jobs": [
            {**common, "camera": CAMERA, "display": {"mode": "render"}, **settings,
             "outputs": [{"path": str(current), "label": "current", "width": 640, "height": 480}]},
            {**common, "camera": "iso", **settings, "outputs": [{"path": str(view_1), "label": "View 1"}]},
        ]})

        status, png, content_type = self.call("GET", "/__cad/prompt-files/part-1-current.png")
        self.assertEqual((status, png, content_type), (200, PNG, "image/png"))
        status, _, content_type = self.call("GET", "/__cad/prompt-files/part-1.job.json")
        self.assertEqual((status, content_type), (200, "application/json; charset=utf-8"))

    def test_a_prompts_measurements_are_drawn_over_every_view_its_job_renders(self) -> None:
        self.release.set()
        mark = {"start": [0, 0, 0], "end": [10, 0, 0], "label": "10.00 mm", "color": "#d59090"}
        _, body, _ = self.call("POST", "/__cad/prompt-files", self.request(annotation=None, measurements=[mark]))
        self.finished(json.loads(body)["id"])
        jobs = json.loads((self.files / "part-1.job.json").read_text(encoding="utf-8"))["jobs"]
        self.assertEqual([job["measurements"] for job in jobs], [[mark], [mark]])

    def test_a_prompt_without_a_sketch_names_none_and_the_next_prompt_is_numbered_on(self) -> None:
        self.release.set()
        _, first, _ = self.call("POST", "/__cad/prompt-files", self.request(annotation=None))
        _, second, _ = self.call("POST", "/__cad/prompt-files", self.request(annotation=None))
        self.assertIsNone(self.finished(json.loads(first)["id"])[1]["annotation"])
        self.assertEqual(self.finished(json.loads(second)["id"])[1]["job"], str(self.files / "part-2.job.json"))
        self.assertNotIn("annotation", json.loads((self.files / "part-1.job.json").read_text(encoding="utf-8"))["jobs"][0])

    def test_a_request_snapshot_would_refuse_is_refused_before_anything_renders(self) -> None:
        for request, said in (
            ([], "requested as an object"),
            (self.request(views=[]), "at least one"),
            (self.request(extra=1), "unknown field(s): extra"),
            (self.request(input="part.stl"), "absolute path"),
            (self.request(input=str(self.tmp / "gone.step")), "no file at"),
            (self.request(views=[{"camera": "iso"}]), "views[0].label"),
            (self.request(views=[{"label": "a", "camera": "iso", "width": 0}]), "views[0].width"),
            (self.request(annotation={"marks": {}}), "annotation is null or an object"),
            # snapshot's own preparation: a camera key it does not take, a pose a mesh does not have
            (self.request(views=[{"label": "a", "camera": {"projection": "perspective"}}]), "display.camera"),
            (self.request(views=[{"label": "a", "camera": "iso", "kinematics": {"x": 1}}]), "kinematics"),
            (self.request(measurements={}), "measurements is null or a list"),
            # a measurement snapshot would not draw: its own check, with the job
            (self.request(measurements=[{"start": [0, 0], "end": [1, 0, 0], "label": "", "color": "#ffffff"}]), "measurements[0].start"),
            # a routine's frame reaches snapshot's own check: a mesh has none
            (self.request(views=[{"label": "a", "camera": "iso", "animation": {"clip": "spin", "time": 1}}]), "animation in its sidecar"),
        ):
            status, body, _ = self.call("POST", "/__cad/prompt-files", request)
            self.assertEqual(status, 400, request)
            self.assertEqual(json.loads(body)["ok"], False)
            self.assertIn(said, json.loads(body)["error"])
        self.assertEqual(self.rendered, [])
        self.assertEqual(sorted(path.name for path in self.files.iterdir()) if self.files.exists() else [], [])

    def test_a_chips_copy_is_saved_as_the_file_snapshot_reads_and_answered_with_its_path(self) -> None:
        mark = {"start": [0, 0, 0], "end": [10, 0, 0], "label": "10.00 mm", "color": "#d59090"}
        status, body, _ = self.call("POST", "/__cad/prompt-files/save", {"name": "part.stl", "kind": "annotation", "data": ANNOTATION})
        self.assertEqual((status, json.loads(body)), (200, {"ok": True, "path": str(self.files / "part-1.annotation.json")}))
        self.assertEqual(json.loads((self.files / "part-1.annotation.json").read_text(encoding="utf-8")), ANNOTATION)
        _, body, _ = self.call("POST", "/__cad/prompt-files/save", {"name": "part.stl", "kind": "measurement", "data": mark})
        self.assertEqual(json.loads(Path(json.loads(body)["path"]).read_text(encoding="utf-8")), [mark])
        for request, said in (
            ({"name": "part.stl", "kind": "image", "data": {}}, "annotation or measurement"),
            ({"name": "part.stl", "kind": "annotation", "data": {"marks": {}}}, "marks (a list)"),
            ({"name": "part.stl", "kind": "measurement", "data": {**mark, "start": [0, 0]}}, "start"),
        ):
            status, body, _ = self.call("POST", "/__cad/prompt-files/save", request)
            self.assertEqual((status, json.loads(body)["ok"]), (400, False), request)
            self.assertIn(said, json.loads(body)["error"])
        self.assertEqual(self.rendered, [])

    def test_a_failed_render_is_a_500_and_an_unknown_id_a_404(self) -> None:
        self.failure = SnapshotError("the page could not draw it")
        self.release.set()
        _, body, _ = self.call("POST", "/__cad/prompt-files", self.request())
        self.assertEqual(self.finished(json.loads(body)["id"]), (500, {"ok": False, "error": "the page could not draw it"}))
        status, body, _ = self.call("GET", "/__cad/prompt-files?id=nope")
        self.assertEqual((status, json.loads(body)["ok"]), (404, False))

    def test_files_are_served_from_the_prompt_files_directory_only_and_to_the_viewer_only(self) -> None:
        self.release.set()
        _, body, _ = self.call("POST", "/__cad/prompt-files", self.request())
        self.finished(json.loads(body)["id"])
        (self.tmp / "secret.json").write_text("{}", encoding="utf-8")
        for name in ("..%2Fsecret.json", "../secret.json", "missing.png", "part-1.job.txt", ""):
            self.assertEqual(self.call("GET", f"/__cad/prompt-files/{name}")[0], 404, name)
        self.assertEqual(self.call("GET", "/__cad/prompt-files/part-1-current.png", guard=False)[0], 403)
        self.assertEqual(self.call("GET", f"/__cad/prompt-files?id={json.loads(body)['id']}", guard=False)[0], 403)


class WarmSnapshotSessionTest(unittest.TestCase):
    """One browser across prompts; a failed render's is never reused; closed at exit."""

    def test_the_session_keeps_its_browser_until_a_render_fails_or_it_is_closed(self) -> None:
        renderers, closed = [], []

        class Renderer:
            def __init__(self, runtime_dir) -> None:
                renderers.append(self)

            async def close(self) -> None:
                closed.append(self)

        async def run(options, *, kinds, cwd, renderer, report):
            self.assertFalse(report)
            if options.job.endswith("bad.job.json"):
                raise SnapshotError("bad")
            return renderer

        session = WarmSnapshotSession()
        self.addCleanup(session.close)
        with mock.patch("cadgen.snapshot_core.BatchSnapshotRenderer", Renderer), \
                mock.patch("cadgen.snapshot_cli.run_snapshot_async", run):
            first, second = session.render(Path("/tmp/a.job.json")), session.render(Path("/tmp/b.job.json"))
            self.assertIs(first, second)
            with self.assertRaises(SnapshotError):
                session.render(Path("/tmp/bad.job.json"))
            self.assertEqual(closed, [first])
            third = session.render(Path("/tmp/c.job.json"))
            self.assertIsNot(third, first)
            session.close()
        self.assertEqual(closed, [first, third])


if __name__ == "__main__":
    unittest.main()
