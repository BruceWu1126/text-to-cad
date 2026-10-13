"""Feature recognition: what a part offers a machinist, read from its B-rep.

Five kinds, each named by the viewer's selector of the face that identifies it:

- ``hole``: concave cylinder faces on one axis line, a whole turn round, joined across air (a cross hole may split
  a wall in two). Size: ``diameter_mm``, ``depth_mm``, ``through``, ``axis``; ``point_mm`` for a drill point (its
  length past the full diameter); ``counterbore`` ``{diameter_mm, depth_mm}`` and ``countersink``
  ``{diameter_mm, angle_deg}`` at an opening. ``depth_mm`` runs from where the hole opens on the part (the top of a
  counterbore or countersink) to the bottom of its full diameter, or right through for a through hole. Its ref is
  the wall; the rest of the wall, the counterbore, the countersink and a blind hole's floor follow in ``refs``.
- ``pocket``: a planar floor whose outer edge is closed by walls rising from it (``length_mm`` and ``width_mm``
  along and across its largest flat wall, ``depth_mm``, ``corner_radius_mm``: its smallest inside corner, 0 for a
  sharp one), and ``islands``: walls standing on the floor inside it, each with its length, width and height.
  Its ref is the floor; the walls and the islands' walls follow.
- ``slot``: a planar floor between two parallel walls facing each other, open at one end or both (width,
  length, depth). Its ref is the floor.
- ``face``: a flat face whose normal is a tool direction, not part of the three above (length along its longest
  straight edge and width, area).
- ``profile``: outside walls along one axis that make a closed ring round it (outline length along its largest
  flat wall and width, height). Profiles are listed along the part's thinnest axis (each axis that ties), where an
  outline is cut; recognizing along one direction (a setup) lists that axis's. Its ref is the largest wall.

``reach`` lists the tool directions that see the feature. It is a ray test with no tool width: every sample point
of a face or a floor (and of a profile's walls, from just outside them) sees out of the part along the direction,
and a hole's opening is clear along its axis at its centre and near its wall. Partly reached features list no
reach. Whether a given tool fits (its diameter, its holder) is the operations' and the checks' business.
Directions are the six axes unless the caller asks for others (a setup that faces a tilted face).

:func:`recognize` returns :class:`Feature` in the document frame; :func:`features_in_frame` gives the ones a setup
reaches in that setup's frame (:class:`cadgen._internal.cam.setups.SetupFrame`) for the operations to plan on.
Stdlib only at import; the geometry is :mod:`cadgen._internal.cam.part`'s.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from cadgen._internal.cam.setups import SetupFrame

__all__ = ["AXES", "KINDS", "Feature", "SetupFeature", "direction_word", "feature_for", "features_in_frame",
           "parse_direction", "recognize"]

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


def _axis_text(axis: Vec) -> str:
    word = _axis_word(axis)
    return word if isinstance(word, str) else direction_word(axis)


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
    #: Its own direction: a face's or a floor's normal, a blind hole's axis out of its opening (a through hole's
    #: points along the axis's positive sense), a profile's axis.
    axis: Vec
    #: The two ends of the feature along ``axis``: ``low`` the bottom (a floor's centroid, a blind hole's bottom
    #: centre, a profile's lowest level), ``high`` where the tool comes in (a hole's opening on the part, above any
    #: counterbore). Equal for a face.
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
    axes: list[Vec] = []
    for direction in directions:
        if not any(parallel(direction, axis) for axis in axes):
            axes.append(direction)
    if len(axes) > 1:  # an outline is cut along the part's thinnest axis
        every = [face.ordinal for face in part.faces]
        height = {axis: (lambda box: box[5] - box[4])(part.extent(every, axis)) for axis in axes}
        axes = [axis for axis in axes if height[axis] <= min(height.values()) + LINEAR_TOL]
    profiles = [feature for axis in axes for feature in _profiles(part, axis, directions, used)]
    found = faces + holes + floors + profiles
    order = sorted(range(len(found)), key=lambda i: (KINDS.index(found[i].kind), _ordinal(found[i].ref), i))
    return tuple(found[i] for i in order)


def _ordinal(ref: str) -> int:
    return int(ref.rsplit(".f", 1)[1])


def _clear_from(part: Part, ordinals, direction: Vec, *, offset: bool = False) -> bool:
    """Every sample point of these faces sees out of the part along ``direction`` (from just off the face when
    ``offset``, for a wall the ray would otherwise run along). A ray, so no tool width: see the module's docstring."""
    for ordinal in ordinals:
        samples = part.samples(ordinal)
        if not samples:
            return False
        for point, normal in samples:
            start = add(point, scale(normal, PROBE_MM)) if offset else point
            if not part.clear(start, direction):
                return False
    return True


