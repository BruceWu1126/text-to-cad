from __future__ import annotations

import copy
import json
import sys
import time

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from cadgen.catalog import source_from_path
from cadgen.cli_logging import CliLogger
from cadgen.coordination import (
    DRAWING_PACKAGE,
    PHASE_MESH,
    STEP_PACKAGE,
    ProgressEvent,
    artifact_build,
    resolve as resolve_progress,
)
from cadgen._internal.step_scene import (
    LoadedStepScene,
    SelectorOptions,
    step_file_hash,
)
from cadgen._internal.generation_runner import (
    _ensure_step_ready,
    _spec_output_dir,
    run_script_generator,
)
from cadgen._internal.generation_spec import (
    EntrySpec,
    GeneratedStepResult,
    _cli_progress_line,
    _display_path,
    _entry_spec_from_source,
    _selector_options_for_part,
)
from cadgen.store.exports import document_pair_state, sha256_or_none

@dataclass(frozen=True)
class _ExportPlan:
    """What a build does about its STEP (STORE.md §3, *Exports*): keep the file on disk,
    whose recorded output entry is ``kept``, or export the result. ``why`` says why a file
    the same result was last written to is written again (a current model's re-export,
    :func:`_hit`); empty otherwise. ``mapped`` is the tree the store maps a kept file's
    bytes to."""

    kept: dict | None
    why: str = ""
    mapped: str | None = None

    @property
    def kept_sha256(self) -> str:
        return str(self.kept["sha256"]) if self.kept is not None else ""

    def output(self) -> dict:
        """The record's entry for the STEP until it is saved: a kept file's own, else
        pending (an empty sha256, which the gate reads as never written)."""
        from cadgen.step_export import STEP_WRITER_SCHEME

        if self.kept is not None:
            return dict(self.kept)
        return {"sha256": "", "writer": STEP_WRITER_SCHEME}

    def map_kept(self, tree: str) -> None:
        """A kept file's bytes mapped to the result ``tree``, where the store maps them to
        another tree: the kept rule asks only that the store maps the bytes, and a compile of
        the file (a door opened it after a document-schema change, before this run) or
        another model's export of equal bytes stands for it then, so refs by lineage would
        miss and parts key otherwise. One index write; nothing is exported."""
        if self.kept is None or self.mapped in (None, tree):
            return
        from cadgen.store.records import note_document_tree

        note_document_tree(self.kept_sha256, tree, producer=_producer_or_none())


def _export_plan(previous: Mapping[str, object], step_key: str, tree: str, *, force: bool,
                 on_disk: str | None) -> _ExportPlan:
    """The kept rule: a build keeps its STEP, writing nothing, when its record's last build
    had this very tree, wrote the file with this writer, the file on disk when the build
    started still has the bytes it recorded, and the store maps those bytes (their document
    entry) -- unless forced. Otherwise it exports; a file whose entry the store lost is
    written again, to the same bytes, and mapped again."""
    from cadgen.step_export import STEP_WRITER_SCHEME
    from cadgen.store.records import document_entry_for_hash

    prior = (previous.get("outputs") or {}).get(step_key)
    prior = prior if isinstance(prior, dict) else {}
    same = previous.get("tree") == tree
    sha = str(prior.get("sha256") or "")
    entry = document_entry_for_hash(sha) if sha else None
    this_writer = prior.get("writer") == STEP_WRITER_SCHEME
    if not force and same and sha and this_writer and on_disk == sha and entry is not None:
        return _ExportPlan(kept={key: prior[key] for key in ("sha256", "writer")},
                           mapped=str(entry.get("tree") or "") or None)
    why = ""
    if not force and same and sha and on_disk == sha:
        why = "was written by another writer" if not this_writer else "has no entry in the store"
    elif not force and same:
        why = ("is owed" if prior.get("owed") else "was never written" if not sha
               else "is missing" if on_disk is None else "is not the file the record names")
    return _ExportPlan(kept=None, why=why)


def _producer_or_none() -> dict | None:
    """This runtime's display producer, or None where it cannot name one."""
    from cadgen.store.parts import producer_identity

    try:
        return producer_identity()
    except ValueError:
        return None


class _Export:
    """What a model's build owes beside its record (STORE.md §3, *Exports*; §9a): its files,
    written from its stored result by the build pool.

    Queued by the one pipeline the moment the model's result is recorded, root or child
    alike. The STEP and the model's declared meshes are one pool job (``kind: export``,
    ``store.exports.run_export``: the STEP written from the stored tree, published once the
    result's own display jobs are done, then the meshes cut), and meshes alone are another
    (``kind: mesh``); the job is started now (``BuildRun.owe``), so a child's file is written
    while its parent's body goes on. In a daemon worker the job is the supervisor's and the
    command answers without it; with no worker the run awaits it before the command answers
    (:meth:`finish`), relaying what it says. The request pins what the job needs and nothing
    it could run: the model, its tree and the digest of the result the record names
    (``store.exports.state_digest``), the files and the bytes the build found there
    (``expected``), what the model's previous export recorded (``previous``), and the display
    jobs to wait for (``displays``).

    A kept STEP writes no bytes, and a result holding a component only this process's live
    shape stands for (an eager-only one) is written here: either is published here, before
    the command answers, by the same publication (``store.exports.publish_step``), and its
    meshes are a mesh job. A current model has its display data asked for by the caller
    (:func:`_hit`), after the STEP its file's bytes then map to; a mesh-only model has only
    its meshes.

    An export writes only while the model's record still names its result: once a newer build
    of the model has recorded its result, this one's files are that build's to write, and the
    job says so in its answer and writes nothing more.
    """

    def __init__(self, spec: EntrySpec, tree: str, *, key: str | None, logger: CliLogger,
                 step: dict | None = None, displays: Sequence[object] = (), announce: str | None = None,
                 expected_meshes: dict[str, str | None] | None = None,
                 previous: Mapping[str, object] | None = None,
                 captured_record: dict) -> None:
        from cadgen._internal import build_timing
        from cadgen.authoring import current_run
        from cadgen.store.exports import state_digest
        from cadgen.store.records import read_record

        run = current_run()
        if run is None:
            raise RuntimeError(f"{spec.cad_ref}: an export was queued outside a build run")
        self.spec, self.tree, self.logger = spec, tree, logger
        self.model = _model_for_spec(spec)
        record = captured_record
        record.setdefault("key", key)
        self.state = state_digest(record)
        # The clock the model's ``done`` event reads, so its ``STEP saved`` timing is on it
        # too. A model restored rather than built has no build clock: its export's queueing.
        clock = build_timing.current()
        self.started = clock.started if clock is not None else time.perf_counter()
        self.started_at = time.time() - (time.perf_counter() - self.started)
        # The summary line is the command's, in its logger's voice; the job's spans and
        # warnings are the build's (``logger``, verbose or not as the build was given it).
        self.command_logger = run.logger if run.logger is not None else logger
        self.announce = announce
        self.request: dict | None = None
        self.job: object | None = None
        self.step_path = (spec.step_path.expanduser().resolve()
                          if spec.step_output and spec.step_path is not None else None)
        self.displays = [request for display in displays if display is not None for request in display.requests]
        expected = dict(expected_meshes if expected_meshes is not None else _mesh_file_state(spec))
        published_here = False
        if step is not None:
            from cadgen._internal.source_sidecar import source_sidecar_path

            plan: _ExportPlan = step["plan"]
            pair = step["expected"] or (None, None)
            expected[str(self.step_path)] = pair[0]
            expected[str(source_sidecar_path(self.step_path).resolve())] = pair[1]
            if plan.kept is None and not step["live"]:
                self.request = self._publication("export", expected, previous, meshes=list(spec.mesh_exports))
            else:
                # A kept file writes no bytes, and a live result is written here: either is
                # the result's before the command answers.
                published_here = self._publish_here(plan, expected, previous, live=step["live"])
                if published_here is None:
                    run.exports.append(self)
                    return
                if announce:
                    self.command_logger.info(announce)
                self.announce = None
        if self.request is None and spec.mesh_exports:
            # The declared meshes its record does not name as current on disk, cut once their
            # STEP is saved; a mesh-only model's from its result.
            owed = _mesh_exports_owed(spec, source_tree=tree, record=read_record(self.model) or record)
            if owed is not None:
                model, tree_hash, jobs = owed
                if not jobs:
                    _note_meshes_saved(spec, model, tree_hash)
                else:
                    owed_paths = {Path(job.out).expanduser().resolve() for job in jobs}
                    self.request = self._publication(
                        "mesh", expected, previous,
                        meshes=[item for item in spec.mesh_exports
                                if Path(item.path).expanduser().resolve() in owed_paths])
        if self.request is not None:
            from cadgen.daemon.telemetry import job_reused

            self.job = run.owe(self.request, model=self.model)
            job_reused(False)  # a file is written: not all of it was the store's
        elif published_here:
            from cadgen.daemon.telemetry import job_reused

            job_reused(False)
        run.exports.append(self)

    def _publication(self, kind: str, expected: Mapping[str, str | None], previous: Mapping[str, object] | None,
                     *, meshes: list) -> dict:
        """The export or mesh request for this model's files (``daemon/artifacts.py``)."""
        files = ([str(self.step_path), f"{self.step_path}.json"] if self.step_path is not None else []) \
            + [str(Path(item.path).expanduser().resolve()) for item in meshes]
        recorded = (previous or {}).get("outputs") or {}
        return {
            "kind": kind,
            "model": self.model,
            "tree": self.tree,
            "state": self.state,
            "step": str(self.step_path) if self.step_path is not None else None,
            "name": self.spec.step_path.stem,
            "expected": {path: expected.get(path) for path in files if path in expected},
            "previous": {"key": str((previous or {}).get("key") or "") or None,
                         "outputs": {path: str(recorded[path]["sha256"]) for path in files
                                     if isinstance(recorded.get(path), dict) and recorded[path].get("sha256")}},
            "displays": list(self.displays),
            "meshes": [{"fmt": item.fmt, "path": str(Path(item.path).expanduser().resolve()),
                        "chord": item.mesh_tolerance, "angle": item.mesh_angular_tolerance} for item in meshes],
        }

    def _publish_here(self, plan: _ExportPlan, expected: Mapping[str, str | None],
                      previous: Mapping[str, object] | None, *, live: Mapping[str, object]) -> bool | None:
        """Publish the STEP in this process (``store.exports.publish_step``): a kept file's
        sidecar and record, or a live result written here first. True when bytes were written,
        False for a kept file, None when a newer result superseded it."""
        from contextlib import ExitStack

        from cadgen.daemon import executors
        from cadgen.store import exports

        request = self._publication("export", expected, previous, meshes=[])
        try:
            with ExitStack() as cleanup:
                staged = exports.stage(self.step_path, cleanup) / self.step_path.name
                if live:
                    written = exports.export_step(self.tree, staged, shapes=live)
                    for label, seconds in written["spans"]:
                        self.logger.timing(label, seconds)
                    for warning in written["warnings"]:
                        self.logger.warning(warning)
                    digest, producer = str(written["sha256"]), written["producer"]
                else:
                    digest, producer = plan.kept_sha256, None
                saved = exports.publish_step(model=self.model, tree=self.tree, state=self.state, step=self.step_path,
                                             expected=request["expected"], previous=request["previous"],
                                             staged=staged, digest=digest, producer=producer, written=bool(live))
        except exports.Superseded as superseded:
            self._superseded(superseded.args[0])
            return None
        except Exception as error:
            exports.note_owed(self.model, self.state, [str(self.step_path)], error)
            raise
        saved_at = time.perf_counter() - self.started
        executors.emit_event(executors.model_event(self.model, "building", phase="STEP saved", elapsed=saved_at,
                                                   saved=saved))
        # The build tree drops an event that follows its model's done, so a person (or a
        # benchmark) reading --verbose finds the moment here, as for any stage.
        self.logger.timing(f"STEP saved {self.step_path.name}", saved_at)
        return bool(live)

    def owed(self) -> list[Path]:
        """The files this export's job still writes: the STEP and the meshes its request names;
        none once it has finished, or when nothing was owed."""
        if self.request is None:
            return []
        from cadgen.daemon.artifacts import publication_files

        return [Path(path) for path in publication_files(self.request)]

    def files(self) -> dict[str, str]:
        """Each file the model writes, by absolute path, and its state: ``saving`` while this
        export still writes it, else ``saved``."""
        declared = [self.spec.step_path] if self.spec.step_output and self.spec.step_path is not None else []
        declared += [item.path for item in self.spec.mesh_exports]
        owed = set(self.owed())
        return {str(path): "saving" if path in owed else "saved"
                for path in (Path(item).expanduser().resolve() for item in declared)}

    def finish(self) -> None:
        """Await the job this process submitted (a run with no worker; a job handed to the
        supervisor is the supervisor's), and say what it did: the writer's steps and warnings,
        each file it saved (its ``saved`` event, the ``STEP saved`` moment, a mesh's ``wrote``
        line), that a newer result superseded it, and the summary line. Its failure is raised:
        the job kept it on the record for the next run."""
        job, self.job = self.job, None
        if job is None:
            self.request = None
            return
        from cadgen.daemon import executors

        try:
            outcome = job.result()
        finally:
            self.request = None
        for label, seconds in outcome.get("spans") or ():
            self.logger.timing(label, seconds)
        for warning in outcome.get("warnings") or ():
            self.logger.warning(warning)
        for saved in outcome.get("saved") or ():
            step = self.step_path is not None and saved.get("output") == str(self.step_path)
            elapsed = float(outcome.get("publishedAt") or time.time()) - self.started_at if step else None
            executors.emit_event(executors.model_event(self.model, "building", phase="STEP saved" if step else None,
                                                       elapsed=elapsed, saved=saved))
            if step:
                self.logger.timing(f"STEP saved {self.step_path.name}", elapsed)
        for fmt, path in outcome.get("wrote") or ():
            print(f"[cadgen] wrote {str(fmt).upper()}: {_display_path(Path(path))}", file=sys.stderr)
        if outcome.get("superseded"):
            self._superseded(outcome["superseded"])
        elif self.announce and not outcome.get("abandoned"):
            self.command_logger.info(self.announce)

    def abandon(self) -> None:
        """Leave the job this process submitted: it writes nothing more once it asks whether
        anyone still wants it, and the next run exports again."""
        job, self.job = self.job, None
        self.request = None
        if job is not None:
            job.detach()

    def _superseded(self, path: str) -> None:
        from cadgen.store.index import display_model

        self.command_logger.info(f"{_display_path(Path(path))} not written: a newer build of "
                                 f"{_display_path(Path(display_model(self.model)))} replaced this result, and "
                                 "writes it")


