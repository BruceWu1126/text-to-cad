"""Record a cadgen model as a kernel-neutral CSG program.

Kernels that can't run build123d (an SDF engine, a mesh CSG library) still
need the model's geometry. This module stands in for build123d: the model
program runs against these recording classes and the result is a JSON tree of
primitives, booleans and edge modifiers that each kernel evaluates its own way.

Only the common builder subset is recorded (BuildPart/BuildSketch,
Locations, Box/Cylinder/Sphere/Cone/Hole, Rectangle/RectangleRounded/Circle,
extrude, revolve, fillet, chamfer, Pos/Rot, and the +/-/& algebra). Anything
else raises Unsupported, which the CLI reports instead of guessing.

Program schema (lengths in mm, matrices row-major 4x4, Z up):
  leaf   {"type": "box", "size": [l, w, h], "matrix": [...]}
         {"type": "cylinder", "radius", "height", "matrix"}
         {"type": "cone", "bottomRadius", "topRadius", "height", "matrix"}
         {"type": "sphere", "radius", "matrix"}
         {"type": "hole", "radius", "depth" (null = through), "matrix"}
         {"type": "prism", "profile", "amount", "both", "matrix"}
         {"type": "revolve", "profile", "axis": {"origin", "direction"},
          "angle", "matrix"}
  bool   {"type": "union", "children": [...]}
         {"type": "subtract", "base": node, "tool": node}
         {"type": "intersect", "children": [...]}
  edges  {"type": "fillet" | "chamfer", "child": node, "size",
          "select": {"all": true} | {"parallel": "X|Y|Z"} | {"custom": str}}
Box, cylinder, cone and sphere are centered on their matrix origin. Holes
are build123d's: a cylinder centered on the location reaching depth along
both +Z and -Z (null = through the whole part). Prisms extrude the profile
(sketch-local XY) along the matrix's +Z.
"""
from __future__ import annotations

import enum
import math
import sys
import types
from pathlib import Path


class Unsupported(Exception):
    """The generator uses build123d API the recorder does not model."""


# -- 4x4 row-major matrices ------------------------------------------------

IDENTITY = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)


def _mul(a, b):
    return tuple(
        sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4)) for r in range(4) for c in range(4)
    )


def _translation(x=0.0, y=0.0, z=0.0):
    return (1.0, 0.0, 0.0, x, 0.0, 1.0, 0.0, y, 0.0, 0.0, 1.0, z, 0.0, 0.0, 0.0, 1.0)


def _rotation_xyz(rx, ry, rz):
    """Intrinsic X-Y-Z rotation in degrees, build123d's Location default."""
    cx, sx = math.cos(math.radians(rx)), math.sin(math.radians(rx))
    cy, sy = math.cos(math.radians(ry)), math.sin(math.radians(ry))
    cz, sz = math.cos(math.radians(rz)), math.sin(math.radians(rz))
    mx = (1, 0, 0, 0, 0, cx, -sx, 0, 0, sx, cx, 0, 0, 0, 0, 1)
    my = (cy, 0, sy, 0, 0, 1, 0, 0, -sy, 0, cy, 0, 0, 0, 0, 1)
    mz = (cz, -sz, 0, 0, sz, cz, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)
    return _mul(_mul(mx, my), mz)


def _apply(m, p):
    return tuple(m[r * 4] * p[0] + m[r * 4 + 1] * p[1] + m[r * 4 + 2] * p[2] + m[r * 4 + 3] for r in range(3))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(v):
    n = math.sqrt(sum(c * c for c in v))
    if n == 0:
        raise Unsupported("zero-length direction")
    return tuple(c / n for c in v)


def _round(m):
    return [0.0 if abs(v) < 1e-12 else round(v, 12) for v in m]


def _vec3(value):
    if isinstance(value, Vector):
        return value.tuple
    values = tuple(float(v) for v in value)
    if len(values) == 2:
        return (values[0], values[1], 0.0)
    if len(values) != 3:
        raise Unsupported(f"expected a 2D or 3D point, got {value!r}")
    return values


# -- build123d value types ---------------------------------------------------

