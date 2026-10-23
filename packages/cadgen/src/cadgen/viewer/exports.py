"""Session-owned, in-memory export jobs and explicitly requested temporary downloads.

No native save dialog: the destination is chosen on the browser's computer. Kernel
work goes through the engine worker. Disconnecting a request never cancels a job.
"""
from __future__ import annotations

import contextlib
import json
import math
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
from pathlib import Path

FORMATS = {"step": ".step", "glb": ".glb", "stl": ".stl", "threemf": ".3mf", "dxf": ".dxf", "xbf": ".xbf", "original": ".xbf"}
ARTIFACT_SECONDS = 3600
STATUS_SECONDS = 86400
MAX_JOBS = 16
MAX_RETAINED_BYTES = 4 * 1024**3
TERMINAL = {"ready", "failed", "cancelled", "expired"}


class ExportError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def specification(payload):
    if not isinstance(payload, dict) or set(payload) - {"doc", "revision", "part", "pose"}:
        raise ExportError("Export source is {doc, revision, part?, pose?}.")
    if not all(isinstance(payload.get(k), str) and payload[k] for k in ("doc", "revision")):
        raise ExportError("Export requires the displayed document and revision.")
    part, pose = payload.get("part"), payload.get("pose")
    if part is not None and (not isinstance(part, str) or not part or len(part) > 4096):
        raise ExportError("Select one part or assembly.")
    if pose is not None and (not isinstance(pose, dict) or len(pose) > 1024 or any(
            not isinstance(k, str) or not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v)
            for k, v in pose.items())):
        raise ExportError("Position must contain finite joint values.")
    return {"doc": payload["doc"], "revision": payload["revision"], "part": part, "pose": pose}


