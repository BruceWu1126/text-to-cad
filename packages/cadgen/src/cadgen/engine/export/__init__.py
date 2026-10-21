"""Exports: a document translated to a file. One writer, one document, two triggers.

``translate(document, kind, path, *, pose=None, part=None, animate=None)`` writes one
file of ``kind`` (``KINDS``: ``xbf``, ``step``, ``glb``, ``stl``, ``threemf``) from a
document: a ``Source`` -- the document a build made, held in the worker's memory
(``Source.built``), or one read from an ``.xbf`` or a ``.step`` (``Source.read``). A
derived file a decorator declares and a file ``cadgen xbf export`` asks for are the same
call, so the same document and the same options write the same bytes.

``pose`` (a configuration's name or ``{joint: value}``) places the mechanism's links by
forward kinematics before anything is written; ``part`` (an occurrence's path or a node's
name in the document's tree) writes that subtree alone, as a document of its own: its
root at the subtree's frame. Meshes are never sources: there is no B-rep in them. An xbf
is written from a STEP (read into a document first), or from an xbf with ``part`` (a
sub-assembly extracted); a posed xbf is refused, since a pose is a request, never a
document. ``animate`` is reserved for GLB motion and takes nothing yet.

Every file is written to a temporary file beside its target and moved over it.

Importing this package imports no kernel: a kind's writer loads on its first use.
"""

from __future__ import annotations

import importlib
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

__all__ = ["KINDS", "ExportRefused", "Kind", "Source", "View", "kind_of", "translate"]


class ExportRefused(ValueError):
    """An export the engine will not write, said by name before anything is written."""


@dataclass(frozen=True)
class Kind:
    """One file kind: its name, the suffixes its files end in (the first is its own), the
    writer (``module:function``, loaded on first use: ``write(view, path)``), and whether
    the kind is written atomically here (an xbf saves itself atomically)."""

    name: str
    suffixes: tuple[str, ...]
    writer: str
    atomic: bool = True

    @property
    def suffix(self) -> str:
        return self.suffixes[0]

    def write(self, view: "View", path: Path) -> None:
        module, _sep, function = self.writer.partition(":")
        getattr(importlib.import_module(module), function)(view, path)


KINDS: dict[str, Kind] = {
    "xbf": Kind("xbf", (".xbf",), "cadgen.engine.export.tree:write_xbf", atomic=False),
    "step": Kind("step", (".step", ".stp"), "cadgen.engine.export.step:write"),
    "glb": Kind("glb", (".glb",), "cadgen.engine.export.glb:write"),
    "stl": Kind("stl", (".stl",), "cadgen.engine.export.stl:write"),
    "threemf": Kind("threemf", (".3mf",), "cadgen.engine.export.threemf:write"),
}
# What a source file is read as: documents only.
SOURCES = (".xbf", ".step", ".stp")
MESHES = (".glb", ".stl", ".3mf")


def kind_of(path: Any) -> str:
    """The kind a destination's suffix names; an unknown suffix is refused by name."""
    suffix = Path(path).suffix.lower()
    for kind in KINDS.values():
        if suffix in kind.suffixes:
            return kind.name
    known = ", ".join(suffix for kind in KINDS.values() for suffix in kind.suffixes)
    raise ExportRefused(f"{Path(path).name}: an export is one of {known}")


# --- a document to translate ---------------------------------------------------------------


