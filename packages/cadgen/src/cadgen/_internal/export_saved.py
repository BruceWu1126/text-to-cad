"""Explicit file completion from a captured record, using the build's exporters.

This is a code-side operation: path -> output index -> qualified model record.
It reads source hashes only to explain which stored result it is exporting.
"""

from __future__ import annotations

import copy
from pathlib import Path

from cadgen._internal.doors import await_writes, display_path
from cadgen.cli_logging import CliLogger


def _recorded(path: Path) -> tuple[str | None, dict | None]:
    from cadgen.store.index import split_model_ref
    from cadgen.store.records import read_record, recorded_model_for_output

    model = recorded_model_for_output(path)
    if model is None:
        return None, None
    script, function = split_model_ref(model)
    # A bare Python path would need today's declarations to identify its model.
    if script.suffix.lower() == ".py" and function is None:
        return model, None
    record = read_record(model)
    if record is None or not isinstance((record.get("outputs") or {}).get(str(path)), dict):
        return model, None
    return model, copy.deepcopy(record)


def _step_path(record: dict) -> Path | None:
    candidates = [Path(path) for path, entry in (record.get("outputs") or {}).items()
                  if isinstance(entry, dict) and entry.get("writer")
                  and Path(path).suffix.lower() in (".step", ".stp")]
    if len(candidates) > 1:
        raise RuntimeError("the stored result names more than one STEP export")
    return candidates[0] if candidates else None


def _ready(path: Path, record: dict) -> bool:
    from cadgen._internal.source_sidecar import appearance_digest, source_sidecar_path
    from cadgen.step_export import STEP_WRITER_SCHEME
    from cadgen.store.exports import document_pair_state, sha256_or_none

    entry = record["outputs"][str(path)]
    if not entry.get("sha256") or entry["sha256"] != sha256_or_none(path):
        return False
    if entry.get("writer"):
        pair = document_pair_state(path)
        sidecar = record["outputs"].get(str(source_sidecar_path(path).resolve())) or {}
        # The saved file stands alone. Losing its derived document map or tree
        # does not make its matching receipt owe another export.
        return (entry["writer"] == STEP_WRITER_SCHEME
                and pair[1] == (sidecar.get("sha256") or None))
    if entry.get("declared"):
        from cadgen._internal.mesh_export import SERIALIZATION_VERSIONS

        step = _step_path(record)
        source = record["outputs"][str(step)].get("sha256") if step else record.get("tree")
        return (bool(source) and entry.get("document") == source
                and entry.get("serializer") == SERIALIZATION_VERSIONS.get(entry["declared"])
                and entry.get("appearance") == appearance_digest(record.get("appearance"))
                # Mesh decorators export the static result, with no clip selector.
                and entry.get("anim") is None)
    step = _step_path(record)
    if step is not None and source_sidecar_path(step).resolve() == path:
        return _ready(step, record)
    # A drawing is already its product; it has no stored geometry to re-emit.
    return True


def _source_notice(record: dict, logger: CliLogger) -> None:
    if record.get("sourceKind") != "python":
        return
    from cadgen.store.closure import changed_closure_files

    script = Path(str(record.get("script") or ""))
    closure = record.get("closure") or {}
    try:
        changed = changed_closure_files(script, closure.get("shas") or {}, closure.get("names") or {},
                                        closure.get("wholes") or {}, closure.get("own") or {})
    except (OSError, ValueError, SyntaxError):
        changed = [script.name]
    if not script.is_file():
        logger.info(f"{display_path(script)} is missing; exporting its stored result")
    elif changed:
        logger.info(f"Source or inputs for {display_path(script)} changed since the stored result; exporting that result")


