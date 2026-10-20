"""Component serialization, intrinsic appearance and extraction helpers.

Canonical geometry inputs contain exact encoded BREP and effective face-color
recipes. Disposable display data is derived by ``cadgen.store.parts`` under a
separately attested producer. Native imports remain local to operations.
"""

from __future__ import annotations

import io
import struct
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

DESCRIPTOR_NAME = "assembly.json"


def is_assembly_package(path: Path) -> bool:
    """True when ``path`` is a view directory (has assembly.json)."""
    return path.is_dir() and (path / DESCRIPTOR_NAME).is_file()


def read_package_descriptor(path: Path) -> dict[str, Any] | None:
    """Load an assembly.json from a view directory (or its assembly.json path).

    Returns None for anything that is not a view directory with a readable
    assembly.json (missing, partial, or a stray file at the tree path)."""
    if path.is_dir():
        descriptor_path = path / DESCRIPTOR_NAME
    elif path.name == DESCRIPTOR_NAME:
        descriptor_path = path
    else:
        return None
    if not descriptor_path.is_file():
        return None
    try:
        descriptor = json.loads(descriptor_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return descriptor if isinstance(descriptor, dict) else None


def _component_id(source_hash: str) -> str:
    # Trees use a short map key alongside the complete geometry content hash.
    # Publication and capture reject a short-ID collision instead of merging it.
    return source_hash[:16]


def _normalized_face_colors(value: object) -> dict[int, tuple[float, float, float, float]]:
    """Canonical extraction input: positive face ordinals and finite RGBA.

    Channels are clamped just as the STEP color writer clamps them. Converting
    keys must never silently merge two different finishes for one face.
    """
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("cad_face_ordinal_colors must map face ordinals to RGBA")
    normalized: dict[int, tuple[float, float, float, float]] = {}
    for raw_ordinal, raw_color in value.items():
        try:
            ordinal = int(raw_ordinal)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError(
                f"cad_face_ordinal_colors: a face is named by its ordinal, a whole number from 1 "
                f"(got {raw_ordinal!r})"
            ) from error
        if ordinal <= 0 or isinstance(raw_ordinal, bool) or (
            not isinstance(raw_ordinal, str) and raw_ordinal != ordinal
        ):
            raise ValueError(
                f"cad_face_ordinal_colors: a face is named by its ordinal, a whole number from 1 "
                f"(got {raw_ordinal!r})"
            )
        try:
            channels = tuple(float(channel) for channel in raw_color)
        except (TypeError, ValueError) as error:
            raise ValueError(f"face {ordinal} color must be finite RGBA") from error
        if len(channels) != 4 or not all(math.isfinite(channel) for channel in channels):
            raise ValueError(f"face {ordinal} color must be finite RGBA")
        color = tuple(min(1.0, max(0.0, channel)) for channel in channels)
        if ordinal in normalized and normalized[ordinal] != color:
            raise ValueError(f"conflicting colors for face ordinal {ordinal}")
        normalized[ordinal] = color
    return dict(sorted(normalized.items()))


def _content_hash_and_bytes(shape: Any, *, face_colors: object = None) -> tuple[str, bytes]:
    """The complete geometry-input identity and its admitted encoded BREP bytes."""
    prepared = prepare_geometry_component(shape, face_colors=face_colors)
    return prepared["entry"]["contentHash"], prepared["payload"]


def _content_hash_shape(shape: Any) -> str:
    """Hash a shape's complete component extraction input (see
    :func:`_content_hash_and_bytes`)."""
    return _content_hash_and_bytes(shape)[0]


def _transform_from_location(location: Any) -> list[float]:
    """Flatten a build123d ``Location`` to a 16-float row-major 4x4 matrix."""
    trsf = location.wrapped.Transformation()
    rows = [trsf.Value(r, c) for r in range(1, 4) for c in range(1, 5)]
    return [
        rows[0], rows[1], rows[2], rows[3],
        rows[4], rows[5], rows[6], rows[7],
        rows[8], rows[9], rows[10], rows[11],
        0.0, 0.0, 0.0, 1.0,
    ]


def optimal_box(wrapped: Any) -> list[float] | None:
    """The TIGHT world-frame bounds of one ``TopoDS_Shape`` as
    ``[xmin, ymin, zmin, xmax, ymax, zmax]``, or None when it bounds nothing.

    ``BRepBndLib::Add`` bounds a B-spline by its CONTROL POLYGON: a NURBS
    circle of radius r reports r/cos(22.5 deg) = 1.082 r, so every rounded body
    came back ~8% too big and a reported bound could invent a clash that is not
    there (PR #370 bug record 004). ``AddOptimal`` subdivides instead.
    ``useTriangulation=False``: meshing here would mutate the shared ``TShape``
    and break content-addressed component dedup on a later in-process rebuild.
    """
    try:
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib

        box = Bnd_Box()
        BRepBndLib.AddOptimal_s(wrapped, box, False, False)
        if box.IsVoid():
            return None
        xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
        return [float(xmin), float(ymin), float(zmin), float(xmax), float(ymax), float(zmax)]
    except Exception:  # noqa: BLE001 - OCP bounds reads can raise on odd shapes
        return None


def _world_leaves(wrapped: Any) -> list[Any]:
    """The shape's leaves, each carrying its WORLD location.

    ``TopoDS_Iterator`` composes the parent's location into every child it
    yields, so recursing containers hands back exactly the placed bodies the
    occurrences describe — one leaf per occurrence, links included.
    """
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopoDS import TopoDS_Iterator

    containers = (TopAbs_ShapeEnum.TopAbs_COMPOUND, TopAbs_ShapeEnum.TopAbs_COMPSOLID)
    leaves: list[Any] = []
    stack = [wrapped]
    while stack:
        node = stack.pop()
        if node.ShapeType() not in containers:
            leaves.append(node)
            continue
        iterator = TopoDS_Iterator(node)
        while iterator.More():
            stack.append(iterator.Value())
            iterator.Next()
    return leaves


def component_leaf_layout(wrapped: Any) -> dict[str, Any]:
    """How a prototype's leaves sit, ``{"leaves": n, "inner": [...]}``: how many leaves the
    UNLOCATED prototype has, in the order their remembered boxes are numbered
    (``placed_prototype_bounds``), and where each sits inside it -- the leaf's own location
    as twelve numbers, the rotation's rows then the translation, the identity for a leaf at
    the prototype's own placement (a product a STEP file places), an offset for one a
    builder wrapped (build123d's ``Box`` is a compound around a solid it centres). A pure
    function of the encoded bytes.
    """
    from OCP.TopLoc import TopLoc_Location

    inner = []
    for leaf in _world_leaves(wrapped.Located(TopLoc_Location())):
        transform = leaf.Location().Transformation()
        inner.append([float(transform.Value(row, column)) for row in range(1, 4) for column in range(1, 5)])
    return {"leaves": len(inner), "inner": inner}


def placed_prototype_bounds(codec: str, brep: str, prototype: Any, location: Any) -> dict[str, list[float]] | None:
    """The world box of ``prototype`` (a decoded component, unlocated) placed at ``location``
    (a ``TopLoc_Location``, or build123d's ``Location`` over one): each leaf measured
    untranslated under its own world rotation and shifted by its own world translation, as
    ``_leaf_bounds`` measures a whole document, and remembered per leaf under
    ``cadgen.store.bounds.PREPARED_OCCURRENCE_BOUNDS_ALGORITHM`` (the component's codec and
    BREP object, the leaf's ordinal, the leaf's rotation), with the component's leaf layout
    beside it. A result and a compiled document measure through this one function, so the
    same body at the same rotation is measured once wherever it is translated to, and a
    composed reader (``bounds.composed_bounds``: a parent's bounds over its children's
    components, the animation bake's leaf boxes) finds the box the publication measured.
    None when no leaf has bounds; a remembered value that is not a box raises."""
    from OCP.TopLoc import TopLoc_Location
    from OCP.gp import gp_Vec

    from cadgen.store.bounds import PREPARED_OCCURRENCE_BOUNDS_ALGORITHM, cached_box, cached_leaf_layout

    wrapped = getattr(prototype, "wrapped", prototype)
    cached_leaf_layout(codec, brep, lambda: component_leaf_layout(wrapped))
    boxes: list[list[float]] = []
    for ordinal, leaf in enumerate(_world_leaves(wrapped.Located(getattr(location, "wrapped", location))), start=1):
        transform = leaf.Location().Transformation()
        translation = tuple(float(value) for value in transform.TranslationPart().Coord())
        transform.SetTranslationPart(gp_Vec(0.0, 0.0, 0.0))
        linear = struct.pack("<12d", *(float(transform.Value(row, column))
                                        for row in range(1, 4) for column in range(1, 5)))
        untranslated = leaf.Located(TopLoc_Location(transform))
        box = cached_box(PREPARED_OCCURRENCE_BOUNDS_ALGORITHM, (codec, brep, ordinal, linear),
                         lambda untranslated=untranslated: optimal_box(untranslated))
        if box is None:
            continue
        if (type(box) not in (list, tuple) or len(box) != 6
                or any(type(value) not in (float, int) or not math.isfinite(value) for value in box)):
            raise ValueError("a remembered leaf box is not six finite numbers")
        boxes.append([float(value) + translation[index % 3] for index, value in enumerate(box)])
    if not boxes:
        return None
    return {
        "min": [min(box[axis] for box in boxes) for axis in range(3)],
        "max": [max(box[axis] for box in boxes) for axis in range(3, 6)],
    }


def _bbox_from_shape(shape: Any) -> dict[str, list[float]] | None:
    """The world-frame axis-aligned bounding box of a composed shape, as the
    ``{"min": [...], "max": [...]}`` the assembly.json records so a reader of the whole
    entry (a snapshot's bounds, ``--mode list``) does not have to re-mesh + extract full
    topology.

    Measured PER LEAF and merged, not once over the whole compound, because a
    leaf's box is a pure function of its geometry and rotation. Translation
    shifts the six bounds without repeating the surface-extrema calculation.
    ``cadgen.store.bounds`` keeps the untranslated box in the warm worker and
    in the store, so translated instances share that calculation. Tight bounds
    cost ~0.08 ms per face, which a whole
    150k-face assembly could not absorb on every finalize but an unchanged
    occurrence never pays twice.

    The key's content digest serializes the leaf's geometry. Occurrences
    of one prototype share its TShape, and nothing runs between two leaves of
    this read-only traversal that could edit it, so the digest is computed once
    per TShape encountered here and never kept past the call: a 2400-occurrence
    assembly of 470 prototypes serializes 470 shapes, not 2400.
    """
    try:
        return _leaf_bounds(shape)
    except Exception:  # noqa: BLE001 - OCP bounds reads can raise on odd shapes; a component without bounds is None
        return None


def _leaf_bounds(shape: Any) -> dict[str, list[float]] | None:
    """:func:`_bbox_from_shape`, raising where it answers None for a failure:
    None here means only that no leaf has bounds. Leaves are merged in the
    order :func:`_world_leaves` yields them, the first of equal values kept."""
    from OCP.TopLoc import TopLoc_Location
    from OCP.gp import gp_Vec

    from cadgen.store.bounds import cached_box

    boxes = []
    digests: dict[Any, str] = {}
    for leaf in _world_leaves(shape.wrapped):
        transform = leaf.Location().Transformation()
        translation = tuple(transform.TranslationPart().Coord())
        transform.SetTranslationPart(gp_Vec(0.0, 0.0, 0.0))
        untranslated = leaf.Located(TopLoc_Location(transform))
        try:
            tshape = leaf.TShape()
            digest = digests.get(tshape)
        except TypeError:  # an unhashable native handle: digest this leaf alone
            tshape, digest = None, None
        if digest is None:
            digest = hashlib.sha256(_raw_brep_bytes(untranslated)).hexdigest()
            if tshape is not None:
                digests[tshape] = digest
        rotation = struct.pack("<12d", *(transform.Value(row, column)
                                         for row in range(1, 4) for column in range(1, 5)))
        box = cached_box(
            # The name names the FUNCTION: change what this computes and
            # change the name with it.
            "occurrence_bbox.optimal.untranslated.v2",
            (digest, rotation),
            lambda untranslated=untranslated: optimal_box(untranslated),
        )
        if box is not None:
            boxes.append([value + translation[index % 3] for index, value in enumerate(box)])
    if not boxes:
        return None
    return {
        "min": [min(box[axis] for box in boxes) for axis in (0, 1, 2)],
        "max": [max(box[axis] for box in boxes) for axis in (3, 4, 5)],
    }


def _occurrence_color(child: Any) -> list[float] | None:
    color = getattr(child, "color", None)
    if color is None:
        return None
    try:
        return [float(color.red), float(color.green), float(color.blue), float(color.alpha)]
    except AttributeError:
        try:
            return [float(c) for c in tuple(color)]
        except TypeError:
            return None


_MATERIAL_KEYS = ("roughness", "metalness", "clearcoat", "clearcoatRoughness", "opacity")


def _occurrence_material(child: Any) -> dict[str, Any] | None:
    """Private material metadata restored from a pinned model tree."""
    material = getattr(child, "_cadgen_material", None)
    if not isinstance(material, dict):
        return None
    resolved: dict[str, Any] = {}
    name = material.get("name")
    if isinstance(name, str) and name.strip():
        resolved["name"] = name.strip()
    base_color = material.get("baseColor")
    if isinstance(base_color, str):
        resolved["baseColor"] = base_color
    for key in _MATERIAL_KEYS:
        value = material.get(key)
        if value is None:
            continue
        try:
            resolved[key] = min(1.0, max(0.0, float(value)))
        except (TypeError, ValueError):
            continue
    return resolved or None


def _shape_brep_bytes(shape: Any) -> bytes:
    """The bytes a component is stored as and hashed by: the location-stripped
    binary BREP (no triangulation/normals) of :func:`_canonical_shape`, so equal
    geometry writes equal bytes whichever kernel objects -- a warm run's memo hits
    or a cold run's fresh results -- carry it.

    Takes a build123d shape or a bare ``TopoDS_Shape``: a document's
    prototypes (``store.build``) come through here too, and they hold kernel
    shapes, not wrappers."""
    return _raw_brep_bytes(_canonical_shape(shape))


def _raw_brep_bytes(shape: Any) -> bytes:
    """The location-stripped binary BREP of a shape exactly as its objects stand:
    what :func:`_shape_brep_bytes` writes of a canonical shape, and a digest of
    geometry a process only compares with itself (a cache key, a check that held
    geometry is unchanged) reads, with no canonical copy to make."""
    from OCP.BinTools import BinTools, BinTools_FormatVersion
    from OCP.TopLoc import TopLoc_Location

    stream = io.BytesIO()
    BinTools.Write_s(
        getattr(shape, "wrapped", shape).Located(TopLoc_Location()),
        stream,
        False,  # theWithTriangles
        False,  # theWithNormals
        # PINNED, not _CURRENT: these component objects are content-addressed — their
        # bytes ARE the cid. A floating _CURRENT would let a future OCP
        # upgrade silently re-serialize every component object and re-key every cid as a
        # dependency-update side effect. Bumping this must stay a deliberate
        # act — treat it like a schema version bump.
        BinTools_FormatVersion.BinTools_FormatVersion_VERSION_4,
    )
    return stream.getvalue()


_IDENTITY_MATRIX = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0)


