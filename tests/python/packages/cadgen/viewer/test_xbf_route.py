"""``/__cad/xbf`` against a real launched viewer and an engine daemon of this test's own: an
``.xbf`` and a STEP are served by the cadgen engine -- the route asks the engine's daemon, which
reads a STEP in a reader process of its own -- and the page reads them through the viewer
(``/__cad/xbf/api/...``), never from the engine's own port: what the CAD app's tunnel carries
too. A STEP's names, colours and materials are the engine's reading of it, and the same STEP
opened again (or touched) is not parsed again while the daemon lives. A request that names no
engine document is refused in words.

The models are written into a fresh temporary folder and built there; nothing reads the sample
corpus.
"""

from __future__ import annotations

import base64
import gzip
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import quote

from cadgen.viewer import handler as handler_module
from cadgen.viewer.http_app import create_cad_app
from cadgen.viewer.scanner import catalog_entry
from cadgen.viewer.xbf import EngineDocuments
from tests.python.support import engine_daemon

SRC = Path(__file__).resolve().parents[5] / "packages" / "cadgen" / "src"

MODEL = """
import build123d as bd
from cadgen.engine import materials, step, xbf


@xbf
def block():
    return bd.Box(10, 6, 4) - bd.Cylinder(1, 10)


@xbf
@step(out="pair.step")
def pair():
    plate = bd.Box(10, 6, 4) - bd.Cylinder(1, 10)
    plate.label, plate.color = "plate", bd.Color(0.8, 0.1, 0.1)
    peg = bd.Pos(8, 0, 4) * bd.Cylinder(1, 6)
    peg.label, peg.material = "peg", materials.aluminium_6061
    return bd.Compound(children=[plate, peg], label="pair")
"""


class XbfRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base = Path(os.path.realpath(tempfile.mkdtemp(prefix="viewer-xbf-")))
        cls.state = Path(tempfile.mkdtemp(prefix="eng-", dir="/tmp" if os.name != "nt" else None)).resolve()
        cls.saved = {name: os.environ.get(name) for name in ("CADGEN_ENGINE_DIR", "CADGEN_ENGINE_MESH", "CADGEN_ENGINE_IDLE")}
        # The daemon this test starts is its own: its own socket folder, one part meshed at a time.
        os.environ.update({"CADGEN_ENGINE_DIR": str(cls.state), "CADGEN_ENGINE_MESH": "inline", "CADGEN_ENGINE_IDLE": "60"})
        (cls.base / "models.py").write_text(textwrap.dedent(MODEL), encoding="utf-8")
        env = engine_daemon.watched({**os.environ, "PYTHONPATH": str(SRC)}, cls.addClassCleanup)
        # Built as a script runs: through this test's own engine daemon.
        done = engine_daemon.run([sys.executable, "models.py"], cwd=cls.base, env=env)
        assert done.returncode == 0, done.stderr
        cls.document = cls.base / "XBF" / "block.xbf"
        cls.step = cls.base / "pair.step"
        assert cls.step.is_file(), done.stdout + done.stderr
        cls.app = create_cad_app(host="127.0.0.1", port=0, dist_dir="")
        cls.server = handler_module.serve(cls.app, "127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        cls.app.port = cls.port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        subprocess.run([sys.executable, "-m", "cadgen.engine", "stop"], env={**os.environ, "PYTHONPATH": str(SRC)},
                       capture_output=True, timeout=120)
        deadline = time.monotonic() + 20
        while any(cls.state.glob("*.sock")) and time.monotonic() < deadline:
            time.sleep(0.05)
        for name, value in cls.saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        shutil.rmtree(cls.base, ignore_errors=True)
        shutil.rmtree(cls.state, ignore_errors=True)

    def request(self, target: str, headers: dict | None = None) -> tuple[int, dict, bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=300)
        try:
            connection.request("GET", target, headers=headers or {})
            response = connection.getresponse()
            return response.status, {name.lower(): value for name, value in response.getheaders()}, response.read()
        finally:
            connection.close()

    def get(self, target: str) -> tuple[int, dict]:
        status, _, body = self.request(target)
        return status, json.loads(body)

    def answer(self, path: Path) -> tuple[int, dict]:
        """The page's open: ``/__cad/xbf?file=``, asked again while the engine reads the file."""
        deadline = time.monotonic() + 300
        while True:
            status, answer = self.get(f"/__cad/xbf?file={quote(str(path), safe='')}")
            if status != 202:
                return status, answer
            self.assertEqual(answer["state"], "reading")
            self.assertLess(time.monotonic(), deadline, "the engine never finished reading the document")

    def open(self, path: Path) -> dict:
        status, answer = self.answer(path)
        self.assertEqual(status, 200, answer)
        return answer

    def structure(self, served: dict) -> dict:
        status, structure = self.get(served["url"])
        self.assertEqual(status, 200, structure)
        return structure

    def held(self, path: Path) -> bool:
        status, answer = self.get(f"/__cad/xbf?file={quote(str(path), safe='')}&held=1")
        self.assertEqual(status, 200, answer)
        return answer["held"]

    def test_an_xbf_is_served_by_the_engine_and_read_through_the_viewer(self) -> None:
        self.assertEqual(catalog_entry(str(self.document))["kind"], "xbf")
        served = self.open(self.document)
        self.assertEqual(served, {"doc": served["doc"], "url": f"/__cad/xbf/api/doc/{served['doc']}"})
        # The structure, a part and the look, all under the viewer's origin: a page from it reads
        # them as its own (the engine's loopback-only answer is never relayed to it).
        status, headers, body = self.request(served["url"], {"Origin": "http://example.test"})
        self.assertEqual(status, 200)
        self.assertFalse([name for name in headers if name.startswith("access-control")], headers)
        structure = json.loads(body)
        self.assertEqual((structure["name"], len(structure["prototypes"])), ("block", 1))
        status, headers, part = self.request(f"{served['url']}/part/{quote(structure['prototypes'][0]['key'], safe='')}")
        self.assertEqual((status, part[:4], headers["content-type"]), (200, b"XBM2", "application/octet-stream"))
        status, look = self.get("/__cad/xbf/api/look")
        self.assertEqual((status, look["environment"]["url"]), (200, "/api/look/environment"))
        status, _, image = self.request("/__cad/xbf/api/look/environment")
        self.assertEqual((status, image[:10]), (200, b"#?RADIANCE"))
        # Asked again, the same document.
        self.assertEqual(self.open(self.document), served)

    def test_a_step_is_read_by_the_engine_with_its_names_colours_and_materials(self) -> None:
        entry = catalog_entry(str(self.step))
        self.assertEqual((entry["kind"], entry["bytes"]), ("step", self.step.stat().st_size))
        structure = self.structure(self.open(self.step))
        by_name = {prototype["name"]: prototype for prototype in structure["prototypes"]}
        self.assertEqual(set(by_name), {"plate", "peg"})
        self.assertEqual([round(value, 2) for value in by_name["plate"]["color"][:3]], [0.8, 0.1, 0.1])
        self.assertEqual((by_name["peg"]["material"]["name"], by_name["peg"]["material"]["density"]),
                         ("Aluminum 6061-T6", 2700.0))
        self.assertEqual(sorted(instance["path"] for instance in structure["instances"]), ["pair/peg", "pair/plate"])
        # Read in a reader process, which the structure names: never the daemon itself.
        self.assertNotIn(structure["read"]["process"], (0, self.daemon_pid()))

    def test_a_step_opened_again_or_touched_is_not_parsed_again(self) -> None:
        copy = self.base / "again.step"
        shutil.copyfile(self.step, copy)
        self.assertFalse(self.held(copy), "the engine is asked, and asked for nothing")
        served = self.open(copy)
        self.assertTrue(self.held(copy))
        first = self.structure(served)
        # Opened again: the same document, the same read.
        self.assertEqual(self.open(copy), served)
        self.assertEqual(self.structure(served)["read"], first["read"])
        # Touched: a new version of the same bytes, read by nothing -- the parse is held by the bytes.
        later = time.time_ns() + 5_000_000_000
        os.utime(copy, ns=(later, later))
        deadline = time.monotonic() + 120
        while True:
            status, answer = self.get(f"{served['url']}/version?after={quote(first['version'], safe='')}&wait=30")
            self.assertEqual(status, 200, answer)
            if answer["version"] != first["version"]:
                break
            self.assertLess(time.monotonic(), deadline, "the engine never noticed the file was written again")
        again = self.structure(served)
        self.assertNotEqual(again["version"], first["version"])
        self.assertEqual(again["read"], first["read"], "the same bytes are not parsed again")
        self.assertEqual([p["key"] for p in again["prototypes"]], [p["key"] for p in first["prototypes"]],
                         "nor meshed again: a part's key is made from the file's bytes")

    def test_the_relay_answers_the_engines_read_routes_and_nothing_else(self) -> None:
        served = self.open(self.document)
        for target in ("/__cad/xbf/", "/__cad/xbf/api/docs", "/__cad/xbf/page/index.html", "/__cad/xbf/three/build/three.module.js",
                       f"{served['url']}/mesh", f"{served['url']}/pose", f"{served['url']}/part"):
            with self.subTest(target=target):
                status, body = self.get(target)
                self.assertEqual((status, body), (404, {"error": "Not found"}))

    def test_a_version_wait_is_held_no_longer_than_the_relay_allows(self) -> None:
        asked = []

        def engine(_address, target, timeout):
            asked.append(target)
            return 200, b'{"version":"1-1","patch":null}', "application/json"

        # The CAD Viewer holds a wait a second at most; the CAD app's tunnel not at all.
        for wait_seconds, expected in ((1.0, "1"), (0.0, "0")):
            documents = EngineDocuments(wait_seconds=wait_seconds)
            documents._engine = "127.0.0.1:9"
            with mock.patch("cadgen.viewer.xbf._get", engine):
                answer = documents.relay("/__cad/xbf/api/doc/abc/version", {"after": "0-0", "wait": "25"})
            self.assertEqual(answer, (200, b'{"version":"1-1","patch":null}', "application/json"))
            self.assertEqual(asked.pop(), f"/api/doc/abc/version?after=0-0&wait={expected}")
        # The build count the page heard goes on to the engine, which answers when the build moves.
        with mock.patch("cadgen.viewer.xbf._get", engine):
            documents.relay("/__cad/xbf/api/doc/abc/version", {"after": "0-0", "wait": "25", "progress": "3"})
            documents.relay("/__cad/xbf/api/doc/abc/version", {"after": "0-0", "wait": "25", "progress": "x"})
        self.assertEqual(asked, ["/api/doc/abc/version?after=0-0&wait=0&progress=3", "/api/doc/abc/version?after=0-0&wait=0"])

    def test_the_cad_apps_tunnel_carries_an_engine_document(self) -> None:
        from cadgen.mcp.tunnel import ViewerTunnel

        tunnel = ViewerTunnel()
        copy = self.base / "tunnelled.step"
        shutil.copyfile(self.step, copy)

        def get(url: str):
            reply = tunnel.serve(method="GET", url=f"http://cad.invalid{url}", headers={}, body=b"")
            body = base64.b64decode(reply.get("body") or "")
            return reply["status"], gzip.decompress(body) if reply.get("encoding") == "gzip" else body

        deadline = time.monotonic() + 300
        while True:
            status, body = get(f"/__cad/xbf?file={quote(str(copy), safe='')}")
            if status != 202:
                break
            # The tunnel holds no call long: the page is told to come back.
            self.assertEqual(json.loads(body), {"state": "reading", "retryMs": 750})
            self.assertLess(time.monotonic(), deadline)
        self.assertEqual(status, 200, body)
        url = json.loads(body)["url"]
        status, body = get(url)
        self.assertEqual(status, 200)
        structure = json.loads(body)
        self.assertEqual({prototype["name"] for prototype in structure["prototypes"]}, {"plate", "peg"})
        status, body = get(f"{url}/part/{quote(structure['prototypes'][0]['key'], safe='')}")
        self.assertEqual((status, body[:4]), (200, b"XBM2"))

    def test_a_request_that_names_no_engine_document_is_refused_in_words(self) -> None:
        (self.base / "part.stl").write_text("solid t\nendsolid t\n", encoding="utf-8")
        (self.base / "garbage.step").write_text("not a step file\n", encoding="utf-8")
        for target, words in (("/__cad/xbf", "needs ?file="),
                              (f"/__cad/xbf?file={quote(str(self.base / 'part.stl'), safe='')}", "not a document the cadgen engine reads"),
                              (f"/__cad/xbf?file={quote(str(self.base / 'gone.xbf'), safe='')}", "no document")):
            with self.subTest(target=target):
                status, body = self.get(target)
                self.assertEqual(status, 400)
                self.assertIn(words, body["error"])
        # A file the engine cannot read: refused in the engine's words, once it has tried.
        status, body = self.answer(self.base / "garbage.step")
        self.assertEqual(status, 400, body)
        self.assertIn("garbage.step", body["error"])

    # --- helpers ---------------------------------------------------------------------------

    def daemon_pid(self) -> int:
        from cadgen.engine.client import request

        return int((request({"op": "status"}, start=False) or {}).get("pid") or 0)


if __name__ == "__main__":
    unittest.main()
