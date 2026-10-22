"""The files no document that the stills draw (`engine/formats`): each kind read into what a
renderer holds -- a mesh file's parts, colours, materials and placements; a GLB's node tree and
textures through OCCT's glTF reader; a robot posed by the viewer's forward kinematics -- and each
drawn as its golden picture.

The fixtures are made here, small: a binary and an ASCII STL, a 3MF of colour groups, base
materials and components, a GLB of a node tree, two materials and a base colour texture, a URDF
of every joint kind (a mimic and a four-bar among them), an SRDF and an SDF over it.

`formats_golden/kinematics.json` is the viewer's own forward kinematics on `KINEMATICS`: each
link's world transform (row-major, metres) at three poses, made by origin/main's
`@text-to-cad/core` `lib/urdf/parseUrdf.js` and `kinematics.js` (with jsdom's DOMParser) when the
port was written; the port must agree to 1e-9.

The pictures (`formats_golden/*.png`, 320x240, macOS) match as `test_render.py`'s do: at most 1.5%
of the pixels more than 32 levels from every golden pixel within one pixel. A test that draws makes
an OpenGL context and is skipped, saying why, where none can be made. A deliberate change to the
look makes them again: `CADGEN_RENDER_GOLDENS=write`.
"""

from __future__ import annotations

import io
import json
import math
import os
import shutil
import struct
import tempfile
import textwrap
import unittest
import zipfile
from pathlib import Path

import numpy as np

from cadgen.engine import formats
from cadgen.engine.formats.buffer import buffer, feature_edges
from cadgen.engine.render import context
from cadgen.engine.snapshot import SnapshotRefused, Still, snapshot

GOLDEN = Path(__file__).resolve().parent / "formats_golden"
WRITE = os.environ.get("CADGEN_RENDER_GOLDENS") == "write"
SIZE = (320, 240)

KINEMATICS = """<?xml version="1.0"?>
<robot name="kinematics" xmlns:tcad="https://text-to-cad.dev/urdf">
  <link name="base"><visual><origin xyz="0 0 0.01"/><geometry><box size="0.3 0.2 0.02"/></geometry></visual></link>
  <link name="turret"><visual><origin xyz="0 0 0.03"/><geometry><cylinder radius="0.04" length="0.06"/></geometry></visual></link>
  <link name="arm"><visual><origin xyz="0.06 0 0" rpy="0 1.5707963 0"/><geometry><cylinder radius="0.012" length="0.12"/></geometry></visual></link>
  <link name="slider"><visual><geometry><box size="0.03 0.03 0.03"/></geometry></visual></link>
  <link name="twin"><visual><origin xyz="0 0.02 0"/><geometry><box size="0.01 0.04 0.01"/></geometry></visual></link>
  <link name="tool"><visual><geometry><sphere radius="0.01"/></geometry></visual></link>
  <link name="ground"><visual><origin xyz="0.1 0 -0.01"/><geometry><box size="0.26 0.02 0.01"/></geometry></visual></link>
  <link name="rocker"><visual><origin xyz="0.04 0 0"/><geometry><box size="0.08 0.015 0.01"/></geometry></visual></link>
  <link name="crank"><visual><origin xyz="0.025 0 0"/><geometry><box size="0.05 0.015 0.01"/></geometry></visual></link>
  <joint name="spin" type="continuous"><parent link="base"/><child link="turret"/><origin xyz="0.05 -0.02 0.02" rpy="0 0 0.3"/><axis xyz="0 0 1"/></joint>
  <joint name="lift" type="revolute"><parent link="turret"/><child link="arm"/><origin xyz="0 0 0.06" rpy="0.2 -0.1 0"/><axis xyz="0 1 0.2"/><limit lower="-1.2" upper="1.4" effort="1" velocity="1"/></joint>
  <joint name="reach" type="prismatic"><parent link="arm"/><child link="slider"/><origin xyz="0.12 0 0" rpy="0 0 0.5"/><axis xyz="1 0 0"/><limit lower="-0.02" upper="0.05" effort="1" velocity="1"/></joint>
  <joint name="mirror" type="revolute"><parent link="arm"/><child link="twin"/><origin xyz="0.08 0 0.015"/><axis xyz="1 0 0"/><limit lower="-1" upper="1" effort="1" velocity="1"/><mimic joint="lift" multiplier="-0.5" offset="0.1"/></joint>
  <joint name="mount" type="fixed"><parent link="slider"/><child link="tool"/><origin xyz="0.02 0.01 0" rpy="0.4 0 0.2"/></joint>
  <joint name="foot" type="fixed"><parent link="base"/><child link="ground"/><origin xyz="-0.12 0.06 0.02" rpy="0 0 -0.4"/></joint>
  <joint name="output_joint" type="revolute"><parent link="ground"/><child link="rocker"/><origin xyz="0.2 0 0" rpy="0 0 1.5707963267948966"/><axis xyz="0 0 1"/><limit lower="-0.5" upper="0.5" effort="1" velocity="1"/></joint>
  <joint name="input_joint" type="revolute"><parent link="ground"/><child link="crank"/><origin xyz="0 0 0" rpy="0 0 1.5253680280265103"/><axis xyz="0 0 1"/><limit lower="-1.5" upper="1.5" effort="1" velocity="1"/>
    <tcad:four_bar driver="output_joint" input_length="0.05" ground_length="0.2" output_length="0.08" coupler_length="0.2" input_zero="1.5253680280265103" output_zero="1.5707963267948966"/>
  </joint>
</robot>
"""

