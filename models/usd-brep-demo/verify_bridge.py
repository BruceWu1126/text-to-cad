"""Manual check of cadgen.usd_brep_bridge: convert representative usd-brep solids
and compare each OpenCascade result with usd-brep's own geometry.

    uv run --python 3.12 --with usd-brep==1.0.0 --with-editable packages/cadgen \
      python models/usd-brep-demo/verify_bridge.py

A row passes when the shape is BRepCheck-valid, keeps its face count, matches
usd-brep's volume to 1e-6 and every usd-brep surface sample lies on it to 1e-4.
"""
from __future__ import annotations

import sys
import time

import numpy as np
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.gp import gp_Pnt

from cadgen import usd_brep as ub
from cadgen.usd_brep_bridge import brep_to_occt


def box(x, y, z, l, w, h):
    return ub.create_box((x, y, z), l, w, h)


def cyl(x, y, z, r, h):
    return ub.create_cylinder((x, y, z), r, h)


def filleted_box(**kw):
    b = box(0, 0, 0, 10, 10, 10)
    return ub.fillet_edges(b, b.edges(), radius=1.0, **kw)


def sphere_chain(n=8):
    s = ub.create_sphere((0, 0, 0), 2)
    for i in range(1, n):
        s = ub.boolean_union(s, ub.create_sphere((3 * i, 0, 0), 2))
    return s


def plate(n=16):
    p = box(0, 0, 0, 100, 100, 10)
    for i in range(int(n ** 0.5)):
        for j in range(int(n ** 0.5)):
            p = ub.boolean_difference(p, cyl(20 * (i + 1), 20 * (j + 1), -1, 3, 12))
    return p


CASES = {
    "box minus cylinder": lambda: ub.boolean_difference(box(0, 0, 0, 20, 10, 5), cyl(10, 5, -1, 2, 7)),
    "box union sphere": lambda: ub.boolean_union(box(0, 0, 0, 10, 10, 10), ub.create_sphere((5, 5, 10), 4)),
    "cube common sphere": lambda: ub.boolean_intersection(box(0, 0, 0, 10, 10, 10), ub.create_sphere((5, 5, 5), 6)),
    "plate with 16 holes": plate,
    "box, all edges filleted": filleted_box,
    "box, all edges G2-blended": lambda: filleted_box(xsect_type=2, continuity=2),
    "napkin ring": lambda: ub.boolean_difference(ub.create_sphere((0, 0, 0), 10), cyl(0, 0, -11, 3, 22)),
    "8-sphere chain": sphere_chain,
    "torus minus cone": lambda: ub.boolean_difference(ub.create_torus((0, 0, 0), 20, 5), ub.create_cone((20, 0, -10), 8, 2, 20)),
    "cone apex": lambda: ub.boolean_difference(ub.create_cone((0, 0, 0), 5, 0, 10), box(-1, -6, -1, 7, 12, 4)),
    "helix pipe": lambda: ub.pipe_sweep(ub.create_helix((0, 0, 0), 25, 10, 10, 5), radius=1.0),
    "box shelled, open top": lambda: open_top_shell(),
}


def open_top_shell():
    b = box(0, 0, 0, 20, 20, 20)
    top = [f for f in b.faces() if f.bounding_box(tight=True)[0][2] > 19.999]
    return ub.shell_brep(b, -2.0, faces_to_shell=top)


def occt_volume(shape):
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props, 1e-9)
    return props.Mass()


def face_count(shape):
    n, ex = 0, TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        n += 1
        ex.Next()
    return n


def max_deviation(brep, shape, samples=200):
    mesh = ub.tessellate(brep, chord_height_tolerance=0.05, curve_angle_tolerance_deg=20, surface_angle_tolerance_deg=20)
    pts = mesh.to_numpy_arrays()["points"].astype(float)
    worst = 0.0
    for p in pts[np.linspace(0, len(pts) - 1, min(samples, len(pts))).astype(int)]:
        vertex = BRepBuilderAPI_MakeVertex(gp_Pnt(*map(float, p))).Vertex()
        worst = max(worst, BRepExtrema_DistShapeShape(vertex, shape).Value())
    return worst


def main() -> int:
    failed = 0
    for name, make in CASES.items():
        brep = make()
        t = time.perf_counter()
        report = {}
        shape = brep_to_occt(brep, report=report)
        ms = (time.perf_counter() - t) * 1000
        want = brep.volume(relative_accuracy=1e-8)
        rel = abs(occt_volume(shape) - want) / want
        dev = max_deviation(brep, shape)
        ok = BRepCheck_Analyzer(shape).IsValid() and face_count(shape) == brep.face_count() and rel < 1e-6 and dev < 1e-4
        failed += not ok
        print(f"{'ok ' if ok else 'BAD'} {name:28} faces {brep.face_count():3}  volume {rel:.1e}  deviation {dev:.1e}  "
              f"strategy {report.get('strategy')}  {ms:6.0f} ms")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
