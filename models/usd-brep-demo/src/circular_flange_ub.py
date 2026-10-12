"""examples/src/circular_flange.py, modeled with NVIDIA usd-brep instead of build123d.

Same dimensions, same STEP contract: the usd-brep solid is converted to exact
OpenCascade B-rep at the return, and cadgen takes it from there.
"""
from __future__ import annotations

from math import cos, sin, tau

from cadgen import step
from cadgen import usd_brep as ub
from cadgen.usd_brep_bridge import to_build123d

OUTER_DIAMETER = 80.0
THICKNESS = 10.0
CENTRAL_BORE_DIAMETER = 30.0
BOLT_HOLE_COUNT = 6
BOLT_HOLE_DIAMETER = 6.0
BOLT_CIRCLE_DIAMETER = 60.0
OUTER_FILLET = 1.5


def circular_edges(brep, radius, z, tol=1e-6):
    """Edges that are full circles of `radius` at height `z` (usd-brep has no selectors)."""
    out = []
    for edge in brep.edges():
        (x0, y0, z0), (x1, y1, z1) = edge.bounding_box(tight=True)
        if abs(z0 - z) < tol and abs(z1 - z) < tol and abs((x1 - x0) - 2 * radius) < tol and abs((y1 - y0) - 2 * radius) < tol:
            out.append(edge)
    return out


@step(out="../STEP/circular_flange_ub.step")
def circular_flange_ub():
    """Return the circular flange model in millimeters."""
    # Primitives are Z-aligned and placed by their base centre; booleans consume their inputs.
    part = ub.create_cylinder((0.0, 0.0, 0.0), OUTER_DIAMETER / 2.0, THICKNESS)
    part = ub.boolean_difference(part, ub.create_cylinder((0.0, 0.0, -1.0), CENTRAL_BORE_DIAMETER / 2.0, THICKNESS + 2.0))
    for index in range(BOLT_HOLE_COUNT):
        angle = tau * index / BOLT_HOLE_COUNT
        x_pos, y_pos = BOLT_CIRCLE_DIAMETER / 2.0 * cos(angle), BOLT_CIRCLE_DIAMETER / 2.0 * sin(angle)
        hole = ub.create_cylinder((x_pos, y_pos, -1.0), BOLT_HOLE_DIAMETER / 2.0, THICKNESS + 2.0)
        part = ub.boolean_difference(part, hole)

    edges = circular_edges(part, OUTER_DIAMETER / 2.0, 0.0) + circular_edges(part, OUTER_DIAMETER / 2.0, THICKNESS)
    part = ub.fillet_edges(part, edges, radius=OUTER_FILLET)  # mutates and returns the same Brep
    return to_build123d(part, label="circular_flange_bolt_pattern")


if __name__ == "__main__":
    circular_flange_ub()