class Source:
    """A document to translate: its structure (``document.Loaded.structure``'s shape: the
    prototypes with names, colours, materials and element names, the instances with world
    matrices, the tree, the mechanism as ``kinematics``), each part's shape by its key, the
    XCAF document when one is held, each assembly occurrence's placement, and the
    mechanism itself. ``live`` shapes are a build's (written and read once to be meshed,
    as saving their document does: ``mesh.shape_bytes``); the others were read from a file
    and are meshed from their bytes as read."""

    def __init__(self, structure: dict[str, Any], shapes: dict[str, Any], *, live: bool, xcaf: Any = None,
                 world: Callable[[tuple[int, ...]], Any] | None = None, mechanism: Any = None,
                 foreign: bool = False, path: Path | None = None) -> None:
        self.structure = structure
        self.shapes = shapes
        self.live = live
        self.xcaf = xcaf
        self._world = world
        self.mechanism = mechanism
        # A document this engine did not write (a foreign STEP): its keys are its reader's,
        # made again from the geometry before an xbf holds them.
        self.foreign = foreign
        self.path = path
        self._bytes: dict[str, bytes] = {}
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return self.structure["name"]

    @classmethod
    def read(cls, path: Any) -> "Source":
        """A document read from its file (``document.load``: held while the file stays as it
        was): an ``.xbf``, or a ``.step``/``.stp`` read into a document first."""
        from cadgen.engine import document

        path = Path(path)
        suffix = path.suffix.lower()
        if suffix in MESHES:
            raise ExportRefused(f"{path.name} is a mesh: it has no B-rep, and no export is made from one")
        if suffix not in SOURCES:
            raise ExportRefused(f"{path.name} is no document: an export reads an .xbf or a .step")
        if not path.is_file():
            raise ExportRefused(f"no document {path}")
        loaded = document.load(path)
        return cls(loaded.structure(), {p.key: p.shape for p in loaded.prototypes}, live=False, xcaf=loaded.doc,
                   world=lambda occurrence: loaded.worlds.get(occurrence), mechanism=loaded.mechanism,
                   foreign=loaded.writer is None, path=loaded.path)

    @classmethod
    def built(cls, doc: Any, tree: Any, path: Path | None) -> "Source":
        """The document a build made, in memory: its XCAF document and the tree it was
        made from; ``path`` the xbf it was saved as, if any (its version)."""
        from cadgen.engine import mechanism as kinematics

        structure = structure_of(tree, path)
        shapes: dict[str, Any] = {}
        for part in tree.parts:
            shapes.setdefault(part.key, part.shape)
        return cls(structure, shapes, live=True, xcaf=doc,
                   world=lambda occurrence: kinematics._occurrence(tree, occurrence)[0],
                   mechanism=tree.mechanism, path=None if path is None else Path(path))

    def world(self, occurrence: tuple[int, ...]) -> Any:
        """An occurrence's placement as built, a 4x4 matrix (numpy), or None."""
        import numpy as np

        from cadgen.engine.mechanism import _numpy

        if self._world is None:
            return None
        found = self._world(tuple(occurrence))
        return None if found is None else np.asarray(_numpy(found), dtype=float)

    def shape_data(self, key: str) -> bytes:
        """A part's shape bytes, the canonical form it is meshed from (``mesh.shape_bytes``)."""
        with self._lock:
            found = self._bytes.get(key)
        if found is None:
            from cadgen.engine.mesh import shape_bytes

            shape = self.shapes[key]
            found = bytes(shape) if isinstance(shape, (bytes, bytearray)) else shape_bytes(shape, read=not self.live)
            with self._lock:
                self._bytes[key] = found
        return found

    def view(self, pose: Any = None, part: str | None = None) -> "View":
        return View.of(self, pose=pose, part=part)

    def shape(self) -> Any:
        """The document as a build123d compound tree: an assembly node a ``Compound`` of its
        components, a part a shape of its own on the prototype's geometry, placed where the
        document places it, labelled, coloured and given its material."""
        import numpy as np
        from build123d import Color, Compound, Location
        from build123d.importers import topods_lut
        from build123d.topology.shape_core import downcast
        from OCP.gp import gp_Trsf
        from OCP.TopLoc import TopLoc_Location

        from cadgen.engine.export.tree import _material
        from cadgen.engine.mesh import read_shape

        instances = self.structure["instances"]

        def node(tree: dict[str, Any]) -> Any:
            if "children" in tree:
                return Compound(children=[node(child) for child in tree["children"]], label=tree["name"])
            prototype = self.structure["prototypes"][tree["prototype"]]
            instance = instances[tree["instance"]]
            shape = self.shapes[prototype["key"]]
            topods = downcast(read_shape(shape) if isinstance(shape, (bytes, bytearray)) else shape)
            made = topods_lut[type(topods)](topods)
            made.label = tree["name"]
            rgba = instance.get("color") or prototype.get("color")
            made.color = Color(*rgba) if rgba else None
            material = _material(prototype.get("material"))
            if material is not None:
                made.material = material
            matrix = np.array(instance["matrix"], dtype=float).reshape(4, 4).T
            trsf = gp_Trsf()
            trsf.SetValues(*(float(matrix[row, column]) for row in range(3) for column in range(4)))
            made.move(Location(TopLoc_Location(trsf)))
            return made

        return node(self.structure["tree"])


