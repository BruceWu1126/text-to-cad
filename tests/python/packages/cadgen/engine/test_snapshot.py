"""`cadgen xbf snapshot`: a document drawn by the viewer's scene builder into a PNG, its
timings reported, and what an engine document's view does not offer refused by name before
anything renders, with OUT untouched. A page on this machine may read a served document; a
page from anywhere else may not."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
import urllib.request
from pathlib import Path

from cadgen.engine import build as engine_build
from cadgen.engine.access import loopback_origin
from cadgen.engine.serve import Server
from cadgen.engine.snapshot import SnapshotRefused, parse_camera, parse_display, snapshot

SRC = Path(__file__).resolve().parents[5] / "packages" / "cadgen" / "src"

MODEL = """
import build123d as bd
from cadgen.engine import paint, xbf


@xbf
def stand():
    plate = bd.Box(40, 20, 4) - bd.Cylinder(4, 10)
    plate = paint(plate, plate.faces().sort_by(bd.Axis.Z)[-1], "orange")
    plate.label = "plate"
    peg = bd.Cylinder(3, 12)
    peg.label = "peg"
    return bd.Compound(children=[plate, bd.Pos(12, 0, 8) * peg, bd.Pos(-12, 0, 8) * peg], label="stand")


@xbf
def peg():
    return bd.Cylinder(3, 12)
