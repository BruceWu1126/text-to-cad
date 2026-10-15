"""A part's display data: ONE index record per part, naming its two objects -- the display
mesh (a GLB body, ``cadgen.store.meshes``) and the selector table
(``cadgen._internal.selector_table``) -- derived from captured immutable geometry inputs,
and read back.

A part is a component under one producer, the kernel that derives it. Its key,
``index/part/<digest>-v<PART_VERSION>``, carries no tolerance: the digest hashes the
component's geometric identity -- its **fingerprint** (``_internal/geometry_fingerprint``,
README law 20), its intrinsic face colours keyed by face token, and the producer; never
its ``contentHash``, so the body a build holds and the same body read back from the STEP
it wrote (different bytes, one fingerprint) are one part. An eager-only component, whose
bytes cannot stand for its shape, hashes its pinned topology facts in place of a
fingerprint, its colours by ordinal. The version is bumped by any change to the
extractor, the selector table, the mesher, the verifier, the GLB payload or the
fingerprint scheme, or in how the digest is hashed.
The record is ``{"glb": {...}, "selectors": {...}}``: each object's address and byte
length and what a reader sizes it by without opening it -- the mesh's decoded-bytes
estimate and counts (and ``unmeshedFaceCount`` when a face could not be meshed), the
table's counts. One probe answers whether a part's display data is stored.

Derivation extracts the component's topology facts in memory (an eager-only component's
are the object its tree pins), builds the selector table from them, meshes the component
from the table and its face colours, writes the two objects and then the record, so a
record always names complete objects. Nothing stores the facts. A mesh export at another
tolerance meshes from the stored table the same way and stores nothing (``mesh_at``).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
from functools import lru_cache
from typing import Any, Callable

from cadgen.store.index import entry_path, versioned_key, write_entry
from cadgen.store.objects import is_object_hash, object_path, put_object, read_verified_object

# 1: one record per part names its display GLB and its selector table (the surface,
# selector and mesh kinds before it are retired).
# 2: the producer key's tag and the selector table's edge-line algorithm name a producer
# and edge lines: every part has a new key, and its table new bytes.
# 3: the mesher measures each cylinder, cone, sphere and torus against its surface and
# meshes a part again where one strays: a part whose round OCCT gridded off it has a new mesh.
# 4: cadgen tessellates every part itself, as the CAD Viewer's tessellator drew it before
# meshes moved to the store (``_internal/tessellate.py``): every part has a new mesh.
# 5: the tessellator runs as cadgen's native library, evaluating its B-splines and conics
# itself: a part's mesh may differ from its version-4 mesh in its last bits.
# 6: a part is keyed by its component's geometry fingerprint, not its bytes, and its selector
# table names every face, edge and vertex by a geometric token (README law 20): every part
# has a new key and its table new bytes.
PART_VERSION = 6
INDEX_KIND = "part"
MAX_INDEX_BYTES = 16 * 1024
_KEY = re.compile(rf"[0-9a-f]{{64}}-v{PART_VERSION}")
# Any version's key, this cadgen's or another's (``obsolete_key``).
_ANY_KEY = re.compile(r"[0-9a-f]{64}-v(\d+)")
ROLES = ("glb", "selectors")
_GLB_FIELDS = {"object", "byteLength", "decodedBytes", "vertexCount", "indexCount", "faceCount", "edgeCount",
               "edgePointCount"}
_UNMESHED_COUNT = "unmeshedFaceCount"
_SELECTOR_FIELDS = {"object", "byteLength", "faceCount", "edgeCount", "vertexCount"}
_PRODUCER_FIELDS = {"build123d", "ocp", "cadqueryOcp"}


class ProducerUnavailable(ValueError):
    """This runtime cannot implement the producer the request pins.

    A caller that holds a replacement producer recovers from this and only
    this; every other failure is a failure. Routing it by a substring of the
    message, retyped at each catch site, made that distinction a typo away
    from wrong, so the refusal has a type and one declared marker text.
    """

    MARKER = "worker cannot implement the request's pinned producer"

    def __init__(self, message: str = MARKER) -> None:
        super().__init__(message)


def producer_unavailable(error: object) -> bool:
    """Whether ``error`` is that refusal, however it reached the caller.

    In-process the type answers. Derivation also runs in a POOLED WORKER, and
    a worker's refusal arrives as an ArtifactJobError carrying its text, so the
    marker is matched too -- one constant, declared beside the raise.
    """
    return isinstance(error, ProducerUnavailable) or ProducerUnavailable.MARKER in str(error)


def validate_producer(value: Any) -> None:
    if type(value) is not dict or set(value) not in (_PRODUCER_FIELDS, _PRODUCER_FIELDS | {"producerKey"}):
        raise ValueError("unsupported producer")
    for field in _PRODUCER_FIELDS:
        text = value[field]
        if type(text) is not str or not text.strip() or "unknown" in text.lower():
            raise ValueError("producer requires known runtime identity")
    if "producerKey" in value and value["producerKey"] != producer_key(value):
        raise ValueError("producer key mismatch")


def producer_key(value: dict) -> str:
    from cadgen._internal.component_package import canonical_json_bytes as canonical_bytes
    definition = {key: item for key, item in value.items() if key != "producerKey"}
    validate_producer(definition)
    # The tag's bytes are in every producer key, part key and view id: a new spelling
    # re-keys every stored part, and moves PART_VERSION with it so the parts at the
    # keys before it are obsolete (``obsolete_key``) and the sweeper retires them.
    return hashlib.sha256(b"cadgen-producer-v1\0" + canonical_bytes(definition)).hexdigest()


def producer_fields(value: dict) -> dict:
    validate_producer(value)
    return {key: item for key, item in value.items() if key != "producerKey"}


@lru_cache(maxsize=1)
def kernel_versions() -> tuple[str, str, str]:
    """The installed build123d, the loaded OCP and cadquery-ocp-novtk versions:
    who produced a derived fact (a part's display data, a measured box).

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
    identity = {"build123d": build123d, "ocp": ocp, "cadqueryOcp": distribution}
    validate_producer(identity)
    return identity


def part_key(entry: dict, producer: dict) -> str:
    """The key of ``entry``'s part (a tree's component entry) under ``producer``."""
    return _part_key(entry, producer_key(producer))


def geometric_key_material(entry: dict) -> tuple[str, dict]:
    """What names ``entry``'s part geometrically: its fingerprint (an eager-only
    component's: the object its topology facts are pinned as) and its intrinsic
    face colours keyed by face token (by ordinal for an eager-only component,
    whose tokens are its facts')."""
    from cadgen._internal.component_package import _normalized_face_colors

    colors = _normalized_face_colors(entry.get("faceColors"))
    if entry.get("kind") == "eager-only":
        return f"topology:{entry['topology']}", {str(ordinal): list(color) for ordinal, color in colors.items()}
    tokens = {str(ordinal): token for ordinal, token in (entry.get("faceTokens") or {}).items()}
    if set(tokens) != {str(ordinal) for ordinal in colors}:
        raise ValueError("a part's face colours need the tokens of the faces they colour")
    return f"fingerprint:{entry['fingerprint']}", {tokens[str(ordinal)]: list(color) for ordinal, color in colors.items()}


def _part_key(entry: dict, producer: str) -> str:
    """``part_key`` under the producer whose key is ``producer``."""
    from cadgen._internal.component_package import canonical_json_bytes

    identity, colors = geometric_key_material(entry)
    digest = hashlib.sha256(b"cadgen-part\0" + identity.encode() + b"\0" + canonical_json_bytes(colors) + b"\0"
                            + producer.encode()).hexdigest()
    return versioned_key(digest, PART_VERSION)


def valid_key(key: Any) -> bool:
    return isinstance(key, str) and _KEY.fullmatch(key) is not None


def obsolete_key(key: Any) -> bool:
    """Whether a part entry's key is an older version's than this cadgen's: no reader asks
    for it again (STORE.md §8). A newer cadgen's is not."""
    match = _ANY_KEY.fullmatch(key) if isinstance(key, str) else None
    return match is not None and int(match[1]) < PART_VERSION


def _view_id(tree_hash: str, producer: dict) -> str:
    key = producer_key(producer)
    return hashlib.sha256(
        b"cadgen-runtime-view-v1\0" + tree_hash.encode() + b"\0" + key.encode()
    ).hexdigest()


def view_from_geometry(tree_hash: str, descriptor: dict, *, producer: dict | None = None) -> dict:
    """Consume an owned descriptor from this call's verified geometry capture: each
    component gains its part key (``part``), and the view its id and producer.

    Internal metadata paths use this after verification to avoid rereading the
    same closure. Callers retaining canonical metadata pass a private copy.
    """
    selected = producer_identity() if producer is None else producer_fields(producer)
    key = producer_key(selected)
    descriptor["viewSchemaVersion"] = 1
    descriptor["tree"] = tree_hash
    descriptor["viewId"] = _view_id(tree_hash, selected)
    descriptor["producer"] = {**selected, "producerKey": key}
    for entry in descriptor["components"].values():
        entry["part"] = _part_key(entry, key)
        entry["brepObject"] = entry.pop("brep")
    return descriptor


def geometry_entry(view_entry: dict) -> dict:
    """The canonical tree entry behind a view's component (``view_from_geometry``)."""
    entry = {"kind": view_entry["kind"], "codec": view_entry["codec"], "brep": view_entry["brepObject"],
             "faceColors": view_entry["faceColors"], "contentHash": view_entry["contentHash"]}
    if view_entry["kind"] == "eager-only":
        entry["topology"] = view_entry["topology"]
    else:
        entry["fingerprint"] = view_entry["fingerprint"]
        if view_entry.get("faceTokens"):
            entry["faceTokens"] = view_entry["faceTokens"]
    return entry


def _counts(value: Any, fields: set[str]) -> bool:
    return all(type(value[name]) is int and value[name] >= 0 for name in fields - {"object"})


def _valid_record(record: Any) -> bool:
    """A part record's shape: ``{"glb": {...}, "selectors": {...}}`` (module docstring)."""
    from cadgen.store.meshes import decoded_bytes

    try:
        if type(record) is not dict or set(record) != set(ROLES):
            return False
        glb, table = record["glb"], record["selectors"]
        if (type(glb) is not dict or set(glb) not in (_GLB_FIELDS, _GLB_FIELDS | {_UNMESHED_COUNT})
                or not is_object_hash(glb["object"]) or not _counts(glb, set(glb))):
            return False
        if _UNMESHED_COUNT in glb and not 0 < glb[_UNMESHED_COUNT] <= glb["faceCount"]:
            return False
        if (glb["byteLength"] < 20 or glb["byteLength"] % 4 or glb["decodedBytes"] != decoded_bytes(glb)
                or glb["edgePointCount"] < 2 * glb["edgeCount"]
                or (glb["edgeCount"] == 0) != (glb["edgePointCount"] == 0)):
            return False
        return (type(table) is dict and set(table) == _SELECTOR_FIELDS and is_object_hash(table["object"])
                and _counts(table, _SELECTOR_FIELDS) and table["byteLength"] > 0)
    except (TypeError, KeyError):
        return False


def probe(key: str) -> dict | None:
    """The part's record, when it is valid and both objects it names are stored at their
    sizes; None otherwise. Bounded metadata only: no body, no kernel import."""
    if not valid_key(key):
        return None
    try:
        with entry_path(INDEX_KIND, key).open("rb") as stream:
            raw = stream.read(MAX_INDEX_BYTES + 1)
        if len(raw) > MAX_INDEX_BYTES:
            return None
        record = json.loads(raw.decode("utf-8"))
        if not _valid_record(record):
            return None
        if any(object_path(record[role]["object"]).stat().st_size != record[role]["byteLength"] for role in ROLES):
            return None
        return record
    except (OSError, ValueError, TypeError, KeyError):
        return None


def read(key: str, role: str, *, expected_object: str | None = None, max_bytes: int | None = None) -> bytes | None:
    """One of the part's objects (``role``: ``glb`` or ``selectors``), at most ``max_bytes``,
    bound to its record: a body's framing and JSON name this part, a table's bytes hash to
    its address. None for anything else."""
    from cadgen.store.meshes import MAX_SAFE_INTEGER, body_facts

    record = probe(key)
    facts = record[role] if record is not None and role in ROLES else None
    if facts is None or (expected_object is not None and expected_object != facts["object"]):
        return None
    limit = facts["byteLength"] if max_bytes is None else max_bytes
    if type(limit) is not int or not 0 < limit <= MAX_SAFE_INTEGER or facts["byteLength"] > limit:
        return None
    try:
        with object_path(facts["object"]).open("rb") as stream:
            if os.fstat(stream.fileno()).st_size != facts["byteLength"]:
                return None
            payload = stream.read(facts["byteLength"] + 1)
        if len(payload) != facts["byteLength"]:
            return None
        if role == "glb":
            # The record's content address names the body ``_write`` validated whole; a read
            # checks the framing, the canonical JSON and the part again. The tables are for
            # the decoders that read them (``meshes.decode_payload``, tessellationCache.js).
            body = body_facts(payload)
            if body.pop("part") != key or body != {name: value for name, value in facts.items() if name != "object"}:
                return None
        elif hashlib.sha256(payload).hexdigest() != facts["object"]:
            return None
        return payload
    except (OSError, ValueError, TypeError, KeyError):
        return None


def stored_unmeshed_faces(key: str) -> list[int]:
    """The faces the part's stored mesh leaves undrawn: none when the store lacks it or its
    record counts none, so a reader asks every part it drew for the price of a probe."""
    from cadgen.store.meshes import unmeshed_faces

    record = probe(key)
    if record is None or not record["glb"].get(_UNMESHED_COUNT):
        return []
    payload = read(key, "glb")
    return unmeshed_faces(payload) if payload is not None else []


def unmeshed_face_names(key: str, ordinals: list[int]) -> list[str]:
    """The faces ``ordinals`` of the part's table as a reader names them: each face's
    token (``f:…``, the ref a person pastes), read off the part's stored selector table;
    ``face <ordinal>`` for a face the table does not hold or when the table does not read."""
    from cadgen._internal.selector_table import read_selector_table

    tokens: dict[int, str] = {}
    payload = read(key, "selectors") if valid_key(key) else None
    if payload is not None:
        try:
            table = read_selector_table(payload)
        except ValueError:
            table = None
        if table is not None:
            columns = table["tables"]["faceColumns"]
            token_at, ordinal_at = columns.index("token"), columns.index("ordinal")
            tokens = {int(row[ordinal_at]): str(row[token_at]) for row in table["faces"]}
    return [tokens.get(int(ordinal), f"face {ordinal}") for ordinal in ordinals]


def _write(key: str, glb: bytes, table: bytes) -> dict:
    """Store the part's two objects, then its record: a record names complete objects."""
    from cadgen._internal.selector_table import read_selector_table
    from cadgen.store.meshes import body_facts

    facts = body_facts(glb)
    if facts.pop("part") != key:
        raise ValueError("the part's mesh names another part")
    stats = read_selector_table(table)["stats"]
    record = {
        "glb": {"object": put_object(glb, repair=True), **facts},
        "selectors": {"object": put_object(table, repair=True), "byteLength": len(table),
                      **{name: stats[name] for name in ("faceCount", "edgeCount", "vertexCount")}},
    }
    if not _valid_record(record):
        raise ValueError("invalid part record")
    # A hit is a read and writes nothing (STORE.md §8); the caller probed first.
    write_entry(INDEX_KIND, key, record)
    return record


@contextlib.contextmanager
def _meshing():
    """Whatever fails inside, as the ``MeshProductionError`` that names it (its type
    and message): one component's failure, which its caller reports once the rest
    of the request is stored. An interrupt is not one."""
    from cadgen._internal.tessellate import MeshProductionError

    try:
        yield
    except Exception as error:  # noqa: BLE001 - the error is the component's, reported by name
        raise MeshProductionError(f"{type(error).__name__}: {error}") from error


def _decoded(entry: dict) -> tuple[Any, dict]:
    """A decode of the component and its face colours by ordinal."""
    from cadgen._internal.component_package import decode_display_shape, effective_face_colors

    shape = decode_display_shape(entry, read_verified_object(entry["brep"]), native=True)
    topods = getattr(shape, "wrapped", shape)
    return topods, effective_face_colors(topods, entry["faceColors"])


def _topology(entry: dict, topods: Any) -> dict:
    """The component's topology facts: an eager-only component's are the object its tree
    pins, any other's are extracted here, in memory."""
    if entry["kind"] == "eager-only":
        from cadgen._internal.topology_facts import read_topology_facts

        return read_topology_facts(read_verified_object(entry["topology"]))
    from cadgen._internal.topology_extract import extract_topology_facts

    return extract_topology_facts(topods)


def _derive(entry: dict, key: str, keep_going: Callable[[], bool] | None = None) -> dict | None:
    """Store the part's display data when the store lacks it and return its record; None when
    ``keep_going`` said stop first. Any failure is the ``MeshProductionError`` naming it."""
    prior = probe(key)
    if prior is not None:
        return prior
    if keep_going is not None and not keep_going():
        return None
    from cadgen._internal.selector_table import build_selector_table, selector_table_bytes
    from cadgen._internal.tessellate import mesh_component
    from cadgen.tessellation_policy import DISPLAY_TESSELLATION

    with _meshing():
        topods, colors = _decoded(entry)
        # The table's element tokens are minted from this decode of the component: for a native
        # component they are the tokens the build's entry names (one geometry, one scheme).
        table = build_selector_table(topods, _topology(entry, topods))
        glb = mesh_component(topods, table, colors, part=key, chord=DISPLAY_TESSELLATION["chordTolerance"],
                             angle=DISPLAY_TESSELLATION["angleTolerance"])
        return _write(key, glb, selector_table_bytes(table))


def mesh_at(entry: dict, key: str, chord: float, angle: float) -> bytes:
    """The GLB body of the part ``key`` names, at ``chord``/``angle``, meshed in this process
    and stored nowhere: a mesh export at a tolerance other than the display one
    (``_internal/mesh_export.py``), whose file is the output. ``entry`` is the component's
    tree entry. The mesher reads what the display mesh's read -- the part's stored
    selector table and the component's face colours -- so nothing is extracted."""
    from cadgen._internal.selector_table import read_selector_table
    from cadgen._internal.tessellate import MeshProductionError, mesh_component

    payload = read(key, "selectors")
    if payload is None:
        raise MeshProductionError(f"part {key} has no stored selector table")
    with _meshing():
        topods, colors = _decoded(entry)
        return mesh_component(topods, read_selector_table(payload), colors, part=key, chord=chord, angle=angle)


def _captured(tree_hash: str, producer: dict | None) -> tuple[dict, dict]:
    """The tree's verified descriptor, and the producer named (this runtime's when None)."""
    from cadgen.store.trees import capture_tree as capture

    # Display work admits only a complete verified geometry graph, but retains none
    # of its BREPs: each derivation reads the exact CAS payload it decodes.
    descriptor, _ = capture(tree_hash, retain_payloads=False)
    return descriptor, producer_identity() if producer is None else producer_fields(producer)


def derive(tree_hash: str, cids: list[str] | None = None, *, producer: dict | None = None,
           keep_going: Callable[[], bool] | None = None) -> dict:
    """Derive in this process the display data of ``cids`` (every component when None) the
    store lacks -- the job a view submits for the parts it found missing -- and return
    their records, ``{cid: record}``.

    ``keep_going``, when given, is asked before each part: False stops there, and the
    result holds the parts done so far (a daemon worker asks whether anyone still wants
    its job, ``daemon/worker.py``). A part that fails, however it fails, does not stop the
    others: they are stored, then ``MeshProductionError`` names each failure (the
    component, and the error's type and message).
    """
    from cadgen._internal.tessellate import MeshProductionError

    descriptor, producer = _captured(tree_hash, producer)
    components = descriptor["components"]
    if producer != producer_identity():
        raise ProducerUnavailable()
    requested = list(components) if cids is None else list(dict.fromkeys(cids))
    if any(cid not in components for cid in requested):
        raise ValueError("display request names an unpinned component")
    key = producer_key(producer)
    result, unmeshed = {}, []
    for cid in requested:
        try:
            record = _derive(components[cid], _part_key(components[cid], key), keep_going)
        except MeshProductionError as error:
            # One part's failure leaves the rest of the request to be done: they are
            # stored before the failure is reported, so a retry finds them.
            unmeshed.append(f"component {cid}: {error}")
            continue
        if record is None:
            break
        result[cid] = record
    if unmeshed:
        raise MeshProductionError("; ".join(unmeshed))
    return result


def all_stored(tree_hash: str, producer: dict) -> bool:
    """Whether the store holds the display data of every part of ``tree_hash`` under
    ``producer``: one probe a part, no kernel import."""
    descriptor, selected = _captured(tree_hash, producer)
    key = producer_key(selected)
    return all(probe(_part_key(entry, key)) is not None for entry in descriptor["components"].values())


def ensure_display(tree_hash: str, *, producer: dict | None = None,
                   progress: Callable[[int, int], None] | None = None) -> list[str]:
    """Derive in this process the display data of every part of ``tree_hash`` the store
    lacks, and return a line for each part that could not be derived, naming the
    occurrences that place it and the error. A build does this before it reports done
    (``_internal/generation.py``), so a view of what it built only reads.

    Which parts are missing is asked of the store alone (``probe``): a tree whose
    display data is all stored imports no kernel. ``producer`` is the one a view of the
    tree selects (its document's), this runtime's when None. ``progress(done, total)``
    hears how many of the missing parts are done, before each one and at the end."""
    descriptor, producer = _captured(tree_hash, producer)
    components = descriptor["components"]
    key = producer_key(producer)
    keys = {cid: _part_key(entry, key) for cid, entry in components.items()}
    missing = [cid for cid in components if probe(keys[cid]) is None]
    if not missing:
        return []
    if producer != producer_identity():
        raise ProducerUnavailable()
    failures = []
    for done, cid in enumerate(missing):
        if progress is not None:
            progress(done, len(missing))
        try:
            _derive(components[cid], keys[cid])
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
