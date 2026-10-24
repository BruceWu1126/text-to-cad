"""The daemon's book of the model documents it has built, each by its id (``ids.model_id``).

Only metadata lives here: a document's structure and shape bytes are the server's
(``serve.Server``). Opening a document asks its worker for them; registration never meshes or
materializes an unviewed model. A document outlives its worker: before the pool retires a worker,
the daemon takes from it every ``pointer`` and ``copy`` document of it the server does not hold yet
(``drain``), and the record stays with no worker (``held``) until memory evicts its bytes
(``evicted``). A ``memory`` document goes with its worker (``expired``). A document held nowhere
is never built here: whoever asks for it is told the command that builds it again (``missing``),
and the engine runs nothing. The book is process memory.

What outlives the process is the registry's (``registry``): each ``pointer`` and ``copy`` document
built is recorded there as it is registered (``record``), synchronously, so after a restart its id
is known -- ``evicted``, with the command that builds it again, or ``detached`` once its script is
gone -- and nothing is run to bring it back. A ``memory`` document has no record. A served
document's generation (``version``, what a page shows and the viewer's export compares) is a token
of its build's own, never a counter that starts again, so an id and a generation never name two
documents across a restart; its record's ``revision`` is its build closure's key (``revision``), the
same for the same code, so a build that changed nothing rewrites no record.
"""
from __future__ import annotations

import base64
import os
import platform
import secrets
import shlex
import threading
import time
from pathlib import Path
from typing import Any

# A document's states, as ``cadgen status`` lists them (``state``).
STATES = ("live", "held", "building", "failed", "evicted", "expired", "detached")


def revision(report: dict[str, Any]) -> str:
    """A model document's recorded revision: its closure's key (the same code, the same revision, in
    any process), else a token of this build's own."""
    key = (report.get("closure") or {}).get("key")
    return str(key) if key else f"t{secrets.token_hex(8)}"