def _canonical_shape(shape: Any) -> Any:
    """An unplaced shape equal to ``shape`` whose BinTools bytes are a function of its
    geometry alone: what :func:`_shape_brep_bytes` writes.

    BinTools writes two things geometry does not decide. Its location table holds
    one entry per transform *object* (``TopLoc_Datum3D``) a sub-shape's placement
    chain or a representation names, and an edge's curve representations in the
    order its list holds them. A warm run's kernel memo hands back the objects the
    first run made, which point at that run's transform objects, while what it
    computes again points at fresh ones of the same value; and a representation
    a hit's replay appends lands at another place in the list than a cold run's.
    So equal geometry could write different bytes. Here every transform object is
    replaced by the first one met of its value (bit for bit), an identity transform
    by no transform at all -- the same homomorphism on every placement and every
    representation's location, so each lookup of a representation by its surface
    and location still finds it -- and each edge's representations are ordered by
    what they are: kind, then the surface (by the order the shape's faces first
    name it), then the location's transform, and two on one surface at one
    placement -- both written, the last one read kept -- by their curves and ranges.

    Nothing reachable from ``shape`` is changed. A sub-shape this changes nothing
    in, below or on is the shape's own (shared, as every TShape it holds, where its
    flags say a parent may take it as it is); every other one is a copy
    (``EmptyCopy``, which shares curves and surfaces) with its flags as they were.
    An already canonical shape is therefore returned unplaced as it is. Should
    merging equal transforms join sub-shapes the original kept apart (two
    occurrences of one TShape at equal placements made by different objects), the
    original numbering would not survive, so the shape is returned unplaced as it
    is instead."""
    from OCP.BRep import (
        BRep_Builder, BRep_CurveOn2Surfaces, BRep_GCurve, BRep_PointOnCurve, BRep_PointOnCurveOnSurface,
        BRep_PointOnSurface, BRep_TEdge, BRep_TFace, BRep_TVertex,
    )
    from OCP.BinTools import BinTools_Curve2dSet, BinTools_SurfaceSet
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopTools import TopTools_IndexedMapOfShape
    from OCP.TopoDS import TopoDS_Iterator, TopoDS_Shape

    root = getattr(shape, "wrapped", shape).Located(TopLoc_Location())
    empty = TopLoc_Location()

    def matrix(transform: Any) -> tuple[float, ...]:
        return tuple(transform.Value(i, j) for i in range(1, 4) for j in range(1, 5))

    # Per transform object: the canonical location standing for it, and whether that
    # is the object itself.
    by_value: dict[bytes, Any] = {}
    by_datum: dict[Any, tuple[Any, bool]] = {}

    def datum_location(datum: Any) -> tuple[Any, bool]:
        found = by_datum.get(datum)
        if found is None:
            values = matrix(datum.Transformation())
            key = struct.pack(">12d", *values)
            canonical = by_value.get(key)
            if canonical is not None:
                found = (canonical, False)
            elif values == _IDENTITY_MATRIX:
                by_value[key] = empty
                found = (empty, False)
            else:
                canonical = by_value[key] = TopLoc_Location(datum)
                found = (canonical, True)
            by_datum[datum] = found
        return found

    by_chain: dict[tuple, tuple[Any, bool]] = {}
    # Each canonical location's transform, as a sort key: by the object's identity, which
    # the memos here keep alive for the whole call.
    placements: dict[int, bytes] = {id(empty): b""}

    def placement_key(loc: Any) -> bytes:
        found = placements.get(id(loc))
        if found is None:
            found = placements[id(loc)] = struct.pack(">12d", *matrix(loc.Transformation()))
        return found

    def location(loc: Any) -> tuple[Any, bool]:
        """The canonical location for ``loc``, and whether it is ``loc`` unchanged."""
        if loc.IsIdentity():
            return empty, True
        items = []
        rest = loc
        while not rest.IsIdentity():
            items.append((rest.FirstDatum(), rest.FirstPower()))
            rest = rest.NextLocation()
        chain = tuple(items)
        found = by_chain.get(chain)
        if found is None:
            # A location is NextLocation() * FirstDatum()^FirstPower(): rebuild it
            # right to left, so its multiplication cancels what it cancelled.
            canonical, same = empty, True
            for datum, power in chain:
                standing, itself = datum_location(datum)
                canonical = standing.Powered(power).Multiplied(canonical)
                same = same and itself
            found = by_chain[chain] = (loc if same else canonical, same)
        return found

    surfaces: dict[Any, int] = {}
    faces = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(root, TopAbs_FACE, faces)
    for index in range(1, faces.Extent() + 1):
        surfaces.setdefault(faces.FindKey(index).TShape().Surface(), len(surfaces))
    unnamed: dict[Any, bytes] = {}

    def surface_key(surface: Any) -> tuple:
        index = surfaces.get(surface)
        if index is not None:
            return (1, index)
        # A surface no face of the shape lies on (a representation another shape's
        # operation left on a shared edge): named by its own bytes.
        found = unnamed.get(surface)
        if found is None:
            found = unnamed[surface] = geometry_bytes(BinTools_SurfaceSet(), surface)
        return (2, found)

    def geometry_bytes(table: Any, geometry: Any) -> bytes:
        table.Add(geometry)
        stream = io.BytesIO()
        table.Write(stream)
        return stream.getvalue()

    def content_key(rep: Any) -> tuple:
        """What tells apart two representations of one edge on one surface at one
        placement -- which BinTools writes both of, and a reader keeps the last of: their
        curves and ranges."""
        curves = [rep.PCurve()] + ([rep.PCurve2()] if rep.IsCurveOnClosedSurface() else [])
        return (rep.First(), rep.Last(), *(geometry_bytes(BinTools_Curve2dSet(), curve) for curve in curves))

    def representation_key(rep: Any, placed: Any, placed2: Any) -> tuple:
        if rep.IsCurve3D():
            return (0, placement_key(placed))
        if isinstance(rep, BRep_CurveOn2Surfaces):
            return (3, surface_key(rep.Surface()), placement_key(placed),
                    surface_key(rep.Surface2()), placement_key(placed2))
        return (2 if rep.IsCurveOnClosedSurface() else 1, surface_key(rep.Surface()), placement_key(placed))

    def drained(held: Any) -> list[Any]:
        # A kernel list's own Python iteration costs milliseconds an item; taking
        # the items off its front costs microseconds. ``held`` is a list of our own.
        items = []
        while not held.IsEmpty():
            items.append(held.First())
            held.RemoveFirst()
        return items

    builder = BRep_Builder()
    copies: dict[Any, tuple[Any, bool]] = {}

    def visit(tshape: Any, root: bool = False) -> tuple[Any, bool]:
        """The canonical TShape for ``tshape``, and whether it is ``tshape`` itself."""
        found = copies.get(tshape)
        if found is not None:
            return found
        children = []
        same = True
        if not isinstance(tshape, BRep_TVertex):  # a vertex has no sub-shapes
            iterator = TopoDS_Iterator(_unplaced(tshape), False, False)
            while iterator.More():
                child = iterator.Value()
                canonical_tshape, kept = visit(child.TShape())
                placed, unmoved = location(child.Location())
                children.append((child, canonical_tshape, kept, placed, unmoved))
                same = same and kept and unmoved
                iterator.Next()
        edge = vertex = None
        if isinstance(tshape, BRep_TEdge):
            # Read the original's representations (``Curves()`` is a copy of its list).
            edge, originals = [], []
            for rep in drained(tshape.Curves()):
                if not isinstance(rep, (BRep_GCurve, BRep_CurveOn2Surfaces)):
                    continue  # a polygon: EmptyCopy keeps none, and BinTools writes none here
                originals.append(rep)
                placed, unmoved = location(rep.Location())
                placed2 = None
                if isinstance(rep, BRep_CurveOn2Surfaces):
                    placed2, unmoved2 = location(rep.Location2())
                    unmoved = unmoved and unmoved2
                edge.append((representation_key(rep, placed, placed2), placed, placed2))
                same = same and unmoved
            if len({key for key, _placed, _placed2 in edge}) < len(edge):
                edge = [(key + content_key(rep), placed, placed2) if key[0] in (1, 2) else (key, placed, placed2)
                        for rep, (key, placed, placed2) in zip(originals, edge)]
            order = sorted(range(len(edge)), key=lambda index: edge[index][0])
            same = same and order == list(range(len(edge)))
        elif isinstance(tshape, BRep_TVertex):
            vertex = []
            for point in drained(tshape.Points()):
                placed, unmoved = location(point.Location())
                vertex.append((point, placed))
                same = same and unmoved
        elif isinstance(tshape, BRep_TFace):
            face_location, unmoved = location(tshape.Location())
            same = same and unmoved
        # A parent that is a copy takes a kept TShape as it is only if that changes no
        # flag of it: adding a child marks it no longer free. The root has no parent.
        if same and (root or not tshape.Free()):
            found = copies[tshape] = (tshape, True)
            return found
        fresh = tshape.EmptyCopy()
        if edge is not None:
            held = fresh.ChangeCurves()
            held.Clear()
            for index in order:
                rep = originals[index]
                _key, placed, placed2 = edge[index]
                if placed2 is not None:
                    # The one kind whose second location has no setter.
                    rep = BRep_CurveOn2Surfaces(rep.Surface(), rep.Surface2(), placed, placed2, rep.Continuity())
                else:
                    rep = rep.Copy()  # shares its curves and surface with the original
                    rep.Location(placed)
                held.Append(rep)
        elif vertex is not None:
            held = fresh.ChangePoints()
            held.Clear()
            for point, placed in vertex:
                if point.IsPointOnCurve():
                    held.Append(BRep_PointOnCurve(point.Parameter(), point.Curve(), placed))
                elif point.IsPointOnCurveOnSurface():
                    held.Append(BRep_PointOnCurveOnSurface(point.Parameter(), point.PCurve(), point.Surface(), placed))
                else:
                    held.Append(BRep_PointOnSurface(point.Parameter(), point.Parameter2(), point.Surface(), placed))
        elif isinstance(fresh, BRep_TFace):
            fresh.Location(face_location)
        parent = _unplaced(fresh)
        for child, canonical_tshape, kept, placed, unmoved in children:
            if kept and unmoved:
                occurrence = child
            else:
                occurrence = TopoDS_Shape()
                occurrence.TShape(canonical_tshape)
                # Placed as the original is, scaled or mirrored included.
                occurrence.Location(placed, False)
                occurrence.Orientation(child.Orientation())
            builder.Add(parent, occurrence)
        fresh.Free(tshape.Free())
        fresh.Modified(tshape.Modified())
        fresh.Checked(tshape.Checked())
        fresh.Orientable(tshape.Orientable())
        fresh.Closed(tshape.Closed())
        fresh.Infinite(tshape.Infinite())
        fresh.Convex(tshape.Convex())
        fresh.Locked(tshape.Locked())
        found = copies[tshape] = (fresh, False)
        return found

    canonical_tshape, kept = visit(root.TShape(), root=True)
    if kept:
        return root
    canonical = TopoDS_Shape()
    canonical.TShape(canonical_tshape)
    canonical.Orientation(root.Orientation())
    before, after = TopTools_IndexedMapOfShape(), TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(root, before)
    TopExp.MapShapes_s(canonical, after)
    return canonical if before.Extent() == after.Extent() else root


