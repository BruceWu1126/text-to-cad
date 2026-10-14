"""Resolve what the recorder can't read from the program text alone.

The recorder (record.py) turns a model into a CSG program without running
build123d, so it can't tell which edges ``part.edges().group_by(Axis.Z)[-1]``
picks, which glyphs ``Text("KEEL")`` draws, or where a swept path runs. This
module runs the model once more in real build123d, only to read those answers:
the selected edges and the faces beside them, glyph outlines, section and path
polylines. Every solid is still built by the kernel.

Edge treatments become ordinary CSG tools, so every kernel can apply them:
a fillet or chamfer on a straight edge is a prism of the material it removes
(or, on a concave edge, adds), on a circular edge a revolve of it. Edges that
are neither are reported and left sharp.
"""
from __future__ import annotations

import math
import types

SAG = 0.002  # mm, chord deviation for sampled arcs and curves

# -- small vector helpers on tuples -----------------------------------------


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _add(a, b):
    return tuple(x + y for x, y in zip(a, b))


def _mul(a, s):
    return tuple(x * s for x in a)


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a):
    n = math.sqrt(_dot(a, a))
    return tuple(x / n for x in a) if n > 1e-15 else None


def _round(m):
    return [0.0 if abs(v) < 1e-12 else round(v, 12) for v in m]


def _segments(radius, angle):
    """Chords for an arc of this radius and angle (radians) within SAG."""
    if radius <= SAG:
        return 4
    step = 2 * math.acos(max(-1.0, 1 - SAG / radius))
    return max(4, math.ceil(abs(angle) / step))


# -- cross-sections ---------------------------------------------------------


def _section(kind, size, t1, t2):
    """2D outline of what a fillet/chamfer of ``size`` takes off (or puts on)
    a corner at the origin whose faces run along unit vectors t1 and t2.

    The outline reaches a little past the corner (into air on a convex edge,
    into material on a concave one) so no tool face lies on a part face."""
    cos_t = max(-1.0, min(1.0, t1[0] * t2[0] + t1[1] * t2[1]))
    theta = math.acos(cos_t)
    if theta < 1e-3 or theta > math.pi - 1e-3:
        return None  # faces tangent or folded: nothing to round
    b = _norm((t1[0] + t2[0], t1[1] + t2[1], 0.0))[:2]
    past = (-b[0] * 0.1 * size, -b[1] * 0.1 * size)
    if kind == "chamfer":
        return [(t1[0] * size, t1[1] * size), past, (t2[0] * size, t2[1] * size)]
    r = size
    s = r / math.tan(theta / 2)
    a = (t1[0] * s, t1[1] * s)
    bb = (t2[0] * s, t2[1] * s)
    c = (b[0] * r / math.sin(theta / 2), b[1] * r / math.sin(theta / 2))
    start = math.atan2(bb[1] - c[1], bb[0] - c[0])
    end = math.atan2(a[1] - c[1], a[0] - c[0])
    # the arc between the tangent points that faces the corner
    toward = math.atan2(-b[1], -b[0])
    sweep = (end - start) % (2 * math.pi)
    mid = start + sweep / 2
    if math.cos(mid - toward) < 0:
        sweep -= 2 * math.pi
    n = _segments(r, sweep)
    arc = [(c[0] + r * math.cos(start + sweep * i / n), c[1] + r * math.sin(start + sweep * i / n))
           for i in range(1, n)]
    return [a, past, bb, *arc]


def _polygon_entity(points):
    return {"type": "polygon", "points": [[round(x, 12), round(y, 12)] for x, y in points],
            "center": [0.0, 0.0], "rotation": 0.0, "mode": "add"}


# -- reading edges from real build123d --------------------------------------


def _edge_faces(body, edge):
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
    import build123d as b3

    cache = getattr(body, "_kernels_edge_faces", None)
    if cache is None:
        cache = TopTools_IndexedDataMapOfShapeListOfShape()
        TopExp.MapShapesAndAncestors_s(body.wrapped, TopAbs_EDGE, TopAbs_FACE, cache)
        try:
            body._kernels_edge_faces = cache
        except AttributeError:
            pass
    if not cache.Contains(edge.wrapped):
        return []
    faces = []
    for shape in cache.FindFromKey(edge.wrapped):
        face = TopoDS.Face_s(shape)
        if not any(face.IsSame(f.wrapped) for f in faces):
            faces.append(b3.Face(face))
    return faces


def _in_face_direction(face, point, tangent, h):
    """Unit direction at ``point``, perpendicular to the edge, into ``face``."""
    import build123d as b3

    n = tuple(face.normal_at(b3.Vector(*point)))
    d = _norm(_cross(tangent, n))
    if d is None:
        return None
    ahead = face.distance_to(b3.Vector(*_add(point, _mul(d, h))))
    behind = face.distance_to(b3.Vector(*_add(point, _mul(d, -h))))
    return d if ahead <= behind else _mul(d, -1)


def _inside(body, point):
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_IN

    return BRepClass3d_SolidClassifier(body.wrapped, gp_Pnt(*point), 1e-7).State() == TopAbs_IN


