"""The daemon's job ledger: every job it runs, whoever asked for it.

One row per request the supervisor relays — a ``python model.py`` from a
terminal, a door's or the viewer's compile — and one per child model a request
builds inside it (its parent's body called it, STORE.md §9a), under the request
(``request``), listed from the child's first event and closed by its own done
or failed event, or with the request. Every row carries the job's DECLARED
OUTPUT PATHS as metadata (the ``out=`` document and the declared meshes for a
model script, parsed statically; the document itself for a compile). Readers
match jobs to files by those paths and never by source state:
the CAD Viewer shows ``compiling · <phase> n/total`` for any job whose outputs
include the document it displays, ``failed`` with the failure's own reason for a
failed one, and nothing else
(``cadgen.viewer.build_progress``). Finished jobs stay listed for
:data:`RETAIN_SECONDS` so a failure is still visible after the job is gone.

State comes from the ``{"event": …}`` frames the worker streams (the build
tree's own transitions: submitted → queued → building [phase, done/total] →
done | failed) and from the request's exit code; a job whose requester left ends
cancelled. Stdlib only; nothing here
imports the kernel — a job's outputs come from ``cadgen.metadata``'s AST parse.

A row also says which files its build still owes: a ``saving`` result names each file a
model's result owes the moment it is recorded, and the file's ``saved`` result follows when
it lands. A door given a path asks this (:func:`being_written`) and waits while a request
is still writing the file (STORE.md §3, *Exports*). The request's end settles what it owed:
its rows owe nothing more, and a row that owed a file the failed request never saved fails
with the request's reason.
"""

from __future__ import annotations

import itertools
import copy
import hashlib
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

__all__ = ["JobLedger", "RETAIN_SECONDS", "being_written", "declared_outputs", "failure_message"]

RETAIN_SECONDS = 120.0
_RUNNING = ("submitted", "queued", "building")
# What a job keeps of a build's results, by output (``viewer.preview`` reads them): of the
# preview revision its tree, the producer its parts are keyed under, the part key of each
# component the store holds and whether that is all of them; of the preview's annotations
# the one object's hash; of a file the build owes the tree it is written from; of the saved
# document its tree and digest, which tell whether the file moved on. A status names objects
# and carries no geometry or annotation content. Each is kept with the ledger's revision when
# it landed, the order of results across rows.
_KEPT_RESULT_FIELDS = {
    "preview": ("previews", ("tree", "producer", "parts", "complete")),
    "annotations": ("annotations", ("tree", "object")),
    "saving": ("saving", ("tree",)),
    "saved": ("savedResults", ("tree", "documentHash")),
}


def _owes(job: dict[str, Any], output: str) -> bool:
    """Whether ``job``'s build still owes ``output``: it named the file as owed (``saving``)
    and has saved nothing there since."""
    owed = (job.get("saving") or {}).get(output)
    if not isinstance(owed, dict) or owed.get("superseded"):
        return False
    saved = (job.get("savedResults") or {}).get(output)
    return not isinstance(saved, dict) or int(saved.get("sequence") or 0) < int(owed.get("sequence") or 0)


def being_written(jobs: list[dict[str, Any]], output: str, *, own: str | None = None) -> bool:
    """Whether a request in ``jobs`` (the ledger's rows for ``output``) is still writing it.

    The newest result that owes the file is the one it will hold: an older build's export is
    skipped or refused once a newer result of the model is recorded (STORE.md §3, *Exports*),
    so only the newest owed result is waited for, until it saves the file or its request
    ends. ``own`` is the asking request's id (``CADGEN_JOB_ID``): a file its own run owes is
    written after its bodies return, so nothing in it waits for that."""
    newest: tuple[dict[str, Any], dict[str, Any]] | None = None
    for job in jobs:
        owed = (job.get("saving") or {}).get(output)
        if isinstance(owed, dict) and (newest is None or int(owed.get("revision") or 0)
                                       > int(newest[0].get("revision") or 0)):
            newest = (owed, job)
    if newest is None:
        return False
    job = newest[1]
    if own and own in (job.get("id"), job.get("request")):
        return False
    return _owes(job, output)

