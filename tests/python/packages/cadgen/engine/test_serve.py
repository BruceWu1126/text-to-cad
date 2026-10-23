"""Serving a document: its structure, one plain buffer per part in the wire format, the
same buffer whichever way it is meshed, a document read again after another process
rebuilt it, a worker's build patched in without its file being read, and the cap on the
buffers held."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import textwrap
import threading
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


@xbf("XBF/post.xbf")
def post():
    return bd.fillet((bd.Box(SIZE, 4, 10) - bd.Cylinder(1, 20)).edges().filter_by(bd.Axis.Z), 0.5)


@xbf("XBF/rack.xbf")
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
        self.ident = self.server.add(self.folder / "XBF/rack.xbf")

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
        first = json.loads(self.get(f"/api/doc/{self.ident}/mesh?mode=inline&drop=1", "POST"))
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


SHELF = """
import build123d as bd
from cadgen.engine import paint, step, xbf

SIZE = {size}


@xbf("XBF/shelf.xbf")
@step("shelf.step")
def shelf():
    post = bd.fillet(bd.Box(SIZE, 4, 10).edges().filter_by(bd.Axis.Z), 0.5)
    post.label = "post"
    left = post.moved(bd.Location((0, 0, 0)))
    right = post.moved(bd.Location((20, 0, 0)))
    right.label = "right"
    right.color = bd.Color(0.8, 0.1, 0.1)
    board = bd.Pos(10, 0, -6) * bd.Box(30, 6, 2)
    board = paint(board, board.faces().sort_by(bd.Axis.Z)[-1], (0.1, 0.2, 0.9))
    board.label = "board"
    board.color = bd.Color(0.3, 0.6, 0.2)
    unnamed = bd.Pos(0, 10, 0) * bd.Sphere(2)
    repainted = board.moved(bd.Location((0, 0, -4)))
    repainted.label = "repainted"
    repainted.color = bd.Color(0.9, 0.9, 0.1)
    tier = bd.Compound(children=[board, repainted, unnamed], label="tier")
    return bd.Compound(children=[left, right, tier], label="shelf")