def _edge_tool(body, edge, kind, size):
    """One edge's tool node and whether it cuts (convex edge) or fills."""
    import build123d as b3

    faces = _edge_faces(body, edge)
    if len(faces) == 1:
        return "seam", None  # a closed face's seam: nothing to round (OCCT skips it too)
    if len(faces) != 2:
        return None, f"an edge with {len(faces)} faces"
    geom = edge.geom_type
    h = min(size * 0.25, 0.01)
    if geom == b3.GeomType.LINE:
        p0, p1 = tuple(edge.start_point()), tuple(edge.end_point())
        length = math.dist(p0, p1)
        tangent = _norm(_sub(p1, p0))
        mid = tuple(edge.position_at(0.5))
        t1 = _in_face_direction(faces[0], mid, tangent, h)
        t2 = _in_face_direction(faces[1], mid, tangent, h)
        if t1 is None or t2 is None:
            return None, "an edge whose faces have no normal there"
        x = t1
        y = _cross(tangent, x)
        outline = _section(kind, size, (1.0, 0.0), (_dot(t2, x), _dot(t2, y)))
        if outline is None:
            return None, "an edge between tangent faces"
        bis = _norm(_add(t1, t2))
        convex = _inside(body, _add(mid, _mul(bis, 0.1 * size)))
        matrix = [x[0], y[0], tangent[0], p0[0], x[1], y[1], tangent[1], p0[1],
                  x[2], y[2], tangent[2], p0[2], 0.0, 0.0, 0.0, 1.0]
        node = {"type": "prism", "profile": {"entities": [_polygon_entity(outline)]}, "amount": float(length),
                "both": False, "matrix": _round(matrix)}
        return (node, convex), None
    if geom == b3.GeomType.CIRCLE:
        center = tuple(edge.arc_center)
        radius = float(edge.radius)
        p0 = tuple(edge.position_at(0))
        t0 = _norm(tuple(edge.tangent_at(0)))
        radial = _norm(_sub(p0, center))
        axis = _norm(_cross(radial, t0))  # the arc runs counter-clockwise about it
        t1 = _in_face_direction(faces[0], p0, t0, h)
        t2 = _in_face_direction(faces[1], p0, t0, h)
        if t1 is None or t2 is None:
            return None, "an edge whose faces have no normal there"
        if abs(_dot(t1, t0)) > 1e-6 or abs(_dot(t2, t0)) > 1e-6:
            return None, "a circular edge between faces that aren't coaxial with it"
        u1 = (_dot(t1, radial), _dot(t1, axis))
        u2 = (_dot(t2, radial), _dot(t2, axis))
        outline = _section(kind, size, u1, u2)
        if outline is None:
            return None, "an edge between tangent faces"
        outline = [(max(radius + px, 0.0), py) for px, py in outline]
        bis = _norm(_add(t1, t2))
        convex = _inside(body, _add(p0, _mul(bis, 0.1 * size)))
        closed = edge.is_closed
        angle = 360.0 if closed else math.degrees(edge.length / radius)
        normal = _cross(radial, axis)
        matrix = [radial[0], axis[0], normal[0], center[0], radial[1], axis[1], normal[1], center[1],
                  radial[2], axis[2], normal[2], center[2], 0.0, 0.0, 0.0, 1.0]
        node = {"type": "revolve", "profile": {"entities": [_polygon_entity(outline)]},
                "axis": {"origin": list(center), "direction": list(axis)}, "angle": angle,
                "matrix": _round(matrix)}
        return (node, convex), None
    return None, f"a {str(geom).split('.')[-1].lower()} edge"


def _union(nodes):
    if not nodes:
        return None
    return nodes[0] if len(nodes) == 1 else {"type": "union", "children": nodes}


def edge_tools(body, edges, kind, size):
    """{"cut": node|None, "fill": node|None, "skipped": [reason, ...]}."""
    cut, fill, skipped = [], [], []
    for edge in edges:
        made, why = _edge_tool(body, edge, kind, float(size))
        if made == "seam":
            continue
        if made is None:
            skipped.append(why)
            continue
        node, convex = made
        (cut if convex else fill).append(node)
    return {"cut": _union(cut), "fill": _union(fill), "skipped": sorted(set(skipped))}


# -- the shadow run -----------------------------------------------------------


def _body_for(real, objects):
    """The part a fillet/chamfer call applies to, as build123d finds it."""
    context = real.Builder._get_context(None, False)
    target = getattr(context, "_obj", None) if context is not None else None
    if target is None:
        items = objects if isinstance(objects, (list, tuple)) else list(objects) if hasattr(objects, "__iter__") else [objects]
        target = next((getattr(o, "topo_parent", None) for o in items if getattr(o, "topo_parent", None) is not None), None)
    if target is None or getattr(target, "_dim", 3) != 3:
        return None
    return target


def _edges_of(objects):
    items = objects if isinstance(objects, (list, tuple)) else [objects]
    out = []
    for item in items:
        out.extend(item.edges() if hasattr(item, "edges") and not hasattr(item, "geom_type") else [item])
    return out


