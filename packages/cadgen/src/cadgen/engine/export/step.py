"""STEP: AP242, written by the kernel's XCAF writer from the document.

The assembly with its instance and product names, colours per part and per painted face,
material names and densities (the writer's material mode), and the element names on face
and edge sub-shape labels (``write.stepcaf.subshapes.name``). A document as built is
written from its XCAF document itself; a posed one, or a subtree, from the view made a
document again (``tree.tree_of``), every placement its own. The mechanism, its
configurations and the model's provenance are never in a STEP: the kernel has no writer
for them.

The same document writes the same bytes. The writer stamps the header's FILE_NAME with the
time of writing: it is ``WRITTEN_STAMP`` instead, set on the model's header. And it adds the
colour presentations (one per styled shape) to the model from a map keyed by shape, whose
order is the shapes' addresses, another in every process: they are put back in the order of
the shape representations they style, which the transfer numbered in the document's order.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = ["SCHEMA", "WRITTEN_STAMP", "write"]

SCHEMA = "AP242DIS"
# FILE_NAME's time_stamp: the date the xbf's header holds (``document.WRITTEN_DATE``), whatever
# the clock.
WRITTEN_STAMP = "2000-01-01T00:00:00"


def write(view: Any, path: Path) -> None:
    doc = view.source.xcaf
    if doc is None or view.posed or view.parted:
        from cadgen.engine import document
        from cadgen.engine.export.tree import tree_of

        doc = document.to_xcaf(tree_of(view))
    write_document(doc, path)


def write_document(doc: Any, path: Path) -> None:
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Interface import Interface_Static
    from OCP.Message import Message
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs

    from cadgen.engine.document import _XCAF_LOCK

    with _XCAF_LOCK:
        _quiet(Message)
        # The writer defines the translator's parameters: set them once it exists.
        writer = STEPCAFControl_Writer()
        Interface_Static.SetCVal_s("write.step.unit", "MM")
        Interface_Static.SetIVal_s("write.stepcaf.subshapes.name", 1)
        Interface_Static.SetCVal_s("write.step.schema", SCHEMA)
        writer.SetNameMode(True)
        writer.SetColorMode(True)
        writer.SetMaterialMode(True)
        writer.SetLayerMode(False)
        if not writer.Transfer(doc, STEPControl_AsIs):
            raise OSError(f"could not write {path}")
        _settle(writer.ChangeWriter().Model())
        if writer.Write(str(path)) != IFSelect_RetDone:
            raise OSError(f"could not write {path}")


def _settle(model: Any) -> None:
    """The transferred model made a function of the document alone: the header's time stamp
    pinned, and the colour presentations in the order of what they style."""
    from OCP.APIHeaderSection import APIHeaderSection_MakeHeader
    from OCP.StepVisual import StepVisual_MechanicalDesignGeometricPresentationRepresentation
    from OCP.TCollection import TCollection_HAsciiString

    header = APIHeaderSection_MakeHeader(model)
    header.SetTimeStamp(TCollection_HAsciiString(WRITTEN_STAMP))
    header.Apply(model)

    entities = [model.Value(number) for number in range(1, model.NbEntities() + 1)]
    kind = StepVisual_MechanicalDesignGeometricPresentationRepresentation
    first = next((index for index, entity in enumerate(entities) if isinstance(entity, kind)), None)
    if first is None:
        return
    # Everything before the first presentation is the transfer's, in the document's order; a
    # presentation and what it alone refers to were added after, in the map's order.
    head, tail = entities[:first], entities[first:]
    # An entity's number by its wrapper: the binding hands back the one Python object per kernel
    # object while ``entities`` holds it (its ``Number`` finds none).
    numbers = {id(entity): number for number, entity in enumerate(entities, 1)}

    def styled(presentation: Any) -> tuple:
        targets = []
        for index in range(1, presentation.NbItems() + 1):
            item = presentation.ItemsValue(index)
            target = item.ItemAP242().Value() if hasattr(item, "ItemAP242") else None
            targets.append(numbers.get(id(target), 0))
        return (numbers.get(id(presentation.ContextOfItems()), 0), tuple(targets))

    presentations = sorted((entity for entity in tail if isinstance(entity, kind)), key=styled)
    model.ClearEntities()
    for entity in head:
        model.AddEntity(entity)
    # Each presentation with what it refers to, then anything else the tail held, in its order:
    # an entity already in the model is not added again.
    for entity in [*presentations, *tail]:
        model.AddWithRefs(entity)


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
