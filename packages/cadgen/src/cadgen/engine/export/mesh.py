"""The mesh exports' common ground: each placed prototype's tessellation, from the one cache.

A part is meshed once per geometry key, wherever it is placed, from its shape bytes
(``Source.shape_data``) at the engine's one tolerance (``engine.mesh``), and held in the
process's mesh cache -- the one the viewer serves from (``serve.MESHES``): a worker's
patch, its exports and a snapshot it draws all take a key's buffer from there, so a part
the build meshed for the viewer is not meshed again for a GLB.

A part's look, per triangle group: its painted faces' colours and materials, and its own
for the rest (``groups``).
"""

from __future__ import annotations

from typing import Any

import numpy as np

__all__ = ["groups", "look_of", "meshes", "placed_points"]

DEFAULT_COLOR = (0.8, 0.8, 0.8, 1.0)


def meshes(view: Any) -> dict[int, Any]:
    """Each prototype the view places, by its index: its ``engine.mesh.Mesh``."""
    from cadgen.engine.mesh import Mesh, mesh_shape
    from cadgen.engine.serve import MESHES

    found: dict[int, Any] = {}
    for index in view.used():
        key = view.prototypes[index]["key"]
        data = MESHES.get(key)
        if data is None:
            made = mesh_shape(view.source.shape_data(key), parallel=True)
            data = made.to_bytes()
            MESHES.put(key, data, made.unmeshed)
        found[index] = Mesh.from_bytes(data)
    return found


def placed_points(points: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    return points @ matrix[:3, :3].T.astype(np.float32) + matrix[:3, 3].astype(np.float32)


def _material(value: Any) -> dict[str, Any] | None:
    return dict(value) if value else None


def look_of(color: Any, material: Any) -> tuple:
    """A hashable look: the base colour (RGBA) and the material -- its structure's JSON but its
    colour, as ``(key, value)`` pairs, lists as tuples (``dict(held)`` reads it) -- or None."""
    rgba = tuple(float(value) for value in (color if color is not None else DEFAULT_COLOR))
    if len(rgba) == 3:
        rgba = (*rgba, 1.0)
    held = None
    if material:
        held = tuple((key, tuple(value) if isinstance(value, list) else value)
                     for key, value in material.items() if key != "color")
    return rgba, held


def groups(mesh: Any, prototype: dict[str, Any], color: Any = None) -> list[tuple[tuple, np.ndarray]]:
    """A part's triangles by look: its painted faces' colours and materials, its own (or
    ``color``, a placement's own colour) for the rest. A face given a material is drawn in
    the material's colour."""
    base = color if color is not None else prototype.get("color")
    material = _material(prototype.get("material"))
    if material is not None and color is None:
        base = base if base is not None else material.get("color")
    face_colors = prototype.get("faceColors") or {}
    face_materials = prototype.get("faceMaterials") or {}
    own = look_of(base, material)
    if not face_colors and not face_materials:
        return [(own, mesh.indices)]
    by_look: dict[tuple, list] = {}
    for ordinal, first, count in mesh.faces:
        text = str(int(ordinal))
        given = face_materials.get(text)
        if given:
            look = look_of(face_colors.get(text) or given.get("color") or base, given)
        elif text in face_colors:
            look = look_of(face_colors[text], None)
        else:
            look = own
        by_look.setdefault(look, []).append(mesh.indices[first:first + count])
    return [(look, np.concatenate(chunks)) for look, chunks in by_look.items()]