def shadow_module(real, found):
    """A build123d module that answers as ``real`` does and records into
    ``found`` (a dict of lists, in call order)."""
    module = types.ModuleType("build123d")
    module.__dict__.update({k: v for k, v in vars(real).items() if not k.startswith("__")})

    def edge_op(kind, original):
        def op(objects, *args, **kwargs):
            size = kwargs.get("radius", kwargs.get("length", args[0] if args else None))
            entry = {"kind": kind, "size": size}
            try:
                body = _body_for(real, objects)
                entry.update(edge_tools(body, _edges_of(objects), kind, size) if body is not None
                             else {"error": "no part to read the edges from"})
            except Exception as exc:  # recorded, never fatal: the kernel then draws the edge sharp
                entry["error"] = f"{type(exc).__name__}: {exc}"
            found.setdefault("edges", []).append(entry)
            return original(objects, *args, **kwargs)
        return op

    module.fillet = edge_op("fillet", real.fillet)
    module.chamfer = edge_op("chamfer", real.chamfer)

    def record(kind, entry_fn):
        """Run entry_fn for this call's findings; an error is recorded, not raised."""
        try:
            entry = entry_fn()
        except Exception as exc:
            entry = {"error": f"{type(exc).__name__}: {exc}"}
        found.setdefault(kind, []).append(entry)

    def pending_faces(sections):
        if sections is not None:
            items = sections if isinstance(sections, (list, tuple)) else [sections]
            return [f for item in items for f in (item.faces() if hasattr(item, "faces") else [item])]
        context = real.Builder._get_context(None, False)
        return list(getattr(context, "pending_faces", []) or [])

    def text(*args, **kwargs):
        obj = real.Text(*args, **kwargs)
        record("text", lambda: {"entities": text_entities(obj)})
        return obj

    def offset(objects=None, amount=0, openings=None, kind=real.Kind.ARC, *args, **kwargs):
        faces = [] if openings is None else (list(openings) if isinstance(openings, (list, tuple)) or hasattr(openings, "__iter__") and not hasattr(openings, "wrapped") else [openings])
        record("offset", lambda: {"amount": float(amount), "openings": opening_planes(faces), "rounded": kind == real.Kind.ARC})
        return real.offset(objects, amount, openings, kind, *args, **kwargs)

    def loft(sections=None, ruled=False, *args, **kwargs):
        faces = pending_faces(sections)
        record("loft", lambda: dict(loft_data(faces, ruled), ruled=bool(ruled)))
        return real.loft(sections, ruled, *args, **kwargs)

    def sweep(sections=None, path=None, multisection=False, *args, **kwargs):
        faces = pending_faces(sections)

        def entry():
            if multisection or len(faces) != 1:
                raise ValueError("sweeping several sections")
            wire = path if isinstance(path, real.Wire) else real.Wire(path.edges() if hasattr(path, "edges") else [path])
            return sweep_data(faces[0], wire)
        record("sweep", entry)
        return real.sweep(sections, path, multisection, *args, **kwargs)

    module.Text = text
    module.offset = offset
    module.loft = loft
    module.sweep = sweep
    return module


def shadow_run(target):
    """Run the model on real build123d with the recording wrappers; returns
    the dict of findings, or {"error": ...} when the model doesn't build."""
    import importlib
    import sys

    from cadgen_stub import install, load, shape_of, split_target

    # The recorder's stand-in may be installed as build123d: load the real one.
    if getattr(sys.modules.get("build123d"), "__file__", None) is None:
        sys.modules.pop("build123d", None)
    real = importlib.import_module("build123d")

    script, function = split_target(str(target))
    found = {}
    install(shadow_module(real, found))
    try:
        shape_of(load(script, function).call())
    except Exception as exc:
        found["error"] = f"{type(exc).__name__}: {exc}"
    return found


# -- outlines -----------------------------------------------------------------


def wire_samples(wire):
    """A wire as ordered samples (point, unit tangent, corner?), curves within
    SAG. corner marks an edge start where the direction turns."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepTools import BRepTools_WireExplorer
    from OCP.GCPnts import GCPnts_QuasiUniformDeflection
    from OCP.GeomAbs import GeomAbs_Line
    from OCP.gp import gp_Pnt, gp_Vec
    from OCP.TopAbs import TopAbs_REVERSED

    samples = []
    explorer = BRepTools_WireExplorer(wire.wrapped)
    while explorer.More():
        edge = explorer.Current()
        curve = BRepAdaptor_Curve(edge)
        first, last = curve.FirstParameter(), curve.LastParameter()
        if curve.GetType() == GeomAbs_Line:
            params = [first, last]
        else:
            sampler = GCPnts_QuasiUniformDeflection(curve, SAG)
            params = [sampler.Parameter(i) for i in range(1, sampler.NbPoints() + 1)]
        reverse = edge.Orientation() == TopAbs_REVERSED
        if reverse:
            params.reverse()
        pts = []
        for u in params:
            p, v = gp_Pnt(), gp_Vec()
            curve.D1(u, p, v)
            t = _norm((v.X(), v.Y(), v.Z())) or (0.0, 0.0, 0.0)
            pts.append(((p.X(), p.Y(), p.Z()), _mul(t, -1) if reverse else t))
        if samples:
            corner = _dot(samples[-1][1], pts[0][1]) < 1 - 1e-9
            samples[-1] = (pts[0][0], pts[0][1], corner)  # the shared vertex, as this edge starts
            samples.extend((p, t, False) for p, t in pts[1:])
        else:
            samples.extend((p, t, i == 0) for i, (p, t) in enumerate(pts))
        explorer.Next()
    if wire.is_closed and len(samples) > 1:
        end = samples.pop()
        samples[0] = (samples[0][0], samples[0][1], _dot(end[1], samples[0][1]) < 1 - 1e-9)
    return samples


def wire_points(wire):
    """A wire as an ordered polyline (3D), curves sampled within SAG."""
    return [p for p, _, _ in wire_samples(wire)]


def true_area(points2d, area):
    """Scale a sampled outline about its centroid to the face's exact area, so
    chords cut off by sampling don't shrink it (straight outlines are unchanged)."""
    sampled = abs(_signed_area(points2d))
    if sampled <= 0 or abs(area / sampled - 1) < 1e-12:
        return points2d
    k = math.sqrt(area / sampled)
    cx = sum(p[0] for p in points2d) / len(points2d)
    cy = sum(p[1] for p in points2d) / len(points2d)
    return [(cx + (px - cx) * k, cy + (py - cy) * k) for px, py in points2d]