SRDF = """<?xml version="1.0"?>
<robot name="kinematics">
  <group name="arm"><joint name="spin"/><joint name="lift"/><joint name="reach"/></group>
  <group_state name="home" group="arm"><joint name="spin" value="0.5"/><joint name="lift" value="0.3"/></group_state>
  <group_state name="tucked" group="arm"><joint name="lift" value="-1.0"/><joint name="reach" value="-0.02"/></group_state>
</robot>
"""

# A link at 1 m along x and 0.5 m up, its frame turned 90 degrees about z, relative_to a frame of
# the model; a hinge whose pose is its child's and whose axis is expressed in the model's frame.
SDF = """<?xml version="1.0"?>
<sdf version="1.9">
  <model name="hinge">
    <frame name="shelf"><pose>1 0 0.5 0 0 0</pose></frame>
    <link name="post"><visual name="v"><geometry><box><size>0.1 0.1 0.1</size></box></geometry></visual></link>
    <link name="door"><pose relative_to="shelf">0 0 0 0 0 1.5707963267948966</pose>
      <visual name="v"><pose>0.2 0 0 0 0 0</pose><geometry><box><size>0.4 0.02 0.3</size></box></geometry>
        <material><diffuse>0.8 0.2 0.1 1</diffuse></material></visual></link>
    <joint name="swing" type="revolute"><parent>post</parent><child>door</child>
      <axis><xyz expressed_in="__model__">0 0 1</xyz><limit><lower>-2</lower><upper>2</upper></limit></axis></joint>
  </model>
</sdf>
"""



def gl() -> None:
    reason = context.available()
    if reason is not None:
        raise unittest.SkipTest(f"no OpenGL context: {reason}")


def pixels(data: bytes) -> np.ndarray:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        return np.asarray(image.convert("RGB")).astype(np.int16)


def mismatch(found: np.ndarray, golden: np.ndarray) -> float:
    if found.shape != golden.shape:
        return 1.0
    height, width = golden.shape[:2]
    padded = np.pad(golden, ((1, 1), (1, 1), (0, 0)), mode="edge")
    best = np.full((height, width), 255, np.int16)
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            best = np.minimum(best, np.abs(found - padded[dy:dy + height, dx:dx + width]).max(-1))
    return float((best > 32).mean())


# --- fixtures --------------------------------------------------------------------------------


def box_soup(size: tuple[float, float, float], at: tuple[float, float, float] = (0, 0, 0)) -> np.ndarray:
    """A box as twelve triangles (M, 3, 3), wound outward."""
    corners = np.array([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)], float) - 0.5
    corners = corners * np.array(size) + np.array(at)
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    return np.array([[corners[a], corners[b], corners[c]] for a, b, c, d in faces for a, b, c in ((a, b, c), (a, c, d))])


