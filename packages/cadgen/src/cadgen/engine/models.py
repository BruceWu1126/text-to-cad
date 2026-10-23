"""The daemon's book of the model documents it has built, each by its id (``ids.model_id``).

Only metadata lives here: a document's structure and shape bytes are the server's
(``serve.Server``). Opening a document asks its worker for them; registration never meshes or
materializes an unviewed model. A document outlives its worker: before the pool retires a worker,
the daemon takes from it every ``pointer`` document of it the server does not hold yet
(``drain``), and the record stays with no worker (``held``) until memory evicts its bytes
(``evicted``). A ``memory`` document goes with its worker (``expired``). A document held nowhere
is never built here: whoever asks for it is told the command that builds it again (``missing``),
and the engine runs nothing. The book is process memory and is never saved.
"""
from __future__ import annotations

import base64
import shlex
import threading
import time
from typing import Any

from cadgen.engine.addresses import document_id, parse

# A document's states, as ``cadgen status`` lists them (``state``).
STATES = ("live", "held", "building", "failed", "evicted", "expired")


class Models:
    def __init__(self, daemon: Any) -> None:
        self.daemon = daemon
        self.records: dict[str, dict[str, Any]] = {}
        # Each ``memory`` document whose worker went: what its id answers from then on (``expired``).
        self.expired: dict[str, dict[str, Any]] = {}
        self.lock = threading.RLock()

    def register(self, worker: Any, answer: dict) -> None:
        done = answer.get("done")
        reports = done.get("reports", []) if isinstance(done, dict) else done
        if not isinstance(reports, list):
            return
        program = answer.get("program")
        for root in reports:
            for report in [root, *(root.get("child_models") or {}).values()]:
                ident, address, source = report.get("id"), report.get("address"), report.get("source")
                if not report.get("ok") or not ident or not source:
                    continue
                with self.lock:
                    old = self.records.get(ident) or self.expired.pop(ident, None)
                    version = (old["version"] if old else 0) + 1
                    self.records[ident] = {"id": ident, "address": address, "source": source, "worker": worker,
                                           "program": program or (old or {}).get("program") or source["script"],
                                           "store": report.get("store") or "pointer",
                                           "builtAt": time.time(), "version": version,
                                           "files": list(report.get("files") or {})}
                self.daemon.builders[ident] = worker
                server = self.register_page(ident)
                app = getattr(server.http, "app", None)
                token = app.token if app is not None else ""
                report["url"] = f"{server.url}/?doc={ident}" + (f"&token={token}" if token else "")

    def register_page(self, ident: str) -> Any:
        server = self.daemon.serve({})
        with self.lock:
            record = self.records[ident]
            record["opened"] = True
            version, address = record["version"], record["address"]
        server.register_model(ident, address, lambda: self.load(ident), version)
        return server

    def paths(self, worker: Any, model: str | None = None) -> list[str]:
        """The ids of the documents ``worker`` holds (of model ``model``'s name, when given)."""
        name = model.rsplit("::", 1)[-1] if model else None
        with self.lock:
            return [ident for ident, row in self.records.items() if row["worker"] is worker
                    and (name is None or row["source"]["name"] == name)]

    def held(self, ident: str) -> dict[str, Any] | None:
        """The record of the model document ``ident`` while the engine holds it -- a live worker
        does, or the server holds its bytes -- else None."""
        with self.lock:
            record = self.records.get(ident)
        if record is None:
            return None
        worker, server = record["worker"], self.daemon.server
        if self._working(worker):
            return record
        if record["store"] == "memory":
            return None  # out of the pool, its worker is going: the document goes with it
        return record if server is not None and server.has(ident, record["version"]) else None

    def _working(self, worker: Any) -> bool:
        """Whether ``worker`` holds its documents still: live in the pool, or out of it and alive --
        being retired, and drained (``drain``) before it goes."""
        return worker is not None and (self.daemon.pool.holds(worker) or worker.alive())

    def rebuild(self, ident: str) -> dict[str, Any] | None:
        """What the engine knows of a model document it does not hold: its state (``evicted``,
        ``expired``), the command that builds it again -- the program that built it, run with
        ``python`` -- and why, in words (the page's ``error``). None for a document it holds or
        never built. The engine never runs the command."""
        with self.lock:
            record = self.records.get(ident)
            gone = self.expired.get(ident)
        if record is not None and self.held(ident) is not None:
            return None
        if record is not None and record["store"] == "memory":
            gone = record  # its worker is going, or gone: expired, whether or not pruned yet
        found = record or gone
        if found is None:
            return None
        command = self.command(ident)
        name = found["source"]["name"]
        if gone is not None:
            text = (f"{name} ({ident}) expired with its worker: it is kept in memory only; "
                    f"run `{command}` to build it again")
            return {"error": text, "state": "expired", "rebuild": command}
        return {"error": f"{name} ({ident}) is no longer held: run `{command}` to build it again",
                "state": "evicted", "rebuild": command}

    def command(self, ident: str) -> str | None:
        """The command that builds model document ``ident`` again: ``python`` and the program that
        built it (else its script). None for an id the engine never built."""
        with self.lock:
            found = self.records.get(ident) or self.expired.get(ident)
        if found is None:
            return None
        return f"python {shlex.quote(str(found.get('program') or found['source']['script']))}"

    def missing(self, ident: str) -> str:
        """Why no document ``ident`` is here, with the command that builds it again where the
        engine built it before: the engine never runs it."""
        found = self.rebuild(ident)
        return found["error"] if found is not None else f"no document {ident} is held"

    def not_held(self, ident: str) -> Exception:
        """The refusal for a model document the engine does not hold (``serve.NotHeld``)."""
        from cadgen.engine.serve import NotHeld

        found = self.rebuild(ident)
        if found is None:
            return NotHeld(self.missing(ident), "evicted", None)
        return NotHeld(found["error"], found["state"], found["rebuild"])

    def drain(self, worker: Any) -> None:
        """Before ``worker`` is retired: every ``pointer`` document of it the server does not hold
        whole at its version is taken from it -- its ``result``, never meshed, with the whole
        document as xbf bytes (``document.to_bytes``) -- so it outlives the worker and answers every
        verb as an on-disk xbf does. A worker that is gone (a crash) drains nothing: its documents
        the server lacks are evicted."""
        server = self.daemon.server
        if server is None or not worker.alive():
            return
        from cadgen.engine.daemon import WorkerGone

        with self.lock:
            owed = [(ident, row["version"], row["address"]) for ident, row in self.records.items()
                    if row["worker"] is worker and row["store"] == "pointer"]
        for ident, version, address in owed:
            if server.has(ident, version, whole=True):
                continue
            try:
                structure, shapes, whole = self.load(ident, worker, whole=True)
            except WorkerGone:
                return
            except (OSError, ValueError, KeyError):
                continue
            with self.lock:
                record = self.records.get(ident)
                current = record is not None and record["version"] == version
            if current:  # a build of it since, by another worker, is newer
                server.keep(ident, address, structure, shapes, version, whole)

    def resolve(self, address: str) -> dict[str, Any]:
        found = parse(address)
        if found is None:
            raise ValueError("expected script.py::NAME")
        with self.lock:
            record = self.records.get(document_id(address))
            if record is not None and self.daemon.pool.holds(record["worker"]):
                source = record["source"]
                return {"script": source["script"], "function": source["name"]}
        return {"script": str(found.script), "function": found.name}

    def serve(self, address: str, request: dict) -> tuple[Any, str]:
        """The server and the id of the model ``address`` names, built first when no live
        worker holds it (unless ``request`` says ``build: false``)."""
        address, ident = str(parse(address)), document_id(address)
        with self.lock:
            record = self.records.get(ident)
            known = record is not None or ident in self.expired
        if self.held(ident) is None:
            if request.get("build") is False:
                if known:
                    raise self.not_held(ident)
                raise ValueError(f"no live model {address}; build it before inspecting")
            source = self.resolve(address)
            replies = []
            self.daemon.handle({"op": "build", "script": source["script"],
                                "functions": [source["function"]], "env": request.get("env", {}),
                                "cwd": request.get("cwd")}, replies.append)
            failure = next((row["failed"] for row in replies if "failed" in row), None)
            if failure:
                raise ValueError(failure)
            with self.lock:
                if ident not in self.records:
                    errors = [r.get("error") for row in replies for r in row.get("done", []) if not r.get("ok")]
                    raise ValueError(next((e for e in errors if e), f"model {address} did not build"))
        return self.register_page(ident), ident

    def load(self, ident: str, leaving: Any = None, whole: bool = False) -> tuple:
        """A model document's structure and shape bytes, asked of the worker that holds it -- through
        the pool, or of ``leaving``, a worker being retired (``drain``), directly, and then
        (``whole``) with the whole document's xbf bytes third. ``serve.NotHeld`` when no worker holds
        it: nothing is built to bring it back."""
        with self.lock:
            record = self.records.get(ident)
            if record is None:
                raise self.not_held(ident)
            source, version, address = record["source"], record["version"], record["address"]
            worker = leaving or record["worker"]
        if worker is None or not worker.alive():
            raise self.not_held(ident)
        order = {"op": "result", "script": source["script"], "name": source["name"]}
        if leaving is None and not whole and self.daemon.pool.holds(worker):
            replies = []
            self.daemon.handle({**order, "env": worker.environment}, replies.append)
            answer = next((row for row in reversed(replies) if "done" in row or "failed" in row), {})
            payload = base64.b64decode(answer.get("payload", ""))
        else:
            # Out of the pool and still alive: being retired, and asked before it goes.
            answer = worker.build({"id": 1, **order, "whole": whole}, lambda _message: None)
            payload = answer.pop("payload", b"")
        if "failed" in answer:
            raise ValueError(answer["failed"])
        done = answer["done"]
        shapes, at = {}, 0
        for key, size in done["shapes"]:
            shapes[key] = payload[at:at + size]
            at += size
        from cadgen.engine.export.viewer import fingerprint

        structure = {**done["structure"], "exportRevision": fingerprint(done["structure"]), "path": address,
                     "address": address, "id": ident, "closure": done.get("closure"), "version": f"{version}-0"}
        if whole:
            return structure, shapes, payload[at:at + int(done.get("xbf") or 0)]
        return structure, shapes

    def prune(self) -> None:
        """Each record whose worker is gone keeps its document, with no worker (``drain`` took it
        first); a ``memory`` document is forgotten with its worker, and its id answers ``expired``."""
        with self.lock:
            for ident, row in list(self.records.items()):
                worker = row["worker"]
                if worker is None or worker.alive() or self.daemon.pool.holds(worker):
                    continue  # a worker out of the pool but alive is being retired: drained first
                if self.daemon.builders.get(ident) is worker:
                    del self.daemon.builders[ident]
                if row["store"] == "memory":
                    self.records.pop(ident)
                    self.expired[ident] = {key: row[key] for key in ("id", "address", "source", "program", "store",
                                                                     "version", "builtAt")}
                    if self.daemon.server is not None:
                        self.daemon.server.forget_model(ident)
                else:
                    row["worker"] = None

    def listing(self, every: bool = False) -> list[dict[str, Any]]:
        """The model documents (``every``: those with declared files too, and the expired), each with
        its ``state`` (``STATES``) and, for one not held, the command that builds it again."""
        self.prune()
        with self.lock:
            rows = [row for row in self.records.values() if every or not row["files"]]
            rows += list(self.expired.values()) if every else []
            found = []
            for row in rows:
                ident = row["id"]
                entry = {"id": ident, "address": row["address"], "script": row["source"]["script"],
                         "name": row["source"]["name"], "builtAt": row["builtAt"], "version": row["version"],
                         "store": row["store"], "state": self.state(ident)}
                lost = self.rebuild(ident) if entry["state"] in ("evicted", "expired") else None
                if lost is not None:
                    entry["rebuild"] = lost["rebuild"]
                found.append(entry)
            return found

    def state(self, ident: str) -> str:
        """``live`` (a live worker holds it), ``held`` (the server holds its bytes; no worker),
        ``building``, ``failed`` (its last build), ``evicted`` (neither: memory evicted its bytes,
        or its worker crashed), or ``expired`` (a ``memory`` document whose worker went)."""
        with self.lock:
            if ident in self.expired:
                return "expired"
            record = self.records.get(ident)
        server = self.daemon.server
        build = server.build_of(ident) if server is not None else {}
        if build.get("building"):
            return "building"
        if build.get("failed"):
            return "failed"
        if record is None:
            return "evicted"
        if self._working(record["worker"]):
            return "live"
        if record["store"] == "memory":
            return "expired"
        return "held" if server is not None and server.has(ident, record["version"]) else "evicted"