def _edge_visibility_classes_match_manifest(
    manifest: Mapping[str, object],
    selector_options: SelectorOptions,
) -> bool:
    edge_rendering = manifest.get("edgeRendering")
    if not isinstance(edge_rendering, Mapping):
        return False
    return tuple(edge_rendering.get("visibilityClasses") or ()) == tuple(selector_options.edge_visibility_classes)


def _manifest_records_edge_visibility_classes(manifest: Mapping[str, object]) -> bool:
    """Well-formedness, not agreement: does this assembly.json say what it was built with?

    Every assembly.json written by a current build does. One that does not is
    truncated or foreign, and reusing it would serve components whose edge
    classes nothing can name.
    """
    edge_rendering = manifest.get("edgeRendering")
    if not isinstance(edge_rendering, Mapping):
        return False
    return bool(edge_rendering.get("visibilityClasses"))


def _manifest_source_sidecar(manifest: Mapping[str, object]) -> Mapping[str, object]:
    sidecar = manifest.get("_sourceSidecar")
    return sidecar if isinstance(sidecar, Mapping) else {}


def _artifact_source_kind_matches_spec(spec: EntrySpec, manifest: Mapping[str, object]) -> bool:
    # The generated-marker is the PROVENANCE RECORD every generated build writes
    # (source_sidecar.py records tier): the assembly.json itself is STEP-pure and
    # carries no sourceKind. An imported spec whose bytes resolve to a generated
    # model's tree is fine — content keying already guarantees the tree IS
    # these bytes' render.
    generated = bool(_manifest_source_sidecar(manifest))
    if spec.source != "generated" and spec.step_path is not None and spec.step_path.is_file():
        return True
    expected = spec.source == "generated" and spec.script_path is not None
    return generated == expected


def _package_descriptor_matches_spec(
    spec: EntrySpec,
    selector_options: SelectorOptions | None = None,
) -> bool | None:
    """Geometry-only currency, with source provenance for explicit builds.

    The saved byte digest selects a complete native tree. Display derivatives
    do not participate. Generated builds additionally check their provenance
    and source closure; artifact readers never do. Edge visibility policy, when
    explicitly requested, remains descriptor metadata rather than a mesh job.
    """
    from cadgen.catalog import result_descriptor_for

    manifest = result_descriptor_for(spec.entry_path)
    if not isinstance(manifest, dict):
        return None
    if spec.source == "generated":
        # Only a SCRIPT run asks whether this tree is its own model's (the
        # provenance record). A document at a door asks nothing of records:
        # a tree for its bytes is its render (STORE.md §2, the law).
        from cadgen._internal.source_sidecar import read_source_provenance

        provenance = read_source_provenance(spec.entry_path)
        if provenance is not None:
            if provenance.get("kinematics"):
                from cadgen._internal.source_sidecar import source_sidecar_matches_document

                if not source_sidecar_matches_document(spec.entry_path):
                    return False
            manifest["_sourceSidecar"] = provenance
    if not _artifact_source_kind_matches_spec(spec, manifest):
        return False
    if selector_options is None:
        return _manifest_records_edge_visibility_classes(manifest)
    return _edge_visibility_classes_match_manifest(manifest, selector_options)


def _existing_topology_artifact_matches_spec_without_scene(spec: EntrySpec) -> bool:
    """True when the entry's tree is current (no scene needed).

    The assembly.json is the ONLY artifact form, so this is a thin guard
    around :func:`_package_descriptor_matches_spec` (None -> no package -> not
    current)."""
    if spec.step_path is None:
        return False
    return bool(_package_descriptor_matches_spec(spec))


def _existing_topology_artifact_matches_options(spec: EntrySpec, selector_options: SelectorOptions) -> bool:
    """As above, but against explicitly supplied selector options."""
    if spec.step_path is None:
        return False
    return bool(_package_descriptor_matches_spec(spec, selector_options))


def _source_sidecar_payload(scene: LoadedStepScene) -> dict[str, object] | None:
    """The sidecar sections of a GENERATED build or a re-emit, filled in once the result
    tree exists (kinematics, appearance, animation), or None for an import."""
    source_kind = str(getattr(scene, "source_kind", "step") or "step").strip().lower()
    reemit_hash = str(getattr(scene, "reemit_source_hash", "") or "").strip()
    if source_kind != "python" and not reemit_hash:
        return None
    return {}


