"""Whether a written STEP holds the result it was written from: the check an export is
verified by.

:func:`verify_step` parses the file as any reader does (``load_step_scene``) and walks it
as a compile walks it (``store.build._document_walk``): the occurrences that walk names, by
the tokens it mints from the file's names and placements, must be the result's, each
holding the part the result places there and drawing each face in the result's colour. It
finds each component the file holds by the part key on its product (``step_names``) and
compares it with the component the result holds, given as an :class:`ExpectedComponent`: as
many faces and edges, each named as the result names it and where the result has it.

An element is where the result has it when every bound of its tight box -- the one a part's
selector table records per face and per edge (``topology_extract``) -- is within its bound
of its counterpart's: the display chord tolerance (``tessellation_policy``) of the larger
of the two components' bounding diagonals, plus the element's own tolerance in the result
(the largest of its own and its edges' and vertices'), within which the kernel takes
geometry as coincident, so a reader that heals or re-approximates within it has not
changed the shape. Elements are paired by name; those no name pairs are paired by place,
in order, else as a set, since a reader need not list them in the order they were written.

What is compared of a colour is the colour each face is drawn in -- its own where its part
gives it one, else its occurrence's -- to the single precision a STEP reader keeps, never
how the file states it: OCCT writes an occurrence's colour onto the faces of a compound
part that have none of their own, which draw alike.

What no file can name the result alone says (``step_names.unnameable``), and an expected
component lists those edges: a reader makes them again (a pole, a whole sphere's seam) or
holds them in a curve set, so they carry no name. An edge a face bounds has no place of its
own to check, and its face is checked instead; one no face bounds (a free wire's) answers
for its body alone, by its place. Every other name must be in the file.

A file written without names (``names=False``: it claims no part and no element) is
verified by its occurrences alone: each holds a component with the faces and edges of the
part the result places there, paired by place, drawn in the result's colours.

Nothing here reads the store: the file and what the caller passes are the whole input, and
the answer is the list of what differs, empty for a file that verifies.
"""

from __future__ import annotations

import bisect
import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from cadgen._internal.step_names import ComponentNames, is_part_key

Box = tuple[float, float, float, float, float, float]
RGBA = tuple[float, float, float, float]
# Never tighter than the kernel's own confusion: below it two points are one point.
_FLOOR = 1e-7
# A STEP reader keeps a colour's channels to single precision.
_COLOR_TOLERANCE = 1e-6
_SHOWN = 5


@dataclass(frozen=True)
class ExpectedComponent:
    """One component of the result: what it is named by (``names.part``, its part key, is
    how the file's component is found); the tight box of each of its faces and edges by the
    ordinals its names count by (None for one that bounds nothing, as a degenerate edge);
    its own face colours by face ordinal (``faceColors``); each face's and edge's own
    tolerance (none given, none allowed); the ordinals of the edges no file can name
    (``step_names.unnameable``) and of those no face bounds. :func:`expected_component`
    measures all of it from the component's shape."""

    names: ComponentNames
    face_boxes: tuple[Box | None, ...]
    edge_boxes: tuple[Box | None, ...]
    face_colors: Mapping[int, RGBA] = field(default_factory=dict)
    face_tolerances: tuple[float, ...] = ()
    edge_tolerances: tuple[float, ...] = ()
    unnameable_edges: frozenset[int] = frozenset()
    faceless_edges: frozenset[int] = frozenset()


@dataclass(frozen=True)
class Mismatch:
    """One way the file differs from the result: the occurrence it concerns, by its token
    (None where it concerns a part wherever it is placed), the part (None for a component
    the file holds with no part key) and what differs."""

    occurrence: str | None
    part: str | None
    message: str


def expected_component(shape: Any, names: ComponentNames, face_colors: Mapping[Any, Any] | None = None
                       ) -> ExpectedComponent:
    """The component the unlocated ``shape`` (a stored component, decoded) is, named
    ``names`` and with face colours ``face_colors`` (``faceColors``, by face ordinal):
    every face's and edge's tight box and own tolerance, measured as a part's selector table
    measures a box, and the edges no file can name."""
    from cadgen._internal.step_names import unnameable

    face_tolerances, edge_tolerances = element_tolerances(shape)
    _faces, edges = unnameable(shape)
    return ExpectedComponent(names=names, face_boxes=face_boxes(shape), edge_boxes=edge_boxes(shape),
                             face_colors=dict(face_colors or {}), face_tolerances=face_tolerances,
                             edge_tolerances=edge_tolerances, unnameable_edges=edges,
                             faceless_edges=faceless_edges(shape))


