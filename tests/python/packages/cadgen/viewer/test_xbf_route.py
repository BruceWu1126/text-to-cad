"""``GET /__cad/xbf`` against a real launched viewer: an ``.xbf`` has a catalog row, and the route
answers where the cadgen engine serves it -- asking the engine's daemon (one of this test's own,
in its own state folder) to serve it -- so the page reads the document from the engine; the engine
answers the page's origin. A request that names no engine document is refused in words.

The model is written into a fresh temporary folder and built there; nothing reads the sample
corpus.
"""

from __future__ import annotations

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
import urllib.request
from pathlib import Path
from urllib.parse import quote

from cadgen.viewer import handler as handler_module
from cadgen.viewer.http_app import create_cad_app
from cadgen.viewer.scanner import catalog_entry

SRC = Path(__file__).resolve().parents[5] / "packages" / "cadgen" / "src"

MODEL = """
import build123d as bd
from cadgen.engine import xbf


@xbf
def block():
    return bd.Box(10, 6, 4) - bd.Cylinder(1, 10)
"""


class XbfRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base = Path(os.path.realpath(tempfile.mkdtemp(prefix="viewer-xbf-")))
        cls.state = Path(tempfile.mkdtemp(prefix="eng-", dir="/tmp" if os.name != "nt" else None)).resolve()
        cls.saved = {name: os.environ.get(name) for name in ("CADGEN_ENGINE_DIR", "CADGEN_ENGINE_MESH", "CADGEN_ENGINE_IDLE")}
        # The daemon this test starts is its own: its own socket folder, one part meshed at a time.
        os.environ.update({"CADGEN_ENGINE_DIR": str(cls.state), "CADGEN_ENGINE_MESH": "inline", "CADGEN_ENGINE_IDLE": "60"})
        (cls.base / "block.py").write_text(textwrap.dedent(MODEL), encoding="utf-8")
        env = {**os.environ, "PYTHONPATH": str(SRC)}
        done = subprocess.run([sys.executable, "-m", "cadgen.engine", "build", "--no-daemon", "block.py"], cwd=cls.base,
                              env=env, capture_output=True, text=True, timeout=300)
        assert done.returncode == 0, done.stderr
        cls.document = cls.base / "block.xbf"
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

    def get(self, target: str) -> tuple[int, dict]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=300)
        try:
            connection.request("GET", target)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_an_xbf_has_a_catalog_row_and_is_served_by_the_engine(self) -> None:
        entry = catalog_entry(str(self.document))
        self.assertEqual(entry["kind"], "xbf")
        status, served = self.get(f"/__cad/xbf?file={quote(str(self.document), safe='')}")
        self.assertEqual(status, 200, served)
        self.assertEqual(served["url"], f"{served['engine']}/api/doc/{served['doc']}")
        # The page's origin reads the structure from the engine directly.
        page = f"http://127.0.0.1:{self.port}"
        request = urllib.request.Request(served["url"], headers={"Origin": page})
        with urllib.request.urlopen(request, timeout=300) as answer:
            self.assertEqual(answer.headers.get("Access-Control-Allow-Origin"), page)
            structure = json.loads(answer.read())
        self.assertEqual((structure["name"], len(structure["prototypes"])), ("block", 1))
        # Asked again, the same engine and document.
        self.assertEqual(self.get(f"/__cad/xbf?file={quote(str(self.document), safe='')}")[1], served)

    def test_a_request_that_names_no_engine_document_is_refused_in_words(self) -> None:
        (self.base / "part.step").write_text("ISO-10303-21;\n", encoding="utf-8")
        for target, words in (("/__cad/xbf", "name the .xbf"),
                              (f"/__cad/xbf?file={quote(str(self.base / 'part.step'), safe='')}", "not a cadgen engine document"),
                              (f"/__cad/xbf?file={quote(str(self.base / 'gone.xbf'), safe='')}", "no document")):
            with self.subTest(target=target):
                status, body = self.get(target)
                self.assertEqual(status, 400)
                self.assertIn(words, body["error"])


if __name__ == "__main__":
    unittest.main()