class Vector:
    def __init__(self, *args):
        if len(args) == 1 and not isinstance(args[0], (int, float)):
            args = tuple(args[0])
        values = [float(v) for v in args] + [0.0] * (3 - len(args))
        self.X, self.Y, self.Z = values[:3]

    @property
    def tuple(self):
        return (self.X, self.Y, self.Z)

    def __iter__(self):
        return iter(self.tuple)


class Align(enum.Enum):
    MIN = "min"
    CENTER = "center"
    MAX = "max"
    NONE = "none"


class Mode(enum.Enum):
    ADD = "add"
    SUBTRACT = "subtract"
    INTERSECT = "intersect"
    REPLACE = "replace"
    PRIVATE = "private"


class Location:
    def __init__(self, *args):
        self.matrix = IDENTITY
        if not args:
            return
        first = args[0]
        if isinstance(first, Location):
            self.matrix = first.matrix
            return
        if isinstance(first, Plane):
            self.matrix = first.matrix
            return
        position = _vec3(first)
        rotation = tuple(float(v) for v in args[1]) if len(args) > 1 else (0.0, 0.0, 0.0)
        self.matrix = _mul(_translation(*position), _rotation_xyz(*rotation))

    @classmethod
    def _from(cls, matrix):
        loc = cls()
        loc.matrix = matrix
        return loc

    def __mul__(self, other):
        if isinstance(other, Location):
            return Location._from(_mul(self.matrix, other.matrix))
        if isinstance(other, Shape):
            return other.moved(self)
        return NotImplemented


def Pos(*args, X=0.0, Y=0.0, Z=0.0):  # noqa: N802, N803 - build123d names
    if args:
        x, y, z = _vec3(args if len(args) > 1 else args[0])
    else:
        x, y, z = X, Y, Z
    return Location._from(_translation(x, y, z))


def Rot(*args, X=0.0, Y=0.0, Z=0.0):  # noqa: N802, N803 - build123d names
    if args:
        x, y, z = _vec3(args if len(args) > 1 else args[0])
    else:
        x, y, z = X, Y, Z
    return Location._from(_rotation_xyz(x, y, z))


class Axis:
    def __init__(self, origin, direction):
        self.origin = _vec3(origin)
        self.direction = _unit(_vec3(direction))

    @property
    def name(self):
        for label, d in (("X", (1, 0, 0)), ("Y", (0, 1, 0)), ("Z", (0, 0, 1))):
            if all(abs(abs(a) - b) < 1e-9 for a, b in zip(self.direction, d)):
                return label
        return None

    def to_json(self):
        return {"origin": list(self.origin), "direction": list(self.direction)}


Axis.X = Axis((0, 0, 0), (1, 0, 0))
Axis.Y = Axis((0, 0, 0), (0, 1, 0))
Axis.Z = Axis((0, 0, 0), (0, 0, 1))


class Plane:
    def __init__(self, origin=(0, 0, 0), x_dir=(1, 0, 0), z_dir=(0, 0, 1)):
        if isinstance(origin, Location):
            self.matrix = origin.matrix
            return
        o = _vec3(origin)
        z = _unit(_vec3(z_dir))
        x = _unit(_vec3(x_dir))
        y = _cross(z, x)
        self.matrix = (x[0], y[0], z[0], o[0], x[1], y[1], z[1], o[1], x[2], y[2], z[2], o[2], 0, 0, 0, 1)

    @classmethod
    def _from(cls, matrix):
        plane = cls()
        plane.matrix = matrix
        return plane

    def offset(self, amount):
        return Plane._from(_mul(self.matrix, _translation(0, 0, amount)))

    def move(self, loc):
        return Plane._from(_mul(loc.matrix, self.matrix))

    def __mul__(self, other):
        if isinstance(other, Location):
            return Plane._from(_mul(self.matrix, other.matrix))
        if isinstance(other, Shape):
            return other.moved(Location._from(self.matrix))
        return NotImplemented

    def __rmul__(self, other):
        if isinstance(other, Location):
            return Plane._from(_mul(other.matrix, self.matrix))
        return NotImplemented


Plane.XY = Plane((0, 0, 0), (1, 0, 0), (0, 0, 1))
Plane.YX = Plane((0, 0, 0), (0, 1, 0), (0, 0, -1))
Plane.XZ = Plane((0, 0, 0), (1, 0, 0), (0, -1, 0))
Plane.ZX = Plane((0, 0, 0), (0, 0, 1), (0, 1, 0))
Plane.YZ = Plane((0, 0, 0), (0, 1, 0), (1, 0, 0))
Plane.ZY = Plane((0, 0, 0), (0, 0, 1), (-1, 0, 0))
Plane.front = Plane.XZ
Plane.top = Plane.XY
Plane.right = Plane.YZ


