"""A component's display data -- its surface, selector table and display mesh -- derived
from captured immutable geometry inputs, and read back."""
from __future__ import annotations

import contextlib
import hashlib
import json
import struct
from functools import lru_cache
from typing import Any, Callable

from cadgen.store.index import read_entry, write_entry
from cadgen.store.objects import put_object, read_verified_object

# 20: a .surf carries no tessellation inputs (SURF format 3).
EXTRACTION_SCHEME = 20
# _internal/surface_extract.SURF_VERSION, without importing the kernel here.
SURF_FORMAT = 3
# Format 2 surfaces stay readable: an older build may have pinned one in an
# eager-only component's geometry identity, and format 3 only removed fields.
SURF_FORMATS_READ = (2, 3)
SURFACE_SCHEMA = 1


class SurfaceProducerUnavailable(ValueError):
    """This runtime cannot implement the surface producer the request pins.

    A caller that holds a replacement producer recovers from this and only
    this; every other failure is a failure. Routing it by a substring of the
    message, retyped at each catch site, made that distinction a typo away
    from wrong, so the refusal has a type and one declared marker text.
    """

    MARKER = "worker cannot implement the request's pinned surface producer"

    def __init__(self, message: str = MARKER) -> None:
        super().__init__(message)


def producer_unavailable(error: object) -> bool:
    """Whether ``error`` is that refusal, however it reached the caller.

    In-process the type answers. Derivation also runs in a POOLED WORKER, and
    a worker's refusal arrives as an ArtifactJobError carrying its text, so the
    marker is matched too -- one constant, declared beside the raise.
    """
    return isinstance(error, SurfaceProducerUnavailable) or SurfaceProducerUnavailable.MARKER in str(error)


def validate_producer(value: Any) -> None:
    expected = {"scheme", "surfFormat", "build123d", "ocp", "cadqueryOcp"}
    if type(value) is not dict or set(value) not in (expected, expected | {"producerKey"}) or type(value.get("scheme")) is not int or type(value.get("surfFormat")) is not int or value.get("scheme") != EXTRACTION_SCHEME or value.get("surfFormat") != SURF_FORMAT:
        raise ValueError("unsupported surface producer")
    for field in ("build123d", "ocp", "cadqueryOcp"):
        text = value[field]
        if type(text) is not str or not text.strip() or "unknown" in text.lower():
            raise ValueError("surface producer requires known runtime identity")
    if "producerKey" in value and value["producerKey"] != producer_key(value):
        raise ValueError("surface producer key mismatch")


def producer_key(value: dict) -> str:
    from cadgen._internal.component_package import canonical_json_bytes as canonical_bytes
    definition = {key: item for key, item in value.items() if key != "producerKey"}
    validate_producer(definition)
    return hashlib.sha256(b"cadgen-surface-producer-v1\0" + canonical_bytes(definition)).hexdigest()


def producer_fields(value: dict) -> dict:
    validate_producer(value)
    return {key: item for key, item in value.items() if key != "producerKey"}


@lru_cache(maxsize=1)
def kernel_versions() -> tuple[str, str, str]:
    """The installed build123d, the loaded OCP and cadquery-ocp-novtk versions:
    who produced a derived fact (an extracted surface, a measured box).

    OCP.__version__ is exported by the native extension, not inferred from
    build123d. build123d's is its installed metadata: importing build123d to
    read it was most of the start of a worker that derives and meshes, which
    never imports build123d otherwise (``component_package.NativeShape``). An
    unknown version is a ValueError: a derived fact must never share an
    identity with an unrelated kernel build.
    """
    from importlib.metadata import PackageNotFoundError, version

    import OCP

    try:
        build123d, distribution = version("build123d"), version("cadquery-ocp-novtk")
    except PackageNotFoundError as error:
        raise ValueError("a derived fact needs the build123d and cadquery-ocp-novtk distributions") from error
    versions = (build123d, getattr(OCP, "__version__", None), distribution)
    if any(not isinstance(value, str) or not value.strip() or "unknown" in value.lower() for value in versions):
        raise ValueError("a derived fact needs known build123d and OCP versions")
    return versions