def faceless_edges(shape: Any) -> frozenset[int]:
    """The ordinals of the edges no face of the unlocated component ``shape`` bounds, in
    ``TopExp.MapShapes_s`` order: a free wire's, a free edge's."""
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp, TopExp_Explorer
    from OCP.TopTools import TopTools_IndexedMapOfShape

    edges = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, edges)
    bounding: set[int] = set()
    faces = TopExp_Explorer(shape, TopAbs_FACE)
    while faces.More():
        explorer = TopExp_Explorer(faces.Current(), TopAbs_EDGE)
        while explorer.More():
            bounding.add(edges.FindIndex(explorer.Current()))
            explorer.Next()
        faces.Next()
    return frozenset(set(range(1, edges.Extent() + 1)) - bounding)


def face_boxes(shape: Any) -> tuple[Box | None, ...]:
    """Each face's tight box in the unlocated component ``shape``, in ``TopExp.MapShapes_s``
    order, measured as a part's selector table measures it."""
    from OCP.TopAbs import TopAbs_FACE

    return _boxes(shape, TopAbs_FACE)


def edge_boxes(shape: Any) -> tuple[Box | None, ...]:
    """Each edge's tight box in the unlocated component ``shape``, in ``TopExp.MapShapes_s``
    order, None for one that bounds nothing, measured as a part's selector table measures it."""
    from OCP.TopAbs import TopAbs_EDGE

    return _boxes(shape, TopAbs_EDGE)


def element_tolerances(shape: Any) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Each face's and each edge's own tolerance in the unlocated component ``shape``, in
    ``TopExp.MapShapes_s`` order: the largest of the element's BREP tolerance and its edges'
    and vertices'."""
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_VERTEX
    from OCP.TopExp import TopExp, TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_IndexedMapOfShape

    def vertices(element: Any) -> float:
        largest = 0.0
        explorer = TopExp_Explorer(element, TopAbs_VERTEX)
        while explorer.More():
            largest = max(largest, BRep_Tool.Tolerance_s(TopoDS.Vertex_s(explorer.Current())))
            explorer.Next()
        return largest

    edges = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, edges)
    edge_tolerances = tuple(max(BRep_Tool.Tolerance_s(TopoDS.Edge_s(edges.FindKey(ordinal))),
                                vertices(edges.FindKey(ordinal))) for ordinal in range(1, edges.Extent() + 1))
    faces = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_FACE, faces)
    face_tolerances = []
    for ordinal in range(1, faces.Extent() + 1):
        face = faces.FindKey(ordinal)
        largest = max(BRep_Tool.Tolerance_s(TopoDS.Face_s(face)), vertices(face))
        explorer = TopExp_Explorer(face, TopAbs_EDGE)
        while explorer.More():
            index = edges.FindIndex(explorer.Current())
            if index:
                largest = max(largest, edge_tolerances[index - 1])
            explorer.Next()
        face_tolerances.append(largest)
    return tuple(face_tolerances), edge_tolerances


def verify_step(path: Path, expected: Iterable[ExpectedComponent], occurrences: Mapping[str, str] | None = None,
                colors: Mapping[str, Any] | None = None, *, named: bool = True) -> list[Mismatch]:
    """:func:`verify_scene` of a parse of the STEP at ``path``."""
    from cadgen._internal.step_scene_loader import load_step_scene

    return verify_scene(load_step_scene(Path(path)), expected, occurrences, colors, named=named)


