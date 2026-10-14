"""Rebuild a recorded CSG program on Emergent Matter's SDM kernel.

SDM (github.com/EmergentMatter/emergent-matter-sdm, package
emergent-matter-sdm-core) models parts as signed distance fields compiled to
JAX and meshes them with marching cubes. It can't run build123d, so this
runner takes the generator's recorded program (references/program.md in this
skill documents the schema) and translates it into an SDM tree:

    box/cylinder/cone/sphere/hole  ->  box/capped_cylinder/capped_cone/sphere
    prism                          ->  2d_to_3d extrusion of the profile
    revolve (360 deg)              ->  2d_to_3d revolution of the profile
    union/subtract/intersect       ->  op nodes
    4x4 placement                  ->  translate + rotate_matrix

SDM has no edge fillets or chamfers. A fillet on every edge of a box becomes
SDM's exact round_box; any other fillet or chamfer is drawn sharp and listed
in the warnings. Partial revolves aren't supported.

    <runtime>/.venv/bin/python runner.py <program.json> <out.stl>

Prints one JSON line: {"ok", "volume", "triangles", "warnings"} or
{"ok": false, "error"}. Runs in its own venv (Python 3.13 + JAX) in the
kernel's runtime folder, made by setup.sh.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import sys
import tempfile
import traceback

# Voxel size, SDM's fidelity knob: as fine as a sample budget allows (so a
# small or flat part gets voxels thin walls survive: a 0.2 mm wall lost 1.3%
# of its volume at a fixed 128 cells across), and never coarser than 128 cells
# across the part's longest side. Flat regions are then decimated, keeping viewer
# meshes in the tens of thousands of triangles instead of hundreds. A fixed
# reduction: SDM's deviation-bounded search re-measured the surface after
# every step and took 10 of the 12 s of a build, while dropping 80% of the
# triangles leaves the deviation from the exact part unchanged (measured on
# the kernel benchmark's motor mount: 0.0134 mm mean either way).
CELLS_ACROSS = 128
MAX_CELLS_ACROSS = 400
SAMPLE_BUDGET = 2_000_000
DECIMATE_REDUCTION = 0.8


class Unsupported(Exception):
    """The program uses something this translation can't express in SDM."""


# -- 4x4 row-major placement -> SDM transform nodes ----------------------------

def _rotation(m):
    return [[m[0], m[1], m[2]], [m[4], m[5], m[6]], [m[8], m[9], m[10]]]


def _transpose(r):
    return [[r[j][i] for j in range(3)] for i in range(3)]


def _is_identity(r):
    return all(abs(r[i][j] - (1.0 if i == j else 0.0)) < 1e-12 for i in range(3) for j in range(3))


def _place(child, rotation, translation):
    """Put a local-frame child into the world.

    SDM transforms move the query point, so the child sees
    R^T (p - t): translate by t outside, rotate by R^T inside.
    """
    node = child
    if not _is_identity(rotation):
        node = {"type": "transform", "transform": "rotate_matrix", "child": node,
                "params": {"R": _transpose(rotation)}}
    if any(abs(v) > 1e-12 for v in translation):
        node = {"type": "transform", "transform": "translate", "child": node,
                "params": {"t": list(translation)}}
    return node


def _place_matrix(child, m):
    return _place(child, _rotation(m), (m[3], m[7], m[11]))


def _prim(kind, **params):
    return {"type": "primitive", "kind": kind, "params": params}


def _op(op, children):
    children = [c for c in children if c is not None]
    if len(children) == 1:
        return children[0]
    return {"type": "op", "op": op, "children": children}


# -- 2-D profiles ---------------------------------------------------------------

