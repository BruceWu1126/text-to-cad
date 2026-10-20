"""The document: a model's result as an XCAF document, the file it is saved as, and
the document read back.

A result becomes **prototypes** and **instances**. A prototype is a part -- one
``TShape`` in one orientation, with the face colours a model painted on it -- or an
assembly: its components, each a prototype placed at a location under a name, with
a colour where its instance's differs from its prototype's. Two nodes of the result
that hold the same geometry are one prototype placed twice, so a part used ten times
is one prototype with ten components above it. The result's tree (build123d's
compound children) gives names and colours; its geometry and placements are read
from the TopoDS compounds themselves, child by child, so a placed copy of an
assembly is placed as the kernel holds it.

The XCAF document: units millimetres; a name on every prototype and component; a
colour per part and per painted face; on each part's face and edge sub-shape labels
the element names the memo's history gave them (``kernel.trace``), so a face's name
survives into the file and into a STEP; and on each part the key of its geometry
(``KEY_GUID``), what a reader caches the part's tessellation by. Saved with the
BinXCAF driver as ``.xbf``, atomically, and only when its bytes changed.

Read back (``Loaded``), a document is its prototypes, its instances with their world
placements, and what each prototype's elements are named; a STEP the engine did not
write reads the same way, with the XCAF reader.
"""

from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["Assembly", "Component", "Loaded", "Part", "Tree", "collect", "geometry_key", "load", "read_step_shape",
           "save", "to_xcaf"]

# The GUID of the attribute that holds a part's geometry key on its label.
KEY_GUID = "c8f2d4a6-1b3e-4f5a-9c7d-2e8b6a4f0d13"
_XCAF_LOCK = threading.RLock()


@dataclass
class Part:
    """A part prototype: its geometry at the identity (orientation kept), its name, the
    colour of its first instance, the face colours painted on it by ordinal, and,
    once named, its element names (``faces``/``edges`` lists by ordinal)."""

    shape: Any
    name: str
    color: tuple | None
    face_colors: dict[int, tuple]
    key: str = ""
    names: dict[str, Any] | None = None
    origin: Any = None  # the result's object it was first seen as


@dataclass
class Component:
    prototype: "Part | Assembly"
    location: Any
    name: str
    color: tuple | None


@dataclass
class Assembly:
    name: str
    components: list[Component] = field(default_factory=list)


@dataclass
class Tree:
    """A result as prototypes: ``order`` lists every prototype once, each after the ones
    it places; ``root`` is what the document's one free shape is."""

    root: "Part | Assembly"
    order: list
    parts: list[Part]
    signature: tuple


def _identity() -> Any:
    from OCP.TopLoc import TopLoc_Location

    return TopLoc_Location()


def _location_key(location: Any) -> tuple:
    if location.IsIdentity():
        return ()
    value = location.Transformation().Value
    return tuple(round(value(row, column), 12) for row in (1, 2, 3) for column in (1, 2, 3, 4))


def _rgba(color: Any) -> tuple | None:
    if color is None:
        return None
    try:
        values = tuple(float(value) for value in color)
    except TypeError:
        return None
    return values if len(values) == 4 else (*values[:3], 1.0)


def _children(topods: Any) -> list[Any]:
    from OCP.TopoDS import TopoDS_Iterator

    found = []
    iterator = TopoDS_Iterator(topods, False, False)
    while iterator.More():
        found.append(iterator.Value())
        iterator.Next()
    return found


