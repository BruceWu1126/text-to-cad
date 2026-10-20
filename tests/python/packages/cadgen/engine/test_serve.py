"""Serving a document: its structure, one plain buffer per part in the wire format, the
same buffer whichever way it is meshed, and a document read again after a rebuild."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import textwrap
import time
import unittest
import urllib.request
from pathlib import Path

import numpy as np

from cadgen.engine import build as engine_build
from cadgen.engine import mesh
from cadgen.engine.serve import Server

MODEL = """
import build123d as bd
from cadgen.engine import xbf

SIZE = {size}


@xbf
def post():
    return bd.fillet((bd.Box(SIZE, 4, 10) - bd.Cylinder(1, 20)).edges().filter_by(bd.Axis.Z), 0.5)


@xbf
def rack():
    left = post()
    right = post().moved(bd.Location((20, 0, 0)))
    right.label = "right"
    return bd.Compound(children=[left, right, bd.Pos(10, 0, -6) * bd.Box(30, 6, 2)], label="rack")
"""


class ServeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="engine-serve-")).resolve()
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.script = self.folder / "rack.py"
        self.write(4)
        self.server = Server(port=0, mesh_mode="inline")  # a test asks for the pool itself
        self.server.start()
        self.addCleanup(self.server.stop)
        self.ident = self.server.add(self.folder / "rack.xbf")

    def write(self, size: int) -> None:
        self.script.write_text(textwrap.dedent(MODEL.format(size=size)), encoding="utf-8")
        os.utime(self.script, ns=(time.time_ns(), time.time_ns()))
        reports = engine_build.build(self.script, ["rack"], cwd=self.folder)
        self.assertTrue(reports[-1]["ok"], reports)

    def get(self, path: str, method: str = "GET") -> bytes:
        request = urllib.request.Request(self.server.url + path, method=method)
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read()

    def structure(self) -> dict:
        return json.loads(self.get(f"/api/doc/{self.ident}"))

    def test_a_part_is_one_plain_buffer_with_face_and_edge_ranges(self) -> None:
        structure = self.structure()
        self.assertEqual([i["path"] for i in structure["instances"]], ["rack/post", "rack/right", "rack/part2"])
        self.assertEqual(len(structure["prototypes"]), 2, "the post placed twice is one prototype")
        post = structure["prototypes"][structure["instances"][0]["prototype"]]
        data = self.get(f"/api/doc/{self.ident}/part/{post['key']}")
        self.assertEqual(data[:4], b"XBM2")
        part = mesh.Mesh.from_bytes(data)
        self.assertEqual(part.positions.dtype, np.float32)
        self.assertEqual(part.indices.dtype, np.uint32)
        # The face table tiles the index buffer, in order, every index a vertex.
        self.assertEqual(int(part.faces[:, 2].sum()), len(part.indices))
        self.assertTrue(np.array_equal(part.faces[1:, 1], np.cumsum(part.faces[:-1, 2])))
        self.assertLess(int(part.indices.max()), len(part.positions))
        # Ordinals are the ones the element names are kept by.
        self.assertEqual({str(o) for o in part.faces[:, 0]}, set(post["faceNames"]))
        self.assertEqual(int(part.edges[:, 2].sum()), len(part.edge_points))
        self.assertTrue(len(part.edges))

    def test_inline_and_processes_mesh_the_same_buffers(self) -> None:
        keys = [p["key"] for p in self.structure()["prototypes"]]
        first = json.loads(self.get(f"/api/doc/{self.ident}/mesh?mode=inline", "POST"))
        self.assertEqual(first["meshed"], 2)
        inline = {key: self.server.cache.get(key) for key in keys}
        again = json.loads(self.get(f"/api/doc/{self.ident}/mesh?mode=processes&drop=1", "POST"))
        self.assertEqual((again["meshed"], again["mode"]), (2, "processes"))
        for key in keys:
            self.assertEqual(self.server.cache.get(key), inline[key])
        held = json.loads(self.get(f"/api/doc/{self.ident}/mesh", "POST"))
        self.assertEqual(held["meshed"], 0, "a part held is not meshed again")

    def test_a_served_document_is_read_again_after_a_rebuild(self) -> None:
        before = self.structure()
        self.write(6)
        after = self.structure()
        self.assertNotEqual(after["version"], before["version"])
        post = lambda s: s["prototypes"][s["instances"][0]["prototype"]]["key"]  # noqa: E731
        self.assertNotEqual(post(after), post(before), "the post's geometry changed")
        self.assertEqual(after["prototypes"][after["instances"][2]["prototype"]]["key"],
                         before["prototypes"][before["instances"][2]["prototype"]]["key"], "the base did not")


if __name__ == "__main__":
    unittest.main()