# -- shapes and the CSG tree -------------------------------------------------

def _union(a, b):
    if a is None:
        return b
    children = (a["children"] if a["type"] == "union" else [a]) + [b]
    return {"type": "union", "children": children}


def _combine(node, new, mode):
    if mode is Mode.PRIVATE:
        return node
    if mode is Mode.REPLACE or node is None:
        if mode is Mode.SUBTRACT:
            raise Unsupported("subtracting from an empty part")
        return new
    if mode is Mode.ADD:
        return _union(node, new)
    if mode is Mode.SUBTRACT:
        return {"type": "subtract", "base": node, "tool": new}
    if mode is Mode.INTERSECT:
        return {"type": "intersect", "children": [node, new]}
    raise Unsupported(f"mode {mode}")


class Shape:
    """A recorded solid: a CSG node usable with build123d's algebra."""

    def __init__(self, node):
        self.node = node

    def __add__(self, other):
        return Shape(_union(self.node, _node_of(other)))

    def __sub__(self, other):
        return Shape({"type": "subtract", "base": self.node, "tool": _node_of(other)})

    def __and__(self, other):
        return Shape({"type": "intersect", "children": [self.node, _node_of(other)]})

    def moved(self, loc):
        return Shape(_transform(self.node, loc.matrix))

    def located(self, loc):
        return self.moved(loc)

    def edges(self):
        return Selection(self, "edges")

    @property
    def part(self):
        return self


Part = Solid = Compound = Shape


def _node_of(value):
    if isinstance(value, Shape):
        return value.node
    if isinstance(value, (list, tuple)) and value:
        node = None
        for item in value:
            node = _union(node, _node_of(item))
        return node
    raise Unsupported(f"expected a shape, got {type(value).__name__}")


def _transform(node, matrix):
    """Pre-multiply every leaf and axis in a subtree by matrix."""
    out = dict(node)
    if "matrix" in node:
        out["matrix"] = _round(_mul(matrix, tuple(node["matrix"])))
    if "axis" in node:
        origin = _apply(matrix, node["axis"]["origin"])
        tip = _apply(matrix, [a + b for a, b in zip(node["axis"]["origin"], node["axis"]["direction"])])
        out["axis"] = {"origin": list(origin), "direction": list(_unit([t - o for t, o in zip(tip, origin)]))}
    for key in ("child", "base", "tool"):
        if key in node:
            out[key] = _transform(node[key], matrix)
    if "children" in node:
        out["children"] = [_transform(c, matrix) for c in node["children"]]
    return out


class Selection(list):
    """part.edges()/faces() with the filters recorded instead of evaluated."""

    def __init__(self, owner, kind, select=None):
        super().__init__()
        self.owner = owner
        self.kind = kind
        self.select = select or {"all": True}

    def filter_by(self, criterion, *args, **kwargs):
        if isinstance(criterion, Axis) and criterion.name and self.select == {"all": True}:
            return Selection(self.owner, self.kind, {"parallel": criterion.name})
        return Selection(self.owner, self.kind, {"custom": f"filter_by({_describe(criterion)})"})

    def _custom(self, label):
        return Selection(self.owner, self.kind, {"custom": label})

    def sort_by(self, *args, **kwargs):
        return self._custom("sort_by")

    def group_by(self, *args, **kwargs):
        return self._custom("group_by")

    def filter_by_position(self, *args, **kwargs):
        return self._custom("filter_by_position")

    def __getitem__(self, index):
        return self._custom(f"[{index}]")

    def edges(self):
        return Selection(self.owner, "edges", self.select)


def _describe(value):
    if isinstance(value, Axis):
        return f"Axis({value.direction})"
    return getattr(value, "name", None) or type(value).__name__


class GeomType(enum.Enum):
    LINE = "line"
    CIRCLE = "circle"
    PLANE = "plane"
    CYLINDER = "cylinder"


class SortBy(enum.Enum):
    LENGTH = "length"
    RADIUS = "radius"
    AREA = "area"
    VOLUME = "volume"
    DISTANCE = "distance"


