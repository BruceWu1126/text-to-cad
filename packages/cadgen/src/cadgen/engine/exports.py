"""Exports: the files a model declares beside its xbf, each a translation of the document.

STEP is the XCAF document translated by the kernel's writer, names, colours and the
element names on face sub-shapes carried (``write.stepcaf.subshapes.name``). The mesh
formats are written from the engine's own tessellation (``mesh``): each part meshed
once, wherever it is placed, and kept in this process's memory by its geometry key,
so a later export meshes only the parts that changed. Binary STL is every placed
triangle; GLB is a glTF 2.0 binary with one mesh per part, a node per placement, the
assembly's names on its nodes and a material per colour; 3MF is a zip with one XML
model, an object per part and a build item per placement. Every file is written
beside its target and moved over it.
"""

from __future__ import annotations

import json
import os
import struct
import threading
import zipfile
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterator

import numpy as np

__all__ = ["placements", "write", "write_from_loaded"]

_MESHES: "OrderedDict[str, Any]" = OrderedDict()
_MESH_BYTES = [0]
_MESH_CAP = 1 << 30
_LOCK = threading.Lock()


def _temporary(path: Path) -> Path:
    return path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp{path.suffix}")


def _replace(temporary: Path, path: Path) -> int:
    size = temporary.stat().st_size
    os.replace(temporary, path)
    return size


def write(kind: str, doc: Any, tree: Any, path: Path) -> int:
    """Write one export of a built document; returns its size."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "step":
        return write_step(doc, path)
    parts = [(part.key, part.shape, part.name, part.color, part.face_colors) for part in tree.parts]
    index = {id(part): at for at, part in enumerate(tree.parts)}
    placed = [(index[id(part)], matrix, path_names, color) for part, matrix, path_names, color in placements(tree)]
    return _write_mesh(kind, parts, placed, path, tree.root.name)


def write_from_loaded(kind: str, loaded: Any, path: Path) -> int:
    """Write one export of a document read from a file (``cadgen xbf export``)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "step":
        return write_step(loaded.doc, path)
    parts = [(p.key, p.shape, p.name, p.color, p.face_colors) for p in loaded.prototypes]
    placed = []
    for instance in loaded.instances:
        matrix = np.array(instance.matrix, dtype=float).reshape(4, 4).T
        placed.append((instance.prototype, matrix, instance.path, instance.color))
    return _write_mesh(kind, parts, placed, path, loaded.name)


def write_step(doc: Any, path: Path) -> int:
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Interface import Interface_Static
    from OCP.Message import Message
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs

    from cadgen.engine.document import _XCAF_LOCK

    temporary = _temporary(path)
    with _XCAF_LOCK:
        _quiet(Message)
        # The writer defines the translator's parameters: set them once it exists.
        writer = STEPCAFControl_Writer()
        Interface_Static.SetCVal_s("write.step.unit", "MM")
        Interface_Static.SetIVal_s("write.stepcaf.subshapes.name", 1)
        Interface_Static.SetCVal_s("write.step.schema", "AP214IS")
        writer.SetNameMode(True)
        writer.SetColorMode(True)
        writer.SetLayerMode(False)
        if not writer.Transfer(doc, STEPControl_AsIs) or writer.Write(str(temporary)) != IFSelect_RetDone:
            temporary.unlink(missing_ok=True)
            raise OSError(f"could not write {path}")
    return _replace(temporary, path)


def _quiet(message: Any) -> None:
    """The kernel's translators report to its default messenger, which prints: no printer."""
    if getattr(_quiet, "done", False):
        return
    messenger = message.DefaultMessenger_s()
    printers = messenger.Printers()
    while printers.Size():
        messenger.RemovePrinter(printers.First())
        printers = messenger.Printers()
    _quiet.done = True  # type: ignore[attr-defined]


def placements(tree: Any) -> Iterator[tuple[Any, np.ndarray, list[str], tuple | None]]:
    """Every placed part of a tree: the part, its world matrix (4x4), the names from the
    root down to it, and its instance's colour."""
    from cadgen.engine.document import Assembly, Part

    def matrix_of(location: Any) -> np.ndarray:
        value = location.Transformation().Value
        found = np.eye(4)
        for row in range(3):
            for column in range(4):
                found[row, column] = value(row + 1, column + 1)
        return found

    stack = [(tree.root, np.eye(4), [tree.root.name], None)]
    while stack:
        prototype, matrix, names, color = stack.pop()
        if isinstance(prototype, Part):
            yield prototype, matrix, names, color or prototype.color
            continue
        assert isinstance(prototype, Assembly)
        for component in reversed(prototype.components):
            stack.append((component.prototype, matrix @ matrix_of(component.location), names + [component.name],
                          component.color or color))