def cylinder_soup(radius: float, height: float, segments: int = 24, at: tuple = (0, 0, 0)) -> np.ndarray:
    turn = np.linspace(0, 2 * math.pi, segments + 1)
    ring = np.stack([radius * np.cos(turn), radius * np.sin(turn)], 1)
    triangles = []
    for (x0, y0), (x1, y1) in zip(ring[:-1], ring[1:]):
        triangles += [[(x0, y0, 0), (x1, y1, 0), (x1, y1, height)], [(x0, y0, 0), (x1, y1, height), (x0, y0, height)],
                      [(0, 0, height), (x0, y0, height), (x1, y1, height)], [(0, 0, 0), (x1, y1, 0), (x0, y0, 0)]]
    return np.array(triangles, float) + np.array(at)


def write_stl(path: Path, triangles: np.ndarray, *, ascii_: bool = False) -> Path:
    if ascii_:
        rows = ["solid fixture"]
        for triangle in triangles:
            rows += ["facet normal 0 0 0", " outer loop", *(f"  vertex {float(x)!r} {float(y)!r} {float(z)!r}" for x, y, z in triangle),
                     " endloop", "endfacet"]
        path.write_text("\n".join(rows + ["endsolid fixture"]) + "\n", encoding="ascii")
        return path
    facets = np.zeros(len(triangles), [("normal", "<f4", 3), ("corners", "<f4", (3, 3)), ("attribute", "<u2")])
    facets["corners"] = triangles
    path.write_bytes(b"\0" * 80 + struct.pack("<I", len(triangles)) + facets.tobytes())
    return path


def write_3mf(path: Path) -> Path:
    """A plate whose top is painted by a colour group, a peg placed twice by a components object,
    a generated default base material (no colour), metallic display properties; in centimetres."""
    plate = box_soup((6, 4, 0.5), (0, 0, 0.25))
    peg = cylinder_soup(0.5, 1.5, 16)

    def mesh(triangles: np.ndarray, paints: list[str]) -> str:
        points, index = [], {}
        rows = []
        for triangle, paint in zip(triangles, paints):
            ids = []
            for point in map(tuple, triangle):
                if point not in index:
                    index[point] = len(points)
                    points.append(point)
                ids.append(index[point])
            rows.append(f'<triangle v1="{ids[0]}" v2="{ids[1]}" v3="{ids[2]}"{paint}/>')
        return ("<mesh><vertices>" + "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in points)
                + "</vertices><triangles>" + "".join(rows) + "</triangles></mesh>")

    top = [' pid="5" p1="1"' if (triangle[:, 2] > 0.4).all() else "" for triangle in plate]
    model = f"""<?xml version="1.0" encoding="UTF-8"?>
<model unit="centimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
       xmlns:m="http://schemas.microsoft.com/3dmanufacturing/material/2015/02">
 <resources>
  <m:pbmetallicdisplayproperties id="4"><m:pbmetallic name="steel" metallicness="1" roughness="0.3"/>
   <m:pbmetallic name="default" metallicness="0" roughness="1"/></m:pbmetallicdisplayproperties>
  <basematerials id="1" m:displaypropertiesid="4"><base name="steel" displaycolor="#8899AA"/><base name="default" displaycolor="#FAFAFB"/></basematerials>
  <m:colorgroup id="5"><m:color color="#20A040FF"/><m:color color="#E03020"/></m:colorgroup>
  <object id="2" type="model" name="plate" pid="1" pindex="1">{mesh(plate, top)}</object>
  <object id="3" type="model" name="peg" pid="1" pindex="0">{mesh(peg, [''] * len(peg))}</object>
  <object id="6" type="model" name="pegs"><components>
   <component objectid="3" transform="1 0 0 0 1 0 0 0 1 -2 0 0.5"/><component objectid="3" transform="1 0 0 0 1 0 0 0 1 2 0 0.5"/>
  </components></object>
 </resources>
 <build><item objectid="2"/><item objectid="6" transform="1 0 0 0 1 0 0 0 1 0 0.5 0"/></build>
</model>"""
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        package.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                         '<Relationship Target="/3D/3dmodel.model" Id="r0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
        package.writestr("3D/3dmodel.model", model)
    return path