def _entity_points(entity, corner_segments=12):
    """Outline of a rect/polygon entity in sketch XY (counter-clockwise)."""
    cx, cy = entity.get("center") or (0.0, 0.0)
    rot = math.radians(entity.get("rotation") or 0.0)
    c, s = math.cos(rot), math.sin(rot)

    def place(x, y):
        return (cx + x * c - y * s, cy + x * s + y * c)

    if entity["type"] == "polygon":
        return [place(x, y) for x, y in entity["points"]]
    hx, hy = entity["width"] / 2, entity["height"] / 2
    r = min(entity.get("cornerRadius") or 0.0, hx, hy)
    if r <= 0:
        return [place(-hx, -hy), place(hx, -hy), place(hx, hy), place(-hx, hy)]
    points = []
    for x, y, start in ((hx - r, -hy + r, -90), (hx - r, hy - r, 0), (-hx + r, hy - r, 90), (-hx + r, -hy + r, 180)):
        for i in range(corner_segments + 1):
            a = math.radians(start + 90 * i / corner_segments)
            points.append(place(x + r * math.cos(a), y + r * math.sin(a)))
    return points


def _entity_2d(entity, to_profile):
    """One sketch entity as a 2-D SDF, in coordinates given by to_profile
    (a rigid map from sketch XY: identity for prisms, (radial, axial) for
    revolves)."""
    kind = entity["type"]
    if kind == "circle":
        cx, cy = to_profile(*(entity.get("center") or (0.0, 0.0)))
        return _place2d(_prim("circle_2d", r=entity["radius"]), cx, cy)
    if kind not in ("rect", "polygon"):
        raise Unsupported(f"sketch entity {kind}")
    if kind == "rect" and not (entity.get("rotation") or 0) and to_profile is _identity2d:
        cx, cy = entity.get("center") or (0.0, 0.0)
        b = [entity["width"] / 2, entity["height"] / 2]
        r = min(entity.get("cornerRadius") or 0.0, *b)
        shape = _prim("rounded_box_2d", b=b, r=r) if r > 0 else _prim("box_2d", b=b)
        return _place2d(shape, cx, cy)
    points = [to_profile(x, y) for x, y in _entity_points(entity)]
    if _signed_area(points) < 0:
        points.reverse()
    return _prim("polygon_2d", vertices=[list(p) for p in points])


def _identity2d(x, y):
    return (x, y)


def _signed_area(points):
    return 0.5 * sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1]))


def _place2d(node, x, y):
    if abs(x) < 1e-12 and abs(y) < 1e-12:
        return node
    return {"type": "transform", "transform": "translate", "child": node, "params": {"t": [x, y]}}


def _profile_2d(profile, to_profile=_identity2d):
    shape = None
    for entity in profile["entities"]:
        piece = _entity_2d(entity, to_profile)
        if entity.get("mode") == "subtract":
            if shape is None:
                raise Unsupported("a sketch that starts by subtracting")
            shape = _op("subtract", [shape, piece])
        elif entity.get("mode") == "intersect":
            shape = _op("intersect", [shape, piece])
        else:
            shape = piece if shape is None else _op("union", [shape, piece])
    if shape is None:
        raise Unsupported("an empty sketch")
    return shape


# -- program -> SDM tree --------------------------------------------------------

def _sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _unit(a):
    n = math.sqrt(_dot(a, a))
    return [a[0] / n, a[1] / n, a[2] / n]


def _apply(m, p):
    return [m[0] * p[0] + m[1] * p[1] + m[2] * p[2] + m[3],
            m[4] * p[0] + m[5] * p[1] + m[6] * p[2] + m[7],
            m[8] * p[0] + m[9] * p[1] + m[10] * p[2] + m[11]]