def face_loops(face):
    """(outer polyline, [inner polylines]) of a face, in 3D."""
    return wire_points(face.outer_wire()), [wire_points(w) for w in face.inner_wires()]


def _plane_frame(face):
    """origin, x, y, normal of a planar face's plane (build123d's Plane(face))."""
    import build123d as b3

    plane = b3.Plane(face)
    return tuple(plane.origin), tuple(plane.x_dir), tuple(plane.y_dir), tuple(plane.z_dir)


def _signed_area(pts):
    return sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
               for i in range(len(pts))) / 2


def ear_clip(pts):
    """Triangles (index triples) of a simple polygon, counter-clockwise or not."""
    idx = list(range(len(pts)))
    if _signed_area(pts) < 0:
        idx.reverse()
    out = []

    def inside(p, a, b, c):
        def s(u, v):
            return (v[0] - u[0]) * (p[1] - u[1]) - (v[1] - u[1]) * (p[0] - u[0])
        return s(a, b) >= -1e-12 and s(b, c) >= -1e-12 and s(c, a) >= -1e-12

    guard = 0
    while len(idx) > 3 and guard < 10 * len(pts) ** 2:
        guard += 1
        n = len(idx)
        for k in range(n):
            i0, i1, i2 = idx[(k - 1) % n], idx[k], idx[(k + 1) % n]
            a, b, c = pts[i0], pts[i1], pts[i2]
            if (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) <= 1e-14:
                continue
            if any(inside(pts[j], a, b, c) for j in idx if j not in (i0, i1, i2)):
                continue
            out.append((i0, i1, i2))
            idx.pop(k)
            break
        else:
            break
    if len(idx) == 3:
        out.append(tuple(idx))
    return out


def _mesh_volume(vertices, triangles):
    vol = 0.0
    for a, b, c in triangles:
        p, q, r = vertices[a], vertices[b], vertices[c]
        vol += _dot(p, _cross(q, r)) / 6
    return vol


def closed_mesh(rings, closed_path=False):
    """A watertight mesh through equal-length 3D rings, capped at both ends
    (unless the path closes), outward-facing."""
    n = len(rings[0])
    vertices = [p for ring in rings for p in ring]
    tris = []
    count = len(rings) if closed_path else len(rings) - 1
    for i in range(count):
        a0, b0 = i * n, ((i + 1) % len(rings)) * n
        for k in range(n):
            k1 = (k + 1) % n
            tris.append((a0 + k, a0 + k1, b0 + k1))
            tris.append((a0 + k, b0 + k1, b0 + k))
    if not closed_path:
        for ring_index, flip in ((0, True), (len(rings) - 1, False)):
            ring = rings[ring_index]
            # triangulate in the ring's own best-fit plane
            c = tuple(sum(p[i] for p in ring) / n for i in range(3))
            nrm = (0.0, 0.0, 0.0)
            for k in range(n):
                nrm = _add(nrm, _cross(_sub(ring[k], c), _sub(ring[(k + 1) % n], c)))
            nrm = _norm(nrm) or (0.0, 0.0, 1.0)
            u = _norm(_cross(nrm, (1.0, 0.0, 0.0) if abs(nrm[0]) < 0.9 else (0.0, 1.0, 0.0)))
            v = _cross(nrm, u)
            flat = [(_dot(_sub(p, c), u), _dot(_sub(p, c), v)) for p in ring]
            base = ring_index * n
            for a, b, cc in ear_clip(flat):
                tris.append((base + a, base + cc, base + b) if flip else (base + a, base + b, base + cc))
    if _mesh_volume(vertices, tris) < 0:
        tris = [(a, c, b) for a, b, c in tris]
    return {"vertices": [round(x, 9) for p in vertices for x in p], "triangles": [i for t in tris for i in t]}


# -- loft -----------------------------------------------------------------------


def _ray_hit(poly, center, angle):
    """Where the ray from center at angle leaves the 2D polygon (farthest hit)."""
    dx, dy = math.cos(angle), math.sin(angle)
    best = None
    for i in range(len(poly)):
        (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % len(poly)]
        ex, ey = x1 - x0, y1 - y0
        den = dx * ey - dy * ex
        if abs(den) < 1e-15:
            continue
        t = ((x0 - center[0]) * ey - (y0 - center[1]) * ex) / den
        s = ((x0 - center[0]) * dy - (y0 - center[1]) * dx) / den
        if t > 0 and -1e-12 <= s <= 1 + 1e-12 and (best is None or t > best):
            best = t
    return None if best is None else (center[0] + dx * best, center[1] + dy * best)