def write_glb(path: Path) -> Path:
    """A node tree (``rig``, turned 45 degrees about glTF's z, holding ``box`` and ``plate``), a cube
    with a base colour texture (a checker with a yellow band along its top rows), a red plate."""
    from PIL import Image

    def cube(size: float) -> tuple:
        h = size / 2
        quads = [((1, 0, 0), [(h, -h, -h), (h, h, -h), (h, h, h), (h, -h, h)]),
                 ((-1, 0, 0), [(-h, h, -h), (-h, -h, -h), (-h, -h, h), (-h, h, h)]),
                 ((0, 1, 0), [(h, h, -h), (-h, h, -h), (-h, h, h), (h, h, h)]),
                 ((0, -1, 0), [(-h, -h, -h), (h, -h, -h), (h, -h, h), (-h, -h, h)]),
                 ((0, 0, 1), [(-h, -h, h), (h, -h, h), (h, h, h), (-h, h, h)]),
                 ((0, 0, -1), [(-h, h, -h), (h, h, -h), (h, -h, -h), (-h, -h, -h)])]
        position, normal, uv, index = [], [], [], []
        for direction, quad in quads:
            base = len(position)
            position += quad
            normal += [direction] * 4
            uv += [(0, 1), (1, 1), (1, 0), (0, 0)]
            index += [base, base + 1, base + 2, base, base + 2, base + 3]
        return (np.array(position, "f4"), np.array(normal, "f4"), np.array(uv, "f4"), np.array(index, "u2"))

    plate = cube(1.0)
    plate = (plate[0] * np.array([0.12, 0.08, 0.01], "f4"),) + plate[1:]
    texture = np.zeros((64, 64, 3), np.uint8)
    ys, xs = np.mgrid[0:64, 0:64]
    texture[((xs // 16 + ys // 16) % 2).astype(bool)] = (230, 90, 40)
    texture[~((xs // 16 + ys // 16) % 2).astype(bool)] = (40, 110, 220)
    texture[:8] = (250, 220, 60)
    picture = io.BytesIO()
    Image.fromarray(texture).save(picture, "PNG")
    blob, views, accessors = bytearray(), [], []

    def view(data: bytes, target: int | None = None) -> int:
        while len(blob) % 4:
            blob.append(0)
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(data), **({"target": target} if target else {})})
        blob.extend(data)
        return len(views) - 1

    def accessor(array: np.ndarray, kind: str, component: int, target: int) -> int:
        found = {"bufferView": view(array.tobytes(), target), "componentType": component, "count": len(array), "type": kind}
        if kind == "VEC3":
            found.update(min=array.min(0).tolist(), max=array.max(0).tolist())
        accessors.append(found)
        return len(accessors) - 1

    meshes = []
    for name, (position, normal, uv, index), material in (("box", cube(0.04), 0), ("plate", plate, 1)):
        attributes = {"POSITION": accessor(position, "VEC3", 5126, 34962), "NORMAL": accessor(normal, "VEC3", 5126, 34962),
                      "TEXCOORD_0": accessor(uv, "VEC2", 5126, 34962)}
        meshes.append({"name": name, "primitives": [{"attributes": attributes, "material": material,
                                                     "indices": accessor(index, "SCALAR", 5123, 34963)}]})
    image = view(picture.getvalue())
    gltf = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
            "nodes": [{"name": "rig", "children": [1, 2], "rotation": [0, 0, 0.3826834, 0.9238795]},
                      {"name": "box", "mesh": 0, "translation": [0, 0, 0.025]},
                      {"name": "plate", "mesh": 1, "translation": [0, 0, -0.005]}],
            "meshes": meshes, "accessors": accessors, "bufferViews": views,
            "materials": [{"name": "checker", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}, "metallicFactor": 0,
                                                                      "roughnessFactor": 0.6}},
                          {"name": "red", "pbrMetallicRoughness": {"baseColorFactor": [0.8, 0.1, 0.1, 1], "metallicFactor": 0,
                                                                  "roughnessFactor": 0.5},
                           "extensions": {"KHR_materials_clearcoat": {"clearcoatFactor": 1, "clearcoatRoughnessFactor": 0.1}}}],
            "textures": [{"source": 0}], "images": [{"bufferView": image, "mimeType": "image/png"}],
            "buffers": [{"byteLength": 0}]}
    while len(blob) % 4:
        blob.append(0)
    gltf["buffers"][0]["byteLength"] = len(blob)
    text = json.dumps(gltf).encode()
    text += b" " * (-len(text) % 4)
    body = struct.pack("<II", len(text), 0x4E4F534A) + text + struct.pack("<II", len(blob), 0x004E4942) + bytes(blob)
    path.write_bytes(struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body)
    return path


class FixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.folder = Path(tempfile.mkdtemp(prefix="engine-formats-")).resolve()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.folder, ignore_errors=True)