class Translator:
    def __init__(self, program):
        self.warnings = []
        self.extent = self._extent(program["root"])

    def _extent(self, node):
        """A length that comfortably spans the part, for through-holes."""
        sizes = []

        def walk(n):
            for key in ("size", "radius", "height", "amount", "bottomRadius", "topRadius"):
                v = n.get(key)
                if isinstance(v, (int, float)):
                    sizes.append(abs(v))
                elif isinstance(v, list):
                    sizes.extend(abs(x) for x in v)
            m = n.get("matrix")
            if m:
                sizes.extend(abs(m[i]) for i in (3, 7, 11))
            for e in (n.get("profile") or {}).get("entities", []):
                sizes.extend(abs(x) for x in (e.get("center") or ()))
                sizes.extend(abs(e.get(k) or 0) for k in ("width", "height", "radius"))
            for child in n.get("children", []):
                walk(child)
            for key in ("child", "base", "tool"):
                if n.get(key):
                    walk(n[key])

        walk(node)
        return 4 * sum(sizes) + 1.0

    def node(self, n):
        kind = n["type"]
        handler = getattr(self, f"_{kind}", None)
        if handler is None:
            raise Unsupported(f"program node {kind}")
        return handler(n)

    def _box(self, n):
        return _place_matrix(_prim("box", b=[s / 2 for s in n["size"]]), n["matrix"])

    def _cylinder(self, n):
        return _place_matrix(_prim("capped_cylinder", h=n["height"] / 2, r=n["radius"]), n["matrix"])

    def _cone(self, n):
        shape = _prim("capped_cone", h=n["height"] / 2, r1=n["bottomRadius"], r2=n["topRadius"])
        return _place_matrix(shape, n["matrix"])

    def _sphere(self, n):
        return _place_matrix(_prim("sphere", r=n["radius"]), n["matrix"])

    def _hole(self, n):
        # build123d's Hole reaches `depth` both ways from its location.
        depth = n["depth"] if n.get("depth") is not None else self.extent
        return _place_matrix(_prim("capped_cylinder", h=depth, r=n["radius"]), n["matrix"])

    def _prism(self, n):
        amount = n["amount"]
        z0, z1 = (-abs(amount), abs(amount)) if n.get("both") else ((0.0, amount) if amount >= 0 else (amount, 0.0))
        solid = {"type": "2d_to_3d", "method": "extrusion", "child": _profile_2d(n["profile"]),
                 "params": {"h": (z1 - z0) / 2}}
        solid = _place(solid, [[1, 0, 0], [0, 1, 0], [0, 0, 1]], (0.0, 0.0, (z0 + z1) / 2))
        return _place_matrix(solid, n["matrix"])

    def _revolve(self, n):
        m = n["matrix"]
        origin = n["axis"]["origin"]
        axis = _unit(n["axis"]["direction"])
        normal = [m[2], m[6], m[10]]
        if abs(_dot(normal, axis)) > 1e-6:
            raise Unsupported("a revolve axis outside the sketch plane")
        radial = _unit(_cross(axis, normal))
        # Point radial at the profile, as build123d's half-plane does.
        xs = [p for e in n["profile"]["entities"] if e.get("mode") != "subtract"
              for p in ([e.get("center") or (0, 0)])]
        cx = sum(p[0] for p in xs) / len(xs)
        cy = sum(p[1] for p in xs) / len(xs)
        if _dot(_sub(_apply(m, [cx, cy, 0.0]), origin), radial) < 0:
            radial = [-v for v in radial]

        def to_profile(x, y):
            d = _sub(_apply(m, [x, y, 0.0]), origin)
            return (_dot(d, radial), _dot(d, axis))

        solid = {"type": "2d_to_3d", "method": "revolution",
                 "child": _profile_2d(n["profile"], to_profile)}
        angle = n.get("angle", 360)
        if abs(abs(angle) - 360) > 1e-9:
            # SDM revolves all the way round: keep the wedge from the radial
            # direction, counter-clockwise about the axis (clockwise if negative).
            reach = 4 * max(1.0, *(abs(v) for v in _profile_box(n["profile"], to_profile)))
            sweep = math.radians(abs(angle))
            steps = max(2, math.ceil(sweep / (math.pi / 32)))
            sign = 1 if angle > 0 else -1
            wedge = [[0.0, 0.0]] + [[reach * math.cos(sign * sweep * i / steps), reach * math.sin(sign * sweep * i / steps)]
                                    for i in range(steps + 1)]
            if sign < 0:
                wedge.reverse()
            cut = {"type": "2d_to_3d", "method": "extrusion", "child": _prim("polygon_2d", vertices=wedge),
                   "params": {"h": reach}}
            solid = _op("intersect", [solid, cut])
        # Revolution is about local Z: local X = radial, Z = axis.
        frame = [[radial[i], _cross(axis, radial)[i], axis[i]] for i in range(3)]
        return _place(solid, frame, tuple(origin))

    def _loft(self, n):
        # SDM lofts matching polygon outlines along local Z at the stations;
        # the recorder already interpolated a smooth loft's in-between rings.
        children = [_prim("polygon_2d", vertices=[list(p) for p in ring]) for ring in n["rings"]]
        solid = {"type": "loft", "children": children, "params": {"z": list(n["stations"]), "interp": "shape"}}
        return _place_matrix(solid, n["matrix"])

    def _sweep(self, n):
        params = {"path": [list(p) for p in n["path"]], "path_kind": "polyline", "frame": "rmf",
                  "normal0": list(n["frames"][0][0])}
        if n.get("closed"):
            params["closed"] = True
        return {"type": "sweep", "child": _prim("polygon_2d", vertices=[list(p) for p in n["section"]]),
                "params": params}

    def _union(self, n):
        return _op("union", [self.node(c) for c in n["children"]])

    def _intersect(self, n):
        return _op("intersect", [self.node(c) for c in n["children"]])

    def _subtract(self, n):
        return _op("subtract", [self.node(n["base"]), self.node(n["tool"])])

    def _fillet(self, n):
        child = n["child"]
        if n.get("select", {}).get("all") and child["type"] == "box":
            b = [s / 2 for s in child["size"]]
            r = min(n["size"], *b)
            return _place_matrix(_prim("round_box", b=b, r=r), child["matrix"])
        return self._tools(n, "fillet")

    def _chamfer(self, n):
        return self._tools(n, "chamfer")

    def _tools(self, n, what):
        """The material the fillet/chamfer removes from convex edges and adds
        to concave ones, as resolved from the real build (resolve.py)."""
        tools = n.get("tools")
        if not tools:
            return self._sharp(n, what)
        solid = self.node(n["child"])
        if tools.get("cut"):
            solid = _op("subtract", [solid, self.node(tools["cut"])])
        if tools.get("fill"):
            solid = _op("union", [solid, self.node(tools["fill"])])
        return solid

    def _sharp(self, n, what):
        self.warnings.append(f"{n['size']} mm {what} drawn sharp: SDM has no edge {what}s")
        return self.node(n["child"])