def producer_identity() -> dict:
    build123d, ocp, distribution = kernel_versions()
    identity = {"scheme": EXTRACTION_SCHEME, "surfFormat": SURF_FORMAT,
                "build123d": build123d, "ocp": ocp, "cadqueryOcp": distribution}
    validate_producer(identity)
    return identity


def _pinned_surface_input(surface_object: str, surf_format: int) -> str:
    return hashlib.sha256(b"cadgen-pinned-surface-input-v1\0" + surface_object.encode()
                          + b"\0" + str(surf_format).encode()).hexdigest()


def surface_input(entry: dict, producer: dict) -> str:
    from cadgen._internal.component_package import canonical_json_bytes as canonical_bytes
    if entry.get("kind") == "eager-only":
        return _pinned_surface_input(entry["eagerSurface"], SURF_FORMAT)
    definition = {"kind": "native", "contentHash": entry["contentHash"],
                  "brepObject": entry["brep"], "codec": entry["codec"],
                  "faceColors": entry["faceColors"], "producerKey": producer_key(producer)}
    return hashlib.sha256(b"cadgen-surface-input-v1\0" + canonical_bytes(definition)).hexdigest()


def obsolete_entry(key: str, entry: Any) -> bool:
    """Whether a surface entry is an older extraction's than this cadgen's: no
    reader asks for it again (STORE.md §8). Its producer's extraction scheme and
    SURF format are no newer than this cadgen's and not both the same; an
    eager-only entry, which names no producer, was keyed under an older SURF
    format. A newer cadgen's is not."""
    if type(entry) is not dict:
        return False
    producer = entry.get("producer")
    if type(producer) is dict:
        scheme, surf_format = producer.get("scheme"), producer.get("surfFormat")
        if type(scheme) is not int or type(surf_format) is not int:
            return False
        return (scheme <= EXTRACTION_SCHEME and surf_format <= SURF_FORMAT
                and (scheme, surf_format) != (EXTRACTION_SCHEME, SURF_FORMAT))
    pinned = entry.get("object")
    return (producer is None and isinstance(pinned, str)
            and any(key == _pinned_surface_input(pinned, older) for older in range(1, SURF_FORMAT)))


def validate_surface_bytes(payload: bytes) -> dict:
    if len(payload) < 12 or payload[:4] != b"SURF":
        raise ValueError("invalid SURF container")
    version, size = struct.unpack_from("<II", payload, 4)
    if version not in SURF_FORMATS_READ or size > len(payload) - 12 or (len(payload) - 12 - size) % 4:
        raise ValueError("invalid SURF version/length")
    index = json.loads(payload[12:12 + size])
    if type(index) is not dict or index.get("version") != version:
        raise ValueError("invalid SURF index")
    for name in ("faces", "edges"):
        rows = index.get(name)
        if type(rows) is not list or any(type(row) is not dict for row in rows) or [row.get("ord") for row in rows] != list(range(1, len(rows) + 1)):
            raise ValueError("invalid SURF ordinal table")
        if type(index.get("counts")) is not dict or index["counts"].get(name) != len(rows):
            raise ValueError("invalid SURF counts")
    return index


def _expected(entry: dict, producer: dict) -> dict:
    return {"schemaVersion": SURFACE_SCHEMA, "surfaceInput": surface_input(entry, producer),
            "component": entry["contentHash"], "brep": entry["brep"],
            "codec": entry["codec"], "faceColors": entry["faceColors"],
            "producer": None if entry["kind"] == "eager-only" else producer_fields(producer)}


