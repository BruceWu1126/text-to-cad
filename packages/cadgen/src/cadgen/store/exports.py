"""A model's files as exports of its stored result (STORE.md §3, *Exports*).

A build publishes its result tree and its record before anything is written from them.
Its files are then the build pool's: the export job (``kind: export``, ``daemon/artifacts.py``,
:func:`run_export`) writes the STEP from the stored tree and publishes it, then cuts the
model's declared meshes; a model that owes only meshes has the mesh job
(``kind: mesh``, :func:`run_meshes`). A request names the tree, the model, the result's
identity and the files, never a script. Publishing a file moves it into place and records it:
the STEP's bytes are mapped to the tree they were written from, so the file is the result and
nothing parses it to check, and its sidecar is written from the record.

:func:`export_step` materializes the tree (``store.materialize``) and writes it with the
names writer (``step_export.export_build123d_step_file``): each component's part key, on
its product, and its faces' and edges' names, on the entities written for them
(``step_names``). The file is a function of the tree and the writer
(``step_export.STEP_WRITER_SCHEME``) alone.

A job writes only while the model's record names its result -- its key, tree, annotations and
output recipes (:func:`state_digest`) -- and reads the record again before it writes, before
each file replaces the saved one, and before each record write: a newer result of the model
supersedes it (:class:`Superseded`), and a record gone with the store is no newer result. A
job nobody wants any more (its build left before it began, ``keep_going``) writes nothing;
one already writing finishes into its staging folder, since an OCCT write is never cut off,
and publishes nothing.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

__all__ = [
    "PublicationRefused",
    "Superseded",
    "document_pair_state",
    "export_step",
    "publish_sidecar",
    "publish_step",
    "recorded_sidecar",
    "run_export",
    "run_meshes",
    "sha256_of",
    "sha256_or_none",
    "stage",
    "state_digest",
]


class PublicationRefused(RuntimeError):
    """A file this export owes was not saved: the file on disk is not one it may replace, or
    changed under the publication, or the record it fills is gone. Reported by its message."""


class Superseded(Exception):
    """A newer result of the model is recorded: what this export owes is that result's to
    write. Its argument is the file it was writing."""


class _Spans:
    """What a writer's logger is told (``CliLogger``'s ``timed`` and ``warning``), kept to
    hand back: each step and the seconds it took, and the warnings, which the build that
    asked relays to its own logger."""

    def __init__(self) -> None:
        self.rows: list[list[Any]] = []
        self.warnings: list[str] = []

    @contextmanager
    def timed(self, label: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            self.rows.append([label, time.perf_counter() - started])

    def warning(self, message: str) -> None:
        self.warnings.append(str(message))

    def debug(self, message: str) -> None:
        pass

    def info(self, message: str) -> None:
        pass


# --- files ---------------------------------------------------------------------------------


def sha256_of(path: Path) -> str:
    from cadgen._internal.atomic_replace import open_with_ladder

    # Through the ladder: a peer build publishing the same model may be renaming
    # over this very file, which Windows refuses an open for (STORE.md §7).
    digest = hashlib.sha256()
    with open_with_ladder(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_or_none(path: Path) -> str | None:
    try:
        return sha256_of(path)
    except (FileNotFoundError, NotADirectoryError):
        return None


def document_pair_state(step_path: Path) -> tuple[str | None, str | None]:
    """The saved STEP's and its sidecar's sha256, None for a file that is absent.

    Through the gate's digest memo (``store.gate._sha256_file``), which one job
    already consults for these very files: a file whose settled identity has
    not moved since this process hashed it, or since it renamed its own written
    copy into place, is not read again (STORE.md §4). A label edit keeps a STEP
    of hundreds of megabytes, and used to read it once per call."""
    from cadgen._internal.source_sidecar import source_sidecar_path
    from cadgen.store.gate import _sha256_file

    step = Path(step_path).expanduser().resolve()
    return _sha256_file(step), _sha256_file(source_sidecar_path(step))


def publish_sidecar(staged_step: Path, entry_path: Path) -> None:
    """Move the staged sidecar beside the saved STEP, or remove the saved one when none was
    staged. A saved sidecar that already has the staged bytes stays in place, as a kept
    document does: a viewer versions the sidecar by its file stamp, and a new stamp would
    reload the model for an edit that changed nothing on screen, such as a label or a comment."""
    from cadgen._internal.atomic_replace import replace_atomic
    from cadgen._internal.source_sidecar import remove_source_sidecar, source_sidecar_path

    staged, saved = source_sidecar_path(staged_step), source_sidecar_path(entry_path)
    if not staged.is_file():
        remove_source_sidecar(entry_path)
    elif sha256_or_none(saved) != sha256_of(staged):
        replace_atomic(staged, saved)


def stage(output: Path, cleanup: ExitStack) -> Path:
    """A folder beside ``output`` for a file to be written into before it replaces the saved
    one, removed with ``cleanup`` and named after this process (``temp_leftovers``). A job
    killed while it saved left its staging folder, a full copy of its file, beside the output:
    gone before this one stages, once its process is. The file takes its final basename there,
    so its labels and bytes do not depend on the folder. A removal Windows refuses (a scanner
    holding the copy) never fails the job; the next stage's sweep removes what is left."""
    from tempfile import TemporaryDirectory

    from cadgen._internal.filetrace import paused
    from cadgen._internal.temp_leftovers import stage_prefix, sweep_stages

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with paused():
        sweep_stages(output.parent)
    return Path(cleanup.enter_context(TemporaryDirectory(prefix=stage_prefix(output.stem),
                                                         dir=output.parent, ignore_cleanup_errors=True)))