# -- builders ----------------------------------------------------------------

_STACK: list = []


def _innermost(kind=None):
    for item in reversed(_STACK):
        if kind is None or isinstance(item, kind):
            return item
    return None


def _builder():
    for item in reversed(_STACK):
        if isinstance(item, (BuildPart, BuildSketch)):
            return item
    return None


class Locations:
    def __init__(self, *points):
        self.local = []
        for point in points:
            if isinstance(point, Location):
                self.local.append(point.matrix)
            elif isinstance(point, Plane):
                self.local.append(point.matrix)
            else:
                self.local.append(_translation(*_vec3(point)))
        builder = _builder()
        outer = builder.locations() if builder else [IDENTITY]
        self.matrices = [_mul(o, m) for o in outer for m in self.local]

    def __enter__(self):
        _STACK.append(self)
        return self

    def __exit__(self, *exc):
        _STACK.remove(self)
        return False


def GridLocations(x_spacing, y_spacing, x_count, y_count, align=None):  # noqa: N802
    width, height = (x_count - 1) * x_spacing, (y_count - 1) * y_spacing
    dx, dy = _align_offsets(align, (width, height))
    points = []
    for i in range(int(x_count)):
        for j in range(int(y_count)):
            points.append((i * x_spacing - width / 2 + dx, j * y_spacing - height / 2 + dy))
    return Locations(*points)


def PolarLocations(radius, count, start_angle=0, angular_range=360, rotate=True, endpoint=False):  # noqa: N802
    step = angular_range / (count - int(endpoint)) if count - int(endpoint) else 0
    locs = []
    for i in range(int(count)):
        a = start_angle + i * step
        loc = _translation(radius * math.cos(math.radians(a)), radius * math.sin(math.radians(a)), 0)
        if rotate:
            loc = _mul(loc, _rotation_xyz(0, 0, a))
        locs.append(Location._from(loc))
    return Locations(*locs)


class _Builder:
    def __init__(self, plane):
        if isinstance(plane, Location):
            plane = Plane(plane)
        self.plane = plane or Plane.XY

    def locations(self):
        """Absolute placements of the innermost Locations under this builder."""
        index = _STACK.index(self)
        for item in reversed(_STACK[index + 1:]):
            if isinstance(item, Locations):
                return item.matrices
        return [self.base_matrix()]

    def base_matrix(self):
        return self.plane.matrix

    def __enter__(self):
        _STACK.append(self)
        return self

    def __exit__(self, *exc):
        _STACK.remove(self)
        if exc[0] is None:
            self._finish()
        return False

    def _finish(self):
        pass


class BuildPart(_Builder):
    def __init__(self, *workplanes, mode=Mode.ADD):
        super().__init__(workplanes[0] if workplanes else None)
        self.node = None
        self.pending = []
        self.mode = mode

    def add(self, node, mode):
        self.node = _combine(self.node, node, mode)

    @property
    def part(self):
        if self.node is None:
            raise Unsupported("BuildPart produced no solid")
        return Shape(self.node)

    def edges(self):
        return Selection(self, "edges")

    def faces(self):
        return Selection(self, "faces")

    def _finish(self):
        parent = _innermost(BuildPart)
        if parent is not None and self.node is not None:
            parent.add(self.node, self.mode)


class BuildSketch(_Builder):
    def __init__(self, *workplanes, mode=Mode.ADD):
        plane = workplanes[0] if workplanes else Plane.XY
        super().__init__(plane)
        self.entities = []
        # build123d gives every builder its own workplane list, so a sketch
        # inside BuildPart sits on its own plane: the part's workplane and
        # Locations don't move it.
        self.parent = _innermost(BuildPart)

    def base_matrix(self):
        return IDENTITY  # entity placements are sketch-local

    def _finish(self):
        if self.parent is None:
            return
        self.parent.pending.append({"entities": list(self.entities), "matrix": _round(self.plane.matrix)})

    @property
    def sketch(self):
        return self


def _align_offsets(align, sizes):
    if align is None:
        align = Align.CENTER
    if isinstance(align, Align):
        align = (align,) * len(sizes)
    offsets = []
    for a, size in zip(align, sizes):
        if a is Align.MIN:
            offsets.append(size / 2)
        elif a is Align.MAX:
            offsets.append(-size / 2)
        else:
            offsets.append(0.0)
    return offsets