def request_view(tree_hash: str, *, producer: dict | None = None) -> dict:
    """Owned request descriptor; never written into canonical geometry."""
    from cadgen.store.trees import capture_tree as capture
    descriptor, _ = capture(tree_hash, retain_payloads=False)
    return _view_from_geometry(tree_hash, descriptor, producer=producer)


def _view_id(tree_hash: str, producer: dict) -> str:
    key = producer_key(producer)
    return hashlib.sha256(
        b"cadgen-runtime-view-v1\0" + tree_hash.encode() + b"\0" + key.encode()
    ).hexdigest()


def _view_from_geometry(tree_hash: str, descriptor: dict, *, producer: dict | None = None) -> dict:
    """Consume an owned descriptor from this call's verified geometry capture.

    Internal metadata paths use this after verification to avoid rereading the
    same closure. Callers retaining canonical metadata pass a private copy.
    """
    selected = producer_identity() if producer is None else producer_fields(producer)
    key = producer_key(selected)
    descriptor["viewSchemaVersion"] = 1
    descriptor["tree"] = tree_hash
    descriptor["viewId"] = _view_id(tree_hash, selected)
    descriptor["surfaceProducer"] = {**selected, "producerKey": key}
    for entry in descriptor["components"].values():
        entry["surfaceInput"] = surface_input(entry, selected)
        entry["brepObject"] = entry.pop("brep")
    return descriptor


def lookup(entry: dict, producer: dict) -> dict | None:
    from cadgen._internal.component_package import canonical_json_bytes as canonical_bytes
    expected = _expected(entry, producer)
    actual = read_entry("surface", expected["surfaceInput"])
    if not actual or set(actual) != {*expected, "object"}:
        return None
    try:
        if any(canonical_bytes(actual[field]) != canonical_bytes(value) for field, value in expected.items()):
            return None
        validate_surface_bytes(read_verified_object(actual["object"]))
    except (OSError, ValueError, TypeError, KeyError, struct.error):
        return None
    return actual


def display_records(entry: dict, producer: dict) -> dict | None:
    """One component's display data as the store holds it -- its verified surface record,
    selector table and display mesh (``{"surface", "selectors", "mesh"}``) -- or None
    while any is missing. A read: nothing is derived."""
    from cadgen.store import meshes, selectors

    surface = lookup(entry, producer)
    if surface is None:
        return None
    table = selectors.probe(selectors.selector_key(surface["surfaceInput"]))
    mesh = meshes.probe(meshes.tessellation_key(surface["surfaceInput"]))
    if table is None or mesh is None or table["surfaceObject"] != surface["object"]:
        return None
    return {"surface": surface, "selectors": table, "mesh": mesh}


def _display_stored(entry: dict, producer: dict) -> bool:
    """Whether the store holds the component's display data: its surface entry and
    object, the selector table bound to that object, and the display mesh. Probes
    only, with no body read and no kernel import: a view verifies what it serves
    (``display_records``) and has derived again what does not verify."""
    from cadgen.store import meshes, selectors
    from cadgen.store.objects import is_object_hash, object_path

    key = surface_input(entry, producer)
    surface = read_entry("surface", key)
    digest = surface.get("object") if type(surface) is dict else None
    if not is_object_hash(digest) or not object_path(digest).is_file():
        return False
    table = selectors.probe(selectors.selector_key(key))
    return (table is not None and table["surfaceObject"] == digest
            and meshes.probe(meshes.tessellation_key(key)) is not None)


@contextlib.contextmanager
def _meshing():
    """Whatever fails inside, as the ``MeshProductionError`` that names it (its type
    and message): one component's failure, which its caller reports once the rest
    of the request is stored. An interrupt is not one."""
    from cadgen._internal.occt_mesh import MeshProductionError

    try:
        yield
    except Exception as error:  # noqa: BLE001 - the error is the component's, reported by name
        raise MeshProductionError(f"{type(error).__name__}: {error}") from error