_STRUCTURES: dict[int, tuple[Any, Any, dict[str, Any]]] = {}


def structure_of(tree: Any, path: Path | None) -> dict[str, Any]:
    """The structure of a build's tree (``document.tree_structure``), made once per tree
    and path: the worker's patch and the build's exports share it."""
    from cadgen.engine import document

    held = _STRUCTURES.get(id(tree))
    if held is not None and held[0] is tree and held[1] == path:
        return held[2]
    found = document.tree_structure(tree, Path(path) if path is not None else None)
    _STRUCTURES.clear()  # one build's tree at a time
    _STRUCTURES[id(tree)] = (tree, path, found)
    return found


# --- what a translation writes ---------------------------------------------------------------


@dataclass
class Placed:
    """One placed part: the prototype's index in the structure, its world matrix (4x4,
    numpy, row-major), its names from the root down, and its instance's own colour."""

    prototype: int
    matrix: Any
    path: list[str]
    color: tuple | None


@dataclass
class View:
    """A document as one translation writes it: posed (``pose``) and cut to a subtree
    (``part``), or as it is. ``tree`` is the structure's tree from the root written (its
    nodes' ``instance`` indices the structure's), ``root`` that root's occurrence in the
    document, ``rerooted`` the matrix that takes the document's frame to the root's."""

    source: Source
    name: str
    placements: list[Placed]
    tree: dict[str, Any]
    root: tuple[int, ...]
    rerooted: Any
    posed: bool
    parted: bool
    matrices: list[Any]  # every instance's world matrix in the root's frame, by instance index

    @property
    def prototypes(self) -> list[dict[str, Any]]:
        return self.source.structure["prototypes"]

    def used(self) -> list[int]:
        """The prototypes the view places, each once, in the order first placed."""
        found: dict[int, None] = {}
        for placed in self.placements:
            found.setdefault(placed.prototype, None)
        return list(found)

    @classmethod
    def of(cls, source: Source, *, pose: Any = None, part: str | None = None) -> "View":
        import numpy as np

        structure = source.structure
        instances = structure["instances"]
        columns = [instance["matrix"] for instance in instances]
        if pose is not None:
            from cadgen.engine import mechanism

            try:
                values, _named = mechanism.resolve(structure.get("kinematics"), pose)
                mechanism.link_worlds(structure["kinematics"], values)
                columns = mechanism.posed_instances(structure, values)
            except ValueError as error:
                raise ExportRefused(f"--pose: {error}") from None
        matrices = [np.array(values, dtype=float).reshape(4, 4).T for values in columns]
        tree, root, name = structure["tree"], (), structure["name"]
        rerooted = np.eye(4)
        if part is not None:
            tree, root = _subtree(structure, str(part))
            name = tree["name"]
            if "children" in tree:
                world = source.world(root)
                if world is None:
                    raise ExportRefused(f"--part {part}: the document does not say where {name!r} stands")
            else:
                world = np.array(instances[tree["instance"]]["matrix"], dtype=float).reshape(4, 4).T
            rerooted = np.linalg.inv(world)
            matrices = [rerooted @ matrix for matrix in matrices]
        placements = []
        for index in _instances(tree):
            instance = instances[index]
            names = instance["path"].split("/")
            placements.append(Placed(instance["prototype"], matrices[index], names[len(root):] if root else names,
                                     tuple(instance["color"]) if instance.get("color") else None))
        return cls(source, name, placements, tree, root, rerooted, pose is not None, part is not None, matrices)


