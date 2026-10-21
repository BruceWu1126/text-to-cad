"""Exports: a document translated to STEP, GLB, STL, 3MF and xbf by one writer
(``export.translate``), whether a decorator declares the file or ``cadgen xbf export`` asks
for it. The STEP reads back through the engine's own reader with its names, colours,
materials and instance tree; the meshes are parsed and counted; a pose moves a placement and
a part is a subtree of its own; a build keeps a file its document did not change, writes only
the files its flags select, and code built from text writes the files its flags name."""

from __future__ import annotations

import json
import os
import shutil
import struct
import tempfile
import textwrap
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree

import numpy as np

from cadgen.engine import api, document, export, mesh
from cadgen.engine import build as engine_build
from cadgen.engine.export import ExportRefused, Source, translate

BOARD = """
import build123d as bd
from cadgen.engine import configuration, glb, materials, paint, step, stl, threemf, xbf


@xbf
def peg():
    peg = bd.Cylinder(2, 10)
    peg.color = bd.Color(0.9, 0.2, 0.1)
    return peg


@xbf
@step("board.step")
@glb
@stl
@threemf
def board():
    plate = bd.Pos(0, -20, 0) * bd.Box(30, 10, 2)
    plate.color = bd.Color(0.2, 0.3, 0.9)
    plate = paint(plate, plate.faces().sort_by(bd.Axis.Z)[-1], (1, 0, 0))
    plate = paint(plate, plate.faces().sort_by(bd.Axis.Z)[0], materials.steel)
    plate.material = materials.aluminium
    plate.label = "plate"
    pegs = [peg().moved(bd.Location((x, 0, 0))) for x in (-10, 0, 10)]
    for index, placed in enumerate(pegs):
        placed.label = f"peg{{index}}"
    rack = bd.Compound(children=pegs, label="rack").moved(bd.Location((0, 0, {lift})))
    base = bd.Box(10, 10, 2)
    base.label = "base"
    lid = bd.Box(10, 10, 1, align=(bd.Align.MIN, bd.Align.CENTER, bd.Align.MIN))
    lid.label = "lid"
    bd.RevoluteJoint("flap", base, axis=bd.Axis((5, 0, 1), (0, 1, 0)), angular_range=(0, 90))
    bd.RigidJoint("mount", lid, bd.Location())
    base.joints["flap"].connect_to(lid.joints["mount"], angle=0)
    hinge = bd.Compound(children=[base, lid], label="hinge")
    configuration(hinge, "open", flap=60)
    return bd.Compound(children=[plate, rack, hinge], label="board")
"""


def _glb_json(path: Path) -> dict:
    data = path.read_bytes()
    length, kind = struct.unpack_from("<I4s", data, 12)
    assert kind == b"JSON"
    return json.loads(data[20:20 + length])


