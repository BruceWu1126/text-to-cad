"""Feature recognition: what a part offers a machinist, read from its B-rep.

Five kinds, each named by the viewer's selector of the face that identifies it:

- ``hole``: one or more concave cylinder faces on one axis, a whole turn round (diameter, depth, through or blind,
  axis). Its ref is the wall; a blind hole's floor (flat or a drill point) follows in ``refs``.
- ``pocket``: a planar floor whose outer edge is closed by walls rising from it (length and width across, depth,
  smallest inside corner radius: 0 for a sharp corner). Its ref is the floor.
- ``slot``: a planar floor between two parallel walls facing each other, open at one end or both (width,
  length, depth). Its ref is the floor.
- ``face``: a flat face whose normal is a tool direction, not part of the three above (length and width, area).
- ``profile``: the outside walls along one tool axis, joined edge to edge (outline length and width, height).
  Its ref is the largest of them.

``reach`` lists the tool directions that see the feature: for a face, a floor or a hole's opening, a ray from it
along the direction leaves the part without touching it; for a profile, the same from just outside every wall.
Directions are the six axes unless the caller asks for others (a setup that faces a tilted face).

:func:`recognize` returns :class:`Feature` in the document frame; :func:`features_in_frame` re-expresses the ones a
setup reaches in that setup's frame (z is the tool axis, the tool coming from +z) for the operations to plan on.
Stdlib only at import; the geometry is :mod:`cadgen._internal.cam.part`'s.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from cadgen._internal.cam.part import (
    LINEAR_TOL,
    PROBE_MM,
    Part,
    Vec,
    add,
    cross,
    dot,
    parallel,
    perpendicular,
    same,
    scale,
    sub,
    unit,
)
from cadgen._internal.cam.values import json_text, words
from cadgen.results import CamFeature

__all__ = ["AXES", "KINDS", "Feature", "Frame", "SetupFeature", "direction_word", "feature_for", "features_in_frame",
           "frame", "parse_direction", "recognize"]

#: The six axis tool directions, by the word a job and the CLI write.
AXES: dict[str, Vec] = {
    "+X": (1.0, 0.0, 0.0), "-X": (-1.0, 0.0, 0.0),
    "+Y": (0.0, 1.0, 0.0), "-Y": (0.0, -1.0, 0.0),
    "+Z": (0.0, 0.0, 1.0), "-Z": (0.0, 0.0, -1.0),
}
#: Feature kinds in listing order.
KINDS = ("face", "hole", "pocket", "slot", "profile")

_TURN = 2 * math.pi


def parse_direction(value, *, where: str = "direction") -> Vec:
    """One of the six axis words (``+Z``) as a unit vector; anything else is a ValueError naming the words."""
    if not isinstance(value, str) or value not in AXES:
        raise ValueError(f"{where}: {json_text(value)} is not a tool direction; use {words(AXES)}")
    return AXES[value]


def direction_word(direction: Vec) -> str:
    """``+Z`` for an axis direction, else the vector to two places."""
    for word, axis in AXES.items():
        if same(axis, direction):
            return word
    return "(" + ", ".join(f"{_num(c, 2):g}" for c in direction) + ")"


def _axis_word(axis: Vec) -> str | list[float]:
    """``Z`` for a line along an axis (either way round), else the vector."""
    for name, vector in (("X", AXES["+X"]), ("Y", AXES["+Y"]), ("Z", AXES["+Z"])):
        if parallel(vector, axis):
            return name
    return [_num(c, 4) for c in axis]


def _num(value: float, places: int = 4) -> float:
    rounded = round(float(value), places)
    return 0.0 if rounded == 0 else rounded


def _mm(value: float) -> str:
    return f"{_num(value, 3):g}"


def _at(point: Vec, axis: Vec) -> str:
    """Where a feature sits, as a machinist reads it off a drawing: the two coordinates across an axis it runs along."""
    for name, vector in (("X", AXES["+X"]), ("Y", AXES["+Y"]), ("Z", AXES["+Z"])):
        if parallel(vector, axis):
            return " ".join(f"{other} {_mm(point['XYZ'.index(other)])}" for other in "XYZ" if other != name)
    return "(" + ", ".join(_mm(c) for c in point) + ")"


def _level(point: Vec, axis: Vec) -> str:
    """The coordinate along an axis line: ``Z 10``."""
    for name, vector in (("X", AXES["+X"]), ("Y", AXES["+Y"]), ("Z", AXES["+Z"])):
        if parallel(vector, axis):
            return f"{name} {_mm(point['XYZ'.index(name)])}"
    return "(" + ", ".join(_mm(c) for c in point) + ")"


@dataclass(frozen=True)
class Feature:
    """One recognized feature, in the document frame (mm)."""

    kind: str
    ref: str
    refs: tuple[str, ...]
    size: dict = field(hash=False)
    #: The tool directions that reach it (unit vectors, from the directions recognition was asked about).
    reach: tuple[Vec, ...]
    #: Its own direction: a face's or a floor's normal, a hole's axis out of its opening (a through hole's points
    #: along the axis's positive sense), a profile's axis.
    axis: Vec
    #: The two ends of the feature along ``axis``: ``low`` the bottom (a floor's centroid, a hole's bottom centre,
    #: a profile's lowest level), ``high`` the opening. Equal for a face.
    low: Vec
    high: Vec
    hint: str = ""

    def public(self) -> CamFeature:
        return CamFeature(ref=self.ref, kind=self.kind, refs=self.refs, size=dict(self.size),
                          reach=tuple(direction_word(d) for d in self.reach), hint=self.hint)


def recognize(part: Part, directions=None) -> tuple[Feature, ...]:
    """Every feature of ``part`` along ``directions`` (unit vectors; the six axes when omitted), in listing order:
    by kind, then by the ordinal of the identifying face."""
    directions = tuple(AXES.values()) if directions is None else tuple(unit(d) for d in directions)
    holes = _holes(part, directions)
    used = {_ordinal(ref) for hole in holes for ref in hole.refs}
    floors: list[Feature] = []
    for kind in ("pocket", "slot"):  # closed pockets claim their walls first: seen from the side, a wall is no slot
        found = [feature for direction in directions for feature in _floors(part, direction, used, kind)]
        used |= {_ordinal(ref) for feature in found for ref in feature.refs}
        floors += found
    faces = [feature for direction in directions for feature in _faces(part, direction, used)]
    profiles: list[Feature] = []
    axes: list[Vec] = []
    for direction in directions:
        if not any(parallel(direction, axis) for axis in axes):
            axes.append(direction)
            profiles += _profiles(part, direction, directions, used)
    found = faces + holes + floors + profiles
    order = sorted(range(len(found)), key=lambda i: (KINDS.index(found[i].kind), _ordinal(found[i].ref), i))
    return tuple(found[i] for i in order)


def _ordinal(ref: str) -> int:
    return int(ref.rsplit(".f", 1)[1])


def _clear_from(part: Part, ordinals, direction: Vec, *, offset: bool = False) -> bool:
    """Every sample point of these faces sees out of the part along ``direction`` (from just off the face when
    ``offset``, for a wall the ray would otherwise run along)."""
    for ordinal in ordinals:
        samples = part.samples(ordinal)
        if not samples:
            return False
        for point, normal in samples:
            start = add(point, scale(normal, PROBE_MM)) if offset else point
            if not part.clear(start, direction):
                return False
    return True


# --- holes ---------------------------------------------------------------------------------------------------

def _coaxial(a, b) -> bool:
    if not parallel(a.axis, b.axis) or abs(a.radius - b.radius) > LINEAR_TOL:
        return False
    offset = sub(b.axis_point, a.axis_point)
    across = sub(offset, scale(a.axis, dot(offset, a.axis)))
    return math.sqrt(dot(across, across)) < LINEAR_TOL


def _canonical(axis: Vec) -> Vec:
    """The axis pointing its largest component positive, so a line has one direction whatever the faces say."""
    biggest = max(range(3), key=lambda i: abs(axis[i]))
    return axis if axis[biggest] > 0 else scale(axis, -1.0)


def _holes(part: Part, directions) -> list[Feature]:
    walls = [face for face in part.faces if face.surface == "cylinder" and face.concave]
    group_of = {face.ordinal: {face.ordinal} for face in walls}
    by_ordinal = {face.ordinal: face for face in walls}
    for face in walls:
        for other in part.neighbours(face.ordinal):
            if other in by_ordinal and group_of[other] is not group_of[face.ordinal] and _coaxial(face, by_ordinal[other]):
                merged = group_of[face.ordinal] | group_of[other]
                for member in merged:
                    group_of[member] = merged
    groups = sorted({tuple(sorted(group)) for group in group_of.values()})
    holes = []
    for group in groups:
        faces = [by_ordinal[o] for o in group]
        if sum(face.span for face in faces) < _TURN - 1e-6:
            continue  # part of a turn: an inside corner or a slot end, not a hole
        hole = _hole(part, faces, directions)
        if hole is not None:
            holes.append(hole)
    return holes


def _hole(part: Part, faces, directions) -> Feature | None:
    first = faces[0]
    axis, origin, radius = _canonical(first.axis), first.axis_point, first.radius
    ts = []
    for face in faces:
        base = dot(sub(face.axis_point, origin), axis)
        sense = dot(face.axis, axis)
        ts += [base + face.v_range[0] * sense, base + face.v_range[1] * sense]
    lo, hi = min(ts), max(ts)
    low, high = add(origin, scale(axis, lo)), add(origin, scale(axis, hi))
    across = perpendicular(axis)
    other = cross(axis, across)
    # The centre and four points near the wall: an end is open only where all of them are in air beyond it, so a
    # counterbore, whose end opens on a smaller hole, is blind on its shoulder.
    rim = [(0.0, 0.0)] + [(0.9 * radius * math.cos(k * math.pi / 2), 0.9 * radius * math.sin(k * math.pi / 2)) for k in range(4)]

    def opens(end: Vec, out: Vec) -> bool:
        beyond = add(end, scale(out, PROBE_MM))
        return not any(part.inside(add(beyond, add(scale(across, a), scale(other, b)))) for a, b in rim)

    open_high, open_low = opens(high, axis), opens(low, scale(axis, -1.0))
    if not (open_high or open_low):
        return None  # a closed void: nothing can reach it
    reach = []
    for end, out, is_open in ((high, axis, open_high), (low, scale(axis, -1.0), open_low)):
        if not is_open:
            continue
        start = add(end, scale(out, PROBE_MM))
        if all(part.clear(add(start, add(scale(across, a), scale(other, b))), out) for a, b in rim):
            reach += [d for d in directions if same(d, out)]
    through = open_high and open_low
    refs = [face.ref for face in faces]
    if not through:
        closed_t = lo if open_high else hi
        for face_ordinal in part.neighbours(first.ordinal) + [n for f in faces[1:] for n in part.neighbours(f.ordinal)]:
            cap = part.face(face_ordinal)
            if cap.ref in refs:
                continue
            at_end = cap.surface == "plane" and parallel(cap.normal, axis) and \
                abs(dot(sub(cap.center, origin), axis) - closed_t) < LINEAR_TOL
            # A drill point lies beyond the closed end; a countersink at the opening is not the floor.
            beyond = (dot(sub(cap.center, origin), axis) - closed_t) * (-1.0 if open_high else 1.0)
            point = cap.surface == "cone" and parallel(cap.axis, axis) and beyond > -LINEAR_TOL
            if at_end or point:
                refs.append(cap.ref)
        if open_low:  # the opening is at the low end: point the hole out of it
            axis, low, high = scale(axis, -1.0), high, low
    depth = hi - lo
    size = {"diameter_mm": _num(2 * radius), "depth_mm": _num(depth), "through": through, "axis": _axis_word(axis)}
    word = _axis_word(axis)
    hint = (f"{_mm(2 * radius)} mm {'through' if through else 'blind'}, {_mm(depth)} mm deep, "
            f"axis {word if isinstance(word, str) else direction_word(axis)}, at {_at(high, axis)}")
    return Feature("hole", refs[0], tuple(refs), size, tuple(reach), axis, low, high, hint)


# --- pockets and slots ---------------------------------------------------------------------------------------

def _floors(part: Part, direction: Vec, used: set[int], kind: str) -> list[Feature]:
    """The pockets (``kind`` "pocket") or the slots whose floors face ``direction``."""
    found = []
    for floor in part.faces:
        if floor.surface != "plane" or floor.ordinal in used or not same(floor.normal, direction):
            continue
        level = dot(floor.center, direction)
        rising, open_ = [], False
        for other in part.neighbours(floor.ordinal, floor.outer_edges):
            low, high = part.extent([other], direction)[4:]
            if low >= level - LINEAR_TOL and high > level + LINEAR_TOL:
                rising.append(other)
            else:
                open_ = True
        if not rising or open_ != (kind == "slot"):
            continue
        # The walls stand on this floor: they reach down to it, or to the top of a round between them and it. A
        # wall whose foot is higher belongs to some other floor (a slot crossing a step's wall).
        foot = level + LINEAR_TOL + max([part.extent([r], direction)[5] - level for r in rising
                                         if not part.is_wall(r, direction)], default=0.0)
        walls, queue = set(rising), list(rising)
        while queue:
            for other in part.neighbours(queue.pop()):
                if other in walls or other == floor.ordinal or other in used or not part.is_wall(other, direction):
                    continue
                if level - LINEAR_TOL <= part.extent([other], direction)[4] <= foot:
                    walls.add(other)
                    queue.append(other)
        walls = sorted(walls)
        depth = max(part.extent([w], direction)[5] for w in walls) - level
        reach = (direction,) if _clear_from(part, [floor.ordinal], direction) else ()
        refs = (floor.ref,) + tuple(part.face(w).ref for w in walls)
        top = add(floor.center, scale(direction, depth))
        corner = _inside_corner(part, walls, direction)
        if not open_:
            xmin, xmax, ymin, ymax = part.extent([floor.ordinal, *walls], direction)[:4]
            length, width = sorted((xmax - xmin, ymax - ymin), reverse=True)
            size = {"length_mm": _num(length), "width_mm": _num(width), "depth_mm": _num(depth),
                    "corner_radius_mm": None if corner is None else _num(corner)}
            corners = ("sharp inside corners" if corner == 0 else f"R{_mm(corner)} inside corners") if corner is not None else ""
            hint = ", ".join(filter(None, (f"{_mm(length)} x {_mm(width)} mm", f"{_mm(depth)} mm deep", corners,
                                           f"floor at {_level(floor.center, direction)}")))
            found.append(Feature("pocket", floor.ref, refs, size, reach, direction, floor.center, top, hint))
            continue
        pair = _facing_walls(part, walls)
        if pair is None:
            continue  # a step: one wall, or walls that do not face: the floor is listed as a face
        first, second = (part.face(o) for o in pair)
        width = abs(dot(sub(first.center, second.center), first.normal))
        along = unit(cross(first.normal, direction))
        xmin, xmax = part.extent([floor.ordinal, *walls], direction, along)[:2]
        size = {"width_mm": _num(width), "length_mm": _num(xmax - xmin), "depth_mm": _num(depth)}
        if corner is not None:
            size["corner_radius_mm"] = _num(corner)
        hint = (f"{_mm(width)} mm wide, {_mm(xmax - xmin)} mm long, {_mm(depth)} mm deep, "
                f"floor at {_level(floor.center, direction)}")
        found.append(Feature("slot", floor.ref, refs, size, reach, direction, floor.center, top, hint))
    return found


def _air_side(part: Part, a: int, b: int) -> bool:
    """Plane face ``a``'s centre lies on the air side of plane face ``b``."""
    return dot(sub(part.face(a).center, part.face(b).center), part.face(b).normal) > LINEAR_TOL