def verify_scene(scene: Any, expected: Iterable[ExpectedComponent], occurrences: Mapping[str, str] | None = None,
                 colors: Mapping[str, Any] | None = None, *, named: bool = True) -> list[Mismatch]:
    """What differs between a parse of a STEP (``scene``, ``load_step_scene``'s) and the
    result: ``expected``, the components the result holds; ``occurrences``, each leaf of the
    flattened result by its token, with the part key of the component it places (unchecked
    when None); and ``colors``, the colour of each of those the result colours, by token
    (none coloured when None). An occurrence either side has that the other does not, one
    holding another part or drawing a face in another colour; a part either side holds that
    the other does not, a component the file holds with no part key, and for each part both
    hold, how its faces and edges differ in number, name or place -- and, where no
    occurrences are given, the colour of each face it colours itself. Empty when the file
    verifies. A file written without names (``named`` False) holds no part keys and no
    element names: each occurrence's component is the part the result places there, its
    faces and edges paired by place. ValueError for an expected component that no part key
    finds, or two that one finds, or one whose names and boxes do not count alike, and for
    a file without names verified without its occurrences."""
    from cadgen._internal.component_package import _normalized_face_colors
    from cadgen._internal.step_scene_mesh import _face_colors_by_ordinal

    wanted: dict[str, tuple[ExpectedComponent, dict[int, RGBA]]] = {}
    for component in expected:
        part = component.names.part
        if not is_part_key(part):
            raise ValueError(f"an expected component is found by its part key, not {part!r}")
        if part in wanted:
            raise ValueError(f"part {part} is expected twice")
        for kind, names, boxes in (("face", component.names.faces, component.face_boxes),
                                   ("edge", component.names.edges, component.edge_boxes)):
            if names and len(names) != len(boxes):
                raise ValueError(f"part {part}: {len(names)} {kind} names and {len(boxes)} {kind} boxes")
        wanted[part] = (component, _normalized_face_colors(component.face_colors))
    if not named and occurrences is None:
        raise ValueError("a file without names is verified by its occurrences")

    read_colors: dict[int, dict[int, RGBA]] = {}
    for prototype_key, shape in scene.prototype_shapes.items():
        raw = scene.prototype_face_colors.get(prototype_key)
        read_colors[prototype_key] = _normalized_face_colors(_face_colors_by_ordinal(shape, raw) if raw else None)

    mismatches: list[Mismatch] = []
    read = None
    if occurrences is not None:
        try:
            read = _read_occurrences(scene)
        except RuntimeError as error:
            mismatches.append(Mismatch(None, None, f"the file does not read as a document: {error}"))
        else:
            mismatches.extend(_occurrence_differences(read, occurrences, named=named))

    # The faces of each component the file holds paired with its part's, by the component
    # (its prototype) and the part.
    face_pairs: dict[tuple[int, str], dict[int, int]] = {}
    if named:
        held: set[str] = set()
        for prototype_key, shape in scene.prototype_shapes.items():
            names = scene.prototype_step_names.get(prototype_key) or ComponentNames()
            if names.part is None:
                label = scene.prototype_names.get(prototype_key)
                mismatches.append(Mismatch(None, None, f"component {label or 'with no name'} carries no part key"))
                continue
            if names.part not in wanted:
                mismatches.append(Mismatch(None, names.part, "the file holds a part the result does not"))
                continue
            held.add(names.part)
            component, own = wanted[names.part]
            differences, face_pairs[prototype_key, names.part] = _differences(shape, names, component)
            mismatches.extend(Mismatch(None, names.part, message) for message in differences)
            if occurrences is None:
                # Unplaced, a face is drawn in its part's own colour where its part gives one.
                drawn = _drawn_otherwise(face_pairs[prototype_key, names.part], read_colors[prototype_key],
                                         scene.prototype_colors.get(prototype_key), own, None, owned_only=True)
                if drawn:
                    mismatches.append(Mismatch(None, names.part, _recoloured("part", component, drawn)))
        mismatches.extend(Mismatch(None, part, "the file does not hold this part") for part in wanted if part not in held)
    else:
        for token, part in occurrences.items():
            if read is None or token not in read or part not in wanted or (read[token][1], part) in face_pairs:
                continue
            prototype_key = read[token][1]
            differences, face_pairs[prototype_key, part] = _differences(
                scene.prototype_shapes[prototype_key], ComponentNames(), wanted[part][0], named=False)
            mismatches.extend(Mismatch(None, part, message) for message in differences)

    for token, part in (occurrences or {}).items():
        if read is None or token not in read:
            continue
        name, prototype_key, read_part, read_color = read[token]
        if (named and read_part != part) or part not in wanted or (prototype_key, part) not in face_pairs:
            continue
        component, own = wanted[part]
        color = None if (colors or {}).get(token) is None else _rgba(colors[token])
        if not component.face_boxes:
            # A part with no faces is drawn in its occurrence's colour.
            if not _same_color(read_color, color):
                mismatches.append(Mismatch(token, part, f"the occurrence {name} is drawn in {_shown(read_color)} "
                                                        f"where the result draws it in {_shown(color)}"))
            continue
        drawn = _drawn_otherwise(face_pairs[prototype_key, part], read_colors[prototype_key], read_color, own, color)
        if drawn:
            mismatches.append(Mismatch(token, part, _recoloured(f"occurrence {name}", component, drawn)))
    return mismatches


