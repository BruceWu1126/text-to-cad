"""The regular display modes' look (``render/look.py``): one record, served at ``GET /api/look``
as the viewer reads it, and the copy of it the viewer's tests parse
(``packages/core/src/lib/xbf/__fixtures__/look.json``) the same record."""

from __future__ import annotations

import json
import unittest
import urllib.request
from pathlib import Path

from cadgen.engine.render.look import LOOK
from cadgen.engine.serve import Server

FIXTURE = Path(__file__).resolve().parents[5] / "packages" / "core" / "src" / "lib" / "xbf" / "__fixtures__" / "look.json"
REGENERATE = ("regenerate it: PYTHONPATH=packages/cadgen/src python -c \"import json; from cadgen.engine.render.look "
              "import LOOK; print(json.dumps(LOOK.json(), indent=2))\" > packages/core/src/lib/xbf/__fixtures__/look.json")


class LookTest(unittest.TestCase):
    def test_the_engine_serves_the_record_to_a_loopback_page(self) -> None:
        server = Server(port=0, mesh_mode="inline")
        server.start()
        self.addCleanup(server.stop)
        request = urllib.request.Request(f"{server.url}/api/look", headers={"Origin": "http://localhost:5173"})
        with urllib.request.urlopen(request, timeout=30) as response:
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], "http://localhost:5173")
            self.assertEqual(json.loads(response.read()), LOOK.json())

    def test_the_viewers_copy_is_the_record(self) -> None:
        self.assertEqual(json.loads(FIXTURE.read_text(encoding="utf-8")), LOOK.json(), REGENERATE)

    def test_the_record_names_every_regular_mode_once(self) -> None:
        served = LOOK.json()
        self.assertEqual(list(served["modes"]), ["solid", "shaded", "parts", "xray", "hidden-line", "wireframe"])
        self.assertEqual(LOOK.mode("xray").opacity, 0.22)
        self.assertEqual(LOOK.edge("feature"), "#253443")
        self.assertEqual(served["tone"], "clamp", "OCCT's raster applies no tone curve")


if __name__ == "__main__":
    unittest.main()
