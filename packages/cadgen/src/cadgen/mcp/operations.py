"""Agent operations independent of a viewer, using the public Python verbs.

A session owns bounded, in-memory status. Repeating a request ID joins its work;
status/cancel go through the same tool. Destinations belong to the engine host.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import keyword
import math
import re
from pathlib import Path

from cadgen.jobs import TERMINAL, Jobs, JobError


def obj(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


STRING = {"type": "string", "minLength": 1}
BOOL = {"type": "boolean"}
JSON_VALUE = {"type": ["object", "array", "string"]}
FORMATS = ("xbf", "step", "glb", "stl", "threemf", "dxf")
COMMON = {
    "action": {"type": "string", "enum": ["start", "status", "cancel"], "default": "start"},
    "requestId": {**STRING, "description": "Unique 16–100 character request ID. Reuse only for an identical start in this session."},
    "operationId": STRING,
    "epoch": {**STRING, "description": "Session epoch returned by start; required for status/cancel."},
    "afterSequence": {"type": "integer", "minimum": 0},
    "waitMs": {"type": "integer", "minimum": 0, "maximum": 25000},
}
SNAPSHOT = {"source": STRING, "output": STRING, "packet": JSON_VALUE,
            "display": JSON_VALUE, "camera": JSON_VALUE, "grid": BOOL, "preview": BOOL,
            "pose": {"type": ["object", "string"]}, "width": {"type": "integer", "minimum": 1},
            "height": {"type": "integer", "minimum": 1}, "size_profile": STRING,
            "annotation": JSON_VALUE, "measurements": JSON_VALUE}
START = {
    "cad_build": obj({"code": {**STRING, "maxLength": 1000000}, "name": STRING, "cwd": STRING,
                      "outputs": obj({"model": {"type": "boolean", "enum": [True]}, **{k: STRING for k in FORMATS}}),
                      "verify": BOOL, "profile": BOOL}, ("code", "name", "cwd", "outputs")),
    "cad_export": obj({"source": STRING, "destinations": {"type": "array", "minItems": 1, "maxItems": 32, "items": STRING},
                       "pose": {"type": ["object", "string"]}, "part": STRING}, ("source", "destinations")),
    "cad_snapshot": obj(SNAPSHOT),
    "cad_inspect": obj({"source": STRING, "ref": STRING, "query": {"type": "string", "enum": ["closure", "geometry", "build"]}}, ("source",)),
}
DESCRIPTIONS = {
    "cad_build": "Build Python source assigning result, in the named working directory. Declare a live model and/or absolute file outputs. Does not select a script or execute its __main__; imported code and called decorated models may write their declared outputs.",
    "cad_export": "Export a document to explicit absolute destinations on the engine host (.xbf/.step/.glb/.stl/.3mf/.dxf). Reuses the Python export operation, including building a script model source first. Viewer downloads instead choose a destination in the user's browser and use temporary artifacts.",
    "cad_snapshot": "Render an explicit source/output PNG or a Quick Edit packet, using the Python snapshot operation. Paths must be absolute on the engine host. A script model source may build first. A failed snapshot or packet leaves existing pictures unchanged.",
    "cad_inspect": "Read closure, geometry (requires ref), or build metadata of an existing file or live model. Never builds source or writes declared model outputs; an unbuilt or expired model must be explicitly built first. Defaults to geometry with a ref, otherwise closure.",
}


def catalog():
    return [{"name": name, "title": name.removeprefix("cad_").capitalize() + " CAD",
             "description": description + " Start requires requestId. Returns an asynchronous operation: call this same tool with action=status, operationId and epoch (optionally afterSequence and waitMs) for progress/result, or action=cancel while queued. Disconnect does not cancel. Status of this MCP session's 64 most recent finished operations lasts up to 24 hours; restart loses it. Running file writes cannot be cancelled.",
             "inputSchema": {**obj({**COMMON, **START[name]["properties"]}), "anyOf": [
                 obj({"action": {"type": "string", "enum": ["start"]}, "requestId": COMMON["requestId"],
                      **START[name]["properties"]}, ("requestId", *START[name]["required"])),
                 obj({**{k: COMMON[k] for k in ("operationId", "epoch", "afterSequence", "waitMs")},
                      "action": {"type": "string", "enum": ["status", "cancel"]}},
                     ("action", "operationId", "epoch"))]},
             "outputSchema": {"type": "object", "required": ["state"], "properties": {
                 "state": {"type": "string", "enum": ["queued", "running", "succeeded", "failed", "cancelled", "rejected"]},
                 "id": STRING, "epoch": STRING, "requestId": STRING, "operation": STRING,
                 "sequence": {"type": "integer"}, "stage": STRING, "cancellable": BOOL,
                 "result": {"type": "object"}, "error": {"type": "string"}}},
             "annotations": {"readOnlyHint": name == "cad_inspect", "destructiveHint": name != "cad_inspect",
                             "idempotentHint": True, "openWorldHint": name != "cad_inspect"}}
            for name, description in DESCRIPTIONS.items()]


def checked(value, schema, label="arguments"):
    """Validate the small JSON schema vocabulary our operation contracts use."""
    kinds = schema.get("type", [])
    kinds = [kinds] if isinstance(kinds, str) else kinds
    types = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool}
    if kinds and not any(type(value) is types[k] for k in kinds):
        raise JobError(f"{label} must be {' or '.join(kinds)}")
    if "enum" in schema and value not in schema["enum"]:
        raise JobError(f"Invalid {label}: choose from {schema['enum']}")
    if isinstance(value, str) and not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 1000000):
        raise JobError(f"Invalid length for {label}")
    if type(value) is int and not schema.get("minimum", -math.inf) <= value <= schema.get("maximum", math.inf):
        raise JobError(f"Invalid range for {label}")
    if isinstance(value, dict) and "properties" in schema:
        missing = set(schema.get("required", [])) - value.keys()
        unknown = value.keys() - schema["properties"].keys()
        if missing or unknown:
            raise JobError(f"{label}: missing {sorted(missing)}; unknown {sorted(unknown)}")
        for key, item in value.items():
            checked(item, schema["properties"][key], f"{label}.{key}")
    if isinstance(value, list) and "items" in schema:
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 10000):
            raise JobError(f"Invalid length for {label}")
        for item in value:
            checked(item, schema["items"], label + "[]")


def absolute(value):
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise JobError("Paths must be absolute on the engine host")
    return str(Path(value).resolve())


def source(value):
    path = value.partition("#")[0].rsplit("::", 1)[0]
    if path != "code" or "::" not in value:
        absolute(path)
    from cadgen.engine.addresses import split
    split(value)  # validate model address without executing it


def packet_paths(value, override=None):
    """Check paths before the snapshot parser resolves them against process cwd."""
    if isinstance(value, str):
        value = json.loads(value if value.lstrip().startswith(("{", "[")) else
                           Path(absolute(value)).read_text(encoding="utf-8"))
    if isinstance(value, list):
        for item in value:
            packet_paths(item, override)
    elif isinstance(value, dict):
        if "input" in value and override is None:
            source(value["input"])
        if ("annotation" in value and isinstance(value["annotation"], str)
                and not value["annotation"].lstrip().startswith(("{", "["))):
            absolute(value["annotation"])
        for output in value.get("outputs", []):
            absolute(output if isinstance(output, str) else output["path"])
        if isinstance(value.get("measurements"), str) and not value["measurements"].lstrip().startswith(("{", "[")):
            absolute(value["measurements"])
        if "jobs" in value:
            packet_paths(value["jobs"], override)
    return value


def serial(value):
    if dataclasses.is_dataclass(value):
        value = dataclasses.asdict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        # A build may register a viewer URL containing the installation capability.
        return {k: serial(v) for k, v in value.items() if k not in ("url", "token")}
    if isinstance(value, (list, tuple)):
        return [serial(v) for v in value]
    return value


# What a session keeps of finished operations: the most recent, at most this many and this many
# bytes of reports, so a session never refuses new work for history it holds (each report is
# already cut to REPORT_BYTES), and the request IDs that named them, for ``request_conflict``.
HISTORY_JOBS = 64
HISTORY_BYTES = 32 * 1024 * 1024
HISTORY_REQUESTS = 2048
REPORT_BYTES = 2 * 1024 * 1024


class Operations(Jobs):
    def _prune(self):
        super()._prune()
        finished = [job for job in self.jobs.values() if job["state"] in TERMINAL]
        kept = sum(job.get("kept", 0) for job in finished)
        for job in sorted(finished, key=lambda job: job["finishedAt"]):
            if len(finished) < HISTORY_JOBS and kept <= HISTORY_BYTES:
                break
            self.jobs.pop(job["id"], None)
            finished.remove(job)
            kept -= job.get("kept", 0)
        # A request ID whose operation is gone still answers "expired" until it is the oldest.
        for request_id in [r for r, ident in self.requests.items() if ident not in self.jobs]:
            if len(self.requests) < HISTORY_REQUESTS:
                break
            del self.requests[request_id]

    def call(self, name, arguments):
        try:
            checked(arguments, obj({**COMMON, **START[name]["properties"]}))
            action = arguments.get("action", "start")
            if action != "start":
                checked(arguments, obj({k: COMMON[k] for k in ("action", "operationId", "epoch", "afterSequence", "waitMs")},
                                       ("action", "operationId", "epoch")))
                if arguments["epoch"] != self.epoch:
                    raise JobError("Operation session restarted", 409)
                ident = arguments["operationId"]
                with self.changed:
                    current = self.get(ident)
                    if current["operation"] != name:
                        raise JobError("Use the tool that started this operation", 409)
                    if action == "cancel":
                        return self.cancel(ident)
                    return self.get(ident, arguments.get("afterSequence"), arguments.get("waitMs", 0) / 1000)
            checked(arguments, obj({"action": COMMON["action"], "requestId": COMMON["requestId"], **START[name]["properties"]},
                                   ("requestId", *START[name]["required"])))
            request_id = arguments["requestId"]
            if not re.fullmatch(r"[\w-]{16,100}", request_id):
                raise JobError("requestId must be 16–100 letters, digits, underscores or hyphens")
            args = {k: v for k, v in arguments.items() if k not in ("action", "requestId")}
            normalized = hashlib.sha256(json.dumps([name, args], sort_keys=True, allow_nan=False).encode()).hexdigest()
            with self.changed:
                # Join retries before re-reading a packet or source that may have changed.
                if request_id in self.requests:
                    return self._submit(request_id, normalized)
                return self._submit(request_id, normalized, operation=name, arguments=args)
        except (ValueError, TypeError, OSError, KeyError) as error:
            return {"state": "rejected", "error": str(error), "status": getattr(error, "status", 400)}

    def public(self, job):
        result = super().public(job)
        if len(json.dumps(result).encode()) <= REPORT_BYTES:
            return result
        # MCP transports have bounded messages. Never silently omit a long report.
        summary = result.get("result") or {}
        return {**{k: v for k, v in result.items() if k not in ("result", "progress", "error")},
                "error": str(result.get("error", ""))[:4096],
                "result": {"truncated": True, "message": "Report exceeds 2 MiB; use the Python API or CLI for the full report.",
                           "address": summary.get("address")}}

    def _failure(self, job, error):
        return {"result": {"outputsMayHaveBeenWritten": job["operation"] in ("cad_build", "cad_export", "cad_snapshot")}}

    def _finished(self, job):
        job.pop("arguments", None)
        # Keep only what status can send: a long report is held as its summary, never in full.
        public = self.public(job)
        job.update({k: public[k] for k in ("result", "error") if k in public})
        if "progress" not in public:
            job.pop("progress", None)
        job["kept"] = len(json.dumps(public).encode())

    def _perform(self, job, progress):
        from cadgen.engine import api
        name, args = job["operation"], dict(job["arguments"])
        if "source" in args:
            source(args["source"])
        progress({"label": name.removeprefix("cad_").capitalize() + " in progress"})
        if name == "cad_build":
            cwd = absolute(args.pop("cwd"))
            model_name = args.pop("name")
            if not model_name.isascii() or not model_name.isidentifier() or keyword.iskeyword(model_name):
                raise JobError("name must be a Python identifier")
            outputs = dict(args.pop("outputs"))
            live = outputs.pop("model", False)
            if not live and not outputs:
                raise JobError("Declare a live model or at least one file output")
            outputs = {kind: absolute(path) for kind, path in outputs.items()}
            built = api.build(**args, **outputs, cwd=cwd, name=model_name, model=model_name if live else None, progress=progress)
            result = {"address": f"code::{model_name}", "report": serial(built.report), "files": serial(built.files),
                      **({"outputsMayHaveBeenWritten": True} if not built.ok else {})}
            return {"state": "succeeded" if built.ok else "failed", "stage": "Build complete" if built.ok else "Build failed",
                    "result": result, **({"error": built.report.get("error") or "Build failed"} if not built.ok else {})}
        if name == "cad_export":
            destinations = [absolute(path) for path in args.pop("destinations")]
            if len(set(destinations)) != len(destinations) or ("::" not in args["source"] and absolute(args["source"]) in destinations):
                raise JobError("Export destinations must be distinct from each other and the source")
            # Keep the one Python operation: all destinations are validated and share a document read.
            src = args.pop("source")
            result = {"files": serial(api.export(src, *destinations, **args, progress=progress))}
        elif name == "cad_snapshot":
            packet = args.pop("packet", None)
            output = args.pop("output", None)
            for field in ("annotation", "measurements"):
                if isinstance(args.get(field), str) and not args[field].lstrip().startswith(("{", "[")):
                    absolute(args[field])
            if packet is not None:
                if output is not None:
                    raise JobError("A packet declares its outputs; omit output")
                packet = packet_paths(packet, args.get("source"))
                result = {"pictures": serial(api.snapshot_job(packet, **args))}
            else:
                if output is None or "source" not in args:
                    raise JobError("Snapshot requires source and output, or a packet")
                result = {"pictures": [serial(api.snapshot(**args, out=absolute(output)))]}
        elif name == "cad_inspect":
            result = {"inspection": serial(api.inspect(**args))}
        return {"state": "succeeded", "stage": "Complete", "result": result}