def _mesh_body(entry: dict, surface: dict, chord: float, angle: float) -> bytes:
    """The component's GLB body at ``chord``/``angle``, from its stored surface and
    BREP. Meshing stores its triangulation on the shape, so it meshes a fresh private
    decode. Any failure -- reading the SURF or BREP, OCCT, the body's encoding -- is
    a ``MeshProductionError`` (``_meshing``)."""
    from cadgen._internal.component_package import decode_display_shape
    from cadgen._internal.occt_mesh import mesh_component
    from cadgen._internal.surf_container import read_surf

    with _meshing():
        index, _floats = read_surf(read_verified_object(surface["object"]))
        shape = decode_display_shape(entry, read_verified_object(entry["brep"]), native=True)
        return mesh_component(getattr(shape, "wrapped", shape), index, surface_input=surface["surfaceInput"],
                              surface_object=surface["object"], chord=chord, angle=angle)


def _derive_mesh(entry: dict, surface: dict, keep_going: Callable[[], bool] | None) -> bool:
    """Store the component's display mesh when the store lacks it; False when told to
    stop first. ``keep_going``'s stop is not a failure, and its own error is the job's."""
    from cadgen.store import meshes

    key = meshes.tessellation_key(surface["surfaceInput"])
    if meshes.probe(key) is not None:
        return True
    if keep_going is not None and not keep_going():
        return False
    body = _mesh_body(entry, surface, meshes.DISPLAY_CHORD, meshes.DISPLAY_ANGLE)
    with _meshing():
        meshes.write(key, body)
    return True


def _derive_selectors(entry: dict, surface: dict, shape: Any = None) -> None:
    """Store the component's selector table (``_internal/selector_table``) when the
    store lacks it. It is the surface's own derivation, a few milliseconds after
    the extraction, so nothing asks ``keep_going`` again between the two.
    ``shape`` is the decoded component when the caller has it in hand (a fresh
    extraction); otherwise the BREP is decoded again, as a mesh decodes it. Any
    failure is a ``MeshProductionError`` naming it (``_meshing``), reported with
    the component's."""
    from cadgen._internal.component_package import decode_display_shape
    from cadgen._internal.selector_table import build_selector_table, selector_table_bytes
    from cadgen._internal.surf_container import read_surf
    from cadgen.store import selectors

    key = selectors.selector_key(surface["surfaceInput"])
    if selectors.probe(key) is not None:
        return
    with _meshing():
        index, _floats = read_surf(read_verified_object(surface["object"]))
        if shape is None:
            shape = decode_display_shape(entry, read_verified_object(entry["brep"]), native=True)
        table = build_selector_table(getattr(shape, "wrapped", shape), index)
        selectors.write(key, surface["object"], selector_table_bytes(table))


def _geometry_entry(surface: dict) -> dict:
    """The geometry input a verified surface record was derived from (its BREP and recipe)."""
    entry = {"kind": "native", "contentHash": surface["component"], "brep": surface["brep"],
             "codec": surface["codec"], "faceColors": surface["faceColors"]}
    if surface["producer"] is None:
        entry.update(kind="eager-only", eagerSurface=surface["object"])
    return entry


def mesh_at(surface_key: str, chord: float, angle: float) -> bytes:
    """The GLB body of the component whose surface ``surface_key`` names, at
    ``chord``/``angle``, meshed in this process and stored nowhere: a mesh export at a
    tolerance other than the display one (``_internal/mesh_export.py``), whose file is
    the output. The surface record names the BREP it was derived from, so no tree is read."""
    surface = read_entry("surface", surface_key)
    validate_surface_record(surface, surface_input_key=surface_key)
    return _mesh_body(_geometry_entry(surface), surface, chord, angle)