def _facts(loaded: document.Loaded, prototype: document.Prototype) -> dict[str, dict]:
    """Each named element of a part by where it is (a face's centroid, an edge's midpoint, a
    vertex's point, rounded): names compared by geometry, not by the reader's ordinals."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_VERTEX
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_IndexedMapOfShape

    def where(kind: int, shape) -> tuple:
        if kind == TopAbs_FACE:
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(shape, props)
            point = props.CentreOfMass()
        elif kind == TopAbs_EDGE:
            curve = BRepAdaptor_Curve(TopoDS.Edge_s(shape))
            point = curve.Value((curve.FirstParameter() + curve.LastParameter()) / 2)
        else:
            point = BRep_Tool.Pnt_s(TopoDS.Vertex_s(shape))
        return tuple(round(value, 4) for value in (point.X(), point.Y(), point.Z()))

    found = {}
    for kind, names, label in ((TopAbs_FACE, prototype.face_names, "faces"),
                               (TopAbs_EDGE, prototype.edge_names, "edges"),
                               (TopAbs_VERTEX, prototype.vertex_names, "vertices")):
        mapped = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(prototype.shape, kind, mapped)
        found[label] = {where(kind, mapped.FindKey(ordinal)): name for ordinal, name in names.items()}
    return found


class ExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.folder = Path(tempfile.mkdtemp(prefix="engine-exports-")).resolve()
        cls.script = cls.folder / "board.py"
        cls.script.write_text(textwrap.dedent(BOARD.format(lift=6)), encoding="utf-8")
        reports = engine_build.build(cls.script, ["board"], cwd=cls.folder)
        assert reports[-1]["ok"], reports
        cls.report = reports[-1]
        cls.xbf = cls.folder / "XBF/board.xbf"
        cls.loaded = document.Loaded(cls.xbf)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.folder, ignore_errors=True)

    def test_the_build_writes_the_xbf_first_then_every_declared_file(self) -> None:
        self.assertEqual(list(self.report["files"]), [str(self.xbf)] + [str(self.folder / name) for name in (
            "board.step", "board.glb", "board.stl", "board.3mf")])
        self.assertTrue(all(row["written"] for row in self.report["files"].values()))

    def test_a_step_reads_back_with_its_names_colours_materials_and_tree(self) -> None:
        step = document.Loaded(self.folder / "board.step")
        built, read = self.loaded.structure(), step.structure()

        def names(node: dict) -> list:
            return [node["name"], [names(child) for child in node.get("children", [])]]

        self.assertEqual(names(read["tree"]), names(built["tree"]), "the instance tree and every name")
        self.assertEqual([i["path"] for i in read["instances"]], [i["path"] for i in built["instances"]])
        for one, two in zip(read["instances"], built["instances"]):
            np.testing.assert_allclose(one["matrix"], two["matrix"], atol=1e-9)
        self.assertEqual([i["prototype"] for i in read["instances"]], [i["prototype"] for i in built["instances"]],
                         "a part placed three times is one product placed three times")
        ours = {p.name: p for p in self.loaded.prototypes}
        theirs = {p.name: p for p in step.prototypes}
        self.assertEqual(set(theirs), set(ours))
        for name, prototype in ours.items():
            other = theirs[name]
            self.assertEqual(other.color, prototype.color, name)
            # Every face, edge and vertex keeps its element name, on the same element.
            self.assertEqual(_facts(step, other), _facts(self.loaded, prototype), name)
            self.assertTrue(prototype.face_names and prototype.edge_names, name)
        plate, read_plate = ours["plate"], theirs["plate"]
        self.assertEqual((read_plate.material.name, read_plate.material.density),
                         (plate.material.name, plate.material.density), "a material's name and density")
        # A painted colour is a face's colour in the STEP as in the xbf.
        red = [rgba for rgba in plate.face_colors.values() if rgba == (1.0, 0.0, 0.0, 1.0)]
        self.assertEqual(red, [rgba for rgba in read_plate.face_colors.values() if rgba == (1.0, 0.0, 0.0, 1.0)])
        # What OCCT's STEP writer drops: the PBR look (metallic, roughness), a face's material
        # (its colour stays), and the mechanism.
        self.assertEqual(plate.material.metallic, 1.0)
        self.assertEqual(read_plate.material.metallic, 0.0)
        self.assertEqual(len(plate.face_materials), 1)
        self.assertEqual(read_plate.face_materials, {})
        [ordinal] = plate.face_materials
        self.assertEqual(read_plate.face_colors[ordinal][:3], plate.face_colors.get(ordinal, plate.face_materials[
            ordinal].color)[:3])
        self.assertIn("kinematics", built)
        self.assertNotIn("kinematics", read)

    def test_glb_holds_a_mesh_per_part_a_node_per_placement_and_paints_apart(self) -> None:
        gltf = _glb_json(self.folder / "board.glb")
        placed = [node for node in gltf["nodes"] if "mesh" in node]
        self.assertEqual(sorted(node["name"] for node in placed),
                         ["hinge/base", "hinge/lid", "plate", "rack/peg0", "rack/peg1", "rack/peg2"])
        self.assertEqual(len(gltf["meshes"]), 4, "the peg placed three times is one mesh")
        plate = next(entry for entry in gltf["meshes"] if entry["name"] == "plate")
        looks = [gltf["materials"][primitive["material"]] for primitive in plate["primitives"]]
        self.assertEqual(len(looks), 3, "the part's own look, a painted colour and a painted material")
        by_name = {look.get("name"): look for look in looks}
        self.assertEqual(by_name["Aluminum Generic"]["pbrMetallicRoughness"]["metallicFactor"], 1.0)
        self.assertIn("Steel-Generic", by_name)
        self.assertIn([1.0, 0.0, 0.0, 1.0], [look["pbrMetallicRoughness"]["baseColorFactor"] for look in looks])
        rack = next(node for node in placed if node["name"] == "rack/peg0")
        self.assertEqual(rack["matrix"][12:15], [-10.0, 0.0, 6.0])
        # The root shows the document's Z-up millimetres in glTF's Y-up metres.
        root = gltf["nodes"][gltf["scenes"][0]["nodes"][0]]
        self.assertEqual(root["scale"], [0.001, 0.001, 0.001])
        np.testing.assert_allclose(root["rotation"], [-0.5 ** 0.5, 0, 0, 0.5 ** 0.5])

    def test_3mf_holds_an_object_per_part_an_item_per_placement_and_its_looks(self) -> None:
        with zipfile.ZipFile(self.folder / "board.3mf") as archive:
            model = ElementTree.fromstring(archive.read("3D/3dmodel.model"))
        core = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
        materials = "{http://schemas.microsoft.com/3dmanufacturing/material/2015/02}"
        objects = model.findall(f"{core}resources/{core}object")
        self.assertEqual(sorted(entry.get("name") for entry in objects), ["base", "lid", "peg0", "plate"])
        self.assertEqual(len(model.findall(f"{core}build/{core}item")), 6)
        bases = model.findall(f"{core}resources/{core}basematerials/{core}base")
        metallic = model.findall(f"{core}resources/{materials}pbmetallicdisplayproperties/{materials}pbmetallic")
        self.assertEqual(len(metallic), len(bases), "one display property per base material")
        self.assertIn("Aluminum Generic", [base.get("name") for base in bases])
        plate = next(entry for entry in objects if entry.get("name") == "plate")
        painted = [t for t in plate.iter(f"{core}triangle") if t.get("p1") is not None]
        self.assertEqual(len(painted), 4, "the two painted faces' triangles name their own looks")

    def test_a_3mf_is_the_same_bytes_whatever_the_clock(self) -> None:
        # zipfile stamps an entry with ``time.localtime()``: two clocks a day apart.
        written = []
        for seconds in (1_700_000_000, 1_700_086_403):
            out = self.folder / f"clock-{seconds}.3mf"
            with mock.patch("time.localtime", return_value=time.gmtime(seconds)):
                translate(self.xbf, None, out)
            written.append(out.read_bytes())
        self.assertEqual(written[0], written[1])
        with zipfile.ZipFile(self.folder / "clock-1700000000.3mf") as archive:
            self.assertEqual({entry.date_time for entry in archive.infolist()}, {export.ZIP_DATE})

    def test_stl_holds_every_placed_triangle(self) -> None:
        data = (self.folder / "board.stl").read_bytes()
        count = struct.unpack_from("<I", data, 80)[0]
        view = Source.read(self.xbf).view()
        held = export.mesh.meshes(view)
        self.assertEqual(count, sum(held[placed.prototype].triangles for placed in view.placements))
        self.assertEqual(len(data), 84 + 50 * count)

    def test_a_pose_moves_the_placements_its_mechanism_moves(self) -> None:
        out = self.folder / "open.glb"
        translate(self.xbf, None, out, pose="open")
        nodes = {node["name"]: node for node in _glb_json(out)["nodes"] if "mesh" in node}
        built = {node["name"]: node for node in _glb_json(self.folder / "board.glb")["nodes"] if "mesh" in node}
        self.assertEqual(nodes["hinge/base"]["matrix"], built["hinge/base"]["matrix"])
        self.assertNotEqual(nodes["hinge/lid"]["matrix"], built["hinge/lid"]["matrix"])
        from cadgen.engine import mechanism

        structure = self.loaded.structure()
        values, _named = mechanism.resolve(structure["kinematics"], "open")
        lid = next(at for at, i in enumerate(structure["instances"]) if i["path"].endswith("lid"))
        np.testing.assert_allclose(nodes["hinge/lid"]["matrix"], mechanism.posed_instances(structure, values)[lid],
                                   atol=1e-6)
        with self.assertRaisesRegex(ExportRefused, "no configuration 'shut'"):
            translate(self.xbf, None, self.folder / "shut.glb", pose="shut")
        self.assertFalse((self.folder / "shut.glb").exists())

    def test_a_part_is_its_subtree_as_a_document_of_its_own(self) -> None:
        rack = self.folder / "rack.xbf"
        translate(self.xbf, None, rack, part="rack")
        loaded = document.Loaded(rack)
        self.assertEqual(loaded.name, "rack")
        self.assertEqual([i.path for i in loaded.instances], [["rack", "peg0"], ["rack", "peg1"], ["rack", "peg2"]])
        # Its root is the subtree's frame: the rack's lift is not in it.
        self.assertEqual([i.matrix[12:15] for i in loaded.instances], [[-10.0, 0.0, 0.0], [0.0, 0.0, 0.0],
                                                                      [10.0, 0.0, 0.0]])
        self.assertEqual(len(loaded.prototypes), 1)
        self.assertEqual(loaded.prototypes[0].face_names, next(p for p in self.loaded.prototypes
                                                               if p.name == "peg0").face_names)
        self.assertRegex(loaded.writer or "", r"^cadgen\.engine xbf/1")
        step = self.folder / "rack.step"
        translate(self.xbf, None, step, part="board/rack")
        self.assertEqual([i.path for i in document.Loaded(step).instances], [i.path for i in loaded.instances])
        # A subtree's share of the mechanism goes with it into its xbf.
        hinge = self.folder / "hinge.xbf"
        translate(self.xbf, None, hinge, part="hinge")
        found = document.Loaded(hinge).mechanism
        self.assertEqual([pair.label for pair in found.pairs], ["flap"])
        self.assertEqual(found.configurations, {"open": {"flap": (60.0,)}})
        self.assertIsNone(document.Loaded(rack).mechanism)
        with self.assertRaisesRegex(ExportRefused, "no part 'nothing'"):
            translate(self.xbf, None, self.folder / "nothing.step", part="nothing")

    def test_what_is_no_document_or_no_new_one_is_refused_by_name(self) -> None:
        with self.assertRaisesRegex(ExportRefused, "only with --part"):
            translate(self.xbf, None, self.folder / "again.xbf")
        with self.assertRaisesRegex(ExportRefused, "a posed xbf is refused"):
            translate(self.xbf, None, self.folder / "posed.xbf", pose="open")
        with self.assertRaisesRegex(ExportRefused, "is a mesh"):
            translate(self.folder / "board.glb", None, self.folder / "back.step")
        with self.assertRaisesRegex(ExportRefused, "an export is one of"):
            translate(self.xbf, None, self.folder / "board.obj")

    def test_a_step_becomes_an_xbf(self) -> None:
        out = self.folder / "from-step.xbf"
        translate(self.folder / "board.step", None, out)
        loaded = document.Loaded(out)
        self.assertEqual(sorted(p.name for p in loaded.prototypes), sorted(p.name for p in self.loaded.prototypes))
        self.assertTrue(all(len(p.key) == 32 for p in loaded.prototypes), "keyed by geometry, as a build keys a part")
        plate = next(p for p in loaded.prototypes if p.name == "plate")
        self.assertEqual(sorted(plate.face_names.values()),
                         sorted(next(p for p in self.loaded.prototypes if p.name == "plate").face_names.values()))

    def test_every_mesh_comes_from_the_one_cache(self) -> None:
        from cadgen.engine.serve import MESHES

        keys = {p.key for p in self.loaded.prototypes}
        self.assertTrue(keys <= set(MESHES.keys()), "the build's exports left their meshes in the process's cache")
        with mock.patch.object(mesh, "mesh_shape", side_effect=AssertionError("meshed again")):
            translate(self.xbf, None, self.folder / "again.stl")
            translate(self.xbf, None, self.folder / "again.3mf")


class BuildFilesTest(unittest.TestCase):
    """What a build writes: what its decorators declare, or what its flags select."""

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="engine-files-")).resolve()
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)

    def files(self) -> set[str]:
        return {str(path.relative_to(self.folder)) for path in self.folder.rglob("*") if path.is_file()}

    def write(self, name: str, text: str) -> Path:
        script = self.folder / name
        script.write_text(textwrap.dedent(text), encoding="utf-8")
        os.utime(script, ns=(time.time_ns(), time.time_ns()))
        return script

    def test_an_unchanged_document_keeps_its_files(self) -> None:
        script = self.write("board.py", BOARD.format(lift=6))
        engine_build.build(script, ["board"], cwd=self.folder)
        stats = {path: os.stat(self.folder / path).st_mtime_ns for path in self.files() if path != "board.py"}
        [report] = engine_build.build(script, ["board"], cwd=self.folder)
        self.assertTrue(report["ok"], report)
        self.assertFalse(any(row["written"] for row in report["files"].values()), report["files"])
        self.assertEqual({path: os.stat(self.folder / path).st_mtime_ns for path in stats}, stats)
        # A file removed is written again, and only it.
        (self.folder / "board.stl").unlink()
        [report] = engine_build.build(script, ["board"], cwd=self.folder)
        self.assertEqual([Path(path).name for path, row in report["files"].items() if row["written"]], ["board.stl"])

    def test_file_flags_select_and_override_what_the_decorators_declare(self) -> None:
        script = self.write("board.py", BOARD.format(lift=6))
        [report] = engine_build.build(script, ["board"], cwd=self.folder, outputs={"xbf": {}})
        self.assertEqual(self.files(), {"board.py", "XBF/board.xbf"}, "--xbf alone: the xbf alone")
        [report] = engine_build.build(script, ["board"], cwd=self.folder,
                                      outputs={"xbf": {}, "step": {"out": "elsewhere/b.step"}, "glb": {}})
        self.assertTrue(report["ok"], report)
        self.assertEqual(self.files(), {"board.py", "XBF/board.xbf", "elsewhere/b.step", "board.glb"})
        self.assertFalse(report["files"][str(self.folder / "XBF/board.xbf")]["written"], "the document did not change")
        with self.assertRaisesRegex(engine_build.BuildError, "a flag names one file"):
            engine_build.build(script, None, cwd=self.folder, outputs={"step": {"out": "x.step"}})

    def test_a_derived_decorator_alone_writes_its_one_file(self) -> None:
        script = self.write("solo.py", """
            import build123d as bd
            from cadgen.engine import step


            @step
            def solo():
                return bd.Box(1, 2, 3)
        """)
        [report] = engine_build.build(script, None, cwd=self.folder)
        self.assertTrue(report["ok"], report)
        self.assertEqual(self.files(), {"solo.py", "solo.step"})

    def test_a_decorator_takes_out_alone_and_a_file_shows_the_joints_pose(self) -> None:
        import cadgen.engine
        from cadgen.engine import model
        from cadgen.engine.model import glb, step, xbf

        with self.assertRaisesRegex(TypeError, "takes out alone, not material"):
            step("a.step", material="steel")
        with self.assertRaisesRegex(TypeError, "takes out alone, not pose"):
            step("a.step", pose="open")
        with self.assertRaisesRegex(TypeError, "takes out alone, not out2"):
            xbf(out2="a.xbf")
        with self.assertRaisesRegex(TypeError, "takes out alone, not animate"):
            glb("a.glb", animate={"flap": (0, 90)})
        self.assertFalse(hasattr(model, "snapshot"), "a picture is the snapshot verb's, never a declaration")
        self.assertNotIn("snapshot", cadgen.engine.__all__)
        # As built is where the joints connect the parts: lid at 30 degrees in every file.
        script = self.write("hinged.py", BOARD.format(lift=6).replace("connect_to(lid.joints[\"mount\"], angle=0)",
                                                                      "connect_to(lid.joints[\"mount\"], angle=30)"))
        [report] = engine_build.build(script, ["board"], cwd=self.folder, outputs={"xbf": {}, "step": {}})
        self.assertTrue(report["ok"], report)
        built = document.Loaded(self.folder / "XBF/board.xbf").structure()
        self.assertEqual(built["kinematics"]["asBuilt"], {"flap": 30.0})
        self.assertIn("open", built["kinematics"]["configurations"], "a configuration names a preset")

        def lid(structure: dict) -> list[float]:
            return [round(v, 6) for v in next(i for i in structure["instances"] if i["path"].endswith("/lid"))["matrix"]]

        self.assertEqual(lid(document.Loaded(self.folder / "board.step").structure()), lid(built),
                         "the STEP shows the lid where its joint put it, not at a configuration")
        with self.assertRaisesRegex(engine_build.BuildError, "takes no pose"):
            engine_build.build(script, ["board"], cwd=self.folder, outputs={"step": {"pose": "open"}})

    def test_an_export_from_a_script_builds_it_then_writes(self) -> None:
        script = self.write("board.py", BOARD.format(lift=6))
        out = self.folder / "made/board.stl"
        written = api.export(f"{script}::board", out, self.folder / "made/rack.3mf", part=None, daemon=False)
        self.assertEqual([row.path for row in written], [out, self.folder / "made/rack.3mf"])
        self.assertTrue(out.is_file())
        self.assertIn("XBF/board.xbf", self.files(), "the model's document was built")
        self.assertNotIn("board.step", self.files(), "and none of its declared derived files")


class CodeTest(unittest.TestCase):
    """Code built from source text: one model, named by ``name``, assigning ``result``."""

    CODE = "import build123d as bd\nbody = bd.Box(4, 4, 2)\nresult = body - bd.Cylinder(1, {height})\n"

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="engine-code-")).resolve()
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.script = engine_build.code_script("plug", self.folder)

    def build(self, code: str, outputs: dict | None = None) -> dict:
        [report] = engine_build.build(self.script, ["plug"], cwd=self.folder, outputs=outputs, code=code)
        return report

    def test_code_assigns_result(self) -> None:
        with self.assertRaisesRegex(engine_build.BuildError, "assigns no `result`"):
            self.build("import build123d as bd\nshape = bd.Box(1, 1, 1)\n")
        with self.assertRaisesRegex(engine_build.BuildError, "--step takes the file's path"):
            self.build(self.CODE.format(height=5), {"step": {}})

    def test_code_is_named_warm_and_cold_cached_by_its_xbf(self) -> None:
        outputs = {"xbf": {"out": "plug.xbf"}, "stl": {"out": "plug.stl"}}
        first = self.build(self.CODE.format(height=5), outputs)
        self.assertTrue(first["ok"], first)
        self.assertEqual({path.name for path in self.folder.iterdir()}, {"plug.xbf", "plug.stl"},
                         "the code's files, and nothing for the code itself")
        loaded = document.Loaded(self.folder / "plug.xbf")
        names = list(loaded.prototypes[0].face_names.values())
        self.assertTrue(names and all(name.startswith(("f:plug.body", "f:plug.result")) for name in names), names)
        warm = self.build(self.CODE.format(height=5), outputs)
        self.assertEqual(warm["bodies"], {"plug.py::plug": "replayed"})
        self.assertFalse(any(row["written"] for row in warm["files"].values()))
        # A new worker: the xbf holds the code's key, and nothing runs.
        from cadgen.engine.kernel import memo

        memo.clear()
        engine_build._WRITTEN.clear()
        engine_build._PARTS.clear()
        cold = self.build(self.CODE.format(height=5), outputs)
        self.assertEqual(cold["cache"], {"used": True, "from": "file"}, cold)
        edited = self.build(self.CODE.format(height=6), outputs)
        self.assertTrue(edited["files"][str(self.folder / "plug.xbf")]["written"])


class StepTest(unittest.TestCase):
    """A STEP's colours and names read as the file means them."""

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="engine-step-")).resolve()
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)

    def write(self, result, name: str) -> Path:
        from cadgen.engine.export.step import write_document

        path = self.folder / f"{name}.step"
        tree = result if isinstance(result, document.Tree) else document.collect(result, name)
        write_document(document.to_xcaf(tree), path)
        return path

    def test_a_colour_written_to_step_reads_back_as_written(self) -> None:
        import build123d as bd

        part = bd.Box(4, 4, 4)
        part.label = "grey"
        part.color = bd.Color(0.58, 0.62, 0.66)
        read = document.read_step_shape(self.write(bd.Compound(children=[part], label="one"), "one"))
        found = read if read.label == "grey" else next(node for node in read.children if node.label == "grey")
        for got, want in zip(tuple(found.color), (0.58, 0.62, 0.66, 1.0)):
            self.assertAlmostEqual(got, want, delta=1e-6)

    def test_a_placeholder_occurrence_name_is_its_product_s_and_escapes_read_as_text(self) -> None:
        import build123d as bd

        board = bd.Box(4, 4, 1)
        board.label = "Board"
        boss = bd.Pos(10, 0, 0) * bd.Cylinder(1, 2)
        boss.label = "凸台-拉伸4"
        tree = document.collect(bd.Compound(children=[board, boss], label="hat"), "hat")
        tree.root.components[0].name = "=>[0:1:1:2]"  # as OCCT names an occurrence its file left unnamed
        step = self.write(tree, "hat")
        self.assertIn("=>[0:1:1:2]", step.read_text(encoding="utf-8"))
        self.assertEqual([i.path[-1] for i in document.Loaded(step).instances], ["Board", "凸台-拉伸4"])
        self.assertEqual([child.label for child in document.read_step_shape(step).children], ["Board", "凸台-拉伸4"])
        text = step.read_text(encoding="utf-8")
        for escaped in (r"\X2\51F853F0\X0\-\X2\62C94F38\X0\4", r"\\X2\\51F853F0\\X0\\-\\X2\\62C94F38\\X0\\4"):
            path = self.folder / "escaped.step"
            path.write_text(text.replace("凸台-拉伸4", escaped), encoding="utf-8")
            self.assertEqual(document.Loaded(path).instances[-1].path[-1], "凸台-拉伸4", escaped)
            self.assertEqual(document.Loaded(path).prototypes[-1].name, "凸台-拉伸4", escaped)
        self.assertEqual(document.step_name("caf\\X\\E9 \\X4\\0001F600\\X0\\"), "café 😀")


if __name__ == "__main__":
    unittest.main()