def _unplaced(tshape: Any) -> Any:
    """A shape of ``tshape`` at no placement, forward."""
    from OCP.TopoDS import TopoDS_Shape

    shape = TopoDS_Shape()
    shape.TShape(tshape)
    return shape


def _build123d_shape_from_topods(topo: Any) -> Any:
    """Wrap a bare ``TopoDS_Shape`` in the build123d class matching its ShapeType
    (a Solid stays a Solid; anything unknown is a Compound)."""
    import build123d
    from OCP.TopAbs import TopAbs_ShapeEnum

    by_type = {
        TopAbs_ShapeEnum.TopAbs_COMPOUND: build123d.Compound,
        TopAbs_ShapeEnum.TopAbs_COMPSOLID: build123d.Compound,
        TopAbs_ShapeEnum.TopAbs_SOLID: build123d.Solid,
        TopAbs_ShapeEnum.TopAbs_SHELL: build123d.Shell,
        TopAbs_ShapeEnum.TopAbs_FACE: build123d.Face,
        TopAbs_ShapeEnum.TopAbs_WIRE: build123d.Wire,
        TopAbs_ShapeEnum.TopAbs_EDGE: build123d.Edge,
        TopAbs_ShapeEnum.TopAbs_VERTEX: build123d.Vertex,
    }
    cls = by_type.get(topo.ShapeType(), build123d.Compound)
    return cls(topo)


