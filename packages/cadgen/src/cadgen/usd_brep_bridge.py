"""EXPERIMENTAL: hand a usd-brep (SMLib) solid to cadgen as exact OpenCascade B-rep.

    from cadgen import step
    from cadgen import usd_brep as ub
    from cadgen.usd_brep_bridge import to_build123d


    @step(out="../STEP/flange.step")
    def flange():
        part = ub.boolean_union(ub.create_cylinder((0, 0, 0), 40, 10), ub.create_cylinder((0, 0, 10), 15, 12))
        return to_build123d(part, label="flange")

The conversion is exact (same planes, cylinders, cones, spheres, tori and NURBS,
same topology), never a mesh. usd-brep's only exact interchange is OpenUSD's
``BrepArray``, so the Brep is exported to one, its packed arrays are read back,
and the topology is rebuilt in OpenCascade. Every result is validated (BRepCheck
plus a volume match against usd-brep's own integral) before it is returned; a
conversion that does not validate raises ``BridgeError`` rather than handing the
pipeline a wrong solid. The heavy implementation is imported on first call, so
importing this module costs nothing at a model's gate.
"""

from __future__ import annotations

from typing import Any


class BridgeError(RuntimeError):
    """A usd-brep -> OpenCascade conversion that did not validate."""


def brep_to_occt(brep: Any, *, validate: str | bool = "fast", report: dict | None = None) -> Any:
    """usd_brep.Brep -> OCP TopoDS_Shape.

    ``validate``: ``"fast"`` (BRepCheck + volume within 2% of usd-brep's default
    integral), ``"strict"`` (volume to 1e-6; seconds on large parts, because
    SMLib's integrator is slow at tight accuracy) or ``False``.
    """
    from cadgen._internal import usd_brep_occt

    return usd_brep_occt.brep_to_occt(brep, validate=validate, report=report)


def to_build123d(brep: Any, *, label: str | None = None, validate: str | bool = "fast") -> Any:
    """usd_brep.Brep -> build123d Solid (or Compound), ready to return from a model."""
    from cadgen import build123d as bd
    from OCP.TopAbs import TopAbs_SOLID

    shape = brep_to_occt(brep, validate=validate)
    obj = bd.Solid(shape) if shape.ShapeType() == TopAbs_SOLID else bd.Compound(shape)
    if label:
        obj.label = label
    return obj