def _inside_corner(part: Part, walls, direction: Vec) -> float | None:
    """The smallest inside corner round the walls: a concave cylinder's radius, 0 where two flat walls meet in a
    sharp inside corner, ``None`` when the walls make no inside corner."""
    radii = [part.face(w).radius for w in walls
             if part.face(w).surface == "cylinder" and part.face(w).concave and parallel(part.face(w).axis, direction)]
    planes = {w for w in walls if part.face(w).surface == "plane"}
    for wall in planes:
        for other in part.neighbours(wall):
            if other in planes and _air_side(part, wall, other) and _air_side(part, other, wall):
                return 0.0
    return min(radii) if radii else None


def _facing_walls(part: Part, walls) -> tuple[int, int] | None:
    """The two largest flat walls that face each other across the floor, if any."""
    planes = sorted((w for w in walls if part.face(w).surface == "plane"), key=lambda w: (-part.face(w).area, w))
    for i, a in enumerate(planes):
        for b in planes[i + 1:]:
            if same(part.face(a).normal, scale(part.face(b).normal, -1.0)) and _air_side(part, a, b) and _air_side(part, b, a):
                return (a, b)
    return None


# --- faces and profiles --------------------------------------------------------------------------------------

def _faces(part: Part, direction: Vec, used: set[int]) -> list[Feature]:
    found = []
    for face in part.faces:
        if face.surface != "plane" or face.ordinal in used or not same(face.normal, direction):
            continue
        xmin, xmax, ymin, ymax = part.extent([face.ordinal], direction)[:4]
        length, width = sorted((xmax - xmin, ymax - ymin), reverse=True)
        reach = (direction,) if _clear_from(part, [face.ordinal], direction) else ()
        size = {"length_mm": _num(length), "width_mm": _num(width), "area_mm2": _num(face.area, 2)}
        hint = f"flat {_mm(length)} x {_mm(width)} mm at {_level(face.center, direction)}"
        found.append(Feature("face", face.ref, (face.ref,), size, reach, direction, face.center, face.center, hint))
    return found


