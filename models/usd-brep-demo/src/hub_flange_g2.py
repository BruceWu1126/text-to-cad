"""A hub flange whose hub-to-plate blend is curvature-continuous (G2).

The showcase for what usd-brep adds: build123d/OpenCascade fillets are circular
(G1, a curvature jump where the round meets each face), while usd-brep's blend
cross-section can be G2. The exact NURBS blend surface survives the bridge into
OpenCascade and on into the STEP and the GLB.
"""
from __future__ import annotations

from math import cos, sin, tau

from cadgen import glb, step
from cadgen import usd_brep as ub
from cadgen.usd_brep_bridge import to_build123d

PLATE_RADIUS = 40.0
PLATE_THICKNESS = 8.0
HUB_RADIUS = 14.0
HUB_HEIGHT = 18.0
BORE_RADIUS = 8.0
BOLT_COUNT = 6
BOLT_RADIUS = 3.0
BOLT_CIRCLE_RADIUS = 30.0
BLEND = 4.0  # hub-to-plate G2 blend
EDGE_ROUND = 1.0


def circles_at(brep, radius, z, tol=1e-6):
    out = []
    for edge in brep.edges():
        (x0, y0, z0), (x1, y1, z1) = edge.bounding_box(tight=True)
        if abs(z0 - z) < tol and abs(z1 - z) < tol and abs((x1 - x0) - 2 * radius) < tol and abs((y1 - y0) - 2 * radius) < tol:
            out.append(edge)
    return out


@step(out="../STEP/hub_flange_g2.step")
@glb(out="../GLB/hub_flange_g2.glb")
def hub_flange_g2():
    part = ub.boolean_union(
        ub.create_cylinder((0, 0, 0), PLATE_RADIUS, PLATE_THICKNESS),
        ub.create_cylinder((0, 0, PLATE_THICKNESS), HUB_RADIUS, HUB_HEIGHT),
    )
    part = ub.boolean_difference(part, ub.create_cylinder((0, 0, -1), BORE_RADIUS, PLATE_THICKNESS + HUB_HEIGHT + 2))
    for i in range(BOLT_COUNT):
        a = tau * i / BOLT_COUNT
        bolt = ub.create_cylinder((BOLT_CIRCLE_RADIUS * cos(a), BOLT_CIRCLE_RADIUS * sin(a), -1), BOLT_RADIUS, PLATE_THICKNESS + 2)
        part = ub.boolean_difference(part, bolt)

    # xsect_type=2 is usd-brep's blend cross-section; continuity=2 makes it G2.
    part = ub.fillet_edges(part, circles_at(part, HUB_RADIUS, PLATE_THICKNESS), radius=BLEND, xsect_type=2, continuity=2)
    rims = circles_at(part, PLATE_RADIUS, 0.0) + circles_at(part, PLATE_RADIUS, PLATE_THICKNESS)
    part = ub.fillet_edges(part, rims, radius=EDGE_ROUND)
    return to_build123d(part, label="hub_flange_g2")


if __name__ == "__main__":
    hub_flange_g2()