def _sketch_entity(entity, mode, rotation, offsets):
    sketch = _innermost(BuildSketch)
    if sketch is None:
        raise Unsupported(f"{entity['type']} outside BuildSketch")
    for loc in sketch.locations():
        local = _mul(loc, _mul(_rotation_xyz(0, 0, rotation), _translation(*offsets, 0)))
        placed = dict(entity)
        placed["center"] = [round(local[3], 12), round(local[7], 12)]
        placed["rotation"] = math.degrees(math.atan2(local[4], local[0]))
        placed["mode"] = "subtract" if mode is Mode.SUBTRACT else "add"
        if mode not in (Mode.ADD, Mode.SUBTRACT):
            raise Unsupported(f"sketch mode {mode.name}")
        sketch.entities.append(placed)


def Rectangle(width, height, rotation=0, align=None, mode=Mode.ADD):  # noqa: N802
    _sketch_entity({"type": "rect", "width": float(width), "height": float(height), "cornerRadius": 0.0},
                   mode, rotation, _align_offsets(align, (width, height)))


def RectangleRounded(width, height, radius, rotation=0, align=None, mode=Mode.ADD):  # noqa: N802
    _sketch_entity({"type": "rect", "width": float(width), "height": float(height), "cornerRadius": float(radius)},
                   mode, rotation, _align_offsets(align, (width, height)))


def Circle(radius, align=None, mode=Mode.ADD):  # noqa: N802
    _sketch_entity({"type": "circle", "radius": float(radius)}, mode, 0,
                   _align_offsets(align, (2 * radius, 2 * radius)))


def _solid(leaf, sizes, align, rotation, mode):
    offsets = _align_offsets(align, sizes)
    local = _mul(_rotation_xyz(*rotation), _translation(*offsets))
    part = _innermost(BuildPart)
    if part is None:
        return Shape(dict(leaf, matrix=_round(local)))
    node = None
    for loc in part.locations():
        node = _union(node, dict(leaf, matrix=_round(_mul(loc, local))))
    part.add(node, mode)
    return Shape(node)


def Box(length, width, height, rotation=(0, 0, 0), align=None, mode=Mode.ADD):  # noqa: N802
    return _solid({"type": "box", "size": [float(length), float(width), float(height)]},
                  (length, width, height), align, rotation, mode)


def Cylinder(radius, height, arc_size=360, rotation=(0, 0, 0), align=None, mode=Mode.ADD):  # noqa: N802
    if arc_size != 360:
        raise Unsupported("partial Cylinder arc_size")
    return _solid({"type": "cylinder", "radius": float(radius), "height": float(height)},
                  (2 * radius, 2 * radius, height), align, rotation, mode)


def Cone(bottom_radius, top_radius, height, arc_size=360, rotation=(0, 0, 0), align=None, mode=Mode.ADD):  # noqa: N802
    if arc_size != 360:
        raise Unsupported("partial Cone arc_size")
    r = max(bottom_radius, top_radius)
    return _solid({"type": "cone", "bottomRadius": float(bottom_radius), "topRadius": float(top_radius),
                   "height": float(height)}, (2 * r, 2 * r, height), align, rotation, mode)


def Sphere(radius, arc_size1=-90, arc_size2=90, arc_size3=360, rotation=(0, 0, 0), align=None, mode=Mode.ADD):  # noqa: N802
    if (arc_size1, arc_size2, arc_size3) != (-90, 90, 360):
        raise Unsupported("partial Sphere")
    return _solid({"type": "sphere", "radius": float(radius)}, (2 * radius,) * 3, align, rotation, mode)


def Hole(radius, depth=None, mode=Mode.SUBTRACT):  # noqa: N802
    part = _innermost(BuildPart)
    if part is None:
        raise Unsupported("Hole outside BuildPart")
    node = None
    for loc in part.locations():
        node = _union(node, {"type": "hole", "radius": float(radius),
                             "depth": None if depth is None else float(depth), "matrix": _round(loc)})
    part.add(node, mode)


def _consume_pending(name):
    part = _innermost(BuildPart)
    if part is None:
        raise Unsupported(f"{name} outside BuildPart")
    if not part.pending:
        raise Unsupported(f"{name} with no pending sketch")
    sketches, part.pending = part.pending, []
    return part, sketches


