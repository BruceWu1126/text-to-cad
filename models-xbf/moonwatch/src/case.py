"""Case cluster entry: every case part positioned in the watch frame.

z = 0 at the case-middle / caseback joint plane, +Z through the crystal,
crown at +X (see `lib/spec.py`).
"""

import build123d as bd
from cadgen.engine import paint, xbf

from lib import case as C
from lib import materials as M


def _grained_top(ring):
    """The bezel ring's snailed top annulus (r 20.55-20.85 at z 9.20): its faces only,
    not the polished walls and rim chamfers around it."""
    top = ring.bounding_box().max.Z
    faces = []
    for face in ring.faces():
        box = face.bounding_box()
        if box.min.Z > top - 0.05 and max(abs(box.min.X), abs(box.max.X), abs(box.min.Y), abs(box.max.Y)) < 20.9:
            faces.append(face)
    return faces


@xbf(out="../XBF/case.xbf")
def case():
    parts = M.apply_materials(C.build_case_parts(), "case")
    # A face finish: the bezel ring is polished steel, its grained top annulus satin.
    ring = next(part for part in parts if part.label == "bezel_ring")
    paint(ring, _grained_top(ring), M.FINISHES["satin_steel"])
    compound = bd.Compound(children=parts, label="case")
    return compound


if __name__ == "__main__":
    case()