"""


class DocumentInMemoryTest(unittest.TestCase):
    """A worker's build reaches a served page without its file being read: the structure
    made from the build's tree is the file's, a patch replaces the document in place, and
    a page waiting on the version is answered by it."""

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="engine-patch-")).resolve()
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.script = self.folder / "shelf.py"
        self.path = self.folder / "XBF/shelf.xbf"

    def build(self, size: int) -> list:
        self.script.write_text(textwrap.dedent(SHELF.format(size=size)), encoding="utf-8")
        os.utime(self.script, ns=(time.time_ns(), time.time_ns()))
        reports = engine_build.build(self.script, ["shelf"], cwd=self.folder)
        self.assertTrue(reports[-1]["ok"], reports)
        return reports

    def test_the_structure_made_from_the_tree_is_the_files(self) -> None:
        from cadgen.engine import document

        self.build(4)
        made = document.tree_structure(document.saved(self.path), self.path)
        read = document.Loaded(self.path).structure()
        self.assertEqual(json.loads(json.dumps(made)), json.loads(json.dumps(read)))
        self.assertTrue(any(p["faceColors"] for p in read["prototypes"]), "a painted face is in it")
        self.assertTrue(any(i["color"] for i in read["instances"]), "an instance's own colour is in it")

    def test_a_patch_replaces_the_document_and_answers_the_page(self) -> None:
        from cadgen.engine import document, worker

        self.build(4)
        server = Server(port=0, mesh_mode="inline")
        self.addCleanup(server.stop)
        ident = server.add(self.path)
        before = server.document(ident)
        server.mesh_all(before, None)
        held = server.hold([str(self.path)])  # as the daemon does while a build writes the file
        export = self.folder / "shelf.step"
        sent = []
        patcher = worker.Patcher({"served": server.versions(), "held": server.cache.keys(),
                                  "shapes": server.shape_keys()}, lambda message, payload: sent.append(
                                      (message["patch"], payload, export.stat().st_mtime_ns)))
        document.SAVED_LISTENERS.append(patcher.saved)
        try:
            reports = self.build(6)
        finally:
            document.SAVED_LISTENERS.remove(patcher.saved)
        patcher.finish(reports)
        self.assertEqual(len(sent), 1)
        header, payload, export_then = sent[0]
        self.assertLess(export_then, export.stat().st_mtime_ns, "sent before the exports were written")
        self.assertEqual(len(header["meshes"]), 1, "only the changed post is meshed by the worker")
        self.assertEqual([key for key, _size in header["shapes"]], [header["meshes"][0][0]])
        self.assertEqual(server.document(ident).version, before.version, "a held file is not read again")
        answers = []
        waiting = threading.Thread(target=lambda: answers.append(server.wait(ident, before.version, 60)))
        waiting.start()
        server.patch(header, payload, time.time())
        waiting.join()
        after = answers[0]
        stat = self.path.stat()
        self.assertEqual(after.version, f"{stat.st_mtime_ns}-{stat.st_size}")
        self.assertIsNotNone(after.patch, "the document came from the patch, not the file")
        self.assertEqual(json.loads(json.dumps(after.structure)),
                         json.loads(json.dumps(document.Loaded(self.path).structure())))
        changed = header["meshes"][0][0]
        self.assertEqual(server.cache.get(changed)[:4], b"XBM2", "the worker's mesh is held")
        server.release(held)
        self.assertIs(server.document(ident), after, "the file the patch describes is not read again")


class ServedStepTest(unittest.TestCase):
    """A STEP served is read in a reader process, never in the server's: its structure and each
    part's shape bytes are what the server keeps. The read is held by the file's bytes -- served
    again, touched or copied, it is not parsed again -- and a STEP rewritten while it is served is
    read again in the background, the old version served until the new one is in."""

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="engine-step-")).resolve()
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        script = self.folder / "shelf.py"
        script.write_text(textwrap.dedent(SHELF.format(size=4)), encoding="utf-8")
        reports = engine_build.build(script, ["shelf"], cwd=self.folder)
        self.assertTrue(reports[-1]["ok"], reports)
        self.step = self.folder / "shelf.step"
        self.server = Server(port=0, mesh_mode="inline")
        self.addCleanup(self.server.stop)

    def test_a_step_is_read_by_a_reader_process_and_held_by_its_bytes(self) -> None:
        from cadgen.engine import document

        ident = self.server.add(self.step)
        served = self.server.document(ident)
        self.assertNotIn(served.structure["read"]["process"], (0, os.getpid()))
        self.assertTrue(all(isinstance(shape, bytes) for shape in served.shapes.values()), "only shape bytes are kept")
        # The document is the engine's reading of the file: what a read in this process makes of it.
        read, kept = (json.loads(json.dumps(structure)) for structure in (document.Loaded(self.step).structure(), served.structure))
        names = lambda structure: sorted((p["name"], p["color"], (p["material"] or {}).get("name"))  # noqa: E731
                                         for p in structure["prototypes"])
        self.assertEqual(names(kept), names(read))
        self.assertEqual(kept["instances"], read["instances"])
        # Served again, from another path, or touched: the same read.
        copy = self.folder / "copy.step"
        shutil.copyfile(self.step, copy)
        self.assertEqual(self.server.add(self.step), ident)
        self.assertEqual(self.server.document(self.server.add(copy)).structure["read"], served.structure["read"])
        later = time.time_ns() + 5_000_000_000
        os.utime(self.step, ns=(later, later))
        touched = self.server.wait(ident, served.version, 60)
        self.assertNotEqual(touched.version, served.version)
        self.assertEqual(touched.structure["read"], served.structure["read"])
        self.assertEqual(sorted(touched.shapes), sorted(served.shapes), "its parts' keys are the bytes'")
        # Other bytes are read again, in a reader of their own.
        self.step.write_bytes(self.step.read_bytes() + b"\n")
        rewritten = self.server.wait(ident, touched.version, 60)
        self.assertNotEqual(rewritten.structure["read"]["process"], served.structure["read"]["process"])

    def test_a_step_that_will_not_read_keeps_the_version_served_until_it_changes(self) -> None:
        ident = self.server.add(self.step)
        served = self.server.document(ident)
        self.step.write_text("not a step file\n", encoding="utf-8")
        stat = self.step.stat()
        self.server._reread(ident, served, (stat.st_mtime_ns, stat.st_size))  # as a version ask starts it
        self.assertIs(self.server.document(ident), served, "the page keeps the version it has")
        self.assertNotIn(ident, self.server.rereading, "and nothing reads the file again until it changes")


class MeshCacheTest(unittest.TestCase):
    def test_the_least_recently_used_buffer_goes_past_the_cap(self) -> None:
        from cadgen.engine.serve import MeshCache

        cache = MeshCache(cap=30)
        cache.put("a", b"x" * 10, {3: "valid"})
        cache.put("b", b"x" * 10)
        cache.put("c", b"x" * 10)
        self.assertIsNotNone(cache.get("a"))  # a is used: b is now the least recent
        cache.put("d", b"x" * 10)
        self.assertEqual(list(cache.items), ["c", "a", "d"])
        self.assertEqual(cache.size, 30)
        self.assertEqual(cache.unmeshed("a"), {3: "valid"})
        cache.put("e", b"x" * 25)
        self.assertEqual(list(cache.items), ["e"], "past the cap, the oldest go first")
        self.assertIsNone(cache.unmeshed("a"))


if __name__ == "__main__":
    unittest.main()
