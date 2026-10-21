"""GLB: a glTF 2.0 binary of the document.

One mesh per prototype -- its positions and normals one accessor pair, its triangles a
primitive per look (a painted face's colour or material is a primitive of its own) -- and
a node per placement, named by the placement's path, under one root node named by the
document. A material per look: PBR metallic-roughness, its base colour the part's (or
the paint's), metallic, roughness and emissive from the part's material, ``BLEND`` where
the colour is not opaque; a part without a material is a matte one of its colour. A
placement whose own colour is not its part's is drawn by a second mesh of the same
accessors in that colour. The placements are in the document's millimetres, Z up; the
root node shows them in glTF's frame, Y up and metres, as cadgen's other GLBs do.
"""

from __future__ import annotations

import json
import math
import struct
from pathlib import Path
from typing import Any

import numpy as np

from cadgen.engine.export.mesh import groups, meshes

__all__ = ["write"]

# The glTF 2.0 container (the spec's constants): its magic, version and chunk types.
MAGIC = 0x46546C67
VERSION = 2
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942
# The document's Z-up millimetres as glTF's Y-up metres, on the root node: -90 degrees
# about X, then 0.001. The placements below it stay in the document's millimetres.
ROTATION = (-math.sqrt(0.5), 0.0, 0.0, math.sqrt(0.5))
SCALE = (0.001, 0.001, 0.001)


def write(view: Any, path: Path) -> None:
    held = meshes(view)
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
    material_at: dict[tuple, int] = {}

    def material(look: tuple) -> int:
        found = material_at.get(look)
        if found is None:
            rgba, given = look
            entry: dict[str, Any] = {"pbrMetallicRoughness": {
                "baseColorFactor": list(rgba),
                "metallicFactor": given[2] if given is not None else 0.0,
                "roughnessFactor": given[3] if given is not None else 0.6},
                "doubleSided": False, "alphaMode": "BLEND" if rgba[3] < 1.0 else "OPAQUE"}
            if given is not None:
                entry["name"] = given[0]
                if any(given[4]):
                    entry["emissiveFactor"] = list(given[4])
            materials.append(entry)
            found = material_at[look] = len(materials) - 1
        return found

    gltf_meshes: list[dict] = []
    shared: dict[int, tuple[int, int, dict]] = {}  # prototype -> positions, normals, indices accessor by triangle span
    mesh_at: dict[tuple, int] = {}

    def mesh_of(index: int, color: tuple | None) -> int | None:
        key = (index, color)
        found = mesh_at.get(key)
        if found is not None:
            return found
        mesh = held[index]
        if not mesh.triangles:
            return None
        prototype = view.prototypes[index]
        if index not in shared:
            shared[index] = (add(np.ascontiguousarray(mesh.positions, "<f4"), 34962, "VEC3", 5126),
                             add(np.ascontiguousarray(mesh.normals, "<f4"), 34962, "VEC3", 5126), {})
        positions, normals, spans = shared[index]
        primitives = []
        for look, indices in groups(mesh, prototype, color):
            span = indices.tobytes()
            accessor = spans.get(span)
            if accessor is None:
                accessor = spans[span] = add(np.ascontiguousarray(indices, "<u4"), 34963, "SCALAR", 5125)
            primitives.append({"attributes": {"POSITION": positions, "NORMAL": normals}, "indices": accessor,
                               "material": material(look)})
        gltf_meshes.append({"name": prototype["name"], "primitives": primitives})
        found = mesh_at[key] = len(gltf_meshes) - 1
        return found

    nodes: list[dict] = [{"name": view.name, "rotation": list(ROTATION), "scale": list(SCALE), "children": []}]
    for placed in view.placements:
        own = held[placed.prototype]
        color = placed.color if placed.color is not None and placed.color != _tuple(
            view.prototypes[placed.prototype].get("color")) else None
        index = mesh_of(placed.prototype, color) if own.triangles else None
        if index is None:
            continue
        nodes.append({"name": "/".join(placed.path[1:]) or placed.path[-1], "mesh": index,
                      "matrix": [float(value) for value in placed.matrix.T.ravel()]})
        nodes[0]["children"].append(len(nodes) - 1)
    document = {"asset": {"version": "2.0", "generator": "cadgen engine"}, "scene": 0,
                "scenes": [{"name": view.name, "nodes": [0]}], "nodes": nodes, "meshes": gltf_meshes,
                "materials": materials, "accessors": accessors, "bufferViews": views,
                "buffers": [{"byteLength": len(blob)}]}
    text = json.dumps(document, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    while len(blob) % 4:
        blob.append(0)
    with open(path, "wb") as handle:
        handle.write(struct.pack("<III", MAGIC, VERSION, 12 + 8 + len(text) + 8 + len(blob)))
        handle.write(struct.pack("<II", len(text), JSON_CHUNK))
        handle.write(text)
        handle.write(struct.pack("<II", len(blob), BIN_CHUNK))
        handle.write(bytes(blob))


def _tuple(value: Any) -> tuple | None:
    return tuple(value) if value is not None else None