def _read_occurrences(scene: Any) -> dict[str, tuple[str, int, str | None, RGBA | None]]:
    """Each occurrence a compile of the file names, by token: its name, the prototype it
    places, that prototype's part key and the colour it is drawn in. RuntimeError where the
    file does not read as a document."""
    from cadgen.store.build import _document_walk

    walk = _document_walk(scene)
    read: dict[str, tuple[str, int, str | None, RGBA | None]] = {}
    for occurrence in walk.occurrences:
        key = occurrence["_node"].prototype_key
        names = scene.prototype_step_names.get(key)
        color = occurrence.get("color")
        read[str(occurrence["id"])] = (str(occurrence["name"]), key, names.part if names is not None else None,
                                       None if color is None else _rgba(color))
    return read


def _occurrence_differences(read: Mapping[str, tuple[str, int, str | None, RGBA | None]],
                            expected: Mapping[str, str], *, named: bool = True) -> list[Mismatch]:
    """The occurrences a compile of the file names otherwise than the result: by token, one
    either side has that the other does not, or (``named``) one the file gives another part."""
    mismatches = []
    for token, part in expected.items():
        if token not in read:
            mismatches.append(Mismatch(token, part, "the file has no occurrence with this token"))
        elif named and read[token][2] != part:
            mismatches.append(Mismatch(token, part, f"the occurrence {read[token][0]} holds part {read[token][2]}"))
    for token, (name, _key, part, _color) in read.items():
        if token not in expected:
            mismatches.append(Mismatch(token, part, f"the file has an occurrence {name} the result does not"))
    return mismatches


def _drawn_otherwise(pairs: Mapping[int, int], read_own: Mapping[int, RGBA], read_placed: RGBA | None,
                     own: Mapping[int, RGBA], placed: RGBA | None, *, owned_only: bool = False
                     ) -> list[tuple[int, RGBA | None, RGBA | None]]:
    """The result's faces (by index, with the colour the file draws each in and the colour
    the result does) that the file draws otherwise: a face is drawn in its part's own colour
    where it has one, else in its occurrence's (``read_placed``, ``placed``). With
    ``owned_only`` only the faces the result's part colours itself are looked at."""
    drawn = []
    for index, read_index in sorted(pairs.items()):
        if owned_only and index + 1 not in own:
            continue
        file_color = read_own.get(read_index + 1, read_placed)
        result_color = own.get(index + 1, placed)
        if not _same_color(file_color, result_color):
            drawn.append((index, file_color, result_color))
    return drawn


def _recoloured(subject: str, component: ExpectedComponent, drawn: list[tuple[int, RGBA | None, RGBA | None]]) -> str:
    """What ``subject`` draws otherwise than the result, by the result's face names."""
    names = _by_ordinal(component.names.faces, len(component.face_boxes))
    labels = [names[index] or f"face {index + 1}" for index, _file, _result in drawn]
    pairs = {(file_color, result_color) for _index, file_color, result_color in drawn}
    if len(pairs) == 1:
        ((file_color, result_color),) = pairs
        return (f"the {subject} draws {_listed(labels)} in {_shown(file_color)} "
                f"where the result draws {_shown(result_color)}")
    return f"the {subject} draws {_listed(labels)} in other colours than the result"