def _generate_part_outputs(
    spec: EntrySpec,
    *,
    preloaded_scene: LoadedStepScene,
    require_step_file: bool = True,
    force: bool = False,
    logger: CliLogger | None = None,
    progress: object | None = None,
    expected_document_pair: tuple[str | None, str | None] | None = None,
    expected_meshes: dict[str, str | None] | None = None,
) -> GeneratedStepResult:
    logger = logger or CliLogger("cad")
    progress = resolve_progress(progress)
    if spec.step_path is None:
        return GeneratedStepResult(spec=spec, scene=None)
    if expected_document_pair is None and spec.source == "generated":
        expected_document_pair = document_pair_state(spec.step_path)
    if require_step_file:
        _ensure_step_ready(spec.step_path)
    if preloaded_scene.step_path != spec.step_path.expanduser().resolve():
        raise RuntimeError(
            f"Preloaded STEP scene path {preloaded_scene.step_path} does not match {_display_path(spec.step_path)}"
        )
    scene = preloaded_scene
    # Raw document compilation publishes the byte-derived canonical tree,
    # whose edge classes are fixed by build_document_tree. Re-emits and
    # Python-backed scenes are walked as compounds, whose trees classify their
    # own edges from the topology they are built from.
    raw_document = (
        spec.source != "generated"
        and str(getattr(scene, "source_kind", "step") or "step").strip().lower() != "python"
        and not str(getattr(scene, "reemit_source_hash", "") or "").strip()
    )
    if (
        spec.source != "generated"
        and not force
        and _existing_topology_artifact_matches_options(spec, _selector_options_for_part(spec))
    ):
        logger.debug(f"reused current tree: {_display_path(spec.step_path)}")
        from cadgen.catalog import result_tree_for

        return GeneratedStepResult(spec=spec, scene=scene, tree=result_tree_for(spec.step_path))

    # UNIFIED render artifact: every model — part or assembly, generated or imported — is
    # a TREE (a store object keyed by content hash: assembly.json plus content-addressed
    # components). Packaging follows the shape itself: a Compound placing children
    # becomes occurrences (links where a child is an intact model result); anything
    # else is one component. No declaration steers it and nothing is inferred from
    # source — the tree's entryKind is read off the tree once built.
    source_compound = getattr(scene, "source_compound", None)

    def component_package_job() -> dict[str, object]:
        from pathlib import Path

        shape = source_compound
        if shape is None and not raw_document:
            # Generated/re-emitted scenes without an authored compound need
            # this wrapper. Raw documents go directly to build_document_tree;
            # constructing the same hierarchy here would be discarded.
            from cadgen._internal.step_scene_mesh import scene_to_build123d_compound

            shape = scene_to_build123d_compound(scene)
        from cadgen._internal.source_sidecar import remove_source_sidecar
        from cadgen.store.build import build_tree_from_compound
        from cadgen.store.records import read_record, write_record

        with logger.timed("tree: sidecar payload"):
            sidecar_payload = _source_sidecar_payload(scene)
        generated = sidecar_payload is not None

        from cadgen.daemon import executors

        # The worker can attest its actual display producer without deriving
        # display data. Unknown display capability never prevents native publication.
        producer = _producer_or_none()

        def tree_kinematics(result_hash: str, display):
            declaration = getattr(scene, "kinematics", None)
            if not declaration:
                return None
            from cadgen._internal.kinematics_resolve import resolve_kinematics_block

            # Mates resolve against the result's occurrences and labels; only an
            # axis ref reads a component's topology, and only its own: once ``display``,
            # the job deriving this build's parts, is done.
            with logger.timed("tree: kinematics"):
                resolved, _ = resolve_kinematics_block(
                    declaration, tree_hash=result_hash, source_ref=str(spec.source_ref),
                    producer=producer, ready=display.settle if display is not None else None,
                )
            return resolved

        # What the preview resolved against the result tree; the sidecar carries the same.
        resolved: dict[str, object] = {}

        def publish_preview(result_hash: str, result_tree: dict):
            """The preview revision (STORE.md §9b), the moment the result exists: its tree and
            parts announced -- those the store holds, and those the build pool's jobs for the
            rest derive (``pending``) -- then its annotations as one object. The jobs are the
            run's to owe (:class:`_OwedDisplay`): in a daemon worker the supervisor names each
            part in the preview as its job stores it (``daemon/jobs.py``); with no worker the
            run awaits them, and the preview names them once they have. The role, output path
            and progress belong to this request, never in the content-addressed tree or the
            saved document's index: a build's status names objects, and its readers take
            neither geometry nor annotations from it."""
            from cadgen._internal.preview_revision import annotations_object
            from cadgen.authoring import current_run
            from cadgen.store.parts import part_keys, stored_keys

            if spec.source == "generated":
                executors.emit_source_result(_model_for_spec(spec), result_hash)
            output = str(spec.step_path.expanduser().resolve())
            announce = writes_step and executors.sink_installed()
            keys, selected = {}, None
            phase = "Saving STEP" if writes_step else None

            def preview_event() -> None:
                if not announce:
                    return
                stored = stored_keys(keys)
                executors.emit_event(executors.model_event(
                    _model_for_spec(spec), "building", phase=phase,
                    preview={"output": output, "tree": result_hash, "producer": selected, "parts": stored,
                             "pending": {cid: key for cid, key in keys.items() if cid not in stored},
                             "complete": bool(keys) and len(stored) == len(keys)}))

            display = None
            if producer is not None:
                # The parts the store holds at once, so a viewer draws them; this build's own
                # others as the pool's jobs store them. A child's parts are its own build's.
                keys, selected = part_keys(result_hash, producer=producer)
                display = _ask_display(result_hash, producer=producer, cids=list(result_tree.get("components") or {}),
                                       model=_model_for_spec(spec), name=spec.display_name)
                # Announced before its jobs start, so the ledger knows what they derive.
                preview_event()
                if display is not None:
                    display.start(current_run(), on_done=preview_event)
            else:
                # No display producer here: the parts are a view's to find, as for a saved file.
                preview_event()
            resolved["display"] = display
            # The declarations, resolved against the result tree: for the sidecar below,
            # and for the preview as the one annotations object a viewer binds to. An axis
            # ref reads its component's selector table once the display job is done. The
            # clips are baked once: the file is written from this tree, so its sidecar
            # carries these very keyframes -- those the stored result of this key and tree
            # carries already, where there is one (``_stored_animation``).
            resolved["kinematics"] = tree_kinematics(result_hash, display=display)
            clips = getattr(scene, "animation", None)
            if writes_step and clips is not None:
                from cadgen._internal.animation_bake import bake_document_animation

                baked = _stored_animation(scene, _model_for_spec(spec), result_hash)
                resolved["animation"] = baked if baked is not None else bake_document_animation(clips, result_hash)
            if announce:
                annotations = annotations_object(
                    result_hash, kinematics=resolved["kinematics"],
                    appearance=result_tree.get("appearance"), animation=resolved.get("animation"))
                if annotations is not None:
                    executors.emit_event(executors.model_event(
                        _model_for_spec(spec), "building",
                        annotations={"output": output, "tree": result_hash, "object": annotations}))
        # Objects first: components + tree. Harmless if this build ends up not
        # publishing its record (publish rule below) — content-addressed and GC'd.
        writes_step = generated and bool(spec.step_output)
        # The model's run, which names each component it made by its construction key
        # and its lineage (README law 20); a document compiled from its bytes has none.
        kernel_run = getattr(scene, "kernel_run", None) if generated else None
        namer = kernel_run
        # The build's own live shapes for the components the store cannot decode, which
        # its STEP is written from in this process (store/exports.py).
        live: dict[str, object] = {}
        with logger.timed("tree: components"):
            if not generated:
                from cadgen.store.build import build_document_tree

                tree_hash, tree, stats = build_document_tree(scene, force=force, progress=progress)
            else:
                tree_hash, tree, stats = build_tree_from_compound(
                    shape, root_name=_document_root_name(shape, spec.step_path), force=force,
                    progress=progress, materials=getattr(scene, "materials", None), namer=namer, live=live,
                )
                # The preview, before any export work: the result is what the file is
                # written from.
                publish_preview(tree_hash, tree)
        stats["tree"] = tree_hash
        if producer is not None and not generated:
            # A document's native result is ready independently of its display data: its
            # compile answers with the tree, and the display jobs are the run's to owe, as a
            # source build's are.
            _request_display(tree_hash, producer=producer, model=_model_for_spec(spec), name=spec.display_name)
        # The build's account of its code (STORE.md §3): the features its run called,
        # where each ran, the components it made, each with the feature every name of
        # its lineage stands for, and each occurrence of one with the feature whose call
        # made the shape it places; written beside the record, after it, for a model
        # whose saved document `store where` can be asked about.
        provenance = None
        if kernel_run is not None and writes_step:
            provenance = {"features": kernel_run.trace.feature_rows(), **stats["account"]}

        model_path = _model_for_spec(spec)
        outputs: dict[str, object] = {}
        step_key = str(spec.step_path.expanduser().resolve())
        from cadgen._internal.source_sidecar import source_sidecar_path

        sidecar_key = str(source_sidecar_path(spec.entry_path).resolve())
        previous = (read_record(model_path) or {}) if generated else {}
        export_plan = _export_plan(previous, step_key, tree_hash, force=force,
                                   on_disk=(expected_document_pair or (None, None))[0]) if writes_step else None
        if writes_step:
            # The record is written at the source result, its STEP pending until the
            # export lands (an empty sha256 the gate reads as never written).
            outputs[step_key] = export_plan.output()
        # An imported document and anything beside it are authored inputs. Cache
        # publication must not add, rewrite, or remove either one.

        # The record. Publish rule: never replace a current record with a stale one.
        closure_hash = str(getattr(scene, "source_closure_hash", "") or "")
        closure_files = list(getattr(scene, "source_closure_files", ()) or ())
        closure_shas = dict(getattr(scene, "source_closure_file_hashes", None) or {})
        closure_names = {rel: list(names) for rel, names in (getattr(scene, "source_closure_names", None) or {}).items()}
        closure_wholes = dict(getattr(scene, "source_closure_wholes", None) or {})
        closure_own = {rel: list(names) for rel, names in (getattr(scene, "source_closure_own", None) or {}).items()}
        closure_static = False
        reemit_source_hash = getattr(scene, "reemit_source_hash", None)
        if not generated:
            # An imported document's closure is the document itself.
            from cadgen.store.closure import closure_hash as _closure_hash

            step_hash = str(getattr(scene, "step_hash", "") or "") or step_file_hash(spec.step_path)
            closure_files = [spec.step_path.name]
            closure_shas = {spec.step_path.name: step_hash}
            closure_names, closure_wholes, closure_own = {}, {}, {}
            closure_hash = _closure_hash([(spec.step_path.name, step_hash)])
        elif reemit_source_hash and not closure_hash:
            # A re-emitted document (`cadgen step build IN OUT`): its source is
            # another document's bytes plus the author's annotation, both compared
            # by that door — no files for the gate to re-hash.
            from cadgen.store.closure import closure_hash as _closure_hash

            closure_files = []
            closure_names, closure_wholes, closure_own = {}, {}, {}
            closure_hash = _closure_hash(
                [("reemit", str(reemit_source_hash)), ("annotation", str(getattr(scene, "reemit_annotation_hash", "") or ""))]
            )
            closure_static = True
        # Declared mesh exports recorded by earlier runs stay listed: each one
        # carries the document hash it was cut from, so the mesh gate re-checks
        # it against THIS document and re-exports only what no longer matches.
        # A foreign compile derives its result from document bytes alone.
        # Only generated jobs retain declarations from an earlier model run.
        for output_path, entry in (previous.get("outputs") or {}).items():
            if isinstance(entry, dict) and entry.get("declared") and output_path not in outputs:
                outputs[output_path] = entry
        # Every declared mesh output is listed from the first publish, sha-less
        # until its export writes it (record_mesh_export fills the entry), so a
        # declaration the exporter failed to honour reads as STALE at the next
        # gate ("never written") instead of as a current model missing an output.
        from cadgen.store.trees import tree_kind

        record = {
            "entryKind": tree_kind(tree),
            "sourceKind": "step" if (not generated or reemit_source_hash) else "python",
            "tree": tree_hash,
            "unannotatedTree": str(stats.get("unannotatedTree") or tree_hash),
            "closure": {"hash": closure_hash, "files": closure_files, "shas": closure_shas, "names": closure_names,
                        "wholes": closure_wholes, "own": closure_own, "static": closure_static},
            # Literals imported from model files, tracked by VALUE (gate clause 2).
            "constants": dict(getattr(scene, "source_closure_constants", None) or {}) if generated else {},
            # Every child the body called, by the key it composed (store/results.py).
            "children": _children_by_key(getattr(scene, "store_children", None)),
            "outputs": outputs,
        }
        if generated:
            _record_mesh_recipes(spec, record)
        from cadgen.store.results import producer_versions
        from cadgen.store.trees import get_tree

        record["producer"] = producer_versions()

        unannotated = get_tree(str(record["unannotatedTree"]))
        intrinsic_appearance = (unannotated or {}).get("appearance")
        if intrinsic_appearance is not None:
            record["intrinsicAppearance"] = copy.deepcopy(intrinsic_appearance)
        if reemit_source_hash:
            record["sourceHash"] = str(reemit_source_hash)
            record["annotationHash"] = str(getattr(scene, "reemit_annotation_hash", "") or "")
            record["inputAppearance"] = str(getattr(scene, "reemit_appearance_hash", "") or "")
        if generated:
            kinematics_block = getattr(scene, "kinematics", None)
            if kinematics_block:
                # Bound to the result's tokens, which the file's are.
                sidecar_payload["kinematics"] = resolved["kinematics"]
            if tree.get("appearance") is not None:
                record["appearance"] = copy.deepcopy(tree["appearance"])
            if writes_step:
                appearance = tree.get("appearance")
                if appearance is not None:
                    sidecar_payload["appearance"] = appearance
                if resolved.get("animation") is not None:
                    sidecar_payload["animation"] = resolved["animation"]
            if sidecar_payload.get("kinematics") is not None:
                record["kinematics"] = sidecar_payload["kinematics"]
            if getattr(scene, "materials", None) is not None:
                record["materials"] = copy.deepcopy(scene.materials)
            if sidecar_payload.get("animation") is not None:
                # The keyframes, not the clips: an annotation refresh rewrites the
                # sidecar from the record without running the model.
                record["animation"] = sidecar_payload["animation"]
            if sidecar_payload.get("appearance") is not None:
                record["appearance"] = copy.deepcopy(sidecar_payload["appearance"])
            if writes_step:
                from cadgen._internal.source_sidecar import sidecar_is_warranted, sidecar_sections

                # A sidecar this build will write is pending with the STEP until the export
                # lands; a missing one is then a run's to write again (gate clause 5). A
                # model that declares nothing lists none.
                if sidecar_is_warranted(sidecar_sections(sidecar_payload)):
                    outputs[sidecar_key] = {"sha256": ""}
        if record["sourceKind"] == "python" and spec.script_path is not None:
            from cadgen._internal.annotation_refresh import capture_geometry_closure

            entry_name = getattr(spec.generator_metadata, "entry_function", None)
            if entry_name:
                geometry_closure = capture_geometry_closure(
                    spec.script_path, record["closure"], entry_name=entry_name
                )
                if geometry_closure is not None:
                    record["geometryClosure"] = geometry_closure
        if generated:
            from cadgen._internal.build_timing import record_fields
            from cadgen.store.trees import claim_tree

            # The model-code time the next build compares itself against.
            record.update(record_fields())

            # Claimed, not just checked: the record below names this closure, so no
            # sweep may take any part of it from here on (STORE.md §8).
            if not claim_tree(tree_hash):
                raise RuntimeError(f"{spec.cad_ref}: result was not saved: the result's geometry disappeared from the cache during the build")
            # A re-emitted STEP has an immutable byte/annotation input closure,
            # not a Python source closure that current_closure_hash can read.
        if generated and not closure_static:
            # The publish rule (``store.publish.decide``): a newer build of the model may have
            # published since this one read the sources. Asked before the record; the files
            # are written for the result the record then names (``_Export``).
            from cadgen.store.publish import decide

            decision = decide(model_path, ran_closure_hash=closure_hash, ran_files=closure_files, ran_names=closure_names,
                              ran_shas=closure_shas, ran_wholes=closure_wholes, ran_own=closure_own)
            if not decision.publish_outputs:
                raise RuntimeError(f"{spec.cad_ref}: result was not saved: {decision.reason}")
        from cadgen.store.records import note_document_tree, note_output

        if not generated:
            # Artifact side: the bytes of the document this tree describes → the tree
            # (a reader's one lookup; STORE.md §2).
            note_document_tree(step_hash, tree_hash, producer=producer)
        if generated:
            if not writes_step:
                # Mesh-only models have no STEP or kinematics sidecar.
                remove_source_sidecar(spec.entry_path)
            # Code side: which model writes each output path (the badge's question,
            # never a reader's).
            for output_path in outputs:
                note_output(output_path, model_path)
        # Keyed, and stored under its key, as it is written (store/results.py): what
        # a later build of these inputs restores.
        key = write_record(model_path, record)
        if provenance is not None:
            from cadgen.store.provenance import write_provenance

            # Beside the record and after it: code side, read by `store where`
            # and by nothing on a render path; kept with the result.
            write_provenance(model_path, provenance, tree=tree_hash)
        if kernel_run is not None:
            # The run's trace was this job's: nothing after it reads the run.
            scene.kernel_run = None
        if generated:
            # What the build still owes, queued: its files' pool job starts now and writes
            # and publishes them on its own (_Export) -- after this build's done, while a
            # parent's body goes on.
            kept = writes_step and export_plan.kept is not None
            if kept:
                export_plan.map_kept(tree_hash)
            summary = (f"kept STEP: {_display_path(spec.step_path)} (its bytes would not change)" if kept
                       else _generated_python_glb_summary(spec))
            _Export(spec, tree_hash, key=key, logger=logger, announce=None if reemit_source_hash else summary,
                    expected_meshes=expected_meshes, previous=previous, captured_record=record,
                    displays=[resolved.get("display")],
                    step={"plan": export_plan, "expected": expected_document_pair, "live": live}
                    if writes_step else None)
        stats["key"] = key
        return stats

    # A build that fails after its preview leaves the preview standing under the failure: its
    # parts' jobs are the run's to owe (:class:`_OwedDisplay`), and go on.
    with logger.timed("write tree"):
        tree_result = component_package_job()
    return GeneratedStepResult(spec=spec, scene=scene, tree=str(tree_result.get("tree") or "") or None,
                               key=tree_result.get("key"))