def extrude(to_extrude=None, amount=None, dir=None, until=None, target=None, both=False, taper=0, clean=True,  # noqa: A002
            mode=Mode.ADD):
    if to_extrude is not None or dir is not None or until is not None or taper:
        raise Unsupported("extrude with to_extrude/dir/until/taper")
    if amount is None:
        raise Unsupported("extrude without amount")
    part, sketches = _consume_pending("extrude")
    node = None
    for sk in sketches:
        node = _union(node, {"type": "prism", "profile": {"entities": sk["entities"]}, "amount": float(amount),
                             "both": bool(both), "matrix": sk["matrix"]})
    part.add(node, mode)
    return Shape(node)


def revolve(profiles=None, axis=Axis.Z, revolution_arc=360.0, clean=True, mode=Mode.ADD):
    if profiles is not None:
        raise Unsupported("revolve with explicit profiles")
    part, sketches = _consume_pending("revolve")
    node = None
    for sk in sketches:
        node = _union(node, {"type": "revolve", "profile": {"entities": sk["entities"]}, "axis": axis.to_json(),
                             "angle": float(revolution_arc), "matrix": sk["matrix"]})
    part.add(node, mode)
    return Shape(node)


def _edge_op(kind, objects, size):
    if not isinstance(objects, Selection):
        raise Unsupported(f"{kind} needs part.edges() selections")
    owner = objects.owner
    if isinstance(owner, BuildPart):
        owner.node = {"type": kind, "child": owner.node, "size": float(size), "select": objects.select}
        return owner.part
    return Shape({"type": kind, "child": owner.node, "size": float(size), "select": objects.select})


def fillet(objects, radius):
    return _edge_op("fillet", objects, radius)


def chamfer(objects, length, length2=None, angle=None, reference=None):
    if length2 is not None or angle is not None:
        raise Unsupported("asymmetric chamfer")
    return _edge_op("chamfer", objects, length)


EXPORTS = {
    "Align": Align, "Axis": Axis, "Box": Box, "BuildPart": BuildPart, "BuildSketch": BuildSketch,
    "Circle": Circle, "Compound": Compound, "Cone": Cone, "Cylinder": Cylinder, "GeomType": GeomType,
    "GridLocations": GridLocations, "Hole": Hole, "Location": Location, "Locations": Locations, "Mode": Mode,
    "Part": Part, "Plane": Plane, "PolarLocations": PolarLocations, "Pos": Pos, "Rectangle": Rectangle,
    "RectangleRounded": RectangleRounded, "Rot": Rot, "Solid": Solid, "SortBy": SortBy, "Sphere": Sphere,
    "Vector": Vector, "chamfer": chamfer, "extrude": extrude, "fillet": fillet, "revolve": revolve,
}


def _recording_module():
    module = types.ModuleType("build123d")
    module.__dict__.update(EXPORTS)
    module.__all__ = sorted(EXPORTS)

    def missing(name):
        if name.startswith("__"):
            raise AttributeError(name)
        raise Unsupported(f"build123d.{name} is not modeled by the mesh kernels yet")

    module.__getattr__ = missing
    return module


def trace_model(target: str | Path) -> dict:
    """Run a model (``model.py`` or ``model.py::function``) against the
    recorder and return its program."""
    from cadgen_stub import Unsupported as ModelUnsupported, install, load, shape_of, split_target

    script, function = split_target(str(target))
    saved = {name: sys.modules.get(name) for name in ("build123d", "cadgen", "cadgen.build123d")}
    install(_recording_module())
    _STACK.clear()
    try:
        model = load(script, function)
        result = shape_of(model.call())
        if isinstance(result, (BuildPart, BuildSketch)):
            result = result.part
        return {"ok": True, "model": model.function, "root": _node_of(result)}
    except (Unsupported, ModelUnsupported) as exc:
        return {"ok": False, "error": str(exc), "unsupported": True}
    except NameError as exc:
        return {"ok": False, "error": f"{exc} (not modeled by the recorder yet)", "unsupported": True}
    finally:
        _STACK.clear()
        for name, module in saved.items():
            if module is not None:
                sys.modules[name] = module
            else:
                sys.modules.pop(name, None)