def _arc_positions(poly):
    """Cumulative arc length at each vertex of a closed polygon, and the total."""
    acc = [0.0]
    for i in range(len(poly)):
        acc.append(acc[-1] + math.dist(poly[i], poly[(i + 1) % len(poly)]))
    return acc


def _at_length(poly, acc, s):
    total = acc[-1]
    s %= total
    for i in range(len(poly)):
        if acc[i + 1] >= s:
            t = (s - acc[i]) / max(acc[i + 1] - acc[i], 1e-15)
            a, b = poly[i], poly[(i + 1) % len(poly)]
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
    return poly[0]


def _length_of_hit(poly, acc, center, angle):
    """Arc length position where the ray from center at angle leaves the polygon."""
    dx, dy = math.cos(angle), math.sin(angle)
    best = None
    for i in range(len(poly)):
        (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % len(poly)]
        ex, ey = x1 - x0, y1 - y0
        den = dx * ey - dy * ex
        if abs(den) < 1e-15:
            continue
        t = ((x0 - center[0]) * ey - (y0 - center[1]) * ex) / den
        u = ((x0 - center[0]) * dy - (y0 - center[1]) * dx) / den
        if t > 0 and -1e-12 <= u <= 1 + 1e-12 and (best is None or t > best[0]):
            best = (t, acc[i] + u * (acc[i + 1] - acc[i]))
    return None if best is None else best[1]


