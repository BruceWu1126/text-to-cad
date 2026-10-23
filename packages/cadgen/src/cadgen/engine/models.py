"""The daemon's book of documents held by live model workers, each by its id (``ids.model_id``).

Only metadata lives here. Opening a document asks its worker for the structure
and shape bytes; registration never meshes or materializes an unviewed model.
The book is process memory and is never saved.
"""
from __future__ import annotations

import base64
import threading
import time
from typing import Any

from cadgen.engine.addresses import document_id, parse


class Models:
    def __init__(self, daemon: Any) -> None:
        self.daemon = daemon
        self.records: dict[str, dict[str, Any]] = {}
        self.lock = threading.RLock()

    def register(self, worker: Any, answer: dict) -> None:
        done = answer.get("done")
        reports = done.get("reports", []) if isinstance(done, dict) else done
        if not isinstance(reports, list):
            return
        for root in reports:
            for report in [root, *(root.get("child_models") or {}).values()]:
                ident, address, source = report.get("id"), report.get("address"), report.get("source")
                if not report.get("ok") or not ident or not source:
                    continue
                with self.lock:
                    old = self.records.get(ident)
                    version = (old["version"] if old else 0) + 1
                    self.records[ident] = {"id": ident, "address": address, "source": source, "worker": worker,
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
        if record is None or not self.daemon.pool.holds(record["worker"]):
            if request.get("build") is False:
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

    def load(self, ident: str) -> tuple[dict, dict[str, bytes]]:
        with self.lock:
            record = self.records[ident]
            source, worker, version, address = record["source"], record["worker"], record["version"], record["address"]
        if not self.daemon.pool.holds(worker):
            raise ValueError(f"model {address} retired; open its address again to rebuild")
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

        return {**done["structure"], "exportRevision": fingerprint(done["structure"]), "path": address, "address": address,
                "id": ident, "closure": done.get("closure"), "version": f"{version}-0"}, shapes

    def prune(self) -> None:
        with self.lock:
            gone = [ident for ident, row in self.records.items() if not self.daemon.pool.holds(row["worker"])]
            for ident in gone:
                self.records.pop(ident)
                if self.daemon.server is not None:
                    self.daemon.server.forget_model(ident)

    def listing(self) -> list[dict[str, Any]]:
        self.prune()
        with self.lock:
            return [{"id": ident, "address": row["address"], "script": row["source"]["script"],
                     "name": row["source"]["name"], "builtAt": row["builtAt"], "version": row["version"],
                     "state": self.state(ident)}
                    for ident, row in self.records.items() if not row["files"]]

    def state(self, ident: str) -> str:
        server = self.daemon.server
        build = server.build_of(ident) if server is not None else {}
        return "building" if build.get("building") else "failed" if build.get("failed") else "idle"