def _derive_component(entry: dict, producer: dict, *, force: bool = False, expected_object: str | None = None,
                      keep_going: Callable[[], bool] | None = None) -> dict | None:
    """Store whatever of one component's display data the store lacks -- its surface,
    selector table and display mesh -- and return its surface record; None when
    ``keep_going`` said stop. A failure of its table or mesh is the
    ``MeshProductionError`` naming it."""
    from cadgen._internal.component_package import decode_geometry_component
    from cadgen._internal.surface_extract import extract_surface_component

    expected = _expected(entry, producer)
    prior = lookup(entry, producer)
    if expected_object is None and force and prior is not None:
        expected_object = prior["object"]
    shape = None
    if entry["kind"] == "eager-only":
        payload = read_verified_object(entry["eagerSurface"])
        validate_surface_bytes(payload)
        actual = {**expected, "object": entry["eagerSurface"]}
    else:
        actual = None if force else prior
        if actual is None:
            if keep_going is not None and not keep_going():
                return None
            shape = decode_geometry_component(entry, read_verified_object(entry["brep"]), native=True)
            payload = extract_surface_component(shape.wrapped, face_colors=shape.cad_face_ordinal_colors)
            validate_surface_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            if expected_object is not None and digest != expected_object:
                raise ValueError("surface producer conflict for the pinned input")
            actual = {**expected, "object": put_object(payload, repair=True)}
    if expected_object is not None and actual["object"] != expected_object:
        raise ValueError("surface producer conflict for the pinned input")
    # The complete verified object is durable first; failure earlier cannot
    # create a readiness marker. Concurrent writers derive identical bytes.
    # A hit is a read and writes nothing (STORE.md §8).
    if actual != prior:
        write_entry("surface", expected["surfaceInput"], actual)
    _derive_selectors(entry, actual, shape=shape)
    return actual if _derive_mesh(entry, actual, keep_going) else None


def _captured(tree_hash: str, producer: dict | None) -> tuple[dict, dict]:
    """The tree's verified descriptor, and the producer named (this runtime's when None)."""
    from cadgen.store.trees import capture_tree as capture

    # Surface work admits only a complete verified geometry graph, but retains none
    # of its BREPs: each derivation reads the exact CAS payload it decodes.
    descriptor, _ = capture(tree_hash, retain_payloads=False)
    return descriptor, producer_identity() if producer is None else producer_fields(producer)


def derive(tree_hash: str, cids: list[str] | None = None, *, force: bool = False,
           expected_objects: dict[str, str] | None = None, producer: dict | None = None,
           keep_going: Callable[[], bool] | None = None) -> dict:
    """Derive the display data of ``cids`` (every component when None) in this process
    and return their surface records: each component's surface, its selector table
    (``cadgen.store.selectors``: the refs, facts and connected sets the page and the CLI
    resolve a pick by, minted here once from the exact BREP) and its mesh at the display
    tolerance (``cadgen.store.meshes``: what the CAD Viewer, snapshots and display-tolerance
    mesh exports draw), each where the store lacks it.

    ``keep_going``, when given, is asked before each extraction and each mesh: False stops
    there, and the result holds the components done so far (a daemon worker asks whether
    anyone still wants its job, ``daemon/worker.py``). A component whose table or mesh
    fails, however it fails, does not stop the others: they are stored, then
    ``MeshProductionError`` names each failure (the component, and the error's type and
    message).
    """
    from cadgen._internal.occt_mesh import MeshProductionError

    descriptor, producer = _captured(tree_hash, producer)
    components = descriptor["components"]
    if producer != producer_identity():
        raise SurfaceProducerUnavailable()
    requested = list(components) if cids is None else list(dict.fromkeys(cids))
    if any(cid not in components for cid in requested):
        raise ValueError("surface request names an unpinned component")
    result, unmeshed = {}, []
    for cid in requested:
        entry = components[cid]
        try:
            actual = _derive_component(entry, producer, force=force, keep_going=keep_going,
                                       expected_object=(expected_objects or {}).get(surface_input(entry, producer)))
        except MeshProductionError as error:
            # One component's failure leaves the rest of the request to be done: they are
            # stored before the failure is reported, so a retry finds them.
            unmeshed.append(f"component {cid}: {error}")
            continue
        if actual is None:
            break
        result[cid] = actual
    if unmeshed:
        raise MeshProductionError("; ".join(unmeshed))
    return result