def _differences(shape: Any, names: ComponentNames, component: ExpectedComponent, *,
                 named: bool = True) -> tuple[list[str], dict[int, int]]:
    """How the component ``shape`` the file holds, named ``names``, differs from
    ``component`` in its faces and edges, and its faces' pairs, from the result's index to
    the file's. Not ``named``, no name is compared, every element paired by place."""
    read_faces, read_edges = face_boxes(shape), edge_boxes(shape)
    display = max(_FLOOR, _display_tolerance([*component.face_boxes, *component.edge_boxes]),
                  _display_tolerance([*read_faces, *read_edges]))
    differences: list[str] = []
    for kind, read, wanted in (("face", read_faces, component.face_boxes), ("edge", read_edges, component.edge_boxes)):
        if len(read) != len(wanted):
            differences.append(f"the file has {len(read)} {kind}s where the result has {len(wanted)}")

    pairs = {}
    unnameable = {ordinal - 1 for ordinal in component.unnameable_edges}
    # An edge no file names that a face bounds is checked through its face; one no face
    # bounds answers for its body by its place.
    through_faces = {ordinal - 1 for ordinal in component.unnameable_edges - component.faceless_edges}
    for kind, read, wanted, read_given, wanted_given, own, unnamed, skipped in (
            ("face", read_faces, component.face_boxes, names.faces, component.names.faces, component.face_tolerances,
             set(), set()),
            ("edge", read_edges, component.edge_boxes, names.edges, component.names.edges, component.edge_tolerances,
             unnameable, through_faces)):
        read_names = _by_ordinal(read_given if named else (), len(read))
        expected_names = _by_ordinal(wanted_given if named else (), len(wanted))
        excused = {expected_names[index] for index in unnamed if index < len(expected_names)} - {None}
        differences.extend(_name_differences(kind, read_names, expected_names, excused))
        bounds = [display + (own[index] if index < len(own) else 0.0) for index in range(len(wanted))]
        pairs[kind] = _paired(kind, read_names, read, expected_names, wanted, bounds, display, differences, skipped)
    return differences, pairs["face"]


def _paired(kind: str, read_names: list[str | None], read_boxes: tuple[Box | None, ...],
            expected_names: list[str | None], expected_boxes: tuple[Box | None, ...], bounds: list[float],
            display: float, differences: list[str], skipped: set[int]) -> dict[int, int]:
    """Pair the result's elements of ``kind`` with the file's -- by a name on one element of
    each, the rest by place -- and add to ``differences`` the named ones off their place (by
    more than their ``bounds``, the display tolerance ``display`` and their own) and the
    number no place pairs. The result's elements at ``skipped`` (edges no file can name that
    a face bounds) are not paired: their faces answer for them. The pairs, from the result's
    index to the file's."""
    counted = Counter(name for name in read_names if name is not None)
    at = {name: index for index, name in enumerate(read_names) if name is not None and counted[name] == 1}
    pairs: dict[int, int] = {}
    taken: set[int] = set()
    moved = []
    for index, name in enumerate(expected_names):
        if name is None or name not in at or at[name] in taken:
            continue
        pairs[index] = at[name]
        taken.add(at[name])
        if not _near(read_boxes[at[name]], expected_boxes[index], bounds[index]):
            moved.append(name)
    if moved:
        differences.append(f"{kind}s off their place by more than {display:.3g} and their own tolerance: "
                           f"{_listed(moved)}")
    rest_expected = [index for index in range(len(expected_boxes)) if index not in pairs and index not in skipped]
    rest_read = [index for index in range(len(read_boxes)) if index not in taken]
    matched = _by_place([expected_boxes[index] for index in rest_expected],
                        [read_boxes[index] for index in rest_read], [bounds[index] for index in rest_expected])
    pairs.update((rest_expected[a], rest_read[b]) for a, b in matched.items())
    unmatched = len(rest_expected) - len(matched)
    if unmatched:
        differences.append(f"{unmatched} {kind}s not paired by name have no counterpart within {display:.3g} "
                           "and their own tolerance")
    return pairs