def collect(result: Any, name: str) -> Tree:
    """The prototypes and instances of a model's result (see the module docstring)."""
    from OCP.TopAbs import TopAbs_COMPOUND

    from cadgen.engine.kernel.memo import FACE_COLORS

    prototypes: dict[tuple, Part | Assembly] = {}
    signatures: dict[int, tuple] = {}
    order: list = []
    parts: list[Part] = []

    def visit(obj: Any, topods: Any, inherited: tuple | None) -> tuple[Part | Assembly, tuple, tuple | None]:
        state = obj.__dict__ if obj is not None else {}
        color = _rgba(state.get("_color")) or inherited
        children = state.get("_NodeMixin__children") or ()
        label = state.get("label") or ""
        held = _children(topods) if children and int(topods.ShapeType()) == int(TopAbs_COMPOUND) else []
        if children and len(held) == len(children):
            components = []
            parts_signature = []
            for child, child_topods in zip(children, held):
                prototype, signature, child_color = visit(child, child_topods.Located(_identity()), color)
                child_label = child.__dict__.get("label") or ""
                components.append((prototype, child_topods.Location(), child_label, child_color))
                parts_signature.append((signature, _location_key(child_topods.Location()), child_label, child_color))
            signature = ("assembly", topods.TShape(), int(topods.Orientation()), label, tuple(parts_signature))
            found = prototypes.get(signature)
            if found is None:
                found = Assembly(label or f"assembly{len(order) + 1}")
                for prototype, location, child_label, child_color in components:
                    found.components.append(Component(prototype, location, child_label or prototype.name, child_color))
                prototypes[signature] = found
                order.append(found)
            return found, signature, color
        painted = tuple(sorted((int(ordinal), tuple(rgba)) for ordinal, rgba in (state.get(FACE_COLORS) or {}).items()))
        signature = ("part", topods.TShape(), int(topods.Orientation()), painted)
        found = prototypes.get(signature)
        if found is None:
            found = Part(topods, label or f"part{len(parts) + 1}", color, dict(painted), origin=obj)
            prototypes[signature] = found
            order.append(found)
            parts.append(found)
        return found, signature, color

    wrapped = result.wrapped
    root, signature, color = visit(result, wrapped.Located(_identity()), None)
    root_name = result.__dict__.get("label") or name
    location = wrapped.Location()
    if isinstance(root, Assembly) and location.IsIdentity():
        root.name = root_name
    else:
        holder = Assembly(root_name, [Component(root, location, root.name, color)])
        order.append(holder)
        root = holder
    return Tree(root, order, parts, (signature, _location_key(location), root_name))


# --- geometry keys ----------------------------------------------------------------------


def direct(shape: Any) -> Any:
    """``shape`` as the document holds it: every face on a direct (right-handed)
    surface. A STEP writer makes faces direct before it writes them, and a face it
    remakes loses its sub-shape label's name; made direct here, the writer remakes
    none and every face keeps its name. Face and edge ordinals are unchanged: the
    kernel's modifier rebuilds the shape in the order it walks it."""
    from OCP.ShapeCustom import ShapeCustom

    found = ShapeCustom.DirectFaces_s(shape)
    return shape if found is None or found.IsNull() else found


def geometry_key(shape: Any) -> str:
    """A key of a part's geometry: a digest of its binary BRep, no triangulation. Two
    parts with the same key tessellate alike, in any process."""
    import io

    from OCP.BinTools import BinTools, BinTools_FormatVersion_VERSION_4

    stream = io.BytesIO()
    BinTools.Write_s(shape, stream, False, False, BinTools_FormatVersion_VERSION_4)
    return hashlib.blake2b(stream.getvalue(), digest_size=16).hexdigest()


# --- the XCAF document --------------------------------------------------------------------


def _application() -> Any:
    from OCP.BinXCAFDrivers import BinXCAFDrivers
    from OCP.XCAFApp import XCAFApp_Application

    app = XCAFApp_Application.GetApplication_s()
    if not getattr(_application, "defined", False):
        BinXCAFDrivers.DefineFormat_s(app)
        _application.defined = True  # type: ignore[attr-defined]
    return app


def _new_document() -> Any:
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDocStd import TDocStd_Document

    app = _application()
    doc = TDocStd_Document(TCollection_ExtendedString("BinXCAF"))
    app.NewDocument(TCollection_ExtendedString("BinXCAF"), doc)
    return doc