def _profiles(part: Part, axis: Vec, directions, used: set[int]) -> list[Feature]:
    outside = [face.ordinal for face in part.faces
               if face.ordinal not in used and part.is_wall(face.ordinal, axis)
               and any(part.clear(add(p, scale(n, PROBE_MM)), n) for p, n in part.samples(face.ordinal))]
    remaining, found = set(outside), []
    for start in outside:
        if start not in remaining:
            continue
        component, queue = {start}, [start]
        remaining.discard(start)
        while queue:
            for other in part.neighbours(queue.pop()):
                if other in remaining:
                    remaining.discard(other)
                    component.add(other)
                    queue.append(other)
        members = sorted(component, key=lambda o: (-part.face(o).area, o))
        across = perpendicular(axis)
        xmin, xmax, ymin, ymax, zmin, zmax = part.extent(members, axis, across)
        side = cross(axis, across)
        middle = add(scale(across, (xmin + xmax) / 2), scale(side, (ymin + ymax) / 2))
        low, high = add(middle, scale(axis, zmin)), add(middle, scale(axis, zmax))
        length, width = sorted((xmax - xmin, ymax - ymin), reverse=True)
        reach = tuple(d for d in directions if parallel(d, axis) and _clear_from(part, members, d, offset=True))
        word = _axis_word(axis)
        size = {"length_mm": _num(length), "width_mm": _num(width), "height_mm": _num(zmax - zmin), "axis": word}
        hint = f"outline {_mm(length)} x {_mm(width)} mm, {_mm(zmax - zmin)} mm tall along {word if isinstance(word, str) else direction_word(axis)}"
        refs = tuple(part.face(o).ref for o in members)
        found.append(Feature("profile", refs[0], refs, size, reach, axis, low, high, hint))
    return found


