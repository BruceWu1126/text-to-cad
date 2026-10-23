"""The daemon's book of documents held by live model workers, each by its id (``ids.model_id``).

Only metadata lives here. Opening a document asks its worker for the structure
and shape bytes; registration never meshes or materializes an unviewed model.
The book is process memory and is never saved. A document no live worker holds is never built
here: whoever asks for it is told the command that builds it again (``missing``), and the engine
runs nothing.
"""
from __future__ import annotations

import base64
import shlex
import threading
import time
from typing import Any


class Models:
    def __init__(self, daemon: Any) -> None:
        self.daemon = daemon
        self.records: dict[str, dict[str, Any]] = {}
        # Per document no live worker holds any more, the program that built it: what ``missing`` names.
        self.gone: dict[str, dict[str, Any]] = {}
        self.lock = threading.RLock()

    def register(self, worker: Any, answer: dict) -> None:
        done = answer.get("done")
        reports = done.get("reports", []) if isinstance(done, dict) else done
        if not isinstance(reports, list):
            return
        program = answer.get("program")
        for root in reports:
            for report in [root, *(root.get("child_models") or {}).values()]:
                ident, source = report.get("id"), report.get("source")
                if not report.get("ok") or not ident or not source:
                    continue
                with self.lock:
                    old = self.records.get(ident)
                    version = (old["version"] if old else 0) + 1
                    self.records[ident] = {"id": ident, "source": source, "worker": worker,
                                           "program": program or (old or {}).get("program") or source["script"],
                                           "builtAt": time.time(), "version": version,
                                           "files": list(report.get("files") or {})}
                    self.gone.pop(ident, None)
                self.daemon.builders[ident] = worker
                server = self.register_page(ident)
                report["url"] = self.url(server, ident)

    @staticmethod
    def url(server: Any, ident: str) -> str:
        """The page of document ``ident`` on ``server``: ``/<id>``, with the token that lets it in."""
        app = getattr(server.http, "app", None)
        token = app.token if app is not None else ""
        return f"{server.url}/{ident}" + (f"?token={token}" if token else "")

    def register_page(self, ident: str) -> Any:
        server = self.daemon.serve({})
        with self.lock:
            record = self.records[ident]
            record["opened"] = True
            version, name = record["version"], record["source"]["name"]
        server.register_model(ident, name, lambda: self.load(ident), version)
        return server

    def paths(self, worker: Any, model: str | None = None) -> list[str]:
        """The ids of the documents ``worker`` holds (of model ``model``'s name, when given)."""
        name = model.rsplit("::", 1)[-1] if model else None
        with self.lock:
            return [ident for ident, row in self.records.items() if row["worker"] is worker
                    and (name is None or row["source"]["name"] == name)]

    def held(self, ident: str) -> dict[str, Any] | None:
        """The record of the model document ``ident`` while a live worker holds it, else None."""
        with self.lock:
            record = self.records.get(ident)
        return record if record is not None and self.daemon.pool.holds(record["worker"]) else None

    def missing(self, ident: str) -> str:
        """Why no document ``ident`` is here, and the command that builds it again where the
        engine knows the program that built it: the engine never runs it."""
        with self.lock:
            record = self.records.get(ident) or self.gone.get(ident)
        if record is None:
            return f"no document {ident} is held"
        program = record.get("program") or record["source"]["script"]
        return (f"no document {ident} is held: {record['source']['name']} is no longer in memory; "
                f"run `python {shlex.quote(str(program))}` to build it again")

    def serve(self, ident: str) -> tuple[Any, str]:
        """The server and the id of the model document ``ident`` a live worker holds; refused,
        naming the command that builds it again, when none does. Nothing is built."""
        if self.held(ident) is None:
            raise ValueError(self.missing(ident))
        return self.register_page(ident), ident

    def load(self, ident: str) -> tuple[dict, dict[str, bytes]]:
        with self.lock:
            record = self.records[ident]
            source, worker, version = record["source"], record["worker"], record["version"]
        if not self.daemon.pool.holds(worker):
            raise ValueError(self.missing(ident))
        replies = []
        self.daemon.handle({"op": "result", "script": source["script"], "name": source["name"],
                            "env": worker.environment}, replies.append)
        answer = next((row for row in reversed(replies) if "done" in row or "failed" in row), {})
        if "failed" in answer:
            raise ValueError(answer["failed"])
        done = answer["done"]
        payload = base64.b64decode(answer.get("payload", ""))
        shapes, at = {}, 0
        for key, size in done["shapes"]:
            shapes[key] = payload[at:at + size]
            at += size
        from cadgen.engine.export.viewer import fingerprint

        return {**done["structure"], "exportRevision": fingerprint(done["structure"]), "path": ident,
                "id": ident, "closure": done.get("closure"), "version": f"{version}-0"}, shapes

    def prune(self) -> None:
        with self.lock:
            gone = [ident for ident, row in self.records.items() if not self.daemon.pool.holds(row["worker"])]
            for ident in gone:
                row = self.records.pop(ident)
                self.gone[ident] = {"source": row["source"], "program": row["program"]}
                if self.daemon.server is not None:
                    self.daemon.server.forget_model(ident)

    def listing(self) -> list[dict[str, Any]]:
        self.prune()
        with self.lock:
            return [{"id": ident, "script": row["source"]["script"], "name": row["source"]["name"],
                     "builtAt": row["builtAt"], "version": row["version"], "state": self.state(ident)}
                    for ident, row in self.records.items() if not row["files"]]

    def state(self, ident: str) -> str:
        server = self.daemon.server
        build = server.build_of(ident) if server is not None else {}
        return "building" if build.get("building") else "failed" if build.get("failed") else "idle"