def _stored_animation(scene: object, model: str | None, tree: str) -> dict | None:
    """The keyframes a stored result of this very build carries -- the result at the key its
    record will have (its model, closure, constants, children's keys and producer,
    ``store.results``) whose tree is ``tree`` -- else None. A result is a function of its key
    (the gate restores one by it, STORE.md §4), and its clips' bake a function of the clips its
    closure holds and of its tree: baking them again gives these, so a model rebuilt with
    nothing changed does not."""
    closure_hash = str(getattr(scene, "source_closure_hash", "") or "")
    if not closure_hash or model is None:
        return None
    from cadgen.store.results import key_for, producer_versions, read_result

    key = key_for({"closure": {"hash": closure_hash},
                   "constants": dict(getattr(scene, "source_closure_constants", None) or {}),
                   "children": _children_by_key(getattr(scene, "store_children", None)),
                   "producer": producer_versions()}, model)
    result = read_result(key) or {}
    animation = (result.get("record") or {}).get("animation") if result.get("tree") == tree else None
    return copy.deepcopy(animation) if isinstance(animation, dict) else None


def _children_by_key(called: object) -> list[dict[str, str]]:
    """The record's ``children``: each model the body called, once, by the key of the
    result the call composed (``cadgen.authoring``), in the order first called."""
    children: dict[str, str] = {}
    for child in called or ():
        model, key = str((child or {}).get("model") or ""), str((child or {}).get("key") or "")
        if model and model not in children:
            children[model] = key
    return [{"model": model, "key": key} for model, key in children.items()]


def _generate_step_outputs(
    spec: EntrySpec,
    *,
    generator: Callable[[], object] | None = None,
    force: bool = False,
    logger: CliLogger | None = None,
    progress: object | None = None,
    verdict: object | None = None,
) -> GeneratedStepResult:
    """``verdict`` is the gate's verdict this job already took for ``spec``
    (:func:`build_model`); without one the gate is asked here. ``generator`` is
    the model's function when its module is loaded already (a child), else None
    (``run_script_generator``). ``spec`` is a model script's (:func:`build_model`)."""
    if not force and verdict is None:
        verdict = _gate_verdict(spec)
    if not force:
        from cadgen._internal.annotation_refresh import refresh_annotations

        refreshed_tree = refresh_annotations(spec, verdict=verdict)
        if refreshed_tree is not None:
            from cadgen.store.records import read_record

            _hit(spec, refreshed_tree, logger=logger or CliLogger("cadgen"), verdict=verdict)
            refreshed = read_record(_model_for_spec(spec)) or {}  # the record the refresh just wrote
            return GeneratedStepResult(spec=spec, scene=None, tree=refreshed_tree, key=refreshed.get("key"))
    reuse_tree = _checked_source_tree(spec, verdict) if not force else None
    # Reuse fast path: skip the build when the tree is already present and current and
    # nothing forces a run. A model's freshness rides on its recorded source closure. A
    # stale verdict settles it before the descriptor check, which hashes the saved
    # document (and, for a model with kinematics, its sidecar's binding to it).
    if not force and reuse_tree is not None and _existing_topology_artifact_matches_spec_without_scene(spec):
        if logger is not None:
            logger.debug(f"reused current tree: {_display_path(spec.step_path)}")
        # A peer published the model while this run waited for its slot: what it may
        # still owe is queued as for any current model.
        _hit(spec, reuse_tree, logger=logger or CliLogger("cadgen"), verdict=verdict)
        return GeneratedStepResult(spec=spec, scene=None, tree=reuse_tree,
                                   key=verdict.key if verdict is not None else None)
    output_kwargs: dict[str, object] = {
        "force": force,
        "progress": progress,
    }
    if logger is not None:
        output_kwargs["logger"] = logger
    if spec.step_path is not None:
        output_kwargs["expected_document_pair"] = document_pair_state(spec.step_path)
    output_kwargs["expected_meshes"] = _mesh_file_state(spec)
    output_kwargs["preloaded_scene"] = run_script_generator(
        spec,
        "step",
        generator=generator,
        logger=logger,
        progress=progress,
    )
    # A @step entry never writes a STEP, so the artifact pipeline must not require one.
    output_kwargs["require_step_file"] = False
    return _generate_part_outputs(spec, **output_kwargs)


class _OwedDisplay:
    """The display data a result owes: the build pool's jobs deriving each part of its tree the
    store lacks (``store.parts.display_requests``) -- ``requests``, one or a slot's share each
    of a large batch -- and the part key of each part they derive (``keys``). Started by
    :meth:`start`: handed to the daemon worker's supervisor, which sees them to their end and
    names each part in the build's preview as its job stores it (``daemon/jobs.py``), or, with no
    worker, submitted from this process and awaited by the run before the command answers
    (:func:`_await_display`). Never awaited on a worker's build thread: a mate's axis ref that
    reads a part's selector table settles them (:meth:`settle`), and the result's export waits
    for them before its file lands (``store.exports``)."""

    def __init__(self, requests: list[dict], keys: dict[str, str], placed: dict, *, model: str,
                 name: str | None) -> None:
        self.requests, self.keys, self.placed, self.model, self.name = requests, keys, placed, model, name
        self.job: object | None = None

    def start(self, run: object, *, on_done: Callable[[], None] | None = None) -> None:
        """Hand the jobs to the supervisor, or submit them here (``on_done`` called once they
        have all finished, however they finished)."""
        if run is None:
            raise RuntimeError(f"{self.name or self.model}: display data was asked for outside a build run")
        if run.hand_off is not None:
            for request in self.requests:
                run.hand_off(request, self.model)
            return
        from cadgen.store.parts import submit_display

        self.job = submit_display(self.requests, self.keys, self.placed)
        run.displays.append((self, self.name))
        if on_done is not None:
            self.job.when_done(lambda _job: on_done())

    def settle(self) -> None:
        """Wait for every job to finish, however it finishes: this process's own, or the
        supervisor's, attached to by asking for the same work (the pool coalesces it)."""
        if self.job is not None:
            self.job.settle()
            return
        from cadgen.daemon.artifacts import resolve_artifact

        for request in self.requests:
            try:
                resolve_artifact(request)
            except Exception:  # noqa: BLE001 - a part it could not derive is the display data's warning
                continue

    def wait(self) -> list[str]:
        """The parts this process's jobs could not derive (``DisplayJob.wait``)."""
        return self.job.wait() if self.job is not None else []

    def abandon(self) -> None:
        if self.job is not None:
            self.job.abandon()


