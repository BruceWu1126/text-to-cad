"""STEP: AP242, written by the kernel's XCAF writer from the document.

The assembly with its instance and product names, colours per part and per painted face,
material names and densities (the writer's material mode), and the element names on face
and edge sub-shape labels (``write.stepcaf.subshapes.name``). A document as built is
written from its XCAF document itself; a posed one, or a subtree, from the view made a
document again (``tree.tree_of``), every placement its own. The mechanism, its
configurations and the model's provenance are never in a STEP: the kernel has no writer
for them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = ["SCHEMA", "write"]

SCHEMA = "AP242DIS"


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
        if not writer.Transfer(doc, STEPControl_AsIs) or writer.Write(str(path)) != IFSelect_RetDone:
            raise OSError(f"could not write {path}")


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