# --- the result a file is written for ----------------------------------------------------------


def state_digest(record: Mapping[str, Any]) -> str:
    """The digest of a record's publication identity (``records.export_state``): its key and
    tree, its resolved annotations and its output recipes. What an export pins of the result
    it writes; receipts and owed notes, which change while one run saves, are not in it."""
    from cadgen.store.records import export_identity

    encoded = json.dumps(export_identity(record), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _naming(model: str, state: str, path: Path | str) -> dict[str, Any] | None:
    """The model's record while it names the result ``state`` pins; None when it is gone with
    the store. A record naming another result raises :class:`Superseded` for ``path``."""
    from cadgen.store.records import read_record

    record = read_record(model)
    if record is not None and state_digest(record) != state:
        raise Superseded(str(path))
    return record


def _gone(model: str) -> PublicationRefused:
    from cadgen.store.index import display_model

    return PublicationRefused(f"{display_model(model)}: result was not saved: its record is gone from the store; "
                              "run the model again")


def recorded_sidecar(record: Mapping[str, Any]) -> dict[str, Any]:
    """The sidecar a model's result carries, from its record -- the kinematics, appearance
    and animation its build resolved, in the build's order -- so a file written from the
    stored result gets the result's annotations, never the file's own (``annotation_refresh``
    writes the same). Empty where the record holds none, which ``write_source_sidecar`` reads
    as: no sidecar."""
    return {section: copy.deepcopy(record[section]) for section in ("kinematics", "appearance", "animation")
            if record.get(section) is not None}


def _previous_pairs(previous: Mapping[str, Any], step: Path, sidecar: Path) -> list[tuple[str, str | None]]:
    """The STEP and sidecar digests the model's previous export wrote: as the record the build
    replaced named them (``previous["outputs"]``), and as the result stored under that record's
    key names them now, which its export's publication rewrote if it landed after the build
    read the record. Bytes the same model's previous export saved while this build ran are the
    model's own, never a competing edit (STORE.md §7)."""
    from cadgen.store.results import read_result

    pinned = previous.get("outputs") or {}
    stored = ((read_result(str(previous.get("key") or "")) or {}).get("record") or {}).get("outputs") or {}
    pairs = []
    for step_digest, sidecar_digest in (
        (pinned.get(str(step)), pinned.get(str(sidecar))),
        ((stored.get(str(step)) or {}).get("sha256"), (stored.get(str(sidecar)) or {}).get("sha256")),
    ):
        if step_digest:
            pairs.append((str(step_digest), str(sidecar_digest or "") or None))
    return pairs


def _previous_digests(previous: Mapping[str, Any], path: Path) -> set[str]:
    """Every digest the model's previous export recorded for ``path``, as :func:`_previous_pairs`
    reads them."""
    from cadgen.store.results import read_result

    found = set()
    pinned = (previous.get("outputs") or {}).get(str(path))
    if pinned:
        found.add(str(pinned))
    stored = ((read_result(str(previous.get("key") or "")) or {}).get("record") or {}).get("outputs") or {}
    recorded = (stored.get(str(path)) or {}).get("sha256")
    if recorded:
        found.add(str(recorded))
    return found


def note_owed(model: str, state: str, paths: list[str], error: BaseException) -> None:
    """Why an export failed, kept on the record's entry for each file it still owed, while the
    record names its result: the model's next run says it first (``generation._say_owed``), and
    writes the files. A newer result owes its own."""
    from cadgen.store.records import read_record, write_record

    record = read_record(model)
    if record is None or state_digest(record) != state:
        return
    outputs = record.get("outputs") or {}
    owed = {"since": time.time(), "error": f"{type(error).__name__}: {error}"}
    entries = [outputs.get(path) for path in paths]
    for entry in entries:
        if isinstance(entry, dict):
            entry["owed"] = owed
    if any(isinstance(entry, dict) for entry in entries):
        write_record(model, record)


# --- the STEP ----------------------------------------------------------------------------------


def export_step(tree_hash: str, out: Path, *, shapes: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Write the stored tree ``tree_hash`` to ``out`` and return the file's ``sha256``, the
    producer whose part keys it carries (``producer``) and the writer's ``spans`` and
    ``warnings``. ``shapes`` gives the build's own live shapes by cid for components the
    store cannot decode (an eager-only component's), which only the build's own process
    holds (STORE.md §3). A part key names its producer: a runtime that cannot name its own
    writes its elements' names and no part key, under no producer."""
    from cadgen.store.parts import producer_identity
    from cadgen.store.trees import capture_tree

    out = Path(out)
    spans = _Spans()
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        producer = producer_identity()
    except ValueError:
        producer = None
    from cadgen.step_export import export_build123d_step_file
    from cadgen.store.materialize import materialize_descriptor

    with spans.timed("materialize"):
        descriptor, payloads = capture_tree(tree_hash)
        placed: dict[int, str] = {}
        # Written, never handed to model code: untagged, so nothing is captured to tell an
        # edit of it apart (``materialize``'s partner evidence).
        document = materialize_descriptor(descriptor, captured_objects=payloads, shapes=shapes, placed=placed)
    digest = export_build123d_step_file(document, out, logger=spans,
                                        names=_namer(descriptor, payloads, placed, producer))
    return {"sha256": digest, "producer": producer, "spans": spans.rows, "warnings": spans.warnings}


def _namer(descriptor: Mapping[str, Any], payloads: Mapping[str, bytes], placed: Mapping[int, str],
           producer: dict | None) -> Callable[[Any], Any]:
    """What the file names each placed leaf of the materialized ``descriptor`` by: its
    component's part key under ``producer`` (none under no producer) and its lineage's names
    for its faces and edges (``step_names.ComponentNames``); None for a shape no component
    placed."""
    from cadgen._internal.component_package import read_lineage
    from cadgen._internal.step_names import ComponentNames
    from cadgen.store.parts import part_key

    components = descriptor["components"]
    found: dict[str, Any] = {}

    def names(shape: Any) -> Any:
        cid = placed.get(id(shape))
        if cid is None:
            return None
        if cid not in found:
            entry = components[cid]
            lineage = read_lineage(payloads[entry["lineage"]]) if entry.get("lineage") else None
            found[cid] = ComponentNames(part=part_key(entry, producer) if producer is not None else None,
                                        faces=tuple(lineage["faces"]) if lineage else (),
                                        edges=tuple(lineage["edges"]) if lineage else ())
        return found[cid]

    return names


def publish_step(*, model: str, tree: str, state: str, step: Path, expected: Mapping[str, Any],
                 previous: Mapping[str, Any], staged: Path, digest: str, producer: dict | None,
                 written: bool) -> dict[str, Any]:
    """The STEP at ``staged`` in place as the model's saved file, with its sidecar and its
    record's output entry (STORE.md §3, *Exports*; §5, publish order); the ``saved`` result:
    the file, the tree it was written from and its digest.

    In order: the sidecar from the record, staged beside the file; the document pair on disk
    checked -- the pair the build found (``expected``), its own bytes, or a pair the model's
    previous export saved meanwhile (``previous``, §7) -- and anything else refused as a
    competing edit; the record read again; the file and its sidecar moved into place, each
    atomically, equal bytes left in place with their stamp; the pair checked again; the
    bytes mapped to the tree in ``index/document`` with the producer the export wrote with
    (``written``; a kept file's bytes are mapped already); the record read again and its
    output entries filled, merged into the latest record, which is read once more right
    before the write. A newer result raises :class:`Superseded`; a refusal,
    :class:`PublicationRefused`."""
    from cadgen._internal.atomic_replace import replace_atomic
    from cadgen._internal.source_sidecar import source_sidecar_path, write_source_sidecar
    from cadgen.catalog import seed_artifact_hash
    from cadgen.step_export import STEP_WRITER_SCHEME
    from cadgen.store.gate import file_stamp, remember_renamed_digest
    from cadgen.store.index import display_model
    from cadgen.store.records import forget_output, note_document_tree, write_record

    step = Path(step)
    sidecar_path = source_sidecar_path(step)
    record = _naming(model, state, step)
    if record is None:
        raise _gone(model)
    sections = recorded_sidecar(record)
    if sections:
        write_source_sidecar(staged, sections, document_hash=digest)
    staged_sidecar = source_sidecar_path(staged)
    sidecar = sha256_of(staged_sidecar) if staged_sidecar.is_file() else None
    found = (expected.get(str(step)), expected.get(str(sidecar_path)))
    # The pair the build found, its own bytes, or the pair the model's previous export saved
    # meanwhile; anything else is a competing edit (another model, a person).
    if document_pair_state(step) not in (found, (digest, sidecar), *_previous_pairs(previous, step, sidecar_path)):
        raise PublicationRefused(f"{display_model(model)}: result was not saved: the STEP file or its annotations "
                                 "changed during the build")
    _naming(model, state, step)
    if document_pair_state(step)[0] != digest:
        # Separate atomic writes, not a multi-file transaction. Equal bytes stay in place,
        # with their stamp. The written copy's identity survives the rename, so its digest
        # needs no second read of the saved document.
        staged_identity = file_stamp(staged)
        replace_atomic(staged, step)
        remember_renamed_digest(step.resolve(), digest, staged_identity)
    publish_sidecar(staged, step)
    if document_pair_state(step) != (digest, sidecar):
        raise PublicationRefused(f"{display_model(model)}: saved files changed during publication; the build record "
                                 "was not updated")
    seed_artifact_hash(step, digest)
    if written:
        # The bytes, mapped to the tree they were written from: the file is the result.
        note_document_tree(digest, tree, producer=producer)
    # A sibling export may have filled its own entry while this one wrote: merge only this
    # receipt into the latest record naming the result, and keep theirs.
    latest = _naming(model, state, step)
    if latest is None:
        raise _gone(model)
    record = copy.deepcopy(latest)
    outputs = record.setdefault("outputs", {})
    outputs[str(step)] = {"sha256": digest, "writer": STEP_WRITER_SCHEME}
    sidecar_key = str(sidecar_path.resolve())
    if sidecar is not None:
        outputs[sidecar_key] = {"sha256": sidecar}
    else:
        outputs.pop(sidecar_key, None)
        forget_output(sidecar_key)
    # A newer result's record is never overwritten with this one's.
    _naming(model, state, step)
    write_record(model, record)
    return {"output": str(step), "tree": tree, "documentHash": digest}


# --- the jobs ----------------------------------------------------------------------------------


def _settle_displays(requests: list[Mapping[str, Any]]) -> None:
    """Wait for the display data the build's own jobs derive (``requests``, the display jobs
    it owes for this result) before a file of the result lands: a reader of the saved file
    then finds its parts stored, and never derives one a second time. A request whose parts the
    store holds is done; one still in flight is attached to (the build pool coalesces an
    identical request), and one that ended without them is asked again. A part that cannot be
    derived is the display data's warning, never a reason not to save the file."""
    from cadgen.daemon.artifacts import resolve_artifact
    from cadgen.store import parts

    for request in requests:
        try:
            keys, _producer = parts.part_keys(request["tree"], producer=request["producer"])
            if all(parts.probe(keys[cid]) is not None for cid in request["cids"]):
                continue
            resolve_artifact(dict(request))
        except Exception:  # noqa: BLE001 - display data never keeps a file from its save
            continue


def _wanted(keep_going: Callable[[], bool] | None) -> bool:
    return keep_going is None or keep_going()


def _outcome() -> dict[str, Any]:
    return {"saved": [], "superseded": None, "abandoned": False, "sha256": None, "spans": [],
            "warnings": [], "wrote": []}


def run_export(request: Mapping[str, Any], *, keep_going: Callable[[], bool] | None = None,
               published: Callable[[dict], None] | None = None) -> dict[str, Any]:
    """The export job (``kind: export``): the STEP written from the stored tree into a staging
    folder beside the saved file, then -- once the build's own display jobs are done
    (:func:`_settle_displays`) -- published (:func:`publish_step`), then the model's declared
    meshes cut (:func:`run_meshes`'s work). ``published`` hears each file the moment it lands
    (the supervisor folds it into the build's ledger row). The answer names each file saved,
    the STEP's digest, the writer's steps and warnings, and whether a newer result superseded
    the job or nobody wanted it any more. A failure keeps its reason on the record's entry for
    each file still owed (:func:`note_owed`)."""
    model, tree, state = str(request["model"]), str(request["tree"]), str(request["state"])
    step = Path(request["step"])
    owed = [str(step), *(str(item["path"]) for item in request["meshes"])]
    outcome = _outcome()

    def saved(entry: dict) -> None:
        outcome["saved"].append(entry)
        if entry["output"] in owed:
            owed.remove(entry["output"])
        if published is not None:
            published(entry)

    try:
        if not _wanted(keep_going):
            outcome["abandoned"] = True
            return outcome
        _naming(model, state, step)
        with ExitStack() as cleanup:
            staged = stage(step, cleanup) / step.name
            written = export_step(tree, staged)
            outcome["spans"] += written["spans"]
            outcome["warnings"] += written["warnings"]
            _settle_displays(request["displays"])
            if not _wanted(keep_going):
                outcome["abandoned"] = True
                return outcome
            entry = publish_step(model=model, tree=tree, state=state, step=step, expected=request["expected"],
                                 previous=request["previous"], staged=staged, digest=written["sha256"],
                                 producer=written["producer"], written=True)
        outcome["sha256"] = written["sha256"]
        outcome["publishedAt"] = time.time()
        saved(entry)
        if request["meshes"]:
            _cut_meshes(request, document=written["sha256"], saved=saved, outcome=outcome)
    except Superseded as superseded:
        outcome["superseded"] = str(superseded.args[0])
    except Exception as error:
        note_owed(model, state, owed, error)
        raise
    return outcome


def run_meshes(request: Mapping[str, Any], *, keep_going: Callable[[], bool] | None = None,
               published: Callable[[dict], None] | None = None) -> dict[str, Any]:
    """The mesh job (``kind: mesh``): a model's declared meshes cut from its stored result when
    its STEP is already saved (or it writes none), once the build's own display jobs are done.
    Answers as :func:`run_export` does."""
    model, state = str(request["model"]), str(request["state"])
    owed = [str(item["path"]) for item in request["meshes"]]
    outcome = _outcome()

    def saved(entry: dict) -> None:
        outcome["saved"].append(entry)
        if entry["output"] in owed:
            owed.remove(entry["output"])
        if published is not None:
            published(entry)

    try:
        if not _wanted(keep_going):
            outcome["abandoned"] = True
            return outcome
        _settle_displays(request["displays"])
        if not _wanted(keep_going):
            outcome["abandoned"] = True
            return outcome
        _cut_meshes(request, document=None, saved=saved, outcome=outcome)
    except Superseded as superseded:
        outcome["superseded"] = str(superseded.args[0])
    except Exception as error:
        note_owed(model, state, owed, error)
        raise
    return outcome


def _cut_meshes(request: Mapping[str, Any], *, document: str | None, saved: Callable[[dict], None],
                outcome: dict[str, Any]) -> None:
    """The model's declared ``@stl``/``@glb``/``@threemf`` files (``request["meshes"]``),
    each cut where the record does not name it current, through the ONE mesh engine the
    `cadgen stl|3mf|glb build` doors use -- same meshes, same writers, same records -- so the
    two front doors cannot drift.

    A STEP model's meshes are keyed on its STEP's bytes as the record names them (``document``,
    the digest the export just wrote, else the record's entry); a mesh-only model's on its
    result tree, which IS its geometry. Geometry and appearance come from the stored result and
    the record, never a door's lookup or a mutable disk sidecar. A declaration's own tolerances
    are the only ones: one that names none is written at the display tolerance, from the very
    meshes the viewer draws. Each file is staged beside its destination; before it replaces the
    destination the record is read again and the destination checked -- the bytes the build
    found, its own, or what the model's previous export recorded meanwhile -- and after it the
    published bytes are verified and the receipt bound to the result. A mesh found current is
    saved as it stands."""
    from dataclasses import replace

    from cadgen._internal.atomic_replace import replace_atomic
    from cadgen._internal.doors import display_path
    from cadgen._internal.mesh_export import MeshExportJob, MeshSource, mesh_export_current, record_mesh_export, \
        run_mesh_exporter
    from cadgen._internal.source_sidecar import appearance_digest
    from cadgen.store.records import export_state, read_record

    model, tree, state = str(request["model"]), str(request["tree"]), str(request["state"])
    declared = list(request["meshes"])
    record = _naming(model, state, declared[0]["path"])
    if record is None:
        raise _gone(model)
    step = request.get("step")
    if document is None:
        document = (str(((record.get("outputs") or {}).get(str(step)) or {}).get("sha256") or "") or None
                    if step else tree)
    if not document:
        return  # nothing names what they are cut from: the STEP's bytes are not recorded yet
    appearance = record.get("appearance")
    appearance_key = appearance_digest(appearance)

    def receipt(path: Path) -> dict:
        outputs = (read_record(model) or {}).get("outputs") or {}
        return {"output": str(path), "tree": tree,
                "documentHash": str((outputs.get(str(path)) or {}).get("sha256") or "") or None}

    jobs = []
    for item in declared:
        job = MeshExportJob(fmt=str(item["fmt"]), out=Path(item["path"]), mesh_tolerance=item["chord"],
                            mesh_angular_tolerance=item["angle"])
        if mesh_export_current(job.out, model=model, document_hash=document, mesh_tolerance=job.mesh_tolerance,
                               mesh_angular_tolerance=job.mesh_angular_tolerance, appearance_key=appearance_key):
            saved(receipt(job.out))
        else:
            jobs.append(job)
    if not jobs:
        return
    expected = request["expected"]
    spans = _Spans()
    with ExitStack() as stages:
        staged = [replace(job, out=stage(job.out, stages) / job.out.name) for job in jobs]
        exported = run_mesh_exporter(MeshSource(tree, document if step else None), staged, name=str(request["name"]),
                                     logger=spans, appearance=appearance)
        for job, prepared in zip(jobs, staged):
            digest = sha256_of(prepared.out)
            # A declared mesh may replace only the bytes the build found or the model's previous
            # export recorded while it ran.
            _naming(model, state, job.out)
            allowed = {expected.get(str(job.out)), digest, *_previous_digests(request["previous"], job.out)}
            if sha256_or_none(job.out) not in allowed:
                raise PublicationRefused(f"{display_path(job.out)} was not saved: the mesh file changed during the build")
            replace_atomic(prepared.out, job.out)
            current = _naming(model, state, job.out)
            if sha256_or_none(job.out) != digest:
                raise PublicationRefused(f"{display_path(job.out)} changed during publication; its receipt was not "
                                         "recorded")
            if current is None:
                raise _gone(model)
            record_mesh_export(
                job.out, model=model, document_hash=document, fmt=job.fmt,
                mesh_tolerance=job.mesh_tolerance, mesh_angular_tolerance=job.mesh_angular_tolerance,
                appearance_key=appearance_key, expected_result=(current.get("key"), current.get("tree")),
                expected_hash=digest, expected_state=export_state(current),
            )
            _naming(model, state, job.out)
            outcome["wrote"].append([job.fmt, str(job.out)])
            saved(receipt(job.out))
    outcome["spans"] += spans.rows
    for entry in exported.get("files") or []:
        outcome["warnings"] += list((entry or {}).get("warnings") or ())