def mesh_of(key: str, shape: Any) -> Any:
    """A part's tessellation, held in memory by its geometry key."""
    from cadgen.engine.mesh import mesh_shape

    with _LOCK:
        found = _MESHES.get(key) if key else None
        if found is not None:
            _MESHES.move_to_end(key)
            return found
    found = mesh_shape(shape)
    if key:
        size = sum(array.nbytes for array in (found.positions, found.normals, found.indices, found.edge_points))
        with _LOCK:
            _MESHES[key] = found
            _MESH_BYTES[0] += size
            while _MESH_BYTES[0] > _MESH_CAP and len(_MESHES) > 1:
                _old, dropped = _MESHES.popitem(last=False)
                _MESH_BYTES[0] -= sum(a.nbytes for a in (dropped.positions, dropped.normals, dropped.indices,
                                                          dropped.edge_points))
    return found


def _write_mesh(kind: str, parts: list, placed: list, path: Path, name: str) -> int:
    meshes = [mesh_of(key, shape) for key, shape, _name, _color, _faces in parts]
    temporary = _temporary(path)
    try:
        if kind == "stl":
            _stl(meshes, placed, temporary, name)
        elif kind == "glb":
            _glb(meshes, parts, placed, temporary, name)
        elif kind == "threemf":
            _threemf(meshes, parts, placed, temporary, name)
        else:
            raise ValueError(f"no export {kind}")
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return _replace(temporary, path)