# The two shapes a failed job's stderr ends in: the CLI's own failure line
# (``[cadgen step compile] FAILED: RuntimeError: ...``) or, under --verbose, a raw
# traceback whose last line is ``RuntimeError: ...``.
_CLI_FAILED = re.compile(r"^\[[^\]]+\]\s+FAILED:\s+(?:[\w.]+\.)?(?P<type>\w+)\s*:\s*(?P<message>.+)$")
_EXCEPTION_LINE = re.compile(r"^(?:[\w.]+\.)?(?P<type>\w+(?:Error|Exception))\s*:\s*(?P<message>.+)$")
_CLI_NOISE = re.compile(r"^\[[^\]]+\]\s+(?:re-run with --verbose|\s)")


def failure_message(output: str) -> tuple[str, str | None]:
    """The one line a person reads about a failed job, and the exception class.

    Reads a job's stderr from the end: the CLI's ``[tool] FAILED: Type: message``
    line first, then a traceback's final ``Type: message``, then the last line
    that is not the CLI's own hint or frame listing. Returns ``("", None)`` for
    silence. Pure text; nothing here knows what ran.
    """
    lines = [line.rstrip() for line in str(output or "").splitlines() if line.strip()]
    for line in reversed(lines):
        match = _CLI_FAILED.match(line.strip())
        if match:
            return match.group("message").strip(), match.group("type")
    for line in reversed(lines):
        match = _EXCEPTION_LINE.match(line.strip())
        if match:
            return match.group("message").strip(), match.group("type")
    for line in reversed(lines):
        if not _CLI_NOISE.match(line):
            return line.strip(), None
    return "", None


def _real(path: str | os.PathLike[str]) -> str:
    try:
        return os.path.realpath(str(path))
    except (OSError, ValueError):
        return str(path)


def _model_subject(ref: str) -> str:
    """A model ref as a subject: its script's real path, ``::fn`` kept."""
    script, separator, function = ref.partition("::")
    return _real(script) + separator + function if script else ""


def _same_model(ref: str, subject: str) -> bool:
    """Whether a model ref (as :func:`_model_subject` spells it) names a request's subject:
    the same script, and the same function where both name one -- an event spells a
    file's sole model bare (``index.display_model``), a request a whole file bare."""
    script, _, function = ref.partition("::")
    subject_script, _, subject_function = subject.partition("::")
    return script == subject_script and (not function or not subject_function or function == subject_function)


def declared_outputs(subject: str, tool: str) -> list[str]:
    """The output paths a job will write, from its declarations alone.

    A compile (``step-compile``) writes the tree for the document it names — the
    document IS its output. A model script's outputs are its ``out=`` document
    (else the sibling ``<stem>.step`` / ``.dxf``) and every declared mesh export,
    resolved exactly as the build resolves them. Never raises: a script that
    cannot be parsed simply declares nothing.
    """
    if not subject:
        return []
    script_ref, _, function = subject.partition("::")
    if not script_ref.endswith(".py"):
        return [_real(subject)]
    try:
        from cadgen.metadata import declared_output_paths

        paths = declared_output_paths(Path(script_ref), function=function or None)
        return list(dict.fromkeys(_real(path) for path in paths))
    except Exception:  # noqa: BLE001 - metadata is best-effort; a job still runs
        return []