# The ONLY version on a component's identity. A component's cid hashes its BREP
# bytes, its intrinsic face colours, the construction key and the lineage its
# build named it by (``construction``, ``lineage``: none for geometry no
# construction of the run made), and this scheme string; nothing else. Bump it
# only when the same inputs must map to a different tree (a codec or
# interpretation change): that re-keys every user's store, so it is rare and
# deliberate. An extractor, selector-table or mesher fix bumps the version of
# that derived artifact (cadgen.store.parts.PART_VERSION) and never this
# string. STORE.md "Identity and versions" records the rule. 4: a component
# carries its construction key and lineage. 5: its bytes are those of its
# canonical shape (``_canonical_shape``: one transform object per distinct
# transform, none for an identity, each edge's representations in a fixed
# order), so a warm run's memo hits and a cold run write one component; the
# same shapes now write different bytes, and stores re-key rather than mix.
GEOMETRY_SCHEME = "cadgen-geometry-input-v5"
GEOMETRY_CODECS = frozenset({"bintools-v4", "bintools-v3", "breptools-ascii-v3"})
COMPONENT_KINDS = frozenset({"native", "eager-only"})
_BREP_HEADERS = {
    "bintools-v4": b"\nOpen CASCADE Topology V4, (c) Open Cascade\n",
    "bintools-v3": b"\nOpen CASCADE Topology V3 (c)\n",
    "breptools-ascii-v3": b"\nCASCADE Topology V3, (c) Open Cascade\n",
}
class NativeUnavailable(RuntimeError):
    """An eager-only component cannot certify faithful native reconstruction."""