class ExportJobs:
    def __init__(self, server, *, run=None, clock=time.time, root=None):
        self.server, self.clock = server, clock
        self.epoch = secrets.token_urlsafe(18)
        self.changed = threading.Condition(threading.RLock())
        self.jobs, self.requests, self.inspections = {}, {}, {}
        self.root = Path(root) if root else None
        self.run = run or self._worker
        self.started = False
        self.executing = False

    def _maintenance(self):
        # Crash recovery has no persistent job registry. Reap only this service's private
        # directories after a full day; living owners refresh their directory timestamp.
        for directory in (self.root or Path(tempfile.gettempdir())).glob("cadgen-download-*"):
            try:
                if directory.is_dir() and not directory.is_symlink() and time.time() - directory.stat().st_mtime > STATUS_SECONDS:
                    shutil.rmtree(directory)
            except OSError:
                pass
        while True:
            with self.changed:
                self.changed.wait(timeout=30)
                self._prune()
                for job in self.jobs.values():
                    if job["directory"]:
                        try:
                            os.utime(job["directory"], None)
                        except OSError:
                            pass
                active = any(j["state"] not in TERMINAL for j in self.jobs.values())
                active = active or any(not row["done"] for row in self.inspections.values())
            if active:
                self.server.touch()
            else:
                # Only run a janitor while this session owns anything.
                with self.changed:
                    if not self.jobs and not self.inspections:
                        self.started = False
                        return

    def keeps_alive(self):
        with self.changed:
            self._prune()
            return (any(job["state"] not in TERMINAL or job["state"] == "ready" for job in self.jobs.values())
                    or any(not row["done"] for row in self.inspections.values()))

    def _start(self):
        if not self.started:
            self.started = True
            threading.Thread(target=self._maintenance, daemon=True, name="cadgen-export-cleanup").start()

    def _prune(self):
        now = self.clock()
        for ident, job in list(self.jobs.items()):
            if job["state"] == "ready" and now >= job["expiresAt"] and not job["readers"]:
                self._remove(job)
                self._change(job, state="expired", stage="Export expired")
            if job["state"] in TERMINAL and now - job["finishedAt"] >= STATUS_SECONDS and not job["readers"]:
                self._remove(job)
                self.jobs.pop(ident)
        for key, row in list(self.inspections.items()):
            if row["done"] and now - row["at"] > 30:
                self.inspections.pop(key)

    def _source(self, spec):
        if self.server is None:
            raise ExportError("This host does not serve exportable documents.")
        with self.server.load_lock:
            doc = self.server.served.get(spec["doc"])
            if doc is None or doc.version != spec["revision"]:
                raise ExportError("The source changed. Open Export again to use the displayed revision.", 409)
            path, digest = str(doc.path), doc.structure.get("exportRevision")
        from cadgen.engine.addresses import parse
        from cadgen.engine.client import request

        address = parse(path)
        if address:
            if not digest:
                raise ExportError("Reopen this model before exporting.", 409)
            answer = request({"op": "model_source", "address": path})
            if not answer or "source" not in answer:
                raise ExportError("The viewed model is no longer held.", 409)
            source, expected = answer["source"], {"digest": digest, "address": path, "modelRevision": spec["revision"]}
        else:
            source, expected = {"path": path}, {"version": spec["revision"]}
        name = path.rsplit("::", 1)[-1] if address else Path(path).stem
        return source, expected, name

    def _worker(self, spec, target, progress):
        from cadgen.engine.client import request

        source, expected, _name = self._source(spec)
        answer = request({"op": "export", "source": source, "expected": expected,
                          "destinations": [str(target)] if target else [], "inspect": target is None,
                          "part": spec.get("part"), "pose": spec.get("pose"),
                          "original": spec.get("format") == "original"}, on_progress=progress)
        if not answer or "failed" in answer:
            raise ExportError((answer or {}).get("failed", "The export worker disconnected."))
        return answer["done"]

    def options(self, payload):
        spec = specification(payload)
        key = json.dumps(spec, sort_keys=True)
        with self.changed:
            self._prune()
            row = self.inspections.get(key)
            if row is None:
                if sum(not r["done"] for r in self.inspections.values()) >= MAX_JOBS:
                    raise ExportError("Already checking formats. Try again shortly.", 429)
                row = self.inspections[key] = {"done": False, "at": self.clock()}
                self._start()
                threading.Thread(target=self._inspect, args=(row, spec), daemon=True).start()
            if not row["done"]:
                return 202, {"state": "checking", "retryMs": 500}
            if "error" in row:
                raise ExportError(row["error"], 409)
            return 200, {"epoch": self.epoch, "formats": row["formats"]}

    def _inspect(self, row, spec):
        try:
            formats = self.run(spec, None, lambda _p: None)["formats"]
            with self.changed:
                row["formats"] = formats
        except Exception as error:
            with self.changed:
                row["error"] = str(error)
        finally:
            with self.changed:
                row.update(done=True, at=self.clock())
                self.changed.notify_all()

    def create(self, payload):
        if not isinstance(payload, dict) or set(payload) != {"requestId", "epoch", "source", "format"}:
            raise ExportError("Export requires requestId, epoch, source and format.")
        spec = specification(payload["source"])
        kind, request_id = payload["format"], payload["requestId"]
        if not isinstance(kind, str) or kind not in FORMATS or not isinstance(request_id, str) or not re.fullmatch(r"[\w-]{16,100}", request_id):
            raise ExportError("Invalid export format or request identity.")
        if payload["epoch"] != self.epoch:
            raise ExportError("The export session restarted. Open Export again.", 409)
        if kind == "original" and (spec["part"] is not None or spec["pose"] is not None):
            raise ExportError("Download original uses the whole authored XBF.")
        normalized = json.dumps(payload, sort_keys=True)
        with self.changed:
            self._prune()
            previous = self.requests.get(request_id)
            if previous:
                job = self.jobs.get(previous)
                if job is None:
                    raise ExportError("This export expired. Open Export to start a new job.", 410)
                if job["request"] != normalized:
                    raise ExportError("This request ID already names another export.", 409)
                return self.public(job)
            if len(self.jobs) >= 128 or len(self.requests) >= 4096:
                raise ExportError("Export history is full for this session. Open a new viewer session.", 429)
            if sum(j["state"] not in TERMINAL for j in self.jobs.values()) >= MAX_JOBS:
                raise ExportError("The export queue is full. Wait or cancel a queued job.", 429)
            if sum(j.get("bytes", 0) for j in self.jobs.values() if j["state"] == "ready") >= MAX_RETAINED_BYTES:
                raise ExportError("Export storage is full. Release a previous export first.", 429)
            # Resolving a held source is done by the background job, never executing code here.
            ident = secrets.token_urlsafe(18)
            spec["format"] = kind
            job = {"id": ident, "requestId": request_id, "request": normalized, "spec": spec,
                   "state": "queued", "stage": "Waiting for export worker", "sequence": 0,
                   "createdAt": self.clock(), "finishedAt": 0, "expiresAt": 0, "readers": 0,
                   "cancel": False, "directory": None, "filename": "export" + FORMATS[kind]}
            self.jobs[ident], self.requests[request_id] = job, ident
            self._start()
            threading.Thread(target=self._execute, args=(job,), daemon=True, name="cadgen-export").start()
            return self.public(job)

    def _change(self, job, **fields):
        job.update(fields)
        job["sequence"] += 1
        self.changed.notify_all()

    def _execute(self, job):
        admitted = False
        try:
            with self.changed:
                self.changed.wait_for(lambda: job["cancel"] or (not self.executing and next(
                    (j for j in self.jobs.values() if j["state"] == "queued"), None) is job))
                if job["cancel"]:
                    return
                self.executing = admitted = True
                self._change(job, state="running", stage="Preparing export")
            _source, _expected, name = self._source(job["spec"])
            filename = (re.sub(r"[^\w .-]", "_", name)[:100].strip(" .") or "export") + FORMATS[job["spec"]["format"]]
            directory = Path(tempfile.mkdtemp(prefix="cadgen-download-", dir=self.root))
            with self.changed:
                job["directory"] = directory
                self._change(job, filename=filename)
            target = directory / filename
            def progress(frame):
                with self.changed:
                    if not job["cancel"]:
                        self._change(job, state="running", stage="Writing " + filename)
            with self.changed:
                if job["cancel"]:
                    return
                self._change(job, state="running", stage="Preparing export")
            self.run(job["spec"], target, progress)
            with self.changed:
                if job["cancel"]:
                    return
                size = target.stat().st_size
                retained = sum(j.get("bytes", 0) for j in self.jobs.values() if j["state"] == "ready")
                if size + retained > MAX_RETAINED_BYTES:
                    raise ExportError("Export exceeded the temporary storage limit.")
                self._change(job, state="ready", stage="Ready to save", bytes=size,
                             finishedAt=self.clock(), expiresAt=self.clock() + ARTIFACT_SECONDS)
        except Exception as error:
            with self.changed:
                if not job["cancel"]:
                    self._change(job, state="failed", stage="Export failed", error=str(error), finishedAt=self.clock())
        finally:
            with self.changed:
                if job["cancel"]:
                    self._change(job, state="cancelled", stage="Export cancelled", finishedAt=self.clock())
                if job["state"] != "ready":
                    self._remove(job)
                if admitted:
                    self.executing = False
                self.changed.notify_all()

    def _remove(self, job):
        if job["directory"]:
            shutil.rmtree(job["directory"], ignore_errors=True)
            job["directory"] = None

    def public(self, job):
        fields = ("id", "requestId", "filename", "state", "stage", "sequence", "createdAt", "finishedAt", "expiresAt", "bytes", "error")
        return {**{k: job[k] for k in fields if k in job}, "epoch": self.epoch}

    def get(self, ident, after=None, wait=0):
        with self.changed:
            self._prune()
            job = self.jobs.get(ident)
            if job is None:
                raise ExportError("Export no longer exists; the server may have restarted.", 404)
            if after is not None and job["sequence"] == after and job["state"] not in TERMINAL:
                self.changed.wait_for(lambda: job["sequence"] != after, timeout=min(25, max(0, wait)))
            return self.public(job)

    def cancel(self, ident):
        with self.changed:
            self.get(ident)
            job = self.jobs[ident]
            if job["state"] not in TERMINAL:
                job["cancel"] = True
                if job["state"] == "queued":
                    self._change(job, state="cancelled", stage="Export cancelled", finishedAt=self.clock())
                else:
                    self._change(job, state="cancelling", stage="Cancelling after the current step")
            return self.public(job)

    def release(self, ident):
        with self.changed:
            self.get(ident)
            job = self.jobs[ident]
            if job["state"] not in TERMINAL:
                raise ExportError("Cancel this export before releasing it.", 409)
            job["expiresAt"] = 0
            self._prune()
            return self.public(job)

    @contextlib.contextmanager
    def artifact(self, ident):
        with self.changed:
            self.get(ident)
            job = self.jobs[ident]
            if job["state"] != "ready":
                raise ExportError("Export is not available for download.", 410 if job["state"] == "expired" else 409)
            job["readers"] += 1
            path = job["directory"] / job["filename"]
        try:
            yield path
        finally:
            with self.changed:
                job["readers"] -= 1
                self._prune()

    def handle(self, request, response):
        try:
            tail = request.path.removeprefix("/__cad/exports").strip("/").split("/")
            if request.method == "POST":
                if int(request.header("content-length") or 0) > 65536:
                    raise ExportError("Export request too large.", 413)
                if tail == ["options"]:
                    status, body = self.options(json.loads(request.body()))
                    return response.send_json(status, body)
                if tail == [""]:
                    return response.send_json(202, self.create(json.loads(request.body())))
                if len(tail) == 2 and tail[1] in ("cancel", "release"):
                    return response.send_json(200, getattr(self, tail[1])(tail[0]))
            elif request.method == "GET":
                if len(tail) == 1:
                    after = request.query.get("after")
                    return response.send_json(200, self.get(tail[0], int(after) if after is not None else None,
                                                           float(request.query.get("wait") or 0)))
                if len(tail) == 2 and tail[1] == "file":
                    with self.artifact(tail[0]) as path:
                        from urllib.parse import quote
                        return response.stream_file(path, path.stat(), "application/octet-stream", [
                            ("content-disposition", "attachment; filename*=UTF-8''" + quote(path.name)),
                            ("x-content-type-options", "nosniff")])
            raise ExportError("Export route not found.", 404)
        except (ValueError, OSError) as error:
            response.send_json(getattr(error, "status", 400), {"error": str(error)})
