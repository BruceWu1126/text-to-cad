"""Materials: the value and its catalogue, a part's and a face's material in the xbf and
read back, an assembly's material taken by the parts below it, STEP's name and density,
GLB's PBR materials, and a material-only edit -- no kernel call, the xbf rewritten,
every geometry key as it was."""

from __future__ import annotations

import json
import os
import shutil
import struct
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from cadgen.engine import Material, materials
from cadgen.engine import build as engine_build
from cadgen.engine import document, exports

MODEL = """
import build123d as bd
from cadgen.engine import Material, materials, paint, step, xbf

GLOW = Material("Glow", 1500, color=(0.2, 0.9, 0.3), metallic=0.25, roughness=0.75, emissive=(0.5, 0.25, 0.0),
                description="a lit plastic")


@xbf
@step(out="plate.step")
def plate():
    body = bd.Box(20, 10, 4) - bd.Cylinder(2, 10)
    body.label = "body"
    body.material = materials.{body}
    faces = body.faces().sort_by(bd.Axis.Z)
    paint(body, faces[-1], GLOW)
    paint(body, faces[0], Material("Bare", 900))
    pin = bd.Cylinder(1, 8).moved(bd.Location((30, 0, 0)))
    pin.label = "pin"
    {pin}
    root = bd.Compound(children=[body, pin], label="plate")
    root.material = materials.{root}
    return root
"""


def _glb_json(path: Path) -> dict:
    data = path.read_bytes()
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    assert (magic, version, length) == (b"glTF", 2, len(data))
    chunk, kind = struct.unpack_from("<I4s", data, 12)
    assert kind == b"JSON"
    return json.loads(data[20:20 + chunk])


class MaterialValueTest(unittest.TestCase):
    def test_fields_are_validated_by_name(self) -> None:
        made = Material("Thing", 1000, color=(1, 0, 0))
        self.assertEqual(made.color, (1.0, 0.0, 0.0, 1.0))
        self.assertEqual((made.metallic, made.roughness, made.emissive), (0.0, 0.5, None))
        for fields, named in (({"name": ""}, "name"), ({"density": -1}, "density"), ({"density": "x"}, "density"),
                              ({"color": (1, 0)}, "color"), ({"color": (2, 0, 0)}, "color"), ({"color": "red"}, "color"),
                              ({"metallic": 1.5}, "metallic"), ({"roughness": -0.1}, "roughness"),
                              ({"emissive": (1, 1, 1, 1)}, "emissive"), ({"description": 3}, "description")):
            with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, f"Material {named}"):
                Material(**{"name": "Thing", **fields})
        with self.assertRaises(Exception):
            made.name = "Other"  # a frozen value

    def test_the_catalogue_s_entries_are_valid_and_distinct(self) -> None:
        entries = list(materials.CATALOGUE.values())
        self.assertGreaterEqual(len(entries), 12)
        for entry in entries:
            self.assertIsInstance(entry, Material)
            self.assertIsNotNone(entry.density)
            self.assertIsNotNone(entry.color)
        self.assertEqual(len({entry.name for entry in entries}), len(entries))
        self.assertEqual(len(set(entries)), len(entries))
        self.assertIs(materials.aluminium, materials.CATALOGUE["aluminium"])


class MaterialDocumentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="engine-materials-")).resolve()
        self.addCleanup(lambda: shutil.rmtree(self.folder, ignore_errors=True))
        self.script = self.folder / "plate.py"

    def build(self, body: str = "steel", root: str = "aluminium", pin: str = "pass") -> dict:
        self.script.write_text(textwrap.dedent(MODEL.format(body=body, root=root, pin=pin)), encoding="utf-8")
        os.utime(self.script, ns=(time.time_ns(), time.time_ns()))
        report = engine_build.build(self.script, ["plate"], cwd=self.folder)[0]
        self.assertTrue(report["ok"], report)
        return report

    def prototypes(self, name: str = "plate.xbf") -> dict:
        return {p.name: p for p in document.Loaded(self.folder / name).prototypes}

    def test_a_part_and_its_faces_keep_their_materials_through_the_xbf(self) -> None:
        self.build()
        body = self.prototypes()["body"]
        steel = materials.steel
        self.assertEqual((body.material.name, body.material.density), (steel.name, steel.density))
        self.assertEqual((body.material.metallic, body.material.roughness), (steel.metallic, steel.roughness))
        self.assertEqual(body.material.color, steel.color)
        self.assertEqual(body.color, steel.color, "a reader of colours alone sees the material's")
        glow, bare = body.face_materials.values()
        self.assertEqual((glow.name, glow.density, glow.description), ("Glow", 1500.0, "a lit plastic"))
        self.assertEqual((glow.metallic, glow.roughness, glow.emissive), (0.25, 0.75, (0.5, 0.25, 0.0)))
        self.assertEqual(glow.color, (0.2, 0.9, 0.3, 1.0))
        # A face's material without a colour is drawn in its part's.
        self.assertEqual((bare.name, bare.density, bare.color), ("Bare", 900.0, steel.color))
        self.assertEqual(sorted(body.face_colors.values()), sorted([steel.color, (0.2, 0.9, 0.3, 1.0)]))
        # The structure the page reads: the same from the file and from the build's tree.
        structure = document.Loaded(self.folder / "plate.xbf").structure()
        made = document.tree_structure(document.saved(self.folder / "plate.xbf"), self.folder / "plate.xbf")
        self.assertEqual(structure["prototypes"], made["prototypes"])
        entry = next(p for p in structure["prototypes"] if p["name"] == "body")
        self.assertEqual(entry["material"]["name"], steel.name)
        self.assertEqual({m["name"] for m in entry["faceMaterials"].values()}, {"Glow", "Bare"})
        self.assertEqual(entry["faceMaterials"][next(k for k, m in entry["faceMaterials"].items()
                                                     if m["name"] == "Glow")]["emissive"], [0.5, 0.25, 0.0])

    def test_an_assembly_s_material_is_every_part_s_below_it_that_has_none(self) -> None:
        self.build(pin="pin.material = materials.brass")
        found = self.prototypes()
        self.assertEqual(found["body"].material.name, materials.steel.name)
        self.assertEqual(found["pin"].material.name, materials.brass.name)
        self.build(pin="pass")
        found = self.prototypes()
        self.assertEqual(found["body"].material.name, materials.steel.name, "a part's own wins")
        self.assertEqual(found["pin"].material.name, materials.aluminium.name)
        self.assertEqual(found["pin"].color, materials.aluminium.color)

    def test_step_carries_each_part_s_material_name_and_density(self) -> None:
        self.build()
        found = self.prototypes("plate.step")
        self.assertEqual((found["body"].material.name, found["body"].material.density),
                         (materials.steel.name, materials.steel.density))
        self.assertEqual((found["pin"].material.name, found["pin"].material.density),
                         (materials.aluminium.name, materials.aluminium.density))
        self.assertEqual(found["pin"].color, materials.aluminium.color)

    def test_glb_has_pbr_materials_from_the_document(self) -> None:
        self.build()
        loaded = document.Loaded(self.folder / "plate.xbf")
        path = self.folder / "plate.glb"
        exports.write_from_loaded("glb", loaded, path)  # ``cadgen xbf export``: the xbf alone
        gltf = _glb_json(path)
        by_name = {entry.get("name"): entry for entry in gltf["materials"]}
        self.assertEqual(set(by_name), {materials.steel.name, materials.aluminium.name, "Glow", "Bare"})
        for entry in gltf["materials"]:
            pbr = entry["pbrMetallicRoughness"]
            self.assertEqual(len(pbr["baseColorFactor"]), 4)
            self.assertIn(entry["alphaMode"], ("OPAQUE", "BLEND"))
            self.assertTrue(0 <= pbr["metallicFactor"] <= 1 and 0 <= pbr["roughnessFactor"] <= 1)
        steel = by_name[materials.steel.name]["pbrMetallicRoughness"]
        self.assertEqual((steel["metallicFactor"], steel["roughnessFactor"]), (1.0, 0.45))
        self.assertEqual(by_name["Glow"]["emissiveFactor"], [0.5, 0.25, 0.0])
        self.assertEqual(by_name["Glow"]["pbrMetallicRoughness"]["roughnessFactor"], 0.75)
        used = {primitive["material"] for mesh in gltf["meshes"] for primitive in mesh["primitives"]}
        self.assertEqual(used, set(range(len(gltf["materials"]))))

    def test_a_material_only_edit_calls_no_kernel_rewrites_the_xbf_and_keeps_every_key(self) -> None:
        self.build()
        before = document.Loaded(self.folder / "plate.xbf")
        report = self.build(body="titanium", root="pla", pin="pin.material = materials.copper")
        self.assertEqual(report["memo"]["misses"], 0, report["memo"])
        self.assertEqual(report["memo"]["uncached"], 0, report["memo"])
        self.assertTrue(report["files"][str(self.folder / "plate.xbf")]["written"])
        after = document.Loaded(self.folder / "plate.xbf")
        self.assertNotEqual(after.structure()["version"], before.structure()["version"])
        self.assertEqual({p.name: p.key for p in after.prototypes}, {p.name: p.key for p in before.prototypes})
        found = {p.name: p for p in after.prototypes}
        self.assertEqual(found["body"].material.name, materials.titanium.name)
        self.assertEqual(found["pin"].material.name, materials.copper.name)

    def test_a_hit_hands_back_the_material_the_first_run_set(self) -> None:
        # The pin is a copy of a shape with a material; the first run then sets another on
        # the very object the copy's entry holds. Without that line, the copy hits and the
        # object comes back as the copy returned it, with the material it was made with.
        self.script.write_text(textwrap.dedent("""
            import build123d as bd
            from cadgen.engine import materials, xbf

            @xbf
            def pair():
                left = bd.Box(4, 4, 4)
                left.label = "left"
                left.material = materials.steel
                right = left.moved(bd.Location((10, 0, 0)))
                right.label = "right"
                {line}
                return bd.Compound(children=[left, right], label="pair")
            """).format(line="right.material = materials.copper"), encoding="utf-8")
        engine_build.build(self.script, ["pair"], cwd=self.folder)
        self.assertEqual(self.prototypes("pair.xbf")["right"].material.name, materials.copper.name)
        self.script.write_text(self.script.read_text(encoding="utf-8").replace(
            "right.material = materials.copper", "pass"), encoding="utf-8")
        os.utime(self.script, ns=(time.time_ns(), time.time_ns()))
        report = engine_build.build(self.script, ["pair"], cwd=self.folder)[0]
        self.assertEqual(report["memo"]["misses"], 0, report["memo"])
        structure = document.Loaded(self.folder / "pair.xbf").structure()
        placed = {i["path"]: structure["prototypes"][i["prototype"]] for i in structure["instances"]}
        self.assertEqual(placed["pair/right"]["material"]["name"], materials.steel.name)
        self.assertEqual(len(structure["prototypes"]), 1, "two placements of one steel box")


if __name__ == "__main__":
    unittest.main()