def _ask_display(tree: str, *, model: str, name: str | None, producer: dict | None = None,
                 cids: list[str] | None = None) -> _OwedDisplay | None:
    """The display data ``tree`` owes (:class:`_OwedDisplay`), not yet started: every part the
    store lacks under ``producer`` (this runtime's when None), of ``cids`` when given -- a
    build's own components: a child queues its own. None when nothing is missing, or when the
    jobs could not be asked for (a warning: the first view derives the parts)."""
    if not tree:
        return None
    from cadgen.store.parts import display_requests

    try:
        asked = display_requests(tree, producer=producer, cids=cids)
    except Exception as error:  # noqa: BLE001 - the build's result stands; its display data waits for a view
        _note_warnings([f"display data not derived ({type(error).__name__}: {error}); the first view derives it"])
        return None
    return _OwedDisplay(*asked, model=model, name=name) if asked is not None else None


def _request_display(tree: str, *, model: str, name: str | None, producer: dict | None = None) -> _OwedDisplay | None:
    """:func:`_ask_display`, started at once in the current run."""
    from cadgen.authoring import current_run

    display = _ask_display(tree, producer=producer, model=model, name=name)
    if display is not None:
        display.start(current_run())
    return display


def _await_display(display: _OwedDisplay | None, *, name: str | None = None) -> None:
    """Wait for the display jobs this process submitted (:class:`_OwedDisplay`), when there are
    any. A part they could not derive is a warning on the build, never its failure: a view, a
    snapshot and a list leave that part out and name it."""
    if display is None:
        return
    progress = resolve_progress(None)
    progress.phase(PHASE_MESH, total=len(display.keys))
    try:
        texts = [f"{failure}; views leave it out" for failure in display.wait()]
    except Exception as error:  # noqa: BLE001 - the build's result stands; its display data waits for a view
        texts = [f"display data not derived ({type(error).__name__}: {error}); the first view derives it"]
    progress.advance(len(display.keys))
    _note_warnings([f"{name}: {text}" for text in texts] if name else texts)


def _note_warnings(texts: list[str]) -> None:
    from cadgen._internal.build_timing import note_warning

    for text in texts:
        # On the done event, which reaches the root from a child whose own output is
        # dropped; outside a build, stderr (stdout is the result channel).
        if not note_warning(text):
            print(f"[cadgen] warning: {text}", file=sys.stderr)


def _ensure_reused_display(spec: EntrySpec, tree: str | None, *, written: bool = False) -> _OwedDisplay | None:
    """The display data of a build that reuses its tree, asked of the pool where the store
    lacks it (:func:`_request_display`): the tree its saved document selects, under the
    producer that document names -- what a view of it reads -- or, for a STEP its export writes
    again (``written``), the result tree under the producer the export writes with (this
    runtime's), which the file's bytes then map to; a mesh-only model's own tree."""
    producer = None
    if spec.step_output and spec.entry_path is not None and not written:
        from cadgen.catalog import result_snapshot_for
        from cadgen.store.records import document_entry_for_hash

        snapshot = result_snapshot_for(spec.entry_path)
        if snapshot is None:
            return None
        document_hash, tree = snapshot
        producer = _reused_display_producer(document_hash, tree, document_entry_for_hash(document_hash) or {})
    return _request_display(tree or "", producer=producer, model=_model_for_spec(spec), name=spec.display_name)


def _reused_display_producer(document_hash: str, tree: str, entry: Mapping[str, object]) -> object:
    """The producer a reused build derives its document's display data under.

    The document ``entry``'s hint while this runtime implements it, or while the store
    holds every part under it (a view reads those). Otherwise -- the kernel was upgraded,
    or another cadgen wrote the hint -- this runtime's, and the document is re-attested
    with it, so neither this build nor a view asks again for parts no worker can make.
    Re-attesting is best effort: a store this process cannot write keeps the hint it has."""
    import contextlib

    from cadgen.store import parts

    hint = entry.get("producer")
    try:
        current = parts.producer_identity()
        hinted = parts.producer_fields(hint) if hint is not None else None
    except (ValueError, TypeError):
        return hint
    if hinted is None or hinted == current or parts.all_stored(tree, hinted):
        return hint
    from cadgen.store.paths import StoreUnwritableError
    from cadgen.store.records import note_document_tree

    with contextlib.suppress(OSError, StoreUnwritableError):
        note_document_tree(document_hash, tree, kind=str(entry.get("kind") or "step"), producer=current)
    return current


def _record_mesh_recipes(spec: EntrySpec, record: dict) -> bool:
    """Persist each declared mesh recipe before its export starts. A changed recipe
    owes new bytes even when the geometry key and tree have not changed."""
    from cadgen._internal.mesh_export import mesh_recipe

    outputs = record.setdefault("outputs", {})
    changed = False
    for declared in spec.mesh_exports:
        path = str(Path(declared.path).expanduser().resolve())
        recipe = mesh_recipe(declared.fmt, declared.mesh_tolerance, declared.mesh_angular_tolerance,
                             spec.step_path.stem)
        old = outputs.get(path) or {}
        source = ((outputs.get(str(spec.step_path.expanduser().resolve())) or {}).get("sha256")
                  if spec.step_output else record.get("tree"))
        if (any(old.get(field) != value for field, value in recipe.items())
                or (old.get("sha256") and old.get("document") != source)):
            outputs[path] = {"sha256": "", **recipe}
            changed = True
    return changed


def _mesh_file_state(spec: EntrySpec) -> dict[str, str | None]:
    """The declared mesh bytes a run found, captured before its body runs."""
    return {str(Path(item.path).expanduser().resolve()): sha256_or_none(Path(item.path))
            for item in spec.mesh_exports}


def _mesh_exports_owed(spec: EntrySpec, *, source_tree: str | None = None,
                       record: Mapping[str, object] | None = None) -> tuple | None:
    """What the model's declared meshes are cut from and which of them are not current on
    disk (``store.exports``'s mesh job cuts them): ``(model, tree, jobs)``, the jobs those to
    cut; None while nothing names what they are cut from (a STEP whose bytes the record does
    not name yet)."""
    if not spec.mesh_exports or spec.entry_path is None or spec.step_path is None:
        return None
    from cadgen._internal.mesh_export import MeshExportJob, mesh_export_current
    from cadgen._internal.source_sidecar import appearance_digest
    from cadgen.store.records import read_record

    model = _model_for_spec(spec)
    record = record if record is not None else (read_record(model) if model is not None else None) or {}
    tree_hash = source_tree or str(record.get("tree") or "") or None
    if spec.step_output:
        output = (record.get("outputs") or {}).get(str(spec.step_path.expanduser().resolve())) or {}
        document_hash = str(output.get("sha256") or "") or None
    else:
        document_hash = tree_hash
    if document_hash is None or tree_hash is None or model is None:
        return None
    appearance_key = appearance_digest(record.get("appearance"))
    jobs = [
        MeshExportJob(fmt=declared.fmt, out=declared.path, mesh_tolerance=declared.mesh_tolerance,
                      mesh_angular_tolerance=declared.mesh_angular_tolerance)
        for declared in spec.mesh_exports
        if not mesh_export_current(declared.path, model=model, document_hash=document_hash,
                                   mesh_tolerance=declared.mesh_tolerance,
                                   mesh_angular_tolerance=declared.mesh_angular_tolerance,
                                   appearance_key=appearance_key)
    ]
    return model, tree_hash, jobs


def _note_meshes_saved(spec: EntrySpec, model: str, tree: str) -> None:
    """Each declared mesh's ``saved`` event, as its file now stands -- cut now or current
    already -- with the digest the record names: the end of what the build owed there."""
    from cadgen.daemon import executors
    from cadgen.store.records import read_record

    outputs = (read_record(model) or {}).get("outputs") or {}
    for declared in spec.mesh_exports:
        path = str(Path(declared.path).expanduser().resolve())
        digest = str((outputs.get(path) or {}).get("sha256") or "") or None
        executors.emit_event(executors.model_event(model, "building", saved={
            "output": path, "tree": tree, "documentHash": digest}))


def _selected_specs_for_targets(targets: Sequence[str]) -> list[EntrySpec]:
    """The targets' own specs. A target is a model script or a document — its outputs
    are what it declares (``out=``); nothing on the command line renames them."""
    explicit_specs: list[EntrySpec] = []
    unresolved_targets: list[str] = []
    from cadgen.store.index import MODEL_REF_SEP

    for target in targets:
        target_text = str(target or "").strip()
        # ``script.py::fn`` names one model of a file holding several.
        function: str | None = None
        if MODEL_REF_SEP in target_text:
            target_text, _, function = target_text.rpartition(MODEL_REF_SEP)
        target_path = Path(target_text)
        resolved = target_path.resolve() if target_path.is_absolute() else (Path.cwd() / target_path).resolve()
        source = (
            source_from_path(resolved, function=function or None)
            if resolved.exists()
            else None
        )
        if source is None:
            unresolved_targets.append(target_text)
            continue
        explicit_specs.append(_entry_spec_from_source(source))

    if not unresolved_targets:
        return explicit_specs

    unresolved = ", ".join(unresolved_targets)
    raise FileNotFoundError(
        f"not a model script cadgen can build: {unresolved}. A model is a Python script that "
        "decorates a function with @step, @dxf or a mesh decorator (@stl/@glb/@threemf); "
        "run it with `python <model>.py`."
    )


def _validate_step_target(spec: EntrySpec, *, tool_name: str) -> None:
    if spec.step_path is None:
        raise ValueError(f"{tool_name} target has no STEP path: {spec.source_ref}")
    if spec.source == "generated":
        metadata = spec.generator_metadata
        if metadata is None or metadata.format != "step":
            raise ValueError(f"{tool_name} target is not a @step model: {spec.source_ref}")
        return
    raise ValueError(
        f"{tool_name} builds model scripts only: {spec.source_ref} is a document. A STEP/STP "
        "file needs no build -- every command that reads one (snapshot, the mesh doors, the "
        "CAD Viewer) compiles its tree on demand; `cadgen step build IN OUT` re-emits one."
    )


