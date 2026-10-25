"""A model's delta: what its build holds at a glance against the revision built before it.

A build's worker says what its tree holds (``facts``): its part occurrences, its world bounds --
each part's box in its own frame, kept by the part's geometry key, so a part a build hands back
unchanged is not measured again, placed where each occurrence stands -- and its mechanism's
joints, configuration sequences and warnings. The box is the kernel's quick one (``BRepBndLib.Add``),
but a free-form face (a B-spline's, a Bezier's, an offset) gives its edges' alone: its surface's
poles reach far past a trimmed face, so its own box was off by up to 168 mm on juno's panels. On
juno's 246 parts, measured cold, that took 31 ms, and its bounds were ``cadgen inspect``'s to 0.1 mm;
``inspect``'s exact boxes (``BRepBndLib.AddOptimal``) took 8.6-12 s. A face bulging past its edges at
the model's extreme is under-measured. The daemon keeps each document's facts beside its record, and in the registry's, and
says the build's against the last (``delta``): each fact now, and what it was when it changed;
a first build has nothing to compare. The CLI prints it under the run line (``cli._describe``).
An edit that keeps every one of those -- a hole's size, a thickness, a fillet -- is still said: the
facts keep each part's geometry key (``shapes``: the digest the build keyed the part by, shortened,
and how many occurrences have it) and, per key, a digest of its parts' colours and materials
(``looks``), so the delta counts the parts whose geometry
no part of the build before had (``changed``), and says when only the look changed. Both come from
what the tree already holds: nothing is measured for them. Volume is not among them: measured, it
costs a kernel pass per part.
"""
from __future__ import annotations

import hashlib
from collections import OrderedDict
from typing import Any

# Per part geometry key, its box in its own frame (``min + max``), or None for no geometry.
_BOXES: "OrderedDict[str, tuple[float, ...] | None]" = OrderedDict()
_BOXES_HELD = 1 << 16
FACTS = ("parts", "bounds", "joints", "sequences", "warnings")
_KEY = 12  # the hex digits of a part's geometry key the facts keep: enough to tell two builds' parts apart


def _shown(value: float) -> float:
    """A measure as the run line prints it (``cli._number``): four significant figures below a
    thousand, whole above. Bounds are compared so, so a change too small to print is none."""
    return float(f"{value:.4g}") if abs(value) < 1000 else float(round(value))


def _box(part: Any) -> tuple[float, ...] | None:
    found = _BOXES.get(part.key, ())
    if found != ():
        _BOXES.move_to_end(part.key)
        return found
    from OCP.Bnd import Bnd_Box
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepBndLib import BRepBndLib
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    box = Bnd_Box()
    faces = TopExp_Explorer(part.shape, TopAbs_FACE)
    while faces.More():
        face = TopoDS.Face_s(faces.Current())
        if BRepAdaptor_Surface(face, False).GetType() in _free():
            edges = TopExp_Explorer(face, TopAbs_EDGE)
            while edges.More():
                BRepBndLib.Add_s(edges.Current(), box, False)
                edges.Next()
        else:
            BRepBndLib.Add_s(face, box, False)
        faces.Next()
    if box.IsVoid():  # wires and points alone
        BRepBndLib.Add_s(part.shape, box, False)
    found = None if box.IsVoid() else tuple(box.Get())
    _BOXES[part.key] = found
    while len(_BOXES) > _BOXES_HELD:
        _BOXES.popitem(last=False)
    return found


def _free() -> set:
    from OCP.GeomAbs import GeomAbs_BezierSurface, GeomAbs_BSplineSurface, GeomAbs_OffsetSurface, GeomAbs_OtherSurface

    return {GeomAbs_BSplineSurface, GeomAbs_BezierSurface, GeomAbs_OffsetSurface, GeomAbs_OtherSurface}


def facts(tree: Any, model: str, signature: tuple) -> dict[str, Any]:
    """What a built tree (``document.Tree``, its parts keyed) holds: ``parts``, its occurrences;
    ``bounds``, its world box's size (None with no geometry); ``joints``, ``sequences`` and
    ``warnings``, its mechanism's. A model whose tree has the signature its last build here had
    (``build._finish``'s: every part, placement, name and harvest) is not walked again."""
    last = _LAST.get(model)
    if last is not None and last[0] == signature:
        return dict(last[1])
    found = _walked(tree)
    _LAST[model] = (signature, found)
    return dict(found)


# Per model (``script::name``), the signature of the tree its last build here made, and its facts.
_LAST: dict[str, tuple[tuple, dict[str, Any]]] = {}


def _walked(tree: Any) -> dict[str, Any]:
    from OCP.gp import gp_Pnt, gp_Trsf

    from cadgen.engine.document import Assembly

    low, high = [float("inf")] * 3, [float("-inf")] * 3
    count = 0
    shapes: dict[str, int] = {}
    looks: dict[str, set[str]] = {}  # per geometry key, its parts' looks: a rename or another copy changes none

    def visit(prototype: Any, placed: Any, painted: Any = None) -> None:
        nonlocal count
        if isinstance(prototype, Assembly):
            for component in prototype.components:
                visit(component.prototype, placed.Multiplied(component.location.Transformation()),
                      component.color if component.color is not None else painted)
            return
        count += 1
        key = prototype.key[:_KEY]
        shapes[key] = shapes.get(key, 0) + 1
        looks.setdefault(key, set()).add(repr((painted, prototype.color, sorted(prototype.face_colors.items()), prototype.material,
                        sorted(prototype.face_materials.items(), key=lambda item: item[0]))))
        box = _box(prototype)
        if box is None:
            return
        for x in (box[0], box[3]):
            for y in (box[1], box[4]):
                for z in (box[2], box[5]):
                    corner = gp_Pnt(x, y, z).Transformed(placed)
                    for axis, value in enumerate((corner.X(), corner.Y(), corner.Z())):
                        low[axis], high[axis] = min(low[axis], value), max(high[axis], value)

    visit(tree.root, gp_Trsf())
    mechanism = tree.mechanism
    return {"parts": count,
            "bounds": None if low[0] == float("inf") else [_shown(b - a) for a, b in zip(low, high)],
            "joints": len(mechanism.pairs) if mechanism is not None else 0,
            "sequences": len(mechanism.motions) if mechanism is not None else 0,
            "warnings": len(mechanism.warnings) if mechanism is not None else 0,
            "shapes": dict(sorted(shapes.items())),
            "looks": {key: hashlib.blake2b("\0".join(sorted(found)).encode(), digest_size=4).hexdigest()
                      for key, found in sorted(looks.items())}}


def delta(now: dict[str, Any], before: dict[str, Any] | None) -> dict[str, Any]:
    """Each fact ``now`` holds, ``{"now": value}``, with ``"was"`` where ``before`` held another;
    ``first`` when there was no build before to compare. Against a build before whose facts kept
    its parts' geometry keys, ``changed``: ``parts``, how many of this build's part occurrences
    have a geometry no part of the build before had, and ``looks``, whether a colour or a material
    changed on a geometry both builds have."""
    found: dict[str, Any] = {"first": before is None}
    for name in FACTS:
        row: dict[str, Any] = {"now": now.get(name)}
        if before is not None and before.get(name) != row["now"]:
            row["was"] = before.get(name)
        found[name] = row
    if before is not None and before.get("shapes") is not None and now.get("shapes") is not None:
        was, looked, looks = before["shapes"], before.get("looks") or {}, now.get("looks") or {}
        found["changed"] = {"parts": sum(count for key, count in now["shapes"].items() if key not in was),
                            "looks": any(looked[key] != look for key, look in looks.items() if key in looked)}
    return found