def loft_data(faces, ruled):
    """Sections with matching points, paired as OCCT pairs them: every
    section is split at the angles (about its centre) of every section's
    corners, and each piece is sampled evenly along its length."""
    origin, x, y, z = _plane_frame(faces[0])
    sections = []
    for face in faces:
        if face.inner_wires():
            raise ValueError("lofting sections with holes")
        samples = wire_samples(face.outer_wire())
        local = [(_dot(_sub(p, origin), x), _dot(_sub(p, origin), y), _dot(_sub(p, origin), z)) for p, _, _ in samples]
        heights = [p[2] for p in local]
        if max(heights) - min(heights) > 1e-6 * max(1.0, max(abs(h) for h in heights)):
            raise ValueError("lofting sections that aren't parallel")
        flat = [(p[0], p[1]) for p in local]
        if not any(c for _, _, c in samples[1:]) or len(flat) > 4 * sum(1 for _, _, c in samples if c):
            flat = true_area(flat, face.area)  # curved outline: keep its exact area
        corners = [flat[i] for i, (_, _, c) in enumerate(samples) if c]
        if _signed_area(flat) < 0:
            flat.reverse()
        cx = sum(p[0] for p in flat) / len(flat)
        cy = sum(p[1] for p in flat) / len(flat)
        sections.append({"z": heights[0], "poly": flat, "center": (cx, cy), "corners": corners})
    breaks = sorted({round(math.atan2(py - sec["center"][1], px - sec["center"][0]) % (2 * math.pi), 12)
                     for sec in sections for px, py in sec["corners"]}) or [0.0]
    rings2d = []
    pieces = []
    for sec in sections:
        acc = _arc_positions(sec["poly"])
        cuts = [_length_of_hit(sec["poly"], acc, sec["center"], a) for a in breaks]
        if any(c is None for c in cuts):
            raise ValueError("a lofting section that isn't star-shaped about its centre")
        pieces.append((acc, cuts))
    # points per piece: enough for the finest section along it
    counts = []
    for k in range(len(breaks)):
        need = 1
        for sec, (acc, cuts) in zip(sections, pieces):
            span = (cuts[(k + 1) % len(cuts)] - cuts[k]) % acc[-1] or acc[-1]
            inside = sum(1 for s0 in acc[:-1] if (s0 - cuts[k]) % acc[-1] < span)
            need = max(need, inside + 1, math.ceil(span / max(acc[-1] / 32, 1e-6)))
        counts.append(need)
    for sec, (acc, cuts) in zip(sections, pieces):
        ring = []
        for k in range(len(breaks)):
            span = (cuts[(k + 1) % len(cuts)] - cuts[k]) % acc[-1] or acc[-1]
            ring.extend(_at_length(sec["poly"], acc, cuts[k] + span * i / counts[k]) for i in range(counts[k]))
        rings2d.append(ring)
    steps = 1 if ruled or len(sections) == 2 else 8
    rings3d, stations = [], []
    n = len(rings2d[0])
    for i in range(len(sections) - 1):
        for s in range(steps):
            t = s / steps
            ring = []
            for k in range(n):
                if steps == 1:
                    p = [rings2d[i][k][j] * (1 - t) + rings2d[i + 1][k][j] * t for j in range(2)]
                else:  # Catmull-Rom through the sections, for a smooth loft
                    p0 = rings2d[max(i - 1, 0)][k]
                    p1, p2 = rings2d[i][k], rings2d[i + 1][k]
                    p3 = rings2d[min(i + 2, len(sections) - 1)][k]
                    p = [0.5 * (2 * p1[j] + (-p0[j] + p2[j]) * t + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * t * t
                                + (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * t ** 3) for j in range(2)]
                ring.append(p)
            zz = sections[i]["z"] * (1 - t) + sections[i + 1]["z"] * t
            rings3d.append([_add(origin, _add(_mul(x, p[0]), _add(_mul(y, p[1]), _mul(z, zz)))) for p in ring])
            stations.append((zz, ring))
    last = sections[-1]
    rings3d.append([_add(origin, _add(_mul(x, p[0]), _add(_mul(y, p[1]), _mul(z, last["z"])))) for p in rings2d[-1]])
    stations.append((last["z"], rings2d[-1]))
    matrix = [x[0], y[0], z[0], origin[0], x[1], y[1], z[1], origin[1], x[2], y[2], z[2], origin[2], 0.0, 0.0, 0.0, 1.0]
    order = sorted(range(len(stations)), key=lambda i: stations[i][0])
    return {"matrix": _round(matrix),
            "stations": [round(stations[i][0], 12) for i in order],
            "rings": [[[round(a, 9), round(b, 9)] for a, b in stations[i][1]] for i in order],
            "mesh": closed_mesh(rings3d)}


# -- sweep -----------------------------------------------------------------------


def sweep_data(face, path_wire):
    """A profile carried along a sampled path with rotation-minimising frames
    (double reflection), set square to the path's own tangent at every sample
    and starting from the profile's own plane."""
    outer, inner = face_loops(face)
    if inner:
        raise ValueError("sweeping a profile with holes")
    samples = wire_samples(path_wire)
    closed = bool(path_wire.is_closed)
    if not closed:
        end = tuple(path_wire.end_point())
        if math.dist(samples[-1][0], end) > 1e-9:
            samples.append((end, _norm(tuple(path_wire.tangent_at(1))), False))
    origin, x, y, z = _plane_frame(face)
    start, tangent = samples[0][0], samples[0][1]
    xa = _norm(_sub(x, _mul(tangent, _dot(x, tangent)))) or _norm(_cross(tangent, (0.0, 0.0, 1.0)))
    ya = _cross(tangent, xa)
    profile = true_area([(_dot(_sub(p, start), xa), _dot(_sub(p, start), ya)) for p in outer], face.area)
    if _signed_area(profile) < 0:
        profile.reverse()
    frames = [(start, xa, ya, tangent)]
    for p, t_new, _ in samples[1:]:
        p_prev, x_prev, _, t_prev = frames[-1]
        v1 = _sub(p, p_prev)
        c1 = _dot(v1, v1)
        if c1 < 1e-24:
            continue
        x_l = _sub(x_prev, _mul(v1, 2 / c1 * _dot(v1, x_prev)))
        t_l = _sub(t_prev, _mul(v1, 2 / c1 * _dot(v1, t_prev)))
        v2 = _sub(t_new, t_l)
        c2 = _dot(v2, v2)
        x_new = _sub(x_l, _mul(v2, 2 / c2 * _dot(v2, x_l))) if c2 > 1e-24 else x_l
        x_new = _norm(_sub(x_new, _mul(t_new, _dot(x_new, t_new))))
        frames.append((p, x_new, _cross(t_new, x_new), t_new))
    rings = [[_add(o, _add(_mul(fx, u), _mul(fy, v))) for u, v in profile] for o, fx, fy, _ in frames]
    return {"section": [[round(u, 9), round(v, 9)] for u, v in profile],
            "path": [[round(c, 9) for c in o] for o, *_ in frames],
            "frames": [[[round(c, 12) for c in fx], [round(c, 12) for c in fy]] for _, fx, fy, _ in frames],
            "closed": closed,
            "mesh": closed_mesh(rings, closed_path=closed)}


# -- text ---------------------------------------------------------------------------


def text_entities(sketch_obj):
    """Polygon sketch entities of a Text object's glyphs, in sketch-local XY:
    each outline adds, each hole subtracts (kernels combine them in order)."""
    out = []
    for face in sketch_obj.faces():
        outer, inner = face_loops(face)
        out.append({"points": [[round(p[0], 9), round(p[1], 9)] for p in outer], "hole": False})
        for loop in inner:
            out.append({"points": [[round(p[0], 9), round(p[1], 9)] for p in loop], "hole": True})
    return out


# -- offset / shell -------------------------------------------------------------------


def opening_planes(faces):
    out = []
    for face in faces:
        if str(face.geom_type).split(".")[-1].upper() != "PLANE":
            raise ValueError("an opening that isn't flat")
        origin, _, _, normal = _plane_frame(face)
        out.append((normal, _dot(normal, origin)))
    return out


def _col(m, k):
    return (m[k], m[4 + k], m[8 + k])


def _origin(m):
    return (m[3], m[7], m[11])


def _with_origin(m, o):
    m = list(m)
    m[3], m[7], m[11] = o
    return _round(m)


def _polygon_offset(points, d):
    """Offset a simple CCW polygon outward by d (inward when negative):
    corners turning away from the offset get an arc, the others a miter."""
    n = len(points)
    if _signed_area(points) < 0:
        points = points[::-1]
    out = []
    for i in range(n):
        p_prev, p, p_next = points[i - 1], points[i], points[(i + 1) % n]
        e1 = _norm((p[0] - p_prev[0], p[1] - p_prev[1], 0.0))
        e2 = _norm((p_next[0] - p[0], p_next[1] - p[1], 0.0))
        if e1 is None or e2 is None:
            continue
        n1, n2 = (e1[1], -e1[0]), (e2[1], -e2[0])  # outward normals of a CCW polygon
        turn = e1[0] * e2[1] - e1[1] * e2[0]  # > 0: convex corner
        if (turn > 0) == (d > 0) and abs(turn) > 1e-12:
            a1, a2 = math.atan2(n1[1] * math.copysign(1, d), n1[0] * math.copysign(1, d)), \
                math.atan2(n2[1] * math.copysign(1, d), n2[0] * math.copysign(1, d))
            sweep = (a2 - a1 + math.pi) % (2 * math.pi) - math.pi
            steps = _segments(abs(d), sweep)
            out.extend((p[0] + abs(d) * math.cos(a1 + sweep * k / steps), p[1] + abs(d) * math.sin(a1 + sweep * k / steps))
                       for k in range(steps + 1))
        else:
            bis = _norm((n1[0] + n2[0], n1[1] + n2[1], 0.0))
            if bis is None:
                out.append((p[0] + n1[0] * d, p[1] + n1[1] * d))
                continue
            scale = d / max(1e-6, bis[0] * n1[0] + bis[1] * n1[1])
            out.append((p[0] + bis[0] * scale, p[1] + bis[1] * scale))
    return out


def _entity_offset(entity, d, rounded):
    """One sketch entity grown by d (shrunk when negative); None when gone."""
    e = dict(entity)
    if e["type"] == "circle":
        e["radius"] = e["radius"] + d
        return e if e["radius"] > 1e-9 else None
    if e["type"] == "rect":
        e["width"], e["height"] = e["width"] + 2 * d, e["height"] + 2 * d
        if min(e["width"], e["height"]) <= 1e-9:
            return None
        r = e.get("cornerRadius") or 0.0
        e["cornerRadius"] = max(0.0, r + d) if (d < 0 or rounded or r > 0) else 0.0
        return e
    if e["type"] == "polygon":
        pts = [tuple(p) for p in e["points"]]
        pts = _polygon_offset(pts, d) if rounded or d < 0 else _polygon_offset_sharp(pts, d)
        if len(pts) < 3 or abs(_signed_area(pts)) < 1e-12:
            return None
        e["points"] = [[round(x, 12), round(y, 12)] for x, y in pts]
        return e
    raise ValueError(f"offsetting a {e['type']} sketch entity")


def _polygon_offset_sharp(points, d):
    if _signed_area(points) < 0:
        points = points[::-1]
    out = []
    n = len(points)
    for i in range(n):
        p_prev, p, p_next = points[i - 1], points[i], points[(i + 1) % n]
        e1 = _norm((p[0] - p_prev[0], p[1] - p_prev[1], 0.0))
        e2 = _norm((p_next[0] - p[0], p_next[1] - p[1], 0.0))
        n1, n2 = (e1[1], -e1[0]), (e2[1], -e2[0])
        bis = _norm((n1[0] + n2[0], n1[1] + n2[1], 0.0)) or (n1[0], n1[1], 0.0)
        scale = d / max(1e-6, bis[0] * n1[0] + bis[1] * n1[1])
        out.append((p[0] + bis[0] * scale, p[1] + bis[1] * scale))
    return out


class Offsetter:
    """Grow (d > 0) or shrink (d < 0) a recorded CSG tree by |d|.

    Shrinking (erosion) is exact for boxes, cylinders, spheres, extrusions and
    revolves, and a cut from them grows its tool; a union shrinks each member,
    which leaves a wall where separate members touch (noted). Growing rounds
    edges for build123d's Kind.ARC where the shape allows it. Faces lying in an
    opening plane move outward instead, so a shell's cavity runs through them.
    """

    def __init__(self, rounded, openings=()):
        self.rounded = rounded
        self.openings = list(openings)
        self.notes = set()

    def _opening(self, normal, point):
        return any(_dot(normal, n) > 1 - 1e-9 and abs(_dot(n, point) - off) < 1e-6 for n, off in self.openings)

    def _span(self, m, k, lo, hi, d):
        """Shift each end of local axis k's [lo, hi] by d, or outward by |d| at an opening."""
        axis = _col(m, k)
        o = _origin(m)
        new = []
        for end, sign in ((lo, -1), (hi, 1)):
            point = _add(o, _mul(axis, end))
            out = _mul(axis, sign)
            new.append(end + sign * (abs(d) if self._opening(out, point) else d))
        return new

    def node(self, n, d):
        kind = n["type"]
        if kind == "box":
            m = n["matrix"]
            size, centre = [], [0.0, 0.0, 0.0]
            for k in range(3):
                lo, hi = self._span(m, k, -n["size"][k] / 2, n["size"][k] / 2, d)
                if hi - lo <= 1e-9:
                    return None
                size.append(hi - lo)
                centre[k] = (lo + hi) / 2
            o = _origin(m)
            world = _add(o, _add(_mul(_col(m, 0), centre[0]), _add(_mul(_col(m, 1), centre[1]), _mul(_col(m, 2), centre[2]))))
            out = {"type": "box", "size": size, "matrix": _with_origin(m, world)}
            if d > 0 and self.rounded:
                out["edgeFillet"] = d
            return out
        if kind == "hole" and n.get("depth") is None:
            r = n["radius"] + d
            return dict(n, radius=r) if r > 1e-9 else None
        if kind in ("cylinder", "hole"):
            m = n["matrix"]
            half = n["height"] / 2 if kind == "cylinder" else n["depth"] or 1e6
            lo, hi = self._span(m, 2, -half, half, d)
            r = n["radius"] + d
            if r <= 1e-9 or hi - lo <= 1e-9:
                return None
            o = _add(_origin(m), _mul(_col(m, 2), (lo + hi) / 2))
            if d > 0 and self.rounded:
                self.notes.add("grown cylinder ends are left square, not rounded")
            return {"type": "cylinder", "radius": r, "height": hi - lo, "matrix": _with_origin(m, o)}
        if kind == "sphere":
            r = n["radius"] + d
            return dict(n, radius=r) if r > 1e-9 else None
        if kind == "prism":
            amount = n["amount"]
            lo, hi = (-abs(amount), abs(amount)) if n.get("both") else ((0.0, amount) if amount >= 0 else (amount, 0.0))
            lo, hi = self._span(n["matrix"], 2, lo, hi, d)
            if hi - lo <= 1e-9:
                return None
            entities = []
            for e in n["profile"]["entities"]:
                grown = _entity_offset(e, d if e.get("mode") != "subtract" else -d, self.rounded)
                if grown is not None:
                    entities.append(grown)
                elif e.get("mode") != "subtract":
                    continue
            if not any(e.get("mode") != "subtract" for e in entities):
                return None
            if d > 0 and self.rounded:
                self.notes.add("grown extrusions keep square edges along their caps")
            m = n["matrix"]
            o = _add(_origin(m), _mul(_col(m, 2), lo))
            return {"type": "prism", "profile": {"entities": entities}, "amount": hi - lo, "both": False,
                    "matrix": _with_origin(m, o)}
        if kind == "revolve":
            entities = []
            for e in n["profile"]["entities"]:
                grown = _entity_offset(e, d if e.get("mode") != "subtract" else -d, self.rounded)
                if grown is not None:
                    entities.append(grown)
            if not any(e.get("mode") != "subtract" for e in entities):
                return None
            return dict(n, profile={"entities": entities})
        if kind == "union":
            parts = [p for p in (self.node(c, d) for c in n["children"]) if p is not None]
            if d < 0 and len(n["children"]) > 1:
                self.notes.add("where separate solids join, the shrunken copy keeps a wall between them")
            return _union(parts)
        if kind == "intersect":
            parts = [self.node(c, d) for c in n["children"]]
            return None if any(p is None for p in parts) else {"type": "intersect", "children": parts}
        if kind == "subtract":
            base = self.node(n["base"], d)
            if base is None:
                return None
            tool = self.node(n["tool"], -d)
            return base if tool is None else {"type": "subtract", "base": base, "tool": tool}
        if kind in ("fillet", "chamfer"):
            self.notes.add(f"the {kind} is offset as if the edge were sharp")
            return self.node(n["child"], d)
        raise ValueError(f"offsetting a {kind}")


def offset_tree(child, amount, openings, rounded):
    """The CSG tree build123d's offset() makes from child, and notes on
    where it is approximate."""
    t = abs(amount)
    if not openings:
        off = Offsetter(rounded)
        out = off.node(child, amount)
        if out is None:
            raise ValueError("the offset removes the whole part")
        return out, sorted(off.notes)
    if amount < 0:
        off = Offsetter(rounded, openings)
        inner = off.node(child, -t)
        return (child if inner is None else {"type": "subtract", "base": child, "tool": inner}), sorted(off.notes)
    off = Offsetter(rounded)
    outer = off.node(child, t)
    through = Offsetter(rounded, openings).node(child, 0.0)
    result = {"type": "subtract", "base": outer, "tool": through}
    for normal, offset in openings:  # nothing grows past an opening
        x = _norm(_cross(normal, (1.0, 0.0, 0.0) if abs(normal[0]) < 0.9 else (0.0, 1.0, 0.0)))
        y = _cross(normal, x)
        far = 1e5
        o = _add(_mul(normal, offset + far / 2), (0.0, 0.0, 0.0))
        box = {"type": "box", "size": [far, far, far],
               "matrix": _round([x[0], y[0], normal[0], o[0], x[1], y[1], normal[1], o[1], x[2], y[2], normal[2], o[2], 0, 0, 0, 1])}
        result = {"type": "subtract", "base": result, "tool": box}
    return result, sorted(off.notes)


# -- cache ----------------------------------------------------------------------------
# Importing build123d into a fresh process takes seconds, longer than most
# kernels take to build. The answers depend only on the model's inputs, so
# they are kept per input: the recorded program (every primitive, placement,
# selection and argument the recorder saw), the curves made, and the model's
# own source.


def _cache_dir():
    import os
    from pathlib import Path

    from .manifest import runtime_home

    return Path(runtime_home(os.environ)) / "_resolve"


def cached_shadow_run(target, program_root, curves):
    import hashlib
    import json
    from pathlib import Path

    from cadgen_stub import split_target

    script, function = split_target(str(target))
    digest = hashlib.sha256()
    for part in (json.dumps(program_root, sort_keys=True), json.dumps(curves), str(function),
                 Path(script).read_text(encoding="utf-8")):
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    path = _cache_dir() / f"{digest.hexdigest()}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    found = shadow_run(target)
    if "error" not in found:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(found), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            pass  # a read-only cache only costs time
    return found