def _validate_dxf_target(spec: EntrySpec) -> None:
    metadata = spec.generator_metadata
    if spec.source != "generated" or spec.script_path is None or metadata is None:
        raise ValueError(f"dxf expected a generated Python source target: {spec.source_ref}")
    if metadata.format != "dxf":
        raise ValueError(f"dxf target is not a @dxf model: {spec.source_ref}")
    if spec.dxf_path is None:
        raise ValueError(f"dxf target has no configured DXF output: {spec.source_ref}")


def _generated_python_glb_summary(spec: EntrySpec) -> str:
    if spec.step_path is not None and not getattr(spec, "step_output", True):
        # A mesh-only model: step_path is the logical document the store keys by,
        # never a file it wrote. Each mesh was already named by the line that
        # wrote it (`wrote STL: …`), so the summary names the model once.
        count = len(spec.mesh_exports or ())
        return f"built {spec.source_ref} ({count} mesh output{'s' if count != 1 else ''})"
    if spec.step_path is not None:
        return f"wrote STEP: {_display_path(spec.step_path)}"
    return f"processed: {spec.source_ref}"


def _generated_dxf_summary(spec: EntrySpec) -> str:
    output = spec.dxf_path
    if output is not None:
        return f"wrote DXF: {_display_path(output)}"
    return f"processed: {spec.source_ref}"


def _tree_event(spec: EntrySpec, state: str, **extra: object) -> None:
    """One model transition for the build tree (cadgen.cli_tree), with the model whose
    body called this one as its ``parent``. Generated models only: an imported document
    has no body and no children to show."""
    model = _model_for_spec(spec)
    if model is None:
        return
    from cadgen.daemon.executors import emit_event, model_event

    emit_event(model_event(model, state, parent=_event_parent(model), **extra))


def _event_parent(model: str) -> str | None:
    """The model whose body called ``model``, for its events: the body in flight on this
    thread (a child's build runs inside its caller's body), or, while ``model``'s own
    body runs, the frame it was entered from. None for the root."""
    from cadgen.authoring import current_frame

    frame = current_frame()
    if frame is None or frame.model is None:
        return None
    return frame.parent if frame.model == model else frame.model


def _current_source_result(spec: EntrySpec, tree: str | None) -> None:
    """Capture the current source result now; consumers never reread the record."""
    if spec.source != "generated" or spec.dxf_path is not None:
        return
    from cadgen.daemon.executors import emit_source_result
    model = _model_for_spec(spec)
    emit_source_result(model, tree)


def _tree_progress_sink(spec: EntrySpec, inner: object | None) -> Callable[[ProgressEvent], None]:
    """Fan a run's phase events out to the caller's sink AND the build tree."""

    def sink(event: ProgressEvent) -> None:
        if inner is not None:
            inner(event)  # type: ignore[operator]
        if event.phase == "done":
            return
        _tree_event(
            spec, "building", phase=event.label or event.phase,
            done=event.done if event.determinate else None,
            total=event.total if event.determinate else None,
            # Empty, not absent: a new phase's job must not keep the last one's detail.
            detail=event.detail or "",
        )

    return sink


class _SkippedGeneration:
    """Marker: a concurrent run ahead of us had already produced a current result."""

    __slots__ = ("spec", "tree")

    def __init__(self, spec: EntrySpec, tree: str | None = None) -> None:
        self.spec = spec
        self.tree = tree


def _run_with_spec_generation_status(
    spec: EntrySpec,
    model_format: str,
    action: Callable[..., object],
    *,
    skip_if_current: Callable[[EntrySpec], bool | str | None] | None = None,
    progress_sink: object | None = None,
    on_queued: Callable[[], None] | None = None,
    profile: bool = False,
    on_timed: Callable[[dict[str, float]], None] | None = None,
) -> object:
    """Run ``action`` under the model's progress record.

    ``on_queued`` is told when the job had to wait for a slot before its body.

    The build is measured (cadgen._internal.build_timing): its ``done`` event carries
    the time split the root prints, and ``on_timed`` receives the same numbers for
    the run's result. ``profile`` profiles the model body and the ``done`` event
    carries the report, and the programs the model's code started.

    Delegates to :func:`cadgen.coordination.artifact_build`, the SAME primitive
    ``cadgen.step_artifact_cli`` uses, so every producer reports the same way.

    ``skip_if_current`` is re-evaluated when the run opens: a run that started behind a
    concurrent build of this model no-ops once that build has published.

    ``action`` is called as ``action(spec, run)``; ``run`` is the progress reporter.
    """
    kind = DRAWING_PACKAGE if model_format == "dxf" else STEP_PACKAGE
    started = time.perf_counter()
    checked_tree = None

    def is_current():
        nonlocal checked_tree
        verdict = skip_if_current(spec)
        checked_tree = verdict if isinstance(verdict, str) else None
        return bool(verdict)

    from cadgen._internal import build_timing

    with artifact_build(
        kind,
        _spec_output_dir(spec, model_format),
        is_current=is_current if skip_if_current is not None else None,
        sink=_tree_progress_sink(spec, progress_sink),
    ) as run, build_timing.measuring(
        last_model_seconds=_last_model_seconds(spec),
        profile=profile,
    ) as clock:
        clock.started = started
        if run.skipped:
            if model_format == "step":
                _current_source_result(spec, checked_tree)
            _tree_event(spec, "current")
            return _SkippedGeneration(spec, checked_tree)
        from cadgen.daemon import broker

        # One running build per core: the body and its emit hold a job slot, and a
        # child's build inside the body runs in the slot its parent holds. `queued`
        # shows in the tree only when the slot did not come at once.
        def queued() -> None:
            _tree_event(spec, "queued")
            if on_queued is not None:
                on_queued()

        waiting_from = time.perf_counter()
        with broker.held(spec.source_ref, on_queued=queued):
            clock.queued = time.perf_counter() - waiting_from
            _tree_event(spec, "building", phase="generate")
            try:
                result = action(spec, run)
            except BaseException:
                _tree_event(spec, "failed", elapsed=time.perf_counter() - started)
                raise
        measured = clock.timings().payload()
        profile_report = clock.profile_report
    if on_timed is not None:
        on_timed(measured)
    timings = {
        **measured,
        "document": _timed_document(spec, model_format),
        # A profiled body is slowed by the profiler: no slowdown to compare.
        "lastModelSeconds": None if profile else clock.last_model_seconds,
    }
    # The programs its code started and its warnings ride the done event as its timings
    # do: the root's build tree prints them once per model (cadgen.cli_tree).
    _tree_event(
        spec, "done", elapsed=time.perf_counter() - started, stale=_stale_after_build(spec),
        timings=timings, profile=profile_report, started=list(clock.programs) or None,
        warnings=list(clock.warnings) or None,
    )
    return result


def _last_model_seconds(spec: EntrySpec) -> float | None:
    """The model-code time the model's record kept from its last build, if any."""
    model = _model_for_spec(spec)
    if model is None or spec.source != "generated":
        return None
    from cadgen.store.records import read_record

    try:
        value = (read_record(model) or {}).get("modelSeconds")
    except Exception:  # noqa: BLE001 - a missing comparison never fails a build
        return None
    return float(value) if isinstance(value, (int, float)) and value > 0 else None


def _timed_document(spec: EntrySpec, model_format: str) -> str | None:
    """The document a time line names: the one the run's result line names."""
    if model_format == "dxf":
        return str(spec.dxf_path.expanduser().resolve()) if spec.dxf_path is not None else None
    return _reported_document(spec)


def _document_root_name(shape: object, step_path: Path) -> str:
    """What a saved document's root is called: the label its author gave it (a
    re-emit's source document named its own root, so OUT keeps IN's name).

    Names are what an author wrote. Unlabelled, an assembly's root is ``model``,
    as a document with no root name is read: a name taken from the file
    would sit among its parts' labels and answer for one of them. An
    unlabelled single part takes its file's name, which nothing else in its
    document can share. A label the STEP round trip does not keep (a blank, a
    shape kind) is no label."""
    from cadgen._internal.step_scene_loader import _normalize_label_name
    from cadgen.store.build import compound_has_children

    authored = _normalize_label_name(getattr(shape, "label", None))
    if authored:
        return authored
    return "model" if compound_has_children(shape) else step_path.stem


def _stale_after_build(spec: EntrySpec) -> str | None:
    """The already-stale-on-completion notice: after publishing, the gate runs once
    more. A child edited during the build leaves the parent stale the moment it is done
    -- the parent built against the child it composed -- and the tree says so instead of
    letting the next run be the first to notice."""
    model = _model_for_spec(spec)
    if model is None or spec.source != "generated":
        return None
    try:
        from cadgen.store.gate import Verdict, stale

        verdict = stale(model)
    except Exception:  # noqa: BLE001 - a notice never fails a build
        return None
    # The sources: an output the run still owes (its STEP, exported once the run finishes
    # its exports) is no change.
    moved = [clause for clause in verdict.clauses if clause.get("stale") and clause.get("clause") != 5]
    if not moved:
        return None
    reason = Verdict(model=verdict.model, stale=True, clauses=moved).reason()
    return f"{reason}; changed during the build" if reason else "changed during the build"


def _reported_document(spec: EntrySpec) -> str | None:
    """The document a model run names on stdout, as an ABSOLUTE path: the STEP it
    declares, or -- for a mesh-only model (`@stl`/`@glb`/`@threemf` with no `@step`)
    -- the first mesh it declares. A STEP model that also declares meshes still
    names its STEP: the line names the model's primary document, not everything
    the build wrote."""
    if spec.step_output:
        return str(spec.step_path.expanduser().resolve()) if spec.step_path is not None else None
    for export in spec.mesh_exports:
        return str(export.path.expanduser().resolve())
    return None


def _model_for_spec(spec: EntrySpec) -> str | None:
    """The store identity of a spec: ``script::fn`` (generated) or its document's
    path (imported) -- cadgen.store.index.model_ref."""
    from cadgen.store.index import model_ref

    if spec.source == "generated" and spec.script_path is not None:
        function = getattr(spec.generator_metadata, "entry_function", None)
        return model_ref(spec.script_path, function)
    return str(spec.entry_path) if spec.entry_path is not None else None


def _gate_verdict(spec: EntrySpec):
    """The gate's verdict on a generated model (``store.gate.stale``), or None."""
    if spec.source != "generated" or spec.step_path is None:
        return None
    from cadgen.store.gate import stale

    return stale(_model_for_spec(spec))