"""


class SnapshotTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.folder = Path(tempfile.mkdtemp(prefix="engine-snapshot-")).resolve()
        script = cls.folder / "stand.py"
        script.write_text(textwrap.dedent(MODEL), encoding="utf-8")
        reports = engine_build.build(script, None, cwd=cls.folder)
        assert all(report["ok"] for report in reports), reports
        cls.document = cls.folder / "XBF/stand.xbf"
        cls.single = cls.folder / "XBF/peg.xbf"
        # One part at a time on this process's thread: the test asks for no pool.
        cls.mesh = os.environ.get("CADGEN_ENGINE_MESH")
        os.environ["CADGEN_ENGINE_MESH"] = "inline"

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.mesh is None:
            os.environ.pop("CADGEN_ENGINE_MESH", None)
        else:
            os.environ["CADGEN_ENGINE_MESH"] = cls.mesh
        shutil.rmtree(cls.folder, ignore_errors=True)

    def test_a_document_is_drawn_into_a_png_with_its_timings(self) -> None:
        from PIL import Image

        out = self.folder / "solid.png"
        report = snapshot(self.document, out, display=parse_display("solid", grid=False), camera=parse_camera("iso"),
                          size=(320, 240), daemon=False)
        self.assertEqual((report["width"], report["height"], report["camera"]), (320, 240, "iso"))
        self.assertEqual((report["parts"], report["instances"], report["unmeshed"]), (2, 3, {}))
        for stage in ("serveMs", "meshMs", "browserMs", "pageMs", "loadSourceMs", "buildModelMs", "captureMs", "totalMs"):
            self.assertIsInstance(report["timings"][stage], int, stage)
        with Image.open(out) as image:
            self.assertEqual(image.size, (320, 240))
            data = image.convert("RGB").tobytes()
        pixels = [tuple(data[at:at + 3]) for at in range(0, len(data), 3)]
        # Not a blank frame: the backdrop is one colour; the parts, their paint and edges are many.
        backdrop = max(set(pixels), key=pixels.count)
        drawn = [pixel for pixel in pixels if pixel != backdrop]
        self.assertGreater(len(drawn), len(pixels) // 10)
        self.assertGreater(len(set(drawn)), 50)
        # The painted top face is warm (the viewer's surface grades its orange): red well above blue.
        self.assertTrue(any(r - b > 60 for r, g, b in drawn))

    def test_the_cli_prints_what_it_drew_and_its_timings(self) -> None:
        out = self.folder / "wire.png"
        env = {**os.environ, "PYTHONPATH": str(SRC), "CADGEN_ENGINE_MESH": "inline"}
        done = subprocess.run([sys.executable, "-m", "cadgen.engine", "snapshot", str(self.document), str(out),
                               "--display", "wireframe", "--grid", "--width", "200", "--height", "160", "--no-daemon",
                               "--json"], env=env, capture_output=True, text=True, timeout=300)
        self.assertEqual(done.returncode, 0, done.stderr)
        report = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertEqual((report["displayMode"], report["width"], report["height"]), ("wireframe", 200, 160))
        self.assertGreater(report["timings"]["totalMs"], 0)
        self.assertEqual(out.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

    def test_what_a_document_does_not_offer_is_refused_by_name_and_out_is_kept(self) -> None:
        out = self.folder / "kept.png"
        out.write_bytes(b"before")
        with self.assertRaisesRegex(SnapshotRefused, "exploded"):
            parse_display('{"mode": "solid", "exploded": {"enabled": true}}', grid=False)
        with self.assertRaisesRegex(SnapshotRefused, "lighting"):
            parse_display('{"lighting": {}}', grid=False)
        with self.assertRaisesRegex(SnapshotRefused, "sideways"):
            parse_display("sideways", grid=False)
        with self.assertRaisesRegex(SnapshotRefused, "underneath"):
            parse_camera("underneath")
        with self.assertRaisesRegex(SnapshotRefused, "projection"):
            parse_camera('{"projection": "perspective"}')
        with self.assertRaisesRegex(SnapshotRefused, r"\.png"):
            snapshot(self.document, self.folder / "out.jpg", daemon=False)
        # Parts colours placed parts apart: a document of one is refused before the page opens.
        with self.assertRaisesRegex(SnapshotRefused, "parts .* places 1 part"):
            snapshot(self.single, out, display={"mode": "parts"}, daemon=False)
        self.assertEqual(out.read_bytes(), b"before")
        env = {**os.environ, "PYTHONPATH": str(SRC)}
        done = subprocess.run([sys.executable, "-m", "cadgen.engine", "snapshot", str(self.document), str(out),
                               "--display", '{"exploded": {"enabled": true}}', "--no-daemon"],
                              env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 2)
        self.assertIn("display.exploded", done.stderr)
        self.assertEqual(out.read_bytes(), b"before")


    def drawn(self, path: Path, size: tuple[int, int]) -> None:
        """A picture of something: the size asked, and more than a backdrop."""
        from PIL import Image

        with Image.open(path) as image:
            self.assertEqual(image.size, size)
            data = image.convert("RGB").tobytes()
        pixels = [tuple(data[at:at + 3]) for at in range(0, len(data), 3)]
        backdrop = max(set(pixels), key=pixels.count)
        self.assertGreater(len([pixel for pixel in pixels if pixel != backdrop]), len(pixels) // 20)

    def test_a_mesh_file_is_drawn_by_the_viewer_s_mesh_renderers(self) -> None:
        from cadgen.engine.export import translate

        for suffix in (".glb", ".stl", ".3mf"):
            mesh = self.folder / f"stand{suffix}"
            translate(self.document, None, mesh)
            out = self.folder / f"mesh-{suffix[1:]}.png"
            report = snapshot(mesh, out, camera=parse_camera("iso"), size=(240, 180), daemon=False)
            self.assertEqual((report["width"], report["height"]), (240, 180), suffix)
            self.drawn(out, (240, 180))
        with self.assertRaisesRegex(SnapshotRefused, "is a mesh and has none"):
            snapshot(self.folder / "stand.glb", self.folder / "posed.png", pose="raised", daemon=False)

    def test_a_model_is_drawn_from_its_worker_and_nothing_else_is_written(self) -> None:
        from cadgen.engine import api

        folder = Path(tempfile.mkdtemp(prefix="engine-drawn-")).resolve()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        script = folder / "stand.py"
        # No @xbf: the picture is drawn from the document the worker holds, and no file but it is written.
        script.write_text(textwrap.dedent(MODEL).replace("@xbf\ndef stand", "@glb\ndef stand")
                          .replace("import paint, xbf", "import glb, paint, xbf"), encoding="utf-8")
        drawn = api.snapshot(f"{script}::stand", folder / "stand.png", display="xray", camera="iso", width=200,
                             height=150, daemon=False)
        self.assertEqual((drawn.width, drawn.height), (200, 150))
        self.drawn(folder / "stand.png", (200, 150))
        self.assertEqual(sorted(path.name for path in folder.rglob("*") if path.is_file()), ["stand.png", "stand.py"])


class AccessTest(unittest.TestCase):
    """The page that reads a document from the engine is the viewer's or the snapshot's, on this
    machine: those origins are named as allowed readers, and no other is."""

    def test_loopback_pages_may_read_a_served_document_and_no_other_page_may(self) -> None:
        for origin in ("http://127.0.0.1:3245", "http://localhost", "http://[::1]:5173", "http://app.localhost:8080"):
            self.assertTrue(loopback_origin(origin), origin)
        for origin in ("https://example.com", "http://127.0.0.1.example.com", "null", "", None, "http://localhost:x"):
            self.assertFalse(loopback_origin(origin), origin)
        folder = Path(tempfile.mkdtemp(prefix="engine-access-")).resolve()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        (folder / "peg.py").write_text(textwrap.dedent(MODEL), encoding="utf-8")
        engine_build.build(folder / "peg.py", ["peg"], cwd=folder)
        server = Server(port=0, mesh_mode="inline")
        server.start()
        self.addCleanup(server.stop)
        url = f"{server.url}/api/doc/{server.add(folder / 'XBF' / 'peg.xbf')}"

        def allowed(origin: str) -> str | None:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"Origin": origin}), timeout=60) as answer:
                return answer.headers.get("Access-Control-Allow-Origin")

        self.assertEqual(allowed("http://127.0.0.1:3245"), "http://127.0.0.1:3245")
        self.assertIsNone(allowed("https://example.com"))


if __name__ == "__main__":
    unittest.main()
