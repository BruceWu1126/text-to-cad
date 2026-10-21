"""What a model imports: a STEP or an xbf made a build123d compound tree.

An XCAF document -- a STEP read by OCCT's XCAF reader, or an xbf -- is described as data
(``describe``): its products, each once however often it is placed (a name, a colour, a
material; a part's shape, the names its file gives its elements, an xbf's painted faces and
face materials; an assembly's components), and its free shapes; each component a product
placed by a location under a name and a colour. The shapes and the locations travel apart,
as kernel objects. ``assemble`` builds the tree a model gets from them: an assembly a
``Compound`` of its components, a part a handle of its own on its product's geometry (a
move places that occurrence, never the product's other placements), every node named as
the file names that occurrence (its own name, else its product's), coloured, given its
material. A mechanism is never imported: a part is a part.

A STEP is read by OCCT's XCAF reader (``read_step``), an xbf by the document's own
retrieval driver (``read_xbf``).
"""

from __future__ import annotations

import os
from typing import Any

__all__ = ["assemble", "describe", "read_step", "read_xbf"]


# --- a document described ---------------------------------------------------------------------


def _rgba(quantity: Any) -> list[float]:
    rgb = quantity.GetRGB()
    return [rgb.Red(), rgb.Green(), rgb.Blue(), quantity.Alpha()]


def describe(doc: Any, step: bool) -> tuple[dict[str, Any], list[Any], list[Any]]:
    """An XCAF document as data (see the module), the shapes its parts are, and the locations
    its components are placed at. ``step``: a STEP's names decoded as the file means them,
    and a part's colour, where its label has none, its shape's or its largest coloured
    face's (as build123d's importer takes it); an xbf's names as the engine wrote them, its
    colours its labels', its painted faces and face materials carried."""
    import unicodedata

    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.Quantity import Quantity_ColorRGBA
    from OCP.TDF import TDF_Label, TDF_LabelSequence
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp, TopExp_Explorer
    from OCP.TopTools import TopTools_IndexedMapOfShape
    from OCP.XCAFDoc import XCAFDoc_ColorCurv, XCAFDoc_ColorGen, XCAFDoc_ColorSurf, XCAFDoc_ColorTool
    from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ShapeTool

    from cadgen.engine.document import _entry, _label_material, _label_name, material_json, step_name
    from cadgen.engine.kernel import imported

    tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    colors = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
    kinds = (XCAFDoc_ColorCurv, XCAFDoc_ColorGen, XCAFDoc_ColorSurf) if step else (XCAFDoc_ColorSurf, XCAFDoc_ColorGen)

    def name_of(label: Any) -> str:
        if not step:
            return _label_name(label)
        text = step_name(_label_name(label))
        return "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")  # as the file has it

    def label_color(label: Any) -> list[float] | None:
        rgba = Quantity_ColorRGBA()
        for kind in kinds:
            if XCAFDoc_ColorTool.GetColor_s(label, kind, rgba):
                return _rgba(rgba)
        return None

    shape_colors: dict[Any, Any] = {}

    def own_color(shape: Any) -> list[float] | None:
        key = shape.TShape()
        if key not in shape_colors:
            rgba = Quantity_ColorRGBA()
            shape_colors[key] = _rgba(rgba) if any(colors.GetColor(shape, kind, rgba) for kind in kinds) else None
        return shape_colors[key]

    def shape_color(shape: Any) -> list[float] | None:
        found = own_color(shape)
        if found is not None:
            return found
        best, extent = None, -1.0
        explorer = TopExp_Explorer(shape, TopAbs_FACE)
        while explorer.More():
            face = explorer.Current()
            color = own_color(face)
            if color is not None:
                box = Bnd_Box()
                BRepBndLib.Add_s(face, box)
                if box.SquareExtent() > extent:
                    best, extent = color, box.SquareExtent()
            explorer.Next()
        return best

    def faces_given(label: Any, shape: Any) -> tuple[dict[str, list[float]], dict[str, Any]]:
        """An xbf part's painted faces and face materials, by ordinal."""
        paints: dict[str, list[float]] = {}
        given: dict[str, Any] = {}
        subs = TDF_LabelSequence()
        XCAFDoc_ShapeTool.GetSubShapes_s(label, subs)
        faces = None
        for at in range(1, subs.Length() + 1):
            sub = subs.Value(at)
            held = XCAFDoc_ShapeTool.GetShape_s(sub)
            if held.IsNull() or held.ShapeType() != TopAbs_FACE:
                continue
            if faces is None:
                faces = TopTools_IndexedMapOfShape()
                TopExp.MapShapes_s(shape, TopAbs_FACE, faces)
            ordinal = faces.FindIndex(held)
            if not ordinal:
                continue
            material = _label_material(sub)
            if material is not None:
                given[str(ordinal)] = material_json(material)
                continue
            color = label_color(sub)
            if color is not None:
                paints[str(ordinal)] = color
        return paints, given

    products: list[dict[str, Any]] = []
    index: dict[str, int] = {}
    shapes: list[Any] = []
    locations: list[Any] = []
    held_names: dict = {}

    def product(label: Any) -> int:
        key = _entry(label)
        found = index.get(key)
        if found is not None:
            return found
        topods = XCAFDoc_ShapeTool.GetShape_s(label)
        assembly = XCAFDoc_ShapeTool.IsAssembly_s(label)
        color = label_color(label)
        if color is None and step and not assembly:
            color = shape_color(topods)
        record: dict[str, Any] = {"name": name_of(label), "color": color, "assembly": assembly}
        found = index[key] = len(products)
        products.append(record)
        if assembly:
            components = TDF_LabelSequence()
            XCAFDoc_ShapeTool.GetComponents_s(label, components, False)
            record["components"] = [component(components.Value(at)) for at in range(1, components.Length() + 1)]
        else:
            record["shape"] = len(shapes)
            shapes.append(topods)
            names = imported.names(label, topods, held_names)  # what the file names its elements
            record["names"] = {str(kind): {str(o): text for o, text in found_names.items()}
                               for kind, found_names in names.items()}
            record["material"] = material_json(_label_material(label))
            if not step:
                record["paints"], record["face_materials"] = faces_given(label, topods)
        return found

    def component(label: Any) -> dict[str, Any]:
        referred = label
        if XCAFDoc_ShapeTool.IsReference_s(label):
            referred = TDF_Label()
            XCAFDoc_ShapeTool.GetReferredShape_s(label, referred)
        own = referred is not label
        at = len(locations)
        locations.append(XCAFDoc_ShapeTool.GetLocation_s(label))
        return {"product": product(referred), "name": name_of(label) if own else "",
                "color": label_color(label) if own else None, "location": at}

    free = TDF_LabelSequence()
    tool.GetFreeShapes(free)
    roots = [component(free.Value(at)) for at in range(1, free.Length() + 1)]
    return {"products": products, "roots": roots}, shapes, locations