def _checked_source_tree(spec: EntrySpec, verdict: object | None = None) -> str | None:
    """The exact source result ``verdict`` checked, while it stands (``Verdict.key``:
    the gate's clauses 1-4 and 6 hold, and the files owe at most an export); without
    a verdict the gate is asked now."""
    if spec.source != "generated" or spec.step_path is None:
        return None
    if verdict is None:
        verdict = _gate_verdict(spec)
    return verdict.tree if verdict.key is not None else None


@dataclass(frozen=True)
class _Built:
    """What :func:`build_model` answers: the model's result tree, how it came about
    (``current``: the stored result; ``built``: its body ran; ``skipped-peer``: a
    concurrent run published it first), and where a built model's time went."""

    tree: str | None
    outcome: str
    timings: dict[str, float] | None = None
    #: The key the result is stored under (store/results.py); a parent records the
    #: child by it.
    key: str | None = None


def build_model(
    spec: EntrySpec,
    *,
    generator: Callable[[], object] | None,
    force: bool,
    profile: bool,
    logger: CliLogger,
    progress_sink: object | None = None,
) -> _Built:
    """The one pipeline every decorated function goes through, root or child (STORE.md
    §9a): the gate, then either the stored result -- its parts' display data, its STEP
    (where the file is not the one its record names, another writer wrote it or the
    store lost its entry) and its declared meshes owed without running its body -- or a
    run of the body, under a job slot and the run's scopes, publishing its record. Either
    way the files it owes are queued (``_Export``) and finished when the run that owns the
    command ends.

    ``generator`` is the model's function when its module is loaded already (a child a
    parent's body called); None loads the script. ``force`` and ``profile`` are the
    root's flags: they never reach a child.
    """
    _say_owed(spec, logger)
    # One gate verdict per model per build: taken here, re-taken only once the run
    # waited for a slot, which can change the answer. The already-stale notice after
    # publishing asks anew.
    verdicts: dict[str, object] = {}

    def verdict() -> object:
        if "verdict" not in verdicts:
            verdicts["verdict"] = _gate_verdict(spec)
        return verdicts["verdict"]

    if not force:
        tree = _checked_source_tree(spec, verdict())
        if tree is None:
            tree = _restore_result(spec, verdict(), logger=logger)
        if tree is not None:
            _hit(spec, tree, logger=logger, verdict=verdict())
            _tree_event(spec, "current")
            return _Built(tree, "current", key=verdict().key)

    # The same question, asked again when the run opens, so a run that started behind
    # a concurrent build of this model no-ops instead of rebuilding it. --force always
    # does the work.
    def built_by_a_peer(peer_spec: EntrySpec) -> str | None:
        return None if force else _checked_source_tree(peer_spec, verdict())

    def build(tracked_spec: EntrySpec, reporter: object) -> object:
        return _generate_step_outputs(
            tracked_spec,
            generator=generator,
            logger=logger if logger.verbose else None,
            force=force,
            progress=reporter,
            verdict=None if force else verdict(),
        )

    from cadgen.daemon.executors import capture_source_result

    timed: dict[str, dict[str, float]] = {}
    with capture_source_result(_model_for_spec(spec)) as captured:
        result = _run_with_spec_generation_status(
            spec,
            "step",
            build,
            skip_if_current=built_by_a_peer,
            progress_sink=progress_sink,
            on_queued=verdicts.clear,
            profile=profile,
            on_timed=lambda measured: timed.__setitem__("timings", measured),
        )
        captured._finish(0)
        if isinstance(result, _SkippedGeneration):
            return _Built(result.tree, "skipped-peer", key=verdict().key)
        if spec.source == "generated":
            result.tree = captured.wait_result()
    return _Built(result.tree, "built", timings=timed.get("timings"), key=result.key)


def _say_owed(spec: EntrySpec, logger: CliLogger) -> None:
    """The files a run of the model owed and could not write, as its record keeps them
    (``store.exports.note_owed``): the first thing the model's next run says, one line each --
    ``<file> owed since <time>: <error>`` -- before it writes them."""
    if spec.source != "generated":
        return
    from cadgen.store.records import read_record

    for path, entry in ((read_record(_model_for_spec(spec)) or {}).get("outputs") or {}).items():
        owed = entry.get("owed") if isinstance(entry, dict) else None
        if isinstance(owed, dict):
            since = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(owed.get("since") or 0)))
            logger.info(f"{_display_path(Path(path))} owed since {since}: {owed.get('error')}")


def _restore_result(spec: EntrySpec, verdict: object, *, logger: CliLogger) -> str | None:
    """A stored result for the source as it is NOW (``Verdict.candidate``, STORE.md
    §4): a source reverted to a state already built. Its record and provenance are
    restored from the result (``store.results``), and the model takes it as a
    current one, exporting what the files owe. None when there is none to restore,
    or its tree is gone; the verdict then names the restored key."""
    candidate = getattr(verdict, "candidate", None)
    if spec.source not in ("generated", "imported") or spec.step_path is None or not candidate or verdict.stored is not None:
        # A record whose stored result stands is the candidate itself: what it owes
        # beyond an export (a drawing) is a run's, not a restore's.
        return None
    from cadgen.store.provenance import write_provenance
    from cadgen.store.records import note_output, write_record
    from cadgen.store.results import read_result
    from cadgen.store.trees import claim_tree, tree_complete

    result = read_result(candidate)
    tree = str((result or {}).get("tree") or "")
    if not result or not tree or not tree_complete(tree) or not claim_tree(tree):
        return None
    record = dict(result["record"])
    model = _model_for_spec(spec)
    verdict.key = write_record(model, record)
    if result.get("provenance"):
        write_provenance(model, result["provenance"], tree=tree)
    for output_path in record.get("outputs") or {}:
        note_output(output_path, model)
    verdict.tree = tree
    logger.info(f"{spec.source_ref}: an earlier build's result answers for the source as it is now; restored")
    return tree


def restore_document_record(step_path: Path, tree: str) -> None:
    """A document read whose bytes the store maps to ``tree`` (a warm read, nothing
    compiled) has its path's record name that tree. The compile of those bytes wrote the
    record and stored it under its key (``store.results``); a file changed and changed
    back reads warm from the first compile's tree while its record still names the
    other's, so ``cadgen store why <file>`` said STALE of a file whose result stands.
    Restored as a model's source brought back to a state already built is
    (:func:`_restore_result`), from the record the path holds: a path with no record
    (bytes compiled under another path) restores nothing."""
    from cadgen.authoring import current_run
    from cadgen.store.gate import stale
    from cadgen.store.records import read_record

    model = str(step_path)
    record = read_record(model)
    if record is None or str(record.get("tree") or "") == tree:
        return
    verdict = stale(model)
    if verdict.key is not None or verdict.stored is not None:
        return
    source = source_from_path(step_path)
    if source is None:
        return
    run = current_run()
    logger = run.logger if run is not None and run.logger is not None else CliLogger("cadgen")
    _restore_result(_entry_spec_from_source(source), verdict, logger=logger)


def _heal_documents(spec: EntrySpec, record: Mapping[str, object]) -> None:
    """The documents a current model's body read (``read_step``: in its closure, by the
    bytes the gate just verified) have their records name the tree those bytes map to
    (:func:`restore_document_record`): the model taken as current or restored read
    them, so a document changed and changed back reads as its model does."""
    if spec.source != "generated" or spec.script_path is None:
        return
    from cadgen.store.records import document_entry_for_hash

    shas = (record.get("closure") or {}).get("shas") or {}
    for rel, sha in shas.items():
        if Path(rel).suffix.lower() not in (".step", ".stp") or not sha:
            continue
        entry = document_entry_for_hash(str(sha))
        tree = str((entry or {}).get("tree") or "")
        if tree:
            restore_document_record(Path(spec.script_path).parent / rel, tree)


def _hit(spec: EntrySpec, tree: str, *, logger: CliLogger, verdict: object | None = None,
         healed: set[str] | None = None, display: bool = True) -> None:
    """A model whose stored result ``tree`` is current: its source result announced, what
    its children owe healed, then what it may still owe itself queued (``_Export``) --
    none of it by running its body. Its STEP (one another writer wrote; one whose entry
    the store lost; one missing, or never written because its export failed) and, with
    it or alone, its sidecar, written from the record (``store.exports.recorded_sidecar``): a
    sidecar is a function of the result, so one on disk that is not the record's --
    deleted, foreign, the other state's after a restore -- is written again even beside a
    kept STEP. Then its parts' display data (a cleared store) and its declared mesh
    exports (a deleted file, a changed declaration). The display data asked for is that of
    the result the file's bytes map to once its STEP is saved.

    The children: a current model's body does not run, so it calls no child, yet its gate
    asked each one (``verdict``, clause 3). Every child whose result stands is healed the
    same way, by this path, its own children with it and before the parent, as a build
    queues them, so one run of the root leaves no file of the tree owed. A child whose
    record its source has moved on from, while a stored result answers for the source as
    it is now, is restored first (:func:`_restore_result`): the parent's hit leaves the
    child's record and files in the state the parent stands on. A child that owes a run
    does not stand, and the parent's run would have called it.

    ``display``: whether the model's parts' display data is asked for. A root's request
    covers its whole tree, links expanded, so the children it heals ask for none."""
    from cadgen.store.gate import stale
    from cadgen.store.index import split_model_ref
    from cadgen.store.records import read_record

    model = _model_for_spec(spec)
    healed = healed if healed is not None else set()
    healed.add(model)
    _current_source_result(spec, tree)
    record = read_record(model) or {}
    _heal_documents(spec, record)
    if _record_mesh_recipes(spec, record):
        from cadgen.store.records import note_output, write_record

        write_record(model, record)
        for declared in spec.mesh_exports:
            note_output(declared.path, model)
    asked = getattr(verdict, "descendants", None) or {}
    for child in record.get("children") or []:
        child_model = str((child or {}).get("model") or "")
        if not child_model or child_model in healed:
            continue
        script, function = split_model_ref(child_model)
        source = source_from_path(script, function=function)
        if source is None:
            continue
        child_spec = _entry_spec_from_source(source)
        child_verdict = asked.get(child_model) or stale(child_model)
        if child_verdict.key is None and _restore_result(child_spec, child_verdict, logger=logger) is None:
            continue
        _hit(child_spec, child_verdict.tree, logger=logger, verdict=child_verdict, healed=healed, display=False)
        _tree_event(child_spec, "current")
    step = None
    if spec.source == "generated" and spec.step_output and spec.step_path is not None:
        from cadgen._internal.source_sidecar import source_sidecar_path

        pair = document_pair_state(spec.step_path)
        plan = _export_plan(record, str(spec.step_path.expanduser().resolve()), tree, force=False, on_disk=pair[0])
        recorded = (record.get("outputs") or {}).get(str(source_sidecar_path(spec.entry_path).resolve())) or {}
        sidecar_owed = pair[1] != (str(recorded.get("sha256") or "") or None)
        if plan.kept is None:
            logger.info(f"{_display_path(spec.step_path)} {plan.why}; writing it again")
        else:
            plan.map_kept(tree)
            if sidecar_owed:
                logger.info(f"{_display_path(source_sidecar_path(spec.step_path))} is not the record's; writing it again")
        if plan.kept is None or sidecar_owed:
            step = {"plan": plan, "expected": pair, "live": {}}
    displays = [_ensure_reused_display(spec, tree, written=step is not None and step["plan"].kept is None)] \
        if display else []
    _Export(spec, tree, key=record.get("key"), logger=logger, step=step, displays=displays,
            captured_record=record)