def runtime() -> dict[str, str]:
    """What a recorded document was built on, by version only."""
    from cadgen.engine import previous

    return {"python": platform.python_version(), "cadgen": previous.version(), **previous._libraries()}


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
                ident, source = report.get("id"), report.get("source")
                if not report.get("ok") or not ident or not source:
                    continue
                version = secrets.token_hex(8)  # this build's generation: a rebuild is a new one
                try:
                    copied = self.record(report, program, answer.get("copy"), revision(report))
                except Exception as error:  # noqa: BLE001 - a copy's inputs that moved: the build fails, loudly
                    report["ok"], report["error"] = False, f"{source['name']}: {error}"
                    root["ok"] = False
                    root.setdefault("error", report["error"])
                    continue
                with self.lock:
                    old = self.records.get(ident) or self.expired.pop(ident, None)
                    self.records[ident] = {"id": ident, "source": source, "worker": worker,
                                           "program": program or (old or {}).get("program") or source["script"],
                                           "store": "copy" if copied else report.get("store") or "pointer",
                                           "builtAt": time.time(), "version": version,
                                           "files": list(report.get("files") or {})}
                self.daemon.builders[ident] = worker
                server = self.register_page(ident)
                report["url"] = self.url(server, ident)

    # --- the registry ------------------------------------------------------------------------

    @staticmethod
    def registry() -> Any:
        from cadgen.engine import registry

        return registry.current()

    def bundle(self, copying: dict[str, Any]) -> str:
        """A copied model's bundle written in its registry folder (``copies``): its ``model.py``."""
        from cadgen.engine.ids import is_id

        held = self.registry()
        if held is None:
            raise ValueError("this engine has no state folder to keep a copy in: save this as a file")
        ident = str(copying.get("id"))
        if not is_id(ident):
            raise ValueError(f"{ident!r} is not a document id")
        files = {name: base64.b64decode(data) for name, data in (copying.get("files") or {}).items()}
        return str(held.write_bundle(ident, files) / "model.py")

    def serving(self, script: str) -> dict[str, Any]:
        """What the worker building ``script`` reads in place of what it names: for a copied
        document's own ``model.py``, each file its first build read, from its copy (``copies.serving``),
        a relative path against the folder that build ran in."""
        held = self.registry()
        ident = held.bundle_of(script) if held is not None and script else None
        record = held.get(ident) if ident else None
        if record is None or not record.get("copied"):
            return {}
        folder = held.folder(ident)
        return {"copied": {original: os.path.normpath(folder / inside) for original, inside in record["copied"].items()},
                "origin": (record.get("origin") or {}).get("cwd")}

    def record(self, report: dict[str, Any], program: str | None, copying: dict[str, Any] | None,
               recorded: str) -> bool:
        """The registry's record of a document a build made (``registry``), written when it changed; a
        copy's inputs copied into its bundle first (``copies.copy_inputs``). Nothing for a ``memory``
        document, or with no registry. Whether the document is a copy."""
        from cadgen.engine import registry as registering

        held = self.registry()
        if held is None or report.get("store") == "memory":
            return False
        ident, source = report["id"], report["source"]
        script = Path(source["script"])
        bundle = held.bundle_of(script)
        if bundle is not None and bundle != ident:
            return False  # a model of a copy's model.py that is not the copy: part of the copy's build
        closure = report.get("closure") or {}
        old = held.get(ident) or {}
        copied = bundle is not None
        if copied:
            from cadgen.engine.copies import copy_inputs

            inputs = copy_inputs(held, ident, closure, dict(old.get("copied") or {}))
            held.prune_inputs(ident, {Path(inside).parts[1] for inside in inputs.values()})
            root, home, run = ".", "model.py", ["python", "model.py"]
        else:
            main = Path(program or script)
            root = os.path.commonpath([str(script.parent), str(main.parent)]) if script.anchor == main.anchor else str(script.parent)
            home, run = registering.relative(script, root), ["python", registering.relative(main, root)]
        base = held.folder(ident) if copied else Path(root)
        rows = [[row[0], *([registering.relative(row[1], base)] if row[0] in ("file", "read") else
                           [row[1], registering.relative(row[2], base) if row[2] else None, row[3]]), row[-1]]
                for row in closure.get("inputs") or []]
        found: dict[str, Any] = {
            "id": ident, "kind": "model", "store": "copy" if copied else "pointer", "root": root, "home": home,
            "entry": source["name"], "label": source["name"], "run": run,
            "revision": recorded,
            "built": old["built"] if old.get("revision") == recorded and old.get("built") else time.time(),
            "closure": {"key": closure.get("key"),
                        "modules": [[registering.relative(path, base), digest] for path, digest in closure.get("modules") or []],
                        "inputs": rows},
            "runtime": runtime()}
        if copied:
            found["copied"] = inputs
            found["origin"] = old.get("origin") or ({"file": copying.get("source"), "cwd": copying.get("origin")}
                                                    if copying else None)
        for key in ("opened", "pinned", "preview"):  # the home's, kept across builds
            if key in old:
                found[key] = old[key]
        held.put(found)
        return copied

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
            return self.recorded(ident)
        command = self.command(ident)
        name = found["source"]["name"]
        if gone is None and not Path(found["source"]["script"]).is_file():
            return {"error": f"{name} ({ident}) is detached: {found['source']['script']} is gone, and nothing "
                             "builds it again; remove it from the home", "state": "detached", "rebuild": None}
        if gone is not None:
            text = (f"{name} ({ident}) expired with its worker: it is kept in memory only; "
                    f"run `{command}` to build it again")
            return {"error": text, "state": "expired", "rebuild": command}
        return {"error": f"{name} ({ident}) is no longer held: run `{command}` to build it again",
                "state": "evicted", "rebuild": command}

    def command(self, ident: str) -> str | None:
        """The command that builds model document ``ident`` again: ``python`` and the program that
        built it (else its script), from this process's book or the registry's record. None for an id
        the engine never built."""
        with self.lock:
            found = self.records.get(ident) or self.expired.get(ident)
        if found is None:
            known = self.recorded(ident)
            return known["rebuild"] if known is not None else None
        return f"python {shlex.quote(str(found.get('program') or found['source']['script']))}"

    def recorded(self, ident: str) -> dict[str, Any] | None:
        """What the registry knows of a model document this process never built (one built before a
        restart): ``evicted``, with the command that builds it again, or ``detached`` when its script
        is gone. None for an id it has no model's record of. Nothing is run."""
        from cadgen.engine import registry as registering

        held = self.registry()
        found = held.get(ident) if held is not None else None
        if found is None or found.get("kind") != "model":
            return None
        name = found.get("label") or found.get("entry") or ident
        if registering.detached(held, found):
            return {"error": f"{name} ({ident}) is detached: {registering.home(held, found)} is gone, and nothing "
                             "builds it again; remove it from the home", "state": "detached", "rebuild": None}
        command = registering.command(held, found)
        return {"error": f"{name} ({ident}) is no longer held: run `{command}` to build it again",
                "state": "evicted", "rebuild": command}

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
        """Before ``worker`` is retired: every ``pointer`` and ``copy`` document of it the server does not hold
        whole at its version is taken from it -- its ``result``, never meshed, with the whole
        document as xbf bytes (``document.to_bytes``, its animations' record with it) -- so it outlives
        the worker and answers every verb as an on-disk xbf does, and a GLB of it as the build's GLB. A
        worker that is gone (a crash) drains nothing: its documents the server lacks are evicted."""
        server = self.daemon.server
        if server is None or not worker.alive():
            return
        from cadgen.engine.daemon import WorkerGone

        with self.lock:
            owed = [(ident, row["version"]) for ident, row in self.records.items()
                    if row["worker"] is worker and row["store"] != "memory"]
        for ident, version in owed:
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
                server.keep(ident, structure, shapes, version, whole)

    def serve(self, ident: str) -> tuple[Any, str]:
        """The server and the id of the model document ``ident`` the engine holds; refused
        (``not_held``), naming its state and the command that builds it again, when it does not.
        Nothing is built."""
        if self.held(ident) is None:
            raise self.not_held(ident)
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
            source, version = record["source"], record["version"]
            worker = leaving or record["worker"]
        if worker is None or not worker.alive():
            raise self.not_held(ident)
        order = {"op": "result", "script": source["script"], "name": source["name"]}
        if leaving is None and not whole and self.daemon.pool.holds(worker):
            replies = []
            # Asked of the worker that built it (``via``): a child model's is its parent's script's.
            self.daemon.handle({**order, "env": worker.environment, "via": worker.script}, replies.append)
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

        structure = {**done["structure"], "exportRevision": fingerprint(done["structure"]), "path": ident,
                     "id": ident, "closure": done.get("closure"), "version": version}
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
                    self.expired[ident] = {key: row[key] for key in ("id", "source", "program", "store",
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
                entry = {"id": ident, "script": row["source"]["script"],
                         "name": row["source"]["name"], "builtAt": row["builtAt"], "version": row["version"],
                         "store": row["store"], "state": self.state(ident)}
                lost = self.rebuild(ident) if entry["state"] in ("evicted", "expired", "detached") else None
                if lost is not None and lost["rebuild"]:
                    entry["rebuild"] = lost["rebuild"]
                found.append(entry)
            return found

    def state(self, ident: str) -> str:
        """``live`` (a live worker holds it), ``held`` (the server holds its bytes; no worker),
        ``building``, ``failed`` (its last build), ``evicted`` (neither: memory evicted its bytes,
        its worker crashed, or the engine restarted since: the registry knows it), ``expired`` (a
        ``memory`` document whose worker went), or ``detached`` (not held, and its script is gone)."""
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
            known = self.recorded(ident)
            return known["state"] if known is not None else "evicted"
        if self._working(record["worker"]):
            return "live"
        if record["store"] == "memory":
            return "expired"
        if server is not None and server.has(ident, record["version"]):
            return "held"
        return "evicted" if Path(record["source"]["script"]).is_file() else "detached"