# --- buffers, STL, 3MF -------------------------------------------------------------------------


class BufferTest(unittest.TestCase):
    def test_a_box_s_edges_are_its_twelve_folds_and_a_cylinder_has_no_seam(self) -> None:
        triangles = box_soup((2, 3, 4))
        segments, kinds = feature_edges(triangles.reshape(-1, 3), np.arange(36).reshape(-1, 3))
        self.assertEqual((len(segments), set(kinds.tolist())), (12, {1}))
        cylinder = cylinder_soup(5, 10)
        segments, _kinds = feature_edges(cylinder.reshape(-1, 3), np.arange(len(cylinder) * 3).reshape(-1, 3))
        self.assertEqual(len(segments), 48, "two rims of 24 segments: 15-degree facets fold under 16")
        open_fan = cylinder[3::4][:6]  # six bottom triangles: a fan with an open rim
        segments, kinds = feature_edges(open_fan.reshape(-1, 3), np.arange(18).reshape(-1, 3))
        self.assertEqual(sorted(set(kinds.tolist())), [5], "an open fan's rim and its two sides are boundaries")

    def test_no_normal_drawn_is_of_no_length(self) -> None:
        """A triangle of no area is not drawn, and a given normal of no length is its vertex's
        triangles' own: one zero normal is enough for OCCT to draw none of the face it is in."""
        soup = np.concatenate([box_soup((2, 3, 4)), np.array([[[0, 0, 0], [1, 1, 1], [2, 2, 2]]], float)])
        mesh, _uv = buffer(soup.reshape(-1, 3), np.arange(len(soup) * 3).reshape(-1, 3))
        self.assertEqual(mesh.triangles, 12)
        self.assertGreater(float(np.linalg.norm(mesh.normals, axis=1).min()), 0.99)
        flat = box_soup((2, 3, 4)).reshape(-1, 3)
        given = np.tile([0.0, 0.0, 1.0], (len(flat), 1))
        given[5] = 0.0
        mesh, _uv = buffer(flat, np.arange(len(flat)).reshape(-1, 3), normals=given)
        self.assertGreater(float(np.linalg.norm(mesh.normals, axis=1).min()), 0.99)

    def test_normals_crease_at_thirty_degrees_and_a_dense_mesh_is_flat(self) -> None:
        cylinder = cylinder_soup(5, 10, segments=24)
        mesh, _uv = buffer(cylinder.reshape(-1, 3), np.arange(len(cylinder) * 3).reshape(-1, 3))
        side = mesh.normals.reshape(-1, 3, 3)[0::4]  # each side quad's first triangle
        self.assertTrue(np.allclose(side[:, :, 2], 0, atol=1e-6), "the side's normals stay level")
        self.assertGreater(np.abs(side[:, 0] - side[:, 1]).max(), 0.1, "smoothed round the side, not flat")
        caps = mesh.normals.reshape(-1, 3, 3)[2::4]
        self.assertTrue(np.allclose(caps[:, :, 2], 1, atol=1e-6), "the cap's corners keep the cap's normal")
        dense = np.concatenate([box_soup((1, 1, 1), (2 * at, 0, 0)) for at in range(900)])  # 10,800 triangles
        mesh, _uv = buffer(dense.reshape(-1, 3), np.arange(len(dense) * 3).reshape(-1, 3))
        flat = mesh.normals.reshape(-1, 3, 3)
        self.assertTrue(np.allclose(flat[:, 0], flat[:, 1]) and np.allclose(flat[:, 0], flat[:, 2]))