def _quantity(rgba: tuple) -> Any:
    from OCP.Quantity import Quantity_Color, Quantity_ColorRGBA, Quantity_TOC_RGB

    # Linear RGB, as build123d's Color holds it.
    return Quantity_ColorRGBA(Quantity_Color(rgba[0], rgba[1], rgba[2], Quantity_TOC_RGB), float(rgba[3]))


def _name(label: Any, text: str) -> None:
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name

    TDataStd_Name.Set_s(label, TCollection_ExtendedString(text, True))


def to_xcaf(tree: Tree) -> Any:
    """The XCAF document of a tree whose parts are named (``Part.names``) and keyed."""
    from OCP.Standard import Standard_GUID
    from OCP.TCollection import TCollection_AsciiString
    from OCP.TDataStd import TDataStd_AsciiString
    from OCP.TDF import TDF_Label
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape
    from OCP.UnitsMethods import UnitsMethods_LengthUnit_Millimeter
    from OCP.XCAFDoc import XCAFDoc_ColorSurf, XCAFDoc_DocumentTool, XCAFDoc_ShapeTool

    with _XCAF_LOCK:
        XCAFDoc_ShapeTool.SetAutoNaming_s(False)
        doc = _new_document()
        XCAFDoc_DocumentTool.SetLengthUnit_s(doc, 1.0, UnitsMethods_LengthUnit_Millimeter)
        shapes = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
        colors = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
        key_guid = Standard_GUID(KEY_GUID)
        labels: dict[int, Any] = {}
        for prototype in tree.order:
            if isinstance(prototype, Part):
                label = shapes.AddShape(prototype.shape, False, False)
                _name(label, prototype.name)
                if prototype.color is not None:
                    colors.SetColor(label, _quantity(prototype.color), XCAFDoc_ColorSurf)
                if prototype.key:
                    TDataStd_AsciiString.Set_s(label, key_guid, TCollection_AsciiString(prototype.key))
                faces = TopTools_IndexedMapOfShape()
                TopExp.MapShapes_s(prototype.shape, TopAbs_FACE, faces)
                face_names = (prototype.names or {}).get("faces") or []
                painted = prototype.face_colors
                for ordinal in range(1, faces.Extent() + 1):
                    text = face_names[ordinal - 1] if ordinal <= len(face_names) else None
                    rgba = painted.get(ordinal)
                    if text is None and rgba is None:
                        continue
                    sub = TDF_Label()
                    if not shapes.AddSubShape(label, faces.FindKey(ordinal), sub) and sub.IsNull():
                        continue
                    if text is not None:
                        _name(sub, text)
                    if rgba is not None:
                        colors.SetColor(sub, _quantity(rgba), XCAFDoc_ColorSurf)
                edge_names = (prototype.names or {}).get("edges") or []
                if any(text is not None for text in edge_names):
                    edges = TopTools_IndexedMapOfShape()
                    TopExp.MapShapes_s(prototype.shape, TopAbs_EDGE, edges)
                    for ordinal in range(1, min(edges.Extent(), len(edge_names)) + 1):
                        text = edge_names[ordinal - 1]
                        if text is None:
                            continue
                        sub = TDF_Label()
                        if shapes.AddSubShape(label, edges.FindKey(ordinal), sub) or not sub.IsNull():
                            _name(sub, text)
            else:
                label = shapes.NewShape()
                _name(label, prototype.name)
                for component in prototype.components:
                    placed = shapes.AddComponent(label, labels[id(component.prototype)], component.location)
                    _name(placed, component.name)
                    held = component.prototype
                    if component.color is not None and not (isinstance(held, Part) and held.color == component.color):
                        colors.SetColor(placed, _quantity(component.color), XCAFDoc_ColorSurf)
            labels[id(prototype)] = label
        shapes.UpdateAssemblies()
        return doc