class JobLedger:
    """Thread-safe: the relay threads write, status requests read."""

    def __init__(self, *, retain_seconds: float = RETAIN_SECONDS, clock=time.time) -> None:
        self._guard = threading.Lock()
        self._changed = threading.Condition(self._guard)
        self._revision = 0
        self._jobs: dict[str, dict[str, Any]] = {}
        self._ids = itertools.count(1)
        self._retain = float(retain_seconds)
        self._clock = clock
        self.epoch = uuid.uuid4().hex
        # What each subject declared the last time its declarations could be read.
        self._last_declared: dict[str, list[str]] = {}
        self._last_declared_guard = threading.Lock()

    def _declared_outputs(self, subject: str, tool: str) -> list[str]:
        """:func:`declared_outputs`, or, when the script no longer imports (an edit broke a
        helper), what it declared the last time it did: the job is still listed against the
        documents its script writes, so its failure reaches whoever shows them. Never a
        guess at the source: only what the script itself declared, in this process."""
        outputs = declared_outputs(subject, tool)
        if not subject:
            return outputs
        with self._last_declared_guard:
            if outputs:
                self._last_declared[subject] = list(outputs)
                return outputs
            return list(self._last_declared.get(subject, ()))

    # --- lifecycle -------------------------------------------------------------

    def start(self, *, tool: str, subject: str, argv: list[str] | None = None, store_root: str = "") -> dict[str, Any]:
        if tool == "artifact":
            subject = ""
        subject = _model_subject(subject)
        now = self._clock()
        sequence = next(self._ids)
        job: dict[str, Any] = {
            "id": f"{self.epoch}:job-{sequence}",
            "epoch": self.epoch,
            "sequence": sequence,
            "storeRoot": _real(store_root) if store_root else "",
            "tool": str(tool),
            "subject": subject,
            "outputs": [] if tool == "artifact" else self._declared_outputs(subject, str(tool)),
            "argv": [str(a) for a in (argv or [])],
            "state": "submitted",
            "phase": None,
            "detail": "",
            "done": None,
            "total": None,
            "startedAt": now,
            "updatedAt": now,
            "finishedAt": None,
            "exit": None,
            "error": None,
        }
        with self._guard:
            self._jobs[job["id"]] = job
            self._notify(job)
        return job

    def start_artifact(self, request: dict, *, store_root: str, dependency=False) -> dict[str, Any]:
        job = self.start(tool="artifact", subject="", store_root=store_root)
        with self._guard:
            job["artifact"] = copy.deepcopy(request)
            job["dependency"] = bool(dependency)
            self._notify(job)
        return job

    def record_artifact_result(self, job: dict[str, Any], result: dict) -> None:
        with self._guard:
            if job["tool"] == "artifact" and job.get("artifactResult") is None:
                job["artifactResult"] = copy.deepcopy(result)
                job["updatedAt"] = self._clock()
                self._notify(job)

    def waiting(self, job: dict[str, Any], detail: str | None) -> None:
        """What a job waits on before it has a worker (``detail``), or None once it has one.

        The daemon's own wait, which nothing the job runs can report: a worker being
        started for it (``server._handle_request``). The job reads as queued, with the
        wait as its detail -- the line the CAD Viewer's loading screen shows under its
        label -- rather than as a bare submission.
        """
        with self._guard:
            if job["state"] not in ("submitted", "queued"):
                return
            if detail:
                job.update(state="queued", phase="queued", detail=str(detail))
            elif job["phase"] == "queued":
                job.update(phase=None, detail="")
            else:
                return
            job["updatedAt"] = self._clock()
            self._notify(job)

    def observe(self, frame: dict[str, Any]) -> None:
        """Fold one relayed frame into the ledger (only ``event`` frames matter): a
        transition of the request's own model, or of a child the request built inside its
        body -- one in the request's own file included -- which has a row of its own under
        the request (:meth:`_child_row`)."""
        event = frame.get("event") if isinstance(frame, dict) else None
        if not isinstance(event, dict):
            return
        model = _model_subject(str(event.get("model") or ""))
        state = str(event.get("state") or "")
        if not model or not state:
            return
        now = self._clock()
        producer = str(event.get("job") or "")
        with self._guard:
            request = self._jobs.get(producer) if producer else self._running_for(model)
            if request is None:
                return  # a transition for a job this ledger never saw start
            job = request
            if producer and not _same_model(model, request["subject"]):
                job = self._child_row(request, model, now)
            ordinal = int(event.get("sequence") or 0)
            if ordinal and ordinal <= job.get("eventSequence", 0):
                return
            if ordinal:
                job["eventSequence"] = ordinal
            if job.get("finishedAt") is None:
                if state in ("submitted", "queued"):
                    if job["state"] == "submitted" or state == "queued":
                        job["state"] = state
                elif state == "building":
                    job["state"] = "building"
                    job["phase"] = event.get("phase") or job["phase"]
                    if event.get("detail") is not None:
                        job["detail"] = str(event.get("detail") or "")
                    job["done"] = event.get("done")
                    job["total"] = event.get("total")
                elif state in ("done", "current"):
                    # A request may run several decorated models, so only its process's
                    # completion closes it (``finish``); a child's own done closes the child.
                    if not producer or job is not request:
                        job["state"] = "done"
                        job["finishedAt"] = now
                elif state == "failed":
                    job["state"] = "failed"
                    job["exit"] = event.get("exit", job["exit"])
                    if event.get("error"):
                        job["error"] = str(event["error"])
                    job["finishedAt"] = now
            # A build's results fold in whenever they land, a saved document after its
            # row closed included (a child's export lands after its done): the names a
            # reader reads, never a state the row has left.
            for field, (bucket, kept) in _KEPT_RESULT_FIELDS.items():
                payload = event.get(field)
                if not isinstance(payload, dict) or not payload.get("output") or not payload.get("tree"):
                    continue
                output = _real(str(payload["output"]))
                updates = job.setdefault(bucket, {})
                previous = updates.get(output)
                if previous is None or ordinal > int(previous.get("sequence") or 0):
                    if field == "saving":
                        # A newer result takes over this file even after its row expires.
                        # Remember that on each older obligation, so finishing or sweeping
                        # the newer request cannot make a door wait on it again.
                        for older in self._jobs.values():
                            if older is job or older.get("storeRoot") != job.get("storeRoot"):
                                continue
                            superseded = (older.get("saving") or {}).get(output)
                            if isinstance(superseded, dict) and not superseded.get("superseded"):
                                superseded["superseded"] = True
                                self._notify(older)
                    updates[output] = {**{key: copy.deepcopy(payload[key]) for key in kept
                                          if payload.get(key) is not None},
                                       "output": output, "sequence": ordinal, "revision": self._revision + 1}
                if output not in job["outputs"]:
                    job["outputs"].append(output)
            job["updatedAt"] = now
            self._notify(job)

    def _child_row(self, request: dict[str, Any], model: str, now: float) -> dict[str, Any]:
        """The row of ``model``, a child built inside ``request``: made at its first
        event, with the child's declared outputs and the request's store."""
        for job in self._jobs.values():
            if job.get("request") == request["id"] and job["subject"] == model:
                return job
        sequence = next(self._ids)
        job: dict[str, Any] = {
            "id": f"{self.epoch}:job-{sequence}", "epoch": self.epoch, "sequence": sequence,
            "storeRoot": request["storeRoot"], "tool": "run", "subject": model, "request": request["id"],
            "outputs": self._declared_outputs(model, "run"), "argv": [], "state": "submitted",
            "phase": None, "detail": "", "done": None, "total": None, "startedAt": now,
            "updatedAt": now, "finishedAt": None, "exit": None, "error": None,
        }
        self._jobs[job["id"]] = job
        return job

    def finish(self, job: dict[str, Any], exit_code: int, *, error: str | None = None,
               cancelled: bool = False) -> None:
        """Close the job. ``error`` is the failure's one-line reason (see
        :func:`failure_message`), kept so a reader can say WHY, not just that.

        ``cancelled``: the job was stopped because whoever asked for it left. That says
        nothing about the source, so it ends ``cancelled``, never ``failed``: a reader
        showing the document builds it again rather than reporting a failure that was
        only a closed connection.

        The request's end settles the files its rows owed (``saving``): none is written
        after it. A row that owed a file the request never saved -- a child whose own done
        closed its row before its file was written -- fails with the request's reason when
        the request failed, so the file's reader sees why, under the row's preview.
        """
        now = self._clock()
        with self._guard:
            # A child row the request leaves running -- its worker died, its caller left --
            # ends as the request does; one already closed settles what it owed.
            rows = [job, *(row for row in self._jobs.values() if row.get("request") == job["id"]
                           and (row["state"] in _RUNNING or "saving" in row))]
            for row in rows:
                closing = row is job or row["state"] in _RUNNING
                unsaved = not closing and exit_code != 0 and not cancelled and any(
                    _owes(row, output) for output in row["saving"])
                if closing or unsaved:
                    if row["state"] in _RUNNING or unsaved:
                        row["state"] = "cancelled" if cancelled else "done" if exit_code == 0 else "failed"
                    row["exit"] = int(exit_code)
                    if exit_code != 0 and error and not cancelled:
                        row["error"] = str(error)
                    row["finishedAt"] = row["finishedAt"] or now
                row.pop("saving", None)
                row["updatedAt"] = now
            self._sweep(now)
            for row in rows:
                self._notify(row)

    # --- reading -----------------------------------------------------------------

    def snapshot(self) -> list[dict[str, Any]]:
        """Every running job and every job finished within the retention window."""
        with self._guard:
            self._sweep(self._clock())
            return copy.deepcopy(list(self._jobs.values()))

    def watch(self, after: str | None = None, *, timeout: float = 1.0,
              output: str | None = None, store_root: str | None = None) -> dict:
        """Wait for a ledger change without occupying a kernel worker.

        Cursor and snapshot are captured under the same lock. The epoch makes
        an old daemon's cursor immediately expire after restart. A bounded
        heartbeat also exposes retained-job expiry and lets document readers
        revalidate saved bytes and missing objects without a new build event.
        """
        deadline = time.monotonic() + max(0.0, min(float(timeout), 1.0))
        normalized_output = _real(output) if output and store_root else ""
        normalized_store = _real(store_root) if output and store_root else ""

        def selected() -> list[dict[str, Any]]:
            if not normalized_output or not normalized_store:
                return list(self._jobs.values())
            return [
                job for job in self._jobs.values()
                if job.get("tool") == "run"
                and job.get("storeRoot") and _real(job["storeRoot"]) == normalized_store
                and normalized_output in {_real(path) for path in job.get("outputs", [])}
            ]

        def cursor(jobs: list[dict[str, Any]]) -> str:
            if not normalized_output or not normalized_store:
                return f"{self.epoch}:{self._revision}"
            digest = hashlib.sha256()
            for value in (self.epoch, normalized_output, normalized_store):
                digest.update(value.encode("utf-8", errors="surrogatepass"))
                digest.update(b"\0")
            for job in jobs:
                digest.update(str(job.get("id") or "").encode("utf-8", errors="surrogatepass"))
                digest.update(b"\0")
                digest.update(str(int(job.get("ledgerRevision") or 0)).encode("ascii"))
                digest.update(b"\0")
            return f"{self.epoch}:scope:{digest.hexdigest()}"

        with self._changed:
            self._sweep(self._clock())
            jobs = selected()
            jobs_cursor = cursor(jobs)
            while after == jobs_cursor:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._changed.wait(remaining)
                self._sweep(self._clock())
                jobs = selected()
                jobs_cursor = cursor(jobs)
            return {"jobsCursor": jobs_cursor, "jobs": copy.deepcopy(jobs)}

    def _notify(self, job: dict[str, Any] | None = None) -> None:
        """Called with the ledger lock held; no event history is retained."""
        self._revision += 1
        if job is not None:
            job["ledgerRevision"] = self._revision
        self._changed.notify_all()

    def _running_for(self, subject: str) -> dict[str, Any] | None:
        for job in reversed(list(self._jobs.values())):
            if job["subject"] == subject and job["state"] in _RUNNING:
                return job
        return None

    def _sweep(self, now: float) -> None:
        # A row whose request is still writing a file it owes stays: a door reads it until the
        # file is saved or the request ends (``finish``).
        for key, job in list(self._jobs.items()):
            finished = job.get("finishedAt")
            if finished is not None and now - finished > self._retain and "saving" not in job:
                self._jobs.pop(key, None)
                self._notify()