class MeshFileTest(FixtureTest):
    def test_an_stl_reads_alike_binary_or_ascii_as_one_part_in_the_fill(self) -> None:
        triangles = np.concatenate([box_soup((20, 10, 4)), cylinder_soup(3, 12, at=(0, 0, 2))])
        binary = formats.read(write_stl(self.folder / "part.stl", triangles))
        text = formats.read(write_stl(self.folder / "text.stl", triangles, ascii_=True))
        self.assertEqual(binary.structure["kind"], "stl")
        self.assertEqual(binary.buffers.keys().__len__(), 1)
        self.assertEqual([p["color"] for p in binary.structure["prototypes"]], [None])
        from cadgen.engine.mesh import Mesh

        first, second = (Mesh.from_bytes(next(iter(found.buffers.values()))) for found in (binary, text))
        np.testing.assert_allclose(first.positions, second.positions, atol=1e-5)  # a binary STL's floats are 32-bit
        self.assertEqual((first.triangles, len(first.edges)), (second.triangles, len(second.edges)))
        self.assertEqual(binary.structure["tree"], {"name": "part", "prototype": 0, "instance": 0})
        (self.folder / "empty.stl").write_bytes(b"\0" * 80 + struct.pack("<I", 0))
        with self.assertRaisesRegex(formats.FormatError, "no triangles"):
            formats.read(self.folder / "empty.stl")

    def test_a_3mf_places_its_objects_in_millimetres_with_their_colours(self) -> None:
        found = formats.read(write_3mf(self.folder / "kit.3mf"))
        structure = found.structure
        self.assertEqual([p["name"] for p in structure["prototypes"]], ["plate", "peg"])
        plate, peg = structure["prototypes"]
        self.assertIsNone(plate["color"], "the generated default base material is no colour")
        linear = [round(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4, 6) for c in (0xE0 / 255, 0x30 / 255, 0x20 / 255)]
        self.assertEqual(list(plate["faceColors"].values()), [[*linear, 1.0]], "the top's colour group, linear")
        self.assertEqual(peg["material"]["metallic"], 1, "the steel base material's display properties")
        self.assertAlmostEqual(peg["color"][0], 0.246201, places=5)
        matrices = [np.array(instance["matrix"]).reshape(4, 4).T for instance in structure["instances"]]
        self.assertEqual([tuple(m[:3, 3]) for m in matrices], [(0, 0, 0), (-20, 5, 5), (20, 5, 5)], "centimetres")
        self.assertEqual([instance["palette"] for instance in structure["instances"]], [0, 1, 2])
        self.assertEqual(structure["tree"]["children"][1]["name"], "Object 2")
        from cadgen.engine.mesh import Mesh

        mesh = Mesh.from_bytes(found.buffers[plate["key"]])
        self.assertAlmostEqual(float(np.ptp(mesh.positions[:, 0])), 60.0, places=4)


# --- GLB -------------------------------------------------------------------------------------------