def _across(part: Part, ordinals, direction: Vec) -> Vec:
    """The in-plane direction a feature's length is measured along: its largest flat wall's, else the longest straight
    edge of its largest flat face across ``direction``, else any perpendicular. So a part turned on the table
    measures the same."""
    by_size = sorted(ordinals, key=lambda o: (-part.face(o).area, o))
    for ordinal in by_size:
        face = part.face(ordinal)
        if face.surface == "plane" and part.is_wall(ordinal, direction):
            return unit(cross(direction, face.normal))
    for ordinal in by_size:
        face = part.face(ordinal)
        if face.surface == "plane" and parallel(face.normal, direction):
            line = part.longest_line(ordinal)
            if line is not None and abs(dot(line, direction)) < 1e-6:
                return unit(sub(line, scale(direction, dot(line, direction))))
    return perpendicular(direction)


def _footprint(part: Part, ordinals, direction: Vec, across_from=None) -> tuple[float, float]:
    """Length and width of these faces across ``direction``, measured along and across :func:`_across`."""
    across = _across(part, across_from if across_from is not None else ordinals, direction)
    xmin, xmax, ymin, ymax = part.extent(ordinals, direction, across)[:4]
    return xmax - xmin, ymax - ymin


# --- holes ---------------------------------------------------------------------------------------------------

def _on_line(a, b) -> bool:
    """Two cylinders or cones on the same axis line."""
    if not parallel(a.axis, b.axis):
        return False
    offset = sub(b.axis_point, a.axis_point)
    across = sub(offset, scale(a.axis, dot(offset, a.axis)))
    return math.sqrt(dot(across, across)) < LINEAR_TOL


def _canonical(axis: Vec) -> Vec:
    """The axis pointing its largest component positive, so a line has one direction whatever the faces say."""
    biggest = max(range(3), key=lambda i: abs(axis[i]))
    return axis if axis[biggest] > 0 else scale(axis, -1.0)


@dataclass
class _Run:
    """Full-turn cylinder walls on one line and radius, joined end to end or across air. ``lo``/``hi`` are positions
    along ``axis`` (``dot(point, axis)``); ``foot`` is the line's point at position 0."""

    faces: list
    axis: Vec
    foot: Vec
    radius: float
    lo: float
    hi: float
    open_lo: bool = False
    open_hi: bool = False

    def at(self, t: float) -> Vec:
        return add(self.foot, scale(self.axis, t))


def _interval(face, axis: Vec) -> tuple[float, float]:
    base = dot(face.axis_point, axis)
    sense = dot(face.axis, axis)
    ends = (base + face.v_range[0] * sense, base + face.v_range[1] * sense)
    return min(ends), max(ends)


def _holes(part: Part, directions) -> list[Feature]:
    walls = [face for face in part.faces if face.surface == "cylinder" and face.concave]
    lines: list[list] = []
    for face in walls:
        for line in lines:
            if abs(line[0].radius - face.radius) <= LINEAR_TOL and _on_line(line[0], face):
                line.append(face)
                break
        else:
            lines.append([face])
    runs: list[_Run] = []
    for line in lines:
        axis = _canonical(line[0].axis)
        foot = sub(line[0].axis_point, scale(axis, dot(line[0].axis_point, axis)))
        pieces = sorted(((*_interval(face, axis), face) for face in line), key=lambda p: (p[0], p[1], p[2].ordinal))
        current: _Run | None = None
        for lo, hi, face in pieces:
            # Joined when they touch or overlap, or when the gap between them is air (a cross hole through the wall).
            if current is not None and (lo <= current.hi + LINEAR_TOL
                                        or not part.inside(add(foot, scale(axis, (current.hi + lo) / 2)))):
                current.faces.append(face)
                current.hi = max(current.hi, hi)
                continue
            current = _Run([face], axis, foot, line[0].radius, lo, hi)
            runs.append(current)
    holes = []
    for run in runs:
        if sum(face.span for face in run.faces) < _TURN - 1e-6:
            continue  # part of a turn: an inside corner or a slot end, not a hole
        run.open_hi = _opens(part, run, run.hi, run.axis)
        run.open_lo = _opens(part, run, run.lo, scale(run.axis, -1.0))
        if run.open_hi or run.open_lo:  # else a closed void: nothing can reach it
            holes.append(run)
    # A blind run on the line of a smaller one, with its floor at the smaller one's opening, is that hole's counterbore.
    bores: dict[int, tuple[_Run, bool]] = {}
    for hole in holes:
        for bore in holes:
            if bore is hole or bore.radius <= hole.radius + LINEAR_TOL or bore.open_lo == bore.open_hi \
                    or not _on_line(bore.faces[0], hole.faces[0]):
                continue
            if hole.open_hi and not bore.open_lo and abs(bore.lo - hole.hi) < LINEAR_TOL:
                bores.setdefault(id(hole), (bore, True))
            elif hole.open_lo and not bore.open_hi and abs(bore.hi - hole.lo) < LINEAR_TOL:
                bores.setdefault(id(hole), (bore, False))
    taken = {id(bore) for bore, _ in bores.values()}
    return [_hole(part, hole, bores.get(id(hole)), directions) for hole in holes if id(hole) not in taken]