def _instances(node: dict[str, Any]) -> list[int]:
    if "children" not in node:
        return [node["instance"]]
    return [index for child in node["children"] for index in _instances(child)]


def _subtree(structure: dict[str, Any], part: str) -> tuple[dict[str, Any], tuple[int, ...]]:
    """The node ``part`` names: an occurrence's path from the root (``board/peg1``, as the
    structure spells an instance's ``path``; the root's name may be left off), else a
    node's name where one node bears it. Two alike, or none, are refused by name."""
    nodes: list[tuple[str, dict[str, Any], tuple[int, ...]]] = []

    def visit(node: dict[str, Any], names: list[str], occurrence: tuple[int, ...]) -> None:
        nodes.append(("/".join(names), node, occurrence))
        for at, child in enumerate(node.get("children") or []):
            visit(child, names + [child["name"]], occurrence + (at,))

    root = structure["tree"]
    visit(root, [root["name"]], ())
    wanted = part.strip().strip("/")
    for full, node, occurrence in nodes:
        if full == wanted or full == f"{root['name']}/{wanted}":
            return node, occurrence
    named = [(full, node, occurrence) for full, node, occurrence in nodes if node["name"] == wanted]
    if len(named) == 1:
        return named[0][1], named[0][2]
    if named:
        raise ExportRefused(f"--part {part!r} names {len(named)} nodes: "
                            f"{', '.join(full for full, _node, _at in named[:6])}{', ...' if len(named) > 6 else ''}; "
                            "give one's path")
    shown = [full for full, node, _at in nodes[1:] if "children" in node][:8] or [full for full, *_ in nodes[1:9]]
    raise ExportRefused(f"{structure['name']} has no part {part!r}" + (f"; its assemblies: {', '.join(shown)}"
                                                                      if shown else ""))


# --- the one call ------------------------------------------------------------------------------


def translate(document: Any, kind: str | None, path: Any, *, pose: Any = None, part: str | None = None,
              animate: Any = None) -> int:
    """Write ``document`` (a ``Source``, or the path of an ``.xbf``/``.step``) to ``path``
    as ``kind`` (from the suffix when None); returns the file's size."""
    path = Path(path)
    kind = kind or kind_of(path)
    if kind not in KINDS:
        raise ExportRefused(f"no export {kind!r}; the kinds are {', '.join(KINDS)}")
    if path.suffix.lower() not in KINDS[kind].suffixes:
        raise ExportRefused(f"{path.name}: a {kind} export ends in {' or '.join(KINDS[kind].suffixes)}")
    if animate is not None:
        raise ExportRefused("GLB animation is not built yet: animate takes nothing")
    source = document if isinstance(document, Source) else Source.read(document)
    if kind == "xbf":
        if pose is not None:
            raise ExportRefused("a posed xbf is refused: a pose is a request, never a document")
        if not source.foreign and part is None and source.path is not None and source.path.suffix.lower() == ".xbf":
            raise ExportRefused("an xbf is written from another xbf only with --part: the document already is one")
    view = source.view(pose=pose, part=part)
    path.parent.mkdir(parents=True, exist_ok=True)
    spec = KINDS[kind]
    if not spec.atomic:
        spec.write(view, path)
        return path.stat().st_size
    temporary = path.with_name(f".{path.stem}.{os.getpid()}.{threading.get_ident()}.tmp{path.suffix}")
    try:
        spec.write(view, temporary)
        size = temporary.stat().st_size
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return size