def _material(found: dict[str, Any] | None) -> Any:
    from cadgen.engine.materials import Material

    if found is None:
        return None
    emissive = tuple(found.get("emissive") or ())
    return Material(found["name"], found.get("density"), tuple(found["color"]) if found.get("color") else None,
                    found.get("metallic", 0.0), found.get("roughness", 0.5), emissive if any(emissive) else None,
                    found.get("description"))


def assemble(header: dict[str, Any], shapes: list[Any], locations: list[Any]) -> Any:
    """The build123d tree a description makes (see the module): the free shape's node, or a
    ``Compound`` of the free shapes' nodes when the document has several."""
    from build123d import Color, Compound, Location
    from build123d.importers import topods_lut
    from build123d.topology.shape_core import downcast

    from cadgen.engine.kernel import imported
    from cadgen.engine.kernel.memo import FACE_COLORS, FACE_MATERIALS

    products = header["products"]

    def build(entries: list[dict[str, Any]]) -> list[Any]:
        made = []
        for entry in entries:
            found = products[entry["product"]]
            if found["assembly"]:
                node = Compound()
                node.children = build(found["components"])
            else:
                topods = downcast(shapes[found["shape"]])
                node = topods_lut[type(topods)](downcast(topods.Located(topods.Location())))
                names = {int(kind): {int(o): text for o, text in held.items()}
                         for kind, held in (found.get("names") or {}).items()}
                if names:
                    node.__dict__[imported.ELEMENT_NAMES] = names
                material = _material(found.get("material"))
                if material is not None:
                    node.material = material
                if found.get("paints"):
                    node.__dict__[FACE_COLORS] = {int(o): tuple(rgba) for o, rgba in found["paints"].items()}
                if found.get("face_materials"):
                    node.__dict__[FACE_MATERIALS] = {int(o): _material(given)
                                                     for o, given in found["face_materials"].items()}
            color = entry["color"] or found["color"]
            node.color = None if color is None else Color(*color)
            node.label = entry["name"] or found["name"]
            node.move(Location(locations[entry["location"]]))
            made.append(node)
        return made

    root = Compound()
    root.children = build(header["roots"])
    return root.children[0] if len(root.children) == 1 else root


# --- reading ---------------------------------------------------------------------------------


def _parse(path: str) -> tuple[dict[str, Any], list[Any], list[Any]]:
    """A STEP read into an XCAF document and described."""
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Interface import Interface_Static
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDocStd import TDocStd_Document

    doc = TDocStd_Document(TCollection_ExtendedString("XCAF"))
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    reader.SetColorMode(True)
    reader.SetLayerMode(True)
    reader.SetMatMode(True)
    Interface_Static.SetIVal_s("read.stepcaf.subshapes.name", 1)  # the names the file gives elements
    if reader.ReadFile(os.fspath(path)) != IFSelect_RetDone:
        raise ValueError(f"cannot read the STEP file {path}")
    reader.Transfer(doc)
    return describe(doc, step=True)


def read_step(path: Any) -> Any:
    """A STEP file as a build123d compound tree (see the module)."""
    return assemble(*_parse(os.path.abspath(os.fspath(path))))


def read_xbf(path: Any) -> Any:
    """An engine document (``.xbf``) as a build123d compound tree (see the module), read in
    this process: names, colours, materials, painted faces and element names as the document
    holds them; its mechanism and configurations are not imported."""
    from pathlib import Path

    from cadgen.engine.document import _read

    path = Path(os.fspath(path))
    if path.suffix.lower() != ".xbf":
        raise ValueError(f"read_xbf reads an .xbf, not {path.name}: read a STEP with read_step")
    if not path.is_file():
        raise FileNotFoundError(f"no document {path}")
    doc = _read(path)
    header, shapes, locations = describe(doc, step=False)
    return assemble(header, shapes, locations)
