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
cancelled. Stdlib only; nothing here imports the kernel, and nothing here runs or
imports a model: a job's outputs come from ``cadgen.metadata``'s AST parse of its
script (``static_output_paths``: a literal ``out=`` and the mesh beside it), and what
the text alone cannot say (an ``out=`` computed at import) its worker names in the
first event of each model it builds (``outputs``). The supervisor never imports model
code, so no module top -- a fault, a sleep, an import only the caller's path finds --
can reach it.

A row also says what its build still owes once its results are recorded: build-pool jobs
the build handed the supervisor (:meth:`JobLedger.owe`, ``server._start_owed``) -- the
export or mesh job writing its files, each file named ``saving`` until the job says it has
landed (``saved``, :meth:`JobLedger.published`), and the display jobs deriving its preview's
parts, which name each part as their answers come (``pending`` to ``parts``). A door given a
path asks this (:func:`being_written`) and waits while a job is still writing the file
(STORE.md §3, *Exports*). A request's row stays open until every job its rows owe has ended,
however its process ended; a file a job failed to save fails the row that owed it, with the
job's reason.
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
# component the store holds and of each it does not yet (``pending``), and whether that is all
# of them; of the preview's annotations the one object's hash; of the saved document its tree
# and digest, which tell whether the file moved on. A status names objects and carries no
# geometry or annotation content. Each is kept with the ledger's revision when it landed, the
# order of results across rows. The files a build owes are no event's: the supervisor names
# them as it takes the job writing them (:meth:`JobLedger.owe`).
_KEPT_RESULT_FIELDS = {
    "preview": ("previews", ("tree", "producer", "parts", "pending", "complete")),
    "annotations": ("annotations", ("tree", "object")),
    "saved": ("savedResults", ("tree", "documentHash")),
}
# The pool jobs a build hands over, and what each writes or derives.
_PUBLISHING = ("export", "mesh")
# A row keeps this many of the warnings its owed jobs leave (a display job's failure).
_WARNINGS_KEPT = 16
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def _owes(job: dict[str, Any], output: str) -> bool:
    """Whether ``job``'s build still owes ``output``: a job it handed over is writing the file,
    and no newer result of the model has taken the file over since."""
    owed = (job.get("saving") or {}).get(output)
    return isinstance(owed, dict) and not owed.get("superseded")


def being_written(jobs: list[dict[str, Any]], output: str) -> bool:
    """Whether a job of a request in ``jobs`` (the ledger's rows for ``output``, of one store)
    is still writing it. Only the newest result that owes the file is the one it will hold: an
    older build's export is skipped or refused once a newer result of the model is recorded
    (STORE.md §3, *Exports*), and a newer result's job takes the file over from older ones as
    the supervisor takes it (:meth:`JobLedger.owe`), so it is waited for alone, until it
    saves the file or ends. A file the asking request's own run owes is waited for like any
    other: a pool job writes it, never the build that waits."""
    return any(_owes(job, output) for job in jobs)


def _publication_outputs(request: dict) -> list[str]:
    """The files an export or mesh request writes (``artifacts.publication_files``): the STEP,
    then the meshes."""
    return ([str(request["step"])] if request["kind"] == "export" else []) + \
        [str(mesh["path"]) for mesh in request.get("meshes") or ()]


def _answer(frame: object) -> object:
    """An artifact job's answer, from the result frame it came in (``{"request", "result"}``)."""
    return frame.get("result") if isinstance(frame, dict) else None


def _artifact_summary(request: dict) -> dict[str, Any]:
    """What a row keeps of an artifact request: the request itself, or, for one that publishes
    a model's files, what a reader asks of it -- the model, the tree and the files -- never the
    pins it carries for the job."""
    if request.get("kind") in _PUBLISHING:
        return {"kind": request["kind"], "model": request["model"], "tree": request["tree"],
                "outputs": [_real(path) for path in _publication_outputs(request)]}
    return copy.deepcopy(request)

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