def _spec(model: str, record: dict, target: Path):
    from cadgen._internal.generation_spec import EntrySpec, ResolvedMeshExport
    from cadgen._internal.source_sidecar import source_sidecar_path
    from cadgen.metadata import GeneratorMetadata
    from cadgen.store.index import split_model_ref

    entry = record["outputs"][str(target)]
    step = _step_path(record)
    meshes = ()
    if entry.get("declared"):
        required = ("declared", "chord", "angle", "name")
        if any(not isinstance(entry.get(field), str) or not entry[field] for field in required):
            raise RuntimeError(f"{display_path(target)} has no complete stored mesh recipe; run its model again")
        meshes = (ResolvedMeshExport(entry["declared"], target, float(entry["chord"]), float(entry["angle"])),)
    elif not entry.get("writer") and not (step and source_sidecar_path(step).resolve() == target):
        raise RuntimeError(f"{display_path(target)} cannot be reproduced from stored geometry; run its model again")
    logical = step or target.parent / f"{entry['name']}.step"
    script, function = split_model_ref(model)
    metadata = GeneratorMetadata(script, function or logical.stem, (), "step", entry_function=function,
                                 is_decorated=True, step_output=step is not None)
    return EntrySpec(source_ref=model, cad_ref=display_path(target), source_path=script,
                     display_name=function or logical.stem, source="generated", step_path=logical,
                     script_path=script, generator_metadata=metadata, mesh_exports=meshes,
                     step_output=step is not None)


def _queue(model: str, record: dict, target: Path, logger: CliLogger):
    from cadgen._internal.generation import _Export, _export_plan
    from cadgen._internal.source_sidecar import source_sidecar_path
    from cadgen.store.exports import document_pair_state
    from cadgen.store.trees import tree_complete

    tree = str(record.get("tree") or "")
    if not tree or not tree_complete(tree):
        raise RuntimeError(f"{display_path(target)} has no complete stored result; run its model again")
    spec = _spec(model, record, target)
    step = None
    if spec.step_output:
        pair = document_pair_state(spec.step_path)
        plan = _export_plan(record, str(spec.step_path), tree, force=False, on_disk=pair[0])
        sidecar_path = source_sidecar_path(spec.step_path).resolve()
        sidecar_entry = record["outputs"].get(str(sidecar_path))
        expected_sidecar = (sidecar_entry or {}).get("sha256")
        if (plan.kept is None or pair[1] != (expected_sidecar or None)
                or sidecar_entry is not None and not expected_sidecar):
            step = {"plan": plan, "expected": pair, "live": {}}
        elif plan.kept is not None:
            plan.map_kept(tree)
    return _Export(spec, tree, key=record.get("key"), logger=logger, step=step, captured_record=record)


def export_saved(path: Path | str) -> Path:
    """Wait for or reproduce ``path`` from the latest recorded result, synchronously: a file
    its result owes is written by a build-pool job (``generation._Export``), which this command
    waits for as any door waits for a write (``doors.await_writes``) -- in a process with no
    daemon worker, the run awaits it here (``BuildRun.finish``)."""
    from cadgen.authoring import build_run
    from cadgen.store.records import export_state

    target = Path(path).expanduser().resolve()
    if target.suffix.lower() == ".py":
        raise ValueError("export takes an artifact path; run a model script with Python")
    logger = CliLogger("cadgen export")
    noticed = None
    while True:
        await_writes(target)
        model, record = _recorded(target)
        if record is None:
            if target.is_file():
                return target
            raise RuntimeError(f"no recoverable stored result for {display_path(target)}; run its model again")
        from cadgen._internal.source_sidecar import source_sidecar_path

        step = _step_path(record)
        # A STEP and its sidecar publish together, under the STEP's write.
        written = step if step is not None and source_sidecar_path(step).resolve() == target else target
        if written != target:
            await_writes(written)
            model, record = _recorded(target)
            if record is None:
                continue
        captured = export_state(record)
        if captured != noticed:
            _source_notice(record, logger)
            noticed = captured
        if _ready(target, record):
            _latest_model, latest = _recorded(target)
            if latest is not None and export_state(latest) == captured:
                return target
            continue
        with build_run(logger):
            _queue(model, record, target, logger)
        # This command promises a file: the job's write is waited for before the answer.
        await_writes(written)
        _latest_model, latest = _recorded(target)
        if latest is not None and export_state(latest) != captured:
            # The job this command queued was superseded (STORE.md §3, *Exports*): the file
            # is the newer result's, which this command finishes instead.
            logger.info(f"{display_path(target)}: a newer result of its model was recorded while it was "
                        "being exported; exporting that result")
            continue
        if latest is not None and _ready(target, latest):
            return target
        owed = (((latest or {}).get("outputs") or {}).get(str(written)) or {}).get("owed") or {}
        raise RuntimeError(f"{display_path(target)} was not saved from its stored result"
                           + (f": {owed['error']}" if owed.get("error") else ""))