# -- conservative bounds ----------------------------------------------------------
# SDM can only infer a sampling box for simple trees, so the part always
# carries one: each leaf's local box pushed through its placement, unions
# merged, subtractions keeping the base.

def _merge(boxes):
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return ([min(b[0][i] for b in boxes) for i in range(3)], [max(b[1][i] for b in boxes) for i in range(3)])


def _placed(m, lo, hi):
    corners = [_apply(m, [x, y, z]) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    return ([min(c[i] for c in corners) for i in range(3)], [max(c[i] for c in corners) for i in range(3)])


def _profile_box(profile, to_profile=_identity2d):
    xs, ys = [], []
    for e in profile["entities"]:
        if e.get("mode") == "subtract":
            continue
        if e["type"] == "circle":
            cx, cy = to_profile(*(e.get("center") or (0.0, 0.0)))
            xs += [cx - e["radius"], cx + e["radius"]]
            ys += [cy - e["radius"], cy + e["radius"]]
        else:
            for x, y in _entity_points(e):
                px, py = to_profile(x, y)
                xs.append(px)
                ys.append(py)
    return min(xs), max(xs), min(ys), max(ys)


def bounds(n):
    kind = n["type"]
    if kind == "box":
        h = [s / 2 for s in n["size"]]
        return _placed(n["matrix"], [-v for v in h], h)
    if kind in ("cylinder", "cone"):
        r = max(n.get("radius") or 0, n.get("bottomRadius") or 0, n.get("topRadius") or 0)
        return _placed(n["matrix"], [-r, -r, -n["height"] / 2], [r, r, n["height"] / 2])
    if kind == "sphere":
        r = n["radius"]
        return _placed(n["matrix"], [-r] * 3, [r] * 3)
    if kind == "prism":
        x0, x1, y0, y1 = _profile_box(n["profile"])
        a = n["amount"]
        z0, z1 = (-abs(a), abs(a)) if n.get("both") else (min(0, a), max(0, a))
        return _placed(n["matrix"], [x0, y0, z0], [x1, y1, z1])
    if kind == "revolve":
        # The swept profile's farthest point from the axis bounds every side.
        m, o = n["matrix"], n["axis"]["origin"]
        far = 0.0
        for e in n["profile"]["entities"]:
            pts = _entity_points(e) if e["type"] != "circle" else [
                ((e.get("center") or (0, 0))[0] + dx, (e.get("center") or (0, 0))[1] + dy)
                for dx, dy in ((e["radius"], 0), (-e["radius"], 0), (0, e["radius"]), (0, -e["radius"]))]
            for x, y in pts:
                d = _sub(_apply(m, [x, y, 0.0]), o)
                far = max(far, math.sqrt(_dot(d, d)) + (e.get("radius") or 0))
        return ([o[i] - far for i in range(3)], [o[i] + far for i in range(3)])
    if kind == "hole":
        return None
    if kind in ("loft", "sweep"):
        v = n["mesh"]["vertices"]
        pts = [v[i:i + 3] for i in range(0, len(v), 3)]
        return ([min(p[i] for p in pts) for i in range(3)], [max(p[i] for p in pts) for i in range(3)])
    if kind in ("union", "intersect"):
        return _merge([bounds(c) for c in n["children"]])
    if kind == "subtract":
        return bounds(n["base"])
    if kind in ("fillet", "chamfer"):
        return bounds(n["child"])
    raise Unsupported(f"program node {kind}")


def build_part(program):
    from software_defined_matter import MaterialRegion, Part

    translator = Translator(program)
    tree = translator.node(program["root"])
    lo, hi = bounds(program["root"])
    pad = 0.05 * max(h - l for l, h in zip(lo, hi)) + 0.5
    part = Part(
        name="model",
        materials=[MaterialRegion(material_id=1, name="PLA", sdf_tree=tree)],
        metadata={"bbox": [[v - pad for v in lo], [v + pad for v in hi]], "units": "mm"},
    )
    return part, translator.warnings


def run(program_path, stl_path):
    with open(program_path) as fh:
        program = json.load(fh)
    if not program.get("ok", True):
        return {"ok": False, "error": program.get("error") or "the program could not be recorded"}
    try:
        part, warnings = build_part(program)
    except Unsupported as exc:
        return {"ok": False, "error": f"SDM can't build {exc}"}

    import trimesh
    from software_defined_matter._meshing import DecimateConfig
    from software_defined_matter.export import export_part
    from software_defined_matter.grid_sampling import material_bbox

    box = material_bbox(part.materials[0], part)
    size = [float(v) for v in box.size]
    longest = max(size)
    voxel = min(longest / CELLS_ACROSS,
                max(longest / MAX_CELLS_ACROSS, (size[0] * size[1] * size[2] / SAMPLE_BUDGET) ** (1 / 3)))
    with tempfile.TemporaryDirectory() as tmp:
        decimate = DecimateConfig(target_reduction=DECIMATE_REDUCTION)
        (written,) = export_part(part, tmp, voxel_size=voxel, stamp=False, decimate=decimate)
        mesh = trimesh.load(written, force="mesh")
        os.makedirs(os.path.dirname(stl_path) or ".", exist_ok=True)
        shutil.move(str(written), stl_path)
    warnings.append(f"meshed at {voxel:.3g} mm voxels")
    return {"ok": True, "volume": float(mesh.volume), "valid": bool(mesh.is_watertight),
            "triangles": int(len(mesh.faces)), "warnings": warnings}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2:
        print(json.dumps({"ok": False, "error": "usage: runner.py <program.json> <out.stl>"}))
        return 2
    try:
        payload = run(*argv)
    except Exception as exc:  # report kernel failures as data
        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                   "trace": traceback.format_exc(limit=4)}
    sys.stdout.write("\n" + json.dumps(payload) + "\n")
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