class GlbTest(FixtureTest):
    def test_a_glb_is_read_by_occt_with_its_tree_materials_and_texture(self) -> None:
        found = formats.read(write_glb(self.folder / "rig.glb"))
        structure = found.structure
        self.assertEqual(structure["tree"]["name"], "rig")
        self.assertEqual([child["name"] for child in structure["tree"]["children"]], ["box", "plate"])
        box, plate = structure["prototypes"]
        checker = next(iter(box["faceMaterials"].values()))
        red = next(iter(plate["faceMaterials"].values()))
        self.assertEqual((checker["name"], checker["roughness"]), ("checker", 0.6))
        self.assertEqual((red["name"], red["color"][:3], red["coat"], red["coatRoughness"]), ("red", [0.8, 0.1, 0.1], 1.0, 0.1))
        textured = found.shapes[box["key"]]
        self.assertEqual(len(textured.textures), 1, "the checker's base colour texture, by its paint")
        self.assertNotIn(plate["key"], found.shapes, "an untextured part is made as a document's is")
        # glTF's y up and metres as z up and millimetres: the box 40 mm, its centre 25 mm along glTF z (-y).
        matrix = np.array(structure["instances"][0]["matrix"]).reshape(4, 4).T
        self.assertTrue(np.allclose(matrix[:3, :3] @ matrix[:3, :3].T, np.eye(3), atol=1e-9), "a rigid placement")
        from cadgen.engine.mesh import Mesh

        positions = Mesh.from_bytes(found.buffers[box["key"]]).positions @ matrix[:3, :3].T + matrix[:3, 3]
        np.testing.assert_allclose(np.ptp(positions, 0).max(), 40 * math.sqrt(2), atol=1e-3)
        np.testing.assert_allclose(positions.mean(0), (0, -25, 0), atol=1e-3)


# --- robots ----------------------------------------------------------------------------------------


class RobotTest(FixtureTest):
    def test_the_forward_kinematics_are_the_viewer_s(self) -> None:
        from cadgen.engine.formats.robot import read_urdf

        path = self.folder / "kinematics.urdf"
        path.write_text(KINEMATICS, encoding="utf-8")
        robot = read_urdf(path)
        for row in json.loads((GOLDEN / "kinematics.json").read_text(encoding="utf-8")):
            values, _named = robot.values(row["values"])
            placed = robot.placements(values)
            for link, matrix in row["links"].items():
                with self.subTest(pose=row["values"], link=link):
                    mine = placed[link].copy()
                    mine[:3, 3] /= 1000
                    np.testing.assert_allclose(mine, np.array(matrix).reshape(4, 4), atol=1e-9)

    def test_a_pose_is_refused_by_name_and_an_srdf_opens_at_home(self) -> None:
        (self.folder / "kinematics.urdf").write_text(KINEMATICS, encoding="utf-8")
        (self.folder / "kinematics.srdf").write_text(SRDF, encoding="utf-8")
        found = formats.read(self.folder / "kinematics.urdf")
        for pose, why in (({"elbow": 3}, "no joint 'elbow'"), ({"mirror": 3}, "'mirror' follows 'lift'"),
                          ({"input_joint": 3}, "follows its driver 'output_joint'"), ({"lift": 200}, "past its limits"),
                          ({"foot": 1}, "is fixed"), ("home", "no group state")):
            with self.subTest(pose=pose), self.assertRaisesRegex(ValueError, why):
                found.pose(found.structure, pose)
        srdf = formats.read(self.folder / "kinematics.srdf")
        posed = srdf.pose(srdf.structure, "tucked")
        self.assertEqual(posed["kinematics"]["pose"]["configuration"], "arm/tucked")
        self.assertAlmostEqual(posed["kinematics"]["pose"]["values"]["spin"], math.degrees(0.5), msg="home under the state")
        self.assertAlmostEqual(posed["kinematics"]["pose"]["values"]["lift"], math.degrees(-1.0))
        self.assertNotEqual(srdf.structure["instances"][1]["matrix"], found.structure["instances"][1]["matrix"],
                            "the SRDF opens at its home state")

    def test_an_sdf_places_its_links_by_their_frames(self) -> None:
        path = self.folder / "hinge.sdf"
        path.write_text(SDF, encoding="utf-8")
        found = formats.read(path)
        door = np.array(found.structure["instances"][1]["matrix"]).reshape(4, 4).T
        np.testing.assert_allclose(door[:3, 3], (1000, 200, 500), atol=1e-6)  # its visual 0.2 m along the door's turned x
        self.assertAlmostEqual(found.structure["prototypes"][1]["name"], "box")
        swung = found.pose(found.structure, {"swing": 90})
        door = np.array(swung["instances"][1]["matrix"]).reshape(4, 4).T
        np.testing.assert_allclose(door[:3, 3], (800, 0, 500), atol=1e-6)  # turned about z through the joint: the door's frame


