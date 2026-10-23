"""The daemon's address book for documents held by live model workers.

Only metadata lives here. Opening a document asks its worker for the structure
and shape bytes; registration never meshes or materializes an unviewed model.
The address book is process memory and is never saved.
"""
from __future__ import annotations

import base64
import os
import threading
import time
from typing import Any

from cadgen.engine.addresses import parse


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
                address, source = report.get("address"), report.get("source")
                if not report.get("ok") or not address or not source:
                    continue
                with self.lock:
                    old = self.records.get(address)
                    version = (old["version"] if old else 0) + 1
                    self.records[address] = {"address": address, "source": source, "worker": worker,
                                             "builtAt": time.time(), "version": version,
                                             "files": list(report.get("files") or {})}
                self.daemon.builders[os.path.normcase(address)] = worker
                server, ident = self.register_page(address)
                app = getattr(server.http, "app", None)
                token = app.token if app is not None else ""
                report["url"] = f"{server.url}/?doc={ident}" + (f"&token={token}" if token else "")

    def register_page(self, address: str) -> tuple[Any, str]:
        server = self.daemon.serve({})
        with self.lock:
            record = self.records[address]
            record["opened"] = True
            version = record["version"]
        ident = server.register_model(address, lambda: self.load(address), version)
        return server, ident

    def paths(self, worker: Any, model: str | None = None) -> list[str]:
        name = model.rsplit("::", 1)[-1] if model else None
        with self.lock:
            return [address for address, row in self.records.items() if row["worker"] is worker
                    and (name is None or row["source"]["name"] == name)]

    def resolve(self, address: str) -> dict[str, Any]:
        found = parse(address)
        if found is None:
            raise ValueError("expected script.py::NAME or code::NAME")
        address = str(found)
        with self.lock:
            record = self.records.get(address)
            if record is not None and self.daemon.pool.holds(record["worker"]):
                source = record["source"]
                return ({"script": source["script"], "code": source["name"]}
                        if source["kind"] == "code" else {"script": source["script"], "function": source["name"]})
        if found.script is not None:
            return {"script": str(found.script), "function": found.name}
        raise ValueError(f"no live model {address}; build it with --model {found.name}")

    def serve(self, address: str, request: dict) -> tuple[Any, str]:
        address = str(parse(address))
        with self.lock:
            record = self.records.get(address)
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
                if address not in self.records:
                    errors = [r.get("error") for row in replies for r in row.get("done", []) if not r.get("ok")]
                    raise ValueError(next((e for e in errors if e), f"model {address} did not build"))
        return self.register_page(address)

    def load(self, address: str) -> tuple[dict, dict[str, bytes]]:
        with self.lock:
            record = self.records[address]
            source, worker, version = record["source"], record["worker"], record["version"]
        if not self.daemon.pool.holds(worker):
            raise ValueError(f"model {address} retired; open its address again to rebuild")
        replies = []
        self.daemon.handle({"op": "result", "script": source["script"], "name": source["name"],
                            "source_kind": source["kind"], "env": worker.environment}, replies.append)
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

        return {**done["structure"], "exportRevision": fingerprint(done["structure"]), "path": address, "address": address, "closure": done.get("closure"), "version": f"{version}-0"}, shapes

    def prune(self) -> None:
        with self.lock:
            gone = [address for address, row in self.records.items() if not self.daemon.pool.holds(row["worker"])]
            for address in gone:
                self.records.pop(address)
                if self.daemon.server is not None:
                    self.daemon.server.forget_model(address)

    def listing(self) -> list[dict[str, Any]]:
        self.prune()
        with self.lock:
            return [{"address": address, "script": row["source"]["script"] if row["source"]["kind"] == "script" else "code",
                     "name": row["source"]["name"], "builtAt": row["builtAt"], "version": row["version"],
                     "state": self.state(address, row)}
                    for address, row in self.records.items() if not row["files"]]

    def state(self, address: str, row: dict) -> str:
        server = self.daemon.server
        build = server.build_of(address) if server is not None else {}
        return "building" if build.get("building") else "failed" if build.get("failed") else "idle"