def _by_place(expected: list[Box | None], read: list[Box | None], bounds: list[float]) -> dict[int, int]:
    """Pair ``expected`` boxes with ``read`` ones, each within its own of ``bounds``: in
    order where every one pairs so, else each with the first free one near it (None only
    with None). The pairs, by index into each."""
    if len(expected) == len(read) and all(_near(b, a, bound) for a, b, bound in zip(expected, read, bounds)):
        return {index: index for index in range(len(expected))}
    pairs: dict[int, int] = {}
    empty = iter(index for index, box in enumerate(read) if box is None)
    for index, box in enumerate(expected):
        if box is None:
            found = next(empty, None)
            if found is not None:
                pairs[index] = found
    pending = sorted((box, index) for index, box in enumerate(read) if box is not None)
    lows = [box[0] for box, _index in pending]
    taken = [False] * len(pending)
    widest = max(bounds, default=0.0)
    for box, index in sorted((box, index) for index, box in enumerate(expected) if box is not None):
        start = bisect.bisect_left(lows, box[0] - widest)
        for candidate in range(start, len(pending)):
            if lows[candidate] > box[0] + widest:
                break
            if not taken[candidate] and _near(pending[candidate][0], box, bounds[index]):
                taken[candidate] = True
                pairs[index] = pending[candidate][1]
                break
    return pairs


def _name_differences(kind: str, read: list[str | None], expected: list[str | None],
                      excused: set[str]) -> list[str]:
    """The names of ``kind`` the file and the result do not share: missing from the file
    (but for those no file can carry, ``excused``), not the result's, or on more than one
    element."""
    counted = Counter(name for name in read if name is not None)
    wanted = {name for name in expected if name is not None}
    differences = []
    missing = sorted(wanted - set(counted) - excused)
    if missing:
        differences.append(f"{kind} names missing from the file: {_listed(missing)}")
    foreign = sorted(set(counted) - wanted)
    if foreign:
        differences.append(f"{kind} names the result does not have: {_listed(foreign)}")
    repeated = sorted(name for name, count in counted.items() if count > 1)
    if repeated:
        differences.append(f"{kind} names on more than one {kind}: {_listed(repeated)}")
    return differences


def _boxes(shape: Any, kind: Any) -> tuple[Box | None, ...]:
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape

    from cadgen._internal.topology_extract import _bnd_box

    elements = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, kind, elements)
    return tuple(_box(_bnd_box(elements.FindKey(ordinal))) for ordinal in range(1, elements.Extent() + 1))


def _by_ordinal(names: tuple[str | None, ...] | list[str | None], count: int) -> list[str | None]:
    """``names`` as one name or None per element (none named when empty)."""
    return list(names) if names else [None] * count


def _display_tolerance(boxes: list[Box | None]) -> float:
    """The display chord tolerance of a component whose elements' boxes are ``boxes``: the
    relative chord tolerance times its bounding diagonal."""
    from cadgen.tessellation_policy import DISPLAY_TESSELLATION

    bounded = [box for box in boxes if box is not None]
    if not bounded:
        return 0.0
    low = [min(box[axis] for box in bounded) for axis in range(3)]
    high = [max(box[axis + 3] for box in bounded) for axis in range(3)]
    return DISPLAY_TESSELLATION["chordTolerance"] * math.dist(low, high)


def _near(read: Box | None, expected: Box | None, bound: float) -> bool:
    if read is None or expected is None:
        return read is None and expected is None
    return all(abs(a - b) <= bound for a, b in zip(read, expected))


def _same_color(read: RGBA | None, expected: RGBA | None) -> bool:
    if read is None or expected is None:
        return read is None and expected is None
    return len(read) == len(expected) and all(abs(a - b) <= _COLOR_TOLERANCE for a, b in zip(read, expected))


def _rgba(color: Any) -> RGBA:
    values = tuple(float(channel) for channel in color)
    return values if len(values) == 4 else (*values[:3], 1.0)


def _shown(color: RGBA | None) -> str:
    return "no colour" if color is None else "(" + ", ".join(f"{channel:.3g}" for channel in color) + ")"


def _box(values: Any) -> Box | None:
    return None if values is None else tuple(float(value) for value in values)


def _listed(names: list[str]) -> str:
    shown = ", ".join(names[:_SHOWN])
    return shown if len(names) <= _SHOWN else f"{shown} and {len(names) - _SHOWN} more"