# --- pictures --------------------------------------------------------------------------------------


class PictureTest(FixtureTest):
    """Each kind drawn as its golden, and what a kind does not offer refused by name."""

    @classmethod
    def setUpClass(cls) -> None:
        gl()
        super().setUpClass()
        folder = cls.folder
        write_stl(folder / "part.stl", np.concatenate([box_soup((20, 10, 4)), cylinder_soup(3, 12, at=(0, 0, 2))]))
        write_3mf(folder / "kit.3mf")
        write_glb(folder / "rig.glb")
        (folder / "kinematics.urdf").write_text(KINEMATICS, encoding="utf-8")
        cls.rows = {
            "stl": (folder / "part.stl", {}),
            "stl-wireframe": (folder / "part.stl", {"display": {"mode": "wireframe"}}),
            "3mf": (folder / "kit.3mf", {}),
            "3mf-parts": (folder / "kit.3mf", {"display": {"mode": "parts", "grid": True}}),
            "glb": (folder / "rig.glb", {}),
            "urdf": (folder / "kinematics.urdf", {"pose": {"spin": 75, "lift": 40, "reach": 0.03, "output_joint": 20}}),
        }

    def draw(self, path: Path, **fields) -> tuple[bytes, dict]:
        from cadgen.engine.render.service import holder

        held = holder(path)
        try:
            held.open()
            return held.draw(Still(**{"size": SIZE, **fields}))
        finally:
            held.close()

    def test_each_kind_draws_as_its_golden(self) -> None:
        GOLDEN.mkdir(exist_ok=True) if WRITE else None
        for name, (path, fields) in self.rows.items():
            with self.subTest(row=name):
                data, report = self.draw(path, **fields)
                self.assertEqual((report["renderer"], report["width"], report["height"]), ("occt", *SIZE))
                golden = GOLDEN / f"{name}.png"
                if WRITE:
                    golden.write_bytes(data)
                    continue
                share = mismatch(pixels(data), pixels(golden.read_bytes()))
                self.assertLessEqual(share, 0.015, f"{name}: {share:.1%} of its pixels are not the golden's")
        if not WRITE:
            self.assertEqual(sorted(path.stem for path in GOLDEN.glob("*.png")), sorted(self.rows))

    def test_the_glb_texture_is_drawn_in_the_lit_looks_alone(self) -> None:
        """The checker's texture in Solid; in Parts the cube one flat palette colour, no checker
        under it (the texture is not modulated by the fill)."""
        textured = pixels(self.draw(self.folder / "rig.glb")[0])
        yellow = (np.abs(textured - np.array([250, 220, 60])).max(-1) < 70).sum()
        self.assertGreater(yellow, 50, "the texture's yellow band")
        parts = pixels(self.draw(self.folder / "rig.glb", display={"mode": "parts"})[0])
        # The checker is orange and blue: in Parts, no pixel is far bluer than it is red.
        blue = ((parts[..., 2] - parts[..., 0]) > 90).sum()
        self.assertLess(blue, 5, "Parts paints over the texture")

    def test_what_a_kind_does_not_offer_is_refused_by_name_and_out_is_kept(self) -> None:
        out = self.folder / "kept.png"
        out.write_bytes(b"kept")
        for path, options, why in (
                (self.folder / "part.stl", {"pose": {"a": 1}}, "is a mesh and has none"),
                (self.folder / "part.stl", {"display": {"mode": "parts"}}, "places 1 part"),
                (self.folder / "kinematics.urdf", {"pose": {"lift": 200}}, "--pose: joint 'lift' at 200 is past its limits")):
            with self.subTest(path=path.name, options=options), self.assertRaisesRegex(SnapshotRefused, why):
                snapshot(path, out, size=SIZE, daemon=False, **options)
        self.assertEqual(out.read_bytes(), b"kept")


if __name__ == "__main__":
    unittest.main()