def declared_outputs(subject: str) -> list[str]:
    """The output paths a job will write, from its declarations' TEXT alone.

    A compile (``step-compile``) writes the tree for the document it names — the
    document IS its output. A model script's outputs are its literal ``out=`` document
    (else the sibling ``<stem>.step`` / ``.dxf``) and every declared mesh export,
    resolved exactly as the build resolves them (``cadgen.metadata.static_output_paths``).
    The script is parsed, never imported: an output only an import could evaluate is
    named by the worker's first event instead. Never raises: a script that cannot be
    parsed simply declares nothing.
    """
    if not subject:
        return []
    script_ref, _, function = subject.partition("::")
    if not script_ref.endswith(".py"):
        return [_real(subject)]
    try:
        from cadgen.metadata import static_output_paths

        paths = static_output_paths(Path(script_ref), function=function or None)
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

    def _declared_outputs(self, subject: str) -> list[str]:
        """:func:`declared_outputs`, plus what the subject's builds named the last time one
        ran here (an ``out=`` the text cannot evaluate, :meth:`_name_outputs`), or, when the
        script no longer parses (an edit broke it), what it declared the last time it did:
        the job is still listed against the documents its script writes, so its failure
        reaches whoever shows them. Never a guess at the source: only what the script itself
        declared, in this process."""
        outputs = declared_outputs(subject)
        if not subject:
            return outputs
        with self._last_declared_guard:
            if outputs:
                self._last_declared[subject] = list(dict.fromkeys([*outputs, *self._last_declared.get(subject, ())]))
            return list(self._last_declared.get(subject, outputs))

    def _name_outputs(self, job: dict[str, Any], outputs: object) -> None:
        """The files a build names in a model's first event (``generation._declared_outputs``):
        the row's outputs, and the subject's for its next run, from now on."""
        if not isinstance(outputs, list):
            return
        named = [_real(str(path)) for path in outputs if isinstance(path, str) and path]
        for output in named:
            if output not in job["outputs"]:
                job["outputs"].append(output)
        if named and job.get("subject"):
            with self._last_declared_guard:
                self._last_declared[job["subject"]] = list(dict.fromkeys(
                    [*named, *self._last_declared.get(job["subject"], ())]))

    # --- lifecycle -------------------------------------------------------------

    def start(self, *, tool: str, subject: str, argv: list[str] | None = None, store_root: str = "") -> dict[str, Any]:
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
            "outputs": [] if tool == "artifact" else self._declared_outputs(subject),
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
            job["artifact"] = _artifact_summary(request)
            job["dependency"] = bool(dependency)
            self._notify(job)
        return job

    def owe(self, owner: dict[str, Any], model: str, request: dict, *, store_root: str) -> dict[str, Any]:
        """A job ``owner``'s build handed over (``server._start_owed``): the build-pool job
        ``model``'s result owes, as an artifact row of its own, registered on the model's row
        -- ``owner`` itself, or the child row it built (:meth:`_child_row`). An export or mesh
        job's files are owed (``saving``) from now on, each taking the file over from any
        older result's job in the store; a display job derives the parts its row's preview of
        the tree names ``pending``. The row, and its request's, stays open until the job ends
        (:meth:`finish`)."""
        job = self.start_artifact(request, store_root=store_root, dependency=True)
        now = self._clock()
        with self._guard:
            subject = _model_subject(str(model))
            row = owner if _same_model(subject, owner["subject"]) else self._child_row(owner, subject, now)
            job["owner"], job["ownerRow"] = owner["id"], row["id"]
            if request["kind"] in _PUBLISHING:
                for output in job["artifact"]["outputs"]:
                    self._supersede(row, output)
                    self._revision += 1
                    row.setdefault("saving", {})[output] = {
                        "output": output, "tree": str(request["tree"]), "artifact": job["id"],
                        "sequence": int(row.get("eventSequence") or 0), "revision": self._revision}
                    if output not in row["outputs"]:
                        row["outputs"].append(output)
            row.setdefault("owing", []).append(job["id"])
            row["updatedAt"] = now
            self._notify(job)
            self._notify(row)
        return job

    def published(self, job: dict[str, Any], payload: dict) -> None:
        """A file ``job`` (an export or mesh job) saved: the ``saved`` result of the row whose
        build owes it, the moment it lands, which owes that file no more."""
        output, tree = _real(str(payload["output"])), str(payload["tree"])
        digest = payload.get("documentHash")
        artifact = job.get("artifact") or {}
        if job.get("tool") != "artifact" or artifact.get("kind") not in _PUBLISHING:
            raise ValueError("only an export or mesh job saves a file")
        if output not in artifact.get("outputs") or tree != artifact.get("tree"):
            raise ValueError("a job saved a file its request does not write")
        if digest is not None and (not isinstance(digest, str) or not _DIGEST.fullmatch(digest)):
            raise ValueError("a saved file's digest is a sha256")
        now = self._clock()
        with self._guard:
            row = self._jobs.get(str(job.get("ownerRow") or ""))
            if row is None:
                return  # a job no build of this daemon handed over: nobody's row to tell
            owed = (row.get("saving") or {}).get(output)
            self._revision += 1
            row.setdefault("savedResults", {})[output] = {
                "tree": tree, **({"documentHash": digest} if digest else {}), "output": output,
                "sequence": int((owed or {}).get("sequence") or row.get("eventSequence") or 0),
                "revision": self._revision}
            if isinstance(owed, dict) and owed.get("artifact") == job["id"]:
                self._settle_owed(row, output)
            row["updatedAt"] = now
            self._notify(row)

    def record_artifact_result(self, job: dict[str, Any], result: dict) -> None:
        """``job``'s answer (its result frame, ``artifacts.result_frame``)."""
        with self._guard:
            if job["tool"] == "artifact" and job.get("artifactResult") is None:
                job["artifactResult"] = copy.deepcopy(result)
                job["updatedAt"] = self._clock()
                self._name_parts(job, _answer(result))
                self._notify(job)

    # --- what a row's build owes -------------------------------------------------------

    def _supersede(self, row: dict[str, Any], output: str) -> None:
        """A newer result takes ``output`` over, even after its row expires: remember that on
        each older row's obligation (and retained failure) for it in the store, so finishing or
        sweeping the newer request cannot make a door wait on it again."""
        for older in self._jobs.values():
            if older is row or older.get("storeRoot") != row.get("storeRoot"):
                continue
            for bucket in ("saving", "saveFailures"):
                superseded = (older.get(bucket) or {}).get(output)
                if isinstance(superseded, dict) and not superseded.get("superseded"):
                    superseded["superseded"] = True
                    if output not in older.setdefault("supersededOutputs", []):
                        older["supersededOutputs"].append(output)
                    self._notify(older)

    @staticmethod
    def _settle_owed(row: dict[str, Any], output: str) -> None:
        row["saving"].pop(output, None)
        if not row["saving"]:
            row.pop("saving")

    def _name_parts(self, job: dict[str, Any], result: object) -> None:
        """The parts a display job ``job`` handed over stored (its answer, ``{cid: record}``),
        named in each preview of its request that is waiting for them -- its own model's, and
        a parent's, whose tree places the child's components by the same ids -- under the same
        producer: moved from ``pending`` to ``parts``, the preview ``complete`` once nothing
        is pending."""
        artifact = job.get("artifact") or {}
        row = self._jobs.get(str(job.get("ownerRow") or ""))
        if row is None or artifact.get("kind") != "display" or not isinstance(result, dict):
            return
        for waiting in self._rows_of(self._request_of(row)):
            for preview in (waiting.get("previews") or {}).values():
                pending = preview.get("pending")
                if not isinstance(pending, dict) or preview.get("producer") != artifact.get("producer"):
                    continue
                named = {cid: pending.pop(cid) for cid in list(pending) if cid in result}
                if not named:
                    continue
                preview.setdefault("parts", {}).update(named)
                preview["complete"] = not pending
                self._revision += 1
                preview["revision"] = self._revision
                waiting["updatedAt"] = self._clock()
                self._notify(waiting)

    def _name_stored_parts(self, row: dict[str, Any], preview: dict[str, Any]) -> None:
        """A preview announced after a display job of its request answered -- a child's,
        whose parts the parent's tree places -- names the parts that job stored at once."""
        pending = preview.get("pending")
        if not isinstance(pending, dict) or not pending:
            return
        request = self._request_of(row)
        owners = {owner["id"] for owner in self._rows_of(request)}
        for job in self._jobs.values():
            artifact = job.get("artifact") or {}
            result = _answer(job.get("artifactResult"))
            if (job.get("ownerRow") not in owners or artifact.get("kind") != "display"
                    or artifact.get("producer") != preview.get("producer") or not isinstance(result, dict)):
                continue
            named = {cid: pending.pop(cid) for cid in list(pending) if cid in result}
            if named:
                preview.setdefault("parts", {}).update(named)
                preview["complete"] = not pending

    def _owed_job_ended(self, job: dict[str, Any], exit_code: int, error: str | None, now: float) -> None:
        """A job ``job``'s build handed over has ended: the files it still owed are no longer
        written -- each a save failure of its row when the job failed (the row fails with the
        job's reason; its request's too, once it closes), else (a newer result took the file
        over, or nobody wanted it) owed no more -- a display job's failure is its row's
        warning, and the row, and its request's, may close (:meth:`_close_if_settled`)."""
        row = self._jobs.get(str(job.get("ownerRow") or ""))
        if row is None:
            return
        failed = False
        for output, owed in list((row.get("saving") or {}).items()):
            if owed.get("artifact") != job["id"]:
                continue
            self._settle_owed(row, output)
            if exit_code != 0 and not owed.get("superseded"):
                self._revision += 1
                row.setdefault("saveFailures", {})[output] = {
                    "output": output, "tree": owed["tree"], "sequence": owed["sequence"], "revision": self._revision}
                failed = True
        reason = str(error or "") or f"the build service's job exited {exit_code}"
        if failed:
            request = self._request_of(row)
            for failing in {id(row): row, id(request): request}.values():
                if failing.get("finishedAt") is None:
                    failing["saveError"] = reason  # its state when it closes
                elif failing.get("state") != "failed":
                    # Closed already (a child at its done, a request cancelled): it fails now.
                    failing.update(state="failed", exit=1, error=reason, finishedAt=now)
                    self._notify(failing)
        elif exit_code != 0 and (job.get("artifact") or {}).get("kind") == "display":
            warnings = row.setdefault("warnings", [])
            warnings.append(f"display data not derived: {reason}")
            del warnings[:-_WARNINGS_KEPT]
        owing = row.get("owing") or []
        if job["id"] in owing:
            owing.remove(job["id"])
        if not owing:
            row.pop("owing", None)
        row["updatedAt"] = now
        self._notify(row)
        self._close_if_settled(self._request_of(row), now)

    def _request_of(self, row: dict[str, Any]) -> dict[str, Any]:
        return self._jobs.get(str(row.get("request") or ""), row)

    def _rows_of(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        return [request, *(row for row in self._jobs.values() if row.get("request") == request["id"])]

    def _close_if_settled(self, request: dict[str, Any], now: float) -> None:
        """Close a request whose process ended well (``exited``) once no row of it owes a job:
        done, or failed when a file it owed was not saved."""
        if request.get("exited") is None or any(row.get("owing") for row in self._rows_of(request)):
            return
        failed = request.pop("saveError", None)
        request.pop("exited")
        request.update(state="failed" if failed else "done", exit=1 if failed else 0, error=failed or None,
                       finishedAt=now, updatedAt=now)
        self._notify(request)

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
            # The files the build names as it takes the model up: what the script's text
            # could not say (an ``out=`` evaluated at import).
            self._name_outputs(job, event.get("outputs"))
            # A build's results fold in whenever they land, a saved document after its
            # row closed included (a kept file published after a child's done): the names a
            # reader reads, never a state the row has left.
            for field, (bucket, kept) in _KEPT_RESULT_FIELDS.items():
                payload = event.get(field)
                if not isinstance(payload, dict) or not payload.get("output") or not payload.get("tree"):
                    continue
                output = _real(str(payload["output"]))
                updates = job.setdefault(bucket, {})
                previous = updates.get(output)
                if previous is None or ordinal > int(previous.get("sequence") or 0):
                    updates[output] = {**{key: copy.deepcopy(payload[key]) for key in kept
                                          if payload.get(key) is not None},
                                       "output": output, "sequence": ordinal, "revision": self._revision + 1}
                    if field == "preview":
                        self._name_stored_parts(job, updates[output])
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
            "outputs": self._declared_outputs(model), "argv": [], "state": "submitted",
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

        A child row the request leaves running -- its worker died, its caller left -- ends as
        the request does. The jobs its rows handed over are the supervisor's and go on: a
        request that ended well stays open while any of them runs (``exited``), so it reads
        as building until its files land, and closes as the last ends
        (:meth:`_close_if_settled`); one that failed or was cancelled closes now, and the
        files its jobs write still land on their rows. A job a build handed over settles
        what it owed as it ends (:meth:`_owed_job_ended`).
        """
        now = self._clock()
        with self._guard:
            if job.get("ownerRow"):
                self._owed_job_ended(job, int(exit_code), error, now)
            rows = [job, *(row for row in self._jobs.values() if row.get("request") == job["id"]
                           and row["state"] in _RUNNING)]
            for row in rows:
                if row is job and exit_code == 0 and not cancelled and job["tool"] != "artifact" \
                        and any(owing.get("owing") for owing in self._rows_of(job)):
                    job["exited"] = 0
                    job["updatedAt"] = now
                    continue
                failed = exit_code != 0 or bool(row.get("saveError"))
                row["state"] = "cancelled" if cancelled else "failed" if failed else "done"
                row["exit"] = int(exit_code) if exit_code != 0 or not row.get("saveError") else 1
                if exit_code != 0 and error and not cancelled:
                    row["error"] = str(error)
                elif row.get("saveError") and not cancelled:
                    row["error"] = row["saveError"]
                row.pop("saveError", None)
                row["finishedAt"] = row["finishedAt"] or now
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
        # A row whose build still owes a job it handed over stays: a door reads it until the
        # file is saved or the job ends (``_owed_job_ended``).
        for key, job in list(self._jobs.items()):
            finished = job.get("finishedAt")
            if finished is not None and now - finished > self._retain and "saving" not in job \
                    and not job.get("owing"):
                self._jobs.pop(key, None)
                self._notify()