def save(doc: Any, path: Path, *, compare: bool) -> tuple[bool, int]:
    """Save ``doc`` to ``path`` atomically: a temporary file beside it whose name ends in
    ``.xbf`` (BinXCAF appends the suffix to any other name), then replaced over the
    path. With ``compare``, an existing file with the same bytes is left as it is.
    Returns whether the file was written, and its size."""
    from OCP.PCDM import PCDM_StoreStatus
    from OCP.TCollection import TCollection_ExtendedString

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.stem}.{os.getpid()}.{threading.get_ident()}.xbf")
    with _XCAF_LOCK:
        status = _application().SaveAs(doc, TCollection_ExtendedString(str(temporary), True))
    if status != PCDM_StoreStatus.PCDM_SS_OK or not temporary.exists():
        temporary.unlink(missing_ok=True)
        raise OSError(f"could not save {path}: {status}")
    size = temporary.stat().st_size
    if compare and path.exists() and path.stat().st_size == size and _same_bytes(path, temporary):
        temporary.unlink()
        return False, size
    os.replace(temporary, path)
    return True, size


def _same_bytes(first: Path, second: Path) -> bool:
    with open(first, "rb") as one, open(second, "rb") as two:
        while True:
            a, b = one.read(1 << 22), two.read(1 << 22)
            if a != b:
                return False
            if not a:
                return True


# --- reading ----------------------------------------------------------------------------


def read_step_shape(path: Any) -> Any:
    """A STEP file as build123d's importer reads it: a compound tree, labelled and
    coloured as the file is."""
    from build123d import importers

    return importers.import_step(os.fspath(path))


@dataclass
class Prototype:
    """A part of a loaded document: its geometry at the identity, its name and colour,
    its key, and what its faces and edges are named and coloured, by ordinal."""

    index: int
    shape: Any
    name: str
    color: tuple | None
    key: str
    face_names: dict[int, str]
    edge_names: dict[int, str]
    face_colors: dict[int, tuple]
    entry: str


@dataclass
class Instance:
    prototype: int
    path: list[str]
    matrix: list[float]  # 4x4, column-major, as WebGL reads it
    color: tuple | None