def _probes(run: _Run) -> list[Vec]:
    """Offsets across the axis: the centre and four points near the wall."""
    across = perpendicular(run.axis)
    other = cross(run.axis, across)
    return [(0.0, 0.0, 0.0)] + [add(scale(across, 0.9 * run.radius * math.cos(k * math.pi / 2)),
                                     scale(other, 0.9 * run.radius * math.sin(k * math.pi / 2))) for k in range(4)]


def _opens(part: Part, run: _Run, t: float, out: Vec) -> bool:
    """The run's end at ``t`` opens outward along ``out``: just beyond it the centre and the points near the wall are
    all in air, and a ray from each leaves the part. A counterbore's shoulder holds material; a drill point's cone
    is air beyond the end but a ray meets the cone."""
    beyond = add(run.at(t), scale(out, PROBE_MM))
    points = [add(beyond, offset) for offset in _probes(run)]
    return not any(part.inside(point) for point in points) and all(part.clear(point, out) for point in points)


def _cones(part: Part, faces, run: _Run) -> list:
    """Cone faces on the run's line next to these faces."""
    seen = {face.ordinal for face in faces}
    found = []
    for face in faces:
        for ordinal in part.neighbours(face.ordinal):
            other = part.face(ordinal)
            if ordinal not in seen and other.surface == "cone" and _on_line(other, run.faces[0]):
                seen.add(ordinal)
                found.append(other)
    return found