class CodecFidelityError(ValueError):
    """No permitted codec proved the required point-bearing fidelity."""

def canonical_json_bytes(value: Any) -> bytes:
    # JSON object keys are strings. Convert ordinal keys before sorting so the
    # same value has identical bytes before and after a JSON store round-trip.
    # Sorting int keys first would emit 1,2,10 while parsed keys emit 1,10,2.
    def json_value(item):
        if type(item) is dict:
            if any(type(key) not in (str, int) for key in item):
                raise ValueError("unsupported canonical JSON key")
            normalized = {str(key): json_value(part) for key, part in item.items()}
            if len(normalized) != len(item):
                raise ValueError("duplicate canonical JSON key")
            return normalized
        if isinstance(item, (list, tuple)):
            return [json_value(part) for part in item]
        return item
    return json.dumps(json_value(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()

def geometry_component_hash(codec: str, payload: bytes, face_colors: dict, *,
                            kind: str = "native", topology: str | None = None,
                            construction: str | None = None, lineage: str | None = None) -> str:
    """A component's complete identity: the scheme, kind, codec, BREP bytes and face
    colours, an eager-only component's topology facts object (``topology``), and the
    construction key and lineage object its build named it by, where it did."""
    from cadgen.store.objects import is_object_hash

    if codec not in GEOMETRY_CODECS or kind not in COMPONENT_KINDS:
        raise ValueError("unsupported geometry codec/kind")
    if (kind == "eager-only") != is_object_hash(topology):
        raise ValueError("eager-only geometry must pin its topology facts")
    if construction is not None and not is_object_hash(construction):
        raise ValueError("a construction key is a sha256 hex digest")
    if lineage is not None and not is_object_hash(lineage):
        raise ValueError("a lineage is an object hash")
    digest = hashlib.sha256()
    digest.update(GEOMETRY_SCHEME.encode() + b"\0" + kind.encode() + b"\0" + codec.encode() + b"\0")
    digest.update(len(payload).to_bytes(8, "big"))
    digest.update(payload)
    digest.update(canonical_json_bytes(face_colors))
    for name, value in (("topology", topology), ("construction", construction), ("lineage", lineage)):
        if value is not None:
            digest.update(b"\0" + name.encode() + b"\0" + value.encode())
    return digest.hexdigest()


_LINEAGE_KINDS = (("faces", "face"), ("edges", "edge"), ("vertices", "vertex"))


def lineage_bytes(lineage: Mapping[str, Any]) -> bytes:
    """A component's lineage (``kernel_trace.Trace.account``) as the object its tree
    entry names: canonical JSON, checked as :func:`read_lineage` reads it."""
    payload = canonical_json_bytes(lineage)
    read_lineage(payload)
    return payload


def read_lineage(payload: bytes) -> dict[str, Any]:
    """A stored lineage: ``{"made", "faces", "edges", "vertices", "table"}``, each list
    an element name or None per ordinal, every name of its kind and distinct, and the
    table spelling every folded atom a name or an entry reaches (``kernel_trace``), and
    nothing else. ValueError for anything else. Each list is checked whole, one match of
    the grammar over its names one per line (``joined_lines``)."""
    from cadgen.cad_ref_syntax import (
        ELEMENT_NAME_LINES_RE,
        FOLDED_ATOM_LINES_RE,
        STEPPED_DERIVATION_LINES_RE,
        folded_atoms,
        is_feature_name,
        joined_lines,
    )

    try:
        lineage = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("invalid lineage JSON") from error
    if type(lineage) is not dict or set(lineage) != {"made", "table", *(plural for plural, _kind in _LINEAGE_KINDS)}:
        raise ValueError("a lineage holds made, faces, edges, vertices and table")
    if lineage["made"] is not None and not is_feature_name(lineage["made"]):
        raise ValueError("a lineage's maker is not a feature name")
    table = lineage["table"]
    if (type(table) is not dict or joined_lines(FOLDED_ATOM_LINES_RE, list(table)) is None
            or joined_lines(STEPPED_DERIVATION_LINES_RE, list(table.values())) is None):
        raise ValueError("a lineage's table maps folded atoms to derivations")
    pending: list[str] = []
    for plural, kind in _LINEAGE_KINDS:
        names = lineage[plural]
        present = [name for name in names if name is not None] if type(names) is list else None
        joined = None if present is None else joined_lines(ELEMENT_NAME_LINES_RE[kind[0]], present)
        if joined is None:
            raise ValueError(f"a lineage's {plural} are not {kind} names")
        if len(set(present)) != len(present):
            raise ValueError(f"a lineage names two {plural} alike")
        pending.extend(folded_atoms(joined))
    reached: set[str] = set()
    while pending:
        folded = pending.pop()
        if folded not in reached:
            if folded not in table:
                raise ValueError(f"a lineage's table does not spell {folded}")
            reached.add(folded)
            if "~" in table[folded]:
                pending.extend(folded_atoms(table[folded]))
    if reached != set(table):
        raise ValueError("a lineage's table spells atoms no name reaches")
    return lineage

def _face_count(shape: Any) -> int:
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape

    faces = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(getattr(shape, "wrapped", shape), TopAbs_FACE, faces)
    return faces.Extent()


def effective_face_colors(shape: Any, colors: Any) -> dict:
    normalized = _normalized_face_colors(colors)
    if not normalized:
        return {}
    count = _face_count(shape)
    return {ordinal: color for ordinal, color in normalized.items() if ordinal <= count}

def _point_signature(shape: Any) -> tuple:
    """Exact native vertex records and referenced placements, scoped locally.

    Geometry table values intentionally stay outside the signature; their
    ordinary decoder normalization remains the existing v4 behavior. Native
    table indices preserve the point-to-geometry association. No pointer or
    native object escapes this call, and no approximate comparison is used.
    Placements compare as numbers, so a negative zero equals zero: a decoder
    recomposes a location chain whose product can carry the other sign (the
    w16 sump pan's rotated bosses), and that is the same placement.
    """
    from OCP.BinTools import BinTools_ShapeSet
    from OCP.TopAbs import TopAbs_VERTEX
    from OCP.TopExp import TopExp
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopTools import TopTools_IndexedMapOfShape

    local = getattr(shape, "wrapped", shape).Located(TopLoc_Location())
    vertices = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(local, TopAbs_VERTEX, vertices)
    point_vertices = [
        (i, vertices.FindKey(i)) for i in range(1, vertices.Extent() + 1)
        if not vertices.FindKey(i).TShape().Points().IsEmpty()
    ]
    if not point_vertices:
        return ()
    shape_set = BinTools_ShapeSet()
    shape_set.SetFormatNb(3)
    shape_set.SetWithTriangles(False)
    shape_set.SetWithNormals(False)
    shape_set.Add(local)

    def placement(location):
        transform = location.Transformation()
        # `+ 0.0` folds -0.0 into 0.0 and leaves every other value unchanged.
        return struct.pack(">12d", *(transform.Value(i, j) + 0.0 for i in range(1, 4) for j in range(1, 5)))

    result = []
    for ordinal, vertex in point_vertices:
        locations = []
        for point in vertex.TShape().Points():
            kinds = [point.IsPointOnCurve(), point.IsPointOnCurveOnSurface(), point.IsPointOnSurface()]
            if sum(kinds) != 1:
                raise CodecFidelityError("unsupported native point representation")
            locations.append(placement(point.Location()))
        stream = io.BytesIO()
        # ShapeSet strips each TShape's location before writing its geometry.
        # Keep the vertex occurrence placement separately and do the same here.
        shape_set.WriteShape(vertex.Located(TopLoc_Location()), stream)
        result.append((ordinal, placement(vertex.Location()), stream.getvalue(), tuple(locations)))
    return tuple(result)

def _binary_v3_bytes(shape: Any) -> bytes:
    from OCP.BinTools import BinTools, BinTools_FormatVersion
    from OCP.TopLoc import TopLoc_Location

    stream = io.BytesIO()
    BinTools.Write_s(
        getattr(shape, "wrapped", shape).Located(TopLoc_Location()), stream,
        False, False, BinTools_FormatVersion.BinTools_FormatVersion_VERSION_3,
    )
    return stream.getvalue()

def _ascii_v3_bytes(shape: Any) -> bytes:
    from OCP.BRepTools import BRepTools
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopTools import TopTools_FormatVersion

    stream = io.BytesIO()
    BRepTools.Write_s(
        getattr(shape, "wrapped", shape).Located(TopLoc_Location()), stream,
        False, False, TopTools_FormatVersion.TopTools_FormatVersion_VERSION_3,
    )
    return stream.getvalue()

class NativeShape:
    """A decoded component as the build pool's kernel work reads it: the bare
    ``TopoDS_Shape`` and its face colours. Topology facts, selector tables and
    meshes read only those, and a build123d wrapper would cost a worker that
    starts for one job most of its start: importing build123d."""

    __slots__ = ("wrapped", "cad_face_ordinal_colors")

    def __init__(self, wrapped: Any) -> None:
        self.wrapped = wrapped
        self.cad_face_ordinal_colors: dict = {}


def _decode_brep(codec: str, payload: bytes, *, native: bool = False) -> Any:
    if codec not in _BREP_HEADERS:
        raise ValueError(f"unsupported BREP codec: {codec}")
    if not isinstance(payload, bytes) or not payload.startswith(_BREP_HEADERS[codec]):
        raise ValueError(f"BREP header does not match declared codec {codec}")
    from OCP.BinTools import BinTools
    from OCP.TopAbs import TopAbs_VERTEX
    from OCP.TopoDS import TopoDS, TopoDS_Shape

    shape = TopoDS_Shape()
    if codec == "breptools-ascii-v3":
        from OCP.BRep import BRep_Builder
        from OCP.BRepTools import BRepTools

        BRepTools.Read_s(shape, io.BytesIO(payload), BRep_Builder())
    else:
        BinTools.Read_s(shape, io.BytesIO(payload))
    if shape.IsNull():
        raise RuntimeError("BREP payload deserialized to a null shape")
    if shape.ShapeType() == TopAbs_VERTEX:
        shape = TopoDS.Vertex_s(shape)
    return NativeShape(shape) if native else _build123d_shape_from_topods(shape)

def _encode_brep(shape: Any) -> dict[str, Any]:
    """Encode a canonical shape (:func:`_canonical_shape`): its bytes are
    :func:`_shape_brep_bytes`'s of the shape it was made from."""
    original = _raw_brep_bytes(shape)
    original_points = _point_signature(shape)
    try:
        private = _decode_brep("bintools-v4", original)
        if _point_signature(private) != original_points:
            raise CodecFidelityError("binary v4 changed exact native point records")
        return {"codec": "bintools-v4", "bytes": original,
                "private_decoded_shape": private}
    except Exception as binary_failure:
        failures = [f"binary v4: {binary_failure}"]
    for alternate_codec, writer in (("bintools-v3", _binary_v3_bytes),
                                    ("breptools-ascii-v3", _ascii_v3_bytes)):
        try:
            alternate = writer(shape)
            private = _decode_brep(alternate_codec, alternate)
            if _shape_brep_bytes(private) != original:
                raise CodecFidelityError("original native byte fidelity not proven")
            if writer(private) != alternate:
                raise CodecFidelityError("alternate read/write is not a byte fixed point")
            return {"codec": alternate_codec, "bytes": alternate,
                    "private_decoded_shape": private}
        except Exception as alternate_failure:
            failures.append(f"{alternate_codec}: {alternate_failure}")
    raise CodecFidelityError("; ".join(failures))


def prepare_geometry_component(shape: Any, *, face_colors: object = None,
                               identity: tuple[str | None, Mapping[str, Any] | None] | None = None) -> dict[str, Any]:
    """Capture a component's owned bytes, effective recipe and private native input.

    No store write occurs here. Ordinary v4 decoding preserves the existing
    canonical worker semantics; exact native point records fence the known
    parameter-loss cases. Alternate codecs require full original-native bytes
    and their own byte fixed point. Failure pins the live shape's topology
    facts explicitly (``topology``, the bytes an eager-only component's tree
    pins), never a falsely readable native entry. ``identity`` is what the run
    that made the shape names it by (``kernel_memo._Run.account``): its
    construction key and its lineage, which the entry carries as
    ``construction`` and ``lineage`` (the lineage's object, whose bytes ride the
    prepared component as ``lineage`` until it is published). Nothing here
    measures the shape's geometry for a name.
    """
    canonical = _canonical_shape(shape)
    try:
        encoded = _encode_brep(canonical)
        kind = "native"
    except CodecFidelityError:
        encoded = {"codec": "bintools-v4", "bytes": _raw_brep_bytes(canonical),
                   "private_decoded_shape": None}
        kind = "eager-only"
    private = encoded["private_decoded_shape"]
    declared = getattr(shape, "cad_face_ordinal_colors", None) if face_colors is None else face_colors
    colors = effective_face_colors(private if private is not None else shape, declared)
    beyond = sorted(set(_normalized_face_colors(declared)) - set(colors))
    if beyond:
        # A colour for a face the part does not have is a mistake, not a colour to drop
        # (README law 10): the ordinal counts the part's faces, 1 to their number.
        label = getattr(shape, "label", "") or "the part"
        raise ValueError(
            f"cad_face_ordinal_colors on {label!r}: no face {', '.join(map(str, beyond))}; its faces "
            f"are numbered 1 to {_face_count(private if private is not None else shape)}"
        )
    payload = encoded["bytes"]
    entry = {"kind": kind, "codec": encoded["codec"],
             "brep": hashlib.sha256(payload).hexdigest(), "faceColors": colors}
    topology = None
    if kind == "eager-only":
        from OCP.TopLoc import TopLoc_Location

        from cadgen._internal.topology_extract import extract_topology_facts
        from cadgen._internal.topology_facts import topology_facts_bytes

        topology = topology_facts_bytes(
            extract_topology_facts(getattr(shape, "wrapped", shape).Located(TopLoc_Location())))
        entry["topology"] = hashlib.sha256(topology).hexdigest()
    construction, lineage = identity if identity is not None else (None, None)
    lineage_payload = None
    if construction is not None:
        entry["construction"] = construction
    if lineage is not None:
        lineage_payload = lineage_bytes(lineage)
        entry["lineage"] = hashlib.sha256(lineage_payload).hexdigest()
    entry["contentHash"] = geometry_component_hash(
        entry["codec"], payload, colors, kind=kind, topology=entry.get("topology"),
        construction=entry.get("construction"), lineage=entry.get("lineage"),
    )
    if private is not None:
        private.cad_face_ordinal_colors = dict(colors)
    return {"entry": entry, "payload": payload, "shape": private, "topology": topology, "lineage": lineage_payload}


def prepare_published_component(shape: Any, *, face_colors: object = None,
                                identity: tuple[str | None, Mapping[str, Any] | None] | None = None,
                                payload: bytes | None = None) -> dict[str, Any]:
    """:func:`prepare_geometry_component` for a parsed prototype whose exact bytes
    may already be published, named by ``identity`` as the component it was read
    from is (a scene read from a stored tree carries it). ``payload`` is the
    prototype's bintools-v4 bytes, when the caller has serialized it already.

    A compile parses every prototype of a file again, and each one was encoded,
    privately decoded and fenced for point fidelity on every compile even when
    the exact bytes were published by the previous one. When the prototype's
    bintools-v4 bytes already exist as an object under a component entry that
    declares them native with this recipe, the codec fence was proven for
    exactly those bytes by the build that published them, so the decode that
    re-proves it is skipped and the prototype itself stands in as the prepared
    native input — a parsed shape is private to its parse, and its only
    consumer measures bounds without meshing. Anything else (a new or changed
    prototype, an eager-only or alternate-codec entry, a missing object) takes
    the ordinary path, and a forced build never calls this.
    """
    from cadgen.store.index import read_entry
    from cadgen.store.objects import has_object

    wrapped = getattr(shape, "wrapped", shape)
    payload = _shape_brep_bytes(wrapped) if payload is None else payload
    digest = hashlib.sha256(payload).hexdigest()
    construction, lineage = identity if identity is not None else (None, None)
    lineage_payload = lineage_bytes(lineage) if lineage is not None else None
    lineage_object = hashlib.sha256(lineage_payload).hexdigest() if lineage_payload is not None else None
    if has_object(digest) and (lineage_object is None or has_object(lineage_object)):
        colors = effective_face_colors(
            wrapped, getattr(shape, "cad_face_ordinal_colors", None) if face_colors is None else face_colors,
        )
        content = geometry_component_hash("bintools-v4", payload, colors, construction=construction,
                                          lineage=lineage_object)
        entry = {"kind": "native", "codec": "bintools-v4", "brep": digest, "faceColors": colors}
        for field, value in (("construction", construction), ("lineage", lineage_object)):
            if value is not None:
                entry[field] = value
        entry["contentHash"] = content
        indexed = read_entry("component", _component_id(content)) or {}
        published = {key: value for key, value in indexed.items() if key not in ("schemaVersion", "color")}
        if published and canonical_json_bytes(published) == canonical_json_bytes(entry):
            try:
                validate_geometry_component(entry, payload)
            except ValueError:
                return prepare_geometry_component(shape, face_colors=face_colors, identity=identity)
            prototype = _build123d_shape_from_topods(wrapped)
            prototype.cad_face_ordinal_colors = dict(colors)
            return {"entry": entry, "payload": payload, "shape": prototype, "topology": None, "lineage": lineage_payload}
    return prepare_geometry_component(shape, face_colors=face_colors, identity=identity)


def decode_geometry_component(entry: dict[str, Any], payload: bytes, *, native: bool = False) -> Any:
    """Privately reconstruct one verified geometry input, without display-data reads.
    ``native`` returns a :class:`NativeShape` rather than a build123d shape."""
    validate_geometry_component(entry, payload)
    if entry["kind"] == "eager-only":
        raise NativeUnavailable("eager-only component has no admitted native representation")
    try:
        shape = _decode_brep(entry["codec"], payload, native=native)
        colors = effective_face_colors(shape, entry["faceColors"])
    except MemoryError:
        raise
    except Exception as exc:
        # Hash/header integrity is independent of native readability. OCP
        # failures use several exception types; readers treat them uniformly
        # as an invalid encoded input and can repair from the selected STEP.
        raise ValueError(f"unreadable {entry['codec']} geometry payload") from exc
    if canonical_json_bytes(colors) != canonical_json_bytes(entry["faceColors"]):
        raise ValueError("intrinsic recipe names an absent native face")
    shape.cad_face_ordinal_colors = colors
    return shape


def decoded_input(entry: dict[str, Any], payload: bytes) -> NativeShape:
    """The native input :func:`prepare_geometry_component` returned with a native ``entry``
    and ``payload`` it prepared (its ``shape``), decoded again from that payload as it was
    decoded then, as the bare kernel shape and its face colours: for a component prepared,
    checked and published before, whose bytes are its own."""
    shape = _decode_brep(entry["codec"], payload, native=True)
    shape.cad_face_ordinal_colors = dict(entry["faceColors"])
    return shape


def decode_display_shape(entry: dict[str, Any], payload: bytes, *, native: bool = False) -> Any:
    """A shape to mesh for display (``cadgen._internal.tessellate``).

    A native component is :func:`decode_geometry_component`. An eager-only
    component's bytes failed the exact round-trip fence, so they never stand in
    for its geometry -- but they decode, with the same topology and ordinals as
    the topology facts taken from the live shape, and a display mesh tolerates
    the known point-parameter loss the fence exists for.
    """
    if entry["kind"] != "eager-only":
        return decode_geometry_component(entry, payload, native=native)
    validate_geometry_component(entry, payload)
    try:
        return _decode_brep(entry["codec"], payload, native=native)
    except MemoryError:
        raise
    except Exception as exc:
        raise ValueError(f"unreadable {entry['codec']} geometry payload") from exc


def validate_geometry_component(entry: Any, payload: bytes, *, cid: str | None = None) -> None:
    """Kernel-free verification of a complete encoded geometry input."""
    if type(entry) is not dict or entry.get("kind") not in COMPONENT_KINDS or entry.get("codec") not in GEOMETRY_CODECS:
        raise ValueError("unsupported geometry component")
    allowed = {"kind", "codec", "brep", "faceColors", "contentHash", "color", "construction", "lineage"}
    if entry["kind"] == "eager-only":
        allowed.add("topology")
    if set(entry) - allowed:
        raise ValueError("unknown geometry component field")
    if not isinstance(payload, bytes) or not payload.startswith(_BREP_HEADERS[entry["codec"]]):
        raise ValueError("BREP header does not match declared codec")
    if hashlib.sha256(payload).hexdigest() != entry.get("brep"):
        raise ValueError("geometry BREP object identity mismatch")
    colors = _normalized_face_colors(entry.get("faceColors"))
    if canonical_json_bytes(colors) != canonical_json_bytes(entry.get("faceColors")):
        raise ValueError("noncanonical intrinsic appearance recipe")
    full = geometry_component_hash(entry["codec"], payload, colors, kind=entry["kind"],
                                   topology=entry.get("topology"), construction=entry.get("construction"),
                                   lineage=entry.get("lineage"))
    if entry.get("contentHash") != full or (cid is not None and cid != full[:16]):
        raise ValueError("geometry input identity mismatch")