def ensure_display(tree_hash: str, *, producer: dict | None = None,
                   progress: Callable[[int, int], None] | None = None) -> list[str]:
    """Derive in this process the display data of every component of ``tree_hash`` the
    store lacks, and return a line for each component that could not be derived, naming
    the occurrences that place it and the error. A build does this before it reports done
    (``_internal/generation.py``), so a view of what it built only reads.

    Which components lack it is asked of the store alone (``_display_stored``): a tree
    whose display data is all stored imports no kernel. ``producer`` is the one a view of
    the tree selects (its document's), this runtime's when None. ``progress(done, total)``
    hears how many of the missing components are done, before each one and at the end."""
    descriptor, producer = _captured(tree_hash, producer)
    components = descriptor["components"]
    missing = [cid for cid, entry in components.items() if not _display_stored(entry, producer)]
    if not missing:
        return []
    if producer != producer_identity():
        raise SurfaceProducerUnavailable()
    failures = []
    for done, cid in enumerate(missing):
        if progress is not None:
            progress(done, len(missing))
        try:
            _derive_component(components[cid], producer)
        except Exception as error:  # noqa: BLE001 - one part's failure is reported, the rest derive
            failures.append((cid, f"{type(error).__name__}: {error}"))
    if progress is not None:
        progress(len(missing), len(missing))
    if not failures:
        return []
    from cadgen.store.meshes import placed_parts

    placed: dict[str, list[tuple[str, str]]] = {}
    for row in descriptor.get("occurrences") or ():
        if isinstance(row, dict) and row.get("component") and row.get("id"):
            placed.setdefault(str(row["component"]), []).append((str(row["id"]), str(row.get("name") or "")))
    return [f"{placed_parts(placed[cid]) if placed.get(cid) else f'part {cid}'} could not be meshed ({error})"
            for cid, error in failures]


def validate_surface_record(value: Any, *, surface_input_key: str | None = None) -> None:
    """Verify a surface index independently of geometry-object availability.

    The index owns its surface; its geometry fields describe the immutable
    derivation input but do not add GC ownership of a BREP or geometry tree.
    """
    from cadgen._internal.component_package import _normalized_face_colors, canonical_json_bytes
    from cadgen.store.objects import is_object_hash

    fields = {"schemaVersion", "surfaceInput", "component", "brep", "codec", "faceColors", "producer", "object"}
    if type(value) is not dict or set(value) != fields or value["schemaVersion"] != SURFACE_SCHEMA:
        raise ValueError("invalid surface index")
    if any(not is_object_hash(value[field]) for field in ("surfaceInput", "component", "brep", "object")):
        raise ValueError("invalid surface index identity")
    from cadgen._internal.component_package import GEOMETRY_CODECS
    if value["codec"] not in GEOMETRY_CODECS:
        raise ValueError("invalid surface geometry codec")
    colors = _normalized_face_colors(value["faceColors"])
    if canonical_json_bytes(colors) != canonical_json_bytes(value["faceColors"]):
        raise ValueError("invalid surface appearance recipe")
    entry = {"kind": "native", "contentHash": value["component"], "brep": value["brep"],
             "codec": value["codec"], "faceColors": value["faceColors"]}
    if value["producer"] is None:
        entry.update(kind="eager-only", eagerSurface=value["object"])
    else:
        validate_producer(value["producer"])
    expected = surface_input(entry, value["producer"])
    if value["surfaceInput"] != expected or (surface_input_key is not None and expected != surface_input_key):
        raise ValueError("surface index input mismatch")
    validate_surface_bytes(read_verified_object(value["object"]))