def _hole(part: Part, run: _Run, bore: tuple[_Run, bool] | None, directions) -> Feature:
    axis = run.axis
    walls = run.faces
    refs = [face.ref for face in walls]
    size: dict = {"diameter_mm": _num(2 * run.radius)}
    extra: list[str] = []
    # Where the hole opens on the part, at each open end: past a counterbore, then past a countersink.
    outer = {True: run.hi, False: run.lo}
    entry_faces = list(walls)
    if bore is not None:
        counterbore, high_side = bore
        refs += [face.ref for face in counterbore.faces]
        entry_faces += counterbore.faces
        for ordinal in sorted({n for face in counterbore.faces for n in part.neighbours(face.ordinal)}):
            shoulder = part.face(ordinal)
            if shoulder.surface == "plane" and parallel(shoulder.normal, axis) and shoulder.ref not in refs and \
                    abs(dot(shoulder.center, axis) - (counterbore.lo if high_side else counterbore.hi)) < LINEAR_TOL:
                refs.append(shoulder.ref)
        outer[high_side] = counterbore.hi if high_side else counterbore.lo
        size["counterbore"] = {"diameter_mm": _num(2 * counterbore.radius), "depth_mm": _num(counterbore.hi - counterbore.lo)}
        extra.append(f"cbore {_mm(2 * counterbore.radius)} x {_mm(counterbore.hi - counterbore.lo)}")
    closed = None if (run.open_hi and run.open_lo) else (run.lo if run.open_hi else run.hi)
    for cone in _cones(part, entry_faces, run):
        low, high = part.extent([cone.ordinal], axis)[4:]
        if closed is not None and (high <= closed + LINEAR_TOL if run.open_hi else low >= closed - LINEAR_TOL):
            refs.append(cone.ref)
            size["point_mm"] = _num(high - low)
            extra.append(f"point {_mm(high - low)} mm")
            continue
        on_high = low >= run.hi - LINEAR_TOL
        if not (run.open_hi if on_high else run.open_lo):
            continue
        refs.append(cone.ref)
        outer[on_high] = max(outer[on_high], high) if on_high else min(outer[on_high], low)
        diameter = _footprint(part, [cone.ordinal], axis)[0]
        angle = math.degrees(2 * cone.half_angle)
        size.setdefault("countersink", {"diameter_mm": _num(diameter), "angle_deg": _num(angle, 2)})
        extra.append(f"csk {_mm(diameter)} x {_mm(angle)}")
    if closed is not None:
        for ordinal in sorted({n for face in walls for n in part.neighbours(face.ordinal)}):
            floor = part.face(ordinal)
            if floor.surface == "plane" and parallel(floor.normal, axis) and floor.ref not in refs and \
                    abs(dot(floor.center, axis) - closed) < LINEAR_TOL:
                refs.append(floor.ref)
    through = closed is None
    if through:
        depth, low, high, toward = outer[True] - outer[False], run.at(outer[False]), run.at(outer[True]), axis
    elif run.open_hi:
        depth, low, high, toward = outer[True] - closed, run.at(closed), run.at(outer[True]), axis
    else:
        depth, low, high, toward = closed - outer[False], run.at(closed), run.at(outer[False]), scale(axis, -1.0)
    reach = tuple(d for d in directions if (run.open_hi and same(d, axis)) or (run.open_lo and same(d, scale(axis, -1.0))))
    size.update({"depth_mm": _num(depth), "through": through, "axis": _axis_word(axis)})
    size = {key: size[key] for key in ("diameter_mm", "depth_mm", "through", "axis", "point_mm", "counterbore",
                                       "countersink") if key in size}
    hint = ", ".join([f"{_mm(2 * run.radius)} mm {'through' if through else 'blind'}", f"{_mm(depth)} mm deep",
                      *extra, f"axis {_axis_text(axis)}", f"at {_at(high, axis)}"])
    return Feature("hole", refs[0], tuple(refs), size, reach, toward, low, high, hint)


# --- pockets and slots ---------------------------------------------------------------------------------------

def _standing(part: Part, floor, seeds, direction: Vec, used: set[int]) -> set[int]:
    """The walls standing on ``floor`` reached from ``seeds`` (faces rising from its edge): they reach down to it, or
    to the top of a round between them and it. A wall whose foot is higher belongs to some other floor (a slot
    crossing a step's wall)."""
    level = dot(floor.center, direction)
    foot = level + LINEAR_TOL + max([part.extent([r], direction)[5] - level for r in seeds
                                     if not part.is_wall(r, direction)], default=0.0)
    walls, queue = set(seeds), list(seeds)
    while queue:
        for other in part.neighbours(queue.pop()):
            if other in walls or other == floor.ordinal or other in used or not part.is_wall(other, direction):
                continue
            if level - LINEAR_TOL <= part.extent([other], direction)[4] <= foot:
                walls.add(other)
                queue.append(other)
    return walls


def _rising(part: Part, level: float, ordinal: int, direction: Vec) -> bool:
    low, high = part.extent([ordinal], direction)[4:]
    return low >= level - LINEAR_TOL and high > level + LINEAR_TOL


def _islands(part: Part, floor, direction: Vec, used: set[int]) -> list[list[int]]:
    """The groups of walls standing on the floor's inner edges, one per island."""
    level = dot(floor.center, direction)
    inner = floor.edges - floor.outer_edges
    seeds = [o for o in part.neighbours(floor.ordinal, inner) if o not in used and _rising(part, level, o, direction)]
    walls = _standing(part, floor, seeds, direction, used) if seeds else set()
    groups, remaining = [], set(walls)
    for start in sorted(walls):
        if start not in remaining:
            continue
        group, queue = [start], [start]
        remaining.discard(start)
        while queue:
            for other in part.neighbours(queue.pop()):
                if other in remaining:
                    remaining.discard(other)
                    group.append(other)
                    queue.append(other)
        groups.append(sorted(group))
    return groups


