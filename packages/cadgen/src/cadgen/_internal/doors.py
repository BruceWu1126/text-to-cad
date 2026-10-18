"""What every CLI door does before it touches a document.

DOCUMENTS-ONLY CLI INPUTS (README law 7). A model script is a PROGRAM:
``python model.py`` is the one source door, and running it writes the document,
its sidecar and its declared exports. Every command therefore takes the
DOCUMENT — a ``.step``/``.stp``/``.stl``/``.dxf`` file — and refuses any other
suffix by naming what it takes.

A door asks ONE question of a document (:func:`document_tree`, STORE.md §9):
does the store have a tree for this file's bytes? Yes → use it. No → a compile
job in the pool builds one from the bytes, exactly as for an imported STEP,
whether or not the file has a script. A door never refuses a document and never
runs a model body: whether the source has moved on since the document was
written is the model's record's business (``cadgen store why`` and the build
tree answer it), never a door's and never the viewer's.

Before it reads a file, a door asks whether a build is still writing it
(:func:`await_writes`): a build answers once its results are recorded and writes
its files after, so a door given one of them waits for that write and then reads
the bytes. It asks the daemon's job ledger -- process state -- and never a model
record.

Stdlib-light: these run before any CAD import, on the ``--help`` path and on a
model script's pre-gate path.
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "STEP_SUFFIXES",
    "CompileFailed",
    "await_writes",
    "display_path",
    "document_target",
    "document_tree",
    "document_snapshot",
]


def await_writes(path: Path | str) -> None:
    """Wait while a build is still writing the file at ``path`` (STORE.md §3, *Exports*).

    The daemon's ledger lists the files each request's results owe (``saving``) and the
    moment each lands (``saved``): while the newest result that owes this file has not saved
    it and its request runs, the file on disk is about to change, and a door reading it now
    would read the bytes it replaces. So the door waits for that write -- bounded by the
    write, which ends in the file or in the request's end -- and reads the file after. With no
    request writing it, or no daemon, nothing waits. Inside a build, a file the build's own
    request owes is written after its bodies return, so nothing waits for it there; waiting
    on another request's write gives up the job slot meanwhile."""
    import os
    import time

    from cadgen.daemon.client import watch_jobs
    from cadgen.daemon.jobs import being_written
    from cadgen.store.paths import store_root

    target, root = os.path.realpath(path), os.path.realpath(store_root())
    own = os.environ.get("CADGEN_JOB_ID") or None
    update = watch_jobs(None, output=target, store_root=root)
    if update is None or not being_written(update["jobs"], target, own=own):
        return
    from cadgen.daemon import broker

    with broker.yielded():
        while update is not None and being_written(update["jobs"], target, own=own):
            if update.get("jobsWatchLimited"):
                time.sleep(0.5)  # every waiter the daemon keeps is taken: ask again shortly
            update = watch_jobs(update["jobsCursor"], output=target, store_root=root)


#: What a STEP document is called on disk. THE definition: every module that
#: asks "is this a STEP document?" imports this one.
STEP_SUFFIXES = (".step", ".stp")


class CompileFailed(RuntimeError):
    """The compile job for a document's bytes did not produce a tree.

    Its message is the compile's own error (the job's output), which is what a
    door reports verbatim -- a door never adds a "regenerate" instruction.
    """


def document_snapshot(document: Path) -> tuple[str, str]:
    """The tree for THIS document's bytes — a door's one question (STORE.md §9).

    Yes, the store has one — ``index/document/<sha256(bytes)>-v<schema>`` → tree (STORE.md
    §2, the law: artifact → artifact; no record is opened) → it is used. Source
    changes are the model's record's business, never the door's: a document is
    never refused and no body ever runs here. No → a **compile job** in the pool
    builds a tree from the file's bytes, exactly as for an imported STEP,
    whether or not the file has a script, and the lookup is repeated.
    """
    from cadgen.catalog import result_snapshot_for

    document = Path(document).expanduser().resolve()
    snapshot = result_snapshot_for(document)
    if snapshot:
        return snapshot
    from cadgen.daemon.executors import submit_compile

    job = submit_compile(document)
    if job.wait() != 0:
        said = job.output().rstrip()
        raise CompileFailed(f"compiling {display_path(document)} failed" + (f":\n{said}" if said else ""))
    snapshot = result_snapshot_for(document)
    if not snapshot:
        raise CompileFailed(f"no tree for {display_path(document)} after its compile")
    return snapshot


def document_tree(document: Path) -> str:
    """The canonical tree from the same coherent lookup used by mesh exports."""
    return document_snapshot(document)[1]


def display_path(path: Path | str) -> str:
    """A path as a MESSAGE names it: relative to the cwd where that is
    meaningful, else as given. THE helper for door and result messages
    (``cadgen.results``, the STEP re-emit); never for a persisted path."""
    try:
        return str(Path(path).resolve().relative_to(Path.cwd().resolve()))
    except (OSError, ValueError):
        return str(path)


def document_target(target: Path | str, *, suffixes: tuple[str, ...]) -> Path:
    """Resolve TARGET as a document, or say what this door takes.

    ``suffixes`` is the door's own accepted set (``.step``/``.stp`` for STEP,
    ``.stl`` for the STL door, ...); any other suffix is refused with the ones
    it takes.
    """
    path = Path(target).expanduser()
    suffix = path.suffix.lower()
    if suffix not in suffixes:
        accepted = "/".join(suffixes)
        raise ValueError(f"this command takes a {accepted} document: {target}")
    resolved = path.resolve()
    await_writes(resolved)
    if not resolved.is_file():
        raise FileNotFoundError(f"document does not exist: {target}")
    return resolved
