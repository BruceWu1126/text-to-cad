"""The mesh exports a document is translated to: every placement's triangles in the STL,
a node per placement and a material per colour in the GLB, an object per part and a
build item per placement in the 3MF -- each written from the xbf alone."""

from __future__ import annotations

import json
import shutil
import struct
import tempfile
import textwrap
import unittest
import zipfile
from pathlib import Path

from cadgen.engine import build as engine_build
from cadgen.engine import document, exports, mesh

MODEL = """
import build123d as bd
from cadgen.engine import xbf


@xbf
def peg():
    peg = bd.Cylinder(2, 10)
    peg.color = bd.Color(0.9, 0.2, 0.1)
    return peg


@xbf
def board():
    plate = bd.Box(30, 10, 2)
    plate.color = bd.Color(0.2, 0.3, 0.9)
    plate.label = "plate"
    pegs = [peg().moved(bd.Location((x, 0, 6))) for x in (-10, 0, 10)]
    for index, placed in enumerate(pegs):
        placed.label = f"peg{index}"
    return bd.Compound(children=[plate, *pegs], label="board")
"""


class ExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.folder = Path(tempfile.mkdtemp(prefix="engine-exports-")).resolve()
        script = cls.folder / "board.py"
        script.write_text(textwrap.dedent(MODEL), encoding="utf-8")
        reports = engine_build.build(script, ["board"], cwd=cls.folder)
        assert reports[-1]["ok"], reports
        cls.loaded = document.load(cls.folder / "board.xbf")
        cls.triangles = {p.key: mesh.mesh_shape(p.shape).triangles for p in cls.loaded.prototypes}

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.folder, ignore_errors=True)

    def placed_triangles(self) -> int:
        return sum(self.triangles[self.loaded.prototypes[i.prototype].key] for i in self.loaded.instances)

    def test_an_export_meshes_each_part_from_its_read_back_bytes(self) -> None:
        # What the server and a worker mesh a key from: the bytes the document holds of it,
        # as read -- never a live shape's further round trip, which can move it by a last bit.
        from unittest import mock

        exports._MESHES.clear()
        given: list = []
        real = mesh.mesh_shape
        with mock.patch.object(mesh, "mesh_shape", side_effect=lambda shape, **kw: given.append(shape) or real(shape, **kw)):
            exports.write_from_loaded("stl", self.loaded, self.folder / "read-back.stl")
        expected = sorted(mesh.shape_bytes(p.shape, read=True) for p in self.loaded.prototypes)
        self.assertTrue(all(isinstance(shape, bytes) for shape in given), "meshed from bytes")
        self.assertEqual(sorted(given), expected)

    def test_stl_holds_every_placement_s_triangles(self) -> None:
        path = self.folder / "board.stl"
        exports.write_from_loaded("stl", self.loaded, path)
        data = path.read_bytes()
        count = struct.unpack_from("<I", data, 80)[0]
        self.assertEqual(count, self.placed_triangles())
        self.assertEqual(len(data), 84 + 50 * count)

    def test_glb_has_a_node_per_placement_and_a_material_per_colour(self) -> None:
        path = self.folder / "board.glb"
        exports.write_from_loaded("glb", self.loaded, path)
        data = path.read_bytes()
        magic, version, length = struct.unpack_from("<4sII", data, 0)
        self.assertEqual((magic, version, length), (b"glTF", 2, len(data)))
        chunk, kind = struct.unpack_from("<I4s", data, 12)
        self.assertEqual(kind, b"JSON")
        gltf = json.loads(data[20:20 + chunk])
        named = [node.get("name") for node in gltf["nodes"] if "mesh" in node]
        self.assertEqual(sorted(named), ["peg0", "peg1", "peg2", "plate"])
        self.assertEqual(len(gltf["meshes"]), 2, "the peg placed three times is one mesh")
        self.assertEqual(len(gltf["materials"]), 2)

    def test_3mf_has_an_object_per_part_and_an_item_per_placement(self) -> None:
        path = self.folder / "board.3mf"
        exports.write_from_loaded("threemf", self.loaded, path)
        with zipfile.ZipFile(path) as archive:
            self.assertIn("[Content_Types].xml", archive.namelist())
            model = archive.read("3D/3dmodel.model").decode("utf-8")
        self.assertEqual(model.count("<object "), 2)
        self.assertEqual(model.count("<item "), 4)

    def test_a_colour_written_to_step_reads_back_as_written(self) -> None:
        # The STEP writes a part's colour as the document keeps it; read back (``read_step``), the
        # part carries the same numbers, not the kernel's colour converted once more.
        import build123d as bd

        part = bd.Box(4, 4, 4)
        part.label = "grey"
        part.color = bd.Color(0.58, 0.62, 0.66)
        folder = Path(tempfile.mkdtemp(prefix="engine-colour-")).resolve()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        exports.write_step(document.to_xcaf(document.collect(bd.Compound(children=[part], label="one"), "one")),
                           folder / "one.step")
        read = document.read_step_shape(folder / "one.step")
        found = read if read.label == "grey" else next(node for node in read.children if node.label == "grey")
        for got, want in zip(tuple(found.color), (0.58, 0.62, 0.66, 1.0)):
            self.assertAlmostEqual(got, want, delta=1e-6)


class StepNameTest(unittest.TestCase):
    """A STEP's names read as the file means them: an occurrence OCCT left a placeholder name
    (``=>[0:1:1:2]``) takes its product's, and a name escaped as ISO 10303-21 escapes it reads
    as its text."""

    def setUp(self) -> None:
        import build123d as bd

        self.folder = Path(tempfile.mkdtemp(prefix="engine-step-names-")).resolve()
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        board = bd.Box(4, 4, 1)
        board.label = "Board"
        boss = bd.Pos(10, 0, 0) * bd.Cylinder(1, 2)
        boss.label = "凸台-拉伸4"
        tree = document.collect(bd.Compound(children=[board, boss], label="hat"), "hat")
        tree.root.components[0].name = "=>[0:1:1:2]"  # as OCCT names an occurrence its file left unnamed
        self.step = self.folder / "hat.step"
        exports.write_step(document.to_xcaf(tree), self.step)

    def names(self, path: Path) -> tuple[list[str], list[str]]:
        loaded = document.Loaded(path)
        read = document.read_step_shape(path)
        return [instance.path[-1] for instance in loaded.instances], [child.label for child in read.children]

    def test_a_placeholder_occurrence_name_is_its_product_s(self) -> None:
        self.assertIn("=>[0:1:1:2]", self.step.read_text(encoding="utf-8"))
        paths, labels = self.names(self.step)
        self.assertEqual(paths, ["Board", "凸台-拉伸4"])
        self.assertEqual(labels, ["Board", "凸台-拉伸4"])


if __name__ == "__main__":
    unittest.main()