def _floors(part: Part, direction: Vec, used: set[int], kind: str) -> list[Feature]:
    """The pockets (``kind`` "pocket") or the slots whose floors face ``direction``."""
    found = []
    for floor in part.faces:
        if floor.surface != "plane" or floor.ordinal in used or not same(floor.normal, direction):
            continue
        level = dot(floor.center, direction)
        rising, open_ = [], False
        for other in part.neighbours(floor.ordinal, floor.outer_edges):
            if _rising(part, level, other, direction):
                rising.append(other)
            else:
                open_ = True
        if not rising or open_ != (kind == "slot"):
            continue
        walls = sorted(_standing(part, floor, rising, direction, used))
        depth = max(part.extent([w], direction)[5] for w in walls) - level
        reach = (direction,) if _clear_from(part, [floor.ordinal], direction) else ()
        refs = (floor.ref,) + tuple(part.face(w).ref for w in walls)
        top = add(floor.center, scale(direction, depth))
        corner = _inside_corner(part, walls, direction)
        if not open_:
            length, width = sorted(_footprint(part, [floor.ordinal, *walls], direction, walls), reverse=True)
            islands = _islands(part, floor, direction, used | set(walls))
            size = {"length_mm": _num(length), "width_mm": _num(width), "depth_mm": _num(depth),
                    "corner_radius_mm": None if corner is None else _num(corner), "islands": []}
            for island in islands:
                island_length, island_width = sorted(_footprint(part, island, direction), reverse=True)
                height = max(part.extent([w], direction)[5] for w in island) - level
                size["islands"].append({"length_mm": _num(island_length), "width_mm": _num(island_width),
                                        "height_mm": _num(height)})
                refs += tuple(part.face(w).ref for w in island)
            corners = ("sharp inside corners" if corner == 0 else f"R{_mm(corner)} inside corners") if corner is not None else ""
            count = len(islands)
            hint = ", ".join(filter(None, (f"{_mm(length)} x {_mm(width)} mm", f"{_mm(depth)} mm deep", corners,
                                           f"{count} island{'s' if count > 1 else ''}" if count else "",
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
        length, width = sorted(_footprint(part, [face.ordinal], direction), reverse=True)
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
        across = _across(part, members, axis)
        xmin, xmax, ymin, ymax, zmin, zmax = part.extent(members, axis, across)
        if min(xmax - xmin, ymax - ymin) <= 10 * LINEAR_TOL:
            continue  # no ring round the axis: a single flat (an island's top seen edge on), not an outline
        side = cross(axis, across)
        middle = add(scale(across, (xmin + xmax) / 2), scale(side, (ymin + ymax) / 2))
        low, high = add(middle, scale(axis, zmin)), add(middle, scale(axis, zmax))
        length, width = sorted((xmax - xmin, ymax - ymin), reverse=True)
        reach = tuple(d for d in directions if parallel(d, axis) and _clear_from(part, members, d, offset=True))
        size = {"length_mm": _num(length), "width_mm": _num(width), "height_mm": _num(zmax - zmin),
                "axis": _axis_word(axis)}
        hint = f"outline {_mm(length)} x {_mm(width)} mm, {_mm(zmax - zmin)} mm tall along {_axis_text(axis)}"
        refs = tuple(part.face(o).ref for o in members)
        found.append(Feature("profile", refs[0], refs, size, reach, axis, low, high, hint))
    return found


# --- setups --------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SetupFeature:
    """A feature as one setup sees it, in the setup's frame: ``top`` where the tool enters it, ``bottom`` the
    deepest point it machines to (the same point for a face)."""

    feature: Feature
    top: Vec
    bottom: Vec

    @property
    def depth(self) -> float:
        return self.top[2] - self.bottom[2]


def features_in_frame(part: Part, setup: "SetupFrame", features=None) -> tuple[SetupFeature, ...]:
    """The features a setup's tool reaches (along ``setup.direction``), in the setup's frame. ``features`` defaults to
    recognizing the part along that one direction."""
    direction = tuple(float(c) for c in setup.direction)
    features = recognize(part, (direction,)) if features is None else features
    found = []
    for feature in features:
        if not any(same(d, direction) for d in feature.reach):
            continue
        a, b = (tuple(float(c) for c in point) for point in setup.frame.point_to_setup([feature.low, feature.high]))
        top, bottom = (a, b) if a[2] >= b[2] else (b, a)
        found.append(SetupFeature(feature, top, bottom))
    return tuple(found)


def feature_for(features, ref: str, kinds=KINDS) -> Feature | None:
    """The feature a selector names: the one it identifies, else the first of ``kinds`` it is part of."""
    candidates = [f for f in features if f.kind in kinds and ref in f.refs]
    named = [f for f in candidates if f.ref == ref]
    return (named or candidates or [None])[0]