# --- setup frames --------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Frame:
    """A right-handed frame in the document: ``z`` is the tool axis (the tool comes from +z), ``origin`` the work
    zero. ``point`` and ``vector`` re-express document coordinates in it."""

    origin: Vec = (0.0, 0.0, 0.0)
    x: Vec = (1.0, 0.0, 0.0)
    y: Vec = (0.0, 1.0, 0.0)
    z: Vec = (0.0, 0.0, 1.0)

    def point(self, p: Vec) -> Vec:
        return self.vector(sub(p, self.origin))

    def vector(self, v: Vec) -> Vec:
        return (dot(v, self.x), dot(v, self.y), dot(v, self.z))


def frame(direction: Vec, origin: Vec = (0.0, 0.0, 0.0), x_axis: Vec | None = None) -> Frame:
    """The frame whose z is ``direction``, with x along ``x_axis`` made square to it (world X, or world Y for an X
    direction, when omitted)."""
    z = unit(direction)
    x = perpendicular(z) if x_axis is None else unit(sub(x_axis, scale(z, dot(x_axis, z))))
    return Frame(tuple(float(c) for c in origin), x, cross(z, x), z)


@dataclass(frozen=True)
class SetupFeature:
    """A feature as one setup sees it, in that setup's frame: ``top`` where the tool enters it, ``bottom`` the
    deepest point it machines to (the same point for a face)."""

    feature: Feature
    top: Vec
    bottom: Vec

    @property
    def depth(self) -> float:
        return self.top[2] - self.bottom[2]


def features_in_frame(part: Part, setup: Frame, features=None) -> tuple[SetupFeature, ...]:
    """The features a tool along ``setup.z`` reaches, in the setup's frame. ``features`` defaults to recognizing
    the part along that one direction."""
    features = recognize(part, (setup.z,)) if features is None else features
    found = []
    for feature in features:
        if not any(same(d, setup.z) for d in feature.reach):
            continue
        a, b = setup.point(feature.low), setup.point(feature.high)
        top, bottom = (a, b) if a[2] >= b[2] else (b, a)
        found.append(SetupFeature(feature, top, bottom))
    return tuple(found)


def feature_for(features, ref: str, kinds=KINDS) -> Feature | None:
    """The feature a selector names: the one it identifies, else the first of ``kinds`` it is part of."""
    candidates = [f for f in features if f.kind in kinds and ref in f.refs]
    named = [f for f in candidates if f.ref == ref]
    return (named or candidates or [None])[0]