class Loaded:
    """A document read into memory: an ``.xbf`` through the BinXCAF retrieval driver, a
    ``.step``/``.stp`` through the XCAF reader. Its prototypes are its simple-shape labels,
    each once; its instances are every placement of one, through the assembly tree."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path).resolve()
        stat = self.path.stat()
        self.version = (stat.st_mtime_ns, stat.st_size)
        self.doc = _read(self.path)
        self.prototypes: list[Prototype] = []
        self.instances: list[Instance] = []
        self.name = self.path.stem
        self.tree: dict[str, Any] = {}
        self._walk()

    def _walk(self) -> None:
        from OCP.TDF import TDF_Label, TDF_LabelSequence, TDF_Tool
        from OCP.TCollection import TCollection_AsciiString
        from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ShapeTool

        shapes = XCAFDoc_DocumentTool.ShapeTool_s(self.doc.Main())
        by_entry: dict[str, int] = {}

        def entry_of(label: Any) -> str:
            text = TCollection_AsciiString()
            TDF_Tool.Entry_s(label, text)
            return text.ToCString()

        def visit(label: Any, location: Any, path: list[str], inherited: tuple | None, name: str) -> dict[str, Any]:
            color = _label_color(label) or inherited
            if XCAFDoc_ShapeTool.IsAssembly_s(label):
                node: dict[str, Any] = {"name": name, "children": []}
                components = TDF_LabelSequence()
                XCAFDoc_ShapeTool.GetComponents_s(label, components, False)
                for at in range(1, components.Length() + 1):
                    component = components.Value(at)
                    referred = TDF_Label()
                    XCAFDoc_ShapeTool.GetReferredShape_s(component, referred)
                    placed = location.Multiplied(XCAFDoc_ShapeTool.GetLocation_s(component))
                    child_name = _label_name(component) or _label_name(referred) or f"{name}.{at}"
                    child_color = _label_color(component) or color
                    node["children"].append(visit(referred, placed, path + [child_name], child_color, child_name))
                return node
            shape = XCAFDoc_ShapeTool.GetShape_s(label)
            entry = entry_of(label)
            index = by_entry.get(entry)
            if index is None:
                index = by_entry[entry] = len(self.prototypes)
                self.prototypes.append(_prototype(index, label, shape, entry, self))
            placed = location.Multiplied(shape.Location())
            prototype = self.prototypes[index]
            self.instances.append(Instance(index, path, _matrix(placed),
                                           color if color != prototype.color else None))
            return {"name": name, "prototype": index, "instance": len(self.instances) - 1}

        free = TDF_LabelSequence()
        shapes.GetFreeShapes(free)
        roots = []
        for at in range(1, free.Length() + 1):
            label = free.Value(at)
            name = _label_name(label) or f"{self.path.stem}{'' if free.Length() == 1 else at}"
            roots.append(visit(label, _identity(), [name], None, name))
        if len(roots) == 1:
            self.tree = roots[0]
            self.name = roots[0]["name"]
        else:
            self.tree = {"name": self.path.stem, "children": roots}

    def structure(self) -> dict[str, Any]:
        """The document as the page reads it (``serve``): its prototypes, its instances
        and its tree, with every name and colour; no geometry."""
        return {
            "name": self.name,
            "path": str(self.path),
            "version": f"{self.version[0]}-{self.version[1]}",
            "units": "mm",
            "prototypes": [{
                "index": p.index, "key": p.key, "name": p.name, "color": p.color,
                "faceNames": {str(k): v for k, v in sorted(p.face_names.items())},
                "edgeNames": {str(k): v for k, v in sorted(p.edge_names.items())},
                "faceColors": {str(k): v for k, v in sorted(p.face_colors.items())},
            } for p in self.prototypes],
            "instances": [{"prototype": i.prototype, "path": "/".join(i.path), "matrix": i.matrix, "color": i.color}
                          for i in self.instances],
            "tree": self.tree,
        }

    def root_label(self) -> Any:
        from OCP.TDF import TDF_LabelSequence
        from OCP.XCAFDoc import XCAFDoc_DocumentTool

        free = TDF_LabelSequence()
        XCAFDoc_DocumentTool.ShapeTool_s(self.doc.Main()).GetFreeShapes(free)
        return free


def _read(path: Path) -> Any:
    from OCP.BinXCAFDrivers import BinXCAFDrivers_DocumentRetrievalDriver
    from OCP.Message import Message_ProgressRange
    from OCP.TCollection import TCollection_ExtendedString

    suffix = path.suffix.lower()
    with _XCAF_LOCK:
        doc = _new_document()
        if suffix == ".xbf":
            # The application's Open and GetDocument leave the Python-side document empty:
            # the retrieval driver reads into the one handed to it.
            BinXCAFDrivers_DocumentRetrievalDriver().Read(TCollection_ExtendedString(str(path), True), doc,
                                                          _application(), None, Message_ProgressRange())
        elif suffix in (".step", ".stp"):
            from OCP.IFSelect import IFSelect_RetDone
            from OCP.Interface import Interface_Static
            from OCP.STEPCAFControl import STEPCAFControl_Reader

            reader = STEPCAFControl_Reader()
            reader.SetNameMode(True)
            reader.SetColorMode(True)
            # The reader defines the translator's parameters: set them once it exists.
            Interface_Static.SetIVal_s("read.stepcaf.subshapes.name", 1)
            if reader.ReadFile(str(path)) != IFSelect_RetDone or not reader.Transfer(doc):
                raise ValueError(f"cannot read {path}")
        else:
            raise ValueError(f"not a document the engine reads: {path.name}")
    return doc


def _find(label: Any, attribute: Any, guid: Any) -> bool:
    # FindAttribute faults on a label without the attribute: ask IsAttribute first.
    return label.IsAttribute(guid) and label.FindAttribute(guid, attribute)


def _label_name(label: Any) -> str:
    from OCP.TDataStd import TDataStd_Name

    found = TDataStd_Name()
    if _find(label, found, TDataStd_Name.GetID_s()):
        return found.Get().ToExtString()
    return ""


def _label_color(label: Any) -> tuple | None:
    from OCP.Quantity import Quantity_ColorRGBA
    from OCP.XCAFDoc import XCAFDoc_ColorGen, XCAFDoc_ColorSurf, XCAFDoc_ColorTool

    # Linear RGB and alpha, as build123d's Color holds a colour.
    rgba = Quantity_ColorRGBA()
    for kind in (XCAFDoc_ColorSurf, XCAFDoc_ColorGen):
        if XCAFDoc_ColorTool.GetColor_s(label, kind, rgba):
            rgb = rgba.GetRGB()
            return (round(rgb.Red(), 6), round(rgb.Green(), 6), round(rgb.Blue(), 6), round(rgba.Alpha(), 6))
    return None


def _prototype(index: int, label: Any, shape: Any, entry: str, loaded: Loaded) -> Prototype:
    from OCP.Standard import Standard_GUID
    from OCP.TDataStd import TDataStd_AsciiString
    from OCP.TDF import TDF_LabelSequence
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape
    from OCP.XCAFDoc import XCAFDoc_ShapeTool

    local = shape.Located(_identity())
    key = TDataStd_AsciiString()
    guid = Standard_GUID(KEY_GUID)
    if _find(label, key, guid):
        found_key = key.Get().ToCString()
    else:
        found_key = f"{loaded.version[0]}-{loaded.version[1]}-{entry}"
    face_names: dict[int, str] = {}
    edge_names: dict[int, str] = {}
    face_colors: dict[int, tuple] = {}
    subs = TDF_LabelSequence()
    XCAFDoc_ShapeTool.GetSubShapes_s(label, subs)
    if subs.Length():
        faces = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(shape, TopAbs_FACE, faces)
        edges = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(shape, TopAbs_EDGE, edges)
        for at in range(1, subs.Length() + 1):
            sub = subs.Value(at)
            held = XCAFDoc_ShapeTool.GetShape_s(sub)
            if held.IsNull():
                continue
            kind = held.ShapeType()
            if kind == TopAbs_FACE:
                ordinal = faces.FindIndex(held)
                if ordinal:
                    name = _label_name(sub)
                    if name:
                        face_names[ordinal] = name
                    color = _label_color(sub)
                    if color is not None:
                        face_colors[ordinal] = color
            elif kind == TopAbs_EDGE:
                ordinal = edges.FindIndex(held)
                name = _label_name(sub) if ordinal else ""
                if name:
                    edge_names[ordinal] = name
    return Prototype(index, local, _label_name(label) or f"part{index + 1}", _label_color(label), found_key,
                     face_names, edge_names, face_colors, entry)


def _matrix(location: Any) -> list[float]:
    transform = location.Transformation()
    value = transform.Value
    rows = [[value(row, column) for column in (1, 2, 3, 4)] for row in (1, 2, 3)] + [[0.0, 0.0, 0.0, 1.0]]
    return [rows[row][column] for column in range(4) for row in range(4)]


_LOADED: dict[Path, Loaded] = {}


def load(path: Path) -> Loaded:
    """The document at ``path``, read once and held while its file stays as it was:
    a changed file (its modification time or size) is read again."""
    path = Path(path).resolve()
    stat = path.stat()
    found = _LOADED.get(path)
    if found is not None and found.version == (stat.st_mtime_ns, stat.st_size):
        return found
    found = _LOADED[path] = Loaded(path)
    return found