def _placed_points(points: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    return points @ matrix[:3, :3].T.astype(np.float32) + matrix[:3, 3].astype(np.float32)


def _stl(meshes: list, placed: list, path: Path, name: str) -> None:
    record = np.dtype([("normal", "<f4", 3), ("corners", "<f4", (3, 3)), ("attribute", "<u2")])
    total = sum(meshes[index].triangles for index, *_rest in placed)
    with open(path, "wb") as handle:
        handle.write(name.encode("ascii", "replace")[:80].ljust(80, b" "))
        handle.write(struct.pack("<I", total))
        for index, matrix, _names, _color in placed:
            mesh = meshes[index]
            if not mesh.triangles:
                continue
            points = _placed_points(mesh.positions, matrix)
            corners = points[mesh.indices.reshape(-1, 3)]
            normal = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
            lengths = np.linalg.norm(normal, axis=1, keepdims=True)
            rows = np.zeros(len(corners), dtype=record)
            rows["normal"] = normal / np.where(lengths > 0, lengths, 1)
            rows["corners"] = corners
            handle.write(rows.tobytes())


def _groups(mesh: Any, part_color: tuple | None, face_colors: dict) -> list[tuple[tuple | None, np.ndarray]]:
    """A part's triangles by colour: its painted faces' colours, and its own for the rest."""
    if not face_colors:
        return [(part_color, mesh.indices)]
    by_color: dict[Any, list] = {}
    for ordinal, first, count in mesh.faces:
        color = face_colors.get(int(ordinal), part_color)
        by_color.setdefault(color, []).append(mesh.indices[first:first + count])
    return [(color, np.concatenate(chunks)) for color, chunks in by_color.items()]


def _glb(meshes: list, parts: list, placed: list, path: Path, name: str) -> None:
    blob = bytearray()
    views: list[dict] = []
    accessors: list[dict] = []

    def add(array: np.ndarray, target: int, kind: str, component: int) -> int:
        while len(blob) % 4:
            blob.append(0)
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": array.nbytes, "target": target})
        blob.extend(array.tobytes())
        accessor: dict[str, Any] = {"bufferView": len(views) - 1, "componentType": component,
                                    "count": int(array.shape[0]), "type": kind}
        if kind == "VEC3":
            accessor["min"] = array.min(axis=0).tolist() if len(array) else [0, 0, 0]
            accessor["max"] = array.max(axis=0).tolist() if len(array) else [0, 0, 0]
        accessors.append(accessor)
        return len(accessors) - 1

    materials: list[dict] = []
    material_of: dict[Any, int] = {}

    def material(color: tuple | None) -> int:
        key = tuple(color) if color is not None else None
        found = material_of.get(key)
        if found is None:
            rgba = list(color) if color is not None else [0.8, 0.8, 0.8, 1.0]
            entry: dict[str, Any] = {"pbrMetallicRoughness": {"baseColorFactor": rgba, "metallicFactor": 0.0,
                                                               "roughnessFactor": 0.6}, "doubleSided": False}
            if rgba[3] < 1.0:
                entry["alphaMode"] = "BLEND"
            materials.append(entry)
            found = material_of[key] = len(materials) - 1
        return found

    gltf_meshes = []
    for mesh, (_key, _shape, part_name, color, face_colors) in zip(meshes, parts):
        primitives = []
        if mesh.triangles:
            positions = add(np.ascontiguousarray(mesh.positions, "<f4"), 34962, "VEC3", 5126)
            normals = add(np.ascontiguousarray(mesh.normals, "<f4"), 34962, "VEC3", 5126)
            for group_color, indices in _groups(mesh, color, face_colors):
                primitives.append({"attributes": {"POSITION": positions, "NORMAL": normals},
                                   "indices": add(np.ascontiguousarray(indices, "<u4"), 34963, "SCALAR", 5125),
                                   "material": material(group_color)})
        gltf_meshes.append({"name": part_name, "primitives": primitives})
    nodes = [{"name": name, "children": []}]
    for index, matrix, names, _color in placed:
        if not gltf_meshes[index]["primitives"]:
            continue
        nodes.append({"name": "/".join(names[1:]) or names[-1], "mesh": index,
                      "matrix": [float(value) for value in matrix.T.ravel()]})
        nodes[0]["children"].append(len(nodes) - 1)
    document = {"asset": {"version": "2.0", "generator": "cadgen engine"}, "scene": 0,
                "scenes": [{"name": name, "nodes": [0]}], "nodes": nodes, "meshes": gltf_meshes,
                "materials": materials, "accessors": accessors, "bufferViews": views,
                "buffers": [{"byteLength": len(blob)}]}
    text = json.dumps(document, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    while len(blob) % 4:
        blob.append(0)
    with open(path, "wb") as handle:
        handle.write(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(text) + 8 + len(blob)))
        handle.write(struct.pack("<II", len(text), 0x4E4F534A))
        handle.write(text)
        handle.write(struct.pack("<II", len(blob), 0x004E4942))
        handle.write(bytes(blob))


def _hex(color: tuple | None) -> str:
    rgba = color if color is not None else (0.8, 0.8, 0.8, 1.0)

    def srgb(value: float) -> int:
        value = min(max(float(value), 0.0), 1.0)
        value = value * 12.92 if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055
        return round(value * 255)

    return "#%02X%02X%02X%02X" % (srgb(rgba[0]), srgb(rgba[1]), srgb(rgba[2]), round(float(rgba[3]) * 255))


def _threemf(meshes: list, parts: list, placed: list, path: Path, name: str) -> None:
    from xml.sax.saxutils import quoteattr

    colors: list[str] = []
    color_index: dict[str, int] = {}
    objects = []
    for at, (mesh, (_key, _shape, part_name, color, _faces)) in enumerate(zip(meshes, parts), start=1):
        hexed = _hex(color)
        if hexed not in color_index:
            color_index[hexed] = len(colors)
            colors.append(hexed)
        vertices = "".join(f'<vertex x="{x:.6g}" y="{y:.6g}" z="{z:.6g}"/>' for x, y, z in mesh.positions.tolist())
        triangles = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in mesh.indices.reshape(-1, 3).tolist())
        objects.append(f'<object id="{at + 1}" type="model" name={quoteattr(part_name)} pid="1" '
                       f'pindex="{color_index[hexed]}"><mesh><vertices>{vertices}</vertices>'
                       f'<triangles>{triangles}</triangles></mesh></object>')
    items = []
    for index, matrix, names, _color in placed:
        if not meshes[index].triangles:
            continue
        m = matrix
        transform = " ".join(f"{value:.9g}" for value in (m[0, 0], m[1, 0], m[2, 0], m[0, 1], m[1, 1], m[2, 1],
                                                          m[0, 2], m[1, 2], m[2, 2], m[0, 3], m[1, 3], m[2, 3]))
        items.append(f'<item objectid="{index + 2}" transform="{transform}"/>')
    materials = "".join(f'<base name="color{at}" displaycolor="{hexed}"/>' for at, hexed in enumerate(colors))
    model = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<model unit="millimeter" xml:lang="en-US" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             f'<metadata name="Title">{_escape(name)}</metadata>'
             f'<resources><basematerials id="1">{materials}</basematerials>{"".join(objects)}</resources>'
             f'<build>{"".join(items)}</build></model>')
    content_types = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
                     '</Types>')
    relationships = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                     '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                     '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
                     'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("3D/3dmodel.model", model)


def _escape(text: str) -> str:
    from xml.sax.saxutils import escape

    return escape(text)