def build_child(defn: object) -> tuple[str, str]:
    """A child model a parent's body called (``cadgen.authoring``): ``(key, tree)`` of
    its stored result, or of the result of running it now, in this process, as a build
    of its own -- once per run, however often it is called."""
    from cadgen.authoring import current_run

    run = current_run()
    if run is None:
        raise RuntimeError(f"{defn.ref} was called inside a body with no build run to belong to")
    result = run.results.get(defn.ref)
    if result is None:
        spec = _entry_spec_from_source(source_from_path(defn.script_path, function=defn.name))
        logger = run.logger if run.logger is not None else CliLogger("cadgen")
        built = build_model(spec, generator=defn.func, force=False, profile=False, logger=logger)
        if not built.tree or not built.key:
            raise RuntimeError(f"{defn.ref}: build supplied no source result")
        result = run.results[defn.ref] = (built.key, built.tree)
    return result


def generate_step_targets(
    targets: Sequence[str],
    *,
    force: bool = False,
    verbose: bool = False,
    json_output: bool = False,
    profile: bool = False,
) -> int:
    """Build trees for ``targets``. Returns the process exit code.

    ``json_output`` additionally prints one JSON line per target to STDOUT. The exit code
    alone cannot say WHICH targets were rebuilt and which were already current, and the
    logger's prose goes to stderr by design -- so without this a caller reading the streams
    apart had no machine-readable result at all.

    The command answers once every model's result is recorded (STORE.md §3, *Exports*): its
    result lines, then one line per file still being written (``saving <file>``, naming
    ``cadgen export <file>``, which waits for it); a JSON line names each file its target's
    run writes, and whether it is ``saving`` or ``saved``. The display data and the files
    are build-pool jobs: in a daemon worker they are its supervisor's, and the worker takes
    its next job the moment this answers (``authoring.supervised``); a process with no
    worker awaits them before it answers.
    """
    tool_name = "cadgen"
    logger = CliLogger("cadgen", verbose=verbose)
    # Each target's result line, with the exports its build queued (its children's first).
    reported: list[tuple[dict[str, object], list]] = []

    def _emit(spec: EntrySpec, built: _Built, exports: list) -> None:
        from cadgen.daemon.telemetry import job_reused
        from cadgen.store.trees import tree_kind_for

        job_reused(built.outcome in ("current", "skipped-peer"))  # in a build worker: telemetry's cache hit, or not
        reported.append(({
            "ok": True,
            # Read off the tree (store.trees.tree_kind): part or assembly is
            # what the returned shape was, never something a model declares.
            "kind": tree_kind_for(built.tree) or "part",
            "outcome": built.outcome,
            # The document the run wrote, and the hash of the result tree it came
            # from. A mesh-only model declares no STEP, so it answers with the mesh
            # it wrote -- a path the caller can open, never the tree hash, which
            # names nothing on disk. ABSOLUTE in the JSON result, as every door's
            # is (a machine reader may not share this cwd); the human line below
            # shows it relative to the cwd.
            "document": _reported_document(spec),
            "tree": built.tree,
            # Where a built model's time went (cadgen._internal.build_timing); a
            # model that was current has none.
            "timings": built.timings if built.outcome == "built" else None,
        }, exports))

    selected_specs = _selected_specs_for_targets(targets)
    for spec in selected_specs:
        _validate_step_target(spec, tool_name=tool_name)
    from cadgen.authoring import build_run

    # The targets are the roots of the run: a child is built when its parent's body
    # calls it (cadgen.authoring), by the same pipeline, in this process. What the run
    # owes -- each model's display data, STEP and meshes -- is build-pool jobs, started as
    # each result is recorded, children first.
    with build_run(logger) as run:
        for spec in selected_specs:
            logger.debug(f"Generating... {spec.source_ref}")
            queued = len(run.exports)
            with _cli_progress_line(spec, logger=logger, fallback="Generating...") as progress_sink:
                with logger.timed(f"generated {spec.source_ref}"):
                    built = build_model(spec, generator=None, force=force, profile=profile, logger=logger,
                                        progress_sink=progress_sink)
            document = _reported_document(spec)
            if built.outcome == "current":
                logger.info(f"{_display_path(Path(document)) if document else spec.cad_ref} is current; not rebuilt")
            elif built.outcome == "skipped-peer":
                logger.info(f"{spec.cad_ref} was built by a concurrent run; skipped")
            _emit(spec, built, run.exports[queued:])
    _answer(logger, reported, run, json_output=json_output)
    return 0


def _answer(logger: CliLogger, reported: list[tuple[dict[str, object], list]], run: object, *,
            json_output: bool) -> None:
    """The command's answer, once every model's result is recorded: its total, then -- on
    STDOUT, the result channel of every CLI, where a caller reading the two streams apart
    finds it -- one line per target, ``outcome document`` (every model names a document, a
    mesh-only one its mesh, so the tree hash is the last resort it never reaches), and one
    line per file the run still writes. ``--json`` makes each target's line JSON, naming
    each file its run writes and its state instead."""
    logger.total()
    for entry, exports in reported:
        if json_output:
            files = {path: state for export in exports for path, state in export.files().items()}
            print(json.dumps({**entry, "files": files}, separators=(",", ":")))
        else:
            document = entry["document"]
            print(f"{entry['outcome']} {_display_path(Path(document)) if document else entry['tree']}")
    if not json_output:
        import shlex

        for path in run.owed() if run is not None else ():
            shown = _display_path(path)
            # The command as a shell takes it: a path with a space is quoted there.
            print(f"saving {shown} (cadgen export {shlex.quote(shown)} waits for it)")


def generate_dxf_targets(
    targets: Sequence[str],
    *,
    force: bool = False,
    verbose: bool = False,
    json_output: bool = False,
    profile: bool = False,
) -> int:
    """Build drawings. A drawing is a model (STORE.md §3), so its run answers on
    stdout exactly as a STEP model's does (:func:`generate_step_targets`): one
    `outcome document` line per target, upgraded to JSON by ``json_output``
    (``tree`` is null — a drawing has no geometry tree), and a line per file still
    being written. The drawing writes its own file in its run, before the answer;
    what the models its body called owe follows it."""
    from cadgen.store.gate import stale

    reported: list[tuple[dict[str, object], list]] = []
    timed: dict[str, dict[str, float]] = {}

    def _emit(spec: EntrySpec, outcome: str, exports: list) -> None:
        from cadgen.daemon.telemetry import job_reused

        job_reused(outcome in ("current", "skipped-peer"))  # in a build worker: telemetry's cache hit, or not
        reported.append(({
            "ok": True,
            "kind": "drawing",
            "outcome": outcome,
            # Absolute in the JSON result, like every door's; the human line
            # shows it relative to the cwd.
            "document": str(spec.dxf_path.expanduser().resolve()) if spec.dxf_path is not None else None,
            "tree": None,
            "timings": timed.get(spec.source_ref) if outcome == "built" else None,
        }, exports))

    def dxf_output_current(spec: EntrySpec, output_path: Path | None) -> bool:
        # The ONE gate every model answers to (STORE.md §4): the drawing's record,
        # its closure, its children by key and its .dxf output.
        #
        # Ask it by the model's IDENTITY (``script::fn``), never by the bare script
        # path: a file may hold several models, and a bare path is ambiguous there --
        # cadgen.store.index.resolve_model_ref refuses it rather than guessing, which
        # would fail the drawing before it ever reached its own gate.
        if output_path is None:
            return False
        model = _model_for_spec(spec)
        if model is None:
            return False
        verdict = stale(model)
        return not verdict.stale

    logger = CliLogger("cadgen", verbose=verbose)
    selected_specs = _selected_specs_for_targets(targets)
    for spec in selected_specs:
        _validate_dxf_target(spec)

    # The .dxf IS the product: every drawing writes the `.dxf` its decorator
    # declares (`out=`, else the sibling `<name>.dxf`). The viewer parses that
    # file directly; there is no drawing package.
    def _effective_output(spec: EntrySpec) -> Path | None:
        return spec.dxf_path

    # No-op fast path: skip regenerating a drawing whose source closure is
    # unchanged and whose recorded output still verifies byte-for-byte.
    if not force:
        current_specs = [
            spec
            for spec in selected_specs
            if spec.script_path is not None
            and dxf_output_current(spec, _effective_output(spec))
        ]
        for spec in current_specs:
            logger.info(
                f"{_display_path(spec.dxf_path) if spec.dxf_path is not None else spec.cad_ref} "
                "is current; not rebuilt"
            )
            _emit(spec, "current", [])
        current_refs = {spec.source_ref for spec in current_specs}
        selected_specs = [spec for spec in selected_specs if spec.source_ref not in current_refs]
    if selected_specs:
        # Re-checked when the run opens, like the STEP path: a run that started behind
        # a concurrent build of this drawing must not regenerate it.
        def _built_by_a_peer(spec: EntrySpec) -> bool:
            if force or spec.script_path is None:
                return False
            return dxf_output_current(spec, _effective_output(spec))

        from cadgen.authoring import build_run

        # The drawing's body calls models, each built by the same pipeline, in this
        # process, as a child of the drawing's run.
        with build_run(logger) as run:
            for spec in selected_specs:
                logger.debug(f"Generating... {spec.source_ref}")
                queued = len(run.exports)
                with _cli_progress_line(spec, logger=logger, fallback="Generating...") as progress_sink:
                    with logger.timed(f"generated {spec.source_ref}"):
                        result = _run_with_spec_generation_status(
                            spec,
                            "dxf",
                            lambda tracked_spec, reporter: run_script_generator(
                                tracked_spec,
                                "dxf",
                                logger=logger,
                                progress=reporter,
                            ),
                            skip_if_current=_built_by_a_peer,
                            progress_sink=progress_sink,
                            profile=profile,
                            on_timed=lambda measured, ref=spec.source_ref: timed.__setitem__(ref, measured),
                        )
                if isinstance(result, _SkippedGeneration):
                    logger.info(f"{spec.cad_ref} was built by a concurrent run; skipped")
                    _emit(spec, "skipped-peer", run.exports[queued:])
                else:
                    logger.info(_generated_dxf_summary(spec))
                    _emit(spec, "built", run.exports[queued:])
        _answer(logger, reported, run, json_output=json_output)
    else:
        _answer(logger, reported, None, json_output=json_output)
    return 0
